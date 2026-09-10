import Foundation
import Darwin

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

final class FixtureProtocol: URLProtocol {
    static let lock = NSLock()
    static var lastGenerateBody: [String: Any]?
    static var lastTranscriptionBody = ""
    static var transcriptionPorts: [Int] = []
    static var holdGeneration = false
    static let generationStarted = DispatchSemaphore(value: 0)
    static let releaseGeneration = DispatchSemaphore(value: 0)
    static var heldCatalogPort: Int?
    static var catalogPorts: [Int] = []
    static let catalogStarted = DispatchSemaphore(value: 0)
    static let releaseCatalog = DispatchSemaphore(value: 0)
    static var heldTokenSavePort: Int?
    static let tokenSaveStarted = DispatchSemaphore(value: 0)
    static let releaseTokenSave = DispatchSemaphore(value: 0)

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    static func generatedBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return lastGenerateBody }
    static func transcriptionBody() -> String { lock.lock(); defer { lock.unlock() }; return lastTranscriptionBody }
    static func recordedTranscriptionPorts() -> [Int] { lock.lock(); defer { lock.unlock() }; return transcriptionPorts }
    static func setHoldGeneration(_ value: Bool) { lock.lock(); defer { lock.unlock() }; holdGeneration = value }
    static func waitForGenerationStart() -> Bool { generationStarted.wait(timeout: .now() + 2) == .success }
    static func holdCatalog(on port: Int) { lock.lock(); defer { lock.unlock() }; heldCatalogPort = port }
    static func waitForCatalogStart() -> Bool { catalogStarted.wait(timeout: .now() + 2) == .success }
    static func catalogCount(on port: Int) -> Int { lock.lock(); defer { lock.unlock() }; return catalogPorts.filter { $0 == port }.count }
    static func holdTokenSave(on port: Int) { lock.lock(); defer { lock.unlock() }; heldTokenSavePort = port }
    static func waitForTokenSaveStart() -> Bool { tokenSaveStarted.wait(timeout: .now() + 2) == .success }

    private func requestBody() -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open(); defer { stream.close() }
        var data = Data(); var buffer = [UInt8](repeating: 0, count: 16_384)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            guard count > 0 else { break }
            data.append(buffer, count: count)
        }
        return data
    }

    override func startLoading() {
        let path = request.url?.path ?? ""
        let method = request.httpMethod ?? "GET"
        if method == "GET", path == "/api/catalog" {
            let port = request.url?.port ?? -1
            Self.lock.lock()
            Self.catalogPorts.append(port)
            let shouldHold = Self.heldCatalogPort == port
            Self.lock.unlock()
            if shouldHold {
                Self.catalogStarted.signal()
                DispatchQueue.global().async { [self] in
                    _ = Self.releaseCatalog.wait(timeout: .now() + 5)
                    finish((200, "application/json", Self.catalogFixture))
                }
                return
            }
        }
        if method == "POST", path == "/api/settings" {
            Self.lock.lock()
            let shouldHold = Self.heldTokenSavePort == request.url?.port
            Self.lock.unlock()
            if shouldHold {
                Self.tokenSaveStarted.signal()
                DispatchQueue.global().async { [self] in
                    _ = Self.releaseTokenSave.wait(timeout: .now() + 5)
                    finish((200, "application/json", Self.savedTokenFixture))
                }
                return
            }
        }
        let result: (Int, String, Data)
        switch (method, path) {
        case ("GET", "/api/catalog"):
            result = (200, "application/json", Self.catalogFixture)
        case ("GET", "/api/transcribe/availability"):
            result = (200, "application/json", Data(#"{"available":true,"default_model":"mlx-community/whisper-large-v3-turbo","models":[{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","size_gb":1.6,"note":"Recommended","engine":"whisper","languages":"Multilingual","supports_word_timestamps":true,"recommended":true,"cached":true},{"repo":"moonshine-ai/moonshine-base","label":"Moonshine","size_gb":0.25,"note":"Pilot","engine":"moonshine","languages":"English","supports_word_timestamps":false,"recommended":false,"cached":true}]}"#.utf8))
        case ("GET", "/api/generate/availability"):
            result = (200, "application/json", Data(#"{"qwen3_preset_speakers":[{"id":"Ryan","lang":"en","gender":"male","description":"Warm"},{"id":"Aiden","lang":"en","gender":"male","description":"Clear"}]}"#.utf8))
        case ("GET", "/api/settings"):
            result = (200, "application/json", Data(#"{"hf_token_set":false,"hf_token_masked":""}"#.utf8))
        case ("POST", "/api/settings"):
            result = (200, "application/json", Self.savedTokenFixture)
        case ("GET", "/api/generate/jobs"):
            result = (200, "application/json", Data(#"{"jobs":[{"id":"old-job","state":"done","progress":1.0,"params":{"text":"Earlier","repo":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"},"output_url":"/api/generate/jobs/old-job/audio","created_at":10}]}"#.utf8))
        case ("GET", "/api/downloads"):
            result = (200, "application/json", Data(#"{"jobs":[{"id":"download-1","repo":"mlx-community/whisper-large-v3-turbo","state":"running","bytes_done":50,"bytes_total":100,"percent":50.0}]}"#.utf8))
        case ("POST", "/api/generate/txt2speech"):
            Self.lock.lock(); let shouldHold = Self.holdGeneration; Self.lock.unlock()
            if shouldHold {
                Self.generationStarted.signal()
                _ = Self.releaseGeneration.wait(timeout: .now() + 5)
            }
            let body = requestBody()
            if let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                Self.lock.lock(); Self.lastGenerateBody = object; Self.lock.unlock()
            }
            result = (200, "application/json", Data(#"{"job":{"id":"new-job","state":"done","progress":1.0,"params":{"text":"Hello locally","repo":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","preset_speaker":"Ryan","language":"en"},"output_url":"/api/generate/jobs/new-job/audio","created_at":20}}"#.utf8))
        case ("POST", "/api/transcribe"):
            let body = requestBody()
            Self.lock.lock()
            Self.lastTranscriptionBody = String(decoding: body, as: UTF8.self)
            Self.transcriptionPorts.append(request.url?.port ?? -1)
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"text":"hello world","language":"en","duration":1.25,"model":"mlx-community/whisper-large-v3-turbo","srt":"1\n00:00:00,000 --> 00:00:01,250\nhello world\n","vtt":"WEBVTT\n\n00:00:00.000 --> 00:00:01.250\nhello world\n"}"#.utf8))
        case ("GET", "/api/generate/jobs/old-job/audio"):
            result = (200, "audio/wav", Data([0x52, 0x49, 0x46, 0x46]))
        case ("GET", "/api/generate/jobs/new-job/audio"):
            result = (200, "audio/wav", Data([0x52, 0x49, 0x46, 0x46]))
        default:
            result = (404, "application/json", Data(#"{"detail":"fixture route missing"}"#.utf8))
        }
        finish(result)
    }

    private static let catalogFixture = Data(#"{"models":[{"repo":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","label":"Qwen CustomVoice","family":"qwen3-tts","size_gb":1.9,"capabilities":["tts","multilingual","expressive"],"languages":["en","zh"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"other/tts","label":"Other TTS","family":"other","size_gb":2.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"}}]}"#.utf8)
    private static let savedTokenFixture = Data(#"{"hf_token_set":true,"hf_token_masked":"hf_…last"}"#.utf8)

    private func finish(_ result: (Int, String, Data)) {
        let response = HTTPURLResponse(url: request.url!, statusCode: result.0,
            httpVersion: "HTTP/1.1", headerFields: ["Content-Type": result.1])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: result.2)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@main
struct NativeAPITests {
    @MainActor
    static func main() async throws {
        let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: temporary) }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FixtureProtocol.self]
        let rejected = NativeWorkspaceStore(sessionConfiguration: configuration)
        await rejected.connect(baseURL: URL(string: "https://example.com")!, dataDirectory: temporary)
        require(rejected.error?.contains("127.0.0.1") == true, "external API base was accepted")

        let state = temporary.appendingPathComponent("Native State")
        let store = NativeWorkspaceStore(sessionConfiguration: configuration)
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state)
        require(store.models.count == 4, "combined model catalog")
        let supportedAudio = store.models.filter { $0.function == .audio && $0.supported }
        let supportedSTT = store.models.filter { $0.function == .transcription && $0.supported }
        require(supportedAudio.map(\.repo) == ["mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"], "native Audio support guard")
        require(supportedSTT.map(\.repo) == ["mlx-community/whisper-large-v3-turbo"], "native Transcription support guard")
        require(store.models.first { $0.repo == "other/tts" }?.supportNote.contains("native") == true, "unsupported runtime model needs a native-workflow note")
        require(store.audioJobs.first?.id == "old-job", "existing audio jobs must load")
        require(store.downloads.first?.percent == 50, "download progress must load")

        store.audioText = "Hello locally"
        store.audioSpeaker = "Ryan"
        store.audioLanguage = "en"
        await store.generateAudio()
        require(store.audioJobs.first?.id == "new-job", "submitted audio job")
        let generated = FixtureProtocol.generatedBody()
        require(generated?["preset_speaker"] as? String == "Ryan", "Qwen speaker field \(String(describing: generated))")
        require(generated?["repo"] as? String == supportedAudio[0].repo, "Qwen repo field")

        let input = temporary.appendingPathComponent("clip.wav")
        try Data([0x52, 0x49, 0x46, 0x46]).write(to: input)
        store.transcriptionFile = input
        store.transcriptionLanguage = "en"
        store.wordTimestamps = true
        await store.transcribe()
        require(store.transcriptionResult?.text == "hello world", "transcription result")
        let multipart = FixtureProtocol.transcriptionBody()
        require(multipart.contains("name=\"model\"") && multipart.contains("name=\"file\""), "multipart transcription contract")
        let subtitleData = try store.transcriptData(format: .srt)
        require(String(decoding: subtitleData, as: UTF8.self).contains("hello world"), "SRT export data")
        let audioData = try await store.audioData(for: store.audioJobs.first!)
        require(audioData == Data([0x52, 0x49, 0x46, 0x46]), "audio export data")

        FixtureProtocol.setHoldGeneration(true)
        let oldGeneration = Task { await store.generateAudio() }
        let generationDidStart = await Task.detached {
            FixtureProtocol.waitForGenerationStart()
        }.value
        require(generationDidStart && store.submittingAudio, "generation fixture did not reach its in-flight state")
        store.disconnect()
        require(!store.submittingAudio && !store.transcribing && !store.isReachable, "disconnect must clear busy and reachable state")
        FixtureProtocol.releaseGeneration.signal()
        await oldGeneration.value
        require(!store.submittingAudio, "an old generation completion restored stale busy state")
        FixtureProtocol.setHoldGeneration(false)

        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state)
        let fifo = temporary.appendingPathComponent("blocked-input.wav")
        require(mkfifo(fifo.path, 0o600) == 0, "could not create lifecycle FIFO fixture")
        store.transcriptionFile = fifo
        let portsBeforeRace = FixtureProtocol.recordedTranscriptionPorts()
        let oldTranscription = Task { await store.transcribe() }
        while !store.transcribing { await Task.yield() }
        store.disconnect()
        require(!store.transcribing, "disconnect must clear an in-flight transcription flag")
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45679")!, dataDirectory: state)
        let fifoWriter = Task.detached {
            let output = try FileHandle(forWritingTo: fifo)
            try output.write(contentsOf: Data([0x52, 0x49, 0x46, 0x46]))
            try output.close()
        }
        try await fifoWriter.value
        await oldTranscription.value
        require(FixtureProtocol.recordedTranscriptionPorts() == portsBeforeRace, "stale transcription submitted to a replacement worker")
        let uploadDirectory = state.appendingPathComponent("Uploads")
        let leftovers = (try? FileManager.default.contentsOfDirectory(atPath: uploadDirectory.path)) ?? []
        require(leftovers.isEmpty, "stale transcription left a multipart file behind")

        store.disconnect()
        let overlappingStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        FixtureProtocol.holdCatalog(on: 45681)
        let oldConnect = Task {
            await overlappingStore.connect(baseURL: URL(string: "http://127.0.0.1:45681")!, dataDirectory: state)
        }
        let catalogDidStart = await Task.detached { FixtureProtocol.waitForCatalogStart() }.value
        require(catalogDidStart, "catalog fixture did not reach its delayed response")
        await overlappingStore.connect(baseURL: URL(string: "http://127.0.0.1:45682")!, dataDirectory: state)
        try await Task.sleep(for: .milliseconds(600))
        FixtureProtocol.releaseCatalog.signal()
        await oldConnect.value
        try await Task.sleep(for: .milliseconds(2_200))
        require(FixtureProtocol.catalogCount(on: 45682) == 1, "stale connect installed a duplicate polling task")
        overlappingStore.disconnect()

        let tokenStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await tokenStore.connect(baseURL: URL(string: "http://127.0.0.1:45683")!, dataDirectory: state)
        FixtureProtocol.holdTokenSave(on: 45683)
        let oldTokenSave = Task { await tokenStore.saveHFToken("hf_old") }
        let tokenSaveDidStart = await Task.detached { FixtureProtocol.waitForTokenSaveStart() }.value
        require(tokenSaveDidStart, "token fixture did not reach its delayed response")
        tokenStore.disconnect()
        await tokenStore.connect(baseURL: URL(string: "http://127.0.0.1:45684")!, dataDirectory: state)
        FixtureProtocol.releaseTokenSave.signal()
        let staleTokenSaved = await oldTokenSave.value
        require(!staleTokenSaved, "stale token save reported success after reconnect")
        let currentTokenSaved = await tokenStore.saveHFToken("hf_current")
        require(currentTokenSaved && tokenStore.tokenStatus.isSet, "current token save did not report published success")
        tokenStore.disconnect()

        store.audioText = "Persist this draft"
        store.transcriptionModel = supportedSTT[0].repo
        store.disconnect()
        let preferences = state.appendingPathComponent("native-workspaces.json")
        require(FileManager.default.fileExists(atPath: preferences.path), "workspace choices not persisted")
        let mode = try FileManager.default.attributesOfItem(atPath: preferences.path)[.posixPermissions] as? NSNumber
        require(mode?.intValue == 0o600, "workspace choices must be private")

        print("Native API contract checks passed")
    }
}
