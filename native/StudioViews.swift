import AppKit
import SwiftUI
import AVFoundation
import UniformTypeIdentifiers

struct StudioWindow: View {
    @ObservedObject var studio: StudioController
    var body: some View { StudioContent(studio: studio, workspace: studio.workspace) }
}

/// Tabs for the unified history. Owner requirement (2026-09-15): each generation page keeps its
/// own history, and the shared history is presented with tabs rather than one flat scroll.
///
/// Membership is derived from `StudioFunction.allCases` — the same list the sidebars, the pages
/// and the model pickers derive from — so a Studio added to the enum cannot arrive without its
/// history tab. This was a hand-written list of four names, and a Studio it did not name simply
/// had no tab, with nothing saying a tab was missing.
enum HistoryTab: Hashable, Identifiable {
    case all
    case studio(StudioFunction)

    static let allCases: [HistoryTab] = [.all] + StudioFunction.allCases
        .filter { $0.hasBatchHistory }
        .map(HistoryTab.studio)

    var studio: StudioFunction? {
        if case .studio(let function) = self { return function }
        return nil
    }
    /// The page's owner-facing name, so the tab reads "Subtitle" and not "Transcription".
    var title: String {
        guard let studio else { return "All" }
        return StudioPage.page(for: studio)?.title ?? studio.rawValue
    }
    var id: String { title }
}

/// A slowly turning rainbow ring, drawn only while `active`.
///
/// The owner's feedback was that a long generation "shows almost nothing", so a running job is now
/// unmistakable: the ring travels around the Generate button and the preview while work is in
/// flight. Because it is a `TimelineView`, it costs nothing when idle - the view is not in the
/// hierarchy at all once the last job finishes.
private struct ActiveAura<Content: View>: View {
    let active: Bool
    @ViewBuilder var content: () -> Content

    private static var rainbow: [Color] {
        [.pink, .purple, .indigo, .blue, .cyan, .green, .yellow, .orange, .pink]
    }

    var body: some View {
        if active {
            TimelineView(.animation) { context in
                let phase = context.date.timeIntervalSinceReferenceDate
                    .truncatingRemainder(dividingBy: 8) / 8
                content()
                    .overlay {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .stroke(AngularGradient(colors: Self.rainbow, center: .center,
                                                    angle: .degrees(phase * 360)),
                                    lineWidth: 3)
                            .shadow(color: .purple.opacity(0.35), radius: 4)
                            .allowsHitTesting(false)
                            .accessibilityHidden(true)
                    }
            }
        } else {
            content()
        }
    }
}

/// Small in-memory cache of Image thumbnails.
///
/// The history list must neither block on the network nor hold a full-size PNG per row, so a
/// thumbnail is fetched once through the verified artifact route, downscaled and kept. A failure
/// is not cached, so a row that could not be fetched is simply retried the next time it appears.
@MainActor
final class ImageThumbnailStore: ObservableObject {
    @Published private(set) var thumbnails: [String: NSImage] = [:]
    private var loading: Set<String> = []
    /// A soft ceiling. Image history is pruned by the Hub anyway; this only stops an unusually
    /// long session from pinning decoded thumbnails forever.
    private let limit = 240

    func thumbnail(for job: NativeJob, using workspace: NativeWorkspaceStore) async -> NSImage? {
        if let ready = thumbnails[job.id] { return ready }
        guard job.state == "done", !loading.contains(job.id) else { return nil }
        loading.insert(job.id)
        defer { loading.remove(job.id) }
        do {
            let data = try await workspace.imageData(for: job)
            guard let full = NSImage(data: data),
                  let small = Self.downscaled(full, to: 72) else { return nil }
            if thumbnails.count >= limit { thumbnails.removeAll() }
            thumbnails[job.id] = small
            return small
        } catch {
            return nil
        }
    }

    private static func downscaled(_ image: NSImage, to maxDimension: CGFloat) -> NSImage? {
        let source = image.size
        guard source.width > 0, source.height > 0 else { return nil }
        let scale = min(1, maxDimension / max(source.width, source.height))
        let target = NSSize(width: max(1, (source.width * scale).rounded()),
                            height: max(1, (source.height * scale).rounded()))
        let small = NSImage(size: target)
        small.lockFocus()
        NSGraphicsContext.current?.imageInterpolation = .high
        image.draw(in: NSRect(origin: .zero, size: target),
                   from: NSRect(origin: .zero, size: source),
                   operation: .sourceOver, fraction: 1)
        small.unlockFocus()
        return small
    }
}

/// One low-cost preview of a completed Image.
///
/// It is deliberately not the preview pane: the full image still loads only on Preview/Save. This
/// asks the store for a cached thumbnail and shows a placeholder until one arrives, so a slow fetch
/// never holds up the row.
private struct JobThumbnail: View {
    let job: NativeJob
    let connected: Bool
    @ObservedObject var store: ImageThumbnailStore
    @ObservedObject var workspace: NativeWorkspaceStore

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor))
            if let image = store.thumbnails[job.id] {
                Image(nsImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: "photo").font(.title3).foregroundStyle(.tertiary)
            }
        }
        .frame(width: 64, height: 64)
        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous)
            .stroke(Color(nsColor: .separatorColor), lineWidth: 1))
        .accessibilityLabel(job.text.isEmpty ? "Generated image thumbnail" : "Thumbnail of \(job.text)")
        .task(id: "\(job.id)-\(connected)") {
            guard connected else { return }
            _ = await store.thumbnail(for: job, using: workspace)
        }
    }
}

/// A whole-number field that can be typed into as well as stepped.
///
/// A value outside `range` is refused on commit and the field falls back to what it was, so a
/// typo can never leave the form in a state the Hub would reject. The stepper still clamps, so
/// the two controls cannot disagree about the allowed range.
private struct NumberField: View {
    let title: String
    @Binding var value: Int
    let range: ClosedRange<Int>
    var step: Int = 1
    var width: CGFloat = 90

    var body: some View {
        HStack(spacing: 6) {
            Text(title)
            Spacer(minLength: 8)
            TextField(title, value: Binding(
                get: { value },
                set: { if range.contains($0) { value = $0 } }
            ), format: .number.grouping(.never))
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .frame(width: width)
                .accessibilityLabel(title)
            Stepper(title, value: $value, in: range, step: step).labelsHidden()
        }
    }
}

/// The same as `NumberField`, for a measured value that may be fractional.
private struct DoubleField: View {
    let title: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    var step: Double = 1
    var width: CGFloat = 90

    var body: some View {
        HStack(spacing: 6) {
            Text(title)
            Spacer(minLength: 8)
            TextField(title, value: Binding(
                get: { value },
                set: { if $0.isFinite && range.contains($0) { value = $0 } }
            ), format: .number.precision(.fractionLength(0)))
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .frame(width: width)
                .accessibilityLabel(title)
            Stepper(title, value: $value, in: range, step: step).labelsHidden()
        }
    }
}

/// Which history an uncertain job belongs to, so one confirmation can act on all three rows.
private enum DiscardKind { case image, audio, music }

/// An uncertain job the operator asked to abandon, held until they confirm.
private struct PendingDiscard: Identifiable {
    let id: String
    let job: NativeJob
    let kind: DiscardKind
}

/// The fields one voice editor collects, for a voice that exists or one being added.
struct VoiceDraft: Identifiable {
    let id = UUID()
    var voice: NativeVoice?
    var name = ""
    var language = "en"
    var gender = "n"
    /// Ownership defaults to "owned by me": every voice in this library is the operator's,
    /// and the consent step is still shown rather than assumed.
    var license = "self-owned"
    var notes = ""
    var transcript = ""
    var clipPath: String?
    var acknowledged = true

    init() {}
    init(voice: NativeVoice) {
        self.voice = voice
        name = voice.name
        language = voice.language
        gender = voice.gender
        license = voice.license
        notes = voice.notes
        transcript = voice.transcript ?? ""
        // Editing never replaces the audio, so no clip is carried in.
        clipPath = nil
        acknowledged = voice.permissionAcknowledged
    }
    func withClip(_ path: String) -> VoiceDraft {
        var copy = self
        copy.clipPath = path
        return copy
    }
}

struct StudioContent: View {
    @ObservedObject var studio: StudioController
    @ObservedObject var workspace: NativeWorkspaceStore
    @StateObject private var player = PreviewPlayer()
    @State private var modelSearch = ""
    @State private var modelFilter = "All functions"
    @State private var installedOnly = false
    @State private var selectedModel: String?
    @State private var transcriptFormat = "Text"
    @State private var fileActionError: String?
    @State private var hfToken = ""
    @State private var tokenMessage: String?
    @State private var savingToken = false
    @State private var imagePreview: NSImage?
    @State private var selectedImageID: String?
    @State private var selectedMusicID: String?
    @State private var historyTab = HistoryTab.all
    @State private var loadingImage = false
    @State private var revealedToken = false
    @State private var connectionNote: String?
    @State private var controllerDraft = ""
    /// The name being typed. Kept apart from the Hub's answer so the field does not fight the poll.
    @State private var machineNameDraft = ""
    @State private var controllerToken = ""
    /// 0 means all time; otherwise the window in hours.
    @State private var statisticsWindow = 0
    @State private var showingStorageEditor = false
    @State private var draftGigabytes = 50.0
    @State private var draftRetentionDays = 30
    @State private var draftCleanup = true
    /// The voice being added or edited. A plain value plus a flag, so the sheet can bind to
    /// each field directly.
    @State private var voiceDraft = VoiceDraft()
    @State private var showingVoiceEditor = false
    /// The voice the operator asked to remove, held until they confirm.
    @State private var pendingVoiceDeletion: NativeVoice?
    /// Set while a clip is being transcribed here and now, for either the voice editor or the
    /// audio reference field, so the button shows it is working and cannot be pressed twice.
    @State private var transcribingClip = false
    /// Decoded Image thumbnails, shared by every history row so one artifact is fetched once.
    @StateObject private var thumbnails = ImageThumbnailStore()
    /// The uncertain job the operator asked to discard, held until they confirm.
    @State private var pendingDiscard: PendingDiscard?
    /// The gateway public key being pasted, and its optional label. Kept as plain text so the
    /// operator can read back exactly what they pasted before confirming.
    @State private var publisherKeyDraft = ""
    @State private var publisherIdDraft = ""
    /// Set while the confirmation dialog is up. The key is not sent until the operator confirms.
    @State private var confirmingPublisherKeyChange = false
    /// A sample is seeded into an empty prompt the first time a studio is opened, so a brand-new
    /// user has something to press Generate on. The flag keeps it from refilling a field the
    /// operator deliberately cleared.
    @State private var seededImageSample = false
    @State private var seededAudioSample = false
    @State private var seededMusicSample = false
    /// Which Music job the shared player is currently holding, so the preview transport only
    /// claims a clip it actually started.
    @State private var loadedMusicJobID: String?


    private var connected: Bool { studio.readyURL != nil && !studio.stopping && workspace.isReachable }
    private var selectedAudioModel: NativeModel? { workspace.models.first { $0.repo == workspace.audioModel && $0.function == .audio } }
    private var selectedTranscriptionModel: NativeModel? { workspace.models.first { $0.repo == workspace.transcriptionModel && $0.function == .transcription } }
    private var selectedImageModel: NativeModel? { workspace.models.first { $0.repo == workspace.imageModel && $0.function == .image } }
    private var selectedMusicModel: NativeModel? { workspace.models.first { $0.repo == workspace.musicModel && $0.function == .music } }
    private var previewImageJob: NativeJob? { workspace.imageJobs.first { $0.state == "done" && (selectedImageID == nil || $0.id == selectedImageID) } }
    private var previewMusicJob: NativeJob? { workspace.musicJobs.first { $0.state == "done" && (selectedMusicID == nil || $0.id == selectedMusicID) } }
    private var activeImages: [NativeJob] { workspace.imageJobs.filter { active($0.state) } }
    private var activeMusic: [NativeJob] { workspace.musicJobs.filter { active($0.state) } }
    private var activeAudio: [NativeJob] { workspace.audioJobs.filter { active($0.state) } }
    private var activeDownloads: [NativeDownload] { workspace.downloads.filter { active($0.state) } }
    private var filteredModels: [NativeModel] {
        workspace.models.filter { model in
            (modelFilter == "All functions" || model.function.rawValue == modelFilter) &&
            (!installedOnly || model.cached) &&
            (modelSearch.isEmpty || model.label.localizedCaseInsensitiveContains(modelSearch) || model.repo.localizedCaseInsensitiveContains(modelSearch))
        }
    }
    private func active(_ state: String) -> Bool { ["queued", "running", "cancel_requested", "cancelling", "paused"].contains(state) }
    private func stateLabel(_ state: String) -> String {
        switch state { case "done": return "Completed"; case "cancel_requested", "cancelling": return "Stopping"; case "error": return "Failed"; default: return state.replacingOccurrences(of: "_", with: " ").capitalized }
    }
    private func modelName(_ repo: String) -> String { workspace.models.first { $0.repo == repo }?.label ?? repo.components(separatedBy: "/").last ?? repo }

    // MARK: - Working state at a glance

    /// The colour a state is read as. Queued, running, done and failed must be distinguishable
    /// from across the room, which is the feedback: small grey text was not enough.
    private func stateColor(_ state: String) -> Color {
        switch state {
        case "done": return .green
        case "error": return .red
        case "uncertain": return .orange
        case "cancelled": return .gray
        case "running": return .blue
        case "queued": return .purple
        default: return .secondary
        }
    }
    private func stateSymbol(_ state: String) -> String {
        switch state {
        case "done": return "checkmark.circle.fill"
        case "error": return "xmark.octagon.fill"
        case "uncertain": return "questionmark.circle.fill"
        case "cancelled": return "slash.circle.fill"
        case "running": return "bolt.fill"
        case "queued": return "clock.fill"
        default: return "stop.circle.fill"
        }
    }

    /// The state as a coloured pill, so "Queued" and "Running" cannot be mistaken for each other.
    private func stateBadge(_ state: String) -> some View {
        Label(stateLabel(state), systemImage: stateSymbol(state))
            .font(.caption.weight(.semibold))
            .foregroundStyle(stateColor(state))
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(stateColor(state).opacity(0.14),
                        in: Capsule())
            .overlay(Capsule().stroke(stateColor(state).opacity(0.45), lineWidth: 1))
            .fixedSize()
    }

    /// The honest progress line for one job.
    ///
    /// A percentage is drawn only when the Hub actually reported one. A queued or unmeasured job
    /// gets an indeterminate linear bar instead, because a bar pinned at zero reads as "stuck" and
    /// a made-up number is worse than no number.
    /// "Running — 45% · DiT sampling", or just the percentage when the studio
    /// reported no phase label of its own.
    static func runningLabel(_ job: NativeJob) -> String {
        let percent = Int((min(1, max(0, job.progress)) * 100).rounded())
        if let stage = job.progressStage, !stage.isEmpty {
            return "Running — \(percent)% · \(stage)"
        }
        return "Running — \(percent)%"
    }

