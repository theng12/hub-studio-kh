"""A controller relays a Chat completion for a node-only model to exactly one node.

No model is invoked and no live state is read: the controller runs in a child process against a
throwaway data dir, model library and workers file, and every worker or node answer is a mock.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from components import model_packages
from components.hub.backend import broker, registry

workers = [
 {"id":"chat", "title":"Chat", "modality":"chat", "machine":"local", "local":True},
 {"id":"chat@node1607", "title":"Chat", "modality":"chat", "machine":"node1607", "local":False,
  "host":"node1607.test", "hub_port":47873, "hub_token":"node1607-secret"},
 {"id":"chat@node1608", "title":"Chat", "modality":"chat", "machine":"node1608", "local":False,
  "host":"node1608.test", "hub_port":47873, "hub_token":"node1608-secret"},
]
monitor = SimpleNamespace(registry=workers, status={s['id']:{'status':'up'} for s in workers})
broker.configure_monitor(monitor)

state = Path(os.environ['STUDIOHUB_DATA_DIR'])
source = state/'fixture-source'
source.mkdir()
(source/'model.bin').write_bytes(b'local-chat-fixture\n')
manifest = {'schema_version':1,'package_id':'local-chat','model_id':'local-model','revision':'a'*40,
            'engine':'chat-mlx','minimum_macos':'0.0','production_allowed':True,
            'files':[{'path':'model.bin','size':19,
                      'sha256':hashlib.sha256(b'local-chat-fixture\n').hexdigest(),'urls':[]}]}
manifests = Path(os.environ['STUDIOHUB_MODEL_MANIFESTS'])
manifests.mkdir()
(manifests/'local-chat.json').write_text(json.dumps(manifest))
library = Path(os.environ['STUDIOHUB_MODELS_DIR'])
library.mkdir()
model_packages.import_folder(manifest, source, library)
workers[0].update(host='127.0.0.1', port=51091, base_url='http://controller-chat',
                  hub_token='controller-worker-secret',
                  runtime_identity={'id':'fixture-runtime','compatible':True})
workers_file = Path(os.environ['STUDIOHUB_WORKERS_FILE'])
workers_file.write_text(json.dumps(workers[:1]))
workers_file.chmod(0o600)

GEMMA = 'mlx-community/gemma-node-only-4bit'
CATALOGS = {
 'chat': ['local-model'],
 'chat@node1607': [GEMMA],
 'chat@node1608': [GEMMA],
}
async def catalog(studio):
 return {'models':[{'repo':repo,'cache':{'state':'cached'}} for repo in CATALOGS[studio['id']]]}
monitor.scheduling_catalog = catalog

wire = []
fault = {'node1607': None}
async def answer(request):
 wire.append((request.url.host, request.url.path, dict(request.headers), request.content))
 if request.url.host == 'controller-chat':
  return httpx.Response(200, json={'choices':[{'message':{'content':'local answer'},'finish_reason':'stop'}]})
 kind = fault.get(request.url.host.split('.')[0])
 if kind == 'connect':
  raise httpx.ConnectError('node never accepted a connection')
 if kind == 'read':
  raise httpx.ReadTimeout('node answer disappeared after the request left')
 if kind == 'busy':
  return httpx.Response(409, json={'detail':'local is reserved for other work on this Mac','code':'MACHINE_BUSY'})
 return httpx.Response(200, headers={'X-StudioHub-Machine':'local'},
                       json={'model':GEMMA,'choices':[{'message':{'content':'node answer'},'finish_reason':'stop'}],
                             'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5},
                             'studio_hub':{'elapsed_seconds':1.5}})
monitor._client = httpx.AsyncClient(transport=httpx.MockTransport(answer))

from components.hub.backend import monitor as monitor_module
with patch.object(monitor_module, 'StudioMonitor', return_value=monitor):
 from components.hub import standalone
from components.hub.backend import ledger, peers
ledger.prepare_database()

def connected(*machines):
 for machine in machines:
  peers._cache[machine] = (time.time(), {'reachable':True,'auth':True,'status':'connected'})

async def proof():
 with patch.object(standalone, 'StudioMonitor', return_value=monitor), \
      patch.object(standalone.output_storage, 'admission', return_value=None), \
      patch.object(broker, '_host_for_studio', return_value=None), \
      patch.object(broker, 'prepare_machine_memory', new=AsyncMock(return_value=('run', None))):
  hub = standalone.create_app(start_background=False)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hub), base_url='http://fixture-hub') as client:
   fleet = {'X-Hub-Token': peers.fleet_token()}
   path = '/studio/chat/v1/chat/completions'
   body = {'model':GEMMA,'messages':[{'role':'user','content':'hi'}],'stream':False}

   # Only node1607 is connected: the one Mac that may run it.
   connected('node1607')
   relayed = await client.post(path, headers=fleet, json=body)
   assert relayed.status_code == 200, relayed.text
   assert relayed.json()['choices'][0]['message']['content'] == 'node answer'
   assert relayed.headers.get('x-studiohub-machine') == 'node1607', relayed.headers
   assert len(wire) == 1, wire
   host, wire_path, headers, content = wire[0]
   assert (host, wire_path) == ('node1607.test', '/studio/chat/v1/chat/completions'), wire[0]
   assert headers['x-hub-token'] == 'node1607-secret', 'the caller credential must not reach the node'
   assert json.loads(content) == body, 'the exact request, exact model, goes to the node'
   assert not broker.busy_machines(), 'the relay released its controller lease'
   recorded = ledger.load_finished_batches(limit=1)[0]['items'][0]
   assert (recorded['machine'], recorded['worker_id'], recorded['state']) == ('node1607', 'chat@node1607', 'done'), recorded

   # A model this controller's own Chat has stays local and is never relayed.
   wire.clear()
   local = await client.post(path, headers=fleet, json={**body, 'model':'local-model'})
   assert local.status_code == 200, local.text
   assert [w[0] for w in wire] == ['controller-chat'], wire
   assert local.headers.get('x-studiohub-machine') == 'local'

   # The node Mac is leased on the controller: refused by name, nothing sent.
   wire.clear()
   assert broker.acquire_external_machine('node1607', 'render:other')
   busy = await client.post(path, headers=fleet, json=body)
   assert busy.status_code == 409 and busy.json()['code'] == 'MACHINE_BUSY', busy.text
   assert not wire
   broker.release_external_machine('node1607', 'render:other')

   # Every holder paused on the controller: no unpaused Mac can run it, nothing sent.
   # (node1608 holds it too; disconnected but unpaused, it alone reads as busy.)
   registry.set_machine_enabled('node1607', False)
   only_disconnected = await client.post(path, headers=fleet, json=body)
   assert only_disconnected.json()['code'] == 'MACHINE_BUSY', only_disconnected.text
   registry.set_studio_enabled('node1608', 'chat@node1608', False)
   paused = await client.post(path, headers=fleet, json=body)
   assert paused.json()['code'] == 'NO_AVAILABLE_MACHINE', paused.text
   assert not wire
   registry.set_machine_enabled('node1607', True)
   registry.set_studio_enabled('node1608', 'chat@node1608', True)

   # A queued (wait-opt-in) request keeps the controller's own queue contract: no relay.
   queued = await client.post(path, json=body, headers={
       **fleet, 'X-StudioHub-Request-ID':'1'*32, 'X-StudioHub-Queue-Wait-Seconds':'1'})
   assert queued.status_code >= 400 and not wire, (queued.status_code, queued.text, wire)

   # Two nodes hold it. Never broadcast, never a second submit after the first may have run.
   connected('node1607', 'node1608')
   fault['node1607'] = 'read'
   uncertain = await client.post(path, headers=fleet, json=body)
   assert uncertain.status_code == 502 and 'code' not in uncertain.json(), uncertain.text
   assert [w[0] for w in wire] == ['node1607.test'], wire
   assert not broker.busy_machines()
   wire.clear()

   # The node refused at its door: its answer passes through, no fallback.
   fault['node1607'] = 'busy'
   refused = await client.post(path, headers=fleet, json=body)
   assert refused.status_code == 409 and refused.json()['code'] == 'MACHINE_BUSY', refused.text
   assert [w[0] for w in wire] == ['node1607.test'], wire
   wire.clear()

   # Nothing accepted the connection: a named, safe refusal.
   fault['node1607'] = 'connect'
   unreachable = await client.post(path, headers=fleet, json=body)
   assert unreachable.status_code == 503 and unreachable.json()['code'] == 'STUDIO_NOT_RUNNING', unreachable.text
   assert [w[0] for w in wire] == ['node1607.test'], wire
   assert not broker.busy_machines()
 await monitor._client.aclose()
asyncio.run(proof())
print('node-only Chat relayed once to one node; refusals named; no second submit')
'''

STALE_PROOF = r'''
import asyncio
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from components.hub.backend import broker, registry

GEMMA = 'mlx-community/gemma-node-only-4bit'
# The worker's own row for it: a 32 GB tier, which neither node below has, so the memory gate
# answers "skip" - the real gate, not a patched one.
GEMMA_ENTRY = {'repo': GEMMA, 'cache': {'state': 'cached'},
               'min_unified_memory_gb': 32, 'min_total_memory_gb': 32}

workers = [
 {"id":"chat", "title":"Chat", "modality":"chat", "machine":"local", "local":True},
 {"id":"chat@node1607", "title":"Chat", "modality":"chat", "machine":"node1607", "local":False,
  "host":"node1607.test", "hub_port":47873, "hub_token":"node1607-secret"},
 {"id":"chat@node1608", "title":"Chat", "modality":"chat", "machine":"node1608", "local":False,
  "host":"node1608.test", "hub_port":47873, "hub_token":"node1608-secret"},
]
monitor = SimpleNamespace(registry=workers, status={s['id']:{'status':'up'} for s in workers})
broker.configure_monitor(monitor)
Path(os.environ['STUDIOHUB_MODEL_MANIFESTS']).mkdir()
Path(os.environ['STUDIOHUB_MODELS_DIR']).mkdir()

reads = {'chat@node1608': 0}
async def catalog(studio):
 if studio['id'] == 'chat@node1607':
  # A real catalog read awaits. This is the window the reviewer named: the memory gate and the
  # priority check below await too, and an observation can go stale while they do.
  await asyncio.sleep(0)
  return {'models':[GEMMA_ENTRY]}
 if studio['id'] == 'chat@node1608':
  reads['chat@node1608'] += 1
  # Read 1 is the holder pass and read 2 the eligibility pass; by read 3 (the relay's own re-read,
  # after the awaits) its observation is unusable - a failed refresh or a stale age, both of which
  # answer None while the studio still reads as up.
  return {'models':[]} if reads['chat@node1608'] > 2 else {'models':[GEMMA_ENTRY]}
 return {'models':[]}
monitor.scheduling_catalog = catalog

wire = []
async def answer(request):
 wire.append((request.url.host, request.url.path))
 return httpx.Response(200, json={'choices':[{'message':{'content':'answer'}}]})
monitor._client = httpx.AsyncClient(transport=httpx.MockTransport(answer))

from components.hub.backend import monitor as monitor_module
with patch.object(monitor_module, 'StudioMonitor', return_value=monitor):
 from components.hub import standalone
from components.hub.backend import ledger, peers
ledger.prepare_database()
for machine in ('node1607', 'node1608'):
 peers._cache[machine] = (time.time(), {'reachable':True,'auth':True,'status':'connected',
                                        'host':{'total_gb':16,'available_gb':8}})

# The real policy resolved the 32 GB floor from the worker's own row: this is the gate the
# earlier contract test patched out.
assert broker._admission_requirements(GEMMA, GEMMA_ENTRY)['min_total'] == 32
assert broker._host_for_studio(workers[1])['total_gb'] == 16

async def proof():
 with patch.object(standalone, 'StudioMonitor', return_value=monitor), \
      patch.object(standalone.output_storage, 'admission', return_value=None):
  hub = standalone.create_app(start_background=False)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hub), base_url='http://fixture-hub') as client:
   fleet = {'X-Hub-Token': peers.fleet_token()}
   stale = await client.post('/studio/chat/v1/chat/completions', headers=fleet,
                             json={'model':GEMMA,'messages':[{'role':'user','content':'hi'}],
                                   'stream':False})
   # A named refusal the Gateway can defer on - never a bare 500, which would become an
   # operator-visible "unknown outcome, may have run" hold for work that never left this Mac.
   assert stale.status_code == 409, (stale.status_code, stale.text)
   assert stale.json()['code'] == 'MACHINE_BUSY', stale.text
   assert isinstance(stale.json().get('detail'), str), stale.text
   assert not wire, wire
   assert not broker.busy_machines(), 'an unprovable candidate must not leave a lease behind'
   assert reads['chat@node1608'] == 3, reads
 await monitor._client.aclose()
asyncio.run(proof())
print('a stale second candidate is skipped, not dispatched: named refusal, zero POSTs')
'''


class ChatFleetRelayTests(unittest.TestCase):
    def _run(self, proof: str) -> None:
        state = tempfile.mkdtemp(prefix='hub-chat-relay-')
        env = {key: value for key, value in os.environ.items() if not key.startswith('STUDIOHUB_')}
        env.update(STUDIOHUB_DATA_DIR=state, STUDIOHUB_MODELS_DIR=state + '/models',
                   STUDIOHUB_MODEL_MANIFESTS=state + '/manifests',
                   STUDIOHUB_WORKERS_FILE=state + '/workers.json',
                   STUDIOHUB_PACKAGED='1', STUDIOHUB_REQUIRE_AUTH='1', PYTHONPATH=str(ROOT))
        result = subprocess.run([os.environ.get('STUDIOHUB_TEST_RUNTIME', sys.executable), '-c', proof],
                                env=env, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_controller_relays_node_only_chat_to_one_node(self):
        self._run(PROOF)

    def test_a_candidate_whose_catalog_goes_stale_is_skipped_not_dispatched(self):
        # The memory gate and the priority check await; a second candidate's catalog observation
        # can go bad in that window and answer None. It must be skipped, never handed to the
        # memory gate (which reads the entry) and never sent with an assumed one.
        self._run(STALE_PROOF)


if __name__ == '__main__':
    unittest.main()
