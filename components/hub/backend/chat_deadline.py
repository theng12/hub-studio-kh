"""How long one Chat job may take, and when a quiet one is stopped.

**The defect this replaces.** `standalone.MODALITY_DEADLINE_FLOOR_S["chat"]` was one flat
1,800 s for the whole lane. It was derived from a real measurement — 30 scenes at ~403 tokens
each is 12,090 tokens, 1,008 s on an M1 — but it is applied to *every* job, so it is too short
for the longest one the fleet plans and far too long for the shortest. The owner's decision
(2026-10-19) is to compute it per job from what the job is: how much text goes in, how much is
expected out, and how fast this Mac has been measured to run.

**What the numbers are, and where each one comes from.**

* **The Mac's decode rate** is the worker's own stated `generation_tps`, read from the activity
  snapshot the Hub already polls every five seconds (`monitor.status[sid]["activity"]`). The chat
  worker publishes it on every generated token (`llm_engine._emit_progress`), and `activity.py`
  already validates and keeps it. Nothing new is measured here — where a Mac has never run a Chat
  job, the stated per-chip estimate is used instead, and the note says which of the two it was.
* **The Mac's prefill rate** is stated, not measured: nothing on this fleet has stored a prompt
  rate for the shipped chat model. `STATED_PREFILL_FASTER_THAN_DECODE` is the inference, in the
  safe direction (a slower prefill makes a *longer* deadline), and the ceiling bounds its cost.
* **Model load** is a stated estimate. The only measurements on this fleet are the tier run's
  9.08-13.69 s on 16 GB M1/M2/M4 (`qualification/README-chat-tiers.md`), and they are cold-load
  numbers on an idle Mac, so the estimate is rounded up.
* **Expected output** is the caller's `expected_output_tokens` hint when it sent one. This is the
  whole point of the hint: the Hub cannot tell a 100-prompt scene pack from a one-line question by
  reading the request, and Story Studio already computes that number. Without a hint, the p90 of
  the fleet's own answers (20,894 completion tokens over 1,122 succeeded answers, last 7 days) is
  used rather than `max_tokens`, which is a ceiling and would make every job three hours long.

**Why the floor and the ceiling both stay.** The floor is the lane's old number, so nothing that
fits today can be cut short by this change. The ceiling is four hours: past that the arithmetic is
not a deadline any more but a hang, and `stall_after_seconds()` catches a quiet job in minutes
whatever the ceiling is.

**The stall stop is not `stall.py`.** That module watches a *studio* go quiet and deliberately
never acts, because a merely-quiet worker may still be working. This one watches the job's own
token count, which is the stronger evidence the owner asked for: a job that has written no new
text for five minutes is stopped, with a reason that says exactly that and is distinct from a
deadline stop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable

#: How long a Chat job may go without writing a new token before it is stopped. Five minutes,
#: configurable, because the right number depends on the slowest Mac someone is willing to wait
#: for: at the fleet's slowest measured decode rate (12.0 tok/s) five minutes is 3,600 tokens,
#: which is more than the median answer (3,415) — a job that is genuinely working does not hit
#: this, and one that is wedged hits it while the operator is still watching.
STALL_AFTER_DEFAULT_S = 300.0

#: Multiplied into the whole estimate. Inference, not a measurement: it covers the round trip,
#: the tokenizer's own overhead and the ordinary spread between one Mac of a class and the next.
#: In the safe direction — a longer deadline cannot lose work, only notice a hang later.
SAFETY_MARGIN = 1.5

#: The longest a Chat job may be given, whatever the arithmetic says. Four hours.
CEILING_S = 4 * 3600.0

#: Characters per token, for estimating a prompt the Hub has no tokenizer for. Four is the
#: usual English average; it is an approximation and is labelled as one in every note that uses it.
CHARS_PER_TOKEN = 4.0

#: A cold model load on a 16 GB Mac, rounded up from the tier run's measured 9.08-13.69 s
#: (`qualification/README-chat-tiers.md`, 2026-09-25).
STATED_LOAD_S = 15.0

#: Decode tokens a second, per chip family, measured on the 16 GB tier with the shipped
#: `qwen3-5-9b-4bit` model (`qualification/model-readings.json`): 12.40-12.48 (M1), 14.14-14.02
#: (M2), 18.33-18.60 (M4). Rounded down, so the deadline they produce is the longer one.
STATED_DECODE_TPS: dict[str, float] = {"M1": 12.0, "M2": 14.0, "M4": 17.9}

#: A Mac whose chip nobody has measured is given the slowest measured one, which is the same
#: rule the Hub's Image speed ranking already uses (`broker._UNMEASURED_S`).
UNMEASURED_DECODE_TPS = 12.0

#: Prefill runs several times faster than decode, because the prompt is processed as one batch
#: rather than one token at a time. Nothing on this fleet has stored a prompt rate for the
#: shipped chat model, so this is an inference [I] — measured at 11x decode on the 26B MoE
#: (`qualification/evidence/gemma4-20261007-32gb`), stated here as 5x, which is the conservative
#: direction: it makes prefill look slower and so makes the deadline longer.
STATED_PREFILL_FASTER_THAN_DECODE = 5.0

#: What one scene prompt is expected to cost, in output tokens. This is Story Studio's own
#: per-scene figure (`scene-prompt-batching.ts`, `tokensPerScene: 300`), used here only for the
#: Hub's own pack lane, where the Hub knows how many scene ids the pack carries.
TOKENS_PER_SCENE = 300.0

#: What a Chat answer is expected to cost when nobody said. The p90 of 1,122 succeeded answers
#: over seven days on the live fleet was 20,894 completion tokens (p50 3,415, p99 52,518,
#: max 64,677); rounded down, because the floor covers the rest.
DEFAULT_EXPECTED_OUTPUT_TOKENS = 20_000

#: The header a caller sends its own expectation in. A header, not a body field, because the body
#: is forwarded verbatim to the chat worker and the worker refuses a field it does not know.
HINT_HEADER = "X-StudioHub-Expected-Output-Tokens"


@dataclass(frozen=True)
class ChatDeadline:
    """One job's computed limit, and the arithmetic behind it in plain words."""

    seconds: float
    note: str

    @property
    def minutes(self) -> float:
        return round(self.seconds / 60.0, 1)


