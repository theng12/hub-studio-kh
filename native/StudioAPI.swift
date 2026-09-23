import Combine
import AVFoundation
import Foundation
import CryptoKit

struct NativeImageSettings: Codable, Equatable {
    var width: Int
    var height: Int
    var steps: Int
    var guidance: Double
    var seed: Int64
}
struct NativeImageSize: Decodable, Identifiable, Equatable {
    let id: String
    let label: String
    let width: Int
    let height: Int
}
struct NativeImageLimits: Decodable, Equatable {
    let minSteps: Int
    let maxSteps: Int
    let minGuidance: Double
    let maxGuidance: Double
    let minSeed: Int64
    let maxSeed: Int64
}
struct NativeImageConfiguration: Equatable {
    let defaults: NativeImageSettings
    let sizes: [NativeImageSize]
    let limits: NativeImageLimits
    func accepts(_ value: NativeImageSettings) -> Bool {
        sizes.contains { $0.width == value.width && $0.height == value.height }
            && value.steps >= limits.minSteps && value.steps <= limits.maxSteps
            && value.guidance.isFinite && value.guidance >= limits.minGuidance && value.guidance <= limits.maxGuidance
            && value.seed >= limits.minSeed && value.seed <= limits.maxSeed
    }
}

/// The controls ACE-Step 1.5 actually accepts, as the Music worker publishes them.
///
/// The Hub catalogue carries `generation_defaults` and `generation_limits` per model, and the
/// worker's own `validate_request` (components/music/app/backend/generation.py) is the contract
/// behind them: duration 1-600 s, 1-50 steps, guidance 0-30, shift 0-20, seed -1 or a 32-bit
/// unsigned value, and a fixed WAV output shape. Only fields the Hub publishes are modelled, so
/// the form cannot invent a knob the engine does not have.
struct NativeMusicSettings: Codable, Equatable {
    var durationSeconds: Double
    var steps: Int
    var guidanceScale: Double
    var shift: Double
    var seed: Int64
    var instrumental: Bool
    var lyrics: String
    var vocalLanguage: String
}
struct NativeMusicLimits: Equatable {
    let minDurationSeconds: Double
    let maxDurationSeconds: Double
    let minSteps: Int
    let maxSteps: Int
    let minGuidance: Double
    let maxGuidance: Double
    /// The Hub does not publish shift bounds today. The worker's own validation is 0-20, so a
    /// missing bound falls back to that contract rather than to a guess.
    let minShift: Double
    let maxShift: Double
    let minSeed: Int64
    let maxSeed: Int64
}
struct NativeMusicConfiguration: Equatable {
    let defaults: NativeMusicSettings
    let limits: NativeMusicLimits
    func accepts(_ value: NativeMusicSettings) -> Bool {
        value.durationSeconds.isFinite
            && value.durationSeconds >= limits.minDurationSeconds && value.durationSeconds <= limits.maxDurationSeconds
            && value.steps >= limits.minSteps && value.steps <= limits.maxSteps
            && value.guidanceScale.isFinite
            && value.guidanceScale >= limits.minGuidance && value.guidanceScale <= limits.maxGuidance
            && value.shift.isFinite && value.shift >= limits.minShift && value.shift <= limits.maxShift
            && value.seed >= limits.minSeed && value.seed <= limits.maxSeed
            && value.lyrics.count <= 8000 && value.vocalLanguage.count <= 32
    }
}

struct NativeModel: Identifiable, Equatable {
    let id: String
    let repo: String
    let label: String
    let family: String
    let function: StudioFunction
    let cached: Bool
    let supported: Bool
    let supportNote: String
    let sizeGB: Double?
    let capabilities: [String]
    let languages: [String]
    let runtimeReady: Bool
    let supportsWordTimestamps: Bool
    let speakers: [String]
    /// Qwen3-TTS *Base* models clone from a reference clip instead of using preset speakers.
    var requiresReference: Bool = false
    /// Which of the three ways of choosing a voice this model uses. The Audio page is organised by
    /// this, because the three are different jobs with different controls: a saved voice, a picked
    /// speaker, or a written description of the voice.
    var voiceMode: VoiceMode = .cloning
    /// The runtime reports this as "required" for every Base tier: a clone fails closed on a
    /// transcript mismatch, so the app must not submit without one.
    var requiresTranscript: Bool = true
    /// Set when this Mac is below the tier the model is qualified at. It is advice, not a
    /// refusal: the model still runs, and the operator is told what they are trading.
    var memoryWarning: String? = nil
    /// Why this model cannot run here, in the operator's words, or nil when it can.
    var blockReason: String? = nil
    /// The Hub paused this model after repeated terminal failures. Pausing is automatic and is not
    /// the same as the model being broken: it is a circuit breaker, and it stays shut until someone
    /// says the cause is fixed. The app has to show that state, or a paused model looks like a model
    /// that silently does nothing.
    var paused: Bool = false
    var pausedReason: String? = nil
    /// How this model sections a long script and how its speed control is delivered, as published
    /// by the runtime that will actually do the work.
    var longForm: NativeLongFormDelivery? = nil
    /// Which Studio paused it, so the recheck names the exact pause to release.
    var pausedStudio: String? = nil
    /// The output shape a Music model publishes. Read-only facts, shown so the operator knows what
    /// the WAV will be without opening it; absent for every other function.
    var sampleRateHz: Int? = nil
    var channels: Int? = nil
    /// Short marker for a picker, where a whole sentence will not fit.
    var pickerSuffix: String { blockReason == nil ? "" : "  —  not available here" }
    /// A cleaned short name for a chooser. The catalogue label carries a recommendation suffix and
    /// a runtime marker that are useful on the Models page but noise in a picker, so both are
    /// dropped here only - `label` stays verbatim everywhere else.
    var pickerName: String { Self.pickerName(for: label) }
    static func pickerName(for label: String) -> String {
        var name = label
        if let suffix = name.range(of: " — ") {
            name = String(name[name.startIndex..<suffix.lowerBound])
        }
        name = name.replacingOccurrences(of: " (MLX)", with: "")
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? label : trimmed
    }
}

/// The three ways a local voice model can be told what to sound like.
///
/// They are genuinely different: cloning needs a recording plus its words, a preset needs only a
/// name, and design needs a written description. Presenting them in one shared form is what made
/// the Audio page hard to manage - the controls changed underneath the operator depending on the
/// model, with nothing saying why.
enum VoiceMode: String, CaseIterable, Identifiable {
    case cloning, preset, design
    var id: String { rawValue }
    var title: String {
        switch self {
        case .cloning: return "Clone a Voice"
        case .preset: return "Ready-Made Voices"
        case .design: return "Design a Voice"
        }
    }
    /// What fits in a segmented control. The full `title` is shown in the line below the tabs and
    /// read out by VoiceOver, so the label can be short where short is what fits.
    var shortTitle: String {
        switch self {
        case .cloning: return "Clone"
        case .preset: return "Ready-Made"
        case .design: return "Design"
        }
    }
    var symbol: String {
        switch self {
        case .cloning: return "waveform.badge.mic"
        case .preset: return "person.wave.2"
        case .design: return "text.bubble"
        }
    }
    /// One line, in the operator's words, about what this tab does.
    var detail: String {
        switch self {
        case .cloning:
            return "Use a voice you saved, or a clip from disk. The model repeats that voice."
        case .preset:
            return "Voices that come with the model. Pick one and type your script."
        case .design:
            return "Describe a voice in words — 'deep, slow, warm' — and the model creates it."
        }
    }
    /// What to say when no installed model offers this.
    var emptyReason: String {
        switch self {
        case .cloning:
            return "No voice-cloning model is installed. Clone models are the Qwen3-TTS Base tiers, OmniVoice and VoxCPM2."
        case .preset:
            return "This Mac has no model with ready-made voices installed. Qwen3-TTS CustomVoice is the model for this tab."
        case .design:
            return "This Mac has no voice-design model installed. Qwen3-TTS VoiceDesign is the model for this tab."
        }
    }
}

/// One selectable idle-release mode. Thresholds arrive from the Studio that owns them, so
/// the app never hard-codes what "Balanced" means.
struct NativeMemoryMode: Decodable, Identifiable, Equatable {
    let mode: String
    let label: String
    let idleSeconds: Int?
    var id: String { mode }
    var summary: String {
        guard let idleSeconds else { return "Keeps the model loaded until you release it." }
        if idleSeconds == 0 { return "Releases the model as soon as each job finishes." }
        if idleSeconds < 60 { return "Releases after \(idleSeconds) seconds idle." }
        return "Releases after \(idleSeconds / 60) idle minutes."
    }
}

/// What one Studio reports about the model it is holding right now.
struct NativeMemoryPolicy: Decodable, Equatable {
    let mode: String
    /// The Studio's own resolved default, which is chosen from the machine's memory and is
    /// not necessarily the Hub's fleet-wide draft default.
    let defaultMode: String?
    let idleSeconds: Int?
    let options: [NativeMemoryMode]?
    let loadedModels: [[String]]?
    let activeWork: Bool?
    let busy: Bool?
    /// A Studio that releases inside every job has no idle timer to configure.
    let fixed: Bool?
    let releasePolicy: String?
    let machineBytes: Int64?

    var resolvedOptions: [NativeMemoryMode] { options ?? [] }
    var resident: [String] { (loadedModels ?? []).map { $0.joined(separator: " ") } }
    var isFixed: Bool { fixed == true }
}

/// One family of cached model files: a model plus the companions it needs, as one unit.
struct NativeStorageFamily: Identifiable, Equatable {
    let id: String
    let label: String
    let bytes: Int64?
    /// The memory the model declares it needs, and this Mac's memory, when the worker knows both.
    let requiredGB: Int?
    let thisMacGB: Int?
    let runsHere: Bool?
}

/// One row of the Hub's per-machine memory inventory.
struct NativeMemoryStudio: Decodable, Identifiable, Equatable {
    let id: String
    let title: String
    let modality: String
    let machineLabel: String
    let health: String
    /// ready · offline · update_required · error
    let state: String
    let supported: Bool?
    let detail: String?
    let policy: NativeMemoryPolicy?

    var isReady: Bool { state == "ready" }
}

struct NativeMemorySummary: Decodable, Equatable {
    let total: Int
    let ready: Int
    let offline: Int
    let updateRequired: Int
}

struct NativeMemoryInventory: Decodable, Equatable {
    let defaultMode: String
    let options: [NativeMemoryMode]
    let studios: [NativeMemoryStudio]
    let summary: NativeMemorySummary
}

struct NativeMemoryChange: Decodable, Equatable {
    let ok: Bool
    let action: String
    let selected: Int
    let succeeded: Int
    let failed: Int
    let mode: String?
    let results: [NativeMemoryResult]
}

struct NativeMemoryResult: Decodable, Equatable {
    let id: String
    let ok: Bool?
    /// updated · released · fixed · busy · offline · update_required · error
    let result: String?
    let detail: String?
}

private struct MemoryModeBody: Encodable {
    let mode: String
    let studioIds: [String]?
}

/// Truthful identity of the Hub this app is talking to. The Connections screen shows these
/// values instead of a fake checkout or a promised update channel.
struct NativeControllerIdentity: Decodable, Equatable {
    let settings: NativeControllerSettings
    let readiness: NativeControllerReadiness
}

struct NativeControllerSettings: Decodable, Equatable {
    let role: String
    let siteId: String
    let siteName: String
    let controllerId: String
    let machineName: String
    let databaseMode: String
    let databaseConfigured: Bool?
    let globalAuthority: String?
    let migrationStage: String?
    /// "minted" for a Mac running its own group, "adopted" for one that joined somebody else's.
    /// Absent from a Hub older than this field, which is why it is optional.
    let fleetCredentialOrigin: String?
}

struct NativeControllerReadiness: Decodable, Equatable {
    let ready: Bool
    let role: String?
    let siteId: String?
    let controllerId: String?
    let migrationStage: String?
    let sqliteAuthoritative: Bool?
    let globalJobClaiming: Bool?
    let globalAuthority: String?
    let reason: String?
    let telemetryWarning: String?
}

struct NativeVersion: Decodable, Equatable {
    let version: String
    let build: String?
    let source: String

    /// "0.6.39 · 22e3539", or just the version when the build is not reported.
    var display: String {
        guard let build, !build.isEmpty else { return version }
        return "\(version) · \(build)"
    }
}

/// The outcome of a Connection action (join, leave, role change), shown as a banner that stays
/// until dismissed rather than as a line of small print that is easy to miss.
struct ConnectionNotice: Identifiable, Equatable {
    enum Kind: Equatable { case success, failure }
    let id = UUID()
    let kind: Kind
    let text: String
}

/// The Hub's per-device storage policy and the bytes it currently accounts for.
///
/// `protectedBytes` is the part cleanup must never touch: unfinished work, output whose
/// delivery has not been acknowledged, and anything with a reader. Showing it beside the
/// limit is what makes "why did cleanup not free space" answerable.
struct NativeDeviceSettings: Decodable, Equatable {
    let defaults: NativeStorageLimits
    let effective: NativeStorageLimits
}

struct NativeStorageLimits: Decodable, Equatable {
    let maxBytes: Int64
    let retentionDays: Int
    let cleanupEnabled: Bool
    let cleanupIntervalSeconds: Int
    let minimumFreeBytes: Int64?
}

struct NativeStoragePreview: Decodable, Equatable {
    let effective: NativeStorageLimits
    let machines: [NativeMachineUsage]
}

struct NativeMachineUsage: Decodable, Equatable, Identifiable {
    let machine: String
    let usedBytes: Int64
    let protectedBytes: Int64
    let maxBytes: Int64
    let retentionDays: Int
    let enabled: Bool
    let overLimit: Bool
    let freeBytes: Int64?
    var id: String { machine }
}

/// One Mac that has joined this controller, with the state it is actually in.
struct NativeFleetNode: Decodable, Equatable, Identifiable {
    let id: String
    let name: String
    let address: String
    let state: String
    let detail: String?
    let version: String?
    let joinedAt: Double?
    var isReachable: Bool { state == "reachable" }
}

struct NativeFleetInventory: Decodable, Equatable {
    let role: String
    let nodes: [NativeFleetNode]
    let summary: NativeFleetSummary
}

struct NativeFleetSummary: Decodable, Equatable {
    let total: Int
    let reachable: Int
    let unreachable: Int
    let error: Int
}

/// One Mac in the fleet, with everything needed to judge whether to send it work.
///
/// A machine that has not reported in shows what is known - that it is absent - rather than
/// numbers invented to fill the row: disk and memory stay nil, and a machine with no finished
/// job reports no average instead of a zero that would read as "instant".
struct NativeFleetMachine: Decodable, Equatable, Identifiable {
    let machine: String
    let label: String
    let reachable: Bool
    let version: String?
    let health: String
    let detail: String?
    let memoryGb: Double?
    let availableGb: Double?
    let diskFreeGb: Double?
    let diskTotalGb: Double?
    let chip: String?
    let jobs: Int
    let averageSeconds: Double?
    let modalities: [String: Int]

    var id: String { machine }
    var isLocal: Bool { machine == "local" }
}

struct NativeFleetOverview: Decodable, Equatable {
    let machines: [NativeFleetMachine]
    let summary: NativeFleetOverviewSummary
}

struct NativeFleetOverviewSummary: Decodable, Equatable {
    let total: Int
    let reachable: Int
    let unreachable: Int
}

/// The Hub's own generation analytics, read straight from the job ledger.
///
/// Every number here is recorded by the job that produced it — runtime, model, machine and
/// source — so the app shows measured history rather than a local estimate. Absent values stay
/// absent: a model with no timed run reports no average instead of a zero.
struct NativeStatistics: Decodable, Equatable {
    let total: Int
    let byMachine: [String: NativeStatsGroup]
    let byModality: [String: NativeStatsGroup]
    let modelRows: [NativeModelStats]
    let bySource: [String: Int]?
    let availableMachines: [String]?
    let availableModalities: [String]?

    var trackedModels: [NativeModelStats] {
        modelRows.filter { $0.avgS != nil }.sorted { ($0.avgS ?? 0) > ($1.avgS ?? 0) }
    }
}

struct NativeStatsGroup: Decodable, Equatable {
    let count: Int
    let avgS: Double?
    let modalities: [String: Int]?
}

struct NativeModelStats: Decodable, Equatable, Identifiable {
    let model: String
    let count: Int
    let avgS: Double?
    let modality: String?
    var id: String { "\(modality ?? "")|\(model)" }
}

struct NativeJob: Identifiable, Equatable {
    let id: String
    let state: String
    let progress: Double
    let text: String
    let model: String
    let createdAt: Double?
    let outputURL: String?
    let error: String?
    var bytes: Int64? = nil
    var sha256: String? = nil
    var imageSettings: NativeImageSettings? = nil
    var musicSettings: NativeMusicSettings? = nil
    /// Runtime facts the Hub recorded for this result, shown beside it so an operator can see
    /// what they actually got without opening the file.
    var durationSeconds: Double? = nil
    var requestedWidth: Int? = nil
    var requestedHeight: Int? = nil
    /// How long the generation took, or has been running. Separate from `durationSeconds`, which
    /// is the length of the media produced.
    var generationSeconds: Double? = nil
    var runningSeconds: Double? = nil
    /// The studio's own label for the phase it is in ("DiT sampling"), when it reports one.
    var progressStage: String? = nil
    /// Which item in the batch this row is. Single-item app submissions are always 0, but the
    /// Jobs page can show a batch another client submitted, so the discard route is addressed with
    /// the index the Hub actually reported.
    var itemIndex: Int = 0

    /// "· took 52 s", "· running 12 s", or nothing when the Hub has not timed it.
    ///
    /// One function, read by every job row, so the generator pages and the Jobs tab cannot
    /// disagree about how long something took. The wire types are private to this file, so the
    /// fixture pins this derivation rather than the decode, and the plumbing is checked against a
    /// live Hub by `native/render-page.sh`.
    var generationSuffix: String {
        Self.generationSuffix(generationSeconds: generationSeconds, runningSeconds: runningSeconds)
    }

    static func generationSuffix(generationSeconds: Double?, runningSeconds: Double?) -> String {
        if let generationSeconds, generationSeconds > 0 {
            return " · took \(clock(generationSeconds))"
        }
        if let runningSeconds, runningSeconds > 0 {
            return " · running \(clock(runningSeconds))"
        }
        return ""
    }

    /// Minutes and seconds once it is long enough to be worth reading that way.
    static func clock(_ seconds: Double) -> String {
        if seconds < 60 { return String(format: "%.1f s", seconds) }
        let minutes = Int(seconds) / 60
        let rest = Int(seconds.rounded()) % 60
        return rest == 0 ? "\(minutes) min" : "\(minutes) min \(rest) s"
    }

    /// "12.5 s · 1.4 MB · 1024×1024" — only the parts that are known.
    var assetFacts: String {
        var parts: [String] = []
        if let durationSeconds, durationSeconds > 0 {
            parts.append(durationSeconds >= 60
                ? String(format: "%.1f min", durationSeconds / 60)
                : String(format: "%.1f s", durationSeconds))
        }
        if let bytes, bytes > 0 {
            parts.append(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file))
        }
        if let requestedWidth, let requestedHeight {
            parts.append("\(requestedWidth)×\(requestedHeight)")
        }
        return parts.joined(separator: " · ")
    }
}

struct NativeDownload: Identifiable, Equatable {
    let id: String
    let repo: String
    let state: String
    let percent: Double
    let downloadedBytes: Int64
    let totalBytes: Int64
    let error: String?
}

/// One saved reference clip in the voice library.
///
/// The worker owns this store (`components/voice/app/backend/voices.py`) and it is
/// engine-agnostic: one clip works with any cloning engine, and the transcript that
/// accompanies it is what lets a clone align audio to words. The app is a window onto it,
/// never a second copy of the truth.
struct NativeVoice: Decodable, Equatable, Identifiable {
    let id: String
    let name: String
    let language: String
    let gender: String
    let notes: String
    let license: String
    let sourceUrl: String?
    let permissionAcknowledged: Bool
    /// The worker reports only whether a transcript exists on disk; the text itself is
    /// fetched with the voice so an operator can read and correct it.
    let hasTranscript: Bool
    let transcript: String?
    let audioExtension: String
    let durationSeconds: Double?
    let sampleRate: Int?
    let channels: Int?
    let fleetManaged: Bool
    let audioSha256: String?
    let createdAt: Double?
    let audioUrl: String?

    var hasWords: Bool { !(transcript ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    /// A clone cannot align audio without words, so this flag is what decides whether a
    /// saved voice is usable for the Base tiers rather than a hint in a notes field.
    var readyForCloning: Bool { hasTranscript }
    var durationLabel: String {
        guard let durationSeconds, durationSeconds > 0 else { return "—" }
        return durationSeconds >= 60
            ? String(format: "%.1f min", durationSeconds / 60)
            : String(format: "%.1f s", durationSeconds)
    }
    var genderLabel: String {
        switch gender { case "m": return "Male"; case "f": return "Female"; default: return "Neutral" }
    }
    /// The library's licence vocabulary, spelled the way the operator reads it.
    static let licenseOptions: [(id: String, label: String)] = [
        ("self-owned", "Owned by me"),
        ("public-domain", "Public domain"),
        ("permission", "Used with permission"),
        ("unknown", "Unknown"),
    ]
    static let languageOptions = ["en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"]
}

struct NativeTranscript: Codable, Equatable {
    let text: String
    let srt: String
    let vtt: String
    let language: String
    let duration: Double
    let model: String?
    let sourceFilename: String?
    /// The Hub batch this transcript came from. Delivery can only be acknowledged for a
    /// transcript that is still linked to the job the Hub is protecting.
    var batchID: String? = nil
}

enum NativeTranscriptFormat {
    case text, srt, vtt
}

struct NativeTokenStatus: Equatable {
    let isSet: Bool
    let masked: String
}

struct NativeTokenTest: Decodable, Equatable {
    let name: String?
    let type: String?
    /// Hugging Face answers with `orgs`; the app shows them as organisations.
    let organizations: [String]
    enum CodingKeys: String, CodingKey {
        case name, type
        case organizations = "orgs"
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        name = try values.decodeIfPresent(String.self, forKey: .name)
        type = try values.decodeIfPresent(String.self, forKey: .type)
        organizations = try values.decodeIfPresent([String].self, forKey: .organizations) ?? []
    }
}

private enum NativeAPIError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self { case .message(let message): return message }
    }
}

private final class RejectRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

@MainActor
final class PreviewPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published var playing = false
    /// Where playback is now, in seconds, and how long the loaded clip is. Both come from the
    /// player actually holding the audio, never from the job's requested length.
    @Published var position: Double = 0
    @Published var duration: Double = 0
    private var player: AVAudioPlayer?
    private var scratch: URL?
    private var ticker: Timer?

    var hasAudio: Bool { player != nil }
    /// "0:07 / 0:30", for a transport that reads like every other player.
    var timeLabel: String {
        "\(Self.clock(position)) / \(Self.clock(duration))"
    }
    static func clock(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds > 0 else { return "0:00" }
        let total = Int(seconds.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    /// Play fetched audio from a file on disk, not from memory.
    ///
    /// `AVAudioPlayer(data:)` fails on MP3 with `OSStatus error -4` ("The operation couldn't be
    /// completed"), which is the error the operator saw on seven of the ten saved voices - the ones
    /// stored as MP3. It is not the audio: `afinfo` reads those same files perfectly and macOS
    /// plays them in Finder. `AVAudioPlayer` simply cannot identify the format of an MP3 handed to
    /// it as bytes, because there is no filename to go on. WAV carries enough header to work, which
    /// is why only the MP3 voices failed.
    ///
    /// Writing the bytes to a real file first, with the extension the worker reported, gives the
    /// player what it needs. The file is written under the app's own directory, not `/tmp`, and is
    /// removed as soon as playback stops or finishes.
    func play(_ data: Data, extension ext: String, in directory: URL) throws {
        stop()
        let folder = directory.appendingPathComponent("Playback", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let suffix = ext.hasPrefix(".") ? ext : (ext.isEmpty ? ".wav" : "." + ext)
        let file = folder.appendingPathComponent("preview-\(UUID().uuidString)\(suffix)")
        try data.write(to: file, options: [.atomic, .completeFileProtection])
        do {
            let ready = try AVAudioPlayer(contentsOf: file)
            ready.delegate = self
            ready.play()
            player = ready
            scratch = file
            duration = ready.duration
            position = ready.currentTime
            playing = true
            startTicker()
        } catch {
            try? FileManager.default.removeItem(at: file)
            throw error
        }
    }

    /// Stop making sound, but keep the clip loaded so Play can resume where it left off.
    func pause() {
        player?.pause()
        playing = false
        stopTicker()
        if let player { position = player.currentTime }
    }

    func resume() {
        guard let player else { return }
        player.play()
        playing = true
        startTicker()
    }

    /// Move the loaded clip to a point in seconds. Used by the scrub bar.
    func seek(to seconds: Double) {
        guard let player else { return }
        let clamped = min(max(seconds, 0), player.duration)
        player.currentTime = clamped
        position = clamped
    }

    func stop() {
        stopTicker()
        player?.stop()
        player = nil
        playing = false
        position = 0
        duration = 0
        if let scratch { try? FileManager.default.removeItem(at: scratch) }
        scratch = nil
    }

    private func startTicker() {
        stopTicker()
        ticker = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }
    private func stopTicker() {
        ticker?.invalidate()
        ticker = nil
    }
    private func tick() {
        guard let player else { return }
        position = player.currentTime
        if !player.isPlaying && playing {
            // The delegate normally lands first; this is the belt to its braces so a finished
            // clip can never leave the transport stuck on "Pause".
            stop()
        }
    }
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in self?.stop() }
    }
}

