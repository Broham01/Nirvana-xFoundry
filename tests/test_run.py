"""xfoundry run: collect, plan, wait for the decision, collect again, gate, apply. Collector, Nirvana and kubectl are stand-ins."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from xfoundry import cli, model
from xfoundry.spec import load

EXAMPLE = Path(__file__).resolve().parent.parent / 'examples' / 'edge-lab.yaml'


class FakeNirvana:
    """Approves (or sends back) on the second check; records what was submitted."""
    def __init__(self, decision='approved'):
        self.decision, self.checks, self.plans = decision, 0, {}

    def __call__(self):  # used as the class: Nirvana()
        return self

    def upload_snapshot(self, content, name=None, key=None):
        return {'id': 'snap-1'}

    def create_plan(self, snapshot_id, manifest, title, file_name, delete=False, key=None):
        self.plans['p1'] = {'id': 'p1', 'state': 'review', 'script': manifest, 'snapshot_id': snapshot_id, 'status': 'looks clean', 'highs': 0}
        return self.plans['p1']

    def get_plan(self, plan_id):
        self.checks += 1
        if self.checks >= 2: self.plans[plan_id]['state'] = self.decision
        return self.plans[plan_id]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    snap = model.built(load(EXAMPLE))
    snap['source'] = {**snap['source'], 'kind': 'kubernetes', 'collector': 'collect-k8s-snapshot.py'}
    (tmp_path / 'snap.json').write_text(json.dumps(snap))
    collector = tmp_path / 'collect.py'
    collector.write_text("import shutil, sys\nout = sys.argv[sys.argv.index('-o') + 1]\n"
                         f"open('{tmp_path}/collector.args', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                         f"shutil.copy('{tmp_path}/snap.json', out)\n")
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    kubectl = bin_dir / 'kubectl'
    # `kubectl get crd -o name` lists what the dev platform has: no Argo CD.
    kubectl.write_text(f"#!/bin/sh\ncase \"$*\" in *'get crd -o name'*) printf '%s\\n' "
                       "customresourcedefinition.apiextensions.k8s.io/virtualmachines.kubevirt.io "
                       "customresourcedefinition.apiextensions.k8s.io/vpcs.kubeovn.io "
                       "customresourcedefinition.apiextensions.k8s.io/subnets.kubeovn.io; exit 0;; esac\n"
                       f"echo \"$@\" > {tmp_path}/kubectl.args\ncat > {tmp_path}/kubectl.stdin\n")
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv('PATH', f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(cli.time, 'sleep', lambda s: None)
    return tmp_path, collector


def test_run_applies_only_after_approval(world, monkeypatch):
    tmp, collector = world
    fake = FakeNirvana(); monkeypatch.setattr(cli, 'Nirvana', fake)
    cli.main(['run', str(EXAMPLE), '--collector', str(collector), '--context', 'kind-xfoundry'])
    assert fake.checks >= 3                                       # waited for the decision, then the gate read it again
    assert (tmp / 'kubectl.args').read_text().split() == ['--context', 'kind-xfoundry', 'apply', '-f', '-']
    assert (tmp / 'kubectl.stdin').read_text() == fake.plans['p1']['script']   # exactly the approved bytes
    args = (tmp / 'collector.args').read_text().splitlines()
    assert len(args) == 2 and '--custom vpcs.kubeovn.io' in args[0] and '--context kind-xfoundry' in args[0]
    assert 'applications.argoproj.io' not in args[0]          # not installed on this cluster, so not asked for
    ledger = json.loads((tmp / '.xfoundry/edge-lab.json').read_text())
    assert ledger['applied_manifest'] == fake.plans['p1']['script'] and 'create' not in ledger['plans']


def test_run_stops_when_sent_back(world, monkeypatch):
    tmp, collector = world
    monkeypatch.setattr(cli, 'Nirvana', FakeNirvana('changes_requested'))
    with pytest.raises(SystemExit) as exc:
        cli.main(['run', str(EXAMPLE), '--collector', str(collector)])
    assert 'Sent back' in str(exc.value) and not (tmp / 'kubectl.args').exists()


def test_run_refuses_when_cluster_drifts_while_waiting(world, monkeypatch):
    tmp, collector = world
    fake = FakeNirvana()
    original = fake.get_plan
    def drift_then_answer(plan_id):
        snap = json.loads((tmp / 'snap.json').read_text())
        snap['kubernetes']['nodes'][0]['unschedulable'] = True      # someone cordons a node during the review
        (tmp / 'snap.json').write_text(json.dumps(snap))
        return original(plan_id)
    fake.get_plan = drift_then_answer
    monkeypatch.setattr(cli, 'Nirvana', fake)
    with pytest.raises(SystemExit) as exc:
        cli.main(['run', str(EXAMPLE), '--collector', str(collector)])
    assert exc.value.code == 2 and not (tmp / 'kubectl.args').exists()


def test_collect_needs_a_collector(monkeypatch):
    monkeypatch.delenv('NIRVANA_DIR', raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(['collect'])
    assert 'NIRVANA_DIR' in str(exc.value)
