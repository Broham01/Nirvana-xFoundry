# The shared spec: `xfoundry.environment/1`

One environment, described once. **xFoundry builds it. Nirvana models it and plans against it.**
Schema: [`contracts/environment.schema.json`](../contracts/environment.schema.json). Example: [`examples/edge-lab.yaml`](../examples/edge-lab.yaml).

## Why one spec

Nirvana's ADR 0012 step 3 (Kubernetes models) and xFoundry's environment definition describe the same thing: what an environment contains. Two formats would drift apart. With one:

- A Nirvana model can be **built for real** by xFoundry, then collected, which removes the Modelled badge the honest way (ADR 0012 evidence rule).
- Every environment xFoundry builds can be **modelled first** in Nirvana, before any hardware is spent.
- VM names follow the same rule in both apps (`PRD-WEB{nn}` × 3 → `PRD-WEB01..03`), so objects match one to one.

## Sections

| Section | Means | Becomes in Kubernetes |
|---|---|---|
| `name`, `owner`, `description` | identity | Namespace `xf-<name>` |
| `lifecycle.ttl`, `lifecycle.keep` | when it is reaped | namespace annotations the reaper reads |
| `rules.production` | treat as production | namespace label `environment=production` (Nirvana's planner reads this) |
| `rules.protect_appliances` | appliances need care | `nirvana.io/protected=true` (Nirvana shows Criticality/Critical) |
| `networks[]` | subnets, optional VLAN, gateway, excluded ranges | Kube-OVN `Subnet` (+ `Vlan`), Multus `NetworkAttachmentDefinition` |
| `appliances[]` | firewall, router, load balancer with fixed interface addresses | KubeVirt `VirtualMachine` (OPNsense, pfSense, VyOS) or `Deployment` (HAProxy, FRR, Traefik, NGINX) |
| `vms[]` | groups of VMs by name pattern, count, image, size, power, tags | one KubeVirt `VirtualMachine` per name |
| `vms[].cloud_init` | first boot of cloud images: one sudo user, SSH public keys, an optional SHA-512 crypt `password_hash` (never a plain password) for the console, packages | a `cloudInitNoCloud` disk with user data, the same at plan and apply |
| `clusters[]` | nested Kubernetes | Argo CD `Application` installing a vcluster (Cluster API on KubeVirt: later) |

The `lifecycle.ttl` reaper is xStage (ADR 0020 there): when it runs out, xStage queues a destroy job, which a person approves like any other.

Appliance configuration (`config.source: netbox`) is pushed by Ansible after the VM boots, from the NetBox device named in `config.ref`. It is not part of the rendered manifest, so it gets its own plan later (Ansible check mode, ADR 0002).

## Rules the validator enforces beyond the schema

Networks referenced must exist; addresses must sit inside their subnet and be unique; subnets must not overlap; VLAN IDs unique; product and role must agree (OPNsense is a firewall, HAProxy a load balancer); no two objects may end up with the same Kubernetes name; at most 500 VMs per environment.

## Projections into Nirvana

`xfoundry model SPEC --stage before|built` writes `nirvana.snapshot/1` with `source.kind: "model"`, `source.domain: "kubernetes"` and the spec under `model.spec`, the same convention as Nirvana's vSphere model wizard:

- **before**: the platform cluster with KubeVirt, Kube-OVN, Multus and Argo CD CRDs installed and no environment. Planning the rendered manifest against it reviews "create this environment".
- **built**: the environment as it will look once applied, with `last-applied-configuration` on every object. Plan day-2 changes (scale, resize, delete) against it.

## Compatibility, checked against Nirvana `af9b5fb`

The rendered `edge-lab` manifest planned with Nirvana's real Kubernetes planner: create = 11 actions, 0 gaps; re-apply against the built model = no-ops for every namespaced object; firewall resize = an update on `fw01`; destroy = every object plus "deletes the namespace and everything in it". Three Nirvana fixes came out of it (cluster-scoped custom resources, namespace annotations, KubeVirt rules), now built in Nirvana PR #1. With them, a re-apply is a no-op on every object, VMs are fully modelled, and the create plan reads "Nothing unverified". See [ADR 0002](adr/0002-nirvana-integration.md).

## Versioning

`format` is the version. Adding optional fields keeps `/1`. Renaming, removing or changing meaning bumps to `/2`, and both apps must read both until the old one is retired. The schema file is the source of truth; Nirvana should vendor it rather than copy it.
