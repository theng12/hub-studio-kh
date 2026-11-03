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

@MainActor enum ConnectionCopyRenderProbe {
    static var frame = CGRect.zero
    static var didCopy = false
}

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
        if ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_CONNECTION_COPY"] == "1" {
            studio.configuration.listenHost = (["127.0.0.1"] + LocalAddresses.current().map(\.address)).joined(separator: ",")
        }

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

        // `STUDIOHUB_RENDER_FIXTURES=1` fills the Audio, Image and Music history with jobs whose prompts
        // are as long as the ones that broke the rows, and serves a generated picture for every image
        // fetch. It needs render_fixture_patch.py, which render-page.sh applies to its copy.
        if ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_FIXTURES"] == "1" {
            seedFixtures(studio.workspace, scratch: scratch)
            seedLibrary(studio.workspace)
            seedRenders(studio.workspace)
            // Running services and both tokens, so Connections draws its (masked) credential rows.
            studio.readyURL = URL(string: "http://127.0.0.1:47873")
            studio.connectionToken = "render-fixture-hub-token-0123456789"
            let hubState = scratch.appendingPathComponent("hub")
            try? FileManager.default.createDirectory(at: hubState, withIntermediateDirectories: true)
            let fleetFile = hubState.appendingPathComponent(".fleet_token")
            try? Data("render-fixture-fleet-token-0123456789".utf8).write(to: fleetFile)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fleetFile.path)
            studio.readFleetToken()
            // Settings > Lite Mode. `STUDIOHUB_RENDER_LITE=off|working|standing_by|yielded|quiet_hours`
            // picks what the Hub says this Mac is doing (default off, the state a Mac turns it on from).
            let lite = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_LITE"] ?? "off"
            let reasons = ["standing_by": "someone is using this Mac", "quiet_hours": "inside its quiet hours",
                           "yielded": "macOS reported critical memory pressure for 20 seconds"]
            let report: [String: Any] = [
                "enabled": lite != "off", "idle_only": lite == "standing_by", "idle_minutes": 3,
                "step_aside": lite == "standing_by", "resume_after_minutes": 2,
                "start_free_gb": lite == "quiet_hours" ? 3.5 : NSNull(),
                "quiet_hours": lite == "quiet_hours" ? ["22:00-07:00", "12:00-13:00"] : [],
                "hide_menu_bar_icon": lite == "quiet_hours", "state": lite,
                "reason": reasons[lite].map { $0 as Any } ?? NSNull(),
                "jobs_today": lite == "off" ? 0 : 3, "yields_today": lite == "yielded" ? 1 : 0]
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            studio.workspace.workstation = try? decoder.decode(
                NativeWorkstation.self, from: JSONSerialization.data(withJSONObject: report))
        }

        // Work choices use local fixtures only; this never changes a live Mac's pause or preferences.
        if ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_FIXTURES"] == "1" {
            let priorityMode = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_WORK_PRIORITY"] ?? "partial"
            let order = priorityMode == "none" ? [] : (priorityMode == "full" ? ["image", "transcription", "voice", "chat"] : ["chat"])
            let policy: [String: Any] = ["machine_enabled": false, "chat_priority": true,
                "priority_order": order,
                "priority_functions": [["id": "voice", "title": "Voice Studio"],
                                       ["id": "image", "title": "Image Studio"],
                                       ["id": "chat", "title": "Chat Studio"],
                                       ["id": "transcription", "title": "Subtitle Studio"]],
                "studios": [["id": "voice@local", "title": "Voice Studio", "modality": "voice", "enabled": true],
                            ["id": "image@local", "title": "Image Studio", "modality": "image", "enabled": true],
                            ["id": "chat@local", "title": "Chat Studio", "modality": "chat", "enabled": true],
                            ["id": "transcription@local", "title": "Subtitle Studio", "modality": "transcription", "enabled": false],
                            ["id": "future@local", "title": "Future registered Studio with a longer name", "modality": "future", "enabled": false]]]
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            studio.workspace.workPolicy = try? decoder.decode(
                NativeWorkPolicy.self, from: JSONSerialization.data(withJSONObject: policy))
        }

        // `STUDIOHUB_RENDER_ROLES=controller,node,node-offline,standalone` renders every page once per
        // role, with the Hub's identity and the fleet answers a Mac in that role would have. Needs the
        // fixture seams: render-page.sh applies them with STUDIOHUB_RENDER_FIXTURES=1.
        let roles: [String?] = (ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_ROLES"] ?? "")
            .split(separator: ",").map { String($0) }
        // `STUDIOHUB_RENDER_APPEARANCE=light|dark|both` renders in that appearance (files gain a
        // `-light` / `-dark` suffix); unset keeps the system's. `STUDIOHUB_RENDER_BORDERLESS=1` draws
        // the window without a title bar so a tall render is only the page.
        let mode = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_APPEARANCE"] ?? ""
        let appearances: [String?] = mode == "both" ? ["light", "dark"] : (mode.isEmpty ? [nil] : [mode])
        let borderless = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_BORDERLESS"] == "1"
        for appearance in appearances {
        if let appearance { app.appearance = NSAppearance(named: appearance == "dark" ? .darkAqua : .aqua) }
        for role in (roles.isEmpty ? [nil] : roles) {
        if let role { applyRole(role, to: studio.workspace) }
        Self.applyInstallPlan(studio.workspace)
        for page in pages {
        studio.selection = page
        for size in sizes {
            // `.key` because a window on no screen is never the key window, and controls draw their
            // inactive look (a grey "prominent" button) otherwise, which is not what a person sees.
            ConnectionCopyRenderProbe.frame = .zero
            let hosting = NSHostingView(rootView: StudioWindow(studio: studio)
                .coordinateSpace(name: "ConnectionCopyRender")
                .environment(\.controlActiveState, .key))
            hosting.frame = NSRect(x: 0, y: 0, width: size.0, height: size.1)
            let window = NSWindow(contentRect: hosting.frame,
                                  styleMask: borderless ? [.borderless] : [.titled, .resizable],
                                  backing: .buffered, defer: false)
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
            if page == .connections && ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_CONNECTION_COPY"] == "1" {
                func scrollView(_ view: NSView) -> NSScrollView? {
                    if let scroll = view as? NSScrollView,
                       scroll.bounds.width > hosting.bounds.width / 2 { return scroll }
                    for child in view.subviews { if let scroll = scrollView(child) { return scroll } }
                    return nil
                }
                // The fixture seam observes layout; the mouse still presses the shipped button.
                app.setActivationPolicy(.regular)
                window.setFrameOrigin(NSPoint(x: 60, y: 60))
                window.makeKeyAndOrderFront(nil)
                app.activate(ignoringOtherApps: true)
                RunLoop.current.run(until: Date().addingTimeInterval(0.3))
                let originalConfiguration = studio.configuration
                var target: CGRect?
                ConnectionCopyRenderProbe.didCopy = false
                if let scroll = scrollView(hosting), let document = scroll.documentView {
                    for step in 0...10 {
                        let offset = max(0, document.bounds.height - scroll.contentView.bounds.height) * CGFloat(step) / 10
                        document.scroll(NSPoint(x: 0, y: offset))
                        for _ in 0..<3 { RunLoop.current.run(until: Date().addingTimeInterval(0.1)) }
                        hosting.layoutSubtreeIfNeeded()
                        window.displayIfNeeded()
                        let frame = ConnectionCopyRenderProbe.frame
                        if !frame.isEmpty && hosting.bounds.insetBy(dx: 0, dy: 20).contains(frame) {
                            target = frame
                            break
                        }
                    }
                }
                guard let address = studio.nodeFacingAddress, let target else {
                    fatalError("Rendered node join Copy button missing")
                }
                let local = NSPoint(x: target.midX,
                    y: hosting.isFlipped ? target.midY : hosting.bounds.height - target.midY)
                let point = hosting.convert(local, to: nil)
                NSPasteboard.general.clearContents()
                for type: NSEvent.EventType in [.leftMouseDown, .leftMouseUp] {
                    let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                        timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                        context: nil, eventNumber: 0, clickCount: 1,
                        pressure: type == .leftMouseDown ? 1 : 0)!
                    app.postEvent(event, atStart: false)
                }
                let stop = Timer.scheduledTimer(withTimeInterval: 0.3, repeats: false) { _ in
                    app.stop(nil)
                    app.postEvent(NSEvent.otherEvent(with: .applicationDefined, location: .zero,
                        modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                        context: nil, subtype: 0, data1: 0, data2: 0)!, atStart: false)
                }
                app.run()
                stop.invalidate()
                if !ConnectionCopyRenderProbe.didCopy {
                    print("COPY_MOUSE missed frame=\(target) host=\(hosting.bounds) flipped=\(hosting.isFlipped) point=\(point)")
                    let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds)!
                    hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
                    try? bitmap.representation(using: .png, properties: [:])?.write(to:
                        URL(fileURLWithPath: output).appendingPathComponent("copy-driver-diagnostic-\(Int(size.0))x\(Int(size.1)).png"))
                    fflush(stdout)
                }
                precondition(NSPasteboard.general.string(forType: .string) == address,
                             "Copy must include the complete node address and port")
                precondition(studio.configuration.listenHost == originalConfiguration.listenHost
                    && studio.configuration.port == originalConfiguration.port
                    && studio.configuration.listenHostAutomatic == originalConfiguration.listenHostAutomatic,
                    "Copy changed connection settings")
                hosting.layoutSubtreeIfNeeded()
                window.displayIfNeeded()
                precondition(ConnectionCopyRenderProbe.didCopy, "Mouse click did not reach the Copy action")
                print("CONNECTION_COPY_INTERACTION passed: native mouse click, complete address and port, settings unchanged; inspect captured Copied feedback")
            }
            if page == .image && ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_IMAGE_REFERENCE"] == "1" {
                func imageControl(_ view: NSView) -> NSSegmentedControl? {
                    if let control = view as? NSSegmentedControl,
                       control.segmentCount == 2, control.label(forSegment: 1) == "Reference to image" { return control }
                    for child in view.subviews { if let found = imageControl(child) { return found } }
                    return nil
                }
                guard let control = imageControl(hosting) else { fatalError("Image mode control missing") }
                control.selectedSegment = 1
                _ = control.sendAction(control.action, to: control.target)
                precondition(studio.workspace.imageMode == "edit", "Native reference mode action did not reach the form")
                studio.workspace.addImageReference(scratch.appendingPathComponent("fixture.png"))
                precondition(studio.workspace.imageReferences.count == 1, "Reference image was not prepared")
                let decoded = Data(base64Encoded: studio.workspace.imageReferences[0].b64!)!
                let bitmap = NSBitmapImageRep(data: decoded)!
                precondition(max(bitmap.pixelsWide, bitmap.pixelsHigh) <= 1024)
                for _ in 0..<8 {
                    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
                    hosting.layoutSubtreeIfNeeded()
                    window.displayIfNeeded()
                }
                print("IMAGE_REFERENCE_INTERACTION passed: native segment action, reference preparation, published pixel bound")
            }
            // Exercise the shipped Jobs view through its native control; no recreated header.
            let captureTabs = page == .jobs && ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_JOBS_TABS"] == "1"
            func jobsControl(_ view: NSView) -> NSSegmentedControl? {
                if let control = view as? NSSegmentedControl,
                   control.segmentCount == HistoryTab.allCases.count { return control }
                for child in view.subviews { if let found = jobsControl(child) { return found } }
                return nil
            }
            if page == .jobs && ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_JOBS_TRANSITIONS"] == "1" {
                guard let control = jobsControl(hosting) else { fatalError("Jobs control missing") }
                app.setActivationPolicy(.regular)
                window.setFrameOrigin(NSPoint(x: 0, y: 60))
                window.makeKeyAndOrderFront(nil)
                app.activate(ignoringOtherApps: true)
                RunLoop.current.run(until: Date().addingTimeInterval(0.3))
                try! captureJobsTransition(control, output: output)
            }
            for tabIndex in (captureTabs ? Array(HistoryTab.allCases.indices) : [-1]) {
            if captureTabs {
                guard let control = jobsControl(hosting) else { fatalError("Jobs control missing") }
                control.selectedSegment = tabIndex
                _ = control.sendAction(control.action, to: control.target)
                for _ in 0..<8 {
                    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
                    hosting.layoutSubtreeIfNeeded()
                    window.displayIfNeeded()
                }
                // Read actual accessible segment frames: automatic widths report zero via width(forSegment:).
                func segmentFrames(_ element: any NSAccessibilityProtocol) -> [[String: Any]] {
                    if element.accessibilityRole() == .radioButton {
                        let frame = element.accessibilityFrame()
                        return [["label": element.accessibilityLabel() ?? "", "x": frame.minX,
                                 "width": frame.width]]
                    }
                    return (element.accessibilityChildren() ?? []).flatMap {
                        guard let child = $0 as? any NSAccessibilityProtocol else { return [[String: Any]]() }
                        return segmentFrames(child)
                    }
                }
                let frame = control.convert(control.bounds, to: hosting)
                let metrics: [String: Any] = ["tab": HistoryTab.allCases[tabIndex].title,
                    "windowWidth": hosting.bounds.width, "windowHeight": hosting.bounds.height,
                    "controlX": frame.minX, "controlWidth": frame.width,
                    "fittingWidth": control.fittingSize.width, "segments": segmentFrames(control)]
                let data = try! JSONSerialization.data(withJSONObject: metrics, options: [.sortedKeys])
                print("JOBS_GEOMETRY " + String(data: data, encoding: .utf8)!)
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
            let tabSuffix = captureTabs ? "-tab-\(tabIndex)" : ""
            let path = "\(output)/\(fileName(for: page))\(tabSuffix)\(role.map { "-" + $0 } ?? "")-\(Int(size.0))x\(Int(size.1))\(appearance.map { "-" + $0 } ?? "").png"
            do {
                try png.write(to: URL(fileURLWithPath: path))
                print("wrote \(path)")
            } catch {
                print("could not write \(path): \(error)")
            }
            }
            window.orderOut(nil)
        }
        }
        }
        }
        try? FileManager.default.removeItem(at: scratch)
    }

    @MainActor private static func decode<T: Decodable>(_ type: T.Type, _ json: String) -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try! decoder.decode(T.self, from: Data(json.utf8))
    }

    /// One fleet machine as the Hub's overview publishes it, including the `disk`, `startup` and
    /// `current_job` objects a controller's Connections page draws.
    private static func machineJSON(_ id: String, _ label: String, role: String, reachable: Bool = true,
                                    chip: String, memory: Double, available: Double, disk: Double,
                                    total: Double, diskState: String, jobs: Int, version: String = "0.6.126",
                                    job: String? = nil, servicesUp: Int = 4, startOnLaunch: Bool = true,
                                    address: String? = nil) -> String {
        let startup = reachable ? """
            {"open_at_login": true, "start_services_on_launch": \(startOnLaunch), "services_running": {"up": \(servicesUp), "total": 4},
             "app_reporting": true}
            """ : "null"
        let diskObject = reachable ? """
            {"state": "\(diskState)", "free_gb": \(disk), "total_gb": \(total), "free_percent": \(((disk / total) * 1000).rounded() / 10), "warnings": []}
            """ : "null"
        return """
            {"machine":"\(id)","label":"\(label)","role":"\(role)","address":\(address.map { "\"" + $0 + "\"" } ?? "null"),
             "reachable":\(reachable),"version":"\(version)","health":"\(reachable ? "up" : "down")","detail":null,
             "memory_gb":\(reachable ? "\(memory)" : "null"),"available_gb":\(reachable ? "\(available)" : "null"),
             "disk_free_gb":\(reachable ? "\(disk)" : "null"),"disk_total_gb":\(reachable ? "\(total)" : "null"),
             "disk":\(diskObject),"startup":\(startup),"current_job":\(job ?? "null"),
             "chip":\(reachable ? "\"\(chip)\"" : "null"),"jobs":\(jobs),"average_seconds":44.0,"modalities":{"image":\(jobs / 2),"voice":\(jobs - jobs / 2)}}
            """
    }

    private static func nodeJSON(_ id: String, _ state: String, _ detail: String?, seen: Double,
                                 version: String = "0.6.126", now: Double) -> String {
        """
        {"id":"\(id)","name":"\(id)","address":"http://100.64.0.\(10 + abs(id.hashValue) % 80):47873","state":"\(state)",
         "detail":\(detail.map { "\"\($0)\"" } ?? "null"),"version":"\(version)","role":"agent",
         "studios":[{"id":"voice","modality":"voice"},{"id":"image","modality":"image"}],
         "joined_at":\(now - 30 * 86400),"last_seen_at":\(now - seen),"credential_issued_at":\(now - 30 * 86400),"has_credential":true}
        """
    }

    /// `controller`, `node`, `node-offline` or `standalone`: the Hub's identity and the fleet answers a
    /// Mac in that role would have. A controller has four joined Macs in four different conditions
    /// (working, low on disk and memory, off, refusing the credential) so every state is on screen.
    /// The install screen with a real answer on it, for the screenshot: this Mac's memory and
    /// free space, the four presets with counts, and models in every state - installed, to copy,
    /// missing from the SSD (the 2026-10-28 `florence-2-large-ft` case) and too big for this Mac.
    ///
    /// A fixture, and labelled as one by its path (`/Volumes/KH-STUDIO-ATLAS/...`); the numbers are
    /// this Mac's real ones where they are cheap to borrow and made up where they are not.
    @MainActor static func applyInstallPlan(_ workspace: NativeWorkspaceStore) {
        func model(_ package: String, _ label: String, _ job: String, _ purpose: String,
                   _ bits: String?, _ gb: Double, _ floor: Int, installed: Bool = false,
                   reason: String? = nil, sources: [String] = ["kit", "internet"]) -> NativeInstallModel {
            NativeInstallModel(id: package, label: label, job: job, purpose: purpose,
                               quantization: bits, bytes: Int64(gb * 1e9), floorGB: floor,
                               installed: installed, reason: reason, sources: sources)
        }
        let presets = [
            NativePreset(id: "essentials-8", name: "Essentials · 8 GB", tierGB: 8,
                         summary: "What an 8 GB Mac needs to write, draw, narrate, transcribe, read pictures, search meaning and score.",
                         installedCount: 9, packageCount: 9, complete: true, fitsThisMac: true,
                         toCopyBytes: 0, freeAfterBytes: 380_200_000_000, refused: nil,
                         packageIDs: ["whisper-turbo"]),
            NativePreset(id: "creator-16", name: "Creator · 16 GB", tierGB: 16,
                         summary: "Adds the larger image model, the writing model, a better captioner, the second voice and the 16 GB voice tier.",
                         installedCount: 9, packageCount: 14, complete: false, fitsThisMac: true,
                         toCopyBytes: 22_500_000_000, freeAfterBytes: 357_700_000_000, refused: nil,
                         packageIDs: ["whisper-turbo"]),
            NativePreset(id: "creator-plus-24", name: "Creator Plus · 24 GB", tierGB: 24,
                         summary: "Adds music generation and Gemma 3 12B for longer writing.",
                         installedCount: 0, packageCount: 16, complete: false, fitsThisMac: false,
                         toCopyBytes: 64_200_000_000, freeAfterBytes: 316_000_000_000, refused: nil,
                         packageIDs: []),
            NativePreset(id: "studio-pro-32", name: "Studio Pro · 32 GB+", tierGB: 32,
                         summary: "Adds the largest writing models: Qwen3.6 35B and Gemma 4 26B.",
                         installedCount: 0, packageCount: 18, complete: false, fitsThisMac: true,
                         toCopyBytes: 100_300_000_000, freeAfterBytes: 284_000_000_000,
                         refused: "Not enough free space on /Volumes/KH-STUDIO-ATLAS/model-target: copying 100.3 GB would leave 284.0 GB, and this Mac keeps 5.0 GB free for working room.",
                         packageIDs: [])
        ]
        let groups = [
            NativeInstallGroup(job: "image", models: [
                model("z-image-turbo-mflux-4bit", "Z-Image-Turbo 6B", "image",
                      "Fast pictures from words; the everyday image model on a small Mac.",
                      "MLX 4-bit", 5.9, 8, installed: true),
                model("qwen-image-2-1-turbo-mflux-q4", "Qwen Image 2.1 Turbo Low-RAM", "image",
                      "Higher-fidelity pictures in eight steps.", "MLX 4-bit", 10.0, 16),
                model("qwen-image-2-1-mlx-serve-4bit", "Qwen Image 2.1 (native MLX server)", "image",
                      "Qwen Image 2.1 through its native MLX server; 24 GB and up.",
                      "MLX 4-bit", 10.7, 24)]),
            NativeInstallGroup(job: "voice", models: [
                model("qwen3-tts-12hz-0-6b-base-4bit", "Qwen3-TTS 0.6B Base", "voice",
                      "Narration that clones a voice from a short clip.", "MLX 4-bit", 1.7, 8,
                      installed: true),
                model("breeze-tts-2-mlx-4bit", "Breeze TTS 2", "voice",
                      "A 16 GB-tier voice with a long-form section policy.", "MLX 4-bit", 3.0, 8)]),
            NativeInstallGroup(job: "writing", models: [
                model("qwen36-35b-4bit", "Qwen 3.6 35B-A3B", "writing",
                      "The largest writing model; 32 GB and up.", "MLX 4-bit", 20.4, 24,
                      reason: "Needs a Mac with at least 24 GB of memory; this one has 16 GB. Everything else installs; this model is left out.")]),
            NativeInstallGroup(job: "vision", models: [
                model("florence-2-large-ft", "Florence-2 large (ft)", "vision",
                      "Reads a picture with more detail.", "bf16", 1.6, 16,
                      sources: ["internet"])]),
            NativeInstallGroup(job: "transcription", models: [
                model("whisper-turbo", "Whisper turbo", "transcription",
                      "Transcribes audio and video; the default subtitle model.", nil, 1.6, 8,
                      installed: true)])
        ]
        workspace.seedInstallPlanForRendering(NativeInstallPlan(
            memoryGB: 16, library: "/Volumes/KH-STUDIO-ATLAS/model-target",
            diskPath: "/Volumes/KH-STUDIO-ATLAS/model-target", freeBytes: 380_200_000_000,
            diskMeasured: true, stopBytes: 5_000_000_000,
            presets: presets,
            startingPreset: "creator-16", groups: groups, problems: []))
    }

    @MainActor private static func applyRole(_ role: String, to workspace: NativeWorkspaceStore) {
        let wire = role.hasPrefix("node") ? "agent" : role
        let now = Date().timeIntervalSince1970
        let working = """
            {"job_id":"job-4821","modality":"image","model":"black-forest-labs/FLUX.2-klein-4B","label":"storystudio","started_at":\(now - 95)}
            """
        let voiceJob = """
            {"job_id":"job-77","modality":"voice","model":"mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit","label":"genstudio-kh","started_at":\(now - 640)}
            """
        workspace.controllerIdentity = decode(NativeControllerIdentity.self, """
            {"settings": {"role": "\(wire)", "site_id": "site-blue", "site_name": "Blue Room", "controller_id": "studio-mac-1503",
                          "machine_name": "studio-mac-1503-m4-24-256", "database_mode": "sqlite",
                          "fleet_credential_origin": "\(wire == "agent" ? "adopted" : "minted")"},
             "readiness": {"ready": true, "role": "\(wire)"}}
            """)
        workspace.controllerAddress = wire == "agent" ? "100.64.0.12:47873" : ""
        workspace.controllerReachable = wire == "agent" ? role != "node-offline" : nil
        switch wire {
        case "controller":
            workspace.fleetOverview = decode(NativeFleetOverview.self, """
                {"machines":[\(machineJSON("local", "This Mac", role: "controller", chip: "Apple M4", memory: 24, available: 11.8, disk: 188, total: 460, diskState: "green", jobs: 40, job: voiceJob)),
                   \(machineJSON("blue-1", "Blue Room 1", role: "agent", chip: "Apple M4", memory: 16, available: 9.8, disk: 142, total: 460, diskState: "green", jobs: 61, job: working, address: "100.64.0.21:47873")),
                   \(machineJSON("blue-2", "Blue Room 2", role: "agent", chip: "Apple M2", memory: 8, available: 1.2, disk: 21, total: 256, diskState: "amber", jobs: 30, version: "0.6.120", startOnLaunch: false, address: "100.64.0.22:47873")),
                   \(machineJSON("blue-3", "Blue Room 3", role: "agent", reachable: false, chip: "Apple M2", memory: 8, available: 0, disk: 0, total: 256, diskState: "unknown", jobs: 12, address: "100.64.0.23:47873"))],
                 "summary":{"total":4,"reachable":3,"unreachable":1},"role":"controller","roster_readable":true}
                """)
            workspace.fleetNodes = decode(NativeFleetInventory.self, """
                {"role":"controller","nodes":[\(nodeJSON("blue-1", "reachable", nil, seen: 4, now: now)),\(nodeJSON("blue-2", "reachable", nil, seen: 11, version: "0.6.120", now: now)),
                  \(nodeJSON("blue-3", "unreachable", "could not reach the node: All connection attempts failed", seen: 190000, now: now)),
                  \(nodeJSON("blue-4", "unauthorized", "that Mac refused this controller's credential", seen: 7200, now: now))],
                 "summary":{"total":4,"reachable":2,"unreachable":1,"error":0,"unauthorized":1,"removed":0}}
                """)
        case "agent":
            workspace.fleetOverview = decode(NativeFleetOverview.self, """
                {"machines":[\(machineJSON("local", "This Mac", role: "agent", chip: "Apple M4", memory: 24, available: 11.8, disk: 188, total: 460, diskState: "green", jobs: 40, job: working)),
                   \(machineJSON("c", "studio-mac-1500-m4-16-256-controller", role: "controller", reachable: role != "node-offline", chip: "Apple M4", memory: 16, available: 9.8, disk: 142, total: 460, diskState: "green", jobs: 61, address: "100.64.0.12:47873")),
                   \(machineJSON("blue-2", "Blue Room 2", role: "agent", chip: "Apple M2", memory: 8, available: 1.2, disk: 21, total: 256, diskState: "amber", jobs: 30, address: "100.64.0.22:47873"))],
                 "summary":{"total":3,"reachable":\(role == "node-offline" ? 2 : 3),"unreachable":\(role == "node-offline" ? 1 : 0)},"role":"agent",
                 "controller": {"machine": "c", "label": "studio-mac-1500-m4-16-256-controller", "role": "controller", "address": "100.64.0.12:47873", "state": "\(role == "node-offline" ? "unreachable" : "reachable")"},
                 "roster_readable": true}
                """)
            workspace.fleetNodes = decode(NativeFleetInventory.self, """
                {"role":"agent","nodes":[],"summary":{"total":0,"reachable":0,"unreachable":0,"error":0}}
                """)
        default:
            workspace.fleetOverview = decode(NativeFleetOverview.self, """
                {"machines":[\(machineJSON("local", "This Mac", role: "standalone", chip: "Apple M4", memory: 24, available: 11.8, disk: 188, total: 460, diskState: "green", jobs: 40))],
                 "summary":{"total":1,"reachable":1,"unreachable":0},"role":"standalone"}
                """)
            workspace.fleetNodes = decode(NativeFleetInventory.self, """
                {"role":"standalone","nodes":[],"summary":{"total":0,"reachable":0,"unreachable":0,"error":0}}
                """)
        }
    }

    /// The Render page's own list, in the shapes the lane really publishes: a finished render with
    /// its file still here, one from a node, one accepted before this build recorded a start (so no
    /// length and no wall time — the row must say "unknown", not 0), a failed one, a cancelled one
    /// and one still rendering. Only in the throwaway copy render-page.sh builds; no Hub is
    /// contacted and no real state is read.
    @MainActor private static func seedRenders(_ workspace: NativeWorkspaceStore) {
        let now = Date().timeIntervalSince1970
        let hash = String(repeating: "4f", count: 32)
        let profile = String(repeating: "9c", count: 32)
        // Small helpers rather than closures inside the template: a nested interpolation with a
        // closure in it is more than the type checker can be asked to prove.
        func text(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        func number(_ value: Double?) -> String { value.map { "\($0)" } ?? "null" }
        func render(_ id: String, _ state: String, _ outcome: String, machine: String,
                    machineName: String?, created: Double, started: Double?, finished: Double?,
                    wall: Double?, running: Double?, video: Double?, width: Double?,
                    height: Double?, fps: Double?, bytes: Double?, failure: String?,
                    file: String?) -> String {
            """
            {"id":"\(id)","state":"\(state)","outcome":"\(outcome)","purpose":"production",
             "machine":"\(machine)","machine_name":\(text(machineName)),
             "created_at":\(created),"started_at":\(number(started)),
             "finished_at":\(number(finished)),"wall_seconds":\(number(wall)),
             "running_seconds":\(number(running)),"video_seconds":\(number(video)),
             "width":\(number(width)),"height":\(number(height)),"frame_rate":\(number(fps)),
             "container":"mp4","codec":"h264","effort":"hardware","crf":23,"tier":"hardware-h264",
             "output_bytes":\(number(bytes)),"output_sha256":\(text(bytes == nil ? nil : hash)),
             "profile_digest":"\(profile)",
             "story_uuid":"6d0b8f2e-8a51-4a63-9f0e-1c2b3a4d5e6f","intent_id":"intent-\(id.prefix(8))",
             "attempt_no":1,"gateway_attempt_key":"gateway-\(id.prefix(8))",
             "dispatch_key":"gateway:\(id.prefix(8))","failure":\(text(failure)),
             "local_file":\(text(file))}
            """
        }
        let rows = [
            render("9e1f2c7a-4b3d-4a1e-9c44-2a7ee1b93d51", "output_ready", "succeeded",
                   machine: "local", machineName: "This Mac", created: now - 3_900,
                   started: now - 3_780, finished: now - 3_640, wall: 140, running: nil,
                   video: 312, width: 1920, height: 1080, fps: 30, bytes: 24_800_000,
                   failure: nil, file: "/tmp/studiohub-render-fixture.mp4"),
            render("2b7c5d33-77a1-45e2-9a10-6f4c1d2e3b8a", "output_ready", "succeeded",
                   machine: "blue-1", machineName: "Blue Room 1", created: now - 9_000,
                   started: now - 8_950, finished: now - 8_889, wall: 61, running: nil,
                   video: 94, width: 1080, height: 1920, fps: 60, bytes: 8_400_000,
                   failure: nil, file: nil),
            // Accepted before this build recorded a start: the Hub states no length and no wall
            // time for it, and the page has to say so rather than print a 0.
            render("c41d0e88-1f52-4c6b-8e27-3a9b5c6d7e1f", "output_ready", "succeeded",
                   machine: "blue-1", machineName: "Blue Room 1", created: now - 40 * 3_600,
                   started: nil, finished: nil, wall: nil, running: nil, video: nil,
                   width: 1920, height: 1080, fps: 30, bytes: 21_300_000, failure: nil, file: nil),
            render("5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d", "failed", "failed",
                   machine: "blue-2", machineName: "Blue Room 2", created: now - 7_200,
                   started: now - 7_150, finished: now - 7_120, wall: 30, running: nil,
                   video: 300, width: 1920, height: 1080, fps: 30, bytes: nil,
                   failure: "ffmpeg exited with code 1.", file: nil),
            render("7f8e9d0c-1b2a-4c3d-8e4f-5a6b7c8d9e0f", "cancelled", "cancelled",
                   machine: "local", machineName: "This Mac", created: now - 15_000,
                   started: now - 14_940, finished: now - 14_900, wall: 40, running: nil,
                   video: 240, width: 1920, height: 1080, fps: 30, bytes: nil,
                   failure: "Cancelled before the output was accepted.", file: nil),
            render("1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f", "rendering", "running",
                   machine: "local", machineName: "This Mac", created: now - 95,
                   started: now - 95, finished: nil, wall: nil, running: 95, video: 480,
                   width: 1920, height: 1080, fps: 30, bytes: nil, failure: nil, file: nil),
        ]
        workspace.renderHistory = decode(NativeRenderHistory.self, """
            {"role":"controller","retention_days":90,
             "renders":[\(rows.joined(separator: ","))],
             "totals":{"processed":6,"succeeded":3,"failed":1,"cancelled":1,"running":1,
                       "output_seconds":406,"output_minutes":6.766666666666667,"unknown_duration":1,
                       "measured_renders":2,"measured_output_seconds":406,
                       "measured_wall_seconds":201,"average_wall_seconds":100.5,
                       "speed":2.019900497512438},
             "machines":[{"machine":"local","machine_name":"This Mac","renders":4,"ok":true,"error":null},
                         {"machine":"blue-1","machine_name":"Blue Room 1","renders":3,"ok":true,"error":null},
                         {"machine":"blue-2","machine_name":"Blue Room 2","renders":1,"ok":false,
                          "error":"that Mac did not answer"}]}
            """)
    }

    /// The Models, Voices and Connections fixtures: a small library, five saved voices and a controller
    /// with four Macs in different conditions. Only in the throwaway copy render-page.sh builds.
    @MainActor private static func seedLibrary(_ workspace: NativeWorkspaceStore) {
        func model(_ repo: String, _ label: String, _ family: String, _ function: StudioFunction, cached: Bool,
                   size: Double, block: String? = nil, memory: String? = nil) -> NativeModel {
            var item = NativeModel(id: repo, repo: repo, label: label, family: family, function: function,
                                   cached: cached, supported: block == nil, supportNote: "", sizeGB: size,
                                   capabilities: ["clone", "design"], languages: ["en", "es", "ja"],
                                   runtimeReady: true, supportsWordTimestamps: function == .transcription)
            item.blockReason = block
            item.memoryWarning = memory
            return item
        }
        workspace.models = [
            model("mlx-community/Kokoro-82M-bf16", "Kokoro 82M bf16", "Kokoro", .audio, cached: true, size: 0.4),
            model("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit", "Qwen3 TTS 0.6B Base 4-bit", "Qwen3-TTS", .audio, cached: true, size: 1.7),
            model("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16", "Qwen3 TTS 1.7B Base bf16", "Qwen3-TTS", .audio, cached: false, size: 4.6,
                  memory: "This Mac has 16 GB; this model is qualified at 24 GB."),
            model("mlx-community/whisper-large-v3-turbo", "Whisper large v3 turbo", "Whisper", .transcription, cached: true, size: 1.6),
            model("mlx-community/whisper-small-mlx", "Whisper small", "Whisper", .transcription, cached: true, size: 0.5),
            model("black-forest-labs/FLUX.2-klein-4B", "FLUX.2 Klein 4B MLX 4-bit", "FLUX", .image, cached: true, size: 8.0),
            model("Tongyi-MAI/Z-Image-Turbo", "Z-Image-Turbo 6B MLX 4-bit", "Z-Image", .image, cached: false, size: 12.4,
                  block: "Needs 24 GB of memory; this Mac has 16 GB."),
            model("stabilityai/stable-audio-3-optimized", "Stable Audio 3", "Stable Audio", .music, cached: true, size: 6.1),
            model("vanch007/mlx-Yue2-3B", "YuE 2 3B", "YuE", .music, cached: false, size: 7.2),
            model("mlx-community/Qwen3-4B-Instruct-4bit", "Qwen3 4B Instruct 4-bit", "Qwen3", .chat, cached: true, size: 2.4),
            model("mlx-community/Qwen3.5-9B-4bit", "Qwen3.5 9B 4-bit", "Qwen3.5", .chat, cached: false, size: 6.0,
                  memory: "Close other apps before loading."),
        ]
        workspace.voices = [
            ("Marlowe", "en", "f", true, 42.0), ("Ines", "es", "f", true, 31.5), ("Okafor", "en", "m", true, 58.2),
            ("Hana", "ja", "f", false, 19.0), ("Brightwater (narrator)", "en", "m", true, 120.4),
        ].enumerated().map { index, row in
            decode(NativeVoice.self, """
                {"id":"voice-\(index)","name":"\(row.0)","language":"\(row.1)","gender":"\(row.2)","notes":"Studio reference, room tone removed.",
                 "license":"self-owned","permission_acknowledged":true,"has_transcript":\(row.3),"transcript":\(row.3 ? "\"The quick brown fox jumps over the lazy dog.\"" : "null"),
                 "audio_extension":"wav","duration_seconds":\(row.4),"sample_rate":24000,"channels":1,"fleet_managed":\(index % 2 == 0)}
                """)
        }
        workspace.hubVersion = decode(NativeVersion.self, #"{"version":"0.6.126","build":"a1b2c3d","source":"app"}"#)
        // One job in every live or failed state, so the badges and progress lines are looked at too.
        func live(_ id: String, _ state: String, _ model: String, _ prompt: String, progress: Double = 0,
                  stage: String? = nil, error: String? = nil, stalled: Double? = nil) -> NativeJob {
            var item = NativeJob(id: id, state: state, progress: progress, text: prompt, model: model,
                                 createdAt: Date().timeIntervalSince1970 - 90, outputURL: nil, error: error)
            item.progressStage = stage
            item.stalledSeconds = stalled
            item.machine = "local"
            return item
        }
        workspace.imageJobs = [
            live("image-run", "running", "black-forest-labs/FLUX.2-klein-4B", "A lighthouse in a storm, long exposure",
                 progress: 0.45, stage: "DiT sampling", stalled: 140),
            live("image-queue", "queued", "black-forest-labs/FLUX.2-klein-4B", "A quiet harbour at first light"),
            live("image-fail", "error", "black-forest-labs/FLUX.2-klein-4B", "A glass city under a copper sky",
                 error: "The worker ran out of memory while loading the model."),
            live("image-unsure", "uncertain", "black-forest-labs/FLUX.2-klein-4B", "A red kite over a salt flat"),
        ] + workspace.imageJobs
        workspace.audioJobs = [
            live("audio-run", "running", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit", "Welcome back. Your previous session is exactly where you left it.",
                 progress: 0.6, stage: "section 3 of 5"),
        ] + workspace.audioJobs
        // A model chosen on every page, so the primary buttons render enabled, as they do on a Mac that has one.
        workspace.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"
        workspace.transcriptionModel = "mlx-community/whisper-large-v3-turbo"
        workspace.imageModel = "black-forest-labs/FLUX.2-klein-4B"
        if let index = workspace.models.firstIndex(where: { $0.repo == workspace.imageModel }) {
            workspace.models[index].imageModes = [
                NativeImageMode(id: "txt2img", label: "Text to image", available: true, reason: nil, references: nil),
                NativeImageMode(id: "edit", label: "Reference to image", available: true, reason: nil,
                    references: NativeImageReferences(min: 1, max: 2, maxDimension: 1024,
                        mediaTypes: ["image/png", "image/jpeg", "image/webp"]))
            ]
        }
        workspace.musicModel = "stabilityai/stable-audio-3-optimized"
        workspace.chatModel = "mlx-community/Qwen3-4B-Instruct-4bit"
        applyRole("controller", to: workspace)
    }

    private static let longPrompts = [
        "A sweeping cinematic landscape of a lighthouse on a basalt cliff at dusk, storm clouds breaking over a copper sea, volumetric god rays, a lone fishing boat returning along the Play of light across the swell, ultra detailed, 35mm film grain",
        "Lo-fi hip hop beat with warm vinyl crackle, mellow Rhodes chords, a slow brushed snare and a walking upright bass, rain on a window in the background, nostalgic and sleepy, perfect for studying late at night along the Play button of a long playlist",
        "Portrait of an elderly clockmaker in a cluttered workshop, brass gears and loose springs on every surface, soft window light, shallow depth of field",
        "Short",
        "An orchestral cue that begins with solo cello, builds through woodwinds and muted brass into a full-string climax, then resolves quietly on a single harp note; slow, hopeful, cinematic, suitable for a documentary ending",
    ]

    @MainActor private static func seedFixtures(_ workspace: NativeWorkspaceStore, scratch: URL) {
        // A picture with some structure, so a fit-to-window view shows its own edges.
        let size = NSSize(width: 1536, height: 1024)
        let picture = NSImage(size: size)
        picture.lockFocus()
        NSGradient(colors: [NSColor(calibratedRed: 0.08, green: 0.12, blue: 0.35, alpha: 1),
                            NSColor(calibratedRed: 0.95, green: 0.55, blue: 0.25, alpha: 1)])!
            .draw(in: NSRect(origin: .zero, size: size), angle: 70)
        NSColor.white.withAlphaComponent(0.85).setFill()
        NSBezierPath(ovalIn: NSRect(x: 1000, y: 620, width: 220, height: 220)).fill()
        NSColor.black.withAlphaComponent(0.55).setFill()
        NSBezierPath(rect: NSRect(x: 0, y: 0, width: size.width, height: 260)).fill()
        picture.unlockFocus()
        let png = NSBitmapImageRep(data: picture.tiffRepresentation!)!.representation(using: .png, properties: [:])!
        let path = scratch.appendingPathComponent("fixture.png").path
        try? png.write(to: URL(fileURLWithPath: path))
        setenv("STUDIOHUB_RENDER_IMAGE", path, 1)

        func job(_ id: String, _ prompt: String, _ model: String, state: String = "done",
                 seconds: Double? = nil) -> NativeJob {
            var item = NativeJob(id: id, state: state, progress: 1, text: prompt, model: model,
                                 createdAt: Date().timeIntervalSince1970 - 600, outputURL: nil, error: nil)
            item.bytes = 1_843_200
            item.sha256 = String(repeating: "a", count: 64)
            item.durationSeconds = seconds
            item.generationSeconds = 41
            item.machine = "local"
            item.artifactPath = "/tmp"
            return item
        }
        workspace.imageJobs = longPrompts.enumerated().map { index, prompt in
            var item = job("image-\(index)", prompt, "black-forest-labs/FLUX.2-klein-4B")
            item.requestedWidth = 1536
            item.requestedHeight = 1024
            item.imageSettings = NativeImageSettings(width: 1536, height: 1024, steps: 4, guidance: 1,
                                                     seed: Int64(48_201 + index))
            return item
        }
        workspace.musicJobs = longPrompts.enumerated().map { index, prompt in
            var item = job("music-\(index)", prompt,
                           index % 2 == 0 ? "stabilityai/stable-audio-3-optimized" : "vanch007/mlx-Yue2-3B", seconds: 94)
            item.musicSettings = NativeMusicSettings(durationSeconds: 90, steps: 8, guidanceScale: 7,
                                                     shift: 3, seed: Int64(7_700 + index),
                                                     instrumental: nil, lyrics: nil, vocalLanguage: nil)
            return item
        }
        workspace.audioJobs = longPrompts.prefix(3).enumerated().map { index, prompt in
            var item = job("audio-\(index)", prompt, "kokoro/Kokoro-82M", seconds: 18)
            item.voiceSettings = nil
            return item
        }
    }

    /// `whats-new` reads better than the raw `What's New`, which is also a filename hazard.
    private static func fileName(for page: StudioPage) -> String {
        page == .whatsNew ? "whats-new" : page.rawValue.lowercased().replacingOccurrences(of: " ", with: "-")
    }

    /// `subtitle` and `transcription` are the same page; its raw value is the stable key.
    private static func pageNamed(_ name: String) -> StudioPage {
        let wanted = name.lowercased()
        if wanted == "subtitle" { return .transcription }
        // The Set up page's raw value is "Set up this Mac"; `setup` is what a command line reads.
        if wanted == "setup" || wanted == "set-up" || wanted == "set-up-this-mac" { return .setup }
        if wanted == "whatsnew" || wanted == "whats-new" { return .whatsNew }
        return StudioPage.allCases.first { $0.rawValue.lowercased() == wanted } ?? .overview
    }
}
