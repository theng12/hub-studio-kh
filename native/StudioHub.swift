import AppKit
import SwiftUI
import WebKit
import ServiceManagement
import Carbon
import Combine
import Darwin

@MainActor
final class StudioController: ObservableObject {
    @Published var selection: StudioFunction = .audio
    @Published var status = "Choose a model library"
    @Published var failure: String?
    @Published var readyURL: URL?
    @Published var starting = false
    @Published var stopping = false
    @Published var canForceStop = false
    @Published var configuration: ShellConfiguration
    @Published var loginStatus = SMAppService.mainApp.status
    let webView: WKWebView
    private var worker: Process?
    private var logHandle: FileHandle?
    private var pollTask: Task<Void, Never>?
    private var instanceLock: InstanceLock?
    var onStopped: (() -> Void)?

    init(configuration: ShellConfiguration) {
        self.configuration = configuration
        let webConfiguration = WKWebViewConfiguration()
        // Preview keeps browser storage separate from all existing Studio installations.
        // Per-model browser presets remain session-only until the Hub settings flow is integrated.
        webConfiguration.websiteDataStore = .nonPersistent()
        // The existing workspace has its own opt-in Auto-play checkbox (off by default).
        webConfiguration.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: webConfiguration)
        webView.allowsBackForwardNavigationGestures = false
        do { instanceLock = try InstanceLock(directory: configuration.dataDirectory) }
        catch { failure = error.localizedDescription; status = "Unable to open" }
    }
    var running: Bool { worker?.isRunning == true }
    var canConfigure: Bool { !running && !starting && !stopping && instanceLock != nil }
    var hasLibrary: Bool { configuration.modelsDirectory != nil }

    func chooseLibrary() {
        guard canConfigure else { return }
        let panel = NSOpenPanel()
        panel.title = "Choose model library"
        panel.message = "Select your model library folder. For an existing Hugging Face cache, choose the folder containing ‘hub’."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            do {
                try ShellConfiguration.requireExternal(url, bundle: Bundle.main.bundleURL)
                try ShellConfiguration.requireDirectory(url)
                configuration.modelsDirectory = url.resolvingSymlinksInPath()
                try configuration.save()
                failure = nil
                start()
            } catch { failure = error.localizedDescription }
        }
    }
    func useDefaultLibrary() {
        guard canConfigure else { return }
        let url = configuration.dataDirectory.appendingPathComponent("models")
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            configuration.modelsDirectory = url
            try configuration.save()
            start()
        } catch { failure = error.localizedDescription }
    }
    func start() {
        guard canConfigure, let models = configuration.modelsDirectory else { return }
        do {
            try ShellConfiguration.requireDirectory(models)
            try configuration.save()
            try? FileManager.default.removeItem(at: configuration.readyFile)
            let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/StudioHubVoice")
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw ShellFailure(message: "The bundled audio engine is missing. Replace this app with a complete build.")
            }
            if !FileManager.default.fileExists(atPath: configuration.logFile.path) {
                FileManager.default.createFile(atPath: configuration.logFile.path, contents: nil, attributes: [.posixPermissions: 0o600])
            }
            let log = try FileHandle(forWritingTo: configuration.logFile)
            try log.seekToEnd()
            logHandle = log
            let process = Process()
            process.executableURL = executable
            process.arguments = ["--data-dir", configuration.dataDirectory.path,
                                 "--models-dir", models.path, "--ready-file", configuration.readyFile.path,
                                 "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
                + (configuration.offline ? ["--offline"] : [])
            process.currentDirectoryURL = configuration.dataDirectory
            process.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"]
            process.standardOutput = log
            process.standardError = log
            process.terminationHandler = { [weak self] process in
                let code = process.terminationStatus
                Task { @MainActor in self?.workerExited(code: code) }
            }
            failure = nil
            starting = true
            status = "Starting audio engine…"
            worker = process
            try process.run()
            pollTask = Task { [weak self] in
                guard let self else { return }
                for _ in 0..<240 {
                    if Task.isCancelled || !process.isRunning { return }
                    if let data = try? Data(contentsOf: self.configuration.readyFile),
                       let ready = try? WorkerReady.decode(data, expectedPID: process.processIdentifier) {
                        guard !Task.isCancelled, !self.stopping, process.isRunning else { return }
                        self.starting = false
                        self.readyURL = ready.url
                        self.status = "Audio engine ready"
                        self.showRoute(self.selection.route ?? "generate")
                        let session = URLSession(configuration: .ephemeral)
                        defer { session.invalidateAndCancel() }
                        while !Task.isCancelled && process.isRunning && !self.stopping {
                            do {
                                var request = URLRequest(url: ready.url.appendingPathComponent("api/health"))
                                request.timeoutInterval = 5
                                let (body, response) = try await session.data(for: request)
                                guard (response as? HTTPURLResponse)?.statusCode == 200,
                                      let health = try JSONSerialization.jsonObject(with: body) as? [String: Any],
                                      health["ok"] as? Bool == true else { throw ShellFailure(message: "Health check failed") }
                                if !self.stopping { self.status = health["busy"] as? Bool == true ? "Audio engine working" : "Audio engine ready" }
                            } catch {
                                if !Task.isCancelled && !self.stopping { self.status = "Audio engine not responding" }
                            }
                            try? await Task.sleep(nanoseconds: 3_000_000_000)
                        }
                        return
                    }
                    try? await Task.sleep(nanoseconds: 500_000_000)
                }
                if process.isRunning {
                    self.failure = "The audio engine did not become ready within two minutes. Open its log, then stop it before retrying."
                    self.status = "Startup needs attention"
                    self.starting = false
                }
            }
        } catch {
            worker = nil
            starting = false
            try? logHandle?.close()
            logHandle = nil
            failure = error.localizedDescription
            status = "Unable to start audio engine"
        }
    }
    private func workerExited(code: Int32) {
        pollTask?.cancel()
        let expectedStop = stopping
        worker = nil
        readyURL = nil
        starting = false
        stopping = false
        canForceStop = false
        webView.loadHTMLString("", baseURL: nil)
        try? logHandle?.close()
        logHandle = nil
        if expectedStop { status = "Audio engine stopped" }
        else { status = "Audio engine stopped unexpectedly"; failure = "The engine exited (code \(code)). Open its log for details, then try Start again." }
        onStopped?()
    }
    func stop() {
        guard let worker, worker.isRunning, !stopping else { return }
        pollTask?.cancel()
        stopping = true
        starting = false
        readyURL = nil
        webView.loadHTMLString("", baseURL: nil)
        status = "Stopping audio engine…"
        worker.terminate()
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 15_000_000_000)
            guard let self, self.stopping, worker.isRunning else { return }
            self.status = "Audio engine is still stopping"
            self.canForceStop = true
            self.confirmForceStop()
        }
    }
    func confirmForceStop() {
        guard let worker, worker.isRunning, canForceStop else { return }
        let alert = NSAlert()
        alert.messageText = "The audio engine is still stopping"
        alert.informativeText = "You can keep waiting or force it to stop. Force Stop may leave an incomplete output or download; it will not restart the job."
        alert.addButton(withTitle: "Keep Waiting")
        alert.addButton(withTitle: "Force Stop")
        if alert.runModal() == .alertSecondButtonReturn, worker.isRunning {
            let pid = worker.processIdentifier
            // Only the still-owned worker's isolated group may be signalled.
            if getpgid(pid) == pid { killpg(pid, SIGKILL) }
            else { kill(pid, SIGKILL) }
        }
    }
    func confirmStop() {
        let alert = NSAlert()
        alert.messageText = "Stop the audio engine?"
        alert.informativeText = "Active generation, transcription, or model downloads may be interrupted. Leave the engine running to finish them."
        alert.addButton(withTitle: "Keep Running")
        alert.addButton(withTitle: "Stop Engine")
        if alert.runModal() == .alertSecondButtonReturn { stop() }
    }
    func select(_ value: StudioFunction) {
        selection = value
        if let route = value.route { showRoute(route) }
    }
    func showRoute(_ route: String) {
        guard let readyURL else { return }
        var components = URLComponents(url: readyURL, resolvingAgainstBaseURL: false)!
        components.fragment = route
        guard let url = components.url else { return }
        if webView.url?.host == readyURL.host && webView.url?.port == readyURL.port {
            // Same page keeps editor contents and upload state when switching functions.
            let encoded = String(data: try! JSONEncoder().encode(route), encoding: .utf8)!
            webView.evaluateJavaScript("window.location.hash = \(encoded)", completionHandler: nil)
        } else { webView.load(URLRequest(url: url)) }
    }
    func setLogin(_ enabled: Bool) {
        do {
            if enabled { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
            loginStatus = SMAppService.mainApp.status
            if loginStatus == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
        } catch { failure = "Could not change login startup: \(error.localizedDescription)"; loginStatus = SMAppService.mainApp.status }
    }
    func openLog() {
        if FileManager.default.fileExists(atPath: configuration.logFile.path) { NSWorkspace.shared.open(configuration.logFile) }
        else { NSWorkspace.shared.open(configuration.dataDirectory) }
    }
}

