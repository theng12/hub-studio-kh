import Foundation
import CryptoKit

/// The node half of the remote app update in
/// `.planning/2026-09-10-standalone-app-feasibility/remote-update-design.md`,
/// "Wire contract (chosen, to build against)".
///
/// The controller (`components/hub/backend/app_release.py`) is already built; this file matches
/// its byte contract exactly:
///
/// * canonical bytes are sorted keys, `,`/`:` separators, no insignificant whitespace, UTF-8,
///   with one signature field removed;
/// * a signature is standard base64 **with padding** over 64 raw Ed25519 bytes;
/// * a key id / fingerprint is lowercase hex SHA-256 of the raw 32-byte public key.
///
/// Verification uses CryptoKit's ``Curve25519/Signing`` (Ed25519). No curve arithmetic is
/// implemented here.

/// A refusal a caller can show verbatim. Kept out of `NativeAPIError` so this file compiles with
/// the same boundary discipline as the rest of the native code.
struct ReleaseError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

// MARK: - Canonical JSON

/// Canonical descriptor bytes, byte for byte the same as the controller's
/// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
///
/// `JSONSerialization` cannot be used for this: it escapes `/` as `\/`, which Python does not, and
/// a base64 signature usually contains `/`. A mismatch there would make every real release fail
/// verification, so the escaping is done here deliberately.
enum ReleaseCanonicalJSON {
    static func encode(_ value: Any) throws -> Data {
        Data(try text(value).utf8)
    }

    /// Canonical bytes with one field removed — the bytes a signature covers.
    static func signingBytes(_ descriptor: [String: Any], removing field: String) throws -> Data {
        var body = descriptor
        body.removeValue(forKey: field)
        return try encode(body)
    }

    private static func text(_ value: Any) throws -> String {
        switch value {
        case let dictionary as [String: Any]:
            let pairs = try dictionary.keys.sorted().map { key -> String in
                "\(quote(key)):\(try text(dictionary[key]!))"
            }
            return "{\(pairs.joined(separator: ","))}"
        case let array as [Any]:
            return "[\(try array.map { try text($0) }.joined(separator: ","))]"
        case let string as String:
            return quote(string)
        case let number as NSNumber:
            return numberText(number)
        case is NSNull:
            return "null"
        default:
            throw ReleaseError("The release descriptor holds a value this Mac cannot canonicalise.")
        }
    }

    private static func numberText(_ number: NSNumber) -> String {
        if CFGetTypeID(number) == CFBooleanGetTypeID() {
            return number.boolValue ? "true" : "false"
        }
        let type = String(cString: number.objCType)
        if type == "d" || type == "f" {
            let value = number.doubleValue
            if value.rounded() == value, value.magnitude < 1e15 {
                return String(Int64(value)) + ".0"
            }
            return String(value)
        }
        return number.stringValue
    }

    /// JSON string escaping with `ensure_ascii=False`: `"`, `\` and the C0 controls are escaped,
    /// everything else is emitted as raw UTF-8. Python's `json` escapes exactly these.
    private static func quote(_ string: String) -> String {
        var output = "\""
        for scalar in string.unicodeScalars {
            switch scalar.value {
            case 0x22: output += "\\\""
            case 0x5C: output += "\\\\"
            case 0x08: output += "\\b"
            case 0x0C: output += "\\f"
            case 0x0A: output += "\\n"
            case 0x0D: output += "\\r"
            case 0x09: output += "\\t"
            default:
                if scalar.value < 0x20 {
                    output += String(format: "\\u%04x", scalar.value)
                } else {
                    output.unicodeScalars.append(scalar)
                }
            }
        }
        return output + "\""
    }
}

// MARK: - Keys and signatures

/// The controller's release-signing public key, and the fingerprint the node pins with it.
struct ReleasePublisherKey: Equatable {
    /// Lowercase hex of the raw 32-byte Ed25519 public key.
    let publicKey: String
    /// Lowercase hex SHA-256 of the raw public key.
    let fingerprint: String

