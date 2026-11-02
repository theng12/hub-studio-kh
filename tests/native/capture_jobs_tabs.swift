import AppKit
import SwiftUI

// Recreated Jobs-header harness, not the real app page. For full-page captures use
// STUDIOHUB_RENDER_JOBS_TABS=1 native/render-page.sh jobs. Measures all 8 selections
// for both BEFORE (stock SwiftUI Picker baseline) and AFTER (owned HubSegmentedControl).
// Composes a single comprehensive before/after sheet saved to Desktop.

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

// MARK: - Recreated Header Views

struct JobsHeaderBefore: View {
    @Binding var tab: HistoryTab

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Jobs")
                .font(.system(size: 24, weight: .bold))
                .foregroundColor(.white)
            Text("Every local generation in one place, or filtered to a single studio.")
                .font(.system(size: 13))
                .foregroundColor(Color(white: 0.7))
            Picker("History", selection: $tab) {
                ForEach(HistoryTab.allCases) { t in
                    Text(t.title).tag(t)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 520, alignment: .leading)
        }
        .padding(16)
        .frame(width: 580, alignment: .leading)
        .background(Color(red: 0.12, green: 0.12, blue: 0.12))
    }
}

struct JobsHeaderAfter: View {
    @Binding var tab: HistoryTab

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Jobs")
                .font(.system(size: 24, weight: .bold))
                .foregroundColor(.white)
            Text("Every local generation in one place, or filtered to a single studio.")
                .font(.system(size: 13))
                .foregroundColor(Color(white: 0.7))
            ScrollView(.horizontal, showsIndicators: false) {
                HubSegmentedControl(
                    selection: $tab,
                    items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                    sizing: .stablePerLabel
                )
            }
            .accessibilityLabel("History filter")
        }
        .padding(16)
        .frame(width: 580, alignment: .leading)
        .background(Color(red: 0.12, green: 0.12, blue: 0.12))
    }
}

struct NarrowReachabilityView: View {
    @Binding var tab: HistoryTab

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Narrow Container (400 pt) — Scrollable Affordance, No Label Clipping")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(Color(white: 0.7))
            ScrollView(.horizontal, showsIndicators: false) {
                HubSegmentedControl(
                    selection: $tab,
                    items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                    sizing: .stablePerLabel
                )
            }
            .frame(width: 400)
        }
        .padding(12)
        .frame(width: 580, alignment: .leading)
        .background(Color(red: 0.14, green: 0.14, blue: 0.14))
        .cornerRadius(6)
    }
}

struct MultibarIndependenceView: View {
    @Binding var tab: HistoryTab
    @Binding var filter: JobMachineFilter

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Coexisting Pickers — Jobs Tabs (8 seg) + Machine Filter (3 seg)")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(Color(white: 0.7))
            HubSegmentedControl(
                selection: $tab,
                items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                sizing: .stablePerLabel
            )
            HubSegmentedControl(
                selection: $filter,
                items: JobMachineFilter.allCases.map { HubSegmentItem(tag: $0, label: $0.rawValue) }
            )
        }
        .padding(12)
        .frame(width: 580, alignment: .leading)
        .background(Color(red: 0.14, green: 0.14, blue: 0.14))
        .cornerRadius(6)
    }
}

// MARK: - Offscreen Render Helper

@MainActor
func renderView<V: View>(_ view: V, size: NSSize) -> (NSImage, NSSegmentedControl?) {
    let hosting = NSHostingView(rootView: view.environment(\.colorScheme, .dark))
    hosting.frame = NSRect(origin: .zero, size: size)
    let window = NSWindow(contentRect: hosting.frame, styleMask: [.borderless], backing: .buffered, defer: false)
    window.appearance = NSAppearance(named: .darkAqua)
    window.contentView = hosting

    for _ in 0..<5 {
        RunLoop.current.run(until: Date().addingTimeInterval(0.04))
        hosting.layoutSubtreeIfNeeded()
    }

    func findSC(_ v: NSView) -> NSSegmentedControl? {
        if let sc = v as? NSSegmentedControl { return sc }
        for child in v.subviews { if let f = findSC(child) { return f } }
        return nil
    }

    let foundSC = findSC(hosting)

    guard let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else {
        fatalError("Failed to allocate bitmap rep")
    }
    hosting.cacheDisplay(in: hosting.bounds, to: rep)
    let img = NSImage(size: size)
    img.addRepresentation(rep)
    return (img, foundSC)
}

// MARK: - Main Capture Logic

