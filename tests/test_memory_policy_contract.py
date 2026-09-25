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

Since the fifth mode, ``auto``, this module also owns the *rule*: every Studio used to
default to ``immediate`` and each carried its own copy of the four-mode table. ``auto``
resolves per machine (``balanced`` at 16 GiB of unified memory and above, ``immediate``
below it, and ``immediate`` when the memory cannot be measured at all), so the answer is
driven here through each worker's own resolution function on both machine sizes rather than
restated as a literal in four files.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
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


def shared_modes():
    """The one table and rule every Studio and the Hub read.

    Imported by name rather than restated: this module proves the workers *share* it, so a
    copy of the 16 GiB threshold in a test would be a fifth place for it to be wrong.
    """
    from components import memory_modes as module
    return module


# The thresholds a stub Studio answers with, from the shared table — the Hub's own MODES
# table carries labels only, because each Studio owns the window it actually runs with.
IDLE_SECONDS = {mode: entry['idle_seconds']
                for mode, entry in shared_modes().MODES.items()}


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
    therefore has no idle mode to change. ``machine`` is what ``auto`` answers on this stub's
    Mac, so the stub publishes the resolution the way a real Studio does.
    """

    def __init__(self, mode: str = 'immediate', *, fixed: bool = False,
                 loaded: list[list[str]] | None = None, busy: bool = False,
                 machine: str = 'immediate'):
        self.mode = mode
        self.fixed = fixed
        self.loaded = loaded or []
        self.busy = busy
        self.machine = machine
        self.puts: list[dict] = []
        self.releases = 0

    def policy(self) -> dict:
        resolved = self.machine if self.mode == 'auto' else self.mode
        return {
            'mode': self.mode,
            'resolved_mode': resolved,
            'default_mode': shared_modes().DEFAULT_MODE,
            'idle_seconds': IDLE_SECONDS[resolved],
            'machine_mode': self.machine,
            'options': shared_modes().options(self.machine),
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

    def test_the_hub_offers_the_same_five_modes_and_drafts_auto(self) -> None:
        """The Hub renders labels and validates names; it must not carry its own list.

        Each Studio owns the window it runs with, so the Hub's table has no thresholds — but
        the names and labels are the shared table's, so `auto` cannot exist in every Studio
        and nowhere in the Hub.
        """
        hub = memory_control()
        shared = shared_modes()
        self.assertEqual(list(hub.MODES), list(shared.MODES))
        for mode, entry in hub.MODES.items():
            with self.subTest(mode=mode):
                self.assertEqual(entry['label'], shared.MODES[mode]['label'])
                self.assertNotIn('idle_seconds', entry,
                                 "the Hub must not restate a threshold it does not enforce")
        self.assertEqual(hub.DEFAULT_MODE, 'auto')
        self.assertEqual(hub.DEFAULT_MODE, shared.DEFAULT_MODE)


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
        operation = asyncio.run(control.release())
        result = operation['results'][0]
        # The controller itself must refuse to call this a release. The app reads `ok` and
        # `succeeded`, so a 200 with the model still listed has to arrive as a failure - and
        # the fleet handoff must agree, which is why both paths share one check.
        self.assertFalse(result['ok'], 'a Studio still holding its model was reported as released')
        self.assertEqual(result['result'], 'still_loaded')
        self.assertFalse(operation['ok'])
        self.assertEqual(operation['succeeded'], 0)
        self.assertEqual(operation['failed'], 1)
        self.assertIn('whisper-turbo', result['detail'])

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

    def test_release_only_touches_the_selected_studios(self) -> None:
        """A per-device release must not free a sibling the operator did not choose."""
        control, stubs = build_fleet(('voice', 'voice', 51102),
                                     ('subtitle', 'transcription', 51103))
        stubs['voice'].loaded = [['qwen-base']]
        stubs['subtitle'].loaded = [['whisper-turbo']]
        result = asyncio.run(control.release(['voice']))
        self.assertTrue(result['ok'])
        self.assertEqual(stubs['voice'].releases, 1)
        self.assertEqual(stubs['subtitle'].releases, 0,
                         'an unselected Studio must not be released')
        self.assertEqual(stubs['subtitle'].loaded, [['whisper-turbo']])


class HandoffTests(PackagedMachine):
    """What happens when a job arrives on a Mac whose memory another Studio is holding.

    This is the scheduler side of the contract the fleet release pass implements, and it is
    the behaviour an operator notices on an 8 GB Mac: an Image generation finishes, a Voice
    job arrives, and the Voice job must not simply wait for someone to notice.
    """

    def setUp(self) -> None:
        super().setUp()
        # Imported here for the same DATA_DIR reason `memory_control()` is.
        from components.hub.backend import broker, chat_jobs, transcription_jobs
        self.broker = broker
        self.chat_jobs = chat_jobs
        self.transcription_jobs = transcription_jobs
        self.addCleanup(self.chat_jobs.busy_studios.clear)
        self.addCleanup(self.transcription_jobs.busy_studios.clear)
        self.addCleanup(self.broker._handoff_state.clear)

    def handoff(self, specs, target_id):
        control, stubs = build_fleet(*specs)
        monitor = control.monitor
        # `release_idle_siblings` builds its own controller from the monitor, so the stub
        # fleet's client has to travel with it.
        monitor._client = control.client
        with patch.object(self.broker, '_monitor', lambda: monitor), \
             patch.object(self.broker, '_busy', set()):
            result = asyncio.run(self.broker.release_idle_siblings(None, monitor.registry[
                next(index for index, row in enumerate(monitor.registry)
                     if row['id'] == target_id)]))
        return result, stubs

    def test_an_idle_sibling_releases_its_model_for_the_incoming_job(self) -> None:
        """The FLUX model Image is holding must not make the Voice job wait for a human."""
        result, stubs = self.handoff(
            (('image', 'image', 51101), ('voice', 'voice', 51102)), 'voice')

        self.assertEqual(result['attempted'], 1)
        self.assertEqual(result['released'], 1)
        self.assertEqual(result['busy'], [])
        self.assertEqual(result['failed'], [])
        # The sibling was actually asked, not merely counted.
        self.assertGreaterEqual(stubs['image'].releases, 1)

    def test_a_sibling_that_is_generating_is_never_preempted(self) -> None:
        """Releasing a model out from under running work would kill the job, not the model."""
        self.transcription_jobs.busy_studios.add('subtitle')
        result, stubs = self.handoff(
            (('subtitle', 'transcription', 51103), ('voice', 'voice', 51102)), 'voice')

        self.assertEqual(result['released'], 0)
        self.assertEqual(result['busy'], ['subtitle'])
        self.assertEqual(stubs['subtitle'].releases, 0,
                         'a busy Studio must not even be asked to release')

    def test_a_second_handoff_on_the_same_machine_is_deferred(self) -> None:
        """Three scheduler lanes must not all clear the same Mac in the same instant."""
        specs = (('image', 'image', 51101), ('voice', 'voice', 51102))
        first, _ = self.handoff(specs, 'voice')
        self.assertFalse(first.get('deferred'))
        second, stubs = self.handoff(specs, 'voice')
        self.assertTrue(second.get('deferred'))
        self.assertLessEqual(stubs['image'].releases, 1)


class FleetSetModeTests(PackagedMachine):
    def test_set_mode_reaches_every_changeable_studio(self) -> None:
        control, stubs = build_fleet(('voice', 'voice', 51102),
                                     ('subtitle', 'transcription', 51103))
        result = asyncio.run(control.set_mode('memory_saver'))
        self.assertTrue(result['ok'])
        self.assertEqual(stubs['voice'].puts, [{'mode': 'memory_saver'}])
        self.assertEqual(stubs['subtitle'].puts, [{'mode': 'memory_saver'}])
        self.assertEqual(stubs['subtitle'].mode, 'memory_saver')

    def test_set_mode_auto_reaches_every_changeable_studio_and_settles_a_fixed_one(self) -> None:
        """The new default is a real selection an operator can make — and Image stays fixed."""
        control, stubs = build_fleet(('image', 'image', 51101),
                                     ('voice', 'voice', 51102),
                                     ('subtitle', 'transcription', 51103))
        stubs['image'].fixed = True
        result = asyncio.run(control.set_mode('auto'))
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'auto')
        self.assertEqual(stubs['image'].puts, [], 'the fixed Studio must not be asked')
        self.assertEqual([row['result'] for row in result['results'] if row['id'] == 'image'],
                         ['fixed'])
        self.assertEqual(stubs['voice'].puts, [{'mode': 'auto'}])
        self.assertEqual(stubs['subtitle'].puts, [{'mode': 'auto'}])
        # And the answer that comes back is the studio's resolution, not the Hub's guess.
        self.assertEqual(stubs['voice'].policy()['mode'], 'auto')
        self.assertEqual(stubs['voice'].policy()['resolved_mode'], 'immediate')

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

    def test_set_mode_only_reaches_the_selected_studios(self) -> None:
        """A per-device mode change must not fan out to an unselected sibling."""
        control, stubs = build_fleet(('voice', 'voice', 51102),
                                     ('subtitle', 'transcription', 51103))
        result = asyncio.run(control.set_mode('memory_saver', ['voice']))
        self.assertTrue(result['ok'])
        self.assertEqual(stubs['voice'].puts, [{'mode': 'memory_saver'}])
        self.assertEqual(stubs['voice'].mode, 'memory_saver')
        self.assertEqual(stubs['subtitle'].puts, [],
                         'an unselected Studio must not be changed')
        self.assertEqual(stubs['subtitle'].mode, 'immediate')
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
    # The fifth mode must survive the same route: the Hub validates mode names from its own
    # table, so a studio the operator sets to `auto` must not come back "invalid policy".
    auto = client.put('/api/hub/memory/policy', json={'mode': 'auto'}, headers=auth)
    out['auto_status'] = auto.status_code
    out['auto'] = auto.json()
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

        # `auto` is a valid mode on the same route. A Hub that refused it would make the new
        # default unreachable from the Settings screen.
        self.assertEqual(out['auto_status'], 200)
        self.assertEqual(out['auto']['mode'], 'auto')
        self.assertEqual(out['auto']['results'][0]['result'], 'fixed',
                         'a fixed Studio is settled, not sent a request it must reject')
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


class _NoJobs:
    """The minimum an Image policy reads: the jobs it is running."""

    def list(self) -> list:
        return []


def studio_policy_modules() -> dict:
    """Every shipped Studio's memory policy, by component name.

    The walk is over the real component tree rather than a list written here, so a Studio
    added tomorrow is examined without anyone remembering to add it.  The module is
    imported from its real package path, so this reads the code that actually ships.
    """
    modules = {}
    for worker in sorted((PROJECT_ROOT / "components").glob("*/app/backend/main.py")):
        name = worker.parents[2].name
        modules[name] = importlib.import_module(
            f"components.{name}.app.backend.memory_policy")
    return modules


class EveryStudioAnswersTheMemoryContract(unittest.TestCase):
    """The invariant the Hub depends on: every Studio answers the memory contract.

    `FleetMemoryControl.targets()` treats every worker modality as a memory-control target,
    and the machine-wide release pass and the fleet handoff both ask each one.  A Studio that
    ships with no answer is rendered "Needs an update" (a control the owner cannot use) and
    counted as a failed release.  A Studio that holds nothing still has to say so - that is
    what Image's fixed single-mode policy is for - so the next resident Studio cannot ship
    without controls by simply omitting them.
    """

    def test_the_walk_finds_every_shipped_studio(self) -> None:
        """A walk that silently finds nothing would make this module green by construction."""
        names = set(studio_policy_modules())
        self.assertTrue(names, "the component walk found no Studio backend at all")
        self.assertLessEqual({"image", "music", "voice", "subtitle", "chat"}, names,
                             "a shipped Studio is missing from the component walk")

    def test_every_studio_worker_serves_the_three_fleet_routes(self) -> None:
        for worker in sorted((PROJECT_ROOT / "components").glob("*/app/backend/main.py")):
            name = worker.parents[2].name
            source = worker.read_text(encoding="utf-8")
            with self.subTest(studio=name):
                self.assertIn("/api/memory-policy", source,
                              f"{name} does not serve GET/PUT /api/memory-policy")
                self.assertIn("/api/memory/release", source,
                              f"{name} does not serve POST /api/memory/release")
                self.assertTrue((worker.parent / "memory_policy.py").is_file(),
                                f"{name} has no memory_policy.py for the Hub to read")

    def test_every_changeable_studio_publishes_the_one_mode_table(self) -> None:
        """The five modes and their thresholds cannot drift between Studios.

        Identity, not equality: a worker that copied the table would pass an equality check
        today and be free to disagree tomorrow, which is the defect the fifth mode would
        otherwise introduce — `auto`'s answer *is* the contract, so there is one copy of it.
        """
        shared = shared_modes()
        voice = importlib.import_module("components.voice.app.backend.memory_policy")
        self.assertEqual(list(shared.MODES),
                         ["auto", "performance", "balanced", "memory_saver", "immediate"])
        for name, module in studio_policy_modules().items():
            if getattr(module, "FIXED", False):
                continue
            with self.subTest(studio=name):
                self.assertIs(module.MODES, shared.MODES,
                              f"{name} carries its own copy of the mode table")
                self.assertEqual(module.DEFAULT_MODE, shared.DEFAULT_MODE)
                self.assertEqual(module.DEFAULT_MODE, "auto",
                                 f"{name}'s default is not the fifth mode")
                self.assertEqual(module.default_mode(), "auto",
                                 f"{name} does not select the default")
                self.assertEqual(module.MODES, voice.MODES)

    def test_image_is_the_only_fixed_policy_and_keeps_its_single_mode(self) -> None:
        """Image is deliberately correct as it stands: mflux exits after every job.

        The Hub renders a one-mode ("fixed") policy as a sentence rather than four buttons
        that would do nothing, so nobody should "fix" Image by giving it modes - and nobody
        should declare a second fixed Studio to dodge the control either.
        """
        modules = studio_policy_modules()
        voice = importlib.import_module("components.voice.app.backend.memory_policy")
        fixed = sorted(name for name, module in modules.items()
                       if getattr(module, "FIXED", False))
        self.assertEqual(fixed, ["image"],
                         "Image is the documented one-mode exception; every other Studio "
                         "has a real residency choice")
        image = modules["image"]
        self.assertEqual(set(image.MODES), {"immediate"})
        self.assertEqual(image.MODES["immediate"]["idle_seconds"],
                         voice.MODES["immediate"]["idle_seconds"])

        row = image.status(_NoJobs())
        self.assertIs(row["fixed"], True)
        self.assertEqual(row["mode"], "immediate")
        self.assertEqual(row["idle_seconds"], 0)
        self.assertEqual(row["options"],
                         [{"mode": "immediate", "idle_seconds": 0,
                           "label": "After every job"}])
        self.assertEqual(row["loaded_models"], [])
        self.assertEqual(row["release_policy"],
                         "The model is released when each job finishes.")

    def test_music_publishes_the_five_modes_and_the_shared_default(self) -> None:
        """The regression this contract exists to catch: Music had no policy at all."""
        music = studio_policy_modules()["music"]
        shared = shared_modes()
        self.assertFalse(getattr(music, "FIXED", False))
        self.assertIs(music.MODES, shared.MODES)
        self.assertEqual(music.DEFAULT_MODE, "auto")
        self.assertIsNone(music.MODES["auto"]["idle_seconds"],
                          "auto has no threshold of its own; the machine's answer is "
                          "published resolved")
        self.assertEqual(music.MODES["performance"]["idle_seconds"], None)
        self.assertEqual(music.MODES["balanced"]["idle_seconds"], 600)
        self.assertEqual(music.MODES["memory_saver"]["idle_seconds"], 120)
        self.assertEqual(music.MODES["immediate"]["idle_seconds"], 0)


class _PolicyEngine:
    """The minimum a real ``MemoryPolicy`` reads, for driving Chat's status()."""

    model_id = "mlx-community/Qwen3.5-9B-4bit"

    def __init__(self, *, loaded: bool = False, busy: bool = False, idle_for: float = 0.0):
        self._loaded = loaded
        self._busy = busy
        self._last = time.time() - idle_for

    def loaded(self) -> bool:
        return self._loaded

    def status(self) -> dict:
        return {"busy": self._busy}

    def last_activity_at(self) -> float:
        return self._last

    def release(self, *, reason: str = "manual") -> dict:
        if self._busy:
            raise RuntimeError("a generation is running; release was refused")
        was, self._loaded = self._loaded, False
        return {"released": was, "reason": reason}