@MainActor
final class NativeWorkspaceStore: ObservableObject {
    static let qwenCustomVoice = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
    /// Requested Qwen3-TTS Base (voice-cloning) models. These clone from a reference clip and
    /// its exact transcript instead of using preset speakers.
    /// The model the Audio page opens on. A packaged Base tier, because the owner's requirement
    /// is clone-first and because the preset-speaker default was not in the shipped library at
    /// all - the page opened on a model that could not generate.
    static let qwenBase = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
    static let qwenBaseRepos: Set<String> = [
        "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        // The 4-bit pair is the same cloning contract at roughly half the weight bytes, so it
        // takes the same reference clip and transcript rather than being treated as unproven.
        "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit",
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit",
    ]
    static let whisperTurbo = "mlx-community/whisper-large-v3-turbo"

    @Published private(set) var models: [NativeModel] = []
    @Published private(set) var audioJobs: [NativeJob] = []
    @Published private(set) var imageJobs: [NativeJob] = []
    /// Independent Subtitle studio history, read from the Hub's transcription jobs (durable),
    /// which the packaged Hub now routes to the subtitle worker.
    @Published private(set) var subtitleJobs: [NativeJob] = []
    @Published private(set) var imageConfigurations: [String: NativeImageConfiguration] = [:]
    @Published var imagePrompt = "" { didSet { scheduleSave() } }
    @Published var imageModel = "" { didSet { if imageModel != oldValue { imageSettings = nil }; scheduleSave() } }
    @Published var imageSettings: NativeImageSettings? { didSet { scheduleSave() } }
    /// Per-studio Music history and the last Music draft, mirroring Image. It is the same Hub job
    /// queue, filtered to `modality == "music"`, so no second job subsystem is introduced.
    @Published private(set) var musicJobs: [NativeJob] = []
    @Published private(set) var musicConfigurations: [String: NativeMusicConfiguration] = [:]
    @Published var musicPrompt = "" { didSet { scheduleSave() } }
    @Published var musicModel = "" { didSet { if musicModel != oldValue { musicSettings = nil }; scheduleSave() } }
    @Published var musicSettings: NativeMusicSettings? { didSet { scheduleSave() } }
    /// A Music-only note, shown beside the Music form. A failure still goes to `error`, which is
    /// the banner every workspace shares.
    @Published var musicNote: String?
    @Published private(set) var submittingMusic = false
    var musicConfiguration: NativeMusicConfiguration? { musicConfigurations[musicModel] }
    /// Which Mac new work should run on, as the Hub's `routing` value: nil means the Hub picks,
    /// and `machine:<id>` pins one machine. Kept as the owner's standing choice so a pin set on
    /// the fleet screen applies to every workspace until it is cleared.
    @Published var routingPin: String? { didSet { scheduleSave() } }
    @Published private(set) var submittingImage = false
    var imageConfiguration: NativeImageConfiguration? { imageConfigurations[imageModel] }

    @Published private(set) var downloads: [NativeDownload] = []
    @Published private(set) var audioSpeakers: [String] = []
    /// The saved voices on this Mac, read from the Voice worker's library.
    @Published private(set) var voices: [NativeVoice] = []
    /// Set while a voice is being added, edited or removed, so a button can show it is working
    /// and cannot be pressed twice.
    @Published private(set) var savingVoice = false
    /// The result of the last voice action, shown beside the list.
    @Published var voiceNote: String?
    /// A voice action that failed, shown where the action was taken.
    @Published var voiceError: String?
    @Published var audioText = "" { didSet { scheduleSave() } }
    @Published var audioModel = qwenBase { didSet { scheduleSave() } }
    /// Which of the three voice tabs the operator is on. Kept so reopening the app returns them to
    /// the job they were doing rather than to whichever model happens to sort first.
    @Published var audioMode: VoiceMode = .cloning { didSet { scheduleSave() } }
    /// The written description a voice-design model turns into a voice.
    @Published var audioDesignPrompt = "" { didSet { scheduleSave() } }
    /// Speaking pace, 0.5-2.0. The engine's own pace is 1.0, and the worker clamps anything else.
    @Published var audioSpeed: Double = 1.0 { didSet { scheduleSave() } }
    /// The bounds the audited contract allows. Held here so the slider, the store and anything that
    /// reports the value cannot disagree about the range.
    static let audioSpeedRange: ClosedRange<Double> = 0.5...2.0
    @Published var audioSpeaker = "" { didSet { scheduleSave() } }
    @Published var audioLanguage = "en" { didSet { scheduleSave() } }
    /// Local reference clip for Qwen Base voice cloning. Choosing a new clip or editing the
    /// transcript invalidates the staged asset, because the Hub derives the asset id from both.
    @Published var audioReferencePath: String? { didSet { invalidateStagedReference() } }
    @Published var audioReferenceTranscript = "" { didSet { invalidateStagedReference() } }
    @Published private(set) var audioReferenceAssetId: String?
    /// Timings from the last Transcribe Clip, sent with the reference so the voice engine can
    /// select a bounded window from a reference longer than the model's maximum clip length.
    private var audioReferenceSegments: [[String: Any]] = []
    @Published private(set) var audioReferenceStatus: String?
    @Published private(set) var stagingReference = false
    @Published var transcriptionModel = whisperTurbo { didSet { scheduleSave() } }
    @Published var transcriptionFile: URL? { didSet { scheduleSave() } }
    @Published var transcriptionLanguage = "" { didSet { scheduleSave() } }
    @Published var wordTimestamps = false { didSet { scheduleSave() } }
    @Published var transcriptionResult: NativeTranscript? { didSet { scheduleSave() } }
    @Published private(set) var transcribing = false
    @Published private(set) var submittingAudio = false
    @Published var error: String?
    @Published private(set) var refreshing = false
    @Published private(set) var isReachable = false
    /// Per-machine model memory, read on demand rather than on the two-second poll: it fans
    /// out to every Studio, and nothing on the generation screens depends on it.
    @Published private(set) var memoryInventory: NativeMemoryInventory?
    @Published private(set) var deviceSettings: NativeDeviceSettings?
    @Published private(set) var storageUsage: [NativeMachineUsage] = []
    @Published private(set) var storageNote: String?
    @Published private(set) var savingStorage = false
    /// Measured generation history from the Hub, refreshed on demand.
    @Published private(set) var statistics: NativeStatistics?
    @Published private(set) var statisticsHours: Int?
    /// Result of the last import or verify, so the Models screen can say what happened.
    /// `modelNoteRepo` scopes a per-model result to that model; a nil repo means the note is
    /// about the library as a whole.
    @Published private(set) var modelNote: String?
    @Published private(set) var modelNoteRepo: String?
    @Published private(set) var updatingModels = false
    @Published private(set) var memoryNote: String?
    @Published private(set) var updatingMemory = false
    /// Truthful Hub identity for the Connections screen.
    @Published private(set) var controllerIdentity: NativeControllerIdentity?
    @Published private(set) var hubVersion: NativeVersion?
    @Published private(set) var roleNote: String?
    @Published private(set) var fleetNodes: NativeFleetInventory?
    /// Every Mac in the fleet in one row each, for the fleet screen.
    @Published private(set) var fleetOverview: NativeFleetOverview?
    /// The controller this Mac was last told to join, so the field is not empty afterwards.
    @Published var controllerAddress = ""
    /// Whether the controller this Mac joined is answering, and a sentence saying so. Kept as
    /// state, not as a transient note, so the Connections page always has something to read.
    @Published private(set) var controllerReachable: Bool?
    @Published private(set) var controllerHealthNote: String?
    /// What this Mac needs to re-join its controller by itself: the address it was told, this
    /// Mac's own fleet credential (which, after joining, *is* the controller's), and the networks
    /// it is reachable on. Set by the app from its saved preferences once the Hub is ready.
    struct FleetMembership {
        let controllerAddress: String
        let token: String?
        let boundHosts: [String]
        /// The controller's release key pinned on first read. Part of the membership: it is
        /// restored with it at startup and cleared when this Mac leaves the group.
        var releasePublisherKey: ReleasePublisherKey? = nil
    }
    var storedMembership: FleetMembership?
    private var startupMembershipAttemptID: UUID?
    private var startupRejoinTask: Task<Bool, Never>?
    /// Called once a join succeeds, so the app can save the controller for the next start; called
    /// with nil when this Mac stops being somebody else's node.
    var onControllerJoined: ((String?) -> Void)?
    @Published private(set) var fleetNote: String?
    /// The last thing the Connections page did, kept on screen until it is dismissed.
    ///
    /// A single small grey line was too easy to miss: the owner pressed Join several times
    /// before noticing that a failure had been reported at all, and a join is exactly the
    /// moment a person needs to read the answer. Success and failure both land here.
    @Published private(set) var connectionNotice: ConnectionNotice?

    func dismissConnectionNotice() { connectionNotice = nil }

    // MARK: Remote app update

    /// The controller's release-signing key, pinned on this Mac the first time its identity is
    /// read. Once pinned it is never replaced by a later read: a new key is an explicit act on
    /// this Mac, not something the network can do. Held with the fleet membership and restored
    /// from it at startup.
    @Published private(set) var releasePublisherKey: ReleasePublisherKey?
    /// The release the controller holds, if any. Shown even when it cannot be installed, so the
    /// reason is visible rather than the offer silently vanishing.
    @Published private(set) var offeredRelease: OfferedRelease?
    /// True only when the release is signed by the pinned controller key, differs from this app's
    /// own StudioHubBuild, and has an artifact form this Mac can install.
    @Published private(set) var updateOffered = false
    /// Why an offered release cannot be installed, in a sentence the banner shows verbatim.
    @Published private(set) var releaseRefusal: String?
    /// True while the artifact is downloading, verifying or being swapped in.
    @Published private(set) var installingRelease = false
    /// A one-line progress sentence while `installingRelease` is true.
    @Published private(set) var releaseProgress: String?
    /// The build whose banner the operator dismissed. An offer stays dismissed until a different
    /// build is advertised; nothing installs on a timer.
    @Published private(set) var dismissedReleaseBuild: String?

    var showingUpdateBanner: Bool {
        updateOffered && offeredRelease?.build != dismissedReleaseBuild
    }

    func dismissOfferedRelease() { dismissedReleaseBuild = offeredRelease?.build }

    /// The build this running app was packaged as. Read from the bundle, and writable so the
    /// contract tests can stand in for a packaged app; a plain CLI has no StudioHubBuild.
    var currentAppBuild: String? = Bundle.main.object(forInfoDictionaryKey: "StudioHubBuild") as? String
    /// The app bundle an update replaces.
    var installedAppURL: URL = Bundle.main.bundleURL
    /// The descriptor behind `offeredRelease`, kept for install so verification runs against the
    /// same bytes that were offered rather than a second, possibly different, read.
    private var offeredReleaseDescriptor: ReleaseDescriptor?
    /// Persist or clear the pinned key with this Mac's controller membership.
    var onReleasePublisherKeyChanged: ((ReleasePublisherKey?) -> Void)?
    /// The existing restart path, called once, after a verified swap.
    var onReleaseRestartRequested: (() -> Void)?
    /// Performs the swap of a verified artifact. Injectable so the download-and-verify path can be
    /// driven in tests without writing to /Applications.
    var installVerifiedArtifact: (URL, ReleaseDescriptor) throws -> String = ReleaseInstaller.live.install

    /// Record the outcome of a connection action.
    ///
    /// `fleetNote` stays the plain record of the last fleet result - the native contract tests
    /// read it, and the startup re-join writes it directly - while the banner beside it carries
    /// the severity the UI colours and keeps on screen.
    private func note(_ kind: ConnectionNotice.Kind, _ text: String) {
        connectionNotice = ConnectionNotice(kind: kind, text: text)
        fleetNote = text
    }
    /// A note about a file the app had to set aside at startup, or a part of a refresh that could
    /// not be read. Shown with the other messages rather than failing silently.
    @Published private(set) var startupNote: String?
    @Published private(set) var joiningFleet = false
    @Published private(set) var tokenStatus = NativeTokenStatus(isSet: false, masked: "")

    private let session: URLSession
    private let redirectDelegate: RejectRedirects
    private var baseURL: URL?
    private var hubToken: String?
    private var connectionID: UUID?
    private var dataDirectory: URL?
    /// Job details that are already final, keyed by batch id.
    ///
    /// A finished job cannot change... while nothing can re-open one. The two-second poll asked the
    /// Hub for the detail of *every* batch it has ever recorded - one request per historical job,
    /// every two seconds, for the life of the app - and re-read records that will never differ.
    /// Checked before relying on it: the app has no retry action, and the Hub's `retry_batch`,
    /// `remove_batch` and `clear_terminal` have no route, so no finished job can come back. The
    /// states that *do* come back (`uncertain`, re-queued by the Hub) are not final and are never
    /// cached. If a retry route is ever added, invalidate this cache there -
    /// `tests/test_job_detail_cache_is_safe.py` fails when that route appears.
    /// What the Voice worker has on disk, grouped by family, with the memory verdict per family.
    ///
    /// Models copied in from another install are present on disk and would otherwise look runnable
    /// here; the worker declares a memory floor per model, so this is the honest answer to "can this
    /// Mac run it". Optional: a Studio that does not answer must not disturb the rest of the refresh.
    @Published private(set) var storageFamilies: [NativeStorageFamily] = []

    private var finishedJobCache: [String: NativeJob] = [:]

    private var pollTask: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var loadingState = false
    private var pendingAudio: HubAudioRequest?
    private var pendingImage: HubImageRequest?
    private var pendingMusic: HubMusicRequest?
    private var pendingTranscription: PendingTranscription?
    private var modelPackages: [String: HubModelPackage] = [:]
    private var hubDownloadRuns: [String: String] = [:]

    init(sessionConfiguration: URLSessionConfiguration = .ephemeral) {
        let configuration = sessionConfiguration.copy() as! URLSessionConfiguration
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        redirectDelegate = RejectRedirects()
        session = URLSession(configuration: configuration, delegate: redirectDelegate, delegateQueue: nil)
    }

