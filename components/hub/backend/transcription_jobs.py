"""Persistent, fleet-wide Whisper transcription batches."""

import asyncio
import hashlib
import json
import os
import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException, UploadFile

from . import broker, execution_identity, ledger, model_exposure, model_ids, peers
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


def _save_quietly(batch: dict) -> bool:
    """Persist a batch, and never let a failing write abort a dispatch pass.

    The dispatch loop writes bookkeeping as it goes - which studio was tried and why it was
    refused, a lease that expired, a note for the operator. An unwrapped write there raised out of
    `dispatch_once`, so a full disk or an unreadable database did not merely lose the note: it
    stopped the pass before any other job could be dispatched, and the exception surfaced as a
    scheduler failure rather than as "this Mac cannot write". Returns whether the write landed.
    """
    try:
        _save(batch)
        return True
    except Exception:
        logging.getLogger("studiohub.transcription").exception(
            "Could not persist batch %s; continuing", batch.get("id"))
        return False


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


# How long a possibly-accepted transcription is left fenced before it is simply redone.
#
# "Uncertain" exists because a lost response might mean the worker is still transcribing, and two
# transcripts of the same audio would be wasteful. But nothing ever resolved it: retry skipped the
# item, remove and clear refused a non-terminal batch, no route exposed any of the three, and the
# machine that held it was excluded from every lane - image, voice and subtitle - for good. The owner
# asked for exactly this: "make it recover after a set time frame so it doesn't block the app.
# transcription isn't expensive. redo it if it fails."
# How long to wait after a dispatch could not be written down: long enough that the same pass
# does not immediately retry it against a Hub whose durable write is failing.
DISPATCH_PERSIST_BACKOFF_S = 30.0

UNCERTAIN_RECOVERY_SECONDS = 15 * 60


def recover_stale_uncertain(now: float | None = None) -> list[str]:
    """Re-queue transcriptions that have been fenced for longer than the recovery window.

    Returns the batch ids that changed. Called from the dispatch loop, so a Mac always recovers on
    its own; nothing has to be pressed and no route has to exist.
    """
    now = time.time() if now is None else now
    touched: list[str] = []
    for batch in batches.values():
        changed = False
        for item in batch.get("items") or []:
            if item.get("state") != "uncertain" or not item.get("manual_action_required"):
                continue
            since = (item.get("finished_at") or item.get("last_progress_at")
                     or batch.get("created_at") or 0)
            if now - float(since) < UNCERTAIN_RECOVERY_SECONDS:
                continue
            # Redo it. The previous attempt left no trustworthy output, so anything it wrote goes to
            # the Trash Folder (never a delete); a move that fails keeps the item pointed at it and
            # the next recovery pass tries again.
            from .. import output_storage
            for key in ("output_path", "partial_output_path"):
                if not item.get(key):
                    continue
                path = Path(item[key])
                if os.path.lexists(path) and not output_storage.retire(path, "transcription-uncertain"):
                    continue
                item[key] = None
            item.update(state="queued", manual_action_required=False, interrupted=False,
                        studio_job_id=None, error=None, retry_at=0,
                        recovered_from_uncertain=True)
            changed = True
        if changed:
            batch["governor_note"] = ("A transcription was uncertain; it is being transcribed "
                                      "again from the original recording.")
            _save(batch)
            touched.append(batch["id"])
    return touched


def _mark_uncertain(item: dict, reason: str) -> None:
    """Fence a possibly accepted HTTP inference until worker reconciliation exists."""
    item.update(state="uncertain", error=reason, interrupted=True,
                manual_action_required=True)


async def create_batch(files: list[UploadFile], item_ids: list[str], model: str,
                       language: str | None, word_timestamps: bool, label: str | None,
                       project: str | None, episode: str | None,
                       genstudio_execution_json: str | None = None,
                       routing: str = routing_rule.POOL,
                       deduplicate: bool = True, admission_guard=None,
                       origin: str | None = None) -> tuple[dict, bool]:
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
        # A half-written upload is retired, not deleted: the owner can still look at it in the
        # Trash Folder, and a move that fails leaves it for the governor.
        from .. import output_storage
        if batch_dir.exists():
            output_storage.retire(batch_dir, "transcription-upload")
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
        # Who asked, stamped from the credential by the route (absent before 0.6.212).
        "origin": origin,
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


