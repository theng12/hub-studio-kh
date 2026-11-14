"""A Hub with no video model is a plain state, not a fault, and a retired model is never offered.

The Hub's one video pack is retired (`components/retired_models.py`), so the release ships no Video
worker (`packaging/stage_voice.py`). These tests run the real Hub app against the REAL shipped
manifest catalog, with only a Voice worker registered, and check what a person or the gateway would
meet:

* the catalog and package routes answer 200, no retired model is in either, and every other package
  still is;
* the Hub reports no video capability (no video model, no video operation);
* a job for a retired model, and a job for a model that is not there, are refused with a named
  code, never a 500 and never a queued job that waits for ever;
* a release manifest without a `video` component starts every other worker (the installer and the
  Hub both accept releases with or without Video, which the 0.6.120 update incident depended on).

The ids come from `RETIRED_MODELS` itself; a test that wrote them out would keep passing after the
list changed, and would be the one place a retired product was still named.
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
sys.path.insert(0, str(WORKTREE))

from components import retired_models  # noqa: E402

#: Package ids (a plain folder name in the library) and model/repo ids, both read from the list.
RETIRED_PACKAGES = [name for name in retired_models.RETIRED_MODELS if "/" not in name]
RETIRED_MODEL_IDS = [name for name in retired_models.RETIRED_MODELS if "/" in name]
RETIRED_PACKAGE, OTHER_RETIRED_PACKAGE = RETIRED_PACKAGES[0], RETIRED_PACKAGES[1]
RETIRED_MODEL, OTHER_RETIRED_MODEL = RETIRED_MODEL_IDS[0], RETIRED_MODEL_IDS[1]


def _runtime() -> str:
    for candidate in (os.environ.get("STUDIOHUB_TEST_RUNTIME"),
                      str(WORKTREE / ".build/voice-runtime/bin/python3"),
                      str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3")):
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


CHILD = r'''
import json, os
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
    for name, model in (("retired", os.environ["TEST_RETIRED_MODEL"]),
                        ("retired_two", os.environ["TEST_RETIRED_MODEL_TWO"]),
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
            "TEST_RETIRED_MODEL": RETIRED_MODEL,
            "TEST_RETIRED_MODEL_TWO": OTHER_RETIRED_MODEL,
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

    def test_the_package_route_answers_and_offers_no_retired_package(self) -> None:
        status, body = self.out["packages"]
        self.assertEqual(status, 200)
        ids = {row["package_id"] for row in body["models"]}
        models = {row["model_id"] for row in body["models"]}
        for package_id in RETIRED_PACKAGES:
            self.assertNotIn(package_id, ids, f"{package_id} is on the retired list")
        for model_id in RETIRED_MODEL_IDS:
            self.assertNotIn(model_id, models, f"{model_id} is on the retired list")
        self.assertIn("whisper-turbo", ids, "the rest of the catalog must be untouched")
        self.assertGreaterEqual(len(ids), 20)

    def test_no_video_model_is_published_and_no_video_capability_is_reported(self) -> None:
        status, body = self.out["catalog"]
        self.assertEqual(status, 200)
        self.assertEqual([m for m in body.get("models", []) if (m.get("hub_modality") or m.get("modality")) == "video"], [])
        published = json.dumps(body).lower()
        for model_id in RETIRED_MODEL_IDS:
            self.assertNotIn(model_id.lower(), published)
        status, body = self.out["capabilities"]
        self.assertEqual(status, 200)
        self.assertNotIn("video.generation", json.dumps(body))

    def test_a_retired_or_missing_video_model_is_refused_by_name_not_queued(self) -> None:
        for name in ("retired", "retired_two"):
            status, body = self.out["job_" + name]
            self.assertEqual(status, 409, body)
            self.assertEqual(body["detail"]["code"], "MODEL_RETIRED")
        status, body = self.out["job_unknown"]
        self.assertEqual(status, 409)
        self.assertEqual(body["detail"]["code"], "MODEL_NOT_PACKAGED")


class InstalledRetiredModelsTests(unittest.TestCase):
    def test_an_installed_retired_package_goes_to_the_trash_folder_and_nothing_else_moves(self) -> None:
        from components import retired_models
        with tempfile.TemporaryDirectory() as tmp:
            library, trash = Path(tmp) / "library", Path(tmp) / "trash"
            for name in (RETIRED_PACKAGE, OTHER_RETIRED_PACKAGE, "whisper-turbo"):
                (library / name).mkdir(parents=True)
                (library / name / "weights.bin").write_text(name)
            (library / "Qwen").mkdir()                       # a repo id's first path part is never a package
            moved = retired_models.retire_installed(library, trash, now=1_790_000_000)
            # The order is the retirement list's, so the two entries move in the order it names them.
            self.assertEqual(moved, [RETIRED_PACKAGE, OTHER_RETIRED_PACKAGE])
            self.assertFalse((library / RETIRED_PACKAGE).exists())
            self.assertFalse((library / OTHER_RETIRED_PACKAGE).exists())
            self.assertTrue((library / "whisper-turbo" / "weights.bin").is_file())
            landed_all = sorted(trash.glob("*/retired-models/*"))
            self.assertEqual(len(landed_all), 2)
            landed = next(p for p in landed_all if p.name.startswith(RETIRED_PACKAGE))
            self.assertRegex(landed.parent.parent.name, r"^\d{4}-\d{2}-\d{2}$", "the Trash Folder's dated layout")
            self.assertEqual((landed / "weights.bin").read_text(), RETIRED_PACKAGE, "moved whole, never deleted")
            self.assertEqual(retired_models.retire_installed(library, trash), [], "a second start moves nothing")

    def test_the_retired_receipt_leaves_installed_json_with_the_folder(self) -> None:
        """A retired package's line in `.studiohub/installed.json` goes with its folder, atomically.

        The Hub already read a missing folder as "not installed", but `verify-kit.sh` walks the whole index
        and counted every file of the moved package as missing, and the kit manifest listed it as installed.
        """
        import fcntl
        from components import model_packages, retired_models
        with tempfile.TemporaryDirectory() as tmp:
            library, trash = Path(tmp) / "library", Path(tmp) / "trash"
            state = library / ".studiohub"
            state.mkdir(parents=True, mode=0o700)
            os.chmod(state, 0o700)
            receipt = lambda name: {"revision": "r1", "folder": f"{name}/r1", "files": {"w.bin": {"st_size": 1}}}
            index = {name: receipt(name) for name in
                     (RETIRED_PACKAGE, OTHER_RETIRED_PACKAGE, "whisper-turbo", "old-other-gone")}
            (state / "installed.json").write_text(json.dumps(index, sort_keys=True))
            for name in (RETIRED_PACKAGE, OTHER_RETIRED_PACKAGE, "whisper-turbo"):
                (library / name / "r1").mkdir(parents=True)
                (library / name / "r1" / "w.bin").write_text("x")
            # The index is busy (an import holds the Hub's lock): the folder still moves, the receipt waits.
            fd = os.open(state / "import.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self.assertEqual(retired_models.retire_installed(library, trash),
                                 [RETIRED_PACKAGE, OTHER_RETIRED_PACKAGE])
            finally:
                os.close(fd)
            self.assertIn(RETIRED_PACKAGE, json.loads((state / "installed.json").read_text()))
            # The next start (nothing left to move) catches up: only the retired receipt is dropped.
            self.assertEqual(retired_models.retire_installed(library, trash), [])
            kept = json.loads((state / "installed.json").read_text())
            self.assertEqual(sorted(kept), ["old-other-gone", "whisper-turbo"],
                             "other receipts, even one with no folder, are not this list's to forget")
            self.assertEqual((state / "installed.json").read_text(), json.dumps(kept, sort_keys=True),
                             "the same format `_register` writes")
            self.assertEqual(os.stat(state / "installed.json").st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in state.iterdir() if p.name.startswith("index-")], [])
            self.assertEqual(model_packages.forget_packages(library, [RETIRED_PACKAGE]), [], "idempotent")
            # A receipt whose folder is still there is never dropped.
            self.assertEqual(model_packages.forget_packages(library, ["whisper-turbo"]), [])
            # An unreadable index is left exactly as it is.
            (state / "installed.json").write_text("not json")
            self.assertEqual(retired_models.retire_installed(library, trash), [])
            self.assertEqual((state / "installed.json").read_text(), "not json")

    def test_a_symlinked_or_failing_move_is_skipped_not_followed(self) -> None:
        from components import retired_models
        with tempfile.TemporaryDirectory() as tmp:
            library, trash, elsewhere = Path(tmp) / "library", Path(tmp) / "trash", Path(tmp) / "elsewhere"
            library.mkdir()
            elsewhere.mkdir()
            (library / RETIRED_PACKAGE).symlink_to(elsewhere)
            self.assertEqual(retired_models.retire_installed(library, trash), [])
            self.assertTrue(elsewhere.is_dir())

    def test_the_hub_runs_it_at_start_up(self) -> None:
        # Retirement is the only list that still moves an installed package out, so it has to be
        # the one start-up passes (shelving was deleted on 2026-10-28, and is asserted absent).
        source = (WORKTREE / "components/hub/standalone.py").read_text()
        self.assertIn("retire_installed_models()", source)
        self.assertNotIn("shelve_installed_models", source)


class ReleaseWithoutVideoTests(unittest.TestCase):
    def test_a_release_manifest_without_video_spawns_every_other_worker(self) -> None:
        sys.path.insert(0, str(WORKTREE / "packaging"))
        import hub_worker
        from tests.test_chat_packaging import WorkerSpecTests

        helper = WorkerSpecTests()
        with tempfile.TemporaryDirectory() as tmp:
            resources = helper._resources(Path(tmp))
            # What a release without a Video worker really is: no worker file, no runtime, no
            # source, no video manifest, and no `video` key in the release manifest.
            for relative in ("video_worker.py", "runtimes/video/bin/python3",
                             "components/video/app/backend/main.py",
                             f"model-manifests/{hub_worker.VIDEO_MODEL_PACKAGE}.json"):
                (resources / relative).unlink()
            components = {name: record for name, record in helper._components().items() if name != "video"}
            specs = helper._specs({"components": components}, resources)
        self.assertEqual({spec["id"] for spec in specs},
                         {"voice", "image", "subtitle", "music", "chat", "embeddings", "vision"})

    def test_the_swift_video_page_has_a_plain_no_model_state(self) -> None:
        views = (WORKTREE / "native/StudioViews.swift").read_text()
        self.assertIn("noVideoModel", views)
        self.assertIn("No video model is available", views)


if __name__ == "__main__":
    unittest.main()
