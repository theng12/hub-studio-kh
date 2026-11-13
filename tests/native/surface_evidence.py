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

        store.imageModel = "fixture/zimage"
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
        store.imageModel = "fixture/flux"
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

        // ── Evidence: the exact strings the Models tab renders ─────────────────────────────
        // The section headers, the per-generator facts with their provenance, the editor's own
        // bounds, a refused value's reason and a stored value the lane no longer accepts — all
        // read back through the real store and the fixture's Studio routes.
        print("MODELS sections        : " + store.modelSections(search: "", installedOnly: false)
            .map(\.header).joined(separator: " | "))
        print("MODELS search flatten  : " + store.modelSections(search: "Narrow Image", installedOnly: false)
            .map { "\($0.header) [\($0.models.map(\.repo).joined(separator: ","))]" }
            .joined(separator: " | "))

        // The evidence's own reconnect (above) started a fresh connection, so the per-model
        // defaults are read again here, exactly as the Models tab reads them when it appears.
        await store.loadModelDefaultsIfNeeded()

        func kindName(_ control: NativeModelDefaultControl) -> String {
            switch control.kind {
            case .integer: return "integer"
            case .number: return "number"
            case .choice(let options): return "choice(\(options.joined(separator: ",")))"
            case .text: return "text"
            case .toggle: return "toggle"
            case .unrenderable: return "unrenderable"
            }
        }

        if let chat = store.models.first(where: { $0.repo == "Qwen/Qwen3.5-9B" }) {
            for fact in chat.facts { print("CHAT fact              : \(fact.line)") }
        }
        if let image = store.models.first(where: { $0.repo == "fixture/narrow-image" }) {
            for fact in image.facts { print("IMAGE fact             : \(fact.line)") }
            for control in store.modelDefaultControls(for: image) {
                print("IMAGE control          : \(control.name) [\(kindName(control))] \(control.boundText)")
            }
        }
        if let voice = store.models.first(where: { $0.repo == "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit" }) {
            for fact in voice.longForm?.facts ?? [] { print("VOICE long-form        : \(fact.line)") }
            for control in store.modelDefaultControls(for: voice) {
                print("VOICE control          : \(control.name) [\(kindName(control))] \(control.boundText)")
            }
        }
        if let subtitle = store.models.first(where: {
            $0.function == .transcription && $0.repo == "mlx-community/whisper-large-v3-turbo"
        }) {
            for fact in subtitle.facts { print("SUBTITLE fact          : \(fact.line)") }
        }
        if let music = store.models.first(where: { $0.repo == "fixture/sung-music" }) {
            let wanted = ["duration_seconds", "duration_seconds ladder", "steps", "guidance_scale",
                          "shift", "Single-pass render", "Vocals & lyrics", "Sample rate"]
            for fact in music.facts where wanted.contains(fact.label) {
                print("MUSIC fact             : \(fact.line)")
            }
            for control in store.modelDefaultControls(for: music) where control.ladder?.isEmpty == false {
                print("MUSIC control          : \(control.name) [\(kindName(control))] \(control.boundText) · rungs "
                      + (control.ladder ?? []).map {
                            $0.exceedsSinglePass == true ? "\($0.label)·sections" : $0.label
                        }.joined(separator: " | "))
            }
        }
        // A refused value, against the field it belongs to.
        if let narrow = store.models.first(where: { $0.repo == "fixture/narrow-image" }) {
            await store.saveModelDefaults(for: narrow, values: ["steps": .integer(99)])
            if let reason = store.modelDefaultRefusals(for: narrow)["steps"] {
                print("EDITOR refusal steps   : \(reason)")
            }
            print("EDITOR note            : \(store.modelDefaultsNote ?? "")")
            // A stored value the lane now refuses, shown rather than dropped.
            FixtureProtocol.seedStoredDefault(modality: "image", repo: "fixture/narrow-image",
                                              values: ["steps": 40])
            await store.refreshModelDefaults(modality: "image")
            for line in store.modelDefaultsNotUsed(for: narrow) {
                print("EDITOR not used        : \(line)")
            }
            // Restore, and the control's return to what the Studio states.
            FixtureProtocol.resetModelDefaultsRequests()
            await store.restoreModelDefaults(for: narrow)
            let requests = FixtureProtocol.modelDefaultsRequestLog().joined(separator: ",")
            let steps = store.modelDefaultControls(for: narrow).first { $0.name == "steps" }
            print("EDITOR restore         : \(requests) → steps \(steps?.value?.fieldText ?? "nil") · chosen \(steps?.chosen == true)")
        }
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
               'native/StudioAPI.swift .build/surface_evidence_main.swift '
               '-o .build/surface-evidence && .build/surface-evidence')
    result = subprocess.run(["/bin/bash", "-c", command], cwd=ROOT, text=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
