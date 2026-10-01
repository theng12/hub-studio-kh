"""Job broker + Swarm Batch — pull-based worker pools per modality.

An N-item batch is a work queue. Each UP studio of the right modality is a
worker slot (one concurrent generation each — heavy models on unified
memory). Free workers pull the next queued item, so faster machines naturally
do more and everyone finishes together; a failed item is requeued (max
MAX_TRIES). With one machine today the pool has one worker per modality —
the moment a second machine joins the registry, the same code fans out.

Memory governor: before
dispatching to a local or connected remote studio, the stricter of the
studio's catalog requirement and Hub production policy is checked against the
host's available memory; the item waits rather than OOMing the box. Render's
catalog hint bypasses the model-memory check because it runs local FFmpeg.

Params stay opaque: item params + sharedParams merge over {repo, prompt-field}
and are forwarded verbatim to the studio's own generate endpoint.
"""

import asyncio
import base64
import hashlib
import itertools
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

import httpx

from . import (artifact_metadata, cloud_guard, execution_assets,
               execution_identity, ledger, model_ids, peers, shared_voices)
from . import routing as routing_rule
from .memory_control import FleetMemoryControl, SUPPORTED_MODALITIES
from .peers import studio_request
from .monitor import is_cached
from .registry import DATA_DIR, base_url, label_for, machine_enabled, studio_enabled
from .resources import host_stats
from . import stall
from . import update_drain
from . import memory_admission

_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
             "image/webp": ".webp"}


def _ext(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), ".png")


def _mime_from_path(p: str) -> str:
    s = p.lower()
    if s.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if s.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _studio_target_for_url(url: str) -> tuple[str, dict[str, str]]:
    studio = next((s for s in _monitor().registry if url.startswith(base_url(s))), None)
    return studio_request(studio, url) if studio else (url, {})


def _multipart_fields(body: dict) -> dict:
    """Flatten the JSON param body into the studio's img2img/edit form fields
    (strings; CSV for lora lists). Unknown fields are ignored by the studio."""
    out = {}
    for k in ("repo", "prompt", "negative_prompt", "width", "height", "steps",
              "guidance", "seed", "image_strength", "quantize"):
        v = body.get(k)
        if v is not None:
            out[k] = str(v)
    for k in ("lora_names", "lora_scales"):
        v = body.get(k)
        if isinstance(v, (list, tuple)):
            out[k] = ",".join(str(x) for x in v)
        elif v is not None:
            out[k] = str(v)
    return out


def _video_multipart_fields(body: dict) -> dict:
    """Flatten an image-to-video request for Video Studio's multipart API."""
    out = {}
    for k in ("repo", "mode", "prompt", "negative_prompt", "width", "height",
              "frames", "fps", "steps", "guidance", "seed", "duration",
              "resolution", "aspect_ratio"):
        v = body.get(k)
        if v is not None:
            out[k] = str(v)
    return out


async def _resolve_reference(client: httpx.AsyncClient, ref: dict):
    """Turn a reference_images[] entry into (bytes, mime). Supports inline b64,
    a tailnet url, or an asset_id from the Hub ledger (incl. uploaded refs).
    Raises ValueError for permanent problems."""
    if ref.get("b64"):
        raw = ref["b64"].strip()
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]  # strip data URL prefix
        try:
            return base64.b64decode(raw), ref.get("mime", "image/png")
        except Exception as e:
            raise ValueError(f"invalid base64: {e}")
    if ref.get("url"):
        url, headers = _studio_target_for_url(ref["url"])
        r = await client.get(url, headers=headers, timeout=30.0)
        r.raise_for_status()
        return r.content, r.headers.get("content-type", "image/png")
    if ref.get("asset_id"):
        a = ledger.get_asset(ref["asset_id"])
        if not a:
            raise ValueError(f"asset {ref['asset_id']} not found")
        p = a.get("artifact_path")
        if p and Path(p).exists():
            return Path(p).read_bytes(), _mime_from_path(p)
        u = a.get("artifact_url")
        if u:
            url, headers = _studio_target_for_url(u)
            r = await client.get(url, headers=headers, timeout=30.0)
            r.raise_for_status()
            return r.content, r.headers.get("content-type", "image/png")
        raise ValueError("asset has no fetchable bytes")
    raise ValueError("reference needs one of b64 / url / asset_id")

# modality -> (generate endpoint, prompt field name, artifact suffix)
MODALITY = {
    "image": ("/api/generate/txt2img", "prompt", "image"),
    "music": ("/api/generate/txt2music", "prompt", "audio"),
    "voice": ("/api/generate/txt2speech", "text", "audio"),
    "video": ("/api/generate/txt2video", "prompt", "video"),
    # Render Studio accepts an immutable episode recipe and downloads every
    # referenced input before FFmpeg starts. It deliberately remains separate
    # from generative Video Studio.
    "render": ("/api/generate/render", "label", "video"),
}

# How a repeated attempt for one modality is treated, and which lanes share the per-machine
# fairness turn. This is a genuinely closed behavioural class, so a new modality must be
# classified *deliberately*: an unclassified modality used to fall through to the non-durable
# default, where a POST that may have reached the worker is replayed and can produce a duplicate
# artifact. `retry_class` refuses by name instead, and `_submit_batch_locked` calls it.
RETRY_CLASS_DURABLE_ASYNC = "durable_async"
RETRY_CLASS_RECEIPT_DURABLE = "receipt_durable"
RETRY_CLASS_NON_DURABLE = "non_durable"
RETRY_CLASS_AUXILIARY_LANE = "auxiliary_lane"

MODALITY_RETRY_CLASS: dict[str, str] = {
    # These workers acknowledge an asynchronous job before the result exists and do not provide
    # Voice's stable client-request recovery contract. Once a POST may have reached one of them,
    # retain that exact attempt instead of silently replaying it.
    "image": RETRY_CLASS_DURABLE_ASYNC,
    "music": RETRY_CLASS_DURABLE_ASYNC,
    # Voice recovers a dropped attempt by its stable client-request id.
    "voice": RETRY_CLASS_RECEIPT_DURABLE,
    # Video is a LONG job — 15 to 45 minutes by the LTX-2.3 runtime's own measured runs on an
    # M2 Pro 32 GB — so it is asynchronous by nature and cannot answer a terminal result for the
    # POST under any sane deadline. It was classified non-durable on reasoning that describes the
    # *legacy inherited* Video Studio (a synchronous POST returning a finished clip), which is not
    # the lane being built here. Under `NON_DURABLE` a node dying at minute 40 of a 45-minute job
    # loses the job — no `uncertain` state, no retained attempt, no re-dispatch — while the
    # identical failure in the Image lane is retained and can be requeued. **This is therefore a
    # requirement on the video worker, not merely a classification: it must acknowledge the job
    # before the result exists**, so the attempt is retained rather than silently replayed (a
    # 45-minute job may already have written a partial MP4, and a blind replay would produce a
    # duplicate artifact — the exact hazard this table exists to prevent).
    "video": RETRY_CLASS_DURABLE_ASYNC,
    # Render is the Hub's own local ffmpeg assembly step and genuinely does answer for the POST.
    "render": RETRY_CLASS_NON_DURABLE,
    # Chat and transcription never enter this queue: each owns its own job module with its own
    # lease (`chat_jobs`, `transcription_jobs`). They do share one per-machine fairness turn.
    "chat": RETRY_CLASS_AUXILIARY_LANE,
    "transcription": RETRY_CLASS_AUXILIARY_LANE,
}
# Derived from the class table, so the three sets cannot disagree with the classification.
DURABLE_ASYNC_MODALITIES = frozenset(
    modality for modality, kind in MODALITY_RETRY_CLASS.items()
    if kind == RETRY_CLASS_DURABLE_ASYNC)
RECEIPT_DURABLE_MODALITIES = frozenset(
    modality for modality, kind in MODALITY_RETRY_CLASS.items()
    if kind in {RETRY_CLASS_DURABLE_ASYNC, RETRY_CLASS_RECEIPT_DURABLE})


def retry_class(modality: object) -> str:
    """The declared retry/idempotency class for one modality, or a named refusal.

    Never a default: the class decides whether a possibly-delivered POST is replayed, and the
    quiet default is the risky one (a duplicate artifact on retry).
    """
    key = str(modality or "")
    try:
        return MODALITY_RETRY_CLASS[key]
    except KeyError as exc:
        raise ValueError(
            f"the modality {key!r} has no retry/idempotency class; add it to "
            f"MODALITY_RETRY_CLASS in components/hub/backend/broker.py "
            f"(classes: {sorted(set(MODALITY_RETRY_CLASS.values()))}) rather than letting it "
            f"take the non-durable default, where a retry can duplicate an artifact") from exc

# Queue order is scarcest-first (owner decision, 2026-09-30): a Mac that frees up takes the queued
# job whose *pool* - the number of distinct Macs that could run it at all - is smallest, so the few
# big Macs are not spent on work the many small ones can do. Ties stay on the model the Mac already
# holds, then first come first served. The old rule ranked audio ahead of image on every Mac; that
# is gone, because "audio" says nothing about how many Macs can run the model. See
# `_queued_batches`, `pool_machines` and memory_admission's requirement ladder. Nothing is preempted
# and nothing is reserved: a Mac with eligible queued work is never left idle.
# Episode renders stay ahead of everything (they are the Hub Mac's own assembly step, and
# `acquire_external_machine` already reserves a render Mac for them).
MAX_TRIES = 3
MAX_INFRA_TRIES = 8
INFRA_RETRY_WINDOW_S = 30 * 60
POLL_S = 2.0
RECOVERY_WINDOW_S = 120.0  # cover a full slow M1 generation after a dropped connection
RETRY_DELAYS_S = (3.0, 10.0)
INFRA_RETRY_DELAYS_S = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 300.0)
CAPACITY_RETRY_S = 30.0
CAPACITY_RETRY_MAX_S = 300.0
CAPACITY_RELEASE_ATTEMPTS = 3
# The owner's rule (2026-10-01): a Mac that has refused the same job for memory this many times does
# not keep it. The Hub hands it back with the typed error below so the Gateway can route it to
# another Mac (a pinned job fails with the same, clear, error). ~4 minutes with the back-off.
CAPACITY_HANDBACK_AFTER = 3
INSUFFICIENT_MEMORY = "INSUFFICIENT_MEMORY"
# A Mac another queue has reserved is not free *yet*. It is not a reason to park the item: the
# refusal takes that machine out of selection for this long - for every item aimed at it, through
# `_machine_reserved_until` - and the dispatcher's own heartbeat retries after that. It keeps its
# own constant rather than borrowing FAILED_WORKER_AVOID_S, because a reservation is a busy Mac,
# not a broken one, and the moment it is free it must take work again.
MACHINE_RESERVED_AVOID_S = 5.0
# A Mac whose disk is full refuses work with 507 / OUTPUT_STORAGE_BLOCKED. Its governor frees space
# on a 5-minute timer, so the Mac is left out of selection for that long - and the refusal is not a
# generation attempt, so it never uses up one of the item's MAX_TRIES.
DISK_FULL_AVOID_S = 300.0
HANDOFF_RETRY_S = 15.0
FAILED_WORKER_AVOID_S = 90.0
MACHINE_FAILURE_THRESHOLD = 2
MACHINE_COOLDOWN_S = 120.0
MAX_BATCH_ITEMS = 1000
MAX_BATCH_JSON_BYTES = 25 * 1024 * 1024
# Never begin a new local inference workload while macOS is already critically
# short of usable memory. Model-specific cold-load floors can raise this value
# through workload_policy.required_free_memory_gb(). Catalog ``size_gb`` is
# download/disk size and must never be used as a RAM estimate.
DEFAULT_MIN_FREE_MEMORY_GB = memory_admission.DEFAULT_MIN_FREE_MEMORY_GB

batches: dict[str, dict] = {}
_busy: set[str] = set()  # studio ids currently running an item for us
_maintenance: set[str] = set()  # drained by fleet maintenance/update operations
_external_machine_leases: dict[str, str] = {}
# The two auxiliary local queues share the same physical-machine lease. Keep a
# small per-machine turn token so a chat or transcription wake loop cannot win
# every newly-free lease while the other lane is waiting. This is advisory
# process-local fairness only; the durable non-preemptive lease remains the
# admission authority and a lone lane always stays work-conserving.
# Derived from the retry-class table above, where those lanes are a deliberate class.
_EXTERNAL_LANES = frozenset(
    modality for modality, kind in MODALITY_RETRY_CLASS.items()
    if kind == RETRY_CLASS_AUXILIARY_LANE)
_external_lane_turn: dict[str, str] = {}
# machine -> monotonic-free wall time until which a peer's MACHINE_BUSY refusal keeps this Mac out
# of selection. One refusal covers every item aimed at the machine, not just the one that met it.
_machine_reserved_until: dict[str, float] = {}
# studio id -> the model it last took work for: the model it is most likely still holding, which the
# scheduler prefers among equal-scarcity work so a Mac does not reload for nothing.
_last_model: dict[str, str] = {}
# machine -> recent transport failures and an optional circuit-breaker cooldown.
# This is intentionally process-local: after a Hub restart every worker must
# answer health again before it becomes eligible, which is a clean circuit reset.
_machine_protection: dict[str, dict] = {}
_handoff_state: dict[str, dict] = {}
_wakeup = asyncio.Event()
# Sum of live-free-memory admission floors reserved by in-flight LOCAL
# dispatches. The physical-machine lease normally prevents overlap; this also
# protects the short interval between an asynchronous telemetry check and the
# lease becoming visible to another local scheduler lane.
_reserved = {"gb": 0.0}
_submit_lock = threading.Lock()
_CLIENT_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,160}")
_GENSTUDIO_VOICE_EVIDENCE_VERSION = (1, 20, 13)
_IMMUTABLE_MODEL_REVISION = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{40,64}$")
_MODEL_REVISION_FIELDS = (
    "runtime_revision", "model_revision", "snapshot_revision", "commit_sha", "revision",
)


def _required_free_memory(mem: dict) -> float:
    """Return the live available-memory floor for a workload.

    ``min_free`` is an explicit runtime admission requirement. ``size`` is
    intentionally ignored because Studio catalogs use it for download/disk
    planning, not runtime unified-memory planning.
    """
    try:
        explicit = float(mem.get("min_free"))
    except (TypeError, ValueError):
        return DEFAULT_MIN_FREE_MEMORY_GB
    return max(0.0, explicit)


def _memory_gate(mem: dict, host: dict, reserved_gb: float = 0.0) -> tuple[str, str | None]:
    """Memory-governor decision for one physical Mac. Returns one of:
      ("skip", note) — this machine can't run the model at all → try another
                       studio (a bigger remote may qualify); never errors the batch
      ("wait", note) — could run, but not enough free RAM right now → defer
      ("run",  None) — clear to dispatch
    Peer Hubs provide the same host-memory snapshot for remote Macs, allowing
    the primary Hub to avoid a request that their own engine would reject."""
    min_total = mem.get("min_total")
    if min_total and host["total_gb"] < min_total:
        return ("skip", f"needs a ~{min_total}GB machine; this one has "
                        f"{host['total_gb']}GB — trying other machines")
    need_free = _required_free_memory(mem)
    effective_free = host["available_gb"] - reserved_gb
    if effective_free < need_free:
        return ("wait", f"waiting for memory: needs ~{need_free:.1f}GB, "
                        f"~{max(0.0, effective_free):.1f}GB free")
    return ("run", None)


def _local_gate(mem: dict, host: dict) -> tuple[str, str | None]:
    """Compatibility wrapper for the local unified-memory reservation."""
    return _memory_gate(mem, host, _reserved["gb"])


def _admission_requirements(model: str, entry: dict) -> dict | None:
    """Resolve the editable site policy used immediately before dispatch."""
    if entry.get("is_cloud"):
        return None
    policy = memory_admission.describe(model, entry)
    return {
        "min_total": policy["effective_min_total_memory_gb"],
        "min_free": policy["effective_min_free_memory_gb"],
    }


def _host_for_studio(studio: dict) -> dict | None:
    machine = studio.get("machine", "local")
    if peers.is_local_studio(studio):
        return host_stats()
    peer = peers.cached(machine) or {}
    host = peer.get("host")
    return host if isinstance(host, dict) else None


async def release_idle_siblings(client: httpx.AsyncClient, target: dict) -> dict:
    """Ask idle sibling Studios on the target Mac to release resident state.

    Worker endpoints own the final safety check: queued or active direct work
    returns 409 and is never preempted. A short machine cooldown prevents three
    independent scheduler lanes from repeatedly clearing the same Mac.
    """
    from . import chat_jobs, transcription_jobs

    machine = target.get("machine", "local")
    now = time.monotonic()
    previous = _handoff_state.get(machine) or {}
    if now - float(previous.get("at") or 0.0) < HANDOFF_RETRY_S:
        return {**previous["result"], "deferred": True}
    _handoff_state[machine] = {
        "at": now,
        "result": {"attempted": 0, "released": 0,
                   "busy": ["handoff_in_progress"], "failed": []},
    }
    known_busy = set(_busy) | set(chat_jobs.busy_studios) | set(
        transcription_jobs.busy_studios)
    siblings = [
        studio for studio in _monitor().registry
        if studio.get("machine", "local") == machine
        and studio.get("id") != target.get("id")
        and studio.get("modality") in SUPPORTED_MODALITIES
    ]

    releasable = [studio["id"] for studio in siblings if studio["id"] not in known_busy]
    operation = (await FleetMemoryControl(_monitor(), client).release(releasable)
                 if releasable else {"results": []})
    results = operation["results"]
    # One definition of "still holding a model", shared with the app-facing release so a
    # single Studio response cannot read as a success to the app and a failure to the
    # scheduler. `_change_one` already marks such a row `result: "still_loaded"`.
    still_loaded = {
        row["id"] for row in results
        if row.get("ok") and FleetMemoryControl._still_resident(row.get("policy"))
    }
    result = {
        "attempted": len(siblings),
        "released": sum(row.get("ok") and row["id"] not in still_loaded
                        for row in results),
        "busy": [studio["id"] for studio in siblings if studio["id"] in known_busy]
                + [row["id"] for row in results if row.get("result") == "busy"],
        "failed": [row["id"] for row in results
                   if (not row.get("ok") and row.get("result") != "busy")
                   or row["id"] in still_loaded],
        "deferred": False,
    }
    _handoff_state[machine] = {"at": now, "result": result}
    logging.getLogger("studiohub.broker").info(
        "memory handoff machine=%s target=%s released=%d busy=%s failed=%s",
        machine, target.get("id"), result["released"], result["busy"], result["failed"])
    return result


async def prepare_machine_memory(client: httpx.AsyncClient, studio: dict,
                                 model: str, entry: dict) -> tuple[str, str | None]:
    """Run admission, coordinated idle handoff, then one fresh admission check."""
    mem = _admission_requirements(model, entry)
    if mem is None:
        return "run", None
    is_local = peers.is_local_studio(studio)
    host = _host_for_studio(studio)
    if not host:
        return "run", None  # the worker's own MemoryGuard remains authoritative
    decision, note = _memory_gate(mem, host, _reserved["gb"] if is_local else 0.0)
    if decision != "wait":
        return decision, note

    handoff = await release_idle_siblings(client, studio)
    if handoff["busy"]:
        return "wait", "waiting for active sibling work on this machine"
    if not handoff["released"]:
        return decision, note
    if not is_local:
        peers.invalidate(studio.get("machine", "local"))
        await peers.refresh(_monitor().registry, client)
    refreshed = _host_for_studio(studio)
    if not refreshed:
        return "wait", "idle sibling memory released; waiting for fresh RAM telemetry"
    decision, note = _memory_gate(
        mem, refreshed, _reserved["gb"] if is_local else 0.0)
    if decision == "wait":
        note = f"released {handoff['released']} idle sibling(s); {note}"
    return decision, note


def memory_admission_dispatchable(studio: dict, model: str, entry: dict) -> bool:
    """Return whether current telemetry can admit this model without a wait.

    This is a cache/telemetry-only probe for the shared Chat/transcription
    turn arbiter. It deliberately does not release siblings or contact peers;
    the owning scheduler still performs ``prepare_machine_memory`` before its
    durable lease. Unknown telemetry remains dispatchable so the worker's own
    MemoryGuard remains authoritative, with the memory-failure turn fallback
    preventing the opposite lane from being stranded.
    """
    mem = _admission_requirements(model, entry)
    if mem is None:
        return True
    host = _host_for_studio(studio)
    if not isinstance(host, dict):
        return True
    try:
        total = float(host.get("total_gb"))
        available = float(host.get("available_gb"))
    except (TypeError, ValueError):
        return True
    if total <= 0 or available < 0:
        return True
    normalized_host = {"total_gb": total, "available_gb": available}
    reserved = _reserved["gb"] if peers.is_local_studio(studio) else 0.0
    decision, _note = _memory_gate(mem, normalized_host, reserved)
    return decision == "run"


def _studio_total_memory_gb(studio: dict) -> float:
    """Sortable physical-memory capacity; unknown evidence stays eligible."""
    host = _host_for_studio(studio)
    try:
        total = float((host or {}).get("total_gb"))
    except (TypeError, ValueError):
        return float("inf")
    return total if total > 0 else float("inf")


def _known_memory_gb(studio: dict) -> float | None:
    """Total memory when it is known, else None, so an unmeasured machine can sort last.

    `_studio_total_memory_gb` answers infinity for unknown telemetry, which is right for a
    floor but wrong for a ranking: it would make an unmeasured Mac the *best* audio candidate.
    """
    memory = _studio_total_memory_gb(studio)
    return None if memory == float("inf") else memory


def _published_memory_tier_gb(model: str, modality: str) -> float | None:
    """The strictest total-memory tier this model's workers publish, from cache only.

    Never an admission decision - dispatch still reads the exact per-worker catalog and the live
    RAM governor - this exists so placement can order machines by *this model's* requirement rather
    than by its modality's name.
    """
    if not model:
        return None
    reader = getattr(_monitor(), "scheduling_catalog_entries", None)
    if not callable(reader):
        # A placement preference must never be the reason a dispatch fails.
        return None
    tiers: list[float] = []
    for match in reader(model, modality=modality):
        try:
            tier = memory_admission.describe(model, match["entry"]).get(
                "effective_min_total_memory_gb")
        except (TypeError, ValueError):
            # A malformed published floor is the governor's problem to refuse; placement must not
            # fail a dispatch over it.
            continue
        if isinstance(tier, (int, float)) and not isinstance(tier, bool) and tier > 0:
            tiers.append(float(tier))
    return max(tiers) if tiers else None


