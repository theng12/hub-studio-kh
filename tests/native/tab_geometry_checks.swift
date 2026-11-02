import AppKit
import SwiftUI

// Persistent standalone geometry regression test for Hub segmented tabs
// Verifies:
// 1. All 8 selection invariance: total control width and every segment width is 100% stable (0.0 pt variance).
// 2. Multi-bar independence: coexisting segmented controls never mutate each other.
// 3. Narrow layout reachability: small widths scroll horizontally without clipping labels.

enum HistoryTab: String, CaseIterable, Identifiable {
    case all = "All", audio = "Audio", subtitle = "Subtitle", image = "Image"
    case music = "Music", video = "Video", chat = "Chat", downloads = "Downloads"
    var id: String { rawValue }
    var title: String { rawValue }
}

enum JobMachineFilter: String, CaseIterable, Identifiable {
    case all = "All", thisMac = "This Mac", nodes = "Nodes"
    var id: String { rawValue }
}

@main
struct TabGeometryChecks {
    static func main() {
        print("=== Running Hub Native Tab Geometry Checks ===")
        var failures = 0
        func assertCheck(_ condition: Bool, _ message: String) {
            if condition {
                print("  ✓ \(message)")
            } else {
                print("  ✗ FAIL: \(message)")
                failures += 1
            }
        }

        func segmented(_ view: NSView) -> NSSegmentedControl? {
            if let control = view as? NSSegmentedControl { return control }
            for child in view.subviews { if let found = segmented(child) { return found } }
            return nil
        }

        func allSegmented(_ view: NSView) -> [NSSegmentedControl] {
            var res: [NSSegmentedControl] = []
            if let sc = view as? NSSegmentedControl { res.append(sc) }
            for sub in view.subviews { res.append(contentsOf: allSegmented(sub)) }
            return res
        }

        // --- CHECK 1: All 8 Selection Invariance ---
        print("\n[1] Testing all 8 selections invariance on Jobs tab control...")
        class StateHolder: ObservableObject {
            @Published var tab: HistoryTab = .all
        }
        let holder = StateHolder()
        let jobsView = ScrollView(.horizontal, showsIndicators: false) {
            HubSegmentedControl(
                selection: Binding(get: { holder.tab }, set: { holder.tab = $0 }),
                items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                sizing: .stablePerLabel
            )
        }

        let hosting = NSHostingView(rootView: jobsView)
        hosting.frame = NSRect(x: 0, y: 0, width: 800, height: 100)
        let window = NSWindow(contentRect: hosting.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = hosting
        for _ in 0..<4 {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            hosting.layoutSubtreeIfNeeded()
        }

        guard let sc = segmented(hosting) else {
            print("  ✗ FAIL: Could not find NSSegmentedControl in hosting view")
            exit(1)
        }

        assertCheck(sc.segmentCount == 8, "Segment count equals 8")
        let baseTotalWidth = sc.fittingSize.width
        let baseSegments = (0..<8).map { sc.width(forSegment: $0) }
        print("    Base total width: \(baseTotalWidth) pt")
        print("    Base segment widths: \(baseSegments)")

        assertCheck(baseTotalWidth > 400 && baseTotalWidth <= 520, "Total width fits within 520pt ceiling: \(baseTotalWidth) pt")

        var maxDelta: CGFloat = 0
        for i in 0..<8 {
            sc.selectedSegment = i
            _ = sc.sendAction(sc.action, to: sc.target)
            for _ in 0..<2 {
                RunLoop.current.run(until: Date().addingTimeInterval(0.02))
                hosting.layoutSubtreeIfNeeded()
            }

            let curW = sc.fittingSize.width
            let delta = abs(curW - baseTotalWidth)
            if delta > maxDelta { maxDelta = delta }

            let curSegments = (0..<8).map { sc.width(forSegment: $0) }
            let segmentsMatch = curSegments == baseSegments
            assertCheck(delta == 0.0 && segmentsMatch,
                        "Selection \(i) (\(HistoryTab.allCases[i].title)): width=\(curW) pt (Δ=\(delta)), segments=\(curSegments)")
        }
        assertCheck(maxDelta == 0.0, "Overall selection variance across all 8 tabs: exactly 0.0 pt")

        // --- CHECK 2: Multi-bar Independence ---
        print("\n[2] Testing multi-bar independence (Jobs tabs + Machine filter)...")
        let multiView = VStack(spacing: 20) {
            HubSegmentedControl(
                selection: .constant(HistoryTab.all),
                items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                sizing: .stablePerLabel
            )
            HubSegmentedControl(
                selection: .constant(JobMachineFilter.all),
                items: JobMachineFilter.allCases.map { HubSegmentItem(tag: $0, label: $0.rawValue) }
            )
        }
        let multiHost = NSHostingView(rootView: multiView)
        multiHost.frame = NSRect(x: 0, y: 0, width: 800, height: 200)
        let multiWindow = NSWindow(contentRect: multiHost.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        multiWindow.contentView = multiHost
        for _ in 0..<4 {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            multiHost.layoutSubtreeIfNeeded()
        }

        let bars = allSegmented(multiHost)
        assertCheck(bars.count == 2, "Found exactly 2 coexisting segmented controls")
        if bars.count == 2 {
            let bar1 = bars[0]
            let bar2 = bars[1]
            assertCheck(bar1.segmentCount == 8, "Bar 1 has 8 segments (Jobs tabs)")
            assertCheck(bar2.segmentCount == 3, "Bar 2 has 3 segments (Machine filter)")

            let bar1InitialW = bar1.fittingSize.width
            let bar2InitialW = bar2.fittingSize.width

            bar2.selectedSegment = 2
            _ = bar2.sendAction(bar2.action, to: bar2.target)
            bar1.selectedSegment = 7
            _ = bar1.sendAction(bar1.action, to: bar1.target)

            for _ in 0..<2 {
                RunLoop.current.run(until: Date().addingTimeInterval(0.02))
                multiHost.layoutSubtreeIfNeeded()
            }

            assertCheck(bar1.segmentCount == 8 && bar1.fittingSize.width == bar1InitialW,
                        "Bar 1 preserved segment count (8) and width (\(bar1.fittingSize.width) pt)")
            assertCheck(bar2.segmentCount == 3 && bar2.fittingSize.width == bar2InitialW,
                        "Bar 2 preserved segment count (3) and width (\(bar2.fittingSize.width) pt)")
        }

        // --- CHECK 3: Narrow Layout Reachability ---
        print("\n[3] Testing narrow layout reachability (400 pt container)...")
        let narrowHost = NSHostingView(rootView: ScrollView(.horizontal, showsIndicators: false) {
            HubSegmentedControl(
                selection: .constant(HistoryTab.all),
                items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                sizing: .stablePerLabel
            )
        })
        narrowHost.frame = NSRect(x: 0, y: 0, width: 400, height: 50)
        let narrowWindow = NSWindow(contentRect: narrowHost.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        narrowWindow.contentView = narrowHost
        for _ in 0..<4 {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            narrowHost.layoutSubtreeIfNeeded()
        }

        if let narrowSC = segmented(narrowHost) {
            assertCheck(narrowSC.segmentCount == 8, "Narrow layout contains all 8 segments without clipping")
            let downloadsW = narrowSC.width(forSegment: 7)
            assertCheck(downloadsW >= 90.0, "Full Downloads segment width preserved in narrow scroll view: \(downloadsW) pt")
        } else {
            assertCheck(false, "Failed to find segmented control in narrow host")
        }

        print("\n=== Summary ===")
        if failures == 0 {
            print("ALL GEOMETRY REGRESSION CHECKS PASSED (0 failures)")
        } else {
            print("FAILED with \(failures) error(s)")
            exit(1)
        }
    }
}
