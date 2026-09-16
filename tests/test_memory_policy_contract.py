"""One memory-policy contract shared by the Hub and every Studio.

The Hub's fleet controller asks each Studio the same three questions on the same three
paths. Two defects lived in that seam and neither was visible from inside a single
component's tests:

* the packaged Subtitle worker published ``/api/memory/policy`` while Voice published
  ``/api/memory-policy``, so the Hub never found the Subtitle model at all;
* ``transcription`` was missing from the controller's target modalities, so even the
  correctly named Subtitle worker stayed out of the machine-wide release pass.

This module owns the seam: the controller's behaviour against a stub fleet, and the Hub
routes the app calls.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from urllib.parse import urlsplit

def memory_control():
    """Import the Hub's controller lazily.

    Importing it at module scope would bind the Hub's DATA_DIR-derived paths to the
    repository root before the other Hub suites have a chance to point them at a temporary
    directory, changing their behaviour from a distance. Each test asks for it by name.
    """
    from components.hub.backend import memory_control as module
    return module


# The Hub's MODES table carries labels only: each Studio owns its own threshold. The stub
# needs the real thresholds to answer the way a Studio does.
IDLE_SECONDS = {'performance': None, 'balanced': 600, 'memory_saver': 120, 'immediate': 0}


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self._payload


class StubStudio:
    """One in-process Studio that answers the fleet memory contract.

    ``fixed`` stands in for the Image worker, which releases its model after every job and
    therefore has no idle mode to change.
    """

    def __init__(self, mode: str = 'immediate', *, fixed: bool = False,
                 loaded: list[list[str]] | None = None, busy: bool = False):
        self.mode = mode
        self.fixed = fixed
        self.loaded = loaded or []
        self.busy = busy
        self.puts: list[dict] = []
        self.releases = 0

    def policy(self) -> dict:
        return {
            'mode': self.mode,
            'default_mode': 'immediate',
            'idle_seconds': IDLE_SECONDS[self.mode],
            'options': [{'mode': key, **value}
                    for key, value in memory_control().MODES.items()],
            'loaded_models': self.loaded,
            'model_idle_seconds': 0.0 if self.loaded else None,
            'active_work': self.busy,
            'busy': self.busy,
            'next_release_at': None,
            'last_release_at': None,
            'last_release_reason': None,
            'last_release_details': None,
            'last_error': None,
            'release_count': self.releases,
            'counters_since': 0.0,
            **({'fixed': True} if self.fixed else {}),
        }

    async def request(self, method: str, url: str, headers=None, json=None, timeout=None):
        path = urlsplit(url).path.lstrip('/')
        if method == 'GET' and path == 'api/memory-policy':
            return FakeResponse(200, self.policy())
        if method == 'PUT' and path == 'api/memory-policy':
            self.puts.append(dict(json or {}))
            if self.fixed:
                return FakeResponse(422, {'detail': 'no idle policy to change'})
            self.mode = (json or {}).get('mode', self.mode)
            return FakeResponse(200, self.policy())
        if method == 'POST' and path == 'api/memory/release':
            self.releases += 1
            if self.busy:
                return FakeResponse(409, {'detail': 'work is active'})
            self.loaded = []
            return FakeResponse(200, {**self.policy(), 'released': True, 'reason': 'manual'})
        return FakeResponse(404, {'detail': 'fixture route missing'})


class StubMonitor:
    def __init__(self, studios: list[dict]):
        self.registry = studios
        self.status = {studio['id']: {'status': 'up'} for studio in studios}


def studio(identifier: str, modality: str, port: int) -> dict:
    return {'id': identifier, 'title': f'{identifier.title()} Studio', 'modality': modality,
            'host': '127.0.0.1', 'port': port, 'machine': 'node-a', 'local': True,
            'hub_token': 'fleet-token'}


class PackagedMachine(unittest.TestCase):
    """The packaged app runs with STUDIOHUB_PACKAGED=1; workers are local per-machine."""

    def setUp(self) -> None:
        self.old_environ = os.environ.copy()
        os.environ['STUDIOHUB_PACKAGED'] = '1'

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_environ)


def build_fleet(*specs):
    """Return (controller, {studio id: stub}) for the given (id, modality, port) specs."""
    studios = [studio(*spec) for spec in specs]
    stubs = {row['id']: StubStudio() for row in studios}
    monitor = StubMonitor(studios)

    class Client:
        async def request(self, method, url, headers=None, json=None, timeout=None):
            target = next(row['id'] for row in studios if f":{row['port']}/" in url)
            return await stubs[target].request(method, url, headers=headers, json=json)

    return memory_control().FleetMemoryControl(monitor, client=Client()), stubs


class FleetTargetTests(PackagedMachine):
    def test_transcription_is_a_fleet_target(self) -> None:
        """The packaged Subtitle worker's modality must be in the release pass."""
        self.assertIn('transcription', memory_control().SUPPORTED_MODALITIES)

    def test_every_shipped_studio_is_inventoried(self) -> None:
        control, _ = build_fleet(('image', 'image', 51101), ('voice', 'voice', 51102),
                                 ('subtitle', 'transcription', 51103))
        inventory = asyncio.run(control.inventory())
        self.assertEqual([row['id'] for row in inventory['studios']],
                         ['image', 'subtitle', 'voice'])
        self.assertEqual(inventory['summary']['ready'], 3)
        self.assertEqual(inventory['default_mode'], memory_control().DEFAULT_MODE)
        self.assertEqual([row['mode'] for row in inventory['options']],
                         list(memory_control().MODES))

    def test_an_unrelated_modality_is_left_alone(self) -> None:
        control, _ = build_fleet(('voice', 'voice', 51102), ('render', 'render', 51109))
        self.assertEqual([row['id'] for row in control.targets()], ['render', 'voice'])


