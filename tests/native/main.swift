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
require(config.recoveryMaxAttempts == 5 && config.recoveryBackoffSeconds == [1, 3, 10, 60, 120], "recovery defaults")
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
require(legacyConfig.startAtLogin && legacyConfig.recoveryMaxAttempts == 5, "legacy preferences must use safe defaults")
require(!legacyConfig.startServicesAtLogin, "preferences written before this setting must not start services")
// The fleet default for "Start services on startup" (owner, 2026-10-02): a Mac that belongs to a fleet
// gets it once, a Mac that does not is left alone, and an owner's later choice is never undone.
require(!legacyConfig.startServicesChoiceRecorded, "an old preferences file has not decided the setting")
var standaloneDefault = legacyConfig
require(!standaloneDefault.isJoinedNode, "no controller address means not joined")
require(!standaloneDefault.applyFleetServicesDefault(inFleet: standaloneDefault.isJoinedNode)
        && !standaloneDefault.startServicesAtLogin && !standaloneDefault.startServicesChoiceRecorded,
        "a Mac that is not in a fleet keeps services off and records nothing")
var joinedDefault = legacyConfig
joinedDefault.controllerAddress = "100.64.0.9:47873"
require(joinedDefault.isJoinedNode, "a controller address means joined")
require(joinedDefault.applyFleetServicesDefault(inFleet: joinedDefault.isJoinedNode)
        && joinedDefault.startServicesAtLogin && joinedDefault.startServicesChoiceRecorded,
        "a joined Mac gets services on at startup")
try joinedDefault.save()
let joinedRestored = try ShellConfiguration(arguments: ["app", "--data-dir", legacyState.path], bundle: bundle)
require(joinedRestored.startServicesAtLogin && joinedRestored.startServicesChoiceRecorded,
        "the default and the record that it was applied survive a restart")
var ownerOff = joinedRestored
ownerOff.chooseStartServices(false)
require(!ownerOff.applyFleetServicesDefault(inFleet: true) && !ownerOff.startServicesAtLogin,
        "an owner's deliberate off is never overridden by the default")
var emptyAddress = legacyConfig
emptyAddress.controllerAddress = "   "
require(!emptyAddress.isJoinedNode, "a blank address is not a membership")
var roleReported = legacyConfig
require(roleReported.applyFleetServicesDefault(inFleet: true) && roleReported.startServicesAtLogin,
        "a Hub that reports itself a controller or node turns it on too")
require(!roleReported.applyFleetServicesDefault(inFleet: true), "and only once")
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

// **A headless Mac that boots before its network comes up is the case this exists for.**
//
// The read above is a single instant, and at boot every chosen address is usually *not up yet*
// rather than gone. The owner's power cut put his controller in exactly that state: the app is a
// login item, so it started while Ethernet was negotiating and Tailscale had not come up, bound
// itself to loopback, and then answered its own health check and wrote its ready file while **no
// node could reach it at all**. He fixed it by stopping and starting the services by hand — which
// was simply the same read taken a minute later.
//
// So the missing set is remembered and re-checked. Asserted in both directions, because the defect
// was not "the address is missing" — that was answered correctly — it was treating a temporary
// absence as permanent.
require(ShellConfiguration.arrivedAddresses(
            missing: ["192.168.99.99"], assigned: ["127.0.0.1"]) == [],
        "an address that is still missing has not arrived")
require(ShellConfiguration.arrivedAddresses(
            missing: ["192.168.99.99"], assigned: ["127.0.0.1", "192.168.99.99"]) == ["192.168.99.99"],
        "an address that has appeared since the start must be noticed, or the Hub stays on "
            + "loopback until somebody restarts it by hand")
require(ShellConfiguration.arrivedAddresses(
            missing: ["192.168.99.99", "100.64.0.1"], assigned: ["100.64.0.1"]) == ["100.64.0.1"],
        "one address arriving is enough to restart, and the other must not be reported as arrived")
