import Foundation

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
let config = try ShellConfiguration(arguments: ["app", "--data-dir", state.path, "--models-dir", library.path, "--offline"], bundle: bundle)
require(config.dataDirectory.path == state.resolvingSymlinksInPath().path, "state path must survive spaces")
require(config.modelsDirectory?.path == library.resolvingSymlinksInPath().path && config.offline, "explicit library and offline must survive")
do {
    _ = try ShellConfiguration(arguments: ["app", "--data-dir", bundle.appendingPathComponent("state").path], bundle: bundle)
    fatalError("state inside app was accepted")
} catch {}
do {
    _ = try ShellConfiguration(arguments: ["app", "--models-dir", temporary.appendingPathComponent("Missing SSD").path], bundle: bundle)
    fatalError("missing selected SSD was accepted")
} catch {}
let base = URL(string: "http://127.0.0.1:45678")!
let ready = Data("{\"url\":\"http://127.0.0.1:45678\",\"pid\":1234,\"version\":\"0.2.0\"}".utf8)
let decoded = try WorkerReady.decode(ready, expectedPID: 1234)
require(decoded.url == base, "ready URL")
for bad in ["https://example.com", "http://localhost:45678", "http://127.0.0.1:45678@evil.test", "http://127.0.0.1:45678/path"] {
    do { _ = try WorkerReady.decode(Data("{\"url\":\"\(bad)\",\"pid\":1234,\"version\":\"0.2.0\"}".utf8), expectedPID: 1234); fatalError("untrusted ready URL") } catch {}
}
do { _ = try WorkerReady.decode(ready, expectedPID: 999); fatalError("wrong process readiness accepted") } catch {}
require(StudioFunction.allCases.map(\.rawValue) == ["Audio", "Transcription", "Image", "Music", "Video", "Chat"], "owner navigation order")
require(StudioFunction.audio.route == "generate" && StudioFunction.transcription.route == "subtitles", "shared voice routes")
require(StudioFunction.video.route == nil, "unbundled engine cannot advertise availability")
let firstLock = try InstanceLock(directory: state)
do { _ = try InstanceLock(directory: state); fatalError("duplicate app owns same state") } catch {}
withExtendedLifetime(firstLock) {}
for root in ["http://127.0.0.1:45678", "http://127.0.0.1:45678/"] {
    let origin = URL(string: root)!
    require(WorkerOrigin.allows(URL(string: "blob:http://127.0.0.1:45678/file")!, base: origin), "blob downloads must accept normalized origin")
    require(WorkerOrigin.allows(URL(string: "http://127.0.0.1:45678/api/file")!, base: origin), "local endpoint")
    require(!WorkerOrigin.allows(URL(string: "https://example.com/download")!, base: origin), "external redirect cannot display in app")
    require(!WorkerOrigin.allows(URL(string: "http://127.0.0.1:12345")!, base: origin), "unrelated local server cannot load")
    require(!WorkerOrigin.allows(URL(string: "blob:http://127.0.0.1:456789/file")!, base: origin), "prefix collision cannot load")
}
print("Native boundary checks passed")
