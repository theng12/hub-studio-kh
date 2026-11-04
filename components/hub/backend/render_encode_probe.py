"""Bounded encoder probe for one exact six-field encode tuple.

The pinned compiler says which encoder a tuple builds; only a real encode proves this
Mac can run it. `-encoders` is a catalogue, never evidence. This helper encodes one short
synthetic clip with the compiler's own encoder arguments, validates the result with the
pinned ffprobe, and returns strict evidence - or nothing.

Scope, stated plainly: this is an encoder probe. It is not a render measurement, not a
speed or memory measurement and not a fidelity qualification. It proves that one tuple
encodes here, on this tool, in this scratch. It says nothing else.

H.264 in MP4 is the render lane's codec, so an H.264 probe is scoped `render_lane`.
HEVC in MP4 is probed as a capability only (`scope: capability_only`): the shipped
renderer does not admit it and this helper never widens that. The compiler hardcodes
`h264_videotoolbox` and has no hardware HEVC expression, so a hardware HEVC probe uses
an explicit probe-only vector derived from the compiler's own options; the evidence says
`compiler_args: false` so nobody can mistake it for a compiler tier. HEVC is never
claimed from an H.264 output: the probed file's own codec is what the evidence records.

Process lifetime - spawn, wall-clock bound, process-tree cleanup, byte caps - belongs to
the caller through the injected runner. This module owns argv and evidence only, never
opens a shell, and never writes outside the private scratch it is given.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .file_manifest import sha256_file
# The compiler's own encoder arguments, not a copy of its table: the probe then measures
# exactly what a job would run, and cannot drift from it.
from .final_render_compiler import (
    UnsupportedFinalRenderCombination,
    _bitrate_cap,
    _encoder_args,
    _videotoolbox_quality,
)
from .render_encode import plan_for, primary_pair

FORMAT = "studio.render.encode-probe.v1"

# The end-to-end lane renders H.264 in MP4 today. HEVC in MP4 is probed as a capability
# only: proving a Mac can encode HEVC says nothing about the render lane, and this helper
# never widens what the live renderer admits or what the output validator accepts.
CEILING_CONTAINER = "mp4"
RENDER_LANE_CODEC = "h264"
PROBEABLE_CODECS = ("h264", "h265")

# ffprobe names the codec it found; the tuple names the codec that was asked for.
_PROBE_CODEC_NAMES = {"h264": "h264", "h265": "hevc"}

_ENCODER_KEYS = ("container", "requested_codec", "requested_effort", "crf",
                 "quality_cpu_preset", "bitrate_kbps")

_DIAGNOSTIC_TAIL_BYTES = 4096
_FFPROBE_LIMIT_BYTES = 1024 * 1024
_DURATION_TOLERANCE_SEC = 0.5


class UnsupportedEncodeProbe(ValueError):
    """This tuple cannot be probed here: malformed, refused, or outside the ceiling."""


class EncodeProbeError(RuntimeError):
    """The probe ran and could not prove the tuple. The reason says why."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ProbeRun:
    """What one finished process said. The caller owns the process; this is its report."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    wall_seconds: float = 0.0


# (argv, timeout_seconds) -> ProbeRun. The runner must bound the wall clock, clean up the
# process tree and cap the bytes it returns.
ProbeRunner = Callable[[Sequence[str], float], ProbeRun]


@dataclass(frozen=True)
class EncodeProbePlan:
    """The exact commands and expectations for one tuple. Building it runs nothing."""

    encode: dict[str, Any]
    tier: str
    exact_encoder: str
    encoder_args: tuple[str, ...]
    ffmpeg_argv: tuple[str, ...]
    ffprobe_argv: tuple[str, ...]
    output_path: Path
    expected: dict[str, Any]
    # What the evidence is about. "render_lane" may back the shipped H.264 lane;
    # "capability_only" is a fact about this Mac's encoders and nothing more.
    scope: str = "render_lane"
    # True when the encoder arguments are the compiler's own for this codec.
    compiler_args: bool = True


def _encoder_name(args: Sequence[str]) -> str | None:
    """The output encoder a set of ffmpeg arguments names."""
    for index, token in enumerate(args):
        if token == "-c:v" and index + 1 < len(args):
            return args[index + 1]
    return None


def _videotoolbox_args(codec: str, crf: int, bitrate: object) -> tuple[str, ...]:
    """Hardware VideoToolbox arguments for a codec, from the compiler's own options.

    The compiler hardcodes `h264_videotoolbox` and has no hardware HEVC expression, so
    this is a probe-only vector: the compiler's own hardware arguments with the encoder
    name and the HEVC tag adjusted, exactly as the compiler adjusts them for libx265.
    It is never a render tier and never a render claim.
    """
    args = list(_encoder_args("h264", "mp4", crf, "hardware", bitrate))
    encoder = "hevc_videotoolbox" if codec == "h265" else "h264_videotoolbox"
    args[args.index("-c:v") + 1] = encoder
    if codec == "h265":
        args.insert(args.index("-c:a"), "-tag:v")
        args.insert(args.index("-c:a"), "hvc1")
    return tuple(args)


def _tuple_values(encode: Mapping[str, Any]) -> dict[str, Any]:
    """The six fields, exact and typed. Everything else about the tuple is the compiler's."""
    if type(encode) is not dict or set(encode) != set(_ENCODER_KEYS):
        raise UnsupportedEncodeProbe("the encode tuple is not the closed six-field form")
    values = {name: encode[name] for name in _ENCODER_KEYS}
    for name in ("container", "requested_codec", "requested_effort", "quality_cpu_preset"):
        if type(values[name]) is not str or not values[name]:
            raise UnsupportedEncodeProbe("encode field is not a string: " + name)
    if type(values["crf"]) is not int:
        raise UnsupportedEncodeProbe("encode crf is not an integer")
    if values["bitrate_kbps"] is not None and type(values["bitrate_kbps"]) is not int:
        raise UnsupportedEncodeProbe("encode bitrate cap is not an integer")
    return values


