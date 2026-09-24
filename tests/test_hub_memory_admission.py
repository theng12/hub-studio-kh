"""The Hub's live-free-memory governor covers every modality it routes.

``memory_admission.SUPPORTED_MODALITIES`` named five modalities and omitted ``chat``, so
``applies_to("chat")`` was False. That skipped the Hub's own queue ranking for the 9B text
model (``broker._batch_memory_constraint_gb``) and published no admission evidence for a
chat model in ``monitor.snapshot`` or the capability contract — the operator could not see
the floor the governor was using, and the next modality would have been omitted the same way.

The rule asserted here is deliberately not a list. It is:

* every modality the Hub routes is covered, derived from the Hub's own vocabulary, so the
  next one is covered the moment it is registered; and
* the floor comes from the row the worker publishes, under whichever spelling it ships —
  ``min_unified_memory_gb`` (Image, Voice, Music) or ``min_total_memory_gb`` (Chat,
  Subtitle) — so the modality name is never the gate.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub import standalone_config
from components.hub.backend import broker, capabilities, memory_admission
from components.hub.backend import vocabulary

CHAT_REPO = "mlx-community/Qwen3.5-9B-OptiQ-4bit"


def chat_row(**fields) -> dict:
    """A row shaped like the shipped Chat worker's own ``/api/catalog`` answer."""
    row = {
        "repo": CHAT_REPO,
        "modality": "chat",
        "hub_modality": "chat",
        "hub_cached": True,
        "hub_ready": True,
        "memory_eligible": True,
        "min_total_memory_gb": 9.0,
    }
    row.update(fields)
    return row


class EveryRoutedModalityIsGovernedTests(unittest.TestCase):
    def test_every_modality_the_hub_routes_is_governed(self) -> None:
        """Derived from the Hub's own vocabulary: registering a modality covers it."""
        routed = set(vocabulary.WORKER_FOR_MODALITY)
        self.assertTrue(routed, "the Hub's modality vocabulary is empty")
        for modality in sorted(routed):
            with self.subTest(modality=modality):
                self.assertTrue(
                    memory_admission.applies_to(modality),
                    f"{modality!r} is routed by the Hub but the RAM governor skips it",
                )

    def test_the_governor_does_not_gate_on_the_modality_name(self) -> None:
        """A modality nobody has registered yet must not silently opt out of the governor."""
        for modality in ("chat", "a-modality-from-the-next-release", "", None):
            with self.subTest(modality=modality):
                self.assertTrue(memory_admission.applies_to(modality))

    def test_the_worker_table_is_the_same_vocabulary(self) -> None:
        """The packaged worker descriptors are the modality table's inverse, not a copy."""
        self.assertEqual(
            set(standalone_config._REQUIRED_WORKERS),
            set(vocabulary.WORKER_FOR_MODALITY.values()),
        )


class TheWorkerPublishesTheFloorTests(unittest.TestCase):
    def test_a_chat_row_publishes_its_own_total_memory_tier(self) -> None:
        policy = memory_admission.describe(CHAT_REPO, chat_row())
        self.assertEqual(
            policy["catalog_min_total_memory_gb"], 9.0,
            "the Chat worker's own min_total_memory_gb was not read, so the 9B model had "
            "no published floor at all",
        )
        self.assertEqual(policy["effective_min_total_memory_gb"], 0.0)
        self.assertEqual(policy["effective_min_free_memory_gb"], 2.0)

    def test_a_chat_row_publishes_its_own_free_memory_floor(self) -> None:
        policy = memory_admission.describe(CHAT_REPO, chat_row(min_free_memory_gb=3.5))
        self.assertEqual(policy["effective_min_free_memory_gb"], 3.5)
        self.assertEqual(broker._admission_requirements(CHAT_REPO, chat_row(min_free_memory_gb=3.5)),
                         {"min_total": 0.0, "min_free": 3.5})

    def test_the_spelling_a_worker_uses_does_not_change_the_floor(self) -> None:
        unified = memory_admission.describe(CHAT_REPO, chat_row(min_unified_memory_gb=9.0))
        total = memory_admission.describe(CHAT_REPO, chat_row(min_total_memory_gb=9.0))
        self.assertEqual(unified["catalog_min_total_memory_gb"],
                         total["catalog_min_total_memory_gb"])
        self.assertEqual(unified["default_min_free_memory_gb"],
                         total["default_min_free_memory_gb"])

    def test_a_chat_job_is_ranked_by_its_floor(self) -> None:
        """`_batch_memory_constraint_gb` skipped chat entirely, so its queue had no floor."""
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-admission-")
        original = memory_admission.SETTINGS_FILE
        self.addCleanup(setattr, memory_admission, "SETTINGS_FILE", original)
        self.addCleanup(memory_admission.reset_for_tests)
        self.addCleanup(temporary.cleanup)
        memory_admission.SETTINGS_FILE = (
            Path(temporary.name) / "memory_admission_overrides.json"
        )
        memory_admission.reset_for_tests()
        memory_admission.set_override(
            CHAT_REPO, min_total_memory_gb=9.0, min_free_memory_gb=2.5,
            catalog_entry=chat_row(),
        )
        entry = {"entry": chat_row(), "observed_at": 1.0, "stale": False, "error": None}
        called = {}

        class _Monitor:
            def scheduling_catalog_entries(self, model, *, modality=None):
                called["modality"] = modality
                return [entry]

        original_monitor = broker._monitor
        broker._monitor = lambda: _Monitor()
        try:
            constraint = broker._batch_memory_constraint_gb(
                {"model": CHAT_REPO, "modality": "chat"}
            )
        finally:
            broker._monitor = original_monitor
        self.assertEqual(called.get("modality"), "chat")
        self.assertEqual(
            constraint, 9.0,
            "a chat batch contributed no memory floor to the queue ranking",
        )

    def test_a_chat_model_publishes_admission_evidence(self) -> None:
        model = chat_row()
        published = capabilities._model_capability(
            model,
            {"id": "chat", "modality": "chat", "machine": "local"},
            {
                "id": "chat", "modality": "chat", "online": True, "ready": True,
                "drained": False, "maintenance": False, "machine_quarantined": False,
                "busy": False, "physical_machine_busy": False,
                "available_capacity": {"slots": 1},
            },
        )
        self.assertIsNotNone(
            published.get("memory_admission"),
            "the capability contract published no admission evidence for the chat model",
        )
        self.assertEqual(published["memory_admission"]["catalog_min_total_memory_gb"], 9.0)


if __name__ == "__main__":
    unittest.main()
