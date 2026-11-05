"""Closed render-packet syntax and R/M/P validation; never execution authority."""

from __future__ import annotations

import math
import re

from .render_hash_v1 import decode_render_json, render_hash

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SAFE_NAME = re.compile(r"([a-z][a-z0-9_-]{0,63})\.(mp4|mov|webm|wav|png|jpg|webp|gif|bmp|tiff)\Z")
_COLOR = re.compile(r"(?:#[0-9A-F]{6}|black)\Z")
_HEX_COLOR = re.compile(r"#[0-9A-F]{6}\Z")
_MAX_SAFE = 9007199254740991
_FPS = frozenset((5, 24, 25, 30, 60))
_DECODERS = frozenset(("png", "mjpeg", "webp", "gif", "bmp", "tiff"))
_LATE_LABEL_MOTIONS = frozenset(("none", "pop", "slide", "float", "pulse", "wipe", "line-draw"))
_LATE_LABEL_CONTRACT = "studio.render.late-label.v3"
_STAGES = (
    "overlay_layer",
    "classic_color_grade",
    "classic_vignette",
    "classic_film_grain",
    "aged_archive_film",
    "classic_letterbox",
    "presentation_frame",
    "late_label_stage",
    "logo",
)
_ROLES = frozenset(("base_video", "premixed_audio", "overlay", "frame_background",
                    "logo", "cue_label"))


def _invalid(reason):
    raise ValueError("Invalid render packet: " + reason)


def _object(value, path):
    if type(value) is not dict:
        _invalid(path + " must be an object")
    return value


def _exact(value, keys, path):
    result = _object(value, path)
    if set(result) != set(keys) or len(result) != len(keys):
        _invalid(path + " has missing or unknown fields")
    return result


def _text(value, path):
    if type(value) is not str or "\0" in value:
        _invalid(path + " must be NUL-free text")
    return value


def _one_of(value, allowed, path):
    result = _text(value, path)
    if result not in allowed:
        _invalid(path + " is unsupported")
    return result


def _number(value, path):
    if type(value) not in (int, float) or not math.isfinite(value):
        _invalid(path + " must be finite")
    return value


def _integer(value, path):
    result = _number(value, path)
    if (type(result) is float and not result.is_integer()) or abs(result) > _MAX_SAFE:
        _invalid(path + " must be a safe integer")
    return int(result)


def _range(value, minimum, maximum, path):
    result = _number(value, path)
    if result < minimum or result > maximum:
        _invalid(path + " is out of range")
    return result


def _integer_range(value, minimum, maximum, path):
    result = _integer(value, path)
    if result < minimum or result > maximum:
        _invalid(path + " is out of range")
    return result


def _asset_ref(value, path):
    ref = _exact(value, ("type", "logical_id"), path)
    if ref["type"] != "asset_ref":
        _invalid(path + ".type must be asset_ref")
    logical_id = _text(ref["logical_id"], path + ".logical_id")
    if _ID.fullmatch(logical_id) is None:
        _invalid(path + ".logical_id is invalid")
    return logical_id


def _input_mode(value, path):
    mode = _object(value, path)
    if mode.get("kind") in ("single", "loop_stream"):
        _exact(mode, ("kind",), path)
    elif mode.get("kind") == "loop_still":
        closed = _exact(mode, ("kind", "decoder"), path)
        _one_of(closed["decoder"], _DECODERS, path + ".decoder")
    else:
        _invalid(path + ".kind is unsupported")


def _effects_gate(value, duration, path):
    gate = _exact(value, ("start_sec", "end_sec"), path)
    for key in ("start_sec", "end_sec"):
        if gate[key] is not None:
            point = _number(gate[key], path + "." + key)
            if point <= 0 or point >= duration:
                _invalid(path + "." + key + " must be interior")
    if gate["start_sec"] is not None and gate["end_sec"] is not None:
        if gate["start_sec"] >= gate["end_sec"]:
            _invalid(path + " must be ordered")


