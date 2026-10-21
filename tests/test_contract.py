"""The xFoundry side of the Nirvana contract (docs/integration/contract.json). If one of these fails, either the
change breaks the integration with Nirvana or the contract must change first, in both repos
(docs/integration/CONTRACT.md)."""
import inspect
import json
import re
from pathlib import Path

from xfoundry import gate, model, nirvana, spec

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / 'docs/integration/contract.json').read_text())
API = CONTRACT['nirvana_api']


def test_client_calls_only_token_routes():
    allowed = {(r['method'], r['path']) for r in API['token_routes']}
    source = inspect.getsource(nirvana)
    calls = re.findall(r"_call\('(GET|POST|PUT|DELETE|PATCH)',\s*f?'(/api/v1/[^']*)'", source)
    assert calls, 'no Nirvana calls found; update this test if the client changed shape'
    for method, path in calls:
        path = re.sub(r'\{[^}]+\}', '{id}', path)
        assert (method, path) in allowed, f'{method} {path} is not a token route in the contract'
    assert not re.search(r"['\"][^'\"\n]*/decision", source), 'xFoundry must never call a decision route'


def test_gate_reads_only_contract_plan_fields():
    used = set(re.findall(r"plan(?:\.get\(|\[)'([a-z_]+)'", inspect.getsource(gate)))
    assert used <= set(API['plan_fields_read_by_xfoundry']), used - set(API['plan_fields_read_by_xfoundry'])


def test_gate_fingerprint_uses_contract_fields():
    fields = CONTRACT['snapshot_format']['kubernetes_fields_in_gate_fingerprint']
    src = inspect.getsource(gate.fingerprint)
    for section, keys in fields.items():
        assert f"'{section}'" in src, section
        if isinstance(keys, list):
            for key in keys:
                assert f"'{key}'" in src, (section, key)


def test_models_are_marked_and_refused():
    env = spec.load(ROOT / 'examples/edge-lab.yaml')
    snap = model.built(env)
    marker = CONTRACT['snapshot_format']['model_marker']
    assert snap['format'] == CONTRACT['snapshot_format']['format']
    assert snap['source'][marker['path'].split('.')[1]] == marker['value']
    record = {'manifest_hash': '', 'snapshot_id': '', 'fingerprint': gate.fingerprint(snap), 'modelled': False, 'delete': False}
    result = gate.check({'state': 'approved', 'script': '', 'snapshot_id': ''}, '', record, snap)
    assert not result.ok and any('model' in r for r in result.reasons)


def test_spec_format_is_ours():
    assert CONTRACT['environment_spec']['format'] == spec.FORMAT


def test_name_pattern_vectors():
    for v in CONTRACT['name_pattern']['vectors']:
        assert spec.names(v['pattern'], v['count']) == v['names'], v


def test_worker_reads_only_contract_job_fields_and_reports_contract_status():
    from xfoundry import status, worker
    console = CONTRACT['console']
    used = set(re.findall(r"(?:job|current)(?:\.get\(|\[)'([a-z_]+)'", inspect.getsource(worker)))
    assert used <= set(console['claimed_job_fields_read_by_xfoundry']), used - set(console['claimed_job_fields_read_by_xfoundry'])
    src = inspect.getsource(status.observe)
    for key in re.findall(r"'([a-z_]+)':", src):
        if key in ('state', 'message', 'namespace', 'applied', 'manifest_hash', 'members', 'checked_at'):
            assert key in console['status_fields']
    member_keys = set(re.findall(r"\{'name': m\['name'\], (.*?)\}\)", src, re.S)[0] and re.findall(r"'([a-z_]+)': ", re.findall(r"members\.append\(\{(.*?)\}\)", src, re.S)[0]))
    assert member_keys | {'name'} <= set(console['member_fields']), member_keys
    states = set(re.findall(r"state, message = '([a-z]+)'", src)) | set(re.findall(r"'state': '([a-z]+)'", src))
    assert states <= set(console['environment_states']), states
    progress_states = set(re.findall(r"state='([a-z_]+)'", inspect.getsource(worker)))
    allowed = set().union(*console['worker_may_set'].values())
    assert progress_states <= allowed, progress_states - allowed
