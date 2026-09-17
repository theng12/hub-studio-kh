"""Run the fleet path against the packaged app: two real Hubs from one bundle.

Everything about the fleet so far is proven between two Hubs started from source. This starts
two from the staged bundle - a controller and a node, each with its own workers - joins them the
way the app does, and sends a real Subtitle job across. It is the check that has found a defect
every previous time it was tried.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "dist" / "Studio Hub.app"
RESOURCES = APP / "Contents" / "Resources"
LIBRARY = ROOT / "model-library"
EVIDENCE = ROOT / "qualification" / "evidence" / "fleet-packaged.json"


def request(url: str, *, method: str = "GET", token: str | None = None,
            payload: dict | None = None, fields: dict | None = None,
            files: dict | None = None, timeout: float = 300.0):
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Hub-Token"] = token
    data = None
    if files:
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        for key, value in (fields or {}).items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.write(str(value).encode())
            body.write(b"\r\n")
        for key, (name, content, media) in files.items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{key}"; '
                       f'filename="{name}"\r\n'.encode())
            body.write(f"Content-Type: {media}\r\n\r\n".encode())
            body.write(content)
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        data = body.getvalue()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw or b"null")
            except ValueError:
                return response.status, raw
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw or b"null")
        except ValueError:
            return error.code, raw


def tone(seconds: float = 5.0, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"".join(
            int(6000 * (1 if (index // 2000) % 2 else -1)).to_bytes(2, "little", signed=True)
            for index in range(int(seconds * rate))))
    return buffer.getvalue()


def run_subtitle_job(controller: "Hub", item_id: str, episode: str, routing: str | None) -> dict:
    """Submit one Subtitle job the way the app does, and wait for it to settle.

    ``routing`` is the app's pin: absent means the Hub chooses the Mac, and ``machine:<id>``
    names one. It travels as a form field because that is how the app sends a Subtitle job.
    """
    fields = {"model": "mlx-community/whisper-large-v3-turbo",
              "item_ids": item_id, "episode": episode}
    if routing:
        fields["routing"] = routing
    status, submitted = request(
        f"{controller.url}/api/hub/transcription/jobs", method="POST",
        token=controller.token,
        fields=fields, files={"files": ("fleet.wav", tone(), "audio/wav")})
    result = {"routing": routing, "submit_status": status,
              "detail": submitted if status != 200 else None}
    if status != 200:
        return result
    batch = submitted["batch_id"]
    result["batch_id"] = batch
    deadline = time.time() + 420
    final: dict = {}
    while time.time() < deadline:
        read_status, final = request(f"{controller.url}/api/hub/transcription/jobs/{batch}",
                                     token=controller.token)
        states = {item.get("state") for item in (final.get("items") or [])}
        if states and states <= {"done", "error", "cancelled"}:
            break
        time.sleep(2)
    result["status"] = final.get("status")
    result["governor_note"] = final.get("governor_note")
    result["items"] = [{key: item.get(key) for key in
                        ("state", "studio", "error", "duration_seconds")}
                       for item in (final.get("items") or [])]
    result["artifact_status"], body = request(
        f"{controller.url}/api/hub/transcription/jobs/{batch}/items/0/artifact",
        token=controller.token)
    result["artifact_bytes"] = len(body) if isinstance(body, bytes) else 0
    result["artifact_sha256"] = (hashlib.sha256(body).hexdigest()
                                 if isinstance(body, bytes) else None)
    result["artifact_preview"] = (body.decode("utf-8", "replace")
                                  if isinstance(body, bytes) else json.dumps(body))[:200]
    return result


class Hub:
    def __init__(self, name: str, data: Path, joined: list[dict] | None = None):
        self.name = name
        self.data = data
        self.data.mkdir(parents=True, exist_ok=True)
        # The packaged Hub keeps its own state - token, fleet credential, joined nodes - in a
        # `hub` subdirectory of the app data directory, not at the top of it.
        self.state = data / "hub"
        self.state.mkdir(parents=True, exist_ok=True)
        # The Hub refuses to run against a state directory anyone else can read.
        os.chmod(self.state, 0o700)
        if joined is not None:
            (self.state / "fleet_nodes.json").write_text(json.dumps({
                row["node_id"]: {"id": row["node_id"], "name": row["name"],
                                 "address": row["address"], "role": "agent",
                                 "studios": row["studios"], "joined_at": 1.0,
                                 "updated_at": 1.0} for row in joined}))
            os.chmod(self.state / "fleet_nodes.json", 0o600)
        self.ready = data / "hub-ready.json"
        self.log = open(data.parent / f"{name}.log", "wb")
        self.process: subprocess.Popen | None = None
        self.url = ""
        self.token = ""

    def start(self, host: str = "127.0.0.1") -> None:
        self.process = subprocess.Popen(
            [str(RESOURCES / "runtimes" / "voice" / "bin" / "python3"), "-I", "-B",
             str(RESOURCES / "hub_worker.py"), "--data-dir", str(self.data),
             "--models-dir", str(LIBRARY), "--ready-file", str(self.ready),
             "--host", host,
             "--parent-pid", str(os.getpid()), "--offline"],
            cwd=str(self.data), stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.name} exited during startup:\n"
                                   f"{(self.data.parent / f'{self.name}.log').read_text()[-3000:]}")
            try:
                info = json.loads(self.ready.read_text())
                self.url, self.token = info["url"].rstrip("/"), info["token"]
                return
            except (OSError, ValueError, KeyError):
                time.sleep(0.5)
        raise RuntimeError(f"{self.name} never became ready")

    def hub_token(self) -> str:
        return (self.state / ".hub_token").read_text().strip()

    def fleet_token(self) -> str:
        return (self.state / ".fleet_token").read_text().strip()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.log.close()


def main() -> int:
    if not (RESOURCES / "hub_worker.py").is_file():
        print(f"missing staged app: {APP}", file=sys.stderr)
        return 2
    temporary = tempfile.TemporaryDirectory(prefix="studiohub-fleet-proof-")
    root = Path(temporary.name)
    evidence: dict = {"app": str(APP.relative_to(ROOT)), "steps": []}
    node = Hub("node", root / "node")
    controller = None
    try:
        node.start()
        evidence["node_url_is_loopback"] = node.url.startswith("http://127.0.0.1:")
        port = node.url.rsplit(":", 1)[1]

        # The controller learns about the node the way it does in the app: the node joined
        # earlier, so it is in the registry when the controller starts.
        controller = Hub("controller", root / "controller", joined=[{
            "node_id": "node-a", "name": "Node A",
            "address": f"http://127.0.0.1:{port}",
            "studios": [{"id": "subtitle", "modality": "transcription"}]}])
        controller.start()

        # Joining the fleet means adopting its credential. The node does that with its own Hub
        # token, which never leaves this Mac.
        status, adopted = request(f"{node.url}/api/hub/fleet/adopt", method="POST",
                                  token=node.hub_token(),
                                  payload={"token": controller.fleet_token()})
        evidence["adopt"] = {"status": status, "adopted": adopted.get("adopted")
                             if isinstance(adopted, dict) else None}

        # And the controller can see it.
        status, listing = request(f"{controller.url}/api/hub/fleet/nodes",
                                  token=controller.token)
        evidence["nodes"] = {"status": status, "summary": listing.get("summary"),
                             "state": (listing.get("nodes") or [{}])[0].get("state")}

        # Wait until the controller can actually see the node's studio, or the job simply runs
        # on the controller - which is correct behaviour and would prove nothing about the fleet.
        deadline = time.time() + 180
        online = False
        while time.time() < deadline:
            status, caps = request(f"{controller.url}/api/hub/capabilities",
                                   token=controller.token)
            if status == 200:
                # A joined node's studio keeps its own id, so it is "node-a:subtitle" here
                # rather than "subtitle" - the machine is what distinguishes it from this Mac's.
                online = any("subtitle" in str(row.get("service_id") or "")
                             and row.get("physical_machine_id") != "local"
                             and row.get("online")
                             for row in caps.get("workers", []))
                if online:
                    break
            time.sleep(2)
        evidence["node_studio_online"] = online
        status, caps = request(f"{controller.url}/api/hub/capabilities", token=controller.token)
        evidence["workers"] = [(row.get("service_id"), row.get("physical_machine_id"),
                                row.get("online"), row.get("ready"))
                               for row in (caps.get("workers") or [])] if status == 200 else None
        if not online:
            raise RuntimeError("the controller never saw the node's Subtitle studio come up")

        # The automatic path first: nothing is named, so this is the Hub choosing a Mac.
        automatic = run_subtitle_job(controller, "fleet-packaged-1", "fleet", None)
        evidence["submit"] = {"status": automatic["submit_status"],
                              "detail": automatic.get("detail"),
                              "batch_id": automatic.get("batch_id")}
        if automatic["submit_status"] != 200:
            raise RuntimeError(f"the controller refused the job: {automatic.get('detail')}")
        evidence["job"] = {"status": automatic.get("status"),
                           "governor_note": automatic.get("governor_note"),
                           "items": automatic.get("items")}
        # The artifact has to come back through the controller even though it was produced on
        # the node, which is the owner's rule: the result lives there and is pulled on request.
        evidence["artifact"] = {"status": automatic.get("artifact_status"),
                                "bytes": automatic.get("artifact_bytes"),
                                "sha256": automatic.get("artifact_sha256"),
                                "preview": automatic.get("artifact_preview")}

        # The manual override: an operator who names a Mac gets that Mac, per modality and for
        # real. Subtitle is the interesting one because it has its own dispatcher, and this is
        # the only place the pin is exercised against the shipped bundle.
        evidence["pinned_job"] = run_subtitle_job(controller, "fleet-packaged-2", "fleet-pin",
                                                 "machine:node-a")
        # And a name that no Mac has is refused at the door instead of queueing forever.
        refused_status, refused = request(
            f"{controller.url}/api/hub/transcription/jobs", method="POST",
            token=controller.token,
            fields={"model": "mlx-community/whisper-large-v3-turbo",
                    "item_ids": "fleet-packaged-3", "episode": "fleet-badpin",
                    "routing": "machine:not-a-mac"},
            files={"files": ("fleet.wav", tone(), "audio/wav")})
        evidence["bad_pin"] = {"status": refused_status,
                               "detail": refused.get("detail") if isinstance(refused, dict) else refused}

        # The voice library, against the real Voice worker rather than a stub: the same calls
        # the Voices page makes, in order. This is the round trip that matters most, because
        # the app is only a window onto a store the worker owns.
        voice_clip = tone(seconds=1.0)
        status, created = request(f"{controller.url}/studio/voice/api/voices", method="POST",
                                  token=controller.token,
                                  fields={"name": "Packaged Probe", "language": "en",
                                          "gender": "n", "license": "self-owned",
                                          "notes": "fleet probe",
                                          "transcript": "one two three four five",
                                          "permission_acknowledged": "true"},
                                  files={"audio": ("probe.wav", voice_clip, "audio/wav")})
        evidence["voice_add"] = {"status": status,
                                 "detail": created if status not in (200, 201) else None}
        voice_id = None
        if isinstance(created, dict) and isinstance(created.get("voice"), dict):
            voice_id = created["voice"].get("id")
            evidence["voice_add"]["name"] = created["voice"].get("name")
            evidence["voice_add"]["has_transcript"] = created["voice"].get("has_transcript")
        if voice_id:
            status, listing = request(f"{controller.url}/studio/voice/api/voices",
                                      token=controller.token)
            rows = {row.get("id"): row for row in (listing.get("voices") or [])}
            evidence["voice_list"] = {"status": status, "count": len(rows),
                                      "found": voice_id in rows}
            # Play: the clip must come back as the bytes that went in.
            status, body = request(
                f"{controller.url}/studio/voice/api/voices/{voice_id}/audio",
                token=controller.token)
            evidence["voice_play"] = {
                "status": status,
                "bytes": len(body) if isinstance(body, bytes) else 0,
                "matches_upload": body == voice_clip if isinstance(body, bytes) else False}
            # Edit changes the transcript, which is the reason to have the route at all.
            status, edited = request(
                f"{controller.url}/studio/voice/api/voices/{voice_id}", method="PATCH",
                token=controller.token,
                payload={"name": "Packaged Probe", "language": "en", "gender": "n",
                         "license": "self-owned", "notes": "edited",
                         "transcript": "one two three four five six"})
            evidence["voice_edit"] = {
                "status": status,
                "transcript": (edited.get("voice") or {}).get("transcript")
                if isinstance(edited, dict) else None}
            status, removed = request(
                f"{controller.url}/studio/voice/api/voices/{voice_id}", method="DELETE",
                token=controller.token)
            evidence["voice_remove"] = {"status": status,
                                        "deleted": removed.get("deleted")
                                        if isinstance(removed, dict) else None}

        # What the operator would see in the app's fleet screen: one row per Mac, from the
        # packaged Hub, after the work has actually run.
        status, overview = request(f"{controller.url}/api/hub/fleet/overview",
                                   token=controller.token)
        evidence["overview"] = {"status": status}
        if status == 200:
            rows = {row.get("machine"): row for row in (overview.get("machines") or [])}
            evidence["overview"]["machines"] = sorted(rows)
            evidence["overview"]["summary"] = overview.get("summary")
            evidence["overview"]["local"] = {
                key: (rows.get("local") or {}).get(key)
                for key in ("reachable", "version", "disk_free_gb", "memory_gb", "chip")}
            node_row = rows.get("node-a") or {}
            evidence["overview"]["node"] = {
                key: node_row.get(key) for key in
                ("reachable", "version", "jobs", "average_seconds", "modalities",
                 "disk_free_gb", "chip")}

        problems = []
        if evidence["submit"]["status"] != 200:
            problems.append("the controller refused the submission")
        if evidence["job"]["status"] != "done":
            problems.append(f"the job did not finish: {evidence['job']}")
        if evidence["artifact"]["status"] != 200:
            problems.append("the artifact did not come back through the controller")
        pinned = evidence["pinned_job"]
        if pinned["submit_status"] != 200:
            problems.append(f"the controller refused a pinned job: {pinned.get('detail')}")
        elif pinned.get("status") != "done":
            problems.append(f"the pinned job did not finish: {pinned}")
        else:
            ran_on = (pinned.get("items") or [{}])[0].get("studio")
            if ran_on != "node-a:subtitle":
                problems.append(f"a job pinned to node-a ran on {ran_on!r} instead")
            if not pinned.get("artifact_bytes"):
                problems.append("the pinned job returned no artifact through the controller")
        voice = evidence.get("voice_add") or {}
        if voice.get("status") not in (200, 201):
            problems.append(f"the packaged Hub refused to save a voice: {voice}")
        else:
            if not voice.get("has_transcript"):
                problems.append("a saved voice did not record its transcript")
            listing = evidence.get("voice_list") or {}
            if not listing.get("found"):
                problems.append(f"a saved voice was not in the list: {listing}")
            played = evidence.get("voice_play") or {}
            if played.get("status") != 200 or not played.get("matches_upload"):
                problems.append(f"the saved clip did not come back unchanged: {played}")
            edited = evidence.get("voice_edit") or {}
            if edited.get("status") != 200 or edited.get(
                    "transcript") != "one two three four five six":
                problems.append(f"editing a saved voice did not take: {edited}")
            if (evidence.get("voice_remove") or {}).get("status") != 200:
                problems.append(f"a saved voice could not be removed: "
                                f"{evidence.get('voice_remove')}")
        if evidence["bad_pin"]["status"] != 400:
            problems.append(f"a pin naming a Mac that is not here was not refused: "
                            f"{evidence['bad_pin']}")
        if evidence["overview"]["status"] != 200:
            problems.append("the fleet overview was not served by the packaged Hub")
        else:
            if evidence["overview"]["machines"] != ["local", "node-a"]:
                problems.append(f"the overview did not cover every Mac: "
                                f"{evidence['overview']['machines']}")
            local = evidence["overview"]["local"]
            if not local.get("disk_free_gb") or not local.get("version"):
                problems.append(f"this Mac's own row is incomplete: {local}")
            node_row = evidence["overview"]["node"]
            if not node_row.get("reachable"):
                problems.append(f"the node that just ran the job reads as absent: {node_row}")
            # The operator has to be able to tell which build a node is running, and the
            # controller can only know it if the node's snapshot carries it.
            if node_row.get("version") != evidence["overview"]["local"].get("version"):
                problems.append("the node does not report the build the controller is running: "
                                f"{node_row.get('version')!r}")
            # The row must count the work that has actually run there. The overview is read
            # after both jobs, so anything that reports one of them is not reporting the node.
            if (node_row.get("jobs") or 0) < 2 or node_row.get("average_seconds") is None:
                problems.append("the node's measured work is missing from its row: "
                                f"{node_row}")
        evidence["problems"] = problems
        evidence["ok"] = not problems
        EVIDENCE.write_text(json.dumps(evidence, indent=1, sort_keys=True, default=str) + "\n")
        print(json.dumps(evidence, indent=1, sort_keys=True, default=str))
        return 0 if not problems else 6
    except Exception as exc:  # noqa: BLE001 - the evidence is the point
        evidence["problems"] = [f"{type(exc).__name__}: {exc}"]
        evidence["ok"] = False
        EVIDENCE.write_text(json.dumps(evidence, indent=1, sort_keys=True, default=str) + "\n")
        print(json.dumps(evidence, indent=1, sort_keys=True, default=str), file=sys.stderr)
        return 7
    finally:
        if controller is not None:
            controller.stop()
        node.stop()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
