"""The packaged, private Studio Hub API surface.

This module intentionally exposes the durable job, transcription and settings
contracts. Legacy Pinokio control, updater and fleet-repair routes are not
registered by the packaged application.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from components import shelved_models
from . import output_storage, standalone_config
from .backend import auth, broker, cloud_guard, control_plane, ledger, model_ids, routing as routing_rule
from .backend import app_release, chat_jobs, release_sync, shared_voices, transcription_jobs, update_drain, vocabulary
from .backend.monitor import StudioMonitor

# The key one unusable manifest carries into the catalog so its row can say what is wrong
# without taking the other packages down. Private to this module: it is an implementation
# detail of `_model_catalog`, and no worker or manifest may set it.
_MANIFEST_ERROR = "_manifest_error"

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
# `video` is the only lane that differs today, and it differs on measurement: the LTX-2.3 runtime's
# own validated runs on an M2 Pro 32 GB are 44:38 for q8 HQ at 480x704x97 and ~15 min for the
# 1280x704x97 identity test. 900 s would cut the first off at a third of the way through.
MODALITY_DEADLINE_FLOOR_S: dict[str, float] = {
    # The LTX-2.3 runtime's own measured runs on an M2 Pro 32 GB: 44:38 for q8 HQ at 480x704x97,
    # ~15 min for the 1280x704x97 identity test. A 900 s wall cuts the first off a third through.
    "video": 7200.0,
    # Chat is the LLM lane, and GatewayKH measured the workload that sets this floor: 30 scenes at
    # ~403 tokens each is 12,090 tokens, 1,008 s on an M1. They asked for the whole Hub ceiling to
    # go to 1,800 s to cover it, and stated the cost they were accepting — *"every job may hang 30
    # min instead of 15, including fast ones."* **That cost is not necessary and is not paid here.**
    # Because this table is per generator, chat gets 1,800 s and subtitle, voice, music and image
    # keep 900 s, so a broken subtitle job is still noticed in 15 minutes rather than 30. This is
    # the whole reason the deadline stopped being one flat number.
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
    "voice": 1800.0,
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
        paths = sorted(root.glob("*.json"))
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
    package_id = str(manifest.get("package_id") or "unknown")
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
    "voice-mlx": ("voice", "voice"),
    "voice-vits": ("voice", "voice"),
    "subtitle-mlx": ("subtitle", "transcription"),
    "music-mlx": ("music", "music"),
    # The Music lane's second engine. YuE2 runs in its own pinned runtime because the port pins
    # `mlx==0.32.2` while the Music overlay carries `mlx==0.31.2`; the worker picks the
    # interpreter from the manifest's engine, exactly as the Video lane does for its two.
    "music-yue2": ("music", "music"),
    "chat-mlx": ("chat", "chat"),
    # Video is one engine: `video-ltx`, the LTX-2.3 22B pack (Lance was retired in 0.6.120).
    "video-ltx": ("video", "video"),
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


def _keep_all_models() -> bool:
    return standalone_config.load_device_settings()["effective"]["keep_all_models"]


def _shelved_reason(manifest: dict[str, Any]) -> str:
    return (f"Not on this Mac: {manifest['package_id']} is a shelved model, which this Mac does not keep. "
            f"It stays on the SSD kits and works on a Mac marked \"keep all models\" "
            f"(Hub setting keep_all_models).")


def _shelved_refusal(manifest: dict[str, Any]) -> None:
    """A shelved model is not brought onto a Mac that does not keep it (it would be moved out again)."""
    if shelved_models.manifest_shelved(manifest) and not _keep_all_models():
        raise HTTPException(409, _shelved_reason(manifest))


def shelve_installed_models() -> list[str]:
    """At start-up, a Mac that is not marked keep-all moves its installed shelved models to the Trash Folder."""
    return shelved_models.shelve_installed(_model_library(), _model_catalog().values(),
                                           output_storage.trash_root(), _keep_all_models())


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
    shelved = shelved_models.manifest_shelved(manifest)
    away = shelved and receipt["state"] == "not_installed" and not _keep_all_models()
    return {
        **receipt,
        # A shelved model is still offered and usable where it is; on a Mac that does not keep it, its
        # absence is "not on this Mac", not a fault (and it is never downloaded back by itself).
        "shelved": shelved,
        **({"reason": _shelved_reason(manifest)} if away else {}),
        **standalone_config.model_readiness({
            "files_state": receipt["state"],
            "minimum_macos": manifest["minimum_macos"],
            "runtime_identity": (worker or {}).get("runtime_identity", {"compatible": False}),
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
    if update_drain.draining():
        # Re-routable: the gateway files MACHINE_BUSY as a location refusal and tries another Mac.
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
    if state.get("manifest_error"):
        return {"code": "MODEL_NOT_READY",
                "detail": "this model's package manifest is unusable on this Hub",
                "readiness": state}
    expected_modality = _modality_for_engine(str(manifest.get("engine") or ""))
    if expected_modality != envelope.get("modality"):
        return {"code": "MODEL_MODALITY_MISMATCH", "detail": "model is not available for this job modality"}
    if state["dispatch_state"] != "eligible":
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
_PEER_TERMINAL_STATES = frozenset({
    "done", "error", "failed", "cancelled", "canceled", "expired",
})
_PEER_JOB_PATH = re.compile(r"^/api/(?:generate/jobs|generate/transcribe/jobs)/([^/]+)")


def _release_peer_lease(owner: str) -> None:
    with _PEER_LEASE_LOCK:
        row = _PEER_LEASES.pop(owner, None)
        for key, value in list(_PEER_JOBS.items()):
            if value == owner:
                _PEER_JOBS.pop(key, None)
    if row is not None:
        broker.release_external_machine(row[0], owner)


def _prune_peer_leases() -> None:
    """Drop peer leases whose job was never observed terminal and whose safety
    window elapsed, so a vanished controller cannot reserve a Mac for ever."""
    now = time.monotonic()
    with _PEER_LEASE_LOCK:
        expired = [owner for owner, (_machine, expiry) in _PEER_LEASES.items()
                   if expiry <= now]
    for owner in expired:
        _release_peer_lease(owner)


async def _peer_lease_loop() -> None:
    """Expire abandoned peer leases even when no further peer request arrives.

    Without this, a controller that vanished mid-job would leave this Mac's own
    scheduler treating the machine as forever busy.
    """
    import asyncio

    try:
        while True:
            await asyncio.sleep(60.0)
            _prune_peer_leases()
    except asyncio.CancelledError:
        return


def _reserve_peer_machine(worker_id: str, machine: str) -> str | None:
    owner = f"peer:{worker_id}:{uuid.uuid4().hex}"
    if not broker.acquire_external_machine(machine, owner):
        return None
    with _PEER_LEASE_LOCK:
        _PEER_LEASES[owner] = (machine, time.monotonic() + _PEER_LEASE_TTL_S)
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
        _release_peer_lease(owner)
        return
    with _PEER_LEASE_LOCK:
        row = _PEER_LEASES.get(owner)
        if row is not None:
            _PEER_LEASES[owner] = (row[0], time.monotonic() + _PEER_LEASE_TTL_S)
        _PEER_JOBS[(worker_id, job[0])] = owner


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


# Which proxied work leaves a job in *this* node's history, keyed by the peer path that creates
# it. This is the one place the choice is made, for both surfaces that reach `_proxy_to_worker`.
#
# The old rule was "a controller's jobs are not this node's", and it does not survive contact
# with the other modalities: a controller submitting Image or Voice work POSTs `/api/hub/jobs`
# *on this node*, which creates the batch here, so that work is listed here. Chat has no broker
# lane - `broker.MODALITY` has no `chat`, deliberately: a completion is synchronous and owns its
# own job module - so this proxy is the only point at which its work could be filed at all.
# Whose history a job is in follows the Mac that ran it, not the credential that asked.
#
# Nothing is recorded for work this node merely forwards to an asynchronous worker: those
# batches are created by the submit route itself, and recording them here as well would file
# every accepted job twice.
_PEER_JOB_RECORDERS = {
    "chat/v1/chat/completions": chat_jobs.record_page_completion,
}


def _peer_job_recorder(modality: str, path: str):
    """The recorder for one proxied work path, or ``None`` when this node keeps no job for it."""
    return _PEER_JOB_RECORDERS.get(f"{modality}/{path.strip('/')}")


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


def _peer_admission_refusal(worker: dict[str, Any], monitor: StudioMonitor,
                            model: str) -> dict[str, Any] | None:
    """This Mac's own answer, applied before its worker sees any peer request."""
    from .backend import registry

    machine = str(worker.get("machine") or "local")
    studio_id = str(worker.get("id") or "")
    if not registry.machine_enabled(machine):
        return {"code": "MACHINE_DISABLED", "detail": f"{machine} is disabled on this Mac"}
    if not registry.studio_enabled(machine, studio_id):
        return {"code": "STUDIO_DISABLED", "detail": f"{studio_id} is disabled on this Mac"}
    if model:
        paused = broker.model_block_state(studio_id, model)
        if paused is not None:
            return {"code": "MODEL_PAUSED",
                    "detail": str(paused.get("reason") or "model is paused on this Mac"),
                    "readiness": paused}
    if machine in broker.busy_machines():
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
    settings = _control_plane.load_settings()
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ledger.prepare_database()
        output_storage.prepare()
        output_storage.recover_process_leases()
        broker.restore_batches()
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
        release_mirror = None
        if start_background:
            # The output size / age limits (device settings), enforced for every studio - not only in
            # development. See job_storage.budget_loop.
            import asyncio as _asyncio
            from .backend import job_storage
            storage_sweep = _asyncio.create_task(
                job_storage.budget_loop(standalone_config.cleanup_interval_seconds()))
            peer_lease_sweep = _asyncio.create_task(_peer_lease_loop())
            # A node fetches the release its controller holds; a no-op on any Mac that is not one.
            release_mirror = _asyncio.create_task(release_sync.loop())
            try:
                moved = shelve_installed_models()
                if moved:
                    print(f"[hub] moved shelved models to the Trash Folder: {', '.join(moved)}", flush=True)
            except (OSError, ValueError):
                pass
            output_storage.start_background(
                lambda: {worker.get("machine", "local") for worker in monitor.registry},
                standalone_config.load_device_settings,
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
            await control_plane.runtime.start(monitor, _app_version())
        try:
            yield
        finally:
            if start_background:
                if storage_sweep is not None:
                    storage_sweep.cancel()
                    try:
                        await storage_sweep
                    except Exception:
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
                await shared_voices.stop()
                await broker.stop_dispatcher()
                await transcription_jobs.stop()
                await control_plane.runtime.stop()
                await monitor.stop()
                from . import model_downloads
                model_downloads.shutdown()

    app = FastAPI(title="Studio Hub", version=_app_version(), lifespan=lifespan)
    app.middleware("http")(auth.make_middleware(token))
    app.state.monitor = monitor

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        # The bind is here, on the route that answers "is this Mac there?", rather than only on
        # `/api/hub/settings`: that route is owner-only and about output storage, so a controller
        # could not read it, and a controller is exactly who needs to know that a node came up
        # loopback-only instead of discovering it as a timeout.
        return {"ok": True, "workers": len(monitor.registry),
                "role": control_plane.load_settings()["role"], "bind": _bind_facts(),
                "update_draining": update_drain.draining()}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": _app_version(), "build": _app_build(),
                "source": "studiohub-mac@06e893e1643bffbcbd162fae122e5a4d0f29e283"}

    @app.get("/api/hub/settings")
    def settings() -> dict[str, Any]:
        # The same `_bind_facts()` the health route publishes, so the two cannot disagree.
        return {**standalone_config.load_device_settings(), "bind": _bind_facts()}

    @app.put("/api/hub/settings")
    def save_settings(body: dict[str, Any]) -> dict[str, dict[str, int | bool]]:
        try:
            value = standalone_config.preview_device_settings(**body)
            return standalone_config.save_device_settings(
                max_bytes=value["max_bytes"], retention_days=value["retention_days"],
                cleanup_enabled=value["cleanup_enabled"],
                cleanup_interval_seconds=value["cleanup_interval_seconds"],
                minimum_free_bytes=value["minimum_free_bytes"],
                keep_all_models=value["keep_all_models"],
            )
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
                "trash": {"bytes": output_storage.trash_bytes(max_age=10), "days": output_storage.TRASH_DAYS}}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/settings/restore-defaults")
    def restore_settings() -> dict[str, dict[str, int]]:
        return standalone_config.restore_device_defaults()

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
        return {"host": host, "studios": studios, "proxy": proxy, "version": _app_version()}

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
        from .backend import fleet_nodes as fleet
        from .backend import ledger, peers
        from .backend import resources as resource_stats
        measured = ledger.stats().get("by_machine") or {}
        local_role = str(control_plane.load_settings().get("role") or "standalone")
        pool = await fleet.pool()
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
                row = {
                    "machine": machine,
                    "label": "This Mac",
                    "role": local_role,
                    "reachable": True,
                    "version": _app_version(),
                    "health": health,
                    "detail": detail,
                    "studios": {"total": len(on_this_machine),
                                "up": sum((monitor.status.get(item["id"]) or {}).get("status") == "up"
                                          for item in on_this_machine)},
                }
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
                else:
                    reachable = bool(snapshot.get("reachable") and snapshot.get("auth"))
                    health = ("up" if snapshot.get("status") == "connected"
                              else str(snapshot.get("status") or "unknown"))
                    detail = None if snapshot else "this Mac has not reported in yet"
                    version = snapshot.get("version")
                    host = snapshot.get("host") or {}
                row = {
                    "machine": machine,
                    "label": str((member or {}).get("label")
                                 or (studio.get("title") or machine).split(" · ")[0]),
                    "role": str((member or {}).get("role") or "agent"),
                    "reachable": reachable,
                    "version": version,
                    "health": health,
                    "detail": detail,
                }
            stats = measured.get(machine) or {}
            rows.append({
                **row,
                "address": (member or {}).get("address"),
                "memory_gb": host.get("total_gb"),
                "available_gb": host.get("available_gb"),
                "disk_free_gb": host.get("disk_free_gb"),
                "disk_total_gb": host.get("disk_total_gb"),
                "chip": host.get("chip"),
                "jobs": stats.get("count", 0),
                "average_seconds": stats.get("avg_s"),
                "modalities": stats.get("modalities") or {},
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
                "memory_gb": host.get("total_gb"), "available_gb": host.get("available_gb"),
                "disk_free_gb": host.get("disk_free_gb"),
                "disk_total_gb": host.get("disk_total_gb"), "chip": host.get("chip"),
                "jobs": (measured.get(machine) or {}).get("count", 0),
                "average_seconds": (measured.get(machine) or {}).get("avg_s"),
                "modalities": modalities,
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
        if node_credential is not None:
            fleet.set_node_credential(str(node_credential))
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
        from .backend import fleet_nodes as fleet
        from .backend import peers
        peers.set_fleet_token("")
        # **The credential the old controller issued goes too.** A Mac that is leaving — or being
        # sold — must not keep a working key to the group it just left, and the app cannot delete
        # it because the file is the Hub's own business (`0600`, in the Hub's data directory).
        # Found by the native work: the app cleared its copy and the Hub's stayed, which is inert
        # after leaving but is exactly the wrong thing to find on a Mac in someone else's hands.
        fleet.set_node_credential(None)
        control_plane.save_settings({
            "fleet_credential_origin": "minted",
            "role": "standalone",
            "parent_controller_url": None,
        })
        return {"ok": True, "left": True}

    @app.post("/api/hub/fleet/nodes", status_code=201)
    async def fleet_join(body: dict[str, Any], request: Request) -> dict[str, Any]:
        from .backend import auth as hub_auth
        from .backend import fleet_nodes as fleet
        try:
            joined = fleet.join(body, actor=hub_auth._fleet_actor(request))
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

    @app.get("/api/hub/fleet/nodes/{node_id}/status")
    async def fleet_node_status(node_id: str, request: Request,
                                name: str | None = None) -> dict[str, Any]:
        """Does this controller still list this Mac? `member`, `removed` (with when) or `unknown`.

        How a removal made here reaches the Mac itself: the node asks on its own tick and clears
        its membership when the answer is `removed`. Read-only and mints no credential, so it can
        be asked every minute. A node credential may ask only about its own id.

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
        return fleet.status(asked)

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
        pushes the whole disk image) and whether it holds this release's manifest.
        """
        trusted = app_release.trusted_publisher()
        release = app_release.load_release()
        return {
            "release": release,
            "trusted_publisher": None if trusted is None else {
                "publisher_id": trusted.get("publisher_id"),
                "fingerprint": trusted.get("fingerprint"),
                "public_key": trusted.get("public_key"),
            },
            "delta": {"schema": app_release.fm.SCHEMA,
                      "manifest": app_release.held_manifest(release) is not None},
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
        return await update_drain.request(bool(body.get("want")), release_sync.controller_target())

    @app.post("/api/hub/fleet/update-slot")
    def update_slot(body: dict[str, Any], request: Request) -> dict[str, Any]:
        """A group member asks this controller for the group's one update slot."""
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
            _shelved_refusal(manifest)
            model_packages.import_folder(manifest, Path(body.get("source_folder", "")), _model_library())
            return _model_state(manifest, catalog, monitor)
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/hub/model-packages/{package_id}/download")
    def download_model_package(package_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from . import model_downloads
        try:
            catalog = _model_catalog()
            manifest = catalog[package_id]
            _shelved_refusal(manifest)
            sources = body.get("sources")
            if sources is not None and not isinstance(sources, dict):
                raise ValueError("sources must be a map of model paths to HTTPS mirrors")
            return model_downloads.start(package_id=package_id, manifest=manifest,
                                         catalog=catalog, library=_model_library(), sources=sources)
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/hub/model-downloads/{run_id}")
    def model_download_status(run_id: str) -> dict[str, Any]:
        from . import model_downloads
        run = model_downloads.status(run_id)
        if run is None:
            raise HTTPException(404, "unknown model download")
        return run

    @app.delete("/api/hub/model-downloads/{run_id}")
    def cancel_model_download(run_id: str) -> dict[str, Any]:
        from . import model_downloads
        run = model_downloads.cancel(run_id)
        if run is None:
            raise HTTPException(404, "unknown model download")
        return run

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
    def set_machine_label(body: dict[str, Any]) -> dict[str, Any]:
        """Name this Mac the way the operator wants it shown.

        The name is a label, not an identity: the Hub's machine key stays "local", routing and fleet
        registration never depend on it, and renaming a Mac after nodes have joined does not orphan
        anything. `registry.set_label` has existed for this the whole time; no route exposed it, so
        the only name an operator could ever have was their hostname.
        """
        from .backend import registry
        name = str(body.get("name") or "").strip()
        if len(name) > 120:
            raise HTTPException(400, "the name must be at most 120 characters")
        registry.set_label("local", name)
        return {"settings": control_plane.public_settings()}

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
            try:
                refusal = _submission_readiness(envelope, monitor)
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
            if refusal:
                raise HTTPException(409, refusal)
        result = broker.submit_batch(envelope)
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

    @app.delete("/api/hub/jobs/{batch_id}")
    async def cancel_job(batch_id: str) -> dict[str, Any]:
        result = await broker.cancel_batch(batch_id)
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
        try:
            batch, duplicate = await transcription_jobs.create_batch(
                files, item_ids, model, language, word_timestamps, label, project,
                episode, genstudio_execution,
                routing=parsed,
                admission_guard=lambda envelope: _submission_readiness(envelope, monitor),
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
        policy = _peer_lease_policy(path) if request.method == "POST" else None
        machine = str(worker.get("machine") or "local")
        owner = None
        _prune_peer_leases()
        if policy is not None:
            model = await _request_model(request, body, content_type)
            refusal = _peer_admission_refusal(worker, monitor, model)
            if refusal is not None:
                return _peer_refusal_response(refusal)
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
        headers = {"X-Hub-Token": worker["hub_token"]}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            upstream = await monitor._client.request(
                request.method, url, headers=headers, content=body or None,
                timeout=httpx.Timeout(
                    deadline_floor_seconds(modality_for_worker(worker_id)), connect=5.0),
            )
        except (httpx.HTTPError, OSError) as exc:
            if owner is not None:
                _release_peer_lease(owner)
            settled(request_body=body, started_at=started_at,
                    runtime_s=time.time() - started_at, worker_id=worker_id, machine=machine,
                    failure="worker_unreachable", failure_detail=str(exc) or type(exc).__name__)
            raise HTTPException(502, f"{worker_id.title()} worker request failed") from exc
        except BaseException:
            # The handler was cancelled before the worker answered - a caller that went away, a
            # shutting-down server, or any layer that cancels rather than waits. Nothing was
            # answered, so the response cannot be asked whether real work continues: the route's
            # declared disconnect policy decides. A synchronous route leaves nothing running and
            # must not pin this Mac; an asynchronous submit keeps its lease, as an accepted job
            # does.
            if owner is not None and policy == PEER_DISCONNECT_RELEASE:
                _release_peer_lease(owner)
            settled(request_body=body, started_at=started_at,
                    runtime_s=time.time() - started_at, worker_id=worker_id, machine=machine,
                    failure="request_cancelled")
            raise
        if owner is not None:
            _settle_peer_lease(owner, worker_id, upstream)
        _observe_peer_response(worker_id, tail, upstream)
        settled(request_body=body, started_at=started_at, runtime_s=time.time() - started_at,
                worker_id=worker_id, machine=machine, status_code=upstream.status_code,
                content_type=upstream.headers.get("content-type") or "",
                content=upstream.content)
        passthrough = {}
        for name in ("content-type", "content-disposition", "x-content-sha256"):
            if name in upstream.headers:
                passthrough[name] = upstream.headers[name]
        return Response(content=upstream.content, status_code=upstream.status_code,
                        headers=passthrough)

    @app.api_route("/studio/{modality}/{path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def studio_proxy(modality: str, path: str, request: Request) -> Response:
        """Serve the fleet peer surface over ``_proxy_to_worker``."""
        return await _proxy_to_worker(modality, path, request,
                                      record=_peer_job_recorder(modality, path))

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

    from . import delivery, operator_controls, statistics
    delivery.register_routes(app, monitor)
    operator_controls.register_routes(app, monitor)
    statistics.register_routes(app, monitor)
    return app


app = create_app()
