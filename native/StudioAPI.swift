import AppKit
import Combine
import AVFoundation
import Foundation
import CryptoKit

/// One image model's generation controls, exactly the ones it publishes.
///
/// A modality can be served by more than one engine, and a second engine does not have to
/// publish this one's exact control set. Every control except the size is therefore optional:
/// one the model never names is left out of the request, and the worker fills its own default
/// (`validate_request` in `components/image/app/backend/generation.py` reads
/// `raw.get('steps', model.defaults['steps'])`). Requiring the shipped engine's five keys is
/// what silently filtered a differently-shaped second engine out of the picker.
struct NativeImageSettings: Codable, Equatable {
    var width: Int
    var height: Int
    var steps: Int?
    var guidance: Double?
    var seed: Int64?
}
struct NativeImageSize: Decodable, Identifiable, Equatable {
    let id: String
    let label: String
    let width: Int
    let height: Int
}
/// The bounds a model publishes for its own image controls. A bound it does not publish is not
/// enforced here: the worker checks the request against its own manifest and answers with its
/// own reason, which is the only place that knows the engine's real limits.
struct NativeImageLimits: Decodable, Equatable {
    let minSteps: Int?
    let maxSteps: Int?
    let minGuidance: Double?
    let maxGuidance: Double?
    let minSeed: Int64?
    let maxSeed: Int64?

    init(minSteps: Int? = nil, maxSteps: Int? = nil, minGuidance: Double? = nil,
         maxGuidance: Double? = nil, minSeed: Int64? = nil, maxSeed: Int64? = nil) {
        self.minSteps = minSteps
        self.maxSteps = maxSteps
        self.minGuidance = minGuidance
        self.maxGuidance = maxGuidance
        self.minSeed = minSeed
        self.maxSeed = maxSeed
    }

    func accepts(_ value: NativeImageSettings) -> Bool {
        if let steps = value.steps {
            if let minSteps, steps < minSteps { return false }
            if let maxSteps, steps > maxSteps { return false }
        }
        if let guidance = value.guidance {
            guard guidance.isFinite else { return false }
            if let minGuidance, guidance < minGuidance { return false }
            if let maxGuidance, guidance > maxGuidance { return false }
        }
        if let seed = value.seed {
            if let minSeed, seed < minSeed { return false }
            if let maxSeed, seed > maxSeed { return false }
        }
        return true
    }

    /// A value the model's own bounds accept, so a default published outside its own limits is
    /// brought to the nearest bound instead of the whole row being hidden.
    func clamped(_ value: NativeImageSettings) -> NativeImageSettings {
        var result = value
        if let steps = result.steps {
            if let minSteps = minSteps, steps < minSteps { result.steps = minSteps }
            if let maxSteps = maxSteps, steps > maxSteps { result.steps = maxSteps }
        }
        if let guidance = result.guidance {
            if let minGuidance = minGuidance, guidance < minGuidance { result.guidance = minGuidance }
            if let maxGuidance = maxGuidance, guidance > maxGuidance { result.guidance = maxGuidance }
        }
        if let seed = result.seed {
            if let minSeed = minSeed, seed < minSeed { result.seed = minSeed }
            if let maxSeed = maxSeed, seed > maxSeed { result.seed = maxSeed }
        }
        return result
    }
}
struct NativeImageConfiguration: Equatable {
    let defaults: NativeImageSettings
    let sizes: [NativeImageSize]
    let limits: NativeImageLimits
    func accepts(_ value: NativeImageSettings) -> Bool {
        sizes.contains { $0.width == value.width && $0.height == value.height }
            && limits.accepts(value)
    }
    /// What to show for a control the operator has not chosen: the model's own published default,
    /// or the bound it publishes. Nil means the model publishes neither, so the control is not
    /// shown and the request omits it — never an invented value.
    var stepsField: Int? { defaults.steps ?? limits.minSteps }
    var guidanceField: Double? { defaults.guidance ?? limits.minGuidance }
    var seedField: Int64? { defaults.seed ?? limits.minSeed }
    var stepsRange: ClosedRange<Int>? {
        guard let value = stepsField else { return nil }
        let low = limits.minSteps ?? value
        let high = limits.maxSteps ?? value
        return low...max(low, high)
    }
    var guidanceRange: ClosedRange<Double>? {
        guard let value = guidanceField else { return nil }
        let low = limits.minGuidance ?? value
        let high = limits.maxGuidance ?? value
        return low...max(low, high)
    }
}

/// One loading mode as the Image model's own catalogue entry publishes it.
///
/// `detail` is the model's own sentence for the option: it carries the peak memory and wall time
/// the model states for this configuration and the host it stated them on. It is shown verbatim.
/// A trade worded here in Swift would be a claim the model never made.
struct NativeImageLoadingOption: Decodable, Identifiable, Equatable {
    let id: String
    let label: String
    let detail: String?
}

/// The Image worker's loading-size setting as one model's catalogue row publishes it.
///
/// This is deliberately not the release policy: that is the Image worker's `/api/memory-policy`,
/// whose answer is fixed because mflux is built inside a job. This is which mflux memory
/// *configuration* the next job loads with on this Mac.
///
/// `supported: false` means the model has exactly one loading mode: `options` is empty and
/// `reason` is the model's own sentence explaining why, including — when the machine-wide
/// setting is `low` — that the setting does not apply to it. No control may be drawn for it.
struct NativeImageLoadingModes: Decodable, Equatable {
    let supported: Bool
    let selected: String?
    let effective: String?
    let thresholdBytes: Int64?
    let options: [NativeImageLoadingOption]?
    /// The model's own sentence for having a single loading mode, or nil when it has several.
    let reason: String?

    var publishedOptions: [NativeImageLoadingOption] { options ?? [] }
}

/// One model's half of `GET /api/loading-mode`, the Image worker's per-machine answer.
struct NativeImageLoadingModeRow: Decodable, Identifiable, Equatable {
    let repo: String
    let supported: Bool
    let selected: String?
    let effective: String?
    let thresholdBytes: Int64?
    let reason: String?

    var id: String { repo }
}

/// `GET`/`PUT /studio/image/api/loading-mode`, the Image worker's own payload.
///
/// `models` is the same derivation the catalogue publishes, projected per repo, so the two
/// surfaces cannot disagree about what a model will do.
struct NativeImageLoadingModeStatus: Decodable, Equatable {
    let mode: String
    let defaultMode: String?
    let options: [NativeImageLoadingOption]?
    let machineBytes: Int64?
    let models: [NativeImageLoadingModeRow]?

    var publishedOptions: [NativeImageLoadingOption] { options ?? [] }
    var publishedModels: [NativeImageLoadingModeRow] { models ?? [] }
}

/// What the Image page renders for the selected model's loading mode.
///
/// Built only from what the producer published: the model's own catalogue row decides whether a
/// control exists at all, and the Image worker's status read supplies the selection and the
/// configuration actually in force after a change. `reason` is set exactly when there is no
/// control to draw.
struct NativeImageLoadingControl: Equatable {
    let repo: String
    /// The options to render, in the producer's order. Empty means no control may be drawn.
    let options: [NativeImageLoadingOption]
    let selected: String
    /// What this Mac will actually load the model as, as the producer states it ("low-ram" on a
    /// machine under the model's own threshold, "default" at or above it).
    let effective: String?
    /// The selected option's own published sentence, when the producer published one.
    let detail: String?
    /// The sentence to render instead of a control: the model's own `reason` for having a single
    /// loading mode, or the fact that its catalogue entry publishes no loading modes at all.
    let reason: String?

    var offersControl: Bool { !options.isEmpty }
    /// "In force on this Mac: low-ram" — the operator can see `auto` resolved to the bounded
    /// configuration here.
    var effectiveSentence: String? {
        guard let effective, !effective.isEmpty else { return nil }
        return "In force on this Mac: \(effective)"
    }
}

/// The controls a Music model actually accepts, as the Music worker publishes them.
///
/// The Hub catalogue carries `generation_defaults` and `generation_limits` per model, and the
/// worker's own `validate_request` (components/music/app/backend/generation.py) is the contract
/// behind them: it validates the request with *that model's* validator and fills anything the
/// request omits from the model's own entry. Only fields the Hub publishes are modelled, and
/// each control is optional, so a second engine of the same modality that publishes a different
/// control set is offered rather than silently dropped.
struct NativeMusicSettings: Codable, Equatable {
    var durationSeconds: Double
    var steps: Int?
    var guidanceScale: Double?
    var shift: Double?
    var seed: Int64?
    var instrumental: Bool?
    var lyrics: String?
    var vocalLanguage: String?
}
/// The bounds a model publishes for its own Music controls; a bound it does not publish is left
/// to the worker's own validator rather than invented here.
struct NativeMusicLimits: Equatable {
    let minDurationSeconds: Double?
    let maxDurationSeconds: Double?
    let minSteps: Int?
    let maxSteps: Int?
    let minGuidance: Double?
    let maxGuidance: Double?
    let minShift: Double?
    let maxShift: Double?
    let minSeed: Int64?
    let maxSeed: Int64?

    init(minDurationSeconds: Double? = nil, maxDurationSeconds: Double? = nil,
         minSteps: Int? = nil, maxSteps: Int? = nil,
         minGuidance: Double? = nil, maxGuidance: Double? = nil,
         minShift: Double? = nil, maxShift: Double? = nil,
         minSeed: Int64? = nil, maxSeed: Int64? = nil) {
        self.minDurationSeconds = minDurationSeconds
        self.maxDurationSeconds = maxDurationSeconds
        self.minSteps = minSteps
        self.maxSteps = maxSteps
        self.minGuidance = minGuidance
        self.maxGuidance = maxGuidance
        self.minShift = minShift
        self.maxShift = maxShift
        self.minSeed = minSeed
        self.maxSeed = maxSeed
    }

    func accepts(_ value: NativeMusicSettings) -> Bool {
        guard value.durationSeconds.isFinite else { return false }
        if let minDurationSeconds, value.durationSeconds < minDurationSeconds { return false }
        if let maxDurationSeconds, value.durationSeconds > maxDurationSeconds { return false }
        if let steps = value.steps {
            if let minSteps, steps < minSteps { return false }
            if let maxSteps, steps > maxSteps { return false }
        }
        if let guidance = value.guidanceScale {
            guard guidance.isFinite else { return false }
            if let minGuidance, guidance < minGuidance { return false }
            if let maxGuidance, guidance > maxGuidance { return false }
        }
        if let shift = value.shift {
            guard shift.isFinite else { return false }
            if let minShift, shift < minShift { return false }
            if let maxShift, shift > maxShift { return false }
        }
        if let seed = value.seed {
            if let minSeed, seed < minSeed { return false }
            if let maxSeed, seed > maxSeed { return false }
        }
        if let lyrics = value.lyrics, lyrics.count > 8000 { return false }
        if let language = value.vocalLanguage, language.count > 32 { return false }
        return true
    }

    /// Bring a value the model itself published inside the bounds it published, rather than
    /// hiding the model because its own two rows disagree.
    func clamped(_ value: NativeMusicSettings) -> NativeMusicSettings {
        var result = value
        if let minDurationSeconds, result.durationSeconds < minDurationSeconds {
            result.durationSeconds = minDurationSeconds
        }
        if let maxDurationSeconds, result.durationSeconds > maxDurationSeconds {
            result.durationSeconds = maxDurationSeconds
        }
        if let steps = result.steps {
            if let minSteps, steps < minSteps { result.steps = minSteps }
            if let maxSteps, steps > maxSteps { result.steps = maxSteps }
        }
        if let guidance = result.guidanceScale {
            if let minGuidance, guidance < minGuidance { result.guidanceScale = minGuidance }
            if let maxGuidance, guidance > maxGuidance { result.guidanceScale = maxGuidance }
        }
        if let shift = result.shift {
            if let minShift, shift < minShift { result.shift = minShift }
            if let maxShift, shift > maxShift { result.shift = maxShift }
        }
        if let seed = result.seed {
            if let minSeed, seed < minSeed { result.seed = minSeed }
            if let maxSeed, seed > maxSeed { result.seed = maxSeed }
        }
        return result
    }
}
struct NativeMusicConfiguration: Equatable {
    let defaults: NativeMusicSettings
    let limits: NativeMusicLimits
    func accepts(_ value: NativeMusicSettings) -> Bool { limits.accepts(value) }
    /// What to show for a control the operator has not chosen, or nil when the model publishes
    /// neither a default nor a bound for it — then the control is not shown and the request
    /// omits it, so the worker uses its own default.
    var stepsField: Int? { defaults.steps ?? limits.minSteps }
    var guidanceField: Double? { defaults.guidanceScale ?? limits.minGuidance }
    var shiftField: Double? { defaults.shift ?? limits.minShift }
    var seedField: Int64? { defaults.seed ?? limits.minSeed }
    var durationRange: ClosedRange<Double>? {
        let low = limits.minDurationSeconds ?? defaults.durationSeconds
        let high = limits.maxDurationSeconds ?? defaults.durationSeconds
        guard low.isFinite, high.isFinite else { return nil }
        return low...max(low, high)
    }
    var stepsRange: ClosedRange<Int>? {
        guard let value = stepsField else { return nil }
        let low = limits.minSteps ?? value
        let high = limits.maxSteps ?? value
        return low...max(low, high)
    }
}

/// One JSON value a lane publishes: a bound's default, a stored default, or a value in force.
///
/// The defaults payload carries a heterogeneous map — `{"steps": 9, "aspect_ratio": "16:9",
/// "instrumental": true}` — so it is decoded as the JSON it is rather than as one concrete type
/// that would drop every key of another shape. Nothing is coerced on the way in or out: an integer
/// stays an integer, so the value the operator typed is the value that goes back to the lane.
enum NativeJSONValue: Codable, Equatable {
    case string(String)
    case integer(Int)
    case number(Double)
    case bool(Bool)
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null; return }
        // Boolean before number on purpose: this decoder is strict about the two, and a JSON
        // `true` must not become 1 in a field the lane validates as a boolean.
        if let value = try? container.decode(Bool.self) { self = .bool(value); return }
        if let value = try? container.decode(Int.self) { self = .integer(value); return }
        if let value = try? container.decode(Double.self) { self = .number(value); return }
        if let value = try? container.decode(String.self) { self = .string(value); return }
        throw DecodingError.dataCorruptedError(
            in: container, debugDescription: "a value this app has no rule for")
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .integer(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    var intValue: Int? {
        switch self {
        case .integer(let value): return value
        case .number(let value) where value == value.rounded() && value.magnitude < 9e18:
            return Int(value)
        default: return nil
        }
    }
    var doubleValue: Double? {
        switch self {
        case .integer(let value): return Double(value)
        case .number(let value): return value
        default: return nil
        }
    }
    var boolValue: Bool? { if case .bool(let value) = self { return value }; return nil }
    var stringValue: String? { if case .string(let value) = self { return value }; return nil }

    /// The value as a field shows it. A double that is really an integer prints without a decimal
    /// tail, because `9.0` in a steps field reads as a number the lane never published.
    var fieldText: String {
        switch self {
        case .string(let value): return value
        case .integer(let value): return String(value)
        case .number(let value): return Self.trimmed(value)
        case .bool(let value): return value ? "on" : "off"
        case .null: return ""
        }
    }

    /// A number as a person writes it: no trailing `.0`, no exponent for the sizes here.
    static func trimmed(_ value: Double) -> String {
        guard value.isFinite else { return "" }
        if value == value.rounded() && value.magnitude < 1e15 { return String(Int(value)) }
        var text = String(format: "%g", value)
        if text.contains("e") { text = String(value) }
        return text
    }
}

/// One suggested value a lane publishes under a parameter — Music's duration ladder.
///
/// A rung is a *suggestion*, never the set of accepted values: the parameter stays the bounded
/// number it always was, and the operator may store any value inside the lane's own range.
/// `exceedsSinglePass` is a fact about the job the rung produces — a length the engine renders in
/// sections — so it is shown rather than hidden.
struct NativeLadderRung: Decodable, Equatable, Identifiable {
    let seconds: Double
    let label: String
    let exceedsSinglePass: Bool?
    var id: Double { seconds }
}

/// One parameter a lane publishes in its `lane_contract`, exactly as it published it.
///
/// This is the whole of what the editor and the facts block know about a control: the name, the
/// type, and the bound the lane says it enforces. Nothing here is a copy of a bound — a parameter
/// added, withdrawn or re-bounded by a Studio changes this screen with no edit in the app.
struct NativeLaneParameter: Decodable, Equatable, Identifiable {
    let name: String
    let type: String
    /// A required parameter is per-request content: it is never offered as a default.
    let required: Bool?
    let minimum: Double?
    let maximum: Double?
    /// `top_p` publishes `minimum_exclusive`: the lane refuses the bound itself, so the editor
    /// must not offer it as an accepted value.
    let minimumExclusive: Bool?
    let maximumExclusive: Bool?
    let maxCharacters: Int?
    let maxBytes: Int?
    /// The lane's own options, in the lane's own order, as text for a picker.
    ///
    /// **`enum` is not always strings, and decoding it as if it were took the whole catalogue
    /// down.** Measured 2026-09-27 against the running Hub 0.6.83: the video lane publishes
    /// `"enum": [2, 4]` for `duration_seconds` and `[24]` for `frame_rate`, so `[String]` failed,
    /// the `/api/hub/catalog` response failed to decode, and every model list in the app went
    /// empty — voice, image and chat included, for a number in one video parameter. `optionValues`
    /// keeps the lane's own values with their types; this is the same list rendered for display,
    /// because a duration of 2 seconds is the integer 2 and reads as "2".
    let options: [String]?
    /// The lane's options exactly as published, with their own JSON types.
    let optionValues: [NativeJSONValue]?
    let defaultValue: NativeJSONValue?
    /// The lane's own sentence about the parameter — including that it is accepted and ignored.
    let note: String?
    /// The lane's own machine-readable answer to "do you read this parameter at all?", where it
    /// publishes one. It outranks the prose note below: a field cannot be reworded by accident.
    let ignored: Bool?
    /// Suggested values the lane publishes for this parameter, when it publishes any.
    let ladder: [NativeLadderRung]?
    /// Where the lane says the bound came from, when it says (Voice publishes `bound_source`).
    let boundSource: String?

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, type, required, minimum, maximum, note, ignored, ladder
        case minimumExclusive, maximumExclusive, maxCharacters, maxBytes, boundSource
        case optionValues = "enum"
        case defaultValue = "default"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        type = try container.decode(String.self, forKey: .type)
        required = try container.decodeIfPresent(Bool.self, forKey: .required)
        minimum = try container.decodeIfPresent(Double.self, forKey: .minimum)
        maximum = try container.decodeIfPresent(Double.self, forKey: .maximum)
        minimumExclusive = try container.decodeIfPresent(Bool.self, forKey: .minimumExclusive)
        maximumExclusive = try container.decodeIfPresent(Bool.self, forKey: .maximumExclusive)
        maxCharacters = try container.decodeIfPresent(Int.self, forKey: .maxCharacters)
        maxBytes = try container.decodeIfPresent(Int.self, forKey: .maxBytes)
        note = try container.decodeIfPresent(String.self, forKey: .note)
        ignored = try container.decodeIfPresent(Bool.self, forKey: .ignored)
        ladder = try container.decodeIfPresent([NativeLadderRung].self, forKey: .ladder)
        boundSource = try container.decodeIfPresent(String.self, forKey: .boundSource)
        defaultValue = try container.decodeIfPresent(NativeJSONValue.self, forKey: .defaultValue)
        // The lane's own values, whatever types it used. An entry this app has no rule for is
        // dropped with the rest of the list kept: one unreadable option must not empty a picker,
        // and it must not fail the catalogue either.
        let values = (try? container.decodeIfPresent([NativeJSONValue].self, forKey: .optionValues)) ?? nil
        optionValues = values
        options = values?.compactMap(Self.optionText)
    }

    /// Build one parameter from values already in hand.
    ///
    /// The decoder above is what reads the wire; this exists for callers that construct a
    /// parameter directly (tests, and any future editor), and it keeps `optionValues` derived from
    /// `options` so the two can never disagree. Declaring `init(from:)` suppresses the implicit
    /// memberwise initializer, which is why it is written out here.
    init(name: String, type: String, required: Bool?, minimum: Double?, maximum: Double?,
         minimumExclusive: Bool?, maximumExclusive: Bool?, maxCharacters: Int?, maxBytes: Int?,
         options: [String]?, defaultValue: NativeJSONValue?, note: String?, ignored: Bool?,
         ladder: [NativeLadderRung]?, boundSource: String?) {
        self.name = name
        self.type = type
        self.required = required
        self.minimum = minimum
        self.maximum = maximum
        self.minimumExclusive = minimumExclusive
        self.maximumExclusive = maximumExclusive
        self.maxCharacters = maxCharacters
        self.maxBytes = maxBytes
        self.options = options
        self.optionValues = options?.map { .string($0) }
        self.defaultValue = defaultValue
        self.note = note
        self.ignored = ignored
        self.ladder = ladder
        self.boundSource = boundSource
    }

    /// One published option as text. Integers and numbers are written plainly — the lane said
    /// `2` and a picker shows `2` — and every other kind is left out rather than described.
    private static func optionText(_ value: NativeJSONValue) -> String? {
        switch value {
        case .string(let text): return text
        case .integer(let number): return String(number)
        case .number(let number):
            return number == number.rounded() ? String(Int(number)) : String(number)
        case .bool(let flag): return flag ? "true" : "false"
        case .null: return nil
        }
    }

    /// Whether the lane itself says it accepts this parameter and discards it.
    ///
    /// The Image worker is the live case: Z-Image-Turbo accepts `guidance` and forces it to zero.
    /// Where the lane publishes the machine-readable `ignored` field that is the answer; where it
    /// has not landed yet, its own sentence — "accepted and ignored: this model runs without
    /// classifier-free guidance" — is read instead. A control that looked editable there would be
    /// a value the lane never reads, so it is shown as ignored and never sent.
    var isDeclaredIgnored: Bool {
        if let ignored { return ignored }
        return (note ?? "").lowercased().hasPrefix("accepted and ignored")
    }

    /// The lane's own bounds as a control range, or nil when it publishes no numeric bound. The
    /// range is inclusive because the lane's clamp is: Voice bounds `speed` with
    /// `max(SPEED_MINIMUM, min(speed, SPEED_MAXIMUM))`, so the endpoints are values it accepts. A
    /// lane that publishes `minimum_exclusive` says otherwise about its own bound, and an editor
    /// that offers one value at a time reads that flag itself.
    var numericRange: ClosedRange<Double>? {
        guard let minimum, let maximum, minimum.isFinite, maximum.isFinite, minimum <= maximum
        else { return nil }
        return minimum...maximum
    }
}

/// One limit a worker publishes with the artefact that states it.
///
/// `value` is nil when nothing states the limit, and `source` is then the reason — never a curated
/// substitute. The app renders the value only when it has one, and always with its source.
struct NativeModelLimit: Decodable, Equatable {
    let value: NativeJSONValue?
    let source: String?
}

/// The artifact a lane's successful item yields.
struct NativeLaneArtifact: Decodable, Equatable {
    let modality: String?
    let mediaType: String?
    let fileExtension: String?
    let perItem: Int?

    enum CodingKeys: String, CodingKey {
        case modality, mediaType, perItem
        case fileExtension = "extension"
    }
}

/// The dispatch contract one Studio publishes for one model (`studiohub.lane-contract.v1`).
///
/// The app read none of this until now. It is the only place a scheduler — or this screen — can
/// learn what a lane accepts, with which bounds, without reading the worker's source.
struct NativeLaneContract: Decodable, Equatable {
    let schema: String?
    /// The lane's own modality: the string every `/studio/<modality>/…` call is addressed with, so
    /// the defaults route is reached without a second table in the app.
    let modality: String
    let operation: String?
    let parameters: [NativeLaneParameter]
    /// The `(aspect_ratio, resolution)` pairs that exist, where the lane publishes them.
    ///
    /// A video lane's two parameter enums are not a set of canvases: the LTX lane's `7:4` is its
    /// `256p` canvas and its `448p` class carries `11:7` and `1:1`, so offering every ratio beside
    /// every class offers requests the lane refuses. `nil` means the lane states none (every other
    /// modality), and the parameter enums are then the only statement there is.
    let canvases: [NativeLaneCanvas]?
    /// Chat publishes the model's own limits here, each with the artefact that states it.
    let modelLimits: [String: NativeModelLimit]?
    let artifact: NativeLaneArtifact?
    /// The Hub adds its own proxy timeout when it echoes a contract on a package row.
    let deadlineSeconds: Double?
}

/// One video canvas the lane publishes: a ratio, the size class it belongs to, the exact pixels,
/// and the durations that canvas accepts. Published as a set of pairs rather than two lists.
struct NativeLaneCanvas: Decodable, Equatable {
    let aspectRatio: String
    let resolution: String
    let width: Int
    let height: Int
    let durations: [Int]

    // **No explicit `"aspect_ratio"` here, and that is the fix for a live defect.** The decoder
    // this app uses sets `keyDecodingStrategy = .convertFromSnakeCase`, which rewrites the JSON's
    // `aspect_ratio` to `aspectRatio` before matching — so a CodingKey whose raw value is the
    // snake_case spelling can never be found. Every canvas failed to decode, and because the
    // catalogue response is decoded whole, one canvas emptied **every** model list in the app.
    // Measured 2026-09-27 against the running Hub 0.6.83: `keyNotFound: aspect_ratio …
    // models[19].laneContract.canvases[0]`. The strategy is the only key mapping this app needs;
    // the key is spelled the way the strategy produces it.
    enum CodingKeys: String, CodingKey {
        case aspectRatio, resolution, width, height, durations
    }
}

/// One row of the size table an Image model publishes: the aspect ratio, the pixels it means, and
/// the resolution tier the model states for it.
struct NativeImageSizeRow: Decodable, Equatable, Identifiable {
    let aspectRatio: String
    let width: Int
    let height: Int
    let tier: String?
    let isDefault: Bool?

    var id: String { aspectRatio }

    enum CodingKeys: String, CodingKey {
        case aspectRatio, width, height, tier
        case isDefault = "default"
    }
}

/// One read-only fact about a model, and what stated it.
///
/// `source` is the producer's own provenance where it publishes one — a `config.json` key, a
/// worker default, a bound's `bound_source` — and nil only where the value is the whole of what
/// was published (a family name, a boolean the row states without qualification).
struct NativeModelFact: Equatable, Identifiable {
    let label: String
    let value: String
    let source: String?

    var id: String { label }

    /// One line, with its provenance beside it — what the detail pane renders and what the
    /// evidence harness prints.
    var line: String {
        guard let source, !source.isEmpty else { return "\(label): \(value)" }
        return "\(label): \(value) — \(source)"
    }
}

/// One section of the Models tab: a Studio from the app's own Studio list, its symbol and name,
/// and the models it offers. Nothing about which Studios exist is written down here.
struct NativeModelSection: Identifiable, Equatable {
    let function: StudioFunction
    let models: [NativeModel]

    var id: String { function.id }
    var symbol: String { function.symbol }
    /// The header, exactly as the Models tab renders it.
    var header: String {
        "\(function.rawValue) — \(models.count) \(models.count == 1 ? "model" : "models")"
    }
}

/// One editor row in "Your defaults for this model".
///
/// Built only from the parameter the lane published: its type, its own bound, and the value in
/// force. The lane's order is preserved, so a parameter the Studio adds appears where the Studio
/// put it.
struct NativeModelDefaultControl: Identifiable, Equatable {
    enum Kind: Equatable {
        case integer
        case number
        /// A picker of exactly the lane's options, in the lane's order.
        case choice([String])
        case text
        case toggle
        /// A type this editor has no rule for (`array`, `object`, or a type it has never seen).
        /// Shown with the lane's own note and never sent: a control invented for a shape nobody
        /// published would be a value the lane never validated.
        case unrenderable
    }

    let name: String
    let kind: Kind
    let minimum: Double?
    let maximum: Double?
    let minimumExclusive: Bool
    let maximumExclusive: Bool
    let step: Double?
    let maxCharacters: Int?
    let maxBytes: Int?
    /// The type the lane published, kept for the caption of a shape this editor cannot render.
    let typeName: String
    /// Suggested values the lane published for this parameter, in the lane's own order and labels.
    /// They are offered beside the field, never instead of it: the bound is what the lane accepts.
    let ladder: [NativeLadderRung]?
    let note: String?
    /// The lane itself says it accepts this parameter and discards it.
    let ignored: Bool
    /// The value in force: the operator's chosen default over the lane's own.
    let value: NativeJSONValue?
    /// What the lane states, so "Restore" can be seen to return the control to it.
    let stated: NativeJSONValue?
    /// The operator has chosen a value for this parameter.
    let chosen: Bool
    let boundSource: String?

    var id: String { name }

    /// Whether this control sends a value in a PUT. A parameter the lane ignores, or a shape this
    /// editor has no rule for, is shown but not sent.
    var sendsValue: Bool {
        if ignored { return false }
        if case .unrenderable = kind { return false }
        return true
    }

    /// The bound, in the lane's own numbers, exactly as the editor captions the control.
    var boundText: String {
        var parts: [String] = []
        switch kind {
        case .choice(let options):
            parts.append("one of " + options.joined(separator: ", "))
        case .toggle:
            parts.append("on or off")
        case .text:
            if let maxCharacters { parts.append("up to \(maxCharacters) characters") }
            if let maxBytes { parts.append("up to \(maxBytes) bytes") }
        case .integer, .number:
            parts.append(Self.rangeText(minimum: minimum, maximum: maximum,
                                        minimumExclusive: minimumExclusive,
                                        maximumExclusive: maximumExclusive))
        case .unrenderable:
            parts.append("published as \(typeName)")
        }
        if let stated { parts.append("default \(stated.fieldText)") }
        return parts.joined(separator: " · ")
    }

    static func rangeText(minimum: Double?, maximum: Double?, minimumExclusive: Bool,
                          maximumExclusive: Bool) -> String {
        let low = minimum.map(NativeJSONValue.trimmed)
        let high = maximum.map(NativeJSONValue.trimmed)
        switch (low, high) {
        case (nil, nil): return "no bound published"
        case (let value?, nil):
            return "\(minimumExclusive ? "above" : "at least") \(value)"
        case (nil, let value?):
            return "\(maximumExclusive ? "below" : "up to") \(value)"
        case (let value?, let ceiling?):
            if !minimumExclusive && !maximumExclusive { return "\(value)–\(ceiling)" }
            let lower = "\(minimumExclusive ? ">" : "≥")\(value)"
            let upper = "\(maximumExclusive ? "<" : "≤")\(ceiling)"
            return "\(lower) and \(upper)"
        }
    }
}

/// One model's editable default surface, exactly as the Studio's own route answers it
/// (`GET /studio/<modality>/api/model-defaults`).
struct NativeModelDefaults: Decodable, Equatable, Identifiable {
    let repo: String
    /// The values in force: the operator's choices over what the lane states.
    let defaults: [String: NativeJSONValue]
    /// What the lane itself states.
    let stated: [String: NativeJSONValue]
    /// What the operator changed.
    let chosen: [String: NativeJSONValue]
    /// A stored value the current lane refuses, with the reason. Shown, never silently dropped.
    let notUsed: [String: String]
    /// The settable parameters the lane publishes, in the lane's order.
    let parameters: [NativeLaneParameter]

    var id: String { repo }
}

struct NativeModelDefaultsPayload: Decodable, Equatable {
    let models: [NativeModelDefaults]
    let settingsFile: String?
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
    /// The preset voices this model's own runtime published. Empty for every model that does not
    /// publish a choosable roster — a single-speaker model has one built-in voice and nothing to
    /// pick — so a view never lends one family's names to another family's model.
    var speakers: [String] = []
    /// Whether this model's family publishes a choosable preset roster at all. False means the
    /// model does not offer a list to choose from (a single built-in voice), not that the list has
    /// not arrived yet; a view needs the difference to say something true in both cases.
    var publishesPresetRoster: Bool = false
    /// Qwen3-TTS *Base* models clone from a reference clip instead of using preset speakers.
    var requiresReference: Bool = false
    /// Which of the three ways of choosing a voice this model offers, exactly as the local engine
    /// published them and in its order. The Audio page is organised by this, because the three are
    /// different jobs with different controls: a saved voice, a picked speaker, or a written
    /// description of the voice. More than one is normal — a model that clones *and* designs gets
    /// both tabs — so there is deliberately no single "the mode" to ask for.
    var voiceModes: [VoiceMode] = []
    /// Whether the local engine said how this model makes a voice at all.
    ///
    /// False is a fact about the engine, not about the model: an older worker publishes no `modes`,
    /// and this app must say so rather than pick one of the three forms it knows. Guessing is how a
    /// CustomVoice model was once sent down the clone path and asked for a reference voice it does
    /// not have.
    var reportsVoiceModes: Bool { !voiceModes.isEmpty }
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
    /// The longest single render this host's Music engine can do, when the model publishes one.
    var singlePassMaxSeconds: Double? = nil
    /// The Image worker's loading-size setting as it applies to this model on this Mac, published
    /// on the model's own catalogue row. Nil means the row published none at all — which is what a
    /// catalogue row for a manifest the worker has no registered model for looks like — and the
    /// Image page says so rather than drawing a control that would do nothing.
    var loadingModes: NativeImageLoadingModes? = nil
    /// The dispatch contract this model's Studio published for it. The Models tab reads every
    /// per-generator fact from here — the operation, every parameter with its enforced bound, and
    /// the modality every `/studio/<modality>/…` call is addressed with. Nil means the Studio
    /// published none, and the detail pane then offers no editor rather than an invented one.
    var laneContract: NativeLaneContract? = nil
    /// The model limits the row publishes *with the artefact that states each one*
    /// (`model_limits`). The bare `context_window_tokens` the same row carries is a number with no
    /// provenance — a worker's fallback published beside the model's own fact is exactly the
    /// confusion the standing rule forbids — so a limit is only ever rendered from here.
    var modelLimits: [String: NativeModelLimit]? = nil
    /// The size table an Image model publishes: each aspect ratio with the pixels it means and the
    /// resolution tier the model states. Empty for every other Studio and for a row that published
    /// none, and then nothing is shown for it.
    var imageSizeRows: [NativeImageSizeRow] = []
    /// The Image worker's own answer to "does this model read a guidance value at all?".
    var guidanceIgnored: Bool = false
    /// The RAM tier a Studio publishes for this model, and the key it came from, so the fact can
    /// be shown with its provenance instead of a re-derived threshold.
    var minimumMemoryGB: Double? = nil
    var minimumMemorySource: String? = nil
    /// Subtitle: whether this model's manifest declares segment timestamps, and word-level ones.
    /// Both are optional claims: a Studio that publishes neither renders nothing for them, and a
    /// model that publishes `false` says "not supported" rather than staying silent.
    var supportsSegmentTimestamps: Bool? = nil
    var wordTimestampsClaim: Bool? = nil
    /// Subtitle: the weight files the worker's own catalogue row lists. This is the receipt — real
    /// data, and it says which files the runtime found — but it is not the format. The format is
    /// `weightFormat` below, which the row states.
    var weightFiles: [String] = []
    /// Subtitle: the weight format the package's own row states (`weight_format`), read verbatim.
    /// Nil when the row states none, and then no format is claimed: the extension of a file name is
    /// not the package's format, and "index, safetensors" is not a format at all.
    var weightFormat: String? = nil
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