def _duration_text(duration_sec: float) -> str:
    return str(int(duration_sec)) if float(duration_sec).is_integer() else repr(float(duration_sec))


def _private_scratch(scratch_dir: Path) -> Path:
    """The scratch the probe writes into. Never a model or job library."""
    scratch = Path(scratch_dir).expanduser()
    resolved = scratch.resolve() if scratch.exists() else scratch.absolute()
    for parent in (resolved, *resolved.parents):
        if (parent / ".studiohub" / "owner.json").exists():
            raise EncodeProbeError("the probe scratch sits inside a model library")
    return resolved


def _require_compilable(values: Mapping[str, Any]) -> None:
    """The compiler must accept this tuple's shape, even when it cannot encode it.

    A hardware HEVC tuple is refused by the compiler's own encoder, not by its quality
    table, so the tuple is checked through the compiler and only the encoder differs.
    """
    try:
        plan_for(values)
    except (UnsupportedFinalRenderCombination, TypeError, ValueError, KeyError) as exc:
        raise UnsupportedEncodeProbe(
            "the pinned compiler does not accept this tuple") from exc


def _probe_vector(values: Mapping[str, Any]) -> dict[str, Any]:
    """What to encode for one tuple: the encoder, its tier and whether it is a render tier.

    The compiler answers for H.264 and for software HEVC. It cannot express hardware
    HEVC - it hardcodes `h264_videotoolbox` and labels that tier HEVC - so that case
    gets an explicit probe-only vector and says so, rather than borrowing the
    compiler's H.264 answer.
    """
    codec = values["requested_codec"]
    effort = values["requested_effort"]
    container = values["container"]
    crf = int(values["crf"])
    bitrate = values["bitrate_kbps"]
    if codec == RENDER_LANE_CODEC:
        args = _encoder_args(codec, container, crf, effort, bitrate)
        pair = primary_pair(values)
        if pair is None:
            raise UnsupportedEncodeProbe("the pinned compiler does not build this tuple")
        tier, exact_encoder = pair
        return {"tier": tier, "exact_encoder": exact_encoder,
                "encoder_args": tuple(args), "scope": "render_lane",
                "compiler_args": True}
    if effort == "hardware":
        # Probe-only: the compiler has no hardware HEVC expression.
        _require_compilable(values)
        args = _videotoolbox_args(codec, crf, bitrate)
        cap = _bitrate_cap(bitrate)
        detail = f"{cap} kbit/s" if cap else f"q:v {_videotoolbox_quality(crf)}"
        return {
            "tier": f"HEVC (H.265) · hardware (videotoolbox, {detail}) [probe only]",
            "exact_encoder": "hevc_videotoolbox",
            "encoder_args": tuple(args),
            "scope": "capability_only",
            "compiler_args": False,
        }
    # Software HEVC: the compiler's own libx265 tier, probed as a capability only.
    args = _encoder_args("h265", container, crf, effort, bitrate)
    pair = primary_pair(values)
    if pair is None:
        raise UnsupportedEncodeProbe("the pinned compiler does not build this tuple")
    tier, exact_encoder = pair
    if _encoder_name(tuple(args)) != exact_encoder:
        raise UnsupportedEncodeProbe("the compiler answer does not match the codec")
    return {"tier": tier, "exact_encoder": exact_encoder, "encoder_args": tuple(args),
            "scope": "capability_only", "compiler_args": True}