    func connect(baseURL: URL, dataDirectory: URL, token: String,
                 allowedHost: String = "127.0.0.1") async {
        disconnect()
        do {
            self.baseURL = try Self.validatedBase(baseURL, allowedHost: allowedHost)
            guard !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw NativeAPIError.message("The local Hub credential is unavailable.")
            }
            hubToken = token
            let requestConnection = UUID()
            self.connectionID = requestConnection
            self.dataDirectory = dataDirectory.standardizedFileURL
            try FileManager.default.createDirectory(
                at: dataDirectory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            // Best-effort: these are the app's own drafts and records. One unreadable file used to
            // throw out of `connect()`, which cleared the Hub address and left every page showing
            // the setup screen with no models, jobs or voices - recoverable only by finding and
            // deleting the file in Finder. A file that will not parse is set aside so nothing is
            // lost, and the app connects with empty state.
            loadBestEffort(dataDirectory.appendingPathComponent("native-workspaces.json"), "your saved workspaces") { try loadState() }
            loadBestEffort(dataDirectory.appendingPathComponent("native-pending-audio.json"), "a pending audio request") { try loadPendingAudio() }
            loadBestEffort(dataDirectory.appendingPathComponent("native-pending-image.json"), "a pending image request") { try loadPendingImage() }
            loadBestEffort(dataDirectory.appendingPathComponent("native-pending-music.json"), "a pending music request") { try loadPendingMusic() }
            loadBestEffort(dataDirectory.appendingPathComponent("native-pending-transcription.json"), "a pending transcription") { try loadPendingTranscription() }
            loadBestEffort(dataDirectory.appendingPathComponent("native-model-downloads.json"), "the model-download list") { try loadHubDownloadRuns() }
            await refresh()
            guard self.connectionID == requestConnection else { return }
            await refreshMemoryPolicy()
            guard self.connectionID == requestConnection else { return }
            pollTask = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(2))
                    guard !Task.isCancelled else { break }
                    await self?.refresh(reportErrors: false)
                }
            }
        } catch {
            self.baseURL = nil
            self.hubToken = nil
            self.connectionID = nil
            self.dataDirectory = nil
            self.error = Self.actionable(error, action: "Connect to the local engine")
            self.isReachable = false
        }
    }

    func disconnect() {
        invalidateStartupMembershipAttempt()
        finishedJobCache.removeAll()
        pollTask?.cancel()
        pollTask = nil
        saveTask?.cancel()
        saveTask = nil
        if dataDirectory != nil { try? saveState() }
        baseURL = nil
        hubToken = nil
        connectionID = nil
        isReachable = false
        submittingAudio = false
        submittingImage = false
        submittingMusic = false
        transcribing = false
        memoryInventory = nil
        memoryNote = nil
        updatingMemory = false
        controllerIdentity = nil
        hubVersion = nil
        roleNote = nil
        statistics = nil
        statisticsHours = nil
        deviceSettings = nil
        storageUsage = []
        storageNote = nil
        savingStorage = false
        modelNote = nil
        modelNoteRepo = nil
        updatingModels = false
        offeredRelease = nil
        offeredReleaseDescriptor = nil
        updateOffered = false
        releaseRefusal = nil
        installingRelease = false
        releaseProgress = nil
    }

    func refresh() async { await refresh(reportErrors: true) }

    private func refresh(reportErrors: Bool) async {
        guard baseURL != nil, let requestConnection = connectionID, !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            // The catalog and the package list are the Hub itself: if they fail, the Hub is not
            // answering. Everything else is a studio or a list, and one of those failing used to
            // abort the whole refresh and mark the Hub unreachable - so a dead Subtitle worker
            // blanked the models, the jobs and the connections with no explanation.
            async let catalog: CatalogResponse = get(["api", "hub", "catalog"])
            async let packages: HubModelPackagesResponse = get(["api", "hub", "model-packages"])
            async let transcription: TranscriptionAvailability? = try? get(["studio", "transcription", "api", "transcribe", "availability"])
            async let generation: GenerationAvailability? = try? get(["studio", "voice", "api", "generate", "availability"])
            async let jobs: HubJobsResponse? = try? get(["api", "hub", "jobs"])
            async let transfers: DownloadsResponse? = try? get(["studio", "voice", "api", "downloads"])
            async let settings: TokenStatusWire? = try? get(["studio", "voice", "api", "settings"])
            async let subtitleHistory: HubTranscriptionList? = try? get(["api", "hub", "transcription", "jobs"])
            let (catalogValue, packagesValue, transcriptionValue, generationValue, jobsValue, transfersValue, settingsValue, subtitleValue) =
                try await (catalog, packages, transcription, generation, jobs, transfers, settings, subtitleHistory)
            if transcriptionValue == nil || generationValue == nil {
                startupNote = "A studio did not answer; the rest of this page is up to date."
            }
            guard connectionID == requestConnection else { return }
            // Built by merging, not uniqueKeysWithValues: a repeated id in a Hub payload would
            // trap the app on every refresh and on the two-second poll. Last one wins instead.
            modelPackages = packagesValue.models.reduce(into: [:]) { $0[$1.modelId] = $1 }
            audioSpeakers = (generationValue?.qwen3PresetSpeakers ?? []).map(\.id)
            if audioSpeaker.isEmpty || (!audioSpeakers.isEmpty && !audioSpeakers.contains(audioSpeaker)) {
                audioSpeaker = audioSpeakers.first ?? "Ryan"
            }
            models = Self.combineModels(
                catalogValue.models,
                transcriptionValue?.models ?? [],
                transcriptionAvailable: transcriptionValue?.available ?? false,
                speakers: audioSpeakers,
                packages: modelPackages
            )
            imageConfigurations = catalogValue.models.reduce(into: [:]) { result, model in
                if let configuration = model.imageConfiguration { result[model.repo] = configuration }
            }
            musicConfigurations = catalogValue.models.reduce(into: [:]) { result, model in
                if let configuration = model.musicConfiguration { result[model.repo] = configuration }
            }
            if imageConfigurations[imageModel] == nil,
               let first = models.first(where: { $0.function == .image && $0.supported }) { imageModel = first.repo }
            if musicConfigurations[musicModel] == nil,
               let first = models.first(where: { $0.function == .music && $0.supported }) { musicModel = first.repo }
            if imageSettings == nil { imageSettings = imageConfiguration?.defaults }
            if musicSettings == nil { musicSettings = musicConfiguration?.defaults }
            var history: [NativeJob] = []
            var images: [NativeJob] = []
            var musics: [NativeJob] = []
            var unreadable = 0
            var finished: [String: NativeJob] = [:]
            for batch in (jobsValue?.batches ?? []) where ["voice", "image", "music"].contains(batch.modality) {
                if let known = finishedJobCache[batch.id] {
                    finished[batch.id] = known
                    if batch.modality == "image" { images.append(known) }
                    else if batch.modality == "music" { musics.append(known) }
                    else { history.append(known) }
                    continue
                }
                do {
                    let detail: HubJobDetail = try await get(["api", "hub", "jobs", batch.id])
                    guard connectionID == requestConnection else { return }
                    let job = Self.mapHubJob(detail)
                    if ["done", "cancelled"].contains(job.state) { finished[batch.id] = job }
                    if batch.modality == "image" { images.append(job) }
                    else if batch.modality == "music" { musics.append(job) }
                    else { history.append(job) }
                } catch {
                    // A batch whose detail will not load is one row, not the whole page: the listing
                    // is a convenience over the Hub's history, and losing it should not read as the
                    // Hub being down.
                    unreadable += 1
                }
            }
            if unreadable > 0 {
                startupNote = "\(unreadable) finished job(s) could not be read; the rest are shown."
            }
            // Only what the Hub still lists is remembered; anything it forgets leaves with it.
            finishedJobCache = finished
            audioJobs = history.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            imageJobs = images.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            musicJobs = musics.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            var subtitles: [NativeJob] = []
            for batch in (subtitleValue?.batches ?? []) {
                let batchState = batch.status ?? "unknown"
                subtitles.append(NativeJob(
                    id: batch.id,
                    state: batchState,
                    progress: batchState == "done" ? 1 : 0,
                    text: batch.label ?? batch.episode ?? "",
                    model: batch.model ?? "",
                    createdAt: batch.createdAt,
                    outputURL: nil,
                    error: nil))
            }
            subtitleJobs = subtitles.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            var hubTransfers: [NativeDownload] = []
            for (runID, repo) in hubDownloadRuns {
                // A download that has finished and been forgotten by the Hub is not a reason to
                // stop showing everything else.
                guard let run: HubModelDownload = try? await get(["api", "hub", "model-downloads", runID]) else {
                    hubDownloadRuns.removeValue(forKey: runID)
                    continue
                }
                hubTransfers.append(NativeDownload(id: run.id, repo: repo, state: run.state,
                    percent: 0, downloadedBytes: 0, totalBytes: 0,
                    error: run.state == "error" || run.state == "interrupted" ? "Hub model download \(run.state)." : nil))
            }
            downloads = hubTransfers + (transfersValue?.jobs ?? []).map(Self.mapDownload)
            tokenStatus = settingsValue?.value ?? NativeTokenStatus(isSet: false, masked: "")
            if let storage: HubStorageGroups = try? await get(["studio", "voice", "api", "model-storage"]) {
                guard connectionID == requestConnection else { return }
                storageFamilies = storage.groups.map { group in
                    NativeStorageFamily(id: group.id, label: group.label, bytes: group.bytesTotal,
                                        requiredGB: group.memory?.requiredGb,
                                        thisMacGB: group.memory?.thisMacGb,
                                        runsHere: group.memory?.runsHere)
                }
            }
            reconcileSelections(defaultTranscription: transcriptionValue?.defaultModel)
            await reconcileTranscription()
            isReachable = true
            if reportErrors { error = nil }
            await retryStartupMembershipIfNeeded()
        } catch {
            guard connectionID == requestConnection else { return }
            isReachable = false
            if reportErrors { self.error = Self.actionable(error, action: "Refresh local studio state") }
        }
    }

    /// Stage a locally chosen reference clip through the same Hub contract GenStudio pushes use.
    /// The Hub derives the asset id from the audio plus the transcript, so editing either
    /// invalidates the staged asset and it must be prepared again.
    func stageVoiceReference() async {
        guard !stagingReference, let requestConnection = connectionID else { return }
        do {
            guard let path = audioReferencePath else {
                throw NativeAPIError.message("Choose a reference clip first.")
            }
            let file = URL(fileURLWithPath: path)
            guard FileManager.default.fileExists(atPath: file.path) else {
                throw NativeAPIError.message("The reference clip is no longer at that location. Choose it again.")
            }
            let transcript = audioReferenceTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
            let transcriptRequired = models.first { $0.repo == audioModel && $0.function == .audio }?.requiresTranscript ?? true
            guard !transcriptRequired || !transcript.isEmpty else {
                throw NativeAPIError.message("Enter the exact words spoken in the reference clip.")
            }
            guard let uploadDirectory = dataDirectory else {
                throw NativeAPIError.message("The app data directory is unavailable.")
            }
            stagingReference = true
            defer { if connectionID == requestConnection { stagingReference = false } }
            let boundary = "StudioHub-\(UUID().uuidString)"
            var fields = [
                "transcript": transcript,
                "language": audioLanguage.trimmingCharacters(in: .whitespacesAndNewlines),
            ]
            // Only when the timings still describe these words. The transcript can be edited by
            // hand after transcribing, and the engine picks the reference window from the timings
            // - so sending timings for words the operator has since changed would window the audio
            // against a transcript it does not match. A mismatch simply means no timings, and the
            // engine falls back to its own behaviour.
            //
            // A voice that came with timings - every long clip in the kit does, computed once when
            // the kit was built - is used when there is nothing fresher, so a long reference can be
            // prepared without transcribing it again on each Mac.
            let timings = audioReferenceSegments.isEmpty
                ? Self.shippedTimings(forReference: file) : audioReferenceSegments
            if Self.segments(timings, match: transcript),
               let encoded = try? JSONSerialization.data(withJSONObject: timings),
               let json = String(data: encoded, encoding: .utf8) {
                fields["transcript_segments"] = json
            }
            let uploadFile = try await Task.detached(priority: .userInitiated) {
                try Self.makeMultipartFile(
                    boundary: boundary, file: file, fields: fields,
                    dataDirectory: uploadDirectory, fileField: "audio"
                )
            }.value
            defer { try? FileManager.default.removeItem(at: uploadFile) }
            guard connectionID == requestConnection else { return }
            var request = try makeRequest(["api", "hub", "voice-references"], method: "POST")
            request.timeoutInterval = 300
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            let wire: HubVoiceReference = try await performUpload(request, fromFile: uploadFile)
            guard connectionID == requestConnection else { return }
            audioReferenceAssetId = wire.assetId
            audioReferenceStatus = "Voice ready — \(wire.bytes) bytes, transcript stored."
            error = nil
        } catch {
            guard connectionID == requestConnection else { return }
            audioReferenceAssetId = nil
            audioReferenceStatus = nil
            self.error = Self.actionable(error, action: "Prepare the reference voice")
        }
    }

    private func invalidateStagedReference() {
        if audioReferenceAssetId != nil { audioReferenceAssetId = nil }
        audioReferenceSegments = []
        if audioReferenceStatus != nil { audioReferenceStatus = nil }
    }

    func generateAudio() async {
        guard !submittingAudio, let requestConnection = connectionID else { return }
        do {
            let body: HubAudioRequest
            if let pendingAudio { body = pendingAudio }
            else {
                let model = try usableModel(repo: audioModel, function: .audio)
                let text = audioText.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { throw NativeAPIError.message("Enter a script before generating audio.") }
                guard text.count <= 40_000 else { throw NativeAPIError.message("The script exceeds the 40,000-character limit.") }
                let language = audioLanguage.trimmingCharacters(in: .whitespacesAndNewlines)
                if model.voiceMode == .design {
                    let description = audioDesignPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !description.isEmpty else {
                        throw NativeAPIError.message("Describe the voice you want — for example 'deep, slow and warm'.")
                    }
                    body = HubAudioRequest(
                        modality: "voice", model: model.repo,
                        clientRequestId: "native:\(UUID().uuidString)",
                        items: [HubAudioItem(text: text, params: HubAudioParams(
                            presetSpeaker: "", language: language, speed: audioSpeed,
                            designPrompt: description))],
                        voiceReferenceAssetId: nil, voiceLibraryId: nil, routing: routingPin)
                } else if model.voiceMode == .cloning {
                    if let voiceID = audioVoiceLibraryID {
                        // A voice from the library: the worker reads the clip, its transcript and
                        // its timings from its own store. Nothing is uploaded, and there is no
                        // reference to prepare first.
                        guard let voice = voices.first(where: { $0.id == voiceID }), voice.readyForCloning else {
                            throw NativeAPIError.message("That saved voice is no longer in the voice library. Choose another, or refresh the Voices page.")
                        }
                        body = HubAudioRequest(
                            modality: "voice", model: model.repo,
                            clientRequestId: "native:\(UUID().uuidString)",
                            items: [HubAudioItem(text: text, params: HubAudioParams(
                                presetSpeaker: "", language: language, speed: audioSpeed))],
                            voiceReferenceAssetId: nil, voiceLibraryId: voiceID, routing: routingPin)
                    } else {
                        guard audioReferencePath != nil else {
                            throw NativeAPIError.message("Choose a saved voice, or a reference clip to clone from.")
                        }
                        guard !model.requiresTranscript || !audioReferenceTranscript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                            throw NativeAPIError.message("This Qwen Base voice needs the exact words spoken in the reference clip.")
                        }
                        guard let assetID = audioReferenceAssetId else {
                            throw NativeAPIError.message("Prepare the reference voice before generating.")
                        }
                        body = HubAudioRequest(
                            modality: "voice", model: model.repo,
                            clientRequestId: "native:\(UUID().uuidString)",
                            items: [HubAudioItem(text: text, params: HubAudioParams(
                                presetSpeaker: "", language: language, speed: audioSpeed))],
                            voiceReferenceAssetId: assetID, voiceLibraryId: nil, routing: routingPin)
                    }
                } else {
                    guard audioSpeakers.isEmpty || audioSpeakers.contains(audioSpeaker) else {
                        throw NativeAPIError.message("Choose a speaker reported by the local Qwen runtime.")
                    }
                    body = HubAudioRequest(
                        modality: "voice", model: model.repo,
                        clientRequestId: "native:\(UUID().uuidString)",
                        items: [HubAudioItem(text: text, params: HubAudioParams(
                            presetSpeaker: audioSpeaker, language: language, speed: audioSpeed))],
                        voiceReferenceAssetId: nil, voiceLibraryId: nil, routing: routingPin)
                }
            }
            submittingAudio = true
            defer { if connectionID == requestConnection { submittingAudio = false } }
            if pendingAudio == nil { pendingAudio = body; try savePendingAudio() }
            let response: HubJobSubmit = try await sendHubJSON(["api", "hub", "jobs"], body: body)
            guard connectionID == requestConnection else { return }
            let detail: HubJobDetail = try await get(["api", "hub", "jobs", response.batchId])
            guard connectionID == requestConnection else { return }
            let job = Self.mapHubJob(detail)
            audioJobs.removeAll { $0.id == job.id }
            audioJobs.insert(job, at: 0)
            pendingAudio = nil
            try savePendingAudio()
            error = nil
        } catch {
            guard connectionID == requestConnection else { return }
            self.error = Self.actionable(error, action: "Generate audio")
        }
    }

    func restoreImageDefaults() {
        imageSettings = imageConfiguration?.defaults
    }

    func generateImage() async {
        guard !submittingImage, let requestConnection = connectionID else { return }
        do {
            let body: HubImageRequest
            if let pendingImage { body = pendingImage }
            else {
                let model = try usableModel(repo: imageModel, function: .image)
                let prompt = imagePrompt.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !prompt.isEmpty else { throw NativeAPIError.message("Enter an image prompt.") }
                guard let settings = imageSettings, imageConfiguration?.accepts(settings) == true else {
                    throw NativeAPIError.message("Choose an available image size and settings within the model's supported ranges, or restore its defaults.")
                }
                if let original = imageJobs.first(where: {
                    $0.state == "uncertain" && $0.model == model.repo && $0.text == prompt
                        && ($0.imageSettings == nil || $0.imageSettings == settings)
                }) {
                    throw NativeAPIError.message("Image job \(original.id) has an uncertain result. Reconcile that original job before repeating this request.")
                }
                body = HubImageRequest(modality: "image", model: model.repo,
                    clientRequestId: "native:\(UUID().uuidString)", items: [HubImageItem(prompt: prompt, params: settings)],
                    routing: routingPin)
            }
            submittingImage = true
            defer { if connectionID == requestConnection { submittingImage = false } }
            if pendingImage == nil { pendingImage = body; try savePendingImage() }
            let response: HubJobSubmit = try await sendHubJSON(["api", "hub", "jobs"], body: body)
            guard connectionID == requestConnection else { return }
            let detail: HubJobDetail = try await get(["api", "hub", "jobs", response.batchId])
            guard connectionID == requestConnection else { return }
            let job = Self.mapHubJob(detail)
            imageJobs.removeAll { $0.id == job.id }; imageJobs.insert(job, at: 0)
            if job.state != "uncertain" { pendingImage = nil }
            try savePendingImage()
            error = job.state == "uncertain" ? "The Image result is uncertain. Its original request is preserved for reconciliation." : nil
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Generate an image") }
        }
    }

    func cancelImage(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard imageJobs.contains(where: { $0.id == job.id }) else { throw NativeAPIError.message("That image job is no longer in the current history.") }
            let _: HubCancelResponse = try await sendEmpty(["api", "hub", "jobs", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the image job") } }
    }

    func reconcileImage(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard imageJobs.contains(where: { $0.id == job.id && $0.state == "uncertain" }) else {
                throw NativeAPIError.message("Refresh the original uncertain Image job before checking its result.")
            }
            let result: HubReconciliation = try await sendEmpty(["api", "hub", "jobs", job.id, "items", "0", "reconcile"], method: "POST")
            guard connectionID == requestConnection else { return }
            if result.reconciled && result.clientRequestId == pendingImage?.clientRequestId {
                pendingImage = nil
                try savePendingImage()
            }
            await refresh()
            guard connectionID == requestConnection else { return }
            if !result.reconciled {
                error = "The original Image result is still uncertain. Its request remains protected; no new generation was submitted."
            }
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Check the original Image result") }
        }
    }

    func imageData(for job: NativeJob) async throws -> Data {
        guard let requestConnection = connectionID else { throw NativeAPIError.message("Start the local services before retrieving images.") }
        guard job.state == "done", imageJobs.contains(where: { $0.id == job.id }),
              let bytes = job.bytes, bytes > 0, let sha = job.sha256,
              sha.count == 64, sha.allSatisfy({ $0.isHexDigit }) else {
            throw NativeAPIError.message("This image has no verified completed result. Refresh its job before saving.")
        }
        let data = try await performData(makeRequest(["api", "hub", "jobs", job.id, "items", "0", "artifact"]))
        guard connectionID == requestConnection else { throw NativeAPIError.message("The Hub connection changed before image retrieval completed.") }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard Int64(data.count) == bytes, digest == sha.lowercased(),
              data.starts(with: [137, 80, 78, 71, 13, 10, 26, 10]) else {
            throw NativeAPIError.message("Image verification failed. Refresh the job and retrieve its original result again.")
        }
        return data
    }

    func saveImage(_ job: NativeJob, to destination: URL) async throws {
        let requestConnection = connectionID
        let data = try await imageData(for: job)
        try data.write(to: destination, options: .atomic)
        guard try Data(contentsOf: destination) == data else {
            throw NativeAPIError.message("Saved image verification failed. Choose another destination and save again.")
        }
        // The destination is verified, so delivery may now be acknowledged. Only a
        // verified save reaches the Hub receipt; if acknowledgement fails the managed
        // copy stays protected instead of being treated as delivered.
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The image was saved and verified, but the local service connection changed before delivery was acknowledged. Save again to confirm delivery.")
        }
        do {
            let receipt: HubAckReceipt = try await sendEmpty(
                ["api", "hub", "jobs", job.id, "items", "0", "ack"], method: "POST")
            if !receipt.ok { throw NativeAPIError.message("rejected") }
        } catch {
            throw NativeAPIError.message("The image was saved and verified, but its delivery was not acknowledged. Save again to confirm delivery; the protected copy is not deleted until acknowledgement succeeds.")
        }
    }

    func restoreMusicDefaults() {
        musicSettings = musicConfiguration?.defaults
    }

    /// Submit one Music job through the same durable Hub route Image and Voice use.
    ///
    /// The Hub's `broker.MODALITY` maps `music` to the worker's `/api/generate/txt2music`, so the
    /// envelope is a Music-shaped `HubMusicRequest` on `POST /api/hub/jobs`. The original attempt is
    /// written to disk before the request leaves, exactly as the Image path does: the Music worker
    /// acknowledges a job before the WAV exists, so a lost response must be reconciled rather than
    /// silently resubmitted even though no durable per-item recovery contract exists.
    func generateMusic() async {
        guard !submittingMusic, let requestConnection = connectionID else { return }
        do {
            let body: HubMusicRequest
            if let pendingMusic { body = pendingMusic }
            else {
                let model = try usableModel(repo: musicModel, function: .music)
                let prompt = musicPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !prompt.isEmpty else { throw NativeAPIError.message("Describe the music you want.") }
                guard prompt.count <= 4_000 else { throw NativeAPIError.message("The prompt exceeds the 4,000-character limit.") }
                guard let settings = musicSettings, musicConfiguration?.accepts(settings) == true else {
                    throw NativeAPIError.message("Choose settings within the model's supported ranges, or restore its defaults.")
                }
                if let original = musicJobs.first(where: {
                    $0.state == "uncertain" && $0.model == model.repo && $0.text == prompt
                        && ($0.musicSettings == nil || $0.musicSettings == settings)
                }) {
                    throw NativeAPIError.message("Music job \(original.id) has an uncertain result. Reconcile that original job before repeating this request.")
                }
                body = HubMusicRequest(modality: "music", model: model.repo,
                    clientRequestId: "native:\(UUID().uuidString)", items: [HubMusicItem(prompt: prompt, params: settings)],
                    routing: routingPin)
            }
            submittingMusic = true
            defer { if connectionID == requestConnection { submittingMusic = false } }
            if pendingMusic == nil { pendingMusic = body; try savePendingMusic() }
            let response: HubJobSubmit = try await sendHubJSON(["api", "hub", "jobs"], body: body)
            guard connectionID == requestConnection else { return }
            let detail: HubJobDetail = try await get(["api", "hub", "jobs", response.batchId])
            guard connectionID == requestConnection else { return }
            let job = Self.mapHubJob(detail)
            musicJobs.removeAll { $0.id == job.id }; musicJobs.insert(job, at: 0)
            if job.state != "uncertain" { pendingMusic = nil }
            try savePendingMusic()
            musicNote = job.state == "uncertain" ? "The Music result is uncertain. Its original request is preserved for reconciliation." : nil
            error = nil
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Generate music") }
        }
    }

    func cancelMusic(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard musicJobs.contains(where: { $0.id == job.id }) else { throw NativeAPIError.message("That music job is no longer in the current history.") }
            let _: HubCancelResponse = try await sendEmpty(["api", "hub", "jobs", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the music job") } }
    }

    /// Check only the original uncertain Music job; never submit a replacement.
    func reconcileMusic(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard musicJobs.contains(where: { $0.id == job.id && $0.state == "uncertain" }) else {
                throw NativeAPIError.message("Refresh the original uncertain Music job before checking its result.")
            }
            let result: HubReconciliation = try await sendEmpty(["api", "hub", "jobs", job.id, "items", "0", "reconcile"], method: "POST")
            guard connectionID == requestConnection else { return }
            if result.reconciled && result.clientRequestId == pendingMusic?.clientRequestId {
                pendingMusic = nil
                try savePendingMusic()
            }
            await refresh()
            guard connectionID == requestConnection else { return }
            musicNote = result.reconciled
                ? nil
                : "The original Music result is still uncertain. Its request remains protected; no new generation was submitted."
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Check the original Music result") }
        }
    }

    /// Abandon one uncertain attempt for good.
    ///
    /// This is deliberately separate from cancel and reconcile. The Hub accepts it only for a job
    /// that is already uncertain, marks it cancelled and releases the worker identity it has been
    /// pinning, so later work is no longer blocked by an attempt nobody can resolve.
    private func discard(_ job: NativeJob, action: String) async {
        guard let requestConnection = connectionID else { return }
        do {
            let response: HubDiscardResponse = try await sendEmpty(
                ["api", "hub", "jobs", job.id, "items", String(job.itemIndex), "discard"],
                method: "POST")
            guard connectionID == requestConnection else { return }
            guard response.ok else { throw NativeAPIError.message("The Hub did not discard that job.") }
            error = nil
            await refresh()
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: action) }
        }
    }
    func discardImage(_ job: NativeJob) async { await discard(job, action: "Discard the image attempt") }
    func discardAudio(_ job: NativeJob) async { await discard(job, action: "Discard the audio attempt") }
    func discardMusic(_ job: NativeJob) async { await discard(job, action: "Discard the music attempt") }

    /// Fetch one Music artifact and verify it against the Hub's own receipt.
    ///
    /// The count, the SHA-256 and the RIFF/WAVE magic are required, exactly as the Voice path
    /// requires them: bytes that are not the generated WAV must never be played or saved under an
    /// audio name.
    func musicData(for job: NativeJob) async throws -> Data {
        guard let requestConnection = connectionID else { throw NativeAPIError.message("Start the local services before retrieving music.") }
        guard job.state == "done", musicJobs.contains(where: { $0.id == job.id }),
              let bytes = job.bytes, bytes > 0, let sha = job.sha256,
              sha.count == 64, sha.allSatisfy({ $0.isHexDigit }) else {
            throw NativeAPIError.message("This music clip has no verified completed result. Refresh its job before playing or saving it.")
        }
        let data = try await performData(makeRequest(["api", "hub", "jobs", job.id, "items", "0", "artifact"]))
        guard connectionID == requestConnection else { throw NativeAPIError.message("The Hub connection changed before music retrieval completed.") }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard Int64(data.count) == bytes, digest == sha.lowercased(),
              data.count >= 12, data.prefix(4) == Data([0x52, 0x49, 0x46, 0x46]),
              data[8..<12] == Data([0x57, 0x41, 0x56, 0x45]) else {
            throw NativeAPIError.message("Music verification failed. Refresh the job and retrieve its original result again.")
        }
        return data
    }

    /// Save one music clip and confirm delivery only after the write is re-read and verified.
    func saveMusic(_ job: NativeJob, to destination: URL) async throws {
        let requestConnection = connectionID
        let data = try await musicData(for: job)
        try data.write(to: destination, options: .atomic)
        guard try Data(contentsOf: destination) == data else {
            throw NativeAPIError.message("Saved music verification failed. Choose another destination and save again.")
        }
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The music was saved and verified, but the local service connection changed before delivery was acknowledged. Save again to confirm delivery.")
        }
        do {
            let receipt: HubAckReceipt = try await sendEmpty(
                ["api", "hub", "jobs", job.id, "items", "0", "ack"], method: "POST")
            if !receipt.ok { throw NativeAPIError.message("rejected") }
        } catch {
            throw NativeAPIError.message("The music was saved and verified, but its delivery was not acknowledged. Save again to confirm delivery; the protected copy is not deleted until acknowledgement succeeds.")
        }
    }

    /// Hand a finished music clip to the shared preview player.
    func playMusic(_ job: NativeJob, with player: PreviewPlayer) async throws {
        let data = try await musicData(for: job)
        guard let directory = dataDirectory else {
            throw NativeAPIError.message("The app data directory is unavailable.")
        }
        try player.play(data, extension: Self.audioExtension(of: data), in: directory)
    }

    /// Read the saved voices from this Mac's Voice worker.
    ///
    /// A failure here must not break the rest of the workspace: an older worker without the
    /// voice library leaves the list empty and says so, rather than making the Voice page
    /// unusable for generation.
    func refreshVoices() async {
        guard let requestConnection = connectionID else { return }
        do {
            let value: VoiceListResponse = try await get(["studio", "voice", "api", "voices"])
            guard connectionID == requestConnection else { return }
            voices = value.voices.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            voiceError = nil
        } catch {
            guard connectionID == requestConnection else { return }
            voices = []
            voiceError = "The voice library is unavailable on this build of the local engine."
        }
    }

    /// Add one voice from a reference clip on this Mac.
    ///
    /// The clip and its transcript are the whole point: the worker stores both, and a clone
    /// later matches the audio against those words. The licence defaults to self-owned, and
    /// the consent flag is passed through unchanged - the operator still confirms it.
    func addVoice(name: String, language: String, gender: String, license: String,
                  notes: String, transcript: String, clip: URL, acknowledged: Bool) async -> Bool {
        guard let requestConnection = connectionID, let directory = dataDirectory else { return false }
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { voiceError = "Give the voice a name."; return false }
        guard FileManager.default.fileExists(atPath: clip.path) else {
            voiceError = "That clip is no longer on disk. Choose it again."; return false
        }
        guard acknowledged else {
            voiceError = "Confirm you have the rights to clone this voice."; return false
        }
        savingVoice = true
        defer { if connectionID == requestConnection { savingVoice = false } }
        let boundary = "StudioHub-\(UUID().uuidString)"
        let fields = [
            "name": trimmedName,
            "language": language,
            "gender": gender,
            "license": license,
            "notes": notes,
            "transcript": transcript,
            "permission_acknowledged": acknowledged ? "true" : "false",
        ]
        do {
            let upload = try await Task.detached(priority: .userInitiated) {
                try Self.makeMultipartFile(boundary: boundary, file: clip, fields: fields,
                                           dataDirectory: directory, fileField: "audio")
            }.value
            defer { try? FileManager.default.removeItem(at: upload) }
            guard connectionID == requestConnection else { return false }
            var request = try makeRequest(["studio", "voice", "api", "voices"], method: "POST")
            request.timeoutInterval = 300
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            let response: VoiceResponse = try await performUpload(request, fromFile: upload)
            guard connectionID == requestConnection else { return false }
            await refreshVoices()
            voiceNote = "Saved \(response.voice.name)."
            voiceError = nil
            return true
        } catch {
            guard connectionID == requestConnection else { return false }
            voiceError = Self.actionable(error, action: "Save the voice")
            return false
        }
    }

    /// Change a saved voice's details, including its transcript.
    ///
    /// The audio file is never touched - the worker updates only the metadata and the
    /// transcript - so correcting a typo in the words cannot damage the stored clip.
    func updateVoice(_ voice: NativeVoice, name: String, language: String, gender: String,
                     license: String, notes: String, transcript: String) async -> Bool {
        guard let requestConnection = connectionID else { return false }
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { voiceError = "Give the voice a name."; return false }
        savingVoice = true
        defer { if connectionID == requestConnection { savingVoice = false } }
        do {
            let body = VoiceEdit(name: trimmedName, language: language, gender: gender,
                                 license: license, notes: notes, transcript: transcript)
            let _: VoiceResponse = try await sendJSON(
                ["studio", "voice", "api", "voices", voice.id], method: "PATCH", body: body)
            guard connectionID == requestConnection else { return false }
            await refreshVoices()
            voiceNote = "Updated \(trimmedName)."
            voiceError = nil
            return true
        } catch {
            guard connectionID == requestConnection else { return false }
            voiceError = Self.actionable(error, action: "Update the voice")
            return false
        }
    }

    /// Remove a saved voice. The worker refuses while a fleet controller manages it, and that
    /// refusal is reported rather than swallowed.
    func deleteVoice(_ voice: NativeVoice) async -> Bool {
        guard let requestConnection = connectionID else { return false }
        savingVoice = true
        defer { if connectionID == requestConnection { savingVoice = false } }
        do {
            let _: VoiceDeleteResponse = try await sendEmpty(
                ["studio", "voice", "api", "voices", voice.id], method: "DELETE")
            guard connectionID == requestConnection else { return false }
            await refreshVoices()
            voiceNote = "Removed \(voice.name)."
            voiceError = nil
            return true
        } catch {
            guard connectionID == requestConnection else { return false }
            voiceError = Self.actionable(error, action: "Remove the voice")
            return false
        }
    }

    /// Fetch one saved voice's reference clip so it can be played in the app.
    ///
    /// This is a read of the operator's own file, so it is not hash-checked against a job
    /// receipt: nothing here is a generation result, and a clip that fails to play should say
    /// so rather than pretend to be a verified artifact.
    func voiceData(for voice: NativeVoice) async throws -> Data {
        guard let requestConnection = connectionID else {
            throw NativeAPIError.message("Start the local services before playing a voice.")
        }
        guard let url = voice.audioUrl else {
            throw NativeAPIError.message("This voice has no reference clip recorded.")
        }
        // The worker reports its own path (`/api/voices/<id>/audio`), but the app talks to the
        // Hub, and the Hub reaches a Studio at `/studio/<modality>/<path>`. Requesting the
        // worker path directly asks the Hub for a route it does not have - which is exactly the
        // 404 this produced the first time.
        let components = url.split(separator: "/").map(String.init).filter { !$0.isEmpty }
        guard components.first == "api" else {
            throw NativeAPIError.message("The voice clip address is not a local one.")
        }
        let data = try await performData(makeRequest(["studio", "voice"] + components))
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The connection changed while the clip was loading.")
        }
        return data
    }

    /// Transcribe one clip here and now, so a transcript never has to be typed by hand.
    ///
    /// This is the Subtitle worker's synchronous route: one upload in, the words out. It exists
    /// because the operator has the clip in front of them — adding a voice, or about to clone
    /// from a reference — and being told to go and transcribe it elsewhere is how a transcript
    /// ends up skipped, which then produces a wrong-sounding clone.
    /// Timings shipped beside a reference clip, when the kit carries them.
    ///
    /// `transcript.timed.json` sits beside the clip with the transcript and the timings the
    /// transcriber measured for it. Nothing is inferred: a file that is missing, malformed or
    /// about a different transcript contributes nothing, and the engine falls back to its own
    /// behaviour rather than being handed timings that do not match the audio.
    static func shippedTimings(forReference clip: URL) -> [[String: Any]] {
        let file = clip.deletingLastPathComponent().appendingPathComponent("transcript.timed.json")
        guard let data = try? Data(contentsOf: file),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let segments = root["segments"] as? [[String: Any]] else { return [] }
        return segments.compactMap { segment in
            let start = segment["start_seconds"] as? Double
            let end = segment["end_seconds"] as? Double
            guard let start, let end, end > start else { return nil }
            var value: [String: Any] = ["start_seconds": start, "end_seconds": end]
            if let text = segment["text"] as? String, !text.isEmpty { value["text"] = text }
            return value
        }
    }

    /// Whether these timings still describe this transcript.
    ///
    /// Compared as words, case- and punctuation-insensitively: the engine's own transcript is
    /// round-tripped through the operator's text field, and a lost comma is not a reason to throw
    /// away timings that are otherwise exact.
    static func segments(_ segments: [[String: Any]], match transcript: String) -> Bool {
        guard !segments.isEmpty else { return false }
        let words = { (value: String) -> [String] in
            value.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
        }
        let timed = words(segments.compactMap { $0["text"] as? String }.joined(separator: " "))
        return !timed.isEmpty && timed == words(transcript)
    }

    func transcribeClip(at file: URL, language: String = "") async throws -> String {
        guard let requestConnection = connectionID, let directory = dataDirectory else {
            throw NativeAPIError.message("Start the local services before transcribing.")
        }
        guard FileManager.default.fileExists(atPath: file.path) else {
            throw NativeAPIError.message("That clip is no longer on disk. Choose it again.")
        }
        let boundary = "StudioHub-\(UUID().uuidString)"
        // Timestamps are requested because a long reference needs them to be cloneable: without
        // them the voice engine refuses an oversized reference instead of selecting a window from
        // it. The text is unchanged; only the timing comes along.
        var fields = ["model": Self.whisperTurbo, "word_timestamps": "true"]
        let trimmed = language.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty { fields["language"] = trimmed }
        let upload = try await Task.detached(priority: .userInitiated) {
            try Self.makeMultipartFile(boundary: boundary, file: file, fields: fields,
                                       dataDirectory: directory, fileField: "file")
        }.value
        defer { try? FileManager.default.removeItem(at: upload) }
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The connection changed before transcription started.")
        }
        var request = try makeRequest(["studio", "transcription", "api", "transcribe"], method: "POST")
        // A long recording takes as long as it takes; this is one synchronous request.
        request.timeoutInterval = 60 * 60
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let wire: HubSyncTranscript = try await performUpload(request, fromFile: upload)
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The connection changed while transcribing.")
        }
        let text = (wire.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            throw NativeAPIError.message("The transcriber found no words in that clip. Try a clip with clear speech.")
        }
        audioReferenceSegments = (wire.segments ?? []).compactMap { segment in
            guard let start = segment.start, let end = segment.end, end > start,
                  let words = segment.text, !words.trimmingCharacters(in: .whitespaces).isEmpty
            else { return nil }
            return ["start_seconds": start, "end_seconds": end, "text": words]
        }
        return text
    }

    /// Open a finished subtitle from the history on the Subtitle page.
    ///
    /// The row said "Open Transcript" and only switched pages, leaving the page empty: the
    /// transcript lived in the finished Hub batch, and nothing fetched it. This reads the batch's
    /// own artifact - the SRT the worker produced - and loads it, so the formats and the export
    /// button work on a transcript from an earlier sitting.
    func openSubtitle(_ job: NativeJob) async throws {
        guard connectionID != nil else {
            throw NativeAPIError.message("Start the local services before opening a transcript.")
        }
        guard job.state == "done" else {
            throw NativeAPIError.message("That transcription is not finished yet.")
        }
        var request = try makeRequest(
            ["api", "hub", "transcription", "jobs", job.id, "items", "0", "artifact"])
        request.timeoutInterval = 300
        let data = try await performData(request)
        guard let srt = String(data: data, encoding: .utf8),
              !srt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw NativeAPIError.message("That transcript is empty or could not be read.")
        }
        let text = Self.plainTranscript(srt)
        guard !text.isEmpty else {
            throw NativeAPIError.message("That transcript has no words in it.")
        }
        transcriptionResult = NativeTranscript(
            text: text, srt: srt, vtt: Self.vtt(fromSRT: srt),
            language: "", duration: 0, model: job.model,
            sourceFilename: nil, batchID: job.id)
        transcriptionModel = job.model.isEmpty ? transcriptionModel : job.model
    }

    /// The WebVTT spelling of an SRT, from the same cue times. Only the header and the decimal
    /// separator differ, which is why one conversion serves both formats.
    nonisolated static func vtt(fromSRT srt: String) -> String {
        var lines = ["WEBVTT", ""]
        for line in srt.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || Int(trimmed) != nil { continue }
            if trimmed.contains("-->") {
                lines.append(trimmed.replacingOccurrences(of: ",", with: "."))
            } else {
                lines.append(trimmed)
                lines.append("")
            }
        }
        return lines.joined(separator: "\n")
    }

    /// Fetch a finished job's audio and hand it to the player.
    ///
    /// Same reason as a saved voice: the player needs a file on disk, not bytes, or an MP3 result
    /// fails with `OSStatus error -4`.
    func play(_ job: NativeJob, with player: PreviewPlayer) async throws {
        let data = try await audioData(for: job)
        guard let directory = dataDirectory else {
            throw NativeAPIError.message("The app data directory is unavailable.")
        }
        try player.play(data, extension: Self.audioExtension(of: data), in: directory)
    }

    /// The container a finished artifact is in, from its own bytes.
    ///
    /// Read from the file rather than assumed: a Voice worker returns whatever container the
    /// engine wrote, and guessing ".wav" for an MP3 result would reintroduce the playback failure
    /// this exists to fix. An unrecognised payload falls back to WAV, which is what the engine
    /// writes by default.
    static func audioExtension(of data: Data) -> String {
        let bytes = [UInt8](data.prefix(12))
        if bytes.count >= 12, String(decoding: bytes[0..<4], as: UTF8.self) == "RIFF",
           String(decoding: bytes[8..<12], as: UTF8.self) == "WAVE" { return ".wav" }
        if bytes.count >= 3, bytes[0] == 0x49, bytes[1] == 0x44, bytes[2] == 0x33 { return ".mp3" }
        if bytes.count >= 2, bytes[0] == 0xFF, (bytes[1] & 0xE0) == 0xE0 { return ".mp3" }
        if bytes.count >= 12, String(decoding: bytes[4..<8], as: UTF8.self) == "ftyp" { return ".m4a" }
        if bytes.count >= 4, String(decoding: bytes[0..<4], as: UTF8.self) == "OggS" { return ".ogg" }
        if bytes.count >= 4, String(decoding: bytes[0..<4], as: UTF8.self) == "fLaC" { return ".flac" }
        return ".wav"
    }

    /// Chosen voice from the library, or nil to use a clip chosen from disk.
    @Published var audioVoiceLibraryID: String? {
        didSet {
            invalidateStagedReference()
            audioReferencePath = nil
            audioReferenceSegments = []
            audioReferenceTranscript = voices.first { $0.id == audioVoiceLibraryID }?.transcript ?? ""
            audioReferenceStatus = nil
        }
    }

    /// The saved voices a clone can use: a voice with no transcript cannot be aligned, so it is not
    /// offered rather than being offered and then refused.
    var selectableVoices: [NativeVoice] { voices.filter { $0.readyForCloning } }

    /// Text for the Audio page, so an empty picker explains itself instead of looking broken.
    var voicePickerDetail: String {
        if !selectableVoices.isEmpty {
            return "Clones \(selectableVoices.count) of your saved voices. The clip, its words and its timings stay in the app's own voice library."
        }
        if voices.isEmpty {
            return "No saved voices yet. Import clips on the Voices page, or choose a clip file to clone from."
        }
        return "None of your saved voices has a transcript yet, so none can be aligned. Add the words on the Voices page, or choose a clip file."
    }

    /// Fetch one saved voice's clip and hand it to the player.
    ///
    /// The clip goes to a file on disk before it is played: `AVAudioPlayer(data:)` cannot identify
    /// an MP3 handed to it as bytes and fails with `OSStatus error -4`, which is what stopped seven
    /// of the ten saved voices from playing.
    func play(_ voice: NativeVoice, with player: PreviewPlayer) async throws {
        let data = try await voiceData(for: voice)
        guard let directory = dataDirectory else {
            throw NativeAPIError.message("The app data directory is unavailable.")
        }
        try player.play(data, extension: voice.audioExtension, in: directory)
    }

    /// Point the next generation at a saved voice instead of a loose file.
    ///
    /// Staging reads the clip from disk, and a saved voice lives in the worker's store, not in
    /// the app's own folder - so the clip is fetched once and written here. Setting the
    /// reference to a path that does not exist would make the next Prepare Voice fail with
    /// "the clip is no longer at that location", which reads like the operator's mistake.
    @discardableResult
    func useVoice(_ voice: NativeVoice) async -> Bool {
        guard voice.readyForCloning else {
            voiceNote = nil
            voiceError = "\(voice.name) has no transcript yet. Add one before cloning, or the clone cannot align the audio."
            return false
        }
        do {
            let data = try await voiceData(for: voice)
            guard let directory = dataDirectory else {
                throw NativeAPIError.message("The app data directory is unavailable.")
            }
            let folder = directory.appendingPathComponent("Voices", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let clip = folder.appendingPathComponent("voice-\(voice.id)\(voice.audioExtension)")
            try data.write(to: clip, options: .atomic)
            audioReferencePath = clip.path
            audioReferenceTranscript = voice.transcript ?? ""
            voiceNote = "Reference set to \(voice.name). Press Prepare Voice, then generate."
            voiceError = nil
            return true
        } catch {
            voiceError = error.localizedDescription
            return false
        }
    }

    /// One voice found in a folder of existing voices: a clip, its name, and the words.
    struct VoiceImportCandidate: Equatable {
        let name: String
        let clip: URL
        let transcript: String
        var isReady: Bool { !transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    /// Read a folder of voices in the shape the old Voice Studio used: one folder per voice
    /// holding a clip and, usually, a transcript; or loose clips with a transcript beside them.
    ///
    /// This is separate from the view so it can be tested without a window. The order is
    /// sorted, so an import is repeatable rather than dependent on directory order.
    nonisolated static func scanVoiceFolder(_ folder: URL) throws -> [VoiceImportCandidate] {
        let audioExtensions: Set<String> = ["wav", "mp3", "m4a", "aac", "flac", "ogg", "opus"]
        let transcriptExtensions: Set<String> = ["txt", "text", "srt", "vtt", "md"]
        let manager = FileManager.default
        let entries: [URL]
        do {
            entries = try manager.contentsOfDirectory(at: folder,
                                                       includingPropertiesForKeys: [.isDirectoryKey],
                                                       options: [.skipsHiddenFiles])
        } catch {
            throw NativeAPIError.message("That folder could not be read.")
        }
        func text(beside clip: URL, in items: [URL]) -> String {
            for item in items {
                if transcriptExtensions.contains(item.pathExtension.lowercased()),
                   let body = try? String(contentsOf: item, encoding: .utf8) {
                    return plainTranscript(body)
                }
            }
            // A loose clip with its transcript beside it, same stem.
            for suffix in transcriptExtensions {
                let sibling = clip.deletingPathExtension().appendingPathExtension(suffix)
                if let body = try? String(contentsOf: sibling, encoding: .utf8) {
                    return plainTranscript(body)
                }
            }
            return ""
        }
        var found: [VoiceImportCandidate] = []
        for entry in entries.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            let isDirectory = (try? entry.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
            let items = isDirectory
                ? ((try? manager.contentsOfDirectory(at: entry, includingPropertiesForKeys: nil,
                                                     options: [.skipsHiddenFiles])) ?? [])
                : [entry]
            guard let clip = items.first(where: { audioExtensions.contains($0.pathExtension.lowercased()) })
            else { continue }
            let name = isDirectory ? entry.lastPathComponent
                                   : entry.deletingPathExtension().lastPathComponent
            found.append(VoiceImportCandidate(name: name, clip: clip,
                                              transcript: text(beside: clip, in: items)))
        }
        return found
    }

    /// Strip subtitle timing so an imported .srt or .vtt is usable as a transcript.
    ///
    /// A transcript has to be the words alone: cue numbers and timestamp lines would be fed to
    /// the clone as if they were spoken, and the alignment would be wrong.
    nonisolated static func plainTranscript(_ body: String) -> String {
        var kept: [String] = []
        for line in body.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty { continue }
            if trimmed.uppercased() == "WEBVTT" { continue }
            if trimmed.contains("-->") { continue }
            if Int(trimmed) != nil { continue }
            kept.append(trimmed)
        }
        return kept.joined(separator: " ")
    }

    /// Import every ready voice from a folder, skipping ones already saved and ones with no
    /// words. Returns what happened, so the caller can say it rather than guess.
    func importVoices(from folder: URL) async -> (added: Int, skipped: Int, total: Int) {
        let candidates: [VoiceImportCandidate]
        do { candidates = try Self.scanVoiceFolder(folder) }
        catch {
            voiceError = error.localizedDescription
            return (0, 0, 0)
        }
        guard !candidates.isEmpty else {
            voiceError = "No reference clips were found in that folder. Each voice needs a clip; a transcript beside it is used when present."
            return (0, 0, 0)
        }
        var added = 0, skipped = 0
        for candidate in candidates {
            // A voice already saved under the same name is left alone: importing twice must not
            // duplicate a library the operator has been curating.
            if voices.contains(where: { $0.name.caseInsensitiveCompare(candidate.name) == .orderedSame })
                || !candidate.isReady {
                skipped += 1
                continue
            }
            let ok = await addVoice(name: candidate.name, language: "en", gender: "n",
                                    license: "self-owned",
                                    notes: "Imported from \(folder.lastPathComponent)",
                                    transcript: candidate.transcript, clip: candidate.clip,
                                    acknowledged: true)
            if ok { added += 1 } else { skipped += 1 }
        }
        voiceNote = "Imported \(added) voice\(added == 1 ? "" : "s")"
            + (skipped > 0 ? "; skipped \(skipped) (no transcript, or already saved)." : ".")
        return (added, skipped, candidates.count)
    }

    func transcribe() async {
        guard !transcribing, let requestConnection = connectionID else { return }
        do {
            if pendingTranscription != nil {
                await reconcileTranscription()
                if pendingTranscription != nil {
                    throw NativeAPIError.message("The previous transcription upload has an uncertain response. Check its Hub history before starting another upload.")
                }
                return
            }
            let model = try usableModel(repo: transcriptionModel, function: .transcription)
            guard let file = transcriptionFile, FileManager.default.fileExists(atPath: file.path) else {
                throw NativeAPIError.message("Choose an audio or video file that still exists.")
            }
            guard !wordTimestamps || model.supportsWordTimestamps else {
                throw NativeAPIError.message("The selected transcription model does not support word timestamps.")
            }
            transcribing = true
            defer { if connectionID == requestConnection { transcribing = false } }
            let identity = PendingTranscription(episode: "native-\(UUID().uuidString)",
                itemID: "clip-\(UUID().uuidString)", filePath: file.path,
                model: model.repo, language: transcriptionLanguage.trimmingCharacters(in: .whitespacesAndNewlines),
                wordTimestamps: wordTimestamps, batchID: nil)
            pendingTranscription = identity
            try savePendingTranscription()
            let boundary = "StudioHub-\(UUID().uuidString)"
            guard let uploadDirectory = dataDirectory else { throw NativeAPIError.message("The app data directory is unavailable.") }
            let fields = [
                    "model": model.repo,
                    "item_ids": identity.itemID,
                    "project": "native",
                    "episode": identity.episode,
                    "language": transcriptionLanguage.trimmingCharacters(in: .whitespacesAndNewlines),
                    "word_timestamps": wordTimestamps ? "true" : "false"
                ]
            // Only present when pinned, so the Hub's automatic choice stays the default rather
            // than an empty string it would have to interpret.
            var uploadFields = fields
            if let routingPin, !routingPin.isEmpty { uploadFields["routing"] = routingPin }
            let uploadFile = try await Task.detached(priority: .userInitiated) {
                try Self.makeMultipartFile(
                    boundary: boundary, file: file, fields: uploadFields, dataDirectory: uploadDirectory
                )
            }.value
            defer { try? FileManager.default.removeItem(at: uploadFile) }
            guard connectionID == requestConnection else { return }
            var request = try makeRequest(["api", "hub", "transcription", "jobs"], method: "POST")
            request.timeoutInterval = 60 * 60
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            let wire: HubTranscriptionSubmit = try await performUpload(request, fromFile: uploadFile)
            guard connectionID == requestConnection else { return }
            pendingTranscription = identity.withBatchID(wire.batchId)
            try savePendingTranscription()
            try await pollTranscription(batchID: wire.batchId, requestConnection: requestConnection)
            if pendingTranscription == nil { error = nil }
        } catch {
            guard connectionID == requestConnection else { return }
            self.error = Self.actionable(error, action: "Transcribe the selected file")
        }
    }

    private func reconcileTranscription() async {
        guard let pending = pendingTranscription, connectionID != nil else { return }
        do {
            var batchID = pending.batchID
            if batchID == nil {
                let listing: HubTranscriptionList = try await get(["api", "hub", "transcription", "jobs"])
                batchID = listing.batches.first { $0.project == "native" && $0.episode == pending.episode }?.id
                if let batchID {
                    pendingTranscription = pending.withBatchID(batchID)
                    try savePendingTranscription()
                }
            }
            if let batchID, let requestConnection = connectionID {
                try await pollTranscription(batchID: batchID, requestConnection: requestConnection, wait: false)
            }
        } catch { /* Keep the uncertain identity; a later refresh can reconcile it. */ }
    }

    private func pollTranscription(batchID: String, requestConnection: UUID, wait: Bool = true) async throws {
        for _ in 0..<(wait ? 1_800 : 1) {
            guard connectionID == requestConnection else { return }
            do {
                let detail: HubTranscriptionDetail = try await get(["api", "hub", "transcription", "jobs", batchID])
                guard connectionID == requestConnection else { return }
                let item = detail.items.first
                if let item, item.state == "done", item.artifactUrl != nil,
                   let pending = pendingTranscription, pending.batchID == batchID {
                    let srt = String(decoding: try await performData(makeRequest(
                        ["api", "hub", "transcription", "jobs", batchID, "items", "0", "artifact"])), as: UTF8.self)
                    guard connectionID == requestConnection else { return }
                    let metadata = item.metadata
                    transcriptionResult = NativeTranscript(text: metadata?.text ?? "", srt: srt,
                        vtt: metadata?.vtt ?? "", language: metadata?.language ?? "",
                        duration: metadata?.duration ?? 0, model: metadata?.model ?? detail.model,
                        sourceFilename: URL(fileURLWithPath: pending.filePath).lastPathComponent,
                        batchID: batchID)
                    pendingTranscription = nil
                    try savePendingTranscription()
                    try saveState()
                    return
                }
                if ["uncertain", "manual_action_required"].contains(item?.state ?? "") {
                    throw NativeAPIError.message("The Hub transcription result is uncertain. Its identity is saved; check the original job before retrying so the same file is not generated twice.")
                }
                if ["error", "cancelled"].contains(item?.state ?? "") {
                    throw NativeAPIError.message("The Hub transcription item ended in state \(item?.state ?? "unknown").")
                }
            } catch { if wait { throw error }; return }
            if wait { try? await Task.sleep(for: .seconds(2)) }
        }
        if wait { throw NativeAPIError.message("The Hub transcription job is still pending. Its identity was saved for recovery.") }
    }

    func startDownload(_ model: NativeModel) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard models.contains(where: { $0.id == model.id && $0.repo == model.repo }), model.supported else {
                throw NativeAPIError.message("This model is not supported by the current native workspace.")
            }
            guard let package = modelPackages[model.repo] else {
                throw NativeAPIError.message("This model has no approved Hub package.")
            }
            let run: HubModelDownload = try await sendHubJSON(
                ["api", "hub", "model-packages", package.packageId, "download"], body: EmptyBody())
            guard connectionID == requestConnection else { return }
            hubDownloadRuns[run.id] = model.repo
            try saveHubDownloadRuns()
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Start the model download") } }
    }

    func cancelDownload(_ job: NativeDownload) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard hubDownloadRuns[job.id] != nil else {
                throw NativeAPIError.message("Only Hub package downloads can be cancelled from this workspace.")
            }
            let _: HubModelDownload = try await sendEmpty(["api", "hub", "model-downloads", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the model download") } }
    }

    /// Register a model folder that was copied onto this Mac, without downloading it again.
    ///
    /// This is the app half of "copy the built app and the model library over": a folder that
    /// arrived by USB, a snapshot, or a Finder copy becomes a verified library entry. The Hub
    /// validates the manifest, every file digest and the layout, so a partial or altered copy
    /// is refused with the reason rather than quietly accepted.
    func importModel(_ model: NativeModel, from folder: URL) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard let package = modelPackages[model.repo] else {
                throw NativeAPIError.message("This model has no approved Hub package.")
            }
            updatingModels = true
            defer { if connectionID == requestConnection { updatingModels = false } }
            // The Hub's import route reads `source_folder`, so this goes through the
            // snake_case encoder rather than the camelCase one used for job envelopes.
            let result: HubModelState = try await sendJSON(
                ["api", "hub", "model-packages", package.packageId, "import"],
                method: "POST", body: HubModelImport(sourceFolder: folder.path))
            guard connectionID == requestConnection else { return }
            // A rejected import is a 2xx with a non-ready state, not an error status, so the
            // answer has to be read rather than assumed from the fact that the call returned.
            try Self.requireVerified(result, model: model)
            modelNote = "Imported \(model.label) from \(folder.lastPathComponent)."
            modelNoteRepo = model.repo
            error = nil
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Import the model folder") } }
    }

    /// The Hub reports a refused verification or import as a 2xx with a non-ready state and
    /// its own reason — `invalid` for changed files, `dependency_missing` for a partial copy.
    /// Treating any 2xx as success would show "Verified" for exactly the broken library this
    /// control exists to find.
    private static func requireVerified(_ result: HubModelState, model: NativeModel) throws {
        guard result.state == "files_verified" else {
            throw NativeAPIError.message(
                "\(model.label) is not ready: \(result.reason ?? result.state).")
        }
    }

    /// Re-check one installed package's files against its pinned manifest.
    ///
    /// Verification is timestamp-sensitive, so a library that was hard-linked or copied with
    /// tools that rewrite metadata reports "File changed" even when the bytes match. This is
    /// the remedy, and it is why it must be reachable without a terminal.
    func verifyModel(_ model: NativeModel) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard let package = modelPackages[model.repo] else {
                throw NativeAPIError.message("This model has no approved Hub package.")
            }
            updatingModels = true
            defer { if connectionID == requestConnection { updatingModels = false } }
            let result: HubModelState = try await sendEmpty(
                ["api", "hub", "model-packages", package.packageId, "verify"], method: "POST")
            guard connectionID == requestConnection else { return }
            try Self.requireVerified(result, model: model)
            modelNote = "Verified \(model.label)."
            modelNoteRepo = model.repo
            error = nil
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Verify the model files") } }
    }

    /// Verify every installed package, reporting how many are now ready.
    func verifyInstalledModels() async {
        guard let requestConnection = connectionID else { return }
        let installed = models.filter { $0.cached }
        guard !installed.isEmpty else { modelNote = "No installed models to verify."; return }
        updatingModels = true
        defer { if connectionID == requestConnection { updatingModels = false } }
        var failed: [String] = []
        for model in installed {
            guard connectionID == requestConnection else { return }
            guard let package = modelPackages[model.repo] else { failed.append(model.label); continue }
            do {
                let result: HubModelState = try await sendEmpty(
                    ["api", "hub", "model-packages", package.packageId, "verify"], method: "POST")
                try Self.requireVerified(result, model: model)
            } catch { failed.append(model.label) }
        }
        guard connectionID == requestConnection else { return }
        modelNote = failed.isEmpty
            ? "Verified all \(installed.count) installed models."
            : "Verified \(installed.count - failed.count) of \(installed.count). Could not verify: \(failed.joined(separator: ", "))."
        // A successful sweep must clear a stale banner, and the summary is about the library
        // rather than about whichever model happens to be selected.
        modelNoteRepo = nil
        if failed.isEmpty { error = nil }
        await refresh()
    }

    /// Release the Hub's pause on a model whose failure has been fixed.
    ///
    /// The Hub pauses a model automatically after repeated terminal failures, and while it is paused
    /// every job for it is refused before dispatch. That refusal was visible nowhere in the app, and
    /// nothing could lift it: the only way back was to edit the Hub's own state file and relaunch.
    func recheckModel(_ model: NativeModel) async {
        guard let requestConnection = connectionID else { return }
        do {
            updatingModels = true
            defer { if connectionID == requestConnection { updatingModels = false } }
            let _: HubModelRecheck = try await sendJSON(
                ["api", "hub", "model-health", "recheck"], method: "POST",
                body: ModelHealthRecheck(model: model.repo, studio: model.pausedStudio))
            guard connectionID == requestConnection else { return }
            modelNote = "Rechecked \(model.label). It can take jobs again."
            modelNoteRepo = model.repo
            error = nil
            await refresh()
        } catch {
            if connectionID == requestConnection {
                self.error = Self.actionable(error, action: "Recheck the model")
            }
        }
    }

    func cancelAudio(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard audioJobs.contains(where: { $0.id == job.id }) else { throw NativeAPIError.message("That audio job is no longer in the local queue.") }
            let _: HubCancelResponse = try await sendEmpty(["api", "hub", "jobs", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the audio job") } }
    }

    /// Call off a running subtitle batch. Its own route, because transcripts are batches rather
    /// than the job queue the other two studios share.
    func cancelSubtitle(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard subtitleJobs.contains(where: { $0.id == job.id }) else {
                throw NativeAPIError.message("That subtitle job is no longer in the local history.")
            }
            let _: HubCancelResponse = try await sendEmpty(
                ["api", "hub", "transcription", "jobs", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch {
            if connectionID == requestConnection {
                self.error = Self.actionable(error, action: "Cancel the subtitle job")
            }
        }
    }

    /// Read the machine's memory policy and what each Studio is holding right now.
    ///
    /// The Hub fans this out to every Studio, so it is refreshed when Settings opens and
    /// after each change rather than on the polling loop.
    func refreshMemoryPolicy() async {
        guard let requestConnection = connectionID else { return }
        do {
            let inventory: NativeMemoryInventory = try await get(["api", "hub", "memory", "policy"])
            guard connectionID == requestConnection else { return }
            memoryInventory = inventory
        } catch {
            // An older Hub without these routes must not break the rest of Settings; the
            // section explains the absence instead of failing the screen.
            guard connectionID == requestConnection else { return }
            memoryInventory = nil
            memoryNote = Self.actionable(error, action: "Read model memory settings")
        }
    }

    /// Read the storage policy and what each machine is currently holding.
    func refreshStorage() async {
        guard let requestConnection = connectionID else { return }
        do {
            let settings: NativeDeviceSettings = try await get(["api", "hub", "settings"])
            guard connectionID == requestConnection else { return }
            deviceSettings = settings
            let preview: NativeStoragePreview = try await sendJSON(
                ["api", "hub", "settings", "preview"], method: "POST",
                body: StoragePreviewBody(
                    maxBytes: settings.effective.maxBytes,
                    retentionDays: settings.effective.retentionDays,
                    cleanupEnabled: settings.effective.cleanupEnabled,
                    cleanupIntervalSeconds: settings.effective.cleanupIntervalSeconds,
                    minimumFreeBytes: settings.effective.minimumFreeBytes ?? 0))
            guard connectionID == requestConnection else { return }
            storageUsage = preview.machines
        } catch {
            guard connectionID == requestConnection else { return }
            deviceSettings = nil
            storageUsage = []
            storageNote = Self.actionable(error, action: "Read local storage settings")
        }
    }

    /// Apply new limits. The Hub validates them before anything is written.
    func saveStorageLimits(maxBytes: Int64, retentionDays: Int, cleanupEnabled: Bool) async {
        guard let requestConnection = connectionID else { return }
        savingStorage = true
        storageNote = nil
        defer { if connectionID == requestConnection { savingStorage = false } }
        do {
            let settings: NativeDeviceSettings = try await sendJSON(
                ["api", "hub", "settings"], method: "PUT",
                body: StorageLimitsBody(maxBytes: maxBytes, retentionDays: retentionDays,
                                        cleanupEnabled: cleanupEnabled))
            guard connectionID == requestConnection else { return }
            deviceSettings = settings
            storageNote = "Saved. Cleanup keeps at most \(Self.gigabytes(settings.effective.maxBytes)) and keeps finished work for \(settings.effective.retentionDays) days."
            error = nil
            await refreshStorage()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Save local storage settings") } }
    }

    /// Restore the shipped limits for this device.
    func restoreStorageDefaults() async {
        guard let requestConnection = connectionID else { return }
        savingStorage = true
        storageNote = nil
        defer { if connectionID == requestConnection { savingStorage = false } }
        do {
            let settings: NativeDeviceSettings = try await sendEmpty(
                ["api", "hub", "settings", "restore-defaults"], method: "POST")
            guard connectionID == requestConnection else { return }
            deviceSettings = settings
            storageNote = "Restored the shipped limits for this Mac."
            error = nil
            await refreshStorage()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Restore the storage defaults") } }
    }

    static func gigabytes(_ bytes: Int64) -> String {
        String(format: "%.1f GB", Double(bytes) / 1_000_000_000)
    }

    /// Read the Hub's generation statistics, optionally limited to the last N hours.
    func refreshStatistics(hours: Int? = nil) async {
        guard let requestConnection = connectionID else { return }
        do {
            // `hours` is a query parameter, not a path segment, so it cannot go through the
            // path-appending helper.
            var request = try makeRequest(["api", "hub", "stats"])
            if let hours, var components = URLComponents(url: request.url!,
                                                         resolvingAgainstBaseURL: false) {
                components.queryItems = [URLQueryItem(name: "hours", value: String(hours))]
                guard let url = components.url else {
                    throw NativeAPIError.message("The statistics request could not be built.")
                }
                request.url = url
            }
            let value: NativeStatistics = try await perform(request)
            guard connectionID == requestConnection else { return }
            statistics = value
            statisticsHours = hours
        } catch {
            guard connectionID == requestConnection else { return }
            // Statistics are extra context; an older Hub without the route must not break
            // the Overview screen.
            statistics = nil
        }
    }

    func releaseMemory() async {
        guard let requestConnection = connectionID else { return }
        updatingMemory = true
        defer { if connectionID == requestConnection { updatingMemory = false } }
        do {
            let result: NativeMemoryChange = try await sendEmpty(["api", "hub", "memory", "release"], method: "POST")
            guard connectionID == requestConnection else { return }
            memoryNote = Self.releaseSummary(result)
            error = nil
            await refreshMemoryPolicy()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Release model memory") } }
    }

    /// Apply one idle-release choice to the Studios that support one.
    func setMemoryMode(_ mode: String, studioIDs: [String]? = nil) async {
        guard let requestConnection = connectionID else { return }
        updatingMemory = true
        defer { if connectionID == requestConnection { updatingMemory = false } }
        do {
            let result: NativeMemoryChange = try await sendJSON(
                ["api", "hub", "memory", "policy"], method: "PUT",
                body: MemoryModeBody(mode: mode, studioIds: studioIDs))
            guard connectionID == requestConnection else { return }
            memoryNote = Self.modeSummary(result)
            error = nil
            await refreshMemoryPolicy()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Change model memory settings") } }
    }

    /// Change this Hub's role. Standalone creates locally; Controller serves other Macs and
    /// reports their work; Node does local work only and defers customer jobs to a controller.
    func clearStartupNote() { startupNote = nil }

    /// Run one file load, and never let a damaged file stop the app.
    ///
    /// The unreadable file is moved aside rather than deleted, so an operator can send it on, and
    /// the reason is kept in `startupNote` for the banner on the main screen.
    private func loadBestEffort(_ url: URL, _ name: String, _ load: () throws -> Void) {
        do { try load() } catch {
            let aside = url.appendingPathExtension("corrupt")
            try? FileManager.default.removeItem(at: aside)
            try? FileManager.default.moveItem(at: url, to: aside)
            startupNote = "\(name.capitalized) could not be read, so it was set aside as \(aside.lastPathComponent) and the app started without it."
        }
    }

    /// Name this Mac. A label for people, not an identity: routing and fleet membership are
    /// unaffected, which is why it can be changed at any time.
    func renameThisMachine(_ name: String) async {
        guard let requestConnection = connectionID else { return }
        roleNote = nil
        do {
            let _: EmptyResponse = try await sendJSON(
                ["api", "hub", "machine-label"], method: "POST", body: RenameRequest(name: name))
            guard connectionID == requestConnection else { return }
            await refreshConnection()
            roleNote = name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? "Name cleared; this Mac shows its hostname again."
                : "This Mac is now called \(name.trimmingCharacters(in: .whitespacesAndNewlines))."
            error = nil
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Rename this Mac") } }
    }

    func setRole(_ role: String) async {
        guard let requestConnection = connectionID else { return }
        roleNote = nil
        do {
            // The Hub's controller route is a PUT; the job-envelope helper always POSTs. Its
            // reply is the flat settings object, not the {settings, readiness} envelope the
            // GET returns, so the body is not decoded here - the refresh below reads the truth.
            let _: EmptyResponse = try await sendJSON(
                ["api", "hub", "controller"], method: "PUT", body: RoleChange(role: role))
            guard connectionID == requestConnection else { return }
            await refreshConnection()
            // A Mac running its own group is nobody's node, so it keeps no controller to re-join.
            if role == "controller" { onControllerJoined?(nil) }
            // The note names the role the way the picker does, and reports the Hub's answer
            // rather than the operator's request.
            let applied = role == "agent" ? "Node" : role.capitalized
            let guidance = role == "agent"
                ? " It joins a controller from this Mac's own Connections page."
                : (role == "controller"
                   ? " Give GatewayKH this Mac's address and Fleet token on its Fleet page to send it work."
                   : "")
            note(.success, "This Mac is now a \(applied)." + guidance)
            error = nil
        } catch {
            if connectionID == requestConnection {
                let message = Self.actionable(error, action: "Change this Mac's role")
                note(.failure, message)
                self.error = message
            }
        }
    }

    /// Read the nodes that have joined this Mac, and their live state.
    func refreshFleetNodes() async {
        guard let requestConnection = connectionID else { return }
        do {
            let value: NativeFleetInventory = try await get(["api", "hub", "fleet", "nodes"])
            guard connectionID == requestConnection else { return }
            fleetNodes = value
        } catch {
            guard connectionID == requestConnection else { return }
            // An older Hub without the route must not break the screen.
            fleetNodes = nil
        }
        // The overview is a separate read because it can be slower - it waits on remote Macs -
        // and the node list must still appear promptly when a node is off.
        do {
            let overview: NativeFleetOverview = try await get(["api", "hub", "fleet", "overview"])
            guard connectionID == requestConnection else { return }
            fleetOverview = overview
        } catch {
            guard connectionID == requestConnection else { return }
            fleetOverview = nil
        }
    }

    /// Join this Mac to a controller at an address the operator typed.
    ///
    /// The request leaves this Mac for another one, so it does not go through the helper that
    /// targets our own Hub — and the address is validated here as well as on the controller,
    /// because a token must never be sent to a host that was not checked first.
    @discardableResult
    func joinController(address: String, token: String, boundHosts: [String] = [],
                        quiet: Bool = false, startupAttemptID: UUID? = nil) async -> Bool {
        if startupAttemptID == nil { invalidateStartupMembershipAttempt() }
        guard let requestConnection = connectionID,
              startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
        fleetNote = nil
        do {
            let target = try Self.privateHTTPAddress(address)
            let secret = token.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !secret.isEmpty else { throw NativeAPIError.message("Enter the controller's token.") }
            let nodeID = (controllerIdentity?.settings.controllerId ?? "studiohub-node")
                .lowercased()
            let name = controllerIdentity?.settings.machineName ?? "Studio Hub"
            var request = URLRequest(url: target.appendingPathComponent("api/hub/fleet/nodes"))
            request.httpMethod = "POST"
            request.timeoutInterval = 30
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            request.setValue(secret, forHTTPHeaderField: "X-Hub-Token")
            guard let ownAddress = Self.studioAddress(for: target, port: baseURL?.port,
                                                      boundHosts: boundHosts) else {
                throw NativeAPIError.message("This Mac is not reachable at any address a controller could use. Choose a network address under Connections → Fleet, restart the services, then join.")
            }
            // Report what this Mac actually runs, from its own Hub rather than an assumption:
            // a Mac whose Image worker failed to start must not be handed image work.
            let workers: HubWorkersResponse = try await get(["api", "hub", "workers"])
            guard connectionID == requestConnection,
                  startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
            let studios = workers.workers.map { JoinStudio(id: $0.id, modality: $0.modality) }
            guard !studios.isEmpty else {
                throw NativeAPIError.message("This Mac is not running any studios yet, so there is nothing to offer a controller.")
            }
            // The Hub reads snake_case here, like its other administration routes. Sending the
            // Swift names would have the controller refuse a join for a missing node_id while
            // the app reported only that it was refused.
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            request.httpBody = try encoder.encode(JoinRequest(
                nodeId: nodeID, name: name, address: ownAddress, studios: studios))
            joiningFleet = true
            defer {
                if connectionID == requestConnection,
                   startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID {
                    joiningFleet = false
                }
            }
            let (data, response) = try await session.data(for: request)
            guard connectionID == requestConnection,
                  startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
            guard let http = response as? HTTPURLResponse else {
                throw NativeAPIError.message("The controller returned no response.")
            }
            if (200..<300).contains(http.statusCode), !secret.isEmpty {
                // Take on the controller's credential, which is what registration means.
                //
                // The route existed and nothing called it, so a Mac joined a controller and then
                // answered 401 to that controller: the controller drives its nodes with its own
                // credential, and handing it over is the point of registering. The one credential
                // per cluster is the whole design, and this is the half that installs it.
                //
                // Registration first, adoption second: the controller has to accept the node before
                // the node stops accepting its own credential.
                do {
                    try await adoptClusterCredential(secret)
                } catch {
                    // A join without adoption is not a working join: the controller has this Mac
                    // on its roster and then gets 401 from it, which reads as "unreachable" with
                    // no explanation. A startup retry may try again quietly, but a person who
                    // pressed Join must be told.
                    if startupAttemptID != nil { throw error }
                    let message = "The controller accepted this Mac, but taking on its Fleet "
                        + "token failed, so the controller cannot send it work yet. "
                        + Self.actionable(error, action: "Adopt the controller's token")
                    note(.failure, message)
                    self.error = message
                    return false
                }
            }
            guard connectionID == requestConnection,
                  startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
            guard (200..<300).contains(http.statusCode) else {
                // Quote the controller's own reason when it gives one - "studio modality must be
                // one of [...]" is far more use than "refused (400)".
                let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                let reason = (body?["detail"] as? String)?
                    .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                throw NativeAPIError.message(reason.isEmpty
                    ? (http.statusCode == 401
                        ? "The controller refused this Mac's join (401). Use the controller's Fleet token, not its Hub token."
                        : "The controller refused the join (\(http.statusCode)).")
                    : "The controller refused this Mac's join: \(reason)")
            }
            controllerAddress = "\(target.host ?? ""):\(target.port ?? 0)"
            note(.success, "Joined the controller at \(controllerAddress). "
                 + "This Mac can now be sent work by it.")
            error = nil
            // Remember it, so this Mac's services can re-join by themselves after a restart
            // instead of waiting for someone to retype an address that may have changed.
            onControllerJoined?(controllerAddress)
            // Show the standing answer straight away, rather than leaving the operator to work
            // out from a transient note whether the join took.
            await refreshControllerHealth()
            return true
        } catch {
            guard connectionID == requestConnection,
                  startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
            if quiet {
                // A start that cannot reach its controller is still a working Mac: the operator
                // gets a line to read, not a modal failure over a machine that is fine.
                fleetNote = "Could not reach the controller at \(address): \(error.localizedDescription)"
            } else {
                // Both the app-wide line and the Connections banner: the line is the global
                // signal that something failed, the banner is where the detail and the fix live.
                let message = Self.actionable(error, action: "Join the controller")
                note(.failure, message)
                self.error = message
            }
            return false
        }
    }

    /// Re-join the controller this Mac belongs to during startup.
    ///
    /// A node's own address changes when its Hub restarts, and a controller's address changed on
    /// every restart before the port was pinned, so a group that had ever restarted was held
    /// together by nothing but the operator retyping addresses. The join route is idempotent - it
    /// records a node or refreshes what a rejoining node reports about itself - so the fix is to
    /// call it at startup.
    ///
    /// Only a Mac that adopted someone else's credential belongs to a group; a controller's own
    /// credential is minted, and its place is not inside another controller.
    private func retryStartupMembershipIfNeeded(waitForJoin: Bool = false) async {
        guard startupRejoinTask == nil, let attemptID = startupMembershipAttemptID,
              let requestConnection = connectionID else { return }
        await refreshConnection()
        guard startupRejoinTask == nil, connectionID == requestConnection,
              startupMembershipAttemptID == attemptID else { return }
        guard let identity = controllerIdentity else { return }
        guard identity.settings.fleetCredentialOrigin == "adopted" else {
            invalidateStartupMembershipAttempt()
            return
        }
        guard let membership = storedMembership, let token = membership.token, !token.isEmpty else {
            invalidateStartupMembershipAttempt()
            return
        }
        let task = Task { @MainActor [weak self] in
            guard let self else { return false }
            let joined = await self.joinController(address: membership.controllerAddress, token: token,
                                                   boundHosts: membership.boundHosts, quiet: true,
                                                   startupAttemptID: attemptID)
            guard self.connectionID == requestConnection,
                  self.startupMembershipAttemptID == attemptID else { return joined }
            self.startupRejoinTask = nil
            if joined { self.startupMembershipAttemptID = nil }
            return joined
        }
        startupRejoinTask = task
        if waitForJoin { _ = await task.value }
    }

    private func invalidateStartupMembershipAttempt() {
        startupRejoinTask?.cancel()
        startupRejoinTask = nil
        startupMembershipAttemptID = nil
        joiningFleet = false
    }

    func rejoinControllerIfNeeded() async {
        guard let membership = storedMembership, let token = membership.token, !token.isEmpty else { return }
        guard controllerIdentity?.settings.fleetCredentialOrigin == "adopted" else { return }
        await joinController(address: membership.controllerAddress, token: token,
                             boundHosts: membership.boundHosts, quiet: true)
    }

    /// What a launch does once the Hub is ready: learn who this Mac is, then re-join its group.
    ///
    /// One entry point, because splitting it is how the re-join became inert: the launch called
    /// `rejoinControllerIfNeeded()` directly, that function reads
    /// `controllerIdentity?.settings.fleetCredentialOrigin`, and nothing on the startup path had
    /// fetched the identity yet - so the guard saw nil and returned silently on every Mac. The test
    /// passed only because it called `refreshConnection()` by hand first.
    func adoptStartupMembership(_ membership: FleetMembership) async {
        invalidateStartupMembershipAttempt()
        storedMembership = membership
        // The pinned release key travels with the membership: restore it before any release is
        // read, so the first read verifies against the key pinned at the last join.
        releasePublisherKey = membership.releasePublisherKey
        startupMembershipAttemptID = UUID()
        // Read the identity before deciding: the credential origin is what says whether this Mac
        // borrowed somebody else's token and therefore belongs to a group.
        await retryStartupMembershipIfNeeded(waitForJoin: true)
    }

    /// Leave the group this Mac belongs to: stop carrying its token and run on this Mac's own.
    ///
    /// This is the action for a Mac that is being retired, sold or moved, and the only one that
    /// takes the group's credential off it. Telling the controller to drop this Mac from its roster
    /// happens first, while the borrowed token still works; if the controller is off or already
    /// gone, leaving is still this Mac's decision and goes ahead with a line saying so.
    func leaveGroup() async {
        guard let requestConnection = connectionID else { return }
        invalidateStartupMembershipAttempt()
        fleetNote = nil
        var controllerNote = ""
        if let membership = storedMembership, let token = membership.token, !token.isEmpty,
           let nodeID = controllerIdentity?.settings.controllerId.lowercased(),
           let target = try? Self.privateHTTPAddress(membership.controllerAddress) {
            var request = URLRequest(url: target.appendingPathComponent("api/hub/fleet/nodes/\(nodeID)"))
            request.httpMethod = "DELETE"
            request.timeoutInterval = 15
            request.setValue(token, forHTTPHeaderField: "X-Hub-Token")
            if let (_, response) = try? await session.data(for: request),
               let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                controllerNote = " The controller at \(membership.controllerAddress) still lists this Mac; remove it there."
            }
        }
        guard connectionID == requestConnection else { return }
        do {
            var request = try makeRequest(["api", "hub", "fleet", "leave"], method: "POST")
            request.timeoutInterval = 30
            let (_, response) = try await session.data(for: request)
            guard connectionID == requestConnection else { return }
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                throw NativeAPIError.message("The Hub refused to leave the group (\((response as? HTTPURLResponse)?.statusCode ?? 0)).")
            }
            storedMembership = nil
            controllerAddress = ""
            // Leaving the group ends the trust that group had: the pinned release key goes with it.
            releasePublisherKey = nil
            onReleasePublisherKeyChanged?(nil)
            offeredRelease = nil
            offeredReleaseDescriptor = nil
            updateOffered = false
            releaseRefusal = nil
            onControllerJoined?(nil)
            await refreshConnection()
            note(.success, "This Mac has left the group and now runs on its own credential." + controllerNote)
            error = nil
        } catch {
            guard connectionID == requestConnection else { return }
            self.error = Self.actionable(error, action: "Leave the group")
        }
    }

    func leaveNode(_ node: NativeFleetNode) async {
        guard let requestConnection = connectionID else { return }
        do {
            let _: EmptyResponse = try await sendEmpty(
                ["api", "hub", "fleet", "nodes", node.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            fleetNote = "Removed \(node.name) from this controller."
            await refreshFleetNodes()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Remove the node") } }
    }

    /// The address to offer a controller: one it can reach this Mac at.
    ///
    /// A controller on the tailnet is given this Mac's Tailscale address and one on the LAN is
    /// given the LAN address, so the address offered is one that actually routes back.
    static func studioAddress(for controller: URL, port: Int?, boundHosts: [String]) -> String? {
        guard let port else { return nil }
        // Only an address the Hub is actually listening on. Offering a LAN address while the
        // Hub is bound to loopback would produce a join that succeeds and a node the controller
        // can never reach - the exact silent failure this whole feature exists to avoid.
        let allowed = Set(boundHosts.filter { $0 != "127.0.0.1" && $0 != "0.0.0.0" })
        let wildcard = boundHosts.contains("0.0.0.0")
        let candidates = LocalAddresses.current().filter { wildcard || allowed.contains($0.address) }
        guard !candidates.isEmpty else { return nil }
        let controllerIsTailscale = (controller.host ?? "").hasPrefix("100.")
        let preferred = candidates.first { $0.isTailscale == controllerIsTailscale }
        guard let chosen = preferred ?? candidates.first else { return nil }
        return "http://\(chosen.address):\(port)"
    }

    /// Accept only a plain http address on a private, loopback or Tailscale IPv4 literal.
    ///
    /// The controller enforces the same rule on what it stores. Doing it here as well is what
    /// keeps this Mac from being talked into sending its credential somewhere unchecked.
    /// Turn what a person types into the controller's URL.
    ///
    /// Accepts `100.64.0.5:47873`, `100.64.0.5` on its own (the Hub's port is fixed, so the
    /// port is assumed), an `http://` prefix, and a name such as
    /// `mac.tailnet.ts.net:47873`. A name is allowed because MagicDNS is the natural thing
    /// to type on a tailnet; what is refused is an address that is not private - the check
    /// exists so this Mac is never pointed at an arbitrary host on the internet, and a name is
    /// held to the same rule by resolving it first.
    static func privateHTTPAddress(_ text: String) throws -> URL {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw NativeAPIError.message(
                "Enter your controller's address, for example 100.64.0.5:47873.")
        }
        let candidate = trimmed.contains("://") ? trimmed : "http://\(trimmed)"
        guard let components = URLComponents(string: candidate),
              components.scheme?.lowercased() == "http",
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/" else {
            throw NativeAPIError.message(
                "Enter an address like 100.64.0.5:47873 - just the address and the port.")
        }
        // The Hub's port is fixed rather than picked per start, so an address on its own is a
        // complete answer and the operator should not have to know the number.
        let port = components.port ?? ShellConfiguration.defaultPort
        guard (1...65535).contains(port), let url = URL(string: "http://\(host):\(port)") else {
            throw NativeAPIError.message("That port is not a valid number between 1 and 65535.")
        }
        if let literal = ipv4(host) {
            guard isPrivate(literal) else {
                throw NativeAPIError.message("That is not a private or Tailscale address. "
                    + "Enter your controller's LAN or Tailscale address.")
            }
            return url
        }
        let resolved = resolvedIPv4(host)
        guard !resolved.isEmpty else {
            throw NativeAPIError.message("Could not look up \(host). Check the spelling, or type "
                + "your controller's Tailscale address instead.")
        }
        guard resolved.allSatisfy(isPrivate) else {
            throw NativeAPIError.message("\(host) resolves to a public address, so it is not a "
                + "controller on your own network. Use its LAN or Tailscale address.")
        }
        return url
    }

    /// Four decimal octets, or nil when the text is a name rather than an address.
    private static func ipv4(_ host: String) -> [Int]? {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        let numbers = parts.compactMap { Int($0) }
        guard numbers.count == 4, numbers.allSatisfy({ (0...255).contains($0) }) else { return nil }
        return numbers
    }

    /// Private, link-local, or Tailscale's carrier-grade NAT range.
    private static func isPrivate(_ parts: [Int]) -> Bool {
        let first = parts[0], second = parts[1]
        return first == 10 || first == 127
            || (first == 192 && second == 168)
            || (first == 172 && (16...31).contains(second))
            || (first == 100 && (64...127).contains(second))
            || (first == 169 && second == 254)
    }

    /// Every IPv4 address a name resolves to, so a name that points at the internet is refused.
    private static func resolvedIPv4(_ host: String) -> [[Int]] {
        var hints = addrinfo()
        hints.ai_family = AF_INET
        hints.ai_socktype = SOCK_STREAM
        var result: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, nil, &hints, &result) == 0, let head = result else { return [] }
        defer { freeaddrinfo(head) }
        var found: [[Int]] = []
        var pointer: UnsafeMutablePointer<addrinfo>? = head
        while let current = pointer {
            if let address = current.pointee.ai_addr {
                var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                if getnameinfo(address, socklen_t(current.pointee.ai_addrlen),
                               &buffer, socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0,
                   let octets = ipv4(String(cString: buffer)), !found.contains(octets) {
                    found.append(octets)
                }
            }
            pointer = current.pointee.ai_next
        }
        return found
    }

    /// Read the Hub's own role and identity, plus the version it reports.
    /// Ask the controller this Mac joined whether it is answering, and keep that answer visible.
    ///
    /// The result of a join used to exist only as a banner at the top of the page, so an operator
    /// sitting at the join form had nothing to read: they had to scroll away from where they were
    /// working to find out whether they were connected. This is the standing answer, refreshed
    /// whenever the page is opened or a join or leave finishes.
    func refreshControllerHealth() async {
        let address = controllerAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !address.isEmpty else {
            controllerReachable = nil
            controllerHealthNote = "Not joined to a controller."
            return
        }
        guard let target = try? Self.privateHTTPAddress(address) else {
            controllerReachable = false
            controllerHealthNote = "Joined to \(address), but that address cannot be used now."
            return
        }
        var request = URLRequest(url: target.appendingPathComponent("api/health"))
        request.timeoutInterval = 8
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
            let body = (String(data: data, encoding: .utf8) ?? "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if (200..<300).contains(http.statusCode) {
                controllerReachable = true
                controllerHealthNote = "Joined to \(address) and connected. \(body.prefix(140))"
            } else {
                controllerReachable = false
                controllerHealthNote = "Joined to \(address), but it answered HTTP \(http.statusCode)."
            }
        } catch {
            controllerReachable = false
            controllerHealthNote = "Joined to \(address), but it is not answering now. "
                + Self.describe(error)
        }
    }

    func refreshConnection() async {
        guard let requestConnection = connectionID else { return }
        defer { Task { await self.refreshControllerHealth() } }
        do {
            async let identity: NativeControllerIdentity = get(["api", "hub", "controller"])
            async let version: NativeVersion = get(["api", "version"])
            let (identityValue, versionValue) = try await (identity, version)
            guard connectionID == requestConnection else { return }
            controllerIdentity = identityValue
            hubVersion = versionValue
        } catch {
            guard connectionID == requestConnection else { return }
            controllerIdentity = nil
            hubVersion = nil
        }
        // Read the release offer on the same visit that reads the connection state, so the banner
        // sits next to what it belongs to. It never installs anything: the operator presses a button.
        guard connectionID == requestConnection else { return }
        await refreshRelease()
    }

    /// Read what the controller currently offers and decide whether this Mac may install it.
    ///
    /// The first successful read of the controller's identity **pins** its key and does not verify
    /// the release in the same pass: a key fetched at the same moment as the artifact proves
    /// nothing, so the caller is told to check again. Later reads verify the countersignature with
    /// the stored pin, and a different key presented later is refused rather than adopted.
    func refreshRelease() async {
        guard let requestConnection = connectionID else { return }
        do {
            let presented = try await readReleaseIdentity()
            guard connectionID == requestConnection else { return }
            if let existing = releasePublisherKey {
                guard existing.fingerprint == presented.fingerprint else {
                    let descriptor = try? await readRelease()
                    offeredRelease = descriptor?.offered
                    offeredReleaseDescriptor = descriptor
                    updateOffered = false
                    releaseRefusal = "The controller now presents a different release-signing key (\(presented.shortFingerprint)…) than the one pinned on this Mac (\(existing.shortFingerprint)…). A pinned key is only replaced by an explicit action on this Mac, so no update can be installed."
                    return
                }
            } else {
                // Pin it, then wait for the next check to verify anything against it.
                releasePublisherKey = presented
                onReleasePublisherKeyChanged?(presented)
                let descriptor = try? await readRelease()
                guard connectionID == requestConnection else { return }
                offeredRelease = descriptor?.offered
                offeredReleaseDescriptor = descriptor
                updateOffered = false
                releaseRefusal = "This Mac just pinned the controller's release-signing key (\(presented.shortFingerprint)…). Check again to verify the release against it — a key read at the same time as an artifact proves nothing."
                return
            }
            guard let pinnedKey = releasePublisherKey else { return }
            guard let descriptor = try await readRelease() else {
                offeredRelease = nil
                offeredReleaseDescriptor = nil
                updateOffered = false
                releaseRefusal = nil
                return
            }
            guard connectionID == requestConnection else { return }
            offeredRelease = descriptor.offered
            offeredReleaseDescriptor = descriptor
            guard descriptor.controllerKeyId == pinnedKey.fingerprint else {
                updateOffered = false
                releaseRefusal = "The release names controller key \(descriptor.controllerKeyId.prefix(12))…, which is not the key pinned on this Mac (\(pinnedKey.shortFingerprint)…). Refusing it."
                return
            }
            guard ReleaseSignature.verifyControllerSignature(
                descriptor: descriptor.raw,
                signatureBase64: descriptor.controllerSignature,
                publicKeyHex: pinnedKey.publicKey) else {
                updateOffered = false
                releaseRefusal = "The controller's signature on this release does not verify against the key pinned on this Mac. Refusing it."
                return
            }
            if case .unsupported(let reason) = ReleaseArtifactForm.classify(filename: descriptor.filename) {
                updateOffered = false
                releaseRefusal = reason
                return
            }
            guard let ownBuild = currentAppBuild, !ownBuild.isEmpty else {
                updateOffered = false
                releaseRefusal = "This app cannot read its own StudioHubBuild, so it cannot tell whether the offered release is different."
                return
            }
            if descriptor.build == ownBuild {
                updateOffered = false
                releaseRefusal = nil
            } else {
                updateOffered = true
                releaseRefusal = nil
            }
        } catch {
            guard connectionID == requestConnection else { return }
            updateOffered = false
            releaseRefusal = Self.actionable(error, action: "Read the update offer")
        }
    }

    /// Install the offered release, once the operator has pressed the button.
    ///
    /// Order is the contract's: verify the controller countersignature with the pinned key before
    /// trusting anything else, download the artifact to a temporary file (streamed to disk, never
    /// held in memory), verify the SHA-256 of those bytes, then swap. Nothing is installed
    /// silently, and the whole thing is abandoned on the first failure.
    func installOfferedRelease() async {
        guard !installingRelease else { return }
        guard updateOffered, let descriptor = offeredReleaseDescriptor, let pinned = releasePublisherKey else {
            note(.failure, releaseRefusal ?? "No verified update is being offered.")
            return
        }
        installingRelease = true
        releaseProgress = "Verifying the controller's signature…"
        defer {
            installingRelease = false
            releaseProgress = nil
        }
        do {
            guard descriptor.controllerKeyId == pinned.fingerprint,
                  ReleaseSignature.verifyControllerSignature(
                    descriptor: descriptor.raw,
                    signatureBase64: descriptor.controllerSignature,
                    publicKeyHex: pinned.publicKey) else {
                throw ReleaseError("The controller's signature on this release does not verify against the key pinned on this Mac. Nothing was downloaded or installed.")
            }
            if case .unsupported(let reason) = ReleaseArtifactForm.classify(filename: descriptor.filename) {
                throw ReleaseError(reason)
            }
            releaseProgress = "Downloading \(descriptor.filename) — \(descriptor.offered.sizeDisplay)…"
            let artifact = try await downloadReleaseArtifact(descriptor: descriptor)
            defer { try? FileManager.default.removeItem(at: artifact.deletingLastPathComponent()) }
            releaseProgress = "Verifying the downloaded bytes…"
            try Self.verifyArtifactFile(artifact, descriptor: descriptor)
            releaseProgress = "Installing \(descriptor.version) (\(descriptor.build))…"
            let landed = try installVerifiedArtifact(artifact, descriptor)
            guard landed == descriptor.build else {
                throw ReleaseError("The app that landed reports build \(landed), not \(descriptor.build).")
            }
            // The running process is still the old build; treat the installed one as current so the
            // banner does not offer the update we just took. A relaunch reads the new Info.plist.
            currentAppBuild = landed
            updateOffered = false
            releaseRefusal = nil
            note(.success, "Installed Studio Hub \(descriptor.version) (\(landed)). Restarting the local services now — running work will stop.")
            onReleaseRestartRequested?()
            await refreshRelease()
        } catch {
            note(.failure, Self.actionable(error, action: "Install the update"))
            await refreshRelease()
        }
    }

    /// Stream the artifact to a private temporary file. Uses URLSession's download task, which
    /// writes to disk, so a 1.2 GB image is never held in memory.
    private func downloadReleaseArtifact(descriptor: ReleaseDescriptor) async throws -> URL {
        let requestConnection = connectionID
        var request = try makeRequest(["api", "hub", "release", "artifact"])
        request.timeoutInterval = 600
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("studiohub-update-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let destination = directory.appendingPathComponent(descriptor.filename)
        do {
            let (temporary, response) = try await session.download(for: request)
            guard connectionID == requestConnection else {
                throw ReleaseError("The local engine connection changed while the update was downloading.")
            }
            try validateResponse(data: Data(), response: response, request: request)
            // The route advertises the digest; the signed descriptor's sha256 stays authoritative
            // and is checked over the bytes below. A disagreement here is refused all the same.
            if let announced = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "X-Content-SHA256"),
               announced.lowercased() != descriptor.sha256 {
                throw ReleaseError("The controller's artifact route advertises SHA-256 \(announced), not the signed \(descriptor.sha256). Nothing was installed.")
            }
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: temporary, to: destination)
        } catch let error as ReleaseError {
            try? FileManager.default.removeItem(at: directory)
            throw error
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw ReleaseError("Could not download the update: \(Self.describe(error))")
        }
        return destination
    }

    /// The signed descriptor's SHA-256 over the bytes on disk. Throws with a plain sentence on any
    /// mismatch; a corruption check, not the trust decision.
    static func verifyArtifactFile(_ file: URL, descriptor: ReleaseDescriptor) throws {
        let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.int64Value
        guard size == descriptor.bytes else {
            throw ReleaseError("The downloaded update is \(size.map(String.init) ?? "an unknown number of") bytes but the signed descriptor says \(descriptor.bytes). Nothing was installed.")
        }
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 4 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        guard digest == descriptor.sha256 else {
            throw ReleaseError("The downloaded update is not the artifact the controller signed: its SHA-256 is \(digest.prefix(16))…, not \(descriptor.sha256.prefix(16))…. Nothing was installed.")
        }
    }

    private func readReleaseIdentity() async throws -> ReleasePublisherKey {
        let data = try await performData(makeRequest(["api", "hub", "release", "identity"]))
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ReleaseError("The controller's release identity was not a JSON object.")
        }
        guard object["algorithm"] as? String == "ed25519" else {
            throw ReleaseError("The controller signs releases with \(object["algorithm"] as? String ?? "an unknown algorithm"), not ed25519.")
        }
        guard let publicKey = (object["public_key"] as? String).flatMap(ReleaseSignature.normalizedHex),
              let fingerprint = (object["fingerprint"] as? String).flatMap(ReleaseSignature.normalizedHex),
              ReleaseSignature.fingerprint(publicKeyHex: publicKey) == fingerprint else {
            throw ReleaseError("The controller's release identity is not a 64-hex Ed25519 key whose fingerprint matches.")
        }
        return ReleasePublisherKey(publicKey: publicKey, fingerprint: fingerprint)
    }

    private func readRelease() async throws -> ReleaseDescriptor? {
        let data = try await performData(makeRequest(["api", "hub", "release"]))
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let release = object["release"] else {
            throw ReleaseError("The controller's release answer was not JSON with a release field.")
        }
        if release is NSNull { return nil }
        guard let dictionary = release as? [String: Any], let descriptor = ReleaseDescriptor(json: dictionary) else {
            throw ReleaseError("The controller holds a release whose descriptor does not match the agreed schema. Refusing it.")
        }
        return descriptor
    }

    /// Show a credential without publishing it. The app already holds this token to talk to
    /// its own Hub; masking keeps it out of screenshots, support logs and the status line
    /// until the operator asks for it.
    static func maskedToken(_ token: String) -> String {
        guard token.count > 12 else { return String(repeating: "•", count: max(token.count, 8)) }
        return "\(token.prefix(4))\(String(repeating: "•", count: 8))\(token.suffix(4))"
    }

    private static func releaseSummary(_ result: NativeMemoryChange) -> String {
        let held = result.results.filter { $0.result == "still_loaded" }
        if result.succeeded == 0 && held.isEmpty { return "No Studio had a model to release." }
        let busy = result.results.filter { $0.result == "busy" }.map(\.id)
        let failed = result.results.filter {
            $0.ok != true && $0.result != "busy" && $0.result != "still_loaded"
        }.map(\.id)
        var parts = ["Released model memory in \(result.succeeded) of \(result.selected) studios."]
        // A Studio that answered success while still holding its model is the one case the
        // operator must not read as "freed": it is exactly the memory this control exists for.
        if !held.isEmpty {
            parts.append("Still holding a model after reporting success: "
                         + held.map(\.id).joined(separator: ", ") + ".")
        }
        if !busy.isEmpty { parts.append("Busy, so left alone: \(busy.joined(separator: ", ")).") }
        if !failed.isEmpty { parts.append("Could not reach: \(failed.joined(separator: ", ")).") }
        return parts.joined(separator: " ")
    }

    private static func modeSummary(_ result: NativeMemoryChange) -> String {
        let fixed = result.results.filter { $0.result == "fixed" }.map(\.id)
        var parts = ["Updated \(result.succeeded) of \(result.selected) studios."]
        if !fixed.isEmpty {
            parts.append("\(fixed.joined(separator: ", ")) releases inside every job, so it needs no setting.")
        }
        if result.failed > 0 { parts.append("\(result.failed) could not be changed.") }
        return parts.joined(separator: " ")
    }

    func removeModel(_ model: NativeModel) async {
        error = "Hub model removal is unavailable until it can protect models with active readers."
    }

    /// Fetch one completed voice clip, checked against the result the Hub recorded.
    ///
    /// Take on the cluster credential, so this Mac accepts work from its controller.
    ///
    /// Best-effort by design: a failure here leaves the node registered but unable to take work,
    /// which the controller reports as unreachable, and re-joining fixes it. Failing the join
    /// instead would be worse - the controller would have a node it cannot reach and the operator
    /// would see only that registration failed, with the credential already half adopted.
    private func adoptClusterCredential(_ token: String) async throws {
        let body = try JSONEncoder().encode(["token": token])
        var request = try makeRequest(["api", "hub", "fleet", "adopt"], method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 30
        request.httpBody = body
        _ = try await performData(request)
    }

    /// The Hub records the byte count, the SHA-256 and the decoded WAV shape when it adopts a
    /// finished voice job, so a clip that arrives truncated or altered is refused here rather
    /// than played or saved as if it were the generation. The RIFF/WAVE header is required for
    /// the same reason the Image path requires a PNG magic number: bytes that are not audio
    /// must never reach the operator's disk under a .wav name.
    func audioData(for job: NativeJob) async throws -> Data {
        guard let requestConnection = connectionID else { throw NativeAPIError.message("Start the local services before retrieving audio.") }
        guard job.state == "done", audioJobs.contains(where: { $0.id == job.id }),
              let bytes = job.bytes, bytes > 0, let sha = job.sha256,
              sha.count == 64, sha.allSatisfy({ $0.isHexDigit }) else {
            throw NativeAPIError.message("This voice clip has no verified completed result. Refresh its job before playing or saving it.")
        }
        let request = try makeRequest(["api", "hub", "jobs", job.id, "items", "0", "artifact"])
        let data = try await performData(request)
        guard connectionID == requestConnection else { throw NativeAPIError.message("The local engine connection changed before audio retrieval completed.") }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard Int64(data.count) == bytes, digest == sha.lowercased(),
              data.count >= 12, data.prefix(4) == Data([0x52, 0x49, 0x46, 0x46]),
              data[8..<12] == Data([0x57, 0x41, 0x56, 0x45]) else {
            throw NativeAPIError.message("Voice clip verification failed. Refresh the job and retrieve its original result again.")
        }
        return data
    }

    /// Save one voice clip and confirm delivery, in that order.
    ///
    /// Saving used to write the bytes and stop, which left two holes: a damaged clip could be
    /// written silently, and the Hub was never told the operator had taken the result, so the
    /// protected copy was never released for cleanup and a Mac's storage grew forever. Only a
    /// verified save reaches the receipt, exactly as the Image and Subtitle paths do.
    func saveAudio(_ job: NativeJob, to destination: URL) async throws {
        let requestConnection = connectionID
        let data = try await audioData(for: job)
        try data.write(to: destination, options: .atomic)
        guard try Data(contentsOf: destination) == data else {
            throw NativeAPIError.message("Saved voice clip verification failed. Choose another destination and save again.")
        }
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The voice clip was saved and verified, but the local service connection changed before delivery was acknowledged. Save again to confirm delivery.")
        }
        do {
            let receipt: HubAckReceipt = try await sendEmpty(
                ["api", "hub", "jobs", job.id, "items", "0", "ack"], method: "POST")
            if !receipt.ok { throw NativeAPIError.message("rejected") }
        } catch {
            throw NativeAPIError.message("The voice clip was saved and verified, but its delivery was not acknowledged. Save again to confirm delivery; the protected copy is not deleted until acknowledgement succeeds.")
        }
    }

    func transcriptData(format: NativeTranscriptFormat) throws -> Data {
        guard let result = transcriptionResult else { throw NativeAPIError.message("There is no completed transcript to export.") }
        let value: String
        switch format { case .text: value = result.text; case .srt: value = result.srt; case .vtt: value = result.vtt }
        return Data(value.utf8)
    }

    /// Save one transcript and confirm delivery, in that order.
    ///
    /// The Image studio has done this since ST-03; Subtitle exported bytes and never told
    /// the Hub, so a delivered transcript stayed indexed as unacknowledged. The Hub publishes
    /// exactly one artifact per item — the SRT — and that is what the delivery receipt is
    /// about, so it is fetched and required to match what the app is showing before anything
    /// is written or acknowledged.
    func saveTranscript(format: NativeTranscriptFormat, to destination: URL) async throws {
        let requestConnection = connectionID
        guard let result = transcriptionResult else {
            throw NativeAPIError.message("There is no completed transcript to export.")
        }
        guard let batchID = result.batchID else {
            throw NativeAPIError.message("This transcript is no longer linked to its Hub job, so its delivery cannot be confirmed. Run the transcription again to export it.")
        }
        let artifact = try await performData(makeRequest(
            ["api", "hub", "transcription", "jobs", batchID, "items", "0", "artifact"]))
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The Hub connection changed before transcript retrieval completed.")
        }
        let hubSRT = String(decoding: artifact, as: UTF8.self)
        guard !hubSRT.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              hubSRT == result.srt else {
            throw NativeAPIError.message("Subtitle verification failed. The Hub's artifact does not match the transcript shown; refresh the job and retrieve it again.")
        }
        let data = try transcriptData(format: format)
        try data.write(to: destination, options: .atomic)
        guard try Data(contentsOf: destination) == data else {
            throw NativeAPIError.message("Saved transcript verification failed. Choose another destination and save again.")
        }
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The transcript was saved and verified, but the local service connection changed before delivery was acknowledged. Save again to confirm delivery.")
        }
        do {
            let receipt: HubAckReceipt = try await sendEmpty(
                ["api", "hub", "transcription", "jobs", batchID, "items", "0", "ack"],
                method: "POST")
            if !receipt.ok { throw NativeAPIError.message("rejected") }
        } catch {
            throw NativeAPIError.message("The transcript was saved and verified, but its delivery was not acknowledged. Save again to confirm delivery; the protected copy is not deleted until acknowledgement succeeds.")
        }
    }

    /// Save a Hugging Face token through the Hub, so downloads of gated models are faster and
    /// authenticated. The worker stores it; the app only ever sends it and shows it masked.
    func saveHFToken(_ token: String) async -> Bool {
        guard let requestConnection = connectionID else { return false }
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { error = "Enter a token first."; return false }
        do {
            let body = TokenRequest(hfToken: trimmed)
            let updated: TokenStatusWire = try await sendJSON(
                ["studio", "voice", "api", "settings"], method: "POST", body: body)
            guard connectionID == requestConnection else { return false }
            tokenStatus = NativeTokenStatus(isSet: updated.value.isSet, masked: updated.value.masked)
            error = nil
            return true
        } catch {
            guard connectionID == requestConnection else { return false }
            self.error = Self.actionable(error, action: "Save the Hugging Face token")
            return false
        }
    }

    /// Ask Hugging Face who the token belongs to. A token that cannot be checked is refused
    /// here rather than failing later in the middle of a 12 GB download.
    func testHFToken(_ token: String) async -> NativeTokenTest? {
        guard let requestConnection = connectionID else { return nil }
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            // An empty token tests the one already saved, which is what "Test Token" means
            // when the operator has not typed a replacement.
            let value: NativeTokenTest = try await sendJSON(
                ["studio", "voice", "api", "settings", "test-hf-token"], method: "POST",
                body: TokenRequest(hfToken: trimmed))
            guard connectionID == requestConnection else { return nil }
            error = nil
            return value
        } catch {
            guard connectionID == requestConnection else { return nil }
            self.error = Self.actionable(error, action: "Test the Hugging Face token")
            return nil
        }
    }

    private func usableModel(repo: String, function: StudioFunction) throws -> NativeModel {
        guard let model = models.first(where: { $0.repo == repo && $0.function == function }) else {
            throw NativeAPIError.message("Choose a \(function.rawValue.lowercased()) model from the connected library.")
        }
        guard model.supported else { throw NativeAPIError.message(model.supportNote) }
        guard model.cached else { throw NativeAPIError.message("Download \(model.label) in Models before using it.") }
        guard model.runtimeReady else { throw NativeAPIError.message("\(model.label) is installed, but its local runtime is unavailable.") }
        return model
    }

    private func reconcileSelections(defaultTranscription: String?) {
        if !models.contains(where: { $0.repo == audioModel && $0.function == .audio && $0.supported }) {
            audioModel = Self.qwenBase
        }
        if !models.contains(where: { $0.repo == transcriptionModel && $0.function == .transcription && $0.supported }) {
            transcriptionModel = models.first {
                $0.repo == defaultTranscription && $0.function == .transcription && $0.supported
            }?.repo ?? Self.whisperTurbo
        }
        if let selected = models.first(where: { $0.repo == transcriptionModel && $0.function == .transcription }),
           !selected.supportsWordTimestamps { wordTimestamps = false }
        // A Music model that leaves the catalogue (or is not runnable here) must not stay selected,
        // or the page shows controls for a model the Hub will refuse.
        if !models.contains(where: { $0.repo == musicModel && $0.function == .music && $0.supported }) {
            musicModel = models.first { $0.function == .music && $0.supported }?.repo ?? ""
        }
    }

    /// Which way a model is told what to sound like.
    ///
    /// Qwen3-TTS ships three tiers that are told apart only by their name: Base clones, CustomVoice
    /// takes preset speakers, VoiceDesign takes a description. VoxCPM2 and OmniVoice clone. The
    /// runtime's declared capability is trusted where it settles the question, and the name is used
    /// for the Qwen tiers because the capability list is identical across them.
    /// Not private: the contract test drives it directly, and a rule this easy to get wrong
    /// deserves a test rather than only a UI to exercise it through.
    static func voiceMode(repo: String, family: String, declaredClone: Bool,
                          isCloneFamily: Bool) -> VoiceMode {
        let name = (repo.split(separator: "/").last.map(String.init) ?? repo).lowercased()
        if name.contains("voicedesign") { return .design }
        if name.contains("customvoice") { return .preset }
        if name.contains("base") { return .cloning }
        if isCloneFamily || declaredClone { return .cloning }
        // A Qwen tier with no name hint and no clone capability is the preset form: it is the only
        // Qwen mode that needs nothing but a speaker name. Anything else keeps the clone form,
        // which is the stricter of the two.
        if family == "qwen3-tts" { return .preset }
        return .cloning
    }

    /// The audio models for one tab, best first: installed and ready, then installed, then the
    /// rest. A tab with nothing usable still shows what to install rather than an empty page.
    func audioModels(for mode: VoiceMode) -> [NativeModel] {
        models.filter { $0.function == .audio && $0.voiceMode == mode }
            .sorted { left, right in
                func rank(_ model: NativeModel) -> Int {
                    if model.cached && model.runtimeReady { return 0 }
                    if model.cached { return 1 }
                    return 2
                }
                return rank(left) == rank(right)
                    ? left.label.localizedCaseInsensitiveCompare(right.label) == .orderedAscending
                    : rank(left) < rank(right)
            }
    }

    private static func combineModels(
        _ audio: [CatalogModel],
        _ transcription: [TranscriptionModel],
        transcriptionAvailable: Bool,
        speakers: [String],
        packages: [String: HubModelPackage]
    ) -> [NativeModel] {
        let audioModels = audio.filter { $0.capabilities?.contains("tts") == true }.map { item in
            let family = item.family ?? ""
            let isQwen = family == "qwen3-tts"
            // Voice-cloning families the packaged Voice worker supports. Qwen CustomVoice is the
            // preset-speaker path; Qwen Base, VoxCPM2 and OmniVoice clone from a reference clip.
            let isVoxCPM = family.hasPrefix("voxcpm")
            let isOmniVoice = family == "omnivoice"
            let isCloneFamily = isVoxCPM || isOmniVoice
            // Prefer the runtime's own capability claim; fall back to the model name only if the
            // catalog omits it, so a catalog change cannot silently drift from the UI.
            let declaredClone = item.referenceAudio?.supported ?? item.referenceAudio?.declared ?? false
            let isClone = isCloneFamily
                || (isQwen && (declaredClone || item.repo.localizedCaseInsensitiveContains("Base")))
            // Qwen Base conditions on the reference transcript; VoxCPM2 and OmniVoice do not.
            let transcriptRequired = isClone
                && (item.referenceAudio?.transcript ?? (isQwen ? "required" : "optional")) == "required"
            // Which tab this model belongs in. Read from the name and the family, because that is
            // what decides the engine's mode; `capabilities` cannot distinguish the three Qwen
            // tiers, which is why the name is consulted at all.
            let mode = Self.voiceMode(repo: item.repo, family: family,
                                      declaredClone: declaredClone, isCloneFamily: isCloneFamily)
            let formCompatible = isQwen || isCloneFamily
            let package = packages[item.repo]
            let supported = formCompatible && package != nil
            let cached = item.cache?.state == "cached"
            let block = Self.blockReason(memoryWarning: item.memoryWarning,
                                         memoryEligible: item.memoryEligible, package: package)
            let ready = (item.runtimeReady ?? false) && block == nil
                && package?.dispatchState == "eligible"
            let qualified = item.repo == qwenCustomVoice || Self.qwenBaseRepos.contains(item.repo)
            let note = supported
                ? (cached && ready
                    ? (qualified
                        ? (isClone
                            ? "Requested Qwen Base voice — clones from a reference clip and its exact transcript."
                            : "Tested local Qwen CustomVoice workflow.")
                        : "Compatible Qwen contract; this exact model has not completed the native pilot test.")
                    : "Native Audio compatible; install the model and ensure its runtime is ready.")
                : (package == nil
                    ? "This model is outside the packaged model catalog."
                    : "This native Audio form does not yet expose the model-specific controls required by this runtime entry.")
            return NativeModel(
                id: "audio:\(item.repo)", repo: item.repo, label: item.label, family: item.family ?? "Audio",
                function: .audio, cached: cached, supported: supported, supportNote: note, sizeGB: item.sizeGb,
                capabilities: item.capabilities ?? [], languages: item.languages?.values ?? [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: (supported && mode == .preset) ? speakers : [],
                requiresReference: isClone,
                // Without this every model kept the struct's default of `.cloning`, so the preset
                // and design tabs were empty and a CustomVoice model was sent down the clone path -
                // which asked for a reference voice it does not have.
                voiceMode: mode, requiresTranscript: transcriptRequired, blockReason: block,
                paused: package?.paused == true, pausedReason: package?.pausedReason,
                longForm: NativeLongFormDelivery(item.longFormDelivery),
                pausedStudio: package?.pausedStudio
            )
        }
        let transcriptionModels = transcription.map { item in
            let package = packages[item.repo]
            // The standalone Subtitle worker reports engine "subtitle-mlx"; the retained Voice
            // worker reports "whisper". Accept either, or the Subtitle screen can never select a
            // model even though the worker reports it ready.
            let engine = (item.engine ?? "").lowercased()
            let supported = (engine.contains("whisper") || engine.contains("subtitle")) && package != nil
            // The transcription catalog carries no memory tier, so its only gates are the ones
            // the package row reports.
            let block = Self.blockReason(memoryWarning: nil, memoryEligible: nil, package: package)
            let ready = transcriptionAvailable && item.cached == true && block == nil
            let qualified = item.repo == whisperTurbo
            let note = supported
                ? (ready
                    ? (qualified ? "Tested local Whisper transcription workflow." : "Compatible Whisper contract; this exact model has not completed the native pilot test.")
                    : "Native Transcription compatible; download this model and ensure the transcription runtime is ready.")
                : (package == nil ? "This model is outside the packaged model catalog." :
                    "This native Transcription form has not been qualified for this runtime entry yet.")
            return NativeModel(
                id: "transcription:\(item.repo)", repo: item.repo, label: item.label, family: item.engine ?? "Transcription",
                function: .transcription, cached: item.cached ?? false, supported: supported, supportNote: note,
                sizeGB: item.sizeGb, capabilities: ["transcription"], languages: item.languageList,
                runtimeReady: ready, supportsWordTimestamps: item.supportsWordTimestamps ?? false, speakers: [],
                blockReason: block, paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio
            )
        }
        let imageModels = audio.filter { $0.capabilities?.contains("txt2img") == true }.map { item in
            let package = packages[item.repo]
            let supported = item.imageConfiguration != nil && package != nil
            let block = Self.blockReason(memoryWarning: item.memoryWarning,
                                         memoryEligible: item.memoryEligible, package: package)
            let ready = item.runtimeReady == true && block == nil
            return NativeModel(id: "image:\(item.repo)", repo: item.repo, label: item.label,
                family: item.family ?? "Image", function: .image, cached: item.cache?.state == "cached",
                supported: supported, supportNote: ready && supported ? "Ready for local image generation." : "Verify the image model files and runtime in Models.",
                sizeGB: item.sizeGb, capabilities: item.capabilities ?? [], languages: [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: [], memoryWarning: item.memoryWarning,
                blockReason: block, paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio)
        }
        return audioModels + transcriptionModels + imageModels
            + Self.musicModels(audio, packages: packages)
    }

    /// The Music models the Hub catalogue exposes.
    ///
    /// The standalone Music worker publishes ACE-Step 1.5 with `capabilities` led by `music` and the
    /// `generation_defaults`/`generation_limits` the form needs. A model with no decodable Music
    /// configuration is not offered, because the app has nothing truthful to put on the form.
    private static func musicModels(_ catalog: [CatalogModel],
                                    packages: [String: HubModelPackage]) -> [NativeModel] {
        catalog.filter { $0.capabilities?.contains("music") == true }.map { item in
            let package = packages[item.repo]
            let supported = item.musicConfiguration != nil && package != nil
            let block = Self.blockReason(memoryWarning: item.memoryWarning,
                                         memoryEligible: item.memoryEligible, package: package)
            let cached = item.cache?.state == "cached"
            let ready = item.runtimeReady == true && block == nil && package?.dispatchState == "eligible"
            let note = supported
                ? (cached && ready
                    ? "Ready for local music generation."
                    : "Native Music compatible; install the model and ensure its runtime is ready.")
                : (package == nil
                    ? "This model is outside the packaged model catalog."
                    : "This native Music form has no compatible generation controls for this runtime entry.")
            return NativeModel(
                id: "music:\(item.repo)", repo: item.repo, label: item.label,
                family: item.family ?? "Music", function: .music, cached: cached, supported: supported,
                supportNote: note, sizeGB: item.sizeGb, capabilities: item.capabilities ?? [],
                languages: [], runtimeReady: ready, supportsWordTimestamps: false, speakers: [],
                memoryWarning: item.memoryWarning, blockReason: block,
                paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio,
                sampleRateHz: item.sampleRateHz, channels: item.channels)
        }
    }

    private static func mapJob(_ wire: JobWire) -> NativeJob {
        NativeJob(
            id: wire.id, state: wire.state, progress: wire.progress ?? 0, text: wire.params?.text ?? "",
            model: wire.params?.repo ?? "", createdAt: wire.createdAt,
            outputURL: wire.state == "done" ? wire.outputUrl : nil, error: wire.error
        )
    }
    /// Why a model cannot run on this Mac, from the gates the Hub reports.
    ///
    /// The Hub refuses a job when any gate is shut, and until now the app showed only the shadow of
    /// that refusal: a model reading "unavailable" with no reason, which the owner read as "because
    /// of RAM pressure or something". The gates are independent - files, runtime, the macOS floor,
    /// the chip, the studio process - and each has a different fix, so each gets its own sentence.
    private static func blockReason(memoryWarning: String?, memoryEligible: Bool?,
                                    package: HubModelPackage?) -> String? {
        // The one an operator cannot fix by clicking anything: this Mac is below the model's tier.
        if let warning = memoryWarning, !warning.isEmpty { return warning }
        if memoryEligible == false { return "This Mac does not have enough memory for this model." }
        guard let package else { return "This model is not part of the packaged catalog." }
        // A pause outranks the other gates: while it is set the Hub refuses every job, whatever the
        // files and the runtime say, and the only way back is the recheck button next to it.
        if package.paused == true {
            let why = package.pausedReason ?? "it failed repeatedly"
            return "Paused by the Hub after repeated failures — \(why). Fix the cause, then press Recheck."
        }
        if let detail = package.reason, !detail.isEmpty { return detail }
        if let files = package.filesState, files != "files_verified" {
            return "Its files are not verified. Open Models and press Verify Files."
        }
        if package.runtimeState == "incompatible" {
            return "The local runtime for this model is not available in this build."
        }
        if package.osState == "unsupported" {
            let needs = package.minimumMacos ?? ""
            let has = package.macosVersion ?? ""
            return needs.isEmpty
                ? "This Mac's version of macOS is older than this model needs."
                : "This Mac is on macOS \(has); this model needs \(needs) or later."
        }
        if package.chipState == "unsupported" { return "This model needs an Apple Silicon Mac." }
        if package.workerState == "unavailable" {
            return "Its Studio is not running. Restart the local services."
        }
        if package.dispatchState == "ineligible" { return "This Mac cannot run this model right now." }
        return nil
    }

    private static func mapHubJob(_ wire: HubJobDetail) -> NativeJob {
        let item = wire.items.first
        return NativeJob(id: wire.id, state: item?.state ?? "queued", progress: item?.progress ?? 0,
            text: item?.prompt ?? "", model: wire.model, createdAt: wire.createdAt,
            outputURL: item?.state == "done" ? item?.artifactUrl : nil, error: item?.error, bytes: item?.bytes, sha256: item?.sha256,
            imageSettings: item?.params?.imageSettings,
            musicSettings: item?.params?.musicSettings,
            durationSeconds: item?.audioDurationS,
            requestedWidth: item?.params?.width, requestedHeight: item?.params?.height,
            generationSeconds: item?.runtimeS, runningSeconds: item?.elapsedS,
            progressStage: item?.progressStage,
            itemIndex: item?.index ?? 0)
    }

    private static func mapDownload(_ wire: DownloadWire) -> NativeDownload {
        NativeDownload(
            id: wire.id, repo: wire.repo, state: wire.state, percent: wire.percent ?? 0,
            downloadedBytes: wire.bytesDone ?? wire.bytesObserved ?? 0, totalBytes: wire.bytesTotal ?? 0, error: wire.error
        )
    }

    /// Accept a plain http address on this Mac's loopback or on the one address the operator
    /// chose to bind. Anything else is refused: the app talks to its own Hub, never to an
    /// arbitrary host it was pointed at.
    private static func validatedBase(_ url: URL, allowedHost: String = "127.0.0.1") throws -> URL {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == "http", components.host == allowedHost,
              let port = components.port, (1...65535).contains(port),
              components.user == nil, components.password == nil, components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/" else {
            throw NativeAPIError.message("The Hub address must be http://\(allowedHost):<port> with no extra path.")
        }
        return URL(string: "http://\(allowedHost):\(port)")!
    }

    private func makeRequest(_ components: [String], method: String = "GET") throws -> URLRequest {
        guard let baseURL else { throw NativeAPIError.message("Start the local services before using this action.") }
        var url = baseURL
        for component in components { url.appendPathComponent(component) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let hubToken { request.setValue(hubToken, forHTTPHeaderField: "X-Hub-Token") }
        return request
    }

    private func get<T: Decodable>(_ components: [String]) async throws -> T {
        try await perform(makeRequest(components))
    }

    private func sendJSON<T: Decodable, Body: Encodable>(_ components: [String], method: String, body: Body) async throws -> T {
        var request = try makeRequest(components, method: method)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(body)
        return try await perform(request)
    }
    private func sendHubJSON<T: Decodable, Body: Encodable>(_ components: [String], body: Body) async throws -> T {
        var request = try makeRequest(components, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // Same encoding as every other JSON request in this app. This call used a bare
        // `JSONEncoder()` while `sendJSON` converted keys to snake_case, so a job envelope left
        // for the Hub as `voiceReferenceAssetId` while the Hub reads
        // `voice_reference_asset_id`. The Hub ignored the reference, dispatched the Voice job with
        // nothing attached, and every Qwen3-TTS Base clone failed with "Voice cloning needs a
        // reference voice or private reference upload". `clientRequestId` was lost the same way,
        // which also defeated the Hub's replay protection: a resubmit started a second batch
        // instead of recognising the first.
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(body)
        return try await perform(request)
    }

    private func sendEmpty<T: Decodable>(_ components: [String], method: String) async throws -> T {
        try await perform(makeRequest(components, method: method))
    }

    private func perform<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data = try await performData(request)
        do {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(T.self, from: data)
        } catch {
            throw NativeAPIError.message("The local engine returned an unexpected response for \(request.url?.path ?? "the request").")
        }
    }

    private func performUpload<T: Decodable>(_ request: URLRequest, fromFile file: URL) async throws -> T {
        let requestConnection = connectionID
        do {
            let (data, response) = try await session.upload(for: request, fromFile: file)
            guard connectionID == requestConnection else { throw NativeAPIError.message("The local engine connection changed before the upload completed.") }
            try validateResponse(data: data, response: response, request: request)
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(T.self, from: data)
        } catch let error as NativeAPIError { throw error }
        catch { throw NativeAPIError.message("Could not upload to the local engine: \(error.localizedDescription)") }
    }

    private func performData(_ request: URLRequest) async throws -> Data {
        let requestConnection = connectionID
        do {
            let (data, response) = try await session.data(for: request)
            guard connectionID == requestConnection else { throw NativeAPIError.message("The local engine connection changed before the request completed.") }
            try validateResponse(data: data, response: response, request: request)
            return data
        } catch let error as NativeAPIError { throw error }
        catch { throw NativeAPIError.message("Could not reach the local engine: \(error.localizedDescription)") }
    }

    private func validateResponse(data: Data, response: URLResponse, request: URLRequest) throws {
        guard let http = response as? HTTPURLResponse else { throw NativeAPIError.message("The local engine returned no HTTP response.") }
        guard let final = http.url,
              final.scheme == "http", final.host == baseURL?.host, final.port == baseURL?.port else {
            throw NativeAPIError.message("The local engine attempted to redirect outside the address it was reached at.")
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = Self.errorDetail(data) ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw NativeAPIError.message("Local engine HTTP \(http.statusCode): \(detail)")
        }
    }

    private static func errorDetail(_ data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let detail = object["detail"] else { return nil }
        if let text = detail as? String { return text }
        if let dictionary = detail as? [String: Any] {
            return dictionary["message"] as? String ?? dictionary["detail"] as? String
        }
        if let items = detail as? [[String: Any]] {
            return items.compactMap { $0["msg"] as? String }.joined(separator: "; ")
        }
        return nil
    }

    private static func actionable(_ error: Error, action: String) -> String {
        "\(action) failed. \(describe(error))"
    }

    /// The message plus, for a network failure, the code that names the kind of failure:
    /// -1004 cannot connect, -1009 no network, -1022 App Transport Security, -1200 TLS,
    /// -1001 timed out. Without the number, a report like "it says something about security"
    /// cannot be told apart from a refused connection or a wrong address.
    static func describe(_ error: Error) -> String {
        if let urlError = error as? URLError {
            return "\(urlError.localizedDescription) (code \(urlError.errorCode))"
        }
        return error.localizedDescription
    }

    /// Ask the controller's Hub whether it is there, before any token is sent to it.
    ///
    /// This separates the two questions a join failure mixes together: "can this Mac reach the
    /// controller at all?" and "will it accept the token?". `/api/health` needs no credential,
    /// so a failure here is the network, the address or the Mac - never the token.
    func testControllerAddress(_ address: String) async {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            note(.failure, "Type the controller's address first, then test it.")
            return
        }
        do {
            let target = try Self.privateHTTPAddress(trimmed)
            var request = URLRequest(url: target.appendingPathComponent("api/health"))
            request.timeoutInterval = 10
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                note(.failure, "\(trimmed) answered, but not like a Hub.")
                return
            }
            let body = (String(data: data, encoding: .utf8) ?? "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if (200..<300).contains(http.statusCode) {
                note(.success, "Reached the controller at \(target.host ?? trimmed):"
                     + "\(target.port ?? 0) — HTTP \(http.statusCode). \(body.prefix(160))")
            } else {
                note(.failure, "\(trimmed) answered HTTP \(http.statusCode), which is not a "
                     + "Hub answering its health check. \(body.prefix(160))")
            }
        } catch {
            note(.failure, "Could not reach \(trimmed). \(Self.describe(error))")
        }
    }

    private nonisolated static func makeMultipartFile(
        boundary: String,
        file: URL,
        fields: [String: String],
        dataDirectory: URL,
        fileField: String = "files"
    ) throws -> URL {
        let uploadDirectory = dataDirectory.appendingPathComponent("Uploads", isDirectory: true)
        try FileManager.default.createDirectory(
            at: uploadDirectory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let upload = uploadDirectory.appendingPathComponent(UUID().uuidString + ".multipart")
        guard FileManager.default.createFile(atPath: upload.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
            throw NativeAPIError.message("Could not prepare the local transcription upload.")
        }
        var completed = false
        defer { if !completed { try? FileManager.default.removeItem(at: upload) } }
        let output = try FileHandle(forWritingTo: upload)
        defer { try? output.close() }
        func append(_ string: String) throws { try output.write(contentsOf: Data(string.utf8)) }
        for (name, value) in fields where !value.isEmpty {
            try append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n")
        }
        let filename = file.lastPathComponent.replacingOccurrences(of: "\"", with: "")
        try append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(filename)\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        let input = try FileHandle(forReadingFrom: file)
        defer { try? input.close() }
        while let chunk = try input.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            try output.write(contentsOf: chunk)
        }
        try append("\r\n--\(boundary)--\r\n")
        completed = true
        return upload
    }

    private struct SavedState: Codable {
        let audioText: String
        let audioModel: String
        /// Absent in state written before the three voice tabs existed; absent means cloning.
        let audioMode: String?
        let audioDesignPrompt: String?
        /// Absent in state written before the pace control existed; absent means the engine's pace.
        let audioSpeed: Double?
        let audioSpeaker: String
        let audioLanguage: String
        let transcriptionModel: String
        let transcriptionFile: String?
        let transcriptionLanguage: String
        let wordTimestamps: Bool
        let transcriptionResult: NativeTranscript?
        let imagePrompt: String?
        let imageModel: String?
        let imageSettings: NativeImageSettings?
        /// Music draft. Absent in state written before the Music workflow existed, so an older file
        /// still loads with an empty Music form.
        let musicPrompt: String?
        let musicModel: String?
        let musicSettings: NativeMusicSettings?
        /// Absent in state written before pinning existed, and absent means automatic.
        let routingPin: String?
    }
    private var pendingAudioURL: URL? { dataDirectory?.appendingPathComponent("native-pending-audio.json") }
    private func savePendingAudio() throws {
        guard let pendingAudioURL else { return }
        if let pendingAudio {
            try JSONEncoder().encode(pendingAudio).write(to: pendingAudioURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: pendingAudioURL.path)
        } else { try? FileManager.default.removeItem(at: pendingAudioURL) }
    }
    private func loadPendingAudio() throws {
        guard let pendingAudioURL, FileManager.default.fileExists(atPath: pendingAudioURL.path) else { return }
        pendingAudio = try JSONDecoder().decode(HubAudioRequest.self, from: Data(contentsOf: pendingAudioURL))
    }
    private var pendingImageURL: URL? { dataDirectory?.appendingPathComponent("native-pending-image.json") }
    private func savePendingImage() throws {
        guard let pendingImageURL else { return }
        if let pendingImage {
            try JSONEncoder().encode(pendingImage).write(to: pendingImageURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: pendingImageURL.path)
        } else { try? FileManager.default.removeItem(at: pendingImageURL) }
    }
    private func loadPendingImage() throws {
        pendingImage = nil
        guard let pendingImageURL, FileManager.default.fileExists(atPath: pendingImageURL.path) else { return }
        pendingImage = try JSONDecoder().decode(HubImageRequest.self, from: Data(contentsOf: pendingImageURL))
    }
    private var pendingMusicURL: URL? { dataDirectory?.appendingPathComponent("native-pending-music.json") }
    private func savePendingMusic() throws {
        guard let pendingMusicURL else { return }
        if let pendingMusic {
            try JSONEncoder().encode(pendingMusic).write(to: pendingMusicURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: pendingMusicURL.path)
        } else { try? FileManager.default.removeItem(at: pendingMusicURL) }
    }
    private func loadPendingMusic() throws {
        pendingMusic = nil
        guard let pendingMusicURL, FileManager.default.fileExists(atPath: pendingMusicURL.path) else { return }
        pendingMusic = try JSONDecoder().decode(HubMusicRequest.self, from: Data(contentsOf: pendingMusicURL))
    }
    private var pendingTranscriptionURL: URL? { dataDirectory?.appendingPathComponent("native-pending-transcription.json") }
    private func savePendingTranscription() throws {
        guard let pendingTranscriptionURL else { return }
        if let pendingTranscription {
            try JSONEncoder().encode(pendingTranscription).write(to: pendingTranscriptionURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: pendingTranscriptionURL.path)
        } else { try? FileManager.default.removeItem(at: pendingTranscriptionURL) }
    }
    private func loadPendingTranscription() throws {
        guard let pendingTranscriptionURL, FileManager.default.fileExists(atPath: pendingTranscriptionURL.path) else { return }
        pendingTranscription = try JSONDecoder().decode(PendingTranscription.self, from: Data(contentsOf: pendingTranscriptionURL))
    }
    private var hubDownloadRunsURL: URL? { dataDirectory?.appendingPathComponent("native-model-downloads.json") }
    private func saveHubDownloadRuns() throws {
        guard let hubDownloadRunsURL else { return }
        try JSONEncoder().encode(hubDownloadRuns).write(to: hubDownloadRunsURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: hubDownloadRunsURL.path)
    }
    private func loadHubDownloadRuns() throws {
        guard let hubDownloadRunsURL, FileManager.default.fileExists(atPath: hubDownloadRunsURL.path) else { return }
        hubDownloadRuns = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: hubDownloadRunsURL))
    }

    private var stateURL: URL? { dataDirectory?.appendingPathComponent("native-workspaces.json") }

    private func scheduleSave() {
        guard !loadingState, dataDirectory != nil else { return }
        saveTask?.cancel()
        saveTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(200))
            guard !Task.isCancelled else { return }
            try? self?.saveState()
        }
    }

    private func saveState() throws {
        guard let stateURL else { return }
        let state = SavedState(
            audioText: audioText, audioModel: audioModel,
            audioMode: audioMode.rawValue, audioDesignPrompt: audioDesignPrompt,
            audioSpeed: audioSpeed,
            audioSpeaker: audioSpeaker, audioLanguage: audioLanguage,
            transcriptionModel: transcriptionModel, transcriptionFile: transcriptionFile?.path,
            transcriptionLanguage: transcriptionLanguage, wordTimestamps: wordTimestamps,
            transcriptionResult: transcriptionResult, imagePrompt: imagePrompt, imageModel: imageModel, imageSettings: imageSettings,
            musicPrompt: musicPrompt, musicModel: musicModel, musicSettings: musicSettings,
            routingPin: routingPin
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(state).write(to: stateURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: stateURL.path)
    }

    private func loadState() throws {
        guard let stateURL, FileManager.default.fileExists(atPath: stateURL.path) else { return }
        let state = try JSONDecoder().decode(SavedState.self, from: Data(contentsOf: stateURL))
        loadingState = true
        defer { loadingState = false }
        audioText = state.audioText
        audioModel = state.audioModel
        audioSpeaker = state.audioSpeaker
        audioLanguage = state.audioLanguage
        // Absent in state written before the tabs existed, and absent means cloning, which is what
        // every audio model in the earlier state did.
        audioMode = state.audioMode.flatMap(VoiceMode.init(rawValue:)) ?? .cloning
        audioDesignPrompt = state.audioDesignPrompt ?? ""
        // Absent means 1.0, and a value from an older or edited file that is out of range is pulled
        // back to the contract's bounds rather than refused - a saved pace is not worth an error.
        audioSpeed = min(max(state.audioSpeed ?? 1.0, Self.audioSpeedRange.lowerBound),
                         Self.audioSpeedRange.upperBound)
        transcriptionModel = state.transcriptionModel
        if let path = state.transcriptionFile, FileManager.default.fileExists(atPath: path) { transcriptionFile = URL(fileURLWithPath: path) }
        transcriptionLanguage = state.transcriptionLanguage
        wordTimestamps = state.wordTimestamps
        transcriptionResult = state.transcriptionResult
        imagePrompt = state.imagePrompt ?? ""
        imageModel = state.imageModel ?? ""
        imageSettings = state.imageSettings
        musicPrompt = state.musicPrompt ?? ""
        musicModel = state.musicModel ?? ""
        musicSettings = state.musicSettings
        routingPin = state.routingPin
    }
}

