"""LTX-2.3 video is retired (owner, 2026-10-01): a Hub with no video model is a plain state, not a fault.

The Hub has no Video worker any more (`packaging/stage_voice.py` ships none while the LTX package is on
`components/retired_models.py`). These tests run the real Hub app against the REAL shipped manifest
catalog, with only a Voice worker registered, and check what a person or the gateway would meet:

* the catalog and package routes answer 200, LTX is in neither, and every other package still is -
  including `gemma-3-12b-it-4bit`, which stays in the catalog (it is not retired);
* the Hub reports no video capability (no video model, no video operation);
* a job for the retired model, and a job for a video model that is not there, are refused with a
  named code, never a 500 and never a queued job that waits for ever;
* a release manifest without a `video` component starts every other worker (the installer and the
  Hub both accept releases with or without Video, which the 0.6.120 update incident depended on).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
LTX_PACKAGE = "ltx-2-3-22b-ram-12gb-mlx"
LTX_MODEL = "baa-ai/LTX-2.3-22B-RAM-12GB-MLX"
GEMMA_PACKAGE = "gemma-3-12b-it-4bit"
GEMMA_MODEL = "mlx-community/gemma-3-12b-it-4bit"


def _runtime() -> str:
    for candidate in (os.environ.get("STUDIOHUB_TEST_RUNTIME"),
                      str(WORKTREE / ".build/voice-runtime/bin/python3"),
                      str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3")):
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


CHILD = r'''
import json
from fastapi.testclient import TestClient

from components.hub import standalone
from components.hub.backend import auth

app = standalone.create_app(start_background=False)
token = auth.load_token()
headers = {"X-Hub-Token": token}
out = {}
with TestClient(app) as client:
    packages = client.get("/api/hub/model-packages", headers=headers)
    out["packages"] = [packages.status_code, packages.json()]
    catalog = client.get("/api/hub/catalog", headers=headers)
    out["catalog"] = [catalog.status_code, catalog.json()]
    capabilities = client.get("/api/hub/capabilities", headers=headers)
    out["capabilities"] = [capabilities.status_code, capabilities.json()]
    for name, model in (("ltx", "baa-ai/LTX-2.3-22B-RAM-12GB-MLX"),
                        ("gemma", "mlx-community/gemma-3-12b-it-4bit"),
                        ("unknown", "nobody/never-heard-of-it")):
        job = client.post("/api/hub/jobs", headers=headers, json={
            "modality": "video", "model": model,
            "items": [{"prompt": "a boat at dusk"}]})
        out["job_" + name] = [job.status_code, job.json()]
print("RESULT:" + json.dumps(out))
'''


class HubWithoutVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="studiohub-no-video-")
        root = Path(cls.temporary.name)
        library = root / "model-library"
        library.mkdir()
        workers = root / "private-workers.json"
        workers.write_text(json.dumps([{
            "id": "voice", "title": "Voice Studio", "modality": "voice", "host": "127.0.0.1",
            "port": 51102, "machine": "local", "local": True, "hub_token": "fixture-token",
            "runtime_identity": {"id": "r", "compatible": True}, "managed_output_roots": []}]))
        os.chmod(workers, 0o600)
        release = root / "release.json"
        release.write_text(json.dumps({"version": "0.0.0", "components": {}}))
        env = os.environ.copy()
        env.update({
            "STUDIOHUB_DATA_DIR": str(root / "state"),
            "STUDIOHUB_WORKERS_FILE": str(workers),
            "STUDIOHUB_MODEL_MANIFESTS": str(WORKTREE / "model-manifests"),   # the REAL catalog
            "STUDIOHUB_MODELS_DIR": str(library),
            "STUDIOHUB_PACKAGED": "1",
            "STUDIOHUB_RELEASE_MANIFEST": str(release),
            "PYTHONPATH": os.pathsep.join([str(WORKTREE), os.environ.get("PYTHONPATH", "")])
                         .rstrip(os.pathsep),
        })
        done = subprocess.run([_runtime(), "-c", textwrap.dedent(CHILD)], cwd=WORKTREE, env=env,
                              text=True, capture_output=True, check=False, timeout=240)
        assert done.returncode == 0, f"fixture failed:\n{done.stdout}\n{done.stderr}"
        line = next(row for row in reversed(done.stdout.splitlines()) if row.startswith("RESULT:"))
        cls.out = json.loads(line[len("RESULT:"):])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_the_package_route_answers_and_has_no_ltx_but_keeps_gemma(self) -> None:
        status, body = self.out["packages"]
        self.assertEqual(status, 200)
        ids = {row["package_id"] for row in body["models"]}
        models = {row["model_id"] for row in body["models"]}
        self.assertNotIn(LTX_PACKAGE, ids)
        self.assertNotIn(LTX_MODEL, models)
        self.assertIn(GEMMA_PACKAGE, ids, "Gemma is not retired: the owner may reuse it as a chat model")
        self.assertIn("whisper-turbo", ids, "the rest of the catalog must be untouched")
        self.assertGreaterEqual(len(ids), 20)

    def test_no_video_model_is_published_and_no_video_capability_is_reported(self) -> None:
        status, body = self.out["catalog"]
        self.assertEqual(status, 200)
        self.assertNotIn("ltx", json.dumps(body).lower())
        self.assertEqual([m for m in body.get("models", []) if (m.get("hub_modality") or m.get("modality")) == "video"], [])
        status, body = self.out["capabilities"]
        self.assertEqual(status, 200)
        self.assertNotIn("video.generation", json.dumps(body))

    def test_a_retired_or_missing_video_model_is_refused_by_name_not_queued(self) -> None:
        status, body = self.out["job_ltx"]
        self.assertEqual(status, 409)
        self.assertEqual(body["detail"]["code"], "MODEL_RETIRED")
        status, body = self.out["job_unknown"]
        self.assertEqual(status, 409)
        self.assertEqual(body["detail"]["code"], "MODEL_NOT_PACKAGED")
        # Gemma is in the catalog but no studio runs it: a clean refusal, not a 500 or a job that waits.
        status, body = self.out["job_gemma"]
        self.assertEqual(status, 409, body)
        self.assertIn(body["detail"]["code"], {"MODEL_NOT_READY", "STUDIO_NOT_RUNNING", "MODEL_NOT_INSTALLED"})


class InstalledRetiredModelsTests(unittest.TestCase):
    def test_an_installed_retired_package_goes_to_the_trash_folder_and_nothing_else_moves(self) -> None:
        sys.path.insert(0, str(WORKTREE))
        from components import retired_models
        with tempfile.TemporaryDirectory() as tmp:
            library, trash = Path(tmp) / "library", Path(tmp) / "trash"
            for name in (LTX_PACKAGE, GEMMA_PACKAGE, "whisper-turbo"):
                (library / name).mkdir(parents=True)
                (library / name / "weights.bin").write_text(name)
            (library / "Qwen").mkdir()                       # a repo id's first path part is never a package
            moved = retired_models.retire_installed(library, trash, now=1_790_000_000)
            self.assertEqual(moved, [LTX_PACKAGE])
            self.assertFalse((library / LTX_PACKAGE).exists())
            self.assertTrue((library / GEMMA_PACKAGE / "weights.bin").is_file(), "Gemma is not retired")
            self.assertTrue((library / "whisper-turbo" / "weights.bin").is_file())
            (landed,) = list(trash.glob("*/retired-models/*"))
            self.assertRegex(landed.parent.parent.name, r"^\d{4}-\d{2}-\d{2}$", "the Trash Folder's dated layout")
            self.assertEqual((landed / "weights.bin").read_text(), LTX_PACKAGE, "moved whole, never deleted")
            self.assertEqual(retired_models.retire_installed(library, trash), [], "a second start moves nothing")

    def test_a_symlinked_or_failing_move_is_skipped_not_followed(self) -> None:
        from components import retired_models
        with tempfile.TemporaryDirectory() as tmp:
            library, trash, elsewhere = Path(tmp) / "library", Path(tmp) / "trash", Path(tmp) / "elsewhere"
            library.mkdir()
            elsewhere.mkdir()
            (library / LTX_PACKAGE).symlink_to(elsewhere)
            self.assertEqual(retired_models.retire_installed(library, trash), [])
            self.assertTrue(elsewhere.is_dir())

    def test_the_hub_runs_it_at_start_up(self) -> None:
        source = (WORKTREE / "components/hub/standalone.py").read_text()
        self.assertIn("retire_installed_models()", source.split("shelve_installed_models()")[-1])


class ReleaseWithoutVideoTests(unittest.TestCase):
    def test_a_release_manifest_without_video_spawns_every_other_worker(self) -> None:
        sys.path.insert(0, str(WORKTREE / "packaging"))
        sys.path.insert(0, str(WORKTREE))
        import hub_worker
        from tests.test_chat_packaging import WorkerSpecTests

        helper = WorkerSpecTests()
        with tempfile.TemporaryDirectory() as tmp:
            resources = helper._resources(Path(tmp))
            # What a retired-video release really is: no worker file, no runtime, no source, no
            # LTX manifest, and no `video` key in the release manifest.
            for relative in ("video_worker.py", "runtimes/video/bin/python3",
                             "components/video/app/backend/main.py",
                             "model-manifests/ltx-2-3-22b-ram-12gb-mlx.json"):
                (resources / relative).unlink()
            components = {name: record for name, record in helper._components().items() if name != "video"}
            specs = helper._specs({"components": components}, resources)
        self.assertEqual({spec["id"] for spec in specs},
                         {"voice", "image", "subtitle", "music", "chat"})

    def test_the_installer_accepts_releases_with_or_without_video(self) -> None:
        """`verifyDeclaredComponents` checks only the components a manifest declares, and its table
        still knows `video` (an older or a future release that ships it must keep installing)."""
        source = (WORKTREE / "native/UpdateClient.swift").read_text()
        table = re.search(r"static let runtimePaths: \[String: \[String\]\] = \[(.*?)\n    \]", source, re.S).group(1)
        self.assertIn('"video":', table)
        verify = source[source.index("func verifyDeclaredComponents"):]
        self.assertIn("for (name, value) in components.sorted", verify.split("\n    }\n")[0],
                      "the installer must iterate what the manifest declares, not a fixed list")

    def test_the_swift_video_page_has_a_plain_no_model_state(self) -> None:
        views = (WORKTREE / "native/StudioViews.swift").read_text()
        self.assertIn("noVideoModel", views)
        self.assertIn("No video model is available", views)


if __name__ == "__main__":
    unittest.main()
