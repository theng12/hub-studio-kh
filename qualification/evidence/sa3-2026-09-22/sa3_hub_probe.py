"""Submit a Stable Audio 3 job to the running Studio Hub app and verify the result.

Unlike the R07 probes this does not start its own service: it talks to the
installed app's Hub through its ready file, so it proves the packaged app path
(Hub -> Music worker -> vendored MLX runtime -> imported package weights).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import wave

import httpx

DEFAULT_READY = (Path.home() / "Library" / "Application Support"
                 / "StudioHub Standalone" / "hub-ready.json")
MODEL = "stabilityai/stable-audio-3-optimized"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", type=Path, default=DEFAULT_READY)
    parser.add_argument("--prompt", default=(
        "Bright cinematic piano and strings, uplifting, wide reverb, instrumental"))
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-wav", type=Path)
    args = parser.parse_args()

    info = json.loads(args.ready.read_text())
    base, token = info["url"], info["token"]
    headers = {"Authorization": "Bearer " + token}
    receipt: dict = {"probe": "stable-audio-3-hub", "model": MODEL,
                     "prompt": args.prompt, "duration_seconds": args.duration,
                     "hub": base}
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(1800.0, connect=10.0)) as client:
        response = client.post(base + "/api/hub/jobs", headers=headers, json={
            "modality": "music", "model": MODEL,
            "client_request_id": f"sa3-hub-{int(time.time())}",
            "items": [{"prompt": args.prompt,
                       "params": {"duration_seconds": args.duration, "steps": 8,
                                  "seed": 11, "repo": MODEL}}]})
        response.raise_for_status()
        batch = response.json()["batch_id"]
        receipt["batch_id"] = batch
        progress_trace: list = []
        while True:
            detail = client.get(base + f"/api/hub/jobs/{batch}", headers=headers).json()
            item = (detail.get("items") or [{}])[0]
            marker = [item.get("progress"), item.get("progress_stage")]
            if not progress_trace or progress_trace[-1] != marker:
                progress_trace.append(marker)
            state = item.get("state")
            if state in {"done", "error", "cancelled", "uncertain"}:
                break
            if time.monotonic() - started > 1200:
                raise SystemExit("timed out waiting for the job")
            time.sleep(2.0)
        receipt["progress_trace"] = progress_trace
        receipt["state"] = state
        receipt["error"] = item.get("error")
        receipt["error_code"] = item.get("error_code")
        if state == "done":
            artifact = client.get(base + item["artifact_url"], headers=headers)
            artifact.raise_for_status()
            payload = artifact.content
            receipt["artifact"] = {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "reported_sha256": item.get("sha256"),
                "matches_reported": hashlib.sha256(payload).hexdigest() == item.get("sha256"),
            }
            if args.save_wav:
                args.save_wav.write_bytes(payload)
                with wave.open(str(args.save_wav), "rb") as handle:
                    receipt["artifact"].update(
                        duration_s=round(handle.getnframes() / handle.getframerate(), 3),
                        sample_rate_hz=handle.getframerate(),
                        channels=handle.getnchannels())
    receipt["wall_seconds"] = round(time.monotonic() - started, 2)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 0 if receipt.get("state") == "done" else 2


if __name__ == "__main__":
    sys.exit(main())
