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
    assert all(o['namespace'] for o in k['objects'])          # cluster-scoped Subnets are left out like the collector does
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

def _setup():
    spec = load(EXAMPLE)
    manifest = to_yaml(render(spec))
    snap = model.built(spec)
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
