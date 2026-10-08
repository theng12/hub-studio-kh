"""Bounded local Chat priority without model invocation or live state."""
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
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from components.hub import operator_controls
from components.hub.backend import broker, registry

workers = [
 {"id":"chat", "title":"Chat", "modality":"chat", "machine":"local", "local":True},
 {"id":"voice", "title":"Voice", "modality":"voice", "machine":"local", "local":True},
 {"id":"remote-chat", "title":"Remote", "modality":"chat", "machine":"other", "local":False},
]
monitor = SimpleNamespace(registry=workers, status={s['id']:{'status':'up'} for s in workers})
broker.configure_monitor(monitor)
app=FastAPI()
operator_controls.register_routes(app, monitor)
with TestClient(app) as client:
 answer=client.get('/api/hub/work-policy')
 assert answer.status_code==200, answer.text
 assert answer.json()['chat_priority'] is False
 assert answer.json()['priority_order']==[]
 assert answer.json()['priority_functions']==[
  {'id':'chat','title':'Chat'}, {'id':'voice','title':'Voice'}]
 assert [s['id'] for s in answer.json()['studios']]==['chat','voice']
 registry.set_studio_enabled('local','voice',False)
 assert client.post('/api/hub/work-policy',json={'chat_priority':True}).status_code==200
 assert registry.studio_enabled('local','voice') is False
 registry._flags_cache=None
 assert registry.chat_priority('local') is True
 assert registry.priority_order('local')==['chat']
 saved=client.post('/api/hub/work-policy',json={'priority_order':['voice','chat']})
 assert saved.status_code==200,saved.text
 assert saved.json()['priority_order']==['voice','chat']
 assert saved.json()['chat_priority'] is False
 assert registry.studio_enabled('local','voice') is False
 registry._flags_cache=None
 assert registry.priority_order('local')==['voice','chat']
 assert client.post('/api/hub/work-policy',json={'priority_order':['chat','chat']}).status_code==400
 assert client.post('/api/hub/work-policy',json={'priority_order':['subtitle']}).status_code==400
 assert client.post('/api/hub/work-policy',json={'priority_order':['']}).status_code==400
 assert client.post('/api/hub/work-policy',json={'priority_order':[1]}).status_code==400
 assert registry.priority_order('local')==['voice','chat']
 before_file=registry.FLAGS_FILE.read_bytes()
 before_cache=registry.load_flags()
 with patch.object(Path,'replace',side_effect=OSError('synthetic write failure')):
  try:
   registry.set_priority_order('local',['chat'])
   raise AssertionError('failed policy write reported success')
  except OSError: pass
 assert registry.FLAGS_FILE.read_bytes()==before_file
 assert registry.load_flags()==before_cache
 assert list(registry.DATA_DIR.glob('.machine_flags.json-*')), 'failed write was deleted'
 registry._flags_cache={'legacy':{'enabled':False,'chat_priority':True,
                                  'studios':{'voice':{'enabled':False}}}}
 assert registry.priority_order('legacy')==['chat']
 assert registry.machine_enabled('legacy') is False
 assert registry.studio_enabled('legacy','voice') is False
 registry._flags_cache=None
 assert client.post('/api/hub/work-policy',json={'chat_priority':1}).status_code==400
 assert client.post('/api/hub/work-policy',json={'chat_priority':True,'enabled':True}).status_code==400

from components.hub.backend import chat_priority
first=chat_priority.register('a'*32,'local','chat','model')
second=chat_priority.register('b'*32,'local','chat','other-model')
try:
 chat_priority.register('2'*32,'local','chat','model')
 raise AssertionError('a third request entered the physical queue')
except HTTPException as e:
 assert e.status_code==409
chat_priority.mark_running(first)
second['ready']=True
assert chat_priority.first(second)
assert chat_priority.cancel(second['id'])['state']=='cancelled'
assert not chat_priority.first(second)
assert chat_priority.cancel('c'*32)['state']=='cancelled'
try:
 chat_priority.register('c'*32,'local','chat','model')
 raise AssertionError('cancel-before-submit started work')
except HTTPException as e:
 assert e.status_code==409
chat_priority.finish(first)
assert chat_priority.snapshot(first['id'])['state']=='done'

