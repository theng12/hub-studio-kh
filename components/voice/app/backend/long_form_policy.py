"""Authoritative private long-form delivery policy for local TTS models.

Callers submit one complete script.  Voice Studio uses these model-specific
budgets to render sentence-safe sections, validate them, and return one joined
artifact.  The catalog publishes this same data so the Models UI cannot drift
from the generation runtime.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Optional


DEFAULT_JOIN_PAUSE_SECONDS = 0.12
QWEN_CLONE_JOIN_PAUSE_SECONDS = 0.18
OMNIVOICE_JOIN_PAUSE_SECONDS = 0.30
OMNIVOICE_PARAGRAPH_JOIN_PAUSE_SECONDS = 0.60
OMNIVOICE_SOFT_JOIN_PAUSE_SECONDS = 0.18
# ── one narration rule for every voice model (owner decision 2026-10-03) ─────
# Every section join follows the text, for every family, by default: 1.0 s after a sentence, 1.5 s
# at a paragraph break (owner decision 2026-10-04; was 0.6 / 1.0). A cut inside a sentence happens only when one sentence is longer than the
# section budget (about 0.05% of Dreamlands sentences at 250 characters) and keeps a short soft join.
# A request may still ask its own `sentence_pause_sec` / `paragraph_pause_sec`. This replaces the
# family-specific joins (Breeze's flat 0.6 s, the 0.3/0.6/0.18 s production joins, Qwen's 0.18 s and
# the 0.12 s default); those numbers stay above as the values the model audits were measured at.
UNIFORM_SENTENCE_JOIN_PAUSE_SECONDS = 1.00
UNIFORM_PARAGRAPH_JOIN_PAUSE_SECONDS = 1.50
UNIFORM_SOFT_JOIN_PAUSE_SECONDS = 0.15
# And one section size for every voice model. Evidence, Dreamlands scripts: median sentence 115
# characters, 99th percentile 210, longest 262; 0.05% of sentences are longer than 250 and 1.87%
# longer than 200. So a 250-character section almost always ends at a sentence, and no family runs a
# section past what it was measured at (the family budgets below are the measured ceilings: Breeze
# 500, VoxCPM2 400, Fish 300, OmniVoice and Qwen clone 288, Qwen preset 360, Chatterbox 500, the
# conservative default 280, the audited Qwen 1.7B Auto 280). `policy_for` caps every one at this.
UNIFORM_SECTION_MAX_CHARACTERS = 250
# ── matching section loudness before the join ────────────────────────────────
# A long script is rendered as independent sections and the engine has no idea what level the
# section before it came out at. Measured on Breeze TTS 2 4-bit: across the 14 sections of one
# 1,227-word script the integrated loudness spans **5.0 LU** (−30.0 to −25.0 LUFS), which the ear
# hears as the narrator stepping back from the microphone at every paragraph and forward again at
# the next. The join itself was already right — correct pause, correct order, no drift — so this was
# never a joining fault. It is the absence of a levelling pass, and the joiner never had one for any
# family, so every long-form family rendered through it has been delivering whatever level its
# engine happened to produce.
#
# The target is speech-programme loudness rather than a peak: −16 LUFS integrated, which is the
# figure the owner's approved Breeze samples were cut at, so a joined render and the sample he
# listened to are the same delivery. The ceiling is **true peak**, not sample peak.
#
# **Why this is FFmpeg's `loudnorm` and not one linear gain per section.** Measured on the real
# segments: their loudness spans 4.9 LU, but so does their *crest factor* (18.4 to 22.7 dB), so the
# gain each section can take before its peak reaches the ceiling is what actually decides its final
# level. Applying `min(target − I, ceiling − TP)` — the obvious linear answer — narrowed the span
# only from **4.9 LU to 4.3 LU**: the gain was capped on every quiet section and the step survived.
# `loudnorm` brought the same 14 sections to a **1.5 LU** span at −1.5 dBTP, which is the measurement
# the owner heard and approved. So the pass is `loudnorm`, per section, and the numbers here are its
# parameters rather than a gain this code computes.
SECTION_LOUDNESS_TARGET_LUFS = -16.0
SECTION_TRUE_PEAK_CEILING_DBTP = -1.5
# Loudness range, in LU: how much level variation within one section `loudnorm` is allowed to leave.
# 11 is the EBU R128 default and the value the approved samples were made with.
SECTION_LOUDNESS_RANGE_LU = 11.0
# The measured section budgets. Each number is named here with the evidence that
# produced it, and *assigned* to the family it belongs to on that family's entry
# in `catalog.FAMILIES` - the registry is where a consumer reads it, so adding a
# family or changing a budget does not mean editing a second branch chain.
QWEN_CLONE_SECTION_MAX_CHARACTERS = 288
QWEN_PRESET_SECTION_MAX_CHARACTERS = 360
CHATTERBOX_STANDARD_SECTION_MAX_CHARACTERS = 500
VOXCPM_SECTION_MAX_CHARACTERS = 400
# OmniVoice is flow-matching, not autoregressive: it has NO internal splitting
# and NO length cap, and commits its whole latent block up front from
# target_len = ceil(duration_s * 25) (or the RuleDurationEstimator when duration
# is omitted). There is no EOS to bail out early, so the outer section budget is
# load-bearing here in a way it is not for the GPT-style engines.
#
# 288 was originally copied verbatim from QWEN_CLONE_SECTION_MAX_CHARACTERS —
# a disqualified model with entirely different failure physics — and carried no
# derivation. It has since been independently justified, and the constraint
# that sets it is QUALITY, not memory:
#
# Transcribe-back coverage, same text/seed/voice, only the section budget
# varying (mechanism: OmniVoice commits target_len frames up front from its
# duration estimator and must fill exactly that budget; per-section estimates
# track short spans well, but over a long span the estimate drifts long and
# the surplus becomes silence and dropped words, not extra pauses):
#
#   288  -> 4 sections, 99.4% coverage, 1 word lost   (shipped value)
#   350  -> 3 sections, 99.4% coverage, 1 word lost   (measured ceiling)
#   400  -> 3 sections, 97.0% coverage, 5 words lost
#   450  -> 3 sections, 96.4% coverage, 6 words lost
#   500  -> 2 sections, 95.8% coverage, 7 words lost
#   600  -> 2 sections, 83.0% coverage, 28 words lost
#   1200 -> 1 section,  60.0% coverage, 66 words lost
#
# 350 is the measured ceiling for equal (99.4%) coverage, but 288 is kept
# because at that equal coverage it paces better: ~26% silence vs. ~30% at
# 350. (Terminal-silence trimming was considered and ruled out as a
# confound — it applies only to Qwen CustomVoice and Chatterbox, never
# OmniVoice, so it cannot explain the pacing or coverage differences above.)
#
# Fleet measurement 2026-08-07 (3 reps/tier, escalating duration through each
# machine's own job engine) separately puts the MEMORY ceiling far higher than
# 288 requires:
#
#   8 GB   passed 2250 frames (90 s), failed 3000 (120 s), reproduced 2x
#   16 GB  passed 3000 frames (120 s) -- the API clamp, not the limit
#   24 GB  passed 3000 frames (120 s) -- the API clamp, not the limit
#
# At the measured 1.749 frames/char for English narration that is ~1286 chars
# even on 8 GB, i.e. 288 is ~4.5x more conservative than memory requires. This
# is retained for the record, but memory was never the binding constraint —
# do NOT raise this value on memory evidence alone. That is the specific
# mistake this comment exists to prevent: the fidelity curve above is what
# sets 288, and raising it past 288 measurably loses words.
OMNIVOICE_SECTION_MAX_CHARACTERS = 288
FISH_AUDIO_SECTION_MAX_CHARACTERS = 300
# Breeze TTS 2 segments itself, and it segments *correctly*: given a 1,227-word, 6,463-character
# script it produced one section per paragraph — **14 sections of 325 to 575 characters, mean 449** —
# and a Whisper round trip found every one a verbatim continuation in order, with no truncation, no
# drift and no repetition (measured 2026-09-29). So this material states the range the engine was
# actually handed and handled.
#
# 500 sits inside that measured range rather than at its edge: above the mean, below the longest
# section Breeze was given, and identical to the budget Chatterbox already uses, so it adds no new
# number to the codebase's vocabulary. It is a *delivery* budget and not the engine's limit —
# nothing measured says Breeze stops working past 575.
BREEZE_TTS_SECTION_MAX_CHARACTERS = 500
# The join pause the owner chose by ear from three cuts of the same 1,200-word Aiden render —
# 0.30 s, 0.45 s and 0.60 s — on 2026-09-29: *"i've listened, the 0.60s pause is the best one."*
# It is also the value OmniVoice already uses for a paragraph boundary, so it is inside the range
# this project ships rather than a new one. Recorded before it was wired, on purpose: the number is
# the owner's, not a re-measurement by whoever adopted the family.
BREEZE_TTS_JOIN_PAUSE_SECONDS = 0.60
# ── families with no measurement of their own ────────────────────────────────
# A family without a policy used to fall out of `_runtime_default` as None, and the caller read
# "None" as "send the whole script in one call". That is how the gateway came to see an engine
# cliff instead of a section budget: families with no sectioning at all sent a 2,000-character
# script as one request to an engine that cannot take it.
#
# 280 is therefore the floor, not a guess at the best value: it is the smallest of the established
# family budgets, so no family is sectioned more coarsely than a family we have actually measured.
# A family with its own evidence keeps its own number, declared on its `catalog.FAMILIES` entry.
CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS = 280
# The public speed control, one range for every family.
SPEED_MINIMUM = 0.5
SPEED_MAXIMUM = 2.0
# Kokoro and KittenTTS predicted their own durations from a speed factor
# (`duration = sigmoid(duration).sum() / speed`), which is the model's own idea of pace and beats a
# resampled one. Neither family ships any more, so every shipped family takes the tempo path - but
# the fact is a property of the engine, and it now lives on the family entry in `catalog.FAMILIES`
# (`native_speed`) instead of in a set here: a family that predicts its own durations declares it
# where the rest of its engine facts live, and cannot be silently handed a second processing pass.
# Every other engine either ignores the argument outright (Chatterbox documents "Ignored", LongCat
# and OmniVoice never read it), reads it as a discrete level rather than a multiplier (Spark-TTS
# indexes SPEED_MAP[float] and would raise KeyError on any other value), or applies it per section
# in a way that would fight the joined-WAV pass (Fish). Those families get one pitch-preserving
# tempo pass on the finished artifact instead - applied exactly once, from the parent, where
# cancellation custody already lives.


@dataclass(frozen=True)
class SpeedPolicy:
    """How the public speed control reaches one family's engine.

    `delivery` is "native" (the engine scales its own durations) or "tempo" (Voice Studio applies
    one pitch-preserving FFmpeg pass to the finished WAV). Publishing it per family is what makes
    "the speed control is supplied for this model" checkable instead of assumed.
    """
    delivery: str
    minimum: float = SPEED_MINIMUM
    maximum: float = SPEED_MAXIMUM
    note: str = ""


SPEED_NATIVE = SpeedPolicy(
    delivery="native",
    note=("The engine scales its own predicted durations, so the requested pace is part of "
          "synthesis rather than applied to the result."),
)
SPEED_TEMPO = SpeedPolicy(
    delivery="tempo",
    note=("The engine does not apply a speed multiplier, so Voice Studio renders at its native "
          "pace and applies one pitch-preserving tempo pass to the joined artifact."),
)


def _family_registry():
    """Import the family registry whichever way this module was loaded.

    The app imports this file as part of the `backend` package; the standalone
    audit validator (`components/voice/audit_contract_runtime.py`) loads it from
    its path with `app.backend` on `sys.path`, so the relative import is not
    available there. Both are real callers. This is a function rather than a
    module-level import because `catalog` imports this module: a module-level
    import would be a cycle, while this runs only while serving a request.
    """
    try:
        from . import catalog
    except ImportError:  # loaded standalone (audit validator, release checks)
        from app.backend import catalog
    return catalog


def speed_policy_for(family: str, repo: str = "") -> SpeedPolicy:
    """The speed contract for one family. Never None: every family has a way to honour speed.

    The delivery comes from the family's own catalog entry (`native_speed`), so a
    new family states its engine's behaviour in the registry instead of being
    silently omitted from a set in this file. A family that is not in the
    registry at all - `policy_for` answers for those too, with the conservative
    default section budget, so that a newly adopted family is sectioned from day
    one - takes the tempo path. That is the safe answer for an unknown engine:
    one pitch-preserving pass on the finished artifact is audible and correct,
    while claiming the engine applies the multiplier itself would silently drop
    the caller's speed control.
    """
    entry = _family_registry().FAMILIES.get(family)
    return SPEED_NATIVE if entry is not None and entry.native_speed else SPEED_TEMPO


@dataclass(frozen=True)
class LongFormPolicy:
    section_max_characters: int
    join_pause_seconds: float
    note: str
    split_method: str = "sentence_safe"
    customer_submits_complete_script: bool = True
    # Set only where the engine itself stops working past a limit. The Models page and the gateway
    # both need to tell "our chosen budget" apart from "the engine's own cliff".
    hard_cap_characters: Optional[int] = None
    # "family_policy" (measured or engine-derived for this family) or "conservative_default".
    origin: str = "family_policy"
    # The integrated loudness every section is brought to before joining, in LUFS, or None to join
    # the sections at exactly the level the engine returned them. Per family, because it is a
    # delivery decision about that lane's output, and a family whose engine already levels its
    # sections can say so instead of being re-levelled to fix nothing.
    section_loudness_target_lufs: Optional[float] = SECTION_LOUDNESS_TARGET_LUFS
    # The true-peak ceiling the levelling pass may not cross. See SECTION_LOUDNESS_TARGET_LUFS.
    section_true_peak_ceiling_dbtp: float = SECTION_TRUE_PEAK_CEILING_DBTP
    # The loudness range the pass may leave inside one section. See SECTION_LOUDNESS_TARGET_LUFS.
    section_loudness_range_lu: float = SECTION_LOUDNESS_RANGE_LU

    def serialize(self, *, source: str = "runtime_default") -> dict:
        payload = asdict(self)
        payload["join_pause_milliseconds"] = round(
            self.join_pause_seconds * 1000
        )
        payload["source"] = source
        return payload


def production_boundary_pause_seconds(boundary: str) -> float:
    """The join pause at one boundary (sentence / paragraph / soft), before the final tempo pass.
    One rule for every family (owner decision 2026-10-03)."""
    pauses = {
        "sentence": UNIFORM_SENTENCE_JOIN_PAUSE_SECONDS,
        "paragraph": UNIFORM_PARAGRAPH_JOIN_PAUSE_SECONDS,
        "soft": UNIFORM_SOFT_JOIN_PAUSE_SECONDS,
    }
    return pauses[boundary]


# A caller may ask for its own sentence / paragraph pause (`sentence_pause_sec`,
# `paragraph_pause_sec`, owner decision 2026-10-03: Sleep wants 1.0 s at a paragraph). Bounds the API
# enforces and the lane contract publishes.
REQUESTED_PAUSE_MINIMUM_SECONDS = 0.0
REQUESTED_PAUSE_MAXIMUM_SECONDS = 3.0


def default_boundary_pauses() -> dict[str, float]:
    """The sentence / paragraph / mid-sentence pause every model joins at when the caller asks none."""
    return {boundary: production_boundary_pause_seconds(boundary)
            for boundary in ("sentence", "paragraph", "soft")}


def requested_boundary_pauses(params: dict) -> dict[str, float]:
    """The join pauses for one request: its own sentence / paragraph pause where it asked one, the
    uniform rule for the rest."""
    pauses = default_boundary_pauses()
    for boundary in ("sentence", "paragraph"):
        value = params.get(f"{boundary}_pause_sec")
        if value is not None:
            pauses[boundary] = float(value)
    return pauses


def qwen_17b_assembly_invariants(repo: str) -> Optional[dict[str, object]]:
    """The Qwen 1.7B assembly its production audit measured (the audit checks compare the record
    to it; the runtime reads only whether it applies). Its joins are the measured 300/600/180 ms;
    the runtime now joins at the uniform rule (`production_boundary_pause_seconds`)."""
    if "1.7b-base" not in repo.rsplit("/", 1)[-1].lower():
        return None
    return {
        "private_join_pause_milliseconds": round(OMNIVOICE_JOIN_PAUSE_SECONDS * 1000),
        "private_paragraph_join_pause_milliseconds": round(
            OMNIVOICE_PARAGRAPH_JOIN_PAUSE_SECONDS * 1000
        ),
        "private_soft_join_pause_milliseconds": round(OMNIVOICE_SOFT_JOIN_PAUSE_SECONDS * 1000),
        "private_edge_destructive_trim": False,
        "private_speech_crossfade": False,
    }


def _runtime_default(family: str, repo: str) -> "LongFormPolicy":
    """This family's own sectioning, read from the family registry.

    Every shipped family states its budget, its join pause, where the number came
    from and any checkpoint-specific refinement on its `catalog.FAMILIES` entry.
    This used to be a chain of `if family == ...` branches in this file, nine of
    which named families cut in ST-13.B: unreachable, but a hazard the moment one
    of those families were re-adopted, because it would silently take a policy
    nobody had measured since it was cut.

    A family the registry does not know still gets the conservative default.
    That is deliberate: `policy_for` answers for a newly adopted family so its
    script is sectioned from day one, and the published 280 is the smallest
    budget any measured family uses.
    """
    entry = _family_registry().FAMILIES.get(family)
    if entry is None or not entry.section_max_characters:
        return _conservative_default()
    policy = LongFormPolicy(
        section_max_characters=entry.section_max_characters,
        join_pause_seconds=entry.join_pause_seconds,
        note=entry.section_note,
        hard_cap_characters=entry.section_hard_cap_characters,
        origin=entry.section_origin,
        section_loudness_target_lufs=entry.section_loudness_target_lufs,
        section_true_peak_ceiling_dbtp=entry.section_true_peak_ceiling_dbtp,
        section_loudness_range_lu=entry.section_loudness_range_lu,
    )
    name = repo.rsplit("/", 1)[-1].lower()
    for refinement in entry.section_refinements:
        if refinement.marker and refinement.marker in name:
            return LongFormPolicy(
                section_max_characters=(
                    refinement.section_max_characters or policy.section_max_characters
                ),
                join_pause_seconds=(
                    refinement.join_pause_seconds or policy.join_pause_seconds
                ),
                note=refinement.note or policy.note,
                hard_cap_characters=policy.hard_cap_characters,
                origin=policy.origin,
                section_loudness_target_lufs=policy.section_loudness_target_lufs,
                section_true_peak_ceiling_dbtp=policy.section_true_peak_ceiling_dbtp,
                section_loudness_range_lu=policy.section_loudness_range_lu,
            )
    return policy


def _conservative_default() -> "LongFormPolicy":
    return LongFormPolicy(
        section_max_characters=CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS,
        join_pause_seconds=DEFAULT_JOIN_PAUSE_SECONDS,
        origin="conservative_default",
        note=(
            "This family has no section-size measurement of its own yet, so Voice Studio renders "
            "conservative sections instead of sending a whole script to an engine "
            "whose input limit is unknown. 280 is the smallest budget any measured family uses, "
            "so no family is sectioned more coarsely than one we have evidence for."
        ),
    )


def policy_for(
    family: str,
    repo: str,
    *,
    audited_section_max_characters: object = None,
    audited_default_section_max_characters: object = None,
    uniform: bool = True,
) -> Optional[dict]:
    """Return the effective runtime policy, including a valid audit override.

    Never None for a known family: a missing policy used to mean "one request for the whole
    script", which is the failure this function exists to prevent.

    ``uniform`` applies the owner's one narration rule (2026-10-03): sections of at most
    `UNIFORM_SECTION_MAX_CHARACTERS` and the uniform sentence join. ``uniform=False`` is the family's
    measured / audited policy, which is what the model audits describe (`audit_contract_runtime`).
    """
    policy = _runtime_default(family, repo)
    # The published `source` is what a person reads on the Models page, so it names the evidence
    # rather than the code path that produced it.
    source = {"conservative_default": "conservative_default",
              "measured": "measured_fleet",
              "engine_input_limit": "engine_input_limit"}.get(policy.origin, "runtime_default")

    # An audit that publishes BOTH a bounded maximum and a smaller whole-number Auto default is
    # stating the runtime section size separately from the ceiling: the Auto default is what a
    # script is split at, and the maximum is the largest override a caller may ask for. Whether a
    # given record is allowed to make that statement is a provenance question, and it is answered
    # before these numbers arrive (`model_audits.qwen_17b_production_v2_limits`). Gating this on a
    # repo id here is what made the measured Qwen 1.7B policy inert for the tier the app ships:
    # the id named a retired 8-bit checkpoint, so the shipped 4-bit build silently fell through to
    # the family default.
    if (
        type(audited_section_max_characters) is int
        and type(audited_default_section_max_characters) is int
        and 230 <= audited_default_section_max_characters <= audited_section_max_characters
    ):
        policy = LongFormPolicy(
            section_max_characters=audited_default_section_max_characters,
            join_pause_seconds=policy.join_pause_seconds,
            hard_cap_characters=policy.hard_cap_characters,
            origin=policy.origin,
            note="The model's audited Auto section size applies. " + policy.note,
            section_loudness_target_lufs=policy.section_loudness_target_lufs,
            section_true_peak_ceiling_dbtp=policy.section_true_peak_ceiling_dbtp,
            section_loudness_range_lu=policy.section_loudness_range_lu,
        )
        return _published_policy(policy, family, source="model_audit", uniform=uniform)

    try:
        audited_limit = int(audited_section_max_characters)
    except (TypeError, ValueError):
        audited_limit = 0
    # An audit may choose a finer section, but never past an engine's own input limit: that is the
    # point where the engine stops working, not a quality preference.
    hard_cap = policy.hard_cap_characters
    if 40 <= audited_limit <= 20_000 and (hard_cap is None or audited_limit <= hard_cap):
        policy = LongFormPolicy(
            section_max_characters=audited_limit,
            join_pause_seconds=policy.join_pause_seconds,
            hard_cap_characters=hard_cap,
            origin=policy.origin,
            note=(
                "The exact model audit overrides the family default. "
                + policy.note
            ),
            section_loudness_target_lufs=policy.section_loudness_target_lufs,
            section_true_peak_ceiling_dbtp=policy.section_true_peak_ceiling_dbtp,
            section_loudness_range_lu=policy.section_loudness_range_lu,
        )
        source = "model_audit"
    return _published_policy(policy, family, source=source, uniform=uniform)


def _published_policy(policy: LongFormPolicy, family: str, *, source: str,
                      uniform: bool = True) -> dict:
    """One policy, as the catalogue publishes it: the section budget, the engine's limit if it has
    one, and how this family's speed control is delivered."""
    if uniform:
        measured = policy.section_max_characters
        policy = replace(
            policy,
            section_max_characters=min(measured, UNIFORM_SECTION_MAX_CHARACTERS),
            join_pause_seconds=UNIFORM_SENTENCE_JOIN_PAUSE_SECONDS,
            note=(f"Every voice model renders sections of up to {UNIFORM_SECTION_MAX_CHARACTERS} "
                  f"characters joined at {UNIFORM_SENTENCE_JOIN_PAUSE_SECONDS:g} s after a sentence "
                  f"and {UNIFORM_PARAGRAPH_JOIN_PAUSE_SECONDS:g} s at a paragraph break "
                  f"(owner decision 2026-10-03; this model was measured at {measured}). "
                  + policy.note),
        )
    payload = policy.serialize(source=source)
    payload["speed"] = asdict(speed_policy_for(family))
    return payload