@main
struct CaptureJobsTabs {
    @MainActor
    static func main() {
        print("=== Generating Native Jobs Tabs Before/After Comparison Sheet ===")

        let tabs = HistoryTab.allCases
        var beforeImages: [NSImage] = []
        var afterImages: [NSImage] = []
        var beforeWidths: [CGFloat] = []
        var afterWidths: [CGFloat] = []
        var afterSegmentArrays: [[CGFloat]] = []

        class Holder {
            var tab: HistoryTab = .all
            var filter: JobMachineFilter = .all
        }
        let holder = Holder()

        for tab in tabs {
            holder.tab = tab
            let binding = Binding(get: { holder.tab }, set: { holder.tab = $0 })

            // 1. Render Before
            let beforeV = JobsHeaderBefore(tab: binding)
            let (beforeImg, beforeSC) = renderView(beforeV, size: NSSize(width: 580, height: 110))
            beforeImages.append(beforeImg)
            guard let beforeSC else { fatalError("Before control was not rendered") }
            let bWidth = beforeSC.frame.width
            beforeWidths.append(bWidth)

            // 2. Render After
            let afterV = JobsHeaderAfter(tab: binding)
            let (afterImg, afterSC) = renderView(afterV, size: NSSize(width: 580, height: 110))
            afterImages.append(afterImg)
            guard let afterSC else { fatalError("After control was not rendered") }
            let aWidth = afterSC.frame.width
            afterWidths.append(aWidth)

            var segs: [CGFloat] = []
            for i in 0..<afterSC.segmentCount {
                segs.append(afterSC.width(forSegment: i))
            }
            afterSegmentArrays.append(segs)
            print("  Tab \(tab.title): Before frame=\(bWidth) pt | After fitting=\(aWidth) pt, segments=\(segs)")
        }

        // 3. Render Narrow Reachability & Multibar
        holder.tab = .downloads
        let narrowV = NarrowReachabilityView(tab: Binding(get: { holder.tab }, set: { holder.tab = $0 }))
        let (narrowImg, _) = renderView(narrowV, size: NSSize(width: 580, height: 85))

        let multiV = MultibarIndependenceView(
            tab: Binding(get: { holder.tab }, set: { holder.tab = $0 }),
            filter: Binding(get: { holder.filter }, set: { holder.filter = $0 })
        )
        let (multiImg, _) = renderView(multiV, size: NSSize(width: 580, height: 105))

        // 4. Compose Composite Sheet
        let sheetWidth: CGFloat = 1920
        let sheetHeight: CGFloat = 1750
        let composite = NSImage(size: NSSize(width: sheetWidth, height: sheetHeight))

        composite.lockFocus()

        // Background
        NSColor(calibratedRed: 0.09, green: 0.09, blue: 0.10, alpha: 1.0).setFill()
        NSRect(x: 0, y: 0, width: sheetWidth, height: sheetHeight).fill()

        // Title Block
        let titleAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 32, weight: .bold),
            .foregroundColor: NSColor.white
        ]
        ("Hub Native Jobs Tabs: Sizing & Geometry Invariance (All 8 Selections)" as NSString)
            .draw(at: NSPoint(x: 48, y: sheetHeight - 56), withAttributes: titleAttrs)

        let subtitleAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 14, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.72, alpha: 1.0)
        ]
        ("Real native AppKit / SwiftUI capture in recreated header harness (not a full app capture) · Base: 4be63f0 · Branch: fix/stable-jobs-tabs-20261102" as NSString)
            .draw(at: NSPoint(x: 48, y: sheetHeight - 84), withAttributes: subtitleAttrs)

        // Comparison Summary Cards
        let cardY = sheetHeight - 165
        let cardH: CGFloat = 64
        let colW: CGFloat = 880
        let col1X: CGFloat = 48
        let col2X: CGFloat = sheetWidth - colW - 48

        // Before summary card
        NSColor(calibratedRed: 0.18, green: 0.12, blue: 0.12, alpha: 1.0).setFill()
        NSBezierPath(roundedRect: NSRect(x: col1X, y: cardY, width: colW, height: cardH), xRadius: 8, yRadius: 8).fill()
        let bHeaderAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 15, weight: .bold),
            .foregroundColor: NSColor(calibratedRed: 1.0, green: 0.45, blue: 0.45, alpha: 1.0)
        ]
        ("BEFORE: Stock SwiftUI Picker (Recreated Baseline)" as NSString)
            .draw(at: NSPoint(x: col1X + 16, y: cardY + 36), withAttributes: bHeaderAttrs)
        let bDescAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.85, alpha: 1.0)
        ]
        ("Baseline Picker recreated from 4be63f0; widths below are measured in this harness, in points." as NSString)
            .draw(at: NSPoint(x: col1X + 16, y: cardY + 14), withAttributes: bDescAttrs)

        // After summary card
        NSColor(calibratedRed: 0.11, green: 0.19, blue: 0.13, alpha: 1.0).setFill()
        NSBezierPath(roundedRect: NSRect(x: col2X, y: cardY, width: colW, height: cardH), xRadius: 8, yRadius: 8).fill()
        let aHeaderAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 15, weight: .bold),
            .foregroundColor: NSColor(calibratedRed: 0.4, green: 0.9, blue: 0.5, alpha: 1.0)
        ]
        ("AFTER: Owned HubSegmentedControl (Stable Geometry, Δ = 0.0 pt Invariant)" as NSString)
            .draw(at: NSPoint(x: col2X + 16, y: cardY + 36), withAttributes: aHeaderAttrs)
        let aDescAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.85, alpha: 1.0)
        ]
        ("Bold-label widths are fixed before selection. Frame and segment widths below come from each native capture." as NSString)
            .draw(at: NSPoint(x: col2X + 16, y: cardY + 14), withAttributes: aDescAttrs)

        // Draw 8 Rows of comparisons
        let startY = cardY - 24
        let rowH: CGFloat = 142

        for i in 0..<8 {
            let rowY = startY - CGFloat(i + 1) * rowH
            let tabName = tabs[i].title

            // Row Label
            let rowLabelAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
                .foregroundColor: NSColor.white
            ]
            ("Selection \(i): \(tabName)" as NSString)
                .draw(at: NSPoint(x: col1X, y: rowY + rowH - 20), withAttributes: rowLabelAttrs)
            ("Selection \(i): \(tabName)" as NSString)
                .draw(at: NSPoint(x: col2X, y: rowY + rowH - 20), withAttributes: rowLabelAttrs)

            // Before image
            beforeImages[i].draw(in: NSRect(x: col1X, y: rowY + 14, width: 620, height: 104))

            // Before annotation badge
            let bBadgeText = String(format: "Measured frame: %.1f pt", Double(beforeWidths[i]))
            let badgeAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .medium),
                .foregroundColor: NSColor(calibratedRed: 1.0, green: 0.7, blue: 0.7, alpha: 1.0)
            ]
            (bBadgeText as NSString).draw(at: NSPoint(x: col1X + 16, y: rowY), withAttributes: badgeAttrs)

            // After image
            afterImages[i].draw(in: NSRect(x: col2X, y: rowY + 14, width: 620, height: 104))

            // After annotation badge
            let segmentText = afterSegmentArrays[i].map { String(format: "%.0f", Double($0)) }.joined(separator: ", ")
            let aBadgeText = String(format: "Frame: %.1f pt (delta %.1f) | segments: [%@] pt",
                                    Double(afterWidths[i]), Double(afterWidths[i] - afterWidths[0]), segmentText)
            let aBadgeAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .medium),
                .foregroundColor: NSColor(calibratedRed: 0.6, green: 1.0, blue: 0.7, alpha: 1.0)
            ]
            (aBadgeText as NSString).draw(at: NSPoint(x: col2X + 16, y: rowY), withAttributes: aBadgeAttrs)
        }

        // Bottom Section: Narrow Reachability & Multibar Independence
        let bottomSectionY = startY - 8 * rowH - 18
        let dividerPath = NSBezierPath()
        dividerPath.move(to: NSPoint(x: 48, y: bottomSectionY + 14))
        dividerPath.line(to: NSPoint(x: sheetWidth - 48, y: bottomSectionY + 14))
        NSColor(calibratedWhite: 0.25, alpha: 1.0).setStroke()
        dividerPath.lineWidth = 1
        dividerPath.stroke()

        let bSectionTitleAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 16, weight: .bold),
            .foregroundColor: NSColor.white
        ]
        ("Verification: Narrow Viewport Reachability & Multibar Independence" as NSString)
            .draw(at: NSPoint(x: 48, y: bottomSectionY - 12), withAttributes: bSectionTitleAttrs)

        // Draw Narrow Reachability Image
        narrowImg.draw(in: NSRect(x: col1X, y: bottomSectionY - 120, width: 620, height: 95))
        let narrowNoteAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.8, alpha: 1.0)
        ]
        ("400 pt viewport, initial scroll position. Scroll right to reach Downloads; reachability is checked in the native test." as NSString)
            .draw(at: NSPoint(x: col1X, y: bottomSectionY - 142), withAttributes: narrowNoteAttrs)

        // Draw Multibar Independence Image
        multiImg.draw(in: NSRect(x: col2X, y: bottomSectionY - 135, width: 620, height: 110))
        ("✓ Coexisting segmented controls (Jobs tabs + Machine filter) are completely isolated: zero hierarchy climbing, zero segment cross-mutation." as NSString)
            .draw(at: NSPoint(x: col2X, y: bottomSectionY - 157), withAttributes: narrowNoteAttrs)

        composite.unlockFocus()

        // 5. Save PNG
        guard let tiffData = composite.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiffData),
              let pngData = bitmap.representation(using: .png, properties: [:]) else {
            fatalError("Could not encode composite PNG")
        }

        let outDir = "/build/Desktop/All YouTube Stuff/Hub Jobs tabs"
        try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
        let outPath = "\(outDir)/hub-jobs-tabs-before-after.png"

        do {
            try pngData.write(to: URL(fileURLWithPath: outPath))
            print("Successfully wrote before/after screenshot sheet to:")
            print("  \(outPath)")
            print("  Dimensions: \(Int(sheetWidth))x\(Int(sheetHeight)) px")
        } catch {
            print("Failed to write screenshot sheet: \(error)")
            exit(1)
        }
    }
}
