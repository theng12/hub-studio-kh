"""Focused regressions for the frozen r5 fleet memory-admission route and relay.

The local Models-screen route is not exercised here: the fleet route's strict body, its one
RAM pair/reset, the explicit reference-write refusal, the 64-hex CAS and the relay's pending /
unknown / passthrough rules are. No live Hub, network, model library or Image worker is touched;
the store is a temp file and the target is a fake client.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# `standalone` builds an app at import time, so its private roots must exist first. All of them
# are throwaway: no real Hub data directory, worker manifest or model library is touched.
_ROOT = tempfile.TemporaryDirectory(prefix="studiohub-fleet-memory-")
atexit.register(_ROOT.cleanup)
os.environ.setdefault("STUDIOHUB_DATA_DIR", _ROOT.name)
_WORKERS = Path(_ROOT.name) / "workers.json"
_WORKERS.write_text(json.dumps([{
    "id": "image", "modality": "image", "host": "127.0.0.1", "port": 51101,
    "machine": "local", "local": True, "runtime_identity": {"id": "image-runtime"},
    "hub_token": "fixture-hub-token"}]))
os.environ.setdefault("STUDIOHUB_WORKERS_FILE", str(_WORKERS))

from components.hub import standalone  # noqa: E402
from components.hub.backend import auth, fleet_nodes, memory_admission  # noqa: E402

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"
ROW = {"repo": MODEL, "min_total_memory_gb": 8.0}
TIER_16 = 16 * 1024 ** 3
HEX = set("0123456789abcdef")


def payload(response) -> tuple[int, dict]:
    return response.status_code, json.loads(response.body)


class FakeAnswer:
    def __init__(self, status_code: int, body: object):
        self.status_code = status_code
        self._body = body

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, answer: FakeAnswer | None = None, error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method: str, url: str, json=None, timeout=None):
        self.calls.append((method, url, json))
        if self.error is not None:
            raise self.error
        return self.answer

    async def aclose(self) -> None:
        return None


class LocalAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Path(self.temp.name) / "memory_admission_overrides.json"
        patches = [
            mock.patch.object(memory_admission, "SETTINGS_FILE", self.settings),
            mock.patch.object(standalone, "_admission_total_memory_bytes", lambda: TIER_16),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)

    def read(self, *, model: str = MODEL, mode: str | None = None,
             node: str = "local", node_id: str = "machine-a") -> tuple[int, dict]:
        return payload(standalone._fleet_memory_admission_read(
            model=model, mode=mode, entry=ROW if model == MODEL else None,
            node=node, node_id=node_id))

    def write(self, body: dict) -> tuple[int, dict]:
        return payload(standalone._fleet_memory_admission_write(
            body=body, entry=ROW, node="local", node_id="machine-a",
            total_memory_bytes=TIER_16))

    def revision(self, mode: str | None = None) -> str:
        return self.read(mode=mode)[1]["revision"]

    def test_read_carries_both_identities_and_a_full_hex_token(self) -> None:
        status, body = self.read()
        self.assertEqual(status, 200)
        self.assertEqual(body["node"], "local")
        self.assertEqual(body["node_id"], "machine-a")
        self.assertEqual(len(body["revision"]), 64)
        self.assertTrue(set(body["revision"]) <= HEX)
        self.assertEqual(body["override_scope"], None)
        # Asking by the machine id keeps that identity in `node` and never overwrites `node_id`.
        _, alias = self.read(node="machine-a")
        self.assertEqual((alias["node"], alias["node_id"]), ("machine-a", "machine-a"))

    def test_missing_model_and_bad_mode_are_structured(self) -> None:
        status, body = self.read(model="")
        self.assertEqual((status, body["code"], body["field"]),
                         (400, "MISSING_MODEL", "model"))
        status, body = self.read(mode="video")
        self.assertEqual((status, body["code"], body["field"]), (400, "BAD_MODE", "mode"))

    def test_a_mode_read_keeps_the_core_reference_readback(self) -> None:
        status, body = self.read(mode="edit")
        self.assertEqual(status, 200)
        self.assertTrue(body["reference_policy"]["available"])
        self.assertEqual(body["reference_policy"]["physical_tier"], "16")
        self.assertIsInstance(body["reference_policy"]["rows"], list)
        self.assertNotIn("reference_policy", self.read()[1])

    def test_numeric_ram_write_requires_both_values(self) -> None:
        revision = self.revision()
        base = {"model": MODEL, "expected_revision": revision}
        for extra, code in (({"min_total_memory_gb": 9}, "RAM_PAIR_REQUIRED"),
                            ({"min_free_memory_gb": 3}, "RAM_PAIR_REQUIRED"),
                            ({"min_free_memory_gb": None, "min_total_memory_gb": 9},
                             "RAM_RESET_MIXED")):
            status, body = self.write({**base, **extra})
            self.assertEqual((status, body["code"]), (400, code), extra)
        for value in (True, "3", float("nan"), float("inf"), -1):
            status, body = self.write({**base, "min_free_memory_gb": value,
                                       "min_total_memory_gb": 9})
            self.assertEqual((status, body["code"], body["field"]),
                             (400, "BAD_VALUE", "min_free_memory_gb"), value)
        self.assertFalse(self.settings.exists(), "a refused write must write nothing")

    def test_a_total_edit_preserves_the_supplied_free_value(self) -> None:
        revision = self.revision()
        status, body = self.write({"model": MODEL, "expected_revision": revision,
                                   "min_free_memory_gb": 3, "min_total_memory_gb": 12})
        self.assertEqual(status, 200)
        self.assertEqual(body["effective_min_total_memory_gb"], 12.0)
        self.assertEqual(body["effective_min_free_memory_gb"], 3.0)
        status, body = self.write({"model": MODEL, "expected_revision": body["revision"],
                                   "min_free_memory_gb": 3, "min_total_memory_gb": 14})
        self.assertEqual(status, 200)
        self.assertEqual(body["effective_min_total_memory_gb"], 14.0)
        self.assertEqual(body["effective_min_free_memory_gb"], 3.0,
                         "the caller's unchanged Free must not be re-derived")

    def test_reset_clears_only_its_scope_and_a_reset_regains_the_earlier_token(self) -> None:
        initial = self.revision()
        self.write({"model": MODEL, "expected_revision": initial,
                    "min_free_memory_gb": 3, "min_total_memory_gb": 12})
        # The mode scope has its own token; it is not the model-wide token above.
        mode_token = self.revision("txt2img")
        mode = self.write({"model": MODEL, "mode": "txt2img",
                           "expected_revision": mode_token,
                           "min_free_memory_gb": 2.5, "min_total_memory_gb": 10})[1]
        self.assertEqual(mode["override_scope"], "mode")

        # A mode reset removes only model::mode; the model-wide record survives and is labelled.
        fallback = self.write({"model": MODEL, "mode": "txt2img",
                               "expected_revision": mode["revision"],
                               "min_free_memory_gb": None,
                               "min_total_memory_gb": None})[1]
        self.assertEqual(fallback["override_scope"], "model")
        self.assertEqual(fallback["effective_min_total_memory_gb"], 12.0)
        self.assertEqual(fallback["effective_min_free_memory_gb"], 3.0)

        cleared = self.write({"model": MODEL, "expected_revision": self.revision(),
                              "min_free_memory_gb": None,
                              "min_total_memory_gb": None})[1]
        self.assertIsNone(cleared["override_scope"])
        self.assertEqual(cleared["revision"], initial,
                         "a reset to the identical state legitimately regains the earlier token")
        self.assertFalse(self.settings.exists())

    def test_reference_writes_are_explicitly_unavailable_and_write_nothing(self) -> None:
        revision = self.revision()
        status, body = self.write({"model": MODEL, "mode": "edit",
                                   "expected_revision": revision,
                                   "reference": {"allowed": True},
                                   "reference_count": None})
        self.assertEqual((status, body["code"]), (400, "TIER_UNKNOWN"))
        self.assertIn("nothing was written", body["detail"])
        self.assertFalse(self.settings.exists())

    def test_reference_identity_and_shape_are_strict(self) -> None:
        revision = self.revision()
        base = {"model": MODEL, "mode": "edit", "expected_revision": revision}
        status, body = self.write({**base, "reference": {"allowed": True}})
        self.assertEqual(body["code"], "REFERENCE_COUNT_REQUIRED")
        status, body = self.write({**base, "reference_count": None})
        self.assertEqual(body["code"], "REFERENCE_COUNT_WITHOUT_REFERENCE")
        for count in (0, -1, True, "1"):
            status, body = self.write({**base, "reference": None, "reference_count": count})
            self.assertEqual((status, body["code"]), (400, "BAD_REFERENCE_COUNT"), count)
        status, body = self.write({**base, "reference": {}, "reference_count": 1})
        self.assertEqual(body["code"], "REFERENCE_EMPTY")
        status, body = self.write({**base, "reference": {"nope": 1}, "reference_count": 1})
        self.assertEqual((body["code"], body["field"]), ("UNKNOWN_FIELD", "nope"))
        status, body = self.write({**base, "reference": {"allowed": "yes"},
                                   "reference_count": 1})
        self.assertEqual((body["code"], body["field"]), ("BAD_VALUE", "allowed"))
        # Reference without a mode is a context error, checked before any row validation.
        status, body = self.write({"model": MODEL, "expected_revision": revision,
                                   "reference": {"allowed": True}, "reference_count": 1})
        self.assertEqual(body["code"], "BAD_MODE_CONTEXT")

    def test_strict_keys_revision_and_nothing_to_write(self) -> None:
        revision = self.revision()
        status, body = self.write({"model": MODEL, "expected_revision": revision, "extra": 1})
        self.assertEqual((body["code"], body["field"]), ("UNKNOWN_FIELD", "extra"))
        for bad in ("abc", "", 5):
            status, body = self.write({"model": MODEL, "expected_revision": bad,
                                       "min_free_memory_gb": 3, "min_total_memory_gb": 9})
            self.assertEqual((status, body["code"]), (400, "BAD_VALUE"), bad)
        status, body = self.write({"model": MODEL, "expected_revision": revision})
        self.assertEqual(body["code"], "NOTHING_TO_WRITE")

    def test_a_stale_revision_is_a_409_with_a_fresh_readback(self) -> None:
        revision = self.revision()
        first = self.write({"model": MODEL, "expected_revision": revision,
                            "min_free_memory_gb": 3, "min_total_memory_gb": 12})[1]
        status, body = self.write({"model": MODEL, "expected_revision": revision,
                                   "min_free_memory_gb": 4, "min_total_memory_gb": 12})
        self.assertEqual((status, body["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(body["current_revision"], first["revision"])
        self.assertEqual(body["admission"]["effective_min_free_memory_gb"], 3.0)


class RelayTests(unittest.TestCase):
    def setUp(self) -> None:
        roster = {"node-a": {"address": "http://node.test"}}
        patcher = mock.patch.object(fleet_nodes, "_read", lambda: roster)
        patcher.start()
        self.addCleanup(patcher.stop)

    def relay(self, method: str, client: FakeClient, *, query=None):
        return asyncio.run(fleet_nodes.relay_memory_admission(
            "node-a", method, {"model": MODEL, "expected_revision": "a" * 64}
            if method == "PUT" else None, query=query, client=client))

    def test_get_encodes_the_query_and_passes_the_target_body_through(self) -> None:
        answer = {"ok": True, "node": "node-a", "node_id": "node-a", "revision": "b" * 64}
        client = FakeClient(FakeAnswer(200, answer))
        self.assertEqual(self.relay("GET", client,
                                    query={"model": MODEL, "mode": "edit"}), answer)
        method, url, _ = client.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.startswith("http://node.test/api/hub/fleet/nodes/node-a/"
                                       "memory/admission?model="))
        self.assertIn("AITRADER%2FFLUX2-klein-4B-mlx-4bit", url)
        self.assertIn("&mode=edit", url)

    def test_202_is_pending_with_no_readback(self) -> None:
        held = {"ok": True, "queued": True, "node": "node-a", "command": "c1", "detail": "held"}
        answer = self.relay("PUT", FakeClient(FakeAnswer(202, held)))
        self.assertEqual(answer["outcome"], "pending")
        self.assertNotIn("revision", answer)
        self.assertNotIn("admission", answer)

    def test_400_and_409_bodies_survive_the_relay(self) -> None:
        for status, body in ((400, {"ok": False, "code": "BAD_VALUE", "field": "mode"}),
                             (409, {"ok": False, "code": "REVISION_CONFLICT",
                                    "current_revision": "c" * 64,
                                    "admission": {"revision": "c" * 64}})):
            with self.assertRaises(fleet_nodes.StartupRelayError) as caught:
                self.relay("PUT", FakeClient(FakeAnswer(status, body)))
            self.assertEqual((caught.exception.status, caught.exception.body), (status, body))

    def test_an_older_or_different_token_is_node_refused(self) -> None:
        with self.assertRaises(fleet_nodes.StartupRelayError) as caught:
            self.relay("GET", FakeClient(FakeAnswer(404, {"detail": "unknown model"})))
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(caught.exception.body["code"], "NODE_REFUSED")

    def test_an_unknown_node_is_a_404(self) -> None:
        with self.assertRaises(fleet_nodes.StartupRelayError) as caught:
            asyncio.run(fleet_nodes.relay_memory_admission(
                "missing", "GET", query={"model": MODEL}, client=FakeClient()))
        self.assertEqual((caught.exception.status, caught.exception.body["code"]),
                         (404, "UNKNOWN_NODE"))

    def test_get_never_queues_an_unreachable_node(self) -> None:
        with mock.patch.object(fleet_nodes, "_queue_for_unreachable",
                               new=mock.AsyncMock()) as queued:
            with self.assertRaises(fleet_nodes.StartupRelayError) as caught:
                self.relay("GET", FakeClient(error=httpx.ConnectError("off")))
        queued.assert_not_awaited()
        self.assertEqual((caught.exception.status, caught.exception.body["code"]),
                         (502, "NODE_UNREACHABLE"))

    def test_a_pre_send_put_failure_is_queued(self) -> None:
        held = fleet_nodes.StartupRelayError(202, {"ok": True, "queued": True,
                                                   "command": "c1"})
        with mock.patch.object(fleet_nodes, "_queue_for_unreachable",
                               new=mock.AsyncMock(return_value=held)) as queued:
            with self.assertRaises(fleet_nodes.StartupRelayError) as caught:
                self.relay("PUT", FakeClient(error=httpx.ConnectError("off")))
        queued.assert_awaited_once()
        self.assertEqual(caught.exception.status, 202)
        self.assertTrue(caught.exception.body["queued"])

    def test_a_maybe_sent_put_failure_is_unknown_and_never_queued(self) -> None:
        with mock.patch.object(fleet_nodes, "_queue_for_unreachable",
                               new=mock.AsyncMock()) as queued:
            with self.assertRaises(fleet_nodes.StartupRelayError) as caught:
                self.relay("PUT", FakeClient(error=httpx.ReadTimeout("late")))
        queued.assert_not_awaited()
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(caught.exception.body["outcome"], "unknown")


class RouteTests(unittest.TestCase):
    """The real app and auth middleware, with only the monitor's catalog and identity faked."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Path(self.temp.name) / "memory_admission_overrides.json"
        patches = [
            mock.patch.object(memory_admission, "SETTINGS_FILE", self.settings),
            mock.patch.object(standalone, "_admission_total_memory_bytes", lambda: TIER_16),
            mock.patch.object(standalone.StudioMonitor, "cached_aggregate_catalog",
                              lambda self: {"models": [ROW]}),
            mock.patch.object(fleet_nodes, "this_machine",
                              lambda: {"id": "machine-a", "name": "A"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)
        from components.hub.backend import peers
        self.token = peers.fleet_token()
        self.app = standalone.create_app(start_background=False)

    def request(self, method: str, path: str, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.app,
                                            client=("100.64.0.9", 51234))
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://fixture-hub") as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(run())

    def test_the_fleet_route_reads_and_writes_this_mac_with_its_fleet_token(self) -> None:
        headers = {"X-Hub-Token": self.token}
        read = self.request("GET", "/api/hub/fleet/nodes/local/memory/admission",
                            params={"model": MODEL}, headers=headers)
        self.assertEqual(read.status_code, 200, read.text)
        body = read.json()
        self.assertEqual((body["node"], body["node_id"]), ("local", "machine-a"))
        write = self.request("PUT", "/api/hub/fleet/nodes/machine-a/memory/admission",
                             headers=headers,
                             json={"model": MODEL, "expected_revision": body["revision"],
                                   "min_free_memory_gb": 3, "min_total_memory_gb": 12})
        self.assertEqual(write.status_code, 200, write.text)
        self.assertEqual(write.json()["effective_min_total_memory_gb"], 12.0)

    def test_unknown_host_memory_keeps_model_wide_ram_and_never_fabricates_a_tier(self) -> None:
        from components.hub.backend import monitor
        with mock.patch.object(standalone, "_admission_total_memory_bytes", lambda: None):
            headers = {"X-Hub-Token": self.token}
            wide = self.request("GET", "/api/hub/fleet/nodes/local/memory/admission",
                                params={"model": MODEL}, headers=headers)
            self.assertEqual(wide.status_code, 200, wide.text)
            body = wide.json()
            self.assertEqual(body["effective_min_total_memory_gb"], 8.0)
            inventory = monitor._memory_admission_inventory(
                MODEL, ROW, node_id="machine-a", total_memory_bytes=None)
            self.assertEqual(body["revision"], inventory["ram"]["model_wide"]["revision"],
                             "the model-wide GET token must match the inventory top token")

            mode = self.request("GET", "/api/hub/fleet/nodes/local/memory/admission",
                                params={"model": MODEL, "mode": "edit"}, headers=headers)
            self.assertEqual(mode.status_code, 200, mode.text)
            mode_body = mode.json()
            policy = mode_body["reference_policy"]
            self.assertFalse(policy["available"])
            self.assertNotIn("physical_tier", policy, "unknown bytes must not band as '<8'")
            self.assertIn("reason", policy)
            self.assertEqual(mode_body["revision"],
                             inventory["reference"]["edit"]["revision"],
                             "the mode GET token must match the inventory mode token")
            self.assertEqual(mode_body["revision_covers"],
                             inventory["ram"]["modes"]["edit"]["revision_covers"])

    def test_a_non_controller_answers_not_controller_instead_of_relaying(self) -> None:
        with mock.patch.object(standalone.control_plane, "load_settings",
                               lambda: {"role": "agent"}):
            answer = self.request("GET", "/api/hub/fleet/nodes/other-mac/memory/admission",
                                  params={"model": MODEL},
                                  headers={"X-Hub-Token": self.token})
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer.json()["code"], "NOT_CONTROLLER")


    def _controller(self, client: FakeClient):
        from components.hub.backend import fleet_pull
        roster = {"node-a": {"address": "http://node.test"}}
        return [
            mock.patch.object(standalone.control_plane, "load_settings",
                              lambda: {"role": "controller"}),
            mock.patch.object(fleet_nodes, "_read", lambda: roster),
            mock.patch.object(fleet_nodes, "_probe_client", lambda: client),
            mock.patch.object(fleet_nodes, "checkin_alive", lambda row: True),
            mock.patch.object(fleet_pull, "queue_json", lambda *a, **k: {"id": "c1"}),
        ]

    def test_a_held_put_is_202_pending_with_no_readback_and_no_replay(self) -> None:
        client = FakeClient(error=httpx.ConnectError("off"))
        patches = self._controller(client)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        answer = self.request("PUT", "/api/hub/fleet/nodes/node-a/memory/admission",
                              headers={"X-Hub-Token": self.token},
                              json={"model": MODEL, "expected_revision": "a" * 64})
        self.assertEqual(answer.status_code, 202, answer.text)
        body = answer.json()
        self.assertEqual(body["outcome"], "pending")
        self.assertTrue(body["queued"])
        self.assertNotIn("revision", body)
        self.assertNotIn("admission", body)
        self.assertEqual(len(client.calls), 1, "a held write is never replayed")

    def test_a_target_202_is_pending_through_the_outer_route(self) -> None:
        held = {"ok": True, "queued": True, "node": "node-a", "command": "c1", "detail": "held"}
        client = FakeClient(FakeAnswer(202, held))
        patches = self._controller(client)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        answer = self.request("PUT", "/api/hub/fleet/nodes/node-a/memory/admission",
                              headers={"X-Hub-Token": self.token},
                              json={"model": MODEL, "expected_revision": "a" * 64})
        # The relay body says pending; the outer route (standalone.py) returns it as 200 because
        # it passes no status. Pinned here so the gap is visible until that owner fixes it.
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["outcome"], "pending")
        self.assertEqual(len(client.calls), 1)


class AllowlistTests(unittest.TestCase):
    PATH = "/api/hub/fleet/nodes/node-a/memory/admission"

    def test_exactly_two_fleet_tuples_and_no_node_tuple(self) -> None:
        matches = [(method, pattern) for method, pattern in auth.FLEET_ALLOWED
                   if "memory/admission" in pattern]
        self.assertEqual(sorted(method for method, _ in matches), ["GET", "PUT"])
        self.assertTrue(auth.fleet_path_allowed("GET", self.PATH))
        self.assertTrue(auth.fleet_path_allowed("PUT", self.PATH))
        self.assertFalse(auth.fleet_path_allowed("POST", self.PATH))
        self.assertFalse(auth.node_path_allowed("GET", self.PATH))
        self.assertFalse(auth.node_path_allowed("PUT", self.PATH))
        self.assertFalse([pattern for _, pattern in auth.NODE_ALLOWED
                          if "memory/admission" in pattern])
        # The local route keeps its own credential boundary: no Fleet tuple was added for it.
        self.assertFalse(auth.fleet_path_allowed("PUT", "/api/hub/memory/admission"))


if __name__ == "__main__":
    unittest.main()
