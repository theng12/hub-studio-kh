"""A peer whose disk is full is avoided for a while; the refusal is not one of the item's three tries.

A node that has run out of disk answers a work POST `507 {"code": "OUTPUT_STORAGE_BLOCKED"}`
(`standalone._peer_refusal_response`). Inside the Hub's broker that used to be an ordinary worker
failure: it consumed one of MAX_TRIES=3 and set no avoid window, so the same full node could be
offered the item again at once and the item failed after three refusals - while another Mac with
free disk sat idle. Now the item stays queued, its try is given back, and that Mac is left out of
selection for DISK_FULL_AVOID_S so the pool offers the item to a Mac that can take it.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from components.hub.backend import broker


def _peer_studio() -> dict:
    return {
        "id": "image@node-a", "modality": "image", "machine": "node-a",
        "host": "10.0.0.9", "port": 51201, "hub_port": 51201,
        "hub_token": "fleet-token", "local": False,
        "runtime_identity": {"id": "fleet:node-a", "compatible": True},
    }


def _dispatched_item(tries: int = 1):
    submitted = broker.submit_batch({
        "modality": "image", "model": "model-a", "routing": "pool",
        "items": [{"prompt": "hello"}],
    })
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = _peer_studio()
    item.update(state="running", studio=studio["id"], tries=tries)
    broker._busy.add(studio["id"])
    return batch, item, studio


class _Refusal:
    def __init__(self, status: int, payload: dict):
        self.status, self.payload = status, payload

    async def post(self, url, **kwargs):
        return httpx.Response(self.status, json=self.payload, request=httpx.Request("POST", url))


_FULL = {"detail": "managed output storage has insufficient free space", "code": "OUTPUT_STORAGE_BLOCKED"}


def test_a_full_peer_defers_the_item_and_is_avoided_without_using_a_try(fixture_monitor):
    batch, item, studio = _dispatched_item()

    asyncio.run(broker._run_item(_Refusal(507, _FULL), batch, item, studio))

    assert item["state"] == "queued", f"{item['state']}: {item['error']}"
    assert item["tries"] == 0, "a refusal at the door consumed one of the item's tries"
    now = time.time()
    assert now + broker.DISK_FULL_AVOID_S - 5 < item["avoid_machines"][studio["machine"]] <= now + broker.DISK_FULL_AVOID_S
    assert not broker._item_allows_studio(item, studio, now), "the full Mac is still selectable"
    assert broker._item_allows_studio(item, studio, now + broker.DISK_FULL_AVOID_S + 1)
    assert broker.item_errors(batch) == [], "a full disk is a wait, not a failed item"


def test_the_last_try_is_not_lost_to_a_full_disk(fixture_monitor):
    """Old behaviour: on try 3 of 3 the same 507 ended the item as an error."""
    batch, item, studio = _dispatched_item(tries=broker.MAX_TRIES)

    asyncio.run(broker._run_item(_Refusal(507, _FULL), batch, item, studio))

    assert item["state"] == "queued" and item["tries"] == broker.MAX_TRIES - 1
    assert broker.item_errors(batch) == []


def test_repeated_full_disk_refusals_never_exhaust_the_tries(fixture_monitor):
    batch, item, studio = _dispatched_item(tries=0)
    for _ in range(broker.MAX_TRIES + 2):
        item.update(state="running", tries=int(item["tries"]) + 1)
        broker._busy.add(studio["id"])
        asyncio.run(broker._run_item(_Refusal(507, _FULL), batch, item, studio))
    assert item["state"] == "queued" and item["tries"] == 0


def test_a_507_with_no_code_is_still_a_full_disk(fixture_monitor):
    batch, item, studio = _dispatched_item()
    asyncio.run(broker._run_item(_Refusal(507, {"detail": "insufficient storage"}), batch, item, studio))
    assert item["state"] == "queued" and item["tries"] == 0 and studio["machine"] in item["avoid_machines"]
