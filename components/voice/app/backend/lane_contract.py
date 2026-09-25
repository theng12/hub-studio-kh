"""The voice lane's published dispatch contract (`studiohub.lane-contract.v1`).

A scheduler learns *that* a lane exists from the modality the Hub publishes. It
cannot learn from a modality string what to send, what comes back, which states
are terminal, or what a cancel means. This module is the voice side of
`docs/lane-execution-contract.md`: the shape every studio publishes so a new lane
is a data change on the Hub and no gateway release at all.

Two rules from that document decide everything here:

* **Only what the worker honours.** A parameter this lane silently drops must not
  be listed, so the list is built from the model's own capabilities and from the
  fields its family's resolver actually reads - not from the request model's full
  field set, which accepts several keys for compatibility and ignores them.
* **Only a bound that is enforced.** `text` carries the model's own audited
  ceiling (`text_max_characters`) pinned against the API's own cap; `speed`
  carries the clamp the dispatcher applies; `section_max_characters` carries the
  range the catalogue will actually accept. Where nothing states a bound, none is
  published.

`deadline_seconds` is deliberately absent: it is the Hub's own peer-proxy timeout
(one constant, enforced by the Hub), and a worker restating it would be a copy of
a Hub number. `runtime_seconds` is ours, and it appears only where a run has
been measured, with the scene and the machine named.

`runtime_seconds` comes from one of two stored records, never from a table kept
here:

* `qualification/evidence/voice-runtime-2026-09-25/measured-runtimes.json` - the
  probe receipts, one row per stored run, each naming the file its number was
  read from. `components/voice/app/tests/test_lane_contract.py` re-reads those
  files and fails when a published number and its receipt disagree.
* the model's own audit record under `components/voice/model-audits/`, through
  `model_audits.audit_record`.

The first is a build-time artefact: a packaged worker is not shipped the
`qualification/` tree, so in the bundle only audit-backed runtimes appear. That
is the honest outcome - a runtime a scheduler sets a deadline from traces to a
stored measurement or it is absent - and it is why this module keeps no curated
copy of a number that would survive the record going missing.

Each parameter's ``default`` is the value **in force** for that model, not a
second copy of what the lane states: the operator's stored per-model default
(when one is set) over the lane's own. The value comes from
``components/model_defaults.resolved`` at the caller - the same call the request
path reads - so this module can never advertise a ``default`` the worker would
refuse. A parameter that has no value in force (neither the lane nor the
operator states one) publishes no ``default`` at all: a ``null`` for a boolean
is itself a value the worker would refuse.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

from . import catalog, long_form_policy, model_audits


SCHEMA = "studiohub.lane-contract.v1"
MODALITY = "voice"
OPERATION = "voice.tts"
# The route a scheduler posts one item to. `broker.MODALITY`'s own string.
DISPATCH_ROUTE = "POST /api/generate/txt2speech"
ARTIFACT = {
    "modality": "voice",
    "media_type": "audio/wav",
    "extension": "wav",
    "per_item": 1,
}
# Quoted verbatim from the spec / `broker`: done, error, cancelled are terminal;
# `uncertain` is NOT terminal - an item whose outcome the Hub cannot establish is
# held for an operator decision - and this lane can release one.
STATES = {
    "accepted": ["queued"],
    "active": ["running", "cancel_requested"],
    "terminal": ["done", "error", "cancelled"],
    "uncertain": "uncertain",
    "uncertain_is_terminal": False,
    "discard_supported": True,
}
CANCEL = {
    "supported": True,
    "request": "DELETE /api/hub/jobs/{batch_id}",
    "semantics": (
        "asks every studio holding an item of this batch to stop; an item already in flight may "
        "still land an artifact, so a cancelled item is not evidence that nothing was written"
    ),
}

# The API's own ceiling on `text` (`main.Txt2SpeechBody.text`). A model whose
# audit states a smaller ceiling publishes the audit's number instead.
API_TEXT_MAX_CHARACTERS = 40_000

# Shapes for the optional per-family fields the voice resolvers read. Each name
# is an API field; the type and any bound match what the worker enforces.
_PARAMETER_SHAPES: dict[str, dict[str, Any]] = {
    "voice_library_id": {
        "type": "string",
        "note": "id of a saved voice in the Voices library; required by cloning families",
    },
    "language": {
        "type": "string",
        "note": "language code; the model's own accepted codes are published as the enum",
    },
    "voice": {
        "type": "string",
        "note": "preset voice name; ignored when a reference voice is supplied",
    },
    "voice_design_prompt": {
        "type": "string",
        "note": "natural-language voice description",
    },
    "instruct": {
        "type": "string",
        "note": "delivery or style instruction",
    },
    "ref_transcript": {
        "type": "string",
        "note": "exact words spoken in the reference clip",
    },
    "cfg_value": {
        "type": "number",
        "minimum": 0.0,
        "maximum": 1.0,
        "default": 0.5,
        "note": "Chatterbox exaggeration dial; clamped to 0.0-1.0 by the worker",
    },
    "normalize_text": {
        "type": "boolean",
        "note": "force digit expansion to words for this request",
    },
    "max_output_duration_s": {
        "type": "number",
        "minimum": 0.0,
        # The API enforces `gt=0` (`main.Txt2SpeechBody.max_output_duration_s`), and an exclusive
        # bound is published as this flag on `minimum` - never as a bound number of its own, which
        # is how `0 <= True` once produced "must be greater than True".
        "minimum_exclusive": True,
        "maximum": 120.0,
        "note": "optional caller ceiling on the generated audio, in seconds",
    },
}


def _text_parameter(repo: str) -> dict[str, Any]:
    """`text`, bounded by the smaller of the model's audit and the API's cap."""
    audited = model_audits.input_limits(repo).get("text_max_characters")
    maximum = API_TEXT_MAX_CHARACTERS
    source = "main.Txt2SpeechBody.text max_length"
    if type(audited) is int and 0 < audited < maximum:
        maximum = audited
        source = f"{repo} audit: contract.input_limits.text_max_characters"
    return {
        "name": "text",
        "type": "string",
        "required": True,
        "max_characters": maximum,
        "bound_source": source,
    }


def _speed_parameter() -> dict[str, Any]:
    return {
        "name": "speed",
        "type": "number",
        "required": False,
        "minimum": long_form_policy.SPEED_MINIMUM,
        "maximum": long_form_policy.SPEED_MAXIMUM,
        "default": 1.0,
        "note": "clamped to this range by the dispatcher; delivered per the family's policy",
    }


def _section_parameter(repo: str) -> Optional[dict[str, Any]]:
    """The private section budget, bounded by the control the catalogue publishes."""
    control = catalog.section_size_control_for(repo)
    if not control:
        return None
    parameter = {
        "name": "section_max_characters",
        "type": "integer",
        "required": False,
        "minimum": int(control["minimum"]),
        "maximum": int(control["maximum"]),
        "default": int(control["runtime_default"]),
        "note": "omit for the measured Auto section size",
    }
    return parameter


def _language_parameter(model: catalog.ModelEntry) -> Optional[dict[str, Any]]:
    """`language`, only for a model whose worker actually reads one.

    A family that takes no language input - the Khmer VITS voices are Khmer by
    construction - must not advertise the key: a scheduler that sends it and sees
    it dropped has been lied to.
    """
    support = model.language_support or catalog.LanguageSupport(codes=model.languages)
    if support.input_selection == "none":
        return None
    parameter: dict[str, Any] = {
        "name": "language",
        "type": "string",
        "required": support.input_selection == "required",
        "bound_source": "model's own published language support",
    }
    if support.enumeration_status == "exact" and support.codes:
        parameter["enum"] = list(support.codes)
    return parameter


def _preset_speaker_parameter(model: catalog.ModelEntry) -> Optional[dict[str, Any]]:
    """`preset_speaker`, only for a model that offers a preset **and** publishes a roster.

    The two conditions are separate facts and both are required:

    * ``preset`` in `catalog.voice_modes` - the model has a ready-made voice form at all. A
      cloning-only tier of a roster family (Qwen3-TTS Base) has nothing to choose from, and
      advertising the key would offer a scheduler a field that lane ignores.
    * a roster on the family - one built-in speaker is not a choice (the Khmer VITS voices),
      and a family whose roster is deliberately unbuilt publishes none either (Pocket TTS
      4-bit, docs/tasks/ST-20.md). Publishing an enum for either would be inventing voices.

    The enum is the family's published roster's own ids - the same object
    `generation.availability` hands the app's picker - so a scheduler and a picker can never
    offer different voices. No ``default`` is stated here: where no value is in force the key
    is absent rather than a first-entry guess, and the value in force (the operator's stored
    per-model default) is filled in by ``_with_resolved_defaults`` like every other parameter.
    """
    roster = catalog.family_for(model).preset_roster
    if roster is None or "preset" not in catalog.voice_modes(model):
        return None
    return {
        "name": "preset_speaker",
        "type": "string",
        "required": False,
        "enum": [speaker["id"] for speaker in roster.speakers],
        "note": f"one of this family's published ready-made voices ({roster.source})",
    }


# The stored record of runs that actually happened, relative to the repository
# root. Found by walking up from this file, so the record can be read from a
# checkout or a development build and is simply absent inside a packaged bundle.
_RUNTIME_RECORD = (
    Path("qualification") / "evidence" / "voice-runtime-2026-09-25" / "measured-runtimes.json"
)


def _load_runtime_record(path: Path) -> dict[str, dict[str, Any]]:
    """One stored measurement record, keyed by repo. Empty when unreadable.

    An unreadable record is not an error: the packaged worker has no
    `qualification/` tree, and "no stored measurement" is exactly what the
    contract must then say. A record that exists but disagrees with its own
    probe files is caught by the test, not by a runtime guess here.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    measurements = payload.get("measurements")
    if not isinstance(measurements, list):
        return {}
    record: dict[str, dict[str, Any]] = {}
    for entry in measurements:
        if not isinstance(entry, dict):
            continue
        repo = entry.get("repo")
        rows = entry.get("measured")
        if not isinstance(repo, str) or not isinstance(rows, list) or not rows:
            continue
        prepared = [
            {
                key: row[key]
                for key in ("seconds", "scene", "source")
                if isinstance(row, dict) and key in row
            }
            for row in rows
            if isinstance(row, dict)
        ]
        if repo in record:
            # A second entry for one model is another run, not a replacement: appending keeps
            # every receipt published instead of letting the last one silently win.
            record[repo]["measured"].extend(prepared)
            continue
        record[repo] = {
            "measured_on": entry.get("measured_on"),
            "measured": prepared,
            "evidence": "measured",
        }
        if entry.get("note"):
            record[repo]["note"] = entry["note"]
    return record


