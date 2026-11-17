"""Free local work is re-run when the Mac or worker doing it dies (owner decision 2026-10-03).

Since 2026-10-05 the same holds when the Hub itself restarts under a job (the tests at the end):
the item runs again and its original is adopted if it finished, instead of the item being fenced
`uncertain` and holding its Mac for the 30-minute sweep.

Before: a node dying mid-job regenerated Image and Voice elsewhere, but left Music and Video
`uncertain` (fenced until an operator acted), any job on this Mac's own worker too, and a worker
that crashed and restarted under a job left it `uncertain` in every lane. Now every modality is
re-run, the original is told to stop when its Mac answers, and if the original had already
finished, its result is used and the re-run cancelled.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from components.hub.backend import broker, ledger


def _studio(machine: str, modality: str, host: str = "10.0.0.9") -> dict:
    return {"id": f"{modality}@{machine}", "modality": modality, "machine": machine,
            "host": host, "port": 51201, "hub_port": 51201, "hub_token": "fleet-token",
            "local": False, "runtime_identity": {"id": f"fleet:{machine}", "compatible": True}}


def _attempt(fixture_monitor, monkeypatch, modality: str, studio: dict) -> tuple[dict, dict]:
    monkeypatch.setattr(broker, "POLL_S", 0.0)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0.0)
    # One round of the background watch: a Mac that stays down is not waited on for 30 minutes.
    monkeypatch.setattr(broker, "ABANDONED_WATCH_S", 0.0)

    async def noop(*args, **kwargs):
        return None
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    batch = {"id": f"{modality}-reroute", "modality": modality, "model": "fixture/model",
             "shared_params": {}, "cancelled": False, "routing": "pool",
             "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "proof", "params": {}, "seed": -1, "state": "running",
            "tries": 1, "studio": studio["id"], "studio_job_id": None, "retry_at": None,
            "error": None,
            "client_request_id": f"studiohub:{batch['id']}:0" if modality == "voice" else None}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    broker._busy.add(studio["id"])
    return batch, item


def _response(method: str, url: str, payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request(method, url))


class Node:
    """A worker behind a peer (or this Mac's own): accepts, then answers polls or goes away."""

    def __init__(self, polls: list[dict] | None = None):
        self.polls = list(polls or [])
        self.down = False
        self.posted: list[dict] = []
        self.deleted: list[str] = []
        self.status_after_down: dict | None = None

    async def post(self, url, **kwargs):
        self.posted.append(kwargs.get("json") or {})
        job_id = f"job-{len(self.posted)}"
        job = {"id": job_id, "state": "queued", "worker_id": "w", "machine_id": "local"}
        if "txt2speech" in url:
            job["params"] = {"client_request_id": (kwargs.get("json") or {}).get("client_request_id")}
        return _response("POST", url, {"job": job})

    async def get(self, url, **kwargs):
        if self.down:
            raise httpx.ConnectError("node stopped answering")
        job = dict(self.polls.pop(0) if len(self.polls) > 1 else self.polls[0])
        job.setdefault("id", url.rsplit("/", 1)[-1])
        job.setdefault("worker_id", "w")
        job.setdefault("machine_id", "local")
        return _response("GET", url, {"job": job})

    async def delete(self, url, **kwargs):
        if self.down:
            raise httpx.ConnectError("node stopped answering")
        self.deleted.append(url)
        return _response("DELETE", url, {})


class GoesDown(Node):
    """Answers `running` once, then the Mac disappears (until a test brings it back)."""

    stay_up = False

    async def get(self, url, **kwargs):
        response = await super().get(url, **kwargs)
        self.down = not self.stay_up
        return response


class Network:
    """One shared client, as the Hub has: each request reaches the Mac its URL names."""

    def __init__(self, nodes: dict[str, Node]):
        self.nodes = nodes

    def _node(self, url: str) -> Node:
        return next(node for host, node in self.nodes.items() if f"//{host}:" in url)

    async def post(self, url, **kwargs):
        return await self._node(url).post(url, **kwargs)

    async def get(self, url, **kwargs):
        return await self._node(url).get(url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._node(url).delete(url, **kwargs)


async def _run(batch, item, studio, node):
    await broker._run_item(node, batch, item, studio)
    await asyncio.gather(*broker._abandoned_peer_cancels)


@pytest.mark.parametrize("modality", ["image", "music", "video", "voice"])
def test_a_node_that_dies_mid_job_is_rerun_in_every_modality(fixture_monitor, monkeypatch, modality):
    """Music and Video stayed `uncertain` here before; now all four are re-run elsewhere."""
    studio = _studio("node-a", modality)
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    batch, item = _attempt(fixture_monitor, monkeypatch, modality, studio)
    node = GoesDown([{"state": "running", **({"params": {"client_request_id": item["client_request_id"]}}
                                             if modality == "voice" else {})}])

    asyncio.run(_run(batch, item, studio, node))

    assert item["state"] == "queued", (item["state"], item.get("error"), item.get("recovery"))
    assert item.get("studio_job_id") is None and item.get("studio") is None
    assert item["avoid_machines"]["node-a"] > time.time(), "the re-run goes to another Mac first"
    assert "node-a" not in broker.busy_machines(), "a dead node's attempt fences nothing"


def test_this_macs_own_worker_dying_mid_job_is_rerun(fixture_monitor, monkeypatch):
    """A job on this Mac's own worker used to be fenced `uncertain` when the worker vanished."""
    studio = next(row for row in fixture_monitor.registry if row["id"] == "music")
    batch, item = _attempt(fixture_monitor, monkeypatch, "music", studio)
    node = GoesDown([{"state": "running"}])

    asyncio.run(_run(batch, item, studio, node))

    assert item["state"] == "queued", (item["state"], item.get("error"))
    assert item.get("studio_job_id") is None
    assert studio["machine"] not in broker.busy_machines()


@pytest.mark.parametrize("modality", ["image", "music", "video", "voice"])
def test_a_worker_that_crashed_and_restarted_reruns_its_job(fixture_monitor, monkeypatch, modality):
    """A restarted worker marks the job it lost (WORKER_RESTART_UNCERTAIN; Voice's
    EXECUTION_IDENTITY_LOST): the item runs again - the same Mac is allowed, whichever is first."""
    studio = _studio("node-a", modality)
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    batch, item = _attempt(fixture_monitor, monkeypatch, modality, studio)
    restarted = ({"state": "uncertain", "error": "Generation status is uncertain after Voice Studio restarted.",
                  "error_code": "EXECUTION_IDENTITY_LOST", "params": {"client_request_id": item["client_request_id"]}}
                 if modality == "voice" else
                 {"state": "error", "error": "Worker restarted before completion",
                  "error_code": "WORKER_RESTART_UNCERTAIN"})
    node = Node([{"state": "running", **({"params": {"client_request_id": item["client_request_id"]}}
                                          if modality == "voice" else {})}, restarted])

    asyncio.run(_run(batch, item, studio, node))

    assert item["state"] == "queued", (item["state"], item.get("error"), item.get("recovery"))
    assert item.get("studio_job_id") is None
    assert "node-a" not in (item.get("avoid_machines") or {}), "the restarted worker may take it again"
    assert broker._item_allows_studio(item, studio, time.time() + 60)
    if modality == "voice":
        # The id is kept (0.6.242): a worker still holding the job answers that id with the job it
        # already has, so the retry reattaches instead of generating it twice. A fresh `:rerun-N`
        # belongs to a person's retry, or to an id answered EXECUTION_IDENTITY_LOST - both later.
        assert item["client_request_id"] == f"studiohub:{batch['id']}:0"


def test_the_original_finishing_first_wins_and_the_rerun_is_cancelled(fixture_monitor, monkeypatch):
    first, second = _studio("node-a", "image", "10.0.0.9"), _studio("node-b", "image", "10.0.0.10")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, first, second])
    batch, item = _attempt(fixture_monitor, monkeypatch, "image", first)
    monkeypatch.setattr(broker, "ABANDONED_WATCH_S", 60.0)
    node_a, node_b = GoesDown([{"state": "running"}]), Node([{"state": "running"}])
    network = Network({"10.0.0.9": node_a, "10.0.0.10": node_b})

    async def scenario():
        await broker._run_item(network, batch, item, first)
        assert item["state"] == "queued"
        # The re-run is dispatched to node-b and accepted there...
        item.update(state="running", studio=second["id"], tries=2)
        broker._busy.add(second["id"])
        rerun = asyncio.create_task(broker._run_item(network, batch, item, second))
        await asyncio.sleep(0)
        # ...and node-a comes back with its original job finished.
        node_a.down, node_a.stay_up = False, True
        node_a.polls = [{"state": "done", "output_url": "/api/generate/jobs/job-1/image",
                         "output_path": "/tmp/job-1.png", "media_type": "image/png",
                         "generated_at": time.time(), "bytes": 123, "sha256": "a" * 64}]
        await asyncio.gather(*broker._abandoned_peer_cancels)
        await asyncio.wait_for(rerun, timeout=10)

    asyncio.run(scenario())

    assert item["state"] == "done", (item["state"], item.get("error"))
    assert item["studio"] == first["id"] and item["studio_job_id"] == "job-1", "the original's result"
    assert any(url.endswith("/api/generate/jobs/job-1") for url in node_b.deleted), \
        "the re-run on node-b is cancelled"
    assert not node_a.deleted, "the finished original is kept, not cancelled"
    assert second["id"] not in broker._busy


