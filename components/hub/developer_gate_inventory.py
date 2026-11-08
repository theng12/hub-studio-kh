"""Read-only physical drive and toolchain inventory for developer gates.

Maps workspace filesystem to physical internal backing drive using macOS diskutil,
aligned with frozen wire revision 2 physicalDrive schema, and probes
caller-supplied trusted tool executables without shell or installs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import plistlib
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence

MIN_INTERNAL_DRIVE_BYTES: int = 512_000_000_000
KNOWN_TOOL_IDS: tuple[str, ...] = ("python", "node", "go", "rust", "swift", "clt")

_TOOL_REGEXES: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"Python\s+([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "node": re.compile(r"v?([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "go": re.compile(r"go version go([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "rust": re.compile(r"rustc\s+([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "swift": re.compile(r"(?:Apple Swift version|swift-driver version:)\s+([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "clt": re.compile(r"(?:version:|version\s+|Apple clang version\s+|clang version\s+|^)\s*([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
}


def _default_diskutil_runner(args: Sequence[str], timeout: float = 5.0) -> bytes:
    cmd = ["diskutil", *args]
    res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    if res.returncode != 0 and not res.stdout.strip().startswith(b"<?xml"):
        raise RuntimeError(f"diskutil exited with status {res.returncode}: {res.stderr.decode(errors='replace')}")
    return res.stdout


def _resolve_mount_point(path: str | Path) -> str:
    # shortcut: firmlinks resolve to /System/Volumes/Data or /; walks parents to nearest mount point
    p = Path(path).resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    mount = p
    while not os.path.ismount(str(mount)) and mount.parent != mount:
        mount = mount.parent
    return str(mount)


def _current_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def probe_internal_drive(
    workspace_path: str | Path | None = None,
    runner: Callable[[Sequence[str]], bytes] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Read physical backing drive capacity with provenance aligned to wire r2 physicalDrive."""
    run_diskutil = runner or (lambda args: _default_diskutil_runner(args, timeout=timeout_seconds))
    observed_at = _current_iso_z()

    def _make_drive_result(
        device_id: str | None = None,
        internal: bool | None = None,
        physical: bool | None = None,
        size_bytes: int | None = None,
        source: str = "unknown",
        workspace_backing_device_id: str | None = None,
        backing_source: str = "unknown",
        reason: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "device_id": device_id,
            "internal": internal,
            "physical": physical,
            "size_bytes": size_bytes,
            "source": source,
            "workspace_backing_device_id": workspace_backing_device_id,
            "backing_source": backing_source,
            "observed_at": observed_at,
            "reason": reason,
        }

    try:
        if workspace_path is None:
            target = "/"
        else:
            target = str(workspace_path)

        raw_volume = run_diskutil(["info", "-plist", target])
        volume_info = plistlib.loads(raw_volume)
        if volume_info.get("Error") is True:
            resolved_target = _resolve_mount_point(target)
            if resolved_target != target:
                raw_volume = run_diskutil(["info", "-plist", resolved_target])
                volume_info = plistlib.loads(raw_volume)

        if volume_info.get("Error") is True:
            err_msg = volume_info.get("ErrorMessage") or "diskutil volume lookup failed"
            return _make_drive_result(
                source="unknown",
                backing_source="unknown",
                reason={"code": "VOLUME_LOOKUP_FAILED", "detail": err_msg[:2048]},
            )

        stores = volume_info.get("APFSPhysicalStores")
        backing_source = "diskutil-info-plist"
        if stores is None and volume_info.get("APFSContainerReference"):
            container_ref = volume_info["APFSContainerReference"]
            raw_container = run_diskutil(["info", "-plist", container_ref])
            container_info = plistlib.loads(raw_container)
            stores = container_info.get("APFSPhysicalStores")

        if stores is not None:
            if len(stores) == 0:
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "NO_PHYSICAL_STORES", "detail": "APFS container has no physical stores"},
                )
            backing_disks: set[str] = set()
            for store in stores:
                store_dev = store.get("APFSPhysicalStore")
                if not store_dev:
                    continue
                raw_store = run_diskutil(["info", "-plist", store_dev])
                store_info = plistlib.loads(raw_store)
                whole_id = store_info.get("ParentWholeDisk") or (store_info.get("DeviceIdentifier") if store_info.get("WholeDisk") else None)
                if whole_id:
                    backing_disks.add(whole_id)

            if len(backing_disks) == 0:
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "NO_BACKING_DISKS", "detail": "Could not identify parent whole disk from stores"},
                )
            if len(backing_disks) > 1:
                disk_list = ",".join(sorted(backing_disks))
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "AMBIGUOUS_BACKING_DRIVES", "detail": f"Workspace backed by multiple drives: {disk_list}"},
                )
            backing_id = next(iter(backing_disks))
        else:
            whole_id = volume_info.get("ParentWholeDisk") or (volume_info.get("DeviceIdentifier") if volume_info.get("WholeDisk") else None)
            if not whole_id:
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "NO_WHOLE_DISK", "detail": "No parent whole disk on non-APFS volume"},
                )
            backing_id = whole_id

        raw_disk = run_diskutil(["info", "-plist", backing_id])
        disk_info = plistlib.loads(raw_disk)

        is_virtual = disk_info.get("VirtualOrPhysical") == "Virtual" or disk_info.get("SystemImage") is True
        if is_virtual:
            return _make_drive_result(
                device_id=backing_id,
                internal=False,
                physical=False,
                size_bytes=None,
                source="diskutil-info-plist",
                workspace_backing_device_id=backing_id,
                backing_source=backing_source,
                reason={"code": "VIRTUAL_DRIVE", "detail": f"Backing device {backing_id} is virtual"},
            )

        is_internal = disk_info.get("Internal")
        if is_internal is True:
            capacity = disk_info.get("TotalSize") or disk_info.get("Size") or disk_info.get("IOKitSize")
            if capacity and int(capacity) >= 1:
                return _make_drive_result(
                    device_id=backing_id,
                    internal=True,
                    physical=True,
                    size_bytes=int(capacity),
                    source="diskutil-info-plist",
                    workspace_backing_device_id=backing_id,
                    backing_source=backing_source,
                    reason=None,
                )
            return _make_drive_result(
                device_id=backing_id,
                internal=True,
                physical=True,
                size_bytes=None,
                source="diskutil-info-plist",
                workspace_backing_device_id=backing_id,
                backing_source=backing_source,
                reason={"code": "MISSING_PHYSICAL_CAPACITY", "detail": f"Backing drive {backing_id} reports no physical capacity"},
            )
        elif is_internal is False:
            return _make_drive_result(
                device_id=backing_id,
                internal=False,
                physical=True,
                size_bytes=None,
                source="diskutil-info-plist",
                workspace_backing_device_id=backing_id,
                backing_source=backing_source,
                reason={"code": "EXTERNAL_DRIVE", "detail": f"Backing drive {backing_id} is external"},
            )
        else:
            return _make_drive_result(
                device_id=backing_id,
                internal=None,
                physical=True,
                size_bytes=None,
                source="unknown",
                workspace_backing_device_id=backing_id,
                backing_source="unknown",
                reason={"code": "UNKNOWN_INTERNAL_STATE", "detail": f"Internal state for drive {backing_id} is unknown"},
            )

    except Exception as exc:
        return _make_drive_result(
            source="unknown",
            backing_source="unknown",
            reason={"code": "PROBE_FAILED", "detail": f"{type(exc).__name__}: {exc}"[:2048]},
        )


