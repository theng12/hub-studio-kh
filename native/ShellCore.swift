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
    /// What this Build ships: whether a Studio here has an engine and a page behind it.
    ///
    /// The switch is exhaustive on purpose. Adding a Studio to the enum stops this build until
    /// someone answers the question for the new case, instead of the new Studio quietly landing
    /// in no sidebar section, no history tab and no status line — the shape that left four
    /// independent per-modality lists to drift apart.
    var hasEngine: Bool {
        switch self {
        case .audio, .transcription, .image, .music, .chat, .video: return true
        }
    }
    /// Whether the Hub files finished batches for this Studio, which is what the Jobs page and
    /// its history tabs list. A Studio answers this before its jobs can be missed.
    ///
    /// Chat joined this list when the Hub began writing each Chat-page completion down as a
    /// finished one-item batch: a Chat answer is work the operator may want to find again, so it
    /// is listed like every other Studio's. **Video joined it on 2026-09-26** with a real local
    /// lane: `broker.MODALITY` files a video batch (it did all along — the worker was the missing
    /// half), so a finished clip is work the operator may want to find again too.
    var hasBatchHistory: Bool {
        switch self {
        case .audio, .transcription, .image, .music, .chat, .video: return true
        }
    }

    /// Whether this Studio gets a tab on the Jobs page.
    ///
    /// The owner asked for Video to be visible before it shipped - "add one for video as a
    /// placeholder now so we don't forget about it" - and it kept the tab when the engine
    /// arrived, because a Studio's Jobs tab is not a claim about which build you are running.
    /// Every Studio answers with its batch history now, so a new Studio still arrives with both.
    var hasHistoryTab: Bool {
        switch self {
        case .audio, .transcription, .image, .music, .chat, .video: return hasBatchHistory
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
    /// Sidebar sections, in the order the owner asked for. "Create" is where work is made; Video
    /// joined it on 2026-09-26 with a real local lane, so "Coming later" is empty until the next
    /// engine-less Studio arrives (an empty section is not drawn). The raw values stay stable, so
    /// moving a page between sections never invalidates saved state.
    ///
    /// Membership is derived from `StudioFunction.allCases` rather than written out here: the
    /// four lists this replaces each had to be edited by hand for a new Studio, and anything
    /// missed was silent — no sidebar row, "Engine not installed" on Overview, no history tab.
    /// `createOrder` is a *display order* only. A Studio it does not name still lands in a
    /// section: an engine-less one in "Coming later", an engine-backed one at the end of
    /// "Create", so a new case can never be invisible.
    static let createOrder: [StudioFunction] = [.image, .audio, .transcription, .music, .chat,
                                                .video]
    static let create: [Self] = {
        let available = StudioFunction.allCases.filter { $0.hasEngine }
        let placed = createOrder.filter { available.contains($0) }
        let unplaced = available.filter { !createOrder.contains($0) }
        return (placed + unplaced).compactMap { Self(rawValue: $0.rawValue) }
    }()
    static let library: [Self] = [.models, .voices, .jobs]
    static let fleet: [Self] = [.connections]
    static let app: [Self] = [.whatsNew, .settings]
    static let comingLater: [Self] = StudioFunction.allCases
        .filter { !$0.hasEngine }
        .compactMap { Self(rawValue: $0.rawValue) }
    static let sidebarSections: [(title: String, pages: [Self])] = [
        ("Create", create),
        ("Library", library),
        ("Fleet", fleet),
        ("App", app),
        ("Coming later", comingLater),
    ]
    var function: StudioFunction? { StudioFunction(rawValue: rawValue) }
    /// The page that shows one Studio, derived from the raw values both enums persist. Nil when
    /// a Studio has no page, which a caller must say out loud rather than opening a wrong page.
    static func page(for function: StudioFunction) -> StudioPage? { StudioPage(rawValue: function.rawValue) }
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
    /// A private address: RFC1918, link-local, or Tailscale's carrier-grade NAT range. Public
    /// addresses are never chosen by default.
    var isPrivate: Bool {
        let parts = address.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4 else { return false }
        if parts[0] == 10 { return true }
        if parts[0] == 172, (16...31).contains(parts[1]) { return true }
        if parts[0] == 192, parts[1] == 168 { return true }
        if parts[0] == 169, parts[1] == 254 { return true }
        return isTailscale
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
    /// Whether `listenHost` is recomputed from this Mac's own addresses on every launch. See
    /// `Preferences.listenHostAutomatic` for why the default is yes.
    var listenHostAutomatic: Bool
    /// Why a listen flag could not be written down, or nil. Recorded rather than thrown: the
    /// launch honours the flag either way, so refusing to start would report the problem by
    /// causing a worse one. Published so "I set it and it did not stick" has an answer.
    var preferencesWriteFailure: String?
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
    /// This Mac's own node credential, issued by that controller in the join answer and presented
    /// on every call this Mac makes home. Stored with the membership and written 0600 — it is a
    /// secret, and it is the only credential whose revocation is about this Mac alone (A8).
    var nodeCredential: String?
    /// The controller's release-signing key, pinned the first time this Mac reads its identity.
    /// Kept with the membership: it is restored when the services start, and leaving the group
    /// clears it. A different key presented later is refused, never adopted silently.
    var releasePublisherPublicKey: String?
    var releasePublisherFingerprint: String?
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
    /// Whether the one-time move off the old `SMAppService` login item has been attempted. Written
    /// after the attempt, so a crash mid-migration retries on the next launch instead of leaving
    /// both a login item and a launch agent in place.
    var loginMigrationComplete: Bool
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
        listenHostAutomatic = true
        preferencesWriteFailure = nil
        port = ShellConfiguration.defaultPort
        controllerAddress = nil
        recoveryMaxAttempts = StartupRecovery.defaultMaxAutomaticAttempts
        recoveryBackoffSeconds = StartupRecovery.defaultDelays
        loginMigrationComplete = false
        var explicitModels: URL?
        var explicitOffline = false
        var explicitHost: String?
        var explicitAutomatic = false
        var explicitFleetMode = false
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
            } else if argument == "--fleet-mode" {
                // One switch for the three things a Mac that must be reachable needs, because
                // three flags are three chances to set two of them. Deliberately does NOT touch
                // sleep: holding a Mac awake is a power decision with its own costs, and nobody
                // has asked for it.
                explicitFleetMode = true
                index += 1
            } else if argument == "--listen-auto" {
                // The kit's supported way to turn automatic listening on and leave it on. Only
                // meaningful with a data directory the app will keep using, which is what makes
                // this a setting rather than a one-launch override.
                explicitAutomatic = true
                index += 1
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
            listenHostAutomatic = saved.listenHostAutomatic ?? true
            port = saved.port ?? Self.defaultPort
            controllerAddress = saved.controllerAddress
            releasePublisherPublicKey = saved.releasePublisherPublicKey
            releasePublisherFingerprint = saved.releasePublisherFingerprint
            loginMigrationComplete = saved.loginMigrationComplete
            if StartupRecovery.validPolicy(maxAttempts: saved.recoveryMaxAttempts,
                                           delays: saved.recoveryBackoffSeconds) {
                recoveryMaxAttempts = saved.recoveryMaxAttempts
                recoveryBackoffSeconds = saved.recoveryBackoffSeconds
            }
        }
        // First run: answer on this Mac's private and Tailscale networks instead of loopback
        // only. A fresh node was otherwise unreachable until its owner worked out that the Hub
        // had to be told to listen there - the exact step that stopped it joining a controller.
        if explicitHost == nil, readPrivateRegularFile(preferencesFile) == nil {
            listenHost = Self.defaultListenHost()
        }
        if let explicitModels { modelsDirectory = explicitModels }
        if explicitOffline { offline = true }
        if explicitFleetMode {
            // Reachable after a reboot, on every network this Mac has, without anyone opening the
            // app. The services half matters as much as the listen half: the app already started
            // at login, but the Hub did not, so a node that rebooted was invisible until somebody
            // logged in and clicked Start - which is the same class of forgotten step as the
            // Tailscale tick this release is about.
            listenHostAutomatic = true
            startAtLogin = true
            startServicesAtLogin = true
        }
        if explicitAutomatic { listenHostAutomatic = true }
        if let explicitHost {
            // Pinning an exact list is an explicit choice, so it turns automatic off: a caller
            // that named addresses meant those addresses.
            listenHost = explicitHost
            listenHostAutomatic = false
        } else if listenHostAutomatic {
            listenHost = Self.automaticListenHost(keeping: listenHost)
        }
        if let explicitPort { port = explicitPort }
        // A flag is a setting, not a one-launch override. `--listen-host 127.0.0.1` used to last
        // exactly until the next launch, because nothing wrote it down - so a kit that configured
        // a Mac and then watched it come up on the old addresses had no way to tell the
        // difference between "the flag was ignored" and "the flag was forgotten".
        //
        // A failed write does **not** stop the app from starting: this launch still honours the
        // flag, and only the next launch differs. Refusing to come up because a preference could
        // not be saved is a worse failure than the one it reports, so it is recorded instead and
        // published where an operator or the gateway can read it.
        if explicitHost != nil || explicitAutomatic || explicitFleetMode {
            do { try save() } catch { preferencesWriteFailure = "\(error)" }
        }
        try Self.requireBindableHost(listenHost)
        try Self.requireBindablePort(port)
        if let modelsDirectory {
            try Self.requireExternal(modelsDirectory, bundle: bundle)
            // Persisted missing volumes stay selected and visible for recovery. Explicit CLI input fails early.
            if explicitModels != nil { try Self.requireDirectory(modelsDirectory) }
        }
    }
    /// The addresses a brand-new install should listen on: every private or Tailscale address
    /// this Mac currently has. Loopback is always added by the Hub, so it is not listed here, and
    /// a public address is never included.
    static func defaultListenHost() -> String {
        let usable = LocalAddresses.current().filter(\.isPrivate).map(\.address)
        return usable.isEmpty ? "127.0.0.1" : usable.joined(separator: ",")
    }

    /// What the saved list becomes when this Mac is allowed to answer for itself.
    ///
    /// **Additive**: every address already saved is kept, and every private or Tailscale address
    /// this Mac has right now is added. Nothing is removed, so this can never take away a way in
    /// that was working; the worst case is an address that is no longer present, which
    /// `unavailableListenAddresses` already drops for the launch and names.
    ///
    /// Current addresses come first so the tailnet leads the list, which is the order the
    /// Connections screen shows and the order the owner asked for.
    static func automaticListenHost(keeping saved: String) -> String {
        var ordered: [String] = []
        let candidates = LocalAddresses.current().filter(\.isPrivate).map(\.address)
            + saved.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
        for candidate in candidates where !candidate.isEmpty && !ordered.contains(candidate) {
            ordered.append(candidate)
        }
        return ordered.isEmpty ? "127.0.0.1" : ordered.joined(separator: ",")
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
                                                        listenHostAutomatic: listenHostAutomatic,
                                                        port: port,
                                                        controllerAddress: controllerAddress,
                                                        nodeCredential: nodeCredential,
                                                        releasePublisherPublicKey: releasePublisherPublicKey,
                                                        releasePublisherFingerprint: releasePublisherFingerprint,
                                                        recoveryMaxAttempts: recoveryMaxAttempts,
                                                        recoveryBackoffSeconds: recoveryBackoffSeconds,
                                                        loginMigrationComplete: loginMigrationComplete))
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
        /// Whether the listen list is recomputed from this Mac's own addresses on every launch,
        /// or pinned to exactly `listenHost`.
        ///
        /// Absent means **automatic**, and that is a deliberate change of default rather than an
        /// accident of decoding. A fresh install always computed its addresses from the machine
        /// (`defaultListenHost()`), but the moment anything was saved they froze — so a Mac whose
        /// owner never opened Connections, or who set it up before it joined a tailnet, stayed on
        /// loopback (or on a LAN address from a network it had since left) forever. The owner
        /// asked for the tailnet to be on without having to remember it.
        ///
        /// Automatic is **additive**: the saved addresses are kept and the Mac's current private
        /// and tailnet addresses are added. It never removes an address. An operator who really
        /// wants loopback only sets this to `false`, which the Connections list does on the first
        /// manual change, and which `--listen-host` does when the kit pins an exact list.
        let listenHostAutomatic: Bool?
        /// Absent in a preferences file written before the port was pinned, and absent means the
        /// Hub's own default rather than whatever the operating system hands out next time.
        let port: Int?
        /// Absent for every Mac that has never joined a controller, which is most of them.
        let controllerAddress: String?
        /// **This Mac's own node credential**, issued by the controller it joined and presented on
        /// every call this Mac makes home (A8). It is what names this Mac alone: the group Fleet
        /// token cannot be revoked from one member, so a Mac that is sold has to be cut off by its
        /// own credential. Kept in this file, which is written 0600 in a 0700 directory, and beside
        /// the membership it belongs to — leaving the group clears both.
        let nodeCredential: String?
        /// The controller's pinned release-signing key. Absent until the first release read, and
        /// absent again after leaving the group.
        let releasePublisherPublicKey: String?
        let releasePublisherFingerprint: String?
        let recoveryMaxAttempts: Int
        let recoveryBackoffSeconds: [TimeInterval]
        /// Absent in every preferences file written before the launch agent replaced the login
        /// item, and absent means the one-time migration has not run yet.
        let loginMigrationComplete: Bool

        init(modelsPath: String, offline: Bool, startAtLogin: Bool, startServicesAtLogin: Bool,
             listenHost: String, listenHostAutomatic: Bool?, port: Int?, controllerAddress: String?,
             nodeCredential: String?,
             releasePublisherPublicKey: String?, releasePublisherFingerprint: String?,
             recoveryMaxAttempts: Int,
             recoveryBackoffSeconds: [TimeInterval],
             loginMigrationComplete: Bool) {
            self.modelsPath = modelsPath
            self.offline = offline
            self.startAtLogin = startAtLogin
            self.startServicesAtLogin = startServicesAtLogin
            self.listenHost = listenHost
            self.listenHostAutomatic = listenHostAutomatic
            self.port = port
            self.controllerAddress = controllerAddress
            self.nodeCredential = nodeCredential
            self.releasePublisherPublicKey = releasePublisherPublicKey
            self.releasePublisherFingerprint = releasePublisherFingerprint
            self.recoveryMaxAttempts = recoveryMaxAttempts
            self.recoveryBackoffSeconds = recoveryBackoffSeconds
            self.loginMigrationComplete = loginMigrationComplete
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
            // Every build before fleet support was loopback-only. The address itself is kept as
            // written; whether it is the *whole* list is what `listenHostAutomatic` decides, and
            // an absent flag means automatic — see the field's own comment.
            listenHost = try values.decodeIfPresent(String.self, forKey: .listenHost) ?? "127.0.0.1"
            listenHostAutomatic = try values.decodeIfPresent(Bool.self, forKey: .listenHostAutomatic)
            port = try values.decodeIfPresent(Int.self, forKey: .port)
            controllerAddress = try values.decodeIfPresent(String.self, forKey: .controllerAddress)
            nodeCredential = try values.decodeIfPresent(String.self, forKey: .nodeCredential)
            releasePublisherPublicKey = try values.decodeIfPresent(String.self, forKey: .releasePublisherPublicKey)
            releasePublisherFingerprint = try values.decodeIfPresent(String.self, forKey: .releasePublisherFingerprint)
            recoveryMaxAttempts = try values.decodeIfPresent(Int.self, forKey: .recoveryMaxAttempts)
                ?? StartupRecovery.defaultMaxAutomaticAttempts
            recoveryBackoffSeconds = try values.decodeIfPresent([TimeInterval].self, forKey: .recoveryBackoffSeconds)
                ?? StartupRecovery.defaultDelays
            // Absent means the migration has not run, which is the state an upgrade starts in.
            loginMigrationComplete = try values.decodeIfPresent(Bool.self, forKey: .loginMigrationComplete) ?? false
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

/// The local service log is rotated once it grows past this size. One previous
/// file is kept, so the pair is bounded at roughly twice this. Without it a
/// long-running install wrote an unbounded log (263 MB was measured on
/// 2026-09-22), which is a disk risk for a shipped app.
let privateLogRotationBytes: off_t = 32 * 1024 * 1024

func rotatePrivateLogIfNeeded(_ url: URL) {
    guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
          let size = attributes[.size] as? NSNumber,
          size.int64Value >= privateLogRotationBytes else { return }
    let previous = url.deletingLastPathComponent()
        .appendingPathComponent(url.lastPathComponent + ".1")
    try? FileManager.default.removeItem(at: previous)
    try? FileManager.default.moveItem(at: url, to: previous)
}

func openPrivateLog(_ url: URL) throws -> FileHandle {
    rotatePrivateLogIfNeeded(url)
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

/// Whether the one login mechanism is in place.
enum LoginAgentStatus: Equatable {
    case enabled
    case disabled
}

/// The one-time move off the old `SMAppService` login item.
///
/// A Mac that ran an older build has an `SMAppService` registration; this build writes a launchd
/// agent instead. Left alone, both would start the app at login and the second instance would fail
/// on the data-folder lock. The decision is pure and the two side effects are injected, so the
/// contract suite exercises every branch without touching the real system; the app supplies the
/// real `SMAppService` calls in one place. The migration never reads or writes the launch agent.
enum LoginMigration {
    /// The old registration as `SMAppService` would report it.
    enum OldStatus: Equatable {
        case enabled
        case requiresApproval
        case notRegistered
        case notFound
        /// ServiceManagement could not be used at all, so the old entry cannot be inspected.
        case unavailable
    }
    enum Outcome: Equatable {
        /// The recorded boolean was already set; nothing was read or changed.
        case alreadyDone
        /// No old registration was enabled.
        case nothingToRemove
        /// The old registration was found and removed.
        case removed
        /// The old registration could not be removed; the app must still run.
        case failed(String)
    }

    static func run(alreadyRecorded: Bool,
                    oldStatus: () -> OldStatus,
                    unregister: () throws -> Void) -> Outcome {
        guard !alreadyRecorded else { return .alreadyDone }
        switch oldStatus() {
        case .enabled, .requiresApproval:
            do { try unregister(); return .removed }
            catch { return .failed(error.localizedDescription) }
        case .notRegistered, .notFound:
            return .nothingToRemove
        case .unavailable:
            return .failed("ServiceManagement is unavailable")
        }
    }

    /// The one-line notice to show when the old entry could not be removed, or nil.
    static func notice(for outcome: Outcome) -> String? {
        guard case .failed(let detail) = outcome else { return nil }
        return "An older \"Start at login\" entry could not be removed (\(detail)). Studio Hub still "
            + "runs; if it starts twice at login, remove the old entry in System Settings > "
            + "General > Login Items."
    }
}

/// The single mechanism that keeps Studio Hub running on an unattended Mac: a per-user launchd
/// agent.
///
/// It replaced `SMAppService.mainApp`, which starts the app at login but does nothing when the app
/// crashes or is quit. Supervising the app's own executable is the whole point: supervising
/// `/usr/bin/open` would track a process that exits immediately, so `KeepAlive` would never work.
/// `KeepAlive` is `SuccessfulExit: false`, so launchd relaunches after a crash (a non-zero exit or
/// a signal) and leaves the app stopped after a deliberate Quit, which exits 0.
enum LaunchAgent {
    static let label = "local.studiohub.standalone"
    static let executableRelativePath = "Contents/MacOS/StudioHub"
    static let defaultApplicationsRoot = URL(fileURLWithPath: "/Applications")
    /// Set in the agent's environment so a launchd launch is still recognisable as a login launch.
    /// launchd starts the executable directly, so the AppleEvent that marked a login item is absent;
    /// without this the app could not tell a login launch from a person opening it, and would start
    /// the services at login even when "Start services at login" is off.
    static let launchedAtLoginEnvironmentKey = "STUDIOHUB_LAUNCHED_AT_LOGIN"

    enum InstallDecision: Equatable {
        case install(executablePath: String)
        case refuse(String)
    }

    /// The program launchd must supervise: the app's own executable, never `/usr/bin/open`.
    static func executablePath(forAppBundle bundle: URL) -> String {
        bundle.appendingPathComponent(executableRelativePath).path
    }

    static func defaultPlistURL(home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> URL {
        home.appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    /// Whether the agent may be written, as a pure decision over the bundle path and whether its
    /// executable exists. A dev build outside `/Applications` is refused rather than writing an
    /// agent that points at a path that will not be there at login.
    static func installDecision(
        appBundle: URL,
        applicationsRoot: URL = defaultApplicationsRoot,
        executableExists: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) }
    ) -> InstallDecision {
        let root = applicationsRoot.standardizedFileURL.resolvingSymlinksInPath().path
        let bundle = appBundle.standardizedFileURL.resolvingSymlinksInPath().path
        guard bundle == root || bundle.hasPrefix(root + "/") else {
            return .refuse("Studio Hub has to be in \(root) before it can start at login. "
                + "Move the app there, then turn Start at login on again.")
        }
        let executable = executablePath(forAppBundle: appBundle)
        guard executableExists(executable) else {
            return .refuse("Studio Hub's own program is missing from \(bundle). "
                + "Reinstall the app before turning Start at login on.")
        }
        return .install(executablePath: executable)
    }

    /// The agent's property list.
    static func propertyList(executablePath: String) -> [String: Any] {
        ["Label": label,
         "ProgramArguments": [executablePath],
         "RunAtLoad": true,
         "KeepAlive": ["SuccessfulExit": false],
         "EnvironmentVariables": [launchedAtLoginEnvironmentKey: "1"]]
    }

    static func plistData(executablePath: String) throws -> Data {
        try PropertyListSerialization.data(fromPropertyList: propertyList(executablePath: executablePath),
                                           format: .xml, options: 0)
    }

    /// Write the agent at `plistURL`, atomically and mode 0600. Returns the supervised program.
    @discardableResult
    static func write(appBundle: URL, to plistURL: URL,
                      applicationsRoot: URL = defaultApplicationsRoot,
                      executableExists: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) }) throws -> String {
        switch installDecision(appBundle: appBundle, applicationsRoot: applicationsRoot,
                               executableExists: executableExists) {
        case .refuse(let message):
            throw ShellFailure(message: message)
        case .install(let executable):
            let data = try plistData(executablePath: executable)
            let directory = plistURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            // `.atomic` writes a sibling and renames it; the chmod closes the window in which the
            // temporary file would be world-readable.
            try data.write(to: plistURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: plistURL.path)
            return executable
        }
    }

    /// Remove the agent if it is present. Removing a file that is not there is not an error.
    static func remove(at plistURL: URL) throws {
        if FileManager.default.fileExists(atPath: plistURL.path) {
            try FileManager.default.removeItem(at: plistURL)
        }
    }

    static func isInstalled(at plistURL: URL) -> Bool {
        FileManager.default.fileExists(atPath: plistURL.path)
    }

    /// Whether the operator sees "Start at login" as on.
    ///
    /// The plist existing is the signal. launchd loads `~/Library/LaunchAgents` at login, so a
    /// correctly installed agent is deliberately NOT loaded while the app is running; asking
    /// `launchctl print` would answer false for it and make the toggle lie. The launchd
    /// disabled-overrides database is not consulted either, because reading it costs a launchctl
    /// subprocess on every status refresh and the operator's choice is the plist itself.
    static func status(at plistURL: URL) -> LoginAgentStatus {
        isInstalled(at: plistURL) ? .enabled : .disabled
    }

    /// Load the agent. Kept as a real function so the toggle's plan can hold it as a spy field:
    /// enabling must never call it while the app is running, because the plist sets RunAtLoad and
    /// launchd would start a second copy of the app immediately.
    static func bootstrap(at plistURL: URL) {
        run(["bootstrap", "gui/\(getuid())", plistURL.path])
    }

    /// Unload the agent. This is what makes disabling take effect immediately.
    static func bootout(label: String = LaunchAgent.label) {
        run(["bootout", "gui/\(getuid())/\(label)"])
    }

    private static func run(_ arguments: [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try? process.run()
        process.waitUntilExit()
    }
}

/// The side effects the "Start at login" toggle performs, injected so the contract suite can assert
/// exactly which launchctl calls happen and, just as importantly, which do not.
struct LoginAgentOperations {
    var write: () throws -> Void
    var bootout: () -> Void
    var remove: () throws -> Void
    /// Present only so a test can prove the enabling path never calls it. Enabling must not
    /// bootstrap: the plist sets `RunAtLoad`, so loading the job would start a second copy of the
    /// app that is already running.
    var bootstrap: () -> Void
}

enum LoginAgent {
    /// Apply the toggle.
    ///
    /// Enabling only writes the plist. A plist in `~/Library/LaunchAgents` is loaded by launchd at
    /// login, which is exactly what "Start at login" means, and `RunAtLoad` then starts the app.
    /// Calling `launchctl bootstrap` here would load the job immediately and start a second copy of
    /// the running app. A job already loaded with an older copy of the plist is left alone: there is
    /// no launchctl call that loads a `RunAtLoad` job without starting it, so the next login picks
    /// the new plist up. `operations.bootstrap` is deliberately never called.
    ///
    /// Disabling takes effect immediately: the loaded job is booted out before the plist is removed.
    static func apply(enabled: Bool, using operations: LoginAgentOperations) throws {
        if enabled {
            try operations.write()
            return
        }
        operations.bootout()
        try operations.remove()
    }
}

/// Why the app could not take its data-folder lock. The distinction matters: "already held" means
/// another copy of Studio Hub is the real app, while "unavailable" is a real failure to open.
enum InstanceLockError: LocalizedError, Equatable {
    case alreadyHeld
    case unavailable(String)
    var errorDescription: String? {
        switch self {
        case .alreadyHeld:
            return "Studio Hub is already using this data folder. Open the running app from its menu-bar icon."
        case .unavailable(let message):
            return message
        }
    }
}

final class InstanceLock {
    private let descriptor: Int32
    init(directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        descriptor = open(directory.appendingPathComponent("app.lock").path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw InstanceLockError.unavailable("Cannot open the app's data folder lock.") }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            close(descriptor)
            throw InstanceLockError.alreadyHeld
        }
    }
    deinit { close(descriptor) }
}

/// What a redundant launch does when another process already owns the data folder.
///
/// This is the second copy launchd can start (for example, a previously loaded agent) while the
/// real app is running. The new process must never show the "Studio Hub could not open" alert: it
/// brings the running app forward if it can and leaves with status 0, which `KeepAlive` treats as a
/// clean exit and never relaunches. The present/exit actions are injected so the contract suite can
/// prove the behaviour without AppKit or terminating the test process.
enum ExistingInstance {
    enum Decision: Equatable {
        case continueStarting
        case presentAndExit
    }

    /// The status a redundant launch exits with. 0 is a clean exit, so launchd's
    /// `KeepAlive { SuccessfulExit: false }` never relaunches the copy.
    static let redundantLaunchExitCode: Int32 = 0

    static func decide(lockHeldByAnother: Bool) -> Decision {
        lockHeldByAnother ? .presentAndExit : .continueStarting
    }

    static func handle(_ decision: Decision,
                       present: () -> Void,
                       exitQuietly: () -> Void) {
        guard decision == .presentAndExit else { return }
        present()
        exitQuietly()
    }
}
