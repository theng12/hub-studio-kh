"""Persistent, fleet-wide Whisper transcription batches."""

import asyncio
import hashlib
import json
import os
import logging
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException, UploadFile

from . import broker, execution_identity, ledger, model_exposure, peers
from . import routing as routing_rule
from .peers import studio_request
from .registry import DATA_DIR, machine_enabled, studio_enabled

ROOT = DATA_DIR / "transcription_jobs"
SETTINGS_FILE = DATA_DIR / "transcription_settings.json"
CHUNK_BYTES = 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_BATCH_BYTES = 20 * 1024 * 1024 * 1024
MAX_FILES = 500
MAX_TRIES = 3
RETENTION_CHOICES = {1, 3, 7, 15, 30, 90}
ALLOWED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".webm", ".mp4", ".mov",
}
TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
AMBIGUOUS_RESPONSE_STATUS = {408, 504}
TERMINAL_STATES = {"done", "error", "cancelled"}

batches: dict[str, dict] = {}
busy_studios: set[str] = set()
_item_tasks: dict[tuple[str, int], asyncio.Task] = {}
_dispatcher_task: asyncio.Task | None = None
_cleanup_task: asyncio.Task | None = None
_wakeup = asyncio.Event()
_shutting_down = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcription_batches (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  finished INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcription_created
  ON transcription_batches(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transcription_idempotency
  ON transcription_batches(idempotency_key, finished);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(ledger.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _save(batch: dict) -> None:
    batch["updated_at"] = time.time()
    finished = int(all(i["state"] in TERMINAL_STATES for i in batch["items"]))
    if finished and not batch.get("finished_at"):
        batch["finished_at"] = batch["updated_at"]
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO transcription_batches "
            "(id,created_at,updated_at,finished,idempotency_key,payload) "
            "VALUES (?,?,?,?,?,?)",
            (batch["id"], batch["created_at"], batch["updated_at"], finished,
             batch["idempotency_key"], json.dumps(batch)),
        )
    from . import control_plane
    control_plane.queue_shadow_job("transcription", batch)


def _load_rows(where: str = "", params: tuple = ()) -> list[dict]:
    sql = "SELECT payload FROM transcription_batches"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [json.loads(row[0]) for row in rows]


async def acknowledge_item(monitor, batch_id: str, item_index: int) -> dict:
    """Verified-delivery receipt for one Subtitle artifact.

    The Image studio has had this since ST-03; Subtitle had no Hub-level acknowledgement
    at all, so a transcript could be exported and the Hub would never record delivery —
    which also kept the output indexed as unacknowledged for cleanup.

    The authenticated caller asserts it has verified delivery, so merely downloading an
    artifact never reaches this route. Mirrors the Image path: the worker's own idempotent
    receipt is the authority, and a failed local write stays retryable.
    """
    from .. import output_storage
    from urllib.parse import quote

    batch = get_batch(batch_id)
    item = next((row for row in (batch or {}).get("items") or []
                 if row.get("index") == item_index), None)
    if item is None:
        raise HTTPException(404, "unknown transcription item")
    if item.get("state") != "done":
        raise HTTPException(409, "only completed output can be acknowledged")
    if item.get("receipt_acked_at"):
        # The receipt is durable; a previous output-index write may have failed.
        output_storage.mark_receipt_acked(batch_id=batch_id, item_index=item_index)
        return {"ok": True}
    studio = next((row for row in monitor.registry if row["id"] == item.get("studio")), None)
    job_id = item.get("studio_task_id")
    if studio is None or not isinstance(job_id, str) or not job_id:
        raise HTTPException(404, "worker job is not available")
    url, headers = studio_request(
        studio, f'/api/generate/transcribe/jobs/{quote(job_id, safe="")}/ack')
    release_transfer = output_storage.acquire_transfer(batch_id=batch_id, item_index=item_index)
    try:
        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                response = await client.post(url, headers=headers, timeout=15)
                response.raise_for_status()
                try:
                    receipt = response.json()
                except ValueError as exc:
                    raise HTTPException(502, "Subtitle worker returned an invalid receipt") from exc
                if not isinstance(receipt, dict) or receipt.get("ok") is not True:
                    raise HTTPException(502, "Subtitle worker rejected receipt acknowledgement")
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Subtitle worker did not acknowledge receipt") from exc
        item["receipt_acked_at"] = time.time()
        try:
            _save(batch)
        except Exception:
            # A failed local save must remain retryable even though the worker
            # already accepted the idempotent receipt.
            item.pop("receipt_acked_at", None)
            raise
        # A failed index update is conservative: the worker and batch receipt stay
        # durable but shared cleanup still treats the output as unacknowledged.
        output_storage.mark_receipt_acked(batch_id=batch_id, item_index=item_index)
        return {"ok": True}
    finally:
        release_transfer()


def get_batch(batch_id: str) -> dict | None:
    if batch_id in batches:
        return batches[batch_id]
    rows = _load_rows("id = ?", (batch_id,))
    return rows[0] if rows else None


def _safe_identifier(value: str, field: str, max_length: int = 120) -> str:
    value = (value or "").strip()
    if not value or len(value) > max_length or value in {".", ".."}:
        raise HTTPException(400, f"invalid {field}")
    if "/" in value or "\\" in value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", value):
        raise HTTPException(400, f"invalid {field}")
    return value


def _safe_filename(value: str) -> str:
    value = (value or "").strip()
    if (
        not value
        or len(value) > 240
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise HTTPException(400, "invalid filename")
    # A filename is display metadata only: uploaded bytes are stored under a
    # generated path below. Keep path traversal protections while accepting
    # ordinary punctuation such as commas and parentheses from user files.
    return value


def _safe_model(value: str) -> str:
    value = (value or "").strip()
    if (not value or len(value) > 240 or value.startswith("/") or "\\" in value
            or ".." in value.split("/")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value)):
        raise HTTPException(400, "invalid model")
    return value


def _idempotency_key(project: str | None, episode: str | None, model: str,
                     item_ids: list[str], filenames: list[str]) -> str:
    canonical = json.dumps({
        "project": project or "", "episode": episode or "", "model": model,
        "items": list(zip(item_ids, filenames)),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _active_duplicate(key: str) -> dict | None:
    rows = _load_rows("idempotency_key = ? AND finished = 0", (key,))
    return rows[0] if rows else None


def _packaged() -> bool:
    return os.environ.get("STUDIOHUB_PACKAGED") == "1"


def _mark_uncertain(item: dict, reason: str) -> None:
    """Fence a possibly accepted HTTP inference until worker reconciliation exists."""
    item.update(state="uncertain", error=reason, interrupted=True,
                manual_action_required=True)


async def create_batch(files: list[UploadFile], item_ids: list[str], model: str,
                       language: str | None, word_timestamps: bool, label: str | None,
                       project: str | None, episode: str | None,
                       genstudio_execution_json: str | None = None,
                       routing: str = routing_rule.POOL,
                       deduplicate: bool = True, admission_guard=None) -> tuple[dict, bool]:
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"files must contain 1 to {MAX_FILES} uploads")
    if len(item_ids) != len(files):
        raise HTTPException(400, "item_ids must contain one stable ID per file")
    model = _safe_model(model)
    clean_ids = [_safe_identifier(v, "item_id") for v in item_ids]
    if len(set(clean_ids)) != len(clean_ids):
        raise HTTPException(400, "item_ids must be unique within a batch")
    filenames = [_safe_filename(f.filename or "") for f in files]
    for filename in filenames:
        if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, f"unsupported audio/video type: {Path(filename).suffix or 'none'}")
    clean_project = _safe_identifier(project, "project") if project else None
    clean_episode = _safe_identifier(episode, "episode") if episode else None
    clean_label = _safe_identifier(label, "label") if label else None
    execution = None
    if genstudio_execution_json:
        try:
            execution = json.loads(genstudio_execution_json)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                400, "genstudio_execution must be valid JSON"
            ) from exc
        if not isinstance(execution, dict):
            raise HTTPException(400, "genstudio_execution must be a JSON object")
        supplied_operation = execution.get("operation")
        if (supplied_operation is not None
                and str(supplied_operation).strip().lower()
                != "audio.transcription"):
            raise HTTPException(
                400,
                "GenStudio execution operation must be audio.transcription",
            )
    envelope = {
        "modality": "transcription",
        "model": model,
        "language": language or None,
        "word_timestamps": bool(word_timestamps),
        # Carried on the batch so the dispatcher can honour a machine pin later, at the moment
        # it actually chooses a Studio.
        "routing": routing,
        "project": clean_project,
        "episode": clean_episode,
        "items": [
            {"item_id": item_id, "filename": filename}
            for item_id, filename in zip(clean_ids, filenames)
        ],
    }
    if execution is not None:
        envelope["genstudio_execution"] = execution
    try:
        probe = execution_identity.prepare(envelope, probe_only=True)
    except execution_identity.ExecutionIdentityError as exc:
        raise HTTPException(409, str(exc)) from exc
    if probe.replay_batch_id:
        replay = get_batch(probe.replay_batch_id)
        if replay is not None:
            for upload in files:
                await upload.close()
            batches.setdefault(replay["id"], replay)
            return replay, True
    key = _idempotency_key(clean_project, clean_episode, model, clean_ids, filenames)
    duplicate = (
        _active_duplicate(key)
        if deduplicate and probe.evidence is None
        else None
    )
    if duplicate:
        for upload in files:
            await upload.close()
        batches.setdefault(duplicate["id"], duplicate)
        return duplicate, True

    if admission_guard is not None:
        try:
            refusal = admission_guard(envelope)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        if refusal:
            raise HTTPException(409, refusal)
    try:
        prepared = execution_identity.prepare(envelope)
    except execution_identity.ExecutionIdentityError as exc:
        raise HTTPException(409, str(exc)) from exc
    if prepared.replay_batch_id:
        replay = get_batch(prepared.replay_batch_id)
        if replay is not None:
            for upload in files:
                await upload.close()
            batches.setdefault(replay["id"], replay)
            return replay, True

    batch_id = uuid.uuid4().hex[:12]
    batch_dir = ROOT / batch_id
    input_dir = batch_dir / "input"
    output_dir = batch_dir / "output"
    total_bytes = 0
    items = []
    try:
        input_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir()
        for index, (upload, item_id, filename) in enumerate(zip(files, clean_ids, filenames)):
            suffix = Path(filename).suffix.lower()
            stored = input_dir / f"{index:04d}-{hashlib.sha256(item_id.encode()).hexdigest()[:12]}{suffix}"
            file_bytes = 0
            with stored.open("wb") as handle:
                while chunk := await upload.read(CHUNK_BYTES):
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > MAX_FILE_BYTES:
                        raise HTTPException(413, f"{filename} exceeds the per-file upload limit")
                    if total_bytes > MAX_BATCH_BYTES:
                        raise HTTPException(413, "batch exceeds the total upload limit")
                    handle.write(chunk)
            if file_bytes == 0:
                raise HTTPException(400, f"{filename} is empty")
            items.append({
                "index": index, "item_id": item_id, "filename": filename,
                "input_path": str(stored), "bytes": file_bytes, "state": "queued",
                "tries": 0, "studio": None, "studio_task_id": None,
                "duration_seconds": None, "media_duration_seconds": None,
                "artifact_path": None, "error": None, "metadata": None,
                # A local failure must not immediately reselect the same Mac.
                # The shared broker circuit and this per-item avoidance window
                # let another Voice Studio recover the work first.
                "avoid_machines": {}, "attempt_history": [],
            })
    except Exception:
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise
    finally:
        for upload in files:
            await upload.close()

    now = time.time()
    batch = {
        "id": batch_id, "idempotency_key": key, "created_at": now,
        # The modality must be persisted: the packaged admission guard re-checks it on every
        # dispatch attempt, and a batch without it is refused with MODEL_MODALITY_MISMATCH
        # forever (submit-time admission uses the request envelope, which does carry it).
        "modality": "transcription",
        "updated_at": now, "finished_at": None, "cancelled": False,
        "label": clean_label, "project": clean_project, "episode": clean_episode,
        "model": model, "language": language or None,
        "word_timestamps": bool(word_timestamps), "items": items,
        "routing": routing,
        "genstudio_execution": prepared.evidence,
    }
    batches[batch_id] = batch
    _save(batch)
    execution_identity.bind_local_batch(prepared.evidence, batch_id)
    _wakeup.set()
    return batch, False


def summary(batch: dict, include_items: bool = True,
            include_metadata: bool = False) -> dict:
    states = [item["state"] for item in batch["items"]]
    counts = {state: states.count(state) for state in ("queued", "running", "uncertain", "done", "error", "cancelled")}
    if counts["uncertain"]:
        status = "uncertain"
    elif counts["queued"] or counts["running"]:
        status = "running" if counts["running"] else "queued"
    elif counts["cancelled"] == len(states):
        status = "cancelled"
    elif counts["error"] and counts["done"]:
        status = "partial"
    elif counts["error"]:
        status = "error"
    elif counts["cancelled"]:
        status = "partial" if counts["done"] else "cancelled"
    else:
        status = "done"
    out = {
        "id": batch["id"], "status": status, "label": batch.get("label"),
        "project": batch.get("project"), "episode": batch.get("episode"),
        "model": batch["model"], "language": batch.get("language"),
        "word_timestamps": batch.get("word_timestamps", False),
        "created_at": batch["created_at"], "updated_at": batch.get("updated_at"),
        "finished_at": batch.get("finished_at"), "total": len(states), **counts,
        "duration_seconds": round(sum(i.get("duration_seconds") or 0 for i in batch["items"]), 2),
        "storage_cleaned": bool(batch.get("storage_cleaned_at")),
    }
    if batch.get("governor_note"):
        out["governor_note"] = batch["governor_note"]
    # Shown only when pinned: an operator looking at a queued job has to be able to see that a
    # machine pin is why nothing has picked it up.
    if str(batch.get("routing") or routing_rule.POOL) != routing_rule.POOL:
        out["routing"] = batch["routing"]
    execution = batch.get("genstudio_execution")
    if isinstance(execution, dict):
        out["genstudio_execution"] = {
            "genstudio_job_id": execution.get("genstudio_job_id"),
            "genstudio_attempt_id": execution.get("genstudio_attempt_id"),
            "fencing_token": execution.get("fencing_token"),
            "lease_expires_at": execution.get("lease_expires_at"),
        }
    if include_items:
        out["items"] = [{
            "index": item["index"], "item_id": item["item_id"],
            "filename": item["filename"], "state": item["state"],
            "studio": item.get("studio"), "studio_task_id": item.get("studio_task_id"),
            "duration_seconds": item.get("duration_seconds"),
            "media_duration_seconds": item.get("media_duration_seconds"),
            "artifact_url": (f"/api/hub/transcription/jobs/{batch['id']}/items/"
                             f"{item['index']}/artifact" if item["state"] == "done"
                             and not batch.get("storage_cleaned_at") else None),
            "error": item.get("error"), "tries": item["tries"],
            "manual_action_required": bool(item.get("manual_action_required")),
            **({"metadata": item.get("metadata")} if include_metadata else {}),
        } for item in batch["items"]]
    return out


def list_batches() -> list[dict]:
    persisted = {b["id"]: b for b in _load_rows()}
    persisted.update(batches)
    return [summary(b, include_items=True) for b in sorted(
        persisted.values(), key=lambda row: row["created_at"], reverse=True)]


def active_assignments() -> dict[str, dict]:
    """Current transcription lease details keyed by Voice Studio id."""
    active = {}
    for batch in batches.values():
        for item in batch["items"]:
            studio = item.get("studio")
            if item["state"] == "running" and studio:
                active[studio] = {
                    "kind": "transcription", "batch_id": batch["id"],
                    "project": batch.get("project"), "episode": batch.get("episode"),
                    "item_id": item["item_id"], "attempt": item["tries"],
                    "max_attempts": MAX_TRIES, "started_at": item.get("started_at"),
                }
    return active


def _execution_matches_model(entry: dict, execution: dict | None) -> bool:
    """Require the exact GenStudio transcription model contract when assigned."""
    if execution is None:
        return True
    try:
        expected_revision, expected_contract = (
            execution_identity.validate_transcription_identity(execution)
        )
    except execution_identity.ExecutionIdentityError:
        return False
    candidate = model_exposure.execution_candidate(entry, "audio.transcription")
    if candidate is None:
        return False
    if entry.get("genstudio_candidate_runtime_match") is not True:
        return False
    cache = entry.get("cache")
    if not isinstance(cache, dict):
        return False
    reported_revision = model_exposure.normalized_immutable_revision(
        candidate["runtime_revision"]
    )
    reported_contract = candidate["contract_hash"]
    if (reported_revision is None
            or reported_revision != expected_revision
            or reported_contract != expected_contract):
        return False
    snapshot_revision = cache.get("snapshot_revision")
    if (model_exposure.normalized_immutable_revision(snapshot_revision)
            != expected_revision):
        return False
    return True


async def _eligible_studios(monitor, model: str, item: dict | None = None,
                            execution: dict | None = None,
                            reasons: list[str] | None = None) -> list[dict]:
    """Studios that may take this model.

    ``reasons`` (optional) collects one short sentence per rejected studio. A transcription
    job that can be dispatched by nobody used to sit queued forever with no explanation; the
    collector is what lets ``dispatch_once`` tell the owner which gate is holding it.
    """
    eligible = []
    now = time.time()
    avoided = (item or {}).get("avoid_machines") or {}
    expected_modality = "transcription" if os.environ.get("STUDIOHUB_PACKAGED") == "1" else "voice"
    for studio in monitor.registry:
        sid = studio.get("id", "?")
        if studio.get("modality") != expected_modality:
            if reasons is not None:
                reasons.append(f"{sid}: modality {studio.get('modality')!r} != {expected_modality!r}")
            continue
        if sid in busy_studios:
            if reasons is not None:
                reasons.append(f"{sid}: already busy")
            continue
        if broker.in_maintenance(sid):
            if reasons is not None:
                reasons.append(f"{sid}: in maintenance")
            continue
        machine = studio.get("machine", "local")
        status = monitor.status.get(sid, {}).get("status")
        if status != "up":
            if reasons is not None:
                reasons.append(f"{sid}: health status is {status!r}")
            continue
        if not machine_enabled(machine):
            if reasons is not None:
                reasons.append(f"{sid}: machine {machine} is disabled")
            continue
        if not studio_enabled(machine, sid):
            if reasons is not None:
                reasons.append(f"{sid}: studio is disabled on {machine}")
            continue
        if machine in broker.busy_machines():
            if reasons is not None:
                reasons.append(f"{sid}: machine {machine} is busy with other work")
            continue
        if broker.machine_is_quarantined(machine):
            if reasons is not None:
                reasons.append(f"{sid}: machine {machine} is quarantined")
            continue
        if float(avoided.get(machine, 0) or 0) > now:
            if reasons is not None:
                reasons.append(f"{sid}: machine {machine} is being avoided after a failure")
            continue
        if not peers.dispatch_ready(studio):
            if reasons is not None:
                reasons.append(f"{sid}: worker authority is not ready")
            continue
        availability = await monitor.scheduling_transcription(studio)
        models = (availability or {}).get("models", [])
        if not (availability or {}).get("available"):
            if reasons is not None:
                reasons.append(f"{sid}: reports transcription unavailable")
            continue
        match = next((m for m in models if m.get("repo") == model), None)
        if match is None:
            if reasons is not None:
                reasons.append(f"{sid}: does not list model {model}")
            continue
        if execution is not None:
            if not _execution_matches_model(match, execution):
                if reasons is not None:
                    reasons.append(f"{sid}: {model} does not match the execution identity")
                continue
        elif not match.get("cached", True):
            if reasons is not None:
                reasons.append(f"{sid}: {model} is not cached/ready")
            continue
        eligible.append(studio)
    eligible.sort(key=lambda studio: (
        broker._studio_total_memory_gb(studio), studio["id"],
    ))
    return eligible


def has_queued_work() -> bool:
    """Whether transcription has work ready to compete for a shared Mac."""
    now = time.time()
    return any(
        not batch.get("cancelled")
        and not execution_identity.lease_expired(batch.get("genstudio_execution"))
        and any(
            item.get("state") == "queued"
            and (not item.get("retry_at") or item["retry_at"] <= now)
            for item in batch.get("items") or []
        )
        for batch in batches.values()
    )


async def has_dispatchable_work(monitor, machine: str) -> bool:
    """Whether this lane can actually use ``machine`` on its next turn."""
    now = time.time()
    for batch in batches.values():
        if (batch.get("cancelled")
                or execution_identity.lease_expired(batch.get("genstudio_execution"))
                or broker.admission_refusal(batch)):
            continue
        for item in batch.get("items") or []:
            if (item.get("state") != "queued"
                    or (item.get("retry_at") and item["retry_at"] > now)):
                continue
            eligible = await _eligible_studios(
                monitor, batch["model"], item, batch.get("genstudio_execution"),
            )
            for studio in eligible:
                if studio.get("machine", "local") != machine:
                    continue
                availability = await monitor.scheduling_transcription(studio)
                entry = next((row for row in (availability or {}).get("models", [])
                              if row.get("repo") == batch["model"]), None)
                if entry and broker.memory_admission_dispatchable(
                        studio, batch["model"], entry):
                    return True
    return False


def _expire_genstudio_batch(batch: dict) -> bool:
    if not execution_identity.lease_expired(batch.get("genstudio_execution")):
        return False
    batch["cancelled"] = True
    batch["lease_expired"] = True
    now = time.time()
    for item in batch.get("items") or []:
        if item.get("state") == "running" and _packaged():
            _mark_uncertain(item, "GenStudio execution lease expired while the worker may still be running")
        elif item.get("state") in {"queued", "running"}:
            item.update(
                state="cancelled",
                error="GenStudio execution lease expired",
                finished_at=now,
            )
    return True


def renew_execution_lease(renewal: dict) -> bool:
    batch_id = renewal.get("local_batch_id")
    batch = batches.get(batch_id) if batch_id else None
    if batch is None and batch_id:
        batch = get_batch(batch_id)
    if batch is None:
        batch = next(
            (
                candidate
                for candidate in batches.values()
                if (candidate.get("genstudio_execution") or {}).get(
                    "genstudio_attempt_id"
                )
                == renewal.get("genstudio_attempt_id")
            ),
            None,
        )
    if batch is None:
        return False
    evidence = dict(batch.get("genstudio_execution") or {})
    evidence["lease_expires_at"] = renewal["lease_expires_at"]
    batch["genstudio_execution"] = evidence
    batches[batch["id"]] = batch
    _save(batch)
    _wakeup.set()
    return True


async def dispatch_once(monitor) -> int:
    assigned = 0
    while True:
        made_progress = False
        active = sorted(
            (batch for batch in batches.values() if not batch.get("cancelled")),
            key=lambda batch: (batch.get("last_dispatched_at", 0), batch["created_at"]),
        )
        for batch in active:
            if _expire_genstudio_batch(batch):
                _save(batch)
                continue
            refusal = broker.admission_refusal(batch)
            if refusal:
                note = str(refusal.get("detail") or "model is not ready")
                if batch.get("governor_note") != note:
                    batch["governor_note"] = note
                    _save(batch)
                continue
            item = next((i for i in batch["items"] if i["state"] == "queued"), None)
            if not item:
                continue
            reasons: list[str] = []
            eligible = await _eligible_studios(
                monitor, batch["model"], item,
                batch.get("genstudio_execution"), reasons)
            routing = str(batch.get("routing") or routing_rule.POOL)
            if routing != routing_rule.POOL:
                eligible = [s for s in eligible if routing_rule.allows(s, routing)]
                if not eligible:
                    reasons.insert(0, f"routing {routing!r} excludes every Studio that could "
                                      "otherwise take this job")
            if not eligible:
                reason = "no transcription studio can take this job: " + (
                    "; ".join(reasons) if reasons else "no transcription studio is registered")
                if batch.get("governor_note") != reason:
                    batch["governor_note"] = reason
                    _save(batch)
                continue
            for studio in eligible:
                machine = studio.get("machine", "local")
                from . import chat_jobs
                if not broker.external_dispatch_allowed(
                        machine, "transcription",
                        other_lane_has_work=await chat_jobs.has_dispatchable_work(
                            monitor, machine)):
                    continue
                availability = await monitor.scheduling_transcription(studio)
                entry = next((row for row in (availability or {}).get("models", [])
                              if row.get("repo") == batch["model"]),
                             {"repo": batch["model"]})
                decision, note = await broker.prepare_machine_memory(
                    monitor._client, studio, batch["model"], entry)
                if decision != "run":
                    broker.note_external_memory_block(machine, "transcription")
                    blocked = f"memory admission on {machine} returned {decision}: {note or 'no detail'}"
                    if batch.get("governor_note") != blocked:
                        batch["governor_note"] = blocked
                        _save(batch)
                    continue
                generation = peers.dispatch_generation(studio)
                if not peers.dispatch_ready(studio, generation):
                    continue
                # Catalog, runtime or OS may have changed during worker and
                # memory probes; recheck before leasing this physical Mac.
                if broker.admission_refusal(batch):
                    continue
                owner = f"transcription:{batch['id']}:{item['index']}"
                if not broker.acquire_external_machine(machine, owner):
                    continue
                broker.note_external_dispatch(machine, "transcription")
                item.update(
                    state="running", studio=studio["id"], machine=machine, error=None,
                    studio_task_id=f"stt-{batch['id']}-{item['index']}",
                    # Stable across retries so the terminal transition updates the same
                    # activity row the running transition created.
                    activity_id=f"stt-{batch['id']}-{item['index']}",
                    started_at=time.time(), tries=item["tries"] + 1,
                )
                try:
                    ledger.record_activity_ownership(
                        machine=str(machine), studio=str(studio["id"]),
                        job_id=item["studio_task_id"], model=batch.get("model"),
                        operation="transcription",
                    )
                except Exception:
                    # Activity evidence is optional and must never block work
                    # after the fleet scheduler has acquired this Mac.
                    logging.getLogger("studiohub.transcription").exception(
                        "Could not record optional transcription activity ownership"
                    )
                batch["last_dispatched_at"] = time.time()
                busy_studios.add(studio["id"])
                _save(batch)
                task = asyncio.create_task(_run_item(
                    monitor, batch, item, studio, owner, generation,
                ))
                _item_tasks[(batch["id"], item["index"])] = task
                assigned += 1
                made_progress = True
                break
        if not made_progress:
            break
    return assigned


def _record_terminal_activity(item: dict, batch: dict, studio: dict) -> None:
    """Record the terminal transition so completed subtitles reach the Hub's statistics.

    A subtitle job produces no asset row, so ``ledger.stats`` counts it from
    ``activity_events``. Only the ``running`` transition was ever written, which meant
    every completed Subtitle job was missing from the per-machine and per-model
    aggregates even though the live activity view showed it running.

    Optional evidence: a failure here must never change the job's outcome.
    """
    state = item.get("state")
    if state not in TERMINAL_STATES:
        return
    try:
        ledger.record_activity_event(
            machine=str(studio.get("machine") or "local"),
            studio=str(studio.get("id") or "subtitle"),
            job_id=str(item.get("activity_id") or item.get("studio_task_id")
                       or f"{batch['id']}:{item['index']}"),
            state=state, model=batch.get("model"), operation="transcription",
            source="job", hub_owned=True,
            progress=1.0 if state == "done" else None,
            started_at=item.get("started_at"), finished_at=time.time(),
            runtime_s=item.get("duration_seconds"),
            error_code=(None if state == "done"
                        else (str(item.get("error") or "")[:120] or None)),
        )
    except Exception:
        logging.getLogger("studiohub.transcription").exception(
            "Could not record optional transcription completion activity")


async def _run_item(monitor, batch: dict, item: dict, studio: dict, owner: str,
                    generation=None) -> None:
    started = time.time()
    post_started = False
    try:
        if _expire_genstudio_batch(batch):
            return
        input_path = Path(item["input_path"])
        if not input_path.is_file():
            raise FileNotFoundError("uploaded audio is missing")
        data = {
            "model": batch["model"],
            "word_timestamps": str(bool(batch.get("word_timestamps"))).lower(),
            "activity_id": item["studio_task_id"],
        }
        if batch.get("language"):
            data["language"] = batch["language"]
        if generation is not None and not peers.dispatch_ready(studio, generation):
            item.update(state="queued", studio=None, studio_task_id=None,
                        tries=max(0, item["tries"] - 1),
                        error="worker authentication changed before dispatch")
            return
        url, headers = studio_request(studio, "/api/transcribe")
        with input_path.open("rb") as handle:
            post_started = True
            response = await monitor._client.post(
                url, data=data,
                files={"file": (item["filename"], handle, "application/octet-stream")},
                headers=headers, timeout=300.0,
            )
        if response.status_code >= 400:
            # A delivered worker refusal is a known outcome. Gateway timeouts
            # can arrive after inference was accepted, so keep those fenced.
            if response.status_code not in AMBIGUOUS_RESPONSE_STATUS:
                post_started = False
            if response.status_code == 401 and not peers.is_local_studio(studio):
                peers.invalidate(studio["machine"], rejected=True,
                                 modality=studio["modality"])
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = getattr(response, "text", "")
            error = RuntimeError(f"HTTP {response.status_code}: {detail or 'transcription failed'}")
            error.transient = response.status_code in TRANSIENT_STATUS
            raise error
        payload = response.json()
        if _expire_genstudio_batch(batch):
            return
        srt = str(payload.get("srt") or "")
        if not srt.strip():
            raise ValueError("Voice Studio returned an empty SRT artifact")
        artifact = ROOT / batch["id"] / "output" / f"{item['index']:04d}.srt"
        temporary = artifact.with_suffix(".srt.part")
        temporary.write_text(srt, encoding="utf-8")
        temporary.replace(artifact)
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise ValueError("SRT artifact verification failed")
        item.update(
            state="done", artifact_path=str(artifact), error=None,
            studio_task_id=payload.get("studio_task_id") or payload.get("task_id"),
            duration_seconds=round(float(payload.get("elapsed_seconds") or time.time() - started), 2),
            media_duration_seconds=payload.get("duration"),
            metadata={k: v for k, v in payload.items() if k != "srt"},
        )
        # Hub-owned SRTs join the same device index. Input uploads stay outside
        # this call and are never retention candidates.
        from .. import output_storage
        try:
            output_storage.register_finalized_output(
                machine=str(studio.get("machine") or "local"), studio="subtitle",
                job_id=str(item.get("studio_task_id") or f"{batch['id']}:{item['index']}"),
                managed_output_roots=[str(artifact.parent)], output_path=str(artifact),
                generated_at=time.time(), batch_id=batch["id"], item_index=item["index"],
                attempt=item.get("tries", 0), model=batch.get("model"),
            )
        except Exception as exc:
            item["output_storage_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        broker.mark_external_machine_success(studio)
    except asyncio.CancelledError:
        if _packaged():
            _mark_uncertain(item, "Hub lost contact with a possibly accepted Subtitle worker attempt")
        elif _shutting_down and not batch.get("cancelled"):
            item.update(state="queued", studio=None, studio_task_id=None,
                        error="interrupted by Hub shutdown", interrupted=True)
        else:
            item.update(state="cancelled", error="cancelled")
    except Exception as exc:
        if _packaged() and post_started:
            _mark_uncertain(item, f"Subtitle worker response is uncertain: {str(exc) or type(exc).__name__}")
            return
        transient = isinstance(exc, (httpx.HTTPError, OSError)) or getattr(exc, "transient", False)
        message = str(exc) or type(exc).__name__
        capacity = broker._is_capacity_failure(message)
        if capacity:
            await broker.release_idle_siblings(monitor._client, studio)
            item["tries"] = max(0, item["tries"] - 1)
        machine = studio.get("machine", "local")
        history = item.setdefault("attempt_history", [])
        history.append({
            "studio": studio.get("id"), "machine": machine,
            "error": message[:240], "at": round(time.time(), 3),
        })
        del history[:-8]
        if transient and not capacity:
            # Availability is a cache of a lightweight endpoint. A real
            # transcription connection failure is stronger evidence, so force
            # a fresh check and route the next attempt to a different machine.
            monitor._transcribe_cache.pop(studio["id"], None)
            broker.mark_external_machine_failure(studio, message)
            item.setdefault("avoid_machines", {})[machine] = time.time() + broker.FAILED_WORKER_AVOID_S
        if transient and item["tries"] < MAX_TRIES and not batch.get("cancelled"):
            item.update(state="queued", studio=None, studio_task_id=None,
                        error=f"try {item['tries']} failed on {machine}: {message}")
        else:
            attempted = ", ".join(
                f"{entry.get('machine')} ({entry.get('error')})"
                for entry in history
            )
            item.update(
                state="error",
                error=f"{message}. Workers attempted: {attempted}" if attempted else message,
            )
    finally:
        busy_studios.discard(studio["id"])
        broker.release_external_machine(studio.get("machine", "local"), owner)
        _item_tasks.pop((batch["id"], item["index"]), None)
        _record_terminal_activity(item, batch, studio)
        _save(batch)
        _wakeup.set()


async def _dispatch_loop(monitor) -> None:
    while True:
        try:
            assigned = await dispatch_once(monitor)
            _wakeup.clear()
            try:
                await asyncio.wait_for(_wakeup.wait(), timeout=0.1 if assigned else 2.0)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(2)


def restore_batches() -> int:
    restored = 0
    for batch in _load_rows("finished = 0"):
        if _expire_genstudio_batch(batch):
            batches[batch["id"]] = batch
            _save(batch)
            restored += 1
            continue
        for item in batch["items"]:
            if item["state"] == "running":
                if _packaged():
                    _mark_uncertain(item, "Hub restarted after a possibly accepted Subtitle worker attempt")
                else:
                    item.update(state="queued", studio=None, studio_task_id=None,
                                error="interrupted by Hub restart", interrupted=True)
            if item["state"] == "queued" and not Path(item["input_path"]).is_file():
                item.update(state="error", error="uploaded audio is missing")
        batches[batch["id"]] = batch
        _save(batch)
        restored += 1
    if restored:
        _wakeup.set()
    return restored


def start_dispatcher(monitor) -> None:
    global _dispatcher_task, _cleanup_task, _shutting_down
    _shutting_down = False
    loop = asyncio.get_running_loop()
    if _dispatcher_task is None or _dispatcher_task.done() or _dispatcher_task.get_loop() is not loop:
        _dispatcher_task = asyncio.create_task(_dispatch_loop(monitor))
    if not _packaged() and (
            _cleanup_task is None or _cleanup_task.done() or _cleanup_task.get_loop() is not loop):
        _cleanup_task = asyncio.create_task(_cleanup_loop())


async def stop() -> None:
    global _dispatcher_task, _cleanup_task, _shutting_down
    _shutting_down = True
    tasks = [t for t in (_dispatcher_task, _cleanup_task, *_item_tasks.values()) if t and not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _dispatcher_task = _cleanup_task = None


async def cancel_batch(batch_id: str) -> dict | None:
    batch = get_batch(batch_id)
    if not batch:
        return None
    batch["cancelled"] = True
    for item in batch["items"]:
        if item["state"] == "queued":
            item.update(state="cancelled", error="cancelled")
        elif item["state"] == "running":
            if _packaged():
                _mark_uncertain(item, "Cancellation lost contact with a possibly accepted Subtitle worker attempt")
            task = _item_tasks.get((batch_id, item["index"]))
            if task:
                task.cancel()
    batches[batch_id] = batch
    _save(batch)
    _wakeup.set()
    return batch


def retry_batch(batch_id: str) -> tuple[dict | None, int]:
    batch = get_batch(batch_id)
    if not batch:
        return None, 0
    retried = 0
    if batch.get("storage_cleaned_at"):
        return batch, 0
    for item in batch["items"]:
        if not item.get("manual_action_required") and (
                item["state"] == "error" or item.get("interrupted")):
            item.update(state="queued", error=None, studio=None, studio_task_id=None,
                        interrupted=False, tries=0)
            retried += 1
    if retried:
        batch["cancelled"] = False
        batch["finished_at"] = None
        batches[batch_id] = batch
        _save(batch)
        _wakeup.set()
    return batch, retried


def settings() -> dict:
    try:
        saved = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError, TypeError):
        saved = {}
    value = saved.get("retention_days", 30)
    value = value if value in RETENTION_CHOICES else 30
    try:
        policy_version = int(saved.get("policy_version", 1))
    except (TypeError, ValueError):
        policy_version = 1
    if policy_version < 2 and value == 3:
        value = 30
        set_retention(value)
    return {"retention_days": value}


def set_retention(days: int) -> dict:
    if days not in RETENTION_CHOICES:
        raise HTTPException(400, f"retention_days must be one of {sorted(RETENTION_CHOICES)}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps({"retention_days": days, "policy_version": 2}),
        encoding="utf-8",
    )
    return {"retention_days": days}


def cleanup(batch_id: str | None = None, expired_only: bool = True) -> dict:
    now = time.time()
    cutoff = now - settings()["retention_days"] * 86400
    candidates = [get_batch(batch_id)] if batch_id else _load_rows()
    cleaned = 0
    reclaimed = 0
    for batch in candidates:
        if not batch or batch.get("storage_cleaned_at"):
            continue
        if any(item["state"] not in TERMINAL_STATES for item in batch["items"]):
            continue
        if expired_only and (batch.get("finished_at") or batch["updated_at"]) > cutoff:
            continue
        batch_dir = ROOT / batch["id"]
        if batch_dir.exists():
            reclaimed += sum(p.stat().st_size for p in batch_dir.rglob("*") if p.is_file())
            shutil.rmtree(batch_dir)
        batch["storage_cleaned_at"] = now
        for item in batch["items"]:
            item["input_path"] = None
            item["artifact_path"] = None
        batches.pop(batch["id"], None)
        _save(batch)
        cleaned += 1
    return {"cleaned": cleaned, "reclaimed_bytes": reclaimed}


def _remove_terminal_batch(batch: dict) -> int:
    """Delete one terminal batch and its Hub-local input/output directory."""
    batch_dir = ROOT / batch["id"]
    reclaimed = 0
    if batch_dir.exists():
        try:
            reclaimed = sum(p.stat().st_size for p in batch_dir.rglob("*") if p.is_file())
        except OSError:
            reclaimed = 0
        shutil.rmtree(batch_dir, ignore_errors=True)
    batches.pop(batch["id"], None)
    for item in batch.get("items", []):
        _item_tasks.pop((batch["id"], item.get("index")), None)
    with _conn() as conn:
        conn.execute("DELETE FROM transcription_batches WHERE id = ?", (batch["id"],))
    return reclaimed


def remove_batch(batch_id: str) -> dict | None:
    """Permanently clear one completed/cancelled batch and its local files.

    Active work is intentionally not clearable: cancel it first, then clear
    it once every chapter has reached a terminal state.
    """
    batch = get_batch(batch_id)
    if not batch or any(item["state"] not in TERMINAL_STATES for item in batch["items"]):
        return None
    return {"removed": batch_id, "reclaimed_bytes": _remove_terminal_batch(batch)}


def clear_terminal() -> dict:
    """Permanently clear all terminal transcription history and local files."""
    cleared = reclaimed = 0
    for batch in _load_rows():
        if any(item["state"] not in TERMINAL_STATES for item in batch["items"]):
            continue
        reclaimed += _remove_terminal_batch(batch)
        cleared += 1
    return {"cleared": cleared, "reclaimed_bytes": reclaimed}


async def _cleanup_loop() -> None:
    while True:
        # Fleet policy controls both age and capacity cleanup. Importing here
        # avoids a module cycle during app startup.
        from . import job_storage
        # Both calls walk the whole transcription tree with rglob+stat, and
        # enforce_budget re-walks it once per candidate batch. On the event
        # loop that froze this single-worker process for the whole walk, so
        # `/health/live` timed out and the site read as flapping.
        if (await asyncio.to_thread(job_storage.status))["enabled"]:
            await asyncio.to_thread(job_storage.enforce_budget)
        await asyncio.sleep(3600)


def statistics() -> dict:
    rows = _load_rows()
    items = [item for batch in rows for item in batch["items"]]
    return {
        "batches": len(rows), "items": len(items),
        "done": sum(i["state"] == "done" for i in items),
        "error": sum(i["state"] == "error" for i in items),
        "duration_seconds": round(sum(i.get("duration_seconds") or 0 for i in items), 2),
    }


def reset_for_tests() -> None:
    global _dispatcher_task, _cleanup_task, _shutting_down
    for task in [t for t in (_dispatcher_task, _cleanup_task, *_item_tasks.values()) if t and not t.done()]:
        task.cancel()
    batches.clear()
    busy_studios.clear()
    _item_tasks.clear()
    _dispatcher_task = _cleanup_task = None
    _shutting_down = False
    broker._external_machine_leases.clear()
    broker._external_lane_turn.clear()
