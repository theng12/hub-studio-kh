"""`components/host_memory.py`: what macOS can give a new job without swapping.

The numbers below are real, recorded on the 32 GB M2 Max Mac Studio the Hub is built on (2026-10-01:
`vm_stat`, `sysctl vm.*`, `memory_pressure -Q` and psutil sampled in one pass), plus synthetic 8 GB
Macs shaped like node 1101, whose run log carries no vm_stat: it shows only "pressure normal, 79%
free, swap 0" and a Hub that held a job for "needs ~3.2GB, ~3.0GB free". The synthetic rows are
labelled as such. 1 page = 16384 bytes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components import host_memory
from components.hub.backend import broker, resources

PAGE = 16384
GIB = 1024 ** 3

# Recorded: 32 GB Mac Studio, pressure normal. psutil.available = free + speculative + inactive.
MAC_32GB = dict(psutil_available=19_159_597_056, total=34_359_738_368, wired=2_753_740_800,
                page=PAGE, free=133_732, file_backed=1_149_866, purgeable=5_127,
                compressor_bytes=1_229_651_968, margin=4_000)

# Synthetic 1101: 8 GB, macOS says 79% free (so wired + compressor ~ 21% = 1.8 GB). psutil sees
# 3.0 GB (the figure the Hub quoted); ~3.1 GB of the file cache is reclaimable on top of free pages.
MAC_8GB_NORMAL = dict(psutil_available=3_000_000_000, total=8_589_934_592, wired=1_070_000_000,
                      page=PAGE, free=25_000, file_backed=190_000, purgeable=10_000,
                      compressor_bytes=700_000_000, margin=4_000)

# Synthetic 8 GB Mac that is genuinely tight: a little free, little cache, a model already loaded.
MAC_8GB_TIGHT = dict(MAC_8GB_NORMAL, psutil_available=1_200_000_000, free=8_000, file_backed=60_000,
                     purgeable=2_000)

# Synthetic 8 GB Mac whose memory is mostly wired + compressed: the ceiling must win over a big cache figure.
MAC_8GB_WIRED = dict(MAC_8GB_NORMAL, wired=6_500_000_000, compressor_bytes=1_500_000_000,
                     psutil_available=2_000_000_000)

QWEN_TTS_0_6B_8BIT = {"min_total": 8.0, "min_free": 3.2}   # catalog numbers; not changed here


def gate(figures: dict) -> tuple[str, str | None]:
    gb = host_memory.macos_available(**figures) / 1e9
    return broker._memory_gate(QWEN_TTS_0_6B_8BIT, {"total_gb": figures["total"] / 1e9, "available_gb": gb})


class MacOSAvailableTests(unittest.TestCase):
    def test_recorded_32gb_mac_counts_free_plus_file_cache_plus_purgeable(self):
        # (133732 + 1149866 + 5127 - 4000 margin) pages: 21.05 GB, against psutil's 19.16 GB.
        self.assertEqual(host_memory.macos_available(**MAC_32GB), 1_284_725 * PAGE)
        self.assertGreater(host_memory.macos_available(**MAC_32GB), MAC_32GB["psutil_available"])

    def test_1101_shaped_mac_is_admitted_at_the_unchanged_3_2_gb_requirement(self):
        # Old measure: 3.0 GB -> "waiting for memory". New: 3.6 GB -> runs.
        self.assertEqual(broker._memory_gate(
            QWEN_TTS_0_6B_8BIT, {"total_gb": 8.59, "available_gb": 3.0})[0], "wait")
        self.assertGreaterEqual(host_memory.macos_available(**MAC_8GB_NORMAL), 3_600_000_000)
        self.assertEqual(gate(MAC_8GB_NORMAL), ("run", None))

    def test_a_genuinely_tight_mac_is_still_refused(self):
        self.assertLess(host_memory.macos_available(**MAC_8GB_TIGHT) / 1e9, 3.2)
        self.assertEqual(gate(MAC_8GB_TIGHT)[0], "wait")

    def test_never_claims_more_than_total_minus_wired_minus_compressor(self):
        figures = MAC_8GB_WIRED
        ceiling = figures["total"] - figures["wired"] - figures["compressor_bytes"]
        reclaimable = (figures["free"] + figures["file_backed"] + figures["purgeable"]) * PAGE
        self.assertGreater(reclaimable, ceiling)   # the cache figure alone would over-claim
        self.assertEqual(host_memory.macos_available(**figures), ceiling)
        self.assertEqual(gate(figures)[0], "wait")

    def test_never_falls_below_psutil_when_the_cache_is_small(self):
        # Inactive anonymous pages are compressed, not swapped: psutil's figure stays a valid floor.
        self.assertEqual(host_memory.macos_available(**MAC_8GB_TIGHT), 1_200_000_000)


class WiringTests(unittest.TestCase):
    def test_hub_host_stats_reads_the_helper(self):
        with mock.patch.object(host_memory, "available_bytes", return_value=3_650_000_000):
            self.assertEqual(resources.host_stats()["available_gb"], 3.65)

    @unittest.skipUnless(sys.platform == "darwin", "reads the macOS kernel counters")
    def test_live_kernel_counters_give_a_sane_figure(self):
        import psutil
        vm = psutil.virtual_memory()
        got = host_memory.available_bytes(vm)
        self.assertGreater(got, 0)
        self.assertLessEqual(got, vm.total - vm.wired)

    def test_falls_back_to_psutil_when_a_counter_is_missing(self):
        vm = SimpleNamespace(available=1_234_567_890, total=8 * GIB, wired=0)
        with mock.patch.object(host_memory, "_sysctl", return_value=None), \
                mock.patch.object(host_memory.sys, "platform", "darwin"):
            self.assertEqual(host_memory.available_bytes(vm), 1_234_567_890)

    def test_other_platforms_use_psutil(self):
        vm = SimpleNamespace(available=42, total=8 * GIB, wired=0)
        with mock.patch.object(host_memory.sys, "platform", "linux"):
            self.assertEqual(host_memory.available_bytes(vm), 42)


if __name__ == "__main__":
    unittest.main()
