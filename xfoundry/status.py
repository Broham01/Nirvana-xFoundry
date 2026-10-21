"""What is running for an environment, read from the cluster with kubectl (read-only).

The result is shown in Nirvana xStage's console. It is never change evidence: plans and the gate always use a
fresh snapshot from Nirvana's collector.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from .render import manifest_hash, namespace_of, render

STARTING = {'Pending', 'Scheduling', 'Scheduled', 'Provisioning', 'Starting', 'WaitingForVolumeBinding', 'DataVolumeError', 'ErrImagePull',
            'ContainerCreating', 'Migrating'}


def _kubectl(context: str | None, *args: str) -> tuple[int, str]:
    cmd = ['kubectl', *(['--context', context] if context else []), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise SystemExit('kubectl is not installed here.')
    except subprocess.TimeoutExpired:
        return 1, 'kubectl timed out'
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stderr


def expected(spec: dict) -> list[dict]:
    """The VMs and appliances the spec renders, with what the console shows about each."""
    out = []
    addresses = {a['name']: [i['address'] for i in a['interfaces'] if 'address' in i] for a in spec.get('appliances', [])}
    for obj in render(spec):
        labels = obj['metadata'].get('labels', {})
        if obj['kind'] == 'VirtualMachine':
            appliance = labels.get('xfoundry.io/component') not in (None, 'vm')
            display = obj['metadata'].get('annotations', {}).get('xfoundry.io/display-name', obj['metadata']['name'])
            out.append({'name': obj['metadata']['name'], 'kind': 'appliance' if appliance else 'vm', 'object': 'vm',
                        'running': obj['spec'].get('runStrategy', 'Always') != 'Halted',
                        'role': labels.get('xfoundry.io/component', '') if appliance else '', 'product': labels.get('xfoundry.io/product', ''),
                        'addresses': addresses.get(display, [])})
        elif obj['kind'] == 'Deployment':
            out.append({'name': obj['metadata']['name'], 'kind': 'appliance', 'object': 'deploy', 'role': labels.get('xfoundry.io/component', ''),
                        'product': labels.get('xfoundry.io/product', ''), 'addresses': addresses.get(obj['metadata']['name'], [])})
    return out


def observe(spec: dict, context: str | None = None, applied_manifest: str | None = None) -> dict:
    ns = namespace_of(spec)
    now = datetime.now(timezone.utc).isoformat()
    base = {'namespace': ns, 'applied': applied_manifest is not None,
            'manifest_hash': manifest_hash(applied_manifest) if applied_manifest else None, 'checked_at': now}
    code, out = _kubectl(context, 'get', 'namespace', ns, '-o', 'name')
    if code != 0:
        if 'NotFound' in out or 'not found' in out:
            return {**base, 'state': 'absent', 'message': 'Not on the cluster.', 'members': []}
        return {**base, 'state': 'unknown', 'message': f'Could not read the cluster: {out.strip()[:300]}', 'members': []}
    code, out = _kubectl(context, 'get', 'virtualmachines,virtualmachineinstances,deployments', '-n', ns, '-o', 'json')
    if code != 0:
        return {**base, 'state': 'unknown', 'message': f'Could not list what is running: {out.strip()[:300]}', 'members': []}
    items = json.loads(out).get('items', [])
    found = {(i['kind'], i['metadata']['name']): i for i in items}
    members, starting, missing = [], 0, 0
    for m in expected(spec):
        if m['object'] == 'vm':
            vm, vmi = found.get(('VirtualMachine', m['name'])), found.get(('VirtualMachineInstance', m['name']))
            phase = (vm or {}).get('status', {}).get('printableStatus') or (vmi or {}).get('status', {}).get('phase') or ('Missing' if not vm else '')
            ready = bool((vm or {}).get('status', {}).get('ready'))
            ips = [i['ipAddress'] for i in (vmi or {}).get('status', {}).get('interfaces', []) if i.get('ipAddress')]
            addresses = m['addresses'] or ips
        else:
            dep = found.get(('Deployment', m['name']))
            available = (dep or {}).get('status', {}).get('availableReplicas', 0) or 0
            ready = available >= 1
            phase = 'Missing' if not dep else 'Available' if ready else 'Starting'
            addresses = m['addresses']
        if m['object'] == 'vm' and not m['running'] and vm:
            ready, phase = True, phase or 'Stopped'   # powered off on purpose (power: half or none)
        if phase == 'Missing': missing += 1
        elif not ready and (phase in STARTING or phase == 'Starting' or phase == ''): starting += 1
        members.append({'name': m['name'], 'kind': m['kind'], 'role': m['role'], 'product': m['product'], 'phase': phase or 'Unknown',
                        'ready': ready, 'addresses': addresses[:16]})
    ready = sum(1 for m in members if m['ready'])
    vms = [m for m in members if m['kind'] == 'vm']
    summary = f"{sum(1 for m in vms if m['ready'])} of {len(vms)} VMs and {sum(1 for m in members if m['kind'] == 'appliance' and m['ready'])} of {len(members) - len(vms)} appliances ready"
    if ready == len(members): state, message = 'ready', summary + '.'
    elif missing == 0 and starting: state, message = 'pending', summary + '; the rest are starting.'
    else:
        broken = [m['name'] for m in members if not m['ready']]
        state, message = 'degraded', f"{summary}. Not ready: {', '.join(broken[:8])}{'…' if len(broken) > 8 else ''}."
    return {**base, 'state': state, 'message': message[:500], 'members': members}
