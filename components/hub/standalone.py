"""The packaged, private Studio Hub API surface.

This module intentionally exposes the durable job, transcription and settings
contracts. Legacy Pinokio control, updater and fleet-repair routes are not
registered by the packaged application.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from starlette.concurrency import run_in_threadpool

from components.os_metadata import is_os_metadata
from . import disk_governor, output_storage, standalone_config, workstation
from .backend import auth, broker, cloud_guard, control_plane, ledger, model_ids, resources, routing as routing_rule
from .backend import app_release, chat_deadline, chat_jobs, peers, release_sync, shared_voices, startup_control, transcription_jobs, update_drain, vocabulary
from .backend import render_executor as render_executor_module, render_service
from .backend.render_assets import RenderAssetStore
from .backend.render_executor import (
    InstalledRenderAuthorityLoader, LateLabelProbeScope, RenderExecutor, SubtitleProbeScope,
)
from .backend.render_encode_probes import EncodeProbeControl
from .backend.render_jobs import RenderJobRuntime
from .backend.monitor import StudioMonitor, is_cached

# The key one unusable manifest carries into the catalog so its row can say what is wrong
# without taking the other packages down. Private to this module: it is an implementation
# detail of `_model_catalog`, and no worker or manifest may set it.
_MANIFEST_ERROR = "_manifest_error"

# Who took the last update hold. Only the installer's 0600-token request makes a later quit
# updater-authored; in-memory and self-expiring, so an aborted install cannot latch it.
_installer_hold_until = 0.0

# One start stamp for this process: a health poll must never move it.
_SERVER_STARTED_AT = time.time()

# The deadline the Hub holds a proxied job to, and the number it publishes as
# `deadline_seconds`. One owner — this process, which enforces it — but **not one number**: a
# subtitle job finishes in seconds and a video job takes the better part of an hour, so a single
# flat ceiling is either too short for video or so long that a broken subtitle job is not noticed
# for hours. The owner asked exactly that question, and it is the right one:
#
#     "if we raise it too high for, say subtitle and it failed, could we find out or we have to
#      wait 2 hours to findout?"
#
# So the deadline is derived, in one place, from two things that are themselves evidence:
#
#   * the model's **own measured runtime**, which the worker publishes as
#     `lane_contract.runtime_seconds` only when it has a measurement behind it, and
#   * a **per-generator floor**, for lanes that have no measurement yet.
#
# A lane with no measurement gets its floor, and nothing more, because there is no evidence to
# justify more. A lane that has been measured gets headroom over the real number. Nothing here is
# invented, and nothing is restated by a worker.
PEER_PROXY_TIMEOUT_FLOOR_S = 900.0

# The floor each generator starts from when no measurement exists. Keyed by modality, because
# that is the vocabulary the Hub already routes and enforces on (`vocabulary.WORKER_FOR_MODALITY`).
# A modality absent from this table gets `PEER_PROXY_TIMEOUT_FLOOR_S` — a conservative default
# rather than a refusal, since a *short* deadline can only cut a job short, and the alternative
# (falling through to the longest floor) would be the silent-wait failure this table exists to fix.
#
# `video` is the only lane that differs today, and it differs on measurement: the local video
# runtime's own validated runs on an M2 Pro 32 GB are 44:38 for q8 HQ at 480x704x97 and ~15 min for
# the 1280x704x97 identity test. 900 s would cut the first off at a third of the way through.
MODALITY_DEADLINE_FLOOR_S: dict[str, float] = {
    # The local video runtime's own measured runs on an M2 Pro 32 GB: 44:38 for q8 HQ at
    # 480x704x97, ~15 min for the 1280x704x97 identity test. A 900 s wall cuts the first off a
    # third through.
    "video": 7200.0,
    # Chat is the LLM lane, and GatewayKH measured the workload that sets this floor: 30 scenes at
    # ~403 tokens each is 12,090 tokens, 1,008 s on an M1. They asked for the whole Hub ceiling to
    # go to 1,800 s to cover it, and stated the cost they were accepting — *"every job may hang 30
    # min instead of 15, including fast ones."* **That cost is not necessary and is not paid here.**
    # Because this table is per generator, chat gets 1,800 s and subtitle and music keep 900 s
    # (voice and image were raised later, each on its own measurement), so a broken subtitle job
    # is still noticed in 15 minutes rather than 30. This is the whole reason the deadline stopped
    # being one flat number.
    "chat": 1800.0,
    # Voice, raised 900 -> 1,800 s on 2026-09-26 at Story Studio's report. This lane is the one
    # that actually failed in the field: a **~17-minute narration part was killed at the 900 s
    # wall**, which is the whole reason the flat constant had to go.
    #
    # 1,800 rather than 1,200: 1,200 is only three minutes of headroom over the part that already
    # died, and that part ran on ONE Mac. The same narration on a slower machine among the fleet's
    # 35 eight-GB and 15 sixteen-GB nodes will take longer, so "just enough" buys the identical
    # failure again at eighteen minutes. 1,800 is 1.76x the observed worst case, and it matches
    # chat, so the two long-form lanes now share one number and one story.
    #
    # The reason a long ceiling is now safe rather than merely long: `stall.py` catches a worker
    # that has gone quiet in about two minutes, whatever this number is. The ceiling no longer has
    # to double as a failure detector, which is exactly what made 1,200 look attractive.
    #
    # 17 minutes is an observation of a failure, not a clean measurement, and 1.76x is inference.
    # The right end state is to measure a real voice part and derive this, as Z-Image's 8 GB floor
    # was derived from the owner's run.
    #
    # Raised 1,800 -> 2,700 s on 2026-10-05, now on a clean measurement: the night-atlas run
    # (2,311 TTS parts, 2026-10-04/05) had its slowest *successful* 1,500-character Breeze part take
    # 1,747 s on an M1 16 GB (97% of the old wall), and 5 parts died at exactly 1,800 s on the same
    # class, pushed over by section re-renders (avg 1.37, max 3.47 renders per delivered section)
    # and model/Whisper load. 2,700 is 1.55x that measured tail. Stall detection is unchanged, so
    # a quiet worker is still caught in minutes. The Gateway scales longer parts per Mac on top.
    "voice": 2700.0,
    # Image, raised 900 -> 1,800 s on 2026-10-08 with Qwen Image 2.1 qualified on 24 GB: the owner's
    # 24 GiB M4 took 440.6-500.3 s for 16 steps (qualification/evidence/qwen-image-21-20261008-24gb),
    # and the model accepts up to 50 steps. Since 0.6.159 each Image job also carries its own
    # `deadline_s` (steps x that Mac's speed, 900-7,200 s; `generation.job_deadline_s`) on its batch
    # item; the Gateway polls to the larger of the two, so this stays the minimum any job gets.
    "image": 1800.0,
}


def deadline_floor_seconds(modality: str | None) -> float:
    """The deadline this Hub holds one generator's job to, and publishes as `deadline_seconds`.

    **One function, deliberately, because the published number and the enforced one must be the
    same number.** The old design said so in a comment — "the deadline a scheduler reads is the one
    the Hub will actually hold it to" — and that promise is easy to break by deriving the published
    value per *model* while the proxy, which knows the worker but not the model, derives its own.
    So the deadline is a property of the **generator**, which both call sites can name.

    A per-model refinement built on the worker's own measured `runtime_seconds` is the right next
    step and is *not* done here, because it would need the model plumbed to the enforcement site
    first. When it lands it must move this function, so both sides move together.

    **Chat is the one lane where this is a floor rather than the enforced number**, since the owner
    decision of 2026-10-19: a Chat job's deadline is computed from the job itself
    (`backend/chat_deadline.py`) and published on the job row as `deadline_s`/`deadline_note`,
    because a 100-prompt scene pack and a one-line question cannot share one number. What this
    function still answers for chat is the *minimum* — the number no Chat job is given less than —
    which is what a scheduler reads a contract for, and what the Gateway now treats it as.
    """
    return MODALITY_DEADLINE_FLOOR_S.get(str(modality or ""), PEER_PROXY_TIMEOUT_FLOOR_S)


def modality_for_worker(worker_id: str | None) -> str | None:
    """The modality a worker serves, from the Hub's own routing vocabulary."""
    return vocabulary.REQUIRED_WORKERS.get(str(worker_id or ""))


def _model_library() -> Path:
    value = os.environ.get("STUDIOHUB_MODELS_DIR", "").strip()
    if not value:
        raise ValueError("STUDIOHUB_MODELS_DIR is required for model management")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("selected model library is unavailable")
    return root


def _model_catalog() -> dict[str, dict[str, Any]]:
    """Every manifest in the catalog directory, keyed by package id.

    Each manifest is read and validated **on its own**. A file that cannot be parsed, or that
    fails the manifest contract, becomes one entry carrying a ``_MANIFEST_ERROR`` reason rather
    than raising for the whole catalog: this function feeds `/api/hub/model-packages`, the app
    reads that route with a hard ``try`` inside its refresh, and one malformed manifest used to
    answer 503 for every package - which marked the Hub unreachable and blanked every model
    picker on every Mac. Only a catalog directory that cannot be listed at all still raises,
    because "no models" would be a lie rather than a degradation.
    """
    from components import model_packages, retired_models
    location = os.environ.get("STUDIOHUB_MODEL_MANIFESTS", "").strip()
    root = Path(location).expanduser().resolve() if location else Path(__file__).resolve().parents[2] / "model-manifests"
    try:
        paths = sorted(path for path in root.glob("*.json") if not is_os_metadata(path.name))
    except OSError as exc:
        raise ValueError(f"model manifest catalog is unavailable: {root}") from exc
    catalog: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            catalog[path.stem] = _manifest_error(
                {"package_id": path.stem, "model_id": path.stem},
                f"{path.name} could not be read as JSON: {exc}")
            continue
        if not isinstance(manifest, dict):
            catalog[path.stem] = _manifest_error(
                {"package_id": path.stem, "model_id": path.stem},
                f"{path.name} is not a manifest object")
            continue
        if retired_models.manifest_retired(manifest, path.stem):
            continue                            # retired: never offered, even from an old manifest
        key = str(manifest.get("package_id") or path.stem)
        try:
            model_packages.validate_manifest(manifest)
        except ValueError as exc:
            catalog[key] = _manifest_error(manifest, f"{path.name} is not a usable manifest: {exc}")
            continue
        catalog[key] = manifest
    return catalog


def _manifest_error(manifest: dict[str, Any], reason: str) -> dict[str, Any]:
    """One unusable manifest, kept in the catalog so it can have its own row.

    The reason travels with it: a package that cannot be used is a fact the operator needs to
    see where the model is chosen, not a 503 that hides every other package.
    """
    return {**manifest, _MANIFEST_ERROR: reason}


def _row_modality(manifest: dict[str, Any]) -> str | None:
    """The modality for a row whose manifest may be unusable.

    An unregistered engine has no modality to publish, and that is the row's own reason: the
    engine allowlist message names the file to edit. Anything else publishes its modality, so
    a reader never has to guess one from the engine name.
    """
    engine = str(manifest.get("engine") or "").strip()
    if not engine:
        return None
    try:
        return _modality_for_engine(engine)
    except ValueError:
        return None


def _manifest_error_row(manifest: dict[str, Any], reason: str, *,
                        hint: str | None = None) -> dict[str, Any]:
    """The `/api/hub/model-packages` row for a package that cannot be used.

    Shaped like a real row - the app decodes `package_id`, `model_id`, `state` and
    `dispatch_state` as required fields, so an error row missing them would fail the whole
    response's decoding and blank the pickers a second way.
    """
    from components import model_packages

    package_id = str(manifest.get("package_id") or "unknown")
    allowed = model_packages.production_allowed(manifest)
    text = reason if reason.endswith(".") else f"{reason}."
    if hint:
        text = f"{text} {hint}"
    return {
        "package_id": package_id,
        "model_id": str(manifest.get("model_id") or package_id),
        "engine": manifest.get("engine"),
        # Present whenever the engine is registered, even on a row whose manifest is unusable:
        # a scheduler reading the row must not need a second join to learn the lane.
        "modality": _row_modality(manifest),
        "lane_contract": None,
        "revision": manifest.get("revision"),
        "state": "invalid",
        "files_state": "invalid",
        "runtime_state": "unknown",
        "os_state": "unknown",
        "chip_state": "unknown",
        "worker_state": "unavailable",
        "worker_id": None,
        "minimum_macos": manifest.get("minimum_macos"),
        "macos_version": None,
        "production_allowed": allowed,
        "admission_state": "manifest_invalid" if allowed else "production_blocked",
        "admission_reason": text,
        "dispatch_state": "ineligible",
        "type": "model",
        "dependencies": _declared_companions(manifest),
        "paused": False,
        "paused_reason": None,
        "paused_studio": None,
        "paused_failures": None,
        "manifest_error": True,
        # The app shows `reason` verbatim under the model, so it says what is wrong. A silent
        # unavailability with no cause is the failure this whole route exists to prevent.
        "reason": text,
    }


def _dependency_package_ids(catalog: dict[str, dict[str, Any]]) -> set[str]:
    """Package ids that exist only because another package needs them.

    A codec, a tokenizer and a real model are indistinguishable in this row otherwise: all of them
    report `engine=voice-mlx`, `state=files_verified` and `runtime_state=ready`, which is how a
    tokenizer ended up offered in a voice picker on the gateway's side. The catalogue already says
    which packages are dependencies - each manifest lists its companions - so the fact is stated
    here rather than left to be inferred from membership of some other list.

    Read through `model_packages.required_packages`, which honours both spellings the shipped
    catalog uses. Reading one spelling here and the other in `model_packages` typed
    `s3tokenizerv2` correctly while publishing `whisper-turbo` - a Subtitle ASR package two Qwen
    voice packages require - as a pickable Voice model.
    """
    from components import model_packages
    required: set[str] = set()
    for entry in catalog.values():
        if entry.get(_MANIFEST_ERROR):
            continue
        try:
            required.update(model_packages.required_packages(entry))
        except ValueError:  # an unusable manifest is already its own row
            continue
    return required


def _declared_companions(manifest: dict[str, Any]) -> list[str]:
    """The companions a row publishes, tolerating a manifest too broken to resolve them."""
    from components import model_packages
    try:
        return list(model_packages.required_packages(manifest))
    except ValueError:
        return []


# engine -> (the worker that runs it, the modality a job for it must declare). ONE map, because
# this vocabulary has been restated three times in this area and every copy drifted: the worker
# map itself (`chat-mlx` → HTTP 500 → every picker blank), the inline modality dict in
# `_submission_readiness` (an engine registered everywhere but there returned None, and every
# job for every model of that engine was refused with MODEL_MODALITY_MISMATCH - "model is not
# available for this job modality", which blames the operator's request instead of the missing
# map), and the fleet join's copy.
_ENGINE_ROUTE: dict[str, tuple[str, str]] = {
    "image-mflux": ("image", "image"),
    "image-mlx-serve": ("image", "image"),
    "voice-mlx": ("voice", "voice"),
    "voice-vits": ("voice", "voice"),
    "subtitle-mlx": ("subtitle", "transcription"),
    "music-mlx": ("music", "music"),
    # The Music lane's second engine. YuE2 runs in its own pinned runtime because the port pins
    # `mlx==0.32.2` while the Music overlay carries `mlx==0.31.2`; the worker picks the
    # interpreter from the manifest's engine, exactly as the Video lane does for its own.
    "music-yue2": ("music", "music"),
    "chat-mlx": ("chat", "chat"),
    # Video is one engine. No video pack is offered today (`components/retired_models.py`); the
    # engine id stays as the lane's routing key so a successor pack changes no route.
    "video-ltx": ("video", "video"),
    "embed-mlx": ("embeddings", "embeddings"),
    "vision-mlx": ("vision", "vision"),
}

_ENGINE_REGISTRATION_HINT = (
    "add it to _ENGINE_ROUTE in components/hub/standalone.py and to the engine allowlist in "
    "components/model_packages.py"
)


def _engine_route(engine: str) -> tuple[str, str]:
    try:
        return _ENGINE_ROUTE[engine]
    except KeyError as exc:
        raise ValueError(
            f"the manifest engine {engine!r} has no worker or modality; "
            f"{_ENGINE_REGISTRATION_HINT}") from exc


def _worker_for_engine(engine: str) -> str:
    """Which worker runs a manifest's engine.

    Kept as one named mapping rather than an inline dict because it has to agree with the engine
    allowlist in `components/model_packages.py`, and for a while it did not: `chat-mlx` was added
    to the allowlist and to the shipped catalog, but not here, so `/api/hub/model-packages` raised
    `KeyError: 'chat-mlx'` and answered 500. The app reads that route with a hard `try` inside its
    refresh, so the 500 did not degrade one list - it marked the whole Hub unreachable and left
    every model picker empty. `tests/test_model_manifest_catalog.py` now fails if the two lists
    drift apart again, and it checks the modality half of the same map as well.
    """
    return _engine_route(engine)[0]


def _modality_for_engine(engine: str) -> str:
    """Which modality a job for a manifest's engine must declare.

    Read from the same map as the worker, so registering an engine in one place registers both.
    """
    return _engine_route(engine)[1]


def _model_row(manifest: dict[str, Any], catalog: dict[str, dict[str, Any]],
               monitor: StudioMonitor,
               lane_contracts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """One `/api/hub/model-packages` row, isolated from every other row.

    A manifest that cannot be used gets its own row carrying the reason instead of an exception
    that takes the whole route - and therefore every model picker on every Mac - down with it.
    The catch is deliberately broad: the point of the isolation is that a package the Hub has
    never seen before must not be able to fail the catalog in a way this code did not predict.
    """
    reason = str(manifest.get(_MANIFEST_ERROR) or "")
    if reason:
        return _manifest_error_row(
            manifest, f"Its package manifest is unusable: {reason}",
            hint=("Fix or remove that file in the Hub's model-manifests folder, "
                  "then reload."))
    try:
        return _model_state(manifest, catalog, monitor, lane_contracts)
    except Exception as exc:  # noqa: BLE001 - one package must never blank the catalog
        logging.getLogger("studiohub.models").exception(
            "model package %r could not be described",
            manifest.get("package_id") or manifest.get("model_id"))
        return _manifest_error_row(
            manifest, f"The Hub could not describe this package: {exc}")


def _lane_contracts(monitor: StudioMonitor) -> dict[str, dict[str, Any]]:
    """Last-known ``lane_contract`` per model identifier, from the studios' own catalogs.

    The studio is the only thing that knows its own API, so the Hub carries the contract the
    worker published and never invents one. A studio that is not currently up contributes
    nothing - a contract from a worker that is down describes a build that is not running - and
    any failure here yields an empty map: this join must never be able to fail a package row.
    That is the 0.6.57 lesson, where one row's failure blanked every picker on every Mac.
    """
    contracts: dict[str, dict[str, Any]] = {}
    try:
        rows = monitor.cached_aggregate_catalog().get("models") or []
    except Exception:  # noqa: BLE001 - a missing contract is not a failed package row
        return contracts
    for row in rows:
        if not isinstance(row, dict):
            continue
        contract = row.get("lane_contract")
        if not isinstance(contract, dict):
            continue
        studio_id = row.get("hub_studio")
        if studio_id and monitor.status.get(studio_id, {}).get("status") != "up":
            continue
        for identifier in (row.get("repo"), row.get("model_id"), row.get("package_id"),
                           *(row.get("aliases") or [])):
            key = str(identifier or "").strip()
            if key:
                contracts.setdefault(key, contract)
    return contracts


def _contract_for(manifest: dict[str, Any],
                  lane_contracts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key in (manifest.get("model_id"), manifest.get("package_id")):
        contract = lane_contracts.get(str(key or "").strip())
        if contract is not None:
            return contract
    return None


# **There is no shelving any more (owner decision 2026-10-28).** "A model is blocked by RAM, never
# by name": the seven packages that were on `components/shelved_models.SHELVED_MODELS`
# (whisper-base, whisper-small, voxcpm2-bf16, fish-audio-s2-pro-bf16, chatterbox-4bit,
# chatterbox-8bit, s3tokenizerv2) are ordinary catalog entries now - listed, tickable, and gated
# only by their own RAM floor (`_memory_refusal` below). The mechanism went with the list: no
# start-up move-out, no download refusal, no kit skip. A copy already installed on a Mac stays
# exactly where it is and works; only `retired_models` still moves anything out.


def _memory_refusal(manifest: dict[str, Any]) -> None:
    """A model this Mac has too little memory to run is not downloaded onto it by the fleet.

    The floor is the scheduler's own (`memory_admission.requirement_gb`: operator override, measured,
    manifest tier), so what is refused here is exactly what dispatch would never send to this Mac.
    """
    import psutil
    from .backend import memory_admission
    floor = (memory_admission.requirement_gb(manifest["model_id"], {})[0]
             or (manifest.get("qualification") or {}).get("minimum_unified_memory_gb"))
    memory_gb = round(psutil.virtual_memory().total / (1024 ** 3))
    if floor and memory_gb < float(floor):
        raise HTTPException(409, (
            f"Not on this Mac: {manifest['package_id']} needs a Mac with at least {float(floor):g} GB of "
            f"memory and this one has {memory_gb} GB, so it could never run here. Nothing was downloaded."))


# **One Mac's memory admission, for the fleet route only (proposal.r5).** The local Models-screen
# route stays model-wide and unchanged. This pair validates the fleet body strictly, resolves one
# RAM pair/reset or one reference row, and returns the same effective readback the local screen
# reads. Reference writes are refused here until D4 runtime proof exists for the running Hub:
# frozen r5 lines 298/320-323 require a live-Hub read of the packaged table before acceptance,
# so source/table verification is not enough. Accepting a row nothing has proven readable would
# make the screen show a policy the installed Hub cannot resolve.
_FLEET_ADMISSION_KEYS = {"model", "mode", "min_free_memory_gb", "min_total_memory_gb",
                         "reference", "reference_count", "expected_revision"}
_REFERENCE_FIELDS = {"allowed", "max_reference_images", "swap_allowance_gb"}


def _admission_total_memory_bytes() -> int | None:
    """This Mac's physical memory, or ``None`` when telemetry cannot be read.

    ``None`` is the honest unknown: the model-wide RAM answer still publishes, and the core
    resolves the mode reference policy as unavailable rather than banding unknown bytes ``<8``.
    """
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001 - telemetry failure is an answer, not a 500
        return None


def _fleet_admission_refusal(code: str, field: str, detail: str, node: str,
                             node_id: str) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "field": field, "detail": detail,
                         "node": node, "node_id": node_id}, status_code=400)


def _fleet_unknown_model(model: str, node: str, node_id: str) -> JSONResponse:
    """A model this Mac does not have is a named 404 on the fleet shape, not a bare string.

    The bare ``HTTPException(404, "unknown model")`` the local screen raises is unwireable: the
    Gateway asked this route to keep its own envelope (code/detail/status/identity) so a
    controller relay can pass the answer through unchanged.
    """
    return JSONResponse({"ok": False, "code": "UNKNOWN_MODEL", "field": "model",
                         "detail": "this Mac has no model like that", "model": model,
                         "node": node, "node_id": node_id}, status_code=404)


def _fleet_memory_admission_read(*, model: str, mode: str | None, entry: dict | None,
                                 node: str, node_id: str) -> JSONResponse:
    from .backend import memory_admission
    if not model:
        return _fleet_admission_refusal("MISSING_MODEL", "model", "model is required",
                                        node, node_id)
    if mode is not None and mode not in memory_admission.MODES:
        return _fleet_admission_refusal(
            "BAD_MODE", "mode", "mode must be one of txt2img, edit", node, node_id)
    if entry is None:
        return _fleet_unknown_model(model, node, node_id)
    state = memory_admission.effective_state(
        model, entry, node_id=node_id, mode=mode,
        total_memory_bytes=_admission_total_memory_bytes(), model_default=None)
    return JSONResponse({"ok": True, "node": node, "node_id": node_id, **state})


def _fleet_memory_admission_write(*, body: dict, entry: dict | None, node: str,
                                  node_id: str, total_memory_bytes: int) -> JSONResponse:
    from .backend import memory_admission
    def fail(code: str, field: str, detail: str) -> JSONResponse:
        return _fleet_admission_refusal(code, field, detail, node, node_id)

    unknown = set(body) - _FLEET_ADMISSION_KEYS
    if unknown:
        name = sorted(unknown)[0]
        return fail("UNKNOWN_FIELD", name, f"{name} is not accepted on this route")
    revision = body.get("expected_revision")
    if (not isinstance(revision, str) or len(revision) != 64
            or any(char not in "0123456789abcdef" for char in revision)):
        return fail("BAD_VALUE", "expected_revision",
                    "expected_revision must be 64 lowercase hex characters")
    model = str(body.get("model") or "").strip()
    if not model:
        return fail("MODEL_REQUIRED", "model", "model is required")
    mode = body.get("mode")
    if mode is not None and mode not in memory_admission.MODES:
        return fail("BAD_MODE", "mode", "mode must be one of txt2img, edit")
    if mode is None and ("reference" in body or "reference_count" in body):
        return fail("BAD_MODE_CONTEXT", "mode",
                    "reference and reference_count require mode (txt2img or edit)")

    has_free = "min_free_memory_gb" in body
    has_total = "min_total_memory_gb" in body
    free = body.get("min_free_memory_gb")
    total = body.get("min_total_memory_gb")
    ram_reset = ram_write = False
    if has_free or has_total:
        # A present numeric value is validated before the pair/reset shape is classified, so an
        # invalid number is named as one instead of reading as a missing or mixed pair.
        for field, present, value in (("min_free_memory_gb", has_free, free),
                                      ("min_total_memory_gb", has_total, total)):
            if present and value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                return fail("BAD_VALUE", field,
                            f"{field} must be a finite number >= 0")
        if has_free and free is None:
            if total is None:
                ram_reset = True
            else:
                return fail("RAM_RESET_MIXED", "min_total_memory_gb",
                            "a reset sends both memory values null or absent")
        elif not has_free or not has_total or total is None:
            return fail("RAM_PAIR_REQUIRED", "min_total_memory_gb",
                        "a numeric RAM write requires both min_free_memory_gb and "
                        "min_total_memory_gb")
        else:
            ram_write = True

    reference_present = "reference" in body
    count_present = "reference_count" in body
    if count_present and not reference_present:
        return fail("REFERENCE_COUNT_WITHOUT_REFERENCE", "reference_count",
                    "reference_count is only valid with reference")
    if reference_present and not count_present:
        return fail("REFERENCE_COUNT_REQUIRED", "reference_count",
                    "send reference_count: null for the generic row or an exact count")
    if reference_present:
        count = body.get("reference_count")
        if count is not None and (isinstance(count, bool)
                                  or not isinstance(count, int) or count < 1):
            return fail("BAD_REFERENCE_COUNT", "reference_count",
                        "reference_count must be null or a positive integer")
        value = body.get("reference")
        if value is not None:
            if not isinstance(value, dict):
                return fail("BAD_VALUE", "reference", "reference must be an object or null")
            unknown_reference = set(value) - _REFERENCE_FIELDS
            if unknown_reference:
                name = sorted(unknown_reference)[0]
                return fail("UNKNOWN_FIELD", name, f"{name} is not a reference field")
            if not value:
                return fail("REFERENCE_EMPTY", "reference",
                            "reference must name a field, or be null to clear the row")
            allowed = value.get("allowed")
            if "allowed" in value and allowed is not None and type(allowed) is not bool:
                return fail("BAD_VALUE", "allowed", "allowed must be a boolean or null")
            maximum = value.get("max_reference_images")
            if ("max_reference_images" in value and maximum is not None
                    and (isinstance(maximum, bool) or not isinstance(maximum, int)
                         or maximum < 0)):
                return fail("BAD_VALUE", "max_reference_images",
                            "max_reference_images must be an integer >= 0 or null")
            swap = value.get("swap_allowance_gb")
            if ("swap_allowance_gb" in value and swap is not None
                    and (isinstance(swap, bool) or not isinstance(swap, (int, float))
                         or not math.isfinite(swap) or swap < 0)):
                return fail("BAD_VALUE", "swap_allowance_gb",
                            "swap_allowance_gb must be a finite number >= 0 or null")
        # D2/D3 consumers and D4 runtime authority are not proven in a running Hub yet: refuse
        # loudly. Later activation must run set_reference_override BEFORE the RAM write inside
        # change(): compare_and_write has no rollback, so a later failure would persist RAM.
        return fail("TIER_UNKNOWN", "reference",
                    "reference writes are unavailable until the published reference table and "
                    "its Hub consumers are wired (D2-D4); nothing was written")

    if not (has_free or has_total or reference_present):
        return fail("NOTHING_TO_WRITE", "", "send a RAM pair, a RAM reset, or a reference")
    if entry is None:
        return _fleet_unknown_model(model, node, node_id)

    def change() -> None:
        if ram_reset:
            memory_admission.reset_override(model, entry, mode=mode)
        else:
            memory_admission.set_override(
                model, min_total_memory_gb=float(total),
                min_free_memory_gb=float(free), catalog_entry=entry, mode=mode)

    try:
        state = memory_admission.compare_and_write(
            model, entry, node_id=node_id, expected_revision=revision, mode=mode,
            total_memory_bytes=total_memory_bytes, model_default=None, change=change)
    except memory_admission.RevisionConflict as conflict:
        current = conflict.current
        return JSONResponse({"ok": False, "code": "REVISION_CONFLICT", "node": node,
                             "node_id": node_id, "current_revision": current.get("revision"),
                             "admission": current}, status_code=409)
    except ValueError as exc:
        return fail("BAD_VALUE", "", str(exc))
    return JSONResponse({"ok": True, "node": node, "node_id": node_id, **state})


def _annotate_worker_catalog(monitor: StudioMonitor, worker: dict, content: bytes,
                             content_type: str) -> bytes:
    """Carry the producer's r5 inventory onto this Mac's raw worker catalog rows.

    A controller reads a remote node's peer `/studio/<modality>/api/catalog` through this proxy.
    Without the annotation that row loses the producer's policy and the controller falls back to
    the older ABSENT path. The aggregate catalog already annotates local rows from one node
    identity and one host total; that exact snapshot is reused, so the remote row carries the
    same token the aggregate GET answers with. A row the aggregate has not seen yet is computed
    with the same helper and identity, never with the caller's or controller's values.
    """
    from .backend import memory_admission, monitor as monitor_module
    if str(worker.get("machine") or "local") != "local":
        return content
    if "json" not in (content_type or "").lower():
        return content
    try:
        catalog = json.loads(content)
    except (TypeError, ValueError):
        return content
    rows = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(rows, list) or not memory_admission.applies_to(worker.get("modality")):
        return content
    try:
        aggregate = monitor.cached_aggregate_catalog().get("models") or []
    except Exception:  # noqa: BLE001 - an unavailable cache is the fallback path, not a 500
        aggregate = []
    by_model = {}
    for row in aggregate:
        if isinstance(row, dict) and row.get("hub_studio") == worker.get("id"):
            by_model.setdefault(row.get("repo"), row)
    node_id = monitor_module._local_node_id()
    total_bytes = monitor_module._local_total_memory_bytes()
    health = ((monitor.status.get(worker.get("id")) or {}).get("health") or {})
    memory = health.get("memory") if isinstance(health, dict) else None
    total_gb = memory.get("total_gb") if isinstance(memory, dict) else None
    if not isinstance(total_gb, (int, float)) or isinstance(total_gb, bool):
        from .backend import hardware_profiles
        total_gb = (hardware_profiles.machine_hardware_profile(
            worker.get("machine", "local")) or {}).get("memory_gb")
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = str(row.get("repo") or row.get("model_id") or "")
        if not model:
            continue
        source = by_model.get(row.get("repo")) or by_model.get(row.get("model_id"))
        if source is not None and "hub_memory_admission" in source:
            row["hub_memory_admission"] = source["hub_memory_admission"]
        else:
            annotated = memory_admission.publish_floor(dict(row), total_gb)
            row["hub_memory_admission"] = monitor_module._memory_admission_inventory(
                model, annotated, node_id=node_id, total_memory_bytes=total_bytes)
        changed = True
    return json.dumps(catalog, separators=(",", ":")).encode() if changed else content


def _library_may_be_moved_from(what: str) -> bool:
    """True only when this Hub's data directory owns the model library (components/library_ownership.py).

    A Hub started with a throwaway data directory over a real library used to retire the real
    weights into the throwaway directory's Trash Folder; deleting it destroyed them. A Hub that
    does not own the library logs why and moves nothing.
    """
    from components import library_ownership
    problem = library_ownership.refusal(_model_library(), ledger.DATA_DIR, what)
    if problem:
        print(f"[hub] {problem}", flush=True)
        return False
    return True


def _lan_sources(catalog: dict[str, dict[str, Any]], library: Path,
                 monitor: StudioMonitor) -> dict[str, str]:
    """Package id -> another Mac's address that has it installed, from this Hub's own registry.

    The picker's source order is "the SSD, then another Mac on the LAN, then the internet". This
    answers only the **middle**: which packages some *other* machine in this Hub's group is running
    with a model cached, so the screen can say "from <machine>" instead of silently offering an
    internet download of 9 GB that a Mac two desks away already holds. It is a hint for the
    download: `POST /api/hub/model-packages/{id}/download` still takes its own `source` share and
    verifies every byte, and a package with no entry here simply falls through to the next source.

    A machine's row is read from the same aggregate the fleet rows come from, so a Mac that is
    asleep contributes nothing rather than a stale claim.
    """
    found: dict[str, str] = {}
    try:
        models = monitor.cached_aggregate_catalog().get("models") or []
        local = monitor.status
    except Exception:  # noqa: BLE001 - a source hint must never fail the screen
        return found
    for row in models:
        if not isinstance(row, dict):
            continue
        studio_id = str(row.get("hub_studio") or "")
        machine = str(row.get("hub_machine") or "local")
        if machine == "local" or (local.get(studio_id) or {}).get("status") != "up":
            continue
        if not is_cached(row):
            continue
        package_id = str((row.get("cache") or {}).get("package_id")
                         or row.get("package_id") or "")
        if package_id and package_id not in catalog:
            continue
        if package_id:
            found.setdefault(package_id, machine)
    return found


def _disk_context(monitor: StudioMonitor) -> disk_governor.Context:
    """What the disk governor needs from this Hub: its folders, the catalog, the settings, the machines."""
    from . import model_downloads
    from .backend import job_storage, release_delta
    try:
        library: Path | None = _model_library()
    except ValueError:
        library = None                          # no library to verify against: no model orphan is judged
    if library is not None:
        from components import library_ownership
        if not library_ownership.resolve(library, ledger.DATA_DIR).owned:
            library = None                      # not this Hub's library: the governor never prunes it
    try:
        manifests = [item for item in _model_catalog().values() if not item.get(_MANIFEST_ERROR)]
    except (ValueError, OSError):
        manifests = []
    roots = [Path(root) for worker in monitor.registry if worker.get("local") is True
             for root in worker.get("managed_output_roots", []) if isinstance(root, str) and root]
    return disk_governor.Context(
        data_dir=ledger.DATA_DIR,
        base_dir=ledger.DATA_DIR.parent if ledger.DATA_DIR.name == "hub" else None,
        library=library, manifests=manifests, app_root=release_delta.installed_app_root(),
        machines={worker.get("machine", "local") for worker in monitor.registry if worker.get("local") is True},
        output_roots=roots, settings=standalone_config.load_device_settings()["effective"],
        unregistered_roots=[Path(root) for worker in monitor.registry
                            if worker.get("local") is True and worker.get("modality") == "voice"
                            for root in worker.get("managed_output_roots", [])
                            if isinstance(root, str) and root],
        running_staging=model_downloads.running_staging,
        spool_limit=lambda: job_storage._read()["max_bytes"], spool_enforce=job_storage.enforce_budget)


