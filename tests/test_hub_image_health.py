"""Private Image health must authenticate before it can enter the Hub catalog."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

PROOF = r'''
import asyncio, os, tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock
import httpx

with tempfile.TemporaryDirectory() as folder:
    os.environ['STUDIOHUB_DATA_DIR'] = folder
    from components.hub.backend.monitor import StudioMonitor
    async def proof():
        monitor = StudioMonitor(catalog_state_path=Path(folder)/'catalog.json')
        await monitor._client.aclose()
        monitor._poll_activity = AsyncMock()
        monitor._note_transition = Mock()
        monitor._note_restart_health = Mock()
        worker = {'id':'image', 'modality':'image', 'machine':'local', 'local':True,
                  'host':'127.0.0.1', 'port':49123, 'studio_token':'fixture-owner'}
        requests = []
        def respond(request):
            requests.append(request)
            authenticated = request.headers.get('x-studio-token') == 'fixture-owner'
            return httpx.Response(200 if authenticated else 401, json={'ok':authenticated})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monitor._client = client
            await monitor._poll_one(worker)
            assert monitor.status['image']['status'] == 'up', monitor.status
            assert requests[0].url.path == '/api/health'
            assert requests[0].headers['x-studio-token'] == 'fixture-owner'
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(401, json={'ok':True}))) as client:
            monitor._client = client
            monitor.status.clear()
            await monitor._poll_one(worker)
            assert monitor.status['image']['status'] != 'up'
    asyncio.run(proof())
'''


class PrivateImageHealthTests(unittest.TestCase):
    def test_authenticated_health_and_rejected_status(self):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
