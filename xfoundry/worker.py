"""The xFoundry worker (ADR 0004): carries out jobs a person queued in Nirvana xStage's console.

It is `xfoundry run` driven from the browser instead of a terminal, and it keeps every rule of the loop:

  claim job ─► check spec ─► collect ─► submit plan ─► wait for a person to approve in xStage
                                                              │
               report status ◄─ kubectl apply ◄─ gate ◄─ collect again

* The worker only claims jobs; it cannot queue, edit or cancel them, and it never approves (its token cannot).
* Nothing is applied without the gate: approved plan, same manifest bytes, same snapshot, no drift, matching
  action, no models. A refusal fails the job; queueing it again makes a new plan.
* A person can cancel until the apply starts. xStage refuses the move to "applying" once cancel was asked.
* Status is reported for the person to see; it is never change evidence.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import signal
import subprocess
import time
from pathlib import Path

import yaml

from . import spec as spec_mod
from .cli import LEDGER_DIR, Refused, apply_plan, collect, read_ledger, submit_plan
from .nirvana import Nirvana, NirvanaError
from .render import namespace_of, render
from .spec import SpecError
from .status import observe

POLL = 3.0          # seconds between check-ins when idle
WAIT_POLL = 5.0     # seconds between decision checks while a plan waits for approval
VERSION = '1.1.0'


POWER_OPS = {'start': 'Started', 'stop': 'Asked to power off', 'restart': 'Restarted', 'force-stop': 'Forced off',
             'force-restart': 'Forced to restart'}


def power_commands(ns: str, op: str, names: list[str], context: str | None = None) -> list[list[str]]:
    """The only kubectl calls power makes. Start and stop set runStrategy (stop shuts the guest down gracefully);
    restart deletes the VM's instance, which KubeVirt starts again from the VM (picking up template changes)."""
    base = ['kubectl', *(['--context', context] if context else []), '-n', ns]
    run = lambda strategy, n: base + ['patch', 'virtualmachine', n, '--type', 'merge', '-p', json.dumps({'spec': {'runStrategy': strategy}})]
    kill = lambda n, force: base + ['delete', 'virtualmachineinstance', n, '--ignore-not-found', '--wait=false',
                                    *(['--grace-period=0', '--force'] if force else [])]
    out = []
    for n in names:
        if op == 'start': out.append(run('Always', n))
        elif op == 'stop': out.append(run('Halted', n))
        elif op == 'restart': out.append(kill(n, False))
        elif op == 'force-stop': out += [run('Halted', n), kill(n, True)]
        elif op == 'force-restart': out.append(kill(n, True))
    return out


