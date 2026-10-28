"""Every Mac reports its own link: which interface, what kind, and the media its Ethernet negotiated.

The owner, 2026-10-28: Mac 1301 was linked at `100baseTX` on an old cable — `10baseT` before that —
and nothing in the fleet view could show it, because a thinned Ethernet link still answers every
other field. So `resources.host_stats()` carries a `connection` block read from this Mac alone.

What this file pins:

* **The parse.** `1000baseT <full-duplex>`, `100baseTX`, `10baseT/UTP <half-duplex>` all read as
  their own speed and duplex; `autoselect` with no active media reads as *not negotiated* rather
  than as zero (AGENTS §6: "none" and "could not tell" are never the same value).
* **The kind comes from the OS's hardware port**, never the device name — `en0` is Wi-Fi on one Mac
  and Ethernet on another.
* **One reading stands for a few minutes**, and a request never shells out.
* **A command that times out is `unmeasured` with a reason**, never a zero.
* **A Hub that predates the block** publishes no `connection` key, so a reader must treat absent
  as absent — the Gateway test covers that half.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import network_link  # noqa: E402

ROUTE_WIFI = """   route to: default
destination: default
       mask: default
    gateway: 192.168.0.1
  interface: en1
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
"""

ROUTE_ETHERNET = ROUTE_WIFI.replace("en1", "en0")

PORTS_MAC_MINI = """Hardware Port: Ethernet
Device: en0
Ethernet Address: a4:fc:14:26:3a:18

Hardware Port: Wi-Fi
Device: en1
Ethernet Address: a4:fc:14:1f:cc:4e

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: 36:d9:ee:ae:1c:80
"""

PORTS_MACBOOK = """Hardware Port: Wi-Fi
Device: en0
Ethernet Address: a4:83:e7:00:00:01

Hardware Port: USB 10/100/1000 LAN
Device: en5
Ethernet Address: 00:11:22:33:44:55
"""

IFCONFIG_1000_FULL = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\toptions=567<RXCSUM,TXCSUM>
\tether a4:fc:14:26:3a:18
\tinet 192.168.0.20 netmask 0xffffff00 broadcast 192.168.0.255
\tmedia: autoselect (1000baseT <full-duplex>)
\tstatus: active
"""

IFCONFIG_100_HALF = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tmedia: autoselect (100baseTX <half-duplex>)
\tstatus: active
"""

IFCONFIG_10_HALF = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tmedia: 10baseT/UTP <half-duplex>
\tstatus: active
"""

