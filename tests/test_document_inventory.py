"""The catalog's own numbers, as the documents state them, measured rather than maintained.

The documents a person reads before adding a model - and the ones that ship on the drive - state
facts about the shipped catalog: how many packages it holds, how many bytes they carry, and which
engines exist. Every one of those was wrong when this test was written: the manifests README said
37 packages and 89,369,563,824 bytes against a measured 24 and 70,529,384,759,
`docs/runtime-contract.md` still named three engines while six ship, and the kit readmes said 19,
12 and 17 packages. A stale count in a document is a hardcoded model list with the same failure
mode as one in code: it is believed, so the next model is added against a picture that is no
longer true.

So the documents state the numbers and this test recomputes them from the manifests and the Hub's
own engine table. It asserts the *rule* - the document agrees with the catalog - not a membership
list, so adding a model means updating the sentence, and forgetting to is a failure here instead of
a wrong answer six months later.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "model-manifests"
README = CATALOG / "README.md"
RUNTIME_CONTRACT = ROOT / "docs" / "runtime-contract.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import standalone  # noqa: E402


def manifests() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(CATALOG.glob("*.json"))}


def measured_packages() -> int:
    return len(manifests())


def measured_bytes() -> int:
    return sum(file["size"] for manifest in manifests().values() for file in manifest["files"])


def kit_manifests() -> dict[str, dict]:
    """Packages ordinary kit setup may copy; pending qualification stays on the SSD."""
    return {package_id: manifest for package_id, manifest in manifests().items()
            if manifest.get("production_allowed") is not False}


def measured_kit_packages() -> int:
    return len(kit_manifests())


def measured_kit_bytes() -> int:
    return sum(file["size"] for manifest in kit_manifests().values()
               for file in manifest["files"])


class ManifestReadmeNumbersTests(unittest.TestCase):
    """`model-manifests/README.md` is where a person adding a model looks first."""

    def test_the_readme_states_the_measured_package_count_and_byte_total(self) -> None:
        text = README.read_text(encoding="utf-8")
        count = re.search(r"This catalog contains \*\*(\d+)\*\* bounded model packages", text)
        self.assertIsNotNone(
            count, "the README no longer states the package count in the measured form "
                   "'This catalog contains **<n>** bounded model packages'")
        self.assertEqual(
            int(count.group(1)), measured_packages(),
            "the README's package count disagrees with model-manifests/*.json; update the "
            "sentence in model-manifests/README.md")
        total = re.search(r"\*\*([\d,]+) expected bytes\*\*", text)
        self.assertIsNotNone(
            total, "the README no longer states the byte total in the measured form "
                   "'**<n> expected bytes**'")
        self.assertEqual(
            int(total.group(1).replace(",", "")), measured_bytes(),
            "the README's byte total disagrees with the sum of the manifests' file sizes; "
            "update the sentence in model-manifests/README.md")


class RuntimeContractVocabularyTests(unittest.TestCase):
    """`docs/runtime-contract.md` states the catalog size and the engine vocabulary."""

    def test_the_contract_states_the_measured_package_count(self) -> None:
        text = RUNTIME_CONTRACT.read_text(encoding="utf-8")
        count = re.search(r"\[Model catalog\]\(\.\./model-manifests/\): (\d+) exact packages", text)
        self.assertIsNotNone(
            count, "docs/runtime-contract.md no longer states the catalog size in the measured "
                   "form '[Model catalog](../model-manifests/): <n> exact packages'")
        self.assertEqual(
            int(count.group(1)), measured_packages(),
            "docs/runtime-contract.md's catalog size disagrees with model-manifests/*.json")

    def test_the_contract_names_every_engine_and_no_other(self) -> None:
        """Read from the Hub's own engine table, so a registered engine is covered by existing."""
        text = RUNTIME_CONTRACT.read_text(encoding="utf-8")
        row = re.search(r"\| engine \| The engines the catalog ships: ([^|]+)\|", text)
        self.assertIsNotNone(
            row, "docs/runtime-contract.md no longer lists the engines in the measured form "
                 "'| engine | The engines the catalog ships: a, b, c |'")
        documented = {name.strip() for name in row.group(1).split(",") if name.strip()}
        self.assertEqual(
            documented, set(standalone._ENGINE_ROUTE),
            "docs/runtime-contract.md and the Hub's engine table disagree about which engines "
            "exist; add the engine to both")

    def test_every_engine_the_catalog_uses_is_a_registered_engine(self) -> None:
        """The invariant behind both documents: no manifest names an engine the Hub cannot route."""
        for package_id, manifest in manifests().items():
            with self.subTest(package=package_id):
                self.assertIn(manifest["engine"], standalone._ENGINE_ROUTE,
                              f"{package_id} names engine {manifest['engine']!r}, which the Hub "
                              f"has no route for")


