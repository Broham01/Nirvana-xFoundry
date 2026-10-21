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

## 3. From the browser (the console)

Once the platform is up, everything below can be done from Nirvana xStage's **Environments** page instead of a terminal ([ADR 0004](../../docs/adr/0004-worker.md)).

1. In xStage: **Account › Service tokens › Create**. Copy the token (it is shown once).
2. Double-click **Set xFoundry token.command** (in the xFoundry folder) and paste it there. It goes into your Keychain, not a file. Once only.
3. Double-click **Start xFoundry worker.command**. Leave the window open; it shows what the worker does. The Environments page shows "Worker … online".
4. In xStage: **Environments › New environment**. Paste `examples/dev-lab.yaml` (or edit the template), then **Save and plan it**.
5. The job appears and, a few seconds later, says **Waiting for your approval**. **Review the plan**, check any high findings and approve. The worker applies it through the gate and shows what is running.

Scaling: edit `count` in the spec, **Save spec**, **Apply spec changes** (adds VMs); after lowering a count, **Remove scaled-down objects** (deletes the extra VMs). **Destroy** removes everything, again only after you approve its plan. **Check** validates the spec and refreshes the status without planning anything.

Closing the worker window stops it; a job waiting for approval carries on when you start it again. The CLI below still works, and both use the same ledger.

## 4. The loop from a terminal

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

## 5. Check that it works (the exit test)

```sh
platform/dev/verify.sh             # appliances ready, VMs Running, a routed ping from the load balancer to each VM
```

The ping from `lb01` (front subnet) to each VM (app subnet) only succeeds if:
- `rt01` forwards between its interfaces (`net.ipv4.ip_forward=1`, which `up.sh` lets the kubelet allow);
- `lb01` routes the app subnet through `rt01` (a Kube-OVN per-interface route xFoundry adds);
- the VM got `rt01` as its default route from DHCP.

Then plan the same spec again. Nirvana should show every object unchanged, against a collected snapshot with no Modelled badge:

```sh
xfoundry collect -o fresh.snapshot.json
xfoundry plan examples/dev-lab.yaml --snapshot fresh.snapshot.json
```

Other things worth a look:
- OVN's router sits on `.254` of each subnet, and `rt01` holds `.1`: `kubectl get subnet xf-dev-lab-app -o yaml`.
- The VPC reroutes traffic between the subnets through `rt01`: `kubectl get vpc xf-dev-lab -o yaml`.
- HAProxy stats: `kubectl -n xf-dev-lab port-forward deploy/lb01 8404`, then open `http://localhost:8404/stats`.

## 6. Day 2: scale down, then tear down

Each change goes through Nirvana like the first one.

1. **Scale down:** in `examples/dev-lab.yaml` set the VM group's `count: 1`, then:
   ```sh
   xfoundry run examples/dev-lab.yaml --action prune    # Nirvana flags app-02's disk as lost; approve to delete it
   xfoundry run examples/dev-lab.yaml                   # brings the rest in line with the spec (usually no changes)
   ```
2. **Tear down:** `xfoundry run examples/dev-lab.yaml --action destroy`. Nirvana lists everything that will be deleted.

`platform/dev/stop.sh` pauses the whole platform; `up.sh` brings it back with the environment intact.

## Troubleshooting

- **`ovs-ovn` in CrashLoopBackOff with `modprobe: Operation not permitted`:** the `openvswitch` module isn't loaded in xFoundry's VM; it is forgotten whenever the VM restarts. Run `up.sh` again: it loads the module and restarts the pod.
- **`kubectl` doesn't see the cluster:** run `source platform/dev/env.sh` in that shell first. The cluster isn't in `~/.kube/config` on purpose.
- **Nirvana or Docker Desktop seem affected (for example Nirvana's update fails at Tests):** check that `env -u DOCKER_CONTEXT docker context show` prints `desktop-linux`. Colima switches the default when it starts unless told not to. `start.sh` passes `--activate=false` and switches back if an earlier start changed it. To fix by hand: `docker context use desktop-linux`.
- **Pods stuck in `ContainerCreating` with `multus-shim ... failed to send CNI request: EOF`:** Multus's daemon ran out of memory. Its upstream manifest allows only 50Mi; `up.sh` raises it to 512Mi. On a cluster made before that fix, run `up.sh` again, and the stuck pods recover by themselves.
- **VMs stuck in `Scheduling`:** xFoundry's VM is short on memory. Recreate it bigger: `down.sh` (say yes to deleting the VM), then `XFOUNDRY_VM_MEMORY=12 platform/dev/up.sh`.
- **VMs slow to boot:** expected in emulation; CirrOS takes a minute or two.
- **`DataVolume` stuck importing:** check `kubectl -n xf-dev-lab logs -l app=containerized-data-importer`; the image must have an arm64 variant (preflight checks the defaults).
- **Start over:** `platform/dev/down.sh`, then `up.sh`. Docker Desktop is never touched.
