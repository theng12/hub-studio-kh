"""Pure deterministic final-pass construction for a closed render packet."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import math
import re
from typing import Literal
import zlib

from .render_contract import validate_render_packet_syntax

TierCondition = Literal["always", "never", "libx265_einval"]
_FINAL_AAC_ARGS = ("-c:a", "aac", "-b:a", "384k")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_BLOCK = 64 * 1024
# Validation safety ceiling, not a published capability bound: decoding never
# allocates a full image, and a declared raw image beyond this is refused early.
_PNG_MAX_RAW_BYTES = 64 * 1024 * 1024
_PRESETS = {
    "none": (0, 1, 1, 0, 0),
    "warm-night": (-0.05, 1.1, 0.9, 0.15, 0),
    "cool-moonlight": (0.02, 1.05, 0.8, -0.15, -0.05),
    "cinematic": (-0.03, 1.2, 0.85, 0.05, 0),
    "vintage": (0.05, 0.95, 0.7, 0.2, 0.05),
    "dreamy-haze": (0.08, 0.9, 0.75, 0.1, 0.05),
    "golden-hour": (0.03, 1.1, 1.1, 0.25, 0.02),
    "noir": (-0.1, 1.4, 0, 0, 0),
}
_RATIOS = {"2.35:1": 2.35, "2.00:1": 2.0, "1.85:1": 1.85}


@dataclass(frozen=True)
class EncodeTier:
    label: str
    argv: tuple[str, ...]
    condition: TierCondition = "always"
    note: str | None = None
    degraded: str | None = None
    drops_burned_in_features: bool = False

    def should_run(self, previous_stderr: str = "") -> bool:
        if self.condition == "always":
            return True
        if self.condition == "never":
            return False
        return "libx265" in previous_stderr and "return code -22" in previous_stderr


@dataclass(frozen=True)
class FinalRenderPlan:
    tiers: tuple[EncodeTier, ...]
    requested_burn_ins: tuple[str, ...]
    input_uses: tuple[tuple[str, int], ...]
    generated_inputs: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class LateLabelSequence:
    """Receiver-owned image2 sequence staging requirement; never a sender path."""
    key: str
    logical_ids: tuple[str, ...]
    working_fps: int
    start_sec: float


@dataclass(frozen=True)
class LateLabelInputRequirements:
    sequences: tuple[LateLabelSequence, ...]
    png_logical_ids: tuple[str, ...]


class UnsupportedFinalRenderCombination(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Unsupported final render combination: " + reason)


def _js_round(value: float) -> int:
    integer = math.floor(value)
    return integer + (1 if value - integer >= 0.5 else 0)


def _js_fixed(value: float, digits: int) -> str:
    if value == 0:
        value = 0.0
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal.from_float(float(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return format(rounded, f".{digits}f")


def _js_number(value: int | float) -> str:
    number = float(value)
    if number == 0:
        return "0"
    if number.is_integer() and abs(number) < 1e21:
        return str(int(number))
    rendered = repr(number)
    absolute = abs(number)
    if 1e-6 <= absolute < 1e21:
        return format(Decimal(rendered), "f").rstrip("0").rstrip(".")
    if "e" not in rendered:
        return rendered
    mantissa, exponent = rendered.split("e")
    if mantissa.endswith(".0"):
        mantissa = mantissa[:-2]
    sign = "+" if not exponent.startswith("-") else "-"
    digits = exponent.lstrip("+-0") or "0"
    return f"{mantissa}e{sign}{digits}"


def _build_gate_enable(start: int | float, end: int | float) -> str:
    if start > 0 and end > 0:
        expression = f"between(t,{_js_fixed(start, 2)},{_js_fixed(end, 2)})"
    elif start > 0:
        expression = f"gt(t,{_js_fixed(start, 2)})"
    elif end > 0:
        expression = f"lt(t,{_js_fixed(end, 2)})"
    else:
        return ""
    return f":enable='{expression}'"


def _late_gate_enable(start: int | float, end: int | float) -> str:
    return (f":enable='between(t,{_js_fixed(start, 3)},"
            f"{_js_fixed(end, 3)})'")


def _late_label_operations(packet: Mapping[str, object]) -> tuple[tuple[int, Mapping[str, object]], ...]:
    return tuple(
        (index, operation)
        for index, operation in enumerate(packet["recipe"]["operations"])
        if operation["kind"] == "late_label_stage"
    )


def late_label_input_requirements(raw: bytes) -> LateLabelInputRequirements | None:
    """Exact private PNG IDs and ordered sequence requirements for the receiver."""
    packet = validate_render_packet_syntax(raw)
    operations = _late_label_operations(packet)
    if not operations:
        return None
    canvas = packet["recipe"]["canvas"]
    sequences = []
    png_ids: list[str] = []
    seen: set[str] = set()

    def take(logical_id: str) -> None:
        if logical_id not in seen:
            seen.add(logical_id)
            png_ids.append(logical_id)

    for op_index, operation in operations:
        label = operation["label"]
        if label is not None:
            take(label["asset"]["logical_id"])
        line = operation["line"]
        if line is not None:
            take(line["asset"]["logical_id"])
        for key, purpose in (("reveal", "r"), ("line_reveal", "lr")):
            sequence = operation[key]
            if sequence is None:
                continue
            logical_ids = tuple(item["asset"]["logical_id"]
                                for item in sequence["frames"])
            for logical_id in logical_ids:
                take(logical_id)
            sequences.append(LateLabelSequence(
                key=f"cue_{op_index:04d}_{purpose}",
                logical_ids=logical_ids,
                working_fps=int(canvas["working_fps"]),
                start_sec=float(operation["enable"]["start_sec"]),
            ))
    return LateLabelInputRequirements(tuple(sequences), tuple(png_ids))


def validate_png_bytes(
    data: bytes,
    *,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> tuple[int, int]:
    """Validate closed RGBA PNG bytes, CRCs and bounded decoded scanline framing."""
    if type(data) not in (bytes, bytearray, memoryview):
        raise ValueError("Invalid late-label PNG: bytes required")
    value = bytes(data)
    if expected_size_bytes is not None and len(value) != expected_size_bytes:
        raise ValueError("Invalid late-label PNG: size mismatch")
    if expected_sha256 is not None and hashlib.sha256(value).hexdigest() != expected_sha256:
        raise ValueError("Invalid late-label PNG: sha256 mismatch")
    if not value.startswith(_PNG_SIGNATURE):
        raise ValueError("Invalid late-label PNG: signature mismatch")

    offset = len(_PNG_SIGNATURE)
    dimensions: tuple[int, int] | None = None
    row_bytes = expected_raw = decoded = 0
    decoder = None
    idat_started = False
    idat_ended = False

    def inflate(payload: bytes) -> None:
        nonlocal decoded
        piece = payload
        while piece and decoder is not None:
            try:
                output = decoder.decompress(piece, _PNG_BLOCK)
            except zlib.error as exc:
                raise ValueError("Invalid late-label PNG: compression") from exc
            if output:
                first_filter = (-decoded) % row_bytes
                if any(filter_type > 4
                       for filter_type in output[first_filter::row_bytes]):
                    raise ValueError("Invalid late-label PNG: scanline filter")
                decoded += len(output)
                if decoded > expected_raw:
                    raise ValueError("Invalid late-label PNG: expands beyond dimensions")
            if decoder.unused_data:
                raise ValueError("Invalid late-label PNG: trailing compressed data")
            tail = decoder.unconsumed_tail
            if tail == piece and not output:
                raise ValueError("Invalid late-label PNG: compression did not progress")
            piece = tail

    while offset + 12 <= len(value):
        length = int.from_bytes(value[offset:offset + 4], "big")
        chunk_type = value[offset + 4:offset + 8]
        end = offset + 12 + length
        data_start = offset + 8
        if (length > 2**31 - 1 or len(chunk_type) != 4
                or any(not (65 <= byte <= 90 or 97 <= byte <= 122)
                       for byte in chunk_type)
                or end > len(value)):
            raise ValueError("Invalid late-label PNG: invalid chunk")
        if chunk_type in (b"acTL", b"fcTL", b"fdAT", b"PLTE", b"tRNS"):
            raise ValueError("Invalid late-label PNG: animation or palette chunk")
        if (not chunk_type[0] & 32
                and chunk_type not in (b"IHDR", b"IDAT", b"IEND")):
            raise ValueError("Invalid late-label PNG: unknown critical chunk")
        if chunk_type == b"IHDR":
            if dimensions is not None or offset != len(_PNG_SIGNATURE) or length != 13:
                raise ValueError("Invalid late-label PNG: IHDR is not first or closed")
        elif chunk_type == b"IDAT":
            if dimensions is None or idat_ended:
                raise ValueError("Invalid late-label PNG: noncontiguous IDAT")
        elif idat_started:
            idat_ended = True

        stored_crc = int.from_bytes(value[data_start + length:end], "big")
        crc = zlib.crc32(chunk_type)
        position = data_start
        remaining = length
        while remaining:
            block = value[position:position + min(_PNG_BLOCK, remaining)]
            crc = zlib.crc32(block, crc)
            position += len(block)
            remaining -= len(block)
        if crc != stored_crc:
            raise ValueError("Invalid late-label PNG: chunk CRC")

        if chunk_type == b"IHDR":
            payload = value[data_start:data_start + length]
            width = int.from_bytes(payload[0:4], "big")
            height = int.from_bytes(payload[4:8], "big")
            facts = tuple(payload[8:13])
            if width < 1 or height < 1 or facts != (8, 6, 0, 0, 0):
                raise ValueError("Invalid late-label PNG: unsupported decoded facts")
            row_bytes = width * 4 + 1
            expected_raw = row_bytes * height
            if expected_raw > _PNG_MAX_RAW_BYTES:
                raise ValueError("Invalid late-label PNG: dimensions exceed bounded decode")
            dimensions = (width, height)
        elif chunk_type == b"IDAT":
            if decoder is None:
                decoder = zlib.decompressobj()
                idat_started = True
            position = data_start
            remaining = length
            while remaining:
                block = value[position:position + min(_PNG_BLOCK, remaining)]
                inflate(block)
                position += len(block)
                remaining -= len(block)
        elif chunk_type == b"IEND":
            if dimensions is None or not idat_started:
                raise ValueError("Invalid late-label PNG: missing IDAT")
            if (length != 0 or end != len(value)
                    or decoder is None or not decoder.eof or decoder.unused_data
                    or decoded != expected_raw):
                raise ValueError("Invalid late-label PNG: incomplete IEND")
            width, height = dimensions
            if expected_width is not None and width != expected_width:
                raise ValueError("Invalid late-label PNG: width mismatch")
            if expected_height is not None and height != expected_height:
                raise ValueError("Invalid late-label PNG: height mismatch")
            return dimensions
        offset = end
    raise ValueError("Invalid late-label PNG: missing IEND")


def validate_late_label_dimensions(
    raw: bytes, dimensions: Mapping[str, tuple[int, int]],
) -> None:
    """Validate receiver-read PNG dimensions against closed cue placement rules."""
    _validate_late_label_dimensions(
        validate_render_packet_syntax(raw), dimensions)


def late_label_sequence_path(output_path: str, key: str) -> str:
    """Receiver-private contiguous image2 pattern; sender bytes never supply it."""
    if type(output_path) is not str or not output_path or "\0" in output_path:
        raise ValueError("Invalid final render output path")
    if type(key) is not str or re.fullmatch(r"cue_[0-9]{4}_(?:r|lr)", key) is None:
        raise ValueError("Invalid late-label sequence key")
    return output_path + ".late-label/" + key + "/%04d.png"


def _validate_late_label_dimensions(
    packet: Mapping[str, object], dimensions: Mapping[str, tuple[int, int]],
) -> None:
    operations = _late_label_operations(packet)
    required: set[str] = set()
    for _, operation in operations:
        for key in ("label", "line"):
            item = operation[key]
            if item is not None:
                required.add(item["asset"]["logical_id"])
        for key in ("reveal", "line_reveal"):
            item = operation[key]
            if item is not None:
                required.update(frame["asset"]["logical_id"]
                                for frame in item["frames"])
    if set(dimensions) != required:
        raise ValueError("Invalid late-label PNG dimension inventory")
    canvas = packet["recipe"]["canvas"]
    width = int(canvas["width"])
    height = int(canvas["height"])

    def require(logical_id: str, expected: tuple[int, int] | None = None,
                path: str = "") -> tuple[int, int]:
        if logical_id not in dimensions:
            raise ValueError("Invalid late-label PNG: missing " + path)
        size = dimensions[logical_id]
        if (type(size) is not tuple or len(size) != 2
                or any(type(value) is not int or value < 1 for value in size)):
            raise ValueError("Invalid late-label PNG: invalid " + path)
        if expected is not None and size != expected:
            raise ValueError("Invalid late-label PNG: incompatible " + path)
        return size

    for op_index, operation in operations:
        path = f"late_label_stage[{op_index}]"
        sizes: list[tuple[int, int]] = []
        label = operation["label"]
        if label is not None:
            sizes.append(require(label["asset"]["logical_id"], path=path + ".label"))
        reveal = operation["reveal"]
        if reveal is not None:
            frame_size = None
            for frame in reveal["frames"]:
                logical_id = frame["asset"]["logical_id"]
                frame_size = require(logical_id, frame_size, path + ".reveal")
            if frame_size is not None:
                sizes.append(frame_size)
        if sizes:
            label_width, label_height = sizes[0]
            if (operation["placement"]["x_px"] + label_width > width
                    or operation["placement"]["y_px"] + label_height > height):
                raise ValueError("Invalid late-label PNG: placement exceeds canvas")
        line = operation["line"]
        if line is not None:
            require(line["asset"]["logical_id"], (width, height), path + ".line")
        line_reveal = operation["line_reveal"]
        if line_reveal is not None:
            frame_size: tuple[int, int] | None = None
            for frame in line_reveal["frames"]:
                frame_size = require(frame["asset"]["logical_id"],
                                     (width, height) if frame_size is None else frame_size,
                                     path + ".line_reveal")
        if label is not None and reveal is not None:
            label_size = dimensions[label["asset"]["logical_id"]]
            for frame in reveal["frames"]:
                if dimensions[frame["asset"]["logical_id"]] != label_size:
                    raise ValueError(
                        "Invalid late-label PNG: reveal frames differ from label")


def _build_input_args(media_path: str, mode: Mapping[str, object]) -> tuple[str, ...]:
    if mode["kind"] == "single":
        return ("-i", media_path)
    if mode["kind"] == "loop_stream":
        return ("-stream_loop", "-1", "-i", media_path)
    decoder = mode.get("decoder")
    prefix = ("-c:v", str(decoder)) if decoder else ()
    return (*prefix, "-loop", "1", "-i", media_path)


def _build_overlay_filters(
    input_label: str,
    alloc_label: Callable[[], str],
    layer: Mapping[str, object],
    input_index: int,
    width: int,
    height: int,
    gate_enable: str,
) -> tuple[tuple[str, ...], str]:
    sizing = layer["sizing"]
    scale_expr = ""
    if sizing["mode"] == "width_fraction":
        target = max(2, _js_round(width * min(0.95, sizing["value"])))
        even_width = target - target % 2
        scale_expr = f"scale={even_width}:-2"
    elif sizing["mode"] == "cover":
        scale_expr = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                      f"crop={width}:{height}")
    elif sizing["mode"] == "contain":
        scale_expr = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black@0")

    parts = []
    if scale_expr:
        parts.append(scale_expr)
    chroma = layer["chroma"]
    if chroma is not None:
        color = chroma["color"]
        if color.startswith("#"):
            color = "0x" + color[1:]
        parts.append(
            f"chromakey=color={color}:similarity={_js_number(chroma['similarity'])}:"
            f"blend={_js_number(chroma['blend'])}"
        )
    opacity = layer["opacity"]
    if opacity < 1:
        parts.append(f"format=rgba,colorchannelmixer=aa={_js_number(opacity)}")
    source = alloc_label()
    source_chain = (f"[{input_index}:v]{','.join(parts)}{source}" if parts
                    else f"[{input_index}:v]null{source}")

    placement = layer["placement"]
    x = "0"
    y = "0"
    if placement["mode"] == "anchor":
        margin = _js_round(height * 0.04)
        anchor = placement["value"]
        if anchor == "center":
            x, y = "(W-w)/2", "(H-h)/2"
        elif anchor == "top-third":
            x, y = "(W-w)/2", "(H/3-h/2)"
        elif anchor == "bottom-third":
            x, y = "(W-w)/2", "(2*H/3-h/2)"
        elif anchor == "lower-right":
            x, y = f"W-w-{margin}", f"H-h-{margin}"
        else:
            x, y = str(margin), f"H-h-{margin}"

    enables = []
    if gate_enable:
        match = re.fullmatch(r":enable='([^']+)'", gate_enable)
        if match:
            enables.append(match.group(1))
    time_range = layer["time_range"]
    if time_range is not None:
        if time_range["end_sec"] is not None:
            enables.append(
                f"between(t,{_js_number(time_range['start_sec'])},"
                f"{_js_number(time_range['end_sec'])})"
            )
        elif time_range["start_sec"]:
            enables.append(f"gte(t,{_js_number(time_range['start_sec'])})")
    enable = f":enable='{'*'.join(enables)}'" if enables else ""
    output = alloc_label()
    return ((source_chain, f"{input_label}{source}overlay={x}:{y}{enable}{output}"), output)


def _channel_expression(shadows: float, mids: float, highs: float) -> str | None:
    if abs(shadows) <= 0.0005 and abs(mids) <= 0.0005 and abs(highs) <= 0.0005:
        return None
    lightness = "(2*y/255)"
    terms = []
    if abs(shadows) > 0.0005:
        terms.append(f"{_js_fixed(shadows, 5)}*clip((0.333-{lightness})*4+0.5,0,1)")
    if abs(mids) > 0.0005:
        terms.append(
            f"{_js_fixed(mids, 5)}*clip(({lightness}-0.333)*4+0.5,0,1)*"
            f"clip((1-{lightness}-0.333)*4+0.5,0,1)"
        )
    if abs(highs) > 0.0005:
        terms.append(f"{_js_fixed(highs, 5)}*clip(({lightness}+0.333-1)*4+0.5,0,1)")
    return f"clip(floor(x+255*0.7*({'+'.join(terms)})+0.5),0,255)"


def _build_color_balance(
    amounts: tuple[float, float, float, float, float, float],
    input_label: str,
    output_label: str,
    alloc_label: Callable[[], str],
    gate_enable: str,
) -> tuple[str, ...] | None:
    red = _channel_expression(amounts[0], amounts[3], 0)
    green = _channel_expression(amounts[1], amounts[4], 0)
    blue = _channel_expression(amounts[2], amounts[5], 0)
    if not red and not green and not blue:
        return None
    main, side, grey = alloc_label(), alloc_label(), alloc_label()
    options = ":".join(value for value in (
        f"c0='{green}'" if green else "",
        f"c1='{blue}'" if blue else "",
        f"c2='{red}'" if red else "",
    ) if value)
    return (
        f"{input_label}format=gbrp,split=2{main}{side}",
        f"{side}format=gray,format=gbrp{grey}",
        f"{main}{grey}lut2={options}{gate_enable},format=yuv420p{output_label}",
    )


def _build_color_grade(
    operation: Mapping[str, object],
    gate_enable: str,
    input_label: str,
    output_label: str,
    alloc_label: Callable[[], str],
) -> tuple[str, ...]:
    base = _PRESETS[operation["preset"]]
    brightness = base[0] + (operation["brightness"] or 0)
    contrast = base[1] * operation["contrast"]
    saturation = base[2] * operation["saturation"]
    warmth = base[3] + (operation["warmth"] or 0)
    tint = base[4] + (operation["tint"] or 0)
    eq_parts = []
    if abs(brightness) > 0.001:
        eq_parts.append(f"brightness={_js_fixed(brightness, 3)}")
    if abs(contrast - 1) > 0.001:
        eq_parts.append(f"contrast={_js_fixed(contrast, 3)}")
    if abs(saturation - 1) > 0.001:
        eq_parts.append(f"saturation={_js_fixed(saturation, 3)}")
    after_eq = alloc_label() if eq_parts else None
    balance = None
    if abs(warmth) > 0.001 or abs(tint) > 0.001:
        balance = _build_color_balance(
            (warmth * 0.3, tint * -0.2, warmth * -0.3,
             warmth * 0.15, tint * -0.1, warmth * -0.15),
            after_eq or input_label, output_label, alloc_label, gate_enable,
        )
    eq = ((f"{input_label}eq={':'.join(eq_parts)}{gate_enable}"
           f"{after_eq if balance else output_label}"),) if eq_parts else ()
    return (*eq, *(balance or ()))


def _build_archive_filters(
    input_label: str,
    output_label: str,
    labels: tuple[str, str, str, str, str, str],
    width: int,
    height: int,
    strength: int,
    gate_enable: str,
) -> tuple[str, ...]:
    grade, noise, marked, base, weave, shifted = labels
    amount = max(1, min(100, _js_round(strength or 45))) / 100
    grain = _js_round(6 + amount * 16)
    contrast = _js_fixed(1.02 + amount * 0.12, 3)
    saturation = _js_fixed(max(0.18, 0.72 - amount * 0.26), 3)
    flicker = _js_fixed(0.006 + amount * 0.018, 3)
    crop = max(2, _js_round(4 + amount * 12) * 2)
    shift = max(1, _js_round(1 + amount * 3))
    scratch_opacity = _js_fixed(0.05 + amount * 0.13, 2)
    dust_opacity = _js_fixed(0.08 + amount * 0.15, 2)
    scratch_x = _js_round(width * 0.13)
    scratch_x2 = _js_round(width * 0.77)
    dust_x = _js_round(width * 0.58)
    dust_y = _js_round(height * 0.31)
    half_crop = _js_number(crop / 2)
    return (
        f"{input_label}eq=brightness='{flicker}*sin(2*PI*t*8)':contrast={contrast}:"
        f"saturation={saturation}:eval=frame{gate_enable}{grade}",
        f"{grade}noise=alls={grain}:allf=t+u{gate_enable}{noise}",
        f"{noise}drawbox=x={scratch_x}:y=0:w=1:h=ih:color=white@{scratch_opacity}:t=fill{gate_enable},"
        f"drawbox=x={scratch_x2}:y=0:w=1:h=ih:color=black@{scratch_opacity}:t=fill{gate_enable},"
        f"drawbox=x={dust_x}:y={dust_y}:w=2:h=2:color=white@{dust_opacity}:t=fill{gate_enable}{marked}",
        f"{marked}split=2{base}{weave}",
        f"{weave}crop=w=iw-{crop}:h=ih-{crop}:x='{half_crop}+{shift}*sin(2*PI*t*0.73)':"
        f"y='{half_crop}+{shift}*cos(2*PI*t*0.53)',scale={width}:{height}:"
        f"flags=bilinear,setsar=1{shifted}",
        f"{base}{shifted}overlay=0:0{gate_enable}{output_label}",
    )


def _even_down(value: float) -> int:
    return max(0, math.floor(value / 2) * 2)


def _letterbox_height(width: int, height: int, ratio: str) -> int:
    picture_height = _even_down(width / _RATIOS.get(ratio, 2.35))
    return _even_down((height - picture_height) / 2)


def _build_letterbox(
    input_label: str, output_label: str, width: int, height: int,
    ratio: str, color: str, mode: str,
) -> str | None:
    bar = _letterbox_height(width, height, ratio)
    if bar <= 0:
        return None
    if mode == "cover":
        return (f"{input_label}drawbox=x=0:y=0:w=iw:h={bar}:color={color}:t=fill,"
                f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color={color}:t=fill{output_label}")
    band = height - bar * 2
    fit_width = _even_down(width * band / height)
    return (f"{input_label}scale={fit_width}:{band},pad={width}:{height}:"
            f"(ow-iw)/2:(oh-ih)/2:color={color},setsar=1{output_label}")


def _clamp(value: object, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed)) if math.isfinite(parsed) else fallback


def _build_frame_filters(
    input_label: str,
    background_label: str | None,
    output_label: str,
    prefix: str,
    width: int,
    height: int,
    fps: int,
    config: Mapping[str, object],
) -> tuple[str, ...]:
    inset_pct = _clamp(config["inset_pct"], 0, 25, 4)
    scale = height / 1080
    border_px = _js_round(_clamp(config["border_width"], 0, 160, 12) * scale)
    inset_px = _js_round(min(width, height) * inset_pct / 100)
    outer_x = outer_y = inset_px
    outer_width = max(16, width - outer_x * 2)
    outer_height = max(16, height - outer_y * 2)
    inner_x = min(math.floor(width / 2) - 4, outer_x + border_px)
    inner_y = min(math.floor(height / 2) - 4, outer_y + border_px)
    inner_width = max(8, width - inner_x * 2)
    inner_height = max(8, height - inner_y * 2)
    radius = _js_round(min(
        _clamp(config["corner_radius"], 0, 300, 28) * scale,
        inner_width / 2,
        inner_height / 2,
    ))
    background = str(config["background_color"])
    border = str(config["border_color"])
    fit = "decrease" if config["fit"] == "contain" else "increase"
    fit_tail = (f",crop={inner_width}:{inner_height}" if fit == "increase" else
                f",pad={inner_width}:{inner_height}:(ow-iw)/2:(oh-ih)/2:color={background}")
    media, bg, border_layer = f"{prefix}media", f"{prefix}bg", f"{prefix}border"
    mask, rgb = f"{prefix}mask", f"{prefix}rgb"
    square = radius * radius
    corners = (
        f"if(lt(X,{radius})*lt(Y,{radius}),if(lte(pow(X-{radius},2)+pow(Y-{radius},2),{square}),255,0),"
        f"if(gte(X,W-{radius})*lt(Y,{radius}),if(lte(pow(X-(W-{radius}),2)+pow(Y-{radius},2),{square}),255,0),"
        f"if(lt(X,{radius})*gte(Y,H-{radius}),if(lte(pow(X-{radius},2)+pow(Y-(H-{radius}),2),{square}),255,0),"
        f"if(gte(X,W-{radius})*gte(Y,H-{radius}),if(lte(pow(X-(W-{radius}),2)+pow(Y-(H-{radius}),2),{square}),255,0),255))))"
    )
    if radius > 0:
        media_chain = (
            f"{input_label}scale={inner_width}:{inner_height}:force_original_aspect_ratio={fit}{fit_tail},format=rgba[{rgb}]",
            f"color=c=white:s={inner_width}x{inner_height}:r={max(1, _js_round(fps))},"
            f"trim=end_frame=1,format=gray,geq=lum='{corners}',loop=loop=-1:size=1[{mask}]",
            f"[{rgb}][{mask}]alphamerge=shortest=1[{media}]",
        )
    else:
        media_chain = (
            f"{input_label}scale={inner_width}:{inner_height}:force_original_aspect_ratio={fit}{fit_tail},format=rgba[{media}]",
        )
    background_chain = (
        f"{background_label}setpts=PTS-STARTPTS,scale={width}:{height}:"
        f"force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[{bg}]"
        if background_label else f"color=c={background}:s={width}x{height}:r={max(1, _js_round(fps))}[{bg}]"
    )
    return (
        *media_chain,
        background_chain,
        f"[{bg}]drawbox=x={outer_x}:y={outer_y}:w={outer_width}:h={outer_height}:color={border}:t=fill[{border_layer}]",
        f"[{border_layer}][{media}]overlay={inner_x}:{inner_y}:shortest=1,format=yuv420p{output_label}",
    )


def _anchor_position(anchor: str, margin: int) -> tuple[str, str]:
    if anchor == "center":
        vertical, horizontal = "center", "center"
    else:
        parts = anchor.split("-")
        vertical, horizontal = parts if len(parts) == 2 else ("bottom", "right")
    x = str(margin) if horizontal == "left" else f"W-w-{margin}" if horizontal == "right" else "(W-w)/2"
    y = str(margin) if vertical == "top" else f"H-h-{margin}" if vertical == "bottom" else "(H-h)/2"
    return x, y


def _build_logo_filters(
    input_label: str,
    alloc_label: Callable[[], str],
    logo: Mapping[str, object],
    input_index: int,
    width: int,
    height: int,
    gate_enable: str,
) -> tuple[tuple[str, ...], str]:
    del height
    logo_width = _js_round(width * (logo.get("scale") or 0.1))
    margin = int(logo.get("margin") or 20)
    x, y = _anchor_position(str(logo["position"]), margin)
    opacity = logo.get("opacity")
    if opacity is None:
        opacity = 1
    output = alloc_label()
    filters = [f"[{input_index}:v]scale={logo_width}:-1,format=rgba,colorchannelmixer=aa={_js_number(opacity)}[logo]"]
    logo_enable = gate_enable
    if not logo_enable and logo.get("hide_during_intro"):
        logo_enable = ":enable='gt(t,10)'"
    filters.append(f"{input_label}[logo]overlay={x}:{y}{logo_enable}{output}")
    return tuple(filters), output


def _late_overlay_statement(
    video_label: str,
    source_label: str,
    output_label: str,
    operation: Mapping[str, object],
    *,
    x: str,
    y: str,
    start_sec: float,
    end_sec: float,
    motion: str,
) -> str:
    scale = None
    eval_frame = False
    if motion == "pop":
        scale = (f"if(lt(t,{_js_fixed(start_sec + .3, 3)}),"
                 f"0.8+0.25*sin((t-{_js_fixed(start_sec, 3)})/0.3*3.14159),1)")
        eval_frame = True
    elif motion == "pulse":
        scale = f"1+0.05*sin((t-{_js_fixed(start_sec, 3)})*6.28)"
        eval_frame = True
    elif motion == "slide":
        x = (f"{operation['placement']['x_px']}-w*(1-(t-"
             f"{_js_fixed(start_sec, 3)})/0.3)")
    elif motion == "float":
        y = (f"{operation['placement']['y_px']}+"
             f"sin((t-{_js_fixed(start_sec, 3)})*2)*3")
    options = [f"overlay=x={x}:y={y}:eof_action=repeat"]
    if scale is not None:
        options.append(f"scale='{scale}'")
    if eval_frame:
        options.append("eval=frame")
    options.append(_late_gate_enable(start_sec, end_sec).lstrip(":"))
    return f"{video_label}{source_label}{':'.join(options)}{output_label}"


def _build_late_label_filters(
    video_label: str,
    alloc_label: Callable[[], str],
    operation: Mapping[str, object],
    input_indices: Mapping[str, int],
    sequence_indices: Mapping[str, int],
    op_index: int,
    fps: int,
) -> tuple[tuple[str, ...], str]:
    del fps
    start = float(operation["enable"]["start_sec"])
    end = float(operation["enable"]["end_sec"])
    motion = str(operation["motion"]["kind"])
    x = str(operation["placement"]["x_px"])
    y = str(operation["placement"]["y_px"])
    filters: list[str] = []
    current = video_label

    line = operation["line"]
    line_reveal = operation["line_reveal"]
    if line_reveal is not None:
        source = alloc_label()
        index = sequence_indices[f"cue_{op_index:04d}_lr"]
        filters.append(
            f"[{index}:v]setpts=PTS+{_js_fixed(start, 3)}/TB{source}")
        output = alloc_label()
        filters.append(_late_overlay_statement(
            current, source, output, operation, x="0", y="0",
            start_sec=start, end_sec=end, motion=motion,
        ))
        current = output
    elif line is not None:
        source = alloc_label()
        index = input_indices[line["asset"]["logical_id"]]
        filters.append(f"[{index}:v]format=rgba{source}")
        output = alloc_label()
        filters.append(_late_overlay_statement(
            current, source, output, operation, x="0", y="0",
            start_sec=start, end_sec=end, motion=motion,
        ))
        current = output

    reveal = operation["reveal"]
    label = operation["label"]
    if reveal is not None:
        source = alloc_label()
        index = sequence_indices[f"cue_{op_index:04d}_r"]
        filters.append(
            f"[{index}:v]setpts=PTS+{_js_fixed(start, 3)}/TB{source}")
        output = alloc_label()
        filters.append(_late_overlay_statement(
            current, source, output, operation, x=x, y=y,
            start_sec=start, end_sec=end, motion=motion,
        ))
        current = output
    elif label is not None:
        source = alloc_label()
        index = input_indices[label["asset"]["logical_id"]]
        filters.append(f"[{index}:v]format=rgba{source}")
        output = alloc_label()
        filters.append(_late_overlay_statement(
            current, source, output, operation, x=x, y=y,
            start_sec=start, end_sec=end, motion=motion,
        ))
        current = output
    return tuple(filters), current


def _describe_burn_ins(operations: list[dict]) -> tuple[str, ...]:
    kinds = {operation["kind"] for operation in operations}
    result = []
    if "classic_color_grade" in kinds:
        result.append("color grading")
    if "classic_vignette" in kinds:
        result.append("vignette")
    if "classic_film_grain" in kinds:
        result.append("film grain")
    if "aged_archive_film" in kinds:
        result.append("aged archive film")
    if "classic_letterbox" in kinds:
        result.append("letterbox")
    if "presentation_frame" in kinds:
        result.append("frame")
    overlays = [operation for operation in operations if operation["kind"] == "overlay_layer"]
    if overlays:
        suffix = " incl. chroma key" if any(item["chroma"] is not None for item in overlays) else ""
        result.append(f"{len(overlays)} overlay layer(s){suffix}")
    if "logo" in kinds:
        result.append("logo")
    return tuple(result)


def _bitrate_cap(value: object) -> int:
    if value is None:
        return 0
    number = float(value)
    return min(100000, _js_round(number)) if math.isfinite(number) and number >= 500 else 0


def _videotoolbox_quality(crf: int) -> int:
    return max(25, min(90, _js_round(80 - (crf - 18) * 3)))


def _encoder_args(codec: str, container: str, crf: int, effort: str,
                  bitrate: object) -> tuple[str, ...]:
    cap = _bitrate_cap(bitrate)
    if container == "webm":
        return ("-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0",
                "-c:a", "libopus")
    if effort == "hardware":
        rate = (("-b:v", f"{cap}k", "-maxrate", f"{_js_round(cap * 1.5)}k",
                 "-bufsize", f"{cap * 2}k") if cap else
                ("-q:v", str(_videotoolbox_quality(crf))))
        return ("-c:v", "h264_videotoolbox", *rate, *_FINAL_AAC_ARGS,
                "-pix_fmt", "yuv420p", "-movflags", "+faststart")
    cap_args = (("-maxrate", f"{cap}k", "-bufsize", f"{cap * 2}k") if cap else ())
    if codec == "h265":
        return ("-c:v", "libx265", "-crf", str(crf), "-preset", effort,
                *cap_args, "-tag:v", "hvc1", *_FINAL_AAC_ARGS,
                "-pix_fmt", "yuv420p", "-movflags", "+faststart")
    return ("-c:v", "libx264", "-crf", str(crf), "-preset", effort,
            *cap_args, *_FINAL_AAC_ARGS, "-pix_fmt", "yuv420p",
            "-movflags", "+faststart")


def _build_tiers(
    encode: Mapping[str, object], pre_encoder: tuple[str, ...], video_path: str,
    audio_path: str, duration: int | float, output_path: str,
) -> tuple[EncodeTier, ...]:
    container = encode["container"]
    requested_codec = encode["requested_codec"]
    effort = encode["requested_effort"]
    preset = encode["quality_cpu_preset"]
    crf = int(encode["crf"])
    crf_text = _js_number(crf)
    bitrate = encode["bitrate_kbps"]
    effective_codec = "h264" if container == "webm" or requested_codec != "h265" else "h265"

    def full(codec: str, effort_override: str | None = None) -> tuple[str, ...]:
        return (*pre_encoder,
                *_encoder_args(codec, container, crf, effort_override or effort, bitrate),
                "-t", _js_number(duration), output_path)

    def label(codec: str) -> str:
        if container == "webm":
            return f"VP9 · CRF {crf_text}"
        cap = _bitrate_cap(bitrate)
        if effort == "hardware":
            detail = (f"{cap} kbit/s" if cap else
                      f"q:v {_videotoolbox_quality(crf)}")
            return f"H.264 · hardware (videotoolbox, {detail})"
        codec_name = "HEVC (H.265)" if codec == "h265" else "H.264"
        return (f"{codec_name} · preset {effort} · capped at {cap} kbit/s"
                if cap else f"{codec_name} · preset {effort}")

    minimal = (
        "-y", "-i", video_path, "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        *_encoder_args("h264", container, crf, effort, bitrate),
        "-t", _js_number(duration), output_path,
    )
    return (
        EncodeTier(label(effective_codec), full(effective_codec), "always"),
        EncodeTier(
            f"H.264 · libx264 {preset} (hardware fallback, full effects)",
            full("h264", preset), "always" if effort == "hardware" else "never",
            "(retry, libx264 software)",
            "hardware encoder unavailable — re-encoded on CPU (same effects, slower)",
        ),
        EncodeTier(
            label("h264") + " (fallback)", full("h264"),
            "libx265_einval" if effective_codec == "h265" else "never",
            "(retry, H.264)", "codec downgraded to H.264",
        ),
        EncodeTier(
            "H.264 · no effects (recovery)", minimal, "always",
            "(recovery, H.264 minimal)",
            "all post effects removed (vignette/grain/grade/chroma/subtitles/letterbox/logo)",
            True,
        ),
    )


def _checked_assets(packet: Mapping[str, object], local_assets: Mapping[str, str],
                    output_path: str) -> dict[str, str]:
    if type(output_path) is not str or not output_path or "\0" in output_path:
        raise ValueError("Invalid final render output path")
    try:
        assets = dict(local_assets)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid final render asset bindings") from exc
    ids = {entry["logical_id"] for entry in packet["manifest"]}
    if set(assets) != ids:
        raise ValueError("Final render assets must exactly match the manifest")
    for logical_id, value in assets.items():
        if type(logical_id) is not str or type(value) is not str or not value or "\0" in value:
            raise ValueError("Invalid local asset binding: " + str(logical_id))
    return assets


def _compile_validated_packet(
    packet: Mapping[str, object], local_assets: Mapping[str, str], output_path: str,
    *, late_labels_allowed: bool = False,
) -> FinalRenderPlan:
    late_operations = _late_label_operations(packet)
    if late_operations and late_labels_allowed is not True:
        raise UnsupportedFinalRenderCombination("late_label_profile_not_allowed")
    assets = _checked_assets(packet, local_assets, output_path)
    recipe = packet["recipe"]
    operations = recipe["operations"]
    width = int(recipe["canvas"]["width"])
    height = int(recipe["canvas"]["height"])
    fps = int(recipe["canvas"]["working_fps"])
    gate = recipe["effects_gate"]
    has_gate = gate["start_sec"] is not None or gate["end_sec"] is not None
    logo = next((item for item in operations if item["kind"] == "logo"), None)
    letterbox = next((item for item in operations if item["kind"] == "classic_letterbox"), None)
    frame = next((item for item in operations if item["kind"] == "presentation_frame"), None)
    if logo is not None and logo["margin_px"] == 0:
        raise UnsupportedFinalRenderCombination("logo_margin_default")
    if logo is not None and has_gate and logo["gate"] != "effects_gate":
        raise UnsupportedFinalRenderCombination("logo_gate_precedence")
    if letterbox is not None:
        bar = _letterbox_height(width, height, letterbox["ratio"])
        if bar <= 0:
            raise UnsupportedFinalRenderCombination("letterbox_noop")
        if bar * 2 > height * 0.5:
            raise UnsupportedFinalRenderCombination("letterbox_skip")

    argv = ["-y"]
    input_uses = []
    generated_inputs = []

    def add_input(reference: Mapping[str, str], mode: Mapping[str, object]) -> int:
        index = len(input_uses) + len(generated_inputs)
        logical_id = reference["logical_id"]
        argv.extend(_build_input_args(assets[logical_id], mode))
        input_uses.append((logical_id, index))
        return index

    def add_generated_input(key: str, fps_value: int) -> int:
        index = len(input_uses) + len(generated_inputs)
        pattern = late_label_sequence_path(output_path, key)
        argv.extend(("-framerate", str(fps_value), "-f", "image2",
                     "-start_number", "0", "-i", pattern))
        generated_inputs.append((key, index))
        return index

    add_input(recipe["base_video"], {"kind": "single"})
    audio_index = add_input(recipe["premixed_audio"], {"kind": "single"})
    logo_index = add_input(logo["asset"], {"kind": "single"}) if logo is not None else -1
    overlay_indices = []
    for operation in operations:
        if operation["kind"] == "overlay_layer":
            overlay_indices.append(add_input(operation["asset"], operation["input_mode"]))
    frame_index = -1
    if frame is not None and frame["background"]["kind"] == "asset":
        frame_index = add_input(frame["background"]["asset"],
                                frame["background"]["input_mode"])

    cue_indices: dict[str, int] = {}
    sequence_indices: dict[str, int] = {}
    for op_index, operation in late_operations:
        for key in ("label", "line"):
            item = operation[key]
            if item is not None:
                logical_id = item["asset"]["logical_id"]
                if logical_id not in cue_indices:
                    cue_indices[logical_id] = add_input(
                        item["asset"], {"kind": "loop_still", "decoder": "png"})
        for purpose, key in (("r", "reveal"), ("lr", "line_reveal")):
            sequence = operation[key]
            if sequence is not None:
                stage_key = f"cue_{op_index:04d}_{purpose}"
                sequence_indices[stage_key] = add_generated_input(stage_key, fps)

    filters = []
    next_label = 0

    def alloc_label() -> str:
        nonlocal next_label
        value = f"[v{next_label}]"
        next_label += 1
        return value

    video_label = "[0:v]"
    reset = alloc_label()
    filters.append(f"{video_label}setpts=PTS-STARTPTS{reset}")
    video_label = reset
    gate_enable = _build_gate_enable(gate["start_sec"] or 0, gate["end_sec"] or 0)
    overlay_cursor = 0
    for operation in operations:
        kind = operation["kind"]
        if kind == "overlay_layer":
            parts, video_label = _build_overlay_filters(
                video_label, alloc_label, operation,
                overlay_indices[overlay_cursor], width, height, gate_enable,
            )
            overlay_cursor += 1
            filters.extend(parts)
        elif kind == "classic_color_grade":
            output = alloc_label()
            statements = _build_color_grade(
                operation, gate_enable, video_label, output, alloc_label,
            )
            if statements:
                filters.extend(statements)
                video_label = output
        elif kind == "classic_vignette":
            angle = 0.45 + operation["strength"] * 0.8
            output = alloc_label()
            filters.append(
                f"{video_label}vignette=angle={_js_fixed(angle, 4)}:mode=forward:"
                f"eval=init{gate_enable}{output}"
            )
            video_label = output
        elif kind == "classic_film_grain":
            output = alloc_label()
            filters.append(
                f"{video_label}noise=alls={_js_round(operation['strength'])}:"
                f"allf=t+u{gate_enable}{output}"
            )
            video_label = output
        elif kind == "aged_archive_film":
            labels = tuple(alloc_label() for _ in range(6))
            output = alloc_label()
            filters.extend(_build_archive_filters(
                video_label, output, labels, width, height,
                operation["strength"], gate_enable,
            ))
            video_label = output
        elif kind == "classic_letterbox":
            output = alloc_label()
            statement = _build_letterbox(
                video_label, output, width, height, operation["ratio"],
                operation["color"], operation["mode"],
            )
            if statement is None:
                raise UnsupportedFinalRenderCombination("letterbox_noop")
            filters.append(statement)
            video_label = output
        elif kind == "presentation_frame":
            output = alloc_label()
            prefix = f"mainframe{next_label}"
            filters.extend(_build_frame_filters(
                video_label, f"[{frame_index}:v]" if frame_index >= 0 else None,
                output, prefix, width, height,
                fps, operation,
            ))
            video_label = output
        elif kind == "late_label_stage":
            op_index = operations.index(operation)
            parts, video_label = _build_late_label_filters(
                video_label, alloc_label, operation, cue_indices,
                sequence_indices, op_index, fps,
            )
            filters.extend(parts)
        else:
            logo_config = {
                "position": operation["anchor"], "opacity": operation["opacity"],
                "scale": operation["scale"], "margin": operation["margin_px"],
                "hide_during_intro": operation["gate"] == "after_10_seconds",
            }
            logo_gate = gate_enable if operation["gate"] == "effects_gate" else ""
            parts, video_label = _build_logo_filters(
                video_label, alloc_label, logo_config, logo_index,
                width, height, logo_gate,
            )
            filters.extend(parts)

    filters.append(f"{video_label}null[vout]")
    graph = ";\n".join(filters)
    argv.extend(("-filter_complex", graph, "-map", "[vout]", "-map", f"{audio_index}:a"))
    tiers = _build_tiers(
        recipe["encode"], tuple(argv), assets[recipe["base_video"]["logical_id"]],
        assets[recipe["premixed_audio"]["logical_id"]],
        recipe["target_duration_sec"], output_path,
    )
    return FinalRenderPlan(
        tiers, _describe_burn_ins(operations), tuple(input_uses),
        tuple(generated_inputs),
    )


def compile_final_render_packet(
    raw: bytes, local_assets: Mapping[str, str], output_path: str,
    *, late_labels_allowed: bool = False,
) -> FinalRenderPlan:
    packet = validate_render_packet_syntax(raw)
    return _compile_validated_packet(
        packet, local_assets, output_path,
        late_labels_allowed=late_labels_allowed,
    )
