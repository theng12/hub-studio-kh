"""Remote authentication for Studio Hub KH.

Trust model:
- Requests from loopback (this Mac) need no token: the Pinokio webview, local
  scripts and the local dashboard keep working untouched.
- Requests from anywhere else (LAN / Tailscale) must present either a valid
  remembered-browser session or the Hub/fleet token.  Tokens remain necessary
  for peer Hubs, scripts, and recovery; people normally sign in with the owner
  password instead.
- The static dashboard page itself is served without a token; its API calls
  are what get checked (the page shows the sign-in screen on first 401).
- A Hub nobody has given a password to accepts one shipped default password,
  under the same loopback/Tailscale rule and the same failure throttle, so an
  unattended Mac is never unreachable to its owner.  Storing any password —
  including the verifier an Agent inherits from its controller — ends that.

The owner password is salted/scrypt-hashed. Browser sessions are random opaque
values whose hashes are stored locally, so neither password nor session can be
recovered from the Hub's state files.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from .registry import DATA_DIR
TOKEN_FILE = DATA_DIR / ".hub_token"
PASSWORD_FILE = DATA_DIR / ".hub_password.json"
SESSIONS_FILE = DATA_DIR / ".hub_sessions.json"

# Paths any client may hit without a token.
PUBLIC_PATHS = {"/", "/api/health", "/api/version", "/health/live",
                "/health/ready", "/health/capacity", "/api/auth/status",
                "/api/auth/login", "/api/auth/logout",
                "/api/hub/enrollment/claim", "/api/hub/enrollment/info"}
STRICT_FLEET_SERVICE_PATHS = {
    "/api/hub/enrollment-repair/apply",
    "/api/hub/enrollment-repair-tickets/redeem",
}
COOKIE_NAME = "kh_hub_token"
SESSION_COOKIE_NAME = "kh_hub_session"
SESSION_TTL_DAYS = 90
SESSION_TTL_S = SESSION_TTL_DAYS * 24 * 60 * 60
_LOGIN_WINDOW_S = 15 * 60
_MAX_LOGIN_FAILURES = 5
_login_failures: dict[str, list[float]] = {}

# A Hub that nobody has given a password to is still the owner's Hub. Until an
# owner password exists, this one value signs in under exactly the same rules
# as a real password: loopback or Tailscale only, same failure throttle. It
# stops being accepted the moment any password is stored, including the
# verifier an Agent inherits from its controller at enrolment.
DEFAULT_OWNER_PASSWORD = "123456"
PASSWORD_MODE_DEFAULT = "default"
PASSWORD_MODE_CUSTOM = "custom"
PASSWORD_MODE_INHERITED = "inherited"
INHERITED_PASSWORD_SOURCE = "controller"
_HEX_FIELD = re.compile(r"^[0-9a-f]+$")


class ExactFleetServiceRequestError(ValueError):
    """Stable route-local rejection for enrollment-repair service traffic."""

    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def strict_fleet_service_path(path: str) -> bool:
    return (
        path in STRICT_FLEET_SERVICE_PATHS
        or path.startswith("/api/hub/enrollment-repair/status/")
        or path.startswith("/api/hub/service/startup-services/local/")
    )


def _private_direct_source(value: str) -> str:
    try:
        address = ipaddress.ip_address(str(value or "").split("%", 1)[0])
    except ValueError as exc:
        raise ExactFleetServiceRequestError("private_source_required", 403) from exc
    allowed = (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or (
            address.version == 4
            and address in ipaddress.ip_network("100.64.0.0/10")
        )
    )
    if not allowed or address.is_multicast or address.is_unspecified:
        raise ExactFleetServiceRequestError("private_source_required", 403)
    return str(address)


def require_exact_fleet_service_request(
    request: Request,
    *,
    expected_source: str | None = None,
) -> tuple[str, str]:
    """Authenticate one repair service request without broad auth fallbacks.

    The current fleet token is read before any request-specific state. Missing
    configuration therefore fails closed without invoking the legacy token
    generator. Browser, bearer, URL, and forwarded identity substitutes are
    rejected even when an otherwise-valid fleet header is also present.
    """
    from . import peers

    current = peers.current_fleet_token()
    if not current:
        raise ExactFleetServiceRequestError("fleet_token_unavailable", 503)
    if (
        request.cookies
        or request.headers.get("authorization") is not None
        or request.query_params
        or any(request.headers.get(name) is not None for name in (
            "forwarded", "x-forwarded-for", "x-forwarded-host",
            "x-forwarded-port", "x-forwarded-proto", "x-real-ip",
        ))
    ):
        raise ExactFleetServiceRequestError("credential_substitute_rejected", 403)
    offered = request.headers.get("x-hub-token")
    if not offered or not secrets.compare_digest(offered, current):
        raise ExactFleetServiceRequestError("fleet_token_mismatch", 401)
    direct_source = _private_direct_source(
        request.client.host if request.client else ""
    )
    if expected_source is not None:
        expected = _private_direct_source(expected_source)
        if not secrets.compare_digest(direct_source, expected):
            raise ExactFleetServiceRequestError("source_host_mismatch", 403)
    return direct_source, current


def _write_private(path, value: dict) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)


def _read_private(path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def load_token() -> str:
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if token:
            os.chmod(TOKEN_FILE, 0o600)
            return token
    token = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(token + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    return token


def _password_record() -> dict:
    return _read_private(PASSWORD_FILE, {})


def _configured(record: Mapping[str, Any]) -> bool:
    return all(isinstance(record.get(key), str) and record[key]
               for key in ("salt", "digest"))


def password_configured() -> bool:
    return _configured(_password_record())


def password_mode() -> str:
    """How this Hub's owner password came to be, for honest dashboard copy."""
    record = _password_record()
    if not _configured(record):
        return PASSWORD_MODE_DEFAULT
    if record.get("source") == INHERITED_PASSWORD_SOURCE:
        return PASSWORD_MODE_INHERITED
    return PASSWORD_MODE_CUSTOM


