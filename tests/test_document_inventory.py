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

import json
import re
import sys
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
    """The documents that ship on the drive state the same measured catalog.

    `packaging/kit-readmes/Studio Hub KH.md` goes onto every SSD and
    `qualification/SSD-KIT-README.md` is the "read this first" beside it, so the numbers there
    are what the owner checks the drive against. They said 19 packages / 59 GB, 12 / 29 GB and
    17 respectively against a measured 24. Retyping today's number would only reset the clock;
    these assert the rule, so the next model fails here instead of shipping a stale readme.
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

    def test_the_kit_readme_states_the_measured_package_count_and_size(self) -> None:
        text = self.KIT_README.read_text(encoding="utf-8")
        match = self._match(
            text, r"The kit carries \*\*(\d+) model packages\*\* \(about ([\d.]+) GB\)",
            "The kit carries **<n> model packages** (about <gb> GB)")
        self.assertEqual(int(match.group(1)), measured_packages(),
                         "packaging/kit-readmes/Studio Hub KH.md's package count disagrees with "
                         "model-manifests/*.json; update the sentence")
        self.assertEqual(float(match.group(2)), round(measured_bytes() / 1e9, 1),
                         "the kit README's size disagrees with the sum of the manifests' file "
                         "sizes; update the sentence")

    def test_the_ssd_readme_states_the_measured_package_count_and_size(self) -> None:
        text = self.SSD_README.read_text(encoding="utf-8")
        match = self._match(
            text, r"\*\*Models:\*\* `model-library/` — (\d+) verified packages, ~([\d.]+) GB",
            "**Models:** `model-library/` — <n> verified packages, ~<gb> GB")
        self.assertEqual(int(match.group(1)), measured_packages(),
                         "qualification/SSD-KIT-README.md's package count disagrees with "
                         "model-manifests/*.json; update the sentence")
        self.assertEqual(float(match.group(2)), round(measured_bytes() / 1e9, 1),
                         "the SSD README's size disagrees with the sum of the manifests' file "
                         "sizes; update the sentence")

    def test_the_node_checklist_states_the_measured_package_count(self) -> None:
        text = self.NODE_CHECKLIST.read_text(encoding="utf-8")
        match = self._match(text, r"with \*\*(\d+)\*\* model packages",
                            "with **<n>** model packages")
        self.assertEqual(int(match.group(1)), measured_packages(),
                         "qualification/NODE-SETUP-CHECKLIST.md's package count disagrees with "
                         "model-manifests/*.json; update the sentence")

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


if __name__ == "__main__":
    unittest.main()
