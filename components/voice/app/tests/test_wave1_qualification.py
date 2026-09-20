from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "wave1_qualification.py"

# The Wave 1 matrix planner is an upstream Voice qualification artefact. It was
# not adopted into this repository (AGENTS.md adopts source selectively at exact
# commits), and `components/voice/tools/` does not exist in any commit. Importing
# it at module scope therefore aborted collection for the entire suite with a
# FileNotFoundError. Skip the module instead, and name the artefact so nobody has
# to guess what is missing. Do not add the tool just to make this run: it would be
# unqualified code, and the matrix it plans is a fleet measurement, not a test.
if not TOOL.is_file():
    pytest.skip(
        "qualification artefact components/voice/tools/wave1_qualification.py is not in "
        "this repository (upstream Voice qualification tool, not adopted); the Wave 1 "
        "matrix cannot be planned or validated here.",
        allow_module_level=True,
    )

SPEC = importlib.util.spec_from_file_location("wave1", TOOL)
wave1 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(wave1)


def test_plan_creates_exact_offline_matrix(tmp_path: Path) -> None:
    run_dir = tmp_path / "wave1"
    assert wave1.main(["plan", "--run-id", "wave1-test", "--run-dir", str(run_dir)]) == 0
    assert wave1.validate(run_dir) == []
    custom = wave1.draft(wave1.QWEN_CUSTOM, "wave1-test")
    chatterbox = wave1.draft(wave1.CHATTERBOX, "wave1-test")
    assert len(custom["required_cases"]) == 11
    assert {case["preset_speaker"] for case in custom["required_cases"] if "preset_speaker" in case} == set(wave1.QWEN_VOICES)
    assert len(chatterbox["required_cases"]) == 25
    assert {case["language"] for case in chatterbox["required_cases"] if case["kind"] == "short_form"} == set(wave1.CHATTERBOX_LANGUAGES)


def test_validation_rejects_passed_audits_and_unknown_contract_values(tmp_path: Path) -> None:
    run_dir = tmp_path / "wave1"
    assert wave1.main(["plan", "--run-id", "wave1-test", "--run-dir", str(run_dir)]) == 0
    (run_dir / "forbidden.audit.json").write_text('{"audit_status":"passed"}', encoding="utf-8")
    assert any("audit artifact forbidden" in error for error in wave1.validate(run_dir))
    (run_dir / "forbidden.audit.json").unlink()
    path = run_dir / wave1._safe_name(wave1.CHATTERBOX)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["required_cases"][0]["language"] = "xx"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert any("draft contract mismatch" in error for error in wave1.validate(run_dir))


def test_pending_status_is_allowed_but_unknown_machine_tier_is_not(tmp_path: Path) -> None:
    run_dir = tmp_path / "wave1"
    assert wave1.main(["plan", "--run-id", "wave1-test", "--run-dir", str(run_dir)]) == 0
    path = run_dir / wave1._safe_name(wave1.QWEN_BASE)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["status"] = "pending_review"
    raw["required_cases"][0]["status"] = "pending_evidence"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert wave1.validate(run_dir) == []
    raw["machine_tiers_gb"] = [8, 16, 32]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert any("draft contract mismatch" in error for error in wave1.validate(run_dir))
