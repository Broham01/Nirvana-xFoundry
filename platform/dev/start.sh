#!/usr/bin/env bash
# Starts xFoundry's own Linux VM (Colima instance "xfoundry"), next to Docker Desktop. Safe to run again.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
command -v colima >/dev/null || { echo "Colima is missing: brew install colima"; exit 1; }
# Colima's default (--activate=true) runs `docker context use colima-xfoundry`, which pulls every other Docker tool,
# Nirvana included, away from Docker Desktop. Never activate; and if an earlier start already switched it, switch back.
restore_default() {
  local now; now=$(env -u DOCKER_CONTEXT docker context show 2>/dev/null || true)
  if [ "$now" = "$DOCKER_CONTEXT" ]; then
    local back=desktop-linux
    env -u DOCKER_CONTEXT docker context inspect "$back" >/dev/null 2>&1 || back=default
    env -u DOCKER_CONTEXT docker context use "$back" >/dev/null
    echo "Your default docker context had been switched to $now; it is back to $back."
  fi
}
if vm_running; then echo "xFoundry VM '$XFOUNDRY_VM' is running."; restore_default; exit 0; fi
step "Starting xFoundry VM '$XFOUNDRY_VM' ($XFOUNDRY_VM_CPUS CPUs, ${XFOUNDRY_VM_MEMORY} GiB) next to Docker Desktop"
args=(start --profile "$XFOUNDRY_VM" --activate=false --cpu "$XFOUNDRY_VM_CPUS" --memory "$XFOUNDRY_VM_MEMORY" --disk "$XFOUNDRY_VM_DISK" --vm-type vz)
# Nested virtualization (M3 or newer, macOS 15+) gives KubeVirt /dev/kvm. Older Macs: Colima rejects the flag; start without it.
if ! colima "${args[@]}" --nested-virtualization; then
  echo "Starting without nested virtualization (VMs will run in emulation)."
  colima stop --profile "$XFOUNDRY_VM" >/dev/null 2>&1 || true
  colima "${args[@]}"
fi
restore_default
echo "Docker Desktop and its containers were not touched. Your default docker context: $(env -u DOCKER_CONTEXT docker context show 2>/dev/null || echo unknown)"
