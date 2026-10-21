# Nirvana xFoundry: instructions for Claude

xFoundry builds, scales and destroys environments on Kubernetes (KubeVirt VMs, Kube-OVN networks, Multus, vclusters). It **holds cluster credentials and executes**, but it changes a cluster only through an approved Nirvana plan. Overview and status: [README.md](README.md). Spec: [docs/SPEC.md](docs/SPEC.md). Order of work: [docs/ROADMAP.md](docs/ROADMAP.md). Decisions: `docs/adr/`.

## Sibling project: Nirvana xStage

[Broham01/Nirvana-xStage](https://github.com/Broham01/Nirvana-xStage) (private) plans changes and is where a person approves them. It is developed in separate Claude chats, with its state in the claude.ai "Nirvana xStage" Project. It was called Nirvana until 2026-10-21; "Nirvana" is now the family name, and in this repo (code, settings `NIRVANA_*`, `xfoundry/nirvana.py`) "Nirvana" still means xStage.

**Before changing anything on the boundary, read [docs/integration/CONTRACT.md](docs/integration/CONTRACT.md) and the newest lines of [docs/integration/CHANGES.md](docs/integration/CHANGES.md).** The boundary is:
- `xfoundry/nirvana.py`: only the token routes in the contract, and only the plan and snapshot fields it lists;
- `xfoundry/gate.py`: the approval, hash, snapshot, fingerprint, action and model checks;
- `xfoundry/model.py`: output must be valid `nirvana.snapshot/1` marked `source.kind: "model"`;
- `xfoundry.environment/1` and `contracts/environment.schema.json` (owned here; Nirvana will import it);
- `spec.names`, which must match Nirvana's `model_builder.names`.

When a change touches the boundary:
1. Update `docs/integration/contract.json` and `CONTRACT.md` here, then copy both byte for byte to the Nirvana repo (in the same sitting; push only if Elfaz asks).
2. Add a dated line to `docs/integration/CHANGES.md` in both repos saying what the other side must do.
3. `tests/test_contract.py` must pass here. Nirvana's CI fails while the two `contract.json` copies differ; that is intended.
4. If xFoundry needs something new from Nirvana (a route, a field, planner support), write it as a numbered request in ADR 0002 and the contract's open items. Do not work around Nirvana: no extra routes, no direct database access, no approving.

## Rules that never change

- **Never apply without the gate.** Every `kubectl apply` or `delete` goes through `gate.check`: approved plan, same manifest hash, same snapshot, no drift, matching action, no models. Any failure means re-plan, never "apply anyway".
- **Never approve.** xFoundry has no code path that approves or requests changes in Nirvana, and must not gain one.
- **Never ask Elfaz to paste a token into chat.** `NIRVANA_TOKEN` is set locally; tokens are created on Nirvana's Account page.
- Snapshots of real clusters are sensitive and are never committed.
- The local dev platform (`platform/dev`) runs on Elfaz's Mac; do not tear down a running cluster without asking.
