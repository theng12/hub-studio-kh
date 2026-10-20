"""A Chat request too large for every Studio is told so, not queued for ever.

WHY THIS EXISTS (output-limit audit, 2026-10-19). `_eligible_studios` skips a Studio whose
model publishes a smaller output ceiling than the pack asks for — correctly: the worker would
422 such a pack, so dispatching it would be worse. But when that filter excluded *every*
Studio, `dispatch_once` wrote *"Waiting for a free online Chat Studio with this model cached"*
and tried again for ever. The request was not waiting for a Mac; no Studio serving that model
could ever take it, and nothing said so. The note now names the ceiling.

The other half is the number itself. The owner's 2026-10-19 decision raised the worker default
to **128,000** ("the minimum is at least 128K tokens"), and a request for exactly that many
tokens is exactly at a fallback-only Studio's published ceiling. The filter compares with `<`,
so it is eligible — a filter written with `<=` would have turned every 128K request into a
permanent queue. Both facts are pinned here.

Nothing in this module starts a worker or a Hub: the monitor is a stub, and the one broker call
the not-eligible path makes is `in_maintenance`.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

# The Hub's own data root, set before the backend imports read it — the gate's env does the
# same. Never the owner's real hub data, and never left behind: a `setdefault` here put a models
# root into the whole test process, so every later module that reads `STUDIOHUB_MODELS_DIR` saw a
# library that is not there and took the packaged model gate. That is what made twelve
# `test_chat_hub_wiring` tests fail whenever this module was imported first.
_TEMPORARY = tempfile.TemporaryDirectory()
_SCRATCH = Path(_TEMPORARY.name)
_OWNED = ("STUDIOHUB_DATA_DIR", "STUDIOHUB_MODELS_DIR", "STUDIOHUB_WORKERS_FILE")
_WAS = {key: os.environ.get(key) for key in _OWNED}
os.environ.setdefault("STUDIOHUB_DATA_DIR", _TEMPORARY.name)
os.environ.setdefault("STUDIOHUB_MODELS_DIR", str(_SCRATCH / "models"))
os.environ.setdefault("STUDIOHUB_WORKERS_FILE", str(_SCRATCH / "workers.json"))
Path(os.environ["STUDIOHUB_MODELS_DIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["STUDIOHUB_WORKERS_FILE"]).write_text("[]\n")

from components.chat.app.backend.llm_engine import FALLBACK_MAX_OUTPUT_TOKENS  # noqa: E402
from components.hub.backend import chat_jobs  # noqa: E402

for _key, _value in _WAS.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value

MODEL = "org/chat-model"
REVISION = "rev-1"


def _monitor(published_limit: int | None) -> SimpleNamespace:
    """One online chat Studio serving `MODEL`, publishing `published_limit`."""
    studio = {"id": "chat-1", "modality": "chat", "machine": "node-a", "local": False}
    entry = {"repo": MODEL, "cached": True, "runtime_revision": REVISION,
             "verified_token_usage": True}
    if published_limit is not None:
        entry["max_output_tokens"] = published_limit

    async def scheduling_catalog(_studio):
        return {"available": True, "models": [entry]}

    return SimpleNamespace(registry=[studio], status={studio["id"]: {"status": "up"}},
                           scheduling_catalog=scheduling_catalog)


class _Eligibility(unittest.TestCase):
    """The filter's own answer, with the rest of the routing gates held open."""

    def setUp(self) -> None:
        chat_jobs.busy_studios.clear()
        self.addCleanup(chat_jobs.busy_studios.clear)
        patches = [
            mock.patch.object(chat_jobs.peers, "dispatch_ready", lambda *_a: True),
            mock.patch.object(chat_jobs.peers, "lite_mode_allows", lambda *_a: True),
            mock.patch.object(chat_jobs.broker, "is_cached", lambda _entry: True),
            mock.patch.object(chat_jobs.broker, "in_maintenance", lambda _id: False),
            mock.patch.object(chat_jobs.broker, "busy_machines", lambda: set()),
            mock.patch.object(chat_jobs, "machine_enabled", lambda _machine: True),
            mock.patch.object(chat_jobs, "studio_enabled", lambda *_a: True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _eligible(self, required: int, published_limit: int | None) -> tuple[list, int | None]:
        return asyncio.run(chat_jobs._eligible_studios(
            _monitor(published_limit), MODEL, {"model_revision": REVISION}, required))


class FallbackAllowanceTests(unittest.TestCase):
    def test_the_last_resort_allowance_is_the_owners_128k(self) -> None:
        """The owner's decision, 2026-10-19: *"the minimum is at least 128K tokens."*

        Pinned as a number rather than read back through the constant, deliberately: the
        constant is what this test is about, so comparing it to itself would assert only that
        nobody edited the file.
        """
        self.assertEqual(128_000, FALLBACK_MAX_OUTPUT_TOKENS)


class PublishedCeilingTests(_Eligibility):
    def test_a_request_exactly_at_the_published_ceiling_is_eligible(self) -> None:
        """128K against a 128K ceiling is not "too large" — it is exactly the allowance.

        This is the case the owner's decision creates: a model that states no output figure of
        its own publishes the worker default, and a caller may ask for all of it.
        """
        eligible, largest = self._eligible(FALLBACK_MAX_OUTPUT_TOKENS, FALLBACK_MAX_OUTPUT_TOKENS)
        self.assertEqual([studio["id"] for studio in eligible], ["chat-1"])
        self.assertEqual(largest, FALLBACK_MAX_OUTPUT_TOKENS)

    def test_a_request_above_every_ceiling_is_not_eligible_and_reports_the_ceiling(self) -> None:
        above = FALLBACK_MAX_OUTPUT_TOKENS + 1
        eligible, largest = self._eligible(above, FALLBACK_MAX_OUTPUT_TOKENS)
        self.assertEqual(eligible, [], "the worker would 422 this pack, so it is not dispatched")
        self.assertEqual(largest, FALLBACK_MAX_OUTPUT_TOKENS,
                         "and the caller can be told which ceiling it is above")

    def test_a_ceiling_is_reported_even_when_the_studio_fails_a_later_gate(self) -> None:
        """The scan is not limited to the Studios that passed the ceiling test.

        A Studio whose model revision does not match the assignment is skipped too — and its
        ceiling is still the largest one on offer for this model, which is what the note has to
        name for the operator to act on it.
        """
        monitor = _monitor(65_536)
        monitor.registry.append({"id": "chat-2", "modality": "chat", "machine": "node-b",
                                 "local": False})
        monitor.status["chat-2"] = {"status": "up"}
        mismatch = {"repo": MODEL, "cached": True, "runtime_revision": "other-revision",
                    "verified_token_usage": True, "max_output_tokens": 262_144}
        matching = {"repo": MODEL, "cached": True, "runtime_revision": REVISION,
                    "verified_token_usage": True, "max_output_tokens": 65_536}

        async def scheduling_catalog(studio):
            return {"available": True,
                    "models": [mismatch if studio["id"] == "chat-2" else matching]}

        monitor.scheduling_catalog = scheduling_catalog
        eligible, largest = asyncio.run(chat_jobs._eligible_studios(
            monitor, MODEL, {"model_revision": REVISION}, 131_072))
        self.assertEqual(eligible, [])
        self.assertEqual(largest, 262_144,
                         "the reason names what is on offer, not only what was eligible")


class QueueNoteTests(_Eligibility):
    """What a stuck batch says about itself."""

    def _dispatch(self, required: int, published_limit: int | None, *,
                  status: str = "up") -> dict:
        batch = {
            "id": f"ceiling-{required}-{status}", "created_at": time.time(),
            "idempotency_key": f"ceiling-key-{required}-{status}", "cancelled": False,
            "model": MODEL, "genstudio_execution": {"model_revision": REVISION},
            "packs": [{"index": 0, "state": "queued", "params": {"max_tokens": required}}],
            "queue_note": None,
        }
        monitor = _monitor(published_limit)
        monitor.status["chat-1"] = {"status": status}
        chat_jobs.batches[batch["id"]] = batch
        self.addCleanup(chat_jobs.batches.pop, batch["id"], None)
        with mock.patch.object(chat_jobs, "_save", lambda _batch: None):
            asyncio.run(chat_jobs.dispatch_once(monitor))
        return batch

    def test_a_request_no_studio_can_take_says_so_instead_of_blaming_availability(self) -> None:
        required = FALLBACK_MAX_OUTPUT_TOKENS + 1
        batch = self._dispatch(required, FALLBACK_MAX_OUTPUT_TOKENS)
        note = batch["queue_note"]
        self.assertIsNotNone(note, "a batch that cannot move must say something")
        self.assertIn(f"{required:,}", note)
        self.assertIn(f"{FALLBACK_MAX_OUTPUT_TOKENS:,}", note)
        self.assertIn("not waiting for a free Mac", note,
                      "a free Mac would not help: the model's ceiling is the reason")
        self.assertNotEqual(note, "Waiting for a free online Chat Studio with this model cached")

    def test_a_studio_that_is_merely_offline_keeps_the_availability_note(self) -> None:
        """The control: the old sentence is still right when the ceiling is not the reason."""
        batch = self._dispatch(FALLBACK_MAX_OUTPUT_TOKENS, 262_144, status="down")
        self.assertEqual(batch["queue_note"],
                         "Waiting for a free online Chat Studio with this model cached")


if __name__ == "__main__":
    unittest.main()
