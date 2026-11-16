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

from . import (artifact_metadata, automatic_recovery, cloud_guard, execution_assets,
               execution_identity, ledger, model_ids, peers, shared_voices)
from . import routing as routing_rule
from .memory_control import FleetMemoryControl, SUPPORTED_MODALITIES
from .peers import studio_request
from .monitor import is_cached, _local_node_id
from .registry import DATA_DIR, assigned_image_model, base_url, label_for, machine_enabled, studio_enabled
from .resources import host_stats
from . import stall
from . import update_drain
from . import memory_admission
from . import preferred_hold
from . import developer_test_admission
from .. import workstation

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
    (strings; CSV for lora lists). Unknown fields are ignored by the studio.

    ``aspect_ratio`` and ``resolution`` travel too: an image request names its geometry that way
    (the Gateway's body has them and not ``width``/``height``), and dropping them here made every
    reference render fall back to the worker's own default ratio — a 1:1 request quietly became
    16:9. A worker that ignores them still sees ``width``/``height`` when the caller supplied them.
    """
    out = {}
    for k in ("repo", "prompt", "negative_prompt", "width", "height", "aspect_ratio",
              "resolution", "steps", "guidance", "seed", "image_strength", "quantize"):
        v = body.get(k)
        if v is not None:
            out[k] = str(v)
    for k in ("lora_names", "lora_scales"):
        v = body.get(k)
        if isinstance(v, (list, tuple)):
            out[k] = ",".join(str(x) for x in v)
        elif v is not None:
            out[k] = str(v)
    # The Hub's internal reference policy travels as JSON, never as a Python repr. It is set
    # only from the selected producer's snapshot; a caller-supplied value is dropped earlier.
    policy = body.get("reference_policy")
    if isinstance(policy, dict):
        out["reference_policy"] = json.dumps(policy, separators=(",", ":"))
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


# ── an image item's lane ────────────────────────────────────────────────────
# A reference-to-image item is a different lane on the same model: it needs a Mac that advertises
# `edit`, and it peaks far higher than text-to-image (12.44 GB measured vs ~3 GB bounded). Both
# facts are per item, so they are computed from the item rather than from the model alone.

def _item_mode(item: dict) -> str:
    """``'edit'`` for an item carrying reference images, else ``'txt2img'``.

    Resolved from the item, never assumed from the model: one model serves both lanes.
    """
    params = item.get("params") or {}
    references = params.get("reference_images")
    return "edit" if isinstance(references, (list, tuple)) and references else "txt2img"


def _batch_mode(batch: dict) -> str:
    """The stricter lane any queued item of this batch needs, so the pool serves it."""
    return ("edit" if any(_item_mode(item) == "edit" for item in batch.get("items") or ())
            else "txt2img")


def _reference_limits(entry: dict | None) -> dict:
    """The reference block a worker published in its lane contract, or ``{}``."""
    contract = (entry or {}).get("lane_contract")
    references = (contract or {}).get("references") if isinstance(contract, dict) else None
    return references if isinstance(references, dict) else {}


# The producer's `hub_memory_admission` annotation is the one authority for a node's reference
# policy. Its absence is the older-producer path (lane contract only); its presence is validated
# strictly below and never replaced by a controller-side guess.
_REFERENCE_POLICY_ABSENT = object()


def _reference_destination_id(studio: dict) -> str:
    """The resolved machine id a job is dispatched to - never this Hub's ``local`` alias."""
    studio = studio or {}
    if peers.is_local_studio(studio):
        return _local_node_id()
    return str(studio.get("machine") or "")


def _aggregate_reference_policy(studio: dict, model: str):
    """The producer's annotated row from the cache-only aggregate catalog, or ``_ABSENT``.

    Local rows are annotated by the monitor from this Mac's own telemetry and live overrides;
    a remote row carries only what its producer published. This never asks a node for anything.
    A readable catalog with no annotation is the older producer; an unreadable cache is unknown
    and refuses by name, never the legacy fallback.
    """
    studio = studio or {}
    try:
        rows = _monitor().cached_aggregate_catalog().get("models") or []
    except Exception as exc:
        # An unreadable cache is not an older producer: nothing was observed, so the lane
        # contract must not answer for it. Only an observed absent annotation keeps that path.
        raise ValueError(
            f"the producer's memory-admission snapshot is unreadable: {exc}") from exc
    for row in rows:
        if not isinstance(row, dict) or row.get("hub_studio") != studio.get("id"):
            continue
        if row.get("repo") == model or model in (row.get("aliases") or []):
            return (row["hub_memory_admission"]
                    if "hub_memory_admission" in row else _REFERENCE_POLICY_ABSENT)
    return _REFERENCE_POLICY_ABSENT


def _producer_reference_policy(studio: dict, entry: dict | None, model: str):
    """The selected producer's own snapshot, or ``_ABSENT`` for an older annotation-less row.

    The worker catalog row is preferred; the aggregate annotation answers for this Hub's own
    rows, whose scheduling view is the raw worker catalog. A present ``None`` or malformed value
    is returned as-is so validation can refuse it loudly instead of falling back. An unreadable
    aggregate raises ``ValueError`` naming the unknown rather than reading as absent.
    """
    entry = entry or {}
    if "hub_memory_admission" in entry:
        return entry["hub_memory_admission"]
    return _aggregate_reference_policy(studio, model)


def _validated_reference_policy(snapshot, studio: dict, model: str, entry: dict | None,
                                mode: str, count: int) -> dict:
    """The exact ACK-shaped dispatch policy for one selected node/model/mode/count.

    Every value comes from the producer's one snapshot. A present but invalid, unknown or
    refusing policy raises ``ValueError`` with a named reason - it never falls back to the lane
    contract or to controller-local policy. ``false`` and ``0`` keep their meaning; a required
    value of ``null`` is unknown and refuses.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("the producer published an invalid memory-admission snapshot (not an object)")
    node_id = _reference_destination_id(studio)
    producer_node = str(snapshot.get("node_id") or "").strip().lower()
    if not node_id or producer_node != node_id.lower():
        raise ValueError(
            f"the reference policy describes node {snapshot.get('node_id')!r}, not the selected "
            f"destination {node_id!r}")
    selected_model = str(model or "").strip()
    producer_model = str(snapshot.get("model") or "").strip()
    # Exact selected model only: another row's repo/model_id alias is a different identity.
    if not producer_model or producer_model != selected_model:
        raise ValueError(
            f"the reference policy names model {producer_model!r}, "
            f"not the selected {selected_model!r}")
    reference = snapshot.get("reference")
    view = reference.get(mode) if isinstance(reference, dict) else None
    if not isinstance(view, dict):
        raise ValueError(f"the producer published no reference policy for mode {mode!r}")
    if view.get("mode") != mode:
        raise ValueError(
            f"the reference policy mode {view.get('mode')!r} is not the selected mode {mode!r}")
    if view.get("available") is not True:
        reason = view.get("reason")
        raise ValueError(
            "the producer's reference policy is unavailable"
            + (f": {reason}" if isinstance(reason, str) and reason else ""))
    tier = view.get("physical_tier")
    if not isinstance(tier, str) or not tier:
        raise ValueError("the producer's reference policy states no physical tier")
    tier_gb = view.get("physical_tier_gb")
    if tier_gb is not None and (isinstance(tier_gb, bool) or not isinstance(tier_gb, int)
                                or str(tier_gb) != tier):
        raise ValueError(
            f"the reference policy tier {tier!r} disagrees with its band {tier_gb!r}")
    revision = view.get("revision")
    if (not isinstance(revision, str) or len(revision) != 64
            or any(char not in "0123456789abcdef" for char in revision)):
        raise ValueError("the producer's reference policy states no valid mode token")
    covers = view.get("revision_covers")
    frozen = memory_admission._REVISION_COVERS_MODE
    if covers != frozen:
        raise ValueError(
            "the producer's reference policy does not cover the exact mode scope "
            f"({', '.join(frozen)})")
    ram = snapshot.get("ram")
    ram_modes = ram.get("modes") if isinstance(ram, dict) else None
    ram_view = ram_modes.get(mode) if isinstance(ram_modes, dict) else None
    if (not isinstance(ram_view, dict) or ram_view.get("model") != producer_model
            or ram_view.get("mode") != mode or ram_view.get("revision") != revision
            or ram_view.get("revision_covers") != covers):
        raise ValueError(
            f"the reference policy revision and scope do not match the producer's own "
            f"{mode!r} RAM snapshot")
    rows = view.get("rows")
    if not isinstance(rows, list):
        raise ValueError("the producer's reference policy states no count rows")
    exact = next((row for row in rows if isinstance(row, dict)
                  and type(row.get("reference_count")) is int
                  and row.get("reference_count") == count), None)
    generic = next((row for row in rows if isinstance(row, dict)
                    and row.get("reference_count") is None), None)
    row = exact if exact is not None else generic
    if row is None:
        raise ValueError(
            f"the producer's reference policy has no row for {count} reference image(s) "
            "and no generic fallback")
    effective = row.get("effective")
    if not isinstance(effective, dict):
        raise ValueError("the selected reference row states no effective policy")
    if "allowed" not in effective:
        raise ValueError("the selected reference row does not state whether references are allowed")
    allowed = effective["allowed"]
    if allowed is not None and type(allowed) is not bool:
        raise ValueError("the selected reference row's allowed value is not a boolean")
    if allowed is None:
        raise ValueError(
            "the selected reference row does not state whether references are allowed (unknown)")
    if allowed is False:
        raise ValueError("the selected node's reference policy refuses reference images")
    maximum = effective.get("max_reference_images")
    if maximum is None:
        raise ValueError(
            "the selected reference row does not state a reference-image cap (unknown)")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ValueError("the selected reference row's reference-image cap is not an integer >= 0")
    swap = effective.get("swap_allowance_gb")
    if swap is None:
        raise ValueError(
            "the selected reference row does not state a swap allowance (unknown)")
    if (isinstance(swap, bool) or not isinstance(swap, (int, float))
            or not math.isfinite(swap) or swap < 0):
        raise ValueError(
            "the selected reference row's swap allowance is not a finite number >= 0")
    intrinsic = row.get("max_intrinsic", view.get("intrinsic_max"))
    if intrinsic is not None:
        if isinstance(intrinsic, bool) or not isinstance(intrinsic, int) or intrinsic < 0:
            raise ValueError("the selected reference row's intrinsic cap is invalid")
        # The engine's intrinsic cap may only lower the Hub ceiling, never raise it.
        maximum = min(maximum, intrinsic)
    if maximum < count:
        raise ValueError(
            f"{count} reference image(s) sent; the selected node's policy allows at most {maximum}")
    return {
        "model": selected_model,
        "mode": mode,
        "node_id": node_id,
        "physical_tier": tier,
        "reference_count": count if exact is not None else None,
        "revision": revision,
        "effective": {"allowed": True, "max_reference_images": maximum,
                      "swap_allowance_gb": swap},
    }


async def _image_reference_files(client: httpx.AsyncClient, entry: dict | None,
                                 refs: list, mode: str) -> list:
    """The multipart parts for an image reference job, in the order the request named them.

    ``edit`` takes N references as repeated ``images`` parts, against the count window the worker
    published; ``img2img`` keeps its single ``image`` part. A worker that advertises `edit`
    without a count window is refused rather than sent a set nobody stated it can take - guessing
    is how a reference becomes a silent text-to-image render.
    """
    if mode != "edit":
        img_bytes, mime = await _resolve_reference(client, refs[0])
        # Video Studio's one source image is a `file` part; Image Studio's img2img one is `image`.
        part = "file" if mode == "img2video" else "image"
        return [(part, (f"reference{_ext(mime)}", img_bytes, mime))]
    limits = _reference_limits(entry)
    minimum, maximum = limits.get("min"), limits.get("max")
    if not isinstance(minimum, int) or not isinstance(maximum, int):
        raise ValueError(
            f"model advertises edit but publishes no reference count window "
            f"(min/max) in its lane contract; refusing {len(refs)} reference image(s)")
    if not minimum <= len(refs) <= maximum:
        raise ValueError(
            f"{len(refs)} reference image(s) sent; this model takes {minimum} to {maximum}")
    files = []
    for index, ref in enumerate(refs):
        img_bytes, mime = await _resolve_reference(client, ref)
        files.append(("images", (f"reference{index}{_ext(mime)}", img_bytes, mime)))
    return files


async def _reference_dispatch(client: httpx.AsyncClient, studio: dict, batch: dict,
                              entry: dict | None, refs: list, ref_mode: str | None,
                              supported_modes: tuple[str, ...],
                              policy_out: dict | None = None) -> tuple[str, str, dict, list]:
    """``(mode, url, headers, files)`` for one reference job, or ``ValueError`` naming why not.

    One function so the decision is testable without a running dispatcher: which mode this worker
    can serve, the endpoint that serves it, and the multipart parts. A worker that advertises
    neither mode is refused here, before anything is posted.

    An ``edit`` job also carries the selected node's own ``reference_policy`` snapshot. The
    annotation is authoritative only when present; an older producer's absence keeps the lane
    contract path exactly as it was. ``policy_out`` receives the validated dispatch field.
    """
    caps = (entry or {}).get("capabilities") or []
    mode = ref_mode or next((name for name in supported_modes if name in caps), None)
    if not mode or mode not in supported_modes or mode not in caps:
        raise ValueError(
            f"model {batch.get('model')} does not support reference images "
            f"(needs {'/'.join(supported_modes)} capability)")
    if mode == "edit":
        snapshot = _producer_reference_policy(studio, entry, str(batch.get("model") or ""))
        if snapshot is not _REFERENCE_POLICY_ABSENT:
            policy = _validated_reference_policy(
                snapshot, studio, str(batch.get("model") or ""), entry, mode, len(refs))
            if policy_out is not None:
                policy_out["reference_policy"] = policy
    files = await _image_reference_files(client, entry, list(refs), mode)
    path = "/api/generate/video2video" if mode == "img2video" else f"/api/generate/{mode}"
    url, headers = studio_request(studio, path)
    return mode, url, headers, files


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
    # Video is a LONG job — 15 to 45 minutes by the local video runtime's own measured runs on an
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

# Queue order is a per-Mac function rank followed by stable FIFO. Only work that
# passes its ordinary routing, catalog, qualification, memory, and pause gates
# competes, so an ineligible preference never holds the Mac idle. Running work is
# not preempted.
# Final render is one of the ranked functions (owner, 2026-10-28): it takes its
# place in the same saved order as every other, through `_priority_key`, and a Mac
# whose saved order never mentioned it still reads it first
# (`registry.with_render_default`, to a non-empty order). `render_wins_next_free` applies the same
# rank at the lease, so the two halves of arbitration cannot disagree.
MAX_TRIES = 3
MAX_INFRA_TRIES = 8
INFRA_RETRY_WINDOW_S = 30 * 60
POLL_S = 2.0
# The shortest a dropped attempt is reconciled for. It used to be the whole window, under a comment
# claiming it covered a slow M1 generation: it did not — a measured Breeze section took 1,747 s and
# the request cap is 900 s on the slowest Mac, so a healthy worker was abandoned by design. The
# window now comes from the lane's own published deadline (`_recovery_budget_s`); this is its floor.
RECOVERY_WINDOW_S = 120.0
# A worker that stops answering leaves nothing to reconcile: stop waiting after this long of
# consecutive failed polls, inside the lane's own ceiling, and let the transport branch name it.
RECOVERY_UNREACHABLE_S = 60.0
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
# The same rule for the admission wait (the Hub's own free-memory floor, checked before a job is sent):
# a job that has waited this long (~4 minutes, like three refusals with back-off) for a Mac whose free
# memory is short - while none of our own work is running on that Mac - is handed back typed
# INSUFFICIENT_MEMORY with the needs-X/has-Y sentence, so the Gateway can re-route it. A job waiting
# behind our own running work on the Mac is ordinary queueing and keeps waiting.
ADMISSION_WAIT_HANDBACK_S = 240.0
# The same wait when another Mac in this job's pool (`pool_machines`) offers the model: the job has
# somewhere else to go, so it is handed back sooner. 240 s stays for a Mac that is the only one.
# ponytail: 60 s is a fixed guess (the 2026-10-09 review: 304 job-minutes lost to 240 s waits on 8 GB
# Macs whose free memory did not come back); measure the time-to-recover if hand-backs ping-pong.
ADMISSION_WAIT_HANDBACK_ELSEWHERE_S = 60.0
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
# Workstation mode (`components/hub/workstation.py`): a shop Mac whose user is at the keyboard, or
# whose free memory is under its floor, stands by - a busy Mac, not a broken one - and one that stopped
# a job for its user hands the job back. A model it does not run is refused for longer.
WORKSTATION_YIELDED = "WORKSTATION_YIELDED"
WORKSTATION_STANDING_BY_AVOID_S = 30.0
WORKSTATION_NOT_ALLOWED_AVOID_S = 600.0
_WORKSTATION_REFUSALS = {"WORKSTATION_STANDING_BY": WORKSTATION_STANDING_BY_AVOID_S,
                         "WORKSTATION_MODEL_NOT_ALLOWED": WORKSTATION_NOT_ALLOWED_AVOID_S}
# Every Mac of this group that could run the model is paused or drained (`every_capable_mac_paused`):
# a new batch is refused with this code (HTTP 503) and queued items are handed back with it, nothing
# charged, so the Gateway places the work in another group instead of it waiting here for ever.
NO_AVAILABLE_MACHINE = "NO_AVAILABLE_MACHINE"
NO_AVAILABLE_MACHINE_TEXT = ("No Mac in this group can take this right now — every Mac that "
                             "could run it is paused")
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
_render_executor = None
# The auxiliary queues share the same physical-machine lease. The legacy turn
# cache remains as a test/reset compatibility surface; ranked FIFO does not read
# or populate it.
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
# One lock for "is this machine free, and claim it": production lanes and Developer Mode tests
# make that check-and-claim atomically instead of racing between the check and the lease write.
_machine_lease_lock = threading.RLock()
# machine -> count of production memory preparations inside an await. A test must not claim the
# Mac between a lane's memory check and its late physical lease check.
_preparing_machines: dict[str, int] = {}
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
    the primary Hub to avoid a request that their own engine would reject.

    **`skip` is for a Mac that can never take the job; `wait` only for one that is short right
    now** — `min_total` answers that for the model's *tier*, and the free floor below answers it
    for the model's *live* floor.
    """
    min_total = mem.get("min_total")
    if min_total and host["total_gb"] < min_total:
        return ("skip", f"needs a ~{min_total}GB machine; this one has "
                        f"{host['total_gb']}GB — trying other machines")
    need_free = _required_free_memory(mem)
    # **A Mac whose whole RAM is below the free floor is not a candidate.** Free memory is always
    # strictly less than the machine's own RAM (macOS and the workers are resident), so a floor at
    # or above this Mac's shipped memory can never be met however idle it becomes — and `wait`
    # there parks the item behind a machine that will never clear it. Judged against the *class*
    # (`memory_class_gb`), not the raw total: a Mac reporting 8.59 GB is an 8 GB Mac and 8.4 GB of
    # free memory is not something it can produce.
    #
    # Real shape: the fleet is ~35 8 GB M1/M2 Macs (`research/owner-hardware.md`) and Fish Audio S2
    # Pro 4-bit publishes an 8.4 GB free-before-start floor; job `97bdce60` (126 attempts, 3,068
    # `INSUFFICIENT_MEMORY` hand-backs in `gateway-kh.log.1`) is the same defect one class up,
    # where the Gateway's only mitigation is a 15-minute in-memory cooldown
    # (`fleet.MEMORY_COOLDOWN_S`) that a restart forgets.
    class_gb = memory_class_gb(host["total_gb"])
    if class_gb is not None and need_free >= class_gb:
        return ("skip", f"needs ~{need_free:.1f}GB free; this is a {class_gb:g}GB Mac, whose whole "
                        "memory could never be free — trying other machines")
    effective_free = host["available_gb"] - reserved_gb
    if effective_free < need_free:
        return ("wait", f"waiting for memory: needs ~{need_free:.1f}GB, "
                        f"~{max(0.0, effective_free):.1f}GB free")
    return ("run", None)


def _disk_gate(host: dict | None) -> tuple[str, str | None]:
    """Whether this Mac may be offered work now, from the disk figures it already publishes.

    No probe: the snapshot the memory gate reads carries it too — this Mac's
    `resources.host_stats()` or the peer's `/api/hub/resources?local_only=true` — with
    `disk` = `disk_governor.snapshot(DATA_DIR)` and its own tier words (`stop`, `red`, `amber`,
    `green`). A Mac in `stop`/`red` has told the fleet its output storage cannot take another job;
    skip it, as memory is skipped. Unknown figures are not a refusal: the worker still refuses at
    the door and that refusal waits and avoids the Mac (`_is_disk_full_refusal`).
    """
    snapshot = (host or {}).get("disk")
    tier = str((snapshot or {}).get("state") or "") if isinstance(snapshot, dict) else ""
    free = (snapshot or {}).get("free_gb") if isinstance(snapshot, dict) else None
    if not isinstance(free, (int, float)) or isinstance(free, bool):
        free = (host or {}).get("disk_free_gb")
    if tier in {"stop", "red"}:
        amount = f" ({float(free):.1f} GB free)" if isinstance(free, (int, float)) else ""
        return ("skip", f"output storage is {tier}{amount}; empty the Trash Folder")
    return ("run", None)


def _disk_allows(b: dict, studio: dict, trace: list | None) -> bool:
    """Offer this studio to the batch only when its published disk can take another job."""
    decision, note = _disk_gate(_host_for_studio(studio))
    if decision == "run":
        return True
    b["governor_note"] = f"{studio['id']}: {note}"
    if trace is not None:
        _record_dispatch_gate(trace, studio, "disk_skip")
    return False


def _machine_has_active_work(studio: dict) -> bool:
    """Whether the Hub itself is running work on this studio's Mac (any studio on it)."""
    from . import chat_jobs, transcription_jobs
    machine = studio.get("machine", "local")
    active = set(_busy) | set(chat_jobs.busy_studios) | set(transcription_jobs.busy_studios)
    return any(row.get("id") in active for row in _monitor().registry
               if row.get("machine", "local") == machine) or studio.get("id") in active


def _admission_wait_expired(item: dict, studio: dict, mem: dict, note: str | None, now: float,
                            b: dict | None = None) -> bool:
    """Whether this item has waited ADMISSION_WAIT_HANDBACK_S on an unmet free-memory floor
    (ADMISSION_WAIT_HANDBACK_ELSEWHERE_S when another Mac in batch ``b``'s pool offers the model).

    The clock runs only while the shortage is real (the Mac's own free memory is below the floor, not
    merely below it after our reservations), the wait is the memory wait itself (not a busy sibling),
    and the Hub has no work of its own running on that Mac. Anything else resets it.
    """
    host = _host_for_studio(studio)
    try:
        short = (bool(host) and "waiting for memory" in str(note or "")
                 and float(host["available_gb"]) < _required_free_memory(mem)
                 and not _machine_has_active_work(studio))
    except (KeyError, TypeError, ValueError):
        short = False
    if not short:
        item.pop("admission_wait_started_at", None)
        return False
    started = item.setdefault("admission_wait_started_at", now)
    limit = ADMISSION_WAIT_HANDBACK_S
    if b is not None and pool_machines(b["modality"], b.get("model") or "",
                                       b.get("routing", "pool"),
                                       _batch_mode(b)) - {studio.get("machine", "local")}:
        limit = min(limit, ADMISSION_WAIT_HANDBACK_ELSEWHERE_S)
    return now - float(started) >= limit


def _local_gate(mem: dict, host: dict) -> tuple[str, str | None]:
    """Compatibility wrapper for the local unified-memory reservation."""
    return _memory_gate(mem, host, _reserved["gb"])


def _admission_requirements(model: str, entry: dict,
                            mode: str | None = None) -> dict | None:
    """Resolve the editable site policy used immediately before dispatch.

    ``mode='edit'`` asks for the model's reference lane, which has its own measured floors (a
    reference item peaks far above text-to-image); the lane's floors win where they are stricter.
    """
    if entry.get("is_cloud"):
        return None
    policy = memory_admission.describe(model, entry, mode=mode)
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


def _with_machine_floor(mem: dict | None, studio: dict) -> dict | None:
    """A workstation's own "free memory needed to start a job", when its owner set one, in place
    of the model's free-before-start floor (`components/hub/workstation.py`, `start_free_gb`)."""
    override = None if mem is None else peers.workstation_start_free_gb(studio.get("machine", "local"))
    return mem if override is None else {**mem, "min_free": override}


async def release_idle_siblings(client: httpx.AsyncClient, target: dict) -> dict:
    """Ask the idle Studios on the target Mac to release resident state.

    The target itself is asked too: a model it holds idle is one the incoming job's own load
    evicts anyway; left resident, it held an idle 8 GB Mac below a different model's free-memory
    floor until the job was handed back as INSUFFICIENT_MEMORY.

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


#: Models that get the whole Mac: every idle Studio on it releases its models before one starts, not
#: only when free memory is short (0.6.169). YuE2 peaks at ~10.7-11.9 GB and its port fails closed the
#: moment macOS memory pressure leaves "normal"; on a 16 GB Mac an idle Breeze, Whisper or image model
#: left resident tripped it mid-song (1100/1200, 2026-10-12). A busy Studio is never preempted: the job
#: waits its turn, as one generation per Mac already makes it.
CLEAR_MACHINE_MODELS = frozenset({"vanch007/mlx-Yue2-3B"})


async def prepare_machine_memory(client: httpx.AsyncClient, studio: dict,
                                 model: str, entry: dict,
                                 mode: str | None = None) -> tuple[str, str | None]:
    """Run admission, coordinated idle handoff, then one fresh admission check.

    ``mode='edit'`` applies the reference lane's own floors, so an edit item waits or skips a Mac
    that can take text-to-image but not a 12.44 GB reference render.
    """
    machine = studio.get("machine", "local")
    if developer_test_admission.held(machine) is not None:
        return "wait", "a Developer Mode test holds this Mac"
    if model in CLEAR_MACHINE_MODELS:
        with preparing_machine(machine) as preparing:
            if preparing.blocked:
                return "wait", "a Developer Mode test holds this Mac"
            handoff = await release_idle_siblings(client, studio)
            if handoff["busy"]:
                return "wait", "waiting for the other work on this Mac before clearing it for YuE2"
            if handoff["failed"]:
                # Not cleared is not a start (0.6.170: guaranteed, not best-effort): another Mac takes it.
                return "skip", ("could not clear this Mac for YuE2 (" + ", ".join(map(str, handoff["failed"]))
                                + " kept its models) — trying other machines")
            if handoff["released"] and not peers.is_local_studio(studio):
                peers.invalidate(machine)
                await peers.refresh(_monitor().registry, client)
    mem = _with_machine_floor(_admission_requirements(model, entry, mode=mode), studio)
    if mem is None:
        return "run", None
    is_local = peers.is_local_studio(studio)
    host = _host_for_studio(studio)
    if not host:
        return "run", None  # the worker's own MemoryGuard remains authoritative
    decision, note = _memory_gate(mem, host, _reserved["gb"] if is_local else 0.0)
    if decision != "wait":
        return decision, note

    with preparing_machine(machine) as preparing:
        if preparing.blocked:
            return "wait", "a Developer Mode test holds this Mac"
        handoff = await release_idle_siblings(client, studio)
        if handoff["busy"]:
            return "wait", "waiting for active sibling work on this machine"
        if not handoff["released"]:
            return decision, note
        if not is_local:
            peers.invalidate(machine)
            await peers.refresh(_monitor().registry, client)
    refreshed = _host_for_studio(studio)
    if not refreshed:
        return "wait", "idle sibling memory released; waiting for fresh RAM telemetry"
    decision, note = _memory_gate(
        mem, refreshed, _reserved["gb"] if is_local else 0.0)
    if decision == "wait":
        note = f"released {handoff['released']} idle sibling(s); {note}"
    return decision, note


def memory_admission_dispatchable(studio: dict, model: str, entry: dict,
                                  mode: str | None = None) -> bool:
    """Return whether current telemetry can admit this model without a wait.

    This is a cache/telemetry-only probe for the shared Chat/transcription
    turn arbiter. It deliberately does not release siblings or contact peers;
    the owning scheduler still performs ``prepare_machine_memory`` before its
    durable lease. Unknown telemetry remains dispatchable so the worker's own
    MemoryGuard remains authoritative, with the memory-failure turn fallback
    preventing the opposite lane from being stranded.
    """
    mem = _with_machine_floor(_admission_requirements(model, entry, mode=mode), studio)
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


#: **Prefer fast Macs for Qwen** (owner decision 2026-10-09). The Gateway (0.2.156,
#: `fleet.speed_ranked`) picks the *site* by expected finish time; this is the same rule, with the
#: same numbers, for the Mac *inside* this site. 20-step medians on 10-08: M2 Max 228 s, M4 600-610 s,
#: M2 16 GB 812 s, M1 16 GB 949 s. Every other model keeps `_placement_key`.
def speed_ranked(model: str | None) -> bool:
    return "qwen-image" in str(model or "").lower()


#: The fallback when no Mac of a class has history for the model: those medians by chip family.
_CHIP_SECONDS = {"M2 Max": 228.0, "M4": 605.0, "M2": 812.0, "M1": 949.0}
#: A Mac with neither history nor a known chip is assumed as slow as the slowest measured.
_UNMEASURED_S = 949.0
#: Half a job: how long, on average, the job a busy Mac is running still has to go.
_RUNNING_REMAINDER = 0.5
_SPEED_TTL_S = 60.0
_speed_cache: dict[str, tuple[float, list]] = {}
#: Unified-memory sizes Apple ships; a Mac reports its total in GB or GiB (16 GiB = 17.18 GB).
_MEMORY_CLASSES = (8, 16, 18, 24, 32, 36, 48, 64, 96, 128, 192, 256, 512)


def memory_class_gb(memory_gb: object) -> float | None:
    """The memory size this Mac shipped with — 8.59 GB of RAM is an 8 GB Mac, 17.18 a 16 GB one.

    A Mac reports its total in bytes/1e9, so every shipped size lands a little above its name
    (`_MEMORY_CLASSES`). This is the Hub's own vocabulary for a *class* of Mac, and the one number
    a free-memory floor can be judged against: an 8 GB Mac cannot have 8.4 GB free however idle it
    becomes, because macOS and the workers are resident. `None` when the total is not a number.
    """
    try:
        gb = float(memory_gb)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return float(max((c for c in _MEMORY_CLASSES if c <= gb + 0.5), default=round(gb)))


def mac_class(chip: object, memory_gb: object) -> str:
    """"M4 · 16 GB": the chip family without "Apple", and the shipped memory size (as the Gateway)."""
    family = str(chip or "").replace("Apple", "").strip() or "unknown chip"
    try:
        gb = float(memory_gb)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"{family} · ? GB"
    size = memory_class_gb(gb)
    return f"{family} · {size:g} GB"


def _speed_samples(model: str) -> list[tuple[str, float]]:
    """`(machine, seconds)` of this model's finished items on this Hub, read at most once a minute."""
    now = time.monotonic()
    cached = _speed_cache.get(model)
    if cached is not None and now - cached[0] < _SPEED_TTL_S:
        return cached[1]
    try:
        rows = ledger.load_finished_batches(500, payload_like="%qwen-image%")
    except Exception:  # noqa: BLE001 - no history is "unmeasured", never a failed dispatch
        rows = []
    machines = {s["id"]: s.get("machine", "local") for s in _monitor().registry}
    samples = []
    for b in rows:
        if b.get("model") != model:
            continue
        for item in b.get("items") or []:
            runtime = item.get("runtime_s", item.get("duration_s"))
            sid = str(item.get("studio") or "")
            if (item.get("state") == "done" and sid and not isinstance(runtime, bool)
                    and isinstance(runtime, (int, float)) and runtime > 0):
                machine = machines.get(sid) or (sid.split("@", 1)[1] if "@" in sid else "local")
                samples.append((machine, float(runtime)))
    _speed_cache[model] = (now, samples)
    return samples


def _mac_seconds(modality: str, model: str):
    """`machine -> expected seconds` for this model: its own median, else its class's, else its chip's."""
    import statistics

    samples = _speed_samples(model)
    studios = {s.get("machine", "local"): s for s in _monitor().registry
               if s.get("modality") == modality}
    classes: dict[str, str] = {}

    def klass(machine: str) -> str:
        if machine not in classes:
            host = (_host_for_studio(studios[machine]) if machine in studios else None) or {}
            classes[machine] = mac_class(host.get("chip"), host.get("total_gb"))
        return classes[machine]

    def seconds(machine: str) -> float:
        own = [value for m, value in samples if m == machine]
        if own:
            return statistics.median(own)
        known = klass(machine)
        if "unknown" not in known and "?" not in known:
            same = [value for m, value in samples if klass(m) == known]
            if same:
                return statistics.median(same)
        return _CHIP_SECONDS.get(known.split(" · ")[0], _UNMEASURED_S)

    return seconds


def _queued_ahead(batch: dict, item: dict) -> int:
    """Speed-ranked items queued on this Hub before this one, in dispatch (FIFO) order."""
    def key(b: dict, i: dict) -> tuple:
        return (b.get("created_at", 0), b.get("id", ""), i.get("index", 0))
    mine = key(batch, item)
    return sum(1 for b in list(batches.values())
               if not b.get("cancelled") and speed_ranked(b.get("model"))
               for i in b.get("items") or []
               if i.get("state") == "queued" and key(b, i) < mine)


def _wait_for_faster_mac(batch: dict, item: dict, studio: dict) -> bool:
    """A free Mac leaves a Qwen item for a busy faster one that would still finish it sooner.

    The Gateway's rule: a busy Mac finishes after half its own job plus the items queued ahead of
    this one (each given to whichever busy Mac would finish it first); the free Mac takes the item
    only when its own time beats that. Free faster Macs are not counted - the fastest-first order
    offered them the item already. A Mac whose owner switched Image off or put another function
    above Image is never waited for (0.6.156/0.6.157 priorities win).
    ponytail: whatever a busy Mac runs is taken as half a Qwen job, like the Gateway; read the
    running item's own clock if long non-Qwen work holds Qwen back.
    """
    model = batch.get("model")
    if not speed_ranked(model):
        return False
    modality = batch["modality"]
    here = studio.get("machine", "local")
    leased = busy_machines()
    seconds = _mac_seconds(modality, model)
    finish: dict[str, list[float]] = {}
    for machine, (other, _entry) in _pool_members(
            modality, model, batch.get("routing", routing_rule.POOL),
            _batch_mode(batch)).items():
        if machine == here or (other["id"] not in _busy and machine not in leased):
            continue
        # The *saved* order: this asks whether the owner put another function above this one on
        # that Mac, which is what makes waiting for it worthwhile. The read-time render default is
        # an arbitration rule, not a preference the owner stated, so it is not read here - and
        # `priority_order` is the function a caller may override to say what a Mac saved.
        order = priority_order(machine)
        if order and order[0] != modality:
            continue
        own = seconds(machine)
        finish[machine] = [own * (1 + _RUNNING_REMAINDER), own]
    if not finish:
        return False
    for _ in range(_queued_ahead(batch, item)):
        best = min(finish, key=lambda m: finish[m][0])
        finish[best][0] += finish[best][1]
    return min(done for done, _own in finish.values()) < seconds(here)


def _pool_members(modality: str, model: str, routing: str = "pool",
                  mode: str | None = None) -> dict[str, tuple]:
    """machine -> (studio, catalog row) for every distinct Mac that could run this model at all.

    A job's *pool*: capacity, not availability. A Mac counts while it is up, enabled, not in
    maintenance or cooling down, its worker is authoritative, the model is cached and not paused
    there, the routing rule allows it (a pin is a pool of one) and its RAM is not known to be below
    the model's requirement (`memory_admission.requirement_gb`; unknown RAM counts). Busy Macs
    count - they will free up. Reads cached catalogs only, and the last-good row rather than the
    fresh-only one, so a catalog that is briefly stale does not shrink the pool to nothing.

    ``mode='edit'`` requires the Mac to advertise the model's reference lane *and* to clear the
    edit floor, so a reference job's pool is the Macs that can actually serve it - the same rule
    the Gateway applies to a location.
    """
    mon = _monitor()
    members: dict[str, tuple] = {}
    for studio, entry in _model_rows(modality, model):
        machine = studio.get("machine", "local")
        if (machine in members or _paused(studio)
                or _machine_blocked(machine)
                or mon.status.get(studio["id"], {}).get("status") != "up"
                or not peers.dispatch_ready(studio)
                or not _can_run(studio, entry, model, routing, mode)):
            continue
        members[machine] = (studio, entry)
    return members


def _model_rows(modality: str, model: str) -> list[tuple[dict, dict]]:
    """(studio, catalog row) for every Studio whose last-good catalog lists this model."""
    mon = _monitor()
    if modality == "transcription":
        cache = getattr(mon, "_transcribe_cache", {})
        return [(studio, row) for studio in mon.registry if studio.get("modality") == modality
                for row in ((cache.get(studio["id"]) or (0, {}))[1] or {}).get("models", [])
                if isinstance(row, dict) and row.get("repo") == model]
    return [(m["studio"], m["entry"]) for m in mon.cached_catalog_entries(model, modality=modality)]


def _paused(studio: dict) -> bool:
    """The operator's "no new work here": the Mac or the Studio paused, or the Studio drained."""
    machine = studio.get("machine", "local")
    return (studio["id"] in _maintenance or not machine_enabled(machine)
            or not studio_enabled(machine, studio["id"]))


def _can_run(studio: dict, entry: dict, model: str, routing: str,
             mode: str | None = None) -> bool:
    """Whether this Studio could run the model at all once free - routing, files, RAM tier -
    whatever its pause, health or current load.

    ``mode='edit'`` also requires the worker to advertise the reference lane: a Mac whose worker
    does not publish `edit` cannot serve a reference job however much RAM it has.
    """
    if (not routing_rule.allows(studio, routing)
            or (routing == routing_rule.REMOTE and peers.is_local_studio(studio))
            or (routing == routing_rule.ASSIGNED
                and assigned_image_model(studio.get("machine", "local")) != model)
            or not entry.get("cached", is_cached(entry))
            or _model_block_note(studio, model, entry)
            or not peers.lite_mode_allows(studio, model)):
        return False
    if mode == "edit" and "edit" not in (entry.get("capabilities") or []):
        return False
    known = _known_memory_gb(studio)
    return known is None or known >= (memory_admission.requirement_gb(model, entry, mode=mode)[0]
                                      or 0.0)


def every_capable_mac_paused(modality: str, model: str | None,
                             routing: str = routing_rule.POOL) -> bool:
    """True when Macs that could run this model are known and every one of them is paused or
    drained, so work for it would wait here for ever (owner, 2026-10-04: never strand work).

    Offline, busy or cooling-down Macs still count as able to take it: they come back. Only a
    pause or a drain is "not allowed".
    """
    # ponytail: a Mac whose catalog this Hub has never seen (offline since it started) is unknown
    # and counts for nothing either way; with no known capable Mac at all, nothing is refused here.
    capable = [studio for studio, entry in _model_rows(modality, model or "")
               if _can_run(studio, entry, model or "", routing)]
    return bool(capable) and all(_paused(studio) for studio in capable)


def pool_machines(modality: str, model: str, routing: str = "pool",
                  mode: str | None = None) -> set[str]:
    """The Macs that could run this model at all - see `_pool_members`."""
    return set(_pool_members(modality, model, routing, mode))


def _pool_size(modality: str, model: str, routing: str = "pool",
               mode: str | None = None) -> float:
    """Pool size for ordering: a job nobody can run sorts last (infinity) and holds nothing up."""
    return len(_pool_members(modality, model, routing, mode)) or math.inf


def _broker_dispatchable_work(machine: str) -> list[tuple[str, float, str]]:
    """Broker work that can use ``machine`` now: modality, age, and identity."""
    now = time.time()
    found = []
    for b in list(batches.values()):
        if (b.get("cancelled") or b["modality"] == "render"
                or execution_identity.lease_expired(b.get("genstudio_execution"))):
            continue
        queued = [i for i in b["items"]
                  if i["state"] == "queued" and (i.get("retry_at") or 0) <= now]
        member = _pool_members(b["modality"], b.get("model") or "", b["routing"],
                               _batch_mode(b)).get(machine) if queued else None
        if member is None or admission_refusal(b):
            continue
        studio, entry = member
        fresh_reader = getattr(_monitor(), "scheduling_catalog_entries", None)
        if callable(fresh_reader):
            fresh = next((row.get("entry") for row in fresh_reader(
                b.get("model") or "", modality=b["modality"])
                if (row.get("studio") or {}).get("id") == studio.get("id")), None)
            if fresh is None:
                continue
            entry = fresh
        if (studio not in _eligible_studios(b["modality"], b["routing"], b.get("model"))
                or not _can_run(studio, entry, b.get("model") or "", b["routing"],
                                _batch_mode(b))
                or not _supports_genstudio_voice_evidence(b, studio)
                or not _catalog_matches_genstudio_revision(b, entry)
                or not memory_admission_dispatchable(studio, b["model"], entry, _batch_mode(b))
                or not any(_shared_voice_allows_studio(b, i, studio)
                           and _item_allows_studio(i, studio, now)
                           and not _wait_for_faster_mac(b, i, studio) for i in queued)):
            continue
        found.append((str(b["modality"]), float(b.get("created_at") or 0),
                      f"broker:{b['id']}"))
    return found


def _is_local_machine(machine: str) -> bool:
    """Whether `machine` is one of this Hub's own Macs (its registry), not a joined peer."""
    return any(s.get("machine", "local") == machine and peers.is_local_studio(s)
               for s in _monitor().registry)


def function_enabled(machine: str, modality: str) -> bool:
    """Whether this Mac takes ``modality`` at all — its work order's own on/off switch.

    **Rank decides *when*; this decides *whether*** (owner, 2026-10-28: "each generation should
    actually have a cross to switch them on or off. but defaults to on."). A switched-off kind is
    not taken by that Mac at all: not scheduled (`next_work`), not offered a Studio row
    (`_eligible_studios`), and not leased by any lane (`acquire_external_machine`).

    Read where it is owned: this Mac's own registry for a Mac this Hub runs, and the switch map a
    joined Mac publishes on its peer snapshot for one it does not. Absent, malformed or a
    non-boolean value reads as ON in both places — every Mac that never opened the control, and
    every Hub old enough not to publish it, takes work exactly as it did before.
    """
    from . import registry
    if _is_local_machine(machine):
        return registry.function_enabled("local", modality)
    snapshot = peers.cached(machine) or {}
    return (registry.normalize_function_enabled(snapshot.get(registry.FUNCTION_ENABLED_KEY))
            .get(modality) is not False)


def priority_order(machine: str) -> list[str]:
    """One Mac's saved order, exactly as stored — no read-time default applied.

    This is the function the work-policy API, the fleet rows, the compatibility projections and
    `_wait_for_faster_mac` read, so it keeps the meaning it has always had: what the owner saved,
    nothing more. Arbitration reads `ranked_priority_order` below, which adds final render's
    read-time default. Keeping the two apart is what lets render be rankable without rewriting a
    single stored order (owner, 2026-10-28).
    """
    from . import registry
    if _is_local_machine(machine):
        return registry.priority_order("local")
    snapshot = peers.cached(machine) or {}
    if "priority_order" in snapshot:
        return registry.normalize_priority_order(snapshot.get("priority_order"))
    return ["chat"] if snapshot.get("chat_priority") is True else []


def ranked_priority_order(machine: str) -> list[str]:
    """The order arbitration uses, with `render`'s read-time default applied.

    A Mac whose saved order names no final render ranks it first, which is what
    `acquire_external_machine` used to reserve unconditionally; a Mac that names it in its order
    gets the owner's ranking (owner, 2026-10-28). Nothing stored is rewritten.

    It is derived from `priority_order` rather than reading the flags again, and the one written
    copy of the rule is `registry.with_render_default`. Deriving rather than re-reading matters:
    arbitration and the API must agree about the *saved* half too, and a caller that overrides
    `priority_order` (the tests do, for a Mac whose flags they cannot write) must move the
    scheduler with it.
    """
    from . import registry
    return registry.with_render_default(priority_order(machine))


def preferred_policy(machine: str) -> dict | None:
    """One Mac's idle-hold preference in force, local from flags, peers from their snapshot.

    A peer that has not published the field (an older build) reads as OFF, exactly like the
    legacy priority-order fallback: no policy is invented for a Mac that did not say so.
    """
    from . import registry
    local = any(s.get("machine", "local") == machine and peers.is_local_studio(s)
                for s in _monitor().registry)
    if local:
        return preferred_hold.active(registry.preferred_work("local"))
    snapshot = peers.cached(machine) or {}
    policy = snapshot.get("preferred_work") if isinstance(snapshot, dict) else None
    return preferred_hold.active(policy)


def hold_remaining(machine: str, policy: dict | None = None) -> float:
    """Seconds of idle hold left on ``machine`` (0.0 when none)."""
    if policy is None:
        policy = preferred_policy(machine)
    if not policy:
        return 0.0
    return preferred_hold.hold_remaining_s(
        policy, registry_preferred_hold(machine, float(policy["idle_seconds"])))


def registry_preferred_hold(machine: str, window_s: float | None = None) -> dict | None:
    """The persisted hold for one Mac — local from this Hub's flags, a peer from its cache.

    A peer publishes only the *remaining seconds*, never its raw stamp: the node is the
    clock authority and its boot-scoped monotonic stamps are meaningless cross-machine.
    The peer answer is therefore rebuilt as a back-dated window whose arithmetic yields
    exactly the published remaining (itself already clamped by the node to its window);
    the next poll refreshes it from the node's own clock, so nothing here counts down.
    """
    from . import registry
    local = any(s.get("machine", "local") == machine and peers.is_local_studio(s)
                for s in _monitor().registry)
    if local:
        return registry.preferred_hold("local")
    snapshot = peers.cached(machine) or {}
    if not isinstance(snapshot, dict):
        return None
    remaining = snapshot.get("preferred_hold_remaining_s")
    if not isinstance(remaining, (int, float)) or isinstance(remaining, bool) \
            or remaining <= 0.0:
        return None
    elapsed = max(0.0, float(window_s or registry.PREFERRED_WORK_DEFAULT_S)
                  - min(float(remaining), float(window_s or registry.PREFERRED_WORK_DEFAULT_S)))
    return {"ended_wall": time.time() - elapsed,
            "boot_steady": time.monotonic() - elapsed}


def _note_preferred_completion(studio: dict, modality: str | None, *,
                               reservation_owner: str | None = None) -> None:
    """Open this Mac's hold window when one of its own preferred jobs just finished.

    Called exactly once per terminal transition of this Hub's own lanes. Off or malformed
    policy, a non-preferred modality and remote studios (their own Hub notes their
    completion) are all no-ops.

    ``reservation_owner`` names the dispatch attempt whose in-flight reservation belongs to
    this completion. The same atomic row write removes that reservation only while it still
    carries this exact owner, so a delayed older attempt can never clear a newer attempt's
    reservation. A late terminal may still open the scheduling hold under the frozen
    completion semantics; that hold is scheduling-only and never promises a warm model.

    The write is optional bookkeeping, so a persistence I/O failure is logged and
    swallowed: this helper runs inside lane finally blocks and peer release paths, and a
    full or unwritable data dir must never skip their own cleanup (busy/reserved/wakeup,
    pack save, peer lease release). Only ``OSError`` is treated this way — unrelated
    failures still propagate, and primary job persistence is untouched.
    """
    if not modality:
        return
    from . import registry
    machine = str(studio.get("machine") or "local")
    if not any(s.get("machine", "local") == machine and peers.is_local_studio(s)
               for s in _monitor().registry):
        return
    policy = preferred_hold.active(registry.preferred_work("local"))
    if policy and preferred_hold.demand_wins(policy, modality):
        try:
            registry.set_preferred_hold(
                machine, time.time(), time.monotonic(),
                clear_reservation_owner=reservation_owner)
        except OSError as exc:
            logging.getLogger("studiohub.broker").warning(
                "preferred-work hold write failed for %s/%s: %s", machine, modality, exc)


# Grace added to a lane's own deadline for the reservation budget. Inference, not a measured
# model limit: it covers the Hub<->worker round trip and the tiny delay between the Hub's
# completion observation and the worker's own release decision. The lane deadline itself is
# the authority (it already includes the lane's measured overhead and ceiling).
PREFERRED_RESERVATION_GRACE_S = 120.0


def _preferred_reservation_budget(modality: str, stated_deadline_s: object = None) -> float:
    """The bounded reservation budget for one lane: its own deadline plus grace.

    The floor comes from the Hub's one published per-generator deadline table
    (``standalone.deadline_floor_seconds``); Image's own stated deadline can raise the budget
    up to its worker-side ceiling (7,200 s), so the reservation tracks the actual lane bound
    instead of inventing a global job cap.
    """
    from .. import standalone   # lazy: one deadline table, and standalone imports this module
    floor = float(standalone.deadline_floor_seconds(modality))
    stated = 0.0
    if isinstance(stated_deadline_s, (int, float)) and not isinstance(stated_deadline_s, bool):
        value = float(stated_deadline_s)
        if math.isfinite(value) and value > 0.0:
            stated = value
    return round(max(floor, stated) + PREFERRED_RESERVATION_GRACE_S, 1)


def _begin_preferred_reservation(studio: dict, modality: str | None, *,
                                 owner: str | None = None) -> str | None:
    """Write a dispatch attempt's bounded reservation immediately before the worker POST.

    Returns the attempt token to pass to the terminal hook, or ``None`` when the lane is not
    preferred work on this Mac or the optional write failed (fail-safe: no exemption is
    recorded and the job runs under the worker's normal memory mode). Only ``OSError`` is
    swallowed; an unrelated failure still propagates. ``owner`` lets the peer door reuse its
    own per-lease token; the Hub lanes leave it unset and get a fresh local-only UUID.
    """
    if modality not in ("chat", "image", "voice"):
        return None   # Music has no warm model to hold; Video drops its pipeline per job
    from . import registry
    machine = str(studio.get("machine") or "local")
    if not peers.is_local_studio(studio):
        return None
    policy = preferred_hold.active(registry.preferred_work("local"))
    if not policy or not preferred_hold.demand_wins(policy, modality):
        return None
    token = owner or uuid.uuid4().hex
    try:
        registry.set_preferred_reservation(
            machine, modality, token, time.time(), time.monotonic(),
            _preferred_reservation_budget(modality))
    except OSError as exc:
        logging.getLogger("studiohub.broker").warning(
            "preferred-work reservation write failed for %s/%s: %s", machine, modality, exc)
        return None
    return token


def _extend_preferred_reservation(studio: dict, modality: str, owner: str | None,
                                  stated_deadline_s: object = None) -> None:
    """Adopt a larger worker-stated deadline once, keeping the dispatch-time start stamps.

    Only the attempt that still owns the reservation may extend it; the write is the same
    optional best-effort kind as the initial one.
    """
    if not owner:
        return
    from . import registry
    machine = str(studio.get("machine") or "local")
    current = registry.preferred_reservation(machine)
    if not current or current["owner"] != owner or current["modality"] != modality:
        return
    budget = _preferred_reservation_budget(modality, stated_deadline_s)
    if budget <= current["budget_s"]:
        return
    try:
        registry.set_preferred_reservation(
            machine, modality, owner, current["started_wall"],
            current["started_steady"], budget)
    except OSError as exc:
        logging.getLogger("studiohub.broker").warning(
            "preferred-work reservation adoption failed for %s/%s: %s",
            machine, modality, exc)


def chat_priority_enabled(machine: str) -> bool:
    """The legacy projection: whether Chat is the first function **the owner** ranked.

    Reads the saved order, never the arbitration order: the read-time render default would put
    render first on every Mac that never mentioned render, and this compatibility flag would then
    answer False on all of them (owner, 2026-10-28).
    """
    order = priority_order(machine)
    return bool(order and order[0] == "chat")


async def priority_chat_waiting(machine: str) -> bool:
    """Compatibility answer: whether Chat is the next eligible work on this Mac."""
    candidate = await next_work(machine)
    return candidate is not None and candidate[3] == "chat"


def _priority_key(machine: str, modality: str, created_at: float,
                  identity: str) -> tuple[int, float, str]:
    order = ranked_priority_order(machine)
    try:
        rank = order.index(modality)
    except ValueError:
        rank = len(order)
    return rank, float(created_at), str(identity)


async def next_work(machine: str) -> tuple[int, float, str, str] | None:
    """Best actually dispatchable queued function for one physical Mac.

    While a preference is enabled, eligible preferred demand wins over the other
    generation functions regardless of the ranked order (whose winner may name a
    third function and would otherwise starve the preference); non-generation lanes
    and OFF keep the exact ranked behaviour.
    """
    from . import chat_jobs, chat_priority, transcription_jobs
    candidates = [
        (*_priority_key(machine, modality, created_at, identity), modality)
        for modality, created_at, identity in _broker_dispatchable_work(machine)
    ]
    direct = chat_priority.oldest_ready(machine)
    if direct is not None:
        candidates.append((*_priority_key(
            machine, "chat", direct.get("created_at", 0),
            f"direct-chat:{direct['id']}"), "chat"))
    chat = (await chat_jobs.dispatchable_candidate(_monitor(), machine)
            if chat_jobs.has_queued_work() else None)
    if chat is not None:
        candidates.append((*_priority_key(machine, "chat", chat[0], f"chat:{chat[1]}"),
                           "chat"))
    transcription = (await transcription_jobs.dispatchable_candidate(_monitor(), machine)
                     if transcription_jobs.has_queued_work() else None)
    if transcription is not None:
        candidates.append((*_priority_key(
            machine, "transcription", transcription[0],
            f"transcription:{transcription[1]}"), "transcription"))
    render = (await _render_executor.dispatchable_candidate(machine)
              if _render_executor is not None else None)
    if render is not None:
        candidates.append((*_priority_key(
            machine, "render", render[0], f"render:{render[1]}"), "render"))
    # **A kind switched off is not a candidate at all** (owner, 2026-10-28), whichever lane it
    # came from — broker, direct Chat, either queue or render. This is the scheduler half; the
    # lease half is `acquire_external_machine`, so a lane cannot slip past this by asking first.
    candidates = [candidate for candidate in candidates
                  if function_enabled(machine, candidate[3])]
    policy = preferred_policy(machine)
    if policy:
        preferred = [c for c in candidates if c[3] == policy["modality"]]
        others = [c for c in candidates if c[3] in preferred_hold.GENERATION_CHOICES
                  and c[3] != policy["modality"]]
        if preferred and others:
            return min(preferred)
    return min(candidates, default=None)


async def higher_priority_work_waiting(machine: str, modality: str,
                                       created_at: float, identity: str) -> bool:
    winner = await next_work(machine)
    if winner is None:
        return False
    if winner[:3] < _priority_key(machine, modality, created_at, identity):
        return True
    # The idle hold: a different generation function may not take this Mac while the preferred
    # function wins. Render never reaches this gate — the broker's own loop skips it for the
    # render lane (a batch loop, not this one), and `RenderExecutor.dispatch_ready` asks it only
    # for a *render* candidate, which the check above has already ranked. A preferred candidate
    # itself is never gated: it is the winner being reserved for.
    if modality in preferred_hold.GENERATION_CHOICES \
            and candidate_loses_hold(machine, modality, winner[3]):
        return True
    return False


def candidate_loses_hold(machine: str, candidate: str, winner: str | None = None) -> bool:
    """Whether the idle hold blocks ``candidate`` on ``machine`` while ``winner`` runs next."""
    policy = preferred_policy(machine)
    if not policy or candidate == policy["modality"]:
        return False
    if candidate not in preferred_hold.GENERATION_CHOICES:
        return False
    if winner is None:
        return hold_remaining(machine, policy) > 0.0
    if winner == policy["modality"]:
        return True  # eligible preferred demand: the ranked winner says so
    # The ranked winner is some third function: the hold still refuses the candidate unless
    # the hold window has expired (then ranked order applies unchanged).
    return hold_remaining(machine, policy) > 0.0


async def scarcer_work_waiting(machine: str, pool: float, own_lane: str) -> bool:
    """Deprecated compatibility shim for pre-ranked internal callers.

    There are no production callers. Ranked FIFO uses
    ``higher_priority_work_waiting`` with the candidate's exact age and stable id.
    """
    return False


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


def _reset_infra_episode(item: dict) -> None:
    """A successful attempt ends the transport-failure episode.

    The two counters used to live for the whole item, so one drop after a failure from hours earlier
    ended a row for a reason that no longer existed. Run from every success route (a completed
    generation, an adopted abandoned attempt, a clean terminal poll — all through
    `_record_worker_success`).
    """
    item.pop("infra_failures", None)
    item.pop("infra_failure_started_at", None)


def _record_infra_failure(item: dict, studio: dict, message: str, now: float) -> str:
    """Count one transport failure inside its episode; return ``"retry"`` or ``"terminal"``.

    An episode is a run of consecutive failures. A failure older than `INFRA_RETRY_WINDOW_S` starts
    a fresh one rather than ending the item, so an item ends only for a reason that exists now. The
    terminal record carries what decided it (`infra_failures`, `infra_failure_started_at`,
    `terminal_clause`) so a later reader can audit the verdict.
    """
    previous = item.get("infra_failure_started_at")
    started = (float(previous) if isinstance(previous, (int, float))
               and not isinstance(previous, bool) else 0.0)
    if not started or now - started >= INFRA_RETRY_WINDOW_S:
        failures = 0            # the previous failure expired: this is a new episode
        started = now
    else:
        failures = int(item.get("infra_failures") or 0)
    failures += 1
    item["infra_failures"] = failures
    item["infra_failure_started_at"] = started
    item.setdefault("avoid_machines", {})[
        studio.get("machine", "local")] = now + FAILED_WORKER_AVOID_S
    if failures < MAX_INFRA_TRIES:
        item["state"] = "queued"
        item["error"] = (f"connection failure {failures}/{MAX_INFRA_TRIES}; "
                         f"recovering automatically: {message}")
        delay_index = min(failures - 1, len(INFRA_RETRY_DELAYS_S) - 1)
        item["retry_at"] = now + INFRA_RETRY_DELAYS_S[delay_index]
        return "retry"
    item["state"] = "error"
    item["error"] = f"connection recovery exhausted: {message}"
    item["terminal_clause"] = "max_infra_tries"
    item["retry_at"] = None
    return "terminal"


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
    # A pause recorded under an older classifier is re-read with today's: one whose evidence no
    # longer counts against the model (2026-10-04: a "***" section made Qwen 0.6B write no audio,
    # and three such jobs paused it on studio-mac-1305 and -1408) is lifted, and said so. A real
    # engine or model-file failure still counts, so its pause stays.
    lifted = [key for key, row in _model_protection.items()
              if row.get("blocked") and not _counts_against_model(
                  str(row.get("reason") or ""), row.get("error_code"))]
    for key in lifted:
        row = _model_protection.pop(key)
        logging.getLogger("studiohub.broker").warning(
            "lifted the pause on %s for %s: its recorded failure is the input, not the model (%s)",
            key[1], key[0], str(row.get("reason") or "")[:240])
    if lifted:
        _save_model_protection()


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


def _is_capacity_failure(message: str) -> bool:
    value = message.lower()
    return any(token in value for token in (
        "memoryguarderror", "memory guard paused", "waiting for memory",
        "not enough memory", "insufficient memory",
    ))


# The Voice splitter never sends a section under 12 spoken characters to a model (fix/tiny-section);
# a worker from before it reports "produced no audio. 3-character section" as a RuntimeError.
_TINY_SECTION_NO_AUDIO = re.compile(r"produced no audio\. (\d+)-character section")
_TINY_SECTION_CHARACTERS = 12


def _counts_against_model(message: str, code: str | None) -> bool:
    """Whether a worker error is evidence against the model, not its input or this Mac's memory.

    The one classifier for both a new failure (`_classify_worker_failure`) and a stored pause
    (`_load_model_protection`), so a pause can never outlive the rule that made it.
    """
    if _is_capacity_failure(message):
        return False
    if _model_file_failure(message, code):
        return True
    tiny = _TINY_SECTION_NO_AUDIO.search(message)
    if tiny and int(tiny.group(1)) < _TINY_SECTION_CHARACTERS:
        return False
    return message.startswith("RuntimeError:") and "ValueError:" not in message


_load_model_protection()


# The two sentences that refuse a job for memory, each carrying the numbers it decided on: the Hub's
# own gate (`_memory_gate`) and the worker's MemoryGuard (Voice `_memory_preflight`).
_MEMORY_DECISION_NUMBERS = (
    re.compile(r"needs ~([\d.]+)\s*GB, ~([\d.]+)\s*GB free"),
    re.compile(r"([\d.]+) GB free is needed, but only ([\d.]+) GB is available"),
)


def _insufficient_memory_text(b: dict, studio: dict, decision: str, failures: int | None = None,
                              waited_s: float | None = None) -> str:
    """The sentence an operator (and the Gateway's user) reads when a Mac hands a job back: which Mac,
    how much it needs and how much it has.

    The numbers are the ones the refusal itself was decided on (``decision`` is that refusal's own
    sentence). They used to be looked up again afterwards - the Hub's policy and its cached
    snapshot of the Mac - so 116 of 272 hand-backs on 2026-10-04 read "needs about 3.1 GB free and
    has about 4.5 GB free": the worker refused on its own fresh reading, and the sentence printed a
    snapshot from another moment (a failed Voice job also unloads its model, which frees memory)."""
    machine = label_for(studio.get("machine", "local"))
    match = next(filter(None, (pattern.search(str(decision or ""))
                               for pattern in _MEMORY_DECISION_NUMBERS)), None)
    numbers = (f"needs about {float(match[1]):.1f} GB free and has about {float(match[2]):.1f} GB free"
               if match else "does not have enough free memory")
    minutes = max(1, round(waited_s / 60)) if waited_s is not None else 0
    why = (f"it waited {minutes} minute{'s' if minutes != 1 else ''}" if waited_s is not None
           else f"it refused {failures} times")
    return (f"{machine} {numbers} for {b['model']} ({why}). "
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
            and str(getattr(exc, "error_code", "") or "") in {"MACHINE_BUSY", *_WORKSTATION_REFUSALS})


def _another_machine_free(b: dict, machine: str) -> bool:
    """Whether a Mac other than ``machine`` could take this batch's item right now."""
    return any(studio.get("machine", "local") != machine
               for studio in _eligible_studios(b["modality"], b["routing"], b.get("model")))


def _is_disk_full_refusal_text(code: object, detail: object) -> bool:
    """Whether this typed refusal is the Mac's own output storage being full."""
    return (str(code or "") in {"OUTPUT_STORAGE_BLOCKED", "DISK_FULL"}
            or any(token in str(detail or "").lower() for token in
                   ("insufficient free space", "empty the trash folder", "disk is full")))


def _is_disk_full_refusal(exc: BaseException) -> bool:
    """Whether a Mac answered "my disk is full": HTTP 507, the OUTPUT_STORAGE_BLOCKED / DISK_FULL
    code (`standalone._peer_refusal_response` writes both for a full node), or the governor's own
    sentence when a worker answers it without a code. Nothing was submitted, so nothing was
    attempted."""
    return (getattr(exc, "status_code", None) == 507
            or _is_disk_full_refusal_text(getattr(exc, "error_code", None), str(exc)))


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


def configure_render_executor(executor) -> None:
    """Attach the final-render lane to this existing scheduler."""
    global _render_executor
    _render_executor = executor


def render_dispatch_allowed(machine: str, owner: str | None = None) -> bool:
    """Apply this broker's existing pause, Lite, drain, and quarantine gates to render.

    **The pause is the one gate an owner can exempt, and only this gate** (owner, 2026-10-28).
    A paused Mac means "run no generation here". Where the owner switches
    `registry.render_while_paused` on for that Mac, final render is the one function its pause no
    longer refuses. The exemption is exactly the pause: not Lite mode, not drain, not maintenance,
    not a quarantine. Off by default, so a Mac that never sets it behaves as it always has.
    """
    from . import registry
    from .vocabulary import RENDER_MODALITY
    if (not registry.function_enabled(machine, RENDER_MODALITY)
            or (not machine_enabled(machine) and not registry.render_while_paused(machine))
            or not studio_enabled(machine, "hub-final-render")
            or "hub-final-render" in _maintenance
            or _machine_blocked(machine)
            or peers.standing_by(machine)):
        return False
    return machine not in busy_machines() or _external_machine_leases.get(machine) == owner


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
    parameter = await _lane_parameter(studio, model, "output_format")
    if parameter is None:
        return False
    options = parameter.get("enum")
    return not isinstance(options, list) or value in options


async def _lane_parameter(studio: dict, model: str, name: str) -> dict | None:
    """The parameter ``name`` as this worker's own lane contract publishes it, or None."""
    entry = await _catalog_entry(studio, model)
    contract = (entry or {}).get("lane_contract")
    for parameter in (contract.get("parameters") if isinstance(contract, dict) else None) or ():
        if isinstance(parameter, dict) and parameter.get("name") == name:
            return parameter
    return None


# Optional per-job voice fields an older worker silently drops (so they are sent only to a worker
# that publishes them, and are not part of a recovered job's identity). Join pauses added 2026-10-03.
_OPTIONAL_LANE_FIELDS = ("output_format", "paragraph_pause_sec", "sentence_pause_sec")


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


#: Originals of attempts a Hub restart gave up on, waiting for their Mac to be in the registry so
#: `_watch_abandoned_attempt` can adopt or stop them: (batch, item, studio id, job id, body, t_start,
#: restored at). And batches a restart settled, still to be published by `_maybe_finish`.
_restored_attempts: list[tuple] = []
_restored_settled: list[dict] = []


def _rerun_after_restart(b: dict, it: dict) -> bool:
    """Free work a Hub restart interrupted runs again; it is never left `uncertain` (owner, 2026-10-05).

    The 2026-10-03 rule (a worker or Mac that dies mid-job re-runs its item) did not cover the Hub
    itself: on 2026-10-05 a controller restart fenced five Z-Image items whose nodes had already
    finished them, held those five Macs for the 30-minute sweep and parked the Gateway jobs as
    "Needs recovery". All Hub work is local and free (cloud is refused at the door), so it is
    requeued for any capable Mac, this one included, as `_abandon_attempt` does. The original is
    watched once its Mac is back in the registry (`_watch_restored_attempts`): adopted if it
    finished first (the re-run is then cancelled), told to stop if it is still running, ignored if
    the re-run finished first. The try it spent stays spent: an item that has used MAX_TRIES (a job
    that takes the Hub down every time) ends `error` / HUB_RESTARTED instead, so the Gateway gets a
    known failure and no Mac is held. A cancel in flight keeps its own path.
    """
    job_id = it.get("studio_job_id")
    if (b.get("cancelled") or it["state"] != "running"
            or b.get("modality") not in RECEIPT_DURABLE_MODALITIES):
        return False
    if job_id and it.get("studio"):
        _, prompt_field, _ = MODALITY[b["modality"]]
        body = {**(b.get("shared_params") or {}), **(it.get("params") or {}),
                "repo": b.get("model"), prompt_field: it.get("prompt")}
        if it.get("seed") is not None:
            body["seed"] = it["seed"]
        if b.get("modality") == "voice":   # the id the original was sent with, as `_run_item` built it
            body["client_request_id"] = (it.get("client_request_id")
                                         or f"studiohub:{b['id']}:{it['index']}")
        _restored_attempts.append((b, it, str(it["studio"]), str(job_id), body,
                                   float(it.get("run_started") or b.get("created_at") or 0),
                                   time.time()))
    if int(it.get("tries") or 0) >= MAX_TRIES:
        now = time.time()
        it.update(state="error", error_code="HUB_RESTARTED", retry_at=None, finished_at=now,
                  last_progress_at=now,
                  error=f"the Hub restarted while this was running, on its last of {MAX_TRIES} tries")
        _restored_settled.append(b)
        return True
    it.update(state="queued", studio=None, studio_job_id=None, retry_at=None,
              error="the Hub restarted while this was running; running it again")
    # The item keeps its client_request_id: if only this Hub restarted, the worker still holds the
    # job and the re-dispatch reattaches to it instead of generating it twice. If the worker
    # restarted too, its restored job answers `EXECUTION_IDENTITY_LOST` and the dispatch takes a
    # fresh id then (`_bump_rerun_id`).
    return True


async def _watch_restored_attempts(client: httpx.AsyncClient) -> None:
    """Publish what a restart settled, and hand each original it gave up on to
    `_watch_abandoned_attempt` once its studio is in the registry again."""
    while _restored_settled:
        await _maybe_finish(client, _restored_settled.pop())
    if not _restored_attempts:
        return
    by_id = {row.get("id"): row for row in _monitor().registry}
    for entry in list(_restored_attempts):
        b, item, studio_id, job_id, body, t_start, restored_at = entry
        studio = by_id.get(studio_id)
        if studio is None and time.time() - restored_at < ABANDONED_WATCH_S:
            continue  # its Mac has not answered the registry yet
        _restored_attempts.remove(entry)
        if studio is not None:
            _watch_abandoned_attempt(client, b, item, studio, job_id, body, t_start)


def restore_batches():
    """Reload unfinished batches after a Hub restart.

    Running work runs again (`_rerun_after_restart`); a Voice, Image, Music or Video item whose
    cancel was in flight stays fenced. Other modalities retain their historic retry behavior.
    """
    # A Developer Mode test that may still own a child process keeps the Mac out of every queue
    # restored below; its hold is restored from the same durable attempt ledger.
    developer_test_admission.restore_before_production()
    for b in ledger.load_unfinished_batches():
        if _expire_genstudio_batch(b):
            batches[b["id"]] = b
            ledger.save_batch(b)
            continue
        for it in b["items"]:
            if _rerun_after_restart(b, it):
                pass
            elif it["state"] in {"running", "cancel_requested"} and b.get("modality") == "voice":
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

    ``delivery_instruction`` (owner 2026-10-29) is lifted for the same reason: a
    per-request direction is one string for the whole batch, and a caller that
    puts it beside ``model`` must not have it silently dropped while the job
    still renders. Its name is unchanged on the way to the worker - the Hub
    renames nothing a worker states.
    """
    shared = dict(envelope.get("sharedParams") or {})
    for key in ("voice_reference_asset_id", "voice_library_id", "delivery_instruction"):
        value = str(envelope.get(key) or "").strip()
        if value and not str(shared.get(key) or "").strip():
            shared[key] = value
    return shared


# The image door's closed mode vocabulary: the wire values an image submission may state.
# The owner-visible labels live on the catalog's `image_modes` — `Text to image` and
# `Reference to image` — and these tokens are the wire spellings of exactly those two modes.
# A deliberate closed class beside `MODALITY`/`MODALITY_RETRY_CLASS`: an unknown value is
# refused by name at the door, before queueing, instead of being silently dropped by
# `_multipart_fields` (owner rule: no silent ignore of an incoming mode).
IMAGE_WIRE_MODES = ("txt2img", "edit")


def _image_item_refs(params: dict) -> list:
    refs = params.get("reference_images")
    return refs if isinstance(refs, (list, tuple)) and refs else []


def _image_mode_door_refusal(envelope: dict) -> str | None:
    """Refuse an image submission whose stated mode is unknown or contradicts its references.

    Checked at the public door for image only; video/music/voice contracts are untouched.
    A stated `mode`/`ref_mode` must be one of the two wire values, and when it is stated it
    must agree with the references the same item carries. Absent mode keeps the dispatch's
    own derivation (references ⇒ edit), so every existing sender is unchanged.
    """
    items = envelope.get("items")
    if not isinstance(items, list) or not items:
        return "items must be a non-empty list"
    if any(not isinstance(item, dict) for item in items):
        return "each image item must be an object"
    stated: list[tuple[str, object]] = []
    if envelope.get("mode") is not None:
        stated.append(("mode", envelope.get("mode")))
    for index, item in enumerate(items):
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        if params.get("reference_images") is not None and not isinstance(params["reference_images"], (list, tuple)):
            return f"items[{index}].params.reference_images must be a list"
        for key in ("mode", "ref_mode"):
            if params.get(key) is not None:
                stated.append((f"items[{index}].params.{key}", params.get(key)))
    for where, value in stated:
        if not isinstance(value, str) or value not in IMAGE_WIRE_MODES:
            return (f"unsupported image mode {value!r} at {where}; accepted modes: "
                    f"Text to image (txt2img), Reference to image (edit)")
    for index, item in enumerate(items):
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        # One canonical resolved mode per item: every declared value — envelope `mode`,
        # `params.mode`, `params.ref_mode` — must say the same thing before the references
        # are judged against it, so a declared contradiction fails loudly rather than
        # whichever single key an older check happened to read.
        resolved = envelope.get("mode")
        for key in ("mode", "ref_mode"):
            if params.get(key) is not None:
                resolved = params.get(key)
        declared = [(where, value) for where, value in stated
                    if where == "mode" or where.startswith(f"items[{index}].params.")]
        if declared and any(value != declared[0][1] for _where, value in declared[1:]):
            places = ", ".join(f"{where}={value!r}" for where, value in declared)
            return (f"image mode keys disagree ({places}); send one mode — Text to image "
                    "(txt2img) or Reference to image (edit) — for the whole submission")
        refs = _image_item_refs(params)
        if resolved == "edit" and not refs:
            return (f"Reference to image needs reference images; item {index} carries "
                    "none (send Text to image instead)")
        if resolved == "txt2img" and refs:
            return (f"Text to image cannot carry reference images; item {index} has "
                    "reference_images (use Reference to image)")
    return None


def _image_reference_door_refusal(model: str, counts: list[int] | None = None) -> str | None:
    """Refuse reference images no Mac here could serve, before queueing.

    Intrinsic support is read from the catalog's own `image_modes` (falling back to
    `lane_contract.references` for a row published before that field): a model with no
    reference lane is refused by name, and a supported model whose hosts are all unready
    or below the floor is refused with the producer's own `reason` — never mislabelled as
    unsupported. No last-known row means no answer, and dispatch keeps its existing
    refusal. Cache-only (`cached_catalog_entries`): the door never waits on a worker.

    When `counts` is supplied, each count must be allowed by at least one producer's own
    ``hub_memory_admission`` snapshot. A row without that annotation is the older-producer
    path and is unchanged. A present but invalid, unknown or refusing snapshot is loud for
    that count; it never becomes an allow and never falls back to controller-local policy.
    """
    try:
        matches = _monitor().cached_catalog_entries(model, modality="image")
    except Exception:
        return None  # an unreadable catalog is not a reason to invent a refusal
    entries = [(m.get("studio"), m.get("entry")) for m in matches
               if isinstance(m.get("entry"), dict)]
    if not entries:
        return None
    intrinsic = False
    available = False
    unavailable_reason = None
    missing_runtime_reason = None
    label = model
    wanted = list(dict.fromkeys(counts or []))
    servable = {count: False for count in wanted}
    policy_reasons: dict[int, str] = {}

    def judge_policy(studio, entry) -> None:
        try:
            snapshot = _producer_reference_policy(studio, entry, model)
        except ValueError as exc:
            for count in wanted:
                policy_reasons.setdefault(count, str(exc))
            return
        if snapshot is _REFERENCE_POLICY_ABSENT:
            for count in wanted:
                servable[count] = True
            return
        for count in wanted:
            try:
                _validated_reference_policy(snapshot, studio, model, entry, "edit", count)
            except ValueError as exc:
                policy_reasons.setdefault(count, str(exc))
            else:
                servable[count] = True

    for studio, entry in entries:
        label = str(entry.get("label") or label)
        readiness = entry.get("readiness") or {}
        if readiness.get("state") == "no_runtime_model":
            missing_runtime_reason = missing_runtime_reason or readiness.get("reason")
            continue
        modes = entry.get("image_modes")
        if isinstance(modes, list):
            edit = next((m for m in modes
                         if isinstance(m, dict) and m.get("id") == "edit"), None)
            if edit is None:
                continue  # text-only row: no intrinsic support stated
            intrinsic = True
            if not edit.get("available"):
                unavailable_reason = unavailable_reason or edit.get("reason")
                continue
        else:
            # Legacy row (published before `image_modes`): the reference contract is the
            # intrinsic statement, `capabilities` the live one.
            contract = entry.get("lane_contract")
            if not (isinstance(contract, dict) and "references" in contract):
                continue
            intrinsic = True
            if "edit" not in (entry.get("capabilities") or []):
                lane = entry.get("reference_lane")
                reason = lane.get("reason") if isinstance(lane, dict) else None
                unavailable_reason = unavailable_reason or reason
                continue
        available = True
        if wanted:
            judge_policy(studio, entry)
        elif counts is None:
            return None  # older callers ask only whether any Mac advertises the lane
    if not intrinsic:
        if missing_runtime_reason:
            return f"Reference to image is unavailable: {missing_runtime_reason}"
        return f"{label} does not support Reference to image; Text to image only"
    if not available:
        if unavailable_reason:
            return f"Reference to image is unavailable: {unavailable_reason}"
        return None  # intrinsic somewhere, availability undetermined — dispatch decides
    for count in wanted:
        if not servable[count]:
            reason = policy_reasons.get(count)
            return (f"Reference to image with {count} reference image(s) is unavailable"
                    + (f": {reason}" if reason else
                       "; no available Mac states a usable reference policy"))
    return None


def submission_shape_refusal(envelope: dict) -> dict[str, str] | None:
    """The one shape gate for every modality, before any len(), membership or dict() use.

    A wrong-typed field here used to raise inside this function and read as a bare HTTP 500
    (an unhashable modality, len() of an int, dict() of a string); this names the field instead.
    """
    modality = envelope.get("modality")
    if not isinstance(modality, str):
        return {"code": "INVALID_MODALITY", "detail": "modality must be a string"}
    model = envelope.get("model")
    if not isinstance(model, str) or not model.strip():
        return {"code": "INVALID_MODEL", "detail": "model (repo) is required"}
    items = envelope.get("items")
    if not isinstance(items, list) or not items:
        return {"code": "INVALID_ITEMS", "detail": "items must be a non-empty list"}
    if any(not isinstance(item, dict) for item in items):
        return {"code": "INVALID_ITEMS", "detail": "every item must be an object"}
    if any(item.get("params") is not None and not isinstance(item.get("params"), dict)
           for item in items):
        return {"code": "INVALID_ITEM_PARAMS", "detail": "every item's params must be an object"}
    shared = envelope.get("sharedParams")
    if shared is not None and not isinstance(shared, dict):
        return {"code": "INVALID_SHARED_PARAMS", "detail": "sharedParams must be an object"}
    return None


def submit_batch(envelope: dict) -> dict:
    with _submit_lock:
        return _submit_batch_locked(envelope)


def _submit_batch_locked(envelope: dict) -> dict:
    shape_refusal = submission_shape_refusal(envelope)
    if shape_refusal:
        return {"error": shape_refusal["detail"], "code": shape_refusal["code"]}
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
    # Image-only door checks, before any fence or idempotency record exists: an unknown or
    # self-contradictory mode, and reference images no Mac here could serve, fail here with
    # plain words rather than queueing work that cannot run (owner rule: loud at the door).
    if modality == "image":
        mode_refusal = _image_mode_door_refusal(envelope)
        if mode_refusal:
            return {"error": mode_refusal}
        if any(_image_item_refs(item.get("params") if isinstance(item.get("params"), dict)
                                else {})
               for item in items_in):
            reference_counts = sorted({
                len(_image_item_refs(item.get("params") if isinstance(item.get("params"), dict)
                                     else {}))
                for item in items_in
            } - {0})
            reference_refusal = _image_reference_door_refusal(
                envelope["model"], reference_counts)
            if reference_refusal:
                return {"error": reference_refusal}
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
        # `origin` is the door's answer to "who asked", not part of the work being asked for:
        # leaving it in would change the fingerprint of a batch queued before 0.6.212, so a retry
        # of that batch would be refused as "a different batch". Same rule as the fence below.
        fingerprint_payload = {
            key: value for key, value in envelope.items()
            if key not in ("clientRequestId", "origin")
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
    batch = {
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
        # Who asked, in the Hub's own words: stamped from the credential at the door, never from
        # the body. Absent on batches written before 0.6.212, and the app shows nothing for those.
        "origin": envelope.get("origin"),
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
    # Durable before public: the fence and the row are written while this batch is still a
    # local candidate, so a failure here publishes nothing, reloads nothing, and answers a
    # coded refusal the caller can retry with the same clientRequestId.
    execution_identity.bind_local_batch(prepared.evidence, batch_id)
    try:
        ledger.save_batch(batch)
    except BaseException:
        # The fence must not point at a batch that was never stored.
        try:
            execution_identity.bind_local_batch(prepared.evidence, "")
        except Exception as rollback:  # noqa: BLE001 - name the type, never its payload
            logging.getLogger("studiohub.broker").warning(
                "could not clear the execution fence for batch %s after a failed store: %s",
                batch_id, type(rollback).__name__)
        raise
    batches[batch_id] = batch
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


def drain_holds_group() -> bool:
    """True when this Mac drains for an update and its own work is done: the group is held too.

    A controller's app counts the work it dispatched to its nodes as its own (its batches), and its
    relaunch would cut that work short. So once nothing runs on this Mac any more, nothing new starts
    on the nodes either: what they run finishes, the count reaches zero and the controller installs.
    Without this a long queue kept some node busy for ever and the controller never updated (owner's
    rule 2026-10-03: stop new work, finish current work, install; never held back because there is
    work). This is the dispatch hold for the controller's own coordinated work while it updates, not
    a one-at-a-time install gate: every Mac installs on its own.
    """
    if not update_drain.draining():
        return False
    from . import chat_jobs, transcription_jobs
    registry = _monitor().registry
    local = {s.get("machine", "local") for s in registry if peers.is_local_studio(s)} | {"local"}
    working = set(_busy) | set(chat_jobs.busy_studios) | set(transcription_jobs.busy_studios)
    return not (any(peers.is_local_studio(s) for s in registry if s.get("id") in working)
                or local & set(_external_machine_leases))


def local_running_jobs() -> list[tuple[str, str]]:
    """(studio id, worker job id) of every job this queue is running on this Mac's own workers -
    what Lite Mode's loop may hand back under critical memory pressure (`workstation.tick`)."""
    local = {s["id"] for s in _monitor().registry if peers.is_local_studio(s)}
    return [(item["studio"], str(item["studio_job_id"])) for batch in list(batches.values())
            for item in batch.get("items") or []
            if item.get("state") == "running" and item.get("studio") in local
            and item.get("studio_job_id")]


