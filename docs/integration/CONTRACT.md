# Nirvana xStage ↔ Nirvana xFoundry integration contract

In this contract "Nirvana" means Nirvana xStage, the planner (renamed 2026-10-21). Format names such as `nirvana.snapshot/1`, `nirvana.plan/1` and the label `nirvana.io/protected` keep the family name and do not change.

Mirrored byte for byte in [Broham01/Nirvana-xStage](https://github.com/Broham01/Nirvana-xStage) and [Broham01/Nirvana-xFoundry](https://github.com/Broham01/Nirvana-xFoundry) at `docs/integration/`. The machine-readable copy is [`contract.json`](contract.json); both repos test themselves against it.

**Nirvana plans. xFoundry builds.** Nirvana never connects to infrastructure and never executes. xFoundry applies only a plan a person approved in Nirvana, against a cluster that has not drifted since.

Decisions behind this: Nirvana [ADR 0012](https://github.com/Broham01/Nirvana-xStage/blob/main/docs/adr/0012-modelled-environments.md) (models) and [ADR 0013](https://github.com/Broham01/Nirvana-xStage/blob/main/docs/adr/0013-service-tokens-and-xfoundry.md) (service tokens); xFoundry [ADR 0002](https://github.com/Broham01/Nirvana-xFoundry/blob/main/docs/adr/0002-nirvana-integration.md) (plan → approve → gate → apply). The console (contract version 2): xStage [ADR 0014](https://github.com/Broham01/Nirvana-xStage/blob/main/docs/adr/0014-console-and-live-updates.md) and xFoundry [ADR 0004](https://github.com/Broham01/Nirvana-xFoundry/blob/main/docs/adr/0004-worker.md).

## Who owns what

| Thing | Owner | The other side may |
|---|---|---|
| Nirvana's API, service tokens, plan states | Nirvana | call the token routes below; never more |
| `nirvana.snapshot/1` and `collect-k8s-snapshot.py` | Nirvana | produce snapshots in this format (xFoundry models) and run the collector |
| Planning, findings, approval | Nirvana | read the result; never approve |
| `xfoundry.environment/1` and its schema | xFoundry | import it (ADR 0012 step 3), vendoring the schema |
| Rendering, the gate, `kubectl apply` | xFoundry | nothing: Nirvana never executes |
| Environments and jobs in the console, who may queue or cancel | Nirvana | claim jobs, report progress and status; never queue, cancel or edit |
| The worker and what it reports | xFoundry | show the reports; never treat them as change evidence |

## The API xFoundry uses

Bearer service token (`nvt_…`), created by a person on Nirvana's Account page. These are the only routes a token can reach:

| Method | Path | Scope |
|---|---|---|
| GET | `/api/v1/snapshots` | `snapshots:read` |
| POST | `/api/v1/snapshots` | `snapshots:write` |
| GET | `/api/v1/plans` | `plans:read` |
| GET | `/api/v1/plans/{id}` | `plans:read` |
| POST | `/api/v1/plans` | `plans:write` |
| POST | `/api/v1/foundry/jobs/claim` | `jobs:write` |
| GET | `/api/v1/foundry/jobs/{id}` | `jobs:read` |
| POST | `/api/v1/foundry/jobs/{id}/progress` | `jobs:write` |
| PUT | `/api/v1/foundry/environments/{id}/status` | `environments:status` |

- Plan states: `review`, `approved`, `changes_requested`. xFoundry reads `id`, `state`, `status`, `highs`, `snapshot_id`, `script` and `decision_note`.
- A plan for prune or destroy is submitted with `parameters: {"Mode": "delete"}`.
- Every POST carries an `Idempotency-Key`. The same key with a different body is refused, so xFoundry derives keys from the whole request.

### Jobs (the console, version 2)

A person queues a job in xStage (`create`, `prune`, `destroy`, `check` or `power`). For create, prune and destroy the worker claims it, plans it, waits for a person to approve the plan, then applies it through the gate.

- **Power (version 3, xStage ADR 0021, xFoundry ADR 0008).** A `power` job carries `power: {op, vms}` with `op` one of `start`, `stop`, `restart`, `force-stop`, `force-restart` and `vms` the VMs' Kubernetes names (empty: every VM). Power changes nothing in the spec, so there is no plan and no gate. The worker acts only on VirtualMachines its render of the job's spec contains, and only by setting `runStrategy` (start, stop) or deleting their VirtualMachineInstance (restart; forced: grace period 0). It moves the job from `running` to `succeeded` or `failed`. A later apply sets every VM back to what the spec says.

- **Claim** returns the job this token already holds and has not finished, else the oldest queued one, else `null`. Each claim is also the worker's check-in (`info`: version, platform, cluster, host, and `pid` and `code` so xStage can tell two workers on one token apart and an older copy from a current one), shown in the console. One worker runs per xFoundry folder.
- **Job states:** `queued`, `running`, `waiting_approval`, `applying`, `succeeded`, `failed`, `cancelled`. The worker may move a job only as `worker_may_set` in `contract.json` allows. It never moves a job back to `queued`, and a final state is final.
- **Cancel** is a person's action. A queued job is cancelled at once; a claimed one gets `cancel_requested`, and xStage refuses the move to `applying` from then on. The worker stops before applying, never halfway.
- **Progress** carries `message`, `level`, and optionally `state`, `plan_id` (a plan this owner has) and `error`.
- **Status** (`state`, `message`, `namespace`, `applied`, `manifest_hash`, `members`, `checked_at`) is what the worker saw. A member that is not ready may carry `detail`: one line on why (its disk import, its pods' waiting reasons, the latest warning event). It is shown to the person and never used as change evidence: only the gate's fresh snapshot is.
- xStage reads the spec's `format` and `name` to file it, and its `lifecycle`, `networks`, `appliances`, `vms` and `clusters` to draw it, pause it and expire it; xFoundry validates the whole spec (a `check` job does only that and reports status).
- **Lifecycle (xStage ADR 0020).** While an environment is paused, a claimed job's spec has every VM group's `power` set to `none`; the saved spec is unchanged, and the worker builds the spec it is given. xStage may queue a job on a person's behalf: the rebuild after a reset's destroy succeeded, and a destroy when the spec's `lifecycle.ttl` ran out. Each still becomes a plan a person approves.

## Rules neither side may break

1. **Only a person approves.** No token, scope or xFoundry code path may approve or request changes.
2. **Models are never applied.** A snapshot with `source.kind: "model"` can be planned and reviewed. Nirvana marks such plans Modelled, and xFoundry's gate refuses them.
3. **Nirvana never executes.** Manifests are parsed as data; scripts run only in the sandbox against fakes.
4. **The gate's fingerprint fields stay stable.** xFoundry fingerprints nodes (`name`, `ready`, `unschedulable`), namespaces (`name`, `labels`), objects (`id`, `spec`, `labels`) and `crds`. Nirvana may add fields to snapshots but must not rename or drop these without a contract change.
5. **A job never applies by itself.** Every create, prune and destroy job becomes a plan a person approves; then the gate decides. Power jobs change only VMs' run state, as above, and a person queues each one. Tokens cannot queue, cancel, edit environments or open the live event stream.
6. **Names follow one rule.** `PRD-WEB{nn}` × 3 gives `PRD-WEB01..03`; a pattern without a placeholder gives `name`, `name-2`, `name-3`. The test vectors are in `contract.json`.

## Changing the contract

1. Write the change in `contract.json` and here, and raise `version` if the other side must change code.
2. Copy both files to the other repo in the same sitting, and add a dated line to `CHANGES.md` in both.
3. Run each repo's tests. Nirvana's CI fails while the two copies differ, which is intended: nothing ships against a contract the other side has not seen.
4. Additive changes (a new optional field, a new route) are version-compatible. Removing or renaming anything listed here is not.

## Open items

- **`power: half` differs.** Nirvana's vSphere model generator powers on every other VM; xFoundry powers on the first half, rounded up. Agree one rule before Nirvana imports `xfoundry.environment/1`.
- **Decisions are polled.** The worker polls `GET /api/v1/plans/{id}` every few seconds; there is no webhook or token event stream yet. Browsers get live updates (`/api/v1/events`), tokens do not.
