import os
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient

from backend import storage_policy
from backend.main import APP_VERSION, FLEET_TOKEN, app


class Job:
    def __init__(self, state):
        self.state = state


class Manager:
    def __init__(self, output_dir, jobs=None):
        self.output_dir = output_dir
        self.jobs = jobs or {}

    def get(self, job_id):
        return self.jobs.get(job_id)

    def delete_job(self, job_id):
        job = self.jobs.pop(job_id, None)
        if not job:
            return False
        for suffix in storage_policy.OUTPUT_SUFFIXES:
            (self.output_dir / f"{job_id}{suffix}").unlink(missing_ok=True)
        return True


def _file(root, name, size, age_days=0):
    path = root / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_age_and_cap_only_remove_generated_audio(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    voices_dir = tmp_path / "voices"
    output_dir.mkdir()
    voices_dir.mkdir()
    monkeypatch.setattr(storage_policy, "SETTINGS_FILE", tmp_path / "config" / "policy.json")
    manager = Manager(output_dir, {"old": Job("done"), "new": Job("done")})
    _file(output_dir, "old.wav", 4, age_days=4)
    _file(output_dir, "new.mp3", 5)
    _file(output_dir, ".history.json", 100, age_days=20)
    master = _file(voices_dir, "shared-voice.wav", 100, age_days=20)
    storage_policy.save(True, 3, 80)

    result = storage_policy.enforce(manager, output_dir, target_bytes=4)

    assert result["deleted"] == 2 and result["used_bytes"] == 0
    assert (output_dir / ".history.json").exists()
    assert master.exists()


def test_active_output_is_never_deleted(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(storage_policy, "SETTINGS_FILE", tmp_path / "config" / "policy.json")
    manager = Manager(output_dir, {"running": Job("running")})
    _file(output_dir, "running.wav", 10, age_days=10)
    storage_policy.save(True, 1, 1)

    result = storage_policy.enforce(manager, output_dir, target_bytes=0)

    assert result["deleted"] == 0
    assert (output_dir / "running.wav").exists()


def test_policy_api_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_policy, "SETTINGS_FILE", tmp_path / "config" / "policy.json")
    client = TestClient(app, headers={"X-Studio-Token": FLEET_TOKEN})
    response = client.put("/api/storage-policy", json={
        "enabled": True,
        "retention_days": 3,
        "max_gb": 80,
    })
    assert response.status_code == 200
    assert response.json()["retention_days"] == 3


def test_the_default_is_the_fleet_output_policy_not_30_days_and_80_gb(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_policy, "SETTINGS_FILE", tmp_path / "config" / "policy.json")
    value = storage_policy._read()
    assert value["retention_days"] == 3
    assert value["max_gb"] == 20.0 or value["max_gb"] <= 0.08 * 1024 ** 4 / 1024 ** 3


def test_the_default_size_follows_a_small_disk(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(storage_policy, "SETTINGS_FILE", tmp_path / "config" / "policy.json")
    monkeypatch.setattr(storage_policy.shutil, "disk_usage",
                        lambda _p: SimpleNamespace(total=100 * 1024 ** 3, used=0, free=0))
    assert storage_policy._read()["max_gb"] == 8.0           # 8% of 100 GB, below the 20 GB default
    monkeypatch.setattr(storage_policy.shutil, "disk_usage",
                        lambda _p: SimpleNamespace(total=2000 * 1024 ** 3, used=0, free=0))
    assert storage_policy._read()["max_gb"] == 20.0


def test_the_old_30_day_80_gb_defaults_migrate_once_and_a_chosen_value_is_kept(tmp_path, monkeypatch):
    policy_file = tmp_path / "config" / "policy.json"
    policy_file.parent.mkdir()
    policy_file.write_text(json.dumps({"enabled": True, "retention_days": 30, "max_gb": 80.0, "policy_version": 2}))
    monkeypatch.setattr(storage_policy, "SETTINGS_FILE", policy_file)

    assert storage_policy._read()["retention_days"] == 3
    migrated = json.loads(policy_file.read_text())
    assert migrated["retention_days"] == 3 and migrated["policy_version"] == storage_policy.POLICY_VERSION

    storage_policy.save(True, 30, 80)                         # the operator chooses the big policy: kept
    assert storage_policy._read()["retention_days"] == 30
    assert storage_policy._read()["max_gb"] == 80.0


def test_release_notes_follow_installed_changelog():
    client = TestClient(app)
    response = client.get("/api/release-notes")
    assert response.status_code == 200
    data = response.json()
    assert data["current_version"] == APP_VERSION
    # Package identity comes from the standalone release manifest. In a
    # development checkout it is intentionally unknown, while installed
    # changelog entries still retain their concrete release version.
    expected_version = (Path(__file__).resolve().parents[4] / "VERSION").read_text().strip()
    assert data["releases"][0]["version"] == expected_version
    assert data["releases"][0]["details"]


def test_webui_exposes_whats_new_modal_next_to_version():
    root = Path(__file__).parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    assert 'class="whats-new-button"' in html
    assert 'aria-labelledby="whats-new-title"' in html
    assert 'fetch("/api/release-notes"' in script
