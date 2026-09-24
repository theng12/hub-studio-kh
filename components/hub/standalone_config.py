"""Packaged Hub configuration that replaces legacy launcher discovery."""

from __future__ import annotations

import json
import os
import platform
import stat
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_MAX_BYTES = 50_000_000_000
DEFAULT_RETENTION_DAYS = 30
DEFAULT_CLEANUP_ENABLED = True
DEFAULT_CLEANUP_INTERVAL_SECONDS = 300
DEFAULT_MINIMUM_FREE_BYTES = 1_000_000_000
_REQUIRED_WORKERS = {
    "image": "image",
    "voice": "voice",
    "subtitle": "transcription",
    "music": "music",
    "chat": "chat",
}
_SETTINGS_FILE = "device_settings.json"


def data_dir() -> Path:
    value = os.environ.get("STUDIOHUB_DATA_DIR", "").strip()
    if not value:
        raise ValueError("STUDIOHUB_DATA_DIR is required for the packaged Hub")
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _private_file(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for the packaged Hub")
    path = Path(value).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"{name} is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{name} must be an owner-owned regular file")
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError(f"{name} must be owner-only")
    if info.st_size > 1024 * 1024:
        raise ValueError(f"{name} is too large")
    return path.resolve()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid packaged worker manifest: {path.name}") from exc


