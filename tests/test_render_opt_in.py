#!/usr/bin/env python3
"""Owner opt-in for remote final rendering: an honest ``accepted_unqualified`` authority.

Default off and byte-identical to 0.6.195; on only through the controller's settings relay;
never a fabricated measured receipt; the memory, tool-identity and tier checks unchanged.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import httpx  # noqa: E402

import test_render_qualification_receipt as base  # noqa: E402
from components.hub.backend import auth, capabilities, fleet_nodes, render_encode  # noqa: E402
from components.hub.backend.render_executor import (  # noqa: E402
    InstalledRenderAuthorityLoader,
    OPT_IN_PROVENANCE,
    RenderExecutor,
)

HARDWARE_CLASS = base.HARDWARE_CLASS

# The row an unmodified 0.6.195 Hub publishes. Pinned here as a literal, not imported, so that
# widening the production constant can never make "off is identical" pass by accident.
OLD_ROW_KEYS = frozenset((
    "operation", "physical_machine_id", "hardware_class", "profile_digest", "tools_digest",
    "compiler_digest", "qualification_digest", "selected_tier", "exact_encoder", "font_support",
    "bounds", "profile_execution_bounds", "production_eligible", "production_enabled",
    "available_capacity", "qualification_candidates",
))


def old_controller_accepts(row: dict) -> bool:
    """The 0.6.195 controller's exact key-set test (fleet_nodes._render_capability)."""
    return set(row) == OLD_ROW_KEYS


class OptInFixture(unittest.TestCase):
    """Reuses the receipt tests' installed-tools fixture without re-running their cases."""

    setUp = base.QualificationReceiptAuthorityTests.setUp
    write_record = base.QualificationReceiptAuthorityTests.write_record
    measured = base.QualificationReceiptAuthorityTests.measured

    def executor(self, *, memory: int = 4_000_000_000, loader=None) -> RenderExecutor:
        broker = SimpleNamespace(
            render_dispatch_allowed=lambda _machine, _owner=None: True,
            busy_machines=lambda: set())
        return RenderExecutor(
            runtime=SimpleNamespace(qualification_consumed=lambda _intent: False),
            assets=None, authority=loader or self.loader, machine="local",
            broker_api=broker, available_memory=lambda: memory)

    def opt_in(self, who: str = "gateway-admin", **changes) -> dict:
        return self.loader.set_opt_in(True, who, **changes)


class DefaultOffTests(OptInFixture):
    def test_nothing_is_offered_without_the_setting(self) -> None:
        self.assertIsNone(self.loader.snapshot())
        state = self.loader.opt_in_state()
        self.assertIs(state["accept_unqualified"], False)
        self.assertIs(state["effective"], False)
        self.assertIsNone(state["set_by"])
        self.assertFalse((self.data_root / "render-authority/opt-in").exists(),
                         "reading the state must not create anything on disk")

    def test_the_row_is_exactly_the_0_6_195_row(self) -> None:
        self.assertEqual(self.executor().capability(), {
            "operation": "video.final_render", "physical_machine_id": "local",
            "hardware_class": None, "profile_digest": None, "tools_digest": None,
            "compiler_digest": None, "qualification_digest": None, "selected_tier": None,
            "exact_encoder": None, "font_support": False, "bounds": None,
            "profile_execution_bounds": None, "production_eligible": False,
            "production_enabled": False,
            "available_capacity": {"slots": 0, "slots_total": 1},
            "qualification_candidates": [],
        })

    def test_a_saved_off_is_the_same_bytes_as_never_having_been_asked(self) -> None:
        never = json.dumps(self.executor().capability(), sort_keys=True)
        self.loader.set_opt_in(True, "gateway-admin")
        self.loader.set_opt_in(False, "gateway-admin")
        self.assertEqual(json.dumps(self.executor().capability(), sort_keys=True), never)
        self.assertIsNone(self.loader.snapshot())
        self.assertIsNone(self.loader.resolve({"recipe": {"profile_digest": "1" * 64}}))

    def test_an_unreadable_or_loosened_record_fails_closed(self) -> None:
        self.opt_in()
        self.assertIsNotNone(self.loader.snapshot())
        path = self.data_root / "render-authority/opt-in/unqualified.json"
        path.chmod(0o666)
        self.assertIsNone(self.loader.snapshot())
        self.assertIs(self.loader.opt_in_state()["accept_unqualified"], False)
        path.chmod(0o600)
        path.write_bytes(b'{"enabled":true}')
        self.assertIsNone(self.loader.snapshot())


