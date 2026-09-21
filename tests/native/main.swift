import Foundation
import Darwin

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}
let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: temporary) }
let bundle = temporary.appendingPathComponent("Moved App.app")
let state = temporary.appendingPathComponent("Node State")
let library = temporary.appendingPathComponent("Model Library")
try FileManager.default.createDirectory(at: library, withIntermediateDirectories: true)
var config = try ShellConfiguration(arguments: ["app", "--data-dir", state.path, "--models-dir", library.path, "--offline"], bundle: bundle)
require(config.dataDirectory.path == state.resolvingSymlinksInPath().path, "state path must survive spaces")
require(config.modelsDirectory?.path == library.resolvingSymlinksInPath().path && config.offline, "explicit library and offline must survive")
require(config.startAtLogin, "login startup defaults on after setup")
require(!config.startServicesAtLogin, "starting services at login defaults off")
require(config.recoveryMaxAttempts == 3 && config.recoveryBackoffSeconds == [1, 3, 10], "recovery defaults")
try config.setRecoveryPolicy(maxAttempts: 4, backoffSeconds: [0.5, 1.5, 4, 8])
try config.save()
let restoredConfig = try ShellConfiguration(arguments: ["app", "--data-dir", state.path], bundle: bundle)
require(restoredConfig.startAtLogin, "login startup preference must survive restart")
require(!restoredConfig.startServicesAtLogin, "an off service start must survive restart")
require(restoredConfig.recoveryMaxAttempts == 4 && restoredConfig.recoveryBackoffSeconds == [0.5, 1.5, 4, 8], "recovery policy must survive restart")
var servicesConfig = restoredConfig
servicesConfig.startServicesAtLogin = true
try servicesConfig.save()
let servicesRestored = try ShellConfiguration(arguments: ["app", "--data-dir", state.path], bundle: bundle)
require(servicesRestored.startServicesAtLogin, "an on service start must survive restart")
// The port and the controller this Mac joined must both survive a restart: one is the address a
// node is told, the other is how this Mac finds its way back into its group after a reboot.
var fleetConfig = servicesRestored
fleetConfig.port = 49001
fleetConfig.controllerAddress = "100.64.0.9:47873"
try fleetConfig.save()
let fleetRestored = try ShellConfiguration(arguments: ["app", "--data-dir", state.path], bundle: bundle)
require(fleetRestored.port == 49001, "a chosen port must survive a restart, got \(fleetRestored.port)")
require(fleetRestored.controllerAddress == "100.64.0.9:47873",
        "the joined controller must survive a restart, got \(fleetRestored.controllerAddress ?? "nil")")
// A command-line port is for a throwaway Hub - the launch verifier uses one - and must not be
// silently replaced by whatever the saved preferences happen to say.
let explicitPortConfig = try ShellConfiguration(
    arguments: ["app", "--data-dir", state.path, "--port", "49123"], bundle: bundle)
require(explicitPortConfig.port == 49123, "--port must override the saved port, got \(explicitPortConfig.port)")
let legacyState = temporary.appendingPathComponent("Legacy State")
try FileManager.default.createDirectory(at: legacyState, withIntermediateDirectories: true)
let legacyPreferences = Data("{\"modelsPath\":\"\(library.path)\",\"offline\":false}".utf8)
try legacyPreferences.write(to: legacyState.appendingPathComponent("app-preferences.json"))
let legacyConfig = try ShellConfiguration(arguments: ["app", "--data-dir", legacyState.path], bundle: bundle)
require(legacyConfig.startAtLogin && legacyConfig.recoveryMaxAttempts == 3, "legacy preferences must use safe defaults")
require(!legacyConfig.startServicesAtLogin, "preferences written before this setting must not start services")
// A preferences file written before the port was pinned must use the Hub's own default rather
// than a number the operating system happened to hand out, and must not claim a controller.
require(legacyConfig.port == ShellConfiguration.defaultPort,
        "legacy preferences must use the pinned default port, got \(legacyConfig.port)")