class KitDocumentNumbersTests(unittest.TestCase):
    """The documents that ship on the drive state stock and the copyable subset separately.

    `packaging/kit-readmes/Studio Hub KH.md` goes onto every SSD and
    `qualification/SSD-KIT-README.md` is the "read this first" beside it, so the numbers there
    are what the owner checks the drive against. A `production_allowed: false` package stays on
    the SSD as stock while ordinary setup refuses to copy it — a set that has been empty since
    2026-10-27, when the owner removed the last name-based hold ("We no longer block or lock any
    model."). Retyping today's numbers would only reset the clock; these derive both inventories
    from the manifests.
    """

    KIT_README = ROOT / "packaging" / "kit-readmes" / "Studio Hub KH.md"
    KIT_INDEX = ROOT / "packaging" / "kit-readmes" / "README.md"
    SSD_README = ROOT / "qualification" / "SSD-KIT-README.md"
    NODE_CHECKLIST = ROOT / "qualification" / "NODE-SETUP-CHECKLIST.md"

    def _match(self, text: str, pattern: str, form: str) -> re.Match:
        match = re.search(pattern, text)
        self.assertIsNotNone(
            match, f"the document no longer states this in the measured form {form!r}")
        return match

    def _assert_copyable_inventory(self, text: str) -> None:
        match = self._match(
            text,
            r"Normal setup copies \*\*(\d+) production-allowed packages\*\*\s+"
            r"\(about ([\d.]+) GB\)\. No package is withheld pending qualification any more",
            "Normal setup copies **<n> production-allowed packages** (about <gb> GB). "
            "No package is withheld pending qualification any more")
        self.assertEqual(int(match.group(1)), measured_kit_packages())
        self.assertEqual(float(match.group(2)), round(measured_kit_bytes() / 1e9, 1))
        # Total and copyable now name the same set: the owner removed the last name-based hold on
        # 2026-10-27 ("We no longer block or lock any model."), so RAM - not a manifest flag -
        # decides what a Mac copies and runs. This assertion is the alarm if a pending flag or a
        # withheld-name list ever comes back without its documents.
        self.assertEqual(measured_kit_packages(), measured_packages(),
                         "some shipped package is withheld from ordinary setup again; if that is "
                         "deliberate, restore the pending-package sentence to these documents and "
                         "to this test")

    def test_the_kit_readme_states_the_measured_package_count_and_size(self) -> None:
        text = self.KIT_README.read_text(encoding="utf-8")
        match = self._match(
            text, r"The source catalog for the next kit build lists \*\*(\d+) model packages\*\* "
                  r"\(about ([\d.]+) GB\)",
            "The source catalog for the next kit build lists **<n> model packages** "
            "(about <gb> GB)")
        self.assertEqual(int(match.group(1)), measured_packages(),
                         "packaging/kit-readmes/Studio Hub KH.md's package count disagrees with "
                         "model-manifests/*.json; update the sentence")
        self.assertEqual(float(match.group(2)), round(measured_bytes() / 1e9, 1),
                         "the kit README's size disagrees with the sum of the manifests' file "
                         "sizes; update the sentence")
        self._assert_copyable_inventory(text)

    def test_the_ssd_readme_states_the_measured_package_count_and_size(self) -> None:
        text = self.SSD_README.read_text(encoding="utf-8")
        match = self._match(
            text, r"\*\*Models:\*\* `model-library/` is described by a (\d+)-package "
                  r"source catalog \(~([\d.]+) GB\)",
            "**Models:** `model-library/` is described by a <n>-package source catalog "
            "(~<gb> GB)")
        self.assertEqual(int(match.group(1)), measured_packages(),
                         "qualification/SSD-KIT-README.md's package count disagrees with "
                         "model-manifests/*.json; update the sentence")
        self.assertEqual(float(match.group(2)), round(measured_bytes() / 1e9, 1),
                         "the SSD README's size disagrees with the sum of the manifests' file "
                         "sizes; update the sentence")
        self._assert_copyable_inventory(text)

    def test_the_node_checklist_states_the_measured_package_count(self) -> None:
        text = self.NODE_CHECKLIST.read_text(encoding="utf-8")
        match = self._match(text, r"with \*\*(\d+)\*\* model packages",
                            "with **<n>** model packages")
        self.assertEqual(int(match.group(1)), measured_packages(),
                         "qualification/NODE-SETUP-CHECKLIST.md's package count disagrees with "
                         "model-manifests/*.json; update the sentence")
        self._assert_copyable_inventory(text)

    def test_the_kit_index_does_not_pin_the_studio_hub_version(self) -> None:
        """A version in this table went stale every release; it points at the kit receipt now."""
        text = self.KIT_INDEX.read_text(encoding="utf-8")
        row = re.search(r"^\| `Studio Hub KH/` \| ([^|]*) \| ([^|]*) \|", text, re.MULTILINE)
        self.assertIsNotNone(row, "packaging/kit-readmes/README.md no longer has its Studio Hub "
                                  "row in the form '| `Studio Hub KH/` | ... | ... | ... |'")
        self.assertIsNone(
            re.search(r"\d+\.\d+\.\d+", row.group(2)),
            "the kit index pins a Studio Hub version, which goes stale on the next release; "
            "point at the kit's own focused-release-manifest.json instead")


