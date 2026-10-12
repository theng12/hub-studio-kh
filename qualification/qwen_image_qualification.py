#!/usr/bin/env python3
"""Measure one pinned Qwen Image 2.1 4-bit package through the packaged Image worker.

The runner starts the app bundle's own ``image_worker.py`` with private state and a copy-on-write
clone of only the selected package. It never reads the running Hub's token or points throwaway
state at a real model library. Use the double-clickable 16 GB or 24 GB command beside this file.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    from qualification import library_guard, ssd_wear
except ImportError:  # copied into a kit's Technical folder
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import library_guard  # type: ignore[no-redef]
    import ssd_wear  # type: ignore[no-redef]


GIB = 1024**3
DECIMAL_GB = 1_000_000_000
PRESSURE = {1: "normal", 2: "warning", 4: "critical"}
TERMINAL = {"done", "error", "cancelled"}
DEFAULT_PROMPT = (
    'A polished cinematic travel poster for Phnom Penh at blue hour. At the top, large clean '
    'gold lettering reads exactly "PHNOM PENH". Beneath it, smaller lettering reads exactly '
    '"NIGHT MARKET 2026". The riverfront glows with warm lanterns, detailed architecture and '
    'reflections on the water. Keep every requested letter legible and add no other text.'
)


@dataclass(frozen=True)
class Profile:
    tier_gb: int
    package_id: str
    repo: str
    revision: str
    quantization: str
    engine: str
    minimum_macos: tuple[int, int]
    label: str


@dataclass(frozen=True)
class HostStatus:
    code: str
    reason: str
    target_tier_verified: bool = False


@dataclass(frozen=True)
class Workload:
    name: str
    width: int
    height: int
    steps: int
    process_cold: bool = False


TURBO_PACKAGE_ID = "qwen-image-2-1-turbo-mflux-q4"
TURBO_REPO = "mflux-community/qwen-image-2-1-turbo-mflux-q4"
TURBO_REVISION = "bda8951ec6b8cc288a961432fe15c22433521a1d"


def profile_for_tier(tier: int, *, turbo: bool = False) -> Profile:
    if turbo:
        if tier not in (16, 24, 32):
            raise SystemExit("Turbo tier must be 16, 24 or 32; 8 GB is unsupported and remains NOT MEASURED")
        return Profile(
            tier, TURBO_PACKAGE_ID, TURBO_REPO, TURBO_REVISION,
            "MLX 4-bit", "image-mflux", (14, 0),
            "Qwen Image 2.1 Turbo Low-RAM MLX 4-bit")
    profiles = {
        24: Profile(
            24, "qwen-image-2-1-mlx-serve-4bit",
            "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit",
            "88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38",
            "MLX affine 4-bit", "image-mlx-serve", (26, 2),
            "Qwen Image 2.1 MLX affine 4-bit (native mlx-serve)"),
        16: Profile(
            16, "qwen-image-2-1-mflux-q4",
            "mlx-community/Qwen-Image-2.1-mflux-q4",
            "746a58556820933a2df5c75887a2570f1ad200c0",
            "MLX affine Q4", "image-mflux", (14, 0),
            "Qwen Image 2.1 MLX affine Q4 (mflux low-memory)"),
    }
    try:
        return profiles[int(tier)]
    except (KeyError, ValueError):
        raise SystemExit("tier must be 16 or 24; 8 GB is unsupported and remains NOT MEASURED")


def version_tuple(value: str) -> tuple[int, ...]:
    parts = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    return tuple(int(part or 0) for part in parts.groups()) if parts else ()


def host_status(profile: Profile, memory_bytes: int, macos: tuple[int, ...],
                *, allow_non_target: bool = False) -> HostStatus:
    if memory_bytes and memory_bytes <= 9 * GIB:
        return HostStatus("NOT MEASURED", "8 GB is unsupported by this Qwen qualification; no fit claim is made")
    if not macos or macos < profile.minimum_macos:
        need = ".".join(str(value) for value in profile.minimum_macos)
        return HostStatus("NOT MEASURED", f"{profile.label} requires macOS {need} or later")
    gib = memory_bytes / GIB if memory_bytes else 0
    physical_match = memory_bytes > 0 and abs(memory_bytes - profile.tier_gb * GIB) <= GIB // 2
    if not physical_match:
        if not allow_non_target:
            return HostStatus(
                "NOT MEASURED",
                f"this is the {profile.tier_gb} GB qualification, but this Mac reports {gib:.1f} GB")
        return HostStatus(
            "READY", f"non-target trial on {gib:.1f} GB; this does not qualify {profile.tier_gb} GB")
    return HostStatus("READY", "host matches the requested physical qualification tier", True)


def workloads(stress_40: bool, *, turbo: bool = False) -> list[Workload]:
    if turbo and stress_40:
        raise SystemExit("--stress-40 is unavailable for Qwen Image 2.1 Turbo MLX 4-bit")
    steps = 8 if turbo else 16
    cases = [
        Workload("cold-square", 1024, 1024, steps, True),
        Workload("warm-square", 1024, 1024, steps),
        Workload("warm-landscape", 1280, 720, steps),
        Workload("warm-portrait", 720, 1280, steps),
    ]
    if stress_40:
        cases.extend([
            Workload("stress-square", 1024, 1024, 40),
            Workload("stress-landscape", 1280, 720, 40),
            Workload("stress-portrait", 720, 1280, 40),
        ])
    return cases


def run_text(command: list[str], timeout: float = 15) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def hardware_facts() -> dict:
    memory = run_text(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip()
    chip = run_text(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    if not chip:
        chip = run_text(["/usr/sbin/sysctl", "-n", "hw.model"]).strip()
    macos = run_text(["/usr/bin/sw_vers", "-productVersion"]).strip()
    return {
        "memory_bytes": int(memory) if memory.isdigit() else 0,
        "chip": chip or "unknown",
        "macos": macos or platform.mac_ver()[0] or "unknown",
        "machine": platform.machine(),
    }


def kit_root(script: Path) -> Path:
    here = script.resolve().parent
    return here.parent if here.name == "Technical" else here


def find_app(explicit: Path | None, kit: Path) -> Path:
    candidates = [explicit, Path(os.environ["STUDIOHUB_APP"]) if os.environ.get("STUDIOHUB_APP") else None,
                  kit / "Studio Hub.app", Path("/Applications/Studio Hub.app"),
                  Path.home() / "Applications/Studio Hub.app"]
    marker = Path("Contents/Resources/image_worker.py")
    for candidate in candidates:
        if candidate and (candidate.expanduser() / marker).is_file():
            return candidate.expanduser().resolve()
    raise SystemExit("NOT MEASURED: no complete Studio Hub.app was found on the kit or this Mac")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_tree_digest(root: Path) -> str:
    """The staging pipeline's path/type/content identity; modes are deliberately excluded."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.readlink(path).encode() + b"\0")
        else:
            digest.update(b"file\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _runtime_file_records(root: Path) -> list[dict]:
    manifest = Path("manifest/runtime-files.json")
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if relative == manifest:
            continue
        if path.is_symlink():
            records.append({"path": relative.as_posix(), "type": "symlink",
                            "target": os.readlink(path),
                            "mode": f"{stat.S_IMODE(path.lstat().st_mode):04o}"})
        elif path.is_file():
            records.append({"path": relative.as_posix(), "type": "file",
                            "sha256": sha256_file(path), "size": path.stat().st_size,
                            "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}"})
    return records


def verify_runtime_file_manifest(root: Path) -> dict:
    path = root / "manifest/runtime-files.json"
    try:
        payload = json.loads(path.read_text())
        rows = payload["files"]
        if not isinstance(rows, list):
            raise TypeError("files is not a list")
        expected = {}
        for row in rows:
            relative = Path(row["path"])
            if (not isinstance(row, dict) or relative.is_absolute() or ".." in relative.parts
                    or relative.as_posix() in expected):
                raise ValueError(f"unsafe or duplicate path {relative}")
            expected[relative.as_posix()] = row
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"NOT MEASURED: packaged runtime-files.json is unreadable: {exc}")
    actual = {row["path"]: row for row in _runtime_file_records(root)}
    mismatches = []
    for relative in sorted(expected.keys() | actual.keys()):
        if relative not in actual:
            mismatches.append(f"{relative}: missing")
        elif relative not in expected:
            mismatches.append(f"{relative}: unexpected")
        elif expected[relative] != actual[relative]:
            fields = sorted(key for key in expected[relative].keys() | actual[relative].keys()
                            if expected[relative].get(key) != actual[relative].get(key))
            mismatches.append(f"{relative}: {','.join(fields)}")
    if mismatches:
        raise SystemExit("NOT MEASURED: packaged runtime-files.json does not match the Image "
                         "runtime tree: " + "; ".join(mismatches[:3]))
    return {
        "runtime_files_verified": True,
        "runtime_files_manifest_sha256": sha256_file(path),
        "runtime_files_count": len(actual),
        "runtime_files_bytes": sum(row.get("size", 0) for row in actual.values()),
    }


