"""Node lifecycle for Developer Mode: one packaged runner, one live attempt, durable settlement.

``developer_tests`` owns the persisted switch, routes and ledger vocabulary. This module owns
what happens between durable acceptance and a terminal receipt: build the packaged runner, run
one attempt on an owned thread, settle the ledger, and recover only the exact persisted process
and test-owned download after a restart. Nothing here starts work at import or ``configure``
time, and nothing re-runs an accepted attempt after a restart.

The runner is another component's API: ``ImageTestRunner``, ``validate_pinned_request`` and
``stop_recorded``. Its child is started from the packaged Image runtime declared by
``STUDIOHUB_RELEASE_MANIFEST``; the Hub's own interpreter is never a fallback.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path

from . import developer_tests as ledger
from . import developer_image_runner as runner_module
from .developer_image_runner import (
    ASSETS_DIR, ImageTestRunner, RunnerRefusal, Metrics, validate_pinned_request)
from .standalone_config import data_dir

STATE_LOCK = threading.RLock()

# Installation is test-owned work but can legitimately outlast inference. The wait is bounded;
# past it the attempt stays recovery_hold rather than pretending the transfer is gone.
INSTALL_WAIT_SECONDS = float(os.environ.get("STUDIOHUB_DEVELOPER_INSTALL_WAIT_SECONDS") or 1800)
STAGE_POLL_SECONDS = 0.05

_live: dict | None = None
_runner: ImageTestRunner | None = None
_runtime_ready = False
_block: dict | None = None
_library: Path | None = None
_stopping = False


def _admission():
    from .backend import developer_test_admission
    return developer_test_admission


def _release_manifest() -> tuple[dict | None, Path | None, dict | None]:
    """(release, runtime python, refusal). A missing runtime is a refusal, never sys.executable."""
    path = os.environ.get("STUDIOHUB_RELEASE_MANIFEST", "").strip()
    if not path:
        return None, None, {"code": "unsupported",
                            "detail": "no packaged release manifest is configured on this Mac"}
    try:
        raw = Path(path).read_bytes()
        release = json.loads(raw)
        if (release.get("schema") != "studiohub.standalone-release"
                or release.get("schema_version") != 1):
            raise ValueError("release manifest schema is not studiohub.standalone-release v1")
        identity = release["components"]["image"]["runtime_sha256"]
        if (not isinstance(identity, str) or len(identity) != 64
                or any(char not in "0123456789abcdef" for char in identity)):
            raise ValueError("release manifest does not identify an exact Image runtime")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return None, None, {"code": "unsupported",
                            "detail": f"invalid Image release identity: {exc}"}
    runtime = Path(path).resolve().parent / "runtimes" / "image" / "bin" / "python3"
    if not runtime.is_file():
        return release, None, {"code": "unsupported",
                               "detail": "the packaged Image runtime interpreter is missing"}
    return release, runtime, None


def _manifests_dir() -> Path:
    value = os.environ.get("STUDIOHUB_MODEL_MANIFESTS", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "model-manifests"


def _rows() -> list:
    """The Image build's own rows; the Hub catalog is the lazy fallback, never a second list."""
    try:
        from components.image.app.backend import generation
        return list(generation.MODELS.values())
    except Exception:  # noqa: BLE001 - projection still has to answer
        from . import standalone
        catalog = standalone._model_catalog()
        return [{"package_id": package_id, "repo": manifest.get("model_id"),
                 "revision": manifest.get("revision"), "engine": manifest.get("engine"),
                 "label": manifest.get("label") or package_id}
                for package_id, manifest in catalog.items()
                if isinstance(manifest, dict)
                and manifest.get("engine") in ("image-mflux", "image-mlx-serve")]


