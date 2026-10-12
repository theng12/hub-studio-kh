from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from qualification import qwen_image_qualification as qwen


ROOT = Path(__file__).resolve().parents[1]


def load_image_worker():
    path = ROOT / "packaging/image_worker.py"
    spec = importlib.util.spec_from_file_location("turbo_qualification_image_worker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QwenTurboQualificationTests(unittest.TestCase):
    def test_cancellation_probe_waits_for_middle_denoise_receipt_before_delete(self) -> None:
        profile = qwen.profile_for_tier(16, turbo=True)
        calls = []
        running = {"id": "job1", "state": "running", "progress": 0.5,
                   "denoise_step": 4, "denoise_steps": 8}
        cancelled = {**running, "state": "cancelled", "finished_at": 123.0,
                     "denoise_cancel_observed": True}

        def request(_base, _token, method, path, payload=None, **_kwargs):
            calls.append((method, path))
            if method == "POST" and path == "/api/generate/txt2img":
                return 200, {"job": {"id": "job1", "state": "queued"}}
            if method == "GET" and path == "/api/generate/jobs/job1":
                return (200, {"job": running}) if calls.count((method, path)) == 1 else (200, {"job": cancelled})
            if method == "DELETE":
                return 200, {"job": running}
            if method == "GET" and path.endswith("/image"):
                return 404, {"detail": "final image unavailable"}
            if method == "GET" and path == "/api/memory-policy":
                return 200, {"busy": False, "active_work": False, "loaded_models": [], "resident": False}
            if method == "POST" and path == "/api/memory/release":
                return 200, {"released": False, "busy": False, "loaded_models": []}
            if method == "GET" and path == "/api/health":
                return 200, {"status": "ok"}
            raise AssertionError((method, path, payload))

        with TemporaryDirectory() as temporary, patch.object(qwen, "request_json", side_effect=request):
            receipt = qwen.run_cancellation_probe(
                "http://private", "token", profile, "prompt", 42, Path(temporary),
                deadline=1.0, poll_interval=0.0,
            )

        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["trigger"]["denoise_step"], 4)
        self.assertEqual(receipt["cancel_request_ack"]["state"], "running")
        self.assertEqual(receipt["terminal_state"], "cancelled")
        self.assertEqual(receipt["artifact_http_status"], 404)
        self.assertIn(("DELETE", "/api/generate/jobs/job1"), calls)

        # A coincident inference error after DELETE must not qualify as a callback stop.
        cancelled.pop("denoise_cancel_observed")
        calls.clear()
        with TemporaryDirectory() as temporary, patch.object(qwen, "request_json", side_effect=request):
            receipt = qwen.run_cancellation_probe(
                "http://private", "token", profile, "prompt", 42, Path(temporary),
                deadline=1.0, poll_interval=0.0,
            )
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("callback", receipt["reason"])

    def test_cancellation_probe_rejects_initial_progress_without_denoise_receipt(self) -> None:
        profile = qwen.profile_for_tier(16, turbo=True)
        calls = []

        def request(_base, _token, method, path, payload=None, **_kwargs):
            calls.append((method, path))
            if method == "POST":
                return 200, {"job": {"id": "job1", "state": "queued"}}
            if method == "GET":
                return 200, {"job": {"id": "job1", "state": "running", "progress": 0.05}}
            raise AssertionError((method, path, payload))

        with TemporaryDirectory() as temporary, patch.object(qwen, "request_json", side_effect=request):
            receipt = qwen.run_cancellation_probe(
                "http://private", "token", profile, "prompt", 42, Path(temporary),
                deadline=0.001, poll_interval=0.0,
            )

        self.assertEqual(receipt["status"], "failed")
        self.assertIn("middle denoise", receipt["reason"])
        self.assertNotIn(("DELETE", "/api/generate/jobs/job1"), calls)

    def test_outcome_fails_when_turbo_cancellation_receipt_is_not_passed(self) -> None:
        rows = [{"name": "cold-square", "status": "completed", "memory_metrics_complete": True}]
        self.assertEqual(
            qwen.outcome(rows, expected_names=["cold-square"], target_tier_verified=True,
                         cancellation={"status": "failed"}, require_cancellation=True),
            "MEASURED FAILURE",
        )

    def test_cancellation_probe_rejects_artifact_and_still_busy_worker(self) -> None:
        profile = qwen.profile_for_tier(16, turbo=True)

        def request(_base, _token, method, path, payload=None, **_kwargs):
            if method == "POST":
                return 200, {"job": {"id": "job1", "state": "queued"}}
            if method == "GET" and path.endswith("job1"):
                request.polls += 1
                job = {"id": "job1", "state": "running", "denoise_step": 4,
                       "denoise_steps": 8, "progress": 0.5}
                return 200, {"job": job if request.polls == 1 else {
                    **job, "state": "cancelled", "denoise_cancel_observed": True}}
            if method == "DELETE":
                return 200, {"job": {"id": "job1", "state": "running"}}
            if method == "GET" and path.endswith("/image"):
                return 200, b"png"
            if method == "GET" and path == "/api/memory-policy":
                return 200, {"busy": False, "active_work": False, "loaded_models": []}
            raise AssertionError((method, path))

        request.polls = 0
        with TemporaryDirectory() as temporary, patch.object(qwen, "request_json", side_effect=request):
            receipt = qwen.run_cancellation_probe(
                "http://private", "token", profile, "prompt", 42, Path(temporary),
                deadline=1.0, settle_deadline=1.0, poll_interval=0.0,
            )
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("HTTP 200", receipt["reason"])

        def busy_request(_base, _token, method, path, payload=None, **_kwargs):
            if method == "POST":
                return 200, {"job": {"id": "job1", "state": "queued"}}
            if method == "GET" and path.endswith("job1"):
                busy_request.polls += 1
                job = {"id": "job1", "state": "running", "denoise_step": 4,
                       "denoise_steps": 8, "progress": 0.5}
                return 200, {"job": job if busy_request.polls == 1 else {
                    **job, "state": "cancelled", "denoise_cancel_observed": True}}
            if method == "DELETE":
                return 200, {"job": {"id": "job1", "state": "running"}}
            if method == "GET" and path.endswith("/image"):
                return 404, {"detail": "final image unavailable"}
            if method == "GET" and path == "/api/memory-policy":
                return 200, {"busy": True, "active_work": True, "loaded_models": [["repo"]]}
            raise AssertionError((method, path))

        busy_request.polls = 0
        with TemporaryDirectory() as temporary, patch.object(qwen, "request_json", side_effect=busy_request):
            receipt = qwen.run_cancellation_probe(
                "http://private", "token", profile, "prompt", 42, Path(temporary),
                deadline=1.0, settle_deadline=0.001, poll_interval=0.0,
            )
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("cleanup did not settle", receipt["reason"])

    def test_cancellation_probe_rejects_wrong_terminal_state_or_refused_delete(self) -> None:
        profile = qwen.profile_for_tier(16, turbo=True)
        for delete_status, terminal_state, expected in (
            (409, "cancelled", "acknowledgement failed"),
            (200, "error", "did not become cancelled"),
        ):
            def request(_base, _token, method, path, payload=None, **_kwargs):
                if method == "POST":
                    return 200, {"job": {"id": "job1", "state": "queued"}}
                if method == "GET" and path.endswith("job1"):
                    request.polls += 1
                    job = {"id": "job1", "state": "running", "denoise_step": 4,
                           "denoise_steps": 8, "progress": 0.5}
                    state = "running" if request.polls == 1 else terminal_state
                    return 200, {"job": {**job, "state": state}}
                if method == "DELETE":
                    return delete_status, {"detail": "refused"} if delete_status != 200 else {
                        "job": {"id": "job1", "state": "running"}}
                raise AssertionError((method, path))

            request.polls = 0
            with self.subTest(delete_status=delete_status), TemporaryDirectory() as temporary, \
                    patch.object(qwen, "request_json", side_effect=request):
                receipt = qwen.run_cancellation_probe(
                    "http://private", "token", profile, "prompt", 42, Path(temporary),
                    deadline=1.0, poll_interval=0.0,
                )
            self.assertEqual(receipt["status"], "failed")
            self.assertIn(expected, receipt["reason"])

    def test_turbo_profiles_use_one_exact_candidate_at_both_supported_tiers(self) -> None:
        manifest = json.loads(
            (ROOT / "model-manifests/qwen-image-2-1-turbo-mflux-q4.json").read_text())
        normal_16 = qwen.profile_for_tier(16)
        turbo_16 = qwen.profile_for_tier(16, turbo=True)
        turbo_24 = qwen.profile_for_tier(24, turbo=True)
        turbo_32 = qwen.profile_for_tier(32, turbo=True)

        self.assertEqual(normal_16.package_id, "qwen-image-2-1-mflux-q4")
        for profile, tier in ((turbo_16, 16), (turbo_24, 24), (turbo_32, 32)):
            self.assertEqual(profile.tier_gb, tier)
            self.assertEqual(profile.package_id, manifest["package_id"])
            self.assertEqual(profile.repo, manifest["model_id"])
            self.assertEqual(profile.revision, manifest["revision"])
            self.assertEqual(profile.quantization, "MLX 4-bit")

    def test_turbo_workloads_are_eight_steps_and_stress_is_refused(self) -> None:
        cases = qwen.workloads(False, turbo=True)
        self.assertEqual(
            [(case.name, case.width, case.height, case.steps, case.process_cold)
             for case in cases],
            [("cold-square", 1024, 1024, 8, True),
             ("warm-square", 1024, 1024, 8, False),
             ("warm-landscape", 1280, 720, 8, False),
             ("warm-portrait", 720, 1280, 8, False)],
        )
        with self.assertRaisesRegex(SystemExit, "stress-40.*Turbo"):
            qwen.workloads(True, turbo=True)

    def test_worker_command_opts_in_only_for_the_turbo_candidate(self) -> None:
        app = Path("/fixture/Kit/Studio Hub.app")
        data = Path("/fixture/Kit/_qualification-private/run/state")
        library = Path("/fixture/Kit/_qualification-private/run/library")
        token = data / "owner.token"
        ready = data / "worker-ready.json"

        ordinary = qwen.worker_command(app, data, library, token, ready, 123)
        turbo = qwen.worker_command(
            app, data, library, token, ready, 123,
            qualification_package="qwen-image-2-1-turbo-mflux-q4")

        self.assertNotIn("--qualification-package", ordinary)
        self.assertEqual(turbo[-2:],
                         ["--qualification-package", "qwen-image-2-1-turbo-mflux-q4"])

    def test_runner_verifies_worker_granted_only_candidate_qualification(self) -> None:
        profile = qwen.profile_for_tier(16, turbo=True)
        row = {"package_id": profile.package_id, "production_allowed": False,
               "qualification_allowed": True, "execution_ready": True}
        with patch.object(qwen, "request_json", return_value=(200, {"models": [row]})):
            self.assertEqual(qwen.verify_qualification_worker("http://private", "token", profile),
                             row)
        for changed in (
            {"production_allowed": True}, {"qualification_allowed": False},
            {"execution_ready": False},
        ):
            bad = {**row, **changed}
            with self.subTest(changed=changed), \
                 patch.object(qwen, "request_json", return_value=(200, {"models": [bad]})), \
                 self.assertRaisesRegex(RuntimeError, "qualification permission"):
                qwen.verify_qualification_worker("http://private", "token", profile)

    def test_launcher_grants_candidate_only_inside_one_private_qualification_root(self) -> None:
        worker = load_image_worker()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "Studio Hub.app/Contents/Resources"
            manifests = resources / "model-manifests"
            manifests.mkdir(parents=True)
            candidate = json.loads(
                (ROOT / "model-manifests/qwen-image-2-1-turbo-mflux-q4.json").read_text())
            (manifests / f"{candidate['package_id']}.json").write_text(json.dumps(candidate))
            private = root / "Kit/_qualification-private/run"
            data, models = private / "state", private / "library"
            data.mkdir(parents=True)
            (models / candidate["package_id"]).mkdir(parents=True)

            allowed = worker.qualification_package(
                resources, data, models, 123, candidate["package_id"])
            self.assertEqual(allowed, candidate["package_id"])

            with self.assertRaisesRegex(ValueError, "private qualification"):
                worker.qualification_package(
                    resources, root / "ordinary-state", root / "ordinary-models", 123,
                    candidate["package_id"])
            with self.assertRaisesRegex(ValueError, "owning parent"):
                worker.qualification_package(
                    resources, data, models, None, candidate["package_id"])

            candidate["production_allowed"] = True
            (manifests / f"{candidate['package_id']}.json").write_text(json.dumps(candidate))
            with self.assertRaisesRegex(ValueError, "already allowed"):
                worker.qualification_package(
                    resources, data, models, 123, candidate["package_id"])

    def test_private_worker_refuses_all_known_foreign_model_processes_before_spawn(self) -> None:
        commands = (
            "/Applications/Studio Hub.app/Contents/Resources/voice_worker.py --port 0",
            "/Applications/Studio Hub.app/Contents/Resources/chat_worker.py --port 0",
            "/runtime/python3 -m mlx_lm.generate --model private",
            "/runtime/bin/mflux-generate --model private",
            "/runtime/bin/mflux-generate-z-image-turbo --model private",
            "/runtime/bin/mflux-generate-qwen-2.1 --model private",
            "/runtime/bin/mflux-generate-flux2 --model private",
        )

        def must_not_spawn(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("qualification worker spawned beside foreign model work")

        for index, command in enumerate(commands, start=301):
            listing = f"  {index} {command}\n"
            with self.subTest(command=command), \
                 self.assertRaisesRegex(qwen.ForeignProcessError, str(index)):
                qwen.spawn_private_worker(
                    ["private-worker"], stdout=subprocess.DEVNULL, cwd=Path("/tmp"), env={},
                    process_listing=listing, popen=must_not_spawn)

    def test_foreign_process_guard_does_not_match_mflux_cli_text_in_other_commands(self) -> None:
        listing = """\
  401 /usr/bin/echo mflux-generate-qwen-2.1
  402 /tmp/mflux-generate-qwen-2.1-cache/helper --serve
  403 /runtime/bin/not-mflux-generate-flux2 --model private
  404 /runtime/bin/mflux-generateish --model private
"""

        self.assertEqual(qwen.foreign_model_processes(listing, current_pid=999), [])


if __name__ == "__main__":
    unittest.main()
