"""Measure one machine: run one generation per advertised model through the packaged app.

Run this on each Mac once the app is installed. It launches the bundle's own Hub (which
brings up its own Voice, Image and Subtitle workers), submits one job per model over the real
Hub API, waits for it to finish, and writes a receipt with that machine's elapsed time, output
identity, peak worker memory and swap state.

Nothing here is a benchmark harness bolted on the side: every number comes from the job
records the Hub already keeps, so the receipt can be compared with `GET /api/hub/stats` on
the same machine.

What the Hub actually publishes differs by studio, and this reads the real fields rather than
assuming one shape:

* output identity comes from `terminal_result` (`bytes`, `sha256`), with the item's own
  top-level fields as a fallback;
* peak worker memory is `resource_usage.worker.peak_rss_gb` — the Hub's sanitized telemetry
  contract, never a top-level item field;
* the Subtitle worker reports no memory telemetry at all, so its run records runtime and the
  input digest only. That is a limitation, not a measured zero.

Voice cloning needs a real reference clip and its exact transcript, because a clone fails
closed on a transcript mismatch. Supply them with --reference and --transcript; without them
the voice run is skipped and the reason is recorded rather than invented.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave


def http(base: str, path: str, token: str, *, method: str = "GET", payload=None,
         fields: dict | None = None, files: dict | None = None, timeout: float = 3600.0):
    url = f"{base}{path}"
    headers = {"Accept": "application/json", "X-Hub-Token": token}
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
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw or b"null")
        except ValueError:
            return error.code, {"raw": raw.decode(errors="replace")[:400]}


def tone(seconds: float = 6.0, rate: int = 24000) -> bytes:
    """A deterministic clip so two machines measure the same input."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"".join(
            int(6000 * (1 if (index // 2000) % 2 else -1)).to_bytes(2, "little", signed=True)
            for index in range(int(seconds * rate))))
    return buffer.getvalue()


def swap_state() -> dict:
    out = {}
    for key, command in (("swap", ["/usr/sbin/sysctl", "-n", "vm.swapusage"]),
                         ("memory", ["/usr/sbin/sysctl", "-n", "hw.memsize"]),
                         ("macos", ["/usr/bin/sw_vers", "-productVersion"])):
        try:
            out[key] = subprocess.check_output(command, text=True, timeout=5).strip()
        except (OSError, subprocess.SubprocessError):
            out[key] = None
    return out


def job_metrics(item: dict) -> dict:
    """The work-identity fields the Hub really publishes for one finished item.

    ``terminal_result`` is the canonical place for a completed item's output identity, kept
    as the primary source with the item's own top-level fields as a fallback. Peak worker
    memory lives in the sanitized ``resource_usage`` telemetry and never at the top level, so
    reading ``peak_process_rss_bytes`` off the item always produced null: a receipt that
    claimed to carry memory pressure carried none of it.
    """
    terminal = item.get("terminal_result")
    terminal = terminal if isinstance(terminal, dict) else {}
    usage = item.get("resource_usage")
    usage = usage if isinstance(usage, dict) else {}
    worker = usage.get("worker") if isinstance(usage.get("worker"), dict) else {}
    mlx = usage.get("mlx") if isinstance(usage.get("mlx"), dict) else {}
    host = usage.get("host") if isinstance(usage.get("host"), dict) else {}
    outcome = usage.get("outcome") if isinstance(usage.get("outcome"), dict) else {}
    return {
        "state": item.get("state"),
        "studio": item.get("studio"),
        "error": item.get("error"),
        "duration_seconds": item.get("duration_seconds") or terminal.get("duration_s"),
        "bytes": terminal.get("bytes", item.get("bytes")),
        "sha256": terminal.get("sha256", item.get("sha256")),
        # The Hub's own names, so a null here means the worker reported nothing rather than
        # this reader looking in the wrong place.
        "peak_worker_rss_gb": worker.get("peak_rss_gb"),
        "peak_mlx_active_gb": mlx.get("peak_active_gb"),
        "maximum_swap_used_gb": host.get("maximum_swap_used_gb"),
        "memory_failure": outcome.get("memory_failure"),
    }


def verified_packages(base: str, token: str) -> dict[str, list[dict]]:
    """Verified packages grouped by the worker that owns them.

    Every voice package shares one engine name, so the worker id is the only reliable
    way to tell a Whisper package from a FLUX one from a Qwen clone.
    """
    status, payload = http(base, "/api/hub/model-packages", token)
    if status != 200:
        return {}
    out: dict[str, list[dict]] = {}
    for row in payload.get("models") or []:
        if row.get("state") != "files_verified":
            continue
        if row.get("worker_state") != "running":
            continue
        out.setdefault(row.get("worker_id") or "unknown", []).append(row)
    return out


def wait_for(base: str, token: str, batch: str, *, transcription: bool) -> dict:
    path = (f"/api/hub/transcription/jobs/{batch}" if transcription
            else f"/api/hub/jobs/{batch}")
    deadline = time.time() + 3600
    while time.time() < deadline:
        status, payload = http(base, path, token)
        if status != 200:
            return {"state": "unreadable", "detail": payload}
        items = payload.get("items") or []
        states = {item.get("state") for item in items}
        if states and states <= {"done", "error", "cancelled"}:
            return payload
        time.sleep(2)
    return {"state": "timeout"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", default=platform.node() or "unnamed-machine")
    parser.add_argument("--kinds", default="image,subtitle,voice")
    parser.add_argument("--audio", type=Path, help="audio used for the subtitle run")
    parser.add_argument("--reference", type=Path, help="reference clip for the voice clone")
    parser.add_argument("--transcript", default="", help="exact words in the reference clip")
    parser.add_argument("--prompt", default="A quiet harbour at dawn, soft light")
    arguments = parser.parse_args()

    resources = arguments.app.expanduser().resolve() / "Contents" / "Resources"
    if not (resources / "hub_worker.py").is_file():
        parser.error(f"{arguments.app} is not a staged Studio Hub app")

    temporary = tempfile.TemporaryDirectory(prefix="studiohub-measure-")
    root = Path(temporary.name)
    data = root / "state"
    data.mkdir()
    ready = data / "hub-ready.json"
    log = (root / "hub.log").open("wb")
    process = subprocess.Popen(
        [str(resources / "runtimes" / "voice" / "bin" / "python3"), "-I", "-B",
         str(resources / "hub_worker.py"), "--data-dir", str(data),
         "--models-dir", str(arguments.library.expanduser().resolve()),
         "--ready-file", str(ready), "--parent-pid", str(os.getpid()), "--offline"],
        cwd=str(data), stdout=log, stderr=subprocess.STDOUT)

    receipt: dict = {
        "label": arguments.label,
        "captured_at": time.time(),
        "captured_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app": arguments.app.name,
        "host": {"platform": platform.platform(), "machine": platform.machine(), **swap_state()},
        "runs": [],
        "skipped": [],
    }
    try:
        deadline = time.time() + 300
        info = None
        while time.time() < deadline:
            if process.poll() is not None:
                print((root / "hub.log").read_text()[-4000:], file=sys.stderr)
                return 3
            try:
                info = json.loads(ready.read_text())
                break
            except (OSError, ValueError):
                time.sleep(0.5)
        if info is None:
            print("the Hub never became ready", file=sys.stderr)
            return 4
        base, token = info["url"].rstrip("/"), info["token"]
        receipt["hub_url"] = base
        packages = verified_packages(base, token)
        receipt["verified_packages"] = {
            worker: [row["package_id"] for row in rows]
            for worker, rows in sorted(packages.items())
        }

        # Accepting a machine includes accepting its model library: verify one installed
        # package through the same Hub route the Models screen's Verify button calls, so a
        # copied or hard-linked library that reads as "file changed" is caught here.
        for worker, rows in sorted(packages.items()):
            if not rows:
                continue
            candidate = rows[0]
            status, verified = http(
                base, f"/api/hub/model-packages/{candidate['package_id']}/verify",
                token, method="POST")
            receipt["verify"] = {"package_id": candidate["package_id"],
                                 "worker": worker, "status": status,
                                 "state": verified.get("state"),
                                 "dispatch_state": verified.get("dispatch_state"),
                                 "detail": verified.get("detail") if status != 200 else None}
            break

        kinds = [kind.strip() for kind in arguments.kinds.split(",") if kind.strip()]
        if "subtitle" in kinds:
            # The Hub's transcription route takes the repository name, and Turbo is the
            # default the app offers, so prefer it when several Whisper sizes are installed.
            subtitle_choices = packages.get("subtitle") or []
            package = next((row for row in subtitle_choices
                            if row["package_id"] == "whisper-turbo"),
                           subtitle_choices[0] if subtitle_choices else None)
            if package is None:
                receipt["skipped"].append(
                    {"kind": "subtitle", "reason": "no verified Whisper package in this library"})
            else:
                audio = arguments.audio.read_bytes() if arguments.audio else tone()
                started = time.time()
                status, submitted = http(
                    base, "/api/hub/transcription/jobs", token, method="POST",
                    fields={"model": package["model_id"], "item_ids": "measure-1",
                            "episode": "measurement"},
                    files={"files": ("measure.wav",
                                     audio, "audio/wav")})
                run = {"kind": "subtitle", "model": package["model_id"],
                       "package_id": package["package_id"], "submit_status": status,
                       "input_sha256": hashlib.sha256(audio).hexdigest(),
                       "input_bytes": len(audio)}
                if status == 200:
                    batch = submitted.get("batch_id")
                    final = wait_for(base, token, batch, transcription=True)
                    run["batch_id"] = batch
                    run["status"] = final.get("status")
                    run["items"] = [job_metrics(item) for item in (final.get("items") or [])]
                else:
                    run["status"] = "refused"
                    run["detail"] = submitted
                run["wall_seconds"] = round(time.time() - started, 3)
                receipt["runs"].append(run)

        if "image" in kinds:
            package = next(iter(packages.get("image") or []), None)
            if package is None:
                receipt["skipped"].append(
                    {"kind": "image", "reason": "no verified FLUX package in this library"})
            else:
                started = time.time()
                status, submitted = http(base, "/api/hub/jobs", token, method="POST", payload={
                    "modality": "image", "model": package["model_id"],
                    "clientRequestId": uuid.uuid4().hex,
                    "items": [{"prompt": arguments.prompt, "params": {}}]})
                run = {"kind": "image", "model": package["model_id"],
                       "package_id": package["package_id"], "submit_status": status}
                if status == 200:
                    batch = submitted.get("batch_id")
                    final = wait_for(base, token, batch, transcription=False)
                    run["batch_id"] = batch
                    run["items"] = [job_metrics(item) for item in (final.get("items") or [])]
                else:
                    run["detail"] = submitted
                run["wall_seconds"] = round(time.time() - started, 3)
                receipt["runs"].append(run)

        if "voice" in kinds:
            candidates = packages.get("voice") or []
            # Prefer a clone model: this receipt is about the shipped cloning path.
            package = next((row for row in candidates
                            if "base" in (row.get("model_id") or "").lower()),
                           candidates[0] if candidates else None)
            if package is None:
                receipt["skipped"].append(
                    {"kind": "voice", "reason": "no verified voice package in this library"})
            elif arguments.reference is None or not arguments.transcript.strip():
                receipt["skipped"].append({
                    "kind": "voice",
                    "reason": "a voice clone needs --reference and --transcript; a clone fails "
                              "closed on a transcript mismatch, so this run is not inferred"})
            else:
                audio = arguments.reference.read_bytes()
                status, staged = http(
                    base, "/api/hub/voice-references", token, method="POST",
                    fields={"transcript": arguments.transcript, "language": "en"},
                    files={"audio": (arguments.reference.name, audio, "audio/wav")})
                run = {"kind": "voice", "model": package["model_id"],
                       "package_id": package["package_id"], "stage_status": status,
                       "reference_sha256": hashlib.sha256(audio).hexdigest()}
                if status == 201:
                    started = time.time()
                    status, submitted = http(base, "/api/hub/jobs", token, method="POST",
                                             payload={
                        "modality": "voice", "model": package["model_id"],
                        "clientRequestId": uuid.uuid4().hex,
                        "voice_reference_asset_id": staged["asset_id"],
                        "items": [{"text": arguments.transcript, "params": {}}]})
                    run["submit_status"] = status
                    if status == 200:
                        batch = submitted.get("batch_id")
                        final = wait_for(base, token, batch, transcription=False)
                        run["batch_id"] = batch
                        run["items"] = [job_metrics(item) for item in (final.get("items") or [])]
                    else:
                        run["detail"] = submitted
                    run["wall_seconds"] = round(time.time() - started, 3)
                else:
                    run["detail"] = staged
                receipt["runs"].append(run)

        status, stats = http(base, "/api/hub/stats", token)
        if status == 200:
            receipt["hub_stats"] = stats
        status, health = http(base, "/api/health", token)
        receipt["hub_health"] = health
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        temporary.cleanup()

    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"label": receipt["label"], "runs": [
        {"kind": run["kind"], "status": run.get("status") or run.get("submit_status"),
         "wall_seconds": run.get("wall_seconds")} for run in receipt["runs"]],
        "skipped": receipt["skipped"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
