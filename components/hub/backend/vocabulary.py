"""The Hub's modality and operation vocabulary — one place, read by every consumer.

A *modality* is registered once, here. Three surfaces used to restate it and each drifted:

* the peer surface's routing table (``standalone.WORKER_FOR_MODALITY``),
* the fleet join's accepted set (``fleet_nodes.NODE_MODALITIES``) — ``chat`` was missing, so a
  node running the shipped Chat worker was refused outright at join time, and an
  already-joined node's chat studio was silently skipped when the controller built its
  registry. The file's own comment records the identical Music incident before it,
* the packaged worker table (``standalone_config._REQUIRED_WORKERS``), which is the inverse.

The *operation* a job is filed under is the second half. ``ledger._VALID_OPERATIONS`` was a
third, shorter copy of it: ``music`` was missing, so both the write and the read path coerced
every Music activity row to the ``"speech"`` default and Music's history was filed under
Voice — while the live view's copy did include music, so the two disagreed about the same job.

Nothing here is a model list. Adding a model never touches this file. Adding a modality or a
worker is a deliberate change that lands in exactly one place.
"""

from __future__ import annotations

import re

# modality -> the local worker id that serves it. This is the Hub's modality vocabulary: the
# peer surface routes /studio/<modality>/... with it, the fleet join validates a node's
# studios against it, and the packaged worker table is its inverse.
WORKER_FOR_MODALITY: dict[str, str] = {
    "image": "image",
    "voice": "voice",
    "transcription": "subtitle",
    "music": "music",
    "chat": "chat",
    # Video is a real worker now, not a placeholder: `components/video` serves both the
    # text-to-video JSON door and the image-to-video multipart door the broker already had.
    "video": "video",
    # Text embeddings for Story's meaning check: synchronous like Chat, but never takes the Mac's
    # lease (see `standalone._PEER_UNLEASED_PATHS`).
    "embeddings": "embeddings",
    # Image analysis (depth, subject, points, outlines) for Story's effects: synchronous and
    # unleased like Embeddings.
    "vision": "vision",
}

# Every modality a node may offer, derived so it cannot drift from the routing table.
NODE_MODALITIES = frozenset(WORKER_FOR_MODALITY)

# **The functions one Mac's saved work order may rank (owner, 2026-10-28).**
#
# Every node modality is rankable, plus one that is *not* a worker: `render`, final video
# assembly. It has no row here because it is not a peer route — it is this Mac's own FFmpeg
# lane (`render_executor`), dispatched by the broker's own `next_work` beside the others.
# Until 2026-10-28 the broker hardcoded it ahead of everything, so an owner whose saved order
# named Chat first still had the Mac reserved for a waiting render. Now it is an ordinary
# entry in the same list. A Mac whose saved order does not mention it keeps that behaviour
# exactly: `registry.with_render_default` reads render first for it, so neither a stored
# order nor an older build has to change.
RENDER_MODALITY = "render"
PRIORITY_MODALITIES = NODE_MODALITIES | {RENDER_MODALITY}

# What each rankable function is called where the order is edited. `render` is *not* a
# worker, so it has no registry row to take a title from — this is its name, and the only
# one: the Hub app and the Gateway register both read it from here rather than each
# capitalising the id into "Render".
PRIORITY_TITLES: dict[str, str] = {RENDER_MODALITY: "Final render"}

# **The one default order** (owner, 2026-10-28: "we shouldn't have a hardcode but instead a
# default one ... arrange-able to match what we wanted").
#
# It is what a saved order that names none of these reads for them, and it is written **once**,
# here — `registry.with_render_default` is the only reader, and the lease rule reads the same
# tuple instead of repeating `True`. Today it holds the one behaviour render had before it was
# rankable: a Mac whose saved order does not mention it ranked it first. An empty saved order is
# *not* given this default (see `with_render_default`): "no preference" is a deliberate state
# where every enabled function is equal, and it is also every Mac that never saved anything.
DEFAULT_PRIORITY_ORDER: tuple[str, ...] = (RENDER_MODALITY,)

# The worker ids the packaged Hub must find, derived as the inverse of the same table.
REQUIRED_WORKERS = {
    worker: modality for modality, worker in WORKER_FOR_MODALITY.items()
}

# A reported operation is a bounded token, never free text: it is persisted in the ledger and
# shown in history, so it must not be able to carry arbitrary worker content.
_OPERATION_TOKEN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")

# studio (its registry modality, machine suffix stripped) -> the operation its jobs are filed
# under when the worker does not report one. Voice says "speech" because that is what the Voice
# worker and the existing history already call it.
STUDIO_OPERATION: dict[str, str] = {
    "image": "image",
    "voice": "speech",
    "transcription": "transcription",
    "music": "music",
    "chat": "chat",
    "video": "video",
    "embeddings": "embeddings",
    "vision": "vision",
}

# The operations the Hub will file a job under when a studio names one of them.
VALID_OPERATIONS = frozenset(STUDIO_OPERATION.values())


def operation_for(studio: str, operation: str | None) -> str:
    """The operation a job is filed under — live view and durable ledger alike.

    The worker's own report wins when it is a bounded token, so a studio that names its own
    operation is taken at its word and a modality added later files under its own name.

    Otherwise the studio decides. A studio nobody has classified yet also files under its own
    name, because the old rule was a closed list plus a ``"speech"`` default: ``music`` was not
    on the list, so every Music row was stored and read back as Voice's operation.
    """
    token = str(operation or "").strip().lower()
    if _OPERATION_TOKEN.fullmatch(token):
        return token
    studio_key = str(studio or "").split("@", 1)[0].strip().lower()
    if not _OPERATION_TOKEN.fullmatch(studio_key):
        return "unknown"
    return STUDIO_OPERATION.get(studio_key, studio_key)
