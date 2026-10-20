import AppKit
import SwiftUI
#if canImport(ServiceManagement)
// Imported only for the one-time migration off the old login item; no other code may touch it.
import ServiceManagement
#endif
import Carbon
import Combine
import Darwin

/// The real old-registration reader, in one place. Kept behind injected closures on the
/// controller so the migration decision is testable without linking ServiceManagement.
enum LegacyLoginItem {
#if canImport(ServiceManagement)
    static func status() -> LoginMigration.OldStatus {
        switch SMAppService.mainApp.status {
        case .enabled: return .enabled
        case .requiresApproval: return .requiresApproval
        case .notRegistered: return .notRegistered
        case .notFound: return .notFound
        @unknown default: return .notFound
        }
    }
    static func unregister() throws { try SMAppService.mainApp.unregister() }
#else
    /// No ServiceManagement on this SDK: the old registration cannot be inspected, so the
    /// migration reports that it may still exist rather than pretending it was cleaned up.
    static func status() -> LoginMigration.OldStatus { .unavailable }
    static func unregister() throws {}
#endif
}

/// The app-side actions for a redundant launch, kept out of `ExistingInstance` so the decision and
/// its present/exit contract stay testable in the contract suite.
enum ExistingInstanceHandler {
    /// Bring the already-running Studio Hub forward, if it can be found. Both copies share the
    /// bundle identifier, so the other one is the process that is not this one.
    static func presentRunningApp() {
        guard let bundleID = Bundle.main.bundleIdentifier else { return }
        let current = ProcessInfo.processInfo.processIdentifier
        for app in NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
        where app.processIdentifier != current {
            app.activate(options: [.activateAllWindows])
            return
        }
    }

    /// Leave with status 0 so `KeepAlive` (SuccessfulExit false) never relaunches this copy, and
    /// leave at most one line where a log collects it. No alert, ever: the operator should not see
    /// "Studio Hub could not open" because they double-clicked an app that is already open.
    static func noteAndExit() {
        FileHandle.standardError.write(Data("Studio Hub is already running; this launch exits.\n".utf8))
        exit(ExistingInstance.redundantLaunchExitCode)
    }
}

@MainActor
final class StudioController: ObservableObject {
    @Published var selection: StudioPage = .overview
    @Published var status = "Choose a model library"
    @Published var failure: String?
    @Published var readyURL: URL?
    /// The credential this app already holds for its own Hub, so Connections can reveal it
    /// on request. It is never written to the status line, a log, or a URL.
    @Published private(set) var connectionToken: String?
    /// The scoped credential a controller is given. It can send work to this Mac and read the
    /// results, and cannot change its settings or delete its output - which is why it, and not
    /// the Hub token, is what an operator should hand to another Mac.
    @Published private(set) var fleetToken: String?
    @Published var starting = false
    @Published var stopping = false
    /// Set when the operator asked for the services to come back up after a stop, so the settings
    /// they just changed take effect without them having to find Start afterwards.
    private var restartAfterStop = false
    /// True while a change that needs a restart is waiting for one.
    @Published var restartPending = false
    @Published var canForceStop = false
    @Published var configuration: ShellConfiguration
    /// Whether the launch agent that starts the app at login and relaunches it after a crash is
    /// in place. Read from the plist and launchd, never remembered from the toggle press.
    @Published var loginStatus: LoginAgentStatus = .disabled
    /// What the operator last did to the fleet address, so the choice reports itself.
    @Published var fleetNote: String?
    let workspace = NativeWorkspaceStore()
    private var worker: Process?
    private var logHandle: FileHandle?
    private var pollTask: Task<Void, Never>?
    /// Addresses the operator chose that this Mac did not have at the moment the services were
    /// started. Stored rather than merely reported, because the supervisor below acts on it: a
    /// headless Mac that boots before its network comes up binds loopback only and is then
    /// unreachable from every node, and the fix is to restart once the address exists. Empty in
    /// the ordinary case, which is why nothing here costs a running Hub anything.
    private var addressesMissingAtStart: [String] = []
    private var restartTask: Task<Void, Never>?
    private var forceStopTask: Task<Void, Never>?
    private var startLadder = ServiceStartLadder()
    /// Which recovery phase the persistent banner has already been told about, so the banner
    /// distinguishes the fast ladder from the slow retry without re-opening the window every time
    /// the next-attempt time changes.
    private enum RecoveryPhase { case fast, slow }
    private var recoveryReportedPhase: RecoveryPhase?
    /// The concrete reason the last automatic start failed, kept so the slow-phase message can
    /// name it even after the banner has been replaced by the phase wording.
    private var lastServiceFailure: String?
    private var healthySince: Date?
    private var startupTimedOut = false
    private var instanceLock: InstanceLock?
    /// True when the data-folder lock is held by another copy of Studio Hub. The app then presents
    /// that copy and exits quietly instead of showing a failure.
    private(set) var existingInstanceDetected = false
    private let serviceExecutable: URL
    private let appBundle: URL
    private let agentPlistURL: URL
    private let loginAgentStatus: (URL) -> LoginAgentStatus
    private let injectedLoginAgentOperations: LoginAgentOperations?
    private let loginAgentCoordinator: LoginAgentMutationCoordinator
    /// The old `SMAppService` reader/unregister, injected so the migration can be exercised in
    /// tests without touching the real system. The default is the only place that calls it.
    private let legacyLoginItemStatus: () -> LoginMigration.OldStatus
    private let legacyLoginItemUnregister: () throws -> Void
    private let now: () -> Date
    private let healthRequest: (URLRequest, URLSession) async throws -> (Data, URLResponse)
    var onStopped: (() -> Void)?
    /// Set by the app delegate: quit and reopen the (now replaced) app after an update.
    var onRelaunchRequested: (() -> Void)?

