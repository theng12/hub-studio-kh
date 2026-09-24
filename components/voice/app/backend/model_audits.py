"""Read verified sibling model-audit evidence for the public catalog.

Audit files are deliberately separate from the runtime catalog.  A cached
model can exist without an audit, and a passed audit is only a *candidate* for
GenStudio.  Studio Hub remains the authority that decides whether an exact
revision and contract hash is exposed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

AUDIT_ROOT = Path(__file__).resolve().parents[2] / "model-audits"
_IMMUTABLE_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")
_CONTRACT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_AUDIT_STATUSES = {"passed", "conditional", "failed", "revoked"}
# ── the Qwen 1.7B production section policy: an audit pin, not a model pin ──
#
# THIS IS A CONTRACT, NOT A LIST THAT WANTS DE-CRUFTING. Everything else in this
# module reads the newest valid `*/*.audit.json` for whatever model it is asked
# about (`audit_record`, and therefore `input_limits`). This one policy is
# different on purpose: the 400-character maximum and the 280-character Auto
# section size are numbers that were *measured once*, on one canary run, and the
# whole point is that a later, newer record cannot change how every machine
# splits a script just by existing. So the pin names the audit whose evidence
# authorises them, and `qwen_17b_production_v2_limits` applies it only to a model
# whose own record either IS that audit or explicitly derives from it.
#
# The anchor is the audit id, never a repo id. The previous version compared
# `model_id` against `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit`; when that
# tier was pruned from the catalog the policy stayed pinned to a model nobody
# ships, and the shipped 4-bit tier silently lost its audited Auto section size.
# `tests/test_voice_bundle.py` asserts the anchor resolves for a shipped model.
_PRODUCTION_SECTION_ANCHOR_AUDIT_ID = (
    "voicestudio-20260826-qwen3-tts-1.7b-base-production-v3"
)
_PRODUCTION_SECTION_MAX_CHARACTERS = 400
_PRODUCTION_SECTION_AUTO_DEFAULT_CHARACTERS = 280
_PRODUCTION_SECTION_AUTO_DEFAULT_FLOOR = 230
_PRODUCTION_SECTION_RECORD_SCHEMA = "studio.model-audit-record"
_DERIVATION_WALK_LIMIT = 4


def _production_section_limits(record: dict[str, Any]) -> dict[str, Any]:
    """The 400/280 pair one record claims, or ``{}`` when it does not claim it.

    Only the numbers and the record's own envelope are checked here; the
    provenance (may this record claim them at all?) is `_inherits_anchor`.
    """
    limits = (record.get("contract") or {}).get("input_limits")
    if not isinstance(limits, dict):
        return {}
    maximum = limits.get("private_section_max_characters")
    auto_default = limits.get("default_private_section_max_characters")
    if (
        type(maximum) is not int
        or maximum != _PRODUCTION_SECTION_MAX_CHARACTERS
        or type(auto_default) is not int
        or auto_default != _PRODUCTION_SECTION_AUTO_DEFAULT_CHARACTERS
        or not _PRODUCTION_SECTION_AUTO_DEFAULT_FLOOR <= auto_default <= maximum
    ):
        return {}
    return {
        "private_section_max_characters": maximum,
        "default_private_section_max_characters": auto_default,
    }


def _envelope_ok(record: dict[str, Any]) -> bool:
    """The top-level audit-record envelope, as distinct from the candidate block.

    ``_valid_record`` already checked the candidate mirror; this checks that the
    document is the record schema and that a passed candidate is what is making
    the claim, so a copied contract without its promotion envelope cannot
    authorise the pin.
    """
    candidate = record.get("genstudio_candidate") or {}
    return (
        record.get("schema") == _PRODUCTION_SECTION_RECORD_SCHEMA
        and record.get("schema_version") == 1
        and candidate.get("audit_status") == "passed"
        and candidate.get("candidate_for_genstudio") is True
        and candidate.get("audit_id") == record.get("audit_id")
    )


def _records_by_audit_id() -> dict[str, dict[str, Any]]:
    """Every valid record, keyed by its own audit id."""
    if not AUDIT_ROOT.exists():
        return {}
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(AUDIT_ROOT.glob("*/*.audit.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if _valid_record(raw) and isinstance(raw.get("audit_id"), str):
            found.setdefault(raw["audit_id"], raw)
    return found


def _inherits_anchor(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    """Whether this record's claim traces back to the measured production audit.

    A record may make the claim when it IS the anchor, or when it declares a
    ``derivation`` from the anchor's audit id, names the same upstream model and
    copies the numbers exactly. Anything else - a neighbouring model's record, a
    record that derives from a record that derives from nowhere - is refused.
    """
    audit_id = str(record.get("audit_id") or "")
    if audit_id == _PRODUCTION_SECTION_ANCHOR_AUDIT_ID:
        return True
    seen: set[str] = set()
    current = record
    for _ in range(_DERIVATION_WALK_LIMIT):
        derivation = current.get("derivation")
        if not isinstance(derivation, dict):
            return False
        parent_id = str(derivation.get("derived_from_audit_id") or "")
        if not parent_id or parent_id in seen:
            return False
        seen.add(parent_id)
        if parent_id == _PRODUCTION_SECTION_ANCHOR_AUDIT_ID:
            subject = record.get("subject") or {}
            return (
                bool(derivation.get("derived_from_model_id"))
                and derivation.get("derived_from_model_id")
                == subject.get("upstream_model_id")
                and subject.get("model_id") != (by_id.get(parent_id, {}).get("subject") or {}).get("model_id")
            )
        parent = by_id.get(parent_id)
        if parent is None:
            return False
        current = parent
    return False


def contract_hash(contract: dict[str, Any]) -> str:
    """Canonical SHA-256 used to bind approval to one exact model contract."""
    encoded = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _valid_record(raw: Any) -> bool:
    if not isinstance(raw, dict) or not isinstance(raw.get("contract"), dict):
        return False
    subject = raw.get("subject")
    candidate = raw.get("genstudio_candidate")
    if not isinstance(subject, dict) or not isinstance(candidate, dict):
        return False
    if not str(subject.get("model_id") or "").strip():
        return False
    if candidate.get("schema") != "studio.model-audit":
        return False
    if (
        type(candidate.get("schema_version")) is not int
        or candidate["schema_version"] != 1
    ):
        return False
    if candidate.get("audit_status") not in _AUDIT_STATUSES:
        return False
    if not isinstance(candidate.get("candidate_for_genstudio"), bool):
        return False
    revision = str(candidate.get("runtime_revision") or "")
    if not _IMMUTABLE_REVISION.fullmatch(revision):
        return False
    fingerprint = str(candidate.get("contract_hash") or "")
    if not _CONTRACT_HASH.fullmatch(fingerprint):
        return False
    if fingerprint != contract_hash(raw["contract"]):
        return False
    for key in (
        "runtime_revision",
        "approved_operations",
        "adapter",
        "controls",
        "input_limits",
        "output_limits",
        "hardware",
    ):
        if candidate.get(key) != raw["contract"].get(key):
            return False
    if "capacity" in candidate and candidate.get("capacity") != raw["contract"].get("capacity"):
        return False
    return True


def audit_record(model_id: str) -> Optional[dict[str, Any]]:
    """Return the newest valid audit for ``model_id``, if one is installed.

    This intentionally reads the small record directory on each catalogue
    request.  A newly installed sibling release can therefore publish its
    audit evidence without maintaining a second in-memory authority.
    """
    if not AUDIT_ROOT.exists():
        return None
    for path in sorted(AUDIT_ROOT.glob("*/*.audit.json"), reverse=True):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not _valid_record(raw):
            continue
        if raw["subject"]["model_id"] == model_id:
            return raw
    return None


def candidate_summary(model_id: str) -> Optional[dict[str, Any]]:
    record = audit_record(model_id)
    if record is None:
        return None
    # Standalone packaging carries source audits as reference evidence only;
    # they must be requalified against the packaged runtime before exposure.
    return None


def input_limits(model_id: str) -> dict[str, Any]:
    """Return detached, audit-bound input limits for one exact model.

    Runtime adapters use this helper for private execution choices such as
    sentence-section budgets and reference-audio preparation.  An unaudited
    model deliberately returns an empty mapping so the established adapter
    fallback remains in force until grounded evidence lands.
    """
    record = audit_record(model_id)
    if record is None:
        return {}
    value = record.get("contract", {}).get("input_limits")
    return json.loads(json.dumps(value)) if isinstance(value, dict) else {}


def qwen_17b_production_v2_limits(model_id: str) -> dict[str, Any]:
    """Return the measured 400/280 Auto section policy for one Qwen 1.7B checkpoint.

    The record is found the same generic way as every other policy question
    (`audit_record`, i.e. the newest valid ``*/*.audit.json`` for this exact
    model), and the pin is the *audit* those numbers were measured in. A model
    therefore gets the policy when its own record is that audit or explicitly
    derives from it - not because its repository name happens to be spelled in
    this file. See the anchor block above for why the pin is deliberate.
    """
    record = audit_record(model_id)
    if record is None:
        return {}
    if not _envelope_ok(record):
        return {}
    limits = _production_section_limits(record)
    if not limits:
        return {}
    if not _inherits_anchor(record, _records_by_audit_id()):
        return {}
    return limits