IFCONFIG_NO_CABLE = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tmedia: autoselect (none)
\tstatus: inactive
"""

IFCONFIG_WIFI = """en1: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tmedia: autoselect
\tstatus: active
"""

WIFI_PROFILER = json.dumps({"SPAirPortDataType": [{"spairport_airport_interfaces": [
    {"_name": "en1", "spairport_status_information": "spairport_status_connected",
     "spairport_current_network_information": {"spairport_network_rate": 432,
                                               "spairport_network_phymode": "802.11ax"}},
    {"_name": "awdl0"},
]}]})


def _answers(route: str | None, ports: str | None, ifconfig: dict[str, str | None],
             profiler: str | None = None) -> object:
    """A `_run` stand-in that answers each command like the Mac it is describing."""

    def run(command: list[str], timeout: float) -> str | None:
        joined = " ".join(command)
        if "route" in joined:
            return route
        if "listallhardwareports" in joined:
            return ports
        if "ifconfig" in joined:
            return ifconfig.get(command[-1])
        if "SPAirPortDataType" in joined:
            return profiler
        return None

    return run


class MediaParsingTests(unittest.TestCase):
    """The media line, which is the whole point: what the OS says the link negotiated."""

    def test_a_gigabit_full_duplex_link_reads_as_1000(self):
        media = network_link.parse_media("autoselect (1000baseT <full-duplex>)")
        self.assertEqual(media["speed_mbps"], 1000)
        self.assertEqual(media["duplex"], "full")
        self.assertTrue(media["negotiated"])
        self.assertTrue(media["autoselect"])

    def test_a_100_megabit_link_reads_as_100(self):
        media = network_link.parse_media("autoselect (100baseTX <half-duplex>)")
        self.assertEqual(media["speed_mbps"], 100)
        self.assertEqual(media["duplex"], "half")

    def test_a_10_megabit_half_duplex_link_reads_as_10(self):
        media = network_link.parse_media("10baseT/UTP <half-duplex>")
        self.assertEqual(media["speed_mbps"], 10)
        self.assertEqual(media["duplex"], "half")
        self.assertFalse(media["autoselect"])

    def test_2500_and_10g_read_as_their_own_speeds(self):
        self.assertEqual(network_link.parse_media("2500baseT <full-duplex>")["speed_mbps"], 2500)
        self.assertEqual(network_link.parse_media("10GbaseT <full-duplex>")["speed_mbps"], 10000)

    def test_autoselect_with_no_active_media_states_no_speed(self):
        """The one that matters: no cable is not a link of 0 Mb/s."""
        for text in ("autoselect", "autoselect (none)", "autoselect (autoselect)", ""):
            media = network_link.parse_media(text)
            self.assertIsNone(media["speed_mbps"], text)
            self.assertFalse(media["negotiated"], text)


class InterfaceClassificationTests(unittest.TestCase):
    def test_the_port_name_decides_the_kind_not_the_device_name(self):
        # The same `en0` is Wi-Fi on the MacBook fixture and Ethernet on the Mac mini's.
        self.assertEqual(network_link.classify_kind("Wi-Fi", "en0"), "wifi")
        self.assertEqual(network_link.classify_kind("Ethernet", "en0"), "ethernet")
        self.assertEqual(network_link.classify_kind("USB 10/100/1000 LAN", "en5"), "ethernet")

    def test_a_port_the_os_does_not_name_is_other(self):
        """`other` says "not one I can classify"; it must never read as wireless."""
        self.assertEqual(network_link.classify_kind(None, "en9"), "other")
        self.assertEqual(network_link.classify_kind("Thunderbolt Bridge", "bridge0"), "other")


class ReadTests(unittest.TestCase):
    def setUp(self):
        network_link.forget()
        self.addCleanup(network_link.forget)

    def test_a_gigabit_ethernet_primary_is_measured(self):
        with mock.patch.object(network_link, "_run", _answers(
                ROUTE_ETHERNET, PORTS_MAC_MINI, {"en0": IFCONFIG_1000_FULL, "en1": None})):
            link = network_link.collect()
        self.assertEqual(link["status"], "measured")
        self.assertEqual((link["interface"], link["kind"]), ("en0", "ethernet"))
        self.assertEqual(link["speed_mbps"], 1000)
        self.assertEqual(link["duplex"], "full")
        self.assertIsNone(link["reason"])
        # The mini has a Wi-Fi port that is not up: reported as present, not as a connection.
        self.assertEqual(link["wifi"]["interface"], "en1")
        self.assertFalse(link["wifi"]["up"])
        self.assertIsNone(link["wifi"]["rate_mbps"])

    def test_a_100_megabit_ethernet_primary_is_measured_as_100(self):
        with mock.patch.object(network_link, "_run", _answers(
                ROUTE_ETHERNET, PORTS_MAC_MINI, {"en0": IFCONFIG_100_HALF})):
            link = network_link.collect()
        self.assertEqual(link["speed_mbps"], 100)
        self.assertEqual(link["duplex"], "half")

    def test_a_10_megabit_link_is_measured_as_10(self):
        with mock.patch.object(network_link, "_run", _answers(
                ROUTE_ETHERNET, PORTS_MAC_MINI, {"en0": IFCONFIG_10_HALF})):
            self.assertEqual(network_link.collect()["speed_mbps"], 10)

    def test_an_ethernet_port_with_no_cable_reports_no_speed_and_says_why(self):
        with mock.patch.object(network_link, "_run", _answers(
                ROUTE_ETHERNET, PORTS_MAC_MINI,
                {"en0": IFCONFIG_NO_CABLE, "en1": IFCONFIG_WIFI})):
            link = network_link.collect()
        self.assertIsNone(link["speed_mbps"])
        self.assertFalse(link["link_up"])
        self.assertIn("no negotiated media", link["reason"])
        self.assertIn("cable may be unplugged", link["reason"])

    def test_a_wifi_primary_is_named_as_wifi_with_its_phy_rate(self):
        with mock.patch.object(network_link, "_run", _answers(
                ROUTE_WIFI, PORTS_MAC_MINI, {"en1": IFCONFIG_WIFI}, WIFI_PROFILER)):
            link = network_link.collect()
        self.assertEqual(link["kind"], "wifi")
        self.assertEqual(link["interface"], "en1")
        # No Ethernet media is claimed for a Wi-Fi interface.
        self.assertIsNone(link["speed_mbps"])
        self.assertIsNone(link["reason"])
        self.assertTrue(link["wifi"]["up"])
        self.assertEqual(link["wifi"]["rate_mbps"], 432)

    def test_a_wifi_rate_the_os_does_not_state_is_absent_not_zero(self):
        with mock.patch.object(network_link, "_run", _answers(
                ROUTE_WIFI, PORTS_MAC_MINI, {"en1": IFCONFIG_WIFI},
                json.dumps({"SPAirPortDataType": [{"spairport_airport_interfaces": [
                    {"_name": "en1", "spairport_current_network_information": {}}]}]}))):
            link = network_link.collect()
        self.assertIsNone(link["wifi"]["rate_mbps"])

    def test_no_default_route_is_unmeasured_with_a_reason(self):
        """A Mac with no default route has no primary connection — not a link of unknown speed."""
        with mock.patch.object(network_link, "_run", _answers(
                "   route to: default\ndestination: default\n", PORTS_MAC_MINI, {})):
            link = network_link.collect()
        self.assertEqual(link["status"], "unmeasured")
        self.assertIn("no default route", link["reason"])
        self.assertIsNone(link["speed_mbps"])

    def test_a_routing_table_that_cannot_be_read_is_unmeasured_with_a_reason(self):
        with mock.patch.object(network_link, "_run", _answers(None, PORTS_MAC_MINI, {})):
            link = network_link.collect()
        self.assertEqual(link["status"], "unmeasured")
        self.assertIn("routing table could not be read", link["reason"])


class TimeoutTests(unittest.TestCase):
    def test_a_command_that_times_out_is_unmeasured_never_a_zero(self):
        """§6: "I could not work out the answer" must not arrive as a number."""
        def boom(command, timeout, **kwargs):
            raise subprocess.TimeoutExpired(command, timeout)

        network_link.forget()
        self.addCleanup(network_link.forget)
        with mock.patch.object(network_link.subprocess, "run", boom):
            link = network_link.collect()
        self.assertEqual(link["status"], "unmeasured")
        self.assertIsNone(link["speed_mbps"])
        self.assertTrue(link["reason"])
        self.assertEqual(link["wifi"], None)

    def test_every_command_is_bounded(self):
        """No subprocess may run without a timeout: this is called from a daemon thread."""
        seen: list[float] = []

        class Result:
            returncode = 1
            stdout = ""

        def record(command, timeout, **kwargs):
            seen.append(timeout)
            return Result()

        with mock.patch.object(network_link.subprocess, "run", record):
            network_link._run(["/sbin/route", "-n", "get", "default"], 2.0)
        self.assertTrue(seen)
        self.assertTrue(all(timeout > 0 for timeout in seen))

    def test_the_bounds_are_seconds_not_minutes(self):
        self.assertLessEqual(network_link.TIMEOUT_FAST_S, 10.0)
        self.assertLessEqual(network_link.TIMEOUT_SYSTEM_PROFILER_S, 30.0)
        self.assertIn(network_link.TTL_S, (60.0, 120.0, 180.0, 300.0))


class CachingTests(unittest.TestCase):
    def setUp(self):
        network_link.forget()
        self.addCleanup(network_link.forget)

    def test_one_reading_stands_for_the_ttl_and_is_re_read_after_it(self):
        clock = [1000.0]
        reads: list[int] = []

        def read():
            reads.append(1)
            return {**network_link.unmeasured("x"), "status": "measured", "speed_mbps": 1000}

        with mock.patch.object(network_link, "clock", lambda: clock[0]), \
                mock.patch.object(network_link, "_refresh", read):
            network_link.status()          # starts a thread for the first reading
            for _ in range(20):
                clock[0] += network_link.TTL_S / 5
                network_link.status()
        self.assertEqual(len(reads), 1, "the link was re-read inside its TTL")

    def test_a_request_never_waits_for_the_reading(self):
        """`/api/hub/resources` is on every controller's poll: it must never block on a subprocess."""
        started = []

        def slow():
            started.append(1)
            # Deliberately never finishes: the caller must not be waiting on it.

        with mock.patch.object(network_link, "_refresh", slow):
            with mock.patch.object(network_link.threading, "Thread") as thread:
                answer = network_link.status()
        self.assertTrue(thread.called, "no refresh was started")
        self.assertEqual(answer["status"], "unmeasured")
        self.assertTrue(answer["reason"])

    def test_a_failing_read_does_not_spin_the_next_request(self):
        """A cached failure is still a reading: a broken network must not spawn a thread per poll."""
        def failing():
            raise OSError("boom")

        with mock.patch.object(network_link, "collect", failing):
            network_link._refresh()
        cached = network_link.status()
        self.assertEqual(cached["status"], "unmeasured")
        self.assertIn("OSError", cached["reason"])
        with mock.patch.object(network_link.threading, "Thread") as thread:
            network_link.status()
            network_link.status()
        self.assertFalse(thread.called, "a cached failure started another read")

    def test_a_read_that_dies_leaves_the_refresh_flag_clear(self):
        """A flag stuck set would answer `unmeasured` for ever with no reason to show."""
        class Killed(BaseException):
            pass

        def die():
            raise Killed()

        with mock.patch.object(network_link, "collect", die):
            with self.assertRaises(Killed):
                network_link._refresh()
        with mock.patch.object(network_link.threading, "Thread") as thread:
            network_link.status()
        self.assertTrue(thread.called, "the refresh flag was left set")

    def test_the_wifi_rate_is_read_on_its_own_slower_timer(self):
        clock = [500.0]
        calls: list[int] = []

        def run(command, timeout):
            calls.append(1)
            return WIFI_PROFILER

        with mock.patch.object(network_link, "clock", lambda: clock[0]), \
                mock.patch.object(network_link, "_run", run):
            network_link._wifi_rate_cached("en1")
            network_link._wifi_rate_cached("en1")
            self.assertEqual(len(calls), 1)
            clock[0] += network_link.WIFI_RATE_TTL_S + 1
            network_link._wifi_rate_cached("en1")
        self.assertEqual(len(calls), 2)
        self.assertGreater(network_link.WIFI_RATE_TTL_S, network_link.TTL_S)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        network_link.forget()
        self.addCleanup(network_link.forget)

    def test_every_unmeasured_answer_carries_the_same_keys(self):
        """A reader must not have to branch on whether the answer was a measurement."""
        measured = network_link.collect()
        self.assertEqual(set(network_link.unmeasured("x")), set(measured))

    def test_host_stats_publishes_the_block(self):
        from components.hub.backend import resources
        network_link.forget()
        with mock.patch.object(network_link.threading, "Thread"):
            with mock.patch.object(resources.host_memory, "available_bytes",
                                   lambda vm: vm.total // 2):
                stats = resources.host_stats()
        self.assertIn("connection", stats)
        # Nothing has been read yet in this test, so it says so rather than claiming a link.
        self.assertEqual(stats["connection"]["status"], "unmeasured")

    def test_resources_route_carries_the_block_into_the_host_snapshot(self):
        """The route a controller reads is what must carry it; a block only `host_stats` can see
        would never reach the Gateway."""
        source = Path(__file__).resolve().parents[1] / "components/hub/standalone.py"
        text = source.read_text()
        self.assertIn('"connection": host.get("connection")', text)
        self.assertIn('"network": host.get("network")', text)

    def test_the_timing_of_the_snapshot_is_not_a_network_read(self):
        """The route itself must never shell out: the block is a cache read by construction."""
        from components.hub.backend import resources
        with mock.patch.object(network_link, "status",
                               return_value=network_link.unmeasured("stubbed")):
            with mock.patch.object(resources, "apple_chip_name", lambda: "Apple M4"):
                stats = resources.host_stats()
        self.assertEqual(stats["connection"]["reason"], "stubbed")


if __name__ == "__main__":
    unittest.main()
