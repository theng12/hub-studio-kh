"""A model the Mac does not have is reported as not installed, not as a broken runtime.

Reported from Mac 1100, 2026-11-19: the Hub published `memory_eligible: true` for
`qwen3-5-9b-4bit` on a 16 GB Mac while the Gateway's catalogue called it locked. The row was
telling the truth about memory and the reason beside it was not telling the truth about why the
model could not run: the worker reports `execution_ready: false` whenever the files are absent, and
that branch outranked the plain fact — the model is not installed. Fake rows only: no workers.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import capabilities  # noqa: E402

REPO = "mlx-community/Qwen3.5-9B-4bit"


def worker() -> dict:
    return {
        "service_id": "chat", "physical_machine_id": "local", "online": True, "ready": True,
        "busy": False, "physical_machine_busy": False, "drained": False, "maintenance": False,
        "machine_quarantined": False, "available_capacity": {"slots": 1},
    }


def row(*, cached: bool) -> dict:
    """The chat worker's own row for a 16 GB Mac: the memory fits, the files may not be there."""
    return {
        "repo": REPO, "model_id": REPO, "hub_modality": "chat", "hub_cached": cached,
        "available": cached, "runtime_ready": True, "execution_ready": cached,
        "memory_eligible": True, "min_unified_memory_gb": 16.0,
        "hub_catalog_error": None, "hub_catalog_stale": False,
    }


class TheReasonNamesTheMissingInstall(unittest.TestCase):
    def capability(self, model: dict) -> dict:
        with mock.patch.object(capabilities, "_machine_host",
                               return_value=(True, {"total_gb": 16.0, "available_gb": 8.0})):
            return capabilities._model_capability(
                model, {"machine": "local", "modality": "chat"}, worker())

    def test_an_uninstalled_model_reports_a_missing_install(self) -> None:
        availability = self.capability(row(cached=False))["availability"]
        self.assertEqual(availability["reason"], "model_not_installed")
        self.assertIs(availability["reported_memory_eligible"], True)
        self.assertIs(availability["installed"], False)

    def test_an_installed_model_with_an_unready_execution_keeps_its_own_reason(self) -> None:
        model = row(cached=True)
        model["execution_ready"] = False
        self.assertEqual(self.capability(model)["availability"]["reason"],
                         "worker_execution_unready")


if __name__ == "__main__":
    unittest.main()
