import copy
import json
from pathlib import Path

import pytest
import yaml

from xfoundry import gate, model
from xfoundry.cli import removed_objects
from xfoundry.render import manifest_hash, render, to_yaml
from xfoundry.spec import SpecError, check, load, names

EXAMPLE = Path(__file__).resolve().parent.parent / 'examples' / 'edge-lab.yaml'


def raw():
    return yaml.safe_load(EXAMPLE.read_text())


def kinds(objects):
    return [o['kind'] for o in objects]


def test_example_is_valid_and_renders_every_part():
    objects = render(load(EXAMPLE))
    k = kinds(objects)
    assert k[0] == 'Namespace'
    assert k.count('Subnet') == 3 and k.count('NetworkAttachmentDefinition') == 3
    assert k.count('VirtualMachine') == 3      # fw01 + APP-01, APP-02
    assert k.count('Deployment') == 1          # haproxy runs as a container


def test_names_match_nirvana_model_wizard():
    assert names('PRD-WEB{nn}', 3) == ['PRD-WEB01', 'PRD-WEB02', 'PRD-WEB03']
    assert names('GOLD', 2) == ['GOLD', 'GOLD-2']
    vm = next(o for o in render(load(EXAMPLE)) if o['metadata']['name'] == 'app-01')
    assert vm['metadata']['annotations']['xfoundry.io/display-name'] == 'APP-01'


def test_render_is_deterministic_and_alias_free():
    a, b = to_yaml(render(load(EXAMPLE))), to_yaml(render(load(EXAMPLE)))
    assert a == b and manifest_hash(a) == manifest_hash(b)
    assert '&id' not in a and '*id' not in a   # Nirvana refuses YAML aliases


def test_appliances_are_protected_and_get_fixed_addresses():
    fw = next(o for o in render(load(EXAMPLE)) if o['metadata']['name'] == 'fw01')
    assert fw['metadata']['labels']['nirvana.io/protected'] == 'true'
    ann = fw['spec']['template']['metadata']['annotations']
    assert ann['net-app.xf-edge-lab.ovn.kubernetes.io/ip_address'] == '10.60.20.1'
    assert fw['spec']['template']['spec']['networks'][0]['multus']['default'] is True


def test_production_rule_labels_namespace_for_nirvana():
    spec = raw(); spec['rules']['production'] = True
    ns = render(check(spec))[0]
    assert ns['metadata']['labels']['environment'] == 'production'


@pytest.mark.parametrize('mutate, expected', [
    (lambda s: s['appliances'][0]['interfaces'].append({'network': 'nope'}), 'not defined'),
    (lambda s: s['appliances'][0]['interfaces'][0].update(address='10.99.0.1'), 'outside'),
    (lambda s: s['networks'].append({'name': 'dup', 'cidr': '10.60.20.0/25'}), 'overlap'),
    (lambda s: s['appliances'][1].update(role='firewall'), 'only be a load-balancer'),
    (lambda s: s['vms'].append({'pattern': 'fw01', 'count': 1, 'image': 'x', 'networks': ['app']}), 'clashes'),
    (lambda s: s.setdefault('clusters', []).append({'name': 'k1', 'provider': 'capk'}), 'not built yet'),
    (lambda s: s.update(name='Bad_Name'), 'name'),
])
def test_spec_problems_are_reported(mutate, expected):
    spec = raw(); mutate(spec)
    with pytest.raises(SpecError) as exc:
        check(spec)
    assert expected in str(exc.value)


def test_half_power_runs_first_half():
    spec = raw(); spec['vms'][0].update(count=3, power='half')
    vms = [o for o in render(check(spec)) if o['metadata'].get('labels', {}).get('xfoundry.io/component') == 'vm']
    assert [v['spec']['runStrategy'] for v in vms] == ['Always', 'Always', 'Halted']


