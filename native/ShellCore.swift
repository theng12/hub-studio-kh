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
    case models = "Models", voices = "Voices", jobs = "Jobs", connections = "Connections"
    case whatsNew = "What's New", settings = "Settings"
    var id: String { rawValue }
    /// Owner-facing name. The raw value stays stable because it is also the StudioFunction
    /// lookup key and is persisted in saved state.
    var title: String { self == .transcription ? "Subtitle" : rawValue }
    static let workspaces: [Self] = [.audio, .transcription, .image, .music, .video, .chat]
    static let management: [Self] = [.models, .voices, .jobs, .connections, .whatsNew, .settings]
    var function: StudioFunction? { StudioFunction(rawValue: rawValue) }
    var symbol: String {
        if let function { return function.symbol }
        switch self {
        case .overview: return "square.grid.2x2"
        case .models: return "externaldrive"
        case .voices: return "person.wave.2"
        case .jobs: return "list.bullet.rectangle"
        case .connections: return "network"
        case .whatsNew: return "sparkles"
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
    /// The port the Hub binds, the same one on every start.
    ///
    /// It used to be the operating system's choice, which meant a new port each launch. Nothing
    /// could then name this Mac: a controller's address in a node's **Join** box, and a node's
    /// address in the controller's registry, both died at the next restart, and nothing repaired
    /// them. `47873` is the Hub's own default from `peers.DEFAULT_HUB_PORT`, which the fleet code
    /// already assumed when it built a peer's studio URL - pinning it here makes that assumption
    /// true instead of about half true.
    var port: Int
    static let defaultPort = 47873
    /// The controller this Mac joined, so its services can re-join by themselves after a restart.
    /// Not a credential: after joining, this Mac's own fleet token *is* the controller's.
    var controllerAddress: String?
    /// The address this app's own Hub answers on. Always loopback.
    ///
    /// Loopback is bound in every configuration - `bind_hosts` adds it unconditionally - and the
    /// worker's readiness record names loopback for that reason. This used to return the scoped
    /// address when the operator picked a LAN or Tailscale address, and the app then rejected its
    /// own Hub's readiness record ("did not become ready within two minutes") for a Hub that was
    /// running perfectly: picking a specific address could not start the services at all. Serving
    /// other Macs does not change where this Mac's own app talks to it.
    var clientHost: String { ShellConfiguration.clientHost(for: listenHost) }
    static func clientHost(for listenHost: String) -> String { "127.0.0.1" }
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
                                 parentPID: Int32, listenHost: String, offline: Bool,
                                 port: Int = ShellConfiguration.defaultPort) -> [String] {
        var arguments = ["--data-dir", dataDirectory.path,
                         "--models-dir", modelsDirectory.path,
                         "--ready-file", readyFile.path,
                         "--parent-pid", String(parentPID),
                         "--host", listenHost,
                         "--port", String(port)]
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
        port = ShellConfiguration.defaultPort
        controllerAddress = nil
        recoveryMaxAttempts = StartupRecovery.defaultMaxAutomaticAttempts
        recoveryBackoffSeconds = StartupRecovery.defaultDelays
        var explicitModels: URL?
        var explicitOffline = false
        var explicitHost: String?
        var explicitPort: Int?
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
            } else if argument == "--port" {
                guard index + 1 < arguments.count, let value = Int(arguments[index + 1]) else {
                    throw ShellFailure(message: "Missing number for --port.")
                }
                explicitPort = value
                index += 2
            } else { if argument == "--offline" { explicitOffline = true }; index += 1 }
        }
        try Self.requireExternal(dataDirectory, bundle: bundle)
        if let data = readPrivateRegularFile(preferencesFile), let saved = try? JSONDecoder().decode(Preferences.self, from: data) {
            // An empty path means "no library chosen yet", not a folder called "".
            modelsDirectory = saved.modelsPath.isEmpty
                ? nil
                : URL(fileURLWithPath: saved.modelsPath).standardizedFileURL.resolvingSymlinksInPath()
            offline = saved.offline
            startAtLogin = saved.startAtLogin
            startServicesAtLogin = saved.startServicesAtLogin
            listenHost = saved.listenHost
            port = saved.port ?? Self.defaultPort
            controllerAddress = saved.controllerAddress
            if StartupRecovery.validPolicy(maxAttempts: saved.recoveryMaxAttempts,
                                           delays: saved.recoveryBackoffSeconds) {
                recoveryMaxAttempts = saved.recoveryMaxAttempts
                recoveryBackoffSeconds = saved.recoveryBackoffSeconds
            }
        }
        if let explicitModels { modelsDirectory = explicitModels }
        if explicitOffline { offline = true }
        if let explicitHost { listenHost = explicitHost }
        if let explicitPort { port = explicitPort }
        try Self.requireBindableHost(listenHost)
        try Self.requireBindablePort(port)
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

    /// Addresses the operator chose that are not on this Mac right now.
    ///
    /// Binding one fails with `EADDRNOTAVAIL` ("Can't assign requested address"): the
    /// Hub exits, the app retries three times, and the operator only saw "Automatic
    /// recovery stopped after repeated service failures". A saved LAN or Tailscale
    /// address after a network change is the usual cause, so it is named here and
    /// skipped for this start instead of poisoning the whole service set.
    func unavailableListenAddresses(assigned: [String] = LocalAddresses.current().map(\.address)) -> [String] {
        let present = Set(assigned)
        return listenHosts.filter { host in
            host != "127.0.0.1" && host != "0.0.0.0" && !present.contains(host)
        }
    }

    /// The listen list with only addresses this Mac can actually bind. Loopback is
    /// always available and the Hub adds it itself.
    func bindableListenHost(assigned: [String] = LocalAddresses.current().map(\.address)) -> String {
        let unavailable = Set(unavailableListenAddresses(assigned: assigned))
        let kept = listenHosts.filter { !unavailable.contains($0) }
        return kept.isEmpty ? "127.0.0.1" : kept.joined(separator: ",")
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

    /// The port has to be one an unprivileged app may bind and a node may type.
    static func requireBindablePort(_ port: Int) throws {
        guard (1024...65535).contains(port) else {
            throw ShellFailure(message: "The port must be between 1024 and 65535.")
        }
    }

    /// Fail before starting when the port is already taken.
    ///
    /// Without this the Hub exits during startup with a bind error in its log, the app retries it
    /// three times, and the operator sees "Automatic recovery stopped after repeated service
    /// failures" - about a number they never chose. The check is a bind on the wildcard address,
    /// so it also catches a service holding the port on only one interface.
    static func requireFreePort(_ port: Int) throws {
        try requireBindablePort(port)
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { return }  // No socket to test with: let the Hub decide.
        defer { close(descriptor) }
        var reuse: Int32 = 1
        setsockopt(descriptor, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(UInt16(port).bigEndian)
        address.sin_addr.s_addr = INADDR_ANY
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard bound == 0 else {
            throw ShellFailure(message: "Port \(port) is already in use on this Mac. Change the port under Connections, or quit whatever is using it, then start again.")
        }
    }
    /// The folder the setup step fills. A sibling of the home folder, because macOS protects
    /// `~/Downloads`, `~/Documents` and `~/Desktop` and a protected library is worse than useless:
    /// see `protectedFolderWarning`.
    static let libraryFolderName = "StudioHubModels"
    /// The folder the first kit used. Still recognised so a Mac set up with it keeps working, but
    /// never offered first, because a library inside `~/Downloads` can stop the app from starting.
    static let legacyLibraryFolder = "Downloads/model-library"

    /// A model library the one-click setup has already filled in.
    ///
    /// Without this the operator had to find the folder in a file panel, and the button offered on
    /// a fresh Mac ("Use Default Folder") points at an empty folder inside the app's own data
    /// directory - inviting a second, 29 GB download of models that are already on the disk.
    ///
    /// Recognised by its install record, not by its name: any folder holding
    /// `.studiohub/installed.json` is a library the app wrote, so this can never offer a random
    /// folder that merely happens to be called `model-library`.
    static func preparedLibrary(home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> URL? {
        for relative in [libraryFolderName, legacyLibraryFolder] {
            let candidate = home.appendingPathComponent(relative)
                .standardizedFileURL.resolvingSymlinksInPath()
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: candidate.path, isDirectory: &isDirectory),
                  isDirectory.boolValue,
                  FileManager.default.fileExists(
                    atPath: candidate.appendingPathComponent(".studiohub/installed.json").path)
            else { continue }
            return candidate
        }
        return nil
    }

    /// Folders macOS protects from the app and the services it starts.
    static let protectedLibraryFolders = ["Desktop", "Documents", "Downloads", "Movies", "Music", "Pictures"]

    /// Why a library in this folder will not work, or nil when the folder is safe.
    ///
    /// On 2026-09-18 a 34 GB library at `~/Downloads/model-library` made the app hang forever:
    /// every service started, then the Voice worker blocked inside `open()` reading the library,
    /// wrote no readiness record, and the app reported only "the local services did not become
    /// ready" after two minutes. No log said why - the process was alive and asleep in the kernel
    /// - and moving the same library to `~/StudioHubModels` brought the Hub up in two seconds.
    /// macOS privacy protection (TCC) covers these folders, and its refusal is a wait, not an
    /// error, so the operator needs telling before starting rather than after.
    static func protectedFolderWarning(for url: URL,
                                       home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> String? {
        let path = url.standardizedFileURL.resolvingSymlinksInPath().path
        let homePath = home.standardizedFileURL.resolvingSymlinksInPath().path
        guard let name = protectedLibraryFolders.first(where: { folder in
            path == homePath + "/" + folder || path.hasPrefix(homePath + "/" + folder + "/")
        }) else { return nil }
        return "macOS privacy protection covers \(name), and the local services can be blocked from "
            + "reading a library inside it - the app then waits for services that never become ready. "
            + "Move the folder out of \(name) (for example to ~/\(libraryFolderName)) and choose it again."
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
        // Saved even before a model library is chosen. This used to return early when there was
        // none, so turning on "Start services at login" or "Use imported models offline" before
        // picking a folder appeared to work and quietly reverted on the next launch.
        try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let data = try JSONEncoder().encode(Preferences(modelsPath: modelsDirectory?.path ?? "", offline: offline,
                                                        startAtLogin: startAtLogin,
                                                        startServicesAtLogin: startServicesAtLogin,
                                                        listenHost: listenHost,
                                                        port: port,
                                                        controllerAddress: controllerAddress,
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
        /// Absent in a preferences file written before the port was pinned, and absent means the
        /// Hub's own default rather than whatever the operating system hands out next time.
        let port: Int?
        /// Absent for every Mac that has never joined a controller, which is most of them.
        let controllerAddress: String?
        let recoveryMaxAttempts: Int
        let recoveryBackoffSeconds: [TimeInterval]

        init(modelsPath: String, offline: Bool, startAtLogin: Bool, startServicesAtLogin: Bool,
             listenHost: String, port: Int?, controllerAddress: String?, recoveryMaxAttempts: Int,
             recoveryBackoffSeconds: [TimeInterval]) {
            self.modelsPath = modelsPath
            self.offline = offline
            self.startAtLogin = startAtLogin
            self.startServicesAtLogin = startServicesAtLogin
            self.listenHost = listenHost
            self.port = port
            self.controllerAddress = controllerAddress
            self.recoveryMaxAttempts = recoveryMaxAttempts
            self.recoveryBackoffSeconds = recoveryBackoffSeconds
        }

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            // Empty until a library is chosen: preferences are saved as soon as anything else is
            // set, so an empty path is a normal state rather than a broken file.
            modelsPath = try values.decodeIfPresent(String.self, forKey: .modelsPath) ?? ""
            offline = try values.decode(Bool.self, forKey: .offline)
            // Older previews did not persist this preference; the first-release default is on.
            startAtLogin = try values.decodeIfPresent(Bool.self, forKey: .startAtLogin) ?? true
            startServicesAtLogin = try values.decodeIfPresent(Bool.self, forKey: .startServicesAtLogin) ?? false
            // Every build before fleet support was loopback-only, and that stays the default
            // for preferences written by them.
            listenHost = try values.decodeIfPresent(String.self, forKey: .listenHost) ?? "127.0.0.1"
            port = try values.decodeIfPresent(Int.self, forKey: .port)
            controllerAddress = try values.decodeIfPresent(String.self, forKey: .controllerAddress)
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
