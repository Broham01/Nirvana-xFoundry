# Dev platform on a Mac

A one-node Kubernetes cluster on your Mac with the same stack the cloud platform will run: Kube-OVN (Open vSwitch), Multus, KubeVirt and CDI. It's for getting the whole loop working (spec → Nirvana plan → approval → apply → real VMs and appliances) before paying for cloud servers.

Versions are pinned in [`versions.env`](versions.env).

## What differs from the cloud platform

| | Mac (this; its own Colima VM) | Cloud (later) |
|---|---|---|
| CPU | arm64 (Apple Silicon) | amd64 |
| VMs | arm64 guests; emulated, or hardware-accelerated with Colima nested virtualization on M3+ | amd64 guests with KVM |
| Firewall VMs (OPNsense, pfSense, VyOS) | not available (x86-only); `dev-lab` uses FRR as its router | available |
| Storage | kind's local-path (no snapshots) | Longhorn or Rook-Ceph with CSI snapshots |
| Registry, NetBox, OpenBao | not yet | yes |

`XFOUNDRY_ARCH` tells xFoundry which platform it is rendering for. `preflight.sh` writes it to `platform/dev/.arch`.

## 1. Runs beside Docker Desktop and Nirvana

The dev platform gets **its own Linux VM**: a Colima instance named `xfoundry`. Docker Desktop keeps running Nirvana exactly as it does today, with the same containers, Postgres on `54329`, and ports `3000`, `8000` and `9001`.

Nothing global changes:
- **Docker:** the scripts send commands to xFoundry's VM through `DOCKER_CONTEXT`, for their own process only. `docker context use` is never run, so your `docker` still talks to Docker Desktop.
- **Kubernetes:** the cluster's credentials live in `platform/dev/.kubeconfig`, not `~/.kube/config`. Your default `kubectl` context is unchanged.
- **Memory:** this is the only shared resource. Docker Desktop needs only what Nirvana uses; the xFoundry VM takes 10 GiB by default. Set `XFOUNDRY_VM_MEMORY` (and `XFOUNDRY_VM_CPUS`) before `up.sh` to change that. `stop.sh` gives the memory back at any time.

Why a separate VM: Kube-OVN needs the `openvswitch` kernel module, and Docker Desktop's VM refuses to load it (`modprobe: Operation not permitted`). Colima's Ubuntu VM loads it. On an M3 or newer Mac with macOS 15+, Colima also gives the VM nested virtualization, so KubeVirt VMs run at hardware speed.

```sh
brew install colima docker kind kubectl virtctl
```

## 2. Check, then bring the platform up

**Shortest path:** `platform/dev/setup-mac.sh` installs what's missing (Colima, kind, kubectl), offers to remove the old cluster an earlier attempt left in Docker Desktop, then runs `up.sh`. Or run the steps yourself:

```sh
platform/dev/preflight.sh      # changes nothing; shows memory for Docker Desktop, xFoundry's VM and macOS
platform/dev/up.sh             # starts xFoundry's VM if needed, then the cluster; 10–20 min the first time; safe to re-run
platform/dev/stop.sh           # pause: frees the VM's memory, keeps the cluster
platform/dev/down.sh           # delete the cluster (and optionally the VM); asks first
```

`up.sh` does the following:
1. starts the VM, trying nested virtualization first;
2. loads the `openvswitch` module (the VM forgets it on every restart);
3. creates or restarts the kind cluster `xfoundry`;
4. installs Kube-OVN, Multus, KubeVirt and CDI;
5. applies the collector's read-only identity;
6. writes `platform/dev/env.sh` for your shell.

## 3. The loop

Nirvana runs as usual (`http://localhost:3000`). Create a token under Account › Service tokens.

```sh
source platform/dev/env.sh                      # this shell only: DOCKER_CONTEXT, KUBECONFIG, XFOUNDRY_ARCH, kube context
export NIRVANA_URL=http://localhost:8000 NIRVANA_TOKEN=nvt_...
export NIRVANA_DIR=~/path/to/Nirvana            # wherever your Nirvana checkout is

xfoundry run examples/dev-lab.yaml
```

`run` does the whole loop:
1. snapshots the cluster with Nirvana's collector;
2. submits the plan;
3. waits (up to 30 minutes) while you review and approve it in Nirvana › Changes;
4. snapshots again;
5. applies only if the gate passes.

If the plan is sent back, nothing changes. If the cluster changed while you were reviewing, nothing changes and it asks you to re-plan.

The same steps by hand: `xfoundry collect`, `xfoundry plan --snapshot`, `xfoundry status`, `xfoundry apply --snapshot`.

Then watch it come up:

```sh
kubectl -n xf-dev-lab get pods,vm,vmi,dv
virtctl console -n xf-dev-lab app-01            # CirrOS login: cirros / gocubsgo
```

After that, take a snapshot again and plan the same spec: Nirvana should show every object as unchanged, against a collected snapshot with no Modelled badge.

Day 2:
- **Scale down:** set `count: 1`, then `xfoundry run examples/dev-lab.yaml --action prune` (Nirvana flags the disk that will be lost).
- **Remove everything:** `xfoundry run examples/dev-lab.yaml --action destroy`.

## 4. What to check once it's running

- **Both routers.** On each subnet, OVN's router sits on `.254`; the FRR pod `rt01` holds `.1`, and DHCP gives the VMs `.1` as their default route. In a VM: `ip route` should show `default via 10.70.20.1`.
- **Static addresses.** `lb01` has `10.70.10.10`: `kubectl -n xf-dev-lab get ips`.
- **HAProxy.** `kubectl -n xf-dev-lab port-forward deploy/lb01 8404` and open `http://localhost:8404/stats`.

- **Isolation.** The environment has its own VPC: `kubectl get vpc xf-dev-lab -o yaml` shows two `reroute` policies, one each way through `rt01`. The VMs cannot reach pods on the cluster network or another environment's subnets.

Nirvana shows the VPC as "1 not verified" because its planner doesn't know the `Vpc` kind yet ([ADR 0003](../../docs/adr/0003-local-dev-platform.md)). The plan is still complete and can be approved.

## Troubleshooting

- **`ovs-ovn` in CrashLoopBackOff with `modprobe: Operation not permitted`:** the `openvswitch` module isn't loaded in xFoundry's VM; it is forgotten whenever the VM restarts. Run `up.sh` again: it loads the module and restarts the pod.
- **`kubectl` doesn't see the cluster:** run `source platform/dev/env.sh` in that shell first. The cluster isn't in `~/.kube/config` on purpose.
- **Nirvana or Docker Desktop seem affected:** they shouldn't be. Check that `env -u DOCKER_CONTEXT docker context show` prints `desktop-linux`. The scripts never change it.
- **VMs stuck in `Scheduling`:** xFoundry's VM is short on memory. Recreate it bigger: `down.sh` (say yes to deleting the VM), then `XFOUNDRY_VM_MEMORY=12 platform/dev/up.sh`.
- **VMs slow to boot:** expected in emulation; CirrOS takes a minute or two.
- **`DataVolume` stuck importing:** check `kubectl -n xf-dev-lab logs -l app=containerized-data-importer`; the image must have an arm64 variant (preflight checks the defaults).
- **Start over:** `platform/dev/down.sh`, then `up.sh`. Docker Desktop is never touched.
