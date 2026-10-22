"""status.observe: what is running and, for anything not ready, why (disk import, pod waiting reasons, warnings)."""
import json
from pathlib import Path

import yaml

from xfoundry import status
from xfoundry.spec import check

SPEC = (Path(__file__).resolve().parent.parent / 'examples' / 'dev-lab.yaml').read_text()


def fake_kubectl(responses):
    def run(context, *args):
        key = args[1] if args[0] == 'get' else args[0]
        if key == 'namespace': return 0, 'namespace/xf-dev-lab'
        return 0, json.dumps({'items': responses.get(key, [])})
    return run


def test_stuck_vm_explains_itself(monkeypatch):
    monkeypatch.setenv('XFOUNDRY_ARCH', 'arm64')
    env = check(yaml.safe_load(SPEC))
    responses = {
        'virtualmachines,virtualmachineinstances,deployments': [
            {'kind': 'VirtualMachine', 'metadata': {'name': 'app-01'}, 'status': {'printableStatus': 'Provisioning', 'ready': False}},
            {'kind': 'VirtualMachine', 'metadata': {'name': 'app-02'}, 'status': {'printableStatus': 'Running', 'ready': True}},
            {'kind': 'Deployment', 'metadata': {'name': 'rt01'}, 'status': {'availableReplicas': 1}},
            {'kind': 'Deployment', 'metadata': {'name': 'lb01'}, 'status': {'availableReplicas': 1}}],
        'datavolumes': [{'metadata': {'name': 'app-01-root'}, 'status': {'phase': 'ImportInProgress', 'progress': '37.5%'}}],
        'pods': [{'metadata': {'name': 'importer-prime-1234', 'labels': {}}, 'status': {}},
                 {'metadata': {'name': 'virt-launcher-app-01-abcde', 'labels': {'vm.kubevirt.io/name': 'app-01'}},
                  'status': {'phase': 'Pending', 'containerStatuses': [{'state': {'waiting': {'reason': 'ContainerCreating'}}}]}}],
        'events': [{'involvedObject': {'name': 'virt-launcher-app-01-abcde'}, 'reason': 'FailedCreatePodSandBox',
                    'message': 'failed to send CNI request: EOF', 'lastTimestamp': '2026-10-22T09:00:00Z'}],
    }
    monkeypatch.setattr(status, '_kubectl', fake_kubectl(responses))
    out = status.observe(env, None, 'manifest')
    app01 = next(m for m in out['members'] if m['name'] == 'app-01')
    assert out['state'] == 'pending' and not app01['ready'] and app01['phase'] == 'Provisioning'
    assert 'disk ImportInProgress 37.5%' in app01['detail']
    assert 'virt-launcher-app-01-abcde: ContainerCreating' in app01['detail']
    assert 'FailedCreatePodSandBox: failed to send CNI request: EOF' in app01['detail']
    assert 'detail' not in next(m for m in out['members'] if m['name'] == 'app-02')
