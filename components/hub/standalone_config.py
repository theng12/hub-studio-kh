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
DEFAULT_RETENTION_DAYS = 7                  # generated output -> Trash Folder (owner rule 2026-10-19; was 3)
DEFAULT_CLEANUP_ENABLED = True
DEFAULT_CLEANUP_INTERVAL_SECONDS = 300
DEFAULT_MINIMUM_FREE_BYTES = 5_000_000_000
DEFAULT_TRASH_HANDOFF_DAYS = 7              # items older than this in the Trash Folder go to macOS Trash
DEFAULT_TRASH_HANDOFF_GB = 20               # ...or the Trash Folder holds more than this (every root)
# `_REQUIRED_WORKERS` (worker id -> modality) is the inverse of the Hub's modality vocabulary,
# derived in `backend/vocabulary.py` rather than restated here. It is kept as a module attribute
# because it is the inverse table, but `_worker()` no longer looks a descriptor's id up in it:
# the descriptor declares its own modality and the vocabulary validates that, so a worker id the
# Hub does not route fails with the modality named and the file to edit (see `_worker`).
_SETTINGS_FILE = "device_settings.json"
# Every default this file has ever shipped, keyed by the `defaults_version` that carried it. A file
# stamped with an older version whose value equals that version's own default was never chosen by the
# operator, so it takes the current default; a value that differs was chosen and is kept. Every write
# stamps the current version, so an operator who saves exactly an old default afterwards is not
# migrated again. Version 0 is a file written before the version stamp existed.
#
#   0  pre-0.6.117:  50 GB / 30 days / 1 GB free
#   2  0.6.117+:     20 GB /  3 days / 5 GB free
#   3  2026-10-19:   20 GB /  7 days / 5 GB free, Trash handoff 7 days or 20 GB
_DEFAULTS_VERSION = 3
_LEGACY_DEFAULTS: dict[int, dict[str, int]] = {
    0: {"max_bytes": 50_000_000_000, "retention_days": 30, "minimum_free_bytes": 1_000_000_000},
    2: {"retention_days": 3},
}


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
# value it read before the other wrote (a toggled retention was the one at risk).
_SETTINGS_LOCK = threading.RLock()
_logged_problem: str | None = None


def _read_settings() -> tuple[dict, str | None]:
    """The saved settings and, when the file exists but is damaged, what is wrong with it.

    An absent file is not a problem (defaults). A file that is unreadable, not JSON or not an object
    is a problem: the values fall back to defaults and the problem is logged loudly once and surfaced
    by `settings_warnings`, so a Mac on defaults never reads as though someone chose them.
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
    if problem != _logged_problem:
        _logged_problem = problem
        if problem:
            log.error("%s: this Mac uses the default settings until the file is fixed", problem)
    return value, problem


def settings_warnings() -> list[str]:
    """Plain-language warnings about this Mac's settings file, for the Hub's settings route."""
    problem = _read_settings()[1]
    return [f"{problem}. This Mac is using the default storage limits until the file is repaired "
            f"(save the settings again to rewrite it)."] if problem else []


def _settings() -> dict[str, int | bool]:
    value, problem = _read_settings()
    if isinstance(value, dict):
        try:
            version = int(value.get("defaults_version", 0))
        except (TypeError, ValueError):
            version = 0
        if version < _DEFAULTS_VERSION:
            never_chosen: dict[str, set[int]] = {}
            for older in range(version, _DEFAULTS_VERSION):
                for key, item in _LEGACY_DEFAULTS.get(older, {}).items():
                    never_chosen.setdefault(key, set()).add(item)
            value = {key: item for key, item in value.items()
                     if item not in never_chosen.get(key, ())}
    max_bytes = value.get("max_bytes", DEFAULT_MAX_BYTES) if isinstance(value, dict) else DEFAULT_MAX_BYTES
    retention_days = value.get("retention_days", DEFAULT_RETENTION_DAYS) if isinstance(value, dict) else DEFAULT_RETENTION_DAYS
    enabled = value.get("cleanup_enabled", DEFAULT_CLEANUP_ENABLED) if isinstance(value, dict) else DEFAULT_CLEANUP_ENABLED
    interval = value.get("cleanup_interval_seconds", DEFAULT_CLEANUP_INTERVAL_SECONDS) if isinstance(value, dict) else DEFAULT_CLEANUP_INTERVAL_SECONDS
    minimum_free = value.get("minimum_free_bytes", DEFAULT_MINIMUM_FREE_BYTES) if isinstance(value, dict) else DEFAULT_MINIMUM_FREE_BYTES
    trash_handoff = value.get("trash_handoff_days", DEFAULT_TRASH_HANDOFF_DAYS) if isinstance(value, dict) else DEFAULT_TRASH_HANDOFF_DAYS
    trash_gb = value.get("trash_handoff_gb", DEFAULT_TRASH_HANDOFF_GB) if isinstance(value, dict) else DEFAULT_TRASH_HANDOFF_GB
    return {
        "max_bytes": max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else DEFAULT_MAX_BYTES,
        "retention_days": retention_days if isinstance(retention_days, int) and retention_days > 0 else DEFAULT_RETENTION_DAYS,
        "cleanup_enabled": enabled if isinstance(enabled, bool) else DEFAULT_CLEANUP_ENABLED,
        "cleanup_interval_seconds": interval if isinstance(interval, int) and interval >= 60 else DEFAULT_CLEANUP_INTERVAL_SECONDS,
        "minimum_free_bytes": minimum_free if isinstance(minimum_free, int) and minimum_free >= 0 else DEFAULT_MINIMUM_FREE_BYTES,
        "trash_handoff_days": trash_handoff if isinstance(trash_handoff, int) and trash_handoff > 0 else DEFAULT_TRASH_HANDOFF_DAYS,
        "trash_handoff_gb": trash_gb if isinstance(trash_gb, int) and trash_gb > 0 else DEFAULT_TRASH_HANDOFF_GB,
    }


def preset_choice() -> str | None:
    """The preset this Mac's owner chose, or None for "the one its memory implies".

    Its own reader because it is the one **string** device setting: `_settings()` is typed
    int|bool throughout and validating a preset id as a number would be the wrong shape. A file
    with no `model_preset` is unset, which is a real answer - the installer offers the starter for
    the Mac's tier and nobody has overridden it.
    """
    value, problem = _read_settings()
    if problem is not None or not isinstance(value, dict):
        return None
    chosen = value.get("model_preset")
    return chosen.strip() if isinstance(chosen, str) and chosen.strip() else None


def preset_cleanup_keep() -> list[str]:
    """Package ids this Mac's owner has said the one-time cleanup must leave alone."""
    value, problem = _read_settings()
    if problem is not None or not isinstance(value, dict):
        return []
    kept = value.get("preset_cleanup_keep")
    return [item for item in kept if isinstance(item, str)] if isinstance(kept, list) else []


