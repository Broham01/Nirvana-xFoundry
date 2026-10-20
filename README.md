# Nirvana xFoundry

xFoundry builds, scales and destroys environments on Kubernetes: firewalls, routers, load balancers, VMs, networks and nested clusters. It changes nothing until a person has approved the exact change in [Nirvana](https://github.com/Broham01/Nirvana).

**Nirvana plans. xFoundry builds.** Nirvana never connects to infrastructure and never executes. xFoundry is the half that does, and it only applies a plan Nirvana approved, against a cluster that has not drifted since the plan was made.

```
spec (xfoundry.environment/1)
  └─ xfoundry render ──► manifest ──► Nirvana plan ──► person approves
                                                         │
             fresh snapshot ──► xfoundry gate ◄──────────┘
                                   │ approved · same bytes · same snapshot · no drift
                                   ▼
                              kubectl apply
```

## Status (2026-10-20)

Built:
- the shared spec and its validator;
- the renderer (Kube-OVN, Multus, KubeVirt, Deployments, vcluster);
- Nirvana model projections;
- the Nirvana client;
- the apply gate and the CLI.

21 tests pass. The rendered output was checked against Nirvana's real Kubernetes planner.

Not built yet:
- the platform cluster (phase 0);
- the Nirvana service token (ADR 0002 item 1);
- the operator;
- the image factory;
- Cluster API.

## Try it

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/xfoundry validate examples/edge-lab.yaml
.venv/bin/xfoundry render examples/edge-lab.yaml
.venv/bin/xfoundry model examples/edge-lab.yaml --stage built -o edge-lab.model.json   # upload in Nirvana > Environment
.venv/bin/pytest -q
```

With Nirvana running and a service token (`NIRVANA_URL`, `NIRVANA_TOKEN`):

```sh
xfoundry plan  examples/edge-lab.yaml --model                 # review only: a modelled plan can never pass the gate
xfoundry plan  examples/edge-lab.yaml --snapshot cluster.json # cluster.json from Nirvana's collect-k8s-snapshot.py
xfoundry status examples/edge-lab.yaml
xfoundry apply  examples/edge-lab.yaml --snapshot fresh.json --dry-run
```

Scale a VM group down by editing `count`, then `plan --action prune` / `apply --action prune` removes the extra VMs. `--action destroy` removes the environment.

## Documents

- [docs/SPEC.md](docs/SPEC.md): the shared spec.
- [docs/ROADMAP.md](docs/ROADMAP.md): phases and order of work.
- [ADR 0001](docs/adr/0001-foundation.md): platform and tool choices.
- [ADR 0002](docs/adr/0002-nirvana-integration.md): the plan → approve → gate → apply loop, and the Nirvana changes it needs.