class RelayPersistenceTests(OptInFixture):
    def test_the_choice_survives_a_restart_with_who_and_when(self) -> None:
        state = self.opt_in("owner@gateway")
        self.assertTrue(state["accept_unqualified"])
        self.assertTrue(state["effective"])
        path = self.data_root / "render-authority/opt-in/unqualified.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

        restarted = InstalledRenderAuthorityLoader(
            self.resources, data_root=self.data_root, clock=lambda: 9_999,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16})
        again = restarted.opt_in_state()
        self.assertEqual((again["accept_unqualified"], again["set_by"], again["set_at"]),
                         (True, "owner@gateway", 1_000))
        self.assertEqual(again["provenance"], OPT_IN_PROVENANCE)
        self.assertIsNotNone(restarted.snapshot())

    def test_it_is_created_when_the_authority_folder_does_not_exist_yet(self) -> None:
        fresh = self.root / "fresh-data"
        fresh.mkdir()
        loader = InstalledRenderAuthorityLoader(
            self.resources, data_root=fresh, clock=lambda: 1_000,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16})
        self.assertTrue(loader.set_opt_in(True, "owner")["accept_unqualified"])
        self.assertEqual((fresh / "render-authority").stat().st_mode & 0o777, 0o700)

    def test_turning_it_off_and_on_again_replaces_the_record_not_the_encode(self) -> None:
        hardware = {**render_encode.DEFAULT_OPT_IN_ENCODE, "requested_effort": "hardware"}
        self.opt_in("first", encode=hardware)
        off = self.loader.set_opt_in(False, "second")
        self.assertEqual((off["accept_unqualified"], off["set_by"]), (False, "second"))
        self.assertIsNone(self.loader.snapshot())
        on = self.loader.set_opt_in(True, "third")
        self.assertEqual(on["set_by"], "third")
        self.assertEqual(on["encode"], hardware, "the encode already chosen is kept")
        self.assertEqual(on["selected_tier"], render_encode.primary_pair(hardware)[0])

    def test_bad_requests_are_refused_with_a_sentence_and_change_nothing(self) -> None:
        for label, args in {
            "not a bool": ("yes", "owner"),
            "no who": (True, None),
            "empty who": (True, ""),
            "non-ascii who": (True, "ownér"),
            "long who": (True, "x" * 121),
            "unknown encode": (True, "owner", {"container": "mp4"}),
            "bitrate capped without probe": (True, "owner", {**render_encode.DEFAULT_OPT_IN_ENCODE,
                                                               "bitrate_kbps": 4000}),
        }.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self.loader.set_opt_in(*args)
        self.assertFalse((self.data_root / "render-authority/opt-in").exists())

    def test_only_the_fleet_credential_can_reach_the_routes_a_leaf_cannot_call_on_another(self) -> None:
        for method, path in (
            ("GET", "/api/hub/fleet/nodes/shop-m4/render-authority"),
            ("POST", "/api/hub/fleet/nodes/shop-m4/render-authority"),
            ("GET", "/api/hub/render-authority"),
            ("POST", "/api/hub/render-authority"),
        ):
            with self.subTest(method=method, path=path):
                self.assertTrue(auth.fleet_path_allowed(method, path))
                self.assertFalse(auth.node_path_allowed(method, path),
                                 "a leaf's own credential must not grant another Mac")
        self.assertFalse(auth.fleet_path_allowed("DELETE", "/api/hub/render-authority"))
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/render-authority/extra"))