private struct CacheWire: Decodable { let state: String? }
private struct CatalogResponse: Decodable { let models: [CatalogModel] }
private struct HubModelPackagesResponse: Decodable { let models: [HubModelPackage] }
private struct HubModelPackage: Decodable {
    let packageId: String
    let modelId: String
    let state: String
    let dispatchState: String
    /// The gates the Hub checks before it accepts a job for this model. `/api/hub/model-packages`
    /// has always sent them; this struct decoded four fields and dropped the rest, which is why a
    /// model this Mac cannot run showed as "unavailable" with no reason anywhere in the app.
    let filesState: String?
    let runtimeState: String?
    let osState: String?
    let chipState: String?
    let workerState: String?
    let minimumMacos: String?
    let macosVersion: String?
    let reason: String?
    /// Set by the Hub's own circuit breaker, not by this worker.
    let paused: Bool?
    let pausedReason: String?
    let pausedStudio: String?
}
/// How the Voice worker groups its cached files, and the memory each family's model declares.
private struct HubStorageGroups: Decodable { let groups: [HubStorageGroup] }
private struct HubStorageGroup: Decodable {
    let id: String
    let label: String
    let bytesTotal: Int64?
    let memory: HubStorageMemory?
}
private struct HubStorageMemory: Decodable {
    let requiredGb: Int
    let thisMacGb: Int
    let runsHere: Bool
}
private struct HubModelDownload: Decodable { let id: String; let packageId: String; let state: String }
private struct HubModelImport: Encodable { let sourceFolder: String }
private struct RenameRequest: Codable { let name: String }
private struct ModelHealthRecheck: Encodable { let model: String; let studio: String? }
private struct HubModelRecheck: Decodable { let ok: Bool }