class ChatStalled(Exception):
    """A Chat job that wrote no new token for longer than the stall window.

    Carries the sentence the operator reads, so the reason is stated once, here, and every
    surface that reports a stopped job reports the same words.
    """

    def __init__(self, silent_for: float) -> None:
        self.silent_for = silent_for
        super().__init__(stall_reason(silent_for))


def stall_after_seconds() -> float:
    """The stall window, in seconds, from `STUDIOHUB_CHAT_STALL_SECONDS` or the default.

    A malformed or non-positive setting is ignored rather than obeyed: a zero window would stop
    every job on its first poll, and that is not a setting anyone meant to make.
    """
    raw = os.environ.get("STUDIOHUB_CHAT_STALL_SECONDS", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return STALL_AFTER_DEFAULT_S
    return value if math.isfinite(value) and value > 0 else STALL_AFTER_DEFAULT_S


def stall_reason(silent_for: float) -> str:
    """The plain-words reason for a stall stop, distinct from a deadline stop."""
    return f"stopped: no new text for {_duration(silent_for)}"


def deadline_reason(deadline: ChatDeadline) -> str:
    """The plain-words reason for a deadline stop, distinct from a stall stop."""
    return (f"stopped: no answer within the job's own {_duration(deadline.seconds)} limit "
            f"({deadline.note})")


def deadline_passed(started: float, deadline: ChatDeadline) -> bool:
    """Whether the job's own limit has elapsed since `started` (a `time.monotonic()` reading).

    A read timeout on a one-request Chat job is the deadline only when this is true.
    """
    return time.monotonic() - started >= deadline.seconds


def _duration(seconds: float) -> str:
    """Seconds as the words a person would say: hours, then minutes, then seconds.

    Rounded to the unit a person would use rather than truncated to one they would not — a
    deadline of 4,072 s is "68 minutes" to whoever has to wait for it, and "4072 seconds" is a
    number they have to divide before it means anything.
    """
    value = float(seconds)
    for size, name in ((3600.0, "hour"), (60.0, "minute")):
        if value >= size * 1.5:
            whole = int(round(value / size))
            return f"{whole} {name}" + ("" if whole == 1 else "s")
    whole = max(1, int(round(value)))
    return f"{whole} second" + ("" if whole == 1 else "s")


def hint_from(value: object) -> int | None:
    """A caller's `expected_output_tokens` hint, when it is a usable positive whole count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number != int(number):
        return None
    return int(number)


def _chip_family(chip: object) -> str:
    """`Apple M1 Pro` -> `M1`, so a chip family and its variants share one measured rate."""
    text = str(chip or "").replace("Apple", "").strip()
    for family in STATED_DECODE_TPS:
        if text.startswith(family):
            return family
    return ""


#: How long one Mac's chip family is remembered. The same shape as `broker._speed_cache`: the
#: answer cannot change while the Hub runs, and the lookup is not free.
_CHIP_TTL_S = 60.0
_chip_cache: dict[str, tuple[float, str]] = {}


def _chip_family_for(studio: dict) -> str:
    """This Mac's chip family, read at most once a minute.

    `broker._host_for_studio` reaches `resource_stats.host_stats()`, which **spawns `sysctl`** —
    and this runs on the chat request path, before every completion. A chip cannot change under a
    running Hub, so the answer is cached per machine rather than paid for per request.
    """
    machine = str(studio.get("machine") or "local")
    now = time.monotonic()
    cached = _chip_cache.get(machine)
    if cached is not None and now - cached[0] < _CHIP_TTL_S:
        return cached[1]
    family = _chip_family((_host(studio) or {}).get("chip"))
    _chip_cache[machine] = (now, family)
    return family


def measured_rates(monitor: Any, worker_id: str) -> tuple[float, float] | None:
    """This Mac's own `(prefill, decode)` tokens a second, or ``None`` when it has never said.

    Read from the activity snapshot the Hub already polls, so this costs no request: the chat
    worker states `generation_tps` and `prompt_tps` on every generated token and the Hub keeps
    the last validated snapshot per studio. A rate from the job that just finished is the same
    machine and the same model, which is the measurement being asked for.
    """
    status = (getattr(monitor, "status", None) or {}).get(worker_id) or {}
    snapshot = status.get("activity") if isinstance(status, dict) else None
    if not isinstance(snapshot, dict):
        return None
    for slot in ("active", "latest"):
        item = snapshot.get(slot)
        if not isinstance(item, dict):
            continue
        prefill = _positive(item.get("prompt_tps"))
        decode = _positive(item.get("generation_tps"))
        if decode is not None:
            return (prefill if prefill is not None
                    else decode * STATED_PREFILL_FASTER_THAN_DECODE), decode
    return None


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def input_tokens(request_body: bytes) -> int:
    """The prompt's own size, estimated from the text the request carries.

    The Hub has no tokenizer for this model, so this is `chars / 4` and every note that uses it
    says "estimated". Erring high is the safe direction, and it is bounded by the ceiling.
    """
    try:
        payload = json.loads((request_body or b"").decode("utf-8", "replace"))
    except (ValueError, TypeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    chars = 0
    for message in payload.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            # Parts: the text is counted, the image's data URI is not text and is not counted.
            # shortcut: an image's own soft tokens (Gemma 4: 280) are left out, ~1% of a
            # 32,768-token prompt — upgrade to per-part counts if a caller ever sends many images.
            chars += sum(len(part.get("text") or "") for part in content
                         if isinstance(part, dict) and isinstance(part.get("text"), str))
    return int(math.ceil(chars / CHARS_PER_TOKEN)) if chars else 0


def expected_output_tokens(hint: object, scene_count: int | None = None) -> tuple[int, str]:
    """What this answer is expected to cost, and where the number came from, in plain words."""
    stated = hint_from(hint)
    if stated is not None:
        return stated, f"{stated:,} expected output tokens (the caller's own hint)"
    if scene_count and scene_count > 0:
        estimate = int(math.ceil(scene_count * TOKENS_PER_SCENE))
        return estimate, (f"{estimate:,} expected output tokens "
                          f"({scene_count} scene prompts x {int(TOKENS_PER_SCENE):,})")
    return DEFAULT_EXPECTED_OUTPUT_TOKENS, (
        f"{DEFAULT_EXPECTED_OUTPUT_TOKENS:,} expected output tokens "
        f"(no length was stated, so the fleet's own p90 answer)")


def chat_deadline(monitor: Any, studio: dict, *, request_body: bytes = b"",
                  hint: object = None, scene_count: int | None = None,
                  floor_s: float) -> ChatDeadline:
    """One Chat job's limit: load, plus the prompt, plus the expected answer, times a margin.

    `floor_s` is the lane's published floor, passed in rather than restated so the number a
    scheduler reads and the number enforced here cannot drift (`standalone.deadline_floor_seconds`).
    """
    measured = measured_rates(monitor, str(studio.get("id") or ""))
    if measured is not None:
        prefill_tps, decode_tps = measured
        rate_note = f"this Mac's own measured rate ({decode_tps:.1f} tok/s)"
    else:
        family = _chip_family_for(studio)
        decode_tps = STATED_DECODE_TPS.get(family, UNMEASURED_DECODE_TPS)
        prefill_tps = decode_tps * STATED_PREFILL_FASTER_THAN_DECODE
        rate_note = (f"{family or 'unmeasured chip'} estimate ({decode_tps:.1f} tok/s, "
                     f"stated)")

    expected, expected_note = expected_output_tokens(hint, scene_count)
    prompt = input_tokens(request_body)

    load_s = STATED_LOAD_S
    prefill_s = prompt / prefill_tps if prefill_tps > 0 else 0.0
    decode_s = expected / decode_tps
    wanted = SAFETY_MARGIN * (load_s + prefill_s + decode_s)

    seconds = max(float(floor_s), min(wanted, CEILING_S))
    note = (f"{_duration(seconds)}: {load_s:.0f} s model load + {prompt:,} input tokens "
            f"(estimated from the request) at {prefill_tps:.1f} tok/s = {prefill_s:.0f} s "
            f"+ {expected_note} at {rate_note} = {decode_s:.0f} s, x {SAFETY_MARGIN} safety")
    if seconds > wanted:
        note += f"; held up to the {_duration(floor_s)} floor"
    elif wanted > CEILING_S:
        note += f"; capped at the {_duration(CEILING_S)} ceiling"
    return ChatDeadline(seconds=seconds, note=note)


def _host(studio: dict) -> dict | None:
    """This Mac's host telemetry, from the Hub's own cached peer/host view."""
    try:
        from . import broker
        return broker._host_for_studio(studio)
    except Exception:  # noqa: BLE001 - absent telemetry is "unmeasured chip", never a failure
        return None


def progress_signature(monitor: Any, studio_id: str) -> tuple[str | None, int] | None:
    """`(job id, tokens written)` for this studio's current job, or ``None`` with no evidence.

    ``None`` is the important half. The chat worker's activity report is optional — a worker old
    enough not to publish one, or one whose reporter is currently failing, has no token count to
    read, and *that is not silence*. Stopping a job because the Hub cannot see it would fail
    healthy work on exactly the Macs that report least, so the caller treats ``None`` as "watch
    nothing" and leaves the deadline as the only bound.

    A *change* in either half means the worker is writing: a new job id starts a new clock, and a
    rising token count restarts the silence window. The prompt's own processed-token counter is
    included, so a long prefill counts as progress exactly as a generated token does.
    """
    status = (getattr(monitor, "status", None) or {}).get(studio_id) or {}
    if not isinstance(status, dict) or status.get("activity_support") != "available":
        return None
    snapshot = status.get("activity")
    if not isinstance(snapshot, dict):
        return None
    item = snapshot.get("active")
    if not isinstance(item, dict):
        item = snapshot.get("latest")
    if not isinstance(item, dict):
        return (None, 0)
    written = 0
    for name in ("generation_tokens", "processed_prompt_tokens"):
        value = item.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            written += value
    job_id = item.get("id")
    return (str(job_id) if job_id else None, written)


async def await_with_progress(monitor: Any, studio_id: str, awaitable: Awaitable[Any], *,
                              stall_after: float | None = None) -> Any:
    """Await one Chat job, but stop waiting when it writes no new token for the stall window.

    The worker has no cancel endpoint — its own lane contract says so — so a stop here is what a
    deadline stop has always been: the Hub abandons the answer, releases the Mac and records why.
    The difference is that this one fires on *silence* rather than on elapsed time, which is what
    makes a four-hour ceiling safe to hold.

    Reads the monitor's already-polled snapshot rather than issuing its own request, so watching a
    job costs nothing on the wire.
    """
    threshold = stall_after_seconds() if stall_after is None else float(stall_after)
    # A quarter of the window, so the stop lands within the window rather than a whole poll after
    # it. Bounded above by the monitor's own poll interval: reading faster than the Hub re-polls
    # cannot see anything new.
    interval = max(0.05, min(5.0, threshold / 4.0))
    task = asyncio.ensure_future(awaitable)
    signature = progress_signature(monitor, studio_id)
    last_change = time.monotonic()
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=interval)
            if task in done:
                return task.result()
            now = time.monotonic()
            current = progress_signature(monitor, studio_id)
            if current is None:
                # No token evidence to read: an unseeing Hub is not a quiet worker, and the
                # deadline stays the only bound on this job.
                signature, last_change = None, now
            elif current != signature:
                signature, last_change = current, now
            if now - last_change > threshold:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                raise ChatStalled(now - last_change)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
