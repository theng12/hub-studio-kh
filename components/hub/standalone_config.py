"""Packaged Hub configuration that replaces legacy launcher discovery."""

from __future__ import annotations

import json
import logging
import os
import platform
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

from .backend.vocabulary import REQUIRED_WORKERS as _REQUIRED_WORKERS
from .backend.vocabulary import WORKER_FOR_MODALITY as _WORKER_FOR_MODALITY


DEFAULT_MAX_BYTES = 20_000_000_000          # live outputs only; the Trash Folder is not counted
DEFAULT_RETENTION_DAYS = 3
DEFAULT_CLEANUP_ENABLED = True
DEFAULT_CLEANUP_INTERVAL_SECONDS = 300
DEFAULT_MINIMUM_FREE_BYTES = 5_000_000_000
DEFAULT_KEEP_ALL_MODELS = False             # True: this Mac keeps shelved models (components/shelved_models.py)
# `_REQUIRED_WORKERS` (worker id -> modality) is the inverse of the Hub's modality vocabulary,
# derived in `backend/vocabulary.py` rather than restated here. It is kept as a module attribute
# because it is the inverse table, but `_worker()` no longer looks a descriptor's id up in it:
# the descriptor declares its own modality and the vocabulary validates that, so a worker id the
# Hub does not route fails with the modality named and the file to edit (see `_worker`).
_SETTINGS_FILE = "device_settings.json"
# The defaults before 0.6.117 (30 days / 50 GB / 1 GB), which every Mac that ever saved or restored
# its settings still has on disk. A file with no `defaults_version` was written before the new
# defaults existed, so a value equal to the old default is read as "never chosen" and gets the new
# one; a value that differs was chosen by the operator and is kept. Every write stamps the version,
# so an operator who saves exactly 30 days afterwards is not migrated again.
_DEFAULTS_VERSION = 2
_LEGACY_DEFAULTS = {"max_bytes": 50_000_000_000, "retention_days": 30, "minimum_free_bytes": 1_000_000_000}


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
    """Validate one owner-supplied worker descriptor, against the Hub's one vocabulary.

    The descriptor is the producer's statement: a packaged worker says which *modality* it
    serves, and the Hub routes by that modality. The old check instead looked the worker's id up
    in a copy of `WORKER_FOR_MODALITY` and answered a bare "unsupported id", so registering a new
    worker meant a startup abort with nothing said about the modality it declares or where to add
    it. The vocabulary still decides whether the Hub routes the modality at all (it must, or the
    peer surface and the fleet join would not know it either), and the failure names both the
    modality and the file.
    """
    if not isinstance(value, dict):
        raise ValueError("worker descriptor must be an object")
    worker = dict(value)
    worker_id = str(worker.get("id") or "").strip()
    modality = str(worker.get("modality") or "").strip().lower()
    if not worker_id:
        raise ValueError("worker descriptor has no id")
    if not modality:
        raise ValueError(
            f"worker {worker_id!r} declares no modality; a packaged worker describes itself in "
            f"STUDIOHUB_WORKERS_FILE and the Hub routes its work by that modality")
    expected_worker = _WORKER_FOR_MODALITY.get(modality)
    if expected_worker is None:
        raise ValueError(
            f"worker {worker_id!r} declares the modality {modality!r}, which this Hub does not "
            f"route; add \"{modality}\": \"{worker_id}\" to WORKER_FOR_MODALITY in "
            f"components/hub/backend/vocabulary.py, then add the worker to the packaged release "
            f"manifest so it is staged and started")
    if expected_worker != worker_id:
        raise ValueError(
            f"worker {worker_id!r} declares the modality {modality!r}, which "
            f"components/hub/backend/vocabulary.py routes to the worker {expected_worker!r}; "
            f"rename the descriptor's id to {expected_worker!r} in STUDIOHUB_WORKERS_FILE, or "
            f"change WORKER_FOR_MODALITY to \"{modality}\": \"{worker_id}\" if this is a "
            f"deliberate replacement for that worker")
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


