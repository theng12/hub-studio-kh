#!/usr/bin/env python3
"""Measure the long-form section budget at which a local TTS family starts losing words.

The Voice worker splits a long script into sentence-safe sections before rendering, because several
local engines drop words when one synthesis call is too long.  Each family's budget lives in
``components/voice/app/backend/long_form_policy.py``.  This script measures what that budget should
be instead of guessing, using the same transcribe-back evidence the OmniVoice budget was set from:

  submit one identical script at several section budgets, same voice and seed,
  transcribe each produced WAV back with the local Whisper worker,
  compare the heard words against the source words.

It prints one row per budget (budget, sections, chars, wall seconds, coverage %, words lost) and a
one-line recommendation: the largest budget that keeps coverage within one word of the best
coverage observed.  If coverage is still perfect at the largest measured budget, the line says
"at least N, not yet limited" - never that the largest budget is a ceiling.

Honest by construction
-----------------------
* A budget whose job fails is reported as ``failed`` with the worker's own reason; no coverage is
  invented for it.
* A budget whose override the worker ignored is caught, not averaged away: the script compares the
  section count the worker actually rendered against the budget it was asked for.  If every budget
  that should have split the script still rendered a single call, the run is declared
  NOT MEASURABLE, the table is printed for the record, and the script exits non-zero.
* If the local transcription route is unavailable, the script stops loudly instead of printing a
  coverage number it could not measure.

Run:
  .build/voice-runtime/bin/python3 qualification/measure_section_budget.py \\
      --family voxtral-tts --repo mlx-community/Voxtral-4B-TTS-2603-mlx-4bit \\
      --budgets 200,280,350,500,800 --text-file /tmp/measure_text.txt

Only the standard library and the running Hub are used: no third-party packages, no product source
is imported or modified.  WAVs and transcripts are kept under ``--out-dir``.

Which override field actually reaches the engine
------------------------------------------------
The Voice worker's HTTP request model (``Txt2SpeechBody``) has no ``_resolved_section_max_characters``
member, so it silently drops that key; the field it does read is the public ``section_max_characters``,
and even that is refused unless the model declares a section-size control.  ``--override-field``
defaults to the private ``_resolved_section_max_characters`` name this measurement was specified
with; if the worker ignores it, the script says so, then probes ``section_max_characters`` to report
whether the worker refuses or accepts that one.  A caller measuring a family whose model declares a
section-size control should pass ``--override-field section_max_characters``.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter

READY = pathlib.Path.home() / "Library/Application Support/StudioHub Standalone/hub-ready.json"
DEFAULT_WORKER_LOG = (
    pathlib.Path.home() / "Library/Application Support/StudioHub Standalone/hub-worker.log"
)

# family -> the availability key that lists its preset voices.  Families absent here are clone
# families and need --voice-library-id, because their engines cannot run without a reference.
PRESET_KEY = {
    "kokoro-mlx": "kokoro_voices",
    "orpheus": "orpheus_voices",
    "vibevoice": "vibevoice_voices",
    "voxtral": "voxtral_voices",
    "voxtral-tts": "voxtral_voices",
    "marvis": "marvis_voices",
    "qwen3-tts": "qwen3_preset_speakers",
    "bark": "bark_voice_presets",
}
TERMINAL = {"done", "error", "cancelled"}
WORD_RE = re.compile(r"[a-z0-9']+")


# ─────────────────────────────── Hub plumbing (urllib only) ───────────────────────────────


def http(base: str, token: str, method: str, path: str, *, body=None,
         content_type: str = "application/json", timeout: float = 120.0):
    """Return (status, body_bytes).  An HTTP error is a result, not an exception."""
    data = body
    if body is not None and content_type == "application/json":
        data = json.dumps(body).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(base + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def http_json(base: str, token: str, method: str, path: str, *, body=None, timeout: float = 120.0):
    status, raw = http(base, token, method, path, body=body, timeout=timeout)
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {}
    return status, parsed, raw


def load_hub():
    if not READY.is_file():
        raise SystemExit(f"FAIL: {READY} is missing - Studio Hub is not running.")
    ready = json.loads(READY.read_text())
    return ready["url"], ready["token"]


# ─────────────────────────────── default family / repo / voice ───────────────────────────────


def resolve_model(base: str, token: str, family: str | None, repo: str | None):
    status, catalog, raw = http_json(base, token, "GET", "/api/hub/catalog")
    if status >= 400:
        raise SystemExit(f"FAIL: /api/hub/catalog -> HTTP {status}: {raw[:300]!r}")
    models = catalog.get("models", catalog)
    if not isinstance(models, list):
        raise SystemExit("FAIL: unexpected catalog shape; cannot pick a model.")
    if repo:
        for row in models:
            if row.get("repo") == repo:
                return (repo, row.get("family") or family or "")
        return (repo, family or "")
    if not family:
        raise SystemExit("FAIL: pass --family and/or --repo.")
    for row in models:
        if row.get("family") == family and row.get("hub_cached"):
            return (row["repo"], family)
    raise SystemExit(f"FAIL: no cached model for family {family!r} in the packaged catalog.")


def resolve_voice(base: str, token: str, family: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    status, availability, raw = http_json(base, token, "GET", "/studio/voice/api/generate/availability")
    if status >= 400:
        raise SystemExit(f"FAIL: voice availability -> HTTP {status}: {raw[:300]!r}")
    key = PRESET_KEY.get(family)
    if key is None:
        raise SystemExit(
            f"FAIL: {family!r} has no known preset-voice key; pass --voice or --voice-library-id."
        )
    options = availability.get(key) or []
    if not options:
        raise SystemExit(f"FAIL: availability[{key!r}] is empty; no preset voice to use.")
    first = options[0]
    if isinstance(first, dict):
        for name in ("id", "name", "voice"):
            if first.get(name):
                return str(first[name])
    if first:
        return str(first)
    raise SystemExit(f"FAIL: could not read a voice id from availability[{key!r}].")


def resolve_transcription_model(base: str, token: str, explicit: str | None) -> str:
    """The Hub packages transcription models by repo id, not by the worker's package_id."""
    if explicit:
        return explicit
    status, availability, raw = http_json(
        base, token, "GET", "/studio/subtitle/api/transcribe/availability")
    if status >= 400:
        raise SystemExit(
            f"FAIL: no local transcription route (subtitle availability HTTP {status}): {raw[:300]!r}")
    models = availability.get("models") or []
    default = availability.get("default_model")
    for row in models:
        if row.get("package_id") == default and row.get("ready"):
            return row["repo"]
    for row in models:
        if row.get("ready"):
            return row["repo"]
    raise SystemExit(
        "FAIL: no ready local Whisper model ("
        + ", ".join(sorted(str(r.get('repo')) for r in models))
        + "); refusing to guess a transcript."
    )


