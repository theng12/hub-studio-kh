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
    static var transcriptionAckPaths: [String] = []
    static var transcriptionAckRejects = false
    static var modelRoutes: [String] = []
    static var statisticsQueries: [String] = []
    static var storageCalls: [String] = []
    static var roleChanges: [String] = []
    static var fleetCalls: [String] = []
    static var currentRole = "standalone"
    static var storageBody: [String: Any]?
    static var storageLimits: [String: Any]?
    static var storagePreviewRejects = false
    static var modelImportBody: [String: Any]?
    static var modelVerifyRejects = false
    static let png = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")!
    static var holdGeneration = false
    static let generationStarted = DispatchSemaphore(value: 0)
    static let releaseGeneration = DispatchSemaphore(value: 0)
    static var heldCatalogPort: Int?
    static var catalogPorts: [Int] = []
    static let catalogStarted = DispatchSemaphore(value: 0)
    static let releaseCatalog = DispatchSemaphore(value: 0)
    static var lastMemoryBody: [String: Any]?
    static var lastMemoryMethod = ""
    static var lastMemoryToken = ""
    static var memoryReleaseCount = 0
    static var memoryUnavailable = false

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
    static func transcriptionAcks() -> [String] { lock.lock(); defer { lock.unlock() }; return transcriptionAckPaths }
    static func setTranscriptionAckRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; transcriptionAckRejects = value }
    static func modelCalls() -> [String] { lock.lock(); defer { lock.unlock() }; return modelRoutes }
    static func statisticsPaths() -> [String] { lock.lock(); defer { lock.unlock() }; return statisticsQueries }
    static func storageRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return storageCalls }
    static func roleRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return roleChanges }
    static func fleetRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return fleetCalls }
    static func storageLimitsBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return storageLimits }
    static func setStoragePreviewRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; storagePreviewRejects = value }
    static func importedFolder() -> String? { lock.lock(); defer { lock.unlock() }; return modelImportBody?["source_folder"] as? String }
    static func setModelVerifyRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; modelVerifyRejects = value }
    static func setHoldGeneration(_ value: Bool) { lock.lock(); defer { lock.unlock() }; holdGeneration = value }
    static func memoryBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return lastMemoryBody }
    static func memoryRequest() -> (String, String) { lock.lock(); defer { lock.unlock() }; return (lastMemoryMethod, lastMemoryToken) }
    static func releases() -> Int { lock.lock(); defer { lock.unlock() }; return memoryReleaseCount }
    static func setMemoryUnavailable(_ value: Bool) { lock.lock(); defer { lock.unlock() }; memoryUnavailable = value }
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
        // Any package can be imported or verified; a per-package switch would silently rot
        // as the catalog grows, and Verify All must reach every installed model.
        if path.hasPrefix("/api/hub/model-packages/") {
            let parts = path.split(separator: "/")
            // /api/hub/model-packages/<package>/<action>
            // String.split drops the leading empty component: [api, hub, model-packages,
            // <package>, <action>].
            if (method == "POST" || method == "PUT"), parts.count == 5,
               parts[4] == "verify" || parts[4] == "import" {
                let package = String(parts[3])
                let action = String(parts[4])
                Self.lock.lock()
                Self.modelRoutes.append("\(action):\(package)")
                let rejects = Self.modelVerifyRejects
                if action == "import" {
                    Self.modelImportBody = (try? JSONSerialization.jsonObject(
                        with: requestBody())) as? [String: Any]
                }
                Self.lock.unlock()
                if rejects && action == "verify" {
                    // Exactly what the Hub sends: 200 with a non-ready state and its reason.
                    finish((200, "application/json", Data("{\"package_id\":\"\(package)\",\"state\":\"invalid\",\"dispatch_state\":\"ineligible\",\"reason\":\"File changed; verify again: model/model.safetensors\"}".utf8)))
                } else if rejects && action == "import" {
                    finish((200, "application/json", Data("{\"package_id\":\"\(package)\",\"state\":\"dependency_missing\",\"dispatch_state\":\"ineligible\",\"reason\":\"companion processor files are missing\"}".utf8)))
                } else {
                    finish((200, "application/json", Data("{\"package_id\":\"\(package)\",\"state\":\"files_verified\",\"dispatch_state\":\"eligible\",\"reason\":null}".utf8)))
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
        case ("GET", "/api/hub/settings"):
            Self.lock.lock(); Self.storageCalls.append("get"); Self.lock.unlock()
            result = (200, "application/json", Self.settingsFixture)
        case ("POST", "/api/hub/settings/preview"):
            let body = requestBody()
            Self.lock.lock()
            Self.storageCalls.append("preview")
            Self.storageBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            let rejects = Self.storagePreviewRejects
            Self.lock.unlock()
            result = rejects
                ? (400, "application/json", Data(#"{"detail":"max_bytes must be a positive integer"}"#.utf8))
                : (200, "application/json", Self.storagePreviewFixture)
        case ("PUT", "/api/hub/settings"):
            let body = requestBody()
            Self.lock.lock()
            Self.storageCalls.append("put")
            Self.storageLimits = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"defaults":{"max_bytes":50000000000,"retention_days":30,"cleanup_enabled":true,"cleanup_interval_seconds":3600,"minimum_free_bytes":20000000000},"effective":{"max_bytes":120000000000,"retention_days":14,"cleanup_enabled":false,"cleanup_interval_seconds":3600,"minimum_free_bytes":20000000000}}"#.utf8))
        case ("POST", "/api/hub/settings/restore-defaults"):
            Self.lock.lock(); Self.storageCalls.append("restore-defaults"); Self.lock.unlock()
            result = (200, "application/json", Self.settingsFixture)
        case ("GET", "/api/hub/stats"):
            Self.lock.lock()
            Self.statisticsQueries.append(request.url?.query ?? "")
            Self.lock.unlock()
            result = (200, "application/json", Self.statisticsFixture)
        case ("PUT", "/api/hub/controller"):
            let body = requestBody()
            Self.lock.lock()
            let requested = ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any])?["role"] as? String ?? ""
            Self.roleChanges.append(requested)
            let accepted = ["controller", "agent", "standalone"].contains(requested)
            if accepted { Self.currentRole = requested }
            let current = Self.currentRole
            Self.lock.unlock()
            // The Hub answers with the flat settings object, not the envelope the GET uses.
            result = accepted
                ? (200, "application/json", Data("{\"version\":2,\"role\":\"\(current)\",\"site_id\":\"studiohub-local\",\"controller_id\":\"controller-local\"}".utf8))
                : (400, "application/json", Data(#"{"detail":"role must be one of ['agent', 'controller', 'standalone']"}"#.utf8))
        case ("GET", "/api/hub/fleet/nodes"):
            Self.lock.lock(); Self.fleetCalls.append("list"); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"role":"controller","nodes":[{"id":"studio-2","name":"Studio 2","address":"http://192.168.1.21:8080","state":"reachable","detail":null,"version":"0.4.0","joined_at":1.0},{"id":"studio-3","name":"Studio 3","address":"http://192.168.1.22:8080","state":"unreachable","detail":"could not reach the node","version":null,"joined_at":2.0}],"summary":{"total":2,"reachable":1,"unreachable":1,"error":0}}"#.utf8))
        case ("DELETE", "/api/hub/fleet/nodes/studio-3"):
            Self.lock.lock(); Self.fleetCalls.append("leave:studio-3"); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("GET", "/api/hub/controller"):
            Self.lock.lock(); let roleNow = Self.currentRole; Self.lock.unlock()
            result = (200, "application/json", Data("{\"settings\":{\"version\":2,\"role\":\"\(roleNow)\",\"site_id\":\"studiohub-local\",\"site_name\":\"This Mac\",\"controller_id\":\"controller-local\",\"database_mode\":\"off\",\"machine_name\":\"Studio Mini\",\"database_configured\":false,\"sqlite_authoritative\":true,\"global_authority\":\"genstudio\",\"migration_stage\":\"local\"},\"runtime\":{\"state\":\"idle\"},\"readiness\":{\"ready\":true,\"role\":\"\(roleNow)\",\"site_id\":\"studiohub-local\",\"controller_id\":\"controller-local\",\"migration_stage\":\"local\",\"sqlite_authoritative\":true,\"global_job_claiming\":false,\"global_authority\":\"genstudio\",\"reason\":null,\"telemetry_warning\":null},\"capacity\":{\"machines\":1,\"queue_depth\":0}}".utf8))
        case ("GET", "/api/version"):
            result = (200, "application/json", Data(#"{"version":"0.4.0","source":"studiohub-mac@06e893e1643bffbcbd162fae122e5a4d0f29e283"}"#.utf8))
        case ("GET", "/api/hub/memory/policy"):
            Self.lock.lock(); let memoryDown = Self.memoryUnavailable; Self.lock.unlock()
            result = memoryDown
                ? (404, "application/json", Data(#"{"detail":"fixture route missing"}"#.utf8))
                : (200, "application/json", Self.memoryFixture)
        case ("PUT", "/api/hub/memory/policy"):
            let body = requestBody()
            Self.lock.lock()
            Self.lastMemoryBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            Self.lastMemoryMethod = method
            Self.lastMemoryToken = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"action":"set_mode","selected":2,"succeeded":2,"failed":0,"mode":"memory_saver","results":[{"id":"subtitle","ok":true,"result":"updated","detail":null},{"id":"voice","ok":true,"result":"updated","detail":null}]}"#.utf8))
        case ("POST", "/api/hub/memory/release"):
            Self.lock.lock()
            Self.memoryReleaseCount += 1
            Self.lastMemoryMethod = method
            Self.lastMemoryToken = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            Self.lock.unlock()
            // One Studio frees its model, one is mid-job and must be left alone.
            result = (200, "application/json", Data(#"{"ok":false,"action":"release","selected":2,"succeeded":1,"failed":1,"mode":null,"results":[{"id":"subtitle","ok":true,"result":"released","detail":null},{"id":"voice","ok":false,"result":"busy","detail":"Voice generation or transcription is active; memory was not released"}]}"#.utf8))
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
        case ("POST", "/api/hub/transcription/jobs/stt-batch/items/0/ack"):
            Self.lock.lock(); Self.transcriptionAckPaths.append(path)
            let rejectsTranscript = Self.transcriptionAckRejects; Self.lock.unlock()
            result = rejectsTranscript
                ? (502, "application/json", Data(#"{"detail":"subtitle worker did not acknowledge receipt"}"#.utf8))
                : (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("GET", "/api/hub/jobs/old-job/items/0/artifact"):
            result = (200, "audio/wav", Data([0x52, 0x49, 0x46, 0x46]))
        case ("GET", "/api/hub/jobs/new-job/items/0/artifact"):
            result = (200, "audio/wav", Data([0x52, 0x49, 0x46, 0x46]))
        default:
            result = (404, "application/json", Data(#"{"detail":"fixture route missing"}"#.utf8))
        }
        finish(result)
    }

    private static let settingsFixture = Data(#"{"defaults":{"max_bytes":50000000000,"retention_days":30,"cleanup_enabled":true,"cleanup_interval_seconds":3600,"minimum_free_bytes":20000000000},"effective":{"max_bytes":50000000000,"retention_days":30,"cleanup_enabled":true,"cleanup_interval_seconds":3600,"minimum_free_bytes":20000000000}}"#.utf8)

    private static let storagePreviewFixture = Data(#"{"effective":{"max_bytes":50000000000,"retention_days":30,"cleanup_enabled":true,"cleanup_interval_seconds":3600,"minimum_free_bytes":20000000000},"machines":[{"machine":"local","used_bytes":7200000000,"protected_bytes":1500000000,"max_bytes":50000000000,"retention_days":30,"enabled":true,"over_limit":false,"free_bytes":410000000000}]}"#.utf8)

    /// Shaped like the Hub's real reply, including a model with no timed run so the app's
    /// "not measured" path is exercised rather than assumed.
    private static let statisticsFixture = Data(#"{"total":3,"by_machine":{"local":{"count":3,"avg_s":45.5,"modalities":{"image":1,"transcription":2}}},"by_modality":{"image":{"count":1,"avg_s":94.47},"transcription":{"count":2,"avg_s":21.02}},"by_model":{"fixture/flux":{"count":1,"avg_s":94.47,"modality":"image"},"mlx-community/whisper-large-v3-turbo":{"count":2,"avg_s":21.02,"modality":"transcription"}},"model_rows":[{"model":"fixture/flux","count":1,"avg_s":94.47,"modality":"image"},{"model":"mlx-community/whisper-large-v3-turbo","count":2,"avg_s":21.02,"modality":"transcription"},{"model":"mlx-community/whisper-base-mlx","count":1,"avg_s":null,"modality":"transcription"}],"matrix":[],"by_source":{"job":3},"available_machines":["local"],"available_modalities":["image","voice","transcription"]}"#.utf8)

    private static let memoryFixture = Data(#"{"default_mode":"immediate","options":[{"mode":"performance","label":"Performance","idle_seconds":null},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"studios":[{"id":"image","title":"Image Studio","modality":"image","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"immediate","default_mode":"immediate","idle_seconds":0,"options":[{"mode":"immediate","label":"After every job","idle_seconds":0}],"loaded_models":[],"active_work":false,"busy":false,"fixed":true,"release_policy":"The model is released when each job finishes.","machine_bytes":17179869184}},{"id":"subtitle","title":"Subtitle Studio","modality":"transcription","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"immediate","default_mode":"immediate","idle_seconds":0,"options":[{"mode":"performance","label":"Performance","idle_seconds":null},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"loaded_models":[["whisper-large-v3-turbo"]],"active_work":false,"busy":false,"machine_bytes":17179869184}},{"id":"voice","title":"Voice Studio","modality":"voice","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"balanced","default_mode":"balanced","idle_seconds":600,"options":[{"mode":"performance","label":"Performance","idle_seconds":null},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"loaded_models":[],"active_work":true,"busy":true,"machine_bytes":17179869184}}],"summary":{"total":3,"ready":3,"offline":0,"update_required":0}}"#.utf8)
    private static let catalogFixture = Data(
#"{"models":[{"repo":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","label":"Qwen CustomVoice","family":"qwen3-tts","size_gb":1.9,"capabilities":["tts","multilingual","expressive"],"languages":["en","zh"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"other/tts","label":"Other TTS","family":"other","size_gb":2.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"mlx-community/whisper-base-mlx","label":"Whisper base","languages":"Multilingual"},{"repo":"fixture/flux","label":"FLUX fixture","family":"flux2-klein","capabilities":["txt2img"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"width":1280,"height":720,"steps":4,"guidance":1.0,"seed":-1},"image_sizes":[{"id":"16:9","label":"Wide 16:9","width":1280,"height":720},{"id":"1:1","label":"Square","width":1024,"height":1024}],"generation_limits":{"min_steps":1,"max_steps":50,"min_guidance":0.0,"max_guidance":10.0,"min_seed":-1,"max_seed":4294967295}},{"repo":"mlx-community/VoxCPM2-4bit","label":"VoxCPM2 4-bit","family":"voxcpm-mlx","size_gb":3.0,"capabilities":["tts","voice-cloning"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}}]}"#.utf8)

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

        // Fleet: one app can be told what it is. The role is the Hub's own setting, saved
        // through its controller route, and the screen shows the Hub's answer rather than the
        // choice the operator just made.
        // The role lives on the Hub, so read it before asserting anything about it.
        await store.refreshConnection()
        require(store.controllerIdentity?.settings.role == "standalone", "the starting role")
        await store.setRole("controller")
        require(FixtureProtocol.roleRequests() == ["controller"],
                "the role change must reach the Hub: \(FixtureProtocol.roleRequests())")
        require(store.controllerIdentity?.settings.role == "controller",
                "the screen must show the Hub's own answer, not the requested value")
        require(store.roleNote?.contains("Controller") == true, "a role change needs a visible result")

        await store.setRole("nonsense")
        require(store.error?.contains("role must be one of") == true,
                "a refused role must carry the Hub's reason, got: \(store.error ?? "nil")")
        await store.setRole("standalone")
        require(store.controllerIdentity?.settings.role == "standalone", "the role must be settable back")
        store.error = nil

        // The fleet: a controller lists who joined, with the state each node is actually in.
        await store.refreshFleetNodes()
        require(FixtureProtocol.fleetRequests().contains("list"), "the joined nodes must be read from the Hub")
        require(store.fleetNodes?.summary.total == 2, "every joined node must be listed")
        require(store.fleetNodes?.nodes.first?.state == "reachable", "a reachable node must say so")
        require(store.fleetNodes?.nodes.last?.isReachable == false,
                "a node that was joined but has since gone must not read as available")
        await store.leaveNode(store.fleetNodes!.nodes.last!)
        require(FixtureProtocol.fleetRequests().contains("leave:studio-3"),
                "removing a node must reach the Hub: \(FixtureProtocol.fleetRequests())")

        // The address typed for a controller is checked before any credential is sent to it.
        for good in ["192.168.1.21:8080", "http://192.168.1.21:8080", "100.101.102.103:8080",
                     "127.0.0.1:9", "10.0.0.4:9000", "172.16.5.5:8080", "169.254.1.1:8080"] {
            _ = try NativeWorkspaceStore.privateHTTPAddress(good)
        }
        for bad in ["93.184.216.34:80", "http://93.184.216.34:80", "controller.local:8080",
                    "192.168.1.21", "https://192.168.1.21:8080", "192.168.1.21:8080/jobs",
                    "192.168.1.21:8080?x=1", "user:pass@192.168.1.21:8080", "8.8.8.8:53"] {
            do {
                _ = try NativeWorkspaceStore.privateHTTPAddress(bad)
                fatalError("a non-private controller address was accepted: \(bad)")
            } catch {}
        }

        // Local storage: the limits come from the Hub, and the bytes cleanup may not touch
        // are shown beside them - including on a machine that is over its limit.
        await store.refreshStorage()
        require(FixtureProtocol.storageRequests() == ["get", "preview"],
                "reading storage must not change it: \(FixtureProtocol.storageRequests())")
        require(store.deviceSettings?.effective.maxBytes == 50_000_000_000, "the Hub's own limit")
        require(store.deviceSettings?.defaults.retentionDays == 30, "the shipped default")
        require(store.storageUsage.count == 1, "the per-machine usage row")
        require(store.storageUsage.first?.protectedBytes == 1_500_000_000,
                "protected bytes must be shown, not just the total")
        require(store.storageUsage.first?.overLimit == false, "an under-limit machine is not flagged")

        await store.saveStorageLimits(maxBytes: 120_000_000_000, retentionDays: 14, cleanupEnabled: false)
        require((FixtureProtocol.storageLimitsBody()?["max_bytes"] as? NSNumber)?.int64Value
                    == 120_000_000_000,
                "the new limit must reach the Hub")
        require(FixtureProtocol.storageLimitsBody()?["retention_days"] as? Int == 14,
                "the new retention must reach the Hub")
        require(FixtureProtocol.storageLimitsBody()?["cleanup_enabled"] as? Bool == false,
                "the cleanup choice must reach the Hub")
        require(store.storageNote?.contains("Saved") == true, "a save needs a visible result")

        await store.restoreStorageDefaults()
        // The refresh that follows the restore appends its own reads, so this checks that the
        // restore happened at all rather than that it was last.
        require(FixtureProtocol.storageRequests().contains("restore-defaults"),
                "Restore Defaults must call the Hub route: \(FixtureProtocol.storageRequests())")
        require(store.deviceSettings?.effective.retentionDays == 30, "defaults must come back")

        // A rejected limit surfaces the Hub's own reason rather than a generic failure.
        FixtureProtocol.setStoragePreviewRejects(true)
        await store.refreshStorage()
        require(store.storageNote?.contains("max_bytes") == true,
                "a refused read must explain why, got: \(store.storageNote ?? "nil")")
        FixtureProtocol.setStoragePreviewRejects(false)

        // Measured history: the Overview shows what the Hub actually recorded, including a
        // model with no timed run reported as unmeasured rather than as zero.
        await store.refreshStatistics()
        require(FixtureProtocol.statisticsPaths() == [""],
                "an all-time read must not invent a window")
        require(store.statistics?.total == 3, "the Hub's own job count must be shown")
        require(store.statistics?.trackedModels.count == 2,
                "a model with no timed run must not appear as a measured average")
        require(store.statistics?.modelRows.count == 3, "every recorded model must be kept")
        require(store.statistics?.byMachine["local"]?.count == 3, "the per-machine breakdown")
        require(store.statistics?.trackedModels.first?.model == "fixture/flux",
                "the slowest measured model must lead")

        await store.refreshStatistics(hours: 168)
        require(FixtureProtocol.statisticsPaths().last == "hours=168",
                "a windowed read must send the window as a query parameter")
        require(store.statisticsHours == 168, "the chosen window must be remembered")

        // Models: a folder copied onto this Mac becomes a verified library entry without a
        // second download, and any installed package can be re-verified. Verification is
        // timestamp-sensitive, so a copied or hard-linked library needs this without a
        // terminal; there was no button for it.
        let whisper = store.models.first { $0.repo == "mlx-community/whisper-large-v3-turbo" }!
        await store.verifyModel(whisper)
        require(FixtureProtocol.modelCalls() == ["verify:whisper-turbo"],
                "Verify Files must call the Hub package verify route")
        require(store.modelNote?.contains("Verified") == true, "a verification needs a visible result")

        let copied = store.models.first { $0.repo == "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit" }!
        await store.importModel(copied, from: URL(fileURLWithPath: "/Volumes/Backup/model-library/synthetic-custom-voice"))
        require(FixtureProtocol.modelCalls().last == "import:synthetic-custom-voice",
                "Import Folder must call the Hub package import route")
        require(FixtureProtocol.importedFolder() == "/Volumes/Backup/model-library/synthetic-custom-voice",
                "the chosen folder must reach the Hub unchanged")
        require(store.modelNote?.contains("Imported") == true, "an import needs a visible result")

        // A refused verification arrives as 200 with state "invalid". Reporting success from
        // the fact that the call returned would show "Verified" for exactly the broken library
        // this control exists to find.
        FixtureProtocol.setModelVerifyRejects(true)
        await store.verifyModel(copied)
        require(store.error?.contains("File changed") == true,
                "a refused verification must explain that a file changed, got: \(store.error ?? "nil")")
        require(store.modelNote?.contains("Verified") != true,
                "a refused verification must not leave a success note")
        // Import reports through the shared error banner too: a non-ready state is a refusal.
        await store.importModel(copied, from: URL(fileURLWithPath: "/Volumes/Backup/incomplete"))
        require(store.error?.contains("companion processor files are missing") == true,
                "a refused import must carry the Hub's reason, got: \(store.error ?? "nil")")
        FixtureProtocol.setModelVerifyRejects(false)
        store.error = nil

        // Results must be scoped: a per-model note belongs to its model, and the library
        // summary to the list, never repeated under whichever model is selected.
        await store.verifyModel(copied)
        require(store.modelNoteRepo == copied.repo, "a per-model result must record its model")

        // Verify All must reach every installed package that has an approved Hub package,
        // exactly once. A catalog entry with no package cannot be verified and must not be
        // silently counted as checked.
        let beforeVerifyAll = FixtureProtocol.modelCalls().count
        await store.verifyInstalledModels()
        let verifyAll = Array(FixtureProtocol.modelCalls().dropFirst(beforeVerifyAll))
        require(Set(verifyAll) == ["verify:synthetic-custom-voice", "verify:fixture-flux",
                                   "verify:whisper-turbo", "verify:voxcpm2-4bit"],
                "Verify All must check every installed model with a Hub package: \(verifyAll)")
        require(verifyAll.count == 4, "Verify All must not verify a model twice")
        // Six catalog entries report as cached, but only four have an approved Hub package.
        // The summary must say so rather than claim everything was checked.
        require(store.modelNote == "Verified 4 of 6. Could not verify: Other TTS, Moonshine.",
                "Verify All must report what it could not verify, got: \(store.modelNote ?? "nil")")
        require(store.modelNoteRepo == nil, "a library-wide summary must not be scoped to a model")

        // Model memory: one Hub route the app reads, with the mode names, thresholds and
        // resolved per-machine default all reported by the Studios that own them.
        await store.refreshMemoryPolicy()
        let memory = store.memoryInventory
        require(memory?.studios.count == 3, "memory inventory must list every Studio")
        require(memory?.summary.updateRequired == 0, "no Studio should need an update")
        let subtitleMemory = memory?.studios.first { $0.id == "subtitle" }
        require(subtitleMemory?.state == "ready", "the Subtitle worker must answer the memory contract")
        require(subtitleMemory?.policy?.mode == "immediate", "Subtitle's saved mode")
        require(subtitleMemory?.policy?.defaultMode == "immediate", "the Studio's own machine-aware default")
        require(subtitleMemory?.policy?.resident == ["whisper-large-v3-turbo"], "a loaded model must be named, not just a boolean")
        require(subtitleMemory?.policy?.resolvedOptions.count == 4, "every idle mode must be selectable")
        let imageMemory = memory?.studios.first { $0.id == "image" }
        require(imageMemory?.policy?.isFixed == true, "Image releases inside every job and has no idle mode")
        require(imageMemory?.policy?.resident.isEmpty == true, "Image holds nothing between jobs")
        require(memory?.studios.first { $0.id == "voice" }?.policy?.busy == true, "a working Studio must be shown as busy")

        // Connections: the Hub reports its own role and identity, and the credential this app
        // already holds is shown masked rather than printed.
        await store.refreshConnection()
        require(store.controllerIdentity?.settings.role == "standalone", "the Hub role must be reported truthfully")
        require(store.controllerIdentity?.settings.siteName == "This Mac", "the station name must come from the Hub")
        require(store.controllerIdentity?.settings.globalAuthority == "genstudio", "GenStudio stays the global authority")
        require(store.controllerIdentity?.readiness.ready == true, "a healthy local Hub must report ready")
        require(store.hubVersion?.version == "0.4.0", "the app must show the Hub's own version")
        let secret = "hub-token-abcdefghijklmnop"
        let masked = NativeWorkspaceStore.maskedToken(secret)
        require(masked != secret, "the token must never be shown unmasked by default")
        require(!masked.contains("efghijkl"), "the middle of a credential must not be published")
        require(masked.hasPrefix("hub-") && masked.hasSuffix("mnop"), "the mask must still be recognisable")
        require(NativeWorkspaceStore.maskedToken("short") == "••••••••", "a short credential must be fully masked")

        await store.setMemoryMode("memory_saver", studioIDs: ["subtitle"])
        let (memoryMethod, memoryToken) = FixtureProtocol.memoryRequest()
        require(memoryMethod == "PUT", "changing the mode must use the Hub route")
        require(memoryToken == "native-test-token", "the memory change must be authenticated")
        require(FixtureProtocol.memoryBody()?["mode"] as? String == "memory_saver", "the selected mode must reach the Hub")
        require(FixtureProtocol.memoryBody()?["studio_ids"] as? [String] == ["subtitle"], "a per-Studio change must not rewrite every Studio")
        require(store.memoryNote?.contains("Updated") == true, "a change needs a result the operator can read")

        await store.releaseMemory()
        require(FixtureProtocol.releases() == 1, "Release Model Memory must reach the Hub")
        require(store.memoryNote?.lowercased().contains("busy") == true, "a Studio that could not be freed must be named, not hidden")

        // An older Hub without these routes must leave Settings usable rather than blank.
        FixtureProtocol.setMemoryUnavailable(true)
        await store.refreshMemoryPolicy()
        require(store.memoryInventory == nil, "an unavailable memory policy must not show stale Studio state")
        require(store.memoryNote?.isEmpty == false, "an unavailable memory policy needs an explanation")
        FixtureProtocol.setMemoryUnavailable(false)
        await store.refreshMemoryPolicy()
        require(store.memoryInventory?.studios.count == 3, "the inventory must recover once the Hub answers again")

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

        // A completed transcript is only delivered once the Hub's own artifact has been
        // verified against what the screen shows, and delivery is acknowledged only after
        // the file is on disk. Exporting bytes without the receipt is the defect this pins.
        require(store.transcriptionResult?.batchID == "stt-batch", "the transcript must stay linked to its Hub job")
        let transcriptDestination = temporary.appendingPathComponent("exported-transcript.srt")
        try await store.saveTranscript(format: .srt, to: transcriptDestination)
        let exportedTranscript = try Data(contentsOf: transcriptDestination)
        require(exportedTranscript == subtitleData, "exported transcript bytes")
        require(FixtureProtocol.transcriptionAcks() == ["/api/hub/transcription/jobs/stt-batch/items/0/ack"],
                "a verified transcript save must acknowledge delivery exactly once")
        let unwritableTranscript = temporary.appendingPathComponent("missing-directory/never.srt")
        do {
            try await store.saveTranscript(format: .srt, to: unwritableTranscript)
            fatalError("a transcript save into a missing directory must fail")
        } catch {
            require(FixtureProtocol.transcriptionAcks().count == 1,
                    "a failed transcript save must never acknowledge delivery")
        }
        FixtureProtocol.setTranscriptionAckRejects(true)
        let rejectedTranscript = temporary.appendingPathComponent("rejected-transcript.srt")
        do {
            try await store.saveTranscript(format: .srt, to: rejectedTranscript)
            fatalError("a rejected transcript acknowledgement must be reported")
        } catch {
            require(error.localizedDescription.contains("was not acknowledged"),
                    "a rejected transcript acknowledgement needs an actionable message")
        }
        require(FileManager.default.fileExists(atPath: rejectedTranscript.path),
                "the verified transcript must remain on disk when acknowledgement fails")
        FixtureProtocol.setTranscriptionAckRejects(false)
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