def _is_controller() -> bool:
    from . import control_plane
    return control_plane.load_settings().get("role") == "controller"


def machine_lease_lock() -> threading.RLock:
    """The lock a check-and-claim of a physical machine must hold."""
    return _machine_lease_lock


def preparing_machines() -> set[str]:
    """Machines with production memory preparation inside an await right now."""
    return {machine for machine, count in _preparing_machines.items() if count > 0}


class preparing_machine:
    """Mark a production preparation window so a test cannot claim the Mac inside it.

    The test-hold check and the marker are one atomic step under the same machine lease lock a
    test uses to reserve, so a runner thread cannot slip between "no test" and "preparing". A
    caller that finds ``blocked`` must return without unloading anything.
    """

    def __init__(self, machine: str):
        self.machine = machine
        self.blocked = False
        self._marked = False

    def __enter__(self):
        with _machine_lease_lock:
            if developer_test_admission.held(self.machine) is not None:
                developer_test_admission.production_arrived(self.machine)
                self.blocked = True
                return self
            _preparing_machines[self.machine] = _preparing_machines.get(self.machine, 0) + 1
            self._marked = True
        return self

    def __exit__(self, *_exc):
        if self._marked:
            with _machine_lease_lock:
                count = _preparing_machines.get(self.machine, 0) - 1
                if count > 0:
                    _preparing_machines[self.machine] = count
                else:
                    _preparing_machines.pop(self.machine, None)
        return False


