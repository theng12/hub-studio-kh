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
            + "Re-admitting it is a decision on that controller: open its Connections page, "
            + "use Re-admit, then join again from here. This Mac has stopped trying to re-join "
            + "and no longer holds the group's credential."
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

    /// What an operator has to do about machines a rotation or handover could not reach.
    ///
    /// Named rather than implied: `manual` and `unreachable` are both "this Mac did not follow",
    /// and neither can be fixed from this screen, so the sentence has to say where the fix lives.
    static let handoverFallback = "Any Mac that did not follow has to be moved by hand: SSH over "
        + "Tailscale, following gateway-kh/repository/docs/credential-rotation-decision.md."

    /// The lead sentence of a rotation or handover result, or nil when every Mac followed.
    static func unfinishedNotice(total: Int, verified: Int, manual: Int, pending: Int) -> String? {
        guard manual > 0 || pending > 0 else { return nil }
        let followed = "\(verified) of \(total) Mac\(total == 1 ? "" : "s") followed"
        var needs: [String] = []
        if manual > 0 { needs.append("\(manual) refused the old credential") }
        if pending > 0 { needs.append("\(pending) could not be reached") }
        return "\(followed.capitalized); \(needs.joined(separator: " and ")). " + handoverFallback
    }
}