require(ShellConfiguration.arrivedAddresses(
            missing: ["192.168.99.99"], assigned: ["192.168.99.99", "192.168.99.99"]) == ["192.168.99.99"],
        "a duplicated interface entry must not make one address look like two")
require(ShellConfiguration.arrivedAddresses(missing: [], assigned: []).isEmpty,
        "a Hub that started with every address it wanted has nothing to wait for")

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
// A removable volume is the owner's normal kit drive, and macOS still asks for permission to read
// one: an unanswered prompt blocks the same way, so the warning must name Removable Volumes.
require(ShellConfiguration.protectedFolderWarning(
            for: URL(fileURLWithPath: "/Volumes/SomeDrive/models"))?.contains("Removable Volumes") == true,
        "an external drive must warn about the Removable Volumes permission")
require(ShellConfiguration.protectedFolderWarning(for: URL(fileURLWithPath: "/Volumes"))?.contains("Removable Volumes") == true,
        "the /Volumes root is covered by the same permission")
require(ShellConfiguration.protectedFolderWarning(
            for: URL(fileURLWithPath: "/Users/someone/StudioHubModels")) == nil,
        "an ordinary folder outside a protected one and outside /Volumes must not warn")
try? FileManager.default.removeItem(at: fakeHome)
// An empty home must not invent one.
require(ShellConfiguration.preparedLibrary(
    home: URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("absent-\(UUID().uuidString)")) == nil,
        "an empty home must offer no prepared library")

// The raw values are the persisted contract: a saved selection has to resolve after an update, so
// the set of names may not change silently. It is asserted as a set, not as an order, because the
// order is not saved and is not a contract.
require(Set(StudioFunction.allCases.map(\.rawValue)) == ["Audio", "Transcription", "Image", "Music", "Video", "Chat"],
        "the persisted Studio names: a new case is a deliberate schema change")
require(StudioFunction.allCases.count == 6,
        "a new Studio must land with its page, its symbol and this test updated")
require(StudioPage.audio != StudioPage.transcription, "independent creation destinations")
// The sidebar is grouped by what a page is for: work is created, libraries are maintained, the
// fleet is connected, and app pages are settings. "Coming later" is declared but empty now.
require(StudioPage.sidebarSections.map(\.title) == ["Create", "Library", "Fleet", "App", "Coming later"],
        "the sidebar sections are named and ordered as the owner asked")
// Membership is derived from the Studio list, so a Studio added to the enum cannot be absent from
// the sidebar. This is the check that a hand-written list used to hide: nothing failed when a
// modality was missing from all four of them.
require(Set(StudioPage.create.compactMap { $0.function }) == Set(StudioFunction.allCases.filter { $0.hasEngine }),
        "Create must hold every Studio with an engine: \(StudioPage.create)")
require(Set(StudioPage.comingLater.compactMap { $0.function }) == Set(StudioFunction.allCases.filter { !$0.hasEngine }),
        "Coming later must hold exactly the Studios with no engine: \(StudioPage.comingLater)")
require(StudioFunction.allCases.allSatisfy { StudioPage.page(for: $0) != nil },
        "every Studio must have its own page: adding a case without one is a wiring hole")
require(StudioFunction.allCases.filter { $0.hasEngine }.count == StudioPage.create.count,
        "every Studio with an engine must appear in Create exactly once")
// The order inside Create is the owner's own (2026-09-15). A Studio this order does not name still
// lands in a section — this asserts the display order, not membership.
require(StudioPage.create == [.image, .audio, .transcription, .music, .chat, .video],
        "Create's order is the owner's; Video joined it when the lane shipped")
// Renders joined Library on 2026-10-26 with the owner's own request ("there's no render tab"): it is a
// list of finished work beside Jobs, not a fleet setting, and it is the same page on a node and on
// a controller. The order is a display decision, which is why it is written here and pinned.
// Setup leads Library from 2026-10-28: the install-time model picker is the first thing an operator
// does with a new Mac, and it belongs beside Models rather than inside it (Models lists what is
// installed; Setup chooses what to install). It is the same page on a new Mac and an old one - on
// a Mac that is already set up it opens showing every preset's count and all installed models, so
// nothing about the "first launch only" question is baked into a second entry point.
require(StudioPage.library == [.setup, .models, .voices, .jobs, .renders],
        "Library holds the things the operator maintains, starting with Setup")