def test_a_still_running_original_is_cancelled_when_its_mac_answers(fixture_monitor, monkeypatch):
    studio = _studio("node-a", "music")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    batch, item = _attempt(fixture_monitor, monkeypatch, "music", studio)
    node = GoesDown([{"state": "running"}])

    async def scenario():
        await broker._run_item(node, batch, item, studio)
        node.down, node.stay_up = False, True  # the Mac is back, its job still running
        await asyncio.gather(*broker._abandoned_peer_cancels)

    asyncio.run(scenario())

    assert [url.endswith("/api/generate/jobs/job-1") for url in node.deleted] == [True]
    assert item["state"] == "queued", "nothing was adopted from a job that had not finished"


# -- The Hub itself restarts under a job (owner, 2026-10-05) ----------------------------------------

_DONE = {"state": "done", "output_url": "/api/generate/jobs/job-1/image", "output_path": "/tmp/job-1.png",
         "media_type": "image/png", "bytes": 123, "sha256": "a" * 64}


def _restart(batch: dict) -> dict:
    """The Hub process ends and a new one loads the ledger, as `standalone`'s lifespan does."""
    ledger.save_batch(batch)
    broker.batches.clear()
    broker._busy.clear()
    broker.restore_batches()
    return broker.batches[batch["id"]]["items"][0]