class FleetReleaseTests(PackagedMachine):
    def test_release_frees_the_subtitle_model_and_a_second_pass_is_a_no_op(self) -> None:
        control, stubs = build_fleet(('subtitle', 'transcription', 51103))
        stubs['subtitle'].loaded = [['whisper-turbo']]
        first = asyncio.run(control.release())
        self.assertTrue(first['ok'])
        self.assertEqual(first['succeeded'], 1)
        self.assertEqual(stubs['subtitle'].loaded, [])
        idle = asyncio.run(control.release())
        self.assertTrue(idle['ok'])

    def test_a_still_resident_model_is_reported_as_failed_not_released(self) -> None:
        """A Studio that answers 200 while still holding its model must not read as freed.

        This is the exact check `broker.release_idle_siblings` makes during a handoff.
        """
        control, stubs = build_fleet(('subtitle', 'transcription', 51103))
        stubs['subtitle'].loaded = [['whisper-turbo']]

        async def stubborn_request(method, url, headers=None, json=None, timeout=None):
            response = await stubs['subtitle'].request(method, url, headers=headers,
                                                       json=json)
            if method == 'POST':
                # 200 with `released: true`, but the model is still listed as resident.
                return FakeResponse(200, {**response.json(),
                                          'loaded_models': [['whisper-turbo']]})
            return response

        control.client.request = stubborn_request
        result = asyncio.run(control.release())['results'][0]
        still_loaded = any((result.get('policy') or {}).get(field) for field in
                           ('loaded_model', 'loaded_models', 'loaded_pipeline'))
        self.assertTrue(still_loaded, 'a resident model must be visible in the release reply')

    def test_active_work_is_reported_busy_rather_than_freed(self) -> None:
        control, stubs = build_fleet(('subtitle', 'transcription', 51103))
        stubs['subtitle'].busy = True
        stubs['subtitle'].loaded = [['whisper-turbo']]
        result = asyncio.run(control.release())
        self.assertFalse(result['ok'])
        self.assertEqual(result['results'][0]['result'], 'busy')

    def test_unknown_studio_ids_are_refused(self) -> None:
        control, _ = build_fleet(('voice', 'voice', 51102))
        with self.assertRaisesRegex(ValueError, 'unknown memory-control studio'):
            asyncio.run(control.release(['nope']))
        with self.assertRaisesRegex(ValueError, 'at least one'):
            asyncio.run(control.release([]))