def _placement_key(modality: str, model: str | None = None):
    """The machine-ordering key for one queued workload: **smallest machine that can run this
    model first**, in every lane there is.

    The owner restated the rule on 2026-09-25 after finding the old one spending the machines that
    only big jobs can use:

        "the qwen3 tts 0.6 can run on 8gb machine so it must take up the small ones first. but
        qwen3 tts 1.7b needs 16+ so it will wait for freed up 16+ machines... this should be
        generator specific at all. instead this priority case should be model - ram requirement
        specific."

    He is right, and the data was already published. The key used to branch on the *artifact class*
    a modality produces, so every audio job sorted high-memory first whatever model it named: the
    0.6B Qwen3-TTS, whose floor is 8 GB, took a 32 GB Mac that a Z-Image-class job needed, while the
    1.7B (12 GB), VoxCPM2 (16 GB) and Fish Audio (24-32 GB) - the ones that genuinely need a big Mac
    - queued behind it. Ordering by modality cannot see that. Ordering by the model can.

    Three bands, and the model's own published floor decides the first two:

    * a machine below this model's floor sorts **after every machine that can run it** - it is not
      a candidate, and putting it first would spend a dispatch discovering that;
    * among machines that can run it, **the smallest comes first**, so the 16 GB+ Macs stay free
      for work that has nowhere else to go;
    * a machine with no telemetry sorts last: an unknown is not evidence of a small Mac.

    A preference, never a filter: every studio stays in the list, because the live RAM governor -
    not this ordering - is what refuses a machine that cannot take the job right now.
    """
    floor = _published_memory_tier_gb(model, modality) if model else None

    def key(studio: dict) -> tuple[int, float, str]:
        memory = _known_memory_gb(studio)
        if memory is None:
            return (2, 0.0, studio["id"])
        if floor is not None and memory < floor:
            return (1, memory, studio["id"])
        return (0, memory, studio["id"])

    return key


def _pool_members(modality: str, model: str, routing: str = "pool") -> dict[str, tuple]:
    """machine -> (studio, catalog row) for every distinct Mac that could run this model at all.

    A job's *pool*: capacity, not availability. A Mac counts while it is up, enabled, not in
    maintenance or cooling down, its worker is authoritative, the model is cached and not paused
    there, the routing rule allows it (a pin is a pool of one) and its RAM is not known to be below
    the model's requirement (`memory_admission.requirement_gb`; unknown RAM counts). Busy Macs
    count - they will free up. Reads cached catalogs only, and the last-good row rather than the
    fresh-only one, so a catalog that is briefly stale does not shrink the pool to nothing.
    """
    mon = _monitor()
    if modality == "transcription":
        cache = getattr(mon, "_transcribe_cache", {})
        rows = [(studio, row) for studio in mon.registry if studio.get("modality") == modality
                for row in ((cache.get(studio["id"]) or (0, {}))[1] or {}).get("models", [])
                if isinstance(row, dict) and row.get("repo") == model]
    else:
        rows = [(m["studio"], m["entry"])
                for m in mon.cached_catalog_entries(model, modality=modality)]
    members: dict[str, tuple] = {}
    for studio, entry in rows:
        machine = studio.get("machine", "local")
        if (machine in members
                or not routing_rule.allows(studio, routing)
                or (routing == routing_rule.REMOTE and peers.is_local_studio(studio))
                or studio["id"] in _maintenance
                or not machine_enabled(machine) or not studio_enabled(machine, studio["id"])
                or _machine_blocked(machine)
                or mon.status.get(studio["id"], {}).get("status") != "up"
                or not peers.dispatch_ready(studio)
                or not entry.get("cached", is_cached(entry))
                or _model_block_note(studio, model, entry)):
            continue
        known = _known_memory_gb(studio)
        needed = memory_admission.requirement_gb(model, entry)[0] or 0.0
        if known is not None and known < needed:
            continue
        members[machine] = (studio, entry)
    return members


def pool_machines(modality: str, model: str, routing: str = "pool") -> set[str]:
    """The Macs that could run this model at all - see `_pool_members`."""
    return set(_pool_members(modality, model, routing))


def _pool_size(modality: str, model: str, routing: str = "pool") -> float:
    """Pool size for ordering: a job nobody can run sorts last (infinity) and holds nothing up."""
    return len(_pool_members(modality, model, routing)) or math.inf


def _broker_dispatchable_pool(machine: str) -> float | None:
    """Smallest pool among this queue's batches that could start on ``machine`` right now.

    The same tests the dispatch loop applies - idle studio, lease, avoid window, synced voice,
    catalog, live memory - so another lane never yields the Mac to work that would not go.
    """
    now = time.time()
    best: float | None = None
    for b in list(batches.values()):
        if b.get("cancelled") or b["modality"] == "render":
            continue
        queued = [i for i in b["items"]
                  if i["state"] == "queued" and (i.get("retry_at") or 0) <= now]
        member = _pool_members(b["modality"], b.get("model") or "", b["routing"]).get(machine) \
            if queued else None
        if member is None or admission_refusal(b):
            continue
        studio, entry = member
        if (studio not in _eligible_studios(b["modality"], b["routing"], b.get("model"))
                or not _supports_genstudio_voice_evidence(b, studio)
                or not memory_admission_dispatchable(studio, b["model"], entry)
                or not any(_shared_voice_allows_studio(b, i, studio)
                           and _item_allows_studio(i, studio, now) for i in queued)):
            continue
        best = min(best or math.inf, _pool_size(b["modality"], b["model"], b["routing"]))
    return best


async def scarcer_work_waiting(machine: str, pool: float, own_lane: str) -> bool:
    """Whether another lane has strictly scarcer work that can take ``machine`` right now.

    Cross-lane scarcest-first: a Mac that image, voice or music work wants is handed to the LLM
    (or the reverse) when the other lane's job has fewer Macs to run on. Only work that can start
    this instant defers anyone, so this can never hold a Mac idle - no reservation, just an order.
    Each lane calls it for itself immediately before leasing; ``own_lane`` is skipped because a lane
    already orders its own queue.
    """
    from . import chat_jobs, transcription_jobs
    found = []
    # `has_queued_work` first: an idle lane costs nothing, which is nearly always.
    if own_lane != "broker" and any(
            item["state"] == "queued" for b in batches.values() for item in b["items"]):
        found.append(_broker_dispatchable_pool(machine))
    if own_lane != "chat" and chat_jobs.has_queued_work():
        found.append(await chat_jobs.dispatchable_pool(_monitor(), machine))
    if own_lane != "transcription" and transcription_jobs.has_queued_work():
        found.append(await transcription_jobs.dispatchable_pool(_monitor(), machine))
    scarcer = any(value is not None and value < pool for value in found)
    if scarcer:  # wake the lanes that can use it - never the caller's own, which would spin
        for lane, wake in (("broker", _wakeup), ("chat", chat_jobs._wakeup),
                           ("transcription", transcription_jobs._wakeup)):
            if lane != own_lane:
                wake.set()
    return scarcer


def _protection(machine: str) -> dict:
    return _machine_protection.setdefault(machine, {
        "failures": 0, "cooldown_until": None, "reason": None,
        "last_failure_at": None, "last_success_at": None,
    })


def _machine_blocked(machine: str, now: float | None = None) -> bool:
    until = (_machine_protection.get(machine) or {}).get("cooldown_until") or 0
    return until > (time.time() if now is None else now)


def machine_protection_snapshot() -> dict[str, dict]:
    """Public, secret-free circuit state for Resources and operator diagnostics."""
    now = time.time()
    out = {}
    for machine, state in _machine_protection.items():
        until = state.get("cooldown_until") or 0
        out[machine] = {
            **state,
            "quarantined": until > now,
            "retry_in_s": round(max(0.0, until - now), 1),
        }
    return out


def machine_is_quarantined(machine: str) -> bool:
    """Whether a machine is temporarily excluded after transport failures."""
    return _machine_blocked(machine)


def mark_external_machine_failure(studio: dict, message: str) -> None:
    """Record a failed non-broker worker request against the shared circuit."""
    _mark_machine_failure(studio, message)


def mark_external_machine_success(studio: dict) -> None:
    """Clear the shared circuit after a successful non-broker worker request."""
    _mark_machine_success(studio)


def _mark_machine_failure(studio: dict, message: str) -> None:
    machine = studio.get("machine", "local")
    state = _protection(machine)
    was_blocked = _machine_blocked(machine)
    state["failures"] = int(state.get("failures") or 0) + 1
    state["last_failure_at"] = time.time()
    state["reason"] = message[:240]
    if state["failures"] < MACHINE_FAILURE_THRESHOLD:
        return
    state["cooldown_until"] = time.time() + MACHINE_COOLDOWN_S
    if not was_blocked:
        from . import alerts
        alerts.emit(
            "machine_quarantined",
            f"{machine} paused for {round(MACHINE_COOLDOWN_S)}s after repeated connection failures",
            {"machine": machine, "failures": state["failures"], "reason": state["reason"]},
        )


def _mark_machine_success(studio: dict) -> None:
    machine = studio.get("machine", "local")
    state = _machine_protection.get(machine)
    if not state:
        return
    had_cooldown = state.get("cooldown_until") is not None
    had_failures = bool(state.get("failures"))
    state.update(failures=0, cooldown_until=None, reason=None,
                 last_success_at=time.time())
    if had_cooldown:
        from . import alerts
        alerts.emit("machine_recovered", f"{machine} passed a worker request and rejoined the pool",
                    {"machine": machine})
    elif not had_failures:
        _machine_protection.pop(machine, None)


# Model failures are independent of transport health. Persist blocked models so
# a healthy HTTP response or Hub restart cannot feed broken weights more work.
MODEL_PROTECTION_FILE = DATA_DIR / "model_protection.json"
_model_protection: dict[tuple[str, str], dict] = {}
_model_protection_lock = threading.RLock()
MODEL_FAILURE_WINDOW_S = 300.0
MODEL_FAILURE_THRESHOLD = 3


def _load_model_protection() -> None:
    try:
        rows = json.loads(MODEL_PROTECTION_FILE.read_text())
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("studio"), str) and isinstance(row.get("model"), str):
                _model_protection[(row["studio"], row["model"])] = row
    except (OSError, ValueError, TypeError):
        pass


def _save_model_protection() -> None:
    temporary = MODEL_PROTECTION_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(list(_model_protection.values()), indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(MODEL_PROTECTION_FILE)


def model_protection_snapshot() -> list[dict]:
    with _model_protection_lock:
        return [{**row, "reason": _sanitize_public_error(row.get("reason", ""))}
                for row in _model_protection.values() if row.get("blocked")]


def prune_model_protection(known_models: set[str]) -> list[str]:
    """Forget pauses for models this build no longer ships, and say which.

    A pause row for a model with no manifest can never be cleared: `recheck_model_protection` verifies
    readiness through the worker catalogue, and a model that is not packaged is not in it, so the
    check can only fail. Removing F5-TTS left exactly such a row - a permanent "paused" for a model
    that cannot be installed - on every Hub that had ever run it.
    """
    with _model_protection_lock:
        stale = sorted({model for _studio, model in _model_protection if model not in known_models})
        for key in [key for key in _model_protection if key[1] not in known_models]:
            _model_protection.pop(key, None)
        if stale:
            _save_model_protection()
    return stale


def model_block_state(studio_id: str, model: str) -> dict | None:
    """The pause row for one Studio's model, or None when it is running normally.

    `recheck_model_protection` could always clear a pause, but nothing in the product called it, so a
    paused model was unreachable: the Hub refused every job for it, the reason lived only in this
    process, and the only way back was to edit `model_protection.json` and restart. That is how a
    single wrong field name looked like "Kokoro does not work" for a whole release cycle.
    """
    with _model_protection_lock:
        row = _model_protection.get((studio_id, model)) or {}
    if not row.get("blocked"):
        return None
    return {
        "studio": studio_id, "model": model, "revision": row.get("revision"),
        "reason": _sanitize_public_error(row.get("reason", "")),
        "error_code": row.get("error_code"), "failures": int(row.get("failures") or 0),
        "blocked_at": row.get("blocked_at"), "last_failure_at": row.get("last_failure_at"),
    }


def _model_file_failure(message: str, code: str | None) -> bool:
    return code == "MODEL_FILES_UNAVAILABLE" or (
        "load_safetensors" in message.lower() and "failed to open" in message.lower()
    )


def _mark_model_failure(studio: dict, model: str, revision: str | None,
                        code: str, reason: str) -> None:
    now = time.time()
    key = (studio["id"], model)
    with _model_protection_lock:
        previous = _model_protection.get(key) or {}
        was_blocked = bool(previous.get("blocked"))
        window_started = previous.get("window_started_at", now)
        recent = 0 <= now - float(window_started) < MODEL_FAILURE_WINDOW_S
        same_revision = previous.get("revision") == revision
        failures = int(previous.get("failures") or 0) + 1 if recent and same_revision else 1
        blocked = was_blocked or code == "MODEL_FILES_UNAVAILABLE" or failures >= MODEL_FAILURE_THRESHOLD
        _model_protection[key] = {
            "studio": studio["id"], "model": model, "revision": revision,
            "reason": _sanitize_public_error(reason), "error_code": code,
            "failures": failures, "last_failure_at": now,
            "window_started_at": window_started if recent and same_revision else now,
            "blocked": blocked, "blocked_at": previous.get("blocked_at") or (now if blocked else None),
        }
        _save_model_protection()
    if blocked and not was_blocked:
        from . import alerts
        alerts.emit("model_paused", f"{studio['id']}: {model} paused after model failures",
                    {"studio": studio["id"], "model": model, "reason": _sanitize_public_error(reason)})


def _model_block_note(studio: dict, model: str, entry: dict) -> str | None:
    readiness = entry.get("readiness")
    readiness = readiness if isinstance(readiness, dict) else {}
    if readiness.get("ready") is False:
        return str(readiness.get("reason") or "Required model files or dependencies are unavailable")
    if entry.get("execution_ready") is False:
        return "Model dependencies or execution requirements are not ready"
    with _model_protection_lock:
        row = _model_protection.get((studio["id"], model)) or {}
        if row.get("blocked"):
            return str(row.get("reason") or "Model paused; repair and recheck readiness")
    return None


async def recheck_model_protection(studio_id: str, model: str) -> dict:
    studio = next((s for s in _monitor().registry if s["id"] == studio_id), None)
    if studio is None:
        raise ValueError("The Studio is no longer registered")
    with _model_protection_lock:
        previous = dict(_model_protection.get((studio_id, model)) or {})
    url, headers = studio_request(studio, "/api/catalog")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=15.0)
            response.raise_for_status()
            entry = next((m for m in response.json().get("models", [])
                          if m.get("repo") == model or model in (m.get("aliases") or [])), None)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Fresh model readiness could not be verified") from exc
    readiness = entry.get("readiness") if entry else None
    # Voice already reports per-engine dependencies and companion cache state.
    # Those facts can reopen an engine-error circuit, but cannot prove that a
    # previously unreadable model file has been repaired.
    if (not isinstance(readiness, dict) and entry
            and previous.get("error_code") != "MODEL_FILES_UNAVAILABLE"
            and entry.get("runtime_ready") is True and entry.get("available") is True
            and isinstance(entry.get("cache"), dict) and entry["cache"].get("state") == "cached"):
        readiness = {"ready": True, "checks": "runtime_dependencies_and_companion_cache"}
    if not isinstance(readiness, dict) or readiness.get("ready") is not True or entry.get("execution_ready") is False:
        raise ValueError("Model readiness is not verified; repair its files/dependencies or update this Studio first")
    with _model_protection_lock:
        if (_model_protection.get((studio_id, model)) or {}) != previous:
            raise ValueError("A new failure arrived during the readiness check; recheck again")
        _model_protection.pop((studio_id, model), None)
        _save_model_protection()
    _wakeup.set()
    return {"ok": True, "studio": studio_id, "model": model, "readiness": readiness}


_load_model_protection()


def _is_capacity_failure(message: str) -> bool:
    value = message.lower()
    return any(token in value for token in (
        "memoryguarderror", "memory guard paused", "waiting for memory",
        "not enough memory", "insufficient memory",
    ))


async def _insufficient_memory_text(b: dict, studio: dict, failures: int) -> str:
    """The sentence an operator (and the Gateway's user) reads when a Mac hands a job back: which Mac,
    how much it needs and how much it has - from the Hub's own admission policy and host snapshot."""
    machine = label_for(studio.get("machine", "local"))
    need = free = None
    try:
        entry = await _catalog_entry(studio, b["model"])
        mem = _admission_requirements(b["model"], entry) if entry else None
        need = _required_free_memory(mem) if mem else None
        host = _host_for_studio(studio)
        free = float(host["available_gb"]) if host else None
    except (KeyError, TypeError, ValueError):
        pass
    numbers = (f"needs about {need:.1f} GB free and has about {free:.1f} GB free"
               if need is not None and free is not None else "does not have enough free memory")
    return (f"{machine} {numbers} for {b['model']} (it refused {failures} times). "
            f"Another Mac with more free memory can run this job.")


def _is_transport_failure(exc: BaseException, message: str) -> bool:
    if getattr(exc, "model_unavailable", False):
        return False
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if getattr(exc, "status_code", None) in {502, 503, 504}:
        return True
    value = message.lower().strip()
    return any(token in value for token in (
        "readerror", "read error", "connection dropped", "connection reset",
        "connection refused", "server disconnected", "remote protocol error",
        "timed out", "timeout", "network is unreachable", "broken pipe",
    ))


def _is_machine_reservation_refusal(exc: BaseException) -> bool:
    """Whether this failure is a Mac in the pool that is reserved right now.

    `standalone._peer_admission_refusal` applies the *serving* Mac's own lease before its
    worker sees a peer request, and answers ``409 {"detail": ..., "code": "MACHINE_BUSY"}``
    while the lease is held. That is the condition the Hub's own scheduler treats as a skip
    through `busy_machines()` - a lease is transient by construction, because the work
    holding it finishes or its TTL expires - so an item that meets it must wait, not fail.

    The code is required, not just the status. A 409 is also how a peer names an operator
    decision no other Mac can fix (a disabled machine or Studio, a paused model), and those
    must stay terminal.
    """
    return (getattr(exc, "status_code", None) == 409
            and str(getattr(exc, "error_code", "") or "") == "MACHINE_BUSY")


def _is_disk_full_refusal(exc: BaseException) -> bool:
    """Whether a Mac answered "my disk is full": HTTP 507, or the OUTPUT_STORAGE_BLOCKED /
    DISK_FULL code (`standalone._peer_refusal_response` writes both for a full node). Nothing was
    submitted, so nothing was attempted."""
    return (getattr(exc, "status_code", None) == 507
            or str(getattr(exc, "error_code", "") or "") in {"OUTPUT_STORAGE_BLOCKED", "DISK_FULL"})


def _item_allows_studio(item: dict, studio: dict, now: float) -> bool:
    avoided = item.get("avoid_machines") or {}
    return float(avoided.get(studio.get("machine", "local"), 0) or 0) <= now


_packaged_monitor = None
_admission_guard = None


def configure_monitor(monitor) -> None:
    """Bind the packaged application's monitor without importing legacy main."""
    global _packaged_monitor
    _packaged_monitor = monitor


def configure_admission_guard(guard) -> None:
    """Install the packaged model readiness check without importing its app."""
    global _admission_guard
    _admission_guard = guard


def admission_refusal(envelope: dict) -> dict | None:
    """Apply the packaged model gate to an accepted or restored job."""
    if _admission_guard is None:
        return None
    try:
        return _admission_guard(envelope)
    except ValueError as exc:
        return {"code": "MODEL_CATALOG_UNAVAILABLE", "detail": str(exc)}


def _monitor():
    """The packaged application's monitor, bound by ``configure_monitor`` at startup.

    The fallback here used to be ``from .main import monitor`` — a module that has not existed since
    the Hub moved to ``standalone.py``, so it raised ``ModuleNotFoundError`` naming a file nobody
    could find. Nothing depended on it (``standalone.py`` configures the monitor before any route
    runs), which is exactly why it survived: a dead import that only fails on the path nobody takes
    until they do. A missing monitor is a programming error, and it now says so.
    """
    if _packaged_monitor is None:
        raise RuntimeError(
            'the Hub monitor has not been configured; call broker.configure_monitor(monitor) at '
            'startup (components/hub/standalone.py does this) before using this route')
    return _packaged_monitor


async def _catalog_entry(studio: dict, model: str) -> dict | None:
    """The studio's own catalog entry for a model, kept verbatim.
    Carries cache state for model-aware dispatch and capability facts for the
    governor: min_unified_memory_gb = 'needs ≥N GB TOTAL machine'. size_gb
    is download/disk metadata and is never treated as runtime RAM."""
    catalog = await _monitor().scheduling_catalog(studio)
    for m in (catalog or {}).get("models", []):
        if m.get("repo") == model or model in (m.get("aliases") or []):
            return m
    return None


async def _lane_accepts_output_format(studio: dict, model: str, value: object) -> bool:
    """Does this worker's published lane contract list ``value`` for ``output_format``?

    The field is optional per job and an older worker silently drops what it does not know, which
    would make recovery's request comparison fail on a perfectly good job. So it is sent only to a
    worker whose own catalog says it honours that value; anything else (no catalog, no parameter,
    a value outside its enum) is treated as "not supported" and the job runs as it always has.
    """
    entry = await _catalog_entry(studio, model)
    contract = (entry or {}).get("lane_contract")
    for parameter in (contract.get("parameters") if isinstance(contract, dict) else None) or ():
        if isinstance(parameter, dict) and parameter.get("name") == "output_format":
            options = parameter.get("enum")
            return not isinstance(options, list) or value in options
    return False


def _normalized_immutable_revision(value: object) -> str | None:
    text = str(value or "").strip()
    if not _IMMUTABLE_MODEL_REVISION.fullmatch(text):
        return None
    return text.removeprefix("sha256:").lower()


def _catalog_immutable_revision(entry: dict) -> str | None:
    """Return only an immutable worker-owned cached model revision."""
    sources = [entry]
    if isinstance(entry.get("cache"), dict):
        sources.append(entry["cache"])
    for source in sources:
        for field in _MODEL_REVISION_FIELDS:
            revision = _normalized_immutable_revision(source.get(field))
            if revision is not None:
                return revision
    return None


def _catalog_matches_genstudio_revision(batch: dict, entry: dict) -> bool:
    """Fence a GenStudio attempt to the exact model snapshot it assigned.

    Legacy callers without GenStudio execution identity retain their existing
    dispatch behavior. A supplied but mutable/invalid expected revision cannot
    be satisfied by inventing evidence at Studio Hub.
    """
    execution = batch.get("genstudio_execution")
    if not isinstance(execution, dict) or not execution.get("model_revision"):
        return True
    expected = _normalized_immutable_revision(execution.get("model_revision"))
    actual = _catalog_immutable_revision(entry)
    return expected is not None and actual == expected


