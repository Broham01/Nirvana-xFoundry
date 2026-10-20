"""Talk to Nirvana's plan API: push snapshots, submit plans, read decisions. Never approves anything.

Endpoints used (Nirvana contracts/openapi.json): POST /api/v1/snapshots, POST /api/v1/plans, GET /api/v1/plans/{id}.
Nirvana's API is currently session + CSRF (browser sign-in). xFoundry authenticates with a bearer service token,
which Nirvana must add first (docs/adr/0002-nirvana-integration.md): it may submit snapshots and plans and read
plans, and can never call /decision. Approval stays with a person in Nirvana.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid


class NirvanaError(RuntimeError):
    pass


class Nirvana:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 150.0):
        self.base = (base_url or os.environ.get('NIRVANA_URL', 'http://localhost:8000')).rstrip('/')
        self.token = token or os.environ.get('NIRVANA_TOKEN', '')
        self.timeout = timeout  # plans can take up to ~2 minutes in Nirvana's sandbox

    def _call(self, method: str, path: str, body: dict | None = None, idempotency_key: str | None = None) -> dict:
        headers = {'Accept': 'application/json'}
        if self.token: headers['Authorization'] = f'Bearer {self.token}'
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
            headers['Idempotency-Key'] = idempotency_key or str(uuid.uuid4())
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors='replace')[:500]
            raise NirvanaError(f'Nirvana {method} {path} -> {exc.code}: {detail}') from exc
        except urllib.error.URLError as exc:
            raise NirvanaError(f'Cannot reach Nirvana at {self.base}: {exc.reason}') from exc

    def upload_snapshot(self, content: dict, name: str | None = None, key: str | None = None) -> dict:
        return self._call('POST', '/api/v1/snapshots', {'name': name, 'content': content}, key)

    def create_plan(self, snapshot_id: str, manifest: str, title: str, file_name: str, delete: bool = False, key: str | None = None) -> dict:
        body = {'snapshot_id': snapshot_id, 'title': title[:120], 'script': manifest, 'file_name': file_name,
                'parameters': {'Mode': 'delete'} if delete else {}}
        return self._call('POST', '/api/v1/plans', body, key)

    def get_plan(self, plan_id: str) -> dict:
        return self._call('GET', f'/api/v1/plans/{plan_id}')