def _machine_release_state(item: dict) -> float | None:
    """A durably persisted automatic-release marker, or ``None``.

    Recovery owns the release metadata. The marker is a finite timestamp, never a
    bool: an unknown or malformed value counts as *not* released, so the machine
    stays fenced (a stale hold is safe, a wrongly freed one is not).
    """
    recovery = item.get("recovery")
    if not isinstance(recovery, dict):
        return None
    value = recovery.get("machine_released_at")
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def _uncertain_hold_counts(by_id: dict) -> dict[str, int]:
    """How many receipt-durable uncertain attempts currently pin each Mac."""
    counts: dict[str, int] = {}
    for batch in batches.values():
        if batch.get("modality") not in RECEIPT_DURABLE_MODALITIES:
            continue
        for item in batch.get("items") or []:
            if item.get("state") != "uncertain":
                continue
            # Automatic age-out durably released this attempt's reservation: the item stays
            # uncertain and reconcilable but no longer keeps its Mac from later work.
            if _machine_release_state(item) is not None:
                continue
            machine = item.get("machine") or (by_id.get(item.get("studio")) or {}).get("machine")
            if machine:
                counts[str(machine)] = counts.get(str(machine), 0) + 1
    return counts


# -- update hold (contract: _orchestra/youtube-stack/update-hold/CONTRACT.md, owner 2026-11-15) --
#: Monotonic deadline of the update hold; 0 when no hold is active. One in-memory float,
#: written nowhere: a restart resumes by itself, and an install that aborts mid-hold can
#: never leave this Hub refusing work.
_hold_until = 0.0


