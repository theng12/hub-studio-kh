"""A long script's sections are brought to one loudness before they are joined.

**The owner, on the Breeze render:** *"did you fix the loudness and the make the joiner work?"*

He was asking about a real defect, and one this joiner had for **every** family. A long script is
rendered as independent sections and nothing told the engine what level the section before it came
out at. Measured on Breeze TTS 2 4-bit, the 14 sections of one 1,227-word script spanned **4.9 LU**
of integrated loudness (−30.0 to −25.0 LUFS): the narrator audibly steps back from the microphone at
each paragraph and forward again at the next. The join itself was already correct — right pause,
right order, no drift — so it was never a joining fault. It was the absence of a levelling pass.

**Why the obvious fix does not work, and this is the part worth keeping.** The first implementation
gave each section one linear gain of `min(target − I, ceiling − TP)`. On the real segments that
narrowed the span only from **4.9 LU to 4.3 LU**, because the sections that most needed lifting were
also the ones with the tallest peaks (crest factors measured 18.4 to 22.7 dB), so the peak ceiling
capped their gain and the step survived. FFmpeg's EBU R128 `loudnorm`, applied per section, brought
the same 14 sections to a **1.5 LU** span — and produced a joined file measuring **−17.50 LUFS /
−1.42 dBTP**, identical to the sample the owner listened to and approved. So the pass is `loudnorm`,
and `long_form_policy` carries those measurements beside the parameters.

The tests below drive the real joiner over real WAV files and measure the result, rather than
asserting that a function was called. A levelling pass that is wired up but does not level is the
defect this file exists to catch — the same shape as a stamping tool that had never once run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_APP = ROOT / "components" / "voice" / "app"
if str(VOICE_APP) not in sys.path:
    sys.path.insert(0, str(VOICE_APP))

# The shipped voice runtime carries FFmpeg, and `packaging/voice_worker.py` points the worker at its
# `bin` directory. The gate runs the checkout rather than the packaged app, so the tests below set
# the same variable — but **inside each test**, never at import.
#
# Setting it here, at module level, was a real defect and this note is why it is not coming back:
# `os.environ` is process-wide, the gate imports every test module into one process, and
# `tests/test_voice_product_suite.py` spawns the Voice product suite as a **subprocess that
# inherits the environment**. So importing this file silently turned loudness matching on inside
# another suite's subprocess, and
# `test_qwen_clone_long_form_join_uses_production_boundary_pacing` — which identifies its sections
# by giving each one its own amplitude — failed there while passing when run alone. A test module
# that changes what a different suite measures is worse than a failing test, because the failure
# only appears in a full run and looks like someone else's bug.
VOICE_RUNTIME_BIN = ROOT / ".build" / "voice-runtime" / "bin"

import numpy as np  # noqa: E402  (after the path setup above)
from backend import generation, long_form_policy  # noqa: E402


def _ffmpeg() -> str | None:
    candidate = VOICE_RUNTIME_BIN / "ffmpeg"
    return str(candidate) if candidate.is_file() else None


def _measure(path: Path) -> tuple[float, float]:
    """Integrated LUFS and true peak of a file, measured by the same meter the fix uses."""
    ffmpeg = _ffmpeg()
    assert ffmpeg is not None
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", completed.stderr or "", re.S)
    assert match is not None, completed.stderr[-2000:]
    payload = json.loads(match.group(0))
    return float(payload["input_i"]), float(payload["input_tp"])


def _speech(path: Path, seconds: float, amplitude: float, rate: int = 24_000) -> Path:
    """A section of noise-shaped tone: something with a real level, not a constant.

    It is deliberately **peaky** — a quiet carrier with a short loud burst — because that is the
    shape that made the linear implementation fail, and a test built from flat tones would have
    passed it.

    `* 32767` before the cast is load-bearing and was missing: `float64.astype("<i2")` **truncates**
    rather than scales, so every sample below 1.0 became 0 and this fixture wrote pure silence. The
    first version of these tests then passed against that silence — the level test vacuously, since
    silent sections trivially have no level difference. `_assert_audible` below is what stops it
    happening again.
    """
    frames = int(seconds * rate)
    time = np.arange(frames) / rate
    carrier = 0.25 * amplitude * np.sin(2 * np.pi * 180 * time)
    mod = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * time)
    signal = carrier * mod
    burst = int(0.05 * rate)
    signal[:burst] += amplitude * 0.9 * np.sin(2 * np.pi * 900 * np.arange(burst) / rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (np.clip(signal, -1.0, 1.0) * 32767.0).astype("<i2")
    _assert_audible(encoded, path.name)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(encoded.tobytes())
    return path


def _assert_audible(encoded: np.ndarray, name: str) -> None:
    """Refuse to run a loudness test against a fixture that has no level to measure.

    This is the whole failure this file exists to catch, one level down: a test that is green
    because its input is silent proves nothing about a levelling pass. It is asserted rather than
    assumed, because it has already been wrong once.
    """
    peak = int(np.max(np.abs(encoded))) if encoded.size else 0
    if peak < 100:
        raise AssertionError(
            f"the {name} fixture encodes to silence (peak sample {peak}); every assertion made "
            "against it would pass for the wrong reason")



class TheJoinerLevelsEverySectionTest(unittest.TestCase):
    def setUp(self) -> None:
        if _ffmpeg() is None:
            self.skipTest(
                f"no FFmpeg at {VOICE_RUNTIME_BIN} — the shipped runtime carries it, and the "
                "levelling pass is FFmpeg's; without it these tests would measure the fallback")
        # Set per test and restored by the patch, so importing this module cannot change what any
        # other suite measures. See the note beside VOICE_RUNTIME_BIN.
        self._tools = mock.patch.dict(
            os.environ, {"STUDIOHUB_TOOLS_DIR": str(VOICE_RUNTIME_BIN)})
        self._tools.start()
        self.addCleanup(self._tools.stop)
        self._directory = tempfile.TemporaryDirectory()
        self.work = Path(self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _join(self, levels: list[float], family: str = "breeze-tts", **kwargs) -> Path:
        sections = [
            _speech(self.work / f"section_{index:02d}.wav", 1.2, amplitude)
            for index, amplitude in enumerate(levels)
        ]
        output = self.work / "joined.wav"
        generation._join_long_form_wavs(sections, output, family, pause_s=0.0, **kwargs)
        return output

    def test_sections_at_different_levels_come_out_at_the_same_level(self) -> None:
        # The defect, in one assertion: before the fix these four sections were 18 dB apart in
        # amplitude and stayed that way, which is the step the owner heard between paragraphs.
        output = self._join([0.05, 0.35, 0.08, 0.30])
        data, rate = _read_wav(output)
        quarter = len(data) // 4
        levels = [_measure_array(data[index * quarter:(index + 1) * quarter], rate)
                  for index in range(4)]
        self.assertLess(
            max(levels) - min(levels), 1.5,
            f"a long-form join still steps between sections: {[round(v, 2) for v in levels]} LUFS")

    def test_the_result_reaches_the_target_the_owner_approved(self) -> None:
        # −16 LUFS target, and the approved Breeze sample measured −17.50 LUFS / −1.42 dBTP. The
        # pass is the same one that made it, so the delivery is the same one he chose.
        output = self._join([0.05, 0.35, 0.08, 0.30, 0.12])
        integrated, true_peak = _measure(output)
        self.assertGreater(integrated, -20.0,
                           f"the levelled join is still quiet at {integrated:.2f} LUFS")
        self.assertLessEqual(true_peak, -1.0,
                             f"the levelled join peaked at {true_peak:.2f} dBTP, past the ceiling")

    def test_it_does_not_clip_a_section_that_holds_a_loud_transient(self) -> None:
        # The reason the ceiling is true peak rather than sample peak: this section is quiet overall
        # and holds one loud burst, so a gain chosen from loudness alone would clip it.
        output = self._join([0.03, 0.30, 0.30])
        data, _ = _read_wav(output)
        self.assertLess(float(np.max(np.abs(data))), 1.0,
                        "the levelled join clips — the peak ceiling is not being honoured")

    def test_the_levelled_copies_are_not_left_behind(self) -> None:
        # They are working files for one join. Left behind, they are a second full copy of every
        # long-form render sitting in the output directory.
        self._join([0.05, 0.35, 0.08])
        leftovers = [p.name for p in self.work.iterdir() if ".levelled" in p.name]
        self.assertEqual([], leftovers, f"levelled working files survived the join: {leftovers}")

    def test_a_section_the_pass_empties_is_not_accepted(self) -> None:
        """`loudnorm` writes a **0-frame** file for a pathologically short input instead of failing.

        The first version of this accepted a levelled section on sample rate and channel count
        alone, so the section's audio was silently replaced by nothing. It was caught by an existing
        test in the voice suite — `test_qwen_clone_join_preserves_segment_audio_and_pause` joined two
        sections and got back only the pause, ten samples of silence where thirty were due — and
        this is the same defect pinned where the levelling lives.

        A ten-sample section is not a real render. It is here because it is the input that makes the
        pass produce an empty file, and an empty file must abandon levelling rather than delete
        audio.
        """
        sections = []
        for index in range(2):
            path = self.work / f"tiny{index}.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(1000)
                handle.writeframes(np.full(10, 8192 * (1 if index == 0 else -1),
                                           dtype="<i2").tobytes())
            sections.append(path)
        output = self.work / "joined.wav"
        generation._join_long_form_wavs(sections, output, "breeze-tts", pause_s=0.01)
        data, rate = _read_wav(output)
        # 10 + (0.01 s of pause) + 10 samples. The audio is still there: this is the assertion the
        # bug broke, and it is stated as a length rather than a level because what was lost was the
        # samples themselves.
        self.assertEqual(30, len(data),
                         "a section's audio was dropped — the levelling pass emptied it and the "
                         "join wrote only the pause")
        self.assertFalse(any(".levelled" in p.name for p in self.work.iterdir()),
                         "the emptied levelled file was left behind")

    def test_a_single_section_is_not_re_levelled(self) -> None:
        # Nothing to match it against, and re-levelling it would change the delivery for no
        # measured reason. Asserted by the copy count, which is what re-levelling would create.
        sections = [_speech(self.work / "only.wav", 1.0, 0.05)]
        output = self.work / "joined.wav"
        generation._join_long_form_wavs(sections, output, "breeze-tts", pause_s=0.0)
        self.assertFalse(any(".levelled" in p.name for p in self.work.iterdir()))
        self.assertTrue(output.is_file())

    def test_a_family_can_opt_out(self) -> None:
        # `target is None` means join the sections at the levels the engine returned. A family whose
        # engine already levels its own output states that instead of being re-levelled to fix
        # nothing, and the mechanism has to honour it or the declaration is decorative.
        from backend import catalog as voice_catalog
        original = voice_catalog.FAMILIES["breeze-tts"].section_loudness_target_lufs
        object.__setattr__(voice_catalog.FAMILIES["breeze-tts"],
                           "section_loudness_target_lufs", None)
        try:
            sections = [_speech(self.work / f"s{index}.wav", 1.0, amplitude)
                        for index, amplitude in enumerate([0.05, 0.35])]
            output = self.work / "joined.wav"
            generation._join_long_form_wavs(sections, output, "breeze-tts", pause_s=0.0)
            leftovers = [p.name for p in self.work.iterdir() if ".levelled" in p.name]
            self.assertEqual([], leftovers,
                             "a family that opted out of loudness matching was levelled anyway")
        finally:
            object.__setattr__(voice_catalog.FAMILIES["breeze-tts"],
                               "section_loudness_target_lufs", original)

    def test_without_ffmpeg_the_join_still_happens_and_says_so(self) -> None:
        # The fallback is the pre-fix behaviour, not a failure: a job that has already spent twenty
        # minutes generating must never be lost because a meter could not run.
        #
        # Asserted by pointing the resolver at an empty directory rather than by reloading
        # `backend.paths` — a module reload inside one test is process-wide state that outlives it,
        # which is the same class of leak the note beside VOICE_RUNTIME_BIN describes.
        import importlib
        from backend import paths
        empty = self.work / "no-tools-here"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"STUDIOHUB_TOOLS_DIR": str(empty)}):
            importlib.reload(paths)
            self.addCleanup(importlib.reload, paths)
            sections = [_speech(self.work / f"s{index}.wav", 1.0, amplitude)
                        for index, amplitude in enumerate([0.05, 0.35])]
            output = self.work / "joined.wav"
            generation._join_long_form_wavs(sections, output, "breeze-tts", pause_s=0.0)
            self.assertTrue(output.is_file(), "the join must still produce a file without FFmpeg")
            self.assertFalse(any(".levelled" in p.name for p in self.work.iterdir()))


class ThePolicyCarriesTheMeasurementTest(unittest.TestCase):
    """The numbers behind the pass, pinned where a reader finds them."""

    def test_the_parameters_are_the_ones_the_approved_sample_was_made_with(self) -> None:
        # The owner approved a file measuring −17.50 LUFS / −1.42 dBTP. Changing any of these three
        # changes the delivery he signed off, so they are asserted rather than left to drift.
        self.assertEqual(-16.0, long_form_policy.SECTION_LOUDNESS_TARGET_LUFS)
        self.assertEqual(-1.5, long_form_policy.SECTION_TRUE_PEAK_CEILING_DBTP)
        self.assertEqual(11.0, long_form_policy.SECTION_LOUDNESS_RANGE_LU)

    def test_the_reason_the_linear_answer_was_rejected_is_recorded(self) -> None:
        # The measurement that decided the implementation, kept beside the constants. Without it the
        # next person re-implements the linear gain and reintroduces the 4.3 LU step.
        source = (ROOT / "components" / "voice" / "app" / "backend"
                  / "long_form_policy.py").read_text(encoding="utf-8")
        self.assertIn("4.9 LU to 4.3 LU", source,
                      "the measurement that rejected the linear gain is no longer recorded")
        self.assertIn("1.5 LU", source, "the measured result of the chosen pass is not recorded")

    def test_every_shipped_family_declares_a_loudness_target(self) -> None:
        # Not "the target equals −16": a family may legitimately declare another one, or None to opt
        # out. What must not happen is the field being absent, which is how a family joins at
        # whatever level its engine produced and nobody can see that was a choice.
        from backend import catalog as voice_catalog
        for family_id, entry in voice_catalog.FAMILIES.items():
            with self.subTest(family=family_id):
                self.assertTrue(
                    hasattr(entry, "section_loudness_target_lufs"),
                    f"{family_id} does not state a section loudness target")
                self.assertTrue(
                    hasattr(entry, "section_true_peak_ceiling_dbtp"),
                    f"{family_id} does not state a true-peak ceiling")

    def test_the_joiner_reads_the_policy_rather_than_a_constant(self) -> None:
        # A family with its own target must actually get it. Asserted through `policy_for`, which is
        # what the joiner reads, so a second copy of the number in `generation.py` would fail here.
        from backend import catalog as voice_catalog
        original = voice_catalog.FAMILIES["breeze-tts"].section_loudness_target_lufs
        object.__setattr__(voice_catalog.FAMILIES["breeze-tts"],
                           "section_loudness_target_lufs", -20.0)
        try:
            policy = long_form_policy.policy_for("breeze-tts", "")
            self.assertEqual(-20.0, policy["section_loudness_target_lufs"],
                             "the joiner would not see a family's own loudness target")
        finally:
            object.__setattr__(voice_catalog.FAMILIES["breeze-tts"],
                               "section_loudness_target_lufs", original)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return data, rate


def _measure_array(data: np.ndarray, rate: int) -> float:
    """A crude level for a slice of the joined file, in dB — enough to compare four sections."""
    if data.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(data))))
    return 20.0 * np.log10(max(rms, 1e-9))


if __name__ == "__main__":
    unittest.main()
