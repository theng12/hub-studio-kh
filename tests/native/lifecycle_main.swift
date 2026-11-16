import Foundation
import Darwin
import AppKit

func requireLifecycle(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

final class LocalHubFixture: @unchecked Sendable {
    private let descriptor: Int32
    private let queue = DispatchQueue(label: "studiohub.lifecycle.http")
    private let lock = NSLock()
    private var stopped = false
    private var healthy = false
    private var healthReads = 0
    let port: Int
    private let busy: Bool

    init(busy: Bool = false) throws {
        self.busy = busy
        let socketFD = socket(AF_INET, SOCK_STREAM, 0)
        guard socketFD >= 0 else { throw POSIXError(.ENOTSOCK) }
        var reuse: Int32 = 1
        setsockopt(socketFD, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = 0
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(socketFD, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0, listen(socketFD, 16) == 0 else {
            close(socketFD)
            throw POSIXError(.EADDRINUSE)
        }
        var actual = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        guard withUnsafeMutablePointer(to: &actual, { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(socketFD, $0, &length)
            }
        }) == 0 else {
            close(socketFD)
            throw POSIXError(.EIO)
        }
        descriptor = socketFD
        port = Int(UInt16(bigEndian: actual.sin_port))
        queue.async { [weak self] in self?.serve() }
    }

    func setHealthy(_ value: Bool) { lock.lock(); healthy = value; lock.unlock() }
    func healthReadCount() -> Int { lock.lock(); defer { lock.unlock() }; return healthReads }
    func resetHealthReadCount() { lock.lock(); healthReads = 0; lock.unlock() }

    func stop() {
        lock.lock(); stopped = true; lock.unlock()
        shutdown(descriptor, SHUT_RDWR)
        close(descriptor)
    }

    private func serve() {
        while true {
            lock.lock(); let done = stopped; lock.unlock()
            if done { return }
            let client = accept(descriptor, nil, nil)
            if client < 0 { continue }
            DispatchQueue.global().async { [weak self] in self?.respond(to: client) }
        }
    }

    private func respond(to client: Int32) {
        defer { close(client) }
        var bytes = [UInt8](repeating: 0, count: 8192)
        let count = read(client, &bytes, bytes.count)
        guard count > 0 else { return }
        let request = String(decoding: bytes[..<count], as: UTF8.self)
        let path = request.split(separator: " ").dropFirst().first.map(String.init) ?? "/"
        let status: String
        let body: String
        switch path {
        case "/api/hub/catalog":
            status = "200 OK"; body = #"{"models":[]}"#
        case "/api/hub/model-packages":
            status = "200 OK"; body = #"{"models":[]}"#
        case "/api/health":
            lock.lock(); healthReads += 1; let ok = healthy; lock.unlock()
            status = ok ? "200 OK" : "503 Service Unavailable"
            body = ok ? "{\"ok\":true,\"busy\":\(busy)}" : #"{"ok":false}"#
        default:
            status = "404 Not Found"; body = #"{"detail":"fixture route missing"}"#
        }
        let response = "HTTP/1.1 \(status)\r\nContent-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
        _ = response.withCString { write(client, $0, strlen($0)) }
    }
}

final class HeldHealthRequest: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<(Data, URLResponse), Never>?

    func send(_ request: URLRequest, _ session: URLSession) async throws -> (Data, URLResponse) {
        await withCheckedContinuation { continuation in
            lock.lock(); self.continuation = continuation; lock.unlock()
        }
    }

    func hasStarted() -> Bool { lock.lock(); defer { lock.unlock() }; return continuation != nil }

    func release(url: URL) {
        lock.lock(); let pending = continuation; continuation = nil; lock.unlock()
        let body = Data(#"{"ok":true,"busy":false}"#.utf8)
        let response = HTTPURLResponse(url: url, statusCode: 200,
            httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        pending?.resume(returning: (body, response))
    }
}

func unusedPort() throws -> Int {
    let descriptor = socket(AF_INET, SOCK_STREAM, 0)
    guard descriptor >= 0 else { throw POSIXError(.ENOTSOCK) }
    defer { close(descriptor) }
    var address = sockaddr_in()
    address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_family = sa_family_t(AF_INET)
    address.sin_port = 0
    address.sin_addr.s_addr = INADDR_ANY
    guard withUnsafePointer(to: &address, { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }) == 0 else { throw POSIXError(.EADDRINUSE) }
    var actual = sockaddr_in()
    var length = socklen_t(MemoryLayout<sockaddr_in>.size)
    guard withUnsafeMutablePointer(to: &actual, { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            getsockname(descriptor, $0, &length)
        }
    }) == 0 else { throw POSIXError(.EIO) }
    return Int(UInt16(bigEndian: actual.sin_port))
}

func waitUntil(timeout: TimeInterval = 8, _ condition: @MainActor () -> Bool) async -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if await condition() { return true }
        try? await Task.sleep(for: .milliseconds(20))
    }
    return await condition()
}

@MainActor
func makeController(root: URL, server: LocalHubFixture, now: @escaping () -> Date,
                    healthRequest: ((URLRequest, URLSession) async throws -> (Data, URLResponse))? = nil,
                    maxAttempts: Int = 1, backoffSeconds: [TimeInterval] = [0],
                    agentPlistURL: URL? = nil,
                    loginAgentOperations: LoginAgentOperations? = nil,
                    loginAgentCoordinator: LoginAgentMutationCoordinator = .shared) throws -> StudioController {
    let data = root.appendingPathComponent("state")
    let models = root.appendingPathComponent("models")
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
    try Data(String(server.port).utf8).write(to: data.appendingPathComponent("fixture-port"))
    let service = root.appendingPathComponent("StudioHubService")
    try writeFixtureService(at: service)
    var configuration = try ShellConfiguration(arguments: ["lifecycle-tests", "--data-dir", data.path,
        "--models-dir", models.path, "--port", String(try unusedPort())],
        bundle: URL(fileURLWithPath: "/Applications/StudioHubFixture.app"))
    try configuration.setRecoveryPolicy(maxAttempts: maxAttempts, backoffSeconds: backoffSeconds)
    return StudioController(configuration: configuration, serviceExecutable: service,
                            agentPlistURL: agentPlistURL ?? LaunchAgent.defaultPlistURL(),
                            loginAgentOperations: loginAgentOperations,
                            loginAgentCoordinator: loginAgentCoordinator,
                            now: now, healthRequest: healthRequest)
}