# ─────────────────────────────── voice job ───────────────────────────────


def submit_voice(base, token, repo, text, params) -> tuple[str | None, str | None]:
    body = {
        "modality": "voice",
        "model": repo,
        "client_request_id": f"measure-{uuid.uuid4().hex[:16]}",
        "items": [{"text": text, "params": params}],
    }
    status, parsed, raw = http_json(base, token, "POST", "/api/hub/jobs", body=body, timeout=180)
    if status >= 400:
        return None, f"HTTP {status}: {raw.decode('utf-8', 'replace')[:400]}"
    batch = parsed.get("batch_id") or parsed.get("id")
    if not batch:
        return None, f"no batch id in submit response: {raw[:200]!r}"
    return batch, None


def poll_voice(base, token, batch, timeout: float, interval: float) -> dict:
    deadline = time.time() + timeout
    last = {"state": "unknown"}
    while time.time() < deadline:
        time.sleep(interval)
        status, parsed, raw = http_json(base, token, "GET", f"/api/hub/jobs/{batch}", timeout=60)
        if status >= 400:
            last = {"state": "error", "error": f"poll HTTP {status}: {raw[:200]!r}"}
            break
        item = (parsed.get("items") or [{}])[0]
        note = str(parsed.get("governor_note") or "")
        error = str(item.get("error") or "")
        if note and note not in error:
            error = f"{error} {note}".strip()
        last = {
            "state": item.get("state") or "unknown",
            "error": error,
            "chunk_total": item.get("chunk_total"),
            "artifact_url": item.get("artifact_url"),
            "runtime_s": item.get("runtime_s"),
        }
        if last["state"] in TERMINAL:
            break
    return last


