"""A Hub job whose worker goes quiet ends KNOWN, with a named reason, wherever it runs.

The owner's question is the one this file answers: *"if we raise it too high for, say subtitle and it
failed, could we find out or we have to wait 2 hours to findout?"* Two mechanisms answer it and they
are deliberately different things:

* `backend/stall.py` — a worker that has gone **silent** while holding an active job. It is
  *visibility only*; nothing is cancelled on its strength.
* `backend/chat_deadline.py` — a Chat job that has written **no new token** for its stall window is
  actually **stopped**, with a sentence that says exactly that and is distinct from the deadline
  stop's.

What was missing is the middle of the first one. `stall.py` is fed by `activity._observed_jobs`,
which merges two sources: a studio's own activity snapshot (whose `activity_received_at` is the real
receipt) and, for a Hub-owned job with no live snapshot, the broker's own item. That second row was
built with `activity_received_at = now` — the *observation* time, not the job's. A worker that stops
reporting therefore produces a row that looks like it reported one instant ago, so
`stall_seconds()` answers `None` for exactly the silence the module exists to surface: the machine
page could never say "quiet for ten minutes" about a Hub-owned job. `tests/test_stall_visibility.py`
pins the field's *name* on every link of the chain but never its *value*, so the hole was invisible.

The tests here are behavioural, in a child process against a throwaway data dir and a mock worker
(no live Gateway, no real model library, no network):

1. the machine/activity row for a silent worker is flagged, and one for a reporting worker is not;
2. a job that has not started, or has ended, is never flagged;
3. the two producers of the flag (`activity` and `broker.batch_summary`) agree about one job;
4. a dispatched Chat **pack** that stops writing is stopped, and the reason is readable afterwards;
5. the **proxied** Chat completion that stops writing is stopped the same way, and its lease is
   released; the same route's own deadline stop is a different, named stop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]


def _runtime() -> str:
    for candidate in (os.environ.get("STUDIOHUB_TEST_RUNTIME"),
                      str(WORKTREE / ".build/voice-runtime/bin/python3"),
                      str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3")):
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


# Every case runs in a child interpreter with a throwaway data directory, a throwaway worker
# manifest and a mock HTTP transport. `STUDIOHUB_PACKAGED=1` is the real packaged contract, so the
# worker manifest is the one the Hub actually reads; it is written owner-only, as the real one is.
_FIXTURE = r'''
import asyncio
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# `-I` isolates the child from the current directory and from PYTHONPATH on purpose; the checkout
# is named explicitly instead, so a stray `json.py` beside the proof cannot be imported.
sys.path.insert(0, os.environ["STUDIOHUB_PROOF_ROOT"])

import httpx

_DATA = Path(tempfile.mkdtemp(prefix="studiohub-stall-stop-"))
_TOKEN = "stall-stop-fixture-token"
_WORKERS = _DATA / "workers.json"
_WORKERS.write_text(json.dumps([
    {"id": "chat", "modality": "chat", "host": "127.0.0.1", "port": 51091,
     "machine": "local", "local": True, "hub_token": _TOKEN,
     "runtime_identity": {"id": "chat-runtime", "compatible": True}},
    {"id": "image", "modality": "image", "host": "127.0.0.1", "port": 51092,
     "machine": "local", "local": True, "hub_token": _TOKEN,
     "runtime_identity": {"id": "image-runtime", "compatible": True}},
]))
_WORKERS.chmod(0o600)
(_DATA / ".hub_token").write_text(_TOKEN + "\n")
(_DATA / ".hub_token").chmod(0o600)
os.environ.update({
    "STUDIOHUB_DATA_DIR": str(_DATA),
    "STUDIOHUB_WORKERS_FILE": str(_WORKERS),
    "STUDIOHUB_PACKAGED": "1",
    "STUDIOHUB_REQUIRE_AUTH": "1",
    # The stall windows are minutes in production; a proof pins them to a fraction of a second so
    # the real code path (not a shortened copy of it) is what runs.
    "STUDIOHUB_CHAT_STALL_SECONDS": "0.4",
})
# This Hub owns no model library, and the packaged model gate only runs where one is configured.
# An inherited value sends the proxy down a readiness path whose catalog this fixture's stub
# monitor does not carry, so the proof would fail on the release gate's own environment (which
# binds an empty models root) and pass on a bare one. Same reason `test_chat_hub_wiring`'s
# fixtures say so themselves.
os.environ.pop("STUDIOHUB_MODELS_DIR", None)

from components.hub.backend import activity, broker, chat_jobs, ledger, peers  # noqa: E402

ledger.prepare_database()
FACTS = {}
'''

# Case 1-3: the stall the machine page can see.
_VISIBILITY = r'''
# ── The machine/activity row a worker's silence is read from ─────────────────────────────────
NOW = 100_000.0
REGISTRY = [
    {"id": "image@node-a", "modality": "image", "machine": "node-a"},
    {"id": "image@node-b", "modality": "image", "machine": "node-b"},
    {"id": "image@node-c", "modality": "image", "machine": "node-c"},
]


def _statuses(**by_studio):
    return by_studio


def _snapshot(studio, *, job_id, state, observed_at, received_at):
    return {
        "status": "up", "activity_support": "available", "activity_received_at": received_at,
        "activity": {"schema": "kh-studio.activity.v1", "studio": studio,
                     "observed_at": observed_at,
                     "active": {"id": job_id, "state": state, "model": "m", "source": "job",
                                "started_at": observed_at - 100} if state == "running" else None,
                     "latest": None},
    }


def _batch(machine, *, state, studio_job_id, started_at, last_progress_at, index=0):
    return {
        "id": f"batch-{machine}-{index}", "modality": "image", "model": "m", "cancelled": False,
        "created_at": started_at,
        "items": [{"index": index, "studio": f"image@{machine}", "studio_job_id": studio_job_id,
                   "state": state, "tries": 1, "progress": 0.4, "run_started": started_at,
                   "started_at": started_at, "last_progress_at": last_progress_at}],
    }


# node-a: the worker answered ten minutes ago and has said nothing since. Its snapshot is no longer
# active-fresh, so the only row left is the broker's own item - the case `stall.py` is for.
SILENT = {"batch-node-a-0": _batch("node-a", state="running", studio_job_id="job-a",
                                  started_at=NOW - 700, last_progress_at=NOW - 600)}
STATUSES = {"image@node-a": _snapshot("image@node-a", job_id="job-a", state="running",
                                      observed_at=NOW - 600, received_at=NOW - 600),
            # node-b: the worker is reporting right now.
            "image@node-b": _snapshot("image@node-b", job_id="job-b", state="running",
                                      observed_at=NOW - 1, received_at=NOW - 1)}
FRESH = {"batch-node-b-0": _batch("node-b", state="running", studio_job_id="job-b",
                                 started_at=NOW - 60, last_progress_at=NOW - 1)}
# node-c: nothing is running - the job is still queued, so silence means nothing yet.
IDLE = {"batch-node-c-0": _batch("node-c", state="queued", studio_job_id="job-c",
                                started_at=NOW - 700, last_progress_at=NOW - 700)}

rows = {}
for machine, batches in (("node-a", SILENT), ("node-b", FRESH), ("node-c", IDLE)):
    observed = activity._observed_jobs(REGISTRY, STATUSES, batches, NOW)
    rows[machine] = next(row for row in observed if row["studio"] == f"image@{machine}")
FACTS["silent_stalled_seconds"] = rows["node-a"]["stalled_seconds"]
FACTS["silent_receipt"] = rows["node-a"]["activity_received_at"]
FACTS["reporting_stalled_seconds"] = rows["node-b"]["stalled_seconds"]
FACTS["queued_stalled_seconds"] = rows["node-c"]["stalled_seconds"]
# The other producer of the same number, for the same job: the Jobs page reads this one. It is
# built on the wall clock because `batch_summary` reads `time.time()` itself.
_WALL = time.time()
_SUMMARY_BATCH = _batch("node-a", state="running", studio_job_id="job-a",
                        started_at=_WALL - 700, last_progress_at=_WALL - 600)
_SUMMARY_BATCH["created_at"] = _WALL - 700
FACTS["summary_stalled_seconds"] = broker.batch_summary(_SUMMARY_BATCH)["stalled_seconds"]
FACTS["stall_after_s"] = __import__(
    "components.hub.backend.stall", fromlist=["x"]).STALL_AFTER_S

# A node that is entirely offline while it holds a job (S5b). Its own worker is gone and there is
# no snapshot at all, so the only evidence left is the broker's item - and the fleet view must not
# read as a healthy working Mac with nothing to say about it.
_DOWN = {"image@node-a": {"status": "down"}}
_offline_live = activity._observed_jobs([REGISTRY[0]], _DOWN, SILENT, NOW)
FACTS["offline_node_state"] = activity._state_at(
    "node-a", _DOWN, [REGISTRY[0]], _offline_live, [], NOW)[0]
FACTS["offline_node_stalled_seconds"] = _offline_live[0]["stalled_seconds"]
FACTS["offline_node_current_job"] = activity._current_job(_offline_live[0])
'''

# Case 4: the Chat pack lane's stall stop actually acts.
_PACK = r'''
async def _silent_worker(request):
    await asyncio.sleep(30)
    return httpx.Response(200, json={"choices": [{"message": {"content": "too late"}}]})


async def _writing_worker(request):
    await asyncio.sleep(0.05)
    return httpx.Response(200, json={"choices": [{"message": {"content": '{"scene-1": "ok"}'}}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 4,
                                               "total_tokens": 5}})


class _Monitor:
    """The Hub's polled view, in the shape `progress_signature` reads it."""

    def __init__(self, client, written):
        self._client = client
        self.written = written
        self.status = {"chat": {"status": "up", "activity_support": "available",
                                "activity_received_at": time.time(), "activity": {
                                    "schema": "kh-studio.activity.v1", "studio": "chat",
                                    "observed_at": time.time(),
                                    "active": {"id": "pack-job", "state": "running",
                                               "progress_stage": "generating",
                                               "generation_tokens": written},
                                    "latest": None}}}