def default_password_accepted(password: Any) -> bool:
    """Whether the shipped default signs in right now."""
    if os.environ.get("STUDIOHUB_PACKAGED") == "1":
        return False
    if password_configured() or not isinstance(password, str):
        return False
    return hmac.compare_digest(password, DEFAULT_OWNER_PASSWORD)


def owner_password_verifier() -> dict | None:
    """The stored salt/digest record — never a password, never recoverable."""
    record = _password_record()
    if not _configured(record):
        return None
    version = record.get("version")
    return {
        "version": version if isinstance(version, int) and not isinstance(version, bool) else 1,
        "salt": record["salt"],
        "digest": record["digest"],
    }


def validated_password_verifier(value: Any) -> dict | None:
    """Accept only a well-formed hex verifier record of a bounded size."""
    if not isinstance(value, Mapping) or value.get("version") != 1:
        return None
    fields = {}
    for name, minimum, maximum in (("salt", 16, 256), ("digest", 32, 1024)):
        field = value.get(name)
        if (not isinstance(field, str) or len(field) % 2
                or not minimum <= len(field) <= maximum
                or _HEX_FIELD.fullmatch(field) is None):
            return None
        fields[name] = field
    return {"version": 1, **fields}


def install_password_verifier(
    value: Any, *, source: str = INHERITED_PASSWORD_SOURCE,
) -> bool:
    """Install a fleet-supplied verifier, never over a password the owner chose."""
    record = validated_password_verifier(value)
    if record is None or password_mode() == PASSWORD_MODE_CUSTOM:
        return False
    _write_private(PASSWORD_FILE, {**record, "source": source})
    clear_browser_sessions()
    return True


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                          n=2**14, r=8, p=1, maxmem=64 * 1024 * 1024)


def clear_browser_sessions() -> None:
    _write_private(SESSIONS_FILE, {"sessions": []})


def set_owner_password(password: str) -> None:
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        raise ValueError("Enter a password.")
    salt = secrets.token_bytes(16)
    digest = _password_digest(password, salt)
    # An owner-chosen password is always "custom": no source marker, so a later
    # fleet broadcast can never replace it.
    _write_private(PASSWORD_FILE, {
        "version": 1,
        "salt": salt.hex(),
        "digest": digest.hex(),
    })
    # Changing the password immediately removes remembered devices.
    clear_browser_sessions()


def verify_owner_password(password: str) -> bool:
    record = _read_private(PASSWORD_FILE, {})
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["digest"])
    except (KeyError, TypeError, ValueError):
        return False
    try:
        actual = _password_digest(password, salt)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def _session_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _live_sessions() -> list[dict]:
    now = time.time()
    rows = _read_private(SESSIONS_FILE, {}).get("sessions", [])
    live = [row for row in rows if isinstance(row, dict)
            and isinstance(row.get("hash"), str)
            and isinstance(row.get("expires_at"), (int, float))
            and row["expires_at"] > now]
    if len(live) != len(rows):
        _write_private(SESSIONS_FILE, {"sessions": live})
    return live


def create_browser_session() -> str:
    value = secrets.token_urlsafe(32)
    sessions = _live_sessions()
    sessions.append({"hash": _session_hash(value),
                     "expires_at": int(time.time() + SESSION_TTL_S)})
    # Keep a sensible finite list if the owner signs in from many browsers.
    sessions = sessions[-20:]
    _write_private(SESSIONS_FILE, {"sessions": sessions})
    return value


def valid_browser_session(value: str | None) -> bool:
    if not value:
        return False
    candidate = _session_hash(value)
    return any(hmac.compare_digest(candidate, row["hash"])
               for row in _live_sessions())


def forget_browser_session(value: str | None) -> None:
    if not value:
        return
    candidate = _session_hash(value)
    sessions = [row for row in _live_sessions()
                if not hmac.compare_digest(candidate, row["hash"])]
    _write_private(SESSIONS_FILE, {"sessions": sessions})