def plan_encode_probe(encode: Mapping[str, Any], *, ffmpeg: Path, ffprobe: Path,
                      scratch_dir: Path, canvas_width: int = 1920,
                      canvas_height: int = 1080, fps: int = 24,
                      duration_sec: float = 2.0) -> EncodeProbePlan:
    """The commands and the expectations for one tuple. Nothing is run here.

    The encoder arguments are the pinned compiler's own, so the probe measures what a job
    would run rather than a copy of the table that could drift from it. HEVC in MP4 is
    probed as a capability only and never as a render claim.
    """
    values = _tuple_values(encode)
    if values["container"] != CEILING_CONTAINER:
        raise UnsupportedEncodeProbe("only the MP4 container is probed today")
    if values["requested_codec"] not in PROBEABLE_CODECS:
        raise UnsupportedEncodeProbe("this codec is not probed today")
    if type(canvas_width) is not int or type(canvas_height) is not int \
            or canvas_width < 2 or canvas_height < 2 \
            or canvas_width % 2 or canvas_height % 2:
        raise UnsupportedEncodeProbe("the probe canvas is not an even pixel pair")
    if type(fps) is not int or fps < 1:
        raise UnsupportedEncodeProbe("the probe frame rate is not a positive integer")
    if not isinstance(duration_sec, (int, float)) or isinstance(duration_sec, bool) \
            or not math.isfinite(duration_sec) or not float(duration_sec) > 0:
        raise UnsupportedEncodeProbe("the probe duration is not a positive number")

    vector = _probe_vector(values)
    tier = vector["tier"]
    exact_encoder = vector["exact_encoder"]
    encoder_args = vector["encoder_args"]
    if _encoder_name(encoder_args) != exact_encoder:
        raise UnsupportedEncodeProbe("the probe vector names no matching encoder")

    scratch = _private_scratch(Path(scratch_dir))
    identity = dict(values, canvas=[canvas_width, canvas_height], fps=fps,
                    duration_sec=float(duration_sec), scope=vector["scope"])
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    output_path = scratch / f"encode-probe-{digest[:16]}.mp4"
    video_source = (f"color=c=black:s={canvas_width}x{canvas_height}:r={fps}"
                    f":d={_duration_text(duration_sec)}")
    audio_source = (f"sine=frequency=440:sample_rate=48000"
                    f":duration={_duration_text(duration_sec)}")
    ffmpeg_argv = (
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
        "-f", "lavfi", "-i", video_source,
        "-f", "lavfi", "-i", audio_source,
        "-map", "0:v", "-map", "1:a",
        *encoder_args,
        "-t", _duration_text(duration_sec),
        str(output_path),
    )
    ffprobe_argv = (
        str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json",
        "-protocol_whitelist", "file,pipe", str(output_path),
    )
    expected = {
        "container": CEILING_CONTAINER,
        "video_codec": _PROBE_CODEC_NAMES[values["requested_codec"]],
        "requested_codec": values["requested_codec"],
        "audio_codec": "aac",
        "pix_fmt": "yuv420p",
        "width": canvas_width,
        "height": canvas_height,
        "fps": fps,
        "duration_sec": float(duration_sec),
    }
    return EncodeProbePlan(
        encode=values, tier=tier, exact_encoder=exact_encoder,
        encoder_args=tuple(encoder_args), ffmpeg_argv=ffmpeg_argv,
        ffprobe_argv=ffprobe_argv, output_path=output_path, expected=expected,
        scope=vector["scope"], compiler_args=vector["compiler_args"])


