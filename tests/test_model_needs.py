"""Declared and measured RAM requirements (`needs_gb`, `needs_source`) across Hub catalogues.

Owner rule (2026-11-25): RAM is the only limit on which model a Mac can run; a model's RAM
requirement is shown *before* an install; a refusal names the shortfall rather than hiding the
model; zero `unknown` in the catalogue.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

from components import model_packages  # noqa: E402
from components.hub.backend import model_ids, model_install_plan, model_needs  # noqa: E402


def _runtime() -> str:
    candidates = [
        os.environ.get("STUDIOHUB_TEST_RUNTIME"),
        str(WORKTREE / ".build/hub-gate/bin/python3"),
        str(WORKTREE / ".build/voice-runtime/bin/python3"),
        str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


def _run_route_probe(script: str, env_overrides: dict[str, str]) -> dict:
    env = {**os.environ, **env_overrides, "PYTHONPATH": str(WORKTREE)}
    completed = subprocess.run(
        [_runtime(), "-c", script],
        cwd=str(WORKTREE),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"route probe failed (rc={completed.returncode}):\nSTDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise AssertionError(f"route probe produced no RESULT line:\n{completed.stdout}\n{completed.stderr}")


ROUTE_PROBE = r'''
import json
from unittest import mock
from fastapi.testclient import TestClient

from components.hub import standalone
from components.hub.backend import auth

app = standalone.create_app(start_background=False)
token = auth.load_token()
fake_vm = type("VM", (), {"total": 8 * 1024 ** 3})()
with mock.patch("psutil.virtual_memory", return_value=fake_vm):
    with TestClient(app) as client:
        headers = {"X-Hub-Token": token}
        packages = client.get("/api/hub/model-packages", headers=headers)
        presets = client.get("/api/hub/model-presets", headers=headers)
print("RESULT:" + json.dumps({
    "packages_status": packages.status_code,
    "packages": packages.json(),
    "presets_status": presets.status_code,
    "presets": presets.json(),
}))
'''


class ModelNeedsCatalogTests(unittest.TestCase):
    def test_no_catalogue_entry_ever_carries_empty_needs_gb_or_unknown_source(self) -> None:
        """Every active shipped manifest has a known engine constant, positive needs_gb, and zero unknown."""
        catalog = model_packages.load_catalog(WORKTREE / "model-manifests")
        self.assertGreater(len(catalog), 0)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for package_id, manifest in sorted(catalog.items()):
                self.assertFalse(manifest.get("_manifest_error"), f"{package_id} has manifest error")
                engine = str(manifest.get("engine") or "")
                self.assertIn(
                    engine,
                    model_needs.ENGINE_OVERHEAD_GB,
                    f"{package_id} uses engine {engine!r} not in ENGINE_OVERHEAD_GB",
                )
                needs_gb, needs_source = model_needs.needs_for(manifest, data_dir=data_dir)
                self.assertIsInstance(needs_gb, float, f"{package_id}: needs_gb must be float")
                self.assertGreater(needs_gb, 0.0, f"{package_id}: needs_gb must be positive")
                self.assertIn(needs_source, ("declared", "measured"), f"{package_id}: bad source")
                self.assertNotEqual(needs_source, "unknown", f"{package_id}: unknown is forbidden")

            # Aggregate /api/hub/catalog and /api/hub/models annotation also never emits empty or unknown.
            sample_rows = [
                {"repo": manifest["model_id"]}
                for manifest in catalog.values()
                if not manifest.get("_manifest_error")
            ]
            sample_rows.append({"repo": "org/unlisted-model", "size_gb": 2.5, "engine": "voice-mlx"})
            annotated = model_ids.annotate({"models": sample_rows})
            for row in annotated["models"]:
                self.assertIsInstance(row.get("needs_gb"), float)
                self.assertGreater(row["needs_gb"], 0.0)
                self.assertIn(row.get("needs_source"), ("declared", "measured"))

    def test_model_that_fits_nowhere_still_appears_with_needs_and_shortfall(self) -> None:
        """A model larger than any Mac is never dropped; it stays listed with its needs_gb and shortfall."""
        real_manifest = json.loads((WORKTREE / "model-manifests" / "whisper-turbo.json").read_text(encoding="utf-8"))
        giant_manifest = json.loads(json.dumps(real_manifest))
        giant_manifest.update({
            "package_id": "giant-test-512gb-4bit",
            "model_id": "org/giant-test-512gb-4bit",
            "display_name": "Giant Test 512GB",
            "engine": "chat-mlx",
            "files": [{"path": "weights.safetensors", "size": 192 * 1024 ** 3, "sha256": "0" * 64, "urls": ["https://example.invalid/w"]}],
            "qualification": {"minimum_unified_memory_gb": 512},
        })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            # Copy all real manifests so shipped presets remain valid, then add the giant model.
            for path in (WORKTREE / "model-manifests").glob("*.json"):
                (manifests_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            (manifests_dir / "giant-test-512gb-4bit.json").write_text(
                json.dumps(giant_manifest), encoding="utf-8"
            )
            # Also add a broken manifest to verify even error rows carry a positive needs_gb.
            (manifests_dir / "broken-manifest.json").write_text("{not-json", encoding="utf-8")

            library_dir = root / "library"
            library_dir.mkdir()
            data_dir = root / "data"
            data_dir.mkdir()

            result = _run_route_probe(
                ROUTE_PROBE,
                {
                    "STUDIOHUB_MODEL_MANIFESTS": str(manifests_dir),
                    "STUDIOHUB_MODELS_DIR": str(library_dir),
                    "STUDIOHUB_DATA_DIR": str(data_dir),
                },
            )

        self.assertEqual(result["packages_status"], 200)
        self.assertEqual(result["presets_status"], 200)

        package_rows = result["packages"]["models"]
        # Every /api/hub/model-packages row carries a positive needs_gb and valid non-unknown source.
        for row in package_rows:
            self.assertIsInstance(row.get("needs_gb"), (int, float), f"{row.get('package_id')} missing needs_gb")
            self.assertGreater(row["needs_gb"], 0.0, f"{row.get('package_id')} non-positive needs_gb")
            self.assertIn(row.get("needs_source"), ("declared", "measured"), f"{row.get('package_id')} bad source")

        giant_pkg = next((r for r in package_rows if r["package_id"] == "giant-test-512gb-4bit"), None)
        self.assertIsNotNone(giant_pkg, "giant model was omitted from /api/hub/model-packages")
        self.assertEqual(giant_pkg["needs_gb"], 194.0)
        self.assertEqual(giant_pkg["needs_source"], "declared")

        preset_rows = result["presets"]["models"]
        for row in preset_rows:
            self.assertIsInstance(row.get("needs_gb"), (int, float), f"{row.get('package_id')} missing needs_gb")
            self.assertGreater(row["needs_gb"], 0.0)
            self.assertIn(row.get("needs_source"), ("declared", "measured"))

        giant_preset = next((r for r in preset_rows if r["package_id"] == "giant-test-512gb-4bit"), None)
        self.assertIsNotNone(giant_preset, "giant model was omitted from /api/hub/model-presets")
        self.assertEqual(giant_preset["needs_gb"], 194.0)
        self.assertEqual(giant_preset["needs_source"], "declared")
        self.assertIsNotNone(giant_preset["reason"], "giant model on 8 GB Mac must name the shortfall")
        self.assertIn("512", giant_preset["reason"])
        self.assertIn("8", giant_preset["reason"])


class ModelNeedsUpgradeTests(unittest.TestCase):
    def test_declared_overhead_is_constant_per_engine(self) -> None:
        a = {"engine": "voice-mlx", "files": [{"size": 2 * 1024 ** 3}]}
        b = {"engine": "voice-mlx", "files": [{"size": 5 * 1024 ** 3}]}
        self.assertAlmostEqual(
            model_needs.declared_gb(b) - model_needs.declared_gb(a),
            3.0,
            places=2,
        )
        self.assertEqual(model_needs.declared_gb(a), round(2.0 + model_needs.ENGINE_OVERHEAD_GB["voice-mlx"], 2))

    def test_measured_upgrade_from_refusal_and_load_preserves_previous_value(self) -> None:
        manifest = json.loads((WORKTREE / "model-manifests" / "whisper-turbo.json").read_text(encoding="utf-8"))
        declared = model_needs.declared_gb(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(model_needs.needs_for(manifest, data_dir=data_dir), (declared, "declared"))

            # Hub's own local gate sentence must NOT be mistaken for a worker measurement.
            ignored = model_needs.note_refusal(
                manifest["model_id"], "waiting for memory: needs ~9.9GB, ~4.0GB free", data_dir=data_dir
            )
            self.assertIsNone(ignored)
            self.assertEqual(model_needs.needs_for(manifest, data_dir=data_dir), (declared, "declared"))

            # Worker's real refusal upgrades declared -> measured and keeps declared in history.
            rec1 = model_needs.note_refusal(
                manifest["model_id"],
                "Memory guard paused this job: 3.2 GB free is needed, but only 2.1 GB is available",
                data_dir=data_dir,
            )
            self.assertIsNotNone(rec1)
            self.assertEqual(rec1["needs_gb"], 3.2)
            self.assertEqual(rec1["source"], "measured")
            self.assertEqual(len(rec1["history"]), 1)
            self.assertEqual(rec1["history"][0]["needs_gb"], declared)
            self.assertEqual(rec1["history"][0]["source"], "declared")
            self.assertEqual(model_needs.needs_for(manifest, data_dir=data_dir), (3.2, "measured"))

            # Duplicate refusal does not rewrite state or pollute history.
            dup = model_needs.note_refusal(
                manifest["model_id"],
                "Mac-mini needs about 3.2 GB free and has about 2.0 GB free",
                data_dir=data_dir,
            )
            self.assertIsNone(dup)

            # A real load measurement updates needs_gb again and retains 3.2 -> declared in history.
            rec2 = model_needs.note_load(
                manifest["model_id"], 2.95, "peak resident on clean load", data_dir=data_dir
            )
            self.assertIsNotNone(rec2)
            self.assertEqual(rec2["needs_gb"], 2.95)
            self.assertEqual(rec2["source"], "measured")
            self.assertEqual([h["needs_gb"] for h in rec2["history"]], [3.2, declared])
            self.assertEqual(model_needs.needs_for(manifest, data_dir=data_dir), (2.95, "measured"))

    def test_precedence_learned_over_manifest_over_worker_over_declared(self) -> None:
        manifest = {
            "package_id": "test-precedence",
            "model_id": "org/test-precedence",
            "engine": "voice-mlx",
            "files": [{"size": 2 * 1024 ** 3}],
            "qualification": {"min_free_memory_gb": 4.2},
        }
        no_qual = {k: v for k, v in manifest.items() if k != "qualification"}
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(model_needs.needs_for(no_qual, None, data_dir=data_dir), (3.0, "declared"))
            self.assertEqual(model_needs.needs_for(no_qual, 3.6, data_dir=data_dir), (3.6, "measured"))
            self.assertEqual(model_needs.needs_for(manifest, 3.6, data_dir=data_dir), (4.2, "measured"))
            model_needs.record_measured("test-precedence", 5.1, "worker refused", data_dir=data_dir)
            self.assertEqual(model_needs.needs_for(manifest, 3.6, data_dir=data_dir), (5.1, "measured"))

    def test_model_install_plan_rows_always_include_needs_gb(self) -> None:
        manifests = model_packages.load_catalog(WORKTREE / "model-manifests")
        modality_map = {
            "image-mflux": "image", "image-mlx-serve": "image", "voice-mlx": "voice",
            "voice-vits": "voice", "subtitle-mlx": "transcription", "music-mlx": "music",
            "music-yue2": "music", "chat-mlx": "chat", "embed-mlx": "embeddings",
            "vision-mlx": "vision",
        }
        rows = model_install_plan.model_rows(
            manifests=manifests, receipts={}, memory_gb=8, modality_of=modality_map.__getitem__
        )
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertIsInstance(row.get("needs_gb"), float)
            self.assertGreater(row["needs_gb"], 0.0)
            self.assertIn(row.get("needs_source"), ("declared", "measured"))


if __name__ == "__main__":
    unittest.main()
