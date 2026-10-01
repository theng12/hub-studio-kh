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

    init() throws {
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
            body = ok ? #"{"ok":true,"busy":false}"# : #"{"ok":false}"#
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
                    maxAttempts: Int = 1) throws -> StudioController {
    let data = root.appendingPathComponent("state")
    let models = root.appendingPathComponent("models")
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
    try Data(String(server.port).utf8).write(to: data.appendingPathComponent("fixture-port"))
    let service = root.appendingPathComponent("StudioHubService")
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
    var configuration = try ShellConfiguration(arguments: ["lifecycle-tests", "--data-dir", data.path,
        "--models-dir", models.path, "--port", String(try unusedPort())],
        bundle: URL(fileURLWithPath: "/Applications/StudioHubFixture.app"))
    try configuration.setRecoveryPolicy(maxAttempts: maxAttempts, backoffSeconds: [0])
    return StudioController(configuration: configuration, serviceExecutable: service,
                            now: now, healthRequest: healthRequest)
}

@MainActor
func readyPID(_ controller: StudioController) -> Int32? {
    guard let data = readPrivateRegularFile(controller.configuration.readyFile),
          let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let pid = value["pid"] as? NSNumber else { return nil }
    return pid.int32Value
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
            let server = try LocalHubFixture(); defer { server.stop() }
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
            let healthyReady = await waitUntil {
                server.healthReadCount() > 0 && controller.status == "Local services ready"
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
            for (name, busy, relaunching, answer) in [("idle", 0, false, true), ("busy-keep", 2, false, false),
                                                       ("busy-quit", 2, false, true), ("relaunch", 2, true, true)] {
                let controller = try makeController(root: temporary.appendingPathComponent("quit-\(name)"),
                                                    server: server, now: Date.init)
                controller.start()
                let started = await waitUntil { readyPID(controller) != nil && controller.running }
                requireLifecycle(started, "\(name): the service did not start")
                let pid = readyPID(controller)!
                var asked = 0
                let delegate = AppDelegate()
                delegate.controller = controller
                delegate.relaunching = relaunching
                delegate.activeWork = { busy }
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
                requireLifecycle(asked == (busy > 0 && !relaunching ? 1 : 0),
                                 "\(name): asked \(asked) times; only Quit over running work asks, once")
            }
        }

        // An installed update relaunches the app from the release tick - a main-actor task. The app must
        // stop the engine first and quit from its exit: quitting while the engine runs waits in a nested
        // loop inside that task, the engine's exit can never be delivered, and the app hangs for ever
        // (seen live on four controllers). Here the quit is a seam, so the test asserts the order.
        if runs("relaunch-after-update") {
            let server = try LocalHubFixture(); defer { server.stop() }
            let controller = try makeController(root: temporary.appendingPathComponent("relaunch-update"),
                                                server: server, now: Date.init)
            controller.start()
            let started = await waitUntil { readyPID(controller) != nil && controller.running }
            requireLifecycle(started, "relaunch-update: the service did not start")
            let pid = readyPID(controller)!
            let delegate = AppDelegate()
            delegate.controller = controller
            controller.onStopped = { delegate.engineStopped() }
            var helpers = 0, quits = 0, runningAtQuit = true
            delegate.spawnRelaunchHelper = { _, _ in helpers += 1 }
            delegate.terminateApp = { quits += 1; runningAtQuit = controller.running }
            delegate.relaunchAfterUpdate()
            requireLifecycle(helpers == 1, "relaunch-update: the reopen helper must be started once, got \(helpers)")
            requireLifecycle(quits == 0, "relaunch-update: the app must not quit while the engine is still running")
            requireLifecycle(controller.forceStopWithoutAsking, "relaunch-update: a hung stop must be forced, not asked about")
            let quit = await waitUntil { quits == 1 }
            requireLifecycle(quit, "relaunch-update: the app never quit after the engine stopped")
            requireLifecycle(!runningAtQuit && kill(pid, 0) != 0, "relaunch-update: the app quit before the engine had stopped")
            // With no engine running there is nothing to wait for: it quits straight away.
            delegate.relaunchAfterUpdate()
            requireLifecycle(quits == 2 && helpers == 2, "relaunch-update: an idle app must quit at once, got \(quits) quits")
        }


        // The relaunch helper's safety net: whatever keeps the old app alive (any future hang), the helper ends
        // it - SIGTERM, then SIGKILL once the grace is over - and still opens the new app. A normal exit is
        // left alone. The helper must survive its parent, so it is spawned in its own session.
        if runs("relaunch-helper") {
            let folder = temporary.appendingPathComponent("relaunch-helper")
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            func orphan(_ body: String) throws -> Int32 {   // a process that is nobody's child, like the old app
                let shell = Process(), out = Pipe()
                shell.executableURL = URL(fileURLWithPath: "/bin/sh")
                shell.arguments = ["-c", "( \(body) ) >/dev/null 2>&1 </dev/null & echo $!"]
                shell.standardOutput = out
                try shell.run(); shell.waitUntilExit()
                return Int32(String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                    .trimmingCharacters(in: .whitespacesAndNewlines))!
            }
            func alive(_ pid: Int32) -> Bool { kill(pid, 0) == 0 }
            func scenario(_ name: String, stub: String, wait: Int, grace: Int, expectKilled: Bool) async throws {
                let marker = folder.appendingPathComponent("\(name)-opened").path
                let opener = folder.appendingPathComponent("\(name)-open.sh")
                try "#!/bin/sh\necho \"$1\" > \"\(marker)\"\n".write(to: opener, atomically: true, encoding: .utf8)
                chmod(opener.path, 0o755)
                let old = try orphan(stub)
                let began = Date()
                let helper = try RelaunchHelper.spawn(pid: old, app: "/Apps/New.app", wait: wait, grace: grace, opener: opener.path)
                requireLifecycle(getsid(helper) == helper && helper != getsid(0),
                                 "\(name): the helper must run in its own session, or it dies with the app's launchd job")
                let opened = await waitUntil(timeout: 40) { FileManager.default.fileExists(atPath: marker) }
                requireLifecycle(opened, "\(name): the helper never opened the new app")
                requireLifecycle(!alive(old), "\(name): the new app was opened while the old one was still alive")
                let target = try String(contentsOfFile: marker, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                requireLifecycle(target == "/Apps/New.app",
                                 "\(name): the helper opened the wrong app")
                let took = Date().timeIntervalSince(began)
                requireLifecycle(expectKilled ? took >= Double(wait) : took < Double(wait),
                                 "\(name): opened after \(took)s; wait was \(wait)s")
            }
            // Normal path: the old app exits by itself well inside the bound; the helper must not touch it.
            let termMarker = folder.appendingPathComponent("normal-got-term").path
            try await scenario("normal", stub: "trap 'touch \(termMarker)' TERM; sleep 1.5", wait: 30, grace: 5, expectKilled: false)
            requireLifecycle(!FileManager.default.fileExists(atPath: termMarker), "normal: an app that exits by itself was sent SIGTERM")
            // Hung app that honours SIGTERM, and one that ignores it (needs the SIGKILL step).
            try await scenario("hung-term", stub: "while :; do sleep 1; done", wait: 2, grace: 5, expectKilled: true)
            try await scenario("hung-kill", stub: "trap '' TERM; while :; do sleep 1; done", wait: 2, grace: 2, expectKilled: true)
        }

        print("Native lifecycle checks passed")
    }
}
