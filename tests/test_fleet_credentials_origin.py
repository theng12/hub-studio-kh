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

    def test_adoption_is_persisted_where_the_app_reads_it(self):
        """The app re-joins on start by reading this field, so it has to survive a settings write.

        `save_settings` builds the file from an explicit list of keys. `fleet_credential_origin` was
        not on it, so `fleet_adopt`'s write was dropped, the in-memory cache was seeded without the
        key, and every reader was told "minted" - a node could never look like a node. The
        automatic re-join only fires for an adopted credential, so this one omission made the whole
        re-join inert while the app and the Hub both looked correct.
        """
        self.peers.set_fleet_token("")
        self.control_plane.save_settings({"fleet_credential_origin": "adopted"})
        self.assertEqual(self.control_plane.load_settings()["fleet_credential_origin"], "adopted",
                         "the cache lost the origin")
        on_disk = json.loads(self.control_plane.SETTINGS_FILE.read_text())
        self.assertEqual(on_disk["fleet_credential_origin"], "adopted",
                         "the origin must be on disk, not only in memory")

    def test_every_default_survives_a_settings_write(self):
        """The shape of the omission above: a key in `defaults()` missing from `save_settings`.

        Written as a rule rather than an example, because the next key added to `defaults()` will
        be dropped the same silent way.
        """
        self.control_plane.save_settings({"role": "standalone"})
        on_disk = json.loads(self.control_plane.SETTINGS_FILE.read_text())
        missing = sorted(set(self.control_plane.defaults()) - set(on_disk))
        self.assertEqual(missing, [], f"save_settings dropped defaults: {missing}")

    def test_an_unknown_origin_is_refused_rather_than_stored(self):
        with self.assertRaises(ValueError):
            self.control_plane.save_settings({"fleet_credential_origin": "borrowed"})

    def test_adoption_alone_forces_a_rotation_on_promotion(self):
        """Containment must not depend on an operator having set the role by hand.

        The promotion rule fires for an adopted credential *or* an agent role. Until the origin was
        persisted, only the role arm could ever be true - so a Mac that joined a group and was then
        promoted while still labelled `standalone` would have kept the group's credential, and two
        groups would have shared one secret. The app's own instructions have the operator set the
        role, which is exactly why this has to hold without that click.
        """
        borrowed = "controller-credential-aaaaaaaa"
        self.peers.set_fleet_token(borrowed)
        self.control_plane.save_settings({"role": "standalone",
                                          "fleet_credential_origin": "adopted"})
        self.assertEqual(self._token(), borrowed)

        self.control_plane.save_settings({"role": "controller"})

        self.assertNotEqual(self._token(), borrowed,
                            "a promoted node kept the credential of the group it left")
        self.assertEqual(self.control_plane.load_settings()["fleet_credential_origin"], "minted")

    def test_joining_records_that_the_credential_is_not_ours(self):
        """The promotion rule can only fire if adoption is written down where it happens."""
        source = (ROOT / "components" / "hub" / "standalone.py").read_text()
        adopt = source[source.index("def fleet_adopt("):]
        adopt = adopt[:adopt.index("@app.post(\"/api/hub/fleet/nodes\"")]
        self.assertIn('"fleet_credential_origin": "adopted"', adopt)

    def test_leaving_mints_a_fresh_credential(self):
        """The half of leaving that only the Hub can do, pinned at the source.

        The behaviour is driven end to end in `tests/test_standalone_connections.py`, against two
        live Hubs. This pins the thing that is easy to lose in a refactor: leaving must *mint*, not
        merely forget, because a Mac that is being sold must not keep the group's token in memory
        or on disk.
        """
        source = (ROOT / "components" / "hub" / "standalone.py").read_text()
        leave = source[source.index("def fleet_leave("):]
        leave = leave[:leave.index("@app.post(\"/api/hub/fleet/nodes\"")]
        self.assertIn('peers.set_fleet_token("")', leave, "leaving has to mint, not merely forget")
        self.assertIn('"fleet_credential_origin": "minted"', leave)
        self.assertIn('"role": "standalone"', leave,
                      "a Mac that left must be able to take work on its own again")

    def test_leaving_needs_the_local_hub_token_not_the_groups(self):
        """A controller must not be able to un-join a node: it cannot remove another owner's Mac.

        The route is outside the fleet credential's allowlist for the same reason adopting is -
        both change which group this Mac answers to, and that is the Mac owner's decision.
        """
        source = (ROOT / "components" / "hub" / "backend" / "auth.py").read_text()
        self.assertNotIn("/api/hub/fleet/leave", source,
                         "leaving must not be callable with the borrowed fleet credential")
        self.assertNotIn("/api/hub/fleet/adopt", source)


if __name__ == "__main__":
    unittest.main()
