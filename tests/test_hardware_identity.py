"""Host identity must not depend on a login shell's PATH.

The packaged Hub is started by launchd, whose PATH is not a user's. `sysctl` and
`system_profiler` live in `/usr/sbin`, so invoking them by bare name made the
chip and machine type unknown in exactly the environment the app runs in - and
the fleet overview then reported a Mac with no chip.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "darwin", "macOS hardware identity")
class HostIdentityTests(unittest.TestCase):
    def test_the_chip_is_reported_without_usr_sbin_on_path(self) -> None:
        from components.hub.backend import resources

        resources.apple_chip_name.cache_clear()
        self.addCleanup(resources.apple_chip_name.cache_clear)
        with patch.dict(os.environ, {"PATH": "/nonexistent"}, clear=False):
            value = resources.apple_chip_name()
        self.assertTrue(value, "the chip must not require sysctl to be on PATH")

    def test_the_machine_type_is_reported_without_usr_sbin_on_path(self) -> None:
        from components.hub.backend import resources

        resources.apple_machine_type.cache_clear()
        self.addCleanup(resources.apple_machine_type.cache_clear)
        with patch.dict(os.environ, {"PATH": "/nonexistent"}, clear=False):
            value = resources.apple_machine_type()
        self.assertTrue(value, "the machine type must not require system_profiler on PATH")


if __name__ == "__main__":
    unittest.main()