def dispatch_paused() -> bool:
    """True while the update hold keeps this Hub from claiming any new work."""
    return time.monotonic() < _hold_until


def pause_dispatch(seconds: float) -> None:
    """Fence every claim path for `seconds` — in-memory and self-expiring, never persisted."""
    global _hold_until
    _hold_until = time.monotonic() + seconds


def resume_dispatch() -> None:
    """Open the claim path at once (`DELETE /api/maintenance/hold`)."""
    global _hold_until
    was_held = dispatch_paused()
    _hold_until = 0.0
    if was_held:
        logging.getLogger("studiohub.broker").info(
            "update hold resumed: dispatch open again")


def in_flight() -> int:
    """Running work right now across the queues this Hub dispatches for."""
    total = sum(1 for b in list(batches.values()) for it in b.get("items") or []
                if it.get("state") in ("running", "cancel_requested"))
    from . import chat_jobs, transcription_jobs
    total += sum(1 for b in list(chat_jobs.batches.values()) for p in b.get("packs") or []
                 if p.get("state") == "running")
    total += sum(1 for b in list(transcription_jobs.batches.values())
                 for it in b.get("items") or [] if it.get("state") == "running")
    # A claimed render is an executor task; its store row already holds the durable attempt id.
    if _render_executor is not None:
        total += len(getattr(_render_executor, "_tasks", ()))
    return total


def unsettled_sends() -> list[str]:
    """In-flight ids a restart now would fence `uncertain` (unknown outcome) instead of
    re-dispatching — `restore_batches` is the authority mirrored here.

    A running item of a live batch keeps its queue row: the next start re-dispatches it or
    watches its persisted attempt, so it is followable and not listed. A cancel in flight is
    not: restore marks a `cancel_requested` or a cancelled batch's running voice/image/music/
    video item `uncertain`, and a cancelled batch's running transcription is `uncertain` when
    packaged. Chat restores running packs straight back to `queued` and a render row keeps its
    durable attempt id — neither can strand, so neither is listed."""
    left = []
    for b in list(batches.values()):
        if b.get("modality") not in RECEIPT_DURABLE_MODALITIES:
            continue  # any other running row restores as re-dispatchable `queued`
        for it in b.get("items") or []:
            state = it.get("state")
            if state not in ("running", "cancel_requested"):
                continue
            if state == "cancel_requested" or b.get("cancelled"):
                left.append(f"{b['id']}:{it.get('index')}")
    from . import transcription_jobs
    if transcription_jobs._packaged():
        for b in list(transcription_jobs.batches.values()):
            if b.get("cancelled"):
                for it in b.get("items") or []:
                    if it.get("state") == "running":
                        left.append(f"transcription:{b['id']}:{it.get('index')}")
    return left


def hold(timeout: float, pause_s: float) -> list[str]:
    """Take the update hold: close the claim path first, then wait up to `timeout` for
    stranding in-flight work to settle. Returns the unsettled ids — empty means a restart now
    would strand nothing (`safe: true`). Queued rows are never touched; running work runs on,
    and the pause expires by itself after `pause_s`."""
    pause_dispatch(pause_s)
    log = logging.getLogger("studiohub.broker")
    log.info("update hold taken: in_flight=%d, dispatch paused for %.0fs",
             in_flight(), pause_s)
    deadline = time.monotonic() + timeout
    left = unsettled_sends()
    while left and time.monotonic() < deadline:
        time.sleep(0.25)
        left = unsettled_sends()
    if left:
        log.warning("update hold unsettled: %s", ",".join(left))
    else:
        log.info("update hold settled: nothing a restart would strand")
    return left


def busy_machines() -> set[str]:
    """Physical machines holding a non-preemptive heavy-work lease."""
    by_id = {s["id"]: s for s in _monitor().registry}
    # A Developer Mode test holds the same lease production does; during an update drain the
    # drain answers below omit lease rows, so union the test hold explicitly.
    test_held = developer_test_admission.held_machines()
    if dispatch_paused():
        # Update hold: every Mac this Hub could dispatch to is fenced at once — local studios,
        # joined nodes and render leases — so no lane (broker, Chat, Subtitle, render, or a new
        # submission at the door) claims work while in-flight settles. Queued rows are untouched
        # (`every_capable_mac_paused` reads pause flags, not this), and running work runs on.
        return ({s.get("machine", "local") for s in by_id.values()} | {"local"}
                | test_held | set(_external_machine_leases))
    if update_drain.draining():
        # This Mac is finishing its work to install an update: its own machine is taken. A
        # controller's joined nodes are other Macs and keep taking work while this Mac's own work
        # finishes (it used to name every machine, so a draining controller left its idle nodes
        # idle for the whole drain) - then they stop too, see `drain_holds_group`.
        if drain_holds_group():
            return {s.get("machine", "local") for s in by_id.values()} | {"local"} | test_held
        return ({s.get("machine", "local") for s in by_id.values()
                 if peers.is_local_studio(s)} | {"local"} | test_held)
    occupied = set(_external_machine_leases) | test_held | {
        by_id[sid].get("machine", "local")
        for sid in _busy
        if sid in by_id
    }
    occupied |= set(_uncertain_hold_counts(by_id))
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


def lease_lane(owner: str) -> str:
    """The generation function a lease owner belongs to, or `""` when it does not say.

    Each lane mints its own owner string through `acquire_external_machine`, and the shapes are
    the ones already in this file, not a second vocabulary to keep in step:

    * `render:<job>` / `chat:<batch>:<i>` / `transcription:<batch>:<i>` — the lane, then identity;
    * `direct-chat:<id>` and `relay:chat:<uuid>` — Chat under two older spellings;
    * `peer:<worker>:<uuid>` — the peer door, whose worker id names the modality
      (`vocabulary.WORKER_FOR_MODALITY`'s inverse is `REQUIRED_WORKERS`), and a worker that names
      no modality is a lane this rule does not arbitrate.

    `""` means "this lease did not say", and the caller then keeps the old answer: a waiting
    render wins the next free moment. A lease that cannot be placed never loses priority to a
    render it does not outrank — the safe direction, because it is the behaviour this replaces.
    """
    from .vocabulary import PRIORITY_MODALITIES, RENDER_MODALITY, REQUIRED_WORKERS
    text = str(owner or "")
    for lane in (RENDER_MODALITY, "transcription"):
        if text.startswith(f"{lane}:"):
            return lane
    if text.startswith(("chat:", "direct-chat:", "relay:chat:")):
        return "chat"
    if text.startswith("peer:"):
        pieces = text.split(":", 2)
        return REQUIRED_WORKERS.get(pieces[1], "") if len(pieces) > 1 else ""
    head = text.partition(":")[0]
    return head if head in PRIORITY_MODALITIES else ""


def render_wins_next_free(machine: str, lane: str) -> bool:
    """Whether a waiting final render outranks `lane` for this Mac's next free moment.

    **Never interrupts:** a running job finishes regardless; this only decides who may take the
    Mac the moment it frees. The answer is one comparison of the two functions' ranks in this
    Mac's order (`_priority_key`), so render and every other lane obey the same saved preference.

    Before 2026-10-28 a waiting render won here unconditionally. The owner made render a rankable
    item on 2026-10-28 ("LLM, render, audio, transcript, image"), so a Mac that ranks Chat above
    render now gives the free moment to Chat. A Mac whose saved order does not mention render
    still reads render first (`registry.with_render_default`), so nothing changes for it.
    """
    from .vocabulary import DEFAULT_PRIORITY_ORDER, RENDER_MODALITY
    if lane == RENDER_MODALITY:
        return False   # the render lane itself is the one being let in
    if not _pending_render_for_machine(machine):
        return False
    order = ranked_priority_order(machine)
    # **An empty order keeps the old answer, and that is deliberate.** `[]` means "no preference",
    # where every function is equal and the oldest eligible work runs; it is also the state of a
    # Mac that has never saved anything, and before render was rankable such a Mac *did* reserve
    # the next free moment for a waiting render (`tests/test_render_physical_admission.py` pins
    # it). Both facts are today's behaviour, and the owner asked for today's behaviour unchanged
    # where the saved order does not mention render - so nothing moves here for an empty order.
    # Read from the one default order, not a bare `True`: the default is not repeated here.
    if not order:
        return RENDER_MODALITY in DEFAULT_PRIORITY_ORDER
    # A lane the order does not name sorts behind every function it does name, and render is named
    # here for every non-empty order (`ranked_priority_order` supplies the read-time default), so
    # an unlisted lane has render ahead of it.
    return order.index(RENDER_MODALITY) < order.index(lane) if lane in order else True


def acquire_external_machine(machine: str, owner: str) -> bool:
    """Atomically reserve a physical machine for another heavy-work queue.

    Never interrupts a running job. Between two waiting functions it is the Mac's ranked order
    that decides, which is why a waiting render only wins the next free moment where the order
    actually puts it above the lane asking (`render_wins_next_free`).

    **A lane whose kind this Mac switched off never gets the lease** (owner, 2026-10-28): the
    switch decides *whether*, the rank decides *when*, and a lane that may not run here at all
    cannot take the Mac for a moment either.
    """
    lane = lease_lane(owner)
    with _machine_lease_lock:
        held_by_test = developer_test_admission.held(machine)
        if held_by_test is not None and held_by_test != owner:
            # Production is asking for a Mac a test holds: stop the test before its next stage
            # and make production wait for verified release. It never interrupts the test child.
            developer_test_admission.production_arrived(machine)
            return False
        if lane and not function_enabled(machine, lane):
            return False
        if machine in busy_machines():
            return False
        if render_wins_next_free(machine, lane):
            return False
        _external_machine_leases[machine] = owner
        _wakeup.set()
        return True


def note_developer_test_production(machine: str) -> None:
    """A production queue has work for this Mac; let an active test stop before its next stage."""
    developer_test_admission.production_arrived(machine)


def restore_external_machine(machine: str, owner: str) -> None:
    """Restore one durable physical hold without replacing any owner."""
    with _machine_lease_lock:
        current = _external_machine_leases.get(machine)
        if current is not None and current != owner:
            raise RuntimeError(f"physical machine {machine!r} is already held by {current!r}")
        _external_machine_leases[machine] = owner
        _wakeup.set()


def external_dispatch_allowed(
        machine: str, lane: str, *, other_lane_has_work: bool) -> bool:
    """Compatibility shim; ranked FIFO arbitration now runs immediately before lease."""
    if lane not in _EXTERNAL_LANES:
        raise ValueError(f"unsupported external dispatch lane: {lane}")
    return True


def note_external_dispatch(machine: str, lane: str) -> None:
    """Deprecated compatibility hook; ranked FIFO has no alternating turn."""
    if lane not in _EXTERNAL_LANES:
        raise ValueError(f"unsupported external dispatch lane: {lane}")


def note_external_memory_block(machine: str, lane: str) -> None:
    """Wake ranked arbitration after a lane's memory gate blocks it."""
    if lane not in _EXTERNAL_LANES:
        raise ValueError(f"unsupported external dispatch lane: {lane}")
    _wakeup.set()