    @ViewBuilder private func jobProgress(_ job: NativeJob) -> some View {
        switch job.state {
        case "queued":
            HStack(spacing: 8) {
                ProgressView().progressViewStyle(.linear).frame(width: 140)
                Text("Waiting for a free worker").font(.caption).foregroundStyle(.secondary)
            }
        case "running":
            if job.progress > 0.001 {
                VStack(alignment: .leading, spacing: 3) {
                    ProgressView(value: min(1, max(0, job.progress)), total: 1)
                        .tint(stateColor(job.state))
                    Text(Self.runningLabel(job))
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else {
                HStack(spacing: 8) {
                    ProgressView().progressViewStyle(.linear).frame(width: 140)
                    Text(job.progressStage.map { "Running — \($0)" }
                         ?? "Running — progress is not reported by this model")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        case "uncertain":
            Label("Needs attention — an earlier attempt may still be running",
                  systemImage: "questionmark.circle")
                .font(.caption).foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
        case "cancel_requested", "cancelling":
            HStack(spacing: 8) {
                ProgressView().progressViewStyle(.linear).frame(width: 140)
                Text("Stopping…").font(.caption).foregroundStyle(.secondary)
            }
        case "error":
            Label("Failed", systemImage: "xmark.octagon")
                .font(.caption).foregroundStyle(.red)
        case "cancelled":
            Text("Cancelled").font(.caption).foregroundStyle(.secondary)
        default:
            EmptyView()
        }
    }

    /// A spinner and a sentence beside a Generate button while its studio is working.
    @ViewBuilder private func activeWorkIndicator(_ jobs: [NativeJob]) -> some View {
        if !jobs.isEmpty {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(jobs.count == 1 ? "Working…" : "Working on \(jobs.count) jobs…")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Sample prompts

    /// Twelve to fifteen varied visual prompts. Offered through "Surprise me" and the examples
    /// menu rather than hidden in documentation, so a blank page is never a dead end.
    static let imageSamples = [
        "A lone lighthouse on a black cliff at dawn, long exposure sea mist, cinematic",
        "A red panda barista pulling an espresso in a tiny wooden café, warm morning light",
        "Aerial view of terraced rice fields after rain, low clouds between the hills",
        "Macro photograph of frost crystals on a spiderweb, deep blue background",
        "A 1970s sci-fi control room with round CRT screens and orange indicator lights",
        "An empty night train carriage lit by overhead fluorescents, muted teal palette",
        "A whale swimming through a city street flooded with sunlight, surreal and calm",
        "A still life of ripe figs, cracked walnuts and dark bread on linen, Dutch masters light",
        "A Japanese garden in late autumn, stone lantern half hidden by red maple leaves",
        "Portrait of an old fisherman with weathered hands, soft overcast light, 85mm",
        "A desert highway at dusk with distant mesas and a single telephone pole",
        "Close-up of a hummingbird hovering beside a red flower, motion frozen, bright bokeh",
        "A cozy attic library with a round window, stacks of books and drifting dust motes",
        "Neon-lit ramen stand in the rain, steam and reflections on wet asphalt, night",
        "A hot air balloon rising over a patchwork of farmland at sunrise, soft haze",
    ]

    /// Lines to speak: short, natural, and some of them not in English, because the models handle
    /// more than one language and the samples should say so.
    static let audioSamples = [
        "Good morning. Here is your studio update for today.",
        "The train to the coast leaves in ten minutes. Please have your ticket ready.",
        "Let's keep this simple: one idea, one sentence, one clear next step.",
        "Guten Morgen. Die Proben beginnen um zehn Uhr im großen Saal.",
        "Bonjour, ceci est un message de bienvenue pour votre nouvel appareil.",
        "Hola, gracias por llamar. En un momento le atenderemos.",
        "こんにちは。今日はいい天気ですね。",
        "Welcome back. Your previous session is exactly where you left it.",
        "In a quiet room, a single idea can change the shape of an entire afternoon.",
        "This is a short test of the selected voice. How does it sound to you?",
        "Please leave a message after the tone, and we will get back to you soon.",
        "The kitchen is closing in fifteen minutes. Last orders, please.",
        "Numbers, dates and names: forty-two, the third of March, and Mr. Okonkwo.",
        "Let's begin with a slow breath in, and then a long, easy breath out.",
    ]

    /// Music descriptions, from sparse to busy, so the examples do not all sound the same.
    static let musicSamples = [
        "Warm lo-fi hip hop with dusty drums, vinyl crackle and a mellow Rhodes piano",
        "Slow orchestral strings building under a solo cello, cinematic and hopeful",
        "Driving 80s synthwave with arpeggios, gated drums and a wide analog bass",
        "Gentle solo piano, unhurried and reflective, recorded in a small hall",
        "Afrobeat groove with bright horns, talking drum and a rolling bassline",
        "Ambient drone with soft chimes and distant rain, calm and weightless",
        "Upright jazz trio, brushed drums and a walking bass, late-night club feel",
        "Punchy electronic dance track with a four-on-the-floor kick and rising pads",
        "Acoustic folk fingerpicking with harmonica and a close, intimate vocal",
        "Dusty boom-bap beat with chopped soul samples and a deep sub bass",
        "Minimal techno, hypnotic and repetitive, with a slow filter sweep",
        "Sweeping film score with taiko drums and brass, epic and determined",
        "Bossa nova guitar with light shakers and a relaxed whistled melody",
        "Chiptune adventure theme with a driving square-wave lead and playful bass",
        "Gospel organ and hand claps under a soaring choir, joyful and full",
    ]

    /// The "Surprise me" button plus the examples menu, shown beside a prompt field.
    private func sampleControl(_ samples: [String], binding: Binding<String>) -> some View {
        HStack(spacing: 8) {
            Button {
                if let pick = samples.randomElement() { binding.wrappedValue = pick }
            } label: {
                Label("Surprise Me", systemImage: "dice")
            }
            .help("Insert a random example into the field. Nothing is submitted.")
            Menu("Examples") {
                ForEach(samples, id: \.self) { sample in
                    Button(sample) { binding.wrappedValue = sample }
                }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .help("Choose an example to insert. Nothing is submitted.")
        }
        .font(.caption)
    }

    /// Fill an empty field once, so a new user can press Generate immediately. Never overwrites
    /// something the operator typed, and never resubmits anything.
    private func seedSample(_ samples: [String], binding: Binding<String>, seeded: Binding<Bool>) {
        guard !seeded.wrappedValue else { return }
        seeded.wrappedValue = true
        guard binding.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let pick = samples.randomElement() else { return }
        binding.wrappedValue = pick
    }

    /// The clip length the Hub reported for this item, or the length that was requested when the
    /// measured value is absent (some Music receipts carry no `audio_duration_s`). A requested
    /// value is labelled as such; a number is never invented.
    private func clipLength(_ job: NativeJob) -> String? {
        if let measured = job.durationSeconds, measured > 0 {
            return NativeJob.clock(measured)
        }
        if let requested = job.musicSettings?.durationSeconds, requested > 0 {
            return "\(NativeJob.clock(requested)) requested"
        }
        return nil
    }

    // MARK: - Discarding an uncertain job

    private func requestDiscard(_ job: NativeJob, kind: DiscardKind) {
        pendingDiscard = PendingDiscard(id: "\(kind)-\(job.id)", job: job, kind: kind)
    }

    private func confirmDiscard(_ pending: PendingDiscard) {
        pendingDiscard = nil
        Task {
            switch pending.kind {
            case .image: await workspace.discardImage(pending.job)
            case .audio: await workspace.discardAudio(pending.job)
            case .music: await workspace.discardMusic(pending.job)
            }
        }
    }

    /// The version this build actually is, shown in the corner so it is never a
    /// guess. A raw development executable has no bundle version; "Development" is
    /// more honest there than inventing a number.
    private var appVersion: String {
        let product = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
            .flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
            ?? "Development"
        // The exact build, stamped at packaging time. Two copies of the same product version are
        // otherwise indistinguishable, which is exactly the question an update raises.
        let build = (Bundle.main.object(forInfoDictionaryKey: "StudioHubBuild") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let build, !build.isEmpty else { return product }
        return "\(product) · \(build)"
    }

    var body: some View {
        HStack(spacing: 0) {
            List(selection: Binding<StudioPage?>(get: { studio.selection }, set: { if let value = $0 { studio.select(value) } })) {
                Label("Overview", systemImage: StudioPage.overview.symbol).tag(StudioPage.overview)
                ForEach(StudioPage.sidebarSections, id: \.title) { section in
                    Section(section.title) {
                        ForEach(section.pages) { page in
                            // "Coming later" pages are reachable but deliberately quieter: they
                            // work, there is just no engine behind them yet.
                            Label(page.title, systemImage: page.symbol)
                                .foregroundStyle(section.title == "Coming later" ? Color.secondary : Color.primary)
                                .tag(page)
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 6) {
                    // The working mode, as a pill. One glance answers "what is this Mac doing?" -
                    // Controller, Node or Standalone - with the dot beside it saying whether the
                    // group it belongs to is actually answering.
                    HStack(spacing: 6) {
                        Text(roleTitle(workspace.controllerIdentity?.settings.role))
                            .font(.caption).fontWeight(.semibold)
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(modeTint.opacity(0.18), in: Capsule())
                            .overlay(Capsule().stroke(modeTint.opacity(0.55)))
                        if workspace.controllerReachable != nil {
                            Circle().fill(connectionStateTint).frame(width: 7, height: 7)
                                .help(workspace.controllerHealthNote ?? "")
                        }
                        Spacer(minLength: 0)
                    }
                    // What this Mac can actually take: one count per kind of generator it is
                    // running, so a worker that failed to start is visible here.
                    if !generatorSummary.isEmpty {
                        Text(generatorSummary)
                            .font(.caption2).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: connected ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(connected ? Color.green : Color.secondary)
                        Text(studio.status).font(.caption).fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    Text("Studio Hub \(appVersion)")
                        .font(.caption2).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .accessibilityLabel("Studio Hub version \(appVersion)")
                }.padding(16)
            }
            // Not a fixed width: a fixed column cannot give anything back, so when the window is
            // squeezed (Split View, most obviously) the layout overflowed and macOS clipped it -
            // the sidebar and the page's first column appeared to be cropped away.
            .frame(minWidth: 150, idealWidth: 210, maxWidth: 260)
            Divider()
            VStack(spacing: 0) {
                if let error = studio.failure ?? workspace.error ?? workspace.startupNote ?? fileActionError {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text(error).textSelection(.enabled)
                        Spacer()
                        Button("Dismiss") { studio.failure = nil; workspace.error = nil; workspace.clearStartupNote(); fileActionError = nil }
                    }.font(.callout).padding(14).background(Color(nsColor: .controlBackgroundColor))
                    Divider()
                }
                switch studio.selection {
                case .overview: overview
                case .audio: audioWorkspace
                case .image: imageWorkspace
                case .music: musicWorkspace
                case .chat: chatWorkspace
                case .transcription: transcriptionWorkspace
                case .models: modelsWorkspace
                case .voices: voicesWorkspace
                case .jobs: jobsWorkspace
                case .connections: connections
                case .whatsNew: whatsNew
                case .settings: settings
                default: unavailableWorkspace
                }
            }
            .frame(minWidth: 0, maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .windowBackgroundColor))
            // The version used to sit in the page's lower-right corner. It now lives with the rest
            // of the status, under the sidebar, so everything an operator needs to identify this
            // Mac - mode, what it offers, whether it is up, which build - is in one place.
            .clipped()
        }
        // A floor that matches the window's own minimum, not the sum of every column: the
        // columns are allowed to compress below their ideal so nothing has to be cropped.
        .frame(minWidth: 720, minHeight: 520)
        .confirmationDialog(
            "Discard this uncertain job?",
            isPresented: Binding(get: { pendingDiscard != nil },
                                 set: { if !$0 { pendingDiscard = nil } }),
            titleVisibility: .visible,
            presenting: pendingDiscard) { pending in
                Button("Discard Attempt", role: .destructive) { confirmDiscard(pending) }
                Button("Keep", role: .cancel) { pendingDiscard = nil }
            } message: { _ in
                Text("This is you declaring the attempt abandoned. The Hub will stop waiting for it, will not try to reconcile it again, and this Mac is released for new work. Any result the original worker may still produce is ignored.")
            }
        // Changing the trusted publisher key decides which Macs may update this Mac, so it is a
        // confirmation like discarding an attempt: the action is spelled out and the cost is named.
        .confirmationDialog(
            "Change the trusted publisher key?",
            isPresented: $confirmingPublisherKeyChange,
            titleVisibility: .visible) {
                Button("Change Publisher Key", role: .destructive) {
                    let key = publisherKeyDraft
                    let publisher = publisherIdDraft
                    Task {
                        if await workspace.changePublisherKey(publicKey: key, publisherId: publisher) {
                            publisherKeyDraft = ""
                            publisherIdDraft = ""
                        }
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This changes which Macs are allowed to update this Mac. A wrong key means releases will be refused, and putting the right key back requires the owner to paste it on this Mac again.")
            }
    }

    private func heading(_ title: String, _ description: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.largeTitle.weight(.semibold))
            Text(description).font(.body).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func sectionTitle(_ title: String) -> some View { Text(title).font(.title3.weight(.semibold)).frame(maxWidth: .infinity, alignment: .leading) }
    private func empty(_ title: String, _ detail: String, symbol: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: symbol).font(.system(size: 28, weight: .light)).foregroundStyle(.secondary)
            Text(title).font(.headline)
            Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 440)
        }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    /// The line above the buttons. It names the click, because on a fresh Mac the difference
    /// between the two prominent buttons is 29 GB of downloading.
    private var setupDetail: String {
        if studio.preparedLibrary != nil {
            return "The models and voices you copied are ready. Accept them and the app starts using them."
        }
        return "Keep models on this Mac or an external drive. Your settings and generated files stay separate from the app."
    }

    private var setup: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(studio.hasLibrary ? "Start your local services" : "Choose where your models live").font(.title3.weight(.semibold))
            Text(studio.hasLibrary ? "Your model library is selected. Start the services to create assets and manage models." : setupDetail).foregroundStyle(.secondary)
            if let warning = studio.libraryPrivacyWarning {
                Text(warning).font(.callout).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if studio.starting || studio.stopping { ProgressView().controlSize(.small); Text(studio.status) }
                else {
                    if studio.hasLibrary { Button("Start Services") { studio.start() }.buttonStyle(.borderedProminent).disabled(!studio.canConfigure) }
                    // The models the setup step just copied are one click away, and are offered
                    // first: the alternative on a fresh Mac is an empty folder that would download
                    // 29 GB that is already on the disk.
                    else if let prepared = studio.preparedLibrary {
                        Button("Use the Models Already on This Mac") { studio.usePreparedLibrary() }
                            .buttonStyle(.borderedProminent).disabled(!studio.canConfigure)
                        Text(prepared.path).font(.caption).foregroundStyle(.secondary)
                        if let warning = ShellConfiguration.protectedFolderWarning(for: prepared) {
                            Text(warning).font(.caption).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    else { Button("Use Default Folder") { studio.useDefaultLibrary() }.buttonStyle(.borderedProminent).disabled(!studio.canConfigure) }
                    Button("Choose Model Folder…") { studio.chooseLibrary() }.disabled(!studio.canConfigure)
                }
                Spacer()
                Button("Settings") { studio.select(.settings) }
            }
        }.padding(20).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }

    private var overview: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                heading("Your studio", "Create locally. Keep track of your jobs, models and workspaces in one place.")
                if !connected { setup }
                HStack(alignment: .firstTextBaseline) {
                    sectionTitle("In progress")
                    Button("View All Jobs") { studio.select(.jobs) }
                }
                if activeAudio.isEmpty && activeImages.isEmpty && activeMusic.isEmpty && activeDownloads.isEmpty && !workspace.transcribing {
                    HStack(spacing: 12) {
                        Image(systemName: "checkmark.circle").foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("No active jobs").font(.headline)
                            Text("Open a workspace to start creating. Completed work stays in Jobs.").foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Open Audio") { studio.select(.audio) }
                    }
                } else {
                    ForEach(activeAudio) { job in jobRow(job, compact: true) }
                    ForEach(activeImages) { imageJobRow($0) }
                    ForEach(activeMusic) { musicJobRow($0) }
                    if workspace.transcribing { transcriptionActivity }
                    ForEach(activeDownloads) { download in downloadRow(download) }
                }
                Divider()
                sectionTitle("Workspaces")
                VStack(spacing: 0) {
                    ForEach(StudioPage.create) { page in
                        HStack(spacing: 14) {
                            Image(systemName: page.symbol).font(.title3).frame(width: 24)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(page.title).font(.headline)
                                Text(workspaceDescription(page)).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(workspaceStatus(page))
                                .font(.callout).foregroundStyle(.secondary)
                            Button("Open") { studio.select(page) }.accessibilityLabel("Open \(page.rawValue)")
                        }.padding(.vertical, 13)
                        if page != StudioPage.create.last { Divider() }
                    }
                }
                Divider()
                HStack {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Model library").font(.headline)
                        Text(connected ? "\(workspace.models.filter(\.cached).count) installed models in the connected library" : "Connect your library to inspect installed models").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Manage Models") { studio.select(.models) }
                }
                Divider()
                measuredSection
            }.padding(28)
        }
        // `connected` becomes true only after the Hub reports ready, so the task has to be
        // keyed on it: a plain `.task` runs once at launch and would never see a connection.
        .task(id: connected) {
            if connected { await workspace.refreshStatistics(hours: statisticsWindow) }
        }
    }

    /// What this Mac has actually measured, straight from the Hub's job ledger.
    ///
    /// The owner asked for per-machine generation speed, and the Hub already records it on
    /// every job; this is that data rather than a separate benchmark. A model with no timed
    /// run shows no average, never a zero.
    private var measuredSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                sectionTitle("Measured on this Mac")
                Spacer()
                Picker("Window", selection: Binding(
                    get: { statisticsWindow },
                    set: { value in
                        statisticsWindow = value
                        Task { await workspace.refreshStatistics(hours: value == 0 ? nil : value) }
                    })) {
                    Text("All time").tag(0)
                    Text("Last 7 days").tag(168)
                    Text("Last 24 hours").tag(24)
                }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 320)
            }
            if let stats = workspace.statistics {
                if stats.total == 0 {
                    Text("No completed generations recorded in this window yet. Run one from any workspace and it appears here.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    Text("\(stats.total) completed \(stats.total == 1 ? "generation" : "generations") recorded by this Hub.")
                        .font(.callout).foregroundStyle(.secondary)
                    ForEach(stats.trackedModels.prefix(8)) { row in
                        HStack(spacing: 12) {
                            Image(systemName: functionSymbol(row.modality)).foregroundStyle(.secondary).frame(width: 22)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(workspace.models.first { $0.repo == row.model }?.label ?? row.model)
                                    .fontWeight(.medium)
                                Text("\(row.modality?.capitalized ?? "Unknown") · \(row.count) \(row.count == 1 ? "run" : "runs")")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(seconds(row.avgS)).font(.system(.body, design: .monospaced))
                        }.padding(.vertical, 4)
                    }
                    if stats.trackedModels.count > 8 {
                        Text("Showing the 8 slowest of \(stats.trackedModels.count) measured models.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(machineBreakdown(stats), id: \.0) { machine, group in
                        HStack {
                            Text(machine == "local" ? "This Mac" : machine).font(.callout)
                            Spacer()
                            Text("\(group.count) jobs · \(seconds(group.avgS)) average")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                Text(connected ? "Generation statistics are unavailable in this build." : "Start local services to see measured generation times.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    private func machineBreakdown(_ stats: NativeStatistics) -> [(String, NativeStatsGroup)] {
        stats.byMachine.sorted { $0.value.count > $1.value.count }
    }

    /// The glyph for one modality's measured runs. Read from the Studio the modality belongs to,
    /// so a new Studio gets its own symbol instead of every unknown modality reading "clock".
    private func functionSymbol(_ modality: String?) -> String {
        guard let modality,
              let function = NativeWorkspaceStore.function(forModality: modality) else { return "clock" }
        return function.symbol
    }

    private func seconds(_ value: Double?) -> String {
        guard let value else { return "not measured" }
        return value >= 60 ? String(format: "%.1f min", value / 60) : String(format: "%.1f s", value)
    }
    private func workspaceStatus(_ page: StudioPage) -> String {
        // Derived from the page's own Studio, not a list of page names: a Studio this build has
        // no engine for says so, and a function-less page (Overview, Models, …) is not a
        // workspace at all.
        guard let function = page.function, function.hasEngine else { return "Engine not installed" }
        guard connected else { return "Services stopped" }
        if workspace.refreshing && workspace.models.isEmpty { return "Checking models…" }
        return workspace.models.contains { $0.function == function && $0.supported && $0.cached && $0.runtimeReady } ? "Ready to create" : "Needs a model"
    }
    private func workspaceDescription(_ page: StudioPage) -> String {
        switch page {
        case .audio: return "Turn a script into spoken audio."
        case .transcription: return "Turn recordings into text and subtitles."
        case .image: return "Generate images from your prompts."
        case .music: return "Create music and musical assets."
        case .video: return "Generate video clips."
        case .chat: return "Work with local language models."
        default: return ""
        }
    }

    private var selectedChatModel: NativeModel? { workspace.models.first { $0.repo == workspace.chatModel && $0.function == .chat } }

    private var chatWorkspace: some View {
        VStack(alignment: .leading, spacing: 20) {
            heading("Chat Studio", "Work with a language model running on this Mac.")
            if !connected { setup }
            HSplitView {
                // The form, on the left, in the same shape as the Image page: the box, the action
                // that acts on it, then a divider and the model it will run on. Send used to sit
                // *below* the model picker, so the one control this page is built around was
                // pushed into the middle of the column with the picker between it and the message
                // it sends.
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            Text("Message").font(.headline)
                            Spacer()
                            Button("Clear") { workspace.clearChat() }
                                .disabled(workspace.chatTurns.isEmpty || workspace.submittingChat)
                        }
                        TextEditor(text: $workspace.chatPrompt)
                            .font(.body).padding(8).frame(height: 155)
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Chat message")
                        HStack {
                            if workspace.submittingChat { ProgressView().controlSize(.small) }
                            Spacer()
                            Button("Send") { Task { await workspace.sendChat() } }
                                .buttonStyle(.borderedProminent)
                                .disabled(!connected || workspace.submittingChat
                                          || workspace.chatPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                          || selectedChatModel?.supported != true
                                          || selectedChatModel?.cached != true
                                          || selectedChatModel?.runtimeReady != true)
                        }
                        Divider().padding(.top, 8)
                        // The same picker the other four pages use. The page used to name one model
                        // in its copy and offer no chooser at all, so a second listed Chat model
                        // could not be selected.
                        modelPicker(.chat)
                        // Everything below belongs to the model above it, which is why it sits
                        // under the picker rather than under the button: a paragraph of warning
                        // text floating beneath Send read as a comment on the message, not on the
                        // model that cannot answer it.
                        if selectedChatModel?.supported != true || selectedChatModel?.cached != true
                            || selectedChatModel?.runtimeReady != true {
                            // The sentence names the model that is actually selected, or says none
                            // is; it used to name one model whatever the selection was.
                            Text(selectedChatModel == nil
                                 ? "No Chat model is listed by the Hub yet. Install the local Chat model and start its worker."
                                 : "\(selectedChatModel!.label) is not ready. Install it in Models and start the local Chat worker.")
                                .font(.callout).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let reason = selectedChatModel?.blockReason {
                            Label(reason, systemImage: "exclamationmark.triangle")
                                .font(.callout).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let warning = selectedChatModel?.memoryWarning {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                                Text(warning).font(.callout).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }.padding(.trailing, 20).padding(.bottom, 20)
                }.frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
                // What you said and what the model answered, on the right, in the house shape the
                // Image preview and the Audio and Subtitle histories use: a fixed heading row, then
                // the content under it. The heading used to be the first line *inside* the
                // ScrollView, so it scrolled away with the conversation and never lined up with the
                // other pages' heading rows.
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Conversation").font(.headline)
                        Spacer()
                    }
                    if workspace.chatTurns.isEmpty {
                        // The house empty state: symbol, title and one line, centred, exactly as an
                        // empty Preview or an empty Recent audio pane reads. A lone sentence at the
                        // top of a large empty pane looked like a rendering fault rather than an
                        // invitation, and no other result pane does that.
                        empty("No messages yet", "The first prompt starts the conversation.",
                              symbol: "bubble.left.and.bubble.right")
                    } else {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 14) {
                                ForEach(workspace.chatTurns) { turn in
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(turn.role == "user" ? "You" : "Model")
                                            .font(.caption).foregroundStyle(.secondary)
                                        Text(turn.content).textSelection(.enabled)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(10)
                                    .background(Color(nsColor: .controlBackgroundColor))
                                    .clipShape(RoundedRectangle(cornerRadius: 6))
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    }
                }
                .padding(.leading, 20)
                // The same house frame as the other three workspace pages, so a result pane cannot
                // drift apart one fix at a time.
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
    }

    private var imageWorkspace: some View {
        VStack(alignment: .leading, spacing: 20) {
            heading("Image Studio", "Describe an image, choose your settings and generate it locally.")
            if !connected { setup }
            HSplitView {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            Text("Prompt").font(.headline)
                            Spacer()
                            sampleControl(Self.imageSamples, binding: $workspace.imagePrompt)
                        }
                        TextEditor(text: $workspace.imagePrompt)
                            .font(.body).padding(8).frame(height: 155)
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Image prompt")
                        HStack {
                            activeWorkIndicator(activeImages)
                            Spacer()
                            ActiveAura(active: !activeImages.isEmpty || workspace.submittingImage) {
                                Button("Generate Image") { selectedImageID = nil; Task { await workspace.generateImage() } }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(!connected || workspace.submittingImage || workspace.imagePrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selectedImageModel?.supported != true || selectedImageModel?.runtimeReady != true || selectedImageModel?.cached != true)
                            }
                        }
                        Divider()
                        modelPicker(.image)
                        if let warning = selectedImageModel?.memoryWarning {
                            // Advice, not a refusal: this Mac is below the tier FLUX is
                            // qualified at, and the operator decides whether to run it here.
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                                Text(warning).font(.callout).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        if let configuration = workspace.imageConfiguration, workspace.imageSettings != nil {
                            imageControls(configuration)
                            Button("Restore Generation Defaults") { workspace.restoreImageDefaults() }
                        }
                        Button("Manage Image Models") { modelFilter = "Image"; studio.select(.models) }
                    }.padding(.trailing, 20).padding(.bottom, 20)
                }.frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Preview").font(.headline)
                        Spacer()
                        if let job = previewImageJob { Button("Save Image…") { saveImage(job) }.disabled(!connected || loadingImage) }
                    }
                    ActiveAura(active: !activeImages.isEmpty) {
                        Group {
                            if loadingImage { ProgressView("Loading image…").frame(maxWidth: .infinity, maxHeight: .infinity) }
                            else if let imagePreview {
                                Image(nsImage: imagePreview).resizable().scaledToFit()
                                    .accessibilityLabel(previewImageJob?.text ?? "Generated image")
                                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                            } else { empty("Your image will appear here", "Enter a prompt and generate an image. Completed images stay in Jobs.", symbol: "photo") }
                        }.frame(minHeight: 200).background(Color(nsColor: .controlBackgroundColor))
                    }
                    if let job = previewImageJob { Text(job.text).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                    Divider()
                    HStack { Text("Recent images").font(.headline); Spacer(); Button("All Jobs") { studio.select(.jobs) } }
                    ScrollView { LazyVStack(alignment: .leading, spacing: 12) { ForEach(workspace.imageJobs.prefix(5)) { imageJobRow($0) } } }
                        .frame(minHeight: 80, maxHeight: 150)
                }
                .padding(.leading, 20)
                // The same house frame as Audio and Subtitle, so the three workspace pages cannot
                // drift apart one fix at a time.
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }.padding(28)
        .onAppear { seedSample(Self.imageSamples, binding: $workspace.imagePrompt, seeded: $seededImageSample) }
        .task(id: previewImageJob?.id) {
            imagePreview = nil
            guard let job = previewImageJob else { return }
            loadingImage = true
            defer { loadingImage = false }
            do {
                let data = try await workspace.imageData(for: job)
                guard !Task.isCancelled else { return }
                guard let image = NSImage(data: data) else { throw CocoaError(.fileReadCorruptFile) }
                imagePreview = image
            } catch { if !Task.isCancelled { fileActionError = error.localizedDescription } }
        }
    }

    /// The controls this model publishes, and only those.
    ///
    /// A second engine of the same modality can publish a different control set, so each control
    /// appears only when the model's own entry names a default or a bound for it. A control that
    /// is not shown is not sent either, and the engine fills its own default — the alternative was
    /// to invent a number the model never named.
    @ViewBuilder
    private func imageControls(_ configuration: NativeImageConfiguration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Picker("Image size", selection: Binding(
                get: { configuration.sizes.first { $0.width == workspace.imageSettings?.width && $0.height == workspace.imageSettings?.height }?.id ?? "" },
                set: { id in
                    guard let size = configuration.sizes.first(where: { $0.id == id }) else { return }
                    workspace.imageSettings?.width = size.width; workspace.imageSettings?.height = size.height
                })) {
                    ForEach(configuration.sizes) { size in Text("\(size.label) · \(size.width) × \(size.height)").tag(size.id) }
                }
            if let field = configuration.stepsField, let range = configuration.stepsRange {
                NumberField(title: "Steps",
                            value: Binding(get: { workspace.imageSettings?.steps ?? field },
                                           set: { workspace.imageSettings?.steps = $0 }),
                            range: range)
            }
            if let field = configuration.guidanceField, let range = configuration.guidanceRange {
                HStack {
                    Text("Guidance")
                    Spacer()
                    TextField("Guidance", value: Binding(get: { workspace.imageSettings?.guidance ?? field },
                                                         set: { workspace.imageSettings?.guidance = $0 }),
                              format: .number)
                        .textFieldStyle(.roundedBorder).frame(width: 95)
                    Text("\(range.lowerBound, specifier: "%g")–\(range.upperBound, specifier: "%g")")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if let field = configuration.seedField {
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text("Seed")
                        Spacer()
                        TextField("Seed", value: Binding(get: { workspace.imageSettings?.seed ?? field },
                                                         set: { workspace.imageSettings?.seed = $0 }),
                                  format: .number.grouping(.never))
                            .textFieldStyle(.roundedBorder).frame(width: 145)
                    }
                    Text("Use −1 for a new random seed.").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func imageJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 10) {
                if job.state == "done" {
                    JobThumbnail(job: job, connected: connected, store: thumbnails, workspace: workspace)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(2)
                    HStack(spacing: 8) {
                        stateBadge(job.state)
                        Text("\(modelName(job.model))\(job.generationSuffix)")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    jobProgress(job)
                    if job.state == "done", !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Result details: \(job.assetFacts)")
                    }
                }
                Spacer()
                if job.state == "done" {
                    Button("Preview") { selectedImageID = job.id; studio.select(.image) }
                    Button("Save…") { saveImage(job) }.disabled(!connected)
                } else if job.state == "uncertain" {
                    Button("Check Original Result") { Task { await workspace.reconcileImage(job) } }.disabled(!connected)
                    Button("Discard…", role: .destructive) { requestDiscard(job, kind: .image) }.disabled(!connected)
                } else if active(job.state) {
                    Button("Cancel") { Task { await workspace.cancelImage(job) } }.disabled(!connected)
                }
            }
            if let message = job.error, !message.isEmpty { Text(message).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
        }
    }

    /// Music generation, laid out exactly like Image: prompt and settings on the left, the result
    /// and the studio's own history on the right.
    private var musicWorkspace: some View {
        VStack(alignment: .leading, spacing: 20) {
            heading("Music Studio", "Describe the music, choose its settings and generate it locally.")
            if !connected { setup }
            HSplitView {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            Text("Prompt").font(.headline)
                            Spacer()
                            sampleControl(Self.musicSamples, binding: $workspace.musicPrompt)
                        }
                        TextEditor(text: $workspace.musicPrompt)
                            .font(.body).padding(8).frame(height: 155)
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Music prompt")
                        HStack {
                            Text("\(workspace.musicPrompt.count) characters").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            activeWorkIndicator(activeMusic)
                            ActiveAura(active: !activeMusic.isEmpty || workspace.submittingMusic) {
                                Button("Generate Music") { selectedMusicID = nil; Task { await workspace.generateMusic() } }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(musicBlockedReason != nil)
                            }
                        }
                        // A disabled prominent button with no explanation is the worst state a form
                        // can be in: whatever holds it up is named right below it, first thing first.
                        if let reason = musicBlockedReason {
                            Label(reason, systemImage: "exclamationmark.circle")
                                .font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider().padding(.top, 8)
                        Text("Generation").font(.headline)
                        modelPicker(.music)
                        if let warning = selectedMusicModel?.memoryWarning {
                            // Advice, not a refusal: the operator decides whether to run it here.
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                                Text(warning).font(.callout).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        if let configuration = workspace.musicConfiguration, let values = workspace.musicSettings {
                            musicControls(configuration, values)
                            Button("Restore Music Defaults") { workspace.restoreMusicDefaults() }
                        } else if selectedMusicModel?.supported == true {
                            Label("This model does not publish generation controls this app can show.",
                                  systemImage: "exclamationmark.triangle")
                                .font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let note = workspace.musicNote {
                            Label(note, systemImage: "exclamationmark.circle")
                                .font(.callout).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider().padding(.top, 8)
                        Text("Model installation and storage are managed centrally.").font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Manage Music Models") { modelFilter = "Music"; studio.select(.models) }
                    }.padding(.trailing, 20).padding(.bottom, 20)
                }.frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Preview").font(.headline)
                        Spacer()
                        if player.playing { Button("Stop Playback") { player.stop() } }
                        if let job = previewMusicJob { Button("Save Audio…") { saveMusic(job) }.disabled(!connected) }
                    }
                    ActiveAura(active: !activeMusic.isEmpty) {
                        Group {
                            if let job = previewMusicJob {
                                VStack(spacing: 12) {
                                    Image(systemName: "music.note").font(.system(size: 32, weight: .light)).foregroundStyle(.secondary)
                                    Text(job.text.isEmpty ? modelName(job.model) : job.text)
                                        .font(.headline).multilineTextAlignment(.center).lineLimit(3)
                                    if let length = clipLength(job) {
                                        Text("Clip length \(length)").font(.caption).foregroundStyle(.secondary)
                                    }
                                    if !job.assetFacts.isEmpty {
                                        Text(job.assetFacts).font(.caption).foregroundStyle(.secondary)
                                            .accessibilityLabel("Result details: \(job.assetFacts)")
                                    }
                                    musicTransport(job)
                                }.frame(maxWidth: .infinity, maxHeight: .infinity)
                            } else {
                                empty("Your music will appear here", "Describe the music and generate it. Completed music stays in Jobs.", symbol: "music.note")
                            }
                        }.frame(minHeight: 200).background(Color(nsColor: .controlBackgroundColor))
                    }
                    Divider()
                    HStack { Text("Recent music").font(.headline); Spacer(); Button("All Jobs") { studio.select(.jobs) } }
                    if workspace.musicJobs.isEmpty {
                        Text("Completed music will appear here.").foregroundStyle(.secondary).font(.callout)
                        Spacer(minLength: 0)
                    } else {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 12) {
                                ForEach(workspace.musicJobs.prefix(5)) { musicJobRow($0) }
                            }
                        }
                        .frame(minHeight: 80, maxHeight: 170)
                    }
                }
                .padding(.leading, 20)
                // The same house frame as Audio, Subtitle and Image, so the four cannot drift apart.
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }.padding(28)
        .onAppear { seedSample(Self.musicSamples, binding: $workspace.musicPrompt, seeded: $seededMusicSample) }
        .onChange(of: previewMusicJob?.id) { _, _ in
            // A different clip is showing, so the transport must not pretend to control the old one.
            player.stop()
            loadedMusicJobID = nil
        }
    }

    /// A conventional transport for the previewed Music clip: play/pause, a stop, a scrub bar and
    /// the elapsed time of the audio actually loaded. Nothing starts on its own.
    @ViewBuilder private func musicTransport(_ job: NativeJob) -> some View {
        let loaded = loadedMusicJobID == job.id && player.hasAudio
        VStack(spacing: 6) {
            HStack(spacing: 12) {
                Button {
                    if loaded && player.playing { player.pause() }
                    else if loaded { player.resume() }
                    else { startPreviewMusic(job) }
                } label: {
                    Label(loaded && player.playing ? "Pause" : "Play",
                          systemImage: loaded && player.playing ? "pause.fill" : "play.fill")
                }
                .disabled(!connected)
                .accessibilityLabel(loaded && player.playing
                                    ? "Pause \(job.text.isEmpty ? "generated music" : job.text)"
                                    : "Play \(job.text.isEmpty ? "generated music" : job.text)")
                Button {
                    player.stop()
                    loadedMusicJobID = nil
                } label: {
                    Label("Stop", systemImage: "stop.fill")
                }
                .disabled(!loaded)
                Spacer()
                Text(loaded ? player.timeLabel : "0:00 / 0:00")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Elapsed and total time")
            }
            Slider(value: Binding(get: { loaded ? player.position : 0 },
                                  set: { player.seek(to: $0) }),
                   in: 0...max(loaded ? player.duration : 1, 0.001))
                .disabled(!loaded)
                .accessibilityLabel("Playback position")
        }
    }

    private func startPreviewMusic(_ job: NativeJob) {
        Task {
            do {
                try await workspace.playMusic(job, with: player)
                loadedMusicJobID = job.id
            } catch { fileActionError = error.localizedDescription }
        }
    }

    /// The Music controls the Hub publishes for this model, and nothing else.
    ///
    /// Each control appears only when this model's own entry names a default or a bound for it:
    /// a second Music engine that publishes fewer controls still gets a usable form, and a control
    /// this model never named is left out of the request for the worker to default itself.
    @ViewBuilder
    private func musicControls(_ configuration: NativeMusicConfiguration, _ values: NativeMusicSettings) -> some View {
        // One validated binding for both the field and the stepper: the field refuses a value
        // outside the model's published range and falls back to the current value, so a typo can
        // never leave the form in a state the Hub would reject.
        let duration = Binding<Double>(
            get: { workspace.musicSettings?.durationSeconds ?? values.durationSeconds },
            set: { proposed in
                guard proposed.isFinite,
                      configuration.durationRange?.contains(proposed) ?? true else { return }
                workspace.musicSettings?.durationSeconds = proposed
            })
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 6) {
                Text("Duration")
                Spacer(minLength: 8)
                TextField("Duration", value: duration, format: .number.precision(.fractionLength(0)))
                    .textFieldStyle(.roundedBorder)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 80)
                    .accessibilityLabel("Duration in seconds")
                Text("s").foregroundStyle(.secondary)
                if let range = configuration.durationRange {
                    Stepper("Duration", value: duration, in: range, step: 5).labelsHidden()
                }
            }
            if let field = configuration.stepsField {
                NumberField(title: "Steps",
                            value: Binding(get: { workspace.musicSettings?.steps ?? field },
                                           set: { workspace.musicSettings?.steps = $0 }),
                            range: configuration.stepsRange ?? field...field)
            }
            if let field = configuration.guidanceField {
                HStack {
                    Text("Guidance")
                    Spacer()
                    TextField("Guidance", value: Binding(get: { workspace.musicSettings?.guidanceScale ?? field },
                                                         set: { workspace.musicSettings?.guidanceScale = $0 }),
                              format: .number)
                        .textFieldStyle(.roundedBorder).frame(width: 95)
                }
            }
            if let field = configuration.shiftField {
                HStack {
                    Text("Shift")
                    Spacer()
                    TextField("Shift", value: Binding(get: { workspace.musicSettings?.shift ?? field },
                                                      set: { workspace.musicSettings?.shift = $0 }),
                              format: .number)
                        .textFieldStyle(.roundedBorder).frame(width: 95)
                }
            }
            if let field = configuration.seedField {
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text("Seed")
                        Spacer()
                        TextField("Seed", value: Binding(get: { workspace.musicSettings?.seed ?? field },
                                                         set: { workspace.musicSettings?.seed = $0 }),
                                  format: .number.grouping(.never))
                            .textFieldStyle(.roundedBorder).frame(width: 145)
                    }
                    Text("Use −1 for a new random seed.").font(.caption).foregroundStyle(.secondary)
                }
            }
            // The vocal controls exist only for a model whose entry publishes them. Stable Audio 3
            // is instrumental only and its catalogue entry omits "lyrics"; a second engine that
            // publishes no vocal fields must not be offered a toggle that sends nothing.
            let supportsVocals = configuration.defaults.instrumental != nil
                || configuration.defaults.lyrics != nil
                || (selectedMusicModel?.capabilities.contains("lyrics") ?? false)
            if supportsVocals {
                Toggle("Instrumental", isOn: Binding(
                    get: { workspace.musicSettings?.instrumental ?? configuration.defaults.instrumental ?? true },
                    set: { workspace.musicSettings?.instrumental = $0 }))
                    .toggleStyle(.checkbox)
                if workspace.musicSettings?.instrumental != true {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Lyrics").font(.callout)
                        TextField("One line per lyric", text: Binding(
                            get: { workspace.musicSettings?.lyrics ?? configuration.defaults.lyrics ?? "" },
                            set: { workspace.musicSettings?.lyrics = $0 }), axis: .vertical)
                            .lineLimit(3...6).textFieldStyle(.roundedBorder)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Vocal language").font(.callout)
                        TextField("Language code, e.g. en", text: Binding(
                            get: { workspace.musicSettings?.vocalLanguage ?? configuration.defaults.vocalLanguage ?? "" },
                            set: { workspace.musicSettings?.vocalLanguage = $0 }))
                            .textFieldStyle(.roundedBorder)
                    }
                }
            } else {
                Text("This model is instrumental only — it has no vocal controls.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let rate = selectedMusicModel?.sampleRateHz, let channels = selectedMusicModel?.channels {
                Text("Output: WAV · \(channels) channel\(channels == 1 ? "" : "s") · \(rate) Hz")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// Why Generate Music cannot run yet, or nil when it can. One rule, read by both the button and
    /// the message under it, so the control and the explanation can never disagree.
    private var musicBlockedReason: String? {
        if !connected { return "Start the local services first." }
        if workspace.submittingMusic { return "Generating. One job at a time." }
        if workspace.musicPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Describe the music you want."
        }
        if workspace.musicPrompt.count > 4_000 { return "The prompt exceeds the 4,000-character limit." }
        guard let model = selectedMusicModel else { return "Choose a model." }
        if let reason = model.blockReason { return reason }
        if model.supported != true { return model.supportNote }
        if model.cached != true { return "Download \(model.label) on the Models page first." }
        if model.runtimeReady != true {
            return "\(model.label) is installed, but its local runtime is unavailable."
        }
        guard let settings = workspace.musicSettings, workspace.musicConfiguration?.accepts(settings) == true else {
            return "Choose settings within the model's supported ranges, or restore its defaults."
        }
        return nil
    }

    private func musicJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: active(job.state) ? "music.note" : job.state == "done"
                      ? "checkmark.circle" : "exclamationmark.circle")
                    .foregroundStyle(stateColor(job.state))
                VStack(alignment: .leading, spacing: 5) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(2)
                    HStack(spacing: 8) {
                        stateBadge(job.state)
                        Text("\(modelName(job.model))\(job.generationSuffix)")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    jobProgress(job)
                    if job.state == "done", !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Result details: \(job.assetFacts)")
                    }
                    // Some Music receipts carry no measured `audio_duration_s`. Rather than show
                    // nothing, name the requested length - and say that is what it is.
                    if job.state == "done", job.durationSeconds == nil,
                       let requested = job.musicSettings?.durationSeconds, requested > 0 {
                        Text("Clip length \(NativeJob.clock(requested)) requested")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if job.state == "done" {
                    Button("Preview") { selectedMusicID = job.id; studio.select(.music) }
                    Button("Play") {
                        Task { do { try await workspace.playMusic(job, with: player) } catch { fileActionError = error.localizedDescription } }
                    }.disabled(!connected)
                    Button("Save…") { saveMusic(job) }.disabled(!connected)
                } else if job.state == "uncertain" {
                    Button("Check Original Result") { Task { await workspace.reconcileMusic(job) } }.disabled(!connected)
                    Button("Discard…", role: .destructive) { requestDiscard(job, kind: .music) }.disabled(!connected)
                } else if active(job.state) {
                    Button("Cancel") { Task { await workspace.cancelMusic(job) } }.disabled(!connected)
                }
            }
            if let message = job.error, !message.isEmpty { Text(message).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
        }
    }

    private var audioWorkspace: some View {
        VStack(alignment: .leading, spacing: 20) {
            heading("Audio", "Write a script, choose a voice and generate spoken audio.")
            if !connected { setup }
            HSplitView {
                // The form, on the left, in the same shape as the Image page: everything you set
                // up, packed from the top, scrolling if it is taller than the window. The Script
                // box used to be its own column with the controls opposite it, which is why every
                // control on this page was once clipped and why the page never matched the others.
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            Text("Script").font(.headline)
                            Spacer()
                            sampleControl(Self.audioSamples, binding: $workspace.audioText)
                        }
                        TextEditor(text: $workspace.audioText)
                            .font(.body).padding(8).frame(height: 155)
                            .background(Color(nsColor: .textBackgroundColor))
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Audio script")
                        HStack {
                            Text("\(workspace.audioText.count) characters").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            activeWorkIndicator(activeAudio)
                            ActiveAura(active: !activeAudio.isEmpty || workspace.submittingAudio) {
                                Button("Generate Audio") { Task { await workspace.generateAudio() } }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(audioBlockedReason != nil)
                            }
                        }
                        // A disabled prominent button with no explanation is the worst state a form
                        // can be in: the operator can see the control, cannot use it, and has
                        // nothing to act on. Whatever holds it up is named here, first thing first.
                        if let reason = audioBlockedReason {
                            Label(reason, systemImage: "exclamationmark.circle")
                                .font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider().padding(.top, 8)
                        Text("Generation").font(.headline)
                        voiceModeTabs
                        modelPicker(.audio)
                        if workspace.audioMode == .cloning {
                            // Cloning is library-only: the voice comes from the Voices page and is
                            // chosen here. There is no loose-clip path on this page any more, so
                            // the picker and the buttons that lead to where a voice is added and
                            // managed are the whole control.
                            voiceSourcePicker
                            if let voice = workspace.selectedLibraryVoice, voice.readyForCloning {
                                // A saved voice carries its own clip, words and timings, so there is
                                // nothing left to choose or prepare.
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("Ready to clone").font(.callout)
                                    Text("This voice's clip, its transcript and its timing are already stored with it. Type your script and press Generate Audio.")
                                        .font(.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            } else if workspace.selectedLibraryVoice != nil {
                                // Chosen, but not usable: say so here rather than in the "Ready to
                                // clone" panel, which would be a claim about a voice that cannot be
                                // cloned from at all.
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("Not ready to clone").font(.callout)
                                    Text("This saved voice has no transcript yet, so a clone cannot align its audio. Add the words on the Voices page, or choose another voice.")
                                        .font(.caption).foregroundStyle(.orange)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        } else if workspace.audioMode == .preset {
                            // The roster belongs to the model, not to the page: a preset family that
                            // publishes no list (Khmer VITS has a single built-in voice) must not be
                            // shown another family's voice names, and must not be told it has a list
                            // it has not reported.
                            let speakers = selectedAudioModel?.speakers ?? []
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Voice").font(.callout)
                                if speakers.isEmpty {
                                    Text(selectedAudioModel?.publishesPresetRoster == true
                                         ? "This model has not reported its built-in voices yet. Press Generate Audio once the model is installed and the runtime is ready, or choose another model above."
                                         : "This model has one built-in voice. There is nothing to choose or prepare.")
                                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                                } else {
                                    Picker("Voice", selection: $workspace.audioSpeaker) {
                                        ForEach(speakers, id: \.self) { Text($0).tag($0) }
                                    }.labelsHidden()
                                    Text("These voices are built into the model. Nothing to choose or prepare.").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        } else {
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Text("Voice description").font(.callout)
                                    Spacer()
                                    Menu("Examples") {
                                        ForEach(Self.designExamples, id: \.self) { example in
                                            Button(example) { workspace.audioDesignPrompt = example }
                                        }
                                    }.font(.caption)
                                }
                                TextField("For example: a deep, slow and warm narrator",
                                          text: $workspace.audioDesignPrompt, axis: .vertical)
                                    .lineLimit(2...4).textFieldStyle(.roundedBorder)
                                Text("The model creates a voice from these words. Nothing is saved to your voice library, so the same description will not sound identical twice.")
                                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Language").font(.callout)
                            TextField("Language code, e.g. en", text: $workspace.audioLanguage).textFieldStyle(.roundedBorder)
                        }
                        speedControl
                        Divider().padding(.top, 8)
                        Text("Model installation and storage are managed centrally.").font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Manage Audio Models") { modelFilter = "Audio"; studio.select(.models) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.trailing, 20).padding(.bottom, 20)
                }
                .frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
                // What you made, on the right, exactly as the Image page shows its preview.
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Recent audio").font(.headline)
                        Spacer()
                        if player.playing { Button("Stop Playback") { player.stop() } }
                        Button("All Jobs") { studio.select(.jobs) }
                    }
                    ActiveAura(active: !activeAudio.isEmpty || workspace.submittingAudio) {
                        Group {
                            if workspace.audioJobs.isEmpty {
                                empty("Your audio will appear here", "Generated audio collects here, newest first.", symbol: "waveform")
                            } else {
                                ScrollView {
                                    LazyVStack(alignment: .leading, spacing: 12) {
                                        ForEach(workspace.audioJobs.prefix(5)) { jobRow($0, compact: true) }
                                    }
                                }
                                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                            }
                        }
                    }
                }
                .padding(.leading, 20)
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }.padding(28)
        .onAppear { seedSample(Self.audioSamples, binding: $workspace.audioText, seeded: $seededAudioSample) }
    }

    /// Where the voice for a clone comes from: the library on the Voices page, and nowhere else.
    ///
    /// The app has had a voice library since the Voices page existed, and the engine has always
    /// accepted a saved voice by its id, but the Audio page once offered only a file chooser - so a
    /// voice the operator could see in **Voices**, ready to clone, could not be picked where they
    /// generate. The picker is that missing control; the two buttons beside it are the only other
    /// thing this control needs, which is the page where a voice is added or managed.
    private var voiceSourcePicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Voice").font(.callout)
                Spacer()
                // Both lead to the one place a voice is added, edited or removed. With no
                // loose-clip path left, reaching that page is the only other thing this control
                // can need to do.
                Button("Add Voice…") { studio.select(.voices) }
                    .font(.caption).fixedSize()
                    .help("Add a reference clip and its transcript on the Voices page")
                Button("Manage Voices") { studio.select(.voices) }
                    .font(.caption).fixedSize()
            }
            Picker("Voice", selection: Binding(
                get: { workspace.audioVoiceLibraryID ?? "" },
                set: { workspace.audioVoiceLibraryID = $0.isEmpty ? nil : $0 })) {
                Text("Choose a saved voice…").tag("")
                // Every saved voice is listed, including the ones that cannot be cloned from yet.
                // Filtering them out left a picker that was empty for a reason nothing on screen
                // named; a voice with no words is a thing the operator can fix, and it cannot be
                // fixed while it is invisible. Choosing one is allowed — it stays selected and the
                // reason is stated underneath — exactly the way an unusable model stays in the
                // model picker with its own reason.
                ForEach(workspace.voices) { voice in
                    Text(voice.name + "  ·  " + voice.durationLabel
                         + (voice.readyForCloning ? "" : "  ·  needs a transcript"))
                        .tag(voice.id)
                }
            }
            .labelsHidden()
            .frame(maxWidth: .infinity)
            // Disabled only when there is nothing at all to choose. A disabled control with no
            // visible reason is the worst state a form can be in, so the line below always says
            // which state the library is in: reading, empty, unreadable, or short of transcripts.
            .disabled(workspace.voices.isEmpty)
            Text(workspace.voicePickerDetail).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            // An empty library, honestly read, offers the one action that fills it. A library that
            // could not be read does not: adding a voice would not fix that.
            if workspace.voicesLoaded, workspace.voices.isEmpty, workspace.voiceError == nil {
                Button("Add Voice…") { studio.select(.voices) }
                    .font(.caption)
            }
        }
    }

