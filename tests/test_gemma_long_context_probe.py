from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
QUALIFICATION = PROJECT / "qualification"
MOE_QUALIFICATION = QUALIFICATION / "moe_qualification.py"
MOE_PROBE = QUALIFICATION / "moe_probe.py"
COMMAND_FILE = QUALIFICATION / "RUN-GEMMA4-LONG-CONTEXT.command"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class GemmaLongContextProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_module("moe_probe_test_mod", MOE_PROBE)
        cls.qualification = _load_module("moe_qualification_test_mod", MOE_QUALIFICATION)

    def test_eval_stop_rule_safe_advances_last_safe_size(self) -> None:
        limit_bytes = int(26.0 * self.probe.GIB)
        row = {
            "name": "input-48k",
            "kernel_phys_footprint_peak_bytes": int(22.5 * self.probe.GIB),
            "swap_delta_bytes": 0,
        }
        should_stop, stop_reason, last_safe = self.probe.eval_stop_rule(row, limit_bytes, "none")
        self.assertFalse(should_stop)
        self.assertEqual(stop_reason, "safe")
        self.assertEqual(last_safe, "input-48k")

    def test_eval_stop_rule_stops_on_swap_growth(self) -> None:
        limit_bytes = int(26.0 * self.probe.GIB)
        row = {
            "name": "input-48k",
            "kernel_phys_footprint_peak_bytes": int(20.0 * self.probe.GIB),
            "swap_delta_bytes": 5 * 1024 * 1024,
        }
        should_stop, stop_reason, last_safe = self.probe.eval_stop_rule(row, limit_bytes, "none")
        self.assertTrue(should_stop)
        self.assertIn("swap grew by 5242880 bytes", stop_reason)
        self.assertIn("at input-48k", stop_reason)
        self.assertEqual(last_safe, "none")

    def test_eval_stop_rule_stops_on_footprint_exceeded(self) -> None:
        limit_bytes = int(26.0 * self.probe.GIB)
        row = {
            "name": "input-64k",
            "kernel_phys_footprint_peak_bytes": int(27.8 * self.probe.GIB),
            "swap_delta_bytes": 0,
        }
        should_stop, stop_reason, last_safe = self.probe.eval_stop_rule(row, limit_bytes, "input-48k")
        self.assertTrue(should_stop)
        self.assertIn("peak process footprint of 27.80 GiB exceeded 26.00 GiB limit", stop_reason)
        self.assertIn("at input-64k", stop_reason)
        self.assertEqual(last_safe, "input-48k")

    def test_eval_stop_rule_stops_on_both_swap_and_footprint(self) -> None:
        limit_bytes = int(26.0 * self.probe.GIB)
        row = {
            "name": "input-48k",
            "kernel_phys_footprint_peak_bytes": int(27.0 * self.probe.GIB),
            "swap_delta_bytes": 10 * 1024 * 1024,
        }
        should_stop, stop_reason, last_safe = self.probe.eval_stop_rule(row, limit_bytes, "none")
        self.assertTrue(should_stop)
        self.assertIn("peak process footprint of 27.00 GiB exceeded 26.00 GiB limit", stop_reason)
        self.assertIn("swap grew by 10485760 bytes", stop_reason)
        self.assertEqual(last_safe, "none")

    def test_eval_stop_rule_respects_custom_threshold(self) -> None:
        custom_limit_bytes = int(20.0 * self.probe.GIB)
        row = {
            "name": "input-48k",
            "kernel_phys_footprint_peak_bytes": int(21.5 * self.probe.GIB),
            "swap_delta_bytes": 0,
        }
        should_stop, stop_reason, last_safe = self.probe.eval_stop_rule(row, custom_limit_bytes, "none")
        self.assertTrue(should_stop)
        self.assertIn("exceeded 20.00 GiB limit", stop_reason)
        self.assertEqual(last_safe, "none")

    def test_validate_long_context_passes_complete_output(self) -> None:
        spec = {"expected_sections": 4, "needle": "HARBOR LANTERN"}
        text = (
            "## Section 01: Initial Observations\n"
            "At dawn the harbor master noted favorable conditions across the bay.\n\n"
            "## Section 02: Tide and Current Tracking\n"
            "The low water mark held steady with no unusual surge recorded.\n\n"
            "## Section 03: Meteorological Synthesis\n"
            "Barometric pressure remained constant throughout the early watch.\n\n"
            "## Section 04: Summary and Verification\n"
            "All readings confirmed. Secret Key: HARBOR LANTERN"
        )
        res = self.probe.validate_long_context(text, spec)
        self.assertTrue(res["pass"])
        self.assertEqual(res["sections_count"], 4)
        self.assertTrue(res["needle_found"])
        self.assertEqual(len(res["errors"]), 0)

    def test_validate_long_context_fails_missing_needle(self) -> None:
        spec = {"expected_sections": 2, "needle": "HARBOR LANTERN"}
        text = (
            "## Section 01: Observation\nValid text here.\n\n"
            "## Section 02: Verification\nNo secret key provided."
        )
        res = self.probe.validate_long_context(text, spec)
        self.assertFalse(res["pass"])
        self.assertFalse(res["needle_found"])
        self.assertTrue(any("needle 'HARBOR LANTERN' not found" in err for err in res["errors"]))

    def test_validate_long_context_fails_missing_sections(self) -> None:
        spec = {"expected_sections": 5, "needle": "HARBOR LANTERN"}
        text = (
            "## Section 01: Observation\nValid text.\n\n"
            "## Section 02: Verification\nHARBOR LANTERN"
        )
        res = self.probe.validate_long_context(text, spec)
        self.assertFalse(res["pass"])
        self.assertEqual(res["sections_count"], 2)
        self.assertTrue(any("expected 5 sections, got 2" in err for err in res["errors"]))

    def test_validate_long_context_fails_on_think_spill(self) -> None:
        spec = {"expected_sections": 2, "needle": "HARBOR LANTERN"}
        text = (
            "<think>internal reasoning trace</think>\n"
            "## Section 01: Observation\nValid text.\n\n"
            "## Section 02: Verification\nHARBOR LANTERN"
        )
        res = self.probe.validate_long_context(text, spec)
        self.assertFalse(res["pass"])
        self.assertTrue(any("thinking trace marker leaked" in err for err in res["errors"]))

    def test_summary_text_records_all_long_context_fields_and_stop_rule(self) -> None:
        spec = self.qualification.MODELS["gemma4-26b-a4b-qat-4bit"]
        report = {
            "status": "pass",
            "suite": "long-context",
            "environment": {
                "physical_ram_tier_gib": 32,
                "physical_ram_bytes": 32 * 1024 ** 3,
                "available_memory_bytes_at_start": 24 * 1024 ** 3,
            },
            "generation_mechanical_pass": True,
            "subjective_writing_review": "not_performed",
            "cold_load": {
                "seconds": 8.12,
                "mlx_peak_bytes": int(14.5 * self.probe.GIB),
                "kernel_phys_footprint_peak_bytes": int(15.8 * self.probe.GIB),
            },
            "stop_rule": {
                "max_footprint_bytes": int(26.0 * self.probe.GIB),
                "max_footprint_gib": 26.0,
                "stopped_early": True,
                "last_safe_size": "input-48k",
                "stop_reason": "stopped before next size: peak process footprint of 27.50 GiB exceeded 26.00 GiB limit at input-64k",
            },
            "cases": [
                {
                    "name": "input-48k",
                    "mechanical_pass": True,
                    "answer_complete": True,
                    "finish_reason": "stop",
                    "elapsed_seconds": 95.32,
                    "prefill_seconds": 28.14,
                    "generate_seconds": 67.18,
                    "prompt_tokens_measured": 49152,
                    "completion_tokens": 4018,
                    "prompt_tps": 1746.5,
                    "decode_tps": 59.8,
                    "mlx_peak_bytes": int(21.2 * self.probe.GIB),
                    "kernel_phys_footprint_peak_bytes": int(23.1 * self.probe.GIB),
                    "memory_pressure": "normal",
                    "swap_delta_bytes": 0,
                    "free_memory_at_start_bytes": int(24.0 * self.probe.GIB),
                    "validation": {
                        "pass": True,
                        "sections_count": 16,
                        "expected_sections": 16,
                        "needle_found": True,
                        "needle": "HARBOR LANTERN",
                    },
                },
                {
                    "name": "input-64k",
                    "mechanical_pass": True,
                    "answer_complete": True,
                    "finish_reason": "stop",
                    "elapsed_seconds": 128.45,
                    "prefill_seconds": 41.20,
                    "generate_seconds": 87.25,
                    "prompt_tokens_measured": 65536,
                    "completion_tokens": 4022,
                    "prompt_tps": 1590.2,
                    "decode_tps": 46.1,
                    "mlx_peak_bytes": int(25.1 * self.probe.GIB),
                    "kernel_phys_footprint_peak_bytes": int(27.5 * self.probe.GIB),
                    "memory_pressure": "warning",
                    "swap_delta_bytes": 0,
                    "free_memory_at_start_bytes": int(24.0 * self.probe.GIB),
                    "validation": {
                        "pass": True,
                        "sections_count": 16,
                        "expected_sections": 16,
                        "needle_found": True,
                        "needle": "HARBOR LANTERN",
                    },
                },
            ],
        }

        summary = self.qualification.summary_text(report, spec)

        # Stop rule block verification
        self.assertIn("Long-Context Stop Rule Evaluation:", summary)
        self.assertIn("Suite: long-context", summary)
        self.assertIn("Peak footprint stop threshold: 26.00 GiB", summary)
        self.assertIn("Last safe context size: input-48k", summary)
        self.assertIn("stopped before next size: peak process footprint of 27.50 GiB exceeded 26.00 GiB limit at input-64k", summary)
        self.assertIn("Stopped early: YES", summary)

        # Per-size case verification for input-48k
        self.assertIn("Case: input-48k", summary)
        self.assertIn("Mechanical: PASS", summary)
        self.assertIn("Answer complete: YES", summary)
        self.assertIn("Finish reason: stop", summary)
        self.assertIn("Prefill time: 28.14 seconds", summary)
        self.assertIn("Generate time: 67.18 seconds", summary)
        self.assertIn("Prompt tokens: 49152", summary)
        self.assertIn("Completion tokens: 4018", summary)
        self.assertIn("Peak MLX memory: 21.20 GiB", summary)
        self.assertIn("Peak process footprint: 23.10 GiB", summary)
        self.assertIn("Memory pressure: normal", summary)
        self.assertIn("Swap delta: 0.00 GiB", summary)
        self.assertIn("Free memory at start: 24.00 GiB", summary)
        self.assertIn("Sections: 16/16; needle 'HARBOR LANTERN': recalled", summary)

        # input-64k verification
        self.assertIn("Case: input-64k", summary)
        self.assertIn("Peak process footprint: 27.50 GiB", summary)
        self.assertIn("Memory pressure: warning", summary)

    def test_summary_text_when_all_sizes_safe(self) -> None:
        spec = self.qualification.MODELS["gemma4-26b-a4b-qat-4bit"]
        report = {
            "status": "pass",
            "suite": "long-context",
            "environment": {"physical_ram_tier_gib": 32, "physical_ram_bytes": 32 * 1024 ** 3},
            "generation_mechanical_pass": True,
            "stop_rule": {
                "max_footprint_bytes": int(26.0 * self.probe.GIB),
                "max_footprint_gib": 26.0,
                "stopped_early": False,
                "last_safe_size": "input-64k",
                "stop_reason": "all tested sizes completed within safe limits",
            },
            "cases": [],
        }
        summary = self.qualification.summary_text(report, spec)
        self.assertIn("Last safe context size: input-64k", summary)
        self.assertIn("Stop reason: all tested sizes completed within safe limits", summary)
        self.assertIn("Stopped early: NO", summary)

    def test_summary_text_when_first_size_stopped(self) -> None:
        spec = self.qualification.MODELS["gemma4-26b-a4b-qat-4bit"]
        report = {
            "status": "fail",
            "suite": "long-context",
            "environment": {"physical_ram_tier_gib": 32, "physical_ram_bytes": 32 * 1024 ** 3},
            "generation_mechanical_pass": False,
            "stop_rule": {
                "max_footprint_bytes": int(26.0 * self.probe.GIB),
                "max_footprint_gib": 26.0,
                "stopped_early": True,
                "last_safe_size": "none",
                "stop_reason": "stopped before next size: peak process footprint of 26.50 GiB exceeded 26.00 GiB limit at input-48k",
            },
            "cases": [],
        }
        summary = self.qualification.summary_text(report, spec)
        self.assertIn("Last safe context size: none", summary)
        self.assertIn("Stop reason: stopped before next size: peak process footprint of 26.50 GiB exceeded 26.00 GiB limit at input-48k", summary)

    def test_launcher_parse_args_accepts_long_context_suite_and_max_footprint(self) -> None:
        args = self.qualification.parse_args([
            "--model", "gemma4-26b-a4b-qat-4bit",
            "--kit-root", "/tmp/kit",
            "--technical-root", "/tmp/technical",
            "--runtime-python", "/tmp/python3",
            "--suite", "long-context",
            "--max-footprint-gb", "24.5",
        ])
        self.assertEqual(args.suite, "long-context")
        self.assertEqual(args.max_footprint_gb, 24.5)

    def test_probe_parse_args_accepts_long_context_suite_and_max_footprint(self) -> None:
        args = self.probe.parse_args([
            "--model-dir", "/tmp/model",
            "--output-dir", "/tmp/output",
            "--suite", "long-context",
            "--max-footprint-gb", "28.0",
        ])
        self.assertEqual(args.suite, "long-context")
        self.assertEqual(args.max_footprint_gb, 28.0)

    def test_command_file_exists_and_executable(self) -> None:
        self.assertTrue(COMMAND_FILE.is_file(), f"{COMMAND_FILE} must exist")
        mode = COMMAND_FILE.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR), "file must have user executable bit set")
        content = COMMAND_FILE.read_text(encoding="utf-8")
        self.assertIn("#!/bin/sh", content)
        self.assertIn("moe-qualification.sh", content)
        self.assertIn("gemma4-26b-a4b-qat-4bit", content)
        self.assertIn("--suite long-context", content)