async def _run_pack(handler, *, written):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monitor = _Monitor(client, written)
    studio = {"id": "chat", "modality": "chat", "machine": "local", "local": True,
              "host": "127.0.0.1", "port": 51091, "hub_token": _TOKEN,
              "base_url": "http://127.0.0.1:51091"}
    pack = {"index": 0, "pack_id": "pack-1", "scene_ids": ["scene-1"],
            "messages": [{"role": "user", "content": "hi"}], "params": {},
            "state": "running", "tries": 1, "studio": "chat", "duration_seconds": None,
            "error": None, "results": {}, "usage": None, "model_revision": None,
            "raw_responses": [], "started_at": None, "finished_at": None, "retry_at": None,
            "deadline_s": None, "deadline_note": None}
    batch = {"id": "pack-batch", "model": "m", "kind": "completion", "cancelled": False,
             "created_at": time.time(), "idempotency_key": "k", "packs": [pack]}
    try:
        await chat_jobs._run_pack(monitor, batch, pack, studio, "chat:owner")
    finally:
        chat_jobs.busy_studios.discard("chat")
        await client.aclose()
    return pack, chat_jobs.summary(batch)["packs"][0]


silent_pack, silent_summary = asyncio.run(_run_pack(_silent_worker, written=0))
FACTS["silent_pack_state"] = silent_pack["state"]
FACTS["silent_pack_error"] = silent_pack["error"]
FACTS["silent_pack_summary_error"] = silent_summary["error"]
FACTS["silent_pack_deadline_s"] = silent_pack["deadline_s"]
FACTS["silent_pack_finished_at"] = silent_pack["finished_at"]