TERMINAL_STATUSES = frozenset({"done", "error", "cancelled", "partial", "uncertain"})


def item_errors(batch: dict) -> list[dict]:
    """The failed items only, as ``{item_index, code, message}``.

    A healthy item contributes nothing, so an empty list is the honest answer.
    Missing detail stays null rather than being invented.
    """
    rows: list[dict] = []
    for item in batch.get("items") or []:
        if item.get("state") != "error":
            continue
        code = item.get("error_code")
        message = item.get("error")
        rows.append({
            "item_index": item.get("index"),
            "code": (str(code)[:128] if isinstance(code, str) and code else None),
            "message": (str(message)[:1000] if isinstance(message, str) else None),
        })
    return rows


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
        "origin": batch.get("origin"),
        "project": batch.get("project"), "episode": batch.get("episode"),
        "model": batch["model"],
        # model_id: the canonical manifest id, so /api/hub/models and
        # /api/hub/transcription/jobs can be joined on one string. The stored
        # model key is preserved unchanged.
        "model_id": model_ids.canonical(batch.get("model")),
        "errors": item_errors(batch),
        "language": batch.get("language"),
        "word_timestamps": batch.get("word_timestamps", False),
        "created_at": batch["created_at"], "updated_at": batch.get("updated_at"),
        "finished_at": batch.get("finished_at"), "total": len(states), **counts,
        "duration_seconds": round(sum(i.get("duration_seconds") or 0 for i in batch["items"]), 2),
        "storage_cleaned": bool(batch.get("storage_cleaned_at")),
    }
    # Only while it still matters. `governor_note` records why a batch could not be dispatched; it
    # is cleared when a dispatch succeeds, but a note written on an attempt that was retried and
    # later finished stays in the record for good. Every transcript in one owner's history carried
    # "model is not in the packaged catalog" from a retry, on jobs that had completed - a warning
    # about the past, sitting next to a finished result.
    if batch.get("governor_note") and status not in TERMINAL_STATUSES:
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
            "error": item.get("error"), "error_code": item.get("error_code"), "tries": item["tries"],
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
        # An uncertain transcription no longer holds its machine: it will be redone on its own
        # timer, and until then the Mac is free to take other work (the owner's call - transcription
        # is cheap enough to repeat). `broker.busy_machines()` covers leases and image fences.
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
        if peers.is_local_studio(studio) and (lite_wait := peers.lite_mode_wait(model)):
            if reasons is not None:
                reasons.append(f"{sid}: {lite_wait}")
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
    return await dispatchable_pool(monitor, machine) is not None


async def dispatchable_candidate(monitor, machine: str) -> tuple[float, str] | None:
    """Oldest queued transcription batch that can use this Mac now."""
    now = time.time()
    for batch in sorted(batches.values(),
                        key=lambda row: (row.get("created_at", 0), row.get("id", ""))):
        if (batch.get("cancelled")
                or execution_identity.lease_expired(batch.get("genstudio_execution"))
                or broker.admission_refusal(batch)):
            continue
        for item in batch.get("items") or []:
            if (item.get("state") != "queued"
                    or (item.get("retry_at") and item["retry_at"] > now)):
                continue
            eligible = await _eligible_studios(
                monitor, batch["model"], item, batch.get("genstudio_execution"))
            for studio in eligible:
                if (studio.get("machine", "local") != machine
                        or not routing_rule.allows(studio, _routing(batch))):
                    continue
                availability = await monitor.scheduling_transcription(studio)
                entry = next((row for row in (availability or {}).get("models", [])
                              if row.get("repo") == batch["model"]), None)
                if entry and broker.memory_admission_dispatchable(
                        studio, batch["model"], entry):
                    return float(batch.get("created_at") or 0), str(batch["id"])
    return None


