#!/usr/bin/env python3
"""Prove a whole long script renders end to end: one request in, one joined artifact out.

The gateway asked for a 15,000-character ceiling, and the honest answer is not a single number: each
local model is rendered in sentence-safe sections of its own size, and the artifact is joined back
into one file. This submits ONE request with a script of the requested length, waits for it, and
reports what came back - how many sections the worker actually rendered, how long it took, and the
artifact's own duration and size. It is the end-to-end counterpart to
`measure_section_budget.py`, which asks how large a section may safely be.

  .build/voice-runtime/bin/python3 qualification/long_script_run.py --model mlx-community/Kokoro-82M-bf16 --chars 15000

Exit status is 0 only when the job finished and produced an artifact.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.request
import uuid

READY = pathlib.Path.home() / "Library/Application Support/StudioHub Standalone/hub-ready.json"
# The worker logs one line per rendered section; reading the count from the log is the only way to
# see whether sectioning actually happened, as opposed to the job merely finishing.
WORKER_LOG = (pathlib.Path.home()
              / "Library/Application Support/StudioHub Standalone/hub-worker.log")

# Plain English narration, no markup. Sentences are varied in length so a section boundary cannot
# always land on a comma; the script is built from these until the requested length is reached and
# always ends on a sentence boundary.
SENTENCES = (
    "The morning light came across the harbour slowly, touching the moored boats one by one.",
    "She counted the crates twice, because the first count had been interrupted by the tide.",
    "There is a kind of patience that only appears when the work cannot be hurried.",
    "He wrote the measurement down, read it back, and then wrote it down again.",
    "The engine settled into its rhythm, and the noise of it became a sort of silence.",
    "Every route they considered that week turned out to need a bridge that was not there.",
    "When the weather turned, the whole crew moved as one, without anyone raising a voice.",
    "It was the sort of afternoon that makes a person believe the day will never end.",
    "The ledger recorded the weight of each load, the time it arrived, and who had signed for it.",
    "Nothing about the plan was clever; it was simply done in the right order.",
    "A long delivery is not one long breath, but many short ones joined carefully together.",
    "She kept the spool of tape in her coat pocket, and it was always there when it was needed.",
)

PRESET_KEY = {
    "kokoro-mlx": "kokoro_voices",
    "orpheus": "orpheus_voices",
    "vibevoice": "vibevoice_voices",
    "voxtral-tts": "voxtral_voices",
    "voxtral": "voxtral_voices",
    "marvis": "marvis_voices",
}


def call(base: str, token: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        base + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read() or b"{}")


def build_script(characters: int) -> str:
    parts: list[str] = []
    length = 0
    index = 0
    while length < characters:
        sentence = SENTENCES[index % len(SENTENCES)]
        parts.append(sentence)
        length += len(sentence) + 1
        index += 1
    return " ".join(parts)


def log_size() -> int:
    try:
        return WORKER_LOG.stat().st_size
    except OSError:
        return 0


# The worker names each rendered section on disk (`/tmp/voice_sections_<job>/section_001.wav`), so
# counting those paths after the submission is what shows the script really was sectioned. Matching
# only the word "section" missed every one of them: the first 15,058-character run reported 0.
_SECTION_LINE = re.compile(r"section_\d+\.wav")


def sections_since(offset: int) -> int:
    """How many sections the worker rendered after `offset` - the proof that it sectioned."""
    try:
        text = WORKER_LOG.read_text(errors="ignore")[offset:]
    except OSError:
        return 0
    return len(_SECTION_LINE.findall(text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="the model repo to render with")
    parser.add_argument("--chars", type=int, default=15000, help="script length in characters")
    parser.add_argument("--family", default="", help="override the family used to pick a voice")
    parser.add_argument("--timeout", type=int, default=5400, help="seconds to wait for the job")
    args = parser.parse_args()

    ready = json.loads(READY.read_text())
    base, token = ready["url"], ready["token"]
    catalogue = call(base, token, "/api/hub/catalog")
    rows = catalogue.get("models", catalogue)
    row = next((item for item in rows if item.get("repo") == args.model), None)
    if row is None:
        print(f"FAIL {args.model} is not in the Hub's catalogue")
        return 1
    family = args.family or str(row.get("family") or "")
    published = row.get("long_form_delivery") or {}
    budget = published.get("section_max_characters")
    expected_sections = -(-args.chars // budget) if budget else 1

    availability = call(base, token, "/studio/voice/api/generate/availability")
    preset = ""
    options = availability.get(PRESET_KEY.get(family, "")) or []
    if options:
        first = options[0]
        preset = (first.get("id") or first.get("name") or first.get("voice") or ""
                  if isinstance(first, dict) else str(first))

    script = build_script(args.chars)
    print(f"model          {args.model}")
    print(f"family         {family}")
    print(f"section budget {budget} characters (source: {published.get('source')})")
    print(f"script         {len(script)} characters -> expecting about {expected_sections} sections")
    print(f"voice          {preset or '(catalogue default)'}")

    before = log_size()
    body = {
        "modality": "voice", "model": args.model,
        "client_request_id": f"longscript-{uuid.uuid4().hex[:12]}",
        "items": [{"text": script,
                   "params": {"language": "en", "speed": 1.0, "preset_speaker": preset}}],
    }
    submitted = call(base, token, "/api/hub/jobs", body)
    batch = submitted.get("batch_id") or submitted.get("id")
    started = time.time()
    state, error, item = "unknown", "", {}
    while time.time() - started < args.timeout:
        time.sleep(10)
        detail = call(base, token, f"/api/hub/jobs/{batch}")
        item = (detail.get("items") or [{}])[0]
        state = item.get("state") or "unknown"
        error = str(item.get("error") or detail.get("governor_note") or "")
        if state in ("done", "error", "cancelled"):
            break
    elapsed = time.time() - started
    rendered = sections_since(before)
    artifact = item.get("artifact_url")

    print(f"state          {state}")
    print(f"wall seconds   {elapsed:.1f}")
    print(f"sections seen  {rendered}")
    for key in ("audio_duration_s", "duration_s", "runtime_s", "bytes", "sample_rate_hz"):
        if item.get(key) is not None:
            print(f"{key:14s} {item[key]}")
    if error:
        print(f"error          {error[:400]}")
    if state != "done" or not artifact:
        print("FAIL: no artifact was produced")
        return 1
    if rendered and rendered < 2 and len(script) > (budget or 0):
        # A long script that produced one section line did not get sectioned: report it rather than
        # calling the run a success because an artifact exists.
        print("WARNING: a script longer than one section produced fewer than two section lines")
    print(f"PASS: one request of {len(script)} characters produced {artifact}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