def _time_range(value, duration, path):
    if value is None:
        return
    interval = _exact(value, ("start_sec", "end_sec"), path)
    start = _number(interval["start_sec"], path + ".start_sec")
    if start < 0 or start >= duration:
        _invalid(path + ".start_sec is out of range")
    if interval["end_sec"] is not None:
        end = _number(interval["end_sec"], path + ".end_sec")
        if end <= start or end > duration:
            _invalid(path + ".end_sec is out of range")


def _add_ref(refs, logical_id, role):
    existing = refs.get(logical_id)
    if existing is not None and existing != role:
        _invalid("asset " + logical_id + " has incompatible roles")
    refs[logical_id] = role


def _late_label_asset(value, expected, refs, path):
    logical_id = _asset_ref(value, path)
    if logical_id != expected:
        _invalid(path + " does not use its ordinal asset name")
    _add_ref(refs, logical_id, "cue_label")


def _late_label_sequence(operation, key, path, fps, refs, op_index, purpose):
    reveal = operation[key]
    if reveal is None:
        return None
    sequence = _exact(reveal, ("duration_sec", "frames"), path)
    if sequence["duration_sec"] != .3:
        _invalid(path + ".duration_sec is unsupported")
    frames = sequence["frames"]
    if type(frames) is not list:
        _invalid(path + ".frames must be an array")
    expected = max(2, math.ceil(fps * .3) + 1)
    if len(frames) != expected:
        _invalid(path + ".frames has wrong count")
    for index, raw in enumerate(frames):
        frame_path = path + ".frames[" + str(index) + "]"
        item = _exact(raw, ("asset", "progress"), frame_path)
        _late_label_asset(
            item["asset"],
            f"cue_{op_index:04d}_{purpose}{index:04d}",
            refs, frame_path + ".asset",
        )
        progress = _number(item["progress"], frame_path + ".progress")
        if progress != index / (expected - 1):
            _invalid(frame_path + ".progress is unsupported")


def _late_label_operation(operation, duration, fps, refs, path, index):
    item = _exact(operation, ("kind", "contract", "cue_id", "enable", "placement",
                              "motion", "label", "reveal", "line", "line_reveal"), path)
    if item["contract"] != _LATE_LABEL_CONTRACT or type(item["contract"]) is not str:
        _invalid(path + ".contract is unsupported")
    if _ID.fullmatch(_text(item["cue_id"], path + ".cue_id")) is None:
        _invalid(path + ".cue_id is invalid")
    if index > 9999:
        _invalid(path + " exceeds supported ordinal assets")

    gate = _exact(item["enable"], ("start_sec", "end_sec"), path + ".enable")
    start = _number(gate["start_sec"], path + ".enable.start_sec")
    end = _number(gate["end_sec"], path + ".enable.end_sec")
    if start < 0 or start >= end or end > duration:
        _invalid(path + ".enable is out of range")

    placement = _exact(item["placement"], ("x_px", "y_px"), path + ".placement")
    _integer_range(placement["x_px"], 0, _MAX_SAFE, path + ".placement.x_px")
    _integer_range(placement["y_px"], 0, _MAX_SAFE, path + ".placement.y_px")

    motion = _exact(item["motion"], ("kind", "entrance_sec"), path + ".motion")
    kind = _one_of(motion["kind"], _LATE_LABEL_MOTIONS, path + ".motion.kind")
    if motion["entrance_sec"] != .3:
        _invalid(path + ".motion.entrance_sec is unsupported")

    label = item["label"]
    if label is None:
        if kind != "wipe":
            _invalid(path + ".label is required")
    else:
        if kind == "wipe":
            _invalid(path + ".label is unsupported for motion")
        closed = _exact(label, ("asset",), path + ".label")
        _late_label_asset(closed["asset"], f"cue_{index:04d}_label", refs,
                          path + ".label.asset")

    _late_label_sequence(item, "reveal", path + ".reveal", fps, refs, index, "r")
    if (kind == "wipe") != (item["reveal"] is not None):
        _invalid(path + ".reveal does not match motion")

    line = item["line"]
    if line is not None:
        closed = _exact(line, ("asset",), path + ".line")
        _late_label_asset(closed["asset"], f"cue_{index:04d}_line", refs,
                          path + ".line.asset")

    _late_label_sequence(item, "line_reveal", path + ".line_reveal", fps, refs,
                         index, "lr")
    if (kind == "line-draw") != (item["line_reveal"] is not None):
        _invalid(path + ".line_reveal does not match motion")
    if line is not None and item["line_reveal"] is not None:
        _invalid(path + " cannot contain both line and line_reveal")


