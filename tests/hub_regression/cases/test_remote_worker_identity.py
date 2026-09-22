"""A remote worker's own machine id is not the node id the Hub routes through.

The Hub stores the routing machine (the node id) on the item, while the worker
reports its own local machine id, so comparing them failed every remote attempt.
"""
from __future__ import annotations

import pytest

from components.hub.backend import broker


def _item() -> dict:
    return {"studio_job_id": "job-1", "model_revision": "r" * 40,
            "runtime_revision": "x" * 64, "worker_id": "music", "machine": "node-a"}


def _job() -> dict:
    return {"id": "job-1", "state": "running", "model_revision": "r" * 40,
            "runtime_revision": "x" * 64, "worker_id": "music", "machine_id": "local"}


def test_a_remote_worker_may_report_its_own_machine_id():
    broker._validate_music_worker_job(_item(), _job(), remote=True)
    broker._validate_image_worker_job(_item(), _job(), remote=True)


def test_a_local_worker_machine_id_must_still_match():
    with pytest.raises(ValueError):
        broker._validate_music_worker_job(_item(), _job())
    with pytest.raises(ValueError):
        broker._validate_image_worker_job(_item(), _job())


def test_other_identity_changes_are_still_rejected_remotely():
    job = _job()
    job["runtime_revision"] = "y" * 64
    with pytest.raises(ValueError):
        broker._validate_music_worker_job(_item(), job, remote=True)
