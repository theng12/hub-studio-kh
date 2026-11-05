"""The peer production door must request a test yield before returning busy."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from components.hub import standalone


class DeveloperNodeIntegrationTests(unittest.TestCase):
    def test_peer_production_requests_yield_before_busy_refusal(self):
        worker = {"machine": "local", "id": "image"}
        with patch("components.hub.backend.registry.machine_enabled", return_value=True), \
                patch("components.hub.backend.registry.studio_enabled", return_value=True), \
                patch.object(standalone.workstation, "admission_refusal", return_value=None), \
                patch.object(standalone.broker, "model_block_state", return_value=None), \
                patch.object(standalone.broker, "busy_machines", return_value={"local"}), \
                patch.object(standalone.broker, "note_developer_test_production") as arrived:
            refusal = standalone._peer_admission_refusal(worker, None, "")
        self.assertEqual(refusal["code"], "MACHINE_BUSY")
        arrived.assert_called_once_with("local")


if __name__ == "__main__":
    unittest.main()
