"""
Voice library — user-contributed reference clips for voice cloning.

Engine-agnostic. One uploaded clip is usable by any cloning engine
(VoxCPM, F5-TTS, Chatterbox, Spark-TTS, XTTS, Qwen3-TTS CustomVoice).
Engines may cache their precomputed speaker embeddings inside the voice
folder (under `embeddings/<engine>.pt`) to skip recomputation on subsequent
generations.

Storage layout:
    app/voices/<voice_id>/
      metadata.json          ← Voice dataclass, plain JSON
      reference.<ext>        ← the actual audio (.wav / .mp3 / .m4a / .flac)
      transcript.txt         ← optional — what's said (required by F5-TTS)
      embeddings/            ← optional, per-engine speaker embedding cache

`voice_id` is a 12-char uuid4 hex prefix. Folder name == id.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import paths


VOICES_DIR = paths.data_path("voices")
METADATA_FILENAME = "metadata.json"
REFERENCE_BASENAME = "reference"            # actual file: reference.<ext>
TRANSCRIPT_FILENAME = "transcript.txt"
# Aligned transcript timings beside the transcript. The kit ships a file of this name with a
# reference clip and the app reads it, so a voice stores its timings the same way however it
# arrived: by hand, from the kit, or pushed from a controller.
TIMED_FILENAME = "transcript.timed.json"


def _clean_segments(value: object) -> list[dict]:
    """Keep the segments a cloning model can actually use, in the shape it is given them."""
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw.get("start_seconds", raw.get("start")))
            end = float(raw.get("end_seconds", raw.get("end")))
        except (TypeError, ValueError):
            continue
        text = str(raw.get("text") or "").strip()
        if not text or end <= start or start < 0:
            continue
        result.append({"start_seconds": start, "end_seconds": end, "text": text})
    return result

# Audio formats we accept on upload. soundfile reads everything except mp3
# (which still uploads/plays/saves fine, we just can't probe its metadata).
ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}

ALLOWED_LICENSES = {"self-owned", "public-domain", "permission", "unknown"}
ALLOWED_GENDERS = {"m", "f", "n"}            # n = neutral / unknown

# Soft size limit — voice references should be short clips. Anything larger
# is almost certainly the wrong file. Enforced at the FastAPI layer too.
MAX_BYTES = 25_000_000                         # 25 MB (decimal)


# ───────────── Seed catalog (curated public-domain voices) ─────────────
# Each entry points at a stable URL from a well-known TTS model repo on
# HuggingFace. These are the reference clips the model authors ship for
# demoing — explicitly meant to be shared, license matches the model repo.
# Click "Add to library" in the UI → backend fetches the URL → saves it as
# a normal library voice with proper attribution.
#
# Adding new entries: pick a stable URL (HF model repos are good — they
# rarely move files), include attribution + license, and verify the URL
# resolves (curl -IL <url>).
SEED_CATALOG = [
    {
        "id": "voxcpm2-en",
        "name": "VoxCPM2 — English demo voice",
        "language": "en",
        "gender": "n",
        "license": "public-domain",
        "source_url": "https://huggingface.co/mlx-community/VoxCPM2-4bit",
        "audio_url": "https://huggingface.co/mlx-community/VoxCPM2-4bit/resolve/main/test_en.wav",
        "transcript": "",
        "notes": "Reference clip shipped with the VoxCPM2-4bit model card. Apache-2.0.",
    },
    {
        "id": "voxcpm2-id",
        "name": "VoxCPM2 — Indonesian demo voice",
        "language": "id",
        "gender": "n",
        "license": "public-domain",
        "source_url": "https://huggingface.co/mlx-community/VoxCPM2-4bit",
        "audio_url": "https://huggingface.co/mlx-community/VoxCPM2-4bit/resolve/main/test_id.wav",
        "transcript": "",
        "notes": "Reference clip shipped with the VoxCPM2-4bit model card. Apache-2.0.",
    },
]


def seed_catalog() -> list[dict]:
    """Return the curated public-domain voice catalog. The UI displays each
    entry as a card with an 'Add to library' button."""
    return [dict(e) for e in SEED_CATALOG]


@dataclass
class Voice:
    id: str
    name: str
    language: str
    gender: str
    notes: str = ""
    license: str = "unknown"
    source_url: Optional[str] = None
    permission_acknowledged: bool = False
    has_transcript: bool = False
    audio_extension: str = ".wav"
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    fleet_managed: bool = False
    audio_sha256: Optional[str] = None
    # Short labels the Gateway puts on a voice (it is the one authority for them). Read-only here:
    # the Voice page shows them and filters by them, and has no editor for them.
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def serialize(self) -> dict:
        d = asdict(self)
        # URLs the frontend can use directly.
        d["audio_url"] = f"/api/voices/{self.id}/audio"
        d["audio_filename"] = f"{REFERENCE_BASENAME}{self.audio_extension}"
        return d


class VoiceLibrary:
    """File-backed voice library. List/add/get/delete are O(N) over the
    voices directory; that's fine — even a power user is unlikely to have
    more than ~100 voices."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        VOICES_DIR.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Voice]:
        out: list[Voice] = []
        for child in VOICES_DIR.iterdir():
            if not child.is_dir():
                continue
            v = self._load_voice(child)
            if v is not None:
                out.append(v)
        out.sort(key=lambda v: v.created_at, reverse=True)
        return out

    def get(self, voice_id: str) -> Optional[Voice]:
        # Defensive id check — we use it as a path component.
        if not voice_id or "/" in voice_id or "\\" in voice_id or voice_id.startswith("."):
            return None
        d = VOICES_DIR / voice_id
        if not d.is_dir():
            return None
        return self._load_voice(d)

    def add(
        self,
        *,
        audio_bytes: bytes,
        original_filename: str,
        name: str,
        language: str,
        gender: str,
        license: str,
        notes: str = "",
        source_url: Optional[str] = None,
        transcript: Optional[str] = None,
        permission_acknowledged: bool = False,
    ) -> Voice:
        # ── validation ─────────────────────────────────────────────
        if not name or not name.strip():
            raise ValueError("name is required")
        if len(name) > 200:
            raise ValueError("name must be at most 200 characters")
        if not language or not language.strip():
            raise ValueError("language is required")
        if license not in ALLOWED_LICENSES:
            raise ValueError(
                f"license must be one of {sorted(ALLOWED_LICENSES)}"
            )
        if gender not in ALLOWED_GENDERS:
            raise ValueError(
                f"gender must be one of {sorted(ALLOWED_GENDERS)}"
            )
        if not permission_acknowledged:
            raise ValueError(
                "permission_acknowledged is required — confirm you have rights to clone this voice"
            )
        if not audio_bytes:
            raise ValueError("audio file is empty")
        if len(audio_bytes) > MAX_BYTES:
            raise ValueError(
                f"audio file is too large ({len(audio_bytes) / 1_000_000:.1f} MB > {MAX_BYTES / 1_000_000:.0f} MB limit). "
                "Voice references should be short clips (3-15 sec) — trim before uploading."
            )

        ext = Path(original_filename).suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTS:
            raise ValueError(
                f"audio extension {ext!r} not allowed (allowed: {sorted(ALLOWED_AUDIO_EXTS)})"
            )

        # ── persist ────────────────────────────────────────────────
        with self._lock:
            voice_id = uuid.uuid4().hex[:12]
            d = VOICES_DIR / voice_id
            # Vanishingly unlikely but be defensive in case of collision.
            while d.exists():
                voice_id = uuid.uuid4().hex[:12]
                d = VOICES_DIR / voice_id
            d.mkdir(parents=True, exist_ok=True)

            audio_path = d / f"{REFERENCE_BASENAME}{ext}"
            audio_path.write_bytes(audio_bytes)

            # Probe duration/sr/channels via soundfile. Best-effort — mp3 will
            # fail to probe on macOS unless libsndfile has the mp3 codec, but
            # that's fine; the audio still plays, we just don't surface those
            # numbers in the UI.
            duration_s, sample_rate, channels = self._probe_audio(audio_path)

            has_transcript = False
            if transcript and transcript.strip():
                (d / TRANSCRIPT_FILENAME).write_text(transcript.strip())
                has_transcript = True

            voice = Voice(
                id=voice_id,
                name=name.strip(),
                language=language.strip(),
                gender=gender,
                notes=(notes or "").strip(),
                license=license,
                source_url=(source_url or "").strip() or None,
                permission_acknowledged=True,
                has_transcript=has_transcript,
                audio_extension=ext,
                duration_seconds=duration_s,
                sample_rate=sample_rate,
                channels=channels,
            )
            (d / METADATA_FILENAME).write_text(
                json.dumps(asdict(voice), indent=2, default=str)
            )
            return voice

    @staticmethod
    def clean_tags(value: object) -> list[str]:
        """A fleet push's tags, trimmed and de-duplicated. Absent is none, and nothing is invented."""
        items = value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text.split(",")
            items = parsed if isinstance(parsed, list) else [parsed]
        if not isinstance(items, (list, tuple)):
            return []
        out: list[str] = []
        for item in items:
            tag = str(item or "").strip()[:48]
            if tag and tag not in out:
                out.append(tag)
            if len(out) >= 12:
                break
        return out

    def sync_from_hub(
        self,
        voice_id: str,
        *,
        audio_bytes: bytes,
        original_filename: str,
        audio_sha256: str,
        name: str,
        language: str,
        gender: str,
        license: str,
        notes: str = "",
        source_url: Optional[str] = None,
        transcript: Optional[str] = None,
        transcript_segments: object = None,
        permission_acknowledged: bool = False,
        tags: object = None,
    ) -> tuple[Voice, str]:
        """Install or refresh one Hub-owned voice under a stable fleet ID.

        Audio is immutable once an ID exists. Repeating the same request is
        idempotent and may refresh metadata/transcript, while an ID or hash
        collision is refused so a local voice can never be overwritten.

        The aligned timings arrive with the transcript and are stored beside it. A voice installed
        without them is a voice that fails to clone on the models that need the alignment, and that
        failure would appear on each node, one at a time, long after the push looked successful.
        """
        if not re.fullmatch(r"[0-9a-f]{12}", voice_id or ""):
            raise ValueError("voice_id must be exactly 12 lowercase hex characters")
        if not re.fullmatch(r"[0-9a-f]{64}", audio_sha256 or ""):
            raise ValueError("audio_sha256 must be a lowercase SHA-256 digest")
        actual_sha256 = hashlib.sha256(audio_bytes).hexdigest()
        if actual_sha256 != audio_sha256:
            raise ValueError("audio SHA-256 does not match the uploaded file")
        self._validate_voice_input(
            audio_bytes=audio_bytes,
            original_filename=original_filename,
            name=name,
            language=language,
            gender=gender,
            license=license,
            permission_acknowledged=permission_acknowledged,
        )
        ext = Path(original_filename).suffix.lower()
        cleaned_transcript = (transcript or "").strip()
        cleaned_segments = _clean_segments(transcript_segments)
        cleaned_tags = self.clean_tags(tags)

        with self._lock:
            VOICES_DIR.mkdir(parents=True, exist_ok=True)
            if VOICES_DIR.is_symlink():
                raise ValueError("voice library root may not be a symbolic link")
            target = VOICES_DIR / voice_id
            if target.is_symlink():
                raise FleetVoiceConflict("voice ID points to an unsafe symbolic link")
            current = self._load_voice(target) if target.is_dir() else None
            if target.exists() and current is None:
                raise FleetVoiceConflict("voice ID already exists but is not a valid voice")
            if current is not None:
                if not current.fleet_managed:
                    raise FleetVoiceConflict("voice ID belongs to a local voice")
                existing_path = target / f"{REFERENCE_BASENAME}{current.audio_extension}"
                if existing_path.is_symlink() or not existing_path.is_file():
                    raise FleetVoiceConflict("managed reference audio is missing or unsafe")
                existing_sha256 = current.audio_sha256 or hashlib.sha256(
                    existing_path.read_bytes()
                ).hexdigest()
                if existing_sha256 != audio_sha256 or current.audio_extension != ext:
                    raise FleetVoiceConflict("voice ID already contains different audio")
                updated = Voice(
                    id=voice_id,
                    name=name.strip(),
                    language=language.strip(),
                    gender=gender,
                    notes=(notes or "").strip(),
                    license=license,
                    source_url=(source_url or "").strip() or None,
                    permission_acknowledged=True,
                    has_transcript=bool(cleaned_transcript),
                    audio_extension=current.audio_extension,
                    duration_seconds=current.duration_seconds,
                    sample_rate=current.sample_rate,
                    channels=current.channels,
                    fleet_managed=True,
                    audio_sha256=audio_sha256,
                    tags=cleaned_tags,
                    created_at=current.created_at,
                )
                transcript_path = target / TRANSCRIPT_FILENAME
                if transcript_path.is_symlink():
                    raise FleetVoiceConflict("managed transcript path is unsafe")
                previous_transcript = (
                    transcript_path.read_text(encoding="utf-8").strip()
                    if transcript_path.exists() else ""
                )
                if cleaned_transcript:
                    transcript_path.write_text(cleaned_transcript, encoding="utf-8")
                elif transcript_path.exists():
                    transcript_path.unlink()
                timed_path = target / TIMED_FILENAME
                if timed_path.is_symlink():
                    raise FleetVoiceConflict("managed transcript timing path is unsafe")
                previous_segments = self.segments(voice_id)
                if cleaned_segments:
                    timed_path.write_text(json.dumps(
                        {"transcript": cleaned_transcript, "segments": cleaned_segments},
                        indent=2), encoding="utf-8")
                elif timed_path.exists():
                    # A voice whose transcript was cleared must not keep timings that describe a
                    # transcript nobody has any more.
                    timed_path.unlink()
                changed = (
                    asdict(updated) != asdict(current)
                    or previous_transcript != cleaned_transcript
                    or previous_segments != cleaned_segments
                )
                (target / METADATA_FILENAME).write_text(
                    json.dumps(asdict(updated), indent=2, default=str), encoding="utf-8"
                )
                return updated, "updated" if changed else "current"

            temporary = VOICES_DIR / f".{voice_id}.{uuid.uuid4().hex}.tmp"
            temporary.mkdir(mode=0o700, parents=False, exist_ok=False)
            try:
                audio_path = temporary / f"{REFERENCE_BASENAME}{ext}"
                audio_path.write_bytes(audio_bytes)
                duration_s, sample_rate, channels = self._probe_audio(audio_path)
                if cleaned_transcript:
                    (temporary / TRANSCRIPT_FILENAME).write_text(
                        cleaned_transcript, encoding="utf-8"
                    )
                if cleaned_segments:
                    (temporary / TIMED_FILENAME).write_text(json.dumps(
                        {"transcript": cleaned_transcript, "segments": cleaned_segments},
                        indent=2), encoding="utf-8")
                voice = Voice(
                    id=voice_id,
                    name=name.strip(),
                    language=language.strip(),
                    gender=gender,
                    notes=(notes or "").strip(),
                    license=license,
                    source_url=(source_url or "").strip() or None,
                    permission_acknowledged=True,
                    has_transcript=bool(cleaned_transcript),
                    audio_extension=ext,
                    duration_seconds=duration_s,
                    sample_rate=sample_rate,
                    channels=channels,
                    fleet_managed=True,
                    audio_sha256=audio_sha256,
                    tags=cleaned_tags,
                )
                (temporary / METADATA_FILENAME).write_text(
                    json.dumps(asdict(voice), indent=2, default=str), encoding="utf-8"
                )
                os.replace(temporary, target)
                return voice, "created"
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)

    def delete_fleet_managed(self, voice_id: str, audio_sha256: str) -> bool:
        """Delete only the exact Hub-managed copy; local voices are protected."""
        if not re.fullmatch(r"[0-9a-f]{12}", voice_id or ""):
            return False
        with self._lock:
            target = VOICES_DIR / voice_id
            if target.is_symlink():
                raise FleetVoiceConflict("voice ID points to an unsafe symbolic link")
            current = self._load_voice(target) if target.is_dir() else None
            if current is None:
                return False
            if not current.fleet_managed:
                raise FleetVoiceConflict("voice ID belongs to a local voice")
            if current.audio_sha256 != audio_sha256:
                raise FleetVoiceConflict("audio SHA-256 does not match the managed voice")
            shutil.rmtree(target, ignore_errors=False)
            return True

    def add_from_url(
        self,
        *,
        url: str,
        name: str,
        language: str,
        gender: str,
        license: str,
        notes: str = "",
        source_url: Optional[str] = None,
        transcript: Optional[str] = None,
    ) -> Voice:
        """Download audio from `url` and add to the library. Used by the
        public-domain seed catalog "+ Add to library" button. Treated as
        if it were a user upload, so the same validation + size cap
        applies."""
        if not url or not url.startswith(("http://", "https://")):
            raise ValueError(f"url must be http(s)://...  got {url!r}")
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "VoiceStudio/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                # Cap the read at MAX_BYTES + 1 so we can detect oversize.
                audio_bytes = r.read(MAX_BYTES + 1)
        except Exception as e:
            raise ValueError(f"failed to fetch {url}: {type(e).__name__}: {e}")
        if not audio_bytes:
            raise ValueError(f"{url} returned an empty response")
        if len(audio_bytes) > MAX_BYTES:
            raise ValueError(
                f"fetched audio too large ({len(audio_bytes)/1_000_000:.1f} MB > {MAX_BYTES/1_000_000:.0f} MB limit)"
            )

        # Derive original filename from the URL path for extension detection.
        from urllib.parse import urlparse
        parsed = urlparse(url)
        original_filename = Path(parsed.path).name or "reference.wav"
        if Path(original_filename).suffix.lower() not in ALLOWED_AUDIO_EXTS:
            # Fall back to .wav — most TTS demo URLs are wav anyway.
            original_filename = "reference.wav"

        return self.add(
            audio_bytes=audio_bytes,
            original_filename=original_filename,
            name=name,
            language=language,
            gender=gender,
            license=license,
            notes=notes,
            source_url=source_url or url,
            transcript=transcript,
            permission_acknowledged=True,   # seed catalog entries are PD by construction
        )

    def update(
        self,
        voice_id: str,
        *,
        name: Optional[str] = None,
        language: Optional[str] = None,
        gender: Optional[str] = None,
        license: Optional[str] = None,
        notes: Optional[str] = None,
        source_url: Optional[str] = None,
        transcript: Optional[str] = None,
    ) -> Optional[Voice]:
        """Update a voice's metadata. Audio file is never touched — only the
        metadata.json + transcript.txt on disk. Pass None for any field to
        leave it unchanged. Pass an empty string to CLEAR a field (notes /
        source_url / transcript).

        Returns the updated Voice, or None if voice_id not found.

        Validates the same way `add()` does for fields that are provided
        (name length, license/gender enum membership). Doesn't re-validate
        permission_acknowledged — only the original upload sets that.
        """
        with self._lock:
            current = self.get(voice_id)
            if current is None:
                return None

            # Validate provided fields (skip None = no change).
            if name is not None:
                if not name.strip():
                    raise ValueError("name cannot be empty")
                if len(name) > 200:
                    raise ValueError("name must be at most 200 characters")
            if language is not None and not language.strip():
                raise ValueError("language cannot be empty")
            if license is not None and license not in ALLOWED_LICENSES:
                raise ValueError(
                    f"license must be one of {sorted(ALLOWED_LICENSES)}"
                )
            if gender is not None and gender not in ALLOWED_GENDERS:
                raise ValueError(
                    f"gender must be one of {sorted(ALLOWED_GENDERS)}"
                )
            d = VOICES_DIR / voice_id
            # Transcript lives in its own file, not metadata.json. Treat
            # empty string as "delete the transcript file".
            transcript_path = d / TRANSCRIPT_FILENAME
            has_transcript = current.has_transcript
            if transcript is not None:
                cleaned = transcript.strip()
                previous = (
                    transcript_path.read_text(encoding="utf-8").strip()
                    if transcript_path.exists() and not transcript_path.is_symlink() else ""
                )
                if cleaned:
                    transcript_path.write_text(cleaned)
                    has_transcript = True
                else:
                    # Empty string → remove transcript.
                    if transcript_path.exists():
                        transcript_path.unlink()
                    has_transcript = False
                # The timings belong to the words they were measured against. Editing the
                # transcript left `transcript.timed.json` describing the *old* words, and a clone
                # then picked its window from timings that do not match the audio - a
                # wrong-sounding result with nothing on screen to explain it. Correcting the words
                # invalidates them; transcribing again measures them anew.
                if cleaned != previous:
                    timed_path = d / TIMED_FILENAME
                    if timed_path.is_symlink():
                        raise FleetVoiceConflict("managed transcript timing path is unsafe")
                    timed_path.unlink(missing_ok=True)

            # Build the updated voice. Apply only the fields the caller passed.
            updated = Voice(
                id=current.id,
                name=name.strip() if name is not None else current.name,
                language=language.strip() if language is not None else current.language,
                gender=gender if gender is not None else current.gender,
                notes=(notes.strip() if notes is not None else current.notes),
                license=license if license is not None else current.license,
                source_url=(
                    (source_url.strip() or None) if source_url is not None
                    else current.source_url
                ),
                permission_acknowledged=current.permission_acknowledged,
                has_transcript=has_transcript,
                audio_extension=current.audio_extension,
                duration_seconds=current.duration_seconds,
                sample_rate=current.sample_rate,
                channels=current.channels,
                fleet_managed=current.fleet_managed,
                audio_sha256=current.audio_sha256,
                created_at=current.created_at,
            )
            (d / METADATA_FILENAME).write_text(
                json.dumps(asdict(updated), indent=2, default=str)
            )
            return updated

    def delete(self, voice_id: str) -> bool:
        if not voice_id or "/" in voice_id or "\\" in voice_id or voice_id.startswith("."):
            return False
        d = VOICES_DIR / voice_id
        if not d.is_dir():
            return False
        with self._lock:
            shutil.rmtree(d, ignore_errors=False)
        return True

    def reference_path(self, voice_id: str) -> Optional[Path]:
        v = self.get(voice_id)
        if v is None:
            return None
        return VOICES_DIR / voice_id / f"{REFERENCE_BASENAME}{v.audio_extension}"

    def transcript(self, voice_id: str) -> Optional[str]:
        if not voice_id:
            return None
        p = VOICES_DIR / voice_id / TRANSCRIPT_FILENAME
        if not p.exists():
            return None
        try:
            return p.read_text()
        except OSError:
            return None

    def segments(self, voice_id: str) -> list[dict]:
        """The aligned timings stored beside this voice's transcript, if it has any.

        Same file and shape as the kit ships beside a reference clip and the app reads
        (`transcript.timed.json`), so a voice's timings do not depend on how it got here. A
        reference longer than the model's window cannot be cloned without them: the model has to be
        told which window of the clip the transcript describes.
        """
        if not voice_id:
            return []
        path = VOICES_DIR / voice_id / TIMED_FILENAME
        if path.is_symlink() or not path.is_file():
            return []
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            return []
        raw = value.get("segments") if isinstance(value, dict) else value
        return _clean_segments(raw)

    # ── internals ──────────────────────────────────────────────────

    @staticmethod
    def _validate_voice_input(
        *, audio_bytes: bytes, original_filename: str, name: str,
        language: str, gender: str, license: str,
        permission_acknowledged: bool,
    ) -> None:
        if not name or not name.strip():
            raise ValueError("name is required")
        if len(name) > 200:
            raise ValueError("name must be at most 200 characters")
        if not language or not language.strip():
            raise ValueError("language is required")
        if license not in ALLOWED_LICENSES:
            raise ValueError(f"license must be one of {sorted(ALLOWED_LICENSES)}")
        if gender not in ALLOWED_GENDERS:
            raise ValueError(f"gender must be one of {sorted(ALLOWED_GENDERS)}")
        if not permission_acknowledged:
            raise ValueError(
                "permission_acknowledged is required — confirm you have rights to clone this voice"
            )
        if not audio_bytes:
            raise ValueError("audio file is empty")
        if len(audio_bytes) > MAX_BYTES:
            raise ValueError(f"audio file is too large ({len(audio_bytes)} bytes)")
        ext = Path(original_filename).suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTS:
            raise ValueError(
                f"audio extension {ext!r} not allowed (allowed: {sorted(ALLOWED_AUDIO_EXTS)})"
            )

    @staticmethod
    def _probe_audio(path: Path) -> tuple[Optional[float], Optional[int], Optional[int]]:
        """Return (duration_seconds, sample_rate, channels) or (None, None, None)
        if the format isn't readable by soundfile."""
        try:
            import soundfile as sf
            info = sf.info(str(path))
            return (float(info.duration), int(info.samplerate), int(info.channels))
        except Exception:
            return (None, None, None)

    @staticmethod
    def _load_voice(d: Path) -> Optional[Voice]:
        meta = d / METADATA_FILENAME
        if not meta.exists():
            return None
        try:
            data = json.loads(meta.read_text())
            had_provider_metadata = "providers" in data
            # Drop any fields that aren't part of the current dataclass schema
            # (forward-compat for adding fields later).
            allowed = {f for f in Voice.__dataclass_fields__}
            data = {k: v for k, v in data.items() if k in allowed}
            voice = Voice(**data)
            if had_provider_metadata:
                meta.write_text(json.dumps(asdict(voice), indent=2, default=str))
            return voice
        except Exception:
            return None


library = VoiceLibrary()


class FleetVoiceConflict(ValueError):
    """A stable fleet ID would overwrite unrelated or changed local data."""