def _operation(value, duration, fps, refs, index):
    path = "recipe.operations[" + str(index) + "]"
    operation = _object(value, path)
    kind = _text(operation.get("kind"), path + ".kind")
    try:
        stage = _STAGES.index(kind)
    except ValueError:
        _invalid(path + ".kind is unsupported")

    if kind == "overlay_layer":
        item = _exact(operation, ("kind", "asset", "input_mode", "opacity", "sizing",
                                  "placement", "time_range", "chroma"), path)
        _add_ref(refs, _asset_ref(item["asset"], path + ".asset"), "overlay")
        _input_mode(item["input_mode"], path + ".input_mode")
        _range(item["opacity"], 0, 1, path + ".opacity")
        sizing = _object(item["sizing"], path + ".sizing")
        if sizing.get("mode") in ("cover", "contain", "native"):
            _exact(sizing, ("mode",), path + ".sizing")
        elif sizing.get("mode") == "width_fraction":
            closed = _exact(sizing, ("mode", "value"), path + ".sizing")
            fraction = _number(closed["value"], path + ".sizing.value")
            if fraction <= 0 or fraction > .95:
                _invalid(path + ".sizing.value is out of range")
        else:
            _invalid(path + ".sizing.mode is unsupported")
        placement = _object(item["placement"], path + ".placement")
        if placement.get("mode") == "origin":
            _exact(placement, ("mode",), path + ".placement")
        elif placement.get("mode") == "anchor":
            closed = _exact(placement, ("mode", "value"), path + ".placement")
            _one_of(closed["value"], ("center", "top-third", "bottom-third",
                                      "lower-right", "lower-left"), path + ".placement.value")
        else:
            _invalid(path + ".placement.mode is unsupported")
        _time_range(item["time_range"], duration, path + ".time_range")
        if item["chroma"] is not None:
            chroma = _exact(item["chroma"], ("color", "similarity", "blend"), path + ".chroma")
            if _HEX_COLOR.fullmatch(_text(chroma["color"], path + ".chroma.color")) is None:
                _invalid(path + ".chroma.color is invalid")
            _range(chroma["similarity"], .01, 1, path + ".chroma.similarity")
            _range(chroma["blend"], 0, 1, path + ".chroma.blend")
    elif kind == "classic_color_grade":
        item = _exact(operation, ("kind", "preset", "brightness", "contrast",
                                  "saturation", "warmth", "tint"), path)
        _one_of(item["preset"], ("none", "warm-night", "cool-moonlight", "cinematic",
                                 "vintage", "dreamy-haze", "golden-hour", "noir"), path + ".preset")
        _range(item["brightness"], -1, 1, path + ".brightness")
        _range(item["contrast"], 0, 3, path + ".contrast")
        _range(item["saturation"], 0, 3, path + ".saturation")
        _range(item["warmth"], -1, 1, path + ".warmth")
        _range(item["tint"], -1, 1, path + ".tint")
    elif kind == "classic_vignette":
        _range(_exact(operation, ("kind", "strength"), path)["strength"], 0, 1, path + ".strength")
    elif kind == "classic_film_grain":
        _integer_range(_exact(operation, ("kind", "strength"), path)["strength"],
                       0, 100, path + ".strength")
    elif kind == "aged_archive_film":
        _integer_range(_exact(operation, ("kind", "strength"), path)["strength"],
                       1, 100, path + ".strength")
    elif kind == "classic_letterbox":
        item = _exact(operation, ("kind", "ratio", "mode", "color"), path)
        _one_of(item["ratio"], ("2.35:1", "2.00:1", "1.85:1"), path + ".ratio")
        _one_of(item["mode"], ("fit", "cover"), path + ".mode")
        if _COLOR.fullmatch(_text(item["color"], path + ".color")) is None:
            _invalid(path + ".color is invalid")
    elif kind == "presentation_frame":
        item = _exact(operation, ("kind", "inset_pct", "border_width", "corner_radius",
                                  "border_color", "background_color", "fit", "background"), path)
        _range(item["inset_pct"], 0, 25, path + ".inset_pct")
        _range(item["border_width"], 0, 160, path + ".border_width")
        _range(item["corner_radius"], 0, 300, path + ".corner_radius")
        for key in ("border_color", "background_color"):
            if _COLOR.fullmatch(_text(item[key], path + "." + key)) is None:
                _invalid(path + "." + key + " is invalid")
        _one_of(item["fit"], ("cover", "contain"), path + ".fit")
        background = _object(item["background"], path + ".background")
        if background.get("kind") == "solid":
            _exact(background, ("kind",), path + ".background")
        elif background.get("kind") == "asset":
            closed = _exact(background, ("kind", "asset", "input_mode"), path + ".background")
            _add_ref(refs, _asset_ref(closed["asset"], path + ".background.asset"), "frame_background")
            _input_mode(closed["input_mode"], path + ".background.input_mode")
        else:
            _invalid(path + ".background.kind is unsupported")
    elif kind == "late_label_stage":
        _late_label_operation(operation, duration, fps, refs, path, index)
    else:
        item = _exact(operation, ("kind", "asset", "opacity", "scale", "anchor",
                                  "margin_px", "gate"), path)
        _add_ref(refs, _asset_ref(item["asset"], path + ".asset"), "logo")
        _range(item["opacity"], 0, 1, path + ".opacity")
        _range(item["scale"], .01, 1, path + ".scale")
        _one_of(item["anchor"], ("top-left", "top-center", "top-right", "center-left",
                                 "center", "center-right", "bottom-left", "bottom-center",
                                 "bottom-right"), path + ".anchor")
        _integer_range(item["margin_px"], 0, 200, path + ".margin_px")
        _one_of(item["gate"], ("effects_gate", "after_10_seconds", "always"), path + ".gate")
    return stage