class RelayClientTests(unittest.TestCase):
    """The controller's relay uses the existing dial-then-queue path and nothing else."""

    NODE = {"id": "shop-m4", "address": "http://192.168.1.20:47873"}

    class Client:
        def __init__(self, response=None, error=None):
            self.calls: list[tuple] = []
            self.response, self.error = response, error

        async def request(self, method, url, json=None, timeout=None):
            self.calls.append((method, url, json))
            if self.error:
                raise self.error
            return self.response

        async def aclose(self):
            pass

    def run_relay(self, client, method="POST", body=None):
        roster = mock.patch.object(fleet_nodes, "_read", return_value={"shop-m4": self.NODE})
        refresh = mock.patch.object(fleet_nodes, "_refresh_render_capability",
                                    new=mock.AsyncMock())
        with roster, refresh as refreshed:
            result = asyncio.run(fleet_nodes.relay_render_authority(
                "shop-m4", method, body, client=client))
        return result, refreshed

    def response(self, status, body):
        return httpx.Response(status, json=body, request=httpx.Request("POST", "http://x"))

    def test_a_change_is_posted_to_that_macs_own_hub_and_its_row_is_refreshed(self) -> None:
        body = {"accept_unqualified": True, "requested_by": "owner"}
        state = {"accept_unqualified": True, "set_by": "owner"}
        client = self.Client(self.response(200, {"ok": True, "render_authority": state}))
        result, refreshed = self.run_relay(client, "POST", body)
        self.assertEqual(client.calls, [(
            "POST", "http://192.168.1.20:47873/api/hub/render-authority", body)])
        self.assertEqual(result, {"ok": True, "node": "shop-m4", "render_authority": state})
        refreshed.assert_awaited_once()

    def test_a_read_changes_nothing_and_does_not_refresh(self) -> None:
        client = self.Client(self.response(200, {"ok": True, "render_authority": {}}))
        _result, refreshed = self.run_relay(client, "GET")
        self.assertEqual(client.calls[0][0], "GET")
        refreshed.assert_not_awaited()

    def test_an_older_hub_is_named_not_mistaken_for_a_failure_to_save(self) -> None:
        for status in (401, 403, 404, 405):
            with self.subTest(status):
                client = self.Client(self.response(status, {}))
                with self.assertRaises(fleet_nodes.StartupRelayError) as raised:
                    self.run_relay(client, "POST", {"accept_unqualified": True})
                self.assertEqual(raised.exception.body["code"], "RENDER_AUTHORITY_UNSUPPORTED")

    def test_a_refusal_with_a_sentence_reaches_the_caller_unchanged(self) -> None:
        refusal = {"ok": False, "code": "BAD_SETTING", "detail": "requested_by must name who"}
        with self.assertRaises(fleet_nodes.StartupRelayError) as raised:
            self.run_relay(self.Client(self.response(400, refusal)), "POST", {})
        self.assertEqual((raised.exception.status, raised.exception.body), (400, refusal))

    def test_an_unreachable_mac_is_held_for_its_next_check_in(self) -> None:
        held = fleet_nodes.StartupRelayError(202, {"ok": True, "queued": True, "command": "c_1"})
        queue = mock.patch.object(fleet_nodes, "_queue_for_unreachable",
                                  new=mock.AsyncMock(return_value=held))
        client = self.Client(error=httpx.ConnectError("down"))
        with queue as queued, self.assertRaises(fleet_nodes.StartupRelayError) as raised:
            self.run_relay(client, "POST", {"accept_unqualified": False, "requested_by": "o"})
        self.assertEqual(raised.exception.status, 202)
        self.assertEqual(queued.await_args.kwargs["kind"], "render_authority")
        self.assertEqual(queued.await_args.args[:3], (
            "shop-m4", "POST", "/api/hub/render-authority"))

    def test_an_unknown_mac_is_refused(self) -> None:
        with mock.patch.object(fleet_nodes, "_read", return_value={}):
            with self.assertRaises(fleet_nodes.StartupRelayError) as raised:
                asyncio.run(fleet_nodes.relay_render_authority("ghost", "GET"))
        self.assertEqual(raised.exception.status, 404)


