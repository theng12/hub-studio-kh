"""The unattended update path (releaseTick -> installOfferedRelease -> relaunch) never needs a person.

A Mac installed from a drive, with nobody at it, has to update itself. Anything in that path that
asks a question - a confirmation, a quit prompt, a "still stopping" dialog - waits for ever.
"""
import re
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1] / "native"


def _body(source: str, signature: str) -> str:
    start = source.index(signature)
    return source[start:start + 6000]


class NoDialogOnTheUnattendedPath(unittest.TestCase):
    def test_the_model_layer_shows_no_dialog(self):
        for name in ("StudioAPI.swift", "UpdateClient.swift"):
            source = (NATIVE / name).read_text()
            self.assertNotRegex(source, r"NSAlert|confirmationDialog|runModal", name)

    def test_the_manual_install_confirmation_stays_in_the_view_and_is_not_the_installer(self):
        view = (NATIVE / "StudioViews.swift").read_text()
        self.assertIn("confirmingInstall = true", view)   # the manual button asks first ...
        api = (NATIVE / "StudioAPI.swift").read_text()
        install = _body(api, "func installOfferedRelease(automatic: Bool = false)")
        self.assertNotIn("confirm", install.lower())      # ... the installer itself never does

    def test_a_relaunch_after_an_update_force_stops_a_hung_shutdown_without_asking(self):
        hub = (NATIVE / "StudioHub.swift").read_text()
        relaunch = _body(hub, "func relaunchAfterUpdate()")
        self.assertRegex(relaunch, r"forceStopWithoutAsking = true\s+NSApp\.terminate")
        force = _body(hub, "private func scheduleForceStop")
        self.assertRegex(force, r"if self\.forceStopWithoutAsking \{ self\.forceStop\(\) \} else \{ self\.confirmForceStop\(\) \}")

    def test_the_quit_prompt_is_skipped_while_relaunching(self):
        hub = (NATIVE / "StudioHub.swift").read_text()
        self.assertRegex(_body(hub, "func applicationShouldTerminate"), r"if !relaunching \{\s+let alert")


if __name__ == "__main__":
    unittest.main()
