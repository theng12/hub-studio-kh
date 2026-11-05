"""Focused checks for the node-side Developer Mode contract.

No runner, model, generation or network is involved. The checks pin the OFF default, the
revisioned exact-byte switch, strict bounded JSON, actor refusal, the refusal-before-acceptance
path and the closed ledger's replay/conflict behaviour.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub import developer_tests as dt


def _request(*, setting_revision: str = "rev-1") -> dict:
    return {
        "format": dt.REQUEST_FORMAT,
        "run_id": "run-1",
        "test_id": "test-1",
        "runner_id": dt.RUNNER_ID,
        "model": {"id": "qwen-image-2-1-turbo-mflux-4bit", "revision": "r1"},
        "kind": "reference_counts",
        "settings": {
            "width_px": 1024,
            "height_px": 1024,
            "steps": 4,
            "guidance": 3.5,
            "seed": 1,
            "memory_mode": "default",
            "reference_count": 1,
            "prompt_characters": 16,
            "fixture_set_id": "fixture-set-1",
            "prompt_sha256": "0" * 64,
        },
        "input_refs": [{
            "fixture_id": "ref-1",
            "sha256": "1" * 64,
            "size_bytes": 100,
        }],
        "requested_limits": {
            "max_runtime_seconds": 600,
            "max_process_tree_rss_bytes": 8_000_000_000,
            "max_system_swap_growth_bytes": 0,
            "test_start_min_free_bytes": 4_000_000_000,
        },
        "runner_revision": "runner-r1",
        "runner_sha256": "2" * 64,
        "fixture_set_sha256": "3" * 64,
        "node_setting_revision": setting_revision,
    }


def _setting_body(revision: str, enabled: bool, request_id: str) -> bytes:
    return json.dumps({
        "format": dt.SETTING_FORMAT,
        "enabled": enabled,
        "expected_revision": revision,
        "request_id": request_id,
    }, separators=(",", ":")).encode()


class DeveloperModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-developer-")
        self.old = os.environ.get("STUDIOHUB_DATA_DIR")
        os.environ["STUDIOHUB_DATA_DIR"] = self.temporary.name
        self.addCleanup(self.restore)
        self.old_identities = dt._identities
        dt._identities = lambda: ("node-a", "controller-a")
        self.addCleanup(setattr, dt, "_identities", self.old_identities)
        dt.ensure_initial_setting()

    def restore(self) -> None:
        if self.old is None:
            os.environ.pop("STUDIOHUB_DATA_DIR", None)
        else:
            os.environ["STUDIOHUB_DATA_DIR"] = self.old
        self.temporary.cleanup()

    def _setting(self) -> dict:
        status, body = dt.setting_state()
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_absence_becomes_a_verified_off_with_a_real_revision(self) -> None:
        setting = self._setting()
        self.assertFalse(setting["enabled"])
        self.assertTrue(setting["verified"])
        self.assertIsNone(setting["reason"])
        self.assertTrue(dt.SAFE_SEGMENT.fullmatch(setting["revision"]))
        self.assertTrue((Path(self.temporary.name) / "developer-tests" / "setting.json").exists())

    def test_switch_is_revisioned_and_exact_byte_idempotent(self) -> None:
        first = self._setting()
        raw = _setting_body(first["revision"], True, "request-1")
        status, body = dt.change_setting(raw)
        self.assertEqual(status, 200)
        enabled = json.loads(body)
        self.assertTrue(enabled["enabled"])
        self.assertNotEqual(enabled["revision"], first["revision"])

        # Exact replay returns the original result and does not mint another revision.
        status, replay = dt.change_setting(raw)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(replay), json.loads(body))

        # Same request id, different bytes: conflict, not a silent overwrite.
        with self.assertRaises(dt.Refused) as conflict:
            dt.change_setting(_setting_body(first["revision"], False, "request-1"))
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(conflict.exception.code, "conflict")

        # A stale expected revision is refused after a change.
        with self.assertRaises(dt.Refused) as stale:
            dt.change_setting(_setting_body(first["revision"], False, "request-2"))
        self.assertEqual(stale.exception.code, "stale_setting")

    def test_absence_after_startup_is_unverified_and_cannot_be_switched(self) -> None:
        path = Path(self.temporary.name) / "developer-tests" / "setting.json"
        path.unlink()
        setting = self._setting()
        self.assertFalse(setting["enabled"])
        self.assertFalse(setting["verified"])
        self.assertEqual(setting["reason"]["code"], "integrity_error")
        with self.assertRaises(dt.Refused) as refusal:
            dt.change_setting(_setting_body(setting["revision"], True, "request-1"))
        self.assertEqual(refusal.exception.status, 503)

    def test_unreadable_setting_is_disabled_unverified_and_refuses_writes(self) -> None:
        folder = Path(self.temporary.name) / "developer-tests"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "setting.json").write_text("{not json", encoding="utf-8")
        setting = self._setting()
        self.assertFalse(setting["enabled"])
        self.assertFalse(setting["verified"])
        self.assertEqual(setting["reason"]["code"], "integrity_error")
        with self.assertRaises(dt.Refused) as refusal:
            dt.change_setting(_setting_body(setting["revision"], True, "request-1"))
        self.assertEqual(refusal.exception.status, 503)

    def test_strict_json_refuses_duplicates_non_finite_unknown_and_oversize(self) -> None:
        cases = [
            b'{"format":"a","format":"b"}',
            b'{"format":NaN}',
            b'{"format":"a","unknown":1}',
        ]
        for raw in cases:
            with self.assertRaises(dt.Refused):
                dt.validate_setting_request(dt.parse_json(raw))
        with self.assertRaises(dt.Refused) as oversized:
            dt.parse_json(b"x" * (dt.REQUEST_BYTES_CEILING + 1))
        self.assertEqual(oversized.exception.status, 413)

    def test_request_validation_is_closed_and_checks_reference_count(self) -> None:
        dt.validate_request(_request())
        changed = _request()
        changed["input_refs"] = []
        with self.assertRaises(dt.Refused):
            dt.validate_request(changed)
        changed = _request()
        changed["settings"]["seed"] = True
        with self.assertRaises(dt.Refused):
            dt.validate_request(changed)
        changed = _request()
        changed["extra"] = 1
        with self.assertRaises(dt.Refused):
            dt.validate_request(changed)

    def test_submission_refuses_before_acceptance(self) -> None:
        raw = json.dumps(_request(setting_revision=self._setting()["revision"])).encode()
        with self.assertRaises(dt.Refused) as disabled:
            dt.submit(raw, "key-1")
        self.assertEqual(disabled.exception.code, "disabled")

        revision = self._setting()["revision"]
        dt.change_setting(_setting_body(revision, True, "request-1"))
        current = self._setting()["revision"]
        raw = json.dumps(_request(setting_revision=current)).encode()
        with self.assertRaises(dt.Refused) as unsupported:
            dt.submit(raw, "key-1")
        self.assertEqual(unsupported.exception.status, 503)
        self.assertEqual(unsupported.exception.code, "unsupported")

    def test_ledger_replays_and_conflicts_on_exact_bytes(self) -> None:
        raw = json.dumps(_request()).encode()
        first = dt.accept_attempt("key-1", raw, dt.validate_request(json.loads(raw)), "rev-1",
                              actor={"kind": "fleet", "node_id": None}, destination="node-a")
        second = dt.accept_attempt("key-1", raw, dt.validate_request(json.loads(raw)), "rev-1",
                              actor={"kind": "fleet", "node_id": None}, destination="node-a")
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        with self.assertRaises(dt.Refused) as conflict:
            dt.accept_attempt("key-1", raw + b" ", dt.validate_request(json.loads(raw)), "rev-1",
                              actor={"kind": "fleet", "node_id": None}, destination="node-a")
        self.assertEqual(conflict.exception.code, "conflict")

        status, body = dt.attempt_status("key-1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "waiting_idle")
        status, body = dt.cancel_attempt(
            "key-1",
            json.dumps({"format": dt.CANCEL_FORMAT, "request_id": "cancel-1"}).encode())
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["cancel_requested"])
        with self.assertRaises(dt.Refused) as receipt:
            dt.attempt_receipt("key-1")
        self.assertEqual(receipt.exception.code, "incomplete_evidence")

    def test_a_node_credential_cannot_authorise_the_switch(self) -> None:
        request = SimpleNamespace(state=SimpleNamespace(fleet_actor={"kind": "node",
                                                                    "node_id": "node-a"}))
        with self.assertRaises(dt.Refused) as refusal:
            dt._require_actor(request)
        self.assertEqual(refusal.exception.status, 403)
        self.assertEqual(refusal.exception.code, "unauthorized")

    def test_an_unpersisted_controller_identity_is_refused_not_invented(self) -> None:
        dt._identities = lambda: ("node-a", None)
        with self.assertRaises(dt.Refused) as refusal:
            dt.setting_state()
        self.assertEqual(refusal.exception.status, 503)
        self.assertEqual(refusal.exception.code, "release_unknown")
        status, body = dt._refusal(refusal.exception)
        self.assertEqual(status, 503)
        payload = json.loads(body)
        self.assertIsNone(payload["node_id"])
        self.assertIsNone(payload["controller_id"])

    def test_fleet_allowlist_has_exact_suffixes_and_node_allowlist_does_not(self) -> None:
        from components.hub.backend import auth
        paths = [
            ("GET", "/api/hub/fleet/nodes/mac-a/developer-tests/capabilities"),
            ("GET", "/api/hub/fleet/nodes/mac-a/developer-tests/setting"),
            ("POST", "/api/hub/fleet/nodes/mac-a/developer-tests/setting"),
            ("POST", "/api/hub/fleet/nodes/mac-a/developer-tests"),
            ("GET", "/api/hub/fleet/nodes/mac-a/developer-tests/by-key/key-1"),
            ("POST", "/api/hub/fleet/nodes/mac-a/developer-tests/by-key/key-1/cancel"),
            ("GET", "/api/hub/fleet/nodes/mac-a/developer-tests/by-key/key-1/receipt"),
            ("GET", "/api/hub/fleet/nodes/mac-a/developer-tests/by-key/key-1/log"),
        ]
        for method, path in paths:
            self.assertTrue(auth.fleet_path_allowed(method, path), path)
            self.assertFalse(auth.node_path_allowed(method, path), path)
        self.assertFalse(auth.fleet_path_allowed(
            "GET", "/api/hub/fleet/nodes/mac-a/developer-tests/by-key/key-1/../../setting"))

    def test_loopback_without_an_actor_is_the_owner_and_a_remote_is_not(self) -> None:
        local = SimpleNamespace(state=SimpleNamespace(), headers={}, cookies={},
                                client=SimpleNamespace(host="127.0.0.1"))
        remote = SimpleNamespace(state=SimpleNamespace(), headers={}, cookies={},
                                 client=SimpleNamespace(host="100.64.0.9"))
        self.assertEqual(dt._actor(local)["kind"], "hub")
        self.assertIsNone(dt._actor(remote)["kind"])

    def test_exact_replay_reconciles_after_off_and_actor_binding_conflicts(self) -> None:
        raw = json.dumps(_request()).encode()
        first = dt.accept_attempt(
            "key-1", raw, dt.validate_request(json.loads(raw)), "rev-1",
            actor={"kind": "fleet", "node_id": None}, destination="node-a")
        revision = self._setting()["revision"]
        dt.change_setting(_setting_body(revision, True, "on"))
        revision = self._setting()["revision"]
        dt.change_setting(_setting_body(revision, False, "off"))

        status, body = dt.submit(raw, "key-1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["attempt_id"], first["attempt_id"])

        with self.assertRaises(dt.Refused) as conflict:
            dt.accept_attempt(
                "key-1", raw, dt.validate_request(json.loads(raw)), "rev-1",
                actor={"kind": "node", "node_id": "node-a"}, destination="node-a")
        self.assertEqual(conflict.exception.code, "conflict")

    def test_loopback_with_a_supplied_node_credential_is_refused(self) -> None:
        from unittest.mock import patch
        from components.hub.backend import auth
        request = SimpleNamespace(state=SimpleNamespace(), headers={"X-Hub-Token": "node-token"}, cookies={},
                                  client=SimpleNamespace(host="127.0.0.1"))
        with patch.object(auth, "presented_token", return_value="node-token"), \
                patch.object(auth, "_node_actor",
                             return_value={"kind": "node", "node_id": "node-a"}):
            self.assertEqual(dt._actor(request)["kind"], "node")

    def test_routes_answer_the_fixed_suffixes_and_keep_the_error_shape(self) -> None:
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.middleware("http")
        async def actor(request: Request, call_next):
            request.state.fleet_actor = {"kind": "fleet", "node_id": None}
            return await call_next(request)

        old_ids = dt._local_ids
        dt._local_ids = lambda: ("local",)
        self.addCleanup(setattr, dt, "_local_ids", old_ids)
        dt.register_routes(app)
        client = TestClient(app)

        setting = client.get("/api/hub/fleet/nodes/local/developer-tests/setting")
        self.assertEqual(setting.status_code, 200)
        revision = setting.json()["revision"]

        changed = client.post(
            "/api/hub/fleet/nodes/local/developer-tests/setting",
            content=_setting_body(revision, True, "request-1"),
            headers={"Content-Type": "application/json"})
        self.assertEqual(changed.status_code, 200)

        refused = client.post(
            "/api/hub/fleet/nodes/local/developer-tests",
            content=json.dumps(_request(setting_revision=changed.json()["revision"])),
            headers={"Content-Type": "application/json", "Idempotency-Key": "key-1"})
        self.assertEqual(refused.status_code, 503)
        self.assertEqual(refused.json()["format"], dt.ERROR_FORMAT)
        self.assertEqual(refused.json()["reason"]["code"], "unsupported")
        self.assertEqual(refused.json()["acceptance"], "not_accepted")

        unknown = client.get("/api/hub/fleet/nodes/local/developer-tests/by-key/key-1")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["reason"]["code"], "unknown_key")

        capabilities = client.get(
            "/api/hub/fleet/nodes/local/developer-tests/capabilities")
        self.assertEqual(capabilities.status_code, 200)
        self.assertFalse(capabilities.json()["supported"])
        self.assertEqual(capabilities.json()["reason"]["code"], "unsupported")


if __name__ == "__main__":
    unittest.main()