@lru_cache(maxsize=1)
def stored_measurements() -> dict[str, dict[str, Any]]:
    """The stored runtime record, keyed by repo; {} when the record is not reachable."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _RUNTIME_RECORD
        if candidate.is_file():
            return _load_runtime_record(candidate)
    return {}


def _audited_runtime(repo: str) -> Optional[dict[str, Any]]:
    """A per-item runtime the model's own audit record measured, if any.

    The number is read from the record's canary evidence rather than restated
    here, and the scene and machine come from the same record. A record that
    inherits its evidence (the shipped 4-bit tier deriving from the 8-bit
    sibling's production run) says so, because a scheduler reading the number
    should know exactly which checkpoint produced it.
    """
    record = model_audits.audit_record(repo)
    if record is None:
        return None
    evidence = record.get("evidence") or {}
    canary = evidence.get("five_thousand_character_canary")
    if not isinstance(canary, dict):
        return None
    seconds = canary.get("runtime_seconds")
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    characters = canary.get("character_count") or 5_000
    sections = canary.get("section_max_characters")
    scene = f"{characters:,}-character script"
    if isinstance(sections, int):
        scene += f", {sections}-character sections"
    if canary.get("seed") is not None:
        scene += f", seed {canary['seed']}"
    runtime = evidence.get("qualified_runtime") or {}
    measured_on = str(runtime.get("hardware") or "") or "the audit's qualification machine"
    result: dict[str, Any] = {
        "measured_on": measured_on,
        "measured": [{"seconds": round(float(seconds), 3), "scene": scene}],
        "evidence": "measured",
    }
    derivation = record.get("derivation")
    if isinstance(derivation, dict):
        result["note"] = (
            "The measurement belongs to the audit this record derives from "
            f"({derivation.get('derived_from_record')}); this checkpoint was not "
            "independently timed."
        )
    return result


def runtime_seconds_for(repo: str) -> Optional[dict[str, Any]]:
    """The measured runtime for one shipped model, or None when nothing was measured.

    None means the key is absent from the contract. A guessed runtime is how a
    scheduler sets a deadline that fails on use, so the only two sources are a
    stored probe receipt and the model's own audit record.
    """
    return stored_measurements().get(repo) or _audited_runtime(repo)


def _with_resolved_defaults(parameters: list[dict[str, Any]],
                            defaults: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Each parameter's ``default`` replaced by the value in force for this model.

    ``defaults`` is what ``model_defaults.resolved`` answered for this model, so
    a scheduler reading the contract sends the number this worker would use when
    the request omits it. A parameter no value is in force for keeps no
    ``default`` rather than gaining a ``null``: ``null`` is not an accepted
    ``boolean`` or ``number``, and publishing it would be a default the lane
    refuses.
    """
    if not defaults:
        return parameters
    return [
        {**parameter, "default": defaults[parameter["name"]]}
        if parameter["name"] in defaults else parameter
        for parameter in parameters
    ]


def parameters_for(model: catalog.ModelEntry,
                   defaults: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The asked-for surface for one model: what the worker honours, with its bounds.

    Built from the model's own capabilities and its family's declared resolver
    fields, so a model that cannot clone never advertises a reference parameter,
    and a family that gains a control publishes it by declaring it on its
    registry entry.

    ``defaults`` is the resolved per-model default map for this model (see
    ``_with_resolved_defaults``); with none, every parameter keeps the default
    the lane itself states.
    """
    family = catalog.family_for(model)
    parameters = [_text_parameter(model.repo), _speed_parameter()]
    section = _section_parameter(model.repo)
    if section is not None:
        parameters.append(section)
    language = _language_parameter(model)
    if language is not None:
        parameters.append(language)
    preset_speaker = _preset_speaker_parameter(model)
    if preset_speaker is not None:
        parameters.append(preset_speaker)
    if "voice-cloning" in (model.capabilities or ()):
        parameters.append({"name": "voice_library_id", **_PARAMETER_SHAPES["voice_library_id"]})
    for name in family.lane_parameters:
        shape = _PARAMETER_SHAPES.get(name)
        if shape is None:
            # A family declared a request field this module has no shape for.
            # Publishing it unbounded would be a guess; refusing names the file.
            raise ValueError(
                f"family {family.id!r} declares lane parameter {name!r}, which has no shape in "
                "_PARAMETER_SHAPES; add it in "
                "components/voice/app/backend/lane_contract.py"
            )
        if any(parameter["name"] == name for parameter in parameters):
            continue
        parameters.append({"name": name, **shape})
    return _with_resolved_defaults(parameters, defaults)


def for_model(model: catalog.ModelEntry,
              defaults: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The complete `lane_contract` for one catalog row.

    ``defaults`` is the resolved per-model default map, so the contract publishes
    the values this worker will actually use for an omitted parameter.
    """
    contract: dict[str, Any] = {
        "schema": SCHEMA,
        "modality": MODALITY,
        "operation": OPERATION,
        "dispatch": DISPATCH_ROUTE,
        "parameters": parameters_for(model, defaults),
        "artifact": dict(ARTIFACT),
        "states": dict(STATES),
        "cancel": dict(CANCEL),
    }
    runtime = runtime_seconds_for(model.repo)
    if runtime is not None:
        contract["runtime_seconds"] = runtime
    return contract
