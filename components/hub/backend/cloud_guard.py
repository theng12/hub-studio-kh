"""Keep Studio Hub's execution and catalog surfaces local-only.

Studio Hub KH exists to run models on the Macs it controls. Any job naming a
hosted model is refused at submission, before it is queued or persisted, and
hosted rows from stale sibling catalogs are filtered out. Render remains exempt:
its historical ``is_cloud`` flag is only a local broker hint for FFmpeg work.

This is not a second download or memory governor. A local model that is still
downloading keeps waiting exactly as it did.
"""

from __future__ import annotations

import logging
import re

from .monitor import LOCAL_ONLY_MODALITIES

log = logging.getLogger("studiohub.cloud_guard")
_packaged_monitor = None

REFUSAL_CODE = "CLOUD_WORK_RETIRED"

# Id schemes that are cloud by construction. The separator is load-bearing:
# ``fal:fal-ai/kling-video/...`` is a Video Studio cloud route, while
# ``fal/AuraFlow-v0.3`` is a LOCAL image model whose Hugging Face owner happens
# to be named "fal". Only the colon forms belong here.
CLOUD_ID_SCHEMES = ("provider:", "fal:", "kie:", "replicate:")

# Cloud inference vendors that Image Studio encodes as a repo *namespace*
# rather than a scheme (``cloudflare/flux-1-schnell``). These are the vendors
# actually observed in the fleet's catalogues; each one is an inference API,
# never a Hugging Face account the Hub would download from.
CLOUD_ID_NAMESPACES = (
    "cloudflare/", "gemini/", "together/", "nebius/", "pollinations/",
    "huggingface/",
)

# What a studio reports in ``provider`` for a model it runs itself. Image
# Studio says "local" on every local entry; the other studios omit the field.
LOCAL_PROVIDER_VALUES = {"", "local", "none", "hub", "on-device", "device"}

# Hosted inference vendors, matched as whole tokens of the reported ``provider`` so that
# "fal" and "fal-ai" match while "local-mlx" and "on-device" never do.
#
# This replaced an allowlist of local spellings, and the direction matters: with an allowlist,
# a studio or engine that reported a provider the Hub had not been taught - "local-mlx",
# "apple" - had every row it published classified as cloud and dropped from the aggregate
# catalog, so that modality's models vanished from every picker with no message anywhere. That
# is the 0.6.57 symptom arriving by a different road. An unrecognised provider is now local;
# the vendors below are refused, and an unrecognised value is named in the log rather than
# guessed about.
CLOUD_PROVIDER_NAMES = frozenset({
    "cloud", "cloudflare", "fal", "gemini", "huggingface", "kie", "nebius",
    "pollinations", "replicate", "together",
})
_CLOUD_PROVIDER_TOKEN = re.compile(r"[^a-z0-9]+")
_reported_unknown_providers: set[str] = set()

# Chat Studio does not set ``is_cloud`` on its catalogue entries; it declares
# the lane on the job instead. "local" runs on a Mac, "free" and "paid" are
# provider calls.
CLOUD_COST_TIERS = {"free", "paid"}


def _id_cloud_reason(model: str) -> str | None:
    lowered = model.strip().lower()
    for scheme in CLOUD_ID_SCHEMES:
        if lowered.startswith(scheme):
            return f"its id uses the cloud scheme '{scheme}'"
    for namespace in CLOUD_ID_NAMESPACES:
        if lowered.startswith(namespace):
            return f"its id is in the cloud-provider namespace '{namespace}'"
    return None


def _provider_is_cloud(provider: str) -> bool:
    """Whether a reported ``provider`` names a hosted inference vendor."""
    tokens = {token for token in _CLOUD_PROVIDER_TOKEN.split(provider) if token}
    return bool(tokens & CLOUD_PROVIDER_NAMES)


