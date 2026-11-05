"""Probe control is explicit, authenticated and closed; discovery cannot run work."""
from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from components.hub.backend.render_encode import DEFAULT_OPT_IN_ENCODE
from components.hub.backend.render_service import RenderService, register_routes
from components.hub.backend import auth


class ProbeRoutesTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.probe_attempts = []
        self.encode = dict(DEFAULT_OPT_IN_ENCODE, requested_effort="hardware", bitrate_kbps=8000)
        state = lambda encode: {"format": "studio.render.encode-probe-state.v1",
                                "requested_encode": encode, "state": "supported",
                                "probe_key": "a" * 64, "reason": None,
                                "evidence": {}, "authority_digest": "b" * 64}

        async def probe(encode, attempt=None):
            self.events.append("probe")
            self.probe_attempts.append(attempt)
            return state(encode)

        control = SimpleNamespace(inspect=state, probe=probe)
        loader = SimpleNamespace(encode_probe_control=control,
                                 migrate_probed_grant=lambda: self.events.append("migrate"))
        self.loader = loader
        app = FastAPI()
        credential = "probe-route-fixture"
        register_routes(app, SimpleNamespace(authority_loader=loader, executor=None),
                        hub_token=credential, fleet_token=lambda: "fleet-token",
                        settings=lambda: {"site_id": "site", "controller_id": "controller"})
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.path = "/api/hub/final-renders/encode-probes"
        self.headers = {"X-Hub-Token": credential}

    def test_get_is_read_only_and_exact_cap_is_preserved(self):
        response = self.client.get(self.path, headers=self.headers,
                                   params={"requested_encode": json.dumps(self.encode)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["requested_encode"], self.encode)
        self.assertEqual(self.events, [])

    def test_discovery_adds_only_current_execution_mapping_without_writes(self):
        mapping = {"encode_authority": {"authority_digest": "b" * 64}}
        self.loader.execution_for_encode = lambda encode: mapping
        response = self.client.get(self.path, headers=self.headers,
                                   params={"requested_encode": json.dumps(self.encode)})
        self.assertEqual(response.json()["execution_authority"], mapping)
        mapping["encode_authority"]["authority_digest"] = "c" * 64
        response = self.client.get(self.path, headers=self.headers,
                                   params={"requested_encode": json.dumps(self.encode)})
        self.assertIsNone(response.json()["execution_authority"])
        self.assertEqual(self.events, [])

    def test_v2_preflight_never_uses_a_qualification_candidate(self):
        service = SimpleNamespace(authority_loader=self.loader)
        self.assertEqual(RenderService._admission_authority(
            service, "qualification-intent", {}, {"authority_digest": "b" * 64}), (True, None))

    def test_explicit_post_probes_before_grant_migration(self):
        response = self.client.post(self.path, headers=self.headers, json={
            "format": "studio.render.encode-probe-request.v1", "requested_encode": self.encode})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events, ["probe", "migrate"])
        self.assertEqual(self.probe_attempts, [None])

    def test_a_deliberate_attempt_identity_is_forwarded_to_the_control(self):
        response = self.client.post(self.path, headers=self.headers, json={
            "format": "studio.render.encode-probe-request.v1",
            "requested_encode": self.encode, "attempt": "retry-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events, ["probe", "migrate"])
        self.assertEqual(self.probe_attempts, ["retry-1"])

    def test_a_malformed_attempt_identity_is_refused_before_the_control(self):
        for attempt in ("", None, "x" * 65, 7, "line\nbreak"):
            with self.subTest(attempt=attempt):
                response = self.client.post(self.path, headers=self.headers, json={
                    "format": "studio.render.encode-probe-request.v1",
                    "requested_encode": self.encode, "attempt": attempt})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.events, [])
        self.assertEqual(self.probe_attempts, [])

    def test_auth_and_duplicate_query_fail_before_control(self):
        self.assertEqual(self.client.post(self.path, json={}).status_code, 401)
        self.assertEqual(self.client.post(self.path, json={
            "format": "studio.render.encode-probe-request.v1",
            "requested_encode": self.encode, "attempt": "retry-1"}).status_code, 401)
        duplicate = [("requested_encode", json.dumps(self.encode))] * 2
        self.assertEqual(self.client.get(self.path, headers=self.headers,
                                         params=duplicate).status_code, 400)
        self.assertEqual(self.events, [])

    def test_untrusted_commands_and_boolean_caps_are_rejected(self):
        for encode in (dict(self.encode, bitrate_kbps=True), dict(self.encode, command="run")):
            response = self.client.post(self.path, headers=self.headers, json={
                "format": "studio.render.encode-probe-request.v1", "requested_encode": encode})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.events, [])

    def test_fleet_may_control_probes_but_a_leaf_may_not(self):
        for path in (self.path, "/api/hub/fleet/nodes/leaf/final-renders/encode-probes"):
            self.assertTrue(auth.fleet_path_allowed("POST", path))
            self.assertFalse(auth.node_path_allowed("POST", path))
