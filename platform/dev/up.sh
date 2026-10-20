#!/usr/bin/env bash
# Creates the local dev platform in xFoundry's own VM: kind + Kube-OVN + Multus + KubeVirt + CDI + the snapshot
# collector's read-only identity. Docker Desktop, Nirvana and your default kubectl context are never touched.
# Safe to run again: each step skips or reconciles what is already there.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
cd "$DEV_DIR"
ovn_diagnostics() {
  echo; echo "---- Kube-OVN diagnostics (paste this into the chat) ----"
  k -n kube-system get pods -o wide | grep -E 'NAME|ovs|ovn' || true
  echo "---- ovs-ovn pod events ----"
  k -n kube-system describe pod -l app=ovs | sed -n '/^Events:/,$p' | tail -25 || true
  echo "---- ovs-ovn logs ----"
  k -n kube-system logs -l app=ovs --all-containers --tail=60 2>&1 || true
  echo "---- previous ovs-ovn logs (if it restarted) ----"
  k -n kube-system logs -l app=ovs --all-containers --previous --tail=30 2>&1 || true
  echo "---- node: openvswitch module and memory ----"
  docker exec "$(kind get nodes --name "$KIND_CLUSTER" | head -1)" sh -c 'grep -w openvswitch /proc/modules || echo "openvswitch not loaded"; free -m' || true
}

./start.sh
./preflight.sh || { echo "Fix the failed checks above, then run again."; exit 1; }

step "kind cluster $KIND_CLUSTER (in xFoundry's VM)"
if kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; then
  # After the VM was stopped, the node container is stopped too: start it and refresh the kubeconfig.
  for node in $(kind get nodes --name "$KIND_CLUSTER"); do
    [ "$(docker inspect -f '{{.State.Running}}' "$node")" = true ] || docker start "$node" >/dev/null
  done
  kind export kubeconfig --name "$KIND_CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null
  echo "exists"
else
  config=kind.yaml
  if in_vm 'test -c /dev/kvm' 2>/dev/null; then
    # Pass the runtime VM's /dev/kvm into the kind node so KubeVirt can use hardware virtualization.
    config=$(mktemp); cp kind.yaml "$config"
    printf '    extraMounts:\n      - hostPath: /dev/kvm\n        containerPath: /dev/kvm\n' >> "$config"
  fi
  kind create cluster --config "$config" --name "$KIND_CLUSTER" --kubeconfig "$KUBECONFIG"
  # Same tweak Kube-OVN's own kind setup makes: OVS needs a bounded file limit in kind nodes.
  for node in $(kind get nodes --name "$KIND_CLUSTER"); do
    docker exec "$node" sed -i 's/LimitNOFILE=infinity/LimitNOFILE=4096/' /etc/systemd/system/containerd.service
    docker exec "$node" sh -c 'systemctl daemon-reload && systemctl restart containerd'
  done
fi
write_env

step "openvswitch kernel module (xFoundry VM)"
# Pods may not load kernel modules, and the runtime VM forgets loaded modules when it restarts (for example after a
# memory change in Docker Desktop), so load it here on every run, from a privileged helper in the VM's own namespaces.
if in_vm 'modprobe openvswitch 2>/dev/null; [ -d /sys/module/openvswitch ]'; then
  echo "loaded"
else
  echo "Could not load the openvswitch module in xFoundry's VM. Kube-OVN cannot run without it."
  echo "Run platform/dev/preflight.sh for details."
  exit 1
fi
if k -n kube-system get pod -l app=ovs -o name 2>/dev/null | grep -q .; then
  # An ovs-ovn pod that crashed before the module was loaded backs off for minutes; restart it now.
  k -n kube-system delete pod -l app=ovs --wait=false >/dev/null
fi

