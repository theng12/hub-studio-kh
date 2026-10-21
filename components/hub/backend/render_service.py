"""Exact authenticated HTTP surface for the packaged final-render lane."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .render_assets import AssetNotFoundError, RenderAssetError
from .render_encode import packet_is_supported
from .render_jobs import (
    MAX_REQUEST_BYTES,
    RenderJobConflict,
    RenderJobNotFound,
    RenderJobUnavailable,
    decode_render_request,
)


_RANGE = re.compile(r"bytes=(\d+)-(\d+)\Z")
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)\Z")
_MAX_OUTPUT_RANGE = 8 * 1024 * 1024
_MAX_ACK_BYTES = 4096
_MAX_RELAY_CHUNK = 8 * 1024 * 1024
_RETIRE_AFTER_S = 24 * 3600
# Real ids are UUIDs; the service only requires one safe path component so a job
# directory can never escape runtime.root/jobs.
_JOB_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}\Z")
_ATTEMPT_ID = re.compile(r"[0-9a-f]{32}\Z")
_log = logging.getLogger("studiohub.final_render")


def _reference_id(owner_scope: str, dispatch_key: str) -> str:
    """One reference per accepted render job: all of its inputs share one identity.

    A terminal job can then release its protection from the job row alone, without
    the request body, and an ACK releases exactly this job's reference.
    """
    digest = hashlib.sha256(
        f"{owner_scope}\0{dispatch_key}".encode()).hexdigest()
    return f"render:{digest}"


class RenderService:
    def __init__(
        self, runtime, assets, executor, authority_loader, *,
        sweep_interval_seconds: float = 300.0,
        trash_root: Callable[[], Path] | Path | None = None,
    ) -> None:
        if (isinstance(sweep_interval_seconds, bool)
                or type(sweep_interval_seconds) not in (int, float)
                or not 0 <= sweep_interval_seconds < float("inf")):
            raise ValueError("sweep_interval_seconds must be finite and non-negative")
        self.runtime = runtime
        self.assets = assets
        self.executor = executor
        self.authority_loader = authority_loader
        self.sweep_interval_seconds = float(sweep_interval_seconds)
        # Retired attempts join the Hub's one Trash Folder (`<data>/trash/<UTC date>/`)
        # where the existing governor and handoff can see them. The runtime's own
        # `_trash` remains the fallback for a service built without a Hub root.
        self._trash_root_source = trash_root
        self._trash_fallback_reported = False
        self._last_sweep = 0.0
        # The executor settles the durable row; this service owns the asset reference that
        # row protects. Wire the two where both exist, so a compile/prelaunch/validation
        # failure or a proven cancel releases its inputs without waiting for a poll.
        attach = getattr(executor, "attach_release_hook", None)
        if callable(attach):
            try:
                attach(self.release_job)
            except Exception:
                pass

    def accept(self, owner_scope: str, dispatch_key: str, raw_request: bytes,
               *, qualification_vector: str | None = None) -> dict[str, Any]:
        body_sha = hashlib.sha256(raw_request).hexdigest()
        existing = self.runtime.by_key(owner_scope, dispatch_key)
        if existing is not None:
            if existing.get("accepted_request_sha256") != body_sha:
                raise RenderJobConflict(
                    "render dispatch key was already accepted for a different exact body")
            self._settle(owner_scope, existing, sweep=False)
            return {**existing, "replayed": True}
        value, packet, packet_json = decode_render_request(raw_request, dispatch_key)
        manifest = {row["logical_id"]: row for row in packet["manifest"]}
        # Both checks are read-only: preflight compiles the packet without touching assets
        # and runs before the permit lookup, and the permit itself is spent only by
        # runtime.accept. A refusal here leaves no reference, no permit consumption and
        # no row.
        self._require_supported_encode(packet, packet_json, qualification_vector)
        permit = None
        if qualification_vector is not None:
            permit = self.authority_loader.qualification_permit(
                qualification_vector, owner_scope, raw_request, {})
            if permit is None:
                raise RenderJobUnavailable(
                    "qualification intent does not match this request")
        descriptors = {}
        for logical_id in sorted(value["assets"]):
            descriptor = self.assets.describe(owner_scope, value["assets"][logical_id])
            expected = manifest[logical_id]
            if descriptor.get("sha256") != expected["sha256"]:
                raise ValueError(f"asset {logical_id} sha256 does not match the packet")
            if descriptor.get("size_bytes") != expected["size_bytes"]:
                raise ValueError(f"asset {logical_id} size does not match the packet")
            descriptors[logical_id] = descriptor
        reference = _reference_id(owner_scope, dispatch_key)
        acquired: list[str] = []
        try:
            for logical_id in sorted(value["assets"]):
                handle = value["assets"][logical_id]
                self.assets.acquire_reference(owner_scope, handle, reference)
                acquired.append(handle)
            accepted = self.runtime.accept(
                owner_scope, dispatch_key, raw_request, qualification_permit=permit)
        except BaseException:
            # A failed attempt must not leave the references it acquired behind. Another
            # attempt with the same key may have won the race and owns that shared job
            # reference, so release only when no row holds this dispatch key.
            if acquired:
                try:
                    winner = self.runtime.by_key(owner_scope, dispatch_key)
                except BaseException:
                    winner = True
                if winner is None:
                    for handle in acquired:
                        try:
                            self.assets.release_reference(
                                owner_scope, handle, reference)
                        except BaseException:
                            pass
            raise
        self.sweep()
        return accepted

    def _admission_authority(self, qualification_vector: str | None):
        """The exact installed authority this request would run under.

        Returns ``(api_available, authority)``. ``api_available`` is False only for a
        loader double that does not expose the installer's lookups, where
        ``runtime.accept`` is still the authority check.
        """
        loader = self.authority_loader
        if loader is None:
            return False, None
        if qualification_vector is not None:
            lister = getattr(loader, "qualification_candidates", None)
            if not callable(lister):
                return False, None
            try:
                for intent, candidate in lister():
                    if intent.get("intent_id") == qualification_vector:
                        return True, candidate
            except Exception:
                return True, None
            return True, None
        snapshot = getattr(loader, "snapshot", None)
        if not callable(snapshot):
            return False, None
        try:
            return True, snapshot()
        except Exception:
            return True, None

    def _require_supported_encode(self, packet, packet_json: str,
                                  qualification_vector: str | None) -> None:
        """Refuse an encode this machine's authority cannot execute, before the row exists.

        The executor applies the same tier rule again at launch. Doing it here keeps an
        accepted job - and the one-use qualification intent or asset references it would
        hold - from ending in a failure the machine could have stated up front.
        """
        available, authority = self._admission_authority(qualification_vector)
        if not available:
            # A loader double without the installer lookup. `runtime.accept` still
            # refuses a missing authority before any row, so nothing is accepted here.
            return
        if authority is None:
            raise RenderJobUnavailable(
                "no installed render authority is available on this machine")
        if not packet_is_supported(packet, packet_json.encode("utf-8"),
                                   authority.selected_tier, authority.exact_encoder):
            raise RenderJobUnavailable(
                "encode is not supported by the qualified render machine")

    def _trash_root(self) -> Path:
        """The Hub's one Trash Folder, or this runtime's own `_trash` fallback."""
        source = self._trash_root_source
        if source is None:
            return Path(self.runtime.root) / "_trash"
        try:
            return Path(source() if callable(source) else source)
        except Exception:
            # A status/cancel request must never fail because the trash callable did;
            # retirement simply falls back to the runtime's own folder. Announce the
            # switch once so an operator can tell handoff is not in use.
            fallback = Path(self.runtime.root) / "_trash"
            if not self._trash_fallback_reported:
                self._trash_fallback_reported = True
                _log.warning(
                    "render trash root unavailable; retiring into %s", fallback)
            return fallback

    def _warn_trash_unavailable(self, root: Path) -> None:
        """Announce, once, that retirement is retained rather than silently relocated."""
        if not self._trash_fallback_reported:
            self._trash_fallback_reported = True
            _log.warning(
                "render trash folder unavailable; retirement retained: %s", root)

    @staticmethod
    def _ensure_trash_root(path: Path) -> None:
        """Create the trash root only when its parent already exists and is a real dir."""
        parent = Path(path).parent
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("trash parent is not a real directory")
        try:
            Path(path).mkdir(mode=0o700)
        except FileExistsError:
            pass

    def sweep(self, *, force: bool = False):
        """Retire aged, unprotected inputs and terminal attempt folders; never delete.

        Called from the existing Hub cleanup thread and from request paths, so an idle
        Mac still retires abandoned uploads, with a short interval so a status poll
        cannot turn into a full store scan. Terminal rows are queried before any asset
        lock is taken; asset retirement then runs under that lock, and attempt folders
        are moved after it is released. Any failure is left for the next sweep.
        """
        now = time.time()
        if not force and now - self._last_sweep < self.sweep_interval_seconds:
            return None
        self._last_sweep = now
        terminal: list[dict[str, Any]] = []
        lister = getattr(self.runtime, "terminal_jobs", None)
        if callable(lister):
            try:
                rows = lister()
                if type(rows) is list:
                    terminal = [row for row in rows if type(row) is dict]
            except Exception:
                terminal = []
        result = None
        retire = getattr(self.assets, "retire_stale", None)
        if callable(retire):
            try:
                result = retire(now=now, destination=self._trash_root())
            except Exception:
                result = None
                self._warn_trash_unavailable(self._trash_root())
        for row in terminal:
            try:
                self._retire_terminal_attempts(
                    str(row.get("owner_scope") or ""), row.get("id"), now=now)
            except Exception:
                pass
        return result

    def release_job_references(self, owner_scope: str, dispatch_key: str) -> int:
        """Release the one job-scoped reference from every asset it protects, best effort."""
        if not dispatch_key:
            return 0
        release = getattr(self.assets, "release_reference_everywhere", None)
        if not callable(release):
            return 0
        try:
            return int(release(
                owner_scope, _reference_id(owner_scope, dispatch_key)) or 0)
        except Exception:
            return 0

    def release_job(self, owner_scope: str, job_id: str) -> int:
        """Release a durably terminal job's references; safe for repeated settle calls.

        `uncertain`, launch, render and cancel-request states deliberately retain their
        references, and `output_ready` retains them until the exact ACK. `RenderExecutor`
        may call this beside its own settle path; the service's status reads are the
        fallback for a terminal job nobody polls.
        """
        try:
            row = self.runtime.get(owner_scope, job_id)
        except Exception:
            return 0
        if type(row) is not dict or row.get("state") not in {"cancelled", "failed"}:
            return 0
        released = self.release_job_references(
            owner_scope, str(row.get("dispatch_key") or ""))
        # A terminal attempt can leave a partial final.mp4 behind. It is moved to the
        # Hub Trash Folder only after the grace period and only with no live lease.
        self._retire_terminal_attempts(owner_scope, row.get("id"))
        return released

    def _settle(self, owner_scope: str, row, *, sweep: bool = True) -> None:
        """Best-effort reconciliation of a row a caller just read; never changes the row."""
        if type(row) is dict and row.get("state") in {"cancelled", "failed"}:
            self.release_job_references(
                owner_scope, str(row.get("dispatch_key") or ""))
            self._retire_terminal_attempts(owner_scope, row.get("id"))
        if sweep:
            self.sweep()

    def by_key(self, owner_scope: str, key: str):
        row = self.runtime.by_key(owner_scope, key)
        self._settle(owner_scope, row)
        return row

    def get(self, owner_scope: str, job_id: str):
        row = self.runtime.get(owner_scope, job_id)
        self._settle(owner_scope, row)
        return row

    async def cancel(self, owner_scope: str, job_id: str):
        if self.executor is None:
            row = self.runtime.request_cancel(owner_scope, job_id)
        else:
            row = await self.executor.request_cancel(owner_scope, job_id)
        self._settle(owner_scope, row)
        return row

    async def cancel_by_key(
        self, owner_scope: str, key: str, expected_request_sha256: str,
    ):
        row = self.runtime.request_cancel_by_key(
            owner_scope, key, expected_request_sha256)
        self._settle(owner_scope, row)
        if self.executor is not None and type(row) is dict and row.get("id"):
            row = await self.executor.request_cancel(owner_scope, row["id"])
            self._settle(owner_scope, row)
        return row

    def output_path(self, owner_scope: str, job: dict[str, Any]) -> Path:
        if job.get("owner_scope") != owner_scope or job.get("state") != "output_ready":
            raise RenderJobNotFound("render output not found")
        attempt = str(job.get("attempt_id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", attempt):
            raise RenderJobNotFound("render output not found")
        path = self.runtime.root / "jobs" / job["id"] / attempt / "final.mp4"
        if path.parent.parent.parent != self.runtime.root / "jobs" or not path.is_file():
            raise RenderJobNotFound("render output not found")
        return path

    def _trash_day(self) -> Path | None:
        """Open today's folder in the Hub Trash Folder, creating only real directories."""
        root = self._trash_root()
        try:
            self._ensure_trash_root(root)
        except OSError:
            self._warn_trash_unavailable(root)
            return None
        try:
            info = root.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return None
        day = root / time.strftime("%Y-%m-%d", time.gmtime())
        try:
            day.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            return None
        try:
            made = day.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(made.st_mode) or not stat.S_ISDIR(made.st_mode):
            return None
        return day

    @staticmethod
    def _newest_mtime(path: Path, depth: int = 2) -> float:
        """Newest touch under one attempt folder, so an active write is never aged out."""
        newest = path.lstat().st_mtime
        try:
            entries = list(os.scandir(path))
        except OSError:
            return newest
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            newest = max(newest, info.st_mtime)
            if depth > 1 and entry.is_dir(follow_symlinks=False):
                newest = max(
                    newest, RenderService._newest_mtime(Path(entry.path), depth - 1))
        return newest

    def _move_attempt_into_trash(self, job_id: str, attempt: str) -> bool:
        """Move one attempt folder into the Hub Trash Folder; never delete anything."""
        if _JOB_ID.fullmatch(job_id) is None or _ATTEMPT_ID.fullmatch(attempt) is None:
            return False
        root = Path(self.runtime.root)
        try:
            info = root.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
        attempt_dir = root / "jobs" / job_id / attempt
        try:
            opened = attempt_dir.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(opened.st_mode) or not stat.S_ISDIR(opened.st_mode):
            return False
        try:
            if (attempt_dir.parent.resolve(strict=True)
                    != (root.resolve(strict=True) / "jobs" / job_id)):
                return False
        except OSError:
            return False
        day = self._trash_day()
        if day is None:
            return False
        target = day / f"{job_id}-{attempt}-{secrets.token_hex(4)}"
        try:
            # Cross-directory rename: same volume only. If the Trash Folder ever resolves
            # to another volume, this OSError retains the attempt folder; nothing is lost.
            os.rename(attempt_dir, target)
        except OSError:
            return False
        try:
            descriptor = os.open(day, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
        return True

    def _retire_terminal_attempts(
        self, owner_scope: str, job_id: Any, *, now: float | None = None,
    ) -> int:
        """Retire attempt folders of a durably failed/cancelled job, after a grace.

        The folder is moved, never deleted. `uncertain`, launch, render,
        cancel-request and `output_ready` rows retain everything: only an ACK retires
        an output_ready attempt. A live compute, reader, transfer or ACK lease also
        retains it, and the row is re-read after the caller's snapshot.
        """
        if not owner_scope or not isinstance(job_id, str):
            return 0
        if _JOB_ID.fullmatch(job_id) is None:
            return 0
        try:
            row = self.runtime.get(owner_scope, job_id)
        except Exception:
            return 0
        if type(row) is not dict or row.get("state") not in {"cancelled", "failed"}:
            return 0
        leases = row.get("leases") or {}
        if any(leases.get(kind) for kind in ("compute", "reader", "transfer", "ack")):
            return 0
        job_dir = Path(self.runtime.root) / "jobs" / job_id
        try:
            info = job_dir.lstat()
        except OSError:
            return 0
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return 0
        try:
            attempts = sorted(
                entry.name for entry in os.scandir(job_dir)
                if entry.is_dir(follow_symlinks=False))
        except OSError:
            return 0
        now = time.time() if now is None else now
        retired = 0
        for attempt in attempts:
            if _ATTEMPT_ID.fullmatch(attempt) is None:
                continue
            try:
                if now - self._newest_mtime(job_dir / attempt) < _RETIRE_AFTER_S:
                    continue
            except OSError:
                continue
            if self._move_attempt_into_trash(job_id, attempt):
                retired += 1
        return retired

    def _retire_output(self, owner_scope: str, job_id: str) -> bool:
        """Move one ACKed attempt folder into the Hub Trash Folder, never delete it.

        The ACK lease is released first, so an active ACK means the output has not been
        consumed. A short transfer lease closes the race with a reader: the output route
        re-reads the row after acquiring its reader lease and refuses while this is set.
        """
        try:
            job = self.runtime.get(owner_scope, job_id)
        except Exception:
            return False
        if type(job) is not dict or job.get("state") != "output_ready":
            return False
        attempt = str(job.get("attempt_id") or "")
        if _ATTEMPT_ID.fullmatch(attempt) is None:
            return False
        lease = f"retire:{attempt}"
        try:
            self.runtime.acquire_file_lease(
                owner_scope, job_id, "transfer", lease)
        except Exception:
            return False
        try:
            try:
                fresh = self.runtime.get(owner_scope, job_id)
            except Exception:
                return False
            leases = (fresh or {}).get("leases") or {}
            if (type(fresh) is not dict or fresh.get("state") != "output_ready"
                    or leases.get("reader") or leases.get("ack")):
                return False
            return self._move_attempt_into_trash(job_id, attempt)
        finally:
            try:
                self.runtime.release_file_lease(
                    owner_scope, job_id, "transfer", lease)
            except Exception:
                pass


def _actor(request: Request, hub_token: str, fleet_token: Callable[[], str | None],
           settings: Callable[[], dict[str, Any]]) -> str:
    if (request.headers.get("authorization") is not None or request.cookies
            or request.headers.get("x-render-owner-scope") is not None
            or any(request.headers.get(name) is not None for name in (
                "forwarded", "x-forwarded-for", "x-forwarded-host",
                "x-forwarded-port", "x-forwarded-proto", "x-real-ip",
            ))):
        raise HTTPException(403, "render service requires its exact machine credential header")
    offered = request.headers.get("x-hub-token")
    if not offered:
        raise HTTPException(401, "render service credential required")
    if secrets.compare_digest(offered, hub_token):
        kind = "hub"
    else:
        fleet = fleet_token()
        if not fleet or not secrets.compare_digest(offered, fleet):
            raise HTTPException(401, "render service credential refused")
        kind = "fleet"
    identity = settings()
    site = str(identity.get("site_id") or "")
    controller = str(identity.get("controller_id") or "")
    if not re.fullmatch(r"[a-z0-9._-]{1,100}", site) or not re.fullmatch(
            r"[a-z0-9._-]{1,100}", controller):
        raise HTTPException(503, "render service identity is unavailable")
    return f"{site}:{controller}:{kind}"


def _answer(value):
    if value is None:
        raise HTTPException(404, "render job not found")
    return value


async def _bounded_body(request: Request, limit: int) -> bytes:
    try:
        declared = int(request.headers.get("content-length") or "0")
    except ValueError as exc:
        raise HTTPException(400, "Content-Length is invalid") from exc
    if declared < 0 or declared > limit:
        raise HTTPException(413, "request body is too large")
    body = bytearray()
    async for piece in request.stream():
        if len(body) + len(piece) > limit:
            raise HTTPException(413, "request body is too large")
        body.extend(piece)
    if request.headers.get("content-length") is not None and len(body) != declared:
        raise HTTPException(400, "Content-Length does not match the request body")
    return bytes(body)


def register_routes(
    app: FastAPI,
    service: RenderService,
    *,
    hub_token: str,
    fleet_token: Callable[[], str | None],
    settings: Callable[[], dict[str, Any]],
) -> None:
    def scope(request: Request) -> str:
        return _actor(request, hub_token, fleet_token, settings)

    @app.get("/api/hub/final-renders/blobs/{sha256}/status")
    async def blob_status(sha256: str, size_bytes: int, request: Request):
        owner = scope(request)
        try:
            return service.assets.upload_by_digest(owner, sha256, size_bytes)
        except AssetNotFoundError as exc:
            raise HTTPException(404, exc.detail) from exc
        except RenderAssetError as exc:
            raise HTTPException(409 if exc.code == "UPLOAD_CONFLICT" else 400,
                                exc.detail) from exc

    @app.get("/api/hub/final-renders/capability")
    async def render_capability(request: Request):
        """This Mac's own final-render worker row, for a controller that must publish it.

        A controller reads this over the fleet credential and republishes the same closed
        shape in its own capabilities, which is what lets the Gateway see a joined leaf's
        render worker. Absent (null) means this Hub has no render authority at all.
        """
        scope(request)
        return service.executor.capability() if service.executor is not None else None

    @app.put("/api/hub/final-renders/blobs/{sha256}")
    async def put_blob(sha256: str, request: Request):
        owner = scope(request)
        match = _CONTENT_RANGE.fullmatch(request.headers.get("content-range") or "")
        if match is None:
            raise HTTPException(400, "exact Content-Range is required")
        start, end, total = map(int, match.groups())
        length = end - start + 1
        if start < 0 or end < start or end >= total or length > service.assets.max_chunk_bytes:
            raise HTTPException(400, "Content-Range is invalid or too large")
        if request.headers.get("content-length") != str(length):
            raise HTTPException(400, "Content-Length must match Content-Range")
        upload = service.assets.begin_upload(owner, sha256, total)
        body = await _bounded_body(request, service.assets.max_chunk_bytes)
        offset = service.assets.append_chunk(
            owner, upload["upload_id"], start, io.BytesIO(body), length)
        if offset == total:
            return service.assets.finish_upload(owner, upload["upload_id"])
        return service.assets.upload_status(owner, upload["upload_id"])

    @app.post("/api/hub/final-renders")
    async def submit(request: Request):
        owner = scope(request)
        key = request.headers.get("idempotency-key") or ""
        vector = request.headers.get("x-studio-render-qualification-vector")
        try:
            return service.accept(owner, key, await _bounded_body(request, MAX_REQUEST_BYTES),
                                  qualification_vector=vector)
        except (ValueError, RenderJobConflict) as exc:
            raise HTTPException(409 if isinstance(exc, RenderJobConflict) else 400, str(exc)) from exc
        except RenderJobUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/hub/final-renders/by-idempotency-key/{key}")
    async def by_key(key: str, request: Request):
        return _answer(service.by_key(scope(request), key))

    @app.post("/api/hub/final-renders/by-idempotency-key/{key}/cancel")
    async def cancel_by_key(key: str, request: Request):
        owner = scope(request)
        try:
            body = json.loads(await _bounded_body(request, _MAX_ACK_BYTES))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(400, "invalid cancellation fence") from exc
        if type(body) is not dict or set(body) != {"expected_request_sha256"}:
            raise HTTPException(400, "invalid cancellation fence")
        try:
            return await service.cancel_by_key(
                owner, key, body["expected_request_sha256"])
        except ValueError as exc:
            raise HTTPException(
                409 if isinstance(exc, RenderJobConflict) else 400, str(exc)) from exc

    @app.get("/api/hub/final-renders/{job_id}")
    async def status(job_id: str, request: Request):
        return _answer(service.get(scope(request), job_id))

    @app.post("/api/hub/final-renders/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        owner = scope(request)
        try:
            return _answer(await service.cancel(owner, job_id))
        except RenderJobNotFound as exc:
            raise HTTPException(404, "render job not found") from exc

    @app.get("/api/hub/final-renders/{job_id}/output")
    async def output(job_id: str, request: Request):
        owner = scope(request)
        job = _answer(service.get(owner, job_id))
        path = service.output_path(owner, job)
        receipt = job["output_receipt"]
        expected_etag = f'"{receipt["output_sha256"]}"'
        offered_etag = request.headers.get("if-match")
        if offered_etag is None:
            raise HTTPException(428, "exact render output If-Match is required")
        if offered_etag != expected_etag:
            raise HTTPException(412, "render output receipt changed")
        total = receipt["output_size_bytes"]
        requested = request.headers.get("range")
        match = _RANGE.fullmatch(requested or "")
        if match is None:
            raise HTTPException(416, "one explicit byte range is required")
        start, end = map(int, match.groups())
        if start > end or end >= total or end - start + 1 > _MAX_OUTPUT_RANGE:
            raise HTTPException(416, "render output range is invalid or too large")
        lease_id = secrets.token_hex(16)
        service.runtime.acquire_file_lease(owner, job_id, "reader", lease_id)
        try:
            current = service.runtime.get(owner, job_id)
            leases = (current or {}).get("leases") or {}
            if (type(current) is not dict or current.get("state") != "output_ready"
                    or leases.get("transfer") or not path.is_file()):
                service.runtime.release_file_lease(
                    owner, job_id, "reader", lease_id)
                raise HTTPException(409, "render output is being retired")
            stream = path.open("rb")
            stream.seek(start)
        except BaseException:
            service.runtime.release_file_lease(owner, job_id, "reader", lease_id)
            raise

        def body():
            remaining = end - start + 1
            try:
                while remaining:
                    piece = stream.read(min(64 * 1024, remaining))
                    if not piece:
                        raise RuntimeError("render output changed while it was being read")
                    remaining -= len(piece)
                    yield piece
            finally:
                stream.close()
                service.runtime.release_file_lease(owner, job_id, "reader", lease_id)

        headers = {
            "ETag": expected_etag,
            "X-Content-SHA256": receipt["output_sha256"],
            "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{total}",
        }
        return StreamingResponse(body(), status_code=206,
                                 media_type="video/mp4", headers=headers)

    @app.post("/api/hub/final-renders/{job_id}/ack")
    async def ack(job_id: str, request: Request):
        owner = scope(request)
        try:
            body = json.loads(await _bounded_body(request, _MAX_ACK_BYTES))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(400, "invalid acknowledgement") from exc
        if type(body) is not dict or set(body) != {"attempt_id", "receipt_digest"}:
            raise HTTPException(400, "invalid acknowledgement")
        job = _answer(service.get(owner, job_id))
        receipt = job.get("output_receipt") or {}
        if (body["attempt_id"] != job.get("attempt_id")
                or body["receipt_digest"] != receipt.get("receipt_digest")):
            raise HTTPException(409, "render output acknowledgement changed")
        service.runtime.release_file_lease(
            owner, job_id, "ack", f"output:{job['attempt_id']}")
        service.release_job_references(
            owner, str(job.get("dispatch_key") or ""))
        service.sweep()
        service._retire_output(owner, job_id)
        return {"ok": True, "job_id": job_id, "receipt_digest": body["receipt_digest"]}


def register_relay_routes(
    app: FastAPI, relay, *, hub_token: str,
    fleet_token: Callable[[], str | None],
    settings: Callable[[], dict[str, Any]],
) -> None:
    """Register the fixed controller-to-node mirror without accepting a caller URL/path."""
    async def answer(node_id, operation, identity, request, *, limit, size_bytes=None):
        _actor(request, hub_token, fleet_token, settings)
        body = await _bounded_body(request, limit) if request.method in {"POST", "PUT"} else b""
        names = {
            "range", "content-range", "content-length", "content-type",
            "if-match", "idempotency-key", "x-studio-render-qualification-vector",
        }
        headers = {name: value for name, value in request.headers.items() if name in names}
        result = await relay(
            node_id, operation, identity=identity, body=body,
            headers=headers, size_bytes=size_bytes,
        )
        return Response(content=result.body, status_code=result.status_code,
                        headers=result.headers)

    @app.put("/api/hub/fleet/nodes/{node_id}/final-renders/blobs/{sha256}")
    async def relay_blob_put(node_id: str, sha256: str, request: Request):
        return await answer(node_id, "blob_put", sha256, request, limit=_MAX_RELAY_CHUNK)

    @app.get("/api/hub/fleet/nodes/{node_id}/final-renders/blobs/{sha256}/status")
    async def relay_blob_status(
        node_id: str, sha256: str, size_bytes: int, request: Request,
    ):
        return await answer(node_id, "blob_status", sha256, request, limit=0,
                            size_bytes=size_bytes)

    @app.post("/api/hub/fleet/nodes/{node_id}/final-renders")
    async def relay_submit(node_id: str, request: Request):
        return await answer(node_id, "submit", "", request, limit=MAX_REQUEST_BYTES)

    @app.get(
        "/api/hub/fleet/nodes/{node_id}/final-renders/by-idempotency-key/{key}")
    async def relay_by_key(node_id: str, key: str, request: Request):
        return await answer(node_id, "key_get", key, request, limit=0)

    @app.post(
        "/api/hub/fleet/nodes/{node_id}/final-renders/by-idempotency-key/{key}/cancel")
    async def relay_cancel_by_key(node_id: str, key: str, request: Request):
        return await answer(node_id, "key_cancel", key, request, limit=_MAX_ACK_BYTES)

    @app.get("/api/hub/fleet/nodes/{node_id}/final-renders/{job_id}")
    async def relay_status(node_id: str, job_id: str, request: Request):
        return await answer(node_id, "status", job_id, request, limit=0)

    @app.post("/api/hub/fleet/nodes/{node_id}/final-renders/{job_id}/cancel")
    async def relay_cancel(node_id: str, job_id: str, request: Request):
        return await answer(node_id, "cancel", job_id, request, limit=_MAX_ACK_BYTES)

    @app.get("/api/hub/fleet/nodes/{node_id}/final-renders/{job_id}/output")
    async def relay_output(node_id: str, job_id: str, request: Request):
        return await answer(node_id, "output", job_id, request, limit=0)

    @app.post("/api/hub/fleet/nodes/{node_id}/final-renders/{job_id}/ack")
    async def relay_ack(node_id: str, job_id: str, request: Request):
        return await answer(node_id, "ack", job_id, request, limit=_MAX_ACK_BYTES)
