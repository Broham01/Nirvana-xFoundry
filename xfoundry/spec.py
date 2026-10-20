"""xfoundry.environment/1: load, validate and expand an environment spec.

The schema (contracts/environment.schema.json) checks shapes; this module checks what ties the parts together
(networks referenced, addresses inside their subnet, no duplicates) and expands VM groups into names exactly the
way Nirvana's model wizard does (PRD-WEB{nn} x 3 -> PRD-WEB01..03), so both apps agree on every object name.
"""
from __future__ import annotations

import ipaddress
import json
import re
from functools import lru_cache
from pathlib import Path

import jsonschema
import yaml

from . import catalog

FORMAT = 'xfoundry.environment/1'
SCHEMA_PATH = Path(__file__).resolve().parent.parent / 'contracts' / 'environment.schema.json'
NUMBER = re.compile(r'\{(n+)\}')
MAX_VMS = 500
VM_PRODUCTS = {'opnsense', 'pfsense', 'vyos'}           # full operating systems: KubeVirt VMs
POD_PRODUCTS = {'haproxy', 'frr', 'traefik', 'nginx'}   # containers: Deployments with extra interfaces


class SpecError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__('; '.join(problems))


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def names(pattern: str, count: int) -> list[str]:
    """Same rule as Nirvana's model_builder.names: {nn} pads, no placeholder gives name, name-2, name-3."""
    if NUMBER.search(pattern):
        return [NUMBER.sub(lambda m: str(i).zfill(len(m.group(1))), pattern) for i in range(1, count + 1)]
    return [pattern if i == 1 else f'{pattern}-{i}' for i in range(1, count + 1)]


def k8s_name(display: str) -> str:
    """Kubernetes names are lower-case DNS labels; the original name is kept in an annotation."""
    out = re.sub(r'[^a-z0-9-]+', '-', display.lower()).strip('-')
    return out[:63].rstrip('-') or 'vm'


def powered(power: str, index: int, count: int) -> bool:
    if power == 'all': return True
    if power == 'none': return False
    return index < (count + 1) // 2   # half: the first half (rounded up) run


def load(path: str | Path) -> dict:
    text = Path(path).read_text(encoding='utf-8')
    data = yaml.safe_load(text) if str(path).endswith(('.yaml', '.yml')) else json.loads(text)
    return check(data)


def _range(network: ipaddress.IPv4Network, item: str) -> set[ipaddress.IPv4Address]:
    if '..' in item:
        a, b = (ipaddress.IPv4Address(x.strip()) for x in item.split('..', 1))
        return {ipaddress.IPv4Address(i) for i in range(int(a), int(b) + 1)}
    return {ipaddress.IPv4Address(item)}


