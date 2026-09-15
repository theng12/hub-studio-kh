import Foundation
import Darwin
import CryptoKit

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

final class FixtureProtocol: URLProtocol {
    static let lock = NSLock()
    static var lastGenerateBody: [String: Any]?
    static var lastGeneratePath = ""
    static var lastGenerateToken = ""
    static var generateBodies: [[String: Any]] = []
    static var dropNextGeneration = false
    static var lastDownloadPath = ""
    static var lastDownloadToken = ""
    static var hubDownloadCancelled = false
    static var lastTranscriptionBody = ""
    static var transcriptionPorts: [Int] = []
    static var uncertainTranscription = false
    static var corruptImage = false
    static var uncertainImage = false
    static var imageAckPaths: [String] = []
    static var imageAckRejects = false
    static let png = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")!
    static var holdGeneration = false
    static let generationStarted = DispatchSemaphore(value: 0)
    static let releaseGeneration = DispatchSemaphore(value: 0)
    static var heldCatalogPort: Int?
    static var catalogPorts: [Int] = []
    static let catalogStarted = DispatchSemaphore(value: 0)
    static let releaseCatalog = DispatchSemaphore(value: 0)

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    static func generatedBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return lastGenerateBody }
    static func generatedBodies() -> [[String: Any]] { lock.lock(); defer { lock.unlock() }; return generateBodies }
    static func dropNextSubmit() { lock.lock(); defer { lock.unlock() }; dropNextGeneration = true }
    static func setUncertainTranscription(_ value: Bool) { lock.lock(); defer { lock.unlock() }; uncertainTranscription = value }
    static func transcriptionBody() -> String { lock.lock(); defer { lock.unlock() }; return lastTranscriptionBody }
    static func recordedTranscriptionPorts() -> [Int] { lock.lock(); defer { lock.unlock() }; return transcriptionPorts }
    static func setCorruptImage(_ value: Bool) { lock.lock(); defer { lock.unlock() }; corruptImage = value }
    static func setUncertainImage(_ value: Bool) { lock.lock(); defer { lock.unlock() }; uncertainImage = value }
    static func imageAckRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return imageAckPaths }
    static func setImageAckRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; imageAckRejects = value }
    static func setHoldGeneration(_ value: Bool) { lock.lock(); defer { lock.unlock() }; holdGeneration = value }
    static func waitForGenerationStart() -> Bool { generationStarted.wait(timeout: .now() + 2) == .success }
    static func holdCatalog(on port: Int) { lock.lock(); defer { lock.unlock() }; heldCatalogPort = port }
    static func waitForCatalogStart() -> Bool { catalogStarted.wait(timeout: .now() + 2) == .success }
    static func catalogCount(on port: Int) -> Int { lock.lock(); defer { lock.unlock() }; return catalogPorts.filter { $0 == port }.count }

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
        if method == "GET", path == "/api/hub/catalog" {
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
        let result: (Int, String, Data)
        switch (method, path) {
        case ("GET", "/api/hub/catalog"):
            result = (200, "application/json", Self.catalogFixture)
        case ("GET", "/api/hub/model-packages"):
            result = (200, "application/json", Data(#"{"models":[{"package_id":"synthetic-custom-voice","model_id":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-flux","model_id":"fixture/flux","state":"files_verified","dispatch_state":"eligible"},{"package_id":"whisper-turbo","model_id":"mlx-community/whisper-large-v3-turbo","state":"files_verified","dispatch_state":"eligible"},{"package_id":"voxcpm2-4bit","model_id":"mlx-community/VoxCPM2-4bit","state":"files_verified","dispatch_state":"eligible"}]}"#.utf8))
        case ("GET", "/studio/voice/api/transcribe/availability"):
            result = (200, "application/json", Data(#"{"available":true,"default_model":"mlx-community/whisper-large-v3-turbo","models":[{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","size_gb":1.6,"note":"Recommended","engine":"subtitle-mlx","languages":"Multilingual","supports_word_timestamps":true,"recommended":true,"cached":true},{"repo":"moonshine-ai/moonshine-base","label":"Moonshine","size_gb":0.25,"note":"Pilot","engine":"moonshine","languages":"English","supports_word_timestamps":false,"recommended":false,"cached":true}]}"#.utf8))
        case ("GET", "/studio/subtitle/api/transcribe/availability"):
            // The Subtitle studio reads its availability from the subtitle worker, not Voice.
            result = (200, "application/json", Data(#"{"available":true,"default_model":"mlx-community/whisper-large-v3-turbo","models":[{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","size_gb":1.6,"note":"Recommended","engine":"subtitle-mlx","languages":"Multilingual","supports_word_timestamps":true,"recommended":true,"cached":true},{"repo":"moonshine-ai/moonshine-base","label":"Moonshine","size_gb":0.25,"note":"Pilot","engine":"moonshine","languages":"English","supports_word_timestamps":false,"recommended":false,"cached":true}]}"#.utf8))
        case ("GET", "/studio/voice/api/generate/availability"):
            result = (200, "application/json", Data(#"{"qwen3_preset_speakers":[{"id":"Ryan","lang":"en","gender":"male","description":"Warm"},{"id":"Aiden","lang":"en","gender":"male","description":"Clear"}]}"#.utf8))
        case ("GET", "/studio/voice/api/settings"):
            result = (200, "application/json", Data(#"{"hf_token_set":false,"hf_token_masked":""}"#.utf8))
        case ("POST", "/api/hub/model-packages/whisper-turbo/download"):
            Self.lock.lock(); Self.lastDownloadPath = path
            Self.lastDownloadToken = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"id":"hub-download-1","package_id":"whisper-turbo","state":"running"}"#.utf8))
        case ("GET", "/api/hub/model-downloads/hub-download-1"):
            Self.lock.lock(); let cancelled = Self.hubDownloadCancelled; Self.lock.unlock()
            result = (200, "application/json", Data("{\"id\":\"hub-download-1\",\"package_id\":\"whisper-turbo\",\"state\":\"\(cancelled ? "cancelled" : "running")\"}".utf8))
        case ("DELETE", "/api/hub/model-downloads/hub-download-1"):
            Self.lock.lock(); Self.hubDownloadCancelled = true; Self.lock.unlock()
            result = (200, "application/json", Data(#"{"id":"hub-download-1","package_id":"whisper-turbo","state":"cancelled"}"#.utf8))
        case ("GET", "/api/hub/jobs"):
            result = (200, "application/json", Data(#"{"batches":[{"id":"old-job","modality":"voice"},{"id":"image-job","modality":"image"}]}"#.utf8))
        case ("GET", "/api/hub/jobs/old-job"):
            result = (200, "application/json", Data(#"{"id":"old-job","modality":"voice","model":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","created_at":10,"items":[{"index":0,"state":"done","progress":1.0,"prompt":"Earlier","artifact_url":"/api/hub/jobs/old-job/items/0/artifact"}]}"#.utf8))
        case ("GET", "/api/hub/jobs/new-job"):
            result = (200, "application/json", Data(#"{"id":"new-job","modality":"voice","model":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","created_at":20,"items":[{"index":0,"state":"done","progress":1.0,"prompt":"Hello locally","artifact_url":"/api/hub/jobs/new-job/items/0/artifact"}]}"#.utf8))
        case ("GET", "/api/hub/jobs/image-job"):
            let sha = SHA256.hash(data: Self.png).map { String(format: "%02x", $0) }.joined()
            Self.lock.lock(); let uncertain = Self.uncertainImage; Self.lock.unlock()
            let body: [String: Any] = ["id": "image-job", "modality": "image", "model": "fixture/flux", "created_at": 30,
                "items": [["index": 0, "state": uncertain ? "uncertain" : "done", "prompt": "A quiet garden", "artifact_url": "/api/hub/jobs/image-job/items/0/artifact", "bytes": Self.png.count, "sha256": sha,
                           "params": ["width": 1280, "height": 720, "steps": 4, "guidance": 1.0, "seed": -1]]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: body))
        case ("GET", "/api/hub/jobs/image-job/items/0/artifact"):
            Self.lock.lock(); let corrupt = Self.corruptImage; Self.lock.unlock()
            result = (200, "image/png", corrupt ? Data([0]) : Self.png)
        case ("POST", "/api/hub/jobs/image-job/items/0/ack"):
            Self.lock.lock(); Self.imageAckPaths.append(path); let rejects = Self.imageAckRejects; Self.lock.unlock()
            // delivery.py never returns 200 {"ok": false}; a rejected acknowledgement
            // surfaces as a non-2xx status, so the fixture must reject the same way.
            result = rejects
                ? (502, "application/json", Data(#"{"detail":"render worker did not acknowledge receipt"}"#.utf8))
                : (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("POST", "/api/hub/jobs/image-job/items/0/reconcile"):
            Self.lock.lock()
            Self.uncertainImage = false
            let originalRequestID = Self.generateBodies.last?["clientRequestId"] as? String ?? ""
            Self.lock.unlock()
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: ["reconciled": true, "client_request_id": originalRequestID]))
        case ("GET", "/studio/voice/api/downloads"):
            result = (200, "application/json", Data(#"{"jobs":[{"id":"download-1","repo":"mlx-community/whisper-large-v3-turbo","state":"running","bytes_done":50,"bytes_total":100,"percent":50.0}]}"#.utf8))
        case ("POST", "/api/hub/jobs"):
            Self.lock.lock(); let shouldHold = Self.holdGeneration; Self.lock.unlock()
            if shouldHold {
                Self.generationStarted.signal()
                _ = Self.releaseGeneration.wait(timeout: .now() + 5)
            }
            let body = requestBody()
            if let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                Self.lock.lock(); Self.lastGenerateBody = object; Self.lastGeneratePath = path
                Self.lastGenerateToken = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
                Self.generateBodies.append(object)
                let drop = Self.dropNextGeneration
                Self.dropNextGeneration = false
                Self.lock.unlock()
                if drop {
                    client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
                    return
                }
                if object["modality"] as? String == "image" {
                    result = (200, "application/json", Data(#"{"batch_id":"image-job","items":1,"replayed":false}"#.utf8))
                    break
                }
            }
            result = (200, "application/json", Data(#"{"batch_id":"new-job","items":1,"replayed":false}"#.utf8))
        case ("POST", "/api/hub/transcription/jobs"):
            let body = requestBody()
            Self.lock.lock()
            Self.lastTranscriptionBody = String(decoding: body, as: UTF8.self)
            Self.transcriptionPorts.append(request.url?.port ?? -1)
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"batch_id":"stt-batch","items":1,"queued":1}"#.utf8))
        case ("GET", "/api/hub/transcription/jobs"):
            // Durable Subtitle history list read during refresh.
            result = (200, "application/json", Data(#"{"batches":[{"id":"stt-batch","status":"done","model":"mlx-community/whisper-large-v3-turbo","created_at":40,"label":"Fixture subtitle"}],"stats":{}}"#.utf8))
        case ("GET", "/api/hub/transcription/jobs/stt-batch"):
            Self.lock.lock(); let uncertain = Self.uncertainTranscription; Self.lock.unlock()
            if uncertain {
                result = (200, "application/json", Data(#"{"id":"stt-batch","status":"uncertain","model":"mlx-community/whisper-large-v3-turbo","items":[{"index":0,"state":"uncertain"}]}"#.utf8))
                break
            }
            result = (200, "application/json", Data(#"{"id":"stt-batch","status":"done","model":"mlx-community/whisper-large-v3-turbo","items":[{"index":0,"state":"done","artifact_url":"/api/hub/transcription/jobs/stt-batch/items/0/artifact","metadata":{"text":"hello world","language":"en","duration":1.25,"model":"mlx-community/whisper-large-v3-turbo","vtt":"WEBVTT\n\n00:00:00.000 --> 00:00:01.250\nhello world\n"}}]}"#.utf8))
        case ("GET", "/api/hub/transcription/jobs/stt-batch/items/0/artifact"):
            result = (200, "application/x-subrip", Data("1\n00:00:00,000 --> 00:00:01,250\nhello world\n".utf8))
        case ("GET", "/api/hub/jobs/old-job/items/0/artifact"):
            result = (200, "audio/wav", Data([0x52, 0x49, 0x46, 0x46]))
        case ("GET", "/api/hub/jobs/new-job/items/0/artifact"):
            result = (200, "audio/wav", Data([0x52, 0x49, 0x46, 0x46]))
        default:
            result = (404, "application/json", Data(#"{"detail":"fixture route missing"}"#.utf8))
        }
        finish(result)
    }

    private static let catalogFixture = Data(#"{"models":[{"repo":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","label":"Qwen CustomVoice","family":"qwen3-tts","size_gb":1.9,"capabilities":["tts","multilingual","expressive"],"languages":["en","zh"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"other/tts","label":"Other TTS","family":"other","size_gb":2.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"mlx-community/whisper-base-mlx","label":"Whisper base","languages":"Multilingual"},{"repo":"fixture/flux","label":"FLUX fixture","family":"flux2-klein","capabilities":["txt2img"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"width":1280,"height":720,"steps":4,"guidance":1.0,"seed":-1},"image_sizes":[{"id":"16:9","label":"Wide 16:9","width":1280,"height":720},{"id":"1:1","label":"Square","width":1024,"height":1024}],"generation_limits":{"min_steps":1,"max_steps":50,"min_guidance":0.0,"max_guidance":10.0,"min_seed":-1,"max_seed":4294967295}},{"repo":"mlx-community/VoxCPM2-4bit","label":"VoxCPM2 4-bit","family":"voxcpm-mlx","size_gb":3.0,"capabilities":["tts","voice-cloning"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}}]}"#.utf8)

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
        await rejected.connect(baseURL: URL(string: "https://example.com")!, dataDirectory: temporary, token: "native-test-token")
        require(rejected.error?.contains("127.0.0.1") == true, "external API base was accepted")

        let state = temporary.appendingPathComponent("Native State")
        let store = NativeWorkspaceStore(sessionConfiguration: configuration)
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state, token: "native-test-token")
        require(store.models.count == 6, "combined model catalog")
        let supportedAudio = store.models.filter { $0.function == .audio && $0.supported }
        let supportedSTT = store.models.filter { $0.function == .transcription && $0.supported }
        require(supportedAudio.map(\.repo) == ["mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", "mlx-community/VoxCPM2-4bit"], "native Audio support guard")
        // The heavy voice families must be reachable from the Audio form, not filtered out by the
        // Qwen-only gate: VoxCPM2 and OmniVoice clone from a reference clip and were previously
        // excluded despite working end to end through the Hub.
        let vox = store.models.first { $0.repo == "mlx-community/VoxCPM2-4bit" }
        require(vox?.requiresReference == true, "VoxCPM2 must be exposed as a clone model")
        require(vox?.requiresTranscript == false, "VoxCPM2 does not need a reference transcript")
        require(supportedSTT.map(\.repo) == ["mlx-community/whisper-large-v3-turbo"], "native Transcription support guard")
        require(store.models.first { $0.repo == "other/tts" }?.supportNote.contains("packaged model catalog") == true, "unsupported runtime model needs a catalog note")
        require(store.audioJobs.first?.id == "old-job", "existing audio jobs must load")
        require(store.downloads.first?.percent == 50, "download progress must load")
        await store.startDownload(supportedSTT[0])
        require(FixtureProtocol.lastDownloadPath == "/api/hub/model-packages/whisper-turbo/download", "model download bypassed Hub package management")
        require(FixtureProtocol.lastDownloadToken == "native-test-token", "Hub download request was unauthenticated")
        require(store.downloads.contains { $0.id == "hub-download-1" }, "Hub model download status missing")
        await store.cancelDownload(store.downloads.first { $0.id == "hub-download-1" }!)
        require(store.downloads.contains { $0.id == "hub-download-1" && $0.state == "cancelled" }, "Hub model download cancellation missing")
        store.audioModel = "other/tts"
        store.audioText = "Unsupported voice model"
        let submitsBeforeUnsupported = FixtureProtocol.generatedBodies().count
        await store.generateAudio()
        require(FixtureProtocol.generatedBodies().count == submitsBeforeUnsupported, "unsupported voice model reached Hub dispatch")
        require(store.error?.contains("packaged model catalog") == true, "unsupported voice model needs an explanation")
        store.audioModel = NativeWorkspaceStore.qwenCustomVoice

        store.audioText = "Hello locally"
        store.audioSpeaker = "Ryan"
        store.audioLanguage = "en"
        await store.generateAudio()
        require(store.audioJobs.first?.id == "new-job", "submitted audio job")
        let generated = FixtureProtocol.generatedBody()
        require(FixtureProtocol.lastGeneratePath == "/api/hub/jobs", "native generation must use Hub admission")
        require(FixtureProtocol.lastGenerateToken == "native-test-token", "native Hub credential missing")
        require(generated?["modality"] as? String == "voice", "Hub modality")
        require(generated?["model"] as? String == supportedAudio[0].repo, "Hub model")
        require((generated?["clientRequestId"] as? String)?.count ?? 0 >= 8, "durable clientRequestId missing")
        let items = generated?["items"] as? [[String: Any]]
        require(items?.first?["text"] as? String == "Hello locally", "Hub text item")
        require((items?.first?["params"] as? [String: Any])?["preset_speaker"] as? String == "Ryan", "Hub speaker params")

        FixtureProtocol.dropNextSubmit()
        store.audioText = "Accepted before connection loss"
        await store.generateAudio()
        let pending = state.appendingPathComponent("native-pending-audio.json")
        require(FileManager.default.fileExists(atPath: pending.path), "lost submit response discarded original request identity")
        let beforeReplay = FixtureProtocol.generatedBodies().last!
        store.disconnect()
        let recovered = NativeWorkspaceStore(sessionConfiguration: configuration)
        await recovered.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state, token: "native-test-token")
        recovered.audioText = ""
        await recovered.generateAudio()
        let replayed = FixtureProtocol.generatedBodies().last!
        require(beforeReplay["clientRequestId"] as? String == replayed["clientRequestId"] as? String,
                "retry changed durable Hub replay identity")
        require((replayed["items"] as? [[String: Any]])?.first?["text"] as? String == "Accepted before connection loss",
                "retry mutated the original accepted payload")
        require(!FileManager.default.fileExists(atPath: pending.path), "reconciled submit identity remained pending")
        recovered.disconnect()
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state, token: "native-test-token")

        require(store.imageJobs.first?.id == "image-job", "Image history must restore through Hub")
        require(store.imageSettings?.width == 1280 && store.imageSettings?.steps == 4, "Image defaults must come from runtime")
        store.imagePrompt = "A quiet garden"
        store.imageSettings?.steps = 6
        store.imageSettings?.seed = 42
        FixtureProtocol.dropNextSubmit()
        await store.generateImage()
        let imagePending = state.appendingPathComponent("native-pending-image.json")
        require(FileManager.default.fileExists(atPath: imagePending.path), "lost Image submit must retain identity")
        let imageFirst = FixtureProtocol.generatedBodies().last!
        store.imagePrompt = "Changed after a lost response"
        store.imageSettings?.steps = 8
        await store.generateImage()
        let imageReplay = FixtureProtocol.generatedBodies().last!
        require(imageFirst["clientRequestId"] as? String == imageReplay["clientRequestId"] as? String, "Image replay changed identity")
        require((imageReplay["items"] as? [[String: Any]])?.first?["prompt"] as? String == "A quiet garden", "Image replay changed prompt")
        require(!FileManager.default.fileExists(atPath: imagePending.path), "accepted Image submit remained pending")
        let image = store.imageJobs.first!
        let verifiedImage = try await store.imageData(for: image)
        require(verifiedImage == FixtureProtocol.png, "verified Image artifact")
        FixtureProtocol.setCorruptImage(true)
        do { _ = try await store.imageData(for: image); fatalError("corrupt Image artifact was accepted") }
        catch { require(error.localizedDescription.contains("verification"), "Image hash mismatch needs actionable message") }
        FixtureProtocol.setCorruptImage(false)
        require(FixtureProtocol.imageAckRequests().isEmpty, "failed or unverified Image reads must not acknowledge delivery")
        // A save that cannot even be written must never reach the Hub receipt, even though
        // the artifact itself was fetched and hash-verified successfully first.
        let unwritableDestination = temporary.appendingPathComponent("missing-directory/never.png")
        do {
            try await store.saveImage(image, to: unwritableDestination)
            fatalError("a save into a missing directory must fail")
        } catch {
            require(FixtureProtocol.imageAckRequests().isEmpty,
                    "a failed save must never acknowledge delivery")
        }
        let imageDestination = temporary.appendingPathComponent("saved-image.png")
        try await store.saveImage(image, to: imageDestination)
        let savedImage = try Data(contentsOf: imageDestination)
        require(savedImage == FixtureProtocol.png, "Image export bytes")
        require(FixtureProtocol.imageAckRequests() == ["/api/hub/jobs/image-job/items/0/ack"],
                "a verified Image save must acknowledge delivery exactly once")
        FixtureProtocol.setImageAckRejects(true)
        let rejectedDestination = temporary.appendingPathComponent("rejected-image.png")
        do {
            try await store.saveImage(image, to: rejectedDestination)
            fatalError("a rejected delivery acknowledgement must be reported")
        } catch {
            require(error.localizedDescription.contains("was not acknowledged"),
                    "rejected acknowledgement needs an actionable message")
        }
        require(FileManager.default.fileExists(atPath: rejectedDestination.path),
                "the verified file must remain on disk when acknowledgement fails")
        FixtureProtocol.setImageAckRejects(false)
        store.restoreImageDefaults()
        require(store.imageSettings?.steps == 4 && store.imageSettings?.seed == -1, "Restore Image Defaults must use runtime values")

        FixtureProtocol.setUncertainImage(true)
        await store.refresh()
        store.imagePrompt = "A quiet garden"
        let beforeUncertainRepeat = FixtureProtocol.generatedBodies().count
        await store.generateImage()
        require(FixtureProtocol.generatedBodies().count == beforeUncertainRepeat, "same unresolved Image request was submitted as new work")
        require(store.error?.contains("uncertain") == true, "unresolved Image repeat needs a reconciliation explanation")
        store.imagePrompt = "A different new image"
        await store.generateImage()
        require(FixtureProtocol.generatedBodies().count == beforeUncertainRepeat + 1, "uncertainty blocked a different new request")
        require(FileManager.default.fileExists(atPath: imagePending.path), "accepted uncertain Image response lost its original request")
        let uncertainImageRequest = FixtureProtocol.generatedBodies().last!
        FixtureProtocol.setUncertainImage(false)
        await store.generateImage()
        require(FixtureProtocol.generatedBodies().last!["clientRequestId"] as? String == uncertainImageRequest["clientRequestId"] as? String, "uncertain Image response generated a new request identity")
        require(!FileManager.default.fileExists(atPath: imagePending.path), "reconciled Image response remained pending")
        FixtureProtocol.setUncertainImage(true)
        store.imagePrompt = "A second different request"
        await store.generateImage()
        let beforeImageCheck = FixtureProtocol.generatedBodies().count
        await store.reconcileImage(store.imageJobs.first!)
        require(FixtureProtocol.generatedBodies().count == beforeImageCheck, "Check Original Result submitted another generation")
        require(store.imageJobs.first?.state == "done", "Image result reconciliation did not refresh history")
        require(!FileManager.default.fileExists(atPath: imagePending.path), "verified original Image result did not clear its matching pending identity")

        let imageConnectionStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        let oldImageState = temporary.appendingPathComponent("old-image-state")
        let newImageState = temporary.appendingPathComponent("new-image-state")
        await imageConnectionStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: oldImageState, token: "native-test-token")
        imageConnectionStore.imagePrompt = "Old device prompt"
        FixtureProtocol.dropNextSubmit()
        await imageConnectionStore.generateImage()
        let oldImageRequest = FixtureProtocol.generatedBodies().last!
        await imageConnectionStore.connect(baseURL: URL(string: "http://127.0.0.1:45679")!, dataDirectory: newImageState, token: "native-test-token")
        imageConnectionStore.imagePrompt = "New device prompt"
        await imageConnectionStore.generateImage()
        let newImageRequest = FixtureProtocol.generatedBodies().last!
        require(oldImageRequest["clientRequestId"] as? String != newImageRequest["clientRequestId"] as? String, "pending Image identity crossed device state directories")
        require((newImageRequest["items"] as? [[String: Any]])?.first?["prompt"] as? String == "New device prompt", "old Image prompt crossed device state directories")
        imageConnectionStore.disconnect()

        let input = temporary.appendingPathComponent("clip.wav")
        try Data([0x52, 0x49, 0x46, 0x46]).write(to: input)
        store.transcriptionFile = input
        store.transcriptionLanguage = "en"
        store.wordTimestamps = true
        await store.transcribe()
        require(store.transcriptionResult?.text == "hello world", "transcription result: \(store.error ?? "no error"), pending \(FileManager.default.fileExists(atPath: state.appendingPathComponent("native-pending-transcription.json").path))")
        let multipart = FixtureProtocol.transcriptionBody()
        require(multipart.contains("name=\"model\"") && multipart.contains("name=\"files\""), "Hub multipart transcription contract")
        require(multipart.contains("name=\"item_ids\"") && multipart.contains("name=\"episode\""), "stable Hub transcription identity")
        let subtitleData = try store.transcriptData(format: .srt)
        require(String(decoding: subtitleData, as: UTF8.self).contains("hello world"), "SRT export data")
        let audioData = try await store.audioData(for: store.audioJobs.first!)
        require(audioData == Data([0x52, 0x49, 0x46, 0x46]), "audio export data")

        FixtureProtocol.setUncertainTranscription(true)
        let uncertainUpload = Task { await store.transcribe() }
        try await Task.sleep(for: .seconds(1))
        require(!store.transcribing, "uncertain transcription must stop polling and surface an actionable state")
        await uncertainUpload.value
        require(store.error?.contains("uncertain") == true, "uncertain transcription needs an actionable message")
        let pendingTranscript = state.appendingPathComponent("native-pending-transcription.json")
        require(FileManager.default.fileExists(atPath: pendingTranscript.path), "uncertain transcription identity must survive")
        let postsAfterUncertainty = FixtureProtocol.recordedTranscriptionPorts()
        await store.transcribe()
        require(FixtureProtocol.recordedTranscriptionPorts() == postsAfterUncertainty, "uncertain transcription must not resubmit")
        FixtureProtocol.setUncertainTranscription(false)
        await store.transcribe()
        require(!FileManager.default.fileExists(atPath: pendingTranscript.path), "completed reconciliation must clear pending identity")

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

        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state, token: "native-test-token")
        let fifo = temporary.appendingPathComponent("blocked-input.wav")
        require(mkfifo(fifo.path, 0o600) == 0, "could not create lifecycle FIFO fixture")
        store.transcriptionFile = fifo
        let portsBeforeRace = FixtureProtocol.recordedTranscriptionPorts()
        let oldTranscription = Task { await store.transcribe() }
        while !store.transcribing { await Task.yield() }
        store.disconnect()
        require(!store.transcribing, "disconnect must clear an in-flight transcription flag")
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45679")!, dataDirectory: state, token: "native-test-token")
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
            await overlappingStore.connect(baseURL: URL(string: "http://127.0.0.1:45681")!, dataDirectory: state, token: "native-test-token")
        }
        let catalogDidStart = await Task.detached { FixtureProtocol.waitForCatalogStart() }.value
        require(catalogDidStart, "catalog fixture did not reach its delayed response")
        await overlappingStore.connect(baseURL: URL(string: "http://127.0.0.1:45682")!, dataDirectory: state, token: "native-test-token")
        try await Task.sleep(for: .milliseconds(600))
        FixtureProtocol.releaseCatalog.signal()
        await oldConnect.value
        try await Task.sleep(for: .milliseconds(2_200))
        require(FixtureProtocol.catalogCount(on: 45682) == 1, "stale connect installed a duplicate polling task")
        overlappingStore.disconnect()

        let tokenStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await tokenStore.connect(baseURL: URL(string: "http://127.0.0.1:45683")!, dataDirectory: state, token: "native-test-token")
        let tokenSaved = await tokenStore.saveHFToken("hf_current")
        require(!tokenSaved, "native sent an unsupported Voice settings mutation")
        require(tokenStore.error?.contains("owner-authenticated Hub route") == true, "unsupported token edit needs a clear reason")
        let tokenTest = await tokenStore.testHFToken("hf_current")
        require(tokenTest == nil, "native sent an unsupported Voice token test")
        await tokenStore.removeModel(supportedSTT[0])
        require(tokenStore.error?.contains("active readers") == true, "unsafe model removal should be blocked")
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
