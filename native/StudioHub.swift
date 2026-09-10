import AppKit
import SwiftUI
import ServiceManagement
import Carbon
import Combine
import Darwin

@MainActor
final class StudioController: ObservableObject {
    @Published var selection: StudioPage = .overview
    @Published var status = "Choose a model library"
    @Published var failure: String?
    @Published var readyURL: URL?
    @Published var starting = false
    @Published var stopping = false
    @Published var canForceStop = false
    @Published var configuration: ShellConfiguration
    @Published var loginStatus = SMAppService.mainApp.status
    let workspace = NativeWorkspaceStore()
    private var worker: Process?
    private var logHandle: FileHandle?
    private var pollTask: Task<Void, Never>?
    private var instanceLock: InstanceLock?
    var onStopped: (() -> Void)?

    init(configuration: ShellConfiguration) {
        self.configuration = configuration
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
                throw ShellFailure(message: "The bundled local services are missing. Replace this app with a complete build.")
            }
            let log = try openPrivateLog(configuration.logFile)
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
            status = "Starting local services…"
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
                        self.status = "Local services ready"
                        await self.workspace.connect(baseURL: ready.url, dataDirectory: self.configuration.dataDirectory)
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
                                if !self.stopping { self.status = health["busy"] as? Bool == true ? "Local services working" : "Local services ready" }
                            } catch {
                                if !Task.isCancelled && !self.stopping { self.status = "Local services not responding" }
                            }
                            try? await Task.sleep(nanoseconds: 3_000_000_000)
                        }
                        return
                    }
                    try? await Task.sleep(nanoseconds: 500_000_000)
                }
                if process.isRunning {
                    self.failure = "The local services did not become ready within two minutes. Open its log, then stop it before retrying."
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
            status = "Unable to start local services"
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
        workspace.disconnect()
        try? logHandle?.close()
        logHandle = nil
        if expectedStop { status = "Local services stopped" }
        else { status = "Local services stopped unexpectedly"; failure = "The engine exited (code \(code)). Open its log for details, then try Start again." }
        onStopped?()
    }
    func stop() {
        guard let worker, worker.isRunning, !stopping else { return }
        pollTask?.cancel()
        stopping = true
        starting = false
        readyURL = nil
        workspace.disconnect()
        status = "Stopping local services…"
        worker.terminate()
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 15_000_000_000)
            guard let self, self.stopping, worker.isRunning else { return }
            self.status = "Local services are still stopping"
            self.canForceStop = true
            self.confirmForceStop()
        }
    }
    func confirmForceStop() {
        guard let worker, worker.isRunning, canForceStop else { return }
        let alert = NSAlert()
        alert.messageText = "The local services are still stopping"
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
        alert.messageText = "Stop the local services?"
        alert.informativeText = "Active generation, transcription, or model downloads may be interrupted. Leave the engine running to finish them."
        alert.addButton(withTitle: "Keep Running")
        alert.addButton(withTitle: "Stop Engine")
        if alert.runModal() == .alertSecondButtonReturn { stop() }
    }
    func select(_ value: StudioPage) { selection = value }
    func setOffline(_ value: Bool) {
        guard canConfigure else { return }
        configuration.offline = value
        do { try configuration.save() } catch { failure = error.localizedDescription }
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
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var controller: StudioController!
    var window: NSWindow!
    private var statusItem: NSStatusItem!
    private var quitPending = false
    private var statusObserver: AnyCancellable?
    private var failureObserver: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let config = try ShellConfiguration(arguments: CommandLine.arguments, bundle: Bundle.main.bundleURL)
            controller = StudioController(configuration: config)
        } catch {
            let alert = NSAlert(); alert.messageText = "Studio Hub could not open"; alert.informativeText = error.localizedDescription; alert.runModal(); NSApp.terminate(nil); return
        }
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1380, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Studio Hub"
        window.contentMinSize = NSSize(width: 1040, height: 680)
        window.setFrameAutosaveName("StudioHubMain")
        let content = NSHostingView(rootView: StudioWindow(studio: controller))
        content.sizingOptions = []
        window.contentView = content
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        let mainMenu = NSMenu()
        let applicationItem = NSMenuItem(); mainMenu.addItem(applicationItem)
        let applicationMenu = NSMenu(); applicationItem.submenu = applicationMenu
        applicationMenu.addItem(withTitle: "About Studio Hub", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        let settingsItem = NSMenuItem(title: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        settingsItem.target = self
        applicationMenu.addItem(settingsItem)
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
        failureObserver = controller.$failure.dropFirst().compactMap { $0 }.sink { [weak self] _ in self?.openWindow() }
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
    @objc func openSettings() { controller?.select(.settings); openWindow() }
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
