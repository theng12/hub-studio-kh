"""Durable policy state for bounded Image/Voice worker recovery.

Studios remain the source of exact job state, and GenStudio remains the only
owner allowed to retry work.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from pathlib import Path


MAX_STALL_S = 6 * 60 * 60.0
# The measured stall budget for each worker this Hub watches continuously. Only Image and Voice
# have one: the observer's progress signature (`_signature`) reads Image's step counters and
# Voice's chunk counters, and nothing else publishes them.
DEFAULT_STALL_S = {"image": 60 * 60.0, "voice": 2 * 60 * 60.0}
# The modalities this recovery covers, derived from the measured budgets above. A modality that
# is not here is *not* silently given the 6-hour ceiling: it is not recovered at all, and
# `stall_threshold_s` returns None so the caller cannot mistake "no budget" for "a very long
# one". Transcription, Music and Chat each own their queue and have no bounded stall policy yet;
# adding one means measuring its progress signature here, not inheriting Image's.
SUPPORTED_MODALITIES = frozenset(DEFAULT_STALL_S)
MAX_OBSERVATION_GAP_S = 5 * 60.0
IN_PROGRESS_PHASES = {"cancel_requested"}


def stall_threshold_s(modality: object) -> float | None:
    """The measured stall budget for a modality, or ``None`` when recovery does not cover it."""
    return DEFAULT_STALL_S.get(str(modality or ""))


# The (stage, total) pair each worker actually publishes while its job makes progress. Image
# Studio reports its denoise step; Voice Studio reports its long-form section index. The names are
# the workers' own — `components/image/app/backend/generation.py` writes `denoise_step`, and
# `components/voice/app/backend/generation.py` writes `chunk_index`. A signature is only evidence
# if it reads a field something writes: this table used to name `current_step`, which no worker in
# this app has ever published, so `stage_evidence` was false for every Image job and the whole
# observer could never fire.
_PROGRESS_FIELDS: dict[str, tuple[str, str]] = {
    "image": ("denoise_step", "denoise_steps"),
    "voice": ("chunk_index", "chunk_total"),
}


def progress_fields(modality: object) -> tuple[str, str] | None:
    """The ``(stage, total)`` pair this modality's worker publishes, or ``None`` when it has none."""
    return _PROGRESS_FIELDS.get(str(modality or ""))


def stall_reason(modality: object, silent_for: float) -> str:
    """The plain words for a stall stop, distinct from a deadline stop.

    The same shape as `chat_deadline.stall_reason`, because an operator reads both on the same
    Jobs page and they must not be mistaken for each other.
    """
    name = str(modality or "job") or "job"
    return f"stopped: no {name} progress for {_duration(silent_for)}"


def _duration(seconds: float) -> str:
    """Seconds as the words a person would say: hours, then minutes, then seconds."""
    value = max(0.0, float(seconds))
    for size, name in ((3600.0, "hour"), (60.0, "minute")):
        if value >= size * 1.5:
            whole = int(round(value / size))
            return f"{whole} {name}" + ("" if whole == 1 else "s")
    whole = max(1, int(round(value)))
    return f"{whole} second" + ("" if whole == 1 else "s")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _signature(modality: str, job: dict) -> list[object]:
    fields = progress_fields(modality)
    if fields is None:
        return [job.get("state"), job.get("progress")]
    return [job.get(field) for field in ("state", "progress", *fields)]


