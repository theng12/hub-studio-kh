"""Best negotiation stays unsupported until the complete evidence contract is qualified."""
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from components.hub.backend import auth, fleet_nodes, render_output_settings, render_service

PATH = "/api/hub/final-renders/output-settings/capabilities"
RELAY_PATH = "/api/hub/fleet/nodes/node-a/final-renders/output-settings/capabilities"
HEADERS = {"x-hub-token": "hub-secret"}
FLEET_HEADERS = {"x-hub-token": "fleet-secret"}
SETTINGS = lambda: {"site_id": "site-a", "controller_id": "controller-b"}


def app():
    application = FastAPI()
    render_output_settings.register_routes(application, hub_token="hub-secret",
                                          fleet_token=lambda: "fleet-secret", settings=SETTINGS)
    return application


class OutputSettingsTests(unittest.TestCase):
    def test_capabilities_are_closed_unsupported_with_null_profiles(self):
        response = TestClient(app()).get(PATH, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"format", "private", "chargeable", "supported", "reason",
                                     "profile_descriptor_utf8", "profile_digest", "server_time"})
        self.assertEqual(body["format"], render_output_settings.CAPABILITIES_FORMAT)
        self.assertIs(body["private"], True)
        self.assertIs(body["chargeable"], False)
        self.assertIs(body["supported"], False)
        self.assertTrue(body["reason"])
        self.assertIsNone(body["profile_descriptor_utf8"])
        self.assertIsNone(body["profile_digest"])
        self.assertGreater(body["server_time"], 0)

    def test_existing_credentials_and_fleet_path_fence(self):
        client = TestClient(app())
        self.assertEqual(client.get(PATH).status_code, 401)
        self.assertEqual(client.get(PATH, headers={"x-hub-token": "wrong"}).status_code, 401)
        self.assertEqual(client.get(PATH, headers=FLEET_HEADERS).status_code, 200)
        self.assertEqual(client.get(PATH, headers={**HEADERS, "authorization": "Bearer x"}).status_code, 403)
        self.assertTrue(auth.fleet_path_allowed("GET", PATH))
        self.assertTrue(auth.fleet_path_allowed("GET", RELAY_PATH))
        self.assertFalse(auth.fleet_path_allowed("POST", PATH))

    def test_no_selection_store_or_measurement_is_offered(self):
        client = TestClient(app())
        self.assertEqual(client.post(PATH.rsplit("/", 1)[0] + "/selections", json={},
                                     headers=HEADERS).status_code, 404)
        self.assertEqual(client.get(PATH.rsplit("/", 1)[0] + "/measurements/" + "a" * 64,
                                    headers=HEADERS).status_code, 404)

    def test_controller_relay_uses_the_fixed_node_route(self):
        application = app()
        seen = []
        node_app = TestClient(app())
        expected = node_app.get(PATH, headers=FLEET_HEADERS).json()

        def transport(request):
            seen.append((request.method, request.url.path))
            return httpx.Response(200, json=expected)

        async def relay(node, operation, **kwargs):
            with patch.object(fleet_nodes, "_read", return_value={"node-a": {"address": "http://node.invalid"}}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    return await fleet_nodes.relay_final_render(node, operation, client=client, **kwargs)

        render_service.register_relay_routes(application, relay, hub_token="hub-secret",
                                            fleet_token=lambda: "fleet-secret", settings=SETTINGS)
        client = TestClient(application)
        self.assertEqual(client.get(RELAY_PATH).status_code, 401)
        self.assertEqual(seen, [])
        answer = client.get(RELAY_PATH, headers=FLEET_HEADERS)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json(), expected)
        self.assertEqual(seen, [("GET", PATH)])
        with self.assertRaises(ValueError):
            fleet_nodes._render_relay_segment("output_settings_capabilities", "other/path")


if __name__ == "__main__":
    unittest.main()
