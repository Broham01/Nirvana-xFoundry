#!/usr/bin/env bash
# Deletes the dev cluster and everything in it. Asks first.
set -euo pipefail
cd "$(dirname "$0")"; source versions.env
read -r -p "Delete kind cluster '$KIND_CLUSTER' and all its VMs? [y/N] " a
[ "$a" = y ] || [ "$a" = Y ] || { echo "Nothing deleted."; exit 0; }
kind delete cluster --name "$KIND_CLUSTER"
