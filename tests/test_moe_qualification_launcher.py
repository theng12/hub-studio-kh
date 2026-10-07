from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
import uuid


PROJECT = Path(__file__).resolve().parents[1]
HELPER = PROJECT / "qualification" / "moe_qualification.py"


def load_helper():
    if not HELPER.is_file():
        raise AssertionError("qualification/moe_qualification.py is not implemented")
    spec = importlib.util.spec_from_file_location("moe_qualification", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MoeQualificationLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        build = PROJECT / ".build"
        build.mkdir(exist_ok=True)
        cls.fixture_root = Path(tempfile.mkdtemp(prefix="moe-launcher-tests-", dir=build))

    @classmethod
    def tearDownClass(cls) -> None:
        archive = PROJECT / "_trash" / "tests" / "moe-qualification-fixtures"
        archive.mkdir(parents=True, exist_ok=True)
        if cls.fixture_root.exists():
            os.rename(cls.fixture_root, archive / f"{cls.fixture_root.name}-{uuid.uuid4().hex}")

    def setUp(self) -> None:
        self.module = load_helper()
        self.root = self.fixture_root / self.id().rsplit(".", 1)[-1]
        self.root.mkdir()

    def _model(self, parent: Path, name: str, revision: str, model_type: str,
               *, ssd: bool = True) -> Path:
        model = (parent / "model-library" / name / revision / "model" if ssd
                 else parent / "models" / name)
        model.mkdir(parents=True)
        (model / "config.json").write_text(json.dumps({
            "model_type": model_type,
            "quantization": {"bits": 4, "group_size": 64, "mode": "affine"},
        }), encoding="utf-8")
        (model / "model-00001-of-00001.safetensors").write_bytes(b"weights")
        (model / "model.safetensors.index.json").write_text(json.dumps({
            "weight_map": {"model.embed_tokens.weight": "model-00001-of-00001.safetensors"},
        }), encoding="utf-8")
        return model

    def _runtime(self, parent: Path, implementation: str) -> Path:
        runtime = parent / "Studio Hub.app" / "Contents" / "Resources" / "runtimes" / "voice"
        python = runtime / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        site = runtime / "lib" / "python3.12" / "site-packages"
        for package, version in (("mlx", "0.31.2"), ("mlx-lm", "0.31.3"),
                                 ("transformers", "5.14.1")):
            metadata = site / f"{package.replace('-', '_')}-{version}.dist-info" / "METADATA"
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text(f"Name: {package}\nVersion: {version}\n", encoding="utf-8")
        source = site / "mlx_lm" / "models" / f"{implementation}.py"
        source.parent.mkdir(parents=True)
        source.write_text("# static fixture\n", encoding="utf-8")
        return python

    def _helpers(self, technical: Path) -> Path:
        technical.mkdir(parents=True)
        marker = technical / "worker-launched"
        (technical / "moe_probe.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('launched')\n",
            encoding="utf-8")
        (technical / "moe-prompts.json").write_text("{}\n", encoding="utf-8")
        (technical / "moe_probe_selfcheck.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        host_memory = technical / "moe-support" / "components" / "host_memory.py"
        host_memory.parent.mkdir(parents=True)
        host_memory.write_text("def available_bytes(vm): return vm.available\n", encoding="utf-8")
        return marker

    def test_resolves_spaced_ssd_and_local_study_layouts(self) -> None:
        spec = self.module.MODELS["nemotron-lightning-4bit"]
        ssd = self.root / "Volume With Spaces" / "Studio Hub KH"
        expected_ssd = self._model(ssd, spec.name, spec.revision, spec.model_type)
        self.assertEqual(self.module.resolve_model_dir(ssd, spec), expected_ssd)

        study = self.root / "Local Study With Spaces"
        expected_local = self._model(study, spec.name, spec.revision, spec.model_type, ssd=False)
        self.assertEqual(self.module.resolve_model_dir(study, spec), expected_local)

    def test_requires_selected_exact_pin_instead_of_an_arbitrary_revision(self) -> None:
        spec = self.module.MODELS["qwen36-35b-4bit"]
        kit = self.root / "kit"
        self._model(kit, spec.name, "wrong-revision", spec.model_type)
        with self.assertRaisesRegex(self.module.QualificationError, spec.revision):
            self.module.resolve_model_dir(kit, spec)

    def test_static_check_requires_complete_model_and_runtime_source_without_importing_mlx(self) -> None:
        spec = self.module.MODELS["gemma4-26b-a4b-qat-4bit"]
        kit = self.root / "kit"
        technical = kit / "Technical"
        marker = self._helpers(technical)
        model = self._model(kit, spec.name, spec.revision, spec.model_type)
        runtime = self._runtime(self.root / "runtime", spec.model_type)

        check = self.module.static_check(spec, kit, technical, runtime)

        self.assertEqual(Path(check["model_dir"]), model)
        self.assertEqual(check["runtime_packages"]["mlx-lm"], "0.31.3")
        self.assertTrue(check["model_implementation"].endswith("/gemma4.py"))
        self.assertNotIn("mlx", sys.modules)
        self.assertNotIn("mlx_lm", sys.modules)
        self.assertFalse(marker.exists())

    def test_missing_model_and_runtime_fail_with_the_missing_path(self) -> None:
        spec = self.module.MODELS["nemotron-lightning-4bit"]
        kit = self.root / "kit"
        with self.assertRaisesRegex(self.module.QualificationError, "model-library.*55ac8c"):
            self.module.resolve_model_dir(kit, spec)
        missing_python = self.root / "Missing Studio Hub.app" / "Contents" / "Resources" / "runtimes" / "voice" / "bin" / "python3"
        with self.assertRaisesRegex(self.module.QualificationError, "voice runtime Python"):
            self.module.check_runtime(missing_python, spec)

    def test_check_mode_creates_no_results_and_never_launches_probe(self) -> None:
        spec = self.module.MODELS["qwen36-35b-4bit"]
        kit = self.root / "Kit With Spaces"
        technical = kit / "Technical"
        marker = self._helpers(technical)
        self._model(kit, spec.name, spec.revision, spec.model_type)
        runtime = self._runtime(self.root / "Runtime With Spaces", spec.model_type)
        output = self.root / "must-not-exist"
        poison = self.root / "poison"
        poison.mkdir()
        (poison / "mlx.py").write_text("raise RuntimeError('mlx imported')\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(poison))

        completed = subprocess.run([
            sys.executable, str(HELPER), "--model", spec.name, "--check",
            "--kit-root", str(kit), "--technical-root", str(technical),
            "--runtime-python", str(runtime), "--output-root", str(output),
        ], capture_output=True, text=True, env=env)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("CHECK PASSED", completed.stdout)
        self.assertFalse(output.exists())
        self.assertFalse(marker.exists())

    def test_common_advisory_lock_excludes_a_second_launcher(self) -> None:
        lock_path = self.root / "shared.lock"
        first = self.module.acquire_lock(lock_path)
        self.addCleanup(first.close)
        with self.assertRaisesRegex(self.module.QualificationError, "already running"):
            self.module.acquire_lock(lock_path)

    def test_cancellation_stops_only_the_owned_process_group(self) -> None:
        sent: list[tuple[int, int]] = []

        class FakeProcess:
            pid = 24680

            def __init__(self) -> None:
                self.waits = 0

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("fake", timeout)
                return -signal.SIGKILL

        process = FakeProcess()
        self.module.stop_owned_process_group(
            process, killpg=lambda pid, sig: sent.append((pid, sig)), timeout=0.01)
        self.assertEqual(sent, [
            (process.pid, signal.SIGTERM),
            (process.pid, signal.SIGKILL),
        ])

    def test_cleanup_checks_the_group_even_after_probe_parent_exits(self) -> None:
        sent: list[tuple[int, int]] = []

        class ExitedParent:
            pid = 13579

            def poll(self):
                return 130

            def wait(self, timeout=None):
                return 130

        self.module.stop_owned_process_group(
            ExitedParent(), killpg=lambda pid, sig: sent.append((pid, sig)),
            group_exists=lambda _pid: True)
        self.assertEqual(sent, [
            (13579, signal.SIGTERM),
            (13579, signal.SIGKILL),
        ])

    def test_summary_keeps_unknown_metrics_and_subjective_review_distinct(self) -> None:
        spec = self.module.MODELS["nemotron-lightning-4bit"]
        report = {
            "status": "pass",
            "suite": "full",
            "environment": {"physical_ram_tier_gib": 24, "physical_ram_bytes": 24 * 1024 ** 3},
            "generation_mechanical_pass": True,
            "subjective_writing_review": "not_performed",
            "cold_load": {"mlx_peak_bytes": 3 * 1024 ** 3},
            "parent_observed_peak_phys_footprint_bytes": None,
            "cases": [{
                "name": "speed", "mechanical_pass": True, "prompt_tps": None,
                "decode_tps": None, "mlx_peak_bytes": None,
                "kernel_phys_footprint_peak_bytes": None,
            }],
        }

        summary = self.module.summary_text(report, spec)

        self.assertIn("Mechanical checks: PASS", summary)
        self.assertIn("Subjective writing quality: PENDING", summary)
        self.assertIn("Physical tier only: 24 GiB", summary)
        self.assertIn("Prompt tokens/s: unknown", summary)
        self.assertIn("Decode tokens/s: unknown", summary)
        self.assertIn("Total measured prompt tokens: unknown", summary)
        self.assertIn("Total completion tokens: unknown", summary)
        self.assertIn("Peak MLX memory: 3.00 GiB", summary)
        self.assertNotIn("0.0 tokens/s", summary)

    def test_summary_exposes_long_workload_counts_caps_and_largest_observed_peak(self) -> None:
        spec = self.module.MODELS["gemma4-26b-a4b-qat-4bit"]
        report = {
            "parent_observed_peak_phys_footprint_bytes": 18 * 1024 ** 3,
            "scene_batch_capacity": {"largest_passing_tested_batch_size": 25,
                                     "failed_batch_sizes": [50]},
            "cases": [
                {"name": "long-script", "kernel_phys_footprint_peak_bytes": 19 * 1024 ** 3,
                 "validation": {"chapters": [{"number": 7, "word_count": 1205, "pass": True}]}},
                {"name": "scenes-bulk-50", "validation": {"count": 50,
                 "length_stats": {"min_chars": 350, "max_chars": 4001, "mean_chars": 700,
                                  "cap": 4000, "over_cap_count": 1}}},
            ],
        }
        summary = self.module.summary_text(report, spec)
        self.assertIn("Largest observed process footprint: 19.00 GiB", summary)
        self.assertIn("Largest passing tested scene batch: 25", summary)
        self.assertIn("Failed scene batch sizes: 50", summary)
        self.assertIn("Chapter 07: 1205 words (PASS)", summary)
        self.assertIn("Scene prompts: 50; characters min/mean/max: 350 / 700 / 4001", summary)
        self.assertIn("Character cap: 4000; over cap: 1", summary)


if __name__ == "__main__":
    unittest.main()
