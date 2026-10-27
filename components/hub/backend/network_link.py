"""This Mac's network connection: which interface carries the default route, what kind of link it is,
and — for Ethernet — the speed the OS says the link actually negotiated.

Why the speed. A bad cable does not fail; it *negotiates*, and a Mac on 100 Mb/s reads "online" in
every other field. The owner found 1301 linked at `100baseTX` on an old cable (earlier `10baseT`) and
nothing in the fleet view could show it. An Ethernet link under 1000 Mb/s or at half duplex is a
worth reporting because the fix is a cable or a port, not the Mac.

**Nothing here is guessed.** The interface is the one the kernel says carries the default route, the
kind is the name `networksetup` gives that device's hardware port, and the speed is the media the OS
states it negotiated. A reading that cannot be made is `unmeasured` with a reason (§6: "none" and
"could not tell" must never be the same value; a speed of `0` is not a link).

**Cheap and cached.** `/api/hub/resources` is read by every controller for every node on its poll, so
this is never computed on a request: a background thread refreshes it every `TTL_S` and a caller only
ever reads the last answer. The Wi-Fi PHY rate comes from `system_profiler`, which takes seconds, so
it is read on a slower timer of its own and may simply be absent. Everything runs as this user — no
root, no network setting touched, every subprocess bounded.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time

#: How long one reading stands. A cable is not swapped every minute, and the poll that reads this
#: runs every few seconds on every Mac in the fleet.
TTL_S = 180.0

#: `system_profiler` takes seconds on a warm Mac and longer on a busy one. Its own timer, so the
#: cheap reads above are not held back by it.
WIFI_RATE_TTL_S = 600.0

#: Bounded, per command. Measured on this Mac: `route` 3 ms, `ifconfig` a few ms,
#: `networksetup -listallhardwareports` 30 ms, `system_profiler SPAirPortDataType` ~4 s.
TIMEOUT_FAST_S = 5.0
TIMEOUT_SYSTEM_PROFILER_S = 20.0

#: A negotiated media token: `1000baseT`, `100baseTX`, `10baseT`, `2500baseT`, `10GbaseT`, ...
_SPEED_RE = re.compile(r"\b(\d+)(G?)base", re.IGNORECASE)
_DUPLEX_RE = re.compile(r"<(full|half)-duplex>", re.IGNORECASE)
#: `ifconfig`: `media: autoselect (1000baseT <full-duplex>)` / `media: 1000baseT <full-duplex>`.
_MEDIA_LINE_RE = re.compile(r"^\s*media:\s*(?P<media>.+?)\s*$", re.MULTILINE)
_STATUS_LINE_RE = re.compile(r"^\s*status:\s*(?P<status>\S+)", re.MULTILINE)
_HW_PORT_RE = re.compile(r"Hardware Port:\s*(?P<port>.+?)\s*\nDevice:\s*(?P<device>\S+)")
_ROUTE_IFACE_RE = re.compile(r"^\s*interface:\s*(?P<device>\S+)", re.MULTILINE)

_state_lock = threading.Lock()
_state: dict = {"value": None, "read_at": 0.0, "reading": False}
_wifi_rate: dict = {"mbps": None, "read_at": None}
_wifi_rate_lock = threading.Lock()

#: Words `networksetup` uses for a hardware port. Only the OS's own name is read; a table of which
#: device is which (`en0` is Wi-Fi on a Mac mini and Ethernet on an iMac) would be a guess, and the
#: next USB dongle would make it wrong.
_WIFI_WORDS = ("wi-fi", "wifi", "airport")
_ETHERNET_WORDS = ("ethernet", "lan", "base-t", "100base", "1000base", "10gbase", "usb 10/100")


def clock() -> float:
    """The clock the cache is measured on; patched by tests so they need not sleep."""
    return time.monotonic()


def _run(command: list[str], timeout: float) -> str | None:
    """One bounded command's stdout, or `None` when it could not be run or timed out.

    Absolute paths first for the reason `resources.apple_chip_name` gives: a Mac's app started by
    launchd does not inherit a login shell's PATH.
    """
    for candidate in (command, [command[0].rsplit("/", 1)[-1], *command[1:]]):
        try:
            result = subprocess.run(candidate, capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return None


def parse_default_route(output: str | None) -> str | None:
    """The interface carrying the default route, as `route -n get default` states it."""
    match = _ROUTE_IFACE_RE.search(output or "")
    return match.group("device") if match else None


def parse_media(media: str | None) -> dict:
    """The negotiated speed and duplex from a media line, and whether anything was negotiated.

    Reads what the OS says rather than assuming: `autoselect` alone (a Wi-Fi interface, or an
    Ethernet port with no cable) states no speed at all, and `autoselect (none)` states there is no
    link. `negotiated` is the one fact the caller needs: without it, "no speed" must stay absent
    rather than become a `0`.
    """
    text = str(media or "").strip()
    speed = _SPEED_RE.search(text)
    duplex = _DUPLEX_RE.search(text)
    return {
        "media": text or None,
        "speed_mbps": (int(speed.group(1)) * (1000 if speed.group(2) else 1)) if speed else None,
        "duplex": duplex.group(1).lower() if duplex else None,
        "autoselect": "autoselect" in text.lower(),
        # A media word (`1000baseT`) is the OS stating a negotiated link. `autoselect (none)` and a
        # bare `autoselect` state the opposite: nothing was negotiated to report.
        "negotiated": bool(speed),
    }


def parse_ifconfig(output: str | None) -> dict:
    """The `media:` and `status:` lines of one interface's `ifconfig` answer."""
    text = output or ""
    media = _MEDIA_LINE_RE.search(text)
    status = _STATUS_LINE_RE.search(text)
    return {
        "media": media.group("media") if media else None,
        "status": status.group("status").lower() if status else None,
    }