    init(configuration: ShellConfiguration, serviceExecutable: URL? = nil,
         appBundle: URL = Bundle.main.bundleURL,
         agentPlistURL: URL = LaunchAgent.defaultPlistURL(),
         loginAgentStatus: ((URL) -> LoginAgentStatus)? = nil,
         loginAgentOperations: LoginAgentOperations? = nil,
         loginAgentCoordinator: LoginAgentMutationCoordinator = .shared,
         legacyLoginItemStatus: (() -> LoginMigration.OldStatus)? = nil,
         legacyLoginItemUnregister: (() throws -> Void)? = nil,
         now: @escaping () -> Date = Date.init,
         healthRequest: ((URLRequest, URLSession) async throws -> (Data, URLResponse))? = nil) {
        self.configuration = configuration
        self.serviceExecutable = serviceExecutable
            ?? Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/StudioHubService")
        self.appBundle = appBundle
        self.agentPlistURL = agentPlistURL
        let statusReader = loginAgentStatus ?? { LaunchAgent.status(at: $0) }
        self.loginAgentStatus = statusReader
        self.injectedLoginAgentOperations = loginAgentOperations
        self.loginAgentCoordinator = loginAgentCoordinator
        self.legacyLoginItemStatus = legacyLoginItemStatus ?? { LegacyLoginItem.status() }
        self.legacyLoginItemUnregister = legacyLoginItemUnregister ?? { try LegacyLoginItem.unregister() }
        self.now = now
        self.healthRequest = healthRequest ?? { request, session in try await session.data(for: request) }
        self.startLadder = ServiceStartLadder(maxFastAttempts: configuration.recoveryMaxAttempts,
                                              fastDelays: configuration.recoveryBackoffSeconds)
        self.loginStatus = statusReader(agentPlistURL)
        do { instanceLock = try InstanceLock(directory: configuration.dataDirectory) }
        catch let error as InstanceLockError {
            // "Already held" is not a failure: another copy of Studio Hub is the real app. The
            // launch path uses `existingInstanceDetected` to bring it forward and exit 0.
            switch error {
            case .alreadyHeld:
                existingInstanceDetected = true
            case .unavailable(let message):
                failure = message
                status = "Unable to open"
            }
        }
        catch { failure = error.localizedDescription; status = "Unable to open" }
        // The workspace knows when a join succeeded; only the app owns the preferences file the
        // answer has to be written to, so the two are wired together here rather than in a view.
        workspace.onControllerJoined = { [weak self] address in
            self?.rememberController(address)
        }
        // This Mac's own node credential is a secret with the same lifetime as the membership, so it
        // is written to the same preferences file — which `save()` writes 0600 in a 0700 directory —
        // and cleared when this Mac leaves the group.
        workspace.onNodeCredentialChanged = { [weak self] credential in
            self?.rememberNodeCredential(credential)
        }
        // A rotation, or a leave that mints this Mac's own token, replaces the value the page shows
        // beside the Copy button — so it is re-read from the Hub's state file rather than cached.
        workspace.onFleetCredentialChanged = { [weak self] in
            self?.readFleetToken()
        }
        // The live credential, read at the moment a controller read needs it. A rotation pushed to
        // this Mac's Hub by its controller replaces the token without this app being told, and a
        // stale value on an authenticated read would report a healthy Mac as refused.
        workspace.fleetTokenProvider = { [weak self] in
            self?.readFleetToken()
            return self?.fleetToken
        }
        // The pinned release key belongs with the membership the app already persists, and the
        // restart after a verified swap is the same path the restart notice uses.
        workspace.onReleasePublisherKeyChanged = { [weak self] key in
            self?.rememberReleasePublisherKey(key)
        }
        // What this Mac starts when it starts, reported up the fleet and changeable from it. The
        // preferences and the launch agent are this app's, so a request from the Gateway arrives
        // here (through the Hub's sync route) and is applied here.
        workspace.startupReportProvider = { [weak self] in
            self?.startupReport() ?? NativeWorkspaceStore.StartupReport(
                openAtLogin: false, openAtLoginSaved: false, startServicesOnLaunch: false, appVersion: "")
        }
        workspace.onStartupRequest = { [weak self] request in self?.applyStartupRequest(request) }
        workspace.onFleetRoleObserved = { [weak self] role in self?.observeFleetRole(role) }
        workspace.onReleaseRestartRequested = { [weak self] in
            // An installed update replaces the app on disk, so the window in front of the operator
            // is still the old binary and its version label stays stale. The app relaunches; with
            // no app around it (tests) the services restart as before.
            if let relaunch = self?.onRelaunchRequested { relaunch() } else { self?.restartServices() }
        }
    }
    var running: Bool { worker?.isRunning == true }
    var ownsInstanceLock: Bool { instanceLock != nil }
    var canConfigure: Bool { !running && !starting && !stopping && instanceLock != nil }
    var hasLibrary: Bool { configuration.modelsDirectory != nil }
    /// Lets the operator cancel a pending restart or timeout recovery while
    /// keeping the owned worker's normal shutdown escalation available.
    var canCancelRecovery: Bool { restartTask != nil || startupTimedOut }

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
                enableLoginByDefault()
                failure = nil
                start()
            } catch { failure = error.localizedDescription }
        }
    }
    /// The library the one-click setup left behind, if there is one.
    ///
    /// The running app's own data directory is passed in so the folder this offers is the same one
    /// the kit's copy step fills (`<data>/models` when it holds anything), `--data-dir` included.
    var preparedLibrary: URL? {
        ShellConfiguration.preparedLibrary(dataDirectory: configuration.dataDirectory)
    }

    /// Why the selected library is a folder macOS protects, or nil when it is safe to use.
    var libraryPrivacyWarning: String? {
        guard let url = configuration.modelsDirectory else { return nil }
        return ShellConfiguration.protectedFolderWarning(for: url)
    }

    /// Select the prepared library and start. One click, and the models the operator just copied
    /// are the ones the app uses.
    func usePreparedLibrary() {
        guard canConfigure, let url = preparedLibrary else { return }
        do {
            try ShellConfiguration.requireExternal(url, bundle: Bundle.main.bundleURL)
            try ShellConfiguration.requireDirectory(url)
            configuration.modelsDirectory = url
            try configuration.save()
            enableLoginByDefault()
            failure = nil
            start()
        } catch { failure = error.localizedDescription }
    }
    /// Select this Mac's default model library and start.
    ///
    /// **This used to be `<app data>/models` unconditionally**, the folder inside the app's own data
    /// directory. The kit's copy step fills `~/StudioHubModels`, so a Mac that pressed this button
    /// and a Mac the drive was copied onto ended up using two different folders for the same models:
    /// the app read an empty one and the models had to be repointed by hand (owner report,
    /// 2026-10-18). `ShellConfiguration.defaultLibraryFolder` keeps an install that already holds
    /// models in the app's data folder where it is, and answers with the canonical home-folder
    /// library everywhere else - the folder the kit fills and the app offers as "Use the Models
    /// Already on This Mac", so the two halves cannot land on different folders again.
    func useDefaultLibrary() {
        guard canConfigure else { return }
        let url = ShellConfiguration.defaultLibraryFolder(dataDirectory: configuration.dataDirectory)
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            configuration.modelsDirectory = url
            try configuration.save()
            enableLoginByDefault()
            start()
        } catch { failure = error.localizedDescription }
    }
    /// Whether the selected library is on this Mac right now.
    ///
    /// A library on a drive that is not connected stays selected, so it can be recovered; this is
    /// what lets the window say so plainly instead of offering a reveal Finder cannot perform.
    var libraryFolderExists: Bool {
        guard let url = configuration.modelsDirectory else { return false }
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory)
            && isDirectory.boolValue
    }
    /// Show the model library where it is, selected in its folder.
    ///
    /// **A missing folder is created, not refused.** The operator needs this to reach a fresh
    /// Mac's library before it holds anything, so models can be copied in by hand. The resolved
    /// folder - the selected one, else the default resolver's answer (`~/StudioHubModels`) - is
    /// created with its parents first. A folder on a disconnected drive still fails, and the
    /// failure is reported rather than leaving the button looking dead.
    @discardableResult
    func revealLibrary() -> Bool {
        let url = configuration.modelsDirectory
            ?? ShellConfiguration.defaultLibraryFolder(dataDirectory: configuration.dataDirectory)
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        } catch {
            failure = error.localizedDescription
            return false
        }
        NSWorkspace.shared.activateFileViewerSelecting([url])
        return true
    }
    func start() {
        restartTask?.cancel()
        restartTask = nil
        startLadder.reset()
        recoveryReportedPhase = nil
        healthySince = nil
        start(automaticRecovery: false)
    }
    private func start(automaticRecovery: Bool) {
        guard canConfigure, let models = configuration.modelsDirectory else { return }
        // Starting is what applies a saved change, so the notice goes as soon as it does.
        restartPending = false
        do {
            try ShellConfiguration.requireDirectory(models)
            // Before anything is spawned: a port another program is holding would otherwise cost
            // three failed starts and a message about "repeated service failures".
            try ShellConfiguration.requireFreePort(configuration.port)
            try configuration.save()
            // A stale readiness record is never proof of a running owner.
            if FileManager.default.fileExists(atPath: configuration.readyFile.path) {
                var info = stat()
                guard lstat(configuration.readyFile.path, &info) == 0,
                      info.st_mode & S_IFMT == S_IFREG else {
                    throw ShellFailure(message: "The local Hub readiness record is not a regular file.")
                }
                try? FileManager.default.removeItem(at: configuration.readyFile)
            }
            let executable = serviceExecutable
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw ShellFailure(message: "The bundled local services are missing. Replace this app with a complete build.")
            }
            let log = try openPrivateLog(configuration.logFile)
            logHandle = log
            let process = Process()
            process.executableURL = executable
            // A saved LAN or Tailscale address can disappear when the network changes.
            // Binding it fails with EADDRNOTAVAIL, and the old app reported that only as
            // "repeated service failures" after three retries. Start on the addresses this
            // Mac actually has and name the ones it does not.
            let unavailable = configuration.unavailableListenAddresses()
            let listenHost = configuration.bindableListenHost()
            // **"Not up yet" is not "no longer has", and at boot that difference is everything.**
            //
            // This read is a single instant, and on a headless Mac after a power cut it is the
            // worst instant available: the app is a login item, so it starts while Ethernet is
            // still negotiating, DHCP is unanswered and Tailscale has not come up. Every address
            // the operator chose is then *absent*, not gone — and the fallback binds the Hub to
            // loopback, where it answers its own health check, writes its ready file and looks
            // perfectly healthy while **no node on the network can reach it at all**. The owner hit
            // exactly that: *"the power cut, the controller is headless… I tried to connect to it
            // from the node. It doesn't work… I just stop server and then start server again. Only
            // then the node can be connect."* The manual stop/start was not superstition — it was
            // the same read taken after the network had arrived.
            //
            // So a missing address is remembered rather than accepted, and the supervisor restarts
            // the services the moment it appears. Nothing narrows permanently, and the operator
            // does not have to know any of this.
            addressesMissingAtStart = unavailable
            if !unavailable.isEmpty {
                fleetNote = "Waiting to serve \(unavailable.joined(separator: ", ")) — this Mac does "
                    + "not have \(unavailable.count == 1 ? "that address" : "those addresses") yet, "
                    + "which is usual for a minute after a restart. Services are starting on the "
                    + "remaining addresses and will restart by themselves once it appears."
            }
            process.arguments = ShellConfiguration.serviceArguments(
                dataDirectory: configuration.dataDirectory, modelsDirectory: models,
                readyFile: configuration.readyFile, parentPID: ProcessInfo.processInfo.processIdentifier,
                listenHost: listenHost, offline: configuration.offline,
                port: configuration.port)
            process.currentDirectoryURL = configuration.dataDirectory
            process.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"]
            process.standardOutput = log
            process.standardError = log
            process.terminationHandler = { [weak self] process in
                let code = process.terminationStatus
                Task { @MainActor in self?.workerExited(process: process, code: code) }
            }
            failure = nil
            starting = true
            startupTimedOut = false
            status = "Starting local services…"
            worker = process
            try process.run()
            pollTask = Task { [weak self] in
                guard let self else { return }
                for _ in 0..<240 {
                    if Task.isCancelled || !process.isRunning { return }
                    if let data = readPrivateRegularFile(self.configuration.readyFile),
                       let ready = try? WorkerReady.decode(data, expectedPID: process.processIdentifier,
                                                           expectedHost: configuration.clientHost) {
                        guard !Task.isCancelled, !self.stopping, process.isRunning,
                              self.worker === process else { return }
                        self.starting = false
                        self.readyURL = ready.url
                        self.connectionToken = ready.token
                        self.readFleetToken()
                        // A service that came up starts a fresh recovery story if it fails later.
                        self.recoveryReportedPhase = nil
                        self.status = "Local services ready"
                        await self.workspace.connect(baseURL: ready.url,
                                                     dataDirectory: self.configuration.dataDirectory,
                                                     token: ready.token,
                                                     allowedHost: self.configuration.clientHost)
                        guard !Task.isCancelled, !self.stopping, process.isRunning,
                              self.worker === process else { return }
                        // Read again now that the Hub has answered: the fleet credential is minted
                        // by the first authenticated request, so the earlier read could predate it.
                        // Connections offers this value to a joining Mac, and it has to be on screen.
                        self.readFleetToken()
                        // Re-join the group this Mac belongs to, once per start. A node's own
                        // address changes when it restarts and the controller needs to be told;
                        // a controller that had to be restarted is found again at the same
                        // address because the port is pinned.
                        await self.workspace.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
                            controllerAddress: self.configuration.controllerAddress ?? "",
                            token: self.fleetToken,
                            boundHosts: self.configuration.listenHosts,
                            nodeCredential: self.configuration.nodeCredential,
                            releasePublisherKey: self.pinnedReleasePublisherKey))
                        guard !Task.isCancelled, !self.stopping, process.isRunning,
                              self.worker === process else { return }
                        let session = URLSession(configuration: .ephemeral)
                        defer { session.invalidateAndCancel() }
                        while !Task.isCancelled && process.isRunning && !self.stopping {
                            do {
                                var request = URLRequest(url: ready.url.appendingPathComponent("api/health"))
                                request.setValue("Bearer \(ready.token)", forHTTPHeaderField: "Authorization")
                                request.timeoutInterval = 5
                                let (body, response) = try await self.healthRequest(request, session)
                                guard (response as? HTTPURLResponse)?.statusCode == 200,
                                      let health = try JSONSerialization.jsonObject(with: body) as? [String: Any],
                                      health["ok"] as? Bool == true else { throw ShellFailure(message: "Health check failed") }
                                guard !Task.isCancelled, !self.stopping, process.isRunning,
                                      self.worker === process else { return }
                                if self.healthySince == nil { self.healthySince = self.now() }
                                self.status = health["busy"] as? Bool == true ? "Local services working" : "Local services ready"
                                // **A Hub that came up without the operator's addresses is
                                // reachable by nobody, however healthy it looks.** It answers this
                                // very health check on loopback, so nothing above can tell the
                                // difference. Now that the network has had time to arrive, restart
                                // once so the services bind what the operator chose.
                                //
                                // One restart, only while `addressesMissingAtStart` is non-empty,
                                // and the spawn clears it — so a Hub that is deliberately serving a
                                // narrowed address set (the operator changed them on purpose) is
                                // restarted at most once, not in a loop.
                                if !self.addressesMissingAtStart.isEmpty {
                                    let arrived = ShellConfiguration.arrivedAddresses(
                                        missing: self.addressesMissingAtStart)
                                    if !arrived.isEmpty {
                                        self.addressesMissingAtStart = []
                                        self.fleetNote = "This Mac has \(arrived.joined(separator: ", ")) now, "
                                            + "so the services are restarting to serve it."
                                        self.restartServices()
                                        return
                                    }
                                }
                            } catch {
                                guard !Task.isCancelled, !self.stopping, process.isRunning,
                                      self.worker === process else { return }
                                self.healthySince = nil
                                self.status = "Local services not responding"
                            }
                            try? await Task.sleep(nanoseconds: 3_000_000_000)
                        }
                        return
                    }
                    try? await Task.sleep(nanoseconds: 500_000_000)
                }
                if process.isRunning {
                    self.failure = "The local services did not become ready within two minutes. Open its log, then stop it before retrying."
                    self.lastServiceFailure = "the services did not become ready within two minutes"
                    self.status = "Startup needs attention"
                    self.starting = false
                    self.startupTimedOut = true
                    self.stopping = true
                    process.terminate()
                    self.scheduleForceStop(for: process)
                }
            }
        } catch {
            worker = nil
            starting = false
            try? logHandle?.close()
            logHandle = nil
            failure = error.localizedDescription
            lastServiceFailure = error.localizedDescription
            status = "Unable to start local services"
            if automaticRecovery { scheduleRecovery() }
        }
    }
    private func workerExited(process: Process, code: Int32) {
        guard worker === process else { return }
        pollTask?.cancel()
        forceStopTask?.cancel()
        forceStopTask = nil
        let expectedStop = stopping
        let startupFailure = startupTimedOut
        startLadder.resetAfterHealthyUptime(readyAt: healthySince, now: now())
        healthySince = nil
        worker = nil
        readyURL = nil
        connectionToken = nil
        fleetToken = nil
        starting = false
        stopping = false
        startupTimedOut = false
        canForceStop = false
        workspace.disconnect()
        if !expectedStop {
            // The only record of how the services ended: the app stays up, so `last-stop.json` (an
            // app stop) is untouched, and this status line is replaced by the restart. On 2026-10-05
            // a controller's services restarted at 22:14 and nothing said whether it was a crash
            // (code), a kill such as a memory kill (signal 9) or an abort (signal 6).
            let how = process.terminationReason == .uncaughtSignal ? "signal \(code)" : "exit code \(code)"
            let stamp = ISO8601DateFormatter().string(from: Date())
            try? logHandle?.write(contentsOf: Data(
                "[app \(stamp)] the local services stopped unexpectedly (\(how)); restarting them\n".utf8))
        }
        try? logHandle?.close()
        logHandle = nil
        if expectedStop {
            status = startupFailure ? "Startup needs attention" : "Local services stopped"
            if startupFailure { scheduleRecovery() }
        } else {
            status = "Local services stopped unexpectedly"
            lastServiceFailure = "the engine exited (code \(code))"
            failure = "The engine exited (code \(code)). Open its log for details, then try Start again."
            scheduleRecovery()
        }
        onStopped?()
        if restartAfterStop {
            restartAfterStop = false
            start()
        }
    }
    private func scheduleRecovery() {
        guard restartTask == nil else { return }
        let attempt = startLadder.attempt
        let libraryAvailable = configuration.modelsDirectory.map {
            FileManager.default.fileExists(atPath: $0.path)
        } ?? false
        switch StartupRecovery.nextAction(attempt: attempt, libraryAvailable: libraryAvailable,
                                          ownerRunning: running, userStopped: false,
                                          maxAttempts: startLadder.maxFastAttempts,
                                          delays: startLadder.fastDelays) {
        case .stop(let message):
            status = message
            failure = message
        case .wait(let delay):
            if startLadder.isFastPhase {
                status = "Recovering local services…"
                if recoveryReportedPhase != .fast {
                    recoveryReportedPhase = .fast
                    failure = "Recovering local services… (last failure: "
                        + "\(lastServiceFailure ?? "the services did not start"))."
                }
            } else {
                // After the fast ladder the app is still trying, so the message has to say so and
                // name the next attempt rather than reading as a permanent failure.
                let next = now().addingTimeInterval(delay)
                let text = "Still trying to start the services (last failure: "
                    + "\(lastServiceFailure ?? "the services did not start")). "
                    + "Next attempt at \(next.formatted(date: .omitted, time: .shortened))."
                status = text
                if recoveryReportedPhase != .slow {
                    recoveryReportedPhase = .slow
                    failure = text
                }
            }
            startLadder.advance()
            restartTask = Task { [weak self] in
                if delay > 0 { try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
                guard !Task.isCancelled else { return }
                await MainActor.run {
                    guard let self else { return }
                    self.restartTask = nil
                    self.start(automaticRecovery: true)
                }
            }
        }
    }
    /// Stop and start again, applying the role, the network addresses, the port or the name.
    ///
    /// Every one of these is read when the Hub starts, and the controls that change them are
    /// disabled while it runs - which left the operator to work out on their own that they had to
    /// stop the services first, change the setting, and start them again. Three steps, two of them
    /// invisible. This is the one button.
    func restartServices() {
        restartPending = false
        guard running || starting else {
            // Nothing is running: this is a start, which still needs a configuration it can run.
            guard canConfigure else { return }
            start()
            return
        }
        // Running: stop, then start again from `workerExited`. The previous guard was `canConfigure`,
        // which is false while the services run, so this branch was unreachable and the button did
        // nothing exactly when it was offered - including after an update replaced the app on disk.
        restartAfterStop = true
        status = "Restarting to apply the change…"
        stop()
    }

    func stop() {
        let cancelledRecovery = restartTask != nil
        restartTask?.cancel()
        restartTask = nil
        startLadder.reset()
        recoveryReportedPhase = nil
        healthySince = nil
        // A timeout is an automatic stop. An explicit Stop while it is
        // winding down cancels the follow-up recovery without interrupting
        // the existing force-stop escalation.
        if stopping {
            startupTimedOut = false
            return
        }
        guard let worker, worker.isRunning, !stopping else {
            if cancelledRecovery { status = "Local services stopped"; onStopped?() }
            return
        }
        pollTask?.cancel()
        stopping = true
        starting = false
        readyURL = nil
        connectionToken = nil
        fleetToken = nil
        workspace.disconnect()
        status = "Stopping local services…"
        worker.terminate()
        scheduleForceStop(for: worker)
    }
    private func scheduleForceStop(for process: Process) {
        forceStopTask?.cancel()
        forceStopTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 15_000_000_000)
            guard let self, !Task.isCancelled, self.stopping, process.isRunning else { return }
            self.status = "Local services are still stopping"
            self.canForceStop = true
            // An unattended update is waiting on this exit and nobody is there to answer a dialog:
            // work was drained before it began, so a shutdown that hangs is ended, not asked about.
            if self.forceStopWithoutAsking { self.forceStop() } else { self.confirmForceStop() }
        }
    }
    var recoveryPending: Bool { restartTask != nil }
    func confirmForceStop() {
        guard let worker, worker.isRunning, canForceStop else { return }
        let alert = NSAlert()
        alert.messageText = "The local services are still stopping"
        alert.informativeText = "You can keep waiting or force it to stop. Force Stop may leave an incomplete output or download; it will not restart the job."
        alert.addButton(withTitle: "Keep Waiting")
        alert.addButton(withTitle: "Force Stop")
        if alert.runModal() == .alertSecondButtonReturn { forceStop() }
    }
    /// Set by the relaunch after an installed update: the force-stop escalation then acts by itself.
    var forceStopWithoutAsking = false
    private func forceStop() {
        guard let worker, worker.isRunning else { return }
        let pid = worker.processIdentifier
        // Only the still-owned worker's isolated group may be signalled.
        if getpgid(pid) == pid { killpg(pid, SIGKILL) }
        else { kill(pid, SIGKILL) }
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

    /// Read this Mac's fleet credential from the Hub's own state directory.
    ///
    /// It is not fetched over HTTP: a Hub does not serve its credentials, so the app reads the
    /// file it already owns, through the same safe reader the readiness record uses.
    ///
    /// The Hub keeps its state under `hub/`, so this read used to look one level too high and
    /// answer nil on every Mac - invisible while the token was only ever typed by hand, and fatal
    /// the moment Connections had to hand it to a joining Mac.
    func readFleetToken() {
        let hubState = configuration.dataDirectory.appendingPathComponent("hub")
        let candidates = [hubState.appendingPathComponent(".fleet_token"),
                          configuration.dataDirectory.appendingPathComponent(".fleet_token")]
        guard let url = candidates.first(where: { readPrivateRegularFile($0) != nil }),
              let data = readPrivateRegularFile(url) else { fleetToken = nil; return }
        let value = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        fleetToken = value.isEmpty ? nil : value
    }
    /// Whether the Hub is running on something other than what is saved.
    ///
    /// Asked of the Hub rather than remembered in the view: the operator can quit the app mid-edit,
    /// and a notice that only exists while a screen is open is a notice that lies.
    var hasPendingRestartSetting: Bool {
        guard running || starting, let bound = readyURL?.port else { return false }
        // The port is the one setting the running Hub tells us about. The addresses are covered by
        // `restartPending`, which the controls that change them set.
        return bound != configuration.port
    }

    /// Mark that a saved change will only take effect at the next start.
    ///
    /// Shown next to the control that made it, with the button that applies it, rather than as a
    /// line at the bottom of the page.
    private func noteRestartNeeded() {
        guard running || starting else { return }
        restartPending = true
    }

    /// Set every network this Hub should answer on. Loopback is implied and added by the Hub.
    func setListenHosts(_ hosts: [String]) {
        let unique = hosts.reduce(into: [String]()) { result, host in
            if !result.contains(host) { result.append(host) }
        }
        configuration.listenHost = unique.isEmpty ? "127.0.0.1" : unique.joined(separator: ",")
        saveConfiguration()
        noteRestartNeeded()
    }

    /// Set the port the Hub binds. Fixed rather than chosen by the operating system, because a
    /// node has to be able to name this Mac again after either of them restarts.
    func setPort(_ port: Int) {
        guard (1024...65535).contains(port), port != configuration.port else { return }
        configuration.port = port
        saveConfiguration()
        noteRestartNeeded()
        fleetNote = "Port set to \(port). Restart the services to use it, then tell every node the new address."
    }

    /// The address a node should be given, when this Mac answers on more than itself.
    var nodeFacingAddress: String? {
        guard let entry = LocalAddresses.current().first(where: { configuration.listenHosts.contains($0.address)
            || configuration.listenHosts.contains("0.0.0.0") }) else { return nil }
        return "\(entry.address):\(configuration.port)"
    }

    /// Remember the controller this Mac joined, so its services can re-join by themselves.
    ///
    /// Called with nil when it leaves: leaving mints a fresh local credential, so the cached one
    /// has to be re-read or the app would keep showing a token the Hub no longer accepts.
    func rememberController(_ address: String?) {
        configuration.controllerAddress = address
        // A Mac that joins a fleet is there to serve: turn "Start services on startup" on, once.
        configuration.applyFleetServicesDefault(inFleet: configuration.isJoinedNode)
        readFleetToken()
        do { try configuration.save() } catch { failure = error.localizedDescription }
    }

    /// Once per Mac, at launch: a Mac that already holds a controller address gets the fleet
    /// default (owner, 2026-10-02: every installed fleet Mac should serve after a reboot). Runs
    /// before the launch decides whether to start the services, so the first launch after the update
    /// already does. A Mac that is not in a fleet, or whose owner has chosen, is left alone.
    func applyFleetServicesDefaultAtLaunch() {
        guard configuration.applyFleetServicesDefault(inFleet: configuration.isJoinedNode) else { return }
        do { try configuration.save() } catch { /* retried next launch; not startup-critical */ }
    }

    /// The Hub's own role, learned while the services run. A controller has no controller address,
    /// and a node's may predate the preference, so the running Hub is the one who knows.
    func observeFleetRole(_ role: String) {
        fleetRoleIsMember = role == "controller" || role == "agent"
        guard fleetRoleIsMember else { return }
        guard configuration.applyFleetServicesDefault(inFleet: true) else { return }
        do { try configuration.save() } catch { /* retried at the next report */ }
    }

    /// What the Settings page, the sidebar and the Gateway all read: the actual login state, the
    /// saved choices and this build.
    func startupReport() -> NativeWorkspaceStore.StartupReport {
        NativeWorkspaceStore.StartupReport(
            openAtLogin: loginStatus == .enabled,
            openAtLoginSaved: configuration.startAtLogin,
            startServicesOnLaunch: configuration.startServicesAtLogin,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
    }

    /// True when this Mac is in a fleet but will not bring its services up after a restart - the one
    /// state the sidebar says out loud, because such a Mac is silent to its controller until
    /// somebody opens the app.
    var servicesWillNotAutoStart: Bool {
        !configuration.startServicesAtLogin && (configuration.isJoinedNode || fleetRoleIsMember)
    }
    /// Set from the Hub's own report of its role; false until a running Hub says so.
    @Published var fleetRoleIsMember = false

    /// Apply one request from the fleet. Never raises the failure banner: the person at this Mac did
    /// not ask for it, and a refusal is reported back to whoever did.
    func applyStartupRequest(_ request: NativeWorkspaceStore.StartupRequest) -> NativeWorkspaceStore.StartupOutcome? {
        func outcome(_ ok: Bool, _ detail: String? = nil) -> NativeWorkspaceStore.StartupOutcome {
            NativeWorkspaceStore.StartupOutcome(id: request.id, ok: ok, detail: detail)
        }
        switch request.action {
        case "set_start_services_on_launch":
            guard let value = request.value else { return outcome(false, "no value was sent") }
            configuration.chooseStartServices(value)
            do { try configuration.save(); return outcome(true) }
            catch { return outcome(false, "could not save the setting: \(error.localizedDescription)") }
        case "set_open_at_login":
            guard let value = request.value else { return outcome(false, "no value was sent") }
            if let message = changeLogin(value) { return outcome(false, message) }
            return outcome(true)
        case "restart_services":
            // The Hub checked that nothing is running (or that this was forced) before asking.
            restartServices()
            return nil
        default:
            return outcome(false, "unknown request \(request.action)")
        }
    }

    /// Persist this Mac's own node credential, or clear it when the membership ends.
    ///
    /// It is issued once, in the join answer, and never published again: a Mac that loses it has to
    /// be re-admitted rather than re-read. So it is written with the membership it belongs to —
    /// `ShellConfiguration.save()` writes the preferences file 0600 in a 0700 directory — and
    /// cleared by the same actions that leave the group.
    func rememberNodeCredential(_ credential: String?) {
        configuration.nodeCredential = credential
        do { try configuration.save() } catch { failure = error.localizedDescription }
    }

    /// The release key stored with the membership, if it is internally consistent. A stored pair
    /// whose fingerprint does not match its own key is ignored rather than trusted.
    var pinnedReleasePublisherKey: ReleasePublisherKey? {
        guard let publicKey = configuration.releasePublisherPublicKey,
              let fingerprint = configuration.releasePublisherFingerprint,
              ReleaseSignature.fingerprint(publicKeyHex: publicKey) == fingerprint else { return nil }
        return ReleasePublisherKey(publicKey: publicKey, fingerprint: fingerprint)
    }

    /// Persist a pinned release key, or clear it when the workspace has left the group.
    func rememberReleasePublisherKey(_ key: ReleasePublisherKey?) {
        configuration.releasePublisherPublicKey = key?.publicKey
        configuration.releasePublisherFingerprint = key?.fingerprint
        do { try configuration.save() } catch { failure = error.localizedDescription }
    }

    /// Persist a configuration change made from a picker, and say that it needs a restart.
    func saveConfiguration() {
        do {
            try configuration.save()
            // A change made while the services run is remembered and applied at the next start,
            // and the notice beside the control offers that restart. Refusing the edit outright
            // was what made the address toggles unusable once the Hub was already up.
            noteRestartNeeded()
            fleetNote = "Saved. Restart the services to use this change."
        } catch { failure = error.localizedDescription }
    }

    func setOffline(_ value: Bool) {
        guard canConfigure else { return }
        configuration.offline = value
        do { try configuration.save() } catch { failure = error.localizedDescription }
    }
    func setRecoveryPolicy(maxAttempts: Int, backoffSeconds: [TimeInterval]) {
        guard canConfigure else { return }
        do {
            try configuration.setRecoveryPolicy(maxAttempts: maxAttempts, backoffSeconds: backoffSeconds)
            try configuration.save()
            startLadder = ServiceStartLadder(maxFastAttempts: configuration.recoveryMaxAttempts,
                                             fastDelays: configuration.recoveryBackoffSeconds)
        } catch { failure = error.localizedDescription }
    }
    func refreshLoginStatus() { loginStatus = loginAgentStatus(agentPlistURL) }

    /// Make the launch agent match the saved "Start at login" choice on every launch.
    ///
    /// This is ordinary reconciliation, deliberately separate from the one-time migration: the
    /// migration must never touch the agent, but after it removes the old login item a Mac whose
    /// preference is still on must get the agent back without the operator re-picking a library.
    func reconcileLoginAgent() {
        // A Mac with no library yet has not been set up, and must not register itself before the
        // operator picks one; library selection already calls `enableLoginByDefault`.
        guard configuration.modelsDirectory != nil else { return }
        enableLoginByDefault()
    }

    /// The one-time move off the old `SMAppService` login item so the launch agent is the only
    /// mechanism left. It never touches the launch agent, and it never fails startup: a Mac that
    /// cannot remove the old entry still runs and is told how to remove it by hand.
    func migrateLegacyLoginItemIfNeeded() {
        guard !configuration.loginMigrationComplete else { return }
        let outcome = LoginMigration.run(alreadyRecorded: configuration.loginMigrationComplete,
                                         oldStatus: legacyLoginItemStatus,
                                         unregister: legacyLoginItemUnregister)
        // Recorded only after the attempt: a crash mid-migration leaves the flag unset and the next
        // launch tries again rather than leaving both login mechanisms live. A caught failure is
        // still an attempt, so a broken registration is named once instead of on every launch.
        configuration.loginMigrationComplete = true
        do { try configuration.save() } catch { /* not startup-critical; retried next launch */ }
        if let notice = LoginMigration.notice(for: outcome) { failure = notice }
    }

    /// Turn the one login mechanism on or off: the launchd agent that starts the app at login and
    /// relaunches it after a crash (and only a crash - see `StopRecord`). A normal Quit exits 0.
    func setLogin(_ enabled: Bool) {
        if let message = changeLogin(enabled) { failure = message }
    }
    /// The login change itself, answering with the reason it failed (nil on success) instead of
    /// raising the banner - so the owner's toggle and a request from the fleet share one path.
    func changeLogin(_ enabled: Bool) -> String? {
        configuration.startAtLogin = enabled
        do {
            try applyLoginAgent(enabled: enabled)
            try configuration.save()
            refreshLoginStatus()
            return nil
        } catch {
            // The preference is remembered even when the plist cannot be written, so a refusal is
            // not silently forgotten; the toggle state and the banner report the truth.
            try? configuration.save()
            refreshLoginStatus()
            return (error as? ShellFailure)?.message
                ?? "Could not change login startup: \(error.localizedDescription)"
        }
    }
    /// The live login-agent side effects. Enabling writes the plist only: bootstrapping a
    /// `RunAtLoad` job while this app is running would start a second copy of it.
    private func applyLoginAgent(enabled: Bool) throws {
        let operations = injectedLoginAgentOperations ?? LoginAgentOperations(
            write: { try LaunchAgent.write(appBundle: self.appBundle, to: self.agentPlistURL) },
            bootout: { LaunchAgent.bootout(label: LaunchAgent.label) },
            remove: { try LaunchAgent.remove(at: self.agentPlistURL) },
            bootstrap: { LaunchAgent.bootstrap(at: self.agentPlistURL) })
        try LoginAgent.apply(enabled: enabled, using: operations, coordinator: loginAgentCoordinator)
    }
    /// Remember whether an at-login launch should also bring the Studio services up.
    ///
    /// Saving is enough: the choice is read on the next launch. Turning it on while the app is
    /// already running changes nothing about the services that are running now.
    func setStartServicesAtLogin(_ enabled: Bool) {
        configuration.chooseStartServices(enabled)
        do { try configuration.save() } catch { failure = error.localizedDescription }
    }
    private func enableLoginByDefault() {
        guard configuration.startAtLogin, loginStatus != .enabled else { return }
        switch LaunchAgent.installDecision(appBundle: appBundle) {
        case .refuse:
            // A dev build or an app moved out of /Applications cannot be started at login, and
            // the operator is told when they press the toggle themselves.
            break
        case .install:
            // Writes the plist only. launchd loads it at login; bootstrapping it here would start
            // a second copy of this already-running app because the plist sets RunAtLoad.
            do { try applyLoginAgent(enabled: true) }
            catch { failure = "Could not set up login startup: \(error.localizedDescription)" }
        }
        refreshLoginStatus()
    }
    func openLog() {
        if FileManager.default.fileExists(atPath: configuration.logFile.path) { NSWorkspace.shared.open(configuration.logFile) }
        else { NSWorkspace.shared.open(configuration.dataDirectory) }
    }
}

/// The helper that reopens the app after an update, as its own `/bin/sh`. It waits for the old
/// process to exit and then runs `open`. **Safety net:** if the old process is still alive after
/// `waitSeconds` (a hung quit, any future bug) it is sent SIGTERM, then SIGKILL after `graceSeconds`,
/// so an update can never leave a Mac without its Hub. It is spawned in its own session, with no
/// inherited descriptors: a launchd job (the login agent) kills what is left in its process group
/// when the app exits, and an inherited data-folder lock descriptor would keep the new copy out.
enum RelaunchHelper {
    static let waitSeconds = 120
    static let graceSeconds = 10
    /// The waits are counted half-second ticks, not `SECONDS` arithmetic. `SECONDS` is whole
    /// seconds since the second the shell started in, so `$((SECONDS + 2))` was reached as little
    /// as 1.0 s later when the shell started late in a second — the helper could SIGTERM a Hub
    /// that was still quitting normally. Counting the ticks it actually slept makes `wait` mean
    /// at least `wait`.
    static let script = """
    pid=$1; app=$2; wait=$3; grace=$4; opener=$5
    gone() { ! kill -0 "$pid" 2>/dev/null; }
    hold() { ticks=$(($1 * 2)); while ! gone && [ "$ticks" -gt 0 ]; do sleep 0.5; ticks=$((ticks - 1)); done; }
    hold "$wait"
    if ! gone; then
      kill -TERM "$pid" 2>/dev/null
      hold "$grace"
      if ! gone; then
        kill -KILL "$pid" 2>/dev/null
        ticks=25; while ! gone && [ "$ticks" -gt 0 ]; do sleep 0.2; ticks=$((ticks - 1)); done
      fi
    fi
    exec "$opener" "$app"
    """

    /// Start the helper and return its pid. `wait`, `grace` and `opener` are parameters for the lifecycle test.
    static func spawn(pid: Int32, app: String, wait: Int = waitSeconds, grace: Int = graceSeconds,
                      opener: String = "/usr/bin/open") throws -> Int32 {
        var attributes: posix_spawnattr_t? = nil
        var actions: posix_spawn_file_actions_t? = nil
        posix_spawnattr_init(&attributes); posix_spawn_file_actions_init(&actions)
        defer { posix_spawnattr_destroy(&attributes); posix_spawn_file_actions_destroy(&actions) }
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETSID | POSIX_SPAWN_CLOEXEC_DEFAULT))
        for descriptor in 0...2 {
            posix_spawn_file_actions_addopen(&actions, Int32(descriptor), "/dev/null", descriptor == 0 ? O_RDONLY : O_WRONLY, 0)
        }
        let arguments: [UnsafeMutablePointer<CChar>?] =
            ["sh", "-c", script, "sh", String(pid), app, String(wait), String(grace), opener].map { strdup($0) } + [nil]
        defer { arguments.forEach { free($0) } }
        var child: pid_t = 0
        let status = posix_spawn(&child, "/bin/sh", &actions, &attributes, arguments, environ)
        guard status == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(status)) }
        return child
    }
}

