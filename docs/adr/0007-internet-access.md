# ADR 0007: Internet access for environment networks

Date: 2026-10-24 · Accepted.

## Problem

Each environment is its own Kube-OVN VPC with no way out. That is the right default for a lab, but a VM could not install updates or packages: `apt` waited forever on "Connecting to ports.ubuntu.com", and cloud-init's `packages` could not install.

## Decision

A network can ask for `internet: true` (optional, default false; `xfoundry.environment/1` stays `/1`). When any network does, the render adds:

- **An egress gateway:** a Deployment `egress` in the environment's namespace. Its primary interface is on the cluster network, which already reaches the internet through each node's NAT. One Multus interface goes on the first internet network, at that network's highest free address; the validator reserves it.
  - The gateway masquerades out of `eth0` and forwards outbound only: from the cluster side, only replies come in.
  - It drops anything addressed to private, carrier-grade NAT or link-local ranges (`XFOUNDRY_EGRESS_BLOCK`). So "internet" means the public internet: the cluster, the platform and your home or office network stay out of reach.
  - It uses Kube-OVN's image (`XFOUNDRY_EGRESS_IMAGE`), which has iptables and is already on every node.
- **VPC policy routes at priority 29000:** traffic from each internet network is rerouted to the gateway. That is below the existing rules (31000 reroute between joined networks, 30000 drop between the rest), so traffic between the environment's own networks is unchanged.
- **DHCP:**
  - internet networks hand out a DNS server (`XFOUNDRY_EGRESS_DNS`, default 1.1.1.1);
  - their default route stays the VPC router, even where a router or firewall appliance holds the network's gateway address;
  - traffic to other networks still goes through that appliance by policy route.

  So internet traffic does not pass through a firewall appliance. Routing the internet through OPNsense (with its own WAN) is a later step.
- **No VLAN networks:** a VLAN network reaches the outside through its own underlay, so `internet: true` is refused there.

## Why not Kube-OVN's VpcEgressGateway or VPC NAT gateway

Both need an external network attachment (usually macvlan on a node NIC with addresses from the physical network). On the dev platform the node is a kind container inside Colima, and there is no such network to give. The pod above uses only what every cluster already has. On the cloud platform, a VpcEgressGateway with a real external network can replace it later without changing the spec.

## Consequences

- Turning internet access on or off is an ordinary spec change: a plan shows the new Deployment and the VPC routing change ("VPC routing change" in xStage's planner), and a person approves it.
- VMs that are already running get the new route at once; they pick up the DNS server when they renew their DHCP lease (within an hour) or restart.
- `XFOUNDRY_EGRESS_*` must be the same at plan and apply, like `XFOUNDRY_ARCH`, or the manifest hash differs and the gate refuses.