def is_internal_drive_eligible(drive_info: Mapping[str, Any], min_bytes: int = MIN_INTERNAL_DRIVE_BYTES) -> bool:
    """Return True only when drive is verified internal physical with size_bytes >= threshold."""
    if not isinstance(drive_info, Mapping):
        return False
    if drive_info.get("internal") is not True or drive_info.get("physical") is not True:
        return False
    size = drive_info.get("size_bytes")
    if size is None or not isinstance(size, int):
        return False
    if drive_info.get("source") != "diskutil-info-plist" or drive_info.get("backing_source") != "diskutil-info-plist":
        return False
    dev_id = drive_info.get("device_id")
    backing_id = drive_info.get("workspace_backing_device_id")
    if not dev_id or dev_id != backing_id:
        return False
    return size >= min_bytes


def _default_tool_runner(args: Sequence[str], timeout: float) -> tuple[int, str, str]:
    res = subprocess.run(args, capture_output=True, timeout=timeout, check=False, text=True)
    return res.returncode, res.stdout, res.stderr


def probe_tool_version(
    tool_id: str,
    executable_path: str | Path | None = None,
    runner: Callable[[Sequence[str], float], tuple[int, str, str]] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Probe installed version of a toolchain executable without shell or mutation."""
    tid = str(tool_id).strip()
    if not executable_path:
        return {
            "id": tid,
            "version": None,
            "available": False,
            "reason": "Executable path not supplied",
        }

    path_str = str(executable_path)
    run_tool = runner or _default_tool_runner

    if runner is None:
        if not os.path.isfile(path_str) or not os.access(path_str, os.X_OK):
            return {
                "id": tid,
                "version": None,
                "available": False,
                "reason": f"Executable not found or not executable: {path_str}",
            }

    tid_norm = tid.lower().replace("-", "_")
    base_name = os.path.basename(path_str).lower()

    if tid_norm == "go":
        args = [path_str, "version"]
    elif tid_norm in ("clt", "clt_sdk"):
        if "pkgutil" in base_name:
            args = [path_str, "--pkg-info=com.apple.pkg.CLTools_Executables"]
        elif "xcrun" in base_name:
            args = [path_str, "--show-sdk-version"]
        elif "xcode_select" in base_name or "xcode-select" in base_name:
            args = [path_str, "-v"]
        else:
            args = [path_str, "--version"]
    else:
        args = [path_str, "--version"]

    try:
        exit_code, stdout, stderr = run_tool(args, timeout_seconds)
    except subprocess.TimeoutExpired:
        return {
            "id": tid,
            "version": None,
            "available": False,
            "reason": f"Probe timed out after {timeout_seconds}s",
        }
    except Exception as exc:
        return {
            "id": tid,
            "version": None,
            "available": False,
            "reason": f"Probe execution failed: {type(exc).__name__}: {exc}",
        }

    if exit_code != 0:
        err_msg = (stderr or stdout).strip()[:200]
        return {
            "id": tid,
            "version": None,
            "available": False,
            "reason": f"Executable exited with code {exit_code}: {err_msg}",
        }

    output = stdout.strip() or stderr.strip()
    regex = _TOOL_REGEXES.get(tid_norm) or re.compile(r"([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)")
    match = regex.search(output)
    if match:
        return {
            "id": tid,
            "version": match.group(1),
            "available": True,
            "reason": None,
        }

    return {
        "id": tid,
        "version": None,
        "available": False,
        "reason": f"Failed to parse version from output: {output[:200]}",
    }


def probe_toolchains(
    executable_paths: Mapping[str, str | Path | None] | None = None,
    tool_ids: Sequence[str] | None = None,
    requested_tools: Sequence[Mapping[str, Any]] | None = None,
    runner: Callable[[Sequence[str], float], tuple[int, str, str]] | None = None,
    timeout_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    """Probe installed versions for requested or standard tools."""
    paths = dict(executable_paths or {})
    if tool_ids is not None:
        ids = list(tool_ids)
    elif requested_tools is not None:
        ids = [t["id"] for t in requested_tools if isinstance(t, Mapping) and "id" in t]
    else:
        ids = list(KNOWN_TOOL_IDS)

    # requested values are never installed facts; only probe results are used
    results: list[dict[str, Any]] = []
    for tid in ids:
        results.append(
            probe_tool_version(
                tool_id=tid,
                executable_path=paths.get(tid),
                runner=runner,
                timeout_seconds=timeout_seconds,
            )
        )
    return results


def collect_developer_gate_inventory(
    workspace_path: str | Path | None = None,
    executable_paths: Mapping[str, str | Path | None] | None = None,
    tool_ids: Sequence[str] | None = None,
    requested_tools: Sequence[Mapping[str, Any]] | None = None,
    disk_runner: Callable[[Sequence[str]], bytes] | None = None,
    tool_runner: Callable[[Sequence[str], float], tuple[int, str, str]] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Collect full read-only inventory data: internal physical drive and toolchains."""
    return {
        "internal_physical_drive": probe_internal_drive(
            workspace_path=workspace_path,
            runner=disk_runner,
            timeout_seconds=timeout_seconds,
        ),
        "toolchains": probe_toolchains(
            executable_paths=executable_paths,
            tool_ids=tool_ids,
            requested_tools=requested_tools,
            runner=tool_runner,
            timeout_seconds=timeout_seconds,
        ),
    }