def _pending_render_for_machine(machine: str) -> bool:
    from .vocabulary import RENDER_MODALITY
    # A Mac that switched final render off in its own work order has no pending render, so a
    # waiting one neither reserves its next free moment nor outranks another lane there.
    if not function_enabled(machine, RENDER_MODALITY):
        return False
    if (_render_executor is not None
            and _render_executor.pending_for_machine(machine)):
        return True
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
    with _machine_lease_lock:
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
        "dispatch_trace": b.get("dispatch_trace"),
        "label": b.get("label"),
        "origin": b.get("origin"),
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


#: How long a cancelled image/music/video item may stay `cancel_requested` while its worker winds
#: down before the outcome is called uncertain. An image render cannot be interrupted mid-step, so
#: the worker answers `cancelled` only when the render ends (tens of seconds, minutes on an 8 GB
#: Mac); music stops at once.
CANCEL_CONFIRM_S = 600.0


def _await_worker_cancel(batch: dict, item: dict, studio: dict, *, lease_expired: bool,
                         cancel_confirmed: bool, phase: str) -> bool:
    """Settle what a cancel means for an accepted image/music/video item.

    **A confirmed stop signal is not an unknown outcome.** The worker took the DELETE and answers
    `cancelled` (or `done`, if it finished first) when it has stopped, exactly as a Voice worker
    does — so the item waits in `cancel_requested`, is polled to the worker's own terminal state,
    and ends `cancelled` with the Mac released the moment the worker says so. Marking every such
    cancel `uncertain` fenced the Mac for up to 30 minutes and filed a hold nobody could resolve
    (audit 2026-10-01).

    `uncertain` stays for the cases where the Hub truly cannot tell: the signal never landed, the
    GenStudio lease expired, or the worker did not confirm within `CANCEL_CONFIRM_S`.
    Returns True when the caller should keep polling.
    """
    if cancel_confirmed and not lease_expired and item.get("studio_job_id"):
        since = item.setdefault("cancel_requested_at", time.time())
        try:
            waited = time.time() - float(since)
        except (TypeError, ValueError):
            waited = 0.0
        if waited <= CANCEL_CONFIRM_S:
            if item.get("state") != "cancel_requested":
                item["state"] = "cancel_requested"
                ledger.save_batch(batch)
            return True
        reason = (f"{batch['modality'].title()} cancellation was requested {phase}, but the worker "
                  f"did not confirm it stopped within {int(CANCEL_CONFIRM_S)} s; the original "
                  "worker outcome must be reconciled.")
    else:
        reason = (f"{batch['modality'].title()} cancellation was requested {phase}; "
                  "the original worker outcome must be reconciled.")
    if item.get("state") != "uncertain":
        _mark_async_uncertain(item, studio, batch["modality"], reason)
    return False


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


# ───────────── bounded local recovery ─────────────

# `automatic_recovery` holds the policy — a measured stall budget per modality, and the durable
# record of what this Hub has already decided — for Image and Voice jobs this Mac runs itself.
# Nothing called it, so a local job whose own stage counter stopped moving was only *flagged*
# (`stall.py`, which deliberately never acts) and never stopped: the lane deadline the Hub
# publishes is the caller's to poll against, and a job this Mac's own page submitted has no
# caller to enforce it. This is the caller it was missing.
_RECOVERY: automatic_recovery.AutomaticRecoveryState | None = None


def _recovery() -> automatic_recovery.AutomaticRecoveryState:
    """The Hub's recovery state, beside its other durable state. Built on first use, not at
    import: the data directory is the process's to decide, as it is for every other store."""
    global _RECOVERY
    if _RECOVERY is None:
        _RECOVERY = automatic_recovery.AutomaticRecoveryState(DATA_DIR / "recovery.json")
    return _RECOVERY


async def _stop_a_stalled_local_job(client: httpx.AsyncClient, b: dict, item: dict,
                                    studio: dict, job: dict) -> bool:
    """Stop this Mac's own Image/Voice job once its own progress has been frozen too long.

    Only a local Studio: a node's job belongs to the node, whose own Hub runs the same check, and
    stopping it from here would fight the peer's own reconciliation. Returns True when the item is
    terminal, so the poll loop stops polling.
    """
    if not peers.is_local_studio(studio):
        return False
    modality = str(b.get("modality") or "")
    verdict = _recovery().observe(str(studio.get("id") or ""), modality, job)
    if not verdict.get("eligible"):
        return False
    reason = automatic_recovery.stall_reason(modality, verdict.get("no_progress_s") or 0.0)
    if not await _signal_worker_cancel(client, item):
        # The worker did not take the stop. Leave the item running so the next poll tries again;
        # ending it here would report a dead job while a live one still holds the Mac.
        return False
    _recovery().mark(str(studio.get("id") or ""), str(job.get("id") or ""),
                     "cancel_requested", reason)
    item.update(state="error", error=reason, error_code="STALLED",
                finished_at=time.time(), retry_at=None)
    ledger.save_batch(b)
    return True


_abandoned_peer_cancels: set[asyncio.Task] = set()
# How long an abandoned attempt is watched for: its Mac coming back to be told to stop, or with a
# finished result that beats the re-run. ponytail: one flat window; per-lane if a lane runs longer.
ABANDONED_WATCH_S = INFRA_RETRY_WINDOW_S


def _superseded(item: dict) -> bool:
    """True once an abandoned attempt's own finished result was adopted for this item: a re-run
    still in flight stands down (cancels its job and records nothing)."""
    return bool(item.get("superseded_by_original"))


def _abandon_attempt(client: httpx.AsyncClient, b: dict, item: dict, studio: dict,
                     body: dict, t_start: float) -> None:
    """Give up on an attempt whose Mac or worker went away, so the item runs again.

    Owner decision 2026-10-03: the fleet is the owner's own Macs and the work is free, so a node
    that goes silent, or a worker that dies or restarts mid-job, never leaves an item `uncertain`
    - every modality is re-run, on any capable Mac (the same one after a worker restart, whichever
    is free first). The original is watched in the background (`_watch_abandoned_attempt`): it is
    told to stop when its Mac answers again, and if it had already finished, its result is used
    and the re-run is cancelled instead.

    The item keeps its `client_request_id` on purpose. A voice worker answers a POST for an id it
    already holds with that job (`generation.py: start_txt2speech`), so the retry reattaches to the
    original instead of paying for a second generation — the drop cost one job, not two. Only a
    person asking for the work again (`retry_failed_item`) takes a fresh `:rerun-N` id, and so does
    a reattached id that comes back as the job a restarted worker could not resume.
    """
    job_id = item.pop("studio_job_id", None)
    item.pop("studio", None)
    if job_id:
        _watch_abandoned_attempt(client, b, item, studio, str(job_id), body, t_start)


def _bump_rerun_id(b: dict, item: dict) -> str:
    """Give this item a new `:rerun-N` request id: an explicit re-run, never a plain retry."""
    item["reruns"] = int(item.get("reruns") or 0) + 1
    item["client_request_id"] = f"studiohub:{b['id']}:{item['index']}:rerun-{item['reruns']}"
    return item["client_request_id"]


def _record_late_result(b: dict, item: dict, studio: dict, job: dict, log) -> None:
    """Keep a result that arrived after the item was terminal, beside the verdict, never over it.

    The row stays `error`/`cancelled` and a later reader can see that the worker did produce an
    artifact — evidence the old code dropped silently whenever the exhausted attempt's job finished
    moments later. Written once; the ledger save is the durable copy.
    """
    if item.get("late_result"):
        return
    late = {"studio": studio.get("id"), "studio_job_id": job.get("id"), "state": "done",
            "finished_at": job.get("finished_at") or time.time(),
            "recorded_at": time.time()}
    for field in ("bytes", "sha256", "media_type"):
        value = job.get(field)
        if value not in (None, ""):
            late[field] = value
    item["late_result"] = late
    if batches.get(b.get("id")) is b:
        ledger.save_batch(b)
    log.info("late result kept beside the verdict: batch %s item %s is %s, job %s finished",
             b.get("id"), item.get("index"), item.get("state"), job.get("id"))


def _watch_abandoned_attempt(client: httpx.AsyncClient, b: dict, item: dict, studio: dict,
                             job_id: str, body: dict, t_start: float) -> None:
    """Best effort, in the background: settle a job this Hub has given up on.

    Without it the abandoned job kept running unpolled, burning that Mac and holding its peer
    lease, while the item regenerated elsewhere. Each round asks the original for its job: a
    finished one is adopted if the item has no result yet (the re-run is cancelled), a live one is
    cancelled, and a Mac that does not answer is sent the cancel anyway and asked again later, for
    `ABANDONED_WATCH_S`. Works on a copy, so the item's own cancel fields stay a user cancel's.
    """
    probe = {"studio": studio.get("id"), "studio_job_id": job_id}
    log = logging.getLogger("studiohub.broker")

    async def watch() -> None:
        deadline = time.monotonic() + ABANDONED_WATCH_S
        delay = 2.0
        while True:
            job = None
            try:
                url, headers = studio_request(studio, f"/api/generate/jobs/{job_id}")
                response = await client.get(url, headers=headers, timeout=10.0)
                if response.status_code >= 400:
                    return  # the worker no longer has it, or refuses us: nothing to stop
                payload = response.json()
                job = payload.get("job") if isinstance(payload, dict) else None
            except Exception:
                job = None
            if isinstance(job, dict) and job.get("id") == job_id:
                state = job.get("state")
                if state == "done" and not job.get("error"):
                    if item.get("state") in ("queued", "running") and not b.get("cancelled"):
                        await _adopt_abandoned_attempt(client, b, item, studio, job, body, t_start)
                    elif item.get("state") in ("error", "cancelled"):
                        # Terminal is terminal (owner, 2026-11-15): the late answer sits beside the
                        # verdict as evidence, and never moves the row back to running or done.
                        _record_late_result(b, item, studio, job, log)
                    return
                if state in ("queued", "running"):
                    sent = await _signal_worker_cancel(client, probe)
                    log.info("abandoned job %s on %s: cancel %s", job_id, probe["studio"],
                             "sent" if sent else f"not sent ({probe.get('cancel_error')})")
                    if sent:
                        return
                else:
                    return  # failed, cancelled or unknown to it: nothing to stop or adopt
            elif job is None:
                try:
                    sent = await _signal_worker_cancel(client, probe)
                except Exception as exc:
                    sent, probe["cancel_error"] = False, str(exc) or type(exc).__name__
                log.info("abandoned job %s on %s: cancel %s", job_id, probe["studio"],
                         "sent" if sent else f"not sent ({probe.get('cancel_error')})")
                if sent:
                    return
            else:
                return
            if time.monotonic() + delay > deadline:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    task = asyncio.create_task(watch())
    _abandoned_peer_cancels.add(task)
    task.add_done_callback(_abandoned_peer_cancels.discard)


async def _adopt_abandoned_attempt(client: httpx.AsyncClient, b: dict, item: dict, studio: dict,
                                   job: dict, body: dict, t_start: float) -> None:
    """Finalize the original privately; publish its winner only after a durable write."""
    candidate = dict(item)
    for key in ("model_revision", "runtime_revision", "machine", "worker_id"):
        candidate.pop(key, None)  # the original needs its own execution evidence
    candidate.update(studio=studio.get("id"), studio_job_id=job["id"], retry_at=None, error=None)
    if b.get("modality") == "voice":
        candidate["client_request_id"] = body.get("client_request_id")
    try:
        if b.get("modality") == "voice":
            _validate_voice_worker_job(candidate, job, expected_request_id=body.get("client_request_id"))
        elif b.get("modality") in DURABLE_ASYNC_MODALITIES:
            _validate_durable_worker_job(b["modality"], candidate, job,
                                         remote=not peers.is_local_studio(studio))
        await _record_worker_success(client, b, candidate, studio, job, body, t_start,
                                     adoption_item=item)
        if (candidate.get("state") != "done" or b.get("cancelled")
                or item.get("state") not in ("queued", "running") or _superseded(item)):
            return
        # No await between the winner recheck, durable publication and in-memory
        # commit: a replacement finishing during metadata validation wins first.
        rerun = {"studio": item.get("studio"), "studio_job_id": item.get("studio_job_id")}
        was_running = item.get("state") == "running"
        # A replacement may have been dispatched while metadata was awaited. Its
        # task still owns these counters and must release its current reservation.
        for key in ("tries", "_reserved", "_dispatch_generation"):
            candidate.pop(key, None)
            if key in item:
                candidate[key] = item[key]
        candidate["superseded_by_original"] = True
        ledger.save_batch({**b, "items": [candidate if row is item else row for row in b["items"]]})
    except Exception:
        logging.getLogger("studiohub.broker").exception(
            "could not adopt the abandoned job %s; its re-run continues", job.get("id"))
        return
    item.clear()
    item.update(candidate)
    _mark_machine_success(studio)
    if rerun["studio"] and rerun["studio_job_id"]:
        await _signal_worker_cancel(client, rerun)
    if not was_running:
        # Nothing is in flight to publish it (a running re-run does that as it stands down).
        reserved = _reserve_item_webhook(b, item)
        await _maybe_finish(client, b)
        if reserved:
            await _post_item_webhook(client, b, item, reserved=True)
        _wakeup.set()


def cancelled_text(batch: dict) -> str:
    """What a cancelled item says. Only a credential the Hub saw is named; otherwise it says
    "Cancelled" and makes no claim about who asked."""
    return {"this-mac": "Cancelled from this Mac",
            "gateway": "Cancelled from the Gateway"}.get(
                str(batch.get("cancelled_by") or ""), "Cancelled")


