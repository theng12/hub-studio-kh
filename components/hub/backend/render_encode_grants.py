"""Reversible v2 owner grant for the tuples this node actually proved.

The owner's v1 opt-in (``opt-in/unqualified.json``) stays exactly where it is and stays the
authority: it is what the owner turns on and off, and it is the source the v2 grant comes
from. This module keeps the v2 grant - the extra tuples that were successfully probed on
this node - in its own record (``opt-in/probed-v2.json``), so the v1 file is never
rewritten and stays restorable byte for byte.

A v2 record binds the v1 it came from (canonical SHA-256 plus the original provenance), the
migration authorization and its time, the physical node it was probed on, and one exact
tuple with its evidence digest per entry. Reading it re-checks all of that: a v1 that is
off, malformed or changed, or a record made for another node, yields no grant at all.

This helper stores and reads; it proves nothing. The caller hands over freshly validated
evidence from this node: an entry without a 64-hex evidence digest is refused, so a
caller-requested tuple can never become a grant on its own.

The v1 schema mirrored below matches the loader's own opt-in record; ``tests/
test_render_encode_grants.py`` pins the two together so they cannot drift apart.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import render_authority_files, render_encode
from .render_hash_v1 import decode_render_json

FORMAT = "studio.render.accept-unqualified-probed-v2.v1"
DIRECTORY = "opt-in"
V1_FILENAME = "unqualified.json"
V2_FILENAME = "probed-v2.json"

# The v1 opt-in record this grant comes from (render_executor's own settings record).
V1_FORMAT = "studio.render.accept-unqualified-setting.v1"
V1_KEYS = {"format", "enabled", "encode", "set_by", "set_at"}
V1_PROVENANCE = "owner-opt-in-unqualified"

V2_KEYS = {"format", "v1", "migration", "node", "entries"}
V1_BLOCK_KEYS = {"sha256", "provenance", "accepted_by", "accepted_at"}
MIGRATION_KEYS = {"authorization_id", "authorized_at"}
NODE_KEYS = {"physical_node_id"}
ENTRY_KEYS = {"requested_encode", "authority_digest"}

# Bounded so the record always fits the strict reader's private record size.
MAX_ENTRIES = 32


def _canonical(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(document: Any) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _is_digest(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_person(value: Any) -> bool:
    return (type(value) is str and 0 < len(value) <= 120
            and value.isascii() and value.isprintable())


def _is_id(value: Any) -> bool:
    return (type(value) is str and 0 < len(value) <= 256
            and value.isascii() and value.isprintable())


def _read_bytes(data_root: Path, filename: str, expected_uid: int) -> bytes | None:
    try:
        return render_authority_files.read_record(
            data_root, DIRECTORY, filename, expected_uid=expected_uid)
    except (OSError, ValueError):
        return None


def _read_v1(data_root: Path, expected_uid: int) -> dict[str, Any] | None:
    """The owner's v1 opt-in record, or None when it is absent, unsafe or malformed."""
    raw = _read_bytes(data_root, V1_FILENAME, expected_uid)
    if raw is None:
        return None
    try:
        document = decode_render_json(raw)
    except ValueError:
        return None
    if type(document) is not dict or set(document) != V1_KEYS:
        return None
    if (document["format"] != V1_FORMAT
            or type(document["enabled"]) is not bool
            or render_encode.normalize_encode(document["encode"]) is None
            or document["encode"]["bitrate_kbps"] is not None
            or not _is_person(document["set_by"])
            or type(document["set_at"]) is not int or document["set_at"] < 1):
        return None
    return document


def _valid_entries(entries: Any) -> bool:
    """Every entry is one exact tuple with its evidence digest, and no tuple twice."""
    if type(entries) is not list or not 1 <= len(entries) <= MAX_ENTRIES:
        return False
    seen: set[bytes] = set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != ENTRY_KEYS:
            return False
        encode = render_encode.normalize_encode(entry["requested_encode"])
        if encode is None or not _is_digest(entry["authority_digest"]):
            return False
        identity = _canonical(encode)
        if identity in seen:
            return False
        seen.add(identity)
    return True


