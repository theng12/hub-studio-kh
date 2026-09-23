"""Controller-side store for a remote app-update release.

This is the Studio Hub (controller) half of the design in
``.planning/2026-09-10-standalone-app-feasibility/remote-update-design.md``,
"Wire contract (chosen, to build against)".  GatewayKH pushes a signed
descriptor plus the artifact; the controller verifies the gateway signature and
the artifact hash, stores both, and countersigns the descriptor with its own
key.  A node later verifies that countersignature against the controller key it
pinned at join.

What the other side (GatewayKH / the node) must match byte for byte
-------------------------------------------------------------------
Canonical descriptor bytes:
    ``json.dumps(descriptor, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False).encode("utf-8")``
    - sorted keys, no insignificant whitespace, UTF-8.  Every field in the
      descriptor today is ASCII, so this also matches a plain
      ``JSON.stringify`` with sorted keys.

Gateway signature (the descriptor's ``signature`` field):
    base64 of the 64 raw Ed25519 bytes over the canonical descriptor with the
    ``signature`` field REMOVED.  Standard base64 with ``=`` padding.

Controller countersignature (the ``controller_signature`` field):
    base64 of the 64 raw Ed25519 bytes over the canonical stored descriptor
    with only the ``controller_signature`` field REMOVED.  That means it signs
    the received descriptor *including* the gateway ``signature`` and the added
    ``controller_key_id``.  A node must pop ``controller_signature``, canonicalise
    the rest, and verify with the controller key it pinned.

Key encodings:
    - raw public keys are 32 bytes; the trust route accepts 64 hex characters
      (either case) or standard base64, and stores lowercase hex.
    - the controller's private key is a raw 32-byte Ed25519 seed, stored as hex
      in ``<data>/.release_controller_key`` with mode 0600; it is never returned.
    - a ``*_key_id`` / fingerprint is lowercase hex sha256 of the raw public key
      bytes (32 bytes -> 64 hex).

Ed25519 itself is implemented in ``backend/ed25519.py`` because the bundled
runtime ships no cryptography library.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from . import ed25519
from .registry import DATA_DIR

# Release data lives beside the other per-machine state, not inside the app
# bundle.  Keys follow the token layout: top-level dotfiles, mode 0600.
RELEASE_DIR = DATA_DIR / "release"
ARTIFACT_FILE = RELEASE_DIR / "artifact"
RELEASE_FILE = RELEASE_DIR / "release.json"
CONTROLLER_KEY_FILE = DATA_DIR / ".release_controller_key"
TRUST_FILE = DATA_DIR / ".release_publisher_trust.json"

SCHEMA = "studiohub.app-release.v1"
SCHEMA_VERSION = 1
CONTROLLER_SIGNATURE_FIELD = "controller_signature"
CONTROLLER_KEY_ID_FIELD = "controller_key_id"
# The descriptor schema is fixed; a generous ceiling refuses an absurd value
# without second-guessing a real (multi-GB) bundle.
MAX_ARTIFACT_BYTES = 16 * 1024**3
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_LOCK = threading.RLock()
_identity_cache: dict[str, str] | None = None


class ReleaseRefused(ValueError):
    """A stable refusal the routes turn into a 4xx with a clear message."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


# ── canonical form and signatures ─────────────────────────────────────────


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def signing_bytes(descriptor: Mapping[str, Any], *, field: str = "signature") -> bytes:
    """Canonical bytes of a descriptor with one signature field removed."""
    body = {key: value for key, value in descriptor.items() if key != field}
    return _canonical(body)


def _decode_signature(signature: str | bytes) -> bytes | None:
    if isinstance(signature, bytes):
        return signature if len(signature) == ed25519.SIGNATURE_SIZE else None
    if not isinstance(signature, str):
        return None
    compact = "".join(signature.split())
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == ed25519.SIGNATURE_SIZE else None


def sign(descriptor: Mapping[str, Any], private_key: bytes, *,
         field: str = "signature") -> str:
    """Sign a descriptor; returns standard base64 of the 64 raw bytes.

    ``field`` is the signature field removed before canonicalising, so the
    default signs the gateway contract and ``field="controller_signature"``
    signs the controller countersignature.
    """
    raw = ed25519.sign(private_key, signing_bytes(descriptor, field=field))
    return base64.b64encode(raw).decode("ascii")


def verify(descriptor: Mapping[str, Any], signature: str | bytes, public_key: bytes,
           *, field: str = "signature") -> bool:
    """Verify a descriptor signature.  False on any malformed input."""
    raw = _decode_signature(signature)
    if raw is None or len(public_key) != ed25519.PUBLIC_KEY_SIZE:
        return False
    return ed25519.verify(public_key, signing_bytes(descriptor, field=field), raw)


def countersign(descriptor: Mapping[str, Any], private_key: bytes) -> str:
    return sign(descriptor, private_key, field=CONTROLLER_SIGNATURE_FIELD)


