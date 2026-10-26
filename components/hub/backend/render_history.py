"""What this Hub has rendered: one row per final render, and the totals over them.

A **read-only view over rows that already exist**. `render_jobs.py` writes a row at
acceptance, at process start and at settlement into `hub.db`; this module opens that
database read-only and writes nothing. It executes nothing, dispatches nothing,
authorises nothing and retires nothing.

Two facts are derived, and each says where it came from:

* **The picture and its length** are the packet's own `recipe.canvas` and
  `target_duration_sec`. The executor proves the output matches them (canvas, fps, and a
  duration within `2 / working_fps`) before it publishes the receipt, so the packet's
  target is what the file is.
* **A finished render's wall time** is `updated_at - started_at`: the moment the Hub bound
  the render process and the row's last lifecycle write, which for a terminal row is its
  settlement. `started_at` is written from this build on; a render accepted before it has no
  measured wall time and reports `None`, never 0.

* **A render's channel and episode** are the labels Story Studio sent with the request (stored in
  `display_json` at acceptance). They are display only — no routing, dispatch, hashing or
  acceptance decision reads them — and a render that sent neither reports `display: None`, so the
  tab names it by job id rather than guessing.

A zero that means two things is a defect (AGENTS.md §6): nothing here returns 0 for "the
Hub does not know". A length, a wall time, an average and a speed are each either a measured
number or `None`, and `unknown_duration` counts the rows the first could not be given for.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

#: The window the Hub keeps job history for (`disk_governor.HISTORY_DAYS`, the ledger's own
#: `compact_history(days=90)`). The Render tab lists the same window so one screen cannot show a
#: window another screen does not.
RETENTION_DAYS = 90

TERMINAL = frozenset({"output_ready", "failed", "cancelled"})
SUCCEEDED = "output_ready"

#: The columns a row needs. `request_bytes` and `packet_json` are the big ones, and
#: `request_bytes` is never read here: a page of renders must not drag in a megabyte of
#: accepted request bodies per row.
_COLUMNS = (
    "id, state, machine, purpose, created_at, updated_at, started_at, display_json, "
    "packet_json, output_path, output_receipt_json, recovery_note, stopped_evidence_json, "
    "story_uuid, intent_id, attempt_no, gateway_attempt_key, dispatch_key, "
    "profile_digest, tools_digest, compiler_digest"
)

FAILURE_REASONS = {
    "prelaunch_failed": "The render could not start on that Mac.",
    "output_validation_failed": "ffmpeg finished, but the file it wrote did not match the "
                               "profile, so it was refused.",
}


def _json(value: Any) -> dict[str, Any]:
    """One stored JSON object, or an empty mapping. A row written by an older build is not an error."""
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stated_number(value: Any, *, positive: bool = True) -> float | None:
    """The number the row states, or None when it states none. Never a stand-in 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    return number


def outcome(state: str) -> str:
    """The four words the tab counts in: succeeded / failed / cancelled / running."""
    if state == SUCCEEDED:
        return "succeeded"
    if state in {"failed", "cancelled"}:
        return state
    # accepted, launch_pending, rendering, cancel_requested, stopped, uncertain: work the Hub
    # still holds, whatever it is doing with it.
    return "running"


def _failure(row: Any, state: str) -> str | None:
    """Why a render did not produce an accepted output, in the Hub's own vocabulary."""
    note = row["recovery_note"]
    if isinstance(note, str) and note in FAILURE_REASONS:
        return FAILURE_REASONS[note]
    if state == "cancelled":
        return "Cancelled before the output was accepted."
    if state != "failed":
        return None
    evidence = _json(row["stopped_evidence_json"])
    code = evidence.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return f"ffmpeg exited with code {code}."
    if evidence.get("reason") == "completed":
        return "ffmpeg finished, but the attempt was not published."
    return "The Hub recorded no reason."


