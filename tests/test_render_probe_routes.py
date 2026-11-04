"""Probe control is explicit, authenticated and closed; discovery cannot run work."""
from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from components.hub.backend.render_encode import DEFAULT_OPT_IN_ENCODE
from components.hub.backend.render_service import register_routes
from components.hub.backend import auth


class ProbeRoutesTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.encode = dict(DEFAULT_OPT_IN_ENCODE, requested_effort="hardware", bitrate_kbps=8000)
        state = lambda encode: {"format": "studio.render.encode-probe-state.v1",
                                "requested_encode": encode, "state": "supported",
                                "probe_key": "a" * 64, "reason": None,
                                "evidence": {}, "authority_digest": "b" * 64}

        async def probe(encode):
            self.events.append("probe")
            return state(encode)

        control = SimpleNamespace(inspect=state, probe=probe)
        loader = SimpleNamespace(encode_probe_control=control,
                                 migrate_probed_grant=lambda: self.events.append("migrate"))
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

    def test_explicit_post_probes_before_grant_migration(self):
        response = self.client.post(self.path, headers=self.headers, json={
            "format": "studio.render.encode-probe-request.v1", "requested_encode": self.encode})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events, ["probe", "migrate"])

    def test_auth_and_duplicate_query_fail_before_control(self):
        self.assertEqual(self.client.post(self.path, json={}).status_code, 401)
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