class OnRowTests(OptInFixture):
    def test_the_row_is_a_production_row_that_says_it_is_unqualified(self) -> None:
        self.opt_in("owner@gateway")
        row = self.executor().capability()
        self.assertEqual(set(row), OLD_ROW_KEYS | {"accepted_unqualified"})
        self.assertEqual(row["accepted_unqualified"], {
            "provenance": "owner-opt-in-unqualified", "accepted_by": "owner@gateway",
            "accepted_at": 1_000})
        self.assertIs(row["production_enabled"], True)
        self.assertIs(row["production_eligible"], True)
        self.assertEqual(row["available_capacity"], {"slots": 1, "slots_total": 1})
        pair = render_encode.primary_pair(render_encode.DEFAULT_OPT_IN_ENCODE)
        self.assertEqual((row["selected_tier"], row["exact_encoder"]), pair)
        self.assertEqual(row["hardware_class"], HARDWARE_CLASS)
        self.assertIs(row["font_support"], False)
        self.assertEqual(len(row["qualification_digest"]), 64)
        self.assertEqual(capabilities._render_slots(row), 1)
        # The encode fact the Gateway intersects is the compiler's own, for exactly that pair.
        encode = render_encode.supported_encode(row["selected_tier"], row["exact_encoder"])
        self.assertIn(render_encode.DEFAULT_OPT_IN_ENCODE, encode["entries"])

    def test_the_memory_floor_is_the_profiles_own_limit_and_is_still_enforced(self) -> None:
        self.opt_in()
        row = self.executor().capability()
        self.assertEqual(row["bounds"]["min_available_memory_bytes"], 100_000_000)
        self.assertEqual(row["bounds"]["max_rss_bytes"], 100_000_000)
        short = self.executor(memory=99_999_999).capability()
        self.assertEqual(short["available_capacity"]["slots"], 0)
        self.assertIn("accepted_unqualified", short, "the authority stays; only the slot goes")

    def test_tool_and_hardware_identity_checks_still_apply(self) -> None:
        self.opt_in()
        self.assertTrue(self.loader.opt_in_state()["effective"])
        ffmpeg = self.tools_root / "ffmpeg"
        original = ffmpeg.read_bytes()
        ffmpeg.write_bytes(b"swapped-ffmpeg")
        try:
            self.assertIsNone(self.loader.snapshot())
            state = self.loader.opt_in_state()
            self.assertEqual((state["accept_unqualified"], state["effective"]), (True, False))
            self.assertNotIn("accepted_unqualified", self.executor().capability())
        finally:
            ffmpeg.write_bytes(original)
        other = InstalledRenderAuthorityLoader(
            self.resources, data_root=self.data_root, clock=lambda: 1_000,
            hardware_identity=lambda: {"chip": "", "memory_gb": 16})
        self.assertIsNone(other.snapshot(), "an unidentifiable Mac offers nothing")

    def test_a_measured_enabled_record_always_wins(self) -> None:
        self.opt_in()
        document = dict(self.document)
        self.write_record("enabled", f"{HARDWARE_CLASS}.json",
                          value={**document, "intent_id": base.INTENT_ID,
                                 "measured": self.measured()})
        row = self.executor().capability()
        self.assertNotIn("accepted_unqualified", row)
        self.assertEqual(row["selected_tier"], document["selected_tier"])

    def test_an_unqualified_document_cannot_pass_as_an_enabled_record(self) -> None:
        self.opt_in()
        unqualified = self.loader.snapshot().qualification.document
        self.write_record("enabled", f"{HARDWARE_CLASS}.json", value=unqualified)
        # The enabled/ reader's key set is closed, so the file is not a record at all; what
        # answers is still the opt-in, labelled as such, never a measured production authority.
        self.assertIsNotNone(self.loader.snapshot().opt_in)


class MixedVersionRowTests(OptInFixture):
    def test_off_rows_are_accepted_by_old_and_new_controllers(self) -> None:
        row = self.executor().capability()
        self.assertTrue(old_controller_accepts(row))
        self.assertEqual(fleet_nodes._render_capability(row), row)

    def test_an_older_leaf_row_is_still_accepted_by_this_controller(self) -> None:
        self.write_record("enabled", f"{HARDWARE_CLASS}.json",
                          value={**self.document, "intent_id": base.INTENT_ID,
                                 "measured": self.measured()})
        row = self.executor().capability()
        self.assertTrue(old_controller_accepts(row))
        self.assertEqual(fleet_nodes._render_capability(row), row)

    def test_an_opted_in_row_is_accepted_here_and_is_the_only_row_an_old_controller_drops(self) -> None:
        self.opt_in()
        row = self.executor().capability()
        self.assertEqual(fleet_nodes._render_capability(row), row)
        self.assertFalse(old_controller_accepts(row))

    def test_a_row_claiming_the_opt_in_without_being_the_production_row_is_refused(self) -> None:
        self.opt_in()
        row = self.executor().capability()
        evidence = row["accepted_unqualified"]
        for label, broken in {
            "other provenance": {**row, "accepted_unqualified": {
                **evidence, "provenance": "measured"}},
            "no who": {**row, "accepted_unqualified": {
                key: value for key, value in evidence.items() if key != "accepted_by"}},
            "extra evidence": {**row, "accepted_unqualified": {**evidence, "note": "x"}},
            "bad time": {**row, "accepted_unqualified": {**evidence, "accepted_at": 0}},
            "not production": {**row, "production_eligible": False},
            "not enabled": {**row, "production_enabled": False},
            "no tier": {**row, "selected_tier": None},
            "extra key": {**row, "other": 1},
        }.items():
            with self.subTest(label):
                self.assertIsNone(fleet_nodes._render_capability(broken))