require(legacyConfig.controllerAddress == nil, "a Mac that has never joined must have no controller")
// The app's service command line must use the flags the bundled worker actually defines.
// It once passed `--listen-host` to a worker that defines `--host`, and argparse answers an
// unknown argument by exiting 2 - which the app shows as "the engine exited (code 2)" with no
// further explanation. Nothing caught it because every test drove the worker directly.
let serviceArgs = ShellConfiguration.serviceArguments(
    dataDirectory: config.dataDirectory, modelsDirectory: config.modelsDirectory!,
    readyFile: config.readyFile, parentPID: 4321, listenHost: "100.64.0.1,192.168.1.5",
    offline: false)
require(serviceArgs.contains("--host"), "the service must be told its listen address with --host")
require(!serviceArgs.contains("--listen-host"),
        "the worker has no --listen-host; passing it makes the worker exit 2")
require(serviceArgs[serviceArgs.firstIndex(of: "--host")! + 1] == "100.64.0.1,192.168.1.5",
        "a multi-address listen value must reach the worker unchanged")
// The port is pinned, not left to the operating system. A new port each start is what made an
// address written on one Mac meaningless on the next, on both sides of a join.
require(serviceArgs[serviceArgs.firstIndex(of: "--port")! + 1] == String(ShellConfiguration.defaultPort),
        "the service must be told the pinned port, got: \(serviceArgs)")
require(ShellConfiguration.defaultPort == 47873,
        "the pinned default must be the Hub's own default port, or peers build URLs to nowhere")
require(!serviceArgs.contains("--offline"), "offline must not be passed when it is off")
let offlineArgs = ShellConfiguration.serviceArguments(
    dataDirectory: config.dataDirectory, modelsDirectory: config.modelsDirectory!,
    readyFile: config.readyFile, parentPID: 4321, listenHost: "127.0.0.1", offline: true)
require(offlineArgs.contains("--offline"), "offline must be passed when it is on")
require(offlineArgs.contains("--parent-pid") && offlineArgs.contains("--data-dir")
        && offlineArgs.contains("--models-dir") && offlineArgs.contains("--ready-file"),
        "the worker requires every mandatory flag on every start")
// No argument may be the empty string: an empty value is accepted by argparse and then fails
// later, somewhere the app cannot explain.
require(!offlineArgs.contains(""), "no service argument may be empty")

// A saved LAN or Tailscale address can vanish when the network changes. Binding it
// fails with EADDRNOTAVAIL, which the app used to report only as "repeated service
// failures" after three retries. It must be named and skipped instead of stalling
// every service on this Mac.
var staleConfig = try ShellConfiguration(
    arguments: ["app", "--data-dir", state.path], bundle: bundle)
staleConfig.listenHost = "192.168.99.99,100.64.0.1,127.0.0.1"
require(staleConfig.unavailableListenAddresses(assigned: ["127.0.0.1", "100.64.0.1"])
        == ["192.168.99.99"], "a missing listen address must be named")
require(staleConfig.bindableListenHost(assigned: ["127.0.0.1", "100.64.0.1"]) == "100.64.0.1,127.0.0.1",
        "only addresses this Mac actually has may reach the worker")
require(staleConfig.unavailableListenAddresses(assigned: ["127.0.0.1"])
        == ["192.168.99.99", "100.64.0.1"], "every missing address must be reported")
require(staleConfig.bindableListenHost(assigned: []) == "127.0.0.1",
        "with no other address available the Hub must still start on loopback")
var wildcardConfig = try ShellConfiguration(
    arguments: ["app", "--data-dir", state.path], bundle: bundle)
wildcardConfig.listenHost = "0.0.0.0"
require(wildcardConfig.unavailableListenAddresses(assigned: []).isEmpty,
        "the wildcard is always bindable and is not a stale address")
require(wildcardConfig.bindableListenHost(assigned: []) == "0.0.0.0",
        "the wildcard must survive the bindable filter")

