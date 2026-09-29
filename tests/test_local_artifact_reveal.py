"""A finished job's path is published only when the file is on *this* Mac.

**The owner's report, 2026-09-29:** *"even on this machine, i can't click show in finder too."*

He was describing a control that could never work. The app has had a **Show in Folder** button on
every finished row since the Jobs page existed, and `NativeJob.canRevealArtifact` decides whether it
is enabled by reading `item.artifactPath` from the Hub's job payload and checking that the file is
there. `broker.public_item` — the one function every job payload is built by — dropped
`artifact_path` from every item it published, so the field arrived as nil on every row of every
machine, the existence check was never reached, and the button was disabled everywhere. The app's
own docstring describes the intended behaviour exactly: *"The Hub records the worker's
`output_path` on the item and this reveals exactly that."* It was the publication that did not.

The distinction the fix turns on is **whose** path it is. A path recorded by a worker on another Mac
names a file on that Mac's disk; publishing it would offer the operator a reveal that Finder cannot
perform, and it is the reason the field was being withheld at all. So it is published for exactly
the items this Mac produced — `peers.is_local_studio`, the same test the broker uses to decide
whether a worker's memory is this Mac's memory — and the app's own `FileManager.fileExists` check
covers the rest. A job that ran on another Mac still gets a disabled button with the tooltip that
says why, which is the behaviour the app was written for.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from components.hub.backend import broker

ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "native" / "StudioAPI.swift"

_PATH = "/Users/someone/Library/Application Support/StudioHub/media/clip-1.wav"


def _batch() -> dict:
    return {"id": "batch-1", "modality": "voice", "model": "fixture/voice"}


def _item(**overrides) -> dict:
    item = {
        "index": 0, "state": "done", "prompt": "hello", "params": {},
        "studio": "voice", "artifact_path": _PATH, "artifact_url": None,
        "worker_artifact_url": "http://127.0.0.1:65461/outputs/clip-1.wav",
        "worker_accepted_params": {"secret": "worker-local receipt"},
    }
    item.update(overrides)
    return item


class _MonitorCase(unittest.TestCase):
    """A monitor is a global on the broker, so every case has to put back what it found."""

    registry: list[dict] = []

    def setUp(self) -> None:
        self._previous = broker._packaged_monitor
        broker.configure_monitor(SimpleNamespace(registry=list(self.registry)))

    def tearDown(self) -> None:
        broker.configure_monitor(self._previous)


class ALocalJobPublishesItsPathTest(_MonitorCase):
    registry = [{"id": "voice", "modality": "voice", "machine": "local"}]

    def test_a_local_job_publishes_the_path_its_worker_wrote(self) -> None:
        published = broker.public_item(_batch(), _item())
        self.assertEqual(_PATH, published.get("artifact_path"),
                         "a job this Mac produced does not carry its path, so Show in Folder is "
                         "disabled on the one machine that could open it")

    def test_a_local_job_is_recognised_without_a_machine_key(self) -> None:
        # `is_local_studio` reads a missing `machine` as local, which is how a single-Mac install
        # registers its own workers. The reveal has to work on the common case, not only on the
        # shape that spells it out.
        broker.configure_monitor(SimpleNamespace(registry=[{"id": "voice", "modality": "voice"}]))
        self.assertEqual(_PATH, broker.public_item(_batch(), _item()).get("artifact_path"))

    def test_the_hub_artifact_url_is_still_the_stable_identity(self) -> None:
        # The path is an addition, never a replacement: a client that cannot read this Mac's disk
        # still fetches the artifact through the Hub.
        published = broker.public_item(_batch(), _item())
        self.assertEqual("/api/hub/jobs/batch-1/items/0/artifact", published.get("artifact_url"))


class AnotherMacsJobKeepsItsPathTest(_MonitorCase):
    registry = [{"id": "voice", "modality": "voice", "machine": "studio-2"}]

    def test_a_remote_job_publishes_no_path(self) -> None:
        published = broker.public_item(_batch(), _item())
        self.assertNotIn("artifact_path", published,
                         "a file on another Mac is advertised as revealable on this one")

    def test_a_remote_job_still_publishes_its_hub_url(self) -> None:
        # Withholding the path must not withhold the result: the artifact is still fetchable
        # through the Hub, which is how a remote job has always been read.
        published = broker.public_item(_batch(), _item())
        self.assertEqual("/api/hub/jobs/batch-1/items/0/artifact", published.get("artifact_url"))


class NoPathIsInventedTest(_MonitorCase):
    registry = [{"id": "voice", "modality": "voice", "machine": "local"}]

    def test_an_item_with_no_recorded_path_publishes_none(self) -> None:
        published = broker.public_item(_batch(), _item(artifact_path=None))
        self.assertNotIn("artifact_path", published)

    def test_an_undispatched_item_publishes_none(self) -> None:
        # No studio means nothing ran, so there is no file and nothing to reveal.
        published = broker.public_item(_batch(), _item(studio=None))
        self.assertNotIn("artifact_path", published)

    def test_a_worker_that_is_no_longer_registered_publishes_none(self) -> None:
        # The registry is how the Hub knows a job's file is local; with the worker gone that
        # cannot be established, and a guess is worse than the disabled button.
        broker.configure_monitor(SimpleNamespace(registry=[]))
        self.assertNotIn("artifact_path", broker.public_item(_batch(), _item()))

    def test_a_path_that_is_not_a_string_publishes_none(self) -> None:
        self.assertIsNone(broker.local_artifact_path(_item(artifact_path=17)))
        self.assertIsNone(broker.local_artifact_path(_item(artifact_path="")))

    def test_an_empty_path_publishes_none(self) -> None:
        self.assertNotIn("artifact_path", broker.public_item(_batch(), _item(artifact_path="")))


class WorkerInternalsNeverTravelTest(_MonitorCase):
    registry = [{"id": "voice", "modality": "voice", "machine": "local"}]

    def test_the_workers_own_url_and_params_are_still_withheld(self) -> None:
        # The reveal fix must not become a hole in the rule it was carved out of: a worker's port
        # belongs to nothing after the next restart, and its accepted params are a private receipt.
        published = broker.public_item(_batch(), _item())
        self.assertNotIn("worker_artifact_url", published)
        self.assertNotIn("worker_accepted_params", published)


class TheAppIsReadyToReceiveItTest(unittest.TestCase):
    """The other half of the contract, pinned where it is written."""

    def setUp(self) -> None:
        self.swift = SWIFT.read_text(encoding="utf-8")

    def test_the_row_decodes_the_path(self) -> None:
        self.assertIn("artifactPath: item?.artifactPath", self.swift,
                      "the app stopped reading the path the Hub now publishes")

    def test_the_button_enables_only_for_a_file_that_is_here(self) -> None:
        check = re.search(r"var canRevealArtifact: Bool \{.*?\n    \}", self.swift, re.S)
        self.assertIsNotNone(check, "canRevealArtifact is gone — the button has no gate")
        assert check is not None
        self.assertIn("fileExists", check.group(0),
                      "the reveal no longer checks that the file is on this Mac, so a remote "
                      "job's path would open Finder on nothing")

    def test_revealing_selects_the_file(self) -> None:
        reveal = re.search(r"func revealArtifact\(_ job: NativeJob\) -> Bool \{.*?\n    \}",
                           self.swift, re.S)
        self.assertIsNotNone(reveal, "revealArtifact is gone")
        assert reveal is not None
        self.assertIn("activateFileViewerSelecting", reveal.group(0))


if __name__ == "__main__":
    unittest.main()
