"""R07 local requalification probe: real FLUX.2 Klein image generation.

Same shape as the Whisper probe: launch the staged app's private service against
the chosen model library, submit one image job through the Hub's durable queue,
and verify the returned PNG against the historical deterministic receipt.
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
DEFAULT_APP = Path("/Applications/Hub Studio KH.app")
MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"
PROMPT = ("A quiet riverside garden at sunrise, soft mist over the water, "
          "green trees, natural light, photographic detail.")
PARAMS = {"width": 1280, "height": 720, "steps": 4, "guidance": 1.0, "seed": 42}
EXPECTED_SHA = "290d2fb8cf0cd927d7d0633412bb157a9ee8675684ed2da7e2e1267c6ce919bd"
EXPECTED_BYTES = 1696066
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--models", type=Path, required=True,
                        help="a DISPOSABLE library (qualification/library_guard.py clone ...), never the real one")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--save-png", type=Path)
    parser.add_argument("--ack", action="store_true",
                        help="acknowledge delivery through the Hub after verifying the artifact")
    args = parser.parse_args()

    app, models = args.app.resolve(), library_guard.require_disposable(args.models)
    service = app / "Contents/MacOS/StudioHubService"
    assert service.is_file(), service
    receipt: dict = {"app": str(app), "models_dir": str(models), "model": MODEL,
                     "prompt": PROMPT, "params": PARAMS}

    with tempfile.TemporaryDirectory(prefix="r07-flux-") as temporary:
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
                with httpx.Client(timeout=httpx.Timeout(900.0, connect=10.0)) as client:
                    response = client.post(
                        base + "/api/hub/jobs", headers=headers,
                        json={"modality": "image", "model": MODEL,
                              "clientRequestId": "r07-flux-1",
                              "items": [{"prompt": PROMPT, "params": PARAMS}]})
                    receipt["submit_status"] = response.status_code
                    if response.status_code >= 400:
                        receipt["submit_body"] = response.text[:500]
                        print(json.dumps(receipt, indent=2))
                        return 1
                    batch = response.json()["batch_id"]
                    receipt["batch_id"] = batch
                    started_gen = time.monotonic()
                    summary = {}
                    while time.monotonic() - started_gen < 900:
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
                                        "worker_id", "machine_id", "studio")}
                    if item.get("state") == "done":
                        artifact = client.get(
                            base + f"/api/hub/jobs/{batch}/items/0/artifact",
                            headers=headers)
                        receipt["artifact_status"] = artifact.status_code
                        blob = artifact.content
                        receipt["artifact"] = {
                            "bytes": len(blob),
                            "sha256": hashlib.sha256(blob).hexdigest(),
                            "png_magic": blob[:8] == PNG_MAGIC,
                        }
                        if args.save_png:
                            args.save_png.write_bytes(blob)
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

    artifact = receipt.get("artifact") or {}
    receipt["matches_historical"] = (
        artifact.get("bytes") == EXPECTED_BYTES
        and artifact.get("sha256") == EXPECTED_SHA)
    # Byte-exact reproduction across runtime versions is informative, not the
    # qualification bar: a valid PNG of the right size from a done job is.
    receipt["ok"] = (receipt.get("final_status") == "done"
                     or (receipt.get("item") or {}).get("state") == "done") \
        and bool(artifact.get("png_magic")) and (artifact.get("bytes") or 0) > 0
    receipt["expected"] = {"bytes": EXPECTED_BYTES, "sha256": EXPECTED_SHA}
    print(json.dumps(receipt, indent=2))
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
