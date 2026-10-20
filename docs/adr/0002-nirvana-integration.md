# ADR 0002: Nirvana integration (plan, approve, then apply)

Date: 2026-10-20 · Accepted. Nirvana items 1–4 are built and merged in [Nirvana PR #1](https://github.com/Broham01/Nirvana/pull/1) (Nirvana ADR 0013); 5 is polling for now; 6 is open; 7 is proposed.

## Decision

xFoundry changes a cluster only through this loop:

1. Render the spec into a manifest (deterministic bytes).
2. Push a snapshot of the target to Nirvana (`POST /api/v1/snapshots`). The snapshot comes from Nirvana's own `collect-k8s-snapshot.py`, or from an xFoundry model for review before anything exists.
3. Submit the manifest as a plan (`POST /api/v1/plans`; `Mode=delete` for prune and destroy). Record the plan id, snapshot id, manifest SHA-256 and snapshot fingerprint in the ledger.
4. A person reviews and approves in Nirvana. High findings must be checked, as today.
5. Before applying, xFoundry collects a fresh snapshot and runs the gate (`xfoundry/gate.py`):
   - the plan is approved;
   - the plan's manifest hash equals both the hash about to be applied and the hash recorded;
   - the snapshot id matches;
   - the fresh snapshot's fingerprint equals the recorded one (nodes, namespaces, objects, CRDs; pods left out because they churn);
   - the action matches;
   - neither the plan's snapshot nor the fresh one is a model.

   Any failure means re-plan.
6. Apply (`kubectl apply`, or `delete` for prune and destroy). A plan is used once.

A plan made against a model can be reviewed, but its fingerprint will never match a collected cluster, so it can never pass the gate. That is Nirvana's ADR 0012 evidence rule, enforced in code.

## Changes needed in Nirvana, in order

1. **Service token.** The API today is passkey session + CSRF. Add a bearer token per owner, created and revoked in Settings, stored hashed. Scopes: `snapshots:write`, `plans:write`, `plans:read`. It is rejected on `/decision` and on every other route. Logged on each use.
2. **Cluster-scoped custom resources.** The planner places a cluster-scoped `kubeovn.io/v1 Subnet` in namespace `default`, and the collector only keeps namespaced objects, so these are always "create". Fix: treat objects without a namespace as cluster-scoped when their CRD is cluster-scoped. The collector should record each CRD's scope, and the planner should key such objects as `-/<Kind>/<name>`.
3. **Namespace annotations.** The collector keeps namespace labels but not annotations, so a re-apply always shows the namespace as changed. Keep annotations (minus the skip list), the same way objects keep them.
4. **KubeVirt rules in the Kubernetes planner**, lifting VMs from `partial` to `modelled`:
   - `runStrategy` Always→Halted stops the VM;
   - CPU, memory or disk changes need a restart unless the VM is live-update capable;
   - deleting a VM deletes its DataVolumes (data loss, irreversible);
   - a VM labelled `nirvana.io/protected` is a protected target.
5. **Decision notification.** A webhook on decision, or keep polling `GET /plans/{id}` (enough for now).
6. **Kubernetes models from `xfoundry.environment/1`.** ADR 0012 step 3 imports this spec, with the schema vendored from this repo, rather than inventing its own.
7. **Kube-OVN `Vpc` as a known kind** (proposed 2026-10-20, not made): today it plans at `partial`; see [ADR 0003](0003-local-dev-platform.md).

## Verified live (2026-10-20, Nirvana PR #1)

1. A token was created on Nirvana's Account page.
2. `xfoundry plan` uploaded a snapshot and submitted edge-lab: "Creates 11 objects in 1 namespace. 2 are protected. Nothing unverified."
3. The token's attempt to approve got 403.
4. Alice approved in the browser.
5. The gate refused drift, a model given as the fresh snapshot, and an edited spec, and passed the approved manifest.

The live run also caught an xFoundry bug: idempotency keys were reused for a different request body, and Nirvana refused them. They are now derived from the whole request.

## Before the fixes (Nirvana `af9b5fb`)

Nirvana's `k8s_planner.plan` on the `edge-lab` example:

| Case | Result |
|---|---|
| Create vs foundation model | completed, 11 actions, 0 gaps |
| Re-apply vs built model | no-ops for all namespaced objects (Subnets and Namespace show as changes: items 2 and 3) |
| Firewall resize | `k8s-update` on `fw01` at `partial` confidence (item 4) |
| Destroy | every object deleted, namespace deletion summarised, 3 warnings for Subnets (item 2) |
