"""Per-model operator defaults, validated against the model's own published parameters.

One place, shared by every Studio, because the rule is the same everywhere and a second copy would
be free to disagree with the lane that enforces it:

    **A default is accepted only when the lane publishes that parameter, and only when the value
    is inside the bound the lane actually enforces.**

That is the whole contract, and it is why this module takes a lane's ``parameters`` list (the same
one ``lane_contract`` publishes) rather than a schema of its own. A parameter a lane does not
publish cannot be defaulted here; a value outside the published bound is refused by name; a
parameter the lane marks ``required`` is **not** defaultable, because a caller must supply it per
request and a stored value would be a quiet way to answer with something the operator did not
write this time.

Where the values live
---------------------
Each Studio keeps its own ``model_defaults.json`` beside its job ledger and its artifacts, the same
way it keeps ``memory_policy.json`` (when a held model is released) and Image keeps
``loading_mode.json`` (which memory configuration the next job loads with). Per machine, because a
default is a statement about how this operator wants this Mac to work.

Why the worker owns it rather than the app
------------------------------------------
A saved default is applied when a request **omits** the parameter, inside the worker. So the app,
this Mac's own Hub, a controller dispatching to it, and GatewayKH all get the same answer without
any of them knowing that defaults exist. The lane contract publishes the resolved value in each
parameter's ``default`` field, so a scheduler that wants to be explicit can read it and send it.

A default that no longer fits is dropped, never applied
------------------------------------------------------
A model can be updated: a bound can tighten, an option can be withdrawn. ``resolved()`` therefore
validates on the way **out** as well as on the way in, so a stored value that the current lane
would refuse is ignored rather than sent. The stored file is left alone — the operator's intent is
not lost because a model moved — and the Studio's status says which values are not being used.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

#: The file name every Studio uses, inside its own state directory.
SETTINGS_NAME = "model_defaults.json"

#: Parameter types a lane may publish, and the Python shape each one accepts. `integer` excludes
#: `bool` deliberately: in Python `True` is an `int`, and a boolean silently becoming `1` is the
#: same class of defect as a string `"false"` becoming true.
_TYPES: dict[str, tuple[type, ...]] = {
    "integer": (int,),
    "number": (int, float),
    "string": (str,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def settings_file(data_dir: str | os.PathLike[str]) -> Path:
    """Where one Studio's defaults live: beside its ledger, never the working directory."""
    return Path(data_dir) / SETTINGS_NAME


def load(data_dir: str | os.PathLike[str]) -> dict[str, dict]:
    """Every stored default, keyed by model repo. An unreadable file reads as none."""
    try:
        raw = json.loads(settings_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(repo): dict(values) for repo, values in raw.items()
            if isinstance(repo, str) and isinstance(values, dict)}


def _write(data_dir: str | os.PathLike[str], stored: dict[str, dict]) -> None:
    path = settings_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)


def editable(parameters: Iterable[dict]) -> list[dict]:
    """The parameters an operator may set a default for, in the lane's own order.

    Two exclusions, both because the thing being offered would not be a choice:

    * a ``required`` parameter is per-request content the caller supplies, and a stored default for
      one would answer with something nobody wrote this time;
    * a parameter whose ``enum`` holds exactly one value has nothing to choose between. Chat
      publishes ``model`` that way — the worker serves one model, ``required: false`` with a
      one-entry enum, which is honest in the contract and pointless as a control. Excluding it here
      is the general rule rather than a chat special case: the moment a worker serves two models
      the enum grows and the control appears by itself.

    Everything else the lane publishes is offered, and nothing it does not publish is.
    """
    offered = []
    for parameter in parameters:
        if not isinstance(parameter, dict) or not parameter.get("name"):
            continue
        if parameter.get("required"):
            continue
        options = parameter.get("enum")
        if isinstance(options, list) and len(options) == 1:
            continue
        offered.append(dict(parameter))
    return offered


