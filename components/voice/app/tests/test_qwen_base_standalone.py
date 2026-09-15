"""Voice-suite view of the ST-04 Qwen3-TTS Base contracts.

The cases live at ``tests/test_qwen_base_standalone.py`` so the project-level
``unittest discover -s tests`` run owns them too.  This module re-exports the
same classes so ``components/voice`` pytest runs collect them as part of the
adopted Voice suite instead of missing them.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

VOICE_APP = Path(__file__).resolve().parents[1]
REPO_ROOT = VOICE_APP.parents[2]
SOURCE = REPO_ROOT / "tests" / "test_qwen_base_standalone.py"
for _entry in (str(VOICE_APP), str(REPO_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

_spec = importlib.util.spec_from_file_location("_st04_qwen_base_contracts", SOURCE)
_implementation = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _implementation
_spec.loader.exec_module(_implementation)

QwenBaseCatalogTests = _implementation.QwenBaseCatalogTests
ReferenceContractTests = _implementation.ReferenceContractTests
ReferenceFailureCodeTests = _implementation.ReferenceFailureCodeTests
QualityCheckLeaseTests = _implementation.QualityCheckLeaseTests
ReferenceCleanupTests = _implementation.ReferenceCleanupTests


def load_tests(loader, standard_tests, pattern):
    return loader.loadTestsFromModule(_implementation)


if __name__ == "__main__":
    unittest.main()
