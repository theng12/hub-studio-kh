"""A reserved Mac that answers the voice-reference upload is a wait, exactly as on the generate POST.

Overnight 2026-10-08/09 (review, section 1): 276 attempts on 252 Breeze jobs - mostly at sites 1600
and 1500 - failed "VOICE_REFERENCE_REJECTED: the Voice worker rejected the reference: local is
reserved for other work on this Mac". That is the serving Mac's lease (`409` + ``MACHINE_BUSY``),
which the generate POST already treats as "keep it queued, avoid that Mac briefly, give the try
back" (`test_pool_peer_machine_busy.py`). The reference upload, the request just before it, made it
final.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from components.hub.backend import broker

BUSY = {"detail": "local is reserved for other work on this Mac", "code": "MACHINE_BUSY"}


def _peer_voice() -> dict:
    return {
        "id": "voice@node-a", "modality": "voice", "machine": "node-a",
        "host": "10.0.0.9", "port": 51201, "hub_port": 51201,
        "hub_token": "fleet-token", "local": False,
        "runtime_identity": {"id": "fleet:node-a", "compatible": True},
    }


def _dispatched(monkeypatch, *, reference: bool):
    monkeypatch.setattr(broker.execution_assets, "read_voice_reference", lambda _asset_id: ({
        "sha256": "a" * 64, "expires_at": time.time() + 600, "audio_extension": ".wav",
        "media_type": "audio/wav", "transcript": "hello", "transcript_segments": [],
    }, b"RIFF"))
    shared = {"voice_reference_asset_id": "b" * 24} if reference else {}
    submitted = broker.submit_batch({"modality": "voice", "model": "model-v", "routing": "pool",
                                     "sharedParams": shared, "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = _peer_voice()
    item.update(state="running", studio=studio["id"], tries=1)
    broker._busy.add(studio["id"])
    return batch, item, studio


class _Busy:
    def __init__(self):
        self.urls: list[str] = []

    async def post(self, url, **kwargs):
        self.urls.append(url)
        return httpx.Response(409, json=BUSY, request=httpx.Request("POST", url))


def _outcome(batch, item, studio) -> dict:
    now = time.time()
    return {
        "state": item["state"], "error_code": item.get("error_code"), "tries": item["tries"],
        "retry_at": item["retry_at"], "published": broker.item_errors(batch),
        "avoided": now < item["avoid_machines"][studio["machine"]] <= now + broker.MACHINE_RESERVED_AVOID_S,
        "reserved": broker._machine_reserved_until.get(studio["machine"], 0) > now,
    }


def test_a_busy_mac_on_the_reference_upload_waits_like_the_generate_path(fixture_monitor, monkeypatch):
    batch, item, studio = _dispatched(monkeypatch, reference=True)
    client = _Busy()
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert [url.rsplit("/api/", 1)[1] for url in client.urls] == ["generate/txt2speech/reference"]
    assert item["state"] == "queued", f"{item['state']}: {item['error']}"
    assert "VOICE_REFERENCE_REJECTED" != item.get("error_code")
    assert "reserved for other work" in item["error"]
    on_reference = _outcome(batch, item, studio)

    broker._machine_reserved_until.clear()
    batch, item, studio = _dispatched(monkeypatch, reference=False)
    client = _Busy()
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert [url.rsplit("/api/", 1)[1] for url in client.urls] != ["generate/txt2speech/reference"]
    assert on_reference == _outcome(batch, item, studio) == {
        "state": "queued", "error_code": "MACHINE_BUSY", "tries": 0, "retry_at": None,
        "published": [], "avoided": True, "reserved": True}


def test_any_other_reference_refusal_is_still_final(fixture_monitor, monkeypatch):
    batch, item, studio = _dispatched(monkeypatch, reference=True)

    class Refuses:
        async def post(self, url, **kwargs):
            return httpx.Response(409, json={"detail": "model-v is paused on this Mac", "code": "MODEL_PAUSED"},
                                  request=httpx.Request("POST", url))

    asyncio.run(broker._run_item(Refuses(), batch, item, studio))
    assert (item["state"], item["error_code"]) == ("error", "VOICE_REFERENCE_REJECTED")
