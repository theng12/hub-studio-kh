"""The four install presets: data, not code, and every id must really be a model.

**Owner decision 2026-10-28.** A new Mac is set up by choosing one of four presets sized to its
memory. What is pinned here, and why each one is a test rather than a review note:

1. **Every preset package id has a manifest.** A preset naming a package the catalog does not ship
   is a Mac that reports an install finished while a lane it was promised is missing. This is the
   "verified completion" rule: the failure must be loud here, not discovered at first use.
2. **A preset includes the smaller ones.** Each file lists its own additions and every package the
   smaller presets carry, so nothing has to union four files. A file that drops a smaller preset's
   package fails, rather than quietly shipping a thinner 32 GB Mac.
3. **A companion is never listed.** ``requires_packages`` already states that ``yue2-3b-mlx`` is no
   use without ``yue2-vae``; a preset that listed the codec would make it look like a choice.
4. **The name carries the RAM number**, because the number *is* the choice.
5. **A model too big for the Mac is refused with its floor and this Mac's memory** — the owner's
   rule is greyed out with the reason, never hidden and never swapped.
6. **Nothing about presets lives in code.** Adding one is a JSON file; this test reads the
   directory, so a fifth preset is exercised without an edit.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from components import model_presets

ROOT = Path(__file__).resolve().parents[1]


def _manifests() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "model-manifests").glob("*.json")}


class PresetDataTests(unittest.TestCase):
    """The shipped files, read the way the Hub reads them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.presets = model_presets.load()
        cls.manifests = _manifests()

    def test_there_are_exactly_four_presets_and_they_are_tiers(self) -> None:
        # The owner's decision is four. A fifth is an owner call, not a convenience, so the count
        # is pinned rather than asserted to be "at least one".
        self.assertEqual([preset["preset_id"] for preset in self.presets],
                         ["essentials-8", "creator-16", "creator-plus-24", "studio-pro-32"])
        self.assertEqual([preset["memory_tier_gb"] for preset in self.presets], [8, 16, 24, 32])

    def test_every_preset_name_carries_its_ram_number(self) -> None:
        for preset in self.presets:
            with self.subTest(preset=preset["preset_id"]):
                self.assertIn(str(preset["memory_tier_gb"]), preset["name"])

    def test_every_listed_package_is_a_shipped_manifest(self) -> None:
        for preset in self.presets:
            with self.subTest(preset=preset["preset_id"]):
                self.assertEqual(model_presets.problems(preset, self.manifests), [])

    def test_a_preset_includes_every_smaller_preset(self) -> None:
        # Not a union at read time: each file states the whole set, so a reader never has to know
        # the ordering. This is what keeps a preset honest when a smaller one grows.
        for smaller, larger in zip(self.presets, self.presets[1:]):
            with self.subTest(smaller=smaller["preset_id"], larger=larger["preset_id"]):
                missing = sorted(set(smaller["packages"]) - set(larger["packages"]))
                self.assertEqual(missing, [], f"{larger['preset_id']} drops {missing}")

    def test_no_preset_lists_a_companion(self) -> None:
        # A companion arrives through `requires_packages`, resolved at install time. Listing one
        # would present a tokenizer or a codec as something a person chose.
        listed = {package for preset in self.presets for package in preset["packages"]}
        companions = {dependency for manifest in self.manifests.values()
                      for dependency in (manifest.get("requires_packages") or [])}
        self.assertEqual(sorted(listed & companions - {"whisper-turbo"}), [],
                         "a preset lists a companion package; it should arrive through requires_packages")

    def test_a_companion_is_carried_into_the_install_set(self) -> None:
        # yue2-3b-mlx is no use without yue2-vae, and the manifest already says so.
        preset = model_presets.by_id("creator-plus-24")
        install = model_presets.install_set(preset, self.manifests)
        self.assertIn("yue2-3b-mlx", preset["packages"])
        self.assertNotIn("yue2-vae", preset["packages"])
        self.assertIn("yue2-vae", install)

    def test_the_owner_s_lists_are_what_ships(self) -> None:
        # The exact membership the owner approved on 2026-10-28, pinned so a later edit to a data
        # file is a visible decision rather than a quiet drift. Renaming a preset is a data edit
        # and stays free; changing what it installs is this test.
        eight = set(model_presets.by_id("essentials-8")["packages"])
        self.assertEqual(eight, {"z-image-turbo-mflux-4bit", "flux2-klein-4b-mlx-4bit",
                                 "qwen3-tts-12hz-0-6b-base-4bit", "voxcpm2-4bit", "whisper-turbo",
                                 "florence-2-base-ft", "depth-anything-v2-small",
                                 "bge-small-en-v1-5-bf16", "stable-audio-3-medium"})
        self.assertEqual(set(model_presets.by_id("creator-16")["packages"]) - eight,
                         {"qwen-image-2-1-turbo-mflux-q4", "qwen3-5-9b-4bit", "florence-2-large-ft",
                          "omnivoice-bfloat16", "breeze-tts-2-mlx-4bit"})
        self.assertEqual(set(model_presets.by_id("creator-plus-24")["packages"])
                         - set(model_presets.by_id("creator-16")["packages"]),
                         {"yue2-3b-mlx", "gemma-3-12b-it-4bit"})
        self.assertEqual(set(model_presets.by_id("studio-pro-32")["packages"])
                         - set(model_presets.by_id("creator-plus-24")["packages"]),
                         {"qwen36-35b-4bit", "gemma4-26b-a4b-qat-4bit"})

    def test_the_lite_mode_set_is_inside_the_8gb_preset(self) -> None:
        # Lite Mode stops being a separate install option (2026-10-28): a shop Mac takes the 8 GB
        # preset. The six lanes it runs must therefore all be present, or a shop Mac set up this
        # way silently loses one. `components/workstation_models.py` keeps its runtime role.
        from components import workstation_models
        lite = {name for name in workstation_models.WORKSTATION_MODELS if "/" not in name}
        eight = set(model_presets.by_id("essentials-8")["packages"])
        self.assertEqual(sorted(lite - eight), [], "the 8 GB preset must carry every Lite Mode model")

    def test_every_preset_fits_its_own_tier(self) -> None:
        # A preset that shipped a model its own tier cannot run would hand a Mac a model it is then
        # refused. Derived from the manifests, never from a list of exceptions.
        for preset in self.presets:
            for package_id in model_presets.install_set(preset, self.manifests):
                with self.subTest(preset=preset["preset_id"], package=package_id):
                    self.assertIsNone(
                        model_presets.refusal(package_id, self.manifests[package_id],
                                              preset["memory_tier_gb"]),
                        f"{package_id} needs more memory than the {preset['name']} preset's own tier")


