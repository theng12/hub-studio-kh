from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

from qualification import qwen_image_qualification as qwen


class QwenImageQualificationTests(unittest.TestCase):
    def _runtime_manifest(self, runtime: Path) -> None:
        records = []
        for path in sorted(runtime.rglob("*")):
            relative = path.relative_to(runtime).as_posix()
            if relative == "manifest/runtime-files.json" or path.is_dir():
                continue
            if path.is_symlink():
                records.append({"path": relative, "type": "symlink",
                                "target": os.readlink(path),
                                "mode": f"{path.lstat().st_mode & 0o7777:04o}"})
            else:
                records.append({"path": relative, "type": "file",
                                "sha256": qwen.sha256_file(path), "size": path.stat().st_size,
                                "mode": f"{path.stat().st_mode & 0o7777:04o}"})
        manifest = runtime / "manifest/runtime-files.json"
        manifest.parent.mkdir(exist_ok=True)
        manifest.write_text(json.dumps({"schema": "fixture", "files": records}))

    def test_available_memory_tracker_records_admission_headroom_start_minimum_and_end(self) -> None:
        values = iter((8_500_000_000, 6_250_000_000, 7_100_000_000))
        tracker = qwen.AvailableMemoryTracker(lambda: next(values))

        tracker.sample()
        tracker.finish()

        self.assertEqual(tracker.fields(), {
            "available_gb_start": 8.5,
            "available_gb_min": 6.25,
            "available_gb_end": 7.1,
            "available_memory_samples": 3,
            "available_memory_sample_errors": 0,
        })

    def test_available_memory_reader_uses_the_pinned_apps_host_memory_formula(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app = Path(raw) / "Hub Studio KH.app"
            components = app / "Contents/Resources/components"
            components.mkdir(parents=True)
            (components / "host_memory.py").write_text(
                "def available_bytes(vm):\n    return vm.available + 123\n")
            psutil = SimpleNamespace(
                virtual_memory=lambda: SimpleNamespace(available=4_000_000_000))
            with patch.dict("sys.modules", {"psutil": psutil}):
                reader = qwen.available_memory_reader(app)

            self.assertEqual(reader(), 4_000_000_123)

    def test_foreign_process_guard_finds_hub_image_and_native_leaf(self) -> None:
        listing = """\
  100 /usr/bin/safe-helper
  101 /Applications/Hub Studio KH.app/Contents/Resources/hub_worker.py --port 0
  102 /Applications/Hub Studio KH.app/Contents/Resources/image_worker.py --port 0
  103 /Applications/Hub Studio KH.app/Contents/Resources/runtimes/image/mlx-serve/mlx-serve --serve
  104 /tmp/qwen_image_qualification.py --tier 24
"""
        found = qwen.foreign_model_processes(listing, current_pid=104)

        self.assertEqual([row.split(":", 1)[0] for row in found],
                         ["pid 101", "pid 102", "pid 103"])

    def test_spawn_rechecks_for_a_hub_restart_before_starting_private_worker(self) -> None:
        listing = "  201 /Applications/Hub Studio KH.app/Contents/Resources/hub_worker.py --port 0\n"

        def must_not_spawn(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("private worker started while the Hub was running")

        with self.assertRaisesRegex(qwen.ForeignProcessError, "hub_worker.py"):
            qwen.spawn_private_worker(
                ["private-worker"], stdout=subprocess.DEVNULL, cwd=Path("/tmp"), env={},
                process_listing=listing, popen=must_not_spawn)

    def test_partial_or_duplicate_workloads_cannot_pass_qualification(self) -> None:
        complete = {"name": "cold-square", "status": "completed", "memory_metrics_complete": True,
                    "memory_pressure": "normal", "responsive": True, "swap_peak_delta_gb": 0}
        expected = ["cold-square", "warm-square"]
        self.assertEqual(qwen.outcome([complete], expected_names=expected), "NOT MEASURED")
        self.assertEqual(qwen.outcome([complete, complete], expected_names=expected), "NOT MEASURED")
        warm = {**complete, "name": "warm-square"}
        self.assertEqual(qwen.outcome([complete, warm], expected_names=expected, target_tier_verified=True),
                         "CANDIDATE PASSED THIS RUN")

    def test_non_target_success_is_explicitly_not_qualified(self) -> None:
        row = {"name": "cold-square", "status": "completed", "memory_metrics_complete": True,
               "memory_pressure": "normal", "responsive": True, "swap_peak_delta_gb": 0}
        self.assertEqual(qwen.outcome([row], target_tier_verified=False),
                         "NON-TARGET TRIAL COMPLETED")
        self.assertEqual(qwen.outcome([row], target_tier_verified=True),
                         "CANDIDATE PASSED THIS RUN")
        self.assertEqual(qwen.exit_status("NON-TARGET TRIAL COMPLETED"), 3)
        self.assertEqual(qwen.exit_status("CANDIDATE PASSED THIS RUN"), 0)

    def test_profiles_keep_the_two_quantized_artifacts_distinct(self) -> None:
        normal = qwen.profile_for_tier(24)
        low = qwen.profile_for_tier(16)

        self.assertEqual(normal.package_id, "qwen-image-2-1-mlx-serve-4bit")
        self.assertEqual(normal.repo, "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")
        self.assertEqual(normal.revision, "88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38")
        self.assertEqual(normal.quantization, "MLX affine 4-bit")
        self.assertEqual(normal.minimum_macos, (26, 2))
        self.assertEqual(low.package_id, "qwen-image-2-1-mflux-q4")
        self.assertEqual(low.repo, "mlx-community/Qwen-Image-2.1-mflux-q4")
        self.assertEqual(low.revision, "746a58556820933a2df5c75887a2570f1ad200c0")
        self.assertEqual(low.quantization, "MLX affine Q4")
        self.assertEqual(low.minimum_macos, (14, 0))

    def test_eight_gb_and_wrong_tier_are_not_measured(self) -> None:
        status = qwen.host_status(qwen.profile_for_tier(16), 8 * 1024**3, (26, 6, 2))
        self.assertEqual(status.code, "NOT MEASURED")
        self.assertIn("8 GB", status.reason)
        self.assertNotIn("DOES NOT FIT", status.reason)

        wrong = qwen.host_status(qwen.profile_for_tier(24), 16 * 1024**3, (26, 6, 2))
        self.assertEqual(wrong.code, "NOT MEASURED")
        self.assertIn("24 GB", wrong.reason)

    def test_a_non_target_run_cannot_claim_the_physical_ram_tier(self) -> None:
        profile = qwen.profile_for_tier(24)
        wrong = qwen.host_status(profile, 18 * qwen.GIB, (27, 0))
        self.assertEqual(wrong.code, "NOT MEASURED")
        trial = qwen.host_status(profile, 32 * qwen.GIB, (27, 0), allow_non_target=True)
        self.assertEqual(trial.code, "READY")
        self.assertFalse(trial.target_tier_verified)
        self.assertIn("does not qualify 24 GB", trial.reason)
        physical = qwen.host_status(profile, 24 * qwen.GIB, (27, 0))
        self.assertTrue(physical.target_tier_verified)

    def test_native_profile_is_not_measured_on_an_older_macos(self) -> None:
        status = qwen.host_status(qwen.profile_for_tier(24), 24 * 1024**3, (15, 7))
        self.assertEqual(status.code, "NOT MEASURED")
        self.assertIn("macOS 26.2", status.reason)

        low = qwen.host_status(qwen.profile_for_tier(16), 16 * 1024**3, (14, 0))
        self.assertEqual(low.code, "READY")

    def test_workloads_cover_cold_warm_square_landscape_and_portrait(self) -> None:
        cases = qwen.workloads(stress_40=False)
        self.assertEqual(
            [(case.name, case.width, case.height, case.steps, case.process_cold) for case in cases],
            [("cold-square", 1024, 1024, 16, True),
             ("warm-square", 1024, 1024, 16, False),
             ("warm-landscape", 1280, 720, 16, False),
             ("warm-portrait", 720, 1280, 16, False)],
        )
        stress = qwen.workloads(stress_40=True)
        self.assertEqual([(case.width, case.height, case.steps) for case in stress[-3:]],
                         [(1024, 1024, 40), (1280, 720, 40), (720, 1280, 40)])

    def test_bundle_identity_must_match_the_pinned_package(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app = Path(raw) / "Hub Studio KH.app"
            resources = app / "Contents/Resources"
            (resources / "model-manifests").mkdir(parents=True)
            (resources / "runtimes/image/bin").mkdir(parents=True)
            (resources / "image_worker.py").write_text("# worker\n")
            python = resources / "runtimes/image/bin/python3"
            python.write_text("#!/bin/sh\n")
            python.chmod(0o755)
            self._runtime_manifest(resources / "runtimes/image")
            runtime_sha256 = qwen.runtime_tree_digest(resources / "runtimes/image")
            release = {
                "version": "0.6.150",
                "components": {"image": {"runtime_sha256": runtime_sha256}},
            }
            (resources / "release-manifest.json").write_text(json.dumps(release))
            profile = qwen.profile_for_tier(16)
            manifest = {
                "package_id": profile.package_id,
                "model_id": profile.repo,
                "revision": profile.revision,
                "quantization": profile.quantization,
            }
            (resources / "model-manifests" / f"{profile.package_id}.json").write_text(
                json.dumps(manifest))

            identity = qwen.validate_bundle(app, profile)
            self.assertEqual(identity["version"], "0.6.150")
            self.assertEqual(identity["runtime_sha256"], runtime_sha256)
            self.assertTrue(identity["runtime_files_verified"])

            manifest["revision"] = "wrong"
            (resources / "model-manifests" / f"{profile.package_id}.json").write_text(
                json.dumps(manifest))
            with self.assertRaisesRegex(SystemExit, "revision"):
                qwen.validate_bundle(app, profile)

    def test_bundle_identity_rejects_runtime_file_corruption_and_wrong_tree_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app = Path(raw) / "Hub Studio KH.app"
            resources = app / "Contents/Resources"
            runtime = resources / "runtimes/image"
            (runtime / "bin").mkdir(parents=True)
            (resources / "model-manifests").mkdir(parents=True)
            (resources / "image_worker.py").write_text("# worker\n")
            python = runtime / "bin/python3"
            python.write_text("#!/bin/sh\n")
            python.chmod(0o755)
            self._runtime_manifest(runtime)
            profile = qwen.profile_for_tier(16)
            (resources / "model-manifests" / f"{profile.package_id}.json").write_text(json.dumps({
                "package_id": profile.package_id, "model_id": profile.repo,
                "revision": profile.revision, "quantization": profile.quantization,
            }))
            release_path = resources / "release-manifest.json"
            release_path.write_text(json.dumps({
                "version": "fixture",
                "components": {"image": {"runtime_sha256": qwen.runtime_tree_digest(runtime)}},
            }))

            python.write_text("corrupt\n")
            with self.assertRaisesRegex(SystemExit, "runtime-files.json does not match"):
                qwen.validate_bundle(app, profile)

            python.write_text("#!/bin/sh\n")
            python.chmod(0o755)
            release_path.write_text(json.dumps({
                "version": "fixture", "components": {"image": {"runtime_sha256": "a" * 64}},
            }))
            with self.assertRaisesRegex(SystemExit, "runtime tree SHA-256"):
                qwen.validate_bundle(app, profile)

    def test_memory_metrics_grade_peak_swap_growth_and_keep_end_delta(self) -> None:
        sampler = SimpleNamespace(
            peak_tree_bytes=7_500_000_000, peak_process_bytes=7_000_000_000,
            tree_sample_count=4, footprint_sample_errors=1,
            pressure=2, pressure_samples=4, pressure_sample_errors=0,
            swap_peak=2.1, swap_samples=4, swap_sample_errors=0,
            free_min=34, max_stall_s=1.25,
            available=SimpleNamespace(fields=lambda: {
                "available_gb_start": 8.2, "available_gb_min": 1.3,
                "available_gb_end": 6.4, "available_memory_samples": 6,
                "available_memory_sample_errors": 0,
            }),
        )

        fields = qwen.memory_measurement_fields(sampler, swap_before=0.4, swap_after=0.7)

        self.assertTrue(fields["memory_metrics_complete"])
        self.assertEqual(fields["swap_peak_delta_gb"], 1.7)
        self.assertEqual(fields["swap_end_delta_gb"], 0.3)
        self.assertEqual(fields["swap_delta_gb"], 1.7)

    def test_missing_memory_metrics_cannot_become_a_ram_pass(self) -> None:
        sampler = SimpleNamespace(
            peak_tree_bytes=0, peak_process_bytes=0,
            tree_sample_count=0, footprint_sample_errors=3,
            pressure=None, pressure_samples=0, pressure_sample_errors=3,
            swap_peak=None, swap_samples=0, swap_sample_errors=3,
            free_min=None, max_stall_s=0.2,
            available=SimpleNamespace(fields=lambda: {
                "available_gb_start": None, "available_gb_min": None,
                "available_gb_end": None, "available_memory_samples": 0,
                "available_memory_sample_errors": 3,
            }),
        )

        fields = qwen.memory_measurement_fields(sampler, swap_before=None, swap_after=None)
        row = {"status": "completed", "responsive": True, **fields}

        self.assertFalse(fields["memory_metrics_complete"])
        self.assertIn("process_tree_peak", fields["memory_metric_errors"])
        self.assertIn("pressure", fields["memory_metric_errors"])
        self.assertIn("swap", fields["memory_metric_errors"])
        self.assertIn("available_memory", fields["memory_metric_errors"])
        self.assertEqual(qwen.outcome([row]), "NOT MEASURED")

    def test_private_clone_is_small_and_does_not_change_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            package = source / "qwen-image-2-1-mflux-q4"
            (package / "rev/model").mkdir(parents=True)
            (package / "rev/model/config.json").write_text("{}")
            (source / ".studiohub").mkdir()
            (source / ".studiohub/installed.json").write_text(json.dumps({
                package.name: {"revision": "rev", "state": "files_verified"},
            }))
            private = root / "private"
            old = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = str(root)
            try:
                cloned = qwen.clone_package(source, private, package.name)
            finally:
                if old is None:
                    os.environ.pop("TMPDIR", None)
                else:
                    os.environ["TMPDIR"] = old

            self.assertEqual((cloned / package.name / "rev/model/config.json").read_text(), "{}")
            self.assertEqual((source / ".studiohub/installed.json").read_text(), json.dumps({
                package.name: {"revision": "rev", "state": "files_verified"},
            }))

    def test_worker_command_uses_the_packaged_image_path_and_private_paths(self) -> None:
        app = Path("/fixture/Kit/Hub Studio KH.app")
        data = Path("/fixture/Kit/_qualification-private/run/state")
        library = Path("/fixture/Kit/_qualification-private/run/library")
        token = data / "owner.token"
        ready = data / "worker-ready.json"
        command = qwen.worker_command(app, data, library, token, ready, 123)
        self.assertEqual(command[0], str(app / "Contents/Resources/runtimes/image/bin/python3"))
        self.assertIn(str(app / "Contents/Resources/image_worker.py"), command)
        self.assertIn(str(library), command)
        self.assertIn("--offline", command)

    def test_worker_readiness_uses_the_private_qualification_token(self) -> None:
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
                seen.append(self.headers.get("X-Hub-Token"))
                if seen[-1] != "qualification-token":
                    self.send_response(401)
                    self.end_headers()
                    return
                body = b'{"ready": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                ready = Path(raw) / "ready.json"
                ready.write_text(json.dumps({
                    "url": f"http://127.0.0.1:{server.server_port}",
                    "pid": 123, "version": "test", "token": "live-controller-token",
                }))

                class Running:
                    returncode = None

                    @staticmethod
                    def poll() -> None:
                        return None

                base, value = qwen.wait_ready(Running(), ready, "qualification-token", timeout=2)
            self.assertEqual(base, f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(value["token"], "live-controller-token")
            self.assertEqual(seen, ["qualification-token"])
        finally:
            server.shutdown()
            server.server_close()

    def test_cleanup_signals_only_the_worker_owned_process_group(self) -> None:
        with patch.object(qwen.os, "getpgid", return_value=41):
            self.assertTrue(qwen.owns_process_group(41))
            self.assertFalse(qwen.owns_process_group(42))

    def test_double_click_commands_launch_the_kit_relative_runner_for_the_right_tier(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as raw:
            kit = Path(raw) / "Portable Kit"
            technical = kit / "Technical"
            runtime = kit / "Hub Studio KH.app/Contents/Resources/runtimes/image/bin"
            technical.mkdir(parents=True)
            runtime.mkdir(parents=True)
            fake_python = runtime / "python3"
            fake_python.write_text('#!/bin/sh\necho WRONG_IMAGE_COLLECTOR\nexit 8\n')
            collector = kit / "Hub Studio KH.app/Contents/Resources/runtimes/voice/bin/python3"
            collector.parent.mkdir(parents=True)
            collector.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            collector.chmod(0o755)
            fake_python.chmod(0o755)
            for name in ("qwen-image-qualification.sh", "qwen_image_qualification.py",
                         "library_guard.py", "ssd_wear.py", "RUN-QWEN-IMAGE-16GB.command",
                         "RUN-QWEN-IMAGE-24GB.command"):
                shutil.copy2(root / "qualification" / name, technical / name)
            environment = {**os.environ, "STUDIOHUB_APP": str(kit / "Hub Studio KH.app")}
            for tier in (16, 24):
                completed = subprocess.run(
                    [str(technical / f"RUN-QWEN-IMAGE-{tier}GB.command")],
                    capture_output=True, text=True, env=environment, timeout=15)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(str(technical / "qwen_image_qualification.py"), completed.stdout)
                self.assertIn(f"--tier\n{tier}\n", completed.stdout)
                self.assertIn(f"--kit\n{kit}\n", completed.stdout)


if __name__ == "__main__":
    unittest.main()