def _expire_genstudio_batch(batch: dict) -> bool:
    """Fence unfinished local work after GenStudio's renewable lease expires."""
    if not execution_identity.lease_expired(batch.get("genstudio_execution")):
        return False
    unfinished = [
        item for item in batch.get("items") or []
        if item.get("state") in {"queued", "running", "cancel_requested"}
    ]
    # A lease protects ownership while work is still executing. Once every
    # item is terminal, expiring that lease must not rewrite a completed batch
    # as cancelled or make a successfully generated artifact look failed in
    # operator history.
    if not unfinished:
        return False
    first_expiry = not batch.get("lease_expired")
    batch["cancelled"] = True
    batch["lease_expired"] = True
    batch["governor_note"] = "GenStudio execution lease expired"
    now = time.time()
    for item in unfinished:
        if (batch.get("modality") == "voice"
                and item.get("state") in {"running", "cancel_requested"}):
            if not _mark_accepted_voice_cancel_uncertain(batch, item):
                reason = (
                    "The GenStudio lease expired during a dispatched Voice attempt, "
                    "but its worker receipt is unavailable; manual action is required."
                )
                _mark_voice_uncertain(item, reason)
                item["error_code"] = "VOICE_CANCEL_OUTCOME_UNCERTAIN"
                item["error"] = reason
        elif batch.get("modality") in DURABLE_ASYNC_MODALITIES and item.get("state") in {
                "running", "cancel_requested"}:
            studio = next((row for row in _monitor().registry
                           if row.get("id") == item.get("studio")), {})
            _mark_async_uncertain(
                item, studio, batch["modality"],
                f"GenStudio lease expired during a {batch['modality'].title()} attempt; cancellation "
                "was requested, but the original worker outcome must be reconciled.",
            )
        else:
            item["state"] = "cancelled"
            item["error"] = "GenStudio execution lease expired"
            item["finished_at"] = now
            item["retry_at"] = None
    if first_expiry:
        from . import alerts
        execution = batch.get("genstudio_execution") or {}
        alerts.emit(
            "genstudio_lease_expired",
            f"GenStudio lease expired with {len(unfinished)} unfinished item(s)",
            {
                "batch_id": batch.get("id"),
                "modality": batch.get("modality"),
                "genstudio_job_id": execution.get("genstudio_job_id"),
                "genstudio_attempt_id": execution.get("genstudio_attempt_id"),
                "unfinished_items": len(unfinished),
            },
        )
    return True


def renew_execution_lease(renewal: dict) -> bool:
    """Apply a validated renewal to the matching in-memory/durable batch."""
    batch_id = renewal.get("local_batch_id")
    batch = batches.get(batch_id) if batch_id else None
    if batch is None and batch_id:
        batch = ledger.load_batch(batch_id)
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
    ledger.save_batch(batch)
    _wakeup.set()
    return True


def _voice_recovery_owns(item: dict) -> bool:
    """Whether recovery owns this accepted Voice identity, including after terminal adoption."""
    return item.get("state") == "uncertain" or isinstance(item.get("recovery"), dict)


def _mark_voice_uncertain(item: dict, reason: str) -> None:
    """Fence accepted Voice work that cannot be safely replayed.

    Voice Studio owns native generation and an accepted request may continue
    after this Hub process exits.  Keep the exact worker identity for an
    operator to reconcile; clearing it or putting the item back on the queue
    could submit the customer's audio twice.
    """
    item["state"] = "uncertain"
    item["retry_at"] = None
    item["recovery"] = {
        "phase": "uncertain",
        "reason": reason,
        "updated_at": time.time(),
    }


def _mark_accepted_voice_cancel_uncertain(batch: dict, item: dict) -> bool:
    """Keep accepted Voice work fenced until its worker reports the outcome."""
    if batch.get("modality") != "voice" or not item.get("studio_job_id"):
        return False
    if batch.get("lease_expired"):
        reason = (
            "The GenStudio lease expired after Voice worker acceptance; "
            "the original job outcome must be reconciled."
        )
    elif item.get("cancel_error"):
        reason = (
            "The Voice worker did not confirm cancellation; "
            "the original job outcome must be reconciled."
        )
    else:
        reason = (
            "Voice cancellation was requested after worker acceptance; "
            "the original job outcome must be reconciled."
        )
    _mark_voice_uncertain(item, reason)
    item["error_code"] = "VOICE_CANCEL_OUTCOME_UNCERTAIN"
    item["error"] = reason
    return True


def _mark_async_uncertain(item: dict, studio: dict, modality: str, reason: str,
                          code: str | None = None) -> None:
    """Retain an async attempt whose worker outcome cannot be safely replayed."""
    item["state"] = "uncertain"
    item["retry_at"] = None
    item.setdefault("machine", str(studio.get("machine") or "local")[:500])
    item.setdefault("worker_id", str(studio.get("id") or "unknown")[:500])
    item["error_code"] = code or f"{modality.upper()}_OUTCOME_UNCERTAIN"
    item["error"] = reason
    item["recovery"] = {"phase": "uncertain", "reason": reason,
                        "updated_at": time.time()}


def _mark_image_uncertain(item: dict, studio: dict, reason: str,
                          code: str = "IMAGE_OUTCOME_UNCERTAIN") -> None:
    """Retain an Image attempt whose worker outcome cannot be safely replayed."""
    _mark_async_uncertain(item, studio, "image", reason, code)


def set_voice_recovery(item: dict, phase: str, reason: str) -> dict:
    """Persist the small, safe operator trail for one Voice item."""
    if phase not in {
        "uncertain", "draining", "cancel_requested", "restart_requested",
        "health_wait", "reconciled", "manual_action_required",
    }:
        raise ValueError("unknown Voice recovery phase")
    recovery = dict(item.get("recovery") or {})
    recovery.update({
        "phase": phase,
        "reason": str(reason)[:500],
        "updated_at": time.time(),
    })
    item["recovery"] = recovery
    return recovery


def voice_recovery_item(batch_id: str, item_index: int) -> tuple[dict, dict]:
    """Return one durable Voice recovery target without widening job control."""
    batch = batches.get(batch_id) or ledger.load_batch(batch_id)
    if batch is None:
        raise ValueError("unknown batch")
    if batch.get("modality") != "voice":
        raise ValueError("Voice recovery is available only for Voice jobs")
    item = next((row for row in batch.get("items", [])
                 if row.get("index") == item_index), None)
    if item is None:
        raise ValueError("unknown job item")
    if item.get("state") != "uncertain":
        raise ValueError("Voice recovery requires an uncertain job")
    if not item.get("studio") or not item.get("studio_job_id"):
        _mark_voice_uncertain(
            item,
            "The original Voice Studio identity is unavailable; manual action is required.",
        )
        set_voice_recovery(
            item, "manual_action_required",
            "The original Voice Studio identity is unavailable; manual action is required.",
        )
        batches[batch["id"]] = batch
        ledger.save_batch(batch)
        raise ValueError("original Voice Studio identity is unavailable")
    batches[batch["id"]] = batch
    return batch, item


def restore_batches():
    """Reload unfinished batches after a Hub restart.

    Accepted Voice, Image, and Music jobs remain fenced after restart. Other
    modalities retain their historic retry behavior.
    """
    for b in ledger.load_unfinished_batches():
        if _expire_genstudio_batch(b):
            batches[b["id"]] = b
            ledger.save_batch(b)
            continue
        for it in b["items"]:
            if it["state"] in {"running", "cancel_requested"} and b.get("modality") == "voice":
                _mark_voice_uncertain(
                    it,
                    "Hub restarted while Voice Studio work was active; "
                    "the original job must be reconciled before any retry.",
                )
            elif (it["state"] in {"running", "cancel_requested"}
                  and b.get("modality") in DURABLE_ASYNC_MODALITIES):
                studio = next((row for row in _monitor().registry
                               if row.get("id") == it.get("studio")), {})
                _mark_async_uncertain(
                    it, studio, b["modality"],
                    f"Hub restarted while a {b['modality'].title()} worker attempt was active; "
                    "reconcile the original job before any retry.",
                )
            elif it["state"] == "running":
                it["state"] = "queued"
                it["studio"] = None
                it["studio_job_id"] = None
        batches[b["id"]] = b
        ledger.save_batch(b)
    if batches:
        _wakeup.set()
    return len(batches)


def _shared_params_with_local_reference(envelope: dict) -> dict:
    """Lift the top-level voice choices into the item parameter map.

    GenStudio transports these inside ``sharedParams``.  The native app submits
    them beside ``model``/``items`` like the Image reference envelope does, so
    the top-level form is folded in without overwriting an explicit
    ``sharedParams`` value.

    ``voice_library_id`` is lifted for the same reason as the reference asset
    id, and it was missed at first: a saved voice chosen in the app reached the
    Hub, was dropped here, and the Voice worker then refused the job with "Voice
    cloning needs a reference voice or private reference upload" - a message
    about the reference, for a voice the operator had just selected from a list.
    Anything the app can put in one of these two slots belongs in this map.
    """
    shared = dict(envelope.get("sharedParams") or {})
    for key in ("voice_reference_asset_id", "voice_library_id"):
        value = str(envelope.get(key) or "").strip()
        if value and not str(shared.get(key) or "").strip():
            shared[key] = value
    return shared


def submit_batch(envelope: dict) -> dict:
    with _submit_lock:
        return _submit_batch_locked(envelope)


def _submit_batch_locked(envelope: dict) -> dict:
    modality = envelope.get("modality")
    if modality not in MODALITY:
        return {"error": f"modality must be one of {sorted(MODALITY)}"}
    try:
        # Refuse an unclassified modality here, at the door, rather than letting it take the
        # non-durable retry path silently.
        retry_class(modality)
    except ValueError as exc:
        return {"error": str(exc)}
    items_in = envelope.get("items") or []
    if not items_in:
        return {"error": "items must be a non-empty list"}
    if len(items_in) > MAX_BATCH_ITEMS:
        return {"error": f"items is limited to {MAX_BATCH_ITEMS} per batch"}
    try:
        if len(json.dumps(envelope, separators=(",", ":")).encode()) > MAX_BATCH_JSON_BYTES:
            return {"error": "batch payload exceeds the 25 MB limit"}
    except (TypeError, ValueError):
        return {"error": "batch payload must be valid JSON"}
    if not envelope.get("model"):
        return {"error": "model (repo) is required"}
    # Refuse at the door: cloud work is never queued, never accepted
    # and then failed. This runs before execution_identity.prepare so a refused
    # submission also leaves no fence or idempotency record behind.
    refusal = cloud_guard.refusal(
        envelope, model=envelope["model"], modality=modality,
    )
    if refusal:
        return {"error": refusal, "code": cloud_guard.REFUSAL_CODE}
    routing, routing_error = routing_rule.parse(envelope.get("routing"))
    if routing_error:
        return {"error": routing_error}
    if routing.startswith(routing_rule.MACHINE_PREFIX):
        # A pin is an instruction, so an unknown machine is refused here rather than left to
        # queue forever with nothing to say. `local` is this Mac; anything else joined it.
        target = routing.split(":", 1)[1]
        known = {s.get("machine", "local") for s in _monitor().registry}
        if target not in known:
            return {"error": f"no machine named {target!r} is in this fleet; known: "
                             + ", ".join(sorted(known))}
    try:
        prepared = execution_identity.prepare(envelope)
    except execution_identity.ExecutionIdentityError as exc:
        return {"error": str(exc)}
    envelope = prepared.envelope
    client_request_id = envelope.get("clientRequestId")
    if client_request_id is not None:
        if (
            not isinstance(client_request_id, str)
            or not _CLIENT_REQUEST_ID_PATTERN.fullmatch(client_request_id)
        ):
            return {
                "error": "clientRequestId must be 8-160 safe letters, digits, or ._:-"
            }
        fingerprint_payload = {
            key: value for key, value in envelope.items() if key != "clientRequestId"
        }
        # GenStudio may reassign the exact attempt with a newer externally
        # issued fence. Ownership transport is not generation payload, so it
        # must not turn an otherwise exact idempotent replay into a conflict.
        if isinstance(fingerprint_payload.get("genstudio_execution"), dict):
            fingerprint_payload["genstudio_execution"] = {
                key: value
                for key, value in fingerprint_payload["genstudio_execution"].items()
                if key != "fencing_token"
            }
        request_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        existing = next(
            (
                batch
                for batch in batches.values()
                if batch.get("client_request_id") == client_request_id
            ),
            None,
        ) or ledger.load_batch_by_client_request_id(client_request_id)
        if existing is not None:
            if existing.get("request_fingerprint") != request_fingerprint:
                return {"error": "clientRequestId was already used for a different batch"}
            current_execution = existing.get("genstudio_execution") or {}
            if (
                prepared.evidence
                and current_execution
                and prepared.evidence["fencing_token"]
                    > int(current_execution.get("fencing_token") or 0)
            ):
                # Preserve the newest GenStudio-issued fence as evidence while
                # returning the same local batch and creating no new work.
                existing["genstudio_execution"] = prepared.evidence
                if existing["id"] in batches:
                    batches[existing["id"]]["genstudio_execution"] = prepared.evidence
                ledger.save_batch(existing)
            execution_identity.bind_local_batch(prepared.evidence, existing["id"])
            return {
                "batch_id": existing["id"],
                "items": len(existing.get("items") or []),
                "replayed": True,
            }
    else:
        request_fingerprint = None
    batch_id = uuid.uuid4().hex[:10]
    batches[batch_id] = {
        "id": batch_id,
        "client_request_id": client_request_id,
        "request_fingerprint": request_fingerprint,
        # Non-authoritative identity assigned by GenStudio. Studio Hub only
        # uses it to fence and evidence this one site-local execution.
        "genstudio_execution": prepared.evidence,
        "modality": modality,
        "model": envelope["model"],
        "shared_params": _shared_params_with_local_reference(envelope),
        "routing": routing,
        "label": envelope.get("label"),        # who submitted (e.g. "storystudio")
        "webhook": envelope.get("webhook"),    # POSTed the summary on completion
        "item_webhook": envelope.get("itemWebhook"),  # POSTed per item as each finishes
        "webhook_sent": False,
        "created_at": time.time(),
        "cancelled": False,
        "items": [{
            "index": i,
            "prompt": it.get("prompt") or it.get("text") or "",
            "seed": it.get("seed"),
            "params": it.get("params") or {},
            "state": "queued",       # queued|running|done|error|cancelled|uncertain
            "tries": 0,
            "studio": None,
            "studio_job_id": None,
            "client_request_id": (
                f"studiohub:{batch_id}:{i}" if modality == "voice" else None
            ),
            "artifact_path": None,
            "artifact_url": None,
            "asset_id": None,
            "error": None,
            "retry_at": None,
        } for i, it in enumerate(items_in)],
    }
    ledger.save_batch(batches[batch_id])
    execution_identity.bind_local_batch(prepared.evidence, batch_id)
    _wakeup.set()
    return {"batch_id": batch_id, "items": len(items_in), "replayed": False}


def busy_studios() -> set:
    """Studio ids currently running a batch item (i.e. 'generating')."""
    return set(_busy)


def activity_batches() -> dict[str, dict]:
    """This queue's batches in the shape the live activity view consumes.

    Each Hub-owned queue publishes its own view rather than being restated by the callers that
    merge them (`monitor.poll_all` and the statistics route); that restatement is how Chat was
    missed by both of them. Broker batches already carry `model`, `items` and their studio ids,
    so this is the queue itself.
    """
    return dict(batches)


# Job stores that own their work and are deliberately *not* listed by `GET /api/hub/jobs`, with
# the reason. Each has its own shape, its own summary and its own route, and none of them is
# actionable through the generic job routes - a listed row that cannot be cancelled, retried or
# read is a worse answer than a row that is honestly absent. Declared here, beside the list
# function, so the next lane has to make this decision instead of being missed:
# `tests/test_hub_jobs.py` fails for a `*_jobs` module that owns a queue and appears in neither
# `listed_batches` nor this table.
UNLISTED_JOB_LANES = {
    "chat_jobs": "the GenStudio pack lane: pack-shaped jobs with their own summary, driven by an "
                 "external controller rather than by any route on this Hub",
    "transcription_jobs": "Subtitle owns /api/hub/transcription/jobs, and its items are "
                          "file-backed transcription records rather than broker items",
}


# The Jobs page lists the newest finished batches, not every batch the ledger has ever held: the app
# re-reads this list every 2-10 s, and the ledger only grows (measured: 248 batches / 6 MB = 0.13 s per
# read, so a controller with a few thousand would spend seconds, and ship tens of MB, per poll).
# Older history stays in the ledger, in Stats and behind `GET /api/hub/jobs/{id}`.
LISTED_FINISHED_LIMIT = 500


def listed_batches() -> list[dict]:
    """Every job `GET /api/hub/jobs` shows, newest first, as summaries.

    One producer-side function so "what the Jobs page can show" has a single definition: the
    ledger's finished batches - which survive a restart, and which every finished producer
    (including the Chat page's own completions) writes through `ledger.save_batch` - merged with
    the broker's in-memory queue.
    """
    listed: dict[str, dict] = {batch["id"]: batch
                               for batch in ledger.load_finished_batches(LISTED_FINISHED_LIMIT)}
    listed.update(batches)
    return [batch_summary(batch) for batch in sorted(
        listed.values(), key=lambda value: -value["created_at"])]


def busy_machines() -> set[str]:
    """Physical machines holding a non-preemptive heavy-work lease."""
    by_id = {s["id"]: s for s in _monitor().registry}
    if update_drain.draining():
        # This Mac is finishing its work to install an update: its own machine is taken. A
        # controller's joined nodes are other Macs and keep taking work - only this Mac's own
        # work drains (it used to name every machine, so a draining controller left its idle
        # nodes idle for the whole drain).
        return {s.get("machine", "local") for s in by_id.values()
                if peers.is_local_studio(s)} | {"local"}
    occupied = set(_external_machine_leases) | {
        by_id[sid].get("machine", "local")
        for sid in _busy
        if sid in by_id
    }
    for batch in batches.values():
        if batch.get("modality") in RECEIPT_DURABLE_MODALITIES:
            for item in batch.get("items") or []:
                if item.get("state") == "uncertain":
                    machine = item.get("machine") or (
                        by_id.get(item.get("studio")) or {}).get("machine")
                    if machine:
                        occupied.add(str(machine))
    from . import transcription_jobs
    if transcription_jobs._packaged():
        for batch in transcription_jobs.batches.values():
            for item in batch.get("items") or []:
                if item.get("state") == "uncertain":
                    # Dispatch persists the physical identity. Use the
                    # registry only for older attempts that lacked the field.
                    machine = item.get("machine") or (
                        by_id.get(item.get("studio")) or {}).get("machine")
                    if machine:
                        occupied.add(str(machine))
    return occupied


def acquire_external_machine(machine: str, owner: str) -> bool:
    """Atomically reserve a physical machine for another heavy-work queue."""
    if machine in busy_machines():
        return False
    # Episode renders are the fleet's highest-priority queued work. Never
    # interrupt a running job, but once a render is waiting, reserve an eligible
    # render Mac so a Chat/transcription poller cannot win the next-free race.
    if not owner.startswith("render:") and _pending_render_for_machine(machine):
        return False
    _external_machine_leases[machine] = owner
    _wakeup.set()
    return True


def external_dispatch_allowed(
        machine: str, lane: str, *, other_lane_has_work: bool) -> bool:
    """Apply the shared chat/transcription turn token for one physical Mac.

    The token is consulted only while both lanes have queued work. If the
    other lane is empty, the caller may use a free machine immediately even if
    the previous token points elsewhere. ``acquire_external_machine`` still
    provides the atomic lease and is the final authority before dispatch.
    """
    if lane not in _EXTERNAL_LANES:
        raise ValueError(f"unsupported external dispatch lane: {lane}")
    if not other_lane_has_work:
        return True
    return _external_lane_turn.get(machine) in {None, lane}


def note_external_dispatch(machine: str, lane: str) -> None:
    """Pass the next shared-machine turn to the opposite auxiliary lane."""
    if lane not in _EXTERNAL_LANES:
        raise ValueError(f"unsupported external dispatch lane: {lane}")
    other = next(iter(_EXTERNAL_LANES - {lane}))
    _external_lane_turn[machine] = other


def note_external_memory_block(machine: str, lane: str) -> None:
    """Pass a blocked lane's shared-machine turn to its eligible opposite."""
    if lane not in _EXTERNAL_LANES:
        raise ValueError(f"unsupported external dispatch lane: {lane}")
    other = next(iter(_EXTERNAL_LANES - {lane}))
    _external_lane_turn[machine] = other
    _wakeup.set()


def _pending_render_for_machine(machine: str) -> bool:
    if not any(
        b.get("modality") == "render" and not b.get("cancelled")
        and any(item.get("state") == "queued" for item in b.get("items", []))
        for b in batches.values()
    ):
        return False
    mon = _monitor()
    return any(
        studio.get("modality") == "render"
        and studio.get("machine", "local") == machine
        and machine_enabled(machine)
        and studio_enabled(machine, studio["id"])
        and mon.status.get(studio["id"], {}).get("status") == "up"
        for studio in mon.registry
    )


def release_external_machine(machine: str, owner: str) -> None:
    if _external_machine_leases.get(machine) == owner:
        del _external_machine_leases[machine]
        _wakeup.set()


def set_maintenance(studio_id: str, enabled: bool):
    if enabled:
        _maintenance.add(studio_id)
    else:
        _maintenance.discard(studio_id)
        _wakeup.set()


def in_maintenance(studio_id: str) -> bool:
    return studio_id in _maintenance


def _recent_avg(modality: str, model: str, limit: int = 50) -> float | None:
    """Average completed-item duration for this (modality, model) across ALL
    batches — so even a 1-item batch gets an ETA from the model's track record,
    not just from its own (nonexistent) completed siblings."""
    durs = []
    for b in batches.values():
        if b["modality"] != modality or b["model"] != model:
            continue
        for i in b["items"]:
            runtime = i.get("runtime_s", i.get("duration_s"))
            if i.get("state") == "done" and isinstance(runtime, (int, float)):
                durs.append(runtime)
    durs = durs[-limit:]
    return round(sum(durs) / len(durs), 1) if durs else None


def item_errors(b: dict) -> list[dict]:
    """The failed items only, as ``{item_index, code, message}``.

    An item that is fine contributes nothing, so an empty list is the honest
    answer for a healthy batch.  Missing detail stays null rather than being
    invented.
    """
    rows: list[dict] = []
    for item in b.get("items") or []:
        if item.get("state") != "error":
            continue
        code = item.get("error_code")
        message = item.get("error")
        rows.append({
            "item_index": item.get("index"),
            "code": (_sanitize_public_error_code(code) if code else None),
            "message": (_sanitize_public_error(message) if message else None),
        })
    return rows