if __name__ == "__main__":
    unittest.main()


class HubBusyGuardTests(unittest.TestCase):
    """The long-context run refuses while Studio Hub could take Gateway work."""

    def _run(self, pgrep_code, ready=None):
        import json, os, subprocess, tempfile
        from unittest import mock
        mq = _load_module("moe_qualification_guard_mod", MOE_QUALIFICATION)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            if ready is not None:
                (data / "hub-ready.json").write_text(json.dumps(ready))
            done = subprocess.CompletedProcess([], pgrep_code, "", "")
            with mock.patch.object(mq, "HUB_DATA", data), \
                 mock.patch.object(mq.subprocess, "run", return_value=done):
                return mq.hub_busy_reason()

    def test_open_app_refuses(self):
        self.assertIn("app is open", self._run(0))

    def test_live_services_refuse(self):
        import os
        self.assertIn("still running", self._run(1, {"pid": os.getpid()}))

    def test_quit_hub_with_stale_ready_file_runs(self):
        self.assertIsNone(self._run(1, {"pid": 999999}))

    def test_quit_hub_without_ready_file_runs(self):
        self.assertIsNone(self._run(1))

    def test_unrunnable_check_refuses(self):
        self.assertIn("could not check", self._run(2))

    def test_live_run_refuses_before_the_lock(self):
        from unittest import mock
        mq = _load_module("moe_qualification_guard_mod", MOE_QUALIFICATION)
        args = mq.parse_args(["--model", "gemma4-26b-a4b-qat-4bit", "--kit-root", "/tmp",
                              "--technical-root", "/tmp", "--runtime-python", "/tmp/x",
                              "--suite", "long-context"])
        with mock.patch.object(mq, "hub_busy_reason", return_value="the Studio Hub app is open"), \
             mock.patch.object(mq, "acquire_lock") as lock:
            with self.assertRaises(mq.QualificationError):
                mq.run_live(args, mq.MODELS[args.model])
        lock.assert_not_called()

    def test_installed_library_is_searched(self):
        import json, tempfile
        from unittest import mock
        mq = _load_module("moe_qualification_guard_mod", MOE_QUALIFICATION)
        spec = mq.MODELS["gemma4-26b-a4b-qat-4bit"]
        with tempfile.TemporaryDirectory() as tmp:
            data, lib = Path(tmp) / "data", Path(tmp) / "lib"
            data.mkdir()
            (data / "app-preferences.json").write_text(json.dumps({"modelsPath": str(lib)}))
            model = lib / spec.name / spec.revision / "model"
            model.mkdir(parents=True)
            with mock.patch.object(mq, "HUB_DATA", data):
                self.assertEqual(mq.resolve_model_dir(Path(tmp) / "nokit", spec), model)
