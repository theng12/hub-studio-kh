"""Measure cold vs warm ACE-Step generation on one resident engine.

The app keeps the Music engine child resident between jobs, so a real session pays
the model load once. This starts one service and runs the same short job twice,
recording each wall time: the first includes the load, the second is the marginal
cost that would actually repeat.
"""
from __future__ import annotations

import argparse
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
APP = Path("/Applications/Studio Hub.app")
MODEL = "ACE-Step/Ace-Step1.5"
PROMPT = "Warm lo-fi hip hop beat, mellow piano, vinyl crackle"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=APP)
    parser.add_argument("--models", type=Path, required=True,
                        help="a DISPOSABLE library (qualification/library_guard.py clone ...), never the real one")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    app, models = args.app.resolve(), library_guard.require_disposable(args.models)
    params = {"duration_seconds": args.duration, "steps": 8, "guidance_scale": 1.0,
              "shift": 3.0, "seed": -1, "instrumental": True,
              "lyrics": "[Instrumental]", "vocal_language": "unknown"}
    receipt: dict = {"probe": "warm-timing", "duration": args.duration,
                     "repeat": args.repeat, "runs": []}
    with tempfile.TemporaryDirectory(prefix="r07-warm-") as temporary:
        root = Path(temporary)
        data = root / "state"
        data.mkdir(mode=0o700)
        ready = data / "hub-ready.json"
        log = (root / "service.log").open("w")
        process = subprocess.Popen(
            [str(app / "Contents/MacOS/StudioHubService"), "--data-dir", str(data),
             "--models-dir", str(models), "--ready-file", str(ready),
             "--parent-pid", str(os.getpid()), "--host", "127.0.0.1", "--port", "0",
             "--offline"],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            started = time.monotonic()
            while not ready.exists():
                if process.poll() is not None:
                    raise SystemExit("service exited:\n" + (root / "service.log").read_text()[-2000:])
                if time.monotonic() - started > 120:
                    raise SystemExit("service never became ready")
                time.sleep(0.2)
            info = json.loads(ready.read_text())
            headers = {"Authorization": "Bearer " + info["token"]}
            base = info["url"]
            receipt["ready_seconds"] = round(time.monotonic() - started, 2)
            with httpx.Client(timeout=httpx.Timeout(1200.0, connect=10.0)) as client:
                for index in range(args.repeat):
                    submitted = time.monotonic()
                    response = client.post(
                        base + "/api/hub/jobs", headers=headers,
                        json={"modality": "music", "model": MODEL,
                              "client_request_id": f"warm-{index}",
                              "items": [{"prompt": PROMPT, "params": params}]})
                    response.raise_for_status()
                    batch = response.json()["batch_id"]
                    while True:
                        summary = client.get(base + f"/api/hub/jobs/{batch}",
                                             headers=headers).json()
                        state = (summary.get("items") or [{}])[0].get("state")
                        if state in {"done", "error", "cancelled", "uncertain"}:
                            break
                        time.sleep(1.0)
                    receipt["runs"].append({
                        "index": index, "state": state,
                        "seconds": round(time.monotonic() - submitted, 2),
                    })
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
    print(json.dumps(receipt, indent=2))
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