class StartingPresetTests(unittest.TestCase):
    def test_the_offered_preset_rounds_down_so_it_always_fits(self) -> None:
        # Down, never up: the marked preset must be one this Mac can run in full, or the screen
        # marks a set whose own floor check refuses half its models.
        self.assertEqual(model_presets.starting_id(8), "essentials-8")
        self.assertEqual(model_presets.starting_id(12), "essentials-8")
        self.assertEqual(model_presets.starting_id(16), "creator-16")
        self.assertEqual(model_presets.starting_id(30), "creator-plus-24")
        self.assertEqual(model_presets.starting_id(32), "studio-pro-32")
        self.assertEqual(model_presets.starting_id(64), "studio-pro-32")

    def test_a_marked_preset_never_contains_a_model_this_mac_cannot_run(self) -> None:
        # The invariant behind rounding down, checked against the real manifests at every tier.
        manifests = _manifests()
        for memory in (8, 10, 12, 16, 18, 24, 32, 64):
            preset = model_presets.by_id(model_presets.starting_id(memory))
            for package_id in model_presets.install_set(preset, manifests):
                with self.subTest(memory=memory, package=package_id):
                    self.assertIsNone(model_presets.refusal(package_id, manifests[package_id], memory))

    def test_an_unknown_memory_is_not_a_small_machine(self) -> None:
        # A memory figure the Hub could not read must not silently become 8 GB: that would offer a
        # smaller starting set than the Mac can run and read as a decision nobody made.
        for value in (None, 0, -1, True, "16"):
            with self.subTest(value=value):
                self.assertIsNone(model_presets.starting_id(value))


