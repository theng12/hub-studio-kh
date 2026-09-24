import Foundation
import Darwin
import CryptoKit

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

func eventually(timeout: TimeInterval = 2, _ condition: () -> Bool) async -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if condition() { return true }
        try? await Task.sleep(for: .milliseconds(10))
    }
    return condition()
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
    static var corruptAudio = false
    static var voiceListCalls = 0
    static var voiceAudioCalls = 0
    static var voiceDeleteCalls = 0
    static var voiceAddCalls = 0
    static var voiceEditCalls = 0
    static var syncTranscribeCalls = 0
    static var tokenSaves: [Data] = []
    static var voiceAdded: [[String: Any]] = []
    static var audioAckPaths: [String] = []
    static var audioAckRejects = false
    static var uncertainImage = false
    static var imageAckPaths: [String] = []
    static var imageAckRejects = false
    static var discardPaths: [String] = []
    static var uncertainMusic = false
    static var corruptMusic = false
    static var musicAckPaths: [String] = []
    static var musicAckRejects = false
    static var transcriptionAckPaths: [String] = []
    static var transcriptionAckRejects = false
    static var modelRoutes: [String] = []
    static var statisticsQueries: [String] = []
    static var storageCalls: [String] = []
    static var roleChanges: [String] = []
    static var fleetCalls: [String] = []
    static var joinBody: [String: Any]?
    static var adoptBody: [String: Any]?
    /// One job detail that cannot be read, so a refresh is exercised with a single failing
    /// sub-request - the case that used to blank the whole page and mark the Hub unreachable.
    static var failJobDetail = false
    static func setFailJobDetail(_ value: Bool) { lock.lock(); defer { lock.unlock() }; failJobDetail = value }
    static var jobDetailReads = 0
    private static let detailLock = NSLock()
    static func resetJobDetailCount() { detailLock.lock(); defer { detailLock.unlock() }; jobDetailReads = 0 }
    static func jobDetailCount() -> Int { detailLock.lock(); defer { detailLock.unlock() }; return jobDetailReads }
    static func noteJobDetail() { detailLock.lock(); defer { detailLock.unlock() }; jobDetailReads += 1 }
    static var currentRole = "standalone"
    /// "minted" for a Mac running its own group, "adopted" for one that joined somebody else's.
    /// The app re-joins on start only for the second kind, so the fixture has to be able to be both.
    static var credentialOrigin = "minted"
    static var identityFailuresRemaining = 0
    static var storageBody: [String: Any]?
    static var storageLimits: [String: Any]?
    static var storagePreviewRejects = false
    static var modelImportBody: [String: Any]?
    static var modelHealthBody: [String: Any]?
    static var modelVerifyRejects = false
    static let png = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")!
    /// One frame of 16-bit mono 8 kHz PCM: the smallest thing that is still a WAV.
    static let wav: Data = {
        var body = Data()
        func le32(_ value: UInt32) -> Data { withUnsafeBytes(of: value.littleEndian) { Data($0) } }
        func le16(_ value: UInt16) -> Data { withUnsafeBytes(of: value.littleEndian) { Data($0) } }
        let pcm = Data([0, 0])
        body.append(Data("RIFF".utf8)); body.append(le32(UInt32(36 + pcm.count)))
        body.append(Data("WAVE".utf8))
        body.append(Data("fmt ".utf8)); body.append(le32(16))
        body.append(le16(1)); body.append(le16(1))          // PCM, mono
        body.append(le32(8000)); body.append(le32(16000))   // sample rate, byte rate
        body.append(le16(2)); body.append(le16(16))         // block align, bits
        body.append(Data("data".utf8)); body.append(le32(UInt32(pcm.count)))
        body.append(pcm)
        return body
    }()
    static func audioDigest() -> String { SHA256.hash(data: wav).map { String(format: "%02x", $0) }.joined() }
    /// The exact key set the Music worker's `validate_request` reads, echoed back on a job item the
    /// way the Hub's `public_item` does. All eight fields are present so the app can reconstruct
    /// the accepted settings and match an uncertain job to its original request.
    static let musicParams: [String: Any] = [
        "duration_seconds": 30.0, "steps": 8, "guidance_scale": 1.0, "shift": 3.0,
        "seed": -1, "instrumental": true, "lyrics": "[Instrumental]", "vocal_language": "unknown",
    ]
    static var holdGeneration = false
    static let generationStarted = DispatchSemaphore(value: 0)
    static let releaseGeneration = DispatchSemaphore(value: 0)
    static var heldCatalogPort: Int?
    static var catalogPorts: [Int] = []
    static let catalogStarted = DispatchSemaphore(value: 0)
    static let releaseCatalog = DispatchSemaphore(value: 0)
    static var holdNextJoin = false
    static var heldJoinStarted = false
    static let releaseJoin = DispatchSemaphore(value: 0)
    /// When set, the join route answers this status instead of 201, so a stale credential can be
    /// exercised without a real controller.
    static var joinRejectStatus: Int?
    static func setJoinReject(_ value: Int?) { lock.lock(); defer { lock.unlock() }; joinRejectStatus = value }
    static var lastMemoryBody: [String: Any]?
    static var lastMemoryMethod = ""
    static var lastMemoryToken = ""
    static var memoryReleaseCount = 0
    static var memoryUnavailable = false
    /// When true, `/api/hub/model-packages` answers the way the Hub does when one manifest is
    /// malformed: a 503 for the whole route, not a partial list.
    static var packagesUnavailable = false
    /// Remote app-update fixture: the controller's identity, the descriptor it holds, and the
    /// artifact bytes its route returns. `releaseArtifactOverride` exists so a signed descriptor
    /// can be paired with different bytes to exercise the SHA-256 refusal.
    static var releaseIdentityData: Data?
    static var releaseBodyData: Data?
    static var releaseArtifactData = Data()
    static var releaseArtifactOverride: Data?
    static var releaseArtifactReads = 0
    /// How the artifact route treats a `Range` request: answer 206 with the remainder, or answer
    /// 200 with the whole body as a server that ignores ranges would. The controller honours it.
    enum ArtifactRangeMode { case honour, ignore }
    static var releaseArtifactRangeMode: ArtifactRangeMode = .honour
    /// Every `Range` header the artifact route was sent, so a test can assert the exact resume.
    static var releaseArtifactRangeHeaders: [String] = []
    /// The gateway key the owner has installed, as the fixture's `GET /api/hub/release` reports it
    /// under `trusted_publisher`. `nil` means none is installed. A trust POST records the body and,
    /// when the key parses, replaces this value the way the real Hub would.
    static var releaseTrustedPublisher: [String: Any]?
    static var releaseTrustBodies: [[String: Any]] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    static func generatedBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return lastGenerateBody }
    static func generatedBodies() -> [[String: Any]] { lock.lock(); defer { lock.unlock() }; return generateBodies }
    static func dropNextSubmit() { lock.lock(); defer { lock.unlock() }; dropNextGeneration = true }
    static func setUncertainTranscription(_ value: Bool) { lock.lock(); defer { lock.unlock() }; uncertainTranscription = value }
    static func transcriptionBody() -> String { lock.lock(); defer { lock.unlock() }; return lastTranscriptionBody }
    static func recordedTranscriptionPorts() -> [Int] { lock.lock(); defer { lock.unlock() }; return transcriptionPorts }
    static func setCorruptImage(_ value: Bool) { lock.lock(); defer { lock.unlock() }; corruptImage = value }
    static func setCorruptAudio(_ value: Bool) { lock.lock(); defer { lock.unlock() }; corruptAudio = value }
    static func setAudioAckRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; audioAckRejects = value }
    static func audioAcks() -> [String] { lock.lock(); defer { lock.unlock() }; return audioAckPaths }
    static func setUncertainImage(_ value: Bool) { lock.lock(); defer { lock.unlock() }; uncertainImage = value }
    static func imageAckRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return imageAckPaths }
    static func discardRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return discardPaths }
    static func setImageAckRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; imageAckRejects = value }
    static func setUncertainMusic(_ value: Bool) { lock.lock(); defer { lock.unlock() }; uncertainMusic = value }
    static func setCorruptMusic(_ value: Bool) { lock.lock(); defer { lock.unlock() }; corruptMusic = value }
    static func setMusicAckRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; musicAckRejects = value }
    static func musicAckRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return musicAckPaths }
    static func transcriptionAcks() -> [String] { lock.lock(); defer { lock.unlock() }; return transcriptionAckPaths }
    static func setTranscriptionAckRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; transcriptionAckRejects = value }
    static func modelCalls() -> [String] { lock.lock(); defer { lock.unlock() }; return modelRoutes }
    static func statisticsPaths() -> [String] { lock.lock(); defer { lock.unlock() }; return statisticsQueries }
    static func storageRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return storageCalls }
    static func roleRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return roleChanges }
    static func fleetRequests() -> [String] { lock.lock(); defer { lock.unlock() }; return fleetCalls }
    static func setCredentialOrigin(_ value: String) { lock.lock(); defer { lock.unlock() }; credentialOrigin = value }
    static func failNextIdentityRead() { lock.lock(); defer { lock.unlock() }; identityFailuresRemaining = 1 }
    static func resetFleetRequests() { lock.lock(); defer { lock.unlock() }; fleetCalls = [] }
    static func joinPayload() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return joinBody }
    static func adoptPayload() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return adoptBody }
    static func storageLimitsBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return storageLimits }
    static func setStoragePreviewRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; storagePreviewRejects = value }
    static func importedFolder() -> String? { lock.lock(); defer { lock.unlock() }; return modelImportBody?["source_folder"] as? String }
    static func recheckBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return modelHealthBody }
    static func setModelVerifyRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; modelVerifyRejects = value }
    static func setHoldGeneration(_ value: Bool) { lock.lock(); defer { lock.unlock() }; holdGeneration = value }
    static func memoryBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return lastMemoryBody }
    static func memoryRequest() -> (String, String) { lock.lock(); defer { lock.unlock() }; return (lastMemoryMethod, lastMemoryToken) }
    static func releases() -> Int { lock.lock(); defer { lock.unlock() }; return memoryReleaseCount }
    static func setMemoryUnavailable(_ value: Bool) { lock.lock(); defer { lock.unlock() }; memoryUnavailable = value }
    static func setPackagesUnavailable(_ value: Bool) { lock.lock(); defer { lock.unlock() }; packagesUnavailable = value }
    static func setRelease(identity: Data?, body: Data?, artifact: Data, override: Data? = nil,
                           trustedPublisher: [String: Any]? = nil) {
        lock.lock(); defer { lock.unlock() }
        releaseIdentityData = identity
        releaseBodyData = body
        releaseArtifactData = artifact
        releaseArtifactOverride = override
        releaseTrustedPublisher = trustedPublisher
    }
    static func clearRelease() {
        lock.lock(); defer { lock.unlock() }
        releaseIdentityData = nil
        releaseBodyData = nil
        releaseArtifactData = Data()
        releaseArtifactOverride = nil
        releaseTrustedPublisher = nil
        releaseTrustBodies = []
        releaseArtifactRangeMode = .honour
        releaseArtifactRangeHeaders = []
    }
    static func releaseReads() -> Int { lock.lock(); defer { lock.unlock() }; return releaseArtifactReads }
    static func setArtifactRangeMode(_ mode: ArtifactRangeMode) {
        lock.lock(); defer { lock.unlock() }; releaseArtifactRangeMode = mode
    }
    static func artifactRangeHeaders() -> [String] {
        lock.lock(); defer { lock.unlock() }; return releaseArtifactRangeHeaders
    }
    static func resetArtifactRequests() {
        lock.lock(); defer { lock.unlock() }
        releaseArtifactReads = 0
        releaseArtifactRangeHeaders = []
    }
    static func releaseTrustRequests() -> [[String: Any]] {
        lock.lock(); defer { lock.unlock() }; return releaseTrustBodies
    }
    static func setTrustedPublisher(_ value: [String: Any]?) {
        lock.lock(); defer { lock.unlock() }; releaseTrustedPublisher = value
    }
    static func waitForGenerationStart() -> Bool { generationStarted.wait(timeout: .now() + 2) == .success }
    static func holdCatalog(on port: Int) { lock.lock(); defer { lock.unlock() }; heldCatalogPort = port }
    static func waitForCatalogStart() -> Bool { catalogStarted.wait(timeout: .now() + 2) == .success }
    static func catalogCount(on port: Int) -> Int { lock.lock(); defer { lock.unlock() }; return catalogPorts.filter { $0 == port }.count }
    static func catalogCount() -> Int { lock.lock(); defer { lock.unlock() }; return catalogPorts.count }
    static func holdJoin() { lock.lock(); defer { lock.unlock() }; holdNextJoin = true; heldJoinStarted = false }
    static func joinHasStarted() -> Bool { lock.lock(); defer { lock.unlock() }; return heldJoinStarted }
    static func releaseHeldJoin() { releaseJoin.signal() }

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

    /// The modalities the Hub's `/studio/<modality>/...` peer surface actually routes.
    ///
    /// Hardcoded from `WORKER_FOR_MODALITY` in `components/hub/standalone.py`, and hardcoded
    /// deliberately: this fixture exists to notice when the app and the Hub disagree, so it must
    /// not derive the answer from the app. It previously answered `/studio/subtitle/...`, which
    /// the real Hub refuses with 404 "unknown studio" - so every gate passed while the app's
    /// Transcribe button could not work, and only the owner clicking it found out.
    private static let hubRoutedModalities: Set<String> = ["image", "voice", "transcription", "music", "chat"]

    override func startLoading() {
        let path = request.url?.path ?? ""
        let method = request.httpMethod ?? "GET"
        if path.hasPrefix("/studio/") {
            let modality = String(path.dropFirst("/studio/".count).split(separator: "/").first ?? "")
            if !Self.hubRoutedModalities.contains(modality) {
                // Exactly what the Hub answers, so this fixture cannot accept a path the real
                // Hub refuses.
                finish((404, "application/json", Data(#"{"detail":"unknown studio"}"#.utf8)))
                return
            }
        }
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
        // The artifact route is range-capable like the controller's. A `Range` request is answered
        // with 206 and the remainder, unless the test asks this server to ignore ranges (200 with
        // the whole body), which is exactly the case a resuming client must handle.
        if method == "GET", path == "/api/hub/release/artifact" {
            Self.lock.lock()
            Self.releaseArtifactReads += 1
            let range = request.value(forHTTPHeaderField: "Range")
            if let range { Self.releaseArtifactRangeHeaders.append(range) }
            let mode = Self.releaseArtifactRangeMode
            let releaseArtifact = Self.releaseArtifactOverride ?? Self.releaseArtifactData
            Self.lock.unlock()
            if mode == .honour, let range, let start = Self.rangeStart(range),
               start > 0, start < releaseArtifact.count {
                let slice = releaseArtifact.subdata(in: start..<releaseArtifact.count)
                finish((206, "application/octet-stream", slice), headers: [
                    "Content-Range": "bytes \(start)-\(releaseArtifact.count - 1)/\(releaseArtifact.count)",
                    "Accept-Ranges": "bytes",
                ])
                return
            }
            finish((200, "application/octet-stream", releaseArtifact), headers: ["Accept-Ranges": "bytes"])
            return
        }
        let result: (Int, String, Data)
        if method == "GET", path.hasPrefix("/api/hub/jobs/") { Self.noteJobDetail() }
        switch (method, path) {
        case ("GET", "/api/hub/catalog"):
            result = (200, "application/json", Self.catalogFixture)
        case ("GET", "/api/hub/model-packages"):
            Self.lock.lock(); let packagesDown = Self.packagesUnavailable; Self.lock.unlock()
            // One malformed manifest is enough for the Hub to refuse the whole route. The app must
            // survive that with the catalog intact.
            result = packagesDown
                ? (503, "application/json", Data(#"{"detail":"PackageError: manifest for one model is malformed"}"#.utf8))
                : (200, "application/json", Self.packagesFixture)
        case ("POST", "/api/hub/model-health/recheck"):
            let body = requestBody()
            Self.lock.lock()
            Self.modelHealthBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"studio":"voice","model":"fixture/paused"}"#.utf8))
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
        case ("GET", "/api/hub/workers"):
            result = (200, "application/json", Data(#"{"workers":[{"id":"image","modality":"image"},{"id":"voice","modality":"voice"},{"id":"subtitle","modality":"transcription"}]}"#.utf8))
        case ("POST", "/api/hub/fleet/nodes"):
            let body = requestBody()
            Self.lock.lock()
            Self.fleetCalls.append("join")
            Self.joinBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            let hold = Self.holdNextJoin
            if hold { Self.holdNextJoin = false; Self.heldJoinStarted = true }
            let reject = Self.joinRejectStatus
            Self.lock.unlock()
            if let reject {
                finish((reject, "application/json", Data(#"{"detail":"invalid fleet token"}"#.utf8)))
                return
            }
            if hold {
                DispatchQueue.global().async { [self] in
                    _ = Self.releaseJoin.wait(timeout: .now() + 5)
                    finish((201, "application/json", Data(#"{"node":{"id":"studio-mini","name":"Studio Mini","address":"http://192.168.1.20:47873","role":"agent"}}"#.utf8)))
                }
                return
            }
            result = (201, "application/json", Data(#"{"node":{"id":"studio-mini","name":"Studio Mini","address":"http://192.168.1.20:47873","role":"agent"}}"#.utf8))
        case ("POST", "/api/hub/fleet/adopt"):
            let body = requestBody()
            Self.lock.lock()
            Self.fleetCalls.append("adopt")
            Self.adoptBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"adopted":true}"#.utf8))
        case ("POST", "/api/hub/fleet/leave"):
            Self.lock.lock()
            Self.fleetCalls.append("leave")
            // Leaving is the Hub's own doing: it mints a fresh credential and stops being a node.
            Self.credentialOrigin = "minted"
            Self.currentRole = "standalone"
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"left":true}"#.utf8))
        case ("DELETE", "/api/hub/fleet/nodes/controller-local"):
            // The controller is asked to drop this Mac while the borrowed token still works, so a
            // Mac that leaves does not stay on the roster as a node that can never be reached.
            Self.lock.lock(); Self.fleetCalls.append("dropped-from-controller"); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("GET", "/api/hub/fleet/nodes"):
            Self.lock.lock(); Self.fleetCalls.append("list"); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"role":"controller","nodes":[{"id":"studio-2","name":"Studio 2","address":"http://192.168.1.21:8080","state":"reachable","detail":null,"version":"0.4.0","joined_at":1.0},{"id":"studio-3","name":"Studio 3","address":"http://192.168.1.22:8080","state":"unreachable","detail":"could not reach the node","version":null,"joined_at":2.0}],"summary":{"total":2,"reachable":1,"unreachable":1,"error":0}}"#.utf8))
        case ("GET", "/studio/voice/api/voices"):
            Self.lock.lock()
            Self.voiceListCalls += 1
            var rows: [[String: Any]] = [
                ["id": "voice-1", "name": "Narrator", "language": "en", "gender": "f",
                 "notes": "booth", "license": "self-owned", "source_url": NSNull(),
                 "permission_acknowledged": true, "has_transcript": true,
                 "transcript": "The exact words in the clip", "audio_extension": ".wav",
                 "duration_seconds": 12.5, "sample_rate": 24000, "channels": 1,
                 "fleet_managed": false, "audio_sha256": "ab", "created_at": 1.0,
                 "audio_url": "/api/voices/voice-1/audio", "audio_filename": "reference.wav"],
                ["id": "voice-2", "name": "No words yet", "language": "en", "gender": "n",
                 "notes": "", "license": "self-owned", "source_url": NSNull(),
                 "permission_acknowledged": true, "has_transcript": false,
                 "transcript": "", "audio_extension": ".wav", "duration_seconds": 4.0,
                 "sample_rate": 24000, "channels": 1, "fleet_managed": false,
                 "audio_sha256": "cd", "created_at": 2.0,
                 "audio_url": "/api/voices/voice-2/audio", "audio_filename": "reference.wav"],
            ]
            rows.append(contentsOf: Self.voiceAdded)
            Self.lock.unlock()
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: ["voices": rows]))
        case ("POST", "/studio/voice/api/voices"):
            // The worker stores what it was given and returns it, so the fixture's list grows
            // with each add. The body is a file upload, and `requestBody()` cannot read a
            // streamed upload - the existing tests check paths for the same reason - so the
            // fixture answers with the names these tests import, in the order they arrive.
            Self.lock.lock()
            Self.voiceAddCalls += 1
            let knownNames = ["Imported Narrator", "Imported Subtitled"]
            let name = Self.voiceAddCalls <= knownNames.count
                ? knownNames[Self.voiceAddCalls - 1]
                : "Imported voice \(Self.voiceAddCalls)"
            let identifier = "voice-added-\(Self.voiceAddCalls)"
            let added: [String: Any] = ["id": identifier, "name": name, "language": "en",
                "gender": "n", "notes": "", "license": "self-owned", "source_url": NSNull(),
                "permission_acknowledged": true, "has_transcript": true,
                "transcript": "imported words", "audio_extension": ".wav",
                "duration_seconds": 1.0, "sample_rate": 24000, "channels": 1,
                "fleet_managed": false, "audio_sha256": "ef", "created_at": 3.0,
                "audio_url": "/api/voices/\(identifier)/audio", "audio_filename": "reference.wav"]
            Self.voiceAdded.append(added)
            Self.lock.unlock()
            result = (201, "application/json", try! JSONSerialization.data(withJSONObject: ["voice": added]))
        case ("PATCH", "/studio/voice/api/voices/voice-1"):
            Self.lock.lock(); Self.voiceEditCalls += 1; Self.lock.unlock()
            result = (200, "application/json", Data(#"{"voice":{"id":"voice-1","name":"Narrator","language":"en","gender":"f","notes":"booth","license":"self-owned","source_url":null,"permission_acknowledged":true,"has_transcript":true,"transcript":"Corrected","audio_extension":".wav","duration_seconds":12.5,"sample_rate":24000,"channels":1,"fleet_managed":false,"audio_sha256":"ab","created_at":1.0,"audio_url":"/api/voices/voice-1/audio","audio_filename":"reference.wav"}}"#.utf8))
        case ("GET", "/studio/voice/api/voices/voice-1/audio"):
            Self.lock.lock(); Self.voiceAudioCalls += 1; Self.lock.unlock()
            result = (200, "audio/wav", Self.wav)
        case ("DELETE", "/studio/voice/api/voices/voice-2"):
            Self.lock.lock(); Self.voiceDeleteCalls += 1; Self.lock.unlock()
            result = (200, "application/json", Data(#"{"deleted":true}"#.utf8))
        case ("POST", "/studio/transcription/api/transcribe"):
            // The synchronous route the app uses to transcribe a clip on the spot. It answers
            // with the words, which is what the "Transcribe Clip" button fills in.
            Self.lock.lock(); Self.syncTranscribeCalls += 1; Self.lock.unlock()
            result = (200, "application/json", Data(#"{"text":"The exact words in the clip","language":"en","duration":12.5,"model":"mlx-community/whisper-large-v3-turbo"}"#.utf8))
        case ("POST", "/studio/voice/api/settings"):
            Self.lock.lock(); Self.tokenSaves.append(requestBody()); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"hf_token_set":true,"hf_token_masked":"hf_…ent"}"#.utf8))
        case ("POST", "/studio/voice/api/settings/test-hf-token"):
            result = (200, "application/json", Data(#"{"ok":true,"name":"Fixture User","type":"user","orgs":["fixture-org"]}"#.utf8))
        case ("GET", "/api/hub/fleet/overview"):
            Self.lock.lock(); Self.fleetCalls.append("overview"); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"machines":[{"machine":"local","label":"This Mac","role":"agent","address":null,"reachable":true,"version":"0.4.0","health":"up","detail":null,"memory_gb":16.0,"available_gb":9.5,"disk_free_gb":220.0,"disk_total_gb":494.0,"chip":"Apple M4","jobs":12,"average_seconds":41.5,"modalities":{"image":9,"transcription":3}},{"machine":"studio-3","label":"Studio 3","role":"agent","address":"http://192.168.1.20:47873","reachable":false,"version":null,"health":"unknown","detail":"this Mac has not reported in yet","memory_gb":null,"available_gb":null,"disk_free_gb":null,"disk_total_gb":null,"chip":null,"jobs":0,"average_seconds":null,"modalities":{}}],"role":"agent","controller":{"machine":"controller-mac","label":"Control Mac","role":"controller","address":"http://192.168.1.21:47873","state":"reachable","detail":null,"version":"0.4.0"},"roster_readable":true,"summary":{"total":2,"reachable":1,"unreachable":1}}"#.utf8))
        case ("DELETE", "/api/hub/fleet/nodes/studio-3"):
            Self.lock.lock(); Self.fleetCalls.append("leave:studio-3"); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("GET", "/api/hub/controller"):
            Self.lock.lock()
            let roleNow = Self.currentRole
            let originNow = Self.credentialOrigin
            let fail = Self.identityFailuresRemaining > 0
            if fail { Self.identityFailuresRemaining -= 1 }
            Self.lock.unlock()
            result = fail
                ? (503, "application/json", Data(#"{"detail":"identity temporarily unavailable"}"#.utf8))
                : (200, "application/json", Data("{\"settings\":{\"version\":2,\"role\":\"\(roleNow)\",\"site_id\":\"studiohub-local\",\"site_name\":\"This Mac\",\"controller_id\":\"controller-local\",\"database_mode\":\"off\",\"machine_name\":\"Studio Mini\",\"database_configured\":false,\"sqlite_authoritative\":true,\"global_authority\":\"genstudio\",\"migration_stage\":\"local\",\"fleet_credential_origin\":\"\(originNow)\"},\"runtime\":{\"state\":\"idle\"},\"readiness\":{\"ready\":true,\"role\":\"\(roleNow)\",\"site_id\":\"studiohub-local\",\"controller_id\":\"controller-local\",\"migration_stage\":\"local\",\"sqlite_authoritative\":true,\"global_job_claiming\":false,\"global_authority\":\"genstudio\",\"reason\":null,\"telemetry_warning\":null},\"capacity\":{\"machines\":1,\"queue_depth\":0}}".utf8))
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
            result = (200, "application/json", Self.transcriptionFixture)
        case ("GET", "/studio/transcription/api/transcribe/availability"):
            // The Subtitle studio reads its availability from the subtitle worker, not Voice.
            result = (200, "application/json", Self.transcriptionFixture)
        case ("GET", "/studio/voice/api/generate/availability"):
            result = (200, "application/json", Data(#"{"qwen3_preset_speakers":[{"id":"Ryan","lang":"en","gender":"male","description":"Warm"},{"id":"Aiden","lang":"en","gender":"male","description":"Clear"}],"secondvoice_preset_speakers":[{"id":"Sophea","lang":"km","gender":"female","description":"Clear"},{"id":"Dara","lang":"km","gender":"male","description":"Warm"}]}"#.utf8))
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
        case ("GET", "/studio/voice/api/model-storage"):
            result = (200, "application/json", Data(#"{"cache_root":"/tmp/hub","groups":[{"id":"voxcpm2","label":"VoxCPM2 (MLX)","bytes_total":5000000000,"memory":{"required_gb":8,"this_mac_gb":16,"runs_here":true}},{"id":"fish-s2-pro","label":"Fish Audio S2 Pro (MLX)","bytes_total":17000000000,"memory":{"required_gb":32,"this_mac_gb":16,"runs_here":false}}]}"#.utf8))
        case ("GET", "/api/hub/jobs"):
            Self.lock.lock()
            let pendingImage = Self.uncertainImage
            let pendingMusic = Self.uncertainMusic
            Self.lock.unlock()
            // The unresolved request is a *different* batch (older, so it does not become the most
            // recent job). It used to be the same id flipping from done to uncertain, which cannot
            // happen now that a finished job's detail is fetched once - and cannot happen in
            // production either, where nothing can re-open a finished job. Music mirrors Image.
            var batches: [[String: String]] = [
                ["id": "old-job", "modality": "voice"],
                ["id": "image-job", "modality": "image"],
                ["id": "music-job", "modality": "music"],
                // One modality that maps to a Studio with no batch history, and one this build has
                // never heard of. Both must be named in the startup note: the old filter skipped
                // anything not on its list before the loop, so neither was shown, counted or
                // reported.
                ["id": "video-job", "modality": "video"],
                ["id": "hologram-job", "modality": "hologram"],
            ]
            if pendingImage { batches.append(["id": "image-uncertain", "modality": "image"]) }
            if pendingMusic { batches.append(["id": "music-uncertain", "modality": "music"]) }
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: ["batches": batches]))
        case ("GET", "/api/hub/jobs/old-job"):
            Self.lock.lock(); let refuse = Self.failJobDetail; Self.lock.unlock()
            if refuse { result = (500, "application/json", Data(#"{"detail":"fixture cannot read this batch"}"#.utf8)); break }
            result = (200, "application/json", Data(#"{"id":"old-job","modality":"voice","model":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","created_at":10,"items":[{"index":0,"state":"done","progress":1.0,"prompt":"Earlier","artifact_url":"/api/hub/jobs/old-job/items/0/artifact","runtime_s":31.0}]}"#.utf8))
        case ("GET", "/api/hub/jobs/new-job"):
            // `created_at` matters: history is sorted by it, and the assertions below take the
            // most recent job, which is this one.
            let audioBody: [String: Any] = ["id": "new-job", "modality": "voice", "model": "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", "created_at": 20,
                "items": [["index": 0, "state": "done", "progress": 1.0, "prompt": "Hello locally",
                           "artifact_url": "/api/hub/jobs/new-job/items/0/artifact",
                           "bytes": Self.wav.count, "sha256": Self.audioDigest(),
                           "media_type": "audio/wav",
                           "audio_duration_s": 12.5, "runtime_s": 52.2]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: audioBody))
        case ("GET", "/api/hub/jobs/new-job/items/0/artifact"):
            Self.lock.lock(); let corrupt = Self.corruptAudio; Self.lock.unlock()
            result = (200, "audio/wav", corrupt ? Data([0x52, 0x49, 0x46, 0x46]) : Self.wav)
        case ("POST", "/api/hub/jobs/new-job/items/0/ack"):
            Self.lock.lock(); Self.audioAckPaths.append(path); let rejects = Self.audioAckRejects; Self.lock.unlock()
            result = rejects
                ? (502, "application/json", Data(#"{"detail":"voice worker did not acknowledge receipt"}"#.utf8))
                : (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("GET", "/api/hub/jobs/image-uncertain"):
            let uncertainSHA = SHA256.hash(data: Self.png).map { String(format: "%02x", $0) }.joined()
            let pending: [String: Any] = ["id": "image-uncertain", "modality": "image", "model": "fixture/flux", "created_at": 20,
                "items": [["index": 0, "state": "uncertain", "prompt": "A quiet garden", "artifact_url": "/api/hub/jobs/image-uncertain/items/0/artifact", "bytes": Self.png.count, "sha256": uncertainSHA]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: pending))
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
            let originalRequestID = Self.generateBodies.last?["client_request_id"] as? String ?? ""
            Self.lock.unlock()
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: ["reconciled": true, "client_request_id": originalRequestID]))
        case ("POST", "/api/hub/jobs/image-job/items/0/discard"),
             ("POST", "/api/hub/jobs/image-uncertain/items/0/discard"):
            Self.lock.lock(); Self.discardPaths.append(path); Self.uncertainImage = false; Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"batch_id":"image-job","item_index":0}"#.utf8))
        case ("GET", "/api/hub/jobs/music-uncertain"):
            let uncertainSHA = SHA256.hash(data: Self.wav).map { String(format: "%02x", $0) }.joined()
            let pending: [String: Any] = ["id": "music-uncertain", "modality": "music", "model": "ACE-Step/Ace-Step1.5", "created_at": 20,
                "items": [["index": 0, "state": "uncertain", "prompt": "Warm lo-fi beat", "artifact_url": "/api/hub/jobs/music-uncertain/items/0/artifact", "bytes": Self.wav.count, "sha256": uncertainSHA,
                           "params": Self.musicParams]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: pending))
        case ("GET", "/api/hub/jobs/music-job"):
            let sha = SHA256.hash(data: Self.wav).map { String(format: "%02x", $0) }.joined()
            Self.lock.lock(); let uncertain = Self.uncertainMusic; Self.lock.unlock()
            let body: [String: Any] = ["id": "music-job", "modality": "music", "model": "ACE-Step/Ace-Step1.5", "created_at": 35,
                "items": [["index": 0, "state": uncertain ? "uncertain" : "done", "progress": 1.0, "prompt": "Warm lo-fi beat",
                           "artifact_url": "/api/hub/jobs/music-job/items/0/artifact", "bytes": Self.wav.count, "sha256": sha,
                           "media_type": "audio/wav", "audio_duration_s": 30.0, "runtime_s": 89.0,
                           "params": Self.musicParams]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: body))
        case ("GET", "/api/hub/jobs/music-job/items/0/artifact"):
            Self.lock.lock(); let corrupt = Self.corruptMusic; Self.lock.unlock()
            result = (200, "audio/wav", corrupt ? Data([0x52, 0x49, 0x46, 0x46]) : Self.wav)
        case ("POST", "/api/hub/jobs/music-job/items/0/ack"):
            Self.lock.lock(); Self.musicAckPaths.append(path); let rejects = Self.musicAckRejects; Self.lock.unlock()
            result = rejects
                ? (502, "application/json", Data(#"{"detail":"music worker did not acknowledge receipt"}"#.utf8))
                : (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("POST", "/api/hub/jobs/music-job/items/0/reconcile"):
            Self.lock.lock()
            Self.uncertainMusic = false
            let originalRequestID = Self.generateBodies.last?["client_request_id"] as? String ?? ""
            Self.lock.unlock()
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: ["reconciled": true, "client_request_id": originalRequestID]))
        case ("POST", "/api/hub/jobs/music-job/items/0/discard"),
             ("POST", "/api/hub/jobs/music-uncertain/items/0/discard"):
            Self.lock.lock(); Self.discardPaths.append(path); Self.uncertainMusic = false; Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"batch_id":"music-job","item_index":0}"#.utf8))
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
                if object["modality"] as? String == "music" {
                    result = (200, "application/json", Data(#"{"batch_id":"music-job","items":1,"replayed":false}"#.utf8))
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
            result = (200, "audio/wav", Self.wav)
        case ("GET", "/api/hub/release/identity"):
            Self.lock.lock(); let releaseIdentity = Self.releaseIdentityData; Self.lock.unlock()
            result = releaseIdentity.map { (200, "application/json", $0) }
                ?? (404, "application/json", Data(#"{"detail":"no release identity"}"#.utf8))
        case ("GET", "/api/hub/release"):
            Self.lock.lock()
            let releaseBody = Self.releaseBodyData
            let trustedPublisher = Self.releaseTrustedPublisher
            Self.lock.unlock()
            var answer: [String: Any] = ["release": NSNull()]
            if let releaseBody,
               let parsed = (try? JSONSerialization.jsonObject(with: releaseBody)) as? [String: Any] {
                answer = parsed
            }
            answer["trusted_publisher"] = trustedPublisher ?? NSNull()
            result = (200, "application/json",
                      (try? JSONSerialization.data(withJSONObject: answer))
                          ?? Data(#"{"release":null,"trusted_publisher":null}"#.utf8))
        case ("POST", "/api/hub/release/trust"):
            let body = ((try? JSONSerialization.jsonObject(with: requestBody())) as? [String: Any]) ?? [:]
            Self.lock.lock()
            Self.releaseTrustBodies.append(body)
            let typedKey = body["public_key"] as? String
            if let typedKey, let fingerprint = ReleaseSignature.fingerprint(publicKeyHex: typedKey) {
                let named = (body["publisher_id"] as? String).flatMap { $0.isEmpty ? nil : $0 }
                Self.releaseTrustedPublisher = ["publisher_id": named ?? NSNull(),
                                                "fingerprint": fingerprint]
            }
            let installed = Self.releaseTrustedPublisher
            Self.lock.unlock()
            let fingerprint = installed?["fingerprint"] as? String
            let publisherId = installed?["publisher_id"] as? String
            result = (200, "application/json",
                      (try? JSONSerialization.data(withJSONObject: [
                        "ok": true,
                        "publisher_id": publisherId.map { $0 as Any } ?? NSNull(),
                        "publisher_key_id": fingerprint.map { $0 as Any } ?? NSNull(),
                        "fingerprint": fingerprint.map { $0 as Any } ?? NSNull(),
                      ])) ?? Data(#"{"ok":true}"#.utf8))
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
#"{"models":[{"repo":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","label":"Qwen CustomVoice","family":"qwen3-tts","size_gb":1.9,"capabilities":["tts","multilingual","expressive"],"languages":["en","zh"],"runtime_ready":true,"cache":{"state":"cached"},"execution_contract":{"qualification_source":"audit","text_max_characters":4000}},{"repo":"other/tts","label":"Other TTS","family":"other","size_gb":2.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/paused","label":"Paused fixture","family":"other","size_gb":1.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"}},{"repo":"mlx-community/whisper-base-mlx","label":"Whisper base","languages":"Multilingual"},{"repo":"fixture/flux","label":"FLUX fixture","family":"flux2-klein","capabilities":["txt2img"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"width":1280,"height":720,"steps":4,"guidance":1.0,"seed":-1},"image_sizes":[{"id":"16:9","label":"Wide 16:9","width":1280,"height":720},{"id":"1:1","label":"Square","width":1024,"height":1024}],"generation_limits":{"min_steps":1,"max_steps":50,"min_guidance":0.0,"max_guidance":10.0,"min_seed":-1,"max_seed":4294967295}},{"repo":"mlx-community/VoxCPM2-4bit","label":"VoxCPM2 4-bit","family":"voxcpm-mlx","size_gb":3.0,"capabilities":["tts","voice-cloning"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}},{"repo":"ACE-Step/Ace-Step1.5","label":"ACE-Step 1.5 Turbo","family":"ace-step-1.5","size_gb":7.7,"capabilities":["music","instrumental","text-to-music","lyrics","long-form"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"duration_seconds":30.0,"steps":8,"guidance_scale":1.0,"shift":3.0,"seed":-1,"instrumental":true,"lyrics":"[Instrumental]","vocal_language":"unknown","dit_model":"acestep-v15-turbo","lm_model":"acestep-5Hz-lm-0.6B"},"generation_limits":{"min_duration_seconds":1.0,"max_duration_seconds":600.0,"single_pass_max_seconds":480,"min_steps":1,"max_steps":50,"min_guidance":0.0,"max_guidance":30.0,"min_seed":-1,"max_seed":4294967295},"sample_rate_hz":48000,"channels":2},{"repo":"mlx-community/chatterbox-8bit","label":"Chatterbox 8-bit","size_gb":3.4},{"repo":"mlx-community/chatterbox-8bit","label":"Chatterbox 8-bit (MLX)","family":"chatterbox-mlx","size_gb":3.4,"capabilities":["tts","voice-cloning","expressive","multilingual"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}},{"repo":"mlx-community/fish-audio-s2-pro-8bit","label":"Fish Audio S2 Pro 8-bit (MLX)","family":"fish-audio-mlx","size_gb":6.7,"capabilities":["tts","voice-cloning","multilingual","expressive"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}},{"repo":"khmerttsopensource/khmer-tts","label":"Khmer TTS (MMS VITS)","family":"khmer-tts-mms","size_gb":0.4,"capabilities":["tts","multilingual"],"languages":["km"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"sengtha/khmer-tts-female-v2","label":"Khmer TTS Female (VITS/ONNX)","family":"khmer-tts-onnx","size_gb":0.12,"capabilities":["tts","multilingual"],"languages":["km"],"runtime_ready":true,"cache":{"state":"uncached"}},{"repo":"mlx-community/pocket-tts-4bit","label":"Pocket TTS 4-bit (MLX)","family":"pocket-tts","size_gb":1.1,"capabilities":["tts","voice-cloning"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"},"reference_audio":{"declared":true,"supported":true}},{"repo":"fixture/unwired-voice","label":"Voice model with no runtime","family":"fixture-unwired-voice","size_gb":1.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":false,"cache":{"state":"cached"}},{"repo":"mlx-community/S3TokenizerV2","label":"S3 speech tokenizer","family":"s3tokenizer","size_gb":0.5,"capabilities":["codec"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/zimage","label":"Z-Image fixture 4-bit","family":"z-image","size_gb":3.2,"capabilities":["txt2img"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"width":1024,"height":1024,"steps":8,"seed":-1},"generation_limits":{"min_steps":1,"max_steps":20,"min_seed":-1,"max_seed":4294967295}},{"repo":"fixture/second-music","label":"Second Music fixture","family":"stable-audio","size_gb":4.1,"capabilities":["music","instrumental"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"duration_seconds":20.0,"steps":10},"generation_limits":{"min_duration_seconds":5.0,"max_duration_seconds":60.0},"sample_rate_hz":44100,"channels":2},{"repo":"fixture/second-preset","label":"Second Preset Voice","family":"secondvoice","size_gb":0.9,"capabilities":["tts","multilingual"],"languages":["km"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"Qwen/Qwen3.5-9B","label":"Qwen3.5-9B 4-bit","family":"chat-mlx","size_gb":5.5,"capabilities":["chat.completion","text-generation"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/chat-conversational","label":"Fixture Conversational 4-bit","family":"qwen3","size_gb":2.2,"capabilities":["conversational"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/chat-family","label":"Fixture Chat Family 4-bit","family":"chat-mlx","size_gb":1.1,"capabilities":["text2text"],"runtime_ready":true,"cache":{"state":"uncached"}},{"repo":"fixture/chat-annotated","label":"Fixture Annotated Chat 4-bit","family":"fixture-llm","size_gb":0.8,"capabilities":["text2text"],"hub_modality":"chat","runtime_ready":true,"cache":{"state":"cached"}}]}"#.utf8)

    /// Shaped like the Hub's own reply. The same repo appears four times at different levels of
    /// detail, exactly as the aggregate catalogue delivers it — one studio publishes the full
    /// transcription contract, others only a name — so the app's dedupe is exercised rather than
    /// assumed. A bare row sits both before and after the rich one: neither "first seen" nor "last
    /// seen" may win, because both would lose the engine and cache state the Subtitle form needs.
    private static let transcriptionFixture = Data(#"{"available":true,"default_model":"mlx-community/whisper-large-v3-turbo","models":[{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","size_gb":1.6},{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","engine":"whisper"},{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","size_gb":1.6,"note":"Recommended","engine":"subtitle-mlx","languages":"Multilingual","supports_word_timestamps":true,"recommended":true,"cached":true},{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo"},{"repo":"moonshine-ai/moonshine-base","label":"Moonshine","size_gb":0.25,"note":"Pilot","engine":"moonshine","languages":"English","supports_word_timestamps":false,"recommended":false,"cached":true},{"repo":"fixture/whisper-unpackaged","label":"Whisper fixture with no Hub package","size_gb":1.0,"engine":"whisper-mlx","languages":"Multilingual","supports_word_timestamps":true,"recommended":false,"cached":true}]}"#.utf8)

    private static let packagesFixture = Data(#"{"models":[{"package_id":"synthetic-custom-voice","model_id":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-flux","model_id":"fixture/flux","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-paused","model_id":"fixture/paused","state":"files_verified","dispatch_state":"eligible","files_state":"files_verified","runtime_state":"ready","os_state":"ready","chip_state":"ready","worker_state":"running","paused":true,"paused_reason":"RuntimeError: SystemExit: 1","paused_studio":"voice"},{"package_id":"ace-step-1-5-turbo","model_id":"ACE-Step/Ace-Step1.5","state":"files_verified","dispatch_state":"eligible","files_state":"files_verified","runtime_state":"ready","os_state":"ready","chip_state":"ready","worker_state":"running"},{"package_id":"whisper-turbo","model_id":"mlx-community/whisper-large-v3-turbo","state":"files_verified","dispatch_state":"eligible"},{"package_id":"moonshine-base","model_id":"moonshine-ai/moonshine-base","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-zimage","model_id":"fixture/zimage","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-second-music","model_id":"fixture/second-music","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-second-preset","model_id":"fixture/second-preset","state":"files_verified","dispatch_state":"eligible"},{"package_id":"qwen3-5-9b","model_id":"Qwen/Qwen3.5-9B","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-chat-conversational","model_id":"fixture/chat-conversational","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-chat-family","model_id":"fixture/chat-family","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-chat-annotated","model_id":"fixture/chat-annotated","state":"files_verified","dispatch_state":"eligible"},{"package_id":"voxcpm2-4bit","model_id":"mlx-community/VoxCPM2-4bit","state":"files_verified","dispatch_state":"ineligible","files_state":"files_verified","runtime_state":"ready","os_state":"unsupported","chip_state":"ready","worker_state":"running","minimum_macos":"15.0","macos_version":"14.5"},{"package_id":"chatterbox-8bit","model_id":"mlx-community/chatterbox-8bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fish-audio-s2-pro-8bit","model_id":"mlx-community/fish-audio-s2-pro-8bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"khmer-tts-mms","model_id":"khmerttsopensource/khmer-tts","state":"files_verified","dispatch_state":"eligible"},{"package_id":"khmer-tts-onnx","model_id":"sengtha/khmer-tts-female-v2","state":"files_verified","dispatch_state":"eligible"},{"package_id":"pocket-tts-4bit","model_id":"mlx-community/pocket-tts-4bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-unwired-voice","model_id":"fixture/unwired-voice","state":"files_verified","dispatch_state":"eligible"}]}"#.utf8)

    /// The catalogue exactly as the fixture serves it, so a test can derive what the app must
    /// show from the data the app was given instead of restating it by hand. A hand-written
    /// expected list is what let the Audio picker keep rejecting families it had never heard of.
    static var catalogModels: [[String: Any]] {
        ((try? JSONSerialization.jsonObject(with: catalogFixture)) as? [String: Any])?["models"]
            as? [[String: Any]] ?? []
    }

    /// The repos the fixture's Hub reports a package for.
    static var packageModelIDs: Set<String> {
        let models = ((try? JSONSerialization.jsonObject(with: packagesFixture)) as? [String: Any])?["models"]
            as? [[String: Any]] ?? []
        return Set(models.compactMap { $0["model_id"] as? String })
    }

    /// The Hub package id the fixture publishes for each repo, which is how the app addresses
    /// verification and downloads.
    static var packageIDsByRepo: [String: String] {
        let models = ((try? JSONSerialization.jsonObject(with: packagesFixture)) as? [String: Any])?["models"]
            as? [[String: Any]] ?? []
        var result: [String: String] = [:]
        for row in models {
            guard let repo = row["model_id"] as? String, let package = row["package_id"] as? String else { continue }
            result[repo] = package
        }
        return result
    }

    /// The transcription rows the worker's availability route publishes, exactly as served.
    static var transcriptionModels: [[String: Any]] {
        ((try? JSONSerialization.jsonObject(with: transcriptionFixture)) as? [String: Any])?["models"]
            as? [[String: Any]] ?? []
    }

    private func finish(_ result: (Int, String, Data), headers: [String: String] = [:]) {
        var fields = headers
        if fields["Content-Type"] == nil { fields["Content-Type"] = result.1 }
        let response = HTTPURLResponse(url: request.url!, statusCode: result.0,
            httpVersion: "HTTP/1.1", headerFields: fields)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: result.2)
        client?.urlProtocolDidFinishLoading(self)
    }

    /// `bytes=1000-` -> `1000`.
    private static func rangeStart(_ range: String) -> Int? {
        guard range.lowercased().hasPrefix("bytes=") else { return nil }
        let spec = range.dropFirst("bytes=".count)
        guard let start = spec.split(separator: "-").first else { return nil }
        return Int(start)
    }

    override func stopLoading() {}
}

// MARK: - Remote update fixtures (throwaway keys only)

private func releaseHex(_ data: Data) -> String {
    data.map { String(format: "%02x", $0) }.joined()
}

private func releaseSha256(_ data: Data) -> String {
    releaseHex(Data(SHA256.hash(data: data)))
}

private func releaseIdentityJSON(publicKey: Data, fingerprint: String,
                                 algorithm: String = "ed25519") -> Data {
    try! JSONSerialization.data(withJSONObject: [
        "algorithm": algorithm,
        "public_key": releaseHex(publicKey),
        "fingerprint": fingerprint,
    ])
}

private func releaseBody(_ descriptor: [String: Any]) -> Data {
    try! JSONSerialization.data(withJSONObject: ["release": descriptor])
}

private func releaseSign(_ object: [String: Any], removing field: String,
                         key: Curve25519.Signing.PrivateKey) -> String {
    var body = object
    body.removeValue(forKey: field)
    guard let bytes = try? ReleaseCanonicalJSON.encode(body),
          let signature = try? key.signature(for: bytes) else { return "" }
    return signature.base64EncodedString()
}

/// One descriptor matching the controller's contract, signed by throwaway keys generated in the
/// test. `controllerKeyId` may name a key that did not sign, which is how an impostor is modelled.
private func makeReleaseDescriptor(version: String = "0.6.50",
                                   build: String,
                                   artifact: Data,
                                   gateway: Curve25519.Signing.PrivateKey,
                                   controllerSigning: Curve25519.Signing.PrivateKey,
                                   controllerKeyId: String? = nil,
                                   filename: String = "StudioHub-0.6.50-abc1234.dmg",
                                   sha256: String? = nil,
                                   bytes: Int? = nil) -> [String: Any] {
    var descriptor: [String: Any] = [
        "schema": "studiohub.app-release.v1",
        "schema_version": 1,
        "version": version,
        "build": build,
        "filename": filename,
        "bytes": bytes ?? artifact.count,
        "sha256": sha256 ?? releaseSha256(artifact),
        "published_at": 1790000000,
        "publisher_id": "gatewaykh",
        "publisher_key_id": releaseSha256(gateway.publicKey.rawRepresentation),
    ]
    descriptor["signature"] = releaseSign(descriptor, removing: "signature", key: gateway)
    descriptor["controller_key_id"] = controllerKeyId
        ?? releaseSha256(controllerSigning.publicKey.rawRepresentation)
    descriptor["controller_signature"] = releaseSign(descriptor, removing: "controller_signature",
                                                     key: controllerSigning)
    return descriptor
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
        // What the app offers is every model the Hub publishes a form for, once each. Derived from
        // the fixture's own payloads instead of a count: a number restates the answer and has to be
        // edited by hand for every new model, which is exactly how the voice allowlist stayed green
        // while six of thirteen packages were invisible.
        let fixtureCatalogRepos = Set(FixtureProtocol.catalogModels.compactMap { entry -> String? in
            guard let repo = entry["repo"] as? String else { return nil }
            let capabilities = Set(entry["capabilities"] as? [String] ?? [])
            let family = entry["family"] as? String ?? ""
            let isChat = !capabilities.isDisjoint(with: ["chat.completion", "chat", "conversational",
                                                         "text-generation"])
                || family.lowercased().hasPrefix("chat")
                || (entry["hub_modality"] as? String)?.lowercased() == "chat"
            let hasForm = capabilities.contains("tts") || capabilities.contains("txt2img")
                || capabilities.contains("music") || isChat
            return hasForm ? repo : nil
        })
        let fixtureTranscriptionRepos = Set(FixtureProtocol.transcriptionModels.compactMap { $0["repo"] as? String })
        require(store.models.count == Set(store.models.map(\.repo)).count,
                "a model must appear once per repo, got: \(store.models.map(\.repo).sorted())")
        require(Set(store.models.map(\.repo)) == fixtureCatalogRepos.union(fixtureTranscriptionRepos),
                "every model the Hub publishes a form for must be listed, and nothing else: got \(Set(store.models.map(\.repo)).symmetricDifference(fixtureCatalogRepos.union(fixtureTranscriptionRepos)).sorted())")

        // One row per model. The aggregate catalogue lists the same repo once per studio that
        // carries it, at different levels of detail; a picker that repeats a model three times is
        // wrong, and letting a bare row win would also lose the engine and cache state the Subtitle
        // form needs. A bare row sits both before and after the rich one, so neither "first seen"
        // nor "last seen" can pass.
        let turboRows = store.models.filter { $0.function == .transcription && $0.repo == "mlx-community/whisper-large-v3-turbo" }
        require(turboRows.count == 1, "one transcription row per model, got \(turboRows.count)")
        require(turboRows.first?.label == "Whisper turbo" && turboRows.first?.cached == true
                && turboRows.first?.supportsWordTimestamps == true && turboRows.first?.supported == true,
                "the richest entry must win the dedupe, got: \(String(describing: turboRows.first))")
        require(store.models.filter { $0.function == .transcription }.count == fixtureTranscriptionRepos.count,
                "deduplicating must not drop a model that is genuinely listed once")
        let chatterboxRows = store.models.filter { $0.function == .audio && $0.repo == "mlx-community/chatterbox-8bit" }
        require(chatterboxRows.count == 1, "one audio row per model, got \(chatterboxRows.count)")
        require(chatterboxRows.first?.family == "chatterbox-mlx"
                && chatterboxRows.first?.capabilities.contains("voice-cloning") == true,
                "the bare duplicate must not win over the rich catalogue entry")
        // The preset roster is carried by the model that publishes it, and the chosen speaker is
        // still seeded from it. It used to live in a global list the Audio page read directly, which
        // is how a Khmer model would have been offered Qwen's voice names.
        // The roster is carried by the model that publishes it, so the speaker follows the model:
        // selecting the Qwen preset model must seed its own first voice.
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
        require(store.audioSpeaker == "Ryan",
                "the selected model's own published roster must seed the speaker default, got: \(store.audioSpeaker)")
        for mode in VoiceMode.allCases {
            require(!mode.emptyReason.contains("Qwen") && !mode.emptyReason.contains("VoxCPM2"),
                    "the \(mode.rawValue) tab's empty copy must not hardcode a family, got: \(mode.emptyReason)")
        }

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
        // A role change must produce something the operator can read. It now lands in the
        // Connections banner rather than the small note beside the picker, because the owner
        // missed the small note entirely.
        require(store.connectionNotice?.kind == .success
                && store.connectionNotice?.text.contains("Controller") == true,
                "a role change needs a visible result")

        await store.setRole("nonsense")
        require(store.error?.contains("role must be one of") == true,
                "a refused role must carry the Hub's reason, got: \(store.error ?? "nil")")
        await store.setRole("standalone")
        require(store.controllerIdentity?.settings.role == "standalone", "the role must be settable back")
        store.error = nil

        // A model this Mac cannot run has to say which gate is shut. The Hub sends five of them -
        // files, runtime, the macOS floor, the chip, the studio process - and the app decoded four
        // fields of the package row and dropped the rest, so every refusal read "unavailable".
        await store.refresh()
        let gated = store.models.first { $0.repo == "mlx-community/VoxCPM2-4bit" }
        require(gated?.blockReason?.contains("macOS 14.5") == true
                && gated?.blockReason?.contains("15.0") == true,
                "an unsupported macOS must name both versions, got: \(gated?.blockReason ?? "nil")")
        require(gated?.pickerSuffix.contains("not available") == true,
                "an unusable model must be marked in the picker")
        // The picker shows a cleaned short name; the full catalogue label stays everywhere else.
        require(NativeModel.pickerName(for: "VoxCPM2 4-bit (MLX) — recommended") == "VoxCPM2 4-bit",
                "a recommendation suffix and runtime marker must be dropped in the picker")
        require(NativeModel.pickerName(for: "VoxCPM2 bf16 (MLX) — full precision") == "VoxCPM2 bf16",
                "the full-precision suffix must be dropped in the picker")
        require(NativeModel.pickerName(for: "Qwen3-TTS 0.6B Base") == "Qwen3-TTS 0.6B Base",
                "a label with nothing to strip must pass through unchanged")
        let healthy = store.models.first { $0.repo == "mlx-community/whisper-large-v3-turbo" }
        require(healthy?.blockReason == nil, "a model that can run must carry no reason")

        // A model the Hub has paused after repeated failures must say so, and must offer the one
        // action that releases the pause. Until this existed, a paused model was refused before
        // dispatch, nothing in the app changed, and the only way back was editing the Hub's state
        // file by hand - which is how one wrong field name read as "this model does not work".
        let paused = store.models.first { $0.repo == "fixture/paused" }
        require(paused?.paused == true, "a paused model must report the pause")
        require(paused?.blockReason?.contains("Paused by the Hub") == true
                && paused?.blockReason?.contains("SystemExit") == true,
                "a pause must name the Hub's own reason, got: \(paused?.blockReason ?? "nil")")
        await store.recheckModel(paused!)
        require(FixtureProtocol.recheckBody()?["model"] as? String == "fixture/paused",
                "the recheck must name the model, got: \(String(describing: FixtureProtocol.recheckBody()))")
        require(FixtureProtocol.recheckBody()?["studio"] as? String == "voice",
                "the recheck must name the paused Studio, or the Hub cannot tell which pause to lift")
        require(store.modelNote?.contains("Rechecked") == true,
                "a recheck must report its result, got: \(store.modelNote ?? "nil")")

        // A damaged file of the app's own must not stop it connecting. This used to throw out of
        // `connect()`, which cleared the Hub address and left every page on the setup screen, with
        // no way back except deleting the file in Finder.
        let corruptState = state.appendingPathComponent("native-workspaces.json")
        try Data("{ this is not json".utf8).write(to: corruptState)
        // The fixture's session, like every other store in this test: a bare store would talk to
        // the real network and fail for a reason that has nothing to do with the damaged file.
        let recoveredStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await recoveredStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                     dataDirectory: state, token: "native-test-token")
        require(recoveredStore.isReachable, "a damaged workspace file must not stop the app connecting")
        require(recoveredStore.startupNote?.contains("set aside") == true,
                "and it must say so, got: \(recoveredStore.startupNote ?? "nil")")
        require(FileManager.default.fileExists(atPath: corruptState.path + ".corrupt"),
                "the unreadable file must be kept, not deleted")

        // One batch whose detail will not load is one row, not the whole page. This used to abort
        // the refresh and mark the Hub unreachable, so a pruned job blanked the models and the jobs.
        // A fresh store with its own data directory: a finished job's detail is fetched once and
        // remembered now, so the failure has to land on a first read - and a second store sharing
        // this directory would clear the pending records the identity assertions below are about.
        FixtureProtocol.setFailJobDetail(true)
        let degradedHome = FileManager.default.temporaryDirectory
            .appendingPathComponent("studiohub-degraded-\(UUID().uuidString)")
        let degraded = NativeWorkspaceStore(sessionConfiguration: configuration)
        await degraded.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                               dataDirectory: degradedHome, token: "native-test-token")
        FixtureProtocol.setFailJobDetail(false)
        require(degraded.isReachable, "an unreadable batch must not mark the Hub unreachable")
        require(degraded.startupNote?.contains("could not be read") == true,
                "the unreadable batch must be reported, got: \(degraded.startupNote ?? "nil")")
        require(!degraded.imageJobs.isEmpty || !degraded.audioJobs.isEmpty,
                "the jobs that could be read must still be shown")

        // The storage families, with the worker's own memory verdict. This is what makes a model
        // copied in from another install visible as "present but not runnable here" rather than
        // looking like any other download.
        require(store.storageFamilies.count == 2,
                "storage families must decode: \(store.storageFamilies.count)")
        let flagged = store.storageFamilies.filter { $0.runsHere == false }
        require(flagged.count == 1, "exactly one fixture family exceeds this Mac")
        require(flagged.first?.requiredGB == 32 && flagged.first?.thisMacGB == 16,
                "the flag must carry both numbers")

        // And a finished job is not asked about twice: the poll is every two seconds, for ever.
        FixtureProtocol.resetJobDetailCount()
        await degraded.refresh()
        let firstPass = FixtureProtocol.jobDetailCount()
        await degraded.refresh()
        let secondPass = FixtureProtocol.jobDetailCount()
        require(secondPass == firstPass,
                "a finished job must be fetched once, not on every poll: \(firstPass) then \(secondPass)")

        // How long a generation took, shown in the generator and in Jobs. The Hub writes
        // `runtime_s` when an item finishes and `elapsed_s` while it runs; the app showed neither,
        // so a 4.6-second clip that took 52 seconds to make read as "4.6 s" - which the owner
        // reported as the app never saying how long a generation takes.
        require(NativeJob.generationSuffix(generationSeconds: 52.2, runningSeconds: nil) == " · took 52.2 s",
                "a finished job must state how long it took")
        require(NativeJob.generationSuffix(generationSeconds: nil, runningSeconds: 7.5) == " · running 7.5 s",
                "a running job must state how long it has been running")
        require(NativeJob.generationSuffix(generationSeconds: nil, runningSeconds: nil).isEmpty,
                "an untimed job must not invent a duration")
        require(NativeJob.generationSuffix(generationSeconds: 125, runningSeconds: nil) == " · took 2 min 5 s",
                "a long generation must read in minutes, not 125 seconds")
        require(NativeJob.generationSuffix(generationSeconds: 0, runningSeconds: nil).isEmpty,
                "a zero must read as unknown rather than as an instant")

        // Joining has to say what this Mac runs, or the controller registers a node that
        // nothing can be dispatched to.
        // A Hub bound to loopback only must refuse to offer an address: a join that succeeds
        // with an address nothing can reach is worse than a join that says why it cannot.
        await store.joinController(address: "192.168.1.21:8080", token: "fleet-token-value",
                                   boundHosts: ["127.0.0.1"])
        require(!FixtureProtocol.fleetRequests().contains("join"),
                "a loopback-only Hub must not join with an unreachable address")
        require(store.error?.contains("not reachable at any address") == true,
                "a refused join must explain why, got: \(store.error ?? "nil")")
        store.error = nil

        await store.joinController(address: "192.168.1.21:8080", token: "fleet-token-value",
                                   boundHosts: LocalAddresses.current().map(\.address))
        require(FixtureProtocol.fleetRequests().contains("join"),
                "Join must reach the controller: \(FixtureProtocol.fleetRequests())")
        let payload = FixtureProtocol.joinPayload()
        require((payload?["node_id"] as? String)?.isEmpty == false,
                "the join must use the Hub's key casing, got: \(String(describing: payload))")
        require((payload?["address"] as? String)?.hasPrefix("http://") == true,
                "the node must offer an address, got: \(payload?["address"] ?? "nil")")
        let studios = payload?["studios"] as? [[String: Any]]
        require(studios?.count == 3, "the node must report every studio it runs")
        require(studios?.compactMap { $0["modality"] as? String }.sorted()
                    == ["image", "transcription", "voice"],
                "the studios must carry the modalities the fleet routes by")
        require(store.fleetNote?.contains("Joined") == true, "a join needs a visible result")
        // Registering is half of joining. A controller drives its nodes with its own credential,
        // and a node that has not taken that credential on answers 401 to the controller it just
        // registered with - so the first job dispatched to it fails. The app called the register
        // route and never the adopt route, which nothing caught because no fixture asserted it.
        require(FixtureProtocol.fleetRequests().contains("adopt"),
                "joining must also adopt the controller's credential: \(FixtureProtocol.fleetRequests())")
        require(FixtureProtocol.adoptPayload()?["token"] as? String == "fleet-token-value",
                "adoption must hand over the controller's credential, got: \(String(describing: FixtureProtocol.adoptPayload()))")
        // And which controller it was taken from. The app remembers the membership in its own
        // preferences, so without this the Hub holds a borrowed credential and cannot say whose -
        // which is the whole reason a node could not show the pool it belongs to.
        require(FixtureProtocol.adoptPayload()?["controller_url"] as? String == "http://192.168.1.21:8080",
                "adoption must record which controller this Mac joined, got: \(String(describing: FixtureProtocol.adoptPayload()))")

        // Joining has to be remembered, or the Mac cannot re-join by itself after a restart. The
        // app owns the preferences file, so the workspace reports the address and the app saves it.
        var remembered: [String?] = []
        store.onControllerJoined = { remembered.append($0) }
        await store.joinController(address: "192.168.1.21:8080", token: "fleet-token-value",
                                   boundHosts: LocalAddresses.current().map(\.address))
        require(remembered.last == "192.168.1.21:8080",
                "a successful join must be remembered, got: \(String(describing: remembered))")

        // And a restart must re-join without anyone retyping anything: the controller may have a
        // new address and the node certainly has one, so the stored call is the repair.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        // Exactly what a launch calls, with no `refreshConnection()` first: the identity fetch is
        // part of the startup step, or the re-join guard sees nil and this never happens.
        await store.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address)))
        require(FixtureProtocol.fleetRequests().contains("join"),
                "a node must re-join its controller on start: \(FixtureProtocol.fleetRequests())")
        require(store.fleetNote?.contains("Joined") == true,
                "a re-join needs a visible result, got: \(store.fleetNote ?? "nil")")

        // The startup identity/version pair can be briefly unavailable while the Hub finishes
        // coming up. The regular two-second lifecycle refresh must retry the pending membership;
        // otherwise this Mac stays detached until the operator restarts or joins by hand.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.failNextIdentityRead()
        await store.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address)))
        require(!FixtureProtocol.fleetRequests().contains("join"),
                "a failed identity read must not guess that this Mac is a node")
        await store.refresh()
        let recoveredJoin = await eventually {
            FixtureProtocol.fleetRequests().filter { $0 == "join" }.count == 1
        }
        require(recoveredJoin,
                "the lifecycle refresh must recover one pending re-join: \(FixtureProtocol.fleetRequests())")
        await store.refresh()
        require(FixtureProtocol.fleetRequests().filter { $0 == "join" }.count == 1,
                "a successful re-join must be idempotent: \(FixtureProtocol.fleetRequests())")

        // An offline controller may take the full remote timeout, but it must not freeze the local
        // two-second catalog/jobs refresh while that retry is outstanding.
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.failNextIdentityRead()
        await store.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address)))
        FixtureProtocol.holdJoin()
        let retryingRefresh = Task { @MainActor in await store.refresh() }
        for _ in 0..<100 where !FixtureProtocol.joinHasStarted() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        require(FixtureProtocol.joinHasStarted(), "the pending controller retry did not start")
        let catalogsBefore = FixtureProtocol.catalogCount()
        await store.refresh()
        require(FixtureProtocol.catalogCount() > catalogsBefore,
                "a held controller retry stalled the local lifecycle refresh")
        FixtureProtocol.releaseHeldJoin()
        await retryingRefresh.value

        // An explicit manual join supersedes a pending automatic one, including its saved token.
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.failNextIdentityRead()
        await store.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.22:8080", token: "stale-fleet-token",
            boundHosts: LocalAddresses.current().map(\.address)))
        await store.joinController(address: "192.168.1.21:8080", token: "fleet-token-value",
                                   boundHosts: LocalAddresses.current().map(\.address))
        let joinsAfterManualChoice = FixtureProtocol.fleetRequests().filter { $0 == "join" }.count
        await store.refresh()
        try? await Task.sleep(for: .milliseconds(100))
        require(FixtureProtocol.fleetRequests().filter { $0 == "join" }.count == joinsAfterManualChoice,
                "the stale automatic membership retried after a manual join: \(FixtureProtocol.fleetRequests())")

        // Disconnect/token replacement invalidates the old startup attempt. A fresh connection
        // cannot send the credential remembered by the connection that ended.
        let replacedConnection = NativeWorkspaceStore(sessionConfiguration: configuration)
        await replacedConnection.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                         dataDirectory: temporary.appendingPathComponent("replaced-connection"),
                                         token: "native-test-token")
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.failNextIdentityRead()
        await replacedConnection.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.22:8080", token: "stale-fleet-token",
            boundHosts: LocalAddresses.current().map(\.address)))
        replacedConnection.disconnect()
        await replacedConnection.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                         dataDirectory: temporary.appendingPathComponent("replaced-connection"),
                                         token: "replacement-local-token")
        await replacedConnection.refresh()
        try? await Task.sleep(for: .milliseconds(100))
        require(FixtureProtocol.fleetRequests().isEmpty,
                "a disconnected startup attempt used its stale token: \(FixtureProtocol.fleetRequests())")
        replacedConnection.disconnect()

        // Leaving is also final for a pending startup attempt: after the Hub mints its own token,
        // a later local refresh must not re-enrol this Mac with the former controller.
        let leavingStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await leavingStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                   dataDirectory: temporary.appendingPathComponent("pending-leave"),
                                   token: "native-test-token")
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.failNextIdentityRead()
        await leavingStore.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.22:8080", token: "stale-fleet-token",
            boundHosts: LocalAddresses.current().map(\.address)))
        await leavingStore.leaveGroup()
        await leavingStore.refresh()
        try? await Task.sleep(for: .milliseconds(100))
        require(!FixtureProtocol.fleetRequests().contains("join"),
                "a pending startup attempt rejoined after leave: \(FixtureProtocol.fleetRequests())")
        leavingStore.disconnect()

        // A Mac running its own group has no controller to re-join, and must not call one.
        FixtureProtocol.setCredentialOrigin("minted")
        FixtureProtocol.resetFleetRequests()
        await store.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address)))
        require(FixtureProtocol.fleetRequests().isEmpty,
                "a Mac with its own credential must not re-join anyone: \(FixtureProtocol.fleetRequests())")

        // Leaving a group is the action that takes the group's credential off a Mac that is being
        // retired or sold. It has to reach the Hub (which mints a fresh credential there) and it
        // has to forget the controller, or the app would keep re-joining a group it left.
        FixtureProtocol.setCredentialOrigin("adopted")
        await store.refreshConnection()
        FixtureProtocol.resetFleetRequests()
        store.storedMembership = NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address))
        await store.leaveGroup()
        require(FixtureProtocol.fleetRequests().contains("leave"),
                "leaving must reach the Hub so it can mint its own credential: \(FixtureProtocol.fleetRequests())")
        require(FixtureProtocol.fleetRequests().contains("dropped-from-controller"),
                "leaving must ask the controller to drop this Mac while the old token still works: \(FixtureProtocol.fleetRequests())")
        require(store.storedMembership == nil, "a Mac that left must not keep re-joining its old group")
        require(store.controllerIdentity?.settings.fleetCredentialOrigin == "minted",
                "the screen must show the Hub's answer, not the previous membership")
        require(store.fleetNote?.contains("left the group") == true,
                "leaving needs a visible result, got: \(store.fleetNote ?? "nil")")

        // Promotion to controller is the end of being somebody's node.
        remembered = []
        store.onControllerJoined = { remembered.append($0) }
        await store.setRole("controller")
        require(remembered == [nil],
                "becoming a controller must forget the controller it used to follow, got: \(String(describing: remembered))")
        await store.setRole("standalone")
        FixtureProtocol.setCredentialOrigin("minted")

        // Which job a voice model does. The Audio page is arranged by this, and getting it wrong
        // put a CustomVoice model on the cloning path, where it was asked for a reference voice it
        // does not have. The rule is the catalog's own data, so a family this build has never seen
        // still lands in the right tab.
        require(NativeWorkspaceStore.voiceMode(repo: "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit", family: "qwen3-tts",
                         declaredClone: true) == .cloning,
                "a Base tier clones")
        require(NativeWorkspaceStore.voiceMode(repo: "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", family: "qwen3-tts",
                         declaredClone: false) == .preset,
                "CustomVoice takes preset speakers")
        require(NativeWorkspaceStore.voiceMode(repo: "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit", family: "qwen3-tts",
                         declaredClone: false) == .design,
                "VoiceDesign takes a description")
        require(NativeWorkspaceStore.voiceMode(repo: "mlx-community/VoxCPM2-4bit", family: "voxcpm-mlx",
                         declaredClone: true) == .cloning,
                "VoxCPM2 clones")
        require(NativeWorkspaceStore.voiceMode(repo: "mlx-community/OmniVoice-bfloat16", family: "omnivoice",
                         declaredClone: true) == .cloning,
                "OmniVoice clones")
        // A name that says nothing either way: the declared capability decides, whatever the family.
        require(NativeWorkspaceStore.voiceMode(repo: "someone/mystery-tts", family: "other",
                         declaredClone: true) == .cloning,
                "a declared clone capability must be believed")
        require(NativeWorkspaceStore.voiceMode(repo: "someone/mystery-tts", family: "other",
                         declaredClone: false) == .preset,
                "a model that declares no cloning is the preset form, not a forced clone")
        // The Qwen tier name is scoped to Qwen: a name that merely contains "base" must not decide
        // another family's contract.
        require(NativeWorkspaceStore.voiceMode(repo: "someone/mystery-base-tts", family: "other",
                         declaredClone: false) == .preset,
                "a non-Qwen name must never force the clone form")
        // A family added to the catalog after this build. It must land correctly without the app
        // knowing its name.
        require(NativeWorkspaceStore.voiceMode(repo: "someone/pocket-tts-4bit", family: "pocket-tts",
                         declaredClone: true) == .cloning,
                "an unknown clone-capable family must land in cloning")
        require(NativeWorkspaceStore.voiceMode(repo: "someone/pocket-tts-4bit", family: "pocket-tts",
                         declaredClone: false) == .preset,
                "an unknown preset family must land in preset")
        require(NativeWorkspaceStore.voiceMode(repo: "someone/mystery-qwen", family: "qwen3-tts",
                         declaredClone: false) == .preset,
                "an unnamed Qwen tier is the preset form, the only mode needing nothing but a name")

        // Which container a fetched result is in, read from its own bytes. Playback needs the
        // right filename extension: `AVAudioPlayer(data:)` fails an MP3 with OSStatus -4, which is
        // what stopped seven of the ten saved voices from playing at all.
        let wavHeader = Data([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x41, 0x56, 0x45])
        require(NativeWorkspaceStore.audioExtension(of: wavHeader) == ".wav", "a RIFF/WAVE header")
        require(NativeWorkspaceStore.audioExtension(of: Data([0x49, 0x44, 0x33, 0x04])) == ".mp3",
                "an ID3-tagged MP3")
        require(NativeWorkspaceStore.audioExtension(of: Data([0xFF, 0xFB, 0x90, 0x00])) == ".mp3",
                "a bare MPEG frame header")
        require(NativeWorkspaceStore.audioExtension(
            of: Data([0, 0, 0, 0x20, 0x66, 0x74, 0x79, 0x70, 0x4D, 0x34, 0x41, 0x20])) == ".m4a",
                "an ISO media file")
        require(NativeWorkspaceStore.audioExtension(of: Data("OggS".utf8)) == ".ogg", "an Ogg stream")
        require(NativeWorkspaceStore.audioExtension(of: Data()) == ".wav",
                "an unreadable payload must not claim a format it cannot prove")

        // Reference timings shipped beside a clip. Every long voice in the kit carries a
        // `transcript.timed.json`, and the engine needs those timings to window a reference longer
        // than its 15-second maximum. Reading them is what lets a long voice be prepared without
        // transcribing it again on each Mac.
        let timedVoice = temporary.appendingPathComponent("timed-voice")
        try FileManager.default.createDirectory(at: timedVoice, withIntermediateDirectories: true)
        let timedClip = timedVoice.appendingPathComponent("reference.wav")
        try FixtureProtocol.wav.write(to: timedClip)
        let shipped = """
        {"transcript":"Hello there","segments":[{"start_seconds":0.0,"end_seconds":8.5,"text":"Hello there"},{"start_seconds":9.0,"end_seconds":12.0,"text":"General Kenobi"}]}
        """
        try shipped.write(to: timedVoice.appendingPathComponent("transcript.timed.json"),
                          atomically: true, encoding: .utf8)
        let timings = NativeWorkspaceStore.shippedTimings(forReference: timedClip)
        require(timings.count == 2, "shipped timings must be read: \(timings.count)")
        require(timings[0]["start_seconds"] as? Double == 0.0, "a shipped start must survive")
        require(timings[1]["text"] as? String == "General Kenobi", "a shipped segment keeps its words")
        require(NativeWorkspaceStore.segments(timings, match: "Hello there General Kenobi"),
                "shipped timings must match the transcript they belong to")
        require(!NativeWorkspaceStore.segments(timings, match: "Completely different words"),
                "timings for other words must never be sent")
        // A voice that shipped no timings contributes nothing rather than guessing. The file is
        // looked for in the clip's own folder, so a second folder is what proves the absence.
        let untimedVoice = temporary.appendingPathComponent("untimed-voice")
        try FileManager.default.createDirectory(at: untimedVoice, withIntermediateDirectories: true)
        let untimedClip = untimedVoice.appendingPathComponent("reference.wav")
        try FixtureProtocol.wav.write(to: untimedClip)
        require(NativeWorkspaceStore.shippedTimings(forReference: untimedClip).isEmpty,
                "a clip with no shipped timings must yield none")

        // The fleet: a controller lists who joined, with the state each node is actually in.
        await store.refreshFleetNodes()
        require(FixtureProtocol.fleetRequests().contains("list"), "the joined nodes must be read from the Hub")
        require(store.fleetNodes?.summary.total == 2, "every joined node must be listed")
        require(store.fleetNodes?.nodes.first?.state == "reachable", "a reachable node must say so")
        require(store.fleetNodes?.nodes.last?.isReachable == false,
                "a node that was joined but has since gone must not read as available")
        // The fleet overview: one row per Mac, with the facts that decide where work goes.
        require(store.fleetOverview?.summary.total == 2, "the overview must cover every machine")
        require(store.fleetOverview?.machines.first?.machine == "local", "this Mac is one of them")
        require(store.fleetOverview?.machines.first?.diskFreeGb == 220.0, "free disk must be shown")
        require(store.fleetOverview?.machines.first?.averageSeconds == 41.5,
                "measured speed must be shown, not estimated")
        require(store.fleetOverview?.machines.last?.averageSeconds == nil,
                "a machine with no measured work must report no average, not zero")
        require(FixtureProtocol.fleetRequests().contains("overview"),
                "the overview must be read from the Hub: \(FixtureProtocol.fleetRequests())")
        // A Mac that joined a group has to be able to see the group. The pool is read back from
        // the controller, so the screen can name the head of the group and say plainly whether
        // what it is showing is all of it.
        require(store.fleetOverview?.belongsToGroup == true,
                "a Mac set as a Node belongs to a group")
        require(store.fleetOverview?.controller?.label == "Control Mac",
                "the node must know which controller it joined: \(String(describing: store.fleetOverview?.controller))")
        require(store.fleetOverview?.controller?.isReachable == true,
                "an answering controller must read as answering")
        require(store.fleetOverview?.controller?.address == "http://192.168.1.21:47873",
                "the controller's address is what makes the group reachable, so it must decode")
        require(store.fleetOverview?.rosterReadable == true,
                "a roster that was read must not read as unreadable")
        require(store.fleetOverview?.machines.last?.role == "agent",
                "a joined node must say what part it plays in the group")
        require(store.fleetOverview?.machines.last?.address == "http://192.168.1.20:47873",
                "a joined node's address must reach the row")

        await store.leaveNode(store.fleetNodes!.nodes.last!)
        require(FixtureProtocol.fleetRequests().contains("leave:studio-3"),
                "removing a node must reach the Hub: \(FixtureProtocol.fleetRequests())")

        // The address typed for a controller is checked before any credential is sent to it.
        for good in ["192.168.1.21:8080", "http://192.168.1.21:8080", "100.101.102.103:8080",
                     "127.0.0.1:9", "10.0.0.4:9000", "172.16.5.5:8080", "169.254.1.1:8080",
                     // The port is optional: the Hub's port is fixed, so the address on its own
                     // is a complete answer and the operator should not have to know the number.
                     "192.168.1.21", "100.101.102.103", "http://100.101.102.103"] {
            _ = try NativeWorkspaceStore.privateHTTPAddress(good)
        }
        let withoutPort = try NativeWorkspaceStore.privateHTTPAddress("192.168.1.21").port
        require(withoutPort == 47873,
                "a missing port must fall back to the Hub's fixed port")
        for bad in ["93.184.216.34:80", "http://93.184.216.34:80",
                    "https://192.168.1.21:8080", "192.168.1.21:8080/jobs",
                    "192.168.1.21:8080?x=1", "user:pass@192.168.1.21:8080", "8.8.8.8:53",
                    // A name is allowed only when it resolves to a private address, so a name
                    // that cannot be looked up is refused rather than attempted.
                    "no-such-controller.invalid", ""] {
            do {
                _ = try NativeWorkspaceStore.privateHTTPAddress(bad)
                fatalError("a controller address that cannot be trusted was accepted: \(bad)")
            } catch {}
        }

        // The voice library: the app is a window onto the worker's store, so what it decodes
        // has to carry the transcript - a clone without one cannot align audio to words.
        await store.refreshVoices()
        require(FixtureProtocol.voiceListCalls >= 1, "the voice library must be read from the worker")
        require(store.voices.count == 2, "every saved voice must be listed")
        let narrator = store.voices.first { $0.id == "voice-1" }!
        require(narrator.transcript == "The exact words in the clip", "the transcript must reach the app")
        require(narrator.readyForCloning, "a voice with a transcript is ready to clone from")
        require(narrator.durationLabel == "12.5 s", "the clip length must be shown: \(narrator.durationLabel)")
        require(narrator.license == "self-owned", "the stored licence must be shown")
        let unfinished = store.voices.first { $0.id == "voice-2" }!
        require(!unfinished.readyForCloning, "a voice with no transcript is not ready to clone from")
        require(store.voices.first?.name == "Narrator", "voices must be listed by name")

        // Playing a saved voice fetches the clip through the Hub's studio proxy. The worker
        // reports `/api/voices/<id>/audio`; asking the Hub for that path directly returns 404,
        // which is the mistake this pins.
        let clip = try await store.voiceData(for: narrator)
        require(clip == FixtureProtocol.wav, "the saved clip must come back as bytes")
        require(FixtureProtocol.voiceAudioCalls == 1, "exactly one clip fetch")

        // Using a saved voice fills the reference fields, including the transcript that makes
        // the clone possible.
        let usedSaved = await store.useVoice(narrator)
        require(usedSaved, "a voice with a transcript must be usable")
        require(store.audioReferenceTranscript == "The exact words in the clip",
                "using a voice must carry its transcript")
        // The clip is fetched into the app's own folder, because staging reads from disk: a
        // saved voice lives in the worker's store, not here.
        require(store.audioReferencePath?.contains("voice-voice-1.wav") == true,
                "using a voice must stage its clip locally: \(store.audioReferencePath ?? "nil")")
        require(FileManager.default.fileExists(atPath: store.audioReferencePath ?? ""),
                "the staged reference must exist on disk")
        let usedUnfinished = await store.useVoice(unfinished)
        require(!usedUnfinished,
                "a voice with no transcript must not be offered for cloning")
        require(store.voiceError?.contains("no transcript") == true,
                "a voice that cannot clone must say why: \(store.voiceError ?? "nil")")

        // Transcribing a clip on the spot: the words come back so neither the voice editor nor
        // the audio reference field has to ask the operator to type them.
        let transcribable = temporary.appendingPathComponent("spoken.wav")
        try FixtureProtocol.wav.write(to: transcribable)
        let words = try await store.transcribeClip(at: transcribable, language: "en")
        require(words == "The exact words in the clip", "the transcript must come back: \(words)")
        require(FixtureProtocol.syncTranscribeCalls == 1, "exactly one synchronous transcription")
        do {
            _ = try await store.transcribeClip(at: temporary.appendingPathComponent("missing.wav"))
            fatalError("transcribing a clip that is not there must be refused")
        } catch {
            require(error.localizedDescription.contains("no longer on disk"),
                    "a missing clip needs an actionable message: \(error.localizedDescription)")
        }

        // Removing a saved voice reaches the worker.
        _ = await store.deleteVoice(unfinished)
        require(FixtureProtocol.voiceDeleteCalls == 1, "removing a voice must reach the worker")

        // Importing voices that already exist: the shape the old Voice Studio used, one folder
        // per voice with a clip and its transcript. Scanning is tested on its own because it is
        // the part that decides what an import will do, and because getting it wrong is how an
        // operator ends up re-importing everything or silently skipping voices.
        // Cleared first: this folder is named, not unique per run, so a previous run's voices
        // would otherwise be "already saved" and the import would look like it did nothing.
        let importRoot = temporary.appendingPathComponent("existing-voices")
        try? FileManager.default.removeItem(at: importRoot)
        try FileManager.default.createDirectory(at: importRoot, withIntermediateDirectories: true)
        func makeVoice(_ folder: String, clip: Data?, transcript: String?) throws {
            let directory = importRoot.appendingPathComponent(folder)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            if let clip { try clip.write(to: directory.appendingPathComponent("reference.wav")) }
            if let transcript { try transcript.write(to: directory.appendingPathComponent("transcript.txt"),
                                                     atomically: true, encoding: .utf8) }
        }
        try makeVoice("Imported Narrator", clip: FixtureProtocol.wav, transcript: "The exact words")
        try makeVoice("Imported NoWords", clip: FixtureProtocol.wav, transcript: nil)
        // Already saved in this library under that name: importing it again must not duplicate.
        try makeVoice("Narrator", clip: FixtureProtocol.wav, transcript: "already here")
        // A subtitle file is a transcript with timing in it, which must not reach the clone.
        try makeVoice("Imported Subtitled", clip: FixtureProtocol.wav,
                      transcript: "1\n00:00:00,000 --> 00:00:02,000\nHello there\n\n2\n00:00:02,000 --> 00:00:04,000\nGeneral Kenobi\n")
        try makeVoice("Empty", clip: nil, transcript: "no clip here")

        let scanned = try NativeWorkspaceStore.scanVoiceFolder(importRoot)
        require(scanned.count == 4, "a folder with no clip is not a voice: \(scanned.map(\.name))")
        require(scanned.map(\.name) == ["Imported Narrator", "Imported NoWords", "Imported Subtitled", "Narrator"],
                "the scan must be sorted and stable: \(scanned.map(\.name))")
        let narratorScan = scanned.first { $0.name == "Imported Narrator" }!
        require(narratorScan.isReady, "a clip with a transcript is ready to import")
        require(narratorScan.transcript == "The exact words", "the transcript must be read whole")
        require(!scanned.first { $0.name == "Imported NoWords" }!.isReady,
                "a clip with no words is not ready - a clone would sound wrong")
        let subtitled = scanned.first { $0.name == "Imported Subtitled" }!
        require(subtitled.transcript == "Hello there General Kenobi",
                "subtitle timing must be stripped: \(subtitled.transcript)")

        // Importing brings in the ready ones and reports the rest, without duplicating what is
        // already saved.
        let imported = await store.importVoices(from: importRoot)
        require(imported.total == 4, "the import must consider every clip: \(imported.total)")
        require(imported.added == 2, "only new voices with words are added: \(imported)")
        require(imported.skipped == 2,
                "the voiceless clip and the one already saved are skipped: \(imported)")
        require(FixtureProtocol.voiceListCalls >= 2, "adding a voice must refresh the library")
        let repeated = await store.importVoices(from: importRoot)
        require(repeated.added == 0, "importing twice must not duplicate a library: \(repeated)")

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
        // Derived, not listed: every installed model the Hub publishes a package for, exactly once.
        let installedModels = store.models.filter { $0.cached }
        let verifiable = installedModels.compactMap { model -> String? in
            guard let package = FixtureProtocol.packageIDsByRepo[model.repo] else { return nil }
            return "verify:\(package)"
        }
        require(Set(verifyAll) == Set(verifiable),
                "Verify All must check every installed model with a Hub package: \(verifyAll.sorted())")
        require(verifyAll.count == Set(verifiable).count, "Verify All must not verify a model twice")
        require(store.modelNote?.hasPrefix("Verified \(verifiable.count) of \(installedModels.count).") == true,
                "Verify All must report what it could not verify, got: \(store.modelNote ?? "nil")")
        let unverifiable = installedModels.filter { FixtureProtocol.packageIDsByRepo[$0.repo] == nil }
        for model in unverifiable {
            require(store.modelNote?.contains(model.label) == true,
                    "\(model.label) has no Hub package and must be named as unverified")
        }
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

        // One bad manifest must not blank the app. The Hub builds its package list from every
        // manifest in a single pass, so one malformed manifest refuses the whole route; read inside
        // a hard `try`, that threw the refresh, marked the Hub unreachable and emptied every model
        // picker on every Mac - the chat-mlx failure. The catalog and the pickers must survive with
        // the package information missing, and the operator must be told why every model suddenly
        // reads as unsupported.
        let modelsBeforePackageFailure = store.models.count
        FixtureProtocol.setPackagesUnavailable(true)
        await store.refresh()
        require(store.isReachable, "a failed package list must not mark the Hub unreachable")
        require(store.models.count == modelsBeforePackageFailure,
                "a failed package list must leave the catalog and the model list populated")
        require(store.models.allSatisfy { !$0.supported },
                "with no package rows nothing can be installed or dispatched, and nothing may claim otherwise")
        require(store.startupNote?.contains("package list") == true,
                "a degraded package read needs a visible reason, got: \(store.startupNote ?? "nil")")
        FixtureProtocol.setPackagesUnavailable(false)
        await store.refresh()
        require(store.isReachable && store.models.contains { $0.supported },
                "the model list must recover once the Hub answers again")
        require(store.models.count == modelsBeforePackageFailure,
                "recovering must not duplicate or drop models")

        let supportedAudio = store.models.filter { $0.function == .audio && $0.supported }
        let supportedSTT = store.models.filter { $0.function == .transcription && $0.supported }

        // Every voice model the catalogue publishes as a TTS form and this Hub can dispatch must be
        // selectable, and each must land in the tab its own catalog data implies. This is the
        // invariant, derived from the fixture the app was actually given, rather than a restatement
        // of the answer: the old guard asserted exactly Qwen and VoxCPM2 and stayed green while
        // khmer-tts-mms, khmer-tts-onnx, chatterbox-mlx and fish-audio-mlx were invisible to every
        // user. Any family added to the catalog now fails this test if the app forgets it.
        let voiceCatalog = FixtureProtocol.catalogModels.filter { entry in
            guard (entry["capabilities"] as? [String])?.contains("tts") == true,
                  let repo = entry["repo"] as? String else { return false }
            return FixtureProtocol.packageModelIDs.contains(repo)
        }
        require(Set(supportedAudio.map(\.repo)) == Set(voiceCatalog.compactMap { $0["repo"] as? String }),
                "every dispatched voice model must be selectable, got: \(supportedAudio.map(\.repo).sorted())")
        for entry in voiceCatalog {
            guard let repo = entry["repo"] as? String else { continue }
            let family = entry["family"] as? String ?? ""
            let declaredClone = (entry["reference_audio"] as? [String: Any])
                .flatMap { $0["supported"] as? Bool ?? $0["declared"] as? Bool }
                ?? ((entry["capabilities"] as? [String])?.contains("voice-cloning") ?? false)
            let model = store.models.first { $0.function == .audio && $0.repo == repo }
            let expected: VoiceMode
            if family == "qwen3-tts" {
                let name = (repo.split(separator: "/").last.map(String.init) ?? repo).lowercased()
                expected = name.contains("voicedesign") ? .design
                    : name.contains("customvoice") ? .preset
                    : name.contains("base") ? .cloning
                    : (declaredClone ? .cloning : .preset)
            } else {
                expected = declaredClone ? .cloning : .preset
            }
            require(model?.voiceMode == expected,
                    "\(repo) must land in \(expected.rawValue) — its catalog data says so")
            require(model?.requiresReference == (expected == .cloning),
                    "\(repo) must need a reference clip only when it clones")
        }

        // The four families this fix exists for, named so a reader sees the bug's own coverage.
        // Khmer is preset-only: the catalogue declares tts + multilingual and no voice-cloning, and
        // its generator rejects a reference voice. Chatterbox and Fish clone from a clip.
        let khmer = store.models.first { $0.repo == "khmerttsopensource/khmer-tts" }
        require(khmer?.supported == true && khmer?.voiceMode == .preset && khmer?.requiresReference == false,
                "khmer-tts-mms must be selectable, in the preset tab, needing no reference clip")
        require(khmer?.speakers.isEmpty == true && khmer?.publishesPresetRoster == false,
                "a single-speaker family must not be offered another family's preset roster")
        let qwenPreset = store.models.first { $0.repo == "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit" }
        require(qwenPreset?.speakers == ["Ryan", "Aiden"] && qwenPreset?.publishesPresetRoster == true,
                "the family that publishes a roster must be the only one offered it")
        let khmerOnnx = store.models.first { $0.repo == "sengtha/khmer-tts-female-v2" }
        require(khmerOnnx?.supported == true && khmerOnnx?.voiceMode == .preset,
                "khmer-tts-onnx must be selectable, in the preset tab")
        let chatterbox = store.models.first { $0.repo == "mlx-community/chatterbox-8bit" }
        require(chatterbox?.supported == true && chatterbox?.voiceMode == .cloning
                && chatterbox?.requiresReference == true,
                "chatterbox-mlx must be selectable, in the cloning tab")
        let fish = store.models.first { $0.repo == "mlx-community/fish-audio-s2-pro-8bit" }
        require(fish?.supported == true && fish?.voiceMode == .cloning,
                "fish-audio-mlx must be selectable, in the cloning tab")
        // The family arriving from the parallel job: it supports cloning, and it must land correctly
        // without this build knowing the name "pocket-tts" at all.
        let pocket = store.models.first { $0.repo == "mlx-community/pocket-tts-4bit" }
        require(pocket?.supported == true && pocket?.voiceMode == .cloning && pocket?.requiresReference == true,
                "an unknown clone-capable family must be selectable in the cloning tab")

        // The heavy voice families must be reachable from the Audio form, not filtered out by a
        // family gate: VoxCPM2 and OmniVoice clone from a reference clip and were previously
        // excluded despite working end to end through the Hub.
        let vox = store.models.first { $0.repo == "mlx-community/VoxCPM2-4bit" }
        require(vox?.requiresReference == true, "VoxCPM2 must be exposed as a clone model")
        require(vox?.requiresTranscript == false, "VoxCPM2 does not need a reference transcript")
        require(vox?.speakers.isEmpty == true && vox?.publishesPresetRoster == false,
                "a cloning model publishes no preset roster")
        // Which transcription models are offered is the producer's own answer: the worker lists the
        // model and the Hub publishes a package for it. The guard this replaces asserted the
        // picker equalled one hardcoded repo, and the fixture's only other engine also had no
        // package — so the assertion passed for two independent reasons and could not say which
        // gate did the work. Moonshine now has a package, so only the engine-name gate could
        // exclude it, and a Whisper-spelled model with no package proves the package gate still
        // does real work.
        let packagedSTT = Set(FixtureProtocol.transcriptionModels.compactMap { row -> String? in
            guard let repo = row["repo"] as? String, FixtureProtocol.packageModelIDs.contains(repo) else { return nil }
            return repo
        })
        require(Set(supportedSTT.map(\.repo)) == packagedSTT,
                "every transcription model the Hub publishes a package for must be selectable, got: \(supportedSTT.map(\.repo).sorted())")
        let moonshine = store.models.first { $0.repo == "moonshine-ai/moonshine-base" }
        require(moonshine?.supported == true && moonshine?.cached == true,
                "an engine the worker reports cached and the Hub packages must be offered whatever it is called")
        require(moonshine?.family == "moonshine",
                "a model's engine must be reported as the worker named it, got: \(moonshine?.family ?? "nil")")
        require(moonshine?.supportNote.contains("Whisper") == false,
                "a non-Whisper engine must not be described as a Whisper contract, got: \(moonshine?.supportNote ?? "nil")")
        require(moonshine?.supportNote.contains("moonshine") == true,
                "the note must name the engine the worker published, got: \(moonshine?.supportNote ?? "nil")")
        let unpackagedSTT = store.models.first { $0.repo == "fixture/whisper-unpackaged" }
        require(unpackagedSTT?.supported == false
                && unpackagedSTT?.supportNote.contains("packaged model catalog") == true,
                "an engine name alone must not make a model dispatchable: with no Hub package it is listed with a reason")
        // The runtime's own `recommended` flag decides which model reads as the tested one, not a
        // repo written down in the app.
        let recommendedSTT = store.models.first { $0.repo == "mlx-community/whisper-large-v3-turbo" }
        require(recommendedSTT?.supportNote.contains("Recommended") == true,
                "the model the worker recommends must be described that way, got: \(recommendedSTT?.supportNote ?? "nil")")

        // B24: a second engine of the same modality publishes a different control set. The shipped
        // engine's exact key set is not a membership test — the model's own published controls are.
        let imageCatalog = FixtureProtocol.catalogModels.filter { entry in
            guard (entry["capabilities"] as? [String])?.contains("txt2img") == true,
                  let repo = entry["repo"] as? String else { return false }
            return FixtureProtocol.packageModelIDs.contains(repo)
        }
        let imagePickers = store.supportedModels(for: .image)
        require(Set(imagePickers.map(\.repo)) == Set(imageCatalog.compactMap { $0["repo"] as? String }),
                "every image model the Hub publishes a package for must be selectable, got: \(imagePickers.map(\.repo).sorted())")
        let zimage = store.models.first { $0.repo == "fixture/zimage" }
        require(zimage?.supported == true,
                "a second image engine that publishes no guidance and no image_sizes must still be selectable")
        store.imageModel = "fixture/zimage"
        require(store.imageConfiguration?.stepsField == 8,
                "the model's own published steps default must be used, got: \(String(describing: store.imageConfiguration?.stepsField))")
        require(store.imageConfiguration?.guidanceField == nil,
                "a control this model never publishes must not be invented")
        require(store.imageConfiguration?.seedField == -1,
                "the model's own published seed default must be used")
        require(store.imageConfiguration?.sizes.count == 1
                && store.imageConfiguration?.sizes.first?.width == 1024,
                "a model publishing no image_sizes generates at the size its own defaults name")
        require(store.imageConfiguration?.defaults.guidance == nil,
                "the stored settings must carry no guidance for a model that has none")
        store.imagePrompt = "A fixture image from a second engine"
        await store.generateImage()
        require(store.error == nil, "a differently-shaped image engine must be able to generate: \(store.error ?? "none")")
        let zimageParams = (FixtureProtocol.generatedBody()?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(zimageParams?["steps"] as? Int == 8, "the model's own steps must be sent")
        require(zimageParams?["guidance"] == nil,
                "a control the model does not publish must be left out of the request for the engine to default")
        require(zimageParams?["width"] as? Int == 1024 && zimageParams?["height"] as? Int == 1024,
                "the size must be the one the model publishes")
        store.imageModel = "fixture/flux"
        require(store.imageConfiguration?.guidanceField == 1.0,
                "switching back must load the shipped engine's own controls")

        // The same rule for Music: a second engine publishing only a length and a step count is
        // still selectable, and the controls it never named are not sent.
        let musicCatalog = FixtureProtocol.catalogModels.filter { entry in
            guard (entry["capabilities"] as? [String])?.contains("music") == true,
                  let repo = entry["repo"] as? String else { return false }
            return FixtureProtocol.packageModelIDs.contains(repo)
        }
        let musicPickers = store.supportedModels(for: .music)
        require(Set(musicPickers.map(\.repo)) == Set(musicCatalog.compactMap { $0["repo"] as? String }),
                "every music model the Hub publishes a package for must be selectable, got: \(musicPickers.map(\.repo).sorted())")
        let secondMusic = store.models.first { $0.repo == "fixture/second-music" }
        require(secondMusic?.supported == true, "a second music engine with a different control set must be selectable")
        store.musicModel = "fixture/second-music"
        require(store.musicConfiguration?.defaults.durationSeconds == 20.0, "the model's own duration default")
        require(store.musicConfiguration?.guidanceField == nil && store.musicConfiguration?.shiftField == nil,
                "controls this music engine never publishes must not be invented")
        store.musicModel = "ACE-Step/Ace-Step1.5"

        // B25: a chat model is whatever the catalogue publishes as one. The capability is spelled
        // several ways across workers, and only two spellings were matched before.
        let chatPickers = store.supportedModels(for: .chat)
        let chatRepos = Set(chatPickers.map(\.repo))
        for repo in ["Qwen/Qwen3.5-9B", "fixture/chat-conversational", "fixture/chat-family",
                     "fixture/chat-annotated"] {
            require(chatRepos.contains(repo),
                    "\(repo) is published as a chat form and must be selectable, got: \(chatRepos.sorted())")
        }
        require(!chatRepos.contains("mlx-community/whisper-large-v3-turbo"),
                "a model the catalogue does not publish as a chat form must not appear in the Chat picker")
        require(store.chatModel == "Qwen/Qwen3.5-9B",
                "the Chat page must open on the first chat model the Hub offers, got: \(store.chatModel)")

        // B26: the Audio page opens on a model that is really in its own list, on the tab that
        // model belongs to. The old repair wrote a hardcoded repo into the selection, so on a
        // library without that exact model the page opened on nothing and Generate stayed shut.
        require(store.supportedModels(for: .audio).contains { $0.repo == store.audioModel },
                "the Audio selection must be one of the models the Audio picker lists, got: \(store.audioModel)")
        require(store.supportedModels(for: .audio).contains { $0.repo == store.audioModel && $0.voiceMode == store.audioMode },
                "the Audio tab must be the one the selected model belongs to")

        // B29: the display and aggregation lists are derived, so a Studio cannot be missing from
        // the sidebar, the status line or the history tabs.
        let engineFunctions = StudioFunction.allCases.filter { $0.hasEngine }
        require(Set(StudioPage.create.compactMap { $0.function }) == Set(engineFunctions),
                "every Studio with an engine must have a Create page, got: \(StudioPage.create)")
        require(StudioPage.comingLater.allSatisfy { $0.function?.hasEngine == false },
                "the Coming later section must hold exactly the Studios with no engine")
        require(Set(StudioFunction.allCases.map { StudioPage.page(for: $0) }) == Set(StudioFunction.allCases.map { Optional(StudioPage(rawValue: $0.rawValue)) }),
                "every Studio must keep a page whose raw value matches, got: \(StudioFunction.allCases.map { $0.rawValue })")
        // `HistoryTab` and the Jobs page derive from this same pair — `hasBatchHistory` for the
        // tabs, `jobHistories` for the sections — so asserting the two here is asserting the tab
        // list without needing the SwiftUI layer in this binary.
        let historyFunctions = Set(store.jobHistories.map(\.function))
        require(historyFunctions == Set(StudioFunction.allCases.filter { $0.hasBatchHistory }),
                "every Studio with a Hub batch history must have a history tab and a Jobs section, got: \(historyFunctions.map(\.rawValue).sorted())")
        for history in store.jobHistories {
            require(!history.function.hasEngine || history.jobs.allSatisfy { !$0.id.isEmpty },
                    "\(history.function.rawValue) rows must be real jobs")
        }

        // B28: "Use in …" must point the Studio that owns the model at it. It used to set the
        // Subtitle selection for every non-audio model.
        for function in engineFunctions where function != .video {
            guard let model = store.models.first(where: { $0.function == function && $0.supported }) else { continue }
            store.select(model)
            let selected = store.supportedModels(for: function).contains { $0.repo == model.repo }
            require(selected, "Use in \(function.rawValue) must select the model in its own picker, got: \(model.repo)")
        }
        store.select(store.models.first { $0.repo == "Qwen/Qwen3.5-9B" }!)
        require(store.chatModel == "Qwen/Qwen3.5-9B", "Use in Chat must set the Chat selection, not Subtitle")
        require(store.transcriptionModel != "Qwen/Qwen3.5-9B", "a Chat model must never become the Subtitle selection")

        // B27: a preset roster belongs to the family that published it and is lent to no other.
        let secondPreset = store.models.first { $0.repo == "fixture/second-preset" }
        require(secondPreset?.speakers == ["Sophea", "Dara"],
                "a second preset family's own published roster must reach its model, got: \(String(describing: secondPreset?.speakers))")
        require(secondPreset?.publishesPresetRoster == true, "a family that published a roster must say so")
        store.audioModel = "fixture/second-preset"
        require(store.audioSpeaker == "Sophea",
                "the chosen speaker must follow the selected model's roster, got: \(store.audioSpeaker)")
        store.audioModel = "khmerttsopensource/khmer-tts"
        require(store.audioSpeaker.isEmpty, "a single-voice family must not be handed another family's speaker")
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
        require(store.audioSpeaker == "Ryan", "selecting the Qwen preset model must seed its own roster again")

        // B23: a batch whose modality this build cannot place is named, not skipped before the
        // loop. Both the fixture's unknown modality and the one that maps to a Studio with no
        // batch history must appear in the startup note.
        require(store.startupNote?.contains("hologram") == true,
                "a modality with no Studio must be reported by name, got: \(store.startupNote ?? "nil")")
        require(store.startupNote?.contains("video") == true,
                "a Studio with no batch history must be reported by name, got: \(store.startupNote ?? "nil")")
        require(store.startupNote?.contains("native/StudioAPI.swift") == true,
                "the report must name the file to update, got: \(store.startupNote ?? "nil")")
        require(store.audioJobs.allSatisfy { $0.id != "hologram-job" && $0.id != "video-job" },
                "an unplaceable batch must not be filed under another Studio's history")
        store.clearStartupNote()

        // B31: the worker owns the voice-library vocabulary. Render what it published, and offer
        // the short list only where the worker supplied nothing.
        require(NativeVoice.genderLabel(for: "m") == "Male" && NativeVoice.genderLabel(for: "f") == "Female",
                "the worker's own gender codes must be spelled out")
        require(NativeVoice.genderLabel(for: "nonbinary") == "Nonbinary",
                "an out-of-vocabulary gender must be rendered, not replaced with Neutral")
        require(NativeVoice.genderLabel(for: "") == "Not stated",
                "a field the worker did not supply must say so rather than claim a value")
        require(NativeVoice.languageChoices(published: ["km", "en"]).contains("km"),
                "a language the library holds must be offered: the app cannot tag only its own list")
        require(NativeVoice.languageChoices(published: []).contains("en"),
                "with no published language the short fallback list is offered")
        require(NativeVoice.licenseChoices(current: "studio-licence").contains { $0.id == "studio-licence" },
                "a licence the worker published must stay selectable, not silently blank the picker")

        // A model the catalog does not declare drivable is still handled sensibly: it is offered
        // with a truthful reason, and nothing claims it can run here. Hiding it would put the app
        // back where it started; claiming it is ready would be worse.
        require(store.models.first { $0.repo == "other/tts" }?.supported == false
                && store.models.first { $0.repo == "other/tts" }?.supportNote.contains("packaged model catalog") == true,
                "a model with no Hub package must be listed with a catalog note, not hidden")
        let unwired = store.models.first { $0.repo == "fixture/unwired-voice" }
        require(unwired?.supported == true, "a shipped model must stay visible even before its runtime is ready")
        require(unwired?.runtimeReady == false, "a family the runtime does not report must never be shown as ready")
        require(unwired?.voiceMode == .preset, "a model that declares no cloning is the preset form")
        require(!store.models.contains { $0.repo == "mlx-community/S3TokenizerV2" },
                "a companion codec the catalogue does not publish as a TTS form is not a voice model")

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
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"

        store.audioText = "Hello locally"
        store.audioSpeaker = "Ryan"
        store.audioLanguage = "en"
        await store.generateAudio()
        require(store.audioJobs.first?.id == "new-job",
                "submitted audio job; error=\(store.error ?? "none") bodies=\(FixtureProtocol.generatedBodies().count)")
        let generated = FixtureProtocol.generatedBody()
        require(FixtureProtocol.lastGeneratePath == "/api/hub/jobs", "native generation must use Hub admission")
        require(FixtureProtocol.lastGenerateToken == "native-test-token", "native Hub credential missing")
        require(generated?["modality"] as? String == "voice", "Hub modality")
        require(generated?["model"] as? String == supportedAudio[0].repo, "Hub model")
        require((generated?["client_request_id"] as? String)?.count ?? 0 >= 8,
                "durable client_request_id missing - the Hub reads snake_case")
        let items = generated?["items"] as? [[String: Any]]
        require((items?.first?["params"] as? [String: Any])?["speed"] as? Double == 1.0,
                "the engine's own pace must be sent explicitly, got: \(String(describing: items?.first?["params"]))")
        require(items?.first?["text"] as? String == "Hello locally", "Hub text item")
        require((items?.first?["params"] as? [String: Any])?["preset_speaker"] as? String == "Ryan", "Hub speaker params")

        // A newly visible preset family must submit as itself. Khmer VITS has one built-in speaker
        // and no roster, so the app must not lend it the Qwen names it happens to be holding: the
        // job is sent with no speaker rather than with a voice the model has never heard of.
        store.audioModel = "khmerttsopensource/khmer-tts"
        store.audioText = "សួស្តី"
        store.audioLanguage = "km"
        await store.generateAudio()
        require(store.error == nil, "a preset family must generate; error=\(store.error ?? "none")")
        let khmerParams = (FixtureProtocol.generatedBody()?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(khmerParams?["preset_speaker"] as? String == "",
                "a single-speaker model must not be sent another family's preset speaker, got: \(String(describing: khmerParams))")
        require(khmerParams?["speed"] as? Double == 1.0, "the engine's own pace must still be sent")
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"

        // A voice clip is the result the owner most cares about, and it used to be saved
        // without being checked or receipted at all: a damaged WAV could be written silently,
        // and - because the Hub was never told the result was taken - the protected copy was
        // never released for cleanup, so local storage grew forever.
        let audio = store.audioJobs.first { $0.id == "new-job" }!
        let audioBytes = try await store.audioData(for: audio)
        require(audioBytes == FixtureProtocol.wav, "audio export data")
        FixtureProtocol.setCorruptAudio(true)
        do { _ = try await store.audioData(for: audio); fatalError("a truncated voice clip was accepted") }
        catch { require(error.localizedDescription.contains("verification"),
                        "a voice clip that does not match its receipt needs an actionable message") }
        FixtureProtocol.setCorruptAudio(false)
        let audioDestination = temporary.appendingPathComponent("saved-audio.wav")
        try await store.saveAudio(audio, to: audioDestination)
        let savedAudio = try Data(contentsOf: audioDestination)
        require(savedAudio == FixtureProtocol.wav, "saved voice clip bytes")
        // A generated asset states what it is: the app used to show only the model and state.
        let facts = audio.assetFacts
        require(facts.contains("12.5 s"), "a clip's length must be shown: \(facts)")
        require(facts.contains("KB") || facts.contains("bytes"),
                "the file size must be shown: \(facts)")

        require(FixtureProtocol.audioAcks() == ["/api/hub/jobs/new-job/items/0/ack"],
                "a verified voice clip save must acknowledge delivery exactly once")
        let unwritableAudio = temporary.appendingPathComponent("missing-directory/never.wav")
        do {
            try await store.saveAudio(audio, to: unwritableAudio)
            fatalError("a voice clip save into a missing directory must fail")
        } catch {
            require(FixtureProtocol.audioAcks().count == 1,
                    "a failed voice clip save must never acknowledge delivery")
        }
        FixtureProtocol.setAudioAckRejects(true)
        let rejectedAudio = temporary.appendingPathComponent("rejected-audio.wav")
        do {
            try await store.saveAudio(audio, to: rejectedAudio)
            fatalError("a rejected voice clip acknowledgement must be reported")
        } catch {
            require(error.localizedDescription.contains("was not acknowledged"),
                    "a rejected voice clip acknowledgement needs an actionable message")
        }
        require(FileManager.default.fileExists(atPath: rejectedAudio.path),
                "the verified voice clip must remain on disk when acknowledgement fails")
        FixtureProtocol.setAudioAckRejects(false)


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
        require(beforeReplay["client_request_id"] as? String == replayed["client_request_id"] as? String,
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
        require(imageFirst["client_request_id"] as? String == imageReplay["client_request_id"] as? String, "Image replay changed identity")
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
        require(FixtureProtocol.generatedBodies().last!["client_request_id"] as? String == uncertainImageRequest["client_request_id"] as? String, "uncertain Image response generated a new request identity")
        require(!FileManager.default.fileExists(atPath: imagePending.path), "reconciled Image response remained pending")
        FixtureProtocol.setUncertainImage(true)
        store.imagePrompt = "A second different request"
        await store.generateImage()
        let beforeImageCheck = FixtureProtocol.generatedBodies().count
        await store.reconcileImage(store.imageJobs.first!)
        require(FixtureProtocol.generatedBodies().count == beforeImageCheck, "Check Original Result submitted another generation")
        require(store.imageJobs.first?.state == "done", "Image result reconciliation did not refresh history")
        require(!FileManager.default.fileExists(atPath: imagePending.path), "verified original Image result did not clear its matching pending identity")

        // Discarding an uncertain attempt must reach the Hub's discard route with the item's own
        // index. It is the only action that releases a job nobody can reconcile.
        FixtureProtocol.setUncertainImage(true)
        await store.refresh()
        let uncertainImageJob = store.imageJobs.first { $0.id == "image-uncertain" }
        require(uncertainImageJob?.state == "uncertain", "the fixture must present an uncertain Image")
        await store.discardImage(uncertainImageJob!)
        require(FixtureProtocol.discardRequests().last == "/api/hub/jobs/image-uncertain/items/0/discard",
                "discard must address the item by its index, got: \(FixtureProtocol.discardRequests())")
        require(store.error == nil, "a successful discard must not leave an error, got: \(store.error ?? "nil")")
        FixtureProtocol.setUncertainImage(false)
        await store.refresh()

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
        require(oldImageRequest["client_request_id"] as? String != newImageRequest["client_request_id"] as? String, "pending Image identity crossed device state directories")
        require((newImageRequest["items"] as? [[String: Any]])?.first?["prompt"] as? String == "New device prompt", "old Image prompt crossed device state directories")
        imageConnectionStore.disconnect()

        // Automatic placement is the default: an unpinned app must not send a routing field at
        // all, or the Hub would have to guess what an empty pin means.
        require(imageReplay["routing"] == nil, "an unpinned job must leave routing to the Hub")
        store.routingPin = "machine:studio-3"
        store.imagePrompt = "Pinned to one Mac"
        await store.generateImage()
        require(FixtureProtocol.generatedBodies().last?["routing"] as? String == "machine:studio-3",
                "a machine pin must reach the Hub: \(String(describing: FixtureProtocol.generatedBodies().last?["routing"]))")
        store.routingPin = nil

        // Music: the same durable Hub route as Image, with the Music worker's own controls. The
        // catalogue publishes a Music-shaped `generation_defaults` under the same key Image uses,
        // so reaching here also pins that one modality cannot break the whole catalogue decode.
        require(store.musicJobs.first?.id == "music-job", "Music history must restore through Hub")
        require(store.musicModel == "ACE-Step/Ace-Step1.5", "the Music model must come from the catalogue")
        require(store.musicSettings?.durationSeconds == 30 && store.musicSettings?.steps == 8
                && store.musicSettings?.guidanceScale == 1 && store.musicSettings?.shift == 3
                && store.musicSettings?.seed == -1 && store.musicSettings?.instrumental == true,
                "Music defaults must come from the runtime")
        require(store.musicConfiguration?.limits.maxDurationSeconds == 600
                && store.musicConfiguration?.limits.maxGuidance == 30,
                "Music limits must come from the runtime")
        let musicModelInfo = store.models.first { $0.function == .music && $0.repo == "ACE-Step/Ace-Step1.5" }
        require(musicModelInfo?.sampleRateHz == 48000 && musicModelInfo?.channels == 2,
                "the published WAV shape must reach the app")
        store.musicPrompt = "Warm lo-fi beat"
        await store.generateMusic()
        let musicBody = FixtureProtocol.generatedBodies().last!
        require(musicBody["modality"] as? String == "music", "Music must submit through the Hub job queue")
        require(musicBody["model"] as? String == "ACE-Step/Ace-Step1.5", "Music must name its model")
        let musicItem = (musicBody["items"] as? [[String: Any]])?.first
        require(musicItem?["prompt"] as? String == "Warm lo-fi beat", "Music prompt must reach the Hub")
        let musicWireParams = musicItem?["params"] as? [String: Any]
        require((musicWireParams?["duration_seconds"] as? NSNumber)?.doubleValue == 30,
                "Music must send duration_seconds the worker reads")
        require((musicWireParams?["guidance_scale"] as? NSNumber)?.doubleValue == 1,
                "Music must send guidance_scale the worker reads")
        require((musicWireParams?["shift"] as? NSNumber)?.doubleValue == 3,
                "Music must send shift the worker reads")
        require((musicWireParams?["vocal_language"] as? String) == "unknown",
                "Music must send vocal_language the worker reads")
        require(musicWireParams?["instrumental"] as? Bool == true, "Music must send the instrumental flag")
        let music = store.musicJobs.first!
        require(music.musicSettings?.steps == 8, "an accepted Music job must carry its settings back")
        let verifiedMusic = try await store.musicData(for: music)
        require(verifiedMusic == FixtureProtocol.wav, "verified Music artifact")
        FixtureProtocol.setCorruptMusic(true)
        do { _ = try await store.musicData(for: music); fatalError("corrupt Music artifact was accepted") }
        catch { require(error.localizedDescription.contains("verification"), "Music hash mismatch needs an actionable message") }
        FixtureProtocol.setCorruptMusic(false)
        require(FixtureProtocol.musicAckRequests().isEmpty, "unverified Music reads must not acknowledge delivery")
        // A save that cannot even be written must never reach the Hub receipt, even though
        // the artifact itself was fetched and hash-verified successfully first.
        let unwritableMusic = temporary.appendingPathComponent("missing-directory/never.wav")
        do {
            try await store.saveMusic(music, to: unwritableMusic)
            fatalError("a Music save into a missing directory must fail")
        } catch {
            require(FixtureProtocol.musicAckRequests().isEmpty, "a failed Music save must never acknowledge delivery")
        }
        let musicDestination = temporary.appendingPathComponent("saved-music.wav")
        try await store.saveMusic(music, to: musicDestination)
        let savedMusic = try Data(contentsOf: musicDestination)
        require(savedMusic == FixtureProtocol.wav, "Music export bytes")
        require(FixtureProtocol.musicAckRequests() == ["/api/hub/jobs/music-job/items/0/ack"],
                "a verified Music save must acknowledge delivery exactly once")
        FixtureProtocol.setMusicAckRejects(true)
        let rejectedMusic = temporary.appendingPathComponent("rejected-music.wav")
        do {
            try await store.saveMusic(music, to: rejectedMusic)
            fatalError("a rejected Music acknowledgement must be reported")
        } catch {
            require(error.localizedDescription.contains("was not acknowledged"),
                    "rejected Music acknowledgement needs an actionable message")
        }
        require(FileManager.default.fileExists(atPath: rejectedMusic.path),
                "the verified Music file must remain on disk when acknowledgement fails")
        FixtureProtocol.setMusicAckRejects(false)
        store.restoreMusicDefaults()
        require(store.musicSettings?.steps == 8 && store.musicSettings?.seed == -1,
                "Restore Music Defaults must use runtime values")

        FixtureProtocol.setUncertainMusic(true)
        await store.refresh()
        store.musicPrompt = "Warm lo-fi beat"
        let beforeUncertainMusic = FixtureProtocol.generatedBodies().count
        await store.generateMusic()
        require(FixtureProtocol.generatedBodies().count == beforeUncertainMusic,
                "the same unresolved Music request must not be submitted as new work")
        require(store.error?.contains("uncertain") == true,
                "an unresolved Music repeat needs a reconciliation explanation")
        store.musicPrompt = "A different new track"
        await store.generateMusic()
        require(FixtureProtocol.generatedBodies().count == beforeUncertainMusic + 1,
                "uncertainty must not block a different new Music request")
        let musicPending = state.appendingPathComponent("native-pending-music.json")
        require(FileManager.default.fileExists(atPath: musicPending.path),
                "an accepted uncertain Music response must retain its original request")
        require(store.musicNote?.contains("uncertain") == true,
                "an uncertain Music response must say so beside the form")
        let uncertainMusicRequest = FixtureProtocol.generatedBodies().last!
        FixtureProtocol.setUncertainMusic(false)
        await store.generateMusic()
        require(FixtureProtocol.generatedBodies().last!["client_request_id"] as? String
                    == uncertainMusicRequest["client_request_id"] as? String,
                "an uncertain Music response must replay its original identity, not mint a new one")
        require(!FileManager.default.fileExists(atPath: musicPending.path),
                "a reconciled Music response must clear its pending identity")
        FixtureProtocol.setUncertainMusic(true)
        store.musicPrompt = "A second different track"
        await store.generateMusic()
        let beforeMusicCheck = FixtureProtocol.generatedBodies().count
        await store.reconcileMusic(store.musicJobs.first!)
        require(FixtureProtocol.generatedBodies().count == beforeMusicCheck,
                "Check Original Result must not submit another Music generation")
        require(store.musicJobs.first?.state == "done", "Music reconciliation must refresh history")
        require(!FileManager.default.fileExists(atPath: musicPending.path),
                "a verified original Music result must clear its matching pending identity")

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
        // Saving and testing a Hugging Face token go through the worker's own settings routes,
        // which the Hub proxies. These used to be stubs that always failed while the screen
        // showed a masked token, so the control looked functional and never was.
        let tokenSaved = await tokenStore.saveHFToken("hf_current")
        require(tokenSaved, "a token must save through the Hub: \(tokenStore.error ?? "no error")")
        require(tokenStore.tokenStatus.isSet, "the saved token must read as set")
        let tokenTest = await tokenStore.testHFToken("hf_current")
        require(tokenTest?.name == "Fixture User", "a token test must report who it belongs to: \(String(describing: tokenTest))")
        require(tokenTest?.organizations == ["fixture-org"], "the test must report organisations")
        await tokenStore.removeModel(supportedSTT[0])
        require(tokenStore.error?.contains("active readers") == true, "unsafe model removal should be blocked")
        tokenStore.disconnect()

        store.audioText = "Persist this draft"
        store.transcriptionModel = supportedSTT[0].repo
        store.musicPrompt = "Persist this music draft"
        store.disconnect()
        let preferences = state.appendingPathComponent("native-workspaces.json")
        require(FileManager.default.fileExists(atPath: preferences.path), "workspace choices not persisted")
        let persistedPreferences = String(decoding: try Data(contentsOf: preferences), as: UTF8.self)
        require(persistedPreferences.contains("Persist this music draft"),
                "the Music draft must persist like the other workspaces")
        let mode = try FileManager.default.attributesOfItem(atPath: preferences.path)[.posixPermissions] as? NSNumber
        require(mode?.intValue == 0o600, "workspace choices must be private")

        // ── Remote app update ──────────────────────────────────────────────────────────────
        // Every signing key below is generated here and thrown away: no real key is trusted.
        let updateDirectory = temporary.appendingPathComponent("Update State")
        let updateStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await updateStore.connect(baseURL: URL(string: "http://127.0.0.1:45690")!,
                                  dataDirectory: updateDirectory, token: "native-test-token")
        updateStore.currentAppBuild = "currentbuild"
        updateStore.currentAppVersion = "0.6.50"

        // The pure version-order rule behind the offer. Both the advertised version and this Mac's
        // own CFBundleShortVersionString are dotted numeric strings compared component by component.
        require(ReleaseOffer.decide(advertisedVersion: "0.6.52", advertisedBuild: "b",
                                    installedVersion: "0.6.51", installedBuild: "a") == .offered,
                "a newer advertised version must be offered")
        require(ReleaseOffer.decide(advertisedVersion: "0.6.51", advertisedBuild: "b",
                                    installedVersion: "0.6.51", installedBuild: "a") == .offered,
                "a rebuild of the same version must be offered")
        require(ReleaseOffer.decide(advertisedVersion: "0.6.51", advertisedBuild: "a",
                                    installedVersion: "0.6.51", installedBuild: "a") == .sameVersionSameBuild,
                "the same version and build is not an update")
        require(ReleaseOffer.decide(advertisedVersion: "0.6.50", advertisedBuild: "b",
                                    installedVersion: "0.6.51", installedBuild: "a") == .olderRelease,
                "an older advertised version must never be offered")
        require(ReleaseOffer.decide(advertisedVersion: "0.6.100", advertisedBuild: "b",
                                    installedVersion: "0.6.51", installedBuild: "a") == .offered,
                "version components must compare numerically, not as strings")
        require(ReleaseOffer.decide(advertisedVersion: "nightly", advertisedBuild: "b",
                                    installedVersion: "0.6.51", installedBuild: "a") == .offered,
                "an unparseable version falls back to build inequality (different builds)")
        require(ReleaseOffer.decide(advertisedVersion: "nightly", advertisedBuild: "a",
                                    installedVersion: "0.6.51", installedBuild: "a") == .sameVersionSameBuild,
                "an unparseable version falls back to build inequality (same build)")
        require(ReleaseOffer.decide(advertisedVersion: "0.6.51", advertisedBuild: "b",
                                    installedVersion: nil, installedBuild: "a") == .offered,
                "a missing installed version falls back to build inequality")
        require(ReleaseVersion("0.6") == ReleaseVersion("0.6.0") && ReleaseVersion("0.6") != nil,
                "trailing zero components are the same version")
        require(ReleaseVersion("0.6.51-beta") == nil, "a non-numeric component does not parse")

        let controllerKey = Curve25519.Signing.PrivateKey()
        let gatewayKey = Curve25519.Signing.PrivateKey()
        let controllerFingerprint = releaseSha256(controllerKey.publicKey.rawRepresentation)
        let identity = releaseIdentityJSON(publicKey: controllerKey.publicKey.rawRepresentation,
                                           fingerprint: controllerFingerprint)
        let artifact = Data(repeating: 0x61, count: 4096)
        let descriptor = makeReleaseDescriptor(build: "newbuild", artifact: artifact,
                                               gateway: gatewayKey, controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(descriptor), artifact: artifact)

        // The first successful read pins the controller key and does not verify a release against
        // a key fetched at the same time; the operator is told to check again.
        await updateStore.refreshRelease()
        require(updateStore.releasePublisherKey?.fingerprint == controllerFingerprint,
                "the first successful identity read must pin the controller key")
        require(!updateStore.updateOffered,
                "a key pinned in the same read must not verify the release it arrived with")
        require(updateStore.releaseRefusal?.contains("pinned") == true,
                "the operator must be told the key was just pinned: \(updateStore.releaseRefusal ?? "nil")")

        // The second read verifies the countersignature against the stored pin.
        await updateStore.refreshRelease()
        require(updateStore.updateOffered,
                "an offered release for a different build must be installable: \(updateStore.releaseRefusal ?? "nil")")
        require(updateStore.offeredRelease?.version == "0.6.50", "the offered version must be exposed")
        require(updateStore.offeredRelease?.build == "newbuild", "the offered build must be exposed")
        require(updateStore.offeredRelease?.bytes == Int64(artifact.count), "the offered size must be exposed")
        require(updateStore.offeredRelease?.sha256 == releaseSha256(artifact), "the offered digest must be exposed")
        require(updateStore.offeredRelease?.controllerKeyId == controllerFingerprint,
                "the offered release must name who signed it")
        require(updateStore.releaseRefusal == nil, "a verified offer carries no refusal")

        // The advertised build matching this app's own build is not an update.
        updateStore.currentAppBuild = "newbuild"
        await updateStore.refreshRelease()
        require(!updateStore.updateOffered, "a release matching this app's own build is not an update")
        updateStore.currentAppBuild = "currentbuild"

        // A controller holding an OLDER version must not offer it: a stale controller cannot roll
        // this Mac backwards. It stays visible with its reason, but with no install action.
        updateStore.currentAppVersion = "0.6.51"
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(descriptor), artifact: artifact)
        await updateStore.refreshRelease()
        require(updateStore.offeredRelease?.version == "0.6.50",
                "an older release is still shown so the reason is visible")
        require(!updateStore.updateOffered, "an older release must not be offered")
        require(!updateStore.showingUpdateBanner,
                "an older release must show no install banner")
        require(updateStore.offeredReleaseIsOlder,
                "an older release must be flagged as older rather than as a refusal")
        require(updateStore.releaseRefusal == nil,
                "an older release is not a verification failure: \(updateStore.releaseRefusal ?? "nil")")
        var olderInstallerRan = false
        updateStore.installVerifiedArtifact = { _, _ in olderInstallerRan = true; return "newbuild" }
        await updateStore.installOfferedRelease()
        require(!olderInstallerRan, "an older release must have no working Install action")

        // A NEWER version must be offered even when its build string is identical to this Mac's.
        let newerDescriptor = makeReleaseDescriptor(version: "0.6.52", build: "currentbuild",
                                                    artifact: artifact, gateway: gatewayKey,
                                                    controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(newerDescriptor), artifact: artifact)
        await updateStore.refreshRelease()
        require(updateStore.updateOffered,
                "a newer version must be offered even when the build matches: \(updateStore.releaseRefusal ?? "nil")")
        require(!updateStore.offeredReleaseIsOlder, "a newer version is not flagged as older")

        // Back to the baseline: same version as this Mac, different build.
        updateStore.currentAppVersion = "0.6.50"
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(descriptor), artifact: artifact)
        await updateStore.refreshRelease()

        // A release whose controller_signature does not verify against the pinned key is refused.
        let impostor = Curve25519.Signing.PrivateKey()
        let impostorDescriptor = makeReleaseDescriptor(
            build: "newbuild", artifact: artifact, gateway: gatewayKey,
            controllerSigning: impostor, controllerKeyId: controllerFingerprint)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(impostorDescriptor), artifact: artifact)
        await updateStore.refreshRelease()
        require(!updateStore.updateOffered, "an unverifiable countersignature must not be offered")
        require(updateStore.releaseRefusal?.lowercased().contains("signature") == true,
                "the refusal must name the signature: \(updateStore.releaseRefusal ?? "nil")")

        // A different controller key presented later must not replace the pin.
        let otherKey = Curve25519.Signing.PrivateKey()
        let otherFingerprint = releaseSha256(otherKey.publicKey.rawRepresentation)
        let otherIdentity = releaseIdentityJSON(publicKey: otherKey.publicKey.rawRepresentation,
                                                fingerprint: otherFingerprint)
        let otherDescriptor = makeReleaseDescriptor(build: "newbuild", artifact: artifact,
                                                    gateway: gatewayKey, controllerSigning: otherKey)
        FixtureProtocol.setRelease(identity: otherIdentity, body: releaseBody(otherDescriptor), artifact: artifact)
        await updateStore.refreshRelease()
        require(updateStore.releasePublisherKey?.fingerprint == controllerFingerprint,
                "a different controller key must not replace the pinned one")
        require(!updateStore.updateOffered,
                "a release signed by an unpinned key must not be offered")

        // A signed descriptor whose artifact bytes hash differently is refused before the swap.
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(descriptor), artifact: artifact,
                                   override: Data(repeating: 0x62, count: artifact.count))
        await updateStore.refreshRelease()
        require(updateStore.updateOffered,
                "the good descriptor must still verify: \(updateStore.releaseRefusal ?? "nil")")
        var installerRan = false
        updateStore.installVerifiedArtifact = { _, _ in installerRan = true; return "newbuild" }
        await updateStore.installOfferedRelease()
        require(!installerRan, "a SHA-256 mismatch must never reach the swap")
        require(updateStore.connectionNotice?.kind == .failure, "a SHA-256 mismatch must be reported")
        require(updateStore.connectionNotice?.text.lowercased().contains("sha-256") == true,
                "the report must name the hash: \(updateStore.connectionNotice?.text ?? "nil")")
        // A completed download whose hash is wrong is not a resumable prefix, so it is discarded.
        let shaDescriptor = ReleaseDescriptor(json: descriptor)!
        let shaArtifactURL = ReleaseArtifactStore.artifactURL(base: updateDirectory.standardizedFileURL,
                                                              descriptor: shaDescriptor)
        require(!FileManager.default.fileExists(atPath: shaArtifactURL.path),
                "a completed download whose hash is wrong must be discarded, not kept for a resume")
        require(FixtureProtocol.releaseReads() >= 1, "the wrong-hash attempt must have downloaded once")

        // The happy path: a verified artifact reaches the swap, and the existing restart path is
        // asked exactly once. The installer is stubbed, so nothing touches /Applications.
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(descriptor), artifact: artifact)
        await updateStore.refreshRelease()
        require(updateStore.updateOffered, "the good release must be offered again: \(updateStore.releaseRefusal ?? "nil")")
        var swaps = 0
        var restarts = 0
        updateStore.onReleaseRestartRequested = { restarts += 1 }
        updateStore.installVerifiedArtifact = { _, _ in swaps += 1; return "newbuild" }
        await updateStore.installOfferedRelease()
        require(swaps == 1, "a verified artifact must reach the swap exactly once")
        require(restarts == 1, "a completed install must ask the existing restart path exactly once")
        require(updateStore.connectionNotice?.kind == .success, "a completed install must be reported")
        require(updateStore.connectionNotice?.text.contains("Restarting") == true,
                "the report must say the services restart: \(updateStore.connectionNotice?.text ?? "nil")")
        require(updateStore.currentAppBuild == "newbuild",
                "the installed build must become the current one so the banner clears")
        require(updateStore.currentAppVersion == "0.6.50",
                "the installed version must become the current one too")

        // A kept partial file is found again and resumed with a Range request, completing to the
        // exact signed artifact. The Range header sent is asserted, and the bytes the swap sees.
        let rangeDescriptorJSON = makeReleaseDescriptor(version: "0.6.52", build: "rangebuild",
                                                        artifact: artifact, gateway: gatewayKey,
                                                        controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(rangeDescriptorJSON), artifact: artifact)
        FixtureProtocol.setArtifactRangeMode(.honour)
        FixtureProtocol.resetArtifactRequests()
        await updateStore.refreshRelease()
        require(updateStore.updateOffered, "the range release must be offered: \(updateStore.releaseRefusal ?? "nil")")
        let rangeDescriptor = ReleaseDescriptor(json: rangeDescriptorJSON)!
        let rangeURL = ReleaseArtifactStore.artifactURL(base: updateDirectory.standardizedFileURL,
                                                        descriptor: rangeDescriptor)
        try FileManager.default.createDirectory(at: rangeURL.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try Data(artifact.prefix(1000)).write(to: rangeURL)
        var capturedRangeBytes: Data?
        updateStore.installVerifiedArtifact = { file, _ in
            capturedRangeBytes = try? Data(contentsOf: file)
            return "rangebuild"
        }
        await updateStore.installOfferedRelease()
        require(capturedRangeBytes == artifact, "the resumed download must be the complete artifact")
        require(FixtureProtocol.artifactRangeHeaders().contains("bytes=1000-"),
                "the retry must ask for the remainder with a Range header: \(FixtureProtocol.artifactRangeHeaders())")
        require(updateStore.connectionNotice?.kind == .success,
                "a resumed install must be reported as success: \(updateStore.connectionNotice?.text ?? "nil")")

        // A server that ignores Range answers 200 with the whole body; the stale fragment must be
        // replaced, never appended to, or the artifact would be corrupt.
        let ignoreDescriptorJSON = makeReleaseDescriptor(version: "0.6.53", build: "ignorebuild",
                                                         artifact: artifact, gateway: gatewayKey,
                                                         controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(ignoreDescriptorJSON), artifact: artifact)
        FixtureProtocol.setArtifactRangeMode(.ignore)
        FixtureProtocol.resetArtifactRequests()
        await updateStore.refreshRelease()
        require(updateStore.updateOffered, "the ignore-range release must be offered: \(updateStore.releaseRefusal ?? "nil")")
        let ignoreDescriptor = ReleaseDescriptor(json: ignoreDescriptorJSON)!
        let ignoreURL = ReleaseArtifactStore.artifactURL(base: updateDirectory.standardizedFileURL,
                                                         descriptor: ignoreDescriptor)
        try FileManager.default.createDirectory(at: ignoreURL.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try Data(repeating: 0x00, count: 1000).write(to: ignoreURL)
        var capturedRestartBytes: Data?
        updateStore.installVerifiedArtifact = { file, _ in
            capturedRestartBytes = try? Data(contentsOf: file)
            return "ignorebuild"
        }
        await updateStore.installOfferedRelease()
        require(capturedRestartBytes == artifact,
                "a 200 answer must restart the file cleanly rather than append to the stale fragment")
        require(FixtureProtocol.artifactRangeHeaders().contains("bytes=1000-"),
                "the request must still offer the range: \(FixtureProtocol.artifactRangeHeaders())")

        // A failed swap keeps the completed artifact; pressing Install again reuses it with no
        // second transfer, and only a successful install removes it.
        let keepDescriptorJSON = makeReleaseDescriptor(version: "0.6.54", build: "keepbuild",
                                                       artifact: artifact, gateway: gatewayKey,
                                                       controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(keepDescriptorJSON), artifact: artifact)
        FixtureProtocol.setArtifactRangeMode(.honour)
        FixtureProtocol.resetArtifactRequests()
        await updateStore.refreshRelease()
        require(updateStore.updateOffered, "the keep release must be offered: \(updateStore.releaseRefusal ?? "nil")")
        let keepDescriptor = ReleaseDescriptor(json: keepDescriptorJSON)!
        let keepURL = ReleaseArtifactStore.artifactURL(base: updateDirectory.standardizedFileURL,
                                                       descriptor: keepDescriptor)
        require(!FileManager.default.fileExists(atPath: keepURL.path),
                "the keep test must start with no artifact")
        var failSwap = true
        updateStore.installVerifiedArtifact = { _, _ in
            if failSwap { throw ReleaseError("simulated swap failure") }
            return "keepbuild"
        }
        await updateStore.installOfferedRelease()
        require(FileManager.default.fileExists(atPath: keepURL.path),
                "a failed swap must keep the completed artifact")
        require(updateStore.connectionNotice?.kind == .failure, "a failed swap must be reported")
        let keepPermissions = try FileManager.default.attributesOfItem(atPath: keepURL.path)[.posixPermissions] as? NSNumber
        require(keepPermissions?.intValue == 0o600, "the kept artifact must be private")
        let readsAfterFailure = FixtureProtocol.releaseReads()
        failSwap = false
        await updateStore.installOfferedRelease()
        require(!FileManager.default.fileExists(atPath: keepURL.path),
                "a successful install must remove the kept artifact")
        require(FixtureProtocol.releaseReads() == readsAfterFailure,
                "a kept complete artifact must be reused instead of re-downloaded")
        require(updateStore.connectionNotice?.kind == .success,
                "the retry from a kept artifact must install")

        // A partial file LARGER than the signed size cannot be a prefix of the artifact, so it is
        // discarded and the download starts clean rather than being appended to.
        let oversizedDescriptorJSON = makeReleaseDescriptor(version: "0.6.55", build: "oversizedbuild",
                                                            artifact: artifact, gateway: gatewayKey,
                                                            controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(oversizedDescriptorJSON), artifact: artifact)
        FixtureProtocol.setArtifactRangeMode(.honour)
        FixtureProtocol.resetArtifactRequests()
        await updateStore.refreshRelease()
        require(updateStore.updateOffered, "the oversized release must be offered: \(updateStore.releaseRefusal ?? "nil")")
        let oversizedDescriptor = ReleaseDescriptor(json: oversizedDescriptorJSON)!
        let oversizedURL = ReleaseArtifactStore.artifactURL(base: updateDirectory.standardizedFileURL,
                                                            descriptor: oversizedDescriptor)
        try FileManager.default.createDirectory(at: oversizedURL.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try (artifact + Data(repeating: 0x7f, count: 100)).write(to: oversizedURL)
        var capturedOversizedBytes: Data?
        updateStore.installVerifiedArtifact = { file, _ in
            capturedOversizedBytes = try? Data(contentsOf: file)
            return "oversizedbuild"
        }
        await updateStore.installOfferedRelease()
        require(capturedOversizedBytes == artifact,
                "a partial file larger than the signed size must be discarded, not appended to")
        require(!FixtureProtocol.artifactRangeHeaders().contains(where: { $0.hasPrefix("bytes=") }),
                "an oversized fragment must not be used as a resume offset: \(FixtureProtocol.artifactRangeHeaders())")
        FixtureProtocol.clearRelease()

        // ── The owner's publisher-key front door ───────────────────────────────────────────
        // A separate store and connection, exercised on its own. Every key below is generated here
        // and thrown away; no real key is trusted.
        let trustDirectory = temporary.appendingPathComponent("Trust State")
        let trustStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await trustStore.connect(baseURL: URL(string: "http://127.0.0.1:45691")!,
                                 dataDirectory: trustDirectory, token: "native-test-token")
        trustStore.currentAppBuild = "currentbuild"

        let ownKey = Curve25519.Signing.PrivateKey()
        let ownFingerprint = releaseSha256(ownKey.publicKey.rawRepresentation)
        let trustIdentity = releaseIdentityJSON(publicKey: ownKey.publicKey.rawRepresentation,
                                                fingerprint: ownFingerprint)
        FixtureProtocol.setRelease(identity: trustIdentity, body: nil, artifact: Data(),
                                   trustedPublisher: ["publisher_id": "gatewaykh",
                                                      "fingerprint": ownFingerprint])
        await trustStore.refreshRelease()
        require(trustStore.controllerReleaseIdentity?.fingerprint == ownFingerprint,
                "the controller's own publisher fingerprint must be read from its route: \(String(describing: trustStore.controllerReleaseIdentity))")
        require(trustStore.controllerReleaseIdentity?.publicKey
                    == releaseHex(ownKey.publicKey.rawRepresentation),
                "the controller's own public key must be read too")
        require(trustStore.controllerReleaseIdentity?.algorithm == "ed25519",
                "the identity must carry the algorithm the controller reports")
        require(trustStore.trustedPublisher?.installedFingerprint == ownFingerprint,
                "the installed trusted fingerprint must be displayed from the fixture: \(String(describing: trustStore.trustedPublisher))")
        require(trustStore.trustedPublisher?.publisherId == "gatewaykh",
                "the installed publisher id must be displayed")

        // Changing it posts the exact key that was typed, then refreshes the reported state.
        let typedKey = Curve25519.Signing.PrivateKey()
        let typedHex = releaseHex(typedKey.publicKey.rawRepresentation)
        let typedFingerprint = releaseSha256(typedKey.publicKey.rawRepresentation)
        let postsBefore = FixtureProtocol.releaseTrustRequests().count
        let changed = await trustStore.changePublisherKey(publicKey: typedHex,
                                                          publisherId: "gatewaykh-two")
        require(changed, "a valid publisher-key change must report success")
        let trustPosts = FixtureProtocol.releaseTrustRequests()
        require(trustPosts.count == postsBefore + 1,
                "exactly one POST /api/hub/release/trust must be sent, got \(trustPosts.count - postsBefore)")
        require(trustPosts.last?["public_key"] as? String == typedHex,
                "the key that was typed must reach the route: \(String(describing: trustPosts.last))")
        require(trustPosts.last?["publisher_id"] as? String == "gatewaykh-two",
                "the publisher id that was typed must reach the route")
        require(trustStore.trustedPublisher?.installedFingerprint == typedFingerprint,
                "a successful change must refresh the installed fingerprint: \(String(describing: trustStore.trustedPublisher))")
        require(trustStore.connectionNotice?.kind == .success,
                "a successful change must be reported through the connection notice")

        // An empty paste is refused with a clear message and never reaches the route.
        let postsBeforeEmpty = FixtureProtocol.releaseTrustRequests().count
        let emptyAccepted = await trustStore.changePublisherKey(publicKey: "   ", publisherId: "")
        require(!emptyAccepted, "an empty publisher key must be refused")
        require(FixtureProtocol.releaseTrustRequests().count == postsBeforeEmpty,
                "an empty publisher key must not send the trust route")
        require(trustStore.connectionNotice?.kind == .failure,
                "an empty publisher key must report a clear message")
        require(trustStore.connectionNotice?.text.contains("Paste") == true,
                "the refusal must say what to do: \(trustStore.connectionNotice?.text ?? "nil")")

        // A trust answer with no fingerprint is reported as no installed key, never invented.
        FixtureProtocol.setTrustedPublisher(["publisher_id": "nameless", "fingerprint": NSNull()])
        await trustStore.refreshRelease()
        require(trustStore.trustedPublisher?.installedFingerprint == nil,
                "a body with no 64-hex fingerprint must not invent one")
        trustStore.disconnect()
        FixtureProtocol.clearRelease()

        // The swap itself: staged beside the target, the bundled runtime required, the old copy
        // removed only after the landed Info.plist reads back. A scratch target, never /Applications.
        let installParent = temporary.appendingPathComponent("Install Target")
        try FileManager.default.createDirectory(at: installParent, withIntermediateDirectories: true)
        let targetApp = installParent.appendingPathComponent("Studio Hub.app")
        let stagedApp = installParent.appendingPathComponent("staged/Studio Hub.app")
        /// One staged app, with the release manifest the real build writes and, for each component
        /// named, the runtime paths that component requires. `components` is the caller's list, so a
        /// test can omit exactly one runtime and prove that component is what is checked.
        func makeFakeApp(at app: URL, build: String,
                         components: [String: [String: Any]] = [
                            "voice": [:],
                            "hub": ["runtime": "voice"],
                         ],
                         includeMusicOverlay: Bool = true) throws {
            let contents = app.appendingPathComponent("Contents")
            let resources = contents.appendingPathComponent("Resources")
            try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
            let plist = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><plist version=\"1.0\"><dict><key>StudioHubBuild</key><string>\(build)</string></dict></plist>"
            try Data(plist.utf8).write(to: contents.appendingPathComponent("Info.plist"))
            let manifest: [String: Any] = ["schema": "studiohub.standalone-release",
                                           "schema_version": 1, "version": "0.6.50",
                                           "components": components]
            try JSONSerialization.data(withJSONObject: manifest)
                .write(to: resources.appendingPathComponent("release-manifest.json"))
            // Every declared component owns its worker entry point, derived from its own name, and
            // exactly the paths its named runtime must carry — both read from the installer's own
            // table, so the fixture cannot drift from what the installer checks.
            for name in components.keys {
                let worker = resources.appendingPathComponent("\(name)_worker.py")
                try Data("# worker\n".utf8).write(to: worker)
            }
            for (_, record) in components {
                let runtime = (record["runtime"] as? String) ?? "voice"
                guard let paths = ReleaseInstaller.runtimePaths[runtime] else { continue }
                for relative in paths {
                    // The Music overlay is created only when the caller asked for it, so one test
                    // can leave it out.
                    if !includeMusicOverlay && relative.contains("runtimes/music") { continue }
                    let file = app.appendingPathComponent(relative)
                    try FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
                                                            withIntermediateDirectories: true)
                    try Data("#!/bin/sh\n".utf8).write(to: file)
                    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
                }
            }
        }
        try makeFakeApp(at: targetApp, build: "currentbuild")
        try makeFakeApp(at: stagedApp, build: "newbuild")
        let installer = ReleaseInstaller(targetApp: targetApp, attach: { _, _ in }, detach: { _ in })
        let swapDescriptor = ReleaseDescriptor(json: descriptor)!
        let landed = try installer.swapStagedApp(staged: stagedApp, descriptor: swapDescriptor)
        require(landed == "newbuild", "the swap must report the build that actually landed")
        require(!FileManager.default.fileExists(atPath: stagedApp.path),
                "the staged app must be moved into place, not left behind")
        let landedPlist = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: targetApp.appendingPathComponent("Contents/Info.plist")),
            options: [], format: nil) as? [String: Any]
        require(landedPlist?["StudioHubBuild"] as? String == "newbuild",
                "the target must hold the new build after the swap")

        // An app whose manifest declares a runtime it does not carry is refused before anything is
        // moved. Each modality is checked, not just Voice: a release missing the Image runtime used
        // to install and look healthy until the Image worker failed.
        let targetBefore = try Data(contentsOf: targetApp.appendingPathComponent("Contents/Info.plist"))
        let allComponents: [String: [String: Any]] = [
            "voice": [:],
            "hub": ["runtime": "voice"],
            "image": ["runtime": "image"],
            "music": ["runtime": "voice+music-overlay"],
            "chat": ["runtime": "voice"],
            "subtitle": ["runtime": "voice"],
        ]
        for missing in ["voice", "image", "music", "chat", "subtitle"] {
            let incomplete = installParent.appendingPathComponent("missing-\(missing)/Studio Hub.app")
            try makeFakeApp(at: incomplete, build: "newbuild", components: allComponents)
            // Remove the component's own worker entry point, which its release manifest declares.
            try FileManager.default.removeItem(
                at: incomplete.appendingPathComponent("Contents/Resources/\(missing)_worker.py"))
            do {
                _ = try installer.swapStagedApp(staged: incomplete, descriptor: swapDescriptor)
                fatalError("an app whose release manifest declares \(missing) but does not carry its worker must be refused")
            } catch {
                require(error.localizedDescription.contains("\(missing)_worker.py"),
                        "the refusal must name the missing file, got: \(error.localizedDescription)")
                let targetAfter = try Data(contentsOf: targetApp.appendingPathComponent("Contents/Info.plist"))
                require(targetBefore == targetAfter, "a refused staged app must leave the installed app untouched")
            }
        }
        // The shared base runtime every worker rides on, and the Music overlay the Hub's own
        // `_music_spec` requires before it will start that worker.
        for (label, relative) in [("voice runtime", "Contents/Resources/runtimes/voice"),
                                  ("music overlay", "Contents/Resources/runtimes/music")] {
            let incomplete = installParent.appendingPathComponent("missing-\(label)/Studio Hub.app")
            try makeFakeApp(at: incomplete, build: "newbuild", components: allComponents)
            try FileManager.default.removeItem(at: incomplete.appendingPathComponent(relative))
            do {
                _ = try installer.swapStagedApp(staged: incomplete, descriptor: swapDescriptor)
                fatalError("an app with no \(label) must be refused")
            } catch {
                require(error.localizedDescription.contains(relative),
                        "the refusal must name the missing path, got: \(error.localizedDescription)")
            }
        }
        // An app with no release manifest at all cannot be checked, so it is refused with a reason
        // rather than installed on the strength of one hardcoded path.
        let noManifest = installParent.appendingPathComponent("no-manifest/Studio Hub.app")
        try makeFakeApp(at: noManifest, build: "newbuild")
        try? FileManager.default.removeItem(at: noManifest.appendingPathComponent(ReleaseInstaller.releaseManifest))
        do {
            _ = try installer.swapStagedApp(staged: noManifest, descriptor: swapDescriptor)
            fatalError("an app with no release manifest must be refused")
        } catch {
            require(error.localizedDescription.contains("release-manifest.json"),
                    "the refusal must name the missing manifest, got: \(error.localizedDescription)")
        }
        // A component whose runtime name this installer does not know is refused by name, naming
        // both files to update.
        let unknownRuntime = installParent.appendingPathComponent("unknown-runtime/Studio Hub.app")
        try makeFakeApp(at: unknownRuntime, build: "newbuild",
                        components: ["voice": [:], "hologram": ["runtime": "holo-render"]])
        do {
            _ = try installer.swapStagedApp(staged: unknownRuntime, descriptor: swapDescriptor)
            fatalError("a manifest naming an unknown runtime must be refused")
        } catch {
            let text = error.localizedDescription
            require(text.contains("holo-render") && text.contains("hologram"),
                    "the refusal must name the component and the runtime, got: \(text)")
            require(text.contains("native/UpdateClient.swift"),
                    "the refusal must name the file to update, got: \(text)")
        }
        updateStore.disconnect()

        // ── Surviving an unattended Mac: login agent, re-join cadence, start ladder ────────────
        // Building the agent is a pure function of a path, so every assertion below runs against a
        // throwaway directory and never touches this machine's real LaunchAgents.
        let agentRoot = temporary.appendingPathComponent("Agent Fixture")
        let appRoot = agentRoot.appendingPathComponent("Applications")
        let appBundle = appRoot.appendingPathComponent("Studio Hub.app")
        let agentExecutable = appBundle.appendingPathComponent(LaunchAgent.executableRelativePath)
        try FileManager.default.createDirectory(at: agentExecutable.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try Data("#!/bin/sh\n".utf8).write(to: agentExecutable)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: agentExecutable.path)
        let agentPlist = agentRoot.appendingPathComponent("LaunchAgents/local.studiohub.standalone.plist")

        let decision = LaunchAgent.installDecision(appBundle: appBundle, applicationsRoot: appRoot,
                                                   executableExists: { _ in true })
        require(decision == .install(executablePath: agentExecutable.path),
                "an app in the applications root with its executable must install, got: \(decision)")
        let written = try LaunchAgent.write(appBundle: appBundle, to: agentPlist,
                                            applicationsRoot: appRoot, executableExists: { _ in true })
        require(written == agentExecutable.path, "the agent must supervise the app's own executable")

        // The written plist, read back: the executable and not `open`, run at load so it covers
        // login, and `KeepAlive.SuccessfulExit == false` so a crash (non-zero exit) relaunches and
        // a deliberate Quit (exit 0) does not.
        let agentValues = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: agentPlist), options: [], format: nil) as? [String: Any]
        require(agentValues?["Label"] as? String == "local.studiohub.standalone", "the agent label")
        let program = agentValues?["ProgramArguments"] as? [String]
        require(program == [agentExecutable.path],
                "the agent must supervise the app executable, never open: \(String(describing: program))")
        require(program?.contains("/usr/bin/open") == false,
                "supervising open tracks a process that exits immediately, so KeepAlive could never work")
        require(agentValues?["RunAtLoad"] as? Bool == true,
                "RunAtLoad must be true so the agent also covers start at login")
        let keepAlive = agentValues?["KeepAlive"] as? [String: Any]
        require(keepAlive?["SuccessfulExit"] as? Bool == false,
                "KeepAlive must relaunch after a crash and not after a clean quit, got: \(String(describing: keepAlive))")
        // launchd starts the executable directly, so the AppleEvent that marked a login item is
        // absent. The environment marker is what keeps "Start services at login" honoured.
        let environment = agentValues?["EnvironmentVariables"] as? [String: Any]
        require(environment?[LaunchAgent.launchedAtLoginEnvironmentKey] as? String == "1",
                "the agent must mark its launch as a login launch, got: \(String(describing: environment))")
        let agentPermissions = try FileManager.default.attributesOfItem(atPath: agentPlist.path)[.posixPermissions] as? NSNumber
        require(agentPermissions?.intValue == 0o600,
                "the agent plist must be private, got: \(String(describing: agentPermissions))")

        // The toggle reads the plist alone: launchd loads the agent at login, so a correctly
        // installed agent is deliberately not loaded while the app runs. `status` makes no
        // launchctl call at all.
        require(LaunchAgent.status(at: agentPlist) == .enabled,
                "a present agent plist must read as on without asking launchd")
        try LaunchAgent.remove(at: agentPlist)
        require(!LaunchAgent.isInstalled(at: agentPlist), "removing the agent must remove the plist")
        require(LaunchAgent.status(at: agentPlist) == .disabled,
                "an agent that is not on disk must read as off")

        // A dev build outside /Applications is refused rather than writing an agent whose program
        // will not exist at login, and nothing is left behind.
        let devBundle = agentRoot.appendingPathComponent("Builds/Studio Hub.app")
        let outsideRefusal = LaunchAgent.installDecision(appBundle: devBundle, applicationsRoot: appRoot,
                                                         executableExists: { _ in true })
        if case .install = outsideRefusal { fatalError("an app outside the applications root must be refused") }
        do {
            _ = try LaunchAgent.write(appBundle: devBundle, to: agentPlist,
                                      applicationsRoot: appRoot, executableExists: { _ in true })
            fatalError("the refusal must not write an agent")
        } catch {}
        require(!FileManager.default.fileExists(atPath: agentPlist.path),
                "a refused install must leave no agent behind")
        // And an app in /Applications whose own executable is missing is refused too.
        let missing = LaunchAgent.installDecision(appBundle: appBundle, applicationsRoot: appRoot,
                                                  executableExists: { _ in false })
        if case .install = missing { fatalError("a missing executable must be refused") }

        // The toggle's plan: enabling writes the plist and must NEVER bootstrap, because the plist
        // sets RunAtLoad and launchd would start a second copy of the running app. Disabling boots
        // the loaded job out first so it takes effect immediately, then removes the plist. The
        // side effects are spies plus the real writer, so no launchctl process is ever started.
        var wroteCount = 0
        var bootoutCount = 0
        var removedCount = 0
        var bootstrapCount = 0
        func togglePlan() -> LoginAgentOperations {
            LoginAgentOperations(
                write: {
                    wroteCount += 1
                    _ = try LaunchAgent.write(appBundle: appBundle, to: agentPlist,
                                              applicationsRoot: appRoot, executableExists: { _ in true })
                },
                bootout: { bootoutCount += 1 },
                remove: { removedCount += 1; try LaunchAgent.remove(at: agentPlist) },
                bootstrap: { bootstrapCount += 1 })
        }
        try LoginAgent.apply(enabled: true, using: togglePlan())
        require(wroteCount == 1 && LaunchAgent.isInstalled(at: agentPlist)
                && bootoutCount == 0 && removedCount == 0 && bootstrapCount == 0,
                "enabling must write the plist and must not bootstrap while the app is running")
        require(LaunchAgent.status(at: agentPlist) == .enabled, "the written plist must read as on")
        try LoginAgent.apply(enabled: false, using: togglePlan())
        require(bootoutCount == 1 && removedCount == 1 && !LaunchAgent.isInstalled(at: agentPlist)
                && bootstrapCount == 0,
                "disabling must boot the job out and remove the plist, still without bootstrapping")
        require(LaunchAgent.status(at: agentPlist) == .disabled, "a removed agent must read as off")

        // A redundant launch presents the running app and leaves with status 0; no alert, and no
        // failure banner. The present/exit actions are injected so the test process does not exit.
        require(ExistingInstance.decide(lockHeldByAnother: true) == .presentAndExit,
                "a held lock must present the running app and exit")
        require(ExistingInstance.decide(lockHeldByAnother: false) == .continueStarting,
                "the only instance must keep starting")
        require(ExistingInstance.redundantLaunchExitCode == 0,
                "a redundant launch must exit 0 so KeepAlive never relaunches it")
        var presentedCount = 0
        var exitedCount = 0
        ExistingInstance.handle(.presentAndExit,
                                present: { presentedCount += 1 },
                                exitQuietly: { exitedCount += 1 })
        require(presentedCount == 1 && exitedCount == 1,
                "a redundant launch must present the running app and leave with status 0")
        presentedCount = 0; exitedCount = 0
        ExistingInstance.handle(.continueStarting,
                                present: { presentedCount += 1 },
                                exitQuietly: { exitedCount += 1 })
        require(presentedCount == 0 && exitedCount == 0,
                "the only instance must neither present another app nor exit")
        // A held lock is specifically "already held", which is what makes a second launch exit
        // quietly rather than read as a failure to open the app.
        let lockDirectory = temporary.appendingPathComponent("instance-lock")
        let heldLock = try InstanceLock(directory: lockDirectory)
        do {
            _ = try InstanceLock(directory: lockDirectory)
            fatalError("a second lock must be refused")
        } catch let error as InstanceLockError {
            require(error == .alreadyHeld, "a held lock must be reported as already held, got: \(error)")
        }
        withExtendedLifetime(heldLock) {}

        // The one-time move off the old SMAppService login item. Pure and injected, so no branch
        // here touches the real system; the app target supplies the real call in one place.
        var unregisterCalls = 0
        var statusCalls = 0
        func oldStatus(_ value: LoginMigration.OldStatus) -> () -> LoginMigration.OldStatus {
            { statusCalls += 1; return value }
        }
        let removed = LoginMigration.run(alreadyRecorded: false, oldStatus: oldStatus(.enabled),
                                         unregister: { unregisterCalls += 1 })
        require(removed == .removed && unregisterCalls == 1 && statusCalls == 1,
                "an enabled old login item must be unregistered exactly once, got: \(removed)")
        require(LoginMigration.notice(for: removed) == nil, "a clean removal needs no notice")
        // requiresApproval is still a live old registration.
        unregisterCalls = 0
        let approval = LoginMigration.run(alreadyRecorded: false, oldStatus: oldStatus(.requiresApproval),
                                          unregister: { unregisterCalls += 1 })
        require(approval == .removed && unregisterCalls == 1,
                "an old registration needing approval must also be removed, got: \(approval)")
        // A second launch has the recorded flag: it must not read or unregister again.
        unregisterCalls = 0; statusCalls = 0
        let secondLaunch = LoginMigration.run(alreadyRecorded: true, oldStatus: oldStatus(.enabled),
                                              unregister: { unregisterCalls += 1 })
        require(secondLaunch == .alreadyDone && unregisterCalls == 0 && statusCalls == 0,
                "the migration must run once and never read or unregister again, got: \(secondLaunch)")
        // No old registration: nothing is called and there is nothing to report.
        unregisterCalls = 0; statusCalls = 0
        for absent in [LoginMigration.OldStatus.notRegistered, .notFound] {
            let skipped = LoginMigration.run(alreadyRecorded: false, oldStatus: oldStatus(absent),
                                             unregister: { unregisterCalls += 1 })
            require(skipped == .nothingToRemove && unregisterCalls == 0 && statusCalls > 0,
                    "an absent old registration must not call unregister, got: \(skipped)")
        }
        // A failing unregister is returned, not thrown, so startup continues; the notice names the
        // manual fix.
        unregisterCalls = 0
        let throwingUnregister: () throws -> Void = {
            unregisterCalls += 1
            throw ShellFailure(message: "not permitted")
        }
        let failed = LoginMigration.run(alreadyRecorded: false, oldStatus: oldStatus(.enabled),
                                        unregister: throwingUnregister)
        require(failed == .failed("not permitted") && unregisterCalls == 1,
                "a failed unregister must return a failure, got: \(failed)")
        let migrationNotice = LoginMigration.notice(for: failed)
        require(migrationNotice?.contains("Login Items") == true
                && migrationNotice?.contains("not permitted") == true,
                "a failed migration must say where to remove the old entry, got: \(String(describing: migrationNotice))")
        require(LoginMigration.notice(for: .nothingToRemove) == nil
                && LoginMigration.notice(for: .alreadyDone) == nil
                && LoginMigration.notice(for: .removed) == nil,
                "only a failure needs a notice")
        // ServiceManagement unavailable is also reported, without calling unregister.
        unregisterCalls = 0
        let unavailable = LoginMigration.run(alreadyRecorded: false, oldStatus: oldStatus(.unavailable),
                                             unregister: { unregisterCalls += 1 })
        require(unavailable == .failed("ServiceManagement is unavailable") && unregisterCalls == 0,
                "an unavailable ServiceManagement must not call unregister and must be reported, got: \(unavailable)")
        require(LoginMigration.notice(for: unavailable)?.contains("Login Items") == true,
                "an unavailable ServiceManagement must still tell the operator where to look")

        // The node re-join schedule: exactly 15, 30, 60, then five minutes.
        require(RejoinBackoff.delay(afterAttempt: 0) == 15
                && RejoinBackoff.delay(afterAttempt: 1) == 30
                && RejoinBackoff.delay(afterAttempt: 2) == 60
                && RejoinBackoff.delay(afterAttempt: 3) == 300
                && RejoinBackoff.delay(afterAttempt: 99) == 300,
                "the re-join cadence must be 15, 30, 60, then 300 seconds")
        require(RejoinRecovery.nextAction(attempt: 0, isNode: true, hasMembership: true, joined: false) == .wait(15),
                "a failing node must retry after 15 seconds")
        require(RejoinRecovery.nextAction(attempt: 3, isNode: true, hasMembership: true, joined: false) == .wait(300),
                "a failing node must settle on the five-minute cadence")
        require(RejoinRecovery.nextAction(attempt: 0, isNode: false, hasMembership: true, joined: false) == .stop,
                "a Mac that is not a node must not re-join")
        require(RejoinRecovery.nextAction(attempt: 0, isNode: true, hasMembership: false, joined: false) == .stop,
                "a node with no stored membership must not re-join")
        require(RejoinRecovery.nextAction(attempt: 0, isNode: true, hasMembership: true, joined: true) == .stop,
                "a successful re-join must stop the schedule")
        require(RejoinRecovery.hasRejoinableMembership(address: "192.168.1.21:8080", token: "t"),
                "a token with an address is a re-joinable membership")
        require(!RejoinRecovery.hasRejoinableMembership(address: "", token: "t")
                && !RejoinRecovery.hasRejoinableMembership(address: "  ", token: "t")
                && !RejoinRecovery.hasRejoinableMembership(address: "192.168.1.21:8080", token: nil)
                && !RejoinRecovery.hasRejoinableMembership(address: "192.168.1.21:8080", token: ""),
                "a membership with no address or no token must not be retried")

        // The service-start ladder: 1, 3, 10, 60, 120, then every five minutes. A manual start or
        // restart resets it to the first rung, which is what `StudioController.start()` does.
        require(StartupRecovery.defaultMaxAutomaticAttempts == 5
                && StartupRecovery.defaultDelays == [1, 3, 10, 60, 120],
                "the default fast ladder must be 1, 3, 10, 60, 120")
        require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: true, ownerRunning: false,
                                           userStopped: false) == .wait(1), "the ladder starts at one second")
        require(StartupRecovery.nextAction(attempt: 4, libraryAvailable: true, ownerRunning: false,
                                           userStopped: false) == .wait(120), "the last fast rung is two minutes")
        require(StartupRecovery.nextAction(attempt: 5, libraryAvailable: true, ownerRunning: false,
                                           userStopped: false) == .wait(300), "past the ladder the retry is five minutes")
        require(StartupRecovery.nextAction(attempt: 50, libraryAvailable: true, ownerRunning: false,
                                           userStopped: false) == .wait(300), "the slow retry must not drift")
        require(StartupRecovery.nextAction(attempt: 0, libraryAvailable: true, ownerRunning: false,
                                           userStopped: false, maxAttempts: 0, delays: [1])
                == .stop("Automatic recovery stopped after repeated service failures"),
                "zero attempts is the one explicit off switch")
        var ladder = ServiceStartLadder()
        require((0...5).map { ladder.delay(forAttempt: $0) } == [1, 3, 10, 60, 120, 300],
                "the ladder delays must be 1, 3, 10, 60, 120 then 300")
        require(ladder.isFastPhase, "the first attempt is in the fast phase")
        for _ in 0..<5 { ladder.advance() }
        require(!ladder.isFastPhase, "the sixth attempt is past the fast ladder")
        ladder.reset()
        require(ladder.attempt == 0 && ladder.delay() == 1,
                "a manual attempt must reset the ladder to its first rung")
        let customLadder = ServiceStartLadder(maxFastAttempts: 2, fastDelays: [2, 4])
        require(customLadder.delay(forAttempt: 0) == 2 && customLadder.delay(forAttempt: 1) == 4
                && customLadder.delay(forAttempt: 2) == 300,
                "a configured ladder must still fall through to the five-minute cadence")

        // A stale Fleet token must not spin silently: three consecutive 401 answers earn exactly
        // one notice, carried by the existing connection banner. The waits are injected so the
        // test does not sit on the real 15/30/60-second schedule; the cadence itself is asserted
        // above.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.setJoinReject(401)
        let authStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await authStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                dataDirectory: temporary.appendingPathComponent("auth-rejoin"),
                                token: "native-test-token")
        authStore.rejoinBackoffSeconds = [0.02]
        await authStore.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "stale-fleet-token",
            boundHosts: LocalAddresses.current().map(\.address)))
        let announced = await eventually(timeout: 3) {
            authStore.connectionNotice?.kind == .failure
                && authStore.connectionNotice?.text.contains("refused this Mac's Fleet token") == true
        }
        require(announced,
                "three 401 answers must be reported, got: \(String(describing: authStore.connectionNotice))")
        require(FixtureProtocol.fleetRequests().filter { $0 == "join" }.count >= 3,
                "the node must have tried at least three times: \(FixtureProtocol.fleetRequests())")
        let refused = await eventually(timeout: 2) { authStore.lastJoinAuthStatus == 401 }
        require(refused, "the refusal status must be recorded")
        authStore.disconnect()
        FixtureProtocol.setJoinReject(nil)
        FixtureProtocol.setCredentialOrigin("minted")

        // The address a node reports for its controller has to be one the Hub will store, and the
        // Hub stores a literal private IPv4 because on a node that value is what the Hub itself
        // dials. A literal is passed through; anything the Hub would refuse is omitted rather than
        // sent, so a join by name still succeeds and only the group's name is lost.
        require(NativeWorkspaceStore.literalControllerAddress(URL(string: "http://192.168.1.21:8080")!)
                    == "http://192.168.1.21:8080",
                "a private literal must be reported unchanged")
        require(NativeWorkspaceStore.literalControllerAddress(URL(string: "http://100.101.102.103:47873")!)
                    == "http://100.101.102.103:47873",
                "a Tailscale literal must be reported unchanged")
        require(NativeWorkspaceStore.literalControllerAddress(URL(string: "http://8.8.8.8:47873")!) == nil,
                "a public address must never be recorded as this Mac's controller")
        // A name is a reasonable thing to type - MagicDNS is the natural address on a tailnet - and
        // `privateHTTPAddress` has already resolved it once to check it is private. Resolving it
        // again here is what lets a join by name still record a controller the Hub can dial.
        require(NativeWorkspaceStore.literalControllerAddress(URL(string: "http://localhost:8080")!)
                    == "http://127.0.0.1:8080",
                "a name that resolves to a private address must be recorded as that address")
        require(NativeWorkspaceStore.literalControllerAddress(URL(string: "http://192.168.1.21")!) == nil,
                "an address with no port cannot name a Hub, so it must not be reported")

        print("Native API contract checks passed")
    }
}