private struct RoleChange: Encodable { let role: String }
private struct JoinRequest: Encodable {
    let nodeId: String
    let name: String
    let address: String
    /// What this Mac runs. The controller builds its dispatch targets from this, so a join
    /// without it would register a node that nothing can send work to.
    let studios: [JoinStudio]
}
private struct JoinStudio: Encodable {
    let id: String
    let modality: String
}
private struct HubWorkersResponse: Decodable { let workers: [HubWorker] }
private struct HubWorker: Decodable {
    let id: String
    let modality: String
}
private struct StorageLimitsBody: Encodable {
    let maxBytes: Int64
    let retentionDays: Int
    let cleanupEnabled: Bool
}
private struct StoragePreviewBody: Encodable {
    let maxBytes: Int64
    let retentionDays: Int
    let cleanupEnabled: Bool
    let cleanupIntervalSeconds: Int
    let minimumFreeBytes: Int64
}
/// The Hub returns the package's readiness row; only the fields the app reads are declared.
private struct HubModelState: Decodable {
    let packageId: String
    let state: String
    let dispatchState: String?
    let reason: String?
    /// The gates the Hub checks before it will accept a job for this model. They were parsed as far
    /// as `dispatchState` and no further, which is why a model this Mac cannot run showed as
    /// "unavailable" and nothing said which gate was shut.
    let filesState: String?
    let runtimeState: String?
    let osState: String?
    let chipState: String?
    let workerState: String?
    let minimumMacos: String?
    let macosVersion: String?
}
private struct EmptyBody: Encodable {}
private struct CatalogReferenceAudio: Decodable {
    let declared: Bool?
    let supported: Bool?
    let transcript: String?
}