/// Lite Mode (internally "workstation", `components/hub/workstation.py`, owner 2026-10-03 "Shops"): a
/// shop's cashier Mac shows no pop-ups - no window opened by itself, no What's New - and may hide this
/// menu-bar icon. Read from the Hub's own `workstation.json` beside its other state, so it needs no
/// request. Changed in Settings > Lite Mode on the Mac itself or from the Gateway's Fleet page.
struct WorkstationQuiet: Equatable {
    var quiet = false
    var hideIcon = false

    static func read(dataDirectory: URL) -> WorkstationQuiet {
        let file = dataDirectory.appendingPathComponent("hub/workstation.json")
        guard let data = try? Data(contentsOf: file),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              object["enabled"] as? Bool == true else { return WorkstationQuiet() }
        return WorkstationQuiet(quiet: true, hideIcon: object["hide_menu_bar_icon"] as? Bool == true)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var controller: StudioController!
    var window: NSWindow!
    private var statusItem: NSStatusItem!
    private var quitPending = false
    /// True while quitting only to reopen after an update, which must not stop to ask.
    var relaunching = false
    /// True while Quit is deciding whether to ask; a second Quit waits for the first.
    private var quitAsking = false
    /// Fresh count of running work, and the question asked only when it is not zero. Seams for the lifecycle test.
    /// Bounded: a hung Hub must not hold Quit for the length of a request timeout.
    lazy var activeWork: () async -> Int = { [unowned self] in
        let read = Task { await self.controller.workspace.activeWorkNow() }
        let cut = Task { try? await Task.sleep(for: .seconds(3)); read.cancel() }
        defer { cut.cancel() }
        return await read.value
    }
    var confirmQuitWhileBusy: () -> Bool = {
        let alert = NSAlert()
        alert.messageText = "Quit Studio Hub and stop its engine?"
        alert.informativeText = "Active generation, transcription, or downloads may be interrupted. Close the window to keep working in the background."
        alert.addButton(withTitle: "Keep Running")
        alert.addButton(withTitle: "Quit and Stop")
        return alert.runModal() == .alertSecondButtonReturn
    }
    private var statusObserver: AnyCancellable?
    private var failureObserver: AnyCancellable?
    private var workstation = WorkstationQuiet()
    private var workstationTimer: Timer?
    /// Re-read every few seconds: the Gateway changes the setting through the Hub while the app runs.
    private func refreshWorkstation() {
        guard let controller else { return }
        workstation = WorkstationQuiet.read(dataDirectory: controller.configuration.dataDirectory)
        statusItem?.isVisible = !workstation.hideIcon
    }

    /// The menu-bar mark: the Wave Frame glyph as a template image (one colour plus alpha, so macOS
    /// tints it for light and dark bars and for the pressed state). `menubar-icon.png` is 18 px and
    /// `menubar-icon@2x.png` 36 px, drawn from `assets/logo/menubar-icon.svg` by
    /// `packaging/make_app_icon.py`; both are added as representations of one 18 pt image so a
    /// Retina bar uses the 36 px drawing rather than a stretched 1x. The SF Symbol it replaced is the
    /// fallback only if the bundle somehow lacks the files.
    static func menuBarMark(bundle: Bundle = .main) -> NSImage? {
        let image = NSImage(size: NSSize(width: 18, height: 18))
        for (name, pixels) in [("menubar-icon", 18), ("menubar-icon@2x", 36)] {
            guard let url = bundle.url(forResource: name, withExtension: "png"),
                  let data = try? Data(contentsOf: url),
                  let rep = NSBitmapImageRep(data: data), rep.pixelsWide == pixels else { continue }
            rep.size = NSSize(width: 18, height: 18)
            image.addRepresentation(rep)
        }
        guard !image.representations.isEmpty else {
            return NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "Studio Hub")
        }
        image.isTemplate = true
        image.accessibilityDescription = "Studio Hub"
        return image
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let config: ShellConfiguration
        /// This copy is an update's reopen (the previous one quit to install it), not someone opening the app.
        var updateReopen = false
        do {
            config = try ShellConfiguration(arguments: CommandLine.arguments, bundle: Bundle.main.bundleURL)
            controller = StudioController(configuration: config)
            // A redundant launch (a second open from Finder while the app is open) presents the
            // running app and exits 0, before any window or alert. The agent's own copy never gets
            // here while the app runs: it stands by in `main` (see `AgentStandby`).
            ExistingInstance.handle(ExistingInstance.decide(lockHeldByAnother: controller.existingInstanceDetected),
                                    present: ExistingInstanceHandler.presentRunningApp,
                                    exitQuietly: ExistingInstanceHandler.noteAndExit)
            // This copy owns the data folder now: settle why the previous one ended (unless the
            // agent's copy already did), then leave the evidence the next settle reads.
            let settled = StopRecord.settle(directory: config.dataDirectory, relaunching: false)
            updateReopen = settled?["reason"] as? String == StopReason.updated.rawValue
            StopRecord.markRunning(directory: config.dataDirectory)
            StopRecord.installSignalMarkers(directory: config.dataDirectory)
            startStopEvidence(directory: config.dataDirectory)
            // One-time, before anything else can offer the login choice: remove the login item an
            // older build left behind so the launch agent is the only mechanism that can run.
            controller.migrateLegacyLoginItemIfNeeded()
            // Then make the agent match the saved choice, so an upgraded Mac whose "Start at login"
            // is still on does not lose login startup when the old item is removed. This writes the
            // plist only; it must not bootstrap a RunAtLoad job while the app is running.
            controller.reconcileLoginAgent()
            // And, once, give a Mac that belongs to a fleet "Start services on startup": it is there
            // to serve, and a Mac whose services are off is silent to its controller after a reboot.
            controller.applyFleetServicesDefaultAtLaunch()
        } catch {
            let alert = NSAlert(); alert.messageText = "Studio Hub could not open"; alert.informativeText = error.localizedDescription; alert.runModal(); NSApp.terminate(nil); return
        }
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1380, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Studio Hub"
        // Matches the SwiftUI floor. It used to be 1040 while the content asked for more than
        // that in fixed columns, and a Split View can go below any minimum anyway - so the
        // layout had to learn to compress rather than rely on this number.
        // The floor is arithmetic, not taste: the sidebar takes ~260, the page padding 56, and a
        // split page needs ~300 for its list column and ~330 for its controls column. At 720 - the
        // old value - the controls column was narrower than its own contents and they were clipped
        // at its edge, so a control the operator could see could not be read or clicked. 980 leaves
        // a little slack; the split itself may still be dragged below it.
        window.contentMinSize = NSSize(width: 980, height: 620)
        window.setFrameAutosaveName("StudioHubMain")
        let content = NSHostingView(rootView: StudioWindow(studio: controller))
        content.sizingOptions = []
        window.contentView = content
        window.delegate = self
        window.isReleasedWhenClosed = false
        controller.workspace.isWindowVisible = { [weak self] in
            guard let window = self?.window else { return true }
            return window.isVisible && !window.isMiniaturized && window.occlusionState.contains(.visible)
        }
        controller.onRelaunchRequested = { [weak self] in self?.relaunchAfterUpdate() }
        window.center()
        let mainMenu = NSMenu()
        let applicationItem = NSMenuItem(); mainMenu.addItem(applicationItem)
        let applicationMenu = NSMenu(); applicationItem.submenu = applicationMenu
        applicationMenu.addItem(withTitle: "About Studio Hub", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        let whatsNewItem = NSMenuItem(title: "What's New in Studio Hub", action: #selector(showWhatsNew), keyEquivalent: "")
        whatsNewItem.target = self
        applicationMenu.addItem(whatsNewItem)
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
        statusItem.button?.image = Self.menuBarMark()
        statusItem.button?.toolTip = "Studio Hub"
        let menu = NSMenu()
        let openItem = NSMenuItem(title: "Open Studio Hub", action: #selector(openWindow), keyEquivalent: ""); openItem.target = self; menu.addItem(openItem)
        let logItem = NSMenuItem(title: "Open Engine Log", action: #selector(openLog), keyEquivalent: ""); logItem.target = self; menu.addItem(logItem)
        let whatsNewBarItem = NSMenuItem(title: "What's New", action: #selector(showWhatsNew), keyEquivalent: ""); whatsNewBarItem.target = self; menu.addItem(whatsNewBarItem)
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
        refreshWorkstation()
        workstationTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refreshWorkstation() }
        }
        failureObserver = controller.$failure.dropFirst().compactMap { $0 }.sink { [weak self] _ in
            guard self?.workstation.quiet != true else { return }   // a shop's Mac: logged, not shown
            self?.openWindow()
        }
        controller.onStopped = { [weak self] in self?.engineStopped() }
        // Decided before the window opens, and only once per launch. The launchd agent starts the
        // executable directly, so it carries the login marker in its environment; a login item
        // launch carries the AppleEvent instead.
        announceReleaseNotes = !workstation.quiet && ReleaseNotesSeen.shouldAnnounce()
        let agentLaunch = ProcessInfo.processInfo.environment[LaunchAgent.launchedAtLoginEnvironmentKey] == "1"
        let loginLaunch = agentLaunch || NSAppleEventManager.shared().currentAppleEvent?
            .paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
        // A Lite Mode Mac opens its window only by hand: an update's reopen must not put Studio Hub in
        // front of a cashier. Opening the app from Applications or Spotlight still shows it - with the
        // menu-bar icon hidden, that is the way back to Settings.
        if (!loginLaunch && !(workstation.quiet && updateReopen)) || !controller.hasLibrary { openWindow() }
        // A login launch starts the Studios only when the owner asked for it; the default is
        // off, so a Mac that signs in overnight stays idle until someone sends it work. Every
        // other launch starts them, because opening the app is the intent to use it.
        if controller.hasLibrary && controller.canConfigure && (!loginLaunch || controller.configuration.startServicesAtLogin) {
            controller.start()
        }
        // Opened by hand or by an update's reopen: refresh the existing launchd registration so it
        // can restore the app after an abnormal exit; a clean Quit remains stopped. Only the
        // default-data lock owner may replace that registration; an agent copy, a test/kit copy,
        // or a process that could not take the lock must leave it alone. The plist check and
        // refresh are serialized with Settings disable.
        let agentPlist = LaunchAgent.defaultPlistURL()
        let ownsInstanceLock = controller.ownsInstanceLock
        let hasCustomDataDirectory = CommandLine.arguments.contains("--data-dir")
        DispatchQueue.global(qos: .utility).async {
            let outcome = LoginAgentSupervision.refreshIfEligible(
                agentLaunch: agentLaunch, hasCustomDataDirectory: hasCustomDataDirectory,
                ownsInstanceLock: ownsInstanceLock, plistURL: agentPlist)
            FileHandle.standardError.write(
                Data("Studio Hub login supervision: \(outcome.sanitizedDescription)\n".utf8))
        }
    }
    /// The heartbeat that dates a kill nothing can record, and the stamp of critical memory pressure
    /// that tells a low-memory stop from a plain Force Quit. See `StopRecord`.
    private var heartbeat: DispatchSourceTimer?
    private var memoryPressure: DispatchSourceMemoryPressure?
    private func startStopEvidence(directory: URL) {
        let timer = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        timer.schedule(deadline: .now() + StopRecord.heartbeatSeconds, repeating: StopRecord.heartbeatSeconds)
        timer.setEventHandler { StopRecord.heartbeat(directory: directory) }
        timer.resume()
        heartbeat = timer
        let pressure = DispatchSource.makeMemoryPressureSource(eventMask: .critical, queue: .global(qos: .utility))
        pressure.setEventHandler { StopRecord.noteCriticalMemory(directory: directory) }
        pressure.resume()
        memoryPressure = pressure
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.willPowerOffNotification, object: nil, queue: nil) { _ in
            StopRecord.notePowerOff(directory: directory)
        }
    }
    /// A deliberate Quit (menu, update relaunch, logout) tells the agent's standby copy to leave
    /// the app stopped. A crash never gets here, so the standby relaunches it; a Force Quit or a
    /// memory kill never gets here either, and `StopRecord` tells those from a crash.
    func applicationWillTerminate(_ notification: Notification) {
        guard let controller, !controller.existingInstanceDetected else { return }
        AgentStandby.markCleanQuit(directory: controller.configuration.dataDirectory)
    }
    /// Reopen the app once this process has exited. The data-folder lock is held until then, so a
    /// second copy started now would hand off to this one and quit; the helper waits for the exit.
    ///
    /// The engine is stopped FIRST and the app quits from `onStopped`. Calling `NSApp.terminate`
    /// while the engine runs answers `.terminateLater` and waits inside a nested event loop - but
    /// this is reached from the release tick, itself a main-actor task, and the engine's exit is
    /// delivered as another main-actor task, which cannot run until the first one returns. The app
    /// then never quit, the helper below waited on it for ever and the Mac stayed without a Hub
    /// (seen live 2026-10-01: four controllers after 0.6.119 -> 0.6.121).
    func relaunchAfterUpdate() {
        do { try spawnRelaunchHelper(ProcessInfo.processInfo.processIdentifier, Bundle.main.bundleURL.path) } catch {
            // Could not arrange a reopen: leave the app running rather than quit and not come back.
            controller.failure = "The update is installed, but Studio Hub could not relaunch itself (\(error.localizedDescription)). Quit and open it again."
            return
        }
        relaunching = true
        StopRecord.noteUpdating(directory: controller.configuration.dataDirectory)
        controller.forceStopWithoutAsking = true
        if controller.running { controller.stop() } else { terminateApp() }
    }
    /// The engine has stopped: finish a Quit that was waiting for it, or the app's quit for a relaunch.
    func engineStopped() {
        if quitPending { NSApp.reply(toApplicationShouldTerminate: true) }
        else if relaunching { terminateApp() }
    }
    /// Seams for the lifecycle test: starting the reopen helper and quitting the app.
    var spawnRelaunchHelper: (Int32, String) throws -> Void = { _ = try RelaunchHelper.spawn(pid: $0, app: $1) }
    var terminateApp: () -> Void = { NSApp.terminate(nil) }
    @objc func openWindow() {
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        controller?.refreshLoginStatus()
        // A new version announces itself once. The flag is consumed here, so opening the window
        // again - from the Dock, the menu bar icon, or a reopen - leaves the operator where they
        // were.
        if announceReleaseNotes {
            announceReleaseNotes = false
            controller?.select(.whatsNew)
        }
    }
    @objc func showWhatsNew() { controller?.select(.whatsNew); openWindow() }
    /// True only on the first launch of a version the operator has not seen notes for.
    private var announceReleaseNotes = false
    @objc func openLog() { controller?.openLog() }
    @objc func openSettings() { controller?.select(.settings); openWindow() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { openWindow(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard controller?.running == true || controller?.recoveryPending == true else { return .terminateNow }
        if quitPending || quitAsking { return .terminateLater }
        // Nothing running: Quit just quits. Only work in progress is worth a question. An update's
        // relaunch never asks (its drain guarantees no work, and nobody is there to answer).
        if relaunching { stopThenQuit(); return controller.running ? .terminateLater : .terminateNow }
        quitAsking = true
        Task { @MainActor in
            let busy = await activeWork() > 0
            quitAsking = false
            if busy, !confirmQuitWhileBusy() { NSApp.reply(toApplicationShouldTerminate: false); return }
            stopThenQuit()
            if !controller.running { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
    /// A stop that hangs after Quit is ended by force: the answer was already given (or none was needed).
    private func stopThenQuit() {
        quitPending = true
        controller.forceStopWithoutAsking = true
        controller.stop()
    }
}

#if !LIFECYCLE_TESTING
@main
struct StudioHubApplication {
    @MainActor static func main() {
        // The agent's copy while another Studio Hub runs: wait for it, before any AppKit setup
        // (no Dock icon, no entry in Force Quit), then let launchd decide. See `AgentStandby`.
        if ProcessInfo.processInfo.environment[LaunchAgent.launchedAtLoginEnvironmentKey] == "1",
           let configuration = try? ShellConfiguration(arguments: CommandLine.arguments, bundle: Bundle.main.bundleURL),
           let status = AgentStandby.waitForRunningCopy(directory: configuration.dataDirectory) {
            exit(status)
        }
        // launchd started this copy with the data folder free: at login, or `KeepAlive` relaunching
        // an app it ran itself. Only a crash may bring the app back (owner decision 2026-10-03).
        if ProcessInfo.processInfo.environment[LaunchAgent.launchedAtLoginEnvironmentKey] == "1",
           let configuration = try? ShellConfiguration(arguments: CommandLine.arguments, bundle: Bundle.main.bundleURL),
           AgentStandby.shouldStayStopped(directory: configuration.dataDirectory) {
            exit(0)
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
#endif
