# Dev platform on a Mac

A one-node Kubernetes cluster on your Mac with the same stack the cloud platform will run: Kube-OVN (Open vSwitch), Multus, KubeVirt and CDI. It's for getting the whole loop working (spec → Nirvana plan → approval → apply → real VMs and appliances) before paying for cloud servers.

Versions are pinned in [`versions.env`](versions.env).

## What differs from the cloud platform

| | Mac (this) | Cloud (later) |
|---|---|---|
| CPU | arm64 (Apple Silicon) | amd64 |
| VMs | arm64 guests; emulated, or hardware-accelerated with Colima nested virtualization on M3+ | amd64 guests with KVM |
| Firewall VMs (OPNsense, pfSense, VyOS) | not available (x86-only); `dev-lab` uses FRR as its router | available |
| Storage | kind's local-path (no snapshots) | Longhorn or Rook-Ceph with CSI snapshots |
| Registry, NetBox, OpenBao | not yet | yes |

`XFOUNDRY_ARCH` tells xFoundry which platform it is rendering for. `preflight.sh` writes it to `platform/dev/.arch`.

## 1. Container runtime: Colima (recommended)

Kube-OVN needs the `openvswitch` kernel module, which Colima's Ubuntu VM has. Docker Desktop's VM may not have it; `preflight.sh` checks.

```sh
brew install colima docker kind kubectl virtctl
# M3 or newer, macOS 15+: hardware-accelerated VMs
colima start --cpu 6 --memory 12 --disk 60 --vm-type vz --nested-virtualization
# M1/M2: VMs run in emulation (slower, still works)
colima start --cpu 6 --memory 12 --disk 60 --vm-type vz
```

If Docker Desktop is also installed, `docker context use colima` makes sure kind uses Colima.

## 2. Check, then bring the platform up

```sh
platform/dev/preflight.sh      # changes nothing; tells you what is missing
platform/dev/up.sh             # 10–20 minutes the first time; safe to run again
```

`up.sh` creates the kind cluster `xfoundry` and installs Kube-OVN, Multus, KubeVirt (emulation switched on only when there's no `/dev/kvm`) and CDI. It also applies the read-only collector identity.

## 3. The loop

Nirvana runs as usual (`http://localhost:3000`). Create a token under Account › Service tokens.

```sh
source platform/dev/.arch                       # XFOUNDRY_ARCH=arm64
export NIRVANA_URL=http://localhost:8000 NIRVANA_TOKEN=nvt_...
export XFOUNDRY_KUBE_CONTEXT=kind-xfoundry
export NIRVANA_DIR=~/path/to/Nirvana            # wherever your Nirvana checkout is

platform/dev/snapshot.sh                        # writes platform/dev/dev.snapshot.json
xfoundry plan examples/dev-lab.yaml --snapshot platform/dev/dev.snapshot.json
#   → review and approve in Nirvana (Changes)
platform/dev/snapshot.sh                        # fresh snapshot for the gate
xfoundry apply examples/dev-lab.yaml --snapshot platform/dev/dev.snapshot.json
```

Then watch it come up:

```sh
kubectl -n xf-dev-lab get pods,vm,vmi,dv
virtctl console -n xf-dev-lab app-01            # CirrOS login: cirros / gocubsgo
```

After that, take a snapshot again and plan the same spec: Nirvana should show every object as unchanged, against a collected snapshot with no Modelled badge.

Day 2:
- **Scale down:** set `count: 1`, then `xfoundry plan --action prune` and `xfoundry apply --action prune` (Nirvana flags the disk that will be lost).
- **Remove everything:** `--action destroy`.

## 4. What to check once it's running

- **Both routers.** On each subnet, OVN's router sits on `.254`; the FRR pod `rt01` holds `.1`, and DHCP gives the VMs `.1` as their default route. In a VM: `ip route` should show `default via 10.70.20.1`.
- **Static addresses.** `lb01` has `10.70.10.10`: `kubectl -n xf-dev-lab get ips`.
- **HAProxy.** `kubectl -n xf-dev-lab port-forward deploy/lb01 8404` and open `http://localhost:8404/stats`.

Known gap: in Kube-OVN's default VPC, traffic between two environment subnets can also go through OVN's own router, so `rt01` is not yet forced into the path. An xFoundry VPC per environment with policy routes is the next networking step ([ADR 0003](../../docs/adr/0003-local-dev-platform.md)).

## Troubleshooting

- **Kube-OVN pods crash with openvswitch errors:** the runtime VM has no `openvswitch` module. Switch to Colima.
- **VMs stuck in `Scheduling`:** the runtime VM is short on memory; give Colima 12 GiB.
- **VMs slow to boot:** expected in emulation; CirrOS takes a minute or two.
- **`DataVolume` stuck importing:** check `kubectl -n xf-dev-lab logs -l app=containerized-data-importer`; the image must have an arm64 variant (preflight checks the defaults).
- **Start over:** `platform/dev/down.sh`, then `up.sh`.
