#!/usr/bin/env bash
# Checks the Mac and xFoundry's VM are ready for the dev platform. Changes nothing (except loading the openvswitch
# module in xFoundry's VM to prove it loads). Docker Desktop is only read, never changed.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
ok=0
pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }
fail() { printf '  \033[31mfail\033[0m  %s\n' "$1"; ok=1; }
warn() { printf '  \033[33mnote\033[0m  %s\n' "$1"; }

echo "Tools"
for t in colima docker kind kubectl; do command -v $t >/dev/null && pass "$t" || fail "$t missing: brew install $t"; done
command -v virtctl >/dev/null && pass "virtctl" || warn "virtctl missing (optional, for VM consoles): brew install virtctl"

echo "Memory on this Mac"
num() { case "$1" in ''|*[!0-9]*) echo 0 ;; *) echo "$1" ;; esac; }
host=$(( $(num "$(sysctl -n hw.memsize 2>/dev/null)") / 1024 / 1024 / 1024 ))
# Docker Desktop is only read here (it may be quit; then it counts as 0).
dd=$(( $(num "$(env -u DOCKER_CONTEXT docker --context desktop-linux info --format '{{.MemTotal}}' 2>/dev/null)") / 1024 / 1024 / 1024 ))
if [ "$host" -gt 0 ]; then
  left=$(( host - XFOUNDRY_VM_MEMORY - dd ))
  pass "Mac ${host} GiB · Docker Desktop (Nirvana) ~${dd} GiB · xFoundry VM ${XFOUNDRY_VM_MEMORY} GiB · left for macOS ~${left} GiB"
  [ "$left" -ge 4 ] || warn "tight: lower Docker Desktop's memory to what Nirvana needs, or set XFOUNDRY_VM_MEMORY=8 before up.sh"
fi
[ "$XFOUNDRY_VM_MEMORY" -ge 8 ] || fail "XFOUNDRY_VM_MEMORY should be at least 8 (GiB)"

echo "xFoundry VM '$XFOUNDRY_VM' (docker context $DOCKER_CONTEXT)"
if ! vm_running; then
  warn "not running yet: up.sh starts it (or run platform/dev/start.sh); the kernel and image checks run then"
  exit $ok
fi
arch=$(docker info --format '{{.Architecture}}'); cpus=$(docker info --format '{{.NCPU}}'); mib=$(( $(docker info --format '{{.MemTotal}}') / 1024 / 1024 ))
pass "$arch, $cpus CPUs, $(( mib / 1024 )).$(( mib % 1024 * 10 / 1024 )) GiB usable"
[ "$mib" -ge 7600 ] || fail "the VM has under 8 GiB; recreate it: platform/dev/down.sh (delete the VM too), then XFOUNDRY_VM_MEMORY=10 platform/dev/up.sh"
if in_vm '[ -d /sys/module/openvswitch ] || { modprobe openvswitch && [ -d /sys/module/openvswitch ]; }' >/dev/null 2>&1; then
  pass "openvswitch module loads (needed by Kube-OVN)"
else fail "the openvswitch module cannot be loaded in xFoundry's VM; Kube-OVN cannot run"; fi
if in_vm '[ -c /dev/kvm ]' >/dev/null 2>&1; then pass "/dev/kvm present: VMs use hardware virtualization"
else warn "no /dev/kvm: VMs run in emulation (slow but works; needs an M3 or newer Mac with macOS 15+ for nested virtualization)"; fi

echo "Images have a build for $arch"
want=$([ "$arch" = x86_64 ] && echo amd64 || echo arm64)
for img in docker.io/kubeovn/kube-ovn:$KUBE_OVN_VERSION ghcr.io/k8snetworkplumbingwg/multus-cni:$MULTUS_VERSION-thick \
           quay.io/kubevirt/virt-operator:$KUBEVIRT_VERSION quay.io/kubevirt/cirros-container-disk-demo:$KUBEVIRT_VERSION \
           quay.io/frrouting/frr:10.7.1 docker.io/library/haproxy:3.4.7; do
  if docker manifest inspect "$img" 2>/dev/null | grep -q "\"architecture\": \"$want\""; then pass "$img"
  else fail "$img has no $want build (or cannot be reached)"; fi
done
exit $ok