def changeable_policy_modules() -> dict:
    """Every Studio that has a real residency choice, by component name."""
    return {name: module for name, module in studio_policy_modules().items()
            if not getattr(module, "FIXED", False)}


def worker_status(module, name: str, directory: Path, total_bytes: int) -> dict:
    """One worker's real ``status()``, with this machine's memory injected.

    The reading is injected at the worker's own ``machine_bytes`` — the seam the rule is
    written against — rather than by reimplementing the rule here.
    """
    settings = Path(directory) / "memory_policy.json"
    with patch.object(module, "machine_bytes", lambda: total_bytes):
        if name == "chat":
            return module.MemoryPolicy(Path(directory), _PolicyEngine()).status()
        if name == "voice":
            with patch.object(module, "SETTINGS_FILE", settings), \
                 patch.object(module, "_GEN_MANAGER", None), \
                 patch.object(module, "_STT_MANAGER", None):
                return module.status()
        with patch.object(module, "settings_file", lambda: settings), \
             patch.object(module, "_MANAGER", None):
            return module.status()


def selected_mode(module, name: str, settings: Path, directory: Path) -> str:
    """The mode one worker selects from a settings file, through its own reader."""
    if name == "chat":
        policy = module.MemoryPolicy(directory, _PolicyEngine())
        policy._settings = settings
        return policy.mode()
    if name == "voice":
        with patch.object(module, "SETTINGS_FILE", settings):
            return module._read()["mode"]
    with patch.object(module, "settings_file", lambda: settings):
        return module._mode()


