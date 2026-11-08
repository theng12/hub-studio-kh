"""Read-only physical drive and toolchain inventory for developer gates.

Maps workspace filesystem to physical internal backing drive using macOS diskutil,
aligned with frozen wire revision 2 physicalDrive schema, and probes
caller-supplied trusted tool executables without shell or installs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import plistlib
import re
import select
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

# Nominal 512GB drive class floor (Apple 512GB SSD measures 500,277,792,768 bytes)
MIN_INTERNAL_DRIVE_BYTES: int = 500_000_000_000
DISKUTIL_PATH: str = "/usr/sbin/diskutil"
STAT_PATH: str = "/usr/bin/stat"
DEFAULT_MAX_BYTES: int = 1_048_576  # 1 MB bound on subprocess probe output
KNOWN_TOOL_IDS: tuple[str, ...] = ("python", "node", "go", "rust", "swift", "clt")

_APFS_CONTAINER_SCHEME_GUID: str = "EF57347C-0000-11AA-AA11-00306543ECAC"
_PHYSICAL_BUS_PROTOCOLS: frozenset[str] = frozenset({
    "Apple Fabric",
    "PCI-Express",
    "NVMe",
    "SATA",
    "SCSI",
    "ATAPI",
})

_TOOL_REGEXES: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"Python\s+([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "node": re.compile(r"v?([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "go": re.compile(r"go version go([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "rust": re.compile(r"rustc\s+([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    # Swift compiler version must be Apple Swift version, never swift-driver version
    "swift": re.compile(r"Apple Swift version\s+([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
    "clt": re.compile(r"(?:version:|version\s+|Apple clang version\s+|clang version\s+|^)\s*([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9._-]+)?)"),
}


def run_bounded_process(
    cmd: Sequence[str],
    timeout: float = 5.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[int, bytes, bytes]:
    """Execute a subprocess with strict runtime timeout and bounded output memory.

    Terminates and waits for the process if output exceeds max_bytes or timeout occurs.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_size = 0
    stderr_size = 0
    deadline = time.monotonic() + timeout

    try:
        assert proc.stdout is not None and proc.stderr is not None
        for pipe in (proc.stdout, proc.stderr):
            flags = fcntl.fcntl(pipe, fcntl.F_GETFL)
            fcntl.fcntl(pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        pipes = [proc.stdout, proc.stderr]
        while pipes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
            rlist, _, _ = select.select(pipes, [], [], remaining)
            if not rlist:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
            for p in rlist:
                chunk = p.read(65536)
                if not chunk:
                    pipes.remove(p)
                    continue
                if p is proc.stdout:
                    stdout_chunks.append(chunk)
                    stdout_size += len(chunk)
                else:
                    stderr_chunks.append(chunk)
                    stderr_size += len(chunk)
                if stdout_size > max_bytes or stderr_size > max_bytes:
                    _terminate_and_wait(proc)
                    raise RuntimeError(f"Output exceeded bound of {max_bytes} bytes")

        proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        return proc.returncode, b"".join(stdout_chunks), b"".join(stderr_chunks)
    except Exception:
        _terminate_and_wait(proc)
        raise
    finally:
        # Pipes are owned here; close them on every path (success included) to avoid ResourceWarning.
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


def _terminate_and_wait(proc: subprocess.Popen[bytes]) -> None:
    try:
        if proc.stdout is not None and not proc.stdout.closed:
            proc.stdout.close()
        if proc.stderr is not None and not proc.stderr.closed:
            proc.stderr.close()
        proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=0.5)
    except Exception:
        pass


def _default_diskutil_runner(args: Sequence[str], timeout: float = 5.0) -> bytes:
    code, stdout, stderr = run_bounded_process(
        [DISKUTIL_PATH, *args],
        timeout=timeout,
        max_bytes=DEFAULT_MAX_BYTES,
    )
    if code != 0 and not stdout.strip().startswith(b"<?xml"):
        raise RuntimeError(f"diskutil exited with status {code}")
    return stdout


def _default_volume_id_runner(path: str | Path, timeout: float = 5.0) -> str:
    code, stdout, stderr = run_bounded_process(
        [STAT_PATH, "-f", "%Sd", str(path)],
        timeout=timeout,
        max_bytes=4096,
    )
    if code != 0:
        raise RuntimeError(f"stat exited with status {code}")
    value = stdout.decode(errors="replace").strip()
    if not value or any(ch.isspace() for ch in value):
        raise ValueError("stat reported no filesystem device")
    return value


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