# A pack the worker is still writing is left alone: every new token restarts the window.
writing_pack, _ = asyncio.run(_run_pack(_writing_worker, written=0))
FACTS["writing_pack_state"] = writing_pack["state"]
FACTS["writing_pack_error"] = writing_pack["error"]
'''

# Case 5: the proxied Chat completion - the same stop, on the route the Gateway actually calls.
_PROXY = r'''
async def _catalog(studio):
    return {"models": [{"repo": "mlx-community/Qwen3.5-9B-4bit", "cache": {"state": "cached"}}]}


# A real loopback worker, not a MockTransport: the deadline stop is the lane's own httpx read
# timeout, and `MockTransport` answers below that layer, so a mocked transport would make this
# proof pass while proving nothing. The port is ephemeral and the address is loopback-only.
QUIET = {"on": True}
POSTS = []


class _Worker(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        POSTS.append(self.path)
        if QUIET["on"]:
            time.sleep(30)
        body = json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # the fixture must not print a request log
        return


_SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Worker)
_PORT = _SERVER.server_address[1]
threading.Thread(target=_SERVER.serve_forever, daemon=True).start()

_NOW = time.time()
WORKERS = [{"id": "chat", "title": "Chat", "modality": "chat", "machine": "local",
            "local": True, "host": "127.0.0.1", "port": _PORT, "hub_token": _TOKEN,
            "base_url": f"http://127.0.0.1:{_PORT}",
            "runtime_identity": {"id": "chat-runtime", "compatible": True}}]
