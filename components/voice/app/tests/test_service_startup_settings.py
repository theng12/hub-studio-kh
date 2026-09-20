"""Startup settings for the standalone worker.

Every test in this module drove the retired Pinokio service install:
`install_service.sh`, `ENVIRONMENT.example`, `voicestudio-serve.sh`,
`voicestudio-watchdog.sh` and the launcher `install.js`/`start.js` that seeded a
machine `ENVIRONMENT` file. None of those files exist in the standalone app, and
the app installs no service at all — the macOS bundle's host process supervises
the workers, which is why `GenerationManager._service_installed()` is hard-wired
to `False` (generation.py).

The surviving property is that last one, and it is a real safety property: a
packaged worker must not discover and act on a copied Pinokio launchd marker
inside its own bundle. The first test guards it. The four Pinokio install tests
are skipped with a reason each.
"""
from __future__ import annotations

import pytest

from backend import generation


def test_packaged_worker_never_acts_on_a_copied_service_marker() -> None:
    """The host process supervises the worker; the worker must not self-manage."""
    assert generation.GenerationManager._service_installed() is False

    manager = object.__new__(generation.GenerationManager)
    manager._consecutive_memory_failures = 0
    manager._restart_scheduled = False
    manager._last_memory_event = None
    assert manager.memory_status()["service_supervised"] is False


@pytest.mark.skip(
    reason=(
        "ENVIRONMENT.example is a Pinokio launcher template and is not shipped; the "
        "standalone app has no machine ENVIRONMENT file to seed or ignore."
    )
)
def test_repository_ships_template_and_ignores_machine_environment() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "install.js and start.js are retired Pinokio launcher scripts; the standalone "
        "app is launched by its bundle and seeds no environment file."
    )
)
def test_install_and_start_seed_only_a_missing_machine_environment() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "install_service.sh and ENVIRONMENT.example are not part of the standalone app; "
        "it installs no startup service and the host process owns worker supervision."
    )
)
def test_service_install_seeds_all_defaults_when_machine_environment_is_missing() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "There is no Pinokio autolaunch to disable: install_service.sh and "
        "voicestudio-serve.sh are gone and the standalone worker refuses to read a "
        "service marker at all (see the guard above)."
    )
)
def test_service_install_disables_pinokio_autolaunch_once_and_idempotently() -> None:  # pragma: no cover
    pass
