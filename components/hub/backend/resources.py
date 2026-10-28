"""Host + per-studio resource monitoring.

Host stats come from psutil. Per-studio memory is resolved port → PID → process
tree RSS. On macOS, system-wide psutil.net_connections needs elevated rights,
so we resolve listening PIDs with `lsof` (same-user processes — the studios all
run as this user). Process handles are cached so cpu_percent() deltas are
meaningful across polls.

Apple Silicon note: unified memory means there is no separate VRAM figure —
process RSS + host memory pressure is the honest picture.
"""

from functools import lru_cache
import ipaddress
import json
import shutil
import socket
import subprocess
import time

import psutil

from components import host_memory
from . import network_link
from .registry import DATA_DIR

_proc_cache: dict[int, psutil.Process] = {}
_proxy_alert_state = {"degraded": False}

CADDY_RSS_WARN_GB = 1.0
CADDY_FD_WARN = 1000

# Both answers below were recomputed on every call: `proxy_stats` walks every process on the Mac
# (~50 ms, every 5 s poll, for a Pinokio Caddy the standalone app does not run) and each studio's
# listener was found with an `lsof` subprocess (~90 ms x 6 studios), which made
# `/api/hub/resources` - read by every controller for every node - take ~0.6 s of CPU.
PROXY_STATS_TTL_S = 60.0
LISTENER_TTL_S = 30.0
_proxy_stats_cache: tuple[float, dict] | None = None
_listener_cache: dict[int, tuple[float, list[int]]] = {}