def parse_hardware_ports(output: str | None) -> dict[str, str]:
    """`networksetup -listallhardwareports` as `{device: hardware port name}`."""
    return {match.group("device"): match.group("port").strip()
            for match in _HW_PORT_RE.finditer(output or "")}


def classify_kind(port_name: str | None, device: str | None) -> str:
    """`ethernet`, `wifi` or `other`, from the hardware port the OS names for that device.

    Never from the device's name: `en0` is Wi-Fi on one Mac and Ethernet on another, and a USB
    dongle enumerates wherever it likes. When the OS names no port for the device, `other` is the
    honest answer — it says "not one I can classify", never "wireless".
    """
    word = str(port_name or "").strip().lower()
    if any(token in word for token in _WIFI_WORDS):
        return "wifi"
    if any(token in word for token in _ETHERNET_WORDS):
        return "ethernet"
    return "other"


def parse_wifi_rate(output: str | None, device: str | None) -> int | None:
    """The Wi-Fi PHY rate `system_profiler` states for `device`, in Mb/s, or `None`.

    `spairport_network_rate` is the transmit rate the interface is currently using. Absent when the
    interface is not associated, and absent from macOS builds that stop publishing it — either way
    the caller shows nothing rather than a zero.
    """
    if not output:
        return None
    import json

    try:
        payload = json.loads(output)
    except ValueError:
        return None
    rows = payload.get("SPAirPortDataType") if isinstance(payload, dict) else None
    block = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    for interface in block.get("spairport_airport_interfaces") or []:
        if not isinstance(interface, dict):
            continue
        if device and str(interface.get("_name") or "") != device:
            continue
        current = interface.get("spairport_current_network_information")
        current = current if isinstance(current, dict) else {}
        rate = current.get("spairport_network_rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
            return None
        return int(rate)
    return None


def unmeasured(reason: str) -> dict:
    """The one shape a caller can always read, whatever went wrong (§6).

    Every key `collect` can return is present and `None`, so a reader never has to branch on
    whether the answer was a measurement before it can read a field.
    """
    return {"status": "unmeasured", "reason": reason, "interface": None, "kind": None,
            "speed_mbps": None, "duplex": None, "media": None, "autoselect": None,
            "link_up": None, "wifi": None, "sampled_at": None}


def _wifi_rate_cached(device: str | None) -> int | None:
    """The PHY rate, re-read at most every `WIFI_RATE_TTL_S`. `None` is "not stated", never zero."""
    if not device:
        return None
    now = clock()
    with _wifi_rate_lock:
        read_at = _wifi_rate["read_at"]
        if read_at is not None and now - read_at < WIFI_RATE_TTL_S:
            return _wifi_rate["mbps"]
    rate = parse_wifi_rate(
        _run(["/usr/sbin/system_profiler", "SPAirPortDataType", "-json"], TIMEOUT_SYSTEM_PROFILER_S),
        device)
    with _wifi_rate_lock:
        _wifi_rate["mbps"], _wifi_rate["read_at"] = rate, now
    return rate


def collect() -> dict:
    """One real reading. Called from the refresh thread, never from a request."""
    route = _run(["/sbin/route", "-n", "get", "default"], TIMEOUT_FAST_S)
    if route is None:
        return unmeasured("This Mac's routing table could not be read.")
    device = parse_default_route(route)
    if not device:
        return unmeasured("This Mac has no default route, so it has no primary network connection.")

    ports = parse_hardware_ports(
        _run(["/usr/sbin/networksetup", "-listallhardwareports"], TIMEOUT_FAST_S))
    kind = classify_kind(ports.get(device), device)
    link = parse_ifconfig(_run(["/sbin/ifconfig", device], TIMEOUT_FAST_S))
    media = parse_media(link["media"])

    wifi_device = next((name for name, port in ports.items()
                        if classify_kind(port, name) == "wifi"), None)
    wifi: dict | None = None
    if wifi_device:
        wifi_link = (link if wifi_device == device
                     else parse_ifconfig(_run(["/sbin/ifconfig", wifi_device], TIMEOUT_FAST_S)))
        wifi_up = wifi_link["status"] == "active"
        wifi = {"interface": wifi_device, "up": wifi_up,
                "rate_mbps": _wifi_rate_cached(wifi_device) if wifi_up else None}

    answer = {
        "status": "measured",
        "reason": None,
        "interface": device,
        "kind": kind,
        "speed_mbps": media["speed_mbps"],
        "duplex": media["duplex"],
        "media": media["media"],
        "autoselect": media["autoselect"],
        # The OS states an `active` status on this interface, or it does not.
        "link_up": link["status"] == "active" if link["status"] else None,
        "wifi": wifi,
        "sampled_at": time.time(),
    }
    if kind == "ethernet" and not media["negotiated"]:
        # A cable is out, or a port is forcing a media the far end will not agree: either way the
        # speed is not known, and a `0` here would read as a measurement.
        answer["reason"] = ("The Ethernet link has no negotiated media"
                            + (" (the cable may be unplugged)" if link["status"] == "inactive" else "")
                            + ", so its speed is not known.")
    return answer


def _refresh() -> None:
    """Re-read once, then stand down. A failure is cached too, so a broken read cannot spin."""
    try:
        value = collect()
    except Exception as exc:  # noqa: BLE001 - a network reading never takes the Hub down with it
        value = unmeasured(f"This Mac's network connection could not be read ({type(exc).__name__}).")
    with _state_lock:
        _state["value"], _state["read_at"], _state["reading"] = value, clock(), False


def status() -> dict:
    """The last reading, refreshed in the background when it is older than `TTL_S`.

    Never blocks: `/api/hub/resources` is read by every controller for every node, so a request must
    not pay for a subprocess. The first read after start-up answers `unmeasured` with that reason and
    the next poll answers with the real link.
    """
    now = clock()
    with _state_lock:
        value = _state["value"]
        stale = value is None or now - _state["read_at"] >= TTL_S
        start = stale and not _state["reading"]
        if start:
            _state["reading"] = True
    if start:
        threading.Thread(target=_refresh, name="network-link", daemon=True).start()
    if value is None:
        return unmeasured("This Mac's network connection has not been read yet.")
    return dict(value)


def forget() -> None:
    """Drop every cached reading. For tests, and for a caller that has just seen the network change."""
    with _state_lock:
        _state["value"], _state["read_at"], _state["reading"] = None, 0.0, False
    with _wifi_rate_lock:
        _wifi_rate["mbps"], _wifi_rate["read_at"] = None, None