require(StudioPage.fleet == [.connections], "Fleet holds the connection surface")
require(StudioPage.app == [.whatsNew, .settings], "App holds What's New and Settings")
require(StudioPage.comingLater.isEmpty,
        "every Studio has an engine now, so nothing is 'coming later'")
// The notes are reachable from the App section, and the page must not pretend to be a workspace:
// it creates nothing.
require(StudioPage.whatsNew.function == nil, "What's New is a page, not a studio function")
require(!StudioPage.create.contains(.whatsNew), "What's New must not appear as a workspace")
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
                                  delays: restoredConfig.recoveryBackoffSeconds) == .wait(300), "after the configured ladder recovery must keep trying slowly")
require(StartupRecovery.nextAction(attempt: 9, libraryAvailable: true, ownerRunning: false, userStopped: false,
                                  maxAttempts: restoredConfig.recoveryMaxAttempts,
                                  delays: restoredConfig.recoveryBackoffSeconds) == .wait(300), "the slow retry must stay at five minutes")
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: true, ownerRunning: true, userStopped: false) == .wait(0), "running owner must not be duplicated")
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: false, ownerRunning: false, userStopped: false) == .stop("The selected model library is unavailable"), "missing SSD must not create replacement state")
require(StartupRecovery.attemptsAfterHealthyUptime(2, readyAt: Date(timeIntervalSince1970: 100), now: Date(timeIntervalSince1970: 130)) == 2, "brief ready period must retain crash bound")
require(StartupRecovery.attemptsAfterHealthyUptime(2, readyAt: Date(timeIntervalSince1970: 100), now: Date(timeIntervalSince1970: 160)) == 0, "sustained health resets crash bound")

// ── what a launch decides about the services ────────────────────────────────
// The setting is read once, at launch, and it governs the login launch only: opening the app is
// the intent to use it. The decision is pure so the fleet's own words ("the studios were down
// after an update") can be driven from here rather than from a running app.
require(LaunchStart.decide(loginLaunch: false, startServicesOnLaunch: false,
                           hasLibrary: true, canConfigure: true) == .start,
        "opening the app starts the services whatever the setting says")
require(LaunchStart.decide(loginLaunch: false, startServicesOnLaunch: false,
                           hasLibrary: true, canConfigure: true).shouldStart,
        "the setting-off case must still start when the app is opened by hand")
require(LaunchStart.decide(loginLaunch: true, startServicesOnLaunch: false,
                           hasLibrary: true, canConfigure: true)
        == .stayStopped(LaunchStart.stoppedByChoice),
        "a login launch with the setting off stays stopped, and says so")
require(!LaunchStart.decide(loginLaunch: true, startServicesOnLaunch: false,
                            hasLibrary: true, canConfigure: true).shouldStart,
        "the setting-off answer is never mistaken for a start")
require(LaunchStart.decide(loginLaunch: true, startServicesOnLaunch: true,
                           hasLibrary: true, canConfigure: true) == .start,
        "a login launch with the setting on really starts the services")
require(LaunchStart.decide(loginLaunch: true, startServicesOnLaunch: true,
                           hasLibrary: true, canConfigure: true).shouldStart,
        "the setting-on answer is a start")
require(LaunchStart.decide(loginLaunch: false, startServicesOnLaunch: true,
                           hasLibrary: true, canConfigure: true) == .start,
        "an update's reopen starts the services even though it is not a login launch")
require(LaunchStart.decide(loginLaunch: true, startServicesOnLaunch: true,
                           hasLibrary: false, canConfigure: true) == .nothingToStart,
        "no model library chosen is nothing to start, not a failure")
require(LaunchStart.decide(loginLaunch: false, startServicesOnLaunch: true,
                           hasLibrary: false, canConfigure: true) == .nothingToStart,
        "the same without a login launch")