def download_artifact(base, token, batch, index, dest: pathlib.Path) -> str:
    status, parsed, raw = http_json(base, token, "GET", f"/api/hub/jobs/{batch}", timeout=60)
    if status >= 400:
        raise RuntimeError(f"cannot read batch {batch}: HTTP {status}")
    item = (parsed.get("items") or [{}])[index]
    artifact_url = item.get("artifact_url") or ""
    if not artifact_url:
        raise RuntimeError("job finished without an artifact_url")
    url = artifact_url if artifact_url.startswith("http") else base + artifact_url
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()
    if not data:
        raise RuntimeError("artifact was empty")
    dest.write_bytes(data)
    return str(dest)


def probe_override_field(base, token, repo, text, field, budget, base_params) -> dict:
    """One cheap submit using a candidate override field, to say WHY a budget was not applied.

    The Hub accepted the batch is not an answer: it queues every submit and the *worker* decides at
    dispatch time.  So this polls until the item either fails with the worker's reason or starts
    running; a run that starts is cancelled at once, so a diagnostic never leaves a real generation
    behind.  "refused" is the useful answer here - it is what a family with no section-size control
    returns for the public field.
    """
    params = {key: value for key, value in base_params.items() if not key.startswith("_")}
    params[field] = budget
    batch, error = submit_voice(base, token, repo, text, params)
    if error:
        return {"field": field, "budget": budget, "verdict": "hub-refused", "detail": error}
    deadline = time.time() + 90.0
    state, detail = "queued", "no dispatch within 90s"
    while time.time() < deadline:
        time.sleep(3)
        status, parsed, raw = http_json(base, token, "GET", f"/api/hub/jobs/{batch}", timeout=60)
        if status >= 400:
            state, detail = "poll-error", f"HTTP {status}: {raw[:200]!r}"
            break
        item = (parsed.get("items") or [{}])[0]
        note = str(parsed.get("governor_note") or "")
        state = item.get("state") or "unknown"
        detail = str(item.get("error") or "") or note or "no error reported"
        if state in TERMINAL or state == "running":
            break
    http(base, token, "DELETE", f"/api/hub/jobs/{batch}", timeout=30)
    if state == "error":
        return {"field": field, "budget": budget, "verdict": "refused",
                "detail": detail[:400], "batch": batch}
    if state in ("running", "done"):
        return {"field": field, "budget": budget, "verdict": "accepted",
                "detail": "the worker started this budget; cancelled at once",
                "batch": batch}
    return {"field": field, "budget": budget, "verdict": state, "detail": detail[:400], "batch": batch}



# ─────────────────────────────── transcription job ───────────────────────────────


def submit_transcription(base, token, wav: pathlib.Path, model: str, language: str,
                          timeout: float = 300.0) -> tuple[str | None, str | None]:
    boundary = "----measure" + uuid.uuid4().hex
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    chunks.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
         f"filename=\"{wav.name}\"\r\nContent-Type: audio/wav\r\n\r\n").encode())
    chunks.append(wav.read_bytes())
    chunks.append(b"\r\n")
    field("item_ids", f"measure-{uuid.uuid4().hex[:12]}")
    field("model", model)
    field("language", language)
    field("word_timestamps", "false")
    chunks.append(f"--{boundary}--\r\n".encode())
    data = b"".join(chunks)
    status, raw = http(
        base, token, "POST", "/api/hub/transcription/jobs", body=data,
        content_type=f"multipart/form-data; boundary={boundary}", timeout=timeout)
    if status >= 400:
        return None, f"HTTP {status}: {raw.decode('utf-8', 'replace')[:400]}"
    body = json.loads(raw or b"{}")
    batch = body.get("batch_id") or body.get("id")
    if not batch:
        return None, f"no batch id in transcription response: {raw[:200]!r}"
    return batch, None


def _srt_to_text(srt: str) -> str:
    out = []
    for line in srt.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        out.append(stripped)
    return " ".join(out)


