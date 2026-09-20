"""Isolated ST-02 Hub/Broker proof.

The real loopback worker and all packaged environment imports live in a child
process so this test cannot contaminate the normal Hub configuration suite.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

# A 659-line string literal used to sit here: a full copy of this module's own source, parsed by
# Python as inert text and executed by nothing. Two of the ten Subtitle lifecycle tests it held
# had never run, and edits aimed at this file silently landed inside it. They now live in
# tests/_subtitle_lifecycle.py, run by tests/test_subtitle_lifecycle_gate.py in their own process
# because their fixtures set STUDIOHUB_* at import and rebind the Hub's data directory for every
# module loaded after them.


class HubJobProofTests(unittest.TestCase):
    def test_real_broker_lifecycle_proof(self) -> None:
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = subprocess.run([sys.executable, "-B", "-c", _PROOF],
                                text=True, capture_output=True, env=env, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class VoiceChoiceLiftingTests(unittest.TestCase):
    """Both ways of naming a voice must survive submission.

    The Hub lifts a voice choice from the top level of the envelope into `sharedParams`, which is
    where dispatch reads it. `voice_reference_asset_id` was lifted from the start;
    `voice_library_id` was not, so a saved voice chosen in the app was dropped on the way to the
    Voice worker - which then refused the job with "Voice cloning needs a reference voice or private
    reference upload", a message about a reference the operator had not been asked for. These pin
    both keys, and pin that an explicit `sharedParams` value still wins.
    """

    def _lift(self, envelope):
        from components.hub.backend import broker
        return broker._shared_params_with_local_reference(envelope)

    def test_a_top_level_saved_voice_is_lifted(self):
        self.assertEqual(self._lift({"voice_library_id": " b96e838f75ab "}),
                         {"voice_library_id": "b96e838f75ab"})

    def test_a_top_level_reference_asset_is_still_lifted(self):
        self.assertEqual(self._lift({"voice_reference_asset_id": "abc123"}),
                         {"voice_reference_asset_id": "abc123"})

    def test_both_choices_travel_together(self):
        lifted = self._lift({"voice_library_id": "voice-1", "voice_reference_asset_id": "asset-1"})
        self.assertEqual(lifted, {"voice_library_id": "voice-1",
                                  "voice_reference_asset_id": "asset-1"})

    def test_an_explicit_shared_param_is_not_overwritten(self):
        lifted = self._lift({"voice_library_id": "outer",
                             "sharedParams": {"voice_library_id": "inner"}})
        self.assertEqual(lifted["voice_library_id"], "inner")

    def test_an_absent_choice_adds_nothing(self):
        self.assertEqual(self._lift({}), {})
        self.assertEqual(self._lift({"voice_library_id": "   "}), {})



class FinishedBatchMemoryTests(unittest.TestCase):
    """A Hub that runs for weeks must not hold every job it has ever seen.

    `broker.batches` is the in-memory copy that a running dispatch is polled through; the durable
    record is the ledger, and the listings merge the two. Nothing ever removed a finished batch, so
    the dict - and every walk of it, on every status read - grew without bound.
    """

    def test_old_finished_batches_leave_memory_but_recent_and_running_ones_stay(self):
        import time as _time
        from components.hub.backend import broker
        saved = dict(broker.batches)
        try:
            broker.batches.clear()
            now = _time.time()
            broker.batches.update({
                "long-done": {"id": "long-done", "modality": "voice", "created_at": now - 7200,
                              "finished_at": now - 7200, "items": [{"state": "done"}]},
                "just-finished": {"id": "just-finished", "modality": "voice", "created_at": now,
                                  "finished_at": now, "items": [{"state": "done"}]},
                "still-running": {"id": "still-running", "modality": "voice", "created_at": now - 9000,
                                  "items": [{"state": "running"}]},
                "half-done": {"id": "half-done", "modality": "voice", "created_at": now - 9000,
                              "finished_at": now - 9000,
                              "items": [{"state": "done"}, {"state": "queued"}]},
            })
            removed = broker.evict_finished_batches(keep_recent=1, older_than_s=900)
            self.assertEqual(removed, 1)
            self.assertNotIn("long-done", broker.batches, "an old finished batch is pure memory")
            self.assertIn("just-finished", broker.batches, "the recent ones are the ETA basis")
            self.assertIn("still-running", broker.batches, "a running job is not history")
            self.assertIn("half-done", broker.batches, "half a batch is not finished")
        finally:
            broker.batches.clear()
            broker.batches.update(saved)


if __name__ == "__main__":
    unittest.main()