require(LaunchStart.decide(loginLaunch: false, startServicesOnLaunch: true,
                           hasLibrary: true, canConfigure: false) == .stayStopped("another copy of Studio Hub is already running"),
        "a second copy of the app must never start a second set of services")
require(LaunchStart.stoppedByChoice == "Start services on startup is off",
        "the reason a deliberate choice gives is a sentence a person can read")
// The bound itself: a start that fails is retried on the ladder, five times, then slowly.
require(StartupRecovery.defaultMaxAutomaticAttempts == 5
        && StartupRecovery.defaultDelays == [1, 3, 10, 60, 120],
        "the launch retry ladder must stay bounded at five fast attempts")
for attempt in 0..<5 {
    guard case .wait(let delay) = StartupRecovery.nextAction(attempt: attempt, libraryAvailable: true,
                                                             ownerRunning: false, userStopped: false) else {
        fatalError("the launch retry at attempt \(attempt) must wait, not stop")
    }
    require(delay == StartupRecovery.defaultDelays[attempt],
            "the launch retry at attempt \(attempt) must wait \(StartupRecovery.defaultDelays[attempt])s, got \(delay)")
}
require(StartupRecovery.nextAction(attempt: 5, libraryAvailable: true, ownerRunning: false, userStopped: false)
        == .wait(300),
        "past the bound the launch retry slows to five minutes instead of stopping")
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: false, ownerRunning: false, userStopped: false)
        == .stop("The selected model library is unavailable"),
        "giving up must record the reason, never a silent stop")
// A deliberate stop is the one thing that still stops.
require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: true, ownerRunning: false, userStopped: true)
        == .stop("Local services stopped"),
        "an owner's stop must not be retried by the ladder")
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
// This used to require `127.0.0.1` — "an older preference file must default to loopback" — and
// that default is what the owner asked to change: *"i keep forgetting to turn on tailscale. i
// want it already the default on."* A Mac whose addresses were frozen on the day it was set up
// never reaches its tailnet, and the operator's only clue is a Connections screen he has to
// remember to open.
//
// An absent flag now means automatic, and automatic is additive over the saved list. The
// expectation is written as the rule rather than as a literal, because what this Mac's addresses
// are depends on the Mac running the test.
require(legacy.listenHostAutomatic, "an absent preference must mean automatic, not pinned")
require(legacy.listenHost == ShellConfiguration.automaticListenHost(keeping: "127.0.0.1"),
        "an older preference file must follow this Mac's networks, got \(legacy.listenHost)")
require(legacy.clientHost == "127.0.0.1", "a default configuration must reach its own Hub on loopback")

// Automatic is ADDITIVE: an address already saved survives, and this Mac's current addresses are
// added to it. Nothing may be removed, so an upgrade can never take away a way in that worked.
let additive = ShellConfiguration.automaticListenHost(keeping: "10.44.0.9")
require(additive.split(separator: ",").map(String.init).contains("10.44.0.9"),
        "an address already configured must survive, got \(additive)")
require(additive == ShellConfiguration.automaticListenHost(keeping: "10.44.0.9"),
        "automatic must be a function of the saved list and this Mac, not of call order")

// An explicit choice stays an explicit choice. `--listen-host` pins the exact list and says so,
// and — the part that was missing — it is written down, so it lasts past this launch.
let pinnedRoot = FileManager.default.temporaryDirectory
    .appendingPathComponent("studiohub-pinned-\(UUID().uuidString)")
try FileManager.default.createDirectory(at: pinnedRoot, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: pinnedRoot) }
let pinned = try ShellConfiguration(
    arguments: ["StudioHub", "--data-dir", pinnedRoot.path, "--listen-host", "127.0.0.1"],
    bundle: URL(fileURLWithPath: "/Applications/StudioHub.app"))
