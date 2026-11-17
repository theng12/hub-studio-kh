import Foundation

enum RecoveryAction: Equatable {
    case wait(TimeInterval)
    case stop(String)
}

/// Decisions for the one app-owned service set. It never adopts another app's process.
struct StartupRecovery {
    static let defaultMaxAutomaticAttempts = 5
    static let defaultDelays: [TimeInterval] = [1, 3, 10, 60, 120]
    /// After the fast ladder the app keeps trying, but slowly. Giving up permanently was wrong
    /// for an unattended Mac: a service that could not start at boot may start an hour later.
    static let slowRetryInterval: TimeInterval = 300
    static let healthyResetInterval: TimeInterval = 60

    static func validPolicy(maxAttempts: Int, delays: [TimeInterval]) -> Bool {
        (0...10).contains(maxAttempts) && !delays.isEmpty && delays.count <= 10
            && delays.allSatisfy { (0...300).contains($0) }
    }

    /// The number of fast-ladder rungs a policy has. Zero attempts disables automatic recovery.
    static func fastLadderCount(maxAttempts: Int, delays: [TimeInterval]) -> Int {
        guard validPolicy(maxAttempts: maxAttempts, delays: delays) else { return defaultDelays.count }
        return max(0, min(maxAttempts, delays.count))
    }

    /// True while the attempt is still inside the fast ladder; after it the slow cadence applies.
    static func isFastPhase(attempt: Int, maxAttempts: Int, delays: [TimeInterval]) -> Bool {
        maxAttempts > 0 && attempt < fastLadderCount(maxAttempts: maxAttempts, delays: delays)
    }

    static func nextAction(attempt: Int, libraryAvailable: Bool, ownerRunning: Bool,
                           userStopped: Bool, maxAttempts: Int = defaultMaxAutomaticAttempts,
                           delays: [TimeInterval] = defaultDelays) -> RecoveryAction {
        if userStopped { return .stop("Local services stopped") }
        if !libraryAvailable { return .stop("The selected model library is unavailable") }
        if ownerRunning { return .wait(0) }
        // Zero attempts is the explicit "do not start this by itself" choice, and the one policy
        // that still stops. Every other policy falls through to the slow cadence instead of giving
        // up, so a service that could not start at boot is started hours later.
        guard maxAttempts > 0 else {
            return .stop("Automatic recovery stopped after repeated service failures")
        }
        let ladder = ServiceStartLadder(maxFastAttempts: maxAttempts, fastDelays: delays)
        return .wait(ladder.delay(forAttempt: attempt))
    }

    static func attemptsAfterHealthyUptime(_ attempts: Int, readyAt: Date?, now: Date = Date()) -> Int {
        guard let readyAt, now.timeIntervalSince(readyAt) >= healthyResetInterval else { return attempts }
        return 0
    }

    /// How long the services may run without answering their health check before the app ends them
    /// so the ladder can start a fresh set.
    ///
    /// Two minutes, because that is the startup path's own bound: a service that has not come up
    /// within two minutes is already declared failed and stopped. A single slow answer never counts
    /// - a Mac under model load may take a while - and neither does a run of them: the clock starts
    /// on the first failed check and is thrown away by the next good one, so only a silence this
    /// long is a silence.
    static let silentServicesRestartAfter: TimeInterval = 120

    /// Whether a service that has stopped answering should be ended now, under this policy.
    ///
    /// `maxAttempts` is the switch `setRecoveryPolicy` sets. Zero attempts means the app never
    /// starts the services by itself, so it never ends them either: such a Mac is left saying it is
    /// not answering - the truth - rather than having its services stopped behind the owner's back.
    static func shouldRestartSilentServices(silentFor: TimeInterval, maxAttempts: Int) -> Bool {
        silentFor >= silentServicesRestartAfter && maxAttempts > 0
    }
}

/// What a launch decides about the services, before anything is spawned.
///
/// The launch itself is the trigger, not a person pressing Start: a Mac with "Start services on
/// startup" on must come back serving after an update, a reboot or a restart, and a Mac whose
/// owner turned the setting off must stay quiet. The decision is here, pure, so the contract
/// suite can drive it without AppKit or a real service.
enum LaunchStartDecision: Equatable {
    /// Start the services now.
    case start
    /// Leave them stopped: the owner asked for it.
    case stayStopped(String)
    /// There is nothing to start yet (no model library chosen).
    case nothingToStart

    /// Whether the services should be started by this launch.
    var shouldStart: Bool { self == .start }
}

/// The one decision a launch makes about the services, and the one reason it may not start them.
///
/// `loginLaunch` is true when launchd started the app at login (the agent's own environment
/// marker) or when macOS opened it as a login item. Every other launch - a person opening the app
/// from Finder or Spotlight, an update's reopen - is the intent to use it, and starts the
/// services.
///
/// The setting "Start services on startup" governs the *login* launch only. It is off by default,
/// so a Mac that is only used in person does not hold its Studios open all night; a fleet node has
/// it turned on for it once (`ShellConfiguration.applyFleetServicesDefault`).
enum LaunchStart {
    /// The message a stopped-by-choice launch carries, so the reason is never silent.
    static let stoppedByChoice = "Start services on startup is off"

