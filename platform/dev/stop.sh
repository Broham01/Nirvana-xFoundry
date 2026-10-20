#!/usr/bin/env bash
# Pauses xFoundry's VM to give its memory back. The cluster and its VMs are kept; up.sh brings them back.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if ! vm_running; then echo "xFoundry VM '$XFOUNDRY_VM' is not running."; exit 0; fi
colima stop --profile "$XFOUNDRY_VM"
echo "Stopped. Docker Desktop was not touched."