    /// A short, human-readable fingerprint for the banner.
    var shortFingerprint: String { String(fingerprint.prefix(12)) }
}

/// The controller's own release-signing identity as `GET /api/hub/release/identity` reports it:
/// what a joining node pins. The algorithm is carried rather than assumed so the screen can say
/// what the controller actually signs with.
struct ReleaseIdentity: Equatable {
    /// The route's own algorithm string (the app refuses anything but ``ed25519``).
    let algorithm: String
    /// Lowercase hex of the raw 32-byte public key.
    let publicKey: String
    /// Lowercase hex SHA-256 of the raw public key.
    let fingerprint: String

    /// A short, human-readable fingerprint for the refusal banner.
    var shortFingerprint: String { String(fingerprint.prefix(12)) }

    var publisherKey: ReleasePublisherKey {
        ReleasePublisherKey(publicKey: publicKey, fingerprint: fingerprint)
    }
}

/// The gateway publisher key the owner installed on this controller, reported by
/// `GET /api/hub/release` as ``trusted_publisher``. ``nil`` (the whole value) means no key has
/// been installed, so this Mac cannot accept a pushed release. A body carrying neither a
/// publisher id nor a 64-hex fingerprint is treated the same way rather than invented.
struct TrustedPublisher: Equatable {
    let publisherId: String?
    let fingerprint: String?

    /// The fingerprint to show, or nil when there is no usable key.
    var installedFingerprint: String? { fingerprint }
}

enum ReleaseSignature {
    static func isHex64(_ text: String) -> Bool {
        text.count == 64 && text.allSatisfy { $0.isHexDigit }
    }

    /// 64 hex characters, normalised to lowercase, or nil.
    static func normalizedHex(_ text: String) -> String? {
        let lower = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return isHex64(lower) ? lower : nil
    }

    static func publicKey(fromHex hex: String) -> Curve25519.Signing.PublicKey? {
        guard let normalized = normalizedHex(hex), let raw = Data(hexString: normalized) else { return nil }
        return try? Curve25519.Signing.PublicKey(rawRepresentation: raw)
    }

    /// Lowercase hex SHA-256 of the raw public key, or nil when the key is not 32 bytes of hex.
    static func fingerprint(publicKeyHex hex: String) -> String? {
        guard let normalized = normalizedHex(hex), let raw = Data(hexString: normalized) else { return nil }
        return SHA256.hash(data: raw).map { String(format: "%02x", $0) }.joined()
    }

    /// Standard base64 **with padding** of exactly 64 bytes. A missing pad or a malformed string
    /// is refused rather than repaired.
    static func decodeSignature(_ text: String) -> Data? {
        let compact = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard compact.count % 4 == 0, !compact.isEmpty,
              let raw = Data(base64Encoded: compact), raw.count == 64 else { return nil }
        return raw
    }

    /// Verify `signatureBase64` over the canonical descriptor with one field removed, using the
    /// pinned controller key. False on every malformed input, never throws.
    static func verify(descriptor: [String: Any],
                       signatureBase64: String,
                       publicKeyHex: String,
                       removing field: String) -> Bool {
        guard let key = publicKey(fromHex: publicKeyHex),
              let signature = decodeSignature(signatureBase64),
              let message = try? ReleaseCanonicalJSON.signingBytes(descriptor, removing: field) else {
            return false
        }
        return key.isValidSignature(signature, for: message)
    }

    /// The controller countersignature over the stored descriptor.
    static func verifyControllerSignature(descriptor: [String: Any],
                                          signatureBase64: String,
                                          publicKeyHex: String) -> Bool {
        verify(descriptor: descriptor, signatureBase64: signatureBase64,
               publicKeyHex: publicKeyHex, removing: "controller_signature")
    }
}

