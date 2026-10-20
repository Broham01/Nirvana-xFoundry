# Roadmap

Order of work. Each step ends with something that works end to end.

## Done (2026-10-20)

- Shared spec `xfoundry.environment/1`, validator, renderer, Nirvana model projections, apply gate, CLI, tests.
- Compatibility run against Nirvana's real Kubernetes planner.

## 1. Nirvana service token and planner fixes: done, merged

[Nirvana PR #1](https://github.com/Broham01/Nirvana/pull/1) (Nirvana ADR 0013):
- service tokens that can never approve;
- KubeVirt, Kube-OVN and Multus modelled;
- cluster-scoped custom resources;
- namespace annotations.

Merged 2026-10-20 (`1403379`). Update the Mac from inside the app; the update runs migration 0009.

## 2a. Dev platform on the Mac (now)

[platform/dev](../platform/dev/README.md): kind on Colima with Kube-OVN, Multus, KubeVirt and CDI ([ADR 0003](adr/0003-local-dev-platform.md)). `examples/dev-lab.yaml` (FRR, HAProxy, CirrOS VMs) runs the whole loop on arm64.

**Exit:** `dev-lab` applied through an approved Nirvana plan; VMs boot and route through `rt01`; a re-plan against a collected snapshot shows no changes.

Next networking step: a Kube-OVN VPC per environment, so traffic between subnets must go through the router or firewall.

## 2b. Phase 0: cloud platform cluster

amd64 servers with KVM. Start with one node to prove it, then three.

- Talos Linux; Kubernetes pinned.
- KubeVirt + CDI.
- Storage with CSI snapshots: Longhorn on one node, Rook-Ceph at three or more.
- Kube-OVN + Multus, with an underlay ProviderNetwork for VLANs.
- Argo CD (app-of-apps in `platform/`), Harbor, NetBox, OpenBao.
- In-cluster collector: a CronJob running Nirvana's `collect-k8s-snapshot.py` with the read-only identity in [`platform/collector-rbac.yaml`](../platform/collector-rbac.yaml) and the `--custom` list there, pushing to Nirvana with a service token.

**Exit:** `xfoundry plan --snapshot` on the real, empty platform shows 0 gaps.

## 3. First joint milestone: edge-lab, for real

1. `xfoundry plan examples/edge-lab.yaml --snapshot <collected>`.
2. Approve in Nirvana.
3. `xfoundry apply`.
4. The OPNsense VM boots, Ansible pushes its config from NetBox, and the app VMs reach the DMZ through it.
5. Collect again: Nirvana shows the environment with no Modelled badge.

## 4. Nirvana Kubernetes models from this spec

ADR 0002 item 6: Nirvana ADR 0012 step 3 imports `xfoundry.environment/1`, so a Nirvana model can be built by xFoundry.

## 5. Image factory (the CI half)

- Argo Events triggers: git push, Harbor webhook, nightly patch schedule, upstream release feeds.
- Packer builds disk images (OPNsense, VyOS, Ubuntu, Windows); BuildKit builds container images.
- Trivy scan and cosign signature; push to Harbor; pin digests in `images.lock.json`, replacing the catalogue's `:stable` tags.
- Smoke test: boot the new image in a throwaway environment, collect, and plan a test change in Nirvana. Promote only on approval.

## 6. Operator

A kopf operator watching `Environment` custom resources (the same spec) replaces the CLI loop:
- status conditions (Planned, Approved, Applied, Drifted);
- a TTL reaper;
- quotas per owner;
- VM snapshots before every change, for rollback (KubeVirt `VirtualMachineSnapshot`).

## 7. Network domain

- Ansible check mode with diff, run by xFoundry (which has access) against appliances; the output goes to Nirvana as plan input. Nirvana stays connection-free.
- Batfish fed from the configs xFoundry holds.

## 8. Cluster API on KubeVirt

`provider: capk` for full nested clusters with real nodes; scaling through MachineDeployment replicas.

## 9. Scenario fixtures

Make Nirvana's ADR 0012 scenarios real (a node cordoned, half the VMs off), apply in a throwaway environment, and compare what happened with what Nirvana predicted. This measures planner accuracy.
