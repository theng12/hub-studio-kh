#!/usr/bin/env python3
"""Negative controls for the four pieces, actually run.

Each control mutates one real source line, runs the check that is supposed to catch it, and
requires that check to FAIL; then restores the file byte-for-byte. Run from the repo root:

    ./.build/voice-runtime/bin/python3 tests/native/negative_controls.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEEP = ROOT / ".build" / "negative-controls"
PYTHON = ROOT / ".build" / "voice-runtime" / "bin" / "python3"

API = ROOT / "native" / "StudioAPI.swift"


def run(cmd: list[str], **kwargs) -> int:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kwargs).returncode


def pytest(*modules: str) -> int:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / ".build" / "pytest-libs")
    return run([str(PYTHON), "-m", "pytest", *modules, "-q", "-p", "no:randomly"],
               env=environment)


def swift_api_checks() -> int:
    """Compile and run the native API contract binary against the mutated sources."""
    command = ('source native/sdk.sh && xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 '
               'native/ShellCore.swift native/StartupRecovery.swift native/ReleaseNotes.swift '
               'native/UpdateClient.swift native/StudioAPI.swift tests/native/api_main.swift '
               '-o .build/native-api-negative && .build/native-api-negative')
    return run(["/bin/bash", "-c", command])


def catches(*modules: str) -> int:
    """Both halves of the contract must fail on the defect: the real store and the source pins."""
    swift = swift_api_checks()
    python = pytest(*modules)
    print(f"        native contract exit {swift}; source-pin exit {python}")
    return 0 if swift == 0 and python == 0 else 1


def control(name: str, path: Path, old: str, new: str, check) -> bool:
    original = path.read_text()
    if old not in original:
        print(f"SKIP  {name}: the line to mutate is not in {path.name}")
        return False
    KEEP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, KEEP / path.name)
    path.write_text(original.replace(old, new, 1))
    try:
        exit_code = check()
    finally:
        shutil.copy2(KEEP / path.name, path)
        assert path.read_text() == original, f"{path} was not restored"
    caught = exit_code != 0
    print(f"{'CAUGHT' if caught else 'MISSED'}  {name} (check exit {exit_code})")
    return caught


def main() -> int:
    results = []

    # 1. The Image control exists only for a model whose row says it has more than one mode.
    results.append(control(
        "image: offer a control for a single-mode model",
        API,
        "let offered = published?.supported == true && !options.isEmpty",
        "let offered = true",
        lambda: catches("tests/test_image_loading_mode_control.py")))

    # 2. The worker's own sentence wins over the app's derived one.
    results.append(control(
        "memory: drop the published `detail` so the derived sentence wins",
        API,
        "        if let detail, !detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {\n"
        "            return detail\n"
        "        }\n",
        "",
        lambda: catches("tests/test_memory_auto_sentence.py")))

    # 3. No rate may be derived from the Hub's round trip.
    results.append(control(
        "chat: derive a rate from the round trip when the worker stated none",
        API,
        'return "\\(completionTokens) tokens · \\(clock(roundTripSeconds)) round trip"',
        'return "\\(completionTokens) tokens · 1.0 tok/s · \\(clock(roundTripSeconds))"',
        lambda: catches("tests/test_chat_token_speed.py")))

    # 4. The catalogue row's warning must be threaded onto the transcription model.
    results.append(control(
        "subtitle: stop threading the catalogue row's warning into the model",
        API,
        "                memoryWarning: memoryWarning,\n",
        "",
        lambda: catches("tests/test_subtitle_warning_wiring.py")))

    print()
    for name, caught in zip(("image control", "auto sentence", "token rate", "subtitle wiring"),
                            results):
        print(f"{name}: {'negative control caught the defect' if caught else 'NOT CAUGHT'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

