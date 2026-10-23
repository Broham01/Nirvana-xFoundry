"""The worker (ADR 0004): jobs queued in xStage are planned, wait for a person's approval, pass the gate and are
applied; cancel, send-back, gate refusal and bad specs end the job without changing anything. xStage, the
collector, kubectl and the status reader are stand-ins."""
import json
import os
from pathlib import Path

import pytest

from xfoundry import model
from xfoundry.cli import Refused, read_ledger, submit_plan
from xfoundry.nirvana import NirvanaError
from xfoundry.worker import Worker

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / 'examples' / 'dev-lab.yaml').read_text()
CONTRACT = json.loads((ROOT / 'docs/integration/contract.json').read_text())
MOVES = CONTRACT['console']['worker_may_set']


class FakeXStage:
    """Enforces what xStage enforces for a worker: its own job only, allowed moves only, no apply after cancel."""
    def __init__(self, action='create', decision='approved', spec=SPEC):
        self.job = {'id': 'j1', 'environment_id': 'e1', 'environment_name': 'dev-lab', 'action': action, 'state': 'running', 'spec': spec,
                    'spec_revision': 1, 'plan_id': None, 'cancel_requested': False}
        self.decision, self.plans, self.steps, self.statuses, self.checks, self.base = decision, {}, [], [], 0, 'http://xstage'
        self.claimed = False
        self.cancel_after_checks = None

    def claim(self, info):
        assert info['version']
        if self.job['state'] in ('succeeded', 'failed', 'cancelled'): return None
        if self.claimed and self.job['state'] == 'running' and not self.job['plan_id']: return None
        self.claimed = True
        return dict(self.job)

    def get_job(self, job_id):
        return dict(self.job)

    def progress(self, job_id, message, level='info', state=None, plan_id=None, error=None):
        if self.job['state'] in ('succeeded', 'failed', 'cancelled'): raise NirvanaError('finished', 409, 'JOB_FINISHED')
        if state and state not in MOVES[self.job['state']]: raise NirvanaError('bad move', 409, 'BAD_TRANSITION')
        if state == 'applying' and self.job['cancel_requested']: raise NirvanaError('cancelled', 409, 'CANCEL_REQUESTED')
        if plan_id: self.job['plan_id'] = plan_id
        if state: self.job['state'] = state
        self.steps.append((level, state, message))
        return dict(self.job)

    def report_status(self, env_id, status):
        self.statuses.append(status); return {}

    def upload_snapshot(self, content, name=None, key=None):
        return {'id': 'snap-1'}

    def create_plan(self, snapshot_id, manifest, title, file_name, delete=False, key=None):
        self.plans['p1'] = {'id': 'p1', 'state': 'review', 'script': manifest, 'snapshot_id': snapshot_id, 'status': 'Creates 12 objects', 'highs': 0,
                            'title': title, 'delete': delete}
        return self.plans['p1']

    def get_plan(self, plan_id):
        self.checks += 1
        if self.cancel_after_checks and self.checks >= self.cancel_after_checks: self.job['cancel_requested'] = True
        if self.checks >= 2: self.plans[plan_id]['state'] = self.decision
        return self.plans[plan_id]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XFOUNDRY_ARCH', 'arm64')
    from xfoundry.spec import check
    import yaml
    env = check(yaml.safe_load(SPEC))
    snap = model.built(env)
    snap['source'] = {**snap['source'], 'kind': 'kubernetes', 'collector': 'collect-k8s-snapshot.py'}
    applied = []

    def collect(path, context, collector):
        Path(path).parent.mkdir(exist_ok=True)
        Path(path).write_text(json.dumps(snap)); return path

    def observe(env, context, manifest):
        return {'state': 'absent' if manifest is None else 'ready', 'message': 'ok', 'namespace': 'xf-dev-lab', 'applied': manifest is not None,
                'manifest_hash': None, 'members': [], 'checked_at': '2026-10-21T12:00:00+00:00'}

    def make(xs, apply_fn=None):
        from xfoundry.cli import apply_plan
        def fake_apply(spec, action, fresh, context, dry_run, client=None):
            if apply_fn: return apply_fn(spec, action, fresh, context, dry_run, client)
            # The real gate, then pretend kubectl ran: the ledger is updated exactly as apply_plan does.
            import xfoundry.cli as cli
            real = cli.subprocess.run
            cli.subprocess.run = lambda cmd, **kw: type('P', (), {'returncode': 0, 'stdout': 'namespace/xf-dev-lab created\nvm/app-01 created\n', 'stderr': ''})()
            try:
                out = apply_plan(spec, action, fresh, context, dry_run, client)
            finally:
                cli.subprocess.run = real
            applied.append(action); return out
        return Worker(client=xs, collect_fn=collect, observe_fn=observe, apply_fn=fake_apply, sleep=lambda s: None, log=lambda m: None)
    return make, applied, env, snap


