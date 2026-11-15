"""The update hold honours the one contract (owner 2026-11-15; update-hold/CONTRACT.md).

`POST/DELETE /api/maintenance/hold` close the claim path first, wait bounded for stranding
in-flight work, and answer exactly `{safe, unsettled, dispatch_paused_s, in_flight}`. The pause
is one in-memory deadline, so a restart with no `DELETE` dispatches again by itself and a hold
with no restart expires on its own after `pause_s`. Unit level only: fixtures and a fake
studio — no real inference, no window, no install.

`components.*` is imported inside the test methods, after `STUDIOHUB_*` is set — the same
order `test_hub_standalone.py` uses, because the Hub reads its packaged data dir at import.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
OWNER = {"Authorization": "Bearer same-existing-hub-token"}


def _studio() -> dict:
    return {"id": "image@local", "title": "Image", "modality": "image",
            "machine": "local", "local": True}


class _Monitor:
    """The dispatchers' view of one always-free local studio (fixtures, no inference)."""

    def __init__(self) -> None:
        self.registry = [_studio()]
        self.status = {"image@local": {"status": "up"}}
        self._client = None
        self.scheduling_catalog_entries = self.cached_catalog_entries

    def cached_catalog_entries(self, model, *, modality=None):
        return [{"studio": row,
                 "entry": {"repo": model, "cache": {"state": "cached"},
                           "runtime_ready": True, "available": True}}
                for row in self.registry
                if modality in (None, row.get("modality"))]


def _batch(batch_id: str, modality: str, items: list[dict]) -> dict:
    return {"id": batch_id, "created_at": 1.0, "cancelled": False, "modality": modality,
            "model": "model", "routing": "pool", "shared_params": {}, "items": items}


def _item(state: str = "queued", *, index: int = 0, job_id: str | None = None) -> dict:
    return {"index": index, "state": state, "retry_at": None, "params": {},
            "prompt": "x", "tries": 1 if state != "queued" else 0,
            "studio": None if state == "queued" else "image@local",
            "studio_job_id": job_id}


async def _catalog(_studio_arg, _model):
    return {"repo": "model", "cache": {"state": "cached"},
            "runtime_ready": True, "available": True}


def run_dispatch_passes(claimed: list) -> None:
    """One real `_dispatch_loop` pass over the seeded batches.

    Only the gates that are NOT under test are patched (catalog, memory admission, upper-
    priority bookkeeping, the ledger write, peers' dispatch bookkeeping). The claim gate itself
    — `busy_machines()` inside `_eligible_studios` and the loop's own re-check right before the
    claim — runs for real; `_run_item` is the claim recorder.
    """
    from components.hub.backend import broker, ledger as ledger_module, peers as peers_module

    async def record(*_args, **_kwargs):
        claimed.append("claimed")

    async def passes():
        with patch.object(broker, "_catalog_entry", new=_catalog), \
                patch.object(broker, "prepare_machine_memory",
                             new=AsyncMock(return_value=("run", None))), \
                patch.object(broker, "admission_refusal", return_value=None), \
                patch.object(broker, "higher_priority_work_waiting",
                             new=AsyncMock(return_value=False)), \
                patch.object(broker, "release_stale_uncertain"), \
                patch.object(broker, "evict_finished_batches"), \
                patch.object(broker, "_run_item", new=record), \
                patch.object(peers_module, "dispatch_ready", return_value=True), \
                patch.object(peers_module, "dispatch_generation", new=lambda _s: 1), \
                patch.object(ledger_module, "save_batch", return_value=None):
            task = asyncio.create_task(broker._dispatch_loop())
            for _ in range(60):
                await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(passes())


