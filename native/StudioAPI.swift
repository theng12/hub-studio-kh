import Combine
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
    /// The runtime reports this as "required" for every Base tier: a clone fails closed on a
    /// transcript mismatch, so the app must not submit without one.
    var requiresTranscript: Bool = true
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
    let source: String
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

struct NativeTokenTest: Equatable {
    let name: String?
    let type: String?
    let organizations: [String]
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
final class NativeWorkspaceStore: ObservableObject {
    static let qwenCustomVoice = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
    /// Requested Qwen3-TTS Base (voice-cloning) models. These clone from a reference clip and
    /// its exact transcript instead of using preset speakers.
    static let qwenBaseRepos: Set<String> = [
        "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
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
    /// Which Mac new work should run on, as the Hub's `routing` value: nil means the Hub picks,
    /// and `machine:<id>` pins one machine. Kept as the owner's standing choice so a pin set on
    /// the fleet screen applies to every workspace until it is cleared.
    @Published var routingPin: String? { didSet { scheduleSave() } }
    @Published private(set) var submittingImage = false
    var imageConfiguration: NativeImageConfiguration? { imageConfigurations[imageModel] }

    @Published private(set) var downloads: [NativeDownload] = []
    @Published private(set) var audioSpeakers: [String] = []
    @Published var audioText = "" { didSet { scheduleSave() } }
    @Published var audioModel = qwenCustomVoice { didSet { scheduleSave() } }
    @Published var audioSpeaker = "" { didSet { scheduleSave() } }
    @Published var audioLanguage = "en" { didSet { scheduleSave() } }
    /// Local reference clip for Qwen Base voice cloning. Choosing a new clip or editing the
    /// transcript invalidates the staged asset, because the Hub derives the asset id from both.
    @Published var audioReferencePath: String? { didSet { invalidateStagedReference() } }
    @Published var audioReferenceTranscript = "" { didSet { invalidateStagedReference() } }
    @Published private(set) var audioReferenceAssetId: String?
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
    @Published private(set) var fleetNote: String?
    @Published private(set) var joiningFleet = false
    @Published private(set) var tokenStatus = NativeTokenStatus(isSet: false, masked: "")

    private let session: URLSession
    private let redirectDelegate: RejectRedirects
    private var baseURL: URL?
    private var hubToken: String?
    private var connectionID: UUID?
    private var dataDirectory: URL?
    private var pollTask: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var loadingState = false
    private var pendingAudio: HubAudioRequest?
    private var pendingImage: HubImageRequest?
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
            try loadState()
            try loadPendingAudio()
            try loadPendingImage()
            try loadPendingTranscription()
            try loadHubDownloadRuns()
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
    }

    func refresh() async { await refresh(reportErrors: true) }

