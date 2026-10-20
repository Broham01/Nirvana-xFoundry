"""Appliance defaults: which image and size each product starts from.

Images are pulled through the platform registry (Harbor), which mirrors upstream and holds the images the
xFoundry image factory builds. Tags here are starting points: the image factory (phase 1) replaces them with
digests in images.lock.json before anything reaches production.
"""
import os

REGISTRY = os.environ.get('XFOUNDRY_REGISTRY', 'harbor.xfoundry.local')
UNDERLAY = os.environ.get('XFOUNDRY_UNDERLAY', 'underlay')  # Kube-OVN ProviderNetwork for VLAN-backed networks

PRODUCTS = {
    # Full operating systems, run as KubeVirt VMs from disk images the image factory builds with Packer.
    'opnsense': {'kind': 'vm', 'image': f'{REGISTRY}/appliances/opnsense:stable', 'size': {'cpu': 2, 'memory': '4Gi', 'disk': '20Gi'}},
    'pfsense':  {'kind': 'vm', 'image': f'{REGISTRY}/appliances/pfsense-ce:stable', 'size': {'cpu': 2, 'memory': '4Gi', 'disk': '20Gi'}},
    'vyos':     {'kind': 'vm', 'image': f'{REGISTRY}/appliances/vyos:stable', 'size': {'cpu': 2, 'memory': '2Gi', 'disk': '10Gi'}},
    # Containers, run as Deployments with extra interfaces from Multus.
    'haproxy':  {'kind': 'pod', 'image': f'{REGISTRY}/mirror/haproxy:3.0', 'size': {'cpu': 1, 'memory': '512Mi'}},
    'frr':      {'kind': 'pod', 'image': f'{REGISTRY}/mirror/frrouting/frr:10.1', 'size': {'cpu': 1, 'memory': '512Mi'}},
    'traefik':  {'kind': 'pod', 'image': f'{REGISTRY}/mirror/traefik:v3.1', 'size': {'cpu': 1, 'memory': '256Mi'}},
    'nginx':    {'kind': 'pod', 'image': f'{REGISTRY}/mirror/nginx:1.27', 'size': {'cpu': 1, 'memory': '256Mi'}},
}
VM_DEFAULT_SIZE = {'cpu': 2, 'memory': '4Gi', 'disk': '30Gi'}

VCLUSTER_CHART = {'repo': 'https://charts.loft.sh', 'chart': 'vcluster', 'version': os.environ.get('XFOUNDRY_VCLUSTER_VERSION', '0.20.0')}