async def wait_proof():
 class Request:
  gone=False
  async def is_disconnected(self): return self.gone
 request=Request()
 leases=[]
 def reserve():
  leases.append('lease')
  return 'lease'
 async def ready(): return None,True
 row=chat_priority.register('d'*32,'local','chat','model')
 request.gone=True
 try:
  await chat_priority.wait(row,request,ready,reserve,1)
  raise AssertionError('lost client reserved a Mac')
 except HTTPException as e: assert e.status_code==409
 assert leases==[]
 assert row['state']=='cancelled'
 request.gone=False
 row=chat_priority.register('e'*32,'local','chat','model')
 async def paused(): return {'code':'STUDIO_DISABLED','detail':'paused'},False
 try:
  await chat_priority.wait(row,request,paused,reserve,1)
  raise AssertionError('paused Studio started a pending job')
 except HTTPException as e: assert e.detail['code']=='STUDIO_DISABLED'
 assert leases==[]
 row=chat_priority.register('f'*32,'local','chat','model')
 try:
  await chat_priority.wait(row,request,ready,reserve,0)
  raise AssertionError('expired request reserved a Mac')
 except HTTPException as e: assert e.detail['code']=='CHAT_QUEUE_EXPIRED'
 assert leases==[]
 row=chat_priority.register('1'*32,'local','chat','model')
 assert await chat_priority.wait(row,request,ready,reserve,1)=='lease'
 assert row['state']=='running'
 chat_priority.finish(row)

 row=chat_priority.register('0'*31+'1','local','chat','model')
 probe_count=0
 async def cancelled_on_final_probe():
  nonlocal probe_count
  probe_count+=1
  if probe_count==2:
   await asyncio.sleep(0)
   chat_priority.cancel(row['id'])
  return None,True
 try:
  await chat_priority.wait(row,request,cancelled_on_final_probe,reserve,1)
  raise AssertionError('cancel during final readiness probe reserved a Mac')
 except HTTPException as e: assert e.detail['code']=='CHAT_REQUEST_CANCELLED'
 assert leases==['lease']

 row=chat_priority.register('0'*31+'2','local','chat','model')
 class DisconnectOnFinalFence:
  checks=0
  async def is_disconnected(self):
   self.checks+=1
   return self.checks==3
 try:
  await chat_priority.wait(row,DisconnectOnFinalFence(),ready,reserve,1)
  raise AssertionError('disconnect during final readiness probe reserved a Mac')
 except HTTPException as e: assert e.detail['code']=='CHAT_REQUEST_CANCELLED'
 assert leases==['lease']

 head=chat_priority.register('0'*31+'3','local','chat','model')
 tail=chat_priority.register('0'*31+'4','local','chat','model')
 class EarlierReadyOnFinalFence:
  checks=0
  async def is_disconnected(self):
   self.checks+=1
   if self.checks==3: head['ready']=True
   if self.checks==4: chat_priority.finish(head)
   return False
 fifo_request=EarlierReadyOnFinalFence()
 assert await chat_priority.wait(tail,fifo_request,ready,reserve,1)=='lease'
 assert fifo_request.checks>=6, 'final await let a later request bypass the ready FIFO head'
 chat_priority.finish(tail)
asyncio.run(wait_proof())

async def sequence_proof():
 class Request:
  async def is_disconnected(self): return False
 async def ready(): return None,True
 broker._external_machine_leases.clear()
 registry.set_priority_order('local',['chat','voice'])
 assert broker.acquire_external_machine('local','tts')
 a=chat_priority.register('3'*32,'local','chat','model')
 b=chat_priority.register('4'*32,'local','chat','other-model')
 def reserve(row):
  owner='chat:'+row['id']
  return owner if broker.acquire_external_machine('local',owner) else None
 ta=asyncio.create_task(chat_priority.wait(a,Request(),ready,lambda:reserve(a),3))
 tb=asyncio.create_task(chat_priority.wait(b,Request(),ready,lambda:reserve(b),3))
 while not a['ready'] or not b['ready']: await asyncio.sleep(0)
 assert a['state']==b['state']=='queued'
 broker.release_external_machine('local','tts')
 assert await broker.higher_priority_work_waiting(
  'local','voice',time.time(),'peer:voice:next')
 owner=await ta
 assert a['state']=='running' and b['state']=='queued'
 chat_priority.finish(a)
 broker.release_external_machine('local',owner)
 assert await broker.higher_priority_work_waiting(
  'local','voice',time.time(),'peer:voice:next')
 owner=await tb
 chat_priority.finish(b)
 broker.release_external_machine('local',owner)
 assert broker.acquire_external_machine('local','peer:voice:next')
 broker.release_external_machine('local','peer:voice:next')

 row=chat_priority.register('5'*32,'local','chat','model')
 async def cancelled_during_probe():
  chat_priority.cancel(row['id'])
  await asyncio.sleep(0)
  return None,True
 try:
  await chat_priority.wait(row,Request(),cancelled_during_probe,lambda:reserve(row),1)
  raise AssertionError('cancel during readiness probe reserved a Mac')
 except HTTPException: pass
 assert 'local' not in broker._external_machine_leases

 head=chat_priority.register('6'*32,'local','chat','model')
 tail=chat_priority.register('7'*32,'local','chat','model')
 head['ready']=False
 tail['ready']=True
 assert chat_priority.first(tail)
 chat_priority.finish(head)
 chat_priority.finish(tail)