def transcribe_wav(base, token, wav: pathlib.Path, model: str, language: str,
                   timeout: float, interval: float) -> str:
    batch, error = submit_transcription(base, token, wav, model, language)
    if error:
        raise RuntimeError(f"transcription submit failed: {error}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        status, parsed, raw = http_json(
            base, token, "GET", f"/api/hub/transcription/jobs/{batch}", timeout=60)
        if status >= 400:
            raise RuntimeError(f"transcription poll HTTP {status}: {raw[:200]!r}")
        item = (parsed.get("items") or [{}])[0]
        state = item.get("state")
        if state in TERMINAL:
            if state != "done":
                raise RuntimeError(f"transcription {state}: {item.get('error')}")
            text = str((item.get("metadata") or {}).get("text") or "").strip()
            if text:
                return text
            status, raw = http(
                base, token, "GET", f"/api/hub/transcription/jobs/{batch}/items/0/artifact",
                timeout=60)
            if status < 400:
                text = _srt_to_text(raw.decode("utf-8", "replace")).strip()
            if not text:
                raise RuntimeError("transcription finished but returned no text")
            return text
    raise RuntimeError("transcription timed out")


# ─────────────────────────────── coverage ───────────────────────────────


def words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def coverage(source: str, transcript: str):
    """Bag-of-words coverage: how many source words the transcript actually contains."""
    src = words(source)
    heard = Counter(words(transcript))
    missing: list[str] = []
    matched = 0
    for word in src:
        if heard.get(word, 0) > 0:
            heard[word] -= 1
            matched += 1
        else:
            missing.append(word)
    percent = (100.0 * matched / len(src)) if src else 0.0
    return percent, matched, missing


# ─────────────────────────────── worker-log section counting ───────────────────────────────