private struct CatalogModel: Decodable {
    let repo: String
    let label: String
    let family: String?
    let sizeGb: Double?
    let capabilities: [String]?
    let languages: StringOrStrings?
    let runtimeReady: Bool?
    let memoryEligible: Bool?
    /// Why this Mac is below the model's qualified tier, when it is.
    let memoryWarning: String?
    let cache: CacheWire?
    /// The generation controls the model publishes. Decoded as a loose bag rather than as one
    /// concrete shape: Image and Music publish different control sets under the same key, and a
    /// mismatch in one modality must not fail the whole catalogue request. The two per-function
    /// views below are the only readers.
    let generationDefaults: HubGenerationDefaults?
    let generationLimits: HubGenerationLimits?
    let imageSizes: [NativeImageSize]?
    /// The fixed WAV shape a Music model publishes. Absent for every other function.
    let sampleRateHz: Int?
    let channels: Int?
    /// Family capability claim from the runtime, so clone vs preset is decided by the catalog
    /// rather than by guessing from the model name.
    let referenceAudio: CatalogReferenceAudio?
    /// How this model is sectioned, how its speed reaches the engine, and the range a person may
    /// choose. Published per model by the worker; it was in the payload all along and the app
    /// decoded none of it, so "how is a long script chunked for this model?" had no answer in the
    /// app at all.
    let longFormDelivery: HubLongFormDelivery?
    var imageConfiguration: NativeImageConfiguration? {
        guard let defaults = generationDefaults?.imageSettings,
              let limits = generationLimits?.imageLimits,
              let sizes = imageSizes, !sizes.isEmpty,
              limits.minSteps > 0, limits.maxSteps >= limits.minSteps,
              limits.minGuidance.isFinite, limits.maxGuidance.isFinite, limits.minGuidance <= limits.maxGuidance,
              limits.minSeed <= limits.maxSeed,
              sizes.allSatisfy({ $0.width > 0 && $0.height > 0 }), Set(sizes.map(\.id)).count == sizes.count else { return nil }
        let value = NativeImageConfiguration(defaults: defaults, sizes: sizes, limits: limits)
        return value.accepts(defaults) ? value : nil
    }
    var musicConfiguration: NativeMusicConfiguration? {
        guard let defaults = generationDefaults?.musicSettings,
              let limits = generationLimits?.musicLimits,
              limits.minDurationSeconds > 0, limits.maxDurationSeconds >= limits.minDurationSeconds,
              limits.maxSteps >= limits.minSteps,
              limits.minGuidance.isFinite, limits.maxGuidance.isFinite, limits.minGuidance <= limits.maxGuidance,
              limits.minShift.isFinite, limits.maxShift.isFinite, limits.minShift <= limits.maxShift,
              limits.minSeed <= limits.maxSeed else { return nil }
        let value = NativeMusicConfiguration(defaults: defaults, limits: limits)
        return value.accepts(defaults) ? value : nil
    }
}

