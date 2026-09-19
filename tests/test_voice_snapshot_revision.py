"""The model files loaded must be the revision the job records as evidence.

`_mlx_audio_snapshot_path` returned `candidates[0]` from the Hugging Face cache, and only consulted
the verified-package library when the cache had no `snapshots` folder at all. A Mac can hold several
revisions: the one the app verified and the manifest names, plus an older one left in the cache. When
both were present the loader took whichever the filesystem listed first, so a job could run older
files while writing the newer revision into its record - wrong evidence, and a result that cannot be
reproduced from what the record says.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.voice.app.backend import generation  # noqa: E402

MANAGER = generation.GenerationManager.__new__(generation.GenerationManager)


class SnapshotRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _cache_with(self, revisions: list[tuple[str, float]]) -> Path:
        snapshots = self.root / "hub" / "models--org--repo" / "snapshots"
        snapshots.mkdir(parents=True)
        for name, age in revisions:
            folder = snapshots / name
            folder.mkdir()
            stamp = time.time() - age
            os.utime(folder, (stamp, stamp))
        return snapshots

    def test_the_verified_revision_wins_over_the_cache(self):
        self._cache_with([("oldrevision", 9000), ("newrevision", 10)])
        verified = self.root / "library" / "org--repo" / "verifiedrev"
        verified.mkdir(parents=True)
        with mock.patch.object(generation.cache, "repo_cache_dir",
                               lambda repo: self.root / "hub" / "models--org--repo"), \
             mock.patch.object(generation.cache, "package_snapshot",
                               lambda repo: ("org--repo", verified)):
            resolved = MANAGER._mlx_audio_snapshot_path("org/repo")
        self.assertEqual(resolved, verified,
                         "the verified revision is the one the manifest names and the job records")

    def test_without_a_verified_package_the_newest_cached_revision_is_used(self):
        self._cache_with([("oldrevision", 9000), ("newest", 5)])
        with mock.patch.object(generation.cache, "repo_cache_dir",
                               lambda repo: self.root / "hub" / "models--org--repo"), \
             mock.patch.object(generation.cache, "package_snapshot", lambda repo: None):
            first = MANAGER._mlx_audio_snapshot_path("org/repo")
            second = MANAGER._mlx_audio_snapshot_path("org/repo")
        self.assertEqual(first.name, "newest")
        self.assertEqual(first, second, "the choice must not depend on filesystem listing order")

    def test_a_missing_cache_still_says_what_to_do(self):
        with mock.patch.object(generation.cache, "repo_cache_dir",
                               lambda repo: self.root / "nothing"), \
             mock.patch.object(generation.cache, "package_snapshot", lambda repo: None):
            with self.assertRaises(RuntimeError) as caught:
                MANAGER._mlx_audio_snapshot_path("org/repo")
        self.assertIn("Models tab", str(caught.exception))

    def test_a_single_cached_revision_needs_no_choice(self):
        self._cache_with([("onlyone", 100)])
        with mock.patch.object(generation.cache, "repo_cache_dir",
                               lambda repo: self.root / "hub" / "models--org--repo"), \
             mock.patch.object(generation.cache, "package_snapshot", lambda repo: None):
            self.assertEqual(MANAGER._mlx_audio_snapshot_path("org/repo").name, "onlyone")


if __name__ == "__main__":
    unittest.main()