def test_built_model_is_a_nirvana_kubernetes_model():
    snap = model.built(load(EXAMPLE))
    assert snap['format'] == 'nirvana.snapshot/1'
    assert snap['source']['kind'] == 'model' and snap['source']['domain'] == 'kubernetes'
    assert snap['model']['spec']['name'] == 'edge-lab' and '_reserved' not in json.dumps(snap['model'])
    k = snap['kubernetes']
    assert 'xf-edge-lab' in {n['name'] for n in k['namespaces']}
    assert {'VirtualMachine', 'Deployment', 'NetworkAttachmentDefinition'} <= {o['kind'] for o in k['objects']}
    assert {o['id'] for o in k['objects'] if not o['namespace']} == {'-/Vpc/xf-edge-lab', '-/Subnet/xf-edge-lab-wan', '-/Subnet/xf-edge-lab-dmz', '-/Subnet/xf-edge-lab-app'}
    assert k['crd_scopes']['Subnet'] == 'Cluster'
    assert len(k['pods']) == 4                               # 3 running VMs + 1 haproxy pod
    assert 'kubevirt.io/v1/VirtualMachine' in k['crds']      # so Nirvana plans VMs instead of reporting a gap


def test_foundation_model_has_platform_but_not_environment():
    k = model.foundation(load(EXAMPLE))['kubernetes']
    assert k['objects'] == [] and 'xf-edge-lab' not in {n['name'] for n in k['namespaces']}


def test_scale_down_finds_removed_vms():
    spec = load(EXAMPLE)
    before = to_yaml(render(spec))
    smaller = raw(); smaller['vms'][0]['count'] = 1
    gone = removed_objects(before, render(check(smaller)))
    assert [o['metadata']['name'] for o in gone] == ['app-02']


# ---- the gate

def _collected(snap):
    return {**snap, 'source': {**snap['source'], 'kind': 'kubernetes', 'collector': 'collect-k8s-snapshot.py'}}


def _setup():
    spec = load(EXAMPLE)
    manifest = to_yaml(render(spec))
    snap = _collected(model.built(spec))
    record = {'plan_id': 'p1', 'snapshot_id': 's1', 'manifest_hash': manifest_hash(manifest), 'fingerprint': gate.fingerprint(snap), 'delete': False}
    plan = {'id': 'p1', 'state': 'approved', 'script': manifest, 'snapshot_id': 's1'}
    return manifest, snap, record, plan


def test_gate_passes_only_when_everything_matches():
    manifest, snap, record, plan = _setup()
    assert gate.check(plan, manifest, record, snap).ok


def test_gate_refuses_unapproved_plan():
    manifest, snap, record, plan = _setup()
    for state in ('review', 'changes_requested'):
        r = gate.check({**plan, 'state': state}, manifest, record, snap)
        assert not r.ok and 'not approved' in r.reasons[0]


def test_gate_refuses_changed_manifest():
    manifest, snap, record, plan = _setup()
    r = gate.check(plan, manifest.replace('cores: 2', 'cores: 4', 1), record, snap)
    assert not r.ok and any('not the one Nirvana planned' in x for x in r.reasons)


def test_gate_refuses_drift_but_ignores_pod_churn():
    manifest, snap, record, plan = _setup()
    churned = copy.deepcopy(snap); churned['kubernetes']['pods'][0]['node'] = 'worker-3'
    assert gate.check(plan, manifest, record, churned).ok
    drifted = copy.deepcopy(snap); drifted['kubernetes']['nodes'][0]['unschedulable'] = True
    r = gate.check(plan, manifest, record, drifted)
    assert not r.ok and any('drift' in x for x in r.reasons)


def test_gate_refuses_action_mismatch():
    manifest, snap, record, plan = _setup()
    assert not gate.check(plan, manifest, record, snap, delete=True).ok