    private func refresh(reportErrors: Bool) async {
        guard baseURL != nil, let requestConnection = connectionID, !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            async let catalog: CatalogResponse = get(["api", "hub", "catalog"])
            async let packages: HubModelPackagesResponse = get(["api", "hub", "model-packages"])
            async let transcription: TranscriptionAvailability = get(["studio", "subtitle", "api", "transcribe", "availability"])
            async let generation: GenerationAvailability = get(["studio", "voice", "api", "generate", "availability"])
            async let jobs: HubJobsResponse = get(["api", "hub", "jobs"])
            async let transfers: DownloadsResponse = get(["studio", "voice", "api", "downloads"])
            async let settings: TokenStatusWire = get(["studio", "voice", "api", "settings"])
            async let subtitleHistory: HubTranscriptionList = get(["api", "hub", "transcription", "jobs"])
            let (catalogValue, packagesValue, transcriptionValue, generationValue, jobsValue, transfersValue, settingsValue, subtitleValue) =
                try await (catalog, packages, transcription, generation, jobs, transfers, settings, subtitleHistory)
            guard connectionID == requestConnection else { return }
            modelPackages = Dictionary(uniqueKeysWithValues: packagesValue.models.map { ($0.modelId, $0) })
            audioSpeakers = (generationValue.qwen3PresetSpeakers ?? []).map(\.id)
            if audioSpeaker.isEmpty || (!audioSpeakers.isEmpty && !audioSpeakers.contains(audioSpeaker)) {
                audioSpeaker = audioSpeakers.first ?? "Ryan"
            }
            models = Self.combineModels(
                catalogValue.models,
                transcriptionValue.models,
                transcriptionAvailable: transcriptionValue.available ?? false,
                speakers: audioSpeakers,
                packages: modelPackages
            )
            imageConfigurations = Dictionary(uniqueKeysWithValues: catalogValue.models.compactMap { model in
                model.imageConfiguration.map { (model.repo, $0) }
            })
            if imageConfigurations[imageModel] == nil,
               let first = models.first(where: { $0.function == .image && $0.supported }) { imageModel = first.repo }
            if imageSettings == nil { imageSettings = imageConfiguration?.defaults }
            var history: [NativeJob] = []
            var images: [NativeJob] = []
            for batch in jobsValue.batches where ["voice", "image"].contains(batch.modality) {
                let detail: HubJobDetail = try await get(["api", "hub", "jobs", batch.id])
                guard connectionID == requestConnection else { return }
                if batch.modality == "image" { images.append(Self.mapHubJob(detail)) }
                else { history.append(Self.mapHubJob(detail)) }
            }
            audioJobs = history.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            imageJobs = images.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            var subtitles: [NativeJob] = []
            for batch in subtitleValue.batches {
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
                let run: HubModelDownload = try await get(["api", "hub", "model-downloads", runID])
                hubTransfers.append(NativeDownload(id: run.id, repo: repo, state: run.state,
                    percent: 0, downloadedBytes: 0, totalBytes: 0,
                    error: run.state == "error" || run.state == "interrupted" ? "Hub model download \(run.state)." : nil))
            }
            downloads = hubTransfers + transfersValue.jobs.map(Self.mapDownload)
            tokenStatus = settingsValue.value
            reconcileSelections(defaultTranscription: transcriptionValue.defaultModel)
            await reconcileTranscription()
            isReachable = true
            if reportErrors { error = nil }
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
            let fields = [
                "transcript": transcript,
                "language": audioLanguage.trimmingCharacters(in: .whitespacesAndNewlines),
            ]
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
                if model.requiresReference {
                    guard audioReferencePath != nil else {
                        throw NativeAPIError.message("Choose a reference clip before generating with this voice model.")
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
                            presetSpeaker: "", language: language))],
                        voiceReferenceAssetId: assetID, routing: routingPin)
                } else {
                    guard audioSpeakers.isEmpty || audioSpeakers.contains(audioSpeaker) else {
                        throw NativeAPIError.message("Choose a speaker reported by the local Qwen runtime.")
                    }
                    body = HubAudioRequest(
                        modality: "voice", model: model.repo,
                        clientRequestId: "native:\(UUID().uuidString)",
                        items: [HubAudioItem(text: text, params: HubAudioParams(
                            presetSpeaker: audioSpeaker, language: language))],
                        voiceReferenceAssetId: nil, routing: routingPin)
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
            let result: HubImageReconciliation = try await sendEmpty(["api", "hub", "jobs", job.id, "items", "0", "reconcile"], method: "POST")
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

    func cancelAudio(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard audioJobs.contains(where: { $0.id == job.id }) else { throw NativeAPIError.message("That audio job is no longer in the local queue.") }
            let _: HubCancelResponse = try await sendEmpty(["api", "hub", "jobs", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the audio job") } }
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
            // The note names the role the way the picker does, and reports the Hub's answer
            // rather than the operator's request.
            let applied = role == "agent" ? "Node" : role.capitalized
            roleNote = "This Mac is now a \(applied)."
            error = nil
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Change this Mac's role") } }
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
    func joinController(address: String, token: String, boundHosts: [String] = []) async {
        guard let requestConnection = connectionID else { return }
        fleetNote = nil
        do {
            let target = try Self.privateHTTPAddress(address)
            let secret = token.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !secret.isEmpty else { throw NativeAPIError.message("Enter the controller's Hub token.") }
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
            guard connectionID == requestConnection else { return }
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
            defer { if connectionID == requestConnection { joiningFleet = false } }
            let (_, response) = try await session.data(for: request)
            guard connectionID == requestConnection else { return }
            guard let http = response as? HTTPURLResponse else {
                throw NativeAPIError.message("The controller returned no response.")
            }
            guard (200..<300).contains(http.statusCode) else {
                throw NativeAPIError.message(http.statusCode == 401
                    ? "The controller refused this Mac's join: check its Hub token."
                    : "The controller refused the join (\(http.statusCode)).")
            }
            controllerAddress = "\(target.host ?? ""):\(target.port ?? 0)"
            fleetNote = "Joined the controller at \(controllerAddress)."
            error = nil
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Join the controller") } }
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
    static func privateHTTPAddress(_ text: String) throws -> URL {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let candidate = trimmed.contains("://") ? trimmed : "http://\(trimmed)"
        guard let components = URLComponents(string: candidate),
              components.scheme == "http", let host = components.host,
              let port = components.port, (1...65535).contains(port),
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/" else {
            throw NativeAPIError.message("Enter an address like 192.168.1.21:8080.")
        }
        let parts = host.split(separator: ".", omittingEmptySubsequences: false).compactMap { Int($0) }
        guard parts.count == 4, parts.allSatisfy({ (0...255).contains($0) }),
              let url = URL(string: "http://\(host):\(port)") else {
            throw NativeAPIError.message("Enter a numeric address, not a name. For example 192.168.1.21:8080.")
        }
        let first = parts[0], second = parts[1]
        let isPrivate = first == 10 || first == 127
            || (first == 192 && second == 168)
            || (first == 172 && (16...31).contains(second))
            || (first == 100 && (64...127).contains(second))
            || (first == 169 && second == 254)
        guard isPrivate else {
            throw NativeAPIError.message("That is not a private or Tailscale address. Enter your controller's LAN or Tailscale address.")
        }
        return url
    }

    /// Read the Hub's own role and identity, plus the version it reports.
    func refreshConnection() async {
        guard let requestConnection = connectionID else { return }
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

    func audioData(for job: NativeJob) async throws -> Data {
        guard let requestConnection = connectionID else { throw NativeAPIError.message("Start the local services before retrieving audio.") }
        guard job.state == "done", audioJobs.contains(where: { $0.id == job.id }) else {
            throw NativeAPIError.message("Audio is available only for a completed job in the current history.")
        }
        let request = try makeRequest(["api", "hub", "jobs", job.id, "items", "0", "artifact"])
        let data = try await performData(request)
        guard connectionID == requestConnection else { throw NativeAPIError.message("The local engine connection changed before audio retrieval completed.") }
        return data
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

    func saveHFToken(_ token: String) async -> Bool {
        error = "Hugging Face token editing needs an owner-authenticated Hub route."
        return false
    }

    func testHFToken(_ token: String) async -> NativeTokenTest? {
        error = "Hugging Face token testing needs an owner-authenticated Hub route."
        return nil
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
            audioModel = Self.qwenCustomVoice
        }
        if !models.contains(where: { $0.repo == transcriptionModel && $0.function == .transcription && $0.supported }) {
            transcriptionModel = models.first {
                $0.repo == defaultTranscription && $0.function == .transcription && $0.supported
            }?.repo ?? Self.whisperTurbo
        }
        if let selected = models.first(where: { $0.repo == transcriptionModel && $0.function == .transcription }),
           !selected.supportsWordTimestamps { wordTimestamps = false }
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
            let formCompatible = isQwen || isCloneFamily
            let package = packages[item.repo]
            let supported = formCompatible && package != nil
            let cached = item.cache?.state == "cached"
            let ready = (item.runtimeReady ?? false) && item.memoryEligible != false
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
                supportsWordTimestamps: false, speakers: (supported && !isClone) ? speakers : [],
                requiresReference: isClone, requiresTranscript: transcriptRequired
            )
        }
        let transcriptionModels = transcription.map { item in
            let package = packages[item.repo]
            // The standalone Subtitle worker reports engine "subtitle-mlx"; the retained Voice
            // worker reports "whisper". Accept either, or the Subtitle screen can never select a
            // model even though the worker reports it ready.
            let engine = (item.engine ?? "").lowercased()
            let supported = (engine.contains("whisper") || engine.contains("subtitle")) && package != nil
            let ready = transcriptionAvailable && item.cached == true && package?.dispatchState == "eligible"
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
                runtimeReady: ready, supportsWordTimestamps: item.supportsWordTimestamps ?? false, speakers: []
            )
        }
        let imageModels = audio.filter { $0.capabilities?.contains("txt2img") == true }.map { item in
            let package = packages[item.repo]
            let supported = item.imageConfiguration != nil && package != nil
            let ready = item.runtimeReady == true && item.memoryEligible != false && package?.dispatchState == "eligible"
            return NativeModel(id: "image:\(item.repo)", repo: item.repo, label: item.label,
                family: item.family ?? "Image", function: .image, cached: item.cache?.state == "cached",
                supported: supported, supportNote: ready && supported ? "Ready for local image generation." : "Verify the image model files and runtime in Models.",
                sizeGB: item.sizeGb, capabilities: item.capabilities ?? [], languages: [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: [])
        }
        return audioModels + transcriptionModels + imageModels
    }

    private static func mapJob(_ wire: JobWire) -> NativeJob {
        NativeJob(
            id: wire.id, state: wire.state, progress: wire.progress ?? 0, text: wire.params?.text ?? "",
            model: wire.params?.repo ?? "", createdAt: wire.createdAt,
            outputURL: wire.state == "done" ? wire.outputUrl : nil, error: wire.error
        )
    }
    private static func mapHubJob(_ wire: HubJobDetail) -> NativeJob {
        let item = wire.items.first
        return NativeJob(id: wire.id, state: item?.state ?? "queued", progress: item?.progress ?? 0,
            text: item?.prompt ?? "", model: wire.model, createdAt: wire.createdAt,
            outputURL: item?.state == "done" ? item?.artifactUrl : nil, error: item?.error, bytes: item?.bytes, sha256: item?.sha256,
            imageSettings: item?.params?.imageSettings)
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
        request.httpBody = try JSONEncoder().encode(body)
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
        "\(action) failed. \(error.localizedDescription)"
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
            audioText: audioText, audioModel: audioModel, audioSpeaker: audioSpeaker, audioLanguage: audioLanguage,
            transcriptionModel: transcriptionModel, transcriptionFile: transcriptionFile?.path,
            transcriptionLanguage: transcriptionLanguage, wordTimestamps: wordTimestamps,
            transcriptionResult: transcriptionResult, imagePrompt: imagePrompt, imageModel: imageModel, imageSettings: imageSettings,
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
        transcriptionModel = state.transcriptionModel
        if let path = state.transcriptionFile, FileManager.default.fileExists(atPath: path) { transcriptionFile = URL(fileURLWithPath: path) }
        transcriptionLanguage = state.transcriptionLanguage
        wordTimestamps = state.wordTimestamps
        transcriptionResult = state.transcriptionResult
        imagePrompt = state.imagePrompt ?? ""
        imageModel = state.imageModel ?? ""
        imageSettings = state.imageSettings
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
}
private struct HubModelDownload: Decodable { let id: String; let packageId: String; let state: String }
private struct HubModelImport: Encodable { let sourceFolder: String }
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
    let cache: CacheWire?
    let generationDefaults: NativeImageSettings?
    let generationLimits: NativeImageLimits?
    let imageSizes: [NativeImageSize]?
    /// Family capability claim from the runtime, so clone vs preset is decided by the catalog
    /// rather than by guessing from the model name.
    let referenceAudio: CatalogReferenceAudio?
    var imageConfiguration: NativeImageConfiguration? {
        guard let defaults = generationDefaults, let limits = generationLimits,
              let sizes = imageSizes, !sizes.isEmpty,
              limits.minSteps > 0, limits.maxSteps >= limits.minSteps,
              limits.minGuidance.isFinite, limits.maxGuidance.isFinite, limits.minGuidance <= limits.maxGuidance,
              limits.minSeed <= limits.maxSeed,
              sizes.allSatisfy({ $0.width > 0 && $0.height > 0 }), Set(sizes.map(\.id)).count == sizes.count else { return nil }
        let value = NativeImageConfiguration(defaults: defaults, sizes: sizes, limits: limits)
        return value.accepts(defaults) ? value : nil
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
    let progress: Double?
    let prompt: String?
    let artifactUrl: String?
    let error: String?
    let bytes: Int64?
    let sha256: String?
    let params: HubJobParams?
}
private struct HubJobParams: Decodable {
    let width: Int?
    let height: Int?
    let steps: Int?
    let guidance: Double?
    let seed: Int64?
    var imageSettings: NativeImageSettings? {
        guard let width, let height, let steps, let guidance, let seed else { return nil }
        return NativeImageSettings(width: width, height: height, steps: steps, guidance: guidance, seed: seed)
    }
}
private struct HubJobSubmit: Decodable { let batchId: String; let replayed: Bool? }
private struct HubCancelResponse: Decodable { let ok: Bool }
private struct HubImageReconciliation: Decodable { let reconciled: Bool; let clientRequestId: String? }
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
private struct HubAckReceipt: Decodable { let ok: Bool }
private struct HubAudioRequest: Codable {
    let modality: String
    let model: String
    let clientRequestId: String
    let items: [HubAudioItem]
    /// Staged voice-clone reference. Sent for Qwen Base models; the Hub streams the clip to the
    /// worker's reference endpoint and supplies the transcript from the staged asset.
    let voiceReferenceAssetId: String?
    /// Overrides the Hub's automatic choice of Studio. Absent means automatic.
    let routing: String?
    enum CodingKeys: String, CodingKey {
        case modality, model, clientRequestId, items, routing
        case voiceReferenceAssetId = "voice_reference_asset_id"
    }
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
    enum CodingKeys: String, CodingKey { case presetSpeaker = "preset_speaker", language }
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
