# Integration changes (Nirvana ↔ xFoundry)

Append-only. Add a dated line in **both** repos whenever either side changes something the other depends on: an API route, a field read across the boundary, the snapshot or spec format, the gate, or a rule in [CONTRACT.md](CONTRACT.md). Newest first. Say which side changed and what the other side must do, if anything.

- 2026-10-21 · Nirvana · The Kubernetes planner models Kube-OVN `Vpc` (no longer partial) and flags VPC routing changes. xFoundry: none; closes ADR 0002 item 7.
- 2026-10-20 · both · Contract v1 written down: [CONTRACT.md](CONTRACT.md) and `contract.json`, with contract tests in both repos. Open item: `power: half` differs between the apps.
- 2026-10-20 · Nirvana · Service tokens, cluster-scoped custom resources, namespace annotations, KubeVirt/Multus/Kube-OVN planning (Nirvana ADR 0013, PR #1). xFoundry: none; this is what ADR 0002 asked for.
- 2026-10-20 · xFoundry · Idempotency keys derived from the whole request after Nirvana refused a reused key (xFoundry ADR 0002).
