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
