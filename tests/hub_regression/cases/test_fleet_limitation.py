"""Machine-readable limitation codes beside the human activity sentence.

``fleet_activity.machines[]`` already carried one sentence for an unavailable,
partial or clock-skewed reporter.  Operators and the stats page also need the
machine-readable cause, when the Hub first observed it, and which fields the
machine cannot currently evidence - without turning absence into "all fine".
The human sentence is unchanged and additive; only the code is new.
"""
from __future__ import annotations

import pytest

from components.hub.backend import activity
from components.hub.backend.monitor import StudioMonitor


NOW = 1_700_000_000.0


def _studio(studio_id: str, modality: str, machine: str = "node-a") -> dict:
    return {"id": studio_id, "modality": modality, "machine": machine}


def _snapshot(registry, statuses, *, now: float = NOW, since_s=None) -> dict:
    return activity.fleet_snapshot(registry, statuses, {}, since_s=since_s, now=now)


def _row(registry, statuses, machine: str = "node-a", **kwargs) -> dict:
    rows = _snapshot(registry, statuses, **kwargs)["machines"]
    return next(row for row in rows if row["machine"] == machine)


def test_fully_reporting_machine_keeps_limitation_absent(reset):
    """Absence stays null: a machine that reports must not carry a code."""
    registry = [_studio("image-a", "image"), _studio("voice-a", "voice")]
    statuses = {
        "image-a": {"status": "up", "activity_support": "available",
                    "activity_support_since": NOW - 10},
        "voice-a": {"status": "up", "activity_support": "available",
                    "activity_support_since": NOW - 10},
    }

    row = _row(registry, statuses)

    assert row["limitation"] is None
    assert row["limitation_code"] is None
    assert row["limitation_reason"] is None
    assert row["limitation_since"] is None
    assert row["limitation_affects"] is None
    assert row["next_attempt_at"] is None
    assert row["eta_seconds"] is None


def test_partial_report_names_the_missing_fields_and_since(reset):
    registry = [_studio("image-a", "image"), _studio("voice-a", "voice")]
    statuses = {
        "image-a": {"status": "up", "activity_support": "available",
                    "activity_support_since": NOW - 10},
        "voice-a": {"status": "up", "activity_support": "unavailable",
                    "activity_support_since": NOW - 300},
    }

    row = _row(registry, statuses)

    assert row["limitation"] == "Direct activity partially unavailable"
    assert row["limitation_code"] == "partial_read"
    assert row["limitation_reason"]
    assert row["limitation_affects"]
    assert {"state", "completed", "median_runtime"} <= set(row["limitation_affects"])
    assert all(isinstance(name, str) and name for name in row["limitation_affects"])
    # The earliest time a currently non-available support was observed, taken
    # from the monitor's stamp, not a fabricated number.
    assert row["limitation_since"] == pytest.approx(NOW - 300)


def test_all_reporters_unavailable_is_reporter_unavailable(reset):
    registry = [_studio("image-a", "image")]
    statuses = {"image-a": {"status": "up", "activity_support": "unavailable",
                            "activity_support_since": NOW - 45}}

    row = _row(registry, statuses)

    assert row["limitation"] == "Direct activity unavailable"
    assert row["limitation_code"] == "reporter_unavailable"
    assert row["limitation_since"] == pytest.approx(NOW - 45)


def test_limitation_since_is_null_when_the_monitor_never_stamped_it(reset):
    """An unstamped support must report unknown rather than invent a time."""
    registry = [_studio("image-a", "image")]
    statuses = {"image-a": {"status": "up", "activity_support": "unavailable"}}

    row = _row(registry, statuses)

    assert row["limitation_code"] == "reporter_unavailable"
    assert row["limitation_since"] is None


def test_every_sentence_case_uses_the_closed_vocabulary(reset):
    """Each existing sentence keeps its wording and gains a closed-set code."""
    registry = [
        _studio("complete-image", "image", "complete"),
        _studio("all-down-image", "image", "all-down"),
        _studio("error-image", "image", "error-only"),
        _studio("skew-image", "image", "skew"),
        _studio("pending-image", "image", "pending"),
        _studio("partial-image", "image", "partial"),
        _studio("partial-voice", "voice", "partial"),
        _studio("mixed-error-image", "image", "mixed-error"),
        _studio("mixed-error-voice", "voice", "mixed-error"),
    ]
    statuses = {
        "complete-image": {"status": "up", "activity_support": "available"},
        "all-down-image": {"status": "up", "activity_support": "unavailable"},
        "error-image": {"status": "up", "activity_support": "error"},
        "skew-image": {"status": "up", "activity_support": "skew"},
        "pending-image": {"status": "up"},
        "partial-image": {"status": "up", "activity_support": "available"},
        "partial-voice": {"status": "up", "activity_support": "unavailable"},
        "mixed-error-image": {"status": "up", "activity_support": "available"},
        "mixed-error-voice": {"status": "up", "activity_support": "error"},
    }
    expected = {
        "complete": None,
        "all-down": "Direct activity unavailable",
        "error-only": "Activity reporter temporarily unavailable",
        "skew": "Activity reporter clock skew exceeds policy",
        "pending": "Activity evidence pending",
        "partial": "Direct activity partially unavailable",
        "mixed-error": "Activity reporter temporarily unavailable",
    }

    rows = {row["machine"]: row for row in _snapshot(registry, statuses)["machines"]}

    assert set(rows) == set(expected)
    for machine, sentence in expected.items():
        row = rows[machine]
        assert row["limitation"] == sentence, machine
        assert (row["limitation_code"] is None) == (sentence is None), machine
        if sentence is None:
            assert row["limitation_affects"] is None
        else:
            assert row["limitation_code"] in activity.LIMITATION_CODES, machine
            assert row["limitation_reason"], machine


def test_limitation_code_maps_every_support_shape_into_the_closed_set():
    """Including support shapes a machine row cannot currently produce."""
    cases = [
        {None}, set(), {"available"}, {"unavailable"}, {"error"}, {"skew"},
        {"available", "unavailable"}, {"available", "error"},
        {"available", "skew"}, {"unavailable", None}, {"error", None},
        {"skew", None},
    ]
    for support in cases:
        code = activity._limitation_code(support)
        assert code is None or code in activity.LIMITATION_CODES, support
    assert activity._limitation_code({"available"}) is None


def test_support_since_advances_only_on_a_real_transition():
    status: dict = {}
    StudioMonitor._record_activity_support(status, "unavailable", 100.0)
    assert status["activity_support"] == "unavailable"
    assert status["activity_support_since"] == 100.0

    StudioMonitor._record_activity_support(status, "unavailable", 200.0)
    assert status["activity_support_since"] == 100.0

    StudioMonitor._record_activity_support(status, "available", 300.0)
    assert status["activity_support"] == "available"
    assert status["activity_support_since"] == 300.0