def test_gate_never_applies_a_modelled_plan():
    manifest, snap, record, plan = _setup()
    assert not gate.check(plan, manifest, {**record, 'modelled': True}, snap).ok
    as_model = {**snap, 'source': {**snap['source'], 'kind': 'model'}}
    r = gate.check(plan, manifest, {**record, 'fingerprint': gate.fingerprint(as_model)}, as_model)
    assert not r.ok and any('model' in x for x in r.reasons)


# ---- routers, arm64 dev platform, appliance configs

DEV = EXAMPLE.parent / 'dev-lab.yaml'


@pytest.fixture
def arm64(monkeypatch):
    from xfoundry import catalog
    monkeypatch.setattr(catalog, 'ARCH', 'arm64')


def subnets(objects):
    return {o['metadata']['name']: o['spec'] for o in objects if o['kind'] == 'Subnet'}


def test_appliance_on_gateway_becomes_router_and_ovn_moves(arm64):
    s = subnets(render(load(DEV)))['xf-dev-lab-app']
    assert s['gateway'] == '10.70.20.254' and s['dhcpV4Options'] == 'router=10.70.20.1' and s['enableDHCP'] is True
    assert '10.70.20.1' in s['excludeIps'] and '10.70.20.254' in s['excludeIps']


def test_network_without_router_keeps_ovn_gateway():
    s = subnets(render(load(EXAMPLE)))['xf-edge-lab-wan']      # 10.60.0.1 is an upstream router, not an appliance
    assert s['gateway'] == '10.60.0.1' and 'dhcpV4Options' not in s
    assert '10.60.0.2' in s['excludeIps']                      # fw01's static address is reserved from dynamic use


def test_x86_only_appliances_refused_on_arm64(arm64):
    with pytest.raises(SpecError) as exc:
        load(EXAMPLE)
    assert 'opnsense has no arm64 build' in str(exc.value)


def test_arm64_vms_import_the_right_image_variant(arm64):
    vm = next(o for o in render(load(DEV)) if o['kind'] == 'VirtualMachine')
    assert vm['spec']['template']['spec']['architecture'] == 'arm64'
    assert vm['spec']['dataVolumeTemplates'][0]['spec']['source']['registry']['platform'] == {'architecture': 'arm64'}


def test_haproxy_gets_a_starter_config_and_frr_gets_network_capabilities(arm64):
    objects = render(load(DEV))
    cm = next(o for o in objects if o['kind'] == 'ConfigMap')
    assert cm['metadata']['name'] == 'lb01-config' and 'bind :8404' in cm['data']['haproxy.cfg']
    lb = next(o for o in objects if o['kind'] == 'Deployment' and o['metadata']['name'] == 'lb01')
    assert lb['spec']['template']['spec']['volumes'][0]['configMap']['name'] == 'lb01-config'
    rt = next(o for o in objects if o['kind'] == 'Deployment' and o['metadata']['name'] == 'rt01')
    assert 'NET_ADMIN' in rt['spec']['template']['spec']['containers'][0]['securityContext']['capabilities']['add']


def test_mirror_prefixes_images(monkeypatch, arm64):
    from xfoundry import catalog
    monkeypatch.setattr(catalog, 'MIRROR', 'registry.local:5000')
    lb = next(o for o in render(load(DEV)) if o['metadata']['name'] == 'lb01' and o['kind'] == 'Deployment')
    assert lb['spec']['template']['spec']['containers'][0]['image'] == 'registry.local:5000/docker.io/library/haproxy:3.4.7'


# ---- one VPC per environment

def vpc_of(objects):
    return next(o for o in objects if o['kind'] == 'Vpc')


def test_environment_gets_its_own_vpc_and_subnets_join_it(arm64):
    objects = render(load(DEV))
    vpc = vpc_of(objects)
    assert vpc['metadata']['name'] == 'xf-dev-lab' and objects.index(vpc) < next(i for i, o in enumerate(objects) if o['kind'] == 'Subnet')
    assert all(s['vpc'] == 'xf-dev-lab' for s in subnets(objects).values())