def check(data: dict) -> dict:
    """Validate and return the spec with defaults filled in. Raises SpecError listing every problem found."""
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        raise SpecError([f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors])
    spec = json.loads(json.dumps(data))  # deep copy
    problems: list[str] = []
    spec.setdefault('lifecycle', {})
    spec['rules'] = {'production': False, 'protect_appliances': True, 'keep_words': [], **spec.get('rules', {})}

    nets: dict[str, ipaddress.IPv4Network] = {}
    used: dict[str, set] = {}
    for n in spec['networks']:
        if n['name'] in nets: problems.append(f"Two networks are called {n['name']}."); continue
        try:
            net = ipaddress.IPv4Network(n['cidr'], strict=True)
        except ValueError as exc:
            problems.append(f"Network {n['name']}: {exc}."); continue
        if net.num_addresses < 8: problems.append(f"Network {n['name']} is smaller than a /29."); continue
        nets[n['name']] = net
        gw = n.setdefault('gateway', str(next(net.hosts())))
        if ipaddress.IPv4Address(gw) not in net: problems.append(f"Network {n['name']}: gateway {gw} is outside {net}.")
        reserved = {ipaddress.IPv4Address(gw)}
        for item in n.get('exclude', []):
            try: reserved |= _range(net, item)
            except ValueError: problems.append(f"Network {n['name']}: cannot read exclude entry {item!r}.")
        used[n['name']] = set()
        n['_reserved'] = sorted(str(a) for a in reserved)
    vlans = [n['vlan'] for n in spec['networks'] if 'vlan' in n]
    if len(vlans) != len(set(vlans)): problems.append('Two networks use the same VLAN.')
    cidrs = list(nets.values())
    for i, a in enumerate(cidrs):
        for b in cidrs[i + 1:]:
            if a.overlaps(b): problems.append(f'Networks {a} and {b} overlap.')

    seen_names: set[str] = set()
    def claim(name: str, what: str):
        key = k8s_name(name)
        if key in seen_names: problems.append(f'{what} {name} clashes with another object called {key}.')
        seen_names.add(key)

    gateways = {n['name']: n['gateway'] for n in spec['networks']}
    for a in spec.get('appliances', []):
        claim(a['name'], 'Appliance')
        for itf in a['interfaces']:
            net = nets.get(itf['network'])
            if itf['network'] not in gateways: problems.append(f"Appliance {a['name']} uses network {itf['network']}, which is not defined."); continue
            if net is None or 'address' not in itf: continue
            addr = ipaddress.IPv4Address(itf['address'])
            if addr not in net: problems.append(f"Appliance {a['name']}: {addr} is outside {net}."); continue
            if addr in used[itf['network']]: problems.append(f"Address {addr} is used twice on {itf['network']}.")
            used[itf['network']].add(addr)
        if a['product'] in ('haproxy', 'traefik', 'nginx') and a['role'] != 'load-balancer':
            problems.append(f"Appliance {a['name']}: {a['product']} can only be a load-balancer.")
        if a['product'] in ('opnsense', 'pfsense') and a['role'] != 'firewall':
            problems.append(f"Appliance {a['name']}: {a['product']} can only be a firewall.")
        if catalog.ARCH not in catalog.PRODUCTS[a['product']]['arch']:
            problems.append(f"Appliance {a['name']}: {a['product']} has no {catalog.ARCH} build, so it cannot run on this platform "
                            f"(XFOUNDRY_ARCH={catalog.ARCH}). Use it on an amd64 cluster.")

    # Appliance addresses are reserved so dynamic VMs never take them (Kube-OVN still assigns a static address inside
    # excludeIps). An appliance holding a network's gateway address is that network's router: OVN's own logical router
    # then moves to another address, and DHCP hands out the appliance as the default route.
    for n in spec['networks']:
        net = nets.get(n['name'])
        if net is None: continue
        claimed = {}
        for a in spec.get('appliances', []):
            for itf in a['interfaces']:
                if itf['network'] == n['name'] and itf.get('address'): claimed[itf['address']] = a['name']
        n['_router'] = claimed.get(n['gateway'])
        reserved = set(n['_reserved']) | set(claimed)
        if n['_router']:
            free = [h for h in reversed(list(net.hosts())) if str(h) not in reserved]
            if not free: problems.append(f"Network {n['name']} has no free address left for OVN's gateway."); continue
            n['_ovn_gateway'] = str(free[0])
            reserved.add(n['_ovn_gateway'])
        else:
            n['_ovn_gateway'] = n['gateway']
        n['_reserved'] = sorted(reserved, key=lambda a: int(ipaddress.IPv4Address(a)))

    total = 0
    for g in spec.get('vms', []):
        total += g['count']
        g.setdefault('power', 'all')
        for n in g['networks']:
            if n not in nets and n not in gateways: problems.append(f"VM group {g['pattern']} uses network {n}, which is not defined.")
        for display in names(g['pattern'], g['count']): claim(display, 'VM')
    if total > MAX_VMS: problems.append(f'An environment can hold up to {MAX_VMS} VMs.')

    for c in spec.get('clusters', []):
        claim(c['name'], 'Cluster')
        if c['provider'] == 'capk': problems.append(f"Cluster {c['name']}: provider capk is not built yet; use vcluster for now.")
        if c.get('network') and c['network'] not in gateways: problems.append(f"Cluster {c['name']} uses network {c['network']}, which is not defined.")
        c.setdefault('workers', 2)

    # Which router or firewall joins each pair of networks: inside the environment's VPC, traffic between two
    # networks goes through that appliance, and networks no appliance joins cannot reach each other.
    joins = []
    for a in spec.get('appliances', []):
        if a['role'] not in ('router', 'firewall'): continue
        addr = {i['network']: i.get('address') for i in a['interfaces']}
        for src in addr:
            for dst in addr:
                if src != dst and addr[src]:
                    joins.append({'from': src, 'to': dst, 'via': a['name'], 'next_hop': addr[src]})
    spec['_routes'] = joins

    if problems: raise SpecError(problems)
    return spec
