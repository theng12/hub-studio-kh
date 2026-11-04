#!/usr/bin/env python3
"""Encode probes run with execution off: installed tools only, and enable nothing.

A probe must be able to run on a Mac that has never been granted production rendering,
because probing is what produces the evidence an owner's later enable acts on. These
pin the three seams of that bootstrap: an off Mac probes with its installed descriptors
(never a fabricated authority), a successful probe alone cannot enable the Mac, and a
tuple the pinned compiler refuses stays off with a reason.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_render_opt_in import HARDWARE_CLASS, OptInFixture  # noqa: E402
from test_render_encode_probes import ENCODE, FakeExecutor, FakeRunner, NODE  # noqa: E402
from components.hub.backend.render_encode_probes import EncodeProbeControl  # noqa: E402


class OffNodeProbeBootstrapTests(OptInFixture):
    """Probing with the owner's opt-in off, on the real installed-tools fixture."""

    def setUp(self) -> None:
        super().setUp()
        self.runner = FakeRunner()
        self.control = EncodeProbeControl(
            self.data_root, loader=self.loader, executor=FakeExecutor(),
            node_id=lambda: NODE,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
            admission_allowed=lambda: True, runner=self.runner,
            expected_uid=os.getuid(), clock=lambda: 1_000,
            scratch_root=self.root / "scratch")
        # The loader reads this node's probes through the control it owns.
        self.loader.encode_probe_control = self.control

    def probe(self, encode=None):
        return asyncio.run(self.control.probe(encode or ENCODE))

    def installed_digest(self, name: str) -> str:
        return hashlib.sha256((self.tools_root / name).read_bytes()).hexdigest()

    def test_an_off_node_probes_with_the_installed_descriptors(self):
        self.assertIsNone(self.loader.snapshot(), "the fixture starts with no grant")
        state = self.probe()
        self.assertEqual(state["state"], "supported")
        evidence = state["evidence"]
        # The identity names the descriptors that are actually installed, not a
        # fabricated authority: exactly the digests a measured authority would carry.
        self.assertEqual(evidence["profile_digest"], self.installed_digest("PROFILE.json"))
        self.assertEqual(evidence["tools_digest"], self.installed_digest("TOOLS.json"))
        self.assertEqual(evidence["compiler_digest"], self.installed_digest("COMPILER.json"))
        self.assertEqual(evidence["hardware_identity"]["hardware_class"], HARDWARE_CLASS)
        self.assertEqual(evidence["requested_encode"], ENCODE)
        self.assertEqual(self.runner.calls and len(self.runner.calls), 2,
                         "one encode and one ffprobe")

    def test_a_successful_off_node_probe_alone_enables_nothing(self):
        state = self.probe()
        self.assertEqual(state["state"], "supported")
        authority = self.loader.probe_authority()
        self.assertIsNotNone(authority)
        # Installed-only: it carries no opt-in evidence and is never production.
        self.assertIsNone(authority.opt_in)
        self.assertIs(authority.qualification.production_eligible, False)
        # A successful probe writes probes, never a grant: still off, still unexecutable.
        self.assertIsNone(self.loader.snapshot())
        self.assertFalse((self.data_root / "render-authority/opt-in").exists())
        self.assertFalse((self.data_root / "render-authority/enabled").exists())
        self.assertIsNone(self.loader.execution_for_encode(ENCODE))
        self.assertFalse(self.loader.opt_in_state()["accept_unqualified"])

    def test_a_refused_tuple_on_an_off_node_stays_off_with_a_reason(self):
        state = self.probe({**ENCODE, "crf": 99})
        self.assertEqual(state["state"], "unsupported")
        self.assertTrue(state["reason"])
        self.assertEqual(self.runner.calls, [], "a refused tuple never runs a process")
        self.assertIsNone(self.loader.snapshot())
        self.assertFalse(self.loader.opt_in_state()["accept_unqualified"])


if __name__ == "__main__":
    import unittest
    unittest.main()
