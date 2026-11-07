"""The Music job runner: durable job state and the audio artifact.

The parent owns everything durable — job records, output files, cancellation
intent.  Both engines run outside this process: Stable Audio 3 as a short-lived
MLX subprocess per job (``stable_audio.py``) and YuE2 in its own pinned runtime
through a bridge (``yue2.py``).  A cancel stops that subprocess, and a crash or a
fatal MLX abort in an engine cannot take the HTTP worker down with it.  Neither
keeps a model resident between jobs, so there is nothing for an idle timer to
release.
"""
from __future__ import annotations

from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import subprocess
import threading
import time
from uuid import uuid4

from components import audio_encode, model_defaults

from . import catalog, yue2, cache as cache_module, stable_audio


# The worker's default model.  Every value is read from that model's own catalog
# entry, so the default engine's identity and the contract this module enforces
# cannot drift apart.  A request naming another model is validated and run from
# *that* model's entry (_VALIDATORS, _runner_for).
DEFAULT_MODEL = catalog.require_model(catalog.DEFAULT_REPO)
MODEL_ID = DEFAULT_MODEL.repo
AUDIO_FORMAT = "wav"
MEDIA_TYPE = "audio/wav"
# What each engine writes natively (Stable Audio 3 -> WAV, YuE2 -> FLAC); every job record and the
# artifact route read the delivered file's own type, never one constant for both engines.
YUE2_AUDIO_FORMAT = "flac"
YUE2_MEDIA_TYPE = yue2.MEDIA_TYPE
MEDIA_TYPE_BY_SUFFIX = {".wav": "audio/wav", ".flac": "audio/flac",
                        ".m4a": audio_encode.MEDIA_TYPE}
# Optional request field. Absent = the engine's native lossless file (today's behaviour).
# ``wav`` / ``flac`` name that native file; ``m4a`` is AAC-LC 256 kbps (audio_encode "music").
OUTPUT_FORMATS = ("wav", "flac", "m4a")

# Single-pass ceiling a caller gets when it does not name the host's cap.  The Hub
# canonicalises a Music request before a worker sees it, so both sides need one
# agreed number; ``submit`` always passes the model's own cap for this host
# (_tier_cap_for_this_host).  This is Stable Audio 3's own maximum, the default
# model's, not a claim about any other model's.
DEFAULT_TIER_CAP_SECONDS = catalog.SA3_MAX_DURATION_SECONDS
TERMINAL = {"done", "error", "cancelled"}