class ReceiptHonestyTests(OptInFixture):
    def test_the_authority_carries_no_measurement_and_says_who_and_when(self) -> None:
        self.opt_in("owner@gateway")
        document = self.loader.snapshot().qualification.document
        self.assertNotIn("measured", document)
        self.assertNotIn("intent_id", document)
        self.assertEqual(document["opt_in"]["provenance"], "owner-opt-in-unqualified")
        self.assertIs(document["production_eligible"], False)
        # Re-saving the setting later (a new time) must not change the authority a queued
        # job was accepted under.
        before = document["qualification_digest"]
        later = InstalledRenderAuthorityLoader(
            self.resources, data_root=self.data_root, clock=lambda: 5_000,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16})
        later.set_opt_in(True, "someone-else")
        self.assertEqual(later.snapshot().qualification.qualification_digest, before)

    def test_a_real_run_leaves_a_receipt_with_its_real_numbers_and_can_be_promoted(self) -> None:
        self.opt_in()
        authority = self.loader.snapshot()
        job_id = str(uuid4())
        receipt_document = self.loader.receipt_document_for(authority.qualification.document)
        measured = self.measured(
            wall_seconds=17, peak_rss_bytes=4_096, min_free_bytes=9_000_000,
            output_bytes=777, hub_job_id=job_id)
        self.assertTrue(self.loader.write_receipt(receipt_document, job_id, measured))
        path = self.data_root / "render-authority/receipts" / (
            InstalledRenderAuthorityLoader._receipt_name(
                HARDWARE_CLASS, job_id, receipt_document["qualification_digest"]))
        written = json.loads(path.read_bytes())
        self.assertEqual(written["measured"], measured, "recorded as measured, never rounded")
        self.assertNotIn("opt_in", written)
        self.assertIs(written["production_eligible"], False)
        self.assertEqual(written["format"], "studio.render.qualification-receipt.v1")

        # The designed promotion: copy the receipt into enabled/ and the Mac is measured.
        self.write_record("enabled", f"{HARDWARE_CLASS}.json", value=written)
        promoted = self.loader.snapshot()
        self.assertIsNone(promoted.opt_in)
        self.assertNotIn("accepted_unqualified", self.executor().capability())

    def test_only_an_opted_in_document_converts_to_a_receipt(self) -> None:
        self.assertIsNone(self.loader.receipt_document_for(self.document))
        self.assertIsNone(self.loader.receipt_document_for({}))

    def test_a_run_without_a_durable_receipt_is_not_success(self) -> None:
        self.opt_in()
        document = self.loader.snapshot().qualification.document
        output = SimpleNamespace(output_size_bytes=1, receipt_digest="5" * 64)
        context = SimpleNamespace(
            purpose="production", qualification_permit=None, hub_job_id=str(uuid4()),
            qualification=SimpleNamespace(document=document))
        clock = SimpleNamespace(value=0.0)
        executor = RenderExecutor(
            runtime=SimpleNamespace(), assets=None, authority=self.loader,
            machine="local", broker_api=SimpleNamespace(), monotonic=lambda: clock.value)
        measured = {"peak_rss_bytes": 1, "min_free_bytes": 1}

        with mock.patch.object(self.loader, "write_receipt", return_value=True) as writer:
            clock.value = 20.2
            self.assertTrue(executor._record_qualification(context, output, measured, 0.0))
            self.assertEqual(writer.call_args.args[2]["wall_seconds"], 21)
            self.assertEqual(writer.call_args.args[1], context.hub_job_id)
            self.assertEqual(writer.call_args.args[0]["format"],
                             "studio.render.qualification-receipt.v1")
            clock.value = 30.1
            self.assertIs(executor._record_qualification(context, output, measured, 0.0), False)
            self.assertEqual(writer.call_count, 1, "an over-bound wall is not clamped into a receipt")
        with mock.patch.object(self.loader, "write_receipt", return_value=False):
            clock.value = 1.0
            self.assertIs(executor._record_qualification(context, output, measured, 0.0), False)
        no_writer = RenderExecutor(
            runtime=SimpleNamespace(), assets=None, authority=SimpleNamespace(),
            machine="local", broker_api=SimpleNamespace())
        self.assertFalse(no_writer._record_qualification(context, output, measured, 0.0))

    def test_an_ordinary_production_run_still_writes_nothing(self) -> None:
        output = SimpleNamespace(output_size_bytes=1, receipt_digest="5" * 64)
        context = SimpleNamespace(
            purpose="production", qualification_permit=None, hub_job_id="job-1",
            qualification=SimpleNamespace(document=self.document))
        executor = RenderExecutor(
            runtime=SimpleNamespace(), assets=None, authority=self.loader,
            machine="local", broker_api=SimpleNamespace())
        with mock.patch.object(self.loader, "write_receipt") as writer:
            self.assertTrue(executor._record_qualification(
                context, output, {"peak_rss_bytes": 1, "min_free_bytes": 1}, 0.0))
        writer.assert_not_called()