def _worker(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("worker descriptor must be an object")
    worker = dict(value)
    worker_id = str(worker.get("id") or "").strip()
    expected_modality = _REQUIRED_WORKERS.get(worker_id)
    if expected_modality is None:
        raise ValueError("worker descriptor has an unsupported id")
    if worker.get("modality") != expected_modality:
        raise ValueError(f"worker {worker_id} has an invalid modality")
    if worker.get("host") not in {"127.0.0.1", "::1"}:
        raise ValueError(f"worker {worker_id} must bind to loopback")
    if worker.get("local") is not True:
        raise ValueError(f"worker {worker_id} must be local")
    if not isinstance(worker.get("machine"), str) or not worker["machine"].strip():
        raise ValueError(f"worker {worker_id} has no stable machine identity")
    if not isinstance(worker.get("port"), int) or not 1024 <= worker["port"] <= 65535:
        raise ValueError(f"worker {worker_id} has an invalid port")
    if not isinstance(worker.get("runtime_identity"), dict):
        raise ValueError(f"worker {worker_id} has no runtime identity")
    if not isinstance(worker.get("hub_token"), str) or not worker["hub_token"]:
        raise ValueError(f"worker {worker_id} has no Hub token")
    # The packaged workers use the existing Hub credential for every private
    # request.  Do not silently cause peers.fleet_token() to mint a second
    # credential just because a descriptor omitted its legacy field.
    supplied_studio_token = worker.get("studio_token")
    if supplied_studio_token is not None and supplied_studio_token != worker["hub_token"]:
        raise ValueError(f"worker {worker_id} has a mismatched Studio token")
    worker["studio_token"] = worker["hub_token"]
    roots = worker.get("managed_output_roots", [])
    if roots is not None and (not isinstance(roots, list) or not all(isinstance(path, str) and path for path in roots)):
        raise ValueError(f"worker {worker_id} has invalid managed output roots")
    worker["managed_output_roots"] = roots or []
    worker.setdefault("title", f"{worker_id.title()} Studio")
    worker.setdefault("emoji", "🧩")
    worker["base_url"] = f"http://{worker['host']}:{worker['port']}"
    return worker


def load_workers() -> list[dict[str, Any]]:
    """Return the complete private worker registry supplied by the owner."""
    raw = _read_json(_private_file("STUDIOHUB_WORKERS_FILE"))
    if not isinstance(raw, list):
        raise ValueError("packaged worker manifest must be a list")
    workers = [_worker(value) for value in raw]
    found = {worker["id"] for worker in workers}
    if not workers or len(workers) != len(found):
        raise ValueError("packaged worker manifest must contain each running worker once")
    if len({(worker["host"], worker["port"]) for worker in workers}) != len(workers):
        raise ValueError("packaged workers must have distinct endpoints")
    return workers


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _settings_path() -> Path:
    return data_dir() / _SETTINGS_FILE


def cleanup_interval_seconds() -> float:
    """How often the storage budget is enforced, honouring the operator's setting."""
    try:
        value = float(_settings().get("cleanup_interval_seconds") or DEFAULT_CLEANUP_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        return float(DEFAULT_CLEANUP_INTERVAL_SECONDS)
    return value if value > 0 else float(DEFAULT_CLEANUP_INTERVAL_SECONDS)


def _settings() -> dict[str, int | bool]:
    try:
        value = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        value = {}
    max_bytes = value.get("max_bytes", DEFAULT_MAX_BYTES) if isinstance(value, dict) else DEFAULT_MAX_BYTES
    retention_days = value.get("retention_days", DEFAULT_RETENTION_DAYS) if isinstance(value, dict) else DEFAULT_RETENTION_DAYS
    enabled = value.get("cleanup_enabled", DEFAULT_CLEANUP_ENABLED) if isinstance(value, dict) else DEFAULT_CLEANUP_ENABLED
    interval = value.get("cleanup_interval_seconds", DEFAULT_CLEANUP_INTERVAL_SECONDS) if isinstance(value, dict) else DEFAULT_CLEANUP_INTERVAL_SECONDS
    minimum_free = value.get("minimum_free_bytes", DEFAULT_MINIMUM_FREE_BYTES) if isinstance(value, dict) else DEFAULT_MINIMUM_FREE_BYTES
    return {
        "max_bytes": max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else DEFAULT_MAX_BYTES,
        "retention_days": retention_days if isinstance(retention_days, int) and retention_days > 0 else DEFAULT_RETENTION_DAYS,
        "cleanup_enabled": enabled if isinstance(enabled, bool) else DEFAULT_CLEANUP_ENABLED,
        "cleanup_interval_seconds": interval if isinstance(interval, int) and interval >= 60 else DEFAULT_CLEANUP_INTERVAL_SECONDS,
        "minimum_free_bytes": minimum_free if isinstance(minimum_free, int) and minimum_free >= 0 else DEFAULT_MINIMUM_FREE_BYTES,
    }


def load_device_settings() -> dict[str, dict[str, int | bool]]:
    return {
        "defaults": {"max_bytes": DEFAULT_MAX_BYTES, "retention_days": DEFAULT_RETENTION_DAYS,
                     "cleanup_enabled": DEFAULT_CLEANUP_ENABLED,
                     "cleanup_interval_seconds": DEFAULT_CLEANUP_INTERVAL_SECONDS,
                     "minimum_free_bytes": DEFAULT_MINIMUM_FREE_BYTES},
        "effective": _settings(),
    }


def preview_device_settings(**changes: object) -> dict[str, int | bool]:
    current = _settings()
    value = {key: changes.get(key, current[key]) for key in current}
    max_bytes, retention_days = value["max_bytes"], value["retention_days"]
    enabled, interval, minimum_free = value["cleanup_enabled"], value["cleanup_interval_seconds"], value["minimum_free_bytes"]
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days <= 0:
        raise ValueError("retention_days must be a positive integer")
    if not isinstance(enabled, bool):
        raise ValueError("cleanup_enabled must be a boolean")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 60:
        raise ValueError("cleanup_interval_seconds must be at least 60")
    if isinstance(minimum_free, bool) or not isinstance(minimum_free, int) or minimum_free < 0:
        raise ValueError("minimum_free_bytes must be a non-negative integer")
    return value


def save_device_settings(*, max_bytes: int, retention_days: int, cleanup_enabled: bool = DEFAULT_CLEANUP_ENABLED,
                         cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS,
                         minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES) -> dict[str, dict[str, int | bool]]:
    value = preview_device_settings(max_bytes=max_bytes, retention_days=retention_days,
                                    cleanup_enabled=cleanup_enabled,
                                    cleanup_interval_seconds=cleanup_interval_seconds,
                                    minimum_free_bytes=minimum_free_bytes)
    _atomic_write(_settings_path(), value)
    return load_device_settings()


def restore_device_defaults() -> dict[str, dict[str, int]]:
    _atomic_write(_settings_path(), {
        "max_bytes": DEFAULT_MAX_BYTES,
        "retention_days": DEFAULT_RETENTION_DAYS,
        "cleanup_enabled": DEFAULT_CLEANUP_ENABLED,
        "cleanup_interval_seconds": DEFAULT_CLEANUP_INTERVAL_SECONDS,
        "minimum_free_bytes": DEFAULT_MINIMUM_FREE_BYTES,
    })
    return load_device_settings()


def _version_parts(value: object) -> tuple[int, ...]:
    return tuple(int(part) for part in str(value or "0").split(".") if part.isdigit())


def model_readiness(model: dict[str, Any], *, macos_version: str | None = None,
                    chip: str | None = None) -> dict[str, Any]:
    """Report independent package, runtime, and OS gates before scheduler admission."""
    files_state = str(model.get("files_state") or "not_installed")
    runtime = model.get("runtime_identity")
    runtime_state = "ready" if (
        isinstance(runtime, dict) and isinstance(runtime.get("id"), str)
        and bool(runtime["id"].strip()) and runtime.get("compatible") is True
    ) else "incompatible"
    actual_os = macos_version or platform.mac_ver()[0]
    minimum_os = str(model.get("minimum_macos") or "0")
    os_state = "ready" if _version_parts(actual_os) >= _version_parts(minimum_os) else "unsupported"
    chip_name = str(chip or platform.processor() or platform.machine() or "unknown")
    chip_state = "ready" if chip_name.lower().startswith(("apple", "arm", "aarch64")) else "unsupported"
    dispatch_state = "eligible" if all((
        files_state == "files_verified", runtime_state == "ready",
        os_state == "ready", chip_state == "ready",
    )) else "ineligible"
    return {
        "files_state": files_state,
        "runtime_state": runtime_state,
        "os_state": os_state,
        "chip_state": chip_state,
        "minimum_macos": minimum_os,
        "macos_version": actual_os,
        "dispatch_state": dispatch_state,
    }
