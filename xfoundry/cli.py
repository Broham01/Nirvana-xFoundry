"""xfoundry command line.

  xfoundry validate SPEC                     check a spec
  xfoundry render SPEC [-o FILE]             print the manifests xFoundry would apply
  xfoundry model SPEC --stage before|built   write a nirvana.snapshot/1 model for Nirvana
  xfoundry plan SPEC [--action A] (--snapshot FILE | --model)
                                             submit to Nirvana for review; records what was submitted
  xfoundry status SPEC [--action A]          show Nirvana's decision on the latest submitted plan
  xfoundry apply SPEC [--action A] --snapshot FRESH [--dry-run]
                                             run the gate, then kubectl apply/delete
  xfoundry collect [-o FILE]                 snapshot the cluster with Nirvana's collector (NIRVANA_DIR)
  xfoundry run SPEC [--action A]             all of it: collect, plan, wait for approval in Nirvana,
                                             collect again, gate, apply
  xfoundry worker [--once]                   carry out jobs queued in Nirvana xStage's console (ADR 0004)

Actions: create (default; create or change to match the spec), prune (delete objects the spec no longer has,
for example after scaling a VM group down), destroy (delete the whole environment).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
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


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:48]


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


def submit_plan(spec: dict, action: str, snap: dict, modelled: bool = False, client: Nirvana | None = None) -> tuple[dict, dict]:
    """Upload the snapshot, submit the manifest for `action` as a plan, and record what was submitted in the ledger."""
    ledger = read_ledger(spec)
    manifest = manifest_for(spec, action, ledger)
    client = client or Nirvana()
    digest = manifest_hash(manifest)
    # Idempotency keys come from the whole request, so a retry replays and a new submission never collides (Nirvana
    # refuses a reused key with a different body).
    snap_name = f"xfoundry {spec['name']} {datetime.now(timezone.utc):%Y-%m-%d %H:%M}"
    stored = client.upload_snapshot(snap, name=snap_name, key='snap-' + _key(snap_name, snap))
    delete = action != 'create'
    title = {'create': 'Create or update', 'prune': 'Prune removed objects from', 'destroy': 'Destroy'}[action] + f" environment {spec['name']}"
    plan = client.create_plan(stored['id'], manifest, title, f"xfoundry-{spec['name']}-{action}.yaml", delete,
                              key='plan-' + _key(action, stored['id'], digest))
    ledger['plans'][action] = {'plan_id': plan['id'], 'snapshot_id': stored['id'], 'manifest_hash': digest, 'fingerprint': gate.fingerprint(snap),
                               'delete': delete, 'modelled': bool(modelled), 'submitted_at': datetime.now(timezone.utc).isoformat()}
    write_ledger(spec, ledger)
    return plan, ledger['plans'][action]


def cmd_plan(a):
    spec = load(a.spec)
    snap = model.foundation(spec) if a.model else _snapshot(a.snapshot)
    plan, _ = submit_plan(spec, a.action, snap, modelled=a.model)
    print(f"Submitted plan {plan['id']} ({plan.get('status', '')}, {plan.get('highs', 0)} high findings). Review and approve it in Nirvana.")
    if a.model: print('Planned against a model: Nirvana marks this Modelled. The apply gate will require a fresh collected snapshot that matches.')


def cmd_status(a):
    spec = load(a.spec)
    rec = read_ledger(spec)['plans'].get(a.action)
    if not rec: raise SystemExit(f'No {a.action} plan submitted for {spec["name"]} yet.')
    plan = Nirvana().get_plan(rec['plan_id'])
    print(f"Plan {plan['id']}: {plan['state']} · {plan.get('status', '')} · {plan.get('highs', 0)} high findings"
          + (f" · note: {plan['decision_note']}" if plan.get('decision_note') else ''))


class Refused(Exception):
    """The gate said no. Nothing was changed."""
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__('; '.join(reasons))


def apply_plan(spec: dict, action: str, fresh: dict, context: str | None = None, dry_run: bool = False,
               client: Nirvana | None = None) -> tuple[list[str], str]:
    """Run the gate against a fresh snapshot, then kubectl apply (or delete). Returns the command and its output.
    Raises Refused when the gate refuses, and SystemExit when there is no plan or kubectl fails."""
    ledger = read_ledger(spec)
    rec = ledger['plans'].get(action)
    if not rec: raise SystemExit(f'No {action} plan submitted for {spec["name"]}. Run xfoundry plan first.')
    manifest = manifest_for(spec, action, ledger)
    result = gate.check((client or Nirvana()).get_plan(rec['plan_id']), manifest, rec, fresh, delete=rec['delete'])
    if not result.ok: raise Refused(result.reasons)
    verb = ['delete', '--ignore-not-found', '--wait=false'] if rec['delete'] else ['apply']
    cmd = ['kubectl', *(['--context', context] if context else []), *verb, '-f', '-'] + (['--dry-run=server'] if dry_run else [])
    try:
        proc = subprocess.run(cmd, input=manifest, text=True, capture_output=True)
    except FileNotFoundError:
        raise SystemExit('kubectl is not installed here. Nothing was changed.')
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode: raise SystemExit(f'kubectl failed (exit {proc.returncode}): {output[-1500:]}')
    if not dry_run:
        if action == 'create': ledger['applied_manifest'] = manifest
        elif action == 'destroy': ledger['applied_manifest'] = None
        else: ledger['applied_manifest'] = to_yaml(render(spec))
        ledger['plans'].pop(action)   # a plan is used once
        write_ledger(spec, ledger)
    return cmd, output


def cmd_apply(a):
    spec = load(a.spec)
    try:
        cmd, output = apply_plan(spec, a.action, _snapshot(a.snapshot), _context(a), a.dry_run)
    except Refused as exc:
        print('Refused. Nothing was changed:', *[f'  - {r}' for r in exc.reasons], sep='\n')
        raise SystemExit(2)
    print('Gate passed. Ran:', ' '.join(cmd))
    if output: print(output)


def _context(a) -> str | None:
    return getattr(a, 'context', None) or os.environ.get('XFOUNDRY_KUBE_CONTEXT')


def installed_custom(context: str | None) -> list[str]:
    """The custom resources xFoundry renders that this cluster actually has. Nirvana's collector stops on a resource
    type the cluster doesn't know (the dev platform has no Argo CD, for example), so ask only for installed ones."""
    try:
        out = subprocess.run(['kubectl', *(['--context', context] if context else []), 'get', 'crd', '-o', 'name'],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Could not list the cluster\'s custom resource definitions: {exc}. Nothing was changed.')
    have = {line.split('/', 1)[-1].strip() for line in out.splitlines() if line.strip()}
    return [r for r in model.COLLECT_CUSTOM if r in have]