class AutoIsAnsweredByThisMachine(unittest.TestCase):
    """`auto` is the default, and this machine — not a literal in four files — answers it.

    The rule: at 16 GiB of unified memory and above `auto` behaves as `balanced`; below it
    as `immediate`; a machine whose memory cannot be measured must not hold a model, so it
    gets `immediate` too. Every case below is driven through each worker's own function.
    """

    # (unified memory in bytes, the mode auto must resolve to)
    MACHINES = ((8, "immediate"), (15, "immediate"),
                (16, "balanced"), (24, "balanced"), (32, "balanced"))

    def test_auto_resolves_per_machine_in_every_worker(self) -> None:
        modules = changeable_policy_modules()
        self.assertGreaterEqual(len(modules), 4, "the walk found fewer workers than ship")
        for name, module in modules.items():
            for gib, expected in self.MACHINES:
                with self.subTest(studio=name, gib=gib):
                    total = gib * 1024**3
                    with patch.object(module, "machine_bytes", lambda t=total: t):
                        self.assertEqual(module.machine_mode(), expected)
                        self.assertEqual(module.resolved_mode("auto"), expected)
                        self.assertEqual(
                            module.idle_seconds("auto"),
                            600 if expected == "balanced" else 0,
                            f"{name} runs auto with the wrong window")

    def test_an_unmeasurable_machine_never_holds_a_model(self) -> None:
        """A Mac that cannot prove it has room resolves to immediate, never balanced."""
        for name, module in changeable_policy_modules().items():
            with self.subTest(studio=name):
                with patch.object(module, "machine_bytes", lambda: 0):
                    self.assertEqual(module.machine_mode(), "immediate")
                    self.assertEqual(module.resolved_mode("auto"), "immediate")
                    self.assertEqual(module.idle_seconds("auto"), 0)
        # And the shared reader itself: an unreadable page count is 0, not a guess. A larger
        # machine is what `balanced` costs, so guessing high is the expensive direction.
        with patch.object(shared_modes().os, "sysconf",
                          side_effect=ValueError("no sysconf here")):
            self.assertEqual(shared_modes().machine_bytes(), 0)
            self.assertEqual(shared_modes().machine_mode(), "immediate")

    def test_status_publishes_the_resolution_not_just_the_name(self) -> None:
        """`idle_seconds` is never None for auto: a UI reading None renders a lie."""
        modules = changeable_policy_modules()
        with tempfile.TemporaryDirectory() as temporary:
            for name, module in modules.items():
                for gib, expected in ((8, "immediate"), (32, "balanced")):
                    with self.subTest(studio=name, gib=gib):
                        row = worker_status(module, name, Path(temporary), gib * 1024**3)
                        self.assertEqual(row["mode"], "auto")
                        self.assertEqual(row["resolved_mode"], expected)
                        self.assertEqual(row["machine_mode"], expected)
                        self.assertEqual(row["idle_seconds"],
                                         600 if expected == "balanced" else 0)
                        self.assertIsNotNone(row["idle_seconds"])
                        # The fields the Hub and the app already read must survive the
                        # fifth mode; a Studio that drops one is counted as released while
                        # still holding its model.
                        for field in ("loaded_models", "model_idle_seconds", "active_work",
                                      "busy", "next_release_at", "last_release_at",
                                      "last_release_reason", "last_release_details",
                                      "last_error", "release_count", "counters_since",
                                      "default_mode", "machine_bytes"):
                            self.assertIn(field, row, f"{name} dropped {field}")

    def test_every_option_carries_its_own_resolved_threshold_and_a_sentence(self) -> None:
        modules = changeable_policy_modules()
        with tempfile.TemporaryDirectory() as temporary:
            for name, module in modules.items():
                for gib, expected in ((8, "immediate"), (32, "balanced")):
                    with self.subTest(studio=name, gib=gib):
                        row = worker_status(module, name, Path(temporary), gib * 1024**3)
                        options = {option["mode"]: option for option in row["options"]}
                        self.assertEqual(list(options), list(shared_modes().MODES),
                                         "options must offer every mode, auto included")
                        self.assertEqual(options["auto"]["idle_seconds"],
                                         600 if expected == "balanced" else 0,
                                         "auto's option must carry the resolved threshold")
                        self.assertEqual(options["performance"]["idle_seconds"], None)
                        self.assertEqual(options["balanced"]["idle_seconds"], 600)
                        self.assertEqual(options["memory_saver"]["idle_seconds"], 120)
                        self.assertEqual(options["immediate"]["idle_seconds"], 0)
                        for mode, option in options.items():
                            self.assertEqual(option["label"],
                                             shared_modes().MODES[mode]["label"])
                            self.assertTrue(str(option["detail"]).strip(),
                                            f"{mode} has no detail for the operator")
                        # The auto sentence must name this machine's answer and its
                        # threshold; "Follows this Mac" alone tells the operator nothing.
                        detail = options["auto"]["detail"]
                        self.assertIn("Follows this Mac", detail)
                        if expected == "balanced":
                            self.assertIn("10 idle minutes", detail)
                        else:
                            self.assertIn("as soon as each job finishes", detail)

    def test_an_absent_or_unknown_saved_mode_yields_the_default(self) -> None:
        """Nothing on disk, or something unreadable, selects `auto` — never a crash."""
        modules = changeable_policy_modules()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, module in modules.items():
                for saved in (None, '{"mode": "overnight"}', "not json at all",
                              '{"mode": null}'):
                    with self.subTest(studio=name, saved=saved):
                        settings = directory / f"{name}.json"
                        settings.unlink(missing_ok=True)
                        if saved is not None:
                            settings.write_text(saved, encoding="utf-8")
                        self.assertEqual(selected_mode(module, name, settings, directory),
                                         "auto")
                        self.assertEqual(module.default_mode(), "auto")

    def test_the_existing_modes_still_mean_exactly_what_they_meant(self) -> None:
        """A fixed mode is never re-interpreted by the machine, whatever it measures."""
        for name, module in changeable_policy_modules().items():
            for gib in (8, 32):
                with self.subTest(studio=name, gib=gib):
                    with patch.object(module, "machine_bytes", lambda: gib * 1024**3):
                        self.assertEqual(module.idle_seconds("immediate"), 0)
                        self.assertIsNone(module.idle_seconds("performance"))
                        self.assertEqual(module.idle_seconds("balanced"), 600)
                        self.assertEqual(module.idle_seconds("memory_saver"), 120)
                        for mode in ("performance", "balanced", "memory_saver", "immediate"):
                            self.assertEqual(module.resolved_mode(mode), mode)

    def test_the_auto_rule_is_written_down_once(self) -> None:
        """No worker restates the threshold or the balanced/immediate choice.

        This is the negative control for the rule: a copy of `16 * 1024**3` or of the
        `balanced`/`immediate` answer in a Studio's memory policy is the drift this design
        exists to prevent, and it would still pass every behavioural test above.
        """
        shared_source = Path(inspect.getsourcefile(shared_modes())).read_text(encoding="utf-8")
        self.assertIn("AUTO_BALANCED_MIN_BYTES", shared_source)
        self.assertIn("16 * 1024**3", shared_source)
        threshold = re.compile(r"1024\s*\*\*\s*3|1024\s*\*\s*3|16\s*\*\s*1024")
        for name, module in changeable_policy_modules().items():
            source = Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
            with self.subTest(studio=name):
                self.assertIsNone(threshold.search(source),
                                  f"{name} restates the 16 GiB threshold instead of "
                                  f"importing components/memory_modes.py")

    def test_the_shared_table_is_staged_into_every_bundle(self) -> None:
        """The Voice worker imports it too, so it must ship in a Voice-alone artifact."""
        spec = importlib.util.spec_from_file_location(
            "stage_voice_for_memory_test", PROJECT_ROOT / "packaging" / "stage_voice.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        staged = {path.name for path in module.shared_components_modules(
            PROJECT_ROOT / "components")}
        self.assertIn("memory_modes.py", staged,
                      "the mode table and auto rule must be staged, or every worker fails "
                      "at import in the bundle")


