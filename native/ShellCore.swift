import Foundation
import Darwin

enum StudioFunction: String, CaseIterable, Identifiable {
    case audio = "Audio", transcription = "Transcription", image = "Image"
    case music = "Music", video = "Video", chat = "Chat"
    var id: String { rawValue }
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


enum StudioPage: String, CaseIterable, Identifiable {
    case overview = "Overview", audio = "Audio", transcription = "Transcription", image = "Image"
    case music = "Music", video = "Video", chat = "Chat"
    case models = "Models", jobs = "Jobs", connections = "Connections", settings = "Settings"
    var id: String { rawValue }
    /// Owner-facing name. The raw value stays stable because it is also the StudioFunction
    /// lookup key and is persisted in saved state.
    var title: String { self == .transcription ? "Subtitle" : rawValue }
    static let workspaces: [Self] = [.audio, .transcription, .image, .music, .video, .chat]
    static let management: [Self] = [.models, .jobs, .connections, .settings]
    var function: StudioFunction? { StudioFunction(rawValue: rawValue) }
    var symbol: String {
        if let function { return function.symbol }
        switch self {
        case .overview: return "square.grid.2x2"
        case .models: return "externaldrive"
        case .jobs: return "list.bullet.rectangle"
        case .connections: return "network"
        case .settings: return "gearshape"
        default: return "circle"
        }
    }
}

struct ShellFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// One address this Mac can be reached at, with the interface that carries it.
struct LocalAddress: Identifiable, Equatable {
    let address: String
    let interface: String
    var id: String { address }
    /// Tailscale hands out addresses from the carrier-grade NAT range. It is the network this
    /// app is meant to be reached over, so it is named rather than left as a bare number.
    var isTailscale: Bool {
        let parts = address.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4, parts[0] == 100 else { return false }
        return (64...127).contains(parts[1])
    }
    var title: String {
        isTailscale ? "Tailscale · \(address)" : "\(address) · \(interface)"
    }
}

/// The addresses this Mac can be reached at, so the operator picks a real one instead of
/// guessing, and Tailscale is named rather than left as a bare number.
enum LocalAddresses {
    static func current() -> [LocalAddress] {
        var found: [LocalAddress] = []
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0, let first = head else { return [] }
        defer { freeifaddrs(head) }
        for pointer in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let flags = Int32(pointer.pointee.ifa_flags)
            guard flags & IFF_UP != 0, flags & IFF_LOOPBACK == 0,
                  let address = pointer.pointee.ifa_addr,
                  address.pointee.sa_family == UInt8(AF_INET) else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            guard getnameinfo(address, socklen_t(address.pointee.sa_len), &host,
                              socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 else { continue }
            let value = String(cString: host)
            guard !value.isEmpty, value != "0.0.0.0",
                  !found.contains(where: { $0.address == value }) else { continue }
            found.append(LocalAddress(address: value,
                                      interface: String(cString: pointer.pointee.ifa_name)))
        }
        // Tailscale first: it is the network this is meant to be used over.
        return found.sorted { left, right in
            left.isTailscale == right.isTailscale ? left.address < right.address : left.isTailscale
        }
    }
}

struct ShellConfiguration {
    var dataDirectory: URL
    var modelsDirectory: URL?
    var offline: Bool
    var startAtLogin: Bool
    /// Whether an at-login launch also starts the Hub and its Studios.
    ///
    /// Off by default, and deliberately a separate choice from starting the app at login: a Mac
    /// that is only ever used in person should not hold its Studios open all night, while a
    /// fleet node that must be ready for work the moment it wakes should.
    var startServicesAtLogin: Bool
    /// The address the Hub binds. Loopback keeps the Hub reachable from this Mac only.
    var listenHost: String
    /// The address the owner app connects to. A wildcard bind also answers on loopback, so
    /// that case still uses it; a scoped bind is only reachable at the address it bound.
    var clientHost: String { ShellConfiguration.clientHost(for: listenHost) }
    static func clientHost(for listenHost: String) -> String {
        // Loopback is always bound, so the app uses it whenever it is available - which is
        // every case except a scoped bind that deliberately excludes it.
        let hosts = listenHost.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
        if hosts.contains("0.0.0.0") || hosts.contains("127.0.0.1") { return "127.0.0.1" }
        return hosts.first ?? "127.0.0.1"
    }
    var recoveryMaxAttempts: Int
    var recoveryBackoffSeconds: [TimeInterval]
    /// The exact command line the bundled service is started with.
    ///
    /// This exists because it was once wrong in a way no test caught: the app passed
    /// `--listen-host` while `packaging/hub_worker.py` defines `--host`, and argparse answers an
    /// unknown argument by exiting 2 - which the app reports only as "the engine exited (code 2)".
    /// Every check ran the worker directly with its real flag, so the mismatch lived in the one
    /// place nothing exercised: the argument list the app builds. The worker's own
    /// `tests/test_fleet_binding.py` pins its side; this pins ours, and the names must stay equal.
    static func serviceArguments(dataDirectory: URL, modelsDirectory: URL, readyFile: URL,
                                 parentPID: Int32, listenHost: String, offline: Bool) -> [String] {
        var arguments = ["--data-dir", dataDirectory.path,
                         "--models-dir", modelsDirectory.path,
                         "--ready-file", readyFile.path,
                         "--parent-pid", String(parentPID),
                         "--host", listenHost]
        if offline { arguments.append("--offline") }
        return arguments
    }
    var readyFile: URL { dataDirectory.appendingPathComponent("hub-ready.json") }
    var logFile: URL { dataDirectory.appendingPathComponent("hub-worker.log") }
    var preferencesFile: URL { dataDirectory.appendingPathComponent("app-preferences.json") }