async def _watch(network) -> None:
    await broker._watch_restored_attempts(network)
    await asyncio.gather(*broker._abandoned_peer_cancels)


@pytest.mark.parametrize("modality", ["image", "music", "video", "voice"])
@pytest.mark.parametrize("machine", ["local", "node-a"])
def test_a_hub_restart_reruns_the_job_and_holds_no_mac(fixture_monitor, monkeypatch, modality, machine):
    """2026-10-05: a controller restart left five finished Z-Image items `uncertain`, holding five idle
    Macs for 30 minutes and parking their Gateway jobs as "Needs recovery"."""
    studio = {**_studio(machine, modality), "local": machine == "local"}
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    batch, item = _attempt(fixture_monitor, monkeypatch, modality, studio)
    item.update(studio_job_id="job-1", machine=machine)

    restored = _restart(batch)

    assert restored["state"] == "queued", (restored["state"], restored.get("error"))
    assert restored["studio"] is None and restored["studio_job_id"] is None
    assert restored["tries"] == 1, "the interrupted try stays spent"
    assert broker.busy_machines() == set(), "no Mac is held for an item nobody runs"
    assert [(row[2], row[3]) for row in broker._restored_attempts] == [(studio["id"], "job-1")]
    if modality == "voice":
        # Kept across a Hub restart for the same reason: the original is adopted by its own id.
        assert restored["client_request_id"] == f"studiohub:{batch['id']}:0"


def test_a_restart_adopts_an_original_that_finished_meanwhile(fixture_monitor, monkeypatch):
    studio = _studio("node-a", "image")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    batch, item = _attempt(fixture_monitor, monkeypatch, "image", studio)
    item["studio_job_id"] = "job-1"
    restored = _restart(batch)
    node = Node([{**_DONE, "generated_at": time.time()}])

    asyncio.run(_watch(Network({"10.0.0.9": node})))

    assert restored["state"] == "done", (restored["state"], restored.get("error"))
    assert (restored["studio"], restored["studio_job_id"]) == (studio["id"], "job-1")
    assert node.posted == [] and node.deleted == [], "nothing re-run, nothing cancelled"