def _batch_stalled_seconds(active: bool, activity_at: list, now: float,
                           capacity_waiting_s: float | None = None) -> float | None:
    """Seconds this batch has been silent while active, or ``None`` when it has not been.

    Reuses `stall.STALL_AFTER_S` rather than inventing a second threshold: one number decides
    how long silence has to last before it means something, whether the clock being read is a
    worker's report receipt or a batch's own progress. ``capacity_waiting_s`` is the other way a
    batch is silent: refused for memory, attempt after attempt - each attempt re-stamps the
    progress clock, so that wait is measured from its first refusal instead.
    """
    if not active:
        return None
    candidates = []
    stamps = [v for v in activity_at if isinstance(v, (int, float)) and v > 0]
    if stamps:
        candidates.append(now - max(stamps))
    if capacity_waiting_s is not None:
        candidates.append(capacity_waiting_s)
    silent_for = max(candidates, default=0.0)
    return round(silent_for, 1) if silent_for > stall.STALL_AFTER_S else None


def batch_summary(b: dict) -> dict:
    items = b["items"]
    states = [i["state"] for i in items]
    now = time.time()
    # ETA basis: this batch's own completed items if any, else the model's recent
    # average across every batch (so single-item jobs still get an estimate).
    done_durs = [i.get("runtime_s", i.get("duration_s")) for i in items
                 if i.get("state") == "done"
                 and isinstance(i.get("runtime_s", i.get("duration_s")), (int, float))]
    avg_s = (round(sum(done_durs) / len(done_durs), 1) if done_durs
             else _recent_avg(b["modality"], b["model"]))
    # Per-item live detail for whatever is running right now (machine tag + progress).
    # Keep batch-level timing separately: the Jobs page needs to answer both
    # "how long has this been processing?" and "has anything moved recently?".
    running_items = []
    started_at = []
    activity_at = [b.get("created_at", now), b.get("last_dispatched_at", 0)]
    for i in items:
        run_started = i.get("run_started")
        if isinstance(run_started, (int, float)):
            started_at.append(run_started)
            activity_at.append(run_started)
        for key in ("last_progress_at", "finished_at"):
            value = i.get(key)
            if isinstance(value, (int, float)):
                activity_at.append(value)
        # Batches saved before explicit terminal timestamps still have enough
        # information to estimate their most recent completed item.
        runtime = i.get("runtime_s", i.get("duration_s"))
        if (i.get("state") == "done" and isinstance(run_started, (int, float))
                and isinstance(runtime, (int, float))):
            activity_at.append(run_started + runtime)
        if i.get("state") != "running":
            continue
        sid = i.get("studio") or ""
        machine = sid.split("@", 1)[1] if "@" in sid else "local"
        started = run_started
        elapsed = round(now - started, 1) if started else None
        running_items.append({
            "index": i.get("index"),
            "studio": sid,                 # e.g. "image@macmini-m1-01" or "image"
            "machine": machine,            # "macmini-m1-01" or "local"
            "progress": i.get("progress"),  # 0..1 or None
            "chunk_index": i.get("chunk_index"),
            "chunk_total": i.get("chunk_total"),
            "elapsed_s": elapsed,
        })
    retrying = [i for i in items if i["state"] == "queued"
                and (i.get("retry_at") or 0) > now]
    # How long an item has been refused for memory, from its first refusal. Every attempt
    # re-stamps the item's own progress clock, so a worker that keeps dying for lack of memory
    # (45 minutes of ~92 s requeues on an 8 GB Mac) always looked recently active; this clock is
    # not reset by the retries, only by the item finishing. Visible, never destructive.
    capacity_since = [i["capacity_wait_started_at"] for i in items
                      if i["state"] in ("queued", "running") and i.get("capacity_failures")
                      and isinstance(i.get("capacity_wait_started_at"), (int, float))]
    capacity_waiting_s = round(max(0.0, now - min(capacity_since)), 1) if capacity_since else None
    active = bool(states.count("queued") or states.count("running") or states.count("cancel_requested") or states.count("uncertain"))
    processing_started_at = min(started_at) if started_at else None
    last_activity_at = max(activity_at)
    # A missing worker progress report is normal for some local MLX models, so
    # do not call a job stuck merely because a single poll had no percentage.
    # Fifteen minutes, or five times its measured per-item average, is a useful
    # conservative warning threshold rather than an automatic cancellation.
    stalled_after_s = max(15 * 60, round((avg_s or 0) * 5))
    no_progress_s = round(max(0, now - last_activity_at), 1) if active else None
    return {
        "id": b["id"], "modality": b["modality"], "model": b["model"],
        # model_id: the canonical manifest id, so /api/hub/models and
        # /api/hub/jobs can be joined on one string. The stored model key is
        # preserved unchanged.
        "model_id": model_ids.canonical(b.get("model")),
        "errors": item_errors(b),
        "created_at": b["created_at"], "finished_at": b.get("finished_at"),
        "cancelled": b["cancelled"],
        "routing": b.get("routing", "pool"),
        # How long this batch has been silent while still active — the operator's version of
        # the question `stall.py` answers for a worker's report receipt. Here it is the batch's
        # OWN progress clock (`last_progress_at` and friends), because that is what the Jobs page
        # shows and what "has anything moved?" means to the person watching it.
        # **Visible, never destructive**: nothing is cancelled on the strength of this.
        "stalled_seconds": _batch_stalled_seconds(active, activity_at, now, capacity_waiting_s),
        "capacity_waiting_s": capacity_waiting_s,
        "capacity_refusals": max((int(i.get("capacity_failures") or 0) for i in items), default=0),
        # Same rule as the transcription summary: a note explains a job that is waiting, and a
        # finished job is not waiting for anything.
        "governor_note": (b.get("governor_note")
                          if any(state in ("queued", "running", "cancel_requested")
                                 for state in states) else None),
        "label": b.get("label"),
        "total": len(states),
        "queued": states.count("queued") - len(retrying),
        "retrying": len(retrying),
        "next_retry_at": min((i["retry_at"] for i in retrying), default=None),
        "running": states.count("running"),
        "cancel_requested": states.count("cancel_requested"),
        "done": states.count("done"),
        "error": states.count("error"),
        "cancelled_items": states.count("cancelled"),
        "uncertain": states.count("uncertain"),
        "avg_s": avg_s,
        "running_items": running_items,
        "processing_started_at": processing_started_at,
        "processing_elapsed_s": (round(max(0, now - processing_started_at), 1)
                                 if active and processing_started_at else None),
        "last_activity_at": last_activity_at,
        "no_progress_s": no_progress_s,
        "stalled_after_s": stalled_after_s,
        "stalled": bool(active and ((no_progress_s is not None
                                     and no_progress_s >= stalled_after_s)
                                    or (capacity_waiting_s is not None
                                        and capacity_waiting_s >= stalled_after_s))),
    }


async def _signal_worker_cancel(client: httpx.AsyncClient, item: dict) -> bool:
    """Ask the exact Studio worker to stop its active generation job."""
    studio_id = item.get("studio")
    job_id = item.get("studio_job_id")
    if not studio_id or not job_id:
        return False
    studio = next((s for s in _monitor().registry if s["id"] == studio_id), None)
    if not studio:
        item["cancel_error"] = "worker is no longer registered"
        return False
    try:
        url, headers = studio_request(studio, f"/api/generate/jobs/{job_id}")
        response = await client.delete(
            url, headers=headers, timeout=15.0)
        # A 404 means the worker no longer has active work under this id. The
        # broker poll will reconcile whether it completed just before cancel.
        if response.status_code in (200, 404):
            item["cancel_signal_sent_at"] = time.time()
            item.pop("cancel_error", None)
            return True
        item["cancel_error"] = f"worker returned HTTP {response.status_code}"
    except httpx.HTTPError as e:
        item["cancel_error"] = str(e) or type(e).__name__
    return False


async def cancel_batch(batch_id: str, client: httpx.AsyncClient | None = None) -> dict | None:
    """Cancel queued work and immediately signal every known running worker."""
    b = batches.get(batch_id)
    if b is None:
        return None
    b["cancelled"] = True
    queued_cancelled = 0
    for it in b["items"]:
        if it["state"] == "queued":
            it["state"] = "cancelled"
            it["error"] = "Cancelled by user"
            it["retry_at"] = None
            queued_cancelled += 1
    ledger.save_batch(b)
    _wakeup.set()

    running = [it for it in b["items"] if it["state"] == "running"]
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        signals = await asyncio.gather(
            *(_signal_worker_cancel(client, it) for it in running)) if running else []
        for it in b["items"]:
            if it["state"] == "cancelled":
                await _post_item_webhook(client, b, it)
        await _maybe_finish(client, b)
    finally:
        if owns_client:
            await client.aclose()
    return {
        "batch": b,
        "queued_cancelled": queued_cancelled,
        "running_signalled": sum(1 for sent in signals if sent),
        "running_pending": sum(1 for sent in signals if not sent),
    }


async def cancel_batches(modality: str | None = None) -> dict:
    """Cancel every active batch, optionally limited to one modality."""
    targets = [
        b["id"] for b in batches.values()
        if (modality is None or b.get("modality") == modality)
        and any(it.get("state") in ("queued", "running", "cancel_requested") for it in b.get("items", []))
    ]
    results = []
    async with httpx.AsyncClient() as client:
        for batch_id in targets:
            result = await cancel_batch(batch_id, client)
            if result:
                results.append(result)
    return {
        "batches_cancelled": len(results),
        "queued_cancelled": sum(r["queued_cancelled"] for r in results),
        "running_signalled": sum(r["running_signalled"] for r in results),
        "running_pending": sum(r["running_pending"] for r in results),
    }


def clear_finished_batches(modality: str | None = None,
                           batch_id: str | None = None) -> dict:
    """Remove terminal job history without deleting any generated assets."""
    known = {b["id"]: b for b in ledger.load_finished_batches()}
    known.update({b["id"]: b for b in batches.values()})
    selected = []
    for candidate_id, b in known.items():
        if batch_id is not None and candidate_id != batch_id:
            continue
        if modality is not None and b.get("modality") != modality:
            continue
        if any(it.get("state") in ("queued", "running", "cancel_requested", "uncertain")
               for it in b.get("items", [])):
            continue
        selected.append(candidate_id)
    for candidate_id in selected:
        batches.pop(candidate_id, None)
    ledger.delete_batches(selected)
    return {"cleared": len(selected), "batch_ids": selected}


def _reserve_item_webhook(b: dict, item: dict) -> bool:
    """Mark one terminal callback before its durable publication write."""
    if (not b.get("item_webhook") or item.get("_item_notified")
            or item.get("state") not in {"done", "error", "cancelled"}):
        return False
    item["_item_notified"] = True
    return True


async def _post_item_webhook(client: httpx.AsyncClient, b: dict, item: dict,
                             *, reserved: bool = False):
    """POST a single item to the client's per-item webhook the moment it reaches a
    terminal state — lets a client submit ALL scenes as one batch yet still
    receive each result as it finishes (instead of waiting for the whole batch).
    Fires at most once per item; skipped for retry-requeued items."""
    url = b.get("item_webhook")
    if not url or (item.get("_item_notified") and not reserved):
        return
    if item["state"] not in ("done", "error", "cancelled"):
        return
    if reserved:
        if not item.get("_item_notified"):
            return
    else:
        item["_item_notified"] = True
    sid = item.get("studio") or ""
    safe_item = public_item(b, item)
    try:
        await client.post(url, json={
            "batch_id": b["id"], "label": b.get("label"),
            "index": item["index"], "state": item["state"],
            "studio": sid,
            "machine": safe_item.get("machine") or (
                sid.split("@", 1)[1] if "@" in sid else "local"
            ),
            "worker_id": safe_item.get("worker_id"),
            "model_revision": safe_item.get("model_revision"),
            "runtime_revision": safe_item.get("runtime_revision"),
            "artifact_url": hub_artifact_url(b, item),
            "asset_id": item.get("asset_id"),
            "runtime_s": item.get("runtime_s", item.get("duration_s")),
            "duration_s": item.get("runtime_s", item.get("duration_s")),  # legacy alias
            # How long it has been running, for a job that has not finished yet. `runtime_s` is
            # only written when an item completes, so a running generation had no duration at all
            # in this payload - which is why the app could show a clip's length but never how long
            # the clip took to make.
            "run_started": item.get("run_started"),
            "elapsed_s": (round(max(0.0, time.time() - item["run_started"]), 1)
                          if isinstance(item.get("run_started"), (int, float))
                          and item.get("state") == "running" else None),
            "execution_started_at": safe_item.get("execution_started_at"),
            "terminal_result": terminal_result(b, item),
            "error_code": safe_item.get("error_code"),
            "error": safe_item.get("error"),
            "resource_usage": safe_item.get("resource_usage"),
            # running batch tally so the client can show n/N without a poll
            "done": sum(1 for i in b["items"] if i["state"] == "done"),
            "total": len(b["items"]),
        }, timeout=10.0)
    except httpx.HTTPError:
        pass  # client unreachable — the item is still in the batch/poll + ledger


def hub_artifact_url(b: dict, item: dict) -> str | None:
    """Stable Hub-relative identity; never expose a worker-local path."""
    if item.get("state") != "done":
        return None
    return f"/api/hub/jobs/{b['id']}/items/{item['index']}/artifact"


def terminal_result(b: dict, item: dict) -> dict | None:
    """Safe result envelope for customer-facing consumers such as GenStudio."""
    state = item.get("state")
    if state != "done":
        return None
    evidence = {
        "runtime_s": item.get("runtime_s", item.get("duration_s")),
        # Kept only for callers that predate runtime_s. It is runtime, never
        # decoded media duration.
        "duration_s": item.get("runtime_s", item.get("duration_s")),
        "model_revision": item.get("model_revision"),
        "runtime_revision": item.get("runtime_revision"),
        "worker_id": item.get("worker_id"),
        "machine_id": item.get("machine"),
        "resource_usage": item.get("resource_usage"),
    }
    return {
        "status": "succeeded",
        "asset_id": item.get("asset_id"),
        "artifact_url": hub_artifact_url(b, item),
        "media_type": item.get("media_type"),
        "format": item.get("format"),
        "bytes": item.get("bytes"),
        "sha256": item.get("sha256"),
        "audio_duration_s": item.get("audio_duration_s"),
        "audio_duration_ms": item.get("audio_duration_ms"),
        "sample_rate_hz": item.get("sample_rate_hz"),
        "channels": item.get("channels"),
        "voice_revision": item.get("voice_revision"),
        "voice_library_id": item.get("voice_library_id"),
        "preset_speaker": item.get("preset_speaker"),
        "reference_audio_sha256": item.get("reference_audio_sha256"),
        "reference_source_sha256": item.get("reference_source_sha256"),
        "reference_preparation_revision": item.get("reference_preparation_revision"),
        "reference_duration_s": item.get("reference_duration_s"),
        "long_form_strategy": item.get("long_form_strategy"),
        "chunk_total": item.get("chunk_total"),
        "width": item.get("width"),
        "height": item.get("height"),
        "steps": item.get("steps"),
        "resolved_seed": item.get("resolved_seed"),
        **evidence,
    }


def local_artifact_path(item: dict) -> str | None:
    """The item's artifact path, but only when that file is on *this* Mac.

    `public_item` used to strip `artifact_path` from every item, which left the
    app's **Show in Finder** button permanently disabled: `native/StudioAPI.swift`
    decodes `item.artifactPath` and reveals the file it names, so a field that
    never arrived meant a control that could never fire. The owner hit it on the
    machine that generated the file ("even on this machine, i can't click show in
    finder too"), which is the one case where the path is guaranteed to resolve.

    The distinction that matters is *whose* path it is. A path recorded by a
    worker on another Mac names a file on that Mac's disk; publishing it would
    offer the operator a reveal that Finder cannot perform. So the path is
    published for exactly the items this Mac produced, and the other gate — the
    app's own `FileManager.fileExists` check — covers the rest.

    Returns None for an item with no recorded path, an unassigned item, or a
    worker that is no longer registered.
    """
    path = item.get("artifact_path")
    if not isinstance(path, str) or not path:
        return None
    studio_id = item.get("studio")
    if not isinstance(studio_id, str) or not studio_id:
        return None
    studio = next((s for s in _monitor().registry if s.get("id") == studio_id), None)
    if studio is None or not peers.is_local_studio(studio):
        return None
    return path


def public_item(b: dict, item: dict) -> dict:
    """Return a public job item without worker URLs or another Mac's paths.

    `worker_artifact_url` and `worker_accepted_params` never leave the Hub: a
    worker's URL names a port that will belong to nothing after the next restart,
    and its accepted params are a worker-local receipt. `artifact_path` is
    published only through `local_artifact_path`, so it appears only when the
    file it names is on this Mac.
    """
    result = {k: v for k, v in item.items()
              if k not in {"artifact_path", "worker_artifact_url",
                           "worker_accepted_params"}}
    local_path = local_artifact_path(item)
    if local_path is not None:
        result["artifact_path"] = local_path
    result["execution_started_at"] = item.get("execution_started_at")
    if result.get("cancel_error") is not None:
        result["cancel_error"] = _sanitize_public_error(result["cancel_error"])
    if item.get("state") == "done":
        result["artifact_url"] = hub_artifact_url(b, item)
        result["terminal_result"] = terminal_result(b, item)
    elif item.get("state") in {"error", "cancelled"}:
        if result.get("error_code") is not None:
            result["error_code"] = _sanitize_public_error_code(result["error_code"])
        if result.get("error") is not None:
            result["error"] = _sanitize_public_error(result["error"])
    recovery = item.get("recovery")
    if isinstance(recovery, dict):
        safe_recovery = {
            "phase": str(recovery.get("phase") or "uncertain")[:80],
            "reason": _sanitize_public_error(recovery.get("reason") or ""),
        }
        updated_at = recovery.get("updated_at")
        if isinstance(updated_at, (int, float)) and not isinstance(updated_at, bool):
            safe_recovery["updated_at"] = updated_at
        result["recovery"] = safe_recovery
    return result


_PUBLIC_AUTHORIZATION_RE = re.compile(
    r'''(?i)["']?\bauthorization\b["']?\s*[:=]\s*["']?'''
    r'''(?:bearer\s+)?[^"'\s,;}]+["']?'''
)
_PUBLIC_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_PUBLIC_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_PUBLIC_CREDENTIAL_RE = re.compile(
    r'''(?i)["']?\b(x-hub-token|api[_-]?key|token|secret|password)\b'''
    r'''["']?\s*[:=]\s*["']?[^"'\s,;}]+["']?'''
)
_PUBLIC_FILE_URI_RE = re.compile(
    r'''(?i)file:///(?:Users|Volumes|Library|Applications|System|Network|private|tmp|var|opt|home|usr|etc|dev|bin|sbin|cores)/[^\s,;}\]"']+'''
)
_PUBLIC_LOCAL_PATH_RE = re.compile(
    r'''(?<![A-Za-z0-9:])/(?:Users|Volumes|Library|Applications|System|Network|private|tmp|var|opt|home|usr|etc|dev|bin|sbin|cores)/[^\s,;}\]"']+'''
)
_PUBLIC_HOME_PATH_RE = re.compile(r'''(?<![A-Za-z0-9])~/[^\s,;}\]"']+''')


def _sanitize_public_error(value: object) -> str:
    """Bound worker detail and remove common credentials and local Mac paths."""
    text = str(value)
    text = _PUBLIC_AUTHORIZATION_RE.sub("Authorization=[redacted]", text)
    text = _PUBLIC_BEARER_RE.sub("Bearer [redacted]", text)
    text = _PUBLIC_CREDENTIAL_RE.sub(
        lambda match: f"{match.group(1)}=[redacted]", text,
    )
    text = _PUBLIC_FILE_URI_RE.sub("[local path]", text)
    text = _PUBLIC_LOCAL_PATH_RE.sub("[local path]", text)
    text = _PUBLIC_HOME_PATH_RE.sub("[local path]", text)
    return text[:1000]


def _sanitize_public_error_code(value: object) -> str:
    code = str(value).strip()[:128]
    return code if _PUBLIC_ERROR_CODE_RE.fullmatch(code) else "WORKER_TERMINAL_ERROR"


async def _cache_voice_artifact_metadata(client: httpx.AsyncClient, item: dict,
                                         studio: dict, worker_url: str,
                                         expected_bytes, expected_sha256,
                                         job: dict | None = None) -> None:
    """Verify a terminal voice artifact once, then persist its facts.

    One download, verified as it streams (`artifact_metadata.WavStreamVerifier`): the hash and the
    WAV facts are computed on the fly and the item fails verification on a size, hash or decode
    mismatch exactly as before - but nothing is held in memory and the transfer that proves the
    artifact is the only one, so the node is freed the moment it ends.

    A metadata failure never retries an already-completed generation: the
    result remains available but is marked unverified and therefore unbillable.

    The artifact's format decides how. A WAV (the default) is decoded here, because the Hub
    derives its billable duration from the bytes themselves. A compressed artifact (``m4a``,
    delivered by a worker that was asked for ``output_format``) is **streamed through a SHA-256 and
    a byte count** and never held in memory: the worker already decoded the lossless master it
    encoded from and reports the duration, rate and channels of that, which the Hub keeps once the
    delivered bytes are proven to be the ones the worker described.
    """
    reported = (job or {}).get("media_type")
    if isinstance(reported, str) and reported.split(";", 1)[0].strip().lower() != "audio/wav":
        await _verify_compressed_voice_artifact(
            client, item, studio, worker_url, expected_bytes, expected_sha256, job or {})
        return
    try:
        url, headers = studio_request(studio, worker_url)
        verifier = artifact_metadata.WavStreamVerifier()
        # Long-form 40k narration can produce a 100–160 MB PCM WAV. Give an
        # authenticated fleet transfer enough time on slower Tailscale links;
        # generation remains protected by the renewable GenStudio lease.
        if hasattr(client, "stream"):
            async with client.stream("GET", url, headers=headers, timeout=600.0) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(1024 * 1024):
                    verifier.feed(chunk)
        else:   # a client double that only buffers; verified by the same code
            response = await client.get(url, headers=headers, timeout=600.0)
            response.raise_for_status()
            verifier.feed(response.content)
        metadata = verifier.finish()
        if expected_bytes is not None:
            if type(expected_bytes) is not int or expected_bytes <= 0:
                raise ValueError("worker byte count is not a positive integer")
        if expected_bytes is not None and expected_bytes != metadata["bytes"]:
            raise ValueError("worker byte count does not match downloaded artifact")
        if expected_sha256 and str(expected_sha256).lower() != metadata["sha256"]:
            raise ValueError("worker checksum does not match downloaded artifact")
    except (OverflowError, TypeError, ValueError, httpx.HTTPError) as exc:
        item["artifact_metadata_error"] = str(exc)
        return
    item.update(metadata)
    item.pop("artifact_metadata_error", None)