class AutoNeverPreemptsWork(unittest.TestCase):
    """`auto` chooses a threshold. It cannot override the refusal that protects a job."""

    def test_a_busy_model_is_not_idle_released_under_auto(self) -> None:
        from components.chat.app.backend import memory_policy

        with tempfile.TemporaryDirectory() as temporary:
            engine = _PolicyEngine(loaded=True, busy=True, idle_for=10_000)
            policy = memory_policy.MemoryPolicy(Path(temporary), engine)
            self.assertEqual(policy.mode(), "auto")
            with patch.object(memory_policy, "machine_bytes", lambda: 32 * 1024**3):
                self.assertEqual(policy.resolved_mode(), "balanced")
                self.assertEqual(policy.idle_seconds(), 600)
                # Well past the 600 s window, and still refused: the job is not preempted.
                policy.tick()
            self.assertTrue(engine.loaded(), "auto released a model out from under a job")

    def test_a_busy_subtitle_model_is_not_released_under_auto(self) -> None:
        from components.subtitle.app.backend import memory_policy

        with tempfile.TemporaryDirectory() as temporary:
            settings = Path(temporary) / "memory_policy.json"
            with patch.object(memory_policy, "settings_file", lambda: settings), \
                 patch.object(memory_policy, "machine_bytes", lambda: 32 * 1024**3), \
                 patch.object(memory_policy, "_MANAGER", None), \
                 patch.object(memory_policy, "_active", return_value=True):
                self.assertEqual(memory_policy.status()["mode"], "auto")
                with self.assertRaisesRegex(RuntimeError, "active"):
                    memory_policy.release_now("idle")