def _check(parameter: dict, value: Any) -> str | None:
    """The reason one value is refused, or ``None`` when the lane would accept it."""
    name = str(parameter.get("name"))
    kind = parameter.get("type")
    accepted = _TYPES.get(str(kind))
    if accepted is None:
        return (f"{name}: the lane publishes type {kind!r}, which this setting has no rule for; "
                f"add it to components/model_defaults.py rather than storing an unvalidated value")
    if isinstance(value, bool) and str(kind) != "boolean":
        return f"{name}: expected {kind}, got a boolean"
    if not isinstance(value, accepted):
        return f"{name}: expected {kind}, got {type(value).__name__}"
    if str(kind) == "integer" and isinstance(value, bool):
        return f"{name}: expected integer, got a boolean"

    options = parameter.get("enum")
    if isinstance(options, list) and options and value not in options:
        return f"{name}: {value!r} is not one of {options}"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # A non-finite number is not a big number, it is a broken one: `json.dumps` writes
        # `Infinity`, which is not valid JSON, and a stored `NaN` or `Infinity` would make **every
        # later request that omitted this parameter** fail the route's own validation. The value is
        # refused at the door instead, where the operator can see why.
        if not math.isfinite(value):
            return f"{name}: {value!r} is not a finite number"
        minimum, maximum = parameter.get("minimum"), parameter.get("maximum")
        # `minimum_exclusive` is a **flag on `minimum`**, not a bound of its own — the shape the
        # lanes publish ("top_p: minimum 0.0, minimum_exclusive true" for a route that refuses
        # `top_p = 0`). Treating it as a number is how `0 <= True` produced "must be greater than
        # True"; the flag is read as a boolean and anything else leaves the bound inclusive.
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            if parameter.get("minimum_exclusive") is True:
                if value <= minimum:
                    return f"{name}: {value} must be greater than {minimum}"
            elif value < minimum:
                return f"{name}: {value} is below the lane's minimum of {minimum}"
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            if parameter.get("maximum_exclusive") is True:
                if value >= maximum:
                    return f"{name}: {value} must be less than {maximum}"
            elif value > maximum:
                return f"{name}: {value} is above the lane's maximum of {maximum}"

    limit = parameter.get("max_characters")
    if isinstance(limit, int) and isinstance(value, str) and len(value) > limit:
        return f"{name}: {len(value)} characters, above the lane's limit of {limit}"
    return None


def validate(parameters: Iterable[dict], values: dict) -> tuple[dict, dict[str, str]]:
    """``(accepted, refusals)`` for one model's proposed defaults.

    Refusals are keyed by parameter and carry the reason, so a route can answer with every problem
    at once instead of the first one — an operator who typed three bad values should not have to
    press Save three times to find out.
    """
    offered = {str(parameter["name"]): parameter for parameter in editable(parameters)}
    accepted: dict[str, Any] = {}
    refusals: dict[str, str] = {}
    for name, value in (values or {}).items():
        parameter = offered.get(str(name))
        if parameter is None:
            refusals[str(name)] = (
                "this lane publishes no such settable parameter, so there is nothing to default")
            continue
        reason = _check(parameter, value)
        if reason is not None:
            refusals[str(name)] = reason
            continue
        accepted[str(name)] = value
    return accepted, refusals


def stated(parameters: Iterable[dict]) -> dict:
    """Every parameter the lane gives a default for, **including the ones an operator is not
    offered a control for**.

    The two sets are deliberately different. The *editor* offers ``editable()`` — no required
    parameters, no one-option enums. The *job* needs every parameter's effective value, because a
    parameter with no control is still one the lane applies: an image request has to carry
    ``resolution`` whether or not there is a picker for it, and chat has to carry ``stream``.
    Building the effective map from the editable surface alone would silently drop them.
    """
    return {str(parameter["name"]): parameter["default"]
            for parameter in parameters
            if isinstance(parameter, dict) and parameter.get("name")
            and "default" in parameter}