def validate_bundle(app: Path, profile: Profile) -> dict:
    resources = app / "Contents/Resources"
    runtime_root = resources / "runtimes/image"
    required = [resources / "image_worker.py", resources / "runtimes/image/bin/python3",
                resources / "release-manifest.json",
                resources / "model-manifests" / f"{profile.package_id}.json",
                runtime_root / "manifest/runtime-files.json"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("NOT MEASURED: the packaged app is incomplete; missing " + ", ".join(missing))
    try:
        release = json.loads(required[2].read_text())
        manifest = json.loads(required[3].read_text())
        runtime = release["components"]["image"]["runtime_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"NOT MEASURED: packaged Image identity is unreadable: {exc}")
    expected = {"package_id": profile.package_id, "model_id": profile.repo,
                "revision": profile.revision}
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SystemExit(
                f"NOT MEASURED: {profile.package_id} manifest {key} is {manifest.get(key)!r}, expected {value!r}")
    if manifest.get("engine") not in (None, profile.engine):
        raise SystemExit(
            f"NOT MEASURED: {profile.package_id} manifest engine is {manifest.get('engine')!r}, expected {profile.engine!r}")
    if not isinstance(runtime, str) or not re.fullmatch(r"[0-9a-f]{64}", runtime):
        raise SystemExit("NOT MEASURED: release manifest lacks the exact Image runtime identity")
    verified_files = verify_runtime_file_manifest(runtime_root)
    actual_runtime = runtime_tree_digest(runtime_root)
    if actual_runtime != runtime:
        raise SystemExit(
            f"NOT MEASURED: packaged Image runtime tree SHA-256 is {actual_runtime}, expected {runtime}")
    return {
        "app": str(app), "version": str(release.get("version") or "unknown"),
        "runtime_sha256": runtime, "manifest_sha256": sha256_file(required[3]),
        "package": {key: manifest.get(key) for key in
                    ("package_id", "model_id", "revision", "engine", "quantization")},
        "manifest_file_count": len(manifest.get("files") or []),
        "manifest_model_bytes": sum(int(row.get("size") or 0)
                                    for row in (manifest.get("files") or [])
                                    if isinstance(row, dict)),
        **verified_files,
    }


def available_memory_reader(app: Path):
    """The packaged app's own admission-headroom arithmetic, evaluated in its bundled Voice Python."""
    source = app / "Contents/Resources/components/host_memory.py"
    if not source.is_file():
        raise SystemExit(f"NOT MEASURED: packaged host-memory formula is missing at {source}")
    specification = importlib.util.spec_from_file_location(
        "_studiohub_qualification_host_memory", source)
    if specification is None or specification.loader is None:
        raise SystemExit(f"NOT MEASURED: packaged host-memory formula cannot be loaded from {source}")
    module = importlib.util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
        import psutil
    except Exception as exc:
        raise SystemExit(f"NOT MEASURED: packaged admission headroom cannot be sampled: {exc}")

    def read() -> int | None:
        try:
            return int(module.available_bytes(psutil.virtual_memory()))
        except Exception:
            return None

    return read


class AvailableMemoryTracker:
    """Start, lowest, and end headroom using the Hub's exact available-bytes definition."""

    def __init__(self, reader) -> None:
        self.reader = reader
        self.start = self._read()
        self.minimum = self.start
        self.end = None
        self.samples = int(self.start is not None)
        self.errors = int(self.start is None)

    def _read(self) -> int | None:
        try:
            value = self.reader()
            return max(0, int(value)) if value is not None else None
        except Exception:
            return None

    def sample(self) -> None:
        value = self._read()
        if value is not None:
            self.samples += 1
            self.minimum = value if self.minimum is None else min(self.minimum, value)
        else:
            self.errors += 1

    def finish(self) -> None:
        self.end = self._read()
        if self.end is not None:
            self.samples += 1
            self.minimum = self.end if self.minimum is None else min(self.minimum, self.end)
        else:
            self.errors += 1

    def fields(self) -> dict:
        def gb(value: int | None) -> float | None:
            return None if value is None else round(value / DECIMAL_GB, 3)

        return {"available_gb_start": gb(self.start),
                "available_gb_min": gb(self.minimum),
                "available_gb_end": gb(self.end),
                "available_memory_samples": self.samples,
                "available_memory_sample_errors": self.errors}


def clone_package(source: Path, destination: Path, package_id: str) -> Path:
    source = source.expanduser().resolve()
    if not (source / package_id).is_dir():
        raise SystemExit(f"NOT MEASURED: {package_id} is absent from {source}")
    return library_guard.clone(source, destination, [package_id])


def worker_command(app: Path, data: Path, library: Path, token: Path, ready: Path,
                   parent_pid: int, qualification_package: str | None = None) -> list[str]:
    resources = app / "Contents/Resources"
    command = [
        str(resources / "runtimes/image/bin/python3"), "-I", "-B",
        str(resources / "image_worker.py"), "--data-dir", str(data),
        "--models-dir", str(library), "--ready-file", str(ready),
        "--hub-token-file", str(token), "--parent-pid", str(parent_pid),
        "--port", "0", "--offline",
    ]
    if qualification_package is not None:
        command.extend(["--qualification-package", qualification_package])
    return command


def owns_process_group(pid: int) -> bool:
    try:
        return pid > 1 and os.getpgid(pid) == pid
    except (OSError, ProcessLookupError):
        return False


def process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_worker(process: subprocess.Popen, grace: float = 15) -> None:
    # The packaged worker makes its PID the process-group id before it becomes ready. The group
    # may still contain its native leaf after the leader exits, so inspect the known group id even
    # when Popen.poll() has already reaped the worker.
    group = owns_process_group(process.pid) or process_group_exists(process.pid)
    if group:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    elif process.poll() is None:
        process.terminate()  # this exact child was created by this runner
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if process.poll() is not None and (not group or not process_group_exists(process.pid)):
            return
        time.sleep(0.1)
    if group and process_group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    if process.poll() is None:
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            if group and process_group_exists(process.pid):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


class ForeignProcessError(RuntimeError):
    pass


foreign_model_processes = library_guard.foreign_model_processes

def spawn_private_worker(command: list[str], *, stdout, cwd: Path, env: dict,
                         process_listing: str | None = None,
                         popen=subprocess.Popen) -> subprocess.Popen:
    """Recheck immediately before spawn; never stop an owner's Hub or ordinary Image job."""
    busy = foreign_model_processes(process_listing)
    if busy:
        raise ForeignProcessError(
            "another Studio Hub/Image process appeared before the qualification worker started: "
            + "; ".join(busy))
    return popen(command, stdout=stdout, stderr=subprocess.STDOUT, cwd=cwd, env=env)


def request_json(base: str, token: str, method: str, path: str,
                 body: dict | None = None, timeout: float = 60,
                 raw: bool = False) -> tuple[int, object]:
    request = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Hub-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
            return response.status, content if raw else json.loads(content or b"{}")
    except urllib.error.HTTPError as exc:
        content = exc.read()
        try:
            value: object = json.loads(content or b"{}")
        except ValueError:
            value = content.decode("utf-8", "replace")[:1000]
        return exc.code, value


def wait_ready(process: subprocess.Popen, ready: Path, token: str,
               timeout: float = 90) -> tuple[str, dict]:
    deadline = time.monotonic() + timeout
    last_error = "ready file not written"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"packaged Image worker exited with status {process.returncode}")
        try:
            value = json.loads(ready.read_text())
            base = str(value["url"])
            status, health = request_json(base, token, "GET", "/api/health", timeout=5)
            if status == 200 and isinstance(health, dict):
                return base, value
            last_error = f"health returned HTTP {status}"
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"packaged Image worker did not become ready: {last_error}")


