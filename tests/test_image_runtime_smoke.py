from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "packaging" / "image-runtime" / "smoke_imports.py"


def load_smoke():
    spec = importlib.util.spec_from_file_location("image_runtime_smoke_test", SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImageRuntimeSmokeTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("mflux"), "requires the pinned Image runtime; run with .build/image-runtime/bin/python3")
    def test_isolated_smoke_imports_registry_local_class_and_existing_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            backend = project / "components" / "image" / "app" / "backend"
            backend.mkdir(parents=True)
            for package in (
                project / "components",
                project / "components" / "image",
                project / "components" / "image" / "app",
                backend,
            ):
                (package / "__init__.py").write_text("", encoding="utf-8")
            (backend / "fixture_model.py").write_text(
                "class RegistryLocalModel:\n    pass\n\n\nclass RegistryLocalEditModel:\n    pass\n",
                encoding="utf-8"
            )
            registry = backend / "generation.py"
            registry.write_text(
                textwrap.dedent(
                    """
                    MFLUX = ImageModel(
                        repo="existing/mflux",
                        model_class="mflux.models.flux2.variants.txt2img.flux2_klein:Flux2Klein",
                    )
                    LOCAL = ImageModel(
                        repo="local/component",
                        model_class="components.image.app.backend.fixture_model:RegistryLocalModel",
                        edit_model_class=("components.image.app.backend.fixture_model:"
                                          "RegistryLocalEditModel"),
                    )
                    MODELS = {model.repo: model for model in (MFLUX, LOCAL)}
                    """
                ),
                encoding="utf-8",
            )
            driver = textwrap.dedent(
                """
                import runpy
                import sys

                smoke = runpy.run_path(sys.argv[1])
                globals_ = smoke["main"].__globals__
                globals_["BASE_MODULES"] = ()
                globals_["native_leaf"] = lambda runtime: {
                    "version": "fixture", "minimum_macos": "14.0"
                }
                sys.argv = [sys.argv[1], "--registry", sys.argv[2]]
                raise SystemExit(smoke["main"]())
                """
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    driver,
                    str(SMOKE),
                    str(registry),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(
            completed.returncode,
            0,
            f"isolated Image runtime smoke failed:\n{completed.stderr}",
        )
        result = json.loads(completed.stdout)
        self.assertEqual(
            result["models"],
            [
                "mflux.models.flux2.variants.txt2img.flux2_klein:Flux2Klein",
                "components.image.app.backend.fixture_model:RegistryLocalModel",
                # The reference/edit lane's class is imported too: a runtime missing the edit
                # variant must fail here, not at the first reference render.
                "components.image.app.backend.fixture_model:RegistryLocalEditModel",
            ],
        )
        self.assertEqual(result["registry"], str(registry.resolve()))

    def test_memory_snapshot_rejects_an_unreadable_live_reading(self):
        smoke = load_smoke()
        fake = types.SimpleNamespace(
            virtual_memory=lambda: types.SimpleNamespace(total=0, available=0)
        )
        with mock.patch.dict(sys.modules, {"psutil": fake}):
            with self.assertRaisesRegex(SystemExit, "unusable live memory"):
                smoke.memory_snapshot()

    def test_registry_must_have_the_repository_component_path(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temporary:
            registry = Path(temporary) / "generation.py"
            registry.write_text("MODELS = {}\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "components/image/app/backend/generation.py"):
                smoke.registry_path(registry)


if __name__ == "__main__":
    unittest.main()
