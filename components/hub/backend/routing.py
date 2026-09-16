"""Where a job is allowed to run.

Three modalities submit jobs through this Hub, and every one of them answers to the same
question: automatic placement, or a machine the operator named. The values and the refusal
messages live here so that Image, Voice and Subtitle cannot drift apart on what a pin means -
a Subtitle batch that accepted a machine name the Image path would refuse is exactly the kind
of gap that leaves work queued forever with nothing to explain it.
"""

from __future__ import annotations

#: The Hub chooses, and any Studio that has the model may take the work.
POOL = "pool"
#: Keep the Hub Mac as the control plane and wait for a Mac that is not this one.
REMOTE = "remote"
#: Pin one Studio, spelled ``studio:<id>``.
STUDIO_PREFIX = "studio:"
#: Pin one Mac, spelled ``machine:<id>``. ``local`` means this Mac.
MACHINE_PREFIX = "machine:"

_RULE = "routing must be pool, remote, studio:<id> or machine:<id>"


def parse(value: object) -> tuple[str, str | None]:
    """Return ``(routing, error)`` for a submitted routing value.

    An absent value is the automatic choice, which is what almost every job wants; a value
    that is not one of the four forms is an error rather than something to silently ignore,
    because an operator who mistyped a machine name must be told, not quietly overruled.
    """
    if value is None or value == "":
        return POOL, None
    routing = str(value).strip()
    if routing in {POOL, REMOTE}:
        return routing, None
    for prefix in (STUDIO_PREFIX, MACHINE_PREFIX):
        if routing.startswith(prefix):
            target = routing[len(prefix):].strip()
            if target:
                return prefix + target, None
            break
    return routing, _RULE


def allows(studio: dict, routing: str) -> bool:
    """Whether ``studio`` may take work submitted with ``routing``.

    Only the pin forms filter anything: automatic placement and ``remote`` are decisions the
    caller's own scheduler already makes with the health and memory facts in front of it.
    """
    if routing.startswith(STUDIO_PREFIX):
        return studio.get("id") == routing[len(STUDIO_PREFIX):]
    if routing.startswith(MACHINE_PREFIX):
        return str(studio.get("machine") or "local") == routing[len(MACHINE_PREFIX):]
    return True