def _display(row: Any) -> dict[str, str] | None:
    """The channel and episode Story Studio named this render with, or None.

    Display only, and never invented: a render accepted before Story sent one has no name here,
    and the tab keeps naming it by its job id. A value that is not a bounded label is dropped
    rather than shown.
    """
    stored = _json(row["display_json"])
    labels = {
        name: stored[name] for name in ("channel", "episode")
        if isinstance(stored.get(name), str) and stored[name]
    }
    return labels or None


def entry(row: Any, *, now: float) -> dict[str, Any]:
    """One render, as the tab shows it. Every field is either measured or None."""
    packet = _json(row["packet_json"])
    recipe = packet.get("recipe") if isinstance(packet.get("recipe"), dict) else {}
    canvas = recipe.get("canvas") if isinstance(recipe.get("canvas"), dict) else {}
    encode = recipe.get("encode") if isinstance(recipe.get("encode"), dict) else {}
    receipt = _json(row["output_receipt_json"])
    state = str(row["state"])
    finished = outcome(state) != "running"

    started = _stated_number(row["started_at"])
    settled = _stated_number(row["updated_at"]) if finished else None
    wall = (settled - started) if (started and settled and settled >= started) else None
    running_for = (now - started) if (started and not finished and now >= started) else None

    path = row["output_path"]
    local_file = (path if (state == SUCCEEDED and isinstance(path, str) and path
                           and Path(path).is_file()) else None)

    return {
        "id": row["id"],
        "state": state,
        "outcome": outcome(state),
        "purpose": row["purpose"] or "production",
        "machine": row["machine"],
        # Which channel and episode this render belongs to, as Story Studio named them. None
        # when it sent no name, so the tab falls back to the job id rather than a guess.
        "display": _display(row),
        "created_at": _stated_number(row["created_at"]),
        "started_at": started,
        "finished_at": settled,
        "wall_seconds": wall,
        "running_seconds": running_for,
        # The profile's own target, which the executor proved the output matched.
        "video_seconds": _stated_number(recipe.get("target_duration_sec")),
        "width": _stated_number(canvas.get("width")),
        "height": _stated_number(canvas.get("height")),
        "frame_rate": _stated_number(canvas.get("working_fps")),
        "container": encode.get("container"),
        "codec": encode.get("requested_codec"),
        "effort": encode.get("requested_effort"),
        "crf": _stated_number(encode.get("crf"), positive=False),
        "tier": receipt.get("selected_tier"),
        "output_bytes": _stated_number(receipt.get("output_size_bytes")),
        "output_sha256": receipt.get("output_sha256"),
        "profile_digest": row["profile_digest"],
        "tools_digest": row["tools_digest"],
        "compiler_digest": row["compiler_digest"],
        "story_uuid": row["story_uuid"],
        "intent_id": row["intent_id"],
        "attempt_no": row["attempt_no"],
        "gateway_attempt_key": row["gateway_attempt_key"],
        "dispatch_key": row["dispatch_key"],
        "failure": _failure(row, state),
        # Where the file is, when this Mac still holds it. Nil means the Hub no longer has it:
        # an accepted output is returned to Story Studio and retired here after its ACK.
        "local_file": local_file,
    }


def _derive(raw: dict[str, Any]) -> dict[str, Any]:
    """The published totals: the summed counts, and each rate only where it was measured."""
    measured = raw["measured_renders"]
    wall = raw["measured_wall_seconds"]
    output = raw["measured_output_seconds"]
    average = (wall / measured) if measured and wall else None
    speed = (output / wall) if wall and output else None
    return {
        "processed": raw["processed"],
        "succeeded": raw["succeeded"],
        "failed": raw["failed"],
        "cancelled": raw["cancelled"],
        "running": raw["running"],
        "output_seconds": raw["output_seconds"],
        "output_minutes": (raw["output_seconds"] / 60) if raw["output_seconds"] else None,
        "unknown_duration": raw["unknown_duration"],
        "measured_renders": measured,
        "measured_output_seconds": output,
        "measured_wall_seconds": wall,
        "average_wall_seconds": average,
        "speed": speed,
    }