/// The catalogue's `generation_defaults` object, decoded field by field.
///
/// Image publishes `width`/`height`/`steps`/`guidance`/`seed`; Music publishes
/// `duration_seconds`/`steps`/`guidance_scale`/`shift`/`seed`/`instrumental`/`lyrics`/
/// `vocal_language`. Both arrive under the same `generation_defaults` key, so a concrete type for
/// one modality would make the other's entry fail the entire catalogue decode.
private struct HubGenerationDefaults: Decodable {
    let width: Int?
    let height: Int?
    let steps: Int?
    let guidance: Double?
    let guidanceScale: Double?
    let seed: Int64?
    let durationSeconds: Double?
    let shift: Double?
    let instrumental: Bool?
    let lyrics: String?
    let vocalLanguage: String?
    var imageSettings: NativeImageSettings? {
        guard let width, let height, let steps, let guidance, let seed else { return nil }
        return NativeImageSettings(width: width, height: height, steps: steps, guidance: guidance, seed: seed)
    }
    var musicSettings: NativeMusicSettings? {
        guard let durationSeconds, let steps, let guidanceScale, let seed else { return nil }
        return NativeMusicSettings(
            durationSeconds: durationSeconds, steps: steps, guidanceScale: guidanceScale,
            shift: shift ?? 3, seed: seed, instrumental: instrumental ?? true,
            lyrics: lyrics ?? "[Instrumental]", vocalLanguage: vocalLanguage ?? "unknown")
    }
}

