#!/usr/bin/env python3
r"""Give each long voice clip in the kit the timings a Qwen Base clone needs.

A Qwen3-TTS Base model can only use fifteen seconds of reference audio at a time. A clip longer
than that is not rejected outright: the voice engine picks a bounded, speech-aligned window, and to
do that it needs the transcript **with timings**, so it knows which words fall inside the window.
Given only plain words it refuses, with

    REFERENCE_TRANSCRIPT_ALIGNMENT_REQUIRED: This model needs a transcript matching the selected
    reference segment. Supply timestamped transcript segments.

Measured on the owner's own voices on 2026-09-17: seven of ten are between 15.4 s and 22.2 s, so
most of the library could not be cloned without transcribing each clip by hand first.

The app can do that (Transcribe Clip keeps its timings now), and this tool does it once, at kit
build time, so every Mac that receives the kit can press Prepare Voice and have it work. It writes
`transcript.timed.json` beside the clip's `transcript.txt`, which the app reads when it has no
fresher timings of its own.

    python3 tools/time_kit_voices.py --voices /Volumes/KH-STUDIO-ATLAS/Studio\ Hub\ App/voices \\
        --app "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/Studio Hub.app" \\
        --models "$HOME/StudioHubModels"

It starts the app's own Hub, transcribes each long clip with the app's own Whisper, writes the
result, and stops. Clips already short enough are left alone; none of the audio is modified.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMED_NAME = "transcript.timed.json"
# The engine's own maximum for a Qwen3-TTS Base reference, from the audited contract. A clip at or
# under this needs no window selection, so it needs no timings.
MAX_REFERENCE_SECONDS = 15.0


sys.path.insert(0, str(Path(__file__).resolve().parent))
import library_guard  # noqa: E402

def reference_clip(folder: Path) -> Path | None:
    return next((p for p in sorted(folder.iterdir()) if p.stem == "reference"), None)


def read_metadata(folder: Path) -> dict:
    try:
        return json.loads((folder / "metadata.json").read_text())
    except (OSError, ValueError):
        return {}


def http_json(url: str, token: str, payload: dict | None = None, timeout: float = 900.0):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-Hub-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def transcribe_multipart(base: str, token: str, clip: Path, model: str) -> dict:
    boundary = "----timing"
    parts = []
    for name, value in (("model", model), ("word_timestamps", "true")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f"{value}\r\n".encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="{clip.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                 + clip.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{base}/studio/transcription/api/transcribe", data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Hub-Token": token})
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read())


def start_hub(app: Path, models: Path, data: Path, ready: Path) -> subprocess.Popen:
    runtime = app / "Contents/Resources/runtimes/voice/bin/python3"
    worker = app / "Contents/Resources/hub_worker.py"
    if not runtime.is_file() or not worker.is_file():
        raise SystemExit(f"no Hub inside {app}")
    process = subprocess.Popen(
        [str(runtime), str(worker), "--data-dir", str(data), "--models-dir", str(models),
         "--ready-file", str(ready), "--host", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(180):
        if ready.is_file() and ready.stat().st_size:
            return process
        if process.poll() is not None:
            raise SystemExit("the Hub exited while starting")
        time.sleep(1)
    process.terminate()
    raise SystemExit("the Hub did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voices", type=Path, required=True, help="the kit's voices folder")
    parser.add_argument("--app", type=Path, required=True, help="a staged Studio Hub.app")
    parser.add_argument("--models", type=Path, required=True, help="the model library")
    parser.add_argument("--model", default="whisper-turbo", help="transcription package to use")
    parser.add_argument("--force", action="store_true", help="re-time clips already timed")
    args = parser.parse_args()

    if not args.voices.is_dir():
        raise SystemExit(f"no voices folder at {args.voices}")
    folders = [f for f in sorted(args.voices.iterdir())
               if f.is_dir() and (f / "metadata.json").is_file() and reference_clip(f)]
    long_clips = [f for f in folders
                  if float(read_metadata(f).get("duration_seconds") or 0) > MAX_REFERENCE_SECONDS]
    pending = [f for f in long_clips
               if args.force or not (f / TIMED_NAME).is_file()]
    print(f"{len(folders)} voice(s); {len(long_clips)} longer than "
          f"{MAX_REFERENCE_SECONDS:g}s; {len(pending)} to time")
    if not pending:
        print("nothing to do")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        data, ready = Path(tmp), Path(tmp) / "ready.json"
        # Never hand the real library to a Hub that has a throwaway data directory (see library_guard).
        models = library_guard.clone(args.models, data / "library", [args.model])
        process = start_hub(args.app, models, data, ready)
        try:
            info = json.loads(ready.read_text())
            base, token = info["url"], info["token"]
            for folder in pending:
                meta = read_metadata(folder)
                clip = reference_clip(folder)
                print(f"  {meta.get('name', folder.name)[:44]:<46}", end="", flush=True)
                try:
                    result = transcribe_multipart(base, token, clip, args.model)
                except urllib.error.HTTPError as error:
                    print(f" FAILED: {error.read().decode()[:120]}")
                    continue
                segments = [{"start_seconds": s.get("start"), "end_seconds": s.get("end"),
                             "text": s.get("text")}
                            for s in (result.get("segments") or [])
                            if s.get("end") is not None and s.get("start") is not None]
                words = (result.get("text") or "").strip()
                if not segments:
                    print(" FAILED: the transcriber returned no timings")
                    continue
                if not words:
                    print(" FAILED: the transcriber returned no words")
                    continue
                (folder / TIMED_NAME).write_text(json.dumps(
                    {"transcript": words, "segments": segments, "model": result.get("model"),
                     "package_id": result.get("package_id"), "timed_at": time.time()},
                    indent=2, sort_keys=True) + "\n")
                print(f" {len(segments)} segment(s), {len(words)} characters")
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
