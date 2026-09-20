"""A paused model must be visible, and it must be releasable.

The Hub pauses a model on its own after repeated terminal failures. That is the right reflex - it
stops feeding a broken model work - but `recheck_model_protection` was defined and never called by
anything, so in practice a pause was permanent: the Hub refused every job for the model before
dispatch, the reason lived only in the Hub process, and the only way back was to edit
`model_protection.json` and relaunch the app.

That is not a hypothetical. Kokoro was paused by a wrong field name in the worker, and the pause is
why it looked like a broken model through three releases: each retest was refused before it ran, so
the operator kept seeing the same old failure reason instead of a fresh attempt.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

from components.hub.backend import broker  # noqa: E402

STANDALONE = (ROOT / "components" / "hub" / "standalone.py").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()


class ModelBlockStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = dict(broker._model_protection)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patch = mock.patch.object(broker, "MODEL_PROTECTION_FILE",
                                  Path(self.directory.name) / "model_protection.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(lambda: broker._model_protection.update(self.previous))

    def test_a_model_that_has_not_failed_reports_no_pause(self) -> None:
        broker._model_protection.clear()
        self.assertIsNone(broker.model_block_state("voice", "mlx-community/Kokoro-82M-bf16"))

    def test_a_paused_model_reports_the_reason_a_person_can_act_on(self) -> None:
        broker._model_protection.clear()
        broker._model_protection[("voice", "mlx-community/Kokoro-82M-bf16")] = {
            "studio": "voice", "model": "mlx-community/Kokoro-82M-bf16",
            "revision": "a71e4d38", "reason": "RuntimeError: SystemExit: 1",
            "error_code": "WORKER_TERMINAL_ERROR", "failures": 3,
            "blocked": True, "blocked_at": 1789871097.6, "last_failure_at": 1789871097.6,
        }
        state = broker.model_block_state("voice", "mlx-community/Kokoro-82M-bf16")
        self.assertIsNotNone(state)
        self.assertEqual(state["reason"], "RuntimeError: SystemExit: 1")
        self.assertEqual(state["failures"], 3)
        self.assertEqual(state["studio"], "voice")

    def test_a_failed_but_unblocked_model_is_not_reported_as_paused(self) -> None:
        """One failure is not a pause, and reporting it as one would hide a working model."""
        broker._model_protection.clear()
        broker._model_protection[("voice", "some/model")] = {
            "studio": "voice", "model": "some/model", "reason": "boom",
            "failures": 1, "blocked": False,
        }
        self.assertIsNone(broker.model_block_state("voice", "some/model"))

    def test_the_pause_snapshot_is_paused_models_only(self) -> None:
        broker._model_protection.clear()
        broker._model_protection[("voice", "blocked/model")] = {
            "studio": "voice", "model": "blocked/model", "reason": "boom", "blocked": True}
        broker._model_protection[("voice", "fine/model")] = {
            "studio": "voice", "model": "fine/model", "reason": "boom", "blocked": False}
        self.assertEqual([row["model"] for row in broker.model_protection_snapshot()],
                         ["blocked/model"])


class StalePauseTests(unittest.TestCase):
    """A pause for a model this build does not ship can never be cleared, so it must be dropped."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patch = mock.patch.object(broker, "MODEL_PROTECTION_FILE",
                                  Path(self.directory.name) / "model_protection.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(broker._model_protection.clear)

    def test_pauses_for_unshipped_models_are_forgotten(self) -> None:
        broker._model_protection.clear()
        broker._model_protection[("voice", "SWivid/F5-TTS")] = {
            "studio": "voice", "model": "SWivid/F5-TTS", "reason": "runtime unavailable",
            "blocked": True}
        broker._model_protection[("voice", "mlx-community/Kokoro-82M-bf16")] = {
            "studio": "voice", "model": "mlx-community/Kokoro-82M-bf16", "reason": "boom",
            "blocked": True}
        forgotten = broker.prune_model_protection({"mlx-community/Kokoro-82M-bf16"})
        self.assertEqual(forgotten, ["SWivid/F5-TTS"])
        self.assertIsNone(broker.model_block_state("voice", "SWivid/F5-TTS"))
        self.assertIsNotNone(broker.model_block_state("voice", "mlx-community/Kokoro-82M-bf16"),
                             "a model this build does ship must keep its pause")

    def test_nothing_is_reported_when_every_pause_is_still_relevant(self) -> None:
        broker._model_protection.clear()
        broker._model_protection[("voice", "mlx-community/Kokoro-82M-bf16")] = {
            "studio": "voice", "model": "mlx-community/Kokoro-82M-bf16", "reason": "boom",
            "blocked": True}
        self.assertEqual(broker.prune_model_protection({"mlx-community/Kokoro-82M-bf16"}), [])


class PauseIsReachableTests(unittest.TestCase):
    def test_the_hub_offers_a_recheck_route_and_calls_the_real_clearer(self) -> None:
        """The bug, pinned where it happened: the clearer existed but nothing called it."""
        self.assertIn('"/api/hub/model-health/recheck"', STANDALONE,
                      "a pause with no route out is a permanent refusal")
        self.assertIn("await broker.recheck_model_protection(studio, model)", STANDALONE)
        self.assertIn('"/api/hub/model-health"', STANDALONE,
                      "the app has to be able to see which models are paused")

    def test_a_paused_model_is_marked_in_the_package_row(self) -> None:
        self.assertIn('"paused": paused is not None', STANDALONE)
        self.assertIn('"paused_reason": (paused or {}).get("reason")', STANDALONE)

    def test_the_app_decodes_the_pause(self) -> None:
        package = API[API.index("private struct HubModelPackage: Decodable"):]
        package = package[:package.index("private struct HubModelDownload")]
        for field in ("paused", "pausedReason", "pausedStudio"):
            self.assertIn(f"let {field}", package, f"{field} must be decoded, not discarded")

    def test_the_pause_outranks_the_other_gates_when_explaining_a_refusal(self) -> None:
        builder = API[API.index("private static func blockReason("):]
        builder = builder[:builder.index("private static func mapHubJob")]
        self.assertIn("Paused by the Hub", builder)
        self.assertLess(builder.index("package.paused == true"), builder.index("package.reason"),
                        "while paused every job is refused, whatever the other gates say")

    def test_the_models_page_shows_the_pause_and_the_way_out(self) -> None:
        models = VIEWS[VIEWS.index("private var modelsWorkspace"):]
        models = models[:models.index("private var ", 10)]
        self.assertIn("model.paused", models)
        self.assertIn("recheckModel", models, "the page that explains a refusal must offer the fix")

    def test_a_recheck_reports_what_happened(self) -> None:
        call = API[API.index("func recheckModel("):]
        call = call[:call.index("func cancelAudio(")]
        self.assertIn('["api", "hub", "model-health", "recheck"]', call)
        self.assertIn("modelNote", call, "an action with no visible result reads as a no-op")


if __name__ == "__main__":
    unittest.main()