async def dispatchable_pool(monitor, machine: str) -> float | None:
    """Smallest pool among this lane's batches that can start on ``machine`` right now, else None."""
    now = time.time()
    best = None
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
                    pool = broker._pool_size(
                        "transcription", batch["model"], _routing(batch))
                    best = pool if best is None else min(best, pool)
    return best


def _routing(batch: dict) -> str:
    return str(batch.get("routing") or routing_rule.POOL)


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


def _hand_back_queued(batch: dict) -> None:
    """Every Mac here that could run it is paused: give queued items back typed
    NO_AVAILABLE_MACHINE (the broker lanes' contract) instead of holding them for ever.
    Nothing ran, so no try is spent; running items finish."""
    now = time.time()
    handed = [item for item in batch["items"] if item["state"] == "queued"]
    for item in handed:
        item.update(state="error", error_code=broker.NO_AVAILABLE_MACHINE,
                    error=broker.NO_AVAILABLE_MACHINE_TEXT, finished_at=now)
    if handed:
        _save(batch)


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
    recover_stale_uncertain()
    assigned = 0
    while True:
        made_progress = False
        active = sorted(
            (batch for batch in batches.values() if not batch.get("cancelled")),
            key=lambda batch: (batch["created_at"], batch["id"]),
        )
        for batch in active:
            if _expire_genstudio_batch(batch):
                _save(batch)
                continue
            if broker.every_capable_mac_paused("transcription", batch["model"], _routing(batch)):
                _hand_back_queued(batch)
                continue
            refusal = broker.admission_refusal(batch)
            if refusal:
                note = str(refusal.get("detail") or "model is not ready")
                if batch.get("governor_note") != note:
                    batch["governor_note"] = note
                    _save_quietly(batch)  # bookkeeping: a failed write must not stop the pass
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
                    _save_quietly(batch)  # bookkeeping: a failed write must not stop the pass
                continue
            for studio in eligible:
                machine = studio.get("machine", "local")
                availability = await monitor.scheduling_transcription(studio)
                entry = next((row for row in (availability or {}).get("models", [])
                              if row.get("repo") == batch["model"]),
                             {"repo": batch["model"]})
                decision, note = await broker.prepare_machine_memory(
                    monitor._client, studio, batch["model"], entry)
                if decision != "run":
                    if decision != "skip":  # a Mac below the model's floor is not a turn to pass on
                        broker.note_external_memory_block(machine, "transcription")
                    blocked = f"memory admission on {machine} returned {decision}: {note or 'no detail'}"
                    if batch.get("governor_note") != blocked:
                        batch["governor_note"] = blocked
                        _save_quietly(batch)  # bookkeeping: a failed write must not stop the pass
                    continue
                generation = peers.dispatch_generation(studio)
                if not peers.dispatch_ready(studio, generation):
                    continue
                # Catalog, runtime or OS may have changed during worker and
                # memory probes; recheck before leasing this physical Mac.
                if broker.admission_refusal(batch):
                    continue
                if await broker.higher_priority_work_waiting(
                        machine, "transcription", batch["created_at"],
                        f"transcription:{batch['id']}"):
                    continue
                if (batch.get("cancelled") or item.get("state") != "queued"
                        or execution_identity.lease_expired(batch.get("genstudio_execution"))
                        or monitor.status.get(studio["id"], {}).get("status") != "up"
                        or not machine_enabled(machine)
                        or not studio_enabled(machine, studio["id"])
                        or broker.in_maintenance(studio["id"])
                        or broker.admission_refusal(batch)
                        or not peers.dispatch_ready(studio, generation)):
                    continue
                owner = f"transcription:{batch['id']}:{item['index']}"
                if not broker.acquire_external_machine(machine, owner):
                    continue
                peers.note_dispatch(studio)
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
                # `_save_quietly` returns whether the write landed: it swallows the exception by
                # design (a failed bookkeeping write must not abort the pass), which made the
                # `except` branch below unreachable when 0.6.19 wrapped this call in try/except.
                if not _save_quietly(batch):
                    # The durable write is what makes a dispatch real. Without it the item stays
                    # "running" in memory with no task to finish it and the Mac leased to a job
                    # nothing will ever complete: the item can never be retried (it is not queued),
                    # never be failed (no task is watching it), and the Mac reads as busy until the
                    # Hub restarts. Both are rolled back so the next pass can try again, and the
                    # retry count is put back because nothing was attempted.
                    broker.release_external_machine(machine, owner)
                    busy_studios.discard(studio["id"])
                    item.update(
                        state="queued", studio=None, machine=None, started_at=None,
                        studio_task_id=None,
                        error="the Hub could not write down this dispatch, so it is queued again",
                        error_code="DISPATCH_PERSIST_FAILED",
                        tries=max(0, item.get("tries", 1) - 1),
                    )
                    batch["governor_note"] = ("A job could not be recorded when it was handed to a "
                                              "Mac, so it is queued again.")
                    # Back off before it is considered again. Freeing the Mac makes the item eligible
                    # inside this same pass, and the retry would run the job against a Hub whose last
                    # durable write failed - which is how a rolled-back dispatch turned into a job
                    # that failed for an unrelated reason. The dispatcher already skips any item
                    # whose `retry_at` is in the future.
                    item["retry_at"] = time.time() + DISPATCH_PERSIST_BACKOFF_S
                    _save_quietly(batch)  # bookkeeping: a failed write must not stop the pass
                    continue
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
            code = None
            try:
                body = response.json()
                detail = body.get("detail")
                code = body.get("code")
            except Exception:
                detail = getattr(response, "text", "")
            error = RuntimeError(f"HTTP {response.status_code}: {detail or 'transcription failed'}")
            error.transient = response.status_code in TRANSIENT_STATUS
            error.status_code = response.status_code
            error.error_code = code
            raise error
        payload = response.json()
        if _expire_genstudio_batch(batch):
            return
        if batch.get("cancelled"):
            # The owner cancelled while the worker was transcribing; it has now answered, so the
            # outcome is known. Nothing is kept.
            item.update(state="cancelled", error="cancelled", cancel_requested=False,
                        finished_at=time.time())
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
        # A worker or Mac that went away mid-transcription (a dropped or timed-out connection, a
        # peer's gateway timeout) is re-run elsewhere at once instead of being fenced for
        # UNCERTAIN_RECOVERY_SECONDS: the work is free and a duplicate SRT is never read (owner
        # decision 2026-10-03). Anything else after the POST keeps the fence.
        went_away = (isinstance(exc, httpx.TransportError)
                     or getattr(exc, "status_code", None) in AMBIGUOUS_RESPONSE_STATUS)
        if _packaged() and post_started and not went_away:
            _mark_uncertain(item, f"Subtitle worker response is uncertain: {str(exc) or type(exc).__name__}")
            return
        transient = isinstance(exc, (httpx.HTTPError, OSError)) or getattr(exc, "transient", False)
        message = str(exc) or type(exc).__name__
        machine = studio.get("machine", "local")
        if broker._is_machine_reservation_refusal(exc) and not batch.get("cancelled"):
            # A node already running other work answered at its door: nothing was attempted and
            # the Mac is healthy. The broker has treated this as a wait since 0.6.111; Subtitle
            # still spent a try, marked the Mac failed and, after three busy nodes, handed the
            # Gateway "local is reserved for other work on this Mac" as a final error.
            item["tries"] = max(0, item["tries"] - 1)
            item.setdefault("avoid_machines", {})[machine] = (
                time.time() + broker.MACHINE_RESERVED_AVOID_S)
            item.update(state="queued", studio=None, studio_task_id=None,
                        error=f"waiting for a free machine: {message}")
            return
        capacity = broker._is_capacity_failure(message)
        if capacity:
            await broker.release_idle_siblings(monitor._client, studio)
            item["tries"] = max(0, item["tries"] - 1)
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
        # Transcription is deliberately not a preferred-generation choice
        # (`preferred_hold.GENERATION_CHOICES`), so a completed subtitle never opens a hold.


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
            if item["state"] == "running" and batch.get("cancelled") and _packaged():
                _mark_uncertain(item, "Hub restarted after a possibly accepted Subtitle worker attempt")
            elif item["state"] == "running" and int(item.get("tries") or 0) >= MAX_TRIES:
                item.update(state="error", error_code="HUB_RESTARTED", studio_task_id=None,
                            error=f"the Hub restarted while this was running, on its last of "
                                  f"{MAX_TRIES} tries")
            elif item["state"] == "running":
                # Free local work runs again rather than holding its Mac `uncertain` for a person
                # (owner, 2026-10-05; `broker._rerun_after_restart`). ponytail: the original is not
                # watched or adopted here - a node still transcribing it finishes unread; add the
                # broker's watch if that waste shows up.
                item.update(state="queued", studio=None, studio_task_id=None, machine=None,
                            error="interrupted by Hub restart; running it again", interrupted=True)
            if item["state"] == "queued" and not Path(item["input_path"]).is_file():
                item.update(state="error", error="uploaded audio is missing")
        batches[batch["id"]] = batch
        _save(batch)
        restored += 1
    if restored:
        _wakeup.set()
    return restored


