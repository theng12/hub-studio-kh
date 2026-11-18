"""The Hub never names or opens a volume root on its own (owner, 2026-10-30).

The defect this pins: an automatic read of a mounted drive is what makes macOS ask an ad-hoc
signed app for permission after every update, and an unanswered prompt held Hub routes until the
Mac looked frozen. 0.6.212 removed the probe entirely. The setup ladder (0.6.248) allows exactly
one free exception: `os.listdir` of the volume root, which never prompts. Nothing inside a volume
is read unless a person asked for that drive (the drive-check route or a drive copy).

**What this test cannot catch.** It reads source and audits this Python process only. It cannot
see a path assembled at runtime from a chosen library (which is allowed), a read by the native
shell or a worker process, or whether macOS shows a prompt on a real fleet Mac. A mounted
drive may hold the test workspace itself. The audit permits only the explicitly selected source,
runtime and temporary fixture roots there; it still refuses unrelated volumes and volume roots.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "components" / "hub"


def _hub_modules() -> list[Path]:
    return sorted(path for path in HUB.rglob("*.py") if "__pycache__" not in path.parts)


def _unexpected_volume_reads(paths, allowed_roots, allow_root=False):
    roots = [Path(root).resolve() for root in allowed_roots]
    unexpected = []
    for path in paths:
        target = Path(os.path.abspath(path))
        if target == Path('/Volumes'):
            if not allow_root:
                unexpected.append(path)
            continue
        if Path('/Volumes') in target.parents and not any(
                target.is_relative_to(root) for root in roots):
            unexpected.append(path)
    return unexpected


class SourceTests(unittest.TestCase):
    def test_audit_allows_only_explicit_volume_subtrees(self):
        selected = '/Volumes/fixture/source'
        allowed = selected + '/components/hub.py'
        other = ['/Volumes/unselected/model.bin',
                 selected + '-other/file', selected + '/../private/file']
        self.assertEqual(_unexpected_volume_reads([allowed, *other], [selected]), other)
        # The volume root itself is the one free listing; the same paths are refused without it.
        self.assertEqual(_unexpected_volume_reads(['/Volumes'], [selected], allow_root=True), [])
        self.assertEqual(_unexpected_volume_reads(['/Volumes'], [selected]), ['/Volumes'])

    def test_no_hub_module_names_a_volume_root(self) -> None:
        offenders = []
        for path in _hub_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value == "/Volumes" or node.value.startswith("/Volumes/"):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value!r}")
        self.assertEqual(offenders, [], "a runtime Hub module names a volume root")

    def test_no_hub_module_reads_the_mount_table(self) -> None:
        offenders = []
        for path in _hub_modules():
            text = path.read_text(encoding="utf-8")
            for needle in ("/sbin/mount", "getfsstat", "diskutil"):
                if needle in text:
                    offenders.append(f"{path.relative_to(ROOT)} names {needle}")
        self.assertEqual(offenders, [])


class RouteAuditTests(unittest.TestCase):
    """Start the real app in a child process and watch every open, scan and list it makes.

    A child keeps the audit hook and the Hub's import-time state out of the gate's process: the
    Hub binds its database path when it is imported, and every other test module shares that one.
    """

    SCRIPT = r"""
import json, os, sys
root = sys.argv[1]
sys.path.insert(0, root)
opened = []
recording = {"on": False}

def hook(event, args):
    if not recording["on"] or event not in ("open", "os.scandir", "os.listdir"):
        return
    target = args[0] if args else None
    if isinstance(target, (str, bytes, os.PathLike)):
        opened.append(os.fsdecode(target))

sys.addaudithook(hook)
from fastapi.testclient import TestClient
from components.hub import standalone
from components.hub.backend import auth
app = standalone.create_app(start_background=False)
token = auth.load_token()
recording["on"] = True
with TestClient(app) as client:
    answers = {route: client.get(route, headers={"X-Hub-Token": token}).status_code
               for route in ("/api/health", "/api/hub/resources", "/api/hub/model-presets")}
recording["on"] = False
recording["on"] = True
probe = os.path.join(sys.argv[2], "audit-probe")
with open(probe, "w") as handle:
    handle.write("proof")
recording["on"] = False
print(json.dumps({"answers": answers, "opened": opened, "probe": probe}))
"""

    def test_status_routes_open_no_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data, models = Path(tmp) / "data", Path(tmp) / "models"
            data.mkdir()
            models.mkdir()
            environment = dict(os.environ, STUDIOHUB_DATA_DIR=str(data),
                               STUDIOHUB_MODELS_DIR=str(models), STUDIOHUB_PACKAGED="0")
            run = subprocess.run([sys.executable, "-I", "-B", "-c", self.SCRIPT, str(ROOT), str(data)],
                                 capture_output=True, text=True, env=environment, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            answer = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(answer["answers"],
                         {"/api/health": 200, "/api/hub/resources": 200,
                          "/api/hub/model-presets": 200})
        volume_reads = _unexpected_volume_reads(answer["opened"], [ROOT, tmp, sys.prefix],
                                                allow_root=True)
        self.assertEqual(volume_reads, [], "a status route opened a path inside a volume")
        # The plan route lists the root to name plugged drives, and that listing is the only
        # volume-root access allowed. Prove it happened rather than silently passing.
        roots = [path for path in answer["opened"] if os.path.abspath(path) == "/Volumes"]
        self.assertEqual(len(roots), 1, "the plan route lists the volume root exactly once")
        # The hook has to be able to record something, or an empty log proves nothing.
        self.assertIn(answer["probe"], answer["opened"],
                      "the audit hook recorded nothing at all")


if __name__ == "__main__":
    unittest.main()