def _is_explicit_physical_whole_disk(disk_info: Mapping[str, Any]) -> bool:
    """Verify disk is an explicit physical whole disk, not a synthesized container or partition."""
    if not isinstance(disk_info, Mapping):
        return False
    if disk_info.get("WholeDisk") is not True:
        return False
    if disk_info.get("VirtualOrPhysical") == "Virtual" or disk_info.get("SystemImage") is True:
        return False
    if disk_info.get("APFSContainerReference") or disk_info.get("APFSContainerSize"):
        return False
    content = str(disk_info.get("Content") or "")
    if content == _APFS_CONTAINER_SCHEME_GUID or "Apple_APFS_Container" in content:
        return False

    # Positive physical provenance
    if disk_info.get("VirtualOrPhysical") == "Physical":
        return True
    if disk_info.get("BusProtocol") in _PHYSICAL_BUS_PROTOCOLS:
        return True
    if content in ("GUID_partition_scheme", "FDisk_partition_scheme"):
        return True
    if disk_info.get("SolidState") is True or disk_info.get("SMARTStatus") is not None:
        return True
    return False


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
        if not isinstance(volume_info, Mapping):
            return _make_drive_result(
                source="unknown",
                backing_source="unknown",
                reason={"code": "INVALID_PLIST", "detail": "Volume plist root is not a mapping"},
            )

        if volume_info.get("Error") is True:
            resolved_target = _resolve_mount_point(target)
            if resolved_target != target:
                raw_volume = run_diskutil(["info", "-plist", resolved_target])
                volume_info = plistlib.loads(raw_volume)

        if not isinstance(volume_info, Mapping) or volume_info.get("Error") is True:
            return _make_drive_result(
                source="unknown",
                backing_source="unknown",
                reason={"code": "VOLUME_LOOKUP_FAILED", "detail": "Failed to look up volume plist"},
            )

        stores = volume_info.get("APFSPhysicalStores")
        backing_source = "diskutil-info-plist"
        if stores is None and volume_info.get("APFSContainerReference"):
            container_ref = volume_info["APFSContainerReference"]
            if not isinstance(container_ref, str) or not container_ref.strip():
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "MALFORMED_CONTAINER_REF", "detail": "APFSContainerReference is not a valid string"},
                )
            raw_container = run_diskutil(["info", "-plist", container_ref])
            container_info = plistlib.loads(raw_container)
            if not isinstance(container_info, Mapping):
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "CONTAINER_LOOKUP_FAILED", "detail": "APFS container plist is not a mapping"},
                )
            stores = container_info.get("APFSPhysicalStores")

        if stores is not None:
            if not isinstance(stores, list) or len(stores) == 0:
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "NO_PHYSICAL_STORES", "detail": "APFSPhysicalStores is not a non-empty list"},
                )
            backing_disks: set[str] = set()
            for store in stores:
                if not isinstance(store, Mapping):
                    return _make_drive_result(
                        source="unknown",
                        backing_source="unknown",
                        reason={"code": "MALFORMED_STORE", "detail": "Physical store item is not a mapping"},
                    )
                store_dev = store.get("APFSPhysicalStore")
                if not isinstance(store_dev, str) or not store_dev.strip():
                    return _make_drive_result(
                        source="unknown",
                        backing_source="unknown",
                        reason={"code": "MALFORMED_STORE_ID", "detail": "APFSPhysicalStore identifier missing or invalid"},
                    )
                raw_store = run_diskutil(["info", "-plist", store_dev])
                store_info = plistlib.loads(raw_store)
                if not isinstance(store_info, Mapping):
                    return _make_drive_result(
                        source="unknown",
                        backing_source="unknown",
                        reason={"code": "STORE_LOOKUP_FAILED", "detail": "Physical store plist is not a mapping"},
                    )
                whole_id = store_info.get("ParentWholeDisk") or (store_info.get("DeviceIdentifier") if store_info.get("WholeDisk") else None)
                if not isinstance(whole_id, str) or not whole_id.strip():
                    return _make_drive_result(
                        source="unknown",
                        backing_source="unknown",
                        reason={"code": "UNRESOLVED_STORE", "detail": "Could not determine whole disk for store"},
                    )
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
            if not isinstance(whole_id, str) or not whole_id.strip():
                return _make_drive_result(
                    source="unknown",
                    backing_source="unknown",
                    reason={"code": "NO_WHOLE_DISK", "detail": "No parent whole disk on non-APFS volume"},
                )
            backing_id = whole_id

        raw_disk = run_diskutil(["info", "-plist", backing_id])
        disk_info = plistlib.loads(raw_disk)
        if not isinstance(disk_info, Mapping):
            return _make_drive_result(
                source="unknown",
                backing_source="unknown",
                reason={"code": "DISK_LOOKUP_FAILED", "detail": "Backing disk plist is not a mapping"},
            )

        if not _is_explicit_physical_whole_disk(disk_info):
            is_virtual = disk_info.get("VirtualOrPhysical") == "Virtual" or disk_info.get("SystemImage") is True
            return _make_drive_result(
                device_id=backing_id,
                internal=False if is_virtual else disk_info.get("Internal") if isinstance(disk_info.get("Internal"), bool) else None,
                physical=False if is_virtual else None,
                size_bytes=None,
                source="diskutil-info-plist" if is_virtual else "unknown",
                workspace_backing_device_id=backing_id,
                backing_source=backing_source if is_virtual else "unknown",
                reason={
                    "code": "VIRTUAL_DRIVE" if is_virtual else "NOT_PHYSICAL_WHOLE_DISK",
                    "detail": "Backing device is not a verified physical whole disk",
                },
            )

        is_internal = disk_info.get("Internal")
        if is_internal is True:
            raw_cap = disk_info.get("TotalSize") or disk_info.get("Size") or disk_info.get("IOKitSize")
            # Positive integer capacity required; strictly reject bool/string coercion
            if isinstance(raw_cap, int) and not isinstance(raw_cap, bool) and raw_cap >= 1:
                return _make_drive_result(
                    device_id=backing_id,
                    internal=True,
                    physical=True,
                    size_bytes=raw_cap,
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
                reason={"code": "MISSING_PHYSICAL_CAPACITY", "detail": "Backing drive reports no positive physical capacity"},
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
                reason={"code": "EXTERNAL_DRIVE", "detail": "Backing drive is external"},
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
                reason={"code": "UNKNOWN_INTERNAL_STATE", "detail": "Internal state for drive is unknown"},
            )

    except Exception as exc:
        return _make_drive_result(
            source="unknown",
            backing_source="unknown",
            reason={"code": "PROBE_FAILED", "detail": f"Probe error: {type(exc).__name__}"},
        )