def set_browser_session_cookie(response, value: str) -> None:
    response.set_cookie(SESSION_COOKIE_NAME, value, max_age=SESSION_TTL_S,
                        httponly=True, samesite="strict", path="/")


def clear_browser_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def login_allowed(request: Request) -> bool:
    key = request.client.host if request.client else "unknown"
    now = time.time()
    failures = [stamp for stamp in _login_failures.get(key, [])
                if stamp > now - _LOGIN_WINDOW_S]
    _login_failures[key] = failures
    return len(failures) < _MAX_LOGIN_FAILURES


def record_login_failure(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.time()
    _login_failures[key] = [stamp for stamp in _login_failures.get(key, [])
                            if stamp > now - _LOGIN_WINDOW_S] + [now]


def clear_login_failures(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    _login_failures.pop(key, None)


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def is_tailscale(request: Request) -> bool:
    """Whether a request arrived through the IPv4 Tailnet address space."""
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def presented_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("x-hub-token")
    if header:
        return header.strip()
    cookie = request.cookies.get(COOKIE_NAME)
    return cookie.strip() if cookie else None


def presented_machine_token(request: Request) -> str | None:
    """Header-only credential for private service-to-service contracts."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    header = request.headers.get("x-hub-token")
    return header.strip() if header else None


def valid_machine_token(request: Request, hub_token: str) -> bool:
    """Accept the Hub or fleet token; never a browser cookie or URL value."""
    offered = presented_machine_token(request)
    if not offered:
        return False
    if secrets.compare_digest(offered, hub_token):
        return True
    from . import peers
    fleet = peers.fleet_token()
    return bool(fleet and secrets.compare_digest(offered, fleet))


def presented_owner_token(request: Request, hub_token: str) -> bool:
    """Whether this request carries **this Mac's own** Hub token, not a fleet credential.

    Both credentials are valid on most routes, and the difference never mattered until a node had
    to answer *"may I run work for the person sitting at this Mac?"*. It does now:
    ``POST /api/hub/jobs`` refuses every submission in agent mode, because an agent must not own a
    customer's job — but the owner's app on his own Mac is not a customer, and refusing him meant a
    Mac switched to a node could not generate anything at all from its own window.

    Deliberately narrow: this answers "is this the owner's credential", never "is this allowed".
    The fleet scope stays exactly where it is.
    """
    offered = presented_machine_token(request)
    return bool(offered and secrets.compare_digest(offered, hub_token))


# What a fleet credential may do on a Mac it does not own.
#
# This is an allowlist, not a denylist: a new route added later is refused to a fleet caller
# until someone decides it belongs here, rather than being granted by default. The Hub token
# keeps unrestricted access and is never checked against this.
#
# In short: send work, watch it, collect it, cancel it - and read what is needed to decide
# where work should go. Never change this Mac's settings, never delete its output, never
# manage its models (beyond starting a checked download, see below) or its membership of the fleet.
#
# `chat` was missing from this regex until 2026-09-25, and the gap was a lie rather than a
# precaution: `GET /api/hub/catalog` is fleet-allowed and aggregates every worker's catalog,
# so a controller was shown the Chat model as available and then got 403 from every route that
# could describe or run it. The owner's decision is that every model that is running is shown
# *and runnable* - so chat's reads are granted here.
#
# Named here for the **GET** contract only. The POST and DELETE patterns below spell their
# modalities out instead of reusing this regex, so widening it grants no write: the one chat
# route that creates work is the single explicit POST entry further down.
#
# `video` was added with the lane it names (2026-09-26). Same reasoning as chat: once a Video
# worker exists, a controller shown the video catalog must be able to read its health, its
# memory policy and its activity, or the failure is a 403 against a model the fleet advertises.
_FLEET_STUDIO = r"(?:voice|subtitle|image|transcription|music|chat|video|embeddings|vision)"
FLEET_ALLOWED = (
    ("GET", r"^/api/health$"),
    ("GET", r"^/api/version$"),
    ("GET", r"^/api/hub/workers$"),
    ("GET", r"^/api/hub/resources$"),
    ("GET", r"^/api/hub/work-policy$"),
    ("POST", r"^/api/hub/work-policy$"),
    ("GET", r"^/api/hub/capabilities$"),
    ("GET", r"^/api/hub/catalog$"),
    ("GET", r"^/api/hub/models$"),
    ("GET", r"^/api/hub/stats$"),
    ("GET", r"^/api/hub/model-packages$"),
    # Model health is a read a controller needs to decide where work can go.
    ("GET", r"^/api/hub/model-health$"),
    ("GET", r"^/api/hub/memory/policy$"),
    # Remote scheduling reads only these worker contracts. Keep each shape
    # explicit so a newly added settings/storage/debug route is denied until
    # it is deliberately classified here.
    ("GET", rf"^/studio/{_FLEET_STUDIO}/api/"
            r"(?:health|catalog|fleet/activity|memory-policy|transcribe/availability|"
            r"generate/availability)$"),
    ("GET", rf"^/studio/{_FLEET_STUDIO}/api/generate/jobs/"
            r"[A-Za-z0-9_-]+(?:/(?:audio|image|video))?$"),
    ("GET", r"^/studio/(?:subtitle|transcription)/api/generate/transcribe/jobs"
            r"(?:/[A-Za-z0-9_-]+(?:/artifact)?)?$"),
    ("GET", r"^/studio/voice/api/voices(?:/[A-Za-z0-9_-]+(?:/audio)?)?$"),
    # Editing a saved voice changes metadata only - never the stored clip - and it is how an
    # operator fixes a transcript typo that would otherwise produce a wrong-sounding clone.
    ("PATCH", r"^/studio/voice/api/voices/[A-Za-z0-9_-]+$"),
    # Installing and removing a shared voice. This is fleet state rather than this Mac's own
    # library: the voice is namespaced by an id the fleet minted, a node's own voice can never be
    # overwritten (the receiver refuses an id that holds other audio, and refuses a local voice),
    # and installing it is what makes "add a voice once, use it on a hundred nodes" possible. The
    # gateway pushes to controllers only; the controller then installs it on the Macs it owns.
    ("PUT", r"^/studio/voice/api/voices/[0-9a-f]{12}/fleet-sync$"),
    ("DELETE", r"^/studio/voice/api/voices/[0-9a-f]{12}/fleet-sync$"),
    # Sending work to a node and calling it off is the whole point of a fleet credential, and
    # these are the only studio paths that do it. The worker's own settings, storage and model
    # routes are deliberately absent: they are reachable over the same proxy, so a blanket
    # allowance for the studio surface would have handed a controller exactly the control this
    # scope exists to withhold.
    # `video` joined the work routes with its lane (2026-09-26): the Hub advertises a location's
    # video models through `/api/hub/catalog`, so a controller that cannot POST the worker's own
    # generate route would be offered work it is refused. Both video doors are under
    # `/api/generate/`, which this pattern covers (`txt2video` and `video2video`).
    ("POST", r"^/studio/(voice|subtitle|image|transcription|music|video)/api/generate/[A-Za-z0-9_./-]*$"),
    # Chat's work route, and the only one. A controller dispatches to a node exactly as this Hub
    # dispatches to its own workers, so the peer surface's spelling of the completion is the one
    # the fleet uses. The Hub's own `POST /api/hub/chat/completions` is deliberately NOT added
    # here even though it runs the same operation: that route exists for this Mac's own Chat
    # page, and handing the fleet a second spelling of one operation is the second vocabulary
    # this project keeps paying for.
    ("POST", r"^/studio/chat/v1/chat/completions$"),
    ("GET", r"^/studio/chat/requests/[0-9a-f]{32}$"),
    # Embeddings' one work route (Story's meaning check). Short and synchronous, like Chat's.
    ("POST", r"^/studio/embeddings/v1/embeddings$"),
    # Vision's one work route (Story's scene analysis), short and synchronous like Embeddings'.
    ("POST", r"^/studio/vision/v1/vision/analyze$"),
    ("DELETE", r"^/studio/chat/requests/[0-9a-f]{32}$"),
    ("POST", r"^/studio/(voice|subtitle|image|transcription)/api/transcribe$"),
    ("DELETE", r"^/studio/(voice|subtitle|image|transcription|music|video)/api/generate/[A-Za-z0-9_./-]*$"),
    # Jobs: submit, watch, collect, acknowledge, reconcile, discard, cancel.
    ("POST", r"^/api/hub/jobs$"),
    ("GET", r"^/api/hub/jobs$"),
    ("GET", r"^/api/hub/jobs/[^/]+$"),
    ("DELETE", r"^/api/hub/jobs/[^/]+$"),
    ("GET", r"^/api/hub/jobs/[^/]+/items/[0-9]+/artifact$"),
    ("POST", r"^/api/hub/jobs/[^/]+/items/[0-9]+/ack$"),
    ("POST", r"^/api/hub/jobs/[^/]+/items/[0-9]+/reconcile$"),
    # Owner decision 2026-09-22: the controller may release a node's machine by discarding an
    # uncertain attempt it sent there. The node's own operator can still do it locally; without
    # this the controller could not free a node whose worker outcome is unknown.
    ("POST", r"^/api/hub/jobs/[^/]+/items/[0-9]+/discard$"),
    # A controller may put a failed item it sent here back in the node's queue.
    # The node still decides what can be retried: only an ``error`` item, never
    # a live or uncertain one.
    ("POST", r"^/api/hub/jobs/[^/]+/items/[0-9]+/retry$"),
    ("POST", r"^/api/hub/transcription/jobs$"),
    ("GET", r"^/api/hub/transcription/jobs$"),
    ("GET", r"^/api/hub/transcription/jobs/[^/]+$"),
    ("DELETE", r"^/api/hub/transcription/jobs/[^/]+$"),
    ("GET", r"^/api/hub/transcription/jobs/[^/]+/items/[0-9]+/artifact$"),
    ("POST", r"^/api/hub/transcription/jobs/[^/]+/items/[0-9]+/ack$"),
    ("POST", r"^/api/hub/transcription/jobs/[^/]+/items/[0-9]+/retry$"),
    # A clone reference travels with the job that uses it.
    ("POST", r"^/api/hub/voice-references$"),
    ("GET", r"^/api/hub/voice-references/[^/]+$"),
    # A fleet member may introduce itself to a controller and see its siblings. **It may also
    # remove itself, and only itself** — the path id has to be the caller's own, checked in the
    # route because an allowlist has no idea who is asking. That check is what makes "Leave this
    # group" work: it used to be a flat 403, so the app reported success while the controller
    # still listed the Mac (B2), and the operator had to walk to the controller and remove it
    # again. Removing *another* machine is still the controller owner's decision.
    ("POST", r"^/api/hub/fleet/nodes$"),
    ("GET", r"^/api/hub/fleet/nodes$"),
    ("GET", r"^/api/hub/fleet/overview$"),
    ("DELETE", r"^/api/hub/fleet/nodes/[^/]+$"),
    # Whether this controller still lists a Mac: how a removal reaches the Mac itself. Read-only;
    # a node credential may ask only about its own id (checked in the route).
    ("GET", r"^/api/hub/fleet/nodes/[^/]+/status$"),
    # Remote app update: a controller mirrors a signed release and its nodes pull it. Publishing
    # is fleet-reachable because GatewayKH holds only the Fleet token; the signature made with the
    # key the owner installed via POST /api/hub/release/trust is what authorises the release.
    # Installing that trusted key is the owner's decision, so /trust is deliberately NOT here and
    # stays Hub-token only, as does replacing what this Mac holds without a valid signature.
    ("POST", r"^/api/hub/release$"),
    ("GET", r"^/api/hub/release$"),
    ("GET", r"^/api/hub/release/artifact$"),
    # The controller's publisher public key and fingerprint, so a joining node can pin it.
    ("GET", r"^/api/hub/release/identity$"),
    # The delta update: the per-file manifest (index, then one gzipped TSV per group), the blobs
    # it names (Range-capable) and the ones still missing. A gateway pushes blobs with PUT; each is
    # verified against the sha256 it is sent under, so the credential grants no way to plant a file.
    ("GET", r"^/api/hub/release/files$"),
    ("GET", r"^/api/hub/release/files/[A-Za-z0-9_.+-]+$"),
    ("GET", r"^/api/hub/release/blob/[0-9a-f]{64}$"),
    ("GET", r"^/api/hub/release/need$"),
    # One Mac per group drains for an update at a time: a node asks its controller for the slot.
    ("POST", r"^/api/hub/fleet/update-slot$"),
    ("PUT", r"^/api/hub/release/blob/[0-9a-f]{64}$"),
    # **Startup control (owner request 2026-10-02: see and change, from the Gateway, whether a Mac
    # starts its services after a reboot).** A controller relays the Gateway's request to a node
    # (`.../fleet/nodes/{id}/startup`) and the node accepts it on `/api/hub/startup/request`. The
    # requests are narrow - two booleans and a restart - validated by the route, refused for a
    # restart while work is running, and written to an audit log. `/api/hub/startup/sync` is the
    # app's own exchange and is deliberately NOT here: it stays Hub-token only. Neither route is in
    # `NODE_ALLOWED`, so a node's own credential cannot command another Mac.
    ("GET", r"^/api/hub/startup$"),
    ("POST", r"^/api/hub/startup/request$"),
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/startup$"),
    # **Renaming a Mac from the Gateway (owner request 2026-10-02).** The controller takes the
    # Gateway's rename (`.../fleet/nodes/{id}/name`) and pushes it to the node's own
    # `/api/hub/machine-label`, which with a fleet credential accepts a 1-120 character name and
    # nothing else (no clearing, no other setting). A name is a label, never an identity: routing,
    # credentials and membership do not read it, so the most a Fleet-token holder can do with this is
    # relabel a Mac - the same group already trusted to send it work and restart its services.
    # Neither is in `NODE_ALLOWED`: a node's own credential cannot rename another Mac.
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/name$"),
    ("POST", r"^/api/hub/machine-label$"),
    # **Pausing a Mac from the Gateway (owner request 2026-10-03).** `{"paused": bool}` flips the
    # scheduler's own per-machine switch: no new work to that Mac, running jobs finish, it stays in
    # the roster and rejoins dispatch when resumed. Not in `NODE_ALLOWED`: a leaf Mac's credential
    # cannot pause another Mac.
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/pause$"),
    # **Workstation mode from the Gateway (owner, 2026-10-03, "Shops").** The controller relays the
    # Gateway's change (`.../fleet/nodes/{id}/workstation`) to the Mac's own `/api/hub/workstation`,
    # which accepts only the workstation settings, validated. Not in `NODE_ALLOWED`.
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/workstation$"),
    ("GET", r"^/api/hub/fleet/nodes/[^/]+/work-policy$"),
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/work-policy$"),
    # **Installing a model from the Gateway (owner decision 2026-10-04: the owner's Gateway may make a
    # Mac install a model).** Only the download and its status, each through the fleet path, which
    # refuses a shelved model, an unknown package, a full disk and a model this Mac has too little
    # memory to run. Importing, cancelling, verifying and the plain `/api/hub/model-packages/...`
    # routes stay Hub-token only, so the fleet still cannot remove or replace a model.
    # Not in `NODE_ALLOWED`.
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/model-packages/[^/]+/download$"),
    ("GET", r"^/api/hub/fleet/nodes/[^/]+/model-downloads/[^/]+$"),
    # **LAN model sharing (0.6.146).** A Mac that has a package installed and verified hands out a
    # short-lived, read-only token for that one revision, which another Mac on its network copies
    # from (`GET /api/hub/model-share/<token>/files/...`, authorised by the token itself - see the
    # middleware). Not in `NODE_ALLOWED`.
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/model-packages/[^/]+/share$"),
    # **A test transcription on one chosen Mac (0.6.147, model qualification from the Gateway).** The
    # same Subtitle work `POST /studio/subtitle/api/transcribe` already lets the fleet send, pinned to
    # one Mac and answered with its per-word timings. Not in `NODE_ALLOWED`.
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/transcribe$"),
    # **One picture's analysis on one chosen Mac (0.6.166).** The same Vision work
    # `POST /studio/vision/v1/vision/analyze` lets the fleet send, pinned to the Mac the Gateway
    # picked (the one that made the picture, owner rule 2026-10-11). Not in `NODE_ALLOWED`.
    ("POST", r"^/api/hub/fleet/nodes/[^/]+/vision/analyze$"),
    ("GET", r"^/api/hub/workstation$"),
    ("POST", r"^/api/hub/workstation$"),
    # **"Set this Mac's group membership."** A4's rotation and A3's handover are the same
    # statement — here is your new credential, and here is your new head — so they are one route.
    # A controller pushes it to a node with the Fleet token; the *operator* calls it with the Hub
    # token to rotate. A node's own credential is deliberately not in `NODE_ALLOWED`: a leaf Mac
    # must not re-point itself at a controller of its choosing.
    ("POST", r"^/api/hub/fleet$"),
)
_FLEET_ALLOWED = tuple((method, re.compile(pattern)) for method, pattern in FLEET_ALLOWED)


def fleet_path_allowed(method: str, path: str) -> bool:
    """Whether a fleet credential may use this route. Unknown routes are refused."""
    return any(method == allowed and pattern.match(path)
               for allowed, pattern in _FLEET_ALLOWED)


#: What a **node's own credential** may do on its controller, which is much less than the group
#: Fleet token. A node registers itself, reads the roster it belongs to, and may remove itself.
#: Everything else the Fleet token can do — submitting work, reading jobs, pulling artifacts,
#: publishing a release — belongs to the *controller* and the Gateway, and a leaf Mac has no
#: business doing any of it. Revoking one machine has to mean something (A8): narrowing this
#: list is what keeps a sold Mac from being a fleet-token holder by another name.
NODE_ALLOWED = (
    ("POST", r"^/api/hub/fleet/nodes$"),
    ("GET", r"^/api/hub/fleet/nodes$"),
    ("GET", r"^/api/hub/fleet/overview$"),
    ("DELETE", r"^/api/hub/fleet/nodes/[^/]+$"),
    ("GET", r"^/api/hub/fleet/nodes/[^/]+/status$"),
    # **Reading the release its controller holds** - the descriptor, the file manifest and the
    # blobs it names, or (as a last resort) the disk image. Read-only: publishing stays with the
    # gateway's Fleet token, and a node can only ever install what the gateway signed.
    ("GET", r"^/api/hub/release$"),
    ("GET", r"^/api/hub/release/artifact$"),
    ("GET", r"^/api/hub/release/files$"),
    ("GET", r"^/api/hub/release/files/[A-Za-z0-9_.+-]+$"),
    ("GET", r"^/api/hub/release/blob/[0-9a-f]{64}$"),
    ("POST", r"^/api/hub/fleet/update-slot$"),
)
_NODE_ALLOWED = tuple((method, re.compile(pattern)) for method, pattern in NODE_ALLOWED)


def node_path_allowed(method: str, path: str) -> bool:
    """Whether a *node credential* may use this route."""
    return any(method == allowed and pattern.match(path)
               for allowed, pattern in _NODE_ALLOWED)


def node_origin_allowed(method: str, path: str) -> bool:
    """Whether a request authenticated as a node may use this route.

    The union of the two lists: a node credential is a fleet credential that can do less, and
    every route a node credential may use is one the Fleet token may use too. Keeping the
    check as a union means the node's *own* credential can never be refused a route the shared
    token is allowed, which would break a node that upgraded to per-node credentials (A8).
    """
    return node_path_allowed(method, path) and fleet_path_allowed(method, path)


def _fleet_actor(request: Request) -> dict[str, Any]:
    """Who authenticated this request, in the one shape the routes check.

    `kind` is `"hub"`, `"fleet"` or `"node"`; `node_id` is set only for a node credential. The
    middleware writes it, and `self_removal_allowed` reads it — the allowlist cannot, because
    an allowlist does not know who is asking.
    """
    return getattr(request.state, "fleet_actor", None) or {"kind": "hub", "node_id": None}


def self_removal_allowed(request: Request, node_id: str, *, credential: str = "") -> bool:
    """Whether this caller may remove `node_id` from this controller's roster.

    Two callers may: **this Mac's own operator** (the Hub token — it owns the machine and the
    roster), and **the machine itself**. Nobody else. This is the whole of A2's first half:
    "Leave this group" sends exactly this request with the leaver's own id, so it must succeed,
    while one Mac must not be able to evict another.

    **Whose identity the path id is compared against is the whole subtlety.** On a node
    credential the middleware already resolved *which* machine is asking (`actor["node_id"]`),
    and that is the value to compare — the first version compared against
    `fleet_nodes.this_machine()`, which on the controller answers the **controller's** own
    controller_id. So a node leaving its controller was refused 403 for naming itself, and the
    test that covered it passed only because its fixture happened to give the controller and
    the node the same id. Found by a live two-Hub probe, not by that test.

    A **Fleet-token** caller is deliberately *not* allowed to remove anything: a shared
    credential identifies nobody, and there is no honest way to tell "this Mac is leaving" from
    "this Mac is evicting its neighbour". A node presents the credential it was issued at join
    (`A8`), so self-removal is always available to the machine that wants it.
    """
    actor = _fleet_actor(request)
    if actor.get("kind") == "hub":
        return True
    asked = str(node_id or "").strip().lower()
    if not asked:
        return False
    if actor.get("kind") == "node":
        return asked == str(actor.get("node_id") or "").strip().lower()
    return False


def _this_machine_name() -> str:
    """This Mac's own identity, for a refusal a person has to act on."""
    try:
        from .fleet_nodes import this_machine
        machine = this_machine()
        return f"{machine.get('name') or 'this Mac'} ({machine.get('id') or 'local'})"
    except Exception:  # noqa: BLE001 - a refusal must never fail while describing itself
        return "this Mac"


def credential_refusal_detail(request: Request, token: str) -> str:
    """Why this request was refused, and which two credentials have to agree.

    Deliberately names *which* credential was presented and *which* one the route wanted,
    without ever echoing a value. A bare 401 across a fleet is unattributable: measured
    2026-09-25, a controller read `/studio/voice/api/transcribe/availability` on a node and
    could not tell whether it had presented the node's Hub token, a Fleet token the node no
    longer holds, or nothing at all. The likely causes are the two Macs holding different Fleet
    tokens or this Mac having none - which is a re-join, and the message says so.
    """
    from . import peers

    offered = presented_token(request)
    # `current_fleet_token` and not `fleet_token`: describing a refusal must not mint a
    # credential as a side effect of an unauthenticated request.
    fleet = peers.current_fleet_token()
    if not offered:
        presented = "no credential was presented"
    elif secrets.compare_digest(offered, token):
        presented = "this Mac's Hub token, which is not what this route checks here"
    elif not fleet:
        presented = ("a credential that is not this Mac's Hub token, and this Mac holds no "
                     "Fleet token at all (it has never adopted one)")
    else:
        presented = ("a credential that is neither this Mac's Hub token nor the Fleet token "
                     "this Mac holds")
    route_allows_fleet = fleet_path_allowed(request.method, request.url.path)
    required = (
        "this Mac's Hub token, or a Fleet token matching the one this Mac holds"
        if route_allows_fleet else
        "this Mac's Hub token; this route is deliberately outside the Fleet credential's scope"
    )
    hint = (
        " If the caller presented a Fleet token, the two Macs hold different Fleet tokens: "
        "re-join this Mac from its controller with the current Fleet Token."
        if route_allows_fleet and fleet else
        " Copy this Mac's Hub token from its dashboard, or join it to a controller first."
    )
    return (f"{request.method} {request.url.path} was refused by {_this_machine_name()}: "
            f"{presented}. The route requires {required}.{hint}")


def make_middleware(token: str):
    from . import peers

    async def middleware(request: Request, call_next):
        if strict_fleet_service_path(request.url.path):
            try:
                require_exact_fleet_service_request(request)
            except ExactFleetServiceRequestError as exc:
                return JSONResponse(
                    {"detail": {"code": exc.code}},
                    status_code=exc.status_code,
                )
            return await call_next(request)
        # Local access stays passwordless, but an unrelated website opened in
        # the user's browser must not be able to mutate a loopback Hub. Native
        # clients do not send Origin; the Hub dashboard sends its own Host.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            origin_host = urlsplit(origin).netloc.lower()
            request_host = request.headers.get("host", "").lower()
            if not origin_host or origin_host != request_host:
                return JSONResponse(
                    {"detail": "Cross-origin browser writes are not allowed."},
                    status_code=403,
                )
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        # A LAN model share: the unguessable, expiring, single-package token in the path is the
        # credential, checked by the route (an unknown or expired one is a 404). Read-only.
        if request.method in {"GET", "HEAD"} and request.url.path.startswith("/api/hub/model-share/"):
            return await call_next(request)
        if (is_loopback(request)
                and os.environ.get("STUDIOHUB_REQUIRE_AUTH") != "1"):
            return await call_next(request)
        if valid_browser_session(request.cookies.get(SESSION_COOKIE_NAME)):
            return await call_next(request)
        offered = presented_token(request)
        if offered is not None:
            if secrets.compare_digest(offered, token):
                request.state.fleet_actor = {"kind": "hub", "node_id": None}
                response = await call_next(request)
                response.set_cookie(COOKIE_NAME, offered, httponly=True,
                                    samesite="strict")
                fleet = peers.fleet_token()
                if fleet:
                    response.set_cookie("kh_studio_token", fleet, httponly=True,
                                        samesite="strict")
                return response
            # **A node's own credential, checked before the shared token.** Every node is
            # issued one at join (A8) and presents it on the calls it makes home. The order
            # matters: if the shared token were tested first, a node whose credential had been
            # revoked could still get in on the group credential it happens to hold, and
            # "removed" would not mean anything.
            actor = _node_actor(offered)
            if actor is not None:
                if not node_origin_allowed(request.method, request.url.path):
                    return JSONResponse(
                        {"detail": f"{request.method} {request.url.path} was refused by "
                                   f"{_this_machine_name()}: a node's own credential lets it "
                                   f"register, read the group it joined and remove itself. "
                                   f"Sending work and reading another Mac's results needs the "
                                   f"controller's Fleet token."},
                        status_code=403,
                    )
                request.state.fleet_actor = actor
                response = await call_next(request)
                response.set_cookie(COOKIE_NAME, offered, httponly=True,
                                    samesite="strict")
                return response
            # Fleet token: lets a controller drive this Mac without holding the credential
            # that owns it. Deliberately narrower than the Hub token - it can send work and
            # read what it needs to decide where work goes, and nothing else.
            fleet = peers.fleet_token()
            if fleet and secrets.compare_digest(offered, fleet):
                if not fleet_path_allowed(request.method, request.url.path):
                    return JSONResponse(
                        {"detail": f"{request.method} {request.url.path} was refused by "
                                   f"{_this_machine_name()}: the fleet credential may send "
                                   f"work to this Mac and read its results, but it cannot "
                                   f"change this Mac's settings or delete its output."},
                        status_code=403,
                    )
                request.state.fleet_actor = {"kind": "fleet", "node_id": None}
                response = await call_next(request)
                response.set_cookie(COOKIE_NAME, offered, httponly=True,
                                    samesite="strict")
                response.set_cookie("kh_studio_token", fleet, httponly=True,
                                    samesite="strict")
                return response
            # **The previous Fleet token, for a node that missed the rotation.** Only while that node
            # is still waiting to be moved over, and only for what a node itself may do (register,
            # read its group, read the release): it is a leaked-token risk the controller accepts for
            # a bounded window so an offline Mac is not stranded, not a way to keep dispatching.
            if peers.accepts_previous_token(offered):
                if not node_origin_allowed(request.method, request.url.path):
                    return JSONResponse(
                        {"detail": f"{request.method} {request.url.path} was refused by "
                                   f"{_this_machine_name()}: the previous Fleet token is accepted "
                                   f"for a limited time so a Mac that missed the rotation can be "
                                   f"told the new one. Use the current Fleet token."},
                        status_code=403,
                    )
                request.state.fleet_actor = {"kind": "fleet", "node_id": None}
                return await call_next(request)
        # A node credential this controller revoked by removing that Mac: say so, with the date,
        # instead of a bare 401 the Mac cannot tell from a controller that lost its roster.
        if offered:
            from . import fleet_nodes
            removed_detail = fleet_nodes.revoked_detail(offered)
            if removed_detail:
                return JSONResponse({"detail": removed_detail}, status_code=403)
        return JSONResponse(
            {"detail": credential_refusal_detail(request, token)},
            status_code=401,
        )
    return middleware


def _node_actor(offered: str) -> dict[str, Any] | None:
    """Which joined node this credential belongs to, or `None` if it belongs to none.

    Scans the roster's hashes. There are at most `MAX_NODES` of them and the comparison is a
    fixed-cost digest, so this stays cheap on the paths that use it — and it is the only way
    to attribute a request to a machine when the credential is a bearer value rather than an
    id. No match means the credential is not a node credential at all, and the caller falls
    through to the shared Fleet token.
    """
    try:
        from . import fleet_nodes
    except Exception:  # noqa: BLE001 - an unimportable roster refuses, never permits
        return None
    digest = fleet_nodes.hash_credential(offered)
    for node_id, row in fleet_nodes._read().items():
        stored = str(row.get("credential_hash") or "")
        if stored and secrets.compare_digest(stored, digest):
            return {"kind": "node", "node_id": node_id}
    return None