def totals(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, output minutes, average render time and output-seconds-per-wall-second.

    Only a **succeeded** render has output: a failed or cancelled attempt produced no video, so
    its packet's length is not counted as minutes the Hub made. A succeeded render the Hub cannot
    give a length for is counted in `unknown_duration` instead — never added as zero.
    """
    raw = {"processed": len(entries), "succeeded": 0, "failed": 0, "cancelled": 0,
           "running": 0, "output_seconds": None, "unknown_duration": 0,
           "measured_renders": 0, "measured_output_seconds": None,
           "measured_wall_seconds": None}
    for item in entries:
        which = item["outcome"]
        raw[which] += 1
        if which != "succeeded":
            continue
        seconds = item["video_seconds"]
        if seconds is None:
            raw["unknown_duration"] += 1
            continue
        raw["output_seconds"] = (raw["output_seconds"] or 0.0) + seconds
        # A rate is only claimed over renders the Hub actually timed.
        wall = item["wall_seconds"]
        if wall is None:
            continue
        raw["measured_renders"] += 1
        raw["measured_wall_seconds"] = (raw["measured_wall_seconds"] or 0.0) + wall
        raw["measured_output_seconds"] = (raw["measured_output_seconds"] or 0.0) + seconds
    return _derive(raw)


def merge_totals(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """One row per Mac's totals added together, then the same rates derived from the sums."""
    raw = {"processed": 0, "succeeded": 0, "failed": 0, "cancelled": 0, "running": 0,
           "output_seconds": None, "unknown_duration": 0, "measured_renders": 0,
           "measured_output_seconds": None, "measured_wall_seconds": None}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for name in ("processed", "succeeded", "failed", "cancelled", "running",
                     "unknown_duration", "measured_renders"):
            raw[name] += int(part.get(name) or 0)
        for name in ("output_seconds", "measured_output_seconds", "measured_wall_seconds"):
            value = part.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                raw[name] = (raw[name] or 0.0) + float(value)
    return _derive(raw)


def read(database_path: str | Path, *, limit: int = 200,
         now: float | None = None) -> dict[str, Any]:
    """The newest `limit` renders in the retained window, plus totals over the whole window.

    A fresh install has no `render_jobs` table yet, and that is an empty history rather than a
    failure: the tab's job is to say "nothing yet", not to raise.
    """
    now = time.time() if now is None else now
    limit = max(1, min(int(limit), 1000))
    answer: dict[str, Any] = {"renders": [], "totals": totals([]),
                              "retention_days": RETENTION_DAYS}
    rows = _rows(database_path, now=now)
    every = [entry(row, now=now) for row in rows]
    answer["renders"] = every[:limit]
    answer["totals"] = totals(every)
    return answer


def _rows(database_path: str | Path, *, now: float) -> list[Any]:
    """Every row in the retained window, newest first. No columns a page does not show."""
    cutoff = now - RETENTION_DAYS * 86400
    path = Path(database_path)
    if not path.exists():
        return []
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            f"SELECT {_COLUMNS} FROM render_jobs WHERE created_at >= ? "
            "ORDER BY created_at DESC, id DESC", (cutoff,)).fetchall()
    except sqlite3.Error:
        # A Hub that has never rendered has no table yet, which is an empty history, not an error.
        return []
    finally:
        connection.close()


def detail(database_path: str | Path, job_id: str, *,
           now: float | None = None) -> dict[str, Any] | None:
    """One render by id, or None. `render_jobs.id` is the Hub's own job id."""
    if not isinstance(job_id, str) or not job_id:
        return None
    now = time.time() if now is None else now
    for row in _rows(database_path, now=now):
        if row["id"] == job_id:
            return entry(row, now=now)
    return None
