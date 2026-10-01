"""How much a run WRITES to this Mac's internal SSD (SSD wear from swap) - shared by the 8 GB runners.

No sudo. Only the INTERNAL boot disk counts: the ioreg block device on Apple Fabric (the NVMe behind ANS) with
location Internal. The kit SSDs the runners live on are USB/Thunderbolt (location External) and the built-in SD reader
is Internal but Secure Digital, so both are left out. Sizes are decimal GB (1e9). Standard library only.

API (all pure except snapshot / baseline_gb_per_min, which read the Mac):
    COLUMNS                              the four per-clip CSV columns, in order
    snapshot()                           {"t", "ssd" (bytes written, or None), "swapouts" (bytes, or None)} now
    baseline_gb_per_min(seconds)         idle internal-SSD write rate over a `seconds` sample, or None
    clip_columns(before, after, base)    the four columns for one clip from two snapshots (blanks where unknown)
    wear_lines(rows, ...)                two plain-language summary lines: GB per unit of output, TB/year at N h/day
    internal_ssd_written(plist_bytes), swapouts_bytes(vm_stat_text, pagesize_text)   the parsers
"""
import plistlib
import re
import subprocess
import time

COLUMNS = ["ssd_written_gb", "swapouts_gb", "ssd_written_baseline_gb_per_min", "ssd_written_net_gb"]
IOREG = ["ioreg", "-a", "-c", "IOBlockStorageDevice", "-r", "-l", "-d", "2"]   # devices + their driver (Statistics), plist


def internal_ssd_written(blob):
    """Bytes ever written to the internal boot disk, from the plist `ioreg -a -c IOBlockStorageDevice -r -l -d 2` prints
    ("Bytes (Write)" of the IOBlockStorageDriver under each Apple Fabric / Internal device, summed). None if unreadable or absent."""
    try:
        nodes = plistlib.loads(blob)
    except Exception:
        return None
    total = None
    for dev in nodes if isinstance(nodes, list) else []:
        pc = dev.get("Protocol Characteristics") or {}
        if pc.get("Physical Interconnect") != "Apple Fabric" or pc.get("Physical Interconnect Location") != "Internal":
            continue
        for drv in dev.get("IORegistryEntryChildren") or []:
            b = (drv.get("Statistics") or {}).get("Bytes (Write)")
            if isinstance(b, int):
                total = (total or 0) + b
    return total


def swapouts_bytes(vm_stat_text, pagesize_text=""):
    """The `Swapouts:` page count from `vm_stat` times the page size (`pagesize`; the vm_stat header, then 16384, as fallbacks). None if absent."""
    m = re.search(r"^Swapouts:\s+(\d+)\.", vm_stat_text, re.M)
    if not m:
        return None
    h = re.search(r"page size of (\d+) bytes", vm_stat_text)
    ps = int(pagesize_text.strip()) if pagesize_text.strip().isdigit() else int(h[1]) if h else 16384
    return int(m[1]) * ps


def _out(cmd, binary=False):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        return r.stdout if binary else r.stdout.decode("utf-8", "replace")
    except Exception:
        return b"" if binary else ""


def snapshot():
    return {"t": time.time(), "ssd": internal_ssd_written(_out(IOREG, True)),
            "swapouts": swapouts_bytes(_out(["vm_stat"]), _out(["pagesize"]))}


def baseline_gb_per_min(seconds, sleep=time.sleep):
    """GB per minute the Mac writes to its internal SSD while idle (two readings `seconds` apart). None if not measurable."""
    a = snapshot(); sleep(seconds); b = snapshot()
    if a["ssd"] is None or b["ssd"] is None or b["ssd"] < a["ssd"] or b["t"] <= a["t"]:
        return None
    return (b["ssd"] - a["ssd"]) / 1e9 / ((b["t"] - a["t"]) / 60)


def clip_columns(before, after, base_gb_per_min):
    """gross internal-SSD writes, swapouts, the idle baseline, and net (gross less baseline x the window, never below 0).
    A counter that went backwards (reboot) or is missing gives blanks."""
    out = {c: "" for c in COLUMNS}
    if base_gb_per_min is not None:
        out["ssd_written_baseline_gb_per_min"] = round(base_gb_per_min, 4)
    if before["swapouts"] is not None and after["swapouts"] is not None and after["swapouts"] >= before["swapouts"]:
        out["swapouts_gb"] = round((after["swapouts"] - before["swapouts"]) / 1e9, 3)
    if before["ssd"] is not None and after["ssd"] is not None and after["ssd"] >= before["ssd"]:
        gross = (after["ssd"] - before["ssd"]) / 1e9
        out["ssd_written_gb"] = round(gross, 3)
        if base_gb_per_min is not None:
            out["ssd_written_net_gb"] = round(max(gross - base_gb_per_min / 60 * (after["t"] - before["t"]), 0), 3)
    return out


def _num(x):
    try:
        return None if x in ("", None) else float(x)
    except (TypeError, ValueError):
        return None


def tb_per_year(net_gb, generation_seconds, hours_per_day=8.0):
    return net_gb / (generation_seconds / 3600) * hours_per_day * 365 / 1000


def wear_lines(rows, unit="minute of audio", units_key="audio_seconds", units_per=60.0, hours_per_day=8.0):
    """Two plain-language lines from COMPLETED clips' rows (dicts with the ssd columns, generation_seconds and `units_key`):
    'about X GB written to this Mac's SSD per <unit>' and 'at 8 hours of generation a day, ~Y TB/year'. [] if nothing is measured.
    `units_key` / `units_per` turn a row into units (audio_seconds / 60 = minutes of audio; use a count column and 1.0 for 'image')."""
    rows = [r for r in rows if _num(r.get("ssd_written_net_gb")) is not None and _num(r.get(units_key)) and _num(r.get("generation_seconds"))]
    if not rows:
        return []
    net = sum(_num(r["ssd_written_net_gb"]) for r in rows)
    units = sum(_num(r[units_key]) for r in rows) / units_per
    gen_s = sum(_num(r["generation_seconds"]) for r in rows)
    swaps = sum(_num(r.get("swapouts_gb")) or 0 for r in rows)
    return [f"About {net / units:.2f} GB written to this Mac's SSD per {unit} (net of background writes; {net:.2f} GB over "
            f"{len(rows)} clip(s); of that window's writes, swapouts {swaps:.2f} GB).",
            f"At {hours_per_day:g} hours of generation a day, ≈ {tb_per_year(net, gen_s, hours_per_day):.1f} TB/year "
            f"({net / (gen_s / 3600):.1f} GB per hour of generation x {hours_per_day:g} h x 365 days). A rough estimate from this run only."]