def test_after_a_restart_the_original_finishing_first_wins_over_the_rerun(fixture_monitor, monkeypatch):
    first, second = _studio("node-a", "image", "10.0.0.9"), _studio("node-b", "image", "10.0.0.10")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, first, second])
    batch, item = _attempt(fixture_monitor, monkeypatch, "image", first)
    item["studio_job_id"] = "job-1"
    restored = _restart(batch)
    node_a, node_b = Node([{**_DONE, "generated_at": time.time()}]), Node([{"state": "running"}])
    network = Network({"10.0.0.9": node_a, "10.0.0.10": node_b})

    async def scenario():
        # The re-run is dispatched to node-b and accepted there; then the watch reaches node-a.
        restored.update(state="running", studio=second["id"], tries=2)
        broker._busy.add(second["id"])
        rerun = asyncio.create_task(broker._run_item(network, broker.batches[batch["id"]], restored, second))
        while not node_b.posted:
            await asyncio.sleep(0)
        await _watch(network)
        await asyncio.wait_for(rerun, timeout=10)

    asyncio.run(scenario())

    assert restored["state"] == "done"
    assert (restored["studio"], restored["studio_job_id"]) == (first["id"], "job-1"), "the original's result"
    assert any(url.endswith("/api/generate/jobs/job-1") for url in node_b.deleted), "the re-run is cancelled"
    assert not node_a.deleted
    assert broker.busy_machines() == set()


@pytest.mark.parametrize("original", ["running", "done"])
def test_after_a_restart_the_rerun_finishing_first_wins_over_the_original(
        fixture_monitor, monkeypatch, original):
    first, second = _studio("node-a", "image", "10.0.0.9"), _studio("node-b", "image", "10.0.0.10")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, first, second])
    batch, item = _attempt(fixture_monitor, monkeypatch, "image", first)
    item["studio_job_id"] = "job-7"
    restored = _restart(batch)
    node_a = Node([{**_DONE, "generated_at": time.time()} if original == "done" else {"state": "running"}])
    node_b = Node([{**_DONE, "generated_at": time.time()}])
    network = Network({"10.0.0.9": node_a, "10.0.0.10": node_b})

    async def scenario():
        restored.update(state="running", studio=second["id"], tries=2)
        broker._busy.add(second["id"])
        await broker._run_item(network, broker.batches[batch["id"]], restored, second)
        await _watch(network)

    asyncio.run(scenario())

    assert restored["state"] == "done"
    assert restored["studio"] == second["id"], "the re-run's result stands"
    if original == "running":
        assert [url.endswith("/api/generate/jobs/job-7") for url in node_a.deleted] == [True], \
            "the original still running is told to stop"
    else:
        assert node_a.deleted == [], "a late original is simply not used"


def test_two_restarts_in_a_row_rerun_and_a_third_ends_it_as_a_known_failure(fixture_monitor, monkeypatch):
    studio = _studio("node-a", "image")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    batch, item = _attempt(fixture_monitor, monkeypatch, "image", studio)
    item["studio_job_id"] = "job-1"
    published: list[str] = []

    async def finish(_client, b):
        published.append(b["id"])
    monkeypatch.setattr(broker, "_maybe_finish", finish)

    restored = _restart(batch)
    assert restored["state"] == "queued"
    restored.update(state="running", studio=studio["id"], studio_job_id="job-2", tries=2)
    restored = _restart(broker.batches[batch["id"]])
    assert restored["state"] == "queued", "a second restart re-runs it again"
    assert [row[3] for row in broker._restored_attempts] == ["job-1", "job-2"]
    restored.update(state="running", studio=studio["id"], studio_job_id="job-3", tries=3)
    restored = _restart(broker.batches[batch["id"]])

    assert (restored["state"], restored["error_code"]) == ("error", "HUB_RESTARTED")
    assert broker.busy_machines() == set()
    node = Node([{"state": "cancelled"}])
    asyncio.run(_watch(Network({"10.0.0.9": node})))
    assert published == [batch["id"]], "the failure is published, so the Gateway reads a known failure"
    assert broker._restored_attempts == []


