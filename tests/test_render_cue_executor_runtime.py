#!/usr/bin/env python3
"""Direct admission checks for cue-overlay WebM inputs in the render executor."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub.backend.render_executor import _input_options

EBML = b"\x1aE\xdf\xa3"
ADMITTED = ("-f", "matroska,webm", "-protocol_whitelist", "file,pipe")


class _Stream:
    def __init__(self, handle):
        self._handle = handle

    def fileno(self) -> int:
        return self._handle.fileno()


class CueInputAdmissionTests(unittest.TestCase):
    def options(self, role: str, safe_name: str, payload: bytes):
        with tempfile.TemporaryFile() as handle:
            handle.write(payload)
            handle.flush()
            return _input_options(
                {"logical_id": "asset", "role": role, "safe_name": safe_name},
                _Stream(handle))

    def test_cue_overlay_webm_is_admitted(self):
        self.assertEqual(
            self.options("cue_overlay", "cue_overlay_0000.webm", EBML + b"payload"),
            ADMITTED)

    def test_ordinary_overlay_webm_options_are_unchanged(self):
        self.assertEqual(
            self.options("overlay", "overlay_0000.webm", EBML + b"payload"),
            ADMITTED)

    def test_cue_overlay_without_ebml_magic_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported format"):
            self.options("cue_overlay", "cue_overlay_0000.webm", b"not-ebml")

    def test_cue_overlay_with_foreign_extension_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported format"):
            self.options("cue_overlay", "cue_overlay_0000.png", EBML + b"payload")


if __name__ == "__main__":
    unittest.main()
