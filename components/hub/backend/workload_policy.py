"""The memory floors the Hub enforces for routed workloads, read from the worker.

Studio catalogs describe what their engine can *technically* attempt. The Hub also owes the
operator the *product* floor: the number a worker measured for the model it serves. That number
belongs to the worker — it knows the weights, the quantization and the residency it manages — so
this module reads it from the catalog row the worker published and never from a table of repo
prefixes here.

That table (`mlx-community/qwen3-tts-12hz-0.6b-` -> 8.0/3.2) was a trap of exactly the kind the
owner's rule forbids: a model added later kept only its own catalog value, silently, because no
prefix matched, so the Hub's stricter production policy simply was not applied. Every spelling a
shipped worker actually publishes is read instead:

* ``min_unified_memory_gb``  — Image, Voice and Music
* ``min_total_memory_gb``    — Chat and Subtitle
* ``qualified_minimum_memory_gb`` — the tier under a second name
* ``min_free_memory_gb``     — an explicit live-free admission floor
* ``required_free_memory_gb`` — the Voice worker's own computed admission floor

``max(...)`` is kept across every spelling, so the strictest published number wins. A floor that
is present but cannot be read raises instead of being skipped: a dropped floor is a silently
weaker gate.
"""

from __future__ import annotations

import math

# The spellings a worker may publish a *total-memory tier* under.
_TOTAL_MEMORY_FIELDS = (
    "min_unified_memory_gb", "min_total_memory_gb", "qualified_minimum_memory_gb",
)
# The spellings a worker may publish a *live-free* admission floor under.
_FREE_MEMORY_FIELDS = ("min_free_memory_gb", "required_free_memory_gb")

# Both floors are read from the producer, and only from the producer. Two earlier versions of
# this file kept a Hub-side table: first keyed by repo *prefix* (`mlx-community/qwen3-tts-12hz-0.6b-`),
# which silently skipped every model it had not been taught, then keyed by exact model id, which
# had the same failure one model later. A floor the Hub keeps to itself is a model-keyed list in a
# consumer, and the symptom is silent: the next model the owner proves keeps only its catalog value
# with no production policy applied, and nothing says so.
#
# So the owner-approved floor for Qwen3-TTS 12Hz 0.6B Base 8-bit (3.2 GB live free) now lives on
# that model's own row in `components/voice/app/backend/catalog.py` and reaches here through its
# catalog row. `required_free_memory_gb` below takes the strictest value the row publishes, so a
# producer that publishes a stricter number wins automatically and there is nothing to remember.


def _identifiers(requested_model: str, catalog_entry: dict) -> list[str]:
    return [str(value or "").strip().lower() for value in (
        requested_model, catalog_entry.get("repo"),
        *(catalog_entry.get("aliases") or []),
    )]


def _published_floor(catalog_entry: dict, fields: tuple[str, ...],
                     requested_model: str) -> float | None:
    """The strictest floor the worker published, or ``None`` when it published none.

    ``None``/``""`` mean "not published" and stay silent; anything else that is not a finite
    non-negative number is a real fault in the row and names the field and the model, because
    dropping it would leave the Hub enforcing a weaker floor than the worker asked for.
    """
    values: list[float] = []
    for field in fields:
        if field not in catalog_entry:
            continue
        raw = catalog_entry.get(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if isinstance(raw, bool):
            parsed = None
        else:
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                parsed = None
        if parsed is None or not math.isfinite(parsed) or parsed < 0:
            raise ValueError(
                f"the catalog row for {requested_model or catalog_entry.get('repo')!r} publishes "
                f"{field}={raw!r}, which is not a memory floor in GB; fix the value in that "
                f"worker's own /api/catalog row (Hub readers: "
                f"components/hub/backend/workload_policy.py)")
        values.append(parsed)
    return max(values) if values else None


def required_total_memory_gb(requested_model: str, catalog_entry: dict) -> float | None:
    """Return the strictest total-memory floor: what the worker publishes, or Hub policy.

    ``requested_model`` may be one of a catalog entry's aliases; the floor is the same row either
    way, so every identifier resolves to the entry the caller passed. A model whose worker
    publishes no tier returns ``None`` - the Hub then has no production floor to apply, and says
    so through ``memory_admission.describe`` rather than inventing one.
    """
    return _published_floor(catalog_entry, _TOTAL_MEMORY_FIELDS, requested_model)


def required_free_memory_gb(requested_model: str, catalog_entry: dict) -> float | None:
    """Return the strictest live-free-memory floor for this model.

    ``size_gb`` is deliberately not considered: Studio catalogs define it as download/disk size,
    which can be much larger than the memory mapped by a quantized or offloaded runtime. The
    worker publishes it, and the producer is the only place it can come from: a floor the Hub
    keeps to itself is a model-keyed table, and the next model the owner proves would silently
    get nothing.
    """
    return _published_floor(catalog_entry, _FREE_MEMORY_FIELDS, requested_model)


# The reference (edit) lane's floors live inside the worker's own lane contract, beside the count
# window and the measured runtime they were measured with - one block, one producer. They are read
# only for an edit item: a text-to-image job on the same model keeps the model's own floor.
def _reference_floor(catalog_entry: dict, field: str, requested_model: str) -> float | None:
    """One edit-lane memory floor, or ``None`` when the worker published none."""
    contract = catalog_entry.get("lane_contract")
    references = contract.get("references") if isinstance(contract, dict) else None
    if not isinstance(references, dict):
        return None
    raw = references.get(field)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        parsed = None
    else:
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            parsed = None
    if parsed is None or not math.isfinite(parsed) or parsed < 0:
        raise ValueError(
            f"the catalog row for {requested_model or catalog_entry.get('repo')!r} publishes "
            f"lane_contract.references.{field}={raw!r}, which is not a memory floor in GB; fix "
            f"the value in that worker's own /api/catalog row (Hub readers: "
            f"components/hub/backend/workload_policy.py)")
    return parsed


def required_reference_total_memory_gb(requested_model: str,
                                       catalog_entry: dict) -> float | None:
    """The total-memory floor this model's reference (edit) lane publishes, if any."""
    return _reference_floor(catalog_entry, "min_total_memory_gb", requested_model)


def required_reference_free_memory_gb(requested_model: str,
                                      catalog_entry: dict) -> float | None:
    """The live-free floor this model's reference (edit) lane publishes, if any."""
    return _reference_floor(catalog_entry, "min_free_memory_gb", requested_model)