def _valid_v2(document: Any, v1: dict[str, Any], physical_node_id: str) -> bool:
    """The v2 record is closed, and still answers for this v1 on this node."""
    if type(document) is not dict or set(document) != V2_KEYS:
        return False
    if document["format"] != FORMAT:
        return False
    source = document["v1"]
    if type(source) is not dict or set(source) != V1_BLOCK_KEYS:
        return False
    if (source["sha256"] != _digest(v1)
            or source["provenance"] != V1_PROVENANCE
            or source["accepted_by"] != v1["set_by"]
            or source["accepted_at"] != v1["set_at"]):
        return False
    migration = document["migration"]
    if type(migration) is not dict or set(migration) != MIGRATION_KEYS:
        return False
    if (not _is_person(migration["authorization_id"])
            or type(migration["authorized_at"]) is not int
            or migration["authorized_at"] < 1):
        return False
    node = document["node"]
    if type(node) is not dict or set(node) != NODE_KEYS:
        return False
    if not _is_id(node["physical_node_id"]) or node["physical_node_id"] != physical_node_id:
        return False
    return _valid_entries(document["entries"])


def read_v2_grants(data_root: str | Path, *, expected_uid: int,
                   physical_node_id: str) -> dict[str, Any] | None:
    """The v2 grant that applies on this node right now, or None when there is none.

    A v1 that is absent, malformed, off or changed since the migration, and a record made
    for another node, all yield None: the v2 grant never outlives the v1 it came from.
    Reading never writes anything.
    """
    root = Path(data_root)
    v1 = _read_v1(root, expected_uid)
    if v1 is None or v1["enabled"] is not True:
        return None
    raw = _read_bytes(root, V2_FILENAME, expected_uid)
    if raw is None:
        return None
    try:
        document = decode_render_json(raw)
    except ValueError:
        return None
    return document if _valid_v2(document, v1, physical_node_id) else None


def write_v2_grants(data_root: str | Path, *, expected_uid: int,
                    physical_node_id: str, authorization_id: str,
                    authorized_at: int, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the v2 grant for this node and return the record that was written.

    ``entries`` are the caller's freshly validated evidence: one closed
    ``{requested_encode, authority_digest}`` per tuple that was successfully probed on
    this node. The helper checks their shape and stores them; it does not prove them, and
    it never writes a grant from a requested tuple on its own. The tuple must be one the
    encode module advertises; whether the pinned compiler still builds it is the
    executor's question, not this record's.

    Raises ``ValueError`` with a sentence for the caller when it refuses. The v1 record is
    never touched, so disabling it withdraws this grant again.
    """
    root = Path(data_root)
    if not _is_id(physical_node_id):
        raise ValueError("the physical node id is missing or not a short text")
    if not _is_person(authorization_id):
        raise ValueError("the migration authorization must name who authorized it")
    if type(authorized_at) is not int or authorized_at < 1:
        raise ValueError("the migration authorization needs the time it was given")
    v1 = _read_v1(root, expected_uid)
    if v1 is None:
        raise ValueError("the owner's original grant is missing or malformed")
    if v1["enabled"] is not True:
        raise ValueError("the owner's original grant is off, so there is nothing to migrate")
    if not _valid_entries(entries):
        raise ValueError(
            "each grant entry needs one exact encode tuple with its evidence digest, "
            "and no tuple twice")
    document = {
        "format": FORMAT,
        "v1": {
            "sha256": _digest(v1),
            "provenance": V1_PROVENANCE,
            "accepted_by": v1["set_by"],
            "accepted_at": v1["set_at"],
        },
        "migration": {
            "authorization_id": authorization_id,
            "authorized_at": authorized_at,
        },
        "node": {"physical_node_id": physical_node_id},
        "entries": [
            {
                "requested_encode": render_encode.normalize_encode(
                    entry["requested_encode"]),
                "authority_digest": entry["authority_digest"],
            }
            for entry in entries
        ],
    }
    raw = _canonical(document)
    if len(raw) > render_authority_files.MAX_RECORD_BYTES:
        raise ValueError("the grant is too large to keep as one private record")
    try:
        render_authority_files.write_record_replace(
            root, DIRECTORY, V2_FILENAME, raw, expected_uid=expected_uid)
    except (OSError, ValueError) as exc:
        raise ValueError("the grant could not be saved on this Mac") from exc
    return document
