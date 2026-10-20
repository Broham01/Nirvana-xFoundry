# Nirvana ↔ xFoundry integration contract

Mirrored byte for byte in [Broham01/Nirvana](https://github.com/Broham01/Nirvana) and [Broham01/nirvana-xfoundry](https://github.com/Broham01/nirvana-xfoundry) at `docs/integration/`. The machine-readable copy is [`contract.json`](contract.json); both repos test themselves against it.

**Nirvana plans. xFoundry builds.** Nirvana never connects to infrastructure and never executes. xFoundry applies only a plan a person approved in Nirvana, against a cluster that has not drifted since.

Decisions behind this: Nirvana [ADR 0012](https://github.com/Broham01/Nirvana/blob/main/docs/adr/0012-modelled-environments.md) (models) and [ADR 0013](https://github.com/Broham01/Nirvana/blob/main/docs/adr/0013-service-tokens-and-xfoundry.md) (service tokens); xFoundry [ADR 0002](https://github.com/Broham01/nirvana-xfoundry/blob/main/docs/adr/0002-nirvana-integration.md) (plan → approve → gate → apply).

## Who owns what

| Thing | Owner | The other side may |
|---|---|---|
| Nirvana's API, service tokens, plan states | Nirvana | call the token routes below; never more |
| `nirvana.snapshot/1` and `collect-k8s-snapshot.py` | Nirvana | produce snapshots in this format (xFoundry models) and run the collector |
| Planning, findings, approval | Nirvana | read the result; never approve |
| `xfoundry.environment/1` and its schema | xFoundry | import it (ADR 0012 step 3), vendoring the schema |
| Rendering, the gate, `kubectl apply` | xFoundry | nothing: Nirvana never executes |

## The API xFoundry uses

Bearer service token (`nvt_…`), created by a person on Nirvana's Account page. These are the only routes a token can reach:

| Method | Path | Scope |
|---|---|---|
| GET | `/api/v1/snapshots` | `snapshots:read` |
| POST | `/api/v1/snapshots` | `snapshots:write` |
| GET | `/api/v1/plans` | `plans:read` |
| GET | `/api/v1/plans/{id}` | `plans:read` |
| POST | `/api/v1/plans` | `plans:write` |

- Plan states: `review`, `approved`, `changes_requested`. xFoundry reads `id`, `state`, `status`, `highs`, `snapshot_id`, `script` and `decision_note`.
- A plan for prune or destroy is submitted with `parameters: {"Mode": "delete"}`.
- Every POST carries an `Idempotency-Key`. The same key with a different body is refused, so xFoundry derives keys from the whole request.

## Rules neither side may break

1. **Only a person approves.** No token, scope or xFoundry code path may approve or request changes.
2. **Models are never applied.** A snapshot with `source.kind: "model"` can be planned and reviewed. Nirvana marks such plans Modelled, and xFoundry's gate refuses them.
3. **Nirvana never executes.** Manifests are parsed as data; scripts run only in the sandbox against fakes.
4. **The gate's fingerprint fields stay stable.** xFoundry fingerprints nodes (`name`, `ready`, `unschedulable`), namespaces (`name`, `labels`), objects (`id`, `spec`, `labels`) and `crds`. Nirvana may add fields to snapshots but must not rename or drop these without a contract change.
5. **Names follow one rule.** `PRD-WEB{nn}` × 3 gives `PRD-WEB01..03`; a pattern without a placeholder gives `name`, `name-2`, `name-3`. The test vectors are in `contract.json`.

## Changing the contract

1. Write the change in `contract.json` and here, and raise `version` if the other side must change code.
2. Copy both files to the other repo in the same sitting, and add a dated line to `CHANGES.md` in both.
3. Run each repo's tests. Nirvana's CI fails while the two copies differ, which is intended: nothing ships against a contract the other side has not seen.
4. Additive changes (a new optional field, a new route) are version-compatible. Removing or renaming anything listed here is not.

## Open items

- **`power: half` differs.** Nirvana's vSphere model generator powers on every other VM; xFoundry powers on the first half, rounded up. Agree one rule before Nirvana imports `xfoundry.environment/1`.
- **Decisions are polled.** xFoundry polls `GET /api/v1/plans/{id}`; there is no webhook yet.
- **Kube-OVN `Vpc`** plans at partial confidence in Nirvana (proposed in xFoundry ADR 0002, item 7).
