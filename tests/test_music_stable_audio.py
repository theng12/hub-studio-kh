"""Stable Audio 3 runner contracts: progress parsing and cancel semantics."""
from __future__ import annotations

import io
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "components" / "music" / "engines" / "stable_audio_3"


class StableAudioRunnerTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT / "components" / "music" / "app"))
        from backend import stable_audio  # noqa: PLC0415
        self.module = stable_audio

    def test_cancelled_is_a_kind_of_failure(self):
        # The worker catches Sa3Cancelled before the generic handler so a cancelled
        # job settles cancelled, not error.
        self.assertTrue(issubclass(self.module.Sa3Cancelled, self.module.Sa3Error))

    def test_every_stage_marker_exists_in_the_vendored_cli(self):
        script = (ENGINE / "scripts" / "sa3_mlx.py").read_text()
        for marker in self.module.STAGE_PROGRESS:
            self.assertIn(f'"{marker}"', script,
                          f"{marker} is not a stage the CLI prints; progress would never move")

    def test_read_output_reports_real_progress(self):
        seen: list[tuple[float, str]] = []
        stream = io.StringIO(
            "  \x1b[1;36m[1/5]\x1b[0m \x1b[1mT5Gemma encode\x1b[0m\n"
            "        embeds (1, 128, 768)\n"
            "  \x1b[1;36m[3/5]\x1b[0m \x1b[1mDiT — load + sample\x1b[0m\n"
            "  \x1b[1;36m[4/5]\x1b[0m \x1b[1mDecoder\x1b[0m\n")
        self.module._read_output(stream, [], lambda ratio, label: seen.append((ratio, label)))
        self.assertEqual([round(r, 2) for r, _ in seen], [0.10, 0.45, 0.80])
        self.assertEqual(seen[0][1], "T5Gemma text encode")

    def test_read_output_keeps_a_bounded_tail(self):
        sink: list[str] = []
        stream = io.StringIO("".join(f"line {index}\n" for index in range(1000)))
        self.module._read_output(stream, sink, None)
        self.assertLessEqual(len(sink), 400)

    def test_progress_callback_errors_never_kill_the_run(self):
        def explode(_ratio, _label):
            raise RuntimeError("a bad consumer")
        sink: list[str] = []
        stream = io.StringIO("  [1/5] T5Gemma encode\n")
        self.module._read_output(stream, sink, explode)
        self.assertTrue(any("T5Gemma" in line for line in sink))


if __name__ == "__main__":
    unittest.main()