def activity_batches() -> dict[str, dict]:
    """This queue's batches in the shape the live activity view consumes.

    Each Hub-owned queue publishes its own view rather than being restated by the callers that
    merge them; that restatement is how Chat was missed by both of them.
    """
    return {
        f"transcription:{batch_id}": {
            "model": batch.get("model"), "operation": "transcription",
            "items": batch.get("items") or [],
        }
        for batch_id, batch in list(batches.items())
    }


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
            task = _item_tasks.get((batch_id, item["index"]))
            if _packaged() and task is not None and not task.done():
                # **Let the request finish instead of abandoning it.** The Subtitle worker cannot
                # be interrupted mid-transcription, and dropping the connection made the outcome
                # genuinely unknown (and fenced the Mac for fifteen minutes). Waiting for the
                # answer it is already producing makes it known: the item is cancelled when the
                # worker returns, the result is discarded, and the Mac is released at the moment
                # it is actually free. `cancel_requested` is what a reader sees meanwhile.
                item["cancel_requested"] = True
                continue
            if _packaged():
                _mark_uncertain(item, "Cancellation lost contact with a possibly accepted Subtitle worker attempt")
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
            item.update(state="queued", error=None, error_code=None, studio=None,
                        studio_task_id=None, interrupted=False, tries=0)
            retried += 1
    if retried:
        batch["cancelled"] = False
        batch["finished_at"] = None
        batches[batch_id] = batch
        _save(batch)
        _wakeup.set()
    return batch, retried


