#!/usr/bin/env python3
"""Manifest-driven Chat sessions, using metadata-only fake engines."""
from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
import tempfile
from unittest import mock

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.chat.app.backend.llm_engine import ChatModelUnavailable
from components.chat.app.backend.model_manager import ChatModelManager, ManifestCatalogError
from components.chat.app.backend import main as chat_main


class _PreservedTemp(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="chat-model-manager-"))

    def tearDown(self) -> None:
        if self.root.exists():
            destination = ROOT / "_trash" / "tests" / f"chat-model-manager-{uuid.uuid4().hex}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.root), destination)


def _manifest(package: str, model: str, revision: str, *, default: bool = False,
              allowed: bool = True) -> dict:
    return {
        "schema_version": 1,
        "package_id": package,
        "model_id": model,
        "revision": revision,
        "engine": "chat-mlx",
        "minimum_macos": "14.0",
        "files": [{"path": "model/config.json", "size": 2, "sha256": "f" * 64,
                   "urls": []}],
        "qualification": {"status": "qualified" if allowed else "unqualified"},
        "default": default,
        "production_allowed": allowed,
    }


class _FakeEngine:
    def __init__(self, *, model_id: str, revision: str, manifest: dict, events: list,
                 **_kwargs) -> None:
        self.model_id = model_id
        self.revision = revision
        self.manifest = manifest
        self.label = manifest.get("label") or model_id
        self.family = manifest.get("family")
        self.minimum_unified_memory_gb = None
        self.context_window_tokens = manifest.get("context_window_tokens")
        self.max_output_tokens = manifest.get("max_output_tokens", 4096)
        self._loaded = False
        self.events = events
        self.release_calls = 0
        self.complete_calls = 0
        self.stream_closed = 0

    def loaded(self) -> bool:
        return self._loaded

    def preflight(self) -> dict:
        return {"state": "files_verified"}

    def status(self) -> dict:
        return {"loaded": self._loaded, "busy": False, "active_memory_bytes": None,
                "peak_memory_bytes": None}

    def model_limits(self) -> dict:
        return {}

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def prompt_length(self, _messages) -> int:
        return 3

    def release(self, **_kwargs) -> dict:
        self.release_calls += 1
        self.events.append(f"release:{self.model_id}")
        was_loaded, self._loaded = self._loaded, False
        return {"released": was_loaded}

    def check_prompt_fits(self, _messages, **_kwargs) -> int:
        self._loaded = True
        self.events.append(f"check:{self.model_id}")
        return 3

    def complete(self, _messages, *, on_progress=None, **_kwargs) -> dict:
        self.complete_calls += 1
        self._loaded = True
        self.events.append(f"complete:{self.model_id}")
        if on_progress:
            on_progress({"phase": "processing", "attempt": 1,
                         "processed_prompt_tokens": 3, "total_prompt_tokens": 3})
            on_progress({"phase": "completed", "attempt": 1,
                         "telemetry": {"attempts": [{"attempt": 1,
                                                       "generation_tokens": 2,
                                                       "generation_tps": 8.0}],
                                       "final_attempt": 1}})
        return {"text": self.model_id, "elapsed_seconds": 0.25, "prompt_tokens": 3,
                "telemetry": {"attempts": [{"attempt": 1, "generation_tokens": 2,
                                              "generation_tps": 8.0}],
                              "final_attempt": 1}}

    def stream(self, _messages, *, on_progress=None, **_kwargs):
        self._loaded = True
        self.events.append(f"stream:{self.model_id}")
        try:
            if on_progress:
                on_progress({"phase": "generating", "attempt": 1,
                             "generation_tokens": 1, "generation_tps": 4.0})
            yield "piece"
            return 0.25
        finally:
            self.stream_closed += 1


