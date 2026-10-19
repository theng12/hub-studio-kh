"""Get a held release onto a node, and apply it: the two halves of a unified update.

**Mirroring (a node's half of HUB-15).** A Mac that joined a controller polls it every few minutes.
When the controller holds a release *and is already running that build* (controllers update before
their nodes), the node verifies the gateway's signature against the publisher key it pinned from
that controller at join, countersigns the descriptor with its own key, and stores it - descriptor
and per-file manifest, not the disk image. Its own app keeps reading its own Hub, exactly as it did
when the release had been pushed to it directly.

**Applying.** ``POST /api/hub/release/stage`` runs :mod:`release_delta` against the held manifest:
the installed app is cloned, only the files that differ are fetched (from this Mac's store, or from
its controller by sha256), and the result is verified. The native app then swaps it in. Every
refusal ends as ``state: "fallback"``, which the app answers by downloading the disk image as it
always has - except ``state: "not_ready"``: a controller still receiving the release's files, which
the app simply asks again about later (a node whose controller cannot supply a file yet answers
the same).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from . import app_release, fleet_nodes, peers, release_delta
from . import file_manifest as fm

log = logging.getLogger("studiohub.release_sync")

POLL_INTERVAL_S = 300.0
FIRST_POLL_DELAY_S = 45.0
TIMEOUT = httpx.Timeout(30.0, connect=5.0)
BLOB_ATTEMPTS = 3
# How long a controller's stage waits for the blobs a gateway is still pushing (~33 blobs over ~2 min
# in the 0.6.151 rollout), and how often it looks.
BLOB_WAIT_S = 15 * 60
BLOB_WAIT_POLL_S = 5.0

# What the last poll concluded, for an operator asking why a node has not updated.
last_sync: dict[str, Any] = {"at": None, "result": None, "error": None}


def last_sync_report() -> dict[str, Any]:
    """The last poll in the one shape the route and the app read: ``ok``, the refusal in plain
    words (``error``), what it concluded (``result``) and when (``at``, epoch seconds).

    Published by ``GET /api/hub/release`` so a Mac whose updates are being refused names its own
    fault on the screen that configures updates, instead of looking merely out of date."""
    snapshot = dict(last_sync)
    snapshot["ok"] = snapshot.get("at") is not None and snapshot.get("error") is None
    return snapshot


def controller_target() -> tuple[str, dict[str, str]] | None:
    """The controller this Mac joined and the credential to present, or None when this Mac is not
    a node. A node presents its own credential (``NODE_ALLOWED``); the group token only before
    the first join has been answered."""
    member = fleet_nodes.membership()
    if not member["member"] or member["role"] != "agent":
        return None
    token = fleet_nodes.node_credential() or peers.current_fleet_token()
    return member["controller_url"], ({"X-Hub-Token": token} if token else {})


# ── mirroring ──────────────────────────────────────────────────────────────


async def _get(client: httpx.AsyncClient, base: str, headers: Mapping[str, str], path: str):
    response = await client.get(f"{base}{path}", headers=dict(headers), timeout=TIMEOUT)
    response.raise_for_status()
    return response


def _trusted_for(descriptor: Mapping[str, Any], presented: Mapping[str, Any] | None) -> bytes:
    """The gateway key to verify this descriptor with: the one this Mac already trusts, or - the
    first time, and only then - the one its controller presents (trust on first use, the owner's
    decision for nodes). A key that differs from an existing pin is refused, never replaced."""
    trusted = app_release.trusted_publisher()
    if trusted is None and presented and isinstance(presented.get("public_key"), str):
        trusted = app_release.pin_publisher(presented["public_key"], presented.get("publisher_id"))
    if trusted is None:
        raise app_release.ReleaseRefused(409, "this Mac trusts no gateway key and its controller presented none")
    if str(descriptor.get("publisher_key_id", "")).lower() != trusted["fingerprint"]:
        raise app_release.ReleaseRefused(
            400, "the release is signed by a gateway key other than the one this Mac trusts")
    return bytes.fromhex(trusted["public_key"])


async def sync_once(client: httpx.AsyncClient | None = None) -> str:
    """One poll. Returns a short sentence saying what it concluded; raises only on a fault."""
    target = controller_target()
    if target is None:
        return "not a node"
    base, headers = target
    owned = client is None
    client = client or httpx.AsyncClient(follow_redirects=False)
    try:
        build = (await _get(client, base, {}, "/api/version")).json().get("build")
        answer = (await _get(client, base, headers, "/api/hub/release")).json()
        release = answer.get("release")
        if not isinstance(release, dict):
            return "the controller holds no release"
        # Controllers before nodes: a node is only offered a release its own controller is
        # already running, so an update never reaches a node the controller could not serve.
        if release.get("build") != build:
            return "the controller has not installed this release yet"
        held = app_release.load_release()
        wants_files = "files_sha256" in release
        if (held and held.get("signature") == release.get("signature")
                and (not wants_files or app_release.held_manifest(held) is not None)):
            return "already mirrored"
        # The gateway signed the descriptor before the controller added its own key and
        # countersignature, so those are set aside to verify (and re-added by this Mac's own).
        release = {key: value for key, value in release.items()
                   if key not in (app_release.CONTROLLER_KEY_ID_FIELD, app_release.CONTROLLER_SIGNATURE_FIELD)}
        app_release.validate_descriptor(release)
        key = await asyncio.to_thread(_trusted_for, release, answer.get("trusted_publisher"))
        if not await asyncio.to_thread(app_release.verify, release, release["signature"], key):
            raise app_release.ReleaseRefused(400, "the gateway signature does not verify")
        files: dict[str, bytes] | None = None
        if wants_files and (answer.get("delta") or {}).get("manifest"):
            files = await _mirror_manifest(client, base, headers, release)
        await asyncio.to_thread(app_release.store_release, release, manifest=files)
        return f"mirrored {release['version']} ({release['build']})" + ("" if files else " without a manifest")
    finally:
        if owned:
            await client.aclose()


async def _mirror_manifest(client, base, headers, release) -> dict[str, bytes]:
    index_bytes = (await _get(client, base, headers, "/api/hub/release/files")).content
    try:
        index = fm.check_index(json.loads(index_bytes))
        if (index["files_sha256"], index["files_count"], index["files_bytes"]) != (
                release["files_sha256"], release["files_count"], release["files_bytes"]):
            raise fm.ManifestError("the controller's manifest is not the one the signed descriptor names")
        files: dict[str, bytes] = {fm.INDEX_NAME: fm.index_bytes(index)}
        for name in index["groups"]:
            compressed = (await _get(client, base, headers, f"/api/hub/release/files/{name}")).content
            fm.check_group(index, name, fm.gunzip_limited(compressed))
            files[name] = compressed
    except (fm.ManifestError, ValueError) as exc:
        raise app_release.ReleaseRefused(400, str(exc)) from exc
    return files


async def loop() -> None:
    """Started from the Hub's lifespan. A fault is logged and remembered, never fatal."""
    await asyncio.sleep(FIRST_POLL_DELAY_S)
    while True:
        try:
            last_sync.update(result=await sync_once(), error=None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a poll must never stop the next one
            last_sync.update(result=None, error=str(exc) or type(exc).__name__)
            log.warning("release sync: %s", last_sync["error"])
        last_sync["at"] = time.time()
        await asyncio.sleep(POLL_INTERVAL_S * random.uniform(0.8, 1.2))


# ── fetching one blob from the controller (blocking; runs in the stage thread) ─────────


def _remote_blob(sha: str, destination: Path, size: int) -> None:
    target = controller_target()
    if target is None:
        raise release_delta.DeltaRefused("missing_blob", f"this Mac holds no copy of {sha} and has no controller to ask")
    base, headers = target
    last: Exception | None = None
    # A controller still receiving the release, or restarting into its own update, cannot supply a
    # blob for a while; and a node holds no disk image of its own (the relay would 404). So the node
    # keeps asking, as long as a controller waits for its own blobs, then answers `not_ready`.
    deadline = time.monotonic() + BLOB_WAIT_S
    attempt = 0
    while True:
        try:
            with httpx.stream("GET", f"{base}/api/hub/release/blob/{sha}", headers=headers,
                              timeout=httpx.Timeout(60.0, connect=5.0)) as response:
                response.raise_for_status()
                with destination.open("wb") as out:
                    for chunk in response.iter_raw():
                        out.write(chunk)
            return
        except (httpx.HTTPError, OSError) as exc:
            last = exc
        attempt += 1
        if attempt >= BLOB_ATTEMPTS:
            if time.monotonic() >= deadline:
                raise release_delta.DeltaRefused("not_ready", f"the controller has not supplied {sha} yet: {last}")
            time.sleep(BLOB_WAIT_POLL_S)


def get_blob(sha: str, destination: Path, size: int) -> None:
    """This Mac's own copy of the bytes if it has one, else the controller's."""
    local = app_release.blob_file(sha)
    if local is not None:
        shutil.copyfile(local, destination)
        return
    _remote_blob(sha, destination, size)


# ── applying (the stage job the native app polls) ──────────────────────────


_job: dict[str, Any] = {"state": "idle"}
_job_lock = threading.Lock()


def stage_status() -> dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set(**values: Any) -> None:
    with _job_lock:
        _job.update(values)


def start_stage() -> dict[str, Any]:
    """Begin staging the held release, or report the run already under way (or already done for
    the same release - a swap the app deferred because the Mac was busy is picked up again)."""
    descriptor = app_release.load_release()
    if descriptor is None:
        raise app_release.ReleaseRefused(409, "no release is held")
    with _job_lock:
        same = _job.get("signature") == descriptor.get("signature")
        if _job["state"] == "running":
            return dict(_job)
        if _job["state"] == "staged" and same and Path(_job["staged_app"]).is_dir():
            return dict(_job)
        _job.clear()
        _job.update(state="running", phase="starting", done_bytes=0, total_bytes=0,
                    build=descriptor.get("build"), signature=descriptor.get("signature"))
    threading.Thread(target=_run_stage, args=(descriptor,), name="release-stage", daemon=True).start()
    return stage_status()


def _await_blobs(descriptor: Mapping[str, Any]) -> None:
    """A gateway pushes the manifest first and the blobs after it, and the release is advertised as
    soon as the manifest lands. A controller's own app that checks inside that window would find a
    blob missing with no controller to ask, and a manifest-pushed controller holds no disk image to
    fall back to - so a controller waits here (reported as ``downloading``) until the blobs are in.
    Still missing after ``BLOB_WAIT_S``: ``not_ready``, which the app retries, never a failure."""
    if controller_target() is not None or app_release.ARTIFACT_FILE.is_file():
        return  # a node fetches from its controller; a held disk image is a working fallback
    sizes = app_release._held_blob_sizes()
    deadline = time.monotonic() + BLOB_WAIT_S
    total = None
    while missing := app_release.missing_blobs():
        if (app_release.load_release() or {}).get("signature") != descriptor.get("signature"):
            raise release_delta.DeltaRefused("not_ready", "a newer release arrived while this one was being received")
        waiting = sum(sizes.get(sha, 0) for sha in missing)
        total = total or waiting
        _set(phase="downloading", done_bytes=max(total - waiting, 0), total_bytes=total)
        if time.monotonic() >= deadline:
            raise release_delta.DeltaRefused(
                "not_ready", f"{len(missing)} files of this release have not reached this Mac yet")
        time.sleep(BLOB_WAIT_POLL_S)


def _run_stage(descriptor: Mapping[str, Any]) -> None:
    def progress(phase: str, done: int, total: int) -> None:
        _set(phase=phase, done_bytes=done, total_bytes=total)

    try:
        held = app_release.held_manifest(dict(descriptor))
        if held is None:
            raise release_delta.DeltaRefused("no_manifest", "this Mac holds no file manifest for the release")
        app = release_delta.installed_app_root()
        if app is None:
            raise release_delta.DeltaRefused("not_installed", "this Hub is not running from an installed app")
        _await_blobs(descriptor)
        staged, summary = release_delta.stage(
            app, fm.flatten(held[1]), descriptor["files_sha256"], get_blob,
            artifact_bytes=descriptor.get("bytes"), progress=progress)
        _set(state="staged", phase="staged", staged_app=str(staged), summary=summary)
    except release_delta.DeltaRefused as exc:
        log.info("delta update refused (%s): %s", exc.code, exc.message)
        _set(state="not_ready" if exc.code == "not_ready" else "fallback", code=exc.code, message=exc.message)
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the full image is the answer
        log.exception("delta update failed")
        _set(state="fallback", code="internal", message=str(exc) or type(exc).__name__)
