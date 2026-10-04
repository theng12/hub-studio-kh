from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import catalog, generation, main, qwen_quality
from backend.main import FLEET_TOKEN


# The kept Qwen cloning tiers. The 1.7B 8-bit tier and the CustomVoice/VoiceDesign
# rows were cut with the menu, so these tests target the 4-bit tier that replaced
# the 8-bit as the quality-cloning option.
QWEN_17B_BASE = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"
QWEN_06B_BASE = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
VOXCPM_4BIT = "mlx-community/VoxCPM2-4bit"
CHATTERBOX_8BIT = "mlx-community/chatterbox-8bit"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def queued_txt2speech_params(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    captured: list[dict] = []

    monkeypatch.setattr(main.gen_manager, "is_available", lambda: True)
    monkeypatch.setattr(main.cache, "cache_state", lambda _repo: "cached")
    monkeypatch.setattr(
        main.gen_manager,
        "start_txt2speech",
        lambda params, **_kwargs: captured.append(params) or SimpleNamespace(serialize=lambda: {}),
    )
    return captured


def _client() -> TestClient:
    return TestClient(main.app, headers={"X-Studio-Token": FLEET_TOKEN})


@pytest.mark.parametrize("requested", [230, 280, 400])
def test_txt2speech_endpoint_canonicalizes_section_budget_before_queueing(
    queued_txt2speech_params: list[dict], requested: int,
) -> None:
    """An explicit override is canonicalized before queueing.

    Auto omits the field entirely, so the endpoint leaves `_resolved` unset and the
    runtime uses the published policy default; that path is covered by
    ``test_omitted_control_for_a_non_qwen_model_keeps_the_normal_queue_path``.
    """
    payload = {"repo": QWEN_17B_BASE, "text": "A bounded request.",
               "section_max_characters": requested}

    response = _client().post("/api/generate/txt2speech", json=payload)

    assert response.status_code == 200
    assert len(queued_txt2speech_params) == 1
    assert queued_txt2speech_params[0]["_resolved_section_max_characters"] == requested
    assert "section_max_characters" not in queued_txt2speech_params[0]


@pytest.mark.parametrize(
    ("repo", "requested", "code"),
    [
        (QWEN_06B_BASE, 5_000, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (QWEN_17B_BASE, 39, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (QWEN_17B_BASE, 3_001, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (VOXCPM_4BIT, 5_000, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
    ],
)
def test_txt2speech_endpoint_rejects_out_of_range_section_budget(
    queued_txt2speech_params: list[dict], repo: str, requested: int, code: str,
) -> None:
    """Every sectioning model now has a published control, so the bound is its own.

    This used to assert ``SECTION_MAX_CHARACTERS_UNSUPPORTED`` for the 0.6B Base
    build, because no control existed for it. Every family now publishes one, so
    the surviving guarantee is that an override outside the published range is
    refused while an override inside it is honoured (see the next test).
    """
    response = _client().post("/api/generate/txt2speech", json={
        "repo": repo,
        "text": "A rejected request.",
        "section_max_characters": requested,
    })

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert queued_txt2speech_params == []


@pytest.mark.parametrize(
    ("repo", "requested"),
    [
        (QWEN_06B_BASE, 280),
        (QWEN_17B_BASE, 400),
        (VOXCPM_4BIT, 400),
        (CHATTERBOX_8BIT, 500),
    ],
)
def test_txt2speech_endpoint_accepts_an_override_inside_the_published_control(
    queued_txt2speech_params: list[dict], repo: str, requested: int,
) -> None:
    response = _client().post("/api/generate/txt2speech", json={
        "repo": repo,
        "text": "A bounded request.",
        "section_max_characters": requested,
    })

    assert response.status_code == 200
    assert len(queued_txt2speech_params) == 1
    assert queued_txt2speech_params[0]["_resolved_section_max_characters"] == requested
    assert "section_max_characters" not in queued_txt2speech_params[0]


@pytest.mark.parametrize(
    ("repo", "requested", "code"),
    [
        (QWEN_06B_BASE, 5_000, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (QWEN_17B_BASE, 39, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (QWEN_17B_BASE, 3_001, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (VOXCPM_4BIT, 5_000, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
    ],
)
def test_reference_endpoint_rejects_section_budget_before_preparing_audio(
    monkeypatch: pytest.MonkeyPatch,
    queued_txt2speech_params: list[dict],
    repo: str,
    requested: int,
    code: str,
) -> None:
    prepared = False

    def prepare(**_kwargs):
        nonlocal prepared
        prepared = True
        return {
            "path": "/private/reference.wav",
            "derived_sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "preparation_revision": "test-v1",
            "duration_seconds": 3.0,
            "transcript": "Prepared reference.",
        }

    monkeypatch.setattr(main.reference_audio, "prepare", prepare)
    response = _client().post(
        "/api/generate/txt2speech/reference",
        data={"request_json": json.dumps({
            "repo": repo,
            "text": "A rejected reference request.",
            "section_max_characters": requested,
        })},
        files={"audio": ("reference.wav", b"RIFF", "audio/wav")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert prepared is False
    assert queued_txt2speech_params == []


def test_reference_endpoint_canonicalizes_section_budget_before_queueing(
    monkeypatch: pytest.MonkeyPatch,
    queued_txt2speech_params: list[dict],
) -> None:
    monkeypatch.setattr(main.reference_audio, "prepare", lambda **_kwargs: {
        "path": "/private/reference.wav",
        "derived_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "preparation_revision": "test-v1",
        "duration_seconds": 3.0,
        "transcript": "Prepared reference.",
    })
    response = _client().post(
        "/api/generate/txt2speech/reference",
        data={"request_json": json.dumps({
            "repo": QWEN_17B_BASE,
            "text": "A bounded reference request.",
            "section_max_characters": 280,
        })},
        files={"audio": ("reference.wav", b"RIFF", "audio/wav")},
    )

    assert response.status_code == 200
    assert queued_txt2speech_params[0]["_resolved_section_max_characters"] == 280
    assert "section_max_characters" not in queued_txt2speech_params[0]


class _InertThread:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def _inert_generation_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> generation.GenerationManager:
    output_dir = tmp_path / "output"
    monkeypatch.setattr(generation, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(generation, "HISTORY_FILE", output_dir / ".history.json")
    manager = object.__new__(generation.GenerationManager)
    manager._lock = threading.RLock()
    manager._jobs = {}
    return manager


def test_internal_queueing_canonicalizes_a_qwen_section_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _inert_generation_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(generation.threading, "Thread", _InertThread)

    job = manager.start_txt2speech({
        "repo": QWEN_17B_BASE,
        "text": "An internal bounded request.",
        "section_max_characters": 280,
    })

    assert job.params["_resolved_section_max_characters"] == 280
    assert "section_max_characters" not in job.params


def test_internal_queueing_rejects_an_out_of_range_qwen_section_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _inert_generation_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(generation.threading, "Thread", _InertThread)

    with pytest.raises(catalog.SectionSizeControlError) as error:
        manager.start_txt2speech({
            "repo": QWEN_17B_BASE,
            "text": "An invalid internal request.",
            "section_max_characters": 3_001,
        })

    assert error.value.code == "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"
    assert manager._jobs == {}


def test_internal_queueing_keeps_qwen_06b_private_fallback_for_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _inert_generation_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(generation.threading, "Thread", _InertThread)
    text = " ".join(
        f"Fallback validation sentence {index} remains independently bounded."
        for index in range(1, 30)
    )

    job = manager.start_txt2speech({"repo": QWEN_06B_BASE, "text": text})

    # The audited 288 is capped at the owner's uniform 250 (2026-10-03), joined at 1.0 s (2026-10-04).
    assert job.params["_resolved_section_max_characters"] == 250
    assert "section_max_characters" not in job.params
    chunks = generation._internal_mlx_text_chunks(
        "qwen3-tts", QWEN_06B_BASE, text, max_chars_override=250,
    )
    expected = round(
        sum(qwen_quality.automatic_duration_limit(chunk, 1.0) for chunk in chunks)
        + (len(chunks) - 1) * 1.0,
        3,
    )
    assert generation.GenerationManager._qwen_job_duration_limit(job) == expected


def test_omitted_control_for_a_non_qwen_model_keeps_the_normal_queue_path(
    queued_txt2speech_params: list[dict],
) -> None:
    response = _client().post("/api/generate/txt2speech", json={
        "repo": VOXCPM_4BIT,
        "text": "A normal non-Qwen request.",
    })

    assert response.status_code == 200
    assert len(queued_txt2speech_params) == 1
    assert queued_txt2speech_params[0]["section_max_characters"] is None
    assert "_resolved_section_max_characters" not in queued_txt2speech_params[0]


def test_omitted_control_for_a_non_qwen_model_prepares_reference_audio(
    monkeypatch: pytest.MonkeyPatch,
    queued_txt2speech_params: list[dict],
) -> None:
    prepared = False

    def prepare(**_kwargs):
        nonlocal prepared
        prepared = True
        return {
            "path": "/private/reference.wav",
            "derived_sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "preparation_revision": "test-v1",
            "duration_seconds": 3.0,
            "transcript": "Prepared reference.",
        }

    monkeypatch.setattr(main.reference_audio, "prepare", prepare)
    response = _client().post(
        "/api/generate/txt2speech/reference",
        data={"request_json": json.dumps({
            "repo": VOXCPM_4BIT,
            "text": "A normal reference request.",
        })},
        files={"audio": ("reference.wav", b"RIFF", "audio/wav")},
    )

    assert response.status_code == 200
    assert prepared is True
    assert queued_txt2speech_params[0]["section_max_characters"] is None
    assert "_resolved_section_max_characters" not in queued_txt2speech_params[0]


@pytest.mark.parametrize(
    ("repo", "requested", "outcome"),
    [
        # Every sectioning model publishes a control now, and the control's range is
        # the guarantee: inside it the caller's number is honoured, past it the
        # request is refused. No catalogued family has an engine input cliff any more
        # (Bark and Orpheus were cut), so out-of-range means past the derived 3000.
        (VOXCPM_4BIT, 5_000, "rejected"),
        (CHATTERBOX_8BIT, 500, "accepted"),
    ],
)
def test_section_override_is_bounded_by_each_models_published_control(
    queued_txt2speech_params: list[dict], repo: str, requested: int, outcome: str,
) -> None:
    """The old name claimed non-Qwen overrides stay fail-closed; they no longer do."""
    response = _client().post("/api/generate/txt2speech", json={
        "repo": repo,
        "text": "A bounded override.",
        "section_max_characters": requested,
    })

    if outcome == "rejected":
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"
        assert queued_txt2speech_params == []
    else:
        assert response.status_code == 200
        assert queued_txt2speech_params[0]["_resolved_section_max_characters"] == requested


def test_internal_queueing_without_a_non_qwen_override_stays_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_history = ROOT / "app" / "output" / ".history.json"
    repository_history_before = (
        repository_history.read_bytes() if repository_history.exists() else None
    )
    manager = _inert_generation_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(generation.threading, "Thread", _InertThread)

    job = manager.start_txt2speech({
        "repo": VOXCPM_4BIT,
        "text": "An ordinary internal non-Qwen request.",
    })

    assert "section_max_characters" not in job.params
    assert "_resolved_section_max_characters" not in job.params
    assert generation.HISTORY_FILE == tmp_path / "output" / ".history.json"
    saved = json.loads(generation.HISTORY_FILE.read_text())
    assert [row["job_id"] for row in saved["jobs"]] == [job.job_id]
    assert (
        repository_history.read_bytes() if repository_history.exists() else None
    ) == repository_history_before


def test_catalog_publishes_a_derived_section_control_for_every_sectioning_model() -> None:
    """The hand-declared Qwen 8-bit control went with its model; every kept model derives one."""
    for entry in catalog.CATALOG:
        published = catalog.serialize_model(entry)["long_form_delivery"]
        control = published["section_size_control"]
        assert control == catalog.section_size_control_for(entry.repo), entry.repo
        assert control == {
            "minimum": 40,
            "maximum": published["hard_cap_characters"] or 3000,
            "step": 10,
            "default_custom": published["section_max_characters"],
            "runtime_default": published["section_max_characters"],
            "source": published["source"],
        }, entry.repo
    qwen4 = catalog.serialize_model(catalog.get_model(QWEN_17B_BASE))["long_form_delivery"]
    # The shipped 1.7B Base is the 4-bit tier; its record derives from the
    # measured production audit (Auto 280, maximum 400), and the owner's uniform
    # 250 (2026-10-03) caps the published budget; a caller may still ask more.
    assert qwen4["section_max_characters"] == 250
    assert qwen4["source"] == "model_audit"
    assert qwen4["section_size_control"]["maximum"] == 3000


def test_frontend_custom_400_is_valid_and_auto_omits_the_override() -> None:
    script = ROOT / "app" / "frontend" / "app.js"
    probe = r"""
const fs = require('fs');
const vm = require('vm');
const submitted = [];
global.fetch = async (_url, options) => {
  submitted.push(JSON.parse(options.body));
  return { ok: true, json: async () => ({ job: {} }) };
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));

(async () => {
  const app = studio();
  const repo = 'mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit';
  app.models = [{
    repo,
    family: 'qwen3-tts',
    cache: { state: 'cached' },
    long_form_delivery: {
      section_size_control: {
        minimum: 40,
        maximum: 3000,
        step: 10,
        default_custom: 400,
        runtime_default: 400,
      },
    },
  }];
  app.gen.available = true;
  app.gen.repo = repo;
  app.gen.text = 'A bounded Qwen request.';
  app.gen.voice_library_id = 'saved-voice';
  app._requestNotificationPermission = () => {};
  app.pushToast = () => {};

  app.gen.section_size_mode = 'auto';
  app.gen.section_max_characters = 400;
  await app.submitGenerate();
  await new Promise(resolve => setTimeout(resolve, 310));

  app.gen.section_size_mode = 'custom';
  app.gen.section_max_characters = 400;
  const custom400IsValid = app.sectionSizeIsValid;
  await app.submitGenerate();

  process.stdout.write(JSON.stringify({ submitted, custom400IsValid }));
})();
"""
    result = subprocess.run(
        ["node", "-e", probe, str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    observed = json.loads(result.stdout)

    assert observed["custom400IsValid"] is True
    assert "section_max_characters" not in observed["submitted"][0]
    assert observed["submitted"][1]["section_max_characters"] == 400


def test_readme_documents_the_section_size_control_contract() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())

    assert "### Section size" in readme
    assert "whole numbers from 40 through 3000" in readme
    # One pause rule for every model (owner 2026-10-03; it was 300/600/180 ms on some families;
    # 1.0 / 1.5 s since 2026-10-04).
    assert "1.0 s after a sentence, 1.5 s at a paragraph break" in readme
    assert "retries with a 230-character section" in readme
    assert "omit `section_max_characters` for Auto" in readme
    assert "send an integer `section_max_characters` for Custom" in readme


@pytest.mark.parametrize(
    ("requested", "expected", "source"),
    [
        # Auto is the owner's uniform 250 (2026-10-03): the measured production
        # Auto default (280) the shipped 4-bit tier reaches through its derived
        # record is capped, and a caller may still choose up to the maximum.
        (None, 250, "audit"),
        (230, 230, "caller_override"),
        (280, 280, "caller_override"),
        (400, 400, "caller_override"),
    ],
)
def test_qwen_section_control_resolves_auto_and_custom(
    requested: int | None, expected: int, source: str,
) -> None:
    result = catalog.resolve_section_budget("qwen3-tts", QWEN_17B_BASE, requested)
    assert result["section_max_characters"] == expected
    assert result["source"] == source


@pytest.mark.parametrize(
    ("requested", "code"),
    [
        (39, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (3_001, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"),
        (True, "SECTION_MAX_CHARACTERS_INVALID"),
        (280.0, "SECTION_MAX_CHARACTERS_INVALID"),
        ("280", "SECTION_MAX_CHARACTERS_INVALID"),
        (float("inf"), "SECTION_MAX_CHARACTERS_INVALID"),
    ],
)
def test_resolve_section_budget_rejects_invalid_or_out_of_range_values(
    requested: object, code: str
) -> None:
    with pytest.raises(catalog.SectionSizeControlError) as error:
        catalog.resolve_section_budget("qwen3-tts", QWEN_17B_BASE, requested)
    assert error.value.code == code


def test_resolve_section_budget_rejects_an_override_beyond_the_published_control() -> None:
    """The old name claimed 0.6B Base had no capability; it now publishes one."""
    with pytest.raises(catalog.SectionSizeControlError) as error:
        catalog.resolve_section_budget("qwen3-tts", QWEN_06B_BASE, 5_000)
    assert error.value.code == "SECTION_MAX_CHARACTERS_OUT_OF_RANGE"
    # Inside the published range the same model accepts the override.
    resolved = catalog.resolve_section_budget("qwen3-tts", QWEN_06B_BASE, 280)
    assert resolved["section_max_characters"] == 280
    assert resolved["source"] == "caller_override"


def test_resolve_section_budget_rejects_capability_from_another_model() -> None:
    # Every model now renders the same 250, so the other model's control differs by its source.
    capability = catalog.section_size_control_for(VOXCPM_4BIT)

    with pytest.raises(catalog.SectionSizeControlError) as error:
        catalog.resolve_section_budget(
            "qwen3-tts", QWEN_06B_BASE, 280, capability=capability
        )

    assert error.value.code == "SECTION_MAX_CHARACTERS_UNSUPPORTED"


def test_resolve_section_budget_rejects_a_stale_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = catalog.section_size_control_for(QWEN_17B_BASE)
    original_policy_for = catalog.long_form_policy.policy_for

    def mismatched_policy_for(
        family: str,
        repo: str,
        *,
        audited_section_max_characters: object = None,
        audited_default_section_max_characters: object = None,
    ) -> dict | None:
        policy = original_policy_for(
            family,
            repo,
            audited_section_max_characters=audited_section_max_characters,
            audited_default_section_max_characters=audited_default_section_max_characters,
        )
        if repo == QWEN_17B_BASE and policy is not None:
            return {**policy, "section_max_characters": 399}
        return policy

    monkeypatch.setattr(catalog.long_form_policy, "policy_for", mismatched_policy_for)

    with pytest.raises(catalog.SectionSizeControlError) as error:
        catalog.resolve_section_budget(
            "qwen3-tts", QWEN_17B_BASE, 280, capability=capability
        )

    assert error.value.code == "SECTION_MAX_CHARACTERS_UNSUPPORTED"
