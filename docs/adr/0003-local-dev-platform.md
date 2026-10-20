# ADR 0003: Local dev platform first, then cloud

Date: 2026-10-20 · Accepted.

## Context

The goal is a cloud platform (amd64 servers with KVM). Before paying for servers, the whole loop should work end to end on a Mac (Apple Silicon): spec → Nirvana plan → approval → gate → apply → running VMs and appliances.

## Decision

- **Dev platform:** kind on Colima (Ubuntu VM: has the `openvswitch` module; nested virtualization on M3+), with the same networking and VM stack as the cloud: Kube-OVN, Multus, KubeVirt and CDI, all pinned in `platform/dev/versions.env`. `up.sh` is idempotent, and `preflight.sh` checks tools, resources, kernel modules, `/dev/kvm` and arm64 images before anything is created.
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
- **Known gap:** in Kube-OVN's default VPC, environment subnets are also joined by OVN's router, so traffic between them can bypass the appliance. Next: a Kube-OVN `Vpc` per environment with policy routes through the router or firewall.
- **Cloud later (amd64):** Talos on servers with KVM. That brings back OPNsense and VyOS, and adds CSI snapshots, Harbor, NetBox and OpenBao. Kube-OVN ships Talos support (`talos/` in its repository at the pinned tag).
