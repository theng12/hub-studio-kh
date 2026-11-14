"""Anchor the strict render-authority reader on a test's own private root, not "/".

Every strict record fixture lives on the ATLAS volume, whose root is group-writable (0775)
and cannot be changed, so the reader's real ancestry walk refuses every path on it. The walk
above the fixture root is not what these tests are about; from the fixture root down, every
open, stat and mode check stays the real call.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import unittest
from unittest import mock

from components.hub.backend import render_authority_files


def anchor_root(case: unittest.TestCase, root: Path) -> Path:
    """Trust the resolved fixture ``root`` (forced owner-only) as the reader's root."""
    root = Path(os.path.realpath(root))
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    mode = stat.S_IMODE(os.stat(root).st_mode)
    if mode != 0o700:
        raise AssertionError(f"fixture root is not owner-only: {mode:o}")
    patcher = mock.patch.object(render_authority_files, "_ROOT_ANCHOR", root)
    patcher.start()
    case.addCleanup(patcher.stop)
    return root