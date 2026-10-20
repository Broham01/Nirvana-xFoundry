"""Project an environment spec into Nirvana's nirvana.snapshot/1 (Kubernetes) format.

Two snapshots, both badged as models in Nirvana (source.kind "model", the spec kept under model.spec,
the same convention as Nirvana's vSphere model wizard):

  foundation(spec)  the platform cluster before this environment exists: nodes and installed CRDs only.
                    Plan the rendered manifests against it to review "create this environment".
  built(spec)       the environment as it will look once applied. Plan day-2 changes (scale, delete,
                    a new appliance image) against it before the real cluster exists.

Once xFoundry has built the environment, its in-cluster collector replaces these with collected snapshots,
which is what removes the Modelled badge legitimately (Nirvana ADR 0012 evidence rule).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .render import namespace_of, render

SNAPSHOT_FORMAT = 'nirvana.snapshot/1'
LAST_APPLIED = 'kubectl.kubernetes.io/last-applied-configuration'
PLATFORM_CRDS = [
    'kubevirt.io/v1/VirtualMachine', 'kubevirt.io/v1/VirtualMachineInstance', 'kubevirt.io/v1/VirtualMachinePool',
    'cdi.kubevirt.io/v1beta1/DataVolume', 'snapshot.kubevirt.io/v1beta1/VirtualMachineSnapshot',
    'kubeovn.io/v1/Subnet', 'kubeovn.io/v1/Vlan', 'kubeovn.io/v1/ProviderNetwork',
    'k8s.cni.cncf.io/v1/NetworkAttachmentDefinition', 'argoproj.io/v1alpha1/Application',
]
# Scope of each platform CRD, as Nirvana's collector records it (crd_scopes, Nirvana ADR 0013 follow-up).
CRD_SCOPES = {'VirtualMachine': 'Namespaced', 'VirtualMachineInstance': 'Namespaced', 'VirtualMachinePool': 'Namespaced',
              'DataVolume': 'Namespaced', 'VirtualMachineSnapshot': 'Namespaced', 'Subnet': 'Cluster', 'Vlan': 'Cluster',
              'ProviderNetwork': 'Cluster', 'NetworkAttachmentDefinition': 'Namespaced', 'Application': 'Namespaced'}
# Custom resources the in-cluster collector must be asked for (collect-k8s-snapshot.py --custom ...).
COLLECT_CUSTOM = ['virtualmachines.kubevirt.io', 'datavolumes.cdi.kubevirt.io', 'subnets.kubeovn.io', 'vlans.kubeovn.io',
                  'network-attachment-definitions.k8s.cni.cncf.io', 'applications.argoproj.io']
PLATFORM_NAMESPACES = ['default', 'kube-system', 'kubevirt', 'cdi', 'kube-ovn', 'argocd', 'harbor', 'netbox', 'xfoundry-system']


def default_nodes(count: int = 3, cpu: str = '32', memory: str = '128Gi') -> list[dict]:
    return [{'id': f'-/Node/worker-{i}', 'name': f'worker-{i}', 'ready': True, 'unschedulable': False,
             'labels': {'topology.kubernetes.io/zone': f'zone-{(i - 1) % 3 + 1}', 'kubernetes.io/os': 'linux'},
             'allocatable': {'cpu': cpu, 'memory': memory, 'pods': '250'}} for i in range(1, count + 1)]


def _base(spec: dict, label: str, at: datetime, nodes: list[dict] | None) -> dict:
    return {
        'format': SNAPSHOT_FORMAT,
        'source': {'kind': 'model', 'domain': 'kubernetes', 'server': f"xfoundry/{spec['name']} ({label})",
                   'collected_at': at.isoformat().replace('+00:00', 'Z'), 'collector': 'xFoundry spec projection'},
        'model': {'version': 1, 'format': spec['format'], 'spec': _public(spec), 'edited_at': at.isoformat().replace('+00:00', 'Z')},
        'kubernetes': {'version': 'v1.31.0', 'nodes': nodes or default_nodes(),
                       'namespaces': [{'id': f'-/Namespace/{n}', 'name': n, 'labels': {}} for n in PLATFORM_NAMESPACES],
                       'objects': [], 'pods': [], 'crds': list(PLATFORM_CRDS), 'crd_scopes': dict(CRD_SCOPES)},
    }


def _public(spec: dict) -> dict:
    """The spec as the user wrote it, without the internal _reserved lists."""
    out = json.loads(json.dumps(spec))
    for n in out.get('networks', []): n.pop('_reserved', None)
    return out


def foundation(spec: dict, at: datetime | None = None, nodes: list[dict] | None = None) -> dict:
    return _base(spec, 'before', at or datetime.now(timezone.utc), nodes)


def built(spec: dict, at: datetime | None = None, nodes: list[dict] | None = None) -> dict:
    snap = _base(spec, 'built', at or datetime.now(timezone.utc), nodes)
    k = snap['kubernetes']
    ns = namespace_of(spec)
    node_names = [n['name'] for n in k['nodes']]
    for obj in render(spec):
        meta = obj['metadata']
        if obj['kind'] == 'Namespace':
            k['namespaces'].append({'id': f"-/Namespace/{meta['name']}", 'name': meta['name'], 'labels': meta.get('labels', {}),
                                    'annotations': meta.get('annotations', {})})
            continue
        applied = json.dumps(obj, sort_keys=True, separators=(',', ':'))
        scope_ns = meta.get('namespace', '')   # '' for cluster-scoped custom resources (Subnet, Vlan)
        k['objects'].append({'id': f"{scope_ns or '-'}/{obj['kind']}/{meta['name']}", 'kind': obj['kind'], 'api_version': obj['apiVersion'],
                             'namespace': scope_ns, 'name': meta['name'], 'labels': meta.get('labels', {}),
                             'annotations': {**meta.get('annotations', {}), LAST_APPLIED: applied}, 'spec': obj.get('spec', {}),
                             **({'data_hashes': {k: hashlib.sha256(v.encode()).hexdigest()[:16] for k, v in obj.get('data', {}).items()}}
                                if obj['kind'] == 'ConfigMap' else {})})
        spot = node_names[len(k['pods']) % len(node_names)]
        if obj['kind'] == 'VirtualMachine' and obj['spec']['runStrategy'] == 'Always':
            dom = obj['spec']['template']['spec']['domain']
            k['pods'].append({'id': f"{ns}/Pod/virt-launcher-{meta['name']}-0", 'name': f"virt-launcher-{meta['name']}-0", 'namespace': ns,
                              'labels': obj['spec']['template']['metadata']['labels'], 'node': spot, 'phase': 'Running',
                              'owner': {'kind': 'VirtualMachine', 'name': meta['name']}, 'images': ['virt-launcher'],
                              'requests': [{'cpu': str(dom['cpu']['cores']), 'memory': dom['memory']['guest']}],
                              'refs': {'configmaps': [], 'secrets': []}})
        if obj['kind'] == 'Deployment':
            tpl = obj['spec']['template']
            k['pods'].append({'id': f"{ns}/Pod/{meta['name']}-0", 'name': f"{meta['name']}-0", 'namespace': ns, 'labels': tpl['metadata']['labels'],
                              'node': spot, 'phase': 'Running', 'owner': {'kind': 'Deployment', 'name': meta['name']},
                              'images': [c['image'] for c in tpl['spec']['containers']],
                              'requests': [c.get('resources', {}).get('requests', {}) for c in tpl['spec']['containers']],
                              'refs': {'configmaps': sorted(v['configMap']['name'] for v in tpl['spec'].get('volumes', []) if 'configMap' in v),
                                       'secrets': []}})
    return snap