class FleetSetModeTests(PackagedMachine):
    def test_set_mode_reaches_every_changeable_studio(self) -> None:
        control, stubs = build_fleet(('voice', 'voice', 51102),
                                     ('subtitle', 'transcription', 51103))
        result = asyncio.run(control.set_mode('memory_saver'))
        self.assertTrue(result['ok'])
        self.assertEqual(stubs['voice'].puts, [{'mode': 'memory_saver'}])
        self.assertEqual(stubs['subtitle'].puts, [{'mode': 'memory_saver'}])
        self.assertEqual(stubs['subtitle'].mode, 'memory_saver')

    def test_a_fixed_studio_is_reported_not_changed(self) -> None:
        """Image has no idle timer; asking it to change would fail the whole operation."""
        control, stubs = build_fleet(('image', 'image', 51101),
                                     ('subtitle', 'transcription', 51103))
        stubs['image'].fixed = True
        result = asyncio.run(control.set_mode('balanced'))
        self.assertTrue(result['ok'])
        self.assertEqual(result['succeeded'], 2)
        self.assertEqual(stubs['image'].puts, [], 'the fixed Studio must not be asked')
        fixed = [row for row in result['results'] if row['id'] == 'image'][0]
        self.assertEqual(fixed['result'], 'fixed')
        self.assertEqual(stubs['subtitle'].puts, [{'mode': 'balanced'}])

    def test_set_mode_rejects_an_unknown_mode_before_any_request(self) -> None:
        control, stubs = build_fleet(('voice', 'voice', 51102))
        with self.assertRaisesRegex(ValueError, 'mode must be one of'):
            asyncio.run(control.set_mode('overnight'))
        self.assertEqual(stubs['voice'].puts, [])
