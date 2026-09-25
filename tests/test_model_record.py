"""The model record must match the records it is generated from.

`docs/model-benchmarks.md` is the page the owner reads to remember what was tested and what each
Mac can run. It is generated, and this is what makes that true: the test rebuilds it in memory and
compares it to the committed file, byte for byte. Edit the document by hand and it fails; add a
reading without rebuilding it and it fails.

That matters more here than for most documents. The owner asked for this precisely because he
*could not remember* what had been tested — so a stale copy would not just be untidy, it would be
the thing he trusts instead of the record. This project has already paid four times for a document
that outlived its facts.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "model-benchmarks.md"
MODULE = ROOT / "qualification" / "model_record.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("studiohub_model_record", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = load_generator()

    def test_the_committed_page_is_exactly_what_the_records_produce(self) -> None:
        self.assertTrue(DOCUMENT.exists(),
                        "docs/model-benchmarks.md is missing; run "
                        "qualification/model_record.py --write")
        self.assertEqual(
            DOCUMENT.read_text(encoding="utf-8"), self.generator.build(),
            "docs/model-benchmarks.md is out of date: the records changed and the page did not. "
            "Run `./.build/voice-runtime/bin/python3 -B qualification/model_record.py --write`.")

    def test_a_reading_whose_source_is_gone_is_refused(self) -> None:
        """A number may not outlive the record it came from.

        This is the failure the page exists to prevent, so it is checked rather than trusted: the
        generator raises when a reading names a source that is not there, which is what stops a
        figure being carried forward after its evidence is deleted or renamed.
        """
        readings = self.generator.load_readings()
        for row in readings["readings"]:
            self.assertTrue((ROOT / row["source_path"]).exists(),
                            f"{row['model']} on {row['machine']} names a missing source: "
                            f"{row['source_path']}")
        self.assertTrue(readings["readings"], "the readings file has no rows at all")

    def test_every_shipped_package_appears_and_no_other_does(self) -> None:
        """The page covers the catalogue, so a model added to the fleet cannot be missing from it.

        A companion package is the exception that is not one: it is files another package requires,
        and the page marks it as supporting files rather than answering "can this Mac run it?"
        about something nobody chooses.
        """
        manifests = self.generator.load_manifests()
        labels = self.generator.label_map(manifests, self.generator.load_readings())
        document = DOCUMENT.read_text(encoding="utf-8")
        companions = {dep for manifest in manifests
                      for dep in (manifest.get("requires_packages") or [])}
        for package_id in (m["package_id"] for m in manifests):
            shown = labels.get(package_id)
            if shown is None:
                self.assertIn(package_id, companions,
                              f"{package_id} is not a companion and no producer names it")
                shown = package_id
            self.assertIn(shown, document,
                          f"{package_id} is shipped but does not appear in the record")

    def test_the_measured_rows_state_their_quantization(self) -> None:
        """Every measured row names the quantization, because a reading without it cannot be
        checked against what is on disk.

        This is deliberately narrower than "every label carries bits". Producers differ honestly:
        the voice and image labels carry the quantization, Whisper's do not because Whisper is not
        quantized, and the Music labels carry none either — which is a gap in *those* labels and is
        reported as one rather than papered over here by inventing a value.
        """
        for row in self.generator.load_readings()["readings"]:
            self.assertTrue(row.get("quantization"),
                            f"{row['model']} is measured but its reading states no quantization")
            self.assertNotIn(row["quantization"].strip().lower(), ("", "n/a", "unknown"))

def json_package_id(path: Path) -> str:
    import json
    return json.loads(path.read_text(encoding="utf-8"))["package_id"]


if __name__ == "__main__":
    unittest.main()