def test_create_job_plans_waits_for_approval_then_applies(world):
    make, applied, env, snap = world
    xs = FakeXStage()
    assert make(xs).once()
    states = [s for _, s, _ in xs.steps if s]
    assert states == ['waiting_approval', 'applying', 'running', 'succeeded'], xs.steps
    assert xs.job['plan_id'] == 'p1' and applied == ['create']
    assert xs.plans['p1']['title'] == 'Create or update environment dev-lab' and not xs.plans['p1']['delete']
    assert xs.statuses[-1]['state'] == 'ready' and read_ledger(env)['applied_manifest']
    assert any('Spec checked: 2 networks, 2 appliances, 2 VMs' in m for _, _, m in xs.steps)


def test_sent_back_changes_nothing(world):
    make, applied, env, snap = world
    xs = FakeXStage(decision='changes_requested')
    make(xs).once()
    assert xs.job['state'] == 'failed' and applied == [] and any('Sent back' in m for _, _, m in xs.steps)


def test_cancel_while_waiting_stops_before_applying(world):
    make, applied, env, snap = world
    xs = FakeXStage(decision='review')
    xs.cancel_after_checks = 1
    make(xs).once()
    assert xs.job['state'] == 'cancelled' and applied == []


def test_cancel_after_approval_is_refused_by_xstage(world):
    make, applied, env, snap = world
    xs = FakeXStage()
    xs.cancel_after_checks = 2   # the person cancels just as the plan is approved
    make(xs).once()
    assert xs.job['state'] == 'cancelled' and applied == []
    assert ('warning', 'cancelled', 'Cancelled before anything was applied.') in xs.steps


def test_gate_refusal_fails_the_job(world):
    make, applied, env, snap = world
    xs = FakeXStage()
    def refuse(*a):
        raise Refused(['the cluster changed since the plan was made (drift)'])
    make(xs, apply_fn=refuse).once()
    assert xs.job['state'] == 'failed' and 'drift' in xs.steps[-1][2] and applied == []


def test_bad_spec_and_wrong_name_fail_before_planning(world):
    make, applied, env, snap = world
    xs = FakeXStage(spec=SPEC.replace('cidr: 10.70.20.0/24', 'cidr: nonsense'))
    make(xs).once()
    assert xs.job['state'] == 'failed' and 'spec has problems' in xs.steps[-1][2] and not xs.plans
    xs = FakeXStage(spec=SPEC.replace('name: dev-lab', 'name: other-lab'))
    make(xs).once()
    assert xs.job['state'] == 'failed' and 'named other-lab' in xs.steps[-1][2]


def test_check_job_reports_status_only(world):
    make, applied, env, snap = world
    xs = FakeXStage(action='check')
    make(xs).once()
    assert xs.job['state'] == 'succeeded' and not xs.plans and xs.statuses and applied == []


def test_destroy_and_nothing_to_prune(world):
    make, applied, env, snap = world
    xs = FakeXStage()
    make(xs).once()
    xs2 = FakeXStage(action='prune')
    make(xs2).once()
    assert xs2.job['state'] == 'succeeded' and 'Nothing to prune' in xs2.steps[-1][2] and not xs2.plans
    xs3 = FakeXStage(action='destroy')
    make(xs3).once()
    assert xs3.job['state'] == 'succeeded' and xs3.plans['p1']['delete'] and applied == ['create', 'destroy']
    assert read_ledger(env)['applied_manifest'] is None and xs3.statuses[-1]['state'] == 'absent'


def test_restart_while_waiting_resumes_the_same_plan(world):
    make, applied, env, snap = world
    xs = FakeXStage(decision='review')
    w = make(xs)
    w.stopping = True   # stops while waiting, as on Ctrl-C
    w.once()
    assert xs.job['state'] == 'waiting_approval' and len(xs.plans) == 1
    xs.decision, w.stopping = 'approved', False
    w.once()
    assert xs.job['state'] == 'succeeded' and len(xs.plans) == 1, 'no second plan on resume'
    assert any('still waiting for the decision' in m for _, _, m in xs.steps)


def test_only_one_worker_per_folder(tmp_path):
    from xfoundry.worker import CODE, Worker, hold_lock
    first = hold_lock(tmp_path / 'worker.lock')
    with pytest.raises(SystemExit) as exc:
        hold_lock(tmp_path / 'worker.lock')
    assert 'Another xFoundry worker is already running' in str(exc.value) and str(os.getpid()) in str(exc.value)
    first.close()
    hold_lock(tmp_path / 'worker.lock').close()      # free again once the first one stops
    info = Worker(client=object()).info()
    assert info['pid'] == str(os.getpid()) and info['code'] == CODE and len(CODE) == 12