    static func decide(loginLaunch: Bool, startServicesOnLaunch: Bool,
                       hasLibrary: Bool, canConfigure: Bool) -> LaunchStartDecision {
        guard hasLibrary else { return .nothingToStart }
        // A copy that does not own the data folder cannot start anything: another Hub Studio KH is
        // the real app, and this copy presents it and exits.
        guard canConfigure else { return .stayStopped("another copy of Hub Studio KH is already running") }
        guard loginLaunch else { return .start }
        return startServicesOnLaunch ? .start : .stayStopped(stoppedByChoice)
    }
}

/// The position of the one app-owned service set in its start ladder.
///
/// The ladder is fixed: 1, 3, 10, 60 and 120 seconds, then a slow retry every five minutes for as
/// long as the app is running. A manual start or restart puts it back on the first rung.
struct ServiceStartLadder: Equatable {
    static let slowRetryInterval: TimeInterval = StartupRecovery.slowRetryInterval
    private(set) var attempt = 0
    var maxFastAttempts: Int
    var fastDelays: [TimeInterval]

    init(maxFastAttempts: Int = StartupRecovery.defaultMaxAutomaticAttempts,
         fastDelays: [TimeInterval] = StartupRecovery.defaultDelays) {
        self.maxFastAttempts = maxFastAttempts
        self.fastDelays = fastDelays
    }
    private var orderedDelays: [TimeInterval] {
        StartupRecovery.validPolicy(maxAttempts: maxFastAttempts, delays: fastDelays)
            ? fastDelays : StartupRecovery.defaultDelays
    }
    private var fastCount: Int {
        maxFastAttempts > 0 ? min(maxFastAttempts, orderedDelays.count) : 0
    }
    var isFastPhase: Bool { attempt < fastCount }
    /// The wait before the attempt at `index`.
    func delay(forAttempt index: Int) -> TimeInterval {
        guard maxFastAttempts > 0 else { return Self.slowRetryInterval }
        guard index < fastCount else { return Self.slowRetryInterval }
        return orderedDelays[min(index, orderedDelays.count - 1)]
    }
    func delay() -> TimeInterval { delay(forAttempt: attempt) }
    mutating func advance() { attempt += 1 }
    mutating func reset() { attempt = 0 }
    mutating func resetAfterHealthyUptime(readyAt: Date?, now: Date) {
        if StartupRecovery.attemptsAfterHealthyUptime(attempt, readyAt: readyAt, now: now) == 0 {
            attempt = 0
        }
    }
}

/// The bounded, backed-off retry a node uses when its controller is not up.
///
/// It is a frequency bound, not an attempt bound: 15, 30, 60, then every 300 seconds for as long
/// as this Mac is still a node and still failing, so a controller that comes up later is found
/// without anyone retyping anything.
enum RejoinBackoff {
    static let delays: [TimeInterval] = [15, 30, 60, 300]
    static let maximumDelay: TimeInterval = 300
    static func delay(afterAttempt attempt: Int,
                      delays: [TimeInterval] = RejoinBackoff.delays) -> TimeInterval {
        guard !delays.isEmpty else { return maximumDelay }
        return delays[min(max(attempt, 0), delays.count - 1)]
    }
}

/// Whether a node keeps trying to re-join, and how long to wait before the next attempt.
struct RejoinRecovery {
    static let authFailureLimit = 3
    enum Action: Equatable { case wait(TimeInterval); case stop }

    static func nextAction(attempt: Int, isNode: Bool, hasMembership: Bool, joined: Bool,
                           delays: [TimeInterval] = RejoinBackoff.delays) -> Action {
        if joined { return .stop }
        guard isNode, hasMembership else { return .stop }
        return .wait(RejoinBackoff.delay(afterAttempt: attempt, delays: delays))
    }

    /// A membership is only re-joinable when it has both a token and somewhere to send it. A
    /// token with an empty address would retry nowhere, which is the silent spin to avoid.
    static func hasRejoinableMembership(address: String, token: String?) -> Bool {
        guard let token, !token.isEmpty else { return false }
        return !address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// A stale or refused credential must not spin silently: after three consecutive 401/403
    /// answers the operator is told once, and the schedule stays on the five-minute cadence.
    static func shouldAnnounceAuthRefusal(consecutive: Int, alreadyAnnounced: Bool) -> Bool {
        consecutive >= authFailureLimit && !alreadyAnnounced
    }
}

/// What this Mac says when its controller has removed it, and when the group moves.
///
/// These sentences are contracts rather than decoration, so they live here instead of inside a
/// view: the removal notice has to name the date the *controller* stated and where re-admitting
/// happens, the handover and rotation results have to name every Mac that did not follow, and the
/// fallback for those is a written runbook. A `View` cannot be driven by the contract suite; this
/// can.
enum FleetCopy {
    /// The sentence a removed Mac shows, from the controller's own refusal.
    ///
    /// Deliberately says three things and no more: who removed it and when (the date the
    /// controller put in its refusal), that re-admitting is a decision on that controller, and
    /// that this Mac has stopped — because the failure this replaces was a Mac that kept
    /// re-joining every five minutes against a decision that had already been made.
    static func removalNotice(date: String?, address: String) -> String {
        let machine = address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "this Mac's controller" : "the controller at \(address)"
        let when = date.map { " on \($0)" } ?? ""
        return "\(machine.prefix(1).uppercased() + machine.dropFirst()) removed this Mac\(when). "
            + "To reconnect, press Reconnect… and paste that controller's Fleet token, the one it "
            + "hands out on its Connections page. Re-admitting it is a decision on that controller. "
            + "This Mac has stopped trying to re-join and no longer holds the group's credential. "
            // Said plainly, because it is what a person wonders next: the work the group had queued
            // for this Mac was dropped with the removal and does not come back, while this Mac's own
            // files were never touched — the Hub does not delete a Mac's work as a side effect.
            + "Work that controller had queued for this Mac was dropped and is not restored; "
            + "files already on this Mac stay here."
    }