class ChatModelManagerTests(_PreservedTemp):
    def _manager(self, manifests: list[dict], events: list[str] | None = None):
        catalog = self.root / f"catalog-{uuid.uuid4().hex}"
        catalog.mkdir()
        for manifest in manifests:
            (catalog / f"{manifest['package_id']}.json").write_text(json.dumps(manifest))
        events = events if events is not None else []
        return ChatModelManager.from_catalog(
            catalog, self.root / "models",
            engine_factory=lambda **kwargs: _FakeEngine(events=events, **kwargs),
            collect=lambda: events.append("collect"),
            clear_cache=lambda: events.append("clear_cache"),
        )

    def test_catalog_requires_one_default_and_unique_model_ids(self) -> None:
        a = _manifest("a", "org/same", "a" * 40, default=True)
        b = _manifest("b", "org/same", "b" * 40)
        with self.assertRaisesRegex(ManifestCatalogError, "duplicate model_id"):
            self._manager([a, b])
        b["model_id"] = "org/b"
        b["default"] = True
        with self.assertRaisesRegex(ManifestCatalogError, "one default"):
            self._manager([a, b])

    def test_catalog_falls_back_only_to_one_production_model(self) -> None:
        eligible = _manifest("base", "org/base", "a" * 40)
        pending = _manifest("pending", "org/pending", "b" * 40, allowed=False)
        eligible.pop("default")
        pending.pop("default")
        manager = self._manager([eligible, pending])
        self.assertEqual(manager.default.model_id, "org/base")

        second = _manifest("second", "org/second", "c" * 40)
        second.pop("default")
        with self.assertRaisesRegex(ManifestCatalogError, "default"):
            self._manager([eligible, second])
        eligible["production_allowed"] = False
        eligible["qualification"]["status"] = "unqualified"
        with self.assertRaisesRegex(ManifestCatalogError, "default"):
            self._manager([eligible, pending])

    def test_explicit_default_must_be_production_eligible(self) -> None:
        with self.assertRaisesRegex(ManifestCatalogError, "production"):
            self._manager([
                _manifest("base", "org/base", "a" * 40),
                _manifest("pending", "org/pending", "b" * 40,
                          default=True, allowed=False),
            ])

    def test_switch_unloads_collects_and_clears_before_the_next_model_runs(self) -> None:
        events: list[str] = []
        manager = self._manager([
            _manifest("a", "org/a", "a" * 40, default=True),
            _manifest("b", "org/b", "b" * 40),
        ], events)
        manager.for_model("org/a").complete([])
        manager.for_model("org/b").complete([])
        self.assertEqual(events, [
            "complete:org/a", "release:org/a", "collect", "clear_cache", "complete:org/b"
        ])
        self.assertFalse(manager.for_model("org/a").loaded())
        self.assertTrue(manager.for_model("org/b").loaded())

    def test_pending_model_refuses_before_engine_load(self) -> None:
        manager = self._manager([
            _manifest("base", "org/base", "a" * 40, default=True),
            _manifest("pending", "org/pending", "b" * 40, allowed=False),
        ])
        pending = manager.for_model("org/pending")
        with self.assertRaisesRegex(ChatModelUnavailable, "not qualified"):
            pending.complete([])
        self.assertEqual(pending._engine.complete_calls, 0)
        self.assertFalse(pending.loaded())

    def test_progress_is_bound_to_the_exact_model_and_stream_close_releases_once(self) -> None:
        manager = self._manager([_manifest("a", "org/a", "a" * 40, default=True)])
        progress: list[dict] = []
        outcome = manager.default.complete([], on_progress=progress.append)
        self.assertEqual(outcome["telemetry"]["final_attempt"], 1)
        self.assertTrue(progress)
        self.assertTrue(all(event["model"] == "org/a" for event in progress))
        stream = manager.default.stream([], on_progress=progress.append)
        self.assertEqual(next(stream), "piece")
        stream.close()
        self.assertEqual(manager.default._engine.stream_closed, 1)
        # The same plain lock is free after cancellation; another call cannot wedge.
        self.assertEqual(manager.default.complete([])["text"], "org/a")

    def test_failed_load_releases_partial_state_once_and_frees_the_global_lock(self) -> None:
        manager = self._manager([
            _manifest("a", "org/a", "a" * 40, default=True),
            _manifest("b", "org/b", "b" * 40),
        ])
        broken = manager.for_model("org/a")

        def fail_before_load(_messages, **_kwargs):
            raise RuntimeError("load failed")

        broken._engine.complete = fail_before_load
        with self.assertRaisesRegex(RuntimeError, "load failed"):
            broken.complete([])
        self.assertEqual(broken._engine.release_calls, 1)
        self.assertEqual(manager.for_model("org/b").complete([])["text"], "org/b")

    def test_waiting_sessions_do_not_run_two_engines_together(self) -> None:
        manager = self._manager([
            _manifest("a", "org/a", "a" * 40, default=True),
            _manifest("b", "org/b", "b" * 40),
        ])
        first = manager.for_model("org/a").stream([])
        self.assertEqual(next(first), "piece")
        answered: list[str] = []
        worker = threading.Thread(
            target=lambda: answered.append(manager.for_model("org/b").complete([])["text"]),
            daemon=True,
        )
        worker.start()
        time.sleep(0.05)
        self.assertTrue(worker.is_alive())
        first.close()
        worker.join(timeout=2)
        self.assertEqual(answered, ["org/b"])

    def test_queued_preflight_stays_waiting_until_its_cold_session_starts(self) -> None:
        manager = self._manager([
            _manifest("a", "org/a", "a" * 40, default=True),
            _manifest("b", "org/b", "b" * 40),
        ])
        first = manager.for_model("org/a").stream([])
        self.assertEqual(next(first), "piece")
        events: list[dict] = []
        worker = threading.Thread(
            target=lambda: manager.for_model("org/b").check_prompt_fits(
                [], on_progress=events.append), daemon=True)
        worker.start()
        time.sleep(0.05)
        self.assertEqual([event["phase"] for event in events], ["waiting"])
        first.close()
        worker.join(timeout=2)
        self.assertEqual([event["phase"] for event in events], ["waiting", "loading"])

    def test_proxy_captures_usage_before_another_model_switches(self) -> None:
        manager = self._manager([
            _manifest("a", "org/a", "a" * 40, default=True),
            _manifest("b", "org/b", "b" * 40),
        ])
        outcome = manager.for_model("org/a").complete([])
        manager.for_model("org/b").complete([])
        self.assertEqual(outcome["usage"], {
            "prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4,
        })

    def test_api_uses_request_usage_after_a_synchronized_model_switch(self) -> None:
        manager = self._manager([
            _manifest("a", "org/a", "a" * 40, default=True),
            _manifest("b", "org/b", "b" * 40),
        ])
        switched = threading.Event()
        switch_thread: list[threading.Thread] = []

        class Outcome(dict):
            usage_reads = 0

            def get(self, key, default=None):
                if key == "usage":
                    self.usage_reads += 1
                    if self.usage_reads > 1:
                        self.assert_switch_completed()
                return super().get(key, default)

            @staticmethod
            def assert_switch_completed():
                if not switched.wait(2):
                    raise AssertionError("second model did not switch before response assembly")

        def complete_a(_messages, **_kwargs):
            manager.default._engine._loaded = True
            worker = threading.Thread(target=lambda: (
                manager.for_model("org/b").complete([]), switched.set()), daemon=True)
            switch_thread.append(worker)
            worker.start()
            return Outcome(text="one", elapsed_seconds=0.1, prompt_tokens=3)

        manager.default._engine.complete = complete_a

        class _Policy:
            data_dir = self.root
            def status(self):
                return {}

        with mock.patch.object(chat_main, "_host_eligibility", return_value={
                "eligible": True, "reason": None, "memory_warning": None}):
            client = TestClient(chat_main.create_app(manager, _Policy(), "secret", "r" * 64))
            response = client.post("/v1/chat/completions", headers={
                "x-studio-token": "secret"}, json={
                    "model": "org/a", "messages": [{"role": "user", "content": "hi"}],
                })
        switch_thread[0].join(timeout=2)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["usage"], {
            "prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4,
        })

    def test_stream_completed_event_keeps_usage_after_model_switch(self) -> None:
        manager = self._manager([
            _manifest("a", "org/a", "a" * 40, default=True),
            _manifest("b", "org/b", "b" * 40),
        ])
        events: list[dict] = []
        stream = manager.for_model("org/a").stream([], on_progress=events.append)
        self.assertEqual(list(stream), ["piece"])
        manager.for_model("org/b").complete([])
        self.assertEqual(events[-1]["usage"], {
            "prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4,
        })

    def test_proxy_fallback_usage_does_not_invent_zero_for_unknown_counts(self) -> None:
        manager = self._manager([_manifest("a", "org/a", "a" * 40, default=True)])
        engine = manager.default._engine
        engine.prompt_length = lambda _messages: None
        engine.count_tokens = lambda _text: None
        outcome = manager.default.complete([])
        self.assertEqual(outcome["usage"], {
            "prompt_tokens": 3, "completion_tokens": None, "total_tokens": None,
        })

        events: list[dict] = []
        self.assertEqual(list(manager.default.stream([], on_progress=events.append)), ["piece"])
        self.assertEqual(events[-1]["usage"], {
            "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        })

        engine.prompt_length = lambda _messages: 0
        engine.count_tokens = lambda _text: 0
        engine.complete = lambda _messages, **_kwargs: {"text": "", "elapsed_seconds": 0.0}
        outcome = manager.default.complete([])
        self.assertEqual(outcome["usage"], {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        })

    def test_catalog_publishes_pending_but_route_refuses_it_before_load(self) -> None:
        manager = self._manager([
            _manifest("base", "org/base", "a" * 40, default=True),
            _manifest("pending", "org/pending", "b" * 40, allowed=False),
        ])

        class _Policy:
            data_dir = self.root

            def status(self):
                return {}

        with mock.patch.object(chat_main, "_host_eligibility", return_value={
                "eligible": True, "reason": None, "memory_warning": None}):
            client = TestClient(chat_main.create_app(manager, _Policy(), "secret", "r" * 64))
            headers = {"x-studio-token": "secret"}
            rows = {row["model_id"]: row for row in
                    client.get("/api/catalog", headers=headers).json()["models"]}
            self.assertTrue(rows["org/base"]["default"])
            self.assertFalse(rows["org/pending"]["production_allowed"])
            self.assertFalse(rows["org/pending"]["execution_ready"])
            self.assertIsNone(rows["org/pending"]["qualified_revision"])
            self.assertFalse(rows["org/pending"]["qualified_revision_match"])
            refused = client.post("/v1/chat/completions", headers=headers, json={
                "model": "org/pending", "messages": [{"role": "user", "content": "hi"}],
            })
            self.assertEqual(refused.status_code, 409)
            self.assertIn("not qualified", refused.json()["detail"])
            self.assertEqual(manager.for_model("org/pending")._engine.complete_calls, 0)

            completed = client.post("/v1/chat/completions", headers=headers, json={
                "model": "org/base", "messages": [{"role": "user", "content": "hi"}],
                "job_id": "job-base",
            })
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertEqual(completed.json()["studio_hub"]["telemetry"]["final_attempt"], 1)
            own = client.get("/api/fleet/activity?job_id=job-base", headers=headers).json()
            self.assertEqual(own["latest"]["id"], "job-base")
            wrong = client.get("/api/fleet/activity?job_id=wrong", headers=headers).json()
            self.assertIsNone(wrong["active"])
            self.assertIsNone(wrong["latest"])

    def test_routes_use_the_final_attempt_finish_reason(self) -> None:
        manager = self._manager([_manifest("base", "org/base", "a" * 40, default=True)])
        engine = manager.default._engine

        def complete(_messages, *, on_progress=None, **_kwargs):
            telemetry = {"attempts": [{"attempt": 1, "finish_reason": "length"}],
                         "final_attempt": 1}
            if on_progress:
                on_progress({"phase": "completed", "attempt": 1,
                             "finish_reason": "length", "telemetry": telemetry})
            return {"text": "cut", "elapsed_seconds": 0.1, "prompt_tokens": 3,
                    "telemetry": telemetry}

        def stream(_messages, *, on_progress=None, **_kwargs):
            telemetry = {"attempts": [{"attempt": 1, "finish_reason": "length"}],
                         "final_attempt": 1}
            yield "cut"
            if on_progress:
                on_progress({"phase": "completed", "attempt": 1,
                             "finish_reason": "length", "telemetry": telemetry})
            return 0.1

        engine.complete = complete
        engine.stream = stream

        class _Policy:
            data_dir = self.root
            def status(self):
                return {}

        with mock.patch.object(chat_main, "_host_eligibility", return_value={
                "eligible": True, "reason": None, "memory_warning": None}):
            client = TestClient(chat_main.create_app(manager, _Policy(), "secret", "r" * 64))
            headers = {"x-studio-token": "secret"}
            body = {"messages": [{"role": "user", "content": "hi"}]}
            buffered = client.post("/v1/chat/completions", headers=headers, json=body).json()
            streamed = client.post("/v1/chat/completions", headers=headers,
                                   json={**body, "stream": True}).text
        self.assertEqual(buffered["choices"][0]["finish_reason"], "length")
        chunks = [json.loads(line[6:]) for line in streamed.splitlines()
                  if line.startswith("data: {")]
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "length")

    def test_unexpected_preflight_failure_closes_activity(self) -> None:
        manager = self._manager([_manifest("base", "org/base", "a" * 40, default=True)])

        def fail(*_args, **_kwargs):
            raise RuntimeError("tokenizer load failed")

        manager.default._engine.check_prompt_fits = fail

        class _Policy:
            data_dir = self.root
            def status(self):
                return {}

        with mock.patch.object(chat_main, "_host_eligibility", return_value={
                "eligible": True, "reason": None, "memory_warning": None}):
            client = TestClient(chat_main.create_app(manager, _Policy(), "secret", "r" * 64),
                                raise_server_exceptions=False)
            headers = {"x-studio-token": "secret"}
            response = client.post("/v1/chat/completions", headers=headers, json={
                "messages": [{"role": "user", "content": "hi"}], "stream": True,
                "job_id": "broken-preflight",
            })
            activity = client.get("/api/fleet/activity?job_id=broken-preflight",
                                  headers=headers).json()
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(activity["active"])
        self.assertEqual(activity["latest"]["state"], "error")
        self.assertEqual(activity["latest"]["error_code"], "generation_failed")

    def test_activity_updates_and_finishes_only_the_named_job_and_model(self) -> None:
        activity = chat_main.ChatActivity()
        activity.begin(job_id="one", model="org/a")
        activity.begin(job_id="two", model="org/b")
        activity.progress(job_id="two", model="org/a", event={"phase": "generating"})
        activity.finish(job_id="one", model="org/b", state="done")
        loaded = type("Loaded", (), {"loaded": lambda _self: False})()
        first = activity.snapshot(loaded)
        self.assertEqual(first["active"]["id"], "one")
        activity.finish(job_id="one", model="org/a", state="done")
        activity.progress(job_id="two", model="org/b", event={
            "phase": "processing", "attempt": 1,
            "processed_prompt_tokens": 2, "total_prompt_tokens": 5,
        })
        second = activity.snapshot(loaded)
        self.assertEqual(second["active"]["id"], "two")
        self.assertEqual(second["active"]["progress_stage"], "processing")
        self.assertEqual(second["active"]["processed_prompt_tokens"], 2)
        self.assertEqual(second["latest"]["id"], "one")

    def test_activity_drops_previous_attempt_metrics_when_repair_processing_starts(self) -> None:
        activity = chat_main.ChatActivity()
        loaded = type("Loaded", (), {"loaded": lambda _self: True})()
        activity.begin(job_id="repair", model="org/a")
        activity.progress(job_id="repair", model="org/a", event={
            "phase": "generating", "attempt": 1, "prompt_tokens": 10,
            "prompt_tps": 20.0, "generation_tokens": 3, "generation_tps": 4.0,
            "peak_memory_bytes": 5, "finish_reason": "length",
        })
        activity.progress(job_id="repair", model="org/a", event={
            "phase": "processing", "attempt": 2,
            "processed_prompt_tokens": 1, "total_prompt_tokens": 8,
        })
        active = activity.snapshot(loaded, job_id="repair")["active"]
        self.assertEqual(active["attempt"], 2)
        self.assertEqual(active["processed_prompt_tokens"], 1)
        for stale in ("prompt_tokens", "prompt_tps", "generation_tokens", "generation_tps",
                      "peak_memory_bytes", "finish_reason"):
            self.assertNotIn(stale, active)

    def test_activity_prefers_working_session_and_can_select_one_job_id(self) -> None:
        activity = chat_main.ChatActivity()
        loaded = type("Loaded", (), {"loaded": lambda _self: True})()
        activity.begin(job_id="waiting", model="org/a")
        activity.begin(job_id="working", model="org/b")
        activity.progress(job_id="working", model="org/b", event={
            "phase": "generating", "attempt": 1,
            "generation_tokens": 4, "generation_tps": 8.0,
        })

        fleet = activity.snapshot(loaded)
        self.assertEqual(fleet["active"]["id"], "working")
        self.assertEqual(fleet["active"]["progress_stage"], "generating")

        own = activity.snapshot(loaded, job_id="waiting")
        self.assertEqual(own["active"]["id"], "waiting")
        self.assertEqual(own["active"]["progress_stage"], "waiting")
        self.assertIsNone(own["latest"])

        activity.finish(job_id="working", model="org/b", state="done")
        finished = activity.snapshot(loaded, job_id="working")
        self.assertIsNone(finished["active"])
        self.assertEqual(finished["latest"]["id"], "working")

        unknown = activity.snapshot(loaded, job_id="not-a-job")
        self.assertIsNone(unknown["active"])
        self.assertIsNone(unknown["latest"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