def test_traffic_between_networks_is_rerouted_through_the_router(arm64):
    routes = vpc_of(render(load(DEV)))['spec']['policyRoutes']
    assert {'priority': 31000, 'action': 'reroute', 'nextHopIP': '10.70.20.1',
            'match': 'ip4.src == 10.70.20.0/24 && ip4.dst == 10.70.10.0/24'} in routes
    assert {'priority': 31000, 'action': 'reroute', 'nextHopIP': '10.70.10.1',
            'match': 'ip4.src == 10.70.10.0/24 && ip4.dst == 10.70.20.0/24'} in routes
    assert not [r for r in routes if r['action'] == 'drop']      # rt01 joins the only pair


def test_networks_no_router_joins_cannot_reach_each_other(arm64):
    spec = yaml.safe_load(DEV.read_text())
    spec['networks'].append({'name': 'isolated', 'cidr': '10.70.30.0/24'})
    routes = vpc_of(render(check(spec)))['spec']['policyRoutes']
    drops = {r['match'] for r in routes if r['action'] == 'drop'}
    assert 'ip4.src == 10.70.30.0/24 && ip4.dst == 10.70.20.0/24' in drops
    assert 'ip4.src == 10.70.10.0/24 && ip4.dst == 10.70.30.0/24' in drops
    assert len(drops) == 4


def test_load_balancers_do_not_route():
    routes = vpc_of(render(load(EXAMPLE)))['spec']['policyRoutes']
    assert {r['nextHopIP'] for r in routes if r['action'] == 'reroute'} == {'10.60.0.2', '10.60.10.1', '10.60.20.1'}   # fw01 only


# ---- routers forward; other appliances route through them

def deployment(objects, name):
    return next(o for o in objects if o['kind'] == 'Deployment' and o['metadata']['name'] == name)


def test_router_pods_forward(arm64):
    rt = deployment(render(load(DEV)), 'rt01')
    assert rt['spec']['template']['spec']['securityContext']['sysctls'] == [{'name': 'net.ipv4.ip_forward', 'value': '1'}]


def test_appliance_pods_route_other_networks_through_the_router(arm64):
    import json as _json
    lb = deployment(render(load(DEV)), 'lb01')
    ann = lb['spec']['template']['metadata']['annotations']
    assert _json.loads(ann['net-front.xf-dev-lab.ovn.kubernetes.io/routes']) == [{'dst': '10.70.20.0/24', 'gw': '10.70.10.1'}]
    assert 'securityContext' not in lb['spec']['template']['spec']          # a load balancer does not forward


def test_no_routes_when_no_router_joins(arm64):
    spec = yaml.safe_load(DEV.read_text())
    spec['appliances'] = [a for a in spec['appliances'] if a['name'] != 'rt01']
    lb = deployment(render(check(spec)), 'lb01')
    assert not [k for k in lb['spec']['template']['metadata']['annotations'] if k.endswith('/routes')]


HASH = '$6$labsalt$' + 'a' * 86


