#!/usr/bin/env python3
"""Print the exact strings the four surfaces render, from the real store and decoders.

Compiles `tests/native/api_main.swift` with an evidence block appended just before its final
`print`, so what it prints came through the real `NativeWorkspaceStore` against the fixture HTTP
server - not from a hand-written expectation. The original file is never modified.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests" / "native" / "api_main.swift"
TARGET = ROOT / ".build" / "surface_evidence_main.swift"

EVIDENCE = r'''
        // ── Evidence: the exact strings the four surfaces render ───────────────────────────
        // The harness disconnects this store before the update section; put it back on the
        // fixture Hub so the reads below reach the fixture instead of no-opping.
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state, token: "native-test-token")
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)
        await store.refreshMemoryPolicy()
        if let policy = store.memoryInventory?.studios.first(where: { $0.id == "subtitle" })?.policy {
            let selected = policy.resolvedOptions.first { $0.mode == policy.mode }
            print("SETTINGS  picker items : " + policy.resolvedOptions.map {
                $0.mode == policy.defaultMode ? "\($0.label) (default)" : $0.label
            }.joined(separator: " | "))
            print("SETTINGS  selected     : \(selected?.label ?? "?")")
            print("SETTINGS  summary line : \(selected?.summary ?? "")")
            print("SETTINGS  resolution   : \(policy.resolution ?? "")")
        }
        FixtureProtocol.setMemoryPolicyOverride(nil)
        await store.refreshMemoryPolicy()

        store.imageModel = "fixture/flux"
        await store.refreshImageLoadingMode()
        if let control = store.imageLoadingControl {
            print("IMAGE     options      : " + control.options.map { "\($0.label) [\($0.id)]" }.joined(separator: " | "))
            print("IMAGE     selected     : \(control.selected)")
            print("IMAGE     detail       : \(control.detail ?? "")")
            print("IMAGE     effective    : \(control.effectiveSentence ?? "")")
        }
        await store.setImageLoadingMode("full")
        if let control = store.imageLoadingControl {
            print("IMAGE     after Full    : selected \(control.selected) · \(control.effectiveSentence ?? "")")
        }
        await store.setImageLoadingMode("auto")
        store.imageModel = "fixture/zimage"
        if let control = store.imageLoadingControl {
            print("IMAGE     single mode  : control offered \(control.offersControl) · reason: \(control.reason ?? "")")
        }
        store.imageModel = "fixture/flux-unpackaged"
        if let control = store.imageLoadingControl {
            print("IMAGE     no row       : control offered \(control.offersControl) · reason: \(control.reason ?? "")")
        }
        store.imageModel = "fixture/flux"

        if let job = store.jobs(for: .chat).first(where: { $0.id == "chat-job" }) {
            let label = store.models.first { $0.repo == job.model }?.label ?? job.model
            print("CHAT row  line 1       : \(label)\(job.generationSuffix)")
            print("CHAT row  line 2       : \(job.tokenSpeed)")
        }
        store.chatModel = "Qwen/Qwen3.5-9B"
        store.clearChat()
        store.chatPrompt = "Show the rate"
        await store.sendChat()
        print("CHAT live reply        : \(store.chatTurns.last?.speed ?? "")")
'''


def main() -> int:
    source = SOURCE.read_text()
    marker = '        print("Native API contract checks passed")'
    if marker not in source:
        print("the harness's final print moved; update this evidence script")
        return 1
    TARGET.write_text(source.replace(marker, EVIDENCE + marker, 1))
    command = ('source native/sdk.sh && xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 '
               'native/ShellCore.swift native/StartupRecovery.swift native/ReleaseNotes.swift '
               'native/UpdateClient.swift native/StudioAPI.swift .build/surface_evidence_main.swift '
               '-o .build/surface-evidence && .build/surface-evidence')
    result = subprocess.run(["/bin/bash", "-c", command], cwd=ROOT, text=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
