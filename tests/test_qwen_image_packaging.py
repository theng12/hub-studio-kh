from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CAPTURE = load_module(
    "capture_model_manifests_qwen_test",
    ROOT / "packaging" / "capture_model_manifests.py",
)
APPEND = load_module(
    "append_mlx_serve_qwen_test",
    ROOT / "packaging" / "image-runtime" / "append_mlx_serve.py",
)


class QwenManifestTests(unittest.TestCase):
    def test_qwen_packages_pin_distinct_artifacts_and_conservative_memory_floors(self):
        choices = {item["package_id"]: item for item in CAPTURE.CHOICES}
        details = CAPTURE.DETAILS

        native = choices["qwen-image-2-1-mlx-serve-4bit"]
        self.assertEqual(native["model_id"], "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")
        self.assertEqual(native["revision"], "88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38")
        self.assertEqual(native["engine"], "image-mlx-serve")
        self.assertEqual(native["minimum_macos"], "26.2")
        self.assertEqual(native["quantization"], "MLX 4-bit")

        low_ram = choices["qwen-image-2-1-mflux-q4"]
        self.assertEqual(low_ram["model_id"], "mlx-community/Qwen-Image-2.1-mflux-q4")
        self.assertEqual(low_ram["revision"], "746a58556820933a2df5c75887a2570f1ad200c0")
        self.assertEqual(low_ram["engine"], "image-mflux")
        self.assertEqual(low_ram.get("minimum_macos", "14.0"), "14.0")
        self.assertEqual(low_ram["quantization"], "MLX 4-bit")

        # Both measured on the owner's physical Macs (2026-10-08): native on the 24 GiB M4,
        # low-RAM on the 16 GiB M4.
        for package_id, tier, evidence, floor in (
            ("qwen-image-2-1-mlx-serve-4bit", 24, "qwen-image-21-20261008-24gb", 12.5),
            ("qwen-image-2-1-mflux-q4", 16, "qwen-image-21-20261008-16gb", 8.0),
        ):
            with self.subTest(package=package_id):
                item = details[package_id]
                self.assertEqual(item["defaults"], {
                    "width": 1024,
                    "height": 1024,
                    "steps": 20,
                    "guidance": 1.0,
                    "seed": -1,
                    "parameters_source": "docs/superpowers/plans/2026-10-06-qwen-image-21.md",
                })
                self.assertEqual(item["memory"][:2], (tier, tier))
                self.assertIn(evidence, item["memory"][2])
                qualification = item["qualification"]
                self.assertEqual(qualification["status"], "qualified")
                self.assertIn(evidence, qualification["reason"])
                self.assertNotIn("target_unified_memory_gb", qualification)
                self.assertEqual(qualification["min_free_memory_gb"], floor)
                self.assertIn(evidence, qualification["min_free_memory_note"])

        low_ram = details["qwen-image-2-1-mflux-q4"]["qualification"]
        self.assertIn("physical 16 GiB M4 run", low_ram["reason"])
        self.assertNotIn("without a physical 16 GB receipt", low_ram["reason"])
        self.assertIn("8 GB is not qualified", low_ram["reason"])

    def test_qwen_manifests_are_reproducible_capture_outputs_and_not_defaults(self):
        self.assertEqual(CAPTURE.catalog_problems(ROOT / "model-manifests"), [])
        for package_id in (
            "qwen-image-2-1-mlx-serve-4bit",
            "qwen-image-2-1-mflux-q4",
        ):
            with self.subTest(package=package_id):
                manifest = json.loads(
                    (ROOT / "model-manifests" / f"{package_id}.json").read_text()
                )
                choice = next(item for item in CAPTURE.CHOICES
                              if item["package_id"] == package_id)
                self.assertEqual(manifest["model_id"], choice["model_id"])
                self.assertEqual(manifest["revision"], choice["revision"])
                self.assertEqual(manifest["engine"], choice["engine"])
                self.assertEqual(manifest["minimum_macos"], choice.get("minimum_macos", "14.0"))
                self.assertEqual(manifest["defaults"], CAPTURE.DETAILS[package_id]["defaults"])
                self.assertEqual(manifest["source"]["captured_at"], "2026-10-06")
                self.assertNotIn("default", manifest)
                # The weights are Qwen's own; Apache-2.0 is the licence of the mlx-community
                # conversion, not of the model it converted. The record said Apache-2.0 for the
                # mflux pack until 2026-10-26 (owner finding), while the Turbo pack on the same
                # upstream model already said qwen-research.
                expected_licence = {"qwen-image-2-1-mflux-q4": "qwen-research"}.get(
                    package_id, "Apache-2.0")
                self.assertEqual(manifest["license"]["weights"], expected_licence)
                self.assertEqual(manifest["quantization"], choice["quantization"])
                self.assertEqual(
                    manifest["quantization_source"], choice["quantization_source"]
                )

    def test_native_package_is_ineligible_below_its_conditional_os_floor(self):
        from components.hub import standalone_config

        base = {
            "files_state": "files_verified",
            "runtime_identity": {"id": "image-runtime", "compatible": True},
        }
        native = json.loads(
            (ROOT / "model-manifests" / "qwen-image-2-1-mlx-serve-4bit.json").read_text()
        )
        low_ram = json.loads(
            (ROOT / "model-manifests" / "qwen-image-2-1-mflux-q4.json").read_text()
        )
        self.assertEqual(
            standalone_config.model_readiness(
                {**base, **native}, macos_version="26.1", chip="arm64"
            )["dispatch_state"],
            "ineligible",
        )
        self.assertEqual(
            standalone_config.model_readiness(
                {**base, **native}, macos_version="26.2", chip="arm64"
            )["dispatch_state"],
            "eligible",
        )
        self.assertEqual(
            standalone_config.model_readiness(
                {**base, **low_ram}, macos_version="14.0", chip="arm64"
            )["dispatch_state"],
            "eligible",
        )