async def cancel_batch(batch_id: str, client: httpx.AsyncClient | None = None,
                       source: str | None = None) -> dict | None:
    """Cancel queued work and immediately signal every known running worker.

    `source` is the credential's own answer to "who asked" (`this-mac` or `gateway`), stamped by
    the route. The four asynchronous paths that write the item's sentence read it back from the
    batch through `cancelled_text`, so they cannot disagree with the route that started them."""
    b = batches.get(batch_id)
    if b is None:
        return None
    b["cancelled"] = True
    if source:
        b["cancelled_by"] = source
    queued_cancelled = 0
    for it in b["items"]:
        if it["state"] == "queued":
            it["state"] = "cancelled"
            it["error"] = cancelled_text(b)
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
    from . import control_plane
    identity = control_plane.execution_identity()
    machine = str(item.get("machine") or "local")
    # A remote executor's label is its own (the registry's name for that raw id, or the id
    # itself), never this controller's local name; only "local" is this Mac's configured name.
    machine_label = identity["machine_label"] if machine == "local" else label_for(machine)
    evidence = {
        "runtime_s": item.get("runtime_s", item.get("duration_s")),
        # Kept only for callers that predate runtime_s. It is runtime, never
        # decoded media duration.
        "duration_s": item.get("runtime_s", item.get("duration_s")),
        "model_revision": item.get("model_revision"),
        "runtime_revision": item.get("runtime_revision"),
        "worker_id": item.get("worker_id"),
        # The raw routing sentinel, unchanged. The fields below qualify which controller
        # and site it belongs to: `machine_label` names the executor, `controller_*` and
        # `site_*` the reporting controller (`controller_id` is stable per Hub, not a
        # global identity), so one site's "local" is never read as another's.
        "machine_id": item.get("machine"),
        "machine_label": machine_label,
        "controller_id": identity["controller_id"],
        "controller_label": identity["controller_label"],
        "site_id": identity["site_id"],
        "site_name": identity["site_name"],
        "resource_usage": item.get("resource_usage"),
    }
    result = {
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
    # The anti-silent-drop proof for a reference render: how many references the worker actually
    # attached. Present only where the worker stated it, so a text-to-image item's result is
    # byte-identical to before this lane existed. The Gateway compares it with the number it sent
    # and fails an item that differs, instead of accepting a text-to-image render as success.
    if item.get("reference_count") is not None:
        result["reference_count"] = item.get("reference_count")
    # The delivery direction this render actually ran with, when there was one: the caller's own
    # text, what the engine received, and the guidance strength applied.
    if isinstance(item.get("delivery_instruction"), dict):
        result["delivery_instruction"] = item["delivery_instruction"]
    return result



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
    # `_review_candidate` is the locator waiting for its terminal save (`ledger.save_batch`); the
    # review routes, never the job view, are how a candidate is reached.
    result = {k: v for k, v in item.items()
              if k not in {"artifact_path", "worker_artifact_url",
                           "worker_accepted_params", "_review_candidate"}}
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
    result remains available but is marked unverified and therefore unbillable. It still carries
    the size and SHA-256 it is served with (`_keep_served_digest`), so a client can check it.

    An empty or unplayable file is not a completed generation (orchestrator, 2026-10-09, after a
    0-byte m4a from studio-mac-1504 was published ``done``): it raises
    `artifact_metadata.UnusableArtifact`, which `_record_worker_success` turns into a failed,
    retryable attempt (``EMPTY_OUTPUT`` / ``INVALID_OUTPUT``).

    The artifact's format decides how. A WAV (the default) is decoded here, because the Hub
    derives its billable duration from the bytes themselves. A compressed artifact (``m4a``,
    delivered by a worker that was asked for ``output_format``) is **streamed through a SHA-256 and
    a byte count** and never held in memory: the worker already decoded the lossless master it
    encoded from and reports the duration, rate and channels of that, which the Hub keeps once the
    delivered bytes are proven to be the ones the worker described.
    """
    if type(expected_bytes) is int and expected_bytes == 0:
        raise artifact_metadata.EmptyArtifact("the worker reported an empty file")
    reported = (job or {}).get("media_type")
    if isinstance(reported, str) and reported.split(";", 1)[0].strip().lower() != "audio/wav":
        await _verify_compressed_voice_artifact(
            client, item, studio, worker_url, expected_bytes, expected_sha256, job or {})
        return
    verifier = artifact_metadata.WavStreamVerifier()
    downloaded = False
    try:
        url, headers = studio_request(studio, worker_url)
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
        downloaded = True
        metadata = verifier.finish()
        if expected_bytes is not None:
            if type(expected_bytes) is not int or expected_bytes <= 0:
                raise ValueError("worker byte count is not a positive integer")
        if expected_bytes is not None and expected_bytes != metadata["bytes"]:
            raise ValueError("worker byte count does not match downloaded artifact")
        if expected_sha256 and str(expected_sha256).lower() != metadata["sha256"]:
            raise ValueError("worker checksum does not match downloaded artifact")
    except artifact_metadata.UnusableArtifact:
        raise
    except (OverflowError, TypeError, ValueError, httpx.HTTPError) as exc:
        item["artifact_metadata_error"] = str(exc)
        _keep_served_digest(item, (verifier.total, verifier.sha256) if downloaded else None,
                            expected_bytes, expected_sha256)
        return
    item.update(metadata)
    item.pop("artifact_metadata_error", None)


def _keep_served_digest(item: dict, measured, expected_bytes, expected_sha256) -> None:
    """An unverified voice artifact is still served, so it still gets a size and SHA-256.

    The bytes the Hub itself downloaded win; when the download did not finish, the worker's own
    report is used if it is well-formed. Only the audio facts stay unset (unbillable).
    """
    if measured is not None:
        item["bytes"], item["sha256"] = measured
        return
    digest = str(expected_sha256 or "").lower()
    if (type(expected_bytes) is int and expected_bytes > 0 and len(digest) == 64
            and all(ch in "0123456789abcdef" for ch in digest)):
        item["bytes"], item["sha256"] = expected_bytes, digest


async def _verify_compressed_voice_artifact(client: httpx.AsyncClient, item: dict,
                                            studio: dict, worker_url: str,
                                            expected_bytes, expected_sha256,
                                            job: dict) -> None:
    """Prove a non-WAV voice artifact is the one the worker described, without buffering it.

    The file is streamed once. Whether it is playable at all (not empty; an MP4 with ``ftyp``,
    ``moov`` and no box cut off; the bytes match the reported type) is decided first and raises
    `UnusableArtifact`; only then are the worker's claims checked, and those are metadata errors.
    """
    from components.audio_encode import Mp4Boxes
    measured = None
    try:
        media_type = artifact_metadata.trusted_media_type(job.get("media_type"), "voice")
        url, headers = studio_request(studio, worker_url)
        hasher, boxes = hashlib.sha256(), Mp4Boxes()
        head = b""
        async with client.stream("GET", url, headers=headers, timeout=600.0) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(1024 * 1024):
                if not head:
                    head = chunk[:16]
                hasher.update(chunk)
                boxes.feed(chunk)
        received = boxes.total
        measured = (received, hasher.hexdigest())
        if received == 0:
            raise artifact_metadata.EmptyArtifact("the delivered file is empty")
        proven = artifact_metadata.sniff_media_type(head)
        if "audio/mp4" in (media_type, proven) or proven == "video/mp4":
            if boxes.problem():
                raise artifact_metadata.UnusableArtifact(
                    f"the delivered file is not a playable MP4: {boxes.problem()}")
        if media_type is not None and proven not in (media_type, None) and not (
                media_type == "audio/mp4" and proven == "video/mp4"):
            raise artifact_metadata.UnusableArtifact(
                "downloaded artifact is not the media type the worker reported")
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
        if received != expected_bytes:
            raise ValueError("worker byte count does not match downloaded artifact")
        if measured[1] != digest:
            raise ValueError("worker checksum does not match downloaded artifact")
    except artifact_metadata.UnusableArtifact:
        raise
    except (OverflowError, TypeError, ValueError, httpx.HTTPError) as exc:
        item["artifact_metadata_error"] = str(exc)
        _keep_served_digest(item, measured, expected_bytes, expected_sha256)
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


def _copy_load_telemetry(item: dict, job: dict) -> None:
    """Carry the worker's load telemetry onto the item only when the job states it.

    Presence-gated on purpose: absent stays absent, false stays false, 0.0 stays 0.0 —
    nothing is derived or backfilled from runtime or from an earlier attempt.
    """
    if "model_reused" in job:
        item["model_reused"] = job["model_reused"]
    if "load_s" in job:
        item["load_s"] = job["load_s"]


async def _record_worker_success(client: httpx.AsyncClient, b: dict, item: dict,
                                 studio: dict, job: dict, body: dict,
                                 t_start: float, *, recovery: bool = False,
                                 adoption_item: dict | None = None):
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
            return False
    if _superseded(item):
        return False
    _persist_worker_execution_started_at(b, item, job)
    if item.get("state") == "done" and item.get("asset_id"):
        return False  # terminal polling/recovery is idempotent
    target = item
    before = dict(item)
    item = dict(before)  # metadata awaits must never mutate a competing winner
    # The accepted attempt this caller found; every post-await fence below re-reads the
    # canonical row against it, so a replaced or discarded attempt can never be adopted.
    owner = {key: before.get(key) for key in
             ("studio_job_id", "studio", "machine", "model_revision",
              "runtime_revision", "worker_id")}

    def _same_attempt() -> bool:
        if any(target.get(key) != value for key, value in owner.items()):
            return False
        if target.get("error_code") == "DISCARDED_UNCERTAIN":
            return False
        # A recovery caller owns the row only while the operator has not cancelled the batch
        # and its accepted attempt is still undecided. Normal polling and the abandoned-rerun
        # adoption keep their existing cancellation handling.
        if recovery and (b.get("cancelled") or target.get("state") != "uncertain"):
            return False
        canonical = batches.get(b.get("id"))
        if recovery:
            # Recovery callers must hold the exact canonical batch AND row throughout every
            # await; an absent or replaced one means ownership was lost mid-read.
            if canonical is None or canonical is not b:
                return False
            row = next((row for row in canonical.get("items") or []
                        if row.get("index") == target.get("index")), None)
            if row is None or row is not target:
                return False
        else:
            if canonical is not None and canonical is not b:
                return False  # the batch object was replaced; a captured batch authorizes nothing
            if adoption_item is None:
                row = next((row for row in (canonical or b).get("items") or []
                            if row.get("index") == target.get("index")), None)
                if row is not None and row is not target:
                    return False  # the canonical row was replaced; never publish onto a copy
        return True
    _record_worker_resource_usage(item, job)
    _record_worker_section_quality(item, job)
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
        # What the worker made of a per-request delivery direction: the caller's text, what the
        # engine received, and the guidance strength used. Only present when the worker stated
        # it, so a job without one writes nothing and an older worker's item is unchanged.
        if isinstance(job.get("delivery_instruction"), dict):
            item["delivery_instruction"] = job["delivery_instruction"]
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
    _copy_load_telemetry(item, job)
    if b["modality"] == "voice" and worker_url:
        try:
            await _cache_voice_artifact_metadata(
                client, item, studio, worker_url, job.get("bytes"), job.get("sha256"), job)
        except artifact_metadata.UnusableArtifact as exc:
            # Not a completed generation, so not "done": fail this attempt, retryably, like any
            # worker failure. Nothing is published and the worker's file is never acknowledged.
            logging.getLogger("studiohub.broker").warning(
                "voice job %s on %s delivered an unusable file (%s; worker reported %r bytes): %s",
                job.get("id"), studio.get("id"), exc.code, job.get("bytes"), exc)
            if _same_attempt():
                target["error_code"] = exc.code
            raise _worker_terminal_error(f"The voice output is unusable: {exc}", exc.code) from exc
        if _voice_recovery_owns(target) and not recovery:
            return False
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
    # The final metadata await can lose to original adoption or another terminal
    # result. Recheck before writing any asset; the losing candidate stays private.
    # Ownership first: a recovery caller that already lost this attempt (another
    # reconciler won, a newer attempt replaced it, an operator discarded it) records
    # nothing - not an asset, not an error - even though its receipt looks valid.
    if (not _same_attempt()
            or _superseded(target) or (target.get("state") == "done" and target.get("asset_id"))
            or (adoption_item is not None and (
                b.get("cancelled") or adoption_item.get("state") not in ("queued", "running")
                or _superseded(adoption_item)))):
        return False
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
        model_reused=job["model_reused"] if "model_reused" in job else None,
        load_s=job["load_s"] if "load_s" in job else None,
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
    _reset_infra_episode(item)   # a completed generation ends the failure episode
    # A finished item is 100% done, whatever the last progress poll caught. A six-section long-form
    # job reported 0.788 - the value after section five - because the worker finished between polls
    # and the final update was never read, so a completed artifact displayed as four fifths rendered.
    item["progress"] = 1.0
    # Commit only this finalization's changes, including cleared errors. Fields
    # changed concurrently by its task (reservation/cancel state) stay current.
    for key in before.keys() - item.keys():
        target.pop(key, None)
    target.update((key, value) for key, value in item.items()
                  if key not in before or value != before[key])
    return True  # this call adopted the winner; callers must not infer it from state alone


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


def _recovery_budget_s(b: dict, t_start: float, job: dict | None = None) -> float:
    """How long a dropped attempt may still be reconciled, from the request's own duration.

    The lane's published deadline (`standalone.deadline_floor_seconds` — the number the Hub both
    enforces and publishes) raised by the worker's own stated `deadline_s`, less the time the
    attempt has already spent, plus the reservation grace. Floored at `RECOVERY_WINDOW_S`: a job
    that has already outlived its ceiling is still given one plain retry window, not zero.
    """
    from .. import standalone   # lazy: one deadline table, and standalone imports this module
    ceiling = float(standalone.deadline_floor_seconds(b.get("modality")))
    stated = (job or {}).get("deadline_s")
    if isinstance(stated, (int, float)) and not isinstance(stated, bool) and math.isfinite(stated):
        ceiling = max(ceiling, float(stated))
    elapsed = max(0.0, time.time() - float(t_start or time.time()))
    return max(RECOVERY_WINDOW_S, ceiling + PREFERRED_RESERVATION_GRACE_S - elapsed)


async def _recover_worker_job(client, b: dict, item: dict, studio: dict,
                              body: dict, t_start: float) -> bool | dict:
    """Reconcile a worker job after a transport failure.

    A generation request is not safely retryable once the worker has accepted
    it. Keep the Hub lease while reconnecting and poll the original job while the
    worker still reports it unfinished, inside the lane's own expected duration
    (`_recovery_budget_s`) — a slow TTS request outlives any flat constant. A
    worker that stops answering for `RECOVERY_UNREACHABLE_S` ends the wait early.
    Return True after adopting success, the exact terminal job for failure
    classification, or False when its outcome remains unknown.
    """
    job_id = item.get("studio_job_id")
    if not job_id or b.get("cancelled"):
        return False
    # Two bounds. The lane ceiling is how long a job that the worker still says is unfinished may be
    # waited on. Anything else (a garbled or invalid answer, a drop) gets only RECOVERY_WINDOW_S
    # since the worker last said "still working", so a bad receipt is not polled for the full ceiling.
    ceiling = time.monotonic() + _recovery_budget_s(
        b, t_start, {"deadline_s": item.get("deadline_s")})
    working_until = time.monotonic() + RECOVERY_WINDOW_S
    last_answer = time.monotonic()
    delay = 1.0
    while time.monotonic() < min(ceiling, working_until):
        if _expire_genstudio_batch(b):
            await _signal_worker_cancel(client, item)
            return False
        try:
            url, headers = studio_request(studio, f"/api/generate/jobs/{job_id}")
            jr = await client.get(
                url, headers=headers, timeout=10.0)
            last_answer = time.monotonic()
            if jr.status_code >= 400:
                return False  # 404/4xx means the worker no longer has the job
            if b.get("modality") == "voice":
                job = _voice_job_from_response(jr)
                if (isinstance(job, dict) and job.get("state") == "uncertain"
                        and job.get("error_code") == "EXECUTION_IDENTITY_LOST"):
                    return False  # Voice Studio restarted under it and never resumes one
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
            _record_worker_section_quality(item, job)
            state = job.get("state")
            if state in ("queued", "running", "cancel_requested"):
                _record_worker_progress(item, job.get("progress"),
                                        job.get("progress_stage"))
                # The worker says it is still on it: keep waiting, up to the lane ceiling.
                working_until = time.monotonic() + RECOVERY_WINDOW_S
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
        except Exception as exc:
            if getattr(exc, "worker_terminal", False):
                # The job finished but its file is unusable: a failed job, not a dropped link.
                return {**job, "state": "error", "error": str(exc),
                        "error_code": getattr(exc, "error_code", None)}
            # A worker that has not answered one poll for RECOVERY_UNREACHABLE_S has nothing to
            # reconcile; the transport branch then names the original error. Anything shorter is
            # the transient drop this window exists for.
            if time.monotonic() - last_answer > RECOVERY_UNREACHABLE_S:
                return False
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
    # The exact accepted attempt this handler found; re-read against the canonical row
    # after every await, so a replaced, discarded or newer-won attempt is never mutated.
    owner = {key: item.get(key) for key in
             ("studio_job_id", "studio", "machine", "model_revision",
              "runtime_revision", "worker_id")}

    def still_uncertain(*, allow_cancelled: bool = False) -> bool:
        if batch.get("cancelled") and not allow_cancelled:
            return False  # operator cancellation owns the remaining lifecycle
        if batches.get(batch_id) is not batch:
            return False  # canonical batch was replaced; a captured dict authorizes nothing
        if next((row for row in batch.get("items") or []
                 if row.get("index") == item_index), None) is not item:
            return False  # the canonical row was replaced; never mutate a captured dict
        if item.get("state") != "uncertain" or _superseded(item):
            return False
        if item.get("error_code") == "DISCARDED_UNCERTAIN":
            return False
        return all(item.get(key) == value for key, value in owner.items())

    def result(reconciled: bool) -> dict:
        current = batches.get(batch_id)
        if current is None:
            # A removed batch has no current item to report.
            return {"batch_id": batch_id, "clientRequestId": None,
                    "reconciled": False, "item": None}
        row = next((row for row in current.get("items") or []
                    if row.get("index") == item_index), None)
        return {"batch_id": batch_id, "clientRequestId": current.get("client_request_id"),
                "reconciled": reconciled and current is batch and row is item,
                "item": public_item(current, row) if row is not None else None}

    def pending(reason: str) -> dict:
        if not still_uncertain():
            # Lost during an await (explicit discard, newer attempt, another winner):
            # return the current state instead of stamping this call's reason on it.
            return result(False)
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
            # Signal on a private copy so a lost ownership during the await cannot
            # write cancel fields onto a replaced or discarded canonical row.
            private = dict(item)
            await _signal_worker_cancel(client, private)
            if still_uncertain(allow_cancelled=True):
                for key in ("cancel_signal_sent_at", "cancel_error"):
                    if key in private:
                        item[key] = private[key]
                    else:
                        item.pop(key, None)
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
        if not still_uncertain():
            # The attempt was discarded or superseded while the worker was read.
            return result(False)
        remote = not peers.is_local_studio(studio)
        try:
            _validate_durable_worker_job(modality, item, job, remote=remote)
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
            "worker_id": item.get("worker_id"),
            # A peer's worker names its own machine, not the node id (see _validate_image_worker_job).
            "machine_id": None if remote else machine,
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
                    if key not in _OPTIONAL_LANE_FIELDS)
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
                adopted = await _record_worker_success(
                    client, batch, item, studio, job, body,
                    float(item.get("run_started") or batch["created_at"]), recovery=True,
                )
            except Exception:
                return pending(f"The original {label} output could not be verified by the Hub.")
            if adopted is False:
                # Another reconciler already owns this attempt's winner, or it was
                # discarded/replaced during the metadata await: adopt nothing, mutate nothing.
                return result(False)
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
    # The exact accepted attempt this handler found; re-read against the canonical row
    # after every await, so a replaced, discarded or newer-won attempt is never mutated.
    owner = {key: item.get(key) for key in
             ("studio_job_id", "studio", "machine", "model_revision",
              "runtime_revision", "worker_id")}

    def still_uncertain(*, allow_cancelled: bool = False) -> bool:
        if batch.get("cancelled") and not allow_cancelled:
            return False  # operator cancellation owns the remaining lifecycle
        if batches.get(batch_id) is not batch:
            return False  # canonical batch was replaced; a captured dict authorizes nothing
        if next((row for row in batch.get("items") or []
                 if row.get("index") == item_index), None) is not item:
            return False  # the canonical row was replaced; never mutate a captured dict
        if item.get("state") != "uncertain" or _superseded(item):
            return False
        if item.get("error_code") == "DISCARDED_UNCERTAIN":
            return False
        return all(item.get(key) == value for key, value in owner.items())

    def result(reconciled: bool) -> dict:
        current = batches.get(batch_id)
        if current is None:
            # A removed batch has no current item to report.
            return {
                "batch_id": batch_id,
                "clientRequestId": None,
                "reconciled": False,
                "item": None,
            }
        row = next((row for row in current.get("items") or []
                    if row.get("index") == item_index), None)
        return {
            "batch_id": batch_id,
            "clientRequestId": current.get("client_request_id"),
            "reconciled": reconciled and current is batch and row is item,
            "item": public_item(current, row) if row is not None else None,
        }

    def pending(reason: str) -> dict:
        if not still_uncertain():
            # Lost during an await (explicit discard, newer attempt, another winner):
            # return the current state instead of stamping this call's reason on it.
            return result(False)
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
            # Signal on a private copy so a lost ownership during the await cannot
            # write cancel fields onto a replaced or discarded canonical row.
            private = dict(item)
            await _signal_worker_cancel(client, private)
            if still_uncertain(allow_cancelled=True):
                for key in ("cancel_signal_sent_at", "cancel_error"):
                    if key in private:
                        item[key] = private[key]
                    else:
                        item.pop(key, None)
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
        expected_request_id = (item.get("client_request_id")
                               or f"studiohub:{batch['id']}:{item['index']}")
        try:
            _validate_voice_worker_job(
                item, job, expected_request_id=expected_request_id,
            )
        except _VoiceReceiptError:
            return pending(
                "The original Voice worker status or artifact receipt was malformed."
            )
        if not still_uncertain():
            # The attempt was discarded or superseded while the worker was read.
            return result(False)
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
                adopted = await _record_worker_success(
                    client, batch, item, studio, job, body,
                    float(item.get("run_started") or batch["created_at"]),
                    recovery=True,
                )
            except Exception:
                return pending(
                    "The original Voice output could not be verified by the Hub."
                )
            if adopted is False:
                # Another reconciler already owns this attempt's winner, or it was
                # discarded/replaced during the metadata await: adopt nothing, mutate nothing.
                return result(False)
            if item.get("state") != "done":
                return pending(
                    "The original Voice output could not be adopted by the Hub."
                )
        else:
            _record_worker_failure(
                item, studio, job,
                float(item.get("run_started") or batch["created_at"]),
            )
            if state == "error":
                # Fetch into a private copy: the live row may have gained a newer winner's
                # locator while this read awaited, and this call must write nothing to it.
                private = dict(item)
                await _attach_review_candidate(client, batch, private, studio,
                                               str(job.get("error_code") or ""))
                if not still_uncertain():
                    return result(False)
                if private.get("_review_candidate") is not None:
                    item["_review_candidate"] = private["_review_candidate"]
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
        # Recheck canonical ownership after the webhook await: a replacement or
        # discard that landed during delivery must not be overwritten by this
        # captured batch's terminal finish. still_uncertain() is False for
        # already-done items, so check canonical identity directly.
        canonical = batches.get(batch_id)
        if canonical is not batch:
            return result(False)
        canonical_row = next((row for row in canonical.get("items") or []
                              if row.get("index") == item_index), None)
        if canonical_row is not item:
            return result(False)
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
    is recorded as cancelled, its machine is released, and its recorded identity is kept so a
    late result is recognisably this attempt's and is discarded rather than adopted.

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
    item["recovery"] = {"phase": "discarded", "reason": reason, "updated_at": time.time()}
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
    # A person asked for this work again: it is a new generation, so it takes a new request id and
    # must not reattach to the failed job the worker still remembers.
    _bump_rerun_id(batch, item)
    for field in ("error", "error_code", "finished_at", "studio", "studio_job_id",
                  "worker_id", "worker_artifact_url", "machine", "recovery",
                  "model_revision", "runtime_revision", "worker_accepted_params",
                  "cancel_signal_sent_at", "cancel_error", "artifact_path",
                  "artifact_url", "asset_id", "progress", "progress_stage",
                  "last_progress_at", "terminal_clause", "late_result",
                  "infra_failures", "infra_failure_started_at"):
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
# right for a while, but an attempt silent for this long is not coming back, and
# leaving it reserved blocks every later job with no reason shown. Automatic
# age-out only releases the machine reservation: the attempt keeps its state and
# exact identity so a late result can still be reconciled. Discarding by hand is
# the operator's separate, permanent decision, see discard_uncertain_item.
UNCERTAIN_AUTO_DISCARD_SECONDS = float(
    os.environ.get("STUDIOHUB_UNCERTAIN_DISCARD_SECONDS", 1800.0))


def _release_uncertain_machine(batch: dict, item: dict, reason: str) -> bool:
    """Durably release one stale uncertain attempt's Mac reservation.

    The candidate recovery dict is written first; the marker reaches memory only
    after the save returns, so capacity is never published on a failed write.
    Recovery metadata merges, so identity, phase trail and marker survive.
    """
    recovery = dict(item.get("recovery") or {})
    recovery.update(machine_released_at=time.time(),
                    phase="manual_action_required", reason=reason[:500],
                    updated_at=time.time())
    candidate = {**item, "recovery": recovery}
    try:
        ledger.save_batch({**batch, "items": [
            candidate if row is item else row for row in batch["items"]]})
    except Exception as exc:
        logging.getLogger("studiohub.broker").warning(
            "machine release for %s:%s not persisted (%s); its hold stays",
            batch.get("id"), item.get("index"), type(exc).__name__)
        return False
    item["recovery"] = recovery  # single-threaded publish, no await before this
    _wakeup.set()
    return True


def release_stale_uncertain(*, now: float | None = None,
                            max_age: float | None = None) -> list[str]:
    """Release Macs held by uncertain attempts nobody is coming back for.

    Only the reservation goes: the item stays ``uncertain`` with its accepted
    identity, so its original receipt can still be reconciled later. A hold stays
    put (never released by age) when the item is cancelled, already released,
    still active on its Mac, shares its Mac with another uncertain attempt, or
    has an identity too incomplete to record a release against.
    Returns ``batch:item`` for every released hold, so a caller can log them.
    """
    limit = UNCERTAIN_AUTO_DISCARD_SECONDS if max_age is None else max_age
    moment = time.time() if now is None else now
    released: list[str] = []
    for batch in list(batches.values()):
        if batch.get("cancelled"):
            continue  # operator cancellation lifecycle, never age-out recovery
        counts = _uncertain_hold_counts({s["id"]: s for s in _monitor().registry})
        for item in batch.get("items") or []:
            if item.get("state") != "uncertain":
                continue
            if _machine_release_state(item) is not None:
                continue
            if item.get("studio") in _busy:
                continue  # an actual execution on that Mac is authoritative; hold stays
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
            machine = item.get("machine") or next(
                (row.get("machine") for row in _monitor().registry
                 if row.get("id") == item.get("studio")), None)
            if machine:
                others = counts.get(str(machine), 0) - 1  # minus this attempt's own hold
                if others > 0:
                    counts[str(machine)] = others
                    continue  # another attempt on the same Mac still needs it
            if not all(isinstance(item.get(field), str) and item.get(field)
                       for field in ("studio", "studio_job_id", "machine")):
                # Incomplete identity: releasing the Mac would not be recorded against
                # anything an operator can reconcile later. Keep it visible instead.
                continue
            reason = (
                "The attempt stayed uncertain past the automatic reconciliation "
                "window, so its machine reservation was released. The attempt stays "
                "uncertain: a late result of this same attempt may still be reconciled.")
            if _release_uncertain_machine(batch, item, reason):
                if machine:
                    counts[str(machine)] = 0
                released.append(f"{batch['id']}:{item['index']}")
    return released


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
    # Never mutate or persist if the canonical batch or row was replaced or
    # removed during the failed await — that would resurrect a detached winner.
    current = batches.get(batch.get("id"))
    if current is not batch:
        return False
    canonical_row = next((row for row in current.get("items") or []
                          if row.get("index") == item.get("index")), None)
    if canonical_row is not item:
        return False
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


_DISPATCH_TRACE_GATES = frozenset({
    "admission_refused",
    "batch_cancelled",
    "catalog_missing",
    "execution_lease_expired",
    "faster_machine_preferred",
    "function_switched_off",
    "higher_priority_work",
    "item_no_longer_queued",
    "item_temporarily_ineligible",
    "lite_mode_blocked",
    "machine_blocked",
    "machine_disabled",
    "machine_lease_busy",
    "machine_reserved",
    "memory_skip",
    "memory_wait",
    "model_not_cached",
    "model_protected",
    "peer_not_dispatch_ready",
    "remote_local_excluded",
    "revision_evidence_missing",
    "revision_mismatch",
    "routing_excluded",
    "studio_busy",
    "studio_disabled",
    "studio_maintenance",
    "unknown",
    "voice_not_synchronized",
    "worker_not_up",
})
_DISPATCH_TRACE_ROWS = 65
_DISPATCH_TRACE_BYTES = 32 * 1024
_DISPATCH_TRACE_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,80}$")


def _safe_dispatch_trace_id(value: object) -> str:
    text = str(value or "")
    return text if _DISPATCH_TRACE_ID.fullmatch(text) else "unknown"


def _record_dispatch_gate(rows: list[dict], studio: dict, gate: str) -> None:
    """Keep one candidate's first fixed-code refusal for this scheduler pass."""
    studio_id = _safe_dispatch_trace_id(studio.get("id"))
    if any(row.get("studio") == studio_id for row in rows):
        return
    rows.append({
        "machine": _safe_dispatch_trace_id(studio.get("machine", "local")),
        "studio": studio_id,
        "gate": gate if gate in _DISPATCH_TRACE_GATES else "unknown",
    })


def _store_blocked_dispatch_trace(batch: dict, queued: list[dict],
                                  rows: list[dict], *, now: float | None = None) -> bool:
    """Replace the last blocked snapshot only when its bounded evidence changed."""
    candidates = []
    for row in rows[:_DISPATCH_TRACE_ROWS]:
        candidates.append({
            "machine": _safe_dispatch_trace_id(row.get("machine")),
            "studio": _safe_dispatch_trace_id(row.get("studio")),
            "gate": (str(row.get("gate")) if row.get("gate") in _DISPATCH_TRACE_GATES
                     else "unknown"),
        })
    all_queued_items = [item.get("index") for item in queued
                        if type(item.get("index")) is int]
    queued_items = all_queued_items[:_DISPATCH_TRACE_ROWS]
    truncated = (len(rows) > len(candidates)
                 or len(all_queued_items) > len(queued_items))
    trace = {
        "version": 1,
        "blocked_at": time.time() if now is None else now,
        "queued_items": queued_items,
        "candidates": candidates,
        "truncated": truncated,
    }
    previous = batch.get("dispatch_trace")
    if isinstance(previous, dict) and isinstance(previous.get("selected"), dict):
        trace["selected"] = previous["selected"]
    while len(json.dumps(trace, separators=(",", ":")).encode()) > _DISPATCH_TRACE_BYTES and candidates:
        candidates.pop()
        trace["truncated"] = True
    while len(json.dumps(trace, separators=(",", ":")).encode()) > _DISPATCH_TRACE_BYTES and queued_items:
        queued_items.pop()
        trace["truncated"] = True
    semantic_keys = ("version", "queued_items", "candidates", "truncated")
    if isinstance(previous, dict) and all(previous.get(key) == trace.get(key)
                                          for key in semantic_keys):
        return False
    batch["dispatch_trace"] = trace
    return True


def _store_dispatch_selection(batch: dict, item: dict, studio: dict,
                              *, now: float | None = None) -> None:
    trace = dict(batch.get("dispatch_trace") or {"version": 1, "truncated": False})
    trace["selected"] = {
        "at": time.time() if now is None else now,
        "item_index": item.get("index") if type(item.get("index")) is int else None,
        "machine": _safe_dispatch_trace_id(studio.get("machine", "local")),
        "studio": _safe_dispatch_trace_id(studio.get("id")),
    }
    batch["dispatch_trace"] = trace


def _eligible_studios(modality: str, routing: str,
                      model: str | None = None,
                      trace: list[dict] | None = None) -> list[dict]:
    mon = _monitor()
    out = []
    leased_machines = busy_machines()
    for s in mon.registry:
        if s["modality"] != modality:
            continue
        # A pin is the operator's instruction, so it beats the automatic choice: a Mac that
        # has the model is still skipped when another Mac was named.
        if not routing_rule.allows(s, routing):
            if trace is not None:
                _record_dispatch_gate(trace, s, "routing_excluded")
            continue
        machine = s.get("machine", "local")
        # A remote render deliberately keeps the Hub Mac as the control plane.
        # It waits for an external Render Studio rather than quietly consuming
        # the Hub machine's CPU / Media Engine as a fallback.
        if routing == "remote" and peers.is_local_studio(s):
            if trace is not None:
                _record_dispatch_gate(trace, s, "remote_local_excluded")
            continue
        if s["id"] in _busy:
            if trace is not None:
                _record_dispatch_gate(trace, s, "studio_busy")
            continue
        if s["id"] in _maintenance:
            if trace is not None:
                _record_dispatch_gate(trace, s, "studio_maintenance")
            continue
        if machine in leased_machines:
            if developer_test_admission.held(machine) is not None:
                developer_test_admission.production_arrived(machine)
            if trace is not None:
                _record_dispatch_gate(trace, s, "machine_lease_busy")
            continue
        if _machine_reserved_until.get(machine, 0) > time.time():
            if trace is not None:
                _record_dispatch_gate(trace, s, "machine_reserved")
            continue
        if not peers.dispatch_ready(s):
            if trace is not None:
                _record_dispatch_gate(trace, s, "peer_not_dispatch_ready")
            continue
        if not peers.lite_mode_allows(s, model):
            if trace is not None:
                _record_dispatch_gate(trace, s, "lite_mode_blocked")
            continue
        # a machine the operator has disabled stays monitored but takes no jobs
        if not machine_enabled(s.get("machine", "local")):
            if trace is not None:
                _record_dispatch_gate(trace, s, "machine_disabled")
            continue
        # **The work order's own on/off switch** (owner, 2026-10-28): a kind this Mac switched off
        # is not taken by it, whatever its rank. It is not the Studio's own pause above — that one
        # hides a worker, this one drops the kind from the order — and both still apply.
        if not function_enabled(machine, s["modality"]):
            if trace is not None:
                _record_dispatch_gate(trace, s, "function_switched_off")
            continue
        # App-specific pauses are scheduler-only: current work finishes and the
        # Studio remains online for monitoring, lifecycle control, and updates.
        if not studio_enabled(machine, s["id"]):
            if trace is not None:
                _record_dispatch_gate(trace, s, "studio_disabled")
            continue
        if _machine_blocked(machine):
            if trace is not None:
                _record_dispatch_gate(trace, s, "machine_blocked")
            continue
        if mon.status.get(s["id"], {}).get("status") == "up":
            out.append(s)
        elif trace is not None:
            _record_dispatch_gate(trace, s, "worker_not_up")
    if modality == "render":
        # Render workers publish a normalized score in /api/health. M4 16 GB
        # machines rank above older/smaller Macs, while every healthy worker
        # remains an eligible fallback.
        out.sort(key=lambda s: (
            -float((mon.status.get(s["id"], {}).get("health") or {})
                   .get("render_score", 0)),
            s["id"],
        ))
    elif speed_ranked(model):
        # Qwen: fastest expected Mac first; a Mac below the model's floor still sorts last.
        seconds = _mac_seconds(modality, model)
        floor = _placement_key(modality, model)
        out.sort(key=lambda s: (floor(s)[0] == 1, seconds(s.get("machine", "local")), s["id"]))
    else:
        # Smallest capable machine first, for every lane. Capability is the model's own published
        # floor and admission enforces it; this only orders the machines that can all run the job,
        # so the few 16 GB+ Macs stay free for work that has nowhere else to go. The rule belongs
        # to the model, not the modality - see `_placement_key`.
        out.sort(key=_placement_key(modality, model))
    return out


