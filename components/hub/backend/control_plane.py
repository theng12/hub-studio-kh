"""Site-controller identity and optional PostgreSQL observability shadow.

GenStudio KH permanently owns global customer jobs, attempts, routing, retry,
leases, fencing, billing, and assets. Studio Hub keeps the proven local SQLite
queues authoritative for site execution and may publish heartbeats, inventory,
capacity, and execution evidence to PostgreSQL. It never claims global work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .registry import DATA_DIR, label_for, machine_enabled, studio_enabled
from .controller_settings_lock import settings_writer_lock
from .resources import host_stats

SETTINGS_FILE = DATA_DIR / "controller_settings.json"


def _write_private_file(path: Path, payload: str) -> None:
    """Write a small file atomically, and privately.

    `write_text` truncates the target first, so a crash - or a full disk - between the truncation and
    the write left a partial `controller_settings.json`. The next start reads it as unreadable and
    falls back to the defaults, which is how this Mac's identity (and the settings that keep it
    contained) silently reset. A temporary file in the same directory plus `os.replace` makes the
    change atomic: either the new content is there, or the old content still is. Mode 0600 because
    the file holds this Mac's identity, not just preferences.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
DATABASE_URL_FILE = DATA_DIR / ".controller_database_url"
MIGRATION_FILE = Path(__file__).resolve().parent / "migrations" / "001_controller_foundation.sql"
BOUNDARY_MIGRATION_FILE = (
    Path(__file__).resolve().parent / "migrations" / "002_execution_evidence_boundary.sql")
MIGRATION_FILES = (MIGRATION_FILE, BOUNDARY_MIGRATION_FILE)
ROLES = {"standalone", "controller", "agent"}
DATABASE_MODES = {"off", "shadow"}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
HEARTBEAT_INTERVAL_SECONDS = 10.0
MAX_PENDING_SHADOW_BYTES = 128 * 1024 * 1024

_settings_cache: dict | None = None
_settings_lock = threading.RLock()
_pending_jobs: dict[tuple[str, str], str] = {}
_pending_job_bytes = 0
_pending_lock = threading.Lock()