def retry_failed_item(batch_id: str, item_index: int) -> dict:
    """Put one failed transcription back in the queue for the dispatcher.

    Only an ``error`` item can be retried.  A queued, running or done item is
    not a failed attempt, and an ``uncertain`` item may still have a worker
    producing a result, so its outcome has to be discarded before it can be
    retried.  The uploaded file and its identity are preserved; only the failed
    attempt's bookkeeping is cleared.
    """
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError("unknown transcription batch")
    if batch.get("cancelled"):
        raise ValueError("batch is cancelled")
    item = next((row for row in batch.get("items", [])
                 if row.get("index") == item_index), None)
    if item is None:
        raise ValueError("unknown transcription item")
    state = str(item.get("state") or "")
    if state == "uncertain":
        raise ValueError(
            "item is uncertain and must be discarded before it can be retried"
        )
    if state != "error":
        raise ValueError(
            f"item is not in a failed state (state is {state or 'unknown'})"
        )
    item.update(
        state="queued", tries=0, error=None, error_code=None, studio=None, studio_task_id=None,
        interrupted=False, manual_action_required=False, started_at=None,
        finished_at=None, duration_seconds=None, artifact_path=None,
    )
    batch["cancelled"] = False
    batch["finished_at"] = None
    batch["governor_note"] = None
    batches[batch_id] = batch
    _save(batch)
    _wakeup.set()
    return {
        "ok": True, "batch_id": batch_id, "item_index": item_index,
        "state": "queued",
    }


