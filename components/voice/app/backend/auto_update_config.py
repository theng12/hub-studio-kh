"""Voice Studio's fixed, non-user-editable updater identity."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .auto_update import AutoUpdater, UpdateError


ROOT = Path(__file__).resolve().parents[2]
SPEC = {
    "root": str(ROOT),
    "title": "Voice Studio KH",
    "slug": "voicestudio",
    "expected_remote": "https://github.com/theng12/voicestudio-mac.git",
    "branch": "main",
    "port": 47870,
    "server_label": "com.kh.voicestudio.server",
    "watchdog_label": "com.kh.voicestudio.watchdog",
    "default_hour": 2,
    "default_weekday": 6,
    "generation_marker": "diffusers",
    "verify_module": "backend.main",
}


class StandaloneUpdater:
    """Read-only updater facade for the packaged application.

    Standalone releases are replaced as app artifacts by the host shell.  The
    legacy Git/launchd updater remains available in source for fleet nodes but
    is never instantiated here, so startup cannot mutate the bundle or install
    a service.
    """

    def __init__(self, readiness: Optional[Callable[[], list[str]]] = None):
        self._readiness = readiness or (lambda: [])

    def public_status(self) -> dict:
        return {
            "state": "unavailable",
            "last_checked": None,
            "latest_version": None,
            "next_check": None,
            "next_retry": None,
            "last_update_result": None,
            "defer_reason": "Packaged standalone Voice uses app replacement for updates.",
            "details": ["Git and launchd updates are unavailable in the packaged app."],
            "rollback": None,
            "pending_manual": False,
            "managed_update": None,
            "capabilities": {"managed_exact_commit": False, "dependency_convergence": 0},
            "settings": {"mode": "off", "frequency": "daily", "maintenance_hour": 2,
                         "idle_only": True, "weekday": 6},
            "installed_version": "unknown",
            "update_available": False,
            "scheduler": {"installed": False, "supported": False, "label": ""},
            "release_notes_url": "",
        }

    def readiness_status(self) -> dict:
        try:
            reasons = [str(reason) for reason in (self._readiness() or [])]
        except Exception:
            reasons = ["readiness check unavailable"]
        return {"idle": not reasons, "reasons": reasons}

    def apply_scheduler_if_idle(self) -> bool:
        return True

    def save_settings(self, _payload: dict) -> dict:
        raise UpdateError("Automatic updates are unavailable in the packaged standalone app.")

    def trigger_check(self) -> dict:
        raise UpdateError("Automatic updates are unavailable in the packaged standalone app.")

    def trigger_update(self, **_kwargs) -> dict:
        raise UpdateError("Automatic updates are unavailable in the packaged standalone app.")

    def retry(self) -> dict:
        raise UpdateError("Automatic updates are unavailable in the packaged standalone app.")


def create_updater(readiness: Optional[Callable[[], list[str]]] = None, **kwargs) -> AutoUpdater | StandaloneUpdater:
    return StandaloneUpdater(readiness=readiness)
