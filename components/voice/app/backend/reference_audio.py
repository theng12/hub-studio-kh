"""Model-owned preparation of private voice-cloning references.

GenStudio owns the customer asset and Studio Hub owns temporary transport.
Voice Studio owns the model-specific execution copy: decoding, silence-aware
selection, format normalization, transcript alignment, and immutable evidence.

The execution copy is built from the model's own published contract: the audited
profile where one is installed, otherwise the model's catalog row (its sample
rate, and the transcript rule its family and guardrails publish). Every value
carries its source (``profile_sources``) and an un-audited model is prepared
anyway, labelled ``profile_audited: false``. Only a cloning model that publishes
nothing to derive from is refused, and that refusal names the missing field and
the file to add it in.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import catalog, media_tools, model_audits, paths


ROOT = paths.data_path("reference_cache")
PREPARATION_REVISION = "voicestudio.reference-audio.v1"
MAX_BYTES = 25_000_000
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".webm"}

# Where a reference-contract value has to be added when the model does not
# publish it. Named in the refusal so the operator gets a to-do, not a dead end.
CATALOG_SOURCE_FILE = "components/voice/app/backend/catalog.py"
AUDIT_SOURCE_DIR = "components/voice/model-audits"
# The published fields a prepared execution copy cannot be built without. A model
# that states none of them is refused, never defaulted.
_REQUIRED_PUBLISHED_FACTS = ("sample_rate_hz",)


class ReferenceAudioError(ValueError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        model_id: str | None = None,
        missing_field: str | None = None,
        fix_in: tuple[str, ...] = (),
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.model_id = model_id
        self.missing_field = missing_field
        self.fix_in = fix_in

    def as_detail(self) -> dict[str, Any]:
        """The route payload: code + explanation, plus the to-do when there is one."""
        payload: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.model_id:
            payload["model_id"] = self.model_id
        if self.missing_field:
            payload["missing_field"] = self.missing_field
        if self.fix_in:
            payload["fix_in"] = list(self.fix_in)
        return payload


@dataclass(frozen=True)
class _ReferenceContract:
    """One model's reference-audio contract and where every value came from.

    ``audited`` says the values came from an installed audit record.
    ``sources`` names, per field, the published fact behind it - or records that
    the model publishes no such fact. ``missing`` names the required published
    field the model does not state; a caller refuses on it rather than guessing.
    """

    model_id: str
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    audited: bool = False
    cloning: bool = False
    missing: str | None = None


def _audited_profile(model_id: str) -> dict[str, Any]:
    limits = model_audits.input_limits(model_id)
    value = limits.get("reference_audio")
    return dict(value) if isinstance(value, dict) else {}


def _published_transcript_rule(model: catalog.ModelEntry) -> tuple[str, str]:
    """Whether the model's own clone path needs the exact reference transcript.

    Read from what the row publishes: a model whose ``quality_guardrails``
    enforce reference word alignment conditions on the transcript, so it is
    required; a family that accepts ``ref_transcript`` takes one optionally; a
    family that has no such parameter never reads one.
    """
    row = catalog.serialize_model(model)
    guardrails = (row.get("execution_contract") or {}).get("quality_guardrails") or {}
    if guardrails.get("reference_word_alignment"):
        return (
            "required",
            "catalog.execution_contract.quality_guardrails.reference_word_alignment "
            f"({model.repo})",
        )
    family = catalog.family_for(model)
    if "ref_transcript" in (family.lane_parameters or ()):
        return (
            "optional",
            f"catalog.FAMILIES[{family.id!r}].lane_parameters accepts ref_transcript",
        )
    return (
        "not_used",
        f"catalog.FAMILIES[{family.id!r}].lane_parameters has no ref_transcript",
    )


def _published_profile(
    model: catalog.ModelEntry,
) -> tuple[dict[str, Any], dict[str, str]]:
    """The reference contract a cloning model publishes about itself.

    Only the model's own row is read. Every value gets a source, and every bound
    the row does not publish is recorded as unpublished rather than defaulted:
    a guessed window is the defect A9 removed. Missing required facts stop the
    derivation here, so a row that states nothing derivable never reaches the
    transcript rule.
    """
    sources: dict[str, str] = {
        field: f"not published in {CATALOG_SOURCE_FILE}" for field in _REQUIRED_PUBLISHED_FACTS
    }
    rate = getattr(model, "sample_rate_hz", None)
    if isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0:
        return {}, sources
    values: dict[str, Any] = {
        "sample_rate_hz": rate,
    }
    sources["sample_rate_hz"] = f"catalog.ModelEntry.sample_rate_hz ({model.repo})"
    requirement, requirement_source = _published_transcript_rule(model)
    values["transcript"] = requirement
    sources["transcript"] = requirement_source
    # No reference length or channel count is published for this model, so
    # preparation enforces none. Recorded, not defaulted: `_decode` produces a
    # mono copy and the artifact's own metadata states that.
    for field in ("minimum_duration_seconds", "maximum_duration_seconds", "channels"):
        sources[field] = f"not published in {CATALOG_SOURCE_FILE} (ModelEntry has no {field})"
    return values, sources


def _profile(model_id: str) -> dict[str, Any]:
    """The model's own reference contract, as values.

    An installed audited profile is used as-is; a field it does not state is
    completed from the model's published row, because that is the same model's
    own statement and the alternative is this consumer's default. A cloning
    model with no audit gets a profile built entirely from its published row.
    A model the catalog does not describe as cloning keeps the generic
    preparation (``{}``), exactly as before.

    This is the seam that withholds a contract in the ST-04 tests: returning
    ``{}`` for a catalogued cloning model is a refusal, not a default.
    """
    auditing = _audited_profile(model_id)
    model = catalog.get_model(model_id)
    if auditing:
        values = dict(auditing)
        published, _ = _published_profile(model) if model is not None else ({}, {})
        for name in ("sample_rate_hz", "transcript"):
            if values.get(name) in (None, "") and name in published:
                values[name] = published[name]
        return values
    if model is None or "voice-cloning" not in (model.capabilities or ()):
        return {}
    published, _ = _published_profile(model)
    return published if all(name in published for name in _REQUIRED_PUBLISHED_FACTS) else {}


def _contract(model_id: str, profile: dict[str, Any]) -> _ReferenceContract:
    """The provenance of a contract returned by `_profile`, and its gaps."""
    model = catalog.get_model(model_id)
    cloning = model is not None and "voice-cloning" in (model.capabilities or ())
    if not profile:
        return _ReferenceContract(
            model_id=model_id,
            cloning=cloning,
            missing=_REQUIRED_PUBLISHED_FACTS[0] if cloning else None,
        )
    published, published_sources = _published_profile(model) if model is not None else ({}, {})
    auditing = _audited_profile(model_id)
    audited = bool(auditing) and all(
        profile.get(name) == value for name, value in auditing.items()
    )
    sources: dict[str, str] = {}
    if audited:
        sources = {
            name: f"audit contract.input_limits.reference_audio.{name}" for name in auditing
        }
    for name, value in profile.items():
        if name in sources:
            continue
        if published.get(name) == value:
            sources[name] = published_sources.get(name, f"catalog row ({name})")
        elif audited:
            sources[name] = f"audit contract.input_limits.reference_audio.{name}"
        else:
            # A value with no published field behind it. It is labelled as such
            # rather than attributed to a source it did not come from.
            sources[name] = f"no published source for {name}"
    for name, source in published_sources.items():
        sources.setdefault(name, source)
    missing = next(
        (name for name in _REQUIRED_PUBLISHED_FACTS if name not in profile),
        None,
    )
    return _ReferenceContract(
        model_id=model_id,
        values=dict(profile),
        sources=sources,
        audited=audited,
        cloning=cloning,
        missing=missing,
    )


def _contract_refusal(contract: _ReferenceContract) -> ReferenceAudioError:
    """Refuse a model that publishes nothing to derive its contract from.

    The message names the model, the missing published field and both files the
    field can be added in, the way `workload_policy.required_free_memory_gb` and
    `generation._worker_for_engine` name theirs: a refusal with a named field is
    a to-do, a refusal because a record does not exist is a dead end.
    """
    field = contract.missing or _REQUIRED_PUBLISHED_FACTS[0]
    audit_file = f"{AUDIT_SOURCE_DIR}/<date>-<name>/<org>--<repo>.audit.json"
    return ReferenceAudioError(
        "REFERENCE_AUDIO_CONTRACT_UNAVAILABLE",
        f"{contract.model_id} declares voice cloning but publishes no {field} to build its "
        f"reference contract from, so Voice Studio cannot derive an execution copy without "
        f"guessing. Add it to this model's row in {CATALOG_SOURCE_FILE} (ModelEntry.{field}), or "
        f"install an audit record under {audit_file} with "
        f"contract.input_limits.reference_audio.{field}. Cloning from a saved voice in the "
        "Voices library is not prepared here and is unaffected.",
        model_id=contract.model_id,
        missing_field=field,
        fix_in=(CATALOG_SOURCE_FILE, audit_file),
    )


def _requires_published_profile(model_id: str) -> bool:
    """Whether this model's reference contract must be the model's own.

    The question is answered by the producer, not by the model's name: a model
    whose published ``capabilities`` declare voice cloning must be prepared from
    its own published facts (an audited profile where one is installed, the
    catalog row otherwise), never from this consumer's generic default.

    The previous version was ``"qwen3-tts" in name and "base" in name``. A new
    cloning family fell outside that substring test and silently received the
    generic defaults; a new Qwen tier that was not a *-Base-* did too.

    A repo that is not in the catalog answers ``False``: the public routes refuse
    an unknown repo before preparation (`main.py` returns 400 for it, and
    `GenerationManager.start_txt2speech` raises), so an unregistered id can only
    arrive from a direct caller, and those kept their existing behaviour.
    """
    model = catalog.get_model(model_id)
    if model is None:
        return False
    return "voice-cloning" in (model.capabilities or ())


# The name the ST-04 contract tests and `packaging/derive_qwen_base_audits.py`
# already know. The follow-up to A9 changed what *satisfies* the question - an
# audited profile or the model's own published facts - not the question itself:
# is this model's reference contract the model's own, rather than a default this
# consumer invented? Keeping the symbol keeps those callers honest instead of
# leaving them asserting a rule the code no longer implements.
_requires_audited_profile = _requires_published_profile


def _number(value: object, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def _decode(path: Path, target_rate: int):
    import numpy as np
    import soundfile as sf

    try:
        audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception:
        ffmpeg = media_tools.find_executable("ffmpeg")
        if not ffmpeg:
            raise ReferenceAudioError(
                "REFERENCE_AUDIO_DECODE_UNAVAILABLE",
                "This reference format needs FFmpeg, but FFmpeg is not available on this Voice Studio.",
            )
        decoded = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.decoded.wav")
        try:
            process = subprocess.run(
                [str(ffmpeg), "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                 "-ac", "1", "-ar", str(target_rate), "-c:a", "pcm_s16le", str(decoded)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if process.returncode != 0 or not decoded.is_file():
                raise ReferenceAudioError(
                    "REFERENCE_AUDIO_DECODE_FAILED",
                    "The uploaded reference audio could not be decoded.",
                )
            audio, sample_rate = sf.read(str(decoded), dtype="float32", always_2d=True)
        finally:
            decoded.unlink(missing_ok=True)

    if audio.size == 0 or sample_rate <= 0:
        raise ReferenceAudioError("REFERENCE_AUDIO_EMPTY", "The uploaded reference contains no audio.")
    if not np.isfinite(audio).all():
        raise ReferenceAudioError("REFERENCE_AUDIO_INVALID", "The reference contains invalid audio samples.")
    mono = audio.mean(axis=1, dtype=np.float32)
    if sample_rate != target_rate:
        old = np.arange(len(mono), dtype=np.float64) / float(sample_rate)
        new_length = max(1, round(len(mono) * target_rate / float(sample_rate)))
        new = np.arange(new_length, dtype=np.float64) / float(target_rate)
        mono = np.interp(new, old, mono).astype(np.float32)
        sample_rate = target_rate
    return mono, sample_rate


def _speech_bounds(audio, sample_rate: int) -> tuple[int, int]:
    """Return silence-trimmed bounds with a short, non-destructive pad."""
    import numpy as np

    frame = max(1, round(sample_rate * 0.02))
    usable = len(audio) - (len(audio) % frame)
    if usable <= 0:
        return 0, len(audio)
    framed = audio[:usable].reshape(-1, frame)
    rms = np.sqrt(np.mean(np.square(framed), axis=1))
    peak = float(rms.max(initial=0.0))
    if peak < 0.001:
        raise ReferenceAudioError(
            "REFERENCE_AUDIO_NO_SPEECH",
            "No usable speech was detected in the uploaded reference.",
        )
    threshold = max(0.0025, peak * 0.08)
    voiced = np.flatnonzero(rms >= threshold)
    if not len(voiced):
        raise ReferenceAudioError(
            "REFERENCE_AUDIO_NO_SPEECH",
            "No usable speech was detected in the uploaded reference.",
        )
    pad = round(sample_rate * 0.15)
    return max(0, int(voiced[0]) * frame - pad), min(
        len(audio), (int(voiced[-1]) + 1) * frame + pad
    )


def _validated_segments(value: object, duration_s: float) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ReferenceAudioError(
            "REFERENCE_TRANSCRIPT_SEGMENTS_INVALID",
            "Reference transcript segments must be an ordered list.",
        )
    result = []
    previous_end = 0.0
    for raw in value:
        if not isinstance(raw, dict):
            raise ReferenceAudioError("REFERENCE_TRANSCRIPT_SEGMENTS_INVALID", "A transcript segment is invalid.")
        start = _number(raw.get("start_seconds", raw.get("start")))
        end = _number(raw.get("end_seconds", raw.get("end")))
        text = str(raw.get("text") or "").strip()
        if start is None or end is None or start < previous_end or end <= start or end > duration_s + 0.05:
            raise ReferenceAudioError(
                "REFERENCE_TRANSCRIPT_SEGMENTS_INVALID",
                "Transcript timestamps must be ordered and remain inside the reference audio.",
            )
        if text:
            result.append({"start": start, "end": end, "text": text})
        previous_end = end
    return result


def _best_segment_window(segments: list[dict[str, Any]], max_seconds: float) -> tuple[float, float, str]:
    best: tuple[float, float, str] | None = None
    best_score = -1
    for start_index, first in enumerate(segments):
        selected = []
        for segment in segments[start_index:]:
            if segment["end"] - first["start"] > max_seconds:
                break
            selected.append(segment)
        if not selected:
            continue
        text = " ".join(segment["text"] for segment in selected).strip()
        score = len(text)
        if score > best_score:
            best_score = score
            best = (first["start"], selected[-1]["end"], text)
    if best is None:
        raise ReferenceAudioError(
            "REFERENCE_AUDIO_DURATION_UNSUPPORTED",
            "No transcript-aligned reference segment fits this model's duration limit.",
        )
    return best


def _best_energy_window(audio, sample_rate: int, seconds: float) -> tuple[int, int]:
    import numpy as np

    width = min(len(audio), max(1, round(seconds * sample_rate)))
    if width >= len(audio):
        return 0, len(audio)
    step = max(1, round(sample_rate * 0.25))
    squared = np.square(audio.astype(np.float64))
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    best_start = 0
    best_energy = -1.0
    for start in range(0, len(audio) - width + 1, step):
        energy = cumulative[start + width] - cumulative[start]
        if energy > best_energy:
            best_energy = energy
            best_start = start
    return best_start, best_start + width


def cleanup_expired(now: float | None = None) -> int:
    current = time.time() if now is None else now
    removed = 0
    if not ROOT.is_dir():
        return 0
    for child in ROOT.iterdir():
        if child.is_symlink() or not child.is_dir() or child.name.startswith("."):
            continue
        try:
            metadata = json.loads((child / "metadata.json").read_text(encoding="utf-8"))
            expires_at = float(metadata.get("expires_at") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            expires_at = 0
        if expires_at and expires_at <= current:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def prepare(
    *, audio_bytes: bytes, filename: str, model_id: str, transcript: str = "",
    transcript_segments: object = None, expires_at: float | None = None,
) -> dict[str, Any]:
    """Create or reuse one deterministic, model-compatible execution copy."""
    import numpy as np
    import soundfile as sf

    if not audio_bytes:
        raise ReferenceAudioError("REFERENCE_AUDIO_EMPTY", "The uploaded reference is empty.")
    if len(audio_bytes) > MAX_BYTES:
        raise ReferenceAudioError("REFERENCE_AUDIO_TOO_LARGE", "Reference audio exceeds the 25 MB limit.")
    extension = Path(filename or "reference.wav").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ReferenceAudioError("REFERENCE_AUDIO_TYPE_UNSUPPORTED", "The reference audio type is unsupported.")

    profile = _profile(model_id)
    contract = _contract(model_id, profile)
    if contract.cloning and not profile:
        # A cloning model the catalog describes must publish its own facts. This
        # is the only refusal left: every catalogued cloning model now derives a
        # usable profile, so reaching here means the row states nothing to build
        # one from - a named to-do, not a dead end.
        raise _contract_refusal(contract)
    # Every catalogued cloning model's rate comes from its published row (or from
    # its audit); the generic rate is reached only by a repo the catalog does not
    # describe as cloning, which kept its historical behaviour.
    target_rate = int(_number(profile.get("sample_rate_hz"), 24000) or 24000)
    target_rate = max(8000, min(target_rate, 96000))
    source_sha = hashlib.sha256(audio_bytes).hexdigest()
    profile_hash = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    segment_hash = hashlib.sha256(
        json.dumps(transcript_segments or [], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    key = hashlib.sha256(
        f"{PREPARATION_REVISION}\n{source_sha}\n{model_id}\n{profile_hash}\n{segment_hash}\n{transcript}".encode()
    ).hexdigest()
    target = ROOT / key
    metadata_path = target / "metadata.json"
    wav_path = target / "reference.wav"
    if metadata_path.is_file() and wav_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if expires_at and float(metadata.get("expires_at") or 0) < expires_at:
            metadata["expires_at"] = expires_at
            metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # This key is derived from the profile's values, so a reused entry was
        # prepared under the same contract. An entry written before the
        # provenance was published gets it here rather than reading as unknown.
        metadata.setdefault("profile_audited", contract.audited)
        metadata.setdefault("profile_sources", contract.sources)
        return {**metadata, "path": str(wav_path)}

    ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_expired()
    source = ROOT / f".{uuid.uuid4().hex}{extension}"
    source.write_bytes(audio_bytes)
    try:
        audio, sample_rate = _decode(source, target_rate)
    finally:
        source.unlink(missing_ok=True)
    original_duration = len(audio) / float(sample_rate)
    segments = _validated_segments(transcript_segments, original_duration)
    speech_start, speech_end = _speech_bounds(audio, sample_rate)
    audio = audio[speech_start:speech_end]
    window_start_s = speech_start / float(sample_rate)
    window_end_s = speech_end / float(sample_rate)

    maximum = _number(profile.get("maximum_duration_seconds"))
    minimum = _number(profile.get("minimum_duration_seconds"), 0.5) or 0.5
    requirement = str(profile.get("transcript") or "optional").lower()
    derived_transcript = transcript.strip()
    if maximum and len(audio) / sample_rate > maximum:
        if segments:
            start_s, end_s, derived_transcript = _best_segment_window(segments, maximum)
            start = max(0, round(start_s * sample_rate))
            end = min(len(audio) + speech_start, round(end_s * sample_rate))
            audio = _decode_array_slice(audio, start - speech_start, end - speech_start)
            window_start_s, window_end_s = start_s, end_s
        else:
            if requirement == "required" and derived_transcript:
                raise ReferenceAudioError(
                    "REFERENCE_TRANSCRIPT_ALIGNMENT_REQUIRED",
                    "This model needs a transcript matching the selected reference segment. Supply timestamped transcript segments.",
                )
            start, end = _best_energy_window(audio, sample_rate, maximum)
            audio = audio[start:end]
            window_start_s += start / float(sample_rate)
            window_end_s = window_start_s + len(audio) / float(sample_rate)
            derived_transcript = ""
    if len(audio) / sample_rate < minimum:
        raise ReferenceAudioError(
            "REFERENCE_AUDIO_TOO_SHORT",
            f"This model needs at least {minimum:g} seconds of usable speech.",
        )
    if requirement == "required" and not derived_transcript:
        raise ReferenceAudioError(
            "REFERENCE_TRANSCRIPT_REQUIRED",
            "This model requires a transcript matching the reference audio.",
        )

    rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float64)))))
    peak = float(np.max(np.abs(audio), initial=0.0))
    if rms <= 1e-5 or peak <= 1e-4:
        raise ReferenceAudioError("REFERENCE_AUDIO_NO_SPEECH", "No usable speech remains after preparation.")
    gain = min(0.95 / peak, 0.1 / rms)
    audio = np.clip(audio * gain, -0.95, 0.95).astype(np.float32)

    temporary = ROOT / f".{key}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir(mode=0o700)
    try:
        output = temporary / "reference.wav"
        sf.write(str(output), audio, sample_rate, format="WAV", subtype="PCM_16")
        derived_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        metadata = {
            "cache_key": key,
            "preparation_revision": PREPARATION_REVISION,
            "model_id": model_id,
            "source_sha256": source_sha,
            "derived_sha256": derived_sha,
            "duration_seconds": round(len(audio) / float(sample_rate), 3),
            "source_duration_seconds": round(original_duration, 3),
            "sample_rate_hz": sample_rate,
            "channels": 1,
            "window_start_seconds": round(window_start_s, 3),
            "window_end_seconds": round(window_end_s, 3),
            "transcript": derived_transcript,
            "profile": profile,
            # Where this contract came from, field by field. Published beside the
            # values so a caller can show an un-audited cloning path as
            # un-audited without the app refusing to do the work.
            "profile_audited": contract.audited,
            "profile_sources": contract.sources,
            "expires_at": expires_at,
            "created_at": time.time(),
        }
        (temporary / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        try:
            os.replace(temporary, target)
        except OSError:
            if not target.is_dir():
                raise
        return {**metadata, "path": str(wav_path)}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _decode_array_slice(audio, start: int, end: int):
    start = max(0, start)
    end = min(len(audio), end)
    return audio[start:end]
