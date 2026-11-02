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
    emoji_cap = probe.validate_scene_json(scene_text([
        {"scene_id": "scene-001", "visual_prompt": "  " + "🪔" * 2000 + "  "},
    ]), ["scene-001"], max_chars=4000)
    assert emoji_cap["pass"] and emoji_cap["length_stats"]["max_chars"] == 4000
    assert not probe.validate_scene_json(scene_text([
        {"scene_id": "scene-001", "visual_prompt": "🪔" * 2000 + "a"},
    ]), ["scene-001"], max_chars=4000)["pass"], "4001 UTF-16 units must fail"
    assert not probe.validate_scene_json(scene_text([
        {"scene_id": "scene-001", "visual_prompt": "a" * 3996},
    ]), ["scene-001"], max_chars=4000, style="ink")["pass"], "style prefix counts toward cap"

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

    long_spec = {
        "minimum_words_per_chapter": 3, "maximum_words_per_chapter": 5,
        "chapters": [
            {"number": 7, "title": "Quiet Meridian"},
            {"number": 8, "title": "Lantern Ledger"},
            {"number": 9, "title": "Dawn Watch"},
        ],
        "must_include": ["Mara", "brass key"], "must_not_include": ["lost the brass key"],
    }
    long_script = (
        "## Chapter 07: Quiet Meridian\n\nMara holds brass key\n\n"
        "## Chapter 08: Lantern Ledger\n\nstill stars remain calm\n\n"
        "## Chapter 09: Dawn Watch\n\nmorning light arrives"
    )
    checked = probe.validate_long_script(long_script, long_spec)
    assert checked["pass"] and [row["word_count"] for row in checked["chapters"]] == [4, 4, 3]
    assert not probe.validate_long_script(long_script.replace(
        "still stars remain calm", "short"), long_spec)["pass"], "every chapter needs its own length check"
    assert not probe.validate_long_script(long_script +
        "\n\n## Chapter 10: Extra\n\nextra body words", long_spec)["pass"], "extra chapter must fail"

    # Long-context validation
    lc_spec = {
        "expected_sections": 3,
        "needle": "HARBOR LANTERN",
    }
    valid_lc = (
        "## Section 01: Dawn Observation\nDetailed notes here.\n\n"
        "## Section 02: Tide Records\nWater levels steady.\n\n"
        "## Section 03: Final Key\nSecret Key: HARBOR LANTERN"
    )
    assert probe.validate_long_context(valid_lc, lc_spec)["pass"]
    assert not probe.validate_long_context(valid_lc.replace("HARBOR LANTERN", "WRONG KEY"), lc_spec)["pass"], "missing needle must fail"
    assert not probe.validate_long_context("## Section 01: Only One", lc_spec)["pass"], "missing sections must fail"
    assert not probe.validate_long_context(valid_lc + "\n<think>leak</think>", lc_spec)["pass"], "think spill must fail"

    # Stop rule checks
    limit_26gb = int(26.0 * probe.GIB)
    # Safe case: no swap, under 26 GB
    stop, reason, safe = probe.eval_stop_rule(
        {"name": "input-48k", "kernel_phys_footprint_peak_bytes": int(22.0 * probe.GIB), "swap_delta_bytes": 0},
        limit_26gb, "none",
    )
    assert not stop and safe == "input-48k"

    # Swap growth: stops before next size
    stop, reason, safe = probe.eval_stop_rule(
        {"name": "input-48k", "kernel_phys_footprint_peak_bytes": int(22.0 * probe.GIB), "swap_delta_bytes": 1024 * 1024},
        limit_26gb, "none",
    )
    assert stop and "swap grew" in reason and safe == "none"

    # Footprint exceeded: stops before next size
    stop, reason, safe = probe.eval_stop_rule(
        {"name": "input-64k", "kernel_phys_footprint_peak_bytes": int(27.0 * probe.GIB), "swap_delta_bytes": 0},
        limit_26gb, "input-48k",
    )
    assert stop and "exceeded 26.00 GiB limit" in reason and safe == "input-48k"

    print("PASS: strict chapter, scene identity, long-context, stop-rule, and think-spill checks")


if __name__ == "__main__":
    main()