async def _verify_compressed_voice_artifact(client: httpx.AsyncClient, item: dict,
                                            studio: dict, worker_url: str,
                                            expected_bytes, expected_sha256,
                                            job: dict) -> None:
    """Prove a non-WAV voice artifact is the one the worker described, without buffering it."""
    try:
        media_type = artifact_metadata.trusted_media_type(job.get("media_type"), "voice")
        if media_type is None:
            raise ValueError("worker reported an unsupported voice media type")
        if type(expected_bytes) is not int or expected_bytes <= 0:
            raise ValueError("worker byte count is not a positive integer")
        digest = str(expected_sha256 or "").lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("worker checksum is not a SHA-256")
        duration_s = job.get("audio_duration_s")
        rate = job.get("sample_rate_hz")
        channels = job.get("channels")
        if (type(duration_s) not in (int, float) or not math.isfinite(float(duration_s))
                or duration_s <= 0 or type(rate) is not int or rate <= 0
                or type(channels) is not int or channels not in (1, 2)):
            raise ValueError("worker did not report the decoded duration, rate and channels")
        url, headers = studio_request(studio, worker_url)
        hasher = hashlib.sha256()
        received = 0
        head = b""
        async with client.stream("GET", url, headers=headers, timeout=600.0) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(1024 * 1024):
                if not head:
                    head = chunk[:16]
                hasher.update(chunk)
                received += len(chunk)
        if received != expected_bytes:
            raise ValueError("worker byte count does not match downloaded artifact")
        if hasher.hexdigest() != digest:
            raise ValueError("worker checksum does not match downloaded artifact")
        proven = artifact_metadata.sniff_media_type(head)
        if proven not in (media_type, None) and not (
                media_type == "audio/mp4" and proven == "video/mp4"):
            raise ValueError("downloaded artifact is not the media type the worker reported")
    except (OverflowError, TypeError, ValueError, httpx.HTTPError) as exc:
        item["artifact_metadata_error"] = str(exc)
        return
    item.update({
        "media_type": media_type, "format": str(job.get("format") or media_type.split("/")[-1]),
        "bytes": received, "sha256": digest,
        "audio_duration_s": round(float(duration_s), 6),
        "audio_duration_ms": (job.get("audio_duration_ms") if type(job.get("audio_duration_ms")) is int
                              else round(float(duration_s) * 1000)),
        "sample_rate_hz": rate, "channels": channels,
    })
    item.pop("artifact_metadata_error", None)


async def _record_worker_success(client: httpx.AsyncClient, b: dict, item: dict,
                                 studio: dict, job: dict, body: dict,
                                 t_start: float, *, recovery: bool = False):
    """Adopt a completed worker job into the Hub ledger.

    This is deliberately separate from the normal poll loop: if the network
    drops after the worker finishes, recovery can record the already-produced
    artifact instead of submitting a duplicate generation.
    """
    if b.get("modality") == "voice":
        request_id = body.get("client_request_id")
        _validate_voice_worker_job(
            item, job,
            expected_request_id=(request_id if isinstance(request_id, str)
                                 and request_id else None),
        )
        if _voice_recovery_owns(item) and not recovery:
            return
    _persist_worker_execution_started_at(b, item, job)
    if item.get("state") == "done" and item.get("asset_id"):
        return  # terminal polling/recovery is idempotent
    _record_worker_resource_usage(item, job)
    item["artifact_path"] = job.get("output_path")
    # The absolute form is built here because the asset record below fetches from it, and because
    # the Hub is the only thing that should ever hold a worker's address.
    worker_url = (
        f"{base_url(studio)}{job['output_url']}" if job.get("output_url") else None)
    # The job item keeps only the worker-relative path, never the port.
    #
    # Worker ports are assigned at start, so an absolute URL written today names a port that will
    # belong to nothing after the next restart. Measured 2026-09-18: an artifact recorded at
    # 127.0.0.1:65461 could not be read once the worker came back on 127.0.0.1:58107, and
    # `job_artifact` - which requires the stored URL to share the worker's current origin - answered
    # 502 "worker artifact reference is invalid" for a finished job whose file was on disk the whole
    # time. Every Voice and Image artifact became unreadable after a restart.
    item["worker_artifact_url"] = job.get("output_url")
    # Kept for readers that predate `worker_artifact_url`; also relative now, and normalized for
    # clients by public_item(), which returns the stable Hub proxy URL.
    item["artifact_url"] = job.get("output_url")
    item["encoder"] = job.get("encoder")
    _record_worker_identity(item, studio, job)
    item["voice_revision"] = job.get("voice_revision")
    if b["modality"] == "image":
        for field in ("width", "height", "steps"):
            value = job.get(field)
            if type(value) is int and value >= 0:
                item[field] = value
        resolved_seed = job.get("resolved_seed")
        if type(resolved_seed) is int:
            item["resolved_seed"] = resolved_seed
            # Completed public items historically expose ``seed``. Make that
            # existing field reproducible when the worker resolved a random seed.
            item["seed"] = resolved_seed
    if b["modality"] == "video":
        # The clip's own facts, carried the same way Image's are: a caller reading a finished
        # video item should not have to fetch the MP4 to learn how long it is. Only values the
        # worker actually measured are copied.
        for field in ("width", "height", "frames", "frame_rate"):
            value = job.get(field)
            if type(value) is int and value > 0:
                item[field] = value
        seconds = job.get("output_seconds")
        if type(seconds) in (int, float) and math.isfinite(float(seconds)) and seconds > 0:
            item["duration_seconds"] = round(float(seconds), 3)
        if type(job.get("has_audio")) is bool:
            item["has_audio"] = job["has_audio"]
        resolved_seed = job.get("resolved_seed")
        if type(resolved_seed) is int:
            item["resolved_seed"] = resolved_seed
            item["seed"] = resolved_seed
    if b["modality"] == "voice":
        item["voice_library_id"] = body.get("voice_library_id")
        item["preset_speaker"] = body.get("preset_speaker")
        for field in (
            "reference_audio_sha256", "reference_source_sha256",
            "reference_preparation_revision", "reference_duration_s",
            "long_form_strategy", "chunk_total",
        ):
            item[field] = job.get(field)
    runtime = job.get("runtime_s", job.get("generation_seconds", job.get("duration_seconds")))
    try:
        runtime = float(runtime) if runtime is not None else round(time.time() - t_start, 2)
    except (TypeError, ValueError):
        runtime = round(time.time() - t_start, 2)
    item["runtime_s"] = runtime
    item["duration_s"] = runtime  # compatibility: historic duration_s meant runtime
    if b["modality"] == "voice" and worker_url:
        await _cache_voice_artifact_metadata(
            client, item, studio, worker_url, job.get("bytes"), job.get("sha256"), job)
        if _voice_recovery_owns(item) and not recovery:
            return
    else:
        item["sha256"] = job.get("sha256")
        item["bytes"] = job.get("bytes")
        item["media_type"] = artifact_metadata.trusted_media_type(
            job.get("media_type") or job.get("content_type"), b["modality"])
        # Image and Music workers report the decoded artifact facts. Carry the
        # audio ones through so the app can show the clip length and format in
        # history without fetching and re-reading every artifact.
        for field in ("sample_rate_hz", "channels", "audio_duration_s",
                      "audio_duration_ms", "format", "frames"):
            value = job.get(field)
            if value is not None:
                item[field] = value
    # A job that asked for `output_format` and got its native file instead says why, so a caller
    # reading the item sees the fallback rather than inferring it from the media type.
    if isinstance(job.get("delivery_fallback_reason"), str):
        item["delivery_fallback_reason"] = job["delivery_fallback_reason"][:400]
    item["finished_at"] = time.time()
    item["last_progress_at"] = item["finished_at"]
    item["asset_id"] = ledger.record_asset(
        id=item.get("asset_id"),
        source="job", modality=b["modality"], studio=studio["id"],
        machine=studio.get("machine", "local"), model=b["model"],
        seed=job.get("resolved_seed") or item["seed"], prompt=item["prompt"],
        params=body, artifact_path=item["artifact_path"],
        artifact_url=worker_url, batch_id=b["id"],
        item_index=item["index"], duration_s=runtime, runtime_s=runtime,
    )
    # The Hub owns the deletion index. A worker path is only accepted when the
    # packaged descriptor supplied that exact managed root.
    from .. import output_storage
    try:
        output_storage.register_finalized_output(
            machine=str(studio.get("machine") or "local"), studio=str(studio["id"]),
            job_id=str(job.get("id") or item.get("studio_job_id") or ""),
            managed_output_roots=studio.get("managed_output_roots"),
            output_path=job.get("output_path"), generated_at=job.get("generated_at", job.get("finished_at")),
            batch_id=b["id"], item_index=item["index"], attempt=item.get("tries", 0),
            model=b.get("model"), runtime_identity=job.get("runtime_revision"),
            bytes=job.get("bytes"), sha256=job.get("sha256"),
            derivatives=job.get("derivative_output_paths"),
        )
    except Exception as exc:
        # The worker has already finished.  Keep its verified result terminal
        # and unacknowledged rather than dispatching another inference attempt.
        item["output_storage_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    else:
        item.pop("output_storage_error", None)
    # Publish terminal state only after metadata, revision evidence, and the
    # stable asset identity are complete. Pollers must never observe a partial
    # "done" result while the Hub is still finalizing the worker artifact.
    item["state"] = "done"
    # A finished item is 100% done, whatever the last progress poll caught. A six-section long-form
    # job reported 0.788 - the value after section five - because the worker finished between polls
    # and the final update was never read, so a completed artifact displayed as four fifths rendered.
    item["progress"] = 1.0


class _VoiceReceiptError(ValueError):
    pass


def _voice_job_from_response(response: httpx.Response) -> object:
    try:
        payload = response.json()
    except (TypeError, ValueError, AttributeError) as exc:
        raise _VoiceReceiptError("Voice worker returned an invalid JSON receipt") from exc
    if not isinstance(payload, dict):
        raise _VoiceReceiptError("Voice worker receipt was not an object")
    return payload.get("job")


def _mark_voice_receipt_invalid(item: dict) -> None:
    reason = (
        "The Voice worker returned a malformed status or artifact receipt; "
        "reconcile the original attempt before any retry."
    )
    _mark_voice_uncertain(item, reason)
    item["error_code"] = "VOICE_RECEIPT_INVALID"
    item["error"] = reason


def _validate_voice_worker_job(item: dict, job: object, *,
                               expected_request_id: str | None = None) -> None:
    """Reject changed Voice identity or a completion without an artifact reference."""
    if (not isinstance(job, dict) or not isinstance(job.get("id"), str)
            or not job["id"]):
        raise _VoiceReceiptError("Voice worker status did not contain a valid job ID")
    accepted_job_id = item.get("studio_job_id")
    if accepted_job_id is not None and job["id"] != accepted_job_id:
        raise _VoiceReceiptError("Voice worker status changed its accepted job ID")
    state = job.get("state")
    if not isinstance(state, str) or state not in {
        "queued", "running", "cancel_requested", "done", "error", "cancelled"
    }:
        raise _VoiceReceiptError("Voice worker status has an unknown state")
    for field, accepted in (
        ("model_revision", item.get("model_revision")),
        ("runtime_revision", item.get("runtime_revision")),
    ):
        reported = job.get(field)
        if accepted is not None and reported is not None and reported != accepted:
            raise _VoiceReceiptError(f"Voice worker status changed its {field}")
    if expected_request_id is not None:
        params = job.get("params")
        if (not isinstance(params, dict)
                or params.get("client_request_id") != expected_request_id):
            raise _VoiceReceiptError(
                "Voice worker request identity did not match the accepted attempt"
            )
    if state == "done" and not job.get("error"):
        expected_url = f"/api/generate/jobs/{job['id']}/audio"
        if (job.get("output_url") != expected_url
                or not isinstance(job.get("output_path"), str)
                or not job["output_path"]):
            raise _VoiceReceiptError(
                "Voice worker completion lacks a final artifact reference"
            )


# The delivered file's media type a completed worker job may carry. Absent `output_format` is the
# lane's native lossless file (PNG; WAV, or FLAC for YuE2); a job that asked for compression at
# source delivers JPEG or AAC in an MP4 container instead. The receipt still names the file that
# will actually be served, with its own bytes and sha256.
_IMAGE_RECEIPT_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})
_MUSIC_RECEIPT_MEDIA_TYPES = frozenset({"audio/wav", "audio/flac", "audio/mp4"})


def _validate_image_worker_job(item: dict, job: object, *,
                               remote: bool = False) -> None:
    """Reject a changed Image identity or a completion without a PNG or JPEG receipt."""
    if not isinstance(job, dict) or job.get("id") != item.get("studio_job_id"):
        raise ValueError("Image worker status did not match the accepted job ID")
    state = job.get("state")
    if state not in {"queued", "running", "cancel_requested", "done", "error", "cancelled"}:
        raise ValueError("Image worker status has an unknown state")
    for field, accepted in (("model_revision", item.get("model_revision")),
                            ("runtime_revision", item.get("runtime_revision")),
                            ("worker_id", item.get("worker_id")),
                            ("machine_id", item.get("machine"))):
        # A remote worker reports its own local machine id, while the Hub stores the node
        # id it routes through - the two are expected to differ, so only a local worker's
        # machine identity is a change worth failing on.
        if remote and field == "machine_id":
            continue
        if accepted is not None and job.get(field) != accepted:
            raise ValueError(f"Image worker status changed its {field}")
    if state == "done" and not job.get("error"):
        digest = job.get("sha256")
        generated_at = job.get("generated_at", job.get("finished_at"))
        if (type(job.get("bytes")) is not int or job["bytes"] <= 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
                or job.get("output_url") != f"/api/generate/jobs/{job['id']}/image"
                or not isinstance(job.get("output_path"), str) or not job["output_path"]
                or job.get("media_type") not in _IMAGE_RECEIPT_MEDIA_TYPES
                or type(generated_at) not in (int, float)
                or not math.isfinite(float(generated_at)) or generated_at <= 0):
            raise ValueError("Image worker completion lacks a final PNG or JPEG receipt")


def _validate_music_worker_job(item: dict, job: object, *,
                               remote: bool = False) -> None:
    """Reject a changed Music identity or a completion without a WAV, FLAC or M4A receipt."""
    if not isinstance(job, dict) or job.get("id") != item.get("studio_job_id"):
        raise ValueError("Music worker status did not match the accepted job ID")
    state = job.get("state")
    if state not in {"queued", "running", "cancel_requested", "done", "error", "cancelled"}:
        raise ValueError("Music worker status has an unknown state")
    for field, accepted in (("model_revision", item.get("model_revision")),
                            ("runtime_revision", item.get("runtime_revision")),
                            ("worker_id", item.get("worker_id")),
                            ("machine_id", item.get("machine"))):
        # See _validate_image_worker_job: a remote worker's own machine id is not the
        # node id the Hub routes through.
        if remote and field == "machine_id":
            continue
        if accepted is not None and job.get(field) != accepted:
            raise ValueError(f"Music worker status changed its {field}")
    if state == "done" and not job.get("error"):
        digest = job.get("sha256")
        generated_at = job.get("generated_at", job.get("finished_at"))
        if (type(job.get("bytes")) is not int or job["bytes"] <= 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
                or job.get("output_url") != f"/api/generate/jobs/{job['id']}/audio"
                or not isinstance(job.get("output_path"), str) or not job["output_path"]
                or job.get("media_type") not in _MUSIC_RECEIPT_MEDIA_TYPES
                or type(generated_at) not in (int, float)
                or not math.isfinite(float(generated_at)) or generated_at <= 0):
            raise ValueError("Music worker completion lacks a final audio receipt")


def _validate_video_worker_job(item: dict, job: object, *,
                               remote: bool = False) -> None:
    """Reject a changed Video identity or a completion without an MP4 receipt.

    The same rule Image and Music already get, for the same reason: a durable-async worker's
    acceptance is a promise, and its completion must carry enough evidence that the artifact a
    caller fetches is the one the worker says it produced. Video adds its own fields because the
    clip has a duration and a frame count that a PNG does not.
    """
    if not isinstance(job, dict) or job.get("id") != item.get("studio_job_id"):
        raise ValueError("Video worker status did not match the accepted job ID")
    state = job.get("state")
    if state not in {"queued", "running", "cancel_requested", "done", "error", "cancelled"}:
        raise ValueError("Video worker status has an unknown state")
    for field, accepted in (("model_revision", item.get("model_revision")),
                            ("runtime_revision", item.get("runtime_revision")),
                            ("worker_id", item.get("worker_id")),
                            ("machine_id", item.get("machine"))):
        # See _validate_image_worker_job: a remote worker's own machine id is not the node id.
        if remote and field == "machine_id":
            continue
        if accepted is not None and job.get(field) != accepted:
            raise ValueError(f"Video worker status changed its {field}")
    if state == "done" and not job.get("error"):
        digest = job.get("sha256")
        generated_at = job.get("generated_at", job.get("finished_at"))
        if (type(job.get("bytes")) is not int or job["bytes"] <= 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
                or job.get("output_url") != f"/api/generate/jobs/{job['id']}/video"
                or not isinstance(job.get("output_path"), str) or not job["output_path"]
                or job.get("media_type") != "video/mp4"
                or type(generated_at) not in (int, float)
                or not math.isfinite(float(generated_at)) or generated_at <= 0):
            raise ValueError("Video worker completion lacks a final MP4 receipt")
        for field in ("width", "height", "frames"):
            value = job.get(field)
            if type(value) is not int or value <= 0:
                raise ValueError(f"Video worker completion lacks a positive {field}")
        seconds = job.get("output_seconds")
        if type(seconds) not in (int, float) or not math.isfinite(float(seconds)) or seconds <= 0:
            raise ValueError("Video worker completion lacks a positive clip duration")


def _validate_durable_worker_job(modality: str, item: dict, job: object, *,
                                 remote: bool = False) -> None:
    if modality == "image":
        _validate_image_worker_job(item, job, remote=remote)
    elif modality == "music":
        _validate_music_worker_job(item, job, remote=remote)
    elif modality == "video":
        _validate_video_worker_job(item, job, remote=remote)


async def _recover_worker_job(client, b: dict, item: dict, studio: dict,
                              body: dict, t_start: float) -> bool | dict:
    """Reconcile a worker job after a transport failure.

    A generation request is not safely retryable once the worker has accepted
    it. Keep the Hub lease while reconnecting and poll the original job for a
    bounded window. Return True after adopting success, the exact terminal job
    for failure classification, or False when its outcome remains unknown.
    """
    job_id = item.get("studio_job_id")
    if not job_id or b.get("cancelled"):
        return False
    deadline = time.monotonic() + RECOVERY_WINDOW_S
    delay = 1.0
    while time.monotonic() < deadline:
        if _expire_genstudio_batch(b):
            await _signal_worker_cancel(client, item)
            return False
        try:
            url, headers = studio_request(studio, f"/api/generate/jobs/{job_id}")
            jr = await client.get(
                url, headers=headers, timeout=10.0)
            if jr.status_code >= 400:
                return False  # 404/4xx means the worker no longer has the job
            if b.get("modality") == "voice":
                job = _voice_job_from_response(jr)
                _validate_voice_worker_job(
                    item, job,
                    expected_request_id=str(body.get("client_request_id") or ""),
                )
            else:
                job = jr.json().get("job") or {}
                if not isinstance(job, dict) or job.get("id") != job_id:
                    return False
                if b.get("modality") in DURABLE_ASYNC_MODALITIES:
                    _validate_durable_worker_job(
                        b["modality"], item, job,
                        remote=not peers.is_local_studio(studio))
            if _voice_recovery_owns(item):
                return False
            _persist_worker_execution_started_at(b, item, job)
            _record_worker_resource_usage(item, job)
            state = job.get("state")
            if state in ("queued", "running", "cancel_requested"):
                _record_worker_progress(item, job.get("progress"),
                                        job.get("progress_stage"))
                await asyncio.sleep(POLL_S)
                continue
            if state == "done" and not job.get("error"):
                await _record_worker_success(client, b, item, studio, job, body, t_start)
                if _voice_recovery_owns(item):
                    return False
                _mark_machine_success(studio)
                return True
            return job if state in ("error", "cancelled") else False
        except _VoiceReceiptError:
            _mark_voice_receipt_invalid(item)
            return False
        except Exception:
            # Tailscale/Wi-Fi and a busy worker can briefly drop the HTTP
            # connection. Back off while retaining the same worker lease.
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
    return False


