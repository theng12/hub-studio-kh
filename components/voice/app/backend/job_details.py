"""Privacy-bounded Voice Studio job details and signed media access."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from . import generation, reference_audio, voices
from .generation import OUTPUT_DIR


DETAIL_SCHEMA = "kh-studio.job-details.v1"
HANDLE_TTL_S = 300
AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}
SAFE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
VOICE_PARAMETER_KEYS = (
    "voice_library_id", "voice", "speaker", "language", "speed", "instruct",
    "voice_design_prompt", "emotion", "temperature", "top_p", "top_k",
    "repetition_penalty", "chunk_index", "chunk_total", "word_timestamps",
    "input_filename",
)


@dataclass(frozen=True)
class MediaTarget:
    path: Path
    media_type: str
    name: str


class JobMediaError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(payload: str, token: str) -> str:
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64(digest)


def _make_handle(job_id: str, kind: str, index: int, expiry: float, token: str) -> str:
    raw = json.dumps(
        {"j": job_id, "k": kind, "i": index, "e": expiry},
        separators=(",", ":"), sort_keys=True,
    ).encode()
    payload = _b64(raw)
    return f"{payload}.{_sign(payload, token)}"


def _registered_path(
    raw: object, root: Path,
) -> tuple[Path, str, Path] | None:
    if not isinstance(raw, (str, os.PathLike)):
        return None
    path = Path(raw)
    if not path.is_absolute():
        return None
    media_type = AUDIO_TYPES.get(path.suffix.lower())
    if media_type is None:
        return None
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return None
    return path, media_type, root


def _reference_paths(job) -> list[tuple[Path, str, Path]]:
    params = job.params if isinstance(job.params, dict) else {}
    private_path = params.get("_reference_audio_path")
    if private_path:
        registered = _registered_path(private_path, reference_audio.ROOT)
        return [registered] if registered is not None else []
    voice_id = params.get("voice_library_id")
    if not isinstance(voice_id, str) or not voice_id:
        return []
    registered = _registered_path(
        voices.library.reference_path(voice_id), voices.VOICES_DIR,
    )
    return [registered] if registered is not None else []


def _output_paths(job) -> list[tuple[Path, str, Path]]:
    registered = _registered_path(job.output_path, OUTPUT_DIR)
    return [registered] if registered is not None else []


def _has_symlink(path: Path, root: Path) -> bool:
    if root.is_symlink():
        return True
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return True
    current = root.absolute()
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _resolve_target(
    path: Path, root: Path, media_type: str,
) -> MediaTarget:
    try:
        if _has_symlink(path, root):
            raise JobMediaError("media_removed")
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if not resolved.is_file():
            raise JobMediaError("media_removed")
    except JobMediaError:
        raise
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise JobMediaError("media_removed") from exc
    return MediaTarget(path=resolved, media_type=media_type, name=path.name)


def _media_metadata(
    job, kind: str, index: int, path: Path, media_type: str, root: Path,
    token: str, expiry: float,
) -> dict:
    size = None
    try:
        size = _resolve_target(path, root, media_type).path.stat().st_size
    except JobMediaError:
        pass
    return {
        "kind": kind,
        "name": path.name,
        "media_type": media_type,
        "size_bytes": size,
        "duration_s": None,
        "handle": _make_handle(job.job_id, kind, index, expiry, token),
        "expires_at": expiry,
    }


def _runtime(job, current: float) -> float | None:
    if job.started_at is None:
        return None
    try:
        end = job.finished_at if job.finished_at is not None else current
        return max(0.0, float(end) - float(job.started_at))
    except (TypeError, ValueError):
        return None


def _progress(job) -> float:
    try:
        value = float(getattr(job, "progress", 0.0))
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return max(0.0, min(1.0, value))


def build_job_details(job, token: str, now: float | None = None) -> dict:
    current = time.time() if now is None else float(now)
    expiry = current + HANDLE_TTL_S
    params = job.params if isinstance(job.params, dict) else {}
    origin = job.origin if job.origin in {"hub", "local_ui", "api", "unknown"} else "unknown"
    device = str(job.origin_device or "").strip()[:160] or None
    parameters = {
        key: params[key] for key in VOICE_PARAMETER_KEYS if key in params
    }
    for key in ("chunk_index", "chunk_total"):
        value = getattr(job, key, None)
        if value is not None:
            parameters[key] = value
    references = _reference_paths(job)
    outputs = _output_paths(job)
    return {
        "schema": DETAIL_SCHEMA,
        "studio": "voice",
        "job": {
            "id": job.job_id,
            "state": job.state,
            "model": params.get("repo"),
            "operation": job.mode,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "runtime_s": _runtime(job, current),
            "origin": origin,
            "origin_device": device,
            **({"progress": _progress(job)}
               if job.state in {"queued", "running"} else {}),
        },
        "inputs": {
            "prompt": None,
            "negative_prompt": None,
            "text": params.get("text"),
            "reference_transcript": params.get("ref_transcript"),
            "parameters": parameters,
        },
        "references": [
            _media_metadata(
                job, "reference", index, path, media_type, root, token, expiry,
            )
            for index, (path, media_type, root) in enumerate(references)
        ],
        "outputs": [
            _media_metadata(
                job, "output", index, path, media_type, root, token, expiry,
            )
            for index, (path, media_type, root) in enumerate(outputs)
        ],
    }


def _decode_handle(handle: str, token: str,
                   kinds: frozenset[str] = frozenset({"reference", "output"})) -> dict:
    try:
        payload, signature = handle.split(".")
        if not hmac.compare_digest(signature, _sign(payload, token)):
            raise JobMediaError("permission_denied")
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        if _b64(raw) != payload:
            raise JobMediaError("permission_denied")
        data = json.loads(raw)
        if set(data) != {"j", "k", "i", "e"}:
            raise JobMediaError("permission_denied")
        if data["k"] not in kinds or type(data["i"]) is not int:
            raise JobMediaError("permission_denied")
        expiry = float(data["e"])
        if not math.isfinite(expiry):
            raise JobMediaError("permission_denied")
        data["e"] = expiry
        return data
    except JobMediaError:
        raise
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise JobMediaError("permission_denied") from exc


def resolve_job_media(
    job, handle: str, token: str, now: float | None = None,
) -> MediaTarget:
    data = _decode_handle(handle, token)
    if data["j"] != job.job_id:
        raise JobMediaError("permission_denied")
    current = time.time() if now is None else float(now)
    if current >= data["e"]:
        raise JobMediaError("handle_expired")
    paths = _reference_paths(job) if data["k"] == "reference" else _output_paths(job)
    try:
        path, media_type, root = paths[data["i"]]
    except IndexError as exc:
        raise JobMediaError("media_removed") from exc
    return _resolve_target(path, root, media_type)


# ── Rejected-section review candidates (inactive, review only) ─────────────────────────────────
# Resolved only from the private receipt, never from ordinary history: a job cleared from history
# keeps its candidate, and a staged folder without a receipt is never readable.
REVIEW_KIND = "rejected_section"


def _read_all(fd: int, limit: int) -> bytes:
    data = b""
    while len(data) <= limit and (chunk := os.read(fd, limit + 1 - len(data))):
        data += chunk
    return data


def _open_review_candidate(job_id: str) -> tuple[dict, int]:
    """The complete receipt and its PCM, open and verified through that same FD."""
    if not isinstance(job_id, str) or not generation.REVIEW_JOB_ID.fullmatch(job_id):
        raise JobMediaError("review_candidate_not_found")
    opened: list[int] = []
    pcm = None
    try:
        root = os.open(generation.REVIEW_ROOT, generation.REVIEW_DIR_FLAGS)
        opened.append(root)
        folder = os.open(job_id, generation.REVIEW_DIR_FLAGS, dir_fd=root)
        opened.append(folder)
        fd = os.open(generation.REVIEW_RECEIPT_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        opened.append(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > generation.REVIEW_MAX_RECEIPT_BYTES:
            raise ValueError("receipt")
        receipt = json.loads(_read_all(fd, generation.REVIEW_MAX_RECEIPT_BYTES))
        body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        audio = receipt["audio"]
        if (receipt.get("schema") != generation.REVIEW_SCHEMA
                or receipt.get("active") is not False
                or receipt.get("disposition") != "review_only"
                or receipt.get("worker_job_id") != job_id
                or receipt.get("receipt_sha256") != generation.review_sha256(body)
                or audio.get("media_type") != "audio/wav"
                or type(audio.get("bytes")) is not int
                or not 0 < audio["bytes"] <= generation.REVIEW_MAX_PCM_BYTES):
            raise ValueError("receipt")
        pcm = os.open(generation.REVIEW_PCM_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        st = os.fstat(pcm)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != audio["bytes"]
                or generation.review_fd_sha256(pcm) != audio["sha256"]):
            raise ValueError("pcm")
        os.lseek(pcm, 0, os.SEEK_SET)
        return receipt, pcm
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if pcm is not None:
            os.close(pcm)
        raise JobMediaError("review_candidate_not_found") from exc
    finally:
        for fd in opened:
            os.close(fd)


def review_candidate_details(job_id: str, token: str, now: float | None = None) -> dict:
    receipt, pcm = _open_review_candidate(job_id)
    os.close(pcm)
    expiry = (time.time() if now is None else float(now)) + HANDLE_TTL_S
    return {
        "receipt": receipt,
        "media": {"handle": _make_handle(job_id, REVIEW_KIND, 0, expiry, token),
                  "expires_at": expiry},
    }


def open_review_candidate_media(
    job_id: str, handle: str, token: str, now: float | None = None,
) -> tuple[dict, int]:
    data = _decode_handle(handle, token, frozenset({REVIEW_KIND}))
    if data["j"] != job_id or data["i"] != 0:
        raise JobMediaError("permission_denied")
    if (time.time() if now is None else float(now)) >= data["e"]:
        raise JobMediaError("handle_expired")
    return _open_review_candidate(job_id)
