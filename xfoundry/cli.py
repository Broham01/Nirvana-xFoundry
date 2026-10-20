"""xfoundry command line.

  xfoundry validate SPEC                     check a spec
  xfoundry render SPEC [-o FILE]             print the manifests xFoundry would apply
  xfoundry model SPEC --stage before|built   write a nirvana.snapshot/1 model for Nirvana
  xfoundry plan SPEC [--action A] (--snapshot FILE | --model)
                                             submit to Nirvana for review; records what was submitted
  xfoundry status SPEC [--action A]          show Nirvana's decision on the latest submitted plan
  xfoundry apply SPEC [--action A] --snapshot FRESH [--dry-run]
                                             run the gate, then kubectl apply/delete

Actions: create (default; create or change to match the spec), prune (delete objects the spec no longer has,
for example after scaling a VM group down), destroy (delete the whole environment).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import gate, model
from .nirvana import Nirvana, NirvanaError
from .render import manifest_hash, render, to_yaml
from .spec import SpecError, load

LEDGER_DIR = Path('.xfoundry')
ACTIONS = ('create', 'prune', 'destroy')


def ledger_path(spec: dict) -> Path:
    return LEDGER_DIR / f"{spec['name']}.json"


def read_ledger(spec: dict) -> dict:
    p = ledger_path(spec)
    return json.loads(p.read_text()) if p.exists() else {'environment': spec['name'], 'plans': {}, 'applied_manifest': None}


def write_ledger(spec: dict, data: dict):
    LEDGER_DIR.mkdir(exist_ok=True)
    ledger_path(spec).write_text(json.dumps(data, indent=2, sort_keys=True))


def removed_objects(previous_manifest: str | None, current: list[dict]) -> list[dict]:
    """Objects in what was last applied that the spec no longer renders (scale-down, removed appliance)."""
    if not previous_manifest: return []
    key = lambda o: (o['kind'], o['metadata'].get('namespace', ''), o['metadata']['name'])
    now = {key(o) for o in current}
    gone = [o for o in yaml.safe_load_all(previous_manifest) if o and key(o) not in now]
    return sorted(gone, key=key)


def manifest_for(spec: dict, action: str, ledger: dict) -> str:
    objects = render(spec)
    if action == 'create': return to_yaml(objects)
    if action == 'destroy': return to_yaml(list(reversed(objects)))
    gone = removed_objects(ledger.get('applied_manifest'), objects)
    if not gone: raise SystemExit('Nothing to prune: every object last applied is still in the spec.')
    return to_yaml(gone)


def _snapshot(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('format') != model.SNAPSHOT_FORMAT or 'kubernetes' not in data:
        raise SystemExit(f'{path} is not a nirvana.snapshot/1 Kubernetes snapshot.')
    return data


def cmd_validate(a):
    spec = load(a.spec)
    vms = sum(g['count'] for g in spec.get('vms', []))
    print(f"{spec['name']}: valid. {len(spec['networks'])} networks, {len(spec.get('appliances', []))} appliances, "
          f"{vms} VMs, {len(spec.get('clusters', []))} clusters.")


def cmd_render(a):
    text = to_yaml(render(load(a.spec)))
    if a.output: Path(a.output).write_text(text); print(f'Wrote {a.output} (sha256 {manifest_hash(text)[:16]}).')
    else: sys.stdout.write(text)


def cmd_model(a):
    spec = load(a.spec)
    snap = model.built(spec) if a.stage == 'built' else model.foundation(spec)
    Path(a.output).write_text(json.dumps(snap, indent=1))
    k = snap['kubernetes']
    print(f"Wrote {a.output}: model '{snap['source']['server']}', {len(k['objects'])} objects, {len(k['pods'])} pods.")


def cmd_plan(a):
    spec = load(a.spec)
    ledger = read_ledger(spec)
    manifest = manifest_for(spec, a.action, ledger)
    snap = model.foundation(spec) if a.model else _snapshot(a.snapshot)
    client = Nirvana()
    digest = manifest_hash(manifest)
    stored = client.upload_snapshot(snap, name=f"xfoundry {spec['name']} {datetime.now(timezone.utc):%Y-%m-%d %H:%M}", key=f'snap-{digest[:24]}-{gate.fingerprint(snap)[:24]}')
    delete = a.action != 'create'
    title = {'create': 'Create or update', 'prune': 'Prune removed objects from', 'destroy': 'Destroy'}[a.action] + f" environment {spec['name']}"
    plan = client.create_plan(stored['id'], manifest, title, f"xfoundry-{spec['name']}-{a.action}.yaml", delete, key=f'plan-{a.action}-{digest[:40]}')
    ledger['plans'][a.action] = {'plan_id': plan['id'], 'snapshot_id': stored['id'], 'manifest_hash': digest, 'fingerprint': gate.fingerprint(snap),
                                 'delete': delete, 'modelled': bool(a.model), 'submitted_at': datetime.now(timezone.utc).isoformat()}
    write_ledger(spec, ledger)
    print(f"Submitted plan {plan['id']} ({plan.get('status', '')}, {plan.get('highs', 0)} high findings). Review and approve it in Nirvana.")
    if a.model: print('Planned against a model: Nirvana marks this Modelled. The apply gate will require a fresh collected snapshot that matches.')


def cmd_status(a):
    spec = load(a.spec)
    rec = read_ledger(spec)['plans'].get(a.action)
    if not rec: raise SystemExit(f'No {a.action} plan submitted for {spec["name"]} yet.')
    plan = Nirvana().get_plan(rec['plan_id'])
    print(f"Plan {plan['id']}: {plan['state']} · {plan.get('status', '')} · {plan.get('highs', 0)} high findings"
          + (f" · note: {plan['decision_note']}" if plan.get('decision_note') else ''))


def cmd_apply(a):
    spec = load(a.spec)
    ledger = read_ledger(spec)
    rec = ledger['plans'].get(a.action)
    if not rec: raise SystemExit(f'No {a.action} plan submitted for {spec["name"]}. Run xfoundry plan first.')
    manifest = manifest_for(spec, a.action, ledger)
    result = gate.check(Nirvana().get_plan(rec['plan_id']), manifest, rec, _snapshot(a.snapshot), delete=rec['delete'])
    if not result.ok:
        print('Refused. Nothing was changed:', *[f'  - {r}' for r in result.reasons], sep='\n')
        raise SystemExit(2)
    verb = ['delete', '--ignore-not-found', '--wait=false'] if rec['delete'] else ['apply']
    cmd = ['kubectl', *verb, '-f', '-'] + (['--dry-run=server'] if a.dry_run else [])
    print('Gate passed. Running:', ' '.join(cmd))
    proc = subprocess.run(cmd, input=manifest, text=True)
    if proc.returncode: raise SystemExit(proc.returncode)
    if not a.dry_run:
        if a.action == 'create': ledger['applied_manifest'] = manifest
        elif a.action == 'destroy': ledger['applied_manifest'] = None
        else: ledger['applied_manifest'] = to_yaml(render(spec))
        ledger['plans'].pop(a.action)   # a plan is used once
        write_ledger(spec, ledger)


def main(argv: list[str] | None = None):
    p = argparse.ArgumentParser(prog='xfoundry', description='Nirvana xFoundry: build, scale and destroy environments, gated by Nirvana plans.')
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('validate'); s.add_argument('spec'); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser('render'); s.add_argument('spec'); s.add_argument('-o', '--output'); s.set_defaults(fn=cmd_render)
    s = sub.add_parser('model'); s.add_argument('spec'); s.add_argument('--stage', choices=['before', 'built'], default='built')
    s.add_argument('-o', '--output', required=True); s.set_defaults(fn=cmd_model)
    s = sub.add_parser('plan'); s.add_argument('spec'); s.add_argument('--action', choices=ACTIONS, default='create')
    g = s.add_mutually_exclusive_group(required=True); g.add_argument('--snapshot'); g.add_argument('--model', action='store_true')
    s.set_defaults(fn=cmd_plan)
    s = sub.add_parser('status'); s.add_argument('spec'); s.add_argument('--action', choices=ACTIONS, default='create'); s.set_defaults(fn=cmd_status)
    s = sub.add_parser('apply'); s.add_argument('spec'); s.add_argument('--action', choices=ACTIONS, default='create')
    s.add_argument('--snapshot', required=True, help='fresh snapshot of the target, from collect-k8s-snapshot.py')
    s.add_argument('--dry-run', action='store_true'); s.set_defaults(fn=cmd_apply)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except SpecError as exc:
        print('The spec has problems:', *[f'  - {x}' for x in exc.problems], sep='\n', file=sys.stderr); raise SystemExit(1)
    except NirvanaError as exc:
        print(exc, file=sys.stderr); raise SystemExit(3)


if __name__ == '__main__':
    main()
