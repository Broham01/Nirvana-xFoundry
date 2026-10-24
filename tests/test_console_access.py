"""The browser console proxy stays read-only (ADR 0005, 0006): its identity only reads, and its path filter admits
VNC, serial console, pod lists and pod logs, never exec, attach, port-forward or anything else."""
import re
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def accept_paths():
    text = (ROOT / 'platform/dev/console.sh').read_text()
    value = re.search(r"--accept-paths='([^']+)'", text).group(1)
    return [re.compile(p) for p in value.split(',')]


def test_identity_only_reads():
    docs = [d for d in yaml.safe_load_all((ROOT / 'platform/console-rbac.yaml').read_text()) if d]
    role = next(d for d in docs if d['kind'] == 'ClusterRole')
    for rule in role['rules']:
        assert set(rule['verbs']) <= {'get', 'list'}, rule
        assert not any(r.endswith(('/exec', '/attach', '/portforward', '/proxy')) for r in rule['resources']), rule
    assert {'pods', 'pods/log'} <= {r for rule in role['rules'] for r in rule['resources']}


def test_path_filter():
    allowed = accept_paths()
    ok = lambda path: any(p.search(path) for p in allowed)
    assert ok('/k8s/apis/subresources.kubevirt.io/v1/namespaces/xf-dev-lab/virtualmachineinstances/app-01/vnc')
    assert ok('/k8s/api/v1/namespaces/xf-dev-lab/pods')
    assert ok('/api/v1/namespaces/xf-dev-lab/pods/rt01-abc/log')
    for bad in ('/k8s/api/v1/namespaces/xf-dev-lab/pods/rt01-abc/exec', '/k8s/api/v1/namespaces/xf-dev-lab/pods/rt01-abc/attach',
                '/k8s/api/v1/namespaces/xf-dev-lab/pods/rt01-abc/portforward', '/k8s/api/v1/namespaces/xf-dev-lab/secrets',
                '/k8s/api/v1/pods', '/k8s/api/v1/namespaces/xf-dev-lab/pods/rt01-abc', '/k8s/apis/apps/v1/namespaces/xf-dev-lab/deployments'):
        assert not ok(bad), bad
    assert "--reject-methods='^(POST|PUT|PATCH|DELETE|OPTIONS)$'" in (ROOT / 'platform/dev/console.sh').read_text()


def test_log_page_shows_text_only():
    page = (ROOT / 'platform/dev/logs.html').read_text()
    assert 'innerHTML' not in page and 'insertAdjacentHTML' not in page
    assert 'createTextNode' in page


def test_console_page_uses_local_novnc_and_shows_text_only():
    """xStage ADR 0022: the paste and Ctrl+Alt+Del page loads noVNC from the same folder, nothing from the internet."""
    page = (ROOT / 'platform/dev/nirvana-console.html').read_text()
    assert "import RFB from './core/rfb.js'" in page
    assert 'http://' not in page.replace('xmlns', '') and 'https://' not in page
    assert 'innerHTML' not in page and 'sendCtrlAltDel' in page
    assert 'nirvana-console.html' in (ROOT / 'platform/dev/console.sh').read_text()