def verify_qualification_worker(base: str, token: str, profile: Profile) -> dict:
    """Confirm the private worker granted only the manifest-blocked candidate trial."""
    status, payload = request_json(base, token, "GET", "/api/catalog", timeout=30)
    rows = payload.get("models") if status == 200 and isinstance(payload, dict) else None
    row = next((item for item in (rows or [])
                if isinstance(item, dict) and item.get("package_id") == profile.package_id), None)
    if (row is None or row.get("production_allowed") is not False
            or row.get("qualification_allowed") is not True
            or row.get("execution_ready") is not True):
        raise RuntimeError(
            f"packaged Image worker did not grant the isolated qualification permission for "
            f"{profile.package_id}")
    return row


def run_cancellation_probe(base: str, token: str, profile: Profile, prompt: str, seed: int,
                           output_dir: Path, *, deadline: float = 60.0,
                           settle_deadline: float = 60.0, poll_interval: float = 0.1) -> dict:
    """Prove DELETE stops a real middle denoise callback before any artifact is delivered."""
    started = time.monotonic()
    receipt: dict = {"status": "failed", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    def fail(reason: str) -> dict:
        receipt["reason"] = reason
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return receipt

    def poll_job(job_id: str, until: float) -> dict | None:
        timeout = max(0.001, min(5.0, until - time.monotonic()))
        status, payload = request_json(
            base, token, "GET", f"/api/generate/jobs/{job_id}", timeout=timeout)
        if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("job"), dict):
            receipt["last_poll"] = {"http_status": status, "payload": str(payload)[:500]}
            return None
        return payload["job"]

    try:
        submit_timeout = max(0.001, min(5.0, deadline))
        status, accepted = request_json(base, token, "POST", "/api/generate/txt2img", {
            "repo": profile.repo, "prompt": prompt, "width": 1024, "height": 1024,
            "steps": 8, "guidance": 1.0, "seed": seed,
        }, timeout=submit_timeout)
        if status >= 400 or not isinstance(accepted, dict) or not isinstance(accepted.get("job"), dict):
            return fail(f"submit failed: HTTP {status}: {str(accepted)[:500]}")
        job_id = accepted["job"].get("id")
        if not isinstance(job_id, str) or not job_id:
            return fail("submit returned no job id")
        receipt["job_id"] = job_id

        trigger_deadline = time.monotonic() + deadline
        trigger = None
        while time.monotonic() < trigger_deadline:
            job = poll_job(job_id, trigger_deadline)
            if job is None:
                return fail("job poll failed before middle denoise callback")
            step, total = job.get("denoise_step"), job.get("denoise_steps")
            if (job.get("state") == "running" and isinstance(step, int)
                    and isinstance(total, int) and total == 8 and 1 < step < total):
                trigger = {"state": job["state"], "denoise_step": step,
                           "denoise_steps": total, "progress": job.get("progress")}
                break
            if job.get("state") in TERMINAL:
                return fail(f"job reached {job.get('state')} before middle denoise callback")
            if poll_interval > 0:
                time.sleep(min(poll_interval, max(0.0, trigger_deadline - time.monotonic())))
        if trigger is None:
            return fail("no persisted middle denoise callback (initial progress is insufficient)")
        receipt["trigger"] = trigger

        ack_timeout = max(0.001, min(5.0, settle_deadline))
        ack_status, ack_payload = request_json(
            base, token, "DELETE", f"/api/generate/jobs/{job_id}", timeout=ack_timeout)
        ack_job = ack_payload.get("job") if isinstance(ack_payload, dict) else None
        receipt["cancel_request_ack"] = {
            "http_status": ack_status,
            "state": ack_job.get("state") if isinstance(ack_job, dict) else None,
            "progress": ack_job.get("progress") if isinstance(ack_job, dict) else None,
        }
        if (ack_status != 200 or not isinstance(ack_job, dict)
                or ack_job.get("id") != job_id):
            return fail(f"DELETE acknowledgement failed: HTTP {ack_status}: {str(ack_payload)[:500]}")

        settle_until = time.monotonic() + settle_deadline
        terminal_deadline = settle_until
        terminal = None
        while time.monotonic() < terminal_deadline:
            terminal = poll_job(job_id, terminal_deadline)
            if terminal is None:
                return fail("job poll failed after DELETE acknowledgement")
            if terminal.get("state") in TERMINAL:
                break
            if poll_interval > 0:
                time.sleep(min(poll_interval, max(0.0, terminal_deadline - time.monotonic())))
        if not isinstance(terminal, dict) or terminal.get("state") != "cancelled":
            return fail(f"job did not become cancelled after DELETE: {str(terminal)[:500]}")
        receipt["terminal_state"] = terminal.get("state")
        receipt["terminal_finished_at"] = terminal.get("finished_at")
        receipt["denoise_cancel_observed"] = terminal.get("denoise_cancel_observed") is True
        if not receipt["denoise_cancel_observed"]:
            return fail("denoise callback did not confirm observing cancellation")

        # Cancellation is persisted before the inference lock and staged-file cleanup finish. Keep
        # polling the same terminal job until the artifact, worker activity, and model residency
        # have all settled, so a legitimate cleanup race is not reported as a failed probe.
        final = {}
        settled = False
        while time.monotonic() < settle_until:
            current = poll_job(job_id, settle_until)
            if current is not None:
                terminal = current
            artifact_timeout = max(0.001, min(5.0, settle_until - time.monotonic()))
            artifact_status, _artifact = request_json(
                base, token, "GET", f"/api/generate/jobs/{job_id}/image",
                raw=True, timeout=artifact_timeout)
            leftovers = sorted(path.name for path in output_dir.glob(f"*{job_id}*"))
            policy_timeout = max(0.001, min(5.0, settle_until - time.monotonic()))
            policy_status, policy = request_json(
                base, token, "GET", "/api/memory-policy", timeout=policy_timeout)
            final = {"job": terminal, "artifact_http_status": artifact_status,
                     "output_files": leftovers, "memory_policy": policy,
                     "memory_policy_http_status": policy_status}
            idle = (policy_status == 200 and isinstance(policy, dict)
                    and policy.get("busy") is False and policy.get("active_work") is False
                    and policy.get("loaded_models") == [])
            if artifact_status == 200:
                receipt["final_observations"] = final
                return fail("cancelled job artifact endpoint returned HTTP 200")
            if (terminal.get("state") == "cancelled" and artifact_status == 404
                    and not leftovers and idle):
                settled = True
                break
            if poll_interval > 0:
                time.sleep(min(poll_interval, max(0.0, settle_until - time.monotonic())))
        receipt["final_observations"] = final
        if not settled:
            return fail(f"cancelled job cleanup did not settle: {str(final)[:500]}")
        receipt["artifact_http_status"] = final["artifact_http_status"]
        receipt["output_files"] = final["output_files"]
        receipt["memory_policy"] = final["memory_policy"]

        if time.monotonic() >= settle_until:
            return fail("settle deadline expired before memory release")
        release_timeout = max(0.001, min(5.0, settle_until - time.monotonic()))
        release_status, release = request_json(
            base, token, "POST", "/api/memory/release", timeout=release_timeout)
        receipt["memory_release"] = release
        if release_status != 200:
            return fail(f"memory release endpoint failed: HTTP {release_status}")
        health_timeout = 5.0
        health_status, health = request_json(
            base, token, "GET", "/api/health", timeout=health_timeout)
        receipt["health_http_status"] = health_status
        if health_status != 200 or not isinstance(health, dict):
            return fail(f"worker did not remain responsive: HTTP {health_status}")
        receipt["status"] = "passed"
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return receipt
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        return fail(f"cancellation probe error: {str(exc)[:500]}")


