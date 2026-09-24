"""The two joined Hubs the app-level pool probe reads.

Kept beside the probe so the check is one command. `tests/native/pool-probe.sh` builds the Swift
probe and points it here through `PROBE_BINARY`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_standalone_connections import HubProcess, free_port  # noqa: E402

PROBE = os.environ.get("PROBE_BINARY")


def request(hub, path, *, token, method="GET", payload=None):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    return hub.request(path, token=token, method=method, headers=headers, body=body)


def configure(hub, token, machine_id, name, role):
    status, body, _ = request(hub, "/api/hub/controller", token=token, method="PUT",
                              payload={"site_id": machine_id, "site_name": name,
                                       "controller_id": machine_id, "role": role})
    assert status == 200, body


def main() -> int:
    if not PROBE or not Path(PROBE).exists():
        print("set PROBE_BINARY to the built Swift probe", file=sys.stderr)
        return 2
    scratch = tempfile.TemporaryDirectory(prefix="studiohub-pool-probe-")
    root = Path(scratch.name)
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    env = {"STUDIOHUB_MODELS_DIR": str(models),
           "STUDIOHUB_MODEL_MANIFESTS": str(ROOT / "model-manifests")}
    controller = HubProcess(root, "controller", "controller-hub-token",
                            workers=[{"id": "voice", "modality": "voice", "port": free_port()}],
                            env=env)
    node = HubProcess(root, "node", "node-hub-token",
                      workers=[{"id": "image", "modality": "image", "port": free_port()},
                               {"id": "subtitle", "modality": "transcription",
                                "port": free_port()}],
                      env=env)
    for hub in (controller, node):
        # The app decodes the version record as {version, build, source}; a fixture without it
        # fails the whole controller read and the page renders as "Not reporting".
        hub.release.write_text(json.dumps({"version": "0.6.56", "build": "probe",
                                           "source": "probe-fixture", "components": {}}))
    controller.start()
    node.start()
    try:
        configure(controller, "controller-hub-token", "control-mac", "Control Mac", "controller")
        configure(node, "node-hub-token", "studio-mini-02", "Studio Mini 02", "agent")
        fleet = (controller.data / ".fleet_token").read_text().strip()
        status, body, _ = request(node, "/api/hub/fleet/adopt", token="node-hub-token",
                                  method="POST",
                                  payload={"token": fleet, "controller_url": controller.base})
        assert status == 200, body
        status, body, _ = request(
            controller, "/api/hub/fleet/nodes", token=fleet, method="POST",
            payload={"node_id": "studio-mini-02", "name": "Studio Mini 02",
                     "address": node.base, "role": "agent",
                     "studios": [{"id": "image", "modality": "image"},
                                 {"id": "subtitle", "modality": "transcription"}]})
        assert status == 201, body
        time.sleep(0.5)
        for who, hub, token in (("CONTROLLER", controller, "controller-hub-token"),
                                ("NODE", node, "node-hub-token")):
            print(f"\n===== what the app shows on the {who} =====")
            result = subprocess.run([PROBE], env={**os.environ, "PROBE_URL": hub.base,
                                                  "PROBE_TOKEN": token},
                                    capture_output=True, text=True, timeout=120)
            print(result.stdout.strip())
            if result.stderr.strip():
                print("stderr:", result.stderr.strip()[:500], file=sys.stderr)
        return 0
    finally:
        node.stop()
        controller.stop()
        scratch.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
