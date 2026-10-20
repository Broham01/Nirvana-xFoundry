# ADR 0003: Local dev platform first, then cloud

Date: 2026-10-20 · Accepted.

## Context

The goal is a cloud platform (amd64 servers with KVM). Before paying for servers, the whole loop should work end to end on a Mac (Apple Silicon): spec → Nirvana plan → approval → gate → apply → running VMs and appliances.

## Decision

- **Dev platform:** kind in **xFoundry's own Colima VM** (instance `xfoundry`), beside Docker Desktop, with the same networking and VM stack as the cloud: Kube-OVN, Multus, KubeVirt and CDI, all pinned in `platform/dev/versions.env`.
  - Docker Desktop can't host it: its VM refuses to load the `openvswitch` module, which was found on the first real run.
  - The scripts never change global state: Docker commands go through a per-process `DOCKER_CONTEXT`, and the cluster has its own kubeconfig (`platform/dev/.kubeconfig`). Nirvana, its containers and the default `kubectl` context are untouched.
  - `up.sh` is idempotent and restarts a stopped VM and cluster; `stop.sh` frees the VM's memory.
  - `preflight.sh` checks tools, the memory split between macOS, Docker Desktop and the VM, the kernel module, `/dev/kvm` and arm64 images.
- **`XFOUNDRY_ARCH`** (amd64 or arm64) is a platform property:
  - it decides which appliances are allowed (OPNsense, pfSense and VyOS are amd64-only);
  - it sets the VM `architecture` and the CDI import `platform.architecture`.
  
  It is part of the rendered manifest, so plan and apply must use the same value or the gate refuses.
- **Images are upstream references**, optionally pulled through `XFOUNDRY_MIRROR`; tags checked on 2026-10-20 (HAProxy 3.4.7 LTS, FRR 10.7.1, Traefik v3.7.15, NGINX 1.30.6 stable). The image factory pins digests later.
- **Routers and gateways on Kube-OVN overlay subnets.** OVN's logical router owns a subnet's gateway address. When an appliance claims the spec's `gateway`, xFoundry:
  - moves OVN's gateway to the highest free address;
  - turns on DHCP with `dhcpV4Options: router=<appliance address>`, so VMs route through the appliance.
  
  Every appliance address is put in `excludeIps`: Kube-OVN still honours static addresses inside it (`pkg/ipam/subnet.go`), and dynamic VMs can never take them.
- **Appliance containers start usable:**
  - HAProxy gets a starter ConfigMap (health and stats on :8404) until Ansible configures it;
  - FRR gets the `NET_ADMIN`, `NET_RAW` and `SYS_ADMIN` capabilities.

## Consequences

- `dev-lab` (FRR router, HAProxy, two CirrOS VMs) plans in Nirvana with every object modelled and 0 gaps. A re-apply against the built model "looks clean", and scaling down flags the lost disk.
- **Not proven yet:** a real run on the Mac. This workspace cannot reach Docker Hub or Quay, so the scripts are linted and the manifests are planned, but no cluster was booted here.
- **Isolation (added 2026-10-20):** each environment gets its own Kube-OVN `Vpc` (`xf-<name>`), and its subnets join it.
  - Environments are cut off from the cluster network and from each other.
  - Traffic between two of an environment's networks is rerouted (priority 31000) to the router or firewall that joins them.
  - Networks that no router or firewall joins are dropped from each other (priority 30000).
  - Load balancers never route.
  - Kube-OVN v1.16.14 supports attachment subnets in a custom VPC: the pod controller sets the logical router per attachment from `subnet.Spec.Vpc`.
  - Pods keep their primary interface on the cluster network, for image pulls and DNS.
- **Nirvana follow-up (proposed, not made):** Nirvana's planner treats `kubeovn.io/v1 Vpc` as a custom resource with `partial` confidence, so a create plan reads "1 not verified". The plan is still complete and can be approved. Adding `Vpc` to the planner's known network kinds, with a finding when policy routes change, would close it. Nirvana is left untouched while other work happens there.
- **Cloud later (amd64):** Talos on servers with KVM. That brings back OPNsense and VyOS, and adds CSI snapshots, Harbor, NetBox and OpenBao. Kube-OVN ships Talos support (`talos/` in its repository at the pinned tag).
