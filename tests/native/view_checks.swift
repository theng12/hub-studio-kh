import AppKit
import SwiftUI

// The Jobs page's Downloads tab (owner, 2026-10-08: model downloads in their own tab, not a section at
// the bottom of All). Draws the real window offscreen, picks the last segment, and checks it draws.
// Run from the lifecycle gate (tests/native/lifecycle_main.swift), which native/build.sh runs.
enum ViewChecks {
    @MainActor static func run(data: URL, models: URL) throws {
        var failures = 0
        func check(_ ok: Bool, _ what: String) { if !ok { failures += 1; print("FAIL: \(what)") } }
        check(HistoryTab.allCases.last == .downloads && HistoryTab.downloads.title == "Downloads",
              "Downloads is the last Jobs tab")
        check(Set(HistoryTab.allCases.map(\.id)).count == HistoryTab.allCases.count, "Jobs tab ids are unique")
        check(HistoryTab.downloads.studio == nil, "the Downloads tab shows no Studio's history")

        let configuration = try ShellConfiguration(
            arguments: ["lifecycle-tests", "--data-dir", data.path, "--models-dir", models.path],
            bundle: URL(fileURLWithPath: "/Applications/StudioHubFixture.app"))
        let studio = StudioController(configuration: configuration, loginAgentStatus: { _ in .disabled })
        studio.selection = .jobs
        NSApplication.shared.setActivationPolicy(.prohibited)
        let hosting = NSHostingView(rootView: StudioWindow(studio: studio))
        hosting.frame = NSRect(x: 0, y: 0, width: 1280, height: 800)
        let window = NSWindow(contentRect: hosting.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = hosting
        window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
        window.orderFront(nil)
        func settle() {
            for _ in 0..<8 {
                RunLoop.current.run(until: Date().addingTimeInterval(0.1))
                hosting.layoutSubtreeIfNeeded()
                window.displayIfNeeded()
            }
        }
        func draw() -> Data? {
            settle()
            guard let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { return nil }
            hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
            return bitmap.representation(using: .png, properties: [:])
        }
        func segmented(_ view: NSView) -> NSSegmentedControl? {
            if let control = view as? NSSegmentedControl { return control }
            for child in view.subviews { if let found = segmented(child) { return found } }
            return nil
        }
        let all = draw()
        let control = segmented(hosting)
        check(control?.segmentCount == HistoryTab.allCases.count, "the Jobs page draws one segment per tab")
        if let control, control.segmentCount > 0 {
            let last = control.segmentCount - 1
            check(control.label(forSegment: last) == "Downloads", "the last segment reads Downloads")
            control.selectedSegment = last
            _ = control.sendAction(control.action, to: control.target)
        }
        let downloads = draw()
        if let path = ProcessInfo.processInfo.environment["STUDIOHUB_VIEW_CHECKS_PNG"], let downloads {
            try? downloads.write(to: URL(fileURLWithPath: path))
        }
        check(all != nil && downloads != nil, "the Jobs page draws offscreen")
        check(all != downloads, "choosing the Downloads tab changes the page")

        // The Models page's function tabs (owner, 2026-10-19: "it has no tab or proper spacing. it's
        // squished"). One segmented control, one segment per function the library holds a model for
        // and none for a function it does not - the empty "Video — 0 models" group the owner asked
        // to be rid of was the same row of nothing. This gate has no Hub, so the library is empty:
        // that is exactly the case pinned here. `native/render-page.sh` shows the populated page.
        studio.selection = .models
        let models = draw()
        let tabs = segmented(hosting)
        check(tabs != nil, "the Models page draws its function tabs as a segmented control")
        check(tabs?.segmentCount == 1,
              "a function the library holds no model for gets no tab, only All")
        check(tabs?.label(forSegment: 0)?.hasPrefix("All ") == true,
              "the Models page's first tab is All, carrying the count it would show")
        check(models != nil, "the Models page draws offscreen")
        if let path = ProcessInfo.processInfo.environment["STUDIOHUB_VIEW_CHECKS_MODELS_PNG"],
           let models {
            try? models.write(to: URL(fileURLWithPath: path))
        }
        // The Render page (owner, 2026-10-26: "there's no render tab"). Two things are pinned.
        //
        // **Its rows never turn a fact the Hub did not record into a number.** A render accepted
        // before this build recorded a start has no wall time and no stated length; the row must
        // show only what it has, and a 0 would read as a measured nothing (AGENTS.md §6).
        let legacy = NativeRender(
            id: "c41d0e88-1f52-4c6b-8e27-3a9b5c6d7e1f", state: "output_ready",
            outcome: "succeeded", purpose: "production", display: nil, machine: "local",
            machineName: "This Mac", createdAt: 1_800_000_000, startedAt: nil, finishedAt: nil,
            wallSeconds: nil, runningSeconds: nil, videoSeconds: nil, width: nil, height: nil,
            frameRate: nil, container: nil, codec: nil, effort: nil, crf: nil, tier: nil,
            outputBytes: nil, outputSha256: nil, profileDigest: nil,
            storyUuid: "6d0b8f2e-8a51-4a63-9f0e-1c2b3a4d5e6f", intentId: "intent-1",
            attemptNo: 1, gatewayAttemptKey: "gateway-1", dispatchKey: "gateway:1",
            failure: nil, localFile: nil)
        let legacyFacts = RenderCopy.facts(legacy)
        check(legacyFacts.contains("length not recorded") && !legacyFacts.contains("0.0 s"),
              "a render with no recorded length says so, and never shows a zero: \(legacyFacts)")
        check(RenderCopy.video(legacy) == "length not recorded",
              "the detail says the length was not recorded, not that it was none")
        // A failed attempt produced nothing. The packet's own target must not be drawn as though it
        // had been rendered, or a failure would read as five minutes of video.
        let failed = NativeRender(
            id: "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d", state: "failed", outcome: "failed",
            purpose: "production", display: nil, machine: "blue-2", machineName: "Blue Room 2",
            createdAt: 1_800_000_000, startedAt: 1_799_999_900, finishedAt: 1_799_999_940,
            wallSeconds: 40, runningSeconds: nil, videoSeconds: 300, width: 1920, height: 1080,
            frameRate: 30, container: "mp4", codec: "h264", effort: "hardware", crf: 23,
            tier: "hardware-h264", outputBytes: nil, outputSha256: nil, profileDigest: nil,
            storyUuid: nil, intentId: nil, attemptNo: 1, gatewayAttemptKey: nil, dispatchKey: nil,
            failure: "ffmpeg exited with code 1.", localFile: nil)
        check(!RenderCopy.facts(failed).contains("5 min 0 s"),
              "a failed attempt does not show the packet's target as produced video")
        check(RenderCopy.video(failed).hasPrefix("none produced"),
              "a failed attempt's detail says nothing was produced")
        check(legacyFacts.contains("6d0b8f2e") == false,
              "the Story instance belongs in the detail, not in the one-line facts")
        check(RenderCopy.title(legacy).hasSuffix("c41d0e88"),
              "a render with no channel or episode name is titled by its own job id")
        check(RenderCopy.facts(legacy).contains("This Mac"),
              "the row names the Mac that rendered it")
        // A render Story Studio named shows which channel and episode it belongs to, and the job
        // id it was titled by before is still reachable in Details — nothing is invented for a
        // render that sent neither.
        let named = NativeRender(
            id: "8e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b", state: "output_ready",
            outcome: "succeeded", purpose: "production",
            display: NativeRenderDisplay(channel: "Khmer History", episode: "EP012 The Fall"),
            machine: "local", machineName: "This Mac", createdAt: 1_800_000_000,
            startedAt: 1_799_999_900, finishedAt: 1_799_999_940, wallSeconds: 40,
            runningSeconds: nil, videoSeconds: 300, width: 1920, height: 1080,
            frameRate: 30, container: "mp4", codec: "h264", effort: "hardware", crf: 23,
            tier: "hardware-h264", outputBytes: 25_000_000, outputSha256: nil,
            profileDigest: nil, storyUuid: nil, intentId: nil, attemptNo: 1,
            gatewayAttemptKey: nil, dispatchKey: nil, failure: nil, localFile: nil)
        check(RenderCopy.title(named).contains("Khmer History")
                && RenderCopy.title(named).contains("EP012 The Fall"),
              "a named render is titled by its channel and episode: \(RenderCopy.title(named))")
        check(RenderCopy.title(legacy).contains("c41d0e88"),
              "a render with no name keeps its job id in the title")
        check(NativeRenderDisplay(channel: nil, episode: "EP013").label == "EP013",
              "one label alone is still shown")
        check(RenderCopy.status(legacy) == .ok && RenderCopy.outcomeLabel(legacy) == "Done",
              "a published output reads as done")

        // And the page itself draws offscreen with nothing read yet: it says it is reading, rather
        // than drawing an empty list that would claim this Hub has rendered nothing.
        studio.selection = .renders
        let renders = draw()
        check(renders != nil, "the Render page draws offscreen")
        if let path = ProcessInfo.processInfo.environment["STUDIOHUB_VIEW_CHECKS_RENDERS_PNG"],
           let renders {
            try? renders.write(to: URL(fileURLWithPath: path))
        }
        window.orderOut(nil)

        if failures > 0 { exit(1) }
        print("Native view checks passed")
    }
}