def configure(*, child_command: list[str] | None = None, models=None, rows=None,
              metrics=None, install_model=None, preflight=None, hub_version: str | None = None,
              assets_dir: Path | None = None, sample_interval_ms: int = 250,
              stop_grace_seconds: float = 5.0) -> dict:
    """Build the packaged runner. No attempt is read, started or recovered here.

    The keyword overrides exist so a focused test can supply a fake child/rows/library; the
    production call is ``configure()`` with the environment the Hub was launched with.
    """
    global _runner, _runtime_ready, _block, _library, _stopping
    with STATE_LOCK:
        if _live is not None:
            return {"configured": False, "reason": "an attempt is active; configure only at start"}
        # A fresh configure is a fresh process lifetime; only shutdown set this fence.
        _stopping = False
        release, runtime, refusal = _release_manifest()
        library_value = os.environ.get("STUDIOHUB_MODELS_DIR", "").strip()
        library = None
        if library_value:
            candidate = Path(library_value).expanduser().resolve()
            if candidate.is_dir():
                library = candidate
        block = refusal
        if library is None and block is None:
            block = {"code": "unsupported",
                     "detail": "STUDIOHUB_MODELS_DIR does not name a usable model library"}
        if child_command is not None:
            runtime = runtime or Path(child_command[0])
        _runtime_ready = runtime is not None
        _library = library
        _block = block
        measurement = metrics or Metrics()
        try:
            total_memory = measurement.total_bytes()
        except Exception:  # noqa: BLE001 - unknown memory is null, never invented
            total_memory = None
        try:
            _runner = ImageTestRunner(
                data_dir=data_dir(), models_dir=library or data_dir() / "model-library-unavailable",
                manifests_dir=_manifests_dir(), assets_dir=Path(assets_dir) if assets_dir else ASSETS_DIR,
                runtime_python=runtime,
                runtime_sha256=(release or {}).get("components", {}).get("image", {}).get(
                    "runtime_sha256"),
                hub_version=hub_version or str((release or {}).get("version") or "unknown"),
                total_memory_bytes=total_memory, models=models,
                rows=rows if rows is not None else _rows(),
                preflight=preflight, metrics=measurement,
                install_model=install_model or _install_model,
                sample_interval_ms=sample_interval_ms, stop_grace_seconds=stop_grace_seconds,
                child_command=child_command)
        except Exception as exc:  # optional test support cannot prevent Hub startup
            _runner = None
            _block = {"code": "integrity_error",
                      "detail": f"Developer Mode runner is unavailable: {type(exc).__name__}"}
            return {"configured": False, "runtime": _runtime_ready,
                    "library": library is not None, "reason": _block}
        return {"configured": True, "runtime": _runtime_ready, "library": library is not None,
                "reason": block}


def _ensure() -> ImageTestRunner:
    with STATE_LOCK:
        if _runner is None and _block is None:
            configure()
        if _runner is None:
            raise ledger.Refused(503, "integrity_error",
                                 (_block or {}).get("detail") or "Developer Mode is unavailable")
        return _runner


def _capabilities_value(runner: ImageTestRunner) -> dict:
    node, controller = ledger._require_identity()
    record, error = ledger._load_setting()
    if error is not None or record is None:
        setting = {
            "format": ledger.SETTING_RESULT_FORMAT, "node_id": node,
            "controller_id": controller, "enabled": False, "revision": "unavailable",
            "verified": False,
            "reason": {"code": "integrity_error",
                       "detail": "the persisted Developer Mode setting could not be read"},
            "updated_at": None, "request_id": None,
        }
    else:
        setting = ledger._setting_result(record)
    try:
        value = runner.capabilities(node_id=node, controller_id=controller, setting=setting)
    except Exception as exc:  # noqa: BLE001 - a runner that cannot describe itself is unavailable
        raise ledger.Refused(503, "integrity_error",
                             f"the packaged runner could not describe itself: {exc}") from exc
    if _block is not None:
        detail = (value.get("reason") or {}).get("detail") or ""
        value["supported"] = False
        value["reason"] = {"code": "unsupported",
                           "detail": "; ".join(part for part in (detail, _block["detail"])
                                               if part)[:4096]}
    return value


def capabilities() -> tuple[int, bytes]:
    """The frozen capabilities object, with ``supported=false`` and a reason when it cannot run.

    Running from source has no packaged Image runtime or full-runner stop proof, so the object is
    still described exactly and says why it is unsupported. It never claims a capability the
    packaged runner does not have, and it never invents a measurement.
    """
    runner = _ensure()
    return 200, json.dumps(_capabilities_value(runner), separators=(",", ":")).encode()


