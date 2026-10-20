"""The apply gate: xFoundry changes a cluster only when a person approved exactly this change in Nirvana,
and the cluster still looks the way it did when the change was planned.

Checks, all required:
  1. the Nirvana plan's state is "approved";
  2. SHA-256 of the plan's manifest == SHA-256 of what xFoundry is about to apply == what xFoundry submitted;
  3. the plan was made against the snapshot xFoundry recorded when it submitted;
  4. a fresh snapshot of the target has the same fingerprint (no drift since planning);
  5. the plan's mode (apply or delete) matches the action.
Any failure means re-plan, never "apply anyway".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .render import manifest_hash


def fingerprint(snapshot: dict) -> str:
    """What must not change between planning and applying. Pods are left out on purpose: they churn
    (restarts, rescheduling) without changing what an apply does. Nodes, namespaces, objects and CRDs count."""
    k = snapshot.get('kubernetes') or {}
    view = {
        'nodes': sorted((n['name'], bool(n.get('ready')), bool(n.get('unschedulable'))) for n in k.get('nodes', [])),
        'namespaces': sorted((n['name'], json.dumps(n.get('labels', {}), sort_keys=True)) for n in k.get('namespaces', [])),
        'objects': sorted((o['id'], json.dumps(o.get('spec', {}), sort_keys=True), json.dumps(o.get('labels', {}), sort_keys=True))
                          for o in k.get('objects', [])),
        'crds': sorted(k.get('crds', [])),
    }
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check(plan: dict, manifest: str, record: dict, fresh_snapshot: dict, delete: bool = False) -> GateResult:
    reasons = []
    if plan.get('state') != 'approved':
        reasons.append(f"Plan {plan.get('id')} is '{plan.get('state')}', not approved in Nirvana.")
    want = manifest_hash(manifest)
    if manifest_hash(plan.get('script', '')) != want:
        reasons.append('The manifest about to be applied is not the one Nirvana planned.')
    if record.get('manifest_hash') != want:
        reasons.append('The manifest differs from the one xFoundry submitted for this plan.')
    if str(plan.get('snapshot_id')) != str(record.get('snapshot_id')):
        reasons.append('The plan was made against a different snapshot than xFoundry recorded.')
    if fingerprint(fresh_snapshot) != record.get('fingerprint'):
        reasons.append('The cluster changed since the plan was made (drift). Re-plan against a fresh snapshot.')
    if bool(record.get('delete')) != delete:
        reasons.append('The plan was for a different action (apply vs delete).')
    return GateResult(not reasons, reasons)
