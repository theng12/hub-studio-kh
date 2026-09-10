import Combine
import Foundation

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
    static let whisperTurbo = "mlx-community/whisper-large-v3-turbo"

    @Published private(set) var models: [NativeModel] = []
    @Published private(set) var audioJobs: [NativeJob] = []
    @Published private(set) var downloads: [NativeDownload] = []
    @Published private(set) var audioSpeakers: [String] = []
    @Published var audioText = "" { didSet { scheduleSave() } }
    @Published var audioModel = qwenCustomVoice { didSet { scheduleSave() } }
    @Published var audioSpeaker = "" { didSet { scheduleSave() } }
    @Published var audioLanguage = "en" { didSet { scheduleSave() } }
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
    @Published private(set) var tokenStatus = NativeTokenStatus(isSet: false, masked: "")

    private let session: URLSession
    private let redirectDelegate: RejectRedirects
    private var baseURL: URL?
    private var connectionID: UUID?
    private var dataDirectory: URL?
    private var pollTask: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var loadingState = false

    init(sessionConfiguration: URLSessionConfiguration = .ephemeral) {
        let configuration = sessionConfiguration.copy() as! URLSessionConfiguration
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        redirectDelegate = RejectRedirects()
        session = URLSession(configuration: configuration, delegate: redirectDelegate, delegateQueue: nil)
    }

    func connect(baseURL: URL, dataDirectory: URL) async {
        disconnect()
        do {
            self.baseURL = try Self.validatedBase(baseURL)
            let requestConnection = UUID()
            self.connectionID = requestConnection
            self.dataDirectory = dataDirectory.standardizedFileURL
            try FileManager.default.createDirectory(
                at: dataDirectory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try loadState()
            await refresh()
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
        connectionID = nil
        isReachable = false
        submittingAudio = false
        transcribing = false
    }

    func refresh() async { await refresh(reportErrors: true) }

    private func refresh(reportErrors: Bool) async {
        guard baseURL != nil, let requestConnection = connectionID, !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            async let catalog: CatalogResponse = get(["api", "catalog"])
            async let transcription: TranscriptionAvailability = get(["api", "transcribe", "availability"])
            async let generation: GenerationAvailability = get(["api", "generate", "availability"])
            async let jobs: JobsResponse = get(["api", "generate", "jobs"])
            async let transfers: DownloadsResponse = get(["api", "downloads"])
            async let settings: TokenStatusWire = get(["api", "settings"])
            let (catalogValue, transcriptionValue, generationValue, jobsValue, transfersValue, settingsValue) =
                try await (catalog, transcription, generation, jobs, transfers, settings)
            guard connectionID == requestConnection else { return }
            audioSpeakers = (generationValue.qwen3PresetSpeakers ?? []).map(\.id)
            if audioSpeaker.isEmpty || (!audioSpeakers.isEmpty && !audioSpeakers.contains(audioSpeaker)) {
                audioSpeaker = audioSpeakers.first ?? "Ryan"
            }
            models = Self.combineModels(
                catalogValue.models,
                transcriptionValue.models,
                transcriptionAvailable: transcriptionValue.available ?? false,
                speakers: audioSpeakers
            )
            audioJobs = jobsValue.jobs.map(Self.mapJob).sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            downloads = transfersValue.jobs.map(Self.mapDownload)
            tokenStatus = settingsValue.value
            reconcileSelections(defaultTranscription: transcriptionValue.defaultModel)
            isReachable = true
            if reportErrors { error = nil }
        } catch {
            guard connectionID == requestConnection else { return }
            isReachable = false
            if reportErrors { self.error = Self.actionable(error, action: "Refresh local studio state") }
        }
    }

    func generateAudio() async {
        guard !submittingAudio, let requestConnection = connectionID else { return }
        do {
            let model = try usableModel(repo: audioModel, function: .audio)
            let text = audioText.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { throw NativeAPIError.message("Enter a script before generating audio.") }
            guard text.count <= 40_000 else { throw NativeAPIError.message("The script exceeds the 40,000-character limit.") }
            guard audioSpeakers.isEmpty || audioSpeakers.contains(audioSpeaker) else {
                throw NativeAPIError.message("Choose a speaker reported by the local Qwen runtime.")
            }
            submittingAudio = true
            defer { if connectionID == requestConnection { submittingAudio = false } }
            let body = AudioRequest(
                repo: model.repo,
                text: text,
                presetSpeaker: audioSpeaker,
                language: audioLanguage.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            let response: JobResponse = try await sendJSON(["api", "generate", "txt2speech"], method: "POST", body: body)
            guard connectionID == requestConnection else { return }
            let job = Self.mapJob(response.job)
            audioJobs.removeAll { $0.id == job.id }
            audioJobs.insert(job, at: 0)
            error = nil
        } catch {
            guard connectionID == requestConnection else { return }
            self.error = Self.actionable(error, action: "Generate audio")
        }
    }

    func transcribe() async {
        guard !transcribing, let requestConnection = connectionID else { return }
        do {
            let model = try usableModel(repo: transcriptionModel, function: .transcription)
            guard let file = transcriptionFile, FileManager.default.fileExists(atPath: file.path) else {
                throw NativeAPIError.message("Choose an audio or video file that still exists.")
            }
            guard !wordTimestamps || model.supportsWordTimestamps else {
                throw NativeAPIError.message("The selected transcription model does not support word timestamps.")
            }
            transcribing = true
            defer { if connectionID == requestConnection { transcribing = false } }
            let boundary = "StudioHub-\(UUID().uuidString)"
            guard let uploadDirectory = dataDirectory else { throw NativeAPIError.message("The app data directory is unavailable.") }
            let fields = [
                    "model": model.repo,
                    "language": transcriptionLanguage.trimmingCharacters(in: .whitespacesAndNewlines),
                    "word_timestamps": wordTimestamps ? "true" : "false"
                ]
            let uploadFile = try await Task.detached(priority: .userInitiated) {
                try Self.makeMultipartFile(
                    boundary: boundary, file: file, fields: fields, dataDirectory: uploadDirectory
                )
            }.value
            defer { try? FileManager.default.removeItem(at: uploadFile) }
            guard connectionID == requestConnection else { return }
            var request = try makeRequest(["api", "transcribe"], method: "POST")
            request.timeoutInterval = 60 * 60
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            let wire: TranscriptWire = try await performUpload(request, fromFile: uploadFile)
            guard connectionID == requestConnection else { return }
            transcriptionResult = wire.value(sourceFilename: file.lastPathComponent)
            error = nil
        } catch {
            guard connectionID == requestConnection else { return }
            self.error = Self.actionable(error, action: "Transcribe the selected file")
        }
    }

    func startDownload(_ model: NativeModel) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard models.contains(where: { $0.id == model.id && $0.repo == model.repo }), model.supported else {
                throw NativeAPIError.message("This model is not supported by the current native workspace.")
            }
            let _: DownloadStartResponse = try await sendJSON(
                ["api", "downloads"], method: "POST", body: DownloadRequest(repo: model.repo)
            )
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Start the model download") } }
    }

    func cancelDownload(_ job: NativeDownload) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard downloads.contains(where: { $0.id == job.id }) else { throw NativeAPIError.message("That download is no longer in the local queue.") }
            let _: DownloadCancelResponse = try await sendEmpty(["api", "downloads", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the model download") } }
    }

    func cancelAudio(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard audioJobs.contains(where: { $0.id == job.id }) else { throw NativeAPIError.message("That audio job is no longer in the local queue.") }
            let _: JobResponse = try await sendEmpty(["api", "generate", "jobs", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the audio job") } }
    }

    func releaseMemory() async {
        guard let requestConnection = connectionID else { return }
        do {
            let _: EmptyResponse = try await sendEmpty(["api", "memory", "release"], method: "POST")
            guard connectionID == requestConnection else { return }
            error = nil
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Release model memory") } }
    }

    func removeModel(_ model: NativeModel) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard models.contains(where: { $0.id == model.id && $0.repo == model.repo }) else {
                throw NativeAPIError.message("Refresh Models before removing this download.")
            }
            let _: EmptyResponse = try await sendEmpty(["api", "model-storage", model.repo], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Remove the downloaded model") } }
    }

    func audioData(for job: NativeJob) async throws -> Data {
        guard let requestConnection = connectionID else { throw NativeAPIError.message("Start the local services before retrieving audio.") }
        guard job.state == "done", audioJobs.contains(where: { $0.id == job.id }) else {
            throw NativeAPIError.message("Audio is available only for a completed job in the current history.")
        }
        let request = try makeRequest(["api", "generate", "jobs", job.id, "audio"])
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

    func saveHFToken(_ token: String) async -> Bool {
        guard let requestConnection = connectionID else { return false }
        do {
            let status: TokenStatusWire = try await sendJSON(["api", "settings"], method: "POST", body: TokenRequest(hfToken: token))
            guard connectionID == requestConnection else { return false }
            tokenStatus = status.value
            error = nil
            return true
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Save the Hugging Face token") }
            return false
        }
    }

    func testHFToken(_ token: String) async -> NativeTokenTest? {
        guard let requestConnection = connectionID else { return nil }
        do {
            let response: TokenTestWire = try await sendJSON(["api", "settings", "test-hf-token"], method: "POST", body: TokenRequest(hfToken: token))
            guard connectionID == requestConnection else { return nil }
            error = nil
            return NativeTokenTest(name: response.name, type: response.type, organizations: response.orgs ?? [])
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
        speakers: [String]
    ) -> [NativeModel] {
        let audioModels = audio.filter { $0.capabilities?.contains("tts") == true }.map { item in
            let supported = item.family == "qwen3-tts" && item.repo.localizedCaseInsensitiveContains("CustomVoice")
            let cached = item.cache?.state == "cached"
            let ready = (item.runtimeReady ?? false) && item.memoryEligible != false
            let qualified = item.repo == qwenCustomVoice
            let note = supported
                ? (cached && ready
                    ? (qualified ? "Tested local Qwen CustomVoice workflow." : "Compatible Qwen CustomVoice contract; this exact model has not completed the native pilot test.")
                    : "Native Audio compatible; install the model and ensure its runtime is ready.")
                : "This native Audio form does not yet expose the model-specific controls required by this runtime entry."
            return NativeModel(
                id: "audio:\(item.repo)", repo: item.repo, label: item.label, family: item.family ?? "Audio",
                function: .audio, cached: cached, supported: supported, supportNote: note, sizeGB: item.sizeGb,
                capabilities: item.capabilities ?? [], languages: item.languages ?? [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: supported ? speakers : []
            )
        }
        let transcriptionModels = transcription.map { item in
            let supported = item.engine?.lowercased().contains("whisper") == true
            let ready = transcriptionAvailable && item.cached == true
            let qualified = item.repo == whisperTurbo
            let note = supported
                ? (ready
                    ? (qualified ? "Tested local Whisper transcription workflow." : "Compatible Whisper contract; this exact model has not completed the native pilot test.")
                    : "Native Transcription compatible; download this model and ensure the transcription runtime is ready.")
                : "This native Transcription form has not been qualified for this runtime entry yet."
            return NativeModel(
                id: "transcription:\(item.repo)", repo: item.repo, label: item.label, family: item.engine ?? "Transcription",
                function: .transcription, cached: item.cached ?? false, supported: supported, supportNote: note,
                sizeGB: item.sizeGb, capabilities: ["transcription"], languages: item.languageList,
                runtimeReady: ready, supportsWordTimestamps: item.supportsWordTimestamps ?? false, speakers: []
            )
        }
        return audioModels + transcriptionModels
    }

    private static func mapJob(_ wire: JobWire) -> NativeJob {
        NativeJob(
            id: wire.id, state: wire.state, progress: wire.progress ?? 0, text: wire.params?.text ?? "",
            model: wire.params?.repo ?? "", createdAt: wire.createdAt,
            outputURL: wire.state == "done" ? wire.outputUrl : nil, error: wire.error
        )
    }

    private static func mapDownload(_ wire: DownloadWire) -> NativeDownload {
        NativeDownload(
            id: wire.id, repo: wire.repo, state: wire.state, percent: wire.percent ?? 0,
            downloadedBytes: wire.bytesDone ?? wire.bytesObserved ?? 0, totalBytes: wire.bytesTotal ?? 0, error: wire.error
        )
    }

    private static func validatedBase(_ url: URL) throws -> URL {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == "http", components.host == "127.0.0.1",
              let port = components.port, (1...65535).contains(port),
              components.user == nil, components.password == nil, components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/" else {
            throw NativeAPIError.message("The worker address must be http://127.0.0.1:<port> with no extra path.")
        }
        return URL(string: "http://127.0.0.1:\(port)")!
    }

    private func makeRequest(_ components: [String], method: String = "GET") throws -> URLRequest {
        guard let baseURL else { throw NativeAPIError.message("Start the local services before using this action.") }
        var url = baseURL
        for component in components { url.appendPathComponent(component) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
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
              final.scheme == "http", final.host == "127.0.0.1", final.port == baseURL?.port else {
            throw NativeAPIError.message("The local engine attempted to redirect outside its loopback address.")
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
        dataDirectory: URL
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
        try append("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: application/octet-stream\r\n\r\n")
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
            transcriptionResult: transcriptionResult
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
    }
}

private struct CacheWire: Decodable { let state: String? }
private struct CatalogResponse: Decodable { let models: [CatalogModel] }
private struct CatalogModel: Decodable {
    let repo: String
    let label: String
    let family: String?
    let sizeGb: Double?
    let capabilities: [String]?
    let languages: [String]?
    let runtimeReady: Bool?
    let memoryEligible: Bool?
    let cache: CacheWire?
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
