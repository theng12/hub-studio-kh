"""A model download that does not fit is refused up front, with a message that says why.

Nothing checked free space before a 6-10 GB download started, so one could begin on a node with
8 GB free and fill it. The check needs the download's size (what is still to fetch, so a resumable
partial is not counted twice) plus the 5 GB the Mac keeps free as working room.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GB = 1_000_000_000


class ModelDownloadSpaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data, self.library = root / "data", root / "models"
        self.data.mkdir()
        self.library.mkdir()
        patcher = mock.patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.data)})
        patcher.start()
        self.addCleanup(patcher.stop)
        from components.hub import model_downloads
        self.module = model_downloads
        patcher = mock.patch.object(model_downloads, "_path", lambda: self.data / "model_downloads.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manifest = {"package_id": "big", "model_id": "org/big", "revision": "a" * 40, "engine": "voice-mlx",
                         "minimum_macos": "0", "files": [{"path": "w.bin", "size": 8 * GB, "sha256": "0" * 64,
                                                          "urls": ["https://example.invalid/w"]}]}

    def start(self, free_gb: float):
        from components.hub import disk_governor
        usage = lambda _path: SimpleNamespace(total=500 * GB, used=0, free=int(free_gb * GB))
        with mock.patch.object(disk_governor, "disk_usage", usage), \
                mock.patch.object(self.module.subprocess, "Popen") as popen, \
                mock.patch("components.model_packages.manifest_digest", return_value="d" * 64):
            popen.return_value = mock.Mock(pid=4242)
            try:
                return self.module.start(package_id="big", manifest=self.manifest, catalog={"big": self.manifest},
                                         library=self.library, sources=None), popen
            except self.module.InsufficientDiskSpace as exc:
                return exc, popen

    def test_an_8_gb_download_on_a_node_with_9_gb_free_is_refused_and_starts_nothing(self):
        outcome, popen = self.start(free_gb=9)
        self.assertIsInstance(outcome, self.module.InsufficientDiskSpace)
        text = str(outcome)
        self.assertIn("8.0 GB", text)
        self.assertIn("9.0 GB is free", text)
        self.assertIn("Nothing was downloaded", text)
        popen.assert_not_called()
        self.assertFalse((self.data / "model-download-runs").exists(), "a run folder was created for a refused download")

    def test_the_same_download_starts_when_it_fits_with_the_margin(self):
        outcome, popen = self.start(free_gb=14)
        self.assertEqual(outcome["state"], "running")
        popen.assert_called_once()

    def test_what_a_resumable_partial_already_holds_is_not_counted_twice(self):
        staging = self.library / ".studiohub" / "staging" / ("big-" + "d" * 64)
        staging.mkdir(parents=True)
        (staging / "w.bin.part").write_bytes(b"x" * 1_000_000)
        with mock.patch.object(self.module, "_dir_bytes", return_value=6 * GB):
            outcome, popen = self.start(free_gb=8)                  # 2 GB left to fetch + 5 GB margin <= 8
        self.assertEqual(outcome["state"], "running")

    def test_the_route_answers_507_with_the_reason(self):
        source = (Path(__file__).resolve().parents[1] / "components/hub/standalone.py").read_text()
        self.assertIn("except model_downloads.InsufficientDiskSpace as exc:", source)
        self.assertIn("HTTPException(507, str(exc))", source)


if __name__ == "__main__":
    unittest.main()