def probe_work_volume(
    workspace_path: str | Path,
    *,
    drive_info: Mapping[str, Any] | None = None,
    statvfs: Callable[[str | Path], Any] | None = None,
    volume_id_runner: Callable[[str | Path], str] | None = None,
    runner: Callable[[Sequence[str]], bytes] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Exact statfs free/total bytes and the volume/backing identity for a work root.

    `free_bytes` and `total_bytes` are `f_bavail`/`f_blocks` times the fragment size from the
    filesystem that holds `workspace_path` (the kernel resolves firmlinks). `volume_id` is that
    filesystem's DeviceIdentifier (`disk3s5`, from `stat -f %Sd`, not the root snapshot volume);
    `device_id`/`physical` are the backing whole disk and its physical flag, reused from
    `probe_internal_drive` when the caller already has it. Unknown facts stay `null` with a
    reason; a measured zero stays zero.
    """
    observed_at = _current_iso_z()
    path = str(workspace_path)
    mount_point: str | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None
    volume_id: str | None = None
    device_id: str | None = None
    physical: bool | None = None
    failures: list[tuple[str, str]] = []

    read_statvfs = statvfs or os.statvfs
    try:
        vfs = read_statvfs(path)
        fragment = int(getattr(vfs, "f_frsize", 0) or getattr(vfs, "f_bsize", 0))
        blocks = getattr(vfs, "f_blocks", None)
        available = getattr(vfs, "f_bavail", None)
        if (isinstance(fragment, bool) or not isinstance(fragment, int) or fragment <= 0
                or isinstance(blocks, bool) or not isinstance(blocks, int) or blocks < 0
                or isinstance(available, bool) or not isinstance(available, int) or available < 0):
            raise ValueError("statvfs returned no usable block counts")
        total_bytes = blocks * fragment
        free_bytes = available * fragment
    except (OSError, ValueError, TypeError) as exc:
        failures.append(("STATFS_FAILED",
                         f"statvfs on the work volume failed: {type(exc).__name__}"))

    read_volume_id = volume_id_runner or (
        lambda value: _default_volume_id_runner(value, timeout=timeout_seconds))
    try:
        candidate = read_volume_id(path)
        if (not isinstance(candidate, str) or not candidate.strip()
                or any(ch.isspace() for ch in candidate)):
            raise ValueError("no filesystem device")
        volume_id = candidate.strip()
    except Exception as exc:  # noqa: BLE001 - a missing id is null, never a guessed one
        failures.append(("VOLUME_ID_UNKNOWN",
                         f"the volume DeviceIdentifier could not be read: {type(exc).__name__}"))

    if volume_id is not None:
        run_diskutil = runner or (
            lambda args: _default_diskutil_runner(args, timeout=timeout_seconds))
        try:
            volume_info = plistlib.loads(run_diskutil(["info", "-plist", volume_id]))
            if not isinstance(volume_info, Mapping) or volume_info.get("Error") is True:
                raise ValueError("volume lookup failed")
            reported = volume_info.get("DeviceIdentifier")
            if (isinstance(reported, str) and reported.strip()
                    and reported.strip() != volume_id):
                raise ValueError("volume identity changed")
            mount = volume_info.get("MountPoint")
            if not isinstance(mount, str) or not mount.strip():
                raise ValueError("volume plist has no MountPoint")
            mount_point = mount.strip()
        except Exception as exc:  # noqa: BLE001 - a missing mount is null, never guessed
            failures.append(("MOUNT_POINT_UNKNOWN",
                             f"the work volume mount could not be read: {type(exc).__name__}"))

    drive = drive_info
    if drive is None:
        drive = probe_internal_drive(workspace_path, runner=runner,
                                     timeout_seconds=timeout_seconds)
    candidate_device = drive.get("device_id") if isinstance(drive, Mapping) else None
    if isinstance(candidate_device, str) and candidate_device.strip():
        device_id = candidate_device.strip()
    candidate_physical = drive.get("physical") if isinstance(drive, Mapping) else None
    if isinstance(candidate_physical, bool):
        physical = candidate_physical
    if device_id is None or physical is None:
        backing_reason = drive.get("reason") if isinstance(drive, Mapping) else None
        if isinstance(backing_reason, Mapping) and isinstance(backing_reason.get("code"), str):
            failures.append((backing_reason["code"],
                             str(backing_reason.get("detail")
                                 or "the physical backing device is unknown")))
        else:
            failures.append(("BACKING_UNKNOWN",
                             "the physical backing device could not be identified"))

    reason = None
    if failures:
        reason = {"code": failures[0][0],
                  "detail": "; ".join(f"{code}: {detail}" for code, detail in failures)}
    return {
        "path": path,
        "mount_point": mount_point,
        "volume_id": volume_id,
        "device_id": device_id,
        "physical": physical,
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "observed_at": observed_at,
        "reason": reason,
    }


def is_internal_drive_eligible(drive_info: Mapping[str, Any], min_bytes: int = MIN_INTERNAL_DRIVE_BYTES) -> bool:
    """Return True only when drive is verified internal physical with size_bytes >= threshold."""
    if not isinstance(drive_info, Mapping):
        return False
    if isinstance(min_bytes, bool) or not isinstance(min_bytes, int) or min_bytes < 0:
        return False
    if drive_info.get("internal") is not True or drive_info.get("physical") is not True:
        return False
    size = drive_info.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return False
    if drive_info.get("source") != "diskutil-info-plist":
        return False
    if drive_info.get("backing_source") not in ("diskutil-info-plist", "diskutil-apfs-list-plist"):
        return False
    dev_id = drive_info.get("device_id")
    backing_id = drive_info.get("workspace_backing_device_id")
    if not isinstance(dev_id, str) or not isinstance(backing_id, str) or not dev_id:
        return False
    if dev_id != backing_id:
        return False
    return size >= min_bytes


def _default_tool_runner(args: Sequence[str], timeout: float) -> tuple[int, str, str]:
    code, stdout_bytes, stderr_bytes = run_bounded_process(args, timeout=timeout, max_bytes=DEFAULT_MAX_BYTES)
    return code, stdout_bytes.decode(errors="replace"), stderr_bytes.decode(errors="replace")


def probe_tool_version(
    tool_id: str,
    executable_path: str | Path | None = None,
    runner: Callable[[Sequence[str], float], tuple[int, str, str]] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Probe installed version of a toolchain executable without shell or mutation."""
    tid = str(tool_id).strip()
    tid_norm = tid.lower().replace("-", "_")
    if tid_norm not in KNOWN_TOOL_IDS:
        return {
            "id": tid,
            "version": None,
            "available": False,
            "reason": f"Unsupported or unknown tool ID: {tid}",
        }

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
                "reason": "Executable not found or not executable",
            }

    base_name = os.path.basename(path_str).lower()

    if tid_norm == "python":
        # Per owner rule: Python probe must use -I -B --version for isolated execution
        args = [path_str, "-I", "-B", "--version"]
    elif tid_norm == "go":
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
            "reason": f"Probe execution failed: {type(exc).__name__}",
        }

    if exit_code != 0:
        return {
            "id": tid,
            "version": None,
            "available": False,
            "reason": f"Executable exited with non-zero status {exit_code}",
        }

    output = stdout.strip() or stderr.strip()
    regex = _TOOL_REGEXES[tid_norm]
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
        "reason": "Failed to parse version from output",
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
