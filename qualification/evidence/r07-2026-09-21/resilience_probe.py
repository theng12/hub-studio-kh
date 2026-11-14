"""R07 resilience probes: cancellation and restart/history.

Two disposable-state checks against the installed app:
  cancel-music      submit a long Music job, cancel it, and confirm it ends
                    terminal without inventing an artifact;
  history-subtitle  transcribe one clip, stop the service, start it again on the
                    same data directory, and confirm the batch is still listed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import library_guard  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
APP = Path("/Applications/Hub Studio KH.app")
CLIP = ROOT / "qualification/evidence/st04-reference-clip.wav"
WHISPER = "mlx-community/whisper-large-v3-turbo"


class Service:
    def __init__(self, data: Path, models: Path):
        self.data = data
        self.models = library_guard.require_disposable(models)
        self.ready = data / "hub-ready.json"
        self.log_path = data / "service.log"
        self.process: subprocess.Popen | None = None
        self.log = None

    def start(self, timeout: float = 120.0) -> dict:
        if self.ready.exists():
            self.ready.unlink()
        self.log = self.log_path.open("a")
        self.process = subprocess.Popen(
            [str(APP / "Contents/MacOS/StudioHubService"), "--data-dir", str(self.data),
             "--models-dir", str(self.models), "--ready-file", str(self.ready),
             "--parent-pid", str(os.getpid()), "--host", "127.0.0.1", "--port", "0",
             "--offline"],
            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        while not self.ready.exists():
            if self.process.poll() is not None:
                raise SystemExit("service exited:\n" + self.log_path.read_text()[-3000:])
            if time.monotonic() - started > timeout:
                raise SystemExit("service never became ready")
            time.sleep(0.2)
        return json.loads(self.ready.read_text())

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.log is not None:
            self.log.close()
            self.log = None


def _headers(info):
    return {"Authorization": "Bearer " + info["token"]}


def cancel_music(models: Path, output: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="r07-cancel-") as temporary:
        data = Path(temporary) / "state"
        data.mkdir(mode=0o700)
        service = Service(data, models)
        params = {"duration_seconds": 120.0, "steps": 8, "guidance_scale": 1.0,
                  "shift": 3.0, "seed": -1, "instrumental": True,
                  "lyrics": "[Instrumental]", "vocal_language": "unknown"}
        receipt: dict = {"probe": "cancel-music", "params": params}
        try:
            info = service.start()
            with httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
                response = client.post(
                    info["url"] + "/api/hub/jobs", headers=_headers(info),
                    json={"modality": "music", "model": "ACE-Step/Ace-Step1.5",
                          "client_request_id": "r07-cancel-1",
                          "items": [{"prompt": "long ambient drone", "params": params}]})
                receipt["submit_status"] = response.status_code
                receipt["submit_body"] = response.text[:200]
                response.raise_for_status()
                batch = response.json()["batch_id"]
                receipt["batch_id"] = batch
                # Wait until the worker has actually started, then cancel.
                deadline = time.monotonic() + 90
                saw_running = False
                while time.monotonic() < deadline:
                    summary = client.get(info["url"] + f"/api/hub/jobs/{batch}",
                                         headers=_headers(info)).json()
                    item = (summary.get("items") or [{}])[0]
                    if item.get("state") in {"running", "queued"}:
                        saw_running = True
                        break
                    time.sleep(1.0)
                receipt["saw_active"] = saw_running
                cancel = client.delete(info["url"] + f"/api/hub/jobs/{batch}",
                                       headers=_headers(info))
                receipt["cancel_status"] = cancel.status_code
                deadline = time.monotonic() + 180
                final = {}
                while time.monotonic() < deadline:
                    final = client.get(info["url"] + f"/api/hub/jobs/{batch}",
                                       headers=_headers(info)).json()
                    item = (final.get("items") or [{}])[0]
                    if item.get("state") in {"cancelled", "error", "done", "uncertain"}:
                        break
                    time.sleep(2.0)
                receipt["final_item"] = {
                    k: (final.get("items") or [{}])[0].get(k)
                    for k in ("state", "error_code", "error", "bytes", "sha256")}
                artifact = client.get(
                    info["url"] + f"/api/hub/jobs/{batch}/items/0/artifact",
                    headers=_headers(info))
                receipt["artifact_status_after_cancel"] = artifact.status_code
        finally:
            service.stop()
        receipt["cancelled_without_artifact"] = (
            (receipt.get("final_item") or {}).get("state") == "cancelled"
            and receipt.get("artifact_status_after_cancel") != 200)
        # Music is a durable async modality: a cancel sent after the worker may have
        # accepted the job cannot be confirmed, so the Hub must hold it uncertain for
        # reconciliation rather than claim a false "cancelled". Either terminal answer
        # is correct; a fabricated artifact or a "done" is not.
        final_state = (receipt.get("final_item") or {}).get("state")
        receipt["terminal_without_artifact"] = (
            final_state in {"cancelled", "uncertain"}
            and receipt.get("artifact_status_after_cancel") != 200)
        receipt["no_false_completion"] = final_state != "done"
        print(json.dumps(receipt, indent=2))
        output.write_text(json.dumps(receipt, indent=2) + "\n")
        return 0 if (receipt["terminal_without_artifact"]
                     and receipt["no_false_completion"]) else 2


def history_subtitle(models: Path, output: Path) -> int:
    state = ROOT / ".build/deepseek-history-state"
    if state.exists():
        shutil.rmtree(state)
    state.mkdir(mode=0o700)
    service = Service(state, models)
    audio = CLIP.read_bytes()
    receipt: dict = {"probe": "history-subtitle", "model": WHISPER}
    try:
        info = service.start()
        with httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            response = client.post(
                info["url"] + "/api/hub/transcription/jobs", headers=_headers(info),
                data={"item_ids": "clip-1", "model": WHISPER, "word_timestamps": "false"},
                files={"files": (CLIP.name, audio, "audio/wav")})
            response.raise_for_status()
            batch = response.json()["batch_id"]
            receipt["batch_id"] = batch
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                summary = client.get(
                    info["url"] + f"/api/hub/transcription/jobs/{batch}",
                    headers=_headers(info)).json()
                states = [i.get("state") for i in summary.get("items", [])]
                if states and all(s in {"done", "error", "failed", "cancelled"}
                                  for s in states):
                    break
                time.sleep(1.0)
            receipt["first_run_state"] = (summary.get("items") or [{}])[0].get("state")
        service.stop()
        # Restart on the same data directory and look for the batch in history.
        info = service.start()
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            listing = client.get(info["url"] + "/api/hub/transcription/jobs",
                                 headers=_headers(info)).json()
            ids = [row.get("batch_id") or row.get("id") for row in listing.get("batches", [])]
            receipt["restart_batch_listed"] = batch in ids
            receipt["restart_history_ids"] = ids[:10]
    finally:
        service.stop()
    print(json.dumps(receipt, indent=2))
    output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt.get("restart_batch_listed") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", choices=("cancel-music", "history-subtitle"))
    parser.add_argument("--models", type=Path, required=True,
                        help="a DISPOSABLE library (qualification/library_guard.py clone ...), never the real one")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.probe == "cancel-music":
        return cancel_music(args.models, args.output)
    return history_subtitle(args.models, args.output)


if __name__ == "__main__":
    sys.exit(main())