    // MARK: - Read-only facts, per generator

    /// Every read-only fact this model publishes, in the order the detail pane shows them.
    ///
    /// Three sources, and no fourth: the lane's own `parameters` (every one, in the lane's order,
    /// with the bound it enforces), the values the row publishes beside them (a size table, a
    /// long-form policy, model limits, a sample rate, a language list), and the label the row
    /// carries. Each block is gated on the data being present rather than on which Studio this is,
    /// so a Studio that starts publishing one of these keys gets its facts with no edit here —
    /// which is the same rule the editor follows.
    ///
    /// Nothing is invented to fill a gap: a value that is absent renders nothing, and a limit whose
    /// producer states no value is left out rather than shown as a curated number.
    var facts: [NativeModelFact] {
        var facts: [NativeModelFact] = []
        if let contract = laneContract {
            if let operation = contract.operation, !operation.isEmpty {
                facts.append(NativeModelFact(label: "Operation", value: operation,
                                             source: "lane_contract.operation"))
            }
            // Every parameter the lane publishes, with the bound it enforces. This is what makes
            // "the steps range" or "the duration ladder" a read of the lane's own numbers rather
            // than a table kept in the app.
            for parameter in contract.parameters {
                facts.append(NativeModelFact(
                    label: parameter.name,
                    value: Self.parameterSummary(parameter),
                    source: parameter.boundSource ?? parameter.note))
                // A published ladder is a fact about the parameter, in the lane's own labels, with
                // the rungs that are rendered in sections marked as such.
                if let ladder = parameter.ladder, !ladder.isEmpty {
                    facts.append(NativeModelFact(
                        label: "\(parameter.name) ladder",
                        value: ladder.map { rung in
                            rung.exceedsSinglePass == true
                                ? "\(rung.label) (renders in sections)" : rung.label
                        }.joined(separator: " | "),
                        source: "lane_contract.parameters.\(parameter.name).ladder"))
                }
            }
            facts.append(contentsOf: Self.limitFacts(modelLimits ?? contract.modelLimits ?? [:]))
            if let artifact = contract.artifact {
                var shape: [String] = []
                if let media = artifact.mediaType, !media.isEmpty { shape.append(media) }
                if let perItem = artifact.perItem { shape.append("\(perItem) per item") }
                if !shape.isEmpty {
                    facts.append(NativeModelFact(label: "Artifact", value: shape.joined(separator: " · "),
                                                 source: "lane_contract.artifact"))
                }
            }
        }
        // The size table: for each aspect ratio the pixels it means, and the tier the model
        // states. One row per ratio, so a model publishing several says so and a model publishing
        // one tier does not imply more.
        for row in imageSizeRows {
            var value = "\(row.width) × \(row.height)"
            if let tier = row.tier, !tier.isEmpty { value += " · \(tier)" }
            if row.isDefault == true { value += " · default" }
            facts.append(NativeModelFact(label: row.aspectRatio, value: value,
                                         source: "catalog row sizes"))
        }
        if guidanceIgnored {
            facts.append(NativeModelFact(
                label: "Guidance", value: "accepted and ignored by this model",
                source: "catalog row guidance_ignored"))
        }
        if let sampleRateHz {
            var value = "\(sampleRateHz) Hz"
            if let channels { value += " · \(channels) channel\(channels == 1 ? "" : "s")" }
            facts.append(NativeModelFact(label: "Sample rate", value: value,
                                         source: "catalog row sample_rate_hz"))
        }
        if let singlePassMaxSeconds {
            facts.append(NativeModelFact(
                label: "Single-pass render",
                value: "up to \(NativeJSONValue.trimmed(singlePassMaxSeconds)) s · longer durations are rendered in sections",
                source: "catalog row generation_limits.single_pass_max_seconds"))
        }
        // Whether this lane accepts vocals and lyrics at all, read from the parameters it
        // published: the Music worker's own rule is that a runtime forcing instrumental output
        // must not advertise the keys it discards, so their absence is the fact. A Studio whose
        // duration is not this shape publishes none of them and renders nothing here.
        if let contract = laneContract, contract.parameters.contains(where: { $0.name == "duration_seconds" }) {
            let vocal = contract.parameters.map(\.name).filter(Self.vocalParameters.contains)
            facts.append(NativeModelFact(
                label: "Vocals & lyrics",
                value: vocal.isEmpty
                    ? "not accepted: this lane publishes no vocal or lyric parameter"
                    : "accepted: " + vocal.joined(separator: ", "),
                source: "lane_contract.parameters"))
        }
        if !languages.isEmpty {
            facts.append(NativeModelFact(label: "Languages", value: languages.joined(separator: ", "),
                                         source: nil))
        }
        if let segment = supportsSegmentTimestamps {
            facts.append(NativeModelFact(
                label: "Segment timestamps", value: segment ? "supported" : "not supported",
                source: "catalog row supports_segment_timestamps"))
        }
        if let words = wordTimestampsClaim {
            facts.append(NativeModelFact(
                label: "Word timestamps", value: words ? "supported" : "not supported",
                source: "availability row supports_word_timestamps"))
        }
        // The format is what the package *states*, never what its file names look like. The worker
        // publishes `weight_format` from the package's own manifest; joining extensions off the
        // receipt was an interpretation standing in for a fact, and it read a package of
        // `model.safetensors.index.json` + shards as the format "index, safetensors". Where the row
        // states no format, no format is claimed. The receipt stays, as its own fact, because which
        // files the runtime found is real data.
        if let weightFormat, !weightFormat.isEmpty {
            facts.append(NativeModelFact(label: "Weight format", value: weightFormat,
                                         source: "catalog row weight_format"))
        }
        if !weightFiles.isEmpty {
            facts.append(NativeModelFact(label: "Weight files",
                                         value: weightFiles.joined(separator: ", "),
                                         source: "catalog row readiness.files.weight_files"))
        }
        if let minimumMemoryGB {
            facts.append(NativeModelFact(
                label: "Memory tier",
                value: "\(NativeJSONValue.trimmed(minimumMemoryGB)) GB",
                source: minimumMemorySource))
        }
        // The heading is "Label", not "Quantization", because a label is what the row publishes.
        // Where the label carries the bits ("Qwen3.5-9B 4-bit") they are on screen verbatim; where
        // it carries none ("Whisper turbo") the line does not claim a quantization that does not
        // exist. Nothing is parsed out of the repo name — that is the name-substring guessing that
        // made six voice packages invisible — and when a structured `quantization` field exists in
        // the manifests it gets a `Quantization` line of its own, beside this honest one.
        facts.append(NativeModelFact(label: "Label", value: label, source: "label"))
        if !family.isEmpty {
            facts.append(NativeModelFact(label: "Family", value: family, source: nil))
        }
        if !voiceModes.isEmpty {
            facts.append(NativeModelFact(
                label: "Voice modes",
                value: voiceModes.map(\.title).joined(separator: " · "),
                source: "catalog row modes"))
        }
        return facts
    }

    /// One parameter, as a fact: its bound in the lane's own numbers and, where the lane states
    /// one, its default.
    static func parameterSummary(_ parameter: NativeLaneParameter) -> String {
        var parts: [String] = []
        let type = parameter.type.lowercased()
        if let options = parameter.options, !options.isEmpty {
            parts.append("one of " + options.joined(separator: ", "))
        } else {
            switch type {
            case "integer", "number":
                parts.append(NativeModelDefaultControl.rangeText(
                    minimum: parameter.minimum, maximum: parameter.maximum,
                    minimumExclusive: parameter.minimumExclusive == true,
                    maximumExclusive: parameter.maximumExclusive == true))
            case "string":
                if let limit = parameter.maxCharacters { parts.append("up to \(limit) characters") }
                if let bytes = parameter.maxBytes { parts.append("up to \(bytes) bytes") }
            case "boolean":
                parts.append("on or off")
            default:
                parts.append("published as \(type)")
            }
        }
        if parameter.required == true { parts.append("required") }
        if let value = parameter.defaultValue { parts.append("default \(value.fieldText)") }
        return parts.isEmpty ? "published" : parts.joined(separator: " · ")
    }

    /// The facts a row's own `model_limits` states: the number, and the artefact that states it.
    /// A limit with no value renders nothing — the source is then the reason, not a substitute.
    static func limitFacts(_ limits: [String: NativeModelLimit]) -> [NativeModelFact] {
        // Sorted so the block is stable; the names are the producer's own keys.
        limits.sorted { $0.key < $1.key }.compactMap { name, limit in
            guard let value = limit.value else { return nil }
            let rendered: String
            switch value {
            case .null: return nil
            case .integer(let number): rendered = "\(number)"
            case .number(let number): rendered = NativeJSONValue.trimmed(number)
            case .string(let text): rendered = text
            case .bool(let flag): rendered = flag ? "yes" : "no"
            }
            return NativeModelFact(label: name, value: rendered,
                                   source: limit.source ?? "lane_contract.model_limits")
        }
    }

    /// The names a Music lane uses for the parameters that make it sing. A lane that forces
    /// instrumental output publishes none of them (its own rule: do not offer what the runtime
    /// discards), so their absence is what "this model cannot sing" is read from. This list
    /// interprets a published vocabulary; it never decides whether a control exists.
    static let vocalParameters: Set<String> = ["instrumental", "lyrics", "vocal_language"]

    /// The lane's published **switches**: every boolean parameter it honours, in its own order.
    ///
    /// A switch is a choice, not a capability, and it is the lane's to publish. Studio Hub 0.6.83
    /// gave the LTX lane an `audio` parameter (default true, with the lane's own note) and no other
    /// video parameter is a boolean, so the Video page renders a toggle for exactly what this
    /// returns and never for a name this app knows on its own. Empty means the lane publishes none.
    var publishedBooleanParameters: [NativeLaneParameter] {
        (laneContract?.parameters ?? []).filter { $0.type.lowercased() == "boolean" }
    }
}

extension NativeModel {
    /// One parameter this model's lane publishes, or nil when it publishes none of that name.
    ///
    /// The `default` inside is the value in force — the worker hands its resolved map to the lane
    /// contract before publishing it — so a page reads here what an omitted field would receive,
    /// and reads the bound the worker will clamp to. A model whose lane does not publish the name
    /// offers no control for it at all, which is the rule the Image and Music forms already follow.
    ///
    /// An extension rather than a member on purpose: the struct's own body is read by a source
    /// contract test that stops at the first indented closing brace, so a method added among the
    /// stored properties silently truncated it and hid the property it was looking for.
    func laneParameter(_ name: String) -> NativeLaneParameter? {
        laneContract?.parameters.first { $0.name == name }
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
            return "Use one of the voices saved in your voice library. The model repeats that voice."
        case .preset:
            return "Voices that come with the model. Pick one and type your script."
        case .design:
            return "Describe a voice in words — 'deep, slow, warm' — and the model creates it."
        }
    }
    /// What to say when no installed model offers this. It names no family: the catalog decides
    /// which models exist, and a copy that lists them goes stale the moment a family is added.
    var emptyReason: String {
        switch self {
        case .cloning:
            return "No voice-cloning model is installed on this Mac yet."
        case .preset:
            return "No model with built-in voices is installed on this Mac yet."
        case .design:
            return "No voice-design model is installed on this Mac yet."
        }
    }
}

/// One selectable idle-release mode. Thresholds arrive from the Studio that owns them, so
/// the app never hard-codes what "Balanced" means.
struct NativeMemoryMode: Decodable, Identifiable, Equatable {
    let mode: String
    let label: String
    let idleSeconds: Int?
    /// The Studio's own sentence for this mode, resolved for this Mac.
    ///
    /// It is the only sentence that knows what `auto` resolved to here — "Follows this Mac:
    /// releases after 10 idle minutes here" on a 32 GB Mac, "…as soon as each job finishes here"
    /// on an 8 GB one. The derived sentence below cannot know that, so it is only the fallback
    /// for a worker that published no detail at all.
    let detail: String?
    var id: String { mode }
    var summary: String {
        if let detail, !detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return detail
        }
        guard let idleSeconds else { return "Keeps the model loaded until you release it." }
        if idleSeconds == 0 { return "Releases the model as soon as each job finishes." }
        if idleSeconds < 60 { return "Releases after \(idleSeconds) seconds idle." }
        return "Releases after \(idleSeconds / 60) idle minutes."
    }
}

/// What one Studio reports about the model it is holding right now.
struct NativeMemoryPolicy: Decodable, Equatable {
    let mode: String
    /// What the selected mode actually resolved to on this Mac. `auto` answers with the machine's
    /// own mode, so this is what is really in force and `mode` alone is not.
    let resolvedMode: String?
    /// What `auto` means on this Mac, published beside the selection. Read only as the fallback
    /// for `resolvedMode`, so the machine rule is never re-derived here.
    let machineMode: String?
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

    /// The one mode whose answer depends on the machine, as every worker names it. A single
    /// contract constant, not a list of the modes: the options and their sentences still come
    /// from the producer's payload.
    private static let machineResolvedMode = "auto"

    var resolvedOptions: [NativeMemoryMode] { options ?? [] }
    var resident: [String] { (loadedModels ?? []).map { $0.joined(separator: " ") } }
    var isFixed: Bool { fixed == true }

    /// Which mode is in force, when the producer says the selected one resolved elsewhere:
    /// "Auto resolves to Balanced on this Mac". Both names are the producer's own labels from
    /// its published options, so a mode this build has never heard of still renders.
    var resolution: String? {
        let resolved = resolvedMode ?? (mode == Self.machineResolvedMode ? machineMode : nil)
        guard let resolved, resolved != mode else { return nil }
        return "\(label(for: mode)) resolves to \(label(for: resolved)) on this Mac"
    }

    func label(for id: String) -> String {
        resolvedOptions.first { $0.mode == id }?.label ?? id
    }
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

/// `PUT /studio/image/api/loading-mode`: the one field the Image worker reads.
private struct ImageLoadingModeBody: Encodable {
    let mode: String
}

/// `PUT /studio/<modality>/api/model-defaults`: the model, and the whole set of values the editor
/// shows. Wholesale on purpose — merging would make a cleared field indistinguishable from an
/// untouched one, which is the same rule the Studio applies when it stores them.
private struct ModelDefaultsBody: Encodable {
    let repo: String
    let values: [String: NativeJSONValue]
}

/// `DELETE /studio/<modality>/api/model-defaults`: the model whose stored defaults are forgotten.
private struct ModelDefaultsRepoBody: Encodable {
    let repo: String
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
    /// The controller this Mac's Hub handed its group to, and when. Non-nil means this Hub is
    /// **retired**: it keeps its history and refuses remote work, so the app must not show it as
    /// still taking jobs. Absent from a Hub older than the handover route.
    let handedOverTo: String?
    let handedOverAt: Double?
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

/// One Studio a joined Mac runs, as the node reported it at join time.
struct NativeFleetStudio: Decodable, Equatable {
    let id: String
    let modality: String?

    /// "Image (image)" is not how a person reads a list; the modality is the part that decides
    /// what work the Mac can take, so it is what the row shows when it is published.
    var display: String {
        guard let modality, !modality.isEmpty else { return id }
        return "\(id) · \(modality)"
    }
}

/// One Mac that has joined this controller, with the state it is actually in.
///
/// Every field below `state` is published by `GET /api/hub/fleet/nodes` and was decoded by nothing:
/// `role`, `studios`, `version`, `joined_at`, `last_seen_at` and the credential's own dates. A
/// roster row that shows only a name and an address cannot answer "is this the Mac I think it is,
/// what can it run, and when was it last here" — which is what the row is for.
struct NativeFleetNode: Decodable, Equatable, Identifiable {
    let id: String
    let name: String
    let address: String
    let state: String
    let detail: String?
    let version: String?
    let role: String?
    let studios: [NativeFleetStudio]?
    let joinedAt: Double?
    /// The last time this controller heard from the Mac. Absent until it has answered a probe.
    let lastSeenAt: Double?
    /// When the credential this Mac holds was issued. Published so a revocation is visible as a
    /// fact on the roster rather than only as the node's behaviour.
    let credentialIssuedAt: Double?
    /// Whether this controller holds a credential for the Mac. False is a row from before
    /// per-node credentials, which has to prove itself on its next join.
    let hasCredential: Bool?

    var isReachable: Bool { state == "reachable" }
    /// The node answered and refused *this controller's* credential — a different fault from a
    /// Mac that is switched off, and one that no amount of waiting fixes.
    var isUnauthorized: Bool { state == "unauthorized" }
    var isFaulted: Bool { state == "error" }
}

/// A machine this controller has evicted. Published by `GET /api/hub/fleet/nodes` under `removed`
/// so the only way to re-admit one is not "remember its id".
struct NativeFleetRemovedNode: Decodable, Equatable, Identifiable {
    let id: String
    let name: String
    let address: String?
    let removedAt: Double?
    let removedBy: String?
    let role: String?
    let studios: [NativeFleetStudio]?
    let joinedAt: Double?
}

struct NativeFleetInventory: Decodable, Equatable {
    let role: String
    let nodes: [NativeFleetNode]
    /// The evicted machines. Absent from a Hub older than the removal record, which is why it is
    /// optional rather than an empty list that would read as "nothing was ever removed".
    let removed: [NativeFleetRemovedNode]?
    let summary: NativeFleetSummary
}

struct NativeFleetSummary: Decodable, Equatable {
    let total: Int
    let reachable: Int
    let unreachable: Int
    let error: Int
    /// Answered, but refused this controller's credential. Its own count, because a fleet page
    /// that folded it into "unreachable" sent the operator to look at the network.
    let unauthorized: Int?
    let removed: Int?
}

/// One Mac's outcome in a Fleet-token rotation or a handover, keyed by machine id in the answer.
struct NativeFleetSyncMachine: Decodable, Equatable {
    let ok: Bool?
    /// `verified`, `manual`, `failed` or `unreachable`.
    let status: String?
    let detail: String?

    /// What happened to this Mac, in the words the page shows. The Hub's own vocabulary is honest
    /// but internal: `manual` means "it refused the credential it was given, so somebody has to
    /// walk to it", which is the sentence the operator needs.
    var outcome: String {
        switch status {
        case "verified": return "followed"
        case "manual": return "refused the old credential"
        case "unreachable": return "could not be reached"
        case "failed": return "answered with an error"
        case let other?: return other
        default: return "did not answer"
        }
    }
}

/// `POST /api/hub/fleet` (rotate) and `POST /api/hub/fleet/handover`.
///
/// One shape because it is one operation with two callers: every joined Mac is handed a new
/// credential, and — for a handover — a new head. `verified + manual + pending == total` is the
/// whole point of the answer: a partial result is never a success, so the app must be able to say
/// which Macs did not follow by name.
struct NativeFleetSyncAnswer: Decodable, Equatable {
    let ok: Bool?
    let rotated: Bool?
    let handedOverTo: String?
    let handedOverAt: Double?
    let token: String?
    let total: Int
    let verified: Int
    let manual: Int
    let pending: Int
    let machines: [String: NativeFleetSyncMachine]
}

/// What the operator is shown after a rotation or a handover.
///
/// Built from the Hub's answer rather than from the request, so the page reports what happened to
/// each Mac. `followed` and `unfinished` carry the machine ids, which the page resolves to names
/// from the roster it already has.
struct FleetOperationReport: Equatable {
    enum Kind: Equatable { case rotation, handover }
    let kind: Kind
    /// The new controller's address, for a handover.
    let target: String?
    /// The new Fleet token, shown once so the operator can give it to GatewayKH.
    let token: String?
    let total: Int
    let verified: Int
    let manual: Int
    let pending: Int
    let followed: [String]
    let unfinished: [String]
    /// Machine id -> what happened to it, in words. A list of ids cannot say *why* a Mac did not
    /// follow, and the two reasons need different work: one is off, the other refused a credential.
    let outcomes: [String: String]

    var isComplete: Bool { manual == 0 && pending == 0 }

    /// The line that must never read as success when it was not.
    var summary: String {
        let action = kind == .handover ? "Handover" : "Fleet token rotation"
        let lead = "\(action): \(verified) of \(total) Mac\(total == 1 ? "" : "s") followed."
        guard let unfinishedLine = FleetCopy.unfinishedNotice(total: total, verified: verified,
                                                             manual: manual, pending: pending)
        else { return lead }
        return "\(lead) \(unfinishedLine)"
    }
}

/// One Mac in the fleet, with everything needed to judge whether to send it work.
///
/// A machine that has not reported in shows what is known - that it is absent - rather than
/// numbers invented to fill the row: disk and memory stay nil, and a machine with no finished
/// job reports no average instead of a zero that would read as "instant".
struct NativeFleetMachine: Decodable, Equatable, Identifiable {
    let machine: String
    let label: String
    /// "controller", "agent" or "standalone" - what this Mac's part in the group is, so a row
    /// says whether the machine hands work out or takes it.
    let role: String?
    /// Where this machine answers, when the Hub knows. Shown so a row can be recognised as the
    /// Mac at a particular address rather than only as a name.
    let address: String?
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
    var isController: Bool { role == "controller" }
}

/// The controller this Mac joined, as its own Hub reports it.
///
/// A node reads its group back from its controller, so when the controller is asleep the row is
/// still here with a state that says so - which is the difference between a Mac whose group is
/// down and a Mac that was never in one.
struct NativeFleetPoolMember: Decodable, Equatable {
    let machine: String
    let label: String
    let role: String
    let address: String?
    let state: String?
    let detail: String?
    let version: String?

    var isReachable: Bool { state == "reachable" }
}

struct NativeFleetOverview: Decodable, Equatable {
    let machines: [NativeFleetMachine]
    let summary: NativeFleetOverviewSummary
    /// "controller", "agent" or "standalone" for this Mac.
    let role: String?
    /// The controller this Mac joined, present only on a node.
    let controller: NativeFleetPoolMember?
    /// False when this Mac knows it belongs to a group but could not read the group's roster, so
    /// the pool is known to be incomplete rather than known to be this small.
    let rosterReadable: Bool?

    var isMember: Bool { controller != nil }
    var belongsToGroup: Bool { (role ?? "") == "agent" || controller != nil }
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

/// One turn in the local Chat Studio transcript.
struct NativeChatTurn: Identifiable, Equatable {
    let id: String
    let role: String
    var content: String
    /// Why this turn has no reply, when it has none.
    ///
    /// A failed request is marked on the operator's own turn rather than answered with a model turn
    /// that never happened - the transcript has to be able to say "this one got no answer" without
    /// inventing one. Display only: the wire keeps the two roles it has always had, and this field
    /// is never sent to the Hub.
    var failure: String?
    /// The tokens the worker stated it generated for this reply, when it stated a usage block.
    /// Nil is "not stated" and is never shown as zero.
    var completionTokens: Int? = nil
    /// The worker's own stated generation time for this reply (`studio_hub.elapsed_seconds`).
    /// The live reply carries no Hub-measured round trip, so when the worker stated no time there
    /// is no rate at all rather than one derived from the app's own clock.
    var statedGenerationSeconds: Double? = nil

    /// "10 tokens · 1.4 tok/s · 7.2 s", or the token count alone, or nothing.
    var speed: String {
        NativeJob.tokenSpeed(completionTokens: completionTokens,
                             statedGenerationSeconds: statedGenerationSeconds,
                             roundTripSeconds: nil)
    }
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
    /// What this job was asked for, for the Studio or modality that publishes those controls.
    /// Exactly one of them is set for a job this build submitted; all are nil for a job another
    /// client filed, which is why the facts line is built from whatever is here and never assumes.
    var videoSettings: NativeVideoSettings? = nil
    var voiceSettings: NativeVoiceSettings? = nil
    /// Where the artifact is on this Mac, when the worker that made it is on this Mac. The Hub
    /// records the worker's own `output_path`; a nil here means the row cannot point Finder at a
    /// file, and its Show in Folder action is disabled rather than opening an empty window.
    var artifactPath: String? = nil
    /// Runtime facts the Hub recorded for this result, shown beside it so an operator can see
    /// what they actually got without opening the file.
    var durationSeconds: Double? = nil
    var requestedWidth: Int? = nil
    var requestedHeight: Int? = nil
    /// The **finished clip's** own measured facts, which the Hub copies onto the item from the
    /// worker's report. Kept separate from `requestedWidth`/`requestedHeight`, which are what was
    /// asked for: a lane that resolves its own canvas (Video does) states these and not those.
    var clipWidth: Int? = nil
    var clipHeight: Int? = nil
    var clipFrames: Int? = nil
    var clipFrameRate: Double? = nil
    var clipHasAudio: Bool? = nil
    /// How long the generation took, or has been running. Separate from `durationSeconds`, which
    /// is the length of the media produced.
    var generationSeconds: Double? = nil
    var runningSeconds: Double? = nil
    /// The tokens the worker stated it generated, from the item's own `usage` block. Nil is
    /// "the worker stated none" and is never rendered as zero.
    var completionTokens: Int? = nil
    /// The Chat worker's own stated generation time (`generation_s`), which is the only number a
    /// rate may be derived from. It is deliberately separate from `generationSeconds`, which stays
    /// the Hub's measured round trip: on a cold turn that includes the model load, so the two are
    /// different facts and only one of them is the model's speed.
    var statedGenerationSeconds: Double? = nil
    /// The studio's own label for the phase it is in ("DiT sampling"), when it reports one.
    var progressStage: String? = nil
    /// How long this batch has been silent **while still active**, from the Hub's own progress
    /// clock. Nil is "the Hub stated none", which is what every queued and every finished job
    /// reports — silence only means something while a job is in flight.
    ///
    /// **Visible, never destructive.** `stall.py` is explicit that a stall is not a timeout and must
    /// never kill a job: a merely-quiet job is one a long deadline exists to protect. This exists so
    /// the operator can see what the API already published; nothing is cancelled on the strength of
    /// it.
    var stalledSeconds: Double? = nil
    /// What a Chat completion answered. Empty for every other Studio, whose result is a file.
    ///
    /// A Chat answer is text and nothing else: the Hub records it as the item's `text` rather than
    /// pointing at a worker artifact, because the worker streams its completion and keeps no file.
    var answer: String = ""
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

    /// "10 tokens · 1.4 tok/s · 7.2 s", or the token count with the round trip named as such, or
    /// nothing when the worker stated no token count.
    ///
    /// The rate is `completion_tokens / generation_s` and nothing else. `generation_s` is the Chat
    /// worker's own stated generation time. When the worker stated none, zero or a non-finite
    /// value there is **no** rate: the Hub's round trip (`runtime_s`) is measured around the whole
    /// proxied call and includes the model load on a cold turn, so a rate derived from it would
    /// understate the model and change meaning between the first reply and the second. The round
    /// trip is still shown where it exists, labelled as the round trip, which is what it is.
    var tokenSpeed: String {
        Self.tokenSpeed(completionTokens: completionTokens,
                        statedGenerationSeconds: statedGenerationSeconds,
                        roundTripSeconds: generationSeconds)
    }

    static func tokenSpeed(completionTokens: Int?, statedGenerationSeconds: Double?,
                           roundTripSeconds: Double?) -> String {
        // No stated token count, no rate and no token line: a count the worker never gave cannot
        // be published, not even as the zero a default would produce.
        guard let completionTokens, completionTokens > 0 else { return "" }
        if let stated = statedGenerationSeconds, stated.isFinite, stated > 0 {
            let rate = Double(completionTokens) / stated
            let speed = rate >= 0.1 ? String(format: "%.1f tok/s", rate)
                                    : String(format: "%.3f tok/s", rate)
            return "\(completionTokens) tokens · \(speed) · \(clock(stated))"
        }
        guard let roundTripSeconds, roundTripSeconds.isFinite, roundTripSeconds > 0 else {
            return "\(completionTokens) tokens"
        }
        return "\(completionTokens) tokens · \(clock(roundTripSeconds)) round trip"
    }

    /// How long something took, at the scale it actually reached.
    ///
    /// One rounding, and every part comes from that one value. The previous version truncated the
    /// minutes (`Int(seconds) / 60`) and rounded the remainder separately, then dropped the seconds
    /// when the rounded remainder was zero: 119.7 s reported "1 min" and hid 59.7 of them, and any
    /// run finishing in the last second before a whole minute also read as a round minute. The
    /// branch is taken from the same rounded value, so a duration can never be reported at a scale
    /// it has not reached - 59.97 s is "1 min 0 s", never "60.0 s".
    ///
    /// Below a minute the tenths are what the operator watches, so they are kept exactly as they
    /// were; at and above it the seconds are always stated, and past an hour the minutes are not
    /// left to run up to 127. The value is clamped before it becomes an `Int`, so a nonsense
    /// duration cannot trap the app on the way to the screen.
    static func clock(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds > 0 else { return "0.0 s" }
        let whole = Int(min(seconds, 359_999).rounded())
        if whole < 60 { return String(format: "%.1f s", seconds) }
        let hours = whole / 3600
        let minutes = (whole % 3600) / 60
        let rest = whole % 60
        return hours > 0 ? "\(hours) h \(minutes) min \(rest) s" : "\(minutes) min \(rest) s"
    }

    /// "12.5 s · 1.4 MB · 1024×1024" — only the parts that are known.
    ///
    /// This is the **result** line: what came out. The canvas falls back from the clip's own
    /// measured size to the size that was requested, because a lane that resolves its own canvas
    /// (Video) states the first and not the second — a finished 1024×576 clip read as "2.0 s ·
    /// 381 KB" with no dimensions at all until this fallback existed.
    var assetFacts: String {
        var parts: [String] = []
        if let durationSeconds, durationSeconds > 0 {
            // One duration format for the whole app. This line rendered minutes as "%.1f min", so
            // a 2 min 5 s clip read "2.1 min" and dropped the seconds - the same defect the run
            // time had, in the line directly above it and in the same row the operator reads.
            parts.append(Self.clock(durationSeconds))
        }
        if let bytes, bytes > 0 {
            parts.append(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file))
        }
        if let width = clipWidth ?? requestedWidth, let height = clipHeight ?? requestedHeight {
            parts.append("\(width)×\(height)")
        }
        // Frames and frame rate together, and only where a frame rate exists: the Hub copies a
        // raw `frames` count for Music and Voice items too, where it is the sample count — a song
        // read "441000 frames" beside its own length, which is a number about nothing.
        if let clipFrameRate, clipFrameRate > 0 {
            if let clipFrames, clipFrames > 0 { parts.append("\(clipFrames) frames") }
            parts.append("\(Self.trimmed(clipFrameRate)) fps")
        }
        if let clipHasAudio { parts.append(clipHasAudio ? "with audio" : "silent") }
        return parts.joined(separator: " · ")
    }

    /// What this job was **asked for**: the controls that explain a result, as the Hub echoed them
    /// back. The counterpart to `assetFacts`, which says what came out.
    ///
    /// One function, read by the Studio pages and the Jobs tab alike, so the same job cannot be
    /// described two ways. The canvas is deliberately left to `assetFacts` for Image — it would
    /// otherwise print twice in one row — and shown here for Video, where "576p · 16:9" is the
    /// operator's own choice and the pixel canvas is the lane's.
    func requestFacts(for function: StudioFunction) -> String {
        var parts: [String] = []
        switch function {
        case .image:
            if let settings = imageSettings {
                if let steps = settings.steps { parts.append("\(steps) steps") }
                if let guidance = settings.guidance { parts.append("guidance \(Self.trimmed(guidance))") }
                if let seed = settings.seed, seed >= 0 { parts.append("seed \(seed)") }
            }
        case .music:
            if let settings = musicSettings {
                parts.append(Self.clock(settings.durationSeconds))
                if let steps = settings.steps { parts.append("\(steps) steps") }
                if let guidance = settings.guidanceScale {
                    parts.append("guidance \(Self.trimmed(guidance))")
                }
                if let shift = settings.shift { parts.append("shift \(Self.trimmed(shift))") }
                if let seed = settings.seed, seed >= 0 { parts.append("seed \(seed)") }
                if settings.instrumental == true { parts.append("instrumental") }
            }
        case .video:
            if let settings = videoSettings {
                if let resolution = settings.resolution { parts.append(resolution) }
                if let aspect = settings.aspectRatio { parts.append(aspect) }
                if let seconds = settings.durationSeconds { parts.append(Self.clock(seconds)) }
                if let audio = settings.audio { parts.append(audio ? "audio on" : "audio off") }
                if let mode = settings.mode, !mode.isEmpty { parts.append(mode) }
                if let seed = settings.seed, seed >= 0 { parts.append("seed \(seed)") }
            }
        case .audio:
            // The script's own length, which is the number a Voice budget is spent in — and which
            // the Audio rows never showed, so a 1,200-character script and a two-word one looked
            // the same in history.
            if !text.isEmpty { parts.append("\(text.count) characters") }
            if let speed = voiceSettings?.speed { parts.append("speed \(Self.trimmed(speed))×") }
        case .chat, .transcription:
            break
        }
        return parts.joined(separator: " · ")
    }

