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
require(config.recoveryMaxAttempts == 3 && config.recoveryBackoffSeconds == [1, 3, 10], "recovery defaults")
try config.setRecoveryPolicy(maxAttempts: 4, backoffSeconds: [0.5, 1.5, 4, 8])
try config.save()
let restoredConfig = try ShellConfiguration(arguments: ["app", "--data-dir", state.path], bundle: bundle)
require(restoredConfig.startAtLogin, "login startup preference must survive restart")
require(restoredConfig.recoveryMaxAttempts == 4 && restoredConfig.recoveryBackoffSeconds == [0.5, 1.5, 4, 8], "recovery policy must survive restart")
let legacyState = temporary.appendingPathComponent("Legacy State")
try FileManager.default.createDirectory(at: legacyState, withIntermediateDirectories: true)
let legacyPreferences = Data("{\"modelsPath\":\"\(library.path)\",\"offline\":false}".utf8)
try legacyPreferences.write(to: legacyState.appendingPathComponent("app-preferences.json"))
let legacyConfig = try ShellConfiguration(arguments: ["app", "--data-dir", legacyState.path], bundle: bundle)
require(legacyConfig.startAtLogin && legacyConfig.recoveryMaxAttempts == 3, "legacy preferences must use safe defaults")
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
require(StudioFunction.allCases.map(\.rawValue) == ["Audio", "Transcription", "Image", "Music", "Video", "Chat"], "owner navigation order")
require(StudioPage.audio != StudioPage.transcription, "independent creation destinations")
require(StudioPage.management == [.models, .jobs, .connections, .settings], "central management navigation")
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
print("Native boundary checks passed")