    /// Why Generate Audio cannot run yet, or nil when it can.
    ///
    /// One rule, read by both the button and the message under it, so the control and the
    /// explanation can never describe different problems. Ordered by what has to be fixed first:
    /// services, then the script, then the model, then the voice.
    ///
    /// This exists because a disabled prominent button with nothing beside it is the worst state a
    /// form can be in: the operator can see the control, cannot use it, and has nothing to act on.
    /// The owner hit exactly that on a Mac Studio and had to ask what was wrong.
    private var audioBlockedReason: String? {
        if !connected { return "Start the local services first." }
        if workspace.submittingAudio { return "Generating. One job at a time." }
        if workspace.audioText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Write the script you want spoken."
        }
        guard let model = selectedAudioModel else { return "Choose a model." }
        if let reason = model.blockReason { return reason }
        if model.supported != true { return model.supportNote }
        if model.cached != true { return "Download \(model.label) on the Models page first." }
        if model.runtimeReady != true {
            return "\(model.label) is installed, but its local runtime is unavailable."
        }
        // Which form this generation will take is the tab, and the tab is one of the modes the
        // engine published for this model. A model whose engine published none cannot be generated
        // with at all: the app used to guess a form, and the guess is what sent a CustomVoice model
        // down the clone path to be refused for a reference voice it does not have.
        guard model.reportsVoiceModes else {
            return "\(model.label) has not reported which voice modes it supports. Update the local engine, or choose another model."
        }
        guard model.voiceModes.contains(workspace.audioMode) else {
            return "\(model.label) does not publish \(workspace.audioMode.shortTitle). Choose one of its modes."
        }
        switch workspace.audioMode {
        case .cloning:
            // Cloning is library-only, so there are exactly four ways to be held up here, and each
            // one names the next action: a failed read, a read still in flight, no voice chosen,
            // and a chosen voice that cannot be aligned. Nothing here tells the operator to pick a
            // loose recording from disk, because there is no longer one to pick.
            if let voiceError = workspace.voiceError, workspace.voices.isEmpty {
                return "Your saved voices could not be read: \(voiceError) Refresh them on the Voices page."
            }
            if !workspace.voicesLoaded, workspace.voiceError == nil {
                // The library may simply not have been read yet. Saying "no saved voice can be
                // used" while the read is still in flight is a claim about the operator's library
                // that the app has not checked.
                return "Reading your saved voices from the local engine."
            }
            if let voice = workspace.selectedLibraryVoice, !voice.readyForCloning {
                return "\(voice.name) has no transcript yet. Add its words on the Voices page, or choose another voice."
            }
            if workspace.selectedLibraryVoice == nil {
                return workspace.voices.isEmpty
                    ? "No saved voice yet. Add one on the Voices page, then choose it here."
                    : "Choose a saved voice."
            }
        case .design:
            if workspace.audioDesignPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return "Describe the voice you want."
            }
        case .preset:
            // Only a model that publishes a roster can be waiting for one. A single-voice model has
            // nothing to report, so it must never be held back by an empty list that will stay empty.
            if model.publishesPresetRoster, model.speakers.isEmpty {
                return "This model has not reported its built-in voices yet."
            }
        }
        return nil
    }

    /// When the Audio page should (re)load the voice library.
    ///
    /// The connection, and **not** which voice tab is showing. Keying this on `audioMode` meant
    /// every tab change was a new id, and `.task(id:)` cancels its previous task when its id
    /// changes — so switching to Clone (or the mode being reconciled just after the page appeared)
    /// cancelled the read that was in flight. A cancelled read reached the store as
    /// `URLError.cancelled`, the store's catch treated it as a broken library, and the Clone tab's
    /// picker came up empty and disabled while the Voices page listed the same voices. The load
    /// itself is now once per connection (`loadVoicesIfNeeded`), so a tab change neither cancels a
    /// read nor starts another one.
    private var voiceLibraryLoadKey: String {
        "\(connected)/\(workspace.connectionGeneration)"
    }

    /// Speaking pace: one slider, with the engine's own pace as the default.
    ///
    /// The engine applies this as pitch-preserving tempo to the finished audio, so the voice keeps
    /// its identity at every setting - which is why it is a plain slider and not an engine choice.
    /// The range is the audited contract's, taken from the store rather than repeated here.
    private var speedControl: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Speed").font(.callout)
                Spacer()
                if abs(workspace.audioSpeed - 1.0) > 0.001 {
                    Button("Normal") { workspace.audioSpeed = 1.0 }
                        .font(.caption).buttonStyle(.link)
                }
                Text(String(format: "%.2f×", workspace.audioSpeed))
                    .font(.callout.monospacedDigit())
                    .foregroundStyle(abs(workspace.audioSpeed - 1.0) > 0.001 ? .primary : .secondary)
            }
            Slider(value: $workspace.audioSpeed, in: NativeWorkspaceStore.audioSpeedRange, step: 0.05) {
                Text("Speed")
            } minimumValueLabel: {
                Text("0.5×").font(.caption2)
            } maximumValueLabel: {
                Text("2×").font(.caption2)
            }
            .labelsHidden()
            Text(workspace.audioSpeed > 1.0
                 ? "Faster than normal, with the voice unchanged."
                 : workspace.audioSpeed < 1.0
                 ? "Slower than normal, with the voice unchanged."
                 : "The engine's own pace.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    /// A few descriptions that produce a usable voice, so the design tab is not a blank box.
    ///
    /// Offered as examples rather than a fixed list: the model takes free text, and these are
    /// simply the shapes that work - an age or character, a pace, and a tone.
    static let designExamples = [
        "a deep, slow and warm narrator",
        "a bright, quick and friendly young woman",
        "an older man, gravelly and calm, speaking slowly",
        "a clear, neutral voice for reading instructions",
    ]

    /// What the chosen tab does, in the operator's words.
    ///
    /// The preset tab is the one tab whose job depends on the model: a model with a published roster
    /// asks the operator to pick a voice, and a model with a single built-in voice has nothing to
    /// pick. The catalog publishes which is which, so the sentence follows the model rather than
    /// assuming every preset model offers a list.
    private var audioModeDetail: String {
        guard let model = selectedAudioModel else {
            return "Choose a model to see how it can make a voice."
        }
        // The local engine publishes which modes each model supports. Say which of the three
        // states this is - not reported, not this one, or the chosen mode - rather than showing a
        // form the engine never claimed.
        guard model.reportsVoiceModes else {
            return "\(model.label) has not reported which voice modes it supports. Update the local engine, or choose another model."
        }
        guard model.voiceModes.contains(workspace.audioMode) else {
            return "\(model.label) does not publish \(workspace.audioMode.shortTitle). Choose one of its modes above."
        }
        if workspace.audioMode == .preset, !model.publishesPresetRoster {
            return "This model's voice is built in. Type your script and generate."
        }
        return workspace.audioMode.detail
    }

    /// The ways of choosing a voice, as tabs — exactly the modes the local engine published for
    /// the selected model, in the producer's order.
    ///
    /// The three tabs used to be shown for every model, including models whose engine can do only
    /// one of them, so two of the three led to a form the engine would refuse. `modes` is the
    /// runtime's own answer, and a model that publishes two (VoxCPM2 4-bit clones *and* designs)
    /// gets both tabs. A model whose engine published none gets no tabs and a sentence saying so:
    /// guessing a form is how a CustomVoice model was once sent down the clone path.
    private var voiceModeTabs: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let model = selectedAudioModel, model.reportsVoiceModes {
                Picker("Voice", selection: $workspace.audioMode) {
                    ForEach(model.voiceModes) { mode in
                        // Short labels, because a segmented control's minimum width is the sum of
                        // its labels and three long ones could not fit the column - which clipped
                        // the tabs themselves. The full name is in the line underneath and in the
                        // accessibility label, so nothing is lost.
                        Text(mode.shortTitle).tag(mode)
                            .accessibilityLabel(mode.title)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: .infinity)
                .onChange(of: workspace.audioMode) { _, mode in
                    // Keep the selected model consistent with the tab: a model from another tab
                    // would leave the controls below describing a different job than the one on
                    // screen.
                    if !workspace.audioModels(for: mode).contains(where: {
                        $0.repo == workspace.audioModel && $0.supported
                    }) {
                        workspace.audioModel = workspace.audioModels(for: mode).first?.repo ?? ""
                    }
                }
            }
            Text(audioModeDetail).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The selection a Studio's picker edits. Exhaustive over `StudioFunction`, so a new Studio
    /// has to be given a selection here rather than silently editing another Studio's.
    /// The voice types to offer. The three codes the worker itself writes, plus whatever value the
    /// voice being edited already carries: a picker that omitted the stored value would show
    /// nothing selected and quietly rewrite it on save.
    private var voiceGenderChoices: [(code: String, label: String)] {
        let standard: [(code: String, label: String)] = [("n", "Neutral"), ("m", "Male"), ("f", "Female")]
        let current = voiceDraft.gender.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !current.isEmpty, !standard.contains(where: { $0.code == current }) else { return standard }
        return standard + [(current, NativeVoice.genderLabel(for: current))]
    }

    private func modelSelection(_ function: StudioFunction) -> Binding<String>? {
        switch function {
        case .audio: return $workspace.audioModel
        case .image: return $workspace.imageModel
        case .music: return $workspace.musicModel
        case .transcription: return $workspace.transcriptionModel
        case .chat: return $workspace.chatModel
        case .video: return nil   // No engine in this build, so there is nothing to choose.
        }
    }

    @ViewBuilder
    private func modelPicker(_ function: StudioFunction) -> some View {
        // Every page that generates uses this one picker: the Chat page used to draw its own
        // model name instead, so even a second listed Chat model could not be chosen.
        let models = workspace.supportedModels(for: function)
        if let selection = modelSelection(function) {
            VStack(alignment: .leading, spacing: 7) {
                Text("Model").font(.callout)
                Picker("Model", selection: selection) {
                    Text("Choose a model").tag("")
                    ForEach(models) { model in
                        Text(model.pickerName + (model.cached ? "" : " · Not installed") + model.pickerSuffix)
                            .tag(model.repo)
                    }
                }.labelsHidden().frame(maxWidth: .infinity)
                if let model = models.first(where: { $0.repo == selection.wrappedValue }) {
                    Label(model.cached ? "Installed" : "Download in Models", systemImage: model.cached ? "checkmark.circle" : "arrow.down.circle")
                        .font(.caption).foregroundStyle(model.cached ? Color.secondary : Color.orange)
                    // Why this model will not run here, when it will not. The Hub has always checked
                    // five gates before accepting a job - files, runtime, the macOS floor, the chip and
                    // the studio process - and the app used to show only that something was unavailable.
                    if let reason = model.blockReason {
                        Label(reason, systemImage: "exclamationmark.triangle")
                            .font(.caption).foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if models.isEmpty {
                    Text(function == .audio
                         ? (connected ? workspace.audioMode.emptyReason : "Start the services to load available models.")
                         : (connected ? "No compatible model is available for this workspace yet." : "Start the services to load available models."))
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            // A Studio with no picker is named here with the file to update, rather than quietly
            // rendering nothing or, worse, another Studio's list.
            Text("This build has no model picker wired for \(function.rawValue). Add it to `modelSelection(_:)` in native/StudioViews.swift.")
                .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var transcriptionWorkspace: some View {
        VStack(alignment: .leading, spacing: 20) {
            heading("Subtitle Studio", "Import a recording, review its transcript and export text or subtitles.")
            if !connected { setup }
            HSplitView {
                // The form, on the left, in the same shape as the Audio and Image pages. The
                // primary action used to sit at the foot of a flexible column, a long way from the
                // file and format it acts on, with the transcript placeholder floating in between.
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack(spacing: 12) {
                            Image(systemName: "waveform").font(.title2).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(workspace.transcriptionFile?.lastPathComponent ?? "No recording selected").font(.headline).lineLimit(2).textSelection(.enabled)
                                Text("Audio or video file").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Choose File…", action: chooseTranscriptionFile).disabled(workspace.transcribing)
                        }.padding(16).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                        // The label sits above the control rather than beside it: in a form this
                        // narrow a leading label wrapped in half ("Result / format").
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Result format").font(.callout)
                            HStack {
                                Picker("Result format", selection: $transcriptFormat) { Text("Text").tag("Text"); Text("SRT").tag("SRT"); Text("VTT").tag("VTT") }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 240)
                                Spacer()
                                if workspace.transcribing { ProgressView().controlSize(.small) }
                                Button("Transcribe") { Task { await workspace.transcribe() } }.buttonStyle(.borderedProminent)
                                    .disabled(!connected || workspace.transcribing || workspace.transcriptionFile == nil || selectedTranscriptionModel?.cached != true || selectedTranscriptionModel?.supported != true || selectedTranscriptionModel?.runtimeReady != true)
                            }
                        }
                        if workspace.transcribing { transcriptionActivity }
                        Divider().padding(.top, 8)
                        Text("Recognition").font(.headline)
                        modelPicker(.transcription)
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Language").font(.callout)
                            TextField("Auto-detect", text: $workspace.transcriptionLanguage).textFieldStyle(.roundedBorder)
                            Text("Leave blank to detect the language, or enter a code such as en.").font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider().padding(.top, 8)
                        Text("This workspace keeps its recording, model and transcript separate from Audio.").font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Manage Subtitle Models") { modelFilter = "Transcription"; studio.select(.models) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.trailing, 20).padding(.bottom, 20)
                }
                .frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
                // What you made, on the right: the transcript itself, then its history underneath.
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Transcript").font(.headline)
                        if let result = workspace.transcriptionResult {
                            Text("\(result.language) · \(result.duration, specifier: "%.1f") seconds").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Export…", action: exportTranscript).disabled(workspace.transcriptionResult == nil)
                    }
                    ScrollView {
                        if let result = workspace.transcriptionResult {
                            Text(transcriptFormat == "SRT" ? result.srt : transcriptFormat == "VTT" ? result.vtt : result.text)
                                .font(transcriptFormat == "Text" ? .body : .system(.body, design: .monospaced))
                                .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(18)
                        } else { empty("Your transcript", "Choose a recording and transcribe it. Text and subtitle exports will appear here.", symbol: "text.alignleft") }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                    Divider()
                    HStack { Text("Recent subtitles").font(.headline); Spacer(); Button("All Jobs") { studio.select(.jobs) } }
                    if workspace.subtitleJobs.isEmpty {
                        Text("Completed subtitles will appear here.").foregroundStyle(.secondary).font(.callout)
                        Spacer(minLength: 0)
                    } else {
                        // The subtitle row, not the audio one: this panel once drew Play and Save
                        // for a transcript, while the Jobs page correctly offered Open Transcript
                        // for the very same job.
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 12) {
                                ForEach(workspace.subtitleJobs.prefix(5)) { job in subtitleJobRow(job) }
                            }
                        }
                        .frame(minHeight: 60, maxHeight: 170)
                    }
                }
                .padding(.leading, 20)
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }.padding(28)
        // The Audio page reads the voice library itself. Loading it only on the Voices page meant a
        // fresh launch that went straight here showed "No saved voices yet" and a disabled picker
        // while ten voices were sitting on the Mac, and nothing on screen said the list had simply
        // never been fetched. It reads once per connection: the store is what knows whether the
        // list is already in hand, so a visit to this page cannot cancel or repeat a read.
        .task(id: voiceLibraryLoadKey) {
            guard connected else { return }
            await workspace.loadVoicesIfNeeded()
        }
    }

    private var modelsWorkspace: some View {
        VStack(alignment: .leading, spacing: 18) {
            heading("Models", "One library for every workspace. Install models here, then choose them where you create.")
            if !connected { setup }
            HStack {
                TextField("Search models", text: $modelSearch).textFieldStyle(.roundedBorder).frame(maxWidth: 310)
                Picker("Function", selection: $modelFilter) {
                    Text("All functions").tag("All functions")
                    ForEach(StudioFunction.allCases) { Text($0.rawValue).tag($0.rawValue) }
                }.frame(maxWidth: 230)
                Toggle("Installed only", isOn: $installedOnly).toggleStyle(.checkbox)
                Spacer()
                Button("Verify All") { Task { await workspace.verifyInstalledModels() } }
                    .help("Re-check every installed model against its pinned file digests")
                    .disabled(!connected || workspace.updatingModels)
                Button { Task { await workspace.refresh() } } label: { Image(systemName: "arrow.clockwise") }.help("Refresh model library").accessibilityLabel("Refresh model library").disabled(!connected || workspace.refreshing)
            }

            // What is actually on disk, per family, with the memory the model declares. This matters
            // most for models copied in from another install: they are present and would otherwise
            // look runnable on a Mac that cannot hold them.
            if !workspace.storageFamilies.isEmpty {
                let tooBig = workspace.storageFamilies.filter { $0.runsHere == false }
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        Text("Storage cache").font(.callout.weight(.medium))
                        Text("\(workspace.storageFamilies.count) families on disk")
                            .font(.caption).foregroundStyle(.secondary)
                        if !tooBig.isEmpty {
                            Text("· \(tooBig.count) cannot run on this Mac")
                                .font(.caption).foregroundStyle(.orange)
                        }
                    }
                    ForEach(tooBig) { family in
                        Label("\(family.label) needs \(family.requiredGB ?? 0) GB; this Mac has \(family.thisMacGB ?? 0) GB",
                              systemImage: "exclamationmark.triangle")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
            }

            // Above the list, not below the split: a flexible split pushes anything after it to the
            // foot of the window, where a line about the library reads as a stray footer.
            if let note = workspace.modelNote, workspace.modelNoteRepo == nil {
                Text(note).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if workspace.refreshing && workspace.models.isEmpty { ProgressView("Reading model library…").controlSize(.small) }
            else if filteredModels.isEmpty { Text(connected ? "No models match this view. Catalogs for the remaining engines will appear when those engines are integrated." : "Start the local services to inspect the model library.").font(.callout).foregroundStyle(.secondary) }
            HSplitView {
                List(filteredModels, selection: $selectedModel) { model in
                    HStack(spacing: 12) {
                        Image(systemName: model.function.symbol).foregroundStyle(.secondary).frame(width: 22)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(model.label).fontWeight(.medium)
                            Text(model.function.rawValue).font(.caption).foregroundStyle(.secondary)
                            // Scanning the library, "how is this one chunked?" should not need a
                            // click per model: the section size and the speed route are the two
                            // facts that differ between families and decide how a long script runs.
                            if let longForm = model.longForm {
                                Text("Sections of \(longForm.chosenSectionMaxCharacters) chars · speed: \(longForm.speedDelivery == "native" ? "engine" : "post-pass")")
                                    .font(.caption2).foregroundStyle(.tertiary)
                            }
                        }
                        Spacer()
                        if model.paused {
                            Label("Paused", systemImage: "pause.circle.fill")
                                .labelStyle(.titleAndIcon)
                                .font(.callout).foregroundStyle(.orange)
                        }
                        Text(model.cached ? "Installed" : "Not installed").font(.callout).foregroundStyle(.secondary)
                    }.padding(.vertical, 7).tag(model.repo)
                }.listStyle(.inset).frame(minWidth: 380)
                ScrollView {
                    if let model = workspace.models.first(where: { $0.repo == selectedModel }) {
                        VStack(alignment: .leading, spacing: 18) {
                            Text(model.label).font(.title2.weight(.semibold)).textSelection(.enabled)
                            Text(model.repo).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            LabeledContent("Function", value: model.function.rawValue)
                            LabeledContent("Storage", value: model.cached ? "Installed" : "Not installed")
                            if let size = model.sizeGB { LabeledContent("Estimated size", value: String(format: "%.2f GB", size)) }
                            // How a long script is cut up for this model, and how its speed control
                            // reaches the engine. Both are per-family decisions the runtime makes,
                            // and both were previously answerable only by reading the worker source.
                            if let longForm = model.longForm {
                                Divider()
                                Text("Long-form delivery").font(.headline)
                                LabeledContent("Section size", value: "\(longForm.chosenSectionMaxCharacters) characters")
                                if let cap = longForm.hardCapCharacters {
                                    Label("This engine stops reading past \(cap) characters, so sections are held at or below it.",
                                          systemImage: "exclamationmark.triangle")
                                        .font(.caption).foregroundStyle(.orange)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                LabeledContent("Adjustable range", value: "\(longForm.sectionMinimum)–\(longForm.sectionMaximum) characters")
                                LabeledContent("Between sections", value: String(format: "%.0f ms", Double(longForm.joinPauseMilliseconds)))
                                LabeledContent("Speed control", value: longForm.speedDelivery == "native"
                                    ? String(format: "the engine's own pace (%.1f–%.1fx)", longForm.speedMinimum, longForm.speedMaximum)
                                    : String(format: "one pitch-preserving pass after rendering (%.1f–%.1fx)", longForm.speedMinimum, longForm.speedMaximum))
                                Text(longForm.note).font(.caption).foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                Text("Source: \(longForm.source.replacingOccurrences(of: "_", with: " "))")
                                    .font(.caption2).foregroundStyle(.tertiary)
                            }
                            Divider()
                            // The Models page is where an operator comes to find out why something
                            // will not run, so the gate that is shut is named here as well.
                            if let reason = model.blockReason {
                                Label(reason, systemImage: "exclamationmark.triangle")
                                    .font(.callout).foregroundStyle(.orange)
                                    .fixedSize(horizontal: false, vertical: true)
                            } else {
                                Text(model.supportNote).font(.callout).foregroundStyle(.secondary)
                            }
                            // A paused model is refused before dispatch, so no job will ever show
                            // its error. The pause has to be visible and releasable here, where the
                            // operator comes to find out why a model will not run.
                            if model.paused {
                                HStack {
                                    Button(workspace.updatingModels ? "Rechecking…" : "Recheck Readiness") {
                                        Task { await workspace.recheckModel(model) }
                                    }
                                    .help("Ask the Hub to verify this model's files, runtime and cached dependencies, then let it take jobs again")
                                    .disabled(!connected || workspace.updatingModels)
                                    Text("The Hub is holding this model until you confirm it is repaired.")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            if model.cached {
                                // The button opens the page that owns this model's own function.
                                // It used to set the Subtitle selection for every non-audio model,
                                // so an Image or Chat model opened Subtitle with a foreign repo.
                                if model.supported, let page = StudioPage.page(for: model.function) {
                                    Button("Use in \(page.title)") {
                                        workspace.select(model)
                                        studio.select(page)
                                    }.buttonStyle(.borderedProminent)
                                } else if model.supported {
                                    Text("This model belongs to \(model.function.rawValue), which this build has no page for. Add it to `StudioPage` in native/ShellCore.swift.")
                                        .font(.caption).foregroundStyle(.orange)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                HStack {
                                    // Verification is timestamp-sensitive: a library that was
                                    // copied or hard-linked can read as changed even when the
                                    // bytes match, and this is the only remedy.
                                    Button(workspace.updatingModels ? "Verifying…" : "Verify Files") {
                                        Task { await workspace.verifyModel(model) }
                                    }.disabled(!connected || workspace.updatingModels)
                                    Button("Remove Download…") { confirmRemove(model) }.disabled(!connected)
                                }
                            } else {
                                HStack {
                                    Button("Download Model") { Task { await workspace.startDownload(model) } }.buttonStyle(.borderedProminent).disabled(!connected || studio.configuration.offline || !model.supported)
                                    // A folder copied onto this Mac becomes a verified library
                                    // entry without a second download and without a terminal.
                                    Button("Import Folder…") { chooseModelFolder(for: model) }
                                        .disabled(!connected || workspace.updatingModels || !model.supported)
                                }
                                // Say which gate is shut, by name, because a greyed-out button
                                // with no reason is indistinguishable from a missing feature.
                                if studio.configuration.offline {
                                    Text("Downloading is off because Offline mode is on (Settings → Model library → \"Use imported models offline\"). Turn that off to download from Hugging Face, or use Import Folder… to add a copy you already have — that works offline.")
                                        .font(.caption).foregroundStyle(.orange)
                                        .fixedSize(horizontal: false, vertical: true)
                                } else if !model.supported {
                                    Text("This model's runtime entry in the catalog does not match what this form can drive, so it cannot be downloaded here yet.")
                                        .font(.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            // Only a note about this model is shown here; a library-wide
                            // summary would otherwise be repeated under every model as if it
                            // were that model's result.
                            if let note = workspace.modelNote,
                               workspace.modelNoteRepo == nil || workspace.modelNoteRepo == model.repo {
                                if workspace.modelNoteRepo == model.repo {
                                    Text(note).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        }.padding(20)
                    } else { empty("Model details", "Select a model to inspect its status and available actions.", symbol: "externaldrive") }
                }.frame(minWidth: 260, idealWidth: 320, maxWidth: 380)
            }
            if !activeDownloads.isEmpty {
                Divider()
                Text("Downloads").font(.headline)
                ScrollView { VStack(spacing: 12) { ForEach(activeDownloads) { downloadRow($0) } } }.frame(maxHeight: 120)
            }
        }.padding(28)
    }

    private var jobsWorkspace: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                heading("Jobs", "Every local generation in one place, or filtered to a single studio.")
                Picker("History", selection: $historyTab) {
                    ForEach(HistoryTab.allCases) { tab in Text(tab.title).tag(tab) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 420, alignment: .leading)
                .accessibilityLabel("History filter")
                if workspace.transcribing { transcriptionActivity }
                // One section per Studio that has a history, derived from the Studio list rather
                // than written out here: a Studio missing from a hand-written section was simply
                // absent from Jobs with nothing saying so.
                ForEach(workspace.jobHistories, id: \.function) { history in
                    if historyTab == .all || historyTab.studio == history.function {
                        sectionTitle(StudioPage.page(for: history.function)?.title ?? history.function.rawValue)
                        if history.jobs.isEmpty {
                            Text("No \(history.function.rawValue.lowercased()) jobs yet.").foregroundStyle(.secondary)
                        }
                        // Lazy so a row's artifact (an image thumbnail above all) is only fetched
                        // for a row that is actually on screen.
                        LazyVStack(alignment: .leading, spacing: 12) {
                            ForEach(history.jobs) { job in
                                jobRow(job, for: history.function)
                                Divider()
                            }
                        }
                        if history.function == .transcription, let result = workspace.transcriptionResult {
                            HStack { VStack(alignment: .leading, spacing: 5) { Text("Latest transcript").font(.headline); Text(result.text).lineLimit(2).foregroundStyle(.secondary) }; Spacer(); Button("Open Subtitle") { studio.select(.transcription) } }
                        }
                    }
                }
                if historyTab == .all {
                    Divider()
                    sectionTitle("Model downloads")
                    if workspace.downloads.isEmpty { Text("No model downloads.").foregroundStyle(.secondary) }
                    ForEach(workspace.downloads) { downloadRow($0) }
                }
            }.padding(28)
        }
    }
    /// A finished transcript is text, not sound.
    ///
    /// These rows were drawn with the Audio row, which offers Play and "Save…" for a clip. A
    /// subtitle artifact is an SRT, so Play tried to play it and the app reported an audio
    /// failure for a transcript that was perfectly fine. Text gets text actions.
    private func subtitleJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: active(job.state) ? "waveform" : job.state == "done"
                      ? "checkmark.circle" : "exclamationmark.circle")
                    .foregroundStyle(stateColor(job.state))
                VStack(alignment: .leading, spacing: 5) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(3)
                    HStack(spacing: 8) {
                        stateBadge(job.state)
                        Text("\(modelName(job.model))\(job.generationSuffix)")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    jobProgress(job)
                    if job.state == "done" {
                        Text("Transcript").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if job.state == "done" {
                    // Opens the Subtitle page, where the transcript is exported and delivery is
                    // acknowledged - the same path the Subtitle screen already uses.
                    // Loads the transcript from the batch, then shows it. Switching pages alone
                    // left the Subtitle page empty for anything not transcribed in this sitting.
                    Button("Open Transcript") {
                        Task {
                            do {
                                try await workspace.openSubtitle(job)
                                studio.select(.transcription)
                            } catch { fileActionError = error.localizedDescription }
                        }
                    }.disabled(!connected)
                } else if active(job.state) {
                    Button("Cancel") { Task { await workspace.cancelSubtitle(job) } }.disabled(!connected)
                }
            }
            if let error = job.error, !error.isEmpty {
                Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
            }
        }
    }

    /// One history row, drawn by the Studio that produced it. The switch is exhaustive over
    /// `StudioFunction`, so a new Studio has to be given a row here rather than quietly showing
    /// an audio row for something that is not audio.
    @ViewBuilder
    private func jobRow(_ job: NativeJob, for function: StudioFunction) -> some View {
        switch function {
        case .image: imageJobRow(job)
        case .audio: jobRow(job, compact: false)
        case .music: musicJobRow(job)
        case .transcription: subtitleJobRow(job)
        case .chat, .video:
            // No batch history, so `jobHistories` never yields one of these.
            EmptyView()
        }
    }

    private func jobRow(_ job: NativeJob, compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: active(job.state) ? "waveform" : job.state == "done" ? "checkmark.circle" : "exclamationmark.circle")
                    .foregroundStyle(stateColor(job.state))
                VStack(alignment: .leading, spacing: 5) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(compact ? 1 : 3)
                    HStack(spacing: 8) {
                        stateBadge(job.state)
                        Text("\(modelName(job.model))\(job.generationSuffix)")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    jobProgress(job)
                    // What was actually produced: length, size and the size requested. An asset
                    // with no stated facts is hard to tell apart from one that came back wrong.
                    if job.state == "done", !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Result details: \(job.assetFacts)")
                    }
                }
                Spacer()
                if job.state == "done" {
                    Button("Play") { Task { do { try await workspace.play(job, with: player) } catch { fileActionError = error.localizedDescription } } }.disabled(!connected)
                    Button("Save…") { saveAudio(job) }.disabled(!connected)
                } else if job.state == "uncertain" {
                    Button("Discard…", role: .destructive) { requestDiscard(job, kind: .audio) }.disabled(!connected)
                } else if active(job.state) { Button("Cancel") { Task { await workspace.cancelAudio(job) } }.disabled(!connected) }
            }
            if let error = job.error, !error.isEmpty { Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
        }
    }
    private func downloadRow(_ download: NativeDownload) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(modelName(download.repo)).lineLimit(1)
                Spacer()
                Text(stateLabel(download.state)).font(.caption).foregroundStyle(.secondary)
                if active(download.state) { Button("Cancel") { Task { await workspace.cancelDownload(download) } }.disabled(!connected) }
            }
            if active(download.state) {
                if download.percent > 0 || download.totalBytes > 0 {
                    ProgressView(value: min(100, max(0, download.percent)), total: 100)
                } else {
                    // A Hub model download runs in the Hub's own child process, which reports its
                    // state but not its byte counts - so there is no honest percentage to draw, and
                    // a bar pinned at zero reads as "stuck" for the whole download. The size comes
                    // from the model itself, which is the same figure the Models page shows.
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(sizeCaption(for: download))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if let error = download.error, !error.isEmpty { Text(error).font(.caption).foregroundStyle(.red) }
        }
    }
    /// What is known about a download's size, without inventing progress.
    ///
    /// The Hub does not report byte counts for a model download, so the honest line is the model's
    /// own size ("downloading a 12.4 GB model") rather than "0 bytes" or a bar that never moves.
    private func sizeCaption(for download: NativeDownload) -> String {
        if let size = workspace.models.first(where: { $0.repo == download.repo })?.sizeGB {
            return String(format: "Downloading in the background · %.1f GB model", size)
        }
        return "Downloading in the background"
    }

    private var transcriptionActivity: some View {
        HStack(spacing: 12) { ProgressView().controlSize(.small); VStack(alignment: .leading, spacing: 4) { Text("Transcribing recording").font(.headline); Text("You can use other pages while this finishes.").foregroundStyle(.secondary).font(.callout) }; Spacer() }
    }

    // MARK: - Voices

    /// The saved reference clips, which is what makes a clone repeatable.
    ///
    /// The library lives in the Voice worker and predates this app; the app had a per-job
    /// reference field and no way to keep, name, edit or reuse a clip. This is that screen.
    private var voicesWorkspace: some View {
        VStack(alignment: .leading, spacing: 0) {
            heading("Voices", "Saved reference clips. Pick one when cloning so the same voice comes back every time.")
                .padding(28)
            HStack(spacing: 10) {
                Button("Add Voice…") { voiceDraft = VoiceDraft(); showingVoiceEditor = true }
                    .buttonStyle(.borderedProminent)
                    .disabled(!connected || workspace.savingVoice)
                Button("Import Folder…") { chooseVoiceImportFolder() }
                    .disabled(!connected || workspace.savingVoice)
                Button("Refresh") { Task { await workspace.refreshVoices() } }
                    .disabled(!connected)
                if workspace.savingVoice { ProgressView().controlSize(.small) }
                Spacer()
            }.padding(.horizontal, 28)
            if let note = workspace.voiceNote {
                Text(note).font(.callout).foregroundStyle(.secondary).padding(.horizontal, 28).padding(.top, 8)
            }
            if let error = workspace.voiceError {
                Text(error).font(.callout).foregroundStyle(.red).padding(.horizontal, 28).padding(.top, 8)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Divider().padding(.top, 14)
            if !connected {
                empty("Not connected", "Start local services to see and save voices.", symbol: "person.wave.2")
            } else if workspace.voices.isEmpty {
                empty("No saved voices", "Add a reference clip and its transcript, or import a folder you already have. A clone needs both.", symbol: "person.wave.2")
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(workspace.voices) { voice in
                            voiceRow(voice)
                            Divider()
                        }
                    }.padding(.horizontal, 28).padding(.bottom, 28)
                }
            }
        }
        .task(id: connected) { if connected { await workspace.refreshVoices() } }
        .sheet(isPresented: $showingVoiceEditor) { voiceEditor }
    }

    private func voiceRow(_ voice: NativeVoice) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: voice.readyForCloning ? "waveform.circle.fill" : "exclamationmark.circle")
                .foregroundStyle(voice.readyForCloning ? Color.green : Color.orange)
                .font(.title3)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(voice.name).fontWeight(.medium)
                    Text(voice.language).font(.caption).foregroundStyle(.secondary)
                    Text(voice.genderLabel).font(.caption).foregroundStyle(.secondary)
                    if voice.fleetManaged {
                        Text("fleet").font(.caption2).padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Color.secondary.opacity(0.15)).clipShape(Capsule())
                    }
                }
                Text(voice.readyForCloning
                     ? "Transcript saved · \(voice.durationLabel)"
                     : "No transcript yet — a clone cannot align the audio without one")
                    .font(.caption)
                    .foregroundStyle(voice.readyForCloning ? Color.secondary : Color.orange)
                if !voice.notes.isEmpty {
                    Text(voice.notes).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
            Button("Play") { Task { await playVoice(voice) } }
            Button("Use") {
                Task {
                    if await workspace.useVoice(voice) { studio.select(.audio) }
                }
            }
                .disabled(!voice.readyForCloning)
                .help("Use this voice for the next clone")
            Button("Edit…") { voiceDraft = VoiceDraft(voice: voice); showingVoiceEditor = true }
            Button("Remove") { pendingVoiceDeletion = voice }
                .disabled(workspace.savingVoice)
        }.padding(.vertical, 10)
        .confirmationDialog("Remove \(voice.name)?",
                            isPresented: Binding(get: { pendingVoiceDeletion?.id == voice.id },
                                                 set: { if !$0 { pendingVoiceDeletion = nil } }),
                            titleVisibility: .visible) {
            Button("Remove Voice", role: .destructive) {
                let target = voice
                pendingVoiceDeletion = nil
                Task { _ = await workspace.deleteVoice(target) }
            }
            Button("Keep", role: .cancel) { pendingVoiceDeletion = nil }
        } message: {
            Text("The saved clip and its transcript are deleted from this Mac. Generated audio is not affected.")
        }
    }

    /// One editor for both adding and editing, because they collect the same fields and the
    /// only real difference is that editing cannot replace the audio.
    @ViewBuilder private var voiceEditor: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(voiceDraft.voice == nil ? "Add a voice" : "Edit \(voiceDraft.voice!.name)")
                .font(.title2.weight(.semibold))
            Text("A clone matches the clip to the transcript, so the transcript must be exactly what the clip says. A mismatch produces a wrong-sounding voice, which is why both are required.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Form {
                TextField("Name", text: $voiceDraft.name)
                // The worker owns the vocabulary and stores what it is given, so language is free
                // text with the library's own values offered as suggestions. A fixed picker could
                // not tag a Khmer voice `km`; the short list is only what a library that has not
                // reported any language of its own falls back to.
                LabeledContent("Language") {
                    HStack(spacing: 8) {
                        TextField("Language code, e.g. en or km", text: $voiceDraft.language)
                            .textFieldStyle(.roundedBorder)
                        Menu("Known") {
                            ForEach(NativeVoice.languageChoices(published: workspace.voices.map(\.language)), id: \.self) { code in
                                Button(code) { voiceDraft.language = code }
                            }
                        }.frame(width: 90)
                    }
                }
                // The same rule for the voice type: the three codes the worker itself writes are
                // offered, and a value the worker already published that is not one of them is
                // added to the list so it is shown rather than silently replaced.
                Picker("Voice type", selection: $voiceDraft.gender) {
                    ForEach(voiceGenderChoices, id: \.code) { choice in Text(choice.label).tag(choice.code) }
                }
                Picker("Rights", selection: $voiceDraft.license) {
                    ForEach(NativeVoice.licenseChoices(current: voiceDraft.license), id: \.id) { option in
                        Text(option.label).tag(option.id)
                    }
                }
                TextField("Notes (optional)", text: $voiceDraft.notes)
                if voiceDraft.voice == nil {
                    LabeledContent("Reference clip") {
                        HStack(spacing: 8) {
                            Button("Choose Clip…") { chooseVoiceClip() }
                            Text(voiceDraft.clipPath.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "No clip chosen")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                        }
                    }
                }
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("Transcript").font(.callout)
                        Spacer()
                        // The operator has the clip right here; making them go and transcribe it
                        // somewhere else is how a transcript gets skipped, and a clone without
                        // one sounds wrong.
                        if voiceDraft.voice == nil {
                            Button(transcribingClip ? "Transcribing…" : "Transcribe Clip") {
                                Task { await transcribeIntoVoiceDraft() }
                            }
                            .disabled(transcribingClip || voiceDraft.clipPath == nil)
                            if transcribingClip { ProgressView().controlSize(.small) }
                        }
                    }
                    TextEditor(text: $voiceDraft.transcript)
                        .font(.body).frame(height: 70)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                    Text("The exact words spoken in the clip, word for word.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Toggle("I have the rights to clone this voice", isOn: $voiceDraft.acknowledged)
            }.formStyle(.grouped)
            HStack {
                Button("Cancel") { showingVoiceEditor = false }
                Spacer()
                Button(voiceDraft.voice == nil ? "Save Voice" : "Save Changes") {
                    let editing = voiceDraft.voice
                    showingVoiceEditor = false
                    Task {
                        if let editing {
                            _ = await workspace.updateVoice(editing, name: voiceDraft.name, language: voiceDraft.language,
                                                            gender: voiceDraft.gender, license: voiceDraft.license,
                                                            notes: voiceDraft.notes, transcript: voiceDraft.transcript)
                        } else if let path = voiceDraft.clipPath {
                            _ = await workspace.addVoice(name: voiceDraft.name, language: voiceDraft.language,
                                                         gender: voiceDraft.gender, license: voiceDraft.license,
                                                         notes: voiceDraft.notes, transcript: voiceDraft.transcript,
                                                         clip: URL(fileURLWithPath: path),
                                                         acknowledged: voiceDraft.acknowledged)
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(voiceDraft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                          || (voiceDraft.voice == nil && voiceDraft.clipPath == nil)
                          || (voiceDraft.voice == nil && !voiceDraft.acknowledged)
                          || voiceDraft.transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(24).frame(width: 560, height: 620)
    }

    /// Transcribe the clip in the voice editor and put the words in its transcript field.
    ///
    /// It overwrites what is there, because the point of pressing it is to get the real words;
    /// the operator can edit the result before saving.
    private func transcribeIntoVoiceDraft() async {
        guard let path = voiceDraft.clipPath else { return }
        transcribingClip = true
        defer { transcribingClip = false }
        do {
            voiceDraft.transcript = try await workspace.transcribeClip(
                at: URL(fileURLWithPath: path), language: voiceDraft.language)
            workspace.voiceError = nil
        } catch {
            workspace.voiceError = error.localizedDescription
        }
    }

    private func chooseVoiceClip() {
        let panel = NSOpenPanel(); panel.title = "Choose a reference clip"
        panel.allowedContentTypes = [.audio]; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { voiceDraft.clipPath = url.path }
    }

    /// Import voices that already exist as clip-plus-text pairs.
    ///
    /// Written for the Voice Studio library the owner already has: one folder per voice
    /// containing a reference clip and, usually, a transcript. Each pair is added through the
    /// same validated path as a hand-entered voice, so an import cannot put something in the
    /// library that the library itself would have refused.
    private func chooseVoiceImportFolder() {
        let panel = NSOpenPanel(); panel.title = "Choose a folder of voices"
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            Task { await importVoiceFolder(url) }
        }
    }

    private func importVoiceFolder(_ folder: URL) async {
        _ = await workspace.importVoices(from: folder)
    }

    private func playVoice(_ voice: NativeVoice) async {
        do { try await workspace.play(voice, with: player) }
        catch { workspace.voiceError = error.localizedDescription }
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 0) {
            heading("Settings", "App behavior, local storage and service controls.").padding(28)
            Form {
                Section("General") {
                    Toggle("Start at login", isOn: Binding(get: { studio.loginStatus == .enabled }, set: { studio.setLogin($0) }))
                    Text("One login item starts Studio Hub at login and relaunches it if it crashes. A normal Quit leaves it stopped until you open it again or next sign in.")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Toggle("Start services at login", isOn: Binding(
                        get: { studio.configuration.startServicesAtLogin },
                        set: { studio.setStartServicesAtLogin($0) }))
                    Text("Off by default. With this on, signing in brings the Hub and its Studios up on their own, so this Mac is ready for work without anyone opening the app. Leave it off and services start when you open the app or press Start Services.")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Text("Closing the window keeps local jobs running. Quit stops the app and its services.").foregroundStyle(.secondary)
                }
                Section("Model library") {
                    LabeledContent("Location") { Text(studio.configuration.modelsDirectory?.path ?? "Not selected").textSelection(.enabled).multilineTextAlignment(.trailing) }
                    if let warning = studio.libraryPrivacyWarning {
                        Text(warning).font(.callout).foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    HStack { Button("Choose Folder…") { studio.chooseLibrary() }.disabled(!studio.canConfigure); if let url = studio.configuration.modelsDirectory { Button("Show in Finder") { NSWorkspace.shared.open(url) } } }
                    if !studio.canConfigure { Text("Stop services before changing the model folder or offline mode.").font(.callout).foregroundStyle(.secondary) }
                    Toggle("Use imported models offline", isOn: Binding(get: { studio.configuration.offline }, set: { studio.setOffline($0) })).disabled(!studio.canConfigure)
                }
                Section("Model downloads") {
                    LabeledContent("Hugging Face token", value: workspace.tokenStatus.isSet ? workspace.tokenStatus.masked : "Not configured")
                    SecureField("New access token", text: $hfToken)
                    Text("For models that require account access. The app shows only the saved token’s masked value.").font(.callout).foregroundStyle(.secondary)
                    HStack {
                        Button("Test Token") { Task { savingToken = true; defer { savingToken = false }; if let result = await workspace.testHFToken(hfToken) { tokenMessage = "Access verified" + (result.name.map { " for " + $0 } ?? "") } } }.disabled(!connected || hfToken.isEmpty || savingToken || studio.configuration.offline)
                        Button("Save Token") { Task { savingToken = true; defer { savingToken = false }; if await workspace.saveHFToken(hfToken) { hfToken = ""; tokenMessage = "Token saved" } } }.disabled(!connected || hfToken.isEmpty || savingToken)
                        if savingToken { ProgressView().controlSize(.small) }
                    }
                    if let tokenMessage { Text(tokenMessage).font(.callout).foregroundStyle(.secondary) }
                }
                Section("Local services") {
                    LabeledContent("Status", value: studio.status)
                    HStack {
                    if studio.canForceStop { Button("Force Stop…") { studio.confirmForceStop() } }
                    if studio.canCancelRecovery {
                        // The automatic ladder is still running; a manual attempt is offered
                        // throughout and restarts the ladder from the first rung.
                        Button("Start Services Now") { studio.start() }
                            .disabled(!studio.canConfigure || !studio.hasLibrary)
                        Button("Cancel Recovery") { studio.stop() }
                    }
                    else if studio.running { Button(studio.stopping ? "Stopping…" : "Stop Services…") { studio.confirmStop() }.disabled(studio.stopping) }
                        else { Button("Start Services") { studio.start() }.disabled(!studio.canConfigure || !studio.hasLibrary) }
                        Button("Release Model Memory") { Task { await workspace.releaseMemory() } }.disabled(!connected)
                        Button("Open Log") { studio.openLog() }
                    }
                }
                Section("Model memory") {
                    Text("Each Studio keeps at most one model in unified memory. This sets how long an idle model stays loaded, which matters most when several Studios share one Mac.")
                        .font(.callout).foregroundStyle(.secondary)
                    memoryControls
                }
                Section("Local storage") {
                    Text("This counts generated output, not the model library. Finished work is cleaned up on this Mac only; anything still being written, not yet acknowledged, or in use is protected and is never deleted.")
                        .font(.callout).foregroundStyle(.secondary)
                    storageControls
                }
                Section("App data") {
                    LabeledContent("Location") { Text(studio.configuration.dataDirectory.path).textSelection(.enabled).multilineTextAlignment(.trailing) }
                    Button("Show App Data") { NSWorkspace.shared.open(studio.configuration.dataDirectory) }
                    LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development")
                }
            }.formStyle(.grouped)
        }
        .task { if connected { await workspace.refreshMemoryPolicy(); await workspace.refreshStorage() } }
        .sheet(isPresented: $showingStorageEditor) { storageEditor }
    }

    /// Editing is a sheet so the saved values stay visible behind it: the operator can see what
    /// the limits are now while deciding what to change them to.
    private var storageEditor: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Local storage limits").font(.title2.weight(.semibold))
            Text("These apply to this Mac only. Protected work is never deleted, so lowering the limit can leave this Mac above it.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Form {
                DoubleField(title: "Keep at most (GB)", value: $draftGigabytes, range: 1...2000, step: 1)
                NumberField(title: "Keep finished work (days)", value: $draftRetentionDays, range: 1...365)
                Toggle("Clean up automatically", isOn: $draftCleanup)
            }.formStyle(.grouped).frame(height: 190)
            HStack {
                Button("Cancel") { showingStorageEditor = false }
                Spacer()
                Button("Save") {
                    showingStorageEditor = false
                    Task {
                        await workspace.saveStorageLimits(
                            maxBytes: Int64(draftGigabytes * 1_000_000_000),
                            retentionDays: draftRetentionDays,
                            cleanupEnabled: draftCleanup)
                    }
                }.buttonStyle(.borderedProminent).disabled(workspace.savingStorage)
            }
        }.padding(24).frame(width: 460)
    }
    /// What's New. Deliberately the same text the release ships as `release-notes.md`, so the
    /// screen cannot describe a build it is not part of.
    private var whatsNew: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("What's New").font(.largeTitle.bold())
                Text(ReleaseNotes.currentVersion)
                    .font(.title3.monospacedDigit())
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .padding([.horizontal, .top], 20)
            .padding(.bottom, 4)
            Divider()
            ReleaseNotesView()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var connections: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                heading("Connections", "Optional API access and external Studio Hub or GatewayKH control.")
                Text("Local creation works independently. Connecting a controller is optional.").font(.headline)
                Divider()
                releaseUpdateSection
                appUpdatesSection
                localAPI
                Divider()
                fleetSection
                Divider()
                controllerSection
            }.padding(28)
        }
        .task { if connected { await workspace.refreshConnection() } }
    }

    /// The controller's app update, next to the connection state it comes from.
    ///
    /// It never installs silently or on a timer: the operator presses Install, and the text says
    /// plainly that the local services restart and running work stops. A release that cannot be
    /// verified against the key already pinned on this Mac is shown without an Install button.
    @ViewBuilder private var releaseUpdateSection: some View {
        if workspace.showingUpdateBanner, let release = workspace.offeredRelease {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "arrow.down.circle.fill").foregroundStyle(.blue)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Update available — \(release.version) (\(release.build))")
                            .font(.callout).fontWeight(.semibold)
                        Text("\(release.sizeDisplay) · signed by controller key \(release.shortSigner)… · from \(release.publisherId)")
                            .font(.callout).foregroundStyle(.secondary)
                        Text("Installing replaces this app and restarts the local services. Any work running right now will stop.")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        if let progress = workspace.releaseProgress {
                            Text(progress).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 8)
                    Button(workspace.installingRelease ? "Installing…" : "Install") {
                        Task { await workspace.installOfferedRelease() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(workspace.installingRelease)
                    Button("Dismiss") { workspace.dismissOfferedRelease() }
                        .disabled(workspace.installingRelease)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.blue.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.blue.opacity(0.40)))
        } else if workspace.offeredReleaseIsOlder, let release = workspace.offeredRelease {
            // A stale controller must not be able to roll this Mac backwards, so this is shown as
            // its own state — not a verification failure — and carries no Install button.
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "arrow.down.circle").foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Older release available — this Mac is on a newer version")
                        .font(.callout).fontWeight(.semibold)
                    Text("The controller holds \(release.version) (\(release.build)), older than the \(workspace.currentAppVersion ?? "newer") version installed here. It is not offered, so a stale controller cannot roll this Mac backwards.")
                        .font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.35)))
        } else if let refusal = workspace.releaseRefusal {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "lock.trianglebadge.exclamationmark.fill").foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Update cannot be verified").font(.callout).fontWeight(.semibold)
                    Text(refusal).font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Check Again") { Task { await workspace.refreshRelease() } }
                        .disabled(workspace.installingRelease)
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.orange.opacity(0.45)))
        }
    }

    /// The owner's front door to the gateway key this Mac trusts for app releases.
    ///
    /// Two fingerprints are shown, and the difference matters: this Mac's own publisher key is what
    /// a joining node pins, while the trusted gateway key is what allows GatewayKH to push a
    /// release here. Only the second is changeable here, and changing it decides which Macs may
    /// update this Mac, so the button confirms first and says what a wrong key costs.
    @ViewBuilder private var appUpdatesSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("App updates")
            publisherIdentityRow
            Divider()
            trustedPublisherRow
            Divider()
            publisherKeyControl
        }
    }

    /// This controller's own release-signing key: the fingerprint a joining node pins.
    @ViewBuilder private var publisherIdentityRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("This Mac's publisher key").font(.callout).fontWeight(.semibold)
            if let identity = workspace.controllerReleaseIdentity {
                LabeledContent("Fingerprint") {
                    Text(identity.fingerprint)
                        .font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled)
                        .multilineTextAlignment(.trailing)
                }
                LabeledContent("Algorithm", value: identity.algorithm)
                Text("A Mac that joins this one pins this fingerprint, so this is who it will accept an app release from.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else {
                Text(connected
                     ? "This Mac's publisher key is unavailable until the services refresh."
                     : "Start the local services to read this Mac's publisher key.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    /// The gateway key the owner has installed, or plainly that none is installed.
    @ViewBuilder private var trustedPublisherRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Trusted gateway key").font(.callout).fontWeight(.semibold)
            if let fingerprint = workspace.trustedPublisher?.installedFingerprint {
                LabeledContent("Fingerprint") {
                    Text(fingerprint)
                        .font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled)
                        .multilineTextAlignment(.trailing)
                }
                if let publisher = workspace.trustedPublisher?.publisherId {
                    LabeledContent("Publisher", value: publisher)
                }
                Text("A release pushed by GatewayKH is accepted only when it is signed by this key.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("No publisher key installed yet — this Mac cannot accept an app release.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Paste a replacement key. The button confirms before anything is sent, and an empty field is
    /// refused with a message rather than a request.
    @ViewBuilder private var publisherKeyControl: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Change publisher key").font(.callout).fontWeight(.semibold)
            HStack {
                TextField("64 hex or base64 Ed25519 public key", text: $publisherKeyDraft)
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 360)
                    .font(.system(.callout, design: .monospaced))
                TextField("Publisher id (optional)", text: $publisherIdDraft)
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 220)
            }
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Button("Change Publisher Key") {
                    // An empty paste has nothing to confirm: refuse it with the same clear message
                    // the store gives, and never send the route.
                    if publisherKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Task { _ = await workspace.changePublisherKey(publicKey: publisherKeyDraft,
                                                                     publisherId: publisherIdDraft) }
                    } else {
                        confirmingPublisherKeyChange = true
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!connected)
                Text("Paste the public key GatewayKH publishes. It must be 64 hex characters (either case) or standard base64.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// ST-10.2 — the private address, the identity behind it, and the credential, with the
    /// secret masked until the operator reveals or copies it.
    private var localAPI: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Local API")
            if let url = studio.readyURL {
                LabeledContent("Address") {
                    Text(url.absoluteString).textSelection(.enabled).multilineTextAlignment(.trailing)
                }
                Text("Reachable only from this Mac on the loopback interface. A location controller reaches this Hub over the network; this build serves the loopback address.")
                    .font(.callout).foregroundStyle(.secondary)
                if let identity = workspace.controllerIdentity {
                    LabeledContent("Role", value: identity.settings.role.capitalized)
                    LabeledContent("Station", value: identity.settings.siteName)
                    LabeledContent("Station ID", value: identity.settings.siteId)
                    LabeledContent("Controller ID", value: identity.settings.controllerId)
                    LabeledContent("This machine", value: identity.settings.machineName)
                    LabeledContent("Accepts customer jobs", value: identity.readiness.ready ? "Yes" : "No")
                    Text("Node-to-node routing is unchanged: GatewayKH sends work to a location controller, and joined nodes keep their existing enrolment. This app does not open a second gateway.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    Text("Reading this Hub's identity…").font(.callout).foregroundStyle(.secondary)
                }
                hubCredential
            } else {
                Text("Start local services to make the API available on this Mac.").foregroundStyle(.secondary)
            }
        }
    }

    private var hubCredential: some View {
        VStack(alignment: .leading, spacing: 8) {
            LabeledContent("Hub token") {
                Text(revealedToken ? (studio.connectionToken ?? "Unavailable")
                                   : (studio.connectionToken.map(NativeWorkspaceStore.maskedToken) ?? "Unavailable"))
                    .textSelection(.enabled).multilineTextAlignment(.trailing)
                    .font(.system(.body, design: .monospaced))
            }
            HStack {
                Button(revealedToken ? "Hide Token" : "Reveal Token") { revealedToken.toggle() }
                    .disabled(studio.connectionToken == nil)
                Button("Copy Hub Token") {
                    if let token = studio.connectionToken {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(token, forType: .string)
                        connectionNote = "Hub token copied"
                    }
                }.disabled(studio.connectionToken == nil)
                if let connectionNote { Text(connectionNote).font(.callout).foregroundStyle(.secondary) }
            }
            Text("This Mac's own key. It is for you, on this Mac, and it never leaves it. Use it to change settings, install or remove models, delete output, and manage shared voices.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("Do not give it out. Anyone who holds it can do everything on this Mac. GatewayKH needs it only if it will manage shared voices here — to send work, give out the Fleet token below instead.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            // Two values, and the difference is the safety property: this one is what a joining Mac
            // adopts and what this Mac then presents back to it. Handing a node the Hub token
            // instead would give every node's owner full authority over this Mac.
            Divider()
            LabeledContent("Fleet token") {
                Text(revealedToken ? (studio.fleetToken ?? "Unavailable")
                                   : (studio.fleetToken.map(NativeWorkspaceStore.maskedToken) ?? "Unavailable"))
                    .textSelection(.enabled).multilineTextAlignment(.trailing)
                    .font(.system(.body, design: .monospaced))
            }
            HStack {
                Button("Copy Fleet Token") {
                    if let token = studio.fleetToken {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(token, forType: .string)
                        connectionNote = "Fleet token copied"
                    }
                }.disabled(studio.fleetToken == nil)
                Text("Give this to GatewayKH so it can send work here and collect the results, and to every Mac that joins this controller.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Text("What it lets the holder do: send work to this Mac, follow that job, download the result, acknowledge it, cancel it or discard an uncertain one, and read the model and fleet lists.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("What it cannot do: change this Mac's settings, install or remove its models, delete its output, manage its shared voices, or make a Mac join or leave. A Mac that holds this token still cannot administer this Mac.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("In short: the Hub token is this Mac's key, the Fleet token is the work badge for the group. Send work with the Fleet token; keep the Hub token to yourself.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    /// "This needs a restart", and the button that restarts.
    ///
    /// The network addresses and the port are read when the Hub starts, so their controls are
    /// disabled while it runs. That left a new operator to work out on their own that they had to
    /// stop the services, change the setting and start them again - and the owner watched exactly
    /// that happen. Anything that waits for a restart now says so here, next to the control, with
    /// the one press that applies it.
    @ViewBuilder private var restartNotice: some View {
        if studio.restartPending || (!studio.canConfigure && studio.hasPendingRestartSetting) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "arrow.clockwise.circle").foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 6) {
                    Text("This change takes effect when the local services restart.")
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                    Button("Restart Services") { studio.restartServices() }
                        .disabled(studio.stopping || studio.starting)
                    Text("They stop and start again on their own; nothing else on this Mac is affected.")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    /// The answer to the last join, leave or role change.
    ///
    /// A banner rather than a line of small print: it says whether the action worked, quotes the
    /// controller's own reason when it refused, can be selected to copy into a report, and stays
    /// until dismissed so a failure cannot scroll away unread.
    @ViewBuilder private func connectionNoticeBanner(_ notice: ConnectionNotice) -> some View {
        let tint: Color = notice.kind == .failure ? .orange : .green
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: notice.kind == .failure
                  ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                .foregroundStyle(tint)
            VStack(alignment: .leading, spacing: 6) {
                Text(notice.kind == .failure ? "That did not work" : "Done")
                    .font(.callout).fontWeight(.semibold)
                Text(notice.text)
                    .font(.callout)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button { workspace.dismissConnectionNotice() } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .help("Dismiss")
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(tint.opacity(0.45)))
    }

    private var controllerSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Studio Hub & GatewayKH")
            if let identity = workspace.controllerIdentity, let version = workspace.hubVersion {
                LabeledContent("Hub version", value: version.display)
                LabeledContent("Data store", value: identity.readiness.sqliteAuthoritative == true ? "Local, authoritative" : identity.settings.databaseMode)
                LabeledContent("Reported protocol authority", value: identity.settings.globalAuthority ?? "Not reported")
                LabeledContent("Migration stage", value: identity.settings.migrationStage ?? identity.readiness.migrationStage ?? "local")
                if let warning = identity.readiness.telemetryWarning {
                    Text(warning).font(.callout).foregroundStyle(.orange)
                }
                Text("Jobs are recorded and claimed on this Mac. GatewayKH sends work to location controllers; Studio Hub manages local execution and reports the capabilities it actually has. This app offers no update channel it cannot honour.")
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                Text("Remote control is optional and not required to create assets locally.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// Role, reachability and the address other Macs use — the whole fleet picture in one place.
    private var fleetSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            sectionTitle("Fleet")
            // The answer to the last join, leave or role change, at the top where it cannot be
            // missed, and kept until dismissed. It used to be a line of small grey print further
            // down, which the owner did not see until the third attempt.
            if let notice = workspace.connectionNotice {
                connectionNoticeBanner(notice)
            }
            // "This Mac is a Not reporting" was what the operator read when the Hub had not
            // answered yet: the article was hard-coded beside a label that is sometimes a phrase.
            // Naming this Mac. The Hub has kept a label for it all along - `machine_name` is what
            // every other Mac and the gateway show - but no control ever offered it, so the only
            // name an operator could have was their hostname.
            VStack(alignment: .leading, spacing: 6) {
                Text("Name").font(.callout)
                HStack {
                    TextField("This Mac's name, e.g. Studio Mini 01", text: $machineNameDraft)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                    Button("Rename") {
                        Task { await workspace.renameThisMachine(machineNameDraft) }
                    }
                    .disabled(!connected || machineNameDraft.trimmingCharacters(in: .whitespacesAndNewlines)
                              == (workspace.controllerIdentity?.settings.machineName ?? ""))
                }
                Text("What this Mac is called in the fleet, in Jobs and to the gateway. It is a label, not an identity: renaming it never changes routing or a node's registration.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .onAppear { machineNameDraft = workspace.controllerIdentity?.settings.machineName ?? "" }
            .onChange(of: workspace.controllerIdentity?.settings.machineName) { _, value in
                machineNameDraft = value ?? ""
            }

            LabeledContent("This Mac is", value: roleTitle(workspace.controllerIdentity?.settings.role))
            LabeledContent("Group", value: groupStatus)
            Text(roleExplanation(workspace.controllerIdentity?.settings.role))
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker("Role", selection: Binding(
                get: { workspace.controllerIdentity?.settings.role ?? "standalone" },
                set: { value in Task { await workspace.setRole(value) } }
            )) {
                Text("Standalone").tag("standalone")
                Text("Controller").tag("controller")
                Text("Node").tag("agent")
            }
            .pickerStyle(.segmented).labelsHidden()
            .disabled(!connected || workspace.controllerIdentity == nil)
            if let note = workspace.roleNote {
                Text(note).font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("A **Controller** runs its own group and sends work to nodes; a **Node** is sent work by a controller; **Standalone** is a Mac on its own. The role applies immediately.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Divider()

            restartNotice

            LabeledContent("Listening on", value: listenTitle(studio.configuration.listenHost))
            Text("Pick every network this Mac should be reachable on. It always answers this Mac itself, so choosing nothing here keeps it private. A new install starts reachable on its private and Tailscale addresses.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            // Several addresses at once, because a Mac serving its office LAN and its tailnet is
            // the normal case; loopback is always included by the Hub, so it is never a choice.
            ForEach(LocalAddresses.current()) { entry in
                Toggle("Reachable at \(entry.title)", isOn: Binding(
                    get: { studio.configuration.listenHosts.contains(entry.address) },
                    set: { included in
                        var hosts = studio.configuration.listenHosts.filter { $0 != "0.0.0.0" }
                        if included { hosts.append(entry.address) }
                        else { hosts.removeAll { $0 == entry.address } }
                        studio.setListenHosts(hosts)
                    }))
                // These are editable while the Hub runs: the change is saved and the notice above
                // offers the restart that applies it, instead of the control refusing to move.
            }
            Toggle("Any address on any network", isOn: Binding(
                get: { studio.configuration.listenHosts.contains("0.0.0.0") },
                set: { studio.setListenHosts($0 ? ["0.0.0.0"] : []) }))
            // The port is fixed rather than picked at each start, so an address written down on
            // one Mac still names this one tomorrow. Shown with the address a node types, because
            // that string is the whole handshake.
            HStack {
                Text("Port").frame(width: 96, alignment: .leading)
                TextField("47873", value: Binding(
                    get: { studio.configuration.port },
                    set: { studio.setPort($0) }), format: .number)
                    .textFieldStyle(.roundedBorder).frame(width: 90)
                    .disabled(!studio.canConfigure)
                Text(studio.nodeFacingAddress.map { "A node joins with the address \($0) and this controller's Fleet token below — not the Hub token." }
                     ?? "Until one of these is on, only this Mac can reach the Hub — a node needs an address like the ones above.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            listenWarning

            if let note = studio.fleetNote {
                Text(note).font(.callout).foregroundStyle(.secondary)
            }
            // Only a Mac that joined somebody belongs to a group. This is the action that takes
            // the group's credential off it, which is what a Mac being retired or sold needs.
            if workspace.controllerIdentity?.settings.fleetCredentialOrigin == "adopted" {
                HStack(alignment: .top) {
                    Button("Leave this group") { Task { await workspace.leaveGroup() } }
                        .disabled(!connected)
                    Text("Stops this Mac using the group's token and returns it to running on its own. Do this before retiring or selling it.")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            // The banner at the top of this page already carries the last actionable result, so
            // the plain note only appears when there is no banner (a quiet startup re-join).
            if workspace.connectionNotice == nil, let note = workspace.fleetNote {
                Text(note).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }

            Divider()
            fleetOverview
            Divider()
            joinedNodes
            Divider()
            joinController
        }
        .task(id: connected) {
            if connected {
                await workspace.refreshFleetNodes()
                await workspace.refreshControllerHealth()
            }
        }
    }

    /// Every Mac in the fleet, one row each: is it there, what can it run, how fast, how full.
    ///
    /// This is the picture the owner asked for in one place, and it is the same row for this Mac
    /// as for a joined one - a fleet view that left out the machine you are sitting at would
    /// answer half the question.
    @ViewBuilder private var fleetOverview: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Fleet overview").font(.headline)
                Spacer()
                if let overview = workspace.fleetOverview {
                    Text("\(overview.summary.reachable) of \(overview.summary.total) reachable")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            if let overview = workspace.fleetOverview {
                Text(poolSummary(overview))
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text(pinSummary)
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                ForEach(overview.machines) { machine in
                    fleetMachineRow(machine)
                }
            } else {
                Text(connected ? "Fleet details are unavailable in this build." : "Start local services to see the fleet.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    /// Whether this Mac chooses where work runs. A controller and a Mac on its own do; a Node does
    /// not - its controller decides, and offering "Send here" on every row of a node's pool would
    /// be offering a choice that is not this Mac's to make.
    private var canPinWork: Bool {
        guard let role = workspace.fleetOverview?.role else { return false }
        return role != "agent"
    }

    /// Which pool this list is. The rows answer "how is each Mac doing", which is only half the
    /// question on a Mac that joined somebody: it also has to know whose group it is looking at,
    /// and be told plainly when the group is bigger than what it could read.
    private func poolSummary(_ overview: NativeFleetOverview) -> String {
        guard overview.belongsToGroup else {
            return "This Mac is on its own. Nodes that join it appear here."
        }
        let head = overview.controller.map { "the group run by \($0.label)" } ?? "its group"
        guard overview.rosterReadable != false else {
            return "This Mac is part of \(head), but the controller is not answering, so the Macs below are only the ones this Mac knows about."
        }
        return "This Mac is part of \(head). Every Mac in the group is listed below."
    }

    @ViewBuilder private func fleetMachineRow(_ machine: NativeFleetMachine) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                Image(systemName: machine.reachable
                      ? (machine.health == "up" ? "checkmark.circle.fill" : "clock")
                      : "exclamationmark.triangle")
                    .foregroundStyle(machine.reachable
                                     ? (machine.health == "up" ? Color.green : Color.secondary)
                                     : Color.orange)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(machine.isLocal ? "This Mac" : machine.label).fontWeight(.medium)
                        if let role = fleetRoleTitle(machine) {
                            Text(role).font(.caption)
                                .padding(.horizontal, 6).padding(.vertical, 1)
                                .background(Color.secondary.opacity(0.15), in: Capsule())
                                .foregroundStyle(.secondary)
                        }
                        if let version = machine.version, !version.isEmpty {
                            Text(version).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text(machineFacts(machine)).font(.caption).foregroundStyle(.secondary)
                    // Where the Mac answers, so a row can be matched to the address the operator
                    // wrote down. Absent for this Mac, whose addresses are listed above.
                    if let address = machine.address, !address.isEmpty, !machine.isLocal {
                        Text(address).font(.caption).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
                Spacer()
                if !machine.modalities.isEmpty {
                    Text(machine.modalities.keys.sorted().map(modalityTitle).joined(separator: ", "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(seconds(machine.averageSeconds)).font(.system(.caption, design: .monospaced))
                    .frame(minWidth: 78, alignment: .trailing)
                // Pinning is a standing choice for every new job; pressing again clears it, which
                // keeps the way back to automatic on the same control that left it. A Mac that is
                // off keeps the control but cannot be pinned by accident - the label says so.
                if !machine.reachable {
                    Text("Offline").font(.caption).foregroundStyle(.orange)
                }
                if canPinWork {
                    Button(workspace.routingPin == "machine:\(machine.machine)" ? "Automatic" : "Send here") {
                        workspace.routingPin = workspace.routingPin == "machine:\(machine.machine)"
                            ? nil : "machine:\(machine.machine)"
                    }
                    .disabled(!connected)
                    .help("Send new Image, Voice, Subtitle and Music work to this Mac until you clear the pin.")
                }
            }
            if let detail = machine.detail {
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 3)
    }

    /// What this Mac's part in the group is, when the Hub says. Absent means an older Hub that
    /// does not report a role, and an empty badge is better than a guessed one.
    private func fleetRoleTitle(_ machine: NativeFleetMachine) -> String? {
        switch machine.role {
        case "controller": return "Controller"
        case "agent": return "Node"
        case "standalone": return machine.isLocal ? nil : "Standalone"
        default: return nil
        }
    }

    private var pinSummary: String {
        guard let pin = workspace.routingPin, pin.hasPrefix("machine:") else {
            return "New work runs on whichever Mac can take it. Use Send here to pin one."
        }
        return "New work is pinned to \(pin.replacingOccurrences(of: "machine:", with: "")). Press Automatic to let the Hub choose again."
    }

    private func machineFacts(_ machine: NativeFleetMachine) -> String {
        var parts: [String] = []
        if let chip = machine.chip, !chip.isEmpty { parts.append(chip) }
        if let memory = machine.memoryGb { parts.append(String(format: "%.0f GB memory", memory)) }
        if let free = machine.diskFreeGb, let total = machine.diskTotalGb {
            parts.append(String(format: "%.0f GB free of %.0f GB", free, total))
        }
        parts.append(machine.jobs == 0 ? "no completed work"
                                       : "\(machine.jobs) completed job\(machine.jobs == 1 ? "" : "s")")
        return parts.joined(separator: " · ")
    }

    private func modalityTitle(_ modality: String) -> String {
        switch modality {
        case "image": return "Image"
        case "transcription": return "Subtitle"
        case "voice", "speech": return "Voice"
        default: return modality.capitalized
        }
    }

    /// What a controller has been joined by, with the state each node is actually in.
    @ViewBuilder private var joinedNodes: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Joined nodes").font(.headline)
                Spacer()
                Button("Refresh") { Task { await workspace.refreshFleetNodes() } }.disabled(!connected)
            }
            if let fleet = workspace.fleetNodes {
                if fleet.nodes.isEmpty {
                    // "No Mac has joined this one yet" is true on a node and reads as "you are on
                    // your own", which is the opposite of what a Mac that has joined a group is.
                    // A node's answer to this section is the group it belongs to, from the top.
                    if let overview = workspace.fleetOverview, overview.belongsToGroup {
                        Text(nodeMembershipSummary(overview))
                            .font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text("No Mac has joined this one yet. Macs that join this one appear here, with Remove to drop them.")
                            .font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    Text("\(fleet.summary.reachable) of \(fleet.summary.total) reachable now.")
                        .font(.callout).foregroundStyle(.secondary)
                    ForEach(fleet.nodes) { node in
                        HStack(spacing: 10) {
                            Image(systemName: node.isReachable ? "checkmark.circle.fill" : "exclamationmark.triangle")
                                .foregroundStyle(node.isReachable ? Color.green : Color.orange)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(node.name).fontWeight(.medium)
                                Text(node.address).font(.caption).foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                                if let detail = node.detail, !node.isReachable {
                                    Text(detail).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Button("Remove") { Task { await workspace.leaveNode(node) } }
                                .disabled(!connected)
                        }.padding(.vertical, 4)
                    }
                }
            } else {
                Text(connected ? "Joined nodes are unavailable in this build." : "Start local services to see joined nodes.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    /// What a node should read where a controller reads its roster: whose group it is in, and how
    /// to leave. A node has no nodes of its own, so the honest answer is the group above it.
    private func nodeMembershipSummary(_ overview: NativeFleetOverview) -> String {
        guard let controller = overview.controller else {
            return "This Mac is set as a Node, but it has not been joined to a controller yet. Use Join a controller below."
        }
        let peers = overview.machines.filter { !$0.isLocal && $0.machine != controller.machine }.count
        let reach = controller.isReachable ? "answering" : "not answering right now"
        let beside = peers == 0 ? "" : " \(peers) other Mac\(peers == 1 ? "" : "s") share the group."
        return "No Mac joins this one: this Mac is a Node in the group run by \(controller.label) at "
            + "\(controller.address ?? "an address this Mac did not record"), which is \(reach)."
            + "\(beside) The whole group is listed under Fleet overview above."
    }

    /// Join another Mac that is running as a controller.
    @ViewBuilder private var joinController: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Join a controller").font(.headline)
            // Where this Mac stands, right here rather than only in a banner at the top of the
            // page. The owner joined successfully and could not tell: the result was a line of
            // grey text that looked the same whether it had worked or not.
            HStack(alignment: .top, spacing: 8) {
                Circle()
                    .fill(connectionStateTint)
                    .frame(width: 10, height: 10)
                    .padding(.top, 5)
                VStack(alignment: .leading, spacing: 2) {
                    Text(connectionStateTitle).font(.callout).fontWeight(.semibold)
                    Text(workspace.controllerHealthNote ?? "Checking the controller…")
                        .font(.callout).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(connectionStateTint.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
            Text("Enter the controller's address and its Fleet token — not its Hub token. On the controller Mac, open Connections and copy the Fleet token from there.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("The address is the controller's Tailscale or LAN address; the port is optional and assumed to be 47873. `http://` is optional too.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("This Mac registers with the controller and adopts that token, so the controller can send it work. The controller then reports whether this Mac is reachable.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                TextField("100.x.y.z  ·  or a name like mac.tailnet.ts.net", text: $controllerDraft)
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                // Visible, not a SecureField: the owner asked to see the token they pasted so a
                // wrong copy is obvious, and it is a LAN credential they already hold in a file.
                TextField("Controller's Fleet token", text: $controllerToken)
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 280)
                Button("Test") {
                    Task { await workspace.testControllerAddress(controllerDraft) }
                }
                .disabled(!connected || controllerDraft.isEmpty)
                .help("Ask the controller's Hub whether it is there. This sends no token.")
                Button(workspace.joiningFleet ? "Joining…" : "Join") {
                    Task {
                        await workspace.joinController(address: controllerDraft, token: controllerToken,
                                                       boundHosts: studio.configuration.listenHosts)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!connected || workspace.joiningFleet
                          || controllerDraft.isEmpty || controllerToken.isEmpty)
                if workspace.joiningFleet { ProgressView().controlSize(.small) }
            }
            // Pasting from a document brings a trailing newline with it, which used to land in
            // these fields and show as the text dropping to a second line - and made the address
            // or the token wrong without looking wrong. Neither value can contain whitespace, so
            // it is removed as it arrives rather than refused later.
            .onChange(of: controllerDraft) { _, value in
                let cleaned = value.filter { !$0.isWhitespace }
                if cleaned != value { controllerDraft = cleaned }
            }
            .onChange(of: controllerToken) { _, value in
                let cleaned = value.filter { !$0.isWhitespace }
                if cleaned != value { controllerToken = cleaned }
            }
            Text("Neither the address nor the token can contain a space, so pasted spaces and newlines are removed for you. The token is shown so you can check what you pasted.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            // Kept inline as well as in the banner at the top: this is the spot the operator is
            // looking at the moment they press Join, so the answer belongs here too - and it is
            // coloured, because a success and a failure used to read identically in grey.
            if let note = workspace.fleetNote {
                Text(note)
                    .font(.callout)
                    .foregroundStyle(workspace.connectionNotice?.kind == .failure ? Color.orange : Color.green)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func roleTitle(_ role: String?) -> String {
        switch role {
        case "controller": return "Controller"
        case "agent": return "Node"
        case "standalone": return "Standalone"
        default: return "Not reporting"
        }
    }

    /// One line that answers "is this Mac in a group, and whose?" - the question the role picker
    /// alone did not answer. `fleet_credential_origin` is the Hub's own record of whether this
    /// Mac is running on its own credential (minted) or somebody else's (adopted).
    private var groupStatus: String {
        guard let settings = workspace.controllerIdentity?.settings else { return "Unknown" }
        guard settings.fleetCredentialOrigin == "adopted" else {
            return settings.role == "controller"
                ? "This Mac is a controller — nodes join it from their own app"
                : "On its own, not joined to a controller"
        }
        let address = workspace.controllerAddress.trimmingCharacters(in: .whitespaces)
        return address.isEmpty ? "Joined to a controller" : "Joined to \(address)"
    }

    /// Green when this Mac is joined and the controller answers, amber when it is joined but not
    /// answering, grey when it is simply on its own. One dot, so the state is readable at a glance
    /// without hunting for a sentence.
    private var connectionStateTint: Color {
        guard let reachable = workspace.controllerReachable else { return .secondary }
        return reachable ? .green : .orange
    }

    private var connectionStateTitle: String {
        guard let reachable = workspace.controllerReachable else { return "Not joined to a controller" }
        return reachable ? "Connected" : "Joined, but not answering"
    }

    /// The colour of the mode pill: a controller is blue, a node takes the connection's colour, and
    /// a Mac on its own stays quiet.
    private var modeTint: Color {
        switch workspace.controllerIdentity?.settings.role {
        case "controller": return .blue
        case "agent": return workspace.controllerReachable == true ? .green : .orange
        default: return .secondary
        }
    }

    /// "Image 1 · Audio 8 · Subtitle 3 · Music 2" - how many models this Mac can run for each
    /// studio, counted with the same filter the model pickers use, so the sidebar and the picker
    /// always agree. It counts models, not workers: every Mac runs one worker per studio, so a
    /// worker count said "1" beside a picker offering eight. The Studios come from
    /// `StudioFunction.allCases`, so a new one is counted here without a second edit.
    private var generatorSummary: String {
        let parts = StudioFunction.allCases.filter { $0.hasEngine }.compactMap { function -> String? in
            let count = workspace.supportedModels(for: function).count
            guard count > 0 else { return nil }
            let label = StudioPage.page(for: function)?.title ?? function.rawValue
            return "\(label) \(count)"
        }
        return parts.joined(separator: " · ")
    }

    private func roleExplanation(_ role: String?) -> String {
        switch role {
        case "controller":
            return "Other Macs on your network can send work here, and this Mac reports what the whole group is doing. GatewayKH sends work to this location controller."
        case "agent":
            return "This Mac creates locally and does not accept customer jobs directly; a controller sends it work. This Mac does not contact GatewayKH directly."
        default:
            return "This Mac creates on its own. Nothing else can send it work and it contacts nothing else."
        }
    }

    private func listenTitle(_ host: String) -> String {
        let hosts = host.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
        let reachable = hosts.filter { $0 != "127.0.0.1" }
        if reachable.isEmpty { return "This Mac only" }
        if reachable.contains("0.0.0.0") { return "Every network this Mac is on" }
        return reachable.joined(separator: ", ")
    }

    /// One plain sentence about who can reach this Mac, because the difference between the
    /// three choices is the whole security question.
    @ViewBuilder private var listenWarning: some View {
        switch listenWarningKey {
        case "127.0.0.1":
            Text("Only this Mac can reach the Hub. Nothing on your network can see it. This is the safe default.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        case "0.0.0.0":
            Text("Any device on any network this Mac joins — including café or hotel Wi-Fi — can reach the Hub and try to send it work. They still need the token, but the token travels in the clear unless you are on Tailscale. Prefer a named address.")
                .font(.callout).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
        default:
            Text("Only devices that can reach this one address can use the Hub. A Tailscale address is the intended choice: it is encrypted and only your own machines can reach it.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        if !studio.canConfigure {
            Text("Stop the services to change the addresses, then start them again. Changing them restarts the Hub.")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    /// Which warning to show: loopback, the wildcard, or a named list.
    private var listenWarningKey: String {
        let hosts = studio.configuration.listenHosts.filter { $0 != "127.0.0.1" }
        if hosts.isEmpty { return "127.0.0.1" }
        return hosts.contains("0.0.0.0") ? "0.0.0.0" : hosts.joined(separator: ",")
    }
    private var unavailableWorkspace: some View {
        // A page whose Studio *does* have an engine but has no workspace wired here is a wiring
        // hole, and it says so by name with the file to update. Saying "engine not installed" for
        // it would be a different, false statement.
        let unwired = studio.selection.function?.hasEngine == true
        return VStack(alignment: .leading, spacing: 24) {
            heading(studio.selection.rawValue, workspaceDescription(studio.selection))
            Spacer()
            if unwired {
                empty("No workspace is wired for this Studio",
                      "\(studio.selection.title) has an engine in this build, but no workspace is wired for it. Add it to the `switch studio.selection` in native/StudioViews.swift.",
                      symbol: studio.selection.symbol)
            } else {
                empty("Engine not installed in this build", "This workspace is part of the standalone app. Its local engine and generation workflow still need to be integrated.", symbol: studio.selection.symbol)
            }
            HStack { Spacer(); Button("View Available Workspaces") { studio.select(.overview) }; Spacer() }
            Spacer()
        }.padding(28)
    }

    /// Idle-release controls for every Studio on this machine, read from the Hub.
    ///
    /// The mode names and thresholds come from the Studios themselves, so this screen never
    /// invents what "Balanced" means, and the resolved default is per machine: a Mac with
    /// under 16 GB defaults to releasing immediately.
    @ViewBuilder private var memoryControls: some View {
        if let inventory = workspace.memoryInventory {
            ForEach(inventory.studios) { row in
                memoryRow(row, options: row.policy?.resolvedOptions ?? inventory.options)
            }
        } else {
            Text(connected
                 ? "Model memory settings are unavailable in this build."
                 : "Start local services to read and change model memory settings.")
                .font(.callout).foregroundStyle(.secondary)
        }
        if let note = workspace.memoryNote {
            Text(note).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        HStack {
            Button(workspace.updatingMemory ? "Working…" : "Release Model Memory") {
                Task { await workspace.releaseMemory() }
            }.disabled(!connected || workspace.updatingMemory)
            Button("Refresh") { Task { await workspace.refreshMemoryPolicy() } }
                .disabled(!connected || workspace.updatingMemory)
            if workspace.updatingMemory { ProgressView().controlSize(.small) }
        }
    }

    @ViewBuilder private func memoryRow(_ row: NativeMemoryStudio, options: [NativeMemoryMode]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(row.title).font(.headline)
                if !row.isReady {
                    Text(memoryStateLabel(row.state)).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Text(memoryResidency(row)).font(.caption).foregroundStyle(.secondary)
            }
            if row.isReady, let policy = row.policy {
                if policy.isFixed {
                    Text(policy.releasePolicy ?? "The model is released when each job finishes.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    Picker("Release policy", selection: Binding(
                        get: { policy.mode },
                        set: { value in Task { await workspace.setMemoryMode(value, studioIDs: [row.id]) } }
                    )) {
                        ForEach(options) { option in
                            Text(option.mode == policy.defaultMode ? "\(option.label) (default)" : option.label)
                                .tag(option.mode)
                        }
                    }
                    .pickerStyle(.segmented).labelsHidden()
                    .disabled(!connected || workspace.updatingMemory)
                    Text(options.first { $0.mode == policy.mode }?.summary ?? "")
                        .font(.callout).foregroundStyle(.secondary)
                }
            } else if let detail = row.detail, !detail.isEmpty {
                Text(detail).font(.callout).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 2)
    }

    private func memoryStateLabel(_ state: String) -> String {
        switch state {
        case "offline": return "Not running"
        case "update_required": return "Needs an update"
        case "error": return "Unavailable"
        default: return state.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func memoryResidency(_ row: NativeMemoryStudio) -> String {
        guard let policy = row.policy else { return "" }
        if let busy = policy.busy, busy { return "Working" }
        let held = policy.resident
        guard !held.isEmpty else { return "No model loaded" }
        return "Holding " + held.map { modelName($0) }.joined(separator: ", ")
    }

    /// Per-device storage limits, with the bytes that cleanup may not touch shown beside them.
    @ViewBuilder private var storageControls: some View {
        if let settings = workspace.deviceSettings {
            LabeledContent("Size limit", value: NativeWorkspaceStore.gigabytes(settings.effective.maxBytes))
            LabeledContent("Keep finished work for", value: "\(settings.effective.retentionDays) days")
            LabeledContent("Automatic cleanup", value: settings.effective.cleanupEnabled ? "On" : "Off")
            ForEach(workspace.storageUsage) { usage in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(usage.machine == "local" ? "This Mac" : usage.machine).font(.headline)
                        Spacer()
                        Text(NativeWorkspaceStore.gigabytes(usage.usedBytes)).font(.system(.body, design: .monospaced))
                    }
                    Text("Generated output: \(NativeWorkspaceStore.gigabytes(usage.usedBytes)), of which \(NativeWorkspaceStore.gigabytes(usage.protectedBytes)) is protected and never deleted"
                         + (usage.freeBytes.map { " · \(NativeWorkspaceStore.gigabytes($0)) free on disk" } ?? ""))
                        .font(.caption).foregroundStyle(.secondary)
                    if usage.overLimit {
                        Text("Over the size limit. Cleaning up is possible, but only work that is finished and acknowledged.")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }.padding(.vertical, 2)
            }
            HStack {
                Button("Change Limits…") {
                    draftGigabytes = Double(settings.effective.maxBytes) / 1_000_000_000
                    draftRetentionDays = settings.effective.retentionDays
                    draftCleanup = settings.effective.cleanupEnabled
                    showingStorageEditor = true
                }
                .disabled(!connected || workspace.savingStorage)
                Button("Restore Defaults") { Task { await workspace.restoreStorageDefaults() } }
                    .disabled(!connected || workspace.savingStorage)
                if workspace.savingStorage { ProgressView().controlSize(.small) }
            }
            if let note = workspace.storageNote {
                Text(note).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        } else {
            Text(connected ? "Storage settings are unavailable in this build." : "Start local services to read and change storage limits.")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    private func chooseTranscriptionFile() {
        let panel = NSOpenPanel(); panel.title = "Choose a recording"; panel.allowedContentTypes = [.audio, .movie]; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK { workspace.transcriptionFile = panel.url }
    }
    /// The folder for a model that was copied onto this Mac, rather than downloaded.
    private func chooseModelFolder(for model: NativeModel) {
        let panel = NSOpenPanel()
        panel.title = "Choose the folder for \(model.label)"
        panel.message = "Select the folder you copied for this model. The Hub checks its manifest and every file before accepting it."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            Task { await workspace.importModel(model, from: url) }
        }
    }
    private func saveImage(_ job: NativeJob) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "image-\(job.id).png"; panel.allowedContentTypes = [.png]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { do { try await workspace.saveImage(job, to: url) } catch { fileActionError = error.localizedDescription } }
    }
    private func saveAudio(_ job: NativeJob) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "audio-\(job.id).wav"; panel.allowedContentTypes = [.wav]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        // The bytes are verified against the Hub's receipt before the write, then the write is
        // re-read, and only then is delivery acknowledged - the same order the Image and
        // Subtitle saves use. Acknowledgement is what releases the protected copy.
        Task {
            do { try await workspace.saveAudio(job, to: url) }
            catch { fileActionError = error.localizedDescription }
        }
    }
    private func saveMusic(_ job: NativeJob) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "music-\(job.id).wav"; panel.allowedContentTypes = [.wav]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        // Same order as every other workspace: fetch, verify the bytes against the Hub's receipt,
        // write, re-read, and only then acknowledge delivery.
        Task {
            do { try await workspace.saveMusic(job, to: url) }
            catch { fileActionError = error.localizedDescription }
        }
    }
    private func exportTranscript() {
        guard workspace.transcriptionResult != nil else { return }
        let ext = transcriptFormat == "Text" ? "txt" : transcriptFormat.lowercased()
        let panel = NSSavePanel(); panel.nameFieldStringValue = "transcript.\(ext)"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let format: NativeTranscriptFormat = transcriptFormat == "SRT" ? .srt
            : transcriptFormat == "VTT" ? .vtt : .text
        // A transcript is only saved once the Hub's own artifact has been verified against
        // what the screen shows, and delivery is acknowledged only after the file is written.
        Task {
            do { try await workspace.saveTranscript(format: format, to: url) }
            catch { fileActionError = error.localizedDescription }
        }
    }
    private func confirmRemove(_ model: NativeModel) {
        let alert = NSAlert(); alert.messageText = "Remove downloaded model?"; alert.informativeText = "Remove \(model.label) from this library. Existing generated files are kept. Models in use or required by another installed model cannot be removed."
        alert.addButton(withTitle: "Keep Model"); alert.addButton(withTitle: "Remove Download")
        if alert.runModal() == .alertSecondButtonReturn { Task { await workspace.removeModel(model) } }
    }
}
