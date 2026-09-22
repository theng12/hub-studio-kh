"""Exercise the exact calls GatewayKH now makes, using the Fleet credential.

This talks to the running installed Hub over HTTP as a controller would:
submit -> poll -> artifact -> ack -> (discard attempt) -> cancel. It proves the
fleet-token allowlist reaches each route and that the Hub records the delivery ACK.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

import httpx

DATA = Path.home() / "Library" / "Application Support" / "StudioHub Standalone"
READY = DATA / "hub-ready.json"
FLEET = DATA / "hub" / ".fleet_token"
MODEL = "stabilityai/stable-audio-3-optimized"


def main() -> int:
    ready = json.loads(READY.read_text())
    base = ready["url"]
    token = FLEET.read_text().strip()
    headers = {"X-Hub-Token": token}
    out: dict = {"base": base, "credential": "fleet"}

    with httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
        # 1. Submit as the controller does.
        response = client.post(base + "/api/hub/jobs", headers=headers, json={
            "modality": "music", "model": MODEL, "client_request_id": "gateway-verify-1",
            "items": [{"prompt": "Short warm piano phrase, instrumental",
                       "params": {"duration_seconds": 10, "steps": 8, "seed": 5}}]})
        out["submit_status"] = response.status_code
        if response.status_code >= 400:
            out["submit_body"] = response.text[:300]
            print(json.dumps(out, indent=2))
            return 1
        batch = response.json()["batch_id"]
        out["batch_id"] = batch

        # 2. Poll.
        deadline = time.monotonic() + 540
        while True:
            detail = client.get(base + f"/api/hub/jobs/{batch}", headers=headers).json()
            item = (detail.get("items") or [{}])[0]
            if item.get("state") in {"done", "error", "cancelled", "uncertain"}:
                break
            if time.monotonic() > deadline:
                out["poll"] = "timeout"
                print(json.dumps(out, indent=2))
                return 1
            time.sleep(2.0)
        out["state"] = item.get("state")

        # 3. Artifact (the Hub sends X-Content-SHA256 when known).
        if item.get("state") == "done":
            artifact = client.get(base + item["artifact_url"], headers=headers)
            out["artifact_status"] = artifact.status_code
            out["artifact_bytes"] = len(artifact.content)
            out["artifact_sha256_header"] = artifact.headers.get("X-Content-SHA256")
            out["artifact_sha256_matches"] = (
                artifact.headers.get("X-Content-SHA256")
                == hashlib.sha256(artifact.content).hexdigest())

            # 4. ACK, exactly as the controller now does.
            ack = client.post(base + f"/api/hub/jobs/{batch}/items/0/ack", headers=headers)
            out["ack_status"] = ack.status_code

            # 5. Did the Hub record the delivery?
            after = client.get(base + f"/api/hub/jobs/{batch}", headers=headers).json()
            after_item = (after.get("items") or [{}])[0]
            out["ack_recorded"] = {
                key: after_item.get(key) for key in
                ("receipt_acked_at", "acknowledged", "acknowledged_at")
                if key in after_item}

        # 6. Discard with the fleet credential: the route must be reachable (400 for a
        #    non-uncertain item), not forbidden (403).
        discard = client.post(base + f"/api/hub/jobs/{batch}/items/0/discard",
                              headers=headers)
        out["discard_status"] = discard.status_code
        out["discard_means"] = ("reachable (item is not uncertain)"
                                if discard.status_code == 400 else discard.text[:120])

        # 7. Cancel, as the controller now does.
        cancel = client.delete(base + f"/api/hub/jobs/{batch}", headers=headers)
        out["cancel_status"] = cancel.status_code

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
