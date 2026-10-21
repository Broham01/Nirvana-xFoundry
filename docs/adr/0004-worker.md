# ADR 0004: The worker (xStage's console drives xFoundry)

Date: 2026-10-21 · Accepted. Contract version 2 ([docs/integration](../integration/CONTRACT.md)); xStage [ADR 0014](https://github.com/Broham01/Nirvana-xStage/blob/main/docs/adr/0014-console-and-live-updates.md).

## Decision

`xfoundry worker` is `xfoundry run` driven from the browser. It checks in with Nirvana xStage every few seconds, claims jobs a person queued on the Environments page, and for each one:

1. checks the spec (`spec.check`) and that its name matches the environment;
2. for `check`: reports what is running and stops;
3. collects a snapshot with xStage's collector and submits the plan (`cli.submit_plan`), then reports `waiting_approval` with the plan id;
4. waits for the decision; a cancel or a send-back ends the job with nothing changed;
5. reports `applying` (xStage refuses this once a person asked to cancel), collects a fresh snapshot and runs the gate and `kubectl` (`cli.apply_plan`); a refusal fails the job;
6. reports what is running (`status.observe`: VMs, appliances, addresses) and finishes.

Every rule of ADR 0002 holds: nothing is applied without the gate, the worker never approves, models are never applied. The worker cannot queue, cancel or edit jobs; its token cannot reach those routes.

**Restarts.** A claimed job comes back to the same token until it finishes. A worker stopped while a plan waits resumes waiting for the same plan; one stopped mid-apply runs the gate again (kubectl apply and delete are idempotent), or reports success if the ledger shows the apply finished.

**On the Mac.** `Set xFoundry token.command` stores the worker's token in the Keychain (`nirvana-xfoundry-token`); `Start xFoundry worker.command` runs `platform/dev/worker.sh`, which loads the dev platform's settings and starts the worker in that window.

## Consequences

- The ledger (`.xfoundry/`) stays the worker's local record, so the CLI and the worker can be mixed for the same environment.
- Status is read with `kubectl get` and shown in xStage; it is never change evidence.
- One worker per cluster for now; it runs one job at a time.