def test_a_cancel_in_flight_at_restart_keeps_the_cautious_fence(fixture_monitor, monkeypatch):
    studio = _studio("node-a", "image")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    batch, item = _attempt(fixture_monitor, monkeypatch, "image", studio)
    item["studio_job_id"] = "job-1"
    batch["cancelled"] = True

    restored = _restart(batch)

    assert restored["state"] == "uncertain" and restored["studio_job_id"] == "job-1"
    assert broker._restored_attempts == []


@pytest.mark.parametrize("failure", ["metadata", "asset", "batch"])
def test_failed_original_finalization_keeps_the_replacement_execution(
        fixture_monitor, monkeypatch, failure):
    """Actual poll/finalization paths: no early winner flag may stop the only task."""
    first, second = _studio("node-a", "voice"), _studio("node-b", "voice", "10.0.0.10")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, first, second])
    batch, item = _attempt(fixture_monitor, monkeypatch, "voice", second)
    entered, release, at_poll, finish_rerun = (asyncio.Event() for _ in range(4))
    original_sleep = asyncio.sleep
    cancelled = []
    record_asset = ledger.record_asset
    save_batch = ledger.save_batch

    async def metadata(client, candidate, studio, *args):
        if studio["id"] == first["id"]:
            entered.set()
            await release.wait()
            if failure == "metadata":
                raise OSError("fixture metadata finalization failed")
        candidate.update(bytes=4, sha256="a" * 64, media_type="audio/wav")

    def asset(**fields):
        if failure == "asset" and fields.get("studio") == first["id"]:
            raise OSError("fixture asset ledger failed")
        return record_asset(**fields)

    def save(candidate):
        if failure == "batch" and any(
                row.get("studio") == first["id"] and row.get("state") == "done"
                for row in candidate["items"]):
            raise OSError("fixture batch ledger failed")
        return save_batch(candidate)

    async def stop(client, probe):
        cancelled.append(dict(probe))
        return True

    async def pause(delay):
        at_poll.set()
        await entered.wait()

    class Client:
        async def post(self, url, **kwargs):
            return _response("POST", url, {"job": {"id": "rerun", "state": "queued",
                "params": {"client_request_id": item["client_request_id"]}}})

        async def get(self, url, **kwargs):
            await finish_rerun.wait()
            return _response("GET", url, {"job": {"id": "rerun", "state": "done",
                "params": {"client_request_id": body["client_request_id"]},
                "output_url": "/api/generate/jobs/rerun/audio", "output_path": "/fixture/rerun.wav"}})

    body = {"client_request_id": item["client_request_id"]}
    original = {"id": "original", "state": "done", "params": {"client_request_id": "original-id"},
                "output_url": "/api/generate/jobs/original/audio", "output_path": "/fixture/original.wav"}
    monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", metadata)
    monkeypatch.setattr(broker, "_signal_worker_cancel", stop)
    monkeypatch.setattr(broker.asyncio, "sleep", pause)
    monkeypatch.setattr(ledger, "record_asset", asset)
    monkeypatch.setattr(ledger, "save_batch", save)
    monkeypatch.setattr(broker.memory_admission, "worker_free_floor_gb",
                        lambda *args, mode=None: None)

    async def scenario():
        rerun = asyncio.create_task(broker._run_item(Client(), batch, item, second))
        adopting = None
        try:
            await asyncio.wait_for(at_poll.wait(), 2)
            adopting = asyncio.create_task(broker._adopt_abandoned_attempt(
                Client(), batch, item, first, original, {"client_request_id": "original-id"}, time.time()))
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(3):
                await original_sleep(0)
            assert not cancelled and not rerun.done(), "live item lost its only execution task"
            assert (item["studio"], item["studio_job_id"]) == (second["id"], "rerun")
            release.set()
            await asyncio.wait_for(adopting, 2)
            assert not cancelled and not broker._superseded(item)
            assert item["state"] == "running" and not rerun.done()
        finally:
            release.set()
            finish_rerun.set()
            if adopting:
                await asyncio.wait_for(adopting, 2)
            await asyncio.wait_for(rerun, 2)
        assert item["state"] == "done" and item["studio_job_id"] == "rerun"
        assert ledger.load_batch(batch["id"])["items"][0]["studio_job_id"] != "original"

    asyncio.run(scenario())


