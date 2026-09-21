"""R07 local requalification probe: real ACE-Step 1.5 Turbo music generation.

Submits one music job through the Hub's durable queue using the model package's
own advertised defaults and reports the WAV artifact. Model-free checks live in
the packaging probes; this one loads the real model.
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
DEFAULT_APP = ROOT / ".build/deepseek-proof-r06/Studio Hub.app"
MODEL = "ACE-Step/Ace-Step1.5"
PROMPT = "Warm lo-fi hip hop beat, mellow piano, vinyl crackle, late night study mood"


def riff_wave(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--models", type=Path, default=Path.home() / "StudioHubModels")
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--save-wav", type=Path)
    parser.add_argument("--log-out", type=Path)
    args = parser.parse_args()

    app, models = args.app.resolve(), args.models.resolve()
    service = app / "Contents/MacOS/StudioHubService"
    assert service.is_file(), service
    params = {"duration_seconds": args.duration, "steps": 8, "guidance_scale": 1.0,
              "shift": 3.0, "seed": -1, "instrumental": True,
              "lyrics": "[Instrumental]", "vocal_language": "unknown"}
    receipt: dict = {"app": str(app), "models_dir": str(models), "model": MODEL,
                     "prompt": args.prompt, "params": params}

    with tempfile.TemporaryDirectory(prefix="r07-music-") as temporary:
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
                with httpx.Client(timeout=httpx.Timeout(1800.0, connect=10.0)) as client:
                    response = client.post(
                        base + "/api/hub/jobs", headers=headers,
                        json={"modality": "music", "model": MODEL,
                              "client_request_id": "r07-music-1",
                              "items": [{"prompt": args.prompt, "params": params}]})
                    receipt["submit_status"] = response.status_code
                    if response.status_code >= 400:
                        receipt["submit_body"] = response.text[:500]
                        print(json.dumps(receipt, indent=2))
                        return 1
                    batch = response.json()["batch_id"]
                    receipt["batch_id"] = batch
                    started_gen = time.monotonic()
                    summary = {}
                    while time.monotonic() - started_gen < 1800:
                        summary = client.get(base + f"/api/hub/jobs/{batch}",
                                             headers=headers).json()
                        states = [item.get("state") for item in summary.get("items", [])]
                        if states and all(s in {"done", "error", "failed", "cancelled",
                                                "uncertain"} for s in states):
                            break
                        time.sleep(2.0)
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
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
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
    print(json.dumps(receipt, indent=2))
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
