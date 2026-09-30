"""A Mac that is reserved on the far side is a wait, not a verdict on the work.

Reported from the Gateway: a batch submitted to a controller with ``routing: "pool"`` failed
its first item terminally -

    {"item_index": 0, "code": "WORKER_TERMINAL_ERROR",
     "message": "HTTP 409: local is reserved for other work on this Mac"}

- because the dispatcher chose a joined Mac that was reserved by other work at that moment.
Inside this Hub the same condition is a *skip*: `_eligible_studios` refuses to hand an item to
a machine in `busy_machines()`. A lease is transient by construction - the work holding it
finishes, or the lease expires - so the answer that arrives over the peer surface must leave
the item queued and take that Mac out of the next selection, so the pool offers the item to a
free machine instead.

The refusal is the peer surface's own (`standalone._peer_admission_refusal`), and its body
carries the code beside ``detail`` rather than inside it. Reading only the worker's nested
shape lost the code: the item fell through to `WORKER_TERMINAL_ERROR`, which is why the
reported message named no cause at all.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from components.hub.backend import broker


def _peer_studio() -> dict:
    """One joined node, as `fleet_nodes.remote_studios` presents it to a controller."""
    return {
        "id": "image@node-a", "modality": "image", "machine": "node-a",
        "host": "10.0.0.9", "port": 51201, "hub_port": 51201,
        "hub_token": "fleet-token", "local": False,
        "runtime_identity": {"id": "fleet:node-a", "compatible": True},
    }


def _dispatched_item():
    """A pool batch whose one item the dispatcher has just committed to a joined node."""
    submitted = broker.submit_batch({
        "modality": "image", "model": "model-a", "routing": "pool",
        "items": [{"prompt": "hello"}],
    })
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = _peer_studio()
    item.update(state="running", studio=studio["id"], tries=1)
    broker._busy.add(studio["id"])
    return batch, item, studio


class _Refusal:
    """The peer Hub's answer to a work POST, as `standalone._peer_refusal_response` writes it."""

    def __init__(self, status: int, payload: dict):
        self.status = status
        self.payload = payload

    async def post(self, url, **kwargs):
        return httpx.Response(self.status, json=self.payload,
                              request=httpx.Request("POST", url))


def test_a_reserved_peer_defers_the_item_and_avoids_that_machine(fixture_monitor):
    batch, item, studio = _dispatched_item()
    client = _Refusal(409, {"detail": "local is reserved for other work on this Mac",
                            "code": "MACHINE_BUSY"})

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert item["state"] == "queued", f"{item['state']}: {item['error']}"
    assert item["error_code"] == "MACHINE_BUSY", "the reason must survive as the peer named it"
    # Premise changed (owner decision 2026-09-30, "never idle while eligible work is queued"): the
    # item used to be parked for 30 s with the Mac avoided for 90 s, so a Mac that freed a moment
    # later sat idle for over a minute. It is now retried on the dispatcher's own heartbeat, and the
    # Mac is out of selection only for the short avoid window - for every item aimed at it.
    assert not item["retry_at"], "a busy Mac must not park the item: the heartbeat retries it"
    now = time.time()
    assert now < item["avoid_machines"][studio["machine"]] <= now + broker.MACHINE_RESERVED_AVOID_S
    assert broker._machine_reserved_until[studio["machine"]] > now
    assert item["tries"] == 0, "a refusal at the door consumed no generation attempt"
    assert broker.item_errors(batch) == [], "a wait must not be published as a failed item"
    assert "reserved for other work" in item["error"], "the peer's own reason must be kept"
    assert not broker._item_allows_studio(item, studio, time.time()), (
        "the reserved Mac must be out of the next selection, so the pool picks a free one")
    assert broker._item_allows_studio(item, studio, time.time() + 2 * broker.MACHINE_RESERVED_AVOID_S), (
        "and only briefly: the same Mac is offered the work again as soon as the window is over")


def test_a_peers_non_reservation_refusal_is_still_terminal(fixture_monitor):
    """A paused model is an operator decision, not a wait: retrying elsewhere cannot fix it."""
    batch, item, studio = _dispatched_item()
    client = _Refusal(409, {"detail": "model-a is paused on this Mac", "code": "MODEL_PAUSED"})

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert item["state"] == "error"
    assert [row["code"] for row in broker.item_errors(batch)] == ["MODEL_PAUSED"]


def test_a_wait_names_the_free_machine_and_not_voice(fixture_monitor):
    """An image batch held by an avoid window is not a Voice problem.

    `_shared_voice_allows_studio` returns True for every non-voice modality, so
    for an image batch the *only* thing that can make the dispatcher skip a
    studio is `_item_allows_studio` - the temporary avoid window a reservation or
    a recent failure writes. That was still reported as "Waiting for a Voice
    Studio where the selected shared voice is synchronized", which sent the
    operator to fix voice on a Mac that was merely reserved by another queue.

    The MACHINE_BUSY deferral above is what makes this reachable constantly, so
    the note has to name the wait it is actually reporting.
    """
    studio = _peer_studio()
    batch = {"modality": "image", "routing": "pool", "model": "model-a"}
    item = {"prompt": "hello", "avoid_machines": {studio["machine"]: time.time() + 60.0}}

    # Pin the premise: the voice predicate is innocent here, so the avoid window
    # is the only reason this studio takes nothing.
    assert broker._shared_voice_allows_studio(batch, item, studio)
    assert not broker._item_allows_studio(item, studio, time.time())

    note = broker._no_candidate_note(batch, [item], studio)
    assert "Voice" not in note
    assert "machine" in note.lower()


def test_a_voice_wait_still_names_the_shared_voice(fixture_monitor, monkeypatch):
    """The Voice wording must survive where it was true - an unsynchronized clone."""
    studio = _peer_studio()
    batch = {"modality": "voice", "routing": "pool",
             "shared_params": {"voice_library_id": "lib-1"}}
    item = {"prompt": "hello"}
    monkeypatch.setattr(broker.shared_voices, "synced_studio_ids",
                        lambda voice_id: {"voice@somewhere-else"})

    assert not broker._shared_voice_allows_studio(batch, item, studio)
    note = broker._no_candidate_note(batch, [item], studio)
    assert "shared voice is synchronized" in note
