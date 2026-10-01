"""The one answer to "how much memory can a new job use right now without swapping?".

psutil's ``available`` on macOS is free + inactive pages. It leaves out the file cache that is
*active* (macOS gives it up on demand, never to swap) and purgeable pages, so a Mac that macOS
itself reports as healthy (pressure normal, swap 0) looked about 1-2 GB shorter than it was, and a
job that needed 3.2 GB on an 8 GB Mac waited for ever behind a "3.0 GB free" that was not true.

macOS can hand over, without compressing or swapping anything:
    free pages + file-backed pages (the file cache, ``vm.page_pageable_external_count``)
    + purgeable pages (``vm.page_purgeable_count``)
We take the larger of that and psutil's figure (inactive anonymous pages are compressed, not
swapped, so psutil's number is still a valid floor), keep the kernel's own free-page target
(``kern.vm_page_free_target``) back as the margin, and never claim more than
total - wired - compressor. Every caller (Hub gate, Voice preflight, telemetry, the runners) reads
this, so they all agree.

``memory_pressure``'s "free percentage" (``kern.memorystatus_level``) is NOT this number: it is
(active + inactive + free + speculative) / total, so memory an app is actively using still counts as
"free" and it only falls as wired or compressed memory grows. Measured on a 32 GB Mac: allocating
12 GB left it at 87% while free pages fell to near zero. Use it to see pressure, not headroom.
"""
from __future__ import annotations

import ctypes
import sys


def _sysctl(name: str) -> int | None:
    value, size = ctypes.c_uint64(0), ctypes.c_size_t(8)   # kernel writes 4 or 8 bytes
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            return None
    except (OSError, AttributeError):
        return None
    return int(value.value)


def macos_available(psutil_available: int, total: int, wired: int, *, page: int, free: int,
                    file_backed: int, purgeable: int, compressor_bytes: int,
                    margin: int) -> int:
    """Pure arithmetic over counters (pages, except ``compressor_bytes``); see the module docstring."""
    reclaimable = (free + file_backed + purgeable - margin) * page
    ceiling = total - wired - compressor_bytes
    return max(0, min(max(psutil_available, reclaimable), ceiling))


def available_bytes(vm) -> int:
    """Bytes a new job can use now. ``vm`` is ``psutil.virtual_memory()``."""
    if sys.platform != "darwin":
        return int(vm.available)
    names = ("vm.page_free_count", "vm.page_pageable_external_count", "vm.page_purgeable_count",
             "vm.compressor_bytes_used", "kern.vm_page_free_target", "vm.pagesize")
    c = [_sysctl(n) for n in names]
    if None in c:   # an older or unusual kernel: fall back to psutil rather than guess
        return int(vm.available)
    free, ext, purg, comp, margin, page = c
    return macos_available(int(vm.available), int(vm.total), int(vm.wired), page=page, free=free,
                           file_backed=ext, purgeable=purg, compressor_bytes=comp, margin=margin)
