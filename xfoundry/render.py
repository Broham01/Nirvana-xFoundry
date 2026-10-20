"""Render an environment spec into the Kubernetes manifests xFoundry will apply.

The output is deterministic: the same spec always renders the same bytes. That matters because the rendered
YAML is exactly what is submitted to Nirvana as the plan's manifest, and xFoundry refuses to apply anything whose
SHA-256 differs from the approved plan.

What each part becomes:
  environment  -> Namespace xf-<name> (labels Nirvana reads: environment=production|lab)
  network      -> Kube-OVN Subnet + Multus NetworkAttachmentDefinition (+ Kube-OVN Vlan when a VLAN is given)
  appliance    -> KubeVirt VirtualMachine (opnsense, pfsense, vyos) or Deployment (haproxy, frr, traefik, nginx)
  vm group     -> one KubeVirt VirtualMachine per name
  cluster      -> Argo CD Application installing a vcluster
"""
from __future__ import annotations

import hashlib
import json

import yaml

from . import catalog
from .spec import k8s_name, names, powered

MANAGED = {'app.kubernetes.io/managed-by': 'xfoundry'}


def namespace_of(spec: dict) -> str:
    return f"xf-{spec['name']}"


def _labels(spec: dict, component: str, protected: bool = False) -> dict:
    out = {**MANAGED, 'xfoundry.io/environment': spec['name'], 'xfoundry.io/component': component}
    if protected: out['nirvana.io/protected'] = 'true'
    return out


def _nad(network: str) -> str:
    return f'net-{network}'


def _ip_annotations(ns: str, interfaces: list[dict]) -> dict:
    # Kube-OVN reads a fixed address per attachment from <nad>.<namespace>.ovn.kubernetes.io/ip_address.
    return {f"{_nad(i['network'])}.{ns}.ovn.kubernetes.io/ip_address": i['address'] for i in interfaces if i.get('address')}


POLICY_PRIORITY = {'reroute': 31000, 'drop': 30000}


def _vpc(spec: dict, ns: str) -> dict:
    """One Kube-OVN VPC per environment: its networks are isolated from the cluster network and from other
    environments. Between two of its networks, traffic is rerouted to the router or firewall that joins them;
    networks that no appliance joins cannot reach each other. (Workloads that use the appliance as their default
    route never reach the VPC router at all; these policies catch everything that does.)"""
    cidr = {n['name']: n['cidr'] for n in spec['networks']}
    routes, joined = [], set()
    first = {}
    for r in spec['_routes']:
        key = (r['from'], r['to'])
        if key in first: continue            # the first appliance listed wins for a pair
        first[key] = r
        joined.add(key)
        routes.append({'priority': POLICY_PRIORITY['reroute'], 'action': 'reroute', 'nextHopIP': r['next_hop'],
                       'match': f"ip4.src == {cidr[r['from']]} && ip4.dst == {cidr[r['to']]}"})
    names = [n['name'] for n in spec['networks']]
    for src in names:
        for dst in names:
            if src != dst and (src, dst) not in joined:
                routes.append({'priority': POLICY_PRIORITY['drop'], 'action': 'drop',
                               'match': f"ip4.src == {cidr[src]} && ip4.dst == {cidr[dst]}"})
    spec_body = {'policyRoutes': routes} if routes else {}
    return {'apiVersion': 'kubeovn.io/v1', 'kind': 'Vpc', 'metadata': {'name': ns, 'labels': _labels(spec, 'network')}, 'spec': spec_body}


def _networks(spec: dict, ns: str) -> list[dict]:
    out = [_vpc(spec, ns)]
    for n in spec['networks']:
        provider = f"{_nad(n['name'])}.{ns}.ovn"
        if 'vlan' in n:
            out.append({'apiVersion': 'kubeovn.io/v1', 'kind': 'Vlan',
                        'metadata': {'name': f"{ns}-{n['name']}", 'labels': _labels(spec, 'network')},
                        'spec': {'id': n['vlan'], 'provider': catalog.UNDERLAY}})
        subnet = {'protocol': 'IPv4', 'provider': provider, 'vpc': ns, 'cidrBlock': n['cidr'], 'gateway': n['_ovn_gateway'],
                  'excludeIps': n['_reserved'], 'enableDHCP': True}
        if n['_router']:
            # The appliance is the default route; OVN's router keeps a separate address on this subnet.
            subnet['dhcpV4Options'] = f"router={n['gateway']}"
        if 'vlan' in n: subnet['vlan'] = f"{ns}-{n['name']}"
        # Kube-OVN Subnets are cluster-scoped, so the name carries the namespace.
        out.append({'apiVersion': 'kubeovn.io/v1', 'kind': 'Subnet',
                    'metadata': {'name': f"{ns}-{n['name']}", 'labels': _labels(spec, 'network')}, 'spec': subnet})
        config = {'cniVersion': '0.3.1', 'type': 'kube-ovn', 'server_socket': '/run/openvswitch/kube-ovn-daemon.sock', 'provider': provider}
        out.append({'apiVersion': 'k8s.cni.cncf.io/v1', 'kind': 'NetworkAttachmentDefinition',
                    'metadata': {'name': _nad(n['name']), 'namespace': ns, 'labels': _labels(spec, 'network')},
                    'spec': {'config': json.dumps(config, sort_keys=True)}})
    return out