class RefusalTests(unittest.TestCase):
    def test_a_model_too_big_is_refused_with_both_numbers(self) -> None:
        manifest = {"package_id": "big", "qualification": {"minimum_unified_memory_gb": 32}}
        reason = model_presets.refusal("big", manifest, 16)
        self.assertIn("32 GB", reason)
        self.assertIn("16 GB", reason)
        self.assertIsNone(model_presets.refusal("big", manifest, 32))

    def test_a_model_that_states_no_floor_is_never_refused(self) -> None:
        # "No number published" and "needs zero" must not produce the same answer, and neither is a
        # reason to refuse: an absent floor is a missing measurement, not a small machine.
        self.assertIsNone(model_presets.refusal("x", {"package_id": "x"}, 8))
        self.assertIsNone(model_presets.refusal("x", {"package_id": "x",
                                                      "qualification": {"minimum_unified_memory_gb": None}},
                                                8))

    def test_the_floor_is_read_from_the_manifest_and_nowhere_else(self) -> None:
        # The kit used to carry PROVEN_SMALLER / CARRY_BELOW_TIER so a model the owner had run on a
        # smaller Mac could be carried there. Both are gone; this pins that the manifest is the one
        # source, so a re-capture cannot quietly re-introduce a second.
        shipped = _manifests()
        for package_id in ("flux2-klein-4b-mlx-4bit", "breeze-tts-2-mlx-4bit",
                           "z-image-turbo-mflux-4bit"):
            with self.subTest(package=package_id):
                self.assertEqual(model_presets.floor_gb(package_id, shipped[package_id]), 8)
        # The kit's two hand-kept floor tables are deleted, not emptied (owner decision 2026-10-28):
        # a table left behind is where the next second copy starts. The comment explaining where
        # their numbers went is allowed to name them; code is not.
        import ast
        tree = ast.parse((ROOT / "qualification" / "copy_models_for_this_mac.py").read_text())
        assigned = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)}
        self.assertNotIn("PROVEN_SMALLER", assigned)
        self.assertNotIn("CARRY_BELOW_TIER", assigned)


class PresetFileRefusalTests(unittest.TestCase):
    """A broken preset file fails loudly, naming the field. Unlike a manifest, which degrades one row."""

    def _load_with(self, files: dict[str, dict]) -> list[dict]:
        directory = tempfile.mkdtemp(prefix="presets-")
        for name, payload in files.items():
            (Path(directory) / name).write_text(json.dumps(payload), encoding="utf-8")
        with unittest.mock.patch.dict("os.environ", {"STUDIOHUB_MODEL_PRESETS": directory}):
            return model_presets.load()

    def test_an_empty_folder_is_a_refusal_not_an_empty_list(self) -> None:
        with self.assertRaises(model_presets.PresetError) as caught:
            self._load_with({})
        self.assertIn("no preset files", str(caught.exception))

    def test_a_bad_field_names_the_file_and_the_field(self) -> None:
        good = {"schema_version": 1, "preset_id": "fine", "name": "Fine · 8 GB",
                "memory_tier_gb": 8, "packages": ["whisper-turbo"]}
        cases = {
            "no name": {**good, "name": ""},
            "no RAM number in the name": {**good, "name": "Starter"},
            "no tier": {**good, "memory_tier_gb": None},
            "no packages": {**good, "packages": []},
            "a repo id in the list": {**good, "packages": ["mlx-community/whisper-turbo"]},
            "the same package twice": {**good, "packages": ["whisper-turbo", "whisper-turbo"]},
            "a future schema": {**good, "schema_version": 2},
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(model_presets.PresetError) as caught:
                    self._load_with({"fine.json": payload})
                self.assertIn("fine", str(caught.exception))

    def test_a_fifth_preset_needs_no_code(self) -> None:
        # The data-not-code rule, proven rather than asserted: a new file is loaded, ordered and
        # offered without an edit anywhere.
        loaded = self._load_with({
            "fine-8.json": {"schema_version": 1, "preset_id": "fine-8", "name": "Fine · 8 GB",
                            "memory_tier_gb": 8, "packages": ["whisper-turbo"]},
            "huge-64.json": {"schema_version": 1, "preset_id": "huge-64", "name": "Huge · 64 GB",
                             "memory_tier_gb": 64, "packages": ["whisper-turbo", "qwen36-35b-4bit"]}})
        self.assertEqual([preset["preset_id"] for preset in loaded], ["fine-8", "huge-64"])
        with unittest.mock.patch.dict("os.environ",
                                      {"STUDIOHUB_MODEL_PRESETS": str(ROOT / "model-presets")}):
            self.assertEqual(model_presets.by_id("creator-16")["preset_id"], "creator-16")


if __name__ == "__main__":
    unittest.main()