private extension Data {
    init?(hexString: String) {
        guard hexString.count % 2 == 0 else { return nil }
        var bytes = [UInt8]()
        bytes.reserveCapacity(hexString.count / 2)
        var index = hexString.startIndex
        while index < hexString.endIndex {
            let next = hexString.index(index, offsetBy: 2)
            guard let byte = UInt8(hexString[index..<next], radix: 16) else { return nil }
            bytes.append(byte)
            index = next
        }
        self = Data(bytes)
    }
}

// MARK: - Descriptor

/// The one wire shape the controller serves. Validated on the way in so a malformed descriptor is
/// refused with a reason rather than trusted field by field.
struct ReleaseDescriptor {
    /// The descriptor exactly as received, which is what the countersignature covers.
    let raw: [String: Any]
    let version: String
    let build: String
    let filename: String
    let bytes: Int64
    let sha256: String
    let publishedAt: Int64
    let publisherId: String
    let publisherKeyId: String
    let signature: String
    let controllerKeyId: String
    let controllerSignature: String

    static let schema = "studiohub.app-release.v1"

    init?(json: [String: Any]) {
        func integer(_ value: Any?) -> Int64? {
            guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
            return number.int64Value
        }
        guard json["schema"] as? String == Self.schema,
              integer(json["schema_version"]) == 1,
              let version = json["version"] as? String, !version.isEmpty,
              let build = json["build"] as? String, !build.isEmpty,
              let filename = json["filename"] as? String, !filename.isEmpty,
              !filename.contains("/"), !filename.contains("\\"), !filename.contains("\0"),
              filename != ".", filename != "..",
              let bytes = integer(json["bytes"]), bytes > 0,
              let sha256 = (json["sha256"] as? String).flatMap(ReleaseSignature.normalizedHex),
              let publishedAt = integer(json["published_at"]), publishedAt >= 0,
              let publisherId = json["publisher_id"] as? String, !publisherId.isEmpty,
              let publisherKeyId = (json["publisher_key_id"] as? String).flatMap(ReleaseSignature.normalizedHex),
              let signature = json["signature"] as? String, !signature.isEmpty,
              let controllerKeyId = (json["controller_key_id"] as? String).flatMap(ReleaseSignature.normalizedHex),
              let controllerSignature = json["controller_signature"] as? String, !controllerSignature.isEmpty
        else { return nil }
        self.raw = json
        self.version = version
        self.build = build
        self.filename = filename
        self.bytes = bytes
        self.sha256 = sha256
        self.publishedAt = publishedAt
        self.publisherId = publisherId
        self.publisherKeyId = publisherKeyId
        self.signature = signature
        self.controllerKeyId = controllerKeyId
        self.controllerSignature = controllerSignature
    }

    var offered: OfferedRelease {
        OfferedRelease(version: version, build: build, filename: filename, bytes: bytes,
                       sha256: sha256, publisherId: publisherId, publisherKeyId: publisherKeyId,
                       controllerKeyId: controllerKeyId, publishedAt: publishedAt,
                       signature: signature)
    }
}

/// What the Connections banner shows about the release on offer.
struct OfferedRelease: Equatable {
    let version: String
    let build: String
    let filename: String
    let bytes: Int64
    let sha256: String
    let publisherId: String
    let publisherKeyId: String
    /// The controller key id the countersignature names — who actually signed it for this node.
    let controllerKeyId: String
    let publishedAt: Int64
    /// Standard base64 Ed25519 signature over the descriptor without `controller_signature`.
    let signature: String

    var shortSigner: String { String(controllerKeyId.prefix(12)) }

    var sizeDisplay: String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

// MARK: - Version ordering and the offer decision

/// A dotted numeric version such as `0.6.51`, compared component by component.
///
/// Both the advertised `version` and this app's own `CFBundleShortVersionString` are parsed this
/// way. Trailing zero components are dropped, so `0.6` and `0.6.0` compare equal. A string with a
/// non-numeric component (for example `0.6.51-beta`) does not parse; the caller then falls back to
/// the build-inequality rule instead of refusing an unfamiliar version string outright.
struct ReleaseVersion: Equatable, Comparable {
    let components: [Int]