async def _reconcile_async_item(batch_id: str, item_index: int, modality: str,
                                client: httpx.AsyncClient | None = None) -> dict:
    """Check only the original accepted async job; never submit another one."""
    if modality not in DURABLE_ASYNC_MODALITIES:
        raise ValueError("unsupported durable modality")
    label = modality.title()
    batch = batches.get(batch_id) or ledger.load_batch(batch_id)
    if batch is None or batch.get("modality") != modality:
        raise ValueError(f"unknown {label} batch")
    item = next((row for row in batch.get("items", [])
                 if row.get("index") == item_index), None)
    if item is None:
        raise ValueError(f"unknown {label} item")
    if item.get("state") != "uncertain":
        raise ValueError(f"{label} item is not uncertain")
    batches[batch_id] = batch

    def result(reconciled: bool) -> dict:
        return {"batch_id": batch_id, "clientRequestId": batch.get("client_request_id"),
                "reconciled": reconciled, "item": public_item(batch, item)}

    def pending(reason: str) -> dict:
        recovery = dict(item.get("recovery") or {})
        recovery.update(phase="manual_action_required", reason=reason,
                        updated_at=time.time())
        item["recovery"] = recovery
        item["error"] = reason
        ledger.save_batch(batch)
        return result(False)

    job_id = item.get("studio_job_id")
    worker_id = item.get("studio")
    machine = item.get("machine")
    if not all(isinstance(value, str) and value for value in (job_id, worker_id, machine)):
        return pending(f"The original {label} worker identity is incomplete; manual reconciliation is required.")
    studio = next((row for row in _monitor().registry
                   if row.get("id") == worker_id and row.get("machine") == machine), None)
    if studio is None:
        return pending(f"The original {label} worker and machine are unavailable; manual reconciliation is required.")
    if any(not isinstance(item.get(field), str) or not item[field]
           for field in ("model_revision", "runtime_revision", "worker_id")):
        return pending(f"The accepted {label} model, runtime or worker revision is missing.")
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(follow_redirects=False)
    try:
        if batch.get("cancelled") and not item.get("cancel_signal_sent_at"):
            await _signal_worker_cancel(client, item)
            ledger.save_batch(batch)
        try:
            url, headers = studio_request(studio, f"/api/generate/jobs/{job_id}")
            response = await client.get(url, headers=headers, timeout=15.0)
            if response.status_code >= 400:
                return pending(f"The original {label} job could not be read; its outcome remains uncertain.")
            job = response.json().get("job")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return pending(f"The original {label} worker response was unavailable or malformed.")
        if not isinstance(job, dict):
            return pending(f"The original {label} worker response was malformed.")
        try:
            _validate_durable_worker_job(modality, item, job)
        except ValueError:
            return pending(f"The original {label} worker status or final artifact receipt was malformed.")
        params = job.get("params")
        body = dict(batch.get("shared_params") or {})
        body.update(item.get("params") or {})
        body.update(repo=batch["model"], prompt=item["prompt"])
        if item.get("seed") is not None and "seed" not in body:
            body["seed"] = item["seed"]
        accepted = {
            "id": job_id, "model_revision": item.get("model_revision"),
            "runtime_revision": item.get("runtime_revision"),
            "worker_id": item.get("worker_id"), "machine_id": machine,
        }
        accepted_params = item.get("worker_accepted_params")
        if modality == "music" and not isinstance(accepted_params, dict):
            # Receipts written before Music retained the worker's normalized
            # request cannot prove that a same-id job has the same inputs.
            # Keep those legacy attempts fenced for manual reconciliation.
            return pending(
                "The accepted Music canonical request settings are missing; "
                "manual reconciliation is required."
            )
        # A worker that predates `output_format` never echoes it, and the dispatcher withheld it
        # from such a worker, so it is not part of the identity being compared for image/video.
        params_match = (
            params == accepted_params if modality == "music"
            else isinstance(params, dict)
            and all(params.get(key) == value for key, value in body.items()
                    if key != "output_format")
        )
        if (any(job.get(key) != value for key, value in accepted.items()
                if value is not None) or not params_match):
            return pending(f"Original {label} job identity or request settings did not match the accepted attempt.")
        state = job.get("state")
        if state in {"queued", "running", "cancel_requested"}:
            recovery = dict(item.get("recovery") or {})
            recovery.update(phase="health_wait", reason=f"The original {label} job is still active.",
                            updated_at=time.time())
            item["recovery"] = recovery
            _record_worker_progress(item, job.get("progress"),
                                    job.get("progress_stage"))
            ledger.save_batch(batch)
            return result(False)
        code = str(job.get("error_code") or "")
        if code in {"JOB_PERSISTENCE_UNCERTAIN", "WORKER_RESTART_UNCERTAIN"}:
            return pending(f"The original {label} worker cannot prove a final receipt; manual reconciliation is required.")
        if state == "done" and not job.get("error"):
            try:
                await _record_worker_success(
                    client, batch, item, studio, job, body,
                    float(item.get("run_started") or batch["created_at"]), recovery=True,
                )
            except Exception:
                return pending(f"The original {label} output could not be verified by the Hub.")
            if item.get("state") != "done":
                return pending(f"The original {label} output could not be adopted by the Hub.")
            item["error"] = None
            item.pop("error_code", None)
        elif state in {"error", "cancelled"}:
            _record_worker_failure(item, studio, job,
                                   float(item.get("run_started") or batch["created_at"]))
            item.update(state=state, error=str(job.get("error") or state)[:500],
                        error_code=code or "WORKER_TERMINAL_ERROR",
                        finished_at=time.time(), retry_at=None)
        else:
            return pending(f"The original {label} worker returned an unknown job state.")
        item["recovery"] = {"phase": "reconciled",
                            "reason": f"The original {label} worker returned a verified terminal result.",
                            "updated_at": time.time()}
        publication_state = {
            key: batch[key] for key in ("done_notified", "finished_at")
            if key in batch
        }
        item_webhook_reserved = _reserve_item_webhook(batch, item)
        try:
            await _maybe_finish(client, batch)
        except Exception:
            _retain_async_terminal_persistence_uncertainty(
                batch, item, studio, publication_state
            )
            raise
        if item_webhook_reserved:
            await _post_item_webhook(client, batch, item, reserved=True)
        _wakeup.set()
        return result(True)
    finally:
        if owns_client:
            await client.aclose()


async def reconcile_image_item(batch_id: str, item_index: int,
                               client: httpx.AsyncClient | None = None) -> dict:
    return await _reconcile_async_item(batch_id, item_index, "image", client)


async def reconcile_music_item(batch_id: str, item_index: int,
                               client: httpx.AsyncClient | None = None) -> dict:
    return await _reconcile_async_item(batch_id, item_index, "music", client)


async def reconcile_video_item(batch_id: str, item_index: int,
                               client: httpx.AsyncClient | None = None) -> dict:
    """Read and adopt only the original accepted Video job after Hub recovery.

    The reconciler itself is modality-agnostic (`_reconcile_async_item`); what was missing was
    this entry and the route branch that reaches it. Without them a video item fell through to
    the **Image** reconciler, which answered `unknown Image batch` for a video batch — measured
    on 2026-09-26, on the first uncertain video item this Hub ever held.
    """
    return await _reconcile_async_item(batch_id, item_index, "video", client)


async def reconcile_voice_item(batch_id: str, item_index: int,
                               client: httpx.AsyncClient | None = None) -> dict:
    """Read and adopt only the original accepted Voice job after Hub recovery."""
    batch, item = voice_recovery_item(batch_id, item_index)

    def result(reconciled: bool) -> dict:
        return {
            "batch_id": batch_id,
            "clientRequestId": batch.get("client_request_id"),
            "reconciled": reconciled,
            "item": public_item(batch, item),
        }

    def pending(reason: str) -> dict:
        set_voice_recovery(item, "manual_action_required", reason)
        item["error"] = reason
        ledger.save_batch(batch)
        return result(False)

    worker_id = item.get("studio")
    machine = item.get("machine")
    studio = next((row for row in _monitor().registry
                   if row.get("id") == worker_id
                   and (machine is None or row.get("machine") == machine)), None)
    if studio is None:
        return pending(
            "The original Voice Studio is unavailable; its outcome remains uncertain."
        )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(follow_redirects=False)
    try:
        job_id = item["studio_job_id"]
        if batch.get("cancelled") and not item.get("cancel_signal_sent_at"):
            await _signal_worker_cancel(client, item)
            ledger.save_batch(batch)
        try:
            url, headers = studio_request(studio, f"/api/generate/jobs/{job_id}")
            response = await client.get(url, headers=headers, timeout=15.0)
            if response.status_code >= 400:
                return pending(
                    "The original Voice job could not be read; its outcome remains uncertain."
                )
            job = _voice_job_from_response(response)
        except httpx.HTTPError:
            return pending(
                "The original Voice worker response was unavailable."
            )
        except _VoiceReceiptError:
            return pending(
                "The original Voice worker response was malformed."
            )
        expected_request_id = f"studiohub:{batch['id']}:{item['index']}"
        try:
            _validate_voice_worker_job(
                item, job, expected_request_id=expected_request_id,
            )
        except _VoiceReceiptError:
            return pending(
                "The original Voice worker status or artifact receipt was malformed."
            )
        state = job["state"]
        if state in {"queued", "running", "cancel_requested"}:
            set_voice_recovery(
                item, "health_wait", "The original Voice job is still active."
            )
            _record_worker_progress(item, job.get("progress"),
                                    job.get("progress_stage"))
            ledger.save_batch(batch)
            return result(False)

        body = dict(batch.get("shared_params") or {})
        body.update(item.get("params") or {})
        body.update(repo=batch["model"], text=item["prompt"],
                    client_request_id=expected_request_id)
        if item.get("seed") is not None:
            body["seed"] = item["seed"]
        if state == "done":
            if job.get("error"):
                return pending(
                    "The original Voice worker returned a contradictory terminal status."
                )
            try:
                await _record_worker_success(
                    client, batch, item, studio, job, body,
                    float(item.get("run_started") or batch["created_at"]),
                    recovery=True,
                )
            except Exception:
                return pending(
                    "The original Voice output could not be verified by the Hub."
                )
            if item.get("state") != "done":
                return pending(
                    "The original Voice output could not be adopted by the Hub."
                )
        else:
            _record_worker_failure(
                item, studio, job,
                float(item.get("run_started") or batch["created_at"]),
            )
            item.update(
                state=state,
                error=str(job.get("error") or state)[:500],
                error_code=str(job.get("error_code") or "WORKER_TERMINAL_ERROR")[:128],
                finished_at=time.time(),
                retry_at=None,
            )
        set_voice_recovery(
            item, "reconciled",
            "The original Voice worker returned a verified terminal result.",
        )
        ledger.save_batch(batch)
        await _post_item_webhook(client, batch, item)
        await _maybe_finish(client, batch)
        _wakeup.set()
        return result(True)
    finally:
        if owns_client:
            await client.aclose()


def discard_uncertain_item(batch_id: str, item_index: int, *,
                           reason: str | None = None) -> dict:
    """Let an operator abandon an uncertain attempt for good.

    An uncertain item keeps its physical Mac reserved for reconciliation, because the accepted
    worker attempt may still be running - and that uncertainty can never resolve by itself when
    the worker is gone. Left alone it pins the machine and every later job waits behind it with
    no reason shown. Discarding is the operator's explicit decision to stop waiting: the attempt
    is recorded as cancelled and its worker and recovery identity are cleared, so a restart,
    monitor pass or reconcile cannot adopt it back.

    This is deliberately separate from cancellation and reconciliation, which keep a live worker
    fenced. Only an item that is already uncertain can be discarded.
    """
    batch = batches.get(batch_id) or ledger.load_batch(batch_id)
    if batch is None:
        raise ValueError("unknown batch")
    item = next((row for row in batch.get("items", [])
                 if row.get("index") == item_index), None)
    if item is None:
        raise ValueError("unknown job item")
    if item.get("state") != "uncertain":
        raise ValueError("Discard is available only for an uncertain job")
    reason = reason or (
        "The operator discarded this uncertain attempt. It was abandoned rather than "
        "reconciled, so any result the original worker may still produce is ignored."
    )
    item["state"] = "cancelled"
    item["error_code"] = "DISCARDED_UNCERTAIN"
    item["error"] = reason
    item["retry_at"] = None
    item["finished_at"] = time.time()
    # The worker attempt and its recovery trail are removed on purpose: nothing may reconcile an
    # attempt the operator has abandoned.
    for field in ("studio", "studio_job_id", "worker_id", "worker_artifact_url", "machine",
                  "recovery", "model_revision", "runtime_revision", "worker_accepted_params",
                  "cancel_signal_sent_at", "cancel_error"):
        item.pop(field, None)
    batches[batch_id] = batch
    ledger.save_batch(batch)
    _wakeup.set()
    return {"ok": True, "batch_id": batch_id, "item_index": item_index}


def retry_failed_item(batch_id: str, item_index: int) -> dict:
    """Put one failed item back in the queue for the dispatcher to run again.

    Only an item whose last attempt ended in ``error`` can be retried: a queued,
    running or done item is not a failed attempt, and an ``uncertain`` item may
    still have a worker producing a result, so its outcome has to be discarded
    before it can be retried.  The item's prompt and params are preserved
    untouched; only the failed attempt's bookkeeping is cleared.
    """
    batch = batches.get(batch_id) or ledger.load_batch(batch_id)
    if batch is None:
        raise ValueError("unknown batch")
    if batch.get("cancelled"):
        raise ValueError("batch is cancelled")
    item = next((row for row in batch.get("items", [])
                 if row.get("index") == item_index), None)
    if item is None:
        raise ValueError("unknown job item")
    state = str(item.get("state") or "")
    if state == "uncertain":
        raise ValueError(
            "item is uncertain and must be discarded before it can be retried"
        )
    if state != "error":
        raise ValueError(
            f"item is not in a failed state (state is {state or 'unknown'})"
        )
    item["state"] = "queued"
    item["tries"] = 0
    item["retry_at"] = None
    for field in ("error", "error_code", "finished_at", "studio", "studio_job_id",
                  "worker_id", "worker_artifact_url", "machine", "recovery",
                  "model_revision", "runtime_revision", "worker_accepted_params",
                  "cancel_signal_sent_at", "cancel_error", "artifact_path",
                  "artifact_url", "asset_id", "progress", "progress_stage",
                  "last_progress_at"):
        item.pop(field, None)
    batch["finished_at"] = None
    batch["done_notified"] = False
    batches[batch_id] = batch
    ledger.save_batch(batch)
    _wakeup.set()
    return {
        "ok": True, "batch_id": batch_id, "item_index": item_index,
        "state": "queued",
    }


# An uncertain attempt reserves its Mac because it may still be running. That is
# right for a while, but an attempt that has been silent for this long is not coming
# back, and leaving it reserved blocks every later job with no reason shown. The
# operator can discard sooner by hand; this sweep is the walk-away backstop.
UNCERTAIN_AUTO_DISCARD_SECONDS = float(
    os.environ.get("STUDIOHUB_UNCERTAIN_DISCARD_SECONDS", 1800.0))


def discard_stale_uncertain(*, now: float | None = None,
                            max_age: float | None = None) -> list[str]:
    """Release Macs held by uncertain attempts nobody is coming back for.

    Returns the ``batch:item`` identifiers it discarded, so a caller can log them.
    """
    limit = UNCERTAIN_AUTO_DISCARD_SECONDS if max_age is None else max_age
    moment = time.time() if now is None else now
    discarded: list[str] = []
    for batch in list(batches.values()):
        for item in batch.get("items") or []:
            if item.get("state") != "uncertain":
                continue
            stamps = [item.get(key) for key in
                      ("last_progress_at", "finished_at", "execution_started_at")]
            newest = max((value for value in stamps
                          if isinstance(value, (int, float)) and not isinstance(value, bool)),
                         default=None)
            if newest is None:
                # Only an older record with no item stamps at all falls back to the
                # batch's own creation time.
                newest = batch.get("created_at")
            if newest is None or moment - newest < limit:
                continue
            try:
                discard_uncertain_item(batch["id"], item["index"], reason=(
                    "The attempt stayed uncertain past the automatic reconciliation "
                    "window, so the machine was released for later work."))
            except ValueError:
                continue
            discarded.append(f"{batch['id']}:{item['index']}")
    return discarded


async def _maybe_finish(client: httpx.AsyncClient, b: dict):
    """Persist state; when the batch just reached a terminal state, alert on any
    failures and fire the client's webhook (Story Studio et al) once."""
    states = {i["state"] for i in b["items"]}
    terminal = not states & {"queued", "running", "cancel_requested", "uncertain"}
    publish = terminal and not b.get("done_notified")
    if publish:
        if not b.get("finished_at"):
            b["finished_at"] = time.time()
        b["done_notified"] = True
    # One durable write publishes both the terminal item and final batch facts.
    # Webhooks below must never outrun this boundary.
    ledger.save_batch(b)
    if not publish:
        return
    summary = batch_summary(b)
    if summary["error"]:
        from . import alerts
        alerts.emit("batch_failed",
                    f"batch {b['id']} ({b['modality']}/{b['model']}): "
                    f"{summary['error']}/{summary['total']} items failed",
                    {"batch_id": b["id"], **{k: summary[k] for k in
                                             ("done", "error", "total")}})
    if b.get("webhook") and not b.get("webhook_sent"):
        b["webhook_sent"] = True
        try:
            await client.post(b["webhook"], json={
                **summary,
                "items": [public_item(b, it) for it in b["items"]],
            }, timeout=10.0)
        except httpx.HTTPError:
            pass  # client unreachable — batch state is still queryable


def _retain_async_terminal_persistence_uncertainty(
    batch: dict, item: dict, studio: dict,
    publication_state: dict | None = None,
) -> bool:
    """Keep a terminal async worker receipt fenced when Hub publication fails."""
    modality = batch.get("modality")
    if (modality not in DURABLE_ASYNC_MODALITIES
            or item.get("state") not in {"done", "error", "cancelled"}
            or not item.get("studio_job_id")):
        return False
    for key in ("done_notified", "finished_at"):
        batch.pop(key, None)
    batch.update(publication_state or {})
    item.pop("_item_notified", None)
    _mark_async_uncertain(
        item, studio, modality,
        f"The {modality.title()} worker returned a terminal result, but the Hub "
        "could not persist it; reconcile the original worker receipt before retrying.",
        "JOB_PERSISTENCE_UNCERTAIN",
    )
    try:
        ledger.save_batch(batch)
    except Exception:
        # The live fence remains authoritative until storage recovers; a
        # restart falls back to the last durable nonterminal receipt.
        pass
    return True


def _eligible_studios(modality: str, routing: str,
                      model: str | None = None) -> list[dict]:
    mon = _monitor()
    out = []
    leased_machines = busy_machines()
    for s in mon.registry:
        # A pin is the operator's instruction, so it beats the automatic choice: a Mac that
        # has the model is still skipped when another Mac was named.
        if not routing_rule.allows(s, routing):
            continue
        machine = s.get("machine", "local")
        # A remote render deliberately keeps the Hub Mac as the control plane.
        # It waits for an external Render Studio rather than quietly consuming
        # the Hub machine's CPU / Media Engine as a fallback.
        if routing == "remote" and peers.is_local_studio(s):
            continue
        if (s["modality"] != modality or s["id"] in _busy
                or s["id"] in _maintenance or machine in leased_machines
                or _machine_reserved_until.get(machine, 0) > time.time()):
            continue
        if not peers.dispatch_ready(s):
            continue
        # a machine the operator has disabled stays monitored but takes no jobs
        if not machine_enabled(s.get("machine", "local")):
            continue
        # App-specific pauses are scheduler-only: current work finishes and the
        # Studio remains online for monitoring, lifecycle control, and updates.
        if not studio_enabled(machine, s["id"]):
            continue
        if _machine_blocked(machine):
            continue
        if mon.status.get(s["id"], {}).get("status") == "up":
            out.append(s)
    if modality == "render":
        # Render workers publish a normalized score in /api/health. M4 16 GB
        # machines rank above older/smaller Macs, while every healthy worker
        # remains an eligible fallback.
        out.sort(key=lambda s: (
            -float((mon.status.get(s["id"], {}).get("health") or {})
                   .get("render_score", 0)),
            s["id"],
        ))
    else:
        # Smallest capable machine first, for every lane. Capability is the model's own published
        # floor and admission enforces it; this only orders the machines that can all run the job,
        # so the few 16 GB+ Macs stay free for work that has nowhere else to go. The rule belongs
        # to the model, not the modality - see `_placement_key`.
        out.sort(key=_placement_key(modality, model))
    return out


def _shared_voice_allows_studio(batch: dict, item: dict, studio: dict) -> bool:
    """Keep Hub-owned clones on workers where their stable id is synchronized.

    Unknown ids retain the legacy behavior because they may be direct-only
    Voice Studio library entries. Hub-owned ids with no successful targets
    wait in queue until the background synchronizer heals one worker.
    """
    if batch.get("modality") != "voice":
        return True
    params = dict(batch.get("shared_params") or {})
    params.update(item.get("params") or {})
    voice_id = str(params.get("voice_library_id") or "").strip()
    if not voice_id:
        return True
    synced = shared_voices.synced_studio_ids(voice_id)
    return synced is None or studio.get("id") in synced


