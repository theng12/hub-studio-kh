"""R07 local requalification probe: real Whisper Turbo transcription.

Launches the staged app's private service with the chosen model library, submits
one real transcription through the Hub's own durable route, and reports the
transcript. No network and no GUI; disposable state under a temp directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_APP = Path("/Applications/Studio Hub.app")
DEFAULT_CLIP = ROOT / "qualification/evidence/st04-reference-clip.wav"
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
EXPECTED = ("The river runs quietly past the old stone bridge, "
            "and the morning light settles on the water.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--models", type=Path, default=Path.home() / "StudioHubModels")
    parser.add_argument("--clip", type=Path, default=DEFAULT_CLIP)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ack", action="store_true",
                        help="verify the artifact and acknowledge delivery through the Hub")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app, models, clip = args.app.resolve(), args.models.resolve(), args.clip.resolve()
    service = app / "Contents/MacOS/StudioHubService"
    assert service.is_file(), service
    assert models.is_dir(), models
    assert clip.is_file(), clip
    audio = clip.read_bytes()
    receipt: dict = {
        "app": str(app),
        "models_dir": str(models),
        "model": args.model,
        "input": {"file": clip.name, "bytes": len(audio),
                  "sha256": hashlib.sha256(audio).hexdigest()},
    }

    with tempfile.TemporaryDirectory(prefix="r07-whisper-") as temporary:
        root = Path(temporary)
        data = root / "state"
        data.mkdir(mode=0o700)
        ready = data / "hub-ready.json"
        log_path = root / "service.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [str(service), "--data-dir", str(data), "--models-dir", str(models),
                 "--ready-file", str(ready), "--parent-pid", str(os.getpid()),
                 "--host", "127.0.0.1", "--port", "0", "--offline"],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                started = time.monotonic()
                while not ready.exists():
                    if process.poll() is not None:
                        raise SystemExit("service exited:\n" + log_path.read_text()[-3000:])
                    if time.monotonic() - started > 120:
                        raise SystemExit("service never became ready")
                    time.sleep(0.2)
                info = json.loads(ready.read_text())
                receipt["ready_seconds"] = round(time.monotonic() - started, 2)
                headers = {"Authorization": "Bearer " + info["token"]}
                base = info["url"]
                with httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
                    workers = client.get(base + "/api/hub/workers", headers=headers).json()["workers"]
                    receipt["workers"] = sorted(row["modality"] for row in workers)
                    response = client.post(
                        base + "/api/hub/transcription/jobs", headers=headers,
                        data={"item_ids": "clip-1", "model": args.model, "word_timestamps": "false"},
                        files={"files": (clip.name, audio, "audio/wav")})
                    receipt["submit_status"] = response.status_code
                    response.raise_for_status()
                    batch = response.json()["batch_id"]
                    receipt["batch_id"] = batch
                    deadline = time.monotonic() + 600
                    summary = {}
                    while time.monotonic() < deadline:
                        summary = client.get(
                            base + f"/api/hub/transcription/jobs/{batch}",
                            headers=headers).json()
                        states = [item.get("state") for item in summary.get("items", [])]
                        if states and all(s in {"done", "error", "failed", "cancelled",
                                                "uncertain"} for s in states):
                            break
                        time.sleep(1.0)
                    receipt["final_status"] = summary.get("status")
                    receipt["item"] = (summary.get("items") or [{}])[0]
                    artifact = client.get(
                        base + f"/api/hub/transcription/jobs/{batch}/items/0/artifact",
                        headers=headers)
                    receipt["artifact_status"] = artifact.status_code
                    receipt["srt"] = artifact.text if artifact.status_code == 200 else None
                    if args.ack and artifact.status_code == 200:
                        ack = client.post(
                            base + f"/api/hub/transcription/jobs/{batch}/items/0/ack",
                            headers=headers)
                        receipt["ack_status"] = ack.status_code
                        receipt["ack_body"] = ack.text[:200]
                        after = client.get(
                            base + f"/api/hub/transcription/jobs/{batch}",
                            headers=headers).json()
                        receipt["receipt_acked_at"] = (
                            (after.get("items") or [{}])[0].get("receipt_acked_at"))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()

    transcript_text = ""
    if receipt.get("srt"):
        transcript_text = " ".join(
            line for line in receipt["srt"].splitlines()
            if line and "-->" not in line and line.strip() != "WEBVTT")
    receipt["transcript_text"] = transcript_text
    receipt["expected"] = EXPECTED
    # Base is smaller and may differ in capitalisation/punctuation; a word-level
    # comparison records that it transcribed correctly without pretending verbatim.
    import re as _re

    def _normalise(value: str) -> str:
        return _re.sub(r"[^a-z0-9 ]", "", value.lower())

    receipt["matches_verbatim"] = EXPECTED.rstrip(".") in transcript_text
    receipt["matches"] = _normalise(EXPECTED) in _normalise(transcript_text)

    print(json.dumps(receipt, indent=2))
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt.get("matches") else 2


if __name__ == "__main__":
    sys.exit(main())
