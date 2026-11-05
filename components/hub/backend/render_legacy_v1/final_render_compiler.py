"""Pure deterministic final-pass construction for a closed render packet."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Literal

from .render_contract import validate_render_packet_syntax

TierCondition = Literal["always", "never", "libx265_einval"]
_FINAL_AAC_ARGS = ("-c:a", "aac", "-b:a", "384k")
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
) -> FinalRenderPlan:
    assets = _checked_assets(packet, local_assets, output_path)
    recipe = packet["recipe"]
    operations = recipe["operations"]
    width = int(recipe["canvas"]["width"])
    height = int(recipe["canvas"]["height"])
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

    def add_input(reference: Mapping[str, str], mode: Mapping[str, object]) -> int:
        index = len(input_uses)
        logical_id = reference["logical_id"]
        argv.extend(_build_input_args(assets[logical_id], mode))
        input_uses.append((logical_id, index))
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
                int(recipe["canvas"]["working_fps"]), operation,
            ))
            video_label = output
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
    return FinalRenderPlan(tiers, _describe_burn_ins(operations), tuple(input_uses))


def compile_final_render_packet(
    raw: bytes, local_assets: Mapping[str, str], output_path: str,
) -> FinalRenderPlan:
    packet = validate_render_packet_syntax(raw)
    return _compile_validated_packet(packet, local_assets, output_path)