def test_cloud_init_adds_user_keys_password_hash_and_packages():
    spec = raw()
    spec['vms'][0]['cloud_init'] = {'user': 'elfaz', 'ssh_keys': ['ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample elfaz@mac'],
                                    'password_hash': HASH, 'packages': ['nginx', 'qemu-guest-agent']}
    vms = [o for o in render(check(spec)) if o['kind'] == 'VirtualMachine' and o['metadata']['labels'].get('xfoundry.io/component') == 'vm']
    tpl = vms[0]['spec']['template']['spec']
    assert [d['name'] for d in tpl['domain']['devices']['disks']] == ['root', 'cloudinit']
    data = tpl['volumes'][1]['cloudInitNoCloud']['userData']
    assert data.startswith('#cloud-config\n')
    doc = yaml.safe_load(data)
    assert doc['hostname'] == vms[0]['metadata']['name'] and doc['ssh_pwauth'] is False and doc['packages'] == ['nginx', 'qemu-guest-agent']
    assert doc['users'][0]['name'] == 'elfaz' and doc['users'][0]['passwd'] == HASH and doc['users'][0]['lock_passwd'] is False
    assert render(check(spec)) == render(check(spec)), 'deterministic: the same manifest at plan and apply'
    # Without a hash the account is key-only; the appliance VM gets no cloud-init.
    del spec['vms'][0]['cloud_init']['password_hash']
    doc = yaml.safe_load(next(o for o in render(check(spec)) if o['kind'] == 'VirtualMachine' and o['metadata']['labels'].get('xfoundry.io/component') == 'vm')
                         ['spec']['template']['spec']['volumes'][1]['cloudInitNoCloud']['userData'])
    assert doc['users'][0]['lock_passwd'] is True and 'passwd' not in doc['users'][0]
    fw = next(o for o in render(check(spec)) if o['metadata']['labels'].get('xfoundry.io/component') == 'firewall')
    assert len(fw['spec']['template']['spec']['volumes']) == 1


@pytest.mark.parametrize('ci', [{'user': 'Root!'}, {'user': 'a', 'password_hash': 'plaintext'}, {'user': 'a', 'ssh_keys': ['not a key']},
                                {'ssh_keys': []}, {'user': 'a', 'packages': ['rm -rf /']}])
def test_cloud_init_refuses_plain_passwords_and_odd_values(ci):
    spec = raw()
    spec['vms'][0]['cloud_init'] = ci
    with pytest.raises(SpecError):
        check(spec)


def internet_spec():
    spec = raw()
    for n in spec['networks']:
        if n['name'] in ('dmz', 'app'): n['internet'] = True
    return spec


def test_internet_access_adds_an_outbound_gateway_and_routes():
    env = check(internet_spec())
    e = env['_egress']
    app = next(n for n in env['networks'] if n['name'] == 'app')
    assert e['network'] == 'dmz' and e['address'] in next(n for n in env['networks'] if n['name'] == 'dmz')['_reserved']
    objects = render(env)
    vpc = next(o for o in objects if o['kind'] == 'Vpc')
    internet = [r for r in vpc['spec']['policyRoutes'] if r['priority'] == 29000]
    assert {r['match'] for r in internet} == {'ip4.src == 10.60.10.0/24', 'ip4.src == 10.60.20.0/24'}
    assert all(r['action'] == 'reroute' and r['nextHopIP'] == e['address'] for r in internet)
    # Traffic between the environment's own networks keeps its higher-priority rules.
    assert min(r['priority'] for r in vpc['spec']['policyRoutes'] if r['priority'] != 29000) > 29000
    subnets = {o['metadata']['name']: o['spec'] for o in objects if o['kind'] == 'Subnet'}
    assert 'dns_server=1.1.1.1' in subnets['xf-edge-lab-app']['dhcpV4Options']
    assert 'router=' not in subnets['xf-edge-lab-app']['dhcpV4Options'], 'VMs use the VPC router, which knows the way out'
    assert 'dhcpV4Options' not in subnets['xf-edge-lab-wan'] or 'dns_server' not in subnets['xf-edge-lab-wan']['dhcpV4Options']
    gw = next(o for o in objects if o['kind'] == 'Deployment' and o['metadata']['name'] == 'egress')
    pod = gw['spec']['template']
    assert pod['metadata']['annotations']['net-dmz.xf-edge-lab.ovn.kubernetes.io/ip_address'] == e['address']
    assert json.loads(pod['metadata']['annotations']['net-dmz.xf-edge-lab.ovn.kubernetes.io/routes']) == [{'dst': app['cidr'], 'gw': e['gateway']}]
    script = pod['spec']['containers'][0]['command'][2]
    assert 'MASQUERADE' in script and '-i eth0 -j DROP' in script and '-d 192.168.0.0/16 -j DROP' in script
    assert script.index('ESTABLISHED,RELATED') < script.index('-i eth0 -j DROP'), 'replies are let in before the rest is dropped'
    assert render(env) == render(check(internet_spec())), 'deterministic'