@MainActor
final class BrowserDelegate: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    weak var studio: StudioController?
    init(studio: StudioController) { self.studio = studio }
    private func isLocal(_ url: URL) -> Bool {
        guard let base = studio?.readyURL else { return false }
        return WorkerOrigin.allows(url, base: base)
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.absoluteString == "about:blank" { decisionHandler(.allow); return }
        if isLocal(url) { decisionHandler(action.shouldPerformDownload ? .download : .allow); return }
        if action.navigationType == .linkActivated && ["http", "https"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = response.response.url, isLocal(url) || url.absoluteString == "about:blank" else {
            decisionHandler(.cancel); return
        }
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        panel.begin { result in completionHandler(result == .OK ? panel.url : nil) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { studio?.failure = "Download failed: \(error.localizedDescription)" }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.begin { result in completionHandler(result == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.addButton(withTitle: "OK"); alert.runModal(); completionHandler()
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.addButton(withTitle: "Cancel"); alert.addButton(withTitle: "Continue"); completionHandler(alert.runModal() == .alertSecondButtonReturn)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { studio?.failure = "The Studio interface stopped responding. Reopen Audio or Transcription to reload it; the engine continues running."; webView.loadHTMLString("", baseURL: nil) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { studio?.failure = "Could not load the Studio interface: \(error.localizedDescription)" }
    }
}

struct StudioBrowser: NSViewRepresentable {
    @ObservedObject var studio: StudioController
    func makeCoordinator() -> BrowserDelegate { BrowserDelegate(studio: studio) }
    func makeNSView(context: Context) -> WKWebView {
        studio.webView.navigationDelegate = context.coordinator
        studio.webView.uiDelegate = context.coordinator
        return studio.webView
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

struct StudioWindow: View {
    @ObservedObject var studio: StudioController
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 20) {
                Text("Studio Hub").font(.title2.weight(.semibold)).padding(.top, 12)
                VStack(spacing: 4) {
                    ForEach(StudioFunction.allCases) { item in
                        Button { studio.select(item) } label: {
                            HStack {
                                Label(item.rawValue, systemImage: item.symbol)
                                Spacer()
                                if item.route == nil { Image(systemName: "circle.dashed").foregroundStyle(.secondary).help("Not installed in this build") }
                            }
                            .padding(.horizontal, 10).padding(.vertical, 10)
                            .contentShape(Rectangle())
                            .background(studio.selection == item ? Color.accentColor.opacity(0.16) : Color.clear, in: RoundedRectangle(cornerRadius: 7))
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(studio.selection == item ? [.isSelected] : [])
                    }
                }
                Spacer()
                VStack(alignment: .leading, spacing: 10) {
                    Label(studio.status, systemImage: studio.readyURL == nil ? "circle" : "checkmark.circle")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Button("Open Engine Log") { studio.openLog() }
                    Button("Choose Model Folder…") { studio.chooseLibrary() }.disabled(!studio.canConfigure)
                    if studio.canForceStop { Button("Force Stop…") { studio.confirmForceStop() } }
                    else if studio.running { Button(studio.stopping ? "Stopping…" : "Stop Engine…") { studio.confirmStop() }.disabled(studio.stopping) }
                    else if studio.hasLibrary { Button("Start Engine") { studio.start() }.disabled(!studio.canConfigure) }
                    Toggle("Start at login", isOn: Binding(get: { studio.loginStatus == .enabled || studio.loginStatus == .requiresApproval }, set: { studio.setLogin($0) }))
                        .toggleStyle(.checkbox)
                    if studio.loginStatus == .requiresApproval { Button("Approve in System Settings") { SMAppService.openSystemSettingsLoginItems() } }
                }.font(.callout)
                Text("Local preview · Audio + Transcription").font(.caption).foregroundStyle(.secondary)
            }.padding(16).frame(width: 214).background(Color(nsColor: .windowBackgroundColor))
            Divider()
            VStack(spacing: 0) {
                if let failure = studio.failure {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle")
                        Text(failure).textSelection(.enabled)
                        Spacer()
                        Button("Dismiss") { studio.failure = nil }
                    }.padding().background(Color.orange.opacity(0.12))
                }
                ZStack {
                    // Retain one browser even on unavailable sections, so a draft or file upload is not discarded.
                    StudioBrowser(studio: studio)
                        .opacity(studio.selection.route != nil && studio.readyURL != nil ? 1 : 0)
                        .allowsHitTesting(studio.selection.route != nil && studio.readyURL != nil)
                        .accessibilityHidden(studio.selection.route == nil || studio.readyURL == nil)
                    if studio.selection.route == nil {
                        VStack(alignment: .leading, spacing: 16) {
                            Label(studio.selection.rawValue, systemImage: studio.selection.symbol).font(.largeTitle.weight(.semibold))
                            Text("Not installed in this build").font(.title3)
                            Text("This function is part of the app plan. Its engine still needs to be bundled and tested on Apple Silicon.").foregroundStyle(.secondary)
                            Button("Open Audio") { studio.select(.audio) }.buttonStyle(.borderedProminent)
                        }.frame(maxWidth: 480, alignment: .leading).padding(40)
                    } else if studio.readyURL == nil {
                        VStack(alignment: .leading, spacing: 18) {
                            Text(studio.starting ? "Starting your audio engine" : studio.stopping ? "Stopping your audio engine" : "Your model library").font(.largeTitle.weight(.semibold))
                            Text("Audio and Transcription share one engine. Keep models on this Mac or an external drive; the app includes its own runtime.").foregroundStyle(.secondary)
                            if let models = studio.configuration.modelsDirectory {
                                Text(models.path).font(.callout).textSelection(.enabled)
                            }
                            if studio.starting || studio.stopping { ProgressView().controlSize(.small) }
                            else {
                                HStack {
                                    if studio.hasLibrary { Button("Start Engine") { studio.start() }.buttonStyle(.borderedProminent).disabled(!studio.canConfigure) }
                                    else { Button("Use Default Folder") { studio.useDefaultLibrary() }.buttonStyle(.borderedProminent).disabled(!studio.canConfigure) }
                                    Button("Choose Folder…") { studio.chooseLibrary() }.disabled(!studio.canConfigure)
                                }
                                Toggle("Use imported models offline", isOn: Binding(get: { studio.configuration.offline }, set: { studio.configuration.offline = $0 })).disabled(!studio.canConfigure)
                                Text("Existing libraries: choose the folder containing ‘hub’. With an empty library, use Models in the Studio to download a supported model.").font(.callout).foregroundStyle(.secondary)
                            }
                        }.frame(maxWidth: 560, alignment: .leading).padding(40)
                    }
                }
            }
        }
        .frame(minWidth: 1040, minHeight: 680)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var controller: StudioController!
    var window: NSWindow!
    private var statusItem: NSStatusItem!
    private var quitPending = false
    private var statusObserver: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let config = try ShellConfiguration(arguments: CommandLine.arguments, bundle: Bundle.main.bundleURL)
            controller = StudioController(configuration: config)
        } catch {
            let alert = NSAlert(); alert.messageText = "Studio Hub could not open"; alert.informativeText = error.localizedDescription; alert.runModal(); NSApp.terminate(nil); return
        }
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1380, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Studio Hub"
        window.setFrameAutosaveName("StudioHubMain")
        window.contentView = NSHostingView(rootView: StudioWindow(studio: controller))
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        let mainMenu = NSMenu()
        let applicationItem = NSMenuItem(); mainMenu.addItem(applicationItem)
        let applicationMenu = NSMenu(); applicationItem.submenu = applicationMenu
        applicationMenu.addItem(withTitle: "About Studio Hub", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "Quit Studio Hub", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "Edit"); editItem.submenu = editMenu
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let windowItem = NSMenuItem(); mainMenu.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window"); windowItem.submenu = windowMenu
        windowMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        NSApp.mainMenu = mainMenu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "Studio Hub")
        statusItem.button?.toolTip = "Studio Hub"
        let menu = NSMenu()
        let openItem = NSMenuItem(title: "Open Studio Hub", action: #selector(openWindow), keyEquivalent: ""); openItem.target = self; menu.addItem(openItem)
        let logItem = NSMenuItem(title: "Open Engine Log", action: #selector(openLog), keyEquivalent: ""); logItem.target = self; menu.addItem(logItem)
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Studio Hub", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = menu
        let stateItem = NSMenuItem(title: controller.status, action: nil, keyEquivalent: "")
        menu.insertItem(stateItem, at: 0)
        menu.insertItem(.separator(), at: 1)
        statusObserver = controller.$status.sink { [weak self] status in
            self?.statusItem.button?.toolTip = "Studio Hub — " + status
            stateItem.title = status
        }
        controller.onStopped = { [weak self] in
            if self?.quitPending == true { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        let loginLaunch = NSAppleEventManager.shared().currentAppleEvent?
            .paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
        if !loginLaunch || !controller.hasLibrary { openWindow() }
        if controller.hasLibrary && controller.canConfigure { controller.start() }
    }
    @objc func openWindow() { window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); controller?.loginStatus = SMAppService.mainApp.status }
    @objc func openLog() { controller?.openLog() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { openWindow(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard controller?.running == true else { return .terminateNow }
        if quitPending { return .terminateLater }
        let alert = NSAlert()
        alert.messageText = "Quit Studio Hub and stop its engine?"
        alert.informativeText = "Active generation, transcription, or downloads may be interrupted. Close the window to keep working in the background."
        alert.addButton(withTitle: "Keep Running")
        alert.addButton(withTitle: "Quit and Stop")
        guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
        quitPending = true
        controller.stop()
        return .terminateLater
    }
}

@main
struct StudioHubApplication {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