step "Kube-OVN $KUBE_OVN_VERSION (CNI, Open vSwitch)"
if k -n kube-system get deploy kube-ovn-controller >/dev/null 2>&1; then echo "installed"
else
  # Its last step ("Step 6/6") copies the kubectl-ko plugin into /usr/local/bin and runs a diagnosis; on a Mac that
  # folder is often missing or root-owned, so the step fails after a good install. Stop the script before it.
  curl -fsSL "https://raw.githubusercontent.com/kubeovn/kube-ovn/$KUBE_OVN_VERSION/dist/images/install.sh" \
    | sed '/\[Step 6\/6\]/,$d' > /tmp/kube-ovn-install.sh
  grep -q 'Step 5/6' /tmp/kube-ovn-install.sh || { echo "Kube-OVN's installer changed shape; check $KUBE_OVN_VERSION"; exit 1; }
  # install.sh talks to the current context, labels control-plane nodes kube-ovn/role=master and waits for itself,
  # but gives Open vSwitch only 120 s. On a fresh node that can be too short: wait longer, then run it again to finish
  # (it applies the same objects, so a second run is safe).
  if ! VERSION="$KUBE_OVN_VERSION" bash /tmp/kube-ovn-install.sh; then
    echo "Kube-OVN's installer stopped waiting; giving Open vSwitch up to 10 more minutes..."
    if k -n kube-system rollout status ds/ovs-ovn --timeout=10m; then
      VERSION="$KUBE_OVN_VERSION" bash /tmp/kube-ovn-install.sh || { ovn_diagnostics; exit 1; }
    else
      ovn_diagnostics; exit 1
    fi
  fi
fi
k -n kube-system rollout status deploy/kube-ovn-controller --timeout=10m
k -n kube-system rollout status ds/kube-ovn-cni --timeout=10m

step "Multus $MULTUS_VERSION (extra interfaces)"
curl -fsSL "https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/$MULTUS_VERSION/deployments/multus-daemonset-thick.yml" \
  | sed "s#ghcr.io/k8snetworkplumbingwg/multus-cni:snapshot-thick#ghcr.io/k8snetworkplumbingwg/multus-cni:$MULTUS_VERSION-thick#g" \
  | k apply -f -
k -n kube-system rollout status ds/kube-multus-ds --timeout=10m

step "KubeVirt $KUBEVIRT_VERSION (VMs)"
k apply -f "https://github.com/kubevirt/kubevirt/releases/download/$KUBEVIRT_VERSION/kubevirt-operator.yaml"
k apply -f "https://github.com/kubevirt/kubevirt/releases/download/$KUBEVIRT_VERSION/kubevirt-cr.yaml"
node=$(kind get nodes --name "$KIND_CLUSTER" | head -1)
if docker exec "$node" test -c /dev/kvm; then emu=false; echo "/dev/kvm found: hardware virtualization"
else emu=true; echo "no /dev/kvm: emulation (VMs are slow; fine for functionality)"; fi
k -n kubevirt patch kubevirt kubevirt --type merge -p "{\"spec\":{\"configuration\":{\"developerConfiguration\":{\"useEmulation\":$emu}}}}"
echo "First run pulls about half a gigabyte of KubeVirt images and runs node checks: usually 3–8 minutes."
wait_for kubevirt kv/kubevirt Available 20 || exit 1

step "CDI $CDI_VERSION (disk images)"
k apply -f "https://github.com/kubevirt/containerized-data-importer/releases/download/$CDI_VERSION/cdi-operator.yaml"
k apply -f "https://github.com/kubevirt/containerized-data-importer/releases/download/$CDI_VERSION/cdi-cr.yaml"
wait_for cdi cdi/cdi Available 15 || exit 1

step "Snapshot collector identity (read-only)"
k apply -f ../collector-rbac.yaml

step "Ready"
cat <<MSG
Platform is up in xFoundry's VM '$XFOUNDRY_VM'. Docker Desktop and Nirvana were not touched.
In the shell where you use xfoundry:
  source platform/dev/env.sh                  # this shell only: DOCKER_CONTEXT, KUBECONFIG, XFOUNDRY_ARCH
  export NIRVANA_URL=http://localhost:8000 NIRVANA_TOKEN=nvt_...  NIRVANA_DIR=~/path/to/Nirvana
  xfoundry run examples/dev-lab.yaml
platform/dev/stop.sh gives the VM's memory back; up.sh brings everything back.
MSG