    /// What a Mac says when its controller no longer lists it at all (not removed, just gone from
    /// the roster): the group is left, and joining again is one action.
    static func forgottenNotice(address: String) -> String {
        "The controller at \(address) no longer lists this Mac, so this Mac has left the group and "
            + "no longer holds its credential. Join again from Connections if it should be a node."
    }

    /// The date out of the controller's refusal, which reads
    /// `this controller removed this Mac on 2026-09-28; re-admit it from Connections to join again`.
    ///
    /// The date is taken from the controller's own words rather than from the moment this app read
    /// them: it is the day the decision was made, and a Mac that was switched off for a fortnight
    /// must not report the day it next started.
    static func removalDate(in detail: String) -> String? {
        guard let marker = detail.range(of: "removed this Mac on ") else { return nil }
        let rest = detail[marker.upperBound...]
        let date = rest.prefix { $0 != ";" && $0 != "." && !$0.isNewline }
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return date.isEmpty ? nil : date
    }

    /// Whether a refusal is a removal rather than a credential this Mac could replace.
    static func isRemoval(_ detail: String) -> Bool {
        detail.contains("removed this Mac")
    }

    /// Whether a refusal **body** is this controller's removal decision — by its code, not its
    /// sentence (agreed with Gateway - Lead - 001, 2026-11-17). The sentence stays as the fallback
    /// for a controller that predates `IDENTITY_REVOKED`, so a Mac never loses the notice it had.
    static func isRevoked(_ body: [String: Any]?) -> Bool {
        if body?["code"] as? String == "IDENTITY_REVOKED"
            || body?["state"] as? String == "revoked" { return true }
        return isRemoval((body?["detail"] as? String) ?? "")
    }

    /// The day the controller removed this Mac: its own `removed_at` when it sends one, else the
    /// date in its sentence.
    static func removalDate(in body: [String: Any]?) -> String? {
        if let at = body?["removed_at"] as? String,
           !at.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return at }
        return removalDate(in: (body?["detail"] as? String) ?? "")
    }

    /// What an operator has to do about machines a rotation or handover could not reach.
    ///
    /// Named rather than implied: `manual` and `unreachable` are both "this Mac did not follow",
    /// and neither can be fixed from this screen, so the sentence has to say where the fix lives.
    static let handoverFallback = "Any Mac that did not follow has to be moved by hand: SSH over "
        + "Tailscale, following gateway-kh/repository/docs/credential-rotation-decision.md."

    /// What happens to a Mac a Fleet-token rotation could not reach: nothing is lost. The controller
    /// keeps accepting and presenting the previous token for it and gives it the new one when it
    /// answers again.
    static let rotationWaitsNote = "A Mac that could not be reached stays on the previous token "
        + "(accepted for 30 days) and is given the new one automatically when it comes back."

    /// The lead sentence of a rotation or handover result, or nil when every Mac followed.
    /// A rotation whose only unfinished Macs are unreachable ones says they will catch up by
    /// themselves; a handover, or a Mac that refused the old credential, still needs a person.
    static func unfinishedNotice(total: Int, verified: Int, manual: Int, pending: Int,
                                 rotation: Bool = false) -> String? {
        guard manual > 0 || pending > 0 else { return nil }
        let followed = "\(verified) of \(total) Mac\(total == 1 ? "" : "s") followed"
        var needs: [String] = []
        if manual > 0 { needs.append("\(manual) refused the old credential") }
        if pending > 0 { needs.append("\(pending) could not be reached") }
        let advice = rotation && manual == 0 ? rotationWaitsNote : handoverFallback
        return "\(followed.capitalized); \(needs.joined(separator: " and ")). " + advice
    }

    /// "N nodes still on the previous token", from the roster's count, or nil when none are.
    static func previousTokenNotice(count: Int, until: Double?) -> String? {
        guard count > 0 else { return nil }
        let noun = count == 1 ? "node is" : "nodes are"
        var line = "\(count) \(noun) still on the previous Fleet token."
        if let until {
            let date = Date(timeIntervalSince1970: until).formatted(date: .abbreviated, time: .omitted)
            line += " The controller keeps accepting it until \(date) and gives them the new token when they come back."
        }
        return line
    }
}