/// The catalogue's `generation_limits` object, decoded field by field for the same reason.
private struct HubGenerationLimits: Decodable {
    let minSteps: Int?
    let maxSteps: Int?
    let minGuidance: Double?
    let maxGuidance: Double?
    let minSeed: Int64?
    let maxSeed: Int64?
    let minDurationSeconds: Double?
    let maxDurationSeconds: Double?
    let minShift: Double?
    let maxShift: Double?
    var imageLimits: NativeImageLimits? {
        guard let minSteps, let maxSteps, let minGuidance, let maxGuidance, let minSeed, let maxSeed else { return nil }
        return NativeImageLimits(minSteps: minSteps, maxSteps: maxSteps,
                                 minGuidance: minGuidance, maxGuidance: maxGuidance,
                                 minSeed: minSeed, maxSeed: maxSeed)
    }
    var musicLimits: NativeMusicLimits? {
        guard let minDurationSeconds, let maxDurationSeconds, let minSteps, let maxSteps,
              let minGuidance, let maxGuidance, let minSeed, let maxSeed else { return nil }
        return NativeMusicLimits(minDurationSeconds: minDurationSeconds, maxDurationSeconds: maxDurationSeconds,
                                 minSteps: minSteps, maxSteps: maxSteps,
                                 minGuidance: minGuidance, maxGuidance: maxGuidance,
                                 minShift: minShift ?? 0, maxShift: maxShift ?? 20,
                                 minSeed: minSeed, maxSeed: maxSeed)
    }
}
/// How one model is sectioned and how its speed control is delivered.
struct HubLongFormDelivery: Decodable {
    let sectionMaxCharacters: Int?
    let joinPauseMilliseconds: Int?
    let hardCapCharacters: Int?
    let source: String?
    let note: String?
    let speed: HubSpeedPolicy?
    let sectionSizeControl: HubSectionSizeControl?
}
struct HubSpeedPolicy: Decodable {
    let delivery: String?
    let minimum: Double?
    let maximum: Double?
    let note: String?
}
struct HubSectionSizeControl: Decodable {
    let minimum: Int
    let maximum: Int
    let step: Int
    let defaultCustom: Int
    let runtimeDefault: Int
}

struct NativeLongFormDelivery: Equatable {
    let sectionMaxCharacters: Int
    let joinPauseMilliseconds: Int
    let hardCapCharacters: Int?
    let source: String
    let note: String
    let speedDelivery: String
    let speedMinimum: Double
    let speedMaximum: Double
    let chosenSectionMaxCharacters: Int
    let sectionMinimum: Int
    let sectionMaximum: Int

    /// The model's default, or nil when the payload has no sectioning policy at all.
    init?(_ wire: HubLongFormDelivery?) {
        guard let wire, let section = wire.sectionMaxCharacters else { return nil }
        sectionMaxCharacters = section
        joinPauseMilliseconds = wire.joinPauseMilliseconds ?? 0
        hardCapCharacters = wire.hardCapCharacters
        source = wire.source ?? "runtime_default"
        note = wire.note ?? ""
        speedDelivery = wire.speed?.delivery ?? "tempo"
        speedMinimum = wire.speed?.minimum ?? 0.5
        speedMaximum = wire.speed?.maximum ?? 2.0
        let control = wire.sectionSizeControl
        chosenSectionMaxCharacters = control?.runtimeDefault ?? section
        sectionMinimum = control?.minimum ?? section
        sectionMaximum = control?.maximum ?? section
    }
}

private struct TranscriptionAvailability: Decodable {
    let available: Bool?
    let defaultModel: String?
    let models: [TranscriptionModel]
}
private struct TranscriptionModel: Decodable {
    let repo: String
    let label: String
    let sizeGb: Double?
    let engine: String?
    let languages: StringOrStrings?
    let supportsWordTimestamps: Bool?
    let recommended: Bool?
    let cached: Bool?
    var languageList: [String] { languages?.values ?? [] }
}
private enum StringOrStrings: Decodable {
    case string(String), strings([String])
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let values = try? container.decode([String].self) { self = .strings(values) }
        else { self = .string(try container.decode(String.self)) }
    }
    var values: [String] { switch self { case .string(let value): return [value]; case .strings(let values): return values } }
}
private struct GenerationAvailability: Decodable { let qwen3PresetSpeakers: [GenerationSpeaker]? }
private struct GenerationSpeaker: Decodable { let id: String }
private struct JobsResponse: Decodable { let jobs: [JobWire] }
private struct HubJobsResponse: Decodable { let batches: [HubJobSummary] }
private struct HubJobSummary: Decodable { let id: String; let modality: String }
private struct HubJobDetail: Decodable {
    let id: String
    let model: String
    let createdAt: Double?
    let items: [HubJobItem]
}
private struct HubJobItem: Decodable {
    let state: String
    /// The item's position in the batch, which is what the discard route is addressed with.
    let index: Int?
    let progress: Double?
    /// The phase label the studio reported with its progress, when it reports one.
    let progressStage: String?
    let prompt: String?
    let artifactUrl: String?
    let error: String?
    let bytes: Int64?
    let sha256: String?
    let params: HubJobParams?
    /// How long the produced audio runs. The Hub records it when it adopts a finished voice
    /// job, and the app showed none of it - a generated clip had no stated length.
    let audioDurationS: Double?
    /// How long the generation itself took. `runtime_s` is written when an item finishes; a
    /// running job reports `elapsed_s` instead, so the operator sees a clock either way.
    ///
    /// This is not the length of the clip it produced, which is what the rows used to show and
    /// what the owner read as "how long it takes": a 4.6-second clip that took 52 seconds to make
    /// showed only "4.6 s".
    let runtimeS: Double?
    let elapsedS: Double?
}
private struct HubJobParams: Decodable {
    let width: Int?
    let height: Int?
    let steps: Int?
    let guidance: Double?
    let seed: Int64?
    /// Music's own controls. The Hub echoes the accepted item params on the public job, so a
    /// finished Music job can show what it was asked for and an uncertain one can be matched to the
    /// request that produced it.
    let durationSeconds: Double?
    let guidanceScale: Double?
    let shift: Double?
    let instrumental: Bool?
    let lyrics: String?
    let vocalLanguage: String?
    var imageSettings: NativeImageSettings? {
        guard let width, let height, let steps, let guidance, let seed else { return nil }
        return NativeImageSettings(width: width, height: height, steps: steps, guidance: guidance, seed: seed)
    }
    var musicSettings: NativeMusicSettings? {
        guard let durationSeconds, let steps, let guidanceScale, let shift, let seed,
              let instrumental, let lyrics, let vocalLanguage else { return nil }
        return NativeMusicSettings(durationSeconds: durationSeconds, steps: steps,
                                   guidanceScale: guidanceScale, shift: shift, seed: seed,
                                   instrumental: instrumental, lyrics: lyrics,
                                   vocalLanguage: vocalLanguage)
    }
}
private struct HubJobSubmit: Decodable { let batchId: String; let replayed: Bool? }
private struct HubCancelResponse: Decodable { let ok: Bool }
/// `POST /api/hub/jobs/{id}/items/{index}/discard` answers with the abandoned item.
private struct HubDiscardResponse: Decodable { let ok: Bool }
/// Both Image and Music reconciliation return this shape; the route and the modality differ.
private struct HubReconciliation: Decodable { let reconciled: Bool; let clientRequestId: String? }
private struct HubImageRequest: Codable {
    let modality: String
    let model: String
    let clientRequestId: String
    let items: [HubImageItem]
    /// Overrides the Hub's automatic choice of Studio. Absent means automatic.
    let routing: String?
    enum CodingKeys: String, CodingKey {
        case modality, model, clientRequestId, items, routing
    }
    init(modality: String, model: String, clientRequestId: String, items: [HubImageItem],
         routing: String?) {
        self.modality = modality
        self.model = model
        self.clientRequestId = clientRequestId
        self.items = items
        self.routing = routing
    }
}
private struct HubImageItem: Codable { let prompt: String; let params: NativeImageSettings }
/// One Music generation on the same durable Hub route Image uses.
///
/// The Hub's `broker.MODALITY["music"]` maps this envelope to the worker's
/// `/api/generate/txt2music` and lifts each item's `params` onto the worker body, so the encoded
/// keys (`duration_seconds`, `guidance_scale`, `shift`, `vocal_language`, ...) are exactly what the
/// Music worker's `validate_request` reads.
private struct HubMusicRequest: Codable {
    let modality: String
    let model: String
    let clientRequestId: String
    let items: [HubMusicItem]
    /// Overrides the Hub's automatic choice of Studio. Absent means automatic.
    let routing: String?
    init(modality: String, model: String, clientRequestId: String, items: [HubMusicItem],
         routing: String?) {
        self.modality = modality
        self.model = model
        self.clientRequestId = clientRequestId
        self.items = items
        self.routing = routing
    }
}
private struct HubMusicItem: Codable { let prompt: String; let params: NativeMusicSettings }
private struct HubAckReceipt: Decodable { let ok: Bool }
private struct HubAudioRequest: Codable {
    let modality: String
    let model: String
    let clientRequestId: String
    let items: [HubAudioItem]
    /// Staged voice-clone reference. Sent for Qwen Base models; the Hub streams the clip to the
    /// worker's reference endpoint and supplies the transcript from the staged asset.
    let voiceReferenceAssetId: String?
    /// A voice already saved in the Voice library, used instead of a staged clip. The worker reads
    /// the clip, its transcript and its timings from its own store, so nothing has to be uploaded
    /// and a voice the operator can see in **Voices** can be used directly.
    let voiceLibraryId: String?
    /// Overrides the Hub's automatic choice of Studio. Absent means automatic.
    let routing: String?
    // No CodingKeys on purpose: every Hub request body is encoded with
    // `.convertToSnakeCase`, and a hand-written mapping here silently overrode it once already.
    // The key this decodes to is `voice_reference_asset_id`, pinned by a test.
}

/// Response of POST /api/hub/voice-references (one contract, local or GenStudio origin).
private struct HubVoiceReference: Decodable {
    let assetId: String
    let sha256: String
    let bytes: Int
    let transcriptSupplied: Bool?
    let origin: String?
}
private struct HubAudioItem: Codable { let text: String; let params: HubAudioParams }
private struct HubAudioParams: Codable {
    let presetSpeaker: String
    let language: String
    /// How fast to speak. The worker bounds this to 0.5-2.0 and applies tempo to the finished
    /// audio, so pitch is preserved; 1.0 is the engine's own pace and is sent as-is.
    var speed: Double
    /// Sent only by a voice-design model. Encoded as `voice_design_prompt`; the Hub and the Voice
    /// worker both read that name.
    var designPrompt: String?
}
private struct PendingTranscription: Codable {
    let episode: String
    let itemID: String
    let filePath: String
    let model: String
    let language: String
    let wordTimestamps: Bool
    let batchID: String?
    func withBatchID(_ id: String) -> Self {
        Self(episode: episode, itemID: itemID, filePath: filePath, model: model,
             language: language, wordTimestamps: wordTimestamps, batchID: id)
    }
}
private struct HubTranscriptionSubmit: Decodable { let batchId: String }
private struct VoiceListResponse: Decodable { let voices: [NativeVoice] }
/// The Subtitle worker's synchronous answer: the words, and when they were said.
private struct HubSyncTranscript: Decodable {
    let text: String?
    let language: String?
    let duration: Double?
    /// Timed sections, when the engine produced them. A Qwen3-TTS Base reference longer than the
    /// model's maximum clip length needs these: the engine selects a bounded, speech-aligned
    /// window and needs to know which words fall inside it.
    let segments: [HubTranscriptSegment]?
}
private struct HubTranscriptSegment: Codable {
    let start: Double?
    let end: Double?
    let text: String?
    /// What the voice engine reads. Kept beside the decoded spelling rather than replacing it, so
    /// the same value serves both the app's own use and the reference contract.
    var wire: [String: Any] {
        var value: [String: Any] = [:]
        if let start { value["start_seconds"] = start }
        if let end { value["end_seconds"] = end }
        if let text, !text.isEmpty { value["text"] = text }
        return value
    }
}
private struct VoiceResponse: Decodable { let voice: NativeVoice }
private struct VoiceDeleteResponse: Decodable { let deleted: Bool? }
private struct VoiceEdit: Encodable {
    let name: String
    let language: String
    let gender: String
    let license: String
    let notes: String
    let transcript: String
}
private struct HubTranscriptionList: Decodable { let batches: [HubTranscriptionSummary] }
private struct HubTranscriptionSummary: Decodable {
    let id: String
    let project: String?
    let episode: String?
    /// Present on the list route; used for the Subtitle history so the studio's own jobs are
    /// visible without opening each one.
    let status: String?
    let model: String?
    let createdAt: Double?
    let label: String?
}
private struct HubTranscriptionDetail: Decodable {
    let model: String
    let items: [HubTranscriptionItem]
}
private struct HubTranscriptionItem: Decodable {
    let state: String
    let artifactUrl: String?
    let metadata: HubTranscriptMetadata?
}
private struct HubTranscriptMetadata: Decodable {
    let text: String?
    let vtt: String?
    let language: String?
    let duration: Double?
    let model: String?
}
private struct JobResponse: Decodable { let job: JobWire }
private struct JobWire: Decodable {
    let id: String
    let state: String
    let progress: Double?
    let params: JobParameters?
    let outputUrl: String?
    let error: String?
    let createdAt: Double?
}
private struct JobParameters: Decodable { let text: String?; let repo: String? }
private struct AudioRequest: Encodable {
    let repo: String
    let text: String
    let presetSpeaker: String
    let language: String
}
private struct DownloadsResponse: Decodable { let jobs: [DownloadWire] }
private struct DownloadWire: Decodable {
    let id: String
    let repo: String
    let state: String
    let percent: Double?
    let bytesDone: Int64?
    let bytesObserved: Int64?
    let bytesTotal: Int64?
    let error: String?
}
private struct DownloadRequest: Encodable { let repo: String }
private struct DownloadStartResponse: Decodable { let alreadyCached: Bool? }
private struct DownloadCancelResponse: Decodable { let job: DownloadWire? }
private struct TranscriptWire: Decodable {
    let text: String
    let srt: String
    let vtt: String
    let language: String
    let duration: Double
    let model: String?
    func value(sourceFilename: String) -> NativeTranscript {
        NativeTranscript(text: text, srt: srt, vtt: vtt, language: language, duration: duration, model: model, sourceFilename: sourceFilename)
    }
}
private struct TokenStatusWire: Decodable {
    let hfTokenSet: Bool
    let hfTokenMasked: String?
    var value: NativeTokenStatus { NativeTokenStatus(isSet: hfTokenSet, masked: hfTokenMasked ?? "") }
}
private struct TokenRequest: Encodable { let hfToken: String }
private struct TokenTestWire: Decodable { let name: String?; let type: String?; let orgs: [String]? }
private struct EmptyResponse: Decodable {}