@pytest.mark.parametrize("winner", ["original", "replacement"])
def test_voice_metadata_race_commits_only_the_first_finalized_result(
        fixture_monitor, monkeypatch, winner):
    first, second = _studio("node-a", "voice"), _studio("node-b", "voice", "10.0.0.10")
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, first, second])
    batch, item = _attempt(fixture_monitor, monkeypatch, "voice", second)
    item["studio_job_id"] = "replacement"
    entered = {name: asyncio.Event() for name in ("original", "replacement")}
    release = {name: asyncio.Event() for name in entered}
    stored = []
    record_asset = ledger.record_asset
    cancelled = []
    notified = []

    async def metadata(client, candidate, studio, *args):
        name = "original" if studio["id"] == first["id"] else "replacement"
        entered[name].set()
        await release[name].wait()
        candidate.update(bytes=4 if name == "original" else 8,
                         sha256=("a" if name == "original" else "b") * 64, media_type="audio/wav")

    def asset(**fields):
        stored.append(fields["studio"])
        return record_asset(**fields)

    async def stop(client, probe):
        cancelled.append(dict(probe))
        return True

    async def webhook(*args, **kwargs):
        notified.append(item["studio_job_id"])

    monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", metadata)
    monkeypatch.setattr(ledger, "record_asset", asset)
    monkeypatch.setattr(broker, "_signal_worker_cancel", stop)
    monkeypatch.setattr(broker, "_post_item_webhook", webhook)
    original = {"id": "original", "state": "done", "params": {"client_request_id": "original-id"},
                "output_url": "/api/generate/jobs/original/audio", "output_path": "/fixture/original.wav"}
    replacement = {"id": "replacement", "state": "done", "params": {"client_request_id": item["client_request_id"]},
                   "output_url": "/api/generate/jobs/replacement/audio", "output_path": "/fixture/replacement.wav"}

    async def scenario():
        tasks = {
            "replacement": asyncio.create_task(broker._record_worker_success(
                None, batch, item, second, replacement,
                {"client_request_id": item["client_request_id"]}, time.time())),
            "original": asyncio.create_task(broker._adopt_abandoned_attempt(
                None, batch, item, first, original, {"client_request_id": "original-id"}, time.time())),
        }
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 2)
        release[winner].set()
        await asyncio.wait_for(tasks[winner], 2)
        loser = "replacement" if winner == "original" else "original"
        release[loser].set()
        await asyncio.wait_for(tasks[loser], 2)
        expected = first if winner == "original" else second
        assert item["state"] == "done" and item["studio_job_id"] == winner
        assert item["studio"] == expected["id"]
        assert item["sha256"] == ("a" if winner == "original" else "b") * 64
        assert stored == [expected["id"]], "a losing candidate must not write a second asset"
        assert bool(cancelled) == (winner == "original")
        # Publication still uses the existing reservation boundary exactly once.
        batch["item_webhook"] = "http://fixture.invalid/webhook"
        reserved = broker._reserve_item_webhook(batch, item)
        if reserved:
            await broker._post_item_webhook(None, batch, item, reserved=True)
        assert not broker._reserve_item_webhook(batch, item)
        assert notified == [winner]

    asyncio.run(scenario())


def test_original_adoption_rechecks_cancellation_after_metadata(fixture_monitor, monkeypatch):
    first, second = _studio("node-a", "voice"), _studio("node-b", "voice")
    batch, item = _attempt(fixture_monitor, monkeypatch, "voice", second)
    item["studio_job_id"] = "replacement"
    entered, release = asyncio.Event(), asyncio.Event()
    assets, cancelled = [], []

    async def metadata(*args):
        entered.set()
        await release.wait()

    async def stop(*args):
        cancelled.append(True)

    monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", metadata)
    monkeypatch.setattr(broker, "_signal_worker_cancel", stop)
    monkeypatch.setattr(ledger, "record_asset", lambda **fields: assets.append(fields))
    job = {"id": "original", "state": "done", "params": {"client_request_id": "original-id"},
           "output_url": "/api/generate/jobs/original/audio", "output_path": "/fixture/original.wav"}

    async def scenario():
        task = asyncio.create_task(broker._adopt_abandoned_attempt(
            None, batch, item, first, job, {"client_request_id": "original-id"}, time.time()))
        await asyncio.wait_for(entered.wait(), 2)
        batch["cancelled"] = True
        item["state"] = "cancel_requested"
        release.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    assert item["state"] == "cancel_requested" and item["studio_job_id"] == "replacement"
    assert not assets and not cancelled and not broker._superseded(item)


