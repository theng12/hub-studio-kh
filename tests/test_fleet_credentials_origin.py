"""A controller must run on its own credential, never on the one it was a node under.

Fifty Macs are being split across five controllers, each with nine or ten nodes, and each controller
mints its own fleet credential - that is how joining works: the app shows a controller's fleet token,
the operator types it into the joining Mac, and the Mac adopts it. The credential is what a node
checks before it will accept work, so it is also what makes one controller's group separate from
another's.

The gap was promotion. A machine that was a node holds its controller's credential, and changing its
role to `controller` left it untouched - measured on 2026-09-18, the fleet token was byte-identical
across a PUT role=controller and a PUT role=standalone. Promoting one of the owner's machines would
therefore have left two independent groups sharing one secret: the new controller, and the old
controller with every node under it. Losing or selling one Mac would have meant re-keying both
groups, which is the containment the five-way split exists to provide.

These tests pin both directions: an adopted credential is replaced on promotion, and a credential
this Hub minted is left alone - a role change must never invalidate a group that is already running.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FleetCredentialTests(unittest.TestCase):
    def setUp(self):
        # Both modules bind their paths at import time, so the attributes are redirected rather
        # than the environment - the same way the fleet roster tests do it.
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control_plane = importlib.import_module("components.hub.backend.control_plane")
        self.peers = importlib.import_module("components.hub.backend.peers")

        self._old_settings = self.control_plane.SETTINGS_FILE
        self._old_cache = self.control_plane._settings_cache
        self._old_fleet = self.peers.FLEET_TOKEN_FILE
        self._old_shared = self.peers.SHARED_STUDIO_TOKEN_FILE
        self._old_peer_cache = self.peers._cache

        self.control_plane.SETTINGS_FILE = self.root / "controller_settings.json"
        self.control_plane._settings_cache = None
        self.peers.FLEET_TOKEN_FILE = self.root / ".fleet_token"
        self.peers.SHARED_STUDIO_TOKEN_FILE = self.root / ".kh_studio_token"
        self.peers._cache = {}

    def tearDown(self):
        self.control_plane.SETTINGS_FILE = self._old_settings
        self.control_plane._settings_cache = self._old_cache
        self.peers.FLEET_TOKEN_FILE = self._old_fleet
        self.peers.SHARED_STUDIO_TOKEN_FILE = self._old_shared
        self.peers._cache = self._old_peer_cache
        self.temporary.cleanup()

    def _token(self) -> str:
        return self.peers.FLEET_TOKEN_FILE.read_text().strip()

    def test_a_machine_that_joined_a_fleet_replaces_that_credential_on_promotion(self):
        controllers = "controller-credential-aaaaaaaa"
        self.peers.set_fleet_token(controllers)                       # what joining does
        self.control_plane.save_settings({"role": "agent",
                                          "fleet_credential_origin": "adopted"})
        self.assertEqual(self._token(), controllers)

        self.control_plane.save_settings({"role": "controller"})

        self.assertNotEqual(self._token(), controllers,
                            "a promoted node kept the credential of the group it left")
        self.assertEqual(self.control_plane.load_settings()["fleet_credential_origin"], "minted")

    def test_a_credential_this_hub_minted_survives_a_role_change(self):
        """A role change must never invalidate a group that is already running."""
        self.peers.set_fleet_token("")                                 # what a fresh Hub does
        self.control_plane.save_settings({"role": "standalone",
                                          "fleet_credential_origin": "minted"})
        mine = self._token()

        self.control_plane.save_settings({"role": "controller"})
        self.assertEqual(self._token(), mine, "a controller's own credential was rotated for nothing")

        self.control_plane.save_settings({"role": "standalone"})
        self.assertEqual(self._token(), mine)

    def test_absent_origin_is_treated_as_this_hubs_own(self):
        """Settings written before the field existed must not cause a silent rotation."""
        self.peers.set_fleet_token("")
        self.control_plane.save_settings({"role": "controller"})
        saved = json.loads(self.control_plane.SETTINGS_FILE.read_text())
        saved.pop("fleet_credential_origin", None)
        self.control_plane.SETTINGS_FILE.write_text(json.dumps(saved))
        self.control_plane._settings_cache = None
        mine = self._token()

        self.control_plane.save_settings({"role": "standalone"})

        self.assertEqual(self._token(), mine)

    def test_joining_records_that_the_credential_is_not_ours(self):
        """The promotion rule can only fire if adoption is written down where it happens."""
        source = (ROOT / "components" / "hub" / "standalone.py").read_text()
        adopt = source[source.index("def fleet_adopt("):]
        adopt = adopt[:adopt.index("@app.post(\"/api/hub/fleet/nodes\"")]
        self.assertIn('"fleet_credential_origin": "adopted"', adopt)


if __name__ == "__main__":
    unittest.main()
