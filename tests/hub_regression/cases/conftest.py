"""Small isolated fixture for preserved upstream Hub safeguards.

The adopted tests used ``backend`` plus the legacy ``main`` application.  The
standalone Hub deliberately has neither import surface, so this fixture binds
only ``components.hub.backend`` to disposable state and a packaged worker
manifest.  It is loaded only when this directory is selected by pytest.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest


_ROOT = Path(tempfile.mkdtemp(prefix="studiohub-st02-regression-"))
_DATA = _ROOT / "state"
_DATA.mkdir(mode=0o700)
_WORKERS = _ROOT / "private-workers.json"
_TOKEN = "st02-regression-token"
_WORKERS.write_text(json.dumps([
    {"id": "image", "modality": "image", "host": "127.0.0.1", "port": 51201,
     "machine": "local", "local": True, "runtime_identity": {"id": "image-runtime", "compatible": True},
     "hub_token": _TOKEN},
    {"id": "voice", "modality": "voice", "host": "127.0.0.1", "port": 51202,
     "machine": "local", "local": True, "runtime_identity": {"id": "voice-runtime", "compatible": True},
     "hub_token": _TOKEN},
    {"id": "subtitle", "modality": "transcription", "host": "127.0.0.1", "port": 51203,
     "machine": "local", "local": True, "runtime_identity": {"id": "subtitle-runtime", "compatible": True},
     "hub_token": _TOKEN},
    {"id": "music", "modality": "music", "host": "127.0.0.1", "port": 51204,
     "machine": "local", "local": True, "runtime_identity": {"id": "music-runtime", "compatible": True},
     "hub_token": _TOKEN},
]))
_WORKERS.chmod(0o600)
(_DATA / ".hub_token").write_text(_TOKEN + "\n")
(_DATA / ".hub_token").chmod(0o600)

# These must be set before any backend import: modules bind their state paths
# at import time.  No source checkout, model cache, or live service is used.
os.environ.update({
    "STUDIOHUB_DATA_DIR": str(_DATA),
    "STUDIOHUB_WORKERS_FILE": str(_WORKERS),
    "STUDIOHUB_PACKAGED": "1",
    "STUDIOHUB_REQUIRE_AUTH": "1",
})

from components.hub.backend import (  # noqa: E402
    alerts,
    app_release,
    auth,
    broker,
    control_plane,
    execution_assets,
    execution_identity,
    ledger,
    memory_admission,
    metrics,
    model_exposure,
    peers,
    shared_voices,
    transcription_jobs,
)
from components.hub.backend import registry  # noqa: E402
from components.hub.backend.monitor import StudioMonitor  # noqa: E402


_MONITOR = StudioMonitor(catalog_state_path=_DATA / "catalog-observations.json")


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def reset_state() -> StudioMonitor:
    """Reset only state owned by the selected standalone Hub modules."""
    files = (
        ledger.DB_FILE, registry.REGISTRY_FILE, registry.LABELS_FILE, registry.FLAGS_FILE,
        auth.TOKEN_FILE, auth.PASSWORD_FILE, auth.SESSIONS_FILE,
        alerts.ALERTS_FILE, metrics.STATE_FILE, broker.MODEL_PROTECTION_FILE,
        memory_admission.SETTINGS_FILE, model_exposure.STATE_FILE,
        execution_identity.DB_FILE, control_plane.SETTINGS_FILE,
        control_plane.DATABASE_URL_FILE, transcription_jobs.SETTINGS_FILE,
        _MONITOR.catalog_state_path,
    )
    for path in files:
        _remove(path)
    auth.TOKEN_FILE.write_text(_TOKEN + "\n")
    auth.TOKEN_FILE.chmod(0o600)
    for directory in (execution_assets.ROOT, shared_voices.ROOT, transcription_jobs.ROOT):
        _remove(directory)
    app_release.reset_for_tests()

    alerts._recent.clear()
    auth._login_failures.clear()
    broker.batches.clear()
    broker._busy.clear()
    broker._maintenance.clear()
    broker._external_machine_leases.clear()
    broker._external_lane_turn.clear()
    broker._machine_protection.clear()
    broker._model_protection.clear()
    broker._handoff_state.clear()
    broker._reserved["gb"] = 0.0
    broker._wakeup = asyncio.Event()
    memory_admission.reset_for_tests()
    execution_identity.reset_for_tests()
    control_plane.reset_for_tests()
    transcription_jobs.reset_for_tests()
    shared_voices._tasks.clear()
    shared_voices._delete_tasks.clear()
    shared_voices._resync_requested.clear()
    peers._cache.clear()

    _MONITOR.reload_registry()
    _MONITOR.status = {
        studio["id"]: {"status": "unknown", "last_seen": None, "last_checked": None}
        for studio in _MONITOR.registry
    }
    _MONITOR._catalog_cache.clear()
    _MONITOR._transcribe_cache.clear()
    _MONITOR._catalog_meta.clear()
    _MONITOR._restart_alerts.clear()
    broker.configure_monitor(_MONITOR)
    broker.configure_admission_guard(None)
    ledger.prepare_database()
    return _MONITOR


@pytest.fixture
def reset():
    reset_state()
    yield
    reset_state()


@pytest.fixture
def fixture_monitor(reset):
    return _MONITOR
