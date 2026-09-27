// Renders one page of the real app, offscreen, to PNG files. A development tool, not a test.
//
// Why it exists: this app's window cannot be looked at from a terminal. `screencapture` needs
// Screen Recording permission (it answers "could not create image from display" without it) and
// driving the sidebar through Accessibility is unreliable. Three separate UI fixes were therefore
// written blind, one of them three times, and the owner had to keep sending screenshots of a
// column whose controls were clipped off the edge.
//
// This builds the actual views and writes what they look like, so the next layout change can be
// checked before it is handed over:
//
//     native/render-page.sh audio /tmp/studiohub-render 1920x1053 980x620
//
// It renders in a real (offscreen) window rather than through `ImageRenderer`, because the controls
// on these pages are AppKit-backed - `TextEditor`, `Picker`, `Slider`, `HSplitView` - and
// `ImageRenderer` draws a yellow "unsupported" placeholder instead of them.
import AppKit
import SwiftUI

@main
struct PageRenderHarness {
    @MainActor static func main() {
        let arguments = CommandLine.arguments
        // One or more pages, comma separated, so a single compile can show the whole app.
        let pages = (arguments.count > 1 ? arguments[1] : "audio")
            .split(separator: ",").map { pageNamed(String($0)) }
        let output = arguments.count > 2 ? arguments[2] : "/tmp/studiohub-render"
        var sizes: [(CGFloat, CGFloat)] = []
        for size in arguments.dropFirst(3) {
            let parts = size.lowercased().split(separator: "x")
            if parts.count == 2, let width = Double(parts[0]), let height = Double(parts[1]) {
                sizes.append((CGFloat(width), CGFloat(height)))
            }
        }
        if sizes.isEmpty { sizes = [(1920, 1053), (980, 620)] }

        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        try? FileManager.default.createDirectory(atPath: output, withIntermediateDirectories: true)
        let scratch = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("studiohub-render-\(UUID().uuidString)")
        let models = scratch.appendingPathComponent("models")
        try? FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        let config = try! ShellConfiguration(
            arguments: ["render", "--data-dir", scratch.path, "--models-dir", models.path],
            bundle: Bundle.main.bundleURL)
        let studio = StudioController(configuration: config)

        // `STUDIOHUB_RENDER_LIVE=1` connects the page to the Hub already running on this Mac, so a
        // render shows the operator's real jobs instead of empty lists. Read-only: it takes the
        // address and token from the readiness record and polls, and it is given a scratch data
        // directory so it cannot touch the running app's own workspace file.
        if ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_LIVE"] == "1" {
            let live = URL(fileURLWithPath: NSHomeDirectory())
                .appendingPathComponent("Library/Application Support/StudioHub Standalone")
            let record = live.appendingPathComponent("hub-ready.json")
            if let raw = try? Data(contentsOf: record),
               let object = try? JSONSerialization.jsonObject(with: raw) as? [String: Any],
               let address = object["url"] as? String, let token = object["token"] as? String,
               let url = URL(string: address) {
                Task { await studio.workspace.connect(baseURL: url, dataDirectory: scratch,
                                                      token: token, allowedHost: "127.0.0.1") }
                // The connection, its first poll and the history it reads all have to land before
                // the snapshot, or the page renders empty and proves nothing.
                for _ in 0..<48 { RunLoop.current.run(until: Date().addingTimeInterval(0.25)) }
                print("connected to the running Hub at \(address)")
            } else {
                print("no running Hub readiness record at \(record.path)")
            }
        }

        // `STUDIOHUB_RENDER_VIDEO_MODEL=<repo>` renders the Video page with a model already
        // chosen, because every control below the picker — the canvas pairs, the durations and the
        // lane's published switches — is drawn only once a model is selected. Without it a layout
        // change to those controls can only be checked by selecting a model by hand in the app.
        if let repo = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_VIDEO_MODEL"], !repo.isEmpty {
            studio.workspace.videoModel = repo
        }

        // `STUDIOHUB_RENDER_RESTART=1` pretends a change is waiting for a restart, so the notice
        // that only appears while the services run can still be looked at.
        if ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_RESTART"] == "1" {
            studio.restartPending = true
        }

        for page in pages {
        studio.selection = page
        for size in sizes {
            let hosting = NSHostingView(rootView: StudioWindow(studio: studio))
            hosting.frame = NSRect(x: 0, y: 0, width: size.0, height: size.1)
            let window = NSWindow(contentRect: hosting.frame,
                                  styleMask: [.titled, .resizable], backing: .buffered, defer: false)
            window.contentView = hosting
            window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
            window.orderFront(nil)
            // SwiftUI builds the AppKit controls on the first run-loop passes; snapshotting before
            // that produces an empty page and a wrong conclusion.
            for _ in 0..<8 {
                RunLoop.current.run(until: Date().addingTimeInterval(0.15))
                hosting.layoutSubtreeIfNeeded()
                window.displayIfNeeded()
            }
            guard let representation = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else {
                print("could not allocate a bitmap for \(Int(size.0))x\(Int(size.1))")
                continue
            }
            hosting.cacheDisplay(in: hosting.bounds, to: representation)
            guard let png = representation.representation(using: .png, properties: [:]) else {
                print("could not encode \(Int(size.0))x\(Int(size.1))")
                continue
            }
            let path = "\(output)/\(fileName(for: page))-\(Int(size.0))x\(Int(size.1)).png"
            do {
                try png.write(to: URL(fileURLWithPath: path))
                print("wrote \(path)")
            } catch {
                print("could not write \(path): \(error)")
            }
            window.orderOut(nil)
        }
        }
        try? FileManager.default.removeItem(at: scratch)
    }

    /// `whats-new` reads better than the raw `What's New`, which is also a filename hazard.
    private static func fileName(for page: StudioPage) -> String {
        page == .whatsNew ? "whats-new" : page.rawValue.lowercased().replacingOccurrences(of: " ", with: "-")
    }

    /// `subtitle` and `transcription` are the same page; its raw value is the stable key.
    private static func pageNamed(_ name: String) -> StudioPage {
        let wanted = name.lowercased()
        if wanted == "subtitle" { return .transcription }
        if wanted == "whatsnew" || wanted == "whats-new" { return .whatsNew }
        return StudioPage.allCases.first { $0.rawValue.lowercased() == wanted } ?? .overview
    }
}
