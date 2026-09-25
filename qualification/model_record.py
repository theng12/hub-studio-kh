#!/usr/bin/env python3
"""Write the fleet's model record — what runs on which Mac, and what was measured.

    ./.build/voice-runtime/bin/python3 -B qualification/model_record.py [--write]

The owner asked for this in as many words: *"something i can keep and store so that future me knows
this benchmark and stuff… if we can test any models, it's records should be stored properly… i just
don't remember. that's why i want something lasting."*

**Why it is generated rather than written.** A hand-kept table of results goes stale and then lies —
this project has paid for that four times (a tracker row that outlived its fix, a handoff that
described a build that was not installed, a `185.61 s` no run could be found for, a `79.34 s` still
unaccounted for). So the document is a *function of the records*: the tiers come from the manifests
the app itself publishes, the voice runtimes from the lane's own evidence file, and the remaining
readings from `qualification/model-readings.json`, where every row names the record it was read from
and the generator fails if that record is gone. `tests/test_model_record.py` regenerates this in
memory and fails if the committed file differs by one byte, so it cannot drift.

**What it deliberately does not do.** It publishes no number that is not already recorded somewhere,
and it names an unmeasured tier as unmeasured rather than leaving a blank that reads like a zero.
Quantization is stated wherever a source states it and the words "not stated" appear where none
does — it is never inferred from a repository name, which is the guessing that once hid six voice
packages from a picker.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # so the workers import as the app imports them
    sys.path.insert(0, str(ROOT))
MANIFESTS = ROOT / "model-manifests"
READINGS = ROOT / "qualification" / "model-readings.json"
VOICE_RUNTIMES = (
    ROOT / "qualification" / "evidence" / "voice-runtime-2026-09-25" / "measured-runtimes.json"
)
DEFAULT_OUTPUT = ROOT / "docs" / "model-benchmarks.md"

# The owner's fleet. `research/owner-hardware.md` is the canonical home — AGENTS.md says so, and
# says not to ask him to restate it — so this is a copy and `tests/test_model_record.py` reads that
# file and fails if the two disagree. It is a copy because the alternative is parsing a prose table
# at build time, and it is a *checked* copy because this one already went wrong: it said the 32 GB
# Mac was an M1 Max, the value `research/owner-hardware.md` records as an error it had corrected.
FLEET = [
    (8, "Apple M1, M2", 2),
    (16, "Apple M1, M2, M4", 3),
    (24, "Apple M4", 1),
    (32, "Apple M2 Max (Mac Studio)", 1),
]

# What each engine is, in the owner's terms, so the table reads without a decoder ring.
ENGINE_NAMES = {
    "chat-mlx": "MLX (text)",
    "image-mflux": "mflux (MLX)",
    "music-mlx": "MLX (audio)",
    "subtitle-mlx": "MLX (speech-to-text)",
    "voice-mlx": "MLX (speech)",
    "voice-vits": "VITS / ONNX",
}

MODALITY_ORDER = ["chat", "voice", "subtitle", "image", "music"]
MODALITY_TITLES = {
    "chat": "Chat (LLM)",
    "voice": "Voice (TTS)",
    "subtitle": "Subtitle (speech-to-text)",
    "image": "Image",
    "music": "Music",
}
MODALITY_OF_ENGINE = {
    "chat-mlx": "chat",
    "voice-mlx": "voice",
    "voice-vits": "voice",
    "subtitle-mlx": "subtitle",
    "image-mflux": "image",
    "music-mlx": "music",
}


def load_manifests() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(MANIFESTS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append(data)
    return rows


def load_readings() -> dict[str, Any]:
    return json.loads(READINGS.read_text(encoding="utf-8"))


def load_voice_runtimes() -> dict[str, Any]:
    if not VOICE_RUNTIMES.exists():
        return {"measurements": [], "withheld": []}
    return json.loads(VOICE_RUNTIMES.read_text(encoding="utf-8"))


def label_map(manifests: list[dict[str, Any]], readings: dict[str, Any]) -> dict[str, str]:
    """`package_id` → the name the producing Studio publishes for it.

    Read from the workers themselves rather than kept here. A label table in this file would be a
    second list of models — the defect that once hid six voice packages from the Audio picker — and
    it would go stale on the next model added. Where a producer's label carries no bits (Whisper is
    not quantized) nothing is invented to fill the gap: the app already treats a label without a
    quantisation marker as a model that has none to claim.
    """
    labels: dict[str, str] = {}
    repo_to_label: dict[str, str] = {}
    for module_name, attribute in (
        ("components.voice.app.backend.catalog", "CATALOG"),
        ("components.music.app.backend.catalog", "CATALOG"),
    ):
        module = importlib.import_module(module_name)
        for entry in getattr(module, attribute):
            repo = getattr(entry, "repo", None)
            label = getattr(entry, "label", None)
            if repo and label:
                repo_to_label[repo] = label
    # Image publishes a dict keyed by repository; Music a list of entries.
    image = importlib.import_module("components.image.app.backend.generation")
    for repo, entry in getattr(image, "MODELS").items():
        label = getattr(entry, "label", None)
        if label:
            repo_to_label[repo] = label
    # Subtitle resolves a label from its manifest, then its own default table — mirror that, so the
    # name here is the name that worker would publish.
    subtitle = importlib.import_module("components.subtitle.app.backend.engine")
    subtitle_labels = getattr(subtitle, "_DEFAULT_LABELS", {})
    for manifest in manifests:
        package_id = manifest["package_id"]
        label = (manifest.get("label") or repo_to_label.get(manifest.get("model_id", ""))
                 or subtitle_labels.get(package_id))
        if label:
            labels[package_id] = label
    # Chat has no static label — its worker names the model from the loaded engine — so a recorded
    # reading supplies it. That is a stated name with a source, not a name guessed here.
    for row in readings.get("readings", []):
        labels.setdefault(row["package_id"], row["model"])
    return labels


def tier_words(minimum: Any, recommended: Any) -> str:
    if minimum is None:
        return "not stated"
    if recommended is None or recommended == minimum:
        return f"{minimum} GB"
    return f"{minimum} GB (recommended {recommended} GB)"


METRIC_LABELS = {
    "tokens_per_second": "speed",
    "generation_seconds": "generation",
    "generation_seconds_default": "generation, default",
    "generation_seconds_low_ram": "generation, low RAM",
    "load_seconds": "load",
    "peak_mlx_gb": "peak MLX",
    "peak_mlx_gb_default": "peak MLX, default",
    "peak_mlx_gb_low_ram": "peak MLX, low RAM",
    "peak_footprint_gb_default": "peak footprint, default",
    "peak_footprint_gb_low_ram": "peak footprint, low RAM",
    "swap_peak_mb": "swap peak",
    "swaps": "swaps",
    "memory_available_at_start_gb": "memory free at start",
    "weights_on_disk_gb": "weights on disk",
    "low_ram_mode": "low-RAM mode",
}


def metric_words(key: str, value: Any) -> str:
    label = METRIC_LABELS.get(key, key.replace("_", " "))
    if isinstance(value, (int, float)) and key.endswith(("_gb", "_mb")):
        unit = "GB" if key.endswith("_gb") else "MB"
        return f"{label} {value} {unit}"
    if isinstance(value, (int, float)) and key.endswith("_seconds"):
        return f"{label} {value} s"
    return f"{label} {value}"


def machine_can_run(tier_gb: Any, machine_gb: int) -> str:
    """What the *declared tier* says about one machine size, never a measurement."""
    if tier_gb is None:
        return "—"
    return "yes" if machine_gb >= tier_gb else "no"


def build() -> str:
    manifests = load_manifests()
    readings = load_readings()
    voice = load_voice_runtimes()
    labels = label_map(manifests, readings)

    for row in readings["readings"]:
        source = ROOT / row["source_path"]
        if not source.exists():
            raise SystemExit(
                f"{row['model']} on {row['machine']} names a source that is gone: "
                f"{row['source_path']} — a reading may not outlive its evidence"
            )

    companions = {dep for manifest in manifests
                  for dep in (manifest.get("requires_packages") or [])}

    by_modality: dict[str, list[dict[str, Any]]] = {name: [] for name in MODALITY_ORDER}
    for manifest in manifests:
        engine = manifest.get("engine", "")
        modality = MODALITY_OF_ENGINE.get(engine)
        if modality is None:
            continue
        by_modality[modality].append(manifest)

    out: list[str] = []
    add = out.append

    add("# Model record — what runs on which Mac, and what was measured")
    add("")
    add("**What this is.** One page for every model this fleet ships: the memory it asks for, whether")
    add("that number was *measured* or only *declared*, and what actually happened when it ran. It is")
    add("generated from the manifests and the stored evidence — do not edit it by hand; run")
    add("`qualification/model_record.py --write` and it is rebuilt from the records.")
    add("")
    add("**How to read it.** `[M]` measured — a run left a record, and the source is named.")
    add("`[V]` the publisher's claim. `[I]` inference, ours, and said so. A tier with no `[M]` beside")
    add("it is a *declared floor from catalog metadata*, which is a starting point rather than proof.")
    add("**Every model is named with its quantization**, because a model named without it cannot be")
    add("checked against what is actually on disk.")
    add("")

    add("## Your machines")
    add("")
    add("| Unified memory | Chips | How many |")
    add("| --- | --- | ---: |")
    for gb, chips, count in FLEET:
        add(f"| {gb} GB | {chips} | {count} |")
    add("")

    add("## What each machine can run")
    add("")
    add("Read this as the **declared** floor, not as a verdict — a model whose floor is above a Mac")
    add("may still run there, and one at or below may still not (see the peak-memory note at the end).")
    add("")
    header = "| Model | Engine | RAM floor |"
    rule = "| --- | --- | --- |"
    for gb, _chips, _count in FLEET:
        header += f" {gb} GB |"
        rule += " --- |"
    add(header)
    add(rule)
    for modality in MODALITY_ORDER:
        rows = by_modality[modality]
        if not rows:
            continue
        add(f"| **{MODALITY_TITLES[modality]}** | | |" + " |" * len(FLEET))
        for manifest in sorted(rows, key=lambda m: (m["qualification"].get("minimum_unified_memory_gb") or 0,
                                                    m["package_id"])):
            q = manifest.get("qualification") or {}
            floor = q.get("minimum_unified_memory_gb")
            label = labels.get(manifest["package_id"], manifest["package_id"])
            if manifest["package_id"] in companions:
                # Its own row would answer "can this machine run it?" about a file nobody picks.
                label += " *(supporting files)*"
            line = (f"| {label} | {ENGINE_NAMES.get(manifest.get('engine',''), manifest.get('engine',''))} "
                    f"| {tier_words(floor, q.get('recommended_unified_memory_gb'))} |")
            for gb, _chips, _count in FLEET:
                line += f" {machine_can_run(floor, gb)} |"
            add(line)
    add("")
    add("`yes` / `no` above is arithmetic on the published floor. It is not a measurement, and the")
    add("section below is where measurements live.")
    add("")

    add("## What has actually been measured")
    add("")
    measured = readings["readings"]
    if not measured:
        add("Nothing yet. Every floor above is catalog metadata.")
        add("")
    else:
        add("| Model | Quantization | Machine | Result | Source |")
        add("| --- | --- | --- | --- | --- |")
        for row in measured:
            metrics = ", ".join(metric_words(k, v) for k, v in row["metrics"].items())
            add(f"| {row['model']} | {row['quantization']} | {row['machine']} | "
                f"[{row['evidence']}] {row['outcome']} — {metrics} | `{row['source_path']}` |")
        add("")
        add("`[M]` on every row above: each is a run that left the record named in the last column.")
        add("")

    voice_rows = voice.get("measurements") or []
    if voice_rows:
        add("### Voice lane runtimes, per item")
        add("")
        add("These are the Voice worker's own measured per-item runtimes — the same numbers it")
        add("publishes as `runtime_seconds`, read from the probe receipts the runs left behind.")
        add("")
        add("| Model | Quantization | Measured on | Runtime | Scene |")
        add("| --- | --- | --- | --- | --- |")
        for row in voice_rows:
            for item in row.get("measured") or []:
                add(f"| {row.get('repo','')} | {row.get('quantization','not stated')} | "
                    f"{row.get('measured_on','')} | **{item.get('seconds')} s** | {item.get('scene','')} |")
        add("")
    withheld = voice.get("withheld") or []
    if withheld:
        add("**Withheld on purpose** — a model with no stored probe receipt gets no runtime rather")
        add("than a curated one:")
        add("")
        for row in withheld:
            if isinstance(row, dict):
                add(f"- {row.get('repo', row)} — {row.get('why') or row.get('reason') or 'no stored receipt'}")
            else:
                add(f"- {row}")
        add("")

    add("## What is NOT measured")
    add("")
    with_a_run = {row["package_id"] for row in measured if row["evidence"] == "M"}
    unmeasured = [m["package_id"] for m in manifests
                  if m["package_id"] not in with_a_run
                  and (m.get("qualification") or {}).get("status") != "qualified"]
    add(f"{len(unmeasured)} of {len(manifests)} packages have no run behind them — their floor is")
    add("catalog metadata, which is a starting point rather than proof. That is not a defect in any")
    add("one of them; it is the honest state of the record, and it is the list of what a test would")
    add("settle:")
    add("")
    add(", ".join(f"`{name}`" for name in unmeasured))
    add("")

    add("## Two things that surprise people reading this")
    add("")
    add("**A peak measured on a big Mac is inflated, and that is why a \"too big\" model runs on a")
    add("small one.** MLX grows its buffer cache into whatever memory is free, so the peak it reports")
    add("on a 32 GB machine is partly a property of that machine. On an 8 GB Mac the cache cannot")
    add("grow, so the same model peaks lower. This is why Z-Image-Turbo 4-bit reports a 12.9 GB-class")
    add("peak and still runs on 8 GB machines. **A floor is a qualification tier, not a wall.**")
    add("")
    add("**Two kinds of evidence sit behind \"it works\".** The rows in this document are")
    add("*benchmarks*: one run, watched, with a number. FLUX.2 Klein 4B MLX 4-bit's 8 GB")
    add("qualification is not that — it rests on completed runs in ordinary use across the fleet. Both")
    add("are real evidence and they answer different questions, so a tier is never \"qualified\" here")
    add("without saying which kind it is.")
    add("")

    add("## Adding a record")
    add("")
    add("So that a future test does not become the thing you cannot remember:")
    add("")
    add("1. **Run the harness, not a guess.** Chat has `Technical/RUN-CHAT-TIER.command` on every kit")
    add("   drive (double-click it); the 8 GB image run is `Technical/8gb-z-image-run.sh`.")
    add("2. **Keep what it wrote.** Each harness writes a folder of results next to itself. That folder")
    add("   *is* the evidence; a number pasted into a chat without it cannot be admitted.")
    add("3. **Record the reading** in `qualification/model-readings.json`, naming the record in")
    add("   `source_path`. The generator refuses a row whose source is missing.")
    add("4. **Rebuild this page**: `qualification/model_record.py --write`. The test that guards it will")
    add("   fail until you do, which is the point — the page cannot drift away from the records.")
    add("")

    add("---")
    add("")
    add(f"Generated from {len(manifests)} manifests, {len(measured)} recorded readings and "
        f"{len(voice_rows)} voice runtime rows.")
    add("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true", help="write the document")
    args = parser.parse_args()

    document = build()
    if args.write:
        args.output.write_text(document, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
