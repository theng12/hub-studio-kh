@preconcurrency import AppKit

/// Samples native layout under the normal AppKit event loop, without forcing SwiftUI layout.
@MainActor
func captureJobsTransition(_ control: NSSegmentedControl, output: String) throws {
    func buttons(_ element: any NSAccessibilityProtocol) -> [any NSAccessibilityProtocol] {
        if element.accessibilityRole() == .radioButton { return [element] }
        return (element.accessibilityChildren() ?? []).flatMap {
            guard let child = $0 as? any NSAccessibilityProtocol else { return [any NSAccessibilityProtocol]() }
            return buttons(child)
        }
    }
    let tabs = buttons(control)
    precondition(tabs.count == HistoryTab.allCases.count, "Jobs accessibility segments missing")
    guard let window = control.window else { fatalError("Jobs window missing") }
    let directory = URL(fileURLWithPath: output).appendingPathComponent("transitions")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    var rows = [[String: Any]]()
    let sequence: [(Double, String)] = [(0.25, "Video"), (0.85, "Audio"), (1.45, "Video")]
    var event = 0
    let start = ProcessInfo.processInfo.systemUptime
    let app = NSApplication.shared
    var tick = 0
    let timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { timer in
        let elapsed = ProcessInfo.processInfo.systemUptime - start
        if event < sequence.count && elapsed >= sequence[event].0 {
            let label = sequence[event].1
            guard let index = tabs.firstIndex(where: { $0.accessibilityLabel() == label }) else { fatalError(label) }
            // Audio and Video centres lie inside their segments in both native distributions.
            let local = NSPoint(x: control.bounds.width * (CGFloat(index) + 0.5) / CGFloat(tabs.count),
                                y: control.bounds.midY)
            let point = control.convert(local, to: nil)
            for type: NSEvent.EventType in [.leftMouseDown, .leftMouseUp] {
                let mouse = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                    context: nil, eventNumber: tick * 2, clickCount: 1,
                    pressure: type == .leftMouseDown ? 1 : 0)!
                app.postEvent(mouse, atStart: false)
            }
            event += 1
        }
        let frame = control.frame
        rows.append(["t": ProcessInfo.processInfo.systemUptime - start,
                     "frame": [frame.minX, frame.minY, frame.width, frame.height],
                     "segments": tabs.map { tab -> [Double] in
                        let r = tab.accessibilityFrame(); return [r.minX, r.minY, r.width, r.height]
                     }, "selection": control.selectedSegment,
                     "distribution": control.segmentDistribution.rawValue])
        // Rasterize only the control: laying out its enclosing page would mask the geometry change.
        guard let bitmap = control.bitmapImageRepForCachingDisplay(in: control.bounds) else { fatalError("Capture bitmap") }
        control.cacheDisplay(in: control.bounds, to: bitmap)
        let path = directory.appendingPathComponent(String(format: "frame-%03d.png", tick))
        try! bitmap.representation(using: .png, properties: [:])!.write(to: path)
        tick += 1
        if tick == 126 {
            timer.invalidate()
            app.stop(nil)
            app.postEvent(NSEvent.otherEvent(with: .applicationDefined, location: .zero,
                modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                context: nil, subtype: 0, data1: 0, data2: 0)!, atStart: false)
        }
    }
    app.run()
    timer.invalidate()
    let selections = Set(rows.map { $0["selection"] as! Int })
    precondition(selections.contains(1) && selections.contains(5), "Mouse events did not switch Audio and Video")
    if (0..<control.segmentCount).allSatisfy({ control.width(forSegment: $0) > 0 }) {
        let first = rows[0]["frame"] as! [CGFloat]
        precondition(rows.allSatisfy { ($0["frame"] as! [CGFloat]) == first },
                     "Fixed-width Jobs control changed frame during a native selection")
    }
    let data = try JSONSerialization.data(withJSONObject: rows, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: directory.appendingPathComponent("geometry.json"))
    print("JOBS_TRANSITION frames=\(rows.count) widths=\(Set(rows.map { ($0["frame"] as! [CGFloat])[2] }))")
}