var noAutomaticRecovery = restoredConfig
try noAutomaticRecovery.setRecoveryPolicy(maxAttempts: 0, backoffSeconds: [1])
require(noAutomaticRecovery.recoveryMaxAttempts == 0, "zero recovery attempts must persist as disabled")
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: true,
                                   ownerRunning: false, userStopped: false,
                                   maxAttempts: 0, delays: [1])
        == .stop("Automatic recovery stopped after repeated service failures"),
        "zero recovery attempts must disable automatic restart")
do {
    var invalid = restoredConfig
    try invalid.setRecoveryPolicy(maxAttempts: 11, backoffSeconds: [1])
    fatalError("invalid recovery policy accepted")
} catch {}
do {
    _ = try ShellConfiguration(arguments: ["app", "--data-dir", bundle.appendingPathComponent("state").path], bundle: bundle)
    fatalError("state inside app was accepted")
} catch {}
do {
    _ = try ShellConfiguration(arguments: ["app", "--models-dir", temporary.appendingPathComponent("Missing SSD").path], bundle: bundle)
    fatalError("missing selected SSD was accepted")
} catch {}
let base = URL(string: "http://127.0.0.1:45678")!
let ready = Data("{\"url\":\"http://127.0.0.1:45678\",\"pid\":1234,\"version\":\"0.2.0\",\"token\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}".utf8)
let decoded = try WorkerReady.decode(ready, expectedPID: 1234)
require(decoded.url == base, "ready URL")
for bad in ["https://example.com", "http://localhost:45678", "http://127.0.0.1:45678@evil.test", "http://127.0.0.1:45678/path"] {
    do { _ = try WorkerReady.decode(Data("{\"url\":\"\(bad)\",\"pid\":1234,\"version\":\"0.2.0\",\"token\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}".utf8), expectedPID: 1234); fatalError("untrusted ready URL") } catch {}
}
do { _ = try WorkerReady.decode(ready, expectedPID: 999); fatalError("wrong process readiness accepted") } catch {}
for token in ["", "short", String(repeating: "a", count: 257), String(repeating: "é", count: 32), String(repeating: "a", count: 31) + "/"] {
    let payload = try JSONSerialization.data(withJSONObject: ["url": base.absoluteString, "pid": 1234, "version": "0.4.0", "token": token])
    do { _ = try WorkerReady.decode(payload, expectedPID: 1234); fatalError("invalid Hub credential accepted") } catch {}
}
// The one-click setup leaves a filled-in library in the home folder. Recognising it is what turns
// "choose a folder in a file panel" into one click, and what keeps a fresh Mac from being offered
// an empty folder that would download 29 GB it already has.
let fakeHome = URL(fileURLWithPath: NSTemporaryDirectory())
    .appendingPathComponent("prepared-\(UUID().uuidString)")
let homeLibrary = "StudioHubModels"
let legacyLibrary = "Downloads/model-library"
func installRecord(_ relative: String) throws {
    try FileManager.default.createDirectory(at: fakeHome.appendingPathComponent("\(relative)/.studiohub"),
                                            withIntermediateDirectories: true)
    try Data("{}".utf8).write(to: fakeHome.appendingPathComponent("\(relative)/.studiohub/installed.json"))
}
try FileManager.default.createDirectory(at: fakeHome.appendingPathComponent(homeLibrary),
                                        withIntermediateDirectories: true)
require(ShellConfiguration.preparedLibrary(home: fakeHome) == nil,
        "a folder without an install record is not a library")
try installRecord(homeLibrary)
let recognised = ShellConfiguration.preparedLibrary(home: fakeHome)
require(recognised?.lastPathComponent == homeLibrary,
        "a library with an install record must be recognised, got \(String(describing: recognised))")
// A Mac the earlier kit filled in is still recognised, but never as the first choice: that folder
// is one macOS protects, and a library inside it stops the services from starting at all.
try installRecord(legacyLibrary)
require(ShellConfiguration.preparedLibrary(home: fakeHome)?.lastPathComponent == homeLibrary,
        "the home-folder library must win over the one inside Downloads")