monitor = SimpleNamespace(
    registry=WORKERS,
    status={"chat": {"status": "up", "activity_support": "available",
                     "activity_received_at": _NOW, "activity": {
                         "schema": "kh-studio.activity.v1", "studio": "chat",
                         "observed_at": _NOW,
                         "active": {"id": "page-job", "state": "running",
                                    "progress_stage": "generating", "generation_tokens": 0},
                         "latest": None}}})
monitor.scheduling_catalog = _catalog
broker.configure_monitor(monitor)

from components.hub.backend import monitor as monitor_module  # noqa: E402
with patch.object(monitor_module, "StudioMonitor", return_value=monitor):
    from components.hub import standalone  # noqa: E402

BODY = {"model": "mlx-community/Qwen3.5-9B-4bit",
        "messages": [{"role": "user", "content": "hi"}], "stream": False}
HEADERS = {"X-Hub-Token": peers.fleet_token()}


async def _post(client, **kwargs):
    return await client.post("/studio/chat/v1/chat/completions", headers=HEADERS,
                             json=BODY, **kwargs)


async def _fresh_client(client):
    """A new worker client per step: a cancelled request leaves its pooled connection unusable."""
    await client.aclose()
    monitor._client = httpx.AsyncClient()
    return monitor._client


async def _proof():
    monitor._client = httpx.AsyncClient()
    with patch.object(standalone, "StudioMonitor", return_value=monitor), \
         patch.object(standalone.output_storage, "admission", return_value=None):
        hub = standalone.create_app(start_background=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hub),
                                     base_url="http://stall-stop-fixture") as client:
            # 1. The worker goes quiet mid-job: it accepted the POST and then wrote nothing.
            stalled = await _post(client)
            FACTS["stall_status"] = stalled.status_code
            FACTS["stall_body"] = stalled.json()
            FACTS["stall_leases"] = sorted(broker.busy_machines())
            recorded = ledger.load_finished_batches(limit=1)[0]["items"][0]
            FACTS["stall_recorded"] = {key: recorded.get(key) for key in
                                       ("state", "error", "error_code", "machine", "worker_id")}
            FACTS["stall_deadline_s"] = recorded.get("deadline_s")
            FACTS["stall_posts"] = len(POSTS)

            # 2. The same route, same worker, answering normally: nothing is stopped.
            QUIET["on"] = False
            POSTS.clear()
            monitor._client = await _fresh_client(monitor._client)
            healthy = await _post(client)
            FACTS["healthy_status"] = healthy.status_code
            FACTS["healthy_body"] = healthy.json()
            FACTS["healthy_leases"] = sorted(broker.busy_machines())
            FACTS["healthy_recorded"] = ledger.load_finished_batches(limit=1)[0]["items"][0]["state"]

            # 3. The lane's other stop: the job's own allowance runs out. The arithmetic is
            #    patched to a fraction of a second - the code path and the read timeout are the
            #    real ones - and the stall window is put out of reach so only the deadline can fire.
            QUIET["on"] = True
            POSTS.clear()
            os.environ["STUDIOHUB_CHAT_STALL_SECONDS"] = "60"
            monitor._client = await _fresh_client(monitor._client)
            with patch.object(standalone, "deadline_floor_seconds", lambda modality: 0.4), \
                 patch.object(chat_deadline, "STATED_LOAD_S", 0.0), \
                 patch.object(chat_deadline, "DEFAULT_EXPECTED_OUTPUT_TOKENS", 1):
                expired = await _post(client)
            FACTS["deadline_status"] = expired.status_code
            FACTS["deadline_body"] = expired.json()
            FACTS["deadline_recorded"] = {
                key: ledger.load_finished_batches(limit=1)[0]["items"][0].get(key)
                for key in ("state", "error", "error_code")}
            FACTS["deadline_leases"] = sorted(broker.busy_machines())
            FACTS["deadline_posts"] = len(POSTS)
    await monitor._client.aclose()


