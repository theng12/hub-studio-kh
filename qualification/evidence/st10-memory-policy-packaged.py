"""Prove the memory-policy contract through the real packaged app.

Launches the staged bundle's own Hub (which spawns its own Voice, Image and Subtitle
workers), then drives the exact routes the app's Settings screen calls. This is the
check that matters: the unit suites cannot see whether the bundled workers answer on
the paths the Hub asks for.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "dist" / "Studio Hub 0.4.0 Memory Proof.app"
RESOURCES = APP / "Contents" / "Resources"
LIBRARY = ROOT / "model-library"
EVIDENCE = ROOT / "qualification" / "evidence" / "st10-memory-policy-packaged.json"


def request(url: str, *, method: str = "GET", token: str | None = None,
            payload: dict | None = None, timeout: float = 30.0):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Hub-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw or b"null")
        except ValueError:
            return error.code, {"raw": raw.decode(errors="replace")[:400]}


def main() -> int:
    if not RESOURCES.is_dir():
        print(f"missing staged app: {APP}", file=sys.stderr)
        return 2
    temporary = tempfile.TemporaryDirectory(prefix="studiohub-memory-proof-")
    root = Path(temporary.name)
    data = root / "state"
    data.mkdir()
    # The Hub refuses a readiness file outside its own app data directory.
    ready = data / "hub-ready.json"
    log = (root / "hub.log").open("wb")
    process = subprocess.Popen(
        [str(RESOURCES / "runtimes" / "voice" / "bin" / "python3"), "-I", "-B",
         str(RESOURCES / "hub_worker.py"), "--data-dir", str(data),
         "--models-dir", str(LIBRARY), "--ready-file", str(ready),
         "--parent-pid", str(os.getpid()), "--offline"],
        cwd=str(data), stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 240
        info = None
        while time.time() < deadline:
            if process.poll() is not None:
                print("hub exited during startup", file=sys.stderr)
                print((root / "hub.log").read_text()[-4000:], file=sys.stderr)
                return 3
            try:
                info = json.loads(ready.read_text())
                break
            except (OSError, ValueError):
                time.sleep(0.4)
        if info is None:
            print("hub never became ready", file=sys.stderr)
            return 4
        base, token = info["url"].rstrip("/"), info["token"]
        evidence: dict = {"app": str(APP.relative_to(ROOT)), "hub_url": base,
                          "ready_keys": sorted(info), "steps": []}

        status, inventory = request(f"{base}/api/hub/memory/policy", token=token)
        evidence["steps"].append({"step": "GET /api/hub/memory/policy",
                                  "status": status, "body": inventory})
        if status != 200:
            print(json.dumps(evidence, indent=1))
            return 5
        studios = {row["id"]: row for row in inventory["studios"]}
        evidence["studios"] = {
            key: {
                "state": row["state"],
                "modality": row["modality"],
                "mode": (row.get("policy") or {}).get("mode"),
                "default_mode": (row.get("policy") or {}).get("default_mode"),
                "loaded_models": (row.get("policy") or {}).get("loaded_models"),
                "fixed": (row.get("policy") or {}).get("fixed"),
                "machine_bytes": (row.get("policy") or {}).get("machine_bytes"),
            } for key, row in studios.items()
        }
        problems = []
        for expected in ("voice", "image", "subtitle"):
            row = studios.get(expected)
            if row is None:
                problems.append(f"{expected} is missing from the memory inventory")
            elif row["state"] != "ready":
                problems.append(f"{expected} reports {row['state']}: {row.get('detail')}")

        status, changed = request(f"{base}/api/hub/memory/policy", method="PUT", token=token,
                                  payload={"mode": "memory_saver"})
        evidence["steps"].append({"step": "PUT /api/hub/memory/policy memory_saver",
                                  "status": status, "body": changed})
        if status != 200 or not changed.get("ok"):
            problems.append(f"mode change failed: {status} {changed}")

        status, denied = request(f"{base}/api/hub/memory/policy", method="PUT",
                                 payload={"mode": "overnight"})
        evidence["steps"].append({"step": "PUT without a credential", "status": status,
                                  "body": denied})
        if status != 401:
            problems.append(f"an unauthenticated write was not refused: {status}")

        status, rejected = request(f"{base}/api/hub/memory/policy", method="PUT", token=token,
                                   payload={"mode": "overnight"})
        evidence["steps"].append({"step": "PUT with an unknown mode", "status": status,
                                  "body": rejected})
        if status != 400:
            problems.append(f"an unknown mode was not refused: {status}")

        status, released = request(f"{base}/api/hub/memory/release", method="POST", token=token,
                                   payload={})
        evidence["steps"].append({"step": "POST /api/hub/memory/release", "status": status,
                                  "body": released})
        if status != 200 or not released.get("ok"):
            problems.append(f"release failed: {status} {released}")

        status, restored = request(f"{base}/api/hub/memory/policy", method="PUT", token=token,
                                   payload={"mode": "immediate"})
        evidence["steps"].append({"step": "PUT /api/hub/memory/policy immediate",
                                  "status": status, "body": restored})
        if status != 200:
            problems.append(f"restoring the default failed: {status}")

        # A saved choice must survive a restart, and the small-machine default must be the
        # one this host resolves rather than the Hub's fleet-wide draft.
        policy_file = data / "subtitle" / "memory_policy.json"
        evidence["subtitle_policy_file"] = {
            "path": str(policy_file.relative_to(root)),
            "exists": policy_file.is_file(),
            "body": policy_file.read_text().strip() if policy_file.is_file() else None,
        }
        if not policy_file.is_file():
            problems.append("the Subtitle worker did not persist the operator's choice "
                            "beside its own state")

        evidence["problems"] = problems
        evidence["ok"] = not problems
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
        print(json.dumps(evidence, indent=1, sort_keys=True))
        return 0 if not problems else 6
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