try FileManager.default.removeItem(at: fakeHome.appendingPathComponent(homeLibrary))
require(ShellConfiguration.preparedLibrary(home: fakeHome)?.lastPathComponent == "model-library",
        "a library left in Downloads by the earlier kit must still be recognised")
// macOS protects these folders, and its refusal looks like a service that never becomes ready.
// The operator must be told before starting rather than after, so the warning is part of the
// configuration rules and not advice in a document.
for protected in ["Downloads/model-library", "Documents/models", "Desktop/models"] {
    require(ShellConfiguration.protectedFolderWarning(
                for: fakeHome.appendingPathComponent(protected), home: fakeHome) != nil,
            "\(protected) must warn: macOS protects it")
}
require(ShellConfiguration.protectedFolderWarning(
            for: fakeHome.appendingPathComponent(homeLibrary), home: fakeHome) == nil,
        "the home-folder library must not warn")
require(ShellConfiguration.protectedFolderWarning(for: URL(fileURLWithPath: "/Volumes/SomeDrive/models")) == nil,
        "an external drive is not one of the protected home folders")
try? FileManager.default.removeItem(at: fakeHome)
// An empty home must not invent one.
require(ShellConfiguration.preparedLibrary(
    home: URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("absent-\(UUID().uuidString)")) == nil,
        "an empty home must offer no prepared library")

require(StudioFunction.allCases.map(\.rawValue) == ["Audio", "Transcription", "Image", "Music", "Video", "Chat"], "owner navigation order")
require(StudioPage.audio != StudioPage.transcription, "independent creation destinations")
// Voices sits beside Models: both are libraries the operator maintains, as opposed to the
// per-workspace pages where work is created.
require(StudioPage.management == [.models, .voices, .jobs, .connections, .whatsNew, .settings],
        "central management navigation includes the voice library and the release notes")
// The notes are reachable without knowing a version, and the page must not pretend to be a
// workspace: it creates nothing.
require(StudioPage.whatsNew.function == nil, "What's New is a page, not a studio function")
require(!StudioPage.workspaces.contains(.whatsNew), "What's New must not appear as a workspace")
let firstLock = try InstanceLock(directory: state)
do { _ = try InstanceLock(directory: state); fatalError("duplicate app owns same state") } catch {}
withExtendedLifetime(firstLock) {}
let logURL = state.appendingPathComponent("worker.log")
try Data("before".utf8).write(to: logURL)
try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: logURL.path)
let log = try openPrivateLog(logURL)
try log.write(contentsOf: Data("after".utf8))
try log.close()
let logText = try String(contentsOf: logURL, encoding: .utf8)
require(logText == "beforeafter", "log must append without truncating")
let permissions = try FileManager.default.attributesOfItem(atPath: logURL.path)[.posixPermissions] as! NSNumber
require(permissions.intValue == 0o600, "existing log must be private")
require(readPrivateRegularFile(logURL) != nil, "regular readiness/log file must be readable")
let linkedLog = state.appendingPathComponent("linked.log")
try FileManager.default.createSymbolicLink(at: linkedLog, withDestinationURL: logURL)
do { _ = try openPrivateLog(linkedLog); fatalError("symlink log accepted") } catch {}
require(readPrivateRegularFile(linkedLog) == nil, "symlink readiness file accepted")
let fifo = state.appendingPathComponent("readiness.fifo")
require(mkfifo(fifo.path, 0o600) == 0, "fixture FIFO creation")
defer { unlink(fifo.path) }
require(readPrivateRegularFile(fifo) == nil, "FIFO readiness file accepted")
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: true, ownerRunning: false, userStopped: false,
                                  maxAttempts: restoredConfig.recoveryMaxAttempts,
                                  delays: restoredConfig.recoveryBackoffSeconds) == .wait(0.5), "configured recovery delay")
