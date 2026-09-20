"""The standalone launch surface.

Every test in this module used to drive the retired Pinokio launcher
(`pinokio.js`, `start.js`, `whats_new.js`, `voicestudio-serve.sh`,
`install_generation.js`), which is not part of the standalone app: the app is a
macOS bundle and the host process launches the workers. Those files are gone, so
each test failed with `CalledProcessError` or `FileNotFoundError` and the
properties they described — menu ordering, action labels, a "What's New" screen,
media-tool preference — were being asserted against a program that no longer
exists.

What survives is the premise itself: the voice component ships an application,
not a Pinokio script set. The first test guards that. The rest are skipped with a
reason each; where the standalone app has a real successor, the skip names the
file that now covers it.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]

# Entry points of the Pinokio launcher this project retired.
RETIRED_LAUNCHER_FILES = (
    "pinokio.js",
    "install.js",
    "install_generation.js",
    "start.js",
    "update.js",
    "update_and_restart.js",
    "reset.js",
    "whats_new.js",
    "voicestudio-serve.sh",
    "voicestudio-watchdog.sh",
    "install_service.sh",
    "ENVIRONMENT.example",
)


def test_the_voice_component_ships_an_application_not_a_pinokio_launcher() -> None:
    """If a launcher file came back, the skips below would be hiding it."""
    present = [name for name in RETIRED_LAUNCHER_FILES if (ROOT / name).exists()]

    assert present == [], (
        "the retired Pinokio launcher files are back; the launcher tests must be "
        f"reinstated rather than skipped: {present}"
    )
    assert (ROOT / "app" / "backend" / "main.py").is_file()
    assert (ROOT / "app" / "frontend" / "app.js").is_file()


@pytest.mark.skip(
    reason=(
        "pinokio.js is not part of the standalone app; launcher menu construction "
        "belongs to the retired Pinokio source and has no Python successor."
    )
)
def test_generation_action_is_persistent_in_regular_and_service_modes() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "What's New is now the native app screen in native/ReleaseNotes.swift, which "
        "reads the bundled release-notes.md; covered by tests/test_release_notes.py."
    )
)
def test_whats_new_is_visible_in_every_launcher_state() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "whats_new.js is gone; the owner-facing notes are release-notes.md rendered by "
        "native/ReleaseNotes.swift, covered by tests/test_release_notes.py."
    )
)
def test_whats_new_displays_the_local_changelog() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "start.js/voicestudio-serve.sh no longer exist, so there is no launcher that "
        "could override the backend's download transport policy."
    )
)
def test_launchers_do_not_override_the_backend_download_transport_policy() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "voicestudio-serve.sh is gone; the standalone worker resolves app-managed tools "
        "through media_tools, covered by tests/test_managed_media_tools.py."
    )
)
def test_startup_service_prefers_app_managed_media_tools() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "Menu order and action labels were properties of pinokio.js, which the "
        "standalone app does not ship."
    )
)
def test_common_actions_use_consistent_names_and_safe_order() -> None:  # pragma: no cover
    pass
