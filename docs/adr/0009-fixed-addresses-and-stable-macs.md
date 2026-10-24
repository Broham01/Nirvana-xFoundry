# ADR 0009: Fixed VM addresses and stable MACs

Date: 2026-10-24 · Accepted. Part of xStage round 1 (xStage ADR 0022).

A domain controller, a DNS server or anything others point at needs the same address every time it is built, and some licences and DHCP reservations need the same MAC.

- **`vms[].addresses`:** `{network: first address}`. The group's first VM takes that address and each next VM the one after it, rendered as Kube-OVN's per-attachment `ip_address` annotation (the same mechanism appliances use). The addresses join the subnet's `excludeIps`, so DHCP never hands them to another VM.
- **Checks:** every address must fit in the network, and must not be the gateway, an excluded address, or used by an appliance or another group. The group must use the network.
- **`vms[].stable_mac`:** each card gets `02:` plus five bytes of SHA-256 over `environment/vm/network`, a locally administered unicast MAC that is the same on every rebuild.
- **Unchanged without the options:** existing specs render byte for byte as before, so no environment restarts because of this change.
