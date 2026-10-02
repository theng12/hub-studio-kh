"""No test or qualification script may start a Hub over a real model library.

The incident (2026-10-01/02): a Hub started with a throwaway --data-dir over the REAL library shelved and
retired real weights into the throwaway directory's Trash Folder, and deleting the directory destroyed
them (Gemma, LTX, Whisper base/small). tests/test_peer_path_contract.py and qualification/verify-launch.sh
did exactly that. The Hub itself now refuses (tests/test_library_ownership.py), but an installed app older
than that check does not, so what these scripts hand a Hub as its library has to be disposable.

This scans every test and qualification script that starts a Hub (`hub_worker.py` or `StudioHubService`)
for a real library: a hard-coded `StudioHubModels` / `model-library` / `/Volumes/` path, or a
`--models`/`--library` option that is not routed through `qualification/library_guard.py`.
"""
import ast
import io
import re
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS = Path(__file__).resolve()
SKIP = {THIS, ROOT / "qualification" / "library_guard.py"}
LAUNCH = re.compile(r"hub_worker\.py|StudioHubService")
REAL_LIBRARY = re.compile(r"StudioHubModels|model-library|/Volumes/")


def _python_strings(source: str) -> list[str]:
    """String constants that are not docstrings."""
    tree = ast.parse(source)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]


def _shell_code(source: str) -> str:
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


def problems(path: str, source: str) -> list[str]:
    """What is wrong with this script if it starts a Hub; empty when it does not or is safe."""
    if not LAUNCH.search(source):
        return []
    found = []
    if path.endswith(".py"):
        code = "\n".join(_python_strings(source))
        calls = [node for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"]
        options = [arg.value for call in calls for arg in call.args[:1]
                   if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        if any(re.search(r"models|library", option) for option in options) and "library_guard" not in source:
            found.append("takes a --models/--library option but does not pass it through library_guard")
    else:
        code = _shell_code(source)
    if REAL_LIBRARY.search(code):
        found.append(f"names a real library path ({REAL_LIBRARY.search(code).group(0)}) in a script that starts a Hub")
    return found


class RealLibraryLaunchGuard(unittest.TestCase):
    def test_no_script_starts_a_hub_over_a_real_library(self):
        offenders = {}
        for folder in ("tests", "qualification"):
            for path in sorted((ROOT / folder).rglob("*")):
                if path.suffix not in {".py", ".sh", ".command"} or path.resolve() in SKIP or not path.is_file():
                    continue
                try:
                    issues = problems(str(path), path.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError):
                    continue
                if issues:
                    offenders[str(path.relative_to(ROOT))] = issues
        self.maxDiff = None
        self.assertEqual(offenders, {}, "a Hub started with a throwaway data dir over a real library "
                                        "moves real weights into the throwaway dir's trash")

    def test_the_scanner_catches_the_two_scripts_that_caused_the_loss(self):
        peer = ('library = Path.home() / "StudioHubModels"\n'
                'subprocess.Popen([str(runtime), str(worker), "--data-dir", tmp, "--models-dir", str(library)])\n'
                'worker = candidate / "Contents/Resources/hub_worker.py"\n')
        self.assertTrue(problems("probe.py", peer))
        verify = '#!/bin/sh\nLIB="$KIT/model-library"\n"$APP/Contents/MacOS/StudioHubService" --models-dir "$LIB"\n'
        self.assertTrue(problems("verify.sh", verify))
        arg = ('import argparse\np = argparse.ArgumentParser()\np.add_argument("--models")\n'
               'x = "hub_worker.py"\n')
        self.assertEqual(len(problems("probe.py", arg)), 1)
        self.assertEqual(problems("probe.py", arg + "import library_guard\n"), [])

    def test_the_scanner_ignores_comments_docstrings_and_scripts_that_start_no_hub(self):
        self.assertEqual(problems("a.sh", '# ~/StudioHubModels is never used\nrun StudioHubService "$TMP/lib"\n'), [])
        self.assertEqual(problems("a.py", '"""not ~/StudioHubModels."""\nx = "hub_worker.py"\n'), [])
        self.assertEqual(problems("a.py", 'x = "~/StudioHubModels"\n'), [])        # starts no Hub


if __name__ == "__main__":
    unittest.main()