def log_marker(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def log_sections(path: pathlib.Path, offset: int, family: str) -> tuple[int | None, str]:
    """Count the sections the worker printed after `offset`, as a second opinion to chunk_total."""
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            window = handle.read().decode("utf-8", "replace")
    except OSError:
        return None, ""
    joined = re.findall(rf"\[gen\] {re.escape(family)} joined (\d+) stable sections", window)
    if joined:
        return int(joined[-1]), window
    per = re.findall(rf"\[gen\] {re.escape(family)} stable section (\d+)/(\d+)", window)
    if per:
        return int(per[-1][1]), window
    return None, window


# ─────────────────────────────── main ───────────────────────────────


def parse_budgets(raw: str) -> list[int]:
    out = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            value = int(piece)
        except ValueError:
            raise SystemExit(f"FAIL: --budgets contains a non-integer: {piece!r}")
        if value <= 0:
            raise SystemExit(f"FAIL: --budgets values must be positive: {value}")
        out.append(value)
    if not out:
        raise SystemExit("FAIL: --budgets is empty")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--family", default=None, help="family id, e.g. voxtral-tts")
    parser.add_argument("--repo", default=None, help="exact model repo (defaulted from --family)")
    parser.add_argument("--voice", default=None, help="preset voice id (defaulted from availability)")
    parser.add_argument("--voice-library-id", default=None,
                        help="saved voice id, for clone families with no preset voice")
    parser.add_argument("--budgets", default="200,280,350,500,800",
                        help="comma-separated section budgets, largest last")
    parser.add_argument("--text-file", required=True, help="the one script to render at every budget")
    parser.add_argument("--out-dir", default="/tmp/voxtral-measure")
    parser.add_argument("--language", default="en")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--transcription-model", default=None,
                        help="Whisper repo id (defaulted from subtitle availability)")
    parser.add_argument("--override-field", default="_resolved_section_max_characters",
                        help="payload field carrying the forced budget")
    parser.add_argument("--poll-timeout", type=float, default=1800.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--transcribe-timeout", type=float, default=1800.0)
    parser.add_argument("--worker-log", default=str(DEFAULT_WORKER_LOG),
                        help="worker log used to cross-check the rendered section count")
    parser.add_argument("--diagnose-only", action="store_true",
                        help="only probe whether the public section_max_characters field is "
                             "accepted for this family, then exit")
    args = parser.parse_args()

    budgets = parse_budgets(args.budgets)
    text_path = pathlib.Path(args.text_file)
    if not text_path.is_file():
        raise SystemExit(f"FAIL: --text-file {text_path} does not exist")
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"FAIL: --text-file {text_path} is empty")
    chars = len(text)

    base, token = load_hub()
    repo, family = resolve_model(base, token, args.family, args.repo)
    family = family or args.family or ""
    if args.voice_library_id:
        voice_label = f"library:{args.voice_library_id}"
        voice_value = None
    else:
        voice_value = resolve_voice(base, token, family, args.voice)
        voice_label = voice_value
    transcription_model = resolve_transcription_model(base, token, args.transcription_model)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_log = pathlib.Path(args.worker_log)

    print(f"family={family} repo={repo} voice={voice_label}")
    print(f"script={text_path} chars={chars} words={len(words(text))} "
          f"seed={args.seed} language={args.language} speed={args.speed}")
    print(f"transcription={transcription_model} out={out_dir}")
    print(f"override field={args.override_field} budgets={budgets}\n")

    rows: list[dict] = []
    any_coverage = False
    transcription_broken = False
    base_params: dict = {"language": args.language, "speed": args.speed, "seed": args.seed}
    if voice_value is not None:
        base_params["preset_speaker"] = voice_value
    if args.voice_library_id:
        base_params["voice_library_id"] = args.voice_library_id

    if args.diagnose_only:
        probe_budget = min(budgets)
        print(json.dumps(probe_override_field(
            base, token, repo, text, "section_max_characters", probe_budget, base_params), indent=2))
        return 0

    for budget in budgets:
        print(f"[budget {budget}] submitting...", flush=True)
        marker = log_marker(work_log)
        params: dict = dict(base_params)
        params[args.override_field] = budget

        started = time.time()
        batch, error = submit_voice(base, token, repo, text, params)
        if error:
            row = {"budget": budget, "sections": None, "chars": chars, "wall_s": None,
                   "coverage": None, "lost": None, "missing": [], "state": "failed",
                   "note": error}
            rows.append(row)
            print(f"[budget {budget}] FAILED to submit: {error}\n", flush=True)
            continue

        job = poll_voice(base, token, batch, args.poll_timeout, args.poll_interval)
        wall = time.time() - started
        observed_log, _ = log_sections(work_log, marker, family) if work_log.is_file() else (None, "")
        sections = job.get("chunk_total")
        if sections is None and observed_log is not None:
            sections = observed_log
        if sections is None and job["state"] == "done":
            # One call is the absence of the section path; the worker prints the whole text length
            # in that single call. Distinguish it from a genuine multi-section render below.
            sections = 1

        if job["state"] != "done":
            row = {"budget": budget, "sections": sections, "chars": chars, "wall_s": round(wall, 1),
                   "coverage": None, "lost": None, "missing": [], "state": job["state"],
                   "note": job.get("error") or "", "batch": batch}
            rows.append(row)
            print(f"[budget {budget}] {job['state'].upper()}: {row['note'][:300]}\n", flush=True)
            continue

        wav = out_dir / f"{family or 'voice'}-b{budget}.wav"
        try:
            download_artifact(base, token, batch, 0, wav)
        except Exception as exc:  # artifact must exist; never guess
            row = {"budget": budget, "sections": sections, "chars": chars, "wall_s": round(wall, 1),
                   "coverage": None, "lost": None, "missing": [], "state": "artifact-error",
                   "note": str(exc), "batch": batch}
            rows.append(row)
            print(f"[budget {budget}] artifact download failed: {exc}\n", flush=True)
            continue

        try:
            transcript = transcribe_wav(base, token, wav, transcription_model, args.language,
                                        args.transcribe_timeout, args.poll_interval)
        except Exception as exc:
            transcription_broken = True
            row = {"budget": budget, "sections": sections, "chars": chars, "wall_s": round(wall, 1),
                   "coverage": None, "lost": None, "missing": [], "state": "transcribe-error",
                   "note": str(exc), "batch": batch, "wav": str(wav)}
            rows.append(row)
            print(f"[budget {budget}] TRANSCRIPTION FAILED: {exc}\n", flush=True)
            continue

        text_out = out_dir / f"{family or 'voice'}-b{budget}.txt"
        text_out.write_text(transcript, encoding="utf-8")
        percent, matched, missing = coverage(text, transcript)
        any_coverage = True
        row = {"budget": budget, "sections": sections, "chars": chars, "wall_s": round(wall, 1),
               "coverage": percent, "lost": len(missing), "missing": missing, "state": "done",
               "note": "", "batch": batch, "wav": str(wav), "transcript": str(text_out),
               "runtime_s": job.get("runtime_s")}
        rows.append(row)
        print(f"[budget {budget}] sections={sections} wall={wall:.1f}s "
              f"coverage={percent:.1f}% lost={len(missing)}\n", flush=True)

    # ── report ──
    print("=" * 78)
    header = f"{'budget':>7}  {'sections':>8}  {'chars':>6}  {'wall_s':>7}  {'coverage%':>9}  {'lost':>4}"
    print(header)
    print("-" * len(header))
    for row in rows:
        sections = "-" if row["sections"] is None else str(row["sections"])
        wall = "-" if row["wall_s"] is None else f"{row['wall_s']:.1f}"
        percent = "-" if row["coverage"] is None else f"{row['coverage']:.1f}"
        lost = "-" if row["lost"] is None else str(row["lost"])
        print(f"{row['budget']:>7}  {sections:>8}  {row['chars']:>6}  {wall:>7}  {percent:>9}  {lost:>4}")
        if row["lost"] is not None and 0 < row["lost"] <= 12:
            print(f"{'':>7}  missing: {', '.join(row['missing'])}")
        if row["state"] not in ("done",):
            print(f"{'':>7}  {row['state']}: {row['note'][:220]}")

    measured = [row for row in rows if row["coverage"] is not None]
    failed = [row for row in rows if row["coverage"] is None]
    splittable = [row for row in rows if row["budget"] < chars]
    override_ignored = (
        bool(splittable)
        and all(row.get("sections") == 1 for row in splittable)
        and all(row["state"] == "done" for row in splittable)
    )

    diagnostic: list[dict] = []
    if not any_coverage:
        recommendation = ("NOT MEASURABLE - no budget produced a transcript; "
                          "see the row notes above.")
        exit_code = 3
    elif override_ignored:
        smallest = min(row["budget"] for row in splittable)
        recommendation = ("NOT MEASURABLE in this build - the section budget is not applied for "
                          "this family, so no ceiling can be named.")
        exit_code = 2
        for alternative in ("section_max_characters",):
            if alternative != args.override_field:
                diagnostic.append(probe_override_field(
                    base, token, repo, text, alternative, smallest, base_params))
    else:
        best_lost = min(row["lost"] for row in measured)
        acceptable = [row for row in measured if row["lost"] <= best_lost + 1]
        largest_ok = max(row["budget"] for row in acceptable)
        largest_measured = max(row["budget"] for row in measured)
        best_percent = max(row["coverage"] for row in measured)
        lost_at_largest = next(row["lost"] for row in measured if row["budget"] == largest_ok)
        if largest_ok == largest_measured and lost_at_largest <= 1:
            recommendation = (f"at least {largest_ok}, not yet limited - coverage was still "
                              f"{best_percent:.1f}% ({lost_at_largest} word(s) lost) at the "
                              "largest measured budget.")
        elif largest_ok == largest_measured:
            recommendation = (f"the largest measured budget {largest_ok} is the best observed but "
                              f"already loses {lost_at_largest} word(s) ({best_percent:.1f}% "
                              "coverage), so the limit was reached at or before this size.")
        else:
            recommendation = (f"{largest_ok} characters keeps coverage within 1 word of the best "
                              f"observed ({best_percent:.1f}%, {lost_at_largest} word(s) lost); "
                              "larger budgets lose more.")
        exit_code = 0

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(
        {"family": family, "repo": repo, "voice": voice_label, "text_file": str(text_path),
         "chars": chars, "words": len(words(text)), "seed": args.seed,
         "transcription_model": transcription_model, "budgets": budgets, "rows": rows,
         "override_ignored": override_ignored, "diagnostic": diagnostic,
         "recommendation": recommendation, "exit_code": exit_code}, indent=2), encoding="utf-8")

    print()
    if failed:
        print(f"ANOMALY: {len(failed)} of {len(rows)} budgets did not yield a coverage number "
              f"({', '.join(str(r['budget']) for r in failed)}).")
    if not any_coverage:
        print("NOT MEASURED: no budget produced a transcript. No coverage is reported.")
    elif override_ignored:
        smallest = min(row["budget"] for row in splittable)
        largest = max(row["budget"] for row in splittable)
        print(f"OVERRIDE IGNORED: every budget below the script length ({chars} chars) rendered "
              f"exactly one call, from {smallest} to {largest}. The worker did not apply "
              f"{args.override_field!r}; the coverage above describes the CURRENT whole-script "
              "behaviour, not the requested budgets.")
    for entry in diagnostic:
        print(f"diagnostic: {entry['field']}={entry['budget']} -> {entry['detail'][:220]}")
    print(f"recommendation: {recommendation}")
    if transcription_broken:
        print("note: at least one budget could not be transcribed; its coverage is omitted.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
