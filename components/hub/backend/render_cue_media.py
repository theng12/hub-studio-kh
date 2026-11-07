"""Receiver-side VP9-alpha, CFR, and media integrity validator for cue_overlay assets.

Authority: Story Studio KH studio.render.cue-overlay.v1 qualification contract (R4).
Validates WebM/VP9/yuva420p media through pinned ffprobe and vetted file descriptors,
deriving exact internal evidence facts without trusting caller assertions or metadata tags alone.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Mapping, Optional

REFUSAL_MEDIA_UNSUPPORTED = "cue_overlay_media_unsupported"
REFUSAL_MEDIA_INTEGRITY = "cue_overlay_media_integrity"
REFUSAL_BOUNDS = "cue_overlay_bounds"

ACCEPTED_PIXEL_FORMATS = ("yuva420p",)
CFR_TOLERANCE_SECONDS = 0.002

_PROBE_EXACT_KEYS = frozenset({
    "container",
    "video_streams",
    "codec",
    "pix_fmt",
    "width",
    "height",
    "fps",
    "frame_count",
    "audio_streams",
    "subtitle_streams",
    "data_streams",
    "attachment_streams",
    "alpha_mode",
    "decoded_alpha",
    "cfr",
    "pts_monotonic",
})


class CueOverlayMediaValidationError(ValueError):
    """Raised when a cue_overlay media asset violates contract rules."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def canvas_within_bounds(width: Any, height: Any, bounds: Mapping[str, Any]) -> bool:
    """Verify decoded canvas dimensions meet profile execution bounds and parity rules."""
    if not isinstance(width, int) or not isinstance(height, int) or width < 2 or height < 2:
        return False
    if width % 2 != 0 or height % 2 != 0:
        return False
    version = bounds.get("bounds_version")
    if version == 2:
        canvas_range = bounds.get("canvas_range")
        if not isinstance(canvas_range, Mapping):
            return False
        max_edge = canvas_range.get("max_edge")
        max_pixels = canvas_range.get("max_pixels")
        if not isinstance(max_edge, int) or not isinstance(max_pixels, int):
            return False
        return width <= max_edge and height <= max_edge and (width * height) <= max_pixels
    if version == 1:
        pairs = bounds.get("canvas_pairs", [])
        return [width, height] in pairs or (width, height) in pairs
    return False


def _safe_fd(asset: Any) -> tuple[int, bool]:
    """Obtain a safe file descriptor from a stream, reader, int, or vetted path.

    Returns (fd, needs_close). Refuses unverified string shapes, URLs, or symlink paths.
    Enforces that opened assets are regular files.
    """
    if hasattr(asset, "fileno") and callable(asset.fileno):
        fd = int(asset.fileno())
        if fd < 0:
            raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, "negative asset descriptor")
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"asset descriptor {fd} is not a regular file")
        except OSError as exc:
            raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"cannot stat asset descriptor {fd}: {exc}") from exc
        return fd, False

    if isinstance(asset, int):
        if asset < 0:
            raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, "negative asset descriptor")
        try:
            st = os.fstat(asset)
            if not stat.S_ISREG(st.st_mode):
                raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"asset descriptor {asset} is not a regular file")
        except OSError as exc:
            raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"cannot stat asset descriptor {asset}: {exc}") from exc
        return asset, False

    if isinstance(asset, (str, Path)):
        raw_str = str(asset)
        if "://" in raw_str:
            raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, "asset path must not be a URL")
        if any(c in raw_str for c in ("|", ";", "&", "<", ">", "$", "`")):
            raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, "asset path contains forbidden characters")
        if raw_str.startswith("/dev/fd/"):
            try:
                fd_num = int(raw_str[len("/dev/fd/"):])
                if fd_num < 0:
                    raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, "negative asset descriptor")
                try:
                    st = os.fstat(fd_num)
                    if not stat.S_ISREG(st.st_mode):
                        raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"asset descriptor {fd_num} is not a regular file")
                except OSError as exc:
                    raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"cannot stat asset descriptor {fd_num}: {exc}") from exc
                return fd_num, False
            except ValueError:
                raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, "malformed /dev/fd path")

        path = Path(asset)
        if path.is_symlink():
            raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, "symlink asset paths are forbidden")
        try:
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                os.close(fd)
                raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"asset {path} is not a regular file")
        except (OSError, FileNotFoundError) as exc:
            raise CueOverlayMediaValidationError(
                REFUSAL_MEDIA_UNSUPPORTED, f"cannot open asset path securely: {exc}") from exc
        return fd, True

    raise CueOverlayMediaValidationError(REFUSAL_MEDIA_UNSUPPORTED, f"unsupported asset handle type: {type(asset)}")