def set_preset_choice(preset_id: str | None) -> str | None:
    """Save (or clear) this Mac's preset. The id is checked against the shipped presets."""
    from components import model_presets
    if preset_id:
        model_presets.by_id(preset_id)          # raises PresetError naming an unknown id
    with _SETTINGS_LOCK:
        value, _problem = _read_settings()
        value = dict(value) if isinstance(value, dict) else {}
        value["defaults_version"] = _DEFAULTS_VERSION
        if preset_id:
            value["model_preset"] = preset_id
        else:
            value.pop("model_preset", None)
        _atomic_write(_settings_path(), value)
    return preset_choice()


def load_device_settings() -> dict[str, dict[str, int | bool]]:
    return {
        "defaults": {"max_bytes": DEFAULT_MAX_BYTES, "retention_days": DEFAULT_RETENTION_DAYS,
                     "cleanup_enabled": DEFAULT_CLEANUP_ENABLED,
                     "cleanup_interval_seconds": DEFAULT_CLEANUP_INTERVAL_SECONDS,
                     "minimum_free_bytes": DEFAULT_MINIMUM_FREE_BYTES,
                     "trash_handoff_days": DEFAULT_TRASH_HANDOFF_DAYS,
                     "trash_handoff_gb": DEFAULT_TRASH_HANDOFF_GB},
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
    handoff = value["trash_handoff_days"]
    if isinstance(handoff, bool) or not isinstance(handoff, int) or handoff <= 0:
        raise ValueError("trash_handoff_days must be a positive integer")
    handoff_gb = value["trash_handoff_gb"]
    if isinstance(handoff_gb, bool) or not isinstance(handoff_gb, int) or handoff_gb <= 0:
        raise ValueError("trash_handoff_gb must be a positive integer")
    return value


def update_device_settings(**changes: object) -> dict[str, dict[str, int | bool]]:
    """Change only the named settings, atomically: read, validate, write under one lock.

    The route used to resolve every field from a read made before the write, so two concurrent
    changes (the output limits and the keep-all toggle) could undo each other.
    """
    with _SETTINGS_LOCK:
        value = preview_device_settings(**changes)
        # **The settings this function does not name are carried over, not dropped.** It writes the
        # whole file, and `preview_device_settings` returns only the fields it validates - so a save
        # of the storage limits used to erase `model_preset` and `preset_cleanup_keep`, which are
        # strings and a list and have their own readers below. The alternative, widening `value`
        # into this function's typed return, would make every caller handle two shapes it does not
        # use; carrying the file's own untouched keys is the smaller and safer statement.
        existing, _problem = _read_settings()
        carried = {key: item for key, item in (existing or {}).items()
                   if key not in value and key != "defaults_version"}
        _atomic_write(_settings_path(),
                      {**carried, **value, "defaults_version": _DEFAULTS_VERSION})
        return load_device_settings()


def save_device_settings(*, max_bytes: int, retention_days: int, cleanup_enabled: bool = DEFAULT_CLEANUP_ENABLED,
                         cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS,
                         minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
                         trash_handoff_days: int | None = None,
                         trash_handoff_gb: int | None = None,
                         ) -> dict[str, dict[str, int | bool]]:
    return update_device_settings(
        max_bytes=max_bytes, retention_days=retention_days, cleanup_enabled=cleanup_enabled,
        cleanup_interval_seconds=cleanup_interval_seconds, minimum_free_bytes=minimum_free_bytes,
        **({} if trash_handoff_days is None else {"trash_handoff_days": trash_handoff_days}),
        **({} if trash_handoff_gb is None else {"trash_handoff_gb": trash_handoff_gb}),
        )


def restore_device_defaults() -> dict[str, dict[str, int]]:
    with _SETTINGS_LOCK:
        _atomic_write(_settings_path(), {
            "max_bytes": DEFAULT_MAX_BYTES,
            "retention_days": DEFAULT_RETENTION_DAYS,
            "cleanup_enabled": DEFAULT_CLEANUP_ENABLED,
            "cleanup_interval_seconds": DEFAULT_CLEANUP_INTERVAL_SECONDS,
            "minimum_free_bytes": DEFAULT_MINIMUM_FREE_BYTES,
            "trash_handoff_days": DEFAULT_TRASH_HANDOFF_DAYS,
            "trash_handoff_gb": DEFAULT_TRASH_HANDOFF_GB,
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