/// The fixture service: it writes its readiness record and then sleeps until it is told to stop.
/// `absent` leaves the executable out entirely, which is how a start fails before anything runs.
func writeFixtureService(at service: URL, absent: Bool = false) throws {
    try? FileManager.default.removeItem(at: service)
    guard !absent else { return }
    let script = #"""
#!/bin/sh
data=""
ready=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --data-dir) data="$2"; shift 2 ;;
    --ready-file) ready="$2"; shift 2 ;;
    *) shift ;;
  esac
done
port=$(<"$data/fixture-port")
echo $$ >> "$data/launches"
tmp="$ready.tmp.$$"
printf '{"url":"http://127.0.0.1:%s","pid":%s,"version":"test","token":"abcdefghijklmnopqrstuvwxyz123456"}' "$port" "$$" > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ready"
trap 'exit 0' TERM INT
while :; do sleep 1; done
"""#
    try Data(script.utf8).write(to: service)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: service.path)
}

@MainActor
func readyPID(_ controller: StudioController) -> Int32? {
    guard let data = readPrivateRegularFile(controller.configuration.readyFile),
          let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let pid = value["pid"] as? NSNumber else { return nil }
    return pid.int32Value
}

/// How many times the fixture service has been spawned, from its own count. The ready file is
/// left behind on exit, so it cannot say whether the services are running; this can.
func launchCount(root: URL) -> Int {
    let path = root.appendingPathComponent("state/launches").path
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return 0 }
    return text.split(separator: "\n", omittingEmptySubsequences: true).count
}

/// The app's own record of why the services ended, as the fleet reads it from `last-stop.json`.
func lastStopReason(root: URL) -> String? {
    guard let data = try? Data(contentsOf: root.appendingPathComponent("state/last-stop.json")),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
    return json["reason"] as? String
}

@main
struct NativeLifecycleTests {
    @MainActor
    static func main() async throws {
        let selected = Set(CommandLine.arguments.dropFirst())
        func runs(_ name: String) -> Bool { selected.isEmpty || selected.contains(name) }
        let temporary = FileManager.default.temporaryDirectory
            .appendingPathComponent("studiohub-lifecycle-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: temporary) }

        // Readiness alone is not health. Even after more than 60 seconds on the controlled clock,
        // two ready-but-unhealthy exits exhaust a one-restart budget.
        if runs("unhealthy") {
            let server = try LocalHubFixture(); defer { server.stop() }
            var clock = Date(timeIntervalSince1970: 100)
            let controller = try makeController(root: temporary.appendingPathComponent("unhealthy"),
                                                server: server, now: { clock })
            controller.start()
            let firstUnhealthy = await waitUntil {
                readyPID(controller) != nil && server.healthReadCount() > 0
                    && controller.status == "Local services not responding"
            }
            requireLifecycle(firstUnhealthy, "first service did not process its unhealthy response")
            clock = Date(timeIntervalSince1970: 161)
            kill(readyPID(controller)!, SIGTERM)
            var previousPID = readyPID(controller)
            let restarted = await waitUntil {
                guard let pid = readyPID(controller) else { return false }
                return pid != previousPID && controller.running
            }
            requireLifecycle(restarted, "unhealthy service did not use its one restart")
            // Services that exited on their own leave the fleet's `last_stop` (0.6.243).
            requireLifecycle(lastStopReason(root: temporary.appendingPathComponent("unhealthy")) == "services_exited",
                "an unexpected service exit must be recorded as services_exited")
            previousPID = readyPID(controller)
            server.resetHealthReadCount()
            let secondUnhealthy = await waitUntil {
                server.healthReadCount() > 0 && controller.status == "Local services not responding"
            }
            requireLifecycle(secondUnhealthy, "second service did not process its unhealthy response")
            clock = Date(timeIntervalSince1970: 222)
            kill(previousPID!, SIGTERM)
            let exhausted = await waitUntil { controller.status.contains("Still trying to start the services") }
            requireLifecycle(exhausted,
                "mere readiness reset the crash budget: \(controller.status)")
        }

        // A valid authenticated health interval does reset that same budget, allowing a third run.
        if runs("healthy") {
            let server = try LocalHubFixture(busy: true); defer { server.stop() }
            var clock = Date(timeIntervalSince1970: 100)
            let controller = try makeController(root: temporary.appendingPathComponent("healthy"),
                                                server: server, now: { clock })
            controller.start()
            let firstUnhealthy = await waitUntil {
                readyPID(controller) != nil && server.healthReadCount() > 0
                    && controller.status == "Local services not responding"
            }
            requireLifecycle(firstUnhealthy, "first service did not process its unhealthy response")
            kill(readyPID(controller)!, SIGTERM)
            let firstPID = readyPID(controller)
            server.setHealthy(true)
            let secondReady = await waitUntil {
                guard let pid = readyPID(controller) else { return false }
                return pid != firstPID && controller.running
            }
            requireLifecycle(secondReady, "second service did not become ready")
            server.resetHealthReadCount()
            // Only the processed health response sets working; startup already sets ready.
            let healthyReady = await waitUntil {
                server.healthReadCount() > 0 && controller.status == "Local services working"
            }
            requireLifecycle(healthyReady, "second service did not report authenticated health")
            let healthyPID = readyPID(controller)!
            clock = Date(timeIntervalSince1970: 161)
            kill(healthyPID, SIGTERM)
            let resetBudget = await waitUntil {
                guard let pid = readyPID(controller) else { return false }
                return pid != healthyPID && controller.running
            }
            requireLifecycle(resetBudget, "authenticated healthy uptime did not reset the restart budget")
            controller.stop()
            _ = await waitUntil { !controller.running }
        }

        // Deliver a successful health response only after its process exited. The terminal
        // failure must remain authoritative; the old request cannot revive a ready status.
        if runs("late") {
            let heldHealth = HeldHealthRequest()
            let server = try LocalHubFixture(); defer { server.stop() }
            let controller = try makeController(root: temporary.appendingPathComponent("late"),
                                                server: server, now: Date.init,
                                                healthRequest: heldHealth.send, maxAttempts: 0)
            controller.start()
            let healthStarted = await waitUntil { heldHealth.hasStarted() }
            requireLifecycle(healthStarted, "health request was not held")
            kill(readyPID(controller)!, SIGTERM)
            let terminal = await waitUntil { controller.status.contains("repeated service failures") }
            requireLifecycle(terminal,
                "worker exit did not reach terminal recovery state")
            heldHealth.release(url: controller.readyURL ?? URL(string: "http://127.0.0.1/health")!)
            try? await Task.sleep(for: .milliseconds(100))
            requireLifecycle(controller.status.contains("repeated service failures"),
                "late health response overwrote terminal state: \(controller.status)")
        }

        // Startup settings (owner, 2026-10-02): the fleet default for "Start services on startup", the
        // requests the fleet sends, and the sidebar's one-line warning. The login agent itself is not
        // touched here: toggling it would run launchctl against the real user session.
        if runs("startup") {
            let root = temporary.appendingPathComponent("startup")
            let models = root.appendingPathComponent("models")
            try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
            let bundle = URL(fileURLWithPath: "/Applications/StudioHubFixture.app")
            func make(data: URL) throws -> StudioController {
                let configuration = try ShellConfiguration(
                    arguments: ["lifecycle-tests", "--data-dir", data.path, "--models-dir", models.path], bundle: bundle)
                return StudioController(configuration: configuration,
                                        serviceExecutable: root.appendingPathComponent("StudioHubService"),
                                        loginAgentStatus: { _ in .disabled },
                                        legacyLoginItemStatus: { .notRegistered },
                                        legacyLoginItemUnregister: {}, now: Date.init)
            }
            let standaloneData = root.appendingPathComponent("standalone")
            let joinedData = root.appendingPathComponent("joined")
            for directory in [standaloneData, joinedData] {
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            }
            do {
                let alone = try make(data: standaloneData)
                alone.applyFleetServicesDefaultAtLaunch()
                requireLifecycle(!alone.configuration.startServicesAtLogin && !alone.servicesWillNotAutoStart,
                                 "a Mac in no fleet keeps services off and the sidebar says nothing")
                let report = alone.startupReport()
                requireLifecycle(!report.openAtLogin && report.openAtLoginSaved && !report.startServicesOnLaunch,
                                 "the report is the actual login state, the saved choice and the services setting")
                alone.observeFleetRole("standalone")
                requireLifecycle(!alone.configuration.startServicesAtLogin, "a standalone Hub changes nothing")
            }
            do {
                let joined = try make(data: joinedData)
                joined.configuration.controllerAddress = "100.64.0.9:47873"
                requireLifecycle(joined.servicesWillNotAutoStart,
                                 "a joined Mac with services off is the one state the sidebar warns about")
                joined.applyFleetServicesDefaultAtLaunch()
                requireLifecycle(joined.configuration.startServicesAtLogin && !joined.servicesWillNotAutoStart,
                                 "the first launch after the update turns services on for a joined Mac")
                // The owner turns it off on purpose through a fleet request; the next launch respects it.
                let off = joined.applyStartupRequest(NativeWorkspaceStore.StartupRequest(id: "r1", action: "set_start_services_on_launch", value: false))
                requireLifecycle(off == NativeWorkspaceStore.StartupOutcome(id: "r1", ok: true, detail: nil) && !joined.configuration.startServicesAtLogin,
                                 "a request to turn services off is applied and answered")
                requireLifecycle(joined.failure == nil, "a fleet request never raises the banner on the Mac")
                joined.applyFleetServicesDefaultAtLaunch()
                requireLifecycle(!joined.configuration.startServicesAtLogin,
                                 "a recorded choice is never overridden by the default")
                let on = joined.applyStartupRequest(NativeWorkspaceStore.StartupRequest(id: "r2", action: "set_start_services_on_launch", value: true))
                requireLifecycle(on?.ok == true && joined.configuration.startServicesAtLogin, "and back on")
                let missing = joined.applyStartupRequest(NativeWorkspaceStore.StartupRequest(id: "r3", action: "set_start_services_on_launch", value: nil))
                requireLifecycle(missing?.ok == false, "a request with no value is refused, not guessed")
                let unknown = joined.applyStartupRequest(NativeWorkspaceStore.StartupRequest(id: "r4", action: "format_disk", value: true))
                requireLifecycle(unknown?.ok == false && unknown?.detail?.contains("format_disk") == true,
                                 "an unknown request is refused by name")
                requireLifecycle(joined.applyStartupRequest(NativeWorkspaceStore.StartupRequest(id: "r5", action: "restart_services", value: nil)) == nil,
                                 "a restart has no outcome to report: the Hub it would answer to is restarting")
            }
            // The choice made above survived: a relaunch reads it from the preferences file.
            let relaunched = try make(data: joinedData)
            requireLifecycle(relaunched.configuration.startServicesAtLogin && relaunched.configuration.startServicesChoiceRecorded,
                             "the setting and the record of the choice survive a relaunch")
            // A controller has no controller address; the running Hub says what it is.
            let controllerData = root.appendingPathComponent("controller")
            try FileManager.default.createDirectory(at: controllerData, withIntermediateDirectories: true)
            let head = try make(data: controllerData)
            requireLifecycle(!head.servicesWillNotAutoStart, "before a Hub reports, nothing is known")
            head.observeFleetRole("controller")
            requireLifecycle(head.configuration.startServicesAtLogin && head.fleetRoleIsMember && !head.servicesWillNotAutoStart,
                             "a Hub that reports itself a controller turns services on for that Mac")
        }

        // The one-time move off the old SMAppService login item. The reader and the unregister are
        // injected, so neither branch touches the real system; driving it through a real
        // StudioController proves the persisted flag across two launches.
        if runs("migration") {
            let root = temporary.appendingPathComponent("migration")
            let data = root.appendingPathComponent("state")
            let models = root.appendingPathComponent("models")
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
            try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
            let bundle = URL(fileURLWithPath: "/Applications/StudioHubFixture.app")
            func makeMigrationController(data: URL,
                                         status: @escaping () -> LoginMigration.OldStatus,
                                         unregister: @escaping () throws -> Void) throws -> StudioController {
                let configuration = try ShellConfiguration(
                    arguments: ["lifecycle-tests", "--data-dir", data.path, "--models-dir", models.path],
                    bundle: bundle)
                return StudioController(configuration: configuration,
                                        serviceExecutable: root.appendingPathComponent("StudioHubService"),
                                        loginAgentStatus: { _ in .disabled },
                                        legacyLoginItemStatus: status,
                                        legacyLoginItemUnregister: unregister,
                                        now: Date.init)
            }

            var unregisterCalls = 0
            let enabled: () -> LoginMigration.OldStatus = { .enabled }
            let unregister: () throws -> Void = { unregisterCalls += 1 }
            do {
                let first = try makeMigrationController(data: data, status: enabled, unregister: unregister)
                first.migrateLegacyLoginItemIfNeeded()
                requireLifecycle(unregisterCalls == 1 && first.configuration.loginMigrationComplete,
                                 "the first launch must remove the old login item and record it")
                requireLifecycle(first.failure == nil,
                                 "a clean migration must not report a failure, got: \(first.failure ?? "nil")")
            }
            // The data-folder lock is released when the first controller is gone.
            let second = try makeMigrationController(data: data, status: enabled, unregister: unregister)
            requireLifecycle(second.configuration.loginMigrationComplete,
                             "the recorded migration flag must survive a relaunch")
            second.migrateLegacyLoginItemIfNeeded()
            requireLifecycle(unregisterCalls == 1,
                             "a second launch must not unregister the old login item again")
            requireLifecycle(second.failure == nil, "the second launch must not repeat a clean migration")

            // A failing unregister must not stop the app; the notice names the manual fix once.
            let failingData = root.appendingPathComponent("failing-state")
            try FileManager.default.createDirectory(at: failingData, withIntermediateDirectories: true)
            var attempts = 0
            let failing = try makeMigrationController(
                data: failingData, status: enabled,
                unregister: { attempts += 1; throw ShellFailure(message: "not permitted") })
            failing.migrateLegacyLoginItemIfNeeded()
            requireLifecycle(attempts == 1, "the failing unregister must have been attempted")
            requireLifecycle(failing.failure?.contains("Login Items") == true,
                             "a failed migration must keep the app running and say where to remove the old entry, got: \(failing.failure ?? "nil")")
            requireLifecycle(failing.configuration.loginMigrationComplete,
                             "a caught failure is still an attempt and must be recorded so it is reported once")
        }

        // Quit asks only when work is in progress. An alert cannot be answered headless, so the
        // question is a seam (`confirmQuitWhileBusy`); a watchdog fails a Quit that never returns.
        if runs("quit") {
            let server = try LocalHubFixture(); defer { server.stop() }
            for (name, busy, answer) in [("idle", 0, true), ("busy-keep", 2, false), ("busy-quit", 2, true)] {
                let controller = try makeController(root: temporary.appendingPathComponent("quit-\(name)"),
                                                    server: server, now: Date.init)
                controller.start()
                let started = await waitUntil { readyPID(controller) != nil && controller.running }
                requireLifecycle(started, "\(name): the service did not start")
                let pid = readyPID(controller)!
                var asked = 0
                let delegate = AppDelegate()
                delegate.controller = controller
                delegate.activeWork = { busy }
                delegate.quitIsAttended = { true }
                delegate.confirmQuitWhileBusy = { asked += 1; return answer }
                let reply = delegate.applicationShouldTerminate(NSApplication.shared)
                requireLifecycle(reply == .terminateLater, "\(name): Quit must answer later, got \(reply)")
                if answer {
                    let gone = await waitUntil { !controller.running && kill(pid, 0) != 0 }
                    requireLifecycle(gone, "\(name): Quit did not stop the engine")
                    requireLifecycle(controller.forceStopWithoutAsking,
                                     "\(name): a hung stop after Quit must be force-stopped, not asked about")
                } else {
                    _ = await waitUntil { asked == 1 }
                    try? await Task.sleep(for: .milliseconds(300))
                    requireLifecycle(controller.running && !controller.forceStopWithoutAsking,
                                     "\(name): Keep Running must leave the engine running")
                    controller.stop()
                    _ = await waitUntil { !controller.running }
                }
                requireLifecycle(asked == (busy > 0 ? 1 : 0),
                                 "\(name): asked \(asked) times; only Quit over running work asks, once")
            }
            // The case the installer actually hits: busy, no window, app not active. It must not
            // wait on an alert; it must stop and let the quit continue.
            let unattendedController = try makeController(
                root: temporary.appendingPathComponent("quit-unattended"),
                server: server, now: Date.init)
            unattendedController.start()
            let unattendedStarted = await waitUntil {
                readyPID(unattendedController) != nil && unattendedController.running
            }
            requireLifecycle(unattendedStarted, "unattended: the service did not start")
            var askedUnattended = 0
            let unattended = AppDelegate()
            unattended.controller = unattendedController
            unattended.activeWork = { 2 }
            unattended.quitIsAttended = { false }
            unattended.confirmQuitWhileBusy = { askedUnattended += 1; return false }
            let unattendedReply = unattended.applicationShouldTerminate(NSApplication.shared)
            requireLifecycle(unattendedReply == .terminateLater,
                             "unattended: Quit must still answer later")
            let unattendedGone = await waitUntil { !unattendedController.running }
            requireLifecycle(unattendedGone, "unattended: Quit did not stop the engine")
            requireLifecycle(askedUnattended == 0,
                             "unattended: an alert was shown without a window or active app")
        }

        // The prompt deadline must fire inside NSAlert.runModal's modal run loop. A watchdog
        // fails the process instead of hanging if the timer never fires.
        if runs("quit-deadline") {
            let alert = NSAlert()
            alert.messageText = "Quit Hub Studio KH and stop its engine?"
            alert.addButton(withTitle: "Keep Running")
            alert.addButton(withTitle: "Quit and Stop")
            alert.window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
            let deadlineLock = NSLock()
            var deadlineFinished = false
            let watchdog = Thread {
                Thread.sleep(forTimeInterval: 3)
                deadlineLock.lock()
                let finished = deadlineFinished
                deadlineLock.unlock()
                if !finished {
                    FileHandle.standardError.write(Data("quit-deadline: timer never fired\n".utf8))
                    exit(2)
                }
            }
            watchdog.start()
            let began = Date()
            let response = QuitPrompt.run(alert, deadline: 0.2)
            let elapsed = Date().timeIntervalSince(began)
            deadlineLock.lock()
            deadlineFinished = true
            deadlineLock.unlock()
            requireLifecycle(response == .alertSecondButtonReturn,
                             "quit-deadline: timeout must choose Quit and Stop, got \(response.rawValue)")
            requireLifecycle(elapsed < 2,
                             "quit-deadline: the modal deadline did not fire promptly (\(elapsed)s)")
        }

        // Supervision is allowed only for a normal, default-data launch that actually owns the
        // instance lock. This drives the same eligibility helper as AppDelegate, with a disposable
        // label and injected runner; it never invokes launchctl or the production label.
        if runs("login-agent-refresh-guard") {
            let root = temporary.appendingPathComponent("login-agent-refresh-guard")
            let server = try LocalHubFixture(); defer { server.stop() }
            let owner = try makeController(root: root.appendingPathComponent("owner"),
                                           server: server, now: Date.init)
            let plist = root.appendingPathComponent("fixture.plist")
            try Data("fixture".utf8).write(to: plist)
            let label = "test.local.studiohub.lifecycle-refresh"
            var commands: [[String]] = []
            let runner: LaunchctlRunner = { arguments in
                commands.append(arguments)
                return .exited(0)
            }
            func refresh(_ controller: StudioController, agent: Bool = false,
                         customData: Bool = false, installed: Bool = true) -> LoginAgentRefreshOutcome {
                let effectivePlist = installed ? plist : root.appendingPathComponent("missing.plist")
                return LoginAgentSupervision.refreshIfEligible(
                    agentLaunch: agent, hasCustomDataDirectory: customData,
                    ownsInstanceLock: controller.ownsInstanceLock,
                    plistURL: effectivePlist, label: label,
                    coordinator: LoginAgentMutationCoordinator(), runner: runner)
            }
            commands.removeAll()
            requireLifecycle(refresh(owner, agent: true) == .skipped && commands.isEmpty,
                             "agent launch must not refresh its own registration")
            requireLifecycle(refresh(owner, customData: true) == .skipped && commands.isEmpty,
                             "custom-data launch must not refresh the default agent")
            requireLifecycle(refresh(owner, installed: false) == .skipped && commands.isEmpty,
                             "disabled login startup must not run launchctl")

            let unavailableRoot = root.appendingPathComponent("unavailable")
            let unavailableData = unavailableRoot.appendingPathComponent("state")
            try FileManager.default.createDirectory(at: unavailableData, withIntermediateDirectories: true)
            try FileManager.default.createSymbolicLink(
                at: unavailableData.appendingPathComponent("app.lock"),
                withDestinationURL: unavailableRoot.appendingPathComponent("lock-target"))
            let unavailable = try makeController(root: unavailableRoot, server: server, now: Date.init)
            commands.removeAll()
            requireLifecycle(!unavailable.ownsInstanceLock && refresh(unavailable) == .skipped
                             && commands.isEmpty,
                             "an unavailable lock must never refresh registration")

            let heldRoot = root.appendingPathComponent("already-held")
            let first = try makeController(root: heldRoot, server: server, now: Date.init)
            let alreadyHeld = try makeController(root: heldRoot, server: server, now: Date.init)
            _ = first
            commands.removeAll()
            requireLifecycle(alreadyHeld.existingInstanceDetected && !alreadyHeld.ownsInstanceLock
                             && refresh(alreadyHeld) == .skipped && commands.isEmpty,
                             "a lock held by another app must never refresh registration")

            owner.start()
            requireLifecycle(owner.starting && !owner.canConfigure && owner.ownsInstanceLock,
                             "fixture must prove services begin before supervision without losing lock ownership")
            commands.removeAll()
            requireLifecycle(refresh(owner) == .registrationAccepted(bootout: .unloaded)
                             && commands.count == 2,
                             "the actual lock owner must refresh even while services are starting")
            requireLifecycle(owner.running && owner.starting && owner.ownsInstanceLock,
                             "registration refresh must leave the actual app and its services running")
            owner.stop()
            _ = await waitUntil { !owner.running }

            let settingsRoot = root.appendingPathComponent("settings-failure")
            let settingsPlist = settingsRoot.appendingPathComponent("fixture.plist")
            try FileManager.default.createDirectory(at: settingsRoot, withIntermediateDirectories: true)
            try Data("fixture".utf8).write(to: settingsPlist)
            var settingsRemoveCount = 0
            var settingsWriteCount = 0
            let settingsController = try makeController(
                root: settingsRoot, server: server, now: Date.init,
                agentPlistURL: settingsPlist,
                loginAgentOperations: LoginAgentOperations(
                    write: { settingsWriteCount += 1 },
                    bootout: { .timedOut(terminationConfirmed: false) },
                    remove: { settingsRemoveCount += 1 }, bootstrap: {}),
                loginAgentCoordinator: LoginAgentMutationCoordinator())
            let settingsFailure = settingsController.changeLogin(false)
            requireLifecycle(settingsFailure != nil && settingsRemoveCount == 0
                             && FileManager.default.fileExists(atPath: settingsPlist.path)
                             && settingsController.loginStatus == .enabled
                             && !settingsController.configuration.startAtLogin,
                             "Settings must remember the requested preference but report uncertain unload and preserve the plist")
            let quarantinedFailure = settingsController.changeLogin(true)
            requireLifecycle(quarantinedFailure != nil && settingsWriteCount == 0
                             && settingsRemoveCount == 0
                             && FileManager.default.fileExists(atPath: settingsPlist.path),
                             "Settings must reject later mutations while the prior unload remains uncertain")

            let signalRoot = root.appendingPathComponent("settings-signal")
            let signalPlist = signalRoot.appendingPathComponent("fixture.plist")
            try FileManager.default.createDirectory(at: signalRoot, withIntermediateDirectories: true)
            try Data("fixture".utf8).write(to: signalPlist)
            var signalRemoveCount = 0
            var signalWriteCount = 0
            let signalController = try makeController(
                root: signalRoot, server: server, now: Date.init,
                agentPlistURL: signalPlist,
                loginAgentOperations: LoginAgentOperations(
                    write: { signalWriteCount += 1 },
                    bootout: { .signaled(LaunchAgent.jobAbsentExitStatus) },
                    remove: { signalRemoveCount += 1 }, bootstrap: {}),
                loginAgentCoordinator: LoginAgentMutationCoordinator())
            let signalFailure = signalController.changeLogin(false)
            requireLifecycle(signalFailure != nil && signalRemoveCount == 0
                             && FileManager.default.fileExists(atPath: signalPlist.path)
                             && signalController.loginStatus == .enabled
                             && !signalController.configuration.startAtLogin,
                             "Settings must not treat a signaled status 3 as a completed disable")
            let signalQuarantinedFailure = signalController.changeLogin(true)
            requireLifecycle(signalQuarantinedFailure != nil && signalWriteCount == 0
                             && signalRemoveCount == 0
                             && FileManager.default.fileExists(atPath: signalPlist.path),
                             "Settings must quarantine later mutations after signaled launchctl settlement")
        }


        // The launch's own start, and what a start that fails does about it. This is the shape of
        // the 2026-10-31 outage at 1300 and 1400: an app that was up, answering, with
        // "Start services on startup" on, and with every studio down because its one start had
        // failed and nothing ever tried again.
        if runs("launch-retry") {
            let server = try LocalHubFixture(); defer { server.stop() }

            // A start that fails is retried, and the retry brings the services up when the cause
            // has gone. The service is missing at the first attempt - the same class of failure as
            // a model library that mounts a second later - and present by the second.
            do {
                let root = temporary.appendingPathComponent("launch-retry-later")
                let controller = try makeController(root: root, server: server, now: Date.init,
                                                    maxAttempts: 5, backoffSeconds: [0, 0, 0, 0, 0])
                try writeFixtureService(at: root.appendingPathComponent("StudioHubService"), absent: true)
                controller.start()
                let failed = await waitUntil { controller.failure != nil }
                requireLifecycle(failed, "launch-retry: a failed start must say so, not stay silent")
                requireLifecycle(controller.failure!.contains("missing") || controller.status == "Unable to start local services",
                                 "launch-retry: the reason must be the one that happened, got \(controller.failure!)")
                // Without this the app sat down for nineteen hours: the first failure scheduled
                // nothing, so no second attempt was ever made.
                requireLifecycle(controller.recoveryPending || controller.canCancelRecovery,
                                 "launch-retry: a failed launch start must enter the retry ladder")
                try writeFixtureService(at: root.appendingPathComponent("StudioHubService"))
                let up = await waitUntil { readyPID(controller) != nil && controller.running }
                requireLifecycle(up, "launch-retry: the retry never started the services; status is \(controller.status)")
                requireLifecycle(readyPID(controller) != nil, "launch-retry: no readiness record after the retry")
                controller.stop()
                _ = await waitUntil { !controller.running }
            }

            // A start that fails every time gives up with a reason the operator can act on, and it
            // says which policy stopped it rather than falling silent.
            do {
                let root = temporary.appendingPathComponent("launch-retry-giveup")
                let controller = try makeController(root: root, server: server, now: Date.init,
                                                    maxAttempts: 0, backoffSeconds: [1])
                try writeFixtureService(at: root.appendingPathComponent("StudioHubService"), absent: true)
                controller.start()
                let gaveUp = await waitUntil(timeout: 8) {
                    controller.status.contains("repeated service failures")
                }
                requireLifecycle(gaveUp, "launch-retry: a start that cannot succeed must give up loudly, got \(controller.status)")
                requireLifecycle(controller.failure?.contains("repeated service failures") == true,
                                 "launch-retry: giving up must be recorded, not only shown")
                requireLifecycle(!controller.recoveryPending, "launch-retry: a policy of zero attempts must stop retrying")
                controller.stop()
            }

            // A start that fails every time under the *default* policy: five fast attempts, then it
            // slows down and says so. It must never fall silent between attempts - an unattended Mac
            // that is being retried has to be able to say it is still being retried, and why.
            do {
                let root = temporary.appendingPathComponent("launch-retry-exhaust")
                let controller = try makeController(root: root, server: server, now: Date.init,
                                                    maxAttempts: 5, backoffSeconds: [0, 0, 0, 0, 0])
                try writeFixtureService(at: root.appendingPathComponent("StudioHubService"), absent: true)
                controller.start()
                let fastPhases = await waitUntil(timeout: 8) {
                    controller.status.contains("Recovering local services")
                }
                requireLifecycle(fastPhases, "launch-retry: the first failure must be on the ladder, got \(controller.status)")
                requireLifecycle(controller.recoveryPending,
                                 "launch-retry: the ladder must still be running while it retries")
                // Past the five rungs it slows down and keeps the reason on screen rather than
                // reporting a success it never achieved.
                let slowed = await waitUntil(timeout: 12) {
                    controller.status.contains("Still trying to start the services")
                }
                requireLifecycle(slowed, "launch-retry: past the bound it must keep trying loudly, got \(controller.status)")
                requireLifecycle(controller.failure?.contains("last failure") == true,
                                 "launch-retry: the slow phase must record the last failure, got \(String(describing: controller.failure))")
                requireLifecycle(!controller.running && readyPID(controller) == nil,
                                 "launch-retry: a start that never succeeded must not report running")
                requireLifecycle(controller.status != "Local services ready",
                                 "launch-retry: a start that never succeeded must not claim to be ready")
                controller.stop()
                requireLifecycle(!controller.recoveryPending, "launch-retry: stop must end the slow retry too")
            }

            // A start that fails, then the owner stops it: the ladder must stop with them. A retry
            // that fires after a deliberate stop would be the app arguing with its owner.
            do {
                let root = temporary.appendingPathComponent("launch-retry-stop")
                let controller = try makeController(root: root, server: server, now: Date.init,
                                                    maxAttempts: 5, backoffSeconds: [1, 1, 1, 1, 1])
                try writeFixtureService(at: root.appendingPathComponent("StudioHubService"), absent: true)
                controller.start()
                let pending = await waitUntil { controller.canCancelRecovery }
                requireLifecycle(pending, "launch-retry: no retry to cancel before the stop")
                controller.stop()
                requireLifecycle(!controller.recoveryPending, "launch-retry: an owner's stop must cancel the pending retry")
                requireLifecycle(controller.status == "Local services stopped",
                                 "launch-retry: an owner's stop must be the last word, got \(controller.status)")
                try? await Task.sleep(for: .milliseconds(400))
                requireLifecycle(!controller.running && readyPID(controller) == nil,
                                 "launch-retry: the services came back after a deliberate stop")
                requireLifecycle(controller.status == "Local services stopped",
                                 "launch-retry: a late retry overwrote the owner's stop, got \(controller.status)")
            }

            // The two launch decisions, driven against a real controller: the setting governs the
            // login launch, and an update's reopen always starts. A Mac with the setting off must
            // not be started by its own login, and one with it on must be.
            do {
                let root = temporary.appendingPathComponent("launch-retry-decide")
                let controller = try makeController(root: root, server: server, now: Date.init)
                controller.configuration.chooseStartServices(false)
                try controller.configuration.save()
                let off = LaunchStart.decide(loginLaunch: true, startServicesOnLaunch: controller.configuration.startServicesAtLogin,
                                             hasLibrary: controller.hasLibrary, canConfigure: controller.canConfigure)
                requireLifecycle(off == .stayStopped(LaunchStart.stoppedByChoice),
                                 "launch-retry: the setting off must leave a login launch stopped, got \(off)")
                controller.noteLaunchDeclined(LaunchStart.stoppedByChoice)
                requireLifecycle(controller.status.contains("Start services on startup is off"),
                                 "launch-retry: the reason must be on the Mac, got \(controller.status)")
                controller.configuration.chooseStartServices(true)
                try controller.configuration.save()
                let on = LaunchStart.decide(loginLaunch: true, startServicesOnLaunch: controller.configuration.startServicesAtLogin,
                                            hasLibrary: controller.hasLibrary, canConfigure: controller.canConfigure)
                requireLifecycle(on == .start, "launch-retry: the setting on must start a login launch, got \(on)")
                // An update's reopen is not a login launch: it starts whatever the setting says.
                let update = LaunchStart.decide(loginLaunch: false, startServicesOnLaunch: controller.configuration.startServicesAtLogin,
                                                hasLibrary: controller.hasLibrary, canConfigure: controller.canConfigure)
                requireLifecycle(update == .start, "launch-retry: an update's reopen must start, got \(update)")
                try writeFixtureService(at: root.appendingPathComponent("StudioHubService"))
                controller.start()
                let started = await waitUntil { readyPID(controller) != nil && controller.running }
                requireLifecycle(started, "launch-retry: the launched app never brought its services up")
                controller.stop()
                _ = await waitUntil { !controller.running }
            }
        }

        // A service that is running but has stopped answering is ended by the app, with the reason
        // recorded where the fleet reads it. This is the shape of the 2026-10-31 outage at 1400:
        // an app that was up, services that were running, health checks that never came back, and
        // nothing in the app that would have ended them - `start_services` by hand was the only
        // thing that did. An exit is already recovered; silence was not.
        if runs("silent") {
            let backoff: [TimeInterval] = [0, 0, 0, 0, 0]

            // Silence past the bound is ended and restarted, and the fleet is told why.
            do {
                let root = temporary.appendingPathComponent("silent-hang")
                let server = try LocalHubFixture(); defer { server.stop() }
                var clock = Date(timeIntervalSince1970: 100)
                let controller = try makeController(root: root, server: server, now: { clock },
                                                    maxAttempts: 5, backoffSeconds: backoff)
                server.setHealthy(true)
                controller.start()
                let up = await waitUntil {
                    readyPID(controller) != nil && controller.status == "Local services ready"
                }
                requireLifecycle(up, "silent: the service never became ready, \(controller.status)")
                let before = readyPID(controller)!
                let launches = launchCount(root: root)

                // The Hub stops answering. Nothing may end it yet: one slow answer, however busy
                // this Mac has been, is not a hung service.
                server.setHealthy(false)
                let wentQuiet = await waitUntil { controller.status == "Local services not responding" }
                requireLifecycle(wentQuiet, "silent: a failed health check must be visible, \(controller.status)")
                server.resetHealthReadCount()
                clock = clock.addingTimeInterval(60)
                let checkedUnderBound = await waitUntil { server.healthReadCount() > 0 }
                requireLifecycle(checkedUnderBound, "silent: the health check never ran again")
                requireLifecycle(readyPID(controller) == before && controller.running,
                                 "silent: a silence under the bound must not end the services")

                // Past the bound it does, and the reason is on disk before anything is torn down:
                // a hung Hub cannot publish while it is hung, so the record has to exist first.
                clock = clock.addingTimeInterval(60)
                let recorded = await waitUntil(timeout: 10) {
                    lastStopReason(root: root) == "stopped_answering"
                }
                requireLifecycle(recorded,
                    "silent: the fleet must be told why, got \(String(describing: lastStopReason(root: root)))")

                // And it comes back by itself - the whole point, and the thing `start_services`
                // by hand used to do by hand.
                server.setHealthy(true)
                let back = await waitUntil(timeout: 12) {
                    guard let pid = readyPID(controller), pid != before else { return false }
                    return controller.running && controller.status == "Local services ready"
                }
                requireLifecycle(back, "silent: the services never came back, \(controller.status)")
                requireLifecycle(launchCount(root: root) > launches,
                                 "silent: a silent service must be started again, not left down")
                controller.stop()
                _ = await waitUntil { !controller.running }
            }

            // The owner stops the services while they are silent: nothing starts them again, and
            // the stop is theirs - it is never recorded as a service that stopped answering.
            do {
                let root = temporary.appendingPathComponent("silent-owner-stop")
                let server = try LocalHubFixture(); defer { server.stop() }
                var clock = Date(timeIntervalSince1970: 100)
                let controller = try makeController(root: root, server: server, now: { clock },
                                                    maxAttempts: 5, backoffSeconds: backoff)
                server.setHealthy(true)
                controller.start()
                let up = await waitUntil {
                    readyPID(controller) != nil && controller.status == "Local services ready"
                }
                requireLifecycle(up, "silent: the service never became ready, \(controller.status)")
                server.setHealthy(false)
                let wentQuiet = await waitUntil { controller.status == "Local services not responding" }
                requireLifecycle(wentQuiet, "silent: a failed health check must be visible, \(controller.status)")

                controller.stop()
                let stopped = await waitUntil { !controller.running }
                requireLifecycle(stopped, "silent: the owner's stop must take effect")
                requireLifecycle(controller.status == "Local services stopped",
                                 "silent: an owner's stop must be the last word, got \(controller.status)")
                requireLifecycle(lastStopReason(root: root) != "stopped_answering",
                    "silent: an owner's stop must not be recorded as a silent service")

                // Well past the bound, with the clock moved on, nothing comes back.
                let launches = launchCount(root: root)
                clock = clock.addingTimeInterval(600)
                try? await Task.sleep(for: .milliseconds(400))
                requireLifecycle(!controller.running && launchCount(root: root) == launches,
                                 "silent: a service the owner stopped must stay stopped")
                requireLifecycle(controller.status == "Local services stopped",
                                 "silent: a late restart overwrote the owner's stop, got \(controller.status)")
            }

            // The harder version of the same rule: the bound is reached, the services are being
            // ended, and the owner stops them while that is in flight. The recovery must be
            // cancelled - a stop the owner asked for is never followed by an app of its own.
            do {
                let root = temporary.appendingPathComponent("silent-owner-cancel")
                let server = try LocalHubFixture(); defer { server.stop() }
                var clock = Date(timeIntervalSince1970: 100)
                let controller = try makeController(root: root, server: server, now: { clock },
                                                    maxAttempts: 5, backoffSeconds: [5, 0, 0, 0, 0])
                server.setHealthy(true)
                controller.start()
                let up = await waitUntil {
                    readyPID(controller) != nil && controller.status == "Local services ready"
                }
                requireLifecycle(up, "silent: the service never became ready, \(controller.status)")
                server.setHealthy(false)
                let wentQuiet = await waitUntil { controller.status == "Local services not responding" }
                requireLifecycle(wentQuiet, "silent: a failed health check must be visible, \(controller.status)")
                clock = clock.addingTimeInterval(120)
                let recorded = await waitUntil(timeout: 10) {
                    lastStopReason(root: root) == "stopped_answering"
                }
                requireLifecycle(recorded, "silent: the fleet must be told why before anything is torn down")

                // The recovery is on a five-second rung, so the owner's stop lands inside it.
                let pending = await waitUntil { controller.recoveryPending || controller.canCancelRecovery }
                requireLifecycle(pending, "silent: a service ended for silence must enter the ladder")
                requireLifecycle(controller.failure?.contains("stopped answering") == true,
                    "silent: the reason must be on screen, not only in the record, "
                        + "got \(String(describing: controller.failure))")
                controller.stop()
                requireLifecycle(!controller.recoveryPending,
                                 "silent: an owner's stop must cancel the pending recovery")
                let launches = launchCount(root: root)
                clock = clock.addingTimeInterval(600)
                try? await Task.sleep(for: .milliseconds(600))
                requireLifecycle(!controller.running && launchCount(root: root) == launches,
                                 "silent: a recovery the owner cancelled must not start the services")
                requireLifecycle(controller.status == "Local services stopped",
                                 "silent: an owner's stop must be the last word, got \(controller.status)")
            }

            // Slow but alive is never killed: the silence clock is thrown away by the next good
            // answer, so an evening of intermittent slowness never ends a working service.
            do {
                let root = temporary.appendingPathComponent("silent-slow")
                let server = try LocalHubFixture(); defer { server.stop() }
                var clock = Date(timeIntervalSince1970: 100)
                let controller = try makeController(root: root, server: server, now: { clock },
                                                    maxAttempts: 5, backoffSeconds: backoff)
                server.setHealthy(true)
                controller.start()
                let up = await waitUntil {
                    readyPID(controller) != nil && controller.status == "Local services ready"
                }
                requireLifecycle(up, "silent: the service never became ready, \(controller.status)")
                let launches = launchCount(root: root)

                server.setHealthy(false)
                let firstQuiet = await waitUntil { controller.status == "Local services not responding" }
                requireLifecycle(firstQuiet, "silent: a failed health check must be visible")
                clock = clock.addingTimeInterval(60)
                server.setHealthy(true)
                let answered = await waitUntil { controller.status == "Local services ready" }
                requireLifecycle(answered, "silent: a service that answers again must be seen, \(controller.status)")

                // A second silence, with the clock already past the first one's bound. If the
                // first silence were still being counted, this is the moment it would fire.
                clock = clock.addingTimeInterval(60)
                server.setHealthy(false)
                let secondQuiet = await waitUntil { controller.status == "Local services not responding" }
                requireLifecycle(secondQuiet, "silent: a failed health check must be visible")
                try? await Task.sleep(for: .milliseconds(1_500))
                requireLifecycle(controller.running && launchCount(root: root) == launches,
                    "silent: a service that answered again must never be killed, "
                        + "launches \(launchCount(root: root)) of \(launches)")
                controller.stop()
                _ = await waitUntil { !controller.running }
            }
        }

        SecretChecks.run()
        let views = temporary.appendingPathComponent("views")
        try FileManager.default.createDirectory(at: views.appendingPathComponent("models"), withIntermediateDirectories: true)
        try ViewChecks.run(data: views.appendingPathComponent("state"), models: views.appendingPathComponent("models"))
        print("Native lifecycle checks passed")
    }
}