class TheKitIndexIsStampedFromTheDriveTest(unittest.TestCase):
    """The kit index names the versions the drive carries, not the ones a file remembered.

    Recorded in the ledger three times before this: `0.6.63` against a `0.6.70` bundle, `0.6.70`
    against `0.6.89`, and again when the three drives were prepared carrying Story **1.4.235** and
    GatewayKH **0.2.67** while the index said **1.4.234** / **0.2.60** and the drive-root copy of
    the same document still said **1.4.219**. The Studio Hub row was fixed by deriving it; these
    two rows are now derived the same way.
    """

    def setUp(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
        import kit_stamp_index
        self.stamp = kit_stamp_index
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.drive = Path(self.tmp.name)

    def _kit(self, *, story: str | None = "Story Studio KH 9.9.9-arm64.dmg",
             gateway: str | None = "gateway-kh-8.8.8.dmg",
             index: str | None = None) -> Path:
        kit = self.drive / "Video Generation Kit"
        (kit / "Studio Hub KH").mkdir(parents=True, exist_ok=True)
        if story:
            (kit / "Story Studio").mkdir(parents=True, exist_ok=True)
            (kit / "Story Studio" / story).write_bytes(b"dmg")
        if gateway:
            (kit / "GatewayKH" / "installers").mkdir(parents=True, exist_ok=True)
            (kit / "GatewayKH" / "installers" / gateway).write_bytes(b"dmg")
        (kit / "Studio Hub KH" / "README.md").write_text(index or (
            "| Folder | App | Version | What it does |\n"
            "|---|---|---|---|\n"
            "| `Story Studio/` | Story Studio KH | 1.4.234 | Writes and produces the episode |\n"
            "| `GatewayKH/` | GatewayKH | 0.2.60 | The middle man |\n"
            "| `Studio Hub KH/` | Studio Hub | its `focused-release-manifest.json` | Runs the models |\n"
        ), encoding="utf-8")
        return kit

    def test_both_rows_take_the_version_the_installer_states(self) -> None:
        kit = self._kit()
        self.assertEqual(self.stamp.stamp(self.drive), 0)
        text = (kit / "Studio Hub KH" / "README.md").read_text(encoding="utf-8")
        self.assertIn("| 9.9.9 |", text)
        self.assertIn("| 8.8.8 |", text)
        self.assertNotIn("1.4.234", text)
        self.assertNotIn("0.2.60", text)

    def test_the_studio_hub_row_is_left_alone(self) -> None:
        # It points at the kit's generated manifest, which derives its own number; restating one
        # here would be the second copy this script exists to remove.
        kit = self._kit()
        self.stamp.stamp(self.drive)
        self.assertIn("its `focused-release-manifest.json`",
                      (kit / "Studio Hub KH" / "README.md").read_text(encoding="utf-8"))

    def test_a_missing_installer_refuses_rather_than_stamping_unknown(self) -> None:
        kit = self._kit(story=None)
        self.assertEqual(self.stamp.stamp(self.drive), 2)
        self.assertIn("1.4.234", (kit / "Studio Hub KH" / "README.md").read_text(encoding="utf-8"),
                      "a refusal must leave the document exactly as it was")

    def test_a_filename_with_no_version_refuses_too(self) -> None:
        self._kit(gateway="gateway-kh-latest.dmg")
        self.assertEqual(self.stamp.stamp(self.drive), 2)

    def test_the_drive_root_copy_is_refreshed_from_the_kit_copy(self) -> None:
        # The two were the same document at different ages, which is how one drive came to name
        # 1.4.219 while the kit beside it carried 1.4.234.
        kit = self._kit()
        (kit / "README.md").write_text("stale", encoding="utf-8")
        self.stamp.stamp(self.drive)
        self.assertEqual((kit / "README.md").read_text(encoding="utf-8"),
                         (kit / "Studio Hub KH" / "README.md").read_text(encoding="utf-8"))

    def test_the_instructions_in_the_body_are_rewritten_too(self) -> None:
        """Stamping the version *line* was not enough — the same document says "Open `…dmg`".

        On the three drives prepared on 2026-09-28 those steps pointed at installers that had just
        been moved to `_trash`: an operator following the checklist failed at step one. The
        filename was copied in by hand and nothing derived it.
        """
        kit = self._kit()
        (kit / "Story Studio" / "README.md").write_text(
            "# Story Studio\n\n**Version 1.4.219.** Installer: `Story Studio KH 1.4.219-arm64.dmg`\n"
            "\n## Install\n\n1. Open `Story Studio KH 1.4.219-arm64.dmg`.\n", encoding="utf-8")
        (kit / "GatewayKH" / "README.md").write_text(
            "1. Open `installers/gateway-kh-0.2.2-b1c0d8f-202609221617-arm64.dmg`.\n",
            encoding="utf-8")
        self.assertEqual(self.stamp.stamp(self.drive), 0)
        story = (kit / "Story Studio" / "README.md").read_text(encoding="utf-8")
        gateway = (kit / "GatewayKH" / "README.md").read_text(encoding="utf-8")
        self.assertIn("`Story Studio KH 9.9.9-arm64.dmg`", story)
        self.assertNotIn("1.4.219", story)
        # The Gateway's installer lives in `installers/`, so the rewritten path keeps that prefix.
        self.assertIn("`installers/gateway-kh-8.8.8.dmg`", gateway)

    def test_another_apps_installer_is_not_rewritten(self) -> None:
        # A document is free to mention a different app's DMG, or a checksum file.
        kit = self._kit()
        (kit / "Story Studio" / "README.md").write_text(
            "See also `GatewayKH/installers/gateway-kh-0.2.2.dmg` and `SHA256.txt`.\n",
            encoding="utf-8")
        self.stamp.stamp(self.drive)
        text = (kit / "Story Studio" / "README.md").read_text(encoding="utf-8")
        self.assertIn("gateway-kh-0.2.2.dmg", text)

    def test_the_newest_installer_wins(self) -> None:
        kit = self._kit()
        (kit / "Story Studio" / "Story Studio KH 9.9.10-arm64.dmg").write_bytes(b"dmg")
        import os
        os.utime(kit / "Story Studio" / "Story Studio KH 9.9.10-arm64.dmg", (2_000_000_000, 2_000_000_000))
        self.stamp.stamp(self.drive)
        self.assertIn("| 9.9.10 |", (kit / "Studio Hub KH" / "README.md").read_text(encoding="utf-8"))

    def test_a_bare_volume_name_is_read_as_a_volume(self) -> None:
        """`prepare-kit.sh` passes the bare name it loops over, and that is how the write was lost.

        `Path("KH-STUDIO-NOVA")` is relative, so the index was looked for under the current
        directory, never found, and the stamp refused — on every drive, every time, with the refusal
        printed as a path that reads like a real one. The mechanism that derives these rows from the
        drive was dead while the rows themselves were right, because the template they were copied
        from happened to be current. Only the *next* release would have shown it.
        """
        self.assertEqual(self.stamp.resolve_drive("KH-STUDIO-NOVA"),
                         Path("/Volumes/KH-STUDIO-NOVA"))

    def test_an_explicit_path_is_left_alone(self) -> None:
        self.assertEqual(self.stamp.resolve_drive("/Volumes/KH-STUDIO-ORICO"),
                         Path("/Volumes/KH-STUDIO-ORICO"))
        self.assertEqual(self.stamp.resolve_drive("./some/drive"), Path("./some/drive"))

    def test_a_refusal_names_an_absolute_path(self) -> None:
        """The refusal was printed in full and still hid for four releases: it read as relative.

        A message that names where it actually looked is the difference between a log line that can
        be checked and one that can only be believed.
        """
        empty = self.drive / "not-a-drive"
        empty.mkdir()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(self.stamp.stamp(empty), 1)
        message = stdout.getvalue()
        self.assertIn(str(empty.resolve()), message)
        self.assertTrue(message.strip().endswith("the drive?"), message)


if __name__ == "__main__":
    unittest.main()