def run_kubectl(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    except FileNotFoundError:
        return 127, 'kubectl is not installed here.'
    except subprocess.TimeoutExpired:
        return 124, 'kubectl timed out.'
    return proc.returncode, (proc.stdout + proc.stderr).strip()


CODE = hashlib.sha256(spec_mod.SCHEMA_PATH.read_bytes()).hexdigest()[:12]   # which spec format this copy understands


def hold_lock(path: Path):
    """One worker per folder: a second one would race the first for jobs (and may be an older copy). Returns the
    open lock file, which must stay open while the worker runs; raises SystemExit if another worker holds it."""
    path.parent.mkdir(exist_ok=True)
    handle = open(path, 'a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.seek(0); other = handle.read().strip()
        handle.close()
        raise SystemExit(f"Another xFoundry worker is already running from this folder{f' (process {other})' if other else ''}. "
                         'Close it first: xStage runs the worker for you, so a Terminal window from "Start xFoundry worker.command" is not needed.')
    handle.seek(0); handle.truncate(); handle.write(str(os.getpid())); handle.flush()
    return handle


class Stop(Exception):
    """The job ends here; the message has already been reported."""


class Worker:
    def __init__(self, client: Nirvana | None = None, context: str | None = None, collector: str | None = None,
                 collect_fn=collect, observe_fn=observe, apply_fn=apply_plan, submit_fn=submit_plan, sleep=time.sleep, log=print,
                 power_fn=None):
        self.client = client or Nirvana()
        self.context, self.collector = context, collector
        self.collect, self.observe, self.apply, self.submit = collect_fn, observe_fn, apply_fn, submit_fn
        self.sleep, self.log = sleep, log
        self.power = power_fn or run_kubectl
        self.stopping = False

    # ---- loop

    def info(self) -> dict:
        # pid and code let xStage tell two workers on one token apart, and an old copy from a current one.
        return {'version': VERSION, 'platform': f'{platform.system()} {platform.machine()}', 'cluster': self.context or 'current context',
                'host': platform.node()[:60], 'pid': str(os.getpid()), 'code': CODE}

    def run_forever(self):
        self.log(f'xFoundry worker {VERSION} checking in with {self.client.base} as {self.info()["host"]}. Ctrl-C stops it.')
        while not self.stopping:
            try:
                job = self.client.claim(self.info())
            except NirvanaError as exc:
                self.log(f'Cannot check in: {exc}. Retrying in 15 s.')
                self.sleep(15); continue
            if job: self.handle(job)
            else: self.sleep(POLL)

    def once(self) -> bool:
        """Claim and carry out at most one job. True if there was one."""
        job = self.client.claim(self.info())
        if job: self.handle(job)
        return bool(job)

    # ---- one job

    def say(self, job: dict, message: str, level: str = 'info', **kw):
        self.log(f"[{job['environment_name']} {job['action']}] {message}")
        return self.client.progress(job['id'], message, level, **kw)

    def fail(self, job: dict, message: str):
        try: self.say(job, message, 'error', state='failed', error=message)
        except NirvanaError as exc: self.log(f'Could not report the failure: {exc}')
        raise Stop(message)

    def handle(self, job: dict):
        try:
            self._handle(job)
        except Stop:
            pass
        except SystemExit as exc:   # the CLI pieces stop with a readable message
            self._safe_fail(job, str(exc) or 'Stopped.')
        except NirvanaError as exc:
            self._safe_fail(job, f'Lost contact with xStage: {exc}')
        except Exception as exc:  # never leave a job running silently
            self._safe_fail(job, f'Unexpected error: {exc.__class__.__name__}: {exc}')

    def _safe_fail(self, job, message):
        try: self.fail(job, message)
        except Stop: pass

    def _handle(self, job: dict):
        try:
            env = spec_mod.check(yaml.safe_load(job['spec']))
        except (SpecError, yaml.YAMLError) as exc:
            problems = exc.problems if isinstance(exc, SpecError) else [str(exc)]
            self.fail(job, 'The spec has problems: ' + '; '.join(problems[:12]))
        if env['name'] != job['environment_name']:
            self.fail(job, f"The spec is named {env['name']}, but the environment is {job['environment_name']}.")
        if job['state'] == 'running' and not job.get('plan_id'):
            self.say(job, f"Spec checked: {len(env['networks'])} networks, {len(env.get('appliances', []))} appliances, "
                          f"{sum(g['count'] for g in env.get('vms', []))} VMs, namespace {namespace_of(env)}.")
        if job['action'] == 'check':
            self.report(job, env)
            self.say(job, 'Checked.', state='succeeded')
            return
        if job['action'] == 'power':
            return self.power_step(job, env)
        rec = read_ledger(env)['plans'].get(job['action'])
        if job['state'] == 'applying':
            # Restarted mid-apply. If the plan is gone from the ledger, the apply finished; otherwise apply again
            # through the gate (kubectl apply and delete are idempotent).
            if not rec:
                self.report(job, env)
                self.say(job, 'The apply had finished before the worker restarted.', state='succeeded'); return
            return self.apply_step(job, env)
        if job.get('plan_id') and rec and rec['plan_id'] == job['plan_id']:
            self.say(job, 'Worker restarted; still waiting for the decision on the plan.')
        else:
            self.plan_step(job, env)
            job = self.client.get_job(job['id'])
        self.wait_step(job, env)
        self.apply_step(job, env)

    def plan_step(self, job: dict, env: dict):
        self.say(job, 'Collecting a snapshot of the cluster with xStage\'s read-only collector.')
        before = self.collect(str(LEDGER_DIR / f"{env['name']}.before.snapshot.json"), self.context, self.collector)
        snap = json.loads(Path(before).read_text())
        try:
            plan, _ = self.submit(env, job['action'], snap, client=self.client)
        except SystemExit as exc:
            msg = str(exc)
            if msg.startswith('Nothing to prune'):
                self.say(job, msg + ' Nothing to do.', state='succeeded'); raise Stop(msg)
            raise
        summary = plan.get('status') or 'Plan ready'
        highs = plan.get('highs', 0)
        self.say(job, f"Plan submitted: {summary}{f' · {highs} high finding' + ('s' if highs != 1 else '') if highs else ''}. "
                      'Review and approve it in Changes; nothing changes until you do.', state='waiting_approval', plan_id=plan['id'])

    def wait_step(self, job: dict, env: dict):
        rec = read_ledger(env)['plans'][job['action']]
        last_checkin = 0.0
        while True:
            current = self.client.get_job(job['id'])
            if current['cancel_requested']:
                self.say(job, 'Cancelled before anything was applied.', 'warning', state='cancelled'); raise Stop('cancelled')
            plan = self.client.get_plan(rec['plan_id'])
            if plan['state'] == 'approved': return
            if plan['state'] == 'changes_requested':
                note = plan.get('decision_note')
                self.fail(job, 'Sent back in xStage' + (f': {note}' if note else '') + '. Nothing was changed. Edit the spec and queue it again.')
            if self.stopping: raise Stop('worker stopping; the job resumes when it starts again')
            if time.monotonic() - last_checkin > 15:   # waiting counts as being online
                last_checkin = time.monotonic()
                try: self.client.claim(self.info())
                except NirvanaError: pass
            self.sleep(WAIT_POLL)

    def apply_step(self, job: dict, env: dict):
        try:
            self.say(job, 'Approved. Collecting a fresh snapshot for the gate.', state='applying')
        except NirvanaError as exc:
            if exc.code == 'CANCEL_REQUESTED':
                self.say(job, 'Cancelled before anything was applied.', 'warning', state='cancelled'); raise Stop('cancelled')
            if exc.code != 'BAD_TRANSITION': raise   # already applying (resumed)
        fresh_path = self.collect(str(LEDGER_DIR / f"{env['name']}.fresh.snapshot.json"), self.context, self.collector)
        fresh = json.loads(Path(fresh_path).read_text())
        try:
            cmd, output = self.apply(env, job['action'], fresh, self.context, False, client=self.client)
        except Refused as exc:
            self.fail(job, 'The gate refused, so nothing was changed: ' + '; '.join(exc.reasons) + ' Queue the job again to make a new plan.')
        verb = 'Deleted' if job['action'] != 'create' else 'Applied'
        lines = [l for l in output.splitlines() if l.strip()]
        self.say(job, f"Gate passed. {verb} {len(lines)} object{'s' if len(lines) != 1 else ''} with kubectl.", state='running')
        if job['action'] == 'destroy': self.wait_gone(job, env)
        self.report(job, env)
        self.say(job, 'Done.', state='succeeded')

    def power_step(self, job: dict, env: dict):
        """Start, stop or restart VMs (ADR 0008). No plan and no gate: power changes nothing in the spec. Only VMs this
        environment renders, and only their runStrategy or their running instance; a later apply restores the spec's."""
        p = job.get('power') or {}
        op, wanted = p.get('op'), list(p.get('vms') or [])
        if op not in POWER_OPS: self.fail(job, f'Unknown power operation {op!r}.')
        rendered = [o['metadata']['name'] for o in render(env) if o['kind'] == 'VirtualMachine']
        unknown = [n for n in wanted if n not in rendered]
        if unknown: self.fail(job, f"Not a VM of {env['name']}: {', '.join(unknown)}. Nothing was changed.")
        names = wanted or rendered
        if not names: self.fail(job, f"{env['name']} has no VMs.")
        for cmd in power_commands(namespace_of(env), op, names, self.context):
            code, out = self.power(cmd)
            if code: self.fail(job, f"kubectl failed (exit {code}): {out[-800:]}")
        self.say(job, f"{POWER_OPS[op]}: {', '.join(names)}.")
        self.report(job, env)
        self.say(job, 'Done.', state='succeeded')

    def wait_gone(self, job, env, limit=180):
        """A destroyed namespace takes a moment to go (VM disks are removed first)."""
        start = time.monotonic()
        while time.monotonic() - start < limit:
            if self.observe(env, self.context, None)['state'] == 'absent': return
            self.sleep(5)
        self.say(job, 'The namespace is still being removed; it will finish by itself.', 'warning')

    def report(self, job: dict, env: dict):
        status = self.observe(env, self.context, read_ledger(env).get('applied_manifest'))
        self.client.report_status(job['environment_id'], status)
        self.say(job, f"Status: {status['message']}")


def main(context: str | None, collector: str | None, once: bool = False):
    lock = hold_lock(LEDGER_DIR / 'worker.lock')   # noqa: F841 - held open for the life of the process
    worker = Worker(context=context, collector=collector)
    def stop(*_):
        worker.stopping = True
        worker.log('Stopping after the current step.')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if once: worker.once()
    else: worker.run_forever()
