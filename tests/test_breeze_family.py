"""Breeze TTS 2 is a shipped family, and it takes three forms through one engine call.

Breeze's `generate(text, voice, instruct, ref_audio, ref_text, …)` covers voice **design** (describe
it in words), **cloning** (a reference clip plus its transcript) and **direction** (how a line should
be read) — measured 2026-09-29 at 100% word match through a Whisper round trip for the first three,
and 93.8% for inline vocal events, where the only "missing" words were the two event tags themselves,
which the engine performed rather than read aloud.

The resolver is tested here rather than through a render because **the weights are not on this
machine**: the four distribution drives were removed, and `.build/tts-hf-cache` holds only dangling
symlinks onto a volume that is no longer mounted. So a real generation cannot be run locally, and
saying "it works" on the strength of an import would be exactly the claim this project keeps having
to correct. What can be checked is the contract: which kwargs each form produces, and that the one
input Breeze genuinely cannot work without is refused rather than faked.

**The transcript is that input.** Breeze builds its clone prompt from `f"{speaker}{ref_text}"` and
raises *"Breeze voice cloning requires ref_text with ref_audio"* without one. Pocket TTS takes the
same resolver shape and substitutes `"."` when a voice has no transcript; doing that here would not
degrade the clone, it would condition it on the wrong words. A voice with no transcript is refused
instead, naming the page that fixes it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
VOICE_APP = ROOT / "components" / "voice" / "app"
if str(VOICE_APP) not in sys.path:
    sys.path.insert(0, str(VOICE_APP))

from backend import catalog, generation, long_form_policy  # noqa: E402

CATALOG_PY = (ROOT / "components" / "voice" / "app" / "backend" / "catalog.py").read_text(
    encoding="utf-8")
GENERATION_PY = (ROOT / "components" / "voice" / "app" / "backend" / "generation.py").read_text(
    encoding="utf-8")


class _Library:
    def __init__(self, transcript: str | None) -> None:
        self._transcript = transcript

    def get(self, voice_id: str):
        return {"id": voice_id} if self._transcript is not None else None

    def reference_path(self, voice_id: str) -> Path:
        return ROOT / "qualification" / "evidence" / "st04-reference-clip.wav"

    def transcript(self, voice_id: str) -> str | None:
        return self._transcript


def _resolve(params: dict, transcript: str | None = "the reference words") -> tuple[dict, str]:
    manager = object.__new__(generation.GenerationManager)
    gen_kwargs: dict = {"text": "hello"}
    voices = SimpleNamespace(library=_Library(transcript))
    label = manager._mlx_kwargs_design_clone_or_direct(params, gen_kwargs, voices)
    return gen_kwargs, label


class TheThreeFormsTest(unittest.TestCase):
    def test_a_reference_clip_clones_and_carries_its_transcript(self) -> None:
        kwargs, label = _resolve({"voice_library_id": "a9aedc5c6bd3"})
        self.assertIn("ref_audio", kwargs, "a chosen voice must reach the engine as a reference")
        self.assertEqual("the reference words", kwargs["ref_text"],
                         "Breeze conditions its clone prompt on the transcript; without it the "
                         "clone is of the wrong words")
        self.assertIn("clone", label)

    def test_a_description_designs_a_voice(self) -> None:
        kwargs, label = _resolve({"voice_design_prompt": "deep, slow and warm"})
        self.assertEqual("deep, slow and warm", kwargs["instruct"])
        self.assertNotIn("ref_audio", kwargs)
        self.assertIn("design", label)

    def test_a_bare_engine_call_is_a_real_mode(self) -> None:
        # Measured in the qualification: design, clone and direction all work, and so does the
        # engine's own default voice. Refusing an empty form would remove a mode it was tested in.
        kwargs, label = _resolve({})
        self.assertEqual({}, {k: v for k, v in kwargs.items() if k != "text"})
        self.assertEqual("zero-shot", label)

    def test_design_and_direction_are_one_key_because_the_engine_has_one(self) -> None:
        # Breeze does not separate "design a voice" from "direct this line" — both go in `instruct`.
        # Inventing a distinction the engine does not make would be a control that does nothing.
        for key in ("voice_design_prompt", "instruct"):
            with self.subTest(key=key):
                kwargs, _ = _resolve({key: "read it like a letter"})
                self.assertEqual("read it like a letter", kwargs["instruct"])


class TheTranscriptIsRequiredTest(unittest.TestCase):
    def test_a_voice_with_no_transcript_is_refused_not_faked(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _resolve({"voice_library_id": "a9aedc5c6bd3"}, transcript="")
        message = str(caught.exception)
        self.assertIn("transcript", message, "the refusal must name the missing thing")
        self.assertIn("Voices", message, "the refusal must say where to fix it")

    def test_no_placeholder_is_substituted(self) -> None:
        # The specific defect this guards: Pocket TTS's resolver passes `fallback_transcript="."`,
        # and copying that shape here would send Breeze a one-character transcript instead of the
        # reference's words — a clone that is quietly conditioned on nonsense.
        breeze = GENERATION_PY[GENERATION_PY.index("def _mlx_kwargs_design_clone_or_direct"):]
        breeze = breeze[:breeze.index("\n    def _inject_voice_clone")]
        self.assertIn('fallback_transcript=""', breeze,
                      "Breeze is being given a placeholder transcript again")
        self.assertNotIn('fallback_transcript="."', breeze)


class TheFamilyIsRegisteredTest(unittest.TestCase):
    def test_the_owner_s_numbers_are_what_the_registry_serves(self) -> None:
        policy = long_form_policy.policy_for("breeze-tts", "mlx-community/Breeze-TTS-2-mlx-4bit")
        # Owner 2026-10-04: the uniform 1.0 s sentence join replaces Breeze's 0.6 s, as for every model.
        self.assertEqual(1.00, policy["join_pause_seconds"],
                         "the pause the owner chose by ear is not what the joiner reads")
        # Owner 2026-10-03: one 250-character section size for every voice model (Breeze was 500).
        self.assertEqual(long_form_policy.UNIFORM_SECTION_MAX_CHARACTERS,
                         policy["section_max_characters"])
        self.assertEqual(250, policy["section_max_characters"])
        self.assertEqual(-16.0, policy["section_loudness_target_lufs"])

    def test_the_joiner_finds_the_family_through_the_registry(self) -> None:
        # Not "the constant is 0.6" — that it arrives through `policy_for`, which is what the
        # joiner calls. A second copy of the number in `generation.py` would pass the test above.
        self.assertIn("_long_form_join_pause_s(model.family, model.repo)", GENERATION_PY)

    def test_the_model_is_in_the_catalogue_under_its_published_id(self) -> None:
        entry = catalog.get_model("mlx-community/Breeze-TTS-2-mlx-4bit")
        self.assertIsNotNone(entry, "the family is registered but no model uses it")
        assert entry is not None
        self.assertEqual("breeze-tts", entry.family)
        self.assertEqual(24000, entry.sample_rate_hz)
        self.assertIn("quantization", CATALOG_PY.lower(), "quantization must be named")

    def test_the_bit_width_is_stated_in_the_label(self) -> None:
        # The owner's rule: every mention of a model names its quantization. A label reading
        # "Breeze TTS 2 (MLX)" is a model that cannot be checked against what is on disk.
        entry = catalog.get_model("mlx-community/Breeze-TTS-2-mlx-4bit")
        assert entry is not None
        self.assertIn("4-bit", entry.label)

    def test_the_family_runs_on_the_shared_mlx_audio_engine(self) -> None:
        entry = catalog.FAMILIES["breeze-tts"]
        self.assertEqual(catalog.ENGINE_MLX_AUDIO, entry.engine)
        self.assertEqual("mlx-audio", generation.engine_for_family("breeze-tts"))
        self.assertEqual("design_clone_or_direct",
                         generation.MLX_AUDIO_FAMILIES["breeze-tts"]["mode"])


if __name__ == "__main__":
    unittest.main()