def _entry_cloud_reason(entry: dict) -> str | None:
    """Why one worker catalogue entry describes a cloud model, if it does.

    Deliberately reads four independent signals, because ``is_cloud`` alone is
    not trustworthy: Voice Studio's provider entries carried ``is_cloud=None``
    and announced themselves only through ``cache.state`` and ``kind``.
    """
    if entry.get("is_cloud"):
        return "its worker catalogue entry sets is_cloud"
    if str(entry.get("kind") or "").strip().lower() == "cloud":
        return "its worker catalogue entry sets kind=cloud"
    cache = entry.get("cache")
    state = cache.get("state") if isinstance(cache, dict) else cache
    if str(state or "").strip().lower() == "cloud":
        return "its worker catalogue entry reports cache.state=cloud"
    # ``cloud_provider`` says what it is by name; a value there is never a local spelling.
    cloud_provider = str(entry.get("cloud_provider") or "").strip().lower()
    if cloud_provider:
        return f"its worker catalogue entry names the cloud provider '{cloud_provider}'"
    # A row carrying a hosted *model identity* is a cloud row whatever `is_cloud` says: the
    # Hub's own publish path already strips these two fields as hosted-lane metadata
    # (`monitor._assemble_aggregate_catalog`), so their presence is the producer saying "hosted".
    # This is the fail-closed half of B12: a new vendor whose name is not in
    # `CLOUD_PROVIDER_NAMES` is still refused when it names which hosted model it is.
    for field in ("cloud_model_id", "provider_model"):
        value = str(entry.get(field) or "").strip()
        if value:
            return f"its worker catalogue entry carries a hosted model identity in '{field}'"
    provider = str(entry.get("provider") or "").strip().lower()
    if provider and _provider_is_cloud(provider):
        return f"its worker catalogue entry names the external provider '{provider}'"
    if provider and provider not in LOCAL_PROVIDER_VALUES:
        # Loud, once per value, and local: the alternative - treating an unknown spelling as
        # cloud - silently deletes a whole modality from every picker.
        if provider not in _reported_unknown_providers:
            _reported_unknown_providers.add(provider)
            log.warning(
                "worker catalogue reports the provider %r, which is not a known cloud "
                "vendor; treating it as a local runtime. Add it to CLOUD_PROVIDER_NAMES in "
                "components/hub/backend/cloud_guard.py if it is a hosted service.",
                provider,
            )
    return None


def cloud_reason(
    model: object,
    *,
    modality: object = None,
    entries: object = None,
    cost_tier: object = None,
) -> str | None:
    """Return why this workload is cloud, or ``None`` if it is local.

    RENDER IS EXEMPT, AND EXEMPT FIRST. Render Studio sets ``is_cloud=true`` on
    ``episode-assembly-v1`` purely to bypass the broker's download/memory
    governor — it is a local FFmpeg assembly step running on a Mac in the
    fleet, not a hosted model. ``LOCAL_ONLY_MODALITIES`` is the single place
    that fact is recorded, and this function honours it before it looks at
    anything else. Render is
    additionally safe by id: ``episode-assembly-v1`` matches no cloud scheme
    and no cloud namespace.
    """
    if str(modality or "").strip().lower() in LOCAL_ONLY_MODALITIES:
        return None
    reason = _id_cloud_reason(str(model or ""))
    if reason:
        return reason
    tier = str(cost_tier or "").strip().lower()
    if tier in CLOUD_COST_TIERS:
        return f"it was submitted on the '{tier}' cost tier, which is a provider call"
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        reason = _entry_cloud_reason(entry)
        if reason:
            return reason
    return None


def _cached_entries(model: object, modality: object) -> list[dict]:
    """Last-good worker catalogue entries for this model, without any I/O.

    Submission must never fan out to workers, so this reads the monitor's
    durable cache only. A cold cache simply means the id-scheme rule decides
    on its own — which today already covers every cloud entry in the fleet.
    """
    try:
        monitor = _packaged_monitor
        if monitor is None:
            from .main import monitor

        matches = monitor.cached_catalog_entries(
            str(model or ""),
            modality=str(modality) if modality else None,
        )
    except Exception:  # a submission must not fail because of a cache read
        return []
    return [match["entry"] for match in matches if isinstance(match.get("entry"), dict)]


def configure_monitor(monitor) -> None:
    global _packaged_monitor
    _packaged_monitor = monitor


def refusal(
    _envelope: object,
    *,
    model: object,
    modality: object = None,
    cost_tier: object = None,
) -> str | None:
    """The refusal message for hosted work, or ``None`` for local work.

    Callers must apply this at the API boundary, before the work is queued: a
    refused job must never be accepted and then failed downstream.
    """
    reason = cloud_reason(
        model,
        modality=modality,
        entries=_cached_entries(model, modality),
        cost_tier=cost_tier,
    )
    if reason is None:
        return None
    message = (
        f"Studio Hub does not accept cloud generation. "
        f"'{model}' is a cloud model because {reason}. Studio Hub runs the "
        f"local Mac fleet only."
    )
    log.warning(
        "refused cloud submission: model=%r modality=%r reason=%s",
        str(model or ""), str(modality or ""), reason,
    )
    return message


def catalog_cloud_reason(model: dict) -> str | None:
    """Why a pushed GenStudio fleet-catalog row is cloud, if it is.

    The fleet catalog asks this Hub to *cache* a model on its Macs, so a cloud
    row is meaningless here on top of being unwanted. Rows are keyed by
    ``sibling_studio`` and ``repo``.
    """
    if not isinstance(model, dict):
        return None
    return cloud_reason(
        model.get("repo"),
        modality=model.get("modality") or model.get("sibling_studio"),
        entries=(model,),
    )