def _validate(value):
    packet = _exact(value, ("recipe", "manifest", "recipe_digest",
                            "manifest_digest", "packet_digest"), "packet")
    recipe = _exact(packet["recipe"], ("namespace", "profile_digest", "base_video",
                                      "premixed_audio", "canvas", "target_duration_sec",
                                      "effects_gate", "operations", "encode"), "recipe")
    if recipe["namespace"] != "studio.render.final.v1":
        _invalid("recipe.namespace is unsupported")
    if _SHA256.fullmatch(_text(recipe["profile_digest"], "recipe.profile_digest")) is None:
        _invalid("recipe.profile_digest is invalid")
    refs = {}
    _add_ref(refs, _asset_ref(recipe["base_video"], "recipe.base_video"), "base_video")
    _add_ref(refs, _asset_ref(recipe["premixed_audio"], "recipe.premixed_audio"), "premixed_audio")
    canvas = _exact(recipe["canvas"], ("width", "height", "working_fps"), "recipe.canvas")
    width = _integer(canvas["width"], "recipe.canvas.width")
    height = _integer(canvas["height"], "recipe.canvas.height")
    fps = _integer(canvas["working_fps"], "recipe.canvas.working_fps")
    if width < 2 or height < 2 or width % 2 or height % 2:
        _invalid("recipe.canvas dimensions must be positive even values")
    if fps not in _FPS:
        _invalid("recipe.canvas.working_fps is unsupported")
    if width * height > _MAX_SAFE or width * height * fps > _MAX_SAFE:
        _invalid("recipe.canvas products are unsafe")
    duration = _number(recipe["target_duration_sec"], "recipe.target_duration_sec")
    if duration <= 0:
        _invalid("recipe.target_duration_sec must be positive")
    _effects_gate(recipe["effects_gate"], duration, "recipe.effects_gate")
    if type(recipe["operations"]) is not list:
        _invalid("recipe.operations must be an array")
    last_stage = -1
    seen = set()
    cue_ids = set()
    for index, item in enumerate(recipe["operations"]):
        stage = _operation(item, duration, fps, refs, index)
        if stage < last_stage:
            _invalid("recipe.operations stage order is invalid")
        kind = item["kind"]
        if kind not in ("overlay_layer", "late_label_stage") and kind in seen:
            _invalid("recipe.operations repeats singleton " + kind)
        if kind == "late_label_stage":
            cue_id = item["cue_id"]
            if cue_id in cue_ids:
                _invalid("recipe.operations repeats cue_id " + cue_id)
            cue_ids.add(cue_id)
        seen.add(kind)
        last_stage = stage

    encode = _exact(recipe["encode"], ("container", "requested_codec", "requested_effort",
                                       "crf", "quality_cpu_preset", "bitrate_kbps"), "recipe.encode")
    _one_of(encode["container"], ("mp4", "mov", "webm"), "recipe.encode.container")
    _one_of(encode["requested_codec"], ("h264", "h265"), "recipe.encode.requested_codec")
    _one_of(encode["requested_effort"], ("ultrafast", "medium", "slow", "hardware"),
            "recipe.encode.requested_effort")
    crf = _integer(encode["crf"], "recipe.encode.crf")
    preset = _one_of(encode["quality_cpu_preset"], ("ultrafast", "medium", "slow"),
                     "recipe.encode.quality_cpu_preset")
    if (crf, preset) not in ((32, "ultrafast"), (28, "ultrafast"), (23, "medium"),
                             (18, "slow"), (23, "ultrafast")):
        _invalid("recipe.encode quality pair is unsupported")
    if encode["bitrate_kbps"] is not None:
        _integer_range(encode["bitrate_kbps"], 500, 100000, "recipe.encode.bitrate_kbps")

    manifest = packet["manifest"]
    if type(manifest) is not list:
        _invalid("manifest must be an array")
    ids = set()
    names = set()
    previous = ""
    for index, raw in enumerate(manifest):
        path = "manifest[" + str(index) + "]"
        entry = _exact(raw, ("logical_id", "safe_name", "role", "sha256", "size_bytes"), path)
        logical_id = _text(entry["logical_id"], path + ".logical_id")
        if _ID.fullmatch(logical_id) is None or logical_id <= previous or logical_id in ids:
            _invalid(path + ".logical_id is invalid or unordered")
        previous = logical_id
        ids.add(logical_id)
        safe_name = _text(entry["safe_name"], path + ".safe_name")
        match = _SAFE_NAME.fullmatch(safe_name)
        if match is None or match.group(1) != logical_id or safe_name in names:
            _invalid(path + ".safe_name is invalid")
        names.add(safe_name)
        role = _one_of(entry["role"], _ROLES, path + ".role")
        if refs.get(logical_id) != role:
            _invalid(path + " does not match an exact recipe reference")
        if _SHA256.fullmatch(_text(entry["sha256"], path + ".sha256")) is None:
            _invalid(path + ".sha256 is invalid")
        if _integer(entry["size_bytes"], path + ".size_bytes") <= 0:
            _invalid(path + ".size_bytes must be positive")
    if len(ids) != len(refs) or any(logical_id not in ids for logical_id in refs):
        _invalid("manifest/reference bijection is incomplete")

    recipe_digest = _text(packet["recipe_digest"], "packet.recipe_digest")
    manifest_digest = _text(packet["manifest_digest"], "packet.manifest_digest")
    packet_digest = _text(packet["packet_digest"], "packet.packet_digest")
    if any(_SHA256.fullmatch(digest) is None
           for digest in (recipe_digest, manifest_digest, packet_digest)):
        _invalid("packet digest syntax is invalid")
    if render_hash("recipe", recipe) != recipe_digest:
        _invalid("recipe digest mismatch")
    if render_hash("manifest", manifest) != manifest_digest:
        _invalid("manifest digest mismatch")
    if render_hash("packet", {
        "recipe_digest": recipe_digest,
        "manifest_digest": manifest_digest,
    }) != packet_digest:
        _invalid("packet digest mismatch")
    return packet


def validate_render_packet_syntax(raw):
    """Decode strict raw JSON and validate syntax/identity only, never executability."""
    try:
        return _validate(decode_render_json(raw))
    except ValueError as exc:
        if str(exc).startswith("Invalid render packet:"):
            raise
        raise ValueError("Invalid render packet: " + str(exc)) from exc