def _refuse_runner(exc: RunnerRefusal) -> ledger.Refused:
    status = exc.status if exc.status in (400, 409, 413, 503) else 409
    if exc.code in ("stale_setting", "integrity_error", "model_unavailable"):
        status = 409
    return ledger.Refused(status, exc.code, exc.detail, acceptance="not_accepted")


def submit_validated(raw: bytes, key: str, request: dict, record: dict, *,
                     actor: dict, destination: str) -> tuple[int, bytes]:
    """Validate every pin, then durably accept and start. Called by ``developer_tests.submit``."""
    runner = _ensure()
    if not _runtime_ready:
        detail = (_block or {}).get("detail") or "the packaged image test runner is not present"
        raise ledger.Refused(503, "unsupported", detail)
    if _block is not None:
        raise ledger.Refused(503, "unsupported", _block["detail"])
    try:
        validate_pinned_request(
            request, fixtures=runner.fixtures, policy=runner.policy,
            models=runner.model_capabilities(refresh=True),
            node_setting_revision=record["revision"], request_bytes=raw)
    except RunnerRefusal as exc:
        raise _refuse_runner(exc) from exc
    value = _capabilities_value(runner)
    if not value["supported"]:
        raise ledger.Refused(503, "unsupported",
                             (value.get("reason") or {}).get("detail")
                             or "the packaged runner does not support this test")
    template = _terminal_receipt({
        "request_base64": base64.b64encode(raw).decode("ascii"),
        "attempt_id": "pending", "idempotency_key": key, "accepted": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        outcome="not_run", code="incomplete_evidence", detail="the attempt has not started")
    if template is None:
        raise ledger.Refused(503, "integrity_error", "the pinned receipt identity could not be retained")
    row = ledger.accept_attempt(key, raw, request, record["revision"],
                                actor=actor, destination=destination, receipt_context=template[0])
    _start_next()
    return 202, json.dumps(ledger._status_of(row), separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# One owned thread, one live attempt at a time.


def _start_next() -> None:
    global _live
    with STATE_LOCK:
        if _stopping or _live is not None or _runner is None:
            return
        try:
            attempts = ledger._load_ledger()["attempts"]
        except Exception:  # noqa: BLE001 - unreadable ledger keeps everything waiting
            return
        waiting = [row for row in attempts.values()
                   if isinstance(row, dict) and row.get("state") == "waiting_idle"
                   and row.get("accepted") and not row.get("cancel_requested")]
        waiting.sort(key=lambda row: str(row.get("created_at") or ""))
        if not waiting:
            return
        row = waiting[0]
        live = {"key": row["idempotency_key"], "attempt_id": row["attempt_id"],
                "cancel": threading.Event(), "runner": _runner,
                "thread": None}
        _live = live
        thread = threading.Thread(target=_run, args=(live,),
                                  name=f"developer-test-{row['attempt_id']}", daemon=True)
        live["thread"] = thread
        thread.start()


def _watch_stage(key: str, attempt_id: str, stop: threading.Event) -> None:
    """Project the runner's persisted stage file into the ledger.

    The runner writes ``stage.json`` before installation and again before the child spawn, so
    the durable stage is already there; this only keeps the status route in step.
    """
    path = data_dir() / "developer-tests" / "attempts" / attempt_id / "stage.json"
    seen = None
    while not stop.wait(STAGE_POLL_SECONDS):
        try:
            stage = json.loads(path.read_text(encoding="utf-8")).get("stage")
        except (OSError, ValueError, AttributeError):
            continue
        if stage in ("installing", "running") and stage != seen:
            seen = stage
            try:
                ledger.update_attempt(key, state=stage)
            except Exception:  # noqa: BLE001 - a projection failure never stops the attempt
                return


def _run(live: dict) -> None:
    global _live
    runner: ImageTestRunner = live["runner"]
    key, attempt_id = live["key"], live["attempt_id"]
    stop = threading.Event()
    watcher = None
    try:
        row = ledger.attempt(key)
        if (row is None or row.get("state") != "waiting_idle"
                or row.get("cancel_requested")):
            if row is not None and row.get("state") == "waiting_idle":
                ledger.settle_attempt(
                    key, state="cancelled",
                    reason={"code": "cancel_requested",
                            "detail": "cancelled before the attempt started"},
                    resource_release_confirmed=True)
            return
        raw = base64.b64decode(row["request_base64"])
        request = ledger.validate_request(ledger.parse_json(raw))
        node, controller = ledger._require_identity()
        # A queued attempt can wait while the switch, runner or fixture pins change. Re-check
        # them against the live setting immediately before starting; a disabled/unverified
        # setting is left to admission so the refusal still settles as a real not_run receipt.
        record, error = ledger._load_setting()
        if error is None and record is not None and record.get("verified") and record.get("enabled"):
            try:
                validate_pinned_request(
                    request, fixtures=runner.fixtures, policy=runner.policy,
                    models=runner.model_capabilities(refresh=True),
                    node_setting_revision=record["revision"], request_bytes=raw)
            except RunnerRefusal as exc:
                _settle_with_receipt(key, row, outcome="not_run", code=exc.code,
                                     detail=exc.detail)
                return
        ledger.update_attempt(key, state="installing", reason=None)
        watcher = threading.Thread(target=_watch_stage, args=(key, attempt_id, stop),
                                   name=f"developer-test-stage-{attempt_id}", daemon=True)
        watcher.start()
        def revalidate_setting():
            current, setting_error = ledger._load_setting()
            if (setting_error is not None or current is None or not current.get("verified")
                    or not current.get("enabled")
                    or current.get("revision") != request["node_setting_revision"]):
                raise RunnerRefusal("stale_setting", "Developer Mode changed before the child started")

        try:
            outcome = runner.run_attempt(
                request, raw, attempt_id=attempt_id, idempotency_key=key,
                node_id=node, controller_id=controller, admission=_admission(),
                cancel_event=live["cancel"], revalidate=revalidate_setting)
        finally:
            stop.set()
        _settle(key, attempt_id, outcome)
    except Exception as exc:  # noqa: BLE001 - an owned child may exist; prove release or hold
        proof = runner.stop_recorded(attempt_id)
        released = proof.confirmed
        try:
            admission = _admission()
            if admission.active_attempt() == attempt_id:
                released = admission.release(attempt_id, confirmed=released)
        except Exception:
            released = False
        reason = {"code": "internal_error", "detail": str(exc)[:4096]}
        if released:
            row = ledger.attempt(key)
            if row is not None:
                _settle_with_receipt(key, row, outcome="interrupted", code="internal_error",
                                     detail=reason["detail"])
            else:
                ledger.settle_attempt(key, state="interrupted", reason=reason,
                                      resource_release_confirmed=True)
        else:
            ledger.update_attempt(key, state="recovery_hold",
                                  reason={"code": "release_unknown",
                                          "detail": "the attempt failed and its owned resources "
                                                    "are not confirmed released"},
                                  resource_release_confirmed=False)
    finally:
        if watcher is not None:
            stop.set()
        with STATE_LOCK:
            if _live is not None and _live["attempt_id"] == attempt_id:
                _live = None
        _start_next()


def _settle(key: str, attempt_id: str, outcome) -> None:
    if outcome.terminal and outcome.receipt is not None and outcome.receipt_bytes is not None:
        receipt = outcome.receipt
        descriptor = outcome.log_descriptor
        ledger.settle_attempt(
            key, state=receipt["outcome"], reason=receipt["reason"],
            receipt_base64=base64.b64encode(outcome.receipt_bytes).decode("ascii"),
            receipt_sha256=outcome.receipt_sha256,
            resource_release_confirmed=bool(receipt["resource_release"]["confirmed"]),
            installation=receipt["installation"],
            process_identity=outcome.process_identity,
            log_base64=base64.b64encode(outcome.log_bytes).decode("ascii"),
            log_sha256=descriptor["sha256"], log_truncated=bool(descriptor["truncated"]))
        return
    reason = outcome.reason or {"code": "release_unknown",
                                "detail": "the attempt did not reach a settled receipt"}
    ledger.update_attempt(key, state="recovery_hold", reason=reason,
                          process_identity=outcome.process_identity,
                          resource_release_confirmed=False)


def _terminal_receipt(row: dict, *, outcome: str, code: str, detail: str,
                      confirmed: bool = True) -> tuple[dict, bytes, str, bytes] | None:
    """An exact frozen receipt for an attempt that provably never started.

    The runner owns receipt construction; until it exposes a public terminal builder this uses
    its own ``_not_run_receipt`` with the same packaged metadata/hardware. Pins that no longer
    match the packaged runner/fixtures cannot produce a receipt, so the caller settles without
    one rather than inventing identities.
    """
    context = row.get("receipt_context")
    if isinstance(context, dict):
        # Acceptance retains the original build/policy identities, even after an app update.
        log_path = data_dir() / "developer-tests" / "attempts" / row["attempt_id"] / "log.txt"
        try:
            with log_path.open("rb") as stream:
                retained_log = stream.read(runner_module.LOG_TOTAL_BYTES)
        except OSError:
            retained_log = b""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stored_install = row.get("installation") or {}
        installation = {**context["installation"], **{
            name: stored_install[name] for name in context["installation"] if name in stored_install}}
        if isinstance(installation.get("reason"), str):
            installation["reason"] = {"code": "model_install_failed",
                                      "detail": installation["reason"][:4096]}
        receipt = {**context, "attempt_id": row["attempt_id"],
                   "idempotency_key": row["idempotency_key"], "started_at": row["created_at"],
                   "finished_at": now, "outcome": outcome,
                   "reason": {"code": code, "detail": detail[:4096]},
                   "installation": installation,
                   "checks": runner_module._checks({
                       "resource_release": (confirmed, "release_unknown", "release unconfirmed")}),
                   "log": {"sha256": ledger._sha256(retained_log), "size_bytes": len(retained_log),
                           "truncated": row.get("state") in ("running", "recovery_hold")},
                   "resource_release": {"confirmed": confirmed,
                                        "confirmed_at": now if confirmed else None,
                                        "reason": None if confirmed else {
                                            "code": "release_unknown", "detail": detail[:4096]}}}
        body = runner_module._json_bytes(receipt)
        return receipt, body, ledger._sha256(body), retained_log
    runner = _ensure()
    raw = base64.b64decode(row["request_base64"])
    try:
        request = ledger.validate_request(ledger.parse_json(raw))
        plan = validate_pinned_request(
            request, fixtures=runner.fixtures, policy=runner.policy,
            models=runner.model_capabilities(refresh=True), request_bytes=raw)
    except Exception:  # noqa: BLE001 - a stale packaged pin cannot be receipted exactly
        return None
    node, controller = ledger._require_identity()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metadata = {
        "method": {
            "start_memory": "psutil.virtual_memory().available",
            "start_memory_scope": "host_available_physical_unified_memory",
            "rss": "sampled_owned_process_tree_rss_sum",
            "swap": "sampled_system_swap_used",
            "sample_interval_ms": runner.sample_interval_ms,
            "elapsed_clock": "monotonic",
        },
        "sample_count": 0,
        "sample_gaps_ms": [],
        "available_memory_before_start_bytes": None,
        "preflight_observed_at": None,
        "safety_policy": runner.policy.wire(),
        "published_limits": {},
        "requested_limits": plan.limits,
    }
    hardware, hardware_reasons = runner_module._hardware(runner.metrics)
    retained_log = runner.read_log(row["attempt_id"]) if row.get("accepted") else b""
    log = {"sha256": ledger._sha256(retained_log), "size_bytes": len(retained_log),
           "truncated": row.get("state") in ("running", "recovery_hold")}
    stored_install = row.get("installation") or {}
    install_started = bool(stored_install.get("started") or row.get("install_download_id"))
    installation = {"required": install_started or not plan.model.wire["installed"],
                    "started": install_started, "completed": bool(stored_install.get("completed")),
                    "download_id": stored_install.get("download_id") or row.get("install_download_id"),
                    "verified_manifest_sha256": stored_install.get("verified_manifest_sha256"),
                    "reason": None}
    release = runner_module.ReleaseProof(confirmed, now if confirmed else None, None, 0.0, [])
    receipt = runner_module._not_run_receipt(
        plan, row["attempt_id"], row["idempotency_key"], node, controller,
        row["created_at"], now, code, detail, runner.hub_version, release, metadata,
        hardware, hardware_reasons, log, installation, {}, {}, outcome=outcome)
    body = runner_module._json_bytes(receipt)
    return receipt, body, ledger._sha256(body), retained_log


def _settle_with_receipt(key: str, row: dict, *, outcome: str, code: str, detail: str,
                         confirmed: bool = True) -> None:
    """Settle a never-started attempt with its exact receipt when the pins still build one."""
    fields = {"state": outcome, "reason": {"code": code, "detail": detail[:4096]},
              "resource_release_confirmed": confirmed}
    built = _terminal_receipt(row, outcome=outcome, code=code, detail=detail,
                              confirmed=confirmed)
    if built is not None:
        receipt, body, digest, retained_log = built
        fields.update(
            receipt_base64=base64.b64encode(body).decode("ascii"),
            receipt_sha256=digest, installation=receipt["installation"],
            log_base64=base64.b64encode(retained_log).decode("ascii"),
            log_sha256=receipt["log"]["sha256"], log_truncated=receipt["log"]["truncated"])
    ledger.settle_attempt(key, **fields)


def request_cancel(attempt_id: str, *, key: str | None = None) -> None:
    """Stop the live attempt before its next stage; a waiting attempt is settled without starting."""
    with STATE_LOCK:
        live = _live
        if live is not None and live["attempt_id"] == attempt_id:
            event = live["cancel"]
            event.reason_code = "cancel_requested"
            event.reason_detail = "the test was cancelled by the node"
            event.set()
            try:
                admitted = _admission().cancel_event(attempt_id)
                if admitted is not None:
                    admitted.reason_code = "cancel_requested"
                    admitted.reason_detail = "the test was cancelled by the node"
                    admitted.set()
            except Exception:  # noqa: BLE001 - the service event still stops the child
                pass
            return
    if key is not None:
        # Never started: settle it here rather than launching a child just to cancel it.
        # A recovery_hold attempt keeps its flag: its release is still unconfirmed.
        row = ledger.attempt(key)
        if row is not None and row.get("state") == "waiting_idle":
            _settle_with_receipt(key, row, outcome="cancelled", code="cancel_requested",
                                 detail="cancelled before the attempt started")


# ---------------------------------------------------------------------------
# Installation: the existing model_downloads transfer only, test-owned work cancellable.


def _install_model(package_id: str, revision: str) -> dict:
    live = _live
    if live is None:
        return {"started": False, "completed": False, "release_confirmed": True,
                "download_id": None, "verified_manifest_sha256": None,
                "reason": "no live attempt owns this installation"}
    key, attempt_id = live["key"], live["attempt_id"]
    from . import model_downloads, standalone
    try:
        catalog = standalone._model_catalog()
        manifest = catalog.get(package_id)
    except Exception as exc:  # noqa: BLE001
        return {"started": False, "completed": False, "release_confirmed": True,
                "download_id": None, "verified_manifest_sha256": None,
                "reason": f"the model catalog could not be read: {exc}"}
    if not isinstance(manifest, dict) or manifest.get("revision") != revision:
        return {"started": False, "completed": False, "release_confirmed": True,
                "download_id": None, "verified_manifest_sha256": None,
                "reason": "the packaged catalog has no manifest pinned to this model revision"}
    ledger.update_attempt(key, install_start_pending=True)
    try:
        run = model_downloads.start(package_id=package_id, manifest=manifest,
                                    catalog=catalog, library=_library, sources=None)
        download_id = run.get("id")
        if not isinstance(download_id, str) or not download_id:
            raise ValueError("the installer returned no transfer identity")
        owned = not bool(run.get("reused"))
        ledger.update_attempt(key, install_download_id=download_id,
                              install_download_owned=owned, install_start_pending=False)
    except Exception as exc:  # the transfer may have started before its identity was saved
        return {"started": True, "completed": False, "release_confirmed": False,
                "download_id": None, "verified_manifest_sha256": None,
                "reason": f"the model download start could not be reconciled: {exc}"}
    deadline = time.monotonic() + INSTALL_WAIT_SECONDS
    admission = _admission()
    while True:
        cancelled = live["cancel"].is_set()
        if not cancelled:
            try:
                cancelled = admission.cancel_requested(attempt_id)
            except Exception:  # noqa: BLE001 - unknown authority stops the wait
                cancelled = True
        if cancelled:
            released = True
            if owned:
                try:
                    run = model_downloads.cancel(download_id) or run
                    released = run.get("state") in ("done", "failed", "cancelled")
                except Exception:  # noqa: BLE001
                    released = False
            result = {"started": True, "completed": False, "release_confirmed": released,
                      "download_id": download_id, "verified_manifest_sha256": None,
                      "reason": ("the test was cancelled while installing" if owned else
                                 "the test stopped waiting for a shared download; it was "
                                 "not cancelled")}
            ledger.update_attempt(key, installation=result)
            return result
        run = model_downloads.status(download_id)
        state = (run or {}).get("state")
        if state == "done":
            digest = None
            try:
                from components import model_packages
                digest = model_packages.manifest_digest(manifest)
            except Exception:  # noqa: BLE001 - a missing digest is null, never fabricated
                digest = None
            result = {"started": True, "completed": True, "release_confirmed": True,
                      "download_id": download_id, "verified_manifest_sha256": digest,
                      "reason": None}
            ledger.update_attempt(key, installation=result)
            return result
        if state in ("failed", "cancelled"):
            result = {"started": True, "completed": False, "release_confirmed": True,
                      "download_id": download_id, "verified_manifest_sha256": None,
                      "reason": str((run or {}).get("error") or state)}
            ledger.update_attempt(key, installation=result)
            return result
        if time.monotonic() > deadline:
            released = True
            if owned:
                try:
                    run = model_downloads.cancel(download_id) or run
                    released = run.get("state") in ("done", "failed", "cancelled")
                except Exception:  # noqa: BLE001
                    released = False
            result = {"started": True, "completed": False, "release_confirmed": released,
                      "download_id": download_id, "verified_manifest_sha256": None,
                      "reason": "the model installation did not finish within the bounded wait"}
            ledger.update_attempt(key, installation=result)
            return result
        time.sleep(0.25)


# ---------------------------------------------------------------------------
# Restart and shutdown: stop only exact persisted identities; never rerun accepted work.


def _restore_recorded_identity(attempt_id: str, identity: dict | None) -> None:
    """Put the exact persisted identity back where ``stop_recorded`` reads it.

    The runner writes this file at spawn; a row that only reached the ledger keeps the same
    pid+create_time, so restoring it lets recovery stop the exact process rather than guessing.
    """
    if not isinstance(identity, dict) or not identity.get("pid"):
        return
    path = data_dir() / "developer-tests" / "attempts" / attempt_id / "child-identity.json"
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(identity), encoding="utf-8")
    except OSError:
        pass


def _release_owned_download(row: dict | None) -> bool:
    if row and row.get("install_start_pending"):
        return False
    if not row or not row.get("install_download_id") or not row.get("install_download_owned"):
        return True
    from . import model_downloads
    try:
        run = model_downloads.status(row["install_download_id"])
        if run is None:
            return False
        if run.get("state") not in ("done", "failed", "cancelled"):
            run = model_downloads.cancel(row["install_download_id"]) or run
        return run.get("state") in ("done", "failed", "cancelled")
    except Exception:  # noqa: BLE001 - unknown release stays held
        return False


def recover() -> dict:
    """After a restart: stop the exact persisted child/download and settle without rerunning."""
    try:
        runner = _ensure()
    except ledger.Refused:
        runner = None
    admission = _admission()
    try:
        rows = [row for row in ledger._load_ledger()["attempts"].values()
                if isinstance(row, dict) and row.get("accepted")]
    except Exception:  # noqa: BLE001 - an unreadable ledger keeps this Mac out of production
        return {"interrupted": [], "recovery_hold": [], "not_run": [],
                "error": "the Developer Mode ledger could not be read"}
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    result = {"interrupted": [], "recovery_hold": [], "not_run": []}
    for row in rows:
        state = row.get("state")
        key = str(row.get("idempotency_key") or "")
        attempt_id = str(row.get("attempt_id") or "")
        if not key or not attempt_id:
            continue
        if runner is None:
            if state in ("installing", "running", "recovery_hold"):
                ledger.update_attempt(
                    key, state="recovery_hold", resource_release_confirmed=False,
                    reason={"code": "release_unknown",
                            "detail": "the test runner is unavailable; owned resources remain unverified"})
                result["recovery_hold"].append(attempt_id)
            elif state == "waiting_idle":
                if isinstance(row.get("receipt_context"), dict):
                    _settle_with_receipt(key, row, outcome="not_run", code="restarted",
                                         detail="the Hub restarted before this attempt started")
                else:
                    ledger.settle_attempt(
                        key, state="not_run", resource_release_confirmed=True,
                        reason={"code": "restarted", "detail": "the Hub restarted before this attempt started"})
                result["not_run"].append(attempt_id)
            continue
        if state in ("installing", "running", "recovery_hold"):
            _restore_recorded_identity(attempt_id, row.get("process_identity"))
            identity = runner.process_identity(attempt_id)
            # A row that says a child was running (or whose release was already uncertain)
            # cannot be called released just because no identity was persisted.
            if identity is None and state in ("running", "recovery_hold"):
                released = False
            else:
                released = runner.stop_recorded(attempt_id).confirmed
            released = bool(released and _release_owned_download(row))
            try:
                admission.release(attempt_id, confirmed=released)
            except Exception:  # noqa: BLE001
                released = False
            if released:
                _settle_with_receipt(
                    key, row, outcome="interrupted", code="worker_lost",
                    detail="the Hub restarted while this attempt was active; the exact owned "
                           "work is released and the attempt was not rerun")
                ledger.update_attempt(key, process_identity=row.get("process_identity"))
                result["interrupted"].append(attempt_id)
            else:
                ledger.update_attempt(
                    key, state="recovery_hold",
                    reason={"code": "release_unknown",
                            "detail": "the Hub restarted and the attempt's exact owned process "
                                      "or download is not confirmed released"},
                    resource_release_confirmed=False,
                    process_identity=runner.process_identity(attempt_id))
                result["recovery_hold"].append(attempt_id)
        elif state == "waiting_idle":
            _settle_with_receipt(
                key, row, outcome="not_run", code="restarted",
                detail="the Hub restarted before this accepted attempt started; it was not "
                       "rerun")
            result["not_run"].append(attempt_id)
    return result


def _reset() -> None:
    """Test-only: forget the built runner and any in-memory live pointer."""
    global _live, _runner, _runtime_ready, _block, _library, _stopping
    with STATE_LOCK:
        _live = None
        _runner = None
        _runtime_ready = False
        _block = None
        _library = None
        _stopping = False


def shutdown() -> dict:
    """Stop the live child/download and settle it. Called once, before the Hub exits."""
    global _live, _stopping
    with STATE_LOCK:
        _stopping = True
        live = dict(_live) if _live is not None else None
    if live is None:
        return {"stopped": None}
    event = live["cancel"]
    event.reason_code = "worker_lost"
    event.reason_detail = "the Hub stopped while this attempt was active"
    event.set()
    try:
        admitted = _admission().cancel_event(live["attempt_id"])
        if admitted is not None:
            admitted.set()
    except Exception:  # noqa: BLE001
        pass
    row = ledger.attempt(live["key"])
    identity = live["runner"].process_identity(live["attempt_id"])
    # A row that already says "running" but has no persisted identity cannot be called released.
    if identity is None and row is not None and row.get("state") == "running":
        released = False
    else:
        released = bool(live["runner"].stop_recorded(live["attempt_id"]).confirmed)
    released = bool(released and _release_owned_download(row))
    try:
        _admission().release(live["attempt_id"], confirmed=released)
    except Exception:  # noqa: BLE001
        released = False
    if released:
        row = ledger.attempt(live["key"])
        if row is not None:
            _settle_with_receipt(
                live["key"], row, outcome="interrupted", code="worker_lost",
                detail="the Hub stopped while this attempt was active; owned work is released")
        else:
            ledger.settle_attempt(live["key"], state="interrupted",
                                  reason={"code": "worker_lost",
                                          "detail": "the Hub stopped while this attempt was "
                                                    "active; owned work is released"},
                                  resource_release_confirmed=True)
    else:
        ledger.update_attempt(
            live["key"], state="recovery_hold",
            reason={"code": "release_unknown",
                    "detail": "the Hub stopped and the attempt's owned work is not confirmed "
                              "released"},
            resource_release_confirmed=False)
    with STATE_LOCK:
        if _live is not None and _live["attempt_id"] == live["attempt_id"]:
            _live = None
    return {"stopped": live["attempt_id"], "release_confirmed": released}
