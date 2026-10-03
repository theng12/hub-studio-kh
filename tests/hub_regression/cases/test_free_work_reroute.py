"""Free local work is re-run when the Mac or worker doing it dies (owner decision 2026-10-03).

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

from components.hub.backend import broker


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
        # Same id again would be answered with the dead job by the restarted worker.
        assert item["client_request_id"] == f"studiohub:{batch['id']}:0:rerun-1"


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