require(StartupRecovery.nextAction(attempt: 4, libraryAvailable: true, ownerRunning: false, userStopped: false,
                                  maxAttempts: restoredConfig.recoveryMaxAttempts,
                                  delays: restoredConfig.recoveryBackoffSeconds) == .stop("Automatic recovery stopped after repeated service failures"), "recovery must be bounded")
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: true, ownerRunning: true, userStopped: false) == .wait(0), "running owner must not be duplicated")
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: false, ownerRunning: false, userStopped: false) == .stop("The selected model library is unavailable"), "missing SSD must not create replacement state")
require(StartupRecovery.attemptsAfterHealthyUptime(2, readyAt: Date(timeIntervalSince1970: 100), now: Date(timeIntervalSince1970: 130)) == 2, "brief ready period must retain crash bound")
require(StartupRecovery.attemptsAfterHealthyUptime(2, readyAt: Date(timeIntervalSince1970: 100), now: Date(timeIntervalSince1970: 160)) == 0, "sustained health resets crash bound")
// ── fleet binding ──────────────────────────────────────────────────────────
// Binding beyond loopback is the act that stops the Hub being private, so the address is
// validated rather than trusted, and the app is told to connect to the address that answers.
for good in ["127.0.0.1", "0.0.0.0", "192.168.1.20", "100.101.102.103",
             "127.0.0.1,192.168.1.20", "192.168.1.20,100.101.102.103",
             " 192.168.1.20 , 100.101.102.103 "] {
    try ShellConfiguration.requireBindableHost(good)
}
// An empty entry is ignored, exactly as the Hub ignores it, so a stray comma is not a refusal.
try ShellConfiguration.requireBindableHost("192.168.1.20,")
try ShellConfiguration.requireBindableHost("192.168.1.20,,100.101.102.103")
// But one genuinely bad entry rejects the whole list rather than being quietly skipped.
for badList in ["192.168.1.20,localhost", "192.168.1.20,999.1.1.1", "192.168.1.20,100.1.1",
                "192.168.1.20,http://192.168.1.21", ",", ""] {
    do { try ShellConfiguration.requireBindableHost(badList); fatalError("invalid listen list accepted: \(badList)") } catch {}
}
// Surrounding whitespace is trimmed, because a comma-separated list is written with spaces.
try ShellConfiguration.requireBindableHost(" 127.0.0.1 ")
for bad in ["", "localhost", "http://100.1.1.1", "100.1.1.1:8080", "100.1.1.0/24",
            "::1", "256.1.1.1", "100.1.1", "100.1.1.1.1", "not an address"] {
    do { try ShellConfiguration.requireBindableHost(bad); fatalError("invalid listen address accepted: \(bad)") } catch {}
}
require(ShellConfiguration.clientHost(for: "0.0.0.0") == "127.0.0.1",
        "a wildcard bind still answers on loopback, so the app keeps using loopback")
require(ShellConfiguration.clientHost(for: "127.0.0.1") == "127.0.0.1", "loopback client address")
// Loopback is bound in every configuration, and the worker's readiness record always names it.
// A scoped bind used to make the app expect the bound address and reject the Hub it had just
// started; these cases pin that it always agrees with the worker now.
require(ShellConfiguration.clientHost(for: "100.101.102.103") == "127.0.0.1",
        "a Tailscale-only bind must still be reached on loopback")