def _measure_stream(fd: int) -> tuple[int, str]:
    """Compute exact byte count and sha256 of the asset without moving file seek pointer."""
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise CueOverlayMediaValidationError(
            REFUSAL_MEDIA_UNSUPPORTED, f"cannot stat asset descriptor {fd}: {exc}") from exc

    if not stat.S_ISREG(st.st_mode):
        raise CueOverlayMediaValidationError(
            REFUSAL_MEDIA_UNSUPPORTED, f"asset descriptor {fd} is not a regular file")

    total_bytes = st.st_size
    hasher = hashlib.sha256()
    offset = 0
    chunk_size = 1024 * 1024
    while offset < total_bytes:
        read_len = min(chunk_size, total_bytes - offset)
        chunk = os.pread(fd, read_len, offset)
        if not chunk:
            break
        hasher.update(chunk)
        offset += len(chunk)

    return total_bytes, hasher.hexdigest()


def probe_cue_overlay_media(
    ffprobe_bin: str | Path,
    asset: Any,
    *,
    expected_fps: float | int | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Execute pinned ffprobe on the vetted asset descriptor and derive the 16 exact media evidence facts.

    Uses libvpx-vp9 decoder to unpack decoded frame pixel format and timestamps.
    """
    ffprobe_path = Path(ffprobe_bin)
    if not ffprobe_path.is_file():
        raise CueOverlayMediaValidationError(
            REFUSAL_MEDIA_UNSUPPORTED, f"pinned ffprobe binary not found at {ffprobe_path}")

    fd, needs_close = _safe_fd(asset)
    target_spec = f"/dev/fd/{fd}"

    argv = [
        str(ffprobe_path),
        "-v", "error",
        "-codec:v", "libvpx-vp9",
        "-show_streams",
        "-show_format",
        "-show_entries", "frame=pts_time,pix_fmt",
        "-of", "json",
        "-protocol_whitelist", "file,pipe,fd",
        target_spec,
    ]

    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            pass_fds=(fd,),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise CueOverlayMediaValidationError(
            REFUSAL_MEDIA_UNSUPPORTED, f"ffprobe execution failed: {exc}") from exc
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass
        if hasattr(asset, "seek") and callable(asset.seek):
            try:
                asset.seek(0)
            except Exception:
                pass
        if needs_close:
            try:
                os.close(fd)
            except OSError:
                pass

    if proc.returncode != 0:
        raise CueOverlayMediaValidationError(
            REFUSAL_MEDIA_UNSUPPORTED, f"ffprobe rejected media (exit {proc.returncode}): {proc.stderr[:200]}")

    try:
        doc = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise CueOverlayMediaValidationError(
            REFUSAL_MEDIA_UNSUPPORTED, f"ffprobe returned unparseable JSON: {exc}") from exc

    fmt = doc.get("format") if isinstance(doc.get("format"), dict) else {}
    format_name = fmt.get("format_name", "")
    if "webm" in format_name.split(","):
        container = "webm"
    else:
        container = format_name.split(",")[0] if format_name else ""

    streams = doc.get("streams", []) if isinstance(doc.get("streams"), list) else []
    video_streams = sum(1 for s in streams if s.get("codec_type") == "video")
    audio_streams = sum(1 for s in streams if s.get("codec_type") == "audio")
    subtitle_streams = sum(1 for s in streams if s.get("codec_type") == "subtitle")
    data_streams = sum(1 for s in streams if s.get("codec_type") == "data")
    attachment_streams = sum(1 for s in streams if s.get("codec_type") == "attachment")

    if video_streams >= 1:
        v = next(s for s in streams if s.get("codec_type") == "video")
        codec = v.get("codec_name", "")
        pix_fmt = v.get("pix_fmt", "")
        width = int(v.get("width", 0))
        height = int(v.get("height", 0))

        tags = v.get("tags") if isinstance(v.get("tags"), dict) else {}
        alpha_tag = tags.get("alpha_mode") or tags.get("ALPHA_MODE") or v.get("alpha_mode")
        alpha_mode = str(alpha_tag).strip() in ("1", "true") or alpha_tag == 1

        r_rate = str(v.get("r_frame_rate", "0/0"))
        if "/" in r_rate:
            num_s, den_s = r_rate.split("/", 1)
            try:
                num, den = float(num_s), float(den_s)
                fps_val = num / den if den != 0 else 0.0
            except ValueError:
                fps_val = 0.0
        else:
            try:
                fps_val = float(r_rate)
            except ValueError:
                fps_val = 0.0

        fps = int(fps_val) if fps_val.is_integer() and fps_val > 0 else fps_val
    else:
        codec = ""
        pix_fmt = ""
        width = 0
        height = 0
        fps = 0
        alpha_mode = False

    frames = doc.get("frames", []) if isinstance(doc.get("frames"), list) else []
    frame_count = len(frames)

    if frame_count == 0:
        decoded_alpha = False
        pts_monotonic = False
        cfr = False
    else:
        decoded_alpha = all(f.get("pix_fmt") in ACCEPTED_PIXEL_FORMATS for f in frames)

        pts_list: list[float] = []
        parse_ok = True
        for f in frames:
            t_str = f.get("pts_time")
            if t_str is None:
                parse_ok = False
                break
            try:
                pts_list.append(float(t_str))
            except ValueError:
                parse_ok = False
                break

        if not parse_ok or len(pts_list) != frame_count:
            pts_monotonic = False
            cfr = False
        elif frame_count == 1:
            pts_monotonic = pts_list[0] >= 0
            cfr = True
        else:
            pts_monotonic = all(pts_list[i] > pts_list[i - 1] for i in range(1, frame_count))
            target_rate = float(expected_fps) if expected_fps is not None and expected_fps > 0 else float(fps)
            if target_rate > 0:
                expected_delta = 1.0 / target_rate
                cfr = all(
                    abs(pts_list[i] - pts_list[i - 1] - expected_delta) <= CFR_TOLERANCE_SECONDS
                    for i in range(1, frame_count)
                )
            else:
                cfr = False

    return {
        "container": container,
        "video_streams": video_streams,
        "codec": codec,
        "pix_fmt": pix_fmt,
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
        "data_streams": data_streams,
        "attachment_streams": attachment_streams,
        "alpha_mode": alpha_mode,
        "decoded_alpha": decoded_alpha,
        "cfr": cfr,
        "pts_monotonic": pts_monotonic,
    }


def check_cue_overlay_probe(
    probe: Mapping[str, Any],
    *,
    recipe_canvas: Mapping[str, Any],
    time_range: Mapping[str, Any],
    manifest_size_bytes: int,
    execution_bounds: Mapping[str, Any],
) -> str | None:
    """Validate receiver-derived media probe dictionary against R4 contract rules.

    Returns None on success, or the exact contract refusal string on violation.
    """
    if set(probe.keys()) != _PROBE_EXACT_KEYS:
        return REFUSAL_MEDIA_UNSUPPORTED

    if probe["container"] != "webm":
        return REFUSAL_MEDIA_UNSUPPORTED
    if probe["video_streams"] != 1:
        return REFUSAL_MEDIA_UNSUPPORTED
    if probe["codec"] != "vp9":
        return REFUSAL_MEDIA_UNSUPPORTED
    if probe["pix_fmt"] not in ACCEPTED_PIXEL_FORMATS:
        return REFUSAL_MEDIA_UNSUPPORTED
    if any(probe[k] != 0 for k in ("audio_streams", "subtitle_streams", "data_streams", "attachment_streams")):
        return REFUSAL_MEDIA_UNSUPPORTED

    if probe["alpha_mode"] is not True or probe["decoded_alpha"] is not True:
        return REFUSAL_MEDIA_UNSUPPORTED
    if probe["cfr"] is not True or probe["pts_monotonic"] is not True:
        return REFUSAL_MEDIA_UNSUPPORTED

    if not canvas_within_bounds(probe["width"], probe["height"], execution_bounds):
        return REFUSAL_MEDIA_UNSUPPORTED
    if probe["width"] != recipe_canvas.get("width") or probe["height"] != recipe_canvas.get("height"):
        return REFUSAL_MEDIA_UNSUPPORTED

    working_fps = recipe_canvas.get("working_fps")
    if probe["fps"] != working_fps:
        return REFUSAL_MEDIA_UNSUPPORTED
    allowed_fps = execution_bounds.get("working_fps", [])
    if probe["fps"] not in allowed_fps:
        return REFUSAL_MEDIA_UNSUPPORTED

    start_sec = time_range.get("start_sec")
    end_sec = time_range.get("end_sec")
    if not isinstance(start_sec, (int, float)) or not isinstance(end_sec, (int, float)):
        return REFUSAL_MEDIA_UNSUPPORTED
    window = end_sec - start_sec
    if window <= 0:
        return REFUSAL_MEDIA_UNSUPPORTED

    required_frames = math.ceil(window * probe["fps"])
    if probe["frame_count"] < required_frames:
        return REFUSAL_MEDIA_UNSUPPORTED

    max_pixel_frames = execution_bounds.get("max_pixel_frames")
    if isinstance(max_pixel_frames, int):
        if probe["frame_count"] * probe["width"] * probe["height"] > max_pixel_frames:
            return REFUSAL_BOUNDS

    max_asset_bytes = execution_bounds.get("max_asset_bytes")
    if isinstance(max_asset_bytes, int):
        if manifest_size_bytes > max_asset_bytes:
            return REFUSAL_BOUNDS

    return None


def validate_cue_overlay_probe(
    probe: Mapping[str, Any],
    *,
    recipe_canvas: Mapping[str, Any],
    time_range: Mapping[str, Any],
    manifest_size_bytes: int,
    execution_bounds: Mapping[str, Any],
) -> None:
    """Validate receiver-derived media probe dictionary, raising CueOverlayMediaValidationError on failure."""
    reason = check_cue_overlay_probe(
        probe,
        recipe_canvas=recipe_canvas,
        time_range=time_range,
        manifest_size_bytes=manifest_size_bytes,
        execution_bounds=execution_bounds,
    )
    if reason is not None:
        raise CueOverlayMediaValidationError(reason)


def validate_cue_overlay_media(
    ffprobe_bin: str | Path,
    asset: Any,
    *,
    recipe_canvas: Mapping[str, Any],
    time_range: Mapping[str, Any],
    execution_bounds: Mapping[str, Any],
    manifest_entry: Optional[Mapping[str, Any]] = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Validate a staged cue_overlay media asset against ACKed R4 contract.

    Natural narrow entry point for RenderExecutor.
    1. If manifest_entry is supplied: measures staged asset byte count and sha256 to prove integrity.
    2. Runs pinned ffprobe against vetted descriptor/handle to derive 16 internal facts.
    3. Validates derived facts against recipe canvas, duration window, and advertised profile bounds.
    4. Returns the 16-field media_probe dictionary.
    """
    fd, needs_close = _safe_fd(asset)
    try:
        max_asset_bytes = execution_bounds.get("max_asset_bytes")
        if manifest_entry is not None:
            expected_size = manifest_entry.get("size_bytes")
            if isinstance(max_asset_bytes, int) and expected_size is not None and expected_size > max_asset_bytes:
                raise CueOverlayMediaValidationError(
                    REFUSAL_BOUNDS,
                    f"manifest size {expected_size} exceeds max_asset_bytes {max_asset_bytes}",
                )
            expected_sha = manifest_entry.get("sha256")
            actual_size, actual_sha = _measure_stream(fd)
            if isinstance(max_asset_bytes, int) and actual_size > max_asset_bytes:
                raise CueOverlayMediaValidationError(
                    REFUSAL_BOUNDS,
                    f"staged size {actual_size} exceeds max_asset_bytes {max_asset_bytes}",
                )
            if expected_size is not None and actual_size != expected_size:
                raise CueOverlayMediaValidationError(
                    REFUSAL_MEDIA_INTEGRITY,
                    f"staged size {actual_size} != manifest size {expected_size}",
                )
            if expected_sha is not None and actual_sha.lower() != str(expected_sha).lower():
                raise CueOverlayMediaValidationError(
                    REFUSAL_MEDIA_INTEGRITY,
                    f"staged sha256 {actual_sha} != manifest sha256 {expected_sha}",
                )
            manifest_size = actual_size
        else:
            actual_size, _ = _measure_stream(fd)
            if isinstance(max_asset_bytes, int) and actual_size > max_asset_bytes:
                raise CueOverlayMediaValidationError(
                    REFUSAL_BOUNDS,
                    f"staged size {actual_size} exceeds max_asset_bytes {max_asset_bytes}",
                )
            manifest_size = actual_size

        working_fps = recipe_canvas.get("working_fps")
        probe = probe_cue_overlay_media(
            ffprobe_bin=ffprobe_bin,
            asset=fd,
            expected_fps=working_fps,
            timeout_seconds=timeout_seconds,
        )

        validate_cue_overlay_probe(
            probe,
            recipe_canvas=recipe_canvas,
            time_range=time_range,
            manifest_size_bytes=manifest_size,
            execution_bounds=execution_bounds,
        )

        return probe
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass
        if hasattr(asset, "seek") and callable(asset.seek):
            try:
                asset.seek(0)
            except Exception:
                pass
        if needs_close:
            try:
                os.close(fd)
            except OSError:
                pass
