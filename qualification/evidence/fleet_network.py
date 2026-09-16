"""Bring a packaged Hub up the way a fleet node has to, on a real network address.

Everything about binding has been proven at the worker level, and the fleet path has been
proven over loopback. Neither answers the question a node actually asks: when this Mac is told
to serve its LAN or its tailnet, does the packaged Hub answer there, and can the controller
reach it and put work on it?

This starts two packaged Hubs - a controller on loopback and a node bound to this Mac's real
address - joins them, and runs a Subtitle job across the real interface. It is deliberately
separate from `fleet_packaged.py`, which stays a loopback test that runs anywhere.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_packaged import (APP, EVIDENCE, RESOURCES, Hub, request,  # noqa: E402
                            run_subtitle_job, tone)

REAL_EVIDENCE = EVIDENCE.with_name("fleet-network.json")


def local_address() -> str | None:
    """This Mac's address on the network it would actually be reached over."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are sent; this only asks the routing table which local address it would use.
        probe.connect(("8.8.8.8", 53))
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return address if address and not address.startswith("127.") else None


def main() -> int:
    if not (RESOURCES / "hub_worker.py").is_file():
        print(f"missing staged app: {APP}", file=sys.stderr)
        return 2
    address = local_address()
    if address is None:
        print("this Mac has no non-loopback IPv4 address to bind", file=sys.stderr)
        return 3

    import tempfile
    temporary = tempfile.TemporaryDirectory(prefix="studiohub-network-")
    root = Path(temporary.name)
    evidence: dict = {"app": str(APP.relative_to(APP.parents[1])), "address": address}
    node = Hub("node", root / "node")
    controller = None
    try:
        # The node serves the address the operator picked in Connections -> Fleet, plus
        # loopback, which the Hub always adds for this Mac's own app.
        node.start(host=f"127.0.0.1,{address}")
        evidence["bound_hosts"] = f"127.0.0.1,{address}"

        def call(url: str, path: str):
            return request(f"{url}{path}", token=node.token)

        status, body = call(node.url, "/api/version")
        evidence["loopback"] = {"status": status, "version": body.get("version")
                                if isinstance(body, dict) else None}
        # The readiness record must keep pointing this Mac's own app at loopback, whatever
        # else was bound; otherwise the app would depend on the network staying up.
        evidence["ready_url_is_loopback"] = node.url.startswith("http://127.0.0.1:")

        port = node.url.rsplit(":", 1)[1]
        remote_url = f"http://{address}:{port}"
        status, body = call(remote_url, "/api/version")
        evidence["network"] = {"url": remote_url, "status": status,
                               "version": body.get("version") if isinstance(body, dict) else None}
        status, body = call(remote_url, "/api/hub/resources?local_only=true")
        evidence["network_resources"] = {
            "status": status,
            "chip": (body.get("host") or {}).get("chip") if isinstance(body, dict) else None,
            "version": body.get("version") if isinstance(body, dict) else None,
            "studios": sorted((body.get("studios") or {}).keys())
            if isinstance(body, dict) else None}

        # The controller joins the node at its network address, exactly as a joined Mac is
        # recorded, and adopts the credential the same way the app's Join does.
        controller = Hub("controller", root / "controller", joined=[{
            "node_id": "node-lan", "name": "Node over the LAN",
            "address": remote_url,
            "studios": [{"id": "subtitle", "modality": "transcription"}]}])
        controller.start()
        status, adopted = request(f"{node.url}/api/hub/fleet/adopt", method="POST",
                                  token=node.hub_token(),
                                  payload={"token": controller.fleet_token()})
        evidence["adopt"] = {"status": status,
                             "adopted": adopted.get("adopted") if isinstance(adopted, dict) else None}

        # Wait for the controller to see the node's studio over the real interface before
        # submitting, so a pass cannot be the job running locally.
        deadline = time.time() + 180
        online = False
        while time.time() < deadline:
            status, caps = request(f"{controller.url}/api/hub/capabilities",
                                   token=controller.token)
            if status == 200:
                online = any("subtitle" in str(row.get("service_id") or "")
                             and row.get("physical_machine_id") == "node-lan"
                             and row.get("online")
                             for row in caps.get("workers", []))
                if online:
                    break
            time.sleep(2)
        evidence["node_studio_online"] = online
        if not online:
            raise RuntimeError(f"the controller never saw the node's studio at {remote_url}")

        job = run_subtitle_job(controller, "fleet-network-1", "fleet-network", None)
        evidence["job"] = {"status": job.get("status"),
                           "studio": (job.get("items") or [{}])[0].get("studio"),
                           "artifact_status": job.get("artifact_status"),
                           "artifact_bytes": job.get("artifact_bytes")}

        problems = []
        if evidence["loopback"]["status"] != 200:
            problems.append("the node stopped answering on loopback once it was bound to the network")
        if not evidence["ready_url_is_loopback"]:
            problems.append("the readiness record pointed the app at the network address")
        if evidence["network"]["status"] != 200:
            problems.append(f"the node did not answer at its own network address: {evidence['network']}")
        if evidence["network_resources"]["status"] != 200:
            problems.append("the node did not publish its resources over the network")
        if evidence["adopt"]["status"] != 200:
            problems.append("the node did not adopt the controller's credential")
        if (evidence["job"]["studio"] or "") != "node-lan:subtitle":
            problems.append(f"the job did not run on the network node: {evidence['job']}")
        if evidence["job"]["artifact_status"] != 200:
            problems.append("the artifact did not come back over the network")
        evidence["problems"] = problems
        evidence["ok"] = not problems
        REAL_EVIDENCE.write_text(json.dumps(evidence, indent=1, sort_keys=True, default=str) + "\n")
        print(json.dumps(evidence, indent=1, sort_keys=True, default=str))
        return 0 if not problems else 6
    except Exception as exc:  # noqa: BLE001 - the evidence is the point
        evidence["problems"] = [f"{type(exc).__name__}: {exc}"]
        evidence["ok"] = False
        REAL_EVIDENCE.write_text(json.dumps(evidence, indent=1, sort_keys=True, default=str) + "\n")
        print(json.dumps(evidence, indent=1, sort_keys=True, default=str), file=sys.stderr)
        return 7
    finally:
        if controller is not None:
            controller.stop()
        node.stop()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