def swap_gb() -> float | None:
    match = re.search(r"used = ([\d.]+)([MG])",
                      run_text(["/usr/sbin/sysctl", "-n", "vm.swapusage"]))
    return None if not match else float(match[1]) / (1024 if match[2] == "M" else 1)


def pressure_level() -> int | None:
    raw = run_text(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    return int(raw) if raw.isdigit() else None


def free_percent() -> int | None:
    match = re.search(r"free percentage: (\d+)%", run_text(["/usr/bin/memory_pressure", "-Q"]))
    return int(match[1]) if match else None


def process_table() -> list[tuple[int, int]]:
    rows = []
    for line in run_text(["/bin/ps", "-axo", "pid=,ppid="]).splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)", line)
        if match:
            rows.append((int(match[1]), int(match[2])))
    return rows


def process_tree(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for pid, parent in process_table():
        children.setdefault(parent, []).append(pid)
    found, pending = [], [root]
    while pending:
        pid = pending.pop()
        if pid not in found:
            found.append(pid)
            pending.extend(children.get(pid, []))
    return found


_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9}


def footprint(pid: int) -> tuple[float, float] | None:
    output = run_text(["/usr/bin/footprint", str(pid)], timeout=30)
    current = re.search(r"Footprint:\s*([\d.]+)\s*(\w+)", output)
    peak = re.search(r"phys_footprint_peak:\s*([\d.]+)\s*(\w+)", output)
    if not current:
        return None
    cur_value = float(current[1]) * _UNITS.get(current[2].upper(), 1)
    peak_value = float(peak[1]) * _UNITS.get(peak[2].upper(), 1) if peak else 0
    return cur_value, peak_value


