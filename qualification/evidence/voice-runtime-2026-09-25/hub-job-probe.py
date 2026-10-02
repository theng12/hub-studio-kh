"""Exercise one Voice model through the shipped Hub bundle and store the receipt.

Written for the 2026-09-25 smoke item. `packaging/smoke_voice.py` runs a voice-only
artifact, which `packaging/stage_voice.py::_stage_voice` stages without `model-manifests/`,
so neither the Qwen clone-quality gate (which reads the model's own `requires_packages`)
nor the Whisper processor can resolve inside it. This probe uses the same recorded model,
reference clip, text and parameters through the hub-inclusive bundle the app ships, whose
voice worker does have the manifest tree, and writes a receipt beside this script.

Run it the way it was run:

    .build/voice-runtime/bin/python3 \
        qualification/evidence/voice-runtime-2026-09-25/hub-job-probe.py \
        --app ".build/StudioHub-0661/Studio Hub.app" \
        --models "$HOME/StudioHubModels" \
        --clip qualification/evidence/st04-reference-clip.wav \
        --output <receipt.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import httpx
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import library_guard  # noqa: E402


TEXT = "This is a standalone Voice Studio test. The bundled engine is speaking clearly."
PARAMS = {"preset_speaker": "", "language": "en", "seed": 42}
TRANSCRIPT = ("The river runs quietly past the old stone bridge, "
              "and the morning light settles on the water.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--clip", type=Path, required=True)
    parser.add_argument("--model", default="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-wav", type=Path)
    args = parser.parse_args()

    app, models, clip = args.app.resolve(), library_guard.require_disposable(args.models), args.clip.resolve()
    service = app / "Contents/MacOS/StudioHubService"
    assert service.is_file(), service
    audio = clip.read_bytes()
    receipt: dict = {"app": str(app), "models_dir": str(models), "model": args.model,
                     "text": TEXT, "params": PARAMS,
                     "reference": {"file": clip.name, "bytes": len(audio),
                                   "sha256": hashlib.sha256(audio).hexdigest(),
                                   "transcript": TRANSCRIPT}}
    with tempfile.TemporaryDirectory(prefix="smoke-params-") as temporary:
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
                    if time.monotonic() - started > 180:
                        raise SystemExit("service never became ready")
                    time.sleep(0.2)
                info = json.loads(ready.read_text())
                receipt["ready_seconds"] = round(time.monotonic() - started, 2)
                headers = {"Authorization": "Bearer " + info["token"]}
                base = info["url"]
                with httpx.Client(timeout=httpx.Timeout(1200.0, connect=10.0)) as client:
                    staged = client.post(
                        base + "/api/hub/voice-references", headers=headers,
                        data={"transcript": TRANSCRIPT, "language": "en"},
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
                              "client_request_id": "smoke-params-20260925",
                              "voice_reference_asset_id": asset["asset_id"],
                              "items": [{"text": TEXT, "params": PARAMS}]})
                    receipt["submit_status"] = response.status_code
                    if response.status_code >= 400:
                        receipt["submit_body"] = response.text[:500]
                        print(json.dumps(receipt, indent=2))
                        return 1
                    batch = response.json()["batch_id"]
                    receipt["batch_id"] = batch
                    started_gen = time.monotonic()
                    summary: dict = {}
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
                                        "error_code", "worker_id", "metadata")}
                    if item.get("state") == "done":
                        artifact = client.get(
                            base + f"/api/hub/jobs/{batch}/items/0/artifact", headers=headers)
                        receipt["artifact_status"] = artifact.status_code
                        blob = artifact.content
                        receipt["artifact"] = {
                            "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
                            "riff_wave": blob[:4] == b"RIFF" and blob[8:12] == b"WAVE",
                        }
                        if args.save_wav:
                            args.save_wav.write_bytes(blob)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
            if not receipt.get("ok") and not receipt.get("artifact"):
                receipt["service_log_tail"] = log_path.read_text(errors="replace")[-3000:]
    artifact = receipt.get("artifact") or {}
    receipt["ok"] = bool((receipt.get("item") or {}).get("state") == "done"
                         and artifact.get("riff_wave") and artifact.get("bytes"))
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ok": receipt["ok"], "model": receipt["model"],
                      "generate_seconds": receipt.get("generate_seconds"),
                      "state": (receipt.get("item") or {}).get("state"),
                      "error": (receipt.get("item") or {}).get("error"),
                      "bytes": artifact.get("bytes")}, indent=2))
    return 0 if receipt["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