def _tail(data: bytes) -> str:
    return data[-_DIAGNOSTIC_TAIL_BYTES:].decode("utf-8", "replace")


def _stream_of(streams: list[Any], kind: str) -> dict[str, Any]:
    for stream in streams:
        if type(stream) is dict and stream.get("codec_type") == kind:
            return stream
    raise EncodeProbeError(f"the encoded clip has no {kind} stream")


def _measure(report: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    """Compare the probed file against what the encoder arguments promised."""
    streams = report.get("streams")
    if type(streams) is not list or len(streams) != 2:
        raise EncodeProbeError("the encoded clip must have exactly two streams")
    video = _stream_of(streams, "video")
    audio = _stream_of(streams, "audio")
    if video.get("codec_name") != expected["video_codec"]:
        raise EncodeProbeError("the encoded video is not the expected codec",
                               str(video.get("codec_name")))
    if video.get("pix_fmt") != expected["pix_fmt"]:
        raise EncodeProbeError("the encoded video is not the expected pixel format",
                               str(video.get("pix_fmt")))
    if audio.get("codec_name") != expected["audio_codec"]:
        raise EncodeProbeError("the encoded audio is not the expected codec",
                               str(audio.get("codec_name")))
    if (video.get("width") != expected["width"]
            or video.get("height") != expected["height"]):
        raise EncodeProbeError("the encoded video is not the expected canvas",
                               f"{video.get('width')}x{video.get('height')}")
    try:
        measured_fps = Fraction(str(video.get("avg_frame_rate")))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise EncodeProbeError("the encoded video has no usable frame rate") from exc
    if measured_fps != Fraction(expected["fps"]):
        raise EncodeProbeError("the encoded video is not the expected frame rate",
                               str(measured_fps))
    try:
        duration = float(video.get("duration"))
    except (TypeError, ValueError) as exc:
        raise EncodeProbeError("the encoded video has no duration") from exc
    if not math.isfinite(duration) or abs(duration - expected["duration_sec"]) > _DURATION_TOLERANCE_SEC:
        raise EncodeProbeError("the encoded video is not the expected duration",
                               str(duration))
    try:
        audio_duration = float(audio.get("duration"))
    except (TypeError, ValueError) as exc:
        raise EncodeProbeError("the encoded audio has no duration") from exc
    if not math.isfinite(audio_duration) or abs(audio_duration - expected["duration_sec"]) > _DURATION_TOLERANCE_SEC:
        raise EncodeProbeError("the encoded audio is not the expected duration",
                               str(audio_duration))
    container = report.get("format")
    format_name = container.get("format_name") if type(container) is dict else None
    if type(format_name) is not str or CEILING_CONTAINER not in format_name.split(","):
        raise EncodeProbeError("the encoded file is not in the expected container",
                               str(format_name))
    return {
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "pix_fmt": video.get("pix_fmt"),
        "width": video.get("width"),
        "height": video.get("height"),
        "avg_frame_rate": str(video.get("avg_frame_rate")),
        "duration_sec": duration,
        "format_name": format_name,
        "bit_rate": container.get("bit_rate") if type(container) is dict else None,
    }


def probe_encode_support(encode: Mapping[str, Any], *, ffmpeg: Path, ffprobe: Path,
                         scratch_dir: Path, runner: ProbeRunner,
                         tool_identity: Mapping[str, Any],
                         hardware_identity: Mapping[str, Any],
                         canvas_width: int = 1920, canvas_height: int = 1080,
                         fps: int = 24, duration_sec: float = 2.0,
                         timeout_seconds: float = 120.0) -> dict[str, Any]:
    """Encode one tuple for real and return the evidence, or raise and return nothing.

    The evidence is strict: it exists only after the encoder exited zero and the probed
    file itself proved the expected codec, pixel format, canvas, frame rate and timing.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise EncodeProbeError("the process wall bound must be finite and positive")
    plan = plan_encode_probe(encode, ffmpeg=ffmpeg, ffprobe=ffprobe,
                             scratch_dir=scratch_dir, canvas_width=canvas_width,
                             canvas_height=canvas_height, fps=fps,
                             duration_sec=duration_sec)
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    # The process owner must allocate fresh scratch; preserve previous evidence.
    if plan.output_path.exists() or plan.output_path.is_symlink():
        raise EncodeProbeError("the probe output already exists")
    try:
        run = runner(plan.ffmpeg_argv, timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - a failed launch is a failed probe
        raise EncodeProbeError("the encoder could not be run", str(exc)) from exc
    if not math.isfinite(run.wall_seconds) or not 0 <= run.wall_seconds <= timeout_seconds:
        raise EncodeProbeError("the encoder exceeded its wall bound",
                               str(run.wall_seconds))
    if run.returncode != 0:
        raise EncodeProbeError("the encoder exited with a non-zero code",
                               _tail(run.stderr))
    if plan.output_path.is_symlink() or not plan.output_path.is_file():
        raise EncodeProbeError("the encoder produced no regular output file")
    try:
        size = plan.output_path.stat().st_size
    except OSError as exc:
        raise EncodeProbeError("the encoder produced no output file") from exc
    if size < 1:
        raise EncodeProbeError("the encoder produced an empty output file")
    try:
        probe = runner(plan.ffprobe_argv, timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - a failed launch is a failed probe
        raise EncodeProbeError("ffprobe could not be run", str(exc)) from exc
    if not math.isfinite(probe.wall_seconds) or not 0 <= probe.wall_seconds <= timeout_seconds:
        raise EncodeProbeError("ffprobe exceeded its wall bound", str(probe.wall_seconds))
    if probe.returncode != 0:
        raise EncodeProbeError("ffprobe exited with a non-zero code", _tail(probe.stderr))
    if len(probe.stdout) > _FFPROBE_LIMIT_BYTES:
        raise EncodeProbeError("ffprobe metadata exceeded its bound")
    try:
        report = json.loads(probe.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EncodeProbeError("ffprobe did not return a JSON report") from exc
    if type(report) is not dict:
        raise EncodeProbeError("ffprobe did not return a JSON report")
    measured = _measure(report, plan.expected)
    measured["output_size_bytes"] = size
    measured["output_sha256"] = sha256_file(plan.output_path)
    measured["ffprobe_sha256"] = hashlib.sha256(probe.stdout).hexdigest()
    return {
        "format": FORMAT,
        "kind": "encoder_probe",
        "scope": plan.scope,
        "encode": dict(plan.encode),
        "tier": plan.tier,
        # The compiler's own tier label, which is what an authority document names.
        # A probe-only vector has no compiler tier, so it publishes none.
        "selected_tier": plan.tier if plan.compiler_args else None,
        "exact_encoder": plan.exact_encoder,
        "encoder_args": list(plan.encoder_args),
        # False says the arguments are this helper's probe-only vector, not the
        # compiler's own: a reader can tell a compiler tier from a probe vector.
        "compiler_args": plan.compiler_args,
        "tool_identity": dict(tool_identity),
        "hardware_identity": dict(hardware_identity),
        "ffmpeg_argv": list(plan.ffmpeg_argv),
        "ffprobe_argv": list(plan.ffprobe_argv),
        "output_path": str(plan.output_path),
        "expected": dict(plan.expected),
        "measured": measured,
        "wall_seconds": {"ffmpeg": run.wall_seconds, "ffprobe": probe.wall_seconds},
        "observed_at": time.time(),
        "note": ("an encoder probe, not a render measurement and not a fidelity "
                 "qualification"),
    }