@lru_cache(maxsize=1)
def apple_chip_name() -> str | None:
    """Return the Mac's marketing chip name without polling sysctl every tick.

    `sysctl` lives in `/usr/sbin`, which is not on the PATH of an app started by
    launchd or of a minimal test harness. Invoking it by bare name alone made the
    chip unknown in exactly the environment the packaged app runs in, so the
    absolute path is tried first. Apple Silicon macOS builds that report an empty
    `machdep.cpu.brand_string` fall back to `system_profiler`'s `chip_type`.
    """
    for command in (["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                    ["sysctl", "-n", "machdep.cpu.brand_string"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            continue
        value = result.stdout.strip()
        if value:
            return value
    for command in (["/usr/sbin/system_profiler", "SPHardwareDataType", "-json"],
                    ["system_profiler", "SPHardwareDataType", "-json"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            rows = json.loads(result.stdout or "{}").get("SPHardwareDataType", [])
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
            continue
        value = str((rows[0].get("chip_type") if rows else "") or "").strip()
        if value:
            return value
    return None


@lru_cache(maxsize=1)
def apple_machine_type() -> str | None:
    """Return the Mac product family used by the hardware-profile catalog.

    Same PATH reasoning as `apple_chip_name`: the absolute `/usr/sbin` path is
    tried first because a packaged app does not inherit a login shell's PATH.
    """
    for command in (["/usr/sbin/system_profiler", "SPHardwareDataType", "-json"],
                    ["system_profiler", "SPHardwareDataType", "-json"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            rows = json.loads(result.stdout or "{}").get("SPHardwareDataType", [])
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
            continue
        value = str((rows[0].get("machine_name") if rows else "") or "").strip()
        if value:
            return value
    return None


def hardware_identity() -> dict:
    """Stable local hardware facts suitable for profile matching."""
    return {
        "machine_name": socket.gethostname().split(".", 1)[0],
        "machine_type": apple_machine_type(),
        "chip": apple_chip_name(),
        "memory_gb": round(psutil.virtual_memory().total / (1024 ** 3)),
    }


def network_addresses() -> list[dict]:
    """This Mac's IPv4 interface addresses, for classifying who shares a local network.

    Facts only (interface, address, prefix length); whether one is a private LAN or Tailscale's
    100.64.0.0/10 is the reader's call. Loopback and link-local addresses say nothing about a
    shared network and are left out.
    """
    try:
        interfaces = psutil.net_if_addrs()
    except (OSError, psutil.Error):
        return []
    rows: list[dict] = []
    for name in sorted(interfaces):
        for entry in interfaces[name]:
            if entry.family != socket.AF_INET or not entry.address or not entry.netmask:
                continue
            try:
                interface = ipaddress.ip_interface(f"{entry.address}/{entry.netmask}")
            except ValueError:
                continue
            if interface.ip.is_loopback or interface.ip.is_link_local:
                continue
            rows.append({"interface": name, "address": str(interface.ip),
                         "prefix_length": interface.network.prefixlen})
    return rows[:16]


def host_stats() -> dict:
    vm = psutil.virtual_memory()
    stats = {
        "chip": apple_chip_name(),
        "machine_type": apple_machine_type(),
        "total_gb": round(vm.total / 1e9, 2),
        "used_gb": round(vm.used / 1e9, 2),
        "available_gb": round(host_memory.available_bytes(vm) / 1e9, 2),
        "percent": vm.percent,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "network": network_addresses(),
    }
    try:
        la1, la5, la15 = psutil.getloadavg()
        stats["load_avg"] = [round(la1, 2), round(la5, 2), round(la15, 2)]
    except OSError:
        stats["load_avg"] = None
    try:
        # The state root is where this Mac's generated output and models live, so its free
        # space is the number that decides whether the next job can be written.
        usage = shutil.disk_usage(DATA_DIR)
        stats["disk_free_gb"] = round(usage.free / 1e9, 2)
        stats["disk_total_gb"] = round(usage.total / 1e9, 2)
    except OSError:
        stats["disk_free_gb"] = None
        stats["disk_total_gb"] = None
    try:
        # The governor's tier, the Trash Folder's physical size, the biggest consumers and the last
        # sweep, for the Gateway's fleet view (cheap: sizes are from the last sweep).
        from components.hub import disk_governor
        stats["disk"] = disk_governor.snapshot(DATA_DIR)
    except Exception:
        stats["disk"] = None
    try:
        # Which interface carries the default route, what kind of link it is and — for Ethernet —
        # the media the OS says it negotiated, so a bad cable thinning the fleet to 100 Mb/s is
        # visible instead of reading as "online". Cached: this never shells out on a request.
        stats["connection"] = network_link.status()
    except Exception:
        stats["connection"] = None
    return stats


def _listening_pids(port: int) -> list[int]:
    """The processes listening on `port`, remembered for LISTENER_TTL_S while they are alive."""
    cached = _listener_cache.get(port)
    if cached is not None and time.monotonic() - cached[0] < LISTENER_TTL_S and cached[1]:
        if all(psutil.pid_exists(pid) for pid in cached[1]):
            return list(cached[1])
    pids = _lsof_listening_pids(port)
    _listener_cache[port] = (time.monotonic(), pids)
    return list(pids)


def _lsof_listening_pids(port: int) -> list[int]:
    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=3,
        )
        return [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return []


def _proc(pid: int) -> psutil.Process | None:
    cached = _proc_cache.get(pid)
    if cached is not None:
        try:
            if cached.is_running():
                return cached
        except psutil.Error:
            pass
        _proc_cache.pop(pid, None)
    try:
        p = psutil.Process(pid)
        p.cpu_percent(interval=None)  # prime the delta for the next call
        _proc_cache[pid] = p
        return p
    except psutil.Error:
        return None


def studio_process_stats(port: int) -> dict | None:
    """RSS/CPU for the process listening on `port`, including children
    (model workers are often child processes)."""
    pids = _listening_pids(port)
    if not pids:
        return None
    rss = 0
    cpu = 0.0
    counted = 0
    root_pid = pids[0]
    for pid in pids:
        p = _proc(pid)
        if p is None:
            continue
        try:
            group = [p] + p.children(recursive=True)
        except psutil.Error:
            group = [p]
        for member in group:
            try:
                rss += member.memory_info().rss
                cpu += member.cpu_percent(interval=None)
                counted += 1
            except psutil.Error:
                continue
    if counted == 0:
        return None
    return {
        "pid": root_pid,
        "rss_gb": round(rss / 1e9, 2),
        "cpu_percent": round(cpu, 1),
        "processes": counted,
    }


def proxy_stats() -> dict:
    """`_proxy_stats` remembered for PROXY_STATS_TTL_S: a Caddy that appears or degrades is still
    reported within a minute, and the process walk no longer runs on every poll."""
    global _proxy_stats_cache
    now = time.monotonic()
    if _proxy_stats_cache is not None and now - _proxy_stats_cache[0] < PROXY_STATS_TTL_S:
        return dict(_proxy_stats_cache[1])
    stats = _proxy_stats()
    _proxy_stats_cache = (now, stats)
    return dict(stats)


def _proxy_stats() -> dict:
    """Inspect Pinokio's Caddy reverse proxy without requiring root.

    Normal Caddy usage is small. A very large descriptor count or RSS is a
    strong signal of a failed configuration reload loop (for example another
    service owning HTTPS port 443), which should be visible before it starves
    generation workers of memory.
    """
    rows = []
    # Asking process_iter() to prefetch cmdline for every process makes the
    # iterator itself fail on macOS when one unrelated process denies
    # KERN_PROCARGS2. Inspect each candidate inside the guarded block instead.
    for candidate in psutil.process_iter():
        try:
            name = (candidate.name() or "").lower()
            cmd = candidate.cmdline() or []
            executable = (cmd[0].rsplit("/", 1)[-1].lower() if cmd else "")
            if name != "caddy" and executable != "caddy":
                continue
            process = _proc(candidate.pid) or candidate
            rows.append({
                "pid": candidate.pid,
                "rss": process.memory_info().rss,
                "cpu": process.cpu_percent(interval=None),
                "fds": process.num_fds() if hasattr(process, "num_fds") else None,
            })
        except (psutil.Error, OSError, SystemError):
            continue
    rss = sum(row["rss"] for row in rows)
    fds = sum(row["fds"] or 0 for row in rows)
    degraded = bool(rows) and (rss / 1e9 >= CADDY_RSS_WARN_GB or fds >= CADDY_FD_WARN)
    return {
        "status": "degraded" if degraded else ("healthy" if rows else "not_running"),
        "processes": len(rows),
        "pids": [row["pid"] for row in rows],
        "rss_gb": round(rss / 1e9, 2),
        "cpu_percent": round(sum(row["cpu"] for row in rows), 1),
        "file_descriptors": fds if rows else None,
    }


def check_proxy_health() -> dict:
    """Emit one alert per Caddy degradation/recovery edge."""
    stats = proxy_stats()
    degraded = stats["status"] == "degraded"
    if degraded and not _proxy_alert_state["degraded"]:
        from . import alerts
        alerts.emit(
            "proxy_degraded",
            "Pinokio Caddy is consuming abnormal resources; check for a port 443 conflict",
            stats,
        )
        _proxy_alert_state["degraded"] = True
    elif stats["status"] == "healthy" and _proxy_alert_state["degraded"]:
        from . import alerts
        alerts.emit("proxy_recovered", "Pinokio Caddy resource use returned to normal", stats)
        _proxy_alert_state["degraded"] = False
    return stats