def runtime_identity() -> str:
    """Deterministic digest of the exact engine packages actually installed."""
    facts = {"python": platform.python_version()}
    for name in ("mlx", "mlx-lm", "torch", "transformers"):
        try:
            facts[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            facts[name] = None
    return sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def _finite_number(value, *, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return number


def _default(model: catalog.ModelEntry, key: str):
    """One model's own generation default, refused by name when it omits it.

    The alternative — falling back to the worker's default model — is how a newly
    added model used to be published with another model's numbers.
    """
    if key not in model.defaults:
        raise ValueError(
            f"{model.repo} publishes no {key!r} generation default; add it to its "
            "ModelEntry in components/music/app/backend/catalog.py")
    return model.defaults[key]


def _limit(model: catalog.ModelEntry, key: str):
    """One model's own generation limit, refused by name when it omits it."""
    if key not in model.limits:
        raise ValueError(
            f"{model.repo} publishes no {key!r} generation limit; add it to its "
            "ModelEntry in components/music/app/backend/catalog.py")
    return model.limits[key]


def _output_format(raw: dict) -> dict:
    """The request's optional ``output_format``, echoed only when it was supplied.

    Echoing nothing for an absent field keeps the normalised params (which the Hub compares on
    recovery, ``params == accepted_params``) identical to what an older caller always got.
    """
    value = raw.get("output_format")
    if value is None:
        return {}
    if not isinstance(value, str) or value.strip().lower() not in OUTPUT_FORMATS:
        raise ValueError("output_format must be " + ", ".join(OUTPUT_FORMATS))
    return {"output_format": value.strip().lower()}


def _validate_stable_audio(raw: dict, model: catalog.ModelEntry, *,
                           tier_cap_seconds: int = DEFAULT_TIER_CAP_SECONDS,
                           data_dir=None) -> dict:
    """Stable Audio 3 parameters: instrumental only, up to this model's own cap.

    ``tier_cap_seconds`` is accepted for the shared validator signature and ignored:
    this engine has no hardware tier table, so its single-pass cap is its own.

    ``data_dir`` is this Studio's state directory. A parameter the request **omits** takes the
    value in force for this model — the operator's stored default over what the model states —
    and a parameter the request supplies always wins verbatim.
    """
    defaults = defaults_in_force(model, data_dir)
    prompt = raw.get("prompt")
    if (not isinstance(prompt, str) or not prompt.strip()
            or len(prompt) > PROMPT_MAX_CHARACTERS):
        raise ValueError(f"Prompt must contain 1-{PROMPT_MAX_CHARACTERS} characters")
    duration = raw.get("duration_seconds",
                       raw.get("duration", defaults["duration_seconds"]))
    duration = _finite_number(duration, name="duration",
                              low=float(_limit(model, "min_duration_seconds")),
                              high=float(_limit(model, "max_duration_seconds")))
    output_format = _output_format(raw)
    steps = raw.get("steps", raw.get("inference_steps", defaults["steps"]))
    if (type(steps) is not int or not _limit(model, "min_steps")
            <= steps <= _limit(model, "max_steps")):
        raise ValueError(
            f"Steps must be an integer between {_limit(model, 'min_steps')} and "
            f"{_limit(model, 'max_steps')}")
    guidance = _finite_number(
        raw.get("guidance_scale", raw.get("cfg", defaults["guidance_scale"])),
        name="guidance_scale", low=float(_limit(model, "min_guidance")),
        high=float(_limit(model, "max_guidance")))
    seed = raw.get("seed", defaults["seed"])
    if seed is None:
        seed = -1
    if (type(seed) is not int
            or not _limit(model, "min_seed") <= seed <= _limit(model, "max_seed")):
        raise ValueError("Seed must be -1 or a 32-bit unsigned integer")
    return {
        "repo": model.repo,
        "model_revision": model.revision,
        "prompt": prompt.strip(), "duration_seconds": duration, "steps": steps,
        "guidance_scale": guidance, "seed": seed,
        # The engine has no vocals; keep the shared job shape honest about that.
        "instrumental": True, "lyrics": "[Instrumental]", "vocal_language": "unknown",
        "dit_model": _default(model, "dit_model"), "lm_model": "",
        "tier_cap_seconds": int(_limit(model, "single_pass_max_seconds")),
        "exceeds_single_pass": False,
        **output_format,
    }


def _validate_yue2(raw: dict, model: catalog.ModelEntry, *,
                   tier_cap_seconds: int = DEFAULT_TIER_CAP_SECONDS,
                   data_dir=None) -> dict:
    """YuE2 parameters, bounded by this model's own limits.

    Three things are this engine's alone and are therefore validated here rather than shared:

    * **`mode`** is the port's `cot` — how much planning the song gets. `off` generates straight
      from style and lyrics, `melody` plans a lead sheet, `full` plans a scored arrangement. This
      lane names it `mode` because that is the vocabulary every other music lane here uses.
    * **The duration is a token budget.** The AR stage emits one codec frame per token, so the
      port's own `max_tokens` is what decides how long the song is — measured at ~25 tokens per
      second of 48 kHz audio (3000 tokens → 120.0 s). The caller states seconds, because that is
      what a person knows; this converts, and the receipt is
      `qualification/yue2-music-results-20260927/`.
    * **`lyrics` are required.** The engine sings: an empty lyric sheet is an instrumental request
      sent to a model that has no instrumental switch, and the port's own context tokens for that
      do not exist. Refusing by name beats rendering a wordless vocal.

    ``tier_cap_seconds`` is accepted for the shared signature and ignored: this engine publishes no
    hardware tier table, so its single-pass cap is its own.
    """
    defaults = defaults_in_force(model, data_dir)
    revision = raw.get("model_revision")
    if revision is not None and revision != model.revision:
        raise ValueError(f"Only the pinned {model.label} revision is available")
    prompt = raw.get("prompt")
    if (not isinstance(prompt, str) or not prompt.strip()
            or len(prompt) > PROMPT_MAX_CHARACTERS):
        raise ValueError(f"Prompt must contain 1-{PROMPT_MAX_CHARACTERS} characters")
    lyrics = raw.get("lyrics", "")
    if not isinstance(lyrics, str) or not lyrics.strip():
        raise ValueError(
            "YuE2 sings from lyrics: send a lyric sheet with [Verse] and [Chorus] markers. It has "
            "no instrumental switch — use Stable Audio 3 for a piece without a vocal.")
    if len(lyrics) > LYRICS_MAX_CHARACTERS:
        raise ValueError(f"Lyrics must contain at most {LYRICS_MAX_CHARACTERS} characters")
    language = raw.get("vocal_language", "unknown")
    if not isinstance(language, str) or len(language) > 32 or any(
            ord(char) < 32 for char in language):
        raise ValueError("vocal_language must be a language name or code of at most 32 characters")
    language = language.strip() or "unknown"
    # SongRequest has no language field: its style carries the language alongside the lyrics.
    language_word = {
        "en": "English", "zh": "Mandarin", "yue": "Cantonese", "ja": "Japanese",
        "ko": "Korean", "es": "Spanish", "ru": "Russian",
    }.get(language.lower(), language)
    style = prompt.strip()
    if language.lower() != "unknown":
        if (style.casefold() != language_word.casefold()
                and not style.casefold().startswith(language_word.casefold() + ",")):
            style = f"{language_word}, {style}"
        if len(style) > PROMPT_MAX_CHARACTERS:
            raise ValueError(f"Prompt including vocal language must contain at most {PROMPT_MAX_CHARACTERS} characters")
    mode = raw.get("mode", raw.get("cot", defaults["mode"]))
    if mode not in ("off", "melody", "full"):
        raise ValueError("mode must be off, melody or full")
    duration = raw.get("duration_seconds",
                       raw.get("duration", defaults["duration_seconds"]))
    duration = _finite_number(duration, name="duration",
                              low=float(_limit(model, "min_duration_seconds")),
                              high=float(_limit(model, "max_duration_seconds")))
    steps = raw.get("steps", raw.get("inference_steps", defaults.get("steps")
                                     or model.inference_steps))
    if (type(steps) is not int or not _limit(model, "min_steps")
            <= steps <= _limit(model, "max_steps")):
        raise ValueError(
            f"Steps must be an integer between {_limit(model, 'min_steps')} and "
            f"{_limit(model, 'max_steps')}")
    guidance = _finite_number(
        raw.get("guidance_scale", raw.get("cfg", defaults.get("guidance_scale")
                                          or model.guidance_scale)),
        name="guidance_scale", low=float(_limit(model, "min_guidance")),
        high=float(_limit(model, "max_guidance")))
    precision = raw.get("precision", defaults.get("precision", "8bit"))
    if precision not in ("bf16", "8bit", "4bit"):
        raise ValueError("precision must be bf16, 8bit or 4bit")
    seed = raw.get("seed", defaults["seed"])
    if seed is None:
        seed = -1
    if (type(seed) is not int
            or not _limit(model, "min_seed") <= seed <= _limit(model, "max_seed")):
        raise ValueError("Seed must be -1 or a non-negative integer")
    # The port stops at its own end token, so a request can come back shorter than asked and never
    # longer — said here because the caller's seconds are a budget, not a promise.
    max_tokens = max(1, min(int(round(duration * catalog.YUE2_TOKENS_PER_SECOND)), 24576))
    output_format = _output_format(raw)
    return {
        "repo": model.repo,
        "model_revision": model.revision,
        "prompt": style, "duration_seconds": duration, "steps": steps,
        "guidance_scale": guidance, "seed": seed,
        "mode": mode, "lyrics": lyrics, "precision": precision,
        "max_tokens": max_tokens,
        "min_tokens": max(1, min(max_tokens, int(round(duration * 0.5 * catalog.YUE2_TOKENS_PER_SECOND)))),
        # YuE2 always sings; the selected language is retained in the job and carried in style.
        "instrumental": False, "vocal_language": language,
        "dit_model": "", "lm_model": "",
        "tier_cap_seconds": int(_limit(model, "single_pass_max_seconds")),
        "exceeds_single_pass": False,
        **output_format,
    }


def yue2_runtime_python() -> Path:
    """The interpreter that runs the YuE2 bridge: the engine's own pinned runtime.

    Resolution order, and why it is not a guess:

    1. ``STUDIOHUB_YUE2_RUNTIME`` — the operator's override, for a development checkout.
    2. ``runtimes/music-yue2`` beside **this worker's own runtime**, which is how the app stages
       it: the Hub puts sibling runtimes next to the one it launches the worker with.
    3. ``runtimes/music-yue2`` beside this file's component tree, for a source checkout.
    4. ``.build/yue2-runtime`` — the same tree before staging, which is what a development
       checkout has.

    A path that does not exist is returned rather than raised on: the bridge's own failure message
    names what is missing, and it is more specific than anything this resolver could say.
    """
    import os
    from pathlib import Path as _Path
    import sys as _sys

    override = os.environ.get("STUDIOHUB_YUE2_RUNTIME")
    candidates = []
    if override:
        candidates.append(_Path(override) / "bin" / "python3")
    # The worker's runtime is the Voice runtime, `.../runtimes/voice/bin/python3`, so its
    # *parent* is where the sibling runtimes live.
    candidates.append(_Path(_sys.executable).resolve().parents[1] / "music-yue2" / "bin" / "python3")
    candidates.append(_Path(__file__).resolve().parents[4] / "runtimes" / "music-yue2" / "bin" / "python3")
    # A development checkout keeps its built runtimes in `.build/`, which is where
    # `packaging/yue2-runtime/build.sh` writes one; the staged name is what the app uses.
    candidates.append(_Path(__file__).resolve().parents[4] / ".build" / "yue2-runtime" / "bin" / "python3")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


# runtime -> request validator.  A model whose runtime is missing here is refused by
# name in validate_request instead of being validated as another engine.
_VALIDATORS = {
    catalog.RUNTIME_STABLE_AUDIO: _validate_stable_audio,
    catalog.RUNTIME_YUE2: _validate_yue2,
}

LANE_CONTRACT_SCHEMA = "studiohub.lane-contract.v1"
PROMPT_MAX_CHARACTERS = 4000
LYRICS_MAX_CHARACTERS = 8000
# runtime -> the parameters that runtime's validator accepts *in addition to* the shared ones.
# Keyed the same way `_VALIDATORS` is, and `tests/test_music_lane_contract.py` asserts the two
# tables cover the same runtimes, so a runtime added to one and not the other fails loudly rather
# than publishing a contract that silently omits half its surface.
#
# Each entry's `default` is filled from the model's own `defaults` in `lane_parameters`: a control
# is published with the value this model states for it, and the operator's stored value overrides
# it, exactly as for the shared parameters.
_CONTRACT_EXTRAS: dict[str, tuple[dict, ...]] = {
    # Stable Audio 3 can only produce instrumentals: `_validate_stable_audio` forces
    # `instrumental=True` and `lyrics="[Instrumental]"` whatever is sent, so offering those
    # parameters would be offering controls the lane discards.
    catalog.RUNTIME_STABLE_AUDIO: (),
    catalog.RUNTIME_YUE2: (
        # Every one of these is read by `_validate_yue2` and passed to the port, so the contract
        # offers exactly what the engine honours.
        {"name": "mode", "type": "string", "required": False,
         "enum": ["off", "melody", "full"],
         "note": "off generates from style and lyrics; melody plans a lead sheet first; full "
                 "plans a scored arrangement. The three measured runs used off."},
        {"name": "lyrics", "type": "string", "required": True,
         "max_characters": LYRICS_MAX_CHARACTERS,
         "note": "this engine sings; there is no instrumental switch"},
        {"name": "vocal_language", "type": "string", "required": False,
         "request_only": True, "max_characters": 32,
         "enum": ["en", "zh", "yue", "ja", "ko", "es", "ru"],
         "note": "Language name or code, added to style; supply lyrics in the same language. "
                 "The owner accepted English, Mandarin, Cantonese, Japanese, Korean, Spanish, "
                 "and Russian clips."},
        {"name": "precision", "type": "string", "required": False,
         "enum": ["8bit", "bf16", "4bit"],
         "note": "8bit is the measured configuration; 4bit needs a conversion this package does "
                 "not ship"},
    ),
}


def _json_number(value: float):
    """A duration as JSON: an integer when it is one, so a ladder rung reads `30`, not `30.0`."""
    return int(value) if float(value).is_integer() else round(float(value), 3)


def duration_ladder(model: catalog.ModelEntry) -> list[dict]:
    """The durations this model itself offers, built from its own generation limits.

    A ladder has to be the model's, not the worker's. The single-pass cap and the absolute
    maximum differ per model — YuE2 renders up to 360 s in one pass, Stable Audio 3
    stops at 380 s — so a fixed list of rungs would offer one of them a
    duration it refuses. Every rung here is therefore inside this model's own
    ``min_duration_seconds``..``max_duration_seconds``; the model's stated default, its
    single-pass maximum and its absolute maximum are always rungs, and the rest are that default
    doubled until the model's own ceiling is reached.

    ``exceeds_single_pass`` compares a rung with the **model's** ``single_pass_max_seconds``, the
    number the catalogue publishes; this host's own tier cap can be lower still, which is why the
    request is validated against the host cap as well (``_tier_cap_for_this_host``).
    """
    low = float(_limit(model, "min_duration_seconds"))
    high = float(_limit(model, "max_duration_seconds"))
    single_pass = float(_limit(model, "single_pass_max_seconds"))
    stated = min(max(float(_default(model, "duration_seconds")), low), high)
    rungs = {low, stated, min(single_pass, high), high}
    rung = stated
    while rung > 0 and rung * 2 <= high:
        rung *= 2
        rungs.add(rung)
    ladder = []
    for value in sorted(rungs):
        rung = {"seconds": _json_number(value),
                "label": f"{_json_number(value)} s",
                "exceeds_single_pass": value > single_pass}
        if rung["exceeds_single_pass"]:
            rung["note"] = ("beyond this model's single-pass maximum: the worker renders it in "
                            "sections")
        ladder.append(rung)
    return ladder


def defaults_in_force(model: catalog.ModelEntry, data_dir=None) -> dict:
    """Every published parameter's default: what this model states, over what was chosen.

    ``data_dir`` is this Studio's state directory, where ``model_defaults.json`` lives. With no
    ``data_dir`` nothing has been chosen on this machine as far as this call can see, so the
    answer is exactly what the lane states.

    One call, two readers: the validators apply these values to a request that omits a parameter,
    and :func:`lane_contract` publishes them in each parameter's ``default``. They therefore
    cannot disagree — a contract can never advertise a number this worker would refuse.

    ``model_defaults.stated``/``resolved`` rather than the *editable* surface the editor renders:
    a parameter with no control (a one-option enum has nothing to choose between) is still a value
    the request has to carry, and building this map from the settable subset would drop it.
    """
    parameters = lane_parameters(model)
    if data_dir is None:
        return model_defaults.stated(parameters)
    resolved, _not_used = model_defaults.resolved(data_dir, model.repo, parameters)
    return resolved


def output_format_parameter(model: catalog.ModelEntry) -> dict:
    """The optional delivery format this lane honours, published so a caller can see it.

    ``request_only``: a per-job choice (Story asks per asset type), never an operator default —
    ``model_defaults.editable`` leaves it out, so omitting the field always means the native file.
    """
    native = _native_format(model)
    return {"name": "output_format", "type": "string", "required": False,
            "enum": [native, "m4a"], "request_only": True,
            "note": (f"omit for the engine's native lossless {native.upper()}; m4a delivers "
                     "AAC-LC 256 kbps (about 5x smaller) encoded after generation, and falls "
                     "back to the native file if encoding fails")}


def _native_format(model: catalog.ModelEntry) -> str:
    return YUE2_AUDIO_FORMAT if model.runtime == catalog.RUNTIME_YUE2 else AUDIO_FORMAT


def lane_parameters(model: catalog.ModelEntry) -> list[dict]:
    """The parameters this model's lane publishes, from its own limits and defaults.

    One definition read by two consumers that must agree: :func:`lane_contract` publishes it, and
    ``components.model_defaults`` validates an operator's stored default against exactly these
    bounds, enums and character limits. A second copy would be free to disagree with the code
    that rejects the request — which is the thing a published bound exists to prevent.

    The per-runtime split is real, not cosmetic: YuE2 accepts lyrics, a mode and a precision, and
    Stable Audio 3 accepts none of them. A contract that listed the union
    would invite a scheduler to send parameters one of the two silently discards.

    A fresh list of fresh dicts on every call: the caller may set a resolved ``default`` on a
    parameter without that leaking into the next model's contract.
    """
    parameters = [
        {"name": "prompt", "type": "string", "required": True,
         "max_characters": PROMPT_MAX_CHARACTERS},
        {"name": "duration_seconds", "type": "number", "required": False,
         "minimum": _limit(model, "min_duration_seconds"),
         "maximum": _limit(model, "max_duration_seconds"),
         "default": _default(model, "duration_seconds"),
         "ladder": duration_ladder(model),
         "note": "a duration beyond this host's single-pass cap is rendered in sections"},
        {"name": "steps", "type": "integer", "required": False,
         "minimum": _limit(model, "min_steps"), "maximum": _limit(model, "max_steps"),
         "default": _default(model, "steps")},
        {"name": "guidance_scale", "type": "number", "required": False,
         "minimum": _limit(model, "min_guidance"), "maximum": _limit(model, "max_guidance"),
         "default": _default(model, "guidance_scale")},
        {"name": "seed", "type": "integer", "required": False,
         "minimum": _limit(model, "min_seed"), "maximum": _limit(model, "max_seed"),
         "default": _default(model, "seed"), "note": "-1 draws a random seed"},
        output_format_parameter(model),
    ]
    for extra in _CONTRACT_EXTRAS.get(model.runtime, ()):
        entry = dict(extra)
        if entry.get("required") or entry.get("request_only"):
            # **A required parameter is per-request content, so it has no default.** YuE2's lyrics
            # are the case: the engine sings, an empty sheet is refused by name, and publishing a
            # default for it would offer the operator a value the lane never accepts as a choice.
            # The video lane already states this rule for its own `prompt`.
            entry.pop("default", None)
        else:
            entry["default"] = _default(model, extra["name"])
        parameters.append(entry)
    return parameters


def lane_contract(model: catalog.ModelEntry, data_dir=None) -> dict:
    """The Music lane's dispatch contract for one model.

    What a scheduler needs in order to *ask for* this lane's work. Every bound is the one
    ``validate_request`` enforces, read from this model's own ``limits``/``defaults`` through the
    same ``_limit``/``_default`` helpers the validators use — so the contract cannot drift from
    the code that accepts or refuses the request, and a newly adopted music model publishes a
    correct contract with no edit here.

    ``data_dir`` resolves each settable parameter's ``default`` to the value in force on this
    machine — what the model states, overridden by what the operator chose. It comes from the same
    :func:`defaults_in_force` call the validators apply, so the catalog cannot publish a default
    the worker would not itself accept.
    """
    parameters = lane_parameters(model)
    in_force = defaults_in_force(model, data_dir)
    for parameter in parameters:
        if parameter["name"] in in_force:
            parameter["default"] = in_force[parameter["name"]]
    contract = {
        "schema": LANE_CONTRACT_SCHEMA,
        "modality": "music",
        # The canonical token, not this worker's own `music.text_to_music` label: the Hub
        # normalises `hub_operation` to `OPERATION_BY_MODALITY` and keeps the worker's wording in
        # `hub_reported_operation`, but it passes this contract through verbatim, so the canonical
        # spelling is the one a reader can join on.
        "operation": "music.generation",
        "parameters": parameters,
        "artifact": {"modality": "music",
                     "media_type": (YUE2_MEDIA_TYPE if model.runtime == catalog.RUNTIME_YUE2
                                    else MEDIA_TYPE),
                     "extension": _native_format(model), "per_item": 1},
        "states": {
            "accepted": ["queued"],
            "active": ["running", "cancel_requested"],
            "terminal": ["done", "error", "cancelled"],
            "uncertain": "uncertain",
            "uncertain_is_terminal": False,
            "discard_supported": True,
        },
        "cancel": {
            "supported": True,
            "request": "DELETE /api/hub/jobs/{batch_id}",
            "semantics": ("stops the render at the next safe point; a long-form job is rendered in "
                          "sections, so sections already written are kept and a cancelled item is "
                          "not evidence that no audio was produced"),
        },
    }
    if model.measured_runtime:
        contract["runtime_seconds"] = model.measured_runtime
    return contract



def validate_request(raw: dict, tier_cap_seconds: int = DEFAULT_TIER_CAP_SECONDS,
                     data_dir=None) -> dict:
    """One request, resolved against the model it names and this Mac's stored defaults.

    ``data_dir`` is where ``model_defaults.json`` lives (the manager's state directory). A
    parameter the request **omits** takes the value in force for this model; a parameter the
    request **supplies** always wins. With no ``data_dir`` there are no stored defaults to read,
    so the answer is exactly what the lane states.
    """
    if not isinstance(raw, dict):
        raise ValueError("Music request must be an object")
    model = catalog.require_model(str(raw.get("repo") or MODEL_ID))
    validator = _VALIDATORS.get(model.runtime)
    if validator is None:
        raise ValueError(
            f"{model.repo} declares runtime {model.runtime!r}, which this worker has no "
            "request validator for; add one in "
            "components/music/app/backend/generation.py")
    return validator(raw, model, tier_cap_seconds=tier_cap_seconds, data_dir=data_dir)


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _activity_time(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _activity_progress(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    if not math.isfinite(parsed):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


def _activity_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def final_matches(job: dict, path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if job.get("output_path") != str(path):
            return False
        return (path.stat().st_size == job.get("bytes")
                and sha256(path.read_bytes()).hexdigest() == job.get("sha256"))
    except OSError:
        return False


class GenerationManager:
    """Durable job state for one Music worker."""

    def __init__(self, data: Path, *, outputs: Path | None = None,
                 manifest_path: str | None = None, library_dir: str | None = None,
                 runtime_sha256: str | None = None, machine: str = "local") -> None:
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.outputs = Path(outputs) if outputs else self.data / "outputs"
        self.outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.outputs.is_symlink():
            raise ValueError("Music output directory cannot be a symlink")
        self.state_file = self.data / "jobs.json"
        self.machine = machine
        self.manifest_path = manifest_path
        self.library_dir = library_dir
        # The worker's default engine is built eagerly, so a broken default
        # package still fails the worker at startup exactly as it always did.
        self.cache = cache_module.MusicCache(library=library_dir,
                                             manifest_path=manifest_path)
        # Every other model's package view is built from its own catalog entry on
        # first use (see _cache_for).  One broken package is recorded against its
        # own model instead of raised, because it must not stop the others: a
        # missing YuE2 package must not take the whole Music worker down with it.
        self._model_caches: dict[str, object] = {DEFAULT_MODEL.repo: self.cache}
        self._model_cache_errors: dict[str, str] = {}
        self.runtime_sha256 = runtime_sha256 or runtime_identity()
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._restore()

    # -- persisted state -----------------------------------------------------
    def _restore(self) -> None:
        from components import job_history
        try:
            saved = json.loads(self.state_file.read_text())
            if not isinstance(saved, dict):
                raise ValueError("Music job history is malformed")
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Music job history is unreadable; original recovery evidence was preserved"
            ) from exc
        for job_id, job in saved.items():
            if not isinstance(job, dict) or job_id != job.get("id"):
                raise RuntimeError(
                    "Music job history is malformed; original recovery evidence was preserved")
            if job.get("state") not in TERMINAL:
                job.update(state="error",
                           error="Worker restarted before completion",
                           error_code="WORKER_RESTART_UNCERTAIN", finished_at=time.time())
            if job.get("state") == "done" and not final_matches(job, self.final_path(job)):
                job.update(state="error", error="Final audio is unavailable after restart",
                           error_code="OUTPUT_UNAVAILABLE", output_path=None, output_url=None)
            if job_history.expired(job):
                continue                  # finished more than 90 days ago: the record goes
            self.jobs[job_id] = job
        self._persist()

    def _persist(self) -> None:
        from components import job_history
        job_history.prune_daily(self, self.jobs)
        _atomic_json(self.state_file, self.jobs)

    # -- readiness / cache ---------------------------------------------------
    def _runner_for(self, model: catalog.ModelEntry):
        """The job runner for this model's runtime, refused by name when unknown."""
        runners = {
            catalog.RUNTIME_STABLE_AUDIO: self._run_sa3,
            catalog.RUNTIME_YUE2: self._run_yue2,
        }
        try:
            return runners[model.runtime]
        except KeyError:
            raise ValueError(
                f"{model.repo} declares runtime {model.runtime!r}, which this worker "
                "has no runner for; add one in "
                "components/music/app/backend/generation.py") from None

    def _cache_for(self, model: catalog.ModelEntry):
        """``(cache, error)`` for one model, resolved from its own catalog entry.

        The manifest name, package id and package root all come from that entry, so
        a model added later can never be verified or loaded from another model's package.
        A package that cannot be read is recorded against its own model rather than
        raised, so one broken package cannot stop the others.
        """
        if (model.repo not in self._model_caches
                and model.repo not in self._model_cache_errors):
            try:
                # The explicit manifest names the worker's default engine only;
                # every other model resolves its own <package_id>.json.
                explicit = (self.manifest_path if model.repo == DEFAULT_MODEL.repo
                            else None)
                self._model_caches[model.repo] = cache_module.MusicCache(
                    library=self.library_dir, manifest_path=explicit, repo=model.repo)
            except (OSError, ValueError, KeyError) as exc:
                self._model_cache_errors[model.repo] = f"{type(exc).__name__}: {exc}"
        return (self._model_caches.get(model.repo),
                self._model_cache_errors.get(model.repo))

    def cache_for(self, repo: str):
        """The verified package view for one catalog model, or a named refusal."""
        model = catalog.require_model(repo)
        cache, error = self._cache_for(model)
        if cache is None:
            raise ValueError(error or f"Music package {model.package_id} is unavailable")
        return cache

    def preflight(self, repo: str | None = None) -> dict:
        model = catalog.require_model(repo or DEFAULT_MODEL.repo)
        cache, error = self._cache_for(model)
        if cache is None:
            return {"state": "manifest_unavailable",
                    "reason": error or f"{model.package_id} is unavailable"}
        try:
            status = cache.status()
        except (OSError, ValueError, KeyError) as exc:
            return {"state": "manifest_unavailable", "reason": str(exc)}
        if model.runtime not in (catalog.RUNTIME_STABLE_AUDIO, catalog.RUNTIME_YUE2):
            return {"state": "runtime_unsupported",
                    "reason": (f"{model.repo} declares runtime {model.runtime!r}, which "
                               "this worker has no executor for; add one in "
                               "components/music/app/backend/generation.py")}
        # Both engines ship their own runtime: Stable Audio 3 vendors its MLX implementation, and
        # YuE2 runs in its own pinned interpreter (`packaging/yue2-runtime`, staged as
        # `runtimes/music-yue2`) because its port pins a different MLX. The package's files are the
        # whole question here, and a runtime that is genuinely absent is named when a job resolves it.
        return status

    # -- queries -------------------------------------------------------------
    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(self.jobs.values(),
                    key=lambda value: value["created_at"], reverse=True)]

    @staticmethod
    def _activity_projection(job: dict, *, observed_at: float) -> dict:
        """Bound one Music job to the Hub's allowlisted activity contract.

        Only job-store fields are used.  The worker cannot tell a Hub-dispatched
        job from a direct one, so it reports ``source: direct`` and the Hub's
        broker ownership reconciliation remains authoritative.
        """
        params = job.get("params") if isinstance(job.get("params"), dict) else {}
        model = _activity_text(params.get("repo"), 500)
        state = str(job.get("state") or "")
        created_at = _activity_time(job.get("created_at"))
        started_at = _activity_time(job.get("started_at"))
        finished_at = _activity_time(job.get("finished_at"))
        projection = {
            "id": str(job.get("id") or "")[:160], "state": state, "model": model,
            "operation": "music", "progress": _activity_progress(job.get("progress")),
            "source": "direct",
        }
        if created_at is not None:
            projection["created_at"] = created_at
        if started_at is not None:
            projection["started_at"] = started_at
        if state in {"queued", "running"}:
            projection["updated_at"] = observed_at
        else:
            if finished_at is not None:
                projection["finished_at"] = finished_at
            if started_at is not None and finished_at is not None:
                projection["runtime_s"] = max(0.0, finished_at - started_at)
            code = _activity_text(job.get("error_code"), 80)
            if code:
                projection["error_code"] = code
        return projection

    def activity_snapshot(self, observed_at: float | None = None) -> dict:
        """Return the private, bounded activity contract the Hub polls.

        The Music job store is the only source.  An empty store yields a valid
        empty snapshot rather than a 404, so an idle worker still answers.
        """
        observed = float(time.time() if observed_at is None else observed_at)
        with self.lock:
            jobs = [dict(job) for job in self.jobs.values()]
        running = [job for job in jobs if job.get("state") == "running"]
        queued = [job for job in jobs if job.get("state") == "queued"]
        terminal = [job for job in jobs if job.get("state") in TERMINAL]
        active = max(
            running or queued,
            key=lambda job: (job.get("state") == "running",
                             _activity_time(job.get("started_at"))
                             or _activity_time(job.get("created_at")) or 0.0),
            default=None,
        )
        latest = max(
            terminal,
            key=lambda job: (_activity_time(job.get("finished_at"))
                             or _activity_time(job.get("created_at")) or 0.0,
                             _activity_time(job.get("created_at")) or 0.0),
            default=None,
        )
        return {
            "schema": "kh-studio.activity.v1", "studio": "music",
            "observed_at": observed,
            "active": self._activity_projection(active, observed_at=observed) if active else None,
            "latest": self._activity_projection(latest, observed_at=observed) if latest else None,
        }

    # -- submit / cancel -----------------------------------------------------
    def submit(self, raw: dict) -> dict:
        # validate_request owns the request shape; the model only has to be known
        # early enough to pick its own duration cap for this host.
        model = (catalog.require_model(str(raw.get("repo") or MODEL_ID))
                 if isinstance(raw, dict) else None)
        tier_cap = (_tier_cap_for_this_host(model) if model is not None
                    else DEFAULT_TIER_CAP_SECONDS)
        params = validate_request(raw, tier_cap_seconds=tier_cap, data_dir=self.data)
        model = catalog.require_model(params["repo"])
        files = self.preflight(model.repo)
        if files["state"] != "cached":
            raise RuntimeError(f"MODEL_FILES_UNAVAILABLE: {files.get('reason') or files['state']}")
        runner = self._runner_for(model)
        job_id = uuid4().hex
        job = {
            "id": job_id, "mode": "txt2music", "params": params, "state": "queued",
            "progress": 0.0, "created_at": time.time(), "started_at": None,
            "finished_at": None, "generated_at": None, "duration_seconds": None,
            "engine_seconds": None, "output_path": None, "output_url": None,
            "sidecar_path": None, "sha256": None, "bytes": None, "media_type": None,
            "sample_rate_hz": None, "channels": None, "audio_duration_s": None,
            "frames": None, "resolved_seed": None, "dit_model": params["dit_model"],
            "lm_model": params["lm_model"],
            "model_revision": params.get("model_revision") or model.revision,
            "runtime_revision": self.runtime_sha256, "worker_id": "music",
            "machine_id": self.machine, "acknowledged": False,
            "error": None, "error_code": None,
        }
        if model.runtime == catalog.RUNTIME_YUE2:
            # This song's own limit (0.6.170): the bridge stops at it, the Hub copies it onto the batch
            # item and the Gateway polls to it, instead of the music lane's flat 900 s.
            job["deadline_s"] = yue2.job_deadline_s(params.get("duration_seconds"))
        with self.lock:
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            try:
                self._persist()
            except Exception:
                self.jobs.pop(job_id, None)
                self.cancel_events.pop(job_id, None)
                raise
        threading.Thread(target=runner, args=(job_id,), daemon=True,
                         name=f"music-{job_id[:8]}").start()
        return dict(job)

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job["state"] not in TERMINAL:
                event = self.cancel_events.get(job_id)
                if event is not None:
                    event.set()
                if job["state"] == "queued":
                    job.update(state="cancelled", finished_at=time.time())
                    self._persist()
            return dict(job)

    def ack(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job["state"] != "done":
                return None
            if not final_matches(job, self.final_path(job)):
                return None
            if not job["acknowledged"]:
                previous_ack = job["acknowledged"]
                previous_time = job.get("receipt_acked_at")
                job["acknowledged"] = True
                job["receipt_acked_at"] = time.time()
                try:
                    self._persist()
                except Exception:
                    job["acknowledged"] = previous_ack
                    if previous_time is None:
                        job.pop("receipt_acked_at", None)
                    else:
                        job["receipt_acked_at"] = previous_time
                    raise
            return {"ok": True, "job_id": job_id, "sha256": job["sha256"]}

    # -- the run -------------------------------------------------------------
    def final_path(self, job: dict) -> Path:
        """The delivered file this job's record names, only ever inside this worker's outputs.

        Not ``{id}.wav``: a YuE2 song is FLAC and a compressed job is M4A, and a path built from
        one hard-coded extension is how a finished YuE2 job answered 409 and was marked
        ``OUTPUT_UNAVAILABLE`` after a restart. An old or foreign record falls back to the WAV name.
        """
        recorded = job.get("output_path")
        if isinstance(recorded, str) and recorded:
            path = Path(recorded)
            if path.parent == self.outputs and path.suffix in MEDIA_TYPE_BY_SUFFIX:
                return path
        return self.outputs / f"{job['id']}.wav"

    def _unlink_audio(self, job_id: str) -> None:
        """Remove every audio file this worker itself wrote for a job that did not complete."""
        for suffix in MEDIA_TYPE_BY_SUFFIX:
            (self.outputs / f"{job_id}{suffix}").unlink(missing_ok=True)

    def _deliver_audio(self, job_id: str, job: dict, master: Path, facts: dict, *,
                       media_type: str, audio_format: str) -> tuple[Path, dict, dict | None]:
        """Compress the finished master when the job asked for ``m4a``; otherwise leave it.

        Runs **after** the inference lock is released, so the next job's model starts while this
        one encodes. The master stays until the encode is verified, then this worker's own
        temporary master is unlinked. Any encode failure delivers the master unchanged and records
        why: a paid generation is never failed by compression.

        Returns ``(delivered path, delivered facts, delivery record or None)``. The decoded facts
        (duration, rate, channels, frames) always describe the audio and come from the master.
        """
        delivered = dict(facts, media_type=media_type, format=audio_format)
        if job["params"].get("output_format") != "m4a":
            return master, delivered, None
        record = {"requested": "m4a", "master_media_type": media_type,
                  "master_bytes": facts["bytes"], "master_sha256": facts["sha256"]}
        self._progress(job_id, 0.97, "Compressing to AAC")
        try:
            encoded = audio_encode.encode_m4a(
                master, self.outputs / f"{job_id}.m4a", profile="music",
                expected_seconds=facts.get("audio_duration_s"))
        except Exception as exc:  # noqa: BLE001 - deliver the master, never fail the paid job
            record.update(applied=audio_format, delivery_fallback_reason=str(exc)[:400])
            return master, delivered, record
        master.unlink(missing_ok=True)
        delivered.update(bytes=encoded["bytes"], sha256=encoded["sha256"],
                         media_type=encoded["media_type"], format=encoded["format"])
        record.update(applied="m4a", codec=encoded["codec"],
                      bitrate_kbps=encoded["bitrate_kbps"])
        return Path(encoded["path"]), delivered, record

    def _complete(self, job: dict, sidecar: Path, request: dict, delivered_path: Path,
                  delivered: dict, delivery: dict | None, *, engine_seconds, extra: dict,
                  runner: dict | None = None) -> None:
        """Write the sidecar and mark the job done, describing the file actually delivered."""
        job_id = job["id"]
        with self.lock:
            now = time.time()
            body = {
                "schema": "studiohub.music-artifact", "schema_version": 1,
                "job_id": job_id, "created_at": job["created_at"], "generated_at": now,
                "request": request,
                "artifact": {key: delivered[key] for key in (
                    "bytes", "sha256", "audio_duration_s", "sample_rate_hz",
                    "channels", "frames")},
            }
            if delivery is not None:
                body["delivery"] = dict(delivery, media_type=delivered["media_type"],
                                        format=delivered["format"])
            if runner is not None:
                body["runner"] = runner
            _atomic_json(sidecar, body)
            job.update(
                state="done", progress=1.0, finished_at=now, generated_at=now,
                duration_seconds=now - job["started_at"], engine_seconds=engine_seconds,
                output_path=str(delivered_path),
                output_url=f"/api/generate/jobs/{job_id}/audio",
                sidecar_path=str(sidecar), sha256=delivered["sha256"],
                bytes=delivered["bytes"], media_type=delivered["media_type"],
                format=delivered["format"],
                sample_rate_hz=delivered["sample_rate_hz"], channels=delivered["channels"],
                audio_duration_s=delivered["audio_duration_s"], frames=delivered["frames"],
                **extra)
            if delivery is not None:
                job["delivery"] = delivery
                if delivery.get("delivery_fallback_reason"):
                    job["delivery_fallback_reason"] = delivery["delivery_fallback_reason"]
            self._persist()

    def _run_sa3(self, job_id: str) -> None:
        """Render one Stable Audio 3 clip; a short-lived MLX subprocess per job."""
        final = self.outputs / f"{job_id}.wav"
        sidecar = self.outputs / f"{job_id}.json"
        job = None
        try:
            with self.inference_lock:
                with self.lock:
                    event = self.cancel_events.get(job_id)
                    job = self.jobs.get(job_id)
                    if job is None:
                        return
                    if event is not None and event.is_set():
                        job.update(state="cancelled", finished_at=time.time())
                        self._persist()
                        return
                    job.update(state="running", progress=0.05, started_at=time.time())
                    self._persist()
                params = job["params"]
                model = catalog.require_model(params["repo"])
                seed = params["seed"]
                if seed == -1:
                    seed = random.randint(0, 2 ** 32 - 1)
                with self.lock:
                    job["resolved_seed"] = seed
                    self._persist()
                files = self.preflight(model.repo)
                if files["state"] != "cached":
                    raise _JobFailed({
                        "state": "error",
                        "error": ("MODEL_FILES_UNAVAILABLE: "
                                  + str(files.get("reason") or files["state"])),
                        "error_code": "MODEL_FILES_UNAVAILABLE"})
                facts = stable_audio.generate(
                    prompt=params["prompt"], seconds=params["duration_seconds"],
                    steps=params["steps"], seed=seed,
                    cfg=params["guidance_scale"],
                    weights=Path(files["checkpoints_root"]), output=final,
                    cancel_event=self.cancel_events.get(job_id),
                    on_progress=lambda ratio, label: self._progress(
                        job_id, ratio, label))
            # The inference lock is released: compressing never delays the next generation.
            delivered_path, delivered, delivery = self._deliver_audio(
                job_id, job, final, facts, media_type=MEDIA_TYPE, audio_format=AUDIO_FORMAT)
            self._complete(
                job, sidecar,
                {"repo": params["repo"], "prompt": params["prompt"],
                 "duration_seconds": params["duration_seconds"],
                 "steps": params["steps"], "seed": seed,
                 "guidance_scale": params["guidance_scale"]},
                delivered_path, delivered, delivery,
                engine_seconds=facts["engine_seconds"],
                extra={"peak_process_rss_bytes": resource.getrusage(
                    resource.RUSAGE_SELF).ru_maxrss})
        except stable_audio.Sa3Cancelled:
            self._unlink_audio(job_id)
            sidecar.unlink(missing_ok=True)
            self._finish_failure(job, {"state": "cancelled"})
        except _JobFailed as failure:
            self._finish_failure(job, failure.outcome)
        except Exception as exc:  # noqa: BLE001 - report the engine's own text
            self._unlink_audio(job_id)
            sidecar.unlink(missing_ok=True)
            # The artifact is gone, so the record must stop pointing at it: a persist failure
            # after the job was marked done otherwise leaves an `output_url` for a missing file.
            self._finish_failure(job, {
                "state": "error", "error": f"{type(exc).__name__}: {exc}",
                "clear_artifacts": True})
        finally:
            self.cancel_events.pop(job_id, None)

    def _run_yue2(self, job_id: str) -> None:
        """Render one YuE2 song in the engine's own runtime, through the app's runner bridge.

        **A separate interpreter, not an import.** `mlx-Yue` pins `mlx==0.32.2` and this worker's
        runtime carries `mlx==0.31.2`, so the bridge is spawned with
        `runtimes/music-yue2/bin/python3` and read one JSON object per line — the same shape
        the Video lane used for its second engine before Lance was retired.

        The peak that matters is the bridge's, not this process's: the worker samples its own
        footprint, and for this engine the memory is in the child. The bridge samples itself and
        its number is what the job records, with the method named.
        """
        final = self.outputs / f"{job_id}.flac"
        sidecar = self.outputs / f"{job_id}.json"
        job = None
        try:
            with self.inference_lock:
                with self.lock:
                    event = self.cancel_events.get(job_id)
                    job = self.jobs.get(job_id)
                    if job is None:
                        return
                    if event is not None and event.is_set():
                        job.update(state="cancelled", finished_at=time.time())
                        self._persist()
                        return
                    job.update(state="running", progress=0.05, started_at=time.time())
                    self._persist()
                params = job["params"]
                model = catalog.require_model(params["repo"])
                seed = params["seed"]
                if seed == -1:
                    # The port requires a non-negative seed, so the lane's -1 is drawn here and
                    # recorded on the job — the same contract the other two engines follow.
                    seed = random.randint(0, 2 ** 63 - 1)
                with self.lock:
                    job["resolved_seed"] = seed
                    job["runner"] = "mlx-yue"
                    self._persist()
                files = self.preflight(model.repo)
                if files["state"] != "cached":
                    raise _JobFailed({
                        "state": "error",
                        "error": ("MODEL_FILES_UNAVAILABLE: "
                                  + str(files.get("reason") or files["state"])),
                        "error_code": "MODEL_FILES_UNAVAILABLE"})
                # The decoder is a companion package: the port loads it by path, and a run without
                # it fails at load rather than making quiet audio. It is resolved from that
                # package's own verified receipt, the way the generator's folder was.
                vae_root = self.cache_for(model.repo).companion_root(catalog.YUE2_VAE_PACKAGE_ID)
                interpreter = yue2_runtime_python()
                facts = yue2.generate(
                    model_dir=Path(files["checkpoints_root"]),
                    vae_dir=vae_root,
                    output=final,
                    prompt=params["prompt"], lyrics=params["lyrics"], mode=params["mode"],
                    precision=params["precision"],
                    duration_seconds=params["duration_seconds"],
                    max_tokens=params["max_tokens"], min_tokens=params["min_tokens"],
                    steps=params["steps"], guidance_scale=params["guidance_scale"],
                    seed=seed, interpreter=interpreter,
                    cancel_event=self.cancel_events.get(job_id),
                    on_progress=lambda ratio, label: self._progress(job_id, ratio, label),
                    timeout_seconds=float(job.get("deadline_s") or yue2.DEFAULT_TIMEOUT_SECONDS))
            # The inference lock is released: compressing never delays the next generation.
            delivered_path, delivered, delivery = self._deliver_audio(
                job_id, job, final, facts, media_type=YUE2_MEDIA_TYPE,
                audio_format=YUE2_AUDIO_FORMAT)
            self._complete(
                job, sidecar,
                {"repo": params["repo"], "prompt": params["prompt"],
                 "mode": params["mode"], "precision": params["precision"],
                 "duration_seconds": params["duration_seconds"],
                 "max_tokens": params["max_tokens"], "steps": params["steps"],
                 "seed": seed, "guidance_scale": params["guidance_scale"]},
                delivered_path, delivered, delivery,
                engine_seconds=facts["generate_seconds"],
                runner={"engine": "mlx-yue", "python": str(interpreter),
                        "load_seconds": facts["load_seconds"],
                        "generate_seconds": facts["generate_seconds"]},
                # The bridge measured itself; the worker's own sampler would report this
                # process, where the memory is not.
                extra={"peak_footprint_bytes": facts["peak_footprint_bytes"],
                       "peak_rss_bytes": facts["peak_rss_bytes"],
                       "footprint_method": facts["footprint_method"]})
        except yue2.Yue2Cancelled:
            self._unlink_audio(job_id)
            sidecar.unlink(missing_ok=True)
            self._finish_failure(job, {"state": "cancelled"})
        except _JobFailed as failure:
            self._unlink_audio(job_id)
            sidecar.unlink(missing_ok=True)
            self._finish_failure(job, failure.outcome)
        except Exception as exc:  # noqa: BLE001 - report the engine's own text
            self._unlink_audio(job_id)
            sidecar.unlink(missing_ok=True)
            self._finish_failure(job, {
                "state": "error", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            self.cancel_events.pop(job_id, None)

    def _progress(self, job_id: str, ratio, desc) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job["state"] not in ("queued", "running"):
                return
            job["progress"] = round(float(ratio), 4)
            if desc:
                job["progress_stage"] = str(desc)[:200]
            job["last_progress_at"] = time.time()

    def _finish_failure(self, job: dict, outcome: dict) -> None:
        job_id = job["id"]
        with self.lock:
            event = self.cancel_events.get(job_id)
            explicit_error = bool(outcome.get("error") or outcome.get("error_code")
                                  or outcome.get("state") == "error")
            cancelled = bool(outcome.get("state") == "cancelled"
                             or (not explicit_error and event is not None and event.is_set()))
            job["engine_peak_rss_bytes"] = outcome.get("peak_rss_bytes")
            if cancelled:
                job.update(state="cancelled", error=None, error_code=None)
            else:
                text = str(outcome.get("error") or "music generation failed")
                job.update(state="error", error=text[:4000],
                           error_code=(outcome.get("error_code")
                                       or ("ENGINE_CHILD_EXIT_UNVERIFIED"
                                           if "not verified" in text
                                           else "MUSIC_GENERATION_FAILED")))
                if outcome.get("clear_artifacts"):
                    job.update(output_path=None, output_url=None, sidecar_path=None,
                               sha256=None, bytes=None, media_type=None)
                if outcome.get("unverified_artifact_paths"):
                    job["unverified_artifact_paths"] = list(
                        outcome["unverified_artifact_paths"])
                    job["output_url"] = None
            job["finished_at"] = time.time()
            if job.get("started_at"):
                job["duration_seconds"] = job["finished_at"] - job["started_at"]
            try:
                self._persist()
            except Exception:
                pass

    # -- memory policy -------------------------------------------------------
    # `memory_policy.py` reads the manager through these methods, the way the Voice and
    # Subtitle workers' policies do, so the fleet contract (`GET /api/memory-policy`,
    # `POST /api/memory/release`) is the same shape on every Studio.  Stable Audio 3 runs a
    # fresh MLX subprocess per job and YuE2 a bridge per job, and both exit with the job, so this
    # worker never holds a model between jobs and has nothing to release.
    def has_active_jobs(self) -> bool:
        """Is a job queued or running? A release must never preempt one."""
        with self.lock:
            return any(job.get("state") in ("queued", "running")
                       for job in self.jobs.values())

    def loaded_model_keys(self) -> list[tuple[str, str]]:
        """Every model this worker is holding, as ``(repo, runtime)`` pairs: always none."""
        return []

    def release_memory_locked(self, reason: str = "manual") -> dict:
        """Nothing is resident, so nothing is released; still refuses while work is active."""
        if self.has_active_jobs():
            raise RuntimeError("music generation is active; memory was not released")
        return {"released": False, "models": [], "actions": [], "reason": reason}

    def close(self) -> None:
        """No engine child outlives its job, so there is nothing to stop."""


class _JobFailed(RuntimeError):
    def __init__(self, outcome: dict) -> None:
        super().__init__(str(outcome.get("error") or ""))
        self.outcome = outcome


def _tier_cap_for_this_host(model: catalog.ModelEntry) -> int:
    """The model's own single-pass cap for the tier this Mac lands in.

    Mirrors the boundaries of the model's own tier table, so the catalogue, the request
    validation and the job record agree on one number.  A model that publishes no tier table
    falls back to its own single-pass limit.
    """
    try:
        raw = subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                      text=True, timeout=3).strip()
        total_gb = int(raw) / 1e9
    except (OSError, ValueError, subprocess.SubprocessError):
        return int(_limit(model, "single_pass_max_seconds"))
    selected = model.tiers[0] if model.tiers else None
    for tier in model.tiers:
        if tier.min_unified_memory_gb is not None and total_gb >= tier.min_unified_memory_gb:
            selected = tier
    if selected is None:
        return int(_limit(model, "single_pass_max_seconds"))
    return selected.max_duration_with_lm_s