def _version_tuple(value: object) -> tuple[int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(map(int, match.groups())) if match else None


def _no_candidate_note(batch: dict, queued: list[dict], studio: dict) -> str:
    """Why this studio took no item this pass, in the operator's words.

    Two different waits end at the same line and used to share one sentence. An
    item held back because its shared voice is not synchronized on this Mac, and
    an item held back because this Mac is inside a temporary avoid window - a
    reservation another queue holds, or a recent failure - are unrelated, and
    only the first one is about Voice. `_shared_voice_allows_studio` returns True
    for every non-voice modality, so for an image batch *only* the avoid window
    can reach this point, and it was still reported as "Waiting for a Voice
    Studio". That was a latent wording bug; the MACHINE_BUSY deferral makes the
    avoid window the ordinary outcome of a pool dispatch, so it has to be named
    for what it is.
    """
    if any(not _shared_voice_allows_studio(batch, item, studio) for item in queued):
        return ("Waiting for a Voice Studio where the selected "
                "shared voice is synchronized")
    return ("Waiting for a free machine: every eligible one is temporarily "
            "avoided (reserved by other work, or recently failed)")


def _supports_genstudio_voice_evidence(batch: dict, studio: dict) -> bool:
    """Keep customer jobs off workers that cannot report immutable revisions."""
    if (
        batch.get("modality") != "voice"
        or not str(batch.get("label") or "").startswith("genstudio-kh:")
    ):
        return True
    status = _monitor().status.get(studio.get("id"), {})
    version = status.get("app_version") or (status.get("health") or {}).get(
        "app_version"
    )
    parsed = _version_tuple(version)
    return parsed is not None and parsed >= _GENSTUDIO_VOICE_EVIDENCE_VERSION


def _queued_batches() -> list[tuple[float, dict]]:
    """Queued work as ``(pool, batch)``, scarcest first.

    A batch's *pool* is how many distinct Macs could run its model at all (`pool_machines`); a Mac
    that frees up is offered the smallest pool first, so the few Macs that can run a big model are
    not spent on work the many small ones can do. Equal pools go first come first served
    (``created_at``) - the dispatch loop puts the model a Mac already holds ahead of that, per Mac.
    A pool of zero (nobody can run it right now, or its catalog is unknown) sorts last and holds
    nothing up. Episode renders stay ahead of everything.

    This changes only the next dispatch decision: running work is never preempted, and every Mac
    with eligible queued work is offered something, so nothing is held idle.
    """
    pools: dict[tuple, float] = {}

    def pool_of(batch: dict) -> float:
        if batch["modality"] == "render":
            return 0
        key = (batch["modality"], batch.get("model") or "",
               batch.get("routing", routing_rule.POOL))
        if key not in pools:
            pools[key] = _pool_size(*key)
        return pools[key]

    return sorted(
        ((pool_of(batch), batch) for batch in batches.values()
         if any(item.get("state") == "queued" for item in batch.get("items", []))),
        key=lambda row: (row[0], row[1].get("created_at", 0)),
    )


def evict_finished_batches(*, keep_recent: int = 20, older_than_s: float = 900.0) -> int:
    """Drop finished batches from memory. The ledger keeps them; nothing an operator sees is lost.

    `batches` holds every batch this process has been told about and nothing ever removed one, so a
    Hub that runs for weeks grew without bound - and every status read, every listing and every
    stalled-job check walked the lot. The durable record is the ledger
    (`ledger.load_finished_batches`), and the listings merge ledger + memory, so the in-memory copy
    of a finished job is pure cost once it is old. The most recent `keep_recent` terminal batches
    stay, because the per-model ETA is measured from them.
    """
    terminal = {"done", "error", "cancelled"}
    finished: list[tuple[float, str]] = []
    for batch_id, batch in batches.items():
        items = batch.get("items") or []
        if not items or any(item.get("state") not in terminal for item in items):
            continue
        stamp = batch.get("finished_at") or batch.get("created_at") or 0
        finished.append((float(stamp), batch_id))
    finished.sort(reverse=True)
    now = time.time()
    removed = 0
    for stamp, batch_id in finished[keep_recent:]:
        if now - stamp < older_than_s:
            continue
        batches.pop(batch_id, None)
        removed += 1
    return removed


async def _dispatch_loop():
    """The scheduler: match queued items to free studios, forever."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=60, write=30, pool=5))
    last_sweep = 0.0
    while True:
        try:
            sweep_now = time.time()
            if sweep_now - last_sweep >= 60:
                last_sweep = sweep_now
                evict_finished_batches()
                # An uncertain attempt that nobody reconciles must not pin the Mac
                # forever; release it so later work can run.
                discard_stale_uncertain(now=sweep_now)
            assigned = False
            for batch in list(batches.values()):
                if (batch.get("modality") in DURABLE_ASYNC_MODALITIES
                        and batch.get("cancelled")):
                    for item in batch.get("items") or []:
                        if (item.get("state") == "uncertain"
                                and item.get("studio_job_id")
                                and not item.get("cancel_signal_sent_at")):
                            await _signal_worker_cancel(client, item)
                            ledger.save_batch(batch)
            # Scarcest pool first; within one pool, a Mac that already holds a model is offered that
            # model's batch before the others (a sticky pass), then first come first served.
            work = []
            for _pool, rows in itertools.groupby(_queued_batches(), key=lambda row: row[0]):
                group = [row[1] for row in rows]
                work += [(b, _pool, True) for b in group if b.get("model") in _last_model.values()]
                work += [(b, _pool, False) for b in group]
            for b, pool, sticky in work:
                if _expire_genstudio_batch(b):
                    ledger.save_batch(b)
                    continue
                if b["cancelled"]:
                    continue
                now = time.time()
                queued = [i for i in b["items"] if i["state"] == "queued"
                          and (i.get("retry_at") or 0) <= now]
                if not queued:
                    continue
                refusal = admission_refusal(b)
                if refusal:
                    b["governor_note"] = str(refusal.get("detail") or "model is not ready")
                    ledger.save_batch(b)
                    continue
                eligible = _eligible_studios(b["modality"], b["routing"], b.get("model"))
                evidence_eligible = [
                    studio
                    for studio in eligible
                    if _supports_genstudio_voice_evidence(b, studio)
                ]
                if eligible and not evidence_eligible:
                    b["governor_note"] = (
                        "Waiting for Voice Studio 1.20.13 or newer so GenStudio "
                        "receives immutable model and voice revision evidence"
                    )
                eligible = evidence_eligible
                if not eligible and b.get("routing") == "remote":
                    b["governor_note"] = (
                        "Waiting for an online remote worker; this Hub Mac is intentionally excluded"
                    )
                for studio in eligible:
                    if not queued:
                        break
                    if sticky and _last_model.get(studio["id"]) != b.get("model"):
                        continue
                    # Another lane's strictly scarcer job that can start on this Mac right now
                    # goes first; it is an order, not a reservation - nothing waits on an idle Mac.
                    if await scarcer_work_waiting(
                            studio.get("machine", "local"), pool, "broker"):
                        continue
                    compatible_index = next(
                        (index for index, candidate in enumerate(queued)
                         if _shared_voice_allows_studio(b, candidate, studio)
                         and _item_allows_studio(candidate, studio, now)),
                        None,
                    )
                    if compatible_index is None:
                        b["governor_note"] = _no_candidate_note(b, queued, studio)
                        continue
                    # ── model-aware dispatch (heterogeneous machines) ──
                    # A studio only gets work for models it actually has. This
                    # is what lets a 3-model Mac and a 1-model Mac share pools.
                    entry = await _catalog_entry(studio, b["model"])
                    if entry is None:
                        b["governor_note"] = (
                            f"'{b['model']}' not in {studio['id']}'s catalog")
                        continue
                    if not _catalog_matches_genstudio_revision(b, entry):
                        b["governor_note"] = (
                            "Waiting for a worker with the exact immutable model "
                            "revision assigned by GenStudio"
                        )
                        continue
                    protection_note = _model_block_note(studio, b["model"], entry)
                    if protection_note:
                        b["governor_note"] = f"{studio['id']}: {protection_note}"
                        continue
                    if not entry.get("is_cloud") and not is_cached(entry):
                        b["governor_note"] = (
                            f"'{b['model']}' not downloaded on {studio['id']} "
                            f"— download it on that Mac, or pin another")
                        continue
                    # ── fleet memory governor ──
                    # Peer Hubs report their own host snapshots, so we can
                    # avoid submitting work that their MemoryGuard would
                    # reject. Missing/stale telemetry does not strand work:
                    # the worker's own guard remains the final authority.
                    is_local = peers.is_local_studio(studio)
                    # A worker catalog is one input. Measured fleet defaults
                    # and visible owner overrides form the effective site-local
                    # admission policy used immediately before dispatch.
                    mem = _admission_requirements(b["model"], entry)
                    reserve = 0.0
                    if mem is not None:
                        decision, note = await prepare_machine_memory(
                            client, studio, b["model"], entry)
                        if decision != "run":
                            # skip → another (maybe bigger/remote) studio may take
                            # it; wait → defer. NEVER errors the whole batch.
                            b["governor_note"] = f"{studio['id']}: {note}"
                            continue
                        reserve = _required_free_memory(mem) if is_local else 0.0
                    # Eligibility was calculated before the catalog/memory awaits.
                    # Recheck the physical lease so a transcription that claimed
                    # this Mac in the meantime never overlaps generation/render.
                    if studio.get("machine", "local") in busy_machines():
                        continue
                    generation = peers.dispatch_generation(studio)
                    if not peers.dispatch_ready(studio, generation):
                        continue
                    refusal = admission_refusal(b)
                    if refusal:
                        b["governor_note"] = str(refusal.get("detail") or "model is not ready")
                        ledger.save_batch(b)
                        continue
                    b["governor_note"] = None
                    item = queued.pop(compatible_index)
                    _clear_worker_attempt_evidence(item)
                    item["state"] = "running"
                    item["retry_at"] = None
                    item["studio"] = studio["id"]
                    # Never let a later dispatch reconcile a previous attempt's
                    # worker id if the new POST loses its response.
                    item["studio_job_id"] = None
                    item["tries"] += 1
                    item["_dispatch_generation"] = generation
                    item["_reserved"] = reserve
                    _reserved["gb"] += reserve
                    _busy.add(studio["id"])
                    _last_model[studio["id"]] = b["model"]
                    b["last_dispatched_at"] = time.time()
                    if b["modality"] in RECEIPT_DURABLE_MODALITIES:
                        # A crash after POST may lose its response. Persist the
                        # attempt identity before the worker can accept it.
                        # Voice also sends a stable client_request_id; Image and
                        # Music retain the possible attempt for reconciliation.
                        try:
                            ledger.save_batch(b)
                        except Exception:
                            item.update(state="queued", studio=None, studio_job_id=None,
                                        tries=item["tries"] - 1)
                            item.pop("_dispatch_generation", None)
                            item.pop("_reserved", None)
                            _reserved["gb"] = max(0.0, _reserved["gb"] - reserve)
                            _busy.discard(studio["id"])
                            raise
                    asyncio.create_task(_run_item(client, b, item, studio))
                    assigned = True
            _wakeup.clear()
            try:  # idle until new work or a worker frees up (or 3s heartbeat)
                await asyncio.wait_for(_wakeup.wait(), timeout=3.0 if not assigned else 0.1)
            except asyncio.TimeoutError:
                pass
        except Exception:
            logging.getLogger("studiohub.broker").exception(
                "dispatch loop error (continuing)")
            await asyncio.sleep(3)  # the scheduler must never die


def _worker_http_error(response) -> RuntimeError:
    """One refusal from a worker or a peer Hub, classified.

    Two producers answer with a code, and they put it in different places. A worker nests it
    inside ``detail`` (``{"detail": {"code": ..., "detail": ...}}``); the peer surface puts it
    beside it (``{"detail": "<reason>", "code": "MACHINE_BUSY"}`` -
    `standalone._peer_refusal_response`). Reading only the nested shape left every peer refusal
    untyped, so a reservation arrived as a bare 409 and its item was published as
    `WORKER_TERMINAL_ERROR` with no cause the operator or the scheduler could act on.
    """
    payload: object = None
    try:
        if "json" in response.headers.get("content-type", ""):
            payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else response.text
    except (AttributeError, ValueError):
        detail = response.text or "worker request failed"
    error = RuntimeError(f"HTTP {response.status_code}: {detail}")
    error.status_code = response.status_code
    if isinstance(detail, dict):
        error.error_code = detail.get("error_code") or detail.get("code")
    elif isinstance(payload, dict):
        error.error_code = payload.get("code") or payload.get("error_code")
    else:
        error.error_code = None
    error.model_unavailable = _model_file_failure(str(detail), error.error_code)
    error.retryable = (error.model_unavailable or response.status_code in {408, 425, 429}
                       or response.status_code >= 500)
    return error


def _worker_terminal_error(message: str, code: str | None = None) -> RuntimeError:
    error = RuntimeError(message)
    error.model_unavailable = _model_file_failure(message, code)
    error.worker_terminal = True
    error.error_code = code
    error.retryable = True  # another healthy worker may handle this item
    return error


def _classify_worker_failure(batch: dict, item: dict, studio: dict,
                             job: dict, started_at: float) -> RuntimeError:
    _record_worker_failure(item, studio, job, started_at)
    message = str(job.get("error") or f"studio job {job.get('state')}")
    code = job.get("error_code") or "WORKER_TERMINAL_ERROR"
    if job.get("state") != "cancelled" and not _is_capacity_failure(message):
        missing_files = _model_file_failure(message, code)
        # Input and capacity errors are not evidence that every future request
        # to this model will fail. Reconciled errors use this same classifier.
        if missing_files or (message.startswith("RuntimeError:") and "ValueError:" not in message):
            code = "MODEL_FILES_UNAVAILABLE" if missing_files else code
            _mark_model_failure(studio, batch["model"], job.get("model_revision"), code, message)
            if missing_files:
                item["error_code"] = code
    return _worker_terminal_error(message, code)


def _record_worker_progress(item: dict, progress, stage=None) -> None:
    """Keep a real activity timestamp without treating an unchanged poll as progress."""
    if isinstance(stage, str) and stage.strip():
        # The studio's own label for the phase ("DiT sampling"); shown beside the bar.
        item["progress_stage"] = stage.strip()[:120]
    if not isinstance(progress, (int, float)):
        return
    value = max(0.0, min(1.0, float(progress)))
    previous = item.get("progress")
    item["progress"] = value
    if previous is None or value > previous + 0.001:
        item["last_progress_at"] = time.time()


def _record_worker_execution_started_at(item: dict, job: object) -> bool:
    """Record the first trustworthy worker-side execution-start proof."""
    if item.get("execution_started_at") is not None:
        return False
    if not isinstance(job, dict) or job.get("state") not in (
            "running", "done", "error", "cancelled"):
        return False
    started_at = job.get("started_at")
    if type(started_at) not in (int, float):
        return False
    try:
        if not math.isfinite(float(started_at)) or started_at <= 0:
            return False
    except OverflowError:
        return False
    item["execution_started_at"] = started_at
    return True


def _persist_worker_execution_started_at(b: dict, item: dict, job: object) -> None:
    """Write through newly observed worker execution proof before more polling."""
    if _record_worker_execution_started_at(item, job):
        ledger.save_batch(b)


_RESOURCE_USAGE_FIELDS = {
    "sampling": {
        "interval_seconds", "samples", "started_at", "finished_at",
    },
    "host": {
        "total_gb", "available_gb_start", "minimum_available_gb",
        "available_gb_end", "maximum_used_gb", "maximum_used_percent",
        "pressure_level_start", "peak_pressure_level", "peak_pressure_raw",
        "pressure_level_end", "swap_used_gb_start", "maximum_swap_used_gb",
        "swap_used_gb_end", "swap_used_delta_gb", "swap_in_delta_bytes",
        "swap_out_delta_bytes",
    },
    "worker": {
        "rss_gb_start", "peak_rss_gb", "rss_gb_end", "peak_process_count",
    },
    "mlx": {
        "supported", "active_gb_start", "peak_active_gb", "peak_cache_gb",
        "reported_peak_gb", "active_gb_end", "cache_gb_end",
    },
    "outcome": {
        "state", "memory_failure", "restart_scheduled", "model_retained",
    },
}
_RESOURCE_USAGE_SCHEMAS = {
    "imagestudio.resource-telemetry",
    "voicestudio.resource-telemetry",
}
_RESOURCE_TEXT_FIELDS = {
    "host": {"pressure_level_start", "peak_pressure_level", "pressure_level_end"},
    "outcome": {"state"},
}
_RESOURCE_BOOL_FIELDS = {
    "mlx": {"supported"},
    "outcome": {"memory_failure", "restart_scheduled", "model_retained"},
}
_RESOURCE_INTEGER_FIELDS = {
    "sampling": {"samples"},
    "worker": {"peak_process_count"},
    "host": {"peak_pressure_raw", "swap_in_delta_bytes", "swap_out_delta_bytes"},
}
_PRESSURE_LEVELS = frozenset({
    "normal", "warning", "urgent", "critical", "unknown", "unavailable",
})
_OUTCOME_STATES = frozenset({"done", "error", "cancelled"})
_ATTEMPT_EVIDENCE_FIELDS = (
    "machine", "worker_id", "model_revision", "runtime_revision",
    "resource_usage", "error_code", "runtime_s", "duration_s", "finished_at",
    "worker_accepted_params",
)


def _clear_worker_attempt_evidence(item: dict) -> None:
    """A retry must report only the worker that owns the new attempt."""
    for field in _ATTEMPT_EVIDENCE_FIELDS:
        item.pop(field, None)
    item["error"] = None


def _worker_reason(response: httpx.Response) -> str:
    """The worker's own explanation of a refusal, in a form an operator can act on.

    Workers answer a refusal with ``{"detail": "<text>"}`` or
    ``{"detail": {"code": ..., "detail": ...}}``. The code is the part that names the cause
    ("REFERENCE_AUDIO_CONTRACT_UNAVAILABLE"), so it is kept; anything unparseable falls back to
    the status, because a truncation here must never hide that the request failed.
    """
    detail: object = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        code = str(detail.get("code") or "").strip()
        text = str(detail.get("detail") or "").strip()
        if code and text:
            return f"{code}: {text}"
        return code or text or f"HTTP {response.status_code}"
    text = str(detail or "").strip()
    return text[:500] or f"HTTP {response.status_code}"


def _record_worker_identity(item: dict, studio: dict, job: dict) -> None:
    """Keep bounded worker identity for both successful and failed attempts."""
    item["machine"] = str(studio.get("machine") or "local")[:500]
    worker_id = job.get("worker_id")
    item["worker_id"] = (
        str(worker_id).strip()[:500] if worker_id is not None
        else str(studio.get("id") or "unknown")[:500]
    )
    for field in ("model_revision", "runtime_revision"):
        value = job.get(field)
        if isinstance(value, str) and value.strip():
            item[field] = value.strip()[:500]


def _record_worker_failure(item: dict, studio: dict, job: dict, t_start: float) -> None:
    """Retain the authenticated worker evidence needed to diagnose a failure."""
    _record_worker_execution_started_at(item, job)
    _record_worker_identity(item, studio, job)
    _record_worker_resource_usage(item, job)
    code = job.get("error_code")
    if code is not None:
        item["error_code"] = _sanitize_public_error_code(code)
    runtime = job.get("runtime_s", job.get("generation_seconds", job.get("duration_seconds")))
    try:
        runtime = float(runtime) if runtime is not None else round(time.time() - t_start, 2)
    except (TypeError, ValueError):
        runtime = round(time.time() - t_start, 2)
    if not math.isfinite(runtime) or runtime < 0:
        runtime = max(0.0, round(time.time() - t_start, 2))
    item["runtime_s"] = runtime
    item["duration_s"] = runtime


def _record_worker_resource_usage(item: dict, job: dict) -> None:
    """Retain only the versioned, worker-produced telemetry contract.

    Worker payloads are authenticated but still cross a service boundary.  A
    small whitelist prevents an accidental future worker field (paths, command
    lines, environment values) from becoming customer-visible through the Hub.
    """
    raw = job.get("resource_usage")
    if not isinstance(raw, dict):
        raw = job.get("resource_telemetry")
    if not isinstance(raw, dict):
        return
    schema = raw.get("schema")
    if schema not in _RESOURCE_USAGE_SCHEMAS:
        return
    if type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
        return
    clean = {
        "schema": schema,
        "schema_version": 1,
    }
    for section, fields in _RESOURCE_USAGE_FIELDS.items():
        values = raw.get(section)
        if not isinstance(values, dict):
            continue
        sanitized = {}
        for key, value in values.items():
            if key not in fields:
                continue
            if value is None:
                sanitized[key] = None
            elif key in _RESOURCE_BOOL_FIELDS.get(section, set()):
                if isinstance(value, bool):
                    sanitized[key] = value
            elif key in _RESOURCE_INTEGER_FIELDS.get(section, set()):
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    sanitized[key] = value
            elif key in _RESOURCE_TEXT_FIELDS.get(section, set()):
                if isinstance(value, str) and (
                    (section == "host" and value in _PRESSURE_LEVELS)
                    or (section == "outcome" and value in _OUTCOME_STATES)
                ):
                    sanitized[key] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
                if math.isfinite(numeric) and (
                    numeric >= 0 or key == "swap_used_delta_gb"
                ):
                    sanitized[key] = value
        clean[section] = sanitized
    item["resource_usage"] = clean


async def _run_item(client: httpx.AsyncClient, b: dict, item: dict, studio: dict):
    endpoint, prompt_field, artifact_kind = MODALITY[b["modality"]]
    t_start = time.time()  # wall-clock fallback for generation duration
    item["run_started"] = t_start   # surfaced live for elapsed/ETA in the UI
    item["progress"] = None         # 0..1 as reported by the studio while running
    item["last_progress_at"] = t_start
    body = dict(b["shared_params"])
    body.update(item["params"])
    body["repo"] = b["model"]
    body[prompt_field] = item["prompt"]
    if b["modality"] == "voice":
        body["client_request_id"] = f"studiohub:{b['id']}:{item['index']}"
        floor = memory_admission.override_free_gb(b["model"])
        if floor is not None:   # the worker's own preflight must honour the same override
            body["memory_floor_gb"] = floor
    if item["seed"] is not None:
        body["seed"] = item["seed"]
    # `output_format` (compress at source) reaches only a worker that advertises it; the rest keep
    # producing their native file, which the caller's own fallback handles.
    if "output_format" in body and not await _lane_accepts_output_format(
            studio, b["model"], body["output_format"]):
        body.pop("output_format")
    # Reference-image jobs use multipart. Image Studio accepts ``image`` on
    # img2img/edit; Video Studio accepts ``file`` on video2video with
    # mode=img2video. One exact source image is used per stable scene id.
    refs = body.pop("reference_images", None) if b["modality"] in ("image", "video") else None
    voice_reference_asset_id = (
        str(body.pop("voice_reference_asset_id", "") or "").strip()
        if b["modality"] == "voice" else ""
    )
    ref_mode = body.pop("ref_mode", None)
    body.pop("reference_images", None)  # never forward references as JSON
    generation = item.get("_dispatch_generation")
    durable_post_attempted = False
    durable_post_rejected = False

    def requeue_if_peer_changed() -> bool:
        if generation is None or peers.dispatch_ready(studio, generation):
            return False
        item.update(state="queued", studio=None, studio_job_id=None,
                    tries=max(0, int(item.get("tries") or 0) - 1),
                    error="worker authentication changed before dispatch")
        return True

    try:
        if _voice_recovery_owns(item):
            if (b.get("modality") in DURABLE_ASYNC_MODALITIES
                    and b.get("cancelled") and item.get("studio_job_id")):
                await _signal_worker_cancel(client, item)
            return  # recovery owns the accepted Voice identity; never race adoption
        if _expire_genstudio_batch(b):
            item["state"] = "cancelled"
            item["error"] = "GenStudio execution lease expired"
            return
        if voice_reference_asset_id:
            if body.get("voice_library_id"):
                item["state"] = "error"
                item["error_code"] = "VOICE_REFERENCE_CONFLICT"
                item["error"] = "Use either voice_library_id or voice_reference_asset_id, not both."
                return
            try:
                reference, reference_bytes = execution_assets.read_voice_reference(
                    voice_reference_asset_id
                )
            except execution_assets.ExecutionAssetError as exc:
                item["state"] = "error"
                item["error_code"] = exc.code
                item["error"] = exc.detail
                return
            if reference.get("transcript"):
                body["ref_transcript"] = reference["transcript"]
            if requeue_if_peer_changed():
                return
            url, headers = studio_request(
                studio, "/api/generate/txt2speech/reference"
            )
            r = await client.post(
                url,
                data={
                    "request_json": json.dumps(body, separators=(",", ":")),
                    "transcript_segments_json": json.dumps(
                        reference.get("transcript_segments") or [],
                        separators=(",", ":"),
                    ),
                    "source_sha256": reference["sha256"],
                    "reference_expires_at": str(reference["expires_at"]),
                },
                files={
                    "audio": (
                        f"reference{reference['audio_extension']}",
                        reference_bytes,
                        reference["media_type"],
                    )
                },
                headers=headers,
                timeout=120.0,
            )
            if r.status_code >= 400:
                # The worker refused the reference. Until this check existed the refusal was
                # discarded, the job was accepted anyway with no reference attached, and the
                # operator saw only "Voice cloning needs a reference voice or private reference
                # upload" three retries later - or the 502 "worker artifact reference is
                # invalid" from a job that never produced anything. Surface the worker's own
                # reason instead: it names the actual cause, such as a model whose audited
                # reference contract is not installed.
                item["state"] = "error"
                item["error_code"] = "VOICE_REFERENCE_REJECTED"
                item["error"] = f"the Voice worker rejected the reference: {_worker_reason(r)}"
                return
            item["voice_reference_asset_id"] = voice_reference_asset_id
        elif refs:
            entry = await _catalog_entry(studio, b["model"])
            caps = (entry or {}).get("capabilities") or []
            if b["modality"] == "video":
                mode = ref_mode or "img2video"
                supported_modes = ("img2video",)
            else:
                mode = ref_mode or ("img2img" if "img2img" in caps
                                    else ("edit" if "edit" in caps else None))
                supported_modes = ("img2img", "edit")
            if not mode or mode not in supported_modes or mode not in caps:
                item["state"] = "error"
                item["error"] = (f"model {b['model']} does not support reference "
                                 f"images (needs {'img2video' if b['modality'] == 'video' else 'img2img/edit'} capability)")
                return  # terminal — the finally block cleans up
            try:
                img_bytes, mime = await _resolve_reference(client, refs[0])
            except (ValueError, httpx.HTTPError) as e:
                item["state"] = "error"
                item["error"] = f"reference image could not be loaded: {e}"
                return
            if b["modality"] == "video":
                body["mode"] = "img2video"
                if requeue_if_peer_changed():
                    return
                url, headers = studio_request(studio, "/api/generate/video2video")
                r = await client.post(
                    url,
                    data=_video_multipart_fields(body),
                    files={"file": (f"reference{_ext(mime)}", img_bytes, mime)},
                    headers=headers)
            else:
                if requeue_if_peer_changed():
                    return
                url, headers = studio_request(studio, f"/api/generate/{mode}")
                durable_post_attempted = True
                r = await client.post(
                    url,
                    data=_multipart_fields(body),
                    files={"image": (f"reference{_ext(mime)}", img_bytes, mime)},
                    headers=headers)
        else:
            if requeue_if_peer_changed():
                return
            url, headers = studio_request(studio, endpoint)
            durable_post_attempted = b["modality"] in DURABLE_ASYNC_MODALITIES
            r = await client.post(url, json=body, headers=headers)
        if r.status_code == 401 and not peers.is_local_studio(studio):
            peers.invalidate(studio["machine"], rejected=True,
                             modality=studio["modality"])
        if r.status_code >= 400 and voice_reference_asset_id:
            try:
                worker_detail = r.json().get("detail")
            except (ValueError, AttributeError):
                worker_detail = None
            if isinstance(worker_detail, dict) and worker_detail.get("code"):
                item["state"] = "error"
                item["error_code"] = str(worker_detail["code"])
                item["error"] = str(
                    worker_detail.get("detail") or "Voice reference preparation failed."
                )
                return
        if r.status_code >= 400:
            durable_post_rejected = b["modality"] in DURABLE_ASYNC_MODALITIES
            rejection = _worker_http_error(r)
            if rejection.model_unavailable:
                item["error_code"] = "MODEL_FILES_UNAVAILABLE"
                _mark_model_failure(studio, b["model"], None,
                                    "MODEL_FILES_UNAVAILABLE", str(rejection))
            elif getattr(rejection, "error_code", None):
                # Record what the refusal called itself. Without it a typed answer - a
                # reservation, a paused model, a disabled machine - reached the item as the
                # generic WORKER_TERMINAL_ERROR, and the deferral below had nothing to key on.
                item["error_code"] = _sanitize_public_error_code(rejection.error_code)
            raise rejection
        if b["modality"] == "voice":
            job = _voice_job_from_response(r)
            _validate_voice_worker_job(item, job)
        else:
            payload = r.json()
            job = payload.get("job") if isinstance(payload, dict) else None
            if b["modality"] in DURABLE_ASYNC_MODALITIES and (
                    not isinstance(job, dict) or not isinstance(job.get("id"), str)
                    or not job["id"] or job.get("state") not in {
                        "queued", "running", "cancel_requested", "done", "error", "cancelled"}):
                raise ValueError(
                    f"{b['modality'].title()} worker acceptance did not contain a valid job receipt"
                )
        if b["modality"] in RECEIPT_DURABLE_MODALITIES:
            # The acceptance receipt below is the durability boundary. Record
            # a supplied start time in the same write so a failure cannot occur
            # first and requeue work the worker has already accepted.
            _record_worker_execution_started_at(item, job)
        else:
            _persist_worker_execution_started_at(b, item, job)
        _record_worker_identity(item, studio, job)
        _record_worker_resource_usage(item, job)
        item["studio_job_id"] = job["id"]
        if b["modality"] == "music" and isinstance(job.get("params"), dict):
            # Music normalizes aliases, defaults and prompt whitespace inside
            # its validator. Retain that worker-owned canonical request in the
            # same durable write as the accepted job identity.
            item["worker_accepted_params"] = job["params"]
        if b["modality"] in RECEIPT_DURABLE_MODALITIES:
            try:
                ledger.save_batch(b)  # retain the accepted receipt before polling
            except Exception:
                if b["modality"] == "voice":
                    _mark_voice_uncertain(
                        item,
                        "The Voice worker accepted this job, but the Hub could not "
                        "persist its receipt; it was not retried automatically.",
                    )
                    item["error_code"] = "JOB_PERSISTENCE_UNCERTAIN"
                    item["error"] = item["recovery"]["reason"]
                else:
                    _mark_async_uncertain(
                        item, studio, b["modality"],
                        f"The {b['modality'].title()} worker accepted this job, but the Hub "
                        "could not persist its receipt; it was not retried automatically.",
                        "JOB_PERSISTENCE_UNCERTAIN",
                    )
                raise
        # Keep the Hub's ownership fact after this finished batch is pruned;
        # the optional Studio reporter must not have to win a poll race.
        try:
            ledger.record_activity_ownership(
                machine=str(studio.get("machine") or "local"),
                studio=str(studio.get("id") or ""), job_id=str(job["id"]),
                model=str(b.get("model") or "") or None,
            )
        except Exception:
            # Optional telemetry must never retry a worker that already
            # accepted customer work; studio_job_id remains the live fallback.
            logging.getLogger("studiohub.broker").exception(
                "Could not record optional activity ownership"
            )
        lease_expired = _expire_genstudio_batch(b)
        if lease_expired or b["cancelled"]:
            cancel_confirmed = bool(item.get("cancel_signal_sent_at"))
            if not cancel_confirmed:
                cancel_confirmed = await _signal_worker_cancel(client, item)
            if b["modality"] in DURABLE_ASYNC_MODALITIES and durable_post_attempted:
                if item.get("state") != "uncertain":
                    _mark_async_uncertain(
                        item, studio, b["modality"],
                        f"{b['modality'].title()} cancellation was requested after submission; "
                        "the original worker outcome must be reconciled.",
                    )
                return
            if b["modality"] == "voice" and item.get("studio_job_id"):
                if lease_expired:
                    return
                if not cancel_confirmed:
                    _mark_accepted_voice_cancel_uncertain(b, item)
                    return
                item["state"] = "cancel_requested"
                ledger.save_batch(b)
            else:
                item["state"] = "cancelled"
                item["error"] = (
                    "GenStudio execution lease expired"
                    if b.get("lease_expired")
                    else "Cancelled by user"
                )
                return
        # poll the studio's async job until terminal
        while True:
            await asyncio.sleep(POLL_S)
            if _voice_recovery_owns(item):
                if (b.get("modality") in DURABLE_ASYNC_MODALITIES
                        and b.get("cancelled") and item.get("studio_job_id")):
                    await _signal_worker_cancel(client, item)
                return  # explicit recovery has taken ownership of this job
            lease_expired = _expire_genstudio_batch(b)
            if lease_expired or b["cancelled"]:
                cancel_confirmed = bool(item.get("cancel_signal_sent_at"))
                if not cancel_confirmed:
                    cancel_confirmed = await _signal_worker_cancel(client, item)
                if b["modality"] in DURABLE_ASYNC_MODALITIES:
                    if item.get("state") != "uncertain":
                        _mark_async_uncertain(
                            item, studio, b["modality"],
                            f"{b['modality'].title()} cancellation was requested after acceptance; "
                            "the original worker outcome must be reconciled.",
                        )
                    return
                if b["modality"] == "voice" and item.get("studio_job_id"):
                    if lease_expired:
                        return
                    if not cancel_confirmed:
                        _mark_accepted_voice_cancel_uncertain(b, item)
                        return
                    item["state"] = "cancel_requested"
                    ledger.save_batch(b)
                else:
                    item["state"] = "cancelled"
                    item["error"] = (
                        "GenStudio execution lease expired"
                        if b.get("lease_expired")
                        else "Cancelled by user"
                    )
                    return
            url, headers = studio_request(studio, f"/api/generate/jobs/{job['id']}")
            jr = await client.get(
                url, headers=headers)
            if jr.status_code >= 400:
                if jr.status_code == 401 and not peers.is_local_studio(studio):
                    peers.invalidate(studio["machine"], rejected=True,
                                     modality=studio["modality"])
                raise _worker_http_error(jr)
            if _voice_recovery_owns(item):
                return
            if b["modality"] == "voice":
                j = _voice_job_from_response(jr)
                _validate_voice_worker_job(
                    item, j,
                    expected_request_id=str(body.get("client_request_id") or ""),
                )
            else:
                payload = jr.json()
                j = payload.get("job") if isinstance(payload, dict) else None
                if b["modality"] in DURABLE_ASYNC_MODALITIES:
                    _validate_durable_worker_job(b["modality"], item, j)
            _persist_worker_execution_started_at(b, item, j)
            _record_worker_identity(item, studio, j)
            _record_worker_resource_usage(item, j)
            state = j.get("state")
            if state in ("queued", "running", "cancel_requested"):
                _record_worker_progress(item, j.get("progress"), j.get("progress_stage"))
                item["chunk_index"] = j.get("chunk_index")
                item["chunk_total"] = j.get("chunk_total")
                continue
            if j.get("error") or state in ("error", "cancelled"):
                if b["cancelled"]:
                    _record_worker_failure(item, studio, j, t_start)
                    terminal_state = state if state in {"error", "cancelled"} else "error"
                    item.update(
                        state=terminal_state,
                        error=str(j.get("error") or terminal_state)[:500],
                        error_code=str(
                            j.get("error_code") or "WORKER_TERMINAL_ERROR"
                        )[:128],
                        finished_at=time.time(),
                        retry_at=None,
                    )
                    return
                raise _classify_worker_failure(b, item, studio, j, t_start)
            # terminal + no error = success
            await _record_worker_success(client, b, item, studio, j, body, t_start)
            if _voice_recovery_owns(item):
                return
            _mark_machine_success(studio)
            return
    except Exception as e:
        if _voice_recovery_owns(item):
            return
        if b.get("modality") == "voice" and isinstance(e, _VoiceReceiptError):
            _mark_voice_receipt_invalid(item)
            return
        # The worker may have completed even though this status request lost
        # its connection. Reconcile the original job before considering a
        # retry; otherwise one image can be generated twice or reported as a
        # false failure.
        if b["cancelled"]:
            if (b["modality"] in DURABLE_ASYNC_MODALITIES
                    and durable_post_attempted and not durable_post_rejected):
                if item.get("state") != "uncertain":
                    _mark_async_uncertain(
                        item, studio, b["modality"],
                        f"{b['modality'].title()} cancellation followed a possible worker acceptance; "
                        "reconcile the original outcome before releasing its machine.",
                    )
                if item.get("studio_job_id"):
                    await _signal_worker_cancel(client, item)
            elif (b["modality"] == "voice" and item.get("studio_job_id")
                  and not getattr(e, "worker_terminal", False)):
                _mark_accepted_voice_cancel_uncertain(b, item)
            else:
                item["state"] = "cancelled"
                item["error"] = "Cancelled by user"
            return
        recovered = (False if getattr(e, "worker_terminal", False)
                     else await _recover_worker_job(client, b, item, studio, body, t_start))
        if _voice_recovery_owns(item) or recovered is True:
            return
        if isinstance(recovered, dict):
            e = _classify_worker_failure(b, item, studio, recovered, t_start)
        message = str(e) or type(e).__name__
        # A Studio on another Mac cannot be reconciled the way a local one can: the result, if
        # there is one, is on a machine that has stopped answering, and nothing here can reach
        # it. The owner's rule for the fleet is therefore to regenerate rather than to wait, so
        # a remote attempt that cannot be reconciled falls through to the bounded infrastructure
        # retry below, which already avoids the machine and limits the attempts. Local work
        # keeps reconciliation, because there the earlier result may genuinely still be there.
        remote_studio = not peers.is_local_studio(studio)
        if b.get("modality") in DURABLE_ASYNC_MODALITIES:
            code = str(getattr(e, "error_code", None) or item.get("error_code") or "")
            if code in {"JOB_PERSISTENCE_UNCERTAIN", "WORKER_RESTART_UNCERTAIN"}:
                _mark_async_uncertain(
                    item, studio, b["modality"],
                    f"The accepted {b['modality'].title()} job has an uncertain final result; "
                    "reconcile its original worker receipt before any retry.", code,
                )
                return
            if (item.get("studio_job_id") and recovered is False
                    and not getattr(e, "worker_terminal", False)):
                if remote_studio and b.get("modality") == "image":
                    item.pop("studio_job_id", None)
                    item.pop("studio", None)
                else:
                    _mark_async_uncertain(
                        item, studio, b["modality"],
                        f"The accepted {b['modality'].title()} job could not be reconciled; "
                        "it was not retried automatically.",
                    )
                    return
            if (durable_post_attempted and not durable_post_rejected
                    and not item.get("studio_job_id")
                    and (not remote_studio or b.get("modality") == "music")):
                # Image preserves its established remote regeneration policy.
                # Music remains fenced even remotely because a single render is
                # long-running and cannot be made idempotent without a receipt.
                _mark_async_uncertain(
                    item, studio, b["modality"],
                    f"{b['modality'].title()} submission may have been accepted but its worker "
                    "receipt was lost; reconcile before any retry.",
                )
                return
        if (b.get("modality") == "voice" and item.get("studio_job_id")
                and _is_transport_failure(e, message)):
            if remote_studio:
                item.pop("studio_job_id", None)
                item.pop("studio", None)
            else:
                _mark_voice_uncertain(
                    item,
                    "The accepted Voice Studio job could not be reconciled; "
                    "it was not retried automatically.",
                )
                return
        retryable = getattr(e, "retryable", True)
        item["last_progress_at"] = time.time()
        now = time.time()
        if retryable and _is_capacity_failure(message) and not b["cancelled"]:
            # Memory pressure is a capacity wait, not a consumed generation
            # attempt. Avoid this Mac briefly so another healthy worker can
            # steal the item; if none can, keep it queued until memory clears.
            #
            # Bounded by the owner's rule: after CAPACITY_HANDBACK_AFTER refusals the job is handed
            # back (below). Until then it backs off: the first refusals release idle siblings and
            # retry soon, the next one waits longer (CAPACITY_RETRY_S doubling, at most
            # CAPACITY_RETRY_MAX_S). A reserved Mac (MACHINE_BUSY, below) is a different wait and
            # is still unbounded.
            failures = int(item.get("capacity_failures") or 0) + 1
            item["capacity_failures"] = failures
            if failures >= CAPACITY_HANDBACK_AFTER:
                # Hand it back, typed: not retryable on this Mac, and not this Mac's failure either
                # (no machine penalty) - the Gateway re-routes it to another eligible Mac.
                item.update(state="error", retry_at=None, error_code=INSUFFICIENT_MEMORY,
                            error=await _insufficient_memory_text(b, studio, failures))
                item["tries"] = max(0, int(item.get("tries") or 0) - 1)
                return
            handoff = (await release_idle_siblings(client, studio)
                       if failures <= CAPACITY_RELEASE_ATTEMPTS else {"released": 0})
            item["tries"] = max(0, int(item.get("tries") or 0) - 1)
            item["state"] = "queued"
            item["error"] = f"waiting for capacity: {message} (refused {failures}x)"
            delay = (3.0 if handoff["released"]
                     else min(CAPACITY_RETRY_S * 2 ** min(failures - 1, 6), CAPACITY_RETRY_MAX_S))
            item["retry_at"] = now + delay
            item.setdefault("capacity_wait_started_at", now)
            item.setdefault("avoid_machines", {})[
                studio.get("machine", "local")
            ] = now + max(FAILED_WORKER_AVOID_S, delay)
        elif _is_machine_reservation_refusal(e) and not b["cancelled"]:
            # Another queue holds the Mac the pool chose. That answer arrives before this
            # item's worker saw the request, so nothing was submitted and no attempt was
            # spent: keep the item queued, and take the reserved Mac out of the next
            # selection so the dispatcher offers it to a free machine in the pool - or to
            # this one again once the lease is released. This is the peer's `busy_machines()`
            # skip, which is a skip in `_eligible_studios` and used to be a terminal failure
            # here: the Gateway reported a `pool` batch dying as item 0 with
            # `HTTP 409: local is reserved for other work on this Mac` while the rest of the
            # pool was idle, and that path is reproduced in
            # `tests/hub_regression/cases/test_pool_peer_machine_busy.py`. The machine is
            # healthy and working, so it is never marked failed.
            #
            # **Nothing here bounds the wait, and that is deliberate.** `tries` is restored on
            # every refusal so the MAX_TRIES ladder cannot end it; the stall detector only
            # *reports* (`stalled` is documented "visible, never destructive", and
            # `capacity_wait_started_at` is written but read nowhere); so a machine that never
            # frees up holds the item until the operator cancels. That is the same unbounded
            # semantics as the capacity wait directly above, and it is what the owner asked
            # for — "should the work get queued instead of failing outright?" — but it is a
            # queue with no clock, not a wait with a deadline, and a later reader should not
            # assume otherwise.
            item["tries"] = max(0, int(item.get("tries") or 0) - 1)
            item["state"] = "queued"
            item["error"] = f"waiting for a free machine: {message}"
            # The Mac is busy, not broken, and the peer's 409 stays the authority on when it
            # is free: no parked retry (the dispatcher's 3 s heartbeat asks again) and a short
            # avoid window, recorded per machine so this one refusal covers every item aimed at it.
            item["retry_at"] = None
            machine = studio.get("machine", "local")
            item.setdefault("avoid_machines", {})[machine] = now + MACHINE_RESERVED_AVOID_S
            _machine_reserved_until[machine] = now + MACHINE_RESERVED_AVOID_S
        elif _is_disk_full_refusal(e) and not b["cancelled"]:
            # The Mac refused at the door because its disk is full: not a failure of the work and not
            # an attempt. Keep the item queued, restore the try it was charged, and leave that Mac out of
            # selection for DISK_FULL_AVOID_S so another one takes it. If every Mac is full it waits
            # here (the same queue-wait as a reserved Mac) and is offered again when the window ends.
            item["tries"] = max(0, int(item.get("tries") or 0) - 1)
            item["state"] = "queued"
            item["error"] = f"waiting for a Mac with free disk space: {message}"
            item["retry_at"] = None
            item.setdefault("avoid_machines", {})[studio.get("machine", "local")] = now + DISK_FULL_AVOID_S
        elif retryable and _is_transport_failure(e, message) and not b["cancelled"]:
            # Infrastructure failures get a longer bounded healing window than
            # genuine generation errors. The stable worker job id was already
            # reconciled above, so retrying cannot duplicate a known result.
            _mark_machine_failure(studio, message)
            failures = int(item.get("infra_failures") or 0) + 1
            started = float(item.get("infra_failure_started_at") or now)
            item["infra_failures"] = failures
            item["infra_failure_started_at"] = started
            item.setdefault("avoid_machines", {})[
                studio.get("machine", "local")
            ] = now + FAILED_WORKER_AVOID_S
            within_window = now - started < INFRA_RETRY_WINDOW_S
            if failures < MAX_INFRA_TRIES and within_window:
                item["state"] = "queued"
                item["error"] = (
                    f"connection failure {failures}/{MAX_INFRA_TRIES}; "
                    f"recovering automatically: {message}"
                )
                delay_index = min(failures - 1, len(INFRA_RETRY_DELAYS_S) - 1)
                item["retry_at"] = now + INFRA_RETRY_DELAYS_S[delay_index]
            else:
                item["state"] = "error"
                item["error"] = f"connection recovery exhausted: {message}"
                item["retry_at"] = None
        elif retryable and item["tries"] < MAX_TRIES and not b["cancelled"]:
            item["state"] = "queued"  # work-stealing retry, possibly elsewhere
            item["error"] = f"try {item['tries']} failed: {message}"
            delay_index = min(item["tries"] - 1, len(RETRY_DELAYS_S) - 1)
            item["retry_at"] = time.time() + RETRY_DELAYS_S[delay_index]
        else:
            item["state"] = "error"
            item["error"] = message
            item["retry_at"] = None
    finally:
        if item["state"] in ("done", "error", "cancelled"):
            item.pop("capacity_failures", None)
            item.pop("capacity_wait_started_at", None)
            item.setdefault("machine", str(studio.get("machine") or "local")[:500])
            item.setdefault("worker_id", str(studio.get("id") or "unknown")[:500])
            if item["state"] == "error":
                item.setdefault("error_code", "WORKER_TERMINAL_ERROR")
            runtime = round(time.time() - t_start, 2)
            item.setdefault("runtime_s", runtime)
            item.setdefault("duration_s", item["runtime_s"])
            item.setdefault("finished_at", time.time())
            item["last_progress_at"] = item["finished_at"]
        _busy.discard(studio["id"])
        _reserved["gb"] = max(0.0, _reserved["gb"] - item.get("_reserved", 0.0))
        item["_reserved"] = 0.0
        item.pop("_dispatch_generation", None)
        # The Mac is free and the item's state is final: let the scheduler offer it the next item
        # now. The per-item webhook below can take seconds (a slow or restarting callback target
        # costs up to its 5 s connect / 60 s read timeout), and the wake used to wait for it.
        _wakeup.set()
        if b.get("modality") in DURABLE_ASYNC_MODALITIES:
            publication_state = {
                key: b[key] for key in ("done_notified", "finished_at")
                if key in b
            }
            item_webhook_reserved = _reserve_item_webhook(b, item)
            try:
                await _maybe_finish(client, b)
            except Exception:
                _retain_async_terminal_persistence_uncertainty(
                    b, item, studio, publication_state
                )
                raise
            if item_webhook_reserved:
                await _post_item_webhook(
                    client, b, item, reserved=True
                )   # per-scene result → client
        else:
            await _post_item_webhook(client, b, item)
            await _maybe_finish(client, b)
        _wakeup.set()


_dispatcher_task: asyncio.Task | None = None


def start_dispatcher():
    global _dispatcher_task
    loop = asyncio.get_running_loop()
    if _dispatcher_task is None or _dispatcher_task.done() or _dispatcher_task.get_loop() is not loop:
        _dispatcher_task = asyncio.create_task(_dispatch_loop())


async def stop_dispatcher() -> None:
    """Stop the one scheduler task created by this packaged Hub lifespan."""
    global _dispatcher_task
    task = _dispatcher_task
    _dispatcher_task = None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def wake_dispatcher() -> None:
    """Re-evaluate queued work after an operator changes admission policy."""
    _wakeup.set()