def _shared_voice_allows_studio(batch: dict, item: dict, studio: dict) -> bool:
    """Keep a cloned voice on the workers that actually hold it.

    A Hub-owned id goes only where it is synchronized; with no successful target it waits in
    queue until the background synchronizer heals one worker. An id the Hub does not manage is
    an entry in this Mac's own Voice Studio library (the app lists voices from it), which no
    other Mac holds: it used to be allowed on *any* studio, so a pool dispatch could send it to
    a node that would fail it for a missing reference. It now stays on this Mac's own studio.
    """
    if batch.get("modality") != "voice":
        return True
    params = dict(batch.get("shared_params") or {})
    params.update(item.get("params") or {})
    voice_id = str(params.get("voice_library_id") or "").strip()
    if not voice_id:
        return True
    synced = shared_voices.synced_studio_ids(voice_id)
    if synced is None:
        return peers.is_local_studio(studio)
    return studio.get("id") in synced


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
    if any(_wait_for_faster_mac(batch, item, studio) for item in queued):
        return f"Waiting for a faster Mac: it should finish sooner than {studio['id']}"
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
    """Queued broker work in render-first, then stable FIFO scan order.

    The per-Mac rank comparator decides which eligible function may lease the
    machine. This scan keeps that decision cheap and deterministic; it no longer
    performs a second, conflicting scarcity/model-stickiness ordering.
    """
    return sorted(
        ((0 if batch["modality"] == "render" else 1, batch)
         for batch in batches.values()
         if any(item.get("state") == "queued" for item in batch.get("items", []))),
        key=lambda row: (row[0], row[1].get("created_at", 0), row[1].get("id", "")),
    )


# Warm-first scan constants. Evidence is pulled on demand from the dispatch loop only —
# no background polling, no timer: the cache window, the fetch deadline, and the age past
# which a queued batch can never be demoted below a warm one (FIFO aging therefore bounds
# any warm-first delay to WARM_BATCH_STALE_S).
WARM_EVIDENCE_FRESH_S = 3.0
WARM_EVIDENCE_DEADLINE_S = 1.0
WARM_BATCH_STALE_S = 30.0
_WARM_EVIDENCE_CACHE: dict = {"at": 0.0, "residents": None}
_WARM_LAST_LOGGED_BATCH: str | None = None


def _warm_first_enabled() -> bool:
    """STUDIOHUB_WARM_FIRST=0 turns warm-first off; unset (or any other value) leaves it on.

    No new setting or memory behavior: warmth can only exist when the owner's existing
    memory modes already left a model resident.
    """
    return os.environ.get("STUDIOHUB_WARM_FIRST") != "0"


def _warm_residents_from_inventory(inventory, *, now: float | None = None) -> dict:
    """(machine, modality) -> resident model strings from a fleet memory-policy inventory.

    Only a `ready` row whose policy says busy is false, loaded_models is non-empty and
    next_release_at is null or in the future contributes. Unknown shapes are skipped, never
    guessed; the result then falls back toward the original order.
    """
    if not isinstance(inventory, dict) or not isinstance(inventory.get("studios"), list):
        return {}
    now = time.time() if now is None else now
    residents: dict = {}
    for row in inventory["studios"]:
        if not isinstance(row, dict) or row.get("state") != "ready":
            continue
        policy = row.get("policy")
        if not isinstance(policy, dict) or policy.get("busy") is not False:
            continue
        due = policy.get("next_release_at")
        if due is not None and (
                isinstance(due, bool) or not isinstance(due, (int, float)) or due <= now):
            continue  # due for release, or an unknown stamp: stale, not warm
        loaded = policy.get("loaded_models")
        models: set = set()
        if isinstance(loaded, str) and loaded:
            models.add(loaded)
        elif isinstance(loaded, (list, tuple)):
            for entry in loaded:
                # Workers report `["repo", ...]` keys; the fleet vocabulary entry is the
                # first element. An unparseable entry is skipped, never guessed.
                if isinstance(entry, str) and entry:
                    models.add(entry)
                elif (isinstance(entry, (list, tuple)) and entry
                        and isinstance(entry[0], str) and entry[0]):
                    models.add(entry[0])
        if not models:
            continue
        key = (str(row.get("machine") or "local"), str(row.get("modality") or ""))
        residents.setdefault(key, set()).update(models)
    return residents


def _batch_is_warm(batch: dict, residents: dict) -> bool:
    """True only on an exact `batch["model"]` string match in some machine's resident set
    for this batch's modality."""
    model = batch.get("model")
    if not isinstance(model, str) or not model:
        return False
    modality = batch.get("modality")
    return any(model in models
               for (_machine, row_modality), models in residents.items()
               if row_modality == modality)


def _warm_first_order(rows, residents, now):
    """Ordering-only helper: existing sort group first, then warm/aged rank, then FIFO.

    Key: (sort_group, rank, created_at, id). Rank 0 = resident-warm, or queued for at
    least WARM_BATCH_STALE_S (never demoted, so a continuous warm stream cannot starve a
    cold batch past 30 s). Never reorders across render/other groups. Empty or unknown
    residents leave the original order untouched — with no warmth every rank collapses and
    aging alone already agrees with FIFO, so no reorder is possible.
    """
    if not residents:
        return list(rows)

    def key(row):
        _group, batch = row
        created = float(batch.get("created_at") or 0)
        aged = now - created >= WARM_BATCH_STALE_S
        rank = 0 if (aged or _batch_is_warm(batch, residents)) else 1
        return (row[0], rank, created, batch.get("id", ""))

    return sorted(rows, key=key)


def _warm_justifying_machine(batch: dict, residents: dict) -> str | None:
    """The machine whose fresh resident evidence makes this batch warm, deterministically."""
    model = batch.get("model")
    modality = batch.get("modality")
    if not isinstance(model, str) or not model:
        return None
    for (machine, row_modality), models in sorted(residents.items()):
        if row_modality == modality and model in models:
            return machine
    return None


def _log_warm_first_reorder(rows, ordered, residents) -> None:
    """One existing-style dispatch line per warm choice; nothing while order is unchanged
    and nothing again while the same warm batch stays ahead of the scan."""
    global _WARM_LAST_LOGGED_BATCH
    moved = next(((before, after) for before, after in zip(rows, ordered)
                  if before[1] is not after[1]), None)
    if moved is None:
        return
    batch = moved[1][1]  # (sort_group, batch), not the row itself
    batch_id = str(batch.get("id") or "")
    if batch_id and batch_id == _WARM_LAST_LOGGED_BATCH:
        return  # same warm choice as last pass: one choice, one line
    machine = _warm_justifying_machine(batch, residents)
    if machine is None:
        return  # changed without resident evidence naming a machine: never log a guess
    _WARM_LAST_LOGGED_BATCH = batch_id
    logging.getLogger("studiohub.broker").info(
        "warm-first scan batch=%s model=%s machine=%s",
        batch.get("id"), batch.get("model"), machine)


async def _fetch_warm_residents(client: httpx.AsyncClient) -> dict:
    """Fresh resident evidence, fetched at most once per WARM_EVIDENCE_FRESH_S seconds.

    The deadline bounds a dispatch pass to about a second: FleetMemoryControl's own
    requests allow 15 s each, and an offline Studio must never stall the loop. Timeout,
    error, non-200 or an unknown response shape all yield no evidence (empty dict) and are
    cached for the same window, so a dead Studio costs one bounded attempt per 3 s.
    """
    now = time.time()
    cached = _WARM_EVIDENCE_CACHE
    if cached["residents"] is not None and now - cached["at"] < WARM_EVIDENCE_FRESH_S:
        return cached["residents"]
    residents: dict = {}
    try:
        inventory = await asyncio.wait_for(
            FleetMemoryControl(_monitor(), client).inventory(),
            timeout=WARM_EVIDENCE_DEADLINE_S,
        )
        residents = _warm_residents_from_inventory(inventory)
    except Exception:  # noqa: BLE001 - any failure means no evidence, today's order
        residents = {}
    _WARM_EVIDENCE_CACHE["at"] = time.time()
    _WARM_EVIDENCE_CACHE["residents"] = residents
    return residents


async def _warm_first_queued(rows, client: httpx.AsyncClient) -> list:
    """Scan order for this pass: warm-first when reordering is possible, else today's order.

    Evidence is fetched only when at least two queued batches compete with different
    (model, modality) pairs — same-model batches on the same modality cannot reorder, and
    warmth is keyed per modality — and the env switch is on: no fetch, no timer, nothing
    while the Hub is idle. Warmth can only reorder scan order among queued batches; every
    eligibility, rank, admission and pause check after this loop still applies unchanged.
    """
    if len(rows) < 2 or not _warm_first_enabled():
        return list(rows)
    if len({(batch.get("model"), batch.get("modality"))
            for _group, batch in rows}) < 2:
        return list(rows)  # one model/modality everywhere: warm-first cannot change order
    residents = await _fetch_warm_residents(client)
    ordered = _warm_first_order(rows, residents, time.time())
    if [b.get("id") for _group, b in ordered] != [b.get("id") for _group, b in rows]:
        _log_warm_first_reorder(rows, ordered, residents)
    return ordered


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


async def _hand_back_queued(client: httpx.AsyncClient, b: dict) -> None:
    """Give every queued item of this batch back, typed NO_AVAILABLE_MACHINE: nothing ran and no
    try is spent. Running items are left to finish. Same publication order as `_run_item`'s
    terminal path: the durable write first, then the per-item webhooks."""
    now = time.time()
    handed = [item for item in b["items"] if item.get("state") == "queued"]
    for item in handed:
        for key in ("capacity_failures", "capacity_wait_started_at",
                    "admission_wait_started_at", "_memory_handback"):
            item.pop(key, None)
        item.update(state="error", retry_at=None, error_code=NO_AVAILABLE_MACHINE,
                    error=NO_AVAILABLE_MACHINE_TEXT, finished_at=now, last_progress_at=now)
    reserved = [item for item in handed if _reserve_item_webhook(b, item)]
    await _maybe_finish(client, b)
    for item in reserved:
        await _post_item_webhook(client, b, item, reserved=True)


async def _dispatch_loop():
    """The scheduler: match queued items to free studios, forever."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=60, write=30, pool=5))
    last_sweep = 0.0
    while True:
        try:
            await _watch_restored_attempts(client)
            sweep_now = time.time()
            if sweep_now - last_sweep >= 60:
                last_sweep = sweep_now
                evict_finished_batches()
                # An uncertain attempt that nobody reconciles must not pin the Mac
                # forever; release its reservation so later work can run. The attempt
                # itself stays uncertain and reconcilable, see release_stale_uncertain.
                release_stale_uncertain(now=sweep_now)
            assigned = False
            if _render_executor is not None:
                assigned = await _render_executor.dispatch_ready()
            for batch in list(batches.values()):
                if (batch.get("modality") in DURABLE_ASYNC_MODALITIES
                        and batch.get("cancelled")):
                    for item in batch.get("items") or []:
                        if (item.get("state") == "uncertain"
                                and item.get("studio_job_id")
                                and not item.get("cancel_signal_sent_at")):
                            await _signal_worker_cancel(client, item)
                            ledger.save_batch(batch)
            # Render first, then FIFO. The final per-Mac rank/eligibility check
            # immediately before the lease is the shared ordering authority.
            # Warm-first only reorders within a sort group, only when fresh resident
            # evidence says a queued batch's model is already loaded, and only when at
            # least two batches compete; everything below this scan is unchanged.
            for _sort_group, b in await _warm_first_queued(_queued_batches(), client):
                if _expire_genstudio_batch(b):
                    ledger.save_batch(b)
                    continue
                if b["cancelled"]:
                    continue
                if every_capable_mac_paused(b["modality"], b.get("model"),
                                            b.get("routing", routing_rule.POOL)):
                    await _hand_back_queued(client, b)
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
                trace_candidates: list[dict] = []
                eligible = _eligible_studios(
                    b["modality"], b["routing"], b.get("model"), trace_candidates,
                )
                evidence_eligible = []
                for studio in eligible:
                    if _supports_genstudio_voice_evidence(b, studio):
                        evidence_eligible.append(studio)
                    else:
                        _record_dispatch_gate(
                            trace_candidates, studio, "revision_evidence_missing"
                        )
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
                elif not eligible and (lite_wait := peers.lite_mode_wait(b.get("model"))):
                    b["governor_note"] = lite_wait   # this Mac in Lite Mode, and nobody else free
                batch_assigned = False
                for studio in eligible:
                    if not queued:
                        break
                    compatible_index = next(
                        (index for index, candidate in enumerate(queued)
                         if _shared_voice_allows_studio(b, candidate, studio)
                         and _item_allows_studio(candidate, studio, now)
                         and not _wait_for_faster_mac(b, candidate, studio)),
                        None,
                    )
                    if compatible_index is None:
                        b["governor_note"] = _no_candidate_note(b, queued, studio)
                        first = queued[0]
                        if not _shared_voice_allows_studio(b, first, studio):
                            gate = "voice_not_synchronized"
                        elif not _item_allows_studio(first, studio, now):
                            gate = "item_temporarily_ineligible"
                        else:
                            gate = "faster_machine_preferred"
                        _record_dispatch_gate(trace_candidates, studio, gate)
                        continue
                    # ── model-aware dispatch (heterogeneous machines) ──
                    # A studio only gets work for models it actually has. This
                    # is what lets a 3-model Mac and a 1-model Mac share pools.
                    entry = await _catalog_entry(studio, b["model"])
                    if entry is None:
                        b["governor_note"] = (
                            f"'{b['model']}' not in {studio['id']}'s catalog")
                        _record_dispatch_gate(trace_candidates, studio, "catalog_missing")
                        continue
                    if not _catalog_matches_genstudio_revision(b, entry):
                        b["governor_note"] = (
                            "Waiting for a worker with the exact immutable model "
                            "revision assigned by GenStudio"
                        )
                        _record_dispatch_gate(trace_candidates, studio, "revision_mismatch")
                        continue
                    protection_note = _model_block_note(studio, b["model"], entry)
                    if protection_note:
                        b["governor_note"] = f"{studio['id']}: {protection_note}"
                        _record_dispatch_gate(trace_candidates, studio, "model_protected")
                        continue
                    if not entry.get("is_cloud") and not is_cached(entry):
                        b["governor_note"] = (
                            f"'{b['model']}' not downloaded on {studio['id']} "
                            f"— download it on that Mac, or pin another")
                        _record_dispatch_gate(trace_candidates, studio, "model_not_cached")
                        continue
                    # ── fleet memory governor ──
                    # Peer Hubs report their own host snapshots, so we can
                    # avoid submitting work that their MemoryGuard would
                    # reject. Missing/stale telemetry does not strand work:
                    # the worker's own guard remains the final authority.
                    is_local = peers.is_local_studio(studio)
                    # A worker catalog is one input. Measured fleet defaults
                    # and visible owner overrides form the effective site-local
                    # admission policy used immediately before dispatch. The item's own lane
                    # is the last input: a reference (edit) item asks for the model's edit
                    # floors, which are the measured ones for that lane.
                    candidate_mode = _item_mode(queued[compatible_index])
                    # ── fleet disk governor ──
                    # The same host snapshot that carries free memory also carries the Mac's own
                    # disk verdict (`disk_governor.snapshot`, published on /api/hub/resources).
                    # A Mac whose output storage is in the governor's stop/red tier cannot take
                    # another job: skip it here, before any attempt, instead of spending one on a
                    # worker refusal and learning it after the fact.
                    if not _disk_allows(b, studio, trace_candidates):
                        continue
                    mem = _admission_requirements(b["model"], entry, mode=candidate_mode)
                    reserve = 0.0
                    if mem is not None:
                        decision, note = await prepare_machine_memory(
                            client, studio, b["model"], entry, mode=candidate_mode)
                        if decision != "run":
                            # skip → another (maybe bigger/remote) studio may take
                            # it; wait → defer. NEVER errors the whole batch - except that an
                            # admission wait is bounded like three refusals (~4 min): the item is
                            # sent on to `_run_item`, which hands it back as INSUFFICIENT_MEMORY.
                            b["governor_note"] = f"{studio['id']}: {note}"
                            # The expiry reads the floor the gate itself used (a workstation's own
                            # start floor included), and the sentence the gate's own numbers.
                            if not (decision == "wait" and _admission_wait_expired(
                                    queued[compatible_index], studio,
                                    _with_machine_floor(mem, studio), note, now, b)):
                                _record_dispatch_gate(
                                    trace_candidates, studio,
                                    "memory_wait" if decision == "wait" else "memory_skip",
                                )
                                continue
                            waited_s = now - float(queued[compatible_index]["admission_wait_started_at"])
                            queued[compatible_index]["_memory_handback"] = _insufficient_memory_text(
                                b, studio, note, waited_s=waited_s)
                        else:
                            queued[compatible_index].pop("admission_wait_started_at", None)
                            queued[compatible_index].pop("_memory_handback", None)
                        reserve = _required_free_memory(mem) if is_local else 0.0
                    # Eligibility was calculated before the catalog/memory awaits.
                    # Recheck the physical lease so a transcription that claimed
                    # this Mac in the meantime never overlaps generation/render.
                    if studio.get("machine", "local") in busy_machines():
                        _record_dispatch_gate(
                            trace_candidates, studio, "machine_lease_busy"
                        )
                        continue
                    if (b["modality"] != "render"
                            and await higher_priority_work_waiting(
                                studio.get("machine", "local"), b["modality"],
                                b.get("created_at", 0), f"broker:{b['id']}")):
                        _record_dispatch_gate(
                            trace_candidates, studio, "higher_priority_work"
                        )
                        continue
                    candidate = queued[compatible_index]
                    machine = studio.get("machine", "local")
                    if b.get("cancelled"):
                        _record_dispatch_gate(trace_candidates, studio, "batch_cancelled")
                        continue
                    if candidate.get("state") != "queued":
                        _record_dispatch_gate(
                            trace_candidates, studio, "item_no_longer_queued"
                        )
                        continue
                    if execution_identity.lease_expired(b.get("genstudio_execution")):
                        _record_dispatch_gate(
                            trace_candidates, studio, "execution_lease_expired"
                        )
                        continue
                    if _monitor().status.get(studio["id"], {}).get("status") != "up":
                        _record_dispatch_gate(trace_candidates, studio, "worker_not_up")
                        continue
                    if not machine_enabled(machine):
                        _record_dispatch_gate(trace_candidates, studio, "machine_disabled")
                        continue
                    if not studio_enabled(machine, studio["id"]):
                        _record_dispatch_gate(trace_candidates, studio, "studio_disabled")
                        continue
                    if studio["id"] in _maintenance:
                        _record_dispatch_gate(
                            trace_candidates, studio, "studio_maintenance"
                        )
                        continue
                    if not _item_allows_studio(candidate, studio, time.time()):
                        _record_dispatch_gate(
                            trace_candidates, studio, "item_temporarily_ineligible"
                        )
                        continue
                    if studio.get("machine", "local") in busy_machines():
                        _record_dispatch_gate(
                            trace_candidates, studio, "machine_lease_busy"
                        )
                        continue
                    generation = peers.dispatch_generation(studio)
                    if not peers.dispatch_ready(studio, generation):
                        _record_dispatch_gate(
                            trace_candidates, studio, "peer_not_dispatch_ready"
                        )
                        continue
                    refusal = admission_refusal(b)
                    if refusal:
                        b["governor_note"] = str(refusal.get("detail") or "model is not ready")
                        _record_dispatch_gate(trace_candidates, studio, "admission_refused")
                        ledger.save_batch(b)
                        continue
                    b["governor_note"] = None
                    item = queued.pop(compatible_index)
                    _clear_worker_attempt_evidence(item)
                    item["state"] = "running"
                    item["retry_at"] = None
                    item["studio"] = studio["id"]
                    peers.note_dispatch(studio)
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
                    previous_dispatch_trace = b.get("dispatch_trace")
                    _store_dispatch_selection(b, item, studio)
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
                            if previous_dispatch_trace is None:
                                b.pop("dispatch_trace", None)
                            else:
                                b["dispatch_trace"] = previous_dispatch_trace
                            raise
                    asyncio.create_task(_run_item(client, b, item, studio))
                    assigned = True
                    batch_assigned = True
                if not batch_assigned:
                    previous_dispatch_trace = b.get("dispatch_trace")
                    if _store_blocked_dispatch_trace(b, queued, trace_candidates):
                        try:
                            ledger.save_batch(b)
                        except Exception:
                            if previous_dispatch_trace is None:
                                b.pop("dispatch_trace", None)
                            else:
                                b["dispatch_trace"] = previous_dispatch_trace
                            raise
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
        if _counts_against_model(message, code):
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
    "worker_accepted_params", "section_quality", "deadline_s",
    "model_reused", "load_s",
)


def _clear_worker_attempt_evidence(item: dict) -> None:
    """A retry must report only the worker that owns the new attempt."""
    for field in _ATTEMPT_EVIDENCE_FIELDS:
        item.pop(field, None)
    item["error"] = None


def _reference_refusal_verdict(item: dict, rejection: BaseException, reason: str) -> str:
    """What a refused Voice-reference upload means: ``"raise"`` (the caller's wait) or ``"terminal"``.

    Two refusals are not about the reference at all and must reach the handler's wait branches:

    * **A reserved Mac** answered its lease before its worker saw the reference — the same 409 the
      generate POST meets, so the same brief wait and the try given back (2026-10-09: 276 attempts
      overnight without it).
    * **A full disk.** **Same refusal, two verdicts (fixed 2026-11-16):** a full Trash Folder used to
      become a terminal `VOICE_REFERENCE_REJECTED` here while the generate path treats the identical
      answer as a 300 s disk-full wait. The terminal verdict spent a Gateway attempt on a Mac that
      could not have taken the job and kept that Mac in the pool.

    Anything else keeps the worker's own reason. Until that existed the refusal was discarded, the
    job accepted with no reference attached, and the operator saw only "Voice cloning needs a
    reference voice" three retries later.
    """
    code = getattr(rejection, "error_code", None)
    if _is_machine_reservation_refusal(rejection):
        item["error_code"] = _sanitize_public_error_code(code)
        return "raise"
    if _is_disk_full_refusal(rejection):
        item["error_code"] = _sanitize_public_error_code(code or "OUTPUT_STORAGE_BLOCKED")
        return "raise"
    item["state"] = "error"
    item["error_code"] = "VOICE_REFERENCE_REJECTED"
    item["error"] = f"the Voice worker rejected the reference: {reason}"
    return "terminal"


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
    # The worker's own count of the references it took. A bounded integer it stated, never a
    # number derived here; `terminal_result` publishes it for the Gateway to compare.
    count = job.get("reference_count")
    if type(count) is int and count >= 0:
        item["reference_count"] = count


def _record_worker_failure(item: dict, studio: dict, job: dict, t_start: float) -> None:
    """Retain the authenticated worker evidence needed to diagnose a failure."""
    _record_worker_execution_started_at(item, job)
    _record_worker_identity(item, studio, job)
    _record_worker_resource_usage(item, job)
    _record_worker_section_quality(item, job)
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


_SECTION_QUALITY_NUMBERS = frozenset({
    "section", "attempt", "duration_s", "dc_offset", "voiced_s", "voiced_ratio", "characters",
    "max_duration_s", "min_voiced_s", "words_expected", "words_heard", "word_overlap",
    "check_seconds", "longest_pause_s", "max_pause_s", "longest_missing_run",
})
_SECTION_TIMELINE_NUMBERS = ("section", "start_s", "end_s", "text_start", "text_end")
_SECTION_QUALITY_WORDS = {
    "transcribe_back": frozenset({"whisper", "unavailable", "memory", "script", "qwen_clone_gate"}),
    "text_check": frozenset({"advisory"}),
    "rejection_code": frozenset({"TTS_SECTION_SILENT", "TTS_SECTION_TOO_LONG",
                                 "TTS_SECTION_INTERNAL_SILENCE", "TTS_SECTION_TEXT_MISMATCH"}),
}


def _record_worker_section_quality(item: dict, job: dict) -> None:
    """Keep a voice job's per-section quality evidence (``tts_quality``), numbers and known words only."""
    raw = job.get("section_quality")
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list):
        return
    sections = []
    for record in raw["sections"][:500]:
        if not isinstance(record, dict):
            continue
        clean = {}
        for key, value in record.items():
            if key == "accepted" and isinstance(value, bool):
                clean[key] = value
            elif key in _SECTION_QUALITY_WORDS and value in _SECTION_QUALITY_WORDS[key]:
                clean[key] = value
            elif (key in _SECTION_QUALITY_NUMBERS and isinstance(value, (int, float))
                  and not isinstance(value, bool) and math.isfinite(value)):
                clean[key] = value
        sections.append(clean)
    item["section_quality"] = {"revision": str(raw.get("revision") or "")[:80], "sections": sections}
    # Where each section sits in the delivered audio and in the request's text (numbers only), so a
    # caller can time subtitles from its own script.
    timeline = [{key: row[key] for key in _SECTION_TIMELINE_NUMBERS
                 if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
                 and math.isfinite(row[key])}
                for row in (raw.get("timeline") or [])[:500] if isinstance(row, dict)]
    if timeline and all(len(row) == len(_SECTION_TIMELINE_NUMBERS) for row in timeline):
        item["section_quality"]["timeline"] = timeline