def retire_installed_models() -> list[str]:
    """At start-up, any installed retired model (`components/retired_models.py`) goes to the Trash Folder."""
    from components import retired_models
    if not _library_may_be_moved_from("retiring models"):
        return []
    return retired_models.retire_installed(_model_library(), output_storage.trash_root())


def _model_state(manifest: dict[str, Any], catalog: dict[str, dict[str, Any]],
                 monitor: StudioMonitor,
                 lane_contracts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    from components import model_packages
    receipt = model_packages.preflight(manifest, _model_library(), catalog=catalog)
    worker_id = _worker_for_engine(manifest["engine"])
    worker = next((item for item in monitor.registry if item["id"] == worker_id), None)
    if lane_contracts is None:
        lane_contracts = _lane_contracts(monitor)
    contract = _contract_for(manifest, lane_contracts)
    if contract is not None:
        # Added here rather than restated by five workers, and derived from the **generator**:
        # a subtitle job is not given a video job's ceiling, because the owner's question was
        # exactly that — how long before a broken short job is noticed. Same function the proxy
        # enforces with, so what a scheduler reads is what this process holds the job to.
        contract = {**contract, "deadline_seconds": deadline_floor_seconds(
            _modality_for_engine(manifest["engine"]))}
    # A paused model still reports verified files and a compatible runtime; it is the Hub's own
    # circuit breaker that refuses it. Without this the app showed every gate open and the job was
    # then declined before dispatch, which read as the model silently not working.
    paused = broker.model_block_state(worker_id, manifest["model_id"])
    return {
        **receipt,
        **standalone_config.model_readiness({
            "files_state": receipt["state"],
            "minimum_macos": manifest["minimum_macos"],
            "runtime_identity": (worker or {}).get("runtime_identity", {"compatible": False}),
            "production_allowed": model_packages.production_allowed(manifest),
        }),
        "model_id": manifest["model_id"],
        "revision": manifest["revision"],
        "engine": manifest["engine"],
        # The modality a job for this package must declare. Read from the same engine table the
        # worker comes from (no second mapping), so a scheduler does not have to make a second,
        # best-effort catalog read and join on model id - which is what silently dropped a model
        # whose engine the reader did not know.
        "modality": _modality_for_engine(manifest["engine"]),
        # The dispatch contract the model's own studio published, verbatim except for the Hub's
        # own `deadline_seconds`, or null when no studio is up with one.
        "lane_contract": contract,
        **({key: contract[key] for key in ("supports_reference_images", "max_reference_images")
            if key in contract} if contract and _modality_for_engine(manifest["engine"]) == "image" else {}),
        # `model` for something a person can pick; `dependency` for a package that only exists to
        # serve another one. Consumers must not have to infer this.
        "type": ("dependency" if manifest["package_id"] in _dependency_package_ids(catalog) else "model"),
        "dependencies": _declared_companions(manifest),
        "worker_id": worker_id,
        "worker_state": "running" if worker else "unavailable",
        "paused": paused is not None,
        "paused_reason": (paused or {}).get("reason"),
        "paused_studio": (paused or {}).get("studio"),
        "paused_failures": (paused or {}).get("failures"),
    }


def artifact_unavailable_reason(item: dict[str, Any], why: str) -> str:
    """Why a finished result cannot be served, as a sentence the operator can act on.

    The owner, on a clip that would not play and on the 502 behind it: *"just give a proper clear
    reason why it can't be played. 502 is no proper reason. it must be understandable from a
    glance. so i know why it didn't play."* He is right, and the status code was the smallest part
    of what was wrong.

    A finished result is not stored in the Hub. It sits on the Mac that generated it — a written
    decision, not a defect (`backend/job_storage.py`: *"Image and voice generation artifacts live on
    the worker Macs"*) — and the Hub serves it by proxying to that Mac. So the fact the operator
    needs is **which Mac**, and the action available is to start or reconnect it. "502" says
    neither, and "worker artifact could not be read" names a component they have never seen.

    The two causes are told apart because their fixes differ: a worker missing from the roster is a
    Mac that is off or disconnected, while a worker still registered that does not answer is one
    that is running and unhealthy. `why` carries the distinction and this turns it into the
    sentence.
    """
    studio = str(item.get("studio") or "").strip()
    machine = str(item.get("machine") or "").strip()
    if not machine and "@" in studio:
        machine = studio.split("@", 1)[1]
    role = studio.split("@", 1)[0] or "worker"
    if machine in ("", "local"):
        return (
            f"This result was made by the local {role} engine and is stored on this Mac rather than "
            f"in the Hub, and that engine is {why}. Start Studio Hub's services and play it again."
        )
    return (
        f"This result was made on {machine} and is stored on that Mac's disk rather than in the "
        f"Hub, so it can only be played while {machine} is running and connected — and it is {why} "
        "right now. Start that Mac, or wake it, and play this again."
    )


def _summary(batch: dict[str, Any]) -> dict[str, Any]:
    return {**broker.batch_summary(batch), "items": [
        broker.public_item(batch, item) for item in batch["items"]
    ]}


#: The fleet-row fields of 0.6.243: both trashes `{count, bytes}`, and where the memory went. Taken from
#: the Mac's own host snapshot; a Hub that predates them sends none, so the row says None, never 0.
_TRASH_AND_MEMORY_KEYS = ("hub_trash", "macos_trash", "wired_gb", "compressed_gb", "swap_used_gb", "top_processes")


def _recent_job_state() -> str | None:
    """This Mac's newest finished job, in `backend.activity`'s own words.

    `needs_attention` only for an error inside the activity module's recent window: an error from
    last night is history, not a reason to flag the Mac now (the rule `activity._state_at` uses).
    """
    from .backend import activity
    now, latest = time.time(), None
    for batch in broker.batches.values():
        for item in batch.get("items") or []:
            if item.get("state") not in ("error", "done", "cancelled"):
                continue
            when = item.get("finished_at") or 0
            if latest is None or when > latest[0]:
                latest = (when, item)
    if latest is None:
        return None
    when, item = latest
    if item.get("state") == "error" and now - when < activity.JUST_FINISHED_S:
        return "needs_attention"
    return "ready"


async def _attention_loop() -> None:
    """Measure this Mac's attention state unprompted, every `attention.settings()["interval_s"]`.

    The point is that a cleared condition clears the flag with nobody touching the Mac, and that
    every row carries the time it was measured. The interval is a setting (default 30 minutes).
    """
    from . import attention
    while True:
        try:
            await run_in_threadpool(attention.refresh, activity_state=_recent_job_state())
        except Exception:                                    # a failed measurement must not kill the loop
            log.exception("attention measurement failed; the last one stays with its own time")
        await _asyncio.sleep(max(60, attention.settings()["interval_s"]))


def _trash_and_memory(host: dict[str, Any]) -> dict[str, Any]:
    # The attention block travels with the row so the fleet page can print when it was measured
    # (owner, 2026-11-17: "signals every 30 minutes so we don't have to keep checking").
    from . import attention
    # `attention_report`, not `attention`: the Gateway's row already uses `attention` for its own
    # sentence, and an object under that key would break every reader (Gateway - Lead - 001, 3ff4686).
    return {**{key: host.get(key) for key in _TRASH_AND_MEMORY_KEYS}, "attention_report": attention.cached()}


def _app_version() -> str:
    try:
        return str(json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())["version"])
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return "unknown"


def _app_build() -> str:
    """This build's exact commit, so two copies of one product version can be told apart."""
    try:
        value = json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())["implementation_commit"]
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return "unknown"
    return str(value)[:7]


def machine_health(states: list[tuple[str, str]]) -> tuple[str, str | None]:
    """One Mac's health from all of its Studios, not from whichever one is listed first.

    `fleet_overview` took the first studio on a machine and reported that studio's status as the
    machine's health, so a Mac running Voice and Image with Image down was reported as "up" if Voice
    happened to come first in the registry - and as "down" while Voice was working fine if it did not.
    A person reading the Fleet page is asking about the Mac.

    Returns the health and, when something is wrong, a sentence naming what.
    """
    up = [name for name, status in states if status == "up"]
    down = [name for name, status in states if status not in ("up", "unknown")]
    if down and not up:
        health = "down"
    elif down:
        health = "degraded"
    elif up:
        health = "up"
    else:
        health = "unknown"
    detail = None
    if down:
        listed = ", ".join(sorted(down))
        detail = f"{listed} is not answering" if len(down) == 1 else f"{listed} are not answering"
    return health, detail


def _retired_refusal(model: object) -> dict[str, Any] | None:
    """Refuse a retired model by name (`components/retired_models.py` is the one list)."""
    from components import retired_models
    if retired_models.is_retired(model):
        return {"code": "MODEL_RETIRED", "detail": f"{model} has been retired and is no longer offered"}
    return None


