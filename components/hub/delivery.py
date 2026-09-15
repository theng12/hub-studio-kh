"""Existing Hub artifact receipt contract, independent of legacy main."""
from urllib.parse import quote
import time

import httpx
from fastapi import HTTPException

from .backend import broker, ledger
from . import output_storage
from .backend.peers import studio_request


def register_routes(app, monitor):
    @app.post('/api/hub/jobs/{batch_id}/items/{item_index}/ack')
    async def acknowledge(batch_id: str, item_index: int):
        # The authenticated caller asserts that it has verified delivery.
        # Merely viewing/downloading an artifact never reaches this route.
        batch = broker.batches.get(batch_id) or ledger.load_batch(batch_id)
        item = next((row for row in (batch or {}).get('items', [])
                     if row.get('index') == item_index), None)
        if not item:
            raise HTTPException(404, 'unknown job item')
        if item.get('state') != 'done':
            raise HTTPException(409, 'only completed output can be acknowledged')
        if item.get('receipt_acked_at'):
            # The receipt is durable, but a previous output-index write may
            # have failed. Repair that conservative protection on retries.
            output_storage.mark_receipt_acked(batch_id=batch_id, item_index=item_index)
            return {'ok': True}
        worker = next((row for row in monitor.registry
                       if row['id'] == item.get('studio')), None)
        job_id = item.get('studio_job_id')
        if worker is None or not isinstance(job_id, str) or not job_id:
            raise HTTPException(404, 'worker job is not available')
        url, headers = studio_request(
            worker, f'/api/generate/jobs/{quote(job_id, safe="")}/ack')
        release_transfer = output_storage.acquire_transfer(batch_id=batch_id, item_index=item_index)
        try:
            try:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await client.post(url, headers=headers, timeout=15)
                    response.raise_for_status()
                    try:
                        receipt = response.json()
                    except ValueError as exc:
                        raise HTTPException(502, 'render worker returned an invalid receipt') from exc
                    if not isinstance(receipt, dict) or receipt.get('ok') is not True:
                        raise HTTPException(502, 'render worker rejected receipt acknowledgement')
            except httpx.HTTPError as exc:
                raise HTTPException(502, 'render worker did not acknowledge receipt') from exc
            item['receipt_acked_at'] = time.time()
            try:
                ledger.save_batch(batch)
            except Exception:
                # A failed local save must remain retryable even though the
                # worker already accepted the idempotent receipt.
                item.pop('receipt_acked_at', None)
                raise
            # A failed index update is conservative: the worker and batch receipt
            # remain durable but shared cleanup still treats output as unacked.
            output_storage.mark_receipt_acked(batch_id=batch_id, item_index=item_index)
            return {'ok': True}
        finally:
            release_transfer()
