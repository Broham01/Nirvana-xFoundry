#!/usr/bin/env bash
# Deletes the dev cluster (and, if you say so, xFoundry's VM). Asks first. Docker Desktop is never touched.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
read -r -p "Delete kind cluster '$KIND_CLUSTER' and all its VMs? [y/N] " a
[ "$a" = y ] || [ "$a" = Y ] || { echo "Nothing deleted."; exit 0; }
if vm_running; then kind delete cluster --name "$KIND_CLUSTER" --kubeconfig "$KUBECONFIG"; fi
rm -f "$KUBECONFIG" "$DEV_DIR/env.sh"
read -r -p "Also delete xFoundry's VM '$XFOUNDRY_VM' (frees its disk; start.sh recreates it)? [y/N] " b
if [ "$b" = y ] || [ "$b" = Y ]; then colima delete --profile "$XFOUNDRY_VM" --force; fi