log = logging.getLogger("studiohub.settings")
# Read-modify-write of device_settings.json comes from threadpool routes (PUT, restore-defaults) and
# the background cleanup loop: serialised so one writer cannot overwrite another's field with the
# value it read before the other wrote (a toggled keep_all_models was the one at risk).
_SETTINGS_LOCK = threading.RLock()
_logged_problem: str | None = None


def _read_settings() -> tuple[dict, str | None]:
    """The saved settings and, when the file exists but is damaged, what is wrong with it.

    An absent file is not a problem (defaults). A file that is unreadable, not JSON or not an object
    is: the values fall back to defaults, **except keep_all_models, which fails safe to true** (see
    `_settings`), and the problem is logged loudly once and surfaced by `settings_warnings`.
    """
    global _logged_problem
    problem: str | None = None
    value: Any = {}
    try:
        value = json.loads(_settings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = {}
    except OSError as exc:
        value, problem = {}, f"device_settings.json could not be read ({exc})"
    except ValueError:
        value, problem = {}, "device_settings.json is not valid JSON"
    if problem is None and not isinstance(value, dict):
        value, problem = {}, "device_settings.json is not a JSON object"
    if problem is None and "keep_all_models" in value and not isinstance(value["keep_all_models"], bool):
        problem = "keep_all_models in device_settings.json is neither true nor false"
    if problem != _logged_problem:
        _logged_problem = problem
        if problem:
            log.error("%s: this Mac keeps ALL models until the file is fixed (nothing is shelved or "
                      "moved to the Trash Folder), and other settings use their defaults", problem)
    return value, problem


def settings_warnings() -> list[str]:
    """Plain-language warnings about this Mac's settings file, for the Hub's settings route."""
    problem = _read_settings()[1]
    return [f"{problem}. This Mac is keeping all models and using default storage limits until it "
            f"is repaired (save the settings again to rewrite it)."] if problem else []


def _settings() -> dict[str, int | bool]:
    value, problem = _read_settings()
    if isinstance(value, dict) and "defaults_version" not in value:
        value = {key: item for key, item in value.items() if _LEGACY_DEFAULTS.get(key) != item}
    max_bytes = value.get("max_bytes", DEFAULT_MAX_BYTES) if isinstance(value, dict) else DEFAULT_MAX_BYTES
    retention_days = value.get("retention_days", DEFAULT_RETENTION_DAYS) if isinstance(value, dict) else DEFAULT_RETENTION_DAYS
    enabled = value.get("cleanup_enabled", DEFAULT_CLEANUP_ENABLED) if isinstance(value, dict) else DEFAULT_CLEANUP_ENABLED
    interval = value.get("cleanup_interval_seconds", DEFAULT_CLEANUP_INTERVAL_SECONDS) if isinstance(value, dict) else DEFAULT_CLEANUP_INTERVAL_SECONDS
    minimum_free = value.get("minimum_free_bytes", DEFAULT_MINIMUM_FREE_BYTES) if isinstance(value, dict) else DEFAULT_MINIMUM_FREE_BYTES
    # Damage never reads as "not keep-all": that would move a keep-all Mac's shelved models away.
    keep_all = True if problem else value.get("keep_all_models", DEFAULT_KEEP_ALL_MODELS)
    return {
        "max_bytes": max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else DEFAULT_MAX_BYTES,
        "retention_days": retention_days if isinstance(retention_days, int) and retention_days > 0 else DEFAULT_RETENTION_DAYS,
        "cleanup_enabled": enabled if isinstance(enabled, bool) else DEFAULT_CLEANUP_ENABLED,
        "cleanup_interval_seconds": interval if isinstance(interval, int) and interval >= 60 else DEFAULT_CLEANUP_INTERVAL_SECONDS,
        "minimum_free_bytes": minimum_free if isinstance(minimum_free, int) and minimum_free >= 0 else DEFAULT_MINIMUM_FREE_BYTES,
        "keep_all_models": keep_all,
    }


def load_device_settings() -> dict[str, dict[str, int | bool]]:
    return {
        "defaults": {"max_bytes": DEFAULT_MAX_BYTES, "retention_days": DEFAULT_RETENTION_DAYS,
                     "cleanup_enabled": DEFAULT_CLEANUP_ENABLED,
                     "cleanup_interval_seconds": DEFAULT_CLEANUP_INTERVAL_SECONDS,
                     "minimum_free_bytes": DEFAULT_MINIMUM_FREE_BYTES,
                     "keep_all_models": DEFAULT_KEEP_ALL_MODELS},
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
    if not isinstance(value["keep_all_models"], bool):
        raise ValueError("keep_all_models must be a boolean")
    return value


def update_device_settings(**changes: object) -> dict[str, dict[str, int | bool]]:
    """Change only the named settings, atomically: read, validate, write under one lock.

    The route used to resolve every field from a read made before the write, so two concurrent
    changes (the output limits and the keep-all toggle) could undo each other.
    """
    with _SETTINGS_LOCK:
        value = preview_device_settings(**changes)
        _atomic_write(_settings_path(), value | {"defaults_version": _DEFAULTS_VERSION})
        return load_device_settings()


def save_device_settings(*, max_bytes: int, retention_days: int, cleanup_enabled: bool = DEFAULT_CLEANUP_ENABLED,
                         cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS,
                         minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
                         keep_all_models: bool | None = None) -> dict[str, dict[str, int | bool]]:
    return update_device_settings(
        max_bytes=max_bytes, retention_days=retention_days, cleanup_enabled=cleanup_enabled,
        cleanup_interval_seconds=cleanup_interval_seconds, minimum_free_bytes=minimum_free_bytes,
        **({} if keep_all_models is None else {"keep_all_models": keep_all_models}))


def restore_device_defaults() -> dict[str, dict[str, int]]:
    with _SETTINGS_LOCK:
        _atomic_write(_settings_path(), {
            "max_bytes": DEFAULT_MAX_BYTES,
            "retention_days": DEFAULT_RETENTION_DAYS,
            "cleanup_enabled": DEFAULT_CLEANUP_ENABLED,
            "cleanup_interval_seconds": DEFAULT_CLEANUP_INTERVAL_SECONDS,
            "minimum_free_bytes": DEFAULT_MINIMUM_FREE_BYTES,
            "keep_all_models": _settings()["keep_all_models"],   # about models, not output limits: not reset here
            "defaults_version": _DEFAULTS_VERSION,
        })
        return load_device_settings()


def _version_parts(value: object) -> tuple[int, ...]:
    return tuple(int(part) for part in str(value or "0").split(".") if part.isdigit())


def model_readiness(model: dict[str, Any], *, macos_version: str | None = None,
                    chip: str | None = None) -> dict[str, Any]:
    """Report independent package, runtime, and OS gates before scheduler admission."""
    from components import model_packages

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
    allowed = model_packages.production_allowed(model)
    requirements_ready = all((
        files_state == "files_verified", runtime_state == "ready",
        os_state == "ready", chip_state == "ready",
    ))
    dispatch_state = "eligible" if allowed and requirements_ready else "ineligible"
    if not allowed:
        admission_state = "production_blocked"
        admission_reason = "production use is disabled pending qualification"
    elif requirements_ready:
        admission_state = "ready"
        admission_reason = None
    else:
        admission_state = "requirements_not_ready"
        admission_reason = "model files, runtime, OS, or chip are not ready"
    return {
        "files_state": files_state,
        "runtime_state": runtime_state,
        "os_state": os_state,
        "chip_state": chip_state,
        "minimum_macos": minimum_os,
        "macos_version": actual_os,
        "production_allowed": allowed,
        "admission_state": admission_state,
        "admission_reason": admission_reason,
        "dispatch_state": dispatch_state,
    }
