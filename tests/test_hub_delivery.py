"""Delivery acknowledgement must not release output on a failed worker call."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


PROOF = r'''
import os,tempfile
temporary=tempfile.TemporaryDirectory(prefix='hub-delivery-')
os.environ['STUDIOHUB_DATA_DIR']=temporary.name
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from types import SimpleNamespace
from unittest.mock import patch
from components.hub import delivery
item={'index':0,'state':'running','studio':'voice','studio_job_id':'worker-1'}
batch={'id':'batch-1','items':[item]}
delivery.broker.batches={'batch-1':batch}
worker={'id':'voice','host':'127.0.0.1','port':54321,'machine':'local','studio_token':'private-token'}
app=FastAPI(); delivery.register_routes(app,SimpleNamespace(registry=[worker]))
calls=[]; saved=[]; marks=[]; status=503; worker_ok=True
class WorkerClient:
    def __init__(self,**kwargs): assert kwargs.get('follow_redirects') is False
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def post(self,url,**kwargs):
        calls.append((url,kwargs))
        return httpx.Response(status,request=httpx.Request('POST',url),json={'ok':worker_ok})
with TestClient(app) as client, patch.object(delivery.httpx,'AsyncClient',WorkerClient), patch.object(delivery.ledger,'save_batch',lambda row:saved.append(dict(row))), patch.object(delivery.output_storage,'mark_receipt_acked',lambda **row:marks.append(row)):
    path='/api/hub/jobs/batch-1/items/0/ack'
    assert client.post(path).status_code==409
    assert not calls and not saved
    item['state']='done'
    assert client.post(path).status_code==502
    assert not saved and not item.get('receipt_acked_at')
    status=200
    assert client.post(path).json()=={'ok':True}
    assert saved and item['receipt_acked_at']>0
    assert calls[-1][0]=='http://127.0.0.1:54321/api/generate/jobs/worker-1/ack'
    assert calls[-1][1]['headers']=={'X-Studio-Token':'private-token'}
    assert marks==[{'batch_id':'batch-1','item_index':0}]
    count=len(calls); stamp=item['receipt_acked_at']
    assert client.post(path).json()=={'ok':True}
    assert len(calls)==count and item['receipt_acked_at']==stamp
    assert len(marks)==2, 'idempotent receipt retry did not repair the output index'
    item.pop('receipt_acked_at')
    worker_ok=False; before=len(saved)
    assert client.post(path).status_code==502
    assert len(saved)==before and not item.get('receipt_acked_at')
    worker_ok=True
    with patch.object(delivery.ledger,'save_batch',side_effect=OSError('disk unavailable')):
        try: client.post(path)
        except OSError: pass
        else: raise AssertionError('failed receipt persistence was reported successful')
    assert not item.get('receipt_acked_at')
    assert client.post(path).json()=={'ok':True}
'''


class HubDeliveryTests(unittest.TestCase):
    def test_worker_failure_preserves_receipt_and_completed_ack_is_idempotent(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


TRANSCRIPTION_PROOF = r'''
"""Subtitle delivery must be acknowledged through the same contract as Image.

The Subtitle studio had no Hub-level acknowledgement at all: a transcript could be
exported and the Hub never recorded delivery, so the output stayed indexed as
unacknowledged for cleanup. This drives the real handler against a stub worker.
"""
import os,tempfile,asyncio
temporary=tempfile.TemporaryDirectory(prefix='hub-subtitle-ack-')
os.environ['STUDIOHUB_DATA_DIR']=temporary.name
import httpx
from types import SimpleNamespace
from unittest.mock import patch
from components.hub import output_storage
from components.hub.backend import transcription_jobs

item={'index':0,'state':'done','studio':'subtitle','studio_task_id':'worker-1'}
batch={'id':'batch-1','items':[item],'idempotency_key':'k','model':'whisper'}
transcription_jobs.batches['batch-1']=batch
monitor=SimpleNamespace(registry=[{'id':'subtitle','host':'127.0.0.1','port':54321,
                                   'machine':'local','studio_token':'private-token'}])
calls=[]; saved=[]; marks=[]; status=503; worker_ok=True
class WorkerClient:
    def __init__(self,**kwargs): assert kwargs.get('follow_redirects') is False
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def post(self,url,**kwargs):
        calls.append((url,kwargs))
        return httpx.Response(status,request=httpx.Request('POST',url),json={'ok':worker_ok})

def run():
    return asyncio.run(transcription_jobs.acknowledge_item(monitor,'batch-1',0))

with patch.object(transcription_jobs.httpx,'AsyncClient',WorkerClient), \
     patch.object(transcription_jobs,'_save',lambda row:saved.append(dict(row))), \
     patch.object(output_storage,'mark_receipt_acked',lambda **row:marks.append(row)), \
     patch.object(output_storage,'acquire_transfer',lambda **row:(lambda:None)):
    # No worker job: the receipt cannot be taken.
    item['studio_task_id']=None
    try: run()
    except Exception as exc: assert getattr(exc,'status_code',None)==404, exc
    else: raise AssertionError('an item with no worker job was acknowledged')
    item['studio_task_id']='worker-1'
    # Only completed output may be acknowledged.
    item['state']='running'
    try: run()
    except Exception as exc: assert getattr(exc,'status_code',None)==409, exc
    else: raise AssertionError('an unfinished Subtitle item was acknowledged')
    item['state']='done'
    # A worker that does not confirm must not produce a local receipt.
    try: run()
    except Exception as exc: assert getattr(exc,'status_code',None)==502, exc
    else: raise AssertionError('a failed worker acknowledgement was reported successful')
    assert not saved and not item.get('receipt_acked_at')
    status=200
    assert run()=={'ok':True}
    assert saved and item['receipt_acked_at']>0
    assert calls[-1][0]=='http://127.0.0.1:54321/api/generate/transcribe/jobs/worker-1/ack'
    assert marks==[{'batch_id':'batch-1','item_index':0}]
    # Idempotent: a retry must not call the worker again but must repair the index.
    count=len(calls); stamp=item['receipt_acked_at']
    assert run()=={'ok':True}
    assert len(calls)==count and item['receipt_acked_at']==stamp
    assert len(marks)==2, 'idempotent receipt retry did not repair the output index'
    # A worker that rejects ok=false is a failure, not a receipt.
    item.pop('receipt_acked_at'); worker_ok=False; before=len(saved)
    try: run()
    except Exception as exc: assert getattr(exc,'status_code',None)==502, exc
    else: raise AssertionError('worker_ok=false was accepted as a receipt')
    assert len(saved)==before and not item.get('receipt_acked_at')
    worker_ok=True
    # A failed local save stays retryable.
    with patch.object(transcription_jobs,'_save',side_effect=OSError('disk unavailable')):
        try: run()
        except OSError: pass
        else: raise AssertionError('failed receipt persistence was reported successful')
    assert not item.get('receipt_acked_at')
    assert run()=={'ok':True}
'''


class HubTranscriptionDeliveryTests(unittest.TestCase):
    def test_subtitle_delivery_uses_the_same_acknowledgement_contract(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, '-B', '-c', TRANSCRIPTION_PROOF], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
