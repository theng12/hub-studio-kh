"""Owned Hub processes must preserve the private worker boundary."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'packaging' / f'{name}.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class HubBundleTests(unittest.TestCase):
    def test_hub_owned_voice_requires_token_for_local_generation(self):
        script = '''
import os,sys
sys.path.insert(0,sys.argv[1])
os.environ.update(STUDIOHUB_DATA_DIR=sys.argv[2],STUDIOHUB_REQUIRE_AUTH='1',STUDIOHUB_FLEET_TOKEN='a'*32)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.fleet_auth import make_middleware
from backend import storage_policy
from unittest.mock import patch
from pathlib import Path
os.environ['STUDIOHUB_OUTPUT_POLICY_OWNER']='hub'
with patch.object(storage_policy.threading, 'Thread') as thread:
    storage_policy.start_background(None, Path(sys.argv[2])/'output')
    assert not thread.called, 'Hub-owned worker started competing legacy cleanup'
    os.environ.pop('STUDIOHUB_OUTPUT_POLICY_OWNER')
    storage_policy.start_background(None, Path(sys.argv[2])/'output')
    assert thread.called, 'Legacy standalone Voice cleanup changed'
app=FastAPI()
app.middleware('http')(make_middleware('a'*32))
@app.post('/api/generate/txt2speech')
def generate(): return {'accepted': True}
with TestClient(app,client=('127.0.0.1',1234)) as client:
    assert client.post('/api/generate/txt2speech').status_code==401
    assert client.post('/api/generate/txt2speech',headers={'X-Hub-Token':'a'*32}).status_code==200
    assert client.post('/api/generate/txt2speech',headers={'X-Hub-Token':'wrong'}).status_code==401
'''
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                str(ROOT / 'components/voice/app'), temp], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_token_rejects_link_public_permissions_and_wrong_directory(self):
        worker = module('voice_worker')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            token = root / '.hub_token'
            token.write_text('a' * 32)
            token.chmod(0o600)
            self.assertEqual(worker.read_private_token(token, root), 'a' * 32)
            token.chmod(0o644)
            with self.assertRaises(ValueError): worker.read_private_token(token, root)
            token.chmod(0o600)
            link = root / 'link'
            link.symlink_to(token)
            with self.assertRaises(ValueError): worker.read_private_token(link, root)
            elsewhere = root / 'other'
            elsewhere.mkdir()
            with self.assertRaises(ValueError): worker.read_private_token(token, elsewhere)
            token.write_text('a' * 256 + '\ntrailing data')
            with self.assertRaises(ValueError): worker.read_private_token(token, root)

    def test_hub_readiness_contains_existing_token_only_in_private_file(self):
        worker = module('voice_worker')
        with tempfile.TemporaryDirectory() as temp:
            ready = Path(temp) / 'ready.json'
            worker.write_ready_file(ready, 'http://127.0.0.1:54321', '0.4.0', token='b' * 32)
            result = json.loads(ready.read_text())
            self.assertEqual(result['token'], 'b' * 32)
            self.assertEqual(result['pid'], os.getpid())
            self.assertEqual(ready.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
