import Foundation

enum RecoveryAction: Equatable {
    case wait(TimeInterval)
    case stop(String)
}

/// Decisions for the one app-owned service set. It never adopts another app's process.
struct StartupRecovery {
    static let defaultMaxAutomaticAttempts = 3
    static let defaultDelays: [TimeInterval] = [1, 3, 10]
    static let healthyResetInterval: TimeInterval = 60

    static func validPolicy(maxAttempts: Int, delays: [TimeInterval]) -> Bool {
        (0...10).contains(maxAttempts) && !delays.isEmpty && delays.count <= 10
            && delays.allSatisfy { (0...300).contains($0) }
    }

    static func nextAction(attempt: Int, libraryAvailable: Bool, ownerRunning: Bool,
                           userStopped: Bool, maxAttempts: Int = defaultMaxAutomaticAttempts,
                           delays: [TimeInterval] = defaultDelays) -> RecoveryAction {
        if userStopped { return .stop("Local services stopped") }
        if !libraryAvailable { return .stop("The selected model library is unavailable") }
        if ownerRunning { return .wait(0) }
        guard attempt < maxAttempts else {
            return .stop("Automatic recovery stopped after repeated service failures")
        }
        let safeDelays = validPolicy(maxAttempts: maxAttempts, delays: delays)
            ? delays : defaultDelays
        return .wait(safeDelays[min(attempt, safeDelays.count - 1)])
    }

    static func attemptsAfterHealthyUptime(_ attempts: Int, readyAt: Date?, now: Date = Date()) -> Int {
        guard let readyAt, now.timeIntervalSince(readyAt) >= healthyResetInterval else { return attempts }
        return 0
    }
}
