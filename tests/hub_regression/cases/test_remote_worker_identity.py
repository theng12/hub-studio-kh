"""A remote worker's own machine id is not the node id the Hub routes through.

The Hub stores the routing machine (the node id) on the item, while the worker
reports its own local machine id, so comparing them failed every remote attempt.
"""
from __future__ import annotations

import pytest

from components.hub.backend import broker


def _item() -> dict:
    return {"studio_job_id": "job-1", "model_revision": "r" * 40,
            "runtime_revision": "x" * 64, "worker_id": "music", "machine": "node-a"}


def _job() -> dict:
    return {"id": "job-1", "state": "running", "model_revision": "r" * 40,
            "runtime_revision": "x" * 64, "worker_id": "music", "machine_id": "local"}


def test_a_remote_worker_may_report_its_own_machine_id():
    broker._validate_music_worker_job(_item(), _job(), remote=True)
    broker._validate_image_worker_job(_item(), _job(), remote=True)


def test_a_local_worker_machine_id_must_still_match():
    with pytest.raises(ValueError):
        broker._validate_music_worker_job(_item(), _job())
    with pytest.raises(ValueError):
        broker._validate_image_worker_job(_item(), _job())


def test_other_identity_changes_are_still_rejected_remotely():
    job = _job()
    job["runtime_revision"] = "y" * 64
    with pytest.raises(ValueError):
        broker._validate_music_worker_job(_item(), job, remote=True)


class _PeerWorker:
    """A joined node's image worker behind the peer surface: accepts, then polls to an answer.

    It reports ``machine_id: "local"`` - its own name for itself - while the controller routes
    through the node id, exactly as a fleet node answers.
    """

    def __init__(self, polls: list[dict]):
        self.polls = list(polls)

    async def post(self, url, **kwargs):
        import httpx
        return httpx.Response(200, json={"job": {"id": "job-1", "state": "queued",
                                                 "worker_id": "image", "machine_id": "local"}},
                              request=httpx.Request("POST", url))

    async def get(self, url, **kwargs):
        import httpx
        job = dict(self.polls.pop(0) if len(self.polls) > 1 else self.polls[0])
        job.update(id="job-1", worker_id="image", machine_id="local")
        return httpx.Response(200, json={"job": job}, request=httpx.Request("GET", url))


def test_a_peer_job_is_polled_to_its_own_answer_not_failed_on_machine_id(fixture_monitor,
                                                                          monkeypatch):
    """Gateway 2026-10-03: a Z-Image-Turbo 4-bit thumbnail failed on two controllers with
    "Image worker status changed its machine_id". The dispatch poll validated a peer job without
    `remote`, so its first poll always raised; only the recovery poll (which passes it) kept the
    job, and only for RECOVERY_WINDOW_S. A job longer than that - Z-Image on an 8 GB node takes
    ~300 s - was abandoned on node after node. Here the recovery window is zero, so the job's own
    outcome is reached only if the dispatch poll itself accepts the peer's machine id."""
    import asyncio

    monkeypatch.setattr(broker, "POLL_S", 0.0)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0.0)
    submitted = broker.submit_batch({"modality": "image", "model": "model-a", "routing": "pool",
                                     "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = {"id": "image@node-a", "modality": "image", "machine": "node-a",
              "host": "10.0.0.9", "port": 51201, "hub_port": 51201, "hub_token": "fleet-token",
              "local": False, "runtime_identity": {"id": "fleet:node-a", "compatible": True}}
    item.update(state="running", studio=studio["id"], tries=1)
    broker._busy.add(studio["id"])
    worker = _PeerWorker([{"state": "running"}, {"state": "running"},
                          {"state": "error", "error": "the worker's own failure",
                           "error_code": "WORKER_OWN_ERROR"}])

    asyncio.run(broker._run_item(worker, batch, item, studio))

    assert "machine_id" not in str(item.get("error")), item.get("error")
    assert "the worker's own failure" in str(item.get("error")), item.get("error")
    assert not worker.polls[1:], "every status the worker gave must have been read"


def test_a_peer_job_given_up_on_is_cancelled_on_its_node(fixture_monitor, monkeypatch):
    """When the controller gives up on a peer image job (the poll lost its connection and the
    recovery window passed) it regenerates elsewhere. The node's job kept running unpolled,
    burning that Mac and holding its peer lease; the controller now asks the node to cancel it."""
    import asyncio
    import httpx

    monkeypatch.setattr(broker, "POLL_S", 0.0)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0.0)
    submitted = broker.submit_batch({"modality": "image", "model": "model-a", "routing": "pool",
                                     "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = {"id": "image@node-a", "modality": "image", "machine": "node-a",
              "host": "10.0.0.9", "port": 51201, "hub_port": 51201, "hub_token": "fleet-token",
              "local": False, "runtime_identity": {"id": "fleet:node-a", "compatible": True}}
    monkeypatch.setattr(fixture_monitor, "registry", [*fixture_monitor.registry, studio])
    item.update(state="running", studio=studio["id"], tries=1)
    broker._busy.add(studio["id"])

    class LostNode(_PeerWorker):
        deleted: list[str] = []

        async def get(self, url, **kwargs):
            raise httpx.ConnectError("node stopped answering")

        async def delete(self, url, **kwargs):
            self.deleted.append(url)
            return httpx.Response(200, json={}, request=httpx.Request("DELETE", url))

    worker = LostNode([{"state": "running"}])

    async def run():
        await broker._run_item(worker, batch, item, studio)
        await asyncio.gather(*broker._abandoned_peer_cancels)

    asyncio.run(run())

    assert item.get("studio_job_id") is None, "the job was not given up on"
    assert [url.endswith("/api/generate/jobs/job-1") for url in worker.deleted] == [True]


def test_a_peer_jobs_own_deadline_is_published_on_its_item(fixture_monitor, monkeypatch):
    """Owner 2026-10-08: an Image job's limit scales with its steps and the Mac running it. The
    worker states it on its job (`deadline_s`); the Hub puts it on the batch item the Gateway polls,
    so both stop at the worker's own number, and a retry elsewhere does not keep the old one."""
    import asyncio

    monkeypatch.setattr(broker, "POLL_S", 0.0)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0.0)
    submitted = broker.submit_batch({"modality": "image", "model": "model-a", "routing": "pool",
                                     "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = {"id": "image@node-a", "modality": "image", "machine": "node-a",
              "host": "10.0.0.9", "port": 51201, "hub_port": 51201, "hub_token": "fleet-token",
              "local": False, "runtime_identity": {"id": "fleet:node-a", "compatible": True}}
    item.update(state="running", studio=studio["id"], tries=1)
    broker._busy.add(studio["id"])
    seen: list = []

    class Worker(_PeerWorker):
        async def post(self, url, **kwargs):
            import httpx
            return httpx.Response(200, json={"job": {"id": "job-1", "state": "queued",
                                                     "worker_id": "image", "machine_id": "local",
                                                     "deadline_s": 2640.0}},
                                  request=httpx.Request("POST", url))

        async def get(self, url, **kwargs):
            seen.append(broker.public_item(batch, item).get("deadline_s"))
            return await super().get(url, **kwargs)

    worker = Worker([{"state": "running"}, {"state": "error", "error": "stop here",
                                            "error_code": "WORKER_OWN_ERROR"}])
    asyncio.run(broker._run_item(worker, batch, item, studio))

    assert seen and set(seen) == {2640.0}, seen
    assert "deadline_s" in broker._ATTEMPT_EVIDENCE_FIELDS
