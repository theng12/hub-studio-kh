from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


VOICE_APP = Path(__file__).resolve().parents[1] / "components" / "voice" / "app"
if str(VOICE_APP) not in sys.path:
    sys.path.insert(0, str(VOICE_APP))


def _reload_backend(monkeypatch, **env):
    for key in ("STUDIOHUB_DATA_DIR", "STUDIOHUB_MODELS_DIR", "STUDIOHUB_TOOLS_DIR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    import backend.paths as paths

    importlib.reload(paths)
    return paths


def test_backend_paths_use_explicit_external_state_roots(monkeypatch, tmp_path):
    data = tmp_path / "state with spaces"
    models = tmp_path / "models"
    tools = tmp_path / "tools"
    paths = _reload_backend(
        monkeypatch,
        STUDIOHUB_DATA_DIR=data,
        STUDIOHUB_MODELS_DIR=models,
        STUDIOHUB_TOOLS_DIR=tools,
    )

    assert paths.data_dir() == (data / "voice").resolve()
    assert paths.models_dir() == models.resolve()
    assert paths.tools_dir() == tools.resolve()
    assert paths.data_path("settings.json") == (data / "voice").resolve() / "settings.json"
    assert paths.models_path("hub") == models.resolve() / "hub"


def test_settings_and_cache_follow_external_roots_without_legacy_source_reads(
    monkeypatch, tmp_path
):
    data = tmp_path / "state"
    models = tmp_path / "models"
    paths = _reload_backend(monkeypatch, STUDIOHUB_DATA_DIR=data, STUDIOHUB_MODELS_DIR=models)

    import backend.cache as cache
    import backend.settings as settings

    importlib.reload(cache)
    importlib.reload(settings)
    assert settings._PATH == (data / "voice").resolve() / "settings.json"
    assert cache.hf_home() == models.resolve()

    settings.set_hf_token("hf_test_token")
    assert (data / "voice" / "settings.json").is_file()
    assert not (VOICE_APP / "backend" / "settings.json").exists()
    assert paths.models_path("hub").parent == models.resolve()


def test_media_tools_use_explicit_tools_root(monkeypatch, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    ffmpeg = tools / "ffmpeg"
    ffmpeg.write_text("#!/bin/sh\n")
    ffmpeg.chmod(0o755)
    _reload_backend(monkeypatch, STUDIOHUB_TOOLS_DIR=tools)

    import backend.media_tools as media_tools

    importlib.reload(media_tools)
    assert media_tools.find_executable("ffmpeg") == ffmpeg.resolve()


def test_standalone_updater_is_truthfully_unavailable(tmp_path):
    from backend.auto_update import UpdateError
    from backend.auto_update_config import create_updater

    updater = create_updater(readiness=lambda: ["voice generation is queued or running"])
    status = updater.public_status()
    assert status["state"] == "unavailable"
    assert status["capabilities"]["managed_exact_commit"] is False
    assert status["scheduler"] == {"installed": False, "supported": False, "label": ""}
    assert updater.readiness_status() == {
        "idle": False,
        "reasons": ["voice generation is queued or running"],
    }
    assert updater.apply_scheduler_if_idle() is True
    try:
        updater.trigger_update()
    except UpdateError as exc:
        assert "unavailable" in str(exc).lower()
    else:
        raise AssertionError("standalone updater accepted an update")


def test_standalone_generation_does_not_claim_pinokio_service_supervision():
    from backend.generation import GenerationManager

    assert GenerationManager._service_installed() is False


def test_release_identity_uses_only_a_valid_standalone_manifest(monkeypatch, tmp_path):
    import backend.main as imported_main

    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema": "studiohub.standalone-release",
            "schema_version": 1,
            "version": "0.1.0",
            "implementation_commit": "a" * 40,
            "development_build": False,
            "payload_sha256": "b" * 64,
            "runtime_sha256": "c" * 64,
            "components": {
                "voice": {
                    "upstream_commit": "d" * 40,
                    "upstream_version": "2.7.2",
                    "source_sha256": "e" * 64,
                }
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("STUDIOHUB_RELEASE_MANIFEST", str(manifest))
    main = importlib.reload(imported_main)

    assert main._read_app_version() == "0.1.0"
    assert main._read_app_commit() == "a" * 40
    assert main._release_identity()["version"] == "0.1.0"
    assert main._release_identity()["components"]["voice"]["upstream_version"] == "2.7.2"
    first_identity = main._release_identity()
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("0.1.0", "0.2.0"), encoding="utf-8")
    assert main._release_identity() == first_identity
    assert main.health()["release"] == first_identity


def test_connectivity_uses_studiohub_bind_values_and_ignores_pinokio_sharing(
    monkeypatch,
):
    from types import SimpleNamespace

    import backend.main as main

    monkeypatch.setenv("STUDIOHUB_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("STUDIOHUB_BIND_PORT", "43123")
    monkeypatch.setenv("PINOKIO_SHARE_LOCAL", "true")
    monkeypatch.setenv("PINOKIO_SHARE_PASSCODE", "secret")

    assert main._detect_bind_host() == "127.0.0.1"
    assert main._detect_bind_port() == 43123
    main._BIND_HOST = main._detect_bind_host()
    main._BIND_PORT = main._detect_bind_port()
    payload = main.connectivity(SimpleNamespace(
        url=SimpleNamespace(port=43123, scheme="http"),
        base_url="http://127.0.0.1:43123/",
    ))
    assert payload["bind_host"] == "127.0.0.1"
    assert payload["bind_port"] == 43123
    assert payload["share_local_enabled"] is False
    assert payload["share_passcode_set"] is False
    assert payload["pinokio_ui_port"] is None


def test_standalone_audit_candidate_is_not_exposed_but_limits_remain(monkeypatch):
    import backend.model_audits as model_audits

    monkeypatch.setenv("STUDIOHUB_RELEASE_MANIFEST", "/tmp/standalone-release-manifest.json")
    record = {
        "contract": {"input_limits": {"text_max_characters": 123}},
        "subject": {"model_id": "demo/model"},
        "genstudio_candidate": {
            "schema": "studio.model-audit",
            "schema_version": 1,
            "audit_status": "passed",
            "candidate_for_genstudio": True,
            "runtime_revision": "a" * 40,
            "contract_hash": "sha256:" + "b" * 64,
            "input_limits": {"text_max_characters": 123},
            "approved_operations": [],
            "adapter": {},
            "controls": {},
            "output_limits": {},
            "hardware": {},
        },
    }
    monkeypatch.setattr(model_audits, "audit_record", lambda _model_id: record)
    assert model_audits.candidate_summary("demo/model") is None
    assert model_audits.input_limits("demo/model") == {"text_max_characters": 123}