    init?(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let parts = trimmed.split(separator: ".", omittingEmptySubsequences: false)
        guard !parts.isEmpty else { return nil }
        var values: [Int] = []
        values.reserveCapacity(parts.count)
        for part in parts {
            guard !part.isEmpty,
                  part.allSatisfy({ $0.isASCII && $0.isNumber }),
                  let value = Int(part) else { return nil }
            values.append(value)
        }
        // "0.6.0" and "0.6" are the same version, not two.
        while values.count > 1, values.last == 0 { values.removeLast() }
        self.components = values
    }

    static func < (lhs: ReleaseVersion, rhs: ReleaseVersion) -> Bool {
        let count = max(lhs.components.count, rhs.components.count)
        for index in 0..<count {
            let left = index < lhs.components.count ? lhs.components[index] : 0
            let right = index < rhs.components.count ? rhs.components[index] : 0
            if left != right { return left < right }
        }
        return false
    }
}

/// The pure result of comparing an advertised release with the app installed here.
enum ReleaseOfferDecision: Equatable {
    /// The advertised version is newer, or is the same version with a different build (a rebuild).
    case offered
    /// Same version and same build: there is nothing to install.
    case sameVersionSameBuild
    /// The advertised version is older than the installed one: a downgrade, never offered.
    case olderRelease
}

enum ReleaseOffer {
    /// Decide whether a signed release should be offered for installation.
    ///
    /// The rule is deliberate: the release is offered only when the advertised dotted numeric
    /// version is GREATER than the installed version, OR the versions are EQUAL and the build
    /// differs. A rebuild of the same version is the controller's normal push and must still be
    /// offered; an older version must never be, so a stale controller cannot roll this Mac
    /// backwards. When either version cannot be parsed this falls back to the original
    /// build-inequality rule (offer iff the builds differ) rather than silently refusing a release
    /// whose version string is unfamiliar.
    static func decide(advertisedVersion: String,
                       advertisedBuild: String,
                       installedVersion: String?,
                       installedBuild: String) -> ReleaseOfferDecision {
        if let advertised = ReleaseVersion(advertisedVersion),
           let installedVersion, let installed = ReleaseVersion(installedVersion) {
            if advertised > installed { return .offered }
            if advertised == installed {
                return advertisedBuild == installedBuild ? .sameVersionSameBuild : .offered
            }
            return .olderRelease
        }
        // Unparseable version: fall back to the build-inequality rule.
        return advertisedBuild == installedBuild ? .sameVersionSameBuild : .offered
    }
}

// MARK: - Kept artifact location

/// Where a release artifact lives while it is downloaded and installed.
///
/// The directory is keyed by the release identity — schema, version, build and digest — rather
/// than a fresh random temporary name, so the fragment a failed attempt left behind is found again
/// on the next press. It sits under the app's private data directory (created 0700, artifact 0600)
/// instead of a world-readable temporary folder.
enum ReleaseArtifactStore {
    /// Lowercase hex SHA-256 of the identity that names one artifact on disk.
    static func identityKey(version: String, build: String, sha256: String) -> String {
        let identity = "\(ReleaseDescriptor.schema)|\(version)|\(build)|\(sha256)"
        return SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    /// The stable directory holding `descriptor`'s artifact, under the app's data directory.
    static func directory(base: URL, descriptor: ReleaseDescriptor) -> URL {
        base.appendingPathComponent("Updates", isDirectory: true)
            .appendingPathComponent(
                identityKey(version: descriptor.version, build: descriptor.build,
                            sha256: descriptor.sha256),
                isDirectory: true)
    }

    /// The stable file a download writes to and a retry resumes from.
    static func artifactURL(base: URL, descriptor: ReleaseDescriptor) -> URL {
        directory(base: base, descriptor: descriptor)
            .appendingPathComponent(descriptor.filename)
    }
}

// MARK: - Artifact form

/// Which artifact shape is published. A disk image is implemented; anything else is refused with
/// a reason rather than guessed at.
enum ReleaseArtifactForm: Equatable {
    case diskImage
    case unsupported(String)