def collect(output: str, context: str | None, collector: str | None = None) -> str:
    """Run Nirvana's read-only collector (scripts/collect-k8s-snapshot.py) with the custom resources xFoundry renders."""
    script = collector or (Path(os.environ['NIRVANA_DIR']) / 'scripts/collect-k8s-snapshot.py' if os.environ.get('NIRVANA_DIR') else None)
    if not script or not Path(script).exists():
        raise SystemExit('Set NIRVANA_DIR to your Nirvana checkout (or pass --collector) so xFoundry can use its snapshot collector.')
    cmd = [sys.executable, str(script), '-o', output, '--name', context or 'xfoundry']
    if context: cmd += ['--context', context]
    for resource in installed_custom(context): cmd += ['--custom', resource]
    proc = subprocess.run(cmd)
    if proc.returncode: raise SystemExit(f'The collector failed (exit {proc.returncode}). Nothing was changed.')
    return output


def cmd_collect(a):
    collect(a.output, _context(a), a.collector)


def cmd_run(a):
    """The whole loop for one action. Stops without changing anything if the plan is sent back, times out,
    or the gate refuses."""
    LEDGER_DIR.mkdir(exist_ok=True)
    spec = load(a.spec)
    before = collect(str(LEDGER_DIR / f"{spec['name']}.before.snapshot.json"), _context(a), a.collector)
    cmd_plan(argparse.Namespace(spec=a.spec, action=a.action, snapshot=before, model=False))
    rec = read_ledger(spec)['plans'][a.action]
    client, deadline, shown = Nirvana(), time.monotonic() + a.wait * 60, None
    print(f'Waiting up to {a.wait} min for a decision in Nirvana (Changes). Ctrl-C stops waiting; nothing is applied.')
    while True:
        plan = client.get_plan(rec['plan_id'])
        if plan['state'] != shown: print(f"  plan {plan['state']}"); shown = plan['state']
        if plan['state'] == 'approved': break
        if plan['state'] == 'changes_requested':
            raise SystemExit('Sent back in Nirvana' + (f": {plan['decision_note']}" if plan.get('decision_note') else '') + '. Nothing was changed.')
        if time.monotonic() > deadline: raise SystemExit('No decision in time. Nothing was changed; `xfoundry apply` can pick it up later.')
        time.sleep(a.poll)
    fresh = collect(str(LEDGER_DIR / f"{spec['name']}.fresh.snapshot.json"), _context(a), a.collector)
    cmd_apply(argparse.Namespace(spec=a.spec, action=a.action, snapshot=fresh, context=_context(a), dry_run=a.dry_run))


def cmd_worker(a):
    from .worker import main as worker_main
    LEDGER_DIR.mkdir(exist_ok=True)
    worker_main(_context(a), a.collector, a.once)


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
    s.add_argument('--context', help='kubectl context to apply to (default: XFOUNDRY_KUBE_CONTEXT, else the current context)')
    s.add_argument('--dry-run', action='store_true'); s.set_defaults(fn=cmd_apply)
    s = sub.add_parser('collect'); s.add_argument('-o', '--output', default='cluster.snapshot.json')
    s.add_argument('--context'); s.add_argument('--collector', help="path to Nirvana's collect-k8s-snapshot.py (default: $NIRVANA_DIR/scripts/...)")
    s.set_defaults(fn=cmd_collect)
    s = sub.add_parser('run'); s.add_argument('spec'); s.add_argument('--action', choices=ACTIONS, default='create')
    s.add_argument('--context'); s.add_argument('--collector')
    s.add_argument('--wait', type=int, default=30, help='minutes to wait for a decision (default 30)')
    s.add_argument('--poll', type=int, default=5, help='seconds between checks (default 5)')
    s.add_argument('--dry-run', action='store_true'); s.set_defaults(fn=cmd_run)
    s = sub.add_parser('worker', help="carry out jobs queued in Nirvana xStage's console")
    s.add_argument('--context'); s.add_argument('--collector'); s.add_argument('--once', action='store_true', help='one job at most, then exit')
    s.set_defaults(fn=cmd_worker)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except SpecError as exc:
        print('The spec has problems:', *[f'  - {x}' for x in exc.problems], sep='\n', file=sys.stderr); raise SystemExit(1)
    except NirvanaError as exc:
        print(exc, file=sys.stderr); raise SystemExit(3)


if __name__ == '__main__':
    main()