asyncio.run(sequence_proof())
for index in range(1000,1300):
 row=chat_priority.register(f'{index:032x}','local','chat','model')
 chat_priority.finish(row)
assert len(chat_priority.requests)<=256

async def routed_proof():
 import httpx
 from unittest.mock import AsyncMock
 from components import model_packages
 state=Path(os.environ['STUDIOHUB_DATA_DIR'])
 source=state/'fixture-source'
 source.mkdir()
 model_bytes=b'priority-fixture-model\n'
 (source/'model.bin').write_bytes(model_bytes)
 manifest={'schema_version':1,'package_id':'priority-fixture','model_id':'model',
           'revision':'a'*40,'engine':'chat-mlx','minimum_macos':'0.0','production_allowed':True,
           'files':[{'path':'model.bin','size':len(model_bytes),
                     'sha256':hashlib.sha256(model_bytes).hexdigest(),'urls':[]}]}
 manifests=Path(os.environ['STUDIOHUB_MODEL_MANIFESTS'])
 manifests.mkdir()
 manifest_file=manifests/'priority-fixture.json'
 manifest_file.write_text(json.dumps(manifest))
 library=Path(os.environ['STUDIOHUB_MODELS_DIR'])
 library.mkdir()
 model_packages.import_folder(manifest,source,library)
 for index,worker in enumerate(workers):
  worker.update(host='127.0.0.1',port=51031+index,
                runtime_identity={'id':'fixture-runtime','compatible':True})
 workers_file=Path(os.environ['STUDIOHUB_WORKERS_FILE'])
 workers_file.write_text(json.dumps(workers[:2]))
 workers_file.chmod(0o600)
 from components.hub.backend import monitor as monitor_module
 with patch.object(monitor_module,'StudioMonitor',return_value=monitor):
  from components.hub import standalone
 from components.hub.backend import auth, ledger, peers
 chat_priority.requests.clear()
 broker._external_machine_leases.clear()
 ledger.prepare_database()
 wire=[]
 fault={'enabled':False,'state':'running','connect':False}
 async def worker_request(request):
  if request.url.path=='/api/fleet/activity':
   return httpx.Response(200,json={'active':({'id':'d'*32,'state':'running'} if fault['state']=='running' else None),
                                   'latest':({'id':'d'*32,'state':'done'} if fault['state']=='done' else None)})
  wire.append(request.url.path)
  if fault['connect']:
   raise httpx.ConnectError('worker never accepted a connection')
  if fault['enabled']:
   raise httpx.ReadTimeout('worker response disappeared after acceptance')
  return httpx.Response(200,json={'choices':[{'message':{'content':'answer'},'finish_reason':'length'}],
                                  'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}})
 monitor._client=httpx.AsyncClient(transport=httpx.MockTransport(worker_request))
 async def catalog(_studio):
  return {'models':[{'repo':'model','cache':{'state':'cached'},'runtime_ready':True,'available':True}]}
 monitor.scheduling_catalog=catalog
 for worker in workers:
  worker.update(base_url='http://fixture-worker',hub_token='worker-secret')
 with patch.object(standalone,'StudioMonitor',return_value=monitor), \
      patch.object(standalone.output_storage,'admission',return_value=None), \
      patch.object(broker,'_host_for_studio',return_value=None), \
      patch.object(broker,'prepare_machine_memory',new=AsyncMock(return_value=('run',None))):
  hub=standalone.create_app(start_background=False)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hub),base_url='http://fixture-hub') as client:
   fleet_headers={'X-Hub-Token':peers.fleet_token()}
   fleet_policy=await client.get('/api/hub/work-policy',headers=fleet_headers)
   assert fleet_policy.status_code==200,(fleet_policy.status_code,fleet_policy.text)
   assert fleet_policy.json()['priority_order']==['chat','voice']
   local_policy=await client.get('/api/hub/fleet/nodes/local/work-policy',headers=fleet_headers)
   assert local_policy.status_code==200,local_policy.text
   assert local_policy.json()['work_policy']['priority_order']==['chat','voice']
   local_save=await client.post('/api/hub/fleet/nodes/local/work-policy',headers=fleet_headers,
                                json={'priority_order':['chat','voice']})
   assert local_save.status_code==200,local_save.text
   assert local_save.json()['node']=='local'
   registry.set_priority_order('local',['image','chat'])
   with patch.object(broker,'_broker_dispatchable_work',return_value=[
         ('image',time.time()-10,'broker:older-image')]):
    image_first=await client.post(
     '/studio/chat/v1/chat/completions',
     headers={**fleet_headers,'X-StudioHub-Request-ID':'7'*32},
     json={'model':'model','messages':[{'role':'user','content':'ordinary proxy'}],
           'stream':False})
   assert image_first.status_code==409,image_first.text
   assert image_first.json()['code']=='MACHINE_BUSY',image_first.text
   assert not wire, 'an ordinary Chat proxy bypassed ready Image-first work'
   registry.set_priority_order('local',['chat','voice'])
   async def pause_voice_during_rank(*_args):
    registry.set_studio_enabled('local','voice',False)
    return False
   with patch.object(broker,'higher_priority_work_waiting',new=pause_voice_during_rank):
    paused_voice=await client.post('/studio/voice/api/generate/fixture',headers=fleet_headers,
                                   json={'model':'model','text':'hello'})
   assert paused_voice.status_code==409,paused_voice.text
   assert paused_voice.json()['code']=='STUDIO_DISABLED',paused_voice.text
   assert not wire, 'a peer POST escaped after its Studio was paused during arbitration'
   registry.set_studio_enabled('local','voice',True)
   assert broker.acquire_external_machine('local','tts')
   registry.set_chat_priority('local',False)
   body={'model':'model','messages':[{'role':'user','content':'hi'}],'stream':False}
   path='/studio/chat/v1/chat/completions'
   def headers(key): return {**fleet_headers,'X-StudioHub-Request-ID':key,'X-StudioHub-Queue-Wait-Seconds':'2'}
   equal_wait=asyncio.create_task(client.post(path,headers=headers('0'*32),json=body))
   for _ in range(1000):
    if chat_priority.requests.get('0'*32,{}).get('ready'): break
    await asyncio.sleep(.001)
   assert chat_priority.snapshot('0'*32)['state']=='queued'
   stopped=await client.delete('/studio/chat/requests/'+'0'*32,headers=fleet_headers)
   assert stopped.status_code==200,stopped.text
   equal_answer=await equal_wait
   assert equal_answer.status_code==409 and equal_answer.json()['detail']['code']=='CHAT_REQUEST_CANCELLED'
   registry.set_chat_priority('local',True)
   registry.set_studio_enabled('local','chat',True)
   manifest['production_allowed']=False
   manifest_file.write_text(json.dumps(manifest))
   unqualified=await client.post(path,headers=headers('f'*32),json=body)
   assert unqualified.status_code==409 and unqualified.json()['code']=='MODEL_UNQUALIFIED',unqualified.text
   assert not any(row['state'] in {'queued','running','unknown'}
                  for row in chat_priority.requests.values())
   manifest['production_allowed']=True
   manifest_file.write_text(json.dumps(manifest))
   one=asyncio.create_task(client.post(path,headers=headers('8'*32),json=body))
   two=asyncio.create_task(client.post(path,headers=headers('9'*32),json=body))
   for _ in range(1000):
    if len(chat_priority.requests)==2 and all(r['ready'] for r in chat_priority.requests.values()): break
    await asyncio.sleep(.001)
   status=await client.get('/studio/chat/requests/'+'8'*32,headers=fleet_headers)
   assert status.status_code==200,(status.status_code,status.text)
   assert status.json()['state']=='queued',status.text
   third=await client.post(path,headers=headers('a'*32),json=body)
   assert third.status_code==409 and third.json()['detail']['code']=='MACHINE_BUSY',third.text
   broker.release_external_machine('local','tts')
   answers=await asyncio.gather(one,two)
   assert [r.status_code for r in answers]==[200,200],[r.text for r in answers]
   assert len(wire)==2,wire
   assert answers[0].json()['choices'][0]['finish_reason']=='length'
   assert broker.acquire_external_machine('local','peer:voice:idle')
   broker.release_external_machine('local','peer:voice:idle')
   cancelled=await client.delete('/studio/chat/requests/'+'b'*32,headers=fleet_headers)
   assert cancelled.status_code==200,cancelled.text
   late=await client.post(path,headers=headers('b'*32),json=body)
   assert late.status_code==409 and late.json()['detail']['code']=='CHAT_REQUEST_CANCELLED',late.text
   assert len(wire)==2
   registry.set_chat_priority('local',False)
   late=await client.post(path,headers=headers('b'*32),json=body)
   assert late.status_code==409 and late.json()['detail']['code']=='CHAT_REQUEST_CANCELLED',late.text
   assert len(wire)==2
   registry.set_chat_priority('local',True)
   checks=[]
   async def pause_during_disconnect(_request):
    checks.append(True)
    if len(checks)>=2: registry.set_studio_enabled('local','chat',False)
    await asyncio.sleep(0)
    return False
   with patch.object(standalone.Request,'is_disconnected',new=pause_during_disconnect):
    paused=await client.post(path,headers=headers('c'*32),json=body)
   assert paused.status_code==409 and paused.json()['detail']['code']=='STUDIO_DISABLED',paused.text
   assert len(wire)==2
   assert chat_priority.snapshot('c'*32)['state']=='refused'
   registry.set_studio_enabled('local','chat',True)
   retry=await client.post(path,headers=headers('c'*32),json=body)
   assert retry.status_code==200,retry.text
   assert len(wire)==3
   fault['enabled']=True
   uncertain=await client.post(path,headers=headers('d'*32),json=body)
   assert uncertain.status_code==502,uncertain.text
   assert chat_priority.snapshot('d'*32)['state']=='unknown'
   assert 'local' in broker.busy_machines()
   assert not broker.acquire_external_machine('local','peer:voice:unsafe')
   with patch.object(standalone.time,'monotonic',return_value=time.monotonic()+86400):
    standalone._prune_peer_leases()
    assert 'local' in broker.busy_machines()
    assert chat_priority.snapshot('d'*32)['state']=='unknown'
   await standalone._release_finished_peer_leases(monitor)
   assert 'local' in broker.busy_machines()
   fault['state']='done'
   await standalone._release_finished_peer_leases(monitor)
   assert 'local' not in broker.busy_machines()
   assert chat_priority.snapshot('d'*32)['state']=='done'
   fault['enabled']=False
   fault['connect']=True
   unreachable=await client.post(path,headers=headers('e'*32),json=body)
   assert unreachable.status_code==503 and unreachable.json()['code']=='STUDIO_NOT_RUNNING',unreachable.text
   assert chat_priority.snapshot('e'*32)['state']=='refused'
   assert 'local' not in broker.busy_machines()
   fault['connect']=False
   recovered=await client.post(path,headers=headers('e'*32),json=body)
   assert recovered.status_code==200,recovered.text
   peers._cache.pop('other',None)
   remote={**workers[2],'host':'fixture-node','port':1234,'hub_port':80}
   async def peer_resources(_request):
    return httpx.Response(200,json={'host':{'total_gb':32,'available_gb':28},
                                    'studios':{'chat':{'status':'up'}},'chat_priority':True})
   async with httpx.AsyncClient(transport=httpx.MockTransport(peer_resources)) as peer_client:
    await peers.refresh([remote],peer_client)
   assert broker.chat_priority_enabled('other') is True
   assert broker.priority_order('other')==['chat']
   peers._cache['other']=(time.time(),{'reachable':True,'auth':True,
                                      'priority_order':['chat','chat'],'chat_priority':True})
   assert broker.priority_order('other')==[]
   assert broker.chat_priority_enabled('other') is False
   peers._cache['other']=(time.time(),{'reachable':True,'auth':True})
   assert broker.chat_priority_enabled('other') is False
   resources=await client.get('/api/hub/resources',headers=fleet_headers)
   assert resources.status_code==200,resources.text
   assert resources.json()['chat_priority'] is True
   assert resources.json()['priority_order'][0]=='chat'
 await monitor._client.aclose()
asyncio.run(routed_proof())
print('local policy persists; one pending slot; cancellation fences late submits')
'''


class ChatPriorityTests(unittest.TestCase):
    def test_policy_queue_and_cancel_before_submit(self):
        state=tempfile.mkdtemp(prefix='hub-chat-priority-')
        # Full-suite collectors configure their own libraries before this test runs. The child
        # owns all Hub paths and exercises admission against a real tiny synthetic package.
        env={key:value for key,value in os.environ.items() if not key.startswith('STUDIOHUB_')}
        env.update(STUDIOHUB_DATA_DIR=state, STUDIOHUB_MODELS_DIR=state+'/models',
                   STUDIOHUB_MODEL_MANIFESTS=state+'/manifests', STUDIOHUB_WORKERS_FILE=state+'/workers.json',
                   STUDIOHUB_PACKAGED='1', STUDIOHUB_REQUIRE_AUTH='1', PYTHONPATH=str(ROOT))
        result=subprocess.run([os.environ.get('STUDIOHUB_TEST_RUNTIME',sys.executable), '-c',PROOF],
                              env=env,cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':
    unittest.main()