    static func classify(filename: String) -> ReleaseArtifactForm {
        let lower = filename.lowercased()
        if lower.hasSuffix(".dmg") { return .diskImage }
        if lower.hasSuffix(".app") {
            return .unsupported("The controller published an app bundle (‘\(filename)’). This Mac installs a disk image (.dmg) release; it will not guess at an unpacked bundle, so no update was installed.")
        }
        return .unsupported("The controller published ‘\(filename)’, which is not a disk image (.dmg). This Mac installs .dmg releases only, so no update was installed.")
    }
}

// MARK: - Installer

/// The part of an update that touches the filesystem. Kept apart from the store so the swap order
/// can be read — and exercised against a scratch target — without writing to /Applications.
///
/// The discipline mirrors `packaging/install_local.sh`: stage beside the target, require the
/// bundled python3, move the old app aside, move the new one in, verify the build that actually
/// landed, and only then remove the old copy. Any failure after the swap puts the old app back.
struct ReleaseInstaller {
    var targetApp: URL
    var fileManager: FileManager
    var attach: (URL, URL) throws -> Void
    var detach: (URL) throws -> Void
    /// Free bytes on the volume holding a folder, or nil when it cannot be read. Injectable so the
    /// free-space guard can be exercised without filling a disk.
    var availableCapacity: (URL) -> Int64?

    init(targetApp: URL = URL(fileURLWithPath: "/Applications/Studio Hub.app"),
         fileManager: FileManager = .default,
         attach: @escaping (URL, URL) throws -> Void = ReleaseInstaller.attachImage,
         detach: @escaping (URL) throws -> Void = ReleaseInstaller.detachImage,
         availableCapacity: @escaping (URL) -> Int64? = ReleaseInstaller.volumeAvailableCapacity) {
        self.targetApp = targetApp
        self.fileManager = fileManager
        self.attach = attach
        self.detach = detach
        self.availableCapacity = availableCapacity
    }

    /// Room to keep the old app, stage the new one beside it, and leave the volume usable.
    static let freeSpaceMargin: Int64 = 1 << 30