def test_without_internet_nothing_changes():
    objects = render(check(raw()))
    assert not any(o['kind'] == 'Deployment' and o['metadata']['name'] == 'egress' for o in objects)
    vpc = next(o for o in objects if o['kind'] == 'Vpc')
    assert all(r['priority'] != 29000 for r in vpc['spec'].get('policyRoutes', []))


def test_internet_refused_on_vlan_networks_and_name_clash():
    spec = raw()
    spec['networks'][0].update(internet=True, vlan=100)
    with pytest.raises(SpecError, match='VLAN network'):
        check(spec)
    spec = internet_spec()
    spec['vms'][0]['pattern'] = 'egress'; spec['vms'][0]['count'] = 1
    with pytest.raises(SpecError, match='clashes'):
        check(spec)


def test_vms_on_internet_networks_get_the_egress_dns_server():
    objects = render(check(internet_spec()))
    vm = next(o for o in objects if o['kind'] == 'VirtualMachine' and o['metadata']['labels'].get('xfoundry.io/component') == 'vm')
    tpl = vm['spec']['template']['spec']
    assert tpl['dnsPolicy'] == 'None' and tpl['dnsConfig'] == {'nameservers': ['1.1.1.1']}
    plain = next(o for o in render(check(raw())) if o['kind'] == 'VirtualMachine' and o['metadata']['labels'].get('xfoundry.io/component') == 'vm')
    assert 'dnsPolicy' not in plain['spec']['template']['spec']


def _app_vms(objects):
    return [o for o in objects if o['kind'] == 'VirtualMachine' and o['metadata']['labels'].get('xfoundry.io/component') == 'vm']


def test_fixed_addresses_and_stable_macs():
    """ADR 0009: the group's first VM takes the given address, the next VM the one after; both are kept out of DHCP.
    stable_mac gives each card the same MAC on every rebuild."""
    spec = raw()
    spec['vms'][0].update(addresses={'app': '10.60.20.50'}, stable_mac=True)
    objects = render(check(spec))
    vms = _app_vms(objects)
    keys = [k for k in vms[0]['spec']['template']['metadata']['annotations'] if k.endswith('ovn.kubernetes.io/ip_address')]
    assert [vm['spec']['template']['metadata']['annotations'][keys[0]] for vm in vms] == ['10.60.20.50', '10.60.20.51']
    subnet = next(o for o in objects if o['kind'] == 'Subnet' and o['metadata']['name'].endswith('app'))
    assert {'10.60.20.50', '10.60.20.51'} <= set(subnet['spec']['excludeIps'])
    macs = [vm['spec']['template']['spec']['domain']['devices']['interfaces'][0]['macAddress'] for vm in vms]
    assert len(set(macs)) == 2 and all(m.startswith('02:') for m in macs)
    assert macs == [vm['spec']['template']['spec']['domain']['devices']['interfaces'][0]['macAddress'] for vm in _app_vms(render(check(spec)))]
    # Without the options nothing changes for existing environments.
    plain = _app_vms(render(check(raw())))
    assert 'macAddress' not in plain[0]['spec']['template']['spec']['domain']['devices']['interfaces'][0]


@pytest.mark.parametrize('addresses, expected', [
    ({'app': '10.60.20.255'}, 'do not fit'),          # the second VM would be past the network
    ({'app': '10.60.20.1'}, 'the gateway'),            # gateway
    ({'app': '10.60.20.9'}, 'excluded'),               # inside exclude
    ({'dmz': '10.60.10.50'}, 'does not use'),
])
def test_fixed_addresses_refused(addresses, expected):
    spec = raw()
    spec['vms'][0]['addresses'] = addresses
    with pytest.raises(SpecError) as e: check(spec)
    assert expected in ' '.join(e.value.problems)
