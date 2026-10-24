# Nirvana xFoundry

xFoundry builds, scales and destroys environments on Kubernetes: VMs (KubeVirt), networks (Kube-OVN, Multus), routers, firewalls, load balancers and nested clusters (vcluster). It changes nothing until a person has approved the exact change in **[Nirvana xStage](https://github.com/Broham01/Nirvana-xStage)**, the planner and console.

**Version 1.1** (2026-10-24), working with xStage 1.0 over contract version 3.

**xStage plans. xFoundry builds.** xStage never connects to infrastructure. xFoundry holds the cluster credentials, and it applies only a plan xStage approved, against a cluster that has not drifted since the plan was made.

```
you ask in xStage ──► worker renders the spec ──► plan in xStage ──► a person approves
                                                                         │
                     fresh snapshot ──► gate: approved · same bytes · same snapshot · no drift
                                                                         ▼
                                                                   kubectl apply
```

## Use it

You use xFoundry through xStage: its Environments pages, the catalog, Jobs and the consoles. xStage runs the worker for you. Setup and everyday use are in the **[Nirvana guide](https://github.com/Broham01/Nirvana-xStage/blob/main/docs/GUIDE.md)**; in short:

1. Clone this repo to `~/Nirvana-xFoundry` (xStage looks for it there).
2. Run `platform/dev/setup-mac.sh` once. It brings up the dev platform: kind on Colima with Kube-OVN, Multus, KubeVirt and CDI ([platform/dev](platform/dev/README.md)).
3. Create a service token in xStage (**Settings › Account and tokens**), then double-click **`Set xFoundry token.command`** and paste it. It is stored in the Keychain.
4. Restart xStage. The worker starts and picks up the jobs you queue.

## What it builds

| Piece | How |
| --- | --- |
| **Networks** | A Kube-OVN VPC per environment, subnets with DHCP, optional VLAN underlay. Traffic between networks goes through the environment's router or firewall. |
| **Internet access** | Optional, outbound only, through an egress gateway pod; private ranges are blocked ([ADR 0007](docs/adr/0007-internet-access.md)). |
| **VMs** | KubeVirt VMs from cloud images with cloud-init (user, SSH keys, password hash, packages), fixed addresses and stable MACs ([ADR 0009](docs/adr/0009-fixed-addresses-and-stable-macs.md)). |
| **Appliances** | FRR, HAProxy, Traefik, nginx as containers; VyOS, OPNsense, pfSense as VMs (x86 platforms only). |
| **Clusters** | vcluster inside the environment. |
| **Power** | Start, stop and restart VMs without a plan; the only change made outside the gate ([ADR 0008](docs/adr/0008-power-without-a-plan.md)). |
| **Consoles and logs** | noVNC with paste-as-typing and Ctrl+Alt+Del, and read-only appliance logs, through a view-only proxy ([ADR 0005](docs/adr/0005-vm-consoles.md), [ADR 0006](docs/adr/0006-pod-logs.md)). |

An environment is one YAML spec, `xfoundry.environment/1` ([SPEC](docs/SPEC.md), [schema](contracts/environment.schema.json), [example](examples/edge-lab.yaml)).

## Command line

The CLI is for development and for running the loop without the console:

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/xfoundry validate examples/edge-lab.yaml
.venv/bin/xfoundry render   examples/edge-lab.yaml
.venv/bin/pytest -q
```

With xStage running, set `NIRVANA_URL=http://localhost:8000` and `NIRVANA_TOKEN`, then `xfoundry plan`, `status`, `apply` and `run`. Use `--action prune` to remove scaled-down VMs and `--action destroy` to remove the environment. `xfoundry worker` is what xStage runs for you.

## Rules it keeps

- **Never apply without the gate:** an approved plan, the same manifest hash, the same snapshot, no drift, a matching action, no models. Any failure means re-plan, never "apply anyway". Power is the one documented exception.
- **Never approve.** xFoundry has no code path that approves in xStage.
- **Tokens stay in the Keychain.** Snapshots of real clusters are never committed.

## Documents

- [docs/integration](docs/integration/CONTRACT.md): the contract with xStage (mirrored in both repos) and the log of changes across it. Read it before touching the client, the gate, the worker, models or the spec.
- [docs/SPEC.md](docs/SPEC.md): the spec. [docs/ROADMAP.md](docs/ROADMAP.md): what is done and what is next.
- [docs/adr](docs/adr): decisions.
  - 0001: foundation.
  - 0002: integration with xStage.
  - 0003: dev platform.
  - 0004: worker.
  - 0005: consoles.
  - 0006: logs.
  - 0007: internet.
  - 0008: power.
  - 0009: fixed addresses.
- [platform/dev](platform/dev/README.md): the dev platform, its exit test and troubleshooting.
