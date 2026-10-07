"""Chat stages the canonical Voice MLX cache helper at both importable package layouts."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _stage_module():
    spec = importlib.util.spec_from_file_location(
        "stage_voice_chat_cache_test", ROOT / "packaging/stage_voice.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChatCacheStagingTests(unittest.TestCase):
    def test_staged_chat_import_resolves_the_byte_identical_canonical_helper(self) -> None:
        module = _stage_module()
        source = ROOT / "components/voice/app/backend/mlx_memory.py"
        with tempfile.TemporaryDirectory() as tmp:
            resources = Path(tmp) / "Resources"
            engine = resources / "components/chat/app/backend/llm_engine.py"
            engine.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "components/chat/app/backend/llm_engine.py", engine)
            voice_helper = resources / "voice/app/backend/mlx_memory.py"
            voice_helper.parent.mkdir(parents=True)
            shutil.copyfile(source, voice_helper)

            before = module.staged_import_gaps(resources)
            self.assertTrue(any("components/voice/app/backend/mlx_memory" in gap
                                for gap in before))

            module.stage_chat_shared_modules(resources)
            staged = resources / "components/voice/app/backend/mlx_memory.py"
            self.assertEqual(staged.read_bytes(), source.read_bytes())
            self.assertEqual(
                [path.relative_to(resources / "components/voice").as_posix()
                 for path in (resources / "components/voice").rglob("*.py")],
                ["app/backend/mlx_memory.py"],
            )
            self.assertFalse(any("mlx_memory" in gap
                                 for gap in module.staged_import_gaps(resources)))

    def test_missing_canonical_helper_is_refused(self) -> None:
        module = _stage_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with mock.patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "mlx_memory.py"):
                    module.stage_chat_shared_modules(Path(tmp) / "Resources")

    def test_untracked_canonical_helper_is_refused(self) -> None:
        module = _stage_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            helper = root / "components/voice/app/backend/mlx_memory.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("# helper\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            with mock.patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "tracked"):
                    module.stage_chat_shared_modules(Path(tmp) / "Resources")


if __name__ == "__main__":
    unittest.main()
