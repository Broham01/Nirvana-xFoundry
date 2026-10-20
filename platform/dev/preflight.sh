#!/usr/bin/env bash
# Checks the Mac is ready for the dev platform. Changes nothing.
set -uo pipefail
cd "$(dirname "$0")" || exit 1; source versions.env
ok=0
pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }
fail() { printf '  \033[31mfail\033[0m  %s\n' "$1"; ok=1; }
warn() { printf '  \033[33mnote\033[0m  %s\n' "$1"; }

echo "Tools"
for t in docker kind kubectl; do command -v $t >/dev/null && pass "$t" || fail "$t missing: brew install ${t/docker/colima docker}"; done
command -v virtctl >/dev/null && pass "virtctl" || warn "virtctl missing (optional, for VM consoles): brew install virtctl"

echo "Container runtime"
if ! docker info >/dev/null 2>&1; then fail "docker is not running (start Colima or Docker Desktop)"; exit 1; fi
ctx=$(docker context show 2>/dev/null); pass "docker context: $ctx"
arch=$(docker info --format '{{.Architecture}}'); cpus=$(docker info --format '{{.NCPU}}'); mem=$(( $(docker info --format '{{.MemTotal}}') / 1024 / 1024 / 1024 ))
pass "runtime VM: $arch, $cpus CPUs, ${mem} GiB"
[ "$cpus" -ge 4 ] || fail "give the runtime VM at least 4 CPUs (6 or more is better)"
[ "$mem" -ge 8 ] || fail "give the runtime VM at least 8 GiB memory (12 is better)"
case "$arch" in aarch64|arm64) echo "export XFOUNDRY_ARCH=arm64" > .arch ;; *) echo "export XFOUNDRY_ARCH=amd64" > .arch ;; esac
pass "$(cat .arch)"

echo "Kernel (inside the runtime VM)"
probe() { docker run --rm --privileged --pid=host docker.io/library/alpine:3.22 nsenter -t 1 -m -u -n -i -- sh -c "$1" >/dev/null 2>&1; }
if probe 'modprobe openvswitch 2>/dev/null || [ -d /sys/module/openvswitch ]'; then pass "openvswitch module (needed by Kube-OVN)"
else fail "no openvswitch kernel module: Kube-OVN cannot run on this runtime. Use Colima: colima start --cpu 6 --memory 12 --vm-type vz"; fi
if probe '[ -c /dev/kvm ]'; then pass "/dev/kvm present: VMs use hardware virtualization"
else warn "no /dev/kvm: VMs run in emulation (slow but works). On an M3 or newer Mac with macOS 15+: colima start --vm-type vz --nested-virtualization"; fi

echo "Images have a build for $arch"
for img in docker.io/kubeovn/kube-ovn:$KUBE_OVN_VERSION ghcr.io/k8snetworkplumbingwg/multus-cni:$MULTUS_VERSION-thick \
           quay.io/kubevirt/virt-operator:$KUBEVIRT_VERSION quay.io/kubevirt/cirros-container-disk-demo:$KUBEVIRT_VERSION \
           quay.io/frrouting/frr:10.7.1 docker.io/library/haproxy:3.4.7; do
  want=$([ "$arch" = x86_64 ] && echo amd64 || echo arm64)
  if docker manifest inspect "$img" 2>/dev/null | grep -q "\"architecture\": \"$want\""; then pass "$img"
  else fail "$img has no $want build (or cannot be reached)"; fi
done
exit $ok