// The port a node is told has to be one the app may bind: below 1024 needs root, and 0 means
// "ask the operating system", which is the behaviour that broke addresses across restarts.
for badPort in [0, 1, 80, 1023, 65536, 99999, -1] {
    do { try ShellConfiguration.requireBindablePort(badPort); fatalError("invalid port accepted: \(badPort)") } catch {}
}
for goodPort in [1024, 47873, 65535] { try ShellConfiguration.requireBindablePort(goodPort) }
// A port another program is holding must be refused before anything is started: otherwise the
// Hub exits, the app retries three times, and the operator is told about repeated service
// failures over a number they never chose.
let heldDescriptor = socket(AF_INET, SOCK_STREAM, 0)
require(heldDescriptor >= 0, "the test needs a socket to hold a port")
var heldAddress = sockaddr_in()
heldAddress.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
heldAddress.sin_family = sa_family_t(AF_INET)
heldAddress.sin_port = 0
heldAddress.sin_addr.s_addr = INADDR_ANY
_ = withUnsafePointer(to: &heldAddress) {
    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
        bind(heldDescriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
    }
}
_ = listen(heldDescriptor, 1)
var boundAddress = sockaddr_in()
var boundLength = socklen_t(MemoryLayout<sockaddr_in>.size)
_ = withUnsafeMutablePointer(to: &boundAddress) {
    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(heldDescriptor, $0, &boundLength) }
}
let heldPort = Int(UInt16(bigEndian: boundAddress.sin_port))
require(heldPort > 1023, "the test must hold a real port, got \(heldPort)")
do {
    try ShellConfiguration.requireFreePort(heldPort)
    fatalError("a port another program holds must be refused, not started and retried")
} catch {}
close(heldDescriptor)
// And the same port is accepted once it is free.
try ShellConfiguration.requireFreePort(heldPort)
require(ShellConfiguration.defaultPort == 47873, "the pinned port is the Hub's own default")
require(ShellConfiguration.clientHost(for: "192.168.1.20") == "127.0.0.1",
        "a LAN-only bind must still be reached on loopback")
require(ShellConfiguration.clientHost(for: "127.0.0.1,192.168.1.20") == "127.0.0.1",
        "a multi-address bind still reaches its own Hub on loopback")

// Tailscale hands out addresses from the carrier-grade NAT range and is the network this is
// meant to be reached over, so it is named rather than left as a bare number.
require(LocalAddress(address: "100.101.102.103", interface: "utun4").isTailscale,
        "a Tailscale address must be recognised")
require(!LocalAddress(address: "100.63.0.1", interface: "en0").isTailscale,
        "the range below the Tailscale block is not Tailscale")
require(!LocalAddress(address: "100.128.0.1", interface: "en0").isTailscale,
        "the range above the Tailscale block is not Tailscale")
require(!LocalAddress(address: "192.168.1.20", interface: "en0").isTailscale,
        "a LAN address is not Tailscale")
require(LocalAddress(address: "100.101.102.103", interface: "utun4").title.contains("Tailscale"),
        "a Tailscale address must be labelled")

// The Hub may only report itself at the address it was asked to bind.
let boundReady = Data("{\"url\":\"http://100.101.102.103:45678\",\"pid\":1234,\"version\":\"0.4.0\",\"token\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}".utf8)
_ = try WorkerReady.decode(boundReady, expectedPID: 1234, expectedHost: "100.101.102.103")
do { _ = try WorkerReady.decode(boundReady, expectedPID: 1234); fatalError("a Hub bound elsewhere was accepted as loopback") } catch {}
let loopbackReady = Data("{\"url\":\"http://127.0.0.1:45678\",\"pid\":1234,\"version\":\"0.4.0\",\"token\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}".utf8)
do { _ = try WorkerReady.decode(loopbackReady, expectedPID: 1234, expectedHost: "100.101.102.103"); fatalError("a Hub bound elsewhere reported loopback and was accepted") } catch {}

// A preference file written before fleet support must stay loopback-only, and the address is
// persisted so the choice survives a restart.
let legacyRoot = FileManager.default.temporaryDirectory
    .appendingPathComponent("studiohub-legacy-\(UUID().uuidString)")
try FileManager.default.createDirectory(at: legacyRoot, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: legacyRoot) }
let preferencesURL = legacyRoot.appendingPathComponent("app-preferences.json")
try Data("{\"modelsPath\":\"\(legacyRoot.path)/models\",\"offline\":false,\"startAtLogin\":true}".utf8)
    .write(to: preferencesURL, options: .atomic)
try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: preferencesURL.path)
let legacy = try ShellConfiguration(arguments: ["StudioHub", "--data-dir", legacyRoot.path],
                                    bundle: URL(fileURLWithPath: "/Applications/StudioHub.app"))
require(legacy.listenHost == "127.0.0.1", "an older preference file must default to loopback")
require(legacy.clientHost == "127.0.0.1", "a default configuration must reach its own Hub on loopback")

print("Native boundary checks passed")