def _production_refusal(manifest: dict[str, Any],
                        readiness: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Refuse a manifest explicitly held from production, independent of package location."""
    from components import model_packages

    if model_packages.production_allowed(manifest):
        return None
    state = readiness or {
        "production_allowed": False,
        "admission_state": "production_blocked",
        "admission_reason": "production use is disabled pending qualification",
    }
    return {
        "code": "MODEL_UNQUALIFIED",
        "detail": str(state.get("admission_reason") or
                      "model is not qualified for production use"),
        "readiness": state,
    }


def _a_joined_mac_can_run(envelope: dict[str, Any], modality: str | None) -> bool:
    """Whether a joined Mac in this group has the model installed, runtime-ready and within its RAM floor.

    The readiness row is this Mac's own disk, so group 1500 refused Fish S2 Pro 4-bit with
    MODEL_NOT_READY while its node 1504 had it ready. The broker sends work only to a Studio whose
    catalog has the model cached, so accepting it here lands it on that node.
    """
    model = str(envelope.get("model") or "")
    routing, _error = routing_rule.parse(envelope.get("routing"))
    return any(
        not peers.is_local_studio(studio)
        and broker._can_run(studio, entry, model, routing)
        and entry.get("runtime_ready") is not False and entry.get("available") is not False
        for studio, entry in broker._model_rows(str(modality or ""), model))


_SAFE_CLIENT_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{8,160}")


def _audit_submission_failure(envelope: dict[str, Any], exc: BaseException) -> None:
    """One bounded line to correlate a failed submit; never the request or the exception text."""
    client_request_id = envelope.get("clientRequestId")
    items = envelope.get("items")
    modality, model = envelope.get("modality"), envelope.get("model")
    startup_control.audit("batch_submit_failed", {
        "client_request_id": (client_request_id
                              if isinstance(client_request_id, str)
                              and _SAFE_CLIENT_REQUEST_ID.fullmatch(client_request_id) else None),
        "modality": modality[:40] if isinstance(modality, str) else None,
        "model": model[:200] if isinstance(model, str) else None,
        "item_count": len(items) if isinstance(items, list) else None,
        "exception": type(exc).__name__,
    })


def _submission_readiness(envelope: dict[str, Any], monitor: StudioMonitor) -> dict[str, Any] | None:
    """Return an explicit refusal when this packaged model cannot run here.

    The catalog is the trusted boundary for shipped model packages.  Keeping
    this check at submit time prevents a job from entering the durable queue
    only to wait forever for absent files, a mismatched runtime, or an OS gate.
    """
    # Preserve the Broker's established input-validation response for malformed
    # envelopes; readiness applies only once a model was actually requested.
    if not envelope.get("model"):
        return None
    if update_drain.draining() and (broker.drain_holds_group() or not any(
            worker.get("modality") == envelope.get("modality") and not peers.is_local_studio(worker)
            for worker in monitor.registry)):
        # Re-routable: the gateway files MACHINE_BUSY as a location refusal and tries another Mac.
        # Not while a joined node can serve the modality and this Mac's own work still runs: the
        # controller keeps dispatching to its nodes until then (`broker.drain_holds_group`).
        return {"code": "MACHINE_BUSY",
                "detail": "this Mac is finishing its current work to install an update"}
    if (retired := _retired_refusal(envelope.get("model"))):
        return retired
    catalog = _model_catalog()
    manifest = next((row for row in catalog.values()
                     if row.get("model_id") == envelope.get("model")), None)
    if manifest is None:
        return {"code": "MODEL_NOT_PACKAGED", "detail": "model is not in the packaged catalog"}
    # One row, built the same way the catalog route builds it, so an unusable manifest is a
    # named refusal here rather than an exception on the submission path.
    state = _model_row(manifest, catalog, monitor)
    if (refusal := _production_refusal(manifest, state)):
        return refusal
    if state.get("manifest_error"):
        return {"code": "MODEL_NOT_READY",
                "detail": "this model's package manifest is unusable on this Hub",
                "readiness": state}
    expected_modality = _modality_for_engine(str(manifest.get("engine") or ""))
    if expected_modality != envelope.get("modality"):
        return {"code": "MODEL_MODALITY_MISMATCH", "detail": "model is not available for this job modality"}
    if state["dispatch_state"] != "eligible" and not _a_joined_mac_can_run(envelope, expected_modality):
        return {"code": "MODEL_NOT_READY", "detail": "model files, runtime, OS, or chip are not ready",
                "readiness": state}
    # A Studio that is not running cannot be given work. Without this the job was accepted - the
    # gates below only refuse for storage, and the readiness gate above only covers files, runtime,
    # macOS and chip - and then queued for ever, because dispatch needs a live studio and nothing
    # ever said why none came. A worker that dies is never removed from `monitor.registry` (there is
    # no deregistration), so liveness is the monitor's own status for it; "unknown" means it has not
    # been checked yet, which must not refuse a job on a Hub that has just started.
    capable = [worker for worker in monitor.registry
               if worker.get("modality") == expected_modality]
    if not capable:
        return {"code": "STUDIO_NOT_RUNNING",
                "detail": f"no {expected_modality} Studio is running on this Mac",
                "readiness": state}
    statuses = [monitor.status.get(worker.get("id"), {}).get("status") for worker in capable]
    if all(status not in (None, "unknown", "up") for status in statuses):
        return {"code": "STUDIO_NOT_RUNNING",
                "detail": f"the {expected_modality} Studio is not answering; restart the local services",
                "readiness": state}

    settings = standalone_config.load_device_settings()["effective"]
    workers = [worker for worker in capable if worker.get("local") is True]
    blocked = [output_storage.admission(machine=worker.get("machine", "local"), max_bytes=settings["max_bytes"],
               retention_days=settings["retention_days"], enabled=settings["cleanup_enabled"],
               minimum_free_bytes=settings["minimum_free_bytes"],
               managed_output_roots=worker.get("managed_output_roots"))
               for worker in workers]
    blocked = [refusal for refusal in blocked if refusal]
    if workers and len(blocked) == len(workers):
        return blocked[0]
    return None


# ── node-owned admission for fleet work ────────────────────────────────────
# A controller dispatches by posting straight at this Mac's worker through the
# peer proxy. The node does not own that durable batch, so this proxy is the
# only place it can enforce its own operator pause, model protection, output
# policy and physical heavy-work lease decisions - and it cannot queue an
# opaque peer job, so the only way to stop a second submission overlapping on
# the same Mac is to hold a machine lease until the accepted job is observed
# terminal. The controller's cached capability view is never trusted for this.
_PEER_LEASE_TTL_S = 6 * 3600.0
_PEER_LEASE_LOCK = threading.Lock()
_PEER_LEASES: dict[str, tuple[str, float]] = {}   # owner -> (machine, monotonic expiry)
_PEER_JOBS: dict[tuple[str, str], str] = {}       # (worker_id, job_id) -> owner
_PEER_UNCERTAIN_CHAT: dict[str, str] = {}        # owner -> exact worker request id
_PEER_TERMINAL_STATES = frozenset({
    "done", "error", "failed", "cancelled", "canceled", "expired",
})
_PEER_JOB_PATH = re.compile(r"^/api/(?:generate/jobs|generate/transcribe/jobs)/([^/]+)")


def _release_peer_lease(owner: str) -> None:
    with _PEER_LEASE_LOCK:
        row = _PEER_LEASES.pop(owner, None)
        chat_request_id = _PEER_UNCERTAIN_CHAT.pop(owner, None)
        for key, value in list(_PEER_JOBS.items()):
            if value == owner:
                _PEER_JOBS.pop(key, None)
    if row is not None:
        broker.release_external_machine(row[0], owner)
    if chat_request_id is not None:
        from .backend import chat_priority
        chat = chat_priority.requests.get(chat_request_id)
        if chat is not None and chat["state"] == "unknown":
            # The six-hour lease backstop is not proof that the request never ran.
            chat.update(lease_held=False, ready=False,
                        expires=time.monotonic() + chat_priority.MAX_WAIT_SECONDS)


def _open_preferred_hold_on_release(owner: str, worker_id: str | None = None) -> None:
    """A peer job on this Mac was observed terminal: open the hold if it was preferred work.

    Called only from the release paths that carry real terminal evidence — a synchronous
    completion response, a status poll that saw the job terminal, or this Mac's own worker
    confirming the job over. TTL expiry, disconnect releases and refusals never call this: an
    unknown outcome never pretends to be a completion.
    """
    with _PEER_LEASE_LOCK:
        row = _PEER_LEASES.get(owner)
    if row is None:
        return
    if worker_id is None:
        worker_id = owner.split(":", 2)[1] if owner.startswith("peer:") else ""
    modality = next((name for name, worker in vocabulary.WORKER_FOR_MODALITY.items()
                     if worker == worker_id), None)
    if modality is None:
        return
    # Share the broker helper so the optional hold write is best-effort in one place:
    # a failed write must never keep this peer lease from being released. The lease owner is
    # also this attempt's reservation token, so the same atomic write conditionally clears it.
    broker._note_preferred_completion({"machine": str(row[0])}, modality,
                                      reservation_owner=owner)


def _retain_uncertain_chat(owner: str, row: dict) -> None:
    """A lost synchronous response never proves the worker stopped generating."""
    row.update(state="unknown", ready=False, lease_held=True,
               expires=time.monotonic() + _PEER_LEASE_TTL_S)
    with _PEER_LEASE_LOCK:
        _PEER_UNCERTAIN_CHAT[owner] = row["id"]


def _prune_peer_leases() -> None:
    """Drop peer leases whose job was never observed terminal and whose safety
    window elapsed, so a vanished controller cannot reserve a Mac for ever."""
    now = time.monotonic()
    with _PEER_LEASE_LOCK:
        expired = [owner for owner, (_machine, expiry) in _PEER_LEASES.items()
                   if expiry <= now and owner not in _PEER_UNCERTAIN_CHAT]
    for owner in expired:
        _release_peer_lease(owner)


async def _release_finished_peer_leases(monitor: StudioMonitor) -> None:
    """Free every lease whose job this Mac's own worker says is over, whoever stopped polling it.

    A lease used to end only when a controller's poll happened to show the job terminal. A
    controller that gave up on a job - a dispatch poll that rejected a peer's own machine id, a
    Wi-Fi drop past its recovery window - never polled again, so this Mac answered every other
    request with "local is reserved for other work on this Mac" for the six-hour TTL while its
    worker sat idle. The worker is the authority on whether its job still runs: ask it.
    """
    with _PEER_LEASE_LOCK:
        held = list(_PEER_JOBS)
        uncertain_chat = list(_PEER_UNCERTAIN_CHAT.items())
    for owner, request_id in uncertain_chat:
        worker = next((row for row in monitor.registry if row.get("id") == "chat"), None)
        if worker is None:
            continue
        try:
            response = await monitor._client.get(
                worker["base_url"] + "/api/fleet/activity", params={"job_id": request_id},
                headers={"X-Hub-Token": worker["hub_token"]}, timeout=10.0)
            evidence = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, OSError, ValueError):
            continue
        if not isinstance(evidence, dict):
            continue
        active, latest = evidence.get("active"), evidence.get("latest")
        if (isinstance(latest, dict) and latest.get("id") == request_id
                and latest.get("state") in _PEER_TERMINAL_STATES
                and not (isinstance(active, dict) and active.get("id") == request_id)):
            from .backend import chat_priority
            chat = chat_priority.requests.get(request_id)
            if chat is not None:
                chat_priority.finish(chat)
            _open_preferred_hold_on_release(owner)
            _release_peer_lease(owner)
    for worker_id, job_id in held:
        worker = next((row for row in monitor.registry if row["id"] == worker_id), None)
        if worker is None:
            continue
        family = ("generate/transcribe/jobs"
                  if worker_id == vocabulary.WORKER_FOR_MODALITY["transcription"]
                  else "generate/jobs")
        tail = f"/api/{family}/{job_id}"
        try:
            response = await monitor._client.get(
                worker["base_url"] + tail, headers={"X-Hub-Token": worker["hub_token"]},
                timeout=10.0)
        except (httpx.HTTPError, OSError):
            continue  # an unanswered worker proves nothing; the TTL still bounds the lease
        if response.status_code == 404:
            with _PEER_LEASE_LOCK:
                owner = _PEER_JOBS.get((worker_id, job_id))
            if owner is not None:
                _release_peer_lease(owner)
        elif response.status_code < 400:
            _observe_peer_response(worker_id, tail, response)


async def _peer_lease_loop(monitor: StudioMonitor | None = None) -> None:
    """Expire abandoned peer leases even when no further peer request arrives.

    Without this, a controller that vanished mid-job would leave this Mac's own
    scheduler treating the machine as forever busy.
    """
    import asyncio

    try:
        while True:
            await asyncio.sleep(60.0)
            _prune_peer_leases()
            if monitor is not None:
                try:
                    await _release_finished_peer_leases(monitor)
                except Exception:
                    logging.getLogger("studiohub.peer").exception(
                        "could not check held peer leases against their workers")
    except asyncio.CancelledError:
        return


def _reserve_peer_machine(worker_id: str, machine: str) -> str | None:
    owner = f"peer:{worker_id}:{uuid.uuid4().hex}"
    if not broker.acquire_external_machine(machine, owner):
        return None
    with _PEER_LEASE_LOCK:
        _PEER_LEASES[owner] = (machine, time.monotonic() + _PEER_LEASE_TTL_S)
    # Immediately after the lease is recorded and before the caller sends the proxied POST:
    # reserve the preferred model against this Mac's own worker idle-release timer. The lease
    # owner is the reservation token, so only this attempt can clear it at a true terminal.
    modality = next((name for name, worker in vocabulary.WORKER_FOR_MODALITY.items()
                     if worker == worker_id), None)
    if modality is not None:
        local = machine == "local" or os.environ.get("STUDIOHUB_PACKAGED") == "1"
        broker._begin_preferred_reservation({"machine": machine, "local": local},
                                            modality, owner=owner)
    return owner


def _worker_job(payload: object) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return None
    job = payload.get("job")
    if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"]:
        return None
    return job["id"], str(job.get("state") or "")


def _settle_peer_lease(owner: str, worker_id: str, response: Response) -> None:
    """Keep the lease for an accepted asynchronous job; free it otherwise.

    A synchronous route (Subtitle's ``/api/transcribe``) or a refusal is already
    finished when the response arrives, so its lease must not outlive the call.
    """
    if response.status_code >= 400:
        _release_peer_lease(owner)
        return
    payload = None
    if "json" in response.headers.get("content-type", ""):
        try:
            payload = json.loads(response.content)
        except (ValueError, TypeError):
            payload = None
    job = _worker_job(payload)
    if job is None or job[1] in _PEER_TERMINAL_STATES:
        # A synchronous completion the worker answered is terminal evidence, not a drop.
        _open_preferred_hold_on_release(owner, worker_id)
        _release_peer_lease(owner)
        return
    with _PEER_LEASE_LOCK:
        row = _PEER_LEASES.get(owner)
        if row is not None:
            _PEER_LEASES[owner] = (row[0], time.monotonic() + _PEER_LEASE_TTL_S)
        _PEER_JOBS[(worker_id, job[0])] = owner
    # Adopt the worker's own stated lane limit once, if it is larger than the floor the
    # reservation started from. Same lease owner; never a fresh start stamp.
    modality = next((name for name, worker in vocabulary.WORKER_FOR_MODALITY.items()
                     if worker == worker_id), None)
    receipt = payload.get("job") if isinstance(payload, dict) else None
    deadline = receipt.get("deadline_s") if isinstance(receipt, dict) else None
    if row is not None and modality is not None:
        broker._extend_preferred_reservation({"machine": str(row[0])}, modality,
                                             owner, deadline)


def _observe_peer_response(worker_id: str, tail: str, response: Response) -> None:
    """Free a held machine once a proxied response shows its job terminal."""
    match = _PEER_JOB_PATH.match(tail)
    if match is None:
        return
    with _PEER_LEASE_LOCK:
        owner = _PEER_JOBS.get((worker_id, match.group(1)))
    if owner is None:
        return
    terminal = False
    if "json" in response.headers.get("content-type", ""):
        try:
            job = _worker_job(json.loads(response.content))
        except (ValueError, TypeError):
            job = None
        terminal = job is not None and job[0] == match.group(1) and job[1] in _PEER_TERMINAL_STATES
    elif response.status_code < 400 and response.content:
        # A raw artifact is served only for a job that has finished.
        terminal = True
    if terminal:
        _open_preferred_hold_on_release(owner, worker_id)
        _release_peer_lease(owner)


# What a proxied work request does with its machine reservation when the caller goes away and the
# handler is cancelled before the worker answers. Cancellation arrives as `asyncio.CancelledError` -
# a BaseException - so it slipped past both the transport handler and the settlement after it, and
# the reservation then survived until `_peer_lease_loop` expired it (six hours). There is no
# response to ask, so the answer is declared per route here, once. *Whether* a given disconnect
# cancels the handler belongs to the server in front - uvicorn 0.52.4 runs a buffered HTTP handler
# to completion on a bare TCP close and cancels it at shutdown - which is why this cannot be
# inferred at the call site.
PEER_DISCONNECT_RELEASE = "release"
PEER_DISCONNECT_RETAIN = "retain"

_PEER_WORK_PATHS: dict[str, str] = {
    # Synchronous routes: the caller holds the connection until the work is done and the response
    # *is* the result, so a disconnect means nothing continues that the lease would protect.
    "v1/chat/completions": PEER_DISCONNECT_RELEASE,
    "api/transcribe": PEER_DISCONNECT_RELEASE,
}
_PEER_WORK_PREFIXES: dict[str, str] = {
    # Asynchronous submits: the worker acknowledges a job before its result exists, and the POST is
    # not the result. A dropped connection may have left a real job on the machine, so the lease is
    # retained exactly as it is for an accepted job - releasing it would let a second request take
    # the machine while the first still runs.
    "api/generate/": PEER_DISCONNECT_RETAIN,
}
# Paths under a work prefix that create no work: a status, cancellation, artifact or
# acknowledgement POST must not reserve a machine, and must not read as an accepted job. The
# documented intent always said so; the single broad prefix did not, so a cancel or an ack took a
# lease of its own.
_PEER_NON_WORK_PREFIXES = ("api/generate/jobs/",)


# Work that runs beside whatever holds this Mac's lease. An embedding batch is a ~130 MB model and
# well under a second for a whole idea history, so making it wait behind a 40-minute narration would
# turn an interactive check into a timeout. Admission (pause, Lite Mode, readiness) still applies.
# ponytail: no lease and no queue; give it the Chat queue's bounded wait if batches ever contend.
# A picture's analysis is ~0.1 s of depth and a few seconds of Florence-2 on ~1 GB of weights, so
# it runs beside the lease holder too (owner rule 2026-10-11: analyse on the Mac that just made the
# picture, which is usually busy making the next one).
_PEER_UNLEASED_PATHS = frozenset({"embeddings/v1/embeddings", "vision/v1/vision/analyze"})


def _peer_lease_policy(path: str) -> str | None:
    """The disconnect policy for one proxied path, or None when it creates no machine work.

    Status, cancellation, artifact and acknowledgement paths are deliberately excluded: an
    operator pausing a mode must not lock anyone out of work this Mac already accepted.
    """
    cleaned = path.strip("/")
    if cleaned.startswith(_PEER_NON_WORK_PREFIXES):
        return None
    for prefix, policy in _PEER_WORK_PREFIXES.items():
        if cleaned.startswith(prefix):
            return policy
    return _PEER_WORK_PATHS.get(cleaned)


def _peer_work_request(method: str, path: str) -> bool:
    """Whether this proxied request asks the node to create new work."""
    return method == "POST" and _peer_lease_policy(path) is not None


# Which proxied work leaves a job in *this* node's history. This is the one place the choice is
# made, for both surfaces that reach `_proxy_to_worker`.
#
# A controller's Image, Music, Voice and Video work reaches this node through this proxy, not
# through `POST /api/hub/jobs`: in agent mode that route refuses a customer's submission, so the
# batch is created on the controller. The controller then polls the worker's own
# `GET /api/generate/jobs/<id>` through the same proxy, and that terminal answer is the only
# point where this node learns the job finished. Chat is synchronous and owns its own recorder.
# Whose history a job is in follows the Mac that ran it, not the credential that asked - without
# this, a node ran work and kept the file but its own Jobs page could not show either.
_PEER_JOB_RECORDERS = {
    "chat/v1/chat/completions": chat_jobs.record_page_completion,
}

_PEER_JOB_STATUS_PATH = re.compile(r"api/generate/jobs/[^/]+")


def _peer_job_recorder(modality: str, path: str):
    """The recorder for one proxied work path, or ``None`` when this node keeps no job for it."""
    cleaned = path.strip("/")
    recorder = _PEER_JOB_RECORDERS.get(f"{modality}/{cleaned}")
    if recorder is not None:
        return recorder
    if modality in broker.MODALITY and _PEER_JOB_STATUS_PATH.fullmatch(cleaned):
        return lambda **facts: _file_peer_finished_job(modality, **facts)
    return None


def _file_peer_finished_job(modality: str, *, content: bytes = b"", worker_id: str = "",
                            status_code: int = 0, **_: object) -> None:
    """File a controller's finished async job in this Mac's own history.

    Only a terminal ``done`` report is written. A queued or running row would be loaded by
    ``ledger.load_unfinished_batches()`` after a restart and re-run by this node's broker - as a
    batch it does not own. The finished row is the asset the operator opens, and the worker keeps
    the file, so the artifact route serves it from this Mac.
    """
    if status_code >= 400 or not content:
        return
    try:
        job = json.loads(content).get("job")
    except (ValueError, AttributeError):
        return
    if not isinstance(job, dict) or job.get("state") != "done" or not job.get("id"):
        return
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    job_id = str(job["id"])
    now = time.time()
    created_at = job.get("created_at") if isinstance(job.get("created_at"), (int, float)) else now
    runtime = job.get("duration_seconds")
    suffix = broker.MODALITY[modality][2]
    item = {
        "index": 0, "state": "done", "progress": 1.0,
        "prompt": str(params.get("prompt") or params.get("text") or params.get("label") or ""),
        "studio": worker_id, "studio_job_id": job_id, "machine": "local",
        "artifact_path": job.get("output_path"),
        "worker_artifact_url": str(job.get("output_url") or f"/api/generate/jobs/{job_id}/{suffix}"),
        "sha256": job.get("sha256"), "bytes": job.get("bytes"),
        "media_type": job.get("media_type"),
        "created_at": created_at, "run_started": job.get("started_at"),
        "finished_at": job.get("finished_at") or now, "runtime_s": runtime,
        "duration_s": job.get("audio_duration_s", runtime), "error": None,
    }
    ledger.save_batch({
        "id": f"peer-{worker_id}-{job_id}",
        "modality": modality,
        "model": str(params.get("repo") or job.get("model") or ""),
        "created_at": created_at, "finished_at": item["finished_at"],
        "cancelled": False, "routing": "pool", "items": [item],
    })


async def _request_model(request: Request, body: bytes, content_type: str) -> str:
    """The model a work request names, or "" when it names none.

    Generation submits carry it as ``repo`` in a JSON body; Subtitle carries it
    as a multipart form field. A missing model only skips the model-specific
    gates - the worker still validates the request it is sent.
    """
    if not body:
        return ""
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if kind == "application/json":
        try:
            payload = json.loads(body)
        except ValueError:
            return ""
        if isinstance(payload, dict):
            return str(payload.get("repo") or payload.get("model") or "").strip()
        return ""
    if kind in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        try:
            form = await request.form()
        except Exception:
            return ""
        return str(form.get("model") or form.get("repo") or "").strip()
    return ""


def _request_models(body: bytes, content_type: str) -> list[str]:
    """The models a JSON work request names as a list (`{"model": [...]}`), else []."""
    if (content_type or "").split(";", 1)[0].strip().lower() != "application/json":
        return []
    try:
        named = json.loads(body).get("model")
    except (ValueError, AttributeError):
        return []
    return [str(m).strip() for m in named if str(m).strip()] if isinstance(named, list) else []


_PEER_REFERENCE_EDIT_PATH = "api/generate/edit"


def _peer_reference_decision(worker: dict[str, Any], model: str, reference_count: int,
                             supplied: object) -> tuple[dict[str, Any] | None, dict | None]:
    """This Mac's own reference policy for one peer edit submit: (refusal, exact policy).

    A controller's ``reference_policy`` is not trusted: only this node's current producer
    snapshot decides whether references are allowed and how many. ``supplied`` is that caller
    field (or the broker's absent sentinel). An older producer with no annotation and no supplied
    field keeps its existing path and returns no policy. A supplied field cannot be verified
    against a node that publishes no policy, so it is refused rather than believed. An unreadable
    catalog is unknown, never an absent annotation. A valid submit returns the one exact
    ACK-shaped policy this node forwards, never the caller's bytes.
    """
    try:
        snapshot = broker._producer_reference_policy(worker, None, model)
    except Exception as exc:  # noqa: BLE001 - an unprovable policy is unknown, not absent
        return {"code": "REFERENCE_POLICY_UNKNOWN",
                "detail": f"this node's producer reference policy is unknown: {exc}"}, None
    if snapshot is broker._REFERENCE_POLICY_ABSENT:
        if supplied is broker._REFERENCE_POLICY_ABSENT:
            return None, None
        return {"code": "REFERENCE_POLICY_UNKNOWN",
                "detail": "this node publishes no reference policy to verify the supplied one"}, None
    try:
        policy = broker._validated_reference_policy(
            snapshot, worker, model, None, "edit", reference_count)
    except ValueError as exc:
        return {"code": "REFERENCE_POLICY_REFUSED", "detail": str(exc)}, None
    if supplied is not broker._REFERENCE_POLICY_ABSENT and supplied != policy:
        return {"code": "REFERENCE_POLICY_MISMATCH",
                "detail": "the supplied reference policy is not this node's current producer "
                          "snapshot"}, None
    return None, policy


def _peer_reference_refusal(worker: dict[str, Any], model: str, reference_count: int,
                            supplied: object) -> dict[str, Any] | None:
    """The refusal half of :func:`_peer_reference_decision`, for refusal-only callers."""
    return _peer_reference_decision(worker, model, reference_count, supplied)[0]


def _multipart_field(body: bytes, content_type: str, name: str, value: str) -> bytes | None:
    """Insert or replace one text form field in a raw request body, touching nothing else.

    The door must hand the worker this node's exact producer policy without re-encoding the
    caller's uploads, so every file byte and the reference count survive unchanged.
    """
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    encoded = value.encode()
    if kind == "application/x-www-form-urlencoded":
        key = quote_plus(name).encode()
        pairs = [pair for pair in body.split(b"&") if pair and pair.split(b"=", 1)[0] != key]
        pairs.append(key + b"=" + quote_plus(value).encode())
        return b"&".join(pairs)
    if kind != "multipart/form-data":
        return None
    boundary = ""
    for parameter in content_type.split(";")[1:]:
        key, _, val = parameter.strip().partition("=")
        if key.lower() == "boundary":
            boundary = val.strip().strip('"')
    if not boundary:
        return None
    delimiter = b"--" + boundary.encode()
    starts = []
    at = body.find(delimiter)
    while at != -1:
        starts.append(at)
        at = body.find(delimiter, at + len(delimiter))
    if not starts:
        return None
    name_bytes = name.encode()
    pieces: list[bytes] = []
    cursor = 0
    replaced = False
    for index, start in enumerate(starts[:-1]):
        headers_end = body.find(b"\r\n\r\n", start + len(delimiter), starts[index + 1])
        if headers_end == -1:
            continue
        headers = body[start + len(delimiter):headers_end]
        match = re.search(rb'name\s*=\s*"?([^";\r\n]+)"?', headers)
        if match is None or match.group(1) != name_bytes:
            continue
        # The worker's form parser reads a repeated field as its last part, so every matching
        # part is replaced: no caller value can outrank this node's policy.
        content_start = headers_end + 4
        next_delimiter = starts[index + 1]
        content_end = (next_delimiter - 2
                       if body[next_delimiter - 2:next_delimiter] == b"\r\n"
                       else next_delimiter)
        pieces.append(body[cursor:content_start])
        pieces.append(encoded)
        cursor = content_end
        replaced = True
    if replaced:
        pieces.append(body[cursor:])
        return b"".join(pieces)
    first = starts[0] + len(delimiter)
    part = (b'Content-Disposition: form-data; name="' + name_bytes + b'"\r\n\r\n'
            + encoded + b"\r\n" + delimiter)
    if body[first:first + 2] == b"\r\n":
        insert_at = first + 2
        part += b"\r\n"  # the next original part's headers need their own CRLF
    else:  # an empty form is just its closing delimiter
        insert_at = first
        part = b"\r\n" + part
    return body[:insert_at] + part + body[insert_at:]


async def _peer_reference_door(worker: dict[str, Any], request: Request, path: str,
                               content_type: str) -> tuple[dict[str, Any] | None, bytes | None]:
    """The reference check for a peer Image edit submit, from its existing multipart body.

    Returns the refusal and, when this node has a producer policy to forward, the body that
    carries it. ``None`` keeps the caller's body byte for byte, which is the older-producer path.
    """
    if (request.method != "POST" or str(worker.get("modality") or "") != "image"
            or path.strip("/") != _PEER_REFERENCE_EDIT_PATH):
        return None, None
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if kind not in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        return None, None
    try:
        body = await request.body()
        form = await request.form()
    except Exception:  # noqa: BLE001 - the worker refuses the malformed body it cannot parse
        return None, None
    try:
        model = str(form.get("model") or form.get("repo") or "").strip()
        count = len(form.getlist("images"))
        supplied: object = broker._REFERENCE_POLICY_ABSENT
        if "reference_policy" in form:
            raw = form.get("reference_policy")
            try:
                supplied = json.loads(raw) if isinstance(raw, str) else None
            except ValueError:
                supplied = None
            if not isinstance(supplied, dict):
                return {"code": "REFERENCE_POLICY_REFUSED",
                        "detail": "reference_policy must be a JSON object"}, None
    finally:
        await form.close()  # spooled uploads would otherwise leak their temp file
    refusal, policy = _peer_reference_decision(worker, model, count, supplied)
    if refusal is not None or policy is None:
        return refusal, None
    forwarded = _multipart_field(body, content_type, "reference_policy",
                                 json.dumps(policy))
    if forwarded is None:
        return {"code": "REFERENCE_POLICY_UNKNOWN",
                "detail": "this node could not attach its producer reference policy to the "
                          "forwarded body"}, None
    return None, forwarded


def _peer_admission_refusal(worker: dict[str, Any], monitor: StudioMonitor,
                            model: str, *, ignore_busy: bool = False) -> dict[str, Any] | None:
    """This Mac's own answer, applied before its worker sees any peer request."""
    from .backend import registry

    machine = str(worker.get("machine") or "local")
    studio_id = str(worker.get("id") or "")
    if not registry.machine_enabled(machine):
        return {"code": "MACHINE_DISABLED", "detail": f"{machine} is disabled on this Mac"}
    if not registry.studio_enabled(machine, studio_id):
        return {"code": "STUDIO_DISABLED", "detail": f"{studio_id} is disabled on this Mac"}
    # A Mac in Lite Mode: someone at the keyboard, quiet hours, or a model it does not run. Busy, not
    # broken - the controller tries another Mac (`broker._WORKSTATION_REFUSALS`). This Mac's own
    # queues apply the same rule through the scheduler (`peers.dispatch_ready`, `lite_mode_allows`).
    if (refusal := workstation.admission_refusal(model)) is not None:
        return refusal
    if model:
        paused = broker.model_block_state(studio_id, model)
        if paused is not None:
            return {"code": "MODEL_PAUSED",
                    "detail": str(paused.get("reason") or "model is paused on this Mac"),
                    "readiness": paused}
    if not ignore_busy and machine in broker.busy_machines():
        broker.note_developer_test_production(machine)
        return {"code": "MACHINE_BUSY",
                "detail": f"{machine} is reserved for other work on this Mac"}
    settings = standalone_config.load_device_settings()["effective"]
    blocked = output_storage.admission(
        machine=machine, max_bytes=settings["max_bytes"],
        retention_days=settings["retention_days"], enabled=settings["cleanup_enabled"],
        minimum_free_bytes=settings["minimum_free_bytes"],
        managed_output_roots=worker.get("managed_output_roots"))
    if blocked:
        return blocked
    if (retired := _retired_refusal(model)):
        return retired
    if model:
        try:
            manifest = next((row for row in _model_catalog().values()
                             if row.get("model_id") == model), None)
            if manifest is not None and (refusal := _production_refusal(manifest)):
                return refusal
        except (ValueError, OSError, KeyError, TypeError):
            pass
    # The packaged model gate only applies where this Hub actually owns a model
    # library to verify against; without one the worker remains the authority on
    # whether its own model can run.
    if model and os.environ.get("STUDIOHUB_MODELS_DIR", "").strip():
        try:
            return _submission_readiness(
                {"model": model, "modality": worker.get("modality")}, monitor)
        except (ValueError, OSError, KeyError, TypeError):
            return {"code": "MODEL_CATALOG_UNAVAILABLE",
                    "detail": "model readiness could not be verified on this Mac"}
    return None


def _peer_refusal_response(refusal: dict[str, Any]) -> JSONResponse:
    code = str(refusal.get("code") or "NODE_REFUSED")
    status = 409
    if code == "OUTPUT_STORAGE_BLOCKED":
        status = 507
    elif code in {"MODEL_NOT_READY", "STUDIO_NOT_RUNNING", "MODEL_CATALOG_UNAVAILABLE"}:
        status = 503
    elif code in {"CHAT_STALLED", "CHAT_DEADLINE_REACHED"}:
        # A job that was accepted, ran and was then stopped by its own limit or by its own
        # silence. Not a refusal (409 says the work never started) and not a 503 (the studio is
        # up): 504 is the one that says the attempt ended with no answer.
        status = 504
    body: dict[str, Any] = {
        "detail": str(refusal.get("detail") or "this Mac refused the work"),
        "code": code,
    }
    if isinstance(refusal.get("readiness"), dict):
        body["readiness"] = refusal["readiness"]
    return JSONResponse(body, status_code=status)


def _bind_facts() -> dict[str, Any]:
    """What this Hub was launched to listen on, in the words a caller can act on.

    One definition, read by both `/api/health` and `/api/hub/settings`, because the two answers
    drifting is the defect this exists to remove.

    The shell decides the list — `--listen-auto` recomputes it from this Mac's own private and
    Tailscale addresses on every launch — and `hub_worker.py` passes the answer it was given
    through the environment rather than re-deriving it here. So this reports what is bound, not
    what someone hoped: a Mac that came up loopback-only says so, which is the fact a gateway
    needs to report instead of discovering it as a timeout.

    Classification is by the same rules `LocalAddresses` uses on the shell side (RFC1918,
    link-local, and Tailscale's carrier-grade NAT range), so "tailnet" means the same thing in
    both places. A name is never guessed: an address that matches none of them is `other`.
    """
    import ipaddress

    raw = (os.environ.get("STUDIOHUB_BOUND_HOST") or "").strip()
    hosts = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not hosts:
        return {"known": False,
                "reason": "this Hub was started without the launcher that records its bind"}

    def classify(host: str) -> str:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "other"
        if address.is_loopback:
            return "loopback"
        if address.version == 4:
            first, second = int(host.split(".")[0]), int(host.split(".")[1])
            if first == 100 and 64 <= second <= 127:
                return "tailnet"
            if first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168):
                return "lan"
        return "other"

    classified = {host: classify(host) for host in hosts}
    classes = sorted(set(classified.values()))
    return {
        "known": True,
        "hosts": hosts,
        "classes": classified,
        "port": int(os.environ.get("STUDIOHUB_BOUND_PORT") or 0) or None,
        # The two questions a caller actually asks, answered rather than left to be worked out.
        "loopback_only": classes == ["loopback"],
        "reachable_off_this_mac": any(kind in {"lan", "tailnet", "other"} for kind in classes),
        "tailnet": [host for host, kind in classified.items() if kind == "tailnet"],
        "wildcard": any(host in {"0.0.0.0", "::"} for host in hosts),
    }


async def _register_with_controller(controller_url: str, token: str, self_address: Any,
                                    fleet: Any, monitor: Any) -> dict[str, Any] | None:
    """Introduce this Mac to a controller it has just been moved to.

    A3 step 3. Returns the controller's answer (which carries this Mac's own credential) or `None`
    when the registration could not be attempted. **Never raises**: the membership has already
    changed by the time this runs, and a node that could not announce itself is still a node — it
    will announce itself on its next re-join. Raising here would report a failed handover for a Mac
    that had in fact followed.

    `self_address` comes from the controller that handed over, because that is the party which has
    been probing this Mac and therefore knows where it answers. Without it this Mac has no honest
    way to name itself: which interface it is reachable on is the *app's* decision at join time,
    not the Hub's.

    **Why the Hub does this and not only the app.** A handover that changed every node's token but
    left the new controller's roster empty collapsed the site's capacity to one Mac — measured in
    the §9 rehearsal as `verified: 1` with the new head reporting `node_summary.total: 0`. The
    nodes had followed the credential and nobody had told their new head who they were.
    """
    logger = logging.getLogger("studiohub.fleet")
    address = str(self_address or "").strip()
    if not address:
        logger.warning(
            "moved to %s but no address came with the handover, so this Mac cannot introduce "
            "itself; it will register on its next re-join", controller_url)
        return None
    from .backend import control_plane as _control_plane
    # `public_settings`, not `load_settings`: only the public view carries `machine_name`, so this
    # introduced every handed-over Mac under its id instead of its name.
    settings = _control_plane.public_settings()
    studios = [{"id": str(row.get("id")), "modality": str(row.get("modality"))}
               for row in (getattr(monitor, "registry", None) or [])
               if row.get("id") and row.get("modality") and not row.get("fleet_node")]
    body = {
        "node_id": str(settings.get("controller_id") or ""),
        "name": str(settings.get("machine_name") or settings.get("controller_id") or ""),
        "address": address,
        "studios": studios,
    }
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(f"{controller_url}/api/hub/fleet/nodes",
                                         json=body, timeout=15.0,
                                         headers={"X-Hub-Token": token})
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("could not introduce this Mac to %s: %s", controller_url, exc)
        return None
    if response.status_code >= 400:
        logger.warning("introducing this Mac to %s was refused (HTTP %s): %s",
                       controller_url, response.status_code, response.text[:200])
        return None
    try:
        answer = response.json()
    except ValueError:
        return {"ok": True}
    return answer if isinstance(answer, dict) else {"ok": True}


def create_app(*, start_background: bool = True) -> FastAPI:
    """Create one owner-authenticated Hub bound to packaged worker descriptors."""
    monitor = StudioMonitor()
    broker.configure_monitor(monitor)
    broker.configure_admission_guard(lambda batch: _submission_readiness(batch, monitor))
    cloud_guard.configure_monitor(monitor)
    token = auth.load_token()
    # A Hub owns its fleet credential from the moment it runs, so an operator can read it
    # without having to provoke a request first. It is a separate scope from the Hub token on
    # purpose: this is what a controller is given.
    from .backend import peers as _peers
    _peers.fleet_token()
    # And its node identity, for the same reason: derived from the hostname until the first save,
    # so a rename in macOS before then would have orphaned the roster row.
    control_plane.ensure_controller_id()
    data_root = standalone_config.data_dir()
    # The /api/hub/resources route below binds `resources` locally in this function.
    from .backend import resources as resource_stats
    # data_dir() is already resolved (that is what makes /var and /tmp paths work on macOS),
    # so the strict reader's no-symlink guarantee starts at this resolved data root.
    installed_authority = InstalledRenderAuthorityLoader(
        Path(__file__).resolve().parents[2],
        hardware_identity=resource_stats.hardware_identity,
        data_root=data_root,
    )
    render_assets_store = RenderAssetStore(
        data_root / "render-assets", max_chunk_bytes=8 * 1024 * 1024,
        max_asset_bytes=standalone_config.DEFAULT_MAX_BYTES,
        reserve_bytes=standalone_config.DEFAULT_MINIMUM_FREE_BYTES,
        free_bytes=lambda path: shutil.disk_usage(path).free,
    )
    render_runtime = RenderJobRuntime(
        data_root / "final-renders", database_path=ledger.DB_FILE,
        qualification_resolver=installed_authority.resolve,
        probed_qualification_resolver=installed_authority.resolve_probed,
        free_bytes=lambda path: shutil.disk_usage(path).free,
    )
    final_render_executor = RenderExecutor(
        render_runtime, render_assets_store, installed_authority,
        machine="local", broker_api=broker,
    )
    installed_authority.physical_node_id = lambda: control_plane.load_settings()["controller_id"]
    encode_probe_control = EncodeProbeControl(
        data_root, loader=installed_authority, executor=final_render_executor,
        node_id=installed_authority.physical_node_id,
        hardware_identity=resource_stats.hardware_identity,
    )
    installed_authority.encode_probe_control = encode_probe_control
    # The scoped v3 control shares the probe record/evidence directories (probe keys mix in
    # its v3 identity, so the two scopes cannot see each other's proofs) and the one physical
    # hold recovery above already restores every record under its own recorded owner.
    late_label_probe_control = EncodeProbeControl(
        data_root, loader=LateLabelProbeScope(installed_authority),
        executor=final_render_executor,
        node_id=installed_authority.physical_node_id,
        hardware_identity=resource_stats.hardware_identity,
    )
    installed_authority.late_label_encode_probe_control = late_label_probe_control
    # The v4 subtitle scope uses one more control over the same shared record/evidence
    # directories; its v4 digests isolate its proofs from v1 and v3 by construction.
    subtitle_probe_control = EncodeProbeControl(
        data_root, loader=SubtitleProbeScope(installed_authority),
        executor=final_render_executor,
        node_id=installed_authority.physical_node_id,
        hardware_identity=resource_stats.hardware_identity,
    )
    installed_authority.subtitle_encode_probe_control = subtitle_probe_control
    final_render_service = render_service.RenderService(
        render_runtime, render_assets_store, final_render_executor, installed_authority,
        trash_root=output_storage.trash_root)
    broker.configure_render_executor(final_render_executor)
    render_executor_module.configure(final_render_executor)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio as _asyncio

        ledger.prepare_database()
        output_storage.prepare()
        output_storage.recover_process_leases()
        # Durable render ownership is restored before any queue or dispatcher can grant this Mac.
        final_render_executor.recover_and_restore_physical_holds()
        await encode_probe_control.recover_and_restore_physical_holds()
        broker.restore_batches()
        from . import developer_test_service
        developer_test_service.configure()
        await _asyncio.to_thread(developer_test_service.recover)
        # App gates restore their exact physical holds and reconcile owned processes before any
        # dispatcher or source upload can start. Production stays unsupported until packaged.
        from . import developer_gate_service
        developer_gate_service.configure()
        await _asyncio.to_thread(developer_gate_service.recover)
        transcription_jobs.restore_batches()
        # A pause this build can never clear is not a safety feature, it is a permanent refusal.
        try:
            manifests = _model_catalog().values()
            known = {str(manifest.get("model_id") or "") for manifest in manifests}
            known |= {alias for manifest in manifests for alias in (manifest.get("aliases") or [])}
            forgotten = broker.prune_model_protection({name for name in known if name})
            if forgotten:
                print(f"[hub] forgot pauses for models this build does not ship: "
                      f"{', '.join(forgotten)}", flush=True)
        except ValueError:
            pass
        storage_sweep = None
        peer_lease_sweep = None
        workstation_watch = None
        release_mirror = None
        if start_background:
            # The output size / age limits (device settings), enforced for every studio - not only in
            # development. See job_storage.budget_loop.
            from .backend import job_storage
            storage_sweep = _asyncio.create_task(
                job_storage.budget_loop(standalone_config.cleanup_interval_seconds()))
            peer_lease_sweep = _asyncio.create_task(_peer_lease_loop(monitor))
            attention_sweep = _asyncio.create_task(_attention_loop())

            def held_jobs() -> list[tuple[str, str]]:
                """Every job this Mac is running: a controller's (peer surface) and its own queue's."""
                with _PEER_LEASE_LOCK:
                    peer = list(_PEER_JOBS)
                return peer + broker.local_running_jobs()
            workstation_watch = _asyncio.create_task(workstation.watch(monitor, held_jobs))
            # A node fetches the release its controller holds; a no-op on any Mac that is not one.
            release_mirror = _asyncio.create_task(release_sync.loop())
            try:
                retired = retire_installed_models()
                if retired:
                    print(f"[hub] moved retired models to the Trash Folder: {', '.join(retired)}", flush=True)
            except (OSError, ValueError):
                pass
            # One unified Trash Folder: adopt what earlier installs left in their own Trash roots.
            # Idempotent, same-volume only, and never a live app or model (output_storage.adopt_legacy).
            try:
                adopted = output_storage.adopt_legacy()
                if adopted["moved_count"]:
                    print(f"[hub] adopted {adopted['moved_count']} retired entries from old Trash roots "
                          f"into {adopted['manifest']}", flush=True)
                for problem in adopted["failed"]:
                    print(f"[hub] Trash Folder adoption left {problem['source']}: {problem['error']}", flush=True)
            except OSError:
                pass

            def after_cleanup(first: bool) -> None:
                """The disk governor takes its turn, then render retirement, on this timer."""
                try:
                    disk_governor.sweep(_disk_context(monitor), startup=first)
                finally:
                    final_render_service.sweep(force=True)

            output_storage.start_background(
                lambda: {worker.get("machine", "local") for worker in monitor.registry},
                standalone_config.load_device_settings,
                # Render retirement rides the same pass: no second cleanup service.
                after_sweep=after_cleanup,
                # So does the 30-day handoff of models removed from the library (owner decision
                # 2026-10-28). `handoff()` resolves the library itself, so a Hub without one says
                # so and does nothing.
                removed_models=True,
            )
            monitor.start()
            # Mint the fleet credential at startup, rather than lazily on the first request that
            # presents a foreign token. Connections offers this value to a Mac that joins, and a
            # lazily minted one does not exist yet when the app reads the file - so the operator
            # saw "Unavailable" on the one screen that has to hand it out.
            from .backend import peers as peer_credentials
            peer_credentials.fleet_token()
            broker.start_dispatcher()
            transcription_jobs.start_dispatcher(monitor)
            # A shared voice has to survive a node being off, so the reconciler is what retries it.
            # The module was complete and nothing started it, which is the same shape of miss as a
            # route with no caller: the machinery existed and no voice could ever move.
            shared_voices.start_reconciler(monitor)
            from .backend import khu_agent
            khu_agent_task = _asyncio.create_task(khu_agent.loop())
            await control_plane.runtime.start(monitor, _app_version())
        try:
            yield
        finally:
            final_render_executor.begin_shutdown()
            await _asyncio.gather(
                encode_probe_control.stop(), late_label_probe_control.stop(),
                subtitle_probe_control.stop())
            if start_background:
                if storage_sweep is not None:
                    attention_sweep.cancel()
                    storage_sweep.cancel()
                    try:
                        await storage_sweep
                    except Exception:
                        pass
                if workstation_watch is not None:
                    workstation_watch.cancel()
                    try:
                        await workstation_watch
                    except BaseException:
                        pass
                if peer_lease_sweep is not None:
                    peer_lease_sweep.cancel()
                    try:
                        await peer_lease_sweep
                    except Exception:
                        pass
                if release_mirror is not None:
                    release_mirror.cancel()
                    try:
                        await release_mirror
                    except BaseException:
                        pass
                if 'khu_agent_task' in locals() and khu_agent_task is not None:
                    khu_agent_task.cancel()
                    try:
                        await khu_agent_task
                    except BaseException:
                        pass
                await shared_voices.stop()
                await final_render_executor.stop()
                await broker.stop_dispatcher()
                await transcription_jobs.stop()
                await control_plane.runtime.stop()
                await monitor.stop()
            await _asyncio.to_thread(developer_test_service.shutdown)
            await _asyncio.to_thread(developer_gate_service.shutdown)
            if start_background:
                from . import model_downloads
                model_downloads.shutdown()
            render_assets_store.close()
            broker.configure_render_executor(None)
            render_executor_module.configure(None)

    app = FastAPI(title="Studio Hub", version=_app_version(), lifespan=lifespan)
    app.middleware("http")(auth.make_middleware(token))
    render_service.register_routes(
        app, final_render_service, hub_token=token, fleet_token=peers.fleet_token,
        settings=control_plane.load_settings,
    )

    from .backend import render_output_settings
    render_output_settings.register_routes(
        app, hub_token=token, fleet_token=peers.fleet_token,
        settings=control_plane.load_settings,
    )

    async def _relay_final_render(node_id, operation, **kwargs):
        from .backend import fleet_nodes as fleet
        try:
            return await fleet.relay_final_render(node_id, operation, **kwargs)
        except fleet.StartupRelayError as error:
            raise HTTPException(error.status, error.body) from error

    render_service.register_relay_routes(
        app, _relay_final_render, hub_token=token, fleet_token=peers.fleet_token,
        settings=control_plane.load_settings,
    )
    # **JSON goes over the WAN compressed.** A Gateway's read of one controller is ~3.6 MB of
    # JSON (`/api/hub/models` ~2 MB, `capabilities` ~1 MB, `jobs` ~0.4 MB) over a Tailscale link
    # measured at 0.5-1.2 MB/s, so it took 4-20 s while `/health` answered in milliseconds. That
    # JSON compresses 9-26x. Binary bodies (`application/octet-stream`: release images and blobs,
    # which nodes read with `iter_raw` and resume with Range) stay identity, as do media types
    # Starlette already excludes.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5,
                       exclude_content_types=(*DEFAULT_EXCLUDED_CONTENT_TYPES,
                                              "application/octet-stream"))
    app.state.monitor = monitor

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        # The bind is here, on the route that answers "is this Mac there?", rather than only on
        # `/api/hub/settings`: that route is owner-only and about output storage, so a controller
        # could not read it, and a controller is exactly who needs to know that a node came up
        # loopback-only instead of discovering it as a timeout.
        # A 200 has to say which process answered: launchctl can report a service stopped while
        # another copy of the Hub still owns the port. Reuse the broker's own per-batch counts.
        summaries = [broker.batch_summary(batch)
                     for batch in broker.activity_batches().values()]
        return {"ok": True, "workers": len(monitor.registry),
                "role": control_plane.load_settings()["role"], "bind": _bind_facts(),
                "update_draining": update_drain.draining(), "disk": disk_governor.snapshot(),
                # Only a hold taken with the installer's 0600 token says a later quit is the
                # updater's; an owner-token hold or an aborted install never claims authorship.
                "update_hold_active": time.monotonic() < _installer_hold_until,
                "pid": os.getpid(), "version": _app_version(), "build": _app_build(),
                "started_at": _SERVER_STARTED_AT, "data_dir": str(data_root),
                "queue": {key: sum(summary[key] for summary in summaries)
                          for key in ("queued", "running", "uncertain")}}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": _app_version(), "build": _app_build(),
                "source": "studiohub-mac@06e893e1643bffbcbd162fae122e5a4d0f29e283"}

    @app.get("/api/hub/settings")
    def settings() -> dict[str, Any]:
        # The same `_bind_facts()` the health route publishes, so the two cannot disagree.
        return {**standalone_config.load_device_settings(), "bind": _bind_facts(),
                "warnings": standalone_config.settings_warnings()}

    @app.get("/api/hub/khu/status")
    def khu_status() -> dict[str, Any]:
        from .backend import khu_agent
        return {"ok": True, **khu_agent.STATUS}

    @app.put("/api/hub/settings")
    def save_settings(body: dict[str, Any]) -> dict[str, dict[str, int | bool]]:
        try:
            return standalone_config.update_device_settings(**body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/settings/preview")
    def preview_settings(body: dict[str, Any]) -> dict[str, Any]:
        try:
            value = standalone_config.preview_device_settings(**body)
            roots_by_machine: dict[str, set[str]] = {}
            for worker in monitor.registry:
                if worker.get("local") is not True:
                    continue
                machine = str(worker.get("machine") or "local")
                roots = roots_by_machine.setdefault(machine, set())
                roots.update(root for root in worker.get("managed_output_roots", [])
                             if isinstance(root, str) and root)
            return {"effective": value, "machines": [output_storage.preview(
                machine=machine, max_bytes=value["max_bytes"], retention_days=value["retention_days"],
                enabled=value["cleanup_enabled"],
                minimum_free_bytes=value["minimum_free_bytes"],
                managed_output_roots=sorted(roots))
                for machine, roots in roots_by_machine.items()],
                "trash": {"bytes": output_storage.trash_bytes(max_age=10)}}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/settings/restore-defaults")
    def restore_settings() -> dict[str, dict[str, int]]:
        return standalone_config.restore_device_defaults()

    def _startup_snapshot() -> dict[str, Any]:
        return startup_control.snapshot(startup_control.studio_states(monitor),
                                        app_version=_app_version())

    @app.get("/api/hub/resources")
    def resources(local_only: bool = True) -> dict[str, Any]:
        """This Mac's host and studio snapshot, as a peer Hub reads it.

        A controller will not send work to a machine it cannot see. Its `peers.dispatch_ready`
        needs a fresh snapshot from this endpoint, so without it a joined node is listed, is
        reachable, passes every other check - and is never dispatched to, with nothing said
        about why. `local_only=true` keeps this Mac from fanning the question back out to its
        own peers.
        """
        from .backend import resources as resource_stats
        try:
            host = resource_stats.host_stats()
        except Exception:
            host = {"error": "host statistics are unavailable"}
        studios: dict[str, Any] = {}
        for worker in monitor.registry:
            if local_only and worker.get("local") is not True:
                continue
            try:
                process = resource_stats.studio_process_stats(worker["port"])
            except Exception:
                process = None
            state = monitor.status.get(worker["id"]) or {}
            studios[worker["modality"]] = {
                "id": worker["id"],
                "machine": worker.get("machine", "local"),
                "status": state.get("status", "unknown"),
                "process": process,
            }
        try:
            proxy = resource_stats.proxy_stats()
        except Exception:
            proxy = None
        # The version travels with the host snapshot because it is what tells an operator
        # whether a node is running the build they think it is, and a snapshot is the only
        # thing a controller has to go on for a Mac it does not own.
        # What starts when this Mac starts travels with the snapshot for the same reason the version
        # does: it is the only thing a controller has to go on for a Mac it does not own, and "will
        # this Mac serve after a reboot" is the question a fleet owner asks of every one of them.
        # Not on `/api/health`, which is public: a person's login settings are not for strangers.
        # What this Mac is working on right now, so a controller (and the Gateway through it) can
        # say which job a node is running instead of only that it is busy. `None` when idle.
        try:
            from .backend import activity as activity_view
            from .backend import peers as peer_view
            local_jobs = activity_view.current_jobs(
                [worker for worker in monitor.registry if peer_view.is_local_studio(worker)],
                monitor.status, activity_view.live_batches())
            local_job = next(iter(local_jobs.values()), None)
        except Exception:
            local_job = None
        # Who this Mac is and what it is called now, so its controller's roster (and the Gateway)
        # follows a rename instead of keeping the name it joined with.
        from .backend import fleet_nodes as fleet_view
        from .backend import registry as registry_view
        from .backend import preferred_hold as preferred_view
        # Bounded orphan reconciliation runs on this node, never at the controller's check-in.
        from . import developer_gate_service
        developer_gate_service.recover_if_due()
        _saved_pref = registry_view.preferred_work("local")
        _policy_pref = preferred_view.normalized(_saved_pref)
        answer = {"host": host, "studios": studios, "proxy": proxy, "version": _app_version(),
                "disk": host.get("disk"), "startup": _startup_snapshot(),
                "current_job": local_job, "machine": fleet_view.this_machine(),
                # Why this Mac's Hub last stopped (quit, crash, Force Quit...), as the app recorded it.
                "last_stop": fleet_view.this_last_stop(),
                # Workstation mode: whether this Mac takes new work right now, and why not.
                "workstation": workstation.status(),
                "priority_order": registry_view.priority_order("local"),
                "chat_priority": registry_view.chat_priority("local"),
                "function_enabled": registry_view.function_switches("local")}
        # Optional frozen gate inventory measured by an explicit gate attempt; a status
        # read never probes the mount table itself.
        gate_inventory = developer_gate_service.cached_inventory_for_resources()
        if gate_inventory is not None:
            answer["developer_gate_inventory"] = gate_inventory
        # Exact work-volume facts from the same probe, optional and absent when unmeasured.
        work_volume = developer_gate_service.work_volume_for_resources()
        if work_volume is not None:
            answer["work_volume"] = work_volume
        # The Mac's own idle-hold truth, read by its controller for group arbitration: the saved
        # preference and the bounded remaining window. An older reader ignores both fields.
        if _policy_pref:
            answer.update(preferred_view.snapshot_fields(
                _policy_pref, registry_view.preferred_hold("local")))
        else:
            answer["preferred_work"] = None
            answer["preferred_hold_remaining_s"] = 0.0
        return answer

    @app.get("/api/hub/workers")
    def workers() -> dict[str, Any]:
        # Token and process metadata are intentionally excluded from responses.
        fields = ("id", "title", "modality", "machine", "runtime_identity")
        return {"workers": [{key: worker[key] for key in fields if key in worker}
                            for worker in monitor.registry]}

    @app.get("/api/hub/controller")
    def controller() -> dict[str, Any]:
        return {"settings": control_plane.public_settings(),
                "runtime": control_plane.runtime.status(),
                "readiness": control_plane.runtime.readiness(),
                "capacity": control_plane.runtime.capacity(monitor)}

    @app.put("/api/hub/controller")
    def save_controller(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("database_mode") == "shadow":
            raise HTTPException(409, "PostgreSQL shadow mode is unavailable in the packaged runtime")
        try:
            return control_plane.save_settings(body, clear_database_url=bool(body.get("clear_database_url")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/hub/capabilities")
    async def capabilities() -> dict[str, Any]:
        from .backend import capabilities as capability_contract
        return await capability_contract.build_snapshot(
            monitor, app_version=_app_version(), settings=control_plane.public_settings(),
            readiness=control_plane.runtime.readiness(),
            base_capacity=control_plane.runtime.capacity(monitor),
        )

    @app.get("/api/hub/model-packages")
    def model_packages_list() -> dict[str, Any]:
        """Every package the Hub ships, one row per manifest, each built on its own.

        This route is how the app learns which models exist, and the app reads it with a hard
        ``try`` inside its refresh: a failure here does not degrade one list, it marks the Hub
        unreachable and blanks every model picker on every Mac. So a manifest that cannot be
        used produces its own row carrying the reason, and the other rows are returned anyway.
        """
        try:
            catalog = _model_catalog()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        # One pass over the studios' last-good catalogs for the whole route: the `lane_contract`
        # join is per model but its source is shared, and a worker that is down simply leaves the
        # key null on its rows.
        contracts = _lane_contracts(monitor)
        return {"models": [_model_row(manifest, catalog, monitor, contracts)
                           for manifest in catalog.values()]}

    @app.get("/api/hub/model-presets")
    def model_presets_screen() -> dict[str, Any]:
        """The install screen's whole answer: this Mac, four presets, and every model by job.

        Owner decision 2026-10-28. One read, computed fresh every time - a projection cached past a
        model switch, an install or a disk change is a number the screen shows that is no longer
        true. It is a **read**: the screen's Apply is the existing
        `POST /api/hub/model-packages/{id}/download`, one request per package, so there is no
        second copy path and no batch endpoint to keep in step.

        Four things reach the screen that the catalogue route cannot answer:

        * the free space on the **model library's** volume (the catalogue reports the data
          directory's, which on a kit setup is a different disk);
        * which packages are **really** on this Mac, from the receipts, so "complete" means
          installed and verified and never a byte count;
        * how each missing package would arrive - another Mac's LAN share, the SSD kit that is
          mounted, or the pinned internet URLs - per package, because a kit can ship a manifest
          without its weights (`florence-2-large-ft`, `phonon-2-mlx` on the 2026-10-28 write);
        * what copying a set costs, and whether it would take this Mac below the floor it already
          stops work at.
        """
        from .backend import model_install_plan
        try:
            catalog = _model_catalog()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        try:
            library = _model_library()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        import psutil
        from components import model_packages
        receipts: dict[str, dict[str, Any]] = {}
        for package_id, manifest in catalog.items():
            if manifest.get(_MANIFEST_ERROR):
                continue
            try:
                receipts[package_id] = model_packages.preflight(manifest, library, catalog=catalog)
            except Exception as exc:  # noqa: BLE001 - one package must not blank the screen
                receipts[package_id] = {"package_id": package_id, "state": "invalid",
                                        "reason": f"The Hub could not read this package's receipt: {exc}"}
        memory_gb = round(psutil.virtual_memory().total / (1024 ** 3))
        return model_install_plan.snapshot(
            manifests=catalog, receipts=receipts, library=library, memory_gb=memory_gb,
            stop_bytes=disk_governor.STOP_BYTES, modality_of=_modality_for_engine,
            lan_source=_lan_sources(catalog, library, monitor))

    @app.get("/api/hub/catalog")
    @app.get("/api/hub/models")
    async def catalog() -> dict[str, Any]:
        """Existing Hub aggregate contract, gathered only through private workers."""
        # model_id is the manifest's stable identifier, added to every model row
        # so /api/hub/models and /api/hub/jobs share one join key. The worker's
        # own repo key is preserved unchanged.
        return model_ids.annotate(await monitor.aggregate_catalog())

    @app.get("/api/hub/model-health")
    def model_health() -> dict[str, Any]:
        """Models the Hub has paused, and why. A pause is Hub state, not worker state, so it is
        reported here rather than inferred from a catalogue that still calls the model ready.

        ``models`` is the additive per-(machine, model) health answer: one row
        with a closed-vocabulary ``state`` and an honest ``reason``/``hint``.
        The existing ``paused`` key is kept unchanged.
        """
        from .backend import model_health as model_health_contract
        return {
            "paused": broker.model_protection_snapshot(),
            "models": model_health_contract.snapshot(monitor),
        }

    @app.post("/api/hub/model-health/recheck")
    async def model_health_recheck(body: dict[str, Any]) -> dict[str, Any]:
        """Let a repaired model back into service without restarting the Hub.

        Pausing is automatic after repeated terminal failures; until now *unpausing* was only
        possible by editing the Hub's own state file and relaunching the app.
        """
        model = str(body.get("model") or "").strip()
        if not model:
            raise HTTPException(400, "model is required")
        studio = str(body.get("studio") or "").strip()
        if not studio:
            matching = [row["studio"] for row in broker.model_protection_snapshot()
                        if row.get("model") == model]
            if len(matching) != 1:
                raise HTTPException(409, "Name the paused Studio for this model")
            studio = matching[0]
        try:
            return await broker.recheck_model_protection(studio, model)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    async def worker_read(worker_id: str, path: str) -> dict[str, Any]:
        """Authenticated pass-through read from one named worker.

        Public Subtitle job state must come from the Subtitle worker, never from the Voice
        worker, otherwise the studio separation is only nominal.
        """
        worker = next((row for row in monitor.registry if row["id"] == worker_id), None)
        label = worker_id.title()
        if worker is None:
            raise HTTPException(503, f"{label} worker is unavailable")
        try:
            response = await monitor._client.get(
                worker["base_url"] + path,
                headers={"X-Hub-Token": worker["hub_token"]}, timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, f"{label} worker read failed") from exc
        if not isinstance(payload, dict):
            raise HTTPException(502, f"{label} worker returned an invalid response")
        return payload

    async def voice_read(path: str) -> dict[str, Any]:
        return await worker_read("voice", path)

    @app.get("/studio/voice/api/generate/availability")
    async def voice_availability() -> dict[str, Any]:
        return await voice_read("/api/generate/availability")

    @app.get("/studio/voice/api/catalog")
    async def voice_catalog() -> dict[str, Any]:
        return await voice_read("/api/catalog")

    @app.get("/studio/voice/api/transcribe/availability")
    async def transcription_availability() -> dict[str, Any]:
        return await voice_read("/api/transcribe/availability")

    @app.get("/studio/voice/api/downloads")
    async def voice_downloads() -> dict[str, Any]:
        return await voice_read("/api/downloads")

    @app.get("/studio/voice/api/settings")
    async def voice_settings() -> dict[str, Any]:
        return await voice_read("/api/settings")

    # Independent Subtitle studio reads. These come from the subtitle worker, not Voice.
    @app.get("/studio/subtitle/api/catalog")
    async def subtitle_catalog() -> dict[str, Any]:
        return await worker_read("subtitle", "/api/catalog")

    @app.get("/studio/subtitle/api/transcribe/availability")
    async def subtitle_availability() -> dict[str, Any]:
        return await worker_read("subtitle", "/api/transcribe/availability")

    @app.get("/studio/subtitle/api/generate/transcribe/jobs")
    async def subtitle_jobs() -> dict[str, Any]:
        return await worker_read("subtitle", "/api/generate/transcribe/jobs")

    @app.get("/studio/subtitle/api/generate/transcribe/jobs/{job_id}")
    async def subtitle_job(job_id: str) -> dict[str, Any]:
        return await worker_read("subtitle", f"/api/generate/transcribe/jobs/{job_id}")

    # ── joined nodes ───────────────────────────────────────────────────────
    # One app is the controller and the others join it by address. Every route is behind the
    # Hub credential like the rest of the private surface: a join is not a public endpoint.

    @app.get("/api/hub/fleet/overview")
    async def fleet_overview() -> dict[str, Any]:
        """One row per machine in the fleet: is it there, what is it running, how fast is it.

        The pieces existed in three places - the peer snapshot for remote host state, the fleet
        roster for joined nodes, and the ledger for measured speed - and none of them answered
        "how is my fleet doing". This puts them in one row per Mac so the app can show it.

        The rows come from the *group*, not from whichever Macs happen to be in the dispatch
        registry right now. That distinction is the whole answer to "show me my pool": a Mac that
        has joined belongs in the list whether it is awake or asleep, and on a node the group
        includes the controller and the Macs beside it - which this Mac can only know by reading
        the roster back from the controller it joined.
        """
        from .backend import broker
        from .backend import fleet_nodes as fleet
        from .backend import ledger, peers
        from .backend import registry as registry_view
        from .backend import resources as resource_stats
        from .backend import activity as activity_view
        measured = ledger.stats().get("by_machine") or {}
        # **This Mac's measured final-render speed** (owner, 2026-10-28: "prefer the fastest render
        # Mac", "from measured evidence first"). `resources.host_stats` already publishes it on
        # every host snapshot this Hub serves, which is how a joined Mac's own figure reaches its
        # controller and the Gateway; here it is added to this Mac's machine row in this overview,
        # from the same function so the two cannot disagree. A Mac with no timed render simply has
        # no such key - never 0.
        local_render_speed = resource_stats.measured_render_speed()
        local_role = str(control_plane.load_settings().get("role") or "standalone")
        # The job each machine is on right now, from the activity this Hub already holds for every
        # registered studio, local or remote. A machine absent here is not working (or not known to
        # be): the field is None rather than a guess.
        try:
            working_on = activity_view.current_jobs(
                monitor.registry, monitor.status, activity_view.live_batches())
        except Exception:
            working_on = {}
        pool = await fleet.pool()
        # The recorded refusal is a fact about why a Mac could not take work, kept by the broker.
        # It is not a threshold computed here: a machine without one has nothing to say on its row.
        protection = broker.machine_protection_snapshot() or {}
        now = time.time()

        def refusal_of(machine: str) -> dict[str, Any]:
            refusal = (protection.get(machine) or {}).get("refusal")
            if not isinstance(refusal, dict) or not isinstance(refusal.get("at"), (int, float)):
                return {}
            return {"refusal": {
                "code": refusal.get("code"),
                "message": refusal.get("message"),
                "age_s": round(now - refusal["at"]),
            }}

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Members of the group as this Mac knows it. A roster row is probed before it is reported,
        # so `reachable` is a fact about now and `detail` says why not when it is not.
        members: dict[str, dict[str, Any]] = {}
        for entry in ([pool["controller"]] if pool.get("controller") else []) + pool["nodes"]:
            machine = str(entry.get("machine") or "")
            if machine and machine not in members:
                members[machine] = entry

        for studio in monitor.registry:
            machine = str(studio.get("machine") or "local")
            if machine in seen:
                continue
            seen.add(machine)
            on_this_machine = [item for item in monitor.registry
                               if str(item.get("machine") or "local") == machine]
            member = members.get(machine)
            if peers.is_local_studio(studio):
                try:
                    host = resource_stats.host_stats()
                except Exception:
                    host = {}
                health, detail = machine_health([
                    (str(item.get("title") or item.get("modality") or item["id"]),
                     (monitor.status.get(item["id"]) or {}).get("status", "unknown"))
                    for item in on_this_machine
                ])
                # This Hub's own configured identity, so the controller-local row names the
                # real Mac and site instead of the same "This Mac" every site would show.
                # `machine` stays the raw routing sentinel ("local"); `controller_id` is
                # stable per Hub, not a global identity.
                identity = control_plane.execution_identity()
                row = {
                    "machine": machine,
                    "label": identity["machine_label"],
                    "machine_label": identity["machine_label"],
                    "controller_id": identity["controller_id"],
                    "site_id": identity["site_id"],
                    "site_name": identity["site_name"],
                    "role": local_role,
                    "reachable": True,
                    "version": _app_version(),
                    "health": health,
                    "detail": detail,
                    "studios": {"total": len(on_this_machine),
                                "up": sum((monitor.status.get(item["id"]) or {}).get("status") == "up"
                                          for item in on_this_machine)},
                    "startup": _startup_snapshot(),
                    "last_stop": fleet.this_last_stop(),
                    "workstation": workstation.status(),
                }
                row["priority_order"] = registry_view.priority_order("local")
                row["chat_priority"] = registry_view.chat_priority("local")
                row["function_enabled"] = registry_view.function_switches("local")
                row["assigned_image_model"] = registry_view.assigned_image_model("local")
            else:
                snapshot = peers.cached(machine) or {}
                # The roster's probe is the one read of this machine: it answers directly when no
                # current snapshot exists and, when there is one, reuses the peer poll the dispatch
                # path already trusts - so the page and the scheduler cannot disagree about a Mac.
                # Its host facts are what this row shows; a roster member is not read a second time
                # through the peer cache, which is what the separate `pool()` probe used to leave
                # unpopulated until the next poll happened to run.
                if member is not None:
                    reachable = member.get("state") == "reachable"
                    health = "up" if reachable else str(member.get("state") or "unknown")
                    detail = member.get("detail")
                    version = member.get("version")
                    host = member.get("host") or {}
                    startup = member.get("startup")
                    last_stop = member.get("last_stop")
                    workstation_row = member.get("workstation")
                else:
                    startup = snapshot.get("startup") if isinstance(snapshot.get("startup"), dict) else None
                    reachable = bool(snapshot.get("reachable") and snapshot.get("auth"))
                    health = ("up" if snapshot.get("status") == "connected"
                              else str(snapshot.get("status") or "unknown"))
                    detail = None if snapshot else "this Mac has not reported in yet"
                    version = snapshot.get("version")
                    host = snapshot.get("host") or {}
                    last_stop = fleet.last_stop_of(snapshot.get("last_stop"))
                    workstation_row = fleet._workstation_of(snapshot.get("workstation"))
                row = {
                    "machine": machine,
                    "label": str((member or {}).get("label")
                                 or (studio.get("title") or machine).split(" · ")[0]),
                    "role": str((member or {}).get("role") or "agent"),
                    "reachable": reachable,
                    "version": version,
                    "health": health,
                    "detail": detail,
                    "startup": startup,
                    "last_stop": last_stop if isinstance(last_stop, dict) else None,
                    "workstation": workstation_row if isinstance(workstation_row, dict) else None,
                }
                policy_source = member if isinstance(member, dict) and (
                    "priority_order" in member or "chat_priority" in member) else snapshot
                remote_order = (
                    registry_view.normalize_priority_order(policy_source.get("priority_order"))
                    if "priority_order" in policy_source
                    else (["chat"] if policy_source.get("chat_priority") is True else [])
                )
                row["priority_order"] = remote_order
                row["chat_priority"] = bool(remote_order and remote_order[0] == "chat")
                switches_source = member if isinstance(member, dict) and (
                    "function_enabled" in member) else snapshot
                row["function_enabled"] = fleet._function_enabled_of(switches_source)
                # Kept on this controller, which is the one that dispatches (registry flags).
                row["assigned_image_model"] = registry_view.assigned_image_model(machine)
            stats = measured.get(machine) or {}
            rows.append({
                **row,
                "address": (member or {}).get("address"),
                "memory_gb": host.get("total_gb"),
                "available_gb": host.get("available_gb"),
                "disk_free_gb": host.get("disk_free_gb"),
                "disk_total_gb": host.get("disk_total_gb"),
                "disk": host.get("disk"),
                **_trash_and_memory(host),
                "current_job": working_on.get(machine) or (member or {}).get("current_job"),
                "chip": host.get("chip"),
                # This Mac's own IPv4 interface addresses (resources.network_addresses), so the
                # Gateway can tell a worker on the same local network from one reached over
                # Tailscale. None from a Mac whose Hub predates it - never guessed from `address`.
                "network": host.get("network"),
                # Which interface carries this Mac's default route, what kind of link it is and the
                # media its Ethernet port negotiated - so a Mac thinned to 100 Mb/s by a bad cable
                # is visible on the fleet instead of reading "online". None from a Hub older than
                # 0.6.206; never guessed from anything else in this row.
                "connection": host.get("connection"),
                # Older peers may still report this optional field.
                **({"removable_volumes": host["removable_volumes"]}
                   if "removable_volumes" in host else {}),
                "jobs": stats.get("count", 0),
                "average_seconds": stats.get("avg_s"),
                "modalities": stats.get("modalities") or {},
                # This Mac's own measured final-render speed, or absent when it has not rendered.
                # Only this Mac: another machine's figure arrives on its own host snapshot.
                **({"render_speed": local_render_speed}
                   if machine == "local" and local_render_speed is not None else {}),
                **refusal_of(machine),
            })

        # A member this Mac runs no studio for. A node with no worker started, or a controller in a
        # group whose roster this Mac read back, is still part of the pool and must be counted -
        # leaving it out is how a group of four Macs reports itself as one.
        for machine, member in members.items():
            if machine in seen or machine == "local":
                continue
            seen.add(machine)
            reachable = member.get("state") == "reachable"
            studios = member.get("studios") or []
            modalities: dict[str, int] = {}
            for entry in studios:
                modality = str(entry.get("modality") or "")
                if modality:
                    modalities[modality] = modalities.get(modality, 0) + 1
            # A member this Mac probed carries its own host facts here; one learned from a
            # controller's roster does not, and stays blank rather than borrowing this Mac's.
            host = member.get("host") or {}
            rows.append({
                "machine": machine,
                "label": str(member.get("label") or machine),
                "role": str(member.get("role") or "agent"),
                "address": member.get("address"),
                "reachable": reachable,
                "version": member.get("version"),
                "health": "up" if reachable else str(member.get("state") or "unknown"),
                "detail": member.get("detail"),
                "startup": member.get("startup"),
                "last_stop": member.get("last_stop"),
                "workstation": member.get("workstation"),
                "priority_order": fleet._priority_order_of(member),
                "chat_priority": bool(fleet._priority_order_of(member)
                                      and fleet._priority_order_of(member)[0] == "chat"),
                "function_enabled": fleet._function_enabled_of(member),
                "memory_gb": host.get("total_gb"), "available_gb": host.get("available_gb"),
                "disk_free_gb": host.get("disk_free_gb"),
                "disk_total_gb": host.get("disk_total_gb"), "disk": host.get("disk"),
                **_trash_and_memory(host),
                "current_job": working_on.get(machine) or member.get("current_job"),
                "chip": host.get("chip"),
                # This Mac's own IPv4 interface addresses (resources.network_addresses), so the
                # Gateway can tell a worker on the same local network from one reached over
                # Tailscale. None from a Mac whose Hub predates it - never guessed from `address`.
                "network": host.get("network"),
                # See the member row above: the same reading, from the same `host` snapshot.
                "connection": host.get("connection"),
                **({"removable_volumes": host["removable_volumes"]}
                   if "removable_volumes" in host else {}),
                "jobs": (measured.get(machine) or {}).get("count", 0),
                "average_seconds": (measured.get(machine) or {}).get("avg_s"),
                "modalities": modalities,
                **refusal_of(machine),
            })

        return {
            "machines": rows,
            "role": pool["role"],
            "member": bool(pool.get("controller")),
            "controller": pool.get("controller"),
            "roster_readable": pool.get("readable", True),
            "summary": {
                "total": len(rows),
                "reachable": sum(row["reachable"] for row in rows),
                "unreachable": sum(not row["reachable"] for row in rows),
            },
        }

    @app.get("/api/hub/fleet/nodes")
    async def fleet_nodes() -> dict[str, Any]:
        from .backend import fleet_nodes as fleet
        return await fleet.inventory()

    @app.post("/api/hub/fleet/adopt")
    def fleet_adopt(body: dict[str, Any]) -> dict[str, Any]:
        """Take on a fleet's credential, which is what joining one means.

        A controller will not send work to a machine whose peer snapshot it cannot trust, and
        trust starts with the credential: every Hub mints its own, so a node that joined with a
        controller's token still answers 401 to that controller until it adopts the same one.

        Adopting also records *which* controller this Mac joined, because taking the credential is
        the moment the group becomes a fact about this Mac. Without it the Hub knew it was holding
        somebody else's token but not whose, so nothing on this Mac could name its own pool - the
        address lived only in the app's preferences, where a reinstall or a second app window
        loses it while the credential stays.

        This is deliberately **not** in the fleet credential's allowlist. Adopting is how a Mac
        joins a fleet, so it must be the Mac owner's decision - requiring the Hub token, which
        never leaves this machine, keeps a fleet-token holder from moving a node between fleets
        or locking it out of its own.
        """
        from .backend import fleet_nodes as fleet
        from .backend import peers
        value = str(body.get("token") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", value):
            raise HTTPException(400, "a fleet token is 24 or more letters, numbers, dashes or underscores")
        # **The node credential comes back with the adoption.** The join answer carries it once
        # (A8) and only this Mac may keep it, so the app hands it straight back here and the Hub
        # writes it 0600 where every node-originated call reads it from. Storing it from the app
        # would mean the app had to know the Hub's data directory, which is the Hub's business.
        node_credential = body.get("node_credential")
        values: dict[str, Any] = {}
        # Which controller was joined, recorded with the credential it came from. Always written,
        # never left at its previous value: an adoption that does not name a controller leaves this
        # Mac *not knowing* which one it answers to, and a stale address from a previous group would
        # show the wrong Mac as the head of this one. Not knowing is recoverable - re-join with an
        # address - and being confidently wrong is not.
        address = body.get("controller_url")
        if address is not None and str(address).strip():
            try:
                values["parent_controller_url"] = fleet.validate_address(address)
            except ValueError as exc:
                raise HTTPException(400, f"controller_url {exc}") from exc
        else:
            values["parent_controller_url"] = None
        controller_id = body.get("parent_controller_id")
        if controller_id is not None and not fleet.ID_PATTERN.fullmatch(str(controller_id)):
            raise HTTPException(400, "parent_controller_id must be a valid roster identity")
        values["parent_controller_id"] = controller_id if values["parent_controller_url"] else None
        if node_credential is not None:
            fleet.set_node_credential(str(node_credential))
        peers.set_fleet_token(value)
        # Record that this credential is someone else's. Promotion to controller reads this and
        # mints a fresh one, so a machine that was a node never ends up running a group on the
        # credential of the group it used to belong to.
        control_plane.save_settings({"fleet_credential_origin": "adopted", **values})
        return {"ok": True, "adopted": True,
                "controller_url": control_plane.load_settings().get("parent_controller_url")}

    @app.post("/api/hub/fleet/leave")
    def fleet_leave() -> dict[str, Any]:
        """Stop being a node: drop the borrowed credential and run on this Mac's own again.

        The mirror of adopting, and the answer to "this Mac is being retired or sold". A Mac that
        leaves a group must not keep the group's token: it is the credential of a machine somebody
        else owns, and it is the one thing that must not travel with a Mac out of the door. Minting
        a fresh token here makes the departure real rather than cosmetic - the old controller's
        credential stops working on this Mac immediately, so it cannot dispatch to it even while
        its roster still lists it.

        Like adopting, this needs the Hub token and stays outside the fleet credential's allowlist:
        a controller must not be able to un-join a node, because leaving is the Mac owner's
        decision and the local Hub token never leaves this machine.
        """
        from .backend import app_release
        from .backend import fleet_nodes as fleet
        from .backend import peers
        from .backend import state_trash
        peers.set_fleet_token("")
        # A Mac that missed the last rotation was being held on its old group token so this
        # controller could still tell it the new one. None of that survives leaving: there is no
        # group left here to be told about, and the held values are copies of a group secret.
        peers.clear_previous_tokens()
        # **The credential the old controller issued goes too.** A Mac that is leaving — or being
        # sold — must not keep a working key to the group it just left, and the app cannot delete
        # it because the file is the Hub's own business (`0600`, in the Hub's data directory).
        # Found by the native work: the app cleared its copy and the Hub's stayed, which is inert
        # after leaving but is exactly the wrong thing to find on a Mac in someone else's hands.
        fleet.set_node_credential(None)
        # **And the gateway key this Mac pinned while it was in that group.** `release_sync`
        # refuses a descriptor signed by a key other than the pinned one, and `pin_publisher`
        # never replaces an existing pin — that is an owner action. So a Mac that left group A and
        # joined group B whose gateway signs with a different key would have refused every release
        # from its new controller, for ever, with a sentence about a key nobody on that Mac chose.
        # The pin belongs to the membership: the app already clears its own copy of it on leave
        # (`clearGroupMembershipLocally`), and this is the Hub's half. Archived rather than
        # deleted so the operator can still read which gateway that Mac was following.
        archived = state_trash.move_to_trash(app_release.TRUST_FILE, reason="leave-fleet")
        control_plane.save_settings({
            "fleet_credential_origin": "minted",
            "role": "standalone",
            "parent_controller_url": None,
        })
        return {"ok": True, "left": True,
                # What it keeps is as load-bearing as what it drops: an operator reading "left"
                # has to know his models and his Hub identity are untouched.
                "kept": ["this Mac's Machine ID", "its model library", "its owner settings",
                         "its local Hub token", "its jobs and output"],
                "archived": {"gateway_publisher_pin": archived} if archived else {}}

    @app.post("/api/hub/fleet/nodes", status_code=201)
    async def fleet_join(body: dict[str, Any], request: Request) -> dict[str, Any]:
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = hub_auth._fleet_actor(request)
        try:
            joined = fleet.join(body, actor=actor, ticket_token=actor.get("ticket"))
        except fleet.NodeRemovedError as exc:
            # **403, not 400.** The asking Mac is well formed and known by name; it is refused
            # on purpose, and the app stops its re-join loop on this status instead of retrying
            # every five minutes for ever against a decision that was already made.
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # A node that just joined has to enter this controller's dispatch registry now.
        # The registry is otherwise loaded once at startup, so without this the node was
        # listed as reachable but no work routed to it until the Hub was restarted.
        monitor.reload_registry()
        if actor.get("kind") == "join":
            # A Mac that came back on a ticket is registered afresh: activity and machine state
            # are read per registration epoch, so this Mac's record starts here and none of its
            # previous life is shown as this one's.
            ledger.reconcile_machine_registrations(set(fleet._read()))
        # `credential` travels back to the joining Mac **once**, in this answer, and is never
        # published on any read route. The caller stores it 0600 in its own data dir and
        # presents it on every later call home (A8).
        return joined

    @app.delete("/api/hub/fleet/nodes/{node_id}")
    async def fleet_leave(node_id: str, request: Request) -> dict[str, Any]:
        """Remove a node from this controller — and make the removal stick.

        Two callers, and the difference is who is asking (A2): this Mac's own operator with the
        Hub token (any node), and a Mac removing **itself** with the Fleet or node credential it
        holds. Before this the route refused every fleet credential outright, so "Leave this
        group" reported success while the controller still listed the Mac — and a node removed
        here re-joined on its next start, because nothing had been revoked (B2, B3).

        The operator's removal is an **eviction**: the node credential goes with the row and the id
        is recorded as removed, so `join` refuses it by name until someone re-admits it
        deliberately. A node removing **itself** is forgotten with no record (see below).
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        offered = hub_auth.presented_token(request) or ""
        actor = hub_auth._fleet_actor(request)
        if not hub_auth.self_removal_allowed(request, node_id, credential=offered):
            raise HTTPException(
                403,
                f"a fleet credential may remove only the Mac that holds it. Removing "
                f"'{node_id}' from this controller is the owner's decision: use this "
                f"controller's Hub token, or let that Mac leave from its own Connections page.")
        if actor.get("kind") == "node":
            # **A Mac leaving on its own is forgetting, not being evicted.** No tombstone: the owner
            # can put it back by joining again, and nothing stays on this controller to be tidied.
            # Only the operator's removal (Hub token) is remembered and refuses a re-join.
            row = fleet._read().get(str(node_id).strip().lower())
            if row is None or not fleet.leave(str(node_id).strip().lower()):
                raise HTTPException(404, "unknown node")
            removed = {"id": row.get("id"), "name": row.get("name")}
        else:
            removed = fleet.remove(node_id, by="operator")
            if removed is None:
                raise HTTPException(404, "unknown node")
        monitor.reload_registry()
        return {"ok": True, "removed": removed}

    @app.post("/api/hub/fleet/nodes/{node_id}/unregister")
    async def fleet_unregister_node(node_id: str, body: dict[str, Any] | None,
                                    request: Request) -> Any:
        """**Remove one Mac from this controller with the credential a Gateway holds** (design 3.1).

        The Gateway's "Remove This Mac…" has to reach the machine through the only token it has,
        which is the group's Fleet token, so this route is on that allowlist. Two callers are
        still distinguished: a *node* credential may unregister only the Mac that holds it, and
        the Fleet token — the Gateway acting for the operator — may name any Mac on this roster.

        The removal is an **eviction** (`fleet.remove`): the row goes, the credential it was
        issued is revoked, the work queued for it is dropped, and the id is tombstoned so the
        Mac cannot put itself back. Everything the Hub held for it was in those two files, so
        there is nothing else to purge and `purge` is honoured by construction.

        `404` carries `NODE_NOT_FOUND` **as a code**, not just a status: the Gateway reads a
        route-missing 404 as "this Hub is too old" and a bare one cannot be told from it.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        asked = node_id.strip().lower()
        actor = hub_auth._fleet_actor(request)
        if actor.get("kind") == "node" and asked != str(actor.get("node_id") or "").strip().lower():
            raise HTTPException(403, "a node credential may unregister only the Mac that holds it")
        payload = body if isinstance(body, dict) else {}
        removed = fleet.remove(asked, by=str(payload.get("requested_by") or "operator"))
        if removed is None:
            return JSONResponse({"ok": False, "code": "NODE_NOT_FOUND",
                                 "detail": "No such node."}, status_code=404)
        # Activity and machine state are read per registration epoch (`ledger`), so closing this
        # Mac's epoch here is what makes a later reconnect start with no record of its last life.
        ledger.reconcile_machine_registrations(set(fleet._read()))
        monitor.reload_registry()
        return {"ok": True, "removed": True, "machine": asked, "revoked": True}

    @app.post("/api/hub/fleet/nodes/connect")
    def fleet_connect_node(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """**Hand out a one-time join ticket for a Mac** (design 3.3) — the Gateway's Connect.

        The ticket is the whole answer. This controller never dials a Mac that is not on its
        roster, so there is no second "pair by address" path here: the Mac redeems the ticket at
        `POST /api/hub/fleet/nodes` with its own hardware identity, and that join replaces any
        tombstone for it, mints a fresh credential and starts it with an empty row — its old
        metrics and its old queue went with the removal.

        `name` is applied by that join if the operator gave one; left out, the Mac's own name is.
        """
        from .backend import fleet_nodes as fleet
        payload = body if isinstance(body, dict) else {}
        try:
            ticket = fleet.mint_join_ticket(name=str(payload.get("name") or "").strip() or None,
                                            by=str(payload.get("requested_by") or ""))
        except ValueError as exc:
            # The Gateway shows this after "No Mac was connected — ", so it is a phrase, not a
            # sentence: no leading capital, no full stop.
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "token": ticket["token"], "expires_at": ticket["expires_at"],
                "name": ticket["name"], "detail": "Ready to connect."}

    @app.get("/api/hub/fleet/nodes/{node_id}/status")
    async def fleet_node_status(node_id: str, request: Request,
                                name: str | None = None) -> dict[str, Any]:
        """Does this controller still list this Mac? `member`, `removed` (with when) or `unknown`.

        How a removal made here reaches the Mac itself: the node asks on its own tick and clears
        its membership when the answer is `removed`. It mints no credential and enqueues no work,
        so it can be asked every minute. A node credential may ask only about its own id.

        A node's own call is recorded as presence and nothing else: it stamps `last_status_at`/
        `last_seen_at`, never marks the Mac reachable or pullable, and never overwrites a
        `check-in`/`probe` reason. A group or Hub credential does not stamp.

        `name` is the Mac's current label. Only that Mac may set it (a node credential, its own
        id): the roster name is written at join, so a rename otherwise never reaches this side.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = hub_auth._fleet_actor(request)
        asked = node_id.strip().lower()
        own = actor.get("kind") == "node" and str(actor.get("node_id") or "").lower() == asked
        if actor.get("kind") == "node" and not own:
            raise HTTPException(403, "a node credential may ask only about the Mac that holds it")
        if own and name and fleet.rename(asked, name):
            monitor.reload_registry()
        result = fleet.status(asked)
        if own and result.get("state") == "member":
            fleet.note_status_seen(asked)
        return result

    @app.post("/api/hub/fleet/nodes/{node_id}/checkin")
    async def fleet_node_checkin(node_id: str, body: dict[str, Any],
                                 request: Request) -> dict[str, Any]:
        """**A joined Mac's own call home — evidence of life, and the pull channel's door.**

        This is the answer to a fleet the controller cannot dial: a Mac behind one-way Tailscale
        node sharing reaches its controller and its controller can never reach it. The Mac's own
        authenticated call is evidence, and this route records it as such — `last_seen_at`, a
        `check-in` reason kept distinct from a probe, its current address, its version and its
        Lite Mode.

        It is also where work arrives. The body carries the ids the Mac has run and the answers it
        got, and the answer carries back the requests this controller could not dial it with —
        each one the exact request the dial would have sent, for the Mac to replay on its own Hub.

        A node credential may check in only for the Mac that holds it, checked here for the same
        reason the status route checks it: an allowlist cannot see who is asking.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        from .backend import fleet_pull
        payload = body if isinstance(body, dict) else {}
        actor = hub_auth._fleet_actor(request)
        asked = node_id.strip().lower()
        own = actor.get("kind") == "node" and str(actor.get("node_id") or "").lower() == asked
        if actor.get("kind") == "node" and not own:
            raise HTTPException(403, "a node credential may check in only for the Mac that holds it")
        row = fleet.record_checkin(asked, payload)
        if row is None:
            # A Mac this controller has never heard of, or has removed. The same three states the
            # status route answers, so the Mac's own handling of `removed`/`unknown` is unchanged.
            # Nothing it carries is written: recording its results would re-create the pull row
            # that `forget` dropped when the Mac was removed.
            return {**fleet.status(asked), "server_time": time.time(),
                    "reachable_via": None, "commands": []}
        # What the Mac has already run, and what it answered. Recorded before the queue is read, so
        # a command it has finished can never be handed back to it in this same answer.
        seen = [str(value) for value in (payload.get("seen") or []) if value] \
            if isinstance(payload.get("seen"), list) else []
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        for entry in results:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            try:
                status = int(entry.get("status") or 200)
            except (TypeError, ValueError):
                status = 200
            fleet_pull.record_result(asked, str(entry["id"]), status, entry.get("json"))
        fleet_pull.ack(asked, seen)
        if payload.get("address") or payload.get("studios"):
            # Where it answers and what it runs decide the dispatch registry, so a change is
            # picked up now rather than at the next restart - the same reason a join reloads it.
            monitor.reload_registry()
        return {**fleet.status(asked), "server_time": time.time(), "reachable_via": "check-in",
                "commands": fleet_pull.take_commands(asked, seen)}

    @app.post("/api/hub/fleet/nodes/{node_id}/readmit")
    async def fleet_readmit(node_id: str) -> dict[str, Any]:
        """Undo an eviction: allow this id to join again.

        Deliberately Hub-token only — it is in neither credential's allowlist. Re-admitting a
        machine the owner removed is a decision about a Mac he may already have sold, and a
        node must never be able to undo its own removal.
        """
        from .backend import fleet_nodes as fleet
        row = fleet.readmit(node_id)
        if row is None:
            raise HTTPException(404, "that machine is not on this controller's removed list")
        return {"ok": True, "readmitted": row}

    @app.post("/api/hub/fleet")
    async def fleet_membership(body: dict[str, Any], request: Request) -> dict[str, Any]:
        """**Set this Mac's group membership** — one route, two callers, two scopes.

        *With this Mac's Hub token* (the operator, on the controller whose credential this is):
        **rotate the Fleet token**. A4: the rotation code has existed in `peers.sync_fleet_token`
        since before the audit with no caller and no route, while the token was shown in the clear
        on every controller's Connections page, held by every member and by GatewayKH — so a leak
        could not be undone. Minting here and pushing to every node is what makes it undoable.

        *With the Fleet token* (a controller, pushing to a node): **adopt the group this Mac
        belongs to** — the new credential and, for a handover, the new controller's address. This
        is the write A3's promotion needs and A4's rotation needs; they are the same statement,
        which is why they are the same route.

        Deliberately **Hub-token or Fleet-token only**. A node's own credential is not in the
        allowlist: a leaf Mac must not be able to re-point itself at a controller of its choosing,
        and it must not be able to rotate the group credential either.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        from .backend import peers
        actor = hub_auth._fleet_actor(request)
        operator = actor.get("kind") == "hub"
        value = str(body.get("token") or "").strip()
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", value):
            raise HTTPException(
                400, "a fleet token is 24 or more letters, numbers, dashes or underscores")

        if not operator:
            # A controller is telling this Mac where its group is. Adopting records *which*
            # controller, because the credential is only half of a membership: a Mac holding a
            # token with nowhere to send it cannot name its own pool.
            address = body.get("controller_url")
            if address is not None and str(address).strip():
                try:
                    controller_url = fleet.validate_address(address)
                except ValueError as exc:
                    raise HTTPException(400, f"controller_url {exc}") from exc
            else:
                controller_url = control_plane.load_settings().get("parent_controller_url")
            previous = control_plane.load_settings().get("parent_controller_url")
            peers.set_fleet_token(value)
            # The credential this Mac was issued by its previous controller is that
            # controller's record, not the new one's: clearing it means the new controller
            # issues its own on the next join, and the old one cannot be replayed.
            fleet.set_node_credential(None)
            control_plane.save_settings({
                "fleet_credential_origin": "adopted",
                "parent_controller_url": controller_url,
                "role": "agent",
            })
            # **And this Mac introduces itself to its new head, at once** (A3 step 3). A node that
            # only changed its token would leave the new controller with an empty roster and the
            # site's capacity at one Mac, which the §9 rehearsal measured. The address is the one
            # the *old* controller reached this Mac at, because which interface a Mac answers on is
            # the app's decision at join time and the Hub must not guess it.
            registered: dict[str, Any] | None = None
            if controller_url and controller_url != previous:
                registered = await _register_with_controller(
                    controller_url, value, body.get("self_address"), fleet, monitor)
                if registered and registered.get("credential"):
                    fleet.set_node_credential(str(registered["credential"]))
            return {"ok": True, "adopted": True, "controller_url": controller_url,
                    "registered": registered}

        # Rotate. The local credential changes only after every push has been attempted, so a
        # partial failure leaves this controller working and names the Macs that need the
        # fallback rather than locking the group out.
        new_token = value or secrets.token_urlsafe(24)
        registry = fleet.remote_studios()
        async with httpx.AsyncClient(follow_redirects=False) as client:
            result = await peers.sync_fleet_token(registry, client, new_token)
        return {"ok": True, "rotated": True, "token": new_token, **result}

    @app.post("/api/hub/fleet/handover")
    async def fleet_handover(body: dict[str, Any]) -> dict[str, Any]:
        """**Hand this group over to a promoted node, while every node can still be reached.**

        The owner's decision A, and the reason the *old controller* performs the handover: it is
        the only party every node already trusts and can reach. It pushes
        `{new_controller_url, new_token}` down the channel it already has, so thirteen Macs follow
        a promotion with **zero touches per node**, and then retires itself.

        **The honest limit is in the answer, not only in a document.** If a node cannot be
        reached, this says so by name: an automatic handover is impossible for a Mac that is off,
        and the fallback is the runbook and SSH over Tailscale
        (`gateway-kh/repository/docs/credential-rotation-decision.md`). `reachable: false` here is
        an operator task, never a silent success.

        Hub-token only, and deliberately so: a node must not be able to hand the group to a
        controller of its choosing.
        """
        from .backend import fleet_nodes as fleet
        from .backend import peers
        address = body.get("controller_url")
        try:
            controller_url = fleet.validate_address(address)
        except ValueError as exc:
            raise HTTPException(400, f"controller_url {exc}") from exc
        new_token = str(body.get("token") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", new_token):
            raise HTTPException(
                400, "a fleet token is 24 or more letters, numbers, dashes or underscores")
        # **The promoted Mac is not a node any more, so it is not asked to follow.** It is the new
        # head, it has minted its own credential, and this push reaches it with a token it
        # correctly rejects — which the rehearsal measured as `manual: 1`, an operator sentence
        # naming the *new* controller as the machine that could not be reached. Its roster row is
        # forgotten without an eviction record (it is being promoted, not sold) and the push skips
        # it.
        promoted = [node_id for node_id, row in fleet._read().items()
                    if str(row.get("address") or "").rstrip("/") == controller_url]
        for node_id in promoted:
            fleet.leave(node_id)
        registry = [row for row in fleet.remote_studios()
                    if str(row.get("fleet_node") or "") not in promoted]
        async with httpx.AsyncClient(follow_redirects=False) as client:
            result = await peers.sync_fleet_token(
                registry, client, new_token,
                # **The retiring controller keeps its own credential.** It is not joining the new
                # group; it is leaving. Rotating its own token here would break the one thing it
                # still has to do — answer the operator while he removes it from the Gateway.
                local_commit=lambda _token: None,
                controller_url=controller_url,
            )
        control_plane.save_settings({
            "handed_over_to": controller_url,
            "handed_over_at": time.time(),
        })
        return {"ok": True, "handed_over_to": controller_url,
                "handed_over_at": control_plane.load_settings().get("handed_over_at"),
                # Named so the operator can see the new head was recognised rather than silently
                # skipped: a machine missing from the push with no explanation reads as a failure.
                "promoted": promoted,
                **result}

    @app.post("/api/hub/fleet/reset")
    def fleet_reset() -> dict[str, Any]:
        """**Disassemble this controller's group and start a clean one** (owner, 2026-10-25).

        The action for a controller whose group the owner no longer wants: every node row goes,
        every per-node credential is revoked, the machines that were waiting on the previous Fleet
        token are forgotten, and a **fresh group token** is minted. It is the controller-side half of
        the reset flow the owner asked for — *"make sure we can disassemble the nodes and
        controllers so I can restart anew. It's just one focus day of resetting and enrolling."* —
        and the other half is each node's own `POST /api/hub/fleet/leave`.

        **Why not just remove every node?** `fleet.remove` is an eviction and writes a tombstone that
        refuses that id for ever, which is right for a Mac being sold and wrong here: the same Macs
        re-enrol minutes later, and a controller that refused its own roster by name would make the
        reset one-way. So the roster is **archived, not evicted** — moved into the data directory's
        `_trash` (`backend/state_trash`) — and no id is recorded as removed.

        **Hub-token only, and deliberately in neither credential's allowlist.** This is the owner's
        decision about his own machines. A Fleet token that could wipe the roster it belongs to would
        turn one leaked group credential into a fleet-wide reset, and a node's own credential is
        narrower still by design.

        It is **not a rotation**: rotating hands the same nodes a new secret. This drops the nodes and
        mints a new secret, and the answer names both, because "the Macs are gone" and "the token
        changed" are two facts an operator acts on differently.
        """
        from .backend import fleet_nodes as fleet
        from .backend import peers
        report = fleet.reset_fleet(by="operator")
        # The new group credential is minted after the old nodes are gone: a rotation would have
        # pushed it to them, and there is nobody left to push it to.
        peers.clear_previous_tokens()
        peers.set_fleet_token("")
        monitor.reload_registry()
        return {**report, "token": peers.current_fleet_token(),
                "detail": ("This controller has forgotten its group and keeps the record in its "
                           "Trash Folder. Every Mac is now a standalone Hub: each one leaves from "
                           "its own Connections page, then all of them can join this Mac again "
                           "with the new Fleet token shown here.")}

    @app.post("/api/hub/fleet/repoint")
    async def fleet_repoint(body: dict[str, Any]) -> dict[str, Any]:
        """**Move this Mac to another controller without re-joining it** (owner, 2026-10-25).

        The address-only repoint: the controller changes, and this Mac's own identity — its Machine
        ID, its per-node credential, the group Fleet token, its release pins, its model library and
        its owner settings — is carried across untouched. `fleet_nodes.repoint` owns the checks and
        the one atomic write; the route only has to classify the refusal.

        **Reachable with this Mac's Hub token or the current group Fleet credential, and never with
        a node's own credential.** `auth.FLEET_ALLOWED` carries it and `auth.NODE_ALLOWED` does not
        (`node_origin_allowed` is the intersection of the two), which is the same boundary
        `POST /api/hub/fleet` already has — a group token may set this Mac's membership, a leaf
        Mac's credential may not. It grants nothing new: that route can already point this Mac at
        another controller, destructively. This one keeps the Mac.

        A 409 is not a malformed request: it is this Mac saying the migration does not apply to it
        (it is not a member, the operation names an address it has already left, the destination
        does not hold its credential, or the operation id was used for a different pair of
        addresses). The body of that answer names **which**, in a `code` and a sentence, because the
        caller's next step differs — and because a controller pushing this operation has to report
        the reason per Mac, which it can only do from what the Mac said.
        """
        from .backend import fleet_nodes as fleet
        try:
            return await fleet.repoint(body)
        except fleet.RepointRefused as exc:
            return JSONResponse({"ok": False, "code": exc.code, "detail": exc.detail,
                                 "repointed": False}, status_code=exc.status)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/fleet/repoint-nodes")
    async def fleet_repoint_nodes(body: dict[str, Any]) -> dict[str, Any]:
        """**Push an address-only repoint to this controller's joined Macs** (owner, 2026-10-25).

        The old controller is the only party every node already trusts and can dial, so it is the
        one that delivers the move — the same reason a handover is performed from here. This is
        deliberately **not** a handover: nothing is retired, no token rotates, and this controller
        keeps its roster, its credential and its work. A Mac it cannot reach keeps the address it
        has and is named in the answer as pending, never as moved.

        Every Mac gets a distinct operation id derived from the caller's, so a retry of the same
        push is a replay of the same operation on each node (`already_applied`) rather than a second
        instruction — and a fresh migration id picks up where the last one stopped.

        `expected_controller_url` is required, and it is the **caller's** statement of where these
        Macs are now: which of this controller's own addresses a node recorded at join is the app's
        decision, and the route that would tell this side (`GET /api/hub/controller`) is outside the
        Fleet credential's scope on purpose. The same shape as `POST /api/hub/fleet`, which takes the
        new head's address from its caller. A wrong value is safe — each node compares it with its
        own membership and refuses.
        """
        from .backend import fleet_nodes as fleet
        from .backend import peers
        address = body.get("controller_url")
        try:
            destination = fleet.validate_address(address)
        except ValueError as exc:
            raise HTTPException(400, f"controller_url {exc}") from exc
        try:
            expected = fleet.validate_address(body.get("expected_controller_url"))
        except ValueError as exc:
            raise HTTPException(
                400, f"expected_controller_url {exc}. It is where these Macs are now — the address "
                     f"they were told at join — and each node refuses the operation if it does not "
                     f"match its own membership.") from exc
        migration = str(body.get("operation_id") or "").strip()
        if not fleet.REPOINT_OPERATION_PATTERN.fullmatch(migration):
            raise HTTPException(
                400, "operation_id must be 8-120 letters, numbers, dots, dashes, colons or "
                     "underscores, starting with a letter or number")
        wanted = body.get("nodes")
        if wanted is not None and not isinstance(wanted, list):
            raise HTTPException(400, "nodes must be a list of node ids when given")
        registry = fleet.remote_studios()
        if wanted:
            keep = {str(node).strip().lower() for node in wanted}
            registry = [row for row in registry
                        if str(row.get("fleet_node") or "").lower() in keep]
        # `remote_studios` is where each Mac is delivered: the address the node itself declared and
        # this controller has been probing — and the destination's own `/status` is then asked from
        # the node, not from here.
        #
        # The client is the **pull-capable** one (`fleet_pull.client`), so a Mac this controller
        # cannot dial still receives the repoint through its own check-in: that is the delivery path
        # the design names, and it is why an unreachable Mac is reported `pending` rather than lost.
        from .backend import fleet_pull
        async with fleet_pull.client() as client:
            result = await peers.sync_repoint(registry, client, destination, migration, expected)
        return {"ok": True, "repointed_to": destination, "operation_id": migration, **result}

    # ── remote app-update release (controller side) ────────────────────────
    # GatewayKH pushes a signed release; this Mac verifies and mirrors it, and
    # its own nodes pull it.  The signature, not the token, is what authorises a
    # release: POST /release is fleet-reachable (a gateway holds only the Fleet
    # token), while installing the trusted gateway key is an owner action and
    # stays Hub-token only.  See backend/app_release.py for the byte contract.

    @app.get("/api/hub/release")
    def get_release() -> dict[str, Any]:
        """What this controller holds, plus the gateway key the owner installed.

        The ``release`` key is unchanged.  ``trusted_publisher`` is additive: the
        gateway key the owner installed with ``POST /api/hub/release/trust``, or
        null when none has been installed - so a Mac reading this route can say
        plainly whether it can accept a pushed release at all.  It carries the key
        itself (``public_key``) so a node can pin it at join.  ``delta`` says this
        controller takes manifest-only pushes (a gateway that finds no ``delta`` key
        pushes the whole disk image), whether it holds this release's manifest, and
        (``artifact``) whether the app may install the disk image instead.
        ``delta`` is null when this Hub has no validated baseline of its own
        installed bytes - not running from an installed app, no identity file,
        or a tree it cannot measure, or while its bytes are still being measured
        for the first time - which a gateway reads as no capability, exactly
        like a Hub old enough never to have sent the key.
        ``last_sync`` is the last controller poll's own conclusion (``ok``,
        ``error``, ``result``, ``at``) so a Mac being refused an update can say why.
        """
        from .backend import release_delta
        trusted = app_release.trusted_publisher()
        release = app_release.load_release()
        # A cold measurement takes about 20 s - as long as a gateway's probe timeout - so the
        # probe answers null at once while one background measurement runs; the capability is
        # published only after the installed bytes are actually measured (never, if they
        # cannot be).
        measured = release_delta.baseline_ready()
        return {
            "release": release,
            "trusted_publisher": None if trusted is None else {
                "publisher_id": trusted.get("publisher_id"),
                "fingerprint": trusted.get("fingerprint"),
                "public_key": trusted.get("public_key"),
            },
            "delta": (None if not measured else {
                "schema": app_release.fm.SCHEMA,
                "manifest": app_release.held_manifest(release) is not None,
                # A release carrying `files_sha256` was pushed as files and left no disk
                # image anywhere in the chain - a node relays to a controller that holds
                # none - so "fall back to the image" is a guaranteed 404 on it. Anything
                # else was pushed as an image, which a node relays from its controller.
                "artifact": app_release.ARTIFACT_FILE.is_file()
                or "files_sha256" not in (release or {}),
            }),
            "last_sync": release_sync.last_sync_report(),
        }

    @app.post("/api/hub/release")
    def push_release(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return app_release.accept_release(body)
        except app_release.ReleaseRefused as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    @app.get("/api/hub/release/artifact")
    async def release_artifact(request: Request):
        descriptor = app_release.load_release()
        if descriptor is None:
            raise HTTPException(404, "no release is held")
        if app_release.ARTIFACT_FILE.is_file():
            return FileResponse(
                app_release.ARTIFACT_FILE,
                media_type="application/octet-stream",
                headers={"X-Content-SHA256": str(descriptor.get("sha256") or "")},
            )
        # Held as files, not as an image. A node relays its controller's image, when that holds
        # one, so the full install stays available as the fallback without a second copy here.
        target = release_sync.controller_target()
        if target is None:
            raise HTTPException(404, "this Mac holds the release as files, not as a disk image")
        base, headers = target
        upstream = {**headers, **({"Range": request.headers["range"]} if "range" in request.headers else {})}
        client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=5.0))
        try:
            answer = await client.send(
                client.build_request("GET", f"{base}/api/hub/release/artifact", headers=upstream),
                stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(502, f"the controller could not be reached for the disk image: {exc}") from exc
        if answer.status_code not in (200, 206):
            await answer.aclose()
            await client.aclose()
            raise HTTPException(404 if answer.status_code == 404 else 502,
                                "the controller holds no disk image for this release")

        async def close() -> None:
            await answer.aclose()
            await client.aclose()
        kept = ("content-length", "content-range", "accept-ranges", "x-content-sha256")
        return StreamingResponse(
            answer.aiter_raw(), status_code=answer.status_code, media_type="application/octet-stream",
            headers={name: answer.headers[name] for name in kept if name in answer.headers},
            background=BackgroundTask(close))

    def _held_manifest_or_404():
        held = app_release.held_manifest()
        if held is None:
            raise HTTPException(404, "no file manifest is held for the release")
        return held

    @app.get("/api/hub/release/files")
    def release_files() -> Response:
        """The manifest index: the groups, each with its sha256, and the digest the descriptor signs."""
        return Response(app_release.fm.index_bytes(_held_manifest_or_404()[0]),
                        media_type="application/json")

    @app.get("/api/hub/release/files/{group}")
    def release_files_group(group: str) -> FileResponse:
        _held_manifest_or_404()
        path = app_release.manifest_dir(app_release.load_release()["files_sha256"]) / f"{group}.tsv.gz"
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", group) or not path.is_file():
            raise HTTPException(404, "the release has no such manifest group")
        return FileResponse(path, media_type="application/gzip")

    @app.get("/api/hub/release/need")
    def release_need() -> dict[str, Any]:
        """The blobs a gateway still has to send for the held release (resumes an interrupted push)."""
        _held_manifest_or_404()
        return {"need": app_release.missing_blobs()}

    @app.get("/api/hub/release/blob/{sha256}")
    def release_blob(sha256: str) -> FileResponse:
        path = app_release.blob_file(sha256)
        if path is None:
            raise HTTPException(404, "this Mac holds no such blob for the release")
        return FileResponse(path, media_type="application/octet-stream")

    @app.put("/api/hub/release/blob/{sha256}")
    async def put_release_blob(sha256: str, request: Request) -> dict[str, Any]:
        # Everything that touches the disk or the 47,000-entry manifest runs off the event loop: a
        # controller taking a push must keep answering its app, its nodes and its gateway.
        try:
            upload = await run_in_threadpool(app_release.BlobUpload, sha256)
            try:
                async for chunk in request.stream():
                    await run_in_threadpool(upload.write, chunk)
                await run_in_threadpool(upload.commit)
            except BaseException:
                upload.abort()
                raise
        except app_release.ReleaseRefused as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        # No "remaining" count: recomputing it per blob was the slow part of a push, and the sender
        # asks `GET /release/need` once at the end (the contract's "complete when need is empty").
        return {"ok": True, "stored": sha256}

    @app.post("/api/hub/release/stage", status_code=202)
    def stage_release() -> dict[str, Any]:
        """Start building the held release as a copy of this app plus the changed files. Hub token
        only (the app on this Mac); progress is ``GET`` of the same path."""
        try:
            return release_sync.start_stage()
        except app_release.ReleaseRefused as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    @app.get("/api/hub/release/stage")
    def stage_release_status() -> dict[str, Any]:
        return release_sync.stage_status()

    @app.post("/api/hub/update/drain")
    async def update_drain_request(body: dict[str, Any]) -> dict[str, Any]:
        """The app on this Mac asks to stop taking new work for an update (``want: true``, renewed
        while it waits) or to resume (``false``). Hub token only. See ``update_drain``."""
        return await update_drain.request(bool(body.get("want")))

    @app.post("/api/maintenance/hold")
    def maintenance_hold(request: Request, timeout_s: float = 20, pause_s: float = 120) -> dict[str, Any]:
        """Update hold (``_orchestra/youtube-stack/update-hold/CONTRACT.md``, owner 2026-11-15):
        close the claim path first, wait up to ``timeout_s`` (ceiling 30) for stranding in-flight
        work to settle, and say whether a restart is safe now. Queued work stays queued and nothing
        is cancelled; the pause expires by itself after ``pause_s``, so an install that aborts can
        never leave this Hub refusing work. The reply is exactly the contract's four keys."""
        timeout_s = min(max(timeout_s, 0.0), 30.0)
        pause_s = min(max(pause_s, 1.0), 900.0)
        left = broker.hold(timeout_s, pause_s)
        global _installer_hold_until
        if auth.installer_hold_request(request):
            _installer_hold_until = time.monotonic() + pause_s
        return {"safe": not left, "unsettled": left, "dispatch_paused_s": pause_s,
                "in_flight": broker.in_flight()}

    @app.delete("/api/maintenance/hold")
    def maintenance_resume() -> dict[str, Any]:
        """Open the claim path at once. Never required — the pause expires by itself — and the
        reply keeps the contract's four keys: nothing is paused, so ``dispatch_paused_s`` is 0
        and `safe`/`unsettled` state what a restart would face right now."""
        global _installer_hold_until
        _installer_hold_until = 0.0
        broker.resume_dispatch()
        left = broker.unsettled_sends()
        return {"safe": not left, "unsettled": left, "dispatch_paused_s": 0,
                "in_flight": broker.in_flight()}

    @app.get("/api/hub/startup")
    def startup_state() -> dict[str, Any]:
        """What starts when this Mac starts, and the last things the fleet asked of it."""
        return {"startup": _startup_snapshot(), "audit": startup_control.recent_audit(20)}

    @app.post("/api/hub/startup/sync")
    def startup_sync(body: dict[str, Any]) -> dict[str, Any]:
        """The app on this Mac reports its settings and collects what the fleet asked for.

        The owner's route, and deliberately **not** in the fleet allowlist: it is how this Mac's own
        app talks to its own Hub, and a fleet credential must not be able to read or answer for it.
        """
        return startup_control.sync(body, role=str(control_plane.load_settings().get("role") or "standalone"))

    async def _startup_request(body: dict[str, Any], request: Request, *, actor: str) -> JSONResponse:
        try:
            result = await startup_control.request(
                str(body.get("action") or ""), body.get("value"), force=body.get("force", False),
                actor=actor, requested_by=body.get("requested_by"),
                studios=startup_control.studio_states(monitor))
        except startup_control.StartupRefused as refusal:
            return JSONResponse(refusal.body(), status_code=refusal.status)
        return JSONResponse(result)

    @app.post("/api/hub/startup/request")
    async def startup_request(body: dict[str, Any], request: Request) -> JSONResponse:
        """Change a startup setting or restart the services on this Mac.

        The Hub token (this Mac's owner) and the Fleet token (its controller, acting for the Gateway)
        may call it; a node's own credential may not - one Mac must not command another. The change
        is the app's to make, so this queues it and answers with what the app did. A restart is
        refused while work is running or waiting, unless ``force`` is true.
        """
        from .backend import auth as hub_auth
        kind = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        if kind == "node":
            raise HTTPException(403, "a node's own credential cannot change another Mac's startup")
        return await _startup_request(body, request, actor=kind)

    @app.post("/api/hub/fleet/nodes/{node_id}/startup")
    async def fleet_node_startup(node_id: str, body: dict[str, Any], request: Request) -> JSONResponse:
        """A controller passes a startup request to one of its nodes - or applies it to itself.

        ``node_id`` is a joined node's id, or this controller's own (or ``local``). Controller only:
        the Gateway reaches nodes through here with the Fleet token it already holds.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        kind = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        if kind == "node":
            raise HTTPException(403, "a node's own credential cannot change another Mac's startup")
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            return await _startup_request(body, request, actor=kind)
        if control_plane.load_settings().get("role") != "controller":
            raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
        try:
            return JSONResponse(await fleet.relay_startup(wanted, body, actor=kind))
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)

    @app.post("/api/hub/fleet/update-slot")
    def update_slot(body: dict[str, Any], request: Request) -> dict[str, Any]:
        """An older group member asks for an update slot. Every holder is granted - there is no
        shared slot any more, so this route exists only for those apps."""
        from .backend import auth as hub_auth
        holder = str(hub_auth._fleet_actor(request).get("node_id") or "fleet")
        return {"granted": update_drain.slot(holder, bool(body.get("want")))}

    @app.get("/api/hub/release/identity")
    def release_identity() -> dict[str, Any]:
        """The controller's publisher public key + fingerprint, for a node to pin."""
        return app_release.controller_identity()

    @app.post("/api/hub/release/trust")
    def trust_release_publisher(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return app_release.set_trusted_publisher(body)
        except app_release.ReleaseRefused as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    # ── machine memory policy ──────────────────────────────────────────────
    # One route pair for every Studio's idle-release choice, so the app never has to know
    # which worker is which. FleetMemoryControl reads each Studio through the shared
    # `GET/PUT /api/memory-policy` + `POST /api/memory/release` contract and reports
    # per-Studio residency back, including how much each Studio is holding right now.

    @app.get("/api/hub/memory/policy")
    async def memory_policy() -> dict[str, Any]:
        from .backend.memory_control import FleetMemoryControl
        return await FleetMemoryControl(monitor).inventory()

    @app.put("/api/hub/memory/policy")
    async def set_memory_policy(body: dict[str, Any]) -> dict[str, Any]:
        from .backend.memory_control import FleetMemoryControl
        try:
            return await FleetMemoryControl(monitor).set_mode(
                str(body.get("mode") or ""), body.get("studio_ids"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/hub/memory/admission")
    def get_memory_admission(model: str) -> dict[str, Any]:
        from .backend import memory_admission
        return memory_admission.describe(model, _admission_entry(model))

    @app.put("/api/hub/memory/admission")
    def set_memory_admission(body: dict[str, Any]) -> dict[str, Any]:
        """Set (or, with ``min_free_memory_gb: null``, clear) the operator's live-free-memory
        override for one model. Dispatch and the worker's own preflight both honour it."""
        from .backend import memory_admission
        model = str(body.get("model") or "").strip()
        if not model:
            raise HTTPException(400, "model is required")
        entry = _admission_entry(model)
        if body.get("min_free_memory_gb") is None:
            return memory_admission.reset_override(model, entry)
        try:
            free = float(body["min_free_memory_gb"])
            total = body.get("min_total_memory_gb")
            total = (memory_admission.requirement_gb(model, entry, use_override=False)[0] or 0.0
                     if total is None else float(total))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "memory values must be numbers") from exc
        if not (free >= 0 and total >= 0):
            raise HTTPException(400, "memory values must not be negative")
        return memory_admission.set_override(model, min_total_memory_gb=total,
                                             min_free_memory_gb=free, catalog_entry=entry)

    def _admission_entry(model: str) -> dict[str, Any]:
        for row in monitor.cached_aggregate_catalog().get("models") or []:
            if row.get("repo") == model:
                return row
        raise HTTPException(404, "unknown model")

    @app.post("/api/hub/memory/release")
    async def release_memory(body: dict[str, Any] | None = None) -> dict[str, Any]:
        from .backend.memory_control import FleetMemoryControl
        try:
            return await FleetMemoryControl(monitor).release((body or {}).get("studio_ids"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/model-packages/{package_id}/import")
    def import_model_package(package_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from components import model_packages
        try:
            catalog = _model_catalog()
            manifest = catalog[package_id]
            model_packages.import_folder(manifest, Path(body.get("source_folder", "")), _model_library())
            return _model_state(manifest, catalog, monitor)
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    # ── Real model removal (owner decision 2026-10-28) ──────────────────────────────────────────
    # The Models screen's Remove was a stub that said it was unavailable "until it can protect
    # models with active readers". It can now, and it moves rather than deletes:
    # `<library>/_trash/<package>-<stamp>/` with a receipt, refused while anything is using it.
    def _removal_refusal(exc: Exception) -> HTTPException:
        from .backend import model_removal
        return HTTPException(exc.status, {"ok": False, "code": exc.code, **exc.body})

    def _removal_state() -> tuple[Path, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        from components import model_packages
        catalog = _model_catalog()
        try:
            library = _model_library()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        records: dict[str, dict[str, Any]] = {}
        index = model_packages._index(model_packages._library(library))
        return library, catalog, index

    def _paused_for(manifest: dict[str, Any]) -> dict[str, Any] | None:
        try:
            worker_id = _worker_for_engine(str(manifest.get("engine") or ""))
        except ValueError:
            return None
        return broker.model_block_state(worker_id, manifest["model_id"])

    def _running_model_jobs() -> list[tuple[str, str]]:
        """Every job this queue is running, as (studio, **model name**), so removal can match it.

        `broker.local_running_jobs()` answers (studio, worker job id), and a job id is not a model
        name; the batch record carries the model beside its items. The two are joined here rather
        than teaching the removal module to guess: a removal that cannot tell whether a model is in
        use must refuse, and the other direction unloads 9 GB under a running generation.
        """
        models_by_studio: dict[str, str] = {}
        for batch in list(broker.batches.values()):
            model = str(batch.get("model") or batch.get("model_id") or "")
            for item in batch.get("items") or []:
                studio = str(item.get("studio") or "")
                if studio and model:
                    models_by_studio.setdefault(studio, model)
        return [(studio, models_by_studio.get(studio, "")) for studio, _job in broker.local_running_jobs()
                if models_by_studio.get(studio)]

    @app.delete("/api/hub/model-packages/{package_id}")
    def remove_model_package(package_id: str) -> dict[str, Any]:
        """Move one installed model out of the library into its own `_trash`. Never a delete.

        Refused while anything is reading it: a running job on the model, a paused model whose
        Studio still holds its weights, or another installed package that requires it. The refusal
        names which, because "the model is in use" is the kind of message that gets clicked again.
        """
        from .backend import model_removal
        library, catalog, index = _removal_state()
        manifest = catalog.get(package_id)
        if manifest is None or manifest.get(_MANIFEST_ERROR):
            raise HTTPException(404, "unknown model package")
        refusal = None
        try:
            owned = _library_may_be_moved_from("removing a model")
            if not owned:
                from components import library_ownership
                refusal = library_ownership.refusal(library, ledger.DATA_DIR, "removing a model")
            return model_removal.remove(
                library=library, manifest=manifest, catalog=catalog, records=index,
                may_move=owned, refusal=refusal, running_jobs=_running_model_jobs(),
                paused=_paused_for(manifest))
        except model_removal.RemoveRefused as exc:
            raise _removal_refusal(exc) from exc

    # ── The install screen from the Gateway (owner decision 2026-10-28) ────────────────────────
    # The same answers the Mac's own screen reads, and the same write the Mac's own screen makes.
    # A joined node's answer is relayed to the node, so the Gateway never computes a projection of
    # its own: a preset's count is the count that Mac's Hub built from its own receipts.
    @app.get("/api/hub/fleet/nodes/{node_id}/model-presets")
    async def fleet_node_model_presets(node_id: str) -> JSONResponse:
        """One Mac's install screen, from the Gateway: relayed to a node, answered locally here."""
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            try:
                return JSONResponse(await fleet.relay_model(wanted, "GET", "/model-presets"))
            except fleet.StartupRelayError as error:
                # An offline Mac is a reported state: the Fleet page shows it and keeps the rest.
                return JSONResponse(error.body, status_code=error.status)
        # `model_presets_screen` is a synchronous read (manifests, receipts, the disk): running it
        # directly on a coroutine would hold the event loop while it works, so every other route -
        # including `/api/health` - waits behind it. A threadpool keeps this route off the loop.
        return JSONResponse({**await run_in_threadpool(model_presets_screen),
                             "node": wanted, "hub_version": _app_version()})

    @app.post("/api/hub/fleet/nodes/{node_id}/model-preset")
    async def fleet_node_set_model_preset(node_id: str, request: Request) -> JSONResponse:
        """Set one Mac's preset from the Gateway. Body `{"preset_id": str, "requested_by": str}`.

        `requested_by` is recorded on the Mac beside the choice, the same way the unqualified-render
        grant records who asked: a preset decides what a Mac installs and what its one-time cleanup
        moves. An unknown preset id is refused by name - never swapped for another.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "code": "BAD_BODY", "detail":
                                 "The body must be a JSON object."}, status_code=400)
        requested_by = str(body.get("requested_by") or "").strip()
        if not requested_by:
            return JSONResponse({"ok": False, "code": "APPROVER_REQUIRED", "detail":
                                 "Name who set this preset."}, status_code=400)
        preset_id = str(body.get("preset_id") or "").strip()
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            startup_control.audit("model_preset_relay", {"node": wanted, "preset": preset_id,
                                                         "actor": actor, "requested_by": requested_by})
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, "POST", "/model-preset",
                    payload={"preset_id": preset_id, "requested_by": requested_by},
                    feature=f"setting a model preset from the Gateway (Studio Hub {_app_version()})"))
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
        from components import model_presets
        try:
            saved = standalone_config.set_preset_choice(preset_id or None)
        except model_presets.PresetError as exc:
            startup_control.audit("model_preset", {"node": wanted, "preset": preset_id,
                                                   "actor": actor, "refused": str(exc)})
            return JSONResponse({"ok": False, "code": "UNKNOWN_PRESET", "detail": str(exc)},
                                status_code=409)
        startup_control.audit("model_preset", {"node": wanted, "preset": saved, "actor": actor,
                                               "requested_by": requested_by})
        return JSONResponse({"ok": True, "node": wanted, "preset_id": saved,
                             "requested_by": requested_by, "hub_version": _app_version()})

    # ── The one-time preset cleanup (owner decision 2026-10-28) ────────────────────────────────
    # A Mac set up before the install screen exists carries whatever the kit copied. This reports
    # what is outside its preset, and - only when told to - moves those packages into the library's
    # trash. Dry run first, per Mac, and an apply that records that it ran.
    PRESET_CLEANUP = "0.6.205"

    def _this_machine_id() -> str:
        """This Mac's fleet key, lower-cased: the one name the cleanup state is filed under."""
        from .backend import fleet_nodes as fleet
        return str(fleet.this_machine()["id"]).lower()

    def _preset_cleanup_inputs() -> dict[str, Any]:
        """Everything a cleanup or its dry run needs, resolved once.

        The preset is the owner's recorded choice for this Mac, else the starter for its memory;
        the "keep" set is the models this Mac's owner has said must stay. Both come from the Hub's
        own state, never from what the library happens to hold - a target derived from the diff
        would move depending on itself.
        """
        from components import model_presets
        import psutil
        library, catalog, index = _removal_state()
        memory_gb = round(psutil.virtual_memory().total / (1024 ** 3))
        chosen = standalone_config.preset_choice()
        keep = standalone_config.preset_cleanup_keep()
        paused = {}
        for package_id, manifest in catalog.items():
            if manifest.get(_MANIFEST_ERROR):
                continue
            state = _paused_for(manifest)
            if state:
                paused[package_id] = state
        return {"library": library, "catalog": catalog, "index": index, "memory_gb": memory_gb,
                "preset_id": preset_cleanup_preset(memory_gb, chosen, catalog),
                "keep": set(keep) if isinstance(keep, list) else set(),
                "running_jobs": _running_model_jobs(), "paused": paused,
                "chosen": chosen}

    def preset_cleanup_preset(memory_gb: float | None, chosen: str | None,
                              catalog: dict[str, dict[str, Any]]) -> str | None:
        from .backend import preset_cleanup
        try:
            return preset_cleanup.preset_for(memory_gb, chosen)
        except Exception:  # noqa: BLE001 - a preset file that cannot be read is reported, not a 500
            return None

    @app.get("/api/hub/preset-cleanup")
    def preset_cleanup_report() -> dict[str, Any]:
        """The **dry run**: what this Mac's one-time cleanup would move, and what it would keep.

        Moves nothing and writes nothing. `ran` says whether it has already run here, so the
        screen can show the report of a Mac it has already tidied rather than offering it again.
        """
        from .backend import preset_cleanup
        inputs = _preset_cleanup_inputs()
        machine = _this_machine_id()
        already = preset_cleanup.has_run(ledger.DATA_DIR, machine)
        try:
            report = preset_cleanup.plan(
                library=inputs["library"], manifests=inputs["catalog"], catalog=inputs["catalog"],
                records=inputs["index"], preset_id=inputs["preset_id"],
                memory_gb=inputs["memory_gb"], running_jobs=inputs["running_jobs"],
                paused=inputs["paused"], keep=inputs["keep"])
        except preset_cleanup.CleanupRefused as exc:
            raise HTTPException(exc.status, exc.body) from exc
        return {**report, "machine": machine, "already_ran": already,
                "chosen_preset": inputs["chosen"], "keep": sorted(inputs["keep"]),
                "hub_version": _app_version()}

    @app.post("/api/hub/preset-cleanup")
    def preset_cleanup_apply(body: dict[str, Any]) -> dict[str, Any]:
        """Run it once. Body `{"approved_by": str, "keep"?: [ids], "preset_id"?: str}`.

        `approved_by` is required and recorded: the owner approves a report, and the record says
        who did. A second call is a no-op that says when it already ran.
        """
        from .backend import preset_cleanup
        approved_by = str(body.get("approved_by") or "").strip()
        if not approved_by:
            raise HTTPException(400, "Name who approved this cleanup.")
        inputs = _preset_cleanup_inputs()
        keep = body.get("keep")
        if keep is not None:
            if not isinstance(keep, list) or not all(isinstance(item, str) for item in keep):
                raise HTTPException(400, "keep must be a list of package ids.")
            inputs["keep"] = set(keep)
        preset_id = str(body.get("preset_id") or inputs["preset_id"] or "")
        owned = _library_may_be_moved_from("cleaning up this Mac's models")
        try:
            return preset_cleanup.apply(
                data_dir=ledger.DATA_DIR, machine=_this_machine_id(), library=inputs["library"],
                manifests=inputs["catalog"], catalog=inputs["catalog"], records=inputs["index"],
                may_move=owned, preset_id=preset_id, memory_gb=inputs["memory_gb"],
                approved_by=approved_by, running_jobs=inputs["running_jobs"],
                paused=inputs["paused"], keep=inputs["keep"])
        except preset_cleanup.CleanupRefused as exc:
            raise HTTPException(exc.status, exc.body) from exc

    @app.get("/api/hub/fleet/nodes/{node_id}/preset-cleanup")
    async def fleet_preset_cleanup_report(node_id: str, request: Request) -> JSONResponse:
        """One Mac's dry run, from the Gateway: relayed to that Mac, or answered for this one."""
        return await _fleet_preset_cleanup(node_id, request, None)

    @app.post("/api/hub/fleet/nodes/{node_id}/preset-cleanup")
    async def fleet_preset_cleanup_apply(node_id: str, request: Request) -> JSONResponse:
        """Run one Mac's cleanup, from the Gateway. An offline Mac is reported, not failed."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not str(body.get("approved_by") or "").strip():
            return JSONResponse({"ok": False, "code": "APPROVAL_REQUIRED", "detail":
                                 "Name who approved this cleanup."}, status_code=400)
        return await _fleet_preset_cleanup(node_id, request, body)

    async def _fleet_preset_cleanup(node_id: str, request: Request,
                                    body: dict[str, Any] | None) -> JSONResponse:
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            if body is not None:
                startup_control.audit("preset_cleanup_relay", {"node": wanted, "actor": actor,
                                                               "approved_by": body.get("approved_by")})
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, "GET" if body is None else "POST", "/preset-cleanup",
                    payload=body if body is not None else None))
            except fleet.StartupRelayError as error:
                # An offline Mac is a reported state, not a failure of the owner's request: the
                # cleanup is a tidy-up, and a Mac that is asleep has nothing to lose by waiting.
                return JSONResponse(error.body, status_code=error.status)
        try:
            if body is None:
                answer = await run_in_threadpool(preset_cleanup_report)
            else:
                answer = await run_in_threadpool(preset_cleanup_apply, body)
        except HTTPException as exc:
            return JSONResponse({"ok": False, "detail": exc.detail}, status_code=exc.status_code)
        return JSONResponse({**answer, "node": wanted, "hub_version": _app_version()})

    @app.get("/api/hub/model-removals")
    def list_model_removals() -> dict[str, Any]:
        """What is in the library's `_trash`, newest first: the restore-within-30-days surface."""
        from .backend import model_removal
        try:
            library = _model_library()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"removed": model_removal.listing(library),
                "restore_days": model_removal.REMOVED_MODEL_DAYS}

    @app.post("/api/hub/model-removals/restore")
    def restore_model_removal(body: dict[str, Any]) -> dict[str, Any]:
        """Body `{"folder": "<name in _trash>", "package_id"?: str}`: put it back in the library."""
        from .backend import model_removal
        try:
            library = _model_library()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        name = str(body.get("folder") or "").strip()
        if not name or Path(name).name != name:
            raise HTTPException(400, "Name the folder inside the library's _trash.")
        try:
            return model_removal.restore(library, library / "_trash" / name,
                                         body.get("package_id"))
        except model_removal.RemoveRefused as exc:
            raise _removal_refusal(exc) from exc

    @app.post("/api/hub/model-packages/drive-check")
    def check_model_drives(body: dict[str, Any]) -> Any:
        """Read each mounted KH-STUDIO kit drive once, bounded, and cache what it holds.

        The only route that stats inside a removable volume besides a drive copy. A refusal is
        `409 NEEDS_PERMISSION` - never an empty `has` list, which would read as "does not have".
        """
        from .backend import kit_drives
        try:
            catalog = _model_catalog()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        try:
            return kit_drives.check(catalog)
        except kit_drives.DriveRefused as exc:
            return JSONResponse(status_code=409, content={"code": exc.code, "detail": exc.detail})

    @app.post("/api/hub/model-packages/{package_id}/download")
    def download_model_package(package_id: str, body: dict[str, Any]) -> Any:
        from . import model_downloads
        from .backend import kit_drives
        try:
            catalog = _model_catalog()
            manifest = catalog[package_id]
            sources = body.get("sources")
            if sources is not None and not isinstance(sources, dict):
                raise ValueError("sources must be a map of model paths to HTTPS mirrors")
            peer_only = body.get("peer_only", False)
            if not isinstance(peer_only, bool):
                raise ValueError("peer_only must be true or false")
            drive_library = None
            drive_name = str(body.get("from_drive") or "").strip()
            if drive_name:
                drive = kit_drives.resolve(drive_name)
                if not kit_drives.has_package(drive, manifest):
                    raise kit_drives.DriveRefused(
                        "DRIVE_LACKS_PACKAGE", f"{drive_name} does not have {package_id}.")
                drive_library = kit_drives.drive_library(drive)
            return model_downloads.start(package_id=package_id, manifest=manifest,
                                         catalog=catalog, library=_model_library(), sources=sources,
                                         peer=None if drive_library else _peer_source(
                                             body.get("source"), manifest, peer_only),
                                         peer_only=peer_only, drive=drive_library)
        except kit_drives.DriveRefused as exc:
            return JSONResponse(status_code=409, content={"code": exc.code, "detail": exc.detail})
        except model_downloads.InsufficientDiskSpace as exc:
            # 507, like the output-storage refusal: the disk is the reason, not the request.
            raise HTTPException(507, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    def _peer_source(source: Any, manifest: dict[str, Any], peer_only: bool = False) -> str | None:
        """The LAN share another Mac made of this package (its share route's `share` object), as
        the URL the downloader tries first. A share of a different revision is not used: the
        download then comes from the internet, as it would with no share at all."""
        from components import model_packages
        if source is None:
            if peer_only:
                raise ValueError("LAN-only copy requires another Mac's model share")
            return None
        if not isinstance(source, dict) or not isinstance(source.get("url_base"), str):
            raise ValueError("source must be another Mac's model share: {url_base, token, files, revision}")
        if source.get("revision") not in (None, manifest.get("revision")):
            if peer_only:
                raise ValueError("LAN-only copy source revision does not match this model package")
            return None
        if peer_only and source.get("revision") != manifest.get("revision"):
            raise ValueError("LAN-only copy source must name this model package revision")
        return model_packages.peer_base(source["url_base"])

    _downloads_shown: set[str] = set()

    async def _show_finished_download(run: dict[str, Any] | None, machine: str) -> None:
        """Before a finished download is answered `done`, re-read the catalogs of the Mac it ran on.

        The worker sees the new package at once, but this Hub re-reads worker catalogs only once a
        minute, so the poller that hears `done` and reloads (the Gateway's Fleet page, the Model
        library) read the model as absent and the scheduler would not send it work until the next
        cycle. Once per run: a later status read of the same run is answered from the cache.
        """
        if not isinstance(run, dict) or run.get("state") != "done" or run.get("id") in _downloads_shown:
            return
        _downloads_shown.add(str(run.get("id")))
        await monitor.refresh_machine_catalogs(machine)

    @app.get("/api/hub/model-downloads")
    def list_model_downloads() -> dict[str, Any]:
        from . import model_downloads
        return {"runs": model_downloads.list_runs()}

    @app.get("/api/hub/model-downloads/{run_id}")
    async def model_download_status(run_id: str) -> dict[str, Any]:
        from . import model_downloads
        run = model_downloads.status(run_id)
        if run is None:
            raise HTTPException(404, "unknown model download")
        await _show_finished_download(run, "local")
        return run

    @app.delete("/api/hub/model-downloads/{run_id}")
    def cancel_model_download(run_id: str) -> dict[str, Any]:
        from . import model_downloads
        run = model_downloads.cancel(run_id)
        if run is None:
            raise HTTPException(404, "unknown model download")
        return run

    _MODEL_REFUSAL_CODES = {404: "UNKNOWN_PACKAGE", 507: "NOT_ENOUGH_DISK"}

    @app.post("/api/hub/fleet/nodes/{node_id}/model-packages/{package_id}/download")
    async def fleet_node_model_download(node_id: str, package_id: str, request: Request) -> JSONResponse:
        """Install a model on one Mac in this group from the Gateway: a joined node (relayed to the
        node's own copy of this route, ``local``) or this controller itself (its id or ``local``).

        The same download as the Model library's, with the same refusals (unknown package, full
        disk) plus one the local button does not need: a model this Mac has too little
        memory to run. Audited here and, for a node, again on the node.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        # Optional source share plus the strict-LAN protocol bit (0.6.174).
        raw_body = await request.body()
        try:
            body = await request.json() if raw_body else {}
        except ValueError:
            return JSONResponse({"ok": False, "code": "REFUSED", "detail":
                                 "The download request body must be a JSON object."}, status_code=409)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "code": "REFUSED", "detail":
                                 "The download request body must be a JSON object."}, status_code=409)
        source = body.get("source")
        peer_only = body.get("peer_only", False)
        if not isinstance(peer_only, bool):
            return JSONResponse({"ok": False, "code": "REFUSED", "detail":
                                 "peer_only must be true or false"}, status_code=409)
        if peer_only and source is None:
            return JSONResponse({"ok": False, "code": "REFUSED", "detail":
                                 "LAN-only copy requires another Mac's model share"}, status_code=409)
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            startup_control.audit("model_download_relay", {"node": wanted, "package": package_id,
                                                           "actor": actor, "lan": source is not None})
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, "POST", f"/model-packages/{package_id}/download",
                    payload={"source": source, "peer_only": peer_only} if source is not None else None))
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
        try:
            manifest = _model_catalog().get(package_id)
            if manifest is None or manifest.get(_MANIFEST_ERROR):
                raise HTTPException(404, "unknown model package")
            _memory_refusal(manifest)
            run = download_model_package(package_id, {"source": source, "peer_only": peer_only})
        except (OSError, ValueError) as exc:
            run, refusal = None, HTTPException(409, str(exc))
        except HTTPException as exc:
            run, refusal = None, exc
        startup_control.audit("model_download", {
            "node": wanted, "package": package_id, "actor": actor,
            **({"run": run.get("id")} if run else {"refused": refusal.detail})})
        if run is None:
            return JSONResponse({"ok": False, "code": _MODEL_REFUSAL_CODES.get(refusal.status_code, "REFUSED"),
                                 "detail": refusal.detail}, status_code=refusal.status_code)
        return JSONResponse({"ok": True, "node": wanted, "run": run})

    # **The Trash Folder from the Gateway (owner decision 4g, 2026-10-16).** List it, move items to macOS
    # Trash, or delete them permanently - on a joined node (relayed to its own `local` copy of the route)
    # or on this Mac (its id or `local`). `output_storage` holds the path-safety and confirmation rules.
    TRASH_HUB = "0.6.182"

    async def _fleet_trash(node_id: str, request: Request, tail: str, run) -> JSONResponse:
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        body: Any = {}
        if request.method == "POST":
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "code": "BAD_BODY",
                                     "detail": "The request body must be a JSON object."}, status_code=400)
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            if request.method == "POST":
                startup_control.audit("trash_relay", {"node": wanted, "action": tail, "actor": actor})
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, request.method, f"/trash{tail}", payload=body if request.method == "POST" else None,
                    timeout=httpx.Timeout(900.0, connect=4.0),
                    feature=f"managing its Trash Folder from the Gateway (Studio Hub {TRASH_HUB})"))
            except fleet.StartupRelayError as error:
                if error.body.get("code") == "NODE_REFUSED" or error.status in (404, 405):
                    return JSONResponse({"ok": False, "code": "HUB_TOO_OLD", "needs_hub": TRASH_HUB,
                                         "detail": f"That Mac needs Studio Hub {TRASH_HUB} to manage its Trash "
                                                   "Folder from the Gateway (or it holds a different Fleet token)."},
                                        status_code=409)
                return JSONResponse(error.body, status_code=error.status)
        try:
            result = await run_in_threadpool(run, body)
        except output_storage.TrashRefused as refusal:
            if request.method == "POST":
                startup_control.audit("trash", {"action": tail, "actor": actor, "refused": refusal.body["code"]})
            return JSONResponse(refusal.body, status_code=refusal.status)
        if request.method == "POST":
            startup_control.audit("trash", {"action": tail, "actor": actor, "ok": result.get("ok"),
                                            "done": result.get("moved_count", result.get("deleted_count")),
                                            "freed": result.get("freed_bytes"), "failed": len(result["failed"])})
        return JSONResponse({"ok": True, **result, "node": wanted, "hub_version": _app_version()})

    @app.get("/api/hub/fleet/nodes/{node_id}/trash")
    async def fleet_node_trash(node_id: str, request: Request) -> JSONResponse:
        """This Mac's Trash Folder: items with physical bytes, kind and age; totals; disk free."""
        return await _fleet_trash(node_id, request, "", lambda _body: output_storage.trash_listing())

    @app.post("/api/hub/fleet/nodes/{node_id}/trash/move-to-macos-trash")
    async def fleet_node_trash_to_macos(node_id: str, request: Request) -> JSONResponse:
        """Body `{"items": "all" | [ids]}`: move them to macOS Trash (the owner empties that)."""
        return await _fleet_trash(node_id, request, "/move-to-macos-trash",
                                  lambda body: output_storage.trash_to_macos(body.get("items")))

    @app.post("/api/hub/fleet/nodes/{node_id}/trash/delete")
    async def fleet_node_trash_delete(node_id: str, request: Request) -> JSONResponse:
        """Body `{"items": "all" | [ids], "confirm": true, "expected_count", "expected_bytes"}`: delete permanently.
        Re-measured here; a mismatch is 409 with nothing deleted. Never called by the Hub itself."""
        return await _fleet_trash(node_id, request, "/delete", lambda body: output_storage.trash_delete(
            items=body.get("items"), confirm=body.get("confirm"),
            expected_count=body.get("expected_count"), expected_bytes=body.get("expected_bytes")))

    # **Empty all trash (owner, 2026-11-16, Studio Hub 0.6.243).** The Hub Trash Folder and the whole
    # macOS Trash on a Mac, for good: the one delete an agent may ask for with the fleet credential. Each
    # Mac answers `machines: [{machine, label, state: emptied|skipped|failed, code, reason, freed_bytes,
    # deleted_count, left_count, left_bytes, locations}]`; a Mac whose "Allow remote empty" is off is
    # skipped with its reason (409 REMOTE_EMPTY_OFF on its own route). One audit line per run.
    EMPTY_TRASH_HUB = "0.6.243"

    def _this_mac() -> tuple[str, str]:
        from .backend import fleet_nodes as fleet
        me = fleet.this_machine()
        return me["id"], me["name"]

    def _empty_row(machine: str, label: str, state: str, *, code: str | None = None, reason: str | None = None,
                   result: dict | None = None) -> dict[str, Any]:
        result = result or {}
        return {"machine": machine, "label": label, "state": state, "code": code, "reason": reason,
                **{key: result.get(key) for key in ("freed_bytes", "deleted_count", "left_count", "left_bytes")},
                "locations": result.get("locations"),
                # Whether this Mac may be emptied from elsewhere, who decided, and the rule behind it
                # (owner, 2026-11-16): so a skipped Mac reads "no generation work in 7 days", not a bare off.
                "remote_empty": output_storage.remote_empty_decision()}

    def _empty_here(body: dict[str, Any], *, remote: bool) -> tuple[int, dict[str, Any]]:
        """Empty this Mac (status, row). `remote` applies its "Allow remote empty" rule, including a
        workstation Mac someone is using, which is never emptied from elsewhere."""
        machine, label = _this_mac()
        refusal = output_storage.remote_empty_refusal() if remote else None
        if refusal is not None:
            return 409, _empty_row(machine, label, "skipped", code=refusal[0], reason=refusal[1])
        try:
            result = output_storage.empty_all(expected_count=body.get("expected_count"),
                                              expected_bytes=body.get("expected_bytes"))
        except output_storage.TrashRefused as refusal:
            # Already emptying is a skip: the Mac is doing exactly what this run asked for.
            state = "skipped" if refusal.body["code"] == "EMPTY_RUNNING" else "failed"
            return refusal.status, _empty_row(machine, label, state, code=refusal.body["code"],
                                              reason=refusal.body["detail"]) | {
                key: refusal.body[key] for key in ("count", "bytes") if key in refusal.body}
        return 200, _empty_row(machine, label, result["state"],
                               code=result["code"], reason=result["detail"], result=result)

    def _empty_envelope(rows: list[dict[str, Any]], status: int = 200, **extra) -> JSONResponse:
        ok = all(row["state"] != "failed" for row in rows)
        first = next((row for row in rows if row["state"] != "emptied"), None)
        body = {"ok": ok and status == 200, "machines": rows, "hub_version": _app_version(), **extra}
        if status != 200 and first:
            body |= {"code": first["code"], "detail": first["reason"]}
        return JSONResponse(body, status_code=status)

    def _relayed_rows(node_id: str, label: str, answer: dict[str, Any] | None,
                      error: "Exception | None") -> list[dict[str, Any]]:
        """A node's answer (or its relay error) as its rows; an offline or older Mac is skipped, with why."""
        from .backend import fleet_nodes as fleet
        if isinstance(error, fleet.StartupRelayError):
            answer, code = error.body, str(error.body.get("code") or "")
            if isinstance(answer.get("machines"), list) and answer["machines"]:
                return answer["machines"]
            if answer.get("queued"):
                return [_empty_row(node_id, label, "skipped", code="QUEUED", reason=str(answer.get("detail")))]
            if code == "NODE_REFUSED" or error.status in (404, 405):
                return [_empty_row(node_id, label, "skipped", code="HUB_TOO_OLD",
                                   reason=f"That Mac needs Studio Hub {EMPTY_TRASH_HUB} to empty its trash "
                                          "from the Gateway (or it holds a different Fleet token).")]
            if code in ("NODE_UNREACHABLE", "UNKNOWN_NODE"):
                return [_empty_row(node_id, label, "skipped", code=code, reason=str(answer.get("detail")))]
            return [_empty_row(node_id, label, "failed", code=code or "RELAY_FAILED",
                               reason=str(answer.get("detail") or f"that Mac answered HTTP {error.status}"))]
        if error is not None:
            return [_empty_row(node_id, label, "failed", code="RELAY_FAILED", reason=str(error)[:300])]
        rows = (answer or {}).get("machines")
        return rows if isinstance(rows, list) and rows else [
            _empty_row(node_id, label, "skipped", code="NO_ANSWER", reason="that Mac answered without naming itself")]

    async def _relay_empty(node_id: str, label: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        from .backend import fleet_nodes as fleet
        try:
            answer = await fleet.relay_model(
                node_id, "POST", "/empty-trash", payload=body, timeout=httpx.Timeout(900.0, connect=4.0),
                feature=f"emptying its trash from the Gateway (Studio Hub {EMPTY_TRASH_HUB})")
        except Exception as error:   # noqa: BLE001 - every Mac ends with a row, never a lost run
            return _relayed_rows(node_id, label, None, error)
        return _relayed_rows(node_id, label, answer, None)

    async def _empty_body(request: Request) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except ValueError:
            body = {} if not await request.body() else None
        return body if isinstance(body, dict) else None

    def _audit_empty(actor: str, scope: str, rows: list[dict[str, Any]]) -> None:
        startup_control.audit("trash_empty", {
            "actor": actor, "scope": scope, "machines": len(rows),
            **{state: sum(1 for row in rows if row["state"] == state) for state in ("emptied", "skipped", "failed")},
            "freed_bytes": sum(row.get("freed_bytes") or 0 for row in rows),
            "deleted_count": sum(row.get("deleted_count") or 0 for row in rows)})

    async def _attention_answer(fresh: bool) -> JSONResponse:
        """This Mac's report: the last measurement, or a bounded fresh one (`?now=1`)."""
        from . import attention
        if not fresh:
            return JSONResponse(attention.cached())
        try:
            result = await _asyncio.wait_for(
                run_in_threadpool(attention.refresh, activity_state=_recent_job_state()), timeout=3.0)
        except _asyncio.TimeoutError:
            return JSONResponse({**attention.cached(), "state": "unknown", "source": "timeout",
                                 "reasons": [{"code": "MEASURE_TIMEOUT",
                                              "detail": "This Mac did not answer within 3 s."}]})
        return JSONResponse(result)

    @app.get("/api/hub/fleet/nodes/{node_id}/attention")
    async def fleet_node_attention(node_id: str, request: Request) -> JSONResponse:
        """One Mac's attention report; `?now=1` re-measures that Mac only.

        `local` (or this Mac's id) answers here; a joined node is relayed through its controller, the
        same way its empty-trash route is, so the Fleet page can refresh a single row without holding
        the node's own address (Gateway - Lead - 001, 2026-11-17).
        """
        from .backend import fleet_nodes as fleet
        fresh = request.query_params.get("now") in ("1", "true", "yes")
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            return await _attention_answer(fresh)
        if control_plane.load_settings().get("role") != "controller":
            raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
        try:
            answer = await fleet.relay_model(wanted, "GET", "/attention?now=1" if fresh else "/attention")
        except fleet.StartupRelayError as error:
            return JSONResponse({"state": "unknown",
                                 "reasons": [{"code": "LOCATION_UNREACHABLE",
                                              "detail": f"{wanted} could not be reached through its controller."}],
                                 "source": "unreachable",
                                 "measured_at": None, "checked_in_at": None,
                                 "interval_s": None, "relayed": {"status": error.status,
                                                                 **(error.body or {})}},
                                status_code=502)
        payload = (answer or {}).get("attention_report") or answer or {}
        return JSONResponse({**payload, "relayed": {"node": wanted}})

    @app.post("/api/hub/fleet/nodes/{node_id}/empty-trash")
    async def fleet_node_empty_trash(node_id: str, request: Request) -> JSONResponse:
        """Empty one Mac's trash (this one as `local` or its id, or a joined node through its controller)."""
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        body = await _empty_body(request)
        if body is None:
            return JSONResponse({"ok": False, "code": "BAD_BODY",
                                 "detail": "The request body must be a JSON object."}, status_code=400)
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            status, row = await run_in_threadpool(_empty_here, body, remote=True)
            rows = [row]
        else:
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            node = fleet._read().get(wanted) or {}
            rows = await _relay_empty(wanted, str(node.get("name") or node.get("label") or wanted), body)
            status = 200
        _audit_empty(actor, wanted, rows)
        return _empty_envelope(rows, status)

    @app.post("/api/hub/fleet/empty-trash")
    async def fleet_empty_trash(request: Request) -> JSONResponse:
        """Empty this Mac and, on a controller, every joined Mac - one row per Mac, each terminal."""
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        body = await _empty_body(request)
        if body is None:
            return JSONResponse({"ok": False, "code": "BAD_BODY",
                                 "detail": "The request body must be a JSON object."}, status_code=400)
        # Expected figures belong to one Mac's window; across Macs each one measures for itself.
        _status, here = await run_in_threadpool(_empty_here, {}, remote=True)
        rows = [here]
        if control_plane.load_settings().get("role") == "controller":
            nodes = fleet._read()
            answers = await asyncio.gather(*(
                _relay_empty(node_id, str(node.get("name") or node.get("label") or node_id), {})
                for node_id, node in nodes.items()))
            rows += [row for answer in answers for row in answer]
        _audit_empty(actor, "all", rows)
        return _empty_envelope(rows)

    # **macOS's own Trash setting (owner request 2026-10-19).** Finder > Settings > Advanced >
    # "Remove items from the Trash after 30 days" is `com.apple.finder FXRemoveOldTrashItems`. Every
    # retirement this Hub makes ends in macOS Trash, so a Mac without it fills its disk until someone
    # empties it by hand - and the owner could not tell which of ~53 Macs had it. **One way only**:
    # nothing here turns it off, and no route reads a body. A node is reached the same way the Trash
    # Folder routes reach it (its controller, on the fleet credential, with an audit line), and the
    # app's own button calls the same code with `local`, so there is one implementation.
    MACOS_TRASH_HUB = "0.6.187"
    MACOS_TRASH_TOO_OLD = (f"That Mac needs Studio Hub {MACOS_TRASH_HUB} to turn on macOS's Trash "
                           "setting from the Gateway.")

    async def _enable_macos_trash(node_id: str, request: Request) -> JSONResponse:
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            startup_control.audit("macos_trash_relay", {"node": wanted, "actor": actor})
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, "POST", "/macos-trash-auto-empty/enable", payload={},
                    timeout=httpx.Timeout(60.0, connect=4.0),
                    feature=f"turning on macOS's Trash setting from the Gateway (Studio Hub {MACOS_TRASH_HUB})"))
            except fleet.StartupRelayError as error:
                if error.body.get("code") == "NODE_REFUSED" or error.status in (404, 405):
                    return JSONResponse({"ok": False, "code": "HUB_TOO_OLD", "needs_hub": MACOS_TRASH_HUB,
                                         "detail": MACOS_TRASH_TOO_OLD}, status_code=409)
                return JSONResponse(error.body, status_code=error.status)
        try:
            result = await run_in_threadpool(output_storage.enable_finder_trash_auto_empty)
        except output_storage.TrashRefused as refusal:
            startup_control.audit("macos_trash", {"actor": actor, "refused": refusal.body["code"]})
            return JSONResponse(refusal.body, status_code=refusal.status)
        startup_control.audit("macos_trash", {"actor": actor, "ok": result.get("ok")})
        # `state: applied` is the vocabulary the startup requests already use, and it is true here: the
        # value was read back. The bulk run on the Gateway reads it, so one Mac's answer and all Macs'
        # answers are the same shape.
        return JSONResponse({**result, "state": "applied", "node": wanted, "hub_version": _app_version()})

    @app.post("/api/hub/fleet/nodes/{node_id}/macos-trash-auto-empty/enable")
    async def fleet_node_enable_macos_trash(node_id: str, request: Request) -> JSONResponse:
        """Turn on this Mac's Finder Trash preference (or, relayed, a node's). Never turns it off."""
        return await _enable_macos_trash(node_id, request)

    @app.post("/api/hub/macos-trash-auto-empty/enable")
    async def hub_enable_macos_trash(request: Request) -> JSONResponse:
        """The same for this Mac, called by its own app with the Hub token. Never turns it off."""
        return await _enable_macos_trash("local", request)

    # **This Mac's Trash Folder in the app (owner request 2026-10-15).** The same unified root the
    # Gateway manages: list it (with its per-root detail) and move its contents to macOS Trash. The
    # app never deletes permanently; the owner empties macOS Trash himself.
    @app.get("/api/hub/attention")
    async def hub_attention(request: Request) -> JSONResponse:
        """This Mac's attention state, with the time it was measured.

        `?now=1` re-measures for a caller that needs the current answer, bounded in time; without it
        the answer is the last measurement and says `source: "cached"` beside its own `measured_at`.
        """
        return await _attention_answer(request.query_params.get("now") in ("1", "true", "yes"))

    @app.get("/api/hub/trash")
    async def hub_trash(request: Request) -> JSONResponse:
        """The unified Trash Folder: item count, physical bytes, and every root that holds retired files."""
        return JSONResponse(await run_in_threadpool(output_storage.trash_overview))

    @app.post("/api/hub/trash/move-to-macos-trash")
    async def hub_trash_to_macos(request: Request) -> JSONResponse:
        """Body `{"items": "all" | [ids]}` (default all): move them to macOS Trash, which the owner empties."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "code": "BAD_BODY",
                                 "detail": "The request body must be a JSON object."}, status_code=400)
        try:
            result = await run_in_threadpool(output_storage.trash_to_macos, body.get("items", "all"))
        except output_storage.TrashRefused as refusal:
            return JSONResponse(refusal.body, status_code=refusal.status)
        return JSONResponse({"ok": True, **result})

    @app.post("/api/hub/trash/empty")
    async def hub_trash_empty(request: Request) -> JSONResponse:
        """This Mac's own window: body `{expected_count, expected_bytes}` as its dialog showed them.
        Re-measured here; a difference is 409 TRASH_CHANGED and nothing is deleted. Not a fleet route."""
        body = await _empty_body(request)
        if body is None or body.get("expected_count") is None or body.get("expected_bytes") is None:
            return JSONResponse({"ok": False, "code": "EXPECTED_REQUIRED",
                                 "detail": "Send the expected_count and expected_bytes the dialog showed."},
                                status_code=400)
        status, row = await run_in_threadpool(_empty_here, body, remote=False)
        _audit_empty("owner", "local", [row])
        return _empty_envelope([row], status)

    @app.post("/api/hub/trash/allow-remote-empty")
    async def hub_trash_allow_remote_empty(request: Request) -> JSONResponse:
        """This Mac's "Allow remote empty" choice and rule, from its own window only. Off until the
        owner turns it on: `{"mode": "automatic"|"on"|"off", "rule_enabled": bool, "window_days": int}`
        (the first cut's `{"enabled": bool}` still means on/off)."""
        body = await _empty_body(request)
        if body is None:
            return JSONResponse({"ok": False, "code": "BAD_BODY",
                                 "detail": "The request body must be a JSON object."}, status_code=400)
        try:
            decision = await run_in_threadpool(
                output_storage.set_remote_empty, body.get("mode"), enabled=body.get("enabled"),
                rule_enabled=body.get("rule_enabled"), window_days=body.get("window_days"))
        except output_storage.TrashRefused as refusal:
            return JSONResponse(refusal.body, status_code=refusal.status)
        startup_control.audit("trash_remote_empty", {"mode": output_storage.remote_empty_settings()["mode"],
                                                     "effective": decision["effective"],
                                                     "source": decision["source"]})
        return JSONResponse({"ok": True, **decision, "allow_remote_empty": decision["effective"]})

    @app.post("/api/hub/fleet/nodes/{node_id}/model-packages/{package_id}/share")
    async def fleet_node_model_share(node_id: str, package_id: str, request: Request) -> JSONResponse:
        """Let another Mac copy this package from a Mac in this group over the LAN (0.6.146).

        Answers a time-limited, read-only token for the one revision installed and verified on that
        Mac, and the URL it is served under - built from the address this request reached the Mac
        at (the controller's own address for it, for a relayed share). Refused for a package that
        is not installed and verified there. Audited here and, for a node, again on the node.
        """
        from components import model_packages
        from . import model_share
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            startup_control.audit("model_share_relay", {"node": wanted, "package": package_id,
                                                        "actor": actor})
            try:
                return JSONResponse(await fleet.relay_model(wanted, "POST",
                                                            f"/model-packages/{package_id}/share"))
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
        catalog = _model_catalog()
        manifest = catalog.get(package_id)
        if manifest is None or manifest.get(_MANIFEST_ERROR):
            return JSONResponse({"ok": False, "code": "UNKNOWN_PACKAGE",
                                 "detail": "unknown model package"}, status_code=404)
        try:
            files = model_packages.shared_files(manifest, _model_library(), catalog=catalog)
        except (OSError, ValueError) as exc:
            startup_control.audit("model_share", {"package": package_id, "actor": actor,
                                                  "refused": str(exc)[:200]})
            return JSONResponse({"ok": False, "code": "NOT_VERIFIED",
                                 "detail": f"This Mac cannot share {package_id}: {exc}"}, status_code=409)
        token, expires_at = model_share.create(package_id, manifest["revision"], files)
        startup_control.audit("model_share", {"package": package_id, "actor": actor,
                                              "revision": manifest["revision"]})
        return JSONResponse({"ok": True, "node": wanted, "share": {
            "url_base": f"{str(request.base_url).rstrip('/')}{model_share.PREFIX}{token}/files",
            "token": token, "expires_at": expires_at, "revision": manifest["revision"],
            "files": [{"path": e["path"], "bytes": e["size"], "sha256": e["sha256"]}
                      for e in manifest["files"]]}})

    class _ShareTransfer(FileResponse):
        """A shared model file; frees its transfer slot when the response ends, however it ends."""

        def __init__(self, path, *, token: str, **kwargs) -> None:
            super().__init__(path, **kwargs)
            self._share_token = token

        async def __call__(self, scope, receive, send) -> None:
            from . import model_share

            async def delivered(message) -> None:
                await send(message)
                if message["type"] == "http.response.body" and message.get("body"):
                    model_share.renew(self._share_token)

            try:
                await super().__call__(scope, receive, delivered)
            finally:
                model_share.release()

    @app.api_route("/api/hub/model-share/{token}/files/{path:path}", methods=["GET", "HEAD"])
    def serve_model_share(token: str, path: str, request: Request) -> Response:
        """One file of a LAN share: Range-capable, identity-encoded, only the shared package's own
        files (anything else - another path, a traversal, an expired token - is the same 404)."""
        from . import model_share
        found = model_share.file_for(token, path)
        if found is None:
            raise HTTPException(404, "no such shared model file")
        share, target = found
        if not model_share.acquire():
            startup_control.audit("model_share_busy", {"package": share["package_id"],
                                                       "peer": request.client.host if request.client else None})
            return JSONResponse({"ok": False, "code": "SHARE_BUSY",
                                 "detail": f"this Mac is already sending {model_share.MAX_TRANSFERS} "
                                           f"model files; try again shortly or use the internet"},
                                status_code=429, headers={"Retry-After": str(model_share.RETRY_AFTER_S)})
        if request.method == "GET" and not request.headers.get("range"):
            startup_control.audit("model_share_served", {
                "package": share["package_id"], "file": path,
                "peer": request.client.host if request.client else None})
        return _ShareTransfer(target, token=token, media_type="application/octet-stream")

    @app.delete("/api/hub/fleet/nodes/{node_id}/model-packages/{package_id}")
    async def fleet_node_model_remove(node_id: str, package_id: str, request: Request) -> JSONResponse:
        """Remove one model from one Mac in this group, from the Gateway (owner decision 2026-10-28).

        The same move the Mac's own Models screen makes: the package folder goes to **that Mac's**
        library `_trash` with a receipt, and nothing is deleted. Refused in the same cases - a
        running job on the model, a paused model still holding its weights, a package another
        installed package requires - and, for a joined Mac, again on the Mac itself.

        A Mac that is offline is **reported, not failed**: the relay answers `NODE_UNREACHABLE` in
        the Mac's own words and this returns it unchanged, exactly as the install relay does.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            startup_control.audit("model_removal_relay", {"node": wanted, "package": package_id,
                                                          "actor": actor})
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, "DELETE", f"/model-packages/{package_id}"))
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
        try:
            answer = await run_in_threadpool(remove_model_package, package_id)
        except HTTPException as exc:
            startup_control.audit("model_removal", {"node": wanted, "package": package_id,
                                                    "actor": actor, "refused": str(exc.detail)[:200]})
            return JSONResponse({"ok": False, "node": wanted,
                                 "detail": exc.detail if isinstance(exc.detail, str) else exc.detail},
                                status_code=exc.status_code)
        startup_control.audit("model_removal", {"node": wanted, "package": package_id,
                                                "actor": actor, "ok": True,
                                                "bytes": answer.get("bytes")})
        return JSONResponse({**answer, "node": wanted})

    @app.get("/api/hub/fleet/nodes/{node_id}/model-downloads/{run_id}")
    async def fleet_node_model_download_status(node_id: str, run_id: str) -> JSONResponse:
        """How a download started through the route above is going, on the Mac that runs it."""
        from . import model_downloads
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            try:
                answer = await fleet.relay_model(wanted, "GET", f"/model-downloads/{run_id}")
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
            # The node re-read its own catalogs before it said `done`; this controller's view of
            # that node is its own cache, and is what the Gateway's Fleet read is built from.
            await _show_finished_download(answer.get("run"), wanted)
            return JSONResponse(answer)
        run = model_downloads.status(run_id)
        if run is None:
            return JSONResponse({"ok": False, "code": "UNKNOWN_DOWNLOAD",
                                 "detail": "this Mac has no model download with that id"}, status_code=404)
        await _show_finished_download(run, "local")
        return JSONResponse({"ok": True, "node": wanted, "run": run})

    @app.post("/api/hub/model-packages/{package_id}/verify")
    def verify_model_package(package_id: str) -> dict[str, Any]:
        from components import model_packages
        try:
            catalog = _model_catalog()
            manifest = catalog[package_id]
            report = model_packages.verify_installed(manifest, _model_library(), catalog=catalog)
            row = _model_state(manifest, catalog, monitor)
            # The row is built from the cheap receipt check, and a verify that fails - bytes that
            # are present but not this manifest's, a companion cache that could not be written -
            # leaves that receipt exactly as it was. Returning the row unchanged would answer a
            # named failure with the state from before it ran, and for a package that has no
            # receipt that state is `not_installed`: "nothing is here" printed directly after
            # "these bytes are wrong". The report is the measurement the operator just asked for.
            state = report.get("state")
            update: dict[str, Any] = {}
            if state and state not in ("files_verified", row.get("files_state")):
                update = {"state": state, "files_state": state, "reason": report.get("reason"),
                          "dispatch_state": "ineligible"}
            elif report.get("reason") and not row.get("reason"):
                # Same state, but the verify is the call that actually looked for the files: its
                # reason names the folder that is missing and the manifest to look at, where the
                # cheap receipt check has nothing to say.
                update = {"reason": report["reason"]}
            if report.get("verification"):
                # This response is the one place the operator asked for a full rehash; saying
                # `cached_receipt` here would describe the pre-verify row instead of the work the
                # button just did.
                update["verification"] = report["verification"]
            return {**row, **update} if update else row
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/hub/machine-label")
    def set_machine_label(body: dict[str, Any], request: Request) -> dict[str, Any]:
        """Name this Mac the way the operator wants it shown.

        The name is a label, not an identity: the Hub's machine key stays "local", routing and fleet
        registration never depend on it, and renaming a Mac after nodes have joined does not orphan
        anything. `registry.set_label` has existed for this the whole time; no route exposed it, so
        the only name an operator could ever have was their hostname.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        from .backend import registry
        name = str(body.get("name") or "").strip()
        if len(name) > 120:
            raise HTTPException(400, "the name must be at most 120 characters")
        # This Mac's controller may rename it with the Fleet token (a rename from the Gateway is relayed
        # here, `fleet.relay_name`). Only to a real name: clearing it back to the hostname is the owner's.
        if hub_auth._fleet_actor(request).get("kind") != "hub":
            try:
                name = fleet.valid_name(name)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        registry.set_label("local", name)
        return {"settings": control_plane.public_settings()}

    @app.post("/api/hub/fleet/nodes/{node_id}/name")
    async def fleet_node_name(node_id: str, body: dict[str, Any]) -> JSONResponse:
        """Rename one Mac in this group - a joined node, or this controller itself (its id or ``local``).

        The Gateway's Fleet page reaches it with the Fleet token it holds. A node is renamed **on the
        node** and then here (see `fleet_nodes.relay_name` for why); a node's own credential cannot call
        this (it is not in `NODE_ALLOWED`).
        """
        from .backend import fleet_nodes as fleet
        from .backend import registry
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            try:
                registry.set_label("local", fleet.valid_name(body.get("name")))
            except ValueError as exc:
                return JSONResponse({"ok": False, "code": "BAD_NAME", "detail": str(exc)}, status_code=400)
            return JSONResponse({"ok": True, "node": wanted, "name": fleet.this_machine()["name"]})
        try:
            answer = await fleet.relay_name(wanted, body.get("name"))
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        monitor.reload_registry()
        return JSONResponse(answer)

    @app.post("/api/hub/fleet/nodes/{node_id}/pause")
    async def fleet_node_pause(node_id: str, body: dict[str, Any]) -> JSONResponse:
        """Pause or resume one Mac in this group by hand: a joined node, or this controller itself.

        The Gateway's Fleet page reaches it with the Fleet token it holds. It is the scheduler's own
        per-machine switch (`registry.set_machine_enabled`, the one every modality's dispatch already
        reads): a paused Mac gets no new work, jobs already running there finish, it stays monitored
        and in the roster, and it takes work again the moment it is resumed. Kept in `machine_flags.json`,
        so it survives a restart. A node's own credential cannot call this (not in `NODE_ALLOWED`).
        """
        from .backend import broker, registry, transcription_jobs
        from .backend import fleet_nodes as fleet
        paused = body.get("paused")
        if not isinstance(paused, bool):
            return JSONResponse({"ok": False, "code": "VALUE_REQUIRED",
                                 "detail": "paused must be true or false"}, status_code=400)
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            machine = "local"
        elif wanted in fleet._read():
            machine = wanted
        else:
            return JSONResponse({"ok": False, "code": "NODE_UNKNOWN",
                                 "detail": f"this controller has no Mac {wanted!r}"}, status_code=404)
        registry.set_machine_enabled(machine, not paused)
        broker.wake_dispatcher()
        transcription_jobs._wakeup.set()
        return JSONResponse({"ok": True, "node": wanted, "paused": paused})

    @app.post("/api/hub/fleet/nodes/{node_id}/assigned-image-model")
    async def fleet_node_assigned_image_model(node_id: str, body: dict[str, Any]) -> JSONResponse:
        """Set the image pool's model for one Mac in this group (owner, 2026-10-24).

        The Gateway decides it - Auto by the Mac's RAM, or the owner's own pick - and sends the
        result here with its Fleet token. Only work submitted with `routing: assigned` reads it;
        a job that names its model still runs on any Mac that has it. Kept in `machine_flags.json`
        like the pause. `model: null` clears it. A node's own credential cannot call this.
        """
        from .backend import broker, registry
        from .backend import fleet_nodes as fleet
        if not isinstance(body, dict) or "model" not in body or not (body["model"] is None or isinstance(body["model"], str)):
            return JSONResponse({"ok": False, "code": "VALUE_REQUIRED",
                                 "detail": "model must be a model id or null"}, status_code=400)
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            machine = "local"
        elif wanted in fleet._read():
            machine = wanted
        else:
            return JSONResponse({"ok": False, "code": "NODE_UNKNOWN",
                                 "detail": f"this controller has no Mac {wanted!r}"}, status_code=404)
        try:
            registry.set_assigned_image_model(machine, body["model"])
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "BAD_SETTING", "detail": str(exc)}, status_code=400)
        broker.wake_dispatcher()
        return JSONResponse({"ok": True, "node": wanted, "model": registry.assigned_image_model(machine)})

    @app.get("/api/hub/workstation")
    def workstation_read() -> dict[str, Any]:
        """This Mac's workstation mode: its settings, what it is doing now and today's counts."""
        return {"ok": True, "workstation": workstation.status()}

    @app.post("/api/hub/workstation")
    def workstation_write(body: dict[str, Any]) -> JSONResponse:
        """Change this Mac's workstation settings (any subset). Its controller relays the Gateway's
        change here with the Fleet token; the setting is this Mac's own, so it is kept here."""
        try:
            answer = workstation.save(body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "BAD_SETTING", "detail": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "workstation": answer})

    @app.post("/api/hub/fleet/nodes/{node_id}/workstation")
    async def fleet_node_workstation(node_id: str, body: dict[str, Any]) -> JSONResponse:
        """Change one Mac's workstation settings from the Gateway: a joined node (relayed to it, the
        way a rename is) or this controller itself (its id or ``local``)."""
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            try:
                answer = workstation.save(body)
            except ValueError as exc:
                return JSONResponse({"ok": False, "code": "BAD_SETTING", "detail": str(exc)},
                                    status_code=400)
            return JSONResponse({"ok": True, "node": wanted, "workstation": answer})
        try:
            answer = await fleet.relay_workstation(wanted, body)
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        return JSONResponse(answer)

    @app.get("/api/hub/fleet/nodes/{node_id}/work-policy")
    async def fleet_node_work_policy_read(node_id: str) -> JSONResponse:
        from . import operator_controls
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            return JSONResponse({"ok": True, "node": wanted,
                                 "work_policy": operator_controls.work_policy(monitor)})
        try:
            answer = await fleet.relay_work_policy(wanted, "GET")
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        return JSONResponse(answer)

    @app.post("/api/hub/fleet/nodes/{node_id}/work-policy")
    async def fleet_node_work_policy_write(node_id: str, body: dict[str, Any]) -> JSONResponse:
        from . import operator_controls
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            try:
                policy = operator_controls.save_work_policy(monitor, body)
            except HTTPException as error:
                return JSONResponse({"ok": False, "code": "BAD_POLICY",
                                     "detail": error.detail}, status_code=error.status_code)
            return JSONResponse({"ok": True, "node": wanted, "work_policy": policy})
        try:
            answer = await fleet.relay_work_policy(wanted, "POST", body)
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        return JSONResponse(answer)

    @app.get("/api/hub/fleet/nodes/{node_id}/memory/admission")
    async def fleet_node_memory_admission_read(node_id: str, model: str = "",
                                               mode: str | None = None) -> JSONResponse:
        """One Mac's memory admission from the Gateway: answered here for this Mac, relayed to a
        joined node. The local Models-screen route is separate and stays model-wide."""
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        machine_id = fleet.this_machine()["id"].lower()
        if wanted not in ("local", machine_id):
            if control_plane.load_settings().get("role") != "controller":
                return JSONResponse({"ok": False, "code": "NOT_CONTROLLER",
                                     "detail": "this Mac is not a controller, so it has no "
                                               "nodes to reach"}, status_code=404)
            try:
                answer = await fleet.relay_memory_admission(
                    wanted, "GET", query={"model": model, "mode": mode})
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
            return JSONResponse(answer)
        wanted_model = str(model or "").strip()
        wanted_mode = mode or None
        entry = None
        if wanted_model and (wanted_mode is None or wanted_mode in ("txt2img", "edit")):
            try:
                entry = _admission_entry(wanted_model)
            except HTTPException as error:
                # _admission_entry raises the local screen's bare 404; this route answers
                # its own structured UNKNOWN_MODEL below instead.
                if error.status_code != 404:
                    raise
        return _fleet_memory_admission_read(model=wanted_model, mode=wanted_mode, entry=entry,
                                            node=wanted, node_id=machine_id)

    @app.put("/api/hub/fleet/nodes/{node_id}/memory/admission")
    async def fleet_node_memory_admission_write(node_id: str,
                                                body: dict[str, Any]) -> JSONResponse:
        """Set or reset one Mac's RAM pair, or (once wired) one reference row, on that Mac."""
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        machine_id = fleet.this_machine()["id"].lower()
        if wanted not in ("local", machine_id):
            if control_plane.load_settings().get("role") != "controller":
                return JSONResponse({"ok": False, "code": "NOT_CONTROLLER",
                                     "detail": "this Mac is not a controller, so it has no "
                                               "nodes to reach"}, status_code=404)
            try:
                answer = await fleet.relay_memory_admission(wanted, "PUT", body)
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
            pending = isinstance(answer, dict) and answer.get("outcome") == "pending"
            return JSONResponse(answer, status_code=202 if pending else 200)
        model = str(body.get("model") or "").strip()
        mode = body.get("mode")
        entry = None
        if model and (mode is None or mode in ("txt2img", "edit")):
            try:
                entry = _admission_entry(model)
            except HTTPException as error:
                if error.status_code != 404:
                    raise
        return _fleet_memory_admission_write(body=body, entry=entry, node=wanted,
                                             node_id=machine_id,
                                             total_memory_bytes=_admission_total_memory_bytes())

    # **Remote final rendering, allowed per Mac from the Gateway (owner, 2026-10-23).** The setting
    # is this Mac's own - it is the one that offers the capacity - so it is kept here, and the
    # controller relays the Gateway's change to it with the Fleet token, the way Lite Mode is.
    # Neither route is in `NODE_ALLOWED` and nothing in the app sets it.
    _RENDER_AUTHORITY_BODY = {"accept_unqualified", "requested_by", "encode"}

    def _render_authority_change(body: dict[str, Any]) -> JSONResponse:
        if not isinstance(body, dict) or not body or set(body) - _RENDER_AUTHORITY_BODY:
            return JSONResponse({"ok": False, "code": "BAD_SETTING", "detail":
                                 "send accept_unqualified, requested_by and optionally encode"},
                                status_code=400)
        try:
            state = installed_authority.set_opt_in(
                body.get("accept_unqualified"), body.get("requested_by"), body.get("encode"))
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "BAD_SETTING", "detail": str(exc)},
                                status_code=400)
        broker.wake_dispatcher()
        return JSONResponse({"ok": True, "render_authority": state})

    @app.get("/api/hub/render-authority")
    def render_authority_read() -> dict[str, Any]:
        """This Mac's remote-rendering opt-in: the choice, who made it, and whether it can act."""
        from .backend import registry
        return {"ok": True, "render_authority": installed_authority.opt_in_state(),
                # The other half of "this Mac renders": whether its own pause exempts render.
                # Read here, beside the opt-in, because the Fleet page shows them together.
                "render_while_paused": registry.render_while_paused("local")}

    # **Does this Mac's own pause exempt final render? (owner, 2026-10-28.)** A separate route from
    # the opt-in above, because it answers a different question and is off by default: a Mac may
    # be paused for generation and still be the one that assembles videos. Nothing here widens
    # any other gate — Lite Mode, drain, maintenance and quarantine still refuse a render.
    _RENDER_WHILE_PAUSED_BODY = {"render_while_paused", "requested_by"}

    def _render_while_paused_change(body: dict[str, Any]) -> JSONResponse:
        if (not isinstance(body, dict) or set(body) != _RENDER_WHILE_PAUSED_BODY
                or type(body.get("render_while_paused")) is not bool
                or not isinstance(body.get("requested_by"), str) or not body["requested_by"]):
            return JSONResponse({"ok": False, "code": "BAD_SETTING", "detail":
                                 "send render_while_paused (true or false) and requested_by"},
                                status_code=400)
        from .backend import registry
        try:
            registry.set_render_while_paused("local", body["render_while_paused"])
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "BAD_SETTING", "detail": str(exc)},
                                status_code=400)
        broker.wake_dispatcher()
        return JSONResponse({"ok": True,
                             "render_while_paused": registry.render_while_paused("local")})

    @app.get("/api/hub/render-while-paused")
    def render_while_paused_read() -> dict[str, Any]:
        """Whether this Mac's pause exempts final render (off unless the owner switched it on)."""
        from .backend import registry
        return {"ok": True, "render_while_paused": registry.render_while_paused("local")}

    @app.post("/api/hub/render-while-paused")
    def render_while_paused_write(body: dict[str, Any]) -> JSONResponse:
        return _render_while_paused_change(body)

    @app.get("/api/hub/fleet/nodes/{node_id}/render-while-paused")
    async def fleet_node_render_while_paused_read(node_id: str) -> JSONResponse:
        from .backend import fleet_nodes as fleet
        from .backend import registry
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            return JSONResponse({"ok": True, "node": wanted,
                                 "render_while_paused": registry.render_while_paused("local")})
        try:
            answer = await fleet.relay_render_while_paused(wanted, "GET")
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        return JSONResponse(answer)

    @app.post("/api/hub/fleet/nodes/{node_id}/render-while-paused")
    async def fleet_node_render_while_paused_write(
        node_id: str, body: dict[str, Any]
    ) -> JSONResponse:
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            answer = await run_in_threadpool(_render_while_paused_change, body)
            if answer.status_code == 200:
                return JSONResponse({**json.loads(answer.body), "node": wanted})
            return answer
        try:
            answer = await fleet.relay_render_while_paused(wanted, "POST", body)
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        return JSONResponse(answer)

    @app.post("/api/hub/render-authority")
    def render_authority_write(body: dict[str, Any]) -> JSONResponse:
        return _render_authority_change(body)

    @app.get("/api/hub/fleet/nodes/{node_id}/render-authority")
    async def fleet_node_render_authority_read(node_id: str) -> JSONResponse:
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            return JSONResponse({"ok": True, "node": wanted, "render_authority":
                                 await run_in_threadpool(installed_authority.opt_in_state)})
        try:
            answer = await fleet.relay_render_authority(wanted, "GET")
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        return JSONResponse(answer)

    @app.post("/api/hub/fleet/nodes/{node_id}/render-authority")
    async def fleet_node_render_authority_write(node_id: str, body: dict[str, Any]) -> JSONResponse:
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted in ("local", fleet.this_machine()["id"].lower()):
            answer = await run_in_threadpool(_render_authority_change, body)
            if answer.status_code == 200:
                return JSONResponse({**json.loads(answer.body), "node": wanted})
            return answer
        try:
            answer = await fleet.relay_render_authority(wanted, "POST", body)
        except fleet.StartupRelayError as error:
            return JSONResponse(error.body, status_code=error.status)
        return JSONResponse(answer)

    @app.get("/api/hub/shared-voices")
    def shared_voice_list() -> dict[str, Any]:
        """What this controller holds and how far each voice has reached its nodes.

        The push is to six controllers, not a hundred Macs: the gateway never talks to a node. This
        is the read that lets it report what actually happened per location instead of assuming.
        """
        return {"voices": shared_voices.list_voices(monitor),
                "deletions": shared_voices.list_deletions(monitor),
                "targets": shared_voices.targets(monitor)}

    @app.post("/api/hub/shared-voices", status_code=201)
    async def shared_voice_push(
        request: Request,
        response: Response,
        audio: UploadFile = File(...),
        name: str = Form(...),
        language: str = Form(...),
        gender: str = Form(...),
        license: str = Form(...),
        notes: str = Form(""),
        source_url: str = Form(""),
        transcript: str | None = Form(None),
        transcript_segments: str | None = Form(None),
        voice_id: str = Form(""),
        tags: str = Form(""),
        permission_acknowledged: bool = Form(False),
    ) -> dict[str, Any]:
        """Create or refresh one fleet-wide voice, then fan it out to every joined node.

        The id may be supplied or left to the Hub: a caller that already names a clip by its own
        content hash can send that (12 lowercase hex) and the same voice then carries one id on
        every controller, which is easier to reconcile than six. A caller that does not gets one.
        Either way the bytes decide identity - identical audio refreshes the voice already holding
        it rather than adding a second copy, and answers 200 instead of 201.

        ``?wait=true`` runs one fan-out pass before answering, for a caller that wants to report per
        location from this response; without it the reconciler finishes the job and retries whatever
        a node was not ready for. It is read from the query string rather than the multipart body,
        so a caller does not have to mix a control flag into the voice's own fields.
        """
        try:
            audio_bytes = await audio.read()
        except Exception as exc:
            raise HTTPException(400, {"code": "SHARED_VOICE_AUDIO_UNREADABLE",
                                      "detail": f"The uploaded reference could not be read: {exc}"}) from exc
        finally:
            await audio.close()
        segments: object = []
        if (transcript_segments or "").strip():
            try:
                segments = json.loads(transcript_segments)
            except ValueError as exc:
                raise HTTPException(400, {
                    "code": "SHARED_VOICE_SEGMENTS_INVALID",
                    "detail": "transcript_segments must be a JSON array of ordered segments.",
                }) from exc
        try:
            voice, created = shared_voices.upsert(
                audio_bytes=audio_bytes, filename=audio.filename or "reference.wav",
                name=name, language=language, gender=gender, license=license,
                notes=notes, source_url=source_url or None,
                transcript=transcript, transcript_segments=segments or None,
                permission_acknowledged=permission_acknowledged,
                voice_id=voice_id.strip() or None,
                # The Gateway's own tags, as a JSON array (or a comma-separated list): the Hub
                # stores and shows them and is never their editor.
                tags=tags or None,
            )
        except shared_voices.SharedVoiceConflict as exc:
            raise HTTPException(409, {"code": "SHARED_VOICE_CONFLICT", "detail": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(400, {"code": "SHARED_VOICE_REFUSED", "detail": str(exc)}) from exc
        wait = str(request.query_params.get("wait", "")).strip().lower() in {"1", "true", "yes"}
        if wait:
            await shared_voices.sync_voice(monitor, voice["id"])
        else:
            shared_voices.start_sync(monitor, voice["id"])
        if not created:
            # A refresh changed nothing about the bytes, so "already there" is a 200. A caller
            # reporting per location should not read that as a new voice.
            response.status_code = 200
        return {"voice": shared_voices.serialize(shared_voices.get(voice["id"]) or voice, monitor),
                "created": created,
                "status": "created" if created else "refreshed"}

    @app.get("/api/hub/shared-voices/{voice_id}")
    def shared_voice_read(voice_id: str) -> dict[str, Any]:
        voice = shared_voices.get(voice_id)
        if not voice:
            raise HTTPException(404, "unknown shared voice")
        return {"voice": shared_voices.serialize(voice, monitor)}

    @app.get("/api/hub/shared-voices/{voice_id}/audio")
    def shared_voice_audio(voice_id: str) -> FileResponse:
        path = shared_voices.audio_path(voice_id)
        if path is None:
            raise HTTPException(404, "unknown shared voice audio")
        return FileResponse(path, media_type="application/octet-stream", filename=path.name)

    @app.delete("/api/hub/shared-voices/{voice_id}")
    async def shared_voice_delete(voice_id: str) -> dict[str, Any]:
        """Tombstone one voice across the fleet: a removal on six controllers, not a hundred Macs.

        Awaited rather than queued: a caller removing a voice wants to know whether it came off the
        nodes, and the per-node work is already parallel with a short timeout.
        """
        try:
            shared_voices.prepare_delete(voice_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown shared voice") from exc
        except shared_voices.SharedVoiceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"deletion": await shared_voices.delete_voice(monitor, voice_id)}

    @app.post("/api/hub/shared-voices/{voice_id}/sync")
    async def shared_voice_sync(voice_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ask again now, for a node that was off during the push."""
        voice = shared_voices.get(voice_id)
        if not voice:
            raise HTTPException(404, "unknown shared voice")
        targets = (body or {}).get("studio_ids")
        target_ids = {str(value) for value in targets} if isinstance(targets, list) else None
        return {"voice": await shared_voices.sync_voice(monitor, voice_id, target_ids)}

    @app.post("/api/hub/jobs")
    def submit_jobs(envelope: dict[str, Any], request: Request) -> dict[str, Any]:
        # The gate is about who owns the work, not about which Mac may run it. A node must not own
        # a *customer's* submission, so a controller is refused - but the person sitting at this
        # Mac is not a customer, and refusing his own app meant a Mac switched to a node could not
        # generate anything locally at all. `presented_owner_token` is the whole of the difference:
        # the Hub token is this Mac's own, the fleet token is someone else's.
        if (not control_plane.accepts_customer_jobs()
                and not auth.presented_owner_token(request, token)):
            raise HTTPException(
                409, control_plane.refusal_for_remote_work())
        client_request_id = envelope.get("clientRequestId")
        replay = (next((batch for batch in broker.batches.values()
                       if batch.get("client_request_id") == client_request_id), None)
                  if isinstance(client_request_id, str) else None)
        replay = replay or (ledger.load_batch_by_client_request_id(client_request_id)
                            if isinstance(client_request_id, str) else None)
        if replay is None:
            shape_refusal = broker.submission_shape_refusal(envelope)
            if shape_refusal:
                raise HTTPException(400, shape_refusal)
            try:
                refusal = _submission_readiness(envelope, monitor)
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
            if refusal:
                raise HTTPException(409, refusal)
            routing, _routing_error = routing_rule.parse(envelope.get("routing"))
            if broker.every_capable_mac_paused(envelope.get("modality"), envelope.get("model"), routing):
                # Every Mac here that could run it is paused: refuse rather than queue it for ever, so
                # the Gateway places it in another group. A replay of an accepted batch is not refused.
                return JSONResponse({"error": broker.NO_AVAILABLE_MACHINE_TEXT,
                                     "code": broker.NO_AVAILABLE_MACHINE,
                                     "detail": broker.NO_AVAILABLE_MACHINE_TEXT}, status_code=503)
        # Who asked: the Hub's own credential check, not a field a caller could set. The Hub
        # token is this Mac's app; any other credential is the Gateway or the fleet.
        envelope = {**envelope,
                    "origin": "this-mac" if auth.presented_owner_token(request, token) else "gateway"}
        try:
            result = broker.submit_batch(envelope)
        except Exception as exc:  # noqa: BLE001 - the door answers with a cause, not a bare 500
            _audit_submission_failure(envelope, exc)
            if isinstance(exc, (OSError, sqlite3.Error)):
                raise HTTPException(503, {
                    "code": "SUBMIT_STORE_FAILED",
                    "detail": "this Hub could not write the batch to its store; "
                              "retry with the same clientRequestId",
                }) from exc
            raise HTTPException(500, {
                "code": "SUBMIT_FAILED",
                "detail": f"the Hub failed while accepting this batch ({type(exc).__name__}); "
                          "retry with the same clientRequestId",
            }) from exc
        if result.get("code") == cloud_guard.REFUSAL_CODE:
            raise HTTPException(403, {"code": result["code"], "detail": result["error"]})
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result

    @app.get("/api/hub/jobs")
    def list_jobs() -> dict[str, Any]:
        return {"batches": broker.listed_batches()}

    @app.get("/api/hub/jobs/{batch_id}")
    def get_job(batch_id: str) -> dict[str, Any]:
        batch = broker.batches.get(batch_id) or ledger.load_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown batch")
        return _summary(batch)

    @app.post("/api/hub/jobs/{batch_id}/items/{item_index}/reconcile")
    async def reconcile_item(batch_id: str, item_index: int) -> dict[str, Any]:
        try:
            batch = broker.batches.get(batch_id) or ledger.load_batch(batch_id)
            if batch is not None and batch.get("modality") == "voice":
                return await broker.reconcile_voice_item(
                    batch_id, item_index, client=monitor._client
                )
            if batch is not None and batch.get("modality") == "music":
                return await broker.reconcile_music_item(
                    batch_id, item_index, client=monitor._client
                )
            if batch is not None and batch.get("modality") == "video":
                return await broker.reconcile_video_item(
                    batch_id, item_index, client=monitor._client
                )
            # No silent fallback to Image: a modality with no reconciler must be named. The
            # fallback is how a video batch answered `unknown Image batch`.
            if batch is not None and batch.get("modality") != "image":
                raise ValueError(
                    f"reconciliation is not available for a "
                    f"{batch.get('modality') or 'unknown'} batch"
                )
            return await broker.reconcile_image_item(
                batch_id, item_index, client=monitor._client
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/jobs/{batch_id}/items/{item_index}/discard")
    async def discard_item(batch_id: str, item_index: int) -> dict[str, Any]:
        """Abandon one uncertain item, releasing the machine it has been pinning."""
        try:
            return broker.discard_uncertain_item(batch_id, item_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/jobs/{batch_id}/items/{item_index}/retry")
    async def retry_item(batch_id: str, item_index: int) -> dict[str, Any]:
        """Put one failed item back in the queue for the dispatcher to run again."""
        try:
            return broker.retry_failed_item(batch_id, item_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    async def open_worker_artifact(worker: dict[str, Any], artifact_path: str):
        from .backend.peers import studio_request
        url, headers = studio_request(worker, artifact_path)
        client = httpx.AsyncClient(follow_redirects=False)
        try:
            response = await client.send(client.build_request("GET", url, headers=headers), stream=True)
            response.raise_for_status()
        except Exception:
            await client.aclose()
            raise
        return client, response

    @app.get("/api/hub/jobs/{batch_id}/items/{item_index}/artifact")
    async def job_artifact(batch_id: str, item_index: int):
        """Authenticated Hub proxy for a completed worker artifact."""
        batch = broker.batches.get(batch_id) or ledger.load_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown batch")
        item = next((row for row in batch["items"] if row.get("index") == item_index), None)
        if not item or item.get("state") != "done":
            raise HTTPException(404, "artifact is not available")
        # A Chat completion's artifact is the answer the Hub already recorded on its item: the
        # worker streams a completion and keeps no file, so there is nothing on the worker to
        # fetch. Serving it here as UTF-8 text keeps one artifact route for every modality, so
        # the Jobs page can show and save a Chat answer the same way it saves an image or a clip.
        if batch.get("modality") == "chat":
            answer = item.get("text")
            if not isinstance(answer, str) or not answer:
                raise HTTPException(404, "artifact is not available")
            payload = answer.encode("utf-8")
            return Response(content=payload, media_type=chat_jobs.PAGE_MEDIA_TYPE,
                            headers={"Content-Length": str(len(payload)),
                                     "X-Content-SHA256": hashlib.sha256(payload).hexdigest()})
        artifact_url = item.get("worker_artifact_url") or item.get("artifact_url")
        if not artifact_url:
            raise HTTPException(404, "artifact is not available")
        worker = next((row for row in monitor.registry if row["id"] == item.get("studio")), None)
        if worker is None:
            raise HTTPException(503, artifact_unavailable_reason(
                item, "not running or not connected to this Hub"))
        suffix = broker.MODALITY.get(batch.get("modality"), (None, None, None))[2]
        expected = f"/api/generate/jobs/{item.get('studio_job_id')}/{suffix}"
        parsed = urlsplit(str(artifact_url))
        worker_origin = urlsplit(worker["base_url"])
        # A path recorded by this Hub is checked against the worker's origin *now*, not the one it
        # had when the job ran - which is why the stored value is a path and not a URL. An absolute
        # URL from a before-this-change batch is accepted only when it happens to name the worker
        # the item is still assigned to and the path this Hub would have written itself; anything
        # else is refused, as before, so a worker-supplied URL is never followed on trust.
        if not parsed.scheme and not parsed.netloc:
            same_origin = True
        else:
            same_origin = (parsed.scheme == worker_origin.scheme
                           and parsed.netloc == worker_origin.netloc
                           and parsed.path == expected)
        if (not suffix or not same_origin or parsed.query or parsed.fragment or parsed.path != expected):
            # Never follow a worker-supplied URL outside its private origin or
            # attach the worker credential to an arbitrary location.
            #
            # This one stays internal: it is a Hub-side integrity refusal, not something the
            # operator caused or can act on, and naming the worker's address to them would be
            # noise. It says so rather than borrowing the "could not be read" sentence above, which
            # would send them to restart a Mac that is running perfectly well.
            raise HTTPException(502, "worker artifact reference is invalid")
        release_reader = output_storage.acquire_reader(batch_id=batch_id, item_index=item_index)
        try:
            client, response = await open_worker_artifact(worker, expected)
        except httpx.HTTPError as exc:
            release_reader()
            raise HTTPException(503, artifact_unavailable_reason(
                dict(item, machine=item.get("machine") or worker.get("machine")),
                "not answering this Hub")) from exc

        async def close_stream() -> None:
            await response.aclose()
            await client.aclose()
            release_reader()

        media_type = item.get("media_type") or response.headers.get("content-type") or "application/octet-stream"
        headers = {}
        if item.get("bytes"):
            headers["Content-Length"] = str(item["bytes"])
        if item.get("sha256"):
            headers["X-Content-SHA256"] = str(item["sha256"])
        return StreamingResponse(response.aiter_bytes(1024 * 1024), media_type=media_type,
                                 headers=headers, background=BackgroundTask(close_stream))

    review_no_store = {"Cache-Control": "no-store, private, max-age=0", "Pragma": "no-cache",
                       "X-Content-Type-Options": "nosniff"}

    async def review_candidate_source(batch_id: str, item_index: int):
        """The protected locator, its worker now, and that worker's freshly re-proved answer.

        Never the ordinary batch: a cleared or compacted batch keeps its candidate reachable, and
        nothing a caller sends (worker, URL, handle, credential) is used to find it.
        """
        from .backend.peers import studio_request
        locator = ledger.review_locator(batch_id, item_index)
        if locator is None:
            raise HTTPException(404, "this item has no review candidate")
        worker = next((row for row in monitor.registry if row["id"] == locator["studio"]), None)
        if worker is None:
            raise HTTPException(503, "the Voice Studio holding this review candidate is not connected")
        url, headers = studio_request(
            worker, f"/api/fleet/jobs/{locator['worker_job_id']}/review-candidate")
        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                response = await client.get(url, headers=headers, timeout=15.0)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "the Voice Studio holding this review candidate is not answering") from exc
        if response.status_code == 404:
            raise HTTPException(404, "the review candidate is no longer available")
        try:
            answer = response.json()
            facts = broker.checked_review_receipt(
                answer["receipt"], worker_job_id=locator["worker_job_id"],
                client_request_id=locator["client_request_id"])
            if (response.status_code != 200 or any(facts[key] != locator[key] for key in facts)
                    or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", answer["media"]["handle"])):
                raise ValueError("mismatch")
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(502, "the review candidate did not match its record") from exc
        return locator, worker, answer

    @app.get("/api/hub/jobs/{batch_id}/items/{item_index}/review-candidate")
    async def job_review_candidate(batch_id: str, item_index: int):
        """A refused narration section kept for listening. Inactive: never an artifact or result."""
        _locator, _worker, answer = await review_candidate_source(batch_id, item_index)
        return JSONResponse({
            "batch_id": batch_id, "item_index": item_index, "active": False,
            "disposition": "review_only", "deliverable": False, "imported": False,
            "receipt": answer["receipt"],
            "audio_url": f"/api/hub/jobs/{batch_id}/items/{item_index}/review-candidate/audio",
        }, headers=review_no_store)

    @app.get("/api/hub/jobs/{batch_id}/items/{item_index}/review-candidate/audio")
    async def job_review_candidate_audio(batch_id: str, item_index: int):
        """The candidate's WAV, counted and hashed against its receipt as it streams.

        The last chunk is held until the whole body is proved, so a body that is short, long or
        differs anywhere never reaches the caller complete.
        """
        locator, worker, answer = await review_candidate_source(batch_id, item_index)
        expected, digest = locator["pcm_bytes"], locator["pcm_sha256"]
        try:
            client, response = await open_worker_artifact(
                worker, f"/api/fleet/jobs/{locator['worker_job_id']}/review-candidate/media/"
                        f"{answer['media']['handle']}")
        except httpx.HTTPError as exc:
            raise HTTPException(503, "the Voice Studio holding this review candidate is not answering") from exc
        closed = False

        async def close_once() -> None:
            nonlocal closed
            if not closed:
                closed = True
                await response.aclose()
                await client.aclose()

        claimed = {name: response.headers.get_list(name)
                   for name in ("content-type", "content-length", "x-content-sha256")}
        if claimed != {"content-type": ["audio/wav"], "content-length": [str(expected)],
                       "x-content-sha256": [digest]}:
            await close_once()
            raise HTTPException(502, "the review candidate's audio did not match its record")

        async def verified():
            sha, count, held = hashlib.sha256(), 0, b""
            try:
                async for chunk in response.aiter_raw():
                    count += len(chunk)
                    if count > expected:
                        raise ValueError("review candidate body is longer than its receipt")
                    sha.update(chunk)
                    if held:
                        yield held
                    held = chunk
                if count != expected or sha.hexdigest() != digest:
                    raise ValueError("review candidate body does not match its receipt")
                yield held
            finally:
                await close_once()

        return StreamingResponse(
            verified(), media_type="audio/wav", background=BackgroundTask(close_once),
            headers={**review_no_store, "Content-Length": str(expected), "X-Content-SHA256": digest,
                     "Content-Disposition": f'inline; filename="{batch_id}-{item_index}-rejected-section.wav"'},
        )

    @app.delete("/api/hub/jobs/{batch_id}")
    async def cancel_job(batch_id: str, request: Request) -> dict[str, Any]:
        result = await broker.cancel_batch(
            batch_id, source="this-mac" if auth.presented_owner_token(request, token) else "gateway")
        if result is None:
            raise HTTPException(404, "unknown batch")
        return {"ok": True, **{key: value for key, value in result.items() if key != "batch"}}

    @app.post("/api/hub/transcription/jobs")
    async def create_transcription_job(
        files: list[UploadFile] = File(...), item_ids: list[str] = Form(...),
        model: str = Form(...), language: str | None = Form(None),
        word_timestamps: bool = Form(False), label: str | None = Form(None),
        project: str | None = Form(None), episode: str | None = Form(None),
        genstudio_execution: str | None = Form(None),
        routing: str | None = Form(None), request: Request = None,
    ) -> dict[str, Any]:
        if (not control_plane.accepts_customer_jobs()
                and not auth.presented_owner_token(request, token)):
            raise HTTPException(
                409, control_plane.refusal_for_remote_work())
        parsed, routing_error = routing_rule.parse(routing)
        if routing_error:
            raise HTTPException(400, routing_error)
        if parsed.startswith(routing_rule.MACHINE_PREFIX):
            # Pinning a machine that is not in the fleet is refused at submission, like the
            # other modalities, rather than left to sit in the queue with nothing to say.
            known = {s.get("machine", "local") for s in monitor.registry}
            target = parsed.split(":", 1)[1]
            if target not in known:
                raise HTTPException(400, f"no machine named {target!r} is in this fleet; known: "
                                         + ", ".join(sorted(known)))

        def admission(envelope: dict) -> Any:
            # Runs after the replay/duplicate checks, so an accepted batch is never refused here.
            refusal = _submission_readiness(envelope, monitor)
            if not refusal and broker.every_capable_mac_paused(
                    "transcription", envelope.get("model"), parsed):
                # Same refusal as `/api/hub/jobs`: nothing accepted, so the Gateway places it elsewhere.
                raise HTTPException(503, {"error": broker.NO_AVAILABLE_MACHINE_TEXT,
                                          "code": broker.NO_AVAILABLE_MACHINE,
                                          "detail": broker.NO_AVAILABLE_MACHINE_TEXT})
            return refusal

        try:
            batch, duplicate = await transcription_jobs.create_batch(
                files, item_ids, model, language, word_timestamps, label, project,
                episode, genstudio_execution,
                routing=parsed,
                admission_guard=admission,
                origin=("this-mac" if auth.presented_owner_token(request, token) else "gateway"),
            )
        finally:
            for upload in files:
                await upload.close()
        if start_background:
            transcription_jobs.start_dispatcher(monitor)
        return {"batch_id": batch["id"], "items": len(batch["items"]),
                "queued": sum(item["state"] == "queued" for item in batch["items"]),
                "duplicate": duplicate}

    @app.post("/api/hub/voice-references", status_code=201)
    async def stage_voice_reference(
        audio: UploadFile = File(...),
        transcript: str = Form(""),
        transcript_segments: str = Form(""),
        language: str = Form(""),
        ttl_seconds: float | None = Form(None),
        source_asset_id: str = Form(""),
        sha256: str = Form(""),
        display_name: str = Form(""),
    ) -> dict[str, Any]:
        """Stage one clone reference for later Voice jobs — one contract, two origins.

        Local origin: the app uploads a chosen clip and omits ``source_asset_id``.
        GenStudio origin: the controller pushes the clip with its durable
        ``source_asset_id``, which keeps the existing required-source semantics.

        Either way the returned ``asset_id`` is passed back verbatim as
        ``voice_reference_asset_id`` on ``/api/hub/jobs``; the Hub then streams
        the staged bytes to the Voice worker's clone route.  Nothing about the
        local origin relaxes GenStudio's type/size/checksum/transcript/expiry
        and privacy rules — only the upstream identity is optional.
        """
        from .backend import execution_assets
        try:
            audio_bytes = await audio.read()
        except Exception as exc:
            raise HTTPException(400, {"code": "REFERENCE_AUDIO_UNREADABLE",
                                      "detail": f"The uploaded reference could not be read: {exc}"}) from exc
        finally:
            await audio.close()
        segments: object = []
        if transcript_segments.strip():
            try:
                segments = json.loads(transcript_segments)
            except ValueError as exc:
                raise HTTPException(400, {
                    "code": "REFERENCE_TRANSCRIPT_SEGMENTS_INVALID",
                    "detail": "transcript_segments must be a JSON array of ordered segments.",
                }) from exc
        stage = (execution_assets.stage_voice_reference if source_asset_id.strip()
                 else execution_assets.stage_local_voice_reference)
        try:
            metadata = stage(
                audio_bytes=audio_bytes,
                filename=audio.filename or "reference.wav",
                declared_sha256=sha256,
                transcript=transcript,
                transcript_segments=segments,
                language=language,
                ttl_seconds=(execution_assets.DEFAULT_TTL_SECONDS if ttl_seconds is None
                             else int(ttl_seconds)),
                display_name=display_name,
                **({"source_asset_id": source_asset_id} if source_asset_id.strip() else {}),
            )
        except execution_assets.ExecutionAssetError as exc:
            raise HTTPException(400, {"code": exc.code, "detail": exc.detail}) from exc
        public = execution_assets.public(metadata)
        return {
            "asset_id": public["id"],
            "sha256": public["sha256"],
            "bytes": public["bytes"],
            "media_type": public["media_type"],
            "created_at": public["created_at"],
            "expires_at": public["expires_at"],
            "transcript_supplied": bool(transcript.strip() or segments),
            "origin": "genstudio" if source_asset_id.strip() else "local",
        }

    @app.get("/api/hub/voice-references/{asset_id}")
    def voice_reference_status(asset_id: str) -> dict[str, Any]:
        """Report whether a staged clone reference is still usable."""
        from .backend import execution_assets
        try:
            metadata, audio_bytes = execution_assets.read_voice_reference(asset_id)
        except execution_assets.ExecutionAssetError as exc:
            raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
        return {
            "asset_id": asset_id,
            "sha256": metadata.get("sha256"),
            "bytes": len(audio_bytes),
            "media_type": execution_assets.MIME_BY_EXTENSION.get(
                str(metadata.get("audio_extension") or ""), "application/octet-stream"
            ),
            "created_at": metadata.get("created_at"),
            "expires_at": metadata.get("expires_at"),
            "transcript_supplied": bool(
                str(metadata.get("transcript") or "").strip()
                or metadata.get("transcript_segments")
            ),
        }

    @app.get("/api/hub/transcription/jobs")
    def list_transcription_jobs() -> dict[str, Any]:
        return {"batches": transcription_jobs.list_batches(), "stats": transcription_jobs.statistics()}

    @app.get("/api/hub/transcription/jobs/{batch_id}")
    def get_transcription_job(batch_id: str) -> dict[str, Any]:
        batch = transcription_jobs.get_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown transcription batch")
        return transcription_jobs.summary(batch, include_metadata=True)

    @app.get("/api/hub/transcription/jobs/{batch_id}/items/{item_index}/artifact")
    def transcription_artifact(batch_id: str, item_index: int):
        batch = transcription_jobs.get_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown transcription batch")
        item = next((row for row in batch["items"] if row.get("index") == item_index), None)
        path = Path((item or {}).get("artifact_path") or "")
        try:
            safe = path.resolve().is_relative_to(transcription_jobs.ROOT.resolve())
        except OSError:
            safe = False
        if (not item or item.get("state") != "done" or not safe or not path.is_file()
                or path.stat().st_size == 0):
            raise HTTPException(404, "SRT artifact is not available")
        return FileResponse(path, media_type="application/x-subrip",
                            filename=f"{item['item_id']}.srt")

    @app.post("/api/hub/transcription/jobs/{batch_id}/items/{item_index}/ack")
    async def acknowledge_transcription(batch_id: str, item_index: int) -> dict[str, Any]:
        """Verified-delivery receipt for one Subtitle artifact, mirroring the Image ack."""
        return await transcription_jobs.acknowledge_item(monitor, batch_id, item_index)

    @app.post("/api/hub/transcription/jobs/{batch_id}/items/{item_index}/retry")
    async def retry_transcription_item(batch_id: str, item_index: int) -> dict[str, Any]:
        """Put one failed transcription back in the queue for the dispatcher."""
        try:
            return transcription_jobs.retry_failed_item(batch_id, item_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/hub/transcription/jobs/{batch_id}")
    async def cancel_transcription_job(batch_id: str) -> dict[str, Any]:
        batch = await transcription_jobs.cancel_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown transcription batch")
        return transcription_jobs.summary(batch)

    # ── the fleet peer surface ─────────────────────────────────────────────
    # A controller dispatches by talking to a peer Hub at /studio/<modality>/<path>, exactly
    # as this Hub talks to its own workers. Serving that surface is what makes a packaged app a
    # valid fleet member: the rest of the fleet logic already exists on the controller side and
    # only ever needed somewhere to point.
    #
    # The table itself lives in `backend/vocabulary.py`, because this routing table, the fleet
    # join's accepted set and the packaged worker descriptors are one vocabulary seen from three
    # directions - and each copy of it fell behind in turn.
    WORKER_FOR_MODALITY = vocabulary.WORKER_FOR_MODALITY

    def _proxy_target(path: str) -> tuple[str, str]:
        """Resolve a peer path to a local worker, or refuse it.

        The path is appended to a worker address this Hub owns, so it can only ever reach a
        local studio - but a traversal or an absolute URL would still let a caller reach worker
        routes it was never meant to, so both are refused outright rather than normalised.
        """
        cleaned = path.strip("/")
        if (not cleaned or ".." in cleaned.split("/")
                or "//" in cleaned or ":" in cleaned.split("/")[0]
                or "?" in cleaned or "#" in cleaned):
            raise HTTPException(400, "invalid studio path")
        segments = cleaned.split("/")
        modality = segments[0]
        worker_id = WORKER_FOR_MODALITY.get(modality)
        if worker_id is None:
            raise HTTPException(404, "unknown studio")
        return worker_id, "/" + "/".join(segments[1:])

    async def _proxy_to_worker(modality: str, path: str, request: Request,
                               record=None) -> Response:
        """Forward one fleet request to this Mac's own worker for that studio.

        The caller's credential is not passed on. The worker trusts only this Hub, so the
        request is re-signed with the worker's own token: a fleet credential reaching this Mac
        must never become a fleet credential reaching its workers.

        ``record`` is called once with the settled outcome of any work this node accepts, whichever
        credential asked for it - which paths those are, and why, is ``_PEER_JOB_RECORDERS``. The
        rule used to be that the peer surface passed nothing because a controller's jobs are the
        controller's. That stopped being true for every other modality: a controller submitting
        Image or Voice work creates the batch on this node through ``/api/hub/jobs``, so its work
        is listed here. Chat is synchronous and files nowhere else, so leaving its proxy
        unrecorded was the same work escaping this node's history rather than a different kind of
        work. The caller's identity does not decide whose history a job is in; the machine that
        ran it does.
        """
        worker_id, tail = _proxy_target(f"{modality}/{path}")
        worker = next((row for row in monitor.registry if row["id"] == worker_id), None)
        if worker is None:
            raise HTTPException(503, f"{worker_id.title()} worker is unavailable")
        body = await request.body()
        content_type = request.headers.get("content-type") or ""
        refusal, forwarded = await _peer_reference_door(worker, request, path, content_type)
        if refusal is not None:
            return _peer_refusal_response(refusal)
        if forwarded is not None:
            body = forwarded
        policy = _peer_lease_policy(path) if request.method == "POST" else None
        machine = str(worker.get("machine") or "local")
        if request.method == "POST" and f"{modality}/{path.strip('/')}" in _PEER_UNLEASED_PATHS:
            # Vision may name a list of models (one picture, depth plus Florence): each is admitted.
            for model in _request_models(body, content_type) or [
                    await _request_model(request, body, content_type)]:
                refusal = _peer_admission_refusal(worker, monitor, model, ignore_busy=True)
                if refusal is not None:
                    return _peer_refusal_response(refusal)
        owner = None
        queued_chat = None
        _prune_peer_leases()
        if policy is not None:
            model = await _request_model(request, body, content_type)
            # Bounded waiting is an advertised opt-in used by the Gateway. Ordinary
            # Chat proxy callers keep the worker's streaming and default-model contract.
            queued_completion = (
                modality == "chat" and path.strip("/") == "v1/chat/completions"
                and "X-StudioHub-Queue-Wait-Seconds" in request.headers)
            refusal = _peer_admission_refusal(
                worker, monitor, model, ignore_busy=queued_completion)
            if refusal is not None:
                return _peer_refusal_response(refusal)
            if queued_completion:
                from .backend import chat_priority
                try:
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise HTTPException(400, "Chat request must be an object")
                    chat_jobs._messages(payload.get("messages"))
                    if not model:
                        raise HTTPException(400, "Chat request must name a model")
                    for key in ("thinking", "stream"):
                        if key in payload and not isinstance(payload[key], bool):
                            raise HTTPException(422, f"{key} must be a boolean")
                    if payload.get("stream") is True:
                        raise HTTPException(422, "queued Chat completions require stream=false")
                    catalog = await monitor.scheduling_catalog(worker)
                    entry = next((row for row in (catalog or {}).get("models", [])
                                  if row.get("repo") == model or model in (row.get("aliases") or [])), None)
                    if entry is None or not broker._can_run(worker, entry, model, routing_rule.POOL):
                        return _peer_refusal_response({"code": "MODEL_NOT_READY", "detail": "this Chat model is not eligible on this Mac"})
                    request_id = request.headers.get("X-StudioHub-Request-ID") or uuid.uuid4().hex
                    queued_chat = chat_priority.register(request_id, machine, worker_id, model)
                    # The worker already supports a caller-supplied activity id. Correlate a lost
                    # response to that exact invocation, without saving or replaying its output.
                    payload["job_id"] = request_id
                    body = json.dumps(payload).encode()
                    raw_wait = request.headers.get("X-StudioHub-Queue-Wait-Seconds")
                    wait_seconds = float(raw_wait) if raw_wait is not None else chat_priority.MAX_WAIT_SECONDS
                    if not math.isfinite(wait_seconds) or wait_seconds < 0:
                        raise HTTPException(400, "queue wait seconds must be finite and nonnegative")

                    async def ready_chat():
                        from .backend import registry
                        if not registry.machine_enabled(machine):
                            return {"code": "MACHINE_DISABLED", "detail": "this Mac was paused while Chat waited"}, False
                        if not registry.studio_enabled(machine, worker_id):
                            return {"code": "STUDIO_DISABLED", "detail": "Chat Studio was paused while this request waited"}, False
                        if monitor.status.get(worker_id, {}).get("status") not in (None, "unknown", "up"):
                            return {"code": "STUDIO_NOT_RUNNING", "detail": "Chat Studio is not answering"}, False
                        if broker.in_maintenance(worker_id) or not broker._can_run(worker, entry, model, routing_rule.POOL):
                            return {"code": "MODEL_NOT_READY", "detail": "this Chat model is no longer eligible"}, False
                        if machine in broker.busy_machines():
                            # It is valid waiting demand; lease arbitration still prevents overlap.
                            return None, True
                        refusal = _peer_admission_refusal(worker, monitor, model, ignore_busy=True)
                        if refusal:
                            return refusal, False
                        decision, _note = await broker.prepare_machine_memory(monitor._client, worker, model, entry)
                        refusal = _peer_admission_refusal(worker, monitor, model, ignore_busy=True)
                        if refusal or decision != "run":
                            return refusal, False
                        if await broker.higher_priority_work_waiting(
                                machine, "chat", queued_chat["created_at"],
                                f"direct-chat:{queued_chat['id']}"):
                            return None, False
                        return None, True

                    def reserve_chat():
                        # is_disconnected() also awaits. Recheck owner controls after that final
                        # await, with no yield between this admission and the physical lease.
                        refusal = _peer_admission_refusal(worker, monitor, model)
                        if refusal:
                            if refusal.get("code") == "MACHINE_BUSY":
                                return None
                            raise HTTPException(409, refusal)
                        if broker.in_maintenance(worker_id):
                            raise HTTPException(409, {"code": "MACHINE_BUSY", "detail": "Chat dispatch was paused before reservation"})
                        return _reserve_peer_machine(worker_id, machine)

                    owner = await chat_priority.wait(
                        queued_chat, request, ready_chat, reserve_chat, wait_seconds)
                except (ValueError, TypeError):
                    if queued_chat is not None:
                        chat_priority.cancel(queued_chat["id"])
                    raise HTTPException(400, "invalid Chat request or queue wait seconds")
                except BaseException:
                    if queued_chat is not None:
                        chat_priority.finish(queued_chat)
                    raise
            else:
                if await broker.higher_priority_work_waiting(
                        machine, modality, time.time(), f"peer:{modality}:{uuid.uuid4().hex}"):
                    return _peer_refusal_response({
                        "code": "MACHINE_BUSY",
                        "detail": "older or higher-priority work is ready on this Mac"})
                refusal = _peer_admission_refusal(worker, monitor, model)
                if refusal is not None:
                    return _peer_refusal_response(refusal)
                if broker.in_maintenance(worker_id):
                    return _peer_refusal_response({
                        "code": "MACHINE_BUSY",
                        "detail": "this Studio entered maintenance before reservation"})
                owner = _reserve_peer_machine(worker_id, machine)
                if owner is None:
                    return _peer_refusal_response(
                        {"code": "MACHINE_BUSY",
                         "detail": f"{machine} is reserved for other work on this Mac"})

        def settled(**facts) -> None:
            """Hand one settled attempt to the caller's recorder, never failing the request.

            A job-history write is the Hub's own bookkeeping; it must not turn an answer the
            worker already produced into an error the operator sees.
            """
            if record is None:
                return
            try:
                record(**facts)
            except Exception:
                logging.getLogger("studiohub.chat").exception(
                    "the completed %s request could not be recorded as a job", worker_id)

        started_at = time.time()
        url = worker["base_url"] + tail
        if request.url.query:
            url += "?" + request.url.query

        def yielded_answer(status_code: int, content_type: str, content: bytes) -> Response | None:
            """A job this Mac handed back (workstation mode) reads as handed back, at once."""
            match = _PEER_JOB_PATH.match(tail)
            if request.method != "GET" or match is None or tail.rstrip("/") != match.group(0):
                return None
            payload = None
            if "json" in content_type:
                try:
                    payload = json.loads(content)
                except (ValueError, TypeError):
                    payload = None
            answer = workstation.rewrite_status(worker_id, match.group(1), status_code, payload)
            if answer is None:
                return None
            with _PEER_LEASE_LOCK:
                held_by = _PEER_JOBS.get((worker_id, match.group(1)))
            if held_by is not None:
                _release_peer_lease(held_by)
            return JSONResponse(answer[1], status_code=answer[0])
        headers = {"X-Hub-Token": worker["hub_token"]}
        if content_type:
            headers["Content-Type"] = content_type

        # **How long this job may take, computed from the job** (`chat_deadline`). The lane's flat
        # 1,800 s was derived from a real measurement — 30 scenes at ~403 tokens each on an M1 —
        # and then applied to every job, so it cut the longest one the fleet plans (a 100-prompt
        # pack) roughly in half while a one-line question held the same Mac for the same thirty
        # minutes. The caller's own `expected_output_tokens` is what makes the number exact: the
        # Hub cannot tell a 100-prompt pack from a question by reading the request, and Story
        # Studio already computes that length. No hint still sizes the job from the fleet's own
        # measured answers rather than from `max_tokens`, which is a ceiling and not an
        # expectation. Other lanes keep the published floor, unchanged.
        chat = None
        if modality == "chat" and request.method == "POST":
            chat = chat_deadline.chat_deadline(
                monitor, worker, request_body=body,
                hint=request.headers.get(chat_deadline.HINT_HEADER),
                floor_s=deadline_floor_seconds("chat"))

        def gave_up(failure: str, detail: str, code: str) -> Response:
            """This attempt ended with no answer: release the Mac, record why, tell the caller.

            Shared by the stall stop and the deadline stop, because they differ only in the words
            and the code. Both leave the worker possibly still generating — its own lane contract
            states it has no cancel endpoint — so for a queued Chat request both are *uncertain*
            exactly as a read timeout already was: the answer may exist and must not be filed as
            a refusal.
            """
            if owner is not None:
                if queued_chat is not None:
                    _retain_uncertain_chat(owner, queued_chat)
                else:
                    _release_peer_lease(owner)
            elif queued_chat is not None:
                queued_chat.update(state="refused", ready=False)
            if (handed_back := yielded_answer(502, "", b"")) is not None:
                return handed_back   # its worker was stopped to free the memory
            settled(request_body=body, started_at=started_at,
                    runtime_s=time.time() - started_at, worker_id=worker_id, machine=machine,
                    failure=failure, failure_detail=detail,
                    deadline_s=chat.seconds if chat is not None else None,
                    deadline_note=chat.note if chat is not None else "")
            return _peer_refusal_response({"code": code, "detail": detail})

        attempt = monitor._client.request(
            request.method, url, headers=headers, content=body or None,
            timeout=httpx.Timeout(
                chat.seconds if chat is not None
                else deadline_floor_seconds(modality_for_worker(worker_id)), connect=5.0),
        )
        try:
            upstream = (await chat_deadline.await_with_progress(monitor, worker_id, attempt)
                        if chat is not None else await attempt)
        except chat_deadline.ChatStalled as exc:
            # The job wrote no new token for the whole window. Distinct from the deadline stop
            # below in both the words and the code, because the two mean different things: one is
            # a job that went quiet, the other is a job that ran out of its own allowance.
            return gave_up("stalled", chat_deadline.stall_reason(exc.silent_for), "CHAT_STALLED")
        except httpx.ReadTimeout as exc:
            if chat is None:
                raise
            # **A read timeout on this lane is the deadline, precisely.** The chat lane is one
            # request for one answer (`stream: false`), so the worker sends nothing at all until
            # it is finished; the only thing that can make the read expire is the job's own
            # allowance running out. `TimeoutException` is deliberately *not* caught here: its
            # `ConnectTimeout` half means the worker was never reached, which is the existing
            # unreachable branch below and not a deadline at all.
            return gave_up("deadline", chat_deadline.deadline_reason(chat),
                           "CHAT_DEADLINE_REACHED")
        except (httpx.HTTPError, OSError) as exc:
            safe_connect = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            uncertain_chat = (queued_chat is not None and owner is not None
                              and not safe_connect)
            if uncertain_chat:
                _retain_uncertain_chat(owner, queued_chat)
            elif queued_chat is not None:
                queued_chat.update(state="refused", ready=False)
            if owner is not None and not uncertain_chat:
                _release_peer_lease(owner)
            if (handed_back := yielded_answer(502, "", b"")) is not None:
                return handed_back   # its worker was stopped to free the memory
            settled(request_body=body, started_at=started_at,
                    runtime_s=time.time() - started_at, worker_id=worker_id, machine=machine,
                    failure="worker_unreachable", failure_detail=str(exc) or type(exc).__name__)
            if queued_chat is not None and safe_connect:
                return _peer_refusal_response({"code": "STUDIO_NOT_RUNNING", "detail": "Chat Studio could not be reached; no work was accepted"})
            raise HTTPException(502, f"{worker_id.title()} worker request failed") from exc
        except BaseException:
            if queued_chat is not None and owner is not None:
                _retain_uncertain_chat(owner, queued_chat)
            # The handler was cancelled before the worker answered - a caller that went away, a
            # shutting-down server, or any layer that cancels rather than waits. Nothing was
            # answered, so the response cannot be asked whether real work continues: the route's
            # declared disconnect policy decides. A synchronous route leaves nothing running and
            # must not pin this Mac; an asynchronous submit keeps its lease, as an accepted job
            # does.
            if owner is not None and policy == PEER_DISCONNECT_RELEASE and queued_chat is None:
                _release_peer_lease(owner)
            settled(request_body=body, started_at=started_at,
                    runtime_s=time.time() - started_at, worker_id=worker_id, machine=machine,
                    failure="request_cancelled")
            raise
        if owner is not None:
            _settle_peer_lease(owner, worker_id, upstream)
            if queued_chat is not None:
                chat_priority.finish(queued_chat)
            if upstream.status_code < 400:
                workstation.note_job_accepted()
        if (handed_back := yielded_answer(upstream.status_code,
                                          upstream.headers.get("content-type") or "",
                                          upstream.content)) is not None:
            return handed_back
        _observe_peer_response(worker_id, tail, upstream)
        settled(request_body=body, started_at=started_at, runtime_s=time.time() - started_at,
                worker_id=worker_id, machine=machine, status_code=upstream.status_code,
                content_type=upstream.headers.get("content-type") or "",
                content=upstream.content)
        passthrough = {"X-StudioHub-Machine": machine}
        for name in ("content-type", "content-disposition", "x-content-sha256"):
            if name in upstream.headers:
                passthrough[name] = upstream.headers[name]
        content = upstream.content
        if request.method == "GET" and path.strip("/") == "catalog":
            content = _annotate_worker_catalog(
                monitor, worker, content, upstream.headers.get("content-type") or "")
        return Response(content=content, status_code=upstream.status_code,
                        headers=passthrough)

    async def _relay_chat_to_node(request: Request, record) -> Response | None:
        """Send a completion for a model only a node has to exactly one such node, or return None.

        The Gateway asks a site's controller; a model on a node and not on the controller's own
        Chat used to be refused here although the site published it. The relay is the pack
        dispatcher's own step (`chat_jobs.dispatch_once`) for one synchronous request: an eligible
        Chat studio, memory readiness, ranked arbitration and the controller's lease on that Mac,
        then the node's peer route, whose own door admits, leases and records it. One node, one
        POST: a named refusal is returned as it is and a lost answer is a bare 502 (it may have
        run), never a second submission. Readiness is re-read live immediately before the lease
        with no await between, so the queued lane's snapshot-generation fence has nothing to add.
        ponytail: no bounded wait here (the queue-wait opt-in stays local); a busy node answers
        MACHINE_BUSY and the Gateway defers.
        """
        from .backend import registry
        body = await request.body()
        model = await _request_model(request, body, request.headers.get("content-type") or "")
        chat_studios = [row for row in monitor.registry if row.get("modality") == "chat"]
        if not model or all(peers.is_local_studio(row) for row in chat_studios):
            return None

        def entry(catalog):
            return next((row for row in (catalog or {}).get("models", [])
                         if (row.get("repo") == model or model in (row.get("aliases") or []))
                         and broker.is_cached(row)), None)

        holders = []
        for studio in chat_studios:
            if entry(await monitor.scheduling_catalog(studio)) is None:
                continue
            if peers.is_local_studio(studio):
                return None   # this Mac's own Chat has it: the ordinary peer route serves it
            holders.append(studio)
        if not holders:
            return None
        held = {studio["id"] for studio in holders}
        # The second value is the output ceiling on offer, which this relay does not need: it is
        # read only to tell a stuck batch whose request is too large why it is not moving.
        eligible, _largest_output_ceiling = await chat_jobs._eligible_studios(monitor, model)
        for studio in eligible:
            if studio["id"] not in held:
                continue
            machine = studio["machine"]
            owner = f"relay:chat:{uuid.uuid4().hex}"
            # Re-read this candidate's own row here rather than reuse the holder pass: the memory
            # gate and the priority check below await (a sibling handoff is a peer request), and a
            # catalog observation that goes stale or fails in that window answers `None`. An
            # unprovable candidate is skipped, never dispatched with an assumed entry - `{}` would
            # make `_admission_requirements` skip the model's memory floor and send work to a Mac
            # nobody has checked.
            row = entry(await monitor.scheduling_catalog(studio))
            if row is None:
                continue
            decision, _note = await broker.prepare_machine_memory(
                monitor._client, studio, model, row)
            if (decision != "run" or await broker.higher_priority_work_waiting(
                    machine, "chat", time.time(), owner)):
                continue
            # No await between this recheck and the lease.
            if (registry.machine_enabled(machine) and registry.studio_enabled(machine, studio["id"])
                    and not broker.in_maintenance(studio["id"]) and peers.dispatch_ready(studio)
                    and broker.acquire_external_machine(machine, owner)):
                break
        else:
            paused = all(not registry.machine_enabled(row["machine"])
                         or not registry.studio_enabled(row["machine"], row["id"]) for row in holders)
            return _peer_refusal_response(
                {"code": "NO_AVAILABLE_MACHINE", "detail": "every Mac with this Chat model is paused"}
                if paused else
                {"code": "MACHINE_BUSY", "detail": "every Mac with this Chat model is busy"})

        def settled(**facts) -> None:
            if record is not None:
                try:
                    record(request_body=body, started_at=started_at,
                           runtime_s=time.time() - started_at, worker_id=studio["id"],
                           machine=machine, **facts)
                except Exception:
                    logging.getLogger("studiohub.chat").exception(
                        "the relayed Chat request could not be recorded as a job")

        url, headers = peers.studio_request(studio, "/v1/chat/completions")
        headers["Content-Type"] = request.headers.get("content-type") or "application/json"
        started_at = time.time()
        try:
            upstream = await monitor._client.post(
                url, headers=headers, content=body,
                timeout=httpx.Timeout(deadline_floor_seconds("chat"), connect=5.0))
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            settled(failure="worker_unreachable", failure_detail=str(exc) or type(exc).__name__)
            return _peer_refusal_response({"code": "STUDIO_NOT_RUNNING",
                                           "detail": f"{machine} could not be reached; no work was accepted"})
        except (httpx.HTTPError, OSError) as exc:
            settled(failure="worker_unreachable", failure_detail=str(exc) or type(exc).__name__)
            raise HTTPException(502, f"the Chat relay to {machine} lost its answer; it may have run") from exc
        except BaseException:
            settled(failure="request_cancelled")
            raise
        finally:
            broker.release_external_machine(machine, owner)
        if upstream.status_code in (401, 403):
            peers.invalidate(machine, rejected=True, modality="chat")
            return _peer_refusal_response({"code": "STUDIO_NOT_RUNNING",
                                           "detail": f"{machine} refused this controller's credential"})
        settled(status_code=upstream.status_code,
                content_type=upstream.headers.get("content-type") or "", content=upstream.content)
        passthrough = {"X-StudioHub-Machine": machine}
        if "content-type" in upstream.headers:
            passthrough["content-type"] = upstream.headers["content-type"]
        return Response(content=upstream.content, status_code=upstream.status_code,
                        headers=passthrough)

    @app.get("/studio/chat/requests/{request_id}")
    async def chat_request_status(request_id: str):
        from .backend import chat_priority
        return chat_priority.snapshot(request_id)

    @app.delete("/studio/chat/requests/{request_id}")
    async def cancel_chat_request(request_id: str):
        from .backend import chat_priority
        return chat_priority.cancel(request_id)

    @app.api_route("/studio/{modality}/{path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def studio_proxy(modality: str, path: str, request: Request) -> Response:
        """Serve the fleet peer surface over ``_proxy_to_worker``."""
        record = _peer_job_recorder(modality, path)
        if (request.method == "POST" and f"{modality}/{path.strip('/')}" == "chat/v1/chat/completions"
                and "X-StudioHub-Queue-Wait-Seconds" not in request.headers
                and (relayed := await _relay_chat_to_node(request, record)) is not None):
            return relayed
        return await _proxy_to_worker(modality, path, request, record=record)

    @app.post("/api/hub/fleet/nodes/{node_id}/transcribe")
    async def fleet_node_transcribe(node_id: str, request: Request) -> Response:
        """Run one test transcription on a chosen Mac in this group (0.6.147, model qualification).

        The Subtitle worker's own synchronous ``/api/transcribe`` (multipart: file, model, language,
        word_timestamps), answered in full - segments with their ``words``. A joined node is relayed
        to its own copy of this route (``local``), the way a model install is; this controller itself
        is its id or ``local``. Audited here and, for a node, again on the node.
        """
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        actor = str(hub_auth._fleet_actor(request).get("kind") or "hub")
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            startup_control.audit("transcribe_relay", {"node": wanted, "actor": actor})
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, "POST", "/transcribe", content=await request.body(),
                    content_type=request.headers.get("content-type") or "",
                    timeout=fleet.TRANSCRIBE_RELAY_TIMEOUT,
                    feature="test transcriptions from the Gateway (needs Studio Hub 0.6.147)"))
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
        startup_control.audit("transcribe", {"node": wanted, "actor": actor})
        # The peer surface's own spelling (`/studio/transcription/api/transcribe`): same admission,
        # machine reservation and worker re-signing as a controller's pooled transcription.
        return await _proxy_to_worker("transcription", "api/transcribe", request)

    @app.post("/api/hub/fleet/nodes/{node_id}/vision/analyze")
    async def fleet_node_vision(node_id: str, request: Request) -> Response:
        """Analyse one picture on a chosen Mac in this group (0.6.166, the Gateway's locality rule).

        The Vision worker's own `POST /v1/vision/analyze` (JSON), answered in full. A joined node is
        relayed to its own copy of this route (`local`), like a test transcription; this controller
        itself is its id or `local`, admitted at the door like the peer route and never leased.
        """
        from .backend import fleet_nodes as fleet
        wanted = node_id.strip().lower()
        if wanted not in ("local", fleet.this_machine()["id"].lower()):
            if control_plane.load_settings().get("role") != "controller":
                raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
            try:
                return JSONResponse(await fleet.relay_model(
                    wanted, "POST", "/vision/analyze", content=await request.body(),
                    content_type=request.headers.get("content-type") or "application/json",
                    timeout=fleet.VISION_RELAY_TIMEOUT,
                    feature="picture analysis from the Gateway (needs Studio Hub 0.6.166)"))
            except fleet.StartupRelayError as error:
                return JSONResponse(error.body, status_code=error.status)
        return await _proxy_to_worker("vision", "v1/vision/analyze", request,
                                      record=_peer_job_recorder("vision", "v1/vision/analyze"))

    @app.post("/api/hub/chat/completions")
    async def hub_chat_completions(request: Request) -> Response:
        """Local Chat completion for this app's own Chat page.

        Reuses the peer surface's worker lookup, admission reservation and lease settlement:
        the app talks to its own Hub, and the Hub re-signs the request with the worker token.
        The outcome is recorded as a finished one-item Chat job, so the Jobs page lists it with
        every other studio's work; the send itself stays synchronous.
        """
        return await _proxy_to_worker("chat", "v1/chat/completions", request,
                                      record=_peer_job_recorder("chat", "v1/chat/completions"))

    @app.get("/api/hub/chat/activity")
    async def hub_chat_activity(job_id: uuid.UUID, request: Request) -> Response:
        """Validated private Chat activity for one caller-named completion."""
        from .backend import activity

        upstream = await _proxy_to_worker("chat", "api/fleet/activity", request)
        if upstream.status_code >= 400:
            return upstream
        try:
            payload = json.loads(upstream.body)
        except (TypeError, ValueError):
            raise HTTPException(502, "Chat worker returned invalid activity")
        snapshot = activity.validate_snapshot(payload, expected_studio="chat")
        if snapshot is None:
            raise HTTPException(502, "Chat worker returned invalid activity")
        for name in ("active", "latest"):
            item = snapshot.get(name)
            if item is not None:
                try:
                    correlated = uuid.UUID(item["id"]) == job_id
                except (KeyError, TypeError, ValueError, AttributeError):
                    correlated = False
                if not correlated:
                    raise HTTPException(502, "Chat worker returned activity for another job")
        return JSONResponse(snapshot)

    from . import delivery, developer_gates, developer_tests, operator_controls, renders, statistics
    delivery.register_routes(app, monitor)
    operator_controls.register_routes(app, monitor)
    statistics.register_routes(app, monitor)
    # Developer Mode's node-side switch and attempt ledger (contract studio.developer-test.v1).
    # Fixed routes only; the controller relays them to this Mac's `local` path. The initial
    # verified OFF is created once here, so a missing file later reads as unverified.
    developer_tests.ensure_initial_setting()
    developer_tests.register_routes(app)
    # App-gate pilot: exactly seven fixed operations, current Fleet header only.
    developer_gates.register_routes(app)
    # The Render tab (owner, 2026-10-26). Reads the rows `render_jobs` already writes; on a
    # controller it adds each joined Mac's own list, over the same Fleet-credential read the rest
    # of the fleet uses. No writes, and nothing here reaches the render lane's own routes.
    from .backend import fleet_nodes as _fleet_for_renders
    renders.register_routes(
        app, database_path=ledger.DB_FILE, pool=_fleet_for_renders.pool,
        read_node=_fleet_for_renders.read_node, local_machine=_fleet_for_renders.this_machine,
        settings=control_plane.load_settings)
    return app


app = create_app()