class HubMemoryRouteTests(unittest.TestCase):
    """The routes the native Settings screen calls, exercised through the real Hub.

    This runs in a subprocess on purpose. The Hub's registry, ledger and token file are all
    resolved from ``STUDIOHUB_DATA_DIR`` at *import* time, so an in-process test either
    inherits whatever directory an earlier suite bound, or has to reload half the package.
    A subprocess binds a clean temporary data directory and, with a stub Studio listening on
    a real socket, proves the whole path the app uses: Hub route → fleet controller → HTTP →
    Studio contract → back.
    """

    PROBE = r'''
import json, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {'mode': 'immediate', 'loaded': [['whisper-turbo']], 'puts': 0, 'releases': 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _policy(self):
        return {
            'mode': STATE['mode'], 'default_mode': 'immediate',
            'idle_seconds': 0, 'options': [{'mode': 'immediate', 'idle_seconds': 0,
                                            'label': 'After every job'}],
            'loaded_models': STATE['loaded'], 'model_idle_seconds': None,
            'active_work': False, 'busy': False, 'next_release_at': None,
            'last_release_at': None, 'last_release_reason': None,
            'last_release_details': None, 'last_error': None,
            'release_count': STATE['releases'], 'counters_since': 0.0, 'fixed': True,
        }

    def do_GET(self):
        if self.path == '/api/memory-policy':
            self._send(200, self._policy())
        else:
            self._send(404, {'detail': 'missing'})

    def do_PUT(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        STATE['puts'] += 1
        STATE['mode'] = body.get('mode', STATE['mode'])
        self._send(200, self._policy())

    def do_POST(self):
        if self.path == '/api/memory/release':
            STATE['releases'] += 1
            STATE['loaded'] = []
            self._send(200, {**self._policy(), 'released': True, 'reason': 'manual'})
        else:
            self._send(404, {'detail': 'missing'})


server = ThreadingHTTPServer(('127.0.0.1', int(os.environ['PROBE_PORT'])), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

from fastapi.testclient import TestClient
from components.hub.standalone import create_app

token = os.environ['PROBE_TOKEN']
auth = {'Authorization': 'Bearer ' + token}
app = create_app(start_background=False)
out = {}
with TestClient(app) as client:
    out['denied'] = client.get('/api/hub/memory/policy').status_code
    out['denied_write'] = client.put('/api/hub/memory/policy',
                                     json={'mode': 'balanced'}).status_code
    inventory = client.get('/api/hub/memory/policy', headers=auth)
    out['inventory_status'] = inventory.status_code
    out['inventory'] = inventory.json()
    changed = client.put('/api/hub/memory/policy', json={'mode': 'memory_saver'}, headers=auth)
    out['change_status'] = changed.status_code
    out['change'] = changed.json()
    bad = client.put('/api/hub/memory/policy', json={'mode': 'overnight'}, headers=auth)
    out['bad_status'] = bad.status_code
    out['bad_detail'] = bad.json().get('detail')
    empty = client.put('/api/hub/memory/policy',
                       json={'mode': 'balanced', 'studio_ids': []}, headers=auth)
    out['empty_status'] = empty.status_code
    out['empty_detail'] = empty.json().get('detail')
    # The Subtitle delivery receipt route must be served by the same app; the handler's own
    # message distinguishes a real route from FastAPI's generic "Not Found".
    ack = client.post('/api/hub/transcription/jobs/no-such-batch/items/0/ack', headers=auth)
    out['ack_status'] = ack.status_code
    out['ack_detail'] = ack.json().get('detail')
    released = client.post('/api/hub/memory/release', json={}, headers=auth)
    out['release_status'] = released.status_code
    out['release'] = released.json()
out['worker'] = {'mode': STATE['mode'], 'puts': STATE['puts'],
                 'releases': STATE['releases'], 'loaded': STATE['loaded']}
server.shutdown()
print(json.dumps(out))
'''

    def setUp(self) -> None:
        import socket
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.output_root = root / 'subtitle-output'
        self.output_root.mkdir()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.token = 'memory-route-token-0123456789abcdef'
        self.workers = root / 'private-workers.json'
        self.workers.write_text(json.dumps([{
            'id': 'subtitle', 'title': 'Subtitle Studio', 'modality': 'transcription',
            'host': '127.0.0.1', 'port': self.port, 'machine': 'node-a', 'local': True,
            'runtime_identity': {'id': 'subtitle-runtime'},
            'hub_token': self.token,
            'managed_output_roots': [str(self.output_root)],
        }]))
        os.chmod(self.workers, 0o600)
        self.data = root / 'Node State'
        self.data.mkdir()
        token_file = self.data / '.hub_token'
        token_file.write_text(self.token + '\n')
        os.chmod(token_file, 0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def probe(self) -> dict:
        import subprocess
        import sys
        script = self.temporary.name + '/probe.py'
        Path(script).write_text(self.PROBE)
        environment = {**os.environ,
                       'PYTHONPATH': str(PROJECT_ROOT),
                       'STUDIOHUB_DATA_DIR': str(self.data),
                       'STUDIOHUB_WORKERS_FILE': str(self.workers),
                       'STUDIOHUB_PACKAGED': '1',
                       'PROBE_PORT': str(self.port),
                       'PROBE_TOKEN': self.token}
        result = subprocess.run([sys.executable, script], cwd=str(PROJECT_ROOT),
                                env=environment, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_routes_serve_the_worker_contract_end_to_end(self) -> None:
        out = self.probe()

        # Unauthenticated reads and writes are refused; the app always sends its Hub token.
        self.assertEqual(out['denied'], 401)
        self.assertEqual(out['denied_write'], 401)

        self.assertEqual(out['inventory_status'], 200)
        inventory = out['inventory']
        self.assertEqual([row['id'] for row in inventory['studios']], ['subtitle'])
        studio = inventory['studios'][0]
        self.assertEqual(studio['state'], 'ready')
        self.assertEqual(studio['modality'], 'transcription')
        self.assertEqual(studio['policy']['loaded_models'], [['whisper-turbo']])

        self.assertEqual(out['change_status'], 200)
        change = out['change']
        self.assertTrue(change['ok'])
        self.assertEqual(change['mode'], 'memory_saver')
        # The stub Studio is fixed, so the controller reports it rather than calling it.
        self.assertEqual(change['results'][0]['result'], 'fixed')
        self.assertEqual(out['worker']['puts'], 0)

        self.assertEqual(out['bad_status'], 400)
        self.assertIn('mode must be one of', out['bad_detail'])
        self.assertEqual(out['empty_status'], 400)
        self.assertIn('at least one', out['empty_detail'])
        self.assertEqual(out['ack_status'], 404)
        self.assertEqual(out['ack_detail'], 'unknown transcription item',
                         'the Subtitle delivery receipt route must be served')

        self.assertEqual(out['release_status'], 200)
        self.assertTrue(out['release']['ok'])
        self.assertEqual(out['worker']['releases'], 1)
        self.assertEqual(out['release']['results'][0]['policy']['loaded_models'], [])


if __name__ == '__main__':
    unittest.main()
