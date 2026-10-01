"""Small, dependency-free validation for brokered media artifacts.

The Hub deliberately validates bytes rather than trusting a filename or a
worker's MIME header.  WAV is supported with the standard library because it
is the current Voice Studio/Kokoro output and includes all billable metadata.
Other media types retain a valid upstream Content-Type at proxy time.
"""

from __future__ import annotations

import hashlib
import io
import wave

# The media types each modality's artifacts are expected to carry. This is the *narrowing* set:
# a worker for one of these lanes cannot claim another lane's type.
#
# `transcription` and `chat` were absent here, and a modality absent from the map got the empty
# set - so their artifacts could never be trusted and were proxied as
# `application/octet-stream` instead of inline media. The sets below are the producers' own:
# the Subtitle worker's `ARTIFACT_FORMATS` (srt / vtt / text) plus its JSON segment payload, and
# the Chat worker's JSON completion.
_MEDIA_TYPES = {
    "image": {"image/png", "image/jpeg", "image/webp", "image/gif"},
    "video": {"video/mp4", "video/webm", "video/quicktime"},
    "render": {"video/mp4", "video/webm", "video/quicktime"},
    "voice": {"audio/wav", "audio/mpeg", "audio/flac", "audio/ogg", "audio/mp4"},
    "music": {"audio/wav", "audio/mpeg", "audio/flac", "audio/ogg", "audio/mp4"},
    "transcription": {"application/x-subrip", "text/vtt", "text/plain",
                      "application/json"},
    "chat": {"application/json", "text/plain"},
}

# Every media type the Hub is willing to echo at all. A modality the Hub has never been taught
# is still served honestly when the type is one of these: the security boundary is "a type the
# Hub knows", never "a type the worker said", and an empty per-modality set is what turned a
# real artifact into an anonymous octet stream. A known modality still narrows to its own set.
KNOWN_MEDIA_TYPES = frozenset().union(*_MEDIA_TYPES.values())

# Magic bytes -> the type they prove, for the artifacts the Hub can recognise without a
# container parser. Used when the bytes are available; a filename or a header is never enough.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "audio/wav"),  # refined below: RIFF....WAVE
    (b"ID3", "audio/mpeg"),
    (b"fLaC", "audio/flac"),
    (b"OggS", "audio/ogg"),
    (b"%PDF", "application/pdf"),
)


def sniff_media_type(data: bytes | None) -> str | None:
    """The media type the artifact's own first bytes prove, or ``None``.

    ``wav_metadata`` already validates WAV bytes for billing; this is the cheap magic-byte
    companion for the types the Hub may proxy, so a caller holding bytes never has to guess from
    an extension. Container checks that need a longer header are guarded by length, so a short
    JSON body is still recognised.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 4:
        return None
    head = bytes(data[:16])
    if len(head) >= 12:
        if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
            return "audio/wav"
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return "image/webp"
        if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
            return "video/x-msvideo"
        if head[4:8] == b"ftyp":
            brand = head[8:12]
            if brand in (b"M4A ", b"M4B "):
                # The brand ffmpeg's iPod/M4A muxer writes: an audio-only AAC file, not a video.
                return "audio/mp4"
            return "video/quicktime" if brand.startswith(b"qt") else "video/mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    for magic, media_type in _MAGIC:
        if head.startswith(magic):
            return media_type
    stripped = head.lstrip()
    if stripped[:1] in (b"{", b"["):
        return "application/json"
    if stripped[:6].lower() == b"<html>" or stripped[:5].lower() == b"<!doc":
        return "text/html"
    return None


def trusted_media_type(value: str | None, modality: str) -> str | None:
    """Return a normalized allowed MIME type, never an arbitrary header.

    A modality that declares its own set narrows the answer to it. A modality with no declared
    set - a lane added later - answers with any type the Hub knows, rather than with nothing:
    the alternative is `application/octet-stream` for every artifact of that lane, which is how
    `transcription` and `chat` were being proxied.
    """
    if not isinstance(value, str):
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    allowed = _MEDIA_TYPES.get(modality)
    if allowed is None:
        return media_type if media_type in KNOWN_MEDIA_TYPES else None
    return media_type if media_type in allowed else None


def media_type_for_proxy(modality: str, cached: str | None,
                         upstream: str | None, data: bytes | None = None) -> str:
    """Prefer byte-proven metadata, then a modality-valid header.

    ``data`` is the artifact's own bytes when the caller holds them. Bytes outrank every header:
    a worker's claim about its output is the weakest evidence the Hub has.
    """
    proven = sniff_media_type(data) if data is not None else None
    if proven is not None and (
        proven in KNOWN_MEDIA_TYPES
        or proven in (_MEDIA_TYPES.get(modality) or ())
    ):
        return proven
    return (trusted_media_type(cached, modality)
            or trusted_media_type(upstream, modality)
            or "application/octet-stream")


def wav_metadata(data: bytes) -> dict:
    """Decode a PCM WAV and return immutable, billable media facts.

    ``wave`` reads the RIFF/WAVE structure itself, so neither an extension nor
    a claimed Content-Type can make unrelated bytes appear to be audio.
    """
    if not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        raise ValueError("artifact is not a RIFF/WAVE file")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if audio.getcomptype() != "NONE":
                raise ValueError("compressed WAV is not supported for billing metadata")
            frames = audio.getnframes()
            sample_rate_hz = audio.getframerate()
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            frame_bytes = audio.readframes(frames)
    except (EOFError, wave.Error) as exc:
        raise ValueError(f"invalid WAV artifact: {exc}") from exc
    if frames <= 0 or sample_rate_hz <= 0 or channels not in (1, 2):
        raise ValueError("WAV has invalid duration, sample rate, or channel count")
    if len(frame_bytes) != frames * channels * sample_width:
        raise ValueError("WAV artifact is truncated before its declared frames end")
    duration_s = frames / sample_rate_hz
    return {
        "media_type": "audio/wav",
        "format": "wav",
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "audio_duration_s": round(duration_s, 6),
        "audio_duration_ms": round(duration_s * 1000),
        "sample_rate_hz": sample_rate_hz,
        "channels": channels,
    }