from components.hub.backend import chat_deadline  # noqa: E402

asyncio.run(_proof())
'''

# S2's own invariant, on the path a real dispatch takes: a completion that names no output is not a
# success. Image, Music and Video already refuse it (`_validate_*_worker_job`); this pins that.
_NO_OUTPUT = r'''
class _Node:
    """A worker that accepts a job and then reports `done` with nothing to show for it."""

    def __init__(self):
        self.posted = []

    async def post(self, url, **kwargs):
        self.posted.append(url)
        return httpx.Response(200, json={"job": {"id": "job-1", "state": "queued",
                                                 "worker_id": "image", "machine_id": "local"}})

    async def get(self, url, **kwargs):
        return httpx.Response(200, json={"job": {"id": "job-1", "state": "done",
                                                 "worker_id": "image", "machine_id": "local",
                                                 "runtime_s": 3.0}})


broker.batches.clear()
studio = {"id": "image", "modality": "image", "machine": "local", "local": True,
          "host": "127.0.0.1", "port": 51092, "hub_token": _TOKEN,
          "runtime_identity": {"id": "image-runtime", "compatible": True}}
item = {"index": 0, "prompt": "p", "params": {}, "seed": 1, "state": "running", "tries": 1,
        "studio": "image", "studio_job_id": None, "retry_at": None, "error": None}
batch = {"id": "image-batch", "modality": "image", "model": "m", "shared_params": {},
         "cancelled": False, "created_at": time.time(), "items": [item]}
broker.batches["image-batch"] = batch

with patch.object(broker, "POLL_S", 0.0), \
     patch.object(broker, "RECOVERY_WINDOW_S", 0.0), \
     patch.object(broker, "RETRY_DELAYS_S", (0.0,)), \
     patch.object(broker, "_post_item_webhook", _noop), \
     patch.object(broker, "_maybe_finish", _noop):
    asyncio.run(broker._run_item(_Node(), batch, item, studio))