def test_original_adoption_rejects_a_changed_receipt_without_touching_rerun(fixture_monitor, monkeypatch):
    first, second = _studio("node-a", "voice"), _studio("node-b", "voice")
    batch, item = _attempt(fixture_monitor, monkeypatch, "voice", second)
    item["studio_job_id"] = "replacement"
    before = dict(item)
    job = {"id": "original", "state": "done", "params": {"client_request_id": "wrong-request"},
           "output_url": "/api/generate/jobs/original/audio", "output_path": "/fixture/original.wav"}
    asyncio.run(broker._adopt_abandoned_attempt(
        None, batch, item, first, job, {"client_request_id": "original-id"}, time.time()))
    assert item == before


def test_original_winner_is_durable_before_the_replacement_is_cancelled(fixture_monitor, monkeypatch):
    first, second = _studio("node-a", "voice"), _studio("node-b", "voice")
    batch, item = _attempt(fixture_monitor, monkeypatch, "voice", second)
    item["studio_job_id"] = "replacement"
    item["state"] = "queued"
    cancelled = []

    async def metadata(client, candidate, *args):
        # The dispatcher starts the replacement while the original is finalized.
        item.update(state="running", tries=2, _reserved=4.0, _dispatch_generation=11)
        candidate.update(bytes=4, sha256="a" * 64, media_type="audio/wav")

    async def stop(client, probe):
        saved = ledger.load_batch(batch["id"])["items"][0]
        assert saved["state"] == "done" and saved["studio_job_id"] == "original"
        assert saved["asset_id"] == item["asset_id"] and saved["sha256"] == "a" * 64
        assert broker._superseded(saved) and broker._superseded(item)
        assert saved["_reserved"] == item["_reserved"] == 4.0
        assert saved["_dispatch_generation"] == item["_dispatch_generation"] == 11
        assert saved["tries"] == item["tries"] == 2
        cancelled.append(dict(probe))
        return True

    monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", metadata)
    monkeypatch.setattr(broker, "_signal_worker_cancel", stop)
    job = {"id": "original", "state": "done", "params": {"client_request_id": "original-id"},
           "output_url": "/api/generate/jobs/original/audio", "output_path": "/fixture/original.wav"}
    asyncio.run(broker._adopt_abandoned_attempt(
        None, batch, item, first, job, {"client_request_id": "original-id"}, time.time()))
    assert cancelled == [{"studio": second["id"], "studio_job_id": "replacement"}]


def test_successful_finalization_clears_prior_owned_errors(fixture_monitor, monkeypatch):
    from components.hub import output_storage
    studio = _studio("node-a", "voice")
    batch, item = _attempt(fixture_monitor, monkeypatch, "voice", studio)
    item.update(studio_job_id="complete", artifact_metadata_error="old hash failure",
                output_storage_error="old registration failure", _reserved=3.0,
                cancel_signal_sent_at=0.0)

    async def metadata(client, candidate, *args):
        candidate.update(bytes=4, sha256="a" * 64, media_type="audio/wav")
        candidate.pop("artifact_metadata_error", None)
        # Concurrent unrelated task state must survive this candidate's commit.
        item["_reserved"] = 7.0
        item["cancel_signal_sent_at"] = 123.0

    monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", metadata)
    monkeypatch.setattr(output_storage, "register_finalized_output", lambda **fields: None)
    job = {"id": "complete", "state": "done", "params": {"client_request_id": item["client_request_id"]},
           "output_url": "/api/generate/jobs/complete/audio", "output_path": "/fixture/complete.wav"}
    asyncio.run(broker._record_worker_success(
        None, batch, item, studio, job, {"client_request_id": item["client_request_id"]}, time.time()))
    assert item["state"] == "done"
    assert "artifact_metadata_error" not in item and "output_storage_error" not in item
    assert item["_reserved"] == 7.0 and item["cancel_signal_sent_at"] == 123.0