def _vm(spec: dict, ns: str, display: str, image: str, size: dict, interfaces: list[dict], running: bool,
        component: str, protected: bool, extra_labels: dict | None = None, annotations: dict | None = None) -> dict:
    name = k8s_name(display)
    nets = [{'name': f"nic{i}", 'multus': {'networkName': f"{ns}/{_nad(itf['network'])}", **({'default': True} if i == 0 else {})}}
            for i, itf in enumerate(interfaces)]
    labels = {**_labels(spec, component, protected), **(extra_labels or {})}
    return {
        'apiVersion': 'kubevirt.io/v1', 'kind': 'VirtualMachine',
        'metadata': {'name': name, 'namespace': ns, 'labels': labels,
                     'annotations': {'xfoundry.io/display-name': display, **(annotations or {})}},
        'spec': {
            'runStrategy': 'Always' if running else 'Halted',
            'dataVolumeTemplates': [{
                'metadata': {'name': f'{name}-root'},
                'spec': {'source': {'registry': {'url': f'docker://{catalog.image(image)}', 'platform': {'architecture': catalog.ARCH}}},
                         'storage': {'resources': {'requests': {'storage': size['disk']}}}}}],
            'template': {
                'metadata': {'labels': {**MANAGED, 'xfoundry.io/environment': spec['name'], 'xfoundry.io/vm': name},
                             'annotations': _ip_annotations(ns, interfaces)},
                'spec': {
                    'architecture': catalog.ARCH,
                    'domain': {'cpu': {'cores': size['cpu']}, 'memory': {'guest': size['memory']},
                               'devices': {'disks': [{'name': 'root', 'disk': {'bus': 'virtio'}}],
                                           'interfaces': [{'name': n['name'], 'bridge': {}} for n in nets]}},
                    'networks': nets,
                    'volumes': [{'name': 'root', 'dataVolume': {'name': f'{name}-root'}}],
                }},
        },
    }


def _pod_appliance(spec: dict, ns: str, a: dict, image: str, size: dict, protected: bool) -> list[dict]:
    name = k8s_name(a['name'])
    product = catalog.PRODUCTS[a['product']]
    attach = ', '.join(f"{ns}/{_nad(i['network'])}" for i in a['interfaces'])
    selector = {'xfoundry.io/environment': spec['name'], 'xfoundry.io/appliance': name}
    container = {'name': a['product'], 'image': catalog.image(image),
                 'resources': {'requests': {'cpu': str(size['cpu']), 'memory': size['memory']}}}
    pod = {'containers': [container]}
    out = []
    if product.get('config'):
        # A starter config so the appliance runs before Ansible configures it.
        cm = f'{name}-config'
        out.append({'apiVersion': 'v1', 'kind': 'ConfigMap',
                    'metadata': {'name': cm, 'namespace': ns, 'labels': _labels(spec, a['role'], protected)},
                    'data': {product['config']['file']: catalog.HAPROXY_STARTER}})
        container['volumeMounts'] = [{'name': 'config', 'mountPath': product['config']['path']}]
        pod['volumes'] = [{'name': 'config', 'configMap': {'name': cm}}]
    if product.get('privileged_net'):
        # A router manages routes and addresses on its interfaces.
        container['securityContext'] = {'capabilities': {'add': ['NET_ADMIN', 'NET_RAW', 'SYS_ADMIN']}}
    out.append({
        'apiVersion': 'apps/v1', 'kind': 'Deployment',
        'metadata': {'name': name, 'namespace': ns, 'labels': {**_labels(spec, a['role'], protected), 'xfoundry.io/product': a['product']}},
        'spec': {
            'replicas': 1,
            'strategy': {'type': 'Recreate'},  # fixed addresses: two pods must never hold the same IP at once
            'selector': {'matchLabels': selector},
            'template': {
                'metadata': {'labels': {**MANAGED, **selector},
                             'annotations': {'k8s.v1.cni.cncf.io/networks': attach, **_ip_annotations(ns, a['interfaces'])}},
                'spec': pod},
        },
    })
    return out


