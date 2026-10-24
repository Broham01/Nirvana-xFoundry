"""Appliance defaults: which image and size each product starts from, and which CPU architectures it runs on.

Images are upstream references. Set XFOUNDRY_MIRROR to pull through a registry mirror (Harbor in the cloud
platform, a local registry on a dev cluster): docker.io/library/haproxy:3.4.7 becomes <mirror>/docker.io/library/haproxy:3.4.7.
Tags here are starting points; the image factory (roadmap step 5) pins digests before production.

XFOUNDRY_ARCH is the platform's CPU architecture (amd64 for cloud servers, arm64 for an Apple Silicon dev cluster).
It decides which products are allowed and which image variant CDI imports. It must be the same when a plan is made
and when it is applied, or the manifest hash will not match and the gate refuses.
"""
import os

ARCH = os.environ.get('XFOUNDRY_ARCH', 'amd64')
MIRROR = os.environ.get('XFOUNDRY_MIRROR', '').rstrip('/')
UNDERLAY = os.environ.get('XFOUNDRY_UNDERLAY', 'underlay')  # Kube-OVN ProviderNetwork for VLAN-backed networks


def image(ref: str) -> str:
    return f'{MIRROR}/{ref}' if MIRROR else ref


PRODUCTS = {
    # Full operating systems, run as KubeVirt VMs. x86 only upstream: they need an amd64 platform (cloud servers).
    # Disk images are built by the image factory with Packer; until then, set `image:` on the appliance.
    'opnsense': {'kind': 'vm', 'arch': ('amd64',), 'image': 'harbor.xfoundry.local/appliances/opnsense:stable', 'size': {'cpu': 2, 'memory': '4Gi', 'disk': '20Gi'}},
    'pfsense':  {'kind': 'vm', 'arch': ('amd64',), 'image': 'harbor.xfoundry.local/appliances/pfsense-ce:stable', 'size': {'cpu': 2, 'memory': '4Gi', 'disk': '20Gi'}},
    'vyos':     {'kind': 'vm', 'arch': ('amd64',), 'image': 'harbor.xfoundry.local/appliances/vyos:stable', 'size': {'cpu': 2, 'memory': '2Gi', 'disk': '10Gi'}},
    # Containers, run as Deployments with extra interfaces from Multus. Multi-arch upstream images.
    'haproxy':  {'kind': 'pod', 'arch': ('amd64', 'arm64'), 'image': 'docker.io/library/haproxy:3.4.7', 'size': {'cpu': 1, 'memory': '256Mi'},
                 'config': {'path': '/usr/local/etc/haproxy', 'file': 'haproxy.cfg'}},
    'frr':      {'kind': 'pod', 'arch': ('amd64', 'arm64'), 'image': 'quay.io/frrouting/frr:10.7.1', 'size': {'cpu': 1, 'memory': '256Mi'},
                 'privileged_net': True},
    'traefik':  {'kind': 'pod', 'arch': ('amd64', 'arm64'), 'image': 'docker.io/library/traefik:v3.7.15', 'size': {'cpu': 1, 'memory': '256Mi'}},
    'nginx':    {'kind': 'pod', 'arch': ('amd64', 'arm64'), 'image': 'docker.io/library/nginx:1.30.6', 'size': {'cpu': 1, 'memory': '128Mi'}},
}
VM_DEFAULT_SIZE = {'cpu': 2, 'memory': '4Gi', 'disk': '30Gi'}

# A starting configuration so a load balancer runs before Ansible configures it: health and stats on :8404.
HAPROXY_STARTER = """global
  log stdout format raw local0
defaults
  mode http
  log global
  timeout connect 5s
  timeout client 30s
  timeout server 30s
frontend health
  bind :8404
  http-request return status 200 content-type text/plain string ok if { path /healthz }
  stats enable
  stats uri /stats
"""

# The egress gateway (networks with internet: true) is a small pod that NATs the environment's traffic out of the
# cluster network. It needs iptables; Kube-OVN's own image has it and is already on every node. Like XFOUNDRY_ARCH,
# keep it the same between plan and apply.
EGRESS_IMAGE = os.environ.get('XFOUNDRY_EGRESS_IMAGE', 'docker.io/kubeovn/kube-ovn:v1.16.14')
EGRESS_DNS = os.environ.get('XFOUNDRY_EGRESS_DNS', '1.1.1.1')   # handed to VMs on internet networks by DHCP
# Internet means the public internet: the cluster, the platform's own networks and your home or office network stay
# out of reach, so an environment is still isolated from everything but the outside world.
EGRESS_BLOCK = os.environ.get('XFOUNDRY_EGRESS_BLOCK', '10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16').split()

VCLUSTER_CHART = {'repo': 'https://charts.loft.sh', 'chart': 'vcluster', 'version': os.environ.get('XFOUNDRY_VCLUSTER_VERSION', '0.20.0')}
