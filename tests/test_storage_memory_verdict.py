"""The storage page's families must carry a memory verdict this Mac can be judged against.

The voice catalogue already declares `min_unified_memory_gb` per model, so "can this Mac run it" is a
comparison, not a guess. A size heuristic would get it wrong in both directions: fish-audio-s2-pro-8bit
is 6.7 GB of weights with a declared floor of 24 GB, so file size alone would call it fine on a 16 GB
Mac. `inventory()` now joins each family's model items to their catalogue entry and reports the floor,
this Mac's memory, and whether the floor fits.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.voice.app.backend import model_storage  # noqa: E402


class FamilyMemoryTests(unittest.TestCase):
    """The join itself, on fake entries - so it can never pass vacuously.

    The first version of this test called `inventory()` from the repository, where the cache
    resolves elsewhere and there is nothing to inspect: it reported zero groups, every loop did
    nothing, and the suite went green while the production code would have raised `AttributeError`
    on every real request. These checks run the join directly.
    """

    def _entry(self, floor):
        return SimpleNamespace(min_unified_memory_gb=floor, repo="x")

    def test_a_dataclass_entry_is_read_by_attribute(self):
        items = [{"type": "model", "repo": "org/model"}]
        memory = model_storage._family_memory(items, {"org/model": self._entry(24)}, 16)
        self.assertEqual(memory, {"required_gb": 24, "this_mac_gb": 16, "runs_here": False})

    def test_a_mapping_entry_still_works(self):
        items = [{"type": "model", "repo": "org/model"}]
        memory = model_storage._family_memory(items, {"org/model": {"min_unified_memory_gb": 8}}, 16)
        self.assertTrue(memory["runs_here"])
        self.assertEqual(memory["required_gb"], 8)

    def test_companions_do_not_contribute_a_floor(self):
        items = [{"type": "dependency", "repo": "org/codec"}]
        self.assertIsNone(model_storage._family_memory(items, {"org/codec": self._entry(99)}, 16))

    def test_the_highest_floor_wins_within_a_family(self):
        items = [{"type": "model", "repo": "a"}, {"type": "model", "repo": "b"}]
        entries = {"a": self._entry(8), "b": self._entry(12)}
        self.assertEqual(model_storage._family_memory(items, entries, 16)["required_gb"], 12)

    def test_no_floor_or_no_memory_means_no_verdict(self):
        items = [{"type": "model", "repo": "a"}]
        self.assertIsNone(model_storage._family_memory(items, {"a": self._entry(None)}, 16))
        self.assertIsNone(model_storage._family_memory(items, {"a": self._entry(8)}, None))
        self.assertIsNone(model_storage._family_memory([], {}, 16))

    def test_a_model_missing_from_the_catalogue_is_skipped_not_fatal(self):
        items = [{"type": "model", "repo": "org/not-catalogued"}]
        self.assertIsNone(model_storage._family_memory(items, {}, 16))


class LiveStorageReportTests(unittest.TestCase):
    """Against whatever cache this process can see; honest about having nothing to inspect."""

    @classmethod
    def setUpClass(cls):
        cls.report = model_storage.inventory()

    def test_a_verdict_always_follows_from_the_numbers_it_reports(self):
        for group in self.report["groups"]:
            memory = group.get("memory")
            if memory is None:
                continue
            self.assertEqual(memory["runs_here"], memory["required_gb"] <= memory["this_mac_gb"],
                             group["id"])

    def test_a_catalogued_model_with_a_floor_is_not_left_without_a_verdict(self):
        entries = model_storage._tts_models()
        checked = 0
        for group in self.report["groups"]:
            items = [item for item in group["items"] if item["type"] == "model"]
            floors = [getattr(entries.get(item["repo"]), "min_unified_memory_gb", None)
                      for item in items]
            if any(isinstance(floor, int) for floor in floors):
                checked += 1
                self.assertIsNotNone(group.get("memory"),
                                     f"{group['id']} declares a floor but reports no verdict")
        if checked == 0:
            self.skipTest("no catalogued model is visible in this process's cache")

    def test_this_macs_memory_is_plausible(self):
        ram = model_storage._machine_memory_gb()
        self.assertIsInstance(ram, int)
        self.assertGreater(ram, 4)
        self.assertLess(ram, 4096)


if __name__ == "__main__":
    unittest.main()