FACTS["no_output_state"] = item["state"]
FACTS["no_output_is_done"] = item["state"] == "done"
FACTS["no_output_terminal_result"] = broker.terminal_result(batch, item)
FACTS["no_output_error"] = item.get("error")
'''

def _child(*scripts: str, extra: str = "") -> dict:
    """Run one child interpreter with the fixture and the given case sources, return its facts."""
    source = "\n".join([_FIXTURE, extra, *scripts, "print('FACTS=' + json.dumps(FACTS, default=str))"])
    with tempfile.TemporaryDirectory(prefix="studiohub-stall-stop-proof-") as folder:
        path = Path(folder) / "proof.py"
        path.write_text(source, encoding="utf-8")
        env = os.environ.copy()
        env["STUDIOHUB_PROOF_ROOT"] = str(WORKTREE)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run([_runtime(), "-I", str(path)], cwd=str(WORKTREE), env=env,
                                capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise AssertionError("the fixture proof failed:\n" + result.stdout + result.stderr)
    line = next(line for line in reversed(result.stdout.splitlines()) if line.startswith("FACTS="))
    return json.loads(line[len("FACTS="):])


class AQuietWorkerIsVisibleOnTheMachinesRowTests(unittest.TestCase):
    """`stall.py`'s flag reaches the machine page for a job the Hub itself owns."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.facts = _child(_VISIBILITY)

    def test_a_worker_that_stopped_reporting_is_flagged(self) -> None:
        # The row's receipt must be the job's own last sign of life, not the moment the Hub looked.
        self.assertEqual(self.facts["silent_receipt"], 99_400.0,
                         "the broker's own row was stamped with the observation time, so a job "
                         "whose worker has been silent for ten minutes reads as reporting now")
        self.assertIsNotNone(self.facts["silent_stalled_seconds"],
                             "a worker silent for ten minutes must be flagged as stalled")
        self.assertGreaterEqual(self.facts["silent_stalled_seconds"],
                                self.facts["stall_after_s"])

    def test_a_reporting_worker_is_not_flagged(self) -> None:
        self.assertIsNone(self.facts["reporting_stalled_seconds"],
                          "a healthy worker must never be flagged between two snapshots")

    def test_a_job_that_has_not_started_is_not_flagged(self) -> None:
        self.assertIsNone(self.facts["queued_stalled_seconds"],
                          "nothing is running yet, so silence is the expected state")

    def test_the_two_producers_of_the_flag_agree(self) -> None:
        """The Jobs page (`broker.batch_summary`) and the machine page (`activity`) read the same
        job's own clock, so one job cannot be stalled on one page and healthy on the other."""
        self.assertGreaterEqual(self.facts["summary_stalled_seconds"], 600.0,
                                "the batch summary no longer reports this job's silence")
        self.assertIsNotNone(self.facts["silent_stalled_seconds"],
                             "the machine page reports the same job as healthy")


class AMacThatGoesOfflineMidJobTests(unittest.TestCase):
    """A node that stops reporting while holding a job is not a healthy working Mac (S5b).

    The job itself already ends known: the controller's poll loses the node, the bounded recovery
    window runs out, the attempt is abandoned and the item is re-queued for another Mac
    (`tests/hub_regression/cases/test_free_work_reroute.py`, "a node that dies mid job is rerun in
    every modality"). What no test pinned is the *fleet health* half of the same event: the machine
    row the operator looks at must not read as a healthy working Mac with nothing to say.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.facts = _child(_VISIBILITY)

    def test_the_silence_is_on_the_row(self) -> None:
        self.assertIsNotNone(self.facts["offline_node_stalled_seconds"],
                             "an offline Mac holding a job reported no silence at all")

    def test_the_row_still_names_the_job_it_holds(self) -> None:
        """`working` is not a lie here - the Hub is still holding the job - so the fix is not to
        change the state but to make the silence beside it visible."""
        self.assertEqual(self.facts["offline_node_state"], "working")
        self.assertEqual(self.facts["offline_node_current_job"]["job_id"], "job-a")


class AChatPackThatGoesQuietIsStoppedTests(unittest.TestCase):
    """The dispatched pack lane: the stall stop acts, and its reason is readable afterwards."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.facts = _child(_PACK)

    def test_the_pack_ends_with_the_stall_reason(self) -> None:
        self.assertEqual(self.facts["silent_pack_state"], "error")
        self.assertEqual(self.facts["silent_pack_error"], "stopped: no new text for 1 second")
        self.assertNotIn("limit", self.facts["silent_pack_error"],
                         "a stall and a deadline are different events and must not share words")
        self.assertIsNotNone(self.facts["silent_pack_finished_at"], "the pack is terminal, not retrying")

    def test_the_reason_survives_into_the_job_the_operator_reads(self) -> None:
        self.assertEqual(self.facts["silent_pack_summary_error"], self.facts["silent_pack_error"])

    def test_the_job_still_states_the_limit_it_was_given(self) -> None:
        self.assertGreaterEqual(self.facts["silent_pack_deadline_s"], 1800.0)

    def test_a_pack_that_is_still_writing_is_not_stopped(self) -> None:
        self.assertEqual(self.facts["writing_pack_state"], "done",
                         self.facts.get("writing_pack_error"))


class TheProxiedChatStopTests(unittest.TestCase):
    """The route the Gateway calls: the same stop, named, and the Mac released."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.facts = _child(_PROXY)

    def test_the_caller_is_told_the_job_was_stopped_for_its_silence(self) -> None:
        self.assertEqual(self.facts["stall_status"], 504,
                         "a job that ran and was stopped is not a refusal (409) and not an "
                         "unreachable studio (503)")
        self.assertEqual(self.facts["stall_body"]["code"], "CHAT_STALLED")
        self.assertEqual(self.facts["stall_body"]["detail"], "stopped: no new text for 1 second")

    def test_the_stop_is_readable_afterwards_in_the_job_history(self) -> None:
        recorded = self.facts["stall_recorded"]
        self.assertEqual(recorded["state"], "error")
        self.assertEqual(recorded["error_code"], "CHAT_STALLED")
        self.assertEqual(recorded["error"], self.facts["stall_body"]["detail"])
        self.assertEqual(recorded["machine"], "local")
        self.assertIsNotNone(self.facts["stall_deadline_s"])

    def test_the_mac_is_released_and_nothing_was_submitted_twice(self) -> None:
        self.assertEqual(self.facts["stall_leases"], [],
                         "a stopped job must not hold the Mac it ran on")
        self.assertEqual(self.facts["stall_posts"], 1, "one job is one POST")

    def test_a_healthy_worker_is_never_stopped(self) -> None:
        self.assertEqual(self.facts["healthy_status"], 200)
        self.assertEqual(self.facts["healthy_recorded"], "done")
        self.assertEqual(self.facts["healthy_leases"], [])

    def test_the_deadline_stop_is_a_different_named_stop(self) -> None:
        self.assertEqual(self.facts["deadline_status"], 504)
        self.assertEqual(self.facts["deadline_body"]["code"], "CHAT_DEADLINE_REACHED")
        self.assertIn("no answer within", self.facts["deadline_body"]["detail"])
        self.assertNotEqual(self.facts["deadline_body"]["detail"], self.facts["stall_body"]["detail"])
        self.assertEqual(self.facts["deadline_recorded"]["error_code"], "CHAT_DEADLINE_REACHED")
        self.assertEqual(self.facts["deadline_leases"], [])


class ACompletionWithNoOutputIsNotASuccessTests(unittest.TestCase):
    """The Hub never reports success without a real output (S2's first invariant).

    Image, Music and Video each refuse a `done` that names no artifact
    (`_validate_image_worker_job` and its two siblings); Voice refuses an unusable file through
    `_cache_voice_artifact_metadata`. This drives the real dispatch path to pin the invariant,
    because a `done` with no artifact would be a success the caller can never fetch.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.facts = _child(_NO_OUTPUT, extra="async def _noop(*a, **k):\n    return None\n")

    def test_it_is_not_filed_as_done(self) -> None:
        # The worker answered with a receipt that carries no artifact, which is not this job's
        # result: the attempt is fenced `uncertain` (the sweep releases it), never `done`.
        self.assertFalse(self.facts["no_output_is_done"],
                         f"a completion with no output was filed as done: {self.facts}")
        self.assertEqual(self.facts["no_output_state"], "uncertain",
                         self.facts.get("no_output_error"))

    def test_no_success_envelope_is_published(self) -> None:
        self.assertIsNone(self.facts["no_output_terminal_result"],
                          "terminal_result() claims `succeeded` and an artifact URL for a job that "
                          "produced no artifact")


if __name__ == "__main__":
    unittest.main()
