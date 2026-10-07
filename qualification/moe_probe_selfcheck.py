#!/usr/bin/env python3
"""Stdlib-only checks for probe parsers and validators; never imports MLX."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("moe_probe", HERE / "moe_probe.py")
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
assert "mlx" not in sys.modules and "mlx_lm" not in sys.modules
assert probe.CACHE_LIMIT == 512_000_000
assert probe.CACHE_LIMIT_SOURCE.startswith("configured:")
assert "not measured RAM, throughput, or an admission floor" in probe.CACHE_LIMIT_SOURCE


def scene_text(rows):
    import json
    return json.dumps({"results": rows})


def main() -> None:
    ids = ["scene-001", "scene-002"]
    valid = scene_text([
        {"scene_id": "scene-001", "visual_prompt": "A lantern glows beside a quiet harbor."},
        {"scene_id": "scene-002", "visual_prompt": "Morning light reaches the stone quay."},
    ])
    assert probe.validate_scene_json(valid, ids)["pass"]
    assert not probe.validate_scene_json("```json\n" + valid + "\n```", ids)["pass"], "fences must not be repaired"
    assert not probe.validate_scene_json('{"results":[}', ids)["pass"], "malformed JSON must fail"
    assert not probe.validate_scene_json(scene_text([
        {"scene_id": "scene-001", "visual_prompt": "usable"},
    ]), ids)["pass"], "missing result must fail"
    assert not probe.validate_scene_json(scene_text([
        {"scene_id": "scene-001", "visual_prompt": "usable"},
        {"scene_id": "scene-001", "visual_prompt": "also usable"},
    ]), ids)["pass"], "duplicate IDs must fail"
    assert not probe.validate_scene_json(scene_text([
        {"scene_id": "scene-001", "visual_prompt": "usable"},
        {"scene_id": "scene-002", "visual_prompt": "   "},
    ]), ids)["pass"], "empty prompts must fail"
    assert not probe.validate_scene_json('{"results":[],"comment":"fixed"}', [])["pass"], "extra keys must fail"

    chapter_spec = {
        "minimum_words": 8, "maximum_words": 30,
        "must_include": ["Mara", "Brindle Observatory", "brass key"],
        "must_not_include": ["lost the brass key"],
    }
    chapter = "## Chapter 07: The Quiet Meridian\n\nMara entered Brindle Observatory carrying the brass key in silence."
    assert probe.validate_chapter(chapter, chapter_spec)["pass"]
    assert not probe.validate_chapter(chapter.replace("brass key", "small key"), chapter_spec)["pass"]
    assert not probe.validate_chapter(chapter + " She lost the brass key.", chapter_spec)["pass"]
    assert not probe.validate_chapter(chapter + "\n<think>private trace</think>", chapter_spec)["pass"]
    assert probe.word_count("calm water, lamp-light") == 3
    print("PASS: strict JSON, ID, nonempty prompt, chapter fact, word-count, and think-spill checks")


if __name__ == "__main__":
    main()

