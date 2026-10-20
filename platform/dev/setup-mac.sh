#!/usr/bin/env bash
# One command for the Mac: installs what's missing, removes the old half-built cluster from Docker Desktop (asks first),
# then brings the dev platform up in xFoundry's own VM. Docker Desktop and Nirvana keep running untouched.
#   platform/dev/setup-mac.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== Tools (Homebrew)"
command -v brew >/dev/null || { echo "Homebrew is needed first: https://brew.sh"; exit 1; }
missing=()
for t in colima docker kind kubectl; do command -v "$t" >/dev/null || missing+=("$t"); done
if [ ${#missing[@]} -gt 0 ]; then echo "Installing: ${missing[*]}"; brew install "${missing[@]}"; else echo "all present"; fi

echo "== Old cluster in Docker Desktop"
# An earlier attempt created kind cluster 'xfoundry' inside Docker Desktop, where Kube-OVN cannot run.
if env -u DOCKER_CONTEXT docker --context desktop-linux ps --format '{{.Names}}' 2>/dev/null | grep -qx 'xfoundry-control-plane'; then
  read -r -p "Remove the old 'xfoundry' kind cluster from Docker Desktop? Nirvana's containers are not affected. [y/N] " a
  if [ "$a" = y ] || [ "$a" = Y ]; then
    DOCKER_CONTEXT=desktop-linux kind delete cluster --name xfoundry --kubeconfig "$(mktemp)"
    # The earlier script also added kind-xfoundry to ~/.kube/config and made it current; take that back out.
    [ "$(kubectl config current-context 2>/dev/null)" = kind-xfoundry ] && kubectl config unset current-context >/dev/null
    kubectl config delete-context kind-xfoundry >/dev/null 2>&1 || true
    kubectl config delete-cluster kind-xfoundry >/dev/null 2>&1 || true
    kubectl config delete-user kind-xfoundry >/dev/null 2>&1 || true
  else
    echo "Left in place (it uses Docker Desktop memory; remove it later with: DOCKER_CONTEXT=desktop-linux kind delete cluster --name xfoundry)"
  fi
else
  echo "none"
fi

echo "== Dev platform in xFoundry's own VM"
./up.sh