class RealTierLabelTests(unittest.TestCase):
    """The pinned compiler's own labels contain a non-ASCII middle dot; a receipt must carry them."""

    def receipt(self, tier):
        from components.hub.backend.render_jobs import ValidatedOutputReceipt
        return ValidatedOutputReceipt(
            owner_scope="site:node:fleet", hub_job_id="job-1", attempt_id="attempt-1",
            accepted_request_sha256="1" * 64, receipt_digest="2" * 64, output_sha256="3" * 64,
            output_size_bytes=1, profile_digest="4" * 64, tools_digest="5" * 64,
            compiler_digest="6" * 64, qualification_digest="7" * 64,
            generated_command_digest="8" * 64, selected_tier=tier, feature_losses=())

    def test_every_label_the_compiler_can_issue_is_accepted_in_a_receipt(self) -> None:
        for effort in ("ultrafast", "medium", "slow", "hardware"):
            pair = render_encode.primary_pair(
                {**render_encode.DEFAULT_OPT_IN_ENCODE, "requested_effort": effort})
            with self.subTest(effort=effort):
                self.assertEqual(self.receipt(pair[0]).selected_tier, pair[0])

    def test_an_empty_padded_control_or_oversize_label_is_still_refused(self) -> None:
        for bad in ("", " H.264", "H.264 ", "H.264\n", "a\x00b", "x" * 257, 5, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.receipt(bad)


class NetworkFactsTests(unittest.TestCase):
    def test_interface_addresses_are_facts_not_a_classification(self) -> None:
        from components.hub.backend import resources
        import socket

        def addr(family, address, netmask):
            return SimpleNamespace(family=family, address=address, netmask=netmask)

        interfaces = {
            "lo0": [addr(socket.AF_INET, "127.0.0.1", "255.0.0.0")],
            "en0": [addr(socket.AF_INET, "192.168.1.20", "255.255.255.0"),
                    addr(socket.AF_INET6, "fe80::1", "ffff:ffff:ffff:ffff::"),
                    addr(socket.AF_INET, "169.254.3.4", "255.255.0.0")],
            "utun4": [addr(socket.AF_INET, "100.101.102.103", "255.255.255.255")],
            "bad": [addr(socket.AF_INET, "not-an-ip", "255.255.255.0")],
        }
        with mock.patch.object(resources.psutil, "net_if_addrs", return_value=interfaces):
            self.assertEqual(resources.network_addresses(), [
                {"interface": "en0", "address": "192.168.1.20", "prefix_length": 24},
                {"interface": "utun4", "address": "100.101.102.103", "prefix_length": 32},
            ])
        with mock.patch.object(resources.psutil, "net_if_addrs", side_effect=OSError):
            self.assertEqual(resources.network_addresses(), [])


if __name__ == "__main__":
    unittest.main()