def render(spec: dict) -> list[dict]:
    ns = namespace_of(spec)
    rules = spec['rules']
    ns_labels = {**MANAGED, 'xfoundry.io/environment': spec['name'], 'environment': 'production' if rules['production'] else 'lab'}
    ns_annotations = {'xfoundry.io/spec-hash': spec_hash(spec)}
    if spec['lifecycle'].get('ttl'): ns_annotations['xfoundry.io/ttl'] = spec['lifecycle']['ttl']
    if spec['lifecycle'].get('keep'): ns_annotations['xfoundry.io/keep'] = 'true'
    if spec.get('owner'): ns_annotations['xfoundry.io/owner'] = spec['owner']
    objects: list[dict] = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns, 'labels': ns_labels, 'annotations': ns_annotations}}]
    objects += _networks(spec, ns)

    for a in spec.get('appliances', []):
        product = catalog.PRODUCTS[a['product']]
        size = {**product['size'], **(a.get('size') or {})}
        image = a.get('image') or product['image']
        protected = rules['protect_appliances']
        if product['kind'] == 'vm':
            config = a.get('config') or {'source': 'netbox'}
            objects.append(_vm(spec, ns, a['name'], image, size, a['interfaces'], True, a['role'], protected,
                               {'xfoundry.io/product': a['product']},
                               {'xfoundry.io/config-source': config.get('source', 'netbox'), 'xfoundry.io/config-ref': config.get('ref', a['name'])}))
        else:
            objects += _pod_appliance(spec, ns, a, image, size, protected)

    for g in spec.get('vms', []):
        size = {**catalog.VM_DEFAULT_SIZE, **(g.get('size') or {})}
        group_names = names(g['pattern'], g['count'])
        for i, display in enumerate(group_names):
            tags = {f"xfoundry.io/tag-{k8s_name(t)}": 'true' for t in g.get('tags', [])}
            objects.append(_vm(spec, ns, display, g['image'], size, [{'network': n} for n in g['networks']],
                               powered(g['power'], i, g['count']), 'vm', False, {'xfoundry.io/group': k8s_name(g['pattern']), **tags},
                               {'xfoundry.io/tags': ','.join(g.get('tags', []))} if g.get('tags') else None))

    for c in spec.get('clusters', []):
        values = {'sync': {'fromHost': {'nodes': {'enabled': False}}}}
        objects.append({
            'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
            'metadata': {'name': f"{ns}-{c['name']}", 'namespace': 'argocd', 'labels': _labels(spec, 'cluster'),
                         'annotations': {'xfoundry.io/kubernetes-version': c.get('version', 'default')}},
            'spec': {'project': 'default',
                     'source': {'repoURL': catalog.VCLUSTER_CHART['repo'], 'chart': catalog.VCLUSTER_CHART['chart'],
                                'targetRevision': catalog.VCLUSTER_CHART['version'],
                                'helm': {'releaseName': c['name'], 'valuesObject': values}},
                     'destination': {'server': 'https://kubernetes.default.svc', 'namespace': ns},
                     'syncPolicy': {'automated': {'prune': True}, 'syncOptions': ['CreateNamespace=false']}},
        })
    return objects


def spec_hash(spec: dict) -> str:
    clean = {k: v for k, v in spec.items()}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()[:16]


class _NoAliases(yaml.SafeDumper):
    """Nirvana refuses YAML aliases (alias-bomb protection, ADR 0010), so never emit &anchors for shared dicts."""
    def ignore_aliases(self, data):
        return True


def to_yaml(objects: list[dict]) -> str:
    """Multi-document YAML, stable key order. This exact text is what Nirvana plans and what gets hashed."""
    return yaml.dump_all(objects, Dumper=_NoAliases, sort_keys=True, default_flow_style=False, explicit_start=True)


def manifest_hash(text: str) -> str:
    """Matches Nirvana's plan script_hash: SHA-256 of the UTF-8 manifest text."""
    return hashlib.sha256(text.encode()).hexdigest()