def verify_countersignature(descriptor: Mapping[str, Any], signature: str | bytes,
                            public_key: bytes) -> bool:
    return verify(descriptor, signature, public_key,
                  field=CONTROLLER_SIGNATURE_FIELD)


def fingerprint(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()


# ── controller key pair ────────────────────────────────────────────────────


def _read_controller_seed() -> bytes | None:
    try:
        raw = bytes.fromhex(CONTROLLER_KEY_FILE.read_text(encoding="ascii").strip())
    except (OSError, ValueError, UnicodeError):
        return None
    if len(raw) != ed25519.SEED_SIZE:
        return None
    os.chmod(CONTROLLER_KEY_FILE, 0o600)
    return raw


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def controller_private_key() -> bytes:
    """The controller's Ed25519 seed, generated on first use and never returned
    by a route.  Stored 0600 under the Hub data dir like the Hub/fleet tokens."""
    with _LOCK:
        existing = _read_controller_seed()
        if existing is not None:
            return existing
        seed = secrets.token_bytes(ed25519.SEED_SIZE)
        _write_private(CONTROLLER_KEY_FILE, seed.hex() + "\n")
        return seed


def controller_identity() -> dict[str, str]:
    """The public half a joining node pins: raw public key (hex) + fingerprint."""
    global _identity_cache
    with _LOCK:
        if _identity_cache is None:
            public = ed25519.public_key(controller_private_key())
            _identity_cache = {
                "algorithm": "ed25519",
                "public_key": public.hex(),
                "fingerprint": fingerprint(public),
            }
        return dict(_identity_cache)


# ── trusted gateway publisher key ──────────────────────────────────────────


def _parse_public_key(value: Any) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseRefused(400, "public_key must be a non-empty hex or base64 string")
    text = value.strip()
    if _HEX64.fullmatch(text):
        return bytes.fromhex(text)
    compact = "".join(text.split())
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        raise ReleaseRefused(
            400, "public_key is neither 64 hex characters nor standard base64")
    if len(raw) != ed25519.PUBLIC_KEY_SIZE:
        raise ReleaseRefused(
            400,
            "an Ed25519 public key is 32 bytes (64 hex characters); "
            f"got {len(raw)} bytes",
        )
    return raw


def trusted_publisher() -> dict[str, Any] | None:
    """The gateway key the owner installed, or None.  Absence is null."""
    try:
        value = json.loads(TRUST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    public_hex = value.get("public_key")
    stored = value.get("fingerprint")
    if not isinstance(public_hex, str) or not _HEX64.fullmatch(public_hex):
        return None
    try:
        raw = bytes.fromhex(public_hex)
    except ValueError:
        return None
    if len(raw) != ed25519.PUBLIC_KEY_SIZE:
        return None
    # Trust the key, not a stale fingerprint: recompute and require agreement.
    recomputed = fingerprint(raw)
    if not isinstance(stored, str) or stored.lower() != recomputed:
        return None
    return {**value, "public_key": public_hex.lower(), "fingerprint": recomputed}


def set_trusted_publisher(body: Any) -> dict[str, Any]:
    """Set or replace the trusted gateway key.  Owner action (Hub token only)."""
    if not isinstance(body, dict):
        raise ReleaseRefused(400, "body must be a JSON object")
    publisher_id = body.get("publisher_id")
    if not isinstance(publisher_id, str) or not publisher_id.strip():
        raise ReleaseRefused(400, "publisher_id must be a non-empty string")
    publisher_id = publisher_id.strip()
    if len(publisher_id) > 128:
        raise ReleaseRefused(400, "publisher_id is longer than 128 characters")
    raw = _parse_public_key(body.get("public_key"))
    key_id = fingerprint(raw)
    record = {
        "publisher_id": publisher_id,
        "public_key": raw.hex(),
        "fingerprint": key_id,
        "set_at": int(time.time()),
    }
    _write_private(TRUST_FILE, json.dumps(record, indent=2) + "\n")
    return {"ok": True, "publisher_id": publisher_id,
            "publisher_key_id": key_id, "fingerprint": key_id}


# ── descriptor validation ──────────────────────────────────────────────────


def _require(condition: bool, status_code: int, message: str) -> None:
    if not condition:
        raise ReleaseRefused(status_code, message)


def _required_string(descriptor: Mapping[str, Any], name: str, maximum: int) -> str:
    value = descriptor.get(name)
    _require(isinstance(value, str) and bool(value),
             400, f"descriptor.{name} must be a non-empty string")
    _require(len(value) <= maximum, 400,
             f"descriptor.{name} is longer than {maximum} characters")
    return value


def validate_descriptor(descriptor: Any) -> None:
    """The schema in the design doc, refused at the first bad field."""
    _require(isinstance(descriptor, dict), 400, "descriptor must be a JSON object")
    _require(descriptor.get("schema") == SCHEMA, 400,
             f"descriptor.schema must be {SCHEMA!r}")
    version = descriptor.get("schema_version")
    _require(isinstance(version, int) and not isinstance(version, bool)
             and version == SCHEMA_VERSION, 400,
             f"descriptor.schema_version must be {SCHEMA_VERSION}")
    _required_string(descriptor, "version", 64)
    _required_string(descriptor, "build", 128)
    filename = _required_string(descriptor, "filename", 255)
    _require("/" not in filename and "\\" not in filename and "\x00" not in filename
             and filename not in (".", ".."), 400,
             "descriptor.filename must be a bare file name, not a path")
    size = descriptor.get("bytes")
    _require(isinstance(size, int) and not isinstance(size, bool) and size > 0, 400,
             "descriptor.bytes must be a positive integer")
    _require(size <= MAX_ARTIFACT_BYTES, 400,
             f"descriptor.bytes exceeds the {MAX_ARTIFACT_BYTES}-byte ceiling")
    sha = descriptor.get("sha256")
    _require(isinstance(sha, str) and _HEX64.fullmatch(sha) is not None, 400,
             "descriptor.sha256 must be 64 hex characters over the artifact bytes")
    published = descriptor.get("published_at")
    _require(isinstance(published, int) and not isinstance(published, bool)
             and published >= 0, 400,
             "descriptor.published_at must be a unix timestamp (non-negative integer)")
    _required_string(descriptor, "publisher_id", 128)
    key_id = descriptor.get("publisher_key_id")
    _require(isinstance(key_id, str) and _HEX64.fullmatch(key_id) is not None, 400,
             "descriptor.publisher_key_id must be the 64-hex key fingerprint")
    _require(_decode_signature(descriptor.get("signature")) is not None, 400,
             "descriptor.signature must be base64 of a 64-byte Ed25519 signature")


# ── accept / store ─────────────────────────────────────────────────────────


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def accept_release(body: Any) -> dict[str, Any]:
    """Verify then store a pushed release; the first failure refuses it.

    Order is the contract's: (a) schema, (b) gateway signature against the
    trusted key (a key that was never set is an error, not a pass), (c) the
    artifact's sha256 and byte length, (d) only then store and countersign.
    Nothing is written until (a)-(c) all pass.
    """
    _require(isinstance(body, dict), 400, "body must be a JSON object")
    artifact_b64 = body.get("artifact_base64")
    _require(isinstance(artifact_b64, str) and bool(artifact_b64.strip()), 400,
             "artifact_base64 must be a non-empty base64 string")

    descriptor = body.get("descriptor")
    # (a) the descriptor matches the schema in the design doc.
    validate_descriptor(descriptor)

    # (b) the gateway signature verifies against the owner-installed key.
    trusted = trusted_publisher()
    _require(trusted is not None, 409,
             "no trusted gateway publisher key is set; install one with "
             "POST /api/hub/release/trust before pushing a release")
    _require(descriptor["publisher_key_id"].lower() == trusted["fingerprint"], 400,
             f"descriptor.publisher_key_id {descriptor['publisher_key_id']} does not "
             f"match the trusted gateway key {trusted['fingerprint']}")
    _require(verify(descriptor, descriptor["signature"],
                    bytes.fromhex(trusted["public_key"])), 400,
             "the gateway signature does not verify against the trusted gateway key")

    # (c) the artifact matches the signed sha256 and byte length.
    compact = "".join(artifact_b64.split())
    try:
        artifact = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        raise ReleaseRefused(400, "artifact_base64 is not valid base64")
    _require(len(artifact) == descriptor["bytes"], 400,
             f"artifact is {len(artifact)} bytes but the descriptor says "
             f"{descriptor['bytes']}")
    digest = hashlib.sha256(artifact).hexdigest()
    _require(digest == descriptor["sha256"].lower(), 400,
             f"artifact sha256 {digest} does not match descriptor.sha256 "
             f"{descriptor['sha256']}")

    # (d) store exactly what was received, plus the controller countersignature.
    merged = dict(descriptor)
    merged[CONTROLLER_KEY_ID_FIELD] = controller_identity()["fingerprint"]
    merged[CONTROLLER_SIGNATURE_FIELD] = countersign(merged, controller_private_key())
    with _LOCK:
        _atomic_write_bytes(ARTIFACT_FILE, artifact)
        _atomic_write_bytes(
            RELEASE_FILE,
            (json.dumps(merged, indent=2) + "\n").encode("utf-8"),
        )
    return merged


def load_release() -> dict[str, Any] | None:
    """The held descriptor, or None when nothing (or no artifact) is held."""
    if not RELEASE_FILE.is_file() or not ARTIFACT_FILE.is_file():
        return None
    try:
        value = json.loads(RELEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


# ── test isolation ─────────────────────────────────────────────────────────


def reset_for_tests() -> None:
    global _identity_cache
    with _LOCK:
        _identity_cache = None
        RELEASE_FILE.unlink(missing_ok=True)
        ARTIFACT_FILE.unlink(missing_ok=True)
        CONTROLLER_KEY_FILE.unlink(missing_ok=True)
        TRUST_FILE.unlink(missing_ok=True)
        try:
            RELEASE_DIR.rmdir()
        except OSError:
            pass