REVIEW_RECEIPT_SCHEMA = "studiohub.rejected-voice-review-candidate.v1"
REVIEW_MAX_PCM_BYTES = 64 * 1024 * 1024
# The worker shows `error` a moment before its receipt is durable (it answers 409
# `review_candidate_pending` until then), so the Hub asks again for this long before giving up.
REVIEW_PENDING_WAIT_S = 15.0
REVIEW_PENDING_POLL_S = 0.5
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _review_pending(response: httpx.Response) -> bool:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return False
    return response.status_code == 409 and isinstance(detail, dict) and \
        detail.get("code") == "review_candidate_pending"


def review_sha256(value) -> str:
    """The Voice worker's canonical receipt digest (`generation.review_sha256`), recomputed here."""
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()


def checked_review_receipt(receipt: object, *, worker_job_id: str, client_request_id: str) -> dict:
    """Re-prove a worker's rejected-section receipt; the locator facts it binds, or ValueError."""
    if not isinstance(receipt, dict):
        raise ValueError("receipt")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    audio, terminal, quality = (receipt.get(key) for key in ("audio", "terminal", "quality"))
    if not all(isinstance(part, dict) for part in (audio, terminal, quality)):
        raise ValueError("receipt")
    final = quality.get("final")
    if (receipt.get("schema") != REVIEW_RECEIPT_SCHEMA or receipt.get("active") is not False
            or receipt.get("disposition") != "review_only"
            or receipt.get("worker_job_id") != worker_job_id
            or receipt.get("client_request_id") != client_request_id
            or receipt.get("receipt_sha256") != review_sha256(body)
            or terminal.get("state") != "error"
            or not isinstance(final, dict)
            or terminal.get("final_quality_sha256") != review_sha256(final)
            or audio.get("media_type") != "audio/wav"
            or type(audio.get("bytes")) is not int
            or not 0 < audio["bytes"] <= REVIEW_MAX_PCM_BYTES
            or not isinstance(audio.get("sha256"), str)
            or not _SHA256_HEX.fullmatch(audio["sha256"])):
        raise ValueError("receipt")
    return {"worker_job_id": worker_job_id, "client_request_id": client_request_id,
            "final_quality_sha256": terminal["final_quality_sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "pcm_bytes": audio["bytes"], "pcm_sha256": audio["sha256"]}


async def _attach_review_candidate(client: httpx.AsyncClient, b: dict, item: dict,
                                   studio: dict, code: str) -> None:
    """Bind a Voice quality failure that is about to settle the item to its worker's candidate.

    Called just before the item turns terminal `error`, so the locator commits with that terminal
    item (`ledger.save_batch`). Optional and read-only: anything missing or mismatched binds
    nothing, and the item's own error is never changed.
    """
    job_id = str(item.get("studio_job_id") or "")
    if (b.get("modality") != "voice" or not code.startswith("TTS_SECTION_")
            or not job_id or not item.get("studio")):
        return
    expected_request_id = item.get("client_request_id") or f"studiohub:{b['id']}:{item['index']}"
    try:
        url, headers = studio_request(studio, f"/api/fleet/jobs/{job_id}/review-candidate")
        deadline = time.monotonic() + REVIEW_PENDING_WAIT_S
        while True:
            # Each read shares the one deadline, so a hung read can't stretch the Mac's hold.
            response = await client.get(url, headers=headers,
                                        timeout=max(0.5, deadline - time.monotonic()))
            if not _review_pending(response) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(REVIEW_PENDING_POLL_S)
        if response.status_code != 200:
            return
        receipt = response.json().get("receipt")
        facts = checked_review_receipt(receipt, worker_job_id=job_id,
                                       client_request_id=expected_request_id)
        if receipt["terminal"].get("rejection_code") != code:
            raise ValueError("rejection code")
        # The receipt's final row, cleaned exactly as this item's own copy was, must be that copy.
        scratch: dict = {}
        _record_worker_section_quality(scratch, {"section_quality": {
            "revision": receipt["quality"].get("revision"), "sections": [receipt["quality"]["final"]]}})
        recorded = (item.get("section_quality") or {}).get("sections") or [None]
        if scratch["section_quality"]["sections"][0] != recorded[-1]:
            raise ValueError("final quality row")
    except Exception as exc:  # noqa: BLE001 - an unavailable candidate is never a different error
        logging.getLogger("studiohub.broker").info(
            "review candidate not bound for %s:%s (%s)", b.get("id"), item.get("index"),
            type(exc).__name__)
        return
    item["_review_candidate"] = {"studio": str(item["studio"]), **facts}


async def _run_item(client: httpx.AsyncClient, b: dict, item: dict, studio: dict):
    endpoint, prompt_field, artifact_kind = MODALITY[b["modality"]]
    t_start = time.time()  # wall-clock fallback for generation duration
    item["run_started"] = t_start   # surfaced live for elapsed/ETA in the UI
    item["progress"] = None         # 0..1 as reported by the studio while running
    item["last_progress_at"] = t_start
    body = dict(b["shared_params"])
    body.update(item["params"])
    # Internal Hub field: only a producer snapshot may become dispatch authority, so a caller's
    # own value is dropped here and the generated one is added below.
    body.pop("reference_policy", None)
    body["repo"] = b["model"]
    body[prompt_field] = item["prompt"]
    if b["modality"] == "voice":
        body["client_request_id"] = (item.get("client_request_id")
                                     or f"studiohub:{b['id']}:{item['index']}")
    if b["modality"] in ("voice", "image"):
        floor = memory_admission.worker_free_floor_gb(
            b["model"], mode=(_item_mode(item) if b["modality"] == "image" else None))
        if floor is not None:   # the worker's own preflight must honour the same override / measurement
            body["memory_floor_gb"] = floor
    if item["seed"] is not None:
        body["seed"] = item["seed"]
    # `output_format` (compress at source) reaches only a worker that advertises it; the rest keep
    # producing their native file, which the caller's own fallback handles.
    if "output_format" in body and not await _lane_accepts_output_format(
            studio, b["model"], body["output_format"]):
        body.pop("output_format")
    for name in ("paragraph_pause_sec", "sentence_pause_sec"):
        if name in body and await _lane_parameter(studio, b["model"], name) is None:
            body.pop(name)
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
    reservation_owner: str | None = None

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
        handback = item.pop("_memory_handback", None)
        if handback:
            # It waited ADMISSION_WAIT_HANDBACK_S for free memory this Mac does not have: nothing was
            # sent and no attempt is spent; the Gateway re-routes it (a pinned job fails with this sentence).
            item.update(state="error", retry_at=None, error_code=INSUFFICIENT_MEMORY, error=handback)
            item["tries"] = max(0, int(item.get("tries") or 0) - 1)
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
            # Which voice this is, for the job rows: the name and id its sender staged it under.
            item["voice"] = {"name": reference.get("display_name") or "",
                             "id": reference.get("source_asset_id") or voice_reference_asset_id}
            if reference.get("transcript"):
                body["ref_transcript"] = reference["transcript"]
            if requeue_if_peer_changed():
                return
            url, headers = studio_request(
                studio, "/api/generate/txt2speech/reference"
            )
            # Immediately before the actual POST: the reservation covers the worker's own
            # generation window, not the Hub's asset preparation before dispatch.
            reservation_owner = _begin_preferred_reservation(studio, b["modality"])
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
                rejection = _worker_http_error(r)
                if _reference_refusal_verdict(item, rejection, _worker_reason(r)) == "raise":
                    raise rejection
                return
            item["voice_reference_asset_id"] = voice_reference_asset_id
        elif refs:
            entry = await _catalog_entry(studio, b["model"])
            try:
                if b["modality"] == "video":
                    # Video keeps its one `file` part and its one mode.
                    _video_mode, url, headers, files = await _reference_dispatch(
                        client, studio, b, entry, refs, ref_mode, ("img2video",))
                    body["mode"] = "img2video"
                    if requeue_if_peer_changed():
                        return
                    reservation_owner = _begin_preferred_reservation(studio, b["modality"])
                    r = await client.post(
                        url,
                        data=_video_multipart_fields(body),
                        files=files,
                        headers=headers)
                else:
                    # `edit` takes every reference the request names, in order, against the count
                    # window the worker published; `img2img` keeps its single `image` part.
                    policy: dict = {}
                    mode, url, headers, files = await _reference_dispatch(
                        client, studio, b, entry, refs, ref_mode, ("img2img", "edit"),
                        policy_out=policy)
                    body.update(policy)
                    if requeue_if_peer_changed():
                        return
                    reservation_owner = _begin_preferred_reservation(studio, b["modality"])
                    durable_post_attempted = True
                    r = await client.post(
                        url,
                        data=_multipart_fields(body),
                        files=files,
                        headers=headers)
            except (ValueError, httpx.HTTPError) as e:
                item["state"] = "error"
                item["error"] = f"reference images could not be dispatched: {e}"
                return

        else:
            if requeue_if_peer_changed():
                return
            url, headers = studio_request(studio, endpoint)
            reservation_owner = _begin_preferred_reservation(studio, b["modality"])
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
                code = str(worker_detail["code"])
                detail = str(worker_detail.get("detail") or "Voice reference preparation failed.")
                if _is_disk_full_refusal_text(code, detail):
                    # The Mac's output storage is full, not the reference bad: wait for space and
                    # leave that Mac out of selection, exactly as the generate path does.
                    refusal = RuntimeError(f"HTTP {r.status_code}: {detail}")
                    refusal.status_code = r.status_code
                    refusal.error_code = code
                    raise refusal
                item["state"] = "error"
                item["error_code"] = code
                item["error"] = detail
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
            if (isinstance(job, dict) and job.get("state") == "uncertain"
                    and job.get("error_code") == "EXECUTION_IDENTITY_LOST"):
                # The id reattached to the job a restarted worker can never resume. That attempt is
                # dead, so take the explicit re-run id and let the dispatcher send it again.
                _bump_rerun_id(b, item)
                error = RuntimeError(
                    "the Voice worker restarted under this job and never resumes it; re-running it")
                error.error_code = "WORKER_RESTART_UNCERTAIN"
                raise error
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
        if _superseded(item):
            # The original attempt's result was adopted while this re-run was being submitted.
            await _signal_worker_cancel(client, {"studio": studio["id"], "studio_job_id": job["id"]})
            return
        if b["modality"] in RECEIPT_DURABLE_MODALITIES:
            # The acceptance receipt below is the durability boundary. Record
            # a supplied start time in the same write so a failure cannot occur
            # first and requeue work the worker has already accepted.
            _record_worker_execution_started_at(item, job)
        else:
            _persist_worker_execution_started_at(b, item, job)
        _record_worker_identity(item, studio, job)
        _record_worker_resource_usage(item, job)
        _record_worker_section_quality(item, job)
        item["studio_job_id"] = job["id"]
        # The worker's own limit for this job on this Mac (Image: steps x that Mac's speed). Published
        # on the item so the Gateway polls to the same number the worker stops at.
        deadline = job.get("deadline_s")
        if (isinstance(deadline, (int, float)) and not isinstance(deadline, bool)
                and math.isfinite(deadline) and deadline > 0):
            item["deadline_s"] = float(deadline)
        # Adopt the worker's own stated lane limit once, if it is larger than the floor the
        # reservation started from. Same attempt token; never a fresh start stamp.
        _extend_preferred_reservation(studio, b["modality"], reservation_owner, deadline)
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
                if not _await_worker_cancel(
                        b, item, studio, lease_expired=lease_expired,
                        cancel_confirmed=cancel_confirmed,
                        phase="after submission"):
                    return
                # The worker took the stop signal: fall through and poll it to its own answer.
            elif b["modality"] == "voice" and item.get("studio_job_id"):
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
                    else cancelled_text(b)
                )
                return
        # poll the studio's async job until terminal
        while True:
            await asyncio.sleep(POLL_S)
            if _superseded(item):
                await _signal_worker_cancel(client, {"studio": studio["id"], "studio_job_id": job["id"]})
                return
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
                    if not _await_worker_cancel(
                            b, item, studio, lease_expired=lease_expired,
                            cancel_confirmed=cancel_confirmed,
                            phase="after acceptance"):
                        return
                elif b["modality"] == "voice" and item.get("studio_job_id"):
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
                        else cancelled_text(b)
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
                if (isinstance(j, dict) and j.get("state") == "uncertain"
                        and j.get("error_code") == "EXECUTION_IDENTITY_LOST"):
                    # Voice Studio restarted under this job and never resumes one: re-run it.
                    raise _worker_terminal_error(
                        str(j.get("error") or "Voice Studio restarted before completion"),
                        "WORKER_RESTART_UNCERTAIN")
                _validate_voice_worker_job(
                    item, j,
                    expected_request_id=str(body.get("client_request_id") or ""),
                )
            else:
                payload = jr.json()
                j = payload.get("job") if isinstance(payload, dict) else None
                if b["modality"] in DURABLE_ASYNC_MODALITIES:
                    # A peer's worker reports its own machine id, not the node id this item
                    # routes through. Without `remote` every poll of a peer job raised "changed
                    # its machine_id"; only `_recover_worker_job` (which passes it) kept the job,
                    # and only while it finished inside RECOVERY_WINDOW_S - so a Z-Image-Turbo
                    # 4-bit thumbnail (~300 s on an 8 GB node) was abandoned on three nodes and
                    # each kept running unpolled, holding that node's peer lease.
                    _validate_durable_worker_job(
                        b["modality"], item, j,
                        remote=not peers.is_local_studio(studio))
            if _superseded(item):
                return  # the original won while this status request was in flight
            if peers.is_local_studio(studio):
                # This Mac's own Lite Mode stopped it (critical memory pressure): read it exactly as
                # a node's handed-back job reads through the peer surface.
                handed_back = workstation.rewrite_status(studio["id"], job["id"], 200, {"job": j})
                if handed_back is not None:
                    j = handed_back[1]["job"]
            _persist_worker_execution_started_at(b, item, j)
            _record_worker_identity(item, studio, j)
            _record_worker_resource_usage(item, j)
            _record_worker_section_quality(item, j)
            state = j.get("state")
            if state in ("queued", "running", "cancel_requested"):
                _record_worker_progress(item, j.get("progress"), j.get("progress_stage"))
                item["chunk_index"] = j.get("chunk_index")
                item["chunk_total"] = j.get("chunk_total")
                if await _stop_a_stalled_local_job(client, b, item, studio, j):
                    return
                continue
            if j.get("error") or state in ("error", "cancelled"):
                if b["cancelled"]:
                    _record_worker_failure(item, studio, j, t_start)
                    terminal_state = state if state in {"error", "cancelled"} else "error"
                    item.update(
                        state=terminal_state,
                        error=str(j.get("error") or terminal_state)[:500],
                        error_code=str(
                            j.get("error_code")
                            or ("CANCELLED" if terminal_state == "cancelled"
                                else "WORKER_TERMINAL_ERROR")
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
        if _voice_recovery_owns(item) or _superseded(item):
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
                item["error"] = cancelled_text(b)
            return
        recovered = (False if getattr(e, "worker_terminal", False)
                     else await _recover_worker_job(client, b, item, studio, body, t_start))
        if _voice_recovery_owns(item) or recovered is True:
            return
        if isinstance(recovered, dict):
            e = _classify_worker_failure(b, item, studio, recovered, t_start)
        message = str(e) or type(e).__name__
        # A worker that stopped answering, lost the job or restarted under it cannot be waited
        # on: the owner's rule (2026-10-03, every lane, this Mac or another) is to re-run rather
        # than fence. The abandoned attempt falls through to the bounded retry below, which
        # avoids a silent machine and limits the attempts, while `_watch_abandoned_attempt`
        # cancels the original - or adopts it if it finished first. What stays `uncertain` is a
        # worker that answered with something that is not this job, or a lost local receipt.
        remote_studio = not peers.is_local_studio(studio)
        code = str(getattr(e, "error_code", None) or item.get("error_code") or "")
        abandoned = False  # set when the attempt is given up on below, which makes it re-runnable
        if code == "WORKER_RESTART_UNCERTAIN" and not b["cancelled"]:
            # The worker restarted under the job and never resumes it: run it again - on this Mac
            # once its worker is back, or elsewhere, whichever is free first (owner, 2026-10-03).
            # It still spends a try, so a job that crashes its worker every time ends.
            _abandon_attempt(client, b, item, studio, body, t_start)
            abandoned = True
        elif b.get("modality") in DURABLE_ASYNC_MODALITIES:
            if code == "JOB_PERSISTENCE_UNCERTAIN":
                _mark_async_uncertain(
                    item, studio, b["modality"],
                    f"The accepted {b['modality'].title()} job has an uncertain final result; "
                    "reconcile its original worker receipt before any retry.", code,
                )
                return
            went_away = _is_transport_failure(e, message) or getattr(e, "status_code", None) == 404
            if (item.get("studio_job_id") and recovered is False
                    and not getattr(e, "worker_terminal", False)):
                if went_away or (remote_studio and b.get("modality") == "image"):
                    # Its worker - on another Mac or this one - stopped answering for the whole
                    # recovery window, or no longer has the job: re-run it (owner, 2026-10-03).
                    # Music and Video used to stay `uncertain` here, and any local job too.
                    _abandon_attempt(client, b, item, studio, body, t_start)
                    abandoned = True
                else:
                    # The worker answered, but with a receipt that is not this job's: fenced.
                    _mark_async_uncertain(
                        item, studio, b["modality"],
                        f"The accepted {b['modality'].title()} job could not be reconciled; "
                        "it was not retried automatically.",
                    )
                    return
            if (durable_post_attempted and not durable_post_rejected and not abandoned
                    and not item.get("studio_job_id") and not remote_studio):
                # A local worker that may have accepted the POST but whose receipt was lost:
                # there is no job id to cancel or adopt, and this Mac is still here, so its worker
                # is reconciled rather than doubled. A remote one is re-run like any lost node.
                _mark_async_uncertain(
                    item, studio, b["modality"],
                    f"{b['modality'].title()} submission may have been accepted but its worker "
                    "receipt was lost; reconcile before any retry.",
                )
                return
        if (b.get("modality") == "voice" and item.get("studio_job_id")
                and _is_transport_failure(e, message)):
            _abandon_attempt(client, b, item, studio, body, t_start)
            abandoned = True
        # A 404 from a worker that lost the job is not retryable by itself; an abandoned attempt is.
        retryable = getattr(e, "retryable", True) or abandoned
        item["last_progress_at"] = time.time()
        now = time.time()
        if code == WORKSTATION_YIELDED and not b["cancelled"]:
            # A workstation stopped this job for its own user (input, or free memory under its
            # floor). Nothing was kept and nothing is pending, and it is not that Mac's failure or
            # an attempt: another Mac of this group takes it now if one is free, otherwise it goes
            # back to the Gateway at once, typed, to be moved to another group.
            machine = studio.get("machine", "local")
            item["tries"] = max(0, int(item.get("tries") or 0) - 1)
            item["retry_at"] = None
            item.setdefault("avoid_machines", {})[machine] = now + WORKSTATION_STANDING_BY_AVOID_S
            if _another_machine_free(b, machine):
                item.update(state="queued", error=f"moved to another Mac: {message}")
            elif peers.is_local_studio(studio) and not _is_controller():
                # A standalone's or a node's own queue has no Gateway hand-back: it waits here
                # until this Mac takes work again.
                item.update(state="queued", error=f"waiting: {message}")
            else:
                item.update(state="error", error=message, error_code=WORKSTATION_YIELDED)
        elif retryable and _is_capacity_failure(message) and not b["cancelled"]:
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
                            error=_insufficient_memory_text(b, studio, message, failures))
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
            avoid = _WORKSTATION_REFUSALS.get(code, MACHINE_RESERVED_AVOID_S)
            item.setdefault("avoid_machines", {})[machine] = now + avoid
            if code != "WORKSTATION_MODEL_NOT_ALLOWED":   # a lane it does not run says nothing of the others
                _machine_reserved_until[machine] = now + avoid
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
            _record_infra_failure(item, studio, message, now)
        elif retryable and item["tries"] < MAX_TRIES and not b["cancelled"]:
            item["state"] = "queued"  # work-stealing retry, possibly elsewhere
            item["error"] = f"try {item['tries']} failed: {message}"
            delay_index = min(item["tries"] - 1, len(RETRY_DELAYS_S) - 1)
            item["retry_at"] = time.time() + RETRY_DELAYS_S[delay_index]
        else:
            await _attach_review_candidate(client, b, item, studio, code)
            item["state"] = "error"
            item["error"] = message
            item["retry_at"] = None
    finally:
        if item["state"] in ("done", "error", "cancelled"):
            item.pop("capacity_failures", None)
            item.pop("capacity_wait_started_at", None)
            item.pop("admission_wait_started_at", None)
            item.pop("_memory_handback", None)
            item.setdefault("machine", str(studio.get("machine") or "local")[:500])
            item.setdefault("worker_id", str(studio.get("id") or "unknown")[:500])
            if item["state"] == "error":
                item.setdefault("error_code", "WORKER_TERMINAL_ERROR")
            runtime = round(time.time() - t_start, 2)
            item.setdefault("runtime_s", runtime)
            item.setdefault("duration_s", item["runtime_s"])
            item.setdefault("finished_at", time.time())
            item["last_progress_at"] = item["finished_at"]
            # A real terminal transition (done, failed or cancelled) of preferred work opens
            # the hold. A queued retry or an uncertain attempt proves nothing and writes none.
            _note_preferred_completion(studio, b.get("modality"),
                                       reservation_owner=reservation_owner)
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
