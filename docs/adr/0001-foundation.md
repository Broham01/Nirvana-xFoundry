# ADR 0001: xFoundry foundation

Date: 2026-10-20 · Accepted.

## Context

Nirvana plans changes and never executes them; that is its main trust argument. Something still has to build images, create, scale and destroy environments. VMware licensing makes vSphere the wrong base for that. The appliances worth testing against (OPNsense, pfSense, VyOS) are full operating systems, and some (FreeBSD-based) cannot run as containers.

## Decision

- **Separate app, separate repo, separate trust boundary.** xFoundry holds cluster credentials and executes. Nirvana keeps none and executes nothing. xFoundry never approves its own changes.
- **Kubernetes for everything, KubeVirt for VMs.** One control plane for containers and VMs. Platform stack, to be stood up in phase 0:
  - OS and Kubernetes: Talos Linux.
  - VMs: KubeVirt + CDI.
  - Storage: Rook-Ceph or Longhorn; CSI VolumeSnapshots are required.
  - Networking: Kube-OVN (Open vSwitch underneath) with Multus for extra interfaces and VLANs.
  - Delivery: Argo CD (GitOps), Argo Workflows + Argo Events (triggers, image builds), Harbor (registry for container and disk images).
  - Source of truth: NetBox.
  - Configuration: Ansible first, Nornir where Python logic is needed.
  - Secrets: OpenBao.
- **One spec shared with Nirvana:** `xfoundry.environment/1` ([docs/SPEC.md](../SPEC.md)).
- **Every change is planned in Nirvana and gated** ([ADR 0002](0002-nirvana-integration.md)).
- **Python**, so contract code can be shared with Nirvana's backend. The CLI comes first; a kopf operator watching `Environment` resources replaces it in phase 2.
- **Appliance choices:** OPNsense as the default firewall (REST API, Ansible collection); pfSense CE only to mirror sites that run it. VyOS for router VMs and FRR for in-cluster BGP. HAProxy for F5-style load balancing and Traefik with Gateway API for ingress; the retired community ingress-nginx is not used. NGINX only as a workload.
- **Cluster API on KubeVirt (CAPK)** comes after vcluster. The spec reserves `provider: capk`, and the validator refuses it until it is built.

## Consequences

- KubeVirt objects are custom resources to Nirvana's planner, planned at `partial` confidence until Nirvana gains KubeVirt rules.
- Appliance disk images must be built (Packer) and kept in Harbor before the first real environment boots. The catalogue's `:stable` tags are placeholders until the image factory pins digests.
- Fixed interface addresses mean appliance Deployments use the Recreate strategy: two pods must never hold one IP.
