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
VIEWS = ROOT / "native" / "StudioViews.swift"


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
               'native/StudioAPI.swift tests/native/api_main.swift '
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

    # 5. The Models tab's sections are the Studio enum, not a list kept in the app. Removing one
    #    Studio from the derivation must be caught by both halves - the real store's own section
    #    list and the source pin that says where the sections come from.
    results.append(control(
        "models: hand-list the sections and leave a Studio out",
        API,
        "        return StudioFunction.allCases.compactMap { function in",
        "        return ([StudioFunction.image, .audio, .transcription, .music, "
        ".chat]).compactMap { function in",
        lambda: catches("tests/test_native_model_defaults.py")))

    # 6. A required parameter is per-request content: a worker that lists one as settable must still
    #    get no control for it.
    results.append(control(
        "models: offer a control for a required parameter",
        API,
        "        row.parameters.filter { $0.required != true }.map { parameter in",
        "        row.parameters.map { parameter in",
        lambda: catches("tests/test_native_model_defaults.py")))

    # 7. A 422's reasons belong to their own parameters. Collapsing them into the shared banner is
    #    the defect the editor's per-field messages exist to prevent.
    results.append(control(
        "models: collapse a 422's per-parameter reasons into one banner",
        API,
        "            modelDefaultRefusals[key] = reasons\n",
        "            self.error = reasons.keys.sorted().joined(separator: \", \")\n",
        lambda: catches("tests/test_native_model_defaults.py")))

    # 8. A stored value the lane now refuses is shown with its reason, never silently dropped.
    results.append(control(
        "models: drop a stored value the lane no longer accepts",
        API,
        '        return row.notUsed.sorted { $0.key < $1.key }.map { "\\($0.key) — \\($0.value)" }',
        "        return []",
        lambda: catches("tests/test_native_model_defaults.py")))

    # 9. A default saved in the Models tab reaches the generator page as a changed catalogue row.
    #    Without the adoption, the page keeps the value it was seeded with.
    results.append(control(
        "models: hold the Image page on the value it was seeded with",
        API,
        "            guard let previous, previous != published else { continue }\n"
        "            if repo == imageModel, imageSettings == nil || imageSettings == previous {\n"
        "                imageSettings = published\n"
        "            }\n",
        "",
        lambda: catches("tests/test_native_model_defaults.py")))

    # 10. A ladder rung the lane marks as longer than one pass must say so on screen.
    results.append(control(
        "models: hide which ladder rung is rendered in sections",
        API,
        '                                ? "\(rung.label) (renders in sections)" : rung.label',
        "                                ? rung.label : rung.label",
        lambda: catches("tests/test_native_model_defaults.py")))

    # 11. Restore is the DELETE; a PUT would store the current values again instead of forgetting
    #     them, which is the opposite of what the button says.
    results.append(control(
        "models: restore with a PUT instead of the DELETE",
        API,
        '                ["studio", modality, "api", "model-defaults"], method: "DELETE",\n'
        '                body: ModelDefaultsRepoBody(repo: model.repo))',
        '                ["studio", modality, "api", "model-defaults"], method: "PUT",\n'
        '                body: ModelDefaultsRepoBody(repo: model.repo))',
        lambda: catches("tests/test_native_model_defaults.py")))

    # 12. A manual address choice must end automatic mode, or the next launch puts the address the
    #     operator just unticked straight back.
    results.append(control(
        "connections: leave automatic mode on after a manual address choice",
        VIEWS,
        "        studio.configuration.listenHostAutomatic = false\n",
        "        studio.configuration.listenHostAutomatic = true\n",
        lambda: catches("tests/test_listen_automatic_ui.py")))

    # 13. A label is not a quantization. Presenting one as the other claims a field the row never
    #     stated — and for a model whose label carries no bits, it invents one.
    results.append(control(
        "models: label the published label as a quantization",
        API,
        'facts.append(NativeModelFact(label: "Label", value: label, source: "label"))',
        'facts.append(NativeModelFact(label: "Quantization", value: label, source: "label"))',
        lambda: catches("tests/test_native_model_defaults.py")))

    print()
    for name, caught in zip(("image control", "auto sentence", "token rate", "subtitle wiring",
                             "model sections", "required parameter", "422 reasons",
                             "not_used", "resolved default", "ladder flag", "restore DELETE",
                             "listen automatic", "label heading"),
                            results):
        print(f"{name}: {'negative control caught the defect' if caught else 'NOT CAUGHT'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

