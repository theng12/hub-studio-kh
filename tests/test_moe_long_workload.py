import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("moe_probe", ROOT / "qualification" / "moe_probe.py")
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def scene_output(scene_id: str, prompt: str) -> str:
    return json.dumps({"results": [{"scene_id": scene_id, "visual_prompt": prompt}]})


class LongScriptValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = {
            "minimum_words_per_chapter": 3,
            "maximum_words_per_chapter": 5,
            "chapters": [
                {"number": 7, "title": "Quiet Meridian"},
                {"number": 8, "title": "Lantern Ledger"},
                {"number": 9, "title": "Dawn Watch"},
            ],
            "must_include": ["Mara", "brass key"],
            "must_not_include": ["lost the brass key"],
        }

    def script(self, headings=None, bodies=None) -> str:
        headings = headings or [
            "## Chapter 07: Quiet Meridian",
            "## Chapter 08: Lantern Ledger",
            "## Chapter 09: Dawn Watch",
        ]
        bodies = bodies or ["Mara holds brass key", "still stars remain calm", "morning light arrives"]
        return "\n\n".join(f"{heading}\n\n{body}" for heading, body in zip(headings, bodies))

    def test_accepts_exact_three_chapters_and_counts_each_body(self) -> None:
        result = probe.validate_long_script(self.script(), self.spec)
        self.assertTrue(result["pass"], result["errors"])
        self.assertEqual([4, 4, 3], [chapter["word_count"] for chapter in result["chapters"]])
        self.assertTrue(all(chapter["pass"] for chapter in result["chapters"]))

    def test_rejects_a_single_short_body_even_when_total_is_long_enough(self) -> None:
        result = probe.validate_long_script(
            self.script(bodies=["Mara holds brass key calmly", "short", "many calm words fill dawn watch"]),
            self.spec,
        )
        self.assertFalse(result["pass"])
        self.assertFalse(result["chapters"][1]["pass"])

    def test_rejects_missing_duplicate_reordered_and_extra_headings(self) -> None:
        valid = self.script()
        variants = [
            valid.replace("## Chapter 08: Lantern Ledger\n\nstill stars remain calm\n\n", ""),
            valid.replace("## Chapter 08: Lantern Ledger", "## Chapter 07: Quiet Meridian"),
            valid.replace("## Chapter 08: Lantern Ledger", "## Chapter 09: Dawn Watch", 1),
            valid + "\n\n## Chapter 10: Extra Chapter\n\nextra body words",
        ]
        for text in variants:
            with self.subTest(text=text[:80]):
                self.assertFalse(probe.validate_long_script(text, self.spec)["pass"])

    def test_rejects_fences_reasoning_and_fact_contradictions(self) -> None:
        valid = self.script()
        for suffix in ("\n```", "\n<think>trace</think>", "\nMara lost the brass key"):
            with self.subTest(suffix=suffix):
                self.assertFalse(probe.validate_long_script(valid + suffix, self.spec)["pass"])