require(!pinned.listenHostAutomatic, "--listen-host names an exact list, so it is not automatic")
require(pinned.listenHost == "127.0.0.1", "the pinned list is what was asked for")
let reopened = try ShellConfiguration(arguments: ["StudioHub", "--data-dir", pinnedRoot.path],
                                      bundle: URL(fileURLWithPath: "/Applications/StudioHub.app"))
require(reopened.listenHost == "127.0.0.1" && !reopened.listenHostAutomatic,
        "a listen host given on the command line must survive the next launch, got "
        + "\(reopened.listenHost) automatic=\(reopened.listenHostAutomatic)")

// ...and `--listen-auto` is the kit's supported way to turn it back on, persisted the same way.
let automaticRoot = FileManager.default.temporaryDirectory
    .appendingPathComponent("studiohub-auto-\(UUID().uuidString)")
try FileManager.default.createDirectory(at: automaticRoot, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: automaticRoot) }
try Data("{\"listenHost\":\"127.0.0.1\",\"offline\":false}".utf8).write(
    to: automaticRoot.appendingPathComponent("app-preferences.json"), options: .atomic)
try FileManager.default.setAttributes(
    [.posixPermissions: 0o600],
    ofItemAtPath: automaticRoot.appendingPathComponent("app-preferences.json").path)
_ = try ShellConfiguration(
    arguments: ["StudioHub", "--data-dir", automaticRoot.path, "--listen-auto"],
    bundle: URL(fileURLWithPath: "/Applications/StudioHub.app"))
let autoReopened = try ShellConfiguration(arguments: ["StudioHub", "--data-dir", automaticRoot.path],
                                          bundle: URL(fileURLWithPath: "/Applications/StudioHub.app"))
require(autoReopened.listenHostAutomatic, "--listen-auto must survive the next launch")
require(autoReopened.listenHost == ShellConfiguration.automaticListenHost(keeping: "127.0.0.1"),
        "--listen-auto must recompute this Mac's addresses, got \(autoReopened.listenHost)")

// The service log must not grow without bound. Rotation moves an oversized log to
// `.1` and leaves a small one alone. A sparse file keeps the test cheap.
let rotationLog = temporary.appendingPathComponent("hub-worker.log")
let rotationFD = open(rotationLog.path, O_WRONLY | O_CREAT, 0o600)
require(rotationFD >= 0, "the rotation fixture must open")
require(ftruncate(rotationFD, privateLogRotationBytes + 1) == 0, "the fixture must be oversized")
close(rotationFD)
rotatePrivateLogIfNeeded(rotationLog)
let rotatedLog = temporary.appendingPathComponent("hub-worker.log.1")
require(FileManager.default.fileExists(atPath: rotatedLog.path),
        "an oversized service log must rotate to .1")
require(!FileManager.default.fileExists(atPath: rotationLog.path),
        "rotation must move the log, not copy it")
try Data("small".utf8).write(to: rotationLog)
rotatePrivateLogIfNeeded(rotationLog)
require(FileManager.default.fileExists(atPath: rotationLog.path),
        "a small service log must not rotate")

// A rotated log is retired, never deleted: the next rotation moves the previous `.1` into the Hub's
// Trash Folder (`<data>/hub/trash/<date>/service-log-*`), where the owner can still see it.
let secondRotationFD = open(rotationLog.path, O_WRONLY | O_TRUNC, 0o600)
require(secondRotationFD >= 0, "the second rotation fixture must open")
require(ftruncate(secondRotationFD, privateLogRotationBytes + 1) == 0, "the fixture must be oversized again")
close(secondRotationFD)
rotatePrivateLogIfNeeded(rotationLog)
var retiredNames: [String] = []
if let walker = FileManager.default.enumerator(at: temporary.appendingPathComponent("hub/trash"),
                                               includingPropertiesForKeys: nil) {
    for case let url as URL in walker { retiredNames.append(url.lastPathComponent) }
}
require(retiredNames.contains("hub-worker.log.1"),
        "the previous rotated log must be retired into the Hub's Trash Folder, not deleted")
require(FileManager.default.fileExists(atPath: rotatedLog.path), "the new rotated log must be in place")

print("Native boundary checks passed")
