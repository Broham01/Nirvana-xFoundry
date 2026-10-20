#!/usr/bin/env bash
# Creates the local dev platform: kind + Kube-OVN + Multus + KubeVirt + CDI + the snapshot collector's read-only identity.
# Safe to run again: each step skips or reconciles what is already there.
set -euo pipefail
cd "$(dirname "$0")"; source versions.env
CTX="kind-$KIND_CLUSTER"
k() { kubectl --context "$CTX" "$@"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
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

./preflight.sh || { echo "Fix the failed checks above, then run again."; exit 1; }

step "kind cluster $KIND_CLUSTER"
if kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; then echo "exists"
else
  config=kind.yaml
  if docker run --rm --privileged --pid=host docker.io/library/alpine:3.22 nsenter -t 1 -m -- test -c /dev/kvm 2>/dev/null; then
    # Pass the runtime VM's /dev/kvm into the kind node so KubeVirt can use hardware virtualization.
    config=$(mktemp); cp kind.yaml "$config"
    printf '    extraMounts:\n      - hostPath: /dev/kvm\n        containerPath: /dev/kvm\n' >> "$config"
  fi
  kind create cluster --config "$config" --name "$KIND_CLUSTER"
  # Same tweak Kube-OVN's own kind setup makes: OVS needs a bounded file limit in kind nodes.
  for node in $(kind get nodes --name "$KIND_CLUSTER"); do
    docker exec "$node" sed -i 's/LimitNOFILE=infinity/LimitNOFILE=4096/' /etc/systemd/system/containerd.service
    docker exec "$node" sh -c 'systemctl daemon-reload && systemctl restart containerd'
  done
fi
kubectl config use-context "$CTX" >/dev/null

step "openvswitch kernel module (runtime VM)"
# Pods may not load kernel modules, and the runtime VM forgets loaded modules when it restarts (for example after a
# memory change in Docker Desktop), so load it here on every run, from a privileged helper in the VM's own namespaces.
if docker run --rm --privileged --pid=host docker.io/library/alpine:3.22 nsenter -t 1 -m -u -n -i -- \
     sh -c 'modprobe openvswitch 2>/dev/null; [ -d /sys/module/openvswitch ]'; then
  echo "loaded"
else
  echo "Could not load the openvswitch module in the runtime VM. Kube-OVN cannot run without it."
  echo "Use Colima instead: colima start --cpu 6 --memory 12 --disk 60 --vm-type vz"
  exit 1
fi
if k -n kube-system get pod -l app=ovs -o name 2>/dev/null | grep -q .; then
  # An ovs-ovn pod that crashed before the module was loaded backs off for minutes; restart it now.
  k -n kube-system delete pod -l app=ovs --wait=false >/dev/null
fi

step "Kube-OVN $KUBE_OVN_VERSION (CNI, Open vSwitch)"
if k -n kube-system get deploy kube-ovn-controller >/dev/null 2>&1; then echo "installed"
else
  curl -fsSL "https://raw.githubusercontent.com/kubeovn/kube-ovn/$KUBE_OVN_VERSION/dist/images/install.sh" -o /tmp/kube-ovn-install.sh
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
k -n kubevirt wait kv kubevirt --for condition=Available --timeout=15m

step "CDI $CDI_VERSION (disk images)"
k apply -f "https://github.com/kubevirt/containerized-data-importer/releases/download/$CDI_VERSION/cdi-operator.yaml"
k apply -f "https://github.com/kubevirt/containerized-data-importer/releases/download/$CDI_VERSION/cdi-cr.yaml"
k wait cdi cdi --for condition=Available --timeout=15m

step "Snapshot collector identity (read-only)"
k apply -f ../collector-rbac.yaml

step "Ready"
cat <<MSG
Platform is up (context $CTX). Next:
  source platform/dev/.arch            # XFOUNDRY_ARCH for this platform
  NIRVANA_DIR=~/path/to/Nirvana platform/dev/snapshot.sh   # collect a snapshot for Nirvana
  xfoundry plan examples/dev-lab.yaml --snapshot platform/dev/dev.snapshot.json
See platform/dev/README.md for the rest of the loop.
MSG
