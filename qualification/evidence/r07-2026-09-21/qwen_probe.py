"""R07 local requalification probe: real Qwen3-TTS Base voice cloning.

Stages the project reference clip through the Hub's shared voice-reference
contract, submits a clone job through the durable queue, and verifies the WAV.
TTS sampling is not assumed deterministic, so validity and the worker's own
quality check are the evidence, not a fixed output hash.
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
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import library_guard  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_APP = Path("/Applications/Studio Hub.app")
DEFAULT_CLIP = ROOT / "qualification/evidence/st04-reference-clip.wav"
TRANSCRIPT = ("The river runs quietly past the old stone bridge, "
              "and the morning light settles on the water.")


def riff_wave(data: bytes) -> bool:
    return (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--models", type=Path, required=True,
                        help="a DISPOSABLE library (qualification/library_guard.py clone ...), never the real one")
    parser.add_argument("--clip", type=Path, default=DEFAULT_CLIP)
    parser.add_argument("--model", default="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit")
    parser.add_argument("--text", default=TRANSCRIPT)
    parser.add_argument("--no-transcript", action="store_true",
                        help="stage the reference with no transcript, to prove the "
                             "clone still works offline")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--save-wav", type=Path)
    parser.add_argument("--log-out", type=Path)
    parser.add_argument("--ack", action="store_true",
                        help="acknowledge delivery through the Hub after verifying the artifact")
    args = parser.parse_args()

    app, models, clip = args.app.resolve(), library_guard.require_disposable(args.models), args.clip.resolve()
    service = app / "Contents/MacOS/StudioHubService"
    assert service.is_file(), service
    audio = clip.read_bytes()
    receipt: dict = {"app": str(app), "models_dir": str(models), "model": args.model,
                     "text": args.text,
                     "reference": {"file": clip.name, "bytes": len(audio),
                                   "sha256": hashlib.sha256(audio).hexdigest(),
                                   "transcript": TRANSCRIPT}}

    with tempfile.TemporaryDirectory(prefix="r07-qwen-") as temporary:
        root = Path(temporary)
        data = root / "state"
        data.mkdir(mode=0o700)
        ready = data / "hub-ready.json"
        log_path = root / "service.log"
        service_log = ""
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
                with httpx.Client(timeout=httpx.Timeout(1200.0, connect=10.0)) as client:
                    staged = client.post(
                        base + "/api/hub/voice-references", headers=headers,
                        data={"transcript": "" if args.no_transcript else TRANSCRIPT,
                              "language": "en"},
                        files={"audio": (clip.name, audio, "audio/wav")})
                    receipt["stage_status"] = staged.status_code
                    if staged.status_code >= 400:
                        receipt["stage_body"] = staged.text[:500]
                        print(json.dumps(receipt, indent=2))
                        return 1
                    asset = staged.json()
                    receipt["asset"] = asset
                    response = client.post(
                        base + "/api/hub/jobs", headers=headers,
                        json={"modality": "voice", "model": args.model,
                              "client_request_id": "r07-qwen-1",
                              "voice_reference_asset_id": asset["asset_id"],
                              "items": [{"text": args.text,
                                         "params": {"preset_speaker": "",
                                                    "language": "en", "speed": 1.0}}]})
                    receipt["submit_status"] = response.status_code
                    if response.status_code >= 400:
                        receipt["submit_body"] = response.text[:500]
                        print(json.dumps(receipt, indent=2))
                        return 1
                    batch = response.json()["batch_id"]
                    receipt["batch_id"] = batch
                    started_gen = time.monotonic()
                    summary = {}
                    while time.monotonic() - started_gen < 1200:
                        summary = client.get(base + f"/api/hub/jobs/{batch}",
                                             headers=headers).json()
                        states = [item.get("state") for item in summary.get("items", [])]
                        if states and all(s in {"done", "error", "failed", "cancelled",
                                                "uncertain"} for s in states):
                            break
                        time.sleep(1.0)
                    receipt["generate_seconds"] = round(time.monotonic() - started_gen, 2)
                    receipt["final_status"] = summary.get("status")
                    item = (summary.get("items") or [{}])[0]
                    receipt["item"] = {k: item.get(k) for k in
                                       ("state", "bytes", "sha256", "media_type", "error",
                                        "error_code", "worker_id", "machine_id", "studio",
                                        "metadata")}
                    if item.get("state") == "done":
                        artifact = client.get(
                            base + f"/api/hub/jobs/{batch}/items/0/artifact",
                            headers=headers)
                        receipt["artifact_status"] = artifact.status_code
                        blob = artifact.content
                        receipt["artifact"] = {
                            "bytes": len(blob),
                            "sha256": hashlib.sha256(blob).hexdigest(),
                            "riff_wave": riff_wave(blob),
                        }
                        if args.save_wav:
                            args.save_wav.write_bytes(blob)
                        if args.ack:
                            ack = client.post(
                                base + f"/api/hub/jobs/{batch}/items/0/ack",
                                headers=headers)
                            receipt["ack_status"] = ack.status_code
                            receipt["ack_body"] = ack.text[:200]
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
            try:
                service_log = log_path.read_text(errors="replace")[-6000:]
            except OSError:
                service_log = ""
            if args.log_out:
                try:
                    args.log_out.write_text(log_path.read_text(errors="replace"))
                except OSError:
                    pass

    artifact = receipt.get("artifact") or {}
    receipt["ok"] = ((receipt.get("final_status") == "done"
                      or (receipt.get("item") or {}).get("state") == "done")
                     and bool(artifact.get("riff_wave"))
                     and (artifact.get("bytes") or 0) > 0)
    if not receipt["ok"] and service_log:
        receipt["service_log_tail"] = service_log
    print(json.dumps(receipt, indent=2))
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
