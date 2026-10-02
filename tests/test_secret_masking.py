"""Every secret the Hub app shows or takes is masked until the eye is pressed (native/Design.swift).

The drawing itself is proved by tests/native/secret_checks.swift (in native/build.sh's lifecycle gate): two different
secrets draw identical pixels while masked. This pins that the screens use those pieces.
"""
import re
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1] / "native"


class SecretMaskingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.views = (NATIVE / "StudioViews.swift").read_text()
        self.design = (NATIVE / "Design.swift").read_text()

    def test_the_design_file_has_the_masked_pieces(self) -> None:
        for name in ("enum SecretMask", "struct RevealButton", "struct SecretValue", "struct SecretInput"):
            self.assertIn(name, self.design)
        self.assertIn('"eye.slash" : "eye"', self.design)

    def test_no_secret_is_typed_into_a_plain_or_bare_secure_field(self) -> None:
        self.assertNotIn("SecureField(", self.views, "use SecretInput, which adds the eye")
        for match in re.finditer(r'TextField\("([^"]*)"', self.views):
            self.assertNotRegex(match.group(1).lower(), r"token|password|api key|secret",
                                f"secret typed into a plain field: {match.group(0)}")

    def test_every_token_shown_is_a_secret_value(self) -> None:
        for name in ("studio.fleetToken", "studio.connectionToken"):
            self.assertIn(f"SecretValue(value: {name}", self.views)
        self.assertIn("SecretValue(value: token)", self.views, "a rotated token is masked too")
        self.assertNotRegex(self.views, r"Text\((token|studio\.fleetToken|studio\.connectionToken)\)")


if __name__ == "__main__":
    unittest.main()
