"""ST-04 real packaged receipts for the two Qwen3-TTS Base clone tiers.

This operator/qualification harness never runs in the test suite.  It:

  1. builds an offline HF-cache-shaped model library by hard-linking the
     already checksum-verified files in ``model-library/`` (no copy, no
     download, no network);
  2. synthesizes one real reference clip with macOS ``say`` plus a
     deterministic exact transcript;
  3. assembles the packaged Voice worker boundary (worker entry point +
     release manifest + isolated runtime symlink + this worktree's backend);
  4. runs the worker offline and submits real clone jobs for QWEN06 and
     QWEN17 through ``/api/generate/txt2speech/reference``;
  5. records job state, quality evidence, output bytes/hash/duration, peak
     RSS, and the single-slot behaviour of the internal Whisper check.

Usage::

    .build/voice-runtime/bin/python3 \
        components/voice/qualification/st04_real_qwen_base_proof.py \
        --proof-dir .build/st04-real-proof \
        --model-library model-library \
        --runtime /path/to/qualified/voice/runtime \
        [--only qwen06]

Nothing here is a claim about the Hub path; the Hub contract is proven by
``tests/test_hub_voice_references.py`` and quoted in ``qualification/st04.md``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER_ENTRY = REPO_ROOT / "packaging" / "voice_worker.py"
VOICE_SOURCE = REPO_ROOT / "components" / "voice" / "app"

REFERENCE_TEXT = (
    "The river runs quietly past the old stone bridge, and the morning light "
    "settles on the water."
)
JOB_TEXT = "Studio Hub turns written words into a clear, steady voice for every listener."

MODELS = {
    "qwen06": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        "manifest": "qwen3-tts-12hz-0-6b-base-8bit.json",
        "library_dir": "qwen3-tts-12hz-0-6b-base-8bit",
    },
    "qwen17": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "manifest": "qwen3-tts-12hz-1-7b-base-8bit.json",
        "library_dir": "qwen3-tts-12hz-1-7b-base-8bit",
    },
    "whisper": {
        "repo": "mlx-community/whisper-large-v3-turbo",
        "manifest": "whisper-turbo.json",
        "library_dir": "whisper-turbo",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hf_cache_dir(root: Path, repo: str) -> Path:
    return root / "hub" / ("models--" + repo.replace("/", "--"))


def link_extra_cache(source_root: Path, cache_root: Path, log) -> list[str]:
    """Hard-link additional read-only HF cache entries (shared tokenizers).

    The narrow WhisperTokenizer path resolves ``openai/whisper-large-v3-turbo``
    from the cache without a network fetch.  Offline qualification must supply
    that exact snapshot, so an existing verified cache tree is linked in.
    """
    copied = []
    source_hub = source_root / "hub"
    if not source_hub.is_dir():
        return copied
    for repo_dir in sorted(source_hub.glob("models--*")):
        target = cache_root / "hub" / repo_dir.name
        if target.exists():
            continue
        shutil.copytree(repo_dir, target, copy_function=os.link)
        copied.append(repo_dir.name)
    if copied:
        log(f"linked shared cache entries: {', '.join(copied)}")
    return copied


def link_snapshot(*, library: Path, cache_root: Path, spec: dict, log) -> dict:
    """Hard-link one verified library package into HF-cache snapshot shape."""
    manifest = json.loads((REPO_ROOT / "model-manifests" / spec["manifest"]).read_text())
    revision = manifest["revision"]
    package_root = library / spec["library_dir"] / revision
    package = package_root / "model"
    if not package.is_dir():
        raise SystemExit(f"missing verified package: {package}")
    target = hf_cache_dir(cache_root, spec["repo"])
    snapshot = target / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)
    (target / "refs").mkdir(parents=True, exist_ok=True)
    # The worker's cache contract compares exact revision bytes without newline.
    (target / "refs" / "main").write_bytes(revision.encode())
    linked = 0
    total = 0
    for entry in manifest["files"]:
        published = entry["path"]
        # Where the verified library actually keeps the bytes: model files live
        # under ``model/`` (or use an explicit primary-file alias), while
        # companion processor files sit beside it at the revision root.
        library_path = entry.get("source_path") or published
        source = package_root / library_path
        if not source.is_file():
            source = package_root / "model" / library_path
        if not source.is_file():
            raise SystemExit(f"manifest file missing from library: {source}")
        # Where the runtime expects them: companion roles collapse to the
        # snapshot root, everything else keeps its published relative path.
        if published.startswith("companions/"):
            destination_relative = published.split("/", 2)[2]
        elif published.startswith("model/"):
            destination_relative = published[len("model/"):]
        else:
            destination_relative = published
        destination = snapshot / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
        linked += 1
        total += source.stat().st_size
    log(
        f"linked {spec['repo']} rev={revision} files={linked} bytes={total} "
        f"snapshot={snapshot}"
    )
    return {"repo": spec["repo"], "revision": revision, "files": linked, "bytes": total,
            "snapshot": str(snapshot)}


def make_reference_clip(destination: Path, *, voice: str) -> dict:
    """Synthesize one real local speech reference with a deterministic transcript.

    macOS ``say`` produces real 24 kHz mono speech; the transcript is exactly the
    sentence that was spoken, so the Qwen reference alignment check has a
    truthful target instead of a guessed one.
    """
    import soundfile as sf

    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["say", "-v", voice, "-o", str(destination),
         "--data-format=LEI16@24000", REFERENCE_TEXT],
        check=True,
    )
    audio, sample_rate = sf.read(str(destination), dtype="float32", always_2d=True)
    duration = len(audio) / float(sample_rate)
    if duration < 3.0:
        raise SystemExit(
            f"reference clip is only {duration:.2f}s; the audited Base contract needs >= 3s"
        )
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "transcript": REFERENCE_TEXT,
        "voice": voice,
        "sample_rate_hz": sample_rate,
        "duration_seconds": round(duration, 3),
    }


def build_bundle(proof: Path, runtime: Path, release_manifest: Path | None) -> tuple[Path, Path]:
    resources = proof / "Voice.app" / "Contents" / "Resources"
    if resources.exists():
        shutil.rmtree(resources)
    (resources / "runtimes").mkdir(parents=True)
    (resources / "voice").mkdir(parents=True)
    shutil.copy2(WORKER_ENTRY, resources / "voice_worker.py")
    # The staged backend is this worktree's adopted source at the receipt commit.
    shutil.copytree(VOICE_SOURCE, resources / "voice" / "app",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    manifest_target = resources / "release-manifest.json"
    if release_manifest is not None and release_manifest.is_file():
        shutil.copy2(release_manifest, manifest_target)
    else:
        manifest_target.write_text(json.dumps({
            "schema": "studiohub.standalone-release", "schema_version": 1,
            "version": "st04-real-proof",
            "development_build": True,
            "qualification": "unqualified-local-proof",
            "components": {"voice": {}},
            "runtime_sha256": "unrecorded-in-this-proof",
        }, indent=1))
    # Symlink the already-audited runtime instead of copying it; this proof is
    # about the model path, not another relocation claim.
    os.symlink(runtime, resources / "runtimes" / "voice")
    return resources, resources / "release-manifest.json"


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def request(url: str, token: str | None = None, *, data=None, method="GET",
            headers=None, raw=False):
    merged = dict(headers or {})
    if token:
        merged["Authorization"] = f"Bearer {token}"
    payload = None
    if data is not None:
        payload = json.dumps(data).encode()
        merged["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, method=method, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            body = response.read()
            if raw:
                return response.status, body, dict(response.headers)
            return response.status, json.loads(body or b"{}")
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body or b"{}")
        except ValueError:
            return error.code, {"detail": body.decode("utf-8", "replace")}


def wait_ready(log_path: Path, process: subprocess.Popen, timeout: float) -> str:
    deadline = time.time() + timeout
    pattern = re.compile(r"Uvicorn running on (http://127\.0\.0\.1:\d+)")
    while time.time() < deadline:
        text = log_path.read_text(errors="replace") if log_path.exists() else ""
        match = pattern.search(text)
        if match and "Application startup complete." in text:
            return match.group(1)
        if process.poll() is not None:
            raise SystemExit(f"worker exited {process.returncode}; see {log_path}")
        time.sleep(0.2)
    raise SystemExit(f"worker did not become ready; see {log_path}")


def rss_kb(pid: int) -> int:
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, check=True).stdout.strip()
        return int(out or 0)
    except Exception:
        return 0


def submit_reference_job(base: str, token: str, *, repo: str, reference: Path,
                         transcript: str, text: str, language: str = "en",
                         seed: int = 42) -> dict:
    boundary = "----st04proof" + hashlib.sha256(repo.encode()).hexdigest()[:16]
    body = json.dumps({
        "repo": repo, "text": text, "language": language, "seed": seed,
        "ref_transcript": transcript, "client_request_id": f"st04:{repo}:{seed}",
    }).encode()
    parts = []
    def field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    field("request_json", body.decode())
    field("transcript_segments_json", "[]")
    field("source_sha256", "")
    field("reference_expires_at", str(time.time() + 3600))
    parts.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; "
         f"filename=\"reference.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
        + reference.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    payload = b"".join(parts)
    req = urllib.request.Request(
        base + "/api/generate/txt2speech/reference", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"clone submit refused {error.code}: {error.read().decode('utf-8', 'replace')[:2000]}"
        ) from error


def poll_job(base: str, token: str, job_id: str, *, timeout: float) -> tuple[dict, list]:
    deadline = time.time() + timeout
    samples = []
    job = {}
    while time.time() < deadline:
        status, job = request(f"{base}/api/generate/jobs/{job_id}", token)
        samples.append({
            "at": round(time.time(), 3), "state": job.get("state"),
            "progress": job.get("progress"),
        })
        if job.get("state") in {"done", "error", "cancelled", "uncertain"}:
            break
        time.sleep(2)
    return job, samples


def run_model(base: str, token: str, worker_pid: int, spec: dict, reference: dict,
              proof: Path, *, timeout: float) -> dict:
    started = time.time()
    peak_rss = 0
    accepted = submit_reference_job(
        base, token, repo=spec["repo"], reference=Path(reference["path"]),
        transcript=reference["transcript"], text=JOB_TEXT,
    )
    job_id = accepted["job"]["id"]
    stop = time.time() + timeout
    samples = []
    job = {}
    while time.time() < stop:
        _status, job = request(f"{base}/api/generate/jobs/{job_id}", token)
        peak_rss = max(peak_rss, rss_kb(worker_pid))
        samples.append({"at": round(time.time(), 3), "state": job.get("state"),
                        "progress": job.get("progress")})
        if job.get("state") in {"done", "error", "cancelled", "uncertain"}:
            break
        time.sleep(2)
    record = {
        "repo": spec["repo"],
        "model_revision": spec["revision"],
        "requested_at": started,
        "job_id": job_id,
        "state": job.get("state"),
        "error": job.get("error"),
        "error_code": job.get("error_code"),
        "quality_validation": job.get("quality_validation"),
        "quality_retry_count": job.get("quality_retry_count"),
        "quality_retry_history": job.get("quality_retry_history"),
        "resolved_seed": job.get("resolved_seed"),
        "duration_seconds": job.get("duration_seconds"),
        "elapsed_seconds": round(time.time() - started, 3),
        "peak_worker_rss_bytes": peak_rss * 1024,
        "samples": samples,
        "output": None,
    }
    if job.get("state") == "done":
        status, audio, headers = request(
            f"{base}/api/generate/jobs/{job_id}/audio", token, raw=True,
        )
        digest = hashlib.sha256(audio).hexdigest()
        output = proof / f"{spec['repo'].replace('/', '_')}.wav"
        output.write_bytes(audio)
        with wave.open(str(output), "rb") as stream:
            frames = stream.getnframes()
            rate = stream.getframerate()
        record["output"] = {
            "status": status,
            "path": str(output),
            "bytes": len(audio),
            "sha256": digest,
            "header_sha256": headers.get("X-Content-SHA256"),
            "hash_matches_header": headers.get("X-Content-SHA256") == digest,
            "duration_seconds": round(frames / float(rate), 3),
        }
        # The internal Whisper check must have produced evidence under the same
        # lease; a missing record means the model was never validated.
        record["quality_check_ran"] = bool(job.get("quality_validation"))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof-dir", type=Path, required=True)
    parser.add_argument("--model-library", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, default=None,
                        help="Shipped release manifest to copy into the proof bundle")
    parser.add_argument("--only", choices=sorted(MODELS), action="append")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--offline-cache", type=Path, action="append", default=[],
                        help="Existing HF cache root whose entries are linked in for offline companions")
    parser.add_argument("--reference-voice", default="Daniel")
    args = parser.parse_args()

    proof = args.proof_dir.expanduser().resolve()
    proof.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []

    def log(message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        log_lines.append(line)

    cache_root = proof / "library"
    reference_dir = proof / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    wanted = args.only or ["qwen06", "qwen17"]
    specs = {}
    for key in set(wanted) | {"whisper"}:
        specs[key] = link_snapshot(library=args.model_library.expanduser().resolve(),
                                   cache_root=cache_root, spec=MODELS[key], log=log)

    for extra in args.offline_cache:
        link_extra_cache(extra.expanduser().resolve(), cache_root, log)

    reference = make_reference_clip(reference_dir / "reference.wav",
                                    voice=args.reference_voice)
    log(f"reference clip {reference['bytes']} bytes {reference['duration_seconds']}s "
        f"sha256={reference['sha256']}")

    resources, manifest = build_bundle(
        proof, args.runtime.expanduser().resolve(), args.release_manifest,
    )
    state = proof / "state"
    state.mkdir(parents=True, exist_ok=True)
    # The worker requires the owner token to be a private file inside its data dir.
    token_file = state / ".hub_token"
    token = "st04-real-proof-token-value-0123456789"
    token_file.write_text(token)
    os.chmod(token_file, 0o600)
    port = free_port()
    worker_log = proof / "voice-worker.log"
    ready_file = state / "voice" / "voice-ready.json"
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "HOME": os.environ.get("HOME", ""),
    }
    command = [
        str(args.runtime.expanduser().resolve() / "bin" / "python3"), "-I", "-B",
        str(resources / "voice_worker.py"),
        # The library root IS the HF cache root: hf_home()/hub/ holds the snapshots.
        "--data-dir", str(state), "--models-dir", str(cache_root),
        "--port", str(port), "--ready-file", str(ready_file),
        "--hub-token-file", str(token_file),
        "--offline",
    ]
    log("worker command: " + " ".join(command))
    with worker_log.open("w") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                   env=environment, cwd=str(state))
    base = None
    records = []
    try:
        base = wait_ready(worker_log, process, 120)
        log(f"worker ready at {base} pid={process.pid}")
        _status, catalog_payload = request(base + "/api/catalog", token)
        cache_states = {
            row["repo"]: (row.get("cache") or {}).get("state")
            for row in catalog_payload.get("models", [])
            if row.get("repo") in {MODELS[key]["repo"] for key in set(wanted) | {"whisper"}}
        }
        log(f"catalog cache states: {json.dumps(cache_states)}")
        for key in wanted:
            log(f"--- real clone run: {key} {MODELS[key]['repo']}")
            record = run_model(base, token, process.pid, specs[key], reference,
                               proof, timeout=args.timeout)
            log(f"    state={record['state']} elapsed={record['elapsed_seconds']}s "
                f"quality_ran={record.get('quality_check_ran')} "
                f"rss={record['peak_worker_rss_bytes']} bytes")
            records.append(record)
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()

    receipt = {
        "schema": "studiohub.st04-qwen-base-real-proof",
        "schema_version": 1,
        "created_at": time.time(),
        "host": {
            "platform": subprocess.run(["sw_vers", "-productVersion"], capture_output=True,
                                       text=True).stdout.strip(),
            "machine": subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True,
                                      text=True).stdout.strip(),
            "chip": subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                   capture_output=True, text=True).stdout.strip(),
            "memory_bytes": int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                               capture_output=True, text=True).stdout.strip()),
        },
        "runtime": str(args.runtime),
        "python": subprocess.run([str(args.runtime.expanduser().resolve() / "bin" / "python3"),
                                  "-VV"], capture_output=True, text=True).stdout.strip(),
        "worker_entry": str(WORKER_ENTRY),
        "voice_source": str(VOICE_SOURCE),
        "reference": reference,
        "job_text": JOB_TEXT,
        "models": {key: specs[key] for key in specs},
        "runs": records,
        "log": log_lines,
    }
    receipt_path = proof / "st04-real-proof.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(f"\nreceipt: {receipt_path}")
    failed = [row for row in records if row["state"] != "done"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