    static func volumeAvailableCapacity(_ folder: URL) -> Int64? {
        (try? folder.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage
    }

    /// Total bytes of the files under `root` (the size the staged copy will occupy).
    static func treeBytes(_ root: URL, fileManager: FileManager) -> Int64 {
        guard let walker = fileManager.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        var total: Int64 = 0
        for case let file as URL in walker {
            total += Int64((try? file.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0)
        }
        return total
    }

    static let live = ReleaseInstaller()

    /// The release manifest the staged bundle carries, whose `components` map is exactly what the
    /// Hub inside will spawn. `packaging/stage_voice.py` writes it and `packaging/hub_worker.py`
    /// reads it, so it is the release's own statement of what must be present.
    static let releaseManifest = "Contents/Resources/release-manifest.json"

    /// The bundle paths each runtime a manifest can name must carry.
    ///
    /// The manifest's `runtime` value is data; this table is the layout contract the Hub and
    /// `packaging/install_local.sh` already share. A runtime name that is not here is refused by
    /// name, with the file to update, rather than passing a release nothing can check.
    static let runtimePaths: [String: [String]] = [
        "voice": ["Contents/Resources/runtimes/voice/bin/python3"],
        "image": ["Contents/Resources/runtimes/image/bin/python3"],
        // The Music overlay rides on the base runtime and adds its own engine manifest, which the
        // Hub's own `_music_spec` requires before it will start the worker.
        "voice+music-overlay": ["Contents/Resources/runtimes/voice/bin/python3",
                                "Contents/Resources/runtimes/music/manifest/music-runtime.json"],
    ]

    static func attachImage(_ image: URL, _ mountPoint: URL) throws {
        _ = try runProcess("/usr/bin/hdiutil",
                           ["attach", "-readonly", "-nobrowse", "-noverify",
                            "-mountpoint", mountPoint.path, image.path])
    }

    static func detachImage(_ mountPoint: URL) throws {
        _ = try runProcess("/usr/bin/hdiutil", ["detach", mountPoint.path])
    }

    @discardableResult
    static func runProcess(_ launchPath: String, _ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launchPath)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do { try process.run() }
        catch { throw ReleaseError("Could not run \(launchPath): \(error.localizedDescription)") }
        let output = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let text = String(decoding: output, as: UTF8.self)
        guard process.terminationStatus == 0 else {
            throw ReleaseError("\(launchPath) failed (\(process.terminationStatus)): \(text.prefix(300))")
        }
        return text
    }

    /// Mount the published disk image read-only, copy the one app out, verify it and swap it in.
    /// Returns the StudioHubBuild that actually landed.
    func install(artifact: URL, descriptor: ReleaseDescriptor) throws -> String {
        if case .unsupported(let reason) = ReleaseArtifactForm.classify(filename: descriptor.filename) {
            throw ReleaseError(reason)
        }
        let mountPoint = fileManager.temporaryDirectory
            .appendingPathComponent("studiohub-release-mount-\(UUID().uuidString)", isDirectory: true)
        try fileManager.createDirectory(at: mountPoint, withIntermediateDirectories: true)
        defer {
            try? detach(mountPoint)
            try? fileManager.removeItem(at: mountPoint)
        }
        try attach(artifact, mountPoint)
        let contents = (try? fileManager.contentsOfDirectory(
            at: mountPoint, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])) ?? []
        let apps = contents.filter { $0.pathExtension.lowercased() == "app" }
        guard apps.count == 1, let source = apps.first else {
            throw ReleaseError("The disk image holds \(apps.count) app bundles; exactly one is expected, so nothing was installed.")
        }
        // The old app stays until the new one has landed, so both are on the volume at once. A full
        // disk half-way through a 5 GB copy is the failure this refuses up front, by name.
        let needed = Self.treeBytes(source, fileManager: fileManager)
        if let free = availableCapacity(targetApp.deletingLastPathComponent()), free < needed + Self.freeSpaceMargin {
            let size = ByteCountFormatter.string(fromByteCount: needed, countStyle: .file)
            let have = ByteCountFormatter.string(fromByteCount: free, countStyle: .file)
            throw ReleaseError("There is not enough free space to install this update: the new app is \(size), the current one stays until it has landed, and only \(have) is free. Free some space and try again. Nothing was changed.")
        }
        // Stage beside the target so the move into place is a same-volume rename, not a copy.
        let stagingDirectory = targetApp.deletingLastPathComponent()
            .appendingPathComponent(".studiohub-staged-\(UUID().uuidString)", isDirectory: true)
        try fileManager.createDirectory(at: stagingDirectory, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: stagingDirectory) }
        let staged = stagingDirectory.appendingPathComponent(targetApp.lastPathComponent)
        do {
            try fileManager.copyItem(at: source, to: staged)
        } catch {
            throw ReleaseError("Could not stage the downloaded app: \(error.localizedDescription)")
        }
        try verifyAppComplete(staged, expectedBuild: descriptor.build)
        return try swapStagedApp(staged: staged, descriptor: descriptor)
    }