    /// Whether Finder can be pointed at this job's file: the worker wrote it on this Mac.
    var canRevealArtifact: Bool {
        guard let artifactPath, !artifactPath.isEmpty else { return false }
        return FileManager.default.fileExists(atPath: artifactPath)
    }

    /// "1.5", "1", "0.35" — a control's value with no trailing zeros and no invented precision.
    static func trimmed(_ value: Double) -> String {
        guard value.isFinite else { return "—" }
        if value == value.rounded() { return String(Int(value)) }
        return String(format: "%g", value)
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
        // The same one formatter: a saved 90-second reference clip reads "1 min 30 s", not "1.5 min".
        return NativeJob.clock(durationSeconds)
    }
    /// What the worker published for this voice's gender, in the operator's words.
    ///
    /// The worker owns the vocabulary and returns `gender` verbatim, so anything it publishes is
    /// rendered rather than replaced by "Neutral": an out-of-vocabulary value used to read as
    /// blank or wrong. The two codes the worker itself writes are spelled out; a value it did not
    /// supply is shown as "Not stated" instead of being given a meaning it never claimed.
    var genderLabel: String { Self.genderLabel(for: gender) }
    static func genderLabel(for gender: String) -> String {
        let value = gender.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return "Not stated" }
        switch value.lowercased() {
        case "m", "male": return "Male"
        case "f", "female": return "Female"
        case "n", "neutral": return "Neutral"
        default: return value.prefix(1).uppercased() + value.dropFirst()
        }
    }
    /// The licence vocabulary, spelled the way the operator reads it. This is the *fallback* for a
    /// new voice, where the worker has supplied nothing: a voice that already carries a licence
    /// shows the worker's own value, in-vocabulary or not.
    static let licenseOptions: [(id: String, label: String)] = [
        ("self-owned", "Owned by me"),
        ("public-domain", "Public domain"),
        ("permission", "Used with permission"),
        ("unknown", "Unknown"),
    ]
    /// The same for language: a convenience list for a new voice, never a closed set. The worker
    /// owns the store, so a Khmer voice tagged `km` is stored and shown as `km`.
    static let languageOptions = ["en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"]

    /// The licence choices to offer, including this voice's own value when the worker published
    /// one this build's short list does not name — otherwise the picker would silently show
    /// nothing selected for a perfectly valid stored value.
    static func licenseChoices(current: String) -> [(id: String, label: String)] {
        let value = current.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, !licenseOptions.contains(where: { $0.id == value }) else { return licenseOptions }
        return licenseOptions + [(id: value, label: value)]
    }

    /// The language choices to offer: the languages this library already holds, then the fallback
    /// list. Empty when the library names none, so the caller can fall back to free text.
    static func languageChoices(published: [String]) -> [String] {
        let known = published.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        let unique = known.reduce(into: [String]()) { result, value in
            if !result.contains(value) { result.append(value) }
        }
        return unique.isEmpty ? languageOptions : unique.sorted()
    }
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
    /// A refusal that names each problem against the parameter it belongs to — what a 422 from a
    /// defaults route carries. Kept as a case of its own so the editor can put each reason under
    /// its own field instead of collapsing them into one sentence.
    case refusals([String: String])

