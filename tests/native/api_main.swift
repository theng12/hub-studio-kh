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

/// Read a rendered duration back into seconds, so the formatter can be checked as a property and
/// not only as a table. Returns nil for anything the formatter should never print.
func clockSeconds(_ rendered: String) -> Double? {
    let parts = rendered.split(separator: " ")
    guard parts.count >= 2, parts.count % 2 == 0 else { return nil }
    var total = 0.0
    for pair in stride(from: 0, to: parts.count, by: 2) {
        guard let value = Double(parts[pair]) else { return nil }
        switch parts[pair + 1] {
        case "h": total += value * 3600
        case "min": total += value * 60
        case "s": total += value
        default: return nil
        }
    }
    return total
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
    /// How many held voice-library reads were torn down before the fixture answered them. A read
    /// the fixture *does* answer also stops loading, which is why the holding flag is cleared
    /// before that answer is delivered.
    static var voiceListCancellations = 0
    static var holdNextVoiceList = false
    static var heldVoiceListStarted = false
    static var heldVoiceListHolding = false
    /// When set, `GET /studio/voice/api/voices` answers this status instead of the list, so a real
    /// read failure can be told apart from a cancelled one.
    static var voiceListFailureStatus: Int?
    static let voiceListStarted = DispatchSemaphore(value: 0)
    /// One semaphore **per hold**, not one for the process.
    ///
    /// It was `static let`, so a `signal()` arriving after its own hold had ended stayed in the
    /// semaphore's count, and the next hold's `wait` would consume it and return instantly —
    /// releasing the read before a test could cancel it, with the cancellation then never counted.
    /// That is a real race and it is removed.
    ///
    /// **It is not claimed as the cause of SH-15(a).** That flake was never reproduced: 60 runs of
    /// this suite with the shared semaphore restored, and 40 with it removed, all passed. Two
    /// timing races were found in the test this assertion lives in and both are fixed; the original
    /// occurrence remains unattributed, and the honest record of that is in
    /// `docs/cross-project-open-items.md`.
    static var releaseVoiceList = DispatchSemaphore(value: 0)
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
    static var retryPaths: [String] = []
    static var overviewFails = false
    /// Change fixture state from async test code, where NSLock's lock/unlock are unavailable.
    static func mutate(_ change: () -> Void) { lock.lock(); change(); lock.unlock() }
    /// "off", "settled" (a failed batch with nothing waiting) or "retried" (queued again).
    static var errorBatchMode = "off"
    static var voiceDownloadsIdle = false
    /// What `GET /api/hub/model-downloads/hub-download-1` answers: normal, a transient 500, the
    /// Hub having forgotten the run (404), or a failed run with the downloader's own last words.
    static var hubDownloadMode = "normal"
    static var hubDownloadReads = 0
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
    /// **This Mac's own node credential**, as the real controller issues it in the join answer: a
    /// value for this one Mac, returned once, and presented on every later call home (A8).
    static var issuedNodeCredential = "node-credential-abc123"
    /// What the last join presented, and what it carried in the body, so the suite can prove the
    /// credential is on the wire rather than only in a property.
    static var lastJoinToken = ""
    static var lastJoinBodyCredential: String?
    /// The credential on the last authenticated controller read, and on the last removal request.
    static var lastResourcesToken = ""
    static var lastRemovalToken = ""
    /// When set, the join answers 403 with this sentence — the removed-Mac refusal.
    static var joinRemovalDetail: String?
    /// How the controller answers this Mac removing itself. Nil is the real answer for a Mac holding
    /// its own credential: **200**. A fixture that always answered 200 is why a 100%-broken "Leave
    /// this group" survived this suite, so the status is a switch and the default is not a stub.
    static var selfRemovalStatus: Int?
    static var selfRemovalDetail: String?
    /// What the *authenticated* controller read answers. 401/403 is a controller that will not
    /// dispatch to this Mac, which is a different state from a Mac that is switched off.
    static var resourcesStatus: Int?
    /// The rotation/handover answer, and the bodies those routes were sent.
    static var fleetSyncAnswer = Data(#"{"ok":true,"rotated":true,"token":"new-fleet-token-abcdefghijklmnop","total":2,"verified":1,"manual":1,"pending":0,"machines":{"studio-2":{"ok":true,"status":"verified","detail":"saved and verified"},"studio-3":{"ok":false,"status":"manual","detail":"peer rejected the current credential"}}}"#.utf8)
    static var fleetSyncBodies: [[String: Any]] = []
    static var readmitCalls: [String] = []
    /// The controller this Mac handed over to, as `GET /api/hub/controller` then reports it.
    static var handedOverTo: String?
    /// When set, a join of an existing id that presents no credential — or only the group Fleet
    /// token — is refused 400 exactly as the real controller refuses it (H12). Off by default
    /// because the fixture cannot tell a first join from a re-join, and a first join is legal.
    static var joinRequiresCredential = false
    static func setJoinRequiresCredential(_ value: Bool) {
        lock.lock(); defer { lock.unlock() }; joinRequiresCredential = value
    }
    /// When set, a join that presents exactly this credential is refused 401 — the credential a
    /// re-admitted Mac still holds from before, which the controller no longer knows. Any other
    /// join succeeds, which is what makes the one retry with the group token observable.
    static var staleJoinCredential: String?
    static func setStaleJoinCredential(_ value: String?) {
        lock.lock(); defer { lock.unlock() }; staleJoinCredential = value
    }
    static func setSelfRemoval(_ status: Int?, detail: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        selfRemovalStatus = status
        selfRemovalDetail = detail
    }
    /// What `GET /api/hub/fleet/nodes/<id>/status` answers: a status and a JSON body. Nil is the
    /// real answer for a Mac that is still listed (200, member). Each call is logged as
    /// `<credential>|<query>` so the suite can see what went on the wire.
    static var statusAnswer: (Int, String)?
    static var statusCallsList: [String] = []
    static func setStatus(_ status: Int?, body: String = "") {
        lock.lock(); defer { lock.unlock() }
        statusAnswer = status.map { ($0, body) }
    }
    static func statusCalls() -> [String] { lock.lock(); defer { lock.unlock() }; return statusCallsList }
    static func resetStatusCalls() { lock.lock(); defer { lock.unlock() }; statusCallsList = [] }
    static func setJoinRemoval(_ detail: String?) {
        lock.lock(); defer { lock.unlock() }; joinRemovalDetail = detail
    }
    static func setResourcesStatus(_ status: Int?) {
        lock.lock(); defer { lock.unlock() }; resourcesStatus = status
    }
    static func setFleetSyncAnswer(_ data: Data) {
        lock.lock(); defer { lock.unlock() }; fleetSyncAnswer = data
    }
    static func setHandedOver(to value: String?) {
        lock.lock(); defer { lock.unlock() }; handedOverTo = value
    }
    static func joinTokens() -> [String] { lock.lock(); defer { lock.unlock() }; return joinTokensList }
    static var joinTokensList: [String] = []
    static func removalTokens() -> [String] { lock.lock(); defer { lock.unlock() }; return removalTokensList }
    static var removalTokensList: [String] = []
    static func resourcesToken() -> String { lock.lock(); defer { lock.unlock() }; return lastResourcesToken }
    static func syncBodies() -> [[String: Any]] { lock.lock(); defer { lock.unlock() }; return fleetSyncBodies }
    static func readmits() -> [String] { lock.lock(); defer { lock.unlock() }; return readmitCalls }
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
    /// Lite Mode (`/api/hub/workstation`): the settings the Hub holds, and the bodies the app posted.
    static var workstationSettings: [String: Any] = [
        "enabled": false, "idle_only": false, "idle_minutes": 3, "step_aside": false, "resume_after_minutes": 2,
        "start_free_gb": NSNull(), "quiet_hours": [String](), "hide_menu_bar_icon": false]
    static var workstationPosts: [[String: Any]] = []
    static func workstationAnswer() -> Data {
        lock.lock(); defer { lock.unlock() }
        let enabled = workstationSettings["enabled"] as? Bool == true
        var report = workstationSettings
        report["state"] = enabled ? "standing_by" : "off"
        report["reason"] = enabled ? "someone is using this Mac" : NSNull()
        report["jobs_today"] = 2; report["yields_today"] = 1
        return try! JSONSerialization.data(withJSONObject: ["ok": true, "workstation": report])
    }
    static var storagePreviewRejects = false
    static var modelImportBody: [String: Any]?
    static var modelHealthBody: [String: Any]?
    static var modelVerifyRejects = false
    static let png = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")!
    /// One frame of 16-bit mono 8 kHz PCM: the smallest thing that is still a WAV.
    /// A minimal ISO base media file, for the Video lane's own artifact checks: bytes 4-8 are
    /// `ftyp`, which is what the native reader verifies exactly as it verifies a PNG signature.
    static let mp4: Data = {
        var body = Data([0x00, 0x00, 0x00, 0x18])
        body.append(Data("ftypmp42".utf8))
        body.append(Data(repeating: 0, count: 64))
        return body
    }()

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
    /// The local Chat completion: how many were asked for, the last body, and a hold so the
    /// transcript can be read while the reply is still in flight. The failure status exists so a
    /// failed attempt can be told from a silent one.
    static var chatCalls = 0
    static var lastChatBody: [String: Any]?
    static var holdNextChat = false
    static var heldChatStarted = false
    static var chatFailureStatus: Int?
    static let releaseChat = DispatchSemaphore(value: 0)
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
    /// The delta stage job the Hub reports: each `POST`/`GET /api/hub/release/stage` answers the next
    /// scripted status (the last one repeats), and the count says whether the app asked at all.
    /// `nil` means the Hub predates the route (404).
    static var releaseStageScript: [[String: Any]]?
    static var releaseStageRequests = 0
    /// What the app asked of `POST /api/hub/update/drain` (`want`), and what the Hub answers: `draining`,
    /// or `waiting` when another Mac in the group holds the update slot.
    static var updateDrainWants: [Bool] = []
    static var updateDrainReply = "draining"
    static func resetUpdateDrain(reply: String = "draining") {
        lock.lock(); defer { lock.unlock() }; updateDrainWants = []; updateDrainReply = reply
    }
    static func drainWants() -> [Bool] { lock.lock(); defer { lock.unlock() }; return updateDrainWants }
    /// What the app sent to `POST /api/hub/startup/sync`, and what the Hub hands back (the requests the
    /// fleet queued, and the role the Hub is in). Every call is recorded; the answer is scripted.
    static var startupSyncBodies: [[String: Any]] = []
    static var startupSyncAnswer: [String: Any] = ["requests": [], "role": "standalone"]
    static func resetStartupSync(answer: [String: Any] = ["requests": [], "role": "standalone"]) {
        lock.lock(); defer { lock.unlock() }; startupSyncBodies = []; startupSyncAnswer = answer
    }
    static func startupSyncs() -> [[String: Any]] { lock.lock(); defer { lock.unlock() }; return startupSyncBodies }

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
    static func joinBodyCredential() -> String? { lock.lock(); defer { lock.unlock() }; return lastJoinBodyCredential }
    static func adoptPayload() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return adoptBody }
    /// The controller's own removal refusal, verbatim: the sentence the app must quote and take the
    /// date out of. Kept as one value so a test cannot assert a wording the Hub does not use.
    static let removalSentence = "this controller removed this Mac on 2026-09-28; re-admit it from Connections to join again"
    static func storageLimitsBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return storageLimits }
    static func setStoragePreviewRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; storagePreviewRejects = value }
    static func importedFolder() -> String? { lock.lock(); defer { lock.unlock() }; return modelImportBody?["source_folder"] as? String }
    static func recheckBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return modelHealthBody }
    static func setModelVerifyRejects(_ value: Bool) { lock.lock(); defer { lock.unlock() }; modelVerifyRejects = value }
    static func setHoldGeneration(_ value: Bool) { lock.lock(); defer { lock.unlock() }; holdGeneration = value }
    /// Hold the next voice-library read unanswered, so a load can be cancelled mid-flight exactly
    /// the way SwiftUI's `.task(id:)` cancels its previous task when the id changes.
    static func holdVoiceList() {
        lock.lock(); defer { lock.unlock() }
        holdNextVoiceList = true
        heldVoiceListStarted = false
        // A fresh count for this hold, so no earlier signal can release it early.
        releaseVoiceList = DispatchSemaphore(value: 0)
    }
    static func voiceListHasStarted() -> Bool { lock.lock(); defer { lock.unlock() }; return heldVoiceListStarted }
    static func voiceListCancelCount() -> Int { lock.lock(); defer { lock.unlock() }; return voiceListCancellations }
    static func releaseHeldVoiceList() { releaseVoiceList.signal() }
    static func setVoiceListFailure(_ status: Int?) { lock.lock(); defer { lock.unlock() }; voiceListFailureStatus = status }
    static func memoryBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return lastMemoryBody }
    static func memoryRequest() -> (String, String) { lock.lock(); defer { lock.unlock() }; return (lastMemoryMethod, lastMemoryToken) }
    static func releases() -> Int { lock.lock(); defer { lock.unlock() }; return memoryReleaseCount }
    static func setMemoryUnavailable(_ value: Bool) { lock.lock(); defer { lock.unlock() }; memoryUnavailable = value }
    static var memoryPolicyOverride: Data?
    static var chatJobGenerationSeconds: Double? = 7.2
    static var chatReplyCompletionTokens: Int? = 10
    static var chatReplyElapsedSeconds: Double? = 7.2
    static var chatJobWithoutStatedTime = false
    /// When set, `GET /api/hub/memory/policy` answers this payload instead of the default fixture,
    /// so a machine whose Studios publish the `auto` mode with their own sentences can be read.
    static func setMemoryPolicyOverride(_ value: Data?) { lock.lock(); defer { lock.unlock() }; memoryPolicyOverride = value }
    /// The Chat worker's own stated generation time on the fixture's recorded Chat job.
    /// `nil` means "the worker stated none", which is the case a rate must never be invented for.
    static func setChatJobGeneration(_ value: Double?) { lock.lock(); defer { lock.unlock() }; chatJobGenerationSeconds = value }
    /// The tokens and stated generation time on the fixture's live Chat reply.
    static func setChatReplyUsage(tokens: Int?, elapsed: Double?) {
        lock.lock(); defer { lock.unlock() }
        chatReplyCompletionTokens = tokens
        chatReplyElapsedSeconds = elapsed
    }
    /// Whether the Hub's job list also carries a second Chat batch whose item states usage but no
    /// generation time. Off by default, so the listing the existing assertions read is unchanged.
    static func setChatJobWithoutStatedTime(_ value: Bool) {
        lock.lock(); defer { lock.unlock() }; chatJobWithoutStatedTime = value
    }
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
        releaseStageScript = nil
        releaseStageRequests = 0
    }
    static func setStageScript(_ script: [[String: Any]]?) {
        lock.lock(); defer { lock.unlock() }; releaseStageScript = script; releaseStageRequests = 0
    }
    static func stageRequests() -> Int { lock.lock(); defer { lock.unlock() }; return releaseStageRequests }
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
    /// Hold the next Chat completion unanswered, so a transcript can be read mid-flight.
    static func holdChat() {
        lock.lock(); defer { lock.unlock() }
        holdNextChat = true
        heldChatStarted = false
    }
    static func chatHasStarted() -> Bool { lock.lock(); defer { lock.unlock() }; return heldChatStarted }
    static func releaseHeldChat() { releaseChat.signal() }
    static func chatCallCount() -> Int { lock.lock(); defer { lock.unlock() }; return chatCalls }
    static func recordedChatBody() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return lastChatBody }
    static func setChatFailure(_ status: Int?) { lock.lock(); defer { lock.unlock() }; chatFailureStatus = status }
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
    private static let hubRoutedModalities: Set<String> = ["image", "voice", "transcription", "music", "chat", "video"]

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
        // **Removal and re-admission, answered by host and by credential rather than stubbed.**
        //
        // The old fixture answered a flat 200 to `DELETE /api/hub/fleet/nodes/<any id>`, which is
        // why a "Leave this group" that could never work passed this suite for as long as it did.
        // The real Hub answers three different things, so this does too:
        //   * the controller, this Mac's own id, this Mac's own node credential -> 200;
        //   * the controller, a different id -> 403 ("only the Mac that holds it");
        //   * the controller, a Mac that was already removed (a revoked credential) -> 401, and the
        //     sentence `removed this Mac on <date>` only comes back on the join;
        //   * this Mac's own Hub -> the operator removing a node from its own roster, 200.
        if path.hasPrefix("/api/hub/fleet/nodes/") {
            let remainder = String(path.dropFirst("/api/hub/fleet/nodes/".count))
            let parts = remainder.split(separator: "/").map(String.init)
            let offered = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            let isLocalHub = (request.url?.host ?? "") == "127.0.0.1"
            if method == "DELETE", parts.count == 1 {
                let nodeID = parts[0]
                Self.lock.lock()
                Self.fleetCalls.append("delete:\(nodeID)")
                // Only the controller-side removals are recorded as self-removals: a removal made
                // against this Mac's own Hub is the operator pruning his own roster.
                if !isLocalHub { Self.removalTokensList.append(offered) }
                let status = Self.selfRemovalStatus
                let detail = Self.selfRemovalDetail
                Self.lock.unlock()
                if isLocalHub {
                    // The operator's own roster: the Hub token owner may remove any node.
                    if nodeID == "studio-3" { Self.lock.lock(); Self.fleetCalls.append("leave:studio-3"); Self.lock.unlock() }
                    finish((200, "application/json", Data(#"{"ok":true}"#.utf8)))
                    return
                }
                guard nodeID == "controller-local" else {
                    finish((403, "application/json", Data(#"{"detail":"a fleet credential may remove only the Mac that holds it. Removing '\#(nodeID)' from this controller is the owner's decision: use this controller's Hub token, or let that Mac leave from its own Connections page."}"#.utf8)))
                    return
                }
                // The controller, this Mac's own id. A node credential that is no longer on the
                // roster is refused 401 before the route runs; the group Fleet token cannot remove
                // anything, which is the real answer and not something to paper over.
                if offered != FixtureProtocol.issuedNodeCredential {
                    finish((403, "application/json", Data(#"{"detail":"a fleet credential may remove only the Mac that holds it. Removing 'controller-local' from this controller is the owner's decision: use this controller's Hub token, or let that Mac leave from its own Connections page."}"#.utf8)))
                    return
                }
                if let status {
                    finish((status, "application/json", Data(#"{"detail":"\#(detail ?? "refused")"}"#.utf8)))
                    return
                }
                Self.lock.lock(); Self.fleetCalls.append("dropped-from-controller"); Self.lock.unlock()
                finish((200, "application/json", Data(#"{"ok":true,"removed":{"id":"controller-local"}}"#.utf8)))
                return
            }
            if method == "GET", parts.count == 2, parts[1] == "status" {
                Self.lock.lock()
                Self.statusCallsList.append("\(offered)|\(request.url?.query ?? "")")
                let answer = Self.statusAnswer
                Self.lock.unlock()
                let (status, body) = answer ?? (200, #"{"id":"\#(parts[0])","state":"member"}"#)
                finish((status, "application/json", Data(body.utf8)))
                return
            }
            if method == "POST", parts.count == 2, parts[1] == "readmit" {
                Self.lock.lock(); Self.readmitCalls.append(parts[0]); Self.fleetCalls.append("readmit:\(parts[0])"); Self.lock.unlock()
                finish((200, "application/json", Data(#"{"ok":true,"readmitted":{"id":"\#(parts[0])"}}"#.utf8)))
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
        // Each Studio's defaults route, reached through the Hub's studio proxy exactly like every
        // other worker call: `/studio/<modality>/api/model-defaults`.
        if path.hasPrefix("/studio/"), path.hasSuffix("/api/model-defaults") {
            let parts = path.split(separator: "/")
            if parts.count == 4 {
                answerModelDefaults(modality: String(parts[1]), method: method)
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
        case ("GET", "/api/hub/model-health"):
            // Per (machine, model): this Mac can run two models and needs one download; the joined
            // node answers its Hub but has no model it can run.
            result = (200, "application/json", Data(#"{"paused":[],"models":[{"model_id":"a","machine":"local","state":"ready"},{"model_id":"b","machine":"local","state":"ready"},{"model_id":"c","machine":"local","state":"download_required"},{"model_id":"a","machine":"studio-3","state":"download_required"},{"model_id":"b","machine":"studio-3","state":"insufficient_memory"},{"model_id":"c","machine":"studio-3","state":"unknown"}]}"#.utf8))
        case ("POST", "/api/hub/jobs/old-job/items/0/retry"):
            Self.lock.lock(); Self.retryPaths.append(path); Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"batch_id":"old-job","item_index":0,"state":"queued"}"#.utf8))
        case ("POST", "/api/hub/model-health/recheck"):
            let body = requestBody()
            Self.lock.lock()
            Self.modelHealthBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"ok":true,"studio":"voice","model":"fixture/paused"}"#.utf8))
        case ("GET", "/api/hub/workstation"):
            result = (200, "application/json", Self.workstationAnswer())
        case ("POST", "/api/hub/workstation"):
            let body = (try? JSONSerialization.jsonObject(with: requestBody())) as? [String: Any] ?? [:]
            Self.lock.lock()
            Self.workstationPosts.append(body)
            let bad = (body["idle_minutes"] as? Int).map { !(1...60).contains($0) } ?? false
            if !bad { Self.workstationSettings.merge(body) { _, new in new } }
            Self.lock.unlock()
            result = bad
                ? (400, "application/json", Data(#"{"ok":false,"code":"BAD_SETTING","detail":"idle_minutes must be a whole number of minutes from 1 to 60"}"#.utf8))
                : (200, "application/json", Self.workstationAnswer())
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
            let offered = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            Self.lock.lock()
            Self.fleetCalls.append("join")
            Self.joinBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            Self.lastJoinToken = offered
            Self.joinTokensList.append(offered)
            Self.lastJoinBodyCredential = Self.joinBody?["credential"] as? String
            let hold = Self.holdNextJoin
            if hold { Self.holdNextJoin = false; Self.heldJoinStarted = true }
            let reject = Self.joinRejectStatus
            let removal = Self.joinRemovalDetail
            let requiresCredential = Self.joinRequiresCredential
            let issued = Self.issuedNodeCredential
            let bodyCredential = Self.joinBody?["credential"] as? String
            Self.lock.unlock()
            // **A row that already holds a credential cannot be taken over with the shared token.**
            // The real controller refuses this 400 so two Macs cannot share one node id; a fixture
            // that accepted it would let the app's re-join rot into a silent takeover.
            if requiresCredential, offered != issued, bodyCredential != issued {
                finish((400, "application/json", Data(#"{"detail":"this controller already has a machine called 'studio-mini' (Studio Mini). Two Macs cannot share one node id: rename this Mac in Connections, or remove the other one first."}"#.utf8)))
                return
            }
            // A credential the controller no longer knows — a re-admitted Mac's old one — is
            // refused 401 before the route runs, exactly as the real middleware refuses it.
            if let stale = Self.staleJoinCredential, offered == stale {
                finish((401, "application/json", Data(#"{"detail":"a credential that is neither this Mac's Hub token nor the Fleet token this Mac holds. The route requires this Mac's Hub token, or a Fleet token matching the one this Mac holds."}"#.utf8)))
                return
            }
            // **A removed Mac is refused 403 with the controller's own sentence.** It is the one
            // answer the app must treat as a decision rather than a failure, so the fixture serves
            // it exactly as the Hub does — and only when a test asks for it.
            if let removal {
                finish((403, "application/json",
                        Data(#"{"detail":"\#(removal)"}"#.utf8)))
                return
            }
            if let reject {
                finish((reject, "application/json", Data(#"{"detail":"invalid fleet token"}"#.utf8)))
                return
            }
            // The credential comes back **once**, in this answer: that is the whole of A8, and a
            // fixture that answered without it would let the app's re-join rot unnoticed.
            let answer = Data(#"{"node":{"id":"studio-mini","name":"Studio Mini","address":"http://192.168.1.20:47873","role":"agent"},"credential":"\#(issued)"}"#.utf8)
            if hold {
                DispatchQueue.global().async { [self] in
                    _ = Self.releaseJoin.wait(timeout: .now() + 5)
                    finish((201, "application/json", answer))
                }
                return
            }
            result = (201, "application/json", answer)
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
        case ("GET", "/api/hub/resources"):
            // **The authenticated read behind the connection light (H11).** `/api/health` needs no
            // credential, so it answered 200 to a controller that refused this Mac — which is how
            // 52 nodes showed green while the controller called every one `unauthorized`.
            let offered = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            Self.lock.lock()
            Self.lastResourcesToken = offered
            let status = Self.resourcesStatus
            Self.lock.unlock()
            if let status {
                result = (status, "application/json",
                          Data(#"{"detail":"this Mac's fleet credential was refused"}"#.utf8))
            } else {
                result = (200, "application/json", Data(#"{"host":{"chip":"Apple M4","memory_gb":16.0},"version":"0.4.0","source":"local"}"#.utf8))
            }
        case ("GET", "/api/health"):
            result = (200, "application/json", Data(#"{"ok":true,"busy":false}"#.utf8))
        case ("POST", "/api/hub/fleet"):
            // The rotation, with the Hub token: the operator's own action on this Mac.
            let body = requestBody()
            Self.lock.lock()
            Self.fleetCalls.append("rotate")
            Self.fleetSyncBodies.append((try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:])
            let answer = Self.fleetSyncAnswer
            Self.lock.unlock()
            result = (200, "application/json", answer)
        case ("POST", "/api/hub/fleet/handover"):
            let body = requestBody()
            Self.lock.lock()
            Self.fleetCalls.append("handover")
            let parsed = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
            Self.fleetSyncBodies.append(parsed)
            var answer = (try? JSONSerialization.jsonObject(with: Self.fleetSyncAnswer)) as? [String: Any] ?? [:]
            answer["handed_over_to"] = parsed["controller_url"]
            answer["handed_over_at"] = 1_790_569_000.0
            // The controller is retired by its own handover, so the identity route says so too.
            Self.handedOverTo = parsed["controller_url"] as? String
            Self.lock.unlock()
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: answer))
        case ("GET", "/api/hub/fleet/nodes"):
            Self.lock.lock(); Self.fleetCalls.append("list"); Self.lock.unlock()
            // Every field here is published by the real Hub and was decoded by nothing: `role`,
            // `studios`, `version`, `joined_at`, `last_seen_at`, the credential's own dates, the
            // `unauthorized` count and the evicted machines. A roster of name-and-address was what
            // the page showed while all of it was on the wire.
            result = (200, "application/json", Data(#"{"role":"controller","nodes":[{"id":"studio-2","name":"Studio 2","address":"http://192.168.1.21:8080","state":"reachable","detail":null,"version":"0.4.0","role":"agent","studios":[{"id":"image","modality":"image"},{"id":"voice","modality":"voice"}],"joined_at":1750000000.0,"last_seen_at":1750003600.0,"credential_issued_at":1750000000.0,"has_credential":true},{"id":"studio-3","name":"Studio 3","address":"http://192.168.1.22:8080","state":"unreachable","detail":"could not reach the node","version":null,"role":"agent","studios":[],"joined_at":1750000100.0,"last_seen_at":null,"credential_issued_at":1750000100.0,"has_credential":true,"never_connected":true},{"id":"studio-4","name":"Studio 4","address":"http://192.168.1.23:8080","state":"unauthorized","detail":"the node answered but rejected this controller's credential (HTTP 401) - re-join it with the current Fleet Token","version":"0.4.1","role":"agent","studios":[{"id":"image","modality":"image"}],"joined_at":1750000200.0,"last_seen_at":null,"credential_issued_at":1750000200.0,"has_credential":true}],"removed":[{"id":"sold-mac","name":"Sold Mac","address":"http://192.168.1.30:8080","removed_at":1790563000.0,"removed_by":"operator","role":"agent","studios":[{"id":"voice","modality":"voice"}],"joined_at":1740000000.0}],"summary":{"total":3,"reachable":1,"unreachable":1,"unauthorized":1,"error":0,"removed":1}}"#.utf8))
        case ("GET", "/studio/voice/api/voices"):
            Self.lock.lock()
            Self.voiceListCalls += 1
            let hold = Self.holdNextVoiceList
            if hold { Self.holdNextVoiceList = false; Self.heldVoiceListStarted = true; Self.heldVoiceListHolding = true }
            let failure = Self.voiceListFailureStatus
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
            if let failure {
                result = (failure, "application/json", Data(#"{"detail":"the voice library is unreadable"}"#.utf8))
            } else {
                let payload = try! JSONSerialization.data(withJSONObject: ["voices": rows])
                if hold {
                    Self.voiceListStarted.signal()
                    DispatchQueue.global().async { [self] in
                        _ = Self.releaseVoiceList.wait(timeout: .now() + 5)
                        Self.lock.lock(); Self.heldVoiceListHolding = false; Self.lock.unlock()
                        finish((200, "application/json", payload))
                    }
                    return
                }
                result = (200, "application/json", payload)
            }
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
            Self.lock.lock(); Self.fleetCalls.append("overview"); let overviewFails = Self.overviewFails; Self.lock.unlock()
            if overviewFails { result = (500, "application/json", Data(#"{"detail":"fixture: a node timed out"}"#.utf8)); break }
            result = (200, "application/json", Data(#"{"machines":[{"machine":"local","label":"This Mac","role":"agent","address":null,"reachable":true,"version":"0.4.0","health":"up","detail":null,"memory_gb":16.0,"available_gb":9.5,"disk_free_gb":220.0,"disk_total_gb":494.0,"disk":{"state":"green","free_gb":220.0,"total_gb":494.0,"free_percent":44.5,"warnings":[]},"startup":{"open_at_login":true,"start_services_on_launch":false,"services_running":{"up":3,"total":4},"app_reporting":true},"current_job":{"job_id":"job-1","modality":"image","model":"black-forest-labs/FLUX.2-klein-4B","label":"storystudio","started_at":1750000000.0},"chip":"Apple M4","jobs":12,"average_seconds":41.5,"modalities":{"image":9,"transcription":3}},{"machine":"studio-3","label":"Studio 3","role":"agent","address":"http://192.168.1.20:47873","reachable":false,"version":null,"health":"unknown","detail":"this Mac has not reported in yet","memory_gb":null,"available_gb":null,"disk_free_gb":null,"disk_total_gb":null,"chip":null,"jobs":0,"average_seconds":null,"modalities":{}}],"role":"agent","controller":{"machine":"controller-mac","label":"Control Mac","role":"controller","address":"http://192.168.1.21:47873","state":"reachable","detail":null,"version":"0.4.0"},"roster_readable":true,"summary":{"total":2,"reachable":1,"unreachable":1}}"#.utf8))
        case ("GET", "/api/hub/controller"):
            Self.lock.lock()
            let roleNow = Self.currentRole
            let originNow = Self.credentialOrigin
            let handedOver = Self.handedOverTo
            let fail = Self.identityFailuresRemaining > 0
            if fail { Self.identityFailuresRemaining -= 1 }
            Self.lock.unlock()
            // `handed_over_to`/`handed_over_at` are what say a controller is retired: it keeps its
            // history and refuses remote work, so the page must not draw it as still taking jobs.
            let handedFields = handedOver.map {
                ",\"handed_over_to\":\"\($0)\",\"handed_over_at\":1790569000.0"
            } ?? ",\"handed_over_to\":null,\"handed_over_at\":null"
            result = fail
                ? (503, "application/json", Data(#"{"detail":"identity temporarily unavailable"}"#.utf8))
                : (200, "application/json", Data("{\"settings\":{\"version\":2,\"role\":\"\(roleNow)\",\"site_id\":\"studiohub-local\",\"site_name\":\"This Mac\",\"controller_id\":\"controller-local\",\"database_mode\":\"off\",\"machine_name\":\"Studio Mini\",\"database_configured\":false,\"sqlite_authoritative\":true,\"global_authority\":\"genstudio\",\"migration_stage\":\"local\",\"fleet_credential_origin\":\"\(originNow)\"\(handedFields)},\"runtime\":{\"state\":\"idle\"},\"readiness\":{\"ready\":true,\"role\":\"\(roleNow)\",\"site_id\":\"studiohub-local\",\"controller_id\":\"controller-local\",\"migration_stage\":\"local\",\"sqlite_authoritative\":true,\"global_job_claiming\":false,\"global_authority\":\"genstudio\",\"reason\":null,\"telemetry_warning\":null},\"capacity\":{\"machines\":1,\"queue_depth\":0}}".utf8))
        case ("GET", "/api/version"):
            result = (200, "application/json", Data(#"{"version":"0.4.0","source":"studiohub-mac@06e893e1643bffbcbd162fae122e5a4d0f29e283"}"#.utf8))
        case ("GET", "/api/hub/memory/policy"):
            Self.lock.lock()
            let memoryDown = Self.memoryUnavailable
            let memoryOverride = Self.memoryPolicyOverride
            Self.lock.unlock()
            result = memoryDown
                ? (404, "application/json", Data(#"{"detail":"fixture route missing"}"#.utf8))
                : (200, "application/json", memoryOverride ?? Self.memoryFixture)
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
        case ("GET", "/studio/image/api/loading-mode"):
            // The Image worker's own per-machine loading-size setting, reached through the Hub's
            // generic studio proxy exactly like its other routes.
            Self.lock.lock()
            Self.lastImageLoadingToken = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            Self.lock.unlock()
            result = (200, "application/json", Self.imageLoadingStatus())
        case ("PUT", "/studio/image/api/loading-mode"):
            let body = requestBody()
            let requested = ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any])?["mode"] as? String ?? ""
            Self.lock.lock()
            Self.lastImageLoadingToken = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            let accepted = ["auto", "low", "full"].contains(requested)
            if accepted {
                Self.imageLoadingSelection = requested
                Self.imageLoadingPuts.append(requested)
            }
            Self.lock.unlock()
            // An unknown mode is refused by name, exactly as the worker's own route refuses it.
            result = accepted
                ? (200, "application/json", Self.imageLoadingStatus())
                : (422, "application/json", Data("{\"detail\":\"unknown memory mode '\(requested)'; must be one of: auto, low, full\"}".utf8))
        case ("GET", "/studio/voice/api/settings"):
            result = (200, "application/json", Data(#"{"hf_token_set":false,"hf_token_masked":""}"#.utf8))
        case ("POST", "/api/hub/model-packages/whisper-turbo/download"):
            Self.lock.lock(); Self.lastDownloadPath = path
            Self.lastDownloadToken = request.value(forHTTPHeaderField: "X-Hub-Token") ?? ""
            Self.lock.unlock()
            result = (200, "application/json", Data(#"{"id":"hub-download-1","package_id":"whisper-turbo","state":"running"}"#.utf8))
        case ("GET", "/api/hub/model-downloads/hub-download-1"):
            Self.lock.lock()
            let cancelled = Self.hubDownloadCancelled
            let mode = Self.hubDownloadMode
            Self.hubDownloadReads += 1
            Self.lock.unlock()
            switch mode {
            case "transient": result = (500, "application/json", Data(#"{"detail":"fixture hiccup"}"#.utf8))
            case "forgotten": result = (404, "application/json", Data(#"{"detail":"unknown model download"}"#.utf8))
            case "failed": result = (200, "application/json", Data(#"{"id":"hub-download-1","package_id":"whisper-turbo","state":"error","error_tail":"Download incomplete or invalid: model.safetensors"}"#.utf8))
            default: result = (200, "application/json", Data("{\"id\":\"hub-download-1\",\"package_id\":\"whisper-turbo\",\"state\":\"\(cancelled ? "cancelled" : "running")\",\"bytes_done\":3000000000,\"total_bytes\":12000000000}".utf8))
            }
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
                // A Chat-page completion, which the Hub now records as a finished one-item batch.
                // It is placed by its modality like every other batch, so the Chat tab lists real
                // work rather than only an empty state.
                ["id": "chat-job", "modality": "chat"],
                // A real local Video batch, filed under Video like every other Studio's work —
                // and one modality this build has never heard of, which must be named in the
                // startup note: the old filter skipped anything not on its list before the loop,
                // so it was neither shown, counted nor reported.
                ["id": "video-job", "modality": "video"],
                ["id": "hologram-job", "modality": "hologram"],
            ]
            if pendingImage { batches.append(["id": "image-uncertain", "modality": "image"]) }
            if pendingMusic { batches.append(["id": "music-uncertain", "modality": "music"]) }
            Self.lock.lock()
            let extraChat = Self.chatJobWithoutStatedTime
            Self.lock.unlock()
            if extraChat { batches.append(["id": "chat-job-nostated", "modality": "chat"]) }
            // A failed image batch, when a test asks for one: the Hub's own item counts say whether
            // it is settled (nothing waiting) or was retried (one queued again).
            Self.lock.lock(); let errorBatch = Self.errorBatchMode; Self.lock.unlock()
            var listed = batches as [[String: Any]]
            if errorBatch != "off" {
                listed.append(["id": "err-job", "modality": "image", "queued": errorBatch == "retried" ? 1 : 0,
                               "retrying": 0, "running": 0, "cancel_requested": 0, "uncertain": 0, "error": 1])
            }
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: ["batches": listed]))
        case ("GET", "/api/hub/jobs/err-job"):
            Self.lock.lock(); let errorBatch = Self.errorBatchMode; Self.lock.unlock()
            result = (200, "application/json", Data("{\"id\":\"err-job\",\"modality\":\"image\",\"model\":\"fixture/flux\",\"created_at\":1,\"items\":[{\"index\":0,\"state\":\"\(errorBatch == "retried" ? "queued" : "error")\",\"prompt\":\"x\",\"error\":\"worker refused\"}]}".utf8))
        case ("GET", "/api/hub/jobs/old-job"):
            Self.lock.lock(); let refuse = Self.failJobDetail; Self.lock.unlock()
            if refuse { result = (500, "application/json", Data(#"{"detail":"fixture cannot read this batch"}"#.utf8)); break }
            result = (200, "application/json", Data(#"{"id":"old-job","modality":"voice","model":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","created_at":10,"governor_note":"'x' not downloaded on image@studio-mac-1102 — download it on that Mac, or pin another","items":[{"index":0,"state":"done","progress":1.0,"prompt":"Earlier","artifact_url":"/api/hub/jobs/old-job/items/0/artifact","runtime_s":31.0,"machine":"studio-mac-1102","tries":2,"retry_at":4102444800.0}]}"#.utf8))
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
            let pending: [String: Any] = ["id": "music-uncertain", "modality": "music", "model": "fixture/sung-music", "created_at": 20,
                "items": [["index": 0, "state": "uncertain", "prompt": "Warm lo-fi beat", "artifact_url": "/api/hub/jobs/music-uncertain/items/0/artifact", "bytes": Self.wav.count, "sha256": uncertainSHA,
                           "params": Self.musicParams]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: pending))
        case ("GET", "/api/hub/jobs/video-job"):
            let sha = SHA256.hash(data: Self.mp4).map { String(format: "%02x", $0) }.joined()
            // `duration_seconds` is the Hub's copy of the worker's own measured MP4 length; the
            // row reads it through `HubJobItem.videoDurationS`.
            let body: [String: Any] = ["id": "video-job", "modality": "video",
                "model": "baa-ai/LTX-2.3-22B-RAM-12GB-MLX", "created_at": 38,
                "items": [["index": 0, "state": "done", "progress": 1.0,
                           "prompt": "A slow push toward a quiet harbour",
                           "artifact_url": "/api/hub/jobs/video-job/items/0/artifact",
                           "bytes": Self.mp4.count, "sha256": sha,
                           "media_type": "video/mp4", "duration_seconds": 4.04,
                           "width": 704, "height": 448, "frames": 97, "frame_rate": 24,
                           "has_audio": true, "runtime_s": 304.0,
                           // The worker's own path, which is what Show in Folder reveals. It is a
                           // path this fixture never creates, so the same field also pins the
                           // negative: a file that is not there must not offer a folder.
                           "artifact_path": "/nonexistent/fixture/video-job.mp4",
                           "params": ["aspect_ratio": "11:7", "resolution": "448p",
                                      "duration_seconds": 4]]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: body))
        case ("GET", "/api/hub/jobs/video-job/items/0/artifact"):
            result = (200, "video/mp4", Self.mp4)
        case ("POST", "/api/hub/jobs/video-job/items/0/ack"):
            result = (200, "application/json", Data(#"{"ok":true}"#.utf8))
        case ("GET", "/api/hub/jobs/music-job"):
            let sha = SHA256.hash(data: Self.wav).map { String(format: "%02x", $0) }.joined()
            Self.lock.lock(); let uncertain = Self.uncertainMusic; Self.lock.unlock()
            let body: [String: Any] = ["id": "music-job", "modality": "music", "model": "fixture/sung-music", "created_at": 35,
                "items": [["index": 0, "state": uncertain ? "uncertain" : "done", "progress": 1.0, "prompt": "Warm lo-fi beat",
                           "artifact_url": "/api/hub/jobs/music-job/items/0/artifact", "bytes": Self.wav.count, "sha256": sha,
                           "media_type": "audio/wav", "audio_duration_s": 125.0, "runtime_s": 89.0,
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
            Self.lock.lock(); let idleDownloads = Self.voiceDownloadsIdle; Self.lock.unlock()
            if idleDownloads { result = (200, "application/json", Data(#"{"jobs":[]}"#.utf8)); break }
            result = (200, "application/json", Data(#"{"jobs":[{"id":"download-1","repo":"mlx-community/whisper-large-v3-turbo","state":"running","bytes_done":50,"bytes_total":100,"percent":50.0}]}"#.utf8))
        case ("GET", "/api/hub/jobs/chat-job"):
            // The shape the Hub records for a Chat-page completion: one item, the operator's last
            // user turn as `prompt`, the answer as the item's `text` (the Chat worker streams its
            // completion and keeps no artifact, so the item is the artifact), the runtime the Hub
            // measured around the proxied call, and - beside it, never folded into it - the usage
            // and generation time the Chat worker itself stated. `runtime_s` stays the Hub's round
            // trip, which includes the model load on a cold turn.
            Self.lock.lock(); let stated = Self.chatJobGenerationSeconds; Self.lock.unlock()
            var chatItem: [String: Any] = ["index": 0, "state": "done", "progress": 1.0,
                       "prompt": "What is a tokenizer?",
                       "text": "It turns text into the numbers a model reads.",
                       "runtime_s": 61.4, "bytes": 48,
                       "media_type": "text/plain; charset=utf-8",
                       "usage": ["prompt_tokens": 4, "completion_tokens": 10, "total_tokens": 14]]
            if let stated { chatItem["generation_s"] = stated }
            let chatJob: [String: Any] = ["id": "chat-job", "modality": "chat",
                "model": "Qwen/Qwen3.5-9B", "created_at": 40, "items": [chatItem]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: chatJob))
        case ("GET", "/api/hub/jobs/chat-job-nostated"):
            // The other half of the same contract: the worker stated a usage block but no
            // generation time, so there is no rate to derive and the row must not invent one from
            // the Hub's round trip.
            let chatJob: [String: Any] = ["id": "chat-job-nostated", "modality": "chat",
                "model": "Qwen/Qwen3.5-9B", "created_at": 41,
                "items": [["index": 0, "state": "done", "progress": 1.0,
                           "prompt": "How fast was that?",
                           "text": "This reply states tokens and no generation time.",
                           "runtime_s": 61.4, "bytes": 48,
                           "media_type": "text/plain; charset=utf-8",
                           "usage": ["prompt_tokens": 4, "completion_tokens": 10, "total_tokens": 14]]]]
            result = (200, "application/json", try! JSONSerialization.data(withJSONObject: chatJob))
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
        case ("POST", "/api/hub/chat/completions"):
            // The Hub's local Chat proxy: one complete reply, or the status a worker that is not
            // answering gets. The body is recorded so the request can be checked against the
            // transcript it was built from, and the hold is what makes the in-flight state
            // observable.
            let body = requestBody()
            Self.lock.lock()
            Self.chatCalls += 1
            Self.lastChatBody = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            let hold = Self.holdNextChat
            if hold { Self.holdNextChat = false; Self.heldChatStarted = true }
            let failure = Self.chatFailureStatus
            Self.lock.unlock()
            if hold {
                _ = Self.releaseChat.wait(timeout: .now() + 5)
            }
            if let failure {
                result = (failure, "application/json",
                          Data(#"{"detail":"the local Chat worker is not answering"}"#.utf8))
            } else {
                // The live reply carries what the worker stated for this answer: its usage block
                // and its own generation time under `studio_hub.elapsed_seconds`. A reply that
                // states no elapsed time states no rate either.
                Self.lock.lock()
                let tokens = Self.chatReplyCompletionTokens
                let elapsed = Self.chatReplyElapsedSeconds
                Self.lock.unlock()
                var payload: [String: Any] = ["choices": [[
                    "message": ["role": "assistant", "content": "Fixture reply."]]]]
                if let tokens {
                    payload["usage"] = ["prompt_tokens": 4, "completion_tokens": tokens,
                                        "total_tokens": tokens + 4]
                }
                if let elapsed { payload["studio_hub"] = ["elapsed_seconds": elapsed] }
                result = (200, "application/json",
                          try! JSONSerialization.data(withJSONObject: payload))
            }
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
        case ("POST", "/api/hub/release/stage"), ("GET", "/api/hub/release/stage"):
            Self.lock.lock()
            let script = Self.releaseStageScript
            let index = Self.releaseStageRequests
            Self.releaseStageRequests += 1
            Self.lock.unlock()
            if let script, !script.isEmpty {
                result = (method == "POST" ? 202 : 200, "application/json",
                          (try? JSONSerialization.data(withJSONObject: script[min(index, script.count - 1)])) ?? Data())
            } else {
                result = (404, "application/json", Data(#"{"detail":"Not Found"}"#.utf8))
            }
        case ("POST", "/api/hub/startup/sync"):
            let body = ((try? JSONSerialization.jsonObject(with: requestBody())) as? [String: Any]) ?? [:]
            Self.lock.lock(); Self.startupSyncBodies.append(body); let answer = Self.startupSyncAnswer; Self.lock.unlock()
            result = (200, "application/json", (try? JSONSerialization.data(withJSONObject: answer)) ?? Data())
        case ("POST", "/api/hub/update/drain"):
            let want = ((try? JSONSerialization.jsonObject(with: requestBody())) as? [String: Any])?["want"] as? Bool ?? false
            Self.lock.lock(); Self.updateDrainWants.append(want); let reply = Self.updateDrainReply; Self.lock.unlock()
            result = (200, "application/json", Data("{\"state\":\"\(want ? reply : "resumed")\"}".utf8))
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

    private static let storagePreviewFixture = Data(#"{"effective":{"max_bytes":50000000000,"retention_days":30,"cleanup_enabled":true,"cleanup_interval_seconds":3600,"minimum_free_bytes":20000000000},"machines":[{"machine":"local","used_bytes":7200000000,"protected_bytes":1500000000,"max_bytes":50000000000,"retention_days":30,"enabled":true,"over_limit":false,"free_bytes":410000000000,"would_delete_bytes":3200000000}],"trash":{"bytes":1800000000,"days":15}}"#.utf8)

    /// Shaped like the Hub's real reply, including a model with no timed run so the app's
    /// "not measured" path is exercised rather than assumed.
    private static let statisticsFixture = Data(#"{"total":3,"by_machine":{"local":{"count":3,"avg_s":45.5,"modalities":{"image":1,"transcription":2}}},"by_modality":{"image":{"count":1,"avg_s":94.47},"transcription":{"count":2,"avg_s":21.02}},"by_model":{"fixture/flux":{"count":1,"avg_s":94.47,"modality":"image"},"mlx-community/whisper-large-v3-turbo":{"count":2,"avg_s":21.02,"modality":"transcription"}},"model_rows":[{"model":"fixture/flux","count":1,"avg_s":94.47,"modality":"image"},{"model":"mlx-community/whisper-large-v3-turbo","count":2,"avg_s":21.02,"modality":"transcription"},{"model":"mlx-community/whisper-base-mlx","count":1,"avg_s":null,"modality":"transcription"}],"matrix":[],"by_source":{"job":3},"available_machines":["local"],"available_modalities":["image","voice","transcription"]}"#.utf8)

    private static let memoryFixture = Data(#"{"default_mode":"immediate","options":[{"mode":"performance","label":"Performance","idle_seconds":null},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"studios":[{"id":"image","title":"Image Studio","modality":"image","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"immediate","default_mode":"immediate","idle_seconds":0,"options":[{"mode":"immediate","label":"After every job","idle_seconds":0}],"loaded_models":[],"active_work":false,"busy":false,"fixed":true,"release_policy":"The model is released when each job finishes.","machine_bytes":17179869184}},{"id":"subtitle","title":"Subtitle Studio","modality":"transcription","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"immediate","default_mode":"immediate","idle_seconds":0,"options":[{"mode":"performance","label":"Performance","idle_seconds":null},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"loaded_models":[["whisper-large-v3-turbo"]],"active_work":false,"busy":false,"machine_bytes":17179869184}},{"id":"voice","title":"Voice Studio","modality":"voice","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"balanced","default_mode":"balanced","idle_seconds":600,"options":[{"mode":"performance","label":"Performance","idle_seconds":null},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"loaded_models":[],"active_work":true,"busy":true,"machine_bytes":17179869184}}],"summary":{"total":3,"ready":3,"offline":0,"update_required":0}}"#.utf8)

    /// A 32 GB Mac whose Subtitle Studio publishes the fifth mode, `auto`, with the worker's own
    /// sentence for what it resolved to here. The `detail` strings are the worker's; the app must
    /// render them rather than its own derivation, because only the worker knows the machine.
    static let memoryAutoFixture = Data(#"{"default_mode":"auto","options":[{"mode":"auto","label":"Auto","idle_seconds":600,"detail":"Follows this Mac: releases after 10 idle minutes here"},{"mode":"performance","label":"Performance","idle_seconds":null},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"studios":[{"id":"subtitle","title":"Subtitle Studio","modality":"transcription","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"auto","resolved_mode":"balanced","default_mode":"auto","idle_seconds":600,"machine_mode":"balanced","machine_bytes":34359738368,"options":[{"mode":"auto","label":"Auto","idle_seconds":600,"detail":"Follows this Mac: releases after 10 idle minutes here"},{"mode":"performance","label":"Performance","idle_seconds":null,"detail":"Keeps the model loaded until you release it"},{"mode":"balanced","label":"Balanced","idle_seconds":600,"detail":"Releases the model after 10 idle minutes"},{"mode":"memory_saver","label":"Memory Saver","idle_seconds":120,"detail":"Releases the model after 2 idle minutes"},{"mode":"immediate","label":"Immediate","idle_seconds":0,"detail":"Releases the model as soon as each job finishes"}],"loaded_models":[],"active_work":false,"busy":false,"machine_bytes":34359738368}}],"summary":{"total":1,"ready":1,"offline":0,"update_required":0}}"#.utf8)

    /// The same machine from a worker that publishes no `detail` at all: the app falls back to its
    /// own derived sentence, which is only ever a fallback for a payload that stated nothing.
    static let memoryNoDetailFixture = Data(#"{"default_mode":"auto","options":[{"mode":"auto","label":"Auto","idle_seconds":600}],"studios":[{"id":"subtitle","title":"Subtitle Studio","modality":"transcription","machine_label":"This Mac","health":"up","state":"ready","supported":true,"detail":null,"policy":{"mode":"auto","resolved_mode":"balanced","default_mode":"auto","idle_seconds":600,"machine_mode":"balanced","machine_bytes":34359738368,"options":[{"mode":"auto","label":"Auto","idle_seconds":600},{"mode":"balanced","label":"Balanced","idle_seconds":600},{"mode":"immediate","label":"Immediate","idle_seconds":0}],"loaded_models":[],"active_work":false,"busy":false,"machine_bytes":34359738368}}],"summary":{"total":1,"ready":1,"offline":0,"update_required":0}}"#.utf8)
    /// The catalogue's fixed rows. The served catalogue is `catalogFixture` below, which projects
    /// each image row's `loading_modes` for the selection the fixture's stub Image worker is
    /// currently holding.
    private static let catalogBaseFixture = Data(
#"{"models":[{"repo":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","label":"Qwen CustomVoice","family":"qwen3-tts","modes":["preset"],"size_gb":1.9,"capabilities":["tts","multilingual","expressive"],"languages":["en","zh"],"runtime_ready":true,"cache":{"state":"cached"},"execution_contract":{"qualification_source":"audit","text_max_characters":4000},"long_form_delivery":{"section_max_characters":400,"join_pause_milliseconds":120,"hard_cap_characters":5000,"source":"long_form_policy","note":"Sections are cut on sentence boundaries and joined with one pause.","speed":{"delivery":"native","minimum":0.5,"maximum":2.0},"section_size_control":{"minimum":400,"maximum":4000,"step":50,"default_custom":1600,"runtime_default":1600}},"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"voice","operation":"voice.tts","parameters":[{"name":"text","type":"string","required":true,"max_characters":4000,"bound_source":"Qwen CustomVoice audit: contract.input_limits.text_max_characters"},{"name":"speed","type":"number","required":false,"minimum":0.5,"maximum":2.0,"default":1.0,"note":"clamped to this range by the dispatcher; delivered per the family's policy"},{"name":"section_max_characters","type":"integer","required":false,"minimum":400,"maximum":4000,"default":1600,"note":"omit for the measured Auto section size"},{"name":"language","type":"string","required":false,"enum":["en","zh"]},{"name":"preset_speaker","type":"string","required":false,"enum":["Ryan","Aiden"],"note":"one of this family's published ready-made voices (the checkpoint's own spk_id map)"}],"artifact":{"modality":"voice","media_type":"audio/wav","extension":"wav","per_item":1}}},{"repo":"other/tts","label":"Other TTS","family":"other","modes":["preset"],"size_gb":2.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/paused","label":"Paused fixture","family":"other","modes":["preset"],"size_gb":1.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"}},{"repo":"mlx-community/whisper-base-mlx","label":"Whisper base","languages":"Multilingual"},{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","engine":"subtitle-mlx","size_gb":1.6,"capabilities":["transcription"],"languages":"Multilingual","runtime_ready":true,"cache":{"state":"cached"},"readiness":{"files":{"weight_files":["weights.safetensors"]}}},{"repo":"fixture/flux","label":"FLUX fixture","family":"flux2-klein","capabilities":["txt2img"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"loading_modes":{"supported":false,"selected":"auto","effective":"default","threshold_bytes":null,"options":[],"measured":{"default":{"peak_gb":null,"seconds":null},"low_ram":{"peak_gb":null,"seconds":null},"measured_on":null,"evidence":"unmeasured","scene":null,"note":"This model states no bounded configuration."},"reason":"No memory knobs on purpose: this fixture is qualified at 16 GB, and adding an mflux memory knob would be an unverified change to a verified path. This machine-wide setting does not change how it loads."},"generation_defaults":{"width":1280,"height":720,"steps":4,"guidance":1.0,"seed":-1},"image_sizes":[{"id":"16:9","label":"Wide 16:9","width":1280,"height":720},{"id":"1:1","label":"Square","width":1024,"height":1024}],"generation_limits":{"min_steps":1,"max_steps":50,"min_guidance":0.0,"max_guidance":10.0,"min_seed":-1,"max_seed":4294967295},"sizes":[{"aspect_ratio":"16:9","width":1280,"height":720,"tier":"1K","default":true},{"aspect_ratio":"1:1","width":1024,"height":1024,"tier":"1K"}],"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"image","operation":"image.text_to_image","parameters":[{"name":"prompt","type":"string","required":true,"max_characters":4000},{"name":"aspect_ratio","type":"string","required":false,"enum":["1:1","16:9"],"default":"16:9"},{"name":"resolution","type":"string","required":false,"enum":["1K"],"default":"1K"},{"name":"steps","type":"integer","required":false,"minimum":1,"maximum":50,"default":4},{"name":"guidance","type":"number","required":false,"minimum":0.0,"maximum":10.0,"default":1.0,"note":"accepted and ignored: this model runs without classifier-free guidance"},{"name":"seed","type":"integer","required":false,"minimum":-1,"maximum":4294967295,"default":-1}],"artifact":{"modality":"image","media_type":"image/png","extension":"png","per_item":1}}},{"repo":"mlx-community/VoxCPM2-4bit","label":"VoxCPM2 4-bit","family":"voxcpm-mlx","modes":["cloning","design"],"size_gb":3.0,"capabilities":["tts","voice-cloning"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}},{"repo":"fixture/sung-music","label":"Sung Music fixture","family":"fixture-sung-music","size_gb":7.7,"capabilities":["music","instrumental","text-to-music","lyrics","long-form"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"duration_seconds":30.0,"steps":8,"guidance_scale":1.0,"shift":3.0,"seed":-1,"instrumental":true,"lyrics":"[Instrumental]","vocal_language":"unknown","dit_model":"fixture-dit","lm_model":"fixture-lm"},"generation_limits":{"min_duration_seconds":1.0,"max_duration_seconds":600.0,"single_pass_max_seconds":480,"min_steps":1,"max_steps":50,"min_guidance":0.0,"max_guidance":30.0,"min_seed":-1,"max_seed":4294967295},"sample_rate_hz":48000,"channels":2,"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"music","operation":"music.generation","parameters":[{"name":"prompt","type":"string","required":true,"max_characters":4000},{"name":"duration_seconds","type":"number","required":false,"minimum":1.0,"maximum":600.0,"default":30.0,"ladder":[{"seconds":1,"label":"1 s"},{"seconds":30,"label":"30 s"},{"seconds":60,"label":"60 s"},{"seconds":120,"label":"120 s"},{"seconds":240,"label":"240 s"},{"seconds":480,"label":"480 s"},{"seconds":600,"label":"600 s","exceeds_single_pass":true}],"note":"a duration beyond this host's single-pass cap is rendered in sections"},{"name":"steps","type":"integer","required":false,"minimum":1,"maximum":50,"default":8},{"name":"guidance_scale","type":"number","required":false,"minimum":0.0,"maximum":30.0,"default":1.0},{"name":"seed","type":"integer","required":false,"minimum":-1,"maximum":4294967295,"default":-1},{"name":"shift","type":"number","required":false,"minimum":0.0,"maximum":20.0,"note":"the engine's own control; its bounds are the engine's, not this model's"},{"name":"instrumental","type":"boolean","required":false,"default":true,"note":"false asks for sung lyrics; this engine is the only one that can sing"},{"name":"lyrics","type":"string","required":false,"max_characters":4000},{"name":"vocal_language","type":"string","required":false,"max_characters":32}],"artifact":{"modality":"music","media_type":"audio/wav","extension":"wav","per_item":1}}},{"repo":"mlx-community/chatterbox-8bit","label":"Chatterbox 8-bit","size_gb":3.4},{"repo":"mlx-community/chatterbox-8bit","label":"Chatterbox 8-bit (MLX)","family":"chatterbox-mlx","modes":["cloning"],"size_gb":3.4,"capabilities":["tts","voice-cloning","expressive","multilingual"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}},{"repo":"mlx-community/fish-audio-s2-pro-8bit","label":"Fish Audio S2 Pro 8-bit (MLX)","family":"fish-audio-mlx","modes":["cloning"],"size_gb":6.7,"capabilities":["tts","voice-cloning","multilingual","expressive"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"},"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}},{"repo":"khmerttsopensource/khmer-tts","label":"Khmer TTS (MMS VITS)","family":"khmer-tts-mms","modes":["preset"],"size_gb":0.4,"capabilities":["tts","multilingual"],"languages":["km"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"sengtha/khmer-tts-female-v2","label":"Khmer TTS Female (VITS/ONNX)","family":"khmer-tts-onnx","modes":["preset"],"size_gb":0.12,"capabilities":["tts","multilingual"],"languages":["km"],"runtime_ready":true,"cache":{"state":"uncached"}},{"repo":"mlx-community/pocket-tts-4bit","label":"Pocket TTS 4-bit (MLX)","family":"pocket-tts","modes":["cloning","preset"],"size_gb":1.1,"capabilities":["tts","voice-cloning"],"languages":["en"],"runtime_ready":true,"cache":{"state":"uncached"},"reference_audio":{"declared":true,"supported":true}},{"repo":"fixture/unwired-voice","label":"Voice model with no runtime","family":"fixture-unwired-voice","modes":["preset"],"size_gb":1.0,"capabilities":["tts"],"languages":["en"],"runtime_ready":false,"cache":{"state":"cached"}},{"repo":"mlx-community/S3TokenizerV2","label":"S3 speech tokenizer","family":"s3tokenizer","size_gb":0.5,"capabilities":["codec"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/zimage","label":"Z-Image fixture 4-bit","family":"z-image","size_gb":3.2,"capabilities":["txt2img"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"loading_modes":{"supported":true,"selected":"auto","effective":"low-ram","threshold_bytes":24000000000,"options":[{"id":"auto","label":"Auto","detail":"Chooses from this Mac's unified memory: the bounded configuration below 24 GB, the default at or above it. The trade: about 7.1 GB peak instead of 17.7 GB, and about 236 s instead of 166 s, measured on 32 GiB M2 Max, mflux 0.20.0, 4-bit pack."},{"id":"low","label":"Low RAM","detail":"Runs mflux's bounded configuration: about 7.1 GB peak instead of 17.7 GB, and about 236 s instead of 166 s, measured on 32 GiB M2 Max, mflux 0.20.0, 4-bit pack."},{"id":"full","label":"Full","detail":"Runs mflux's default configuration: about 17.7 GB peak instead of 7.1 GB, and about 166 s instead of 236 s, measured on 32 GiB M2 Max, mflux 0.20.0, 4-bit pack."}],"measured":{"default":{"peak_gb":17.67,"seconds":166},"low_ram":{"peak_gb":7.12,"seconds":236},"measured_on":"32 GiB M2 Max, mflux 0.20.0, 4-bit pack","evidence":"measured","scene":"1280x720, 9 steps, seed 42","note":null},"reason":null},"generation_defaults":{"width":1024,"height":1024,"steps":8,"seed":-1},"generation_limits":{"min_steps":1,"max_steps":20,"min_seed":-1,"max_seed":4294967295}},{"repo":"fixture/second-music","label":"Second Music fixture","family":"stable-audio","size_gb":4.1,"capabilities":["music","instrumental"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"generation_defaults":{"duration_seconds":20.0,"steps":10},"generation_limits":{"min_duration_seconds":5.0,"max_duration_seconds":60.0},"sample_rate_hz":44100,"channels":2,"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"music","operation":"music.generation","parameters":[{"name":"prompt","type":"string","required":true,"max_characters":4000},{"name":"duration_seconds","type":"number","required":false,"minimum":5.0,"maximum":60.0,"default":20.0,"ladder":[{"seconds":1,"label":"1 s"},{"seconds":30,"label":"30 s"},{"seconds":60,"label":"60 s"},{"seconds":120,"label":"120 s"},{"seconds":240,"label":"240 s"},{"seconds":380,"label":"380 s"}]},{"name":"steps","type":"integer","required":false,"minimum":1,"maximum":50,"default":10}],"artifact":{"modality":"music","media_type":"audio/wav","extension":"wav","per_item":1}}},{"repo":"fixture/flux-unpackaged","label":"Unregistered Image fixture 4-bit","family":"flux2-klein","capabilities":["txt2img"],"runtime_ready":true,"cache":{"state":"cached"},"generation_defaults":{"width":1024,"height":1024,"steps":4,"seed":-1},"generation_limits":{"min_steps":1,"max_steps":20,"min_seed":-1,"max_seed":4294967295}},{"repo":"fixture/ltx-video","label":"LTX fixture mixed 2-8 bit","family":"ltx-2-3","capabilities":["img2video","txt2video"],"hub_modality":"video","runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"video","operation":"video.generation","modes":["image-to-video","text-to-video"],"parameters":[{"name":"prompt","type":"string","required":true,"max_characters":4000},{"name":"mode","type":"string","required":false,"enum":["image-to-video","text-to-video"],"default":"image-to-video"},{"name":"aspect_ratio","type":"string","required":false,"enum":["11:7","1:1"],"default":"11:7"},{"name":"resolution","type":"string","required":false,"enum":["448p"],"default":"448p"},{"name":"duration_seconds","type":"integer","required":false,"enum":[2,4],"default":4},{"name":"audio","type":"boolean","required":false,"default":true,"note":"the model generates picture and sound together, so this does not change generation time; unticking it skips the audio decode and writes a silent MP4."},{"name":"seed","type":"integer","required":false,"minimum":-1,"maximum":4294967295,"default":-1}],"canvases":[{"resolution":"448p","aspect_ratio":"11:7","width":704,"height":448,"durations":[2,4]},{"resolution":"448p","aspect_ratio":"1:1","width":448,"height":448,"durations":[2,4]}],"artifact":{"modality":"video","media_type":"video/mp4","extension":"mp4","per_item":1}}},{"repo":"fixture/second-preset","label":"Second Preset Voice","family":"secondvoice","modes":["preset"],"size_gb":0.9,"capabilities":["tts","multilingual"],"languages":["km"],"runtime_ready":true,"cache":{"state":"cached"},"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"voice","operation":"voice.tts","parameters":[{"name":"text","type":"string","required":true,"max_characters":4000},{"name":"speed","type":"number","required":false,"minimum":0.5,"maximum":2.0,"default":1.0},{"name":"preset_speaker","type":"string","required":false,"enum":["Sophea","Dara"],"note":"one of this family's published ready-made voices"}],"artifact":{"modality":"voice","media_type":"audio/wav","extension":"wav","per_item":1}}},{"repo":"fixture/two-mode-voice","label":"Two-mode voice fixture 4-bit","family":"fixture-two-mode","size_gb":2.0,"capabilities":["tts","voice-cloning"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"},"modes":["cloning","design"],"reference_audio":{"declared":true,"supported":true,"transcript":"optional"}},{"repo":"fixture/unreported-modes","label":"Voice model with unreported modes","family":"fixture-unreported-modes","size_gb":1.2,"capabilities":["tts"],"languages":["en"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"Qwen/Qwen3.5-9B","label":"Qwen3.5-9B 4-bit","family":"chat-mlx","size_gb":5.5,"capabilities":["chat.completion","text-generation"],"runtime_ready":true,"cache":{"state":"cached"},"min_total_memory_gb":16,"model_limits":{"context_window_tokens":{"value":262144,"source":"model/config.json text_config.max_position_embeddings"},"max_output_tokens":{"value":4096,"source":"worker default: the checkpoint ships no generation_config.json"}},"memory_warning":null,"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"chat","operation":"chat.completion","parameters":[{"name":"model","type":"string","required":false,"default":"Qwen/Qwen3.5-9B","note":"the one model this worker serves; any other value is refused by name"},{"name":"messages","type":"array","required":true,"note":"OpenAI-shaped messages"},{"name":"max_tokens","type":"integer","required":false,"minimum":1,"maximum":4096,"default":4096},{"name":"temperature","type":"number","required":false,"minimum":0.0,"default":0.7},{"name":"top_p","type":"number","required":false,"minimum":0.0,"minimum_exclusive":true,"maximum":1.0,"default":0.95},{"name":"stream","type":"boolean","required":false,"default":false},{"name":"seed","type":"integer","required":false,"note":"a real integer"},{"name":"response_format","type":"object","required":false,"note":"type must be json_object or json_schema; anything else is refused"}],"artifact":{"modality":"chat","media_type":"application/json","extension":"json","per_item":1}}},{"repo":"fixture/chat-conversational","label":"Fixture Conversational 4-bit","family":"qwen3","size_gb":2.2,"capabilities":["conversational"],"runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/chat-family","label":"Fixture Chat Family 4-bit","family":"chat-mlx","size_gb":1.1,"capabilities":["text2text"],"runtime_ready":true,"cache":{"state":"uncached"}},{"repo":"fixture/chat-annotated","label":"Fixture Annotated Chat 4-bit","family":"fixture-llm","size_gb":0.8,"capabilities":["text2text"],"hub_modality":"chat","runtime_ready":true,"cache":{"state":"cached"}},{"repo":"fixture/narrow-image","label":"Narrow Image fixture 4-bit","family":"narrow-engine","capabilities":["txt2img"],"runtime_ready":true,"memory_eligible":true,"cache":{"state":"cached"},"min_total_memory_gb":16,"generation_defaults":{"width":800,"height":600,"steps":4,"seed":-1},"generation_limits":{"min_steps":1,"max_steps":20,"min_seed":-1,"max_seed":4294967295},"sizes":[{"aspect_ratio":"4:3","width":800,"height":600,"tier":"1K","default":true},{"aspect_ratio":"3:2","width":900,"height":600,"tier":"1K"}],"guidance_ignored":true,"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"image","operation":"image.text_to_image","parameters":[{"name":"prompt","type":"string","required":true,"max_characters":4000},{"name":"aspect_ratio","type":"string","required":false,"enum":["4:3","3:2"],"default":"4:3"},{"name":"resolution","type":"string","required":false,"enum":["1K"],"default":"1K"},{"name":"steps","type":"integer","required":false,"minimum":3,"maximum":5,"default":4},{"name":"guidance","type":"number","required":false,"minimum":0.0,"maximum":1.0,"default":0.0,"ignored":true,"note":"accepted and ignored: this model runs without classifier-free guidance"},{"name":"seed","type":"integer","required":false,"minimum":-1,"maximum":4294967295,"default":-1}],"artifact":{"modality":"image","media_type":"image/png","extension":"png","per_item":1}}}]}"#.utf8)

    /// Shaped like the Hub's own reply. The same repo appears four times at different levels of
    /// detail, exactly as the aggregate catalogue delivers it — one studio publishes the full
    /// transcription contract, others only a name — so the app's dedupe is exercised rather than
    /// assumed. A bare row sits both before and after the rich one: neither "first seen" nor "last
    /// seen" may win, because both would lose the engine and cache state the Subtitle form needs.
    private static let transcriptionFixture = Data(#"{"available":true,"default_model":"mlx-community/whisper-large-v3-turbo","models":[{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","size_gb":1.6},{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","engine":"whisper"},{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo","size_gb":1.6,"note":"Recommended","engine":"subtitle-mlx","languages":"Multilingual","weight_format":"safetensors","supports_word_timestamps":true,"supports_segment_timestamps":true,"recommended":true,"cached":true,"lane_contract":{"schema":"studiohub.lane-contract.v1","modality":"transcription","operation":"audio.transcription","parameters":[{"name":"file","type":"string","required":true,"max_bytes":2147483648,"enum":[".wav",".mp3",".m4a",".flac",".mp4",".mov"],"note":"multipart upload; the filename extension must be one of the listed values"},{"name":"model","type":"string","required":false,"enum":["whisper-turbo","whisper-base"],"default":"whisper-turbo"},{"name":"word_timestamps","type":"boolean","required":false,"default":false},{"name":"language","type":"string","required":false,"enum":["en","km","zh"]}],"artifact":{"modality":"transcription","media_type":"application/json","extension":"json","per_item":1}}},{"repo":"mlx-community/whisper-large-v3-turbo","label":"Whisper turbo"},{"repo":"moonshine-ai/moonshine-base","label":"Moonshine","size_gb":0.25,"note":"Pilot","engine":"moonshine","languages":"English","supports_word_timestamps":false,"recommended":false,"cached":true},{"repo":"fixture/whisper-unpackaged","label":"Whisper fixture with no Hub package","size_gb":1.0,"engine":"whisper-mlx","languages":"Multilingual","supports_word_timestamps":true,"recommended":false,"cached":true}]}"#.utf8)

    private static let packagesFixture = Data(#"{"models":[{"package_id":"synthetic-custom-voice","model_id":"mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-flux","model_id":"fixture/flux","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-narrow-image","model_id":"fixture/narrow-image","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-paused","model_id":"fixture/paused","state":"files_verified","dispatch_state":"eligible","files_state":"files_verified","runtime_state":"ready","os_state":"ready","chip_state":"ready","worker_state":"running","paused":true,"paused_reason":"RuntimeError: SystemExit: 1","paused_studio":"voice"},{"package_id":"fixture-sung-music","model_id":"fixture/sung-music","state":"files_verified","dispatch_state":"eligible","files_state":"files_verified","runtime_state":"ready","os_state":"ready","chip_state":"ready","worker_state":"running"},{"package_id":"whisper-turbo","model_id":"mlx-community/whisper-large-v3-turbo","state":"files_verified","dispatch_state":"eligible"},{"package_id":"moonshine-base","model_id":"moonshine-ai/moonshine-base","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-zimage","model_id":"fixture/zimage","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-second-music","model_id":"fixture/second-music","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-second-preset","model_id":"fixture/second-preset","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-ltx-video","model_id":"fixture/ltx-video","state":"files_verified","dispatch_state":"eligible"},{"package_id":"qwen3-5-9b","model_id":"Qwen/Qwen3.5-9B","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-chat-conversational","model_id":"fixture/chat-conversational","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-chat-family","model_id":"fixture/chat-family","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-chat-annotated","model_id":"fixture/chat-annotated","state":"files_verified","dispatch_state":"eligible"},{"package_id":"voxcpm2-4bit","model_id":"mlx-community/VoxCPM2-4bit","state":"files_verified","dispatch_state":"ineligible","files_state":"files_verified","runtime_state":"ready","os_state":"unsupported","chip_state":"ready","worker_state":"running","minimum_macos":"15.0","macos_version":"14.5"},{"package_id":"chatterbox-8bit","model_id":"mlx-community/chatterbox-8bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fish-audio-s2-pro-8bit","model_id":"mlx-community/fish-audio-s2-pro-8bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"khmer-tts-mms","model_id":"khmerttsopensource/khmer-tts","state":"files_verified","dispatch_state":"eligible"},{"package_id":"khmer-tts-onnx","model_id":"sengtha/khmer-tts-female-v2","state":"files_verified","dispatch_state":"eligible"},{"package_id":"pocket-tts-4bit","model_id":"mlx-community/pocket-tts-4bit","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-unwired-voice","model_id":"fixture/unwired-voice","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-two-mode-voice","model_id":"fixture/two-mode-voice","state":"files_verified","dispatch_state":"eligible"},{"package_id":"fixture-unreported-modes","model_id":"fixture/unreported-modes","state":"files_verified","dispatch_state":"eligible"}]}"#.utf8)

    /// The catalogue exactly as the fixture serves it, so a test can derive what the app must
    /// show from the data the app was given instead of restating it by hand. A hand-written
    /// expected list is what let the Audio picker keep rejecting families it had never heard of.
    static var catalogModels: [[String: Any]] {
        ((try? JSONSerialization.jsonObject(with: catalogFixture)) as? [String: Any])?["models"]
            as? [[String: Any]] ?? []
    }

    // MARK: The Image worker's loading-size setting

    /// The selection the fixture's stub Image worker is holding. A test changes it only by PUTting
    /// a mode, the way the app does.
    static var imageLoadingSelection = "auto"
    static var imageLoadingPuts: [String] = []
    static var lastImageLoadingToken = ""
    static func imageLoadingPutModes() -> [String] {
        lock.lock(); defer { lock.unlock() }; return imageLoadingPuts
    }
    static func imageLoadingToken() -> String {
        lock.lock(); defer { lock.unlock() }; return lastImageLoadingToken
    }
    static func resetImageLoadingPuts() {
        lock.lock(); defer { lock.unlock() }; imageLoadingPuts = []
    }

    /// What the fixture's stub Image worker will load each model as, for each selection it can
    /// hold. Written out as test data rather than computed: this fixture exists to check what the
    /// app renders, and a copy of the worker's threshold rule here would only agree with itself.
    static let imageLoadingEffective: [String: [String: String]] = [
        // The fixture's Z-Image row mirrors the shipped Z-Image-Turbo 6B MLX 4-bit, which has a
        // bounded configuration; its FLUX row mirrors FLUX.2 Klein 4B MLX 4-bit, which has one
        // loading mode on purpose and says so in its own reason.
        "fixture/zimage": ["auto": "low-ram", "low": "low-ram", "full": "default"],
        "fixture/flux": ["auto": "default", "low": "default", "full": "default"],
    ]

    // MARK: The Subtitle worker's below-tier warning

    /// The repo whose catalogue row carries the Subtitle worker's `memory_warning`.
    static let subtitleWarningRepo = "mlx-community/whisper-large-v3-turbo"
    /// The worker's own sentence, from `engine.host_eligibility()` on a host below the tier.
    static let subtitleWarningSentence = "This Mac has 8 GB of unified memory. Whisper is qualified on 8 GB, so transcription here is below the qualified tier and may be slow if other Studios are holding models."
    /// Whether the fixture's catalogue row carries it. `nil` is the machine that is at or above the
    /// tier: the worker publishes nothing at all, not a reassuring message.
    static var subtitleMemoryWarning: String? = subtitleWarningSentence
    static func setSubtitleMemoryWarning(_ value: String?) {
        lock.lock(); defer { lock.unlock() }; subtitleMemoryWarning = value
    }

    /// The catalogue exactly as the fixture's Hub serves it: the fixed rows, with each image row's
    /// `loading_modes` projected for the selection the stub worker holds — the way the real
    /// worker's catalogue is re-derived after a PUT — so the app's post-change catalogue refresh
    /// shows the new `selected` and `effective`, and a single-mode model's reason gains the clause
    /// saying the machine-wide setting does not apply to it.
    static var catalogFixture: Data {
        lock.lock()
        let selection = imageLoadingSelection
        let subtitleWarning = subtitleMemoryWarning
        let stored = modelDefaultsChosen
        lock.unlock()
        let models = catalogBaseModels.map { entry -> [String: Any] in
            var patched = entry
            // The Image and Music workers publish `generation_defaults` **resolved**: the
            // operator's saved default over the model's own, which is the value a job that omits
            // the parameter is given. The fixture resolves it from the same store its defaults
            // route reads, so the page the app seeds and the editor it saves through cannot
            // disagree about what is in force.
            if let repo = entry["repo"] as? String,
               let contract = entry["lane_contract"] as? [String: Any],
               let modality = contract["modality"] as? String,
               let values = stored["\(modality)/\(repo)"], !values.isEmpty,
               var defaults = entry["generation_defaults"] as? [String: Any] {
                for (name, value) in values {
                    // The one value that is not the same shape in both places: the lane takes the
                    // aspect ratio and the job takes the pixels it means.
                    if name == "aspect_ratio", let ratio = value as? String,
                       let sizes = entry["sizes"] as? [[String: Any]],
                       let size = sizes.first(where: { $0["aspect_ratio"] as? String == ratio }) {
                        defaults["width"] = size["width"]
                        defaults["height"] = size["height"]
                        continue
                    }
                    defaults[name] = value
                }
                patched["generation_defaults"] = defaults
            }
            // The Subtitle worker's own below-tier sentence, on its own catalogue row and under
            // the same top-level key Image, Music and Chat use. `null` when this Mac is at or
            // above the tier, which is the case the page must render nothing for.
            if (entry["repo"] as? String) == subtitleWarningRepo {
                patched["memory_warning"] = subtitleWarning ?? NSNull()
            }
            guard (entry["capabilities"] as? [String])?.contains("txt2img") == true,
                  let repo = entry["repo"] as? String,
                  var modes = entry["loading_modes"] as? [String: Any] else { return patched }
            modes["selected"] = selection
            modes["effective"] = imageLoadingEffective[repo]?[selection] ?? modes["effective"]
            if modes["supported"] as? Bool == false, let reason = modes["reason"] as? String {
                modes["reason"] = selection == "auto" ? reason
                    : reason + " The setting is '\(selection)' on this Mac; this model still loads its default configuration."
            }
            patched["loading_modes"] = modes
            return patched
        }
        return try! JSONSerialization.data(withJSONObject: ["models": models])
    }

    private static var catalogBaseModels: [[String: Any]] {
        ((try? JSONSerialization.jsonObject(with: catalogBaseFixture)) as? [String: Any])?["models"]
            as? [[String: Any]] ?? []
    }

    /// `GET`/`PUT /studio/image/api/loading-mode`, built from the same catalogue rows the Hub
    /// serves so the two surfaces cannot disagree about a model.
    static func imageLoadingStatus() -> Data {
        lock.lock()
        let selection = imageLoadingSelection
        lock.unlock()
        var rows: [[String: Any]] = []
        for entry in catalogModels {
            guard let repo = entry["repo"] as? String,
                  let modes = entry["loading_modes"] as? [String: Any] else { continue }
            rows.append([
                "repo": repo,
                "supported": modes["supported"] ?? false,
                "selected": selection,
                "effective": modes["effective"] ?? NSNull(),
                "threshold_bytes": modes["threshold_bytes"] ?? NSNull(),
                "reason": modes["reason"] ?? NSNull(),
            ])
        }
        let payload: [String: Any] = [
            "mode": selection,
            "default_mode": "auto",
            "options": [
                ["id": "auto", "label": "Auto",
                 "detail": "Each model decides from this Mac's unified memory: its bounded low-RAM configuration below the model's own threshold, its default configuration at or above it."],
                ["id": "low", "label": "Low RAM",
                 "detail": "Force the bounded configuration on every model that declares one. A model that declares none has a single loading mode: it loads unchanged, and its catalogue entry says this setting does not apply."],
                ["id": "full", "label": "Full",
                 "detail": "Force each model's default configuration whatever this Mac's memory is. A default measured on a larger Mac may exceed a smaller one's memory; the job's own recorded mode shows which configuration ran."],
            ],
            "machine_bytes": 34_359_738_368,
            "models": rows.sorted { ($0["repo"] as? String ?? "") < ($1["repo"] as? String ?? "") },
        ]
        return try! JSONSerialization.data(withJSONObject: payload)
    }


    // MARK: The per-model defaults route (`/studio/<modality>/api/model-defaults`)

    /// The values the stub Studios are holding, keyed `"<modality>/<repo>"` and then by parameter.
    static var modelDefaultsChosen: [String: [String: Any]] = [:]
    static var modelDefaultsRequests: [String] = []
    static var lastModelDefaultsBody: [String: Any]?

    static func modelDefaultsRequestLog() -> [String] {
        lock.lock(); defer { lock.unlock() }; return modelDefaultsRequests
    }
    static func lastModelDefaultsRequest() -> [String: Any]? {
        lock.lock(); defer { lock.unlock() }; return lastModelDefaultsBody
    }
    static func resetModelDefaultsRequests() {
        lock.lock(); defer { lock.unlock() }; modelDefaultsRequests = []
    }
    /// Store a value the way an earlier session would have: without validating it now, so a bound
    /// that has since tightened is exercised rather than refused at the door.
    static func seedStoredDefault(modality: String, repo: String, values: [String: Any]) {
        lock.lock(); defer { lock.unlock() }
        modelDefaultsChosen["\(modality)/\(repo)"] = values
    }
    static func clearStoredDefaults() {
        lock.lock(); defer { lock.unlock() }; modelDefaultsChosen = [:]
    }

    /// One `(repo, parameters)` pair per model whose own lane contract names this modality.
    ///
    /// The catalogue rows and the transcription availability rows are both searched, because that
    /// is where the two producers publish their contracts — the same two places the app reads.
    static func modelDefaultsModels(modality: String) -> [(repo: String, parameters: [[String: Any]])] {
        var rows: [(repo: String, parameters: [[String: Any]])] = []
        for entry in catalogModels + transcriptionModels {
            guard let repo = entry["repo"] as? String,
                  let contract = entry["lane_contract"] as? [String: Any],
                  contract["modality"] as? String == modality,
                  let parameters = contract["parameters"] as? [[String: Any]] else { continue }
            rows.append((repo, parameters))
        }
        var seen = Set<String>()
        return rows.filter { seen.insert($0.repo).inserted }
    }

    /// `components/model_defaults.editable`: what the lane publishes, minus what it requires and
    /// minus a parameter whose enum holds exactly one value.
    static func modelDefaultsEditable(_ parameters: [[String: Any]]) -> [[String: Any]] {
        parameters.filter {
            guard ($0["required"] as? Bool) != true,
                  (($0["name"] as? String) ?? "").isEmpty == false else { return false }
            if let options = $0["enum"] as? [Any] { return options.count > 1 }
            return true
        }
    }

    /// `components/model_defaults.stated`: every parameter the lane gives a default for, including
    /// the ones an operator is offered no control for.
    static func modelDefaultsStated(_ parameters: [[String: Any]]) -> [String: Any] {
        var stated: [String: Any] = [:]
        for parameter in parameters where (parameter["required"] as? Bool) != true {
            guard let name = parameter["name"] as? String, let value = parameter["default"] else { continue }
            stated[name] = value
        }
        return stated
    }

    private static func isJSONBool(_ value: Any) -> Bool {
        guard let number = value as? NSNumber else { return false }
        return CFGetTypeID(number) == CFBooleanGetTypeID()
    }
    private static func isJSONNumber(_ value: Any) -> Bool {
        guard let number = value as? NSNumber else { return false }
        return CFGetTypeID(number) != CFBooleanGetTypeID()
    }
    private static func sameJSON(_ left: Any, _ right: Any) -> Bool {
        guard let left = left as? NSObject, let right = right as? NSObject else {
            return String(describing: left) == String(describing: right)
        }
        return left.isEqual(right)
    }

    /// `components/model_defaults._check`, in the fixture: the reason one value is refused, or nil.
    static func modelDefaultsRefusal(_ parameter: [String: Any], _ value: Any) -> String? {
        let name = parameter["name"] as? String ?? "?"
        let type = parameter["type"] as? String ?? ""
        if value is NSNull { return "\(name): expected \(type), got null" }
        switch type {
        case "integer":
            guard isJSONNumber(value), let number = value as? NSNumber,
                  number.doubleValue == number.doubleValue.rounded() else {
                return "\(name): expected integer, got \(Swift.type(of: value))"
            }
        case "number":
            guard isJSONNumber(value) else { return "\(name): expected number, got \(Swift.type(of: value))" }
        case "string":
            guard value is String else { return "\(name): expected string, got \(Swift.type(of: value))" }
        case "boolean":
            guard isJSONBool(value) else { return "\(name): expected boolean, got \(Swift.type(of: value))" }
        case "array":
            guard value is [Any] else { return "\(name): expected array, got \(Swift.type(of: value))" }
        case "object":
            guard value is [String: Any] else { return "\(name): expected object, got \(Swift.type(of: value))" }
        default:
            return "\(name): the lane publishes type \(type), which this setting has no rule for"
        }
        if let options = parameter["enum"] as? [Any],
           !options.contains(where: { sameJSON($0, value) }) {
            return "\(name): \(value) is not one of \(options.map { String(describing: $0) })"
        }
        if isJSONNumber(value), let number = (value as? NSNumber)?.doubleValue {
            if let minimum = (parameter["minimum"] as? NSNumber)?.doubleValue, number < minimum {
                return "\(name): \(value) is below the lane's minimum of \(minimum)"
            }
            if let maximum = (parameter["maximum"] as? NSNumber)?.doubleValue, number > maximum {
                return "\(name): \(value) is above the lane's maximum of \(maximum)"
            }
        }
        if let limit = parameter["max_characters"] as? Int, let text = value as? String,
           text.count > limit {
            return "\(name): \(text.count) characters, above the lane's limit of \(limit)"
        }
        return nil
    }

    /// The payload the Studio's route answers with, built from the lane contracts the fixture
    /// already serves — so the fixture cannot disagree with itself about which parameters exist.
    static func modelDefaultsPayload(modality: String) -> Data {
        lock.lock()
        let stored = modelDefaultsChosen
        lock.unlock()
        var rows: [[String: Any]] = []
        for model in modelDefaultsModels(modality: modality) {
            let editable = modelDefaultsEditable(model.parameters)
            let stated = modelDefaultsStated(model.parameters)
            var inForce = stated
            var notUsed: [String: String] = [:]
            for (name, value) in (stored["\(modality)/\(model.repo)"] ?? [:]).sorted(by: { $0.key < $1.key }) {
                guard let parameter = editable.first(where: { $0["name"] as? String == name }) else {
                    notUsed[name] = "this lane publishes no such settable parameter, so there is nothing to default"
                    continue
                }
                if let reason = modelDefaultsRefusal(parameter, value) {
                    notUsed[name] = reason
                    continue
                }
                inForce[name] = value
            }
            var chosen: [String: Any] = [:]
            for parameter in editable {
                guard let name = parameter["name"] as? String, let value = inForce[name] else { continue }
                if stated[name] == nil || !sameJSON(stated[name]!, value) { chosen[name] = value }
            }
            rows.append(["repo": model.repo, "defaults": inForce, "stated": stated,
                         "chosen": chosen, "not_used": notUsed, "parameters": editable])
        }
        let payload: [String: Any] = [
            "models": rows.sorted { ($0["repo"] as? String ?? "") < ($1["repo"] as? String ?? "") },
            "settings_file": "model_defaults.json",
        ]
        return try! JSONSerialization.data(withJSONObject: payload)
    }

    /// GET, PUT and DELETE for the defaults route, exactly as a Studio answers them: a PUT whose
    /// values are refused at any parameter answers 422 with `detail` keyed by parameter.
    private func answerModelDefaults(modality: String, method: String) {
        let body = method == "GET" ? nil
            : (try? JSONSerialization.jsonObject(with: requestBody())) as? [String: Any]
        Self.lock.lock()
        Self.modelDefaultsRequests.append("\(method) \(modality)")
        if method != "GET" { Self.lastModelDefaultsBody = body }
        Self.lock.unlock()
        let repo = body?["repo"] as? String ?? ""
        if method == "PUT", let values = body?["values"] as? [String: Any] {
            var refusals: [String: String] = [:]
            let editable = Self.modelDefaultsEditable(
                Self.modelDefaultsModels(modality: modality).first { $0.repo == repo }?.parameters ?? [])
            for (name, value) in values {
                guard let parameter = editable.first(where: { $0["name"] as? String == name }) else {
                    refusals[name] = "this lane publishes no such settable parameter, so there is nothing to default"
                    continue
                }
                if let reason = Self.modelDefaultsRefusal(parameter, value) { refusals[name] = reason }
            }
            if !refusals.isEmpty {
                let payload = try! JSONSerialization.data(withJSONObject: ["detail": refusals])
                finish((422, "application/json", payload))
                return
            }
            Self.lock.lock()
            Self.modelDefaultsChosen["\(modality)/\(repo)"] = values.filter { $0.value is NSNull == false }
            Self.lock.unlock()
            finish((200, "application/json", Self.modelDefaultsPayload(modality: modality)))
            return
        }
        if method == "DELETE" {
            Self.lock.lock()
            Self.modelDefaultsChosen.removeValue(forKey: "\(modality)/\(repo)")
            Self.lock.unlock()
            finish((200, "application/json", Self.modelDefaultsPayload(modality: modality)))
            return
        }
        finish((200, "application/json", Self.modelDefaultsPayload(modality: modality)))
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

    override func stopLoading() {
        // A held voice-library read that is torn down before the fixture answers was cancelled.
        guard request.url?.path == "/studio/voice/api/voices" else { return }
        Self.lock.lock()
        if Self.heldVoiceListHolding { Self.voiceListCancellations += 1 }
        Self.lock.unlock()
    }
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
                                   bytes: Int? = nil,
                                   filesSha256: String? = nil) -> [String: Any] {
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
    if let filesSha256 {
        // What a release with a per-file manifest adds, inside the signed bytes.
        descriptor["files_sha256"] = filesSha256
        descriptor["files_count"] = 93_798
        descriptor["files_bytes"] = 5_200_000_000
    }
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
            // Video counts as a form too: the Hub's Video page offers a picker for any model it
            // publishes a video lane for, and this predicate is what says which rows the app must
            // list. Leaving it out is how a whole modality goes invisible while the assertion
            // still passes.
            let isVideo = !capabilities.isDisjoint(with: ["img2video", "txt2video", "video"])
                || (entry["hub_modality"] as? String)?.lowercased() == "video"
            let hasForm = capabilities.contains("tts") || capabilities.contains("txt2img")
                || capabilities.contains("music") || isChat || isVideo
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
        // The preset roster is carried by the model that publishes it, and the chosen voice follows
        // the model. It used to live in a global list the Audio page read directly, which is how a
        // Khmer model would have been offered Qwen's voice names.
        //
        // What it must NOT do is *seed a choice*. Selecting this model used to fill the field with
        // its first voice ("Ryan"), which read as an untouched picker while the app supplied a name
        // on every preset job — so a voice stored for this model in the Models tab could never
        // reach the engine. The field stays "not chosen" now, and the picker shows the value in
        // force through `audioSpeakerInForce` without claiming it as the operator's own.
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
        require(store.audioSpeaker == "",
                "selecting a model must leave the voice unchosen, got: \(store.audioSpeaker)")
        require(store.audioSpeakerForRequest == nil,
                "an unchosen voice must send no preset_speaker, or the stored default is unreachable")
        // A name from another family's roster is cleared rather than kept, and clearing is not
        // seeding: the field goes back to not-chosen, not to the new model's first voice.
        store.audioSpeaker = "Ryan"
        store.audioModel = "khmerttsopensource/khmer-tts"
        require(store.audioSpeaker == "",
                "a voice from another family's roster must be cleared, got: \(store.audioSpeaker)")
        require(store.audioSpeakerForRequest == nil,
                "and a model whose lane publishes no preset_speaker is never sent one")
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

        // A failed batch is remembered too - but only while the Hub says nothing in it is waiting, so
        // a Retry (which queues it again) is seen on the next poll rather than hidden by the cache.
        FixtureProtocol.mutate { FixtureProtocol.errorBatchMode = "settled" }
        await degraded.refresh()
        let withFailure = FixtureProtocol.jobDetailCount()
        await degraded.refresh()
        require(FixtureProtocol.jobDetailCount() == withFailure,
                "a settled failed batch must be fetched once, not on every poll")
        FixtureProtocol.mutate { FixtureProtocol.errorBatchMode = "retried" }
        await degraded.refresh()
        require(FixtureProtocol.jobDetailCount() > withFailure
                    && degraded.imageJobs.first { $0.id == "err-job" }?.state == "queued",
                "a retried batch must be read again, not served from the cache")
        FixtureProtocol.mutate { FixtureProtocol.errorBatchMode = "off" }

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

        // B31: the duration formatter is exact, and never contradicts itself.
        //
        // The owner: "when it took seconds. it showed seconds. but if it took more than 1 minute. it
        // just shows the minute without the exact seconds." He was reading a real defect: the
        // minutes were truncated while the seconds were rounded, and the seconds were dropped
        // entirely when the rounded remainder was zero, so 119.7 s reported "1 min" - hiding 59.7
        // seconds - and a run that finished at 60.4 s reported "1 min" as well.
        //
        // Exact inputs to exact strings, every boundary the old implementation got wrong, driven
        // against the real function rather than against a copy of it.
        let clockTable: [(Double, String)] = [
            (0.0, "0.0 s"),          // no duration at all reads without inventing one
            (0.4, "0.4 s"),
            (52.2, "52.2 s"),
            (59.4, "59.4 s"),        // the last tenth below the boundary stays in seconds
            (59.9, "1 min 0 s"),     // rounded once: 59.9 is a minute, and it says what it is
            (59.97, "1 min 0 s"),    // never "60.0 s" - the scale comes from the same rounding
            (60.0, "1 min 0 s"),     // seconds are stated, not dropped
            (60.4, "1 min 0 s"),
            (119.7, "2 min 0 s"),    // was "1 min", hiding 59.7 s
            (120.4, "2 min 0 s"),
            (125.0, "2 min 5 s"),
            (3599.6, "1 h 0 min 0 s"),   // was "59 min 59 s" at best, and "59 min" at worst
            (3600.4, "1 h 0 min 0 s"),
            (7623.0, "2 h 7 min 3 s"),   // a two-hour render must not read 127 min 3 s
        ]
        for (seconds, expected) in clockTable {
            require(NativeJob.clock(seconds) == expected,
                    "clock(\(seconds)) read \(NativeJob.clock(seconds)), expected \(expected)")
        }
        // The property behind the table: whatever scale it picks, no rendering may lose more than
        // half a second of the real duration. This is what the old one broke - "1 min" for 119.7 s
        // was off by 59.7 - and it is checked across every boundary rather than at a few points.
        var probe = 0.0
        while probe < 8000 {
            let shown = NativeJob.clock(probe)
            require(clockSeconds(shown) != nil, "clock(\(probe)) read as unreadable: \(shown)")
            let lost = abs((clockSeconds(shown) ?? 0) - probe)
            require(lost <= 0.5 + 1e-9,
                    "clock(\(probe)) lost \(lost) s by reading \(shown)")
            probe += 0.37
        }
        // A duration no machine will ever report must not trap on the way to the screen.
        require(NativeJob.clock(.infinity) == "0.0 s" && NativeJob.clock(1e300) == "99 h 59 min 59 s",
                "a nonsense duration must read as one, not crash the app")

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
        // **The screen and the re-join loop must read the same address.** They are two properties —
        // `controllerAddress` is what `groupStatus` and `refreshControllerHealth` read, while
        // `storedMembership.controllerAddress` drives the re-join — and the startup path published
        // only the second. A node whose re-join was working perfectly therefore came up saying
        // "Joined to a controller" in one line and "Not joined to a controller" in the next, with a
        // grey dot and a Connections page that had no address to check at all. The owner reported
        // exactly that from a second Mac. Asserted as *agreement* between the two, so fixing one
        // and not the other still fails.
        // **The screen and the re-join loop must read the same address**, and this is asserted on a
        // restore that produces **no join** — because that is the only way to see the restore path
        // at all. The assertion above it cannot catch this: a successful join publishes the address
        // from the join target, which here is the same string, so the defect is invisible behind
        // it. That is exactly how it reached the owner: on a Mac whose controller was reachable the
        // two agreed by luck, and on the Mac where the identity read came back late they did not.
        //
        // The address differs from the previous block on purpose, so a published value that is
        // simply *stale* fails here too.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.failNextIdentityRead()
        await store.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.99:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address)))
        require(!FixtureProtocol.fleetRequests().contains("join"),
                "a failed identity read must not guess that this Mac is a node")
        // The two properties the UI reads: the standing line and the health note. Both must agree
        // with the membership the app restored, or the screen contradicts itself — "Joined to a
        // controller" beside "Not joined to a controller", with a grey dot and no address for the
        // Connections page to check.
        require(store.controllerAddress == "192.168.1.99:8080",
                "a restored membership must publish its address to the screen, got: "
                    + "\(store.controllerAddress.isEmpty ? "empty" : store.controllerAddress)")
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
            boundHosts: LocalAddresses.current().map(\.address),
            nodeCredential: FixtureProtocol.issuedNodeCredential)
        await store.leaveGroup()
        require(FixtureProtocol.fleetRequests().contains("leave"),
                "leaving must reach the Hub so it can mint its own credential: \(FixtureProtocol.fleetRequests())")
        // **With its own credential, not the group token (A8).** A shared Fleet token identifies
        // nobody, so the controller refuses every removal made with one — which is exactly the
        // failure the old fixture's flat 200 hid.
        require(FixtureProtocol.removalTokens().last == FixtureProtocol.issuedNodeCredential,
                "leaving must present this Mac's own node credential, got: \(FixtureProtocol.removalTokens())")
        require(FixtureProtocol.fleetRequests().contains("dropped-from-controller"),
                "leaving must ask the controller to drop this Mac with the credential it holds: \(FixtureProtocol.fleetRequests())")
        require(store.storedMembership == nil, "a Mac that left must not keep re-joining its old group")
        require(store.nodeCredential == nil,
                "leaving must take this Mac's own node credential off it too")
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

        // The modes a voice model offers come from the producer's own `modes` array, in its order,
        // and the app no longer derives them: a derivation is a second source of truth free to
        // disagree with the engine that answers the request, and it showed one tab for a model that
        // publishes two. The decoder is driven here; the fixture-driven half is asserted against
        // the catalogue the app is actually given.
        require(NativeWorkspaceStore.voiceModes(["cloning", "design"]) == [.cloning, .design],
                "both published modes must be offered, in the producer's order")
        require(NativeWorkspaceStore.voiceModes(["cloning", "preset"]) == [.cloning, .preset],
                "a model that clones and presets is offered in both tabs")
        require(NativeWorkspaceStore.voiceModes(["design", "cloning"]) == [.design, .cloning],
                "the producer's order is the display order")
        require(NativeWorkspaceStore.voiceModes(["preset", "preset"]) == [.preset],
                "a repeated mode is one tab")
        // A mode this build cannot build a request for is dropped, not guessed at - and a row that
        // published only unknown modes therefore reports none, which the page says out loud.
        require(NativeWorkspaceStore.voiceModes(["hologram"]) == [],
                "an unknown mode must not become a known one")
        require(NativeWorkspaceStore.voiceModes(["cloning", "hologram"]) == [.cloning],
                "a known mode beside an unknown one still stands")
        require(NativeWorkspaceStore.voiceModes([]) == [] && NativeWorkspaceStore.voiceModes(nil) == [],
                "no published modes means no tabs, never a default form")

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
        require(store.fleetNodes?.summary.total == 3, "every joined node must be listed")
        require(store.fleetNodes?.nodes.first?.state == "reachable", "a reachable node must say so")
        // **Every published field is decoded and drawn (M21/B4).** `role`, `studios`, `version`,
        // `joined_at` and `last_seen_at` were on the wire and read by nothing, so the roster row was
        // a name and an address.
        let firstNode = store.fleetNodes?.nodes.first
        require(firstNode?.role == "agent", "a node's part in the group must decode")
        require(firstNode?.studios?.map(\.id) == ["image", "voice"],
                "what a node runs must decode: \(String(describing: firstNode?.studios))")
        require(firstNode?.version == "0.4.0", "which build a node runs must decode")
        require(firstNode?.joinedAt == 1_750_000_000.0, "when a node joined must decode")
        require(firstNode?.lastSeenAt == 1_750_003_600.0, "when a node was last seen must decode")
        require(firstNode?.hasCredential == true,
                "whether a credential is held must decode, so a pre-A8 row can say so")
        require(store.fleetNodes?.nodes.last?.isReachable == false,
                "a node that was joined but has since gone must not read as available")
        // A node that answered and refused this controller's credential is its own state, with its
        // own count: folded into "unreachable", the operator goes looking at the network.
        let unauthorized = store.fleetNodes?.nodes.last
        require(unauthorized?.state == "unauthorized" && unauthorized?.isUnauthorized == true,
                "a refused credential must decode as its own state")
        require(store.fleetNodes?.summary.unauthorized == 1,
                "the unauthorized count must decode, not just exist on the wire")
        // The evicted machines are published so re-admitting one does not need its id remembered.
        require(store.fleetNodes?.summary.removed == 1, "the removed count must decode")
        require(store.fleetNodes?.removed?.first?.id == "sold-mac",
                "every removed Mac must decode")
        require(store.fleetNodes?.removed?.first?.removedBy == "operator",
                "who removed a Mac must decode")
        require(store.fleetNodes?.removed?.first?.removedAt == 1_790_563_000.0,
                "when a Mac was removed must decode")
        // `last seen` is the difference between a Mac that is off and one that is gone for a month.
        require(NativeWorkspaceStore.ago(1_750_003_600.0, now: Date(timeIntervalSince1970: 1_750_003_660)) == "1 min ago",
                "a last-seen stamp must be readable as an age")
        require(NativeWorkspaceStore.ago(nil) == nil && NativeWorkspaceStore.ago(0) == nil,
                "a Mac with no stamp must report none, never a date")
        let muchLater = Date(timeIntervalSince1970: 1_900_000_000)
        require(muchLater.timeIntervalSince1970 > 0
                && NativeWorkspaceStore.ago(1_750_003_600, now: muchLater)?.hasSuffix("days ago") == true,
                "a stamp from long ago must read in days")
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
        // Runnable, not merely answering (F2): per machine, from /api/hub/model-health.
        require(store.fleetReadiness["local"] == NativeMachineReadiness(ready: 2, downloadRequired: 1, blocked: 0),
                "this Mac's readiness: \(String(describing: store.fleetReadiness["local"]))")
        require(store.fleetReadiness["local"]?.cannotRunAnything == false, "a Mac with ready models can run")
        require(store.fleetReadiness["studio-3"] == NativeMachineReadiness(ready: 0, downloadRequired: 1, blocked: 1),
                "an unknown state counts as nothing: \(String(describing: store.fleetReadiness["studio-3"]))")
        require(store.fleetReadiness["studio-3"]?.cannotRunAnything == true,
                "a node that answers but has no model it can run must not read as healthy")
        require(store.fleetReadiness["studio-3"]?.summary == "0 models ready · 1 need download · 1 blocked",
                "the row says why: \(store.fleetReadiness["studio-3"]?.summary ?? "nil")")
        require(store.fleetRefreshedAt != nil && store.fleetReadError == nil && !store.fleetRefreshing,
                "a clean read is dated and reports no error")
        let sorted = NativeWorkspaceStore.unreachableFirst(store.fleetOverview!.machines, reachable: \.reachable)
        require(sorted.map(\.machine) == ["studio-3", "local"], "unreachable machines float to the top")
        require(NativeWorkspaceStore.unreachableFirst([1, 2, 3, 4], reachable: { $0 % 2 == 0 }) == [1, 3, 2, 4],
                "the order within each group is the Hub's")
        // One table of Macs: the overview's row and the roster's row for the same Mac are joined on
        // its id, so no Mac is listed twice and none from either side is dropped.
        let tableRows = NativeFleetRow.rows(overview: store.fleetOverview, nodes: store.fleetNodes)
        let tableIDs = tableRows.map(\.id)
        require(Set(tableIDs).count == tableIDs.count, "a Mac must be one row, not two: \(tableIDs)")
        for machine in store.fleetOverview?.machines ?? [] {
            require(tableIDs.contains(machine.machine), "overview Mac \(machine.machine) missing from the table")
        }
        for node in store.fleetNodes?.nodes ?? [] {
            require(tableIDs.contains(node.id), "roster node \(node.id) missing from the table")
        }
        // What a Mac reports about itself reaches the table: its disk word, what starts with it, and
        // the job it is on - the three things the old two lists never showed.
        let ownRow = tableRows.first { $0.id == "local" }
        require(ownRow?.machine?.disk?.state == "green" && ownRow?.machine?.diskTier == .ok,
                "the governor's disk object must decode and read as ok")
        require(ownRow?.currentJob?.jobId == "job-1" && ownRow?.currentJob?.label == "storystudio",
                "the job a Mac is on must decode: \(String(describing: ownRow?.currentJob))")
        require(ownRow?.state(readiness: nil) == .working, "a Mac with a current job reads as working")
        require(ownRow?.startupText == "Services 3 of 4 up" && ownRow?.startupNeedsAttention == true,
                "a Studio that is down is a startup state worth a look: \(String(describing: ownRow?.startupText))")
        require(tableRows.first { $0.id == "studio-3" }?.state(readiness: nil) == .offline,
                "a Mac that does not answer reads as offline")
        require(tableRows.first.map { !$0.isReachable } ?? true, "a Mac that does not answer sorts first")
        // Disk tiers are the governor's: amber below 12 % free, red below 6 %, stop below 5 GB; its own
        // word wins when it gives one. Memory (owner decision 2026-10-02): amber below 20 % free, red
        // below 10 % or below 1.5 GB.
        require(NativeResourceTier.disk(state: "green", freeGB: 1, totalGB: 100) == .ok, "the governor's word wins")
        require(NativeResourceTier.disk(state: "stop", freeGB: 400, totalGB: 500) == .bad, "stop is red")
        require(NativeResourceTier.disk(state: nil, freeGB: 200, totalGB: 460) == .ok, "43 % free is fine")
        require(NativeResourceTier.disk(state: nil, freeGB: 50, totalGB: 460) == .warn, "11 % free is amber")
        require(NativeResourceTier.disk(state: nil, freeGB: 20, totalGB: 460) == .bad, "4 % free is red")
        require(NativeResourceTier.disk(state: nil, freeGB: 4, totalGB: 2000) == .bad, "under 5 GB is red whatever the size")
        require(NativeResourceTier.disk(state: nil, freeGB: nil, totalGB: nil) == .unknown, "no figure is unknown, never green")
        require(NativeResourceTier.memory(availableGB: 8, totalGB: 16) == .ok, "half free is fine")
        require(NativeResourceTier.memory(availableGB: 3, totalGB: 16) == .warn, "19 % free is amber")
        require(NativeResourceTier.memory(availableGB: 1.4, totalGB: 8) == .bad, "under 1.5 GB is red")
        require(NativeResourceTier.memory(availableGB: 1.2, totalGB: 24) == .bad, "5 % free is red")
        require(NativeResourceTier.memory(availableGB: nil, totalGB: 16) == .unknown, "no figure is unknown")
        // A read that fails keeps the last good list and says what failed (F10).
        FixtureProtocol.mutate { FixtureProtocol.overviewFails = true }
        await store.refreshFleetNodes()
        require(store.fleetOverview?.summary.total == 2, "a failed read must not wipe the last good overview")
        require(store.fleetReadError?.contains("fleet overview") == true,
                "the error must name what could not be read: \(store.fleetReadError ?? "nil")")
        FixtureProtocol.mutate { FixtureProtocol.overviewFails = false }
        await store.refreshFleetNodes()
        require(store.fleetReadError == nil, "the next clean read clears the error")
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

        // **"Send here" is not offered to a Mac that cannot choose.** Joining a controller never set
        // the app's role, so `role != "agent"` was true on a Mac that had joined somebody, and the
        // pin it then set was refused by the Hub with "no machine named X is in this fleet".
        require(!NativeWorkspaceStore.canPinWork(role: "standalone", credentialOrigin: "adopted",
                                                 handedOverTo: nil),
                "a Mac that adopted someone else's credential is a node, whatever its role says")
        require(!NativeWorkspaceStore.canPinWork(role: "agent", credentialOrigin: "minted",
                                                 handedOverTo: nil),
                "a Mac set as a Node does not choose where work runs")
        require(!NativeWorkspaceStore.canPinWork(role: "controller", credentialOrigin: "minted",
                                                 handedOverTo: "http://192.168.1.21:8080"),
                "a retired controller is not dispatching anything")
        require(NativeWorkspaceStore.canPinWork(role: "controller", credentialOrigin: "minted",
                                                handedOverTo: nil),
                "a controller chooses where work runs")
        require(NativeWorkspaceStore.canPinWork(role: "standalone", credentialOrigin: nil,
                                                handedOverTo: nil),
                "a Mac on its own chooses where work runs")
        require(!NativeWorkspaceStore.canPinWork(role: nil, credentialOrigin: nil, handedOverTo: nil),
                "a Hub that has not answered yet is not a licence to pin work")

        // **A pin whose machine left the fleet is cleared.** It used to be unclearable, and the Hub
        // then refused every job with a sentence about a machine that is no longer listed.
        store.routingPin = "machine:gone-mac"
        await store.refreshFleetNodes()
        require(store.routingPin == nil,
                "a pin on a Mac that is no longer in the fleet must be cleared, got: \(store.routingPin ?? "nil")")
        require(store.fleetNote?.contains("no longer in this fleet") == true,
                "clearing a pin must say so, got: \(store.fleetNote ?? "nil")")
        // A pin on a machine that is merely off is left alone: it is still in the fleet, and the
        // pin is a promise about where work goes when it comes back.
        store.routingPin = "machine:studio-3"
        await store.refreshFleetNodes()
        require(store.routingPin == "machine:studio-3",
                "a pin on an unreachable Mac that is still in the fleet must survive")

        require(store.fleetNodes!.nodes.first { $0.id == "studio-3" }?.neverConnected == true,
                "the roster's never_connected flag must be decoded")
        require(store.fleetNodes!.nodes.first { $0.id == "studio-2" }?.neverConnected == nil,
                "a row that does not carry the flag reads as nil, not false")

        // Removing the pinned Mac is the other way a pin loses its machine.
        await store.leaveNode(store.fleetNodes!.nodes.first { $0.id == "studio-3" }!)
        require(FixtureProtocol.fleetRequests().contains("leave:studio-3"),
                "removing a node must reach the Hub: \(FixtureProtocol.fleetRequests())")
        require(store.routingPin == nil,
                "removing the Mac a pin names must clear the pin, got: \(store.routingPin ?? "nil")")
        // A pin on this Mac itself is not a machine that left.
        store.routingPin = "machine:local"
        await store.refreshFleetNodes()
        require(store.routingPin == "machine:local", "a pin on this Mac is not a stale pin")
        store.routingPin = nil

        // **Re-admitting is the way back**, and it is the operator's action on the controller.
        await store.readmitNode(store.fleetNodes!.removed!.first!)
        require(FixtureProtocol.readmits() == ["sold-mac"],
                "re-admitting must reach the Hub with the machine's id: \(FixtureProtocol.readmits())")
        require(store.fleetNote?.contains("Sold Mac") == true,
                "re-admitting needs a visible result, got: \(store.fleetNote ?? "nil")")

        // ---------------------------------------------------------------------------------------
        // A8/A2 — this Mac's own credential, a real eviction, and an honest "Leave this group".
        // ---------------------------------------------------------------------------------------
        let fleetStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await fleetStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                 dataDirectory: temporary.appendingPathComponent("node-credential"),
                                 token: "native-test-token")
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()

        // The join answer carries this Mac's own credential. It has to be kept, handed to the Hub
        // (which presents it on the reads it makes itself) and presented on every later call home.
        FixtureProtocol.setSelfRemoval(nil)
        await fleetStore.joinController(address: "192.168.1.21:8080", token: "fleet-token-value",
                                        boundHosts: LocalAddresses.current().map(\.address))
        // From here the fixture refuses a re-join that presents no credential, exactly as the real
        // controller refuses one (H12). The first join of a fresh id is legal, so this is turned on
        // only after it.
        FixtureProtocol.setJoinRequiresCredential(true)
        require(fleetStore.nodeCredential == FixtureProtocol.issuedNodeCredential,
                "the credential from the join answer must be kept: \(String(describing: fleetStore.nodeCredential))")
        require(FixtureProtocol.adoptPayload()?["node_credential"] as? String == FixtureProtocol.issuedNodeCredential,
                "the credential must be handed to the local Hub over adopt, got: \(String(describing: FixtureProtocol.adoptPayload()))")

        // A re-join presents it — this is what makes a per-node eviction meaningful (A8), and what
        // lets a Mac re-join after the group's Fleet token was rotated out from under it.
        FixtureProtocol.resetFleetRequests()
        await fleetStore.joinController(address: "192.168.1.21:8080", token: "rotated-fleet-token",
                                        boundHosts: LocalAddresses.current().map(\.address))
        require(FixtureProtocol.joinTokens().last == FixtureProtocol.issuedNodeCredential,
                "a re-join must present this Mac's own credential, got: \(FixtureProtocol.joinTokens())")
        require(FixtureProtocol.joinBodyCredential() == FixtureProtocol.issuedNodeCredential,
                "the credential must also travel in the body, so the request proves itself")
        require(FixtureProtocol.fleetRequests().filter { $0 == "join" }.count == 1,
                "a re-join with a working credential must not be retried: \(FixtureProtocol.fleetRequests())")

        // The group token alone no longer re-joins an id that already holds a credential: the
        // controller refuses it 400, and the app must report the controller's own reason rather
        // than retrying for ever.
        let noCredentialStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await noCredentialStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                        dataDirectory: temporary.appendingPathComponent("no-credential"),
                                        token: "native-test-token")
        FixtureProtocol.setCredentialOrigin("adopted")
        await noCredentialStore.joinController(address: "192.168.1.21:8080", token: "fleet-token-value",
                                               boundHosts: LocalAddresses.current().map(\.address))
        require(noCredentialStore.fleetNote?.contains("Two Macs cannot share one node id") == true,
                "a takeover refusal must quote the controller, got: \(noCredentialStore.fleetNote ?? "nil")")
        noCredentialStore.disconnect()
        FixtureProtocol.setJoinRequiresCredential(false)

        // ---------------------------------------------------------------------------------------
        // 0.6.101 — the Join button sends what the operator typed.
        //
        // The owner, in the field: a healthy node and a healthy controller, the controller's own
        // Fleet token pasted into the box, and every press refused with *"a credential that is
        // neither this Mac's Hub token nor the Fleet token this Mac holds … the two Macs hold
        // different Fleet tokens."* Both tokens were correct and both worked when used directly —
        // because the app never sent the one he typed.
        //
        // `nodeOriginCredential` prefers what this Mac already holds: its own node credential, else
        // the group token it stored. That is right for the startup re-join loop, where the Mac is
        // re-announcing itself. It is wrong for the Join button, and the retry that was supposed to
        // save it was gated on `heldNodeCredential` — which a Mac holding a stored *group* token and
        // no per-node credential (every row from before A8, i.e. every Mac being re-joined here)
        // does not have. So the typed token never left the Mac.
        //
        // This is exactly that state: a stored group token, no node credential, operator presses
        // Join. Before the fix the wire carried `token-from-an-earlier-group`; it must now carry the
        // typed value.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        let typedStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await typedStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                 dataDirectory: temporary.appendingPathComponent("typed-token"),
                                 token: "native-test-token")
        typedStore.storedMembership = NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.9:47873",
            token: "token-from-an-earlier-group",
            boundHosts: LocalAddresses.current().map(\.address))
        require(typedStore.nodeCredential == nil,
                "this fixture is only the failing case while there is no per-node credential")
        await typedStore.joinController(address: "192.168.1.21:8080", token: "the-typed-fleet-token",
                                        boundHosts: LocalAddresses.current().map(\.address))
        // `.last`, not `.first`: the fixture's join list is the whole suite's history, so the
        // attempt this block just made is the newest one.
        require(FixtureProtocol.joinTokens().last == "the-typed-fleet-token",
                "the Join button must put what the operator typed on the wire, got: "
                + "\(FixtureProtocol.joinTokens())")
        typedStore.disconnect()

        // **And a Mac that holds its own node credential still presents it**, even when the
        // operator typed something else. That credential is the one value that names this Mac
        // alone, so it is what survives a group-wide Fleet-token rotation — the property the first
        // version of this fix broke, and the reason the existing "a re-join must present this
        // Mac's own credential" assertion above went red. The typed token is still tried, but only
        // as the single retry.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        let holderStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await holderStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                  dataDirectory: temporary.appendingPathComponent("holder-token"),
                                  token: "native-test-token")
        holderStore.storedMembership = NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080",
            token: "an-old-group-token",
            boundHosts: LocalAddresses.current().map(\.address),
            nodeCredential: "this-macs-own-node-credential")
        await holderStore.joinController(address: "192.168.1.21:8080", token: "the-typed-fleet-token",
                                         boundHosts: LocalAddresses.current().map(\.address))
        require(FixtureProtocol.joinTokens().last == "this-macs-own-node-credential",
                "a Mac holding its own node credential must present it, not the typed group token; "
                + "that credential is what survives a rotation. got: \(FixtureProtocol.joinTokens())")
        holderStore.disconnect()
        // **A re-admitted Mac still holds the credential from before**, which the controller no
        // longer knows: the same 401 path, but with a working outcome after the retry. The fresh
        // credential replaces the stale one, and the Mac is a member again.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.setStaleJoinCredential("old-node-credential")
        FixtureProtocol.issuedNodeCredential = "fresh-node-credential-456"
        let readmittedStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await readmittedStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                      dataDirectory: temporary.appendingPathComponent("readmitted"),
                                      token: "native-test-token")
        await readmittedStore.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address),
            nodeCredential: "old-node-credential"))
        require(Array(FixtureProtocol.joinTokens().suffix(2)) == ["old-node-credential", "fleet-token-value"],
                "a stale credential must be retried once with the group token: \(FixtureProtocol.joinTokens())")
        require(readmittedStore.nodeCredential == "fresh-node-credential-456",
                "the re-join must take the fresh credential: \(String(describing: readmittedStore.nodeCredential))")
        require(readmittedStore.controllerRemoval == nil,
                "a Mac that re-joined is not a Mac that was removed")
        FixtureProtocol.setStaleJoinCredential(nil)
        FixtureProtocol.issuedNodeCredential = "node-credential-abc123"
        readmittedStore.disconnect()

        // **A removal is terminal.** The controller answers 403 with its own sentence; the app must
        // stop the re-join loop, drop the group credential this Mac holds, and say who removed it
        // and when — with the date the controller gave, not the date this app read it.
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.setJoinRemoval(FixtureProtocol.removalSentence)
        await fleetStore.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address),
            nodeCredential: FixtureProtocol.issuedNodeCredential))
        let removal = fleetStore.controllerRemoval
        require(removal != nil, "a 403 removal must be recorded")
        require(removal?.date == "2026-09-28",
                "the date must come from the controller's sentence, got: \(String(describing: removal?.date))")
        require(removal?.clearedGroupCredential == true,
                "removing must drop the group credential this Mac holds (the local leave): \(FixtureProtocol.fleetRequests())")
        require(FixtureProtocol.fleetRequests().contains("leave"),
                "a removed Mac must stop being a node locally: \(FixtureProtocol.fleetRequests())")
        require(fleetStore.nodeCredential == nil,
                "the revoked credential must not be kept")
        require(fleetStore.storedMembership == nil,
                "a removed Mac must not keep re-joining the controller that removed it")
        // The sentence the page shows must name the date and where re-admitting happens.
        require(FleetCopy.isRemoval(FixtureProtocol.removalSentence), "the removal sentence must be recognised")
        let removalNotice = FleetCopy.removalNotice(date: removal?.date, address: "192.168.1.21:8080")
        require(removalNotice.contains("removed this Mac on 2026-09-28"),
                "the notice must say what happened and when: \(removalNotice)")
        require(removalNotice.contains("Re-admitting it is a decision on that controller"),
                "the notice must say re-admitting happens on the controller: \(removalNotice)")
        require(removalNotice.contains("has stopped trying to re-join"),
                "the notice must say the loop stopped: \(removalNotice)")
        require(FleetCopy.removalDate(in: FixtureProtocol.removalSentence) == "2026-09-28",
                "the date is read out of the controller's own words")
        require(FleetCopy.removalDate(in: "refused for another reason") == nil,
                "a refusal that is not a removal must not invent a date")
        // The removal must survive a later lifecycle refresh: the loop is stopped, not merely
        // interrupted, and the startup attempt is not allowed to start a new one.
        FixtureProtocol.resetFleetRequests()
        await fleetStore.refresh()
        try? await Task.sleep(for: .milliseconds(150))
        require(!FixtureProtocol.fleetRequests().contains("join"),
                "a removed Mac re-joined after its removal: \(FixtureProtocol.fleetRequests())")
        require(fleetStore.controllerRemoval != nil, "the removal notice must stay until dismissed")
        fleetStore.dismissControllerRemoval()
        require(fleetStore.controllerRemoval == nil, "the notice must be dismissible")
        FixtureProtocol.setJoinRemoval(nil)
        fleetStore.disconnect()

        // **Leaving reports what the controller answered, not what the button hoped.** The old
        // fixture answered a flat 200 to every removal, which is why an app that could never remove
        // this Mac passed this suite.
        let refusedLeaveStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await refusedLeaveStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                   dataDirectory: temporary.appendingPathComponent("leaving-node"),
                                   token: "native-test-token")
        FixtureProtocol.setCredentialOrigin("adopted")
        // 1. Its own credential is unknown to the controller (already revoked): 401, then the group
        //    token is refused 403 because a shared token removes nothing. Both are reported.
        FixtureProtocol.setSelfRemoval(401, detail: "this Mac's fleet credential was refused")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.removalTokensList = []
        await refusedLeaveStore.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address),
            nodeCredential: FixtureProtocol.issuedNodeCredential))
        await refusedLeaveStore.leaveGroup()
        require(FixtureProtocol.removalTokens().count == 2,
                "a refused credential must be retried once with the group token: \(FixtureProtocol.removalTokens())")
        require(refusedLeaveStore.fleetNote?.contains("did not recognise this Mac's own credential") == true,
                "a 401 on this Mac's own credential must say so: \(refusedLeaveStore.fleetNote ?? "nil")")
        require(refusedLeaveStore.fleetNote?.contains("left the group") == true,
                "leaving is still this Mac's decision: \(refusedLeaveStore.fleetNote ?? "nil")")
        require(refusedLeaveStore.storedMembership == nil && refusedLeaveStore.nodeCredential == nil,
                "leaving must take both credentials off this Mac")
        // 2. A different refusal: the controller's own reason is quoted, and the page never says
        //    the removal happened.
        FixtureProtocol.setCredentialOrigin("adopted")
        FixtureProtocol.setSelfRemoval(403, detail: "a fleet credential may remove only the Mac that holds it")
        await refusedLeaveStore.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address),
            nodeCredential: FixtureProtocol.issuedNodeCredential))
        await refusedLeaveStore.leaveGroup()
        require(refusedLeaveStore.fleetNote?.contains("refused to remove this Mac") == true,
                "a refusal must be reported as one: \(refusedLeaveStore.fleetNote ?? "nil")")
        require(refusedLeaveStore.fleetNote?.contains("only the Mac that holds it") == true,
                "the controller's own reason must be quoted verbatim: \(refusedLeaveStore.fleetNote ?? "nil")")
        FixtureProtocol.setSelfRemoval(nil)
        refusedLeaveStore.disconnect()

        // ---------------------------------------------------------------------------------------
        // Symmetric removal. A removal on the controller reaches this Mac on its own tick; a leave
        // this Mac could not deliver is kept and retried until the controller answers.
        // ---------------------------------------------------------------------------------------
        func nodeStore(_ name: String) async -> NativeWorkspaceStore {
            let store = NativeWorkspaceStore(sessionConfiguration: configuration)
            await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                dataDirectory: temporary.appendingPathComponent(name),
                                token: "native-test-token")
            FixtureProtocol.setCredentialOrigin("adopted")
            FixtureProtocol.setJoinRemoval(nil)
            await store.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
                controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
                boundHosts: LocalAddresses.current().map(\.address),
                nodeCredential: FixtureProtocol.issuedNodeCredential))
            return store
        }
        let t0 = Date()
        let tickStore = await nodeStore("membership-tick")
        FixtureProtocol.setSelfRemoval(nil)
        FixtureProtocol.setStatus(nil)
        FixtureProtocol.resetStatusCalls()
        await tickStore.membershipTick(now: t0)
        require(FixtureProtocol.statusCalls().count == 1,
                "the tick must ask the controller once: \(FixtureProtocol.statusCalls())")
        require(FixtureProtocol.statusCalls().first?.hasPrefix(FixtureProtocol.issuedNodeCredential + "|") == true,
                "the tick must present this Mac's own credential: \(FixtureProtocol.statusCalls())")
        require(FixtureProtocol.statusCalls().first?.contains("name=") == true,
                "the tick carries the current label so a rename reaches the roster: \(FixtureProtocol.statusCalls())")
        require(tickStore.storedMembership != nil && tickStore.controllerRemoval == nil,
                "a Mac that is still listed stays a member")
        await tickStore.membershipTick(now: t0.addingTimeInterval(10))
        require(FixtureProtocol.statusCalls().count == 1, "the check is once a minute, not every poll")
        // A blip: an unreadable answer or a server error is not evidence of anything.
        FixtureProtocol.setStatus(503, body: #"{"detail":"busy"}"#)
        for step in 1...5 { await tickStore.membershipTick(now: t0.addingTimeInterval(Double(step) * 60)) }
        require(tickStore.storedMembership != nil, "a controller that errors must not clear a membership")
        // `unknown` clears only after three in a row, and a `member` in between starts the count over.
        let unknown = #"{"id":"controller-local","state":"unknown"}"#
        FixtureProtocol.setStatus(200, body: unknown)
        await tickStore.membershipTick(now: t0.addingTimeInterval(400))
        await tickStore.membershipTick(now: t0.addingTimeInterval(460))
        require(tickStore.storedMembership != nil, "two unknown answers must not clear the membership")
        FixtureProtocol.setStatus(nil)
        await tickStore.membershipTick(now: t0.addingTimeInterval(520))
        FixtureProtocol.setStatus(200, body: unknown)
        await tickStore.membershipTick(now: t0.addingTimeInterval(580))
        await tickStore.membershipTick(now: t0.addingTimeInterval(640))
        require(tickStore.storedMembership != nil, "a member answer in between must reset the unknown count")
        await tickStore.membershipTick(now: t0.addingTimeInterval(700))
        require(tickStore.storedMembership == nil && tickStore.controllerRemoval != nil,
                "three unknown answers in a row must leave the group")
        require(tickStore.controllerHealthNote?.contains("no longer lists this Mac") == true,
                "the Mac says why it left: \(tickStore.controllerHealthNote ?? "nil")")
        tickStore.disconnect()

        // The poll tick is what carries the check: without the hook a Mac would never ask.
        let pollStore = await nodeStore("membership-poll")
        FixtureProtocol.setStatus(nil)
        FixtureProtocol.resetStatusCalls()
        await pollStore.pollOnce(now: t0)
        require(FixtureProtocol.statusCalls().count == 1,
                "pollOnce must run the membership check: \(FixtureProtocol.statusCalls())")
        pollStore.disconnect()

        // Removed on the controller: gone here too, with the controller's own date, on the next tick.
        let removedStore = await nodeStore("membership-removed")
        FixtureProtocol.resetFleetRequests()
        FixtureProtocol.setStatus(200, body: #"{"id":"controller-local","state":"removed","detail":"\#(FixtureProtocol.removalSentence)"}"#)
        await removedStore.membershipTick(now: t0)
        require(removedStore.controllerRemoval?.date == "2026-09-28",
                "a removal on the controller must reach this Mac: \(String(describing: removedStore.controllerRemoval))")
        require(FixtureProtocol.fleetRequests().contains("leave"),
                "a removed Mac must stop being a node locally: \(FixtureProtocol.fleetRequests())")
        require(removedStore.storedMembership == nil && removedStore.nodeCredential == nil,
                "a removed Mac must hold neither credential")
        removedStore.disconnect()

        // A revoked node credential is refused 403 with the removal sentence (not a bare 401).
        let revokedStore = await nodeStore("membership-revoked")
        FixtureProtocol.setStatus(403, body: #"{"detail":"\#(FixtureProtocol.removalSentence)"}"#)
        await revokedStore.membershipTick(now: t0)
        require(revokedStore.controllerRemoval != nil, "a 403 removal sentence must clear the membership")
        revokedStore.disconnect()
        FixtureProtocol.setStatus(nil)

        // A leave the controller did not hear is kept, survives a restart, and is retried each tick
        // until the controller answers (2xx/401/403/404).
        let leaverStore = await nodeStore("pending-leave")
        FixtureProtocol.setSelfRemoval(503, detail: "controller busy")
        await leaverStore.leaveGroup()
        require(leaverStore.pendingLeave?.nodeID == "controller-local",
                "an undelivered leave must be kept: \(String(describing: leaverStore.pendingLeave))")
        require(leaverStore.fleetNote?.contains("keep telling it") == true,
                "the owner is told it will be retried: \(leaverStore.fleetNote ?? "nil")")
        let pendingFile = temporary.appendingPathComponent("pending-leave").appendingPathComponent("native-pending-leave.json")
        require(FileManager.default.fileExists(atPath: pendingFile.path), "the pending leave must be on disk")
        leaverStore.disconnect()
        let restarted = NativeWorkspaceStore(sessionConfiguration: configuration)
        await restarted.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                dataDirectory: temporary.appendingPathComponent("pending-leave"),
                                token: "native-test-token")
        require(restarted.pendingLeave?.credential == FixtureProtocol.issuedNodeCredential,
                "the pending leave must survive a restart")
        FixtureProtocol.resetFleetRequests()
        await restarted.membershipTick(now: t0)
        require(restarted.pendingLeave != nil, "a controller that still errors keeps the leave pending")
        FixtureProtocol.setSelfRemoval(nil)
        await restarted.membershipTick(now: t0.addingTimeInterval(61))
        require(FixtureProtocol.fleetRequests().contains("dropped-from-controller"),
                "the retry must reach the controller: \(FixtureProtocol.fleetRequests())")
        require(restarted.pendingLeave == nil && !FileManager.default.fileExists(atPath: pendingFile.path),
                "a delivered leave is dropped, in memory and on disk")
        restarted.disconnect()

        // 404 is an answer too (already gone), and a leave past its lifetime is abandoned unsent.
        let goneStore = await nodeStore("pending-gone")
        FixtureProtocol.setSelfRemoval(503, detail: "down")
        await goneStore.leaveGroup()
        FixtureProtocol.setSelfRemoval(404, detail: "unknown node")
        await goneStore.membershipTick(now: t0)
        require(goneStore.pendingLeave == nil, "a 404 means the controller no longer lists it: retry stops")
        FixtureProtocol.setSelfRemoval(503, detail: "down")
        await goneStore.adoptStartupMembership(NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: LocalAddresses.current().map(\.address),
            nodeCredential: FixtureProtocol.issuedNodeCredential))
        await goneStore.leaveGroup()
        require(goneStore.pendingLeave != nil, "precondition: a leave is pending")
        FixtureProtocol.resetFleetRequests()
        await goneStore.membershipTick(now: t0.addingTimeInterval(15 * 24 * 3600))
        require(goneStore.pendingLeave == nil && !FixtureProtocol.fleetRequests().contains("delete:controller-local"),
                "a leave past its 14 days is dropped without another attempt")
        FixtureProtocol.setSelfRemoval(nil)
        goneStore.disconnect()

        // The fixture's own fidelity, so the next test cannot be green by construction: the
        // controller refuses a removal of a machine that is not the one asking.
        var foreign = URLRequest(url: URL(string: "http://192.168.1.21:8080/api/hub/fleet/nodes/studio-9")!)
        foreign.httpMethod = "DELETE"
        foreign.setValue(FixtureProtocol.issuedNodeCredential, forHTTPHeaderField: "X-Hub-Token")
        let foreignSession = URLSession(configuration: configuration)
        if let (_, foreignResponse) = try? await foreignSession.data(for: foreign),
           let status = (foreignResponse as? HTTPURLResponse)?.statusCode {
            require(status == 403,
                    "the fixture must refuse removing a different Mac with 403, got: \(status)")
        } else {
            require(false, "the fixture did not answer the foreign removal")
        }
        foreignSession.invalidateAndCancel()

        // ---------------------------------------------------------------------------------------
        // B2/H11 — the connection light asks with a credential, and a refusal is its own state.
        // ---------------------------------------------------------------------------------------
        let healthStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await healthStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                  dataDirectory: temporary.appendingPathComponent("health"),
                                  token: "native-test-token")
        healthStore.controllerAddress = "192.168.1.21:8080"
        healthStore.storedMembership = NativeWorkspaceStore.FleetMembership(
            controllerAddress: "192.168.1.21:8080", token: "fleet-token-value",
            boundHosts: [], nodeCredential: FixtureProtocol.issuedNodeCredential)
        FixtureProtocol.setResourcesStatus(nil)
        await healthStore.refreshControllerHealth()
        require(healthStore.controllerReachable == true,
                "an authenticated read that succeeds must read as connected")
        require(!healthStore.controllerCredentialRefused, "a successful read is not a refusal")
        require(FixtureProtocol.resourcesToken() == "fleet-token-value",
                "the read must present the group credential this route checks, got: \(FixtureProtocol.resourcesToken())")
        require(healthStore.controllerHealthNote?.contains("Apple M4") == true,
                "the answer's own facts must be shown: \(healthStore.controllerHealthNote ?? "nil")")
        // The controller is there and refuses this Mac: that is not "unreachable", and it is not
        // green either. This is the state 52 nodes were missing.
        FixtureProtocol.setResourcesStatus(401)
        await healthStore.refreshControllerHealth()
        require(healthStore.controllerReachable == false,
                "a refused credential must not read as connected")
        require(healthStore.controllerCredentialRefused,
                "a refused credential must be its own state, not folded into unreachable")
        require(healthStore.controllerHealthNote?.contains("refused this Mac's credential") == true,
                "the refusal must be said in words: \(healthStore.controllerHealthNote ?? "nil")")
        require(healthStore.controllerHealthNote?.contains("not dispatchable") == true,
                "the consequence must be said: \(healthStore.controllerHealthNote ?? "nil")")
        // The membership's token is a snapshot; a rotation this Mac did not perform replaces the
        // Hub's own token without telling this app. The read asks for the live value, or a healthy
        // Mac reads as refused after every rotation.
        FixtureProtocol.setResourcesStatus(nil)
        healthStore.fleetTokenProvider = { "live-fleet-token" }
        await healthStore.refreshControllerHealth()
        require(FixtureProtocol.resourcesToken() == "live-fleet-token",
                "the read must present the Hub's current token, got: \(FixtureProtocol.resourcesToken())")
        require(healthStore.controllerReachable == true && !healthStore.controllerCredentialRefused,
                "a live credential that is accepted must read as connected")
        healthStore.fleetTokenProvider = nil
        // A controller that answers with something else is neither of those two states.
        FixtureProtocol.setResourcesStatus(500)
        await healthStore.refreshControllerHealth()
        require(healthStore.controllerReachable == false && !healthStore.controllerCredentialRefused,
                "an error is neither a refusal nor a live connection")
        FixtureProtocol.setResourcesStatus(nil)
        healthStore.disconnect()

        // ---------------------------------------------------------------------------------------
        // A4/A3 — rotation and handover report every Mac by name, and a partial result is never
        // reported as success.
        // ---------------------------------------------------------------------------------------
        let groupStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await groupStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                 dataDirectory: temporary.appendingPathComponent("group-ops"),
                                 token: "native-test-token")
        FixtureProtocol.setCredentialOrigin("minted")
        await groupStore.refreshFleetNodes()
        FixtureProtocol.resetFleetRequests()
        await groupStore.rotateFleetToken()
        let rotation = groupStore.fleetOperation
        require(rotation?.kind == .rotation, "a rotation must be reported as one")
        require(rotation?.total == 2 && rotation?.verified == 1 && rotation?.manual == 1,
                "every count must come from the Hub's answer: \(String(describing: rotation))")
        require(rotation?.isComplete == false, "one Mac refused the old credential: this is not complete")
        require(rotation?.followed == ["studio-2"] && rotation?.unfinished == ["studio-3"],
                "the Macs must be named individually: \(String(describing: rotation))")
        require(rotation?.outcomes["studio-3"] == "refused the old credential",
                "the page must say *why* a Mac did not follow, not only that it did not: \(String(describing: rotation?.outcomes))")
        require(rotation?.outcomes["studio-2"] == "followed", "a Mac that followed must say so")
        require(rotation?.token == "new-fleet-token-abcdefghijklmnop",
                "the new token must be shown so every member and GatewayKH can be given it")
        require(rotation?.summary.contains("1 of 2 Macs followed") == true,
                "the result must lead with what actually happened: \(rotation?.summary ?? "nil")")
        require(rotation?.summary.contains("SSH over Tailscale") == true,
                "the fallback for a Mac that did not follow must be named: \(rotation?.summary ?? "nil")")
        require(rotation?.summary.contains("credential-rotation-decision.md") == true,
                "the fallback must point at the runbook: \(rotation?.summary ?? "nil")")
        require(groupStore.connectionNotice?.kind == .failure,
                "a partial rotation must not be reported as success")
        require(groupStore.fleetMachineName("studio-3") == "Studio 3",
                "a machine id in the result must be shown as its name")
        require(groupStore.fleetMachineName("controller-local") == "Studio Mini",
                "this Mac's own id must be shown as its label, never as the key")
        require(groupStore.fleetMachineName("never-heard-of") == "never-heard-of",
                "an id nobody has a label for is the only case that falls back to the id")
        require(FixtureProtocol.fleetRequests().contains("rotate"),
                "the rotation must go to this Mac's own Hub: \(FixtureProtocol.fleetRequests())")

        // A handover carries the new controller's address and token, and retires this controller.
        FixtureProtocol.resetFleetRequests()
        await groupStore.handOverFleet(to: "192.168.1.21:8080", token: "new-controller-token-abcdefghijkl")
        let handover = groupStore.fleetOperation
        require(FixtureProtocol.fleetRequests().contains("handover"),
                "the handover must reach the Hub: \(FixtureProtocol.fleetRequests())")
        let handoverBody = FixtureProtocol.syncBodies().last
        require(handoverBody?["controller_url"] as? String == "http://192.168.1.21:8080",
                "the new controller's address must be sent as the Hub stores it, got: \(String(describing: handoverBody))")
        require(handoverBody?["token"] as? String == "new-controller-token-abcdefghijkl",
                "the new controller's token must be sent once, in this request")
        require(handover?.kind == .handover && handover?.target == "http://192.168.1.21:8080",
                "a handover must be reported as one, naming where the group went")
        require(handover?.isComplete == false, "a Mac that did not follow means the handover is not complete")
        require(groupStore.connectionNotice?.kind == .failure,
                "a partial handover must never be reported as success")
        // The controller is retired by its own handover: the Hub's answer says so, and the page
        // must report it rather than showing a controller that still takes work.
        await groupStore.refreshConnection()
        require(groupStore.controllerIdentity?.settings.handedOverTo == "http://192.168.1.21:8080",
                "a handed-over controller must report where the group went: \(String(describing: groupStore.controllerIdentity?.settings.handedOverTo))")
        require(groupStore.controllerIdentity?.settings.handedOverAt != nil,
                "a handed-over controller must report when")
        require(!NativeWorkspaceStore.canPinWork(
                    role: "controller", credentialOrigin: "minted",
                    handedOverTo: groupStore.controllerIdentity?.settings.handedOverTo),
                "a retired controller must not offer to take work")
        FixtureProtocol.setHandedOver(to: nil)
        groupStore.disconnect()

        // The order of names and the shape of a complete answer.
        require(FleetCopy.unfinishedNotice(total: 2, verified: 2, manual: 0, pending: 0) == nil,
                "a complete result needs no warning")
        require(FleetCopy.unfinishedNotice(total: 3, verified: 1, manual: 1, pending: 1)?
                    .contains("1 refused the old credential") == true,
                "a manual Mac must be counted in words")
        require(FleetCopy.unfinishedNotice(total: 3, verified: 1, manual: 1, pending: 1)?
                    .contains("1 could not be reached") == true,
                "an unreachable Mac must be counted in words")
        require(FleetCopy.handoverFallback.contains("SSH over Tailscale")
                && FleetCopy.handoverFallback.contains("credential-rotation-decision.md"),
                "the fallback names the mechanism and the runbook")
        // A rotation that only missed Macs that were off says they catch up by themselves.
        require(FleetCopy.unfinishedNotice(total: 2, verified: 1, manual: 0, pending: 1, rotation: true)?
                    .contains("given the new one automatically") == true,
                "a rotation's unreachable Mac must be told it is not stranded")
        require(FleetCopy.unfinishedNotice(total: 2, verified: 1, manual: 1, pending: 0, rotation: true)?
                    .contains("SSH over Tailscale") == true,
                "a Mac that refused the old credential still needs the manual path")
        require(FleetCopy.previousTokenNotice(count: 0, until: nil) == nil,
                "nothing is said when no node is on the previous token")
        require(FleetCopy.previousTokenNotice(count: 3, until: nil)?
                    .hasPrefix("3 nodes are still on the previous Fleet token.") == true,
                "the count of nodes still on the previous token is shown")
        require(FleetCopy.previousTokenNotice(count: 1, until: 1_800_000_000)?
                    .hasPrefix("1 node is still on the previous Fleet token.") == true,
                "one node reads in the singular")
        let rotationSummaryJSON = Data(#"{"total":2,"reachable":1,"unreachable":1,"error":0,"on_previous_token":1,"previous_token_until":1800000000.0}"#.utf8)
        let rotationDecoder = JSONDecoder()
        rotationDecoder.keyDecodingStrategy = .convertFromSnakeCase
        let rotationSummary = try? rotationDecoder.decode(NativeFleetSummary.self, from: rotationSummaryJSON)
        require(rotationSummary?.onPreviousToken == 1 && rotationSummary?.previousTokenUntil == 1_800_000_000,
                "the roster summary must carry the nodes still on the previous token")

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
        // the clone possible - and selects the voice, because "Use voice" means this is the voice
        // the Audio page clones from now that the picker there is the only way to choose one.
        let usedSaved = await store.useVoice(narrator)
        require(usedSaved, "a voice with a transcript must be usable")
        require(store.audioReferenceTranscript == "The exact words in the clip",
                "using a voice must carry its transcript")
        require(store.audioVoiceLibraryID == "voice-1",
                "using a voice must select it for the Audio page's picker: \(store.audioVoiceLibraryID ?? "nil")")
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

        // A cancelled voice-library read is not a failed one.
        //
        // The Audio page's Clone tab reads this library from a `.task(id:)`, and SwiftUI cancels
        // that task the moment its id changes — a tab change, or the connection reconciling after
        // the page appears. The cancellation reaches the store as `URLError.cancelled` (-999), and
        // the catch used to treat any error as a broken library: it wiped the list held in memory
        // and published "the voice library is unavailable on this build of the local engine". That
        // is why the Clone tab arrived with an empty, disabled picker while the Voices page — whose
        // task is keyed on the connection alone, and so is never cancelled this way — listed the
        // very same voices correctly. Measured here by holding the read open, cancelling it
        // mid-flight and checking what the store kept.
        await store.refreshVoices()
        require(store.voices.count == 2 && store.voiceError == nil,
                "the baseline read must have the fixture's voices and no error")
        FixtureProtocol.holdVoiceList()
        let keyedReload = Task { @MainActor in await store.refreshVoices() }
        for _ in 0..<200 where !FixtureProtocol.voiceListHasStarted() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        require(FixtureProtocol.voiceListHasStarted(), "the held voice-library read never started")
        keyedReload.cancel()
        await keyedReload.value
        // `stopLoading` runs on URLSession's own delegate queue, which can reach the fixture a
        // moment *after* the awaiting task resumes, so reading the count once races the callback
        // that increments it. Wait for the effect being asserted, bounded, the same way the "has
        // started" check above does — a test that asserts an asynchronous effect must wait for it.
        for _ in 0..<200 where FixtureProtocol.voiceListCancelCount() < 1 {
            try? await Task.sleep(for: .milliseconds(10))
        }
        require(FixtureProtocol.voiceListCancelCount() >= 1,
                "the in-flight read must have been torn down by the cancellation")
        require(store.voiceError == nil,
                "a cancelled read must not be published as a failure: \(store.voiceError ?? "nil"); voices=\(store.voices.map(\.name)); selectable=\(store.selectableVoices.count)")
        require(store.voices.count == 2,
                "a cancelled read must leave the voices already in hand: \(store.voices.map(\.name))")
        require(store.selectableVoices.count == 1,
                "the picker's list must survive a cancelled reload")
        FixtureProtocol.releaseHeldVoiceList()

        // A real read failure is still a failure, and it names what happened rather than blaming
        // the build: the owner's voices are on disk and a route that answers 500 is not proof that
        // this build cannot read a library.
        FixtureProtocol.setVoiceListFailure(500)
        await store.refreshVoices()
        require(store.voiceError?.contains("500") == true,
                "a real failure must carry the engine's own answer: \(store.voiceError ?? "nil")")
        FixtureProtocol.setVoiceListFailure(nil)
        await store.refreshVoices()
        require(store.voiceError == nil && store.voices.count == 2,
                "a good read must clear the failure and restore the list")

        // The shape cancellation arrives in, pinned so the next catch that sees one does not have
        // to guess. Measured: `URLSession.data(for:)` reports a cancelled task as `URLError` -999
        // (`NSURLErrorCancelled`), and `error is CancellationError` is false for it — so the store
        // recognises both, and must keep treating a refused or timed-out connection as a failure.
        require(NativeWorkspaceStore.isCancellation(URLError(.cancelled)),
                "a cancelled URLSession read must read as cancellation")
        require(NativeWorkspaceStore.isCancellation(CancellationError()),
                "a cancelled Swift task must read as cancellation")
        require(!NativeWorkspaceStore.isCancellation(URLError(.cannotConnectToHost)),
                "a refused connection is a failure, not cancellation")
        require(!NativeWorkspaceStore.isCancellation(URLError(.timedOut)),
                "a timeout is a failure, not cancellation")

        // The Audio page's trigger: read once per connection, not on every visit or tab change.
        // A repeat read on every appearance is what created the cancellation window in the first
        // place, so a loaded library must not be read again.
        let readsAfterLoad = FixtureProtocol.voiceListCalls
        await store.loadVoicesIfNeeded()
        require(FixtureProtocol.voiceListCalls == readsAfterLoad,
                "a library already in hand must not be read again on the next appearance")

        // The library is read as part of joining a Hub, not left to whichever page is opened
        // first: the Clone tab's picker is built from it, and it is the only control that chooses
        // the voice. A read cancelled mid-connect leaves the library unloaded rather than empty, so
        // the next look reads it again instead of showing an empty picker for the rest of the
        // session.
        FixtureProtocol.holdVoiceList()
        let freshStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        let connecting = Task { @MainActor in
            await freshStore.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                                     dataDirectory: temporary.appendingPathComponent("voice-cancel-fresh"),
                                     token: "native-test-token")
        }
        for _ in 0..<200 where !FixtureProtocol.voiceListHasStarted() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        require(FixtureProtocol.voiceListHasStarted(), "joining a Hub must read the voice library")
        connecting.cancel()
        await connecting.value
        require(freshStore.voices.isEmpty && !freshStore.voicesLoaded,
                "a cancelled first read must leave the library unloaded, not empty-and-loaded")
        require(freshStore.voiceError == nil,
                "a cancelled first read must not publish a failure: \(freshStore.voiceError ?? "nil")")
        FixtureProtocol.releaseHeldVoiceList()
        await freshStore.loadVoicesIfNeeded()
        require(freshStore.voices.count == 2 && freshStore.voicesLoaded,
                "the next look at an unloaded library must read it: \(freshStore.voices.map(\.name))")
        freshStore.disconnect()

        // What the picker says in each state it can be in. The line under the picker is the only
        // thing that explains a disabled control, so a state that is silently mislabelled is the
        // bug this pins: "not read yet" is not "you have no voices".
        let emptyStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        require(emptyStore.voicePickerDetail.contains("Reading your saved voices"),
                "an unread library must say it is being read: \(emptyStore.voicePickerDetail)")
        require(store.voicePickerDetail.contains("Clones 1 of your saved voices"),
                "a readable library must say how many voices it can clone: \(store.voicePickerDetail)")
        require(store.voicePickerDetail.contains("No words yet"),
                "a saved voice with no transcript must be named, not filtered out of sight: \(store.voicePickerDetail)")

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

        require(store.storageUsage.first?.wouldDeleteBytes == 3_200_000_000, "the Hub's dry run must decode")
        require(store.storageTrash?.bytes == 1_800_000_000 && store.storageTrash?.days == 15,
                "the Trash Folder's size and emptying period must decode for the storage screen")
        let wouldDelete = await store.storageWouldDelete(maxBytes: 10_000_000_000, retentionDays: 7, cleanupEnabled: true)
        require(wouldDelete == 3_200_000_000, "the editor's figure comes from a preview with the draft limits")
        require(FixtureProtocol.storageRequests().last == "preview" && !FixtureProtocol.storageRequests().contains("put"),
                "the dry run must be a preview call, never a save: \(FixtureProtocol.storageRequests())")
        await store.saveStorageLimits(maxBytes: 120_000_000_000, retentionDays: 14, cleanupEnabled: false)
        require((FixtureProtocol.storageLimitsBody()?["max_bytes"] as? NSNumber)?.int64Value
                    == 120_000_000_000,
                "the new limit must reach the Hub")
        require(FixtureProtocol.storageLimitsBody()?["retention_days"] as? Int == 14,
                "the new retention must reach the Hub")
        require(FixtureProtocol.storageLimitsBody()?["cleanup_enabled"] as? Bool == false,
                "the cleanup choice must reach the Hub")
        require(store.storageNote?.contains("Saved") == true, "a save needs a visible result")

        // Lite Mode: read from this Mac's own Hub, saved to it whole - "each model's own" floor as an
        // explicit null, or the Hub would keep the old figure - and the Hub's own reason for a refusal.
        await store.refreshWorkstation()
        require(store.workstation?.settings.enabled == false && store.workstation?.now == "Off",
                "Lite Mode must read as off from a Hub that has it off, got: \(String(describing: store.workstation))")
        require(store.workstation?.settings.policySentence.hasPrefix("Lite Mode is off") == true,
                "the sentence under the toggles names Lite Mode")
        var lite = store.workstation!.settings
        lite.enabled = true; lite.stepAside = true; lite.quietHours = ["22:00-07:00"]; lite.startFreeGb = 3.5
        let liteSaved = await store.saveWorkstation(lite)
        require(liteSaved, "a valid change is saved: \(store.workstationNote ?? "")")
        let posted = FixtureProtocol.workstationPosts.last ?? [:]
        require(posted["enabled"] as? Bool == true && posted["step_aside"] as? Bool == true
                    && posted["quiet_hours"] as? [String] == ["22:00-07:00"]
                    && (posted["start_free_gb"] as? NSNumber)?.doubleValue == 3.5
                    && posted["hide_menu_bar_icon"] as? Bool == false && posted.count == 8,
                "every setting must reach the Hub under its own key, got: \(posted)")
        require(store.workstation?.now == "Standing by (someone is using this Mac)"
                    && store.workstation?.today == "Today: 2 jobs · 1 step-aside",
                "the saved answer shows what the Mac is doing now, got: \(String(describing: store.workstation?.now))")
        lite.startFreeGb = nil
        _ = await store.saveWorkstation(lite)
        require(FixtureProtocol.workstationPosts.last?["start_free_gb"] is NSNull,
                "back to each model's own floor must be sent as null, got: \(String(describing: FixtureProtocol.workstationPosts.last))")
        lite.idleMinutes = 90
        let liteRefused = await store.saveWorkstation(lite)
        require(liteRefused == false
                    && store.workstationNote?.contains("idle_minutes must be") == true,
                "a refused setting shows the Hub's reason, got: \(store.workstationNote ?? "nil")")
        var sentence = NativeWorkstationSettings()
        sentence.enabled = true
        require(sentence.policySentence == "This Mac takes fleet work at background priority, even while someone is using it.",
                "the Gateway sheet's sentence, off/off")
        sentence.idleOnly = true; sentence.idleMinutes = 1
        require(sentence.policySentence == "This Mac takes fleet work only after 1 minute without input.",
                "the Gateway sheet's sentence, idle only")

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

        // The fifth mode: `auto`, with the Studio's own sentence for what it resolved to here.
        //
        // The mode list renders from the Studio's published `options`, so `auto` appears without a
        // list in the app. What the app must not keep is its own sentence: only the worker knows
        // what `auto` means on this Mac, and "Follows this Mac: releases after 10 idle minutes
        // here" is a fact the local derivation cannot produce.
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)
        await store.refreshMemoryPolicy()
        let autoStudio = store.memoryInventory?.studios.first { $0.id == "subtitle" }
        require(autoStudio?.policy?.mode == "auto", "the Studio's selected mode must be the published one")
        require(autoStudio?.policy?.resolvedOptions.count == 5,
                "every mode the Studio publishes must be selectable, got \(autoStudio?.policy?.resolvedOptions.count ?? -1)")
        require(autoStudio?.policy?.resolvedOptions.map(\.mode).first == "auto",
                "the modes must render in the Studio's own published order")
        let autoOption = autoStudio?.policy?.resolvedOptions.first { $0.mode == "auto" }
        require(autoOption?.summary == "Follows this Mac: releases after 10 idle minutes here",
                "the worker's own sentence must be rendered, not a locally derived one, got: \(autoOption?.summary ?? "nil")")
        require(autoStudio?.policy?.resolution == "Auto resolves to Balanced on this Mac",
                "the row must name the selected mode and what it resolved to, got: \(autoStudio?.policy?.resolution ?? "nil")")
        require(autoStudio?.policy?.machineMode == "balanced" && autoStudio?.policy?.machineBytes == 34359738368,
                "the machine's own answer must come from the published payload")

        // ...and a worker that published no sentence at all still gets a readable row: the derived
        // sentence is the fallback for a payload that stated nothing, not the rule.
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryNoDetailFixture)
        await store.refreshMemoryPolicy()
        let noDetail = store.memoryInventory?.studios.first { $0.id == "subtitle" }?.policy?
            .resolvedOptions.first { $0.mode == "auto" }
        require(noDetail?.summary == "Releases after 10 idle minutes.",
                "a payload with no detail must fall back to the derived sentence, got: \(noDetail?.summary ?? "nil")")
        FixtureProtocol.setMemoryPolicyOverride(nil)
        await store.refreshMemoryPolicy()
        require(store.memoryInventory?.studios.count == 3, "the default fixture must be restored")

        // Connections: the Hub reports its own role and identity.
        await store.refreshConnection()
        require(store.controllerIdentity?.settings.role == "standalone", "the Hub role must be reported truthfully")
        require(store.controllerIdentity?.settings.siteName == "This Mac", "the station name must come from the Hub")
        require(store.controllerIdentity?.settings.globalAuthority == "genstudio", "GenStudio stays the global authority")
        require(store.controllerIdentity?.readiness.ready == true, "a healthy local Hub must report ready")
        require(store.hubVersion?.version == "0.4.0", "the app must show the Hub's own version")

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
            // The modes the app shows must be the modes the producer published for that row, in
            // the producer's order: `modes` is the Voice runtime's own answer, and the app used to
            // derive one instead - which is how a model that clones *and* designs was shown a
            // single tab. Derived from the fixture, so a new model or a new mode is covered without
            // editing this test.
            let published = (entry["modes"] as? [String]) ?? []
            let expected = published.compactMap(VoiceMode.init(rawValue:)).reduce(into: [VoiceMode]()) {
                if !$0.contains($1) { $0.append($1) }
            }
            let model = store.models.first { $0.function == .audio && $0.repo == repo }
            require(model?.voiceModes == expected,
                    "\(repo) must offer exactly the modes its own catalog row published, got: \(model?.voiceModes.map(\.rawValue) ?? [])")
            require(model?.reportsVoiceModes == !published.isEmpty,
                    "\(repo) must report whether its engine published any modes")
            require(model?.requiresReference == expected.contains(.cloning),
                    "\(repo) must need a reference clip only when it clones")
            // Every tab the model publishes must actually list it, and no mode it did not publish
            // may be reachable: this is what "the tabs follow the model" means from the store side.
            for mode in VoiceMode.allCases {
                let listed = store.audioModels(for: mode).contains { $0.repo == repo }
                let wanted = expected.contains(mode) || expected.isEmpty
                require(listed == wanted,
                        "\(repo) listed in the \(mode.rawValue) tab: \(listed), expected \(wanted)")
            }
        }

        // A model whose engine reported no modes is still listed — hiding an installed model would
        // answer "no voice model is installed" to someone looking straight at one — and the page
        // says why it cannot generate with it.
        let unreported = store.models.first { $0.repo == "fixture/unreported-modes" }
        require(unreported?.supported == true && unreported?.voiceModes.isEmpty == true,
                "a model with no published modes must still be listed as supported")
        require(store.audioModels(for: .cloning).contains { $0.repo == "fixture/unreported-modes" },
                "a model with no published modes must stay visible rather than vanish from every tab")

        // The four families this fix exists for, named so a reader sees the bug's own coverage.
        // Khmer is preset-only: the catalogue declares tts + multilingual and no voice-cloning, and
        // its generator rejects a reference voice. Chatterbox and Fish clone from a clip.
        let khmer = store.models.first { $0.repo == "khmerttsopensource/khmer-tts" }
        require(khmer?.supported == true && khmer?.voiceModes == [.preset] && khmer?.requiresReference == false,
                "khmer-tts-mms must be selectable, in the preset tab, needing no reference clip")
        require(khmer?.speakers.isEmpty == true && khmer?.publishesPresetRoster == false,
                "a single-speaker family must not be offered another family's preset roster")
        let qwenPreset = store.models.first { $0.repo == "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit" }
        require(qwenPreset?.speakers == ["Ryan", "Aiden"] && qwenPreset?.publishesPresetRoster == true,
                "the family that publishes a roster must be the only one offered it")
        let khmerOnnx = store.models.first { $0.repo == "sengtha/khmer-tts-female-v2" }
        require(khmerOnnx?.supported == true && khmerOnnx?.voiceModes == [.preset],
                "khmer-tts-onnx must be selectable, in the preset tab")
        let chatterbox = store.models.first { $0.repo == "mlx-community/chatterbox-8bit" }
        require(chatterbox?.supported == true && chatterbox?.voiceModes == [.cloning]
                && chatterbox?.requiresReference == true,
                "chatterbox-mlx must be selectable, in the cloning tab")
        let fish = store.models.first { $0.repo == "mlx-community/fish-audio-s2-pro-8bit" }
        require(fish?.supported == true && fish?.voiceModes == [.cloning],
                "fish-audio-mlx must be selectable, in the cloning tab")
        // The family arriving from the parallel job: it publishes cloning *and* preset, so both tabs
        // must offer it, and it must not need the app to know the name "pocket-tts" at all.
        let pocket = store.models.first { $0.repo == "mlx-community/pocket-tts-4bit" }
        require(pocket?.supported == true && pocket?.voiceModes == [.cloning, .preset]
                && pocket?.requiresReference == true,
                "a model that publishes two modes must be selectable in both tabs")

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

        // The Subtitle page's below-tier warning, rendered exactly as the Image page renders its
        // own. The Subtitle worker publishes the sentence on its catalogue row under
        // `memory_warning` - the same key and place Image, Music and Chat use - and no Mac in this
        // fleet is below Subtitle's 8 GB tier, so the fixture is the only way to see it. It stays
        // advice: the model is still offered and still eligible.
        let warnedSTT = store.models.first { $0.repo == FixtureProtocol.subtitleWarningRepo }
        require(warnedSTT?.memoryWarning == FixtureProtocol.subtitleWarningSentence,
                "the Subtitle worker's published warning must reach the page's model, got: "
                    + "\(warnedSTT?.memoryWarning ?? "nil")")
        require(warnedSTT?.supported == true && warnedSTT?.blockReason == nil,
                "a RAM tier is advice, not a wall: the model must still be offered")

        // The negative control: a Mac at or above the tier publishes no warning at all, so the
        // page's `if let` draws nothing. A warning shown on a machine that is fine is noise the
        // operator learns to ignore - and then the real one is ignored too.
        FixtureProtocol.setSubtitleMemoryWarning(nil)
        await store.refresh()
        let unwarnedSTT = store.models.first { $0.repo == FixtureProtocol.subtitleWarningRepo }
        require(unwarnedSTT?.memoryWarning == nil,
                "a Mac at or above the tier must render nothing, got: "
                    + "\(unwarnedSTT?.memoryWarning ?? "nil")")
        require(unwarnedSTT?.supported == true,
                "the model must stay selectable on a machine with nothing to warn about")
        FixtureProtocol.setSubtitleMemoryWarning(FixtureProtocol.subtitleWarningSentence)
        await store.refresh()
        require(store.models.first { $0.repo == FixtureProtocol.subtitleWarningRepo }?.memoryWarning
                    == FixtureProtocol.subtitleWarningSentence,
                "the warning must come back when the worker publishes it again")

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

        // The Video lane's published switch. `fixture/ltx-video` publishes `audio` (default true)
        // exactly as Studio Hub 0.6.83's LTX lane does, and the request must carry a choice only
        // when the operator made one — the lane's own default is the answer for an untouched
        // switch, and a value for a model that publishes none must never be sent.
        store.videoModel = "fixture/ltx-video"
        store.videoPrompt = "A slow push in over a quiet harbour"
        store.snapVideoSwitchesToPublished()
        // Two live defects are what this fixture row exists to catch, and both of them emptied
        // EVERY model list in the app rather than only the video page, because the catalogue is
        // decoded whole:
        //   1. `enum` is not always strings — the video lane publishes [2, 4] for
        //      `duration_seconds` and [24] for `frame_rate`, and decoding those as [String] failed
        //      the response. Measured 2026-09-27 on the running Hub 0.6.83.
        //   2. a CodingKey whose raw value is snake_case can never match under this app's
        //      `.convertFromSnakeCase` strategy, so every canvas failed to decode.
        // If either returns, `store.models` below is empty and the completeness assertion fails.
        let videoContract = store.models.first { $0.function == .video && $0.repo == "fixture/ltx-video" }?.laneContract
        require(videoContract?.canvases?.count == 2,
                "a published canvas list must decode, got: \(String(describing: videoContract?.canvases))")
        require(videoContract?.canvases?.first?.aspectRatio == "11:7",
                "a canvas's own aspect ratio must decode through the key strategy")
        require(videoContract?.canvases?.first?.durations == [2, 4],
                "a canvas's own durations must decode as the integers the lane published")
        let durationParameter = videoContract?.parameters.first { $0.name == "duration_seconds" }
        require(durationParameter?.options == ["2", "4"],
                "a numeric enum must render as text rather than failing the catalogue: \(String(describing: durationParameter?.options))")
        require(durationParameter?.optionValues == [.integer(2), .integer(4)],
                "the lane's own values must keep their types beside the text form")
        let videoSwitches = store.selectedVideoSwitches
        require(videoSwitches.map(\.name) == ["audio"],
                "the page must offer exactly the switches the lane publishes, got: \(videoSwitches.map(\.name))")
        require(videoSwitches.first?.defaultValue == .bool(true),
                "the lane's own published default must reach the control")
        require(videoSwitches.first?.note?.contains("does not change generation time") == true,
                "the lane's own sentence about the switch must reach the page")
        await store.generateVideo()
        require(store.error == nil, "the video lane must accept the request: \(store.error ?? "none")")
        var videoItemParams = (FixtureProtocol.generatedBody()?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(videoItemParams?["audio"] == nil,
                "an untouched switch must be absent so the lane's own default decides")

        store.videoSwitches["audio"] = false
        await store.generateVideo()
        videoItemParams = (FixtureProtocol.generatedBody()?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(videoItemParams?["audio"] as? Bool == false,
                "the OFF half of the switch is the whole point of it and must be sent: \(String(describing: videoItemParams))")

        // A model change replaces the switch set: a value for a lane that never published the
        // choice is dropped rather than carried, which is what keeps one model's answer out of
        // another model's request.
        store.videoModel = "fixture/flux"
        store.snapVideoSwitchesToPublished()
        require(store.videoSwitches.isEmpty,
                "a switch the newly selected model does not publish must be dropped, got: \(store.videoSwitches)")

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

        // The Image page's loading control. It is one lever - which mflux memory configuration the
        // next job loads with - and it is not the Settings release policy: Image's
        // `/api/memory-policy` answer stays the fixed one above.
        //
        // Everything the control renders was published. The options and their sentences are the
        // model's own catalogue row (its measured peak and wall time, and the host they were
        // measured on), the machine-wide setting is the Image worker's own payload, and a model
        // with one loading mode renders its own reason instead of a dead control. The fixture's
        // rows mirror the two shipped models: Z-Image-Turbo 6B MLX 4-bit has the bounded
        // configuration, FLUX.2 Klein 4B MLX 4-bit has one loading mode on purpose.
        await store.refreshImageLoadingMode()
        store.imageModel = "fixture/zimage"
        let zimageRow = FixtureProtocol.catalogModels.first { $0["repo"] as? String == "fixture/zimage" }
        let zimagePublished = ((zimageRow?["loading_modes"] as? [String: Any])?["options"] as? [[String: Any]]) ?? []
        require(zimagePublished.count == 3, "the fixture's Z-Image row must publish the three options")
        let supportedControl = store.imageLoadingControl
        require(supportedControl?.repo == "fixture/zimage" && supportedControl?.offersControl == true,
                "a model with more than one loading mode must offer the control")
        require(supportedControl?.options.map(\.id) == zimagePublished.compactMap { $0["id"] as? String },
                "the control's options must be the published ones in the published order, got: "
                    + "\(supportedControl?.options.map(\.id) ?? [])")
        require(supportedControl?.options.map(\.label) == zimagePublished.compactMap { $0["label"] as? String },
                "the control's labels must be the published ones, got: \(supportedControl?.options.map(\.label) ?? [])")
        require(supportedControl?.options.map { $0.detail ?? "" } == zimagePublished.map { $0["detail"] as? String ?? "" },
                "each option's sentence must be the model's own, with its measured numbers")
        require(supportedControl?.detail?.contains("7.1 GB") == true && supportedControl?.detail?.contains("236 s") == true
                    && supportedControl?.detail?.contains("32 GiB M2 Max") == true,
                "the selected option's own measured sentence must be shown, got: \(supportedControl?.detail ?? "nil")")
        require(supportedControl?.selected == "auto" && supportedControl?.effective == "low-ram"
                    && supportedControl?.effectiveSentence == "In force on this Mac: low-ram",
                "the page must show that auto resolved to the bounded configuration here, got: "
                    + "\(supportedControl?.effectiveSentence ?? "nil")")

        // Choosing a mode PUTs the chosen id to the Image worker through the Hub proxy, then
        // refreshes, so the page shows the new selection and the new `effective`.
        FixtureProtocol.resetImageLoadingPuts()
        await store.setImageLoadingMode("full")
        require(FixtureProtocol.imageLoadingPutModes() == ["full"],
                "choosing a mode must PUT the chosen id, got: \(FixtureProtocol.imageLoadingPutModes())")
        require(FixtureProtocol.imageLoadingToken() == "native-test-token",
                "the loading-mode change must be authenticated through the Hub proxy")
        require(store.imageLoadingControl?.selected == "full",
                "the page must show the selection the worker accepted, got: \(store.imageLoadingControl?.selected ?? "nil")")
        require(store.imageLoadingControl?.effectiveSentence == "In force on this Mac: default",
                "the page must show the new effective after the change, got: "
                    + "\(store.imageLoadingControl?.effectiveSentence ?? "nil")")
        require(store.models.first { $0.repo == "fixture/zimage" }?.loadingModes?.selected == "full",
                "the refreshed catalogue must carry the new selection too")

        // A model with exactly one loading mode: its own published reason is the sentence, and no
        // control is drawn.
        store.imageModel = "fixture/flux"
        let fluxRow = FixtureProtocol.catalogModels.first { $0["repo"] as? String == "fixture/flux" }
        let fluxReason = (fluxRow?["loading_modes"] as? [String: Any])?["reason"] as? String
        let fluxControl = store.imageLoadingControl
        require(fluxControl?.offersControl == false,
                "a single-mode model must not be given a control the lane would discard")
        require(fluxControl?.reason == fluxReason && fluxReason?.isEmpty == false,
                "the model's own reason must be the sentence shown, got: \(fluxControl?.reason ?? "nil")")
        require(fluxControl?.effectiveSentence == "In force on this Mac: default",
                "a single-mode model still says what it loads as")

        // ...and when the machine-wide setting is low, the model's reason gains the clause saying
        // the setting does not apply to it — the half of the sentence the catalogue re-derives.
        await store.setImageLoadingMode("low")
        let lowReason = store.imageLoadingControl?.reason ?? ""
        require(lowReason.contains("still loads its default configuration"),
                "a forced setting must be named in the model's own reason, got: \(lowReason)")
        require(store.imageLoadingControl?.offersControl == false,
                "the reason must never become a control")
        await store.setImageLoadingMode("auto")
        require(store.imageLoadingControl?.effectiveSentence == "In force on this Mac: default",
                "auto must resolve back to the default for a single-mode model")

        // A catalogue row for a manifest the worker has no registered model for publishes no
        // loading modes at all; the page says so rather than drawing a dead control.
        store.imageModel = "fixture/flux-unpackaged"
        let unregistered = store.imageLoadingControl
        require(unregistered?.offersControl == false && unregistered?.reason?.contains("publishes no loading mode") == true,
                "a row with no published loading modes must say so, got: \(unregistered?.reason ?? "nil")")
        store.imageModel = "fixture/flux"

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
        store.musicModel = "fixture/sung-music"

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
        require(store.supportedModels(for: .audio).contains {
            $0.repo == store.audioModel && ($0.voiceModes.contains(store.audioMode) || $0.voiceModes.isEmpty)
        }, "the Audio tab must be one the selected model publishes")

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

        // B32: the Chat tab lists real Chat work, and the Video tab lists real clips.
        //
        // A Chat-page send is synchronous and used to leave no job behind at all, so a Chat tab
        // over `/api/hub/jobs` could only ever have been empty. The Hub now records each attempt as
        // a finished one-item batch; the app has to place it by its modality, carry the answer, and
        // give the row the facts the other rows have. **Video followed on 2026-09-26**: the worker
        // and the batch history exist, so the tab that used to be an honest placeholder now files
        // clips like every other Studio.
        require(StudioFunction.chat.hasBatchHistory && StudioFunction.video.hasBatchHistory,
                "Chat and Video both file Hub batches, so both must claim a batch history")
        // `HistoryTab.allCases` is built from this same pair in the SwiftUI layer, so a Studio that
        // answers yes here has a tab there without this binary needing to know about tabs.
        require(StudioFunction.allCases.allSatisfy(\.hasHistoryTab),
                "every Studio must have a Jobs tab, including the ones with nothing to list yet, "
                    + "missing: \(StudioFunction.allCases.filter { !$0.hasHistoryTab }.map(\.rawValue))")
        require(store.startupNote?.contains("chat") != true,
                "a Chat batch must be placed, not reported as a modality this build cannot place: "
                    + "\(store.startupNote ?? "nil")")
        require(store.startupNote?.contains("video") != true,
                "the fixture's video batch is placed under Video, so it must not be reported as unplaceable, got: \(store.startupNote ?? "nil")")
        let chatJobs = store.jobs(for: .chat)
        require(chatJobs.count == 1 && chatJobs.first?.id == "chat-job",
                "the Hub's Chat batch must be listed under Chat, got: \(chatJobs.map(\.id))")
        let chatJob = chatJobs[0]
        require(chatJob.text == "What is a tokenizer?",
                "the row's label is the prompt the operator sent, got: \(chatJob.text)")
        require(chatJob.answer == "It turns text into the numbers a model reads.",
                "the answer the Hub recorded must reach the row, got: \(chatJob.answer)")
        require(chatJob.model == "Qwen/Qwen3.5-9B" && chatJob.generationSuffix == " · took 1 min 1 s",
                "the row must carry the model and how long it took, got: "
                    + "\(chatJob.model)\(chatJob.generationSuffix)")

        // Tokens per second, from the Chat worker's own stated generation time and nothing else.
        // The Hub's round trip stays a separate fact on the same row: it includes the model load on
        // a cold turn (61.4 s here) where the worker stated 7.2 s of generation.
        require(chatJob.completionTokens == 10,
                "the worker's stated completion tokens must reach the row, got: \(String(describing: chatJob.completionTokens))")
        require(chatJob.statedGenerationSeconds == 7.2 && chatJob.generationSeconds == 61.4,
                "the worker's stated generation time and the Hub's round trip must stay distinct, got: "
                    + "\(String(describing: chatJob.statedGenerationSeconds)) / \(String(describing: chatJob.generationSeconds))")
        require(chatJob.tokenSpeed == "10 tokens · 1.4 tok/s · 7.2 s",
                "the row must show the rate the worker's own numbers give, got: \(chatJob.tokenSpeed)")

        // The negative control, through the same decode path: a Chat item that states usage but no
        // generation time. There is no rate to state - the Hub's round trip is not the model's
        // speed - so the row names the round trip as the round trip and publishes no `tok/s`.
        FixtureProtocol.setChatJobWithoutStatedTime(true)
        await store.refresh()
        let noStatedTime = store.jobs(for: .chat).first { $0.id == "chat-job-nostated" }
        require(noStatedTime?.completionTokens == 10 && noStatedTime?.statedGenerationSeconds == nil,
                "an item that stated no generation time must decode as none, got: "
                    + "\(String(describing: noStatedTime?.statedGenerationSeconds))")
        require(noStatedTime?.tokenSpeed.contains("tok/s") == false,
                "no rate may be derived from the round trip, got: \(noStatedTime?.tokenSpeed ?? "nil")")
        require(noStatedTime?.tokenSpeed == "10 tokens · 1 min 1 s round trip",
                "the round trip must be shown labelled as the round trip, got: \(noStatedTime?.tokenSpeed ?? "nil")")
        FixtureProtocol.setChatJobWithoutStatedTime(false)
        await store.refresh()
        require(store.jobs(for: .chat).count == 1,
                "the fixture's extra batch must be gone once the control is turned off")
        require(store.jobHistories.contains { $0.function == .chat && $0.jobs.count == 1 },
                "the Chat tab must have a section with the Chat job in it")
        let videoJobs = store.jobs(for: .video)
        require(videoJobs.count == 1 && videoJobs.first?.id == "video-job",
                "the Hub's Video batch must be listed under Video, got: \(videoJobs.map(\.id))")
        require(videoJobs.first?.durationSeconds == 4.04,
                "a finished clip must carry the length the worker measured, got: \(String(describing: videoJobs.first?.durationSeconds))")
        require(store.audioJobs.allSatisfy { $0.id != "video-job" },
                "a Video batch must not be filed under another Studio's history")
        // The row's two lines: what was asked for, and what came out. A video row used to say
        // neither — no canvas, no frames, no frame rate and no audio — so a finished clip was a
        // length and a byte count.
        let videoJob = videoJobs[0]
        require(videoJob.requestFacts(for: .video) == "448p · 11:7 · 4.0 s",
                "the Video row must state the canvas and length that were asked for, got: "
                    + videoJob.requestFacts(for: .video))
        // Asserted in parts: the byte count is the fixture's own clip, which is not this test's
        // subject, while the canvas, frames, frame rate and audio are.
        require(videoJob.assetFacts.hasPrefix("4.0 s · ")
                    && videoJob.assetFacts.hasSuffix("704×448 · 97 frames · 24 fps · with audio"),
                "the Video row must state the clip's own measured facts, got: \(videoJob.assetFacts)")
        require(videoJob.canRevealArtifact == false,
                "a worker path that is not on this Mac must not offer Show in Folder")
        require(videoJob.requestFacts(for: .image).isEmpty,
                "a Video job showed Image facts, which means the modality is not deciding the line")
        let voiceJob = store.jobs(for: .audio).first { !$0.answer.isEmpty } ?? store.jobs(for: .audio)[0]
        if !voiceJob.text.isEmpty {
            require(voiceJob.requestFacts(for: .audio).hasPrefix("\(voiceJob.text.count) characters"),
                    "a Voice row must state the script's own length, got: "
                        + voiceJob.requestFacts(for: .audio))
        }
        require(NativeJob.trimmed(1.0) == "1" && NativeJob.trimmed(1.5) == "1.5",
                "a control's value must be shown without trailing zeros")

        // A row says when it was generated. Six row builders each hand-wrote the "status · model"
        // line and none of them wrote a date, so the owner could not tell two runs of the same
        // script apart except by counting rows down from the top.
        let stamp = 1_700_000_000.0
        let datedJob = NativeJob(id: "dated", state: "done", progress: 1, text: "hello",
                                 model: "fixture/voice", createdAt: stamp,
                                 outputURL: nil, error: nil)
        require(datedJob.generatedAtLabel == NativeWorkspaceStore.moment(stamp),
                "a job with a Hub stamp must show it, got: \(datedJob.generatedAtLabel ?? "nil")")
        let undatedJob = NativeJob(id: "undated", state: "done", progress: 1, text: "hello",
                                   model: "fixture/voice", createdAt: nil,
                                   outputURL: nil, error: nil)
        require(undatedJob.generatedAtLabel == nil,
                "a job the Hub never stamped must not be dated, got: "
                    + "\(undatedJob.generatedAtLabel ?? "nil")")
        // A zero stamp is the same absence, not a job made in 1970: 0 is what a missing time looks
        // like once it has been through a number field.
        let zeroedJob = NativeJob(id: "zeroed", state: "done", progress: 1, text: "hello",
                                  model: "fixture/voice", createdAt: 0,
                                  outputURL: nil, error: nil)
        require(zeroedJob.generatedAtLabel == nil,
                "a zero stamp must read as no stamp, got: \(zeroedJob.generatedAtLabel ?? "nil")")

        // Show in Folder points at a file on this Mac. The Hub publishes the path only for a job
        // this Mac produced; the row is the second gate, so a path that is not here is never
        // offered whatever the payload said.
        let here = FileManager.default.temporaryDirectory
            .appendingPathComponent("hub-reveal-\(UUID().uuidString).wav")
        try Data("RIFF".utf8).write(to: here)
        defer { try? FileManager.default.removeItem(at: here) }
        let onThisMac = NativeJob(id: "local", state: "done", progress: 1, text: "",
                                  model: "fixture/voice", createdAt: stamp,
                                  outputURL: nil, error: nil, artifactPath: here.path)
        require(onThisMac.canRevealArtifact,
                "a result that is a file on this Mac must offer Show in Folder")
        let onAnotherMac = NativeJob(id: "remote", state: "done", progress: 1, text: "",
                                     model: "fixture/voice", createdAt: stamp,
                                     outputURL: nil, error: nil,
                                     artifactPath: here.path + ".not-here")
        require(onAnotherMac.canRevealArtifact == false,
                "a path with no file behind it must not offer Show in Folder")
        let withNoPath = NativeJob(id: "nopath", state: "done", progress: 1, text: "",
                                   model: "fixture/voice", createdAt: stamp,
                                   outputURL: nil, error: nil)
        require(withNoPath.canRevealArtifact == false,
                "a job with no recorded path must not offer Show in Folder")

        // B28: "Use in …" must point the Studio that owns the model at it. It used to set the
        // Subtitle selection for every non-audio model.
        for function in engineFunctions {
            guard let model = store.models.first(where: { $0.function == function && $0.supported }) else { continue }
            store.select(model)
            let selected = store.supportedModels(for: function).contains { $0.repo == model.repo }
            require(selected, "Use in \(function.rawValue) must select the model in its own picker, got: \(model.repo)")
        }
        store.select(store.models.first { $0.repo == "Qwen/Qwen3.5-9B" }!)
        require(store.chatModel == "Qwen/Qwen3.5-9B", "Use in Chat must set the Chat selection, not Subtitle")
        require(store.transcriptionModel != "Qwen/Qwen3.5-9B", "a Chat model must never become the Subtitle selection")

        // B30: the Chat transcript tells the truth while a reply is in flight, and after one fails.
        //
        // The operator's own turn was appended only once the completion returned, so during a slow
        // send the pane still read "No messages yet" and the message they had just sent was nowhere
        // on screen - while the editor kept the text, which made a slow worker look like a dead
        // button. A failed send was worse: the prompt came back to the editor and the transcript
        // never mentioned the attempt at all. Measured with a held completion, then with a failing
        // one, then with a connection that ends mid-flight.
        store.clearChat()
        require(store.chatTurns.isEmpty && store.chatPrompt.isEmpty,
                "the Chat transcript and editor must start empty for this test")
        let chatCallsBefore = FixtureProtocol.chatCallCount()
        store.chatPrompt = "Hello from the fixture"
        FixtureProtocol.holdChat()
        let heldSend = Task { await store.sendChat() }
        for _ in 0..<200 where !FixtureProtocol.chatHasStarted() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        require(FixtureProtocol.chatHasStarted(), "the held Chat completion never reached the fixture")
        require(store.submittingChat, "a send in flight must hold the Chat gate")
        require(store.chatTurns.count == 1 && store.chatTurns.first?.role == "user"
                    && store.chatTurns.first?.content == "Hello from the fixture",
                "the operator's own message must be in the transcript before the reply arrives, got: "
                    + "\(store.chatTurns.map { "\($0.role):\($0.content)" })")
        require(store.chatTurns.allSatisfy { $0.role != "assistant" },
                "no model turn may exist before the model has answered")
        require(store.chatPrompt.isEmpty,
                "a sent message must leave the editor at once, not when the reply lands")
        // A second send while one is in flight is refused by the gate, not queued: the transcript
        // must not grow a second copy, and the reply must stay attached to the prompt that asked
        // for it.
        store.chatPrompt = "Typed while the first was still in flight"
        await store.sendChat()
        require(store.chatTurns.count == 1,
                "a second send while one is in flight must append nothing, got: "
                    + "\(store.chatTurns.map(\.content))")
        require(FixtureProtocol.chatCallCount() == chatCallsBefore + 1,
                "the gate must refuse a second send before it reaches the Hub, got "
                    + "\(FixtureProtocol.chatCallCount() - chatCallsBefore) requests")
        FixtureProtocol.releaseHeldChat()
        await heldSend.value
        require(!store.submittingChat, "a completed send must release the Chat gate")
        require(store.chatTurns.count == 2 && store.chatTurns.first?.role == "user"
                    && store.chatTurns.last?.role == "assistant",
                "the reply must land once, after the message that asked for it, got: "
                    + "\(store.chatTurns.map(\.role))")
        require(store.chatTurns.first?.failure == nil, "a successful turn must not be marked as failed")
        require(store.chatTurns.last?.content == "Fixture reply.",
                "the transcript must carry the worker's own text, got: \(store.chatTurns.last?.content ?? "nil")")
        // The live reply states its own speed: the worker's token count over the worker's own
        // generation time, exactly as the Chat job row does. Both come off the reply body.
        require(store.chatTurns.last?.completionTokens == 10
                    && store.chatTurns.last?.statedGenerationSeconds == 7.2,
                "the live reply's stated usage and generation time must reach the turn, got: "
                    + "\(String(describing: store.chatTurns.last?.completionTokens)) / "
                    + "\(String(describing: store.chatTurns.last?.statedGenerationSeconds))")
        require(store.chatTurns.last?.speed == "10 tokens · 1.4 tok/s · 7.2 s",
                "a live reply must show the same rate the job row does, got: \(store.chatTurns.last?.speed ?? "nil")")
        // The wire body is the conversation and nothing else: the message that was just sent, as a
        // plain user turn. A failure marker is a display fact and must never reach the Hub.
        let chatBody = FixtureProtocol.recordedChatBody()
        require(chatBody?["stream"] as? Bool == false, "the Chat request must ask for one complete reply")
        require(chatBody?["model"] as? String == "Qwen/Qwen3.5-9B",
                "the request must name the model the operator chose, got: \(chatBody?["model"] ?? "nil")")
        let sentMessages = chatBody?["messages"] as? [[String: Any]]
        require(sentMessages?.count == 1
                    && sentMessages?.first?["role"] as? String == "user"
                    && sentMessages?.first?["content"] as? String == "Hello from the fixture",
                "the request must carry exactly the message that was sent, got: \(sentMessages ?? [])")

        // The live reply's negative control: a worker that states usage but no generation time.
        // The app does not time the call itself - its own wall clock would include the model load
        // and the network - so the turn shows the token count and no rate at all.
        FixtureProtocol.setChatReplyUsage(tokens: 10, elapsed: nil)
        store.clearChat()
        store.chatPrompt = "A reply with no stated generation time"
        await store.sendChat()
        require(store.chatTurns.last?.statedGenerationSeconds == nil,
                "a reply that stated no generation time must carry none")
        require(store.chatTurns.last?.speed == "10 tokens",
                "a reply with no stated time must show the token count and no rate, got: "
                    + "\(store.chatTurns.last?.speed ?? "nil")")
        require(store.chatTurns.last?.speed.contains("tok/s") == false,
                "a rate must not be invented for a reply that stated no generation time")
        FixtureProtocol.setChatReplyUsage(tokens: 10, elapsed: 7.2)
        store.clearChat()

        // No model chosen is an absent field, not an empty string. The worker reads a falsy `model`
        // as "serve the model I was started with", so `model: ""` could not be told apart from a
        // real choice and the Hub had nothing to gate or dispatch by.
        let callsBeforeUnnamed = FixtureProtocol.chatCallCount()
        store.clearChat()
        store.chatPrompt = "No model chosen"
        store.chatModel = ""                      // set immediately before the send: reconcile
        await store.sendChat()                    // re-seeds it on the next catalogue refresh
        require(FixtureProtocol.chatCallCount() == callsBeforeUnnamed + 1,
                "the unnamed send never reached the fixture")
        let unnamedBody = FixtureProtocol.recordedChatBody()
        require(unnamedBody?["model"] == nil,
                "an unselected model must be left out, not sent as an empty string, got: "
                    + "\(unnamedBody?["model"] ?? "nil")")
        store.chatModel = "Qwen/Qwen3.5-9B"

        // A failure stays in the transcript, marked, with no invented reply.
        FixtureProtocol.setChatFailure(500)
        store.chatPrompt = "This one gets no answer"
        await store.sendChat()
        require(store.chatTurns.count == 3,
                "a failed send must add exactly its own turn, got: \(store.chatTurns.map(\.role))")
        let failedTurn = store.chatTurns[2]
        require(failedTurn.role == "user" && failedTurn.content == "This one gets no answer",
                "the failed attempt must stay in the transcript as the operator's own message")
        require(failedTurn.failure?.isEmpty == false,
                "a failed attempt must be marked in the transcript, not only in the status line")
        require(store.chatTurns.filter { $0.role == "assistant" }.count == 1,
                "a failed completion must not be answered with a model turn that never happened, got: "
                    + "\(store.chatTurns.map(\.role))")
        require(store.error?.contains("Chat failed") == true,
                "the page banner must still name the failure, got: \(store.error ?? "nil")")
        require(store.chatPrompt.isEmpty,
                "a failed message must not sit in the editor as well as in the transcript")
        FixtureProtocol.setChatFailure(nil)

        // A send whose connection ends mid-flight must not file its reply under a transcript it no
        // longer owns - and must release the gate, rather than leaving Send disabled for good.
        store.clearChat()
        store.chatPrompt = "Sent to a connection that goes away"
        FixtureProtocol.holdChat()
        let supersededSend = Task { await store.sendChat() }
        for _ in 0..<200 where !FixtureProtocol.chatHasStarted() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        require(FixtureProtocol.chatHasStarted(), "the superseded Chat completion never reached the fixture")
        store.disconnect()
        require(!store.submittingChat, "a connection that ends must release the Chat gate")
        FixtureProtocol.releaseHeldChat()
        await supersededSend.value
        require(store.chatTurns.count == 1 && store.chatTurns.first?.role == "user",
                "a superseded send must not append its reply to a transcript it no longer owns, got: "
                    + "\(store.chatTurns.map(\.role))")
        require(store.chatTurns.first?.failure?.isEmpty == false,
                "a superseded send must say in the transcript that no answer arrived")
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!, dataDirectory: state, token: "native-test-token")
        store.clearChat()

        // B27: a preset roster belongs to the family that published it and is lent to no other.
        let secondPreset = store.models.first { $0.repo == "fixture/second-preset" }
        require(secondPreset?.speakers == ["Sophea", "Dara"],
                "a second preset family's own published roster must reach its model, got: \(String(describing: secondPreset?.speakers))")
        require(secondPreset?.publishesPresetRoster == true, "a family that published a roster must say so")
        store.audioModel = "fixture/second-preset"
        require(store.audioSpeaker == "",
                "selecting a model must not choose a voice for the operator, got: \(store.audioSpeaker)")
        // The roster is still what the picker offers and what a request may carry — the second
        // family's own names, and no other family's.
        require(secondPreset?.speakers == ["Sophea", "Dara"],
                "the picker must offer exactly the family's published voices")
        store.audioSpeaker = "Sophea"
        require(store.audioSpeakerForRequest == "Sophea",
                "a voice the operator chose from this model's own roster must be sent")
        store.audioModel = "khmerttsopensource/khmer-tts"
        require(store.audioSpeaker.isEmpty, "a single-voice family must not be handed another family's speaker")
        require(store.audioSpeakerForRequest == nil,
                "and its lane publishes no preset_speaker, so none is ever sent")
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
        require(store.audioSpeaker == "", "changing model must not seed a voice from the new roster")
        require(store.audioSpeakerInForce == nil,
                "with no voice stored for it, nothing is stated about which voice this model uses")

        // B23: a batch whose modality this build cannot place is named, not skipped before the
        // loop. Both the fixture's unknown modality and the one that maps to a Studio with no
        // batch history must appear in the startup note.
        require(store.startupNote?.contains("hologram") == true,
                "a modality with no Studio must be reported by name, got: \(store.startupNote ?? "nil")")
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
        require(unwired?.voiceModes == [.preset], "the modes are the producer's, not a default")
        require(!store.models.contains { $0.repo == "mlx-community/S3TokenizerV2" },
                "a companion codec the catalogue does not publish as a TTS form is not a voice model")

        require(store.audioJobs.first?.id == "old-job", "existing audio jobs must load")
        // What the Hub says about a waiting job, and where it ran, reaches the row (F1/F3).
        let described = store.audioJobs.first
        require(described?.governorNote?.contains("not downloaded on image@studio-mac-1102") == true,
                "the Hub's reason for a waiting batch must be decoded, not replaced by a fixed sentence")
        require(described?.machine == "studio-mac-1102" && described?.tries == 2 && described?.retryAt == 4_102_444_800,
                "the machine, tries and next retry must be decoded: \(String(describing: described))")
        let inTen = Date(timeIntervalSince1970: 4_102_444_790)
        require(NativeJob.waitingLabel(note: nil, tries: nil, retryAt: nil, now: inTen) == "Waiting for a free worker",
                "with no stated reason the generic wait remains")
        require(NativeJob.waitingLabel(note: "'x' not downloaded on a", tries: nil, retryAt: nil, now: inTen)
                    == "'x' not downloaded on a", "a stated reason replaces the generic wait")
        require(NativeJob.waitingLabel(note: nil, tries: 1, retryAt: 4_102_444_800, now: inTen)
                    == "Waiting for a free worker · retrying in 10.0 s (attempt 2)",
                "a retry loop must say when it retries: \(NativeJob.waitingLabel(note: nil, tries: 1, retryAt: 4_102_444_800, now: inTen))")
        require(!NativeJob.waitingLabel(note: nil, tries: 1, retryAt: 4_102_444_780, now: inTen).contains("retrying"),
                "a retry time in the past is not a countdown")
        await store.retryJob(described!)
        require(FixtureProtocol.retryPaths == ["/api/hub/jobs/old-job/items/0/retry"],
                "Retry must post the failed item's own route: \(FixtureProtocol.retryPaths)")
        require(store.downloads.first?.percent == 50, "download progress must load")
        await store.startDownload(supportedSTT[0])
        require(FixtureProtocol.lastDownloadPath == "/api/hub/model-packages/whisper-turbo/download", "model download bypassed Hub package management")
        require(FixtureProtocol.lastDownloadToken == "native-test-token", "Hub download request was unauthenticated")
        require(store.downloads.contains { $0.id == "hub-download-1" }, "Hub model download status missing")
        await store.cancelDownload(store.downloads.first { $0.id == "hub-download-1" }!)
        require(store.downloads.contains { $0.id == "hub-download-1" && $0.state == "cancelled" }, "Hub model download cancellation missing")
        // A finished run is not polled again, for the rest of the session.
        FixtureProtocol.mutate {
            FixtureProtocol.hubDownloadMode = "normal"; FixtureProtocol.hubDownloadCancelled = false
            FixtureProtocol.hubDownloadReads = 0
        }
        await store.refresh()
        require(FixtureProtocol.hubDownloadReads == 0, "a cancelled download run must be pruned, not polled every tick")
        // The fixture reuses one run id for every download; a real Hub does not, so clear the old row.
        store.dismissDownload(store.downloads.first { $0.id == "hub-download-1" }!)
        // A running download reports real progress, from the Hub's own byte counts.
        await store.startDownload(supportedSTT[0])
        let progress = store.downloads.first { $0.id == "hub-download-1" }
        require(progress?.percent == 25 && progress?.totalBytes == 12_000_000_000,
                "download progress must come from the Hub's bytes: \(String(describing: progress))")
        // One failed read is not the download vanishing.
        FixtureProtocol.mutate { FixtureProtocol.hubDownloadMode = "transient" }
        await store.refresh()
        require(store.downloads.contains { $0.id == "hub-download-1" && $0.state == "running" },
                "a transient read failure must not drop a running download")
        // Only the Hub saying it does not know the run drops it.
        FixtureProtocol.mutate { FixtureProtocol.hubDownloadMode = "forgotten" }
        await store.refresh()
        require(!store.downloads.contains { $0.id == "hub-download-1" }, "a run the Hub forgot (404) must go")
        // A failure names its real reason, and the row is kept for the operator, without polling.
        FixtureProtocol.mutate { FixtureProtocol.hubDownloadMode = "normal" }
        await store.startDownload(supportedSTT[0])
        FixtureProtocol.mutate { FixtureProtocol.hubDownloadMode = "failed"; FixtureProtocol.hubDownloadReads = 0 }
        await store.refresh()
        let failedRun = store.downloads.first { $0.id == "hub-download-1" }
        require(failedRun?.state == "error" && failedRun?.error == "Download incomplete or invalid: model.safetensors",
                "a failed download must show the downloader's own reason: \(String(describing: failedRun))")
        await store.refresh()
        require(FixtureProtocol.hubDownloadReads == 1, "a failed run is read once, then kept without polling")
        store.dismissDownload(failedRun!)
        require(!store.downloads.contains { $0.id == "hub-download-1" }, "a dismissed failure must clear")
        FixtureProtocol.mutate { FixtureProtocol.hubDownloadMode = "normal" }
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
        // An untouched pace control sends no `speed` key at all. Sending 1.0 here is what held the
        // Models tab's stored pace dead: the Voice worker treats a supplied parameter as the
        // caller's own answer, so the app's own constant outranked the operator's default on every
        // job.
        require((items?.first?["params"] as? [String: Any])?["speed"] == nil,
                "an untouched pace must send no speed at all, so this model's own default applies, got: \(String(describing: items?.first?["params"]))")
        require(items?.first?["text"] as? String == "Hello locally", "Hub text item")
        require((items?.first?["params"] as? [String: Any])?["preset_speaker"] as? String == "Ryan", "Hub speaker params")

        // A pace the operator chose on this page is theirs, and it is what the request carries.
        store.audioSpeed = 1.4
        await store.generateAudio()
        let paced = (FixtureProtocol.generatedBody()?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(paced?["speed"] as? Double == 1.4,
                "a pace chosen on the page must be sent, got: \(String(describing: paced))")

        // And it is pulled into this model's own published bounds - the same bounds the worker
        // clamps to - rather than sent out of range.
        store.audioSpeed = 5.0
        await store.generateAudio()
        let clamped = (FixtureProtocol.generatedBody()?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(clamped?["speed"] as? Double == 2.0,
                "a chosen pace must be pulled into the lane's own bounds, got: \(String(describing: clamped))")
        store.audioSpeed = nil

        // A newly visible preset family must submit as itself. Khmer VITS has one built-in speaker
        // and no roster, so the app must not lend it the Qwen names it happens to be holding: the
        // job is sent with no speaker rather than with a voice the model has never heard of.
        store.audioModel = "khmerttsopensource/khmer-tts"
        store.audioText = "សួស្តី"
        store.audioLanguage = "km"
        await store.generateAudio()
        require(store.error == nil, "a preset family must generate; error=\(store.error ?? "none")")
        let khmerParams = (FixtureProtocol.generatedBody()?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(khmerParams?["preset_speaker"] == nil,
                "a single-speaker model must not be sent another family's preset speaker, got: \(String(describing: khmerParams))")
        // This model publishes no lane contract, so it publishes no pace: one is never invented for
        // it, and the app never sends a parameter the lane does not offer.
        require(khmerParams?["speed"] == nil,
                "a model that publishes no pace must never be sent one, got: \(String(describing: khmerParams))")

        // The Audio page's tabs are the modes this Mac's models publish between them, not the modes
        // of whichever model happens to be selected. Deriving them from the selection closed a loop
        // with the picker below it - the tab decided which models the picker listed, and the
        // selected model decided which tabs existed - so a model that could not do the mode you
        // wanted also hid every model that could. The owner had Khmer TTS installed, was on the
        // default Clone tab with a cloning-capable model selected, and had no Ready-Made tab to
        // press and nothing on screen saying a preset voice existed on his own machine.
        store.audioModel = "fixture/two-mode-voice"
        require(store.audioModes.contains(.cloning) && store.audioModes.contains(.design),
                "a two-mode model's own modes must be offered, got: \(store.audioModes)")
        require(store.audioModes.contains(.preset),
                "the preset models installed on this Mac must stay reachable from another model's "
                + "page, got: \(store.audioModes)")
        require(store.audioModels(for: .preset).contains { $0.repo == "khmerttsopensource/khmer-tts" },
                "a preset-only model must be listed on the preset tab, got: "
                + "\(store.audioModels(for: .preset).map(\.repo))")
        // And a tab click lands on a model this Mac can actually generate with, not on the first
        // name in the list: the first listed can be an uninstalled package, which leaves Generate
        // disabled for no visible reason.
        require(store.preferredAudioModel(for: .preset)?.runtimeReady == true,
                "a tab must open on a model that can run here, got: "
                + "\(store.preferredAudioModel(for: .preset)?.label ?? "nil")")

        // A model this Mac cannot run is one the operator may have selected on purpose, to go and
        // install it. Having been seen unavailable once, it is left alone: a poll must not undo a
        // choice a person made.
        let unrunnable = store.models.first { $0.repo == "mlx-community/VoxCPM2-4bit" }
        require(unrunnable?.runtimeReady == false,
                "the fixture must keep a catalogue model this Mac cannot run")
        store.audioModel = "mlx-community/VoxCPM2-4bit"
        await store.refresh()
        require(store.audioModel == "mlx-community/VoxCPM2-4bit",
                "a deliberate selection of an unavailable model must survive a refresh, got: \(store.audioModel)")
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"

        // The launch case, which is the one the owner actually hit: the *persisted* selection is a
        // model this Mac can no longer run. His page opened on `voxcpm2-4bit`, whose folder is gone
        // while its install receipt still says it was installed - so `supported` was true, nothing
        // moved the selection, and the page sat there disabled with an install error under the
        // model. A fresh store with that selection already written down is exactly that state; it
        // has to be corrected on the first catalogue read.
        let launchHome = FileManager.default.temporaryDirectory
            .appendingPathComponent("studiohub-audio-launch-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: launchHome, withIntermediateDirectories: true)
        let persisted = """
        {"audioText":"","audioModel":"mlx-community/VoxCPM2-4bit","audioSpeaker":"",\
        "audioLanguage":"en","transcriptionModel":"","transcriptionLanguage":""}
        """
        try Data(persisted.utf8).write(to: launchHome.appendingPathComponent("native-workspaces.json"))
        let launched = NativeWorkspaceStore(sessionConfiguration: configuration)
        await launched.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                               dataDirectory: launchHome, token: "native-test-token")
        require(launched.audioModel != "mlx-community/VoxCPM2-4bit",
                "a persisted selection this Mac cannot run must not survive the first refresh, got: \(launched.audioModel)")
        require(launched.models.first { $0.repo == launched.audioModel }?.runtimeReady == true,
                "and the page must land on a model that can run, got: \(launched.audioModel)")
        // The tab it lands on is one the selection publishes, so the form below matches the tab.
        require(launched.models.first { $0.repo == launched.audioModel }?
                    .voiceModes.contains(launched.audioMode) == true,
                "the corrected selection must publish the tab that is showing, got: "
                + "\(launched.audioModel) / \(launched.audioMode)")

        // Which request a two-mode model builds is the tab's decision, not the model's first mode.
        // VoxCPM2 4-bit / 8-bit / bf16 (MLX 4-bit / 8-bit / bf16) clone *and* design, so picking the
        // form from a single derived mode is how the other tab's job was sent with the wrong shape.
        store.audioModel = "fixture/two-mode-voice"
        require(store.audioMode == .cloning, "a model opens on the first mode it publishes")
        store.audioVoiceLibraryID = "voice-1"
        store.audioText = "Two-mode clone"
        store.audioLanguage = "en"
        await store.generateAudio()
        require(store.error == nil, "the clone tab of a two-mode model must generate; error=\(store.error ?? "none")")
        var twoMode = FixtureProtocol.generatedBody()
        require(twoMode?["voice_library_id"] as? String == "voice-1",
                "the clone tab must send the library voice, got: \(String(describing: twoMode?["voice_library_id"]))")
        var twoModeParams = (twoMode?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(twoModeParams?["design_prompt"] == nil,
                "the clone tab must not send a design description")
        store.audioMode = .design
        store.audioDesignPrompt = "a deep, slow and warm narrator"
        store.audioText = "Two-mode design"
        await store.generateAudio()
        require(store.error == nil, "the design tab must generate; error=\(store.error ?? "none")")
        twoMode = FixtureProtocol.generatedBody()
        twoModeParams = (twoMode?["items"] as? [[String: Any]])?.first?["params"] as? [String: Any]
        require(twoModeParams?["design_prompt"] as? String == "a deep, slow and warm narrator",
                "the design tab must send the description, got: \(String(describing: twoModeParams))")
        require(twoMode?["voice_library_id"] == nil,
                "the design tab must not address a library voice")

        // The tab follows the model only as far as it must. A mode the new model does not publish
        // cannot stay selected; a mode it does publish is the operator's choice and is left alone.
        store.audioModel = "mlx-community/chatterbox-8bit"
        require(store.audioMode == .cloning,
                "a mode the model does not publish must not stay selected, got: \(store.audioMode.rawValue)")
        store.audioModel = "mlx-community/pocket-tts-4bit"
        store.audioMode = .preset
        store.audioModel = "mlx-community/pocket-tts-4bit"
        require(store.audioMode == .preset,
                "a mode the model does publish must be left where the operator put it")
        require(store.audioModels(for: .preset).contains { $0.repo == "mlx-community/pocket-tts-4bit" }
                && store.audioModels(for: .cloning).contains { $0.repo == "mlx-community/pocket-tts-4bit" },
                "a two-mode model must be offered in both of its tabs")
        require(!store.audioModels(for: .design).contains { $0.repo == "mlx-community/pocket-tts-4bit" },
                "and not in a tab it did not publish")
        store.audioMode = .cloning
        store.audioModel = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"

        // A model whose engine reported no modes cannot be generated with, and the refusal says
        // which fact is missing. The app used to guess a form and send it.
        store.audioModel = "fixture/unreported-modes"
        store.audioText = "A voice whose engine said nothing"
        let submitsBeforeUnreported = FixtureProtocol.generatedBodies().count
        await store.generateAudio()
        require(FixtureProtocol.generatedBodies().count == submitsBeforeUnreported,
                "a model with no published modes must not reach Hub dispatch")
        require(store.error?.contains("voice modes") == true,
                "and it must say which fact is missing: \(store.error ?? "nil")")
        store.error = nil
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
        require(store.musicModel == "fixture/sung-music", "the Music model must come from the catalogue")
        require(store.musicSettings?.durationSeconds == 30 && store.musicSettings?.steps == 8
                && store.musicSettings?.guidanceScale == 1 && store.musicSettings?.shift == 3
                && store.musicSettings?.seed == -1 && store.musicSettings?.instrumental == true,
                "Music defaults must come from the runtime")
        require(store.musicConfiguration?.limits.maxDurationSeconds == 600
                && store.musicConfiguration?.limits.maxGuidance == 30,
                "Music limits must come from the runtime")
        let musicModelInfo = store.models.first { $0.function == .music && $0.repo == "fixture/sung-music" }
        require(musicModelInfo?.sampleRateHz == 48000 && musicModelInfo?.channels == 2,
                "the published WAV shape must reach the app")
        store.musicPrompt = "Warm lo-fi beat"
        await store.generateMusic()
        let musicBody = FixtureProtocol.generatedBodies().last!
        require(musicBody["modality"] as? String == "music", "Music must submit through the Hub job queue")
        require(musicBody["model"] as? String == "fixture/sung-music", "Music must name its model")
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
        // A 2 min 5 s clip. The row's facts used minutes to one decimal, so this read "2.1 min" and
        // the seconds were not on screen at all - the owner's rule is that a duration states its
        // exact seconds wherever it appears, and a media length is the same claim as a run time.
        require(music.assetFacts.contains("2 min 5 s"),
                "a clip's own length must state its seconds too, got: \(music.assetFacts)")
        require(!music.assetFacts.contains("min 0 s") || music.assetFacts.contains("2 min 5 s"),
                "a clip length must not be rounded away, got: \(music.assetFacts)")
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
        // The word-timings toggle follows this model's own published default until the operator
        // answers it, and it is offered only where the runtime itself claims word timings - so a
        // model that produces none gets no toggle rather than one whose movement does nothing.
        require(store.wordTimestampsControl == false,
                "an untouched word-timings toggle must show this model's published default")
        store.transcriptionModel = "moonshine-ai/moonshine-base"
        require(store.wordTimestampsControl == nil,
                "a model whose runtime claims no word timings must be offered no toggle")
        store.transcriptionModel = "mlx-community/whisper-large-v3-turbo"
        store.wordTimestamps = true
        await store.transcribe()
        require(store.transcriptionResult?.text == "hello world", "transcription result: \(store.error ?? "no error"), pending \(FileManager.default.fileExists(atPath: state.appendingPathComponent("native-pending-transcription.json").path))")
        let multipart = FixtureProtocol.transcriptionBody()
        require(multipart.contains("name=\"model\"") && multipart.contains("name=\"files\""), "Hub multipart transcription contract")
        require(multipart.contains("name=\"item_ids\"") && multipart.contains("name=\"episode\""), "stable Hub transcription identity")
        // An answered toggle is submitted as the caller's own answer. The untouched case sends no
        // `word_timestamps` key at all, which is what `tests/test_peer_path_contract.py` pins: the
        // Subtitle worker reads a key's presence as "the caller answered", so supplying the app's
        // own `false` is what kept the Models tab's stored default out of every job.
        require(multipart.contains("name=\"word_timestamps\"") && multipart.contains("true"),
                "an answered word-timings toggle must be submitted as the operator's answer")
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

        // An older download an earlier attempt left behind. A FAILED install must move nothing to
        // the Trash Folder: the downloads are what the next press resumes from.
        let updatesFolder = updateDirectory.standardizedFileURL.appendingPathComponent("Updates")
        let hubTrash = updateDirectory.standardizedFileURL.appendingPathComponent("hub/trash")
        let olderDownload = updatesFolder.appendingPathComponent("older-release")
        try FileManager.default.createDirectory(at: olderDownload, withIntermediateDirectories: true)
        try Data("older release".utf8).write(to: olderDownload.appendingPathComponent("older.dmg"))
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(descriptor), artifact: artifact)
        await updateStore.refreshRelease()
        updateStore.installVerifiedArtifact = { _, _ in throw ReleaseError("fixture: the swap failed") }
        await updateStore.installOfferedRelease()
        require(updateStore.connectionNotice?.kind == .failure, "the failed install must be reported")
        require(FileManager.default.fileExists(atPath: olderDownload.path) && !FileManager.default.fileExists(atPath: hubTrash.path),
                "a failed install must move nothing to the Trash Folder")

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
        require(updateStore.connectionNotice?.text.contains("Relaunching") == true,
                "the report must say the app relaunches: \(updateStore.connectionNotice?.text ?? "nil")")
        require(updateStore.currentAppBuild == "newbuild",
                "the installed build must become the current one so the banner clears")
        require(updateStore.currentAppVersion == "0.6.50",
                "the installed version must become the current one too")
        // Everything under Updates/ - the installed download and the older one - is deleted at once
        // (0.6.127 disk governor: a 1.9 GB copy of an app that is already installed is not worth the
        // Trash Folder's 15 days), so nothing is left and nothing goes to the Trash Folder.
        require(((try? FileManager.default.contentsOfDirectory(atPath: updatesFolder.path)) ?? []).isEmpty,
                "nothing may be left under Updates/ after a completed install")
        require(!FileManager.default.fileExists(atPath: olderDownload.path),
                "the older download an earlier attempt left must be deleted too")
        require(!FileManager.default.fileExists(atPath: hubTrash.path),
                "a completed install deletes the stored downloads; it no longer files them in the Trash Folder")

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
        // ── An idle Mac takes an offered update itself, controller or node ──
        // (owner decision 2026-09-30, extended to controllers by the unified update). The installer is stubbed, so nothing touches /Applications.
        let autoDescriptor = makeReleaseDescriptor(version: "0.6.60", build: "autobuild", artifact: artifact,
                                                   gateway: gatewayKey, controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(autoDescriptor), artifact: artifact)
        updateStore.currentAppBuild = "currentbuild"
        updateStore.currentAppVersion = "0.6.50"
        var autoSwaps = 0
        var autoRelaunches = 0
        updateStore.installVerifiedArtifact = { _, _ in autoSwaps += 1; return "autobuild" }
        updateStore.onReleaseRestartRequested = { autoRelaunches += 1 }
        FixtureProtocol.mutate { FixtureProtocol.voiceDownloadsIdle = true }
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)   // every Studio idle
        var tick = Date(timeIntervalSince1970: 2_000_000_000)
        // A controller updates itself too - once it is drained. A busy one waits and keeps the badge.
        FixtureProtocol.currentRole = "controller"
        FixtureProtocol.setMemoryPolicyOverride(nil)                                  // the voice Studio is busy
        await updateStore.refreshConnection()
        await updateStore.releaseTick(now: tick)
        require(updateStore.showingUpdateBanner, "a busy controller must show the update badge")
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(autoSwaps == 0, "a busy controller must wait, not interrupt its work")
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)   // drained
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(autoSwaps == 1 && autoRelaunches == 1,
                "a drained controller must install the offered update and relaunch once: \(autoSwaps) swaps, \(autoRelaunches) relaunches")
        // Back to an un-updated app, so the node cases below start from the same offer.
        autoSwaps = 0
        autoRelaunches = 0
        updateStore.currentAppBuild = "currentbuild"
        updateStore.currentAppVersion = "0.6.50"
        await updateStore.refreshRelease()
        require(updateStore.updateOffered, "the release is offered again for the node cases: \(updateStore.releaseRefusal ?? "nil")")
        FixtureProtocol.currentRole = "agent"
        await updateStore.refreshConnection()
        FixtureProtocol.setMemoryPolicyOverride(nil)                                  // the voice Studio is busy
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(autoSwaps == 0, "a busy node must wait, not interrupt its work")
        require(updateStore.nextPollDelay == 2, "a visible window over active work polls every two seconds")
        updateStore.isWindowVisible = { false }
        require(updateStore.nextPollDelay == 10, "a hidden window does not poll at the busy rate")
        updateStore.isWindowVisible = { true }
        require(updateStore.activeWorkCount >= 1, "a busy Studio is active work: \(updateStore.activeWorkCount)")
        let busyNow = await updateStore.activeWorkNow()
        require(busyNow >= 1, "Quit reads fresh work and must see a busy Studio: \(busyNow)")
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(updateStore.activeWorkCount == 0, "the fixture node is idle: \(updateStore.activeWorkCount)")
        let idleNow = await updateStore.activeWorkNow()
        require(idleNow == 0, "Quit over an idle Mac must not ask: \(idleNow)")
        FixtureProtocol.setMemoryPolicyOverride(nil)                                  // work starts; no poll has seen it
        let startedNow = await updateStore.activeWorkNow()
        require(startedNow >= 1, "Quit must read fresh, not the last poll: \(startedNow)")
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)
        _ = await updateStore.activeWorkNow()
        require(autoSwaps == 1 && autoRelaunches == 1,
                "an idle node must install the offered update and relaunch once: \(autoSwaps) swaps, \(autoRelaunches) relaunches")
        // A failed unattended install is not retried for the same build (it would re-copy 5 GB a minute).
        let failing = makeReleaseDescriptor(version: "0.6.61", build: "failbuild", artifact: artifact,
                                            gateway: gatewayKey, controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(failing), artifact: artifact)
        var failedSwaps = 0
        updateStore.installVerifiedArtifact = { _, _ in failedSwaps += 1; throw ReleaseError("fixture: the copy failed") }
        await updateStore.refreshRelease()
        tick += 700
        await updateStore.releaseTick(now: tick)
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(failedSwaps == 1, "an unattended install that failed must not loop: \(failedSwaps)")
        require(updateStore.lastUpdateError?.phase == "installing" && updateStore.lastUpdateError?.build == "failbuild"
                && updateStore.lastUpdateError?.reason.contains("fixture: the copy failed") == true,
                "the failure is kept, with its phase and one-line reason, for the fleet report: \(String(describing: updateStore.lastUpdateError))")
        // ...but the same release is tried again after half an hour (nobody has to quit the app),
        tick += NativeWorkspaceStore.autoUpdateFailureRetry
        await updateStore.releaseTick(now: tick)
        require(failedSwaps == 2, "a failed release is tried again after the retry interval: \(failedSwaps)")
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(failedSwaps == 2, "and then waits out the interval again: \(failedSwaps)")
        // ...and a re-push of the same build (a new signature) is tried at once.
        let repushed = makeReleaseDescriptor(version: "0.6.61", build: "failbuild", artifact: artifact,
                                             gateway: gatewayKey, controllerSigning: controllerKey)
        require(repushed["signature"] as? String != failing["signature"] as? String,
                "the fixture's re-push must carry a different signature")
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(repushed), artifact: artifact)
        await updateStore.refreshRelease()
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(failedSwaps == 3, "a re-pushed release is not held back by the old one's failure: \(failedSwaps)")
        // The failure travels up the fleet with the startup report, so the Gateway's row can show it.
        FixtureProtocol.resetStartupSync()
        updateStore.startupReportProvider = {
            NativeWorkspaceStore.StartupReport(openAtLogin: true, openAtLoginSaved: true,
                                               startServicesOnLaunch: true, appVersion: "0.6.50")
        }
        await updateStore.startupSyncTick()
        updateStore.startupReportProvider = nil
        let reportedError = FixtureProtocol.startupSyncs().last?["last_update_error"] as? [String: Any]
        require(reportedError?["phase"] as? String == "installing" && reportedError?["build"] as? String == "failbuild"
                && (reportedError?["reason"] as? String)?.isEmpty == false,
                "the startup report must carry the last update error: \(String(describing: reportedError))")
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(failedSwaps == 3, "the re-pushed release that failed must not loop either: \(failedSwaps)")
        // The cases above ran as a node (`agent`); a controller runs the same app and gets the same rule.
        FixtureProtocol.currentRole = "controller"
        await updateStore.refreshConnection()
        let failingOnController = makeReleaseDescriptor(version: "0.6.61", build: "failctl", artifact: artifact,
                                                        gateway: gatewayKey, controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(failingOnController), artifact: artifact)
        await updateStore.refreshRelease()
        failedSwaps = 0
        tick += 120
        await updateStore.releaseTick(now: tick)
        tick += 120
        await updateStore.releaseTick(now: tick)
        require(failedSwaps == 1, "a controller's failed install does not loop: \(failedSwaps)")
        tick += NativeWorkspaceStore.autoUpdateFailureRetry
        await updateStore.releaseTick(now: tick)
        require(failedSwaps == 2 && updateStore.lastUpdateError?.build == "failctl",
                "a controller retries after the interval too, and reports it: \(failedSwaps)")
        tick += 120
        await updateStore.releaseTick(now: tick)
        FixtureProtocol.currentRole = "agent"
        await updateStore.refreshConnection()

        // ── Drain for update: a Mac that never goes idle stops taking NEW work, finishes, installs ──
        func offerDrain(_ build: String) async {
            let json = makeReleaseDescriptor(version: "0.6.62", build: build, artifact: artifact,
                                             gateway: gatewayKey, controllerSigning: controllerKey)
            FixtureProtocol.setRelease(identity: identity, body: releaseBody(json), artifact: artifact)
            updateStore.currentAppBuild = "currentbuild"
            updateStore.currentAppVersion = "0.6.50"
            await updateStore.refreshRelease()
            require(updateStore.updateOffered, "the drain release must be offered: \(updateStore.releaseRefusal ?? "nil")")
        }
        var drainSwaps = 0
        var drainRelaunches = 0
        var drainFails = false
        updateStore.installVerifiedArtifact = { _, _ in
            drainSwaps += 1
            if drainFails { throw ReleaseError("fixture: the copy failed") }
            return "drainbuild"
        }
        updateStore.onReleaseRestartRequested = { drainRelaunches += 1 }
        updateStore.updateDrainGrace = 600
        FixtureProtocol.resetUpdateDrain()
        FixtureProtocol.setMemoryPolicyOverride(nil)                                  // the voice Studio is busy, for good
        await offerDrain("drainbuild")
        tick += 700
        await updateStore.releaseTick(now: tick)
        let drainStart = tick
        require(updateStore.updateStatusLine == "Updating after current work finishes…",
                "a pending update must say so in the app: \(updateStore.updateStatusLine ?? "nil")")
        require(!updateStore.updateDraining && FixtureProtocol.drainWants().isEmpty,
                "inside the grace period a busy Mac keeps taking work")
        tick = drainStart + 500
        await updateStore.releaseTick(now: tick)
        require(!updateStore.updateDraining && FixtureProtocol.drainWants().isEmpty,
                "the grace period is ten minutes, not less")
        tick = drainStart + 601
        await updateStore.releaseTick(now: tick)
        require(updateStore.updateDraining && FixtureProtocol.drainWants() == [true],
                "after the grace period the Mac asks its Hub to stop taking new work: \(FixtureProtocol.drainWants())")
        require(drainSwaps == 0 && drainRelaunches == 0, "a draining Mac must not interrupt the work still running")
        // Work that is only QUEUED waits in the Hub's saved queue through the relaunch: it must not hold the drain open.
        FixtureProtocol.mutate { FixtureProtocol.errorBatchMode = "retried" }
        await updateStore.refresh()
        require(updateStore.countActiveWork(now: Date(), excludingQueued: false) == 1 + updateStore.countActiveWork(now: Date(), excludingQueued: true),
                "the fixture must hold a queued job that only the drain stops counting")
        tick += 10
        await updateStore.releaseTick(now: tick)
        require(drainSwaps == 0, "running work has not finished yet, so nothing installs")
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)   // the running work finished
        tick += 10
        await updateStore.releaseTick(now: tick)
        require(drainSwaps == 1 && drainRelaunches == 1,
                "once the running work finishes, the drained Mac installs and relaunches: \(drainSwaps) swaps, \(drainRelaunches) relaunches")
        tick += 10
        await updateStore.releaseTick(now: tick)
        require(!updateStore.updateDraining && FixtureProtocol.drainWants().last == false && updateStore.updateStatusLine == nil,
                "with the update taken the Mac takes work again")
        FixtureProtocol.mutate { FixtureProtocol.errorBatchMode = "off" }

        // A failed install ends the drain: the Mac takes work again, and does not loop on that build.
        drainSwaps = 0
        drainFails = true
        FixtureProtocol.resetUpdateDrain()
        FixtureProtocol.setMemoryPolicyOverride(nil)
        await offerDrain("drainfail")
        tick += 700
        await updateStore.releaseTick(now: tick)
        tick += 601
        await updateStore.releaseTick(now: tick)
        require(updateStore.updateDraining, "the second busy Mac drains after the grace period too")
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)
        tick += 10
        await updateStore.releaseTick(now: tick)
        require(drainSwaps == 1 && !updateStore.updateDraining && FixtureProtocol.drainWants().last == false,
                "a failed install must end the drain and resume taking work: \(drainSwaps) swaps, draining \(updateStore.updateDraining)")
        tick += 700
        await updateStore.releaseTick(now: tick)
        require(drainSwaps == 1 && !updateStore.updateDraining, "a build that failed is not tried or drained for again")
        drainFails = false

        // One Mac per controller group: while another holds the slot this one keeps waiting and asks again.
        FixtureProtocol.resetUpdateDrain(reply: "waiting")
        FixtureProtocol.setMemoryPolicyOverride(nil)
        await offerDrain("drainslot")
        tick += 700
        await updateStore.releaseTick(now: tick)
        tick += 601
        await updateStore.releaseTick(now: tick)
        require(!updateStore.updateDraining && FixtureProtocol.drainWants() == [true],
                "a Mac whose group already has a draining Mac must not drain")
        FixtureProtocol.resetUpdateDrain(reply: "draining")
        tick += 61                                   // a Mac still waiting asks again a minute later
        await updateStore.releaseTick(now: tick)
        require(updateStore.updateDraining, "it drains as soon as the slot is free")
        // The offer going away (a different release, or none) ends the drain too.
        FixtureProtocol.clearRelease()
        tick += 700
        await updateStore.releaseTick(now: tick)
        require(!updateStore.updateDraining && FixtureProtocol.drainWants().last == false,
                "no pending update means no drain")
        FixtureProtocol.setMemoryPolicyOverride(nil)

        // ── Startup settings: reported to the Hub on every poll, requests from the fleet applied ──
        let startupStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await startupStore.connect(baseURL: URL(string: "http://127.0.0.1:45690")!,
                                   dataDirectory: updateDirectory, token: "native-test-token")
        FixtureProtocol.resetStartupSync()
        await startupStore.startupSyncTick()
        require(FixtureProtocol.startupSyncs().isEmpty,
                "a workspace with no app around it reports nothing to the Hub it is connected to")
        var applied: [NativeWorkspaceStore.StartupRequest] = []
        var roles: [String] = []
        startupStore.startupReportProvider = {
            NativeWorkspaceStore.StartupReport(openAtLogin: true, openAtLoginSaved: true,
                                               startServicesOnLaunch: false, appVersion: "0.6.127")
        }
        startupStore.onStartupRequest = { request in
            applied.append(request)
            return request.action == "restart_services" ? nil
                : NativeWorkspaceStore.StartupOutcome(id: request.id, ok: request.action != "set_open_at_login",
                                                       detail: request.action == "set_open_at_login" ? "not in /Applications" : nil)
        }
        startupStore.onFleetRoleObserved = { roles.append($0) }
        await startupStore.startupSyncTick()
        let first = FixtureProtocol.startupSyncs()
        require(first.count == 1, "one sync per poll: \(first.count)")
        require(first[0]["open_at_login"] as? Bool == true && first[0]["open_at_login_saved"] as? Bool == true
                && first[0]["start_services_on_launch"] as? Bool == false && first[0]["app_version"] as? String == "0.6.127",
                "the settings travel under the names the Hub reads: \(first[0])")
        require((first[0]["applied"] as? [Any])?.isEmpty == true, "nothing to report back yet")
        require(roles == ["standalone"], "the Hub's role reaches the app: \(roles)")
        FixtureProtocol.resetStartupSync(answer: ["role": "agent", "requests": [
            ["id": "su-1", "action": "set_start_services_on_launch", "value": true],
            ["id": "su-2", "action": "set_open_at_login", "value": true],
            ["id": "su-3", "action": "restart_services", "value": NSNull()]]])
        await startupStore.startupSyncTick()
        require(applied.map(\.id) == ["su-1", "su-2", "su-3"] && applied[0].value == true && applied[2].value == nil,
                "every request the fleet queued is handed to the app: \(applied)")
        require(roles.last == "agent", "a node reports its role")
        FixtureProtocol.resetStartupSync()
        await startupStore.startupSyncTick()
        let second = FixtureProtocol.startupSyncs()[0]["applied"] as? [[String: Any]] ?? []
        require(second.count == 2 && second[0]["id"] as? String == "su-1" && second[0]["ok"] as? Bool == true
                && second[1]["ok"] as? Bool == false && second[1]["detail"] as? String == "not in /Applications",
                "the outcomes come back on the next call, a restart has none: \(second)")
        await startupStore.startupSyncTick()
        require((FixtureProtocol.startupSyncs()[1]["applied"] as? [Any])?.isEmpty == true,
                "an outcome is reported once, not on every poll")

        // ── The unattended path pins its own Hub's key on first read, then verifies on the next tick ──
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)
        let freshDescriptor = makeReleaseDescriptor(version: "0.6.63", build: "freshbuild", artifact: artifact,
                                                    gateway: gatewayKey, controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(freshDescriptor), artifact: artifact)
        let pinStore = NativeWorkspaceStore(sessionConfiguration: configuration)
        await pinStore.connect(baseURL: URL(string: "http://127.0.0.1:45690")!,
                                 dataDirectory: updateDirectory, token: "native-test-token")
        pinStore.currentAppBuild = "currentbuild"
        pinStore.currentAppVersion = "0.6.50"
        var pinSwaps = 0
        pinStore.installVerifiedArtifact = { _, _ in pinSwaps += 1; return "freshbuild" }
        pinStore.onReleaseRestartRequested = {}
        require(pinStore.releasePublisherKey == nil, "a Mac installed from a drive has no key pinned")
        tick += 700
        await pinStore.releaseTick(now: tick)
        require(pinStore.releasePublisherKey?.fingerprint == controllerFingerprint,
                "the unattended tick must pin its own Hub's key on the first read")
        require(pinSwaps == 0, "a key read at the same time as the artifact proves nothing: nothing installs yet")
        tick += 10
        await pinStore.releaseTick(now: tick)
        require(pinSwaps == 1, "the next tick verifies against the pin and installs, with nobody at Connections: \(pinStore.releaseRefusal ?? "nil")")

        // A different key presented later is refused, never adopted.
        let swappedKey = Curve25519.Signing.PrivateKey()
        let swappedIdentity = releaseIdentityJSON(publicKey: swappedKey.publicKey.rawRepresentation,
                                                fingerprint: releaseSha256(swappedKey.publicKey.rawRepresentation))
        let swappedDescriptor = makeReleaseDescriptor(version: "0.6.64", build: "otherbuild", artifact: artifact,
                                                    gateway: gatewayKey, controllerSigning: swappedKey)
        FixtureProtocol.setRelease(identity: swappedIdentity, body: releaseBody(swappedDescriptor), artifact: artifact)
        pinStore.currentAppBuild = "currentbuild"
        tick += 700
        await pinStore.releaseTick(now: tick)
        tick += 120
        await pinStore.releaseTick(now: tick)
        require(pinSwaps == 1 && !pinStore.updateOffered && pinStore.releasePublisherKey?.fingerprint == controllerFingerprint,
                "an update signed under a different key must not install, and must not replace the pin")
        require(pinStore.releaseRefusal?.contains("different App Update Key") == true,
                "the refusal must name the changed key: \(pinStore.releaseRefusal ?? "nil")")
        FixtureProtocol.setMemoryPolicyOverride(nil)
        FixtureProtocol.setMemoryPolicyOverride(nil)
        FixtureProtocol.mutate { FixtureProtocol.voiceDownloadsIdle = false }
        FixtureProtocol.currentRole = "standalone"
        FixtureProtocol.clearRelease()

        // ── The delta update: built on the Hub from the changed files, else the disk image ──────
        // The Hub is scripted; the installer and the staged swap are stubbed, so nothing touches /Applications.
        FixtureProtocol.setMemoryPolicyOverride(FixtureProtocol.memoryAutoFixture)
        FixtureProtocol.currentRole = "agent"
        func offerDelta(_ build: String) async -> ReleaseDescriptor {
            let json = makeReleaseDescriptor(version: "0.6.70", build: build, artifact: artifact,
                                             gateway: gatewayKey, controllerSigning: controllerKey,
                                             filesSha256: String(repeating: "d", count: 64))
            FixtureProtocol.setRelease(identity: identity, body: releaseBody(json), artifact: artifact)
            updateStore.currentAppBuild = "currentbuild"
            updateStore.currentAppVersion = "0.6.50"
            await updateStore.refreshRelease()
            require(updateStore.updateOffered, "the delta release must be offered: \(updateStore.releaseRefusal ?? "nil")")
            return ReleaseDescriptor(json: json)!
        }
        let deltaStagedHolder = temporary.appendingPathComponent(".studiohub-staged-fixture")
        let deltaStagedApp = deltaStagedHolder.appendingPathComponent("Studio Hub.app")
        try FileManager.default.createDirectory(at: deltaStagedApp, withIntermediateDirectories: true)
        var stagedSwaps = 0
        var imageInstalls = 0
        updateStore.installStagedApp = { staged, _ in
            stagedSwaps += 1
            require(staged.standardizedFileURL.path == deltaStagedApp.standardizedFileURL.path, "the swap must be given the bundle the Hub staged: \(staged.path)")
            return "deltabuild"
        }
        updateStore.installVerifiedArtifact = { _, _ in imageInstalls += 1; return "deltabuild" }

        // 1. The Hub builds it: installed from the staged bundle, the disk image is never downloaded,
        //    and the staging folder is removed afterwards.
        _ = await offerDelta("deltabuild")
        FixtureProtocol.setStageScript([["state": "running", "phase": "downloading", "done_bytes": 10, "total_bytes": 100],
                                        ["state": "staged", "phase": "staged", "staged_app": deltaStagedApp.path]])
        FixtureProtocol.resetArtifactRequests()
        await updateStore.installOfferedRelease()
        require(stagedSwaps == 1 && imageInstalls == 0,
                "a staged release installs from the staged bundle, not the image: \(stagedSwaps) staged, \(imageInstalls) image")
        require(FixtureProtocol.releaseReads() == 0, "a staged release must not download the disk image")
        require(FixtureProtocol.stageRequests() >= 2, "the app must poll the stage job: \(FixtureProtocol.stageRequests())")
        require(!FileManager.default.fileExists(atPath: deltaStagedHolder.path), "the staging folder must be removed after the swap")
        require(updateStore.connectionNotice?.kind == .success, "the staged install must succeed: \(updateStore.connectionNotice?.text ?? "nil")")

        // 2. The Hub says it cannot (no manifest, a file it could not verify): the disk image, as before.
        stagedSwaps = 0
        _ = await offerDelta("deltabuild")
        // (a stale staged_app left in the answer must not be mistaken for a bundle to install)
        FixtureProtocol.setStageScript([["state": "fallback", "code": "corrupt", "message": "a file did not hash",
                                         "staged_app": deltaStagedApp.path]])
        FixtureProtocol.resetArtifactRequests()
        await updateStore.installOfferedRelease()
        require(stagedSwaps == 0 && imageInstalls == 1 && FixtureProtocol.releaseReads() >= 1,
                "a fallback must download and install the disk image: \(stagedSwaps) staged, \(imageInstalls) image, \(FixtureProtocol.releaseReads()) reads")

        // 3. A Hub that does not know the route (404) is a fallback too, never an error.
        imageInstalls = 0
        _ = await offerDelta("deltabuild")
        FixtureProtocol.setStageScript(nil)
        FixtureProtocol.resetArtifactRequests()
        await updateStore.installOfferedRelease()
        require(imageInstalls == 1 && stagedSwaps == 0, "an older Hub falls back to the image: \(imageInstalls) image installs")

        // 4. A release with no files_sha256 never even asks the Hub to stage it.
        imageInstalls = 0
        let plainJSON = makeReleaseDescriptor(version: "0.6.71", build: "plainbuild", artifact: artifact,
                                              gateway: gatewayKey, controllerSigning: controllerKey)
        FixtureProtocol.setRelease(identity: identity, body: releaseBody(plainJSON), artifact: artifact)
        FixtureProtocol.setStageScript([["state": "staged", "staged_app": deltaStagedApp.path]])
        updateStore.installVerifiedArtifact = { _, _ in imageInstalls += 1; return "plainbuild" }
        await updateStore.refreshRelease()
        await updateStore.installOfferedRelease()
        require(FixtureProtocol.stageRequests() == 0 && imageInstalls == 1,
                "a release without a manifest goes straight to the image: \(FixtureProtocol.stageRequests()) stage requests")

        // 5. A staged bundle that fails its swap is not kept: the next attempt asks the Hub again.
        try FileManager.default.createDirectory(at: deltaStagedApp, withIntermediateDirectories: true)
        updateStore.installStagedApp = { _, _ in throw ReleaseError("fixture: the staged app did not verify") }
        _ = await offerDelta("badstaged")
        FixtureProtocol.setStageScript([["state": "staged", "staged_app": deltaStagedApp.path]])
        await updateStore.installOfferedRelease()
        require(updateStore.connectionNotice?.kind == .failure, "a failed staged swap must be reported")
        require(!FileManager.default.fileExists(atPath: deltaStagedHolder.path), "a failed staged bundle must be removed")

        // 6. A controller still receiving the release answers `not_ready`: no disk image is fetched,
        //    nothing is reported as failed, and the next automatic check simply asks again.
        stagedSwaps = 0
        imageInstalls = 0
        try FileManager.default.createDirectory(at: deltaStagedApp, withIntermediateDirectories: true)
        updateStore.installStagedApp = { _, _ in stagedSwaps += 1; return "arrivingbuild" }
        FixtureProtocol.mutate { FixtureProtocol.voiceDownloadsIdle = true }       // an idle Mac updates by itself
        _ = await offerDelta("arrivingbuild")
        let errorBefore = updateStore.lastUpdateError
        FixtureProtocol.setStageScript([["state": "running", "phase": "downloading", "done_bytes": 1, "total_bytes": 2],
                                        ["state": "not_ready", "code": "not_ready",
                                         "message": "3 files of this release have not reached this Mac yet"]])
        FixtureProtocol.resetArtifactRequests()
        tick += 700
        await updateStore.releaseTick(now: tick)
        require(stagedSwaps == 0 && imageInstalls == 0 && FixtureProtocol.releaseReads() == 0,
                "a release still arriving must not fall back to the disk image: \(imageInstalls) image, \(FixtureProtocol.releaseReads()) reads")
        require(updateStore.connectionNotice?.kind == .success && updateStore.lastUpdateError == errorBefore,
                "not ready is not a failure: \(updateStore.connectionNotice?.text ?? "nil")")
        FixtureProtocol.setStageScript([["state": "staged", "phase": "staged", "staged_app": deltaStagedApp.path]])
        tick += 61
        await updateStore.releaseTick(now: tick)
        require(stagedSwaps == 1, "the next automatic check installs it once it has arrived: \(stagedSwaps)")

        // 7. An uncertain job counts only while it is younger than the longest lane deadline; after
        //    that the Hub has given up on it and it must not keep the Mac from ever updating.
        FixtureProtocol.setUncertainImage(true)
        await updateStore.refresh()
        let uncertainJobs = updateStore.imageJobs.filter { $0.state == "uncertain" }
        require(!uncertainJobs.isEmpty, "the fixture must present an uncertain Image job")
        let created = uncertainJobs[0].createdAt ?? 0
        let young = updateStore.countActiveWork(now: Date(timeIntervalSince1970: created + 60), deadline: 3600)
        let aged = updateStore.countActiveWork(now: Date(timeIntervalSince1970: created + 7200), deadline: 3600)
        require(young - aged == uncertainJobs.count,
                "an uncertain job older than the lane deadline must stop counting as work: \(young) young, \(aged) aged")
        require(updateStore.countActiveWork(now: Date(timeIntervalSince1970: created + 7200), deadline: nil) >= young - 0,
                "with no published deadline an uncertain job still counts")
        FixtureProtocol.setUncertainImage(false)
        await updateStore.refresh()
        FixtureProtocol.setStageScript(nil)
        FixtureProtocol.setMemoryPolicyOverride(nil)
        FixtureProtocol.mutate { FixtureProtocol.voiceDownloadsIdle = false }
        FixtureProtocol.clearRelease()
        FixtureProtocol.currentRole = "standalone"

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
                // The layout the real bundle has (`runtimes/<runtime>/bin/python3`), NOT the
                // installer's own table: a runtime missing from the table used to be skipped here,
                // so a manifest the installer could not verify still built a "complete" fake app and
                // the suite stayed green (0.6.120: `video` was never in the table).
                let paths = ReleaseInstaller.runtimePaths[runtime]
                    ?? ["Contents/Resources/runtimes/\(runtime)/bin/python3"]
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

        // The replaced app goes to the Trash Folder (so an update can be rolled back for 15 days);
        // it is not deleted. Where there is no usable Trash Folder it is removed, as before.
        let trashRoot = installParent.appendingPathComponent("hub/trash")
        try Data("previous app".utf8).write(to: targetApp.appendingPathComponent("Contents/previous-app-marker"))
        try makeFakeApp(at: stagedApp, build: "newbuild")
        let keepingInstaller = ReleaseInstaller(targetApp: targetApp, trashFolder: trashRoot,
                                                attach: { _, _ in }, detach: { _ in })
        _ = try keepingInstaller.swapStagedApp(staged: stagedApp, descriptor: swapDescriptor)
        let keptApps = (FileManager.default.enumerator(at: trashRoot, includingPropertiesForKeys: nil)?
            .compactMap { $0 as? URL }.filter { $0.lastPathComponent == "previous-app-marker" }) ?? []
        require(keptApps.count == 1, "the replaced app must be in the Trash Folder, not deleted: \(keptApps)")
        require(!FileManager.default.fileExists(atPath: targetApp.appendingPathComponent("Contents/previous-app-marker").path),
                "the new app, not the old one, must be in place")
        let blockedTrash = installParent.appendingPathComponent("trash-is-a-file")
        try Data("x".utf8).write(to: blockedTrash)
        try makeFakeApp(at: stagedApp, build: "newbuild")
        let fallbackInstaller = ReleaseInstaller(targetApp: targetApp, trashFolder: blockedTrash,
                                                 attach: { _, _ in }, detach: { _ in })
        _ = try fallbackInstaller.swapStagedApp(staged: stagedApp, descriptor: swapDescriptor)
        require(((try? FileManager.default.contentsOfDirectory(atPath: installParent.path)) ?? [])
                    .filter { $0.hasPrefix(".studiohub-old-") }.isEmpty,
                "with no usable Trash Folder the replaced app is removed, as before")

        // The full-image install refuses up front when the volume cannot hold the new app beside the
        // old one, names the numbers, and leaves nothing staged and the installed app untouched.
        let roomyParent = installParent.appendingPathComponent("space-check")
        let roomyTarget = roomyParent.appendingPathComponent("Studio Hub.app")
        try makeFakeApp(at: roomyTarget, build: "currentbuild")
        var spaceCheckInstalled = false
        let tightInstaller = ReleaseInstaller(
            targetApp: roomyTarget,
            attach: { _, mount in try makeFakeApp(at: mount.appendingPathComponent("Studio Hub.app"), build: "newbuild") },
            detach: { _ in },
            availableCapacity: { _ in 1024 })
        let roomyInstaller = ReleaseInstaller(
            targetApp: roomyTarget,
            attach: { _, mount in try makeFakeApp(at: mount.appendingPathComponent("Studio Hub.app"), build: "newbuild") },
            detach: { _ in },
            availableCapacity: { _ in Int64.max / 2 })
        let placeholderImage = temporary.appendingPathComponent("space-check.dmg")
        try Data("dmg".utf8).write(to: placeholderImage)
        do {
            _ = try tightInstaller.install(artifact: placeholderImage, descriptor: swapDescriptor)
            spaceCheckInstalled = true
        } catch {
            require(error.localizedDescription.contains("not enough free space"),
                    "a full disk must be named as such, got: \(error.localizedDescription)")
        }
        require(!spaceCheckInstalled, "an install with no room must be refused, not attempted")
        let parentAfter = (try? FileManager.default.contentsOfDirectory(atPath: roomyParent.path)) ?? []
        require(parentAfter == ["Studio Hub.app"], "a refused install must stage nothing: \(parentAfter)")
        let stillCurrent = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: roomyTarget.appendingPathComponent("Contents/Info.plist")),
            options: [], format: nil) as? [String: Any]
        require(stillCurrent?["StudioHubBuild"] as? String == "currentbuild", "a refused install must leave the installed app alone")
        let roomyLanded = try roomyInstaller.install(artifact: placeholderImage, descriptor: swapDescriptor)
        require(roomyLanded == "newbuild", "with room, the same install proceeds")

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
            "video": ["runtime": "video"],
        ]
        for missing in ["voice", "image", "music", "chat", "subtitle", "video"] {
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
        // The release manifest of the build that shipped (0.6.120, c439bbf), byte for byte as the
        // installed app carries it, must pass the installer's own pre-swap check. The hand-built
        // manifests above never named `video`, so they could not see that the installer refused
        // every release that ships Video - which is why no Mac in the fleet updated itself.
        let shippedManifest = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("fixtures/release-manifest-0.6.120-c439bbf.json")
        let shippedComponents = (try JSONSerialization.jsonObject(with: Data(contentsOf: shippedManifest))
            as! [String: Any])["components"] as! [String: [String: Any]]
        require(shippedComponents["video"] != nil, "the fixture must be a manifest that ships Video")
        let shippedApp = installParent.appendingPathComponent("shipped-manifest/Studio Hub.app")
        try makeFakeApp(at: shippedApp, build: "newbuild", components: shippedComponents)
        let shippedLanded = try installer.swapStagedApp(staged: shippedApp, descriptor: swapDescriptor)
        require(shippedLanded == "newbuild",
                "the installer must accept the release manifest that actually shipped, got: \(shippedLanded)")

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

        // The agent's standby copy (2026-10-03: a Force Quit left a Mac without its Hub because the
        // running app was supervised by nothing). A free data folder means "be the app"; a held one
        // blocks until its holder ends, then exits 1 so launchd relaunches a fresh copy ONLY after a
        // crash (owner decision 2026-10-03): a Quit, a Force Quit or a memory kill exits 0, so
        // KeepAlive leaves the app stopped - relaunching after a Force Quit reloads the models and
        // recreates the memory pressure the owner just relieved.
        final class StandbyResult: @unchecked Sendable { var status: Int32?? = .none }
        let standbyDirectory = temporary.appendingPathComponent("agent-standby")
        let noReports = temporary.appendingPathComponent("no-crash-reports")
        let reports = temporary.appendingPathComponent("crash-reports")
        for folder in [standbyDirectory, noReports, reports] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        }
        require(AgentStandby.waitForRunningCopy(directory: standbyDirectory, crashReports: noReports,
                                                crashReportWait: 0) == nil,
                "with nobody holding the data folder the agent's copy must start as the app")
        func lastStop() -> [String: Any]? {
            (try? Data(contentsOf: standbyDirectory.appendingPathComponent(StopRecord.fileName)))
                .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        }
        // (how the running app ended, crash report folder, standby exit status, recorded reason)
        let endings: [(String, URL, Int32, String)] = [
            ("quit", noReports, 0, "quit"),
            ("crash signal", noReports, 1, "crashed"),
            ("crash report only", reports, 1, "crashed"),
            ("SIGTERM", noReports, 0, "force_quit"),
            ("SIGKILL", noReports, 0, "force_quit"),
            ("SIGKILL under critical memory pressure", noReports, 0, "low_memory"),
            ("update relaunch", noReports, 0, "updated"),
        ]
        for (ending, crashReports, expected, reason) in endings {
            var holder: InstanceLock? = try InstanceLock(directory: standbyDirectory)
            StopRecord.markRunning(directory: standbyDirectory, now: Date().addingTimeInterval(-60))
            let result = StandbyResult()
            let finished = DispatchSemaphore(value: 0)
            Thread {
                result.status = .some(AgentStandby.waitForRunningCopy(
                    directory: standbyDirectory, crashReports: crashReports, crashReportWait: 0))
                finished.signal()
            }.start()
            require(finished.wait(timeout: .now() + 0.3) == .timedOut,
                    "the standby must wait while the running app holds the data folder")
            // What the app leaves behind as it ends.
            switch ending {
            case "quit": AgentStandby.markCleanQuit(directory: standbyDirectory)
            case "crash signal":
                FileManager.default.createFile(atPath: standbyDirectory.appendingPathComponent(StopRecord.crashedMarkerName).path, contents: Data([11]))
            case "crash report only":
                FileManager.default.createFile(atPath: reports.appendingPathComponent("StudioHub-2026-10-03-093600.ips").path, contents: Data())
            case "SIGTERM":
                FileManager.default.createFile(atPath: standbyDirectory.appendingPathComponent(StopRecord.terminatedMarkerName).path, contents: Data([15]))
            case "SIGKILL under critical memory pressure": StopRecord.noteCriticalMemory(directory: standbyDirectory)
            case "update relaunch":
                StopRecord.noteUpdating(directory: standbyDirectory); AgentStandby.markCleanQuit(directory: standbyDirectory)
            default: break  // SIGKILL leaves nothing
            }
            withExtendedLifetime(holder) {}
            holder = nil
            require(finished.wait(timeout: .now() + 5) == .success,
                    "the standby must wake when the running app ends")
            require(result.status == .some(expected),
                    "after \(ending) the standby must exit \(expected), got \(String(describing: result.status))")
            let record = lastStop()
            require(record?["reason"] as? String == reason,
                    "after \(ending) last-stop.json must say \(reason), got \(String(describing: record))")
            require(!(record?["label"] as? String ?? "").isEmpty && record?["at"] is Double,
                    "the record carries a plain label and a time: \(String(describing: record))")
            require(!FileManager.default.fileExists(atPath: standbyDirectory.appendingPathComponent(StopRecord.runningMarkerName).path),
                    "settling consumes the evidence")
            require(StopRecord.settle(directory: standbyDirectory, relaunching: false, crashReports: noReports) == nil,
                    "a settled stop is not settled twice")
            require(lastStop()?["reason"] as? String == reason, "a second settle keeps the record")
            try? FileManager.default.removeItem(at: reports.appendingPathComponent("StudioHub-2026-10-03-093600.ips"))
        }
        require(lastStop() != nil && (StopRecord.settle(directory: standbyDirectory, relaunching: true) == nil),
                "nothing to settle without evidence")

        // The decision itself: only a crash relaunches.
        let stopped = Date(timeIntervalSince1970: 1_800_000_000)
        let booted = stopped.addingTimeInterval(-86_400)
        func verdict(cleanQuit: Bool = false, crashed: Bool = false, terminated: Bool = false,
                     boot: Date = booted, pressure: Date? = nil, updating: Bool = false,
                     poweringOff: Bool = false) -> StopReason {
            StopRecord.classify(cleanQuit: cleanQuit, crashed: crashed, terminated: terminated,
                                stoppedAt: stopped, bootTime: boot, memoryCriticalAt: pressure,
                                updating: updating, poweringOff: poweringOff)
        }
        require(verdict(cleanQuit: true) == .quit, "a Quit is a Quit")
        require(verdict(cleanQuit: true, poweringOff: true) == .macRestarted, "a Quit by a restart/shutdown/logout")
        require(verdict(cleanQuit: true, updating: true) == .updated, "a Quit for an update")
        require(verdict(crashed: true) == .crashed && verdict(cleanQuit: true, crashed: true) == .crashed,
                "crash evidence wins")
        require(verdict(terminated: true) == .forceQuit, "SIGTERM is a Force Quit")
        require(verdict() == .forceQuit, "an uncaught kill with no memory pressure is a Force Quit")
        require(verdict(pressure: stopped.addingTimeInterval(-120)) == .lowMemory,
                "an uncaught kill right after critical memory pressure is a low-memory stop")
        require(verdict(pressure: stopped.addingTimeInterval(-3_600)) == .forceQuit,
                "memory pressure an hour earlier says nothing about this kill")
        require(verdict(boot: stopped.addingTimeInterval(300)) == .macRestarted,
                "no marker and a boot after the last heartbeat is a restart or a power cut")
        for reason in [StopReason.quit, .updated, .forceQuit, .lowMemory, .macRestarted] {
            require(!StopRecord.relaunches(reason) && AgentStandby.exitStatus(for: reason) == 0,
                    "\(reason) must leave the app stopped")
        }
        require(StopRecord.relaunches(.crashed) && AgentStandby.exitStatus(for: .crashed) == 1,
                "a crash must relaunch")
        // The launch agent itself keeps relaunching on any unsuccessful exit, so the plist needs no
        // migration on Macs that already have it.
        require((LaunchAgent.propertyList(executablePath: "/x")["KeepAlive"] as? [String: Any])?["SuccessfulExit"] as? Bool == false,
                "KeepAlive is unchanged; the decision lives in the app")

        // A copy launchd starts with the folder free - `KeepAlive` relaunching an app it ran
        // directly - exits 0 straight away after a kill, and starts after a crash. A login launch
        // long after a kill, or after a Quit, starts too.
        let relaunchDirectory = temporary.appendingPathComponent("agent-relaunch")
        try FileManager.default.createDirectory(at: relaunchDirectory, withIntermediateDirectories: true)
        let now = Date()
        StopRecord.markRunning(directory: relaunchDirectory, now: now.addingTimeInterval(-600))
        require(AgentStandby.shouldStayStopped(directory: relaunchDirectory, now: now,
                                               crashReports: noReports, crashReportWait: 0),
                "a launchd relaunch moments after a Force Quit must stay stopped")
        StopRecord.markRunning(directory: relaunchDirectory, now: now.addingTimeInterval(-600))
        FileManager.default.createFile(atPath: relaunchDirectory.appendingPathComponent(StopRecord.crashedMarkerName).path, contents: Data([6]))
        require(!AgentStandby.shouldStayStopped(directory: relaunchDirectory, now: now,
                                                crashReports: noReports, crashReportWait: 0),
                "a launchd relaunch after a crash must start")
        StopRecord.markRunning(directory: relaunchDirectory, now: now.addingTimeInterval(-600))
        AgentStandby.markCleanQuit(directory: relaunchDirectory)
        require(!AgentStandby.shouldStayStopped(directory: relaunchDirectory, now: now,
                                                crashReports: noReports, crashReportWait: 0),
                "a login launch just after a Quit must start")
        StopRecord.markRunning(directory: relaunchDirectory, now: now.addingTimeInterval(-7_200))
        utimes(relaunchDirectory.appendingPathComponent(StopRecord.runningMarkerName).path,
               [timeval(tv_sec: Int(now.timeIntervalSince1970) - 3_600, tv_usec: 0),
                timeval(tv_sec: Int(now.timeIntervalSince1970) - 3_600, tv_usec: 0)])
        require(!AgentStandby.shouldStayStopped(directory: relaunchDirectory, now: now,
                                                crashReports: noReports, crashReportWait: 0),
                "a login launch an hour after a kill must start")
        require(!AgentStandby.shouldStayStopped(directory: relaunchDirectory, now: now,
                                                crashReports: noReports, crashReportWait: 0),
                "a first launch with no evidence must start")

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


        // ── The Models tab: one section per Studio, per-generator facts, per-model defaults ────

        // The harness disconnected this store before the update sections; put it back on the
        // fixture Hub so the reads below reach the fixture instead of no-opping.
        await store.connect(baseURL: URL(string: "http://127.0.0.1:45678")!,
                            dataDirectory: state, token: "native-test-token")

        // A section per Studio, in the app's own Studio list's order — derived, never listed here.
        // Adding a case to `StudioFunction` and nothing else must change this screen, which is what
        // this assertion is: the sections ARE the enum, in its order, with no Studio missing.
        let sections = store.modelSections(search: "", installedOnly: false)
        require(sections.map(\.function) == StudioFunction.allCases,
                "one section per Studio, in the Studio list's own order, got: \(sections.map(\.function.rawValue))")
        require(sections.allSatisfy { $0.symbol == $0.function.symbol },
                "each section must carry its own Studio's symbol")
        guard let imageSection = sections.first(where: { $0.function == .image }) else {
            fatalError("the fixture publishes image models, so Image must have a section")
        }
        require(imageSection.header == "Image — \(imageSection.models.count) models",
                "the header must be the Studio's own name and its count, got: \(imageSection.header)")
        // The existing filters still apply inside a section, and a search reaches every Studio: the
        // narrow-bound fixture is found by its label, and only the section holding it is listed.
        let searched = store.modelSections(search: "Narrow Image", installedOnly: false)
        require(searched.count == 1 && searched.first?.function == .image
                && searched.first?.models.map(\.repo) == ["fixture/narrow-image"],
                "a search must reach into every Studio and list the sections that matched, got: \(searched.map(\.function.rawValue))")
        require(store.modelSections(search: "narrow image", installedOnly: true).count == 0
                || store.modelSections(search: "narrow image", installedOnly: true).first?.models.isEmpty == false,
                "the installed-only filter must still apply inside a search")

        func fact(_ label: String, _ model: NativeModel?) -> NativeModelFact? {
            (model?.facts ?? []).first { $0.label == label }
        }
        func deliveryFact(_ label: String, _ model: NativeModel?) -> NativeModelFact? {
            (model?.longForm?.facts ?? []).first { $0.label == label }
        }

        // Chat: the model's own limits, each with the artefact that states it — the number beside
        // the source, never a curated substitute.
        let chatModel = store.models.first { $0.repo == "Qwen/Qwen3.5-9B" }
        require(fact("context_window_tokens", chatModel)?.value == "262144",
                "the chat context window must be the model's own number, got: \(fact("context_window_tokens", chatModel)?.value ?? "nil")")
        require(fact("context_window_tokens", chatModel)?.source
                    == "model/config.json text_config.max_position_embeddings",
                "the context window must carry the source that states it, from the row's model_limits")
        require(chatModel?.modelLimits?["context_window_tokens"]?.value?.intValue == 262144,
                "the row's model_limits object is the only source of a limit with provenance")
        require(fact("max_output_tokens", chatModel)?.value == "4096"
                && fact("max_output_tokens", chatModel)?.source?.contains("worker default") == true,
                "a worker's fallback must say it is a worker's fallback, got: \(fact("max_output_tokens", chatModel)?.line ?? "nil")")
        require(fact("Family", chatModel)?.value == "chat-mlx", "the family is a published fact")
        require(fact("Label", chatModel)?.value == "Qwen3.5-9B 4-bit"
                && fact("Label", chatModel)?.source == "label",
                "the published label must be shown verbatim, bits and all, got: \(fact("Label", chatModel)?.line ?? "nil")")
        require(chatModel?.facts.contains { $0.label == "Quantization" } == false,
                "nothing may label a label as a quantization: the row states no such field")
        let untagged = store.models.first { $0.function == .transcription && $0.repo == "mlx-community/whisper-large-v3-turbo" }
        require(untagged?.facts.contains { $0.label == "Quantization" } == false
                && fact("Label", untagged)?.value == "Whisper turbo",
                "a model whose label carries no bits must not be given a quantization line")
        require(fact("Memory tier", chatModel)?.value == "16 GB"
                && fact("Memory tier", chatModel)?.source == "catalog row min_total_memory_gb",
                "the memory tier must be the published one, with its key")
        require(fact("messages", chatModel)?.value.contains("required") == true,
                "a required parameter is shown as required, not offered as a default")

        // Image: the size table with the pixels each aspect ratio means and the tier published, the
        // steps/guidance bounds, and the model's own answer about guidance.
        let narrow = store.models.first { $0.repo == "fixture/narrow-image" }
        require(fact("4:3", narrow)?.value == "800 × 600 · 1K · default",
                "the size table must state the pixels and the tier, got: \(fact("4:3", narrow)?.value ?? "nil")")
        require(fact("3:2", narrow)?.value == "900 × 600 · 1K",
                "every published aspect ratio belongs in the size table")
        require(fact("steps", narrow)?.value == "3–5 · default 4",
                "the steps range must be the lane's own, got: \(fact("steps", narrow)?.value ?? "nil")")
        require(fact("resolution", narrow)?.value == "one of 1K · default 1K",
                "the resolution tiers must be the ones the model publishes — one, not more")
        require(fact("Guidance", narrow)?.value == "accepted and ignored by this model",
                "a model that ignores guidance must say so rather than imply it works")

        // Voice: the long-form bounds with their step and engine default, the hard cap, the join
        // pause, and how the speed control reaches the engine.
        let voiceModel = store.models.first { $0.repo == "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit" }
        require(deliveryFact("Section size", voiceModel)?.value == "1600 characters · engine default",
                "the section size in force must name the engine's own default, got: \(deliveryFact("Section size", voiceModel)?.value ?? "nil")")
        require(deliveryFact("Adjustable range", voiceModel)?.value == "400–4000 characters · step 50",
                "the adjustable range must carry the lane's own step, got: \(deliveryFact("Adjustable range", voiceModel)?.value ?? "nil")")
        require(deliveryFact("Hard cap", voiceModel)?.value == "stops reading past 5000 characters",
                "the hard cap must be shown")
        require(deliveryFact("Between sections", voiceModel)?.value == "120 ms", "the join pause must be shown")
        require(deliveryFact("Speed control", voiceModel)?.value == "engine-native · 0.5–2×",
                "the speed fact must carry both the range and how it is delivered, got: \(deliveryFact("Speed control", voiceModel)?.value ?? "nil")")
        require(fact("Voice modes", voiceModel)?.value == "Ready-Made Voices",
                "the modes a model offers must come from its own row")

        // Music: the duration ladder with the host's single-pass cap, and whether vocals and lyrics
        // are accepted at all — read from the parameters the lane published.
        let musicModel = store.models.first { $0.repo == "fixture/sung-music" }
        require(fact("duration_seconds", musicModel)?.value == "1–600 · default 30",
                "the duration ladder must be the lane's own, got: \(fact("duration_seconds", musicModel)?.value ?? "nil")")
        require(fact("steps", musicModel)?.value == "1–50 · default 8", "Music's steps range must be shown")
        require(fact("guidance_scale", musicModel)?.value == "0–30 · default 1", "Music's guidance range must be shown")
        require(fact("shift", musicModel)?.value == "0–20", "Music's shift range must be shown")
        require(fact("duration_seconds ladder", musicModel)?.value
                    == "1 s | 30 s | 60 s | 120 s | 240 s | 480 s | 600 s (renders in sections)",
                "the published ladder must be shown in the lane's own labels, with the rung that "
                + "is rendered in sections marked, got: \(fact("duration_seconds ladder", musicModel)?.value ?? "nil")")
        require(fact("Single-pass render", musicModel)?.value
                    == "up to 480 s · longer durations are rendered in sections",
                "the single-pass maximum is a different fact from the model's own maximum")
        require(fact("Vocals & lyrics", musicModel)?.value
                    == "accepted: instrumental, lyrics, vocal_language",
                "an engine that can sing must say which controls it accepts, got: \(fact("Vocals & lyrics", musicModel)?.value ?? "nil")")
        require(fact("Sample rate", musicModel)?.value == "48000 Hz · 2 channels",
                "the output shape must be shown")
        let instrumentalOnly = store.models.first { $0.repo == "fixture/second-music" }
        require(fact("duration_seconds ladder", instrumentalOnly)?.value
                    == "1 s | 30 s | 60 s | 120 s | 240 s | 380 s",
                "a ladder with no sectioned rung must render without one")
        require(fact("Vocals & lyrics", instrumentalOnly)?.value
                    == "not accepted: this lane publishes no vocal or lyric parameter",
                "a runtime that forces instrumental output must say so, got: \(fact("Vocals & lyrics", instrumentalOnly)?.value ?? "nil")")

        // Subtitle: languages, both timestamp claims, and the weight format the package *states*.
        let subtitleModel = store.models.first { $0.function == .transcription && $0.repo == "mlx-community/whisper-large-v3-turbo" }
        require(fact("Languages", subtitleModel)?.value == "Multilingual", "the manifest's languages must be shown")
        require(fact("Segment timestamps", subtitleModel)?.value == "supported",
                "the manifest's segment-timestamp claim must be shown")
        require(fact("Word timestamps", subtitleModel)?.value == "supported",
                "the manifest's word-timestamp claim must be shown")
        // The format is the row's own `weight_format`, verbatim. It used to be derived by joining
        // the extensions off the file receipt - an interpretation standing in for a fact, which
        // read a sharded package as the format "index, safetensors" and had no answer at all for a
        // package with no loadable weights.
        require(fact("Weight format", subtitleModel)?.value == "safetensors",
                "the weight format must be the row's own stated value, got: \(fact("Weight format", subtitleModel)?.value ?? "nil")")
        require(fact("Weight format", subtitleModel)?.source == "catalog row weight_format",
                "the stated format must name the field it came from")
        // The receipt stays, as its own fact: which files the runtime found is real data.
        require(fact("Weight files", subtitleModel)?.value == "weights.safetensors",
                "the file receipt must still be shown, got: \(fact("Weight files", subtitleModel)?.value ?? "nil")")
        // A row that states no format claims none, rather than inferring one from a file name.
        let unstatedFormat = store.models.first { $0.function == .transcription && $0.repo == "moonshine-ai/moonshine-base" }
        require(fact("Weight format", unstatedFormat) == nil,
                "a package that states no weight format must claim none, got: \(fact("Weight format", unstatedFormat)?.value ?? "nil")")

        // The editor is built from the Studio's own route: one control per settable parameter, in
        // the Studio's order, with the Studio's bounds.
        await store.loadModelDefaultsIfNeeded()
        require(FixtureProtocol.modelDefaultsRequestLog().contains("GET image"),
                "the Models tab must read each Studio's published defaults: \(FixtureProtocol.modelDefaultsRequestLog())")
        require(store.modelDefaultsSettingsFile == "model_defaults.json",
                "the file the values live in must come from the Studio's own answer")
        guard let narrowModel = narrow else { fatalError("the narrow fixture must be in the library") }
        let controls = store.modelDefaultControls(for: narrowModel)
        require(controls.map(\.name) == ["aspect_ratio", "steps", "guidance", "seed"],
                "the editor's controls must be the Studio's settable parameters in its own order, got: \(controls.map(\.name))")
        // The one-option enum the worker does not offer a control for is still a stated value: the
        // image request has to carry a resolution whether or not there is a picker for it.
        require(store.savedDefault("resolution", for: narrowModel)?.stringValue == "1K"
                && !controls.contains { $0.name == "resolution" },
                "a parameter with no control must still be in the values in force")
        require(!controls.contains { $0.name == "prompt" },
                "a required parameter is per-request content and must render no control")
        require(controls.first { $0.name == "steps" }?.boundText == "3–5 · default 4",
                "the narrow bound must be rendered exactly, got: \(controls.first { $0.name == "steps" }?.boundText ?? "nil")")
        require(controls.first { $0.name == "aspect_ratio" }?.boundText == "one of 4:3, 3:2 · default 4:3",
                "an enum control must offer exactly the lane's options in its order")
        require(controls.first { $0.name == "guidance" }?.ignored == true
                && controls.first { $0.name == "guidance" }?.sendsValue == false,
                "a parameter the lane ignores must be shown as ignored and never sent")
        // The structured field outranks the prose note, and the note still covers a lane that has
        // not published the field yet.
        let noteOnly = store.modelDefaultControls(for: store.models.first { $0.repo == "fixture/flux" }!)
        require(noteOnly.first { $0.name == "guidance" }?.ignored == true
                && noteOnly.first { $0.name == "guidance" }?.sendsValue == false,
                "a lane that states it in a note alone must still be obeyed")
        // The chat lane publishes two shapes this editor has no rule for (an array and an object),
        // and both are shown rather than hidden or faked as editable.
        let chatControls = store.modelDefaultControls(for: chatModel!)
        require(chatControls.first { $0.name == "response_format" }?.sendsValue == false,
                "a parameter whose type this editor cannot render must not be sent")
        require(chatControls.first { $0.name == "top_p" }?.boundText.contains(">0") == true,
                "an exclusive bound must be rendered as one, got: \(chatControls.first { $0.name == "top_p" }?.boundText ?? "nil")")
        require(chatControls.first { $0.name == "max_tokens" }?.boundText == "1–4096 · default 4096",
                "the chat maximum must be the model's own published ceiling")
        let durationControl = store.modelDefaultControls(for: musicModel!).first { $0.name == "duration_seconds" }
        require(durationControl?.ladder?.count == 7
                && durationControl?.ladder?.last?.exceedsSinglePass == true
                && durationControl?.ladder?.last?.label == "600 s",
                "the duration control must carry the lane's ladder, flagged as published")
        require(durationControl?.boundText == "1–600 · default 30",
                "the ladder must not narrow the bound: the control stays the model's own range, got: \(durationControl?.boundText ?? "nil")")

        // A worker that wrongly lists a required parameter as settable still renders no control for
        // it: the rule is the app's own as well as the route's, because a stored default for
        // per-request content would answer with something nobody wrote this time.
        func laneParameter(_ name: String, required: Bool, type: String, options: [String]? = nil,
                           minimum: Double? = nil, maximum: Double? = nil,
                           defaultValue: NativeJSONValue? = nil) -> NativeLaneParameter {
            NativeLaneParameter(name: name, type: type, required: required, minimum: minimum,
                                maximum: maximum, minimumExclusive: nil, maximumExclusive: nil,
                                maxCharacters: nil, maxBytes: nil, options: options,
                                defaultValue: defaultValue, note: nil, ignored: nil, ladder: nil,
                                boundSource: nil)
        }
        let synthetic = NativeModelDefaults(
            repo: "fixture/synthetic", defaults: [:], stated: [:], chosen: [:], notUsed: [:],
            parameters: [laneParameter("prompt", required: true, type: "string"),
                         laneParameter("steps", required: false, type: "integer",
                                       minimum: 2, maximum: 4, defaultValue: .integer(2))])
        require(NativeWorkspaceStore.modelDefaultControls(synthetic).map(\.name) == ["steps"],
                "a required parameter must render no control, in the lane's order and nothing else")

        // Saving a value inside the published bound, and taking the Studio's own answer as the
        // result: the value in force, marked as chosen.
        store.error = nil
        await store.saveModelDefaults(for: narrowModel,
                                      values: ["steps": .integer(5), "aspect_ratio": .string("3:2")])
        require(FixtureProtocol.lastModelDefaultsRequest()?["repo"] as? String == "fixture/narrow-image",
                "the PUT must name the model it is about")
        require(store.modelDefaultControls(for: narrowModel).first { $0.name == "steps" }?.value?.intValue == 5,
                "the Studio's answer is what the editor shows after a save")
        require(store.modelDefaultControls(for: narrowModel).first { $0.name == "steps" }?.chosen == true,
                "a value the operator chose must be marked as chosen")

        // A refused value answers 422 with a reason per parameter, and each reason belongs to its
        // own field — not to one generic banner.
        await store.saveModelDefaults(for: narrowModel,
                                      values: ["steps": .integer(99), "aspect_ratio": .string("7:3")])
        let refusals = store.modelDefaultRefusals(for: narrowModel)
        require(refusals["steps"]?.contains("above the lane's maximum") == true,
                "the refused steps value must carry the lane's own reason, got: \(refusals["steps"] ?? "nil")")
        require(refusals["aspect_ratio"]?.contains("is not one of") == true,
                "the refused enum value must carry its own reason, got: \(refusals["aspect_ratio"] ?? "nil")")
        require(refusals.count == 2, "every refused value must be reported, got: \(refusals)")
        require(store.error == nil,
                "a per-parameter refusal must not be collapsed into the shared banner, got: \(store.error ?? "nil")")
        require(store.modelDefaultControls(for: narrowModel).first { $0.name == "steps" }?.value?.intValue == 5,
                "a refused save must leave the values in force untouched")

        // A stored value the lane now refuses is shown with its reason and is not in force.
        FixtureProtocol.seedStoredDefault(modality: "image", repo: "fixture/narrow-image",
                                          values: ["steps": 40])
        await store.refreshModelDefaults(modality: "image")
        let notUsed = store.modelDefaultsNotUsed(for: narrowModel)
        require(notUsed.count == 1 && notUsed[0].hasPrefix("steps — ")
                && notUsed[0].contains("above the lane's maximum"),
                "a stored value the lane refuses must be shown with its reason, got: \(notUsed)")
        require(store.modelDefaultControls(for: narrowModel).first { $0.name == "steps" }?.value?.intValue == 4,
                "a refused stored value must not be the value in force")

        // Restore sends the DELETE and the editor returns to what the Studio states.
        FixtureProtocol.resetModelDefaultsRequests()
        await store.restoreModelDefaults(for: narrowModel)
        require(FixtureProtocol.modelDefaultsRequestLog() == ["DELETE image"],
                "Restore must issue the DELETE for this model's Studio, got: \(FixtureProtocol.modelDefaultsRequestLog())")
        let restored = store.modelDefaultControls(for: narrowModel)
        require(restored.first { $0.name == "steps" }?.value?.intValue == 4
                && restored.first { $0.name == "steps" }?.chosen == false,
                "Restore must return the control to what the lane states")
        require(restored.first { $0.name == "aspect_ratio" }?.value?.stringValue == "4:3",
                "every restored control must return to the lane's own value")
        require(store.modelDefaultsNotUsed(for: narrowModel).isEmpty,
                "Restore must clear the values the lane no longer accepts")
        require(store.modelDefaultRefusals(for: narrowModel).isEmpty,
                "Restore must clear the refusals it replaced")

        // The generator page opens on the catalogue's resolved value — the worker's own answer to
        // "what would this job run with?" — and adopts a save on the next refresh.
        store.imageModel = "fixture/narrow-image"
        store.restoreImageDefaults()
        require(store.imageSettings?.steps == 4,
                "with nothing saved, the page opens on the model's own default, got: \(String(describing: store.imageSettings?.steps))")
        await store.saveModelDefaults(for: narrowModel,
                                      values: ["steps": .integer(5), "aspect_ratio": .string("3:2")])
        require(store.imageSettings?.steps == 5,
                "the Image page must pick up the resolved default on the refresh after a save, got: \(String(describing: store.imageSettings?.steps))")
        require(store.imageSettings?.width == 900 && store.imageSettings?.height == 600,
                "the saved aspect ratio must reach the page as the pixels the catalogue resolves, got: \(String(describing: store.imageSettings))")
        store.imageModel = "fixture/flux"
        store.imageModel = "fixture/narrow-image"
        require(store.imageSettings?.steps == 5,
                "reopening the model must show the resolved default, got: \(String(describing: store.imageSettings?.steps))")
        // ...and a value the operator has since typed is never overwritten by a poll.
        store.imageSettings?.steps = 9
        await store.refresh()
        require(store.imageSettings?.steps == 9,
                "a refresh must not overwrite a value the operator typed, got: \(String(describing: store.imageSettings?.steps))")

        let instrumentalModel = store.models.first { $0.repo == "fixture/second-music" }
        require(instrumentalModel != nil, "the second music fixture must be in the library")
        store.musicModel = "fixture/second-music"
        await store.saveModelDefaults(for: instrumentalModel!,
                                      values: ["duration_seconds": .number(45), "steps": .integer(12)])
        store.musicModel = "fixture/sung-music"
        store.musicModel = "fixture/second-music"
        require(store.musicSettings?.durationSeconds == 45 && store.musicSettings?.steps == 12,
                "the Music page must show the catalogue's resolved duration and steps, got: \(String(describing: store.musicSettings))")

        print("Native API contract checks passed")
    }
}
