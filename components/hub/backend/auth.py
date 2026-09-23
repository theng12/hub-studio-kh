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


# What a fleet credential may do on a Mac it does not own.
#
# This is an allowlist, not a denylist: a new route added later is refused to a fleet caller
# until someone decides it belongs here, rather than being granted by default. The Hub token
# keeps unrestricted access and is never checked against this.
#
# In short: send work, watch it, collect it, cancel it - and read what is needed to decide
# where work should go. Never change this Mac's settings, never delete its output, never
# manage its models or its membership of the fleet.
_FLEET_STUDIO = r"(?:voice|subtitle|image|transcription|music)"
FLEET_ALLOWED = (
    ("GET", r"^/api/health$"),
    ("GET", r"^/api/version$"),
    ("GET", r"^/api/hub/workers$"),
    ("GET", r"^/api/hub/resources$"),
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
    ("POST", r"^/studio/(voice|subtitle|image|transcription|music)/api/generate/[A-Za-z0-9_./-]*$"),
    ("POST", r"^/studio/(voice|subtitle|image|transcription)/api/transcribe$"),
    ("DELETE", r"^/studio/(voice|subtitle|image|transcription|music)/api/generate/[A-Za-z0-9_./-]*$"),
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
    # A fleet member may introduce itself to a controller and see its siblings, but removing
    # a node is the controller owner's decision.
    ("POST", r"^/api/hub/fleet/nodes$"),
    ("GET", r"^/api/hub/fleet/nodes$"),
    ("GET", r"^/api/hub/fleet/overview$"),
)
_FLEET_ALLOWED = tuple((method, re.compile(pattern)) for method, pattern in FLEET_ALLOWED)


def fleet_path_allowed(method: str, path: str) -> bool:
    """Whether a fleet credential may use this route. Unknown routes are refused."""
    return any(method == allowed and pattern.match(path)
               for allowed, pattern in _FLEET_ALLOWED)


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
        if (is_loopback(request)
                and os.environ.get("STUDIOHUB_REQUIRE_AUTH") != "1"):
            return await call_next(request)
        if valid_browser_session(request.cookies.get(SESSION_COOKIE_NAME)):
            return await call_next(request)
        offered = presented_token(request)
        if offered is not None:
            if secrets.compare_digest(offered, token):
                response = await call_next(request)
                response.set_cookie(COOKIE_NAME, offered, httponly=True,
                                    samesite="strict")
                fleet = peers.fleet_token()
                if fleet:
                    response.set_cookie("kh_studio_token", fleet, httponly=True,
                                        samesite="strict")
                return response
            # Fleet token: lets a controller drive this Mac without holding the credential
            # that owns it. Deliberately narrower than the Hub token - it can send work and
            # read what it needs to decide where work goes, and nothing else.
            fleet = peers.fleet_token()
            if fleet and secrets.compare_digest(offered, fleet):
                if not fleet_path_allowed(request.method, request.url.path):
                    return JSONResponse(
                        {"detail": "The fleet credential may send work to this Mac and read "
                                   "its results, but it cannot change this Mac's settings or "
                                   "delete its output."},
                        status_code=403,
                    )
                response = await call_next(request)
                response.set_cookie(COOKIE_NAME, offered, httponly=True,
                                    samesite="strict")
                response.set_cookie("kh_studio_token", fleet, httponly=True,
                                    samesite="strict")
                return response
        return JSONResponse(
            {"detail": "Hub token required for remote access. "
                       "Open the dashboard on the Hub machine to see the token."},
            status_code=401,
        )
    return middleware
