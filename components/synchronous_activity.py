"""Bounded activity state for synchronous workers with one real executor."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import CancelledError
from dataclasses import dataclass
import threading
import time
from typing import Callable, TypeVar
from uuid import uuid4


_T = TypeVar("_T")


@dataclass(slots=True)
class _Job:
    id: str
    model: str
    created_at: float
    state: str = "queued"
    started_at: float | None = None
    preparing: bool = False


class SynchronousActivity:
    """Keep one running/queued row and the latest terminal row, never payloads."""

    def __init__(self, studio: str) -> None:
        self._studio = studio
        self._lock = threading.Lock()
        self._queued: OrderedDict[str, _Job] = OrderedDict()
        self._running: _Job | None = None
        self._latest: dict | None = None

    def queue(self, model: str) -> _Job:
        job = _Job(uuid4().hex, model, time.time())
        with self._lock:
            self._queued[job.id] = job
        return job

    def prepare(self, job: _Job, function: Callable[..., _T], *args, **kwargs) -> _T:
        """Run accepted preparation without reporting terminal before its thread exits."""
        with self._lock:
            if job.state != "queued":
                raise CancelledError()
            job.preparing = True
        try:
            return function(*args, **kwargs)
        finally:
            with self._lock:
                job.preparing = False
                if job.state == "cancel_requested":
                    self._cancelled(job, time.time())

    def run(self, job: _Job, function: Callable[..., _T], *args, **kwargs) -> _T:
        """Run on the worker's real executor, which owns running and terminal state."""
        with self._lock:
            if job.state != "queued":
                raise CancelledError()
            job.state = "running"
            job.started_at = time.time()
            self._queued.pop(job.id, None)
            self._running = job
        try:
            result = function(*args, **kwargs)
        except BaseException:
            self._finish(job, "error", error_code="INFERENCE_FAILED")
            raise
        self._finish(job, "done")
        return result

    def submission_failed(self, job: _Job) -> None:
        """Close a row if the executor refuses the submitted callable."""
        self._finish(job, "error", error_code="EXECUTOR_SUBMISSION_FAILED")

    def cancel_queued(self, job: _Job) -> bool:
        """Cancel only work the real executor has not claimed."""
        with self._lock:
            if job.state != "queued":
                return False
            if job.preparing:
                job.state = "cancel_requested"
            else:
                self._cancelled(job, time.time())
            return True

    def discard(self, job: _Job) -> None:
        """Remove preparation rejected before the inference executor accepted it."""
        with self._lock:
            if job.state == "queued":
                job.state = "discarded"
                self._queued.pop(job.id, None)

    def snapshot(self) -> dict:
        with self._lock:
            observed_at = time.time()
            active = self._running
            if active is None and self._queued:
                active = next(reversed(self._queued.values()))
            active_row = self._active(active, observed_at) if active else None
            latest = dict(self._latest) if self._latest else None
        return {
            "schema": "kh-studio.activity.v1", "studio": self._studio,
            "observed_at": observed_at, "active": active_row, "latest": latest,
        }

    def _finish(self, job: _Job, state: str, *, error_code: str | None = None) -> None:
        finished_at = time.time()
        with self._lock:
            if job.state not in {"queued", "running"}:
                return
            job.state = state
            self._queued.pop(job.id, None)
            if self._running is job:
                self._running = None
            self._latest = self._terminal(job, state, finished_at, error_code=error_code)

    def _cancelled(self, job: _Job, finished_at: float) -> None:
        job.state = "cancelled"
        self._queued.pop(job.id, None)
        self._latest = self._terminal(job, "cancelled", finished_at)

    def _base(self, job: _Job) -> dict:
        return {
            "id": job.id, "state": job.state, "model": job.model,
            "operation": self._studio, "source": "direct", "origin": "unknown",
            "created_at": job.created_at,
        }

    def _active(self, job: _Job, observed_at: float) -> dict:
        row = {**self._base(job), "updated_at": observed_at}
        if job.started_at is not None:
            row["started_at"] = job.started_at
        return row

    def _terminal(self, job: _Job, state: str, finished_at: float,
                  *, error_code: str | None = None) -> dict:
        row = {**self._base(job), "state": state, "updated_at": finished_at,
               "finished_at": finished_at}
        if job.started_at is not None:
            row["started_at"] = job.started_at
            row["runtime_s"] = round(max(0.0, finished_at - job.started_at), 3)
        if error_code:
            row["error_code"] = error_code
        return row
