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
    /// The install screen (owner decision 2026-10-28): choose a preset for this Mac's memory, or
    /// tick models by hand, and see the free space and the cost before anything is copied.
    case setup = "Set up this Mac"
    case models = "Models", voices = "Voices", jobs = "Jobs", connections = "Connections"
    /// The final renders this Hub has run (owner, 2026-10-26: *"there's no render tab"*). Not a
    /// Studio: no lane generates into it, the render service records into it.
    case renders = "Renders"
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
        // The Video lane was retired (LTX-2.3); its page stays for old links and its finished
        // clips stay in Jobs, but a retired feature is not navigation (critic, 2026-11-13).
        return (placed + unplaced).compactMap { Self(rawValue: $0.rawValue) }
            .filter { $0 != .video }
    }()
    static let library: [Self] = [.setup, .models, .voices, .jobs, .renders]
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
        case .setup: return "wand.and.stars"
        case .models: return "externaldrive"
        case .voices: return "person.wave.2"
        case .jobs: return "list.bullet.rectangle"
        case .renders: return "play.rectangle.on.rectangle"
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
    /// Whether "Start services on startup" has been decided by something other than the bare default:
    /// the owner toggled it, `--fleet-mode` set it, or the fleet default below was applied.
    ///
    /// **Why this exists (owner, 2026-10-02):** about thirty Macs were set up without ticking
    /// "Start services on startup", and a Mac whose services are off is silent to its controller
    /// after every reboot. A Mac that belongs to a fleet exists to serve, so the default is applied
    /// to it once - and only once, so an owner who later turns it off on purpose is not overridden
    /// by the next launch.
    var startServicesChoiceRecorded: Bool
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
    /// `recoveryMaxAttempts` is this Mac's own `setRecoveryPolicy` switch carried onto the same
    /// command line: the worker's child watcher restarts a studio that has stopped answering, and
    /// a Mac whose owner asked for no automatic restarts must not have one restarted behind their
    /// back. Zero means never; the worker keeps restarting a studio that has actually exited.
    static func serviceArguments(dataDirectory: URL, modelsDirectory: URL, readyFile: URL,
                                 parentPID: Int32, listenHost: String, offline: Bool,
                                 recoveryMaxAttempts: Int,
                                 port: Int = ShellConfiguration.defaultPort) -> [String] {
        var arguments = ["--data-dir", dataDirectory.path,
                         "--models-dir", modelsDirectory.path,
                         "--ready-file", readyFile.path,
                         "--parent-pid", String(parentPID),
                         "--host", listenHost,
                         "--port", String(port),
                         "--recovery-attempts", String(recoveryMaxAttempts)]
        if offline { arguments.append("--offline") }
        return arguments
    }
    /// Whether this Mac has joined a controller: it holds the address it joined.
    var isJoinedNode: Bool {
        guard let address = controllerAddress else { return false }
        return !address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Turn "Start services on startup" on, once, for a Mac that belongs to a fleet.
    ///
    /// `inFleet` is true for a node that holds a controller address, and for a Hub that reports
    /// itself a controller or a node (a running Hub tells the app its role). A Mac that is not in a
    /// fleet is left exactly as it is: one that is only used in person should not hold its Studios
    /// open all night. Returns true when the configuration changed (the setting, or only the record
    /// that the default has been applied), so the caller knows to save.
    ///
    /// Recorded the first time it runs for a fleet Mac, whether or not it had to change anything, so
    /// the owner's later choice (a deliberate off) is never undone by a launch.
    @discardableResult
    mutating func applyFleetServicesDefault(inFleet: Bool) -> Bool {
        guard inFleet, !startServicesChoiceRecorded else { return false }
        startServicesChoiceRecorded = true
        guard !startServicesAtLogin else { return true }
        startServicesAtLogin = true
        return true
    }

    /// The owner's own choice: remembered as a choice, so the fleet default never overrides it.
    mutating func chooseStartServices(_ enabled: Bool) {
        startServicesAtLogin = enabled
        startServicesChoiceRecorded = true
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
        startServicesChoiceRecorded = false
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
            startServicesChoiceRecorded = saved.startServicesChoiceRecorded ?? false
            listenHost = saved.listenHost
            listenHostAutomatic = saved.listenHostAutomatic ?? true
            port = saved.port ?? Self.defaultPort
            controllerAddress = saved.controllerAddress
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
            startServicesChoiceRecorded = true
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

    /// Which of the addresses this Mac was missing at start it has since acquired.
    ///
    /// The distinction this exists for: at boot a chosen address is usually **not up yet** rather
    /// than gone, and one reading cannot tell the two apart. The app used to treat the first reading
    /// as final, which on a headless Mac that boots before its network binds the Hub to loopback —
    /// healthy, answering its own health check, and reachable by no node at all, until somebody
    /// stopped and started the services by hand. So the missing set is kept and re-checked, and this
    /// answers "has it arrived?" without a second copy of the comparison.
    ///
    /// Pure, and `assigned` is injectable, so the case that matters — a headless boot where the
    /// address turns up a minute later — is testable without waiting for a network.
    static func arrivedAddresses(
        missing: [String],
        assigned: [String] = LocalAddresses.current().map(\.address)
    ) -> [String] {
        let present = Set(assigned)
        return missing.filter(present.contains)
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

    /// The canonical home-folder library: the folder the kit's copy step fills, and the folder a Mac
    /// that has never chosen one uses. `components/model_library.py` carries the same two names for
    /// the kit's Python, so the app, the kit and its copy helper cannot drift apart again (owner
    /// report, 2026-10-18: the drive filled one folder while the app was using another).
    static func defaultLibrary(home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> URL {
        home.appendingPathComponent(libraryFolderName).standardizedFileURL.resolvingSymlinksInPath()
    }

    /// The folder the "Use Default Folder" button selects.
    ///
    /// **An install that already keeps its models in the app's own data folder keeps them there.**
    /// `<data>/models` was this button's answer for a long time, so a Mac set up then may hold its
    /// whole library in it; selecting another folder would leave those models behind. Only a folder
    /// with something in it counts - the button creates the empty one, so emptiness proves nothing.
    /// Everywhere else the answer is the canonical home-folder library, which is the folder the kit's
    /// copy step fills and what `components/model_library.py` resolves for that step, so a new Mac
    /// cannot end up with two model folders again.
    static func defaultLibraryFolder(dataDirectory: URL,
                                     home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> URL {
        let appOwned = dataDirectory.appendingPathComponent("models")
        let entries = (try? FileManager.default.contentsOfDirectory(atPath: appOwned.path)) ?? []
        if !entries.isEmpty { return appOwned.standardizedFileURL.resolvingSymlinksInPath() }
        return defaultLibrary(home: home)
    }

    /// The app's own data folder under a home folder - the default `dataDirectory` in `init`.
    ///
    /// Named here so `preparedLibrary` can look inside it with no data directory to hand (the
    /// native test calls it with a fake home), and so the folder it looks in is the same one the
    /// kit's `components/model_library.py` reads as `<data>/models`.
    static func defaultDataDirectory(home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> URL {
        home.appendingPathComponent("Library/Application Support/StudioHub Standalone")
    }

    /// A model library the one-click setup has already filled in.
    ///
    /// Without this the operator had to find the folder in a file panel. A library recognised here
    /// is offered as **Use the Models Already on This Mac**, and it is the same folder
    /// `defaultLibrary(home:)` names, so the app's default and the kit's copy step cannot land on
    /// two different folders (they did: the default used to be an empty folder inside the app's own
    /// data directory, which invited a second, 29 GB download of models already on the disk).
    ///
    /// **The app's own data folder is offered first when it holds anything.** `<data>/models` was
    /// the "Use Default Folder" answer for a long time, and `components/model_library.py` resolves a
    /// non-empty one for the kit's copy step (its step 2) - so this has to prefer it too, or the
    /// screen would offer the canonical folder while the copy step filled the data folder. Only a
    /// folder with something in it counts: the button creates the empty one. The `dataDirectory` is
    /// the running app's own (a `--data-dir` override included); with none to hand the default under
    /// `home` is used.
    ///
    /// Recognised by its install record, not by its name: any folder holding
    /// `.studiohub/installed.json` is a library the app wrote, so this can never offer a random
    /// folder that merely happens to be called `model-library`.
    static func preparedLibrary(dataDirectory: URL? = nil,
                                home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> URL? {
        let appOwned = (dataDirectory ?? defaultDataDirectory(home: home))
            .appendingPathComponent("models")
        let entries = (try? FileManager.default.contentsOfDirectory(atPath: appOwned.path)) ?? []
        if !entries.isEmpty { return appOwned.standardizedFileURL.resolvingSymlinksInPath() }
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
    ///
    /// `/Volumes` is here for the same reason as the home folders: reading removable volumes is
    /// gated by macOS, and on an ad-hoc signed build that permission is lost on every update. A
    /// library there can block the service inside `open()` exactly as `~/Downloads` did, so the
    /// warning names it before a person chooses it. The Hub itself reads no drive at all (0.6.212);
    /// only a library the person chose here is read.
    static let protectedLibraryFolders = ["Desktop", "Documents", "Downloads", "Movies", "Music", "Pictures"]
    static let protectedVolumeRoots = ["/Volumes"]

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
        }) else {
            // An external drive, or a folder on one: macOS asks for permission to read removable
            // volumes, and a refused or unanswered prompt blocks a read the same way.
            if protectedVolumeRoots.contains(where: { path == $0 || path.hasPrefix($0 + "/") }) {
                return "macOS asks for permission before an app may read removable volumes, and an "
                    + "unanswered prompt blocks the read - the services can then wait for ever on a "
                    + "library on this drive. That is expected on a kit drive: click Allow when macOS "
                    + "asks for \u{201C}Removable Volumes\u{201D}, then choose this folder again."
            }
            return nil
        }
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
                                                        startServicesChoiceRecorded: startServicesChoiceRecorded,
                                                        listenHost: listenHost,
                                                        listenHostAutomatic: listenHostAutomatic,
                                                        port: port,
                                                        controllerAddress: controllerAddress,
                                                        nodeCredential: nodeCredential,
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
        /// Absent in every file written before the fleet default existed: absent means "not decided".
        let startServicesChoiceRecorded: Bool?
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
        let recoveryMaxAttempts: Int
        let recoveryBackoffSeconds: [TimeInterval]
        /// Absent in every preferences file written before the launch agent replaced the login
        /// item, and absent means the one-time migration has not run yet.
        let loginMigrationComplete: Bool

        init(modelsPath: String, offline: Bool, startAtLogin: Bool, startServicesAtLogin: Bool,
             startServicesChoiceRecorded: Bool?, listenHost: String, listenHostAutomatic: Bool?, port: Int?, controllerAddress: String?,
             nodeCredential: String?,
             recoveryMaxAttempts: Int,
             recoveryBackoffSeconds: [TimeInterval],
             loginMigrationComplete: Bool) {
            self.modelsPath = modelsPath
            self.offline = offline
            self.startAtLogin = startAtLogin
            self.startServicesAtLogin = startServicesAtLogin
            self.startServicesChoiceRecorded = startServicesChoiceRecorded
            self.listenHost = listenHost
            self.listenHostAutomatic = listenHostAutomatic
            self.port = port
            self.controllerAddress = controllerAddress
            self.nodeCredential = nodeCredential
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
            startServicesChoiceRecorded = try values.decodeIfPresent(Bool.self, forKey: .startServicesChoiceRecorded)
            // Every build before fleet support was loopback-only. The address itself is kept as
            // written; whether it is the *whole* list is what `listenHostAutomatic` decides, and
            // an absent flag means automatic — see the field's own comment.
            listenHost = try values.decodeIfPresent(String.self, forKey: .listenHost) ?? "127.0.0.1"
            listenHostAutomatic = try values.decodeIfPresent(Bool.self, forKey: .listenHostAutomatic)
            port = try values.decodeIfPresent(Int.self, forKey: .port)
            controllerAddress = try values.decodeIfPresent(String.self, forKey: .controllerAddress)
            nodeCredential = try values.decodeIfPresent(String.self, forKey: .nodeCredential)
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
    if FileManager.default.fileExists(atPath: previous.path) { retireRotatedLog(previous) }
    try? FileManager.default.moveItem(at: url, to: previous)
}

/// The Hub's Trash Folder (`<data>/hub/trash/<UTC date>/...`). Nothing in it is deleted by the Hub:
/// the owner empties it through macOS Trash (owner request 2026-10-15), so anything moved here can be
/// taken back until then.
enum HubTrash {
    static var live: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/StudioHub Standalone/hub/trash", isDirectory: true)
    }

    /// Move `item` into `<trash>/<UTC date>/<label>-<id>/`, keeping its name unless `named` says
    /// otherwise. Only ever a same-volume rename: returns false, leaving `item` untouched, if the
    /// Trash Folder is on another volume or the move fails. It never copies and never deletes;
    /// what to do about a false is the caller's decision.
    @discardableResult
    static func move(_ item: URL, into trash: URL, label: String, named: String? = nil,
                     fileManager: FileManager = .default, now: Date = Date()) -> Bool {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(identifier: "UTC")
        format.dateFormat = "yyyy-MM-dd"
        let folder = trash.appendingPathComponent(format.string(from: now), isDirectory: true)
            .appendingPathComponent("\(label)-\(UUID().uuidString.prefix(8))", isDirectory: true)
        do {
            try fileManager.createDirectory(at: folder, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            let here = try item.resourceValues(forKeys: [.volumeIdentifierKey]).volumeIdentifier
            let there = try folder.resourceValues(forKeys: [.volumeIdentifierKey]).volumeIdentifier
            if let here, let there, here.isEqual(there) {
                try fileManager.moveItem(at: item, to: folder.appendingPathComponent(named ?? item.lastPathComponent))
                return true
            }
        } catch {}
        rmdir(folder.path)   // the folder made above, only if it is still empty
        return false
    }

    /// Retire `item`: into the Hub's Trash Folder, else (no Trash Folder, another volume, a failed move)
    /// into macOS Trash, else it stays where it is. Never a delete. Returns whether it moved.
    @discardableResult
    static func retire(_ item: URL, into trash: URL?, label: String, named: String? = nil,
                       fileManager: FileManager = .default) -> Bool {
        if let trash, move(item, into: trash, label: label, named: named, fileManager: fileManager) { return true }
        return (try? fileManager.trashItem(at: item, resultingItemURL: nil)) != nil
    }
}

/// A rotated log is retired, never deleted: into the Hub's Trash Folder (`<data>/hub/trash`, the one
/// the app's storage screen shows), else macOS Trash, else it stays where it is. The live log is
/// truncated only by the Hub's disk governor, under red.
func retireRotatedLog(_ url: URL) {
    let format = DateFormatter()
    format.locale = Locale(identifier: "en_US_POSIX")
    format.timeZone = TimeZone(identifier: "UTC")
    format.dateFormat = "yyyy-MM-dd"
    let folder = url.deletingLastPathComponent()
        .appendingPathComponent("hub/trash", isDirectory: true)
        .appendingPathComponent(format.string(from: Date()), isDirectory: true)
        .appendingPathComponent("service-log-\(UUID().uuidString.prefix(8))", isDirectory: true)
    do {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let here = try url.resourceValues(forKeys: [.volumeIdentifierKey]).volumeIdentifier
        let there = try folder.resourceValues(forKeys: [.volumeIdentifierKey]).volumeIdentifier
        if let here, let there, here.isEqual(there) {
            try FileManager.default.moveItem(at: url, to: folder.appendingPathComponent(url.lastPathComponent))
            return
        }
    } catch {}
    rmdir(folder.path)   // the folder made above, only if it is still empty
    _ = try? FileManager.default.trashItem(at: url, resultingItemURL: nil)
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

enum LaunchctlCommandResult: Equatable {
    case exited(Int32)
    case signaled(Int32)
    case spawnFailed
    case timedOut(terminationConfirmed: Bool)
}

typealias LaunchctlRunner = ([String]) -> LaunchctlCommandResult

/// Narrow process lifecycle used by the bounded launchctl runner. Tests drive these actions
/// directly without launching launchctl or addressing any real service label.
struct LaunchctlProcessLifecycle {
    var start: () throws -> Void
    var wait: (TimeInterval) -> Bool
    var terminate: () -> Void
    var forceKill: () -> Bool
    var status: () -> Int32
    var terminationReason: () -> Process.TerminationReason
}

final class LoginAgentMutationCoordinator: @unchecked Sendable {
    static let shared = LoginAgentMutationCoordinator()
    private let lock = NSRecursiveLock()
    private var quarantined = false

    func perform<T>(_ operation: () throws -> T) rethrows -> T? {
        lock.lock()
        defer { lock.unlock() }
        guard !quarantined else { return nil }
        return try operation()
    }

    func quarantine() {
        lock.lock()
        quarantined = true
        lock.unlock()
    }
}

enum LoginAgentRefreshStage: Equatable { case bootout, bootstrap }
enum LoginAgentBootoutOutcome: Equatable { case unloaded, alreadyAbsent }
enum LoginAgentRefreshOutcome: Equatable {
    case skipped
    case quarantined
    case spawnFailed(stage: LoginAgentRefreshStage)
    case signaled(stage: LoginAgentRefreshStage, signal: Int32)
    case uncertain(stage: LoginAgentRefreshStage, terminationConfirmed: Bool)
    case bootoutFailed(status: Int32)
    case bootstrapFailed(status: Int32)
    case registrationAccepted(bootout: LoginAgentBootoutOutcome)

    var sanitizedDescription: String {
        switch self {
        case .skipped: return "skipped: login startup is not eligible"
        case .quarantined: return "skipped: a prior login-agent command has an uncertain outcome"
        case .spawnFailed(let stage): return "\(stage) could not start launchctl"
        case .signaled(let stage, let signal):
            return "\(stage) launchctl ended from signal \(signal); registration outcome uncertain"
        case .uncertain(let stage, let confirmed):
            return "\(stage) timed out; launchctl termination \(confirmed ? "confirmed" : "unconfirmed") and registration outcome uncertain"
        case .bootoutFailed(let status): return "bootout failed with status \(status)"
        case .bootstrapFailed(let status): return "bootstrap failed with status \(status)"
        case .registrationAccepted(let bootout):
            return "registration accepted after \(bootout == .unloaded ? "bootout" : "absent job")"
        }
    }
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
        return "An older \"Start at login\" entry could not be removed (\(detail)). Hub Studio KH still "
            + "runs; if it starts twice at login, remove the old entry in System Settings > "
            + "General > Login Items."
    }
}

/// The single mechanism that keeps Hub Studio KH running on an unattended Mac: a per-user launchd
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
    static let jobAbsentExitStatus: Int32 = 3

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
            return .refuse("Hub Studio KH has to be in \(root) before it can start at login. "
                + "Move the app there, then turn Start at login on again.")
        }
        let executable = executablePath(forAppBundle: appBundle)
        guard executableExists(executable) else {
            return .refuse("Hub Studio KH's own program is missing from \(bundle). "
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

    /// The program an installed agent names, or nil when the plist is absent or unreadable.
    static func installedExecutablePath(at plistURL: URL) -> String? {
        guard let data = try? Data(contentsOf: plistURL),
              let plist = try? PropertyListSerialization.propertyList(
                  from: data, options: [], format: nil) as? [String: Any],
              let arguments = plist["ProgramArguments"] as? [String] else { return nil }
        return arguments.first
    }

    /// Whether an agent that exists names a program other than this app's.
    ///
    /// The plist's existence is the operator's "on", but the path inside it goes stale when the app
    /// is renamed or moved: 0.6.228 renamed the bundle to `Hub Studio KH.app`, while every Mac kept
    /// an agent pointing at the old `Studio Hub.app` path, so login startup ran a program that no
    /// longer existed. An unreadable plist counts as needing repair.
    static func needsRepair(at plistURL: URL, currentExecutable: String) -> Bool {
        guard FileManager.default.fileExists(atPath: plistURL.path) else { return false }
        guard let installed = installedExecutablePath(at: plistURL) else { return true }
        return installed != currentExecutable
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
        _ = run(["bootstrap", "gui/\(getuid())", plistURL.path])
    }

    /// Unload the agent. This is what makes disabling take effect immediately.
    static func bootout(label: String = LaunchAgent.label) -> LaunchctlCommandResult {
        run(["bootout", "gui/\(getuid())/\(label)"])
    }

    static func refreshRegistration(
        at plistURL: URL, label: String = LaunchAgent.label,
        installed: (URL) -> Bool = LaunchAgent.isInstalled,
        coordinator: LoginAgentMutationCoordinator = .shared,
        runner: LaunchctlRunner = LaunchAgent.run
    ) -> LoginAgentRefreshOutcome {
        guard let outcome: LoginAgentRefreshOutcome = coordinator.perform({ () -> LoginAgentRefreshOutcome in
            // This check belongs inside the same boundary as Settings disable. Otherwise a queued
            // refresh can observe the plist, wait behind disable, then load the removed job again.
            guard installed(plistURL) else { return .skipped }
            let bootout = runner(["bootout", "gui/\(getuid())/\(label)"])
            let bootoutOutcome: LoginAgentBootoutOutcome
            switch bootout {
            case .exited(0): bootoutOutcome = .unloaded
            // launchctl returns ESRCH (3) when the exact service target is absent. Only that known
            // absence may proceed; every other non-zero status could be permission or I/O failure.
            case .exited(jobAbsentExitStatus): bootoutOutcome = .alreadyAbsent
            case .exited(let status): return .bootoutFailed(status: status)
            case .spawnFailed: return .spawnFailed(stage: .bootout)
            case .signaled(let signal):
                coordinator.quarantine()
                return .signaled(stage: .bootout, signal: signal)
            case .timedOut(let confirmed):
                // A timeout cannot establish whether launchd accepted the request, even when the
                // client process later exits. Quarantine this in-process coordinator so no later
                // refresh or Settings toggle can be reported as completing after unknown state.
                coordinator.quarantine()
                return .uncertain(stage: .bootout, terminationConfirmed: confirmed)
            }
            switch runner(["bootstrap", "gui/\(getuid())", plistURL.path]) {
            case .exited(0): return .registrationAccepted(bootout: bootoutOutcome)
            case .exited(let status): return .bootstrapFailed(status: status)
            case .spawnFailed: return .spawnFailed(stage: .bootstrap)
            case .signaled(let signal):
                coordinator.quarantine()
                return .signaled(stage: .bootstrap, signal: signal)
            case .timedOut(let confirmed):
                coordinator.quarantine()
                return .uncertain(stage: .bootstrap, terminationConfirmed: confirmed)
            }
        }) else { return .quarantined }
        return outcome
    }

    /// Run one launchctl command with no inherited input/output. It gets five seconds to finish,
    /// then at most 1.25 seconds to confirm termination. A spawn failure returns immediately;
    /// waiting for a process that never started would raise an exception and conceal the outcome.
    static func run(_ arguments: [String]) -> LaunchctlCommandResult {
        let process = Process()
        let finished = DispatchSemaphore(value: 0)
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { _ in finished.signal() }
        return run(process: LaunchctlProcessLifecycle(
            start: { try process.run() },
            wait: { finished.wait(timeout: .now() + $0) == .success },
            terminate: { process.terminate() },
            forceKill: {
                let result = kill(process.processIdentifier, SIGKILL)
                return result == 0 || errno == ESRCH
            },
            status: { process.terminationStatus },
            terminationReason: { process.terminationReason }))
    }

    static func run(process: LaunchctlProcessLifecycle) -> LaunchctlCommandResult {
        do { try process.start() }
        catch { return .spawnFailed }
        guard !process.wait(5) else {
            let status = process.status()
            switch process.terminationReason() {
            case .exit: return .exited(status)
            case .uncaughtSignal: return .signaled(status)
            @unknown default: return .signaled(status)
            }
        }
        process.terminate()
        if process.wait(0.25) { return .timedOut(terminationConfirmed: true) }
        guard process.forceKill() else { return .timedOut(terminationConfirmed: false) }
        return .timedOut(terminationConfirmed: process.wait(1))
    }
}

/// The side effects the "Start at login" toggle performs, injected so the contract suite can assert
/// exactly which launchctl calls happen and, just as importantly, which do not.
struct LoginAgentOperations {
    var write: () throws -> Void
    var bootout: () -> LaunchctlCommandResult
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
    /// Disabling boots the loaded job out before removing the plist. A failed or uncertain unload
    /// keeps the plist and reports failure instead of claiming the registration is disabled.
    static func apply(enabled: Bool, using operations: LoginAgentOperations,
                      coordinator: LoginAgentMutationCoordinator = .shared) throws {
        let applied: Void? = try coordinator.perform {
            if enabled {
                try operations.write()
                return
            }
            switch operations.bootout() {
            case .exited(0), .exited(LaunchAgent.jobAbsentExitStatus):
                try operations.remove()
            case .exited(let status):
                throw ShellFailure(message: "Could not turn off login startup: bootout failed with status \(status). The login agent was kept.")
            case .spawnFailed:
                throw ShellFailure(message: "Could not turn off login startup because launchctl could not start. The login agent was kept.")
            case .signaled(let signal):
                coordinator.quarantine()
                throw ShellFailure(message: "Could not confirm login startup was unloaded because launchctl ended from signal \(signal); its registration outcome is uncertain. The login agent was kept. Reopen Hub Studio KH before trying again.")
            case .timedOut(let confirmed):
                coordinator.quarantine()
                let settlement = confirmed ? "launchctl ended" : "launchctl did not settle"
                throw ShellFailure(message: "Could not confirm login startup was unloaded (\(settlement)); its registration outcome is uncertain. The login agent was kept. Reopen Hub Studio KH before trying again.")
            }
        }
        // ponytail: quarantine lasts for this app process; a later normal app startup gets a fresh
        // coordinator after launchd/OS settlement. Add persistent reconciliation only if field
        // evidence shows uncertainty survives that boundary.
        guard applied != nil else {
            throw ShellFailure(message: "Could not safely change login startup because a prior launchctl command has an uncertain outcome. Reopen Hub Studio KH before trying again.")
        }
    }
}

enum LoginAgentSupervision {
    static func refreshIfEligible(
        agentLaunch: Bool, hasCustomDataDirectory: Bool, ownsInstanceLock: Bool,
        plistURL: URL, label: String = LaunchAgent.label,
        coordinator: LoginAgentMutationCoordinator = .shared,
        runner: LaunchctlRunner = LaunchAgent.run
    ) -> LoginAgentRefreshOutcome {
        guard !agentLaunch, !hasCustomDataDirectory, ownsInstanceLock else { return .skipped }
        return LaunchAgent.refreshRegistration(at: plistURL, label: label,
                                               coordinator: coordinator, runner: runner)
    }
}

/// Why the app could not take its data-folder lock. The distinction matters: "already held" means
/// another copy of Hub Studio KH is the real app, while "unavailable" is a real failure to open.
enum InstanceLockError: LocalizedError, Equatable {
    case alreadyHeld
    case unavailable(String)
    var errorDescription: String? {
        switch self {
        case .alreadyHeld:
            return "Hub Studio KH is already using this data folder. Open the running app from its menu-bar icon."
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

/// The one menu-bar owner on this Mac, independent of bundle id, path and data folder.
///
/// `InstanceLock` only stops copies that share a data folder, so an SSD copy, an older build or a
/// copy started with another `--data-dir` each took its own lock and drew its own status item — the
/// owner saw a second, third and fourth Hub menu (report, 2026-11-13). This lock lives at one fixed
/// path, so every copy on the Mac contends for it. A waiter is given `wait` seconds for the previous
/// owner to exit — the Launcher restarts Hub without asking, and the new copy must not start drawing
/// before the old one is gone — then gives up and exits silently.
enum MenuBarLock {
    private static var heldDescriptor: Int32?

    static func defaultURL(home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> URL {
        home.appendingPathComponent("Library/Application Support/StudioHub Standalone/menubar.lock")
    }

    /// Take the menu for this process. False means another copy is genuinely running.
    static func acquire(at url: URL = defaultURL(), wait: TimeInterval = 10) -> Bool {
        guard heldDescriptor == nil else { return true }
        let directory = url.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
        let descriptor = open(url.path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { return false }
        let deadline = Date().addingTimeInterval(wait)
        while flock(descriptor, LOCK_EX | LOCK_NB) != 0 {
            if Date() >= deadline { close(descriptor); return false }
            usleep(100_000)
        }
        heldDescriptor = descriptor
        return true
    }

    /// Release the menu (process exit would release it too; the tests use this).
    static func release() {
        guard let descriptor = heldDescriptor else { return }
        flock(descriptor, LOCK_UN)
        close(descriptor)
        heldDescriptor = nil
    }
}

/// What a redundant launch does when another process already owns the data folder.
///
/// This is the second copy launchd can start (for example, a previously loaded agent) while the
/// real app is running. The new process must never show the "Hub Studio KH could not open" alert: it
/// brings the running app forward if it can and leaves with status 0, which `KeepAlive` treats as a
/// clean exit and never relaunches. The present/exit actions are injected so the contract suite can
/// prove the behaviour without AppKit or terminating the test process.
/// What an uninstall does, as data, so its order and refusals are testable without touching a Mac.
///
/// Owner rule 2026-11-12: deleting the app must leave no worker, no launchd job and no login item,
/// and must keep the operator's data. The data directory is deliberately absent from every step.
enum Uninstall {
    enum Step: Equatable {
        case bootoutAgent
        case removeAgentPlist
        case unregisterLegacyItem
        case trashAppBundle
    }

    enum Decision: Equatable {
        case proceed([Step])
        case refuse(String)
    }

    static func decide(appBundle: URL, agentInstalled: Bool, runningCopyDetected: Bool,
                       applicationsRoot: URL = LaunchAgent.defaultApplicationsRoot) -> Decision {
        guard !runningCopyDetected else {
            return .refuse("Quit Hub Studio KH first: the app cannot remove itself while another copy is running.")
        }
        let root = applicationsRoot.standardizedFileURL.resolvingSymlinksInPath().path
        let bundle = appBundle.standardizedFileURL.resolvingSymlinksInPath().path
        guard bundle.hasPrefix(root + "/") else {
            return .refuse("Only a copy installed in \(root) can uninstall itself; this one is at \(bundle).")
        }
        var steps: [Step] = []
        if agentInstalled { steps.append(.bootoutAgent); steps.append(.removeAgentPlist) }
        steps.append(.unregisterLegacyItem)
        steps.append(.trashAppBundle)
        return .proceed(steps)
    }
}

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

/// Why the previous run of Hub Studio KH ended, as this Mac can tell (owner decision 2026-10-03).
///
/// Only a crash brings the app back. A Force Quit is deliberate - often the answer to macOS's
/// "out of application memory" dialog - and a memory kill is macOS making room; relaunching either
/// reloads the models and recreates the pressure. The app cannot see its own exit status, so it
/// leaves evidence while it runs: a `running` file it touches every few seconds (its start time
/// inside), a `crashed` file written from a handler for the crash signals, a `terminated` file for
/// SIGTERM, a `memory-critical` file stamped when macOS reports critical memory pressure, and the
/// `clean-quit` file a deliberate Quit writes. SIGKILL (Force Quit, macOS) cannot be caught, so it
/// is what is left: no clean marker and no crash evidence. A crash report in
/// `~/Library/Logs/DiagnosticReports` backs up the signal handler (a stack overflow cannot run it).
/// The verdict is written to `last-stop.json`, which the Hub publishes as `last_stop`.
enum StopReason: String, Equatable {
    case quit, crashed
    case forceQuit = "force_quit"
    case lowMemory = "low_memory"
    case macRestarted = "mac_restarted"
    /// The services were running but had stopped answering, so the app ended them to start a fresh
    /// set. `classify` never returns it - the app itself did not stop - and it is written by
    /// `recordSilentServices` instead, so the fleet's `last_stop` says "stopped answering" rather
    /// than showing a Mac that is merely offline.
    case stoppedAnswering = "stopped_answering"
    /// The services exited on their own (a crash, or a kill such as a memory kill) while the app
    /// stayed up. Written by `recordSilentServices` with the exit code or signal in the label: the
    /// app never ends here, so without it `last_stop` kept an older app stop (1200, 2026-11-16).
    case servicesExited = "services_exited"

    /// Plain English without a time; the Gateway adds the time in its own time zone.
    func label(relaunched: Bool) -> String {
        switch self {
        case .quit: return "Quit"
        case .crashed: return relaunched ? "Crashed (restarted)" : "Crashed"
        case .forceQuit: return "Force-quit"
        case .lowMemory: return "Stopped for low memory"
        case .macRestarted: return "Mac restarted, shut down or lost power"
        case .stoppedAnswering: return "Local services stopped answering"
        case .servicesExited: return "Local services exited"
        }
    }
}

enum StopRecord {
    static let fileName = "last-stop.json"
    static let runningMarkerName = "running"
    static let crashedMarkerName = "crashed"
    static let terminatedMarkerName = "terminated"
    static let memoryMarkerName = "memory-critical"
    /// Written when macOS announces a restart, shutdown or logout
    /// (`NSWorkspace.willPowerOffNotification`), so it does not read as the owner quitting.
    static let powerOffMarkerName = "power-off"
    static let crashSignals: [Int32] = [SIGSEGV, SIGBUS, SIGILL, SIGABRT, SIGFPE, SIGTRAP, SIGSYS]
    /// How often the running app touches its `running` file, which dates a kill nothing records.
    static let heartbeatSeconds: TimeInterval = 15
    /// Critical memory pressure this close before the end makes an uncaught kill a low-memory stop.
    static let lowMemoryWindow: TimeInterval = 600
    static let defaultCrashReports = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Library/Logs/DiagnosticReports")

    /// The decision, pure. `stoppedAt` is when the run ended as far as the evidence dates it.
    static func classify(cleanQuit: Bool, crashed: Bool, terminated: Bool,
                         stoppedAt: Date, bootTime: Date, memoryCriticalAt: Date?,
                         poweringOff: Bool = false) -> StopReason {
        if crashed { return .crashed }
        if cleanQuit { return poweringOff ? .macRestarted : .quit }
        if poweringOff { return .macRestarted }
        if terminated { return .forceQuit }
        if bootTime > stoppedAt { return .macRestarted }
        if let pressure = memoryCriticalAt, stoppedAt.timeIntervalSince(pressure) < lowMemoryWindow {
            return .lowMemory
        }
        return .forceQuit
    }

    /// Only a crash brings the app back.
    static func relaunches(_ reason: StopReason) -> Bool { reason == .crashed }

    static func bootTime() -> Date {
        var time = timeval()
        var size = MemoryLayout<timeval>.stride
        var mib: [Int32] = [CTL_KERN, KERN_BOOTTIME]
        guard sysctl(&mib, 2, &time, &size, nil, 0) == 0 else { return .distantPast }
        return Date(timeIntervalSince1970: TimeInterval(time.tv_sec))
    }

    private static func modified(_ url: URL) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }

    /// The newest crash report of this app written at or after `since`.
    static func crashReport(since: Date, in directory: URL = defaultCrashReports,
                            process: String = "StudioHub") -> Date? {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.filter { $0.hasPrefix(process + "-") && $0.hasSuffix(".ips") }
            .compactMap { modified(directory.appendingPathComponent($0)) }
            .filter { $0 >= since }.max()
    }

    /// Read the evidence the previous run left, write `last-stop.json`, clear the evidence, and
    /// answer the record - or nil when there is nothing to settle (it was settled already).
    /// Call only while holding, or just after taking, the data-folder lock. `waitForCrashReport`
    /// covers the seconds ReportCrash takes when the crash handler could not run.
    @discardableResult
    static func settle(directory: URL, now: Date = Date(), relaunching: Bool,
                       crashReports: URL = defaultCrashReports, waitForCrashReport: TimeInterval = 0,
                       boot: Date = StopRecord.bootTime(), standbySince: Date? = nil) -> [String: Any]? {
        func marker(_ name: String) -> URL { directory.appendingPathComponent(name) }
        let running = marker(runningMarkerName)
        let cleanQuitAt = modified(marker(AgentStandby.cleanQuitMarkerName))
        let crashedAt = modified(marker(crashedMarkerName))
        let terminatedAt = modified(marker(terminatedMarkerName))
        let lastAlive = modified(running)
        guard lastAlive != nil || cleanQuitAt != nil || crashedAt != nil || terminatedAt != nil
                || standbySince != nil else { return nil }
        // A standby saw the end happen, so `now` dates it; otherwise the last heartbeat does.
        let alive = standbySince != nil ? now : (lastAlive ?? now)
        let started = (try? String(contentsOf: running, encoding: .utf8))
            .flatMap { TimeInterval($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
            .map { Date(timeIntervalSince1970: $0) } ?? standbySince ?? alive.addingTimeInterval(-86_400)
        var reportAt = crashReport(since: started, in: crashReports)
        // Only for an end that just happened: a login launch after a power cut must not wait.
        if crashedAt == nil, cleanQuitAt == nil, terminatedAt == nil, reportAt == nil, waitForCrashReport > 0,
           now.timeIntervalSince(alive) < AgentStandby.relaunchWindow {
            let until = Date().addingTimeInterval(waitForCrashReport)
            while reportAt == nil, Date() < until {
                Thread.sleep(forTimeInterval: 1)
                reportAt = crashReport(since: started, in: crashReports)
            }
        }
        let stoppedAt = cleanQuitAt ?? crashedAt ?? terminatedAt ?? reportAt.map { min($0, alive) } ?? alive
        let reason = classify(cleanQuit: cleanQuitAt != nil, crashed: crashedAt != nil || reportAt != nil,
                              terminated: terminatedAt != nil, stoppedAt: stoppedAt, bootTime: boot,
                              memoryCriticalAt: modified(marker(memoryMarkerName)),
                              poweringOff: modified(marker(powerOffMarkerName)) != nil)
        let relaunched = relaunching && relaunches(reason)
        let record: [String: Any] = ["reason": reason.rawValue, "at": stoppedAt.timeIntervalSince1970,
                                     "label": reason.label(relaunched: relaunched), "relaunched": relaunched]
        if let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) {
            try? data.write(to: marker(fileName), options: .atomic)
        }
        for name in [runningMarkerName, crashedMarkerName, terminatedMarkerName, memoryMarkerName,
                     powerOffMarkerName, AgentStandby.cleanQuitMarkerName] {
            unlink(marker(name).path)
        }
        return record
    }

    /// The app's own record of services that had stopped answering, published as `last_stop`.
    ///
    /// `settle` answers why the *app* ended; this says why the services did, which is the fact a
    /// controller needs when a Mac goes quiet for no reason it can see - the owner's decision of
    /// 2026-10-03 was "Force-quit at 09:36" on the Fleet page instead of a bare "offline", and a
    /// hung Hub is the same kind of unexplained outage. The app stays up to restart them, so this
    /// is not an app stop; the next `settle` overwrites it, because that is the newer fact.
    static func recordSilentServices(directory: URL, now: Date = Date(),
                                     reason: StopReason = .stoppedAnswering, how: String? = nil) {
        let label = reason.label(relaunched: false) + (how.map { " (\($0))" } ?? "")
        let record: [String: Any] = ["reason": reason.rawValue, "at": now.timeIntervalSince1970,
                                     "label": label, "relaunched": false]
        guard let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
        else { return }
        try? data.write(to: directory.appendingPathComponent(fileName), options: .atomic)
    }

    /// Start of a run: the evidence a later `settle` reads. The start time is the file's content,
    /// the heartbeat its modification time.
    static func markRunning(directory: URL, now: Date = Date()) {
        try? Data(String(now.timeIntervalSince1970).utf8)
            .write(to: directory.appendingPathComponent(runningMarkerName), options: .atomic)
    }

    static func heartbeat(directory: URL) {
        utimes(directory.appendingPathComponent(runningMarkerName).path, nil)
    }

    static func noteCriticalMemory(directory: URL) { touch(directory.appendingPathComponent(memoryMarkerName)) }
    static func notePowerOff(directory: URL) { touch(directory.appendingPathComponent(powerOffMarkerName)) }
    private static func touch(_ url: URL) {
        let path = url.path
        if utimes(path, nil) != 0 {
            FileManager.default.createFile(atPath: path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
    }

    /// Mark a crash signal or SIGTERM in the data folder, then die of it as before. The handler
    /// only calls open/write/close/signal/raise, which are async-signal-safe.
    static func installSignalMarkers(directory: URL) {
        stopMarkerCrashedPath = strdup(directory.appendingPathComponent(crashedMarkerName).path)
        stopMarkerTerminatedPath = strdup(directory.appendingPathComponent(terminatedMarkerName).path)
        for number in crashSignals {
            signal(number) { received in
                writeStopMarker(stopMarkerCrashedPath, received)
                signal(received, SIG_DFL); raise(received)
            }
        }
        signal(SIGTERM) { received in
            writeStopMarker(stopMarkerTerminatedPath, received)
            signal(received, SIG_DFL); raise(received)
        }
    }
}

private var stopMarkerCrashedPath: UnsafeMutablePointer<CChar>?
private var stopMarkerTerminatedPath: UnsafeMutablePointer<CChar>?
private func writeStopMarker(_ path: UnsafeMutablePointer<CChar>?, _ number: Int32) {
    guard let path else { return }
    let descriptor = open(path, O_CREAT | O_WRONLY | O_TRUNC, 0o600)
    guard descriptor >= 0 else { return }
    var byte = UInt8(truncatingIfNeeded: number)
    _ = write(descriptor, &byte, 1)
    close(descriptor)
}

/// What makes the launch agent supervise a copy of Hub Studio KH that launchd did not start.
///
/// `KeepAlive` only relaunches the process launchd itself spawned. An app opened from Finder, or
/// reopened by an update, was supervised by nothing, and the agent written after login was not
/// even loaded until the next login - so on 2026-10-03 a Force Quit left the owner's controller
/// without a Hub for 24 minutes. Now such a launch loads/starts the agent, and the agent's copy
/// finds the data folder taken and stands by: it blocks on the same lock, before any AppKit
/// setup, until the running copy ends. It then settles why the app ended (`StopRecord`) and exits
/// 1 - so launchd starts a fresh copy from the bundle on disk (never the standby's own, possibly
/// replaced, image) - only after a crash. A Quit, a Force Quit, a memory kill or anything it
/// cannot prove was a crash exits 0, and `KeepAlive` leaves the app stopped.
enum AgentStandby {
    static let cleanQuitMarkerName = "clean-quit"
    /// How long a launchd-started copy treats the previous run's end as the reason it was started.
    static let relaunchWindow: TimeInterval = 120
    static let crashReportWait: TimeInterval = 15

    static func exitStatus(for reason: StopReason) -> Int32 { StopRecord.relaunches(reason) ? 1 : 0 }

    /// nil when the data folder is free (this process is the app); otherwise blocks until the
    /// copy holding it ends and answers the status this process should exit with.
    static func waitForRunningCopy(directory: URL, crashReports: URL = StopRecord.defaultCrashReports,
                                   crashReportWait: TimeInterval = AgentStandby.crashReportWait) -> Int32? {
        let descriptor = open(directory.appendingPathComponent("app.lock").path,
                              O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { return nil }
        defer { close(descriptor) }
        if flock(descriptor, LOCK_EX | LOCK_NB) == 0 { return nil }
        let waitingSince = Date()
        while flock(descriptor, LOCK_EX) != 0 && errno == EINTR {}
        let record = StopRecord.settle(directory: directory, relaunching: true, crashReports: crashReports,
                                       waitForCrashReport: crashReportWait, standbySince: waitingSince)
        return exitStatus(for: (record?["reason"] as? String).flatMap(StopReason.init) ?? .forceQuit)
    }

    /// For a copy launchd started while nothing held the data folder: a login launch, or
    /// `KeepAlive` relaunching an app it supervised directly. True when the previous run ended
    /// moments ago in a way that must not bring the app back (anything but a crash), so this copy
    /// exits 0 at once - before any AppKit setup - and `KeepAlive` leaves the app stopped. Only a
    /// kill counts: a Quit already exited 0 (launchd did not relaunch it), so a Quit just before a
    /// quick logout and login must not stop the login launch.
    static func shouldStayStopped(directory: URL, now: Date = Date(),
                                  crashReports: URL = StopRecord.defaultCrashReports,
                                  crashReportWait: TimeInterval = AgentStandby.crashReportWait) -> Bool {
        guard let record = StopRecord.settle(directory: directory, now: now, relaunching: true,
                                             crashReports: crashReports, waitForCrashReport: crashReportWait),
              let reason = (record["reason"] as? String).flatMap(StopReason.init),
              let at = record["at"] as? TimeInterval else { return false }
        return (reason == .forceQuit || reason == .lowMemory) && now.timeIntervalSince1970 - at < relaunchWindow
    }

    /// Written by a deliberate Quit while the lock is still held; cleared by the next app start.
    static func markCleanQuit(directory: URL) {
        FileManager.default.createFile(atPath: directory.appendingPathComponent(cleanQuitMarkerName).path,
                                       contents: nil, attributes: [.posixPermissions: 0o600])
    }

}
