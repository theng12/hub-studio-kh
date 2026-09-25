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

class FleetTableTests(unittest.TestCase):
    """The record's machine table must match the file that owns that fact.

    `research/owner-hardware.md` is canonical (AGENTS.md: "do not ask the owner to restate his
    machines"). The generator keeps a copy because the alternative is parsing prose at build time,
    and a copy is only safe if something checks it — this one had already drifted, naming the 32 GB
    Mac an M1 Max, which that file records as a correction it had made.
    """

    def test_the_generator_fleet_matches_the_canonical_file(self) -> None:
        canonical = (ROOT / "research" / "owner-hardware.md").read_text(encoding="utf-8")
        rows = []
        for line in canonical.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 3 or not cells[0].endswith("GB"):
                continue
            try:
                size = int(cells[0].split()[0])
                count = int(cells[2])
            except ValueError:
                continue
            rows.append((size, cells[1], count))
        self.assertTrue(rows, "no fleet table found in research/owner-hardware.md")

        def facts(entries):
            """The fact, not the phrasing: 'Apple M2 Max (Mac Studio)' and 'M2 Max' are one machine.

            The canonical table writes a chip list; the generator may qualify it or name the model.
            What must agree is which sizes exist, what silicon they are, and how many of each — the
            three things a reader acts on.
            """
            normalised = []
            for size, chips, count in entries:
                text = chips.replace("Apple ", "")
                text = text.split("(")[0].strip().rstrip(",")
                normalised.append((size, " ".join(text.split()), count))
            return sorted(normalised)

        self.assertEqual(
            facts(rows), facts(load_generator().FLEET),
            "qualification/model_record.py's FLEET disagrees with research/owner-hardware.md; the "
            "canonical file wins and the generator's copy must be corrected")


def json_package_id(path: Path) -> str:
    import json
    return json.loads(path.read_text(encoding="utf-8"))["package_id"]


if __name__ == "__main__":
    unittest.main()
