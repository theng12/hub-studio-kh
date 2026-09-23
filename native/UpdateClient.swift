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

    init(targetApp: URL = URL(fileURLWithPath: "/Applications/Studio Hub.app"),
         fileManager: FileManager = .default,
         attach: @escaping (URL, URL) throws -> Void = ReleaseInstaller.attachImage,
         detach: @escaping (URL) throws -> Void = ReleaseInstaller.detachImage) {
        self.targetApp = targetApp
        self.fileManager = fileManager
        self.attach = attach
        self.detach = detach
    }

    static let live = ReleaseInstaller()

    /// The bundled runtime that `install_local.sh` checks. Its absence used to mean a copy had
    /// arrived incomplete while the app looked installed.
    static let requiredRuntime = "Contents/Resources/runtimes/voice/bin/python3"

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

    /// The app is complete only if the bundled python3 is present and executable and the
    /// Info.plist names a build; returns that build.
    @discardableResult
    func verifyAppComplete(_ app: URL, expectedBuild: String?) throws -> String {
        let python = app.appendingPathComponent(Self.requiredRuntime)
        guard fileManager.isExecutableFile(atPath: python.path) else {
            throw ReleaseError("The app bundle is incomplete: its bundled python3 is missing or not executable.")
        }
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
}
