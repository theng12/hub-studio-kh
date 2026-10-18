"""Preferred-work idle hold — the one decision module for the per-Mac preference.

Owner request, 2026-10-17: an optional per-Mac choice of a *preferred generation function*
(LLM/Chat, TTS/Voice, Image, Music or Video — the lanes that genuinely generate through the
broker/Chat contracts) that wins over the ranked function priority while preferred demand
lasts, then keeps the Mac reserved for ``idle_seconds`` after its last preferred job actually
finishes before lending the Mac to other work. OFF by default; OFF is exactly the previous
behaviour.

One module owns the vocabulary and the clock arithmetic so the scheduler lanes, the policy API
and the native UI cannot drift:

* **Demand wins.** While the preference is enabled, an *eligible* preferred candidate beats
  every other generation candidate on this Mac — not merely ties with the old rank order,
  whose winner may name a different function.
* **The hold follows real completions, on the Mac that ran the job.** The window opens only
  when a preferred job of this Mac actually finishes (its own lane's terminal transition, or
  this Mac's peer door observing a proxied job terminal — never an unserviceable request, a
  disconnect or a TTL prune). A restart never invents one: with no completion evidence the
  Mac simply lends until the first preferred job runs.
* **The hold is bounded.** Remaining time is clamped to the configured window, so a clock
  jump backwards reads as at most a full window, a restart reads the same bound, and no
  stored state can hold the Mac past its own expiry. The deadline is persisted exactly once
  per completion — never per poll.
* **Nothing preempts.** A running non-preferred job always finishes; unknown/uncertain leases
  keep the machine fenced through ``broker.busy_machines()`` exactly as before.
* **Residency follows the operator's memory mode, with one bounded exemption.** The hold never
  asks a worker to unload: while it runs, no non-preferred job dispatches to this Mac, so the
  Hub's coordinated release never targets the preferred model. A bounded dispatch-time
  reservation (written immediately before the worker POST) additionally answers the race where
  a worker's own idle timer fires at the finish instant. A worker skips only its OWN idle
  release while the preferred hold or reservation is alive; manual release, memory pressure,
  an explicitly stored ``immediate`` mode, crashes and restarts still win. This coordinates
  scheduling; it does not promise a warm model, and Video (which drops its pipeline per job
  by construction) never reports one.
"""
from __future__ import annotations

from components import preferred_hold_clock

from . import registry
from .vocabulary import NODE_MODALITIES

# Re-exported so the scheduler keeps one import site; the arithmetic itself is the shared leaf
# that workers also read (``components/preferred_hold_clock.py``).
hold_remaining_s = preferred_hold_clock.hold_remaining_s
hold_expires_wall = preferred_hold_clock.hold_expires_wall

# The generation functions a preference may name: the Chat lane plus every broker generation
# modality (`broker.MODALITY` plus `chat`, which owns its own job module). `render` is
# deliberately absent — it is the Hub Mac's own FFmpeg assembly step and already outranks
# everything through `acquire_external_machine`; a hold must never block it.
# `transcription` is a conversion, not generation; `embeddings`/`vision` run beside the machine
# lease (`standalone._PEER_UNLEASED_PATHS`) so a "hold" could not mean anything for them.
# tests/test_preferred_work_idle_hold.py pins this set against `broker.MODALITY` so a new
# generation lane must be classified here deliberately.
GENERATION_CHOICES: frozenset[str] = frozenset(
    modality for modality in ("chat", "image", "voice", "music", "video")
    if modality in NODE_MODALITIES
)


def choices_for(local_modalities) -> list[str]:
    """The selectable generation functions this Mac actually serves, in stable display order."""
    order = ["chat", "voice", "image", "music", "video"]
    return [modality for modality in order
            if modality in GENERATION_CHOICES and modality in set(local_modalities)]


def normalized(policy: object) -> dict | None:
    """A registry preference made safe for the scheduler (malformed/disabled = off)."""
    if not isinstance(policy, dict):
        return None
    if policy.get("modality") not in GENERATION_CHOICES or not policy.get("enabled"):
        return None
    low, high = registry.PREFERRED_WORK_BOUNDS
    seconds = policy.get("idle_seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not low <= seconds <= high:
        seconds = registry.PREFERRED_WORK_DEFAULT_S
    return {"modality": str(policy["modality"]), "idle_seconds": int(seconds)}


def active(policy: object) -> dict | None:
    """The preference in force right now — an alias of ``normalized`` for the scheduler's
    read sites, kept as its own name so call-sites state intent ("is anything enabled")."""
    return normalized(policy)


def demand_wins(policy: dict | None, candidate_modality: object) -> bool:
    """Whether ``candidate_modality`` is the preferred function this setting reserves for."""
    return bool(policy) and candidate_modality == policy["modality"]


def state(policy: dict | None, hold: dict | None, running_modality: str | None,
          machine_prefers: bool = False) -> dict:
    """The simple actual state a UI publishes: off / working / holding / lending.

    ``machine_prefers`` marks that this Mac is currently running the preferred function's
    work (the caller reads it from its own running-job evidence; it is never guessed).
    """
    if not policy:
        return {"phase": "off", "remaining_s": None, "expires_at": None}
    if machine_prefers:
        return {"phase": "working", "remaining_s": None, "expires_at": None}
    remaining = hold_remaining_s(policy, hold)
    if remaining > 0.0:
        return {"phase": "holding",
                "remaining_s": round(remaining, 1),
                "expires_at": hold_expires_wall(policy, hold)}
    return {"phase": "lending", "remaining_s": None, "expires_at": None}


def snapshot_fields(policy: dict | None, hold: dict | None) -> dict:
    """What a node publishes on its own snapshot for its controller's group arbitration."""
    if not policy:
        return {"preferred_work": None, "preferred_hold_remaining_s": 0.0}
    return {"preferred_work": {"enabled": True, "modality": policy["modality"],
                               "idle_seconds": policy["idle_seconds"]},
            "preferred_hold_remaining_s": round(hold_remaining_s(policy, hold), 1)}
