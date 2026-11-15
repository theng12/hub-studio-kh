"""The Hub app must not be auto-terminated when it has no window (2026-11-15).

A login launch opens no window. AppKit automatically terminated the windowless app after ~8
minutes on 2026-11-15. The opt-out counter only has effect when automatic termination support
is enabled, so the bundle declares support true and the app calls
`disableAutomaticTermination` at launch; declaring support false made the opt-out a no-op
(observed 2026-11-15). The login agent's KeepAlive is `SuccessfulExit: false`, so a clean exit
would not be restarted — the app would simply vanish. These checks pin the declaration, the
runtime opt-out and the agent behaviour; the long-idle login-agent survival run is recorded in
the release evidence.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class AutomaticTerminationTests(unittest.TestCase):
    def test_staging_declares_support_and_the_app_opts_out_at_launch(self):
        source = (ROOT / "packaging/stage_voice.py").read_text()
        self.assertIn("'NSSupportsAutomaticTermination': True", source)
        app = (ROOT / "native/StudioHub.swift").read_text()
        self.assertIn("disableAutomaticTermination(", app)

    def test_login_agent_does_not_restart_a_clean_exit(self):
        source = (ROOT / "native/ShellCore.swift").read_text()
        self.assertIn('"KeepAlive": ["SuccessfulExit": false]', source)

    def test_a_staged_app_can_be_checked_when_the_gate_provides_one(self):
        staged = os.environ.get("STUDIOHUB_STAGED_APP", "").strip()
        if not staged:
            self.skipTest("no staged app supplied; the source declaration is covered above")
        plist = plistlib.loads((Path(staged) / "Contents/Info.plist").read_bytes())
        self.assertIs(plist.get("NSSupportsAutomaticTermination"), True)


if __name__ == "__main__":
    unittest.main()
