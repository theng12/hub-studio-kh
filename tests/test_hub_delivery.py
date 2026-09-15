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
calls=[]; saved=[]; status=503
class WorkerClient:
    def __init__(self,**kwargs): assert kwargs.get('follow_redirects') is False
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def post(self,url,**kwargs):
        calls.append((url,kwargs))
        return httpx.Response(status,request=httpx.Request('POST',url),json={'ok':True})
with TestClient(app) as client, patch.object(delivery.httpx,'AsyncClient',WorkerClient), patch.object(delivery.ledger,'save_batch',lambda row:saved.append(dict(row))):
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
    count=len(calls); stamp=item['receipt_acked_at']
    assert client.post(path).json()=={'ok':True}
    assert len(calls)==count and item['receipt_acked_at']==stamp
    item.pop('receipt_acked_at')
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


if __name__ == '__main__':
    unittest.main()