class _VerifiedPackageCache:
    """A package view that reports every model cached, so no weights are needed."""

    def __init__(self, *unused, library=None, **also_unused):
        self.root = Path(library) / "installed" / "model"
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self, **unused):
        return {"state": "cached", "checkpoints_root": str(self.root)}


class _MusicResidentEngine:
    """A stand-in for the Music worker's resident engine child."""

    def __init__(self):
        self._pid = 2468
        self.discards = 0

    @property
    def child_pid(self):
        return self._pid

    def _discard(self):
        self.discards += 1
        self._pid = None
        return True

    def close(self):
        self._discard()


class MusicRowThroughTheHubController(PackagedMachine):
    """`GET /api/hub/memory/policy` must show the Music row as ready, with its four modes.

    The native Settings screen reads that route, and the route reads every Studio through
    `FleetMemoryControl.inventory()`.  This drives the real controller against the real
    Music FastAPI app and the real `GenerationManager`; only the transport is an adapter
    onto its TestClient, since a socket would not change the contract being read.
    """

    def setUp(self) -> None:
        super().setUp()
        if importlib.util.find_spec("fastapi") is None:
            self.skipTest("FastAPI is not installed in the system test interpreter")
        from fastapi.testclient import TestClient
        from components.music.app.backend import catalog, generation, memory_policy
        from components.music.app.backend.main import create_app

        self.catalog = catalog
        self.memory_policy = memory_policy
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root / "models").mkdir()
        (root / "engine").mkdir()
        with patch.object(generation.cache_module, "MusicCache", _VerifiedPackageCache):
            self.manager = generation.GenerationManager(
                root / "data", engine_root=str(root / "engine"),
                library_dir=str(root / "models"), runtime_sha256="f" * 64)
        self.addCleanup(self.manager.close)
        self.addCleanup(self._unbind_policy)
        self.manager._engine_contract_reason = lambda: None
        self.client = TestClient(create_app(self.manager, "secret"))

    def _unbind_policy(self) -> None:
        self.memory_policy._MANAGER = None
        self.memory_policy._STATE_DIR = None

    def control(self):
        # The packaged Hub registers each owned worker with the same credential in both
        # fields (`hub_worker.worker_specs`), which is what the worker's middleware checks.
        studios = [{"id": "music", "title": "Music Studio", "modality": "music",
                    "host": "127.0.0.1", "port": 51107, "machine": "local",
                    "local": True, "hub_token": "secret", "studio_token": "secret"}]
        app_client = self.client

        class Client:
            async def request(self, method, url, headers=None, json=None, timeout=None):
                response = app_client.request(method, urlsplit(url).path,
                                              headers=headers, json=json)
                return FakeResponse(response.status_code, response.json())

        class Monitor:
            registry = studios
            status = {"music": {"status": "up"}}

        return memory_control().FleetMemoryControl(Monitor(), client=Client())

    def test_the_music_row_is_ready_with_the_five_modes_and_resolves_auto(self) -> None:
        """The row the Settings screen reads, on a Mac with room to hold a model."""
        with patch.object(self.memory_policy, "machine_bytes", lambda: 32 * 1024 ** 3):
            inventory = asyncio.run(self.control().inventory())
        row = inventory["studios"][0]
        self.assertEqual(row["id"], "music")
        self.assertEqual(row["state"], "ready", row.get("detail"))
        self.assertNotEqual(row["state"], "update_required",
                            "Music must not read 'Run Update on this Studio to add "
                            "memory controls'")
        self.assertTrue(row["supported"])
        self.assertIsNone(row["detail"])
        policy = row["policy"]
        # A fresh Music install selects `auto`; the row publishes what that means on this Mac
        # rather than making the Hub re-derive it.
        self.assertEqual(policy["mode"], "auto")
        self.assertEqual(policy["default_mode"], "auto")
        self.assertEqual(policy["resolved_mode"], "balanced")
        self.assertEqual(policy["machine_mode"], "balanced")
        self.assertEqual(policy["idle_seconds"], 600)
        self.assertEqual([option["mode"] for option in policy["options"]],
                         ["auto", "performance", "balanced", "memory_saver", "immediate"])
        self.assertEqual([option["label"] for option in policy["options"]],
                         ["Auto", "Performance", "Balanced", "Memory Saver", "Immediate"])
        auto = policy["options"][0]
        self.assertEqual(auto["idle_seconds"], 600,
                         "auto's option must carry the resolved threshold, not None")
        self.assertIn("10 idle minutes", auto["detail"])

    def test_set_mode_changes_the_music_policy_and_the_hub_reads_it_back(self) -> None:
        control = self.control()
        result = asyncio.run(control.set_mode("memory_saver"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["result"], "updated")
        self.assertEqual(self.memory_policy.status()["mode"], "memory_saver")
        self.assertEqual(
            json.loads(self.memory_policy.settings_file().read_text(encoding="utf-8")),
            {"mode": "memory_saver"})
        # Reflected back through the same route the app calls.
        row = asyncio.run(control.inventory())["studios"][0]
        self.assertEqual(row["state"], "ready")
        self.assertEqual(row["policy"]["mode"], "memory_saver")
        self.assertEqual(row["policy"]["idle_seconds"], 120)

    def test_release_through_the_hub_discards_the_resident_engine(self) -> None:
        engine = _MusicResidentEngine()
        self.manager._executors[self.catalog.MAIN_REPO] = engine
        result = asyncio.run(self.control().release())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["result"], "released")
        self.assertEqual(engine.discards, 1)
        self.assertEqual(result["results"][0]["policy"]["loaded_models"], [])


if __name__ == '__main__':
    unittest.main()
