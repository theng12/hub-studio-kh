"""Exact probed grants remain separate from measured qualification and revocation."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_render_opt_in import OptInFixture
from components.hub.backend import render_encode
from components.hub.backend.render_hash_v1 import render_hash
from components.hub.backend.render_jobs import ValidatedOutputReceipt


class ProbedAuthorityTests(OptInFixture):
    def setUp(self):
        super().setUp()
        self.loader.physical_node_id = lambda: "node-01"
        self.encode = dict(render_encode.DEFAULT_OPT_IN_ENCODE,
                           requested_effort="hardware", bitrate_kbps=8000)
        tier, encoder = render_encode.primary_pair(self.encode)
        self.state = {
            "state": "supported", "requested_encode": self.encode,
            "authority_digest": "a" * 64,
            "evidence": {"selected_tier": tier, "exact_encoder": encoder},
        }
        self.loader.encode_probe_control = SimpleNamespace(
            inspect=lambda encode: self.state if encode == self.encode else {
                "state": "unmeasured", "authority_digest": None},
            supported_entries=lambda: [self.state],
        )

    def packet(self):
        packet = json.loads(render_encode._synthetic_packet(self.encode))
        packet["recipe"].update({
            "profile_digest": hashlib.sha256(self.profile_raw).hexdigest(),
            "canvas": {"width": 128, "height": 96, "working_fps": 5},
        })
        packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"],
        })
        return packet

    def test_hardware_cap_requires_successful_probe_and_grant(self):
        self.opt_in()
        self.assertIsNone(self.loader.authority_for_packet(self.packet()))
        original = (self.data_root / "render-authority/opt-in/unqualified.json").read_bytes()
        self.loader.migrate_probed_grant()
        authority = self.loader.authority_for_packet(self.packet())
        self.assertIsNotNone(authority)
        document = authority.qualification.document
        self.assertEqual(document["format"], self.loader._PROBED_FORMAT)
        self.assertEqual(document["requested_encode"], self.encode)
        self.assertEqual(document["encode_authority"]["authority_digest"], "a" * 64)
        self.assertNotEqual(authority.qualification.qualification_digest, "a" * 64)
        self.assertNotIn("measured", document)
        self.assertEqual(original, (
            self.data_root / "render-authority/opt-in/unqualified.json").read_bytes())
        self.assertEqual(self.loader.opt_in_state()["grant_version"], "v2")
        self.assertEqual(self.loader.opt_in_state()["granted_encodes"], [self.encode])

    def test_legacy_grant_write_cannot_bypass_the_exact_cap_probe(self):
        self.opt_in()
        path = self.data_root / "render-authority/opt-in/unqualified.json"
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "exact successful probe"):
            self.loader.set_opt_in(True, "owner", self.encode)
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(self.loader.authority_for_packet(self.packet()))

    def test_off_stays_off_without_a_v2_file(self):
        self.loader.migrate_probed_grant()
        self.assertIsNone(self.loader.authority_for_packet(self.packet()))
        self.assertFalse((self.data_root / "render-authority/opt-in/probed-v2.json").exists())

    def test_revocation_refuses_new_work_but_preserves_claimed_document(self):
        self.opt_in()
        self.loader.migrate_probed_grant()
        authority = self.loader.authority_for_packet(self.packet())
        self.loader.set_opt_in(False, "owner")
        self.assertIsNone(self.loader.authority_for_packet(self.packet()))
        recovered = self.loader.authority_for({"qualification": authority.qualification})
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.qualification.document, authority.qualification.document)

    def test_changed_probe_identity_withdraws_new_admission(self):
        self.opt_in()
        self.loader.migrate_probed_grant()
        self.state["authority_digest"] = "b" * 64
        self.assertIsNone(self.loader.authority_for_packet(self.packet()))

    def test_probe_change_between_listing_and_binding_refuses_authority(self):
        self.opt_in()
        self.loader.migrate_probed_grant()
        self.loader.executable_probe_entries = lambda: [{
            "requested_encode": self.encode, "authority_digest": "a" * 64}]
        self.loader.encode_probe_control.inspect = lambda encode: {
            "state": "unmeasured", "authority_digest": None, "evidence": None}
        self.assertIsNone(self.loader.authority_for_packet(self.packet()))

    def test_hevc_capability_does_not_become_phase_one_render_permission(self):
        self.opt_in()
        self.state["requested_encode"] = dict(self.encode, requested_codec="h265")
        self.loader.migrate_probed_grant()
        self.assertIsNone(self.loader.probed_grant())

    def test_matching_measured_record_keeps_its_exact_qualification_digest(self):
        from components.hub.backend.render_executor import InstalledRenderAuthorityLoader
        from test_render_qualification_receipt import INTENT_ID, HARDWARE_CLASS
        self.opt_in()
        self.loader.migrate_probed_grant()
        document = dict(self.document,
                        selected_tier=self.state["evidence"]["selected_tier"],
                        exact_encoder=self.state["evidence"]["exact_encoder"])
        document["qualification_digest"] = InstalledRenderAuthorityLoader._record_digest(document)
        document.update(intent_id=INTENT_ID, measured=self.measured())
        self.write_record("enabled", f"{HARDWARE_CLASS}.json", value=document)
        authority = self.loader.authority_for_packet(self.packet())
        self.assertIsNotNone(authority)
        self.assertEqual(authority.qualification.qualification_digest, document["qualification_digest"])
        self.assertEqual(authority.qualification.document["qualified_document"], document)
        self.assertNotIn("opt_in", authority.qualification.document)
        self.assertEqual(self.loader.resolve(self.packet()).document,
                         authority.qualification.document)

    def test_exact_cap_validation_rejects_bool_and_out_of_range(self):
        for cap in (True, 499, 100001, "8000"):
            self.assertIsNone(render_encode.normalize_encode(dict(self.encode, bitrate_kbps=cap)))
        self.assertEqual(render_encode.normalize_encode(self.encode), self.encode)

    def test_receipt_binding_must_name_the_actual_selected_tier(self):
        fields = dict(
            owner_scope="site:controller:fleet", hub_job_id="job", attempt_id="attempt",
            accepted_request_sha256="1" * 64, receipt_digest="2" * 64,
            output_sha256="3" * 64, output_size_bytes=1, profile_digest="4" * 64,
            tools_digest="5" * 64, compiler_digest="6" * 64,
            qualification_digest="7" * 64, generated_command_digest="8" * 64,
            selected_tier="actual-tier", feature_losses=(),
            encode_authority={"format": "studio.render.encode-binding.v1",
                              "authority_digest": "9" * 64,
                              "selected_tier": "other-tier", "exact_encoder": "libx264"})
        with self.assertRaises(ValueError):
            ValidatedOutputReceipt(**fields)