    var errorDescription: String? {
        switch self {
        case .message(let message): return message
        case .refusals(let reasons):
            return reasons.sorted { $0.key < $1.key }.map { "\($0.key): \($0.value)" }
                .joined(separator: "; ")
        }
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

/// Streams one ranged artifact download to a file, appending to an existing fragment when the
/// controller honours Range.
///
/// URLSession's async byte sequence yields one byte at a time, which is useless for a gigabyte
/// image, so this holds its own session and data-task delegate and writes each received chunk
/// straight to disk. It leaves the file in place on every failure: the caller resumes from its
/// length, or discards it when it proves not to be the signed artifact.
private final class ResumableArtifactDownloader: NSObject, URLSessionDataDelegate {
    private let configuration: URLSessionConfiguration
    private let destination: URL
    private let startingOffset: Int64
    private let expectedSha256: String
    private let onProgress: (Int64) -> Void

    private var handle: FileHandle?
    private var continuation: CheckedContinuation<Int64, Error>?
    private var failure: Error?
    private var received: Int64
    private var lastReported: Int64
    private var expectedHost: String?
    private var expectedPort: Int?

    init(configuration: URLSessionConfiguration,
         destination: URL,
         startingOffset: Int64,
         expectedSha256: String,
         onProgress: @escaping (Int64) -> Void) {
        self.configuration = configuration
        self.destination = destination
        self.startingOffset = startingOffset
        self.expectedSha256 = expectedSha256
        self.onProgress = onProgress
        self.received = startingOffset
        self.lastReported = startingOffset
        super.init()
    }

    /// Download `request` (already carrying a Range header when resuming) and return the number of
    /// bytes now on disk. Throws with the partial file left in place.
    func run(_ request: URLRequest) async throws -> Int64 {
        let configuration = configuration.copy() as! URLSessionConfiguration
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        expectedHost = request.url?.host
        expectedPort = request.url?.port
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            session.dataTask(with: request).resume()
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse else {
            failure = ReleaseError("The controller's artifact route returned no HTTP response.")
            completionHandler(.cancel)
            return
        }
        guard http.url?.host == expectedHost, http.url?.port == expectedPort else {
            failure = ReleaseError("The controller's artifact route answered from an address this Mac did not ask.")
            completionHandler(.cancel)
            return
        }
        guard (200..<300).contains(http.statusCode) else {
            failure = ReleaseError("The controller's artifact route answered HTTP \(http.statusCode). Nothing was installed.")
            completionHandler(.cancel)
            return
        }
        // The route advertises the digest; the signed descriptor's sha256 stays authoritative and
        // is checked over the bytes too. A disagreement here is refused all the same.
        if let announced = http.value(forHTTPHeaderField: "X-Content-SHA256"),
           announced.lowercased() != expectedSha256 {
            failure = ReleaseError("The controller's artifact route advertises SHA-256 \(announced), not the signed \(expectedSha256). Nothing was installed.")
            completionHandler(.cancel)
            return
        }
        do {
            var resumeAt = received
            if startingOffset > 0, http.statusCode == 200 {
                // The server ignored the Range header and is sending the whole artifact, so the
                // stale fragment is replaced rather than appended to.
                resumeAt = 0
                try? FileManager.default.removeItem(at: destination)
            } else if startingOffset > 0, http.statusCode == 206 {
                // A 206 must actually start where the fragment ends, or the bytes would land at
                // the wrong offset in the file.
                if let contentRange = http.value(forHTTPHeaderField: "Content-Range"),
                   let start = Self.rangeStart(contentRange), start != received {
                    try? FileManager.default.removeItem(at: destination)
                    failure = ReleaseError("The controller answered a range starting at byte \(start), not the \(received) this Mac has. The part-downloaded file was discarded; press Install again.")
                    completionHandler(.cancel)
                    return
                }
            } else {
                // Nothing to resume from: start a clean file.
                try? FileManager.default.removeItem(at: destination)
            }
            if !FileManager.default.fileExists(atPath: destination.path) {
                guard FileManager.default.createFile(atPath: destination.path, contents: nil,
                                                     attributes: [.posixPermissions: 0o600]) else {
                    throw ReleaseError("Could not create the private update file.")
                }
            }
            let handle = try FileHandle(forWritingTo: destination)
            try handle.seekToEnd()
            // A fragment seeded or left by an earlier attempt must be private too.
            try? FileManager.default.setAttributes([.posixPermissions: 0o600],
                                                   ofItemAtPath: destination.path)
            self.handle = handle
            received = resumeAt
            completionHandler(.allow)
        } catch {
            failure = error
            completionHandler(.cancel)
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let handle else { return }
        do {
            try handle.write(contentsOf: data)
            received += Int64(data.count)
            // Report at most every 8 MB; a callback per chunk is thousands of main-actor hops.
            if received - lastReported >= 8 << 20 {
                lastReported = received
                onProgress(received)
            }
        } catch {
            failure = ReleaseError("Could not write the update to disk: \(error.localizedDescription)")
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        try? handle?.close()
        handle = nil
        let continuation = continuation
        self.continuation = nil
        if let failure {
            continuation?.resume(throwing: failure)
        } else if let error {
            let receivedText = ByteCountFormatter.string(fromByteCount: received, countStyle: .file)
            continuation?.resume(throwing: ReleaseError("The download stopped after \(receivedText): \(error.localizedDescription). The part-downloaded file is kept; press Install again to resume."))
        } else {
            continuation?.resume(returning: received)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    /// `bytes 1000-4095/4096` -> `1000`.
    private static func rangeStart(_ contentRange: String) -> Int64? {
        let trimmed = contentRange.trimmingCharacters(in: .whitespaces)
        guard trimmed.lowercased().hasPrefix("bytes ") else { return nil }
        let afterUnit = trimmed.dropFirst("bytes ".count)
        guard let range = afterUnit.split(separator: "/").first,
              let start = range.split(separator: "-").first else { return nil }
        return Int64(start)
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
    /// Which model a Studio opens on is not written down here. The worker publishes its own
    /// `default_model` for transcription, and every other Studio opens on the first model the Hub
    /// offers for it — the same rule Image and Music already used. A repo constant here is what
    /// opened the Audio page on a model that was not in its own picker.

    @Published private(set) var models: [NativeModel] = []
    @Published private(set) var audioJobs: [NativeJob] = []
    @Published private(set) var imageJobs: [NativeJob] = []
    /// Independent Subtitle studio history, read from the Hub's transcription jobs (durable),
    /// which the packaged Hub now routes to the subtitle worker.
    @Published private(set) var subtitleJobs: [NativeJob] = []
    /// Chat history, read from the Hub's own record of each Chat-page completion.
    ///
    /// A Chat send is synchronous - the app posts, the Hub proxies to the local Chat worker and the
    /// answer comes back - and it used to leave nothing behind at all, so this list could only ever
    /// be empty. The Hub now files each attempt as a finished one-item batch, which is what this
    /// lists, answered or failed.
    @Published private(set) var chatJobs: [NativeJob] = []
    @Published private(set) var imageConfigurations: [String: NativeImageConfiguration] = [:]
    @Published var imagePrompt = "" { didSet { scheduleSave() } }
    /// Changing the model changes the controls, so the new model's own published defaults are
    /// loaded at once: leaving them nil disabled Generate until the next poll, which read as the
    /// model being unusable.
    @Published var imageModel = "" {
        didSet {
            // Changing the model changes the controls, so the new model's own published controls
            // are loaded at once. Leaving them nil disabled Generate until the next poll, which
            // read as the model being unusable. These are the *resolved* controls: the Image
            // worker publishes `generation_defaults` as the operator's saved default over the
            // model's own, so seeding from here is what makes a saved default reach a job.
            if imageModel != oldValue { imageSettings = imageConfigurations[imageModel]?.defaults }
            scheduleSave()
        }
    }
    @Published var imageSettings: NativeImageSettings? { didSet { scheduleSave() } }
    /// Per-studio Music history and the last Music draft, mirroring Image. It is the same Hub job
    /// queue, filtered to `modality == "music"`, so no second job subsystem is introduced.
    @Published private(set) var musicJobs: [NativeJob] = []
    @Published private(set) var musicConfigurations: [String: NativeMusicConfiguration] = [:]
    @Published var musicPrompt = "" { didSet { scheduleSave() } }
    @Published var musicModel = "" {
        didSet {
            // The Music worker resolves `generation_defaults` the same way Image does.
            if musicModel != oldValue { musicSettings = musicConfigurations[musicModel]?.defaults }
            scheduleSave()
        }
    }
    @Published var musicSettings: NativeMusicSettings? { didSet { scheduleSave() } }
    /// A Music-only note, shown beside the Music form. A failure still goes to `error`, which is
    /// the banner every workspace shares.
    @Published var musicNote: String?
    @Published private(set) var submittingMusic = false
    /// Per-Studio Video history and the last Video draft, mirroring Music. Same Hub queue,
    /// filtered to `modality == "video"`.
    @Published private(set) var videoJobs: [NativeJob] = []
    @Published var videoPrompt = "" { didSet { scheduleSave() } }
    @Published var videoModel = "" { didSet { scheduleSave() } }
    /// The lane's own published choices. Empty until a model is selected; the form reads the
    /// model's contract rather than a ladder written here, and sends only what it publishes.
    @Published var videoAspectRatio = "" { didSet { scheduleSave() } }
    @Published var videoResolution = "" { didSet { scheduleSave() } }
    @Published var videoDurationSeconds = 0 { didSet { scheduleSave() } }
    /// The operator's own value for each **published switch** on the selected video model, by the
    /// lane's own parameter name. Empty means nobody chose anything, which is not the same as
    /// "off": an untouched switch leaves the field out of the request so the lane's own default
    /// decides. Only names the selected model publishes are ever sent, and
    /// `snapVideoControlsToPublishedCanvases` prunes a choice that belonged to a previous model.
    @Published var videoSwitches: [String: Bool] = [:] { didSet { scheduleSave() } }
    /// A Video-only note, shown beside the Video form.
    @Published var videoNote: String?
    @Published private(set) var submittingVideo = false
    var musicConfiguration: NativeMusicConfiguration? { musicConfigurations[musicModel] }
    /// Which Mac new work should run on, as the Hub's `routing` value: nil means the Hub picks,
    /// and `machine:<id>` pins one machine. Kept as the owner's standing choice so a pin set on
    /// the fleet screen applies to every workspace until it is cleared.
    @Published var routingPin: String? { didSet { scheduleSave() } }
    @Published private(set) var submittingImage = false
    var imageConfiguration: NativeImageConfiguration? { imageConfigurations[imageModel] }
    /// The Image worker's loading-size setting for this Mac, as the worker itself publishes it:
    /// the machine-wide selection, its options, and what each model resolves to. Nil until the
    /// Image page reads it. This is *not* the release policy in Settings — that is
    /// `/api/hub/memory/policy`, and Image's answer there is fixed because mflux is built inside
    /// a job and dropped when it returns.
    @Published private(set) var imageLoadingMode: NativeImageLoadingModeStatus?
    @Published private(set) var updatingImageLoadingMode = false

    /// The loading control for the selected Image model, built only from what the producer
    /// published: whether a control exists at all comes from the model's own catalogue row, and
    /// the selection and the configuration in force come from the Image worker's status read when
    /// it has answered (it is fresher than the catalogue after a change).
    var imageLoadingControl: NativeImageLoadingControl? {
        guard let model = models.first(where: { $0.function == .image && $0.repo == imageModel })
        else { return nil }
        let published = model.loadingModes
        let options = published?.publishedOptions ?? []
        let offered = published?.supported == true && !options.isEmpty
        let status = imageLoadingMode?.publishedModels.first { $0.repo == model.repo }
        let selected = status?.selected ?? published?.selected ?? ""
        return NativeImageLoadingControl(
            repo: model.repo,
            options: offered ? options : [],
            selected: selected,
            effective: status?.effective ?? published?.effective,
            detail: offered ? options.first { $0.id == selected }?.detail : nil,
            // The model's own sentence for having one loading mode. The fallback is not a trade:
            // it names the fact that the catalogue row published no loading modes at all, which
            // is what a row for a manifest with no registered model looks like.
            reason: offered ? nil : (published?.reason
                ?? "This model's catalogue entry publishes no loading mode, so this Mac has no loading setting for it."))
    }

    /// Local Chat Studio: one transcript, one local text model. The page talks to the Hub's
    /// `/api/hub/chat/completions`, which proxies to the packaged Chat worker with the same
    /// admission and worker signing as every other Studio.
    @Published var chatModel = "" { didSet { scheduleSave() } }
    @Published var chatPrompt = "" { didSet { scheduleSave() } }
    @Published private(set) var chatTurns: [NativeChatTurn] = []
    @Published private(set) var submittingChat = false

    @Published private(set) var downloads: [NativeDownload] = []
    /// The saved voices on this Mac, read from the Voice worker's library.
    @Published private(set) var voices: [NativeVoice] = []
    /// Whether `voices` is the result of a read that finished on this connection.
    ///
    /// The Audio page's Clone tab draws a picker from `voices`, and an empty array means two very
    /// different things: "this Mac has no saved voices" and "the list has not been read yet". The
    /// page cannot tell them apart from the array alone, so it said "No saved voices yet" while the
    /// read was still in flight. This is that missing fact, kept beside the list it describes.
    @Published private(set) var voicesLoaded = false
    /// Set while a voice is being added, edited or removed, so a button can show it is working
    /// and cannot be pressed twice.
    @Published private(set) var savingVoice = false
    /// The result of the last voice action, shown beside the list.
    @Published var voiceNote: String?
    /// A voice action that failed, shown where the action was taken.
    @Published var voiceError: String?
    @Published var audioText = "" { didSet { scheduleSave() } }
    /// Empty until the Hub answers: the first supported model of this Studio is selected by
    /// `reconcileSelections` from what the Hub actually offers.
    @Published var audioModel = "" { didSet { syncAudioToModel(); scheduleSave() } }
    /// Which of the three voice tabs the operator is on. Kept so reopening the app returns them to
    /// the job they were doing rather than to whichever model happens to sort first.
    @Published var audioMode: VoiceMode = .cloning { didSet { scheduleSave() } }
    /// The written description a voice-design model turns into a voice.
    @Published var audioDesignPrompt = "" { didSet { scheduleSave() } }
    /// Speaking pace, or nil when the operator has not chosen one on this page.
    ///
    /// Nil is the ordinary state, and it is deliberately not the same as 1.0: an untouched control
    /// puts no pace in the request at all, so the model's own value in force applies — what the
    /// Models tab stores for this model over the pace its lane states. A number here is a choice
    /// made on this page, it is what the request carries, and it therefore always wins. Nothing
    /// about the pace is fixed in the app: the bounds and the value in force are read from the
    /// selected model's lane contract, which the worker publishes with its defaults resolved.
    @Published var audioSpeed: Double? { didSet { scheduleSave() } }

    /// The pace control the selected Audio model publishes, from its own lane contract.
    ///
    /// Nil when no Audio model is selected, when the model publishes no `speed`, when it declares
    /// the parameter accepted-and-ignored, or when it publishes no range or no value in force. A
    /// model that reads no pace gets no slider, rather than one whose movement changes nothing.
    /// The `default` inside is the resolved value — `lane_contract.for_model` is handed what the
    /// request path would apply — so the number shown and the number an omitted field receives
    /// cannot be two answers to one question.
    var audioSpeedControl: NativeLaneParameter? {
        guard let model = models.first(where: { $0.repo == audioModel && $0.function == .audio }),
              let parameter = model.laneParameter("speed"),
              parameter.type == "number",
              !parameter.isDeclaredIgnored,
              parameter.numericRange != nil,
              parameter.defaultValue?.doubleValue != nil
        else { return nil }
        return parameter
    }

    /// The pace the slider shows: the operator's own choice where they made one, otherwise the
    /// model's value in force. So the control always displays the pace that will actually be used,
    /// instead of a stored app constant silently outranking the Models tab. A choice made against
    /// another model is pulled into this model's published bounds — the same bounds the worker
    /// clamps to, so what is shown is what is heard.
    var audioSpeedInForce: Double? {
        guard let control = audioSpeedControl, let range = control.numericRange,
              let value = audioSpeed ?? control.defaultValue?.doubleValue else { return nil }
        return min(max(value, range.lowerBound), range.upperBound)
    }

    /// What an Audio request carries: the operator's pace only when they chose one. An untouched
    /// control sends nothing, which is what lets this model's stored default reach the engine.
    var audioSpeedForRequest: Double? {
        audioSpeed == nil ? nil : audioSpeedInForce
    }

    /// The language control the selected Audio model publishes, from its own lane contract.
    ///
    /// Nil for a family that takes no language input, which is most of them: the lane omits
    /// `language` on purpose because it discards the key — the Khmer VITS voices are Khmer by
    /// construction — and the page must not offer a control whose value goes nowhere. Chatterbox is
    /// the live case that publishes one, and it publishes it `required`, so the lane's own answer
    /// decides whether a request can be made without it. Voice publishes no optional `language`,
    /// which is why there is no stored default for it to follow.
    var audioLanguageControl: NativeLaneParameter? {
        guard let model = models.first(where: { $0.repo == audioModel && $0.function == .audio })
        else { return nil }
        return model.laneParameter("language")
    }
    /// The ready-made voice the operator chose, or empty when they have not chosen one.
    ///
    /// Empty is the ordinary state and is deliberately not the same as naming the first voice in
    /// the roster. An untouched picker must send no `preset_speaker` at all, so this model's stored
    /// voice — set in the Models tab — is what the engine speaks with. Seeding the roster's first
    /// entry into this field, which is what the app did, made that stored default unreachable: the
    /// picker looked untouched while the app supplied a name on every preset job, and the Voice
    /// worker reads a supplied parameter as the caller's own answer.
    @Published var audioSpeaker = "" { didSet { scheduleSave() } }

    /// The voice that will actually be used, when the producer states one: the operator's own
    /// choice, or the value in force for this model (what the Models tab stores for it). Nil means
    /// nothing is stated — not that the model has no voice — and the picker's not-chosen row says
    /// so in those words rather than naming a voice the lane never published. The engine's own
    /// default is the engine's to state, and this app does not speak for it.
    var audioSpeakerInForce: String? {
        guard let model = models.first(where: { $0.repo == audioModel && $0.function == .audio })
        else { return nil }
        let chosen = audioSpeaker.trimmingCharacters(in: .whitespacesAndNewlines)
        if !chosen.isEmpty { return chosen }
        let stored = model.laneParameter("preset_speaker")?.defaultValue?.stringValue ?? ""
        return stored.isEmpty ? nil : stored
    }

    /// What a preset request carries: the operator's voice only when they chose one, and only when
    /// this model's lane publishes `preset_speaker` at all. A model with a single built-in voice,
    /// or a cloning path, publishes no such parameter — the lane discards the key — so a request
    /// that carried one would be a parameter nothing reads.
    var audioSpeakerForRequest: String? {
        guard let model = models.first(where: { $0.repo == audioModel && $0.function == .audio }),
              let parameter = model.laneParameter("preset_speaker"),
              !parameter.isDeclaredIgnored else { return nil }
        let chosen = audioSpeaker.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !chosen.isEmpty else { return nil }
        // A name the lane does not publish is never sent. The roster is the producer's, and sending
        // one family's voice names to another family's engine is how a Qwen preset was once refused
        // by name after the app had already accepted it.
        if let options = parameter.options, !options.isEmpty, !options.contains(chosen) { return nil }
        return chosen
    }
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
    /// Empty until the Hub answers; the worker's own `default_model` names the transcription
    /// model to open on (`reconcileSelections(defaultTranscription:)`).
    @Published var transcriptionModel = "" { didSet { scheduleSave() } }
    /// The transcription model the worker itself names as its default. Read from the worker's
    /// availability route; the app never writes a repo down as the default.
    @Published private(set) var transcriptionDefaultModel: String?
    @Published var transcriptionFile: URL? { didSet { scheduleSave() } }
    @Published var transcriptionLanguage = "" { didSet { scheduleSave() } }
    /// Per-word timings, or nil when the operator has not chosen.
    ///
    /// The Subtitle worker counts a field as supplied when its key is in the submitted form — its
    /// own words: "an explicit `word_timestamps=false` is still the caller's answer and a stored
    /// `true` cannot override it". So an untouched toggle must submit nothing at all for this
    /// model's stored default to apply; an explicit off is still an answer and still wins.
    @Published var wordTimestamps: Bool? { didSet { scheduleSave() } }

    /// The word-timings control for the selected Subtitle model: the model's own runtime has to
    /// claim word timings, and its lane has to publish a boolean `word_timestamps` with a value in
    /// force. Nil otherwise, so a model that produces no word timings gets no toggle rather than
    /// one whose movement would change nothing.
    ///
    /// The value returned is the one in force: the operator's own answer where they gave one,
    /// otherwise what the Models tab stores for this model over the `false` its lane states. It was
    /// a persisted app value with no control anywhere in the app, submitted as `false` on every
    /// request — so the Models control could never reach the engine and the operator could not
    /// reach the value at all.
    var wordTimestampsControl: Bool? {
        guard let model = models.first(where: { $0.repo == transcriptionModel && $0.function == .transcription }),
              model.supportsWordTimestamps,
              let parameter = model.laneParameter("word_timestamps"),
              parameter.type == "boolean",
              !parameter.isDeclaredIgnored,
              let inForce = parameter.defaultValue?.boolValue
        else { return nil }
        return wordTimestamps ?? inForce
    }
    @Published var transcriptionResult: NativeTranscript? { didSet { scheduleSave() } }
    @Published private(set) var transcribing = false
    @Published private(set) var submittingAudio = false
    @Published var error: String?
    @Published private(set) var refreshing = false
    @Published private(set) var isReachable = false
    /// How many times this store has attached to a Hub.
    ///
    /// Published because the Audio page keys its voice-library load on it: a `.task(id:)` reloads
    /// when its id changes, and a reconnect may be a *different* Hub whose voice library has
    /// nothing to do with the one already in memory. The Bool "connected" alone cannot tell the two
    /// apart — a silent reconnect leaves it true — so the id is this counter, not a tab name.
    @Published private(set) var connectionGeneration = 0
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
    /// Each Studio's published default surface, keyed `"<modality>/<repo>"` — the modality being
    /// the one the model's own lane contract states, so the route is addressed without a table.
    @Published private(set) var modelDefaults: [String: NativeModelDefaults] = [:]
    /// The per-parameter reasons a PUT was refused with, keyed the same way and then by parameter,
    /// so each reason lands against the field it belongs to instead of in one generic banner.
    @Published private(set) var modelDefaultRefusals: [String: [String: String]] = [:]
    /// Whether every Studio that publishes a lane contract has been asked for its defaults.
    @Published private(set) var modelDefaultsLoaded = false
    /// The modalities already read on this connection, so several pages asking for the same
    /// Studio make one request.
    private var modelDefaultsReadModalities: Set<String> = []
    /// The per-run controls each Image and Music model last published, so a *change* in the
    /// catalogue's resolved values can be told from the same values read again.
    private var publishedImageDefaults: [String: NativeImageSettings] = [:]
    private var publishedMusicDefaults: [String: NativeMusicSettings] = [:]
    @Published private(set) var savingModelDefaults = false
    /// Where the values are stored, as the Studio's own route names the file.
    @Published private(set) var modelDefaultsSettingsFile: String?
    /// The result of the last save or restore, shown in the editor.
    @Published private(set) var modelDefaultsNote: String?
    /// Bumped whenever the published defaults change, so a page re-seeds its per-run controls.
    @Published private(set) var modelDefaultsRevision = 0
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
    /// True when the controller answered the authenticated read and refused this Mac's credential.
    /// A third state, not a flavour of "unreachable": 52 Macs showed green after a controller
    /// replacement while that controller called every one of them `unauthorized`, and the fix was
    /// to ask with a credential and to say which of the two answers came back.
    @Published private(set) var controllerCredentialRefused = false
    @Published private(set) var controllerHealthNote: String?
    /// Set when this Mac's controller has **removed** it. Terminal: the re-join loop stops, the
    /// group credential goes, and the page says who removed it, when, and where re-admitting
    /// happens. Nil while this Mac is a member or was never one.
    @Published private(set) var controllerRemoval: ControllerRemoval?
    /// This Mac's own node credential, issued by its controller in the join answer. Presented on
    /// every call this Mac makes home — register, re-join, read the group, remove itself — and the
    /// only credential whose revocation is about this Mac alone (A8). Kept with the membership the
    /// app already persists; the Hub keeps its own 0600 copy, written over
    /// `POST /api/hub/fleet/adopt`, and presents it on the reads it makes itself.
    @Published private(set) var nodeCredential: String?
    /// The last Fleet-token rotation or handover, with each Mac's outcome. Nil until one is run.
    @Published private(set) var fleetOperation: FleetOperationReport?
    @Published private(set) var fleetOperationRunning = false
    /// Persist or clear the node credential with the membership, the way the pinned release key is
    /// kept. The app owns the preferences file; the workspace owns the value.
    var onNodeCredentialChanged: ((String?) -> Void)?
    /// Called when the Hub's **Fleet token** has been replaced on this Mac — a rotation this Mac
    /// performed, or a leave that minted its own. The page shows that value next to a Copy button,
    /// so a rotation that left the old one on screen would hand GatewayKH a token that no longer
    /// opens anything.
    var onFleetCredentialChanged: (() -> Void)?
    /// The Hub's **current** Fleet token, read from the Hub's own state file by the app layer.
    ///
    /// The membership's copy is a snapshot taken when this Mac joined or started, and a rotation
    /// another Mac pushed to this one's Hub replaces it without telling this app. Presenting the
    /// stale value on an authenticated read would then report a healthy Mac as refused — so the
    /// read asks the app for the live value at the moment it needs it.
    var fleetTokenProvider: (@MainActor () -> String?)?

    /// What this Mac's controller did to it, in the words its own page shows.
    struct ControllerRemoval: Equatable {
        /// The date the controller stated, e.g. "2026-09-28".
        let date: String?
        /// The controller's own refusal, quoted verbatim.
        let detail: String
        let address: String
        /// False when the local Hub could not be told to mint its own credential, which is a
        /// different sentence: the Mac stopped re-joining either way, but it may still hold the
        /// group's token.
        let clearedGroupCredential: Bool
    }

    /// What this Mac needs to re-join its controller by itself: the address it was told, this
    /// Mac's own fleet credential (which, after joining, *is* the controller's), and the networks
    /// it is reachable on. Set by the app from its saved preferences once the Hub is ready.
    struct FleetMembership {
        let controllerAddress: String
        let token: String?
        let boundHosts: [String]
        /// This Mac's own node credential, issued by that controller. Part of the membership: the
        /// app persists it beside the address, and a Mac that rejoins after a restart presents it.
        var nodeCredential: String? = nil
        /// The controller's release key pinned on first read. Part of the membership: it is
        /// restored with it at startup and cleared when this Mac leaves the group.
        var releasePublisherKey: ReleasePublisherKey? = nil
    }
    var storedMembership: FleetMembership?
    private var startupMembershipAttemptID: UUID?
    private var startupRejoinTask: Task<Bool, Never>?
    /// The backed-off periodic re-join a node keeps while its controller is not up. A single task,
    /// so one attempt runs at a time; it stops when the join succeeds or this Mac stops being a
    /// node. `rejoinAttempt` indexes the schedule and `rejoinAuthFailures` counts consecutive
    /// 401/403 refusals so a stale credential cannot spin silently.
    private var rejoinTask: Task<Void, Never>?
    private var rejoinAttempt = 0
    private var rejoinAuthFailures = 0
    private var rejoinAuthAnnounced = false
    /// How long to wait before each automatic re-join retry. Overridable so the contract suite can
    /// exercise the loop without waiting fifteen minutes; the schedule itself is
    /// `RejoinBackoff.delays`.
    var rejoinBackoffSeconds: [TimeInterval] = RejoinBackoff.delays
    /// The status of the last controller join that was refused for authentication (401/403), else
    /// nil. Read by the re-join loop to tell a stale credential from a controller that is simply off.
    private(set) var lastJoinAuthStatus: Int?
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
    /// True only when the release is signed by the pinned controller key, is newer than (or a
    /// rebuild of) the version installed here, and has an artifact form this Mac can install.
    @Published private(set) var updateOffered = false
    /// True when the controller holds a release older than the version installed here. Shown
    /// distinctly and with no Install button, so a stale controller cannot roll this Mac backwards.
    @Published private(set) var offeredReleaseIsOlder = false
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
    /// The product version this running app was packaged as (`CFBundleShortVersionString`), used
    /// with `currentAppBuild` to order an advertised release against this one. Writable for tests.
    var currentAppVersion: String? = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    /// The app bundle an update replaces.
    var installedAppURL: URL = Bundle.main.bundleURL
    /// The descriptor behind `offeredRelease`, kept for install so verification runs against the
    /// same bytes that were offered rather than a second, possibly different, read.
    private var offeredReleaseDescriptor: ReleaseDescriptor?
    /// Persist or clear the pinned key with this Mac's controller membership.
    var onReleasePublisherKeyChanged: ((ReleasePublisherKey?) -> Void)?
    /// The controller's own release-signing identity as its route reports it — what a joining node
    /// pins. Kept apart from `releasePublisherKey`, which is the key already pinned on this Mac.
    @Published private(set) var controllerReleaseIdentity: ReleaseIdentity?
    /// The gateway key the owner has installed on this controller, or nil when none is installed.
    /// Read from `trusted_publisher` on `GET /api/hub/release`; never inferred from a release.
    @Published private(set) var trustedPublisher: TrustedPublisher?
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
    /// A local file the app could not read and set aside. Kept apart from `startupNote` so a Hub
    /// refresh cannot erase it, and composed with the refresh's own notes when the banner is built.
    private var recoveryNote: String?
    @Published private(set) var joiningFleet = false
    @Published private(set) var tokenStatus = NativeTokenStatus(isSet: false, masked: "")

    private let session: URLSession
    /// The same configuration `session` was built from, so the artifact downloader's own session
    /// (which needs its own data delegate) still uses the fixture protocol in tests.
    private let sessionConfiguration: URLSessionConfiguration
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
        self.sessionConfiguration = configuration
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
            // A reconnect may be a different Hub, so anything read from the last one is no longer
            // known to be true: the Audio page keys its voice-library read on this counter.
            connectionGeneration += 1
            voicesLoaded = false
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
            // The Clone tab's picker is built from this library and is the only way to choose the
            // voice a clone repeats, so it is read as part of joining a Hub rather than left to
            // whichever page happens to be looked at first. Once per connection; a read that
            // fails is reported and waits for the Voices page or its Refresh button, not a retry
            // loop.
            await loadVoicesIfNeeded()
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
        cancelMembershipRejoin()
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
        // The list stays for the operator to read, but it belongs to the connection that ended:
        // the next connection reads its own library rather than trusting this one.
        voicesLoaded = false
        submittingAudio = false
        submittingImage = false
        submittingMusic = false
        transcribing = false
        // Chat belongs in this list for the same reason as the other four: a send whose connection
        // ends never reaches its own `defer` (that reset is guarded on the connection id), so
        // without this the Send button stayed disabled for the life of the store after a drop.
        submittingChat = false
        memoryInventory = nil
        memoryNote = nil
        updatingMemory = false
        // The loading-size status belongs to the Image worker that answered it; the next
        // connection reads its own rather than showing this one's `effective`.
        imageLoadingMode = nil
        updatingImageLoadingMode = false
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
        // A saved default is a statement about one worker on one Mac, so the next connection reads
        // its own instead of showing this one's values or refusals.
        modelDefaults = [:]
        modelDefaultRefusals = [:]
        modelDefaultsReadModalities = []
        publishedImageDefaults = [:]
        publishedMusicDefaults = [:]
        modelDefaultsLoaded = false
        savingModelDefaults = false
        modelDefaultsNote = nil
        modelDefaultsSettingsFile = nil
        offeredRelease = nil
        offeredReleaseDescriptor = nil
        updateOffered = false
        offeredReleaseIsOlder = false
        releaseRefusal = nil
        installingRelease = false
        releaseProgress = nil
        controllerReleaseIdentity = nil
        trustedPublisher = nil
    }

    func refresh() async { await refresh(reportErrors: true) }

    private func refresh(reportErrors: Bool) async {
        guard baseURL != nil, let requestConnection = connectionID, !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            // The catalog is the Hub itself: if it fails, the Hub is not answering. The package
            // list is a second read of the same Hub, and it must not be fatal: the Hub builds it
            // from every manifest in one pass, so a single malformed manifest answers 503 for the
            // whole route. Read with a hard `try`, that one row threw the refresh, marked the Hub
            // unreachable and blanked every model picker on every Mac - the chat-mlx failure. Losing
            // the package rows costs install state, not the catalog.
            async let catalog: CatalogResponse = get(["api", "hub", "catalog"])
            async let packages: HubModelPackagesResponse? = try? get(["api", "hub", "model-packages"])
            async let transcription: TranscriptionAvailability? = try? get(["studio", "transcription", "api", "transcribe", "availability"])
            async let generation: GenerationAvailability? = try? get(["studio", "voice", "api", "generate", "availability"])
            async let jobs: HubJobsResponse? = try? get(["api", "hub", "jobs"])
            async let transfers: DownloadsResponse? = try? get(["studio", "voice", "api", "downloads"])
            async let settings: TokenStatusWire? = try? get(["studio", "voice", "api", "settings"])
            async let subtitleHistory: HubTranscriptionList? = try? get(["api", "hub", "transcription", "jobs"])
            let (catalogValue, packagesValue, transcriptionValue, generationValue, jobsValue, transfersValue, settingsValue, subtitleValue) =
                try await (catalog, packages, transcription, generation, jobs, transfers, settings, subtitleHistory)
            // Torn down while reading. The child reads that use `try?` come back nil rather than
            // throwing, so without this a cancelled refresh would publish "a studio did not answer"
            // and count its missing jobs — reporting a fault that never happened.
            guard !Task.isCancelled else { return }
            // A degraded read is still a read the operator has to know about: without the package
            // rows every model reads as unsupported, and that looks like the models are broken
            // unless the reason is on screen.
            var startupNotes: [String] = []
            if packagesValue == nil {
                startupNotes.append("The Hub's model package list could not be read; models are listed without their install state until it answers again.")
            }
            if transcriptionValue == nil || generationValue == nil {
                startupNotes.append("A studio did not answer; the rest of this page is up to date.")
            }
            var refreshNotes = startupNotes
            if let recoveryNote { refreshNotes.insert(recoveryNote, at: 0) }
            guard connectionID == requestConnection else { return }
            // Built by merging, not uniqueKeysWithValues: a repeated id in a Hub payload would
            // trap the app on every refresh and on the two-second poll. Last one wins instead.
            // An unreadable list is no rows, which the model builders already treat as "nothing
            // can be installed or dispatched" rather than as a reason to give up on the page.
            modelPackages = packagesValue?.models.reduce(into: [:]) { $0[$1.modelId] = $1 } ?? [:]
            // Every preset roster the Voice runtime publishes, under the key the runtime itself
            // names it (`qwen3_preset_speakers` today, `<family>_preset_speakers` for the next
            // family). Decoding one fixed key was what left a second preset family with an empty
            // roster; each model is then lent only the roster its own family published.
            let rosters = generationValue?.rosters ?? [:]
            // One row per repo, richest kept: the Hub's aggregate carries a model once per studio
            // that lists it, so the same repo can arrive with `family`/`capabilities` on one row and
            // without them on another. Deduplicating here also keeps a bare row from overwriting a
            // rich model's published generation settings in the two maps below.
            let catalogModels = Self.oneEntryPerRepo(
                catalogValue.models, repo: { $0.repo }, richness: Self.catalogRichness)
            models = Self.combineModels(
                catalogModels,
                transcriptionValue?.models ?? [],
                transcriptionAvailable: transcriptionValue?.available ?? false,
                rosters: rosters,
                packages: modelPackages
            )
            imageConfigurations = catalogModels.reduce(into: [:]) { result, model in
                if let configuration = model.imageConfiguration { result[model.repo] = configuration }
            }
            musicConfigurations = catalogModels.reduce(into: [:]) { result, model in
                if let configuration = model.musicConfiguration { result[model.repo] = configuration }
            }
            if imageConfigurations[imageModel] == nil,
               let first = models.first(where: { $0.function == .image && $0.supported }) { imageModel = first.repo }
            if musicConfigurations[musicModel] == nil,
               let first = models.first(where: { $0.function == .music && $0.supported }) { musicModel = first.repo }
            if imageSettings == nil { imageSettings = imageConfiguration?.defaults }
            if musicSettings == nil { musicSettings = musicConfiguration?.defaults }
            // A default saved in the Models tab reaches the generator page as a changed catalogue
            // row on the next refresh. Adopt it here, before the page is opened on it.
            adoptResolvedDefaults()
            // The speaker follows the selected model's own published roster, never a global list:
            // a global outlives the model it was read for, which is how a Khmer model would have
            // been offered another family's voice names. A name the roster does not carry is
            // cleared rather than reseeded — clearing means "not chosen", which sends no
            // `preset_speaker` and lets this model's stored voice apply, where seeding the first
            // entry presented an untouched picker that had silently made a choice.
            if let selected = models.first(where: { $0.repo == audioModel && $0.function == .audio }),
               !audioSpeaker.isEmpty, !selected.speakers.contains(audioSpeaker) {
                audioSpeaker = ""
            }
            var history: [NativeJob] = []
            var images: [NativeJob] = []
            var musics: [NativeJob] = []
            var subtitlesFromHub: [NativeJob] = []
            var chats: [NativeJob] = []
            var videos: [NativeJob] = []
            var unreadable = 0
            // The batch's own `modality` decides where it goes, not a list here that skips
            // anything it has not heard of before the loop: a skipped batch was not shown, not
            // counted and not reported. A modality this build cannot place is collected and named
            // in the startup note instead of disappearing.
            var unplaceable: [String: Int] = [:]
            var finished: [String: NativeJob] = [:]
            for batch in (jobsValue?.batches ?? []) {
                // A modality this build cannot place, or one that maps to a Studio with no
                // batch history, is named rather than skipped: the loop must not fetch a detail
                // it has nowhere to show. Every Studio ships a batch history now, so this is
                // reached only by a modality this build has never heard of.
                guard let function = Self.function(forModality: batch.modality),
                      function.hasBatchHistory else {
                    unplaceable[batch.modality, default: 0] += 1
                    continue
                }
                if let known = finishedJobCache[batch.id] {
                    finished[batch.id] = known
                    Self.file(known, for: function, images: &images, musics: &musics,
                              history: &history, subtitles: &subtitlesFromHub, chats: &chats,
                              videos: &videos)
                    continue
                }
                do {
                    let detail: HubJobDetail = try await get(["api", "hub", "jobs", batch.id])
                    guard connectionID == requestConnection else { return }
                    let job = Self.mapHubJob(detail)
                    if ["done", "cancelled"].contains(job.state) { finished[batch.id] = job }
                    Self.file(job, for: function, images: &images, musics: &musics,
                              history: &history, subtitles: &subtitlesFromHub, chats: &chats,
                              videos: &videos)
                } catch {
                    // A batch whose detail will not load is one row, not the whole page: the listing
                    // is a convenience over the Hub's history, and losing it should not read as the
                    // Hub being down. A cancelled read is not a row that failed, so it stops the
                    // refresh rather than being counted as unreadable.
                    guard !Task.isCancelled, !Self.isCancellation(error) else { return }
                    unreadable += 1
                }
            }
            var jobNotes: [String] = []
            if unreadable > 0 {
                jobNotes.append("\(unreadable) finished job(s) could not be read; the rest are shown.")
            }
            if !unplaceable.isEmpty {
                let named = unplaceable.sorted { $0.key < $1.key }
                    .map { "\($0.value) for \($0.key.isEmpty ? "a batch with no modality" : $0.key)" }
                    .joined(separator: ", ")
                jobNotes.append("The Hub listed job(s) this build has no Studio history for: \(named). Add the modality to `function(forModality:)` in native/StudioAPI.swift.")
            }
            // Appended, not assigned: a degraded read and an unplaceable batch are different
            // facts, and the second must not erase the first.
            refreshNotes.append(contentsOf: jobNotes)
            if !refreshNotes.isEmpty { startupNote = refreshNotes.joined(separator: " ") }
            // Only what the Hub still lists is remembered; anything it forgets leaves with it.
            finishedJobCache = finished
            audioJobs = history.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            imageJobs = images.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            musicJobs = musics.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            videoJobs = videos.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            var subtitles: [NativeJob] = subtitlesFromHub
            for batch in (subtitleValue?.batches ?? []) where !subtitles.contains(where: { $0.id == batch.id }) {
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
            chatJobs = chats.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
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
            transcriptionDefaultModel = transcriptionValue?.defaultModel
            reconcileSelections(defaultTranscription: transcriptionValue?.defaultModel)
            await reconcileTranscription()
            isReachable = true
            if reportErrors { error = nil }
            await retryStartupMembershipIfNeeded()
        } catch {
            guard connectionID == requestConnection else { return }
            // A cancelled refresh is not a Hub that went away. Marking it unreachable here would
            // put every page's controls into their "start the local services" state over a read the
            // app itself cancelled.
            guard !Task.isCancelled, !Self.isCancellation(error) else { return }
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
                // A family that takes no language input must not be sent one. Most of them do not:
                // the lane omits `language` precisely because it discards the key - the Khmer VITS
                // voices are Khmer by construction - and the app sent the field's value on every
                // job anyway, which is the "do not offer what the lane discards" rule read from the
                // other side. A lane that *requires* a language is a different case and is refused
                // below rather than filled in with a code this app invented.
                let requestedLanguage = audioLanguageControl == nil ? nil : (language.isEmpty ? nil : language)
                if audioLanguageControl?.required == true, requestedLanguage == nil {
                    throw NativeAPIError.message("\(model.label) needs a language code. Enter one, for example en.")
                }
                // Which form this request takes is the tab the operator is on, and the tab is one
                // the model publishes. A model that clones *and* designs has two valid forms, so
                // neither the model's first mode nor a guess may decide this.
                guard model.reportsVoiceModes else {
                    throw NativeAPIError.message("The local engine has not reported which voice modes \(model.label) supports. Update it, or choose another model.")
                }
                guard model.voiceModes.contains(audioMode) else {
                    throw NativeAPIError.message("\(model.label) does not publish the \(audioMode.shortTitle) mode. Choose a mode it publishes.")
                }
                if audioMode == .design {
                    let description = audioDesignPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !description.isEmpty else {
                        throw NativeAPIError.message("Describe the voice you want — for example 'deep, slow and warm'.")
                    }
                    body = HubAudioRequest(
                        modality: "voice", model: model.repo,
                        clientRequestId: "native:\(UUID().uuidString)",
                        items: [HubAudioItem(text: text, params: HubAudioParams(
                            presetSpeaker: nil, language: requestedLanguage, speed: audioSpeedForRequest,
                            designPrompt: description))],
                        voiceReferenceAssetId: nil, voiceLibraryId: nil, routing: routingPin)
                } else if audioMode == .cloning {
                    if let voiceID = audioVoiceLibraryID {
                        // A voice from the library: the worker reads the clip, its transcript and
                        // its timings from its own store. Nothing is uploaded, and there is no
                        // reference to prepare first.
                        guard let voice = voices.first(where: { $0.id == voiceID }) else {
                            throw NativeAPIError.message("That saved voice is no longer in the voice library. Choose another, or refresh the Voices page.")
                        }
                        guard voice.readyForCloning else {
                            throw NativeAPIError.message("\(voice.name) has no transcript yet, so a clone cannot align its audio. Add the words on the Voices page, or choose another voice.")
                        }
                        body = HubAudioRequest(
                            modality: "voice", model: model.repo,
                            clientRequestId: "native:\(UUID().uuidString)",
                            items: [HubAudioItem(text: text, params: HubAudioParams(
                                presetSpeaker: nil, language: requestedLanguage, speed: audioSpeedForRequest))],
                            voiceReferenceAssetId: nil, voiceLibraryId: voiceID, routing: routingPin)
                    } else {
                        // The staged-reference path: kept for a reference that arrives without a
                        // library id. The Audio page's Clone tab no longer has a way to reach it -
                        // cloning there is library-only - so the first thing to say is still which
                        // voice to choose, never a clip to pick.
                        guard audioReferencePath != nil else {
                            throw NativeAPIError.message("Choose a saved voice for this clone.")
                        }
                        guard !model.requiresTranscript || !audioReferenceTranscript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                            throw NativeAPIError.message("This voice model needs the exact words spoken in the reference clip.")
                        }
                        guard let assetID = audioReferenceAssetId else {
                            throw NativeAPIError.message("Prepare the reference voice before generating.")
                        }
                        body = HubAudioRequest(
                            modality: "voice", model: model.repo,
                            clientRequestId: "native:\(UUID().uuidString)",
                            items: [HubAudioItem(text: text, params: HubAudioParams(
                                presetSpeaker: nil, language: requestedLanguage, speed: audioSpeedForRequest))],
                            voiceReferenceAssetId: assetID, voiceLibraryId: nil, routing: routingPin)
                    }
                } else {
                    // Only a speaker the model's own runtime published may be sent. A preset model
                    // that published no roster — Khmer VITS has a single built-in voice — is asked
                    // for nothing rather than being handed another family's speaker names, and an
                    // operator who has not chosen a voice sends none either: that is what lets this
                    // model's stored voice apply.
                    guard audioSpeaker.isEmpty || model.speakers.isEmpty
                            || model.speakers.contains(audioSpeaker) else {
                        throw NativeAPIError.message("Choose a speaker reported by this model's local runtime.")
                    }
                    body = HubAudioRequest(
                        modality: "voice", model: model.repo,
                        clientRequestId: "native:\(UUID().uuidString)",
                        items: [HubAudioItem(text: text, params: HubAudioParams(
                            presetSpeaker: audioSpeakerForRequest,
                            language: requestedLanguage, speed: audioSpeedForRequest))],
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
        // What the Image worker publishes, which is already the resolved set: the operator's saved
        // default for this model over the model's own.
        imageSettings = imageConfiguration?.defaults
    }

    /// Send the current message to the local Chat worker and append the reply.
    ///
    /// The operator's own turn goes into the transcript *before* the request leaves. It used to be
    /// appended after the reply arrived, so for the whole time the worker was thinking the pane
    /// still read "No messages yet" and the message the operator had just sent was nowhere on
    /// screen - while the editor kept the text, which made a slow worker look like a dead button.
    ///
    /// Every mutation is looked up by the turn's own id, so a completion that lands after the
    /// transcript has moved on cannot append a second copy of anything or attach a reply to
    /// somebody else's prompt; and the `submittingChat` gate stays, so a second send while one is
    /// in flight is refused rather than queued behind it.
    func sendChat() async {
        guard !submittingChat, let requestConnection = connectionID else { return }
        let prompt = chatPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else { error = "Enter a message."; return }
        let turn = NativeChatTurn(id: UUID().uuidString, role: "user", content: prompt)
        chatTurns.append(turn)
        chatPrompt = ""
        submittingChat = true
        defer { if connectionID == requestConnection { submittingChat = false } }
        do {
            // The transcript *is* the conversation, and the turn just appended is the message being
            // sent, so this is the same body the Hub has always been given. `failure` never reaches
            // the wire: a marked turn is still a real message from the operator, sent as one.
            let messages = chatTurns.map { HubChatMessage(role: $0.role, content: $0.content) }
            let reply: HubChatCompletion = try await sendHubJSON(
                ["api", "hub", "chat", "completions"],
                // The model the operator chose, by the id the worker itself publishes. Sending an
                // empty string meant the worker fell back to whatever it was started with, so the
                // picker above Send chose nothing and the Hub had no model to gate or dispatch by.
                // With nothing selected the field is left out entirely, which is the same request
                // this page has always made - never a name the worker would refuse.
                body: HubChatRequest(model: chatModel.isEmpty ? nil : chatModel,
                                     messages: messages, stream: false))
            guard connectionID == requestConnection else {
                // The transcript outlived the connection it was sent on, so this reply may be an
                // answer from a Hub the app is no longer talking to. Say so rather than dropping
                // it silently or filing it under a transcript it does not belong to.
                markChatTurnFailed(turn.id, "No reply. The connection to the local Hub ended before an answer arrived.")
                return
            }
            guard let content = reply.choices.first?.message.content, !content.isEmpty else {
                throw NativeAPIError.message("The local Chat worker returned no message.")
            }
            guard let answered = chatTurns.firstIndex(where: { $0.id == turn.id }) else { return }
            // The reply's own stated facts travel with it: its token count and the worker's own
            // generation time. Nothing is timed here — the app's wall clock would include the
            // model load and the network, which is not the model's speed.
            chatTurns.insert(NativeChatTurn(id: UUID().uuidString, role: "assistant", content: content,
                                            completionTokens: reply.completionTokens,
                                            statedGenerationSeconds: reply.statedGenerationSeconds),
                             at: answered + 1)
            error = nil
        } catch {
            // The transcript tells the truth about what happened: the attempt stays, marked as one
            // that got no reply, and nothing is invented to fill the gap. The page banner is only
            // for the live connection's own failures - a cancelled send is not the Hub breaking.
            markChatTurnFailed(turn.id, "No reply. \(Self.describe(error))")
            if connectionID == requestConnection, !Self.isCancellation(error) {
                self.error = Self.actionable(error, action: "Chat")
            }
        }
    }

    /// Mark the operator's turn as one that got no reply, by id.
    ///
    /// Idempotent by construction: a turn that is no longer in the transcript (the operator cleared
    /// it) is left alone, and nothing is ever appended, so a late completion cannot double-append.
    private func markChatTurnFailed(_ id: String, _ reason: String) {
        guard let index = chatTurns.firstIndex(where: { $0.id == id }) else { return }
        chatTurns[index].failure = reason
    }

    func clearChat() {
        chatTurns.removeAll()
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

    /// Read the Image worker's loading-size setting for this Mac, through the Hub's generic
    /// studio proxy exactly like every other Image call.
    func refreshImageLoadingMode() async {
        guard let requestConnection = connectionID else { return }
        do {
            let status: NativeImageLoadingModeStatus = try await get(
                ["studio", "image", "api", "loading-mode"])
            guard connectionID == requestConnection else { return }
            imageLoadingMode = status
        } catch {
            // An Image worker without this route must not break the page: the catalogue row still
            // says whether the model offers a control. The reason is kept off the shared banner
            // because a page that has the catalogue answer is not broken by this read failing.
            guard connectionID == requestConnection else { return }
            imageLoadingMode = nil
        }
    }

    /// Choose the Image worker's loading mode for everything it will do on this Mac.
    ///
    /// The PUT answers with the same payload as the GET, so the new `effective` is on screen at
    /// once; the catalogue is then refreshed so the model rows carry the new selection too. The
    /// choice is never assumed here: a refused mode leaves the published one in place and says
    /// what the worker answered.
    func setImageLoadingMode(_ mode: String) async {
        guard let requestConnection = connectionID, !mode.isEmpty else { return }
        updatingImageLoadingMode = true
        defer { if connectionID == requestConnection { updatingImageLoadingMode = false } }
        do {
            let status: NativeImageLoadingModeStatus = try await sendJSON(
                ["studio", "image", "api", "loading-mode"], method: "PUT",
                body: ImageLoadingModeBody(mode: mode))
            guard connectionID == requestConnection else { return }
            imageLoadingMode = status
            error = nil
            await refresh()
        } catch {
            if connectionID == requestConnection {
                self.error = Self.actionable(error, action: "Change the image loading mode")
            }
        }
    }

    // MARK: - Per-model defaults (`/studio/<modality>/api/model-defaults`)

    /// The key one model's published defaults are held under.
    ///
    /// The modality is the model's own lane contract's, which is the same string the Studio proxy
    /// is addressed with — so a new Studio needs no entry anywhere in this app.
    static func modelDefaultsKey(modality: String, repo: String) -> String { "\(modality)/\(repo)" }

    func modelDefaultsKey(for model: NativeModel) -> String? {
        guard let modality = model.laneContract?.modality, !modality.isEmpty else { return nil }
        return Self.modelDefaultsKey(modality: modality, repo: model.repo)
    }

    /// Every modality the loaded catalogue publishes a lane contract for, in a stable order.
    ///
    /// Derived from the models themselves rather than from a list of Studios: a Studio that starts
    /// publishing contracts is read without an edit, and one that publishes none is not called.
    var modelDefaultModalities: [String] {
        var seen: [String] = []
        for model in models {
            guard let modality = model.laneContract?.modality, !modality.isEmpty,
                  !seen.contains(modality) else { continue }
            seen.append(modality)
        }
        return seen.sorted()
    }

    /// Read every Studio's default surface, once per connection.
    ///
    /// Called when the Models tab appears rather than on the two-second poll: it is one request per
    /// Studio and nothing else on screen depends on it.
    func loadModelDefaultsIfNeeded() async {
        guard connectionID != nil else { return }
        modelDefaultsLoaded = true
        for modality in modelDefaultModalities { await readModelDefaults(modality: modality) }
    }

    /// Read one model's Studio defaults, once per connection.
    ///
    /// A generator page calls this so its per-run controls open on the operator's saved default
    /// rather than on the model's own. Reading is keyed on the modality, so several pages asking
    /// for the same Studio make one request.
    func loadModelDefaultsIfNeeded(for model: NativeModel?) async {
        guard let modality = model?.laneContract?.modality, !modality.isEmpty else { return }
        await readModelDefaults(modality: modality)
    }

    private func readModelDefaults(modality: String) async {
        guard connectionID != nil, !modelDefaultsReadModalities.contains(modality) else { return }
        modelDefaultsReadModalities.insert(modality)
        await refreshModelDefaults(modality: modality)
    }

    func refreshModelDefaults(modality: String) async {
        guard let requestConnection = connectionID, !modality.isEmpty else { return }
        do {
            let payload: NativeModelDefaultsPayload = try await get(
                ["studio", modality, "api", "model-defaults"])
            guard connectionID == requestConnection else { return }
            mergeModelDefaults(payload, modality: modality)
        } catch {
            guard connectionID == requestConnection, !Self.isCancellation(error) else { return }
            // A Studio that does not publish this route must not break the page: the facts still
            // render from the lane contract, and the editor says what is missing. The shared error
            // banner is left alone because the rest of the tab is not broken by this read.
            modelDefaultsNote = "\(modality.capitalized) Studio did not publish its defaults route "
                + "(GET /studio/\(modality)/api/model-defaults): \(Self.describe(error))"
        }
    }

    /// Save one model's defaults, wholesale, and take the Studio's own answer as the result.
    ///
    /// A refused value comes back as 422 with a reason per parameter; those reasons are kept
    /// against their parameters so the editor can show each one under its own field.
    func saveModelDefaults(for model: NativeModel, values: [String: NativeJSONValue]) async {
        guard let modality = model.laneContract?.modality, let requestConnection = connectionID,
              let key = modelDefaultsKey(for: model) else { return }
        savingModelDefaults = true
        defer { if connectionID == requestConnection { savingModelDefaults = false } }
        do {
            let payload: NativeModelDefaultsPayload = try await sendJSON(
                ["studio", modality, "api", "model-defaults"], method: "PUT",
                body: ModelDefaultsBody(repo: model.repo, values: values))
            guard connectionID == requestConnection else { return }
            mergeModelDefaults(payload, modality: modality)
            modelDefaultRefusals[key] = nil
            modelDefaultsNote = "Saved your defaults for \(model.label)."
            error = nil
            // The catalogue republishes these parameters resolved, so reading it again is how the
            // generator page comes to open on the value that was just saved.
            await refresh()
        } catch NativeAPIError.refusals(let reasons) {
            guard connectionID == requestConnection else { return }
            // Every refusal at once, each against its own parameter. The values stay on screen so
            // the operator can fix the one that was refused instead of retyping the rest.
            modelDefaultRefusals[key] = reasons
            let named = reasons.keys.sorted().joined(separator: ", ")
            modelDefaultsNote = "\(model.label) refused \(reasons.count) value\(reasons.count == 1 ? "" : "s"): \(named)."
            error = nil
        } catch {
            guard connectionID == requestConnection, !Self.isCancellation(error) else { return }
            self.error = Self.actionable(error, action: "Save defaults for \(model.label)")
        }
    }

    /// Forget one model's stored defaults (the DELETE), leaving it on what the lane states.
    func restoreModelDefaults(for model: NativeModel) async {
        guard let modality = model.laneContract?.modality, let requestConnection = connectionID,
              let key = modelDefaultsKey(for: model) else { return }
        savingModelDefaults = true
        defer { if connectionID == requestConnection { savingModelDefaults = false } }
        do {
            let payload: NativeModelDefaultsPayload = try await sendJSON(
                ["studio", modality, "api", "model-defaults"], method: "DELETE",
                body: ModelDefaultsRepoBody(repo: model.repo))
            guard connectionID == requestConnection else { return }
            mergeModelDefaults(payload, modality: modality)
            modelDefaultRefusals[key] = nil
            modelDefaultsNote = "Restored \(model.label) to what its Studio states."
            error = nil
            await refresh()
        } catch {
            guard connectionID == requestConnection, !Self.isCancellation(error) else { return }
            self.error = Self.actionable(error, action: "Restore defaults for \(model.label)")
        }
    }

    private func mergeModelDefaults(_ payload: NativeModelDefaultsPayload, modality: String) {
        modelDefaultsReadModalities.insert(modality)
        for row in payload.models {
            modelDefaults[Self.modelDefaultsKey(modality: modality, repo: row.repo)] = row
        }
        if let file = payload.settingsFile, !file.isEmpty { modelDefaultsSettingsFile = file }
        modelDefaultsRevision += 1
        // Nothing on the generator pages is seeded from here: they seed from the catalogue's own
        // resolved values, which is the one path a job's parameters can come from. This read is
        // for the editor — what is settable, stated, chosen and no longer accepted.
    }

    /// The Models tab, one section per Studio in the app's own Studio list.
    ///
    /// The Studios come from `StudioFunction.allCases` — the same list the sidebar, the Jobs tabs
    /// and the status line are built from — so a Studio added to the app gets a section without an
    /// edit here, and no Studio can be left off the screen by a list kept separately. The order is
    /// the enum's own. A search drops empty sections (it is a question about models); without one,
    /// a Studio with nothing to show keeps its header and reports a count of zero.
    func modelSections(search: String, installedOnly: Bool) -> [NativeModelSection] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        return StudioFunction.allCases.compactMap { function in
            let matching = models.filter { model in
                model.function == function
                    && (!installedOnly || model.cached)
                    && (query.isEmpty
                        || model.label.localizedCaseInsensitiveContains(query)
                        || model.repo.localizedCaseInsensitiveContains(query))
            }
            guard query.isEmpty || !matching.isEmpty else { return nil }
            return NativeModelSection(function: function, models: matching)
        }
    }

    /// The controls the editor renders for one model: one per parameter the Studio published as
    /// settable, in the Studio's order, with the Studio's own bounds.
    ///
    /// A `required` parameter is excluded even if a worker publishes it in this list: it is
    /// per-request content, and a stored default for one would answer with something nobody wrote
    /// this time. Nothing else is filtered — a parameter whose type this editor cannot render is
    /// shown, with the lane's note, rather than hidden or faked.
    func modelDefaultControls(for model: NativeModel) -> [NativeModelDefaultControl] {
        guard let key = modelDefaultsKey(for: model), let row = modelDefaults[key] else { return [] }
        return Self.modelDefaultControls(row)
    }

    static func modelDefaultControls(_ row: NativeModelDefaults) -> [NativeModelDefaultControl] {
        row.parameters.filter { $0.required != true }.map { parameter in
            let type = parameter.type.lowercased()
            let kind: NativeModelDefaultControl.Kind
            if let options = parameter.options, !options.isEmpty {
                kind = .choice(options)
            } else {
                switch type {
                case "integer": kind = .integer
                case "number": kind = .number
                case "boolean": kind = .toggle
                case "string": kind = .text
                default: kind = .unrenderable
                }
            }
            return NativeModelDefaultControl(
                name: parameter.name,
                kind: kind,
                minimum: parameter.minimum,
                maximum: parameter.maximum,
                minimumExclusive: parameter.minimumExclusive == true,
                maximumExclusive: parameter.maximumExclusive == true,
                step: type == "integer" ? 1 : nil,
                maxCharacters: parameter.maxCharacters,
                maxBytes: parameter.maxBytes,
                typeName: type,
                ladder: parameter.ladder,
                note: parameter.note,
                ignored: parameter.isDeclaredIgnored,
                value: row.defaults[parameter.name],
                stated: row.stated[parameter.name],
                chosen: row.chosen[parameter.name] != nil,
                boundSource: parameter.boundSource)
        }
    }

    /// The reasons a PUT was refused with, against the parameters they belong to.
    func modelDefaultRefusals(for model: NativeModel) -> [String: String] {
        guard let key = modelDefaultsKey(for: model) else { return [:] }
        return modelDefaultRefusals[key] ?? [:]
    }

    /// The stored values this model's lane now refuses, each with the lane's own reason, in a
    /// stable order. They are shown rather than dropped: the operator's intent is not lost because
    /// a model moved.
    func modelDefaultsNotUsed(for model: NativeModel) -> [String] {
        guard let key = modelDefaultsKey(for: model), let row = modelDefaults[key] else { return [] }
        return row.notUsed.sorted { $0.key < $1.key }.map { "\($0.key) — \($0.value)" }
    }

    /// The value in force for one published parameter, by the lane's own name for it.
    func savedDefault(_ name: String, for model: NativeModel) -> NativeJSONValue? {
        guard let key = modelDefaultsKey(for: model) else { return nil }
        return modelDefaults[key]?.defaults[name]
    }

    /// The per-run controls as the catalogue's own resolved values.
    ///
    /// The Image and Music workers publish `generation_defaults` **resolved**: the operator's
    /// saved default over the model's own, the same answer the job would get if the parameter were
    /// omitted. So the generated page seeds from the catalogue and there is no second read path —
    /// a saved default reaches a job because the form's own starting value is the resolved one.
    ///
    /// A save in the Models tab changes that catalogue row, and the page has to adopt the change
    /// without a relaunch. It does so here, and only when the page is still holding the value it
    /// was seeded with: a poll must never overwrite something the operator typed.
    private func adoptResolvedDefaults() {
        for (repo, configuration) in imageConfigurations {
            let published = configuration.defaults
            let previous = publishedImageDefaults[repo]
            publishedImageDefaults[repo] = published
            guard let previous, previous != published else { continue }
            if repo == imageModel, imageSettings == nil || imageSettings == previous {
                imageSettings = published
            }
        }
        for (repo, configuration) in musicConfigurations {
            let published = configuration.defaults
            let previous = publishedMusicDefaults[repo]
            publishedMusicDefaults[repo] = published
            guard let previous, previous != published else { continue }
            if repo == musicModel, musicSettings == nil || musicSettings == previous {
                musicSettings = published
            }
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

    /// Submit one Video job through the same durable Hub route Image, Music and Voice use.
    ///
    /// Text-to-video only, and deliberately so: the Hub's broker takes a reference image as
    /// multipart on its own img2video door, and the surface that owns scene images is Story
    /// Studio. What this page adds is the lane the owner asked for — a local model, its tier and
    /// its own published options — not a second image picker beside Story's.
    ///
    /// The request carries only parameters the selected model's `lane_contract` publishes:
    /// `aspect_ratio`, `resolution` and `duration_seconds`, each taken from the lane's own enum.
    /// A model that publishes none of them still submits a prompt, because those are optional in
    /// every video contract this build ships.
    /// The selected video model's published switches, in the lane's own order.
    ///
    /// Read from the model's contract rather than a list in the view, so the page renders exactly
    /// what this Mac's worker will honour — including nothing at all for a lane that publishes none.
    var selectedVideoSwitches: [NativeLaneParameter] {
        supportedModels(for: .video).first { $0.repo == videoModel }?.publishedBooleanParameters ?? []
    }

    /// Drop any switch value the selected model does not publish.
    ///
    /// Called when the page opens and whenever the model changes. A value that survived a model
    /// change would be sent for a lane that never offered the choice, and the operator would have
    /// no way to see why — this is the store's invariant, not the view's, so a second caller
    /// cannot forget it.
    func snapVideoSwitchesToPublished() {
        let published = Set(selectedVideoSwitches.map(\.name))
        let pruned = videoSwitches.filter { published.contains($0.key) }
        if pruned != videoSwitches { videoSwitches = pruned }
    }

    func generateVideo() async {
        guard !submittingVideo, let requestConnection = connectionID else { return }
        do {
            let model = try usableModel(repo: videoModel, function: .video)
            let prompt = videoPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !prompt.isEmpty else { throw NativeAPIError.message("Describe the video you want.") }
            guard prompt.count <= 4_000 else { throw NativeAPIError.message("The prompt exceeds the 4,000-character limit.") }
            var params: [String: NativeJSONValue] = [:]
            if !videoAspectRatio.isEmpty { params["aspect_ratio"] = .string(videoAspectRatio) }
            if !videoResolution.isEmpty { params["resolution"] = .string(videoResolution) }
            if videoDurationSeconds > 0 { params["duration"] = .integer(videoDurationSeconds) }
            // The published switches this model honours, and only those: a value for a switch it
            // does not publish is dropped here rather than sent for the lane to ignore or refuse.
            // An untouched switch is absent, so the lane's own default stands — never a value
            // this app decided on the operator's behalf.
            for parameter in model.publishedBooleanParameters {
                guard let choice = videoSwitches[parameter.name] else { continue }
                params[parameter.name] = .bool(choice)
            }
            let body = HubVideoRequest(
                modality: "video", model: model.repo,
                clientRequestId: "native:\(UUID().uuidString)",
                items: [HubVideoItem(prompt: prompt, params: params)],
                routing: routingPin)
            submittingVideo = true
            defer { if connectionID == requestConnection { submittingVideo = false } }
            let response: HubJobSubmit = try await sendHubJSON(["api", "hub", "jobs"], body: body)
            guard connectionID == requestConnection else { return }
            let detail: HubJobDetail = try await get(["api", "hub", "jobs", response.batchId])
            guard connectionID == requestConnection else { return }
            let job = Self.mapHubJob(detail)
            videoJobs.removeAll { $0.id == job.id }; videoJobs.insert(job, at: 0)
            videoNote = job.state == "uncertain"
                ? "The Video result is uncertain. The Hub keeps the original attempt; reconcile it rather than resubmitting."
                : nil
            error = nil
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Generate video") }
        }
    }

    func cancelVideo(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard videoJobs.contains(where: { $0.id == job.id }) else {
                throw NativeAPIError.message("That video job is no longer in the current history.")
            }
            let _: HubCancelResponse = try await sendEmpty(["api", "hub", "jobs", job.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            await refresh()
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Cancel the video job") }
        }
    }

    /// Check only the original uncertain Video job; never submit a replacement.
    func reconcileVideo(_ job: NativeJob) async {
        guard let requestConnection = connectionID else { return }
        do {
            guard videoJobs.contains(where: { $0.id == job.id && $0.state == "uncertain" }) else {
                throw NativeAPIError.message("Refresh the original uncertain Video job before checking its result.")
            }
            let result: HubReconciliation = try await sendEmpty(
                ["api", "hub", "jobs", job.id, "items", "0", "reconcile"], method: "POST")
            guard connectionID == requestConnection else { return }
            await refresh()
            guard connectionID == requestConnection else { return }
            videoNote = result.reconciled
                ? nil
                : "The original Video result is still uncertain. Its request remains protected; no new generation was submitted."
        } catch {
            if connectionID == requestConnection { self.error = Self.actionable(error, action: "Check the original Video result") }
        }
    }

    /// Read a finished clip's bytes from the Hub, verifying them against its receipt first.
    ///
    /// The same order every other artifact takes: the stored receipt is checked before the fetch,
    /// the fetched bytes are hashed against it after, and only then may anything be written. The
    /// MP4 check is the video equivalent of the PNG signature: bytes 4–8 of an ISO base media
    /// file are `ftyp`, and a body that is not one is not a clip.
    func videoData(for job: NativeJob) async throws -> Data {
        guard let requestConnection = connectionID else {
            throw NativeAPIError.message("Start the local services before retrieving video.")
        }
        guard job.state == "done", videoJobs.contains(where: { $0.id == job.id }),
              let bytes = job.bytes, bytes > 0, let sha = job.sha256,
              sha.count == 64, sha.allSatisfy({ $0.isHexDigit }) else {
            throw NativeAPIError.message("This clip has no verified completed result. Refresh its job before saving.")
        }
        let data = try await performData(
            makeRequest(["api", "hub", "jobs", job.id, "items", "0", "artifact"]))
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The Hub connection changed before video retrieval completed.")
        }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard Int64(data.count) == bytes, digest == sha.lowercased(),
              data.count > 12, data[4..<8] == Data("ftyp".utf8) else {
            throw NativeAPIError.message("Video verification failed. Refresh the job and retrieve its original result again.")
        }
        return data
    }

    /// Save a finished clip, verify it on disk, then acknowledge delivery.
    func saveVideo(_ job: NativeJob, to destination: URL) async throws {
        let requestConnection = connectionID
        let data = try await videoData(for: job)
        try data.write(to: destination, options: .atomic)
        guard try Data(contentsOf: destination) == data else {
            throw NativeAPIError.message("Saved video verification failed. Choose another destination and save again.")
        }
        guard connectionID == requestConnection else {
            throw NativeAPIError.message("The video was saved and verified, but the local service connection changed before delivery was acknowledged. Save again to confirm delivery.")
        }
        do {
            let receipt: HubAckReceipt = try await sendEmpty(
                ["api", "hub", "jobs", job.id, "items", "0", "ack"], method: "POST")
            if !receipt.ok { throw NativeAPIError.message("rejected") }
        } catch {
            throw NativeAPIError.message("The video was saved and verified, but its delivery was not acknowledged. Save again to confirm delivery.")
        }
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

    func discardVideo(_ job: NativeJob) async { await discard(job, action: "Discard the video attempt") }

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

    /// A file inside the app's own Playback folder, created on demand.
    ///
    /// A view that hands an artifact to AVFoundation needs a URL rather than bytes, and the folder
    /// belongs to the app — never `/tmp`, and never a path a view invents for itself.
    func playbackFile(named name: String) throws -> URL {
        guard let directory = dataDirectory else {
            throw NativeAPIError.message("The app data directory is unavailable.")
        }
        let folder = directory.appendingPathComponent("Playback", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        return folder.appendingPathComponent(name)
    }

    /// Hand a finished music clip to the shared preview player.
    func playMusic(_ job: NativeJob, with player: PreviewPlayer) async throws {
        let data = try await musicData(for: job)
        guard let directory = dataDirectory else {
            throw NativeAPIError.message("The app data directory is unavailable.")
        }
        try player.play(data, extension: Self.audioExtension(of: data), in: directory)
    }

    /// Show a finished job's file where it actually is.
    ///
    /// The operator's question was *"where is it?"* — the app could play, save and preview a
    /// result but never point at the file, so the only way to find a generated clip was to know
    /// the worker's own output directory. The Hub records the worker's `output_path` on the item
    /// and this reveals exactly that, selected in its folder.
    ///
    /// **A file that is not here is not revealed.** The path is checked first, because a job whose
    /// worker sits on another Mac has no local file and Finder would open on nothing. The row's
    /// button is disabled in that case, and this returns false so a caller can say why.
    @discardableResult
    func revealArtifact(_ job: NativeJob) -> Bool {
        guard let path = job.artifactPath, !path.isEmpty,
              FileManager.default.fileExists(atPath: path) else { return false }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        return true
    }

    /// Read the saved voices from this Mac's Voice worker.
    ///
    /// A failure here must not break the rest of the workspace: an older worker without the
    /// voice library leaves the list empty and says so, rather than making the Voice page
    /// unusable for generation.
    ///
    /// **A cancelled read is not a failed read.** The Audio page's Clone tab reads this library
    /// from a `.task(id:)`, and SwiftUI cancels that task when its id changes or the page goes
    /// away. `URLSession` reports that cancellation as `URLError.cancelled` (-999) — not as a
    /// `CancellationError`, which is why `isCancellation` checks both — and treating it as a
    /// failure wiped the list already in memory and published "the voice library is unavailable on
    /// this build of the local engine" over a library that was sitting on disk intact. The Clone
    /// tab then arrived with an empty, disabled picker while the Voices page listed the same
    /// voices, because that page's task is keyed on the connection alone and is never cancelled
    /// this way. A cancelled read now leaves `voices`, `voicesLoaded` and `voiceError` untouched.
    func refreshVoices() async {
        guard let requestConnection = connectionID else { return }
        do {
            let value: VoiceListResponse = try await get(["studio", "voice", "api", "voices"])
            guard connectionID == requestConnection else { return }
            // Cancelled before the answer could be published: someone else's read, not ours.
            guard !Task.isCancelled else { return }
            voices = value.voices.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            voicesLoaded = true
            voiceError = nil
        } catch {
            guard connectionID == requestConnection else { return }
            guard !Task.isCancelled, !Self.isCancellation(error) else { return }
            // The read failed for a reason that is not cancellation. What is in hand is the last
            // thing this Mac was told, so it stays on screen — labelled — instead of vanishing
            // because one read did not answer. The error names the engine's own failure rather
            // than blaming this build, which is what the old fixed sentence did for every cause,
            // including a worker that was simply not running.
            voiceError = Self.actionable(error, action: "Read the voice library")
        }
    }

    /// Read the voice library once for this connection, unless it is already in hand.
    ///
    /// This is what the Audio page's Clone tab calls when it appears. Reading it on every tab
    /// change was the bug: `.task(id:)` cancels the previous read when its id changes, and the
    /// cancellation was mistaken for a broken library. The Voices page and every add, edit or
    /// remove call `refreshVoices` directly, because those are real reasons for the library to have
    /// changed; this path is only the first look.
    func loadVoicesIfNeeded() async {
        guard connectionID != nil, !voicesLoaded else { return }
        await refreshVoices()
    }

    /// Whether an error is this task being torn down rather than the engine failing.
    ///
    /// Measured, not assumed: a cancelled `URLSession.data(for:)` throws
    /// `URLError` code **-999** (`NSURLErrorCancelled`), and `error is CancellationError` is
    /// `false` for it. A check for `CancellationError` alone would still have wiped the list, so
    /// both shapes are recognised — plus the `Task.isCancelled` check at the catch site, which
    /// covers a task cancelled while its error was already on the way.
    static func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if let urlError = error as? URLError, urlError.code == .cancelled { return true }
        return false
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
        //
        // The model is the worker's own `default_model`, or the one the operator chose on the
        // Subtitle page. When neither is known the field is left out and the worker uses its own
        // default, rather than the app naming a repo it has not been told about.
        var fields = ["word_timestamps": "true"]
        if let model = transcriptionDefaultModel ?? (transcriptionModel.isEmpty ? nil : transcriptionModel) {
            fields["model"] = model
        }
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
    /// offered as a working choice rather than being offered and then refused.
    var selectableVoices: [NativeVoice] { voices.filter { $0.readyForCloning } }

    /// The saved voices that cannot be used yet, kept visible and named so an operator can see what
    /// to fix. A saved voice with no transcript is not a voice that does not exist, and filtering it
    /// out of the picker entirely is how a library of untranscribed voices reads as an empty,
    /// broken picker with nothing to act on.
    var voicesNeedingWords: [NativeVoice] { voices.filter { !$0.readyForCloning } }

    /// The library voice currently chosen, if the list still holds it.
    var selectedLibraryVoice: NativeVoice? {
        guard let audioVoiceLibraryID else { return nil }
        return voices.first { $0.id == audioVoiceLibraryID }
    }

    /// Text for the Audio page, so an empty picker explains itself instead of looking broken.
    ///
    /// The states are ordered by what the operator most needs to know. A read that failed is named
    /// first; then a read that has not finished, because an empty list at that moment means "not
    /// read yet", not "you have no voices" — the sentence that made a first visit to the Clone tab
    /// look like an empty library; then an empty library; then a library whose voices have no words.
    var voicePickerDetail: String {
        if let voiceError {
            return voices.isEmpty
                ? "\(voiceError) The saved voices cannot be listed until that read succeeds."
                : "\(voiceError) Showing the voices read earlier."
        }
        let waiting = voicesNeedingWords
        if !selectableVoices.isEmpty {
            var detail = "Clones \(selectableVoices.count) of your saved voices. The clip, its words and its timings stay in the app's own voice library."
            if !waiting.isEmpty {
                detail += " Needs a transcript before cloning: \(Self.nameList(waiting))."
            }
            return detail
        }
        // Nothing can be chosen yet: say which of the two reasons it is, and never the wrong one.
        guard voicesLoaded else {
            return "Reading your saved voices from the local engine…"
        }
        if voices.isEmpty {
            return "No saved voices yet. Add one on the Voices page, then choose it here."
        }
        return "None of your saved voices has a transcript yet, so none can be aligned: \(Self.nameList(voices)). Add the words on the Voices page."
    }

    /// Names for a sentence, so a list of voices reads as a list.
    static func nameList(_ voices: [NativeVoice]) -> String {
        voices.map(\.name).joined(separator: ", ")
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
    /// the app's own folder - so the clip is fetched once and written here. The voice is also
    /// selected in the picker: "Use voice" on the Voices page means this voice is now the one the
    /// Audio page clones from, and leaving the picker on "Choose a saved voice…" afterwards would
    /// say the choice had not been made.
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
            // Selecting the voice clears the staged-clip fields, so it is set first and the clip
            // fields are written after it. The clip still travels with the reference for the
            // prepare path; the plain "generate from the library" path addresses the voice by id.
            audioVoiceLibraryID = voice.id
            audioReferencePath = clip.path
            audioReferenceTranscript = voice.transcript ?? ""
            voiceNote = "\(voice.name) is selected for cloning on the Audio page."
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
            guard wordTimestamps != true || model.supportsWordTimestamps else {
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
            // Only the fields the operator actually answered are submitted. The Subtitle worker
            // counts a field as supplied when its key is in the form - its own words: "an explicit
            // `word_timestamps=false` is still the caller's answer and a stored `true` cannot
            // override it" - so an untouched toggle or an empty language box must send no key at
            // all, or this model's stored default can never apply. An empty language means
            // auto-detect, which is what omitting it means too, so nothing is lost by leaving it
            // out.
            let language = transcriptionLanguage.trimmingCharacters(in: .whitespacesAndNewlines)
            var fields = [
                    "model": model.repo,
                    "item_ids": identity.itemID,
                    "project": "native",
                    "episode": identity.episode
                ]
            if !language.isEmpty { fields["language"] = language }
            if let wordTimestamps { fields["word_timestamps"] = wordTimestamps ? "true" : "false" }
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
    func clearStartupNote() { startupNote = nil; recoveryNote = nil }

    /// Run one file load, and never let a damaged file stop the app.
    ///
    /// The unreadable file is moved aside rather than deleted, so an operator can send it on, and
    /// the reason is kept in `startupNote` for the banner on the main screen.
    private func loadBestEffort(_ url: URL, _ name: String, _ load: () throws -> Void) {
        do { try load() } catch {
            let aside = url.appendingPathExtension("corrupt")
            try? FileManager.default.removeItem(at: aside)
            try? FileManager.default.moveItem(at: url, to: aside)
            // A damaged local file is a different fact from a degraded Hub read, so it is kept
            // apart and composed with them rather than being erased by the next refresh.
            recoveryNote = "\(name.capitalized) could not be read, so it was set aside as \(aside.lastPathComponent) and the app started without it."
            startupNote = recoveryNote
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
            // **The rename is reported as a rename, and the identity it did not change is named.**
            // `controller_id` is what a join registers and what the controller routes by; a note
            // that only said "this Mac is now called X" left the operator to assume the fleet saw a
            // different machine.
            let identity = controllerIdentity?.settings.controllerId ?? "unchanged"
            roleNote = name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? "Name cleared; this Mac shows its hostname again. Its node identity is still \(identity)."
                : "This Mac is now called \(name.trimmingCharacters(in: .whitespacesAndNewlines)). "
                    + "Its node identity is still \(identity), so nothing re-registered."
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
            // A role change away from Node ends the periodic re-join: the Mac is no longer a node.
            if role != "agent" { cancelMembershipRejoin() }
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

    // MARK: This Mac's own node credential (A8)

    /// The credential to present on a route **this Mac's own node credential may use**: register
    /// and re-join, read the group's roster and overview, remove itself.
    ///
    /// The node credential comes first because it is the one that survives a group rotation: it
    /// names this Mac alone, so a controller that re-issued the shared Fleet token has not
    /// invalidated it. The group Fleet token is presented only by a Mac that has not been issued
    /// one yet — the no-flag-day path for a roster written before per-node credentials existed.
    private var nodeOriginCredential: String? {
        if let held = heldNodeCredential { return held }
        let token = storedMembership?.token?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (token?.isEmpty ?? true) ? nil : token
    }

    /// This Mac's own node credential, from the live value or from the membership it is persisted
    /// with. Nil means this Mac has never been issued one — the state a roster written before
    /// per-node credentials leaves it in.
    private var heldNodeCredential: String? {
        if let nodeCredential, !nodeCredential.isEmpty { return nodeCredential }
        if let held = storedMembership?.nodeCredential, !held.isEmpty { return held }
        return nil
    }

    /// The credential to present on a route **only the group Fleet token may use** — the
    /// authenticated read this app makes to its controller for the connection light, and the
    /// rotation verification the Hub itself performs. A node credential is refused there by
    /// design, so presenting it would report a permission as a fault.
    private var fleetTokenCredential: String? {
        // The live value first: the membership's copy predates any rotation this Mac did not
        // perform itself, and the Hub's own file is the authority on what it holds now.
        if let live = fleetTokenProvider?(), !live.isEmpty { return live }
        let token = storedMembership?.token?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let token, !token.isEmpty { return token }
        return nodeCredential
    }

    /// Record the credential this Mac was issued, and persist it with the membership.
    ///
    /// Called from every successful join, because **each join mints a fresh credential**: the
    /// controller keeps only the newest hash, so a Mac that kept the old value would be holding a
    /// secret its controller no longer recognises.
    private func setNodeCredential(_ value: String?) {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let clean = trimmed.isEmpty ? nil : trimmed
        guard clean != nodeCredential else { return }
        nodeCredential = clean
        // The membership is what the app persists, so the live value and the stored one are kept
        // equal here rather than by every caller remembering to do both.
        storedMembership?.nodeCredential = clean
        onNodeCredentialChanged?(clean)
    }

    /// Dismiss the removal notice. The date is the controller's decision, so dismissing it is a
    /// deliberate act rather than something the next refresh does silently.
    func dismissControllerRemoval() { controllerRemoval = nil }

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
        clearRoutingPinIfItsMachineLeft()
    }

    /// Drop a "Send here" pin whose machine is no longer in this fleet.
    ///
    /// A pin is a standing promise about a Mac, and once that Mac leaves, the promise cannot be
    /// kept: the Hub refuses every new job with "no machine named X is in this fleet" and the
    /// screen that shows the refusal had no way to clear the pin that caused it.
    ///
    /// Two callers, and the difference is the evidence. A **refresh** may only clear a pin when the
    /// roster read succeeded and no longer lists that machine — a failed read says nothing about who
    /// is in the fleet. `justRemoved` is the operator's own removal on this controller, which is
    /// conclusive straight away: the machine is gone from routing whether or not the next read
    /// succeeds.
    func clearRoutingPinIfItsMachineLeft(justRemoved machine: String? = nil) {
        guard let pin = routingPin, pin.hasPrefix("machine:") else { return }
        let pinned = String(pin.dropFirst("machine:".count)).lowercased()
        guard !pinned.isEmpty, pinned != "local",
              pinned != (controllerIdentity?.settings.controllerId ?? "").lowercased() else { return }
        if let machine {
            guard machine.lowercased() == pinned else { return }
        } else {
            // The local roster is the list of machines this Hub may send work to. It is only
            // authoritative when the read succeeded.
            guard let fleet = fleetNodes else { return }
            guard !fleet.nodes.contains(where: { $0.id.lowercased() == pinned }) else { return }
        }
        routingPin = nil
        fleetNote = "The Mac pinned with Send here (\(pinned)) is no longer in this fleet, "
            + "so the pin was cleared. New work runs on whichever Mac can take it."
    }

    /// Join this Mac to a controller at an address the operator typed.
    ///
    /// The request leaves this Mac for another one, so it does not go through the helper that
    /// targets our own Hub — and the address is validated here as well as on the controller,
    /// because a token must never be sent to a host that was not checked first.
    ///
    /// **Two credentials, and both are needed.** This Mac presents the node credential its
    /// controller issued it (A8) — the one value that names this Mac alone and therefore survives a
    /// group-wide Fleet-token rotation — and the group Fleet token is what it hands over on the
    /// first join and on the one retry that can hear a removal.
    @discardableResult
    func joinController(address: String, token: String, boundHosts: [String] = [],
                        quiet: Bool = false, startupAttemptID: UUID? = nil,
                        scheduledRejoin: Bool = false) async -> Bool {
        // A manual join is a person taking over: it cancels the pending startup attempt and the
        // backed-off loop, and starts its own from scratch. The loop's own calls skip this.
        if !scheduledRejoin {
            if startupAttemptID == nil { invalidateStartupMembershipAttempt() }
            cancelMembershipRejoin()
        }
        lastJoinAuthStatus = nil
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
            joiningFleet = true
            defer {
                if connectionID == requestConnection,
                   startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID {
                    joiningFleet = false
                }
            }
            func request(presenting credential: String) throws -> URLRequest {
                // The Hub reads snake_case here, like its other administration routes. Sending the
                // Swift names would have the controller refuse a join for a missing node_id while
                // the app reported only that it was refused.
                var attempt = URLRequest(url: target.appendingPathComponent("api/hub/fleet/nodes"))
                attempt.httpMethod = "POST"
                attempt.timeoutInterval = 30
                attempt.setValue("application/json", forHTTPHeaderField: "Content-Type")
                attempt.setValue("application/json", forHTTPHeaderField: "Accept")
                attempt.setValue(credential, forHTTPHeaderField: "X-Hub-Token")
                let encoder = JSONEncoder()
                encoder.keyEncodingStrategy = .convertToSnakeCase
                // The node credential travels in the body as well as the header: the controller
                // accepts either as proof of identity, and a request that carries its own proof is
                // the one that still parses if something in between rewrites headers.
                attempt.httpBody = try encoder.encode(JoinRequest(
                    nodeId: nodeID, name: name, address: ownAddress, studios: studios,
                    credential: heldNodeCredential))
                return attempt
            }
            /// **Two credentials, and which one goes first depends on which it is.**
            ///
            /// A Mac's **own node credential** always goes first. It names this Mac alone, so it is
            /// the value that survives a group-wide Fleet-token rotation, and a re-join presenting
            /// it is correct whether a person pressed Join or the startup loop is re-announcing
            /// this Mac. `tests/native/api_main.swift` pins that: a re-join after a rotation must
            /// present the node credential, not the rotated group token.
            ///
            /// A stored **group token** has no such privilege. It is just the last group credential
            /// this Mac was given, and it goes stale the moment it joins a different controller.
            /// `nodeOriginCredential` treated it as if it outranked the operator — so the **Join
            /// button**, whose screen says *"Enter the controller's address and its Fleet token"*,
            /// sent the stored token instead and never sent the typed one. The controller answered
            /// 401, and the retry could not save it: the retry was gated on `heldNodeCredential`,
            /// which a Mac holding a stored group token and **no** node credential does not have.
            /// That is not an edge case — it is every row written before per-node credentials
            /// existed, which is every Mac the owner is re-joining. Measured in the field on a
            /// healthy node (0.6.100, role already `agent` from an earlier group): joining a
            /// healthy controller with that controller's correct Fleet token failed every time with
            /// *"the two Macs hold different Fleet tokens"*.
            ///
            /// So: the node credential first, always; otherwise what the operator typed, when the
            /// operator is the one asking (`startupAttemptID == nil` is the Join button; the loop
            /// always passes one); otherwise the stored token.
            let operatorInitiated = startupAttemptID == nil
            let storedGroupToken = storedMembership?.token?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let own: String
            if let held = heldNodeCredential {
                own = held
            } else if operatorInitiated, !secret.isEmpty {
                own = secret
            } else if let storedGroupToken, !storedGroupToken.isEmpty {
                own = storedGroupToken
            } else {
                own = secret
            }
            var (data, response) = try await session.data(for: try request(presenting: own))
            guard connectionID == requestConnection,
                  startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
            guard var http = response as? HTTPURLResponse else {
                throw NativeAPIError.message("The controller returned no response.")
            }
            // **A revoked credential answers 401, and a 401 says nothing about why.** The removal
            // sentence is only served to a request the controller will authenticate at all, so a
            // 401 is answered with the *other* credential this Mac holds, exactly once — one retry,
            // then the answer stands. Three cases: a Mac the controller has **removed** hears a
            // decision that was already made instead of retrying for ever against a bare 401; a Mac
            // that was **re-admitted** (its row holds no credential) is let back in and issued a
            // fresh one; and the operator's typed token gets its turn even when this Mac was
            // holding something else — and the held credential gets its turn when what he typed no
            // longer works.
            if http.statusCode == 401 {
                // The retry presents the **other** credential, never the same value a second time.
                // `heldNodeCredential ?? secret` was wrong here: when the held credential is what
                // just failed, that expression offers it again and the retry is skipped — which is
                // the re-admitted-Mac case, where the node's own credential is stale and the group
                // token is the one that gets it back in. `tests/native/api_main.swift` failed on
                // exactly that ("a stale credential must be retried once with the group token").
                let alternative: String
                if let held = heldNodeCredential, held == own {
                    alternative = secret
                } else if let held = heldNodeCredential {
                    alternative = held
                } else {
                    alternative = secret
                }
                if !alternative.isEmpty, alternative != own {
                    (data, response) = try await session.data(for: try request(presenting: alternative))
                    guard connectionID == requestConnection,
                          startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
                    guard let retried = response as? HTTPURLResponse else {
                        throw NativeAPIError.message("The controller returned no response.")
                    }
                    http = retried
                }
            }
            guard (200..<300).contains(http.statusCode) else {
                lastJoinAuthStatus = (http.statusCode == 401 || http.statusCode == 403)
                    ? http.statusCode : nil
                // Quote the controller's own reason when it gives one - "studio modality must be
                // one of [...]" is far more use than "refused (400)".
                let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                let reason = (body?["detail"] as? String)?
                    .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                // **A removal is terminal.** It is a decision the controller already made, not a
                // credential this Mac can replace, so the re-join loop stops here and the page says
                // who removed this Mac and when.
                if http.statusCode == 403, FleetCopy.isRemoval(reason) {
                    await handleControllerRemoval(detail: reason, address: address, target: target)
                    return false
                }
                throw NativeAPIError.message(reason.isEmpty
                    ? (http.statusCode == 401
                        ? "The controller refused this Mac's join (401). Use the controller's Fleet token, not its Hub token."
                        : "The controller refused the join (\(http.statusCode)).")
                    : "The controller refused this Mac's join: \(reason)")
            }
            // This Mac's own credential, minted for it in this answer and returned nowhere else.
            let issued = ((try? JSONDecoder().decode(JoinAnswer.self, from: data))?.credential)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let issued, !issued.isEmpty {
                setNodeCredential(issued)
            }
            // Take on the controller's group credential, which is what registration means.
            //
            // The route existed and nothing called it, so a Mac joined a controller and then
            // answered 401 to that controller: the controller drives its nodes with its own
            // credential, and handing it over is the point of registering. The one credential
            // per cluster is the whole design, and this is the half that installs it.
            //
            // Registration first, adoption second: the controller has to accept the node before
            // the node stops accepting its own credential. The node credential goes with it, so the
            // local Hub presents *this Mac's* credential on the reads it makes itself (A8) —
            // the app never has to know where the Hub keeps its state.
            do {
                try await adoptClusterCredential(secret, controller: target, nodeCredential: issued)
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
            guard connectionID == requestConnection,
                  startupAttemptID == nil || startupMembershipAttemptID == startupAttemptID else { return false }
            // A Mac that joined is not a Mac that was removed: the notice is about the previous
            // controller's decision, and it has been overtaken by a re-admission.
            controllerRemoval = nil
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

    /// The controller has removed this Mac: stop, drop the group credential, and say so.
    ///
    /// A removal is the one refusal that must not be retried. The owner removes Macs to sell them,
    /// so a Mac that kept re-joining would look broken rather than retired; the app stops the loop
    /// and the startup attempt, tells the local Hub to mint its own credential (which is what
    /// actually takes the group's token off this Mac), and keeps the controller's own sentence on
    /// screen with the date it named.
    private func handleControllerRemoval(detail: String, address: String, target: URL) async {
        // This Mac's own credential was revoked the moment it was removed; keeping it would only
        // invite a retry the controller has already refused.
        setNodeCredential(nil)
        let date = FleetCopy.removalDate(in: detail)
        let shown = address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "\(target.host ?? ""):\(target.port ?? 0)" : address
        // **The local leave runs before the loops are cancelled.** Cancelling first would tear down
        // the very task this code is running in — the re-join loop or the startup attempt called
        // this join — and a cancelled URLSession request cannot take the group's token off this Mac.
        // The terminal state below is what stops the loop; the cancel is the belt to that braces.
        let cleared = await clearGroupMembershipLocally()
        controllerRemoval = ControllerRemoval(date: date, detail: detail, address: shown,
                                              clearedGroupCredential: cleared)
        cancelMembershipRejoin()
        invalidateStartupMembershipAttempt()
        controllerReachable = false
        controllerCredentialRefused = false
        controllerHealthNote = FleetCopy.removalNotice(date: date, address: shown)
        // A removal is a failure the operator has to act on, and it is also the answer to a join
        // they may have just pressed: both the banner and the standing line carry it.
        note(.failure, controllerHealthNote ?? detail)
        self.error = controllerHealthNote
    }

    /// Tell this Mac's own Hub to stop being a node: it mints its own credential and forgets the
    /// controller it borrowed one from. Best-effort — a Mac whose Hub is not answering has still
    /// stopped being a member here, and the caller says which of the two happened.
    @discardableResult
    private func clearGroupMembershipLocally() async -> Bool {
        var cleared = true
        do {
            var request = try makeRequest(["api", "hub", "fleet", "leave"], method: "POST")
            request.timeoutInterval = 30
            let (_, response) = try await session.data(for: request)
            cleared = (response as? HTTPURLResponse).map { (200..<300).contains($0.statusCode) } ?? false
        } catch {
            cleared = false
        }
        storedMembership = nil
        controllerAddress = ""
        // This Mac's own node credential belonged to the controller it just left, so it goes with
        // the membership: keeping it would leave a working membership secret on a Mac that is
        // being retired or sold, which is the one thing leaving exists to prevent.
        setNodeCredential(nil)
        // The Hub has minted its own Fleet token, so the value on the page is re-read rather than
        // left showing the credential of a group this Mac is no longer in.
        onFleetCredentialChanged?()
        releasePublisherKey = nil
        onReleasePublisherKeyChanged?(nil)
        offeredRelease = nil
        offeredReleaseDescriptor = nil
        updateOffered = false
        offeredReleaseIsOlder = false
        releaseRefusal = nil
        onControllerJoined?(nil)
        return cleared
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
        guard let membership = storedMembership, let token = membership.token, !token.isEmpty,
              !membership.controllerAddress.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
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
            if joined {
                self.startupMembershipAttemptID = nil
            } else {
                // The one-shot attempt is over. Hand off to the backed-off schedule so the
                // two-second lifecycle refresh does not hammer a controller that is simply down.
                self.startupMembershipAttemptID = nil
                self.scheduleMembershipRejoin()
            }
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

    /// Whether this Mac holds a stored membership it can actually re-join with. An address is
    /// required: a token with nowhere to send it is not a membership, and retrying it would be the
    /// silent spin this schedule exists to avoid.
    private var hasRejoinableMembership: Bool {
        guard let membership = storedMembership else { return false }
        return RejoinRecovery.hasRejoinableMembership(address: membership.controllerAddress,
                                                      token: membership.token)
    }

    /// Retry the controller re-join on the bounded, backed-off schedule while this Mac is a node.
    ///
    /// A node that boots before its controller used to stay out of the group until something else
    /// happened. The retries are 15s, 30s, 60s and then every five minutes, one at a time, and the
    /// loop stops when the join succeeds or this Mac stops being a node.
    private func scheduleMembershipRejoin() {
        guard rejoinTask == nil else { return }
        // **A removal is terminal.** The controller has already decided, so a Mac that was removed
        // does not start a new re-join loop — not after a failed startup attempt, and not when the
        // lifecycle refresh notices the membership is gone.
        guard controllerRemoval == nil else { return }
        guard hasRejoinableMembership,
              controllerIdentity?.settings.fleetCredentialOrigin == "adopted",
              let requestConnection = connectionID else { return }
        // The attempt that just failed is the first of this failure streak, so a 401/403 there
        // counts towards the three that earn one spoken warning.
        rejoinAttempt = 0
        rejoinAuthFailures = (lastJoinAuthStatus == 401 || lastJoinAuthStatus == 403) ? 1 : 0
        rejoinAuthAnnounced = false
        rejoinTask = Task { @MainActor [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                // The pure schedule decides whether there is anything left to do and how long to
                // wait; the loop only carries it out. Leaving the group, a role change, a removal or
                // a lost membership stops it here rather than re-enrolling a Mac that left.
                let isNode = self.controllerRemoval == nil
                    && self.controllerIdentity?.settings.fleetCredentialOrigin == "adopted"
                let hasMembership = self.hasRejoinableMembership
                guard case .wait(let delay) = RejoinRecovery.nextAction(
                    attempt: self.rejoinAttempt, isNode: isNode, hasMembership: hasMembership,
                    joined: false, delays: self.rejoinBackoffSeconds) else {
                    self.rejoinTask = nil
                    return
                }
                if delay > 0 { try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
                guard !Task.isCancelled, self.connectionID == requestConnection else { return }
                guard let membership = self.storedMembership, let token = membership.token,
                      !token.isEmpty, self.hasRejoinableMembership,
                      self.controllerIdentity?.settings.fleetCredentialOrigin == "adopted" else {
                    self.rejoinTask = nil
                    return
                }
                let joined = await self.joinController(address: membership.controllerAddress,
                                                       token: token,
                                                       boundHosts: membership.boundHosts,
                                                       quiet: true, scheduledRejoin: true)
                guard !Task.isCancelled, self.connectionID == requestConnection else { return }
                if joined {
                    self.rejoinAttempt = 0
                    self.rejoinAuthFailures = 0
                    self.rejoinAuthAnnounced = false
                    self.rejoinTask = nil
                    return
                }
                if let status = self.lastJoinAuthStatus, status == 401 || status == 403 {
                    self.rejoinAuthFailures += 1
                } else {
                    self.rejoinAuthFailures = 0
                }
                if RejoinRecovery.shouldAnnounceAuthRefusal(consecutive: self.rejoinAuthFailures,
                                                            alreadyAnnounced: self.rejoinAuthAnnounced) {
                    self.rejoinAuthAnnounced = true
                    // A refused credential is not a controller that is merely down: from here on
                    // the cadence is the slow one, so three 401s do not become a hammer.
                    self.rejoinAttempt = max(self.rejoinAttempt, self.rejoinBackoffSeconds.count - 1)
                    self.note(.failure, "The controller refused this Mac's Fleet token "
                        + "(\((self.lastJoinAuthStatus ?? 0))) on three re-join attempts. "
                        + "This Mac will keep trying every five minutes. Get a fresh Fleet token "
                        + "from the controller's Fleet page and join again.")
                }
                self.rejoinAttempt += 1
            }
        }
    }

    /// Stop the backed-off re-join and put it back at the first interval. Called when this Mac is
    /// no longer a node, when it leaves the group, or when an operator joins by hand.
    private func cancelMembershipRejoin() {
        rejoinTask?.cancel()
        rejoinTask = nil
        rejoinAttempt = 0
        rejoinAuthFailures = 0
        rejoinAuthAnnounced = false
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
        cancelMembershipRejoin()
        storedMembership = membership
        // The pinned release key travels with the membership: restore it before any release is
        // read, so the first read verifies against the key pinned at the last join.
        releasePublisherKey = membership.releasePublisherKey
        // **This Mac's node credential travels with the membership too.** It is the value that
        // names this Mac alone, so it is what the re-join presents; without it a controller whose
        // roster already holds a credential for this id refuses the join (H12), and a Mac would
        // have to be re-admitted by hand after every restart.
        setUpNodeCredentialAndPublish(membership.nodeCredential)
        startupMembershipAttemptID = UUID()
        // Read the identity before deciding: the credential origin is what says whether this Mac
        // borrowed somebody else's token and therefore belongs to a group.
        await retryStartupMembershipIfNeeded(waitForJoin: true)
    }

    /// Restore a credential that is already this Mac's, without telling the app to save it again.
    private func setUpNodeCredentialAndPublish(_ value: String?) {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        nodeCredential = trimmed.isEmpty ? nil : trimmed
    }

    /// Leave the group this Mac belongs to: stop carrying its token and run on this Mac's own.
    ///
    /// This is the action for a Mac that is being retired, sold or moved, and the only one that
    /// takes the group's credential off it. Telling the controller to drop this Mac from its roster
    /// happens first, while this Mac's own credential still works; if the controller is off or
    /// already gone, leaving is still this Mac's decision and goes ahead with a line saying so.
    ///
    /// **The removal request goes out with this Mac's own credential (A8).** It used to present the
    /// shared Fleet token, which cannot remove anything: a credential every member holds identifies
    /// nobody. The controller's answer is reported as it comes — including its own reason — because
    /// the failure this replaces was an app that said "left the group" while the controller still
    /// listed the Mac.
    func leaveGroup() async {
        guard let requestConnection = connectionID else { return }
        invalidateStartupMembershipAttempt()
        cancelMembershipRejoin()
        fleetNote = nil
        var controllerNote = ""
        let membership = storedMembership
        if let membership, let token = membership.token, !token.isEmpty,
           let nodeID = controllerIdentity?.settings.controllerId.lowercased(),
           let target = try? Self.privateHTTPAddress(membership.controllerAddress) {
            let outcome = await removeThisMacFromController(target: target, nodeID: nodeID,
                                                            groupToken: token)
            switch outcome {
            case .removed:
                controllerNote = ""
            case .removedEarlier(let detail):
                controllerNote = " The controller had already removed this Mac: \(detail)"
            case .credentialNotRecognised(let detail):
                controllerNote = " The controller did not recognise this Mac's own credential (HTTP 401), "
                    + "so this Mac is probably no longer on its roster"
                    + (detail.isEmpty ? "." : ", and the group Fleet token was refused too: \(detail)")
            case .refused(let status, let detail):
                controllerNote = detail.isEmpty
                    ? " The controller refused to remove this Mac (HTTP \(status)); remove it there."
                    : " The controller refused to remove this Mac (HTTP \(status)): \(detail)"
            case .unavailable(let reason):
                controllerNote = " The controller at \(membership.controllerAddress) answered nothing "
                    + "(\(reason)); it may still list this Mac — remove it there."
            }
        }
        guard connectionID == requestConnection else { return }
        let cleared = await clearGroupMembershipLocally()
        guard connectionID == requestConnection else { return }
        controllerRemoval = nil
        await refreshConnection()
        guard connectionID == requestConnection else { return }
        guard cleared else {
            let message = "This Mac stopped re-joining, but its own Hub did not confirm leaving, so "
                + "it may still hold the group's token. Start the local services and try again."
            note(.failure, message)
            self.error = message
            return
        }
        note(.success, "This Mac has left the group and now runs on its own credential." + controllerNote)
        error = nil
    }

    /// How the controller answered a self-removal.
    private enum SelfRemovalOutcome {
        case removed
        /// The controller had already evicted this Mac — the same decision, already taken.
        case removedEarlier(String)
        case refused(Int, String)
        /// This Mac's own credential is not recognised (401), and the group token it still holds
        /// cannot remove anything either. Both halves are named, because together they mean "this
        /// Mac is probably no longer on that roster" — which is a different sentence from "the
        /// controller refused".
        case credentialNotRecognised(String)
        case unavailable(String)
    }

    /// Ask this Mac's controller to drop it, with this Mac's own credential, once.
    ///
    /// A node credential is the only thing that may remove this Mac: the shared Fleet token
    /// identifies nobody, so a self-removal with it is refused by design. The Fleet token is used
    /// only by a Mac that has not been issued a node credential (a roster written before A8).
    private func removeThisMacFromController(target: URL, nodeID: String,
                                             groupToken: String) async -> SelfRemovalOutcome {
        let credential = heldNodeCredential ?? groupToken
        func request(_ token: String) -> URLRequest {
            var attempt = URLRequest(url: target.appendingPathComponent("api/hub/fleet/nodes/\(nodeID)"))
            attempt.httpMethod = "DELETE"
            attempt.timeoutInterval = 15
            attempt.setValue("application/json", forHTTPHeaderField: "Accept")
            attempt.setValue(token, forHTTPHeaderField: "X-Hub-Token")
            return attempt
        }
        do {
            var (data, response) = try await session.data(for: request(credential))
            guard let http = response as? HTTPURLResponse else {
                return .unavailable("no HTTP response")
            }
            // A 401 on this Mac's own credential means it is no longer on the roster. The
            // controller's *own* sentence is only served to a request it will authenticate, so a
            // stale-credential Mac retries once with the group token — which cannot remove anything
            // either, and that pair of answers is reported as what it is.
            if http.statusCode == 401, credential != groupToken {
                (data, response) = try await session.data(for: request(groupToken))
                guard let retried = response as? HTTPURLResponse else {
                    return .unavailable("no HTTP response")
                }
                switch Self.removalOutcome(status: retried.statusCode, data: data) {
                case .removed:
                    return .removedEarlier("it answered the group token and removed this Mac")
                case .refused(_, let detail):
                    return .credentialNotRecognised(detail)
                case .credentialNotRecognised(let detail), .removedEarlier(let detail):
                    return .credentialNotRecognised(detail)
                case .unavailable(let reason):
                    return .unavailable(reason)
                }
            }
            return Self.removalOutcome(status: http.statusCode, data: data)
        } catch {
            return .unavailable(Self.describe(error))
        }
    }

    private static func removalOutcome(status: Int, data: Data) -> SelfRemovalOutcome {
        let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let detail = (body?["detail"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if (200..<300).contains(status) { return .removed }
        if status == 403, FleetCopy.isRemoval(detail) { return .removedEarlier(detail) }
        return .refused(status, detail)
    }

    /// Remove a joined Mac from this controller's roster — the operator's own action, with the Hub
    /// token. Removing a machine the operator can see is a decision with a consequence (the Mac may
    /// not re-join until it is re-admitted), which is why the view confirms it first.
    func leaveNode(_ node: NativeFleetNode) async {
        guard let requestConnection = connectionID else { return }
        do {
            let _: EmptyResponse = try await sendEmpty(
                ["api", "hub", "fleet", "nodes", node.id], method: "DELETE")
            guard connectionID == requestConnection else { return }
            // A "Send here" pin on the Mac that just left can never be honoured, and the Hub would
            // refuse every new job with a sentence about a machine that is no longer listed.
            clearRoutingPinIfItsMachineLeft(justRemoved: node.id)
            if fleetNote == nil {
                fleetNote = "Removed \(node.name) from this controller. It cannot re-join until it "
                    + "is re-admitted from here."
            }
            await refreshFleetNodes()
        } catch { if connectionID == requestConnection { self.error = Self.actionable(error, action: "Remove the node") } }
    }

    /// Un-evict a machine: allow it to join this controller again. Hub token only.
    func readmitNode(_ node: NativeFleetRemovedNode) async {
        guard let requestConnection = connectionID else { return }
        do {
            let _: EmptyResponse = try await sendJSON(
                ["api", "hub", "fleet", "nodes", node.id, "readmit"], method: "POST",
                body: EmptyBody())
            guard connectionID == requestConnection else { return }
            fleetNote = "\(node.name) may join this controller again."
            error = nil
            await refreshFleetNodes()
        } catch {
            if connectionID == requestConnection {
                self.error = Self.actionable(error, action: "Re-admit the node")
            }
        }
    }

    // MARK: Group operations (A3, A4) — the Hub token, on this Mac

    /// **Rotate the group's Fleet token.** Mint a new one and push it to every joined Mac.
    ///
    /// This is the action that makes a leak undoable: the token is shown on every controller's
    /// page, held by every member and by GatewayKH, and before the route existed one holder could
    /// not be cut off without re-joining the whole fleet. It is confirmed before it runs because
    /// every member — and GatewayKH — has to be given the new value afterwards, and its result is
    /// reported per Mac: a partial push is never a success.
    func rotateFleetToken() async {
        guard !fleetOperationRunning, let requestConnection = connectionID else { return }
        fleetOperationRunning = true
        fleetOperation = nil
        defer { fleetOperationRunning = false }
        do {
            let answer: NativeFleetSyncAnswer = try await sendHubJSON(
                ["api", "hub", "fleet"], body: FleetTokenRequest(token: nil))
            guard connectionID == requestConnection else { return }
            let report = operationReport(kind: .rotation, target: nil, answer: answer)
            fleetOperation = report
            // The Hub has replaced its own Fleet token, so the app re-reads it before the page draws
            // the value beside the Copy button.
            onFleetCredentialChanged?()
            // The new Fleet token is what the Connections page shows, so read it back rather than
            // leaving the old value on screen beside a report about the new one.
            await refreshConnection()
            guard connectionID == requestConnection else { return }
            note(report.isComplete ? .success : .failure, report.summary)
            error = report.isComplete ? nil : report.summary
        } catch {
            guard connectionID == requestConnection else { return }
            let message = Self.actionable(error, action: "Rotate the Fleet token")
            note(.failure, message)
            self.error = message
        }
    }

    /// **Hand this group to a promoted node**, then retire this controller.
    ///
    /// The old controller performs it because it is the only party every node already trusts and
    /// can reach: it pushes `{new controller, new token}` down the channel it already has and then
    /// stops taking work. A Mac that is off cannot follow, and that is reported by name — the
    /// fallback for those is SSH over Tailscale, never a silent success.
    func handOverFleet(to address: String, token newToken: String) async {
        guard !fleetOperationRunning, let requestConnection = connectionID else { return }
        fleetOperationRunning = true
        fleetOperation = nil
        defer { fleetOperationRunning = false }
        do {
            let target = try Self.privateHTTPAddress(address)
            guard let literal = Self.literalControllerAddress(target) else {
                throw NativeAPIError.message("The new controller's address has to be a private or "
                    + "Tailscale address, and it could not be resolved to one the Hub will store.")
            }
            let secret = newToken.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !secret.isEmpty else {
                throw NativeAPIError.message("Enter the new controller's Fleet token.")
            }
            let answer: NativeFleetSyncAnswer = try await sendHubJSON(
                ["api", "hub", "fleet", "handover"],
                body: FleetHandoverRequest(controllerUrl: literal, token: secret))
            guard connectionID == requestConnection else { return }
            let report = operationReport(kind: .handover, target: literal, answer: answer)
            fleetOperation = report
            // This controller is retired by the handover, and its own settings say so: read them
            // back so the page shows the retirement rather than the role it had a moment ago.
            await refreshConnection()
            guard connectionID == requestConnection else { return }
            note(report.isComplete ? .success : .failure, report.summary)
            error = report.isComplete ? nil : report.summary
        } catch {
            guard connectionID == requestConnection else { return }
            let message = Self.actionable(error, action: "Hand the group over")
            note(.failure, message)
            self.error = message
        }
    }

    /// Turn the Hub's per-machine answer into the report the page shows. Built from the answer
    /// rather than from the request, so the page can never claim a Mac followed when it did not.
    private func operationReport(kind: FleetOperationReport.Kind, target: String?,
                                 answer: NativeFleetSyncAnswer) -> FleetOperationReport {
        var followed: [String] = []
        var unfinished: [String] = []
        var outcomes: [String: String] = [:]
        for (machine, row) in answer.machines.sorted(by: { $0.key < $1.key }) {
            outcomes[machine] = row.outcome
            if row.ok == true, row.status == "verified" {
                followed.append(machine)
            } else {
                unfinished.append(machine)
            }
        }
        return FleetOperationReport(kind: kind, target: answer.handedOverTo ?? target,
                                    token: answer.token, total: answer.total,
                                    verified: answer.verified, manual: answer.manual,
                                    pending: answer.pending, followed: followed,
                                    unfinished: unfinished, outcomes: outcomes)
    }

    /// The name of a machine in this controller's roster, for a result that must name Macs rather
    /// than ids. Falls back to the id the Hub used, which is still a value the operator can match.
    func fleetMachineName(_ machine: String) -> String {
        fleetNodes?.nodes.first { $0.id.lowercased() == machine.lowercased() }?.name ?? machine
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

    /// The controller's address as the Hub has to store it: a literal private IPv4, never a name.
    ///
    /// The Hub validates what it keeps, because on a node that value is what the Hub itself dials
    /// to read its own group. A name is a reasonable thing to type - MagicDNS is the natural
    /// address on a tailnet - so it is resolved here, where the lookup has already happened once
    /// to check the address was private, rather than refusing a join the operator can see is
    /// correct. Nil means "cannot be expressed as an address the Hub will accept", and the join
    /// still succeeds: a node that cannot name its controller is worse than one that can, but far
    /// better than one that refused to join.
    static func literalControllerAddress(_ target: URL) -> String? {
        guard let host = target.host, let port = target.port else { return nil }
        if let literal = ipv4(host), isPrivate(literal) {
            return "http://\(literal.map(String.init).joined(separator: ".")):\(port)"
        }
        guard let resolved = resolvedIPv4(host).first(where: isPrivate) else { return nil }
        return "http://\(resolved.map(String.init).joined(separator: ".")):\(port)"
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
    private static func isPrivate(_ parts: [Int]) -> Bool {        let first = parts[0], second = parts[1]
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
    /// Ask the controller this Mac joined whether it is answering — **with a credential**.
    ///
    /// The result of a join used to exist only as a banner at the top of the page, so an operator
    /// sitting at the join form had nothing to read: they had to scroll away from where they were
    /// working to find out whether they were connected. This is the standing answer, refreshed
    /// whenever the page is opened or a join or leave finishes.
    ///
    /// **`/api/health` is public, and that was the defect.** It answered 200 to a controller that
    /// refused this Mac's credential, so after a controller replacement every node showed green
    /// while the controller listed each one as `unauthorized` and refused to dispatch to it. The
    /// read below is the same authenticated one the controller makes to a node
    /// (`/api/hub/resources?local_only=true`), so the light reports what the dispatch path will
    /// actually meet, and a refusal is said in its own words rather than as "not answering".
    func refreshControllerHealth() async {
        let address = controllerAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !address.isEmpty else {
            controllerReachable = nil
            controllerCredentialRefused = false
            // A removed Mac has no controller left to read, and the sentence that explains why must
            // not be replaced by the flat "not joined" a lifecycle refresh would otherwise write
            // every two seconds.
            if controllerRemoval == nil {
                controllerHealthNote = "Not joined to a controller."
            }
            return
        }
        guard let target = try? Self.privateHTTPAddress(address) else {
            controllerReachable = false
            controllerCredentialRefused = false
            controllerHealthNote = "Joined to \(address), but that address cannot be used now."
            return
        }
        var components = URLComponents(url: target, resolvingAgainstBaseURL: false)
        components?.path = "/api/hub/resources"
        components?.query = "local_only=true"
        guard let url = components?.url else {
            controllerReachable = false
            controllerCredentialRefused = false
            controllerHealthNote = "Joined to \(address), but that address cannot be used now."
            return
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 8
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // The group Fleet token, because that is the credential this route checks on a node. A
        // Mac that has none presents nothing and is told so by the controller's own 401.
        if let token = fleetTokenCredential {
            request.setValue(token, forHTTPHeaderField: "X-Hub-Token")
        }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
            if (200..<300).contains(http.statusCode) {
                controllerReachable = true
                controllerCredentialRefused = false
                controllerHealthNote = "Joined to \(address) and connected. " + Self.hostFacts(data)
            } else if http.statusCode == 401 || http.statusCode == 403 {
                // The controller answered, so it is up: it refused *this Mac*. Saying "not
                // answering" here sends the operator to the network for a credential problem.
                controllerReachable = false
                controllerCredentialRefused = true
                let reason = Self.controllerRefusal(data)
                controllerHealthNote = "Joined to \(address), but it refused this Mac's credential "
                    + "(HTTP \(http.statusCode))" + (reason.isEmpty ? "." : ": \(reason)")
                    + " This Mac is not dispatchable until it re-joins with the current Fleet token."
            } else {
                controllerReachable = false
                controllerCredentialRefused = false
                controllerHealthNote = "Joined to \(address), but it answered HTTP \(http.statusCode)."
            }
        } catch {
            controllerReachable = false
            controllerCredentialRefused = false
            controllerHealthNote = "Joined to \(address), but it is not answering now. "
                + Self.describe(error)
        }
    }

    /// What the controller's own refusal said, when it sent a sentence with it.
    private static func controllerRefusal(_ data: Data) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = object["detail"] as? String else { return "" }
        return detail.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A short, honest description of the machine that answered: only what it published.
    private static func hostFacts(_ data: Data) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return "It answered the authenticated read."
        }
        var parts: [String] = []
        if let host = object["host"] as? [String: Any] {
            if let chip = host["chip"] as? String, !chip.isEmpty { parts.append(chip) }
            if let memory = host["memory_gb"] as? Double {
                parts.append(String(format: "%.0f GB memory", memory))
            } else if let memory = host["total_memory_gb"] as? Double {
                parts.append(String(format: "%.0f GB memory", memory))
            }
        }
        if let version = object["version"] as? String, !version.isEmpty {
            parts.append("Hub \(version)")
        }
        return parts.isEmpty ? "It answered the authenticated read." : parts.joined(separator: " · ")
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
            offeredReleaseIsOlder = false
            let presented = try await readReleaseIdentity()
            guard connectionID == requestConnection else { return }
            controllerReleaseIdentity = presented
            if let existing = releasePublisherKey {
                guard existing.fingerprint == presented.fingerprint else {
                    let answer = try? await readRelease()
                    guard connectionID == requestConnection else { return }
                    trustedPublisher = answer?.trustedPublisher
                    offeredRelease = answer?.descriptor?.offered
                    offeredReleaseDescriptor = answer?.descriptor
                    updateOffered = false
                    releaseRefusal = "The controller now presents a different release-signing key (\(presented.shortFingerprint)…) than the one pinned on this Mac (\(existing.shortFingerprint)…). A pinned key is only replaced by an explicit action on this Mac, so no update can be installed."
                    return
                }
            } else {
                // Pin it, then wait for the next check to verify anything against it.
                let pinned = presented.publisherKey
                releasePublisherKey = pinned
                onReleasePublisherKeyChanged?(pinned)
                let answer = try? await readRelease()
                guard connectionID == requestConnection else { return }
                trustedPublisher = answer?.trustedPublisher
                offeredRelease = answer?.descriptor?.offered
                offeredReleaseDescriptor = answer?.descriptor
                updateOffered = false
                releaseRefusal = "This Mac just pinned the controller's release-signing key (\(presented.shortFingerprint)…). Check again to verify the release against it — a key read at the same time as an artifact proves nothing."
                return
            }
            guard let pinnedKey = releasePublisherKey else { return }
            let answer = try await readRelease()
            guard connectionID == requestConnection else { return }
            trustedPublisher = answer.trustedPublisher
            guard let descriptor = answer.descriptor else {
                offeredRelease = nil
                offeredReleaseDescriptor = nil
                updateOffered = false
                releaseRefusal = nil
                return
            }
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
                offeredReleaseIsOlder = false
                releaseRefusal = "This app cannot read its own StudioHubBuild, so it cannot tell whether the offered release is different."
                return
            }
            switch ReleaseOffer.decide(advertisedVersion: descriptor.version,
                                       advertisedBuild: descriptor.build,
                                       installedVersion: currentAppVersion,
                                       installedBuild: ownBuild) {
            case .offered:
                updateOffered = true
                offeredReleaseIsOlder = false
                releaseRefusal = nil
            case .sameVersionSameBuild:
                updateOffered = false
                offeredReleaseIsOlder = false
                releaseRefusal = nil
            case .olderRelease:
                // A stale controller cannot roll this Mac backwards: the release stays visible,
                // with the reason, but there is no install action.
                updateOffered = false
                offeredReleaseIsOlder = true
                releaseRefusal = nil
            }
        } catch {
            guard connectionID == requestConnection else { return }
            updateOffered = false
            offeredReleaseIsOlder = false
            releaseRefusal = Self.actionable(error, action: "Read the update offer")
        }
    }

    /// Install or replace the trusted gateway publisher key on this controller.
    ///
    /// This is the owner's decision and is never implied by a release or run on a timer: the
    /// operator presses the button and confirms the dialog, and only then is the route sent. An
    /// empty key is refused here with a plain sentence, before any request. The publisher id is a
    /// display label and may be blank. A success refreshes what the route reports and lands in the
    /// existing Connections notice.
    func changePublisherKey(publicKey: String, publisherId: String = "") async -> Bool {
        guard let requestConnection = connectionID else {
            note(.failure, "Start the local services before changing the publisher key. Nothing was sent.")
            return false
        }
        let key = publicKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else {
            note(.failure, "Paste the gateway's Ed25519 public key before changing it — nothing was sent.")
            return false
        }
        let named = publisherId.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let requested = ReleaseTrustRequest(publicKey: key,
                                                publisherId: named.isEmpty ? nil : named)
            let result: ReleaseTrustResult = try await sendJSON(
                ["api", "hub", "release", "trust"], method: "POST", body: requested)
            guard connectionID == requestConnection else { return false }
            await refreshRelease()
            guard connectionID == requestConnection else { return false }
            let fingerprint = result.fingerprint ?? result.publisherKeyId ?? "unknown"
            let listed = result.publisherId ?? (named.isEmpty ? "no publisher name" : named)
            note(.success, "Installed the trusted gateway publisher key \(fingerprint) (\(listed)). "
                 + "This Mac now accepts app releases signed by that key.")
            return true
        } catch {
            guard connectionID == requestConnection else { return false }
            note(.failure, Self.actionable(error, action: "Change the publisher key"))
            return false
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
            currentAppVersion = descriptor.version
            updateOffered = false
            releaseRefusal = nil
            // Only a completed install makes the artifact disposable. Every failure above leaves it
            // in place, so the next press reuses or resumes it rather than re-downloading.
            try? FileManager.default.removeItem(at: artifact)
            try? FileManager.default.removeItem(at: artifact.deletingLastPathComponent())
            note(.success, "Installed Studio Hub \(descriptor.version) (\(landed)). Restarting the local services now — running work will stop.")
            onReleaseRestartRequested?()
            await refreshRelease()
        } catch {
            note(.failure, Self.actionable(error, action: "Install the update"))
            await refreshRelease()
        }
    }

    /// Download the signed artifact to a stable, private file, resuming a kept partial file when
    /// the controller's route honours Range.
    ///
    /// The file is not removed here: it stays until the install succeeds, so a failed swap does
    /// not throw away a gigabyte of transfer. It lives under this Mac's data directory, keyed by
    /// the release identity, created 0700 with the artifact itself 0600.
    private func downloadReleaseArtifact(descriptor: ReleaseDescriptor) async throws -> URL {
        guard let base = dataDirectory else {
            throw ReleaseError("This Mac has no private updates folder, so the release could not be downloaded.")
        }
        let directory = ReleaseArtifactStore.directory(base: base, descriptor: descriptor)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let destination = directory.appendingPathComponent(descriptor.filename)

        var offset = Self.artifactSize(destination)
        if offset > descriptor.bytes {
            // A file larger than the signed size cannot be a prefix of the artifact, so it is not
            // resumable and is discarded rather than trusted and appended to.
            try? FileManager.default.removeItem(at: destination)
            offset = 0
            releaseProgress = "Discarded a partial update larger than the signed \(descriptor.bytes) bytes; starting again…"
        } else if offset == descriptor.bytes {
            // A complete artifact was kept from an earlier failed install. It is reused without a
            // second transfer once it still matches the signed size and digest.
            do {
                try Self.verifyArtifactFile(destination, descriptor: descriptor)
                releaseProgress = "Using the update already downloaded — \(descriptor.offered.sizeDisplay)."
                return destination
            } catch {
                try? FileManager.default.removeItem(at: destination)
                offset = 0
                releaseProgress = "Discarded a downloaded file that did not match the signed artifact; downloading again…"
            }
        }

        if offset > 0 {
            releaseProgress = "Resuming from \(Self.sizeDisplay(offset)) of \(descriptor.offered.sizeDisplay)…"
        } else {
            releaseProgress = "Starting download of \(descriptor.offered.sizeDisplay)…"
        }

        let requestConnection = connectionID
        var request = try makeRequest(["api", "hub", "release", "artifact"])
        request.timeoutInterval = 600
        if offset > 0 { request.setValue("bytes=\(offset)-", forHTTPHeaderField: "Range") }

        let downloader = ResumableArtifactDownloader(
            configuration: sessionConfiguration,
            destination: destination,
            startingOffset: offset,
            expectedSha256: descriptor.sha256
        ) { [weak self] received in
            Task { @MainActor in
                guard let self, self.installingRelease else { return }
                self.releaseProgress = "Downloading… \(Self.sizeDisplay(received)) of \(descriptor.offered.sizeDisplay)"
            }
        }
        // On any failure the fragment stays on disk; the next press of Install resumes it.
        _ = try await downloader.run(request)
        guard connectionID == requestConnection else {
            throw ReleaseError("The local engine connection changed while the update was downloading.")
        }
        // Completeness is proved against the signed descriptor before the bytes go any further. A
        // file that is short or hashes wrong is not a resumable prefix, so it is discarded.
        do {
            try Self.verifyArtifactFile(destination, descriptor: descriptor)
        } catch {
            try? FileManager.default.removeItem(at: destination)
            throw ReleaseError("\(error.localizedDescription) The downloaded file was discarded.")
        }
        return destination
    }

    private static func artifactSize(_ url: URL) -> Int64 {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attributes[.size] as? NSNumber else { return 0 }
        return size.int64Value
    }

    private static func sizeDisplay(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
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

    private func readReleaseIdentity() async throws -> ReleaseIdentity {
        let data = try await performData(makeRequest(["api", "hub", "release", "identity"]))
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ReleaseError("The controller's release identity was not a JSON object.")
        }
        guard let algorithm = object["algorithm"] as? String, algorithm == "ed25519" else {
            throw ReleaseError("The controller signs releases with \(object["algorithm"] as? String ?? "an unknown algorithm"), not ed25519.")
        }
        guard let publicKey = (object["public_key"] as? String).flatMap(ReleaseSignature.normalizedHex),
              let fingerprint = (object["fingerprint"] as? String).flatMap(ReleaseSignature.normalizedHex),
              ReleaseSignature.fingerprint(publicKeyHex: publicKey) == fingerprint else {
            throw ReleaseError("The controller's release identity is not a 64-hex Ed25519 key whose fingerprint matches.")
        }
        return ReleaseIdentity(algorithm: algorithm, publicKey: publicKey, fingerprint: fingerprint)
    }

    /// The answer to `GET /api/hub/release`: the held descriptor (if any) plus the gateway key the
    /// owner installed. Keeping them together means one read reports both what is offered and
    /// whether this Mac could accept it at all.
    private struct ReleaseAnswer {
        let descriptor: ReleaseDescriptor?
        let trustedPublisher: TrustedPublisher?
    }

    /// Parse the additive `trusted_publisher` value. A null, or an object carrying no 64-hex
    /// fingerprint, is reported as no installed key rather than a fabricated one.
    static func parseTrustedPublisher(_ value: Any?) -> TrustedPublisher? {
        guard let object = value as? [String: Any] else { return nil }
        let publisherId = (object["publisher_id"] as? String)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .flatMap { $0.isEmpty ? nil : $0 }
        let fingerprint = (object["fingerprint"] as? String).flatMap(ReleaseSignature.normalizedHex)
        guard fingerprint != nil || publisherId != nil else { return nil }
        return TrustedPublisher(publisherId: publisherId, fingerprint: fingerprint)
    }

    private func readRelease() async throws -> ReleaseAnswer {
        let data = try await performData(makeRequest(["api", "hub", "release"]))
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let release = object["release"] else {
            throw ReleaseError("The controller's release answer was not JSON with a release field.")
        }
        let trusted = Self.parseTrustedPublisher(object["trusted_publisher"])
        if release is NSNull { return ReleaseAnswer(descriptor: nil, trustedPublisher: trusted) }
        guard let dictionary = release as? [String: Any], let descriptor = ReleaseDescriptor(json: dictionary) else {
            throw ReleaseError("The controller holds a release whose descriptor does not match the agreed schema. Refusing it.")
        }
        return ReleaseAnswer(descriptor: descriptor, trustedPublisher: trusted)
    }

    /// Show a credential without publishing it. The app already holds this token to talk to
    /// its own Hub; masking keeps it out of screenshots, support logs and the status line
    /// until the operator asks for it.
    static func maskedToken(_ token: String) -> String {
        guard token.count > 12 else { return String(repeating: "•", count: max(token.count, 8)) }
        return "\(token.prefix(4))\(String(repeating: "•", count: 8))\(token.suffix(4))"
    }

    /// A moment the Hub stamped, as a person reads it. The Hub's unit is seconds since 1970 and
    /// its own clock, which is the same Mac's clock in every case that matters here.
    static func moment(_ seconds: Double, now: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: Date(timeIntervalSince1970: seconds))
    }

    /// How long ago a stamp was, in words: "just now", "4 min ago", "3 h ago", "6 days ago".
    ///
    /// Nil means the Hub published no stamp at all, which is a different fact from "a long time
    /// ago": a node that has never answered a probe has no `last_seen_at`, and the page says
    /// "never seen" rather than inventing a date.
    static func ago(_ seconds: Double?, now: Date = Date()) -> String? {
        guard let seconds, seconds > 0 else { return nil }
        let elapsed = now.timeIntervalSince1970 - seconds
        guard elapsed >= 0 else { return "just now" }
        switch elapsed {
        case ..<45: return "just now"
        case ..<3600: return "\(Int(elapsed / 60)) min ago"
        case ..<86_400: return "\(Int(elapsed / 3600)) h ago"
        default: return "\(Int(elapsed / 86_400)) day\(Int(elapsed / 86_400) == 1 ? "" : "s") ago"
        }
    }

    /// Whether this Mac chooses where work runs — the decision behind "Send here".
    ///
    /// **Membership decides it, not the role string.** Joining a controller never sets the app's
    /// role, so `role != "agent"` was true on a Mac that had joined somebody: the page offered a
    /// pin the Hub then refused with "no machine named X is in this fleet". Holding a credential
    /// this Mac did not mint is the fact that makes it a node, and a retired controller dispatches
    /// nothing either.
    static func canPinWork(role: String?, credentialOrigin: String?,
                           handedOverTo: String?) -> Bool {
        if credentialOrigin == "adopted" { return false }
        if !(handedOverTo ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return false
        }
        guard let role else { return false }
        return role != "agent"
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

    /// Take on the cluster credential, so this Mac accepts work from its controller.
    ///
    /// Best-effort by design: a failure here leaves the node registered but unable to take work,
    /// which the controller reports as unreachable, and re-joining fixes it. Failing the join
    /// instead would be worse - the controller would have a node it cannot reach and the operator
    /// would see only that registration failed, with the credential already half adopted.
    ///
    /// The controller's address goes with the token, because adopting is the moment this Mac
    /// joins a group and the Hub has to be able to name the group afterwards. It is the Hub that
    /// needs it, not the app: the app remembers the membership in its own preferences, so a
    /// reinstall or a second window would leave the Hub holding a borrowed credential with no
    /// idea whose it was, and no way to show this Mac the pool it is in.
    private func adoptClusterCredential(_ token: String, controller: URL,
                                        nodeCredential: String?) async throws {
        var payload = ["token": token]
        if let address = Self.literalControllerAddress(controller) {
            payload["controller_url"] = address
        }
        // **This Mac's own node credential goes with the adoption.** The join answer carries it
        // once, and only this Mac may keep it; handing it straight back here is what lets the local
        // Hub present it on the reads it makes itself (A8) without the app ever having to know
        // where the Hub keeps its state.
        if let nodeCredential, !nodeCredential.isEmpty {
            payload["node_credential"] = nodeCredential
        }
        let body = try JSONEncoder().encode(payload)
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

    /// Which Studio a Hub batch belongs to, read from the batch's own `modality`.
    ///
    /// The Hub's routing table names the Voice studio's modality `voice` while this app's page and
    /// its persisted selection are `Audio`, so that one alias is the whole mapping. Nothing here is
    /// a list of the modalities that exist: a modality this build cannot place is reported by
    /// `refresh` instead of being skipped, which is what a `contains(...)` filter did.
    static func function(forModality modality: String) -> StudioFunction? {
        let value = modality.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !value.isEmpty else { return nil }
        if let exact = StudioFunction.allCases.first(where: { $0.rawValue.lowercased() == value }) {
            return exact
        }
        return value == "voice" ? .audio : nil
    }

    /// File one finished batch under the Studio its modality named.
    ///
    /// The switch is exhaustive over `StudioFunction`, so a new Studio cannot be added without
    /// answering where its jobs go. A Studio with no batch history (Chat, and Video which has no
    /// engine) has its batches named in the startup note rather than dropped in silence.
    private static func file(_ job: NativeJob, for function: StudioFunction,
                             images: inout [NativeJob], musics: inout [NativeJob],
                             history: inout [NativeJob], subtitles: inout [NativeJob],
                             chats: inout [NativeJob], videos: inout [NativeJob]) {
        switch function {
        case .image: images.append(job)
        case .music: musics.append(job)
        case .transcription: subtitles.append(job)
        case .audio: history.append(job)
        case .chat: chats.append(job)
        case .video: videos.append(job)
        }
    }

    /// The tab and the chosen speaker both follow the chosen model.
    ///
    /// A preset roster belongs to the model that published it, so without this, switching from one
    /// family's preset model to another's left the previous family's voice name selected and sent
    /// it to an engine that has never heard of it. The tab follows too, because the picker lists
    /// only the current tab's models: a model selected from elsewhere (the Models page's "Use in
    /// Audio") must not leave the page showing a list the selected model is not in.
    private func syncAudioToModel() {
        guard let model = models.first(where: { $0.repo == audioModel && $0.function == .audio })
        else { return }
        // The tab follows the model, but only as far as it has to: a mode the new model does not
        // publish cannot stay selected, and a mode it does publish is the operator's choice and is
        // left alone. Forcing the model's first mode every time would move an operator off the
        // Design tab of a model that clones *and* designs.
        if !model.voiceModes.isEmpty, !model.voiceModes.contains(audioMode) {
            audioMode = model.voiceModes[0]
        }
        if !audioSpeaker.isEmpty, !model.speakers.contains(audioSpeaker) { audioSpeaker = "" }
    }

    private func reconcileSelections(defaultTranscription: String?) {
        // The first model the Hub actually offers for the Studio, never a repo written down here.
        // "Repairing" the Audio selection to a hardcoded repo opened the page on a model that was
        // not in its own picker on a library without that model, and Generate stayed disabled
        // until the tab was changed.
        //
        // `supported` is only "the Hub ships a package for this", which every TTS entry answers
        // true, so a selection that can no longer *run* survived it indefinitely: the owner's page
        // opened on `voxcpm2-4bit`, whose folder is gone and whose receipt still says it was
        // installed. Availability is the second half of the question and it is read here — but only
        // where it changed, so an operator who picks a model to install it keeps it.
        let ordered = models.filter { $0.function == .audio }
        let nowRunnable = Dictionary(uniqueKeysWithValues: ordered.map { ($0.repo, Self.runsHere($0)) })
        let selected = ordered.first { $0.repo == audioModel }
        let becameUnavailable = selected.map { model in
            !Self.runsHere(model) && audioModelRunnable[model.repo] != false
        } ?? true
        audioModelRunnable = nowRunnable
        if selected == nil || !selected!.supported || becameUnavailable {
            audioModel = preferredAudioModel(for: audioMode)?.repo ?? ""
            if let chosen = models.first(where: { $0.repo == audioModel && $0.function == .audio }),
               !audioSpeaker.isEmpty, !chosen.speakers.contains(audioSpeaker) {
                audioSpeaker = ""
            }
        }
        // The tab has to be one of the modes the selected model publishes, and a catalogue refresh
        // can change what a model publishes (a worker update, or a model re-installed with a
        // different runtime). Re-checking the pair here keeps the tabs and the request shape in
        // step even when the model selection itself did not move.
        syncAudioToModel()
        if !models.contains(where: { $0.repo == transcriptionModel && $0.function == .transcription && $0.supported }) {
            // The worker's own `default_model` names the model it would transcribe with; the first
            // supported one is the fallback, so a library without that exact model still has a
            // selection that is really in the list.
            transcriptionModel = models.first {
                $0.repo == defaultTranscription && $0.function == .transcription && $0.supported
            }?.repo ?? models.first { $0.function == .transcription && $0.supported }?.repo ?? ""
        }
        // Clearing the choice rather than answering "off": a model that does not support word
        // timings has no business recording that the operator asked for none, and an untouched
        // toggle submits no key, so this leaves the field to the model's own default.
        if let selected = models.first(where: { $0.repo == transcriptionModel && $0.function == .transcription }),
           !selected.supportsWordTimestamps { wordTimestamps = nil }
        // A Music model that leaves the catalogue (or is not runnable here) must not stay selected,
        // or the page shows controls for a model the Hub will refuse.
        if !models.contains(where: { $0.repo == musicModel && $0.function == .music && $0.supported }) {
            musicModel = models.first { $0.function == .music && $0.supported }?.repo ?? ""
        }
        // Chat is the same rule: never keep a selected model the catalogue no longer lists as
        // supported, or the page submits work the Hub refuses.
        if !models.contains(where: { $0.repo == chatModel && $0.function == .chat && $0.supported }) {
            chatModel = models.first { $0.function == .chat && $0.supported }?.repo ?? ""
        }
    }

    /// The voice modes a catalogue row published, in the producer's own order.
    ///
    /// The Voice runtime publishes `modes` per model — `"cloning"`, `"preset"`, `"design"` — and
    /// this is the only source for which tabs the Audio page shows. The app used to work the form
    /// out for itself, from `reference_audio`/`capabilities` plus a Qwen tier name check, which is a
    /// second source of truth free to disagree with the engine that answers the request: a model
    /// that clones *and* designs (VoxCPM2 4-bit / 8-bit / bf16, OmniVoice bf16) was shown one tab
    /// and the other form could not be reached at all.
    ///
    /// A mode string this build does not know is dropped rather than guessed at; a row that
    /// published modes and none of them known therefore decodes to none, which the page says out
    /// loud instead of inventing a form. Not private: the contract test drives it directly, and a
    /// rule this easy to get wrong deserves a test rather than only a UI to exercise it through.
    static func voiceModes(_ published: [String]?) -> [VoiceMode] {
        var result: [VoiceMode] = []
        for name in published ?? [] {
            guard let mode = VoiceMode(rawValue: name), !result.contains(mode) else { continue }
            result.append(mode)
        }
        return result
    }

    /// How much of what this app reads a catalogue entry actually publishes.
    ///
    /// The Hub's aggregate catalogue carries one row per studio that lists a repo, and the same
    /// repo can arrive several times with different amounts of detail: the bare rows carry no
    /// `family` and no `capabilities`, which are exactly the fields the support and mode rules are
    /// built on. Keeping the richest row is what stops a bare duplicate from erasing the data the
    /// app needs; a tie means the rows say the same thing, so the first is kept.
    private static func catalogRichness(_ model: CatalogModel) -> Int {
        var score = 0
        if model.family?.isEmpty == false { score += 4 }
        if model.capabilities?.isEmpty == false { score += 4 }
        // A row that publishes the voice modes is the row the Audio page needs: losing them to a
        // bare duplicate of the same repo would leave a model with no tabs and no way to generate,
        // which is exactly the kind of silent loss the dedupe exists to prevent.
        if model.modes?.isEmpty == false { score += 4 }
        if model.cache != nil { score += 1 }
        if model.runtimeReady != nil { score += 1 }
        if model.referenceAudio != nil { score += 1 }
        if model.memoryEligible != nil { score += 1 }
        if model.longFormDelivery != nil { score += 1 }
        if model.imageConfiguration != nil { score += 2 }
        if model.musicConfiguration != nil { score += 2 }
        // The lane contract is the row's dispatch surface: losing it to a bare duplicate of the
        // same repo would leave that model with no facts and no editor on the Models tab.
        if model.laneContract != nil { score += 2 }
        if model.sizes?.isEmpty == false { score += 1 }
        if model.hubModality?.isEmpty == false { score += 1 }
        if model.sizeGb != nil { score += 1 }
        return score
    }

    /// How much of what the Subtitle form reads a transcription entry actually publishes.
    private static func transcriptionRichness(_ model: TranscriptionModel) -> Int {
        var score = 0
        if model.engine?.isEmpty == false { score += 4 }
        if model.cached != nil { score += 2 }
        if model.supportsWordTimestamps != nil { score += 2 }
        if model.supportsSegmentTimestamps != nil { score += 1 }
        // The lane contract and the manifest's language list are what the Subtitle facts are read
        // from, so a row carrying them wins over a bare duplicate of the same repo.
        if model.laneContract != nil { score += 2 }
        if !model.languageList.isEmpty { score += 1 }
        if model.sizeGb != nil { score += 1 }
        return score
    }

    /// One entry per repo, keeping the richest and the order of first appearance.
    ///
    /// Two rows for one repo on one machine are one model, and the consumer is the layer that has
    /// to present it that way: the Hub's aggregate is a shared contract that passes studio rows
    /// through verbatim, and a picker repeating a model three times is this app's problem to fix.
    /// Entries that map to different functions are genuinely different offerings and are deduped
    /// per function, not merged across them.
    private static func oneEntryPerRepo<T>(_ items: [T], repo: (T) -> String,
                                          richness: (T) -> Int) -> [T] {
        var best: [String: (item: T, score: Int)] = [:]
        var order: [String] = []
        for item in items {
            let key = repo(item)
            let score = richness(item)
            if let existing = best[key] {
                if score > existing.score { best[key] = (item, score) }
            } else {
                best[key] = (item, score)
                order.append(key)
            }
        }
        return order.compactMap { best[$0]?.item }
    }

    /// The audio models for one tab, best first: installed and ready, then installed, then the
    /// rest. A tab with nothing usable still shows what to install rather than an empty page.
    ///
    /// A model that publishes two modes is offered in both tabs — the tab chooses the request. A
    /// model that published no modes is offered too, in every tab: it is installed and it is real,
    /// and hiding it would answer "no model is installed" to an operator looking straight at one.
    /// What it cannot do is generate, and that is said where it is chosen.
    func audioModels(for mode: VoiceMode) -> [NativeModel] {
        models.filter { $0.function == .audio && ($0.voiceModes.contains(mode) || $0.voiceModes.isEmpty) }
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

    /// Whether this model can be generated with on this Mac right now.
    ///
    /// `supported` is only "this Hub ships a package for it", and every TTS entry in the catalogue
    /// answers true — so a rule built on `supported` alone offers models that cannot run, which is
    /// what the Audio page was opening on. `runtimeReady` is the app's collected answer from the
    /// producer's own gates: the files are verified, the runtime is compatible, the Hub would
    /// dispatch it, and it is not paused (`blockReason` is folded into it where the row is built).
    /// Generate is already disabled on it, so nothing that fails it can be presented as usable.
    static func runsHere(_ model: NativeModel) -> Bool { model.runtimeReady }

    /// Which modes the Audio page offers as tabs: the union of what this Mac's Audio models
    /// publish, **not** the modes of whichever model happens to be selected.
    ///
    /// Deriving the tabs from the selection made a closed loop with the picker: the tab decided
    /// which models the picker listed (`audioModels(for:)`), and the model decided which tabs
    /// existed (`voiceModeTabs` read `model.voiceModes`). With a cloning-capable model selected
    /// there was no Ready-Made tab, so the preset-only models were absent from the picker too, and
    /// between them every preset voice on the machine became unreachable — the owner had Khmer TTS
    /// installed and could not see it, with nothing on screen saying preset existed at all.
    ///
    /// The union is taken over models that can run here, so a tab is never offered with nothing
    /// behind it, and installing a preset model brings the tab with it. Order is the app's own
    /// `VoiceMode.allCases`, which is the vocabulary the tabs are made of and already matches the
    /// order the producers publish in (cloning, preset, design).
    var audioModes: [VoiceMode] {
        let available = models.filter { $0.function == .audio && Self.runsHere($0) }
        let published = Set(available.flatMap(\.voiceModes))
        return VoiceMode.allCases.filter { published.contains($0) }
    }

    /// The model to open on for one mode: the best one that can actually run here, or the first
    /// listed when none can — so a Mac with nothing installed still has a selection that explains
    /// itself rather than an empty picker. `audioModels(for:)` already sorts runnable models first.
    func preferredAudioModel(for mode: VoiceMode) -> NativeModel? {
        let candidates = audioModels(for: mode)
        // A model that reports no modes is listed in every tab but can generate in none of them,
        // so it is a last resort for a tab click and never the first pick.
        return candidates.first { Self.runsHere($0) && $0.reportsVoiceModes }
            ?? candidates.first(where: Self.runsHere)
            ?? candidates.first
    }

    /// Whether each Audio model could run on this Mac, as last seen.
    ///
    /// The selection is only moved off an unavailable model on a **transition** — or on first
    /// sight, which is the launch case — and never again for a model already known to be
    /// unavailable. Without the map, a Mac whose page opened on a model whose folder had been
    /// removed stayed on it, disabled, with an install error underneath; with a plain "always
    /// prefer a runnable model" rule instead, an operator who deliberately selects a model he is
    /// about to download would have it taken away on the next poll. Both are the same question
    /// asked at different times, and the answer only changes at the transition.
    private var audioModelRunnable: [String: Bool] = [:]

    /// The models this app offers for one Studio, filtered exactly as that Studio's picker
    /// filters them. The sidebar summary and the picker both read this, so the two cannot
    /// disagree about what is selectable — "Image 1" beside a picker offering eight is what a
    /// second list produced.
    func supportedModels(for function: StudioFunction) -> [NativeModel] {
        function == .audio
            ? audioModels(for: audioMode).filter { $0.supported }
            : models.filter { $0.function == function && $0.supported }
    }

    /// Every Studio's finished-job history, in the Studio list's own order, so the Jobs page and
    /// its tabs derive from `StudioFunction.allCases` instead of each keeping a copy that a new
    /// Studio would silently miss. Chat and Video have no Hub batch history
    /// (`StudioFunction.hasBatchHistory`).
    var jobHistories: [(function: StudioFunction, jobs: [NativeJob])] {
        StudioFunction.allCases.filter { $0.hasBatchHistory }.map { ($0, jobs(for: $0)) }
    }

    /// The finished batches the Hub filed for one Studio.
    func jobs(for function: StudioFunction) -> [NativeJob] {
        switch function {
        case .audio: return audioJobs
        case .image: return imageJobs
        case .music: return musicJobs
        case .transcription: return subtitleJobs
        case .chat: return chatJobs
        case .video: return videoJobs
        }
    }

    /// Point the Studio that owns this model at it — the library's "Use in …" action.
    ///
    /// The model's own `function` decides which selection is set. The button used to send every
    /// non-audio model to `transcriptionModel` and open Subtitle, so an Image or Chat model was
    /// handed to the Subtitle form.
    func select(_ model: NativeModel) {
        switch model.function {
        case .audio: audioModel = model.repo
        case .image: imageModel = model.repo
        case .music: musicModel = model.repo
        case .transcription: transcriptionModel = model.repo
        case .chat: chatModel = model.repo
        case .video: videoModel = model.repo
        }
    }

    private static func combineModels(
        _ catalog: [CatalogModel],
        _ transcription: [TranscriptionModel],
        transcriptionAvailable: Bool,
        rosters: [String: [String]],
        packages: [String: HubModelPackage]
    ) -> [NativeModel] {
        // The Hub's aggregate lists a repo once per studio that carries it, so the same model can
        // arrive three times with different amounts of detail. One row per repo, richest kept: the
        // bare rows are the ones missing `family` and `capabilities`, and a picker that shows a
        // model twice is wrong even when both rows agree.
        let transcriptionRows = Self.oneEntryPerRepo(
            transcription, repo: { $0.repo }, richness: Self.transcriptionRichness)
        // The below-tier sentence each catalogue row publishes, by repo. A Studio whose own
        // availability route carries no tier (Subtitle's) still has its catalogue row in the
        // aggregate, so the sentence reaches the model rather than being dropped on the floor.
        let catalogWarnings = catalog.reduce(into: [String: String]()) { result, item in
            if let warning = item.memoryWarning, !warning.isEmpty { result[item.repo] = warning }
        }
        // The weight files the Subtitle worker lists on its own catalogue row, by repo. The
        // availability route the transcription models come from carries the languages and the
        // timestamp claims but not the file receipt, so the weight format is read where the
        // producer actually publishes it.
        let catalogWeightFiles = catalog.reduce(into: [String: [String]]()) { result, item in
            if let files = item.readiness?.files?.weightFiles, !files.isEmpty { result[item.repo] = files }
        }
        // The RAM tier each catalogue row publishes, with the key that carried it, so the fact is
        // shown with its provenance rather than as a threshold this app re-derived.
        let catalogTiers = catalog.reduce(into: [String: (value: Double, source: String)]()) { result, item in
            if let tier = item.memoryTier { result[item.repo] = tier }
        }
        let audioModels = catalog.filter { $0.capabilities?.contains("tts") == true }.map { item in
            // What a model can do is published by the catalog, and this app reads it instead of
            // keeping its own list of families. `modes` is the runtime's own answer to "how can
            // this model be told what to sound like?"; a family allowlist here is what made six of
            // thirteen shipped voice packages invisible — khmer-tts-mms, khmer-tts-onnx,
            // chatterbox-mlx and fish-audio-mlx were dropped for not being on a list the catalog
            // had never heard of, while the Hub, the manifests and the kits all offered them.
            let modes = Self.voiceModes(item.modes)
            // The form follows the modes: cloning is the only form that needs a reference clip, and
            // its transcript is required only where the catalog's audited profile says so.
            let isClone = modes.contains(.cloning)
            let transcriptRequired = isClone
                // The transcript is required only where a **producer states that it is**. The
                // catalogue's own `reference_audio.transcript` says so for the Qwen3-TTS Base
                // tiers ('required'); where a model states nothing, the answer is "not required"
                // rather than a guess — and until this change the guess was
                // `family == "qwen3-tts"`, a hardcoded family name in a consumer, which is the
                // rule this project keeps paying for. Checked against every shipped cloning model
                // before removing it: the two that need a transcript both publish 'required', and
                // every model that fell back to the name resolves to 'optional' anyway, so this
                // changes nothing today and stops the next family having to be added here.
                && item.referenceAudio?.transcript == "required"
            let package = packages[item.repo]
            // Support is a property of the pair (catalog entry, Hub package), not of a family name:
            // every TTS entry this Hub can dispatch is offered, so nothing shipped can be hidden by
            // an app-side list. Whether it can run *now* is `ready` below — gating support on the
            // runtime would hide an installable model on a Mac whose runtime is not set up yet,
            // which is how a missing map once blanked every picker.
            let supported = package != nil
            let cached = item.cache?.state == "cached"
            let block = Self.blockReason(memoryWarning: item.memoryWarning,
                                         memoryEligible: item.memoryEligible, package: package)
            let ready = (item.runtimeReady ?? false) && block == nil
                && package?.dispatchState == "eligible"
            // Whether this exact model's contract is audit-grounded is the runtime's own published
            // answer (`execution_contract.qualification_source`), not a repo list here: a repo list
            // silently mislabelled a new quant of an audited family, and would mislabel the next one.
            let qualified = item.isAuditGrounded
            // A preset roster is published per family. Each model is lent the roster its own family
            // published — matched by the key the runtime named — and no other: a single-speaker
            // model such as Khmer VITS has no list to choose from, and lending it another family's
            // names would be inventing data. A preset family that publishes no roster at all has
            // one built-in voice.
            let roster = modes.contains(.preset) ? Self.roster(for: item, rosters: rosters) : nil
            let publishesPresetRoster = supported && modes.contains(.preset) && roster != nil
            let note = supported
                ? (cached && ready
                    ? (qualified
                        ? (isClone
                            ? "Audited for this exact model — clones from a reference clip and its exact transcript."
                            : "Audited for this exact model; its published limits come from a recorded audit.")
                        : "Published by the local Voice runtime; this exact model has not completed the native pilot test.")
                    : "Native Audio compatible; install the model and ensure its runtime is ready.")
                : "This model is outside the packaged model catalog."
            return NativeModel(
                id: "audio:\(item.repo)", repo: item.repo, label: item.label, family: item.family ?? "Audio",
                function: .audio, cached: cached, supported: supported, supportNote: note, sizeGB: item.sizeGb,
                capabilities: item.capabilities ?? [], languages: item.languages?.values ?? [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: roster ?? [], publishesPresetRoster: publishesPresetRoster,
                requiresReference: isClone,
                // The tabs the Audio page shows are the modes the producer published, in its order.
                // This used to be one derived mode per model, which showed a single tab for a model
                // that supports two and sent the request down the wrong path for the other.
                voiceModes: modes, requiresTranscript: transcriptRequired,
                // The sentence the Voice worker publishes when this Mac is below the model's own
                // RAM floor (SH-3). It was the one Studio that carried no warning: the worker
                // published a structured `fit` verdict and no sentence, this construction dropped
                // even that, and the Audio page had nothing to draw. Advice, never a refusal —
                // `blockReason` is the only field that stops a job.
                memoryWarning: item.memoryWarning,
                blockReason: block,
                paused: package?.paused == true, pausedReason: package?.pausedReason,
                longForm: NativeLongFormDelivery(item.longFormDelivery),
                pausedStudio: package?.pausedStudio,
                laneContract: item.laneContract,
                modelLimits: item.modelLimits
            )
        }
        let transcriptionModels = transcriptionRows.map { item in
            let package = packages[item.repo]
            // Being dispatchable is the producer's own answer: the transcription worker lists this
            // model and the Hub publishes a package for it. The engine's *name* is not a gate — a
            // hardcoded whisper*/subtitle* test hid every engine the worker reported ready while
            // calling every other engine a "Whisper contract".
            let engine = (item.engine ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let supported = package != nil
            // The transcription catalog carries no memory tier, so its only gates are the ones
            // the package row reports.
            let block = Self.blockReason(memoryWarning: nil, memoryEligible: nil, package: package)
            // The RAM tier the Subtitle worker publishes on this same repo's catalogue row. The
            // availability route the transcription rows come from carries no tier, and the
            // catalogue row is where Image, Music and Chat publish theirs (`memory_warning`);
            // reading none of it left the published sentence with no page that could show it.
            let memoryWarning = catalogWarnings[item.repo]
            let ready = transcriptionAvailable && item.cached == true && block == nil
            // The runtime says which model it would pick; the app does not name one.
            let qualified = item.recommended == true
            let note = supported
                ? (ready
                    ? (qualified
                        ? "Recommended by the local \(engine.isEmpty ? "transcription" : engine) runtime."
                        : "\(engine.isEmpty ? "This transcription engine" : "The \(engine) engine") is supported through this Hub; this exact model has not completed the native pilot test.")
                    : "Native Transcription compatible; download this model and ensure the transcription runtime is ready.")
                : "This model is outside the packaged model catalog."
            return NativeModel(
                id: "transcription:\(item.repo)", repo: item.repo, label: item.label, family: item.engine ?? "Transcription",
                function: .transcription, cached: item.cached ?? false, supported: supported, supportNote: note,
                sizeGB: item.sizeGb, capabilities: ["transcription"], languages: item.languageList,
                runtimeReady: ready, supportsWordTimestamps: item.supportsWordTimestamps ?? false, speakers: [],
                memoryWarning: memoryWarning,
                blockReason: block, paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio,
                laneContract: item.laneContract,
                modelLimits: item.modelLimits,
                minimumMemoryGB: catalogTiers[item.repo]?.value,
                minimumMemorySource: catalogTiers[item.repo]?.source,
                supportsSegmentTimestamps: item.supportsSegmentTimestamps,
                wordTimestampsClaim: item.supportsWordTimestamps,
                weightFiles: catalogWeightFiles[item.repo] ?? [],
                weightFormat: item.weightFormat
            )
        }
        let imageModels = catalog.filter { $0.capabilities?.contains("txt2img") == true }.map { item in
            let package = packages[item.repo]
            // The form can drive this model when the model itself publishes image controls. What
            // counts is the model's own data, not the shipped engine's exact key set: a second
            // engine publishing fewer keys is drivable too, and its unpublished controls are
            // omitted from the request rather than faked here.
            let drivable = item.imageConfiguration != nil
            let supported = drivable && package != nil
            let block = Self.blockReason(memoryWarning: item.memoryWarning,
                                         memoryEligible: item.memoryEligible, package: package)
            let ready = item.runtimeReady == true && block == nil
            let note = supported
                ? (ready ? "Ready for local image generation." : "Verify the image model files and runtime in Models.")
                : (package == nil
                    ? "This model is outside the packaged model catalog."
                    : "This model publishes no image size, so this form has nothing to generate at. Publish `image_sizes` (or `width`/`height`) in its catalog entry — see components/image/app/backend/main.py.")
            return NativeModel(id: "image:\(item.repo)", repo: item.repo, label: item.label,
                family: item.family ?? "Image", function: .image, cached: item.cache?.state == "cached",
                supported: supported, supportNote: note,
                sizeGB: item.sizeGb, capabilities: item.capabilities ?? [], languages: [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: [], memoryWarning: item.memoryWarning,
                blockReason: block, paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio,
                // The loading-size setting is the model's own catalogue row, read rather than
                // rebuilt: whether a control exists is the model's answer, not this app's.
                loadingModes: item.loadingModes,
                laneContract: item.laneContract,
                modelLimits: item.modelLimits,
                // The size table with its tier where the row publishes one, and the label-carrying
                // `image_sizes` rows where it does not. Nothing is invented for the missing tier.
                imageSizeRows: item.sizes ?? (item.imageSizes ?? []).map {
                    NativeImageSizeRow(aspectRatio: $0.id, width: $0.width, height: $0.height,
                                       tier: nil, isDefault: nil)
                },
                guidanceIgnored: item.guidanceIgnored ?? false,
                minimumMemoryGB: catalogTiers[item.repo]?.value,
                minimumMemorySource: catalogTiers[item.repo]?.source)
        }
        // A chat form is whatever the catalogue publishes as one. The capability is spelled
        // several ways across workers (`chat.completion`, `chat`, `text-generation`,
        // `conversational`), and matching only the first two is what kept a chat model out of the
        // picker; the family is read as well, because a worker may name its family `chat-mlx`
        // without repeating the capability token.
        let chatModels = catalog.filter { Self.isChatForm($0) }.map { item in
            let package = packages[item.repo]
            let block = Self.blockReason(memoryWarning: item.memoryWarning,
                                         memoryEligible: item.memoryEligible, package: package)
            let cached = item.cache?.state == "cached"
            let ready = item.runtimeReady == true && block == nil && cached
            let note = ready
                ? "Ready for local chat."
                : "Install the Chat model and ensure the local Chat worker is running."
            return NativeModel(
                id: "chat:\(item.repo)", repo: item.repo, label: item.label,
                family: item.family ?? "Chat", function: .chat, cached: cached,
                supported: package != nil, supportNote: note, sizeGB: item.sizeGb,
                capabilities: item.capabilities ?? [], languages: [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: [], memoryWarning: item.memoryWarning,
                blockReason: block, paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio,
                laneContract: item.laneContract,
                modelLimits: item.modelLimits,
                minimumMemoryGB: catalogTiers[item.repo]?.value,
                minimumMemorySource: catalogTiers[item.repo]?.source)
        }
        // A video model is whatever the catalogue publishes as one: the Hub's own modality
        // annotation first, then the broker's capability tokens (`img2video` is the one its
        // reference-image gate reads, so a model publishing it can be animated from a scene).
        // The tier warning and the Hub's block verdict travel like every other Studio's.
        let videoModels = catalog.filter { item in
            if item.hubModality?.lowercased() == "video" { return true }
            let tokens = Set(item.capabilities ?? [])
            return !tokens.isDisjoint(with: ["img2video", "txt2video", "video"])
        }.map { item in
            let package = packages[item.repo]
            let block = Self.blockReason(memoryWarning: item.memoryWarning,
                                         memoryEligible: item.memoryEligible, package: package)
            let cached = item.cache?.state == "cached"
            let ready = item.runtimeReady == true && block == nil && cached
            let note = ready
                ? "Ready for local video generation."
                : "Install the Video model and ensure the local Video worker is running."
            return NativeModel(
                id: "video:\(item.repo)", repo: item.repo, label: item.label,
                family: item.family ?? "Video", function: .video, cached: cached,
                supported: package != nil, supportNote: note, sizeGB: item.sizeGb,
                capabilities: item.capabilities ?? [], languages: [], runtimeReady: ready,
                supportsWordTimestamps: false, speakers: [], memoryWarning: item.memoryWarning,
                blockReason: block, paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio,
                laneContract: item.laneContract,
                modelLimits: item.modelLimits,
                minimumMemoryGB: catalogTiers[item.repo]?.value,
                minimumMemorySource: catalogTiers[item.repo]?.source)
        }
        return audioModels + transcriptionModels + imageModels + chatModels + videoModels
            + Self.musicModels(catalog, packages: packages)
    }

    /// Whether a catalogue entry is a chat form, read from the entry's own published data.
    ///
    /// Capabilities are tokens, not a fixed sentence: the Hub's workers spell the same claim as
    /// `chat.completion`, `chat`, `text-generation` or `conversational`, and only the first two
    /// were matched — so a chat model written the other way never appeared. The family is read too,
    /// because a worker can name itself a chat family (`chat-mlx`) without repeating the token.
    fileprivate static func isChatForm(_ item: CatalogModel) -> Bool {
        // The Hub's own annotation first: a row the chat studio published is a chat model whatever
        // it calls its capabilities.
        if item.hubModality?.lowercased() == "chat" { return true }
        let chatTokens: Set<String> = ["chat", "chat.completion", "conversational",
                                       "conversation", "text-generation", "textgeneration"]
        var tokens: Set<String> = []
        for capability in item.capabilities ?? [] {
            let lowered = capability.lowercased()
            tokens.insert(lowered)
            tokens.formUnion(lowered.split(whereSeparator: { ".-_:".contains($0) }).map(String.init))
        }
        if !tokens.isDisjoint(with: chatTokens) { return true }
        guard let family = item.family?.lowercased(), !family.isEmpty else { return false }
        let familyTokens = Set(family.split(whereSeparator: { ".-_:".contains($0) }).map(String.init))
        return !familyTokens.isDisjoint(with: ["chat", "llm"])
    }

    /// The roster one model publishes, matched by the key its own runtime named it under.
    ///
    /// The Voice runtime publishes one roster per family as `<family>_preset_speakers` (the
    /// shipped one is `qwen3_preset_speakers`), and the model's catalog entry says which family it
    /// is. Matching by name is the only join key the payload carries today; the field to ask the
    /// producer for is a per-model `preset_speakers` on the model's own catalog entry, which would
    /// make this a direct read. Nil means the family publishes no roster — one built-in voice.
    fileprivate static func roster(for item: CatalogModel, rosters: [String: [String]]) -> [String]? {
        guard !rosters.isEmpty else { return nil }
        let family = Self.normalizedIdentity(item.family ?? "")
        let repo = Self.normalizedIdentity(item.repo)
        for (key, speakers) in rosters.sorted(by: { $0.key < $1.key }) {
            let named = Self.rosterFamily(key)
            guard !named.isEmpty, !speakers.isEmpty else { continue }
            if family == named || family.hasPrefix(named) || named.hasPrefix(family) { return speakers }
            if !repo.isEmpty, repo.contains(named) { return speakers }
        }
        return nil
    }

    /// `qwen3_preset_speakers` -> `qwen3`; a key that names no family stays empty and is ignored.
    ///
    /// Both spellings are accepted because the app's decoder runs
    /// `keyDecodingStrategy = .convertFromSnakeCase` before this sees a key, so the runtime's
    /// `qwen3_preset_speakers` arrives here as `qwen3PresetSpeakers`.
    private static func rosterFamily(_ key: String) -> String {
        let lowered = key.lowercased()
        let suffixes = ["_preset_speakers", "presetspeakers", "_preset_voices", "presetvoices",
                        "_speakers", "speakers", "_voices", "voices"]
        for suffix in suffixes where lowered.hasSuffix(suffix) && lowered.count > suffix.count {
            return Self.normalizedIdentity(String(lowered.dropLast(suffix.count)))
        }
        return ""
    }

    private static func normalizedIdentity(_ value: String) -> String {
        String(value.lowercased().filter { $0.isLetter || $0.isNumber })
    }

    /// The Music models the Hub catalogue exposes.
    ///
    /// The Music worker publishes each model's own `generation_defaults`/`generation_limits`; a
    /// model that publishes a duration is offered, and every other control is read from that same
    /// entry. A model with no duration at all is not offered, because the app has nothing truthful
    /// to put on the form — and it says so rather than disappearing.
    private static func musicModels(_ catalog: [CatalogModel],
                                    packages: [String: HubModelPackage]) -> [NativeModel] {
        let catalogTiers = catalog.reduce(into: [String: (value: Double, source: String)]()) { result, item in
            if let tier = item.memoryTier { result[item.repo] = tier }
        }
        return catalog.filter { $0.capabilities?.contains("music") == true }.map { item in
            let package = packages[item.repo]
            let drivable = item.musicConfiguration != nil
            let supported = drivable && package != nil
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
                    : "This model publishes no generation defaults, so this form has no length to generate. Add `generation_defaults` to its catalog entry — see components/music/app/backend/catalog.py.")
            return NativeModel(
                id: "music:\(item.repo)", repo: item.repo, label: item.label,
                family: item.family ?? "Music", function: .music, cached: cached, supported: supported,
                supportNote: note, sizeGB: item.sizeGb, capabilities: item.capabilities ?? [],
                languages: [], runtimeReady: ready, supportsWordTimestamps: false, speakers: [],
                memoryWarning: item.memoryWarning, blockReason: block,
                paused: package?.paused == true, pausedReason: package?.pausedReason,
                pausedStudio: package?.pausedStudio,
                sampleRateHz: item.sampleRateHz, channels: item.channels,
                singlePassMaxSeconds: item.generationLimits?.singlePassMaxSeconds,
                laneContract: item.laneContract,
                modelLimits: item.modelLimits,
                minimumMemoryGB: catalogTiers[item.repo]?.value,
                minimumMemorySource: catalogTiers[item.repo]?.source)
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
        // `memoryWarning` is the published RAM *tier*, and a tier is advice, not a gate. The
        // worker still owns the live free-memory guard, and the owner runs FLUX.2 Klein on
        // 8 GB Macs. The tier is surfaced through `NativeModel.memoryWarning`; returning it
        // here is what made an 8 GB Mac read "needs 16 GB" and refuse to generate. Only the
        // worker's own hard eligibility gate blocks.
        _ = memoryWarning
        if memoryEligible == false { return "This Mac does not have enough memory for this model." }
        guard let package else { return "This model is not part of the packaged catalog." }
        // A pause outranks the other gates: while it is set the Hub refuses every job, whatever the
        // files and the runtime say, and the only way back is the recheck button next to it.
        if package.paused == true {
            let why = package.pausedReason ?? "it failed repeatedly"
            return "Paused by the Hub after repeated failures — \(why). Fix the cause, then press Recheck."
        }
        if let detail = package.reason, !detail.isEmpty { return detail }
        // "Nothing is here" and "what is here is wrong" are different instructions. Every
        // not-installed package used to fall through to the line below and tell the operator to
        // press Verify Files, which cannot conjure files that were never downloaded — the
        // producer's own sentence for this state is "Install it in Models (or copy its folder in)
        // and verify again", and this says the same thing.
        if package.filesState == "not_installed" {
            return "Not installed on this Mac. Download it in Models, or copy its folder into the model library and verify it."
        }
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
            videoSettings: item?.params?.videoSettings,
            voiceSettings: item?.params?.voiceSettings,
            artifactPath: item?.artifactPath,
            durationSeconds: item?.audioDurationS ?? item?.durationSeconds,
            requestedWidth: item?.params?.width, requestedHeight: item?.params?.height,
            clipWidth: item?.width, clipHeight: item?.height, clipFrames: item?.frames,
            clipFrameRate: item?.frameRate, clipHasAudio: item?.hasAudio,
            generationSeconds: item?.runtimeS, runningSeconds: item?.elapsedS,
            completionTokens: item?.usage?.completionTokens,
            statedGenerationSeconds: item?.generationS,
            progressStage: item?.progressStage,
            // Taken from the DETAIL, not from the summary the loop is iterating: both are
            // `broker.batch_summary`, so they are the same number, and reading it here keeps one
            // source for the row rather than threading a second value through the mapping.
            stalledSeconds: wire.stalledSeconds,
            answer: item?.text ?? "",
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
        catch { throw Self.transportError(error) }
    }

    private func performData(_ request: URLRequest) async throws -> Data {
        let requestConnection = connectionID
        do {
            let (data, response) = try await session.data(for: request)
            guard connectionID == requestConnection else { throw NativeAPIError.message("The local engine connection changed before the request completed.") }
            try validateResponse(data: data, response: response, request: request)
            return data
        } catch let error as NativeAPIError { throw error }
        catch { throw Self.transportError(error) }
    }

    /// Turn a URLSession failure into something a screen can act on — or hand cancellation back
    /// as cancellation.
    ///
    /// A task that is torn down (a page that goes away, a `.task(id:)` whose id changed) makes
    /// `URLSession` throw `URLError` **-999**, `NSURLErrorCancelled`. Wrapping that into the same
    /// "could not reach the local engine" message as a refused connection made a cancelled read
    /// indistinguishable from a broken one: the voice library was wiped and a false failure was
    /// shown for a load the app itself had just cancelled. Cancellation is not a transport fact, so
    /// it is rethrown as `CancellationError` and callers decide; `isCancellation` recognises it on
    /// the far side.
    private static func transportError(_ error: Error) -> Error {
        if error is CancellationError { return CancellationError() }
        if let urlError = error as? URLError, urlError.code == .cancelled { return CancellationError() }
        if Task.isCancelled { return CancellationError() }
        return NativeAPIError.message("Could not reach the local engine: \(error.localizedDescription)")
    }

    private func validateResponse(data: Data, response: URLResponse, request: URLRequest) throws {
        guard let http = response as? HTTPURLResponse else { throw NativeAPIError.message("The local engine returned no HTTP response.") }
        guard let final = http.url,
              final.scheme == "http", final.host == baseURL?.host, final.port == baseURL?.port else {
            throw NativeAPIError.message("The local engine attempted to redirect outside the address it was reached at.")
        }
        guard (200..<300).contains(http.statusCode) else {
            // A parameter-valued refusal is not one sentence: the defaults routes answer 422 with
            // `detail` keyed by parameter, and collapsing that into a string loses which field the
            // operator has to fix. It is carried as such and the editor places each reason.
            if http.statusCode == 422, let reasons = Self.refusalDetail(data), !reasons.isEmpty {
                throw NativeAPIError.refusals(reasons)
            }
            let detail = Self.errorDetail(data) ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw NativeAPIError.message("Local engine HTTP \(http.statusCode): \(detail)")
        }
    }

    /// `{"detail": {"steps": "steps: 101 is above the lane's maximum of 50"}}` as a Swift map,
    /// or nil when the body is not a per-parameter refusal.
    private static func refusalDetail(_ data: Data) -> [String: String]? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = object["detail"] as? [String: Any] else { return nil }
        var reasons: [String: String] = [:]
        for (name, reason) in detail {
            if let text = reason as? String { reasons[name] = text }
        }
        return reasons
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
        /// Absent means the operator has not chosen; absent in state written before the toggle
        /// existed, and still absent in state written since whenever they have left it alone.
        let wordTimestamps: Bool?
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
        // A saved pace of exactly 1.0 stays absent. Every build before this one wrote 1.0 whenever
        // the operator had never touched the slider, so keeping it would read as a choice and hold
        // the Models tab's stored pace dead on every Mac that has ever run the app. Nil is "not
        // chosen", which is what that 1.0 meant. A deliberate 1.0 chosen against a model whose
        // default is something else is the one value this cannot tell apart, and it costs one
        // movement of the slider to restore. A pace from an older file is otherwise kept as
        // written: it is pulled into the selected model's published bounds when it is used, which
        // are the bounds the worker clamps to.
        audioSpeed = state.audioSpeed.flatMap { abs($0 - 1.0) > 0.0001 ? $0 : nil }
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
/// `POST /api/hub/fleet` on this Mac with the Hub token: a rotation. `token` is omitted — the Hub
/// mints the new value, so it never travels from the app.
private struct FleetTokenRequest: Encodable { let token: String? }
/// `POST /api/hub/fleet/handover`: the promoted node's address and its new Fleet token.
private struct FleetHandoverRequest: Encodable {
    let controllerUrl: String
    let token: String
}
/// What the owner's app sends to `POST /api/hub/release/trust`. `publisherId` is a display label
/// and is omitted when blank; the Hub stores null rather than inventing a name.
private struct ReleaseTrustRequest: Encodable {
    let publicKey: String
    let publisherId: String?
}
/// The Hub's answer to a trust install. `publisherKeyId` and `fingerprint` are the same value; both
/// are read so a slightly older Hub that returns only one still reports correctly.
private struct ReleaseTrustResult: Decodable {
    let publisherId: String?
    let publisherKeyId: String?
    let fingerprint: String?
}
private struct JoinRequest: Encodable {
    let nodeId: String
    let name: String
    let address: String
    /// What this Mac runs. The controller builds its dispatch targets from this, so a join
    /// without it would register a node that nothing can send work to.
    let studios: [JoinStudio]
    /// This Mac's own node credential, when it has one, so a re-join of a row that already holds
    /// a credential can prove it is the same machine. The header carries it too; the body copy is
    /// what makes the request self-describing if a proxy ever drops the header.
    let credential: String?
}

/// The controller's answer to a join.
///
/// `credential` is this Mac's own, minted for it alone, and it is returned **once**: it is never
/// published on a read route, so a Mac that loses it has to be re-admitted rather than re-read.
/// Absent from a controller older than per-node credentials; the join still stands.
private struct JoinAnswer: Decodable {
    let credential: String?
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
    /// How this model can be told what to sound like, published by the Voice runtime itself:
    /// `"cloning"`, `"preset"`, `"design"`, in that order. This is the producer's own answer and
    /// the only source for which tabs the Audio page shows; absent means the worker is older than
    /// this field, which the page says out loud rather than guessing a form.
    let modes: [String]?
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
    /// The Image worker's loading-size setting as it applies to this model on this Mac: whether
    /// the model has more than one loading mode at all, the options and their own measured
    /// sentences, and the reason a single-mode model carries instead. Published per Image
    /// catalogue row; absent for every other function, and absent for an image row whose manifest
    /// the worker has no registered model for.
    let loadingModes: NativeImageLoadingModes?
    /// Family capability claim from the runtime, so clone vs preset is decided by the catalog
    /// rather than by guessing from the model name.
    let referenceAudio: CatalogReferenceAudio?
    /// How this model is sectioned, how its speed reaches the engine, and the range a person may
    /// choose. Published per model by the worker; it was in the payload all along and the app
    /// decoded none of it, so "how is a long script chunked for this model?" had no answer in the
    /// app at all.
    let longFormDelivery: HubLongFormDelivery?
    /// Which Studio the Hub says published this row (`hub_modality`). The Hub's aggregate adds it
    /// to every model, so it is the producer's own answer to "which form is this?" — independent of
    /// how that worker happens to spell its capabilities.
    let hubModality: String?
    /// The runtime's own claim about this exact model's contract: `"audit"` when the limits below
    /// are audit-grounded, `"unverified"` otherwise. The app read none of it and instead decided
    /// which models were "pilot tested" from a repo list, so a new quant of an audited family was
    /// mislabelled. This is the producer's own answer.
    let executionContract: CatalogExecutionContract?
    /// The dispatch contract this row's Studio published for this model. It is where the app
    /// learns the operation, the modality every worker call for this model is addressed with, and
    /// every parameter with the bound the lane enforces.
    let laneContract: NativeLaneContract?
    /// The Image size table with its resolution tier: each aspect ratio, the pixels it means, and
    /// the tier the model states. Published beside `image_sizes`, which carries the label.
    let sizes: [NativeImageSizeRow]?
    /// The model limits the row publishes with the artefact that states each one. The bare
    /// `context_window_tokens`/`max_output_tokens` beside it carry numbers with no provenance and
    /// are deliberately not decoded: a worker's fallback must never be shown as the model's fact.
    let modelLimits: [String: NativeModelLimit]?
    /// The Image worker's own answer to "does this model read a guidance value at all?".
    let guidanceIgnored: Bool?
    /// The RAM tier the Studio publishes for this model. Two spellings ship; each is kept with the
    /// key it came from so the fact can name its source.
    let minTotalMemoryGb: Double?
    let qualifiedMinimumMemoryGb: Double?
    /// The package's file receipt, which is where the Subtitle worker names the weight files it
    /// found. Read one level down, because that is where the producer publishes them.
    let readiness: CatalogReadiness?
    /// The weight format the row states for this package, from the package's own manifest. Kept
    /// beside `readiness` on purpose: the receipt says which files exist, this says what they are.
    let weightFormat: String?
    var isAuditGrounded: Bool { executionContract?.qualificationSource == "audit" }


    /// The tier this row publishes with the key that carried it, or nil when it publishes none.
    var memoryTier: (value: Double, source: String)? {
        if let minTotalMemoryGb { return (minTotalMemoryGb, "catalog row min_total_memory_gb") }
        if let qualifiedMinimumMemoryGb {
            return (qualifiedMinimumMemoryGb, "catalog row qualified_minimum_memory_gb")
        }
        return nil
    }

    /// The controls this model actually publishes, or nil when it publishes no size to generate
    /// at. Every control key inside is optional: a second engine of the same modality publishing
    /// a different control set is offered, with the controls it never named left out of the
    /// request, instead of decoding to nil and being filtered out of the picker.
    var imageConfiguration: NativeImageConfiguration? {
        let published = (imageSizes ?? []).filter { $0.width > 0 && $0.height > 0 }
        let deduped = published.reduce(into: [NativeImageSize]()) { result, size in
            if !result.contains(where: { $0.id == size.id }) { result.append(size) }
        }
        guard let size = generationDefaults?.imageSize ?? deduped.first.map({ ($0.width, $0.height) })
        else { return nil }
        // The picker needs at least the size the model generates at; a model that publishes no
        // `image_sizes` gets the one its own defaults name rather than an empty chooser.
        let sizes = deduped.isEmpty
            ? [NativeImageSize(id: "\(size.width)x\(size.height)",
                               label: "\(size.width) × \(size.height)",
                               width: size.width, height: size.height)]
            : deduped
        let limits = generationLimits?.imageLimits ?? NativeImageLimits()
        let defaults = limits.clamped(NativeImageSettings(
            width: size.width, height: size.height,
            steps: generationDefaults?.steps ?? limits.minSteps,
            guidance: generationDefaults?.guidance ?? limits.minGuidance,
            seed: generationDefaults?.seed ?? limits.minSeed))
        return NativeImageConfiguration(defaults: defaults, sizes: sizes, limits: limits)
    }
    var musicConfiguration: NativeMusicConfiguration? {
        guard let published = generationDefaults?.musicSettings else { return nil }
        let limits = generationLimits?.musicLimits ?? NativeMusicLimits()
        let defaults = limits.clamped(NativeMusicSettings(
            durationSeconds: published.durationSeconds,
            steps: published.steps ?? limits.minSteps,
            guidanceScale: published.guidanceScale ?? limits.minGuidance,
            shift: published.shift ?? limits.minShift,
            seed: published.seed ?? limits.minSeed,
            instrumental: published.instrumental, lyrics: published.lyrics,
            vocalLanguage: published.vocalLanguage))
        return NativeMusicConfiguration(defaults: defaults, limits: limits)
    }
}

/// The runtime's own per-model execution contract. Only the fields the app reads are declared.
private struct CatalogExecutionContract: Decodable {
    /// `"audit"` when this exact model's published limits come from an audit.
    let qualificationSource: String?
}

/// One level of the catalogue row's `readiness` block: the file receipt, where a worker names the
/// weight files it found for a package.
private struct CatalogReadiness: Decodable {
    let files: CatalogFiles?
}
private struct CatalogFiles: Decodable {
    let weightFiles: [String]?
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
    /// The size this model generates at, when it publishes one.
    var imageSize: (width: Int, height: Int)? {
        guard let width, let height, width > 0, height > 0 else { return nil }
        return (width, height)
    }
    var musicSettings: NativeMusicSettings? {
        guard let durationSeconds else { return nil }
        return NativeMusicSettings(
            durationSeconds: durationSeconds, steps: steps, guidanceScale: guidanceScale,
            shift: shift, seed: seed, instrumental: instrumental,
            lyrics: lyrics, vocalLanguage: vocalLanguage)
    }
}

/// The catalogue's `generation_limits` object, decoded field by field for the same reason.
/// Every bound is optional: a model that publishes only some of them is still offered, and the
/// bounds it does not publish are the worker's own validation to enforce.
private struct HubGenerationLimits: Decodable {
    let minSteps: Int?
    let maxSteps: Int?
    let minGuidance: Double?
    let maxGuidance: Double?
    let minSeed: Int64?
    let maxSeed: Int64?
    let minDurationSeconds: Double?
    let maxDurationSeconds: Double?
    /// The longest single render this host's engine can do: a longer request is rendered in
    /// sections, which is a different fact from the model's own maximum.
    let singlePassMaxSeconds: Double?
    let minShift: Double?
    let maxShift: Double?
    var imageLimits: NativeImageLimits {
        NativeImageLimits(minSteps: minSteps, maxSteps: maxSteps,
                          minGuidance: minGuidance, maxGuidance: maxGuidance,
                          minSeed: minSeed, maxSeed: maxSeed)
    }
    var musicLimits: NativeMusicLimits {
        NativeMusicLimits(minDurationSeconds: minDurationSeconds, maxDurationSeconds: maxDurationSeconds,
                          minSteps: minSteps, maxSteps: maxSteps,
                          minGuidance: minGuidance, maxGuidance: maxGuidance,
                          minShift: minShift, maxShift: maxShift,
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
    /// The step the catalogue's own section-size control offers, when it publishes one.
    let sectionStep: Int?

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
        sectionStep = control?.step
    }

    /// How long-form delivery is described, each line carrying the producer's own source.
    ///
    /// The first two lines are the bounds: the size the engine will actually cut at, and the range
    /// a caller may choose from with the catalogue's own step. A step nobody published is not a
    /// step this app invents, so it is left out rather than defaulted.
    var facts: [NativeModelFact] {
        var facts: [NativeModelFact] = []
        facts.append(NativeModelFact(
            label: "Section size",
            value: "\(chosenSectionMaxCharacters) characters · engine default",
            source: "catalog row long_form_delivery.section_size_control.runtime_default"))
        var bounds = "\(sectionMinimum)–\(sectionMaximum) characters"
        if let sectionStep { bounds += " · step \(sectionStep)" }
        facts.append(NativeModelFact(label: "Adjustable range", value: bounds,
                                     source: "catalog row long_form_delivery.section_size_control"))
        if let hardCapCharacters {
            facts.append(NativeModelFact(
                label: "Hard cap",
                value: "stops reading past \(hardCapCharacters) characters",
                source: "catalog row long_form_delivery.hard_cap_characters"))
        }
        facts.append(NativeModelFact(label: "Between sections",
                                     value: "\(joinPauseMilliseconds) ms",
                                     source: "catalog row long_form_delivery.join_pause_milliseconds"))
        // How the speed control reaches the engine is a different fact from its range: an engine
        // that paces itself keeps the voice, and a post-pass is applied to finished audio.
        let delivery = speedDelivery == "native" ? "engine-native" : "post-pass"
        facts.append(NativeModelFact(
            label: "Speed control",
            value: "\(delivery) · \(NativeJSONValue.trimmed(speedMinimum))–\(NativeJSONValue.trimmed(speedMaximum))×",
            source: "catalog row long_form_delivery.speed.delivery"))
        if !note.isEmpty {
            facts.append(NativeModelFact(label: "Long-form note", value: note, source: source))
        }
        return facts
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
    /// The manifest's own claim about timed sections, published beside the word-timestamp claim.
    let supportsSegmentTimestamps: Bool?
    /// The weight format the package states, on the row the Subtitle worker computes it for
    /// (`weight_format`, from the manifest's own file list). This is the fact; the catalogue's
    /// `readiness.files.weight_files` is the receipt beside it.
    let weightFormat: String?
    /// The lane contract the Subtitle worker publishes on this same row.
    let laneContract: NativeLaneContract?
    /// The model limits the row publishes with the artefact that states each one, when it does.
    let modelLimits: [String: NativeModelLimit]?
    /// The weight files the worker's catalogue row lists for this package, read from the Hub's
    /// aggregated catalogue row for the same repo. It is what says which weight format this
    /// runtime can load, and it is published on the catalogue row rather than here.
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
/// What the Voice runtime publishes about its own generation contracts.
///
/// The preset rosters are decoded as a bag keyed the way the runtime itself names them, rather
/// than as one fixed key: the shipped runtime publishes `qwen3_preset_speakers`, and a second
/// preset family publishes `<family>_preset_speakers`. Reading only the Qwen-shaped key left a
/// second preset family with an empty roster, so its Ready-Made tab read "This model has not
/// reported its ready-made voices yet." while the runtime had reported them.
private struct GenerationAvailability: Decodable {
    let rosters: [String: [String]]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: RosterKey.self)
        var found: [String: [String]] = [:]
        for key in container.allKeys {
            let name = key.stringValue.lowercased()
            let isRoster = name.hasSuffix("preset_speakers") || name.hasSuffix("presetspeakers")
                || name.hasSuffix("preset_voices") || name.hasSuffix("presetvoices")
                || name.hasSuffix("_speakers") || name == "speakers"
            guard isRoster,
                  let speakers = try? container.decode([GenerationSpeaker].self, forKey: key)
            else { continue }
            found[key.stringValue] = speakers.map(\.id)
        }
        rosters = found
    }

    private struct RosterKey: CodingKey {
        let stringValue: String
        var intValue: Int? { nil }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }
}
private struct GenerationSpeaker: Decodable { let id: String }
private struct JobsResponse: Decodable { let jobs: [JobWire] }
private struct HubJobsResponse: Decodable { let batches: [HubJobSummary] }
private struct HubChatMessage: Codable { let role: String; let content: String }
/// `model` is optional on purpose: the synthesized encoder writes nothing for a `nil`, so "no model
/// chosen" is an absent field rather than an empty string. The shipped chat worker reads
/// `body.get("model")` and treats a falsy value as "serve the model I was started with", which is
/// why an empty string could never be told apart from a real choice - the Hub had nothing to gate
/// or dispatch by, and a second chat model could not be addressed at all.
private struct HubChatRequest: Encodable { let model: String?; let messages: [HubChatMessage]; let stream: Bool }
/// Token usage as a Chat worker states it. Every count is optional: a field the worker did not
/// publish stays absent, so nothing here can be mistaken for a stated zero.
private struct HubUsage: Decodable {
    let promptTokens: Int?
    let completionTokens: Int?
    let totalTokens: Int?
}
/// `studio_hub` on a Chat completion: the worker's own generation time for the reply it just
/// produced. This is the only number a tokens-per-second rate may be derived from.
private struct HubChatStudio: Decodable { let elapsedSeconds: Double? }
private struct HubChatCompletion: Decodable {
    struct Choice: Decodable {
        struct Message: Decodable { let content: String }
        let message: Message
    }
    let choices: [Choice]
    /// The usage the worker stated for this reply, when it stated any.
    let usage: HubUsage?
    /// The worker's own stated generation window for this reply. The live reply carries no
    /// Hub-measured round trip, so when this is absent there is no rate to show.
    let studioHub: HubChatStudio?
    var completionTokens: Int? { usage?.completionTokens }
    var statedGenerationSeconds: Double? { studioHub?.elapsedSeconds }
}
private struct HubJobSummary: Decodable {
    let id: String
    let modality: String
    /// The Hub's own "nothing has moved" clock for this batch. Published on the summary and on the
    /// detail (both come from `broker.batch_summary`), and read by neither until now.
    let stalledSeconds: Double?
}
private struct HubJobDetail: Decodable {
    let id: String
    let model: String
    let createdAt: Double?
    /// The same field the summary carries: `/api/hub/jobs/{id}` is `_summary(batch)`, so the detail
    /// is the summary plus `items`.
    let stalledSeconds: Double?
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
    /// The answer a Chat completion produced, when the batch is a Chat one.
    ///
    /// The Hub writes the assistant's text here (`PAGE_ANSWER_FIELD` in the Hub's own chat job
    /// module) because a Chat worker streams its answer and keeps no file, so the item *is* the
    /// artifact. Only `chatJobRow` reads this: for a Voice item the same key has carried the script
    /// the operator typed, and nothing may show that as an answer.
    let text: String?
    let artifactUrl: String?
    let error: String?
    let bytes: Int64?
    let sha256: String?
    let params: HubJobParams?
    /// Where the worker wrote the artifact **on its own disk**, which the Hub records when it
    /// adopts a finished job (`artifact_path`). This is what "Show in Folder" reveals, and it is
    /// the only field that answers the operator's question — *where is the actual file?* — without
    /// fetching it. Nil is honest for a job whose worker sits on another Mac: that file is not on
    /// this one, and the row says so rather than opening Finder on nothing.
    let artifactPath: String?
    /// The finished clip's own measured facts, copied onto the item by the Hub from the worker's
    /// report, so a 1024×576 video says so without the app opening the MP4. Image and Video both
    /// carry `width`/`height`; `frames`, `frame_rate` and `has_audio` are Video's.
    let width: Int?
    let height: Int?
    let frames: Int?
    let frameRate: Double?
    let hasAudio: Bool?
    /// How long the produced audio runs. The Hub records it when it adopts a finished voice
    /// job, and the app showed none of it - a generated clip had no stated length.
    let audioDurationS: Double?
    /// How long the produced **video** clip runs, from the worker's own MP4 measurement. The Hub
    /// copies `duration_seconds` onto a finished video item the same way it copies the Voice
    /// worker's `audio_duration_s`; a video row read neither before this field existed. The name
    /// is the wire key, not the producer: this decodes `duration_seconds`, which is why it is not
    /// spelled `video_duration_s`.
    let durationSeconds: Double?
    /// How long the generation itself took. `runtime_s` is written when an item finishes; a
    /// running job reports `elapsed_s` instead, so the operator sees a clock either way.
    ///
    /// This is not the length of the clip it produced, which is what the rows used to show and
    /// what the owner read as "how long it takes": a 4.6-second clip that took 52 seconds to make
    /// showed only "4.6 s".
    let runtimeS: Double?
    let elapsedS: Double?
    /// The tokens the Chat worker stated it generated for this item, when it stated a usage block.
    /// Absent is "the worker stated none", never zero.
    let usage: HubUsage?
    /// The Chat worker's own stated generation time, beside — never derived from — `runtime_s`.
    /// `runtime_s` is the Hub's measured round trip and includes the model load on a cold turn,
    /// so a rate computed from it would understate the model.
    let generationS: Double?
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
    /// Video's own controls: the canvas the operator picked and the switch they set. The lane
    /// resolves `resolution` + `aspect_ratio` into a pixel canvas itself, so these are shown as
    /// asked rather than converted here into numbers the lane never published.
    let aspectRatio: String?
    let resolution: String?
    let audio: Bool?
    let mode: String?
    /// Voice's own controls. `speed` is the pace the operator chose, which the engine's own
    /// default equals 1.0; the row shows it because a clip generated at 1.5× is a different
    /// result from one at the model's pace.
    let speed: Double?
    /// A finished job's own controls, exactly as the Hub echoed them. A control the model did not
    /// publish is absent here too, so the row shows what was really asked for.
    var imageSettings: NativeImageSettings? {
        guard let width, let height else { return nil }
        return NativeImageSettings(width: width, height: height, steps: steps,
                                   guidance: guidance, seed: seed)
    }
    var musicSettings: NativeMusicSettings? {
        guard let durationSeconds else { return nil }
        return NativeMusicSettings(durationSeconds: durationSeconds, steps: steps,
                                   guidanceScale: guidanceScale, shift: shift, seed: seed,
                                   instrumental: instrumental, lyrics: lyrics,
                                   vocalLanguage: vocalLanguage)
    }
    var videoSettings: NativeVideoSettings? {
        guard resolution != nil || aspectRatio != nil || durationSeconds != nil else { return nil }
        return NativeVideoSettings(aspectRatio: aspectRatio, resolution: resolution,
                                   durationSeconds: durationSeconds, audio: audio, mode: mode,
                                   seed: seed)
    }
    var voiceSettings: NativeVoiceSettings? {
        guard speed != nil else { return nil }
        return NativeVoiceSettings(speed: speed)
    }
}

/// What a Video job was asked for. Kept apart from `NativeVideoConfiguration`, which is what the
/// **lane publishes** for the form: this is what one job actually sent.
struct NativeVideoSettings: Equatable {
    var aspectRatio: String?
    var resolution: String?
    var durationSeconds: Double?
    var audio: Bool?
    var mode: String?
    var seed: Int64?
}

/// What a Voice job was asked for, beyond the model and the text.
struct NativeVoiceSettings: Equatable {
    var speed: Double?
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
private struct HubVideoRequest: Codable {
    let modality: String
    let model: String
    let clientRequestId: String
    let items: [HubVideoItem]
    /// Overrides the Hub's automatic choice of Studio. Absent means automatic.
    let routing: String?
    init(modality: String, model: String, clientRequestId: String, items: [HubVideoItem],
         routing: String?) {
        self.modality = modality
        self.model = model
        self.clientRequestId = clientRequestId
        self.items = items
        self.routing = routing
    }
}
/// One Video item. `params` is deliberately an open map of the lane's own published
/// parameters — the form sends what the selected model's contract offers and nothing else, so a
/// lane with no geometry control receives a prompt-only item rather than invented numbers.
private struct HubVideoItem: Codable { let prompt: String; let params: [String: NativeJSONValue] }
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
    /// The ready-made voice the operator chose, or nil when they have not chosen one — and nil for
    /// a model whose lane publishes no `preset_speaker`, which is every model with a single built-in
    /// voice and every cloning path. Nil is encoded away entirely (`encodeIfPresent`), so the
    /// request carries no key and the value in force applies. A non-optional `String` here was the
    /// same defect the pace had: the app supplied `""` or a seeded name on every job, and a
    /// parameter the caller supplies is read as their own answer.
    var presetSpeaker: String?
    /// The language the operator asked for, or nil when this model publishes no `language` at all:
    /// a family that takes no language input discards the key, so sending one is a parameter the
    /// lane never reads. Nil is encoded away entirely (`encodeIfPresent`).
    var language: String?
    /// How fast to speak, or nil when the operator has not chosen a pace on this page. Nil is
    /// encoded away entirely (`encodeIfPresent`), so the request carries no `speed` key at all and
    /// the Voice worker's own resolution applies: the Models tab's stored pace for this model, over
    /// the pace its lane states. Sending 1.0 for an untouched slider was what made that stored
    /// default unreachable from the app.
    var speed: Double?
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
    /// The operator's own answer, or nil when they left the toggle alone and the model's stored
    /// default was in force. Kept as the answer rather than as the resolved value so the record of
    /// what was asked for stays what was asked for.
    let wordTimestamps: Bool?
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
