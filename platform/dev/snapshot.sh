#!/usr/bin/env bash
# Collects a read-only snapshot of the dev platform with Nirvana's collector, including the custom resources xFoundry renders.
set -euo pipefail
cd "$(dirname "$0")"; source versions.env
: "${NIRVANA_DIR:?set NIRVANA_DIR to your Nirvana checkout}"
out="${1:-dev.snapshot.json}"
python3 "$NIRVANA_DIR/scripts/collect-k8s-snapshot.py" --context "kind-$KIND_CLUSTER" --name "xfoundry-dev" -o "$out" \
  --custom virtualmachines.kubevirt.io --custom datavolumes.cdi.kubevirt.io \
  --custom subnets.kubeovn.io --custom vlans.kubeovn.io --custom vpcs.kubeovn.io \
  --custom network-attachment-definitions.k8s.cni.cncf.io
