"""
Static catalog of text-to-speech models for VoiceStudio (Mac).

Each entry describes a Hugging Face repo plus metadata the UI uses: download
size (AFTER per-repo filtering), gating status, hardware floor, capabilities
(tts / voice-cloning / multilingual / expressive / streaming), and a long-form
"best for" description.

Capability vocabulary:
- "tts"             — basic text-to-speech with default voice(s)
- "voice-cloning"   — clones a target voice from a reference audio clip
- "multilingual"    — supports more than English
- "expressive"      — emotion / style tags or control sliders
- "streaming"       — supports real-time / streaming generation (low-latency)
- "lyrics"          — can sing (rare in open-source; ACE-Step does it elsewhere)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from . import lane_contract, long_form_policy, model_audits, qwen_quality


@dataclass(frozen=True)
class TextGuidance:
    # Soft character threshold. None = effectively unlimited (sentence-chunked
    # under the hood). For "hard-cap" engines this is the cliff past which
    # output is known to degrade.
    soft_max_chars: Optional[int]
    # "unlimited"  — engine cleanly handles arbitrary length (Kokoro KPipeline)
    # "auto-split" — wrapper or engine splits long text by sentence and stitches
    # "hard-cap"   — fixed training-window past which output silences /
    #                hallucinates (Bark, Orpheus, XTTS). UI should soft-block.
    chunking: str
    # One-liner shown under the textarea + as the chip tooltip.
    note: str


# ───────────── family engines ─────────────
#
# The engine is the producer's own fact about a family, and it is the single
# source of truth every consumer reads:
#
#   * `serialize_model` labels the family "Apple MLX" vs "PyTorch / MPS" and the
#     frontend's "🍎 MLX only" filter keeps or drops it from the same value;
#   * `generation.py` derives `MLX_AUDIO_FAMILIES`, `_ENGINE_REQUIREMENTS` and
#     `_WIRED_FAMILIES` from it, so the dispatch table and the UI cannot
#     disagree about a family;
#   * the diagnostics panel reports the packages the engine needs.
#
# A previous version of this file kept a hand-written `_MLX_AUDIO_FAMILIES`
# tuple here and a second list in generation.py, with a comment asking the next
# author to keep them in sync. They drifted: `kittentts`, `vibevoice` and
# `omnivoice` were published as "PyTorch / MPS" and hidden by the MLX-only
# filter (CHANGELOG, 2026-09-24). There is no second list any more.
ENGINE_MLX_AUDIO = "mlx-audio"                    # mlx_audio.generate_audio
ENGINE_TRANSFORMERS_VITS = "transformers-vits"    # transformers VitsModel on torch
ENGINE_ONNX_VITS = "onnx-vits"                    # onnxruntime InferenceSession
# Engines whose models run on Apple's MLX framework. "Apple MLX" is what the
# Models tab calls them and what the MLX-only filter selects on.
APPLE_OPTIMIZED_ENGINES = frozenset({ENGINE_MLX_AUDIO})
ALL_ENGINES = frozenset({
    ENGINE_MLX_AUDIO, ENGINE_TRANSFORMERS_VITS, ENGINE_ONNX_VITS,
})


@dataclass(frozen=True)
class SectioningRefinement:
    """A checkpoint inside one family whose measured sectioning differs from the default.

    ``marker`` is matched against the repository's own name ("1.7b-base",
    "customvoice"). The refinement is declared with the family instead of being a
    repo-name branch in `long_form_policy`, so the number, the checkpoint it
    belongs to and the reason sit together - and a refinement for a model nobody
    ships reads as a fact about that checkpoint rather than an unreachable `if`.
    """
    marker: str
    section_max_characters: Optional[int] = None
    join_pause_seconds: Optional[float] = None
    note: str = ""


@dataclass(frozen=True)
class PresetRoster:
    """The ready-made voices one family's engine accepts by name.

    Three facts that only mean something together, so they live together:

    ``speakers``
        The roster itself - one mapping per voice, carrying the id the engine resolves and
        the metadata a picker renders.
    ``key``
        The name the runtime publishes it under (``<key>_preset_speakers``). A consumer joins
        a model to its family's roster by that key's shape, so the key is part of the data
        rather than a literal in a second producer.
    ``source``
        Where the ids came from - republished as the lane contract's ``note``, so a scheduler
        reads the same provenance a person does.

    Exactly two producers publish a roster: ``generation.availability`` for the app's picker
    and ``lane_contract`` for a scheduler. Both read this one object, so the two cannot
    disagree about which voices exist.
    """
    key: str
    speakers: tuple[Mapping[str, str], ...]
    source: str


@dataclass(frozen=True)
class Family:
    id: str
    label: str
    # Required, deliberately without a default: a new family must state which
    # engine runs it, and a missing statement is an import-time TypeError in
    # this file rather than a family that is silently published as non-MLX.
    engine: str
    # Packages this family's shipped engine imports. Published by
    # `generation.diagnostics()` and checked one-by-one by the health panel.
    requirements: tuple[str, ...]
    summary: str
    how_to_use: str
    text_guidance: TextGuidance
    # Required for the same reason as `engine`: a new family must state how its
    # long scripts are split, or say out loud that it takes the conservative
    # default. A family that omits it used to fall through to "the other
    # behaviour" with nobody able to see which one it got.
    section_max_characters: int
    join_pause_seconds: float = 0.12
    section_note: str = ""
    # "family_policy" (measured or engine-derived) or "conservative_default".
    section_origin: str = "family_policy"
    # The integrated loudness every rendered section is brought to before the long-form join, in
    # LUFS, or None to join the sections at exactly the level the engine returned them. Per family
    # because it is a delivery decision about that lane's output, and because an engine that already
    # levels its own sections should be able to say so rather than be re-levelled to fix nothing.
    # The defaults are the policy's own constants rather than literals repeated here: the joiner
    # reads this entry, so a second copy of the number is a second number that can disagree.
    section_loudness_target_lufs: Optional[float] = (
        long_form_policy.SECTION_LOUDNESS_TARGET_LUFS)
    # The true-peak ceiling the levelling pass may not cross.
    section_true_peak_ceiling_dbtp: float = long_form_policy.SECTION_TRUE_PEAK_CEILING_DBTP
    # The loudness range the pass may leave inside one section.
    section_loudness_range_lu: float = long_form_policy.SECTION_LOUDNESS_RANGE_LU
    # The engine's own input limit where it has one: past this the engine stops
    # working rather than degrading, so no audit or caller may raise the budget.
    section_hard_cap_characters: Optional[int] = None
    section_refinements: tuple[SectioningRefinement, ...] = ()
    # The extra request fields this family's engine resolver actually reads
    # (beyond `text` and `speed`, which every family honours). Published by the
    # lane contract, so a scheduler never sends a key the lane silently drops.
    lane_parameters: tuple[str, ...] = ()
    # The ready-made voices this family publishes, when it publishes a roster at
    # all. None means it does not: a family with one built-in speaker (the Khmer
    # VITS voices) has nothing to choose between, and a family whose preset roster
    # was deliberately skipped (Pocket TTS 4-bit, the owner's decision in
    # docs/tasks/ST-20.md) publishes none either. Both the availability payload a
    # picker reads and the lane contract a scheduler reads come from this field,
    # so the two cannot disagree about which voices exist.
    preset_roster: Optional[PresetRoster] = None
    # Behaviour flags that used to be per-family sets in consumers. They are
    # facts about the engine, so they live with the rest of the family's facts.
    #
    # The engine does no number normalisation of its own, so raw digits reach
    # the tokenizer and are mispronounced (OmniVoice: measured 2026-08-07).
    numbers_need_expansion: bool = False
    # The engine scales its own predicted durations from a speed factor, so the
    # public speed control goes in as part of synthesis. Every other engine
    # applies no multiplier and gets one pitch-preserving tempo pass on the
    # finished artifact instead (long_form_policy.speed_policy_for).
    native_speed: bool = False


# The exact tag set mlx-audio's OmniVoice treats as atomic non-verbal tokens
# (mlx_audio/tts/models/omnivoice/omnivoice.py:14, _NONVERBAL_PATTERN). Anything
# outside this list is NOT a tag: it falls through to ordinary tokenization and
# is rendered as noise. The catalog previously advertised "[cough]", which is not
# in the pattern — owner listening on 2026-08-07 confirmed it renders badly.
OMNIVOICE_NONVERBAL_TAGS: tuple[str, ...] = (
    "laughter", "sigh", "confirmation-en",
    "question-en", "question-ah", "question-oh", "question-ei", "question-yi",
    "surprise-ah", "surprise-oh", "surprise-wa", "surprise-yo",
    "dissatisfaction-hnn",
)


# ───────────── the published preset rosters ─────────────

# SOURCE OF TRUTH: the CustomVoice model's own config.json `spk_id` map. The installed
# mlx-community/Qwen3-TTS-12Hz-*-CustomVoice models ship EXACTLY these 9 speakers (verified
# against config.json):
#     aiden, dylan, eric, ono_anna, ryan, serena, sohee, uncle_fu, vivian
# mlx-audio matches speaker names case-insensitively and lowercases them before the spk_id
# lookup (qwen3_tts.py: `config.spk_id[speaker.lower()]`), so the capitalised display ids
# below resolve correctly.
#
# DO NOT add speakers that aren't in the model's spk_id map. Earlier this list carried two
# phantom speakers ("Ethan", "Chelsie") copied from an older Qwen roster — picking them passed
# the app's own validation but mlx-audio rejected them with `ValueError: Speaker 'Ethan' not
# supported`, which surfaced only as the generic "mlx-audio didn't produce a wav file" wall.
# Removed in v1.4.4.
#
# This lives here, with the family that publishes it, because two consumers read it and a
# second copy is free to disagree with the other: `generation.availability` publishes it as
# `qwen3_preset_speakers` for the app's picker, and `lane_contract` publishes its ids as
# `preset_speaker`'s enum for a scheduler.
QWEN3_PRESET_SPEAKERS: tuple[Mapping[str, str], ...] = (
    {"id": "Ryan",     "lang": "en", "gender": "m", "description": "Dynamic male, strong rhythmic drive"},
    {"id": "Aiden",    "lang": "en", "gender": "m", "description": "Sunny American male, clear midrange"},
    {"id": "Serena",   "lang": "en", "gender": "f", "description": "Warm, gentle young female"},
    {"id": "Vivian",   "lang": "en", "gender": "f", "description": "Bright, slightly edgy young female"},
    {"id": "Uncle_Fu", "lang": "zh", "gender": "m", "description": "Seasoned male, low mellow timbre"},
    {"id": "Dylan",    "lang": "zh", "gender": "m", "description": "Youthful Beijing male, clear natural timbre"},
    {"id": "Eric",     "lang": "zh", "gender": "m", "description": "Lively Chengdu male, slightly husky"},
    {"id": "Ono_Anna", "lang": "ja", "gender": "f", "description": "Playful Japanese female, light timbre"},
    {"id": "Sohee",    "lang": "ko", "gender": "f", "description": "Warm Korean female, rich emotion"},
)

# The key stays the legacy `qwen3` prefix rather than the family id: the native app joins a
# roster to its family by the key's shape (`<family>_preset_speakers`, matched on the
# normalised prefix), and the web picker reads `qwen3_preset_speakers` by that exact name, so
# renaming it to `qwen3_tts` would empty the picker it was named for.
QWEN3_PRESET_ROSTER = PresetRoster(
    key="qwen3",
    speakers=QWEN3_PRESET_SPEAKERS,
    source="the Qwen3-TTS CustomVoice checkpoint's own config.json spk_id map",
)


FAMILIES: dict[str, Family] = {
    "voxcpm-mlx": Family(
        id="voxcpm-mlx",
        label="VoxCPM2 (MLX)",
        engine=ENGINE_MLX_AUDIO,
        requirements=("mlx", "mlx_audio", "soundfile", "numpy"),
        section_max_characters=long_form_policy.VOXCPM_SECTION_MAX_CHARACTERS,
        section_note=(
            "Keeps each synthesis pass inside the practical voice-fidelity window "
            "while preserving the selected clone controls."
        ),
        lane_parameters=("voice_design_prompt", "instruct", "ref_transcript"),
        summary=(
            "OpenBMB's VoxCPM2 ported to MLX for native Apple Silicon inference "
            "(via Prince Canuma's mlx-audio). 2B parameters, 30 languages, 48 kHz "
            "studio-quality output, faster-than-realtime on M-series chips. "
            "Combines voice cloning + voice design + zero-shot into one model. "
            "This is the current VoxCPM architecture and replaces the older "
            "PyTorch v1 and duplicate v2 entries."
        ),
        how_to_use=(
            "Three modes activate based on what you fill in: leave both fields "
            "blank → zero-shot with the default voice; type a natural-language "
            "voice description → voice design; pick a reference voice from your "
            "library → voice cloning. A saved transcript automatically enables "
            "the highest-fidelity continuation-cloning path. You can also combine "
            "the description + library voice for controlled cloning."
        ),
        # VoxCPM publishes audio patch/context limits, not a word or character
        # limit. Real clone fidelity can drift after roughly 30 seconds, so Voice
        # Studio independently renders sentence-safe ~400-character sections and
        # reuses the original reference for every section.
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts auto-split into sentence-safe ~400-character sections. Every section reuses the original reference, then Voice Studio verifies and joins the audio.",
        ),
    ),
    "qwen3-tts": Family(
        id="qwen3-tts",
        label="Qwen3-TTS (MLX)",
        engine=ENGINE_MLX_AUDIO,
        requirements=("mlx", "mlx_audio", "soundfile", "numpy"),
        section_max_characters=long_form_policy.QWEN_CLONE_SECTION_MAX_CHARACTERS,
        join_pause_seconds=long_form_policy.QWEN_CLONE_JOIN_PAUSE_SECONDS,
        section_note=(
            "Clone continuity setting. Every section reuses the same reference voice "
            "and transcript evidence, with a fixed 180 ms join."
        ),
        # `max_output_duration_s` is honoured by this family alone: the dispatcher turns it into
        # Qwen's own audio-token ceiling (`generation._qwen_max_tokens_for_duration`, for the Base
        # clone and the other Qwen3-TTS tiers) and the quality path enforces it on the finished WAV.
        # It is declared here because a lane must publish what it accepts - the shape existed in
        # `lane_contract._PARAMETER_SHAPES` with no family naming it, so no contract offered it and
        # a scheduler could not discover a ceiling the worker was enforcing. No other family
        # declares it, which is right: they accept the field and ignore it, and a lane must not
        # advertise a key it throws away.
        lane_parameters=("ref_transcript", "max_output_duration_s"),
        # The roster a preset tier of this family chooses from: `qwen3_preset_speakers` to the
        # app's picker, `preset_speaker`'s enum to a scheduler. Base tiers offer no preset and
        # publish neither; see `lane_contract._preset_speaker_parameter`.
        preset_roster=QWEN3_PRESET_ROSTER,
        section_refinements=(
            # The production candidate the fleet measured: 300 ms sentence joins
            # (the audit records 300/600/180 ms for it), same section budget.
            SectioningRefinement(
                marker="1.7b-base",
                join_pause_seconds=long_form_policy.OMNIVOICE_JOIN_PAUSE_SECONDS,
                note=(
                    "The 1.7B production candidate runtime uses 300 ms sentence, "
                    "600 ms paragraph and 180 ms soft joins."
                ),
            ),
            # Preset and voice-design tiers never use reference-audio cloning, so
            # they keep the wider preset budget and the shared 120 ms join.
            SectioningRefinement(
                marker="customvoice",
                section_max_characters=long_form_policy.QWEN_PRESET_SECTION_MAX_CHARACTERS,
                join_pause_seconds=long_form_policy.DEFAULT_JOIN_PAUSE_SECONDS,
                note=(
                    "Preset and voice-design delivery setting; these paths do not use "
                    "reference-audio cloning."
                ),
            ),
            SectioningRefinement(
                marker="voicedesign",
                section_max_characters=long_form_policy.QWEN_PRESET_SECTION_MAX_CHARACTERS,
                join_pause_seconds=long_form_policy.DEFAULT_JOIN_PAUSE_SECONDS,
                note=(
                    "Preset and voice-design delivery setting; these paths do not use "
                    "reference-audio cloning."
                ),
            ),
        ),
        summary=(
            "Alibaba's Qwen3-TTS, quantized to 8-bit and ported to MLX for native "
            "Apple Silicon inference (via mlx-audio). Base models clone a reference "
            "voice, CustomVoice provides named speakers with emotion control, and "
            "VoiceDesign creates a voice from a description. Apache-2.0."
        ),
        how_to_use=(
            "Pick a Base model to clone a voice from a short reference clip. "
            "CustomVoice uses built-in preset speakers; it does not clone. "
            "VoiceDesign takes a natural-language prompt like "
            "'deep male voice, slow, contemplative' and synthesizes a matching "
            "voice without needing audio. Use 0.6B Base for speed or 1.7B Base "
            "for the strongest cloning quality."
        ),
        # Qwen's ICL/Base clone path bypasses mlx-audio's newline splitter. Voice
        # Studio therefore renders a long clone in short sentence-aware sections
        # and joins them with clean pauses before returning one chapter audio file.
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long voice-clone scripts are split into short sentence-safe sections, rendered with the same reference, and joined with relaxed narration pauses into one chapter. No manual splitting needed.",
        ),
    ),
    "chatterbox-mlx": Family(
        id="chatterbox-mlx",
        label="Chatterbox (MLX)",
        engine=ENGINE_MLX_AUDIO,
        requirements=("mlx", "mlx_audio", "soundfile", "numpy"),
        section_max_characters=long_form_policy.CHATTERBOX_STANDARD_SECTION_MAX_CHARACTERS,
        section_note=(
            "Standard Chatterbox delivery setting. The same reference voice and "
            "expression controls are reused for every section."
        ),
        lane_parameters=("cfg_value",),
        summary=(
            "Resemble AI's Chatterbox ported to MLX for native Apple Silicon "
            "inference (via mlx-audio). Same MIT-licensed voice-cloning + "
            "expressive-intensity dial as the PyTorch original, runs on lower "
            "memory budgets thanks to MLX quantization."
        ),
        how_to_use=(
            "Pick a reference voice from your Voices library and provide text. "
            "Tune the exaggeration slider for more or less expressive delivery "
            "(values above 0.7 get dramatic fast). Recommended over PyTorch "
            "Chatterbox on M-series Macs — same quality, smaller memory budget."
        ),
        # Resemble publishes a 1,000 generated-speech-token default, not a word
        # cap. Voice Studio converts that to conservative independent synthesis
        # windows: 500 characters for standard, 400 for Turbo.
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts auto-split at sentence boundaries (about 500 chars standard / 400 Turbo), then Voice Studio verifies and joins every section.",
        ),
    ),
    "omnivoice": Family(
        id="omnivoice",
        label="OmniVoice (MLX)",
        engine=ENGINE_MLX_AUDIO,
        # v0.4.6 OmniVoice is MLX + Transformers; no separate omnivoice package
        # is required by the tracked generation environment.
        requirements=("mlx", "mlx_audio", "transformers", "soundfile", "numpy"),
        section_max_characters=long_form_policy.OMNIVOICE_SECTION_MAX_CHARACTERS,
        join_pause_seconds=long_form_policy.OMNIVOICE_JOIN_PAUSE_SECONDS,
        section_note=(
            "Conservative cloning setting using the same reference for every section, "
            "with 300 ms sentence joins, 600 ms paragraph joins, and 180 ms soft joins. "
            "Fleet measurement puts OmniVoice's memory ceiling near 1286 characters even "
            "on 8 GB; this budget is retained until the long-section quality gate has run."
        ),
        lane_parameters=("voice_design_prompt", "normalize_text"),
        # Measured on OmniVoice 2026-08-07: "1,200" -> "one two hundred",
        # "12,500" -> "one million two hundred thousand five hundred",
        # "$1,450.75" -> nonsense. 5 of 8 digit forms were wrong, while every
        # fully spelled-out form was correct: the engine has no normaliser.
        numbers_need_expansion=True,
        summary=(
            "k2-fsa's OmniVoice — a 0.6B-parameter diffusion language-model TTS "
            "supporting 600+ languages with voice design (gender / accent / age "
            "/ whisper via natural-language) and a fixed set of non-verbal "
            "symbols. The current mlx-audio backend supports both voice design "
            "and zero-shot cloning from a 3–10 second clip. Apache-2.0."
        ),
        how_to_use=(
            "Choose a reference voice to clone it, enter voice traits to design a "
            "new voice, or combine both to steer the cloned delivery. The bf16 "
            "checkpoint is the supported MLX option; allow extra memory headroom "
            "for longer or higher-step generations. Only these non-verbal tags "
            "are recognised: " + ", ".join(f"[{t}]" for t in OMNIVOICE_NONVERBAL_TAGS)
            + ". Anything else — [cough], [breath], [gasp] — is not a tag to this "
            "model: it falls through to ordinary text and renders as noise. "
            "Numbers are expanded to words before synthesis because the model has "
            "no normaliser of its own and mispronounces raw digits."
        ),
        # Audit (v1.2.4): mlx_audio omnivoice/omnivoice.py:483 — flow-matching (diffusion).
        # Takes explicit duration_s or estimates via RuleDurationEstimator. No GPT-style cliff.
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts are split privately into short sentence-safe sections using the same reference voice, then joined into one final file.",
        ),
    ),
    "fish-audio-mlx": Family(
        id="fish-audio-mlx",
        label="Fish Audio S2 Pro (MLX)",
        engine=ENGINE_MLX_AUDIO,
        requirements=("mlx", "mlx_lm", "mlx_audio", "transformers", "soundfile", "numpy"),
        section_max_characters=long_form_policy.FISH_AUDIO_SECTION_MAX_CHARACTERS,
        section_note=(
            "Voice Studio owns the outer delivery boundary so clone and style controls "
            "remain identical across the final joined artifact."
        ),
        lane_parameters=("instruct",),
        summary=(
            "Fish Audio's S2 Pro 5B multilingual TTS running through the MLX-Audio "
            "v0.4.6 backend. It supports zero-shot voice cloning from a reference "
            "clip plus expressive instruction prompts and includes its own codec."
        ),
        how_to_use=(
            "Choose a Fish S2 Pro checkpoint, optionally select a reference voice, "
            "and describe the delivery in the style field. Voice Studio privately "
            "splits long scripts at safe boundaries, joins the sections, and applies "
            "the requested final speed once."
        ),
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts are split privately at sentence-safe ~300-character sections, rendered with the same clone/style controls, joined, and tempo-adjusted once.",
        ),
    ),
    "khmer-tts-mms": Family(
        id="khmer-tts-mms",
        label="Khmer TTS (MMS VITS)",
        engine=ENGINE_TRANSFORMERS_VITS,
        requirements=("torch", "transformers", "soundfile", "numpy"),
        # No section-size measurement of its own yet: the conservative default,
        # which is the smallest budget any measured family uses.
        section_max_characters=long_form_policy.CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS,
        section_origin="conservative_default",
        section_note=(
            "This family has no section-size measurement of its own yet, so Voice Studio "
            "renders conservative 280-character sections instead of sending a whole script "
            "to an engine whose input limit is unknown."
        ),
        summary=(
            "A Khmer text-to-speech model fine-tuned from Meta's Massively "
            "Multilingual Speech (MMS) Khmer VITS checkpoint, running offline on "
            "PyTorch/Transformers — not MLX. Compact (83M parameters) and "
            "single-speaker at 16 kHz. Khmer only."
        ),
        how_to_use=(
            "Type or paste Khmer text and generate. This family has one built-in "
            "speaker and no reference clip, so leave the voice and reference "
            "fields empty. Long scripts are split privately into sentence-safe "
            "sections and joined into one final file."
        ),
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts are split privately into conservative sentence-safe sections and joined into one final file. One built-in speaker; no reference clip is used.",
        ),
    ),
    "khmer-tts-onnx": Family(
        id="khmer-tts-onnx",
        label="Khmer TTS Female (VITS/ONNX)",
        engine=ENGINE_ONNX_VITS,
        requirements=("onnxruntime", "soundfile", "numpy"),
        section_max_characters=long_form_policy.CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS,
        section_origin="conservative_default",
        section_note=(
            "This family has no section-size measurement of its own yet, so Voice Studio "
            "renders conservative 280-character sections instead of sending a whole script "
            "to an engine whose input limit is unknown."
        ),
        summary=(
            "A single-speaker female Khmer VITS voice trained on the "
            "DDD-Cambodia read-speech corpus and exported to ONNX. Runs fully "
            "offline through ONNX Runtime at 22.05 kHz. Khmer only, no cloning."
        ),
        how_to_use=(
            "Type or paste Khmer text and generate. One built-in female speaker, "
            "no reference clip. Long scripts are split privately into "
            "sentence-safe sections and joined into one final file."
        ),
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts are split privately into conservative sentence-safe sections and joined into one final file. One built-in female speaker; no reference clip is used.",
        ),
    ),
    "pocket-tts": Family(
        id="pocket-tts",
        label="Pocket TTS (MLX)",
        engine=ENGINE_MLX_AUDIO,
        # mlx-audio is the only inference dependency; the sentencepiece
        # tokenizer and preset voice prompts ship as packaged companions
        # (catalog.FAMILY_COMPANIONS), not Python packages.
        requirements=("mlx", "mlx_audio", "soundfile", "numpy"),
        lane_parameters=("voice",),
        section_max_characters=long_form_policy.CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS,
        section_origin="conservative_default",
        section_note=(
            "This family has no section-size measurement of its own yet, so Voice Studio "
            "renders conservative 280-character sections instead of sending a whole script "
            "to an engine whose input limit is unknown."
        ),
        summary=(
            "Kyutai's Pocket TTS — a compact 117M-parameter English text-to-speech "
            "model ported to MLX for native Apple Silicon inference (via mlx-audio) "
            "and quantized to 4-bit. It clones a voice from a short reference clip, "
            "or speaks with one of eight built-in preset voices when no reference is "
            "given. 24 kHz output. English only."
        ),
        how_to_use=(
            "Pick a reference voice from your Voices library and Pocket TTS clones "
            "it; a private reference clip works the same way. Leave the voice and "
            "reference fields empty and it uses its built-in preset voice instead. "
            "Long scripts are split privately into sentence-safe sections, rendered "
            "with the same reference or preset, and joined into one final file."
        ),
        # Pocket TTS is an autoregressive flow-LM over mimi latents. Its own
        # wrapper does not split long text, so Voice Studio owns the section
        # boundary; every section reuses the same reference prompt so the voice
        # cannot drift between sections.
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts are split privately into conservative sentence-safe sections, rendered with the same reference or preset voice, and joined into one final file.",
        ),
    ),
    "breeze-tts": Family(
        id="breeze-tts",
        label="Breeze TTS 2 (MLX 4-bit)",
        engine=ENGINE_MLX_AUDIO,
        # mlx-audio is the only inference dependency and it is a hard floor, not a preference:
        # `breeze_tts` first ships in mlx-audio 0.5.7, so a runtime on 0.4.7 has no such module at
        # all. `generation.diagnostics()` reads this tuple, which is what turns "the model is
        # installed" into "this Mac cannot run it on this runtime" instead of an ImportError inside
        # a job.
        requirements=("mlx", "mlx_audio", "soundfile", "numpy"),
        section_max_characters=long_form_policy.BREEZE_TTS_SECTION_MAX_CHARACTERS,
        join_pause_seconds=long_form_policy.BREEZE_TTS_JOIN_PAUSE_SECONDS,
        section_note=(
            "Breeze segments itself, one section per paragraph, and was measured doing it: 14 "
            "sections of 325 to 575 characters from a 1,227-word script, every one a verbatim "
            "continuation in order. Sections are joined at 600 ms, the pause the owner chose by ear "
            "from three cuts of the same render, and levelled to one loudness before the join."
        ),
        lane_parameters=("voice_design_prompt", "normalize_text"),
        summary=(
            "A 4-bit MLX text-to-speech model that designs a voice from a written description, "
            "clones one from a reference clip and its transcript, or takes a direction for how a "
            "line should be read — including inline vocal events such as (sigh) and (laugh), which "
            "it performs rather than reads aloud. 24 kHz mono output. English and Chinese."
        ),
        how_to_use=(
            "Describe the voice you want and it designs one, or pick a reference voice from your "
            "Voices library to clone it, or write how the line should be read. Long scripts are "
            "split privately into paragraph-sized sections, rendered with the same voice, levelled "
            "to one loudness and joined into a single file at a 600 ms pause."
        ),
        text_guidance=TextGuidance(
            soft_max_chars=None,
            chunking="auto-split",
            note="Long scripts are split privately into paragraph-sized sections (about 500 characters), rendered with the same voice or design, matched to one loudness, and joined into one final file at a 600 ms pause.",
        ),
    ),
}


@dataclass(frozen=True)
class LanguageSupport:
    """Truthful language capability metadata for catalog and Studio Hub clients.

    ``codes`` is present only when it is a complete, actionable enumeration.
    Large upstream coverage claims use a count/lower-bound instead of a
    fabricated pseudo-language such as ``+70 more``.
    """
    input_selection: str = "none"  # none | required | optional
    enumeration_status: str = "exact"  # exact | claimed_count | claimed_lower_bound
    codes: tuple[str, ...] = ()
    claimed_count: Optional[int] = None
    claimed_lower_bound: Optional[int] = None
    runtime_enforced: bool = False


VOXCPM2_LANGUAGE_CODES = (
    "ar", "my", "zh", "da", "nl", "en", "fi", "fr", "de", "el",
    "he", "hi", "id", "it", "ja", "km", "ko", "lo", "ms", "no",
    "pl", "pt", "ru", "es", "sw", "sv", "tl", "th", "tr", "vi",
)

CHATTERBOX_LANGUAGE_CODES = (
    "ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi",
    "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv",
    "sw", "tr", "zh",
)

# Audio8 preview scope per its mlx-audio README: Cantonese, Chinese, Dutch,
# English, French, German, Italian, Japanese, Korean, Polish, Spanish. "yue"
# (ISO 639-3) is the standard code for Cantonese — there is no 639-1 code.
AUDIO8_LANGUAGE_CODES = (
    "yue", "zh", "nl", "en", "fr", "de", "it", "ja", "ko", "pl", "es",
)


@dataclass(frozen=True)
class SectionSizeControl:
    minimum: int
    maximum: int
    step: int
    default_custom: int
    runtime_default: int
    source: str

    def serialize(self) -> dict[str, int | str]:
        return {
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "default_custom": self.default_custom,
            "runtime_default": self.runtime_default,
            "source": self.source,
        }


@dataclass(frozen=True)
class ModelEntry:
    repo: str
    label: str
    family: str
    size_gb: float                       # approximate on-disk size AFTER filtering
    gated: bool
    # None means the model is intentionally listed without a claimed memory
    # floor while controlled qualification is still in progress.
    min_unified_memory_gb: Optional[int] = 8
    # The live-free-memory floor the Hub enforces before dispatching this model, where the owner
    # has approved one. `None` keeps the Hub's own default. The tier above is a *qualification*
    # tier and is deliberately never enforced; this is the one that stops a job starting into a
    # nearly-full Mac, so it is a different fact and is published under its own name.
    min_free_memory_gb: Optional[float] = None
    recommended_hardware: str = ""
    capabilities: tuple[str, ...] = ("tts",)
    best_for: str = ""
    sample_rate_hz: int = 24000           # most TTS engines output 22–24 kHz
    languages: tuple[str, ...] = ("en",)
    language_support: Optional[LanguageSupport] = None
    # huggingface_hub-style glob patterns of files to SKIP during download.
    # F5-TTS in particular ships 4–5 alternate checkpoints in one repo (different
    # training stages and vocoders) — keep just one. Chatterbox has multiple
    # t3_*.safetensors variants. See musicstudio's catalog for the prior art.
    ignore_patterns: tuple[str, ...] = ()
    # Structured per-model use cases — each entry is (kind, text) where kind is
    # one of "good" / "weak" / "avoid". The UI renders these as ✅ / ⚠️ / ❌
    # bullets so users can set realistic expectations BEFORE submitting.
    use_cases: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    section_size_control: SectionSizeControl | None = None
    # One recorded fact about the model's license, stated once and never as
    # advice. Empty means "not established in this file", which is the honest
    # value: an invented license name is worse than an absent one.
    license_name: str = ""
    license_source: str = ""


CATALOG: tuple[ModelEntry, ...] = (
    # ──────────── VoxCPM2 (MLX) ────────────
    # MLX ports of openbmb/VoxCPM2. 2B params, 30 languages, 48 kHz, one model
    # that does zero-shot + voice design + cloning. Inference via `mlx-audio`
    # — same library as Qwen3-TTS, so the worker shares load_model + generate.
    # Apache-2.0. 4-bit is the recommended pick (faster + smaller, minimal
    # quality loss per the MLX conversion benchmarks). 8-bit sits between 4-bit
    # and bf16; bf16 remains the final-render tier.
    ModelEntry(
        repo="mlx-community/VoxCPM2-4bit",
        label="VoxCPM2 4-bit (MLX) — recommended",
        family="voxcpm-mlx",
        size_gb=2.3,
        gated=False,
        # Measured on the fleet 2026-08-07, same section of text on both tiers:
        # 61.9 s on a 17.2 GB M2 (3.95x realtime) but 738.4 s on an 8.6 GB M2
        # (47.09x realtime) — twelve times slower for identical work. Peak was
        # 7.963 GB against 8.6 GB of total RAM, so the small machine spends the
        # run swapping. The output is correct either way (93% transcribe-back),
        # which is exactly why the old 8 GB floor looked fine on paper.
        min_unified_memory_gb=16,
        recommended_hardware="24 GB unified memory. Measured at 3.95x realtime on 16 GB; an 8 GB Mac swaps and drops to 47x realtime.",
        capabilities=("tts", "voice-cloning", "multilingual", "expressive"),
        license_name="Apache-2.0",
        license_source="components/voice/app/backend/catalog.py family note: 'Apache-2.0'",
        best_for="The recommended VoxCPM2 pick for most users. 4-bit quantized — fastest and smallest, with minimal quality loss vs bf16. 48 kHz studio-quality output, 30 languages, voice cloning + voice design in one model.",
        sample_rate_hz=48000,
        languages=VOXCPM2_LANGUAGE_CODES,
        language_support=LanguageSupport(
            input_selection="none",
            enumeration_status="exact",
            codes=VOXCPM2_LANGUAGE_CODES,
        ),
        use_cases=(
            ("good",  "Recommended starter for multilingual TTS on Apple Silicon"),
            ("good",  "48 kHz studio output — sharpest sample rate in the catalog"),
            ("good",  "Zero-shot mode (just type text) OR voice cloning from library"),
            ("good",  "Voice design from natural-language prompt ('elderly male, gravelly')"),
            ("weak",  "4-bit quantization can occasionally fumble on rare-word pronunciation"),
            ("avoid", "Final renders where you can't afford a quantization artifact — use bf16"),
        ),
    ),
    ModelEntry(
        repo="mlx-community/VoxCPM2-8bit",
        label="VoxCPM2 8-bit (MLX) — balanced",
        family="voxcpm-mlx",
        size_gb=3.2,
        gated=False,
        min_unified_memory_gb=16,
        recommended_hardware="16 GB unified memory or more.",
        capabilities=("tts", "voice-cloning", "multilingual", "expressive"),
        license_name="Apache-2.0",
        license_source="components/voice/app/backend/catalog.py family note: 'Apache-2.0'",
        best_for="Middle tier: richer than 4-bit with less quantization loss, lighter and faster than bf16. 48 kHz studio-quality output, 30 languages, voice cloning + voice design in one model.",
        sample_rate_hz=48000,
        languages=VOXCPM2_LANGUAGE_CODES,
        language_support=LanguageSupport(
            input_selection="none",
            enumeration_status="exact",
            codes=VOXCPM2_LANGUAGE_CODES,
        ),
        use_cases=(
            ("good",  "Balanced quality and speed when 4-bit artifacts are audible but bf16 is too slow"),
            ("good",  "48 kHz studio output; same cloning and voice-design contract as the other tiers"),
            ("weak",  "No distinct workflow versus 4-bit and bf16 - it sits between them"),
            ("avoid", "8 GB Macs — use the 4-bit tier there"),
        ),
    ),
    ModelEntry(
        repo="mlx-community/VoxCPM2-bf16",
        label="VoxCPM2 bf16 (MLX) — full precision",
        family="voxcpm-mlx",
        size_gb=5.0,
        gated=False,
        # Raised from 12 with its 4-bit sibling: that variant was measured at
        # 7.963 GB peak and needs 16 GB, and this checkpoint carries 2.2x the
        # weights, so it cannot plausibly need less. 16 is the floor this is
        # known not to run below; the true figure is unmeasured and may be higher.
        min_unified_memory_gb=16,
        recommended_hardware="M1 Pro / M2 16 GB recommended for headroom.",
        capabilities=("tts", "voice-cloning", "multilingual", "expressive"),
        license_name="Apache-2.0",
        license_source="components/voice/app/backend/catalog.py family note: 'Apache-2.0'",
        best_for="Full bf16 precision — slower (RTF ~0.5×) but the reference quality. Pick when you're doing final renders and quality matters more than speed.",
        sample_rate_hz=48000,
        languages=VOXCPM2_LANGUAGE_CODES,
        language_support=LanguageSupport(
            input_selection="none",
            enumeration_status="exact",
            codes=VOXCPM2_LANGUAGE_CODES,
        ),
        use_cases=(
            ("good",  "Reference quality — the bf16 weights are openbmb's published model"),
            ("good",  "Final renders, audiobook production, anything where artifacts can't slip through"),
            ("weak",  "Slowest VoxCPM2 variant — RTF ~0.5× (half realtime, so 2 min audio = 4 min compute)"),
            ("avoid", "8 GB Macs — tight memory will swap and slow generations further"),
            ("avoid", "Quick iteration — use 4-bit for the prompt scouting, then upgrade to bf16 for finals"),
        ),
    ),

    # ──────────── Qwen3-TTS (MLX) ────────────
    # Four focused variants come pre-quantized to 8-bit by mlx-community for native
    # Apple Silicon inference. Inference is via the `mlx-audio` package (not
    # transformers/diffusers), so the worker is a separate code path. Apache-2.0.
    ModelEntry(
        repo="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        label="Qwen3-TTS 0.6B Base",
        family="qwen3-tts",
        size_gb=1.9,
        gated=False,
        min_unified_memory_gb=8,
        # Owner-approved production floor for the 8 GB voice-cloning tier: this model reserves
        # 3.2 GB of live free memory. Measured cold requirement is 3.15 GB, so 3.2 is the
        # rounded-up guard the owner accepted. It used to live in the Hub
        # (`workload_policy._HUB_PRODUCTION_FREE_GB`, keyed by this exact repo id) because this
        # row published no floor; a model-keyed table in a consumer is the defect this release
        # removes, so the fact lives with the model that has it.
        min_free_memory_gb=3.2,
        recommended_hardware=(
            "Apple Silicon with 8 GB unified memory minimum; 16 GB preferred "
            "for normal memory pressure and faster generation."
        ),
        # Base model handles voice CLONING from a reference audio clip — pair
        # with a voice from the Voices library. Not a "plain TTS" model.
        capabilities=("tts", "voice-cloning", "multilingual"),
        license_name="Apache-2.0",
        license_source="components/voice/app/backend/catalog.py family note: 'Apache-2.0'",
        best_for="Fast, memory-friendly voice cloning. Pair it with a reference clip from the Voices library; use the 1.7B Base model when likeness and prosody matter more than speed.",
        sample_rate_hz=24000,
        languages=("en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"),
        use_cases=(
            ("good",  "Qualified transcript-assisted voice cloning on 8 GB and larger Macs"),
            ("good",  "Multilingual cloning across the ten upstream-supported languages"),
            ("good",  "MLX-native — no PyTorch install needed"),
            ("weak",  "Less prosodic nuance than the 1.7B tier — voice character may sound flatter"),
        ),
    ),
    # 4-bit Base pair: half the weight bytes of the 8-bit builds, so the larger model
    # stays practical on a small Mac. Quantization is lossy, so these are offered as a
    # memory trade rather than a default - the 8-bit and bf16 tiers remain the quality
    # references, and this build's own audio has not been measured here yet.
    ModelEntry(
        repo="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit",
        label="Qwen3-TTS 1.7B Base — quality voice cloning (MLX 4-bit)",
        family="qwen3-tts",
        size_gb=2.3,
        gated=False,
        min_unified_memory_gb=12,
        recommended_hardware=(
            "Apple Silicon with 12 GB or more. The larger cloning checkpoint at "
            "roughly the weight size of the small 8-bit build."
        ),
        capabilities=("tts", "voice-cloning", "multilingual"),
        license_name="Apache-2.0",
        license_source="components/voice/app/backend/catalog.py family note: 'Apache-2.0'",
        best_for=(
            "The larger Base checkpoint's speaker likeness and phrasing with the "
            "memory footprint of the smaller 8-bit model — the middle rung between "
            "the 0.6B builds and the full-quality 1.7B 8-bit."
        ),
        sample_rate_hz=24000,
        languages=("en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"),
        use_cases=(
            ("good",  "1.7B cloning quality at 2.3 GB of weights"),
            ("good",  "A practical middle tier for a 16 GB Mac sharing memory with Image or Subtitle"),
            ("weak",  "4-bit quantization: expect slightly less fidelity than the 1.7B 8-bit build"),
            ("avoid", "The very smallest Macs — use the 0.6B Base 4-bit there"),
        ),
    ),

    # ──────────── Chatterbox (MLX) ────────────
    # Resemble AI's Chatterbox ported to MLX. Voice cloning + expressive-
    # intensity dial. Pair with a Voices library reference clip.
    ModelEntry(
        repo="mlx-community/chatterbox-4bit",
        label="Chatterbox 4-bit (MLX, 2026 build) — recommended",
        family="chatterbox-mlx",
        size_gb=0.6,
        gated=False,
        min_unified_memory_gb=8,
        recommended_hardware="Any Apple Silicon Mac with 8 GB.",
        capabilities=("tts", "voice-cloning", "expressive", "multilingual"),
        license_name="MIT",
        license_source="components/voice/app/backend/catalog.py family note: 'MIT-licensed'",
        best_for="Compact 4-bit MLX conversion of ResembleAI/chatterbox (~600 MB on disk), reporting 23-language support. The recommended Chatterbox pick.",
        sample_rate_hz=24000,
        languages=CHATTERBOX_LANGUAGE_CODES,
        language_support=LanguageSupport(
            input_selection="required",
            enumeration_status="exact",
            codes=CHATTERBOX_LANGUAGE_CODES,
            runtime_enforced=True,
        ),
        use_cases=(
            ("good",  "Compact MLX conversion of ResembleAI/chatterbox (~600 MB on disk)"),
            ("good",  "23 languages — broader than the old build"),
            ("good",  "Same voice cloning + exaggeration knob"),
            ("weak",  "Same caveat: reference voice required, exaggeration > 0.7 gets glitchy"),
        ),
    ),
    ModelEntry(
        repo="mlx-community/chatterbox-8bit",
        label="Chatterbox 8-bit (MLX, 2026 build)",
        family="chatterbox-mlx",
        size_gb=1.0,
        gated=False,
        min_unified_memory_gb=8,
        recommended_hardware="Any Apple Silicon Mac with 8 GB.",
        capabilities=("tts", "voice-cloning", "expressive", "multilingual"),
        license_name="MIT",
        license_source="components/voice/app/backend/catalog.py family note: 'MIT-licensed'",
        best_for="Higher-precision newer Chatterbox. Pick if 4-bit shows artifacts on your reference voice.",
        sample_rate_hz=24000,
        languages=CHATTERBOX_LANGUAGE_CODES,
        language_support=LanguageSupport(
            input_selection="required",
            enumeration_status="exact",
            codes=CHATTERBOX_LANGUAGE_CODES,
            runtime_enforced=True,
        ),
        use_cases=(
            ("good",  "Newer 8-bit conversion, 23 languages"),
            ("good",  "Voice cloning + exaggeration dial"),
        ),
    ),

    # ──────────── OmniVoice (MLX) ────────────
    # k2-fsa's OmniVoice through mlx-audio. The published 4-bit and 8-bit
    # conversions use a custom row-wise scale layout that the v0.4.6 generic
    # loader cannot load. Keep the compatible bfloat16 checkpoint visible.
    ModelEntry(
        repo="mlx-community/OmniVoice-bfloat16",
        label="OmniVoice 0.6B bf16 (MLX) — recommended",
        family="omnivoice",
        size_gb=2.0,
        gated=False,
        min_unified_memory_gb=8,
        recommended_hardware="M1 Pro / M2 16 GB recommended.",
        capabilities=("tts", "voice-cloning", "multilingual", "expressive"),
        license_name="Apache-2.0",
        license_source="components/voice/app/backend/catalog.py family note: 'Apache-2.0'",
        best_for="The reliable OmniVoice MLX option for multilingual cloning and voice design. It preserves the reference checkpoint precision without the redundant fp32 download.",
        sample_rate_hz=24000,
        languages=(),
        language_support=LanguageSupport(
            enumeration_status="claimed_count",
            claimed_count=646,
        ),
        use_cases=(
            ("good",  "Reference-quality OmniVoice at half the size of fp32"),
            ("good",  "Apache-2.0, 646 languages, voice cloning and voice design"),
            ("good",  "The lightest clone-capable model in the catalog — 3.3–3.5 GB peak, roughly a third of Audio8, and faster: 1.4–1.7x real time on 16/24 GB and 1.7–2.1x on 8 GB"),
            ("good",  "The only clone-capable model that completes on an 8 GB Mac (verified on M1 and two others, 2026-08-07)"),
            ("weak",  "On 8 GB it finishes without swapping but leaves under 1 GB free and reaches memory-pressure warning — fine on a dedicated fleet worker, not alongside other apps"),
            ("weak",  "The published compact conversions are not compatible with the current MLX engine, so this bf16 build is the only option"),
        ),
    ),

    # ──────────── Fish Audio S2 Pro (MLX) ────────────
    # v0.4.6 is the first pinned mlx-audio release in this app with the Fish
    # S2 Pro engine. The model repo bundles its codec.safetensors, so it needs
    # no companion download.
    ModelEntry(
        repo="mlx-community/fish-audio-s2-pro-8bit",
        label="Fish Audio S2 Pro 5B 8-bit (MLX) — fleet candidate",
        family="fish-audio-mlx",
        size_gb=6.73,
        gated=False,
        # Qualified on the fleet 2026-08-07 (was previously unqualified, hence
        # the None floor). Measured on one production section, Aiden clone:
        #   16 GB fleet Mac (17.2 GB): 13.27 GB MLX active, host peaked 88.4%,
        #       1.99 GB free, `warning` pressure, +0.33 GB swap  → runs, tight
        #   24 GB fleet Mac (25.8 GB): 13.51 GB MLX active, host peaked 81.2%,
        #       4.85 GB free, `normal` pressure, no swap          → comfortable
        # Refused outright by the guard on an 8.6 GB Mac. So 16 GB is the floor
        # and 24 GB is the comfortable tier — the same shape as Audio8.
        # Raised from 16 by the owner after listening on real hardware: 16 GB
        # clears the guard but not comfortably. Measured at 13.234 GB peak on a
        # 17.2 GB machine -- it fits with under 4 GB to spare, before macOS and
        # anything else the worker is doing, and ran at 3.75x realtime, the
        # slowest of every model measured on the fleet.
        min_unified_memory_gb=24,
        recommended_hardware=(
            "Apple Silicon with 16 GB minimum, 24 GB comfortable. Measured "
            "13.3 GB peak: on a 16 GB Mac that leaves ~2 GB free and reaches "
            "memory-pressure warning; on 24 GB it stays in normal pressure."
        ),
        capabilities=("tts", "voice-cloning", "multilingual", "expressive"),
        best_for="The practical Fish S2 Pro MLX tier. It bundles the model and codec in about 6.73 GB, claims 80+ language coverage and supports reference-voice cloning.",
        sample_rate_hz=44100,
        languages=(),
        language_support=LanguageSupport(
            enumeration_status="claimed_lower_bound",
            claimed_lower_bound=80,
        ),
        use_cases=(
            ("good",  "High-quality multilingual voice cloning with natural-language style control"),
            ("good",  "Single download includes the S2 Pro model and bundled codec"),
            ("weak",  "No unified-memory floor is claimed until controlled 16 GB and 24 GB qualification completes"),
            ("avoid", "8/16 GB Macs — measured at 13.3 GB peak, so a 16 GB Mac reaches memory-pressure warning"),
        ),
    ),
    ModelEntry(
        repo="mlx-community/fish-audio-s2-pro-bf16",
        label="Fish Audio S2 Pro 5B bf16 (MLX)",
        family="fish-audio-mlx",
        size_gb=11.01,
        gated=False,
        min_unified_memory_gb=32,
        recommended_hardware="Apple Silicon with 32 GB or more; not suitable for the 8/16 GB fleet tier.",
        capabilities=("tts", "voice-cloning", "multilingual", "expressive"),
        best_for="Full-precision Fish S2 Pro for reference-quality cloning and qualification work on high-memory Apple Silicon Macs.",
        sample_rate_hz=44100,
        languages=(),
        language_support=LanguageSupport(
            enumeration_status="claimed_lower_bound",
            claimed_lower_bound=80,
        ),
        use_cases=(
            ("good",  "Full-precision reference tier for 32 GB+ Apple Silicon"),
            ("good",  "Same cloning and style controls as the 8-bit build"),
            ("weak",  "About 11.01 GB on disk before runtime memory and cache overhead"),
            ("avoid", "8/16 GB Macs — the 32 GB tier is the stated floor"),
        ),
    ),

    # ──────────── Khmer TTS (MMS VITS) ────────────
    # Single-speaker Khmer VITS fine-tune of facebook/mms-tts-khm. Inference is
    # transformers VitsModel on PyTorch — deliberately NOT an mlx-audio family,
    # so it has its own worker (see generation._generate_khmer_vits).
    ModelEntry(
        repo="khmerttsopensource/khmer-tts",
        label="Khmer TTS (MMS VITS) — single speaker",
        family="khmer-tts-mms",
        # 332,188,360 B weights + a few KB of config/tokenizer.
        size_gb=0.34,
        gated=False,
        min_unified_memory_gb=8,
        recommended_hardware="Any Apple Silicon Mac with 8 GB. CPU/MPS VITS — under 1 GB peak.",
        capabilities=("tts", "multilingual"),
        best_for=(
            "A purpose-built, compact Khmer voice that runs fully offline. One "
            "built-in speaker, 16 kHz output. Meta's MMS VITS Khmer checkpoint, "
            "fine-tuned with an 83M-parameter model whose tokenizer covers Khmer "
            "script directly."
        ),
        sample_rate_hz=16000,
        languages=("km",),
        ignore_patterns=(
            "eval/*",
            "training/*",
            "README.md",
            "LICENSE.md",
            "UPLOAD.md",
            ".gitattributes",
        ),
        use_cases=(
            ("good", "Purpose-built Khmer model — Khmer characters are real tokens, not byte fallback"),
            ("good", "Compact and fast: ~332 MB of weights, comfortably fits an 8 GB Mac"),
            ("good", "Fully offline — no cloud dependency"),
            ("weak", "Single built-in speaker — no cloning, presets, or voice design"),
            ("weak", "16 kHz output is narrower than the 24–48 kHz families"),
            ("weak", "Khmer only — it cannot speak English"),
            ("avoid", "Voice cloning or multi-language narration"),
        ),
    ),

    # ──────────── Khmer TTS Female (VITS/ONNX) ────────────
    # sengtha's Coqui VITS export. Inference is onnxruntime — its own worker
    # (see generation._generate_khmer_onnx). Only khmer_tts_v3.onnx + tts_meta
    # are needed; the 998 MB best_model.pth training checkpoint is skipped.
    ModelEntry(
        repo="sengtha/khmer-tts-female-v2",
        label="Khmer TTS Female (VITS/ONNX) — single speaker",
        family="khmer-tts-onnx",
        # khmer_tts_v3.onnx 114,696,756 B + tts_meta.json 1,922 B.
        size_gb=0.115,
        gated=False,
        min_unified_memory_gb=8,
        recommended_hardware="Any Apple Silicon Mac with 8 GB. ONNX Runtime on CPU — a ~115 MB model.",
        capabilities=("tts", "multilingual"),
        best_for=(
            "A single female Khmer voice exported to ONNX for fully on-device "
            "use, trained on roughly 727 hours of the DDD-Cambodia read-speech "
            "corpus. 22.05 kHz, offline, no cloning."
        ),
        sample_rate_hz=22050,
        languages=("km",),
        ignore_patterns=(
            "best_model.pth",
            "khmer_tts.onnx",
            "khmer_tts_ls100.onnx",
            "khmer_tts_ls115.onnx",
            "config.json",
            "README.md",
            ".gitattributes",
        ),
        use_cases=(
            ("good", "Natural single female Khmer voice trained on a large read-speech corpus"),
            ("good", "Tiny (~115 MB) and fast — ONNX Runtime, no GPU required"),
            ("good", "Fully offline — no cloud dependency"),
            ("weak", "Single built-in speaker — no cloning, presets, or voice design"),
            ("weak", "Khmer only — it cannot speak English"),
            ("weak", "22.05 kHz output is narrower than the 24–48 kHz families"),
            ("avoid", "Voice cloning or multi-language narration"),
        ),
    ),

    # ──────────── Pocket TTS (MLX) ────────────
    # Kyutai's English Pocket TTS through mlx-audio's `pocket_tts` engine. The whole
    # executable model is 89 MB of 4-bit weights (117,332,322 parameters per its own
    # index) plus the mimi codec in the same safetensors file. It carries no tokenizer
    # and no voice prompts: its config.json names both by `hf://` URL against a SECOND
    # repository, kyutai/pocket-tts-without-voice-cloning, which the engine resolves
    # through the Hugging Face cache at generation time. Both are packaged as
    # companions (see FAMILY_COMPANIONS) so a node with no network can still run it.
    ModelEntry(
        repo="mlx-community/pocket-tts-4bit",
        # The quantization belongs in the label (owner rule, 2026-09-24): this is one
        # specific 4-bit conversion, not "Pocket TTS" in general, and it is the build
        # the owner listened to.
        label="Pocket TTS 4-bit (MLX) — English clone or preset",
        family="pocket-tts",
        # 89,282,784 B weights + 28,637 B weight index + 2,203 B config.
        size_gb=0.09,
        gated=False,
        # The owner's listening review (2026-09-24) measured a 1.9 GB peak and placed
        # this among the two engines with a credible 8 GB path, so 8 GB is the floor
        # this catalogue claims.
        min_unified_memory_gb=8,
        recommended_hardware=(
            "Any Apple Silicon Mac with 8 GB. Measured at 1.9 GB peak in the owner's "
            "2026-09-24 review; 16 GB leaves comfortable headroom."
        ),
        # "voice-cloning" is load-bearing beyond the UI wording. The native app derives a
        # model's form from `reference_audio.declared`, which catalog.serialize_model
        # computes as exactly `bool("voice-cloning" in capabilities)`. Dropping it would
        # silently re-file this model as a preset-only engine and hide the reference-voice
        # field that is its main use. Pocket TTS genuinely does both: its own
        # `generate(text, voice=None, ref_audio=None, …)` takes a preset voice name OR a
        # reference clip, and the reference wins when both are present.
        capabilities=("tts", "voice-cloning"),
        best_for=(
            "The best-sounding English cloning model on the owner's 2026-09-24 listening "
            "review, and the lightest clone-capable package in the catalogue: 4-bit "
            "weights, about 89 MB on disk and 1.9 GB peak, 24 kHz output. Clone a short "
            "reference clip, or fall back to one of its eight built-in preset voices."
        ),
        sample_rate_hz=24000,
        languages=("en",),
        language_support=LanguageSupport(
            input_selection="none",
            enumeration_status="exact",
            codes=("en",),
            runtime_enforced=True,
        ),
        ignore_patterns=("README.md", ".gitattributes"),
        use_cases=(
            ("good", "English voice cloning from a short reference clip — a private upload works too"),
            ("good", "The lightest clone-capable package here: 89 MB of 4-bit weights, ~1.9 GB peak"),
            ("good", "Preset voices when no reference is supplied (eight built-in prompts), so it still works with an empty voice field"),
            ("good", "Fast on Apple Silicon — no separate codec or vocoder download; the mimi codec is inside the same file"),
            ("weak", "English only — it cannot speak Khmer or any other language"),
            ("weak", "24 kHz output is narrower than the 44.1–48 kHz families"),
            ("weak", "4-bit quantization: expect slightly less fidelity than Kyutai's unquantized checkpoint"),
            ("avoid", "Khmer or other non-English narration — use the Khmer VITS families instead"),
        ),
    ),
    # ──────────── Breeze TTS 2 (MLX 4-bit) ────────────
    # Qualified 2026-09-29 on this project's staging Mac, in a throwaway venv, then listened to by
    # the owner: *"it's a good tts and i love it's quality."* Voice design from a written
    # description, cloning from a clip plus its transcript, direction, and inline vocal events such
    # as (sigh) — measured at 100% word match through a Whisper round trip for design, clone and
    # direction, and 93.8% for vocal events where the only "missing" words were the two event tags
    # themselves, which the engine performed rather than read aloud.
    ModelEntry(
        repo="mlx-community/Breeze-TTS-2-mlx-4bit",
        label="Breeze TTS 2 4-bit (MLX)",
        family="breeze-tts",
        # 2,325,487,160 B of weights plus tokenizer, configs and the audio tokenizer: 2.9 GB on
        # disk as measured on the four distribution drives. The weights' own SHA-256 is identical
        # on all four.
        size_gb=2.9,
        gated=False,
        # 16 GB is the floor this catalogue claims, and it is the honest one rather than the
        # flattering one. Two measurements disagree and only one of them is settled:
        #   [M] resident set size held flat at ~3.3 GB in every mode, which fits 8 GB;
        #   [M] the kernel's peak footprint reading ranged 5.41 to 23.93 GB across runs and was
        #       never pinned down, because the samplers could not see the real process.
        # Until that second number is settled, 8 GB is a plausible fit and not a demonstrated one,
        # so the claim is the tier that is not at risk. 16 GB is also where 50 of the owner's ~53
        # machines sit.
        min_unified_memory_gb=16,
        recommended_hardware=(
            "16 GB or more. Measured ~3.3 GB resident in every mode; the kernel peak footprint "
            "reading was unsettled between 5.41 and 23.93 GB, so 16 GB is claimed rather than 8."
        ),
        capabilities=("tts", "voice-cloning", "expressive", "multilingual"),
        best_for=(
            "Designing a voice from a written description, cloning one from a short reference "
            "clip and its transcript, or directing how a line is read — including inline vocal "
            "events such as (sigh) and (laugh), which it performs instead of reading aloud. "
            "24 kHz mono. English and Chinese."
        ),
        sample_rate_hz=24000,
        languages=("en", "zh"),
        language_support=LanguageSupport(
            input_selection="none",
            enumeration_status="exact",
            codes=("en", "zh"),
            runtime_enforced=True,
        ),
        ignore_patterns=("README.md", ".gitattributes", "_samples"),
        use_cases=(
            ("good", "Voice design — describe the voice in words and it makes one, with no reference clip at all"),
            ("good", "Cloning from a short reference clip, measured at 100% word match through a Whisper round trip"),
            ("good", "Long scripts — it segments itself one section per paragraph and was measured across 14 of them with no drift, no truncation and no repetition"),
            ("good", "Inline vocal events: (sigh), (laugh) and similar are performed rather than narrated"),
            ("weak", "English and Chinese only — it cannot speak Khmer; use the Khmer VITS families for that"),
            ("weak", "2.9 GB on disk and the heaviest voice package here after the 3B families"),
            ("weak", "Slower than it looks: measured 0.27x realtime, so about 19.8 minutes of generation per 1,000 words"),
            ("weak", "Needs the mlx-audio 0.5.7 runtime; an older voice runtime has no breeze_tts module at all"),
            ("avoid", "An 8 GB Mac until the peak-footprint question is settled — the resident size fits, the kernel peak did not"),
        ),
    ),
)


def get_model(repo: str) -> Optional[ModelEntry]:
    for m in CATALOG:
        if m.repo == repo:
            return m
    return None


class SectionSizeControlError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _audited_long_form_policy(family: str, repo: str) -> dict | None:
    """The effective long-form policy: the measured production pin, then any model audit.

    `model_audits.qwen_17b_production_v2_limits` answers for exactly the models
    whose own audit record is the measured production audit (or explicitly
    derives from it) and returns ``{}`` for every other model, so this file asks
    one question in one order. It used to compare ``repo`` against a hardcoded
    8-bit repo id first; when that tier was pruned, the shipped tier fell through
    to the family default and the audited Auto section size went inert.
    """
    pinned = model_audits.qwen_17b_production_v2_limits(repo)
    if pinned:
        return long_form_policy.policy_for(
            family,
            repo,
            audited_section_max_characters=pinned.get("private_section_max_characters"),
            audited_default_section_max_characters=pinned.get(
                "default_private_section_max_characters"
            ),
        )
    return long_form_policy.policy_for(
        family,
        repo,
        audited_section_max_characters=model_audits.input_limits(repo).get(
            "private_section_max_characters"
        ),
    )


def _derived_section_size_control(policy: dict) -> dict[str, int | str]:
    """The section-size control every sectioning family gets, whether or not one was declared.

    Only the Qwen 1.7B Base model carried a control, so every other family's sectioning was
    invisible and unchangeable: the budget it used was published nowhere an operator could act on,
    and no caller could tighten it. Deriving the control from the same policy the runtime obeys
    means the number a person sees is the number the engine is given.

    The range is deliberate. `minimum` is the floor `policy_for` already accepts from an audit
    (40 characters), and `maximum` is the engine's own input limit where it has one - Orpheus stops
    reading at 170 characters and Bark fails past 150, so no control may offer more than that - or
    the largest budget any measured family sustains (Kokoro's and VibeVoice's 3000) where the
    engine has no cliff. A larger section than that is a claim nobody has evidence for.
    """
    budget = int(policy["section_max_characters"])
    hard_cap = policy.get("hard_cap_characters")
    maximum = int(hard_cap) if hard_cap else max(budget, 3000)
    return {
        "minimum": 40,
        "maximum": maximum,
        "step": 10,
        "default_custom": budget,
        "runtime_default": budget,
        "source": str(policy.get("source") or "runtime_default"),
    }


def section_size_control_for(repo: str) -> dict[str, int | str] | None:
    entry = get_model(repo)
    if entry is None:
        return None
    policy = _audited_long_form_policy(entry.family, entry.repo)
    if policy is None:
        return None
    if entry.section_size_control is None:
        return _derived_section_size_control(policy)
    control = entry.section_size_control.serialize()
    if policy["section_max_characters"] != control["runtime_default"]:
        return None
    return control


def resolve_section_budget(
    family: str,
    repo: str,
    requested: object,
    capability: dict[str, int | str] | None = None,
) -> dict[str, object]:
    derived_control = section_size_control_for(repo)
    if capability is not None and capability != derived_control:
        raise SectionSizeControlError(
            "SECTION_MAX_CHARACTERS_UNSUPPORTED",
            f"Model {repo} has no section-size control.",
        )
    control = derived_control
    policy = _audited_long_form_policy(family, repo)
    if policy is None:
        raise SectionSizeControlError(
            "SECTION_MAX_CHARACTERS_UNSUPPORTED",
            f"Model {repo} has no section-size control.",
        )
    if requested is None:
        # A model whose own audit anchors the measured production section policy
        # must publish a derivable control: refusing here is better than
        # silently falling back to the family budget for a model the audit has
        # an exact number for.
        if control is None and model_audits.qwen_17b_production_v2_limits(repo):
            raise SectionSizeControlError(
                "SECTION_MAX_CHARACTERS_UNSUPPORTED",
                f"Model {repo} has no section-size control.",
            )
        return {
            "section_max_characters": (
                control["runtime_default"]
                if control is not None
                else int(policy["section_max_characters"])
            ),
            "source": "audit",
            "capability": control,
        }
    if type(requested) is not int:
        raise SectionSizeControlError(
            "SECTION_MAX_CHARACTERS_INVALID", "Section size must be a whole number."
        )
    if control is None:
        raise SectionSizeControlError(
            "SECTION_MAX_CHARACTERS_UNSUPPORTED",
            f"Model {repo} has no section-size control.",
        )
    if (
        requested < control["minimum"]
        or requested > control["maximum"]
    ):
        raise SectionSizeControlError(
            "SECTION_MAX_CHARACTERS_OUT_OF_RANGE",
            "Section size must be within the audited range.",
        )
    return {
        "section_max_characters": requested,
        "source": "caller_override",
        "capability": control,
    }


def ignore_patterns_for(repo: str) -> tuple[str, ...]:
    """Return the per-repo download skip-list, or () if the repo is unknown
    or has no filtering. Used by downloads.py to thin out huge HF repos that
    ship redundant weight formats or alternate checkpoints."""
    m = get_model(repo)
    if m is None:
        return ()
    return m.ignore_patterns


# ───────────── Companion (helper) models ─────────────
#
# Some engines load a SECOND model at generation time — an audio codec /
# tokenizer that lives in a *different* HF repo than the catalog model. If we
# only download the catalog repo, the first generation triggers a surprise
# download (the user thinks the model is "ready" but then waits again).
#
# To make a download complete-on-first-run, every family whose engine pulls an
# external companion is listed here. downloads.py fetches these right after the
# main model, and /api/catalog marks a model "partial" until its companions are
# present too — so the cached badge stays honest.
#
# Each companion: {repo, allow_patterns, label}.
#   allow_patterns=None → whole repo. A tuple → only those files (used for
#   kyutai/moshiko-pytorch-bf16, where we need ONE codec file out of a ~15 GB
#   repo, NOT the whole thing).
#
# VERIFIED against the installed mlx-audio engine source:
#   - chatterbox-mlx    → chatterbox.py loads mlx-community/S3TokenizerV2
#   - pocket-tts        → pocket_tts/conditioners.py loads the sentencepiece model and
#                         pocket_tts/utils.py loads the preset voice prompts, both from
#                         kyutai/pocket-tts-without-voice-cloning @ d4fdd22a, by `hf://`
#                         URL through `hf_hub_download`. Unlike every other companion
#                         here these are not optional extras: the tokenizer is loaded on
#                         EVERY generation, so without it the engine cannot encode text
#                         at all, and the preset prompts cover the zero-shot default
#                         (`DEFAULT_AUDIO_PROMPT = "alba"`) when no reference is supplied.
# OmniVoice / Fish S2 Pro load their codecs from inside their own repo (no
# companion). The marvis / orpheus / moss-tts-nano / echo-tts / longcat / bark
# entries were removed with their families on 2026-09-24 (ST-13.B).
FAMILY_COMPANIONS: dict[str, tuple[dict, ...]] = {
    "chatterbox-mlx": (
        {
            "repo": "mlx-community/S3TokenizerV2",
            "allow_patterns": None,
            "label": "S3 speech tokenizer",
        },
    ),
    "pocket-tts": (
        {
            "repo": "kyutai/pocket-tts-without-voice-cloning",
            "allow_patterns": ("tokenizer.model", "embeddings/*.safetensors"),
            "label": "Pocket TTS tokenizer and preset voices",
        },
    ),
}


def companions_for(repo: str) -> tuple[dict, ...]:
    """Companion (codec/tokenizer) models the engine pulls at generation time
    for the family that owns `repo`. Empty tuple when the family is
    self-contained or the repo is unknown."""
    m = get_model(repo)
    if m is None:
        return ()
    return FAMILY_COMPANIONS.get(m.family, ())


class RegistryError(ValueError):
    """A catalog row or family registry entry that cannot be resolved.

    Raised with the repo, the family id and the file to edit, never as a bare
    ``KeyError`` from inside a route: `/api/catalog` serializes every row in one
    loop, so an unexplained exception hides the whole model list, not one row.
    """


def family_for(model: ModelEntry) -> Family:
    """The family entry a catalog row names, or a named failure.

    ``FAMILIES[m.family]`` used to be an unchecked index, so a row with a typo'd
    or removed family id raised ``KeyError: 'khmer-tts-ms'`` while serializing
    the catalog and the picker showed nothing.
    """
    family = FAMILIES.get(model.family)
    if family is None:
        raise RegistryError(
            f"catalog model {model.repo!r} names family {model.family!r}, which is not in "
            "FAMILIES; add the Family entry (or fix the id) in "
            "components/voice/app/backend/catalog.py"
        )
    return family


def _validate_registry() -> None:
    """Refuse to import a catalog whose rows and families disagree.

    Two invariants, both of which used to be silent until a request arrived:
    every row's family exists, and every family has at least one row. An
    engine this file does not know is refused here too, because the MLX label,
    the dispatch table and the diagnostics panel all read ``Family.engine``.
    """
    for model in CATALOG:
        family_for(model)
    used = {model.family for model in CATALOG}
    for family_id, family in FAMILIES.items():
        if family_id != family.id:
            raise RegistryError(
                f"FAMILIES[{family_id!r}] carries id={family.id!r}; fix the key or the id in "
                "components/voice/app/backend/catalog.py"
            )
        if family.engine not in ALL_ENGINES:
            raise RegistryError(
                f"family {family_id!r} names engine {family.engine!r}, which is not one of "
                f"{sorted(ALL_ENGINES)}; add the engine in "
                "components/voice/app/backend/catalog.py and a worker for it in "
                "components/voice/app/backend/generation.py"
            )
        if family_id not in used:
            raise RegistryError(
                f"family {family_id!r} is in FAMILIES but no CATALOG row uses it; remove the "
                "entry or add the model in components/voice/app/backend/catalog.py"
            )
        roster = family.preset_roster
        if roster is not None and (
            not roster.key or not roster.source or not roster.speakers
            or any(not speaker.get("id") for speaker in roster.speakers)
        ):
            raise RegistryError(
                f"family {family_id!r} publishes a preset roster with no key, no source, no "
                "voices, or a voice with no id; a roster a consumer cannot match to its family "
                "- or a scheduler cannot choose from - is a picker that never fills and a "
                "contract that lies (components/voice/app/backend/catalog.py)"
            )


# The three ways this app can be told what a voice should sound like, in the
# order every consumer renders them. The names are the contract: a consumer
# (the native Voice page's tabs, a scheduler) must not have to map them.
VOICE_MODES = ("cloning", "preset", "design")


def voice_modes(m: ModelEntry) -> list[str]:
    """Which of ``cloning`` / ``preset`` / ``design`` this model actually offers.

    Derived from two published facts and nothing else, so a model added to the
    catalog lands in the right tabs without an edit anywhere:

    * ``capabilities`` - ``"voice-cloning"`` is the model declaring that it
      clones a reference voice;
    * the family's own ``lane_parameters`` - the request fields its resolver
      reads. ``voice_design_prompt`` means the engine designs a voice from a
      description; ``voice`` means it speaks with a named built-in preset.

    A model with none of the three is not voiceless: a family that declares no
    cloning, no design and no preset input (the Khmer VITS voices) speaks with
    its own single built-in voice, which is the ready-made form.

    This exists because the native Voice page used to offer all three tabs for
    every model and reduce a model to one mode by name - so an operator could
    pick "Design" on a model that cannot design and only learn it from a
    refusal, and a model that both clones and carries presets was reachable in
    one tab only. Publishing the list here is what lets that page offer a model
    in each mode it publishes; no consumer should re-derive it.
    """
    family = family_for(m)
    capabilities = tuple(m.capabilities or ())
    declared = set(family.lane_parameters)
    cloning = "voice-cloning" in capabilities
    design = "voice_design_prompt" in declared
    named_preset = "voice" in declared
    modes = []
    if cloning:
        modes.append("cloning")
    if named_preset or not (cloning or design):
        modes.append("preset")
    if design:
        modes.append("design")
    return modes


def serialize_model(m: ModelEntry, defaults: Mapping[str, object] | None = None) -> dict:
    """One catalog row.

    ``defaults`` is the resolved per-model default map for this model
    (``components/model_defaults.resolved``), passed straight into the lane
    contract so the ``default`` a scheduler reads there is the value this worker
    will use for an omitted parameter. It is a parameter rather than a read of
    the worker's state directory here, because this function is also the
    fixture-free way a test and ``reference_audio`` ask what a model publishes.
    """
    # Per-model hardware-fit verdict against the running Mac's detected RAM.
    # Lazy import dodges any potential cycle at module-load time.
    try:
        from . import system_info
        fit = system_info.fit_for(m.min_unified_memory_gb)
    except Exception:
        fit = None
    family = family_for(m)
    # Naming-compat with the sibling Studio frontends: "apple_optimized"
    # identifies MLX-native families for runtime labels and the opt-in filter.
    # It is the family's own engine fact, so a new MLX family is labelled and
    # filtered correctly the moment it declares `engine=ENGINE_MLX_AUDIO`; no
    # second list exists to fall out of sync (the 2026-09-24 regression, when
    # kittentts/vibevoice/omnivoice shipped hidden, was exactly that).
    apple_optimized = family.engine in APPLE_OPTIMIZED_ENGINES
    try:
        audited_input_limits = model_audits.input_limits(m.repo)
    except Exception:
        audited_input_limits = {}
    reference_profile = audited_input_limits.get("reference_audio")
    if not isinstance(reference_profile, dict):
        reference_profile = {}
    family_guidance = family.text_guidance
    audited_text_max = audited_input_limits.get("text_max_characters")
    try:
        audited_text_max = int(audited_text_max) if audited_text_max is not None else None
    except (TypeError, ValueError):
        audited_text_max = None
    language_support = m.language_support or LanguageSupport(codes=m.languages)
    effective_long_form_policy = _audited_long_form_policy(m.family, m.repo)
    if effective_long_form_policy is not None:
        effective_long_form_policy = {
            **effective_long_form_policy,
            "section_size_control": section_size_control_for(m.repo),
        }
    qwen_clone_guardrails = (
        {
            "validator_revision": qwen_quality.VALIDATOR_REVISION,
            "reference_word_alignment": True,
            "automatic_section_token_ceiling": True,
            "output_transcript_validation": True,
            "max_local_quality_retries": 1,
            "retry_section_max_characters": qwen_quality.RETRY_SECTION_MAX_CHARACTERS,
            "required_local_models": qwen_quality.validator_models(m.repo),
        }
        if qwen_quality.is_qwen_base_clone(m.repo)
        else None
    )
    return {
        "repo": m.repo,
        "label": m.label,
        "family": m.family,
        "family_label": family.label,
        "size_gb": m.size_gb,
        "gated": m.gated,
        "min_unified_memory_gb": m.min_unified_memory_gb,
        # The enforced floor, published under the name the Hub's governor reads
        # (`workload_policy.required_free_memory_gb`). Absent means "no floor of this model's
        # own", which is a different statement from zero.
        **({"min_free_memory_gb": float(m.min_free_memory_gb)}
           if m.min_free_memory_gb is not None else {}),
        "recommended_hardware": m.recommended_hardware,
        "capabilities": list(m.capabilities),
        # Which of `cloning` / `preset` / `design` this model offers, derived from the
        # capabilities above and the family's own declared request fields (see
        # `voice_modes`). A consumer offers the model in each mode listed here and in no
        # other: offering a mode the model does not support is a silent-wrong-UI shape
        # where the operator learns it from a refusal after pressing Generate.
        "modes": voice_modes(m),
        "best_for": m.best_for,
        "sample_rate_hz": m.sample_rate_hz,
        # No fixed max duration for TTS — output length scales with text length.
        # Frontend will use text length as the user-visible cost signal instead.
        "max_duration_seconds": None,
        "languages": list(m.languages),
        "language_support": {
            "input_selection": language_support.input_selection,
            "enumeration_status": language_support.enumeration_status,
            "codes": list(language_support.codes),
            "claimed_count": language_support.claimed_count,
            "claimed_lower_bound": language_support.claimed_lower_bound,
            "runtime_enforced": language_support.runtime_enforced,
        },
        # The dispatch contract a scheduler reads (docs/lane-execution-contract.md).
        # Kept beside, not inside, `execution_contract`: that one is TTS
        # presentation for the native Voice page, this one is how to ask for work.
        # Its parameters carry this model's resolved defaults, so the number a
        # scheduler reads is the number an omitted field gets.
        "lane_contract": lane_contract.for_model(m, defaults),
        "execution_contract": {
            # Public limits are qualification results, not optimistic guesses
            # from family guidance. An unaudited model remains available for
            # local testing without claiming a sellable 40k contract.
            "text_max_characters": audited_text_max,
            "long_form_strategy": audited_input_limits.get(
                "long_form_strategy"
            ) or "unverified",
            "private_section_max_characters": audited_input_limits.get(
                "private_section_max_characters"
            ),
            "qualification_source": "audit" if audited_input_limits else "unverified",
            "quality_guardrails": qwen_clone_guardrails,
        },
        "long_form_delivery": effective_long_form_policy,
        "reference_audio": {
            # ``declared`` is the family capability claim; ``profile_source``
            # says whether the exact limits below are audit-grounded or the
            # adapter's documented fallback. Qwen3-TTS Base is the only family
            # whose reference contract is qualification-bound, so a caller must
            # be able to tell "cloning supported, limits audited" from
            # "cloning supported, limits unverified" without guessing.
            "declared": bool("voice-cloning" in m.capabilities),
            "supported": bool("voice-cloning" in m.capabilities),
            "profile_audited": bool(reference_profile),
            "profile_source": "audit" if reference_profile else "unverified",
            # ``transcript`` ("required" for every Base tier) comes from the
            # audited profile below; it is the backend-enforced requirement.
            **reference_profile,
        },
        "apple_optimized": apple_optimized,
        "license": {"name": m.license_name, "source": m.license_source},
        "quantization": None,
        "aliases": [],
        "ignore_patterns": list(m.ignore_patterns),
        # New in v1.1 — structured use cases + hardware fit verdict.
        "use_cases": [{"kind": k, "text": t} for k, t in m.use_cases],
        "fit": fit,
        # The below-tier warning, under the **same key and in the same place** as the other four
        # Studios (Image, Music, Subtitle, Chat), so the Audio page draws it with the same render
        # site they use. Voice was the one worker that published a structured `fit` verdict and no
        # sentence, so the page had nothing to show and the operator was never told (SH-3).
        #
        # Derived from `fit` rather than from a second comparison: `risky` IS this worker's own
        # answer to "this host is below the model's floor", and `hint` already names both numbers.
        # A second threshold here would be free to disagree with the chip beside it.
        "memory_warning": fit.get("hint") if fit and fit.get("state") == "risky" else None,
    }


def serialize_family(f: Family) -> dict:
    return {
        "id": f.id,
        "label": f.label,
        # The engine travels with the family, so the UI (and any sibling
        # consumer) can tell an MLX family from a PyTorch one without a list.
        "engine": f.engine,
        "summary": f.summary,
        "how_to_use": f.how_to_use,
        "text_guidance": {
            "soft_max_chars": f.text_guidance.soft_max_chars,
            "chunking": f.text_guidance.chunking,
            "note": f.text_guidance.note,
        },
    }


# Refuse to import a registry whose rows and families disagree (see
# `_validate_registry`). This runs after both registries exist and turns a
# typo'd family id into an immediate, named import failure instead of a
# `KeyError` on the first `/api/catalog` request.
_validate_registry()