    init(arguments: [String], bundle: URL) throws {
        dataDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/StudioHub Standalone")
        modelsDirectory = nil
        offline = false
        startAtLogin = true
        startServicesAtLogin = false
        listenHost = "127.0.0.1"
        recoveryMaxAttempts = StartupRecovery.defaultMaxAutomaticAttempts
        recoveryBackoffSeconds = StartupRecovery.defaultDelays
        var explicitModels: URL?
        var explicitOffline = false
        var explicitHost: String?
        var index = 1
        while index < arguments.count {
            let argument = arguments[index]
            if argument == "--data-dir" || argument == "--models-dir" {
                guard index + 1 < arguments.count else { throw ShellFailure(message: "Missing path for \(argument).") }
                let url = URL(fileURLWithPath: NSString(string: arguments[index + 1]).expandingTildeInPath).standardizedFileURL.resolvingSymlinksInPath()
                if argument == "--data-dir" { dataDirectory = url } else { explicitModels = url }
                index += 2
            } else if argument == "--listen-host" {
                guard index + 1 < arguments.count else { throw ShellFailure(message: "Missing address for --listen-host.") }
                explicitHost = arguments[index + 1]
                index += 2
            } else { if argument == "--offline" { explicitOffline = true }; index += 1 }
        }
        try Self.requireExternal(dataDirectory, bundle: bundle)
        if let data = readPrivateRegularFile(preferencesFile), let saved = try? JSONDecoder().decode(Preferences.self, from: data) {
            modelsDirectory = URL(fileURLWithPath: saved.modelsPath).standardizedFileURL.resolvingSymlinksInPath()
            offline = saved.offline
            startAtLogin = saved.startAtLogin
            startServicesAtLogin = saved.startServicesAtLogin
            listenHost = saved.listenHost
            if StartupRecovery.validPolicy(maxAttempts: saved.recoveryMaxAttempts,
                                           delays: saved.recoveryBackoffSeconds) {
                recoveryMaxAttempts = saved.recoveryMaxAttempts
                recoveryBackoffSeconds = saved.recoveryBackoffSeconds
            }
        }
        if let explicitModels { modelsDirectory = explicitModels }
        if explicitOffline { offline = true }
        if let explicitHost { listenHost = explicitHost }
        try Self.requireBindableHost(listenHost)
        if let modelsDirectory {
            try Self.requireExternal(modelsDirectory, bundle: bundle)
            // Persisted missing volumes stay selected and visible for recovery. Explicit CLI input fails early.
            if explicitModels != nil { try Self.requireDirectory(modelsDirectory) }
        }
    }
    /// The addresses the Hub should listen on: loopback (always added by the Hub itself) plus
    /// whichever of this Mac's addresses the operator picked. A comma-separated list is how a
    /// Mac serves its LAN and its tailnet at once.
    var listenHosts: [String] {
        listenHost.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    /// Every entry must be an IPv4 address or the wildcard. Refusing anything else keeps a
    /// typo from silently widening what can reach this Mac.
    static func requireBindableHost(_ host: String) throws {
        let entries = host.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
        guard !entries.isEmpty else {
            throw ShellFailure(message: "Choose at least one address, or this Mac only.")
        }
        for entry in entries {
            if ["127.0.0.1", "0.0.0.0"].contains(entry) { continue }
            let parts = entry.split(separator: ".", omittingEmptySubsequences: false)
            guard parts.count == 4, parts.allSatisfy({ part in
                guard let value = Int(part), String(value) == part || part == "0" else { return false }
                return (0...255).contains(value)
            }) else {
                throw ShellFailure(message: "The listen address must be an IPv4 address such as 127.0.0.1.")
            }
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
        let data = try JSONEncoder().encode(Preferences(modelsPath: modelsDirectory.path, offline: offline,
                                                        startAtLogin: startAtLogin,
                                                        startServicesAtLogin: startServicesAtLogin,
                                                        listenHost: listenHost,
                                                        recoveryMaxAttempts: recoveryMaxAttempts,
                                                        recoveryBackoffSeconds: recoveryBackoffSeconds))
        try data.write(to: preferencesFile, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: preferencesFile.path)
    }
    private struct Preferences: Codable {
        let modelsPath: String
        let offline: Bool
        let startAtLogin: Bool
        /// Absent in every preferences file written before this setting existed, and absent
        /// means off: the default is the conservative one.
        let startServicesAtLogin: Bool
        let listenHost: String
        let recoveryMaxAttempts: Int
        let recoveryBackoffSeconds: [TimeInterval]

        init(modelsPath: String, offline: Bool, startAtLogin: Bool, startServicesAtLogin: Bool,
             listenHost: String, recoveryMaxAttempts: Int,
             recoveryBackoffSeconds: [TimeInterval]) {
            self.modelsPath = modelsPath
            self.offline = offline
            self.startAtLogin = startAtLogin
            self.startServicesAtLogin = startServicesAtLogin
            self.listenHost = listenHost
            self.recoveryMaxAttempts = recoveryMaxAttempts
            self.recoveryBackoffSeconds = recoveryBackoffSeconds
        }

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            modelsPath = try values.decode(String.self, forKey: .modelsPath)
            offline = try values.decode(Bool.self, forKey: .offline)
            // Older previews did not persist this preference; the first-release default is on.
            startAtLogin = try values.decodeIfPresent(Bool.self, forKey: .startAtLogin) ?? true
            startServicesAtLogin = try values.decodeIfPresent(Bool.self, forKey: .startServicesAtLogin) ?? false
            // Every build before fleet support was loopback-only, and that stays the default
            // for preferences written by them.
            listenHost = try values.decodeIfPresent(String.self, forKey: .listenHost) ?? "127.0.0.1"
            recoveryMaxAttempts = try values.decodeIfPresent(Int.self, forKey: .recoveryMaxAttempts)
                ?? StartupRecovery.defaultMaxAutomaticAttempts
            recoveryBackoffSeconds = try values.decodeIfPresent([TimeInterval].self, forKey: .recoveryBackoffSeconds)
                ?? StartupRecovery.defaultDelays
        }
    }

    mutating func setRecoveryPolicy(maxAttempts: Int, backoffSeconds: [TimeInterval]) throws {
        guard StartupRecovery.validPolicy(maxAttempts: maxAttempts, delays: backoffSeconds) else {
            throw ShellFailure(message: "Recovery attempts must be 0–10 with delays from 0 to 300 seconds.")
        }
        recoveryMaxAttempts = maxAttempts
        recoveryBackoffSeconds = backoffSeconds
    }
}

struct WorkerReady: Decodable {
    let url: URL
    let pid: Int32
    let version: String
    let token: String
    static func decode(_ data: Data, expectedPID: Int32, expectedHost: String = "127.0.0.1") throws -> WorkerReady {
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.pid == expectedPID, (24...256).contains(value.token.utf8.count),
              value.token.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }),
              value.url.scheme == "http", value.url.host == expectedHost,
              let port = value.url.port, (1...65535).contains(port), value.url.user == nil,
              value.url.password == nil, value.url.query == nil, value.url.fragment == nil,
              value.url.path.isEmpty || value.url.path == "/" else {
            throw ShellFailure(message: "The local Hub reported invalid startup details. Open its log for details.")
        }
        return value
    }
}

func openPrivateLog(_ url: URL) throws -> FileHandle {
    let descriptor = open(url.path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0o600)
    guard descriptor >= 0 else { throw ShellFailure(message: "Cannot open the local service log safely.") }
    var info = stat()
    guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
          fchmod(descriptor, 0o600) == 0 else {
        close(descriptor)
        throw ShellFailure(message: "The local service log must be a private regular file.")
    }
    return FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
}

func readPrivateRegularFile(_ url: URL) -> Data? {
    let descriptor = open(url.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0)
    guard descriptor >= 0 else { return nil }
    var info = stat()
    guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
          info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else {
        close(descriptor)
        return nil
    }
    let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    return try? handle.readToEnd()
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