def _safe_id(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return cleaned[:100] if cleaned and ID_PATTERN.fullmatch(cleaned[:100]) else fallback


def defaults() -> dict:
    hostname = _safe_id(socket.gethostname().split(".", 1)[0], "studiohub")
    return {
        "version": 2,
        "role": "standalone",
        "site_id": "local-site",
        "site_name": "Local site",
        "controller_id": f"{hostname}-hub"[:100],
        "database_mode": "off",
        "parent_controller_url": None,
        # "minted" when this Hub generated it, "adopted" when it took a controller's during a join.
        # Absent in settings written before this existed, and absent is treated as minted unless the
        # machine is joining, which is the conservative reading: never rotate a credential that
        # might be the one its own nodes hold.
        "fleet_credential_origin": "minted",
        # **Retirement after a handover.** When this controller hands its group to a promoted
        # node it keeps its history and stops taking new work, and the only durable record of
        # that decision is here: a Mac that answered "yes" to a dispatch request an hour after
        # handing over would be serving two controllers at once. `accepts_customer_jobs` reads
        # these, so the retirement is enforced by the same function every remote caller uses.
        "handed_over_to": None,
        "handed_over_at": None,
        # **The last address-only repoint this Mac applied.** One record, holding the operation id,
        # the address it moved from, the address it moved to and when. It has a second job beyond
        # bookkeeping: the app reads it at startup so a Mac whose Hub followed a new controller
        # stops re-joining the old one, and an exact replay of the same operation is answered from
        # here instead of being applied twice. `None` until this Mac is repointed.
        "repoint_receipt": None,
    }


def ensure_controller_id() -> None:
    """Write the derived `controller_id` to disk once, so it stops following the hostname.

    `defaults()` re-derives it from the hostname on every read until a save happens, so renaming
    the Mac in macOS before then changed the id its controller knows it by. Once written, the
    saved value wins. The label (`machine_name`) is what people see; this is only the key.
    """
    global _settings_cache
    with _settings_lock:
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except OSError:
            saved = {}
        except ValueError:
            return  # a damaged file is not ours to overwrite
        if not isinstance(saved, dict) or saved.get("controller_id"):
            return
        merged = {**defaults(), **saved}
        _write_private_file(SETTINGS_FILE, json.dumps(merged, indent=2) + "\n")
        _settings_cache = merged


def load_settings() -> dict:
    global _settings_cache
    with _settings_lock:
        if _settings_cache is None:
            saved = {}
            try:
                saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    saved = {}
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            _settings_cache = {**defaults(), **saved}
        result = dict(_settings_cache)

    # Environment values make immutable deployments possible without putting
    # credentials or site identity in the repository.
    for env, key in (
        ("STUDIOHUB_ROLE", "role"),
        ("STUDIOHUB_SITE_ID", "site_id"),
        ("STUDIOHUB_SITE_NAME", "site_name"),
        ("STUDIOHUB_CONTROLLER_ID", "controller_id"),
        ("STUDIOHUB_DATABASE_MODE", "database_mode"),
    ):
        if os.environ.get(env):
            result[key] = os.environ[env].strip()
    return result


def database_url() -> str | None:
    # Agent Hubs are local worker authorities and must never receive or use a
    # PostgreSQL credential. A stale local file is removed when agent mode is
    # saved; an accidentally inherited environment value is ignored here.
    if load_settings()["role"] == "agent":
        return None
    env = os.environ.get("STUDIOHUB_DATABASE_URL", "").strip()
    if env:
        return env
    try:
        value = DATABASE_URL_FILE.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _database_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or "configured"
        port = f":{parsed.port}" if parsed.port else ""
        database = parsed.path.strip("/") or "database"
        return f"{parsed.scheme or 'postgresql'}://{host}{port}/{database}"
    except ValueError:
        return "configured"


def public_settings() -> dict:
    settings = load_settings()
    url = database_url()
    machine_name = label_for("local")
    if machine_name == "local":
        machine_name = socket.gethostname().split(".", 1)[0] or "local"
    return {
        **settings,
        # The local machine key is deliberately stable ("local"). The real
        # hostname is the default operator-facing name; an explicit rename
        # remains cosmetic and never changes routing or fleet identity.
        "machine_name": machine_name,
        "database_configured": bool(url),
        "database_endpoint": _database_endpoint(url),
        "database_source": (
            None if not url else (
                "environment" if os.environ.get("STUDIOHUB_DATABASE_URL")
                else "local private file"
            )
        ),
        "sqlite_authoritative": True,
        "global_job_claiming": False,
        "global_authority": "genstudio",
        "migration_stage": "shadow" if settings["database_mode"] == "shadow" else "local",
    }


def execution_identity() -> dict:
    """Site/controller context published beside raw machine ids.

    `machine_id` stays the routing sentinel ("local") and `site_id` its default
    ("local-site"); these qualify which controller and site that sentinel belongs
    to, so one site's "local" is never read as another's. `machine_label` is this
    Hub's own configured name — the local executor's label; a remote executor's
    label is its own (see `broker.terminal_result`). `controller_id` is stable per
    Hub but only unique within the roster that stores it: not a global identity.
    """
    settings = load_settings()
    label = public_settings()["machine_name"]
    return {
        "machine_label": label,
        "controller_id": settings["controller_id"],
        "controller_label": label,
        "site_id": settings["site_id"],
        "site_name": settings["site_name"],
    }


def save_settings(values: dict, *, new_database_url: str | None = None,
                  clear_database_url: bool = False,
                  writer_blocking: bool = False) -> dict:
    global _settings_cache
    with settings_writer_lock(blocking=writer_blocking):
        current = load_settings()
        updated = {
            "version": 2,
            "role": str(values.get("role", current["role"])).strip().lower(),
            "site_id": str(values.get("site_id", current["site_id"])).strip().lower(),
            "site_name": str(values.get("site_name", current["site_name"])).strip(),
            "controller_id": str(values.get("controller_id", current["controller_id"])).strip().lower(),
            "database_mode": str(values.get("database_mode", current["database_mode"])).strip().lower(),
            "parent_controller_url": values.get(
                "parent_controller_url", current.get("parent_controller_url")),
        }
        # Every key `defaults()` defines has to be carried here, or `save_settings` writes a file -
        # and seeds the in-memory cache - without it, and the value silently reverts to its default.
        # This one was dropped exactly that way: joining wrote "adopted", nothing persisted it, and
        # every reader was told "minted". That made the app's automatic re-join impossible (it only
        # fires for an adopted credential) and left promotion containment resting on the operator
        # having set the role by hand.
        origin = str(values.get("fleet_credential_origin",
                                current.get("fleet_credential_origin") or "minted")).strip().lower()
        if origin not in {"minted", "adopted"}:
            raise ValueError("fleet_credential_origin must be 'minted' or 'adopted'")
        updated["fleet_credential_origin"] = origin
        # Carried like every other key in `defaults()`. The file's own comment above records what
        # dropping one costs: joining wrote "adopted", nothing persisted it, and every reader was
        # told "minted".
        for key in ("handed_over_to", "handed_over_at", "repoint_receipt"):
            if key in values:
                updated[key] = values[key]
            else:
                updated[key] = current.get(key)
        if updated["role"] not in ROLES:
            raise ValueError(f"role must be one of {sorted(ROLES)}")
        if updated["database_mode"] not in DATABASE_MODES:
            raise ValueError(f"database_mode must be one of {sorted(DATABASE_MODES)}")
        if updated["role"] != "controller" and updated["database_mode"] != "off":
            raise ValueError("PostgreSQL shadow mode is available only in controller role")
        if not ID_PATTERN.fullmatch(updated["site_id"]):
            raise ValueError("site_id must use lowercase letters, numbers, dots, dashes, or underscores")
        if not ID_PATTERN.fullmatch(updated["controller_id"]):
            raise ValueError("controller_id must use lowercase letters, numbers, dots, dashes, or underscores")
        if not 1 <= len(updated["site_name"]) <= 120:
            raise ValueError("site_name must be between 1 and 120 characters")
        # **This Mac's identity is not editable from inside a group** (owner, 2026-10-25: *"what are
        # the identifiers on Studio Hub for each machine? Make sure they're prominent and easily
        # edited, so I don't repeat my current mistake, because they're hard to change."*).
        #
        # Editable and routed-by are not the same thing. `controller_id` becomes this Mac's roster
        # `node_id` when it joins, and its controller addresses work to that id; changing it under a
        # live membership would leave the controller holding a row for a Mac that no longer answers
        # to it, and the Mac's own credential would still be keyed to the old id. So the answer is a
        # refusal that names the way out — `POST /api/hub/fleet/leave` — rather than a rename that
        # quietly breaks the group. Read-only while joined, freely editable standalone: the whole
        # point is that the owner sets these up *before* he enrols, on a clean slate.
        if str(current.get("fleet_credential_origin") or "minted").lower() == "adopted":
            for key, label in (("controller_id", "Machine ID"),
                               ("site_id", "Site ID"), ("site_name", "Site name")):
                if updated[key] != current.get(key):
                    raise ValueError(
                        f"{label} cannot be changed while this Mac is in a group: its controller "
                        f"routes by this Mac's Machine ID and knows it by these. Leave the fleet "
                        f"first (Connections → Leave this group), change it, then join again.")
        # **A second Mac may not answer to an id this controller already routes to.** A join
        # registers `controller_id` as the roster's `node_id`, so two machines with one id are one
        # routed machine: work meant for one lands on the other and the roster shows a single row.
        # Checked against this Mac's own roster, which is the only list it can be sure of locally —
        # a controller it is not joined to cannot be asked, and guessing there would be worse.
        if updated["controller_id"] != current.get("controller_id"):
            from . import fleet_nodes
            existing = fleet_nodes._read().get(updated["controller_id"])
            if existing is not None:
                raise ValueError(
                    f"'{updated['controller_id']}' is already a Mac in this controller's roster "
                    f"({existing.get('name') or updated['controller_id']} at "
                    f"{existing.get('address') or 'an unknown address'}). Two Macs answering to one "
                    f"Machine ID are one routed machine, not two: choose another id.")
        candidate_url = None
        if new_database_url is not None and new_database_url.strip():
            candidate_url = new_database_url.strip()
            parsed = urlsplit(candidate_url)
            if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
                raise ValueError("database_url must be a PostgreSQL connection URL")
        if updated["role"] == "agent" and os.environ.get("STUDIOHUB_DATABASE_URL"):
            raise ValueError(
                "Agent Hubs must not have STUDIOHUB_DATABASE_URL; remove it before enabling agent mode")
        if updated["role"] == "agent" and candidate_url:
            raise ValueError("Agent Hubs must not store PostgreSQL credentials")
        if updated["role"] == "agent":
            clear_database_url = True
        # A machine that was a node holds the credential of the controller it joined. Promoting it
        # to controller without minting its own would leave two independent groups sharing one
        # secret: the new controller, and the old controller plus every node under it. That is the
        # whole containment the five-controller split is supposed to buy, so it is worth one
        # rotation at the moment of promotion rather than a credential that quietly spans groups.
        previous_role = str(current.get("role") or "").lower()
        origin = str(current.get("fleet_credential_origin") or "minted").lower()
        if updated["role"] == "controller" and (previous_role == "agent" or origin == "adopted"):
            from . import fleet_nodes
            from . import peers
            peers.set_fleet_token("")          # empty asks for a fresh one
            # **And the borrowed node credential goes with the borrowed token.** Promotion is
            # where a Mac stops being somebody's node and becomes its own controller: the
            # credential its old controller issued is that controller's record, not this Mac's,
            # and a promoted Mac that kept it would hold a working key into the group it just
            # left. Same reasoning as the token rotation beside it, and the same moment.
            fleet_nodes.set_node_credential(None)
            updated["fleet_credential_origin"] = "minted"
        # Which controller this Mac joined is a fact about whose credential it is holding, so it
        # lasts exactly as long as that credential does. It used to be cleared whenever the role was
        # not "agent", which meant any later save on a Mac that had joined but not yet been switched
        # to Node - a rename, a listen address, an unrelated write - silently erased the only record
        # this Mac had of the group it belonged to. Reading it back now survives every save that
        # does not actually leave the group.
        if updated["fleet_credential_origin"] != "adopted":
            updated["parent_controller_url"] = None
        elif updated["parent_controller_url"] is not None:
            updated["parent_controller_url"] = str(updated["parent_controller_url"]).strip() or None

        _write_private_file(SETTINGS_FILE, json.dumps(updated, indent=2) + "\n")
        with _settings_lock:
            _settings_cache = updated
        if clear_database_url:
            DATABASE_URL_FILE.unlink(missing_ok=True)
        elif candidate_url:
            _write_private_file(DATABASE_URL_FILE, candidate_url + "\n")
        runtime.wake()
        return public_settings()


def accepts_customer_jobs() -> bool:
    """Whether a **remote** caller may submit customer work to this Hub.

    Agents execute controller commands but never own customer submissions, so a node refuses a
    controller's job submission with a 409. That rule is about who *owns* the work, and it does not
    apply to the person sitting at the Mac: ``standalone`` asks
    ``auth.presented_owner_token`` alongside this and lets the owner's own credential through, so a
    Mac switched to a node can still generate from its own window. See the call sites.

    **A handed-over controller is retired and refuses too.** It has told every Mac it owned to
    report to a new head, and one that kept answering would put the same model on two controllers'
    books. The owner then removes it from the Gateway and wipes or sells it (A3 step 4). Refusing
    here — the one function every remote submission passes — is what makes "retired" a fact rather
    than a label on a page.
    """
    settings = load_settings()
    if str(settings.get("handed_over_to") or "").strip():
        return False
    return settings["role"] != "agent"


def refusal_for_remote_work() -> str:
    """Why a remote caller was refused, in the two different words the two reasons need.

    **Agent mode and retirement are not the same state and must not share a sentence.** A node
    in agent mode is doing its job; a controller that has handed over is finished, and an
    operator reading "this Hub is in agent mode" on a Mac he just retired would go looking at
    the wrong setting. One function so the two call sites cannot drift apart.
    """
    settings = load_settings()
    target = str(settings.get("handed_over_to") or "").strip()
    if target:
        return (f"This controller handed its group over to {target} and is retired: it keeps "
                f"its history but takes no new work. Remove it from the Gateway and wipe or "
                f"sell it.")
    return ("This Hub is in agent mode, so a controller submits work here. This Mac's own app "
            "can still run work locally with this Mac's Hub token.")


def queue_shadow_job(kind: str, batch: dict) -> None:
    global _pending_job_bytes
    settings = load_settings()
    if (settings["role"] != "controller"
            or settings["database_mode"] != "shadow" or not database_url()):
        return
    snapshot = json.dumps(batch, default=str, separators=(",", ":"))
    with _pending_lock:
        key = (kind, str(batch.get("id")))
        previous = _pending_jobs.pop(key, None)
        if previous is not None:
            _pending_job_bytes -= len(previous.encode("utf-8"))
        incoming_bytes = len(snapshot.encode("utf-8"))
        while (_pending_jobs
               and _pending_job_bytes + incoming_bytes > MAX_PENDING_SHADOW_BYTES):
            # PostgreSQL is only a shadow in this stage. Bound outage memory;
            # SQLite retains the authoritative record for a later backfill.
            oldest = next(iter(_pending_jobs))
            removed = _pending_jobs.pop(oldest)
            _pending_job_bytes -= len(removed.encode("utf-8"))
        _pending_jobs[key] = snapshot
        _pending_job_bytes += incoming_bytes
    runtime.wake()


def _connect(url: str):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL driver is not installed; update Studio Hub dependencies") from exc
    return psycopg.connect(url, connect_timeout=5)


def _safe_database_error(exc: Exception, url: str) -> str:
    message = str(exc).replace(url, "<database>")
    try:
        parsed = urlsplit(url)
        for secret in (parsed.password, parsed.username):
            if secret:
                message = message.replace(secret, "***")
    except ValueError:
        pass
    message = " ".join(message.split())[:300]
    return f"{type(exc).__name__}: {message or 'PostgreSQL check failed'}"


def _job_state(batch: dict) -> str:
    if batch.get("cancelled"):
        return "cancelled"
    items = batch.get("items") or batch.get("packs") or []
    states = {str(item.get("state") or "unknown") for item in items}
    if "running" in states:
        return "running"
    if "queued" in states:
        return "queued"
    if states and states <= {"done", "complete"}:
        return "done"
    if "error" in states or "partial" in states:
        return "error"
    return str(batch.get("status") or "unknown")


def _timestamp(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class ControlPlaneRuntime:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._monitor = None
        self._version = "0.0.0"
        self._started_at = time.time()
        self._status = {
            "database": "off", "connected": False, "schema_version": None,
            "last_success_at": None, "last_error": None, "pending_job_snapshots": 0,
        }

    async def start(self, monitor, app_version: str) -> None:
        self._monitor = monitor
        self._version = app_version
        self._started_at = time.time()
        if self._task is None or self._task.done():
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._wake = None

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def status(self) -> dict:
        with _pending_lock:
            pending = len(_pending_jobs)
            pending_bytes = _pending_job_bytes
        return {**self._status, "pending_job_snapshots": pending,
                "pending_shadow_bytes": pending_bytes}

    def capacity(self, monitor_override=None) -> dict:
        from . import broker, chat_jobs, transcription_jobs

        monitor = monitor_override or self._monitor
        registry = list(getattr(monitor, "registry", []) or [])
        statuses = getattr(monitor, "status", {}) or {}
        modalities: dict[str, dict] = {}
        for studio in registry:
            modality = studio.get("modality", "unknown")
            row = modalities.setdefault(modality, {"registered": 0, "online": 0, "working": 0})
            row["registered"] += 1
            row["online"] += int(statuses.get(studio["id"], {}).get("status") == "up")
            row["working"] += int(
                studio["id"] in broker.busy_studios()
                or studio["id"] in chat_jobs.busy_studios
                or studio["id"] in transcription_jobs.busy_studios
            )
        queued = sum(
            item.get("state") == "queued"
            for batch in broker.batches.values() for item in batch.get("items", [])
        ) + sum(
            item.get("state") == "queued"
            for batch in chat_jobs.batches.values() for item in batch.get("packs", [])
        ) + sum(
            item.get("state") == "queued"
            for batch in transcription_jobs.batches.values() for item in batch.get("items", [])
        )
        return {
            "site_id": load_settings()["site_id"],
            "host": host_stats(),
            "studios": modalities,
            "machines": len({s.get("machine", "local") for s in registry}),
            "queue_depth": queued,
            "accepting_customer_jobs": accepts_customer_jobs(),
            "measured_at": time.time(),
        }

    def readiness(self) -> dict:
        settings = public_settings()
        status = self.status()
        role = settings["role"]
        shadow_enabled = role == "controller" and settings["database_mode"] == "shadow"
        database_ready = status["connected"] if shadow_enabled else None
        return {
            # Optional observability must never remove a healthy local
            # controller from GenStudio's routing pool.
            "ready": True,
            "role": role,
            "site_id": settings["site_id"],
            "controller_id": settings["controller_id"],
            "migration_stage": settings["migration_stage"],
            "database_ready": database_ready,
            "sqlite_authoritative": True,
            "global_job_claiming": False,
            "global_authority": "genstudio",
            "reason": None,
            "telemetry_warning": (
                status.get("last_error") or "PostgreSQL shadow is unavailable"
                if shadow_enabled and not database_ready else None
            ),
        }

    async def capability_snapshot(self, app_version: str, monitor_override=None) -> dict:
        """Private GenStudio routing facts composed from existing local state."""
        from .capabilities import build_snapshot

        monitor = monitor_override or self._monitor
        return await build_snapshot(
            monitor,
            app_version=app_version,
            settings=public_settings(),
            readiness=self.readiness(),
            base_capacity=self.capacity(monitor),
        )

    async def check_now(self) -> dict:
        settings = load_settings()
        url = database_url()
        if settings["role"] != "controller" or settings["database_mode"] == "off" or not url:
            self._status.update(database="off" if not url else "configured",
                                connected=False, schema_version=None,
                                last_error=None if not url else "Database mode is off")
            return {"settings": public_settings(), "runtime": self.status(),
                    "readiness": self.readiness()}
        try:
            result = await asyncio.to_thread(self._sync_once, settings, url)
            self._status.update(database="postgresql", connected=True,
                                schema_version=2, last_success_at=time.time(), last_error=None)
            return {"ok": True, **result, "settings": public_settings(),
                    "runtime": self.status(), "readiness": self.readiness()}
        except Exception as exc:
            message = _safe_database_error(exc, url)
            self._status.update(database="postgresql", connected=False, last_error=message)
            return {"ok": False, "settings": public_settings(), "runtime": self.status(),
                    "readiness": self.readiness()}

    async def _loop(self) -> None:
        while True:
            await self.check_now()
            try:
                if self._wake is None:
                    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                else:
                    self._wake.clear()
                    await asyncio.wait_for(self._wake.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                return

    def _sync_once(self, settings: dict, url: str) -> dict:
        global _pending_job_bytes
        capacity = self.capacity()
        with _pending_lock:
            pending = dict(_pending_jobs)
        with _connect(url) as connection:
            for migration in MIGRATION_FILES:
                connection.execute(migration.read_text(encoding="utf-8"))
            self._write_heartbeat(connection, settings, capacity)
            for (kind, local_id), batch_json in pending.items():
                self._write_job(
                    connection, settings, kind, local_id, json.loads(batch_json))
            connection.commit()
        if pending:
            with _pending_lock:
                for key, snapshot in pending.items():
                    if _pending_jobs.get(key) == snapshot:
                        _pending_jobs.pop(key, None)
                        _pending_job_bytes -= len(snapshot.encode("utf-8"))
        return {"schema_version": 2, "shadow_jobs_written": len(pending)}

    def _write_heartbeat(self, connection, settings: dict, capacity: dict) -> None:
        connection.execute(
            """INSERT INTO sites(site_id, display_name, updated_at)
               VALUES (%s, %s, NOW())
               ON CONFLICT(site_id) DO UPDATE SET display_name=EXCLUDED.display_name,
                 updated_at=NOW()""",
            (settings["site_id"], settings["site_name"]),
        )
        connection.execute(
            """INSERT INTO controllers(controller_id, site_id, role, hostname,
                 app_version, started_at, last_seen_at, ready, migration_stage, capacity)
               VALUES (%s,%s,%s,%s,%s,TO_TIMESTAMP(%s),NOW(),TRUE,'shadow',CAST(%s AS jsonb))
               ON CONFLICT(controller_id) DO UPDATE SET site_id=EXCLUDED.site_id,
                 role=EXCLUDED.role, hostname=EXCLUDED.hostname,
                 app_version=EXCLUDED.app_version, last_seen_at=NOW(), ready=TRUE,
                 migration_stage='shadow', capacity=EXCLUDED.capacity""",
            (settings["controller_id"], settings["site_id"], settings["role"],
             socket.gethostname(), self._version, self._started_at, json.dumps(capacity)),
        )
        monitor = self._monitor
        statuses = getattr(monitor, "status", {}) or {}
        for studio in list(getattr(monitor, "registry", []) or []):
            machine = studio.get("machine", "local")
            status = statuses.get(studio["id"], {})
            machine_id = f"{settings['site_id']}:{machine}"
            global_studio_id = f"{machine_id}:{studio.get('modality', 'unknown')}"
            connection.execute(
                """INSERT INTO machines(machine_id, site_id, authority_controller_id,
                     address, enabled, reachable, last_seen_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN NOW() ELSE NULL END,NOW())
                   ON CONFLICT(machine_id) DO UPDATE SET
                     authority_controller_id=EXCLUDED.authority_controller_id,
                     address=EXCLUDED.address, enabled=EXCLUDED.enabled,
                     reachable=EXCLUDED.reachable, last_seen_at=EXCLUDED.last_seen_at,
                     updated_at=NOW()""",
                (machine_id, settings["site_id"], settings["controller_id"],
                 studio.get("host"), machine_enabled(machine), status.get("status") == "up",
                 status.get("status") == "up"),
            )
            connection.execute(
                """INSERT INTO studios(studio_id, runtime_id, machine_id, site_id,
                     modality, enabled, status, app_version, last_seen_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                     CASE WHEN %s='up' THEN NOW() ELSE NULL END,NOW())
                   ON CONFLICT(studio_id) DO UPDATE SET runtime_id=EXCLUDED.runtime_id,
                     enabled=EXCLUDED.enabled, status=EXCLUDED.status,
                     app_version=EXCLUDED.app_version, last_seen_at=EXCLUDED.last_seen_at,
                     updated_at=NOW()""",
                (global_studio_id, studio["id"], machine_id, settings["site_id"],
                 studio.get("modality", "unknown"), studio_enabled(machine, studio["id"]),
                 status.get("status", "unknown"), status.get("app_version"),
                 status.get("status", "unknown")),
            )

    def _write_job(self, connection, settings: dict, kind: str,
                   local_id: str, batch: dict) -> None:
        items = batch.get("items") or batch.get("packs") or []
        state = _job_state(batch)
        created = _timestamp(batch.get("created_at")) or time.time()
        finished = _timestamp(batch.get("finished_at"))
        execution = batch.get("genstudio_execution") or {}
        local_idempotency = batch.get("client_request_id") or batch.get("idempotency_key")
        # This is a site execution-evidence identity, never a global customer
        # job id. Re-execution at another site intentionally creates another
        # evidence row under that controller.
        evidence_id = f"{settings['controller_id']}:{kind}:{local_id}"
        stored_idempotency = (
            f"{settings['controller_id']}:"
            f"{hashlib.sha256(str(local_idempotency).encode()).hexdigest()}"
            if local_idempotency else None)
        connection.execute(
            """INSERT INTO jobs(job_id, local_job_id, source_controller_id, site_id,
                 job_kind, idempotency_key, request_fingerprint, state, payload,
                 genstudio_job_id, genstudio_attempt_id, external_idempotency_hash,
                 external_fencing_token, operation, model_revision, voice_revision,
                 evidence_site_id,
                 created_at, updated_at, finished_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CAST(%s AS jsonb),
                 %s,%s,%s,%s,%s,%s,%s,%s,
                 TO_TIMESTAMP(%s),NOW(),
                 CASE WHEN CAST(%s AS double precision) IS NULL THEN NULL
                   ELSE TO_TIMESTAMP(CAST(%s AS double precision)) END)
               ON CONFLICT(job_id) DO UPDATE SET state=EXCLUDED.state,
                 payload=EXCLUDED.payload,
                 genstudio_job_id=EXCLUDED.genstudio_job_id,
                 genstudio_attempt_id=EXCLUDED.genstudio_attempt_id,
                 external_idempotency_hash=EXCLUDED.external_idempotency_hash,
                 external_fencing_token=EXCLUDED.external_fencing_token,
                 operation=EXCLUDED.operation,
                 model_revision=EXCLUDED.model_revision,
                 voice_revision=EXCLUDED.voice_revision,
                 evidence_site_id=EXCLUDED.evidence_site_id,
                 updated_at=NOW(), finished_at=EXCLUDED.finished_at""",
            (evidence_id, local_id, settings["controller_id"], settings["site_id"],
             kind, stored_idempotency, batch.get("request_fingerprint"), state,
             json.dumps(batch), execution.get("genstudio_job_id"),
             execution.get("genstudio_attempt_id"), execution.get("idempotency_hash"),
             execution.get("fencing_token"), execution.get("operation"),
             execution.get("model_revision"), execution.get("voice_revision"),
             execution.get("site_id") or settings["site_id"],
             created, finished, finished),
        )
        for index, item in enumerate(items):
            item_id = str(item.get("item_id") or item.get("id") or item.get("index", index))
            terminal = item.get("terminal_result") or {}
            connection.execute(
                """INSERT INTO job_items(job_id, item_id, item_index, state,
                     assigned_machine, assigned_studio, attempt, artifact_url,
                     artifact_sha256, error, payload, started_at, finished_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CAST(%s AS jsonb),
                     CASE WHEN CAST(%s AS double precision) IS NULL THEN NULL
                       ELSE TO_TIMESTAMP(CAST(%s AS double precision)) END,
                     CASE WHEN CAST(%s AS double precision) IS NULL THEN NULL
                       ELSE TO_TIMESTAMP(CAST(%s AS double precision)) END,NOW())
                   ON CONFLICT(job_id,item_id) DO UPDATE SET state=EXCLUDED.state,
                     assigned_machine=EXCLUDED.assigned_machine,
                     assigned_studio=EXCLUDED.assigned_studio, attempt=EXCLUDED.attempt,
                     artifact_url=EXCLUDED.artifact_url,
                     artifact_sha256=EXCLUDED.artifact_sha256, error=EXCLUDED.error,
                     payload=EXCLUDED.payload, started_at=EXCLUDED.started_at,
                     finished_at=EXCLUDED.finished_at, updated_at=NOW()""",
                (evidence_id, item_id, int(item.get("index", index)), item.get("state", "unknown"),
                 item.get("machine"), item.get("studio"), int(item.get("tries") or 0),
                 terminal.get("artifact_url") or item.get("artifact_url"),
                 terminal.get("sha256"), item.get("error"), json.dumps(item),
                 _timestamp(item.get("started_at")), _timestamp(item.get("started_at")),
                 _timestamp(item.get("finished_at")), _timestamp(item.get("finished_at"))),
            )


runtime = ControlPlaneRuntime()


def reload_settings_from_disk() -> None:
    """Discard the identity cache after an atomic setup rollback or restore."""
    global _settings_cache
    with _settings_lock:
        _settings_cache = None
    runtime.wake()


def reload_settings_cache() -> None:
    """Discard the cached identity after a caller-owned restore."""
    reload_settings_from_disk()


def reset_for_tests() -> None:
    global _pending_job_bytes, _settings_cache
    with _settings_lock:
        _settings_cache = None
    with _pending_lock:
        _pending_jobs.clear()
        _pending_job_bytes = 0
    runtime._monitor = None
    runtime._status = {
        "database": "off", "connected": False, "schema_version": None,
        "last_success_at": None, "last_error": None, "pending_job_snapshots": 0,
    }