    /// Move a fully staged app into place, verifying what landed before the old copy is removed.
    @discardableResult
    func swapStagedApp(staged: URL, descriptor: ReleaseDescriptor) throws -> String {
        try verifyAppComplete(staged, expectedBuild: descriptor.build)
        let old = targetApp.deletingLastPathComponent()
            .appendingPathComponent(".studiohub-old-\(UUID().uuidString)")
        let hadOld = fileManager.fileExists(atPath: targetApp.path)
        if hadOld {
            do { try fileManager.moveItem(at: targetApp, to: old) }
            catch {
                throw ReleaseError("Could not move the installed app aside: \(error.localizedDescription). Nothing was replaced.")
            }
        }
        do {
            try fileManager.moveItem(at: staged, to: targetApp)
        } catch {
            // The new one never landed, so the old one must go straight back.
            if hadOld { try? fileManager.moveItem(at: old, to: targetApp) }
            throw ReleaseError("Could not put the new app in place; the previous app was restored. \(error.localizedDescription)")
        }
        do {
            let landed = try verifyAppComplete(targetApp, expectedBuild: descriptor.build)
            // Only now is the old copy disposable.
            if hadOld { try? fileManager.removeItem(at: old) }
            return landed
        } catch {
            try? fileManager.removeItem(at: targetApp)
            if hadOld { try? fileManager.moveItem(at: old, to: targetApp) }
            throw ReleaseError("The new app did not verify after the swap, so the previous app was restored. \(error.localizedDescription)")
        }
    }

    /// The app is complete only if every component its own release manifest declares is present,
    /// and the Info.plist names a build; returns that build.
    ///
    /// This used to check exactly one hardcoded path — the Voice runtime's python3. A release
    /// missing the image, music, chat or subtitle runtime therefore installed and looked healthy
    /// until that worker failed, and moving the voice layout refused every release instead. What is
    /// required now comes from the manifest the release itself carries.
    @discardableResult
    func verifyAppComplete(_ app: URL, expectedBuild: String?) throws -> String {
        try verifyDeclaredComponents(app)
        let plist = app.appendingPathComponent("Contents/Info.plist")
        guard let data = fileManager.contents(atPath: plist.path),
              let object = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any],
              let build = object["StudioHubBuild"] as? String, !build.isEmpty else {
            throw ReleaseError("The app bundle has no readable StudioHubBuild in its Info.plist.")
        }
        if let expectedBuild, build != expectedBuild {
            throw ReleaseError("The app bundle declares build \(build), but the release says \(expectedBuild).")
        }
        return build
    }

    /// Every component the release manifest declares must be present and runnable here.
    ///
    /// The manifest's `components` map is the release's own list of what it ships — the same map
    /// the Hub inside spawns its workers from — so a component added there is checked here without
    /// an edit, and a component whose runtime this installer does not know is refused by name.
    func verifyDeclaredComponents(_ app: URL) throws {
        let manifest = app.appendingPathComponent(Self.releaseManifest)
        guard let data = fileManager.contents(atPath: manifest.path),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let components = object["components"] as? [String: Any], !components.isEmpty else {
            throw ReleaseError("The app bundle is incomplete: its Contents/Resources/release-manifest.json is missing or lists no components, so the runtimes it ships cannot be checked.")
        }
        for (name, value) in components.sorted(by: { $0.key < $1.key }) {
            let record = value as? [String: Any]
            // A component that is the base runtime itself (`voice`) publishes no `runtime` name.
            let runtime = (record?["runtime"] as? String) ?? name
            guard let runtimeRequired = Self.runtimePaths[runtime] else {
                throw ReleaseError("The release manifest lists component '\(name)' with runtime '\(runtime)', which this installer does not know. Add '\(runtime)' to `runtimePaths` in native/UpdateClient.swift — and to COMPONENT_WORKERS in packaging/hub_worker.py — before shipping a release that uses it.")
            }
            // Every component also ships its own worker entry point beside the shared runtime,
            // named for the component. The path is derived from the component's own key rather
            // than listed here, so a component added to the manifest is checked without an edit.
            var required = runtimeRequired
            required.append("Contents/Resources/\(name)_worker.py")
            for relative in required {
                let path = app.appendingPathComponent(relative).path
                let mustExecute = relative.hasSuffix("bin/python3")
                let present = mustExecute ? fileManager.isExecutableFile(atPath: path)
                                          : fileManager.fileExists(atPath: path)
                guard present else {
                    throw ReleaseError("The app bundle is incomplete: the \(name) component's \(relative) is missing\(mustExecute ? " or not executable" : ""). Its own release manifest declares that component.")
                }
            }
        }
    }
}
