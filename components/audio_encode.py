"""Compress a finished lossless audio master to AAC-LC in an ``.m4a`` container.

One place for the Music and Voice workers, because the preset is one fixed, measured decision
(2026-10-01 compression plan), not a knob: a request names the *result* (``output_format: m4a``)
and the lane picks the bitrate.

* ``music``     AAC-LC 256 kbps, sample rate and channels kept (the stereo masters are 44.1/48 kHz).
* ``narration`` AAC-LC 128 kbps, mono. The encoder tops out near 106 kbps for 24 kHz speech whatever
  is asked, and 128k is what spends nothing extra at 48 kHz.

The caller keeps the lossless master until :func:`encode_m4a` returns, because it raises
:class:`EncodeError` on any failure and the caller then delivers the master unchanged. A paid job is
never failed by compression.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

MEDIA_TYPE = "audio/mp4"
FORMAT = "m4a"

# profile -> (bitrate, forced channel count or None)
PROFILES: dict[str, tuple[str, int | None]] = {
    "music": ("256k", None),
    "narration": ("128k", 1),
}

ENCODE_TIMEOUT_SECONDS = 900.0
# Decoded length of the m4a against the master's. AAC keeps sample-exact length through the iPod
# edit list (measured identical to 1 us), so this only has to catch a truncated or empty encode.
DURATION_TOLERANCE_SECONDS = 0.25


class EncodeError(RuntimeError):
    """The master could not be compressed; deliver it unchanged."""


class Mp4Boxes:
    """Walk an MP4/M4A's top-level boxes as its bytes pass, holding nothing but one box header.

    `problem()` says why a player could not open the file: it is empty, does not start with
    ``ftyp``, has no ``moov`` ("moov atom not found"), or ends inside a box (cut off). Shared by the
    encoder, which checks its own output, and the Hub, which checks what a worker delivered.
    """

    def __init__(self) -> None:
        self.total = 0
        self._next = 0              # offset of the next top-level box
        self._header = b""          # that box's header bytes seen so far
        self._first: bytes | None = None
        self._kinds: set[bytes] = set()
        self._broken: str | None = None

    def feed(self, chunk: bytes) -> None:
        start = self.total
        self.total += len(chunk)
        while self._broken is None and self._next < self.total:
            index = self._next + len(self._header) - start
            if index >= len(chunk):
                return
            large = len(self._header) >= 8 and self._header[:4] == b"\0\0\0\1"
            self._header += bytes(chunk[index:index + (16 if large else 8) - len(self._header)])
            if len(self._header) < 8:
                return
            size = int.from_bytes(self._header[:4], "big")
            if size == 1:
                if len(self._header) < 16:
                    continue
                size = int.from_bytes(self._header[8:16], "big")
            kind, header = self._header[4:8], len(self._header)
            self._header = b""
            self._first = self._first or kind
            self._kinds.add(kind)
            if size == 0:                       # the last box, running to the end of the file
                self._next = float("inf")
            elif size < header:
                self._broken = f"box {kind!r} has an impossible size {size}"
            else:
                self._next += size

    def problem(self) -> str | None:
        if self.total == 0:
            return "the file is empty"
        if self._broken:
            return self._broken
        if self._first != b"ftyp":
            return "it does not start with an ftyp box"
        if self._header or float("inf") > self._next > self.total:
            return "it ends inside a box (cut off)"
        if b"moov" not in self._kinds:
            return "it has no moov box"
        return None


def find_tool(name: str = "ffmpeg") -> Path | None:
    """The bundled ffmpeg/ffprobe, found beside the worker's own interpreter.

    The Voice runtime (which the Music worker also runs on) ships both next to ``python3``. The
    environment override is for tests and development; ``PATH`` is the last resort because a worker
    clears it.
    """
    candidates: list[Path] = []
    override = os.environ.get(f"STUDIOHUB_{name.upper()}", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    tools = os.environ.get("STUDIOHUB_TOOLS_DIR", "").strip()
    if tools:
        candidates += [Path(tools) / name, Path(tools) / "bin" / name]
    executable = Path(sys.executable)
    candidates += [executable.parent / name, executable.resolve().parent / name]
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def probe_duration(path: Path, ffprobe: Path | None = None) -> float | None:
    """Container duration in seconds, or None when it cannot be read."""
    ffprobe = ffprobe or find_tool("ffprobe")
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60, check=True)
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def encode_m4a(master: Path, destination: Path, *, profile: str,
               expected_seconds: float | None = None) -> dict:
    """Encode ``master`` to ``destination`` (``.m4a``) and return the delivered file's facts.

    The file is written under a hidden partial name, fsynced, verified (non-empty, ``ftyp`` then
    a ``moov`` box, no box cut off, decoded length within a quarter second of the master), then
    renamed. Anything else raises :class:`EncodeError` after removing the partial; the master is
    never touched.
    """
    bitrate, channels = PROFILES[profile]
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg is None:
        raise EncodeError("ffmpeg is not available for AAC encoding")
    master, destination = Path(master), Path(destination)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-i", str(master), "-vn", "-map_metadata", "-1", "-c:a", "aac", "-b:a", bitrate]
    if channels:
        command += ["-ac", str(channels)]
    command += ["-movflags", "+faststart", "-f", "ipod", str(partial)]
    try:
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=ENCODE_TIMEOUT_SECONDS, start_new_session=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise EncodeError(f"AAC encode did not run: {type(exc).__name__}") from exc
        if result.returncode != 0:
            raise EncodeError("AAC encode failed: " + (result.stderr or "").strip()[-300:])
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise EncodeError("AAC encode produced no file")
        digest, boxes = hashlib.sha256(), Mp4Boxes()
        with partial.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
                boxes.feed(block)
        # The duration probe below is skipped when ffprobe is missing or cannot run, so the
        # container itself is checked here: a file without `moov` must never be delivered.
        if boxes.problem():
            raise EncodeError(f"AAC encode is not a readable MP4: {boxes.problem()}")
        if expected_seconds:
            seconds = probe_duration(partial)
            if seconds is not None and abs(seconds - float(expected_seconds)) > DURATION_TOLERANCE_SECONDS:
                raise EncodeError(
                    f"AAC encode is {seconds:.2f}s but the master is {float(expected_seconds):.2f}s")
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        size = partial.stat().st_size
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return {"path": str(destination), "bytes": size, "sha256": digest.hexdigest(),
            "media_type": MEDIA_TYPE, "format": FORMAT, "codec": "aac-lc",
            "bitrate_kbps": int(bitrate[:-1])}