class SceneLengthValidationTests(unittest.TestCase):
    def test_uses_trimmed_utf16_units_at_the_4000_character_boundary(self) -> None:
        at_cap = probe.validate_scene_json(
            scene_output("s1", " \ufeff" + "🪔" * 2000 + "\ufeff "), ["s1"], max_chars=4000
        )
        over = probe.validate_scene_json(scene_output("s1", "🪔" * 2000 + "a"), ["s1"], max_chars=4000)
        self.assertTrue(at_cap["pass"], at_cap["errors"])
        self.assertEqual(1, at_cap["length_stats"]["count"])
        self.assertEqual(4000, at_cap["length_stats"]["max_chars"])
        self.assertFalse(over["pass"])
        self.assertEqual(4001, over["length_stats"]["max_chars"])
        self.assertEqual(1, over["length_stats"]["over_cap_count"])
        self.assertFalse(probe.validate_scene_json(scene_output("s1", "\ufeff"), ["s1"], max_chars=4000)["pass"])

    def test_counts_deterministic_style_prefix_in_the_hard_cap(self) -> None:
        result = probe.validate_scene_json(
            scene_output("s1", "a" * 3996), ["s1"], max_chars=4000, style="ink"
        )
        already_prefixed = probe.validate_scene_json(
            scene_output("s1", "ink" + "a" * 3997), ["s1"], max_chars=4000, style="ink"
        )
        self.assertFalse(result["pass"])
        self.assertEqual(4001, result["length_stats"]["items"][0]["chars"])
        self.assertTrue(already_prefixed["pass"], already_prefixed["errors"])
        self.assertEqual(4000, already_prefixed["length_stats"]["items"][0]["chars"])

    def test_style_prefix_matches_story_normalization_and_punctuation(self) -> None:
        normalized = probe.validate_scene_json(
            scene_output("s1", "SOFT---PAINTERLY, a quiet room"), ["s1"], max_chars=4000,
            style="Soft Painterly!",
        )
        long_style = "Detailed historical storybook illustration with amber light and blue shadows"
        partial = probe.validate_scene_json(
            scene_output("s1", "detailed historical storybook illustration, a hill"), ["s1"],
            max_chars=4000, style=long_style,
        )
        punctuated = probe.validate_scene_json(
            scene_output("s1", "view"), ["s1"], max_chars=4000, style="ink;"
        )
        plain = probe.validate_scene_json(
            scene_output("s1", "view"), ["s1"], max_chars=4000, style="ink"
        )
        self.assertEqual(len("SOFT---PAINTERLY, a quiet room"), normalized["length_stats"]["max_chars"])
        self.assertEqual(len("detailed historical storybook illustration, a hill"), partial["length_stats"]["max_chars"])
        self.assertEqual(len("ink; view"), punctuated["length_stats"]["max_chars"])
        self.assertEqual(len("ink. view"), plain["length_stats"]["max_chars"])

    def test_unpaired_surrogate_counts_like_javascript_and_empty_stats_are_unknown(self) -> None:
        surrogate = probe.validate_scene_json(scene_output("s1", "\ud800"), ["s1"], max_chars=1)
        malformed = probe.validate_scene_json("not json", ["s1"], max_chars=4000)
        self.assertTrue(surrogate["pass"], surrogate["errors"])
        self.assertEqual(1, surrogate["length_stats"]["max_chars"])
        self.assertIsNone(malformed["length_stats"]["min_chars"])
        self.assertIsNone(malformed["length_stats"]["max_chars"])
        self.assertIsNone(malformed["length_stats"]["mean_chars"])


class WorkloadProfileTests(unittest.TestCase):
    def test_full_suite_keeps_baselines_and_adds_nested_25_and_50_scene_cases(self) -> None:
        prompts = probe.load_prompts()
        cases = probe.scene_case_specs(prompts, "full")
        self.assertEqual([12, 12, 12, 25, 50], [len(case["batch"]["scenes"]) for case in cases])
        self.assertEqual([6144, 6144, 6144, 12288, 16384], [case["max_tokens"] for case in cases])
        self.assertLessEqual(max(case["max_tokens"] for case in cases), 16384)
        bulk_ids = [row[0] for row in cases[-1]["batch"]["scenes"]]
        bulk_beats = [row[1] for row in cases[-1]["batch"]["scenes"]]
        self.assertEqual(50, len(set(bulk_ids)))
        self.assertEqual(50, len(set(bulk_beats)))
        self.assertEqual(bulk_ids[:25], [row[0] for row in cases[-2]["batch"]["scenes"]])
        self.assertIn("Write all 25 prompts", probe.scene_messages(cases[-2]["batch"])[1]["content"])

    def test_capacity_verdict_is_informational_and_lists_failed_sizes(self) -> None:
        verdict = probe.scene_batch_capacity([
            {"scene_count": 12, "mechanical_pass": True},
            {"scene_count": 25, "mechanical_pass": False},
            {"scene_count": 25, "mechanical_pass": False},
            {"scene_count": 50, "mechanical_pass": True},
        ])
        self.assertEqual(50, verdict["largest_passing_tested_batch_size"])
        self.assertEqual([25], verdict["failed_batch_sizes"])
        self.assertEqual("single-run screening; not a production reliability guarantee", verdict["scope"])


if __name__ == "__main__":
    unittest.main()
