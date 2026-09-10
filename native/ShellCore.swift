import Foundation
import Darwin

enum StudioFunction: String, CaseIterable, Identifiable {
    case audio = "Audio", transcription = "Transcription", image = "Image"
    case music = "Music", video = "Video", chat = "Chat"
    var id: String { rawValue }
    var route: String? {
        switch self { case .audio: return "generate"; case .transcription: return "subtitles"; default: return nil }
    }
    var symbol: String {
        switch self {
        case .audio: return "waveform"
        case .transcription: return "text.bubble"
        case .image: return "photo"
        case .music: return "music.note"
        case .video: return "film"
        case .chat: return "bubble.left.and.bubble.right"
        }
    }
}

struct ShellFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct ShellConfiguration {
    var dataDirectory: URL
    var modelsDirectory: URL?
    var offline: Bool
    var readyFile: URL { dataDirectory.appendingPathComponent("voice-ready.json") }
    var logFile: URL { dataDirectory.appendingPathComponent("voice-worker.log") }
    var preferencesFile: URL { dataDirectory.appendingPathComponent("app-preferences.json") }

    init(arguments: [String], bundle: URL) throws {
        dataDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/StudioHub Standalone")
        modelsDirectory = nil
        offline = false
        var explicitModels: URL?
        var explicitOffline = false
        var index = 1
        while index < arguments.count {
            let argument = arguments[index]
            if argument == "--data-dir" || argument == "--models-dir" {
                guard index + 1 < arguments.count else { throw ShellFailure(message: "Missing path for \(argument).") }
                let url = URL(fileURLWithPath: NSString(string: arguments[index + 1]).expandingTildeInPath).standardizedFileURL.resolvingSymlinksInPath()
                if argument == "--data-dir" { dataDirectory = url } else { explicitModels = url }
                index += 2
            } else { if argument == "--offline" { explicitOffline = true }; index += 1 }
        }
        try Self.requireExternal(dataDirectory, bundle: bundle)
        if let data = try? Data(contentsOf: preferencesFile), let saved = try? JSONDecoder().decode(Preferences.self, from: data) {
            modelsDirectory = URL(fileURLWithPath: saved.modelsPath).standardizedFileURL.resolvingSymlinksInPath()
            offline = saved.offline
        }
        if let explicitModels { modelsDirectory = explicitModels }
        if explicitOffline { offline = true }
        if let modelsDirectory {
            try Self.requireExternal(modelsDirectory, bundle: bundle)
            // Persisted missing volumes stay selected and visible for recovery. Explicit CLI input fails early.
            if explicitModels != nil { try Self.requireDirectory(modelsDirectory) }
        }
    }
    static func requireExternal(_ url: URL, bundle: URL) throws {
        let path = url.standardizedFileURL.resolvingSymlinksInPath().path
        let root = bundle.standardizedFileURL.resolvingSymlinksInPath().path
        guard path != root && !path.hasPrefix(root + "/") else {
            throw ShellFailure(message: "Choose a data or model folder outside the application.")
        }
    }
    static func requireDirectory(_ url: URL) throws {
        var directory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &directory), directory.boolValue else {
            throw ShellFailure(message: "The selected model library is unavailable. Connect its drive or choose another folder.")
        }
    }
    func save() throws {
        guard let modelsDirectory else { return }
        try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let data = try JSONEncoder().encode(Preferences(modelsPath: modelsDirectory.path, offline: offline))
        try data.write(to: preferencesFile, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: preferencesFile.path)
    }
    private struct Preferences: Codable { let modelsPath: String; let offline: Bool }
}

struct WorkerReady: Decodable {
    let url: URL
    let pid: Int32
    let version: String
    static func decode(_ data: Data, expectedPID: Int32) throws -> WorkerReady {
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.pid == expectedPID, value.url.scheme == "http", value.url.host == "127.0.0.1",
              let port = value.url.port, (1...65535).contains(port), value.url.user == nil,
              value.url.password == nil, value.url.query == nil, value.url.fragment == nil,
              value.url.path.isEmpty || value.url.path == "/" else {
            throw ShellFailure(message: "The audio engine reported an invalid startup address. Open its log for details.")
        }
        return value
    }
}

final class InstanceLock {
    private let descriptor: Int32
    init(directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        descriptor = open(directory.appendingPathComponent("app.lock").path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw ShellFailure(message: "Cannot open the app's data folder lock.") }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            close(descriptor)
            throw ShellFailure(message: "Studio Hub is already using this data folder. Open the running app from its menu-bar icon.")
        }
    }
    deinit { close(descriptor) }
}

enum WorkerOrigin {
    static func allows(_ url: URL, base: URL) -> Bool {
        guard base.scheme == "http", base.host == "127.0.0.1", let port = base.port else { return false }
        if url.scheme == "blob" { return url.absoluteString.hasPrefix("blob:http://127.0.0.1:\(port)/") }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == port && url.user == nil && url.password == nil
    }
}