class AutomaticRecoveryState:
    """One continuously observed exact job per local Studio."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._observer_session = uuid.uuid4().hex
        self._state = self._load()

    def _load(self) -> dict:
        healthy = True
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            value = {}
        except (OSError, ValueError, TypeError):
            value, healthy = {}, False
        if self.path.exists() and (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(value.get("jobs"), dict)
            or not all(isinstance(row, dict) for row in value.get("jobs", {}).values())
            or value.get("state_healthy", True) is not True
        ):
            value, healthy = {}, False
        jobs = {}
        for studio_id, raw in value.get("jobs", {}).items():
            row = dict(raw)
            if row.get("phase") in IN_PROGRESS_PHASES:
                row.update(
                    phase="manual_action_required",
                    reason="The Hub restarted during local recovery; inspect the exact job before acting.",
                )
            jobs[str(studio_id)] = row
        return {
            "schema_version": 1,
            "jobs": jobs,
            "updated_at": _number(value.get("updated_at")),
            "state_healthy": healthy,
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def observe(
        self, studio_id: str, modality: str, job: dict, *, now: float | None = None,
    ) -> dict:
        now = float(time.time() if now is None else now)
        job_id = str(job.get("id") or "")
        model = str(job.get("model") or (job.get("params") or {}).get("repo") or "")[:500]
        signature = _signature(modality, job)
        threshold = stall_threshold_s(modality)
        stage_field, total_field = progress_fields(modality) or (None, None)
        stage = _number(job.get(stage_field))
        total = _number(job.get(total_field))
        stage_evidence = bool(stage is not None and total is not None and total > 0 and stage <= total)
        with self._lock:
            previous = self._state["jobs"].get(studio_id)
            same = isinstance(previous, dict) and previous.get("job_id") == job_id
            continuously_observed = bool(
                same
                and previous.get("observer_session") == self._observer_session
                and (last_checked := _number(previous.get("last_checked_at"))) is not None
                and 0 <= now - last_checked <= MAX_OBSERVATION_GAP_S
            )
            meaningful_at = (
                float(previous.get("last_meaningful_at") or now)
                if continuously_observed and previous.get("signature") == signature else now
            )
            first_seen = float(previous.get("first_seen_at") or now) if same else now
            started_at = _number(job.get("started_at"))
            elapsed = now - started_at if started_at is not None else 0.0
            no_progress = now - meaningful_at
            eligible = bool(
                job_id and threshold is not None and job.get("state") == "running"
                and self._state["state_healthy"] and stage_evidence
                and elapsed >= threshold and no_progress >= threshold
            )
            protected_phase = (
                previous.get("phase")
                if same and previous.get("phase") not in {None, "watching", "eligible"}
                else None
            )
            if protected_phase:
                eligible = False
            row = {
                "studio": studio_id, "modality": modality, "job_id": job_id,
                "model": model or None,
                "phase": protected_phase or ("eligible" if eligible else "watching"),
                "first_seen_at": first_seen, "last_meaningful_at": meaningful_at,
                "last_checked_at": now, "stall_after_s": threshold,
                "elapsed_s": max(0.0, elapsed), "no_progress_s": max(0.0, no_progress),
                "stage_evidence": stage_evidence,
                "signature": signature, "observer_session": self._observer_session,
            }
            if protected_phase and previous.get("reason"):
                row["reason"] = previous["reason"]
            self._state["jobs"][studio_id] = row
            self._state["updated_at"] = now
            self._save()
            return {**row, "eligible": eligible}

    def mark(
        self, studio_id: str, job_id: str, phase: str, reason: str,
        *, now: float | None = None,
    ) -> dict:
        now = float(time.time() if now is None else now)
        with self._lock:
            row = dict(self._state["jobs"].get(studio_id) or {})
            if row.get("job_id") != job_id:
                row = {"studio": studio_id, "job_id": job_id}
            row.update(
                phase=str(phase)[:80], reason=str(reason)[:500], last_checked_at=now,
            )
            self._state["jobs"][studio_id] = row
            self._state["updated_at"] = now
            self._save()
            return dict(row)

    def snapshot(self, *, now: float | None = None) -> dict:
        now = float(time.time() if now is None else now)
        with self._lock:
            rows = []
            for studio_id, raw in sorted(self._state["jobs"].items()):
                row = {
                    key: value for key, value in raw.items()
                    if key not in {"signature", "observer_session"}
                }
                rows.append(row)
            attention = next((
                row for row in rows
                if row.get("phase") == "manual_action_required"
            ), None)
            recovering = next((
                row for row in rows
                if row.get("phase") in IN_PROGRESS_PHASES
            ), None)
            healthy = self._state["state_healthy"]
            if not healthy:
                state = "attention"
                reason = "Automatic cancellation is disabled because its durable state is invalid."
            elif attention:
                state = "attention"
                reason = attention.get("reason") or "A local recovery needs manual attention."
            elif recovering:
                state = "recovering"
                reason = recovering.get("reason") or "A bounded local recovery is in progress."
            else:
                state = "watching"
                reason = "Exact Image and Voice jobs are being watched with conservative stall windows."
            return {
                "enabled": True,
                "state": state,
                "reason": reason,
                "state_healthy": healthy,
                "policy": {
                    # Named per modality from the measured budgets, plus the covered set and the
                    # reason an uncovered modality is not recovered at all.
                    **{f"{name}_stall_s": seconds
                       for name, seconds in DEFAULT_STALL_S.items()},
                    "recovered_modalities": sorted(SUPPORTED_MODALITIES),
                    "uncovered_modalities_note": (
                        "a modality with no measured stall budget is not recovered by this "
                        "observer; it is not given a longer default"),
                    "max_observation_gap_s": MAX_OBSERVATION_GAP_S,
                    "service_restart": "manual",
                },
                "jobs": rows,
                "updated_at": self._state.get("updated_at"),
            }
