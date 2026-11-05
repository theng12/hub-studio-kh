"""A memory-pressure level the test states, decided at the sysctl source.

Image's tests must never read this Mac's live pressure (a rendering host can be critical
mid-run), and must never patch ``generation._under_memory_pressure`` itself: patching the
predicate hides the level it was asked for, so a warning fixture can pass for a critical one
(the defect a4260a5 repaired in one file out of eleven). This replaces only what
``_under_memory_pressure`` reads - through ``generation.subprocess``, so no other module's
subprocess calls change - and leaves the real predicate, its level default and its
unreadable-is-not-pressure rule running.

    with pressure(4) as reported:      # 1 normal, 2 warning, 4 critical, None unreadable
        reported.level = 4             # settable while it runs (a Mac that turns critical)

``setUpModule``/``tearDownModule`` use the same fixture for a whole test module, so no job
in it reaches the start door against the live host.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from components.image.app.backend import generation

PRESSURE_SYSCTL = 'kern.memorystatus_vm_pressure_level'


class _HostSubprocess:
    """`generation`'s own subprocess: the pressure read comes from the fixture, nothing else."""

    def __init__(self, holder: 'Pressure'):
        self._holder = holder

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def check_output(self, *args, **kwargs):
        if args and PRESSURE_SYSCTL in str(args[0]):
            level = self._holder.level
            if level is None:
                raise OSError(f'{PRESSURE_SYSCTL} unavailable')
            return f'{level}\n'
        return subprocess.check_output(*args, **kwargs)


class Pressure:
    """One running fixture: set ``.level`` while it runs, ``stop()`` ends it."""

    def __init__(self, level: int | None = 1):
        self.level = level
        self._patcher = None

    def start(self) -> 'Pressure':
        if self._patcher is None:
            self._patcher = patch.object(generation, 'subprocess', _HostSubprocess(self))
            self._patcher.start()
        return self

    def stop(self) -> None:
        if self._patcher is not None:
            self._patcher.stop()
            self._patcher = None

    def __enter__(self) -> 'Pressure':
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def pressure(level: int | None = 1) -> Pressure:
    """A started fixture reporting ``level`` (1 normal - what an ordinary image test runs with)."""
    return Pressure(level).start()