class MlxServeRuntimeAppendTests(unittest.TestCase):
    FILES = (
        "LICENSE",
        "LICENSE-APACHE-2.0",
        "NOTICE",
        "mlx-serve",
        "lib/libmlx.dylib",
    )

    def test_fresh_build_freezes_base_modes_before_manifest_and_native_append(self):
        script = (ROOT / "packaging" / "image-runtime" / "build.sh").read_text()
        audit = script.index('runtime_packaging.py" audit "$STAGE"')
        freeze = script.index('chmod -R a-w "$STAGE"', audit)
        unlock_manifest = script.index('chmod u+w "$STAGE/manifest"', freeze)
        manifest = script.index('runtime_packaging.py" manifest "$STAGE"', unlock_manifest)
        relock = script.index(
            'chmod a-w "$STAGE/manifest/runtime-files.json" "$STAGE/manifest"',
            manifest,
        )
        append = script.index('append_mlx_serve.py" "$STAGE"', relock)

        self.assertLess(audit, freeze)
        self.assertLess(freeze, unlock_manifest)
        self.assertLess(unlock_manifest, manifest)
        self.assertLess(manifest, relock)
        self.assertLess(relock, append)
        self.assertEqual(script.count('chmod -R a-w "$STAGE"'), 1)

    def archive(self, path: Path, files: tuple[str, ...] | None = None,
                extra: tuple[str, ...] = ()) -> dict:
        files = files or self.FILES
        with tarfile.open(path, "w:gz") as bundle:
            for name in (*files, *extra):
                body = f"fixture:{name}".encode()
                item = tarfile.TarInfo(f"mlx-serve-macos-arm64/{name}")
                item.size = len(body)
                item.mode = 0o755 if name == "mlx-serve" else 0o644
                bundle.addfile(item, io.BytesIO(body))
        return {
            "schema": "studiohub.mlx-serve-pin.v1",
            "version": "26.10.1",
            "archive": path.name,
            "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "archive_root": "mlx-serve-macos-arm64",
            "source_url": "https://example.invalid/mlx-serve.tar.gz",
            "base_runtime_minimum_macos": "14.0",
            "minimum_macos": "26.2",
            "files": list(files),
        }

    def runtime(self, root: Path) -> Path:
        runtime = root / "image-runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin" / "python3").write_text("fixture")
        (runtime / "manifest").mkdir()
        (runtime / "manifest" / "macho-audit.json").write_text(json.dumps({
            "schema": "studiohub.voice-runtime-macho.v1",
            "target_macos": "14.0",
            "status": "passed",
            "failures": [],
            "binaries": [],
        }))
        APPEND.write_runtime_manifest(runtime)
        return runtime

    def test_append_records_a_conditional_native_floor_without_changing_the_base_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            archive = root / "mlx-serve.tar.gz"
            pin = self.archive(archive)
            backup = root / "backups"
            native_audit = [{
                "path": "mlx-serve",
                "architectures": ["arm64"],
                "minimum_macos": "26.2",
                "dependencies": [],
                "rpaths": [],
            }]
            with mock.patch.object(APPEND, "relocate_native_tree", return_value=[]), \
                    mock.patch.object(APPEND, "audit_native_tree", return_value=native_audit), \
                    mock.patch.object(APPEND, "native_version", return_value="26.10.1"):
                APPEND.append_runtime(runtime, archive, pin, backup_root=backup)

            native = json.loads((runtime / "manifest" / "mlx-serve.json").read_text())
            self.assertEqual(native["base_runtime_minimum_macos"], "14.0")
            self.assertEqual(native["minimum_macos"], "26.2")
            self.assertTrue(native["conditional"])
            self.assertEqual(native["archive_sha256"], pin["archive_sha256"])
            self.assertEqual(
                json.loads((runtime / "manifest" / "macho-audit.json").read_text())["target_macos"],
                "14.0",
            )
            audit = json.loads(
                (runtime / "manifest" / "mlx-serve-macho-audit.json").read_text()
            )
            self.assertEqual(audit["target_macos"], "26.2")
            self.assertEqual(audit["binaries"], native_audit)
            files = {
                item["path"]
                for item in json.loads(
                    (runtime / "manifest" / "runtime-files.json").read_text()
                )["files"]
            }
            self.assertIn("mlx-serve/mlx-serve", files)
            self.assertIn("manifest/mlx-serve.json", files)
            self.assertTrue(any(backup.rglob("runtime-files.json")))

    def test_archive_traversal_is_rejected_before_the_runtime_is_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            before = (runtime / "manifest" / "runtime-files.json").read_bytes()
            archive = root / "bad.tar.gz"
            pin = self.archive(archive, extra=("../escaped",))

            with self.assertRaisesRegex(ValueError, "unsafe archive path"):
                APPEND.append_runtime(runtime, archive, pin, backup_root=root / "backups")

            self.assertFalse((runtime / "mlx-serve").exists())
            self.assertEqual((runtime / "manifest" / "runtime-files.json").read_bytes(), before)

    def test_existing_native_tree_is_an_overwrite_collision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            (runtime / "mlx-serve").mkdir()
            archive = root / "mlx-serve.tar.gz"
            pin = self.archive(archive)

            with self.assertRaisesRegex(FileExistsError, "collision"):
                APPEND.append_runtime(runtime, archive, pin, backup_root=root / "backups")


if __name__ == "__main__":
    unittest.main()