class MaintenanceHoldRouteTests(unittest.TestCase):
    """The two contract routes on a real app instance (start_background off, temp data).

    Harness mirrors `StandaloneHubConfigTests` (env, workers file, token) plus the drain
    test's module patches — both are proven shapes in `test_hub_standalone.py`.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / "Node State"
        self.workers_file = self.root / "private-workers.json"
        self.output_roots = [self.root / "image-output", self.root / "voice-output",
                             self.root / "subtitle-output", self.root / "music-output"]
        for root in self.output_roots:
            root.mkdir()
        self.workers_file.write_text(json.dumps([
            {"id": "image", "title": "Image Studio", "modality": "image",
             "host": "127.0.0.1", "port": 51101, "machine": "node-a", "local": True,
             "runtime_identity": {"id": "image-runtime"},
             "hub_token": "same-existing-hub-token",
             "managed_output_roots": [str(self.output_roots[0])]},
            {"id": "voice", "title": "Voice Studio", "modality": "voice",
             "host": "127.0.0.1", "port": 51102, "machine": "node-a", "local": True,
             "runtime_identity": {"id": "voice-runtime"},
             "hub_token": "same-existing-hub-token",
             "managed_output_roots": [str(self.output_roots[1])]},
            {"id": "subtitle", "title": "Subtitle Studio", "modality": "transcription",
             "host": "127.0.0.1", "port": 51103, "machine": "node-a", "local": True,
             "runtime_identity": {"id": "subtitle-runtime"},
             "hub_token": "same-existing-hub-token",
             "managed_output_roots": [str(self.output_roots[2])]},
            {"id": "music", "title": "Music Studio", "modality": "music",
             "host": "127.0.0.1", "port": 51104, "machine": "node-a", "local": True,
             "runtime_identity": {"id": "music-runtime"},
             "hub_token": "same-existing-hub-token",
             "managed_output_roots": [str(self.output_roots[3])]},
        ]))
        os.chmod(self.workers_file, 0o600)
        self.data_dir.mkdir()
        token_file = self.data_dir / ".hub_token"
        token_file.write_text("same-existing-hub-token\n")
        os.chmod(token_file, 0o600)
        self.old_environ = os.environ.copy()
        os.environ.update({
            "STUDIOHUB_DATA_DIR": str(self.data_dir),
            "STUDIOHUB_WORKERS_FILE": str(self.workers_file),
            "STUDIOHUB_PACKAGED": "1",
        })
        self.addCleanup(self._restore)

        from components.hub.backend import auth as auth_module
        from components.hub.backend import broker
        from components.hub.backend import chat_jobs
        from components.hub.backend import ledger as ledger_module
        from components.hub.backend import monitor as monitor_module
        from components.hub.backend import registry as registry_module
        from components.hub.backend import update_drain
        self.broker, self.chat_jobs, self.update_drain = broker, chat_jobs, update_drain

        stack = ExitStack()
        self.addCleanup(stack.close)
        for module, name, value in (
            (registry_module, "DATA_DIR", self.data_dir),
            (registry_module, "REGISTRY_FILE", self.data_dir / "studios.json"),
            (registry_module, "LABELS_FILE", self.data_dir / "machine_labels.json"),
            (registry_module, "FLAGS_FILE", self.data_dir / "machine_flags.json"),
            (monitor_module, "DATA_DIR", self.data_dir),
            (auth_module, "TOKEN_FILE", token_file),
            (auth_module, "PASSWORD_FILE", self.data_dir / ".hub_passwords.json"),
            (auth_module, "SESSIONS_FILE", self.data_dir / ".hub_sessions.json"),
            (ledger_module, "DB_FILE", self.data_dir / "hub.db"),
            (ledger_module, "PINOKIO_HOME", self.data_dir),
        ):
            stack.enter_context(patch.object(module, name, value))
        registry_module._labels_cache = None
        registry_module._flags_cache = None
        # Queue state this file's hold tests own — cleared on both ends.
        broker.batches.clear()
        broker._busy.clear()
        broker._maintenance.clear()
        broker._external_machine_leases.clear()
        broker._machine_reserved_until.clear()
        for name, value in (("_hold_until", 0.0), ("batches", {}), ("_busy", set()),
                            ("_maintenance", set()), ("_external_machine_leases", {}),
                            ("_machine_reserved_until", {})):
            self.addCleanup(setattr, broker, name, value)
        self.addCleanup(setattr, update_drain, "_drain_until", 0.0)
        self.addCleanup(chat_jobs.batches.clear)
        self.addCleanup(chat_jobs.busy_studios.clear)

    def _restore(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_environ)
        self.temporary.cleanup()

    def _open(self, stack: ExitStack):
        from fastapi.testclient import TestClient
        from components.hub.backend import broker
        from components.hub.standalone import create_app
        client = stack.enter_context(TestClient(create_app(start_background=False)))
        # Leave the app's own monitor behind us; this file swapped the broker's.
        self.addCleanup(broker.configure_monitor, client.app.state.monitor)
        broker.configure_monitor(_Monitor())
        return client

    def test_post_hold_closes_every_claim_path_and_reports_in_flight(self) -> None:
        """Check 1: with live work queued, POST holds the queue — no new claim, honest counts."""
        broker, chat_jobs = self.broker, self.chat_jobs
        with ExitStack() as stack:
            client = self._open(stack)
            running = _batch("live", "image", [_item("running", job_id="job-1")])
            queued = _batch("fresh", "image", [_item("queued")])
            broker.batches["live"] = running
            broker.batches["fresh"] = queued
            chat_jobs.batches["chat-live"] = {
                "id": "chat-live", "cancelled": False, "kind": "scene",
                "packs": [{"state": "running"}]}

            # Not public: a caller without this Mac's credential is refused — the contract
            # route sits behind the same middleware as /api/hub/update/drain.
            self.assertEqual(
                client.post("/api/maintenance/hold?timeout_s=0").status_code, 401)

            answer = client.post("/api/maintenance/hold?timeout_s=0&pause_s=60", headers=OWNER)
            self.assertEqual(answer.status_code, 200)
            body = answer.json()
            self.assertEqual(set(body),
                             {"safe", "unsettled", "dispatch_paused_s", "in_flight"},
                             "the reply is exactly the contract's four keys")
            self.assertEqual(body["dispatch_paused_s"], 60)
            self.assertEqual(body["in_flight"], 2,
                             "one running broker item + one running Chat pack, as seeded")
            self.assertTrue(broker.dispatch_paused())
            # The single choke every claim path reads: broker loop, Chat, Subtitle, render.
            self.assertIn("local", broker.busy_machines())

            claimed: list = []
            run_dispatch_passes(claimed)
            self.assertEqual(claimed, [], "held: no new job is claimed")
            self.assertEqual(queued["items"][0]["state"], "queued")
            self.assertEqual(queued["items"][0]["tries"], 0,
                             "a held claim spends nothing")

            # DELETE answers the same four keys and opens the path at once; the very same
            # fixture then claims, which is what proves the hold — not the fixture — held it.
            resumed = client.delete("/api/maintenance/hold", headers=OWNER)
            self.assertEqual(resumed.status_code, 200)
            self.assertEqual(set(resumed.json()),
                             {"safe", "unsettled", "dispatch_paused_s", "in_flight"})
            self.assertEqual(resumed.json()["dispatch_paused_s"], 0)
            self.assertFalse(broker.dispatch_paused())
            run_dispatch_passes(claimed)
            self.assertEqual(len(claimed), 1, "unheld: the same fixture claims exactly once")
            self.assertEqual(queued["items"][0]["state"], "running")

    def test_in_flight_at_the_deadline_keeps_a_resumable_row(self) -> None:
        """Check 2: at the deadline in-flight work either settles or keeps the row/flag a next
        start follows — asserted on the persisted row, not on any log line."""
        from components.hub.backend import ledger as ledger_module
        broker = self.broker
        with ExitStack() as stack:
            client = self._open(stack)

            # (a) A running voice item of a live batch: restore re-dispatches it from this very
            # row, so it is followable — safe with it still running, ids intact in the ledger.
            followable = _batch("voice-run", "voice", [_item("running", job_id="voice-job")])
            broker.batches["voice-run"] = followable
            ledger_module.save_batch(followable)
            body = client.post("/api/maintenance/hold?timeout_s=0&pause_s=60",
                               headers=OWNER).json()
            self.assertTrue(body["safe"], "a running row of a live batch is followable")
            self.assertEqual(body["unsettled"], [])
            self.assertEqual(body["in_flight"], 1)
            rows = {b["id"]: b for b in ledger_module.load_unfinished_batches()}
            saved = rows["voice-run"]["items"][0]
            self.assertEqual(saved["state"], "running", "the row/flag, not a log")
            self.assertEqual(saved["studio_job_id"], "voice-job",
                             "the id the next start follows")
            broker.batches.clear()

            # (b) A cancel in flight: restore would fence it `uncertain` — named unsettled,
            # safe false, and its row still stands (nothing cancelled, nothing deleted).
            cancelling = _batch("voice-cancel", "voice",
                                [_item("cancel_requested", job_id="voice-cancel-job")])
            broker.batches["voice-cancel"] = cancelling
            body = client.post("/api/maintenance/hold?timeout_s=0&pause_s=60",
                               headers=OWNER).json()
            self.assertFalse(body["safe"],
                             "a cancel in flight would strand as unknown-outcome")
            self.assertEqual(body["unsettled"], ["voice-cancel:0"])
            self.assertEqual(broker.batches["voice-cancel"]["items"][0]["studio_job_id"],
                             "voice-cancel-job", "the row keeps its id while unsettled")

    def test_restart_without_delete_dispatches_again(self) -> None:
        """Check 3: the pause is written nowhere, so a restart with no DELETE claims again
        with no manual step — a fresh process (the restart) reads unpaused while we hold."""
        broker = self.broker
        with ExitStack() as stack:
            client = self._open(stack)
            before = sorted(str(p.relative_to(self.data_dir))
                            for p in self.data_dir.rglob("*"))
            answer = client.post("/api/maintenance/hold?timeout_s=0&pause_s=60",
                                 headers=OWNER)
            self.assertEqual(answer.status_code, 200)
            self.assertTrue(broker.dispatch_paused())
            after = sorted(str(p.relative_to(self.data_dir))
                           for p in self.data_dir.rglob("*"))
            self.assertEqual(before, after,
                             "hold writes no latch a restarted process could read")
            result = subprocess.run(
                [sys.executable, "-c",
                 "from components.hub.backend import broker; import sys; "
                 "sys.exit(0 if not broker.dispatch_paused() else 1)"],
                cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True, timeout=120)
            self.assertEqual(
                result.returncode, 0,
                "a fresh process must read the claim path open — nothing persisted: "
                + result.stderr.decode()[-400:])

    def test_hold_resumes_by_itself_after_pause_s(self) -> None:
        """Check 4: hold with no restart and no DELETE — dispatch opens by itself after
        `pause_s` (contract floor: 1 s, so the expiry is measured end-to-end)."""
        broker = self.broker
        with ExitStack() as stack:
            client = self._open(stack)
            body = client.post("/api/maintenance/hold?timeout_s=0&pause_s=1",
                               headers=OWNER).json()
            self.assertEqual(body["dispatch_paused_s"], 1)
            self.assertTrue(broker.dispatch_paused())
            deadline = time.monotonic() + 3.0
            while broker.dispatch_paused() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(
                broker.dispatch_paused(),
                "the pause expires by itself; no DELETE and no restart needed")
            self.assertNotIn("local", broker.busy_machines(),
                             "the claim path is open again after expiry")


if __name__ == "__main__":
    unittest.main()
