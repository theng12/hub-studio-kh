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
from typing import Optional

from . import long_form_policy, model_audits, qwen_quality


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


@dataclass(frozen=True)
class Family:
    id: str
    label: str
    summary: str
    how_to_use: str
    text_guidance: TextGuidance


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


FAMILIES: dict[str, Family] = {
    "voxcpm-mlx": Family(
        id="voxcpm-mlx",
        label="VoxCPM2 (MLX)",
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
        summary=(
            "Fish Audio's S2 Pro 5B multilingual TTS running through the MLX-Audio "
            "v0.4.6 backend. It supports zero-shot voice cloning from a reference "
            "clip plus expressive instruction prompts and includes its own codec."
        ),
        how_to_use=(
            "Choose a Fish S2 Pro checkpoint, optionally select a reference voice, "
            "and describe the delivery in the style field. Voice Studio privately "
            "splits long scripts at safe boundaries, joins the sections, and applies "
            "the requested final speed once. Check Fish Audio's license before "
            "commercial use: the public model license is research/non-commercial "
            "unless you have a separate commercial grant."
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
        recommended_hardware=(
            "Apple Silicon with 8 GB unified memory minimum; 16 GB preferred "
            "for normal memory pressure and faster generation."
        ),
        # Base model handles voice CLONING from a reference audio clip — pair
        # with a voice from the Voices library. Not a "plain TTS" model.
        capabilities=("tts", "voice-cloning", "multilingual"),
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
    # no companion download. The public Fish model license is not commercial.
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
        best_for="The practical Fish S2 Pro MLX tier. It bundles the model and codec in about 6.73 GB, claims 80+ language coverage and supports reference-voice cloning, but the public Fish Audio research license is not a commercial-use grant.",
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
            ("avoid", "Commercial customer work unless you have Fish Audio's separate commercial license"),
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
        best_for="Full-precision Fish S2 Pro for reference-quality cloning and qualification work on high-memory Apple Silicon Macs. The public model license is research/non-commercial unless separately licensed.",
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
            ("avoid", "8/16 GB Macs and commercial use without a separate Fish license"),
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
    if repo == long_form_policy.QWEN_17B_BASE_REPO:
        limits = model_audits.qwen_17b_production_v2_limits(repo)
        return long_form_policy.policy_for(
            family,
            repo,
            audited_section_max_characters=limits.get("private_section_max_characters"),
            audited_default_section_max_characters=limits.get(
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
        if repo == long_form_policy.QWEN_17B_BASE_REPO and control is None:
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
}


def companions_for(repo: str) -> tuple[dict, ...]:
    """Companion (codec/tokenizer) models the engine pulls at generation time
    for the family that owns `repo`. Empty tuple when the family is
    self-contained or the repo is unknown."""
    m = get_model(repo)
    if m is None:
        return ()
    return FAMILY_COMPANIONS.get(m.family, ())


def serialize_model(m: ModelEntry) -> dict:
    # Per-model hardware-fit verdict against the running Mac's detected RAM.
    # Lazy import dodges any potential cycle at module-load time.
    try:
        from . import system_info
        fit = system_info.fit_for(m.min_unified_memory_gb)
    except Exception:
        fit = None
    # Naming-compat with the sibling Studio frontends: "apple_optimized"
    # identifies MLX-native families for runtime labels and the opt-in filter.
    # Keep this set in sync with generation.MLX_AUDIO_FAMILIES
    # (catalog.py can't import generation.py without a circular import, hence
    # the explicit enumeration). The "-mlx" suffix catches voxcpm-mlx and
    # chatterbox-mlx; the rest are named. Pruned 2026-09-24 (ST-13.B) to the
    # families that remain in the catalog.
    _MLX_AUDIO_FAMILIES = (
        "qwen3-tts", "omnivoice", "fish-audio-mlx",
    )
    apple_optimized = m.family.endswith("-mlx") or m.family in _MLX_AUDIO_FAMILIES
    try:
        audited_input_limits = model_audits.input_limits(m.repo)
    except Exception:
        audited_input_limits = {}
    reference_profile = audited_input_limits.get("reference_audio")
    if not isinstance(reference_profile, dict):
        reference_profile = {}
    family_guidance = FAMILIES[m.family].text_guidance
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
            "required_local_models": [qwen_quality.WHISPER_REPO],
        }
        if qwen_quality.is_qwen_base_clone(m.repo)
        else None
    )
    return {
        "repo": m.repo,
        "label": m.label,
        "family": m.family,
        "family_label": FAMILIES[m.family].label,
        "size_gb": m.size_gb,
        "gated": m.gated,
        "min_unified_memory_gb": m.min_unified_memory_gb,
        "recommended_hardware": m.recommended_hardware,
        "capabilities": list(m.capabilities),
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
        "quantization": None,
        "aliases": [],
        "ignore_patterns": list(m.ignore_patterns),
        # New in v1.1 — structured use cases + hardware fit verdict.
        "use_cases": [{"kind": k, "text": t} for k, t in m.use_cases],
        "fit": fit,
    }


def serialize_family(f: Family) -> dict:
    return {
        "id": f.id,
        "label": f.label,
        "summary": f.summary,
        "how_to_use": f.how_to_use,
        "text_guidance": {
            "soft_max_chars": f.text_guidance.soft_max_chars,
            "chunking": f.text_guidance.chunking,
            "note": f.text_guidance.note,
        },
    }
