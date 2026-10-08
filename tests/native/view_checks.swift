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
        window.orderOut(nil)

        if failures > 0 { exit(1) }
        print("Native view checks passed")
    }
}