# The output policy of every Hub-side store: 7 days (owner rule 2026-10-19; 3 before that, 30 before that).
DEFAULT_RETENTION_DAYS = 7
POLICY_VERSION = 4
# An older file's value equal to that version's own default was never chosen, so it takes the new
# default. `(default, the policy_version that replaced it)`.
_LEGACY_DEFAULTS = ((30, 3), (3, 4))


def settings() -> dict:
    try:
        saved = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError, TypeError):
        saved = {}
    value = saved.get("retention_days", DEFAULT_RETENTION_DAYS)
    value = value if value in RETENTION_CHOICES else DEFAULT_RETENTION_DAYS
    try:
        policy_version = int(saved.get("policy_version", 1))
    except (TypeError, ValueError):
        policy_version = 1
    if saved:
        for old, replaced_in in _LEGACY_DEFAULTS:
            if policy_version < replaced_in and value == old:
                value = DEFAULT_RETENTION_DAYS
                set_retention(value)
                break
    return {"retention_days": value}


def set_retention(days: int) -> dict:
    if days not in RETENTION_CHOICES:
        raise HTTPException(400, f"retention_days must be one of {sorted(RETENTION_CHOICES)}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps({"retention_days": days, "policy_version": POLICY_VERSION}),
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
            # To the Hub's Trash Folder, never a delete; a move that fails keeps the batch for the next pass.
            from .. import output_storage
            size = sum(p.stat().st_size for p in batch_dir.rglob("*") if p.is_file())
            if not output_storage.retire(batch_dir, "transcription"):
                continue
            reclaimed += size
        batch["storage_cleaned_at"] = now
        for item in batch["items"]:
            item["input_path"] = None
            item["artifact_path"] = None
        batches.pop(batch["id"], None)
        _save(batch)
        cleaned += 1
    return {"cleaned": cleaned, "reclaimed_bytes": reclaimed}


def _remove_terminal_batch(batch: dict) -> int | None:
    """Clear one terminal batch: its Hub-local input/output directory goes to the Trash Folder (never
    a delete), then the record goes. None when the files could not be moved - the batch is kept, so
    the next clear can try again instead of leaving files with no record pointing at them."""
    batch_dir = ROOT / batch["id"]
    reclaimed = 0
    if batch_dir.exists():
        try:
            reclaimed = sum(p.stat().st_size for p in batch_dir.rglob("*") if p.is_file())
        except OSError:
            reclaimed = 0
        from .. import output_storage
        if not output_storage.retire(batch_dir, "transcription-batch"):
            return None
    batches.pop(batch["id"], None)
    for item in batch.get("items", []):
        _item_tasks.pop((batch["id"], item.get("index")), None)
    with _conn() as conn:
        conn.execute("DELETE FROM transcription_batches WHERE id = ?", (batch["id"],))
    return reclaimed


def remove_batch(batch_id: str) -> dict | None:
    """Clear one completed/cancelled batch and its local files.

    Active work is intentionally not clearable: cancel it first, then clear
    it once every chapter has reached a terminal state. The files are moved to the Trash Folder,
    never deleted; if that move fails nothing is cleared and the caller is told.
    """
    batch = get_batch(batch_id)
    if not batch or any(item["state"] not in TERMINAL_STATES for item in batch["items"]):
        return None
    reclaimed = _remove_terminal_batch(batch)
    if reclaimed is None:
        raise HTTPException(409, "The batch files could not be moved to the Trash Folder; nothing was cleared.")
    return {"removed": batch_id, "reclaimed_bytes": reclaimed}


def clear_terminal() -> dict:
    """Clear all terminal transcription history and local files (moved to the Trash Folder, never deleted)."""
    cleared = reclaimed = 0
    for batch in _load_rows():
        if any(item["state"] not in TERMINAL_STATES for item in batch["items"]):
            continue
        size = _remove_terminal_batch(batch)
        if size is None:
            continue                    # its files could not be moved: kept for the next clear
        reclaimed += size
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
