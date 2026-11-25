"""Make a bare `pytest tests/...` see the same import roots the gate sets.

The gate runs `unittest discover` with PYTHONPATH=repo:repo/tests:repo/packaging:repo/components/voice/app.
Without that, `packaging/stage_voice.py` cannot import its sibling `stage_khu_agent` and
`test_voice_bundle` fails with a ModuleNotFoundError that looks like a real regression. Appended,
not prepended: the runtime's PyPI `packaging` must keep winning over this repo's `packaging/`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / 'tests'), str(ROOT / 'packaging')):
    if entry not in sys.path:
        sys.path.append(entry)