class Sampler(threading.Thread):
    def __init__(self, root_pid: int, available_reader, swap_before: float | None) -> None:
        super().__init__(daemon=True)
        self.root_pid = root_pid
        self.stop_event = threading.Event()
        self.peak_tree_bytes = 0.0
        self.peak_process_bytes = 0.0
        self.tree_sample_count = 0
        self.footprint_sample_errors = 0
        self.pressure = pressure_level()
        self.pressure_samples = int(self.pressure is not None)
        self.pressure_sample_errors = int(self.pressure is None)
        self.swap_peak = swap_before
        self.swap_samples = 0
        self.swap_sample_errors = 0
        self.free_min = free_percent()
        self.max_stall_s = 0.0
        self.available = AvailableMemoryTracker(available_reader)

    def run(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            total = 0.0
            tree_valid = True
            pids = process_tree(self.root_pid)
            for pid in pids:
                measured = footprint(pid)
                if measured is None:
                    self.footprint_sample_errors += 1
                    tree_valid = False
                    continue
                current, peak = measured
                total += current
                self.peak_process_bytes = max(self.peak_process_bytes, peak)
            if pids and tree_valid and total > 0:
                self.tree_sample_count += 1
                self.peak_tree_bytes = max(self.peak_tree_bytes, total)
            level = pressure_level()
            if level is not None:
                self.pressure_samples += 1
                self.pressure = level if self.pressure is None else max(self.pressure, level)
            else:
                self.pressure_sample_errors += 1
            swap = swap_gb()
            if swap is not None:
                self.swap_samples += 1
                self.swap_peak = swap if self.swap_peak is None else max(self.swap_peak, swap)
            else:
                self.swap_sample_errors += 1
            free = free_percent()
            if free is not None:
                self.free_min = free if self.free_min is None else min(self.free_min, free)
            self.available.sample()
            self.max_stall_s = max(self.max_stall_s, time.monotonic() - started)
            self.stop_event.wait(1)

    def finish(self) -> None:
        self.stop_event.set()
        self.join(timeout=35)
        self.available.finish()


def memory_measurement_fields(sampler, swap_before: float | None,
                              swap_after: float | None) -> dict:
    available = sampler.available.fields()
    errors = []
    if sampler.tree_sample_count < 1 or sampler.peak_tree_bytes <= 0:
        errors.append("process_tree_peak")
    if sampler.pressure_samples < 1 or sampler.pressure not in PRESSURE:
        errors.append("pressure")
    if (swap_before is None or sampler.swap_peak is None or swap_after is None
            or sampler.swap_samples < 1):
        errors.append("swap")
    if (available.get("available_memory_samples", 0) < 3
            or any(available.get(key) is None for key in
                   ("available_gb_start", "available_gb_min", "available_gb_end"))):
        errors.append("available_memory")
    swap_peak_delta = (None if swap_before is None or sampler.swap_peak is None
                       else round(max(0, sampler.swap_peak - swap_before), 3))
    swap_end_delta = (None if swap_before is None or swap_after is None
                      else round(max(0, swap_after - swap_before), 3))
    return {
        **available,
        "peak_process_tree_gb": round(sampler.peak_tree_bytes / DECIMAL_GB, 3),
        "peak_single_process_gb": round(sampler.peak_process_bytes / DECIMAL_GB, 3),
        "process_tree_samples": sampler.tree_sample_count,
        "footprint_sample_errors": sampler.footprint_sample_errors,
        "memory_pressure": PRESSURE.get(sampler.pressure) if sampler.pressure is not None else None,
        "pressure_samples": sampler.pressure_samples,
        "pressure_sample_errors": sampler.pressure_sample_errors,
        "swap_before_gb": None if swap_before is None else round(swap_before, 3),
        "swap_after_gb": None if swap_after is None else round(swap_after, 3),
        "swap_peak_gb": None if sampler.swap_peak is None else round(sampler.swap_peak, 3),
        "swap_samples": sampler.swap_samples,
        "swap_sample_errors": sampler.swap_sample_errors,
        "swap_peak_delta_gb": swap_peak_delta,
        "swap_end_delta_gb": swap_end_delta,
        # Existing receipt consumers read this field; it now carries the peak growth used to grade.
        "swap_delta_gb": swap_peak_delta,
        "free_percent_min": sampler.free_min,
        "max_sampler_stall_s": round(sampler.max_stall_s, 3),
        "responsive": sampler.max_stall_s < 20,
        "memory_metrics_complete": not errors,
        "memory_metric_errors": errors,
    }


def png_dimensions(value: bytes) -> tuple[int, int] | None:
    if len(value) < 24 or value[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(value[16:20], "big"), int.from_bytes(value[20:24], "big")


def run_workload(base: str, token: str, profile: Profile, case: Workload,
                 prompt: str, seed: int, timeout: float, result_dir: Path,
                 worker_pid: int, baseline: float | None, available_reader) -> dict:
    started_at = time.time()
    swap_before = swap_gb()
    disk_before = ssd_wear.snapshot()
    sampler = Sampler(worker_pid, available_reader, swap_before)
    sampler.start()
    row = {**asdict(case), "seed": seed, "repo": profile.repo,
           "package_id": profile.package_id, "revision": profile.revision,
           "quantization": profile.quantization, "status": "started",
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        status, accepted = request_json(base, token, "POST", "/api/generate/txt2img", {
            "repo": profile.repo, "prompt": prompt, "width": case.width,
            "height": case.height, "steps": case.steps, "guidance": 1.0, "seed": seed,
        })
        if status >= 400 or not isinstance(accepted, dict) or not isinstance(accepted.get("job"), dict):
            row.update(status="refused", error=f"HTTP {status}: {str(accepted)[:500]}")
            return row
        job = accepted["job"]
        job_id = job["id"]
        row["job_id"] = job_id
        deadline = time.monotonic() + timeout
        while job.get("state") not in TERMINAL and time.monotonic() < deadline:
            time.sleep(2)
            polled, payload = request_json(base, token, "GET", f"/api/generate/jobs/{job_id}")
            if polled != 200 or not isinstance(payload, dict):
                row.update(status="poll_failed", error=f"HTTP {polled}: {str(payload)[:500]}")
                return row
            job = payload.get("job") or {}
        if job.get("state") not in TERMINAL:
            request_json(base, token, "DELETE", f"/api/generate/jobs/{job_id}")
            row.update(status="timeout", error=f"not finished within {timeout:g} seconds")
            return row
        row.update({
            "worker_state": job.get("state"), "error": job.get("error"),
            "error_code": job.get("error_code"), "duration_seconds": job.get("duration_seconds"),
            "memory_mode": job.get("memory_mode"), "loading_mode_setting": job.get("loading_mode_setting"),
            "model_reused": job.get("model_reused"), "memory_hooks": job.get("memory_hooks"),
            "peak_mlx_bytes": job.get("peak_mlx_bytes"),
            "runtime_revision": job.get("runtime_revision"),
            "model_revision": job.get("model_revision"),
        })
        if job.get("state") != "done":
            row["status"] = str(job.get("state") or "failed")
            return row
        artifact_status, artifact = request_json(
            base, token, "GET", f"/api/generate/jobs/{job_id}/image", raw=True, timeout=300)
        if artifact_status != 200 or not isinstance(artifact, bytes):
            row.update(status="artifact_failed", error=f"artifact HTTP {artifact_status}")
            return row
        dimensions = png_dimensions(artifact)
        if dimensions != (case.width, case.height):
            row.update(status="artifact_failed", error=f"PNG dimensions {dimensions}, expected {(case.width, case.height)}")
            return row
        destination = result_dir / f"{case.name}-seed{seed}.png"
        destination.write_bytes(artifact)
        row.update(status="completed", artifact=destination.name,
                   artifact_bytes=len(artifact), artifact_sha256=hashlib.sha256(artifact).hexdigest())
        return row
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        row.update(status="failed", error=str(exc)[:500])
        return row
    finally:
        sampler.finish()
        disk_after = ssd_wear.snapshot()
        swap_after = swap_gb()
        row.update(ssd_wear.clip_columns(disk_before, disk_after, baseline))
        row.update(memory_measurement_fields(sampler, swap_before, swap_after))
        row["wall_seconds"] = round(time.time() - started_at, 3)


def outcome(rows: list[dict], *, expected_names: list[str] | None = None,
            target_tier_verified: bool = False, cancellation: dict | None = None,
            require_cancellation: bool = False) -> str:
    if not rows:
        return "NOT MEASURED"
    if any(row.get("status") != "completed" for row in rows):
        return "MEASURED FAILURE"
    if expected_names is not None and (len(rows) != len(expected_names)
            or {row.get("name") for row in rows} != set(expected_names)):
        return "NOT MEASURED"
    if any(not row.get("memory_metrics_complete", False) for row in rows):
        return "NOT MEASURED"
    if require_cancellation and (not isinstance(cancellation, dict)
                                 or cancellation.get("status") != "passed"):
        return "MEASURED FAILURE" if isinstance(cancellation, dict) and cancellation.get("status") == "failed" else "NOT MEASURED"
    if any(row.get("memory_pressure") == "critical" or not row.get("responsive", True)
           or ((row.get("swap_peak_delta_gb") or 0) > 1.0)
           for row in rows):
        return "COMPLETED UNDER PRESSURE"
    return "CANDIDATE PASSED THIS RUN" if target_tier_verified else "NON-TARGET TRIAL COMPLETED"


def exit_status(result: str) -> int:
    if result == "CANDIDATE PASSED THIS RUN":
        return 0
    return 3 if result == "NON-TARGET TRIAL COMPLETED" else 2


def summary_text(record: dict) -> str:
    profile, host, rows = record["profile"], record["host"], record.get("rows") or []
    verified = (record.get("host_status") or {}).get("target_tier_verified", False)
    purpose = f"{profile['tier_gb']} GB qualification" if verified else "non-target trial (not qualified)"
    lines = [
        f"{profile['label']} — {purpose}",
        f"Outcome: {record['outcome']}", "",
        f"Mac: {host['chip']}, {host['memory_bytes'] / GIB:.1f} GiB, macOS {host['macos']}",
        f"App: {record['bundle']['version']} at {record['bundle']['app']}",
        f"Package: {profile['package_id']} / {profile['revision']}",
        f"Runtime identity: {record['bundle']['runtime_sha256']}",
        "",
    ]
    if profile["package_id"] == TURBO_PACKAGE_ID:
        cancellation = record.get("cancellation") or {}
        lines.append(f"Cancellation probe: {cancellation.get('status', 'not run')}")
        if cancellation.get("reason"):
            lines.append(f"  Cancellation detail: {cancellation['reason']}")
        lines.append("")
    for row in rows:
        lines.append(
            f"{row['name']}: {row['width']}x{row['height']}, {row['steps']} steps — "
            f"{row['status']}; {row.get('wall_seconds', '?')} s wall; "
            f"{row.get('peak_process_tree_gb', '?')} GB peak worker tree; "
            f"available {row.get('available_gb_start', '?')}→"
            f"{row.get('available_gb_min', '?')}→{row.get('available_gb_end', '?')} GB "
            f"(start/min/end); "
            f"pressure {row.get('memory_pressure', '?')}; "
            f"swap peak +{row.get('swap_peak_delta_gb', '?')} GB "
            f"(end +{row.get('swap_end_delta_gb', '?')} GB); "
            f"SSD net {row.get('ssd_written_net_gb', '?')} GB")
        if row.get("error"):
            lines.append(f"  Error: {row['error']}")
        if row.get("memory_metric_errors"):
            lines.append("  Incomplete memory telemetry: "
                         + ", ".join(row["memory_metric_errors"]))
    lines += ["", "This receipt describes this physical Mac and exact pinned package only.",
              "An 8 GB result is never inferred; unsupported or unrun tiers remain NOT MEASURED.",
              "Generated images are evidence for visual review, not an automatic language or product-selection rule."]
    return "\n".join(lines) + "\n"


def write_record(result_dir: Path, record: dict) -> None:
    (result_dir / "measurement.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    rows = record.get("rows") or []
    if rows:
        columns = sorted({key for row in rows for key in row})
        with (result_dir / "results.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    (result_dir / "summary.txt").write_text(summary_text(record))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", type=int, required=True)
    parser.add_argument("--turbo", action="store_true",
                        help="qualify the pinned Qwen Image 2.1 Turbo MLX 4-bit candidate")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--kit", type=Path)
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--stress-40", action="store_true")
    parser.add_argument("--allow-non-target", action="store_true")
    parser.add_argument("--baseline-seconds", type=float, default=30)
    args = parser.parse_args(argv)

    profile = profile_for_tier(args.tier, turbo=args.turbo)
    cases = workloads(args.stress_40, turbo=args.turbo)
    host = hardware_facts()
    status = host_status(profile, host["memory_bytes"], version_tuple(host["macos"]),
                         allow_non_target=args.allow_non_target)
    if status.code != "READY":
        print(f"{status.code}: {status.reason}\nNothing was run and no result was written.", file=sys.stderr)
        return 4
    if host["machine"] != "arm64":
        print("NOT MEASURED: Apple Silicon is required.\nNothing was run and no result was written.", file=sys.stderr)
        return 4
    busy = foreign_model_processes()
    if busy:
        print("NOT MEASURED: Studio Hub, another Image worker, or mlx-serve is running. Quit Studio Hub "
              "and finish ordinary Image work first:\n  " + "\n  ".join(busy), file=sys.stderr)
        return 4

    script = Path(__file__).resolve()
    kit = (args.kit or kit_root(script)).expanduser().resolve()
    app = find_app(args.app, kit)
    bundle = validate_bundle(app, profile)
    read_available = available_memory_reader(app)
    source_library = (args.models_dir or kit / "model-library").expanduser().resolve()
    if not (source_library / profile.package_id).is_dir():
        raise SystemExit(
            f"NOT MEASURED: {profile.package_id} is absent from the kit library {source_library}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    result_dir = (args.results or script.parent / f"Qwen Image {profile.tier_gb}GB Run {stamp}").expanduser().resolve()
    if result_dir.exists():
        raise SystemExit(f"refusing to overwrite existing result folder {result_dir}")
    private_root = kit / "_qualification-private" / f"qwen-image-{profile.tier_gb}gb-{stamp}"
    if private_root.exists():
        raise SystemExit(f"refusing to reuse existing private qualification folder {private_root}")
    private_root.mkdir(parents=True, mode=0o700)
    data = private_root / "state"
    data.mkdir(mode=0o700)
    token_path, ready = data / "owner.token", data / "worker-ready.json"
    token = secrets.token_urlsafe(32)
    token_path.write_text(token + "\n")
    token_path.chmod(0o600)

    old_tmp = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = str(private_root)
    try:
        private_library = clone_package(source_library, private_root / "library", profile.package_id)
    finally:
        if old_tmp is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = old_tmp

    result_dir.mkdir(parents=True)

    record = {
        "schema": "studiohub.qwen-image-qualification.v1",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": asdict(profile), "host": host, "host_status": asdict(status),
        "bundle": bundle, "source_library": str(source_library),
        "private_library": str(private_library), "private_state": str(data),
        "available_memory_source": "packaged components/host_memory.py + psutil.virtual_memory()",
        "prompt": args.prompt,
        "workloads": [asdict(case) for case in cases],
        "rows": [], "cancellation": None, "outcome": "STARTED",
    }
    write_record(result_dir, record)

    log = (result_dir / "worker.log").open("ab", buffering=0)
    command = worker_command(
        app, data, private_library, token_path, ready, os.getpid(),
        qualification_package=profile.package_id if args.turbo else None)
    child_env = {key: value for key, value in os.environ.items()
                 if key in {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR", "TZ"}}
    child_env["TMPDIR"] = str(private_root)
    try:
        process = spawn_private_worker(command, stdout=log, cwd=data, env=child_env)
    except ForeignProcessError as exc:
        log.close()
        record.update(outcome="NOT MEASURED", harness_error=str(exc),
                      finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        write_record(result_dir, record)
        print(f"NOT MEASURED: {exc}\nResults: {result_dir}", file=sys.stderr)
        return 4
    try:
        base, worker_ready = wait_ready(process, ready, token)
        if args.turbo:
            verify_qualification_worker(base, token, profile)
        record["worker"] = {"pid": process.pid,
                            "ready": {key: worker_ready.get(key) for key in
                                      ("url", "pid", "version") if key in worker_ready},
                            "command": [str(value) for value in command]}
        baseline = (ssd_wear.baseline_gb_per_min(args.baseline_seconds)
                    if args.baseline_seconds > 0 else None)
        record["ssd_baseline_gb_per_min"] = baseline
        for index, case in enumerate(cases):
            print(f"[{case.name}] {case.width}x{case.height}, {case.steps} steps", flush=True)
            row = run_workload(base, token, profile, case, args.prompt, 42 + index,
                               args.timeout, result_dir, process.pid, baseline, read_available)
            row.update({"machine_ram_gb": round(host["memory_bytes"] / GIB, 1),
                        "macos": host["macos"], "chip": host["chip"],
                        "app_version": bundle["version"]})
            record["rows"].append(row)
            record["outcome"] = outcome(record["rows"], expected_names=[case["name"] for case in record["workloads"]],
                                      target_tier_verified=record["host_status"]["target_tier_verified"],
                                      cancellation=record.get("cancellation"),
                                      require_cancellation=args.turbo)
            write_record(result_dir, record)
            print(f"  {row['status']}: {row.get('wall_seconds')} s, "
                  f"peak {row.get('peak_process_tree_gb')} GB, "
                  f"available {row.get('available_gb_start')}→{row.get('available_gb_min')}→"
                  f"{row.get('available_gb_end')} GB, "
                  f"pressure {row.get('memory_pressure')}", flush=True)
            if row["status"] != "completed":
                break
        if args.turbo:
            if record["rows"] and all(row.get("status") == "completed" for row in record["rows"]):
                record["cancellation"] = run_cancellation_probe(
                    base, token, profile, args.prompt, 1001, data / "image" / "outputs",
                    deadline=args.timeout)
            else:
                record["cancellation"] = {
                    "status": "not_run", "reason": "normal workload did not complete"
                }
            record["outcome"] = outcome(
                record["rows"], expected_names=[case["name"] for case in record["workloads"]],
                target_tier_verified=record["host_status"]["target_tier_verified"],
                cancellation=record.get("cancellation"), require_cancellation=True)
            write_record(result_dir, record)
    except KeyboardInterrupt:
        record["outcome"] = outcome(record["rows"], expected_names=[case["name"] for case in record["workloads"]],
                                      target_tier_verified=record["host_status"]["target_tier_verified"],
                                      cancellation=record.get("cancellation"),
                                      require_cancellation=args.turbo) if record["rows"] else "NOT MEASURED"
        record["interrupted"] = True
    except Exception as exc:  # preserve a useful receipt for worker start/lifecycle failures
        record["outcome"] = "MEASURED FAILURE" if record["rows"] else "NOT MEASURED"
        record["harness_error"] = str(exc)
    finally:
        stop_worker(process)
        log.close()
        record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        record["outcome"] = outcome(record["rows"], expected_names=[case["name"] for case in record["workloads"]],
                                      target_tier_verified=record["host_status"]["target_tier_verified"],
                                      cancellation=record.get("cancellation"),
                                      require_cancellation=args.turbo) if record["rows"] else record["outcome"]
        write_record(result_dir, record)

    print("\n" + summary_text(record), end="")
    print(f"Results: {result_dir}")
    print(f"Private package clone (keep private; do not send as a receipt): {private_library}")
    return exit_status(record["outcome"])


if __name__ == "__main__":
    raise SystemExit(main())