def resolved(data_dir: str | os.PathLike[str], repo: str,
             parameters: Iterable[dict]) -> tuple[dict, dict[str, str]]:
    """The values in force for one model: what is stored, over what the lane states.

    Returns ``(in_force, not_used)`` — every parameter the lane defaults, plus any stored choice.
    A stored value the current lane would refuse is **not** applied and is named in ``not_used``
    with the reason: a model update that tightens a bound must not turn a stored number into a
    request the lane rejects.
    """
    parameters = [parameter for parameter in parameters if isinstance(parameter, dict)]
    offered = {str(parameter["name"]): parameter for parameter in editable(parameters)}
    in_force = stated(parameters)
    not_used: dict[str, str] = {}
    for name, value in (load(data_dir).get(repo) or {}).items():
        parameter = offered.get(str(name))
        if parameter is None:
            not_used[str(name)] = (
                "this lane publishes no such settable parameter, so there is nothing to default")
            continue
        reason = _check(parameter, value)
        if reason is not None:
            not_used[str(name)] = reason
            continue
        in_force[str(name)] = value
    return in_force, not_used


def save(data_dir: str | os.PathLike[str], repo: str, values: dict,
         parameters: Iterable[dict]) -> dict:
    """Store one model's defaults, replacing them wholesale.

    Wholesale on purpose: the editor shows every settable parameter, so what it sends is the whole
    answer, and merging would make a cleared field indistinguishable from an untouched one.
    Values equal to the lane's own default are still stored — the operator may have set them back
    deliberately, and "same as the model" is not the same statement as "never chosen".

    Raises a ``ValueError`` whose message is the JSON object of per-parameter reasons. Use
    :func:`refusals` to read it, rather than parsing the message in every Studio.
    """
    accepted, refusals_ = validate(parameters, values)
    if refusals_:
        raise ValueError(json.dumps(refusals_, sort_keys=True))
    stored = load(data_dir)
    if accepted:
        stored[repo] = accepted
    else:
        stored.pop(repo, None)
    _write(data_dir, stored)
    return accepted


def refusals(error: BaseException) -> dict[str, str]:
    """The per-parameter reasons out of a :func:`save` refusal, or ``{}``.

    One reader for all five Studios. Each of them needs to answer a bad ``PUT`` with the reasons
    keyed by parameter — an operator who typed three bad values should not have to press Save three
    times — and a private copy of this parse in every route is five places for the shape to drift.
    An error that is not one of ours reads as no reasons rather than raising, so a route can fall
    back to the message it was given.
    """
    try:
        parsed = json.loads(str(error))
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(name): str(reason) for name, reason in parsed.items()}


def clear(data_dir: str | os.PathLike[str], repo: str) -> None:
    """Forget one model's defaults, leaving it on whatever the lane states."""
    stored = load(data_dir)
    if stored.pop(repo, None) is not None:
        _write(data_dir, stored)


def status(data_dir: str | os.PathLike[str], models: Iterable[tuple[str, Iterable[dict]]]) -> dict:
    """The payload a Studio publishes for the Models tab, per model.

    ``models`` is ``(repo, parameters)`` per registered model, so this is a projection of the same
    lane contracts the catalog publishes rather than a second answer to the same question.

    * ``parameters`` — the settable surface, which is what an editor renders controls from;
    * ``stated`` — every value the lane itself defaults, including parameters with no control;
    * ``defaults`` — what is in force, which is ``stated`` with the operator's choices over it;
    * ``chosen`` — the settable parameters whose value differs from the lane's (a parameter with
      no control is never "chosen");
    * ``not_used`` — a stored value the lane now refuses, with the reason, so a model update is
      visible rather than silent.
    """
    rows = []
    for repo, parameters in models:
        parameters = [parameter for parameter in parameters if isinstance(parameter, dict)]
        offered = editable(parameters)
        in_force, not_used = resolved(data_dir, repo, parameters)
        lane = stated(parameters)
        rows.append({
            "repo": repo,
            "defaults": in_force,
            "stated": lane,
            "chosen": {str(parameter["name"]): in_force[str(parameter["name"])]
                       for parameter in offered
                       if str(parameter["name"]) in in_force
                       and in_force[str(parameter["name"])] != lane.get(str(parameter["name"]))},
            "not_used": not_used,
            "parameters": offered,
        })
    return {"models": sorted(rows, key=lambda row: row["repo"]), "settings_file": SETTINGS_NAME}
