import AppKit
import SwiftUI
import AVFoundation
import AVKit
import UniformTypeIdentifiers

struct StudioWindow: View {
    @ObservedObject var studio: StudioController
    var body: some View {
        // The one place the window's width is read, so a view that has to choose between two layouts
        // (the Connections node table) is told it rather than measuring itself.
        GeometryReader { proxy in
            StudioContent(studio: studio, workspace: studio.workspace)
                .environment(\.hubWindowWidth, proxy.size.width)
                .environment(\.hubWindowHeight, proxy.size.height)
        }
        .frame(minWidth: 720, minHeight: 520)
    }
}

/// Tabs for the unified history. Owner requirement (2026-09-15): each generation page keeps its
/// own history, and the shared history is presented with tabs rather than one flat scroll.
///
/// Membership is derived from `StudioFunction.allCases` — the same list the sidebars, the pages
/// and the model pickers derive from — so a Studio added to the enum cannot arrive without its
/// history tab. This was a hand-written list of four names, and a Studio it did not name simply
/// had no tab, with nothing saying a tab was missing.
///
/// `downloads` is the model downloads on this Mac, whoever started them (owner, 2026-10-08: its own
/// tab, not a section under every Studio's history).
enum HistoryTab: Hashable, Identifiable {
    case all
    case studio(StudioFunction)
    case downloads

    static let allCases: [HistoryTab] = [.all] + StudioFunction.allCases
        .filter { $0.hasHistoryTab }
        .map(HistoryTab.studio) + [.downloads]

    var studio: StudioFunction? {
        if case .studio(let function) = self { return function }
        return nil
    }
    /// The page's owner-facing name, so the tab reads "Subtitle" and not "Transcription".
    var title: String {
        if self == .downloads { return "Downloads" }
        guard let studio else { return "All" }
        return StudioPage.page(for: studio)?.title ?? studio.rawValue
    }
    var id: String { title }
}

/// Which Macs the Jobs page lists.
///
/// A node runs work its controller owns, so the two histories meet on the controller and only
/// there: a job's own `machine` (already on every row) says which Mac made it. That is the one
/// fact the operator needs to sort finished assets before previewing them.
enum JobMachineFilter: String, CaseIterable, Identifiable {
    case all = "All"
    case thisMac = "This Mac"
    case nodes = "Nodes"

    var id: String { rawValue }
    /// The picker's word for the filter: "Other Macs" reads as people, "Nodes" as the roster.
    var label: String { self == .nodes ? "Other Macs" : rawValue }

    func includes(_ job: NativeJob) -> Bool {
        switch self {
        case .all: return true
        // Only the Hub's own sentinel is this Mac's. A row with no recorded Mac has not been
        // placed, or its Mac was never written, so neither Mac filter claims it: unknown is not
        // a guess for "This Mac".
        case .thisMac: return job.machine == "local"
        case .nodes: return job.machine != nil && job.machine != "local"
        }
    }
}

/// The small facts every job row carries and the order the Jobs page groups them in, as pure
/// functions so the native view checks pin the words rather than a screenshot.
enum JobFacts {
    enum Stage { case running, waiting, finished }

    /// Running first, then what waits: the owner read the Jobs page as having no running work at
    /// all because it opened on a Studio's finished rows.
    static func stage(_ state: String) -> Stage {
        if ["running", "cancel_requested", "cancelling"].contains(state) { return .running }
        if ["queued", "paused", "recovery_pending"].contains(state) { return .waiting }
        return .finished
    }

    /// Which Mac a row runs on. A live row whose Hub reported no Mac says exactly that - it may
    /// still be waiting to be placed, or running somewhere the Hub did not name, and neither is
    /// "this Mac". A finished row with no recorded Mac (filed before this build) says nothing.
    static func whereTag(machine: String?, isActive: Bool, name: (String) -> String) -> String? {
        if let machine, !machine.isEmpty { return machine == "local" ? "This Mac" : name(machine) }
        return isActive ? "Mac not reported" : nil
    }

    /// Who asked. Nil when the Hub did not record it: an older batch makes no claim.
    static func askedTag(origin: String?) -> String? {
        switch origin {
        case "this-mac": return "asked here"
        case "gateway": return "sent by the Gateway"
        default: return nil
        }
    }
}

/// A slowly turning rainbow ring, drawn only while `active`.
///
/// The owner's feedback was that a long generation "shows almost nothing", so a running job is now
/// unmistakable: the ring travels around the Generate button and the preview while work is in
/// flight. Because it is a `TimelineView`, it costs nothing when idle - the view is not in the
/// hierarchy at all once the last job finishes.
///
/// `inset` keeps a margin between the ring and what it surrounds (a list or a preview), so rows
/// never run into the colour. It applies whether or not the ring shows, so nothing moves when work
/// starts or stops.
private struct ActiveAura<Content: View>: View {
    let active: Bool
    var inset: CGFloat = 0
    @ViewBuilder var content: () -> Content

    var body: some View {
        if active {
            // One accent ring, the same colour as the app's own action colour. The travelling
            // rainbow this replaces was decoration that matched nothing else in the app; the ring
            // still makes running work unmistakable without inventing a second palette.
            content()
                .padding(inset)
                .overlay {
                    RoundedRectangle(cornerRadius: HubRadius.card, style: .continuous)
                        .stroke(Color.accentColor, lineWidth: 2)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
        } else {
            content().padding(inset)
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

/// A still from a finished video, for the row that made it.
///
/// Video rows had no preview at all, so a clip was a filename and a byte count until it was saved
/// and opened elsewhere. This asks the Hub for the clip's bytes, hands them to AVFoundation, and
/// keeps the first frame — the same shape as `ImageThumbnailStore`, with one difference that
/// matters: **a video is fetched to a file, not held in memory**, because a clip can be far larger
/// than an image and `AVAssetImageGenerator` needs a URL to seek in.
///
/// The scratch file lives under the app's own Playback directory (the same folder the audio player
/// uses) and is removed as soon as the frame is taken, so a thumbnail never leaves a video copy
/// behind. Only the downscaled frame is cached.
final class VideoThumbnailStore: ObservableObject {
    @Published private(set) var thumbnails: [String: NSImage] = [:]
    private var loading: Set<String> = []
    /// The same soft ceiling the image store keeps, for the same reason: the Hub prunes history,
    /// this only stops a long session pinning decoded frames forever.
    private let limit = 120

    func thumbnail(for job: NativeJob, using workspace: NativeWorkspaceStore) async -> NSImage? {
        if let ready = thumbnails[job.id] { return ready }
        guard job.state == "done", !loading.contains(job.id) else { return nil }
        loading.insert(job.id)
        defer { loading.remove(job.id) }
        do {
            let data = try await workspace.videoData(for: job)
            guard let frame = await Self.firstFrame(of: data, using: workspace) else { return nil }
            if thumbnails.count >= limit { thumbnails.removeAll() }
            thumbnails[job.id] = frame
            return frame
        } catch {
            return nil
        }
    }

    /// The clip's first frame, downscaled. Written to a file first because the generator seeks —
    /// and `AVAsset` cannot seek in bytes it has no URL for.
    @MainActor
    private static func firstFrame(of data: Data, using workspace: NativeWorkspaceStore) async -> NSImage? {
        guard let file = try? workspace.playbackFile(named: "thumb-\(UUID().uuidString).mp4") else {
            return nil
        }
        do {
            try data.write(to: file, options: [.atomic, .completeFileProtection])
        } catch { return nil }
        defer { try? FileManager.default.removeItem(at: file) }
        let asset = AVURLAsset(url: file)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 144, height: 144)
        // A tenth of a second in, not frame zero: the first frame of an image-to-video clip is the
        // still that was submitted, which would show the input rather than the animation.
        let time = CMTime(seconds: 0.1, preferredTimescale: 600)
        guard let cgImage = try? await generator.image(at: time).image else { return nil }
        return NSImage(cgImage: cgImage, size: NSSize(width: cgImage.width, height: cgImage.height))
    }
}

/// The clip, playing in place.
///
/// The bytes come from the Hub like every other artifact, are written to the app's own Playback
/// folder because `AVPlayer` needs a file, and are removed when the sheet closes.
private struct VideoPreviewSheet: View {
    let job: NativeJob
    @ObservedObject var workspace: NativeWorkspaceStore
    let dismiss: () -> Void

    @State private var url: URL?
    @State private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    Text(job.text.isEmpty ? "Generated clip" : job.text)
                        .font(HubType.rowTitle).lineLimit(2)
                    if !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(HubType.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Group {
                if let url {
                    VideoPlayer(player: AVPlayer(url: url))
                } else if let failure {
                    Text(failure).foregroundStyle(HubStatus.bad.textColor).fixedSize(horizontal: false, vertical: true)
                } else {
                    HStack(spacing: HubSpace.m) {
                        ProgressView().controlSize(.small)
                        Text("Loading the clip…").foregroundStyle(.secondary)
                    }
                }
            }
            .frame(minWidth: 720, minHeight: 420)
        }
        .padding(20)
        .task(id: job.id) { await load() }
        .onDisappear {
            if let url { try? FileManager.default.removeItem(at: url) }
        }
    }

    private func load() async {
        guard url == nil, failure == nil else { return }
        do {
            let data = try await workspace.videoData(for: job)
            let file = try workspace.playbackFile(named: "preview-\(job.id).mp4")
            try data.write(to: file, options: [.atomic, .completeFileProtection])
            url = file
        } catch {
            failure = error.localizedDescription
        }
    }
}

/// How one final render is named and drawn, in the one place both the row and the detail use it.
///
/// **It names nothing the Hub did not record.** The packet Story sends carries the Story instance,
/// the intent, the attempt and the gateway's own keys — no channel and no episode — so a render is
/// titled by its Hub job id and the detail says which Story instance it belongs to. A name invented
/// here would be a name the customer cannot match to anything.
enum RenderCopy {
    static func title(_ render: NativeRender) -> String {
        let kind = render.purpose == "qualification" ? "Qualification render" : "Final render"
        // The channel and episode Story Studio named this render with, when it sent them. The
        // job id the row used to be titled by is still in Details, so nothing is lost by naming
        // the render the way the customer knows it.
        if let named = render.display?.label { return "\(kind) · \(named)" }
        // No name from Story Studio: the kind and the day identify the row; the full time and
        // machine are in its facts. A job id is never a title.
        let day = render.createdAt.map {
            Date(timeIntervalSince1970: $0).formatted(.dateTime.day().month(.abbreviated))
        }
        return day.map { "\(kind) · \($0)" } ?? kind
    }

    /// The one line under the title: only the parts the render actually states.
    static func facts(_ render: NativeRender) -> String {
        var parts: [String] = []
        // A failed or cancelled attempt produced no video, so the packet's length is not shown as
        // though it had. A finished render with no stated length says so, rather than leaving a gap
        // a reader would fill with "0" (AGENTS.md §6).
        if render.outcome == "succeeded" {
            parts.append(render.videoSeconds.map { NativeJob.clock($0) } ?? "length not recorded")
        }
        if let width = render.width, let height = render.height {
            parts.append("\(Int(width))×\(Int(height))")
        }
        if let fps = render.frameRate { parts.append("\(Int(fps)) fps") }
        if let wall = render.wallSeconds {
            parts.append("\(NativeJob.clock(wall)) to render")
        } else if let running = render.runningSeconds {
            parts.append("running \(NativeJob.clock(running))")
        }
        if let bytes = render.outputBytes {
            parts.append(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file))
        }
        parts.append(machineName(render))
        if let created = render.createdAt {
            parts.append(Date(timeIntervalSince1970: created).formatted(date: .abbreviated, time: .shortened))
        }
        return parts.joined(separator: " · ")
    }

    static func machineName(_ render: NativeRender) -> String {
        if let name = render.machineName, !name.isEmpty { return name }
        let machine = render.machine ?? "local"
        return machine == "local" ? "This Mac" : machine
    }

    static func status(_ render: NativeRender) -> HubStatus {
        switch render.outcome {
        case "succeeded": return .ok
        case "failed": return .bad
        case "cancelled": return .off
        default: return .info
        }
    }

    static func outcomeLabel(_ render: NativeRender) -> String {
        switch render.outcome {
        case "succeeded": return "Done"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        default: return "Rendering"
        }
    }

    static func symbol(_ render: NativeRender) -> String {
        switch render.outcome {
        case "succeeded": return "checkmark.circle.fill"
        case "failed": return "xmark.octagon.fill"
        case "cancelled": return "slash.circle.fill"
        default: return "bolt.fill"
        }
    }

    /// The encode the packet asked for, in the packet's own words.
    static func profile(_ render: NativeRender) -> String? {
        var parts: [String] = []
        if let codec = render.codec { parts.append(codec.uppercased()) }
        if let effort = render.effort { parts.append(effort) }
        if let crf = render.crf { parts.append("CRF \(Int(crf))") }
        if let container = render.container { parts.append(container.uppercased()) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The produced length, and — for an attempt that produced nothing — what the packet asked for,
    /// said as what it is.
    static func video(_ render: NativeRender) -> String {
        if render.outcome == "succeeded" {
            return render.videoSeconds.map { NativeJob.clock($0) } ?? "length not recorded"
        }
        guard let planned = render.videoSeconds else { return "none produced" }
        return "none produced (the packet asked for \(NativeJob.clock(planned)))"
    }

    static func when(_ seconds: Double?) -> String {
        guard let seconds else { return "not recorded" }
        return Date(timeIntervalSince1970: seconds).formatted(date: .abbreviated, time: .standard)
    }
}

/// One render in full: the numbers, the profile, the hash, why it failed, and the video itself when
/// this Mac still holds it.
///
/// The file is played from disk rather than streamed through the Hub: it lives on this Mac, and the
/// Hub deletes nothing here — an accepted output is returned to Story Studio and its attempt folder
/// is retired after Story acknowledges it. When it is gone the page says exactly that instead of
/// offering a Play button that would fail.
private struct RenderDetailSheet: View {
    let render: NativeRender
    let dismiss: () -> Void

    @State private var player: AVPlayer?

    var body: some View {
        VStack(alignment: .leading, spacing: HubSpace.l) {
            HStack(alignment: .top, spacing: HubSpace.m) {
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    Text(RenderCopy.title(render)).font(HubType.rowTitle)
                    Text(RenderCopy.facts(render)).font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: HubSpace.m)
                StatusBadge(text: RenderCopy.outcomeLabel(render), status: RenderCopy.status(render))
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            if let failure = render.failure {
                HubBanner(status: .bad, symbol: "xmark.octagon.fill", message: failure)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: HubSpace.s) {
                    Group {
                        ValueRow("Video", value: RenderCopy.video(render))
                        ValueRow("Canvas", value: render.width.flatMap { width in
                            render.height.map { "\(Int(width))×\(Int($0))" } } ?? "not recorded")
                        ValueRow("Frame rate", value: render.frameRate.map { "\(Int($0)) fps" } ?? "not recorded")
                        ValueRow("Encode", value: RenderCopy.profile(render) ?? "not recorded")
                        ValueRow("Render time", value: render.wallSeconds.map { NativeJob.clock($0) }
                            ?? (render.runningSeconds.map { "running \(NativeJob.clock($0))" } ?? "not recorded"))
                        ValueRow("Output size", value: render.outputBytes.map {
                            ByteCountFormatter.string(fromByteCount: Int64($0), countStyle: .file) } ?? "not recorded")
                        ValueRow("Encoder", value: render.tier ?? "not recorded")
                    }
                    Divider()
                    Group {
                        ValueRow("Rendered on", value: RenderCopy.machineName(render))
                        ValueRow("When", value: RenderCopy.when(render.createdAt))
                        ValueRow("Finished", value: render.finishedAt == nil ? "not recorded" : RenderCopy.when(render.finishedAt))
                        ValueRow("Hub job id") {
                            Text(render.id).font(HubType.monoCaption).textSelection(.enabled)
                        }
                        if let hash = render.outputSha256 {
                            ValueRow("File hash (SHA-256)") {
                                Text(hash).font(HubType.monoCaption).textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        if let profile = render.profileDigest {
                            ValueRow("Profile") {
                                Text(profile).font(HubType.monoCaption).textSelection(.enabled)
                                    .lineLimit(1).truncationMode(.middle)
                            }
                        }
                    }
                    Divider()
                    Group {
                        ValueRow("Story instance", value: render.storyUuid ?? "not recorded")
                        if let channel = render.display?.channel {
                            ValueRow("Channel", value: channel)
                        }
                        if let episode = render.display?.episode {
                            ValueRow("Episode", value: episode)
                        }
                        ValueRow("Intent", value: render.intentId ?? "not recorded")
                        ValueRow("Attempt", value: render.attemptNo.map(String.init) ?? "not recorded")
                        ValueRow("Gateway attempt", value: render.gatewayAttemptKey ?? "not recorded")
                        if let key = render.dispatchKey {
                            ValueRow("Dispatch key") {
                                Text(key).font(HubType.monoCaption).textSelection(.enabled)
                                    .lineLimit(1).truncationMode(.middle)
                            }
                        }
                    }
                    Divider()
                    media
                }
            }
        }
        .padding(HubSpace.xl)
        .frame(minWidth: 720, minHeight: 480)
        .task(id: render.id) {
            guard let path = render.localFile, FileManager.default.fileExists(atPath: path) else { return }
            player = AVPlayer(url: URL(fileURLWithPath: path))
        }
        .onDisappear { player?.pause() }
    }

    /// The video, or where it went. **Inside the same scroll as the facts**, never pinned below a
    /// fixed-height list: a player under a block that has its own height is the part that gets cut
    /// off on a short window, and the one thing the owner asked to be able to see.
    @ViewBuilder private var media: some View {
        if let player {
            VideoPlayer(player: player).frame(height: 320)
        } else if render.localFile != nil {
            HStack(spacing: HubSpace.m) {
                ProgressView().controlSize(.small)
                Text("Loading the video…").foregroundStyle(.secondary)
            }
            .frame(height: 60, alignment: .center)
        } else {
            HubBanner(status: .off, symbol: "arrow.up.forward.square",
                      title: "The video is not on this Mac any more",
                      message: "An accepted render is returned to Story Studio and its files are retired here once Story acknowledges them, so this Hub no longer holds a copy. Story has the finished video.")
        }
    }
}

/// One low-cost still from a completed video. Same contract as `JobThumbnail`: a placeholder until
/// the frame arrives, and nothing else in the row waits on it.
private struct VideoThumbnail: View {
    let job: NativeJob
    let connected: Bool
    @ObservedObject var store: VideoThumbnailStore
    @ObservedObject var workspace: NativeWorkspaceStore

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor))
            if let image = store.thumbnails[job.id] {
                Image(nsImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: "film").font(HubType.icon).foregroundStyle(.tertiary)
            }
        }
        .frame(width: 64, height: 64)
        .clipShape(RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous)
            .stroke(Color(nsColor: .separatorColor), lineWidth: 1))
        .accessibilityLabel(job.text.isEmpty ? "Generated video thumbnail" : "Thumbnail of \(job.text)")
        .task(id: "\(job.id)-\(connected)") {
            guard connected else { return }
            _ = await store.thumbnail(for: job, using: workspace)
        }
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
            RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor))
            if let image = store.thumbnails[job.id] {
                Image(nsImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: "photo").font(HubType.icon).foregroundStyle(.tertiary)
            }
        }
        .frame(width: 64, height: 64)
        .clipShape(RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous)
            .stroke(Color(nsColor: .separatorColor), lineWidth: 1))
        .accessibilityLabel(job.text.isEmpty ? "Generated image thumbnail" : "Thumbnail of \(job.text)")
        .task(id: "\(job.id)-\(connected)") {
            guard connected else { return }
            _ = await store.thumbnail(for: job, using: workspace)
        }
    }
}

/// One width for every row action in the app.
///
/// The owner's report: *"the buttons change sizes each time. i want them stable and consistent."*
/// Each action was sized by its own label, so `Save…` was narrow and `Check Original Result` was
/// wide, and the column moved as the list scrolled — a row's controls read as part of the row's
/// content rather than as a column of controls.
///
/// Every action button now takes the same width and every row reserves the same trailing space, so
/// the column is the same on every row of every Studio and on the Jobs tab.
private struct RowActionButton: ViewModifier {
    func body(content: Content) -> some View {
        content.buttonStyle(RowButtonStyle())
    }
}

/// The button itself is 104 points wide, not a 104-point frame around a bezel as wide as its label:
/// `Play` and `Show in Folder` are the same size on the screen, which is what the owner asked for.
private struct RowButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(HubType.secondary)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .foregroundStyle(configuration.role == .destructive ? HubStatus.bad.textColor : Color.primary)
            .frame(width: 104, height: 24)
            .background(RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous)
                .fill(Color.primary.opacity(configuration.isPressed ? 0.22 : 0.12)))
            .opacity(isEnabled ? 1 : 0.4)
            .contentShape(RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous))
    }
}

private extension View {
    /// Marks a row action: one width, one font, one place to change both.
    func rowAction() -> some View { modifier(RowActionButton()) }
}

/// The actions of one row: a block of fixed width, two buttons across, as many rows as it takes.
///
/// The block used to be one `HStack` in a 214-point frame, which is two buttons and their spacing.
/// A finished Music row has four (Preview, Play, Save…, Show in Folder) and an Image or Voice row
/// three, so the buttons overflowed that frame to the left and sat on top of the row's text — the
/// owner's report, *"Play sits on the text"*. Two across, wrapping, makes the block's width true
/// for any number of buttons, so there is nothing left to overflow.
private struct RowActions<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        TwoAcrossLayout { content }
            .layoutValue(key: RowSlotKey.self, value: .actions)
    }
}

private enum RowSlot { case content, actions }

private struct RowSlotKey: LayoutValueKey { static let defaultValue = RowSlot.content }

/// Two fixed-width columns, filled left to right. The width is the same for one button and for
/// four, so every row's block has the same edges.
private struct TwoAcrossLayout: Layout {
    static let column: CGFloat = 104
    static let gap: CGFloat = 6
    static var width: CGFloat { column * 2 + gap }

    private func rows(_ subviews: Subviews) -> [[LayoutSubviews.Element]] {
        stride(from: 0, to: subviews.count, by: 2).map { Array(subviews[$0..<min($0 + 2, subviews.count)]) }
    }

    private func heights(_ rows: [[LayoutSubviews.Element]]) -> [CGFloat] {
        rows.map { row in
            row.map { $0.sizeThatFits(.init(width: Self.column, height: nil)).height }.max() ?? 0
        }
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let heights = heights(rows(subviews))
        guard !heights.isEmpty else { return .zero }
        return CGSize(width: Self.width, height: heights.reduce(0, +) + Self.gap * CGFloat(heights.count - 1))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let grouped = rows(subviews)
        let heights = heights(grouped)
        var y = bounds.minY
        for (index, row) in grouped.enumerated() {
            for (column, subview) in row.enumerated() {
                subview.place(at: CGPoint(x: bounds.minX + CGFloat(column) * (Self.column + Self.gap), y: y),
                              anchor: .topLeading, proposal: .init(width: Self.column, height: heights[index]))
            }
            y += heights[index] + Self.gap
        }
    }
}

/// One finished-work row: an optional leading mark, the text, and the actions.
///
/// **The text and the actions never share space.** Wide enough, the actions are a column at the
/// trailing edge and the text takes what is left of the row. Not wide enough — the right-hand column
/// of the Image and Music pages is a few hundred points — the actions sit under the text, so a long
/// prompt wraps or truncates in its own column at any window width instead of running under a
/// button. This is the one place that decides, so every Studio's list and the Jobs tab agree.
///
/// Children are the leading mark (optional), the text, and a `RowActions` (optional), in that order.
private struct ActionRow: Layout {
    private let spacing: CGFloat = 12
    private let narrowestText: CGFloat = 280

    private struct Parts {
        var lead: LayoutSubviews.Element?
        var text: LayoutSubviews.Element?
        var actions: LayoutSubviews.Element?
    }

    private func parts(_ subviews: Subviews) -> Parts {
        let plain = subviews.filter { $0[RowSlotKey.self] == .content }
        return Parts(lead: plain.count > 1 ? plain.first : nil, text: plain.last,
                     actions: subviews.first { $0[RowSlotKey.self] == .actions })
    }

    private struct Plan {
        var leadSize = CGSize.zero
        var textWidth: CGFloat = 0
        var textSize = CGSize.zero
        var actionsSize = CGSize.zero
        var trailing = true
        var size = CGSize.zero
    }

    private func plan(_ parts: Parts, width proposed: CGFloat?) -> Plan {
        var plan = Plan()
        let width = proposed ?? 760
        plan.leadSize = parts.lead?.sizeThatFits(.unspecified) ?? .zero
        plan.actionsSize = parts.actions?.sizeThatFits(.unspecified) ?? .zero
        let lead = parts.lead == nil ? 0 : plan.leadSize.width + spacing
        let actions = parts.actions == nil ? 0 : plan.actionsSize.width + spacing
        plan.trailing = lead + narrowestText + actions <= width
        plan.textWidth = max(0, width - lead - (plan.trailing ? actions : 0))
        plan.textSize = parts.text?.sizeThatFits(.init(width: plan.textWidth, height: nil)) ?? .zero
        let head = max(plan.leadSize.height, plan.textSize.height)
        let height = plan.trailing ? max(head, plan.actionsSize.height)
            : head + (parts.actions == nil ? 0 : spacing / 2 + plan.actionsSize.height)
        plan.size = CGSize(width: width, height: height)
        return plan
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        plan(parts(subviews), width: proposal.width).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let parts = parts(subviews)
        let plan = plan(parts, width: bounds.width)
        let lead = parts.lead == nil ? 0 : plan.leadSize.width + spacing
        parts.lead?.place(at: bounds.origin, anchor: .topLeading, proposal: .init(plan.leadSize))
        parts.text?.place(at: CGPoint(x: bounds.minX + lead, y: bounds.minY), anchor: .topLeading,
                          proposal: .init(width: plan.textWidth, height: plan.textSize.height))
        if plan.trailing {
            parts.actions?.place(at: CGPoint(x: bounds.maxX - plan.actionsSize.width, y: bounds.minY),
                                 anchor: .topLeading, proposal: .init(plan.actionsSize))
        } else {
            let head = max(plan.leadSize.height, plan.textSize.height)
            parts.actions?.place(at: CGPoint(x: bounds.minX + lead, y: bounds.minY + head + spacing / 2),
                                 anchor: .topLeading, proposal: .init(plan.actionsSize))
        }
    }
}

/// The scrolling list of finished work a Studio page shows under its "Recent …" heading.
///
/// The five panels were five copies of `ScrollView { LazyVStack(spacing: HubSpace.m) { ForEach(…) } }`, so
/// no row had a boundary: on the dark workspace every row sat directly on the panel behind it and
/// the owner could not tell where one finished and the next began — *"they all in the same black
/// background"*. The Jobs page had drawn a `Divider()` between its rows all along; this is that
/// same division, in one place, so the five panels cannot drift apart one fix at a time.
private struct RecentRows<Item: Identifiable, Row: View>: View {
    let items: [Item]
    let row: (Item) -> Row

    init(items: [Item], @ViewBuilder row: @escaping (Item) -> Row) {
        self.items = items
        self.row = row
    }

    var body: some View {
        LazyVStack(alignment: .leading, spacing: HubSpace.m) {
            ForEach(items) { item in
                row(item)
                Divider()
            }
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
        HStack(spacing: HubSpace.tight) {
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
        HStack(spacing: HubSpace.tight) {
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

/// Local registered functions and dispatch priority; neither choice resumes a paused Mac.
private struct WorkPolicySettings: View {
    @ObservedObject var workspace: NativeWorkspaceStore
    let connected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            Text("Choose the functions this Mac accepts. These choices apply only to this Mac.")
                .font(HubType.secondary).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let policy = workspace.workPolicy {
                Text(connected
                     ? (policy.machineEnabled ? "Local work is enabled. Gateway or fleet pauses still apply."
                        : "Local work is paused. Resume it on Gateway's Fleet page; changing these choices keeps it paused.")
                     : "Services are disconnected. The last saved choices are shown below.")
                    .font(HubType.secondary)
                    .foregroundStyle(!policy.machineEnabled && connected ? HubStatus.warn.textColor : Color.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(policy.studios) { studio in
                    Toggle(studio.title, isOn: Binding(
                        get: { studio.enabled },
                        set: { enabled in Task { _ = await workspace.saveWorkStudio(studio.id, enabled: enabled) } }))
                        .toggleStyle(.checkbox)
                        .accessibilityHint("Accept \(studio.title) jobs on this Mac")
                }
                if policy.studios.isEmpty {
                    Text("No local Studio functions are registered yet.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                }
                Divider()
                Picker("Job priority", selection: Binding(
                    get: { !policy.priorityOrder.isEmpty },
                    set: { custom in Task {
                        _ = await workspace.saveWorkPriority(custom ? policy.priorityFunctions.map(\.id) : [])
                    } })) {
                    Text("No preference").tag(false)
                    Text("Custom order").tag(true)
                }
                .pickerStyle(.menu)
                .fixedSize()
                .disabled(!policy.supportsFunctionPriority || policy.priorityFunctions.isEmpty)
                .accessibilityHint("Choose equal priority or a custom order for this Mac")
                if !policy.supportsFunctionPriority {
                    if policy.priorityOrder == ["chat"] {
                        Text("Chat jobs first; other enabled functions have equal priority.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text("Restart the updated Hub to change job priority. The last saved choice is shown.")
                        .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                } else if !policy.priorityOrder.isEmpty {
                    ForEach(Array(policy.enabledPriorityFunctions.enumerated()), id: \.element.id) { index, function in
                        if !policy.priorityOrder.contains(function.id)
                            && (index == 0 || policy.priorityOrder.contains(policy.enabledPriorityFunctions[index - 1].id)) {
                            Text("Equal after ranked functions")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                        }
                        HStack(spacing: HubSpace.s) {
                            Text(policy.priorityOrder.contains(function.id) ? "\(index + 1)." : "Equal")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                                .frame(width: 44, alignment: .leading)
                            // A plain label: the enable switch is the checkbox above, and the
                            // xmark below. A third checkbox here was the same choice twice.
                            Text(function.title)
                                .foregroundStyle(.primary)
                            Spacer(minLength: HubSpace.s)
                            Button {
                                if let order = policy.priorityOrder(moving: function.id, by: -1) {
                                    Task { _ = await workspace.saveWorkPriority(order) }
                                }
                            } label: { Image(systemName: "arrow.up") }
                                .disabled(index == 0)
                                .accessibilityLabel("Move \(function.title) up")
                                .help("Move \(function.title) up")
                            Button {
                                if let order = policy.priorityOrder(moving: function.id, by: 1) {
                                    Task { _ = await workspace.saveWorkPriority(order) }
                                }
                            } label: { Image(systemName: "arrow.down") }
                                .disabled(index == policy.enabledPriorityFunctions.count - 1)
                                .accessibilityLabel("Move \(function.title) down")
                                .help("Move \(function.title) down")
                            Button {
                                Task { _ = await workspace.saveWorkFunction(function.id, enabled: !function.enabled) }
                            } label: { Image(systemName: function.enabled ? "xmark" : "plus") }
                                .accessibilityLabel(function.enabled ? "Switch \(function.title) off" : "Switch \(function.title) on")
                                .help(function.enabled ? "Switch \(function.title) off on this Mac" : "Switch \(function.title) on on this Mac")
                        }
                        .buttonStyle(.bordered).controlSize(.small)
                    }
                    if policy.enabledPriorityFunctions.isEmpty {
                        Text("Enable a function above to include it in job priority.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                    }
                    Text("Disabled functions keep their saved priority until enabled. Moving a function gives the enabled functions a custom order.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(policy.priorityOrder.isEmpty
                     ? "All enabled functions have equal priority. The current job finishes, then the oldest eligible queued job runs."
                     : "The current job finishes. Higher-ranked eligible queued work runs first, oldest first at the same rank. Other enabled work runs when higher-ranked work is not waiting.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if policy.supportsPreferredWork {
                    Divider()
                    PreferredWorkSection(workspace: workspace, policy: policy)
                }
            } else {
                Text(connected ? "Reading work choices…" : "Start local services to read and change this Mac's work choices.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            }
            if workspace.savingWorkPolicy {
                ProgressView("Saving work choice…").controlSize(.small)
            }
            if let note = workspace.workPolicyNote {
                Text(note).font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
        }
        .disabled(!connected || workspace.savingWorkPolicy)
        .task(id: "\(connected)/\(workspace.connectionGeneration)") {
            guard connected else { return }
            while !Task.isCancelled {
                await workspace.refreshWorkPolicy()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }
}

/// The preferred-generation idle hold (`components/hub/backend/preferred_hold.py`): pick one
/// generation function this Mac serves and how long it stays reserved for it after its last
/// preferred job finishes. OFF by default; episode renders are never held.
private struct PreferredWorkSection: View {
    @ObservedObject var workspace: NativeWorkspaceStore
    let policy: NativeWorkPolicy

    var body: some View {
        Toggle("Keep this Mac ready for", isOn: Binding(
            get: { policy.preferredWork.enabled },
            set: { on in Task {
                _ = await workspace.savePreferredWork(
                    enabled: on,
                    modality: on ? (policy.preferredWork.modality ?? policy.preferredChoices.first) : nil,
                    idleSeconds: policy.preferredWork.idleSeconds)
            } }))
            .toggleStyle(.checkbox)
            .disabled(policy.preferredChoices.isEmpty)
            .accessibilityHint("Reserve this Mac for one function between jobs")
        if !policy.preferredChoices.isEmpty {
            HStack(spacing: HubSpace.s) {
                Picker("Function", selection: Binding(
                    get: { policy.preferredWork.modality ?? policy.preferredChoices.first },
                    set: { modality in Task {
                        _ = await workspace.savePreferredWork(
                            enabled: policy.preferredWork.enabled,
                            modality: modality,
                            idleSeconds: policy.preferredWork.idleSeconds)
                    } })) {
                    ForEach(policy.preferredChoices, id: \.self) { choice in
                        Text(title(for: choice)).tag(choice as String?)
                    }
                }
                .pickerStyle(.menu).fixedSize()
                .disabled(!policy.preferredWork.enabled)
                .frame(maxWidth: 220, alignment: .leading)
                // Any whole number of seconds 1-3600 (the API's own bounds), typed or stepped;
                // a value saved elsewhere (a custom N from the Gateway) is shown and re-saved
                // exactly, never rounded to a preset.
                HStack(spacing: HubSpace.xs) {
                    TextField("Hold seconds", value: Binding(
                        get: { policy.preferredWork.idleSeconds },
                        set: { seconds in Task {
                            _ = await workspace.savePreferredWork(
                                enabled: policy.preferredWork.enabled,
                                modality: policy.preferredWork.modality
                                    ?? policy.preferredChoices.first,
                                idleSeconds: seconds)
                        } }), format: .number.grouping(.never))
                        .textFieldStyle(.roundedBorder).multilineTextAlignment(.trailing)
                        .frame(width: 56)
                        .disabled(!policy.preferredWork.enabled)
                        .accessibilityLabel("Hold seconds after the last job")
                    Stepper("", value: Binding(
                        get: { policy.preferredWork.idleSeconds },
                        set: { seconds in Task {
                            _ = await workspace.savePreferredWork(
                                enabled: policy.preferredWork.enabled,
                                modality: policy.preferredWork.modality
                                    ?? policy.preferredChoices.first,
                                idleSeconds: seconds)
                        } }), in: 1...3600).labelsHidden()
                        .disabled(!policy.preferredWork.enabled)
                }
                Text("s")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            }
        }
        Text(stateText)
            .font(HubType.secondary).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        Text("Episode renders are never held or delayed by this choice. A running job of any kind always finishes first, and the Mac's model memory still follows its own model-memory setting.")
            .font(HubType.secondary).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var stateText: String {
        let name = policy.preferredWork.modality.flatMap { title(for: $0) } ?? "the chosen function"
        switch policy.preferredState?.phase {
        case "working":
            return "\(name) work is on this Mac now; it stays reserved while that continues."
        case "holding":
            let seconds = Int((policy.preferredState?.remainingS ?? 0).rounded())
            return seconds >= 60
                ? "Holding this Mac for \(name): about \(seconds / 60) min remaining."
                : "Holding this Mac for \(name): under a minute remaining."
        case "lending":
            return "Available for other work until the next \(name) job."
        default:
            return "Off: all enabled functions have equal priority, as job priority above decides."
        }
    }

    private func title(for modality: String) -> String {
        policy.priorityFunctions.first { $0.id == modality }?.title
            ?? modality.capitalized
    }
}

/// Lite Mode on this Mac (internally "workstation mode", `components/hub/workstation.py`): a shop's own
/// Mac lends the fleet its idle time. The same settings, wording and sentence as the Gateway's Fleet
/// sheet (`FleetWorkstation.swift`), read from and saved to this Mac's Hub - the one copy both edit.
/// Saving needs no restart: the Hub reads the file on every decision and the menu-bar icon follows it
/// within a few seconds.
private struct LiteModeSettings: View {
    @ObservedObject var workspace: NativeWorkspaceStore
    let connected: Bool
    /// What is being edited, and the saved settings it was made from. A poll that brings new saved
    /// settings replaces the draft only while nothing has been typed.
    @State private var draft = NativeWorkstationSettings()
    @State private var quietText = ""
    @State private var base: NativeWorkstationSettings?

    static let idleHint = "Takes work only after this many minutes with no keyboard or mouse use, or while the screen is locked."
    static let stepAsideHint = "When someone uses the keyboard or mouse, this Mac finishes its current job, then takes no "
        + "new work until it has had this many minutes without input."
    static let startFreeHint = "A job starts only when this much memory is free. A running job always finishes; it is "
        + "stopped only if macOS reports critical memory pressure for 20 seconds (very rare), and then moves to another Mac."
    static let quietHint = "No work during these hours. Separate several with commas, like 22:00-07:00, 12:00-13:00."
    static let hideIconHint = "With the icon hidden, open Hub Studio KH from Applications or Spotlight to come back to these settings."

    /// The draft as it would be saved: quiet hours from the text field.
    private var edited: NativeWorkstationSettings {
        var settings = draft
        settings.quietHours = quietText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        return settings
    }
    private var dirty: Bool { base != nil && edited != base }

    var body: some View {
        VStack(alignment: .leading, spacing: HubSpace.m) { content }
            .onAppear { adopt(workspace.workstation?.settings) }
            .onChange(of: workspace.workstation?.settings) { _, saved in adopt(saved) }
            // The state ("Now") changes as someone uses this Mac, so it is read again while shown.
            .task(id: connected) {
                guard connected else { return }
                while !Task.isCancelled {
                    await workspace.refreshWorkstation()
                    try? await Task.sleep(for: .seconds(5))
                }
            }
    }

    @ViewBuilder private var content: some View {
        if let current = workspace.workstation {
            Toggle("Lite Mode", isOn: $draft.enabled).toggleStyle(.checkbox).font(HubType.rowTitle)
            // Follows the boxes as they are ticked, before Save, so it always describes what Save will do.
            Text(edited.policySentence)
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            ValueRow("Now", value: current.now)
            if let today = current.today {
                Text(today).font(HubType.caption).foregroundStyle(.secondary)
            }
            group {
                Toggle("Only when idle", isOn: $draft.idleOnly).toggleStyle(.checkbox)
                indented { Text("Idle for"); minutes($draft.idleMinutes, "Idle minutes"); Text("minutes") }
                hint(Self.idleHint)
            }
            group {
                Toggle("Step aside when someone uses it", isOn: $draft.stepAside).toggleStyle(.checkbox)
                indented { Text("Resume after"); minutes($draft.resumeAfterMinutes, "Resume minutes"); Text("minutes without input") }
                hint(Self.stepAsideHint)
            }
            group {
                Text("Free memory needed to start a job")
                indented {
                    Picker("Free memory needed to start a job", selection: Binding(
                        get: { draft.startFreeGb == nil ? 0 : 1 },
                        set: { draft.startFreeGb = $0 == 0 ? nil : (draft.startFreeGb ?? 2.8) })) {
                        Text("Each model's own (about 2.8 GB)").tag(0)
                        Text("Custom").tag(1)
                    }.labelsHidden().fixedSize()
                    TextField("GB", value: Binding(
                        get: { draft.startFreeGb ?? 2.8 },
                        set: { if $0.isFinite && (0.5...8).contains($0) { draft.startFreeGb = ($0 * 10).rounded() / 10 } }),
                              format: .number.precision(.fractionLength(1)))
                        .textFieldStyle(.roundedBorder).multilineTextAlignment(.trailing).frame(width: 52)
                        .disabled(draft.startFreeGb == nil).accessibilityLabel("Free memory in GB")
                    Text("GB")
                }
                hint(Self.startFreeHint)
            }
            group {
                Text("Quiet hours")
                indented { TextField("22:00-07:00", text: $quietText).textFieldStyle(.roundedBorder).frame(width: 240) }
                hint(Self.quietHint)
            }
            group {
                Toggle("Hide the Hub Studio KH menu-bar icon", isOn: $draft.hideMenuBarIcon).toggleStyle(.checkbox)
                hint(Self.hideIconHint)
            }
            HStack {
                Button("Save") {
                    let wanted = edited
                    Task { if await workspace.saveWorkstation(wanted) { adopt(workspace.workstation?.settings, force: true) } }
                }
                .buttonStyle(.borderedProminent).disabled(!connected || !dirty || workspace.savingWorkstation)
                if dirty { Button("Revert") { adopt(base, force: true) }.disabled(workspace.savingWorkstation) }
                if workspace.savingWorkstation { ProgressView().controlSize(.small) }
            }
            if let note = workspace.workstationNote {
                Text(note).font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Text("A fleet's Gateway can also see and change these settings from its Fleet page.")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        } else {
            Text(connected ? (workspace.workstationNote ?? "Reading Lite Mode settings…")
                 : "Start local services to read and change Lite Mode.")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The Hub's saved settings become the draft when nothing has been typed (or when asked to).
    private func adopt(_ saved: NativeWorkstationSettings?, force: Bool = false) {
        guard let saved else { return }
        if force || base == nil || !dirty {
            draft = saved
            quietText = saved.quietHours.joined(separator: ", ")
        }
        base = saved
    }

    private func group<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.xs) { content() }
    }
    /// A control line under its heading, indented to the checkbox's text.
    private func indented<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        HStack(spacing: HubSpace.s) { content() }.padding(.leading, 20)
    }
    private func hint(_ text: String) -> some View {
        Text(text).font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            .padding(.leading, 20)
    }
    private func minutes(_ value: Binding<Int>, _ label: String) -> some View {
        HStack(spacing: HubSpace.xs) {
            TextField(label, value: Binding(get: { value.wrappedValue }, set: { if (1...60).contains($0) { value.wrappedValue = $0 } }),
                      format: .number.grouping(.never))
                .textFieldStyle(.roundedBorder).multilineTextAlignment(.trailing).frame(width: 44).accessibilityLabel(label)
            Stepper(label, value: value, in: 1...60).labelsHidden()
        }
    }
}

/// Which history an uncertain job belongs to, so one confirmation can act on all three rows.
private enum DiscardKind { case image, audio, music, video }

/// An uncertain job the operator asked to abandon, held until they confirm.
private struct PendingDiscard: Identifiable {
    let id: String
    let job: NativeJob
    let kind: DiscardKind
}

/// A removal confirmation belongs to the roster and connection that opened it. Keeping both
/// prevents a dialog left open across reconnect from acting on the replacement connection.
private struct PendingNodeRemoval {
    let node: NativeFleetNode
    let connectionGeneration: Int
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
    /// The install screen's own search and the preset the operator last picked. Local view state on
    /// purpose: which card is highlighted is not a decision about the Mac until its button is
    /// pressed, so it must not be published, persisted or shared with the Gateway.
    @State private var setupSearch = ""
    @State private var setupSelectedPreset: String? = nil
    @State private var modelFilter = "All functions"
    @State private var installedOnly = false
    @State private var selectedModel: String?
    /// The Studios whose Models section the operator has folded away. A search ignores this, so a
    /// result can never stay hidden inside a collapsed section.
    @State private var collapsedModelSections: Set<String> = []
    /// How wide the model list is, and whether a drag is in flight.
    ///
    /// The owner reads the details more often than the model name — *"it should work both ways
    /// because more often than not, I want to see details more than the model name"* — so the
    /// split is dragged either way and the width is remembered across launches.
    @AppStorage("studioHub.modelsListWidth") private var modelsListWidth: Double = 380
    @State private var modelsSplitDragStart: Double?
    /// What the operator has typed into "Your defaults for this model", before it is saved. Cleared
    /// whenever the model or the Studio's published answer changes, so a stale draft cannot be
    /// saved onto a model it was not written for.
    @State private var modelDefaultDraft: [String: NativeJSONValue] = [:]
    @State private var transcriptFormat = "Text"
    @State private var fileActionError: String?
    @State private var hfToken = ""
    @State private var tokenMessage: String?
    @State private var savingToken = false
    @State private var imagePreview: NSImage?
    @State private var imagePreviewError: String?
    @State private var imagePreviewReceipt: String?
    @State private var imagePreviewRetry = 0
    @State private var selectedImageID: String?
    /// The image shown large, over the page. Nil is closed. It is a view over the page rather than a
    /// separate window so it is the same size as the page the operator is on and Esc closes it.
    @State private var imageModalID: String?
    @State private var imageModalImage: NSImage?
    @State private var imageModalError: String?
    @State private var imageModalRetry = 0
    @State private var selectedMusicID: String?
    /// The clip the operator asked to preview, by job id. Nil shows the list as it was.
    @State private var previewVideoID: String?
    /// Which finished Voice clip the shared player is holding, so the transport under the Audio
    /// list controls the clip that is actually loaded rather than whichever row was last drawn.
    @State private var loadedAudioJobID: String?
    @State private var historyTab = HistoryTab.all
    @State private var jobSearch = ""
    @State private var voiceSearch = ""
    @State private var fleetSearch = ""
    /// The Mac whose Details sheet is open; the row's overflow owns the action.
    @State private var fleetDetailRow: NativeFleetRow?
    /// Remembered per list: Table or Cards. Cards are forced below the table's minimum width.
    @AppStorage("hub.fleetView") private var fleetView = "table"
    @State private var pendingModelRemoval: NativeModel?
    /// System, Light or Dark — the only three the app offers, saved on this Mac.
    @AppStorage("studiohub.appearance") private var appearancePreference = "system"
    /// A test machine runs without a model library: no install blocker, no error posture.
    @AppStorage("studiohub.testMachine") private var testMachine = false
    /// Which Macs the Jobs page lists. Offered only where more than one Mac's work can land.
    @State private var jobMachineFilter = JobMachineFilter.all
    /// The same choice on Overview's In progress list, kept separate so narrowing one page does
    /// not silently narrow the other.
    @State private var overviewMachineFilter = JobMachineFilter.all
    /// The tag the Voices page is narrowed to. Nil is every voice; the Gateway owns the tags.
    @State private var voiceTagFilter: String? = nil
    /// The render whose detail is open on the Render page. Nil shows the list as it was.
    @State private var openRender: NativeRender?
    @State private var loadingImage = false
    @State private var connectionNote: String?
    @State private var controllerDraft = ""
    /// The name being typed. Kept apart from the Hub's answer so the field does not fight the poll.
    @State private var machineNameDraft = ""
    /// The Machine ID and the site being typed, same rule. Editable only while this Mac is not in a
    /// group; the Hub refuses the change as well, and its refusal is the authority.
    @State private var machineIdDraft = ""
    @State private var siteIdDraft = ""
    @State private var siteNameDraft = ""
    /// "Machine ID copied" and the like, shown under the row that was copied rather than up in the
    /// token block: the operator just pressed a button here, and a confirmation somewhere else is
    /// a confirmation they will not see.
    @State private var identityCopyNote: String?
    @State private var controllerToken = ""
    /// The role the operator picked in the picker but has not confirmed. The picker itself shows
    /// the Hub's own answer, so a cancelled confirmation leaves the control where it was.
    @State private var pendingRoleChange: String?
    /// Leaving, removing a node and rotating the Fleet token are each one press away from something
    /// that cannot be undone by pressing the same button again, so each is confirmed first.
    @State private var confirmingLeave = false
    /// Resetting a whole group is the largest of these: it drops every Mac this controller knows
    /// and mints a new group secret, so it names how many Macs and what the nodes have to do.
    @State private var confirmingFleetReset = false
    @State private var confirmingRotation = false
    @State private var confirmingHandover = false
    @State private var confirmingRestoreStorage = false
    /// The Trash Folder's contents are moved to macOS Trash only after this confirmation names the
    /// item count and bytes; macOS Trash still holds them, so it is recoverable, not a delete.
    @State private var confirmingTrashMove = false
    /// What cleanup would delete under the limits typed in the storage editor; nil while unknown.
    @State private var draftWouldDelete: Int64?
    @State private var draftTrashHandoffDays = 7
    @State private var draftTrashHandoffGb = 20
    @State private var pendingNodeRemoval: PendingNodeRemoval?
    @State private var nodeRemovalRefusal: String?
    /// The new controller's address and token, typed before the handover runs.
    @State private var handoverAddress = ""
    @State private var handoverToken = ""
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
    /// Video stills are a separate store: they cost a clip download each, and mixing them into the
    /// image cache would evict image previews for frames the operator may never look at again.
    @StateObject private var videoThumbnails = VideoThumbnailStore()
    /// The uncertain job the operator asked to discard, held until they confirm.
    @State private var pendingDiscard: PendingDiscard?
    /// A sample is seeded into an empty prompt the first time a studio is opened, so a brand-new
    /// user has something to press Generate on. The flag keeps it from refilling a field the
    /// operator deliberately cleared.
    @State private var seededImageSample = false
    @State private var seededAudioSample = false
    @State private var seededMusicSample = false
    /// Which Music job the shared player is currently holding, so the preview transport only
    /// claims a clip it actually started.
    @State private var loadedMusicJobID: String?
    /// The window's width, from the one `GeometryReader` at the root of `body`, which decides whether
    /// the node table is one line per Mac or a card per Mac.
    @Environment(\.hubWindowWidth) private var windowWidth
    @Environment(\.hubWindowHeight) private var windowHeight


    private var connected: Bool { studio.readyURL != nil && !studio.stopping && workspace.isReachable }

    /// The sidebar's status symbol, read from the same facts as `studio.status`: a Mac with no
    /// model library is not "connected and fine", and the line beside this symbol says so.
    private var footerStatusSymbol: String {
        // The sentence decides. "Choose a model library" is an instruction, so it cannot wear a
        // success tick even when services are connected (page critics, 2026-11-12).
        if studio.configuration.modelsDirectory == nil || studio.status.hasPrefix("Choose") {
            return "exclamationmark.circle"
        }
        if localServicesReady, workspace.workPolicy?.machineEnabled == false {
            return "pause.circle.fill"
        }
        return connected ? "checkmark.circle.fill" : "circle"
    }
    /// Enough to attempt an authenticated Nodes read. Model-catalog reachability remains the gate
    /// for generation controls; it is not evidence that the fleet roster itself is unavailable.
    private var localServicesReady: Bool { studio.readyURL != nil && !studio.stopping }
    private var selectedAudioModel: NativeModel? { workspace.models.first { $0.repo == workspace.audioModel && $0.function == .audio } }
    private var selectedTranscriptionModel: NativeModel? { workspace.models.first { $0.repo == workspace.transcriptionModel && $0.function == .transcription } }
    private var selectedImageModel: NativeModel? { workspace.models.first { $0.repo == workspace.imageModel && $0.function == .image } }
    private var selectedMusicModel: NativeModel? { workspace.models.first { $0.repo == workspace.musicModel && $0.function == .music } }
    private var selectedVideoModel: NativeModel? { workspace.models.first { $0.repo == workspace.videoModel && $0.function == .video } }
    private var previewImageJob: NativeJob? { workspace.imageJobs.first { $0.state == "done" && (selectedImageID == nil || $0.id == selectedImageID) } }
    private var previewMusicJob: NativeJob? { workspace.musicJobs.first { $0.state == "done" && (selectedMusicID == nil || $0.id == selectedMusicID) } }
    private var activeImages: [NativeJob] { workspace.imageJobs.filter { active($0.state) } }
    private var activeMusic: [NativeJob] { workspace.musicJobs.filter { active($0.state) } }
    private var activeAudio: [NativeJob] { workspace.audioJobs.filter { active($0.state) } }
    private var activeVideo: [NativeJob] { workspace.videoJobs.filter { active($0.state) } }
    /// A subtitle batch this app is transcribing shows as `transcriptionActivity`, so it is not
    /// listed twice.
    private var activeSubtitles: [NativeJob] {
        workspace.transcribing ? [] : workspace.subtitleJobs.filter { active($0.state) }
    }

    /// The All / This Mac / Nodes filter, drawn from one place so Overview's In progress and the
    /// Jobs page cannot drift apart. `counts` puts each tab's own row count on its label, so a tab
    /// never offers work its list cannot show.
    private func machineFilterPicker(_ selection: Binding<JobMachineFilter>,
                                     counts: [JobMachineFilter: Int]? = nil) -> some View {
        HubSegmentedControl(
            selection: selection,
            items: JobMachineFilter.allCases.map { filter in
                let count = counts?[filter]
                return HubSegmentItem(
                    tag: filter,
                    label: count.map { "\(filter.label) \($0)" } ?? filter.label
                )
            },
            accessibilityLabel: "Show jobs made on",
            accessibilityHint: "Separate this Mac's own work from work its nodes made"
        )
    }

    /// How many running rows each tab of the Overview filter would show: every Studio's active
    /// work, counted through the same predicate the list filters with.
    private var overviewActiveCounts: [JobMachineFilter: Int] {
        let active = activeAudio + activeImages + activeMusic + activeVideo + activeSubtitles
        return Dictionary(uniqueKeysWithValues: JobMachineFilter.allCases.map {
            ($0, active.filter($0.includes).count)
        })
    }
    /// Running downloads, and a failed one so the reason stays where the operator started it.
    private var activeDownloads: [NativeDownload] {
        workspace.downloads.filter { active($0.state) || $0.error != nil }
    }
    private var filteredModels: [NativeModel] {
        workspace.models.filter { model in
            (modelFilter == "All functions" || model.function.rawValue == modelFilter) &&
            (!installedOnly || model.cached) &&
            (modelSearch.isEmpty || model.label.localizedCaseInsensitiveContains(modelSearch) || model.repo.localizedCaseInsensitiveContains(modelSearch))
        }
    }
    private func active(_ state: String) -> Bool { ["queued", "running", "cancel_requested", "cancelling", "paused", "recovery_pending"].contains(state) }
    private func stateLabel(_ state: String) -> String {
        switch state { case "done": return "Completed"; case "cancel_requested", "cancelling": return "Stopping"; case "error": return "Failed"; default: return state.replacingOccurrences(of: "_", with: " ").capitalized }
    }
    private func modelName(_ repo: String) -> String { workspace.models.first { $0.repo == repo }?.label ?? repo.components(separatedBy: "/").last ?? repo }

    // MARK: - Working state at a glance

    /// The status a job state is read as: one vocabulary with the rest of the app (`HubStatus`), so a
    /// finished job, a working Mac and a ready model are the same green, and a refusal is always red.
    private func stateStatus(_ state: String) -> HubStatus {
        switch state {
        case "done", "running": return .ok
        case "error": return .bad
        case "uncertain": return .warn
        default: return .off
        }
    }
    private func stateColor(_ state: String) -> Color { stateStatus(state).color }
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

    /// The state as a badge, so "Queued" and "Running" cannot be mistaken for each other: the word
    /// is always there, colour is never the only signal.
    private func stateBadge(_ state: String) -> some View {
        StatusBadge(text: stateLabel(state), status: stateStatus(state), symbol: stateSymbol(state))
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
            HStack(spacing: HubSpace.s) {
                ProgressView().progressViewStyle(.linear).frame(width: 140)
                // The Hub's own reason when it states one ("model not downloaded on X"), in orange
                // because it is the thing the operator can act on; the generic wait otherwise.
                Text(NativeJob.waitingLabel(note: job.governorNote, tries: job.tries, retryAt: job.retryAt))
                    .font(HubType.caption)
                    .foregroundStyle(job.governorNote?.isEmpty == false ? HubStatus.warn.textColor : Color.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case "running":
            if job.progress > 0.001 {
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    ProgressView(value: min(1, max(0, job.progress)), total: 1)
                        .tint(stateColor(job.state))
                    Text(Self.runningLabel(job))
                        .font(HubType.caption).foregroundStyle(.secondary)
                    stallNote(job)
                }
            } else {
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    HStack(spacing: HubSpace.s) {
                        ProgressView().progressViewStyle(.linear).frame(width: 140)
                        Text(job.progressStage.map { "Running — \($0)" }
                             ?? "Running — progress is not reported by this model")
                            .font(HubType.caption).foregroundStyle(.secondary)
                    }
                    stallNote(job)
                }
            }
        case "uncertain":
            Label("Needs attention — an earlier attempt may still be running",
                  systemImage: "questionmark.circle")
                .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                .fixedSize(horizontal: false, vertical: true)
        case "cancel_requested", "cancelling":
            HStack(spacing: HubSpace.s) {
                ProgressView().progressViewStyle(.linear).frame(width: 140)
                Text("Stopping…").font(HubType.caption).foregroundStyle(.secondary)
            }
        case "error":
            // The badge already says Failed; the row's own error line says why. A second "Failed"
            // under it was the same fact twice (critic, 2026-11-13).
            EmptyView()
        case "cancelled":
            Text("Cancelled").font(HubType.caption).foregroundStyle(.secondary)
        default:
            EmptyView()
        }
    }

    /// The Hub's own "nothing has moved" signal, shown and deliberately not acted on.
    ///
    /// `stalled_seconds` is the batch's progress clock — how long it has been silent while still
    /// active — and the Hub has published it on every job row since 0.6.70. The app read neither it
    /// nor the fleet signal, so a job that had gone quiet looked exactly like one that was working:
    /// a scheduler could see the stall and the operator could not. `stall.py` is the detector and
    /// says in its own docstring why this is **not** a timeout.
    ///
    /// **Advisory only, and worded so.** A merely-quiet job is one a long deadline exists to
    /// protect, so this says what is happening and promises nothing about the job ending — the same
    /// rule the Hub's own comment carries: visible, never destructive.
    @ViewBuilder private func stallNote(_ job: NativeJob) -> some View {
        if let stalled = job.stalledSeconds, stalled > 0 {
            Label("No progress for \(NativeJob.clock(stalled)) — still held, not cancelled",
                  systemImage: "exclamationmark.triangle")
                .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// A spinner and a sentence beside a Generate button while its studio is working.
    @ViewBuilder private func activeWorkIndicator(_ jobs: [NativeJob]) -> some View {
        if !jobs.isEmpty {
            HStack(spacing: HubSpace.s) {
                ProgressView().controlSize(.small)
                Text(jobs.count == 1 ? "Working…" : "Working on \(jobs.count) jobs…")
                    .font(HubType.caption).foregroundStyle(.secondary)
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
        HStack(spacing: HubSpace.s) {
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
        .font(HubType.secondary)
        .controlSize(.small)
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
            case .video: await workspace.discardVideo(pending.job)
            }
        }
    }

    /// The version this build actually is, shown in the corner so it is never a
    /// guess. A raw development executable has no bundle version; "Development" is
    /// more honest there than inventing a number.
    private var appVersion: String {
        // The offscreen render harness is a plain binary, not a bundle, so it passes the version
        // it is rendering; without this every footer screenshot showed "Development" and no number
        // (footer critic, 2026-11-13). Shipped builds always read their own Info.plist.
        let product = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
            .flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
            ?? ProcessInfo.processInfo.environment["STUDIOHUB_VERSION"].flatMap {
                $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0
            }
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
            // The list takes the height that is left and scrolls; the footer is its own row beneath it,
            // never a layer over it, so a short window cannot draw the footer on top of the last items.
            VStack(spacing: 0) {
            // A drawn sidebar rather than an AppKit List: the List's selection fill is drawn by
            // the window, and in an inactive window it came out black with a black label and icon.
            // Drawing the fill and the label together makes the current page readable in every
            // window state, in both appearances (critic, 2026-11-13).
            ScrollView { sidebarList }
            // A drawn list still moves with the arrow keys.
            .focusable()
            .onMoveCommand { direction in
                let pages: [StudioPage] = [.overview] + StudioPage.sidebarSections.flatMap(\.pages)
                guard let index = pages.firstIndex(of: studio.selection) else { return }
                switch direction {
                case .up: if index > 0 { studio.select(pages[index - 1]) }
                case .down: if index < pages.count - 1 { studio.select(pages[index + 1]) }
                default: break
                }
            }
            Divider()
            VStack(alignment: .leading, spacing: HubSpace.tight) {
                    // The fleet footer is three things: support, the version/What's New pill with
                    // its update control, and one status line. The role, machine name and group
                    // live in that line's text and tooltip instead of three stacked blocks.
                    // The services' actual failure, beside the status it explains, kept whole on
                    // click (owner, 2026-10-28: "can we have it like in a notification icon
                    // somewhere instead?"). It replaces the old full-width top banner.
                    StartupFailureIcon(detail: studio.failure) { studio.failure = nil }
                    // A Studio that has stopped answering, beside the status it belongs to - not
                    // across the top of every page (owner, 2026-10-28). It names the Studio and
                    // clears itself the moment the Studio answers.
                    StudioTroubleNote(trouble: workspace.studioTrouble)
                    // MERGE POINT (feat/remote-startup): one self-contained line. Keep it, and the
                    // `StartupSidebarNote` struct at the end of this file, when merging the footer.
                    StartupSidebarNote(studio: studio)
                    // Owner footer standard (2026-11-12), in order: Support row, one-line version,
                    // the What's New pill, the icon-only Launcher control, then the single status line,
                    // with equal insets and even rhythm. Optional and non-blocking throughout.
                    Button {
                        if let url = URL(string: "https://nowpayments.io/donation/studiokh") {
                            NSWorkspace.shared.open(url)
                        }
                    } label: {
                        Label("Support this app", systemImage: "heart")
                            .font(HubType.caption)
                    }
                    .buttonStyle(.link)
                    .help("Open the donation page in your browser.")
                    .accessibilityLabel("Support this app")
                    HStack(alignment: .center, spacing: HubSpace.s) {
                        footerVersionPill
                        // Icon-only by the owner's standard; the words live in the hover text.
                        Button {
                            NativeWorkspaceStore.openStudioLauncher()
                        } label: {
                            Image(systemName: "arrow.triangle.2.circlepath")
                                .font(.system(size: 12, weight: .medium))
                        }
                        .buttonStyle(.borderless)
                        .help("Updates are installed by Studio Launcher KH.")
                        .accessibilityLabel("Updates are installed by Studio Launcher KH.")
                        Spacer(minLength: 0)
                    }
                    // The single status line, last in the column (owner footer standard, 2026-11-12).
                    footerStatusLine
            }
            .padding(HubSpace.l)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.bar)
            }
            // Not a fixed width: a fixed column cannot give anything back, so when the window is
            // squeezed (Split View, most obviously) the layout overflowed and macOS clipped it -
            // the sidebar and the page's first column appeared to be cropped away.
            .frame(minWidth: 180, idealWidth: HubSpace.sidebar, maxWidth: HubSpace.sidebar)
            // An opaque sidebar surface, above the content in stacking order: a page's scroll
            // content can never paint across it or its footer.
            .background(HubSurface.sidebar)
            .zIndex(1)
            Divider()
            VStack(spacing: 0) {
                // The local-services failure is the notification icon in the sidebar footer, with
                // its whole message on click (owner, 2026-10-28). What is left here is a note about
                // a read or a file action, which is a different thing and still belongs on top.
                if let error = workspace.error ?? workspace.startupNote ?? fileActionError {
                    HubBanner(status: .warn, symbol: "exclamationmark.triangle.fill", message: error) {
                        Button("Dismiss") { workspace.error = nil; workspace.clearStartupNote(); fileActionError = nil }
                            .hubRowControl()
                    }
                    .padding(.horizontal, HubSpace.page).padding(.top, HubSpace.m)
                }
                switch studio.selection {
                case .overview: overview
                case .audio: audioWorkspace
                case .image: imageWorkspace
                case .music: musicWorkspace
                case .video: videoWorkspace
                case .chat: chatWorkspace
                case .transcription: transcriptionWorkspace
                case .setup: setupWorkspace
                case .models: modelsWorkspace
                case .voices: voicesWorkspace
                case .jobs: jobsWorkspace
                case .renders: rendersWorkspace
                case .connections: connections
                case .whatsNew: whatsNew
                case .settings: settings
                }
                // No `default:` and no unavailable-workspace fallback. Every `StudioPage` case is
                // handled above, so the compiler now *proves* the property the old fallback only
                // reported at runtime: a page with an engine and no workspace cannot ship. Adding
                // a Studio to the enum stops this build until its page is wired.
            }
            .frame(minWidth: 0, maxWidth: .infinity, maxHeight: .infinity)
            .background(HubSurface.page)
            // The version used to sit in the page's lower-right corner. It now lives with the rest
            // of the status, under the sidebar, so everything an operator needs to identify this
            // Mac - mode, what it offers, whether it is up, which build - is in one place.
            .clipped()
            .overlay { imageLightbox }
        }
        .onAppear { applyAppearance() }
        .onChange(of: appearancePreference) { _, _ in applyAppearance() }
        .onChange(of: studio.selection) { _, _ in imageModalID = nil }
        // **"Controller · fleet not read" was true only until the Fleet page was opened.** The
        // sidebar read the roster, and the roster was read by that page's own task, so a
        // controller whose Nodes page nobody visited said "not read" for hours while the Gateway
        // was already reading the same Macs (1400, 18:49). Read it once per connection instead;
        // the read is local, and a failure leaves the last good list and its own reason.
        .task(id: "fleet-line/\(workspace.connectionGeneration)/\(workspace.controllerIdentity?.settings.role ?? "")") {
            guard localServicesReady,
                  workspace.controllerIdentity?.settings.role == "controller",
                  workspace.fleetNodes == nil else { return }
            await workspace.refreshFleetNodes()
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
        .confirmationDialog(
            "Stop the local services?",
            isPresented: $studio.stopPrompt,
            titleVisibility: .visible) {
                Button("Stop Engine", role: .destructive) { studio.stopNow() }
                Button("Keep Running", role: .cancel) { studio.stopPrompt = false }
            } message: {
                Text("Active generation, transcription, or model downloads may be interrupted. Leave the engine running to finish them.")
            }
        .confirmationDialog(
            "The local services are still stopping",
            isPresented: $studio.forceStopPrompt,
            titleVisibility: .visible) {
                Button("Force Stop", role: .destructive) { studio.forceStopNow() }
                Button("Keep Waiting", role: .cancel) { studio.forceStopPrompt = false }
            } message: {
                Text("You can keep waiting or force it to stop. Force Stop may leave an incomplete output or download; it will not restart the job.")
            }
    }

    /// System, Light or Dark applies to the whole app, whether or not the window is open.
    private func applyAppearance() {
        switch appearancePreference {
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        case "dark": NSApp.appearance = NSAppearance(named: .darkAqua)
        default: NSApp.appearance = nil
        }
    }

    /// The one status line: symbol, words, and the role/machine/group tooltip.
    @ViewBuilder private var footerStatusLine: some View {
        let role = roleTitle(workspace.controllerIdentity?.settings.role)
        let paused = localServicesReady && workspace.workPolicy?.machineEnabled == false
        let line: String = {
            if testMachine { return localServicesReady ? "Test machine · running" : "Test machine · off" }
            if localServicesReady { return paused ? "Hub running · work paused" : "Hub running" }
            return studio.status
        }()
        HStack(alignment: .top, spacing: HubSpace.tight) {
            Image(systemName: footerStatusSymbol)
                .foregroundStyle(footerStatusSymbol == "checkmark.circle.fill" ? HubStatus.ok.textColor
                                 : (footerStatusSymbol == "pause.circle.fill" ? HubStatus.warn.textColor : Color.secondary))
            Text(line).font(HubType.caption).fixedSize(horizontal: false, vertical: true)
                .help("\(role) · \(thisMachineName) · \(groupLine)")
            Spacer(minLength: 0)
        }
    }

    /// The version + What's New pill, extracted so the footer body stays inside the type checker.
    @ViewBuilder private var footerVersionPill: some View {
        Button { studio.select(.whatsNew) } label: {
            HStack(spacing: HubSpace.tight) {
                Text("What's New")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .lineLimit(1)
                Text(appVersion)
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .lineLimit(1).fixedSize()
            }
            .lineLimit(1)
            .padding(.horizontal, HubSpace.s).padding(.vertical, 3)
            .background(Capsule().fill(Color.secondary.opacity(0.12)))
            .fixedSize()
        }
        .buttonStyle(.plain)
        .help("Hub Studio KH \(appVersion). Open What's New.")
        .accessibilityLabel("Hub Studio KH version \(appVersion). Open What's New.")
    }

    /// The sidebar's rows and section headings, separate from the window body so the type checker
    /// does not have to solve the whole page in one expression.
    @ViewBuilder private var sidebarList: some View {
        VStack(alignment: .leading, spacing: 2) {
            sidebarRow(.overview)
            ForEach(StudioPage.sidebarSections.filter { !$0.pages.isEmpty }, id: \.title) { section in
                Text(section.title)
                    .font(HubType.captionStrong).foregroundStyle(.secondary)
                    .padding(.horizontal, HubSpace.s)
                    .padding(.top, HubSpace.m).padding(.bottom, HubSpace.xs)
                ForEach(section.pages) { page in
                    sidebarRow(page, quiet: section.title == "Coming later")
                }
            }
        }
        .padding(.horizontal, HubSpace.s).padding(.vertical, HubSpace.s)
    }

    /// One sidebar row: the glyph and the name, with the selected one filled in the accent colour
    /// and written in white so the current page is always readable.
    private func sidebarRow(_ page: StudioPage, quiet: Bool = false) -> some View {
        let selected = studio.selection == page
        return Button { studio.select(page) } label: {
            HStack(spacing: HubSpace.s) {
                Image(systemName: page.symbol).frame(width: 18)
                Text(page.title).lineLimit(1)
                Spacer(minLength: 0)
            }
            .font(HubType.body)
            .foregroundStyle(selected ? Color.white : (quiet ? Color.secondary : Color.primary))
            .padding(.horizontal, HubSpace.s).padding(.vertical, 5)
            .background(RoundedRectangle(cornerRadius: HubRadius.control)
                .fill(selected ? Color.accentColor : Color.clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityLabel(page.title)
    }

    /// Every page's title is the sidebar row's own name (`StudioPage.title`), so the two cannot drift.
    private func pageTitle(_ page: StudioPage) -> String { page.title }
    private func sectionTitle(_ title: String) -> some View { Text(title).font(HubType.section).frame(maxWidth: .infinity, alignment: .leading) }
    private func empty(_ title: String, _ detail: String, symbol: String) -> some View {
        EmptyState(title: title, detail: detail, symbol: symbol)
    }
    /// A warning the Hub's own worker published (memory below the model's tier, a gate that is shut):
    /// advice, never a refusal, in the readable warning colour with the triangle beside it.
    private func advisory(_ text: String) -> some View {
        HStack(alignment: .top, spacing: HubSpace.s) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(HubStatus.warn.color)
            Text(text).font(HubType.secondary).fixedSize(horizontal: false, vertical: true)
        }
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
        HubCard {
        VStack(alignment: .leading, spacing: HubSpace.m) {
            Text(studio.hasLibrary ? "Start your local services" : "Choose where your models live").font(HubType.section)
            Text(studio.hasLibrary ? "Your model library is selected. Start the services to create assets and manage models." : setupDetail)
                .font(HubType.body).foregroundStyle(.secondary)
            if let warning = studio.libraryPrivacyWarning {
                Text(warning).font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
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
                        Text(prepared.path).font(HubType.caption).foregroundStyle(.secondary)
                        if let warning = ShellConfiguration.protectedFolderWarning(for: prepared) {
                            Text(warning).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    else { Button("Use Default Folder") { studio.useDefaultLibrary() }.buttonStyle(.borderedProminent).disabled(!studio.canConfigure) }
                    Button("Choose Model Folder…") { studio.chooseLibrary() }.disabled(!studio.canConfigure)
                }
                Spacer()
                Button("Settings") { studio.select(.settings) }
            }
        }
        }
    }

    /// The one line a Studio page shows under its title: its engine, the model it will use, and
    /// whether work is moving. The same words as the sidebar's own state, so the two never differ.
    /// "1 needs attention" / "3 need attention": one phrase, both call sites.
    private func attentionPhrase(_ count: Int) -> String {
        "\(count) \(count == 1 ? "needs" : "need") attention"
    }

    private func studioStatus(_ function: StudioFunction, active: Int,
                              selected: String? = nil) -> PageStatusLine {
        guard localServicesReady else {
            return PageStatusLine("Local services are stopped — start them to create here.", tone: .warn)
        }
        // The model the page will actually run, not whichever ready model happens to be first:
        // "Ready · Kokoro" beside a Qwen3 picker was the critic's clearest contradiction.
        let chosen = selected.flatMap { repo in workspace.models.first { $0.repo == repo } }
        let ready = chosen ?? workspace.models.first {
            $0.function == function && $0.cached && $0.supported && $0.runtimeReady && !$0.paused
        }
        if let ready, ready.cached, ready.supported, ready.runtimeReady, !ready.paused {
            if function == .audio, !ready.reportsVoiceModes {
                return PageStatusLine("\(ready.label) is installed, but its engine has not reported its voice modes — update it or choose another model.",
                                      tone: .warn)
            }
            let work = active == 0 ? "no jobs running"
                : "\(active) \(active == 1 ? "job" : "jobs") running"
            // A failed, uncertain or stalled job is the worst state on the page, even when the
            // model is ready and other work is moving.
            let recent = workspace.jobHistories.first { $0.function == function }?.jobs ?? []
            let attention = recent.filter {
                $0.state == "error" || $0.state == "uncertain" || ($0.stalledSeconds ?? 0) > 0
            }.count
            if attention > 0 {
                return PageStatusLine("Ready · \(ready.label) · \(work) · \(attentionPhrase(attention))",
                                      tone: .warn)
            }
            return PageStatusLine("Ready · \(ready.label) · \(work)", tone: active > 0 ? .ok : .off)
        }
        if workspace.models.contains(where: { $0.function == function && $0.cached }) {
            return PageStatusLine("The installed model needs attention — open Models to check it.", tone: .warn)
        }
        let page = StudioPage.page(for: function)?.title ?? function.rawValue
        return PageStatusLine("No \(page) model is installed — set up this Mac to add one.", tone: .warn)
    }

    /// The actions every Studio page keeps out of its content: go to the library, go to the work,
    /// or change what this Mac offers. One overflow instead of a toolbar of four.
    private var studioPageMenu: [PageMenuAction] {
        [
            PageMenuAction("Models", symbol: "externaldrive") { studio.select(.models) },
            PageMenuAction("All Jobs", symbol: "list.bullet.rectangle") { studio.select(.jobs) },
            PageMenuAction("Connections", symbol: "network") { studio.select(.connections) },
            PageMenuAction("Settings", symbol: "gearshape") { studio.select(.settings) },
        ]
    }

    /// What this page says under its title: the Hub's state, the library, and live work, in the
    /// order an operator asks about them.
    private var overviewStatus: PageStatusLine {
        if testMachine {
            return PageStatusLine("Test machine · \(localServicesReady ? "services running" : "services off")",
                                  tone: localServicesReady ? .ok : .off)
        }
        if !localServicesReady {
            return PageStatusLine("Local services are stopped — start them to create anything on this Mac.", tone: .warn)
        }
        if studio.configuration.modelsDirectory == nil {
            return PageStatusLine("No model library is selected yet — set up this Mac to install models.", tone: .warn)
        }
        let installed = workspace.models.filter(\.cached).count
        // The same count the All filter shows, so the line and the tab cannot disagree.
        let running = overviewActiveCounts[.all] ?? 0
        var parts = ["Hub running", "\(installed) models installed"]
        parts.append(running == 0 ? "no jobs active" : "\(running) jobs active")
        if !activeDownloads.isEmpty {
            parts.append("\(activeDownloads.count) downloading")
        }
        // A job that has gone quiet is not a green page, even while others run.
        let stalledJobs = (activeAudio + activeImages + activeMusic + activeVideo)
            .filter { ($0.stalledSeconds ?? 0) > 0 }
        if !stalledJobs.isEmpty {
            parts.append("\(stalledJobs.count) stalled")
        }
        return PageStatusLine(parts.joined(separator: " · "),
                              tone: !stalledJobs.isEmpty ? .warn
                                : (running > 0 || !activeDownloads.isEmpty ? .ok : .off))
    }

    private var overview: some View {
        PageScaffold(title: pageTitle(.overview),
                     status: overviewStatus,
                     scrolls: true,
                     primary: localServicesReady
                        ? PagePrimaryAction((overviewActiveCounts[.all] ?? 0) > 0 ? "Open Jobs" : "Open Audio") {
                            studio.select((overviewActiveCounts[.all] ?? 0) > 0 ? .jobs : .audio)
                        }
                        : PagePrimaryAction("Start Services", isEnabled: studio.canConfigure) { studio.start() },
                     menu: [
                        PageMenuAction("All Jobs", symbol: "list.bullet.rectangle") { studio.select(.jobs) },
                        PageMenuAction("Set Up This Mac", symbol: "wand.and.stars") { studio.select(.setup) },
                        PageMenuAction("Models", symbol: "externaldrive") { studio.select(.models) },
                        PageMenuAction("Settings", symbol: "gearshape") { studio.select(.settings) },
                     ]) {
            if !connected { setup }
            // One honest next step in the column, not only in the sidebar footer: the landing page
            // should say what to do first when something is missing (page critic, 2026-11-12).
            if studio.configuration.modelsDirectory == nil, !testMachine {
                HStack(spacing: HubSpace.s) {
                    Label("Choose a model library before you create anything.", systemImage: "exclamationmark.circle")
                        .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                    Button("Set up this Mac") { studio.select(.setup) }
                    Spacer()
                }
            } else if !testMachine, !workspace.models.contains(where: { $0.cached }) {
                HStack(spacing: HubSpace.s) {
                    Label("No models are installed yet. Install one to start creating.",
                          systemImage: "exclamationmark.circle")
                        .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                    Button("Set up this Mac") { studio.select(.setup) }
                    Spacer()
                }
            }
            VStack(alignment: .leading, spacing: HubSpace.m) {
                HStack(alignment: .firstTextBaseline) {
                    sectionTitle("In progress")
                    Spacer(minLength: 0)
                }
                // The same All / This Mac / Nodes filter as the Jobs page (owner, on a controller:
                // *"there's no way to see which job is its own and which is the fleet's"*), with
                // each tab's own count. Only where more than one Mac's work can land.
                if workspace.controllerIdentity?.settings.role == "controller" {
                    machineFilterPicker($overviewMachineFilter, counts: overviewActiveCounts)
                }
                // A download is this Mac's own work, not a job: it has its own strip below the
                // card, so the All filter counts exactly the job rows the card draws.
                let downloadsHere = overviewMachineFilter == .nodes ? [] : activeDownloads
                HubCard {
                    let transcribingHere = overviewMachineFilter == .nodes ? false : workspace.transcribing
                    let audio = activeAudio.filter(overviewMachineFilter.includes)
                    let images = activeImages.filter(overviewMachineFilter.includes)
                    let music = activeMusic.filter(overviewMachineFilter.includes)
                    let video = activeVideo.filter(overviewMachineFilter.includes)
                    let subtitles = activeSubtitles.filter(overviewMachineFilter.includes)
                    if audio.isEmpty && images.isEmpty && music.isEmpty && video.isEmpty
                        && subtitles.isEmpty && !transcribingHere {
                        HStack(spacing: HubSpace.m) {
                            Image(systemName: "checkmark.circle").font(HubType.icon).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: HubSpace.xs) {
                                Text(overviewMachineFilter == .all ? "No active jobs"
                                     : "No active jobs on \(overviewMachineFilter == .thisMac ? "this Mac" : "the nodes")")
                                    .font(HubType.rowTitle)
                                Text("Open a workspace to start creating. Completed work stays in Jobs.")
                                    .font(HubType.secondary).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Open Audio") { studio.select(.audio) }.hubRowControl()
                        }
                    } else {
                        // One divider between rows, none after the last: the card draws its own edge.
                        let rows: [AnyView] = audio.map { job in AnyView(jobRow(job, compact: true)) }
                            + images.map { AnyView(imageJobRow($0)) }
                            + music.map { AnyView(musicJobRow($0)) }
                            + video.map { AnyView(videoJobRow($0)) }
                            + subtitles.map { AnyView(subtitleJobRow($0)) }
                            + (transcribingHere ? [AnyView(transcriptionActivity)] : [])
                        VStack(alignment: .leading, spacing: HubSpace.m) {
                            ForEach(rows.indices, id: \.self) { index in
                                if index > 0 { Divider() }
                                rows[index]
                            }
                        }
                    }
                }
                if !downloadsHere.isEmpty {
                    VStack(alignment: .leading, spacing: HubSpace.m) {
                        sectionTitle("Downloads")
                        HubCard {
                            VStack(alignment: .leading, spacing: HubSpace.m) {
                                ForEach(downloadsHere) { downloadRow($0) }
                            }
                        }
                    }
                }
            }
            VStack(alignment: .leading, spacing: HubSpace.m) {
                sectionTitle("Workspaces")
                // The sidebar already lists every Studio. This card earns its place only when one
                // needs a step; when they are all ready it is one line, not a second navigation.
                let needing = StudioPage.create.filter { workspaceStatus($0) != "Ready to create" }
                HubCard(padding: 0) {
                    if needing.isEmpty {
                        HStack(spacing: HubSpace.m) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(HubStatus.ok.color)
                            Text("All \(StudioPage.create.count) studios are ready — pick one in the sidebar.")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                            Spacer()
                        }
                        .padding(HubSpace.l)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(needing) { page in
                                let status = workspaceBadge(page)
                                HStack(spacing: HubSpace.m) {
                                    Image(systemName: page.symbol).font(HubType.icon).frame(width: 24)
                                    VStack(alignment: .leading, spacing: HubSpace.xs) {
                                        Text(page.title).font(HubType.rowTitle)
                                        Text(workspaceDescription(page)).font(HubType.secondary).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    StatusBadge(text: status.text, status: status.status)
                                    Button("Open") { studio.select(page) }.hubRowControl()
                                        .accessibilityLabel("Open \(page.rawValue)")
                                }
                                .padding(.horizontal, HubSpace.l).padding(.vertical, HubSpace.m)
                                if page != needing.last { Divider() }
                            }
                        }
                    }
                }
            }
            VStack(alignment: .leading, spacing: HubSpace.m) {
                sectionTitle("Model library")
                HubCard {
                    HStack {
                        Text(testMachine ? "Test machine — no model library is required."
                             : (connected ? "\(workspace.models.filter(\.cached).count) installed models in the connected library" : "Connect your library to inspect installed models"))
                            .font(HubType.body).foregroundStyle(.secondary)
                        Spacer()
                        Button("Manage Models") { studio.select(.models) }.hubRowControl()
                    }
                }
            }
            measuredSection
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
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(alignment: .firstTextBaseline) {
                sectionTitle("Measured on this Mac")
                Spacer()
                HubSegmentedControl(
                    selection: Binding(
                        get: { statisticsWindow },
                        set: { value in
                            statisticsWindow = value
                            Task { await workspace.refreshStatistics(hours: value == 0 ? nil : value) }
                        }),
                    items: [
                        HubSegmentItem(tag: 0, label: "All time"),
                        HubSegmentItem(tag: 168, label: "Last 7 days"),
                        HubSegmentItem(tag: 24, label: "Last 24 hours")
                    ],
                    accessibilityLabel: "Window"
                )
            }
            HubCard {
            VStack(alignment: .leading, spacing: HubSpace.m) {
            if let stats = workspace.statistics {
                if stats.total == 0 {
                    HStack(spacing: HubSpace.m) {
                        Text("No completed generations recorded in this window yet. Run one from any workspace and it appears here.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                        Button("Open Audio") { studio.select(.audio) }.hubRowControl()
                        Spacer()
                    }
                } else {
                    Text("\(stats.total) completed \(stats.total == 1 ? "generation" : "generations") recorded by this Hub.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                    ForEach(stats.trackedModels.prefix(8)) { row in
                        HStack(spacing: HubSpace.m) {
                            Image(systemName: functionSymbol(row.modality)).foregroundStyle(.secondary).frame(width: 22)
                            VStack(alignment: .leading, spacing: HubSpace.xs) {
                                Text(workspace.models.first { $0.repo == row.model }?.label ?? row.model)
                                    .font(HubType.rowTitle)
                                Text("\(row.modality?.capitalized ?? "Unknown") · \(row.count) \(row.count == 1 ? "run" : "runs")")
                                    .font(HubType.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(seconds(row.avgS)).font(HubType.mono)
                        }
                    }
                    if stats.trackedModels.count > 8 {
                        Text("Showing the 8 slowest of \(stats.trackedModels.count) measured models.")
                            .font(HubType.caption).foregroundStyle(.secondary)
                    }
                    ForEach(machineBreakdown(stats), id: \.0) { machine, group in
                        HStack {
                            Text(machine == "local" ? "This Mac" : workspace.fleetMachineName(machine)).font(HubType.secondary)
                            Spacer()
                            Text("\(group.count) jobs · \(seconds(group.avgS)) average")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                Text(connected ? "Generation statistics are unavailable in this build." : "Start local services to see measured generation times.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            }
            }
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
        // A measured run says its exact seconds, like every other duration in the app.
        return NativeJob.clock(value)
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
    /// `workspaceStatus` as a badge: what is ready is green, what needs a step is amber, and what is
    /// simply off or absent is grey.
    private func workspaceBadge(_ page: StudioPage) -> (text: String, status: HubStatus) {
        let text = workspaceStatus(page)
        switch text {
        case "Ready to create": return (text, .ok)
        case "Needs a model": return (text, .warn)
        default: return (text, .off)
        }
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

    private var canSendChat: Bool {
        connected && !workspace.submittingChat
            && !workspace.chatPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && selectedChatModel?.supported == true
            && selectedChatModel?.cached == true
            && selectedChatModel?.runtimeReady == true
    }

    private var chatWorkspace: some View {
        PageScaffold(title: pageTitle(.chat),
                     status: studioStatus(.chat, active: workspace.chatJobs.filter { active($0.state) }.count,
                                   selected: workspace.chatModel),
                     menu: studioPageMenu) {
            if !connected { setup }
            StudioSplit(sideTitle: "Conversation", sideSymbol: "bubble.left.and.bubble.right") {
                // The form, on the left, in the same shape as the Image page: the box, the action
                // that acts on it, then a divider and the model it will run on. Send used to sit
                // *below* the model picker, so the one control this page is built around was
                // pushed into the middle of the column with the picker between it and the message
                // it sends.
                ScrollView {
                    VStack(alignment: .leading, spacing: HubSpace.l) {
                        HStack {
                            Text("Message").font(HubType.section)
                            Spacer()
                            Button("Clear") { workspace.clearChat() }
                                .disabled(workspace.chatTurns.isEmpty || workspace.submittingChat)
                        }
                        // An empty composer says what it is for (page critic, 2026-11-12): a blank
                        // box under an empty conversation left the one control unlabelled.
                        TextEditor(text: $workspace.chatPrompt)
                            .font(HubType.body).padding(HubSpace.s).frame(height: 155)
                            .overlay(RoundedRectangle(cornerRadius: HubRadius.control).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            // Overlaid, not stacked behind: a TextEditor draws its own opaque
                            // background, so the placeholder behind it was never visible (page
                            // critic, 2026-11-12).
                            .overlay(alignment: .topLeading) {
                                if workspace.chatPrompt.isEmpty {
                                    Text("Write a message…").font(HubType.body)
                                        .foregroundStyle(.secondary)
                                        .padding(.top, HubSpace.s + 8).padding(.leading, HubSpace.s + 5)
                                        .allowsHitTesting(false)
                                }
                            }
                            .accessibilityLabel("Chat message")
                        if workspace.submittingChat {
                            VStack(alignment: .leading, spacing: HubSpace.xs) {
                                if let activity = workspace.chatActivity {
                                    if let progress = activity.progress {
                                        ProgressView(value: progress, total: 1)
                                            .accessibilityLabel(activity.statusText)
                                    } else {
                                        ProgressView().progressViewStyle(.linear)
                                            .accessibilityLabel(activity.statusText)
                                    }
                                    Text(activity.statusText)
                                        .font(HubType.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                } else {
                                    ProgressView().progressViewStyle(.linear)
                                        .accessibilityLabel("Waiting for Chat activity")
                                    Text("Waiting").font(HubType.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        HStack {
                            Spacer()
                            // A disabled prominent button keeps its saturated fill in dark
                            // appearance, so an unsendable Send looked like the primary action in
                            // dark and a dead one in light. When it cannot send it is a plain
                            // button in both themes (page critic, 2026-11-12).
                            if canSendChat {
                                Button("Send") { Task { await workspace.sendChat() } }
                                    .buttonStyle(.borderedProminent)
                            } else {
                                Button("Send") {}.buttonStyle(.bordered).disabled(true)
                            }
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
                                .font(HubType.secondary).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let reason = selectedChatModel?.blockReason {
                            Label(reason, systemImage: "exclamationmark.triangle")
                                .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let warning = selectedChatModel?.memoryWarning {
                            advisory(warning)
                        }
                        // The row every other workspace page ends with: where models are installed
                        // and stored is not this page's business, and the page that owns it is one
                        // click away. Chat was the only form without it, which is the difference
                        // the owner saw when he put the five pages side by side.
                        Divider().padding(.top, 8)
                        Text("Model installation and storage are managed centrally.").font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Manage Chat Models") { modelFilter = "Chat"; studio.select(.models) }
                    }.padding(.trailing, HubSpace.m).padding(.bottom, HubSpace.l)
                // At a wide window the composer column takes a share of the width instead of staying
                // 390 pt beside 1200 pt of empty conversation (page critic, 2026-11-12).
                }.frame(minWidth: 350,
                        idealWidth: windowWidth >= 1500 ? 620 : 390,
                        maxWidth: windowWidth >= 1500 ? 760 : 440)
            } side: {
                // What you said and what the model answered, on the right, in the house shape the
                // Image preview and the Audio and Subtitle histories use: a fixed heading row, then
                // the content under it. The heading used to be the first line *inside* the
                // ScrollView, so it scrolled away with the conversation and never lined up with the
                // other pages' heading rows.
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    HStack {
                        Text("Conversation").font(HubType.section)
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
                            LazyVStack(alignment: .leading, spacing: HubSpace.m) {
                                ForEach(workspace.chatTurns) { turn in
                                    VStack(alignment: .leading, spacing: HubSpace.xs) {
                                        Text(turn.role == "user" ? "You" : "Model")
                                            .font(HubType.caption).foregroundStyle(.secondary)
                                        Text(turn.content).textSelection(.enabled)
                                            .fixedSize(horizontal: false, vertical: true)
                                        // A message that got no reply says so, under the message
                                        // itself. The alternative the page used to offer was
                                        // silence: the turn was not even in the transcript.
                                        if let failure = turn.failure {
                                            Label(failure, systemImage: "exclamationmark.triangle")
                                                .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                                .textSelection(.enabled)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }
                                        // Measured final-attempt facts come from the worker. Legacy
                                        // times remain labelled; no rate is calculated in the app.
                                        if !turn.chatMetrics.isEmpty {
                                            Text(turn.chatMetrics).font(HubType.caption).foregroundStyle(.secondary)
                                        }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(HubSpace.m)
                                    .background(Color(nsColor: .controlBackgroundColor))
                                    .clipShape(RoundedRectangle(cornerRadius: HubRadius.control))
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    }
                }
                .padding(.leading, HubSpace.m)
                // The same house frame as the other three workspace pages, so a result pane cannot
                // drift apart one fix at a time.
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        // The page margin comes from `PageScaffold`, the same 28 pt every page has.
        // Nothing on the Chat page is seeded from a saved default yet — this page has no
        // temperature or top-p control — but the Studio's published surface is read here so the
        // Models tab editor for this model is ready when it is opened.
        .task(id: modelDefaultsLoadKey) { await workspace.loadModelDefaultsIfNeeded(for: selectedChatModel) }
    }

    private var imageWorkspace: some View {
        PageScaffold(title: pageTitle(.image),
                     status: studioStatus(.image, active: activeImages.count, selected: workspace.imageModel),
                     menu: studioPageMenu) {
            if !connected { setup }
            StudioSplit(sideTitle: "Preview", sideSymbol: "photo") {
                ScrollView {
                    VStack(alignment: .leading, spacing: HubSpace.l) {
                        imageModeControl
                        HStack {
                            Text("Prompt").font(HubType.section)
                            Spacer()
                            sampleControl(Self.imageSamples, binding: $workspace.imagePrompt)
                        }
                        TextEditor(text: $workspace.imagePrompt)
                            .font(HubType.body).padding(HubSpace.s).frame(height: 155)
                            .overlay(RoundedRectangle(cornerRadius: HubRadius.control).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Image prompt")
                        HStack {
                            activeWorkIndicator(activeImages)
                            pinChip
                            Spacer()
                            ActiveAura(active: !activeImages.isEmpty || workspace.submittingImage) {
                                Button("Generate Image") { selectedImageID = nil; Task { await workspace.generateImage() } }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(!connected || workspace.submittingImage || workspace.imagePrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selectedImageModel?.supported != true || selectedImageModel?.runtimeReady != true || selectedImageModel?.cached != true || workspace.selectedImageMode?.available == false)
                            }
                        }
                        Divider()
                        modelPicker(.image)
                        if let warning = selectedImageModel?.memoryWarning {
                            // Advice, not a refusal: this Mac is below the tier FLUX is
                            // qualified at, and the operator decides whether to run it here.
                            advisory(warning)
                        }
                        imageLoadingModeControl
                        if let configuration = workspace.imageConfiguration, workspace.imageSettings != nil {
                            imageControls(configuration)
                            Button("Restore Generation Defaults") { workspace.restoreImageDefaults() }
                        }
                        Button("Manage Image Models") { modelFilter = "Image"; studio.select(.models) }
                    }.padding(.trailing, HubSpace.m).padding(.bottom, HubSpace.l)
                }.frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
            } side: {
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    HStack {
                        Text("Preview").font(HubType.section)
                        Spacer()
                        if let job = previewImageJob {
                            Button("Save Image…") { saveImage(job) }.disabled(!connected || loadingImage).rowAction()
                            revealButton(job)
                        }
                    }
                    if let error = imagePreviewError {
                        HubBanner(status: .warn, symbol: "exclamationmark.triangle.fill", message: error) {
                            Button("Retry Preview") { imagePreviewRetry += 1 }.disabled(!connected).hubRowControl()
                        }
                    }
                    // A card, not a pane: the picture is a fixed height so the prompt, its settings
                    // and the history below it keep the room. Click it for the large view.
                    ActiveAura(active: !activeImages.isEmpty, inset: HubSpace.s) {
                        Group {
                            if loadingImage { ProgressView("Loading image…").frame(maxWidth: .infinity, maxHeight: .infinity) }
                            else if let imagePreview, let job = previewImageJob {
                                Button { imageModalID = job.id } label: {
                                    Image(nsImage: imagePreview).resizable().scaledToFit()
                                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                                        .overlay(alignment: .bottomTrailing) {
                                            Image(systemName: "arrow.up.left.and.arrow.down.right")
                                                .font(HubType.captionStrong).padding(6)
                                                .background(.regularMaterial, in: Circle()).padding(8)
                                        }
                                        .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .help("View large")
                                .accessibilityLabel("View \(job.text.isEmpty ? "generated image" : job.text) large")
                            } else { empty("Your image will appear here", "Enter a prompt and generate an image. Completed images stay in Jobs.", symbol: "photo") }
                        }
                        .frame(height: previewImageJob == nil ? 150 : 230)
                        .frame(maxWidth: .infinity)
                        .hubCardSurface()
                    }
                    if let job = previewImageJob {
                        VStack(alignment: .leading, spacing: HubSpace.xs) {
                            Text(job.text).font(HubType.secondary).lineLimit(4).textSelection(.enabled)
                            Text(imageMeta(job)).font(HubType.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                    Divider()
                    HStack { Text("Recent images").font(HubType.section); Spacer(); Button("All Jobs") { studio.select(.jobs) }.hubRowControl() }
                    ScrollView { RecentRows(items: workspace.imageJobs) { imageJobRow($0) } }
                        .frame(minHeight: 80, maxHeight: .infinity)
                }
                .padding(.leading, HubSpace.m)
                // The same house frame as Audio and Subtitle, so the three workspace pages cannot
                // drift apart one fix at a time.
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .onAppear { seedSample(Self.imageSamples, binding: $workspace.imagePrompt, seeded: $seededImageSample) }
        // The worker's own per-machine loading setting: read when the page opens rather than on
        // the two-second poll, because it is one extra read and nothing else on the page needs it.
        // Keyed on the connection, not on the page: a reconnect is a different Image worker, and
        // the status from the one that ended is not its answer.
        .task(id: "\(connected)/\(workspace.connectionGeneration)") {
            await workspace.refreshImageLoadingMode()
            // ...and the operator's saved defaults for the selected model, so the steps, size,
            // guidance and seed below open on them rather than on the model's own.
            await workspace.loadModelDefaultsIfNeeded(for: selectedImageModel)
        }
        .task(id: "\(imageReceiptKey(previewImageJob))/\(imagePreviewRetry)") {
            let receipt = imageReceiptKey(previewImageJob)
            imagePreview = nil
            imagePreviewReceipt = nil
            imagePreviewError = nil
            loadingImage = false
            guard connected, let job = previewImageJob else { return }
            loadingImage = true
            defer { if imageReceiptKey(previewImageJob) == receipt { loadingImage = false } }
            do {
                let data = try await workspace.imageData(for: job)
                guard !Task.isCancelled else { return }
                guard let image = NSImage(data: data) else { throw CocoaError(.fileReadCorruptFile) }
                imagePreview = image
                imagePreviewReceipt = receipt
                imagePreviewError = nil
            } catch { if !Task.isCancelled { imagePreviewError = error.localizedDescription } }
        }
    }

    @ViewBuilder private var imageModeControl: some View {
        if !workspace.imageModes.isEmpty {
            VStack(alignment: .leading, spacing: HubSpace.s) {
                Text("Image mode").font(HubType.secondary)
                HubSegmentedControl(selection: $workspace.imageMode,
                    items: workspace.imageModes.map { HubSegmentItem(tag: $0.id, label: $0.label) },
                    accessibilityLabel: "Image mode")
            }
            if let mode = workspace.selectedImageMode, !mode.available {
                advisory(mode.reason ?? "\(mode.label) is unavailable on this Mac.")
            }
        } else {
            Text("Image modes have not been published by this Hub.")
                .font(HubType.caption).foregroundStyle(.secondary)
        }
        if let mode = workspace.imageModes.first(where: { $0.id == "edit" }),
           let bounds = mode.references {
            Text(bounds.max > 0 ? "Reference images: up to \(bounds.max)" : "Reference images: unavailable")
                .font(HubType.caption).foregroundStyle(.secondary)
            if bounds.evidenceNote != nil || bounds.limitsTable != nil {
                DisclosureGroup("Image limit details") {
                    if let table = bounds.limitsTable {
                        imageLimitTable(table)
                    }
                    if let note = bounds.evidenceNote {
                        Text(note).font(HubType.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        if workspace.imageMode == "edit" {
            if let bounds = workspace.selectedImageMode?.references {
                VStack(alignment: .leading, spacing: HubSpace.s) {
                    if bounds.max > 0 || !workspace.imageReferences.isEmpty {
                        Text("Reference images").font(HubType.section)
                    }
                    if bounds.max > 0 {
                        Text("Choose \(bounds.min)–\(bounds.max) images · up to \(bounds.maxDimension) pixels per side")
                            .font(HubType.caption).foregroundStyle(.secondary)
                    }
                    ForEach(0..<max(bounds.max, workspace.imageReferences.count), id: \.self) { index in
                        HStack {
                            Button {
                                let panel = NSOpenPanel()
                                panel.title = "Choose a reference image"
                                panel.allowedContentTypes = [.png, .jpeg, .webP]
                                panel.allowsMultipleSelection = false
                                if panel.runModal() == .OK, let url = panel.url {
                                    workspace.addImageReference(url, replacing: index < workspace.imageReferences.count ? index : nil)
                                }
                            } label: {
                                Text(index < workspace.imageReferenceNames.count
                                     ? workspace.imageReferenceNames[index] : "Choose reference \(index + 1)…")
                                    .lineLimit(1).truncationMode(.middle)
                            }.accessibilityIdentifier("image-reference-\(index)")
                                .disabled(workspace.selectedImageMode?.available != true || index >= bounds.max)
                            if index < workspace.imageReferences.count {
                                Button("Remove") {
                                    workspace.imageReferences.remove(at: index)
                                    workspace.imageReferenceNames.remove(at: index)
                                }.rowAction()
                            }
                        }
                    }
                }
            } else {
                advisory("This model does not offer Reference to image. Choose another model or Text to image.")
            }
        } else if !workspace.imageReferences.isEmpty {
            Button("Remove reference images") {
                workspace.imageReferences.removeAll()
                workspace.imageReferenceNames.removeAll()
            }
            Text("Reference images are kept until you remove them or choose Reference to image.")
                .font(HubType.caption).foregroundStyle(.secondary)
        }
    }

    private func imageLimitTable(_ table: NativeImageLimitTable) -> some View {
        let rows = table.rows.filter { $0.modelId == workspace.imageModel }
        let tiers = Set(rows.flatMap { $0.tiers.keys }).sorted {
            (Int($0) ?? 0) < (Int($1) ?? 0)
        }
        return ScrollView([.horizontal, .vertical]) {
            VStack(alignment: .leading, spacing: HubSpace.s) {
                HStack(alignment: .top, spacing: HubSpace.m) {
                    Text("Job").frame(width: 140, alignment: .leading)
                    ForEach(tiers, id: \.self) { tier in
                        Text("\(tier) GB RAM").frame(width: 150, alignment: .leading)
                    }
                }.font(HubType.secondary)
                ForEach(rows) { row in
                    HStack(alignment: .top, spacing: HubSpace.m) {
                        Text(row.label).frame(width: 140, alignment: .leading)
                        ForEach(tiers, id: \.self) { tier in
                            VStack(alignment: .leading, spacing: HubSpace.xs) {
                                if let cell = row.tiers[tier] {
                                    Text(cell.allowed ? "On" : "Off")
                                    if row.mode == "edit" {
                                        Text("Up to \(cell.maxReferenceImages) references")
                                    }
                                    Text("Swap allowance: \(cell.swapAllowanceGb.formatted()) GB")
                                    Text(cell.source).foregroundStyle(.secondary)
                                    if let receipt = cell.receipt {
                                        Text(receipt).foregroundStyle(.secondary)
                                    }
                                } else {
                                    Text("Not published")
                                }
                            }.frame(width: 150, alignment: .leading)
                        }
                    }
                }
                Text("Published defaults · \(table.revision)").foregroundStyle(.secondary)
            }.font(HubType.caption).padding(.vertical, HubSpace.xs)
        }.frame(maxHeight: 280)
    }

    private func imageReceiptKey(_ job: NativeJob?) -> String {
        "\(connected)/\(workspace.connectionGeneration)/\(job?.id ?? "")/\(job?.outputURL ?? "")/\(job?.bytes ?? 0)/\(job?.sha256 ?? "")/\(job?.mediaType ?? "")"
    }

    /// Which mflux memory configuration the next Image job loads with, on this Mac.
    ///
    /// Everything shown here was published: the options and their sentences are the selected
    /// model's own catalogue row — they carry the peak memory and wall time that model states for
    /// each configuration, and the host it states them for — and the line underneath is the
    /// configuration this Mac will actually use, so `auto` resolving to the bounded configuration
    /// is visible rather than implied. A model whose row says it has one loading mode renders its
    /// own `reason` instead, and a row that published no loading modes at all says that: neither
    /// gets a control that would do nothing. There is deliberately no list of modes here and no
    /// copy of the worker's threshold rule.
    @ViewBuilder private var imageLoadingModeControl: some View {
        if let control = workspace.imageLoadingControl {
            if control.offersControl {
                LabeledRow(label: "Loading mode") {
                  VStack(alignment: .leading, spacing: HubSpace.tight) {
                    HubSegmentedControl(
                        selection: Binding(
                            get: { control.selected },
                            set: { value in Task { await workspace.setImageLoadingMode(value) } }),
                        items: control.options.map { HubSegmentItem(tag: $0.id, label: $0.label) },
                        accessibilityLabel: "Loading mode"
                    )
                    .disabled(!connected || workspace.updatingImageLoadingMode)
                    if let detail = control.detail {
                        Text(detail).font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let effective = control.effectiveSentence {
                        Text(effective).font(HubType.caption).foregroundStyle(.secondary)
                    }
                  }
                }
            } else if let reason = control.reason {
                Text(reason).font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
        VStack(alignment: .leading, spacing: HubSpace.m) {
            LabeledRow(label: "Image size") {
                Picker("Image size", selection: Binding(
                    get: { configuration.sizes.first { $0.width == workspace.imageSettings?.width && $0.height == workspace.imageSettings?.height }?.id ?? "" },
                    set: { id in
                        guard let size = configuration.sizes.first(where: { $0.id == id }) else { return }
                        workspace.imageSettings?.width = size.width; workspace.imageSettings?.height = size.height
                    })) {
                        ForEach(configuration.sizes) { size in Text("\(size.label) · \(size.width) × \(size.height)").tag(size.id) }
                    }
                    .labelsHidden()
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
                        .font(HubType.caption).foregroundStyle(.secondary)
                }
            }
            if let field = configuration.seedField {
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    HStack {
                        Text("Seed")
                        Spacer()
                        TextField("Seed", value: Binding(get: { workspace.imageSettings?.seed ?? field },
                                                         set: { workspace.imageSettings?.seed = $0 }),
                                  format: .number.grouping(.never))
                            .textFieldStyle(.roundedBorder).frame(width: 145)
                    }
                    Text("Use −1 for a new random seed.").font(HubType.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func jobMetaParts(_ job: NativeJob) -> [String] {
        var parts = ["\(modelName(job.model))\(job.generationSuffix)"]
        if let made = job.generatedAtLabel { parts.append(made) }
        if job.state == "error", let tries = job.tries, tries > 1 { parts.append("after \(tries) tries") }
        return parts
    }

    /// Where a row's work runs, as the small tag every row carries. Owner, on a controller:
    /// *"there's no way to see which job is its own and which is the fleet's."* A job the Hub has
    /// not placed yet is said to be unplaced rather than assigned to a Mac it may never run on;
    /// a finished row whose Hub never recorded a Mac (filed before this build) says nothing.
    private func jobWhereTag(_ job: NativeJob) -> String? {
        JobFacts.whereTag(machine: job.machine, isActive: active(job.state),
                          name: workspace.fleetMachineName)
    }

    /// Who asked, when the Hub recorded it. Nil for a batch filed before 0.6.212, which is shown
    /// as nothing rather than guessed.
    private func jobAskedTag(_ job: NativeJob) -> String? { JobFacts.askedTag(origin: job.origin) }

    /// One small capsule. A tag is read as one fact, so it never wraps.
    private func jobTag(_ text: String) -> some View {
        Text(text)
            .font(HubType.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, HubSpace.s)
            .padding(.vertical, 1)
            .background(Color.secondary.opacity(0.12),
                        in: RoundedRectangle(cornerRadius: HubRadius.control, style: .continuous))
            .accessibilityLabel(text)
    }

    @ViewBuilder private func jobTags(_ job: NativeJob) -> some View {
        HStack(spacing: HubSpace.xs) {
            if let where_ = jobWhereTag(job) { jobTag(where_) }
            if let asked = jobAskedTag(job) { jobTag(asked) }
        }
    }

    /// The line under a row's prompt: what made it, whether it finished, and when.
    ///
    /// Six row builders each hand-wrote this `HStack`, so a fact added to one was missing from the
    /// other five. That is how the list ended up with no generation date at all: the model and the
    /// pace were written out per builder and the date was written out nowhere. One builder means
    /// the next fact lands on every row of every Studio at once.
    private func jobMetaLine(_ job: NativeJob) -> some View {
        // One line of text that wraps as a line, not four texts that each wrap inside their own
        // sliver: in the narrow column beside a Studio page "FLUX.2-klein-4B" broke across three
        // lines. The model's own published label carries its quantization, and the date is absent
        // when the Hub stamped no time — a different fact from "a long time ago". Which Mac ran
        // or holds it matters on a controller, where it is otherwise unclear which node keeps
        // failing a job.
        let parts = jobMetaParts(job)
        let line = parts.joined(separator: " · ")
        let spoken = job.generatedAtLabel.map { "Generated \($0). \(parts.joined(separator: ", "))" }
            ?? parts.joined(separator: ", ")
        // The badge beside the line when the line fits on one, and over it when it does not: a badge
        // with a date wrapped to its right broke "2 Oct 2026" across two lines in the narrow columns.
        // The where/who tags always share the badge's row: they are the answer to "whose work is
        // this", so a narrow column may shorten the line, never drop a tag.
        let badge = stateBadge(job.state)
        return ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: HubSpace.s) {
                badge
                jobTags(job)
                Text(line).font(HubType.caption).foregroundStyle(.secondary).lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                    .accessibilityLabel(spoken)
            }
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                HStack(spacing: HubSpace.s) {
                    badge
                    jobTags(job)
                }
                Text(line).font(HubType.caption).foregroundStyle(.secondary).lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(spoken)
            }
        }
    }

    /// What a job was asked for, under the model line. Renders nothing when the job states no
    /// controls — a job another client filed, or a modality with none — rather than an empty line.
    @ViewBuilder
    private func jobRequestFacts(_ job: NativeJob, _ function: StudioFunction) -> some View {
        let facts = job.requestFacts(for: function)
        if !facts.isEmpty {
            Text(facts).font(HubType.caption).foregroundStyle(.secondary)
                .accessibilityLabel("Requested settings: \(facts)")
        }
    }

    /// Point Finder at the file the job produced, or say why it cannot be pointed there.
    ///
    /// This button used to be *disabled* whenever the artifact was not a file on this Mac, with the
    /// reason tucked into a `.help` tooltip — and macOS does not show a tooltip for a disabled
    /// control. So the reason was visible nowhere, and the owner read the grey button as broken:
    /// *"even on this machine, i can't click show in finder too."* A control that refuses without
    /// saying why is the state this project keeps having to fix.
    ///
    /// It is always clickable now. Where the file is here, Finder selects it; where it is not — a
    /// job that ran on another Mac, or one whose file has since been cleaned up — the row says so,
    /// through the same banner every other file action reports through.
    @ViewBuilder
    private func revealButton(_ job: NativeJob) -> some View {
        Button("Show in Folder") {
            if !workspace.revealArtifact(job) { fileActionError = job.revealUnavailableReason }
        }
        .help(job.canRevealArtifact
              ? "Reveal \(job.artifactPath ?? "") in Finder"
              : job.revealUnavailableReason)
        .rowAction()
    }

    /// Puts a failed job back in the queue (`POST .../items/{i}/retry`); prompt and settings are kept.
    @ViewBuilder private func retryButton(_ job: NativeJob) -> some View {
        Button("Retry") { Task { await workspace.retryJob(job) } }.disabled(!connected).rowAction()
    }

    /// "FLUX.2 klein 4B · 1536×1024 · 4 steps · guidance 1 · seed 48201": the facts that identify an
    /// image and let it be made again, in one line. Nothing is invented: a job another client filed
    /// states what it states.
    private func imageMeta(_ job: NativeJob) -> String {
        var parts = [modelName(job.model)]
        if let width = job.clipWidth ?? job.requestedWidth ?? job.imageSettings?.width,
           let height = job.clipHeight ?? job.requestedHeight ?? job.imageSettings?.height {
            parts.append("\(width)×\(height)")
        }
        let facts = job.requestFacts(for: .image)
        if !facts.isEmpty { parts.append(facts) }
        return parts.joined(separator: " · ")
    }

    private var imageModalJob: NativeJob? {
        guard let imageModalID else { return nil }
        return workspace.imageJobs.first { $0.id == imageModalID && $0.state == "done" }
    }

    /// One image, as large as the window allows, with what made it and what to do with it.
    ///
    /// Esc and the Close button close it, and so does a click outside the card. It is drawn over the
    /// page rather than as a sheet, so it fits the window the operator has instead of a sheet's fixed
    /// size, and it needs no second window.
    @ViewBuilder private var imageLightbox: some View {
        if let job = imageModalJob {
            ZStack {
                Color.black.opacity(0.6).onTapGesture { imageModalID = nil }
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    Group {
                        if let image = imageModalImage {
                            Image(nsImage: image).resizable().scaledToFit()
                                .accessibilityLabel(job.text.isEmpty ? "Generated image" : job.text)
                        } else if let error = imageModalError {
                            VStack(spacing: HubSpace.s) {
                                Text(error).font(HubType.secondary).textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                                Button("Retry Image") { imageModalRetry += 1 }.disabled(!connected)
                            }
                        } else {
                            ProgressView("Loading image…")
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color.black.opacity(0.25), in: RoundedRectangle(cornerRadius: HubRadius.card))
                    VStack(alignment: .leading, spacing: HubSpace.xs) {
                        Text(job.text.isEmpty ? modelName(job.model) : job.text)
                            .font(HubType.secondary).lineLimit(3).textSelection(.enabled)
                        Text(imageMeta(job)).font(HubType.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                    HStack(spacing: HubSpace.s) {
                        Button("Save…") { saveImage(job) }.disabled(!connected || imageModalImage == nil)
                        revealButton(job)
                        Spacer()
                        Button("Close") { imageModalID = nil }
                            .keyboardShortcut(.cancelAction)
                            .buttonStyle(.borderedProminent)
                    }
                }
                .padding(HubSpace.l)
                .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: HubRadius.sheet))
                .overlay(RoundedRectangle(cornerRadius: HubRadius.sheet).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                .shadow(radius: 24)
                .padding(HubSpace.page)
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Image")
            .task(id: "\(imageReceiptKey(job))/\(imageModalRetry)") {
                imageModalError = nil
                imageModalImage = imageReceiptKey(job) == imagePreviewReceipt ? imagePreview : nil
                guard connected else {
                    imageModalError = "Start local services to retrieve this image."
                    return
                }
                guard imageModalImage == nil else { return }
                do {
                    let data = try await workspace.imageData(for: job)
                    guard !Task.isCancelled else { return }
                    guard let image = NSImage(data: data) else { throw CocoaError(.fileReadCorruptFile) }
                    imageModalImage = image
                    imageModalError = nil
                } catch { if !Task.isCancelled { imageModalError = error.localizedDescription } }
            }
        }
    }

    private func imageJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            ActionRow {
                if job.state == "done" {
                    Button { imageModalID = job.id } label: {
                        JobThumbnail(job: job, connected: connected, store: thumbnails, workspace: workspace)
                    }
                    .buttonStyle(.plain)
                    .help("View large")
                }
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(2)
                    jobMetaLine(job)
                    jobProgress(job)
                    DiagnosticDetails(title: "Request", summary: "settings used") { jobRequestFacts(job, .image) }
                    if job.state == "done", !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(HubType.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Result details: \(job.assetFacts)")
                    }
                }
                RowActions {
                    if job.state == "done" {
                        Button("Preview") { selectedImageID = job.id; studio.select(.image) }.rowAction()
                        Button("Save…") { saveImage(job) }.disabled(!connected).rowAction()
                        revealButton(job)
                    } else if job.state == "uncertain" {
                        Button("Check Result") { Task { await workspace.reconcileImage(job) } }.disabled(!connected).rowAction()
                        Button("Discard…", role: .destructive) { requestDiscard(job, kind: .image) }.disabled(!connected).rowAction()
                    } else if job.state == "error" {
                        retryButton(job)
                    } else if active(job.state) {
                        Button("Cancel") { Task { await workspace.cancelImage(job) } }.disabled(!connected).rowAction()
                    }
                }
            }
            if let message = job.error, !message.isEmpty {
                Text(message).font(HubType.caption).foregroundStyle(HubStatus.bad.textColor).textSelection(.enabled)
                if message.localizedCaseInsensitiveContains("memory") {
                    Text("Close other apps on that Mac, or choose a smaller image model, then Retry.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// Music generation, laid out exactly like Image: prompt and settings on the left, the result
    /// and the studio's own history on the right.
    private var musicWorkspace: some View {
        PageScaffold(title: pageTitle(.music),
                     status: studioStatus(.music, active: activeMusic.count, selected: workspace.musicModel),
                     menu: studioPageMenu) {
            if !connected { setup }
            StudioSplit(sideTitle: "Preview", sideSymbol: "music.note") {
                ScrollView {
                    VStack(alignment: .leading, spacing: HubSpace.l) {
                        HStack {
                            Text("Prompt").font(HubType.section)
                            Spacer()
                            sampleControl(Self.musicSamples, binding: $workspace.musicPrompt)
                        }
                        TextEditor(text: $workspace.musicPrompt)
                            .font(HubType.body).padding(HubSpace.s).frame(height: 155)
                            .overlay(RoundedRectangle(cornerRadius: HubRadius.control).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Music prompt")
                        HStack {
                            Text("\(workspace.musicPrompt.count) characters").font(HubType.caption).foregroundStyle(.secondary)
                            Spacer()
                            pinChip
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
                            HStack(alignment: .firstTextBaseline, spacing: HubSpace.s) {
                                Label(reason, systemImage: "exclamationmark.circle")
                                    .font(HubType.caption).foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                if reason.localizedCaseInsensitiveContains("settings")
                                    || reason.localizedCaseInsensitiveContains("controls") {
                                    Button("Open Model Defaults") {
                                        modelFilter = "Music"
                                        studio.select(.models)
                                    }.hubRowControl()
                                }
                            }
                        }
                        Divider().padding(.top, 8)
                        Text("Generation").font(HubType.section)
                        modelPicker(.music)
                        if let warning = selectedMusicModel?.memoryWarning {
                            // Advice, not a refusal: the operator decides whether to run it here.
                            advisory(warning)
                        }
                        if let configuration = workspace.musicConfiguration, let values = workspace.musicSettings {
                            musicControls(configuration, values)
                            Button("Restore Music Defaults") { workspace.restoreMusicDefaults() }
                        } else if selectedMusicModel?.supported == true {
                            Label("This model does not publish generation controls this app can show.",
                                  systemImage: "exclamationmark.triangle")
                                .font(HubType.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let note = workspace.musicNote {
                            Label(note, systemImage: "exclamationmark.circle")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider().padding(.top, 8)
                        Text("Model installation and storage are managed centrally.").font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Manage Music Models") { modelFilter = "Music"; studio.select(.models) }
                    }.padding(.trailing, HubSpace.m).padding(.bottom, HubSpace.l)
                }.frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
            } side: {
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    Text("Preview").font(HubType.section)
                    ActiveAura(active: !activeMusic.isEmpty, inset: HubSpace.s) {
                        if let job = previewMusicJob {
                            musicPlayerCard(job)
                        } else {
                            empty("Your music will appear here", "Describe the music and generate it. Completed music stays in Jobs.", symbol: "music.note")
                                .frame(height: 150).frame(maxWidth: .infinity)
                                .hubCardSurface()
                        }
                    }
                    Divider()
                    HStack { Text("Recent music").font(HubType.section); Spacer(); Button("All Jobs") { studio.select(.jobs) }.hubRowControl() }
                    if workspace.musicJobs.isEmpty {
                        Text("Completed music will appear here.").foregroundStyle(.secondary).font(HubType.secondary)
                        Spacer(minLength: 0)
                    } else {
                        ScrollView { RecentRows(items: workspace.musicJobs) { musicJobRow($0) } }
                        .frame(minHeight: 80, maxHeight: .infinity)
                    }
                }
                .padding(.leading, HubSpace.m)
                // The same house frame as Audio, Subtitle and Image, so the four cannot drift apart.
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .onAppear { seedSample(Self.musicSamples, binding: $workspace.musicPrompt, seeded: $seededMusicSample) }
        // Duration, steps, guidance, shift and seed open on the operator's saved default for this
        // model when there is one. Read when the page opens, once per connection.
        .task(id: modelDefaultsLoadKey) { await workspace.loadModelDefaultsIfNeeded(for: selectedMusicModel) }
        .onChange(of: previewMusicJob?.id) { _, shown in
            // A different clip is showing, so the player must not pretend to control the old one. A
            // clip that was just started for the one now showing (the row's Play) is not the old one.
            guard loadedMusicJobID != shown else { return }
            player.stop()
            loadedMusicJobID = nil
        }
    }

    /// "ACE-Step 1.5 · 1:34 · 1.8 MB · seed 7700": which model, how long, how big, and the seed.
    private func musicMeta(_ job: NativeJob) -> String {
        var parts = [modelName(job.model)]
        if let length = clipLength(job) { parts.append(length) }
        if let bytes = job.bytes, bytes > 0 {
            parts.append(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file))
        }
        if let seed = job.musicSettings?.seed, seed >= 0 { parts.append("seed \(seed)") }
        return parts.joined(separator: " · ")
    }

    /// The previewed Music clip as one compact player: what it is, one play/pause control, a scrub
    /// bar with the elapsed and total time, and what to do with the file. Nothing starts on its own.
    ///
    /// This was a panel the height of the column — a note icon, the prompt, the length, a full-width
    /// Play and Stop, and the bar, centred in a field of nothing — which left the history a strip.
    /// It is the height of its content now, and the history takes the rest.
    @ViewBuilder private func musicPlayerCard(_ job: NativeJob) -> some View {
        let loaded = loadedMusicJobID == job.id && player.hasAudio
        let playing = loaded && player.playing
        let name = job.text.isEmpty ? "generated music" : job.text
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(alignment: .center, spacing: HubSpace.m) {
                Button {
                    if playing { player.pause() }
                    else if loaded { player.resume() }
                    else { startPreviewMusic(job) }
                } label: {
                    Image(systemName: playing ? "pause.fill" : "play.fill")
                        .font(HubType.section)
                        .foregroundStyle(.white)
                        .frame(width: 44, height: 44)
                        .background(Circle().fill(Color.accentColor))
                        .opacity(connected ? 1 : 0.4)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .disabled(!connected)
                .accessibilityLabel(playing ? "Pause \(name)" : "Play \(name)")
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text)
                        .font(HubType.rowTitle).lineLimit(2).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text(musicMeta(job)).font(HubType.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.tail)
                }
            }
            HStack(spacing: HubSpace.m) {
                Text(loaded ? PreviewPlayer.clock(player.position) : "0:00")
                    .accessibilityLabel("Elapsed time")
                Slider(value: Binding(get: { loaded ? player.position : 0 },
                                      set: { player.seek(to: $0) }),
                       in: 0...max(loaded ? player.duration : 1, 0.001))
                    .disabled(!loaded)
                    .accessibilityLabel("Playback position")
                Text(loaded ? PreviewPlayer.clock(player.duration)
                     : PreviewPlayer.clock(job.durationSeconds ?? job.musicSettings?.durationSeconds ?? 0))
                    .accessibilityLabel("Total time")
            }
            .font(HubType.monoCaption).foregroundStyle(.secondary)
            HStack(spacing: HubSpace.s) {
                Button("Save…") { saveMusic(job) }.disabled(!connected).rowAction()
                revealButton(job)
                Spacer(minLength: 0)
            }
        }
        .padding(HubSpace.m)
        // A player is a card, not a banner: the card itself stops growing where a long line stops
        // being readable, left-aligned with the page, so its background never outruns its content.
        .frame(maxWidth: 760, alignment: .leading)
        .hubCardSurface()
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func startPreviewMusic(_ job: NativeJob) {
        // Claimed before the bytes arrive, so the player card and the history agree on which clip
        // this is the moment Play is pressed; given back if the clip cannot be played.
        player.stop()
        loadedMusicJobID = job.id
        Task {
            do { try await workspace.playMusic(job, with: player) }
            catch {
                if loadedMusicJobID == job.id { loadedMusicJobID = nil }
                fileActionError = error.localizedDescription
            }
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
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(spacing: HubSpace.tight) {
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
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    HStack {
                        Text("Seed")
                        Spacer()
                        TextField("Seed", value: Binding(get: { workspace.musicSettings?.seed ?? field },
                                                         set: { workspace.musicSettings?.seed = $0 }),
                                  format: .number.grouping(.never))
                            .textFieldStyle(.roundedBorder).frame(width: 145)
                    }
                    Text("Use −1 for a new random seed.").font(HubType.caption).foregroundStyle(.secondary)
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
                    // The same count the prompt box carries: a lyric sheet is what the engine
                    // sings, and how long it is decides how much of it survives.
                    LabeledRow(label: "Lyrics",
                               help: "\((workspace.musicSettings?.lyrics ?? configuration.defaults.lyrics ?? "").count) characters") {
                        TextField("One line per lyric", text: Binding(
                            get: { workspace.musicSettings?.lyrics ?? configuration.defaults.lyrics ?? "" },
                            set: { workspace.musicSettings?.lyrics = $0 }), axis: .vertical)
                            .lineLimit(3...6).textFieldStyle(.roundedBorder)
                    }
                    LabeledRow(label: "Vocal language") {
                        if let supportedOptions = selectedMusicModel?.laneParameter("vocal_language")?.options,
                           !supportedOptions.isEmpty {
                            let defaultLanguage = configuration.defaults.vocalLanguage ?? "en"
                            let current = workspace.musicSettings?.vocalLanguage ?? defaultLanguage
                            let effective = current.isEmpty ? defaultLanguage : current
                            let options = supportedOptions.contains(effective) ? supportedOptions : supportedOptions + [effective]
                            Picker("Vocal language", selection: Binding(
                                get: {
                                    let val = workspace.musicSettings?.vocalLanguage ?? defaultLanguage
                                    return val.isEmpty ? defaultLanguage : val
                                },
                                set: { workspace.musicSettings?.vocalLanguage = $0 })) {
                                ForEach(options, id: \.self) { code in
                                    Text(Locale.current.localizedString(forLanguageCode: code) ?? code).tag(code)
                                }
                            }
                            .labelsHidden()
                        } else {
                            TextField("Language code, e.g. en", text: Binding(
                                get: { workspace.musicSettings?.vocalLanguage ?? configuration.defaults.vocalLanguage ?? "" },
                                set: { workspace.musicSettings?.vocalLanguage = $0 }))
                                .textFieldStyle(.roundedBorder)
                        }
                    }
                }
            } else {
                Text("This model is instrumental only — it has no vocal controls.")
                    .font(HubType.caption).foregroundStyle(.secondary)
            }
            if let rate = selectedMusicModel?.sampleRateHz, let channels = selectedMusicModel?.channels {
                Text("Output: WAV · \(channels) channel\(channels == 1 ? "" : "s") · \(rate) Hz")
                    .font(HubType.caption).foregroundStyle(.secondary)
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
        VStack(alignment: .leading, spacing: HubSpace.s) {
            ActionRow {
                Image(systemName: active(job.state) ? "music.note" : job.state == "done"
                      ? "checkmark.circle" : "exclamationmark.circle")
                    .foregroundStyle(stateColor(job.state))
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(2)
                    jobMetaLine(job)
                    jobProgress(job)
                    DiagnosticDetails(title: "Request", summary: "settings used") { jobRequestFacts(job, .music) }
                    if job.state == "done", !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(HubType.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Result details: \(job.assetFacts)")
                    }
                    // Some Music receipts carry no measured `audio_duration_s`. Rather than show
                    // nothing, name the requested length - and say that is what it is.
                    if job.state == "done", job.durationSeconds == nil,
                       let requested = job.musicSettings?.durationSeconds, requested > 0 {
                        Text("Clip length \(NativeJob.clock(requested)) requested")
                            .font(HubType.caption).foregroundStyle(.secondary)
                    }
                }
                RowActions {
                    if job.state == "done" {
                        Button("Play") {
                            // Loads the clip into the player card and plays it; from Jobs it also
                            // opens Music Studio, where the card is, so the clip can be paused.
                            selectedMusicID = job.id
                            if studio.selection != .music { studio.select(.music) }
                            startPreviewMusic(job)
                        }
                            .disabled(!connected).rowAction()
                        Button("Save…") { saveMusic(job) }.disabled(!connected).rowAction()
                        revealButton(job)
                    } else if job.state == "uncertain" {
                        Button("Check Result") { Task { await workspace.reconcileMusic(job) } }.disabled(!connected).rowAction()
                        Button("Discard…", role: .destructive) { requestDiscard(job, kind: .music) }.disabled(!connected).rowAction()
                    } else if job.state == "error" {
                        retryButton(job)
                    } else if active(job.state) {
                        Button("Cancel") { Task { await workspace.cancelMusic(job) } }.disabled(!connected).rowAction()
                    }
                }
            }
            if let message = job.error, !message.isEmpty { Text(message).font(HubType.caption).foregroundStyle(HubStatus.bad.textColor).textSelection(.enabled) }
        }
    }

    /// Connected, and the Hub publishes no video model: the state of every Hub since LTX-2.3 was
    /// retired. Not connected is a different state (the setup banner covers it).
    private var noVideoModel: Bool { connected && workspace.supportedModels(for: .video).isEmpty }

    /// Video generation, laid out like Music: the form on the left, the lane's own history on
    /// the right. Every drop-down is built from the selected model's `lane_contract`, so the page
    /// cannot offer an option its worker would refuse — and a model that publishes no geometry
    /// control gets a prompt-only form rather than an invented ladder.
    private var videoWorkspace: some View {
        PageScaffold(title: pageTitle(.video),
                     status: noVideoModel
                        ? PageStatusLine("The local video model was retired; finished clips stay in Jobs and Renders.", tone: .off)
                        : studioStatus(.video, active: activeVideo.count, selected: workspace.videoModel),
                     menu: studioPageMenu) {
            if !connected { setup }
            StudioSplit(sideTitle: "Recent clips", sideSymbol: "film") {
                ScrollView {
                    VStack(alignment: .leading, spacing: HubSpace.l) {
                        // The local video model (LTX-2.3) was retired, so a Hub publishes no video
                        // model at all. That is a plain state, not a fault: say so once, above the
                        // form, rather than showing an empty picker and a button that cannot work.
                        if noVideoModel {
                            Label("No video model is available. The local video model was retired because its clips were not good enough. Clips you made earlier are still listed on the right.",
                                  systemImage: "film")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        VStack(alignment: .leading, spacing: HubSpace.s) {
                            Text("Prompt").font(HubType.section)
                            TextEditor(text: $workspace.videoPrompt)
                                .font(HubType.body).padding(HubSpace.s).frame(height: 155)
                                .overlay(RoundedRectangle(cornerRadius: HubRadius.control).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                                .accessibilityLabel("Video prompt")
                            Text("Describe the shot. The clip is generated locally; nothing leaves this Mac.")
                                .font(HubType.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        // The action that acts on the prompt sits right under it, in the same place as on
                        // the Image, Music and Audio pages, ahead of the settings it will run with.
                        HStack {
                            if workspace.submittingVideo { ProgressView().controlSize(.small) }
                            pinChip
                            Spacer()
                            Button("Generate Video") { Task { await workspace.generateVideo() } }
                                .hubPrimary()
                                .disabled(!connected || workspace.submittingVideo
                                          || workspace.videoPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                          || selectedVideoModel?.cached != true
                                          || selectedVideoModel?.supported != true
                                          || selectedVideoModel?.runtimeReady != true)
                        }
                        Divider()
                        modelPicker(.video)
                        // The worker's own below-tier sentence, in the same shape every other page
                        // renders. For Video the tier is also a routing verdict: below it the
                        // worker publishes `memory_eligible: false` and the model is not offered.
                        if let warning = selectedVideoModel?.memoryWarning {
                            advisory(warning)
                        }
                        if !videoOptions("aspect_ratio").isEmpty {
                            LabeledRow(label: "Aspect ratio") {
                                Picker("Aspect ratio", selection: $workspace.videoAspectRatio) {
                                    ForEach(videoOptions("aspect_ratio"), id: \.self) { Text($0).tag($0) }
                                }.labelsHidden()
                                .onChange(of: workspace.videoAspectRatio) { _, _ in
                                    snapVideoControlsToPublishedCanvases()
                                }
                            }
                        }
                        if !videoOptions("resolution").isEmpty {
                            LabeledRow(label: "Size") {
                                Picker("Size", selection: $workspace.videoResolution) {
                                    ForEach(videoOptions("resolution"), id: \.self) { Text($0).tag($0) }
                                }.labelsHidden()
                            }
                        }
                        if !videoDurationChoices.isEmpty {
                            LabeledRow(label: "Duration") {
                                Picker("Duration", selection: $workspace.videoDurationSeconds) {
                                    ForEach(videoDurationChoices, id: \.self) { Text("\($0) s").tag($0) }
                                }.labelsHidden()
                            }
                        }
                        // The lane's own switches, from its own contract. The lane's `note` is
                        // shown under each one, because it is the only honest statement of what
                        // the switch does — for `audio` it says the model makes picture and sound
                        // in one pass, so a silent clip is not cheaper or faster.
                        ForEach(selectedVideoModel?.publishedBooleanParameters ?? []) { parameter in
                            VStack(alignment: .leading, spacing: HubSpace.xs) {
                                Toggle(videoSwitchLabel(parameter.name), isOn: videoSwitchBinding(parameter))
                                    .toggleStyle(.switch)
                                    .frame(maxWidth: 260, alignment: .leading)
                                    // A switch with no published default has no position this page
                                    // can honestly show, so it is left to the lane rather than
                                    // drawn as off — which would be this app answering for the lane.
                                    .disabled(parameter.defaultValue == nil)
                                if parameter.defaultValue == nil {
                                    Text("This lane publishes no default for this switch, so it is "
                                         + "left to the model.")
                                        .font(HubType.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                        .frame(maxWidth: 420, alignment: .leading)
                                } else if let note = parameter.note, !note.isEmpty {
                                    Text(note).font(HubType.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                        .frame(maxWidth: 420, alignment: .leading)
                                }
                            }
                        }
                        if let note = workspace.videoNote {
                            Text(note).font(HubType.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if !noVideoModel {
                            Divider().padding(.top, 8)
                            Text("Image-to-video is driven from Story Studio, which owns the scene images: it uploads the exact scene and submits the animation here. This page is the lane's own text-to-video surface.")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Button("Manage Video Models") { modelFilter = "Video"; studio.select(.models) }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.trailing, HubSpace.m).padding(.bottom, HubSpace.l)
                }
                .frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
            } side: {
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    HStack {
                        Text("Recent clips").font(HubType.section)
                        Spacer()
                        Button("All Jobs") { studio.select(.jobs) }.hubRowControl()
                    }
                    if workspace.videoJobs.isEmpty {
                        empty("Your clips", "Finished video jobs will appear here, with their length and a way to save them.", symbol: "film")
                    } else {
                        ScrollView { RecentRows(items: workspace.videoJobs) { videoJobRow($0) } }
                    }
                    Spacer(minLength: 0)
                }
                .padding(.leading, HubSpace.m)
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        // The lane's published defaults, read from the selected model's contract when the page
        // opens or the model changes. The request itself omits a field the operator never chose,
        // so this only decides what the controls show.
        .task(id: workspace.videoModel) {
            snapVideoControlsToPublishedCanvases()
        }
        // The clip, playing where it was made. Preview used to mean Save… and open it elsewhere.
        .sheet(item: Binding(get: { previewVideoID.flatMap { id in workspace.videoJobs.first { $0.id == id } } },
                             set: { if $0 == nil { previewVideoID = nil } })) { job in
            VideoPreviewSheet(job: job, workspace: workspace) { previewVideoID = nil }
        }
    }

    /// The canvases this lane publishes, if it publishes them as pairs.
    private var videoCanvases: [NativeLaneCanvas] {
        selectedVideoModel?.laneContract?.canvases ?? []
    }

    /// The options one lane parameter publishes, in the lane's own order.
    ///
    /// **A ratio and a size class are only offered together where a canvas carries both.** The
    /// two parameters are independent enums, and a video lane's pairs are a strict subset of
    /// their cross product (the LTX lane's `7:4` is its `256p` canvas; its `448p` class is `11:7`
    /// or `1:1`), so a picker built from the enums alone offered this app's own worker requests it
    /// refuses. Where the lane states no canvases the enums are still the whole statement.
    private func videoOptions(_ name: String) -> [String] {
        let stated = videoCanvases
        if !stated.isEmpty {
            switch name {
            case "aspect_ratio":
                var seen: [String] = []
                for canvas in stated where !seen.contains(canvas.aspectRatio) {
                    seen.append(canvas.aspectRatio)
                }
                return seen
            case "resolution":
                let forRatio = stated.filter { $0.aspectRatio == workspace.videoAspectRatio }
                let pool = forRatio.isEmpty ? stated : forRatio
                var seen: [String] = []
                for canvas in pool where !seen.contains(canvas.resolution) {
                    seen.append(canvas.resolution)
                }
                return seen
            default:
                break
            }
        }
        return selectedVideoModel?.laneContract?.parameters.first { $0.name == name }?.options ?? []
    }

    /// The canvas the current ratio and class name, when the lane publishes canvases.
    private var selectedVideoCanvas: NativeLaneCanvas? {
        videoCanvases.first {
            $0.aspectRatio == workspace.videoAspectRatio && $0.resolution == workspace.videoResolution
        }
    }

    /// Put the controls on a canvas the lane publishes: its default where the lane has one, the
    /// first published canvas otherwise, and the selected canvas's own durations.
    ///
    /// A control left on a pair no canvas carries is the defect this exists to prevent — the
    /// worker refuses it, and the operator sees a refusal for a choice this page offered.
    private func snapVideoControlsToPublishedCanvases() {
        // A switch the newly selected model does not publish is dropped, not carried: a value that
        // survived a model change would be sent for a lane that never offered the choice, and the
        // operator would have no way to see why. Published names are kept with their values.
        workspace.snapVideoSwitchesToPublished()
        if let value = videoDefault("aspect_ratio"), workspace.videoAspectRatio.isEmpty {
            workspace.videoAspectRatio = value
        }
        if let value = videoDefault("resolution"), workspace.videoResolution.isEmpty {
            workspace.videoResolution = value
        }
        let canvases = videoCanvases
        if !canvases.isEmpty {
            if !videoOptions("aspect_ratio").contains(workspace.videoAspectRatio) {
                workspace.videoAspectRatio = canvases.first?.aspectRatio ?? ""
            }
            if !videoOptions("resolution").contains(workspace.videoResolution) {
                workspace.videoResolution = videoOptions("resolution").first ?? ""
            }
        }
        if workspace.videoDurationSeconds == 0, let seconds = videoDefaultSeconds() {
            workspace.videoDurationSeconds = seconds
        }
        let durations = videoDurationChoices
        if !durations.isEmpty && !durations.contains(workspace.videoDurationSeconds) {
            // The worker's own default first, so a class that cannot take it lands on a duration
            // the lane does state rather than on an arbitrary one.
            let preferred = videoDefaultSeconds()
            workspace.videoDurationSeconds = durations.contains(preferred ?? -1)
                ? (preferred ?? durations[0]) : durations[0]
        }
    }

    /// A switch's label, from the lane's own parameter name. The lane's `note` carries the meaning;
    /// this only turns `audio` into `Audio` so the control reads as a label rather than a field.
    private func videoSwitchLabel(_ name: String) -> String {
        name.prefix(1).uppercased() + name.dropFirst()
    }

    /// What a switch shows: the operator's own choice when they made one, otherwise the lane's
    /// published default. **A lane that publishes no default shows off and cannot be moved** — the
    /// page cannot display a state nobody stated, and sending one would decide for the lane.
    private func videoSwitchBinding(_ parameter: NativeLaneParameter) -> Binding<Bool> {
        Binding(
            get: {
                if let chosen = workspace.videoSwitches[parameter.name] { return chosen }
                if case .bool(let stated) = parameter.defaultValue { return stated }
                return false
            },
            set: { value in workspace.videoSwitches[parameter.name] = value })
    }

    private func videoDefault(_ name: String) -> String? {
        guard let value = selectedVideoModel?.laneContract?.parameters
            .first(where: { $0.name == name })?.defaultValue else { return nil }
        if case .string(let text) = value { return text }
        return nil
    }

    private func videoDefaultSeconds() -> Int? {
        guard let value = selectedVideoModel?.laneContract?.parameters
            .first(where: { $0.name == "duration_seconds" })?.defaultValue else { return nil }
        if case .integer(let seconds) = value { return seconds }
        return nil
    }

    private var videoDurationChoices: [Int] {
        // The chosen canvas's own durations where it publishes them: a big canvas at the longer
        // duration is the pair the worker's token ceiling refuses, so it is not offered here.
        if let canvas = selectedVideoCanvas, !canvas.durations.isEmpty {
            return canvas.durations.sorted()
        }
        return (selectedVideoModel?.laneContract?.parameters
            .first { $0.name == "duration_seconds" }?.options ?? []).compactMap(Int.init)
    }

    private func videoJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            ActionRow {
                // A still from the clip itself, the way an Image result shows a thumbnail. A video
                // row was a line of text with a film icon, so the only way to see what had been
                // generated was to save it and open it somewhere else.
                if job.state == "done" {
                    VideoThumbnail(job: job, connected: connected, store: videoThumbnails, workspace: workspace)
                } else {
                    Image(systemName: active(job.state) ? "film" : "exclamationmark.circle")
                        .foregroundStyle(stateColor(job.state))
                }
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(2)
                    jobMetaLine(job)
                    jobProgress(job)
                    DiagnosticDetails(title: "Request", summary: "settings used") { jobRequestFacts(job, .video) }
                    // The clip's own facts already carry its length, so the separate "Clip length"
                    // line is gone: it printed the same number twice in one row.
                    if job.state == "done", !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(HubType.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Result details: \(job.assetFacts)")
                    }
                }
                RowActions {
                    if job.state == "done" {
                        Button("Preview") { previewVideoID = job.id }.rowAction()
                        Button("Save…") { saveVideo(job) }.disabled(!connected).rowAction()
                        revealButton(job)
                    } else if job.state == "uncertain" {
                        Button("Check Result") { Task { await workspace.reconcileVideo(job) } }.disabled(!connected).rowAction()
                        Button("Discard…", role: .destructive) { requestDiscard(job, kind: .video) }.disabled(!connected).rowAction()
                    } else if job.state == "error" {
                        retryButton(job)
                    } else if active(job.state) {
                        Button("Cancel") { Task { await workspace.cancelVideo(job) } }.disabled(!connected).rowAction()
                    }
                }
            }
            if let message = job.error, !message.isEmpty {
                Text(message).font(HubType.caption).foregroundStyle(HubStatus.bad.textColor).textSelection(.enabled)
            }
        }
    }

    private func saveVideo(_ job: NativeJob) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "clip-\(job.id).mp4"
        panel.allowedContentTypes = [.mpeg4Movie]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task {
            do { try await workspace.saveVideo(job, to: url) }
            catch { fileActionError = error.localizedDescription }
        }
    }

    private var audioWorkspace: some View {
        PageScaffold(title: pageTitle(.audio),
                     status: studioStatus(.audio, active: activeAudio.count, selected: workspace.audioModel),
                     menu: studioPageMenu) {
            if !connected { setup }
            StudioSplit(sideTitle: "Recent audio", sideSymbol: "waveform") {
                // The form, on the left, in the same shape as the Image page: everything you set
                // up, packed from the top, scrolling if it is taller than the window. The Script
                // box used to be its own column with the controls opposite it, which is why every
                // control on this page was once clipped and why the page never matched the others.
                ScrollView {
                    VStack(alignment: .leading, spacing: HubSpace.l) {
                        HStack {
                            Text("Script").font(HubType.section)
                            Spacer()
                            sampleControl(Self.audioSamples, binding: $workspace.audioText)
                        }
                        TextEditor(text: $workspace.audioText)
                            .font(HubType.body).padding(HubSpace.s).frame(height: 155)
                            .background(Color(nsColor: .textBackgroundColor))
                            .overlay(RoundedRectangle(cornerRadius: HubRadius.control).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Audio script")
                        HStack {
                            Text("\(workspace.audioText.count) characters").font(HubType.caption).foregroundStyle(.secondary)
                            Spacer()
                            pinChip
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
                        if let reason = audioBlockedReason, reason != audioModeDetail {
                            Label(reason, systemImage: "exclamationmark.circle")
                                .font(HubType.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider().padding(.top, 8)
                        Text("Generation").font(HubType.section)
                        voiceModeTabs
                        modelPicker(.audio)
                        // The sentence the Voice worker publishes when this Mac is below the
                        // model's own RAM floor (SH-3), drawn exactly as the Image, Music,
                        // Subtitle and Chat pages draw theirs — same triangle, same orange, same
                        // position under the picker. It was the one Studio with no warning, so a
                        // model the worker had judged too large for this Mac was offered with
                        // nothing said. Advice, never a refusal: Generate stays enabled and the
                        // Hub's own governor owns the live decision.
                        if let warning = selectedAudioModel?.memoryWarning {
                            advisory(warning)
                        }
                        if workspace.audioMode == .cloning {
                            // Cloning is library-only: the voice comes from the Voices page and is
                            // chosen here. There is no loose-clip path on this page any more, so
                            // the picker and the buttons that lead to where a voice is added and
                            // managed are the whole control.
                            voiceSourcePicker
                            if let voice = workspace.selectedLibraryVoice, voice.readyForCloning {
                                // A saved voice carries its own clip, words and timings, so there is
                                // nothing left to choose or prepare.
                                VStack(alignment: .leading, spacing: HubSpace.tight) {
                                    Text("Ready to clone").font(HubType.rowTitle)
                                    Text("This voice's clip, its transcript and its timing are already stored with it. Type your script and press Generate Audio.")
                                        .font(HubType.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            } else if workspace.selectedLibraryVoice != nil {
                                // Chosen, but not usable: say so here rather than in the "Ready to
                                // clone" panel, which would be a claim about a voice that cannot be
                                // cloned from at all.
                                VStack(alignment: .leading, spacing: HubSpace.tight) {
                                    Text("Not ready to clone").font(HubType.rowTitle)
                                    Text("This saved voice has no transcript yet, so a clone cannot align its audio. Add the words on the Voices page, or choose another voice.")
                                        .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        } else if workspace.audioMode == .preset {
                            // The roster belongs to the model, not to the page: a preset family that
                            // publishes no list (Khmer VITS has a single built-in voice) must not be
                            // shown another family's voice names, and must not be told it has a list
                            // it has not reported.
                            let speakers = selectedAudioModel?.speakers ?? []
                            LabeledRow(label: "Voice") {
                              VStack(alignment: .leading, spacing: HubSpace.tight) {
                                if speakers.isEmpty {
                                    Text(selectedAudioModel?.publishesPresetRoster == true
                                         ? "This model has not reported its built-in voices yet. Press Generate Audio once the model is installed and the runtime is ready, or choose another model above."
                                         : "This model has one built-in voice. There is nothing to choose or prepare.")
                                        .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                                } else {
                                    Picker("Voice", selection: $workspace.audioSpeaker) {
                                        // The not-chosen state is a real row, and it is the default
                                        // one: it sends no `preset_speaker`, so the voice stored for
                                        // this model in the Models tab is the one the engine speaks
                                        // with. It names that voice where the producer states one,
                                        // and says only that the model decides where it does not —
                                        // the engine's own default is the engine's to state.
                                        Text(workspace.audioSpeakerInForce.map { "Model default — \($0)" }
                                             ?? "This model's own voice")
                                            .tag("")
                                        ForEach(speakers, id: \.self) { Text($0).tag($0) }
                                    }.labelsHidden()
                                    Text("These voices are built into the model. Nothing to choose or prepare. Leaving this on the model's default uses the voice set for this model in Models.")
                                        .font(HubType.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                              }
                            }
                        } else {
                            VStack(alignment: .leading, spacing: HubSpace.tight) {
                                HStack {
                                    Text("Voice description").font(HubType.rowTitle)
                                    Spacer()
                                    Menu("Examples") {
                                        ForEach(Self.designExamples, id: \.self) { example in
                                            Button(example) { workspace.audioDesignPrompt = example }
                                        }
                                    }.font(HubType.caption)
                                }
                                TextField("For example: a deep, slow and warm narrator",
                                          text: $workspace.audioDesignPrompt, axis: .vertical)
                                    .lineLimit(2...4).textFieldStyle(.roundedBorder)
                                Text("The model creates a voice from these words. Nothing is saved to your voice library, so the same description will not sound identical twice.")
                                    .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        // Offered only where this model's lane publishes a `language`. The lane
                        // omits it for a family that takes no language input — it discards the key
                        // — so a box here would be a control whose value goes nowhere.
                        if let languageControl = workspace.audioLanguageControl {
                            LabeledRow(label: languageControl.required == true ? "Language (required)" : "Language",
                                       help: languageControl.options.flatMap { $0.isEmpty ? nil : "This model accepts \($0.joined(separator: ", "))." }) {
                                TextField(languageControl.options?.first.map { "Language code, e.g. \($0)" }
                                          ?? "Language code, e.g. en",
                                          text: $workspace.audioLanguage).textFieldStyle(.roundedBorder)
                            }
                        }
                        speedControl
                        Divider().padding(.top, 8)
                        Text("Model installation and storage are managed centrally.").font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Manage Audio Models") { modelFilter = "Audio"; studio.select(.models) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.trailing, HubSpace.m).padding(.bottom, HubSpace.l)
                }
                .frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
            } side: {
                // What you made, on the right, exactly as the Image page shows its preview.
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    HStack {
                        Text("Recent audio").font(HubType.section)
                        Spacer()
                        if player.playing { Button("Stop Playback") { player.stop(); loadedAudioJobID = nil } }
                        Button("All Jobs") { studio.select(.jobs) }.hubRowControl()
                    }
                    // The loaded clip's own transport, in the shape the Music page already had:
                    // play/pause, a stop, a scrub bar, the elapsed time of the audio actually
                    // loaded, and the pace the clip was made at. The Audio page offered Play and
                    // nothing else, so a three-minute script could not be scrubbed, paused where it
                    // mattered, or compared against its own length.
                    // The transport is always on screen, loaded or not. It used to appear only once
                    // a clip had been played, which pushed the whole list down the moment Play was
                    // pressed and pulled it back on Stop — the row the operator was reading moved
                    // out from under the pointer, and on a long history the list jumped by the
                    // height of the transport. Reserving the space is the fix; with nothing loaded
                    // the controls are disabled and say so.
                    audioTransport(loadedAudioJob)
                    ActiveAura(active: !activeAudio.isEmpty || workspace.submittingAudio, inset: HubSpace.s) {
                        Group {
                            if workspace.audioJobs.isEmpty {
                                empty("Your audio will appear here", "Generated audio collects here, newest first.", symbol: "waveform")
                            } else {
                                ScrollView { RecentRows(items: workspace.audioJobs) { jobRow($0, compact: true) } }
                                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                            }
                        }
                    }
                }
                .padding(.leading, HubSpace.m)
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .onAppear { seedSample(Self.audioSamples, binding: $workspace.audioText, seeded: $seededAudioSample) }
        // The speaking pace opens on the operator's saved `speed` default for this model when its
        // lane publishes one, and on the engine's own pace otherwise.
        .task(id: modelDefaultsLoadKey) { await workspace.loadModelDefaultsIfNeeded(for: selectedAudioModel) }
    }

    /// Where the voice for a clone comes from: the library on the Voices page, and nowhere else.
    ///
    /// The app has had a voice library since the Voices page existed, and the engine has always
    /// accepted a saved voice by its id, but the Audio page once offered only a file chooser - so a
    /// voice the operator could see in **Voices**, ready to clone, could not be picked where they
    /// generate. The picker is that missing control; the two buttons beside it are the only other
    /// thing this control needs, which is the page where a voice is added or managed.
    private var voiceSourcePicker: some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            HStack {
                Text("Voice").font(HubType.rowTitle)
                Spacer()
                // Both lead to the one place a voice is added, edited or removed. With no
                // loose-clip path left, reaching that page is the only other thing this control
                // can need to do.
                Button("Add Voice…") { studio.select(.voices) }
                    .hubRowControl().fixedSize()
                    .help("Add a reference clip and its transcript on the Voices page")
                Button("Manage Voices") { studio.select(.voices) }
                    .hubRowControl().fixedSize()
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
            // Disabled only when there is nothing at all to choose. A disabled control with no
            // visible reason is the worst state a form can be in, so the line below always says
            // which state the library is in: reading, empty, unreadable, or short of transcripts.
            .disabled(workspace.voices.isEmpty)
            Text(workspace.voicePickerDetail).font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            // An empty library, honestly read, offers the one action that fills it. A library that
            // could not be read does not: adding a voice would not fix that.
            if workspace.voicesLoaded, workspace.voices.isEmpty, workspace.voiceError == nil {
                Button("Add Voice…") { studio.select(.voices) }
                    .hubRowControl()
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

    /// Speaking pace: one slider, whose range and value both come from the selected model's own
    /// lane contract.
    ///
    /// The engine applies this as pitch-preserving tempo to the finished audio, so the voice keeps
    /// its identity at every setting - which is why it is a plain slider and not an engine choice.
    /// Nothing here decides the pace: the bounds are the lane's own `minimum`/`maximum`, and the
    /// value shown is the operator's choice or, until they make one, the value in force for this
    /// model - what the Models tab stores over the pace the lane states. A model that publishes no
    /// pace gets no slider, rather than one whose movement would change nothing.
    @ViewBuilder
    private var speedControl: some View {
        if let control = workspace.audioSpeedControl, let range = control.numericRange,
           let shown = workspace.audioSpeedInForce {
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                HStack {
                    Text("Speed").font(HubType.rowTitle)
                    Spacer()
                    if workspace.audioSpeed != nil {
                        Button("Model Default") { workspace.audioSpeed = nil }
                            .font(HubType.caption).buttonStyle(.link)
                    }
                    Text(String(format: "%.2f×", shown))
                        .font(HubType.secondary.monospacedDigit())
                        .foregroundStyle(workspace.audioSpeed == nil ? .secondary : .primary)
                }
                Slider(value: Binding(get: { shown }, set: { workspace.audioSpeed = $0 }),
                       in: range, step: 0.05) {
                    Text("Speed")
                } minimumValueLabel: {
                    Text(String(format: "%.2g×", range.lowerBound)).font(HubType.caption)
                } maximumValueLabel: {
                    Text(String(format: "%.2g×", range.upperBound)).font(HubType.caption)
                }
                .labelsHidden()
                Text(workspace.audioSpeed == nil
                     ? "This model's own pace. Move the slider to choose another."
                     : shown > 1.0
                     ? "Faster than this model's pace, with the voice unchanged."
                     : shown < 1.0
                     ? "Slower than this model's pace, with the voice unchanged."
                     : "This model's pace.")
                    .font(HubType.caption).foregroundStyle(.secondary)
            }
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
            return "\(model.label) does not publish \(workspace.audioMode.shortTitle). Pick a model that does, or choose another tab."
        }
        if workspace.audioMode == .preset, !model.publishesPresetRoster {
            return "This model's voice is built in. Type your script and generate."
        }
        return workspace.audioMode.detail
    }

    /// The ways of choosing a voice, as tabs — the modes this Mac's Audio models publish between
    /// them (`workspace.audioModes`).
    ///
    /// The tabs used to be exactly the selected model's modes, which closed a loop with the model
    /// picker below: the tab decided which models the picker listed, and the selected model decided
    /// which tabs existed. A model that could not do the mode you wanted therefore also hid every
    /// model that could — the owner had Khmer TTS installed, was on the default Clone tab with a
    /// cloning-only model selected, and had no Ready-Made tab to press and no way to see that a
    /// preset voice existed on his own machine. `audioModes` in the store carries the full shape.
    ///
    /// A Mac whose models publish no modes at all gets no tabs and a sentence saying so, rather than
    /// a guessed form — guessing is how a CustomVoice model was once sent down the clone path.
    private var voiceModeTabs: some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            if !workspace.audioModes.isEmpty {
                HubSegmentedControl(
                    selection: $workspace.audioMode,
                    items: workspace.audioModes.map { mode in
                        HubSegmentItem(tag: mode, label: mode.shortTitle, accessibilityLabel: mode.title)
                    },
                    accessibilityLabel: "Voice"
                )
                .frame(maxWidth: .infinity)
                .onChange(of: workspace.audioMode) { _, mode in
                    // The tab decides the shape of the request, so the model has to be one that
                    // publishes it. `preferredAudioModel` picks the best that can actually run
                    // here; this used to take the first listed, which could be a model this Mac
                    // cannot generate with, leaving Generate disabled for no visible reason.
                    let keepsSelection = workspace.audioModels(for: mode).contains {
                        $0.repo == workspace.audioModel && NativeWorkspaceStore.runsHere($0)
                    }
                    if !keepsSelection {
                        workspace.audioModel = workspace.preferredAudioModel(for: mode)?.repo ?? ""
                    }
                }
            }
            Text(audioModeDetail).font(HubType.caption).foregroundStyle(.secondary)
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
        case .video: return $workspace.videoModel
        }
    }

    @ViewBuilder
    private func modelPicker(_ function: StudioFunction) -> some View {
        // Every page that generates uses this one picker: the Chat page used to draw its own
        // model name instead, so even a second listed Chat model could not be chosen.
        let models = workspace.supportedModels(for: function)
        if let selection = modelSelection(function) {
            LabeledRow(label: "Model") {
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Picker("Model", selection: selection) {
                        Text("Choose a model").tag("")
                        ForEach(models) { model in
                            Text(model.pickerName + (model.cached ? "" : " · Not installed") + model.pickerSuffix)
                                .tag(model.repo)
                        }
                    }.labelsHidden()
                    if let model = models.first(where: { $0.repo == selection.wrappedValue }) {
                        Label(model.cached ? "Installed" : "Download in Models", systemImage: model.cached ? "checkmark.circle" : "arrow.down.circle")
                            .font(HubType.caption).foregroundStyle(model.cached ? Color.secondary : HubStatus.warn.textColor)
                        // Why this model will not run here, when it will not. The Hub has always checked
                        // five gates before accepting a job - files, runtime, the macOS floor, the chip and
                        // the studio process - and the app used to show only that something was unavailable.
                        if let reason = model.blockReason {
                            Label(reason, systemImage: "exclamationmark.triangle")
                                .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if models.isEmpty {
                        Text(function == .audio
                             ? (connected ? workspace.audioMode.emptyReason : "Start the services to load available models.")
                             : (connected
                                ? (function == .video ? "No video model is available." : "No compatible model is available for this workspace yet.")
                                : "Start the services to load available models."))
                            .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        } else {
            // A Studio with no picker is named here with the file to update, rather than quietly
            // rendering nothing or, worse, another Studio's list.
            Text("This build has no model picker wired for \(function.rawValue). Add it to `modelSelection(_:)` in native/StudioViews.swift.")
                .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var transcriptionWorkspace: some View {
        PageScaffold(title: pageTitle(.transcription),
                     status: studioStatus(.transcription, active: activeSubtitles.count, selected: workspace.transcriptionModel),
                     menu: studioPageMenu) {
            if !connected { setup }
            StudioSplit(sideTitle: "Transcript", sideSymbol: "text.alignleft") {
                // The form, on the left, in the same shape as the Audio and Image pages. The
                // primary action used to sit at the foot of a flexible column, a long way from the
                // file and format it acts on, with the transcript placeholder floating in between.
                ScrollView {
                    VStack(alignment: .leading, spacing: HubSpace.l) {
                        HStack(spacing: HubSpace.m) {
                            Image(systemName: "waveform").font(HubType.icon).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: HubSpace.xs) {
                                Text(workspace.transcriptionFile?.lastPathComponent ?? "No recording selected").font(HubType.rowTitle).lineLimit(2).textSelection(.enabled)
                                Text("Audio or video file").font(HubType.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Choose File…", action: chooseTranscriptionFile).disabled(workspace.transcribing)
                        }.padding(HubSpace.l).hubCardSurface()
                        // The label sits above the control rather than beside it: in a form this
                        // narrow a leading label wrapped in half ("Result / format").
                        VStack(alignment: .leading, spacing: HubSpace.tight) {
                            Text("Result format").font(HubType.rowTitle)
                            HStack {
                                HubSegmentedControl(
                                    selection: $transcriptFormat,
                                    items: [
                                        HubSegmentItem(tag: "Text", label: "Text"),
                                        HubSegmentItem(tag: "SRT", label: "SRT"),
                                        HubSegmentItem(tag: "VTT", label: "VTT")
                                    ],
                                    accessibilityLabel: "Result format"
                                )
                                Spacer()
                                if workspace.transcribing { ProgressView().controlSize(.small) }
                                pinChip
                                Button("Transcribe") { Task { await workspace.transcribe() } }.buttonStyle(.borderedProminent)
                                    .disabled(!connected || workspace.transcribing || workspace.transcriptionFile == nil || selectedTranscriptionModel?.cached != true || selectedTranscriptionModel?.supported != true || selectedTranscriptionModel?.runtimeReady != true)
                            }
                        }
                        if workspace.transcribing { transcriptionActivity }
                        Divider().padding(.top, 8)
                        Text("Recognition").font(HubType.section)
                        modelPicker(.transcription)
                        // The Subtitle worker's own below-tier sentence, in the same shape the
                        // Image page uses for its published warning: advice, not a wall, and
                        // nothing at all when this Mac is at or above the tier. It comes from the
                        // model's catalogue row (`memory_warning`), the same key and the same place
                        // Image, Music and Chat publish theirs.
                        if let warning = selectedTranscriptionModel?.memoryWarning {
                            advisory(warning)
                        }
                        LabeledRow(label: "Language",
                                   help: "Leave blank to detect the language, or enter a code such as en. Blank means this model's own default, which the Models tab sets.") {
                            TextField("Auto-detect", text: $workspace.transcriptionLanguage).textFieldStyle(.roundedBorder)
                        }
                        // Shown only where the model's own runtime claims word timings and its lane
                        // publishes the parameter, so the wording never promises timings a model
                        // cannot produce.
                        if let wordTimings = workspace.wordTimestampsControl {
                            VStack(alignment: .leading, spacing: HubSpace.tight) {
                                Toggle(isOn: Binding(get: { wordTimings },
                                                     set: { workspace.wordTimestamps = $0 })) {
                                    Text("Word timings").font(HubType.rowTitle)
                                }
                                Text("Per-word start and end times in the returned segments. Off returns segment timings only. The value shown is this model's default until you change it.")
                                    .font(HubType.caption).foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        Divider().padding(.top, 8)
                        Text("This workspace keeps its recording, model and transcript separate from Audio.").font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Manage Subtitle Models") { modelFilter = "Transcription"; studio.select(.models) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.trailing, HubSpace.m).padding(.bottom, HubSpace.l)
                }
                .frame(minWidth: 350, idealWidth: 390, maxWidth: 440)
            } side: {
                // What you made, on the right: the transcript itself, then its history underneath.
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    HStack {
                        Text("Transcript").font(HubType.section)
                        if let result = workspace.transcriptionResult {
                            Text("\(result.language) · \(result.duration, specifier: "%.1f") seconds").font(HubType.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Export…", action: exportTranscript).disabled(workspace.transcriptionResult == nil)
                    }
                    ScrollView {
                        if let result = workspace.transcriptionResult {
                            Text(transcriptFormat == "SRT" ? result.srt : transcriptFormat == "VTT" ? result.vtt : result.text)
                                .font(transcriptFormat == "Text" ? HubType.body : HubType.mono)
                                .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(HubSpace.l)
                        } else { empty("Your transcript", "Choose a recording and transcribe it. Text and subtitle exports will appear here.", symbol: "text.alignleft") }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: HubRadius.card))
                    .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                    Divider()
                    HStack { Text("Recent subtitles").font(HubType.section); Spacer(); Button("All Jobs") { studio.select(.jobs) }.hubRowControl() }
                    if workspace.subtitleJobs.isEmpty {
                        Text("Completed subtitles will appear here.").foregroundStyle(.secondary).font(HubType.secondary)
                        Spacer(minLength: 0)
                    } else {
                        // The subtitle row, not the audio one: this panel once drew Play and Save
                        // for a transcript, while the Jobs page correctly offered Open Transcript
                        // for the very same job.
                        ScrollView { RecentRows(items: workspace.subtitleJobs) { subtitleJobRow($0) } }
                        .frame(minHeight: 60, maxHeight: 170)
                    }
                }
                .padding(.leading, HubSpace.m)
                .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        // The Audio page reads the voice library itself. Loading it only on the Voices page meant a
        // fresh launch that went straight here showed "No saved voices yet" and a disabled picker
        // while ten voices were sitting on the Mac, and nothing on screen said the list had simply
        // never been fetched. It reads once per connection: the store is what knows whether the
        // list is already in hand, so a visit to this page cannot cancel or repeat a read.
        .task(id: voiceLibraryLoadKey) {
            guard connected else { return }
            await workspace.loadVoicesIfNeeded()
        }
        // The Subtitle page has no per-run default to seed — the language and the word-timestamp
        // toggle are per-request — but its Studio's published defaults are read here too, so the
        // Models tab shows this model's editor without waiting for a visit to that tab.
        .task(id: modelDefaultsLoadKey) {
            await workspace.loadModelDefaultsIfNeeded(for: selectedTranscriptionModel)
        }
    }

    // MARK: - Models: one section per Studio, and the per-model defaults editor

    /// The Models tab, one section per Studio in the app's own Studio list.
    ///
    /// The list comes from `StudioFunction.allCases`, so a Studio added to the app gets a section
    /// with its own symbol, name and count without an edit here — and a Studio that is missing from
    /// the list below cannot be missing from the screen.
    /// The list only has to hold a model's name and its status badge; everything past that belongs
    /// to the details. The ceiling keeps the details able to take most of the window.
    private static let modelsListMinWidth: Double = 360
    private static let modelsListMaxWidth: Double = 720

    private var modelSections: [NativeModelSection] {
        workspace.modelSections(search: modelSearch, installedOnly: installedOnly)
    }

    /// The functions the tab bar offers: the ones this library actually holds a model for.
    ///
    /// A Studio with nothing in it is not a tab. It used to be a heading with nothing under it
    /// ("Video — 0 models"), which is the row of nothing the owner asked to be rid of, and a tab
    /// that only says zero is the same row. The one exception is the Studio the operator arrived
    /// from — the generator pages' "Manage … Models" rows land here — because a tab that vanished
    /// under the click that chose it reads as a broken button.
    ///
    /// The order is the app's own (`StudioPage.createOrder`, the sidebar's), so a Studio added to
    /// the enum is placed without an edit here, and one the order does not name still gets a tab.
    private var modelTabFunctions: [StudioFunction] {
        var available = StudioFunction.allCases.filter(modelLibraryHolds)
        if let chosen = StudioFunction(rawValue: modelFilter), !available.contains(chosen) {
            available.append(chosen)
        }
        let placed = StudioPage.createOrder.filter { available.contains($0) }
        return placed + available.filter { !StudioPage.createOrder.contains($0) }
    }

    /// Whether this library holds any model for a function at all — before the search and before
    /// "Installed only", so a tab cannot vanish under the operator's own typing.
    private func modelLibraryHolds(_ function: StudioFunction) -> Bool {
        workspace.modelSections(search: "", installedOnly: false)
            .first { $0.function == function }?.models.isEmpty == false
    }

    /// The one tab bar: every function, with the count this view would show under it.
    private var modelTabs: some View {
        let counts = modelSections.reduce(into: [StudioFunction: Int]()) { $0[$1.function] = $1.models.count }
        let total = counts.values.reduce(0, +)
        let icons = windowWidth >= 980
        var items = [HubSegmentItem(tag: "All functions", label: "All \(total)",
                                    symbol: icons ? "square.grid.2x2" : nil)]
        for function in modelTabFunctions {
            let label = "\(StudioPage.page(for: function)?.title ?? function.rawValue) \(counts[function] ?? 0)"
            items.append(HubSegmentItem(tag: function.rawValue, label: label,
                                        symbol: icons ? function.symbol : nil))
        }
        return HubSegmentedControl(
            selection: $modelFilter,
            items: items,
            controlSize: icons ? .regular : .small,
            accessibilityLabel: "Function"
        )
    }

    /// The sections the list draws: every Studio with something in it under "All", the chosen
    /// Studio's own section under a function's tab. A section with nothing under it is drawn
    /// nowhere — an empty heading is the row of nothing the owner asked to be rid of.
    private var modelSectionsToShow: [NativeModelSection] {
        modelSections.filter { section in
            !section.models.isEmpty
                && (modelFilter == "All functions" || section.function.rawValue == modelFilter)
        }
    }

    /// The divider between the list and the details, dragged either way and remembered.
    ///
    /// This was an `HSplitView` whose details side was capped at 380 pt and whose list side had a
    /// 380 pt floor, so the divider would only move one way. Both sides now have their own floor
    /// and no ceiling but the window's.
    private var modelSplitDivider: some View {
        Rectangle()
            .fill(Color(nsColor: .separatorColor))
            .frame(width: 1)
            .frame(width: HubSpace.m)
            .contentShape(Rectangle())
            .onHover { inside in
                if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
            }
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        let start = modelsSplitDragStart ?? modelsListWidth
                        if modelsSplitDragStart == nil { modelsSplitDragStart = start }
                        modelsListWidth = min(max(start + value.translation.width,
                                                  Self.modelsListMinWidth), Self.modelsListMaxWidth)
                    }
                    .onEnded { _ in modelsSplitDragStart = nil }
            )
            // A drag is not reachable from the keyboard, so the divider is adjustable too.
            .accessibilityElement()
            .accessibilityLabel("Resize the model list")
            .accessibilityValue("\(Int(modelsListWidth)) points")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment:
                    modelsListWidth = min(modelsListWidth + 40, Self.modelsListMaxWidth)
                case .decrement:
                    modelsListWidth = max(modelsListWidth - 40, Self.modelsListMinWidth)
                default: break
                }
            }
    }

    /// When the Models tab (re)reads every Studio's published defaults: the connection, never the
    /// search or the selection, so a keystroke cannot start a read.
    private var modelDefaultsLoadKey: String {
        "\(connected)/\(workspace.connectionGeneration)"
    }

    /// Whether a Studio's section shows its models.
    ///
    /// A search opens every section it matched: a result hidden inside a collapsed section is a
    /// result the operator cannot see, which is the one thing a search must never do.
    private func isModelSectionExpanded(_ function: StudioFunction) -> Bool {
        !modelSearch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !collapsedModelSections.contains(function.id)
    }

    private func modelSectionHeader(_ section: NativeModelSection) -> some View {
        Button {
            if collapsedModelSections.contains(section.function.id) {
                collapsedModelSections.remove(section.function.id)
            } else {
                collapsedModelSections.insert(section.function.id)
            }
        } label: {
            HStack(spacing: HubSpace.s) {
                Image(systemName: "chevron.right")
                    .font(HubType.captionStrong)
                    .rotationEffect(.degrees(isModelSectionExpanded(section.function) ? 90 : 0))
                Image(systemName: section.symbol)
                Text(StudioPage.page(for: section.function)?.title ?? section.header)
                    .font(HubType.rowTitle)
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(section.header)
        .accessibilityHint(isModelSectionExpanded(section.function)
                           ? "Collapse this Studio" : "Expand this Studio")
        .id(section.id)
    }

    /// What one catalog row says about the model: the state a person needs before choosing it.
    /// "Installed" is not "ready" — a paused or unverified model is a different answer, and a
    /// download in flight is a third. The Hub already publishes every one of these facts.
    private func modelStateChip(_ model: NativeModel) -> (text: String, status: HubStatus, symbol: String?) {
        if let download = workspace.downloads.first(where: { $0.repo == model.repo && active($0.state) }) {
            let percent = Int(min(100, max(0, download.percent)))
            return ("Downloading \(percent)%", .info, "arrow.down.circle")
        }
        if model.paused { return ("Paused", .warn, "pause.circle.fill") }
        if model.cached, model.runtimeReady, model.supported { return ("Ready", .ok, "checkmark.circle.fill") }
        if model.cached { return ("Needs setup", .warn, "exclamationmark.triangle.fill") }
        return ("Not downloaded", .off, "icloud.and.arrow.down")
    }

    private func modelListRow(_ model: NativeModel) -> some View {
        let chip = modelStateChip(model)
        return HStack(spacing: HubSpace.m) {
            Image(systemName: model.function.symbol).foregroundStyle(.secondary).frame(width: 22)
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                Text(model.label).font(HubType.rowTitle)
                // Under "All" the second line is the Studio the model belongs to. Inside that
                // Studio's own tab the tab already says it, so the line carries the size the
                // catalogue states instead of repeating the heading on every row.
                if let detail = modelRowDetail(model) {
                    Text(detail).font(HubType.caption).foregroundStyle(.secondary)
                }
                // Scanning the library, "how is this one chunked?" should not need a
                // click per model: the section size and the speed route are the two
                // facts that differ between families and decide how a long script runs.
                if let longForm = model.longForm {
                    Text("Sections of \(longForm.chosenSectionMaxCharacters) chars · speed: \(longForm.speedDelivery == "native" ? "engine" : "post-pass")")
                        .font(HubType.caption).foregroundStyle(.tertiary)
                }
                // A model with no Download button says why on its own row, not only in Details.
                if let reason = model.blockReason {
                    Text(reason).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: HubSpace.m)
            if !model.cached, model.supported,
               !workspace.downloads.contains(where: { $0.repo == model.repo && active($0.state) }) {
                Button("Download") { Task { await workspace.startDownload(model) } }
                    .controlSize(.small)
                    .disabled(!connected || studio.configuration.offline)
                    .help(studio.configuration.offline
                          ? "Downloading is off while Offline mode is on. Use Import Folder instead."
                          : "Download this model into the library")
            }
            StatusBadge(text: chip.text, status: chip.status, symbol: chip.symbol)
        }
        // The badges keep their own room at the trailing edge instead of being pressed against it.
        .padding(.vertical, HubSpace.s).padding(.trailing, HubSpace.s).tag(model.repo)
    }

    /// A row's second line: the Studio's name only where the list is not already one Studio's, plus
    /// the size the catalogue states — the same figure the details pane labels "Estimated size".
    private func modelRowDetail(_ model: NativeModel) -> String? {
        var parts: [String] = []
        if modelFilter == "All functions" { parts.append(model.function.rawValue) }
        if let size = model.sizeGB { parts.append(String(format: "%.2f GB", size)) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// Every read-only fact this model publishes, each with the artefact that states it.
    private func modelFactsBlock(_ model: NativeModel) -> some View {
        let facts = model.facts
        return Group {
            if !facts.isEmpty {
                Divider()
                Text("What this model publishes").font(HubType.section)
                factRows(facts)
            }
        }
    }

    /// How a long script is cut up for this model, and how its speed control reaches the engine —
    /// both per-family decisions the runtime makes, rendered from the catalogue's own policy.
    ///
    /// Kept as its own named block rather than folded into the fact list because it is the one
    /// group of facts an operator reads together, and the four lines answer the question the owner
    /// asked ("how is chunking applied per model?") without opening a terminal.
    @ViewBuilder private func modelLongFormBlock(_ model: NativeModel) -> some View {
        if let longForm = model.longForm {
            Divider()
            Text("Long-form delivery").font(HubType.section)
            factRows(longForm.facts)
        }
    }

    /// The free memory a job must find before this model starts, and whose number it is.
    ///
    /// This is the Hub's own floor (`memory_admission.describe`), not the RAM tier above it: it is
    /// the number dispatch waits on, and the only admission number an operator may replace — the
    /// override is site-local, so it lives on this Hub and not on the model's row. It is shown here
    /// because an override left behind by a measurement run is otherwise invisible: the row is the
    /// model's, and the operator's number is nowhere on the model. The way back to the model's own
    /// figure sits beside it while there is something to go back from.
    @ViewBuilder private func modelFreeMemoryBlock(_ model: NativeModel) -> some View {
        if let gb = model.freeFloorGB {
            Divider()
            HStack(alignment: .firstTextBaseline) {
                Text("Memory to start a job").font(HubType.section)
                Spacer(minLength: HubSpace.s)
                if model.freeFloorIsOperatorSet {
                    Button("Reset to default") {
                        Task { await workspace.resetFreeMemoryFloor(for: model) }
                    }
                    .help("Use the free-memory floor this model's own row publishes")
                    .disabled(!connected)
                }
            }
            factRows([NativeModelFact(label: "Free before start",
                                      value: "\(NativeJSONValue.trimmed(gb)) GB",
                                      source: model.freeFloorSourceText)])
        }
    }

    /// One row per fact, with the artefact that stated it where there is one.
    private func factRows(_ facts: [NativeModelFact]) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            ForEach(facts) { fact in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: HubSpace.s) {
                        Text(fact.label).font(HubType.secondary)
                        Spacer(minLength: 12)
                        Text(fact.value).font(HubType.secondary.monospacedDigit())
                            .textSelection(.enabled)
                            .multilineTextAlignment(.trailing)
                            // A published bound can be long ("up to 40000 characters · required");
                            // it wraps to the next line rather than being cut off with an ellipsis
                            // the moment the details pane is narrow.
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let source = fact.source, !source.isEmpty {
                        Text("Source: \(source)").font(HubType.caption).foregroundStyle(.tertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    /// "Your defaults for this model": one control per parameter the Studio published as settable.
    ///
    /// The controls, their order, their types and their bounds are the lane's own — nothing here
    /// decides which controls exist. A parameter this editor cannot render (an `array` or an
    /// `object`) is shown with the lane's note rather than faked; a parameter the lane marks
    /// required never appears; and a parameter the lane says it ignores is shown as ignored.
    private func modelDefaultsEditor(_ model: NativeModel) -> some View {
        let controls = workspace.modelDefaultControls(for: model)
        let refusals = workspace.modelDefaultRefusals(for: model)
        let notUsed = workspace.modelDefaultsNotUsed(for: model)
        return Group {
            if model.laneContract != nil {
                Divider()
                Text("Your defaults for this model").font(HubType.section)
                if let file = workspace.modelDefaultsSettingsFile {
                    Text("Stored on this Mac in \(file), and applied when a request leaves the parameter out.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let note = workspace.modelDefaultsNote {
                    Text(note).font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !notUsed.isEmpty {
                    VStack(alignment: .leading, spacing: HubSpace.xs) {
                        Label("Saved values this model no longer accepts", systemImage: "exclamationmark.triangle")
                            .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                        ForEach(notUsed, id: \.self) { line in
                            Text(line).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Text("They are kept in the file and ignored until you change them; nothing sends them.")
                            .font(HubType.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if controls.isEmpty {
                    Text("This model's Studio publishes no settable parameter to default.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    VStack(alignment: .leading, spacing: HubSpace.m) {
                        ForEach(controls) { control in
                            modelDefaultControlRow(control, refusals: refusals)
                        }
                    }
                    HStack {
                        Button(workspace.savingModelDefaults ? "Saving…" : "Save Defaults") {
                            let values = modelDefaultDraftValues(controls)
                            Task { await workspace.saveModelDefaults(for: model, values: values) }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!connected || workspace.savingModelDefaults || !workspace.modelDefaultsLoaded)
                        Button("Restore This Model's Defaults") {
                            Task { await workspace.restoreModelDefaults(for: model) }
                        }
                        .help("Forget this model's saved defaults and use what its Studio states")
                        .disabled(!connected || workspace.savingModelDefaults)
                        if modelDefaultDraft.isEmpty == false {
                            Text("Unsaved changes.").font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        }
                    }
                }
            }
        }
    }

    /// The value a control would send: what the operator typed, or what is in force.
    private func modelDefaultDraftValues(_ controls: [NativeModelDefaultControl]) -> [String: NativeJSONValue] {
        var values: [String: NativeJSONValue] = [:]
        for control in controls where control.sendsValue {
            if let typed = modelDefaultDraft[control.name] { values[control.name] = typed }
            else if let value = control.value { values[control.name] = value }
        }
        return values
    }

    @ViewBuilder private func modelDefaultControlRow(
        _ control: NativeModelDefaultControl, refusals: [String: String]
    ) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.xs) {
            HStack(spacing: HubSpace.s) {
                Text(control.name).font(HubType.secondary)
                if control.chosen {
                    Text("chosen").font(HubType.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 12)
                modelDefaultControl(control)
            }
            Text(control.boundText).font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let ladder = control.ladder, !ladder.isEmpty, control.sendsValue {
                modelLadderRow(control, ladder: ladder)
            }
            if control.ignored {
                Label("This model accepts \(control.name) and discards it, so it is not editable here.",
                      systemImage: "exclamationmark.triangle")
                    .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                    .fixedSize(horizontal: false, vertical: true)
            } else if let note = control.note, !note.isEmpty {
                Text(note).font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // A refused value is shown against the field it belongs to, with the lane's own reason.
            if let reason = refusals[control.name] {
                Label(reason, systemImage: "exclamationmark.circle.fill")
                    .font(HubType.caption).foregroundStyle(HubStatus.bad.textColor)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// The lengths a Music lane suggests, offered beside the field that accepts any of them.
    ///
    /// They are buttons, not a picker: the parameter stays the bounded number the lane validates,
    /// so an operator may store a length between the rungs. A rung the lane marks as beyond one
    /// pass says so — a length that is rendered in sections is a fact about the job, not a detail.
    private func modelLadderRow(_ control: NativeModelDefaultControl,
                                ladder: [NativeLadderRung]) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.xs) {
            HStack(spacing: HubSpace.m) {
                ForEach(ladder) { rung in
                    Button {
                        setDefaultNumber(control, rung.seconds)
                    } label: {
                        Text(rung.exceedsSinglePass == true ? "\(rung.label) · sections" : rung.label)
                            .font(HubType.caption)
                            .foregroundStyle(rung.exceedsSinglePass == true ? HubStatus.warn.textColor : Color.accentColor)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(rung.exceedsSinglePass == true
                                        ? "\(rung.label), rendered in sections" : rung.label)
                }
            }
            Text("Suggested lengths, not the only ones: anything inside this range is accepted.")
                .font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func setDefaultNumber(_ control: NativeModelDefaultControl, _ seconds: Double) {
        if control.kind == .integer {
            modelDefaultDraft[control.name] = .integer(Int(seconds.rounded()))
        } else {
            modelDefaultDraft[control.name] = .number(seconds)
        }
    }

    @ViewBuilder private func modelDefaultControl(_ control: NativeModelDefaultControl) -> some View {
        switch control.kind {
        case .integer:
            HStack(spacing: HubSpace.tight) {
                TextField(control.name, value: integerBinding(control), format: .number)
                    .textFieldStyle(.roundedBorder).multilineTextAlignment(.trailing)
                    .frame(width: 90)
                    .accessibilityLabel(control.name)
                if let range = integerRange(control) {
                    Stepper(control.name, value: integerBinding(control), in: range).labelsHidden()
                }
            }
            .disabled(control.ignored)
        case .number:
            TextField(control.name, value: numberBinding(control), format: .number)
                .textFieldStyle(.roundedBorder).multilineTextAlignment(.trailing)
                .frame(width: 95)
                .accessibilityLabel(control.name)
                .disabled(control.ignored)
        case .choice(let options):
            Picker(control.name, selection: choiceBinding(control, options: options)) {
                ForEach(choiceOptions(control, options), id: \.self) { option in Text(option).tag(option) }
            }
            .labelsHidden().frame(maxWidth: 220)
            .disabled(control.ignored)
        case .text:
            TextField(control.name, text: textBinding(control))
                .textFieldStyle(.roundedBorder).frame(maxWidth: 220)
                .accessibilityLabel(control.name)
                .disabled(control.ignored)
        case .toggle:
            Toggle(control.name, isOn: toggleBinding(control))
                .labelsHidden().toggleStyle(.switch)
                .disabled(control.ignored)
        case .unrenderable:
            Text("published as \(control.typeName); not editable here")
                .font(HubType.caption).foregroundStyle(.secondary)
        }
    }

    /// A text field will not exceed the lane's published character limit: a value the lane would
    /// refuse is not allowed to leave the field in the first place.
    private func clampText(_ text: String, to control: NativeModelDefaultControl) -> String {
        guard let limit = control.maxCharacters, text.count > limit else { return text }
        return String(text.prefix(limit))
    }

    private func integerRange(_ control: NativeModelDefaultControl) -> ClosedRange<Int>? {
        guard control.kind == .integer else { return nil }
        guard control.minimum != nil || control.maximum != nil else { return nil }
        let current = integerBinding(control).wrappedValue
        let low = control.minimum.map { Int(($0 + (control.minimumExclusive ? 1 : 0)).rounded(.up)) } ?? current
        let high = control.maximum.map { Int(($0 - (control.maximumExclusive ? 1 : 0)).rounded(.down)) } ?? current
        return low...max(low, high)
    }

    private func integerBinding(_ control: NativeModelDefaultControl) -> Binding<Int> {
        Binding(get: {
            if let typed = modelDefaultDraft[control.name]?.intValue { return typed }
            if let value = control.value?.intValue { return value }
            if let value = control.stated?.intValue { return value }
            return Int(control.minimum ?? 0)
        }, set: { modelDefaultDraft[control.name] = .integer($0) })
    }

    private func numberBinding(_ control: NativeModelDefaultControl) -> Binding<Double> {
        Binding(get: {
            if let typed = modelDefaultDraft[control.name]?.doubleValue { return typed }
            if let value = control.value?.doubleValue { return value }
            if let value = control.stated?.doubleValue { return value }
            return control.minimum ?? 0
        }, set: { modelDefaultDraft[control.name] = .number($0) })
    }

    private func textBinding(_ control: NativeModelDefaultControl) -> Binding<String> {
        Binding(get: {
            modelDefaultDraft[control.name]?.stringValue ?? control.value?.stringValue
                ?? control.stated?.stringValue ?? ""
        }, set: { modelDefaultDraft[control.name] = .string(clampText($0, to: control)) })
    }

    private func toggleBinding(_ control: NativeModelDefaultControl) -> Binding<Bool> {
        Binding(get: {
            modelDefaultDraft[control.name]?.boolValue ?? control.value?.boolValue
                ?? control.stated?.boolValue ?? false
        }, set: { modelDefaultDraft[control.name] = .bool($0) })
    }

    private func choiceBinding(_ control: NativeModelDefaultControl,
                               options: [String]) -> Binding<String> {
        Binding(get: {
            modelDefaultDraft[control.name]?.stringValue
                ?? control.value?.stringValue ?? control.stated?.stringValue
                ?? options.first ?? ""
        }, set: { modelDefaultDraft[control.name] = .string($0) })
    }

    /// The lane's own options, plus the value in force when the lane no longer offers it — shown
    /// so the operator can see the value rather than have the picker silently move to another one.
    private func choiceOptions(_ control: NativeModelDefaultControl, _ options: [String]) -> [String] {
        let current = modelDefaultDraft[control.name]?.stringValue
            ?? control.value?.stringValue ?? control.stated?.stringValue
        guard let current, !options.contains(current) else { return options }
        return [current] + options
    }

    // MARK: - Set up this Mac: the install-time model picker (owner decision 2026-10-28)

    /// The choice a new Mac makes once, and the same screen for add/remove later.
    ///
    /// Three things are true on it at all times, because each is a promise the owner made:
    /// **this Mac's memory and the free space on the models' own drive** are stated before
    /// anything is chosen; **a model too big for this Mac is shown greyed out with its floor and
    /// this Mac's memory**, never hidden and never swapped; and **"complete" means installed and
    /// verified**, counted from the Hub's receipts, so a preset at 9 of 10 says 9 of 10.
    ///
    /// The four presets are the Hub's, read from `model-presets/` as data - this view names no
    /// preset and no package, so a fifth preset or a changed list needs no build of this app.
    private var setupWorkspace: some View {
        PageScaffold(title: pageTitle(.setup),
                     status: setupStatus,
                     scrolls: true) {
            if !connected { setup }
            // This read is **on demand**, deliberately: it is not part of the two-second poll.
            // The Hub's version of it used to walk /Volumes and could freeze the Mac on an
            // unanswered macOS permission prompt (0.6.206-0.6.209).
            if workspace.installPlanLoading && workspace.installPlan == nil {
                HubCard {
                    HStack(spacing: HubSpace.m) {
                        ProgressView().controlSize(.small)
                        Text("Reading this Mac's models…").font(HubType.body).foregroundStyle(.secondary)
                    }
                }
            } else if let error = workspace.installPlanError, workspace.installPlan == nil {
                HubCard {
                    VStack(alignment: .leading, spacing: HubSpace.s) {
                        Text("This Mac's models could not be read").font(HubType.rowTitle)
                        Text(error).font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Try Again") { Task { await workspace.loadInstallPlan() } }
                    }
                }
            } else if !workspace.installPlanSupported && workspace.installPlan == nil {
                HubCard {
                    VStack(alignment: .leading, spacing: HubSpace.s) {
                        Text("This Hub Studio KH does not offer the install screen yet").font(HubType.rowTitle)
                        Text("Update Hub Studio KH on this Mac, then reopen this page. The Models page still works for everything else.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            } else if let plan = workspace.installPlan {
                setupSummary(plan)
                if !plan.problems.isEmpty {
                    HubCard {
                        VStack(alignment: .leading, spacing: HubSpace.xs) {
                            Text("This Mac's preset list needs fixing").font(HubType.rowTitle)
                            ForEach(plan.problems, id: \.self) { problem in
                                Text(problem).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
                setupPresets(plan)
                setupManual(plan)
            } else {
                HubCard {
                    HStack(spacing: HubSpace.m) {
                        ProgressView().controlSize(.small)
                        Text("Reading this Mac's models…").font(HubType.body).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .task { if connected { await workspace.loadInstallPlan() } }
    }

    /// What this Mac is and what it has, before any choice: memory, the drive, and where the models
    /// come from. The free figure is the **library's** volume - the Hub measures that one, and on a
    /// kit setup it is an external drive, not the startup disk.
    /// The setup page's one line: what this Mac has and whether a set fits, before any copy.
    private var setupStatus: PageStatusLine {
        guard localServicesReady else {
            return PageStatusLine("Local services are stopped — start them to read this Mac's models.", tone: .warn)
        }
        if workspace.installPlanLoading && workspace.installPlan == nil {
            return PageStatusLine("Reading this Mac's models and free space…", tone: .off)
        }
        if workspace.installPlanError != nil, workspace.installPlan == nil {
            return PageStatusLine("This Mac's models could not be read — try again below.", tone: .warn)
        }
        guard let plan = workspace.installPlan else {
            return PageStatusLine("Reading this Mac's models…", tone: .off)
        }
        let memory = plan.memoryGB.map { "\(Int($0)) GB memory" } ?? "memory not reported"
        let free = plan.diskMeasured && plan.freeBytes != nil
            ? "\(Self.gigabytes(plan.freeBytes!)) free on the models' drive"
            : "free space not measured"
        return PageStatusLine("\(memory) · \(free)", tone: plan.diskMeasured ? .ok : .warn)
    }

    private func setupSummary(_ plan: NativeInstallPlan) -> some View {
        HubCard {
            VStack(alignment: .leading, spacing: HubSpace.m) {
                HStack(alignment: .firstTextBaseline, spacing: HubSpace.l) {
                    Label(plan.memoryGB.map { "\(Int($0)) GB of memory" } ?? "Memory not reported",
                          systemImage: "memorychip")
                        .font(HubType.rowTitle)
                    Spacer(minLength: HubSpace.m)
                    if plan.diskMeasured, let free = plan.freeBytes {
                        // Free space on the **models'** volume, which is the number that decides
                        // whether a set fits - not the startup disk's.
                        Label("\(Self.gigabytes(free)) free on the models' drive",
                              systemImage: "internaldrive")
                            .font(HubType.body)
                    } else {
                        Label("Free space not measured", systemImage: "internaldrive")
                            .font(HubType.body).foregroundStyle(HubStatus.warn.textColor)
                    }
                }
                if !plan.library.isEmpty {
                    DiagnosticDetails(title: "Library", summary: "where models are stored") {
                        DiagnosticValue(label: "Folder", value: plan.library)
                    }
                }
                if !plan.diskMeasured {
                    Text("The Hub could not read the free space on that drive, so it cannot say whether a set fits. Open Models to check the drive is mounted.")
                        .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text("To install from an SSD, open the drive and run its copy command, or copy the models into the library in Finder. Otherwise models download from another Mac or the internet.")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private static func gigabytes(_ bytes: Int64) -> String {
        // Storage is decimal GB, the unit Finder and the Hub's own plan use. The binary formatter
        // is for RAM, where "32 GB" means 34.36e9 bytes; using it for disk made one card show
        // 93.4 GB and its own refusal say 100.3 GB for the same copy (critic, 2026-11-13).
        String(format: "%.1f GB", Double(bytes) / 1_000_000_000)
    }

    /// The four presets, each with its own count, cost and refusal. Selecting one marks it; nothing
    /// is copied until the button on that card is pressed.
    private func setupPresets(_ plan: NativeInstallPlan) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(alignment: .firstTextBaseline) {
                sectionTitle("Presets")
                Spacer(minLength: HubSpace.m)
                if let starting = plan.starting {
                    Text("This Mac suits \(starting.name)").font(HubType.caption).foregroundStyle(.secondary)
                }
            }
            // The action sits above the cards: at 768 the four tall cards stacked and pushed the
            // only Install button below the fold (critic, 2026-11-13).
            if let preset = plan.presets.first(where: { $0.id == setupSelectedPreset }) ?? plan.starting {
                HStack(spacing: HubSpace.m) {
                    Button(preset.complete ? "Install anything missing" : "Install \(preset.name)") {
                        Task { await workspace.installPreset(preset) }
                    }
                    .hubPrimary()
                    .disabled(!connected || preset.refused != nil || !workspace.installBusy.isEmpty)
                    if preset.refused != nil {
                        Text("Not enough space on the models' drive for this set.")
                            .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                    }
                    Spacer(minLength: 0)
                }
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: HubSpace.m, alignment: .top)],
                      alignment: .leading, spacing: HubSpace.m) {
                ForEach(plan.presets) { preset in setupPresetCard(preset) }
            }
        }
    }

    private func setupPresetCard(_ preset: NativePreset) -> some View {
        // Before a click, the Mac's own preset is the one the Install button acts on, so it wears
        // the selected edge too.
        let chosen = setupSelectedPreset == preset.id
            || (setupSelectedPreset == nil && workspace.installPlan?.starting?.id == preset.id)
        return Button {
            setupSelectedPreset = preset.id
        } label: {
            VStack(alignment: .leading, spacing: HubSpace.s) {
                HStack(alignment: .firstTextBaseline) {
                    Text(preset.name).font(HubType.rowTitle)
                    Spacer(minLength: HubSpace.s)
                    if preset.complete {
                        StatusBadge(text: "Installed", status: .ok, symbol: "checkmark.circle.fill")
                    } else if preset.installedCount > 0 {
                        StatusBadge(text: "\(preset.installedCount) of \(preset.packageCount)",
                                    status: .warn, symbol: "circle.dashed")
                    }
                    if chosen { Image(systemName: "checkmark.circle.fill").foregroundStyle(.tint) }
                }
                if let summary = preset.summary {
                    Text(summary).font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(setupPresetCost(preset)).font(HubType.caption)
                    .foregroundStyle(preset.refused == nil ? .secondary : HubStatus.warn.textColor)
                    .fixedSize(horizontal: false, vertical: true)
                if let refused = preset.refused {
                    Text(refused).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if preset.fitsThisMac == false {
                    Text("Sized for a bigger Mac than this one; its models are checked against this Mac's memory individually.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else if preset.fitsThisMac == nil {
                    Text("The Hub did not say whether this preset fits this Mac; its models are checked individually.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(HubSpace.l)
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: HubRadius.card))
            .overlay(RoundedRectangle(cornerRadius: HubRadius.card)
                .stroke(chosen ? Color.accentColor : Color(nsColor: .separatorColor), lineWidth: chosen ? 2 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(preset.name), \(preset.installedCount) of \(preset.packageCount) installed")
        .contextMenu {
            Button(preset.complete ? "Re-check Files" : "Install \(preset.name)") {
                Task { await workspace.installPreset(preset) }
            }
            .disabled(!connected || preset.refused != nil)
        }
    }

    /// What a preset will copy, in the Hub's own numbers. The sentence names the disk state the Hub
    /// measured, so "62 GB to copy" and "380 GB free after" are one claim, not two guesses.
    private func setupPresetCost(_ preset: NativePreset) -> String {
        if preset.complete { return "Every model in this preset is installed and verified." }
        // A refused preset's own sentence is the authoritative number; a second computed
        // "copies X GB" beside it showed two different sizes for the same copy.
        if preset.refused != nil { return "This preset does not fit on the models' drive right now." }
        var parts: [String] = []
        parts.append(preset.installedCount == 0
                     ? "Copies \(Self.gigabytes(preset.toCopyBytes))"
                     : "\(preset.installedCount) of \(preset.packageCount) installed; copies \(Self.gigabytes(preset.toCopyBytes)) more")
        if let after = preset.freeAfterBytes {
            parts.append("\(Self.gigabytes(after)) free after")
        }
        return parts.joined(separator: " · ")
    }

    /// Everything outside the four presets, grouped by the job it does - and every model is here,
    /// including the ones too big for this Mac, greyed out with their floor and this Mac's memory.
    private func setupManual(_ plan: NativeInstallPlan) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(alignment: .firstTextBaseline) {
                sectionTitle("Every other model")
                Spacer(minLength: HubSpace.m)
                TextField("Search models", text: $setupSearch).textFieldStyle(.roundedBorder).frame(maxWidth: 260)
                    .onExitCommand { setupSearch = "" }
            }
            ForEach(plan.groups) { group in
                let rows = group.models.filter { setupSearch.isEmpty || $0.label.localizedCaseInsensitiveContains(setupSearch) || $0.id.localizedCaseInsensitiveContains(setupSearch) }
                if !rows.isEmpty {
                    VStack(alignment: .leading, spacing: HubSpace.s) {
                        Text(group.title).font(HubType.section)
                        HubCard(padding: 0) {
                            VStack(spacing: 0) {
                                ForEach(rows) { model in setupModelRow(model) }
                            }
                        }
                    }
                }
            }
        }
    }

    private func setupModelRow(_ model: NativeInstallModel) -> some View {
        HStack(alignment: .top, spacing: HubSpace.m) {
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                HStack(spacing: HubSpace.s) {
                    Text(model.label).font(HubType.rowTitle)
                    if let quantization = model.quantization {
                        // The standing rule: every mention of a model names its quantization.
                        Text(quantization).font(HubType.caption).foregroundStyle(.secondary)
                    }
                }
                Text(model.purpose).font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: HubSpace.s) {
                    if let bytes = model.bytes { Text(Self.gigabytes(bytes)).font(HubType.caption).foregroundStyle(.tertiary) }
                    if let floor = model.floorGB { Text("needs \(floor) GB").font(HubType.caption).foregroundStyle(.tertiary) }
                    if !model.sources.isEmpty {
                        Text(model.sources.compactMap { NativeInstallSource(rawValue: $0)?.text }.joined(separator: ", "))
                            .font(HubType.caption).foregroundStyle(.tertiary)
                    }
                }
                if let reason = model.reason {
                    Text(reason).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: HubSpace.m)
            if workspace.installBusy.contains(model.id) {
                ProgressView().controlSize(.small)
            } else if model.installed {
                Button("Remove") { Task { await workspace.removePackage(model.id) } }
                    .hubRowControl().disabled(!connected)
                    .help("Move it into the model library's trash. Nothing is deleted.")
                StatusBadge(text: "Installed", status: .ok)
            } else {
                Button("Install") { Task { await workspace.installPackage(model.id) } }
                    .hubRowControl().disabled(!connected || model.refusedHere || model.sources.isEmpty)
                    .help(model.refusedHere ? (model.reason ?? "") : "Copy it onto this Mac")
                if model.refusedHere { StatusBadge(text: "Too big here", status: .off) }
            }
        }
        .padding(.horizontal, HubSpace.l).padding(.vertical, HubSpace.m)
        .opacity(model.refusedHere ? 0.55 : 1)
        .id(model.id)
    }

    /// "11 models · 6 ready · 1 downloading" — the library's state, not a description of the page.
    private var modelsStatus: PageStatusLine {
        guard localServicesReady else {
            return PageStatusLine("Local services are stopped — start them to read the model library.", tone: .warn)
        }
        let installed = workspace.models.filter(\.cached).count
        let ready = workspace.models.filter { $0.cached && $0.supported && $0.runtimeReady && !$0.paused }.count
        let downloading = activeDownloads.count
        var parts = ["\(installed) of \(workspace.models.count) models in the library",
                     "\(ready) ready"]
        if downloading > 0 { parts.append("\(downloading) downloading") }
        return PageStatusLine(parts.joined(separator: " · "),
                              tone: downloading > 0 ? .info : (ready > 0 ? .ok : .warn))
    }

    private var modelsWorkspace: some View {
        PageScaffold(title: pageTitle(.models),
                     status: modelsStatus,
                     primary: PagePrimaryAction("Install Models…",
                                               isEnabled: connected,
                                               help: "Choose a preset or tick models to install") { studio.select(.setup) },
                     menu: [
                        PageMenuAction("Verify All", symbol: "checkmark.seal",
                                       isEnabled: connected && !workspace.updatingModels) {
                            Task { await workspace.verifyInstalledModels() }
                        },
                        PageMenuAction("Refresh Library", symbol: "arrow.clockwise",
                                       isEnabled: connected && !workspace.refreshing) {
                            Task { await workspace.refresh() }
                        },
                        PageMenuAction("Show in Finder", symbol: "folder",
                                       isEnabled: studio.hasLibrary) { _ = studio.revealLibrary() },
                     ]) {
            if !connected { setup }
            HStack(spacing: HubSpace.m) {
                TextField("Search models", text: $modelSearch).textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                    .onExitCommand { modelSearch = "" }
                Toggle("Installed only", isOn: $installedOnly).toggleStyle(.checkbox).fixedSize()
                Spacer(minLength: HubSpace.xl)
            }

            // One tab per function, each with the count this view would show. This replaced a
            // "Jump to" picker over one long stack of sections — the owner's report was "it has no
            // tab or proper spacing. it's squished", and a heading every few rows is not a tab.
            modelTabs

            // Where the models actually live is a diagnostic, not a headline: one summary line, the
            // whole path on demand. "Where did my model folder go" is still answered here.
            DiagnosticDetails(title: "Library", summary: "where models are stored") {
                DiagnosticValue(label: "Folder", value: studio.configuration.modelsDirectory?.path)
                if studio.hasLibrary, !studio.libraryFolderExists {
                    Text("The folder is not on this Mac right now — reconnect the drive or choose another library in Settings.")
                        .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: 560, alignment: .leading)

            // Downloads sit above the list as one compact strip: a section below it took height
            // from the catalogue and left the last visible row cut in half (critic, 2026-11-13).
            if !activeDownloads.isEmpty {
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    ForEach(activeDownloads.prefix(3)) { download in
                        HStack(spacing: HubSpace.m) {
                            Image(systemName: "arrow.down.circle").foregroundStyle(HubStatus.info.color)
                            Text(modelName(download.repo)).font(HubType.rowTitle).lineLimit(1)
                            StatusBadge(text: "Downloading \(Int(min(100, max(0, download.percent))))%",
                                        status: .info, symbol: "arrow.down.circle")
                            ProgressView(value: min(100, max(0, download.percent)), total: 100)
                                .frame(maxWidth: 220)
                            Text(download.totalBytes > 0
                                 ? "\(ByteCountFormatter.string(fromByteCount: download.downloadedBytes, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: download.totalBytes, countStyle: .file))"
                                 : sizeCaption(for: download))
                                .font(HubType.caption).foregroundStyle(.secondary)
                            Spacer(minLength: 0)
                            Button("Cancel") { Task { await workspace.cancelDownload(download) } }
                                .hubRowControl().disabled(!connected)
                        }
                    }
                }
                .padding(HubSpace.m)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: HubRadius.card))
                .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
            }

            // What is actually on disk, per family, with the memory the model declares. This matters
            // most for models copied in from another install: they are present and would otherwise
            // look runnable on a Mac that cannot hold them.
            if !workspace.storageFamilies.isEmpty {
                let tooBig = workspace.storageFamilies.filter { $0.runsHere == false }
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    HStack(spacing: HubSpace.s) {
                        Text("Storage cache").font(HubType.rowTitle)
                        Text("\(workspace.storageFamilies.count) families on disk")
                            .font(HubType.caption).foregroundStyle(.secondary)
                        if !tooBig.isEmpty {
                            Text("· \(tooBig.count) cannot run on this Mac")
                                .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        }
                    }
                    ForEach(tooBig) { family in
                        Label("\(family.label) needs \(family.requiredGB ?? 0) GB; this Mac has \(family.thisMacGB ?? 0) GB",
                              systemImage: "exclamationmark.triangle")
                            .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                    }
                }
            }

            // Above the list, not below the split: a flexible split pushes anything after it to the
            // foot of the window, where a line about the library reads as a stray footer.
            if let note = workspace.modelNote, workspace.modelNoteRepo == nil {
                Text(note).font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if workspace.refreshing && workspace.models.isEmpty { ProgressView("Reading model library…").controlSize(.small) }
            // With nothing in the library at all, the sentence is about the services; with a
            // library but nothing matching, it is about the tabs, the search and "Installed only".
            else if workspace.models.isEmpty { Text(connected ? "No models match this view. Catalogs for the remaining engines will appear when those engines are integrated." : "Start the local services to inspect the model library.").font(HubType.secondary).foregroundStyle(.secondary) }
            else if modelSectionsToShow.isEmpty {
                // Two different nothings. A Studio whose catalogue lists no model at all is a fact
                // about the Hub, and "clear the search" would point at a control that changes
                // nothing; that tab exists only because the operator arrived from that Studio.
                if let chosen = StudioFunction(rawValue: modelFilter), !modelLibraryHolds(chosen) {
                    HStack(spacing: HubSpace.m) {
                        Text("\(StudioPage.page(for: chosen)?.title ?? chosen.rawValue) has no model in this library yet. It will appear here when the Hub offers one.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                        Button("Set Up This Mac") { studio.select(.setup) }.hubRowControl()
                        Spacer()
                    }
                } else {
                    HStack(spacing: HubSpace.m) {
                        Text("No models match this view.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                        Button("Show the Whole Library") { modelSearch = ""; installedOnly = false; modelFilter = "All functions" }
                            .hubRowControl()
                        Spacer()
                    }
                }
            }
            HStack(spacing: 0) {
                // A selected model takes the whole page on a narrow window; the 380 pt list plus
                // the 320 pt detail pane cannot both fit beside the sidebar (critic, 2026-11-13).
                if windowWidth >= 1000 || selectedModel == nil {
                List(selection: $selectedModel) {
                    // Under "All" the headings are the Studio list itself, so a Studio the Hub
                    // offers a model for is named without an edit here. Inside a function's own
                    // tab the tab is the heading, and a second one would only repeat it.
                    ForEach(modelSectionsToShow) { section in
                        Section {
                            if isModelSectionExpanded(section.function) {
                                ForEach(section.models) { model in modelListRow(model) }
                            }
                        } header: {
                            if modelFilter == "All functions" { modelSectionHeader(section) }
                        }
                    }
                }
                .listStyle(.plain)
                // With nothing selected the list takes the whole width rather than sitting in its
                // remembered column beside an empty panel. Once a model is picked it goes back to
                // that column, which is what the divider drags and what is remembered.
                .frame(width: selectedModel == nil ? nil : CGFloat(modelsListWidth))
                }
                // Nothing selected, nothing to inspect: the list keeps the whole width rather than
                // leaving a panel that says only "select a model" across a third of the page.
                if let model = workspace.models.first(where: { $0.repo == selectedModel }) {
                    if windowWidth >= 1000 || selectedModel == nil { modelSplitDivider }
                    ScrollView {
                        let chip = modelStateChip(model)
                        VStack(alignment: .leading, spacing: HubSpace.l) {
                            if windowWidth < 1000 {
                                Button { selectedModel = nil } label: {
                                    Label("All Models", systemImage: "chevron.left")
                                }
                                .hubRowControl()
                                .accessibilityLabel("Back to all models")
                            }
                            HStack(alignment: .firstTextBaseline, spacing: HubSpace.m) {
                                Text(model.label).font(HubType.section).textSelection(.enabled)
                                StatusBadge(text: chip.text, status: chip.status, symbol: chip.symbol)
                                Spacer(minLength: 0)
                            }
                            VStack(alignment: .leading, spacing: HubSpace.xs) {
                                ValueRow("Studio", value: StudioPage.page(for: model.function)?.title ?? model.function.rawValue)
                                if let size = model.sizeGB { ValueRow("Size", value: String(format: "%.2f GB", size)) }
                            }
                            DiagnosticDetails(title: "Model details", summary: model.repo) {
                                DiagnosticValue(label: "Repository", value: model.repo)
                                if let size = model.sizeGB {
                                    DiagnosticValue(label: "Size on disk", value: String(format: "%.2f GB", size))
                                }
                                DiagnosticValue(label: "Files", value: model.cached ? "verified library copy" : "not installed")
                            }
                            // Every fact this model publishes, per generator, with its provenance:
                            // the lane's operation and every parameter with its enforced bound, the
                            // size table with its resolution tiers, the long-form section policy,
                            // model limits — whatever the producers state, and nothing they do not.
                            modelFactsBlock(model)
                            modelLongFormBlock(model)
                            // The one admission number the operator may change: the free memory a
                            // job must find before this model starts, whose number it is, and the
                            // way back to the model's own figure.
                            modelFreeMemoryBlock(model)
                            // And, under it, the controls the operator may set a default for, built
                            // from the same lane contract.
                            modelDefaultsEditor(model)
                            Divider()
                            // The Models page is where an operator comes to find out why something
                            // will not run, so the gate that is shut is named here as well.
                            if let reason = model.blockReason {
                                Label(reason, systemImage: "exclamationmark.triangle")
                                    .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                                    .fixedSize(horizontal: false, vertical: true)
                            } else {
                                Text(model.supportNote).font(HubType.secondary).foregroundStyle(.secondary)
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
                                        .font(HubType.caption).foregroundStyle(.secondary)
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
                                        .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
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
                                        .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                        .fixedSize(horizontal: false, vertical: true)
                                } else if !model.supported && workspace.modelPackagesUnread {
                                    // No package row exists at all, so "unsupported" here is the
                                    // missing read, not this build's form: the catalog sentence
                                    // below would send the operator to look for a fault that is
                                    // not there.
                                    Text("Couldn't read the Hub's model list — retrying. Download stays unavailable until it answers.")
                                        .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                                        .fixedSize(horizontal: false, vertical: true)
                                } else if !model.supported {
                                    Text("This model's runtime entry in the catalog does not match what this form can drive, so it cannot be downloaded here yet.")
                                        .font(HubType.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            // Only a note about this model is shown here; a library-wide
                            // summary would otherwise be repeated under every model as if it
                            // were that model's result.
                            if let note = workspace.modelNote,
                               workspace.modelNoteRepo == nil || workspace.modelNoteRepo == model.repo {
                                if workspace.modelNoteRepo == model.repo {
                                    Text(note).font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        }.padding(HubSpace.l)
                    }.frame(minWidth: 320, maxWidth: .infinity)
                }
            }
        }
        .confirmationDialog(
            "Remove downloaded model?",
            isPresented: Binding(get: { pendingModelRemoval != nil },
                                 set: { if !$0 { pendingModelRemoval = nil } }),
            titleVisibility: .visible,
            presenting: pendingModelRemoval) { model in
                Button("Remove Download", role: .destructive) {
                    pendingModelRemoval = nil
                    Task { await workspace.removeModel(model) }
                }
                Button("Keep Model", role: .cancel) { pendingModelRemoval = nil }
            } message: { model in
                Text("Move \(model.label) into this library's trash. Nothing is deleted: it can be restored for 30 days, then it goes to the Mac's Trash. Generated files are kept. A model a job is using, or one another installed model needs, is refused and says which.")
            }
        // The defaults are read when this tab appears, once per Studio and once per connection: it
        // is one request each, and nothing on the two-second poll depends on it. Keyed on the
        // connection, so a reconnect reads the new Hub's answers rather than showing the last one's.
        .task(id: modelDefaultsLoadKey) { await workspace.loadModelDefaultsIfNeeded() }
        // A draft belongs to the model it was typed for, and to the answer it was typed against.
        .onChange(of: selectedModel) { _, _ in modelDefaultDraft = [:] }
        .onChange(of: workspace.modelDefaultsRevision) { _, _ in modelDefaultDraft = [:] }
    }

    // MARK: - Renders (owner, 2026-10-26)

    /// The final videos this Hub rendered for Story Studio.
    ///
    /// The owner, 2026-10-26: *"there's no render tab so there's no way to see the resulting video
    /// and how many has been processed, and the duration records etc..."* Every field here is one
    /// the Hub's render lane already recorded; nothing is computed on this page and nothing is
    /// named that the packet did not name. Story Studio now sends the channel and episode with the
    /// request as **display only** (it decides no routing or acceptance), so a row is titled with
    /// them; a render it sent no name for is still titled by its job id, and a name is never
    /// guessed.
    private var rendersWorkspace: some View {
        PageScaffold(title: pageTitle(.renders),
                     status: rendersStatus,
                     scrolls: true,
                     // The list is read when the page opens, not on the poll: a render finishing
                     // while the page is open is one click away rather than two seconds away.
                     // The grant is read with it: the owner changes the grant on the Gateway,
                     // and this action is the one way to see the new answer here.
                     primary: PagePrimaryAction("Refresh Renders", isEnabled: connected) {
                        Task { await workspace.refreshRenders(); await workspace.refreshRenderAuthority() }
                     }) {
            renderGrantLine
            if let history = workspace.renderHistory {
                renderTotals(history)
                if history.renders.isEmpty {
                    HubCard {
                        EmptyState(
                            title: "No renders recorded yet",
                            detail: "A final render appears here the moment this Hub finishes one. Renders made before the build that added this page are not listed, because nothing recorded them. From here on this page lists the last " +
                                "\(history.retentionDays ?? 90) days.",
                            symbol: "play.rectangle.on.rectangle",
                            actionTitle: "Refresh",
                            action: {
                                Task { await workspace.refreshRenders(); await workspace.refreshRenderAuthority() }
                            })
                    }
                } else {
                    VStack(alignment: .leading, spacing: HubSpace.m) {
                        sectionTitle("Renders")
                        // Lazy so a long history does not build every row before the first is drawn.
                        LazyVStack(alignment: .leading, spacing: HubSpace.m) {
                            ForEach(history.renders) { render in
                                renderRow(render)
                                Divider()
                            }
                        }
                    }
                }
            } else if let problem = workspace.renderHistoryError {
                HubBanner(status: .warn, symbol: "exclamationmark.triangle.fill",
                          title: "This Hub cannot list its renders",
                          message: problem)
            } else {
                HStack(spacing: HubSpace.m) {
                    ProgressView().controlSize(.small)
                    Text("Reading this Hub's renders…").font(HubType.secondary).foregroundStyle(.secondary)
                }
            }
        }
        // Read when the page opens and when the connection changes, not on the two-second poll: the
        // list changes only when a render finishes, and on a controller the read asks every Mac.
        .task(id: connected) { if connected { await workspace.refreshRenders() } }
        .sheet(item: $openRender) { render in
            RenderDetailSheet(render: render) { openRender = nil }
        }
    }

    /// What the render list holds, or why it does not hold anything yet.
    private var rendersStatus: PageStatusLine {
        guard localServicesReady else {
            return PageStatusLine("Local services are stopped — start them to read this Hub's renders.", tone: .warn)
        }
        if let history = workspace.renderHistory {
            let count = history.renders.count
            let failed = history.totals.failed
            return PageStatusLine(count == 0
                                  ? "No final renders yet — videos Story Studio renders here appear automatically."
                                  : "\(count) final \(count == 1 ? "render" : "renders") · newest first · last \(history.retentionDays ?? 90) days"
                                    + (failed > 0 ? " · \(failed) failed" : ""),
                                  tone: failed > 0 ? .warn : (count == 0 ? .off : .ok))
        }
        if let problem = workspace.renderHistoryError {
            return PageStatusLine(problem, tone: .warn)
        }
        return PageStatusLine("Reading this Hub's renders…", tone: .off)
    }

    /// The owner's render grant on this Mac, in one plain line, with every exact tuple it covers.
    ///
    /// Read only — the app never changes the grant. A grant the Hub did not answer about says
    /// so; it never reads as a working one, and a grant that is on but cannot act on this Mac
    /// says that too rather than being softened into "On".
    private var renderGrantLine: some View {
        VStack(alignment: .leading, spacing: HubSpace.xs) {
            if let authority = workspace.renderAuthority {
                // The grant's internal version ("v2") is not something a person acts on.
                Text(authority.grantLine.replacingOccurrences(of: " \\(v[0-9]+\\)", with: "",
                                                             options: .regularExpression))
                    .font(HubType.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                let tuples = authority.effectiveTuples
                if tuples.isEmpty {
                    Text("No encode tuple was published for this grant.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    // Encoder settings are diagnostics: one summary, every tuple on demand.
                    DiagnosticDetails(title: "Encode details",
                                      summary: tuples.first?.line ?? "published tuples") {
                        ForEach(tuples.indices, id: \.self) { index in
                            Text(tuples[index].line)
                                .font(HubType.monoCaption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            } else if let problem = workspace.renderAuthorityError {
                Text("Render grant: not read — \(problem)")
                    .font(HubType.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Reading this Mac's render grant…")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            }
        }
        .task(id: connected) { if connected { await workspace.refreshRenderAuthority() } }
    }

    /// The four numbers the owner asked for, each either measured or absent — never a 0 standing in
    /// for "the Hub does not know".
    private func renderTotals(_ history: NativeRenderHistory) -> some View {
        let totals = history.totals
        var counts = ["\(totals.succeeded) done", "\(totals.failed) failed"]
        if totals.running > 0 { counts.append("\(totals.running) running") }
        if totals.cancelled > 0 { counts.append("\(totals.cancelled) cancelled") }
        // The same formatter every other duration in the app uses: one duration voice, and a
        // total is a duration too.
        let output = totals.outputSeconds.map { NativeJob.clock($0) }
            ?? (totals.succeeded == 0 ? "none yet" : "unknown")
        return HubCard {
            VStack(alignment: .leading, spacing: HubSpace.m) {
                // Four columns wide, 2x2 in a narrow window: a value split mid-phrase
                // ("6 min / 46 s") is not a metric.
                if windowWidth >= 1000 {
                    HStack(alignment: .top, spacing: HubSpace.xl) { renderStats(totals, counts: counts, output: output) }
                } else {
                    LazyVGrid(columns: [GridItem(.flexible(), alignment: .topLeading),
                                                 GridItem(.flexible(), alignment: .topLeading)],
                              alignment: .leading, spacing: HubSpace.m) {
                        renderStats(totals, counts: counts, output: output)
                    }
                }
                // Only where the list came from more than one Mac: a node's page is its own.
                if let machines = history.machines, machines.count > 1 {
                    Divider()
                    HStack(alignment: .firstTextBaseline, spacing: HubSpace.m) {
                        Text(machines.map { machine in
                            let name = machine.machineName ?? (machine.machine == "local" ? "This Mac" : machine.machine)
                            return machine.ok ? "\(name) · \(machine.renders)" : "\(name) did not answer"
                        }.joined(separator: "   ·   "))
                            .font(HubType.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        if machines.contains(where: { !$0.ok }) {
                            Button("Ask Again") { Task { await workspace.refreshRenders() } }
                                .hubRowControl()
                        }
                        Spacer()
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func renderStats(_ totals: NativeRenderTotals, counts: [String], output: String) -> some View {
        renderStat("Renders", "\(totals.processed)", counts.joined(separator: " · "))
        renderStat("Output video", output,
                   totals.unknownDuration > 0
                     ? "\(totals.unknownDuration) with no recorded length"
                     : "of finished video")
        renderStat("Average render",
                   totals.averageWallSeconds.map { NativeJob.clock($0) } ?? "not measured",
                   totals.measuredRenders > 0
                     ? "over \(totals.measuredRenders) timed render\(totals.measuredRenders == 1 ? "" : "s")"
                     : "no render timed yet")
        renderStat("Speed",
                   totals.speed.map { String(format: "%.1f× real time", $0) } ?? "not measured",
                   "video seconds per second of rendering")
    }

    private func renderStat(_ title: String, _ value: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.xs) {
            Text(title).font(HubType.caption).foregroundStyle(.secondary)
            Text(value).font(HubType.figure).lineLimit(1).minimumScaleFactor(0.75)
            Text(detail).font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func renderRow(_ render: NativeRender) -> some View {
        ActionRow {
            Image(systemName: RenderCopy.symbol(render))
                .foregroundStyle(RenderCopy.status(render).color)
            VStack(alignment: .leading, spacing: HubSpace.tight) {
                Text(RenderCopy.title(render)).font(HubType.rowTitle).lineLimit(1)
                Text(RenderCopy.facts(render)).font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let failure = render.failure {
                    // A cancelled render's note is a fact, not an alarm: the row's own status
                    // colour says which it is.
                    Text(failure).font(HubType.caption)
                        .foregroundStyle(RenderCopy.status(render) == .bad
                                         ? HubStatus.bad.textColor : Color.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if render.outcome == "failed" {
                        Text("Story Studio owns the retry for this episode; this Hub keeps the failure.")
                            .font(HubType.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            RowActions {
                StatusBadge(text: RenderCopy.outcomeLabel(render), status: RenderCopy.status(render))
                Button("Details") { openRender = render }.hubRowControl()
            }
        }
    }

    /// Every job the Jobs page's current tab and Mac filter select, across the Studios that have
    /// a batch history, newest first as each Studio's own list is.
    private var jobsInScope: [(function: StudioFunction, job: NativeJob)] {
        workspace.jobHistories
            .filter { historyTab == .all || historyTab.studio == $0.function }
            .flatMap { history in
                history.jobs.filter(jobMachineFilter.includes)
                    .filter(jobMatchesSearch)
                    .map { (history.function, $0) }
            }
    }

    /// How many jobs each Mac filter would list, so the filter says what it hides.
    private var jobMachineCounts: [JobMachineFilter: Int] {
        let all = workspace.jobHistories.flatMap(\.jobs)
        return Dictionary(uniqueKeysWithValues: JobMachineFilter.allCases.map {
            ($0, all.filter($0.includes).count)
        })
    }

    /// The Jobs page's one search: the prompt, the model's label, its repo, or a failure's words.
    private func jobMatchesSearch(_ job: NativeJob) -> Bool {
        let query = jobSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return true }
        return job.text.localizedCaseInsensitiveContains(query)
            || modelName(job.model).localizedCaseInsensitiveContains(query)
            || job.model.localizedCaseInsensitiveContains(query)
            || (job.error ?? "").localizedCaseInsensitiveContains(query)
    }

    /// Everything this Hub has run, by state: the page's one line, whatever tab is open.
    private var jobsStatus: PageStatusLine {
        guard localServicesReady else {
            return PageStatusLine("Local services are stopped — start them to see this Mac's work.", tone: .warn)
        }
        let all = workspace.jobHistories.flatMap(\.jobs)
        let running = all.filter { JobFacts.stage($0.state) == .running }.count
        let waiting = all.filter { JobFacts.stage($0.state) == .waiting }.count
        // "Completed" is done, not "everything that is not running": a failed or cancelled job
        // was counted as completed before (critic, 2026-11-13).
        let completed = all.filter { $0.state == "done" }.count
        let failed = all.filter { $0.state == "error" }.count
        let attention = all.filter { $0.state == "uncertain" }.count
        let downloads = workspace.downloads.filter { active($0.state) }.count
        // The total is the same number the All Mac filter carries; downloads are listed apart
        // because they are a tab of their own, not a job.
        var parts = ["\(all.count) jobs", "\(running) running", "\(waiting) queued"]
        if completed > 0 { parts.append("\(completed) completed") }
        if failed > 0 { parts.append("\(failed) failed") }
        if attention > 0 { parts.append(attentionPhrase(attention)) }
        if downloads > 0 { parts.append("\(downloads) downloading") }
        return PageStatusLine(parts.joined(separator: " · "),
                              tone: (failed > 0 || attention > 0 || waiting > 0) ? .warn
                                : (running > 0 ? .ok : .off))
    }

    private var jobsWorkspace: some View {
        PageScaffold(title: pageTitle(.jobs), status: jobsStatus, scrolls: true) {
                // The tabs keep their order and their counts; at a narrow window they drop to the
                // small control instead of scrolling sideways off the page.
                HubSegmentedControl(
                    selection: $historyTab,
                    items: HistoryTab.allCases.map { tab in
                        let running = workspace.downloads.filter { active($0.state) }.count
                        // Every filter carries its count at a width that fits; a narrow window
                        // keeps the short labels so the row does not have to scroll.
                        let jobs = workspace.jobHistories.flatMap(\.jobs)
                        let count: Int? = {
                            if tab == .all { return jobs.count }
                            if tab == .downloads { return running > 0 ? running : nil }
                            guard let studio = tab.studio else { return nil }
                            return workspace.jobHistories.first { $0.function == studio }?.jobs.count
                        }()
                        let label = (windowWidth >= 980 ? count.map { "\(tab.title) \($0)" } : nil)
                            ?? (tab == .downloads && running > 0 ? "\(tab.title) (\(running))" : tab.title)
                        return HubSegmentItem(
                            tag: tab,
                            label: label,
                            symbol: windowWidth < 980 ? nil
                                : (tab == .all ? "square.grid.2x2"
                                   : (tab == .downloads ? "arrow.down.circle" : tab.studio?.symbol))
                        )
                    },
                    // Wide windows get equal tabs and glyphs; a narrow one gets each tab at its own
                    // width, which is what keeps all eight on screen without a sideways scroll.
                    sizing: windowWidth < 980 ? .stablePerLabel : .equalWidest,
                    controlSize: windowWidth < 980 ? .small : .regular,
                    accessibilityLabel: "History filter"
                )
                .accessibilityLabel("History filter")
                // The controller is the only Mac whose Jobs page holds work that ran somewhere
                // else. A node's list is its own machine throughout, so a split there would be
                // three names for one list.
                if workspace.controllerIdentity?.settings.role == "controller" {
                    machineFilterPicker($jobMachineFilter, counts: jobMachineCounts)
                }
                HStack(spacing: HubSpace.s) {
                    TextField("Search jobs", text: $jobSearch)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                        .onExitCommand { jobSearch = "" }
                    if !jobSearch.isEmpty {
                        Button("Clear") { jobSearch = "" }.hubRowControl()
                    }
                    Spacer(minLength: 0)
                }
                if workspace.transcribing { transcriptionActivity }
                // A tab for a Studio the Hub files nothing for is not nothing to show: it is the
                // one place the owner asked to be reminded from. It says exactly that - no rows, no
                // controls and no date - and which Studio it is comes from the tab, never from a
                // name written here.
                if let studio = historyTab.studio, !studio.hasBatchHistory {
                    let name = StudioPage.page(for: studio)?.title ?? studio.rawValue
                    VStack(alignment: .leading, spacing: HubSpace.m) {
                    sectionTitle(name)
                    HStack(alignment: .top, spacing: HubSpace.m) {
                        Image(systemName: studio.symbol).font(HubType.icon).foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: HubSpace.xs) {
                            Text("\(name) Studio is not in this build yet.").font(HubType.rowTitle)
                            Text("When it ships, every finished clip will be listed here, newest first, with the model that made it, its length and how long it took. Until then this tab lists nothing.")
                                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    }
                }
                // **Running work first** (owner: *"no status of running jobs here either"*). The
                // page used to open on finished Audio rows because the Studios are listed in their
                // sidebar order; live work was there, further down. One group per state, each row
                // drawn by its own Studio, honouring the tab and the Mac filter.
                let running = jobsInScope.filter { JobFacts.stage($0.job.state) == .running }
                let waiting = jobsInScope.filter { JobFacts.stage($0.job.state) == .waiting }
                // Failures and uncertain attempts first: they are the work that needs a person,
                // and they used to sit inside their kind's finished list.
                let needsYou = jobsInScope.filter { row in
                    JobFacts.stage(row.job.state) == .finished
                        && (row.job.state == "error" || row.job.state == "uncertain")
                }
                if !running.isEmpty {
                    VStack(alignment: .leading, spacing: HubSpace.m) {
                        sectionTitle("Running now")
                        LazyVStack(alignment: .leading, spacing: HubSpace.m) {
                            ForEach(Array(running.enumerated()), id: \.offset) { index, row in
                                jobRow(row.job, for: row.function)
                                if index < running.count - 1 { Divider() }
                            }
                        }
                    }
                }
                if !needsYou.isEmpty {
                    VStack(alignment: .leading, spacing: HubSpace.m) {
                        sectionTitle("Needs you")
                        LazyVStack(alignment: .leading, spacing: HubSpace.m) {
                            ForEach(Array(needsYou.enumerated()), id: \.offset) { index, row in
                                jobRow(row.job, for: row.function)
                                if index < needsYou.count - 1 { Divider() }
                            }
                        }
                    }
                }
                if !waiting.isEmpty {
                    VStack(alignment: .leading, spacing: HubSpace.m) {
                        sectionTitle("Queued")
                        LazyVStack(alignment: .leading, spacing: HubSpace.m) {
                            ForEach(Array(waiting.enumerated()), id: \.offset) { index, row in
                                jobRow(row.job, for: row.function)
                                if index < waiting.count - 1 { Divider() }
                            }
                        }
                    }
                }
                // One section per Studio that has a history, derived from the Studio list rather
                // than written out here: a Studio missing from a hand-written section was simply
                // absent from Jobs with nothing saying so. Only finished work: what is running or
                // waiting is in the two groups above it.
                ForEach(workspace.jobHistories, id: \.function) { history in
                    if historyTab == .all || historyTab.studio == history.function {
                      let jobs = history.jobs.filter(jobMachineFilter.includes)
                          .filter(jobMatchesSearch)
                          .filter { !active($0.state) }
                          .filter { $0.state != "error" && $0.state != "uncertain" }
                      VStack(alignment: .leading, spacing: HubSpace.m) {
                        sectionTitle(StudioPage.page(for: history.function)?.title ?? history.function.rawValue)
                        // Only when this Studio has nothing at all: a Studio whose work is all
                        // in Running now or Queued above must not also say "no jobs yet".
                        if jobs.isEmpty && history.jobs.filter(jobMachineFilter.includes).isEmpty {
                            let source = jobMachineFilter == .thisMac ? "this Mac"
                                : (jobMachineFilter == .nodes ? "the nodes" : "this Mac or its nodes")
                            HStack(spacing: HubSpace.m) {
                                Text(jobSearch.isEmpty
                                     ? "No \((StudioPage.page(for: history.function)?.title ?? history.function.rawValue).lowercased()) jobs from \(source) yet."
                                     : "No jobs match “\(jobSearch)” here.")
                                    .font(HubType.secondary).foregroundStyle(.secondary)
                                if let page = StudioPage.page(for: history.function) {
                                    Button("Open \(page.title)") { studio.select(page) }.hubRowControl()
                                }
                                Spacer()
                            }
                        }
                        // Lazy so a row's artifact (an image thumbnail above all) is only fetched
                        // for a row that is actually on screen.
                        LazyVStack(alignment: .leading, spacing: HubSpace.m) {
                            ForEach(jobs) { job in
                                jobRow(job, for: history.function)
                                Divider()
                            }
                        }
                        if history.function == .transcription, let result = workspace.transcriptionResult {
                            HStack { VStack(alignment: .leading, spacing: HubSpace.xs) { Text("Latest transcript").font(HubType.rowTitle); Text(result.text).font(HubType.secondary).lineLimit(2).foregroundStyle(.secondary) }; Spacer(); Button("Open Subtitle") { studio.select(.transcription) }.hubRowControl() }
                        }
                      }
                    }
                }
                if historyTab == .downloads {
                  VStack(alignment: .leading, spacing: HubSpace.m) {
                    sectionTitle("Model downloads")
                    if workspace.downloads.isEmpty {
                        HStack(spacing: HubSpace.m) {
                            Text("No model downloads. Start one from Models or Set up this Mac.")
                                .font(HubType.secondary).foregroundStyle(.secondary)
                            Button("Open Models") { studio.select(.models) }.hubRowControl()
                            Spacer()
                        }
                    }
                    ForEach(workspace.downloads.filter { jobSearch.isEmpty
                        || modelName($0.repo).localizedCaseInsensitiveContains(jobSearch) }) { downloadRow($0) }
                  }
                }
        }
    }
    /// A finished transcript is text, not sound.
    ///
    /// These rows were drawn with the Audio row, which offers Play and "Save…" for a clip. A
    /// subtitle artifact is an SRT, so Play tried to play it and the app reported an audio
    /// failure for a transcript that was perfectly fine. Text gets text actions.
    private func subtitleJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            ActionRow {
                Image(systemName: active(job.state) ? "waveform" : job.state == "done"
                      ? "checkmark.circle" : "exclamationmark.circle")
                    .foregroundStyle(stateColor(job.state))
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(3)
                    jobMetaLine(job)
                    jobProgress(job)
                    if job.state == "done" {
                        Text("Transcript").font(HubType.caption).foregroundStyle(.secondary)
                    }
                }
                RowActions {
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
                        }.disabled(!connected).rowAction()
                    } else if active(job.state) {
                        Button("Cancel") { Task { await workspace.cancelSubtitle(job) } }.disabled(!connected).rowAction()
                    }
                }
            }
            if let error = job.error, !error.isEmpty {
                Text(error).font(HubType.caption).foregroundStyle(HubStatus.bad.textColor).textSelection(.enabled)
            }
        }
    }

    /// A Chat answer is text, so the row is a text row: what was asked, what came back, and the
    /// two things worth doing with an answer - keeping it and copying it.
    ///
    /// It is not drawn with the audio, image or subtitle rows: those offer Play, Preview or Open
    /// Transcript for an artifact, and a completion has no file to open. The Hub records the answer
    /// itself as the item's text, so this shows it and saves it directly.
    private func chatJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            ActionRow {
                Image(systemName: active(job.state) ? "bubble.left.and.bubble.right"
                      : job.state == "done" ? "checkmark.circle" : "exclamationmark.circle")
                    .foregroundStyle(stateColor(job.state))
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(3)
                    jobMetaLine(job)
                    // Final-attempt rates are worker measurements. Legacy completion and round-trip
                    // times stay labelled, and are never used to manufacture a rate.
                    if !job.chatMetrics.isEmpty {
                        Text(job.chatMetrics).font(HubType.caption).foregroundStyle(.secondary)
                    }
                    jobProgress(job)
                    if job.state == "done", !job.answer.isEmpty {
                        Text(job.answer)
                            .lineLimit(8)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(HubSpace.m)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color(nsColor: .controlBackgroundColor),
                                        in: RoundedRectangle(cornerRadius: HubRadius.control))
                    }
                }
                // Only a real answer has anything to copy or save. A failed attempt offers
                // neither, because there is nothing there to offer.
                if job.state == "done", !job.answer.isEmpty {
                    RowActions {
                        Button("Copy Answer") { copyAnswer(job) }.rowAction()
                        Button("Save…") { saveAnswer(job) }.rowAction()
                    }
                }
            }
            if let error = job.error, !error.isEmpty {
                Text(error).font(HubType.caption).foregroundStyle(HubStatus.bad.textColor).textSelection(.enabled)
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
        case .chat: chatJobRow(job)
        case .video: videoJobRow(job)
        }
    }

    private func jobRow(_ job: NativeJob, compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            ActionRow {
                Image(systemName: active(job.state) ? "waveform" : job.state == "done" ? "checkmark.circle" : "exclamationmark.circle")
                    .foregroundStyle(stateColor(job.state))
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(compact ? 1 : 3)
                    jobMetaLine(job)
                    jobProgress(job)
                    DiagnosticDetails(title: "Request", summary: "settings used") { jobRequestFacts(job, .audio) }
                    // What was actually produced: length, size and the size requested. An asset
                    // with no stated facts is hard to tell apart from one that came back wrong.
                    if job.state == "done", !job.assetFacts.isEmpty {
                        Text(job.assetFacts).font(HubType.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Result details: \(job.assetFacts)")
                    }
                }
                RowActions {
                    if job.state == "done" {
                        Button("Play") {
                            Task {
                                do {
                                    try await workspace.play(job, with: player)
                                    loadedAudioJobID = job.id
                                } catch { fileActionError = error.localizedDescription }
                            }
                        }.disabled(!connected).rowAction()
                        Menu {
                            Button("Save…") { saveAudio(job) }.disabled(!connected)
                            Button("Show in Folder") {
                                if !workspace.revealArtifact(job) { fileActionError = job.revealUnavailableReason }
                            }
                        } label: {
                            Image(systemName: "ellipsis")
                                .font(.system(size: 13, weight: .semibold))
                                .frame(width: 22, height: 18)
                                .contentShape(Rectangle())
                        }
                        .menuStyle(.borderlessButton)
                        .fixedSize()
                        .help("Save or show this clip")
                        .accessibilityLabel("More actions for this clip")
                    } else if job.state == "uncertain" {
                        Button("Discard…", role: .destructive) { requestDiscard(job, kind: .audio) }.disabled(!connected).rowAction()
                    } else if job.state == "error" { retryButton(job)
                    } else if active(job.state) { Button("Cancel") { Task { await workspace.cancelAudio(job) } }.disabled(!connected).rowAction() }
                }
            }
            if let error = job.error, !error.isEmpty { Text(error).font(HubType.caption).foregroundStyle(HubStatus.bad.textColor).textSelection(.enabled) }
        }
    }
    /// The finished Voice clip the shared player is holding, by id. Nil until something is played.
    private var loadedAudioJob: NativeJob? {
        guard let loadedAudioJobID else { return nil }
        return workspace.audioJobs.first { $0.id == loadedAudioJobID }
    }

    /// A conventional transport for the loaded Voice clip — drawn whether or not one is loaded.
    ///
    /// Nothing starts on its own: the row's Play loads the clip, and this controls what is loaded.
    /// The parameter is optional so the panel can reserve this control's height before the first
    /// clip is played. When it is nil the transport is inert rather than hidden, and the caption
    /// names the one thing that fills it.
    @ViewBuilder private func audioTransport(_ job: NativeJob?) -> some View {
        // Both halves of "there is something to control": a job selected *and* the player holding
        // its audio. They arrive one after the other, and a slider bound to a player with no file
        // would otherwise look live while it did nothing.
        let ready = job != nil && player.hasAudio
        VStack(spacing: HubSpace.tight) {
            HStack(spacing: HubSpace.m) {
                Button {
                    if player.playing { player.pause() } else { player.resume() }
                } label: {
                    Label(player.playing ? "Pause" : "Play",
                          systemImage: player.playing ? "pause.fill" : "play.fill")
                }
                .disabled(!ready)
                .accessibilityLabel(player.playing ? "Pause the loaded clip" : "Resume the loaded clip")
                Button {
                    player.stop()
                    loadedAudioJobID = nil
                } label: {
                    Label("Stop", systemImage: "stop.fill")
                }
                .disabled(!ready)
                Spacer()
                // The pace the operator chose, when the lane publishes one: a clip made at 1.5× is
                // a different result from one at the model's own pace, and the row could not say so.
                if let speed = job?.voiceSettings?.speed {
                    Text("speed \(NativeJob.trimmed(speed))×")
                        .font(HubType.monoCaption)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Playback pace the clip was generated at")
                }
                Text(player.timeLabel)
                    .font(HubType.monoCaption)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Elapsed and total time")
            }
            Slider(value: Binding(get: { player.position }, set: { player.seek(to: $0) }),
                   in: 0...max(player.duration, 0.001))
                .disabled(!ready)
                .accessibilityLabel("Playback position")
            // The reason the three controls above are grey, said once and only while it is true.
            if !ready {
                Text("Choose Play on a clip to load it here.")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(HubSpace.m)
        .background(Color(nsColor: .controlBackgroundColor),
                    in: RoundedRectangle(cornerRadius: HubRadius.card, style: .continuous))
    }

    private func downloadRow(_ download: NativeDownload) -> some View {
        // The same row shape as a job: text and progress in the content slot, one action in the
        // fixed actions column, so a Cancel lines up with the job Cancels above it.
        ActionRow {
            VStack(alignment: .leading, spacing: HubSpace.tight) {
                HStack(spacing: HubSpace.s) {
                    Text(modelName(download.repo)).lineLimit(1)
                    if active(download.state) {
                        StatusBadge(text: "Downloading \(Int(min(100, max(0, download.percent))))%",
                                    status: .info, symbol: "arrow.down.circle")
                    } else {
                        StatusBadge(text: stateLabel(download.state),
                                    status: download.error != nil ? .bad : .ok)
                    }
                }
                if active(download.state) {
                    if download.percent > 0 || download.totalBytes > 0 {
                        ProgressView(value: min(100, max(0, download.percent)), total: 100)
                        if download.totalBytes > 0 {
                            Text("\(ByteCountFormatter.string(fromByteCount: download.downloadedBytes, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: download.totalBytes, countStyle: .file))")
                                .font(HubType.caption).foregroundStyle(.secondary)
                        }
                    } else {
                        // No byte counts yet: the model's own size, never a bar pinned at zero.
                        HStack(spacing: HubSpace.s) {
                            ProgressView().controlSize(.small)
                            Text(sizeCaption(for: download))
                                .font(HubType.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if let error = download.error, !error.isEmpty {
                    Text(error).font(HubType.caption).foregroundStyle(HubStatus.bad.textColor).textSelection(.enabled)
                }
            }
            RowActions {
                if active(download.state) {
                    Button("Cancel") { Task { await workspace.cancelDownload(download) } }
                        .disabled(!connected).rowAction()
                } else if download.error != nil {
                    Button("Dismiss") { workspace.dismissDownload(download) }.rowAction()
                }
            }
        }
    }
    /// What is known about a download's size, without inventing progress.
    ///
    /// Before the Hub reports byte counts the honest line is the model's own size
    /// ("downloading a 12.4 GB model") rather than "0 bytes" or a bar that never moves.
    private func sizeCaption(for download: NativeDownload) -> String {
        if let size = workspace.models.first(where: { $0.repo == download.repo })?.sizeGB {
            return String(format: "Downloading in the background · %.1f GB model", size)
        }
        return "Downloading in the background"
    }

    private var transcriptionActivity: some View {
        HStack(spacing: HubSpace.m) { ProgressView().controlSize(.small); VStack(alignment: .leading, spacing: HubSpace.xs) { Text("Transcribing recording").font(HubType.rowTitle); Text("You can use other pages while this finishes.").foregroundStyle(.secondary).font(HubType.secondary) }; Spacer() }
    }

    // MARK: - Voices

    /// The saved reference clips, which is what makes a clone repeatable.
    ///
    /// The library lives in the Voice worker and predates this app; the app had a per-job
    /// reference field and no way to keep, name, edit or reuse a clip. This is that screen.
    /// Every tag in the voice library, with its count, alphabetical. A voice with no tags simply
    /// contributes none, so a library that predates tags shows no filter at all.
    private var voiceTagCounts: [(tag: String, count: Int)] {
        var counts: [String: Int] = [:]
        for voice in workspace.voices {
            for tag in voice.tagList { counts[tag, default: 0] += 1 }
        }
        return counts.keys.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
            .map { ($0, counts[$0] ?? 0) }
    }

    /// The voices the list draws: all of them, or those carrying the chosen tag.
    private var shownVoices: [NativeVoice] {
        let byTag = voiceTagFilter.map { filter in
            workspace.voices.filter { $0.tagList.contains(filter) }
        } ?? workspace.voices
        let query = voiceSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return byTag }
        return byTag.filter {
            $0.name.localizedCaseInsensitiveContains(query)
                || $0.language.localizedCaseInsensitiveContains(query)
                || $0.notes.localizedCaseInsensitiveContains(query)
        }
    }

    /// The saved reference library in one line: how many, and what they are for.
    private var voicesStatus: PageStatusLine {
        guard localServicesReady else {
            return PageStatusLine("Local services are stopped — start them to use saved voices.", tone: .warn)
        }
        let count = workspace.voices.count
        return PageStatusLine(count == 0
                              ? "No saved voices yet — add a reference clip to clone the same voice again."
                              : "\(count) saved \(count == 1 ? "voice" : "voices") · pick one when cloning",
                              tone: count == 0 ? .off : .ok)
    }

    private var voicesWorkspace: some View {
        PageScaffold(title: pageTitle(.voices),
                     status: voicesStatus,
                     primary: PagePrimaryAction("Add Voice…",
                                               isEnabled: connected && !workspace.savingVoice,
                                               help: "Save a reference clip and its transcript") {
                        voiceDraft = VoiceDraft(); showingVoiceEditor = true
                     },
                     menu: [
                        PageMenuAction("Import Folder…", symbol: "folder.badge.plus",
                                       isEnabled: connected && !workspace.savingVoice) { chooseVoiceImportFolder() },
                        PageMenuAction("Refresh Voices", symbol: "arrow.clockwise", isEnabled: connected) {
                            Task { await workspace.refreshVoices() }
                        },
                        PageMenuAction("Models", symbol: "externaldrive") { studio.select(.models) },
                     ]) {
            VStack(alignment: .leading, spacing: HubSpace.m) {
                if workspace.savingVoice {
                    HStack(spacing: HubSpace.s) {
                        ProgressView().controlSize(.small)
                        Text("Saving the voice…").font(HubType.secondary).foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: HubSpace.s) {
                    TextField("Search voices", text: $voiceSearch)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                        .onExitCommand { voiceSearch = "" }
                    if !voiceSearch.isEmpty { Button("Clear") { voiceSearch = "" }.hubRowControl() }
                    Spacer(minLength: 0)
                }
                // Tags arrive from the Gateway, which owns them; this row narrows the list and is
                // the only thing this app does with them.
                if !voiceTagCounts.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: HubSpace.xs) {
                            voiceTagButton("All tags", count: workspace.voices.count, tag: nil)
                            ForEach(voiceTagCounts, id: \.tag) { entry in
                                voiceTagButton(entry.tag, count: entry.count, tag: entry.tag)
                            }
                        }
                    }
                }
                if let note = workspace.voiceNote {
                    Text(note).font(HubType.secondary).foregroundStyle(.secondary)
                }
                if let error = workspace.voiceError {
                    HubBanner(status: .bad, symbol: "xmark.octagon.fill", message: error)
                }
            }
            if !connected {
                empty("Not connected", "Start local services to see and save voices.", symbol: "person.wave.2")
            } else if workspace.voices.isEmpty {
                empty("No saved voices", "Add a reference clip and its transcript, or import a folder you already have. A clone needs both.", symbol: "person.wave.2")
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: HubSpace.m) {
                        if shownVoices.isEmpty, let filter = voiceTagFilter {
                            HStack(spacing: HubSpace.m) {
                                Text(voiceSearch.isEmpty
                                     ? "No voice is tagged \(filter)."
                                     : "No voice matches “\(voiceSearch)”.")
                                    .font(HubType.secondary).foregroundStyle(.secondary)
                                Button("Show All Voices") { voiceTagFilter = nil; voiceSearch = "" }.hubRowControl()
                                Spacer()
                            }
                        }
                        ForEach(shownVoices) { voice in
                            voiceRow(voice)
                            Divider()
                        }
                    }
                }
            }
        }
        .task(id: connected) { if connected { await workspace.refreshVoices() } }
        .sheet(isPresented: $showingVoiceEditor) { voiceEditor }
    }

    /// One tag filter. Choosing the one already chosen clears it, so "all" is never a dead end.
    private func voiceTagButton(_ title: String, count: Int, tag: String?) -> some View {
        Button {
            voiceTagFilter = (voiceTagFilter == tag) ? nil : tag
        } label: {
            Text("\(title) (\(count))").font(HubType.caption)
        }
        .buttonStyle(.plain)
        .padding(.horizontal, HubSpace.s).padding(.vertical, 2)
        .background(voiceTagFilter == tag ? Color.accentColor.opacity(0.18) : Color.secondary.opacity(0.10),
                    in: Capsule())
        .overlay(Capsule().strokeBorder(voiceTagFilter == tag ? Color.accentColor : Color.secondary.opacity(0.25)))
        .accessibilityLabel("\(title), \(count) voices")
        .accessibilityAddTraits(voiceTagFilter == tag ? .isSelected : [])
    }

    private func voiceRow(_ voice: NativeVoice) -> some View {
        ActionRow {
            Image(systemName: voice.readyForCloning ? "waveform.circle.fill" : "exclamationmark.circle")
                .foregroundStyle(voice.readyForCloning ? HubStatus.ok.color : HubStatus.warn.color)
                .font(HubType.icon)
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                HStack(spacing: HubSpace.tight) {
                    Text(voice.name).font(HubType.rowTitle)
                    // A language reads as a word, not an ISO code ("en" -> "English"), and "Fleet"
                    // explains itself on hover (page critic, 2026-11-12).
                    Text((Locale.current.localizedString(forLanguageCode: voice.language) ?? voice.language).capitalized)
                        .font(HubType.caption).foregroundStyle(.secondary)
                    Text(voice.genderLabel).font(HubType.caption).foregroundStyle(.secondary)
                    if voice.fleetManaged {
                        StatusBadge(text: "Fleet", status: .off)
                            .help("This voice came with the fleet and can be used on this Mac.")
                    }
                    ForEach(voice.tagList, id: \.self) { tag in jobTag(tag) }
                }
                Text(voice.readyForCloning
                     ? "Transcript saved · \(voice.durationLabel)"
                     : "No transcript yet — a clone cannot align the audio without one")
                    .font(HubType.caption)
                    .foregroundStyle(voice.readyForCloning ? Color.secondary : HubStatus.warn.textColor)
                if !voice.notes.isEmpty {
                    Text(voice.notes).font(HubType.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            // One line, not a two-across block: three controls wrapped the kebab under Play, where
            // it read as part of the player (critic, 2026-11-13).
            HStack(spacing: HubSpace.s) {
                Button("Play") { Task { await playVoice(voice) } }.rowAction()
                Button("Use") {
                    Task {
                        if await workspace.useVoice(voice) { studio.select(.audio) }
                    }
                }
                .disabled(!voice.readyForCloning)
                .help("Use this voice for the next clone")
                .rowAction()
                Menu {
                    if !voice.readyForCloning {
                        Button("Add Transcript…") { voiceDraft = VoiceDraft(voice: voice); showingVoiceEditor = true }
                    }
                    Button("Edit…") { voiceDraft = VoiceDraft(voice: voice); showingVoiceEditor = true }
                    Button("Remove…", role: .destructive) { pendingVoiceDeletion = voice }
                        .disabled(workspace.savingVoice)
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 15, weight: .semibold))
                        .frame(width: 26, height: 22)
                        .contentShape(Rectangle())
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .help("Edit or remove this voice")
                .accessibilityLabel("More actions for \(voice.name)")
            }
            .layoutValue(key: RowSlotKey.self, value: .actions)
        }
        .padding(.vertical, HubSpace.s)
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
        VStack(alignment: .leading, spacing: HubSpace.l) {
            Text(voiceDraft.voice == nil ? "Add a voice" : "Edit \(voiceDraft.voice!.name)")
                .font(HubType.section)
            Text("A clone matches the clip to the transcript, so the transcript must be exactly what the clip says. A mismatch produces a wrong-sounding voice, which is why both are required.")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Form {
                TextField("Name", text: $voiceDraft.name)
                // The worker owns the vocabulary and stores what it is given, so language is free
                // text with the library's own values offered as suggestions. A fixed picker could
                // not tag a Khmer voice `km`; the short list is only what a library that has not
                // reported any language of its own falls back to.
                ValueRow("Language") {
                    HStack(spacing: HubSpace.s) {
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
                    ValueRow("Reference clip") {
                        HStack(spacing: HubSpace.s) {
                            Button("Choose Clip…") { chooseVoiceClip() }
                            Text(voiceDraft.clipPath.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "No clip chosen")
                                .font(HubType.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                        }
                    }
                }
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    HStack {
                        Text("Transcript").font(HubType.secondary)
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
                        .font(HubType.body).frame(height: 70)
                        .overlay(RoundedRectangle(cornerRadius: HubRadius.control).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                    Text("The exact words spoken in the clip, word for word.")
                        .font(HubType.caption).foregroundStyle(.secondary)
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

    /// The two choices that decide whether this Mac is ready after a restart, each with the state it is
    /// actually in - and the Gateway reads and changes the same two from the fleet. They are separate
    /// on purpose: the first opens the app, the second brings the Hub and its Studios up with it, and
    /// a Mac with only the first is open but silent to its controller.
    @ViewBuilder private var startupSettings: some View {
        Toggle("Start at login", isOn: Binding(get: { studio.loginStatus == .enabled }, set: { studio.setLogin($0) }))
            .toggleStyle(.checkbox)
        Text(studio.loginStatus == .enabled
             ? "On: Hub Studio KH opens by itself when you sign in and relaunches if it crashes. A normal Quit leaves it stopped until you open it or sign in again."
             : "Off: Hub Studio KH does not open by itself after a restart.")
            .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        Toggle("Start services on startup", isOn: Binding(
            get: { studio.configuration.startServicesAtLogin },
            set: { studio.setStartServicesAtLogin($0) }))
            .toggleStyle(.checkbox)
        Text(studio.configuration.startServicesAtLogin
             ? "On: signing in brings the Hub and its Studios up on their own, so this Mac is ready for work without anyone opening the app."
             : "Off: after a restart the app opens but the Hub and its Studios stay stopped until you press Start Services. A Mac in a fleet cannot be reached by its controller while they are stopped.")
            .font(HubType.secondary).foregroundStyle(studio.servicesWillNotAutoStart ? HubStatus.warn.textColor : Color.secondary)
            .fixedSize(horizontal: false, vertical: true)
        if studio.servicesWillNotAutoStart {
            Text("This Mac belongs to a fleet. Turn this on so it serves again after every restart.")
                .font(HubType.secondary).fixedSize(horizontal: false, vertical: true)
        }
        Text("A fleet's Gateway can also see and change both settings, and restart the services, from its Fleet page.")
            .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }

    private var settings: some View {
        PageScaffold(title: pageTitle(.settings),
                     status: localServicesReady
                        ? (workspace.workPolicy?.machineEnabled == false
                           ? PageStatusLine("Hub running · local work is paused (resume on Gateway's Fleet page)", tone: .warn)
                           : PageStatusLine("Hub running · changes save as you make them", tone: .ok))
                        : PageStatusLine("Hub stopped — start it in Startup below.", tone: .warn),
                     scrolls: true,
                     primary: localServicesReady ? nil
                        : PagePrimaryAction("Start Services", isEnabled: studio.canConfigure) { studio.start() }) {
            // Two columns where they fit: eleven sections in one column needed a full-page scroll
            // at 900 points (critic, 2026-11-13). At 768 it is one column again.
            MasonryLayout(columns: windowWidth >= 1000 ? 2 : 1, spacing: HubSpace.xl) {
            HubSection("Startup") {
                startupSettings
            }
            HubSection("Work on this Mac") {
                WorkPolicySettings(workspace: workspace, connected: connected)
            }
            HubSection("Lite Mode") {
                LiteModeSettings(workspace: workspace, connected: connected)
            }
            HubSection("Appearance") {
                HubSegmentedControl(
                    selection: $appearancePreference,
                    items: [
                        HubSegmentItem(tag: "system", label: "System"),
                        HubSegmentItem(tag: "light", label: "Light"),
                        HubSegmentItem(tag: "dark", label: "Dark"),
                    ],
                    accessibilityLabel: "Appearance"
                )
                Text("System follows macOS. Light and Dark apply to this app only, on this Mac.")
                    .font(HubType.caption).foregroundStyle(.secondary)
            }
            HubSection("General") {
                Toggle("Test machine (no models needed)", isOn: $testMachine)
                    .toggleStyle(.checkbox)
                Text("Off by default. On, Hub does not ask for a model library or present one as a blocker; services may stay stopped without an error posture.")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                ValueRow("Developer Mode", value: connected ? workspace.developerModeSetting.map {
                    $0.verified ? ($0.enabled ? "On" : "Off") : "Unverified"
                } ?? "Unavailable" : "Unavailable")
                if let note = workspace.developerModeNote ?? workspace.developerModeSetting?.reason?.detail {
                    Text(note).font(HubType.secondary).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else if connected {
                    Text("The Hub reported Developer Mode as unavailable without a reason.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                } else {
                    Text("The Hub has not answered, so it cannot say why.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                }
                Text("Closing the window keeps local jobs running. Quit stops the app and its services.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            }
            HubSection("Model library") {
                DiagnosticDetails(title: "Location", summary: "where models are stored") {
                    DiagnosticValue(label: "Folder", value: studio.configuration.modelsDirectory?.path)
                }
                if studio.hasLibrary && !studio.libraryFolderExists {
                    // A library on a drive that is not connected stays selected and is recoverable, so
                    // this names which way it failed instead of leaving a grey button with no reason.
                    Text("That folder is not on this Mac right now. Connect its drive, or choose the folder again.")
                        .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let warning = studio.libraryPrivacyWarning {
                    Text(warning).font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack {
                    Button("Choose Folder…") { studio.chooseLibrary() }.disabled(!studio.canConfigure)
                    if studio.configuration.modelsDirectory != nil {
                        // Reveals the folder the Hub is actually using, selected in Finder. It
                        // creates a folder that is not there yet, so a fresh Mac can be filled by
                        // hand - a disconnected drive still reports its own failure.
                        Button("Show in Finder") { _ = studio.revealLibrary() }
                    }
                }
                if !studio.canConfigure { Text("Stop services before changing the model folder or offline mode.").font(HubType.secondary).foregroundStyle(.secondary) }
                Toggle("Use imported models offline", isOn: Binding(get: { studio.configuration.offline }, set: { studio.setOffline($0) })).toggleStyle(.checkbox).disabled(!studio.canConfigure)
            }
            HubSection("Model downloads") {
                ValueRow("Hugging Face token", value: workspace.tokenStatus.isSet ? workspace.tokenStatus.masked : "Not configured")
                SecretInput(placeholder: "New access token", text: $hfToken).frame(maxWidth: 380)
                Text("For models that require account access. The app shows only the saved token’s masked value.").font(HubType.secondary).foregroundStyle(.secondary)
                HStack {
                    Button("Test Token") { Task { savingToken = true; defer { savingToken = false }; if let result = await workspace.testHFToken(hfToken) { tokenMessage = "Access verified" + (result.name.map { " for " + $0 } ?? "") } } }.disabled(!connected || hfToken.isEmpty || savingToken || studio.configuration.offline)
                    Button("Save Token") { Task { savingToken = true; defer { savingToken = false }; if await workspace.saveHFToken(hfToken) { hfToken = ""; tokenMessage = "Token saved" } } }.disabled(!connected || hfToken.isEmpty || savingToken)
                    if savingToken { ProgressView().controlSize(.small) }
                }
                if let tokenMessage { Text(tokenMessage).font(HubType.secondary).foregroundStyle(.secondary) }
            }
            HubSection("Local services") {
                ValueRow("Status", value: studio.status)
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
            HubSection("Model memory") {
                Text("Each Studio keeps at most one model in unified memory. This sets how long an idle model stays loaded, which matters most when several Studios share one Mac.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
                memoryControls
            }
            HubSection("Local storage") {
                Text("This counts generated output, not the model library. Finished work is cleaned up on this Mac only; anything still being written, not yet acknowledged, or in use is protected and is never deleted.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
                storageControls
            }
            HubSection("App data") {
                DiagnosticDetails(title: "Location", summary: "where settings and jobs live") {
                    DiagnosticValue(label: "Folder", value: studio.configuration.dataDirectory.path)
                }
                Button("Show App Data") { NSWorkspace.shared.open(studio.configuration.dataDirectory) }
                ValueRow("Version", value: appVersion)
            }
            }
        }
        .task { if connected { await workspace.refreshDeveloperMode(); await workspace.refreshMemoryPolicy(); await workspace.refreshStorage(); await workspace.refreshTrash() } }
        .sheet(isPresented: $showingStorageEditor) { storageEditor }
    }

    /// Editing is a sheet so the saved values stay visible behind it: the operator can see what
    /// the limits are now while deciding what to change them to.
    private var storageEditor: some View {
        VStack(alignment: .leading, spacing: HubSpace.l) {
            Text("Local storage limits").font(HubType.section)
            Text("These apply to this Mac only. Cleaned-up work moves to the Trash Folder; items that stay there longer than the handoff below, or that push the Trash Folder past its size cap, are sent to macOS Trash automatically, where you can still restore them. Protected work is never touched, so lowering the limit can leave this Mac above it.")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Form {
                DoubleField(title: "Keep at most (GB)", value: $draftGigabytes, range: 1...2000, step: 1)
                NumberField(title: "Move to Trash after (days)", value: $draftRetentionDays, range: 1...365)
                NumberField(title: "Send Trash to macOS Trash after (days)", value: $draftTrashHandoffDays, range: 1...365)
                NumberField(title: "…or the Trash Folder holds (GB)", value: $draftTrashHandoffGb, range: 1...2000)
                Toggle("Clean up automatically", isOn: $draftCleanup)
            }.formStyle(.grouped).frame(height: 260)
            // Both stages, in the operator's own words: each is whichever comes first.
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                Text("Move to Trash after \(draftRetentionDays) days or \(NativeWorkspaceStore.gigabytes(Int64(draftGigabytes * 1_000_000_000)))")
                Text("Send Trash to macOS Trash after \(draftTrashHandoffDays) days or \(draftTrashHandoffGb) GB")
            }.font(HubType.secondary).foregroundStyle(.secondary)
            // The Hub's own dry run under these limits: saving applies them straight away and the
            // background cleanup then moves finished, acknowledged output to the Trash Folder.
            if let bytes = draftWouldDelete {
                Text(bytes == 0 ? "Nothing would be moved to the Trash Folder with these limits."
                     : "Saving lets cleanup move about \(NativeWorkspaceStore.gigabytes(bytes)) of finished work to the Trash Folder.")
                    .font(HubType.secondary).foregroundStyle(bytes == 0 ? Color.secondary : HubStatus.warn.textColor)
            }
            HStack {
                Button("Cancel") { showingStorageEditor = false }
                Spacer()
                Button("Save") {
                    showingStorageEditor = false
                    Task {
                        await workspace.saveStorageLimits(
                            maxBytes: Int64(draftGigabytes * 1_000_000_000),
                            retentionDays: draftRetentionDays,
                            cleanupEnabled: draftCleanup,
                            trashHandoffDays: draftTrashHandoffDays,
                            trashHandoffGb: draftTrashHandoffGb)
                    }
                }.buttonStyle(.borderedProminent).disabled(workspace.savingStorage)
            }
        }.padding(24).frame(width: 460)
        .task(id: "\(draftGigabytes)|\(draftRetentionDays)|\(draftCleanup)") {
            draftWouldDelete = nil
            try? await Task.sleep(for: .milliseconds(400))
            guard !Task.isCancelled else { return }
            draftWouldDelete = await workspace.storageWouldDelete(
                maxBytes: Int64(draftGigabytes * 1_000_000_000), retentionDays: draftRetentionDays,
                cleanupEnabled: draftCleanup)
        }
    }
    /// What's New. Deliberately the same text the release ships as `release-notes.md`, so the
    /// screen cannot describe a build it is not part of. The file's own first heading names the
    /// page, so the scaffold's title stands in for it rather than repeating it.
    private var whatsNew: some View {
        let notes = ReleaseNotes.text().split(separator: "\n", omittingEmptySubsequences: false)
        let body = notes.drop(while: { $0.trimmingCharacters(in: .whitespaces).isEmpty
            || $0.trimmingCharacters(in: .whitespaces).hasPrefix("# ") })
        return PageScaffold(title: pageTitle(.whatsNew),
                            status: ReleaseNotes.hasNotes
                                ? PageStatusLine("Hub Studio KH \(ReleaseNotes.currentVersion) · newest first")
                                : PageStatusLine("These notes were not found inside this build — Studio Launcher can reinstall it.", tone: .warn),
                            scrolls: true) {
            if !ReleaseNotes.hasNotes {
                // The next step is the build that ships the notes: Studio Launcher installs it.
                HStack(spacing: HubSpace.m) {
                    Button("Open Studio Launcher") { NativeWorkspaceStore.openStudioLauncher() }
                        .hubPrimary()
                    Text("Reinstall this build there, or install a newer one; its notes ship inside the app.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                    Spacer()
                }
            } else {
                // Plain-language changes first; engineering detail is a disclosure, not the page.
                let notes = body.joined(separator: "\n")
                let parts = notes.components(separatedBy: "### Technical notes")
                MarkdownText(markdown: parts[0])
                    .frame(maxWidth: 760, alignment: .leading)
                if parts.count > 1 {
                    DiagnosticDetails(title: "Technical notes", summary: "engineering detail") {
                        MarkdownText(markdown: parts.dropFirst().joined(separator: "### Technical notes"))
                    }
                    .frame(maxWidth: 760, alignment: .leading)
                }
            }
        }
    }

    /// "Controller · 4 Macs · 3 reachable" or "This Mac is on its own" — the page's state in one line.
    private var connectionsStatus: PageStatusLine {
        guard localServicesReady else {
            return PageStatusLine("Local services are stopped — start them to see this Mac's group.", tone: .warn)
        }
        let role = roleTitle(workspace.controllerIdentity?.settings.role)
        if workspace.fleetOverview != nil || workspace.fleetNodes != nil {
            // The same rows the table draws, this Mac included: the summary's total does not
            // count it, so the line and the table disagreed by one (critic, 2026-11-13).
            let rows = NativeFleetRow.rows(overview: workspace.fleetOverview, nodes: workspace.fleetNodes)
            let total = rows.count
            let reachable = rows.filter(\.isReachable).count
            let tone: HubStatus = total > 0 && reachable == 0 ? .bad
                : (reachable < total ? .warn : .ok)
            return PageStatusLine("\(role) · \(total) \(total == 1 ? "Mac" : "Macs") in this group · \(reachable) reachable now",
                                  tone: tone)
        }
        if let fleet = workspace.fleetNodes {
            let reachable = fleet.nodes.filter(\.isReachable).count
            return PageStatusLine("\(role) · \(fleet.nodes.count) joined \(fleet.nodes.count == 1 ? "Mac" : "Macs") · \(reachable) reachable",
                                  tone: fleet.nodes.isEmpty ? .off : (reachable == 0 ? .bad : .ok))
        }
        return PageStatusLine(workspace.fleetReadError ?? "Reading this Mac's group…", tone: .warn)
    }

    private var connections: some View {
        PageScaffold(title: pageTitle(.connections),
                     status: connectionsStatus,
                     scrolls: true,
                     primary: PagePrimaryAction("Refresh Fleet",
                                               isEnabled: localServicesReady && !workspace.fleetRefreshing) {
                        Task { await workspace.refreshFleetNodes() }
                     },
                     menu: [
                        PageMenuAction("Copy Join Address", symbol: "doc.on.doc",
                                       isEnabled: studio.nodeFacingAddress != nil) {
                            if let address = studio.nodeFacingAddress {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(address, forType: .string)
                                connectionNote = "Address copied: \(address)"
                            }
                        },
                     ]) {
            fleetSection
            appUpdatesSection
            localAPI
            controllerSection
        }
        .task { if connected { await workspace.refreshConnection() } }
        .sheet(item: $fleetDetailRow) { row in
            FleetRowDetailSheet(row: row) { fleetDetailRow = nil }
        }
    }

    /// The controller's app update, next to the connection state it comes from.
    ///
    /// It never installs silently or on a timer: the operator presses Install, and the text says
    /// plainly that the local services restart and running work stops. A release that cannot be
    /// verified against the key already pinned on this Mac is shown without an Install button.
    /// Where updates come from now: one sentence, and nothing in this app installs one.
    @ViewBuilder private var appUpdatesSection: some View {
        HubSection("App updates") {
            Text("Updates are installed by Studio Launcher KH.")
                .font(HubType.secondary).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }


    /// ST-10.2 — the private address, the identity behind it, and the credential, with the
    /// secret masked until the operator reveals or copies it.
    private var localAPI: some View {
        HubSection("Local API") {
            if let url = studio.readyURL {
                ValueRow("Address") {
                    Text(url.absoluteString).textSelection(.enabled).multilineTextAlignment(.trailing)
                }
                Text("Reachable only from this Mac on the loopback interface. A location controller reaches this Hub over the network; this build serves the loopback address.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
                if let identity = workspace.controllerIdentity {
                    ValueRow("Accepts customer jobs", value: identity.readiness.ready ? "Yes" : "No")
                    DiagnosticDetails(title: "This Hub's identity",
                                      summary: "\(identity.settings.role.capitalized) · \(identity.settings.machineName)") {
                        // `controllerId` is this Mac's own fixed id. On a controller it names the group; on
                        // a node it is the id the controller routes this Mac by, so "Controller ID" there
                        // read as the controller's id (live 2026-10-02, a renamed node).
                        let isController = identity.settings.role == "controller"
                        DiagnosticValue(label: "Role", value: identity.settings.role.capitalized)
                        DiagnosticValue(label: "Station", value: identity.settings.siteName)
                        DiagnosticValue(label: "Station ID", value: identity.settings.siteId)
                        DiagnosticValue(label: isController ? "Controller ID" : "Machine ID",
                                        value: identity.settings.controllerId)
                        DiagnosticValue(label: "Machine name", value: identity.settings.machineName)
                        Text(isController
                             ? "Node-to-node routing is unchanged: GatewayKH sends work to this location controller, and joined nodes keep their existing enrolment. This app does not open a second gateway."
                             : "The machine ID stays the same when this Mac is renamed; only the name changes. GatewayKH reaches it through its controller.")
                            .font(HubType.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    Text("Reading this Hub's identity…").font(HubType.secondary).foregroundStyle(.secondary)
                }
                hubCredential
            } else {
                Text("Start local services to make the API available on this Mac.").foregroundStyle(.secondary)
            }
        }
    }

    private var hubCredential: some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            // **One token to copy, and it is this one.** The two tokens are not peers and this
            // screen used to draw them as if they were: both rows identical, both with a copy
            // button, and the operator left to work out which to paste. He then read the Hub token
            // off the *joining* Mac, pasted it, and could not tell why it failed — which is the
            // failure this ordering and these headings exist to prevent. The token a joining Mac
            // needs is this one, so it is first, it says what to do with it, and the other one is
            // labelled as what it is: private.
            //
            // The two values stay distinct, and that is deliberate — see the note further down and
            // `tests/test_peer_path_contract.py::FleetCredentialWiringTests`. Merging the secrets
            // would give every node (and the Gateway) authority to administer every Mac.
            Text("Give this out").font(HubType.rowTitle)
            // Both tokens are masked until the eye is pressed; Copy works without showing them.
            ValueRow("Fleet token") {
                SecretValue(value: studio.fleetToken, copyTitle: "Copy Fleet Token") {
                    connectionNote = "Fleet token copied"
                }
            }
            Text("Give this to GatewayKH so it can send work here and collect the results, and to every Mac that joins this controller. **This is the only token you hand out.**")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("What it lets the holder do: send work to this Mac, follow that job, download the result, acknowledge it, cancel it or discard an uncertain one, read the model and fleet lists, and rename this Mac (its name only).")
                .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("What it cannot do: change this Mac's settings, install or remove its models, delete its output, manage its shared voices, or make a Mac join or leave. A Mac that holds this token still cannot administer this Mac.")
                .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Divider()
            DisclosureGroup("Advanced") {
                VStack(alignment: .leading, spacing: HubSpace.s) {
                    ValueRow("Hub token") {
                        SecretValue(value: studio.connectionToken, copyTitle: "Copy Hub Token") {
                            connectionNote = "Hub token copied"
                        }
                    }
                    Text("This Mac's master key. Never give it out.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let connectionNote { Text(connectionNote).font(HubType.secondary).foregroundStyle(.secondary) }
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
            HStack(alignment: .top, spacing: HubSpace.m) {
                Image(systemName: "arrow.clockwise.circle").foregroundStyle(HubStatus.warn.color)
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text("This change takes effect when the local services restart.")
                        .font(HubType.secondary).fixedSize(horizontal: false, vertical: true)
                    Button("Restart Services") { studio.restartServices() }
                        .disabled(studio.stopping || studio.starting)
                    Text("They stop and start again on their own; nothing else on this Mac is affected.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: HubRadius.card))
        }
    }

    /// The answer to the last join, leave or role change.
    ///
    /// A banner rather than a line of small print: it says whether the action worked, quotes the
    /// controller's own reason when it refused, can be selected to copy into a report, and stays
    /// until dismissed so a failure cannot scroll away unread.
    @ViewBuilder private func connectionNoticeBanner(_ notice: ConnectionNotice) -> some View {
        let tint: Color = notice.kind == .failure ? HubStatus.bad.color : HubStatus.ok.color
        HStack(alignment: .top, spacing: HubSpace.m) {
            Image(systemName: notice.kind == .failure
                  ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                .foregroundStyle(tint)
            VStack(alignment: .leading, spacing: HubSpace.tight) {
                Text(notice.kind == .failure ? "That did not work" : "Done")
                    .font(HubType.rowTitle)
                Text(notice.text)
                    .font(HubType.secondary)
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
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: HubRadius.card))
        .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(tint.opacity(0.35)))
    }

    /// The controller removed this Mac. Its own refusal is quoted, and the fix is named as being
    /// somewhere else — because the operator cannot undo this here.
    @ViewBuilder private func controllerRemovalBanner(_ removal: NativeWorkspaceStore.ControllerRemoval) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            HStack(alignment: .top, spacing: HubSpace.m) {
                Image(systemName: "xmark.octagon.fill").foregroundStyle(HubStatus.bad.color)
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text("This Mac was removed from its group").font(HubType.rowTitle)
                    Text(FleetCopy.removalNotice(date: removal.date, address: removal.address))
                        .font(HubType.secondary).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(removal.detail)
                        .font(HubType.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    if !removal.clearedGroupCredential {
                        Text("The local Hub did not confirm dropping the group's token, so start the local services and press Leave this group again to be sure it is gone.")
                            .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 8)
                Button("Dismiss") { workspace.dismissControllerRemoval() }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(HubStatus.bad.color.opacity(0.10), in: RoundedRectangle(cornerRadius: HubRadius.card))
        .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(HubStatus.bad.color.opacity(0.35)))
    }

    /// A controller that has handed its group over keeps its history and refuses new work. Its own
    /// Hub enforces that; this is the page saying so rather than leaving a retired Mac looking live.
    @ViewBuilder private func retiredBanner(_ target: String, at moment: Double?) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            HStack(alignment: .top, spacing: HubSpace.m) {
                Image(systemName: "arrow.right.circle.fill").foregroundStyle(HubStatus.warn.color)
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    Text("Handed over to \(target)\(moment.map { " on \(NativeWorkspaceStore.moment($0))" } ?? "")")
                        .font(HubType.rowTitle).textSelection(.enabled)
                    Text("This controller is retired: it keeps its job history and refuses new remote work, so nothing should be sent here. Remove it from GatewayKH and wipe or sell it.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(HubStatus.warn.color.opacity(0.12), in: RoundedRectangle(cornerRadius: HubRadius.card))
        .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(HubStatus.warn.color.opacity(0.35)))
    }

    /// Rotating the Fleet token and handing the group over: the two operations that change what
    /// every other Mac in the group trusts, so they are here beside the roster they act on.
    @ViewBuilder private var fleetGroupOperations: some View {
        let role = workspace.controllerIdentity?.settings.role
        let adopted = workspace.controllerIdentity?.settings.fleetCredentialOrigin == "adopted"
        let retired = !(workspace.controllerIdentity?.settings.handedOverTo ?? "").isEmpty
        if role == "controller" && !adopted {
            VStack(alignment: .leading, spacing: HubSpace.m) {
                Text("Group operations").font(HubType.rowTitle)
                if retired {
                    Text("This controller has handed its group over, so there is nothing left to rotate or hand over. Resetting its fleet only clears a roster that is already empty — it does not un-retire this Mac.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    // Still offered here: the owner is disassembling his own machines today, and a
                    // retired controller that was re-pointed by hand should not have to be told to
                    // edit a JSON file before it can start a group again.
                    fleetResetControl
                } else {
                    HStack(alignment: .center) {
                        Button("Rotate Fleet token…") { confirmingRotation = true }
                            .disabled(!connected || workspace.fleetOperationRunning)
                        Text("Gives every member a new token; hand it to GatewayKH afterwards.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    // **The clean-slate counterpart to a rotation** (owner, 2026-10-25: *"make sure
                    // we can disassemble the nodes and controllers so I can restart anew"*). A
                    // rotation keeps the group and changes its secret; this drops the group. Both
                    // are here because they answer two different decisions, and putting the reset
                    // behind a different word is what stops an operator reaching for the wrong one.
                    fleetResetControl
                    VStack(alignment: .leading, spacing: HubSpace.tight) {
                        Text("Hand over to…").font(HubType.rowTitle)
                        Text("Move this group to another controller; this Mac retires and keeps its history.")
                            .font(HubType.secondary).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        HStack {
                            TextField("New controller's address, e.g. 100.x.y.z:47873", text: $handoverAddress)
                                .textFieldStyle(.roundedBorder).frame(maxWidth: 300)
                            SecretInput(placeholder: "Its Fleet token", text: $handoverToken)
                                .frame(maxWidth: 260)
                            Button(workspace.fleetOperationRunning ? "Handing over…" : "Hand over…") {
                                confirmingHandover = true
                            }
                            .disabled(!connected || workspace.fleetOperationRunning
                                      || handoverAddress.isEmpty || handoverToken.isEmpty)
                        }
                        .onChange(of: handoverAddress) { _, value in
                            let cleaned = value.filter { !$0.isWhitespace }
                            if cleaned != value { handoverAddress = cleaned }
                        }
                        .onChange(of: handoverToken) { _, value in
                            let cleaned = value.filter { !$0.isWhitespace }
                            if cleaned != value { handoverToken = cleaned }
                        }
                        DiagnosticDetails(title: "If a Mac does not follow",
                                          summary: "the result below names it") {
                            Text("A Mac that is switched off cannot be told, and the fallback for it is SSH over Tailscale — gateway-kh/repository/docs/credential-rotation-decision.md. The result below names every Mac that did not follow.")
                                .font(HubType.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                if let notice = FleetCopy.previousTokenNotice(
                    count: workspace.fleetNodes?.summary.onPreviousToken ?? 0,
                    until: workspace.fleetNodes?.summary.previousTokenUntil) {
                    Text(notice).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let report = workspace.fleetOperation {
                    fleetOperationReportView(report)
                }
            }
        } else if let report = workspace.fleetOperation {
            // A Mac that is no longer a controller can still have run one of these a moment ago.
            fleetOperationReportView(report)
        }
    }

    /// **Start this controller over**: drop its whole roster and mint a new group token.
    ///
    /// One view so the retired and the working controller show the same control with the same
    /// sentence — the action is local and keeps its records, so it is available in both states.
    @ViewBuilder private var fleetResetControl: some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            HStack(alignment: .center) {
                Button("Reset the fleet…") { confirmingFleetReset = true }
                    .disabled(!connected || workspace.fleetOperationRunning)
                Text("Forget this roster and start a new group; nothing is deleted.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            }
            DiagnosticDetails(title: "What a reset does", summary: "the old roster is kept in Trash") {
                Text("Every Mac on this roster is forgotten, every per-node credential is revoked and a new Fleet token is minted. The old roster is kept in this Hub's Trash Folder, and no Mac is barred from joining again. Each Mac leaves from its own Connections page, then joins again with the new token.")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// The per-machine result of a rotation or a handover, by name.
    @ViewBuilder private func fleetOperationReportView(_ report: FleetOperationReport) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            Text(report.summary)
                .font(HubType.secondary)
                .foregroundStyle(report.isComplete ? HubStatus.ok.textColor : HubStatus.warn.textColor)
                .fixedSize(horizontal: false, vertical: true)
            if let token = report.token, report.kind == .rotation {
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    Text("New Fleet token — give it to every member and to GatewayKH").font(HubType.caption)
                    SecretValue(value: token) { connectionNote = "Fleet token copied" }
                }
            }
            if !report.followed.isEmpty {
                Text("Followed: " + report.followed.map(workspace.fleetMachineName).joined(separator: ", "))
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !report.unfinished.isEmpty {
                Text("Did not follow: "
                     + report.unfinished.map { machine in
                         "\(workspace.fleetMachineName(machine)) — \(report.outcomes[machine] ?? "no answer")"
                     }.joined(separator: "; ")
                     + ". " + (report.kind == .rotation && report.manual == 0
                                ? FleetCopy.rotationWaitsNote : FleetCopy.handoverFallback))
                    .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }.padding(.vertical, 2)
    }

    /// What the operator is agreeing to, for each role. Written as one function so the picker and
    /// the Promote button cannot describe the same change differently.
    private func roleConfirmation(_ role: String) -> (title: String, message: String, action: String) {
        switch role {
        case "controller":
            return ("Make this Mac a controller?",
                    "This Mac stops following its controller (if it had one) and becomes the head of its own group. The Hub mints a new Fleet token for it — the one it held belonged to the group it was in — so every Mac that is to join this one needs that new token, and GatewayKH has to be given this Mac's address and the new Fleet token to send it work.",
                    "Make it a Controller")
        case "agent":
            return ("Make this Mac a node?",
                    "This Mac stops running its own group and takes work from a controller instead. It keeps creating locally, but it stops accepting customer jobs directly and reaches GatewayKH through its controller. Join a controller from this page afterwards.",
                    "Make it a Node")
        default:
            return ("Run this Mac on its own?",
                    "This Mac stops being a node: it drops the group's token, so its controller can no longer send it work, and it goes back to running on its own credential. The controller's roster still lists it until it is removed there or it leaves the group.",
                    "Make it Standalone")
        }
    }

    private var controllerSection: some View {
        HubSection("Hub Studio KH & GatewayKH") {
            if let identity = workspace.controllerIdentity, let version = workspace.hubVersion {
                ValueRow("Hub version", value: version.display)
                ValueRow("Data store", value: identity.readiness.sqliteAuthoritative == true ? "Local, authoritative" : identity.settings.databaseMode)
                ValueRow("Reported protocol authority", value: identity.settings.globalAuthority ?? "Not reported")
                ValueRow("Migration stage", value: identity.settings.migrationStage ?? identity.readiness.migrationStage ?? "local")
                if let warning = identity.readiness.telemetryWarning {
                    Text(warning).font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor)
                }
                Text("Jobs are recorded and claimed on this Mac. GatewayKH sends work to location controllers; Hub Studio KH manages local execution and reports the capabilities it actually has. This app offers no update channel it cannot honour.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            } else {
                Text("Remote control is optional and not required to create assets locally.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// Role, reachability and the address other Macs use — the whole fleet picture in one place.
    private var fleetSection: some View {
        VStack(alignment: .leading, spacing: HubSpace.xl) {
            // The answer to the last join, leave or role change, at the top where it cannot be
            // missed, and kept until dismissed. It used to be a line of small grey print further
            // down, which the owner did not see until the third attempt.
            if let notice = workspace.connectionNotice {
                connectionNoticeBanner(notice)
            }
            // The controller removed this Mac, and that decision stands until it is re-admitted
            // there. Shown above everything else on the page because it explains a state the
            // operator did not ask for, and because the fix is not on this Mac.
            if let removal = workspace.controllerRemoval {
                controllerRemovalBanner(removal)
            }
            // A controller that has handed its group over keeps its history and takes no new work.
            // Saying so here is what stops an operator reading a retired Mac as a working one.
            if let handedOver = workspace.controllerIdentity?.settings.handedOverTo,
               !handedOver.isEmpty {
                retiredBanner(handedOver, at: workspace.controllerIdentity?.settings.handedOverAt)
            }
            // **One list of Macs.** The overview and the roster used to be two lists of the same
            // Macs in two styles; this is the one table, with each Mac's state, what it is doing,
            // its disk and memory, and what starts when it starts.
            nodesSection
            HubSection("This Mac") {
            // "This Mac is a Not reporting" was what the operator read when the Hub had not
            // answered yet: the article was hard-coded beside a label that is sometimes a phrase.
            // Naming this Mac. The Hub has kept a label for it all along - `machine_name` is what
            // every other Mac and the gateway show - but no control ever offered it, so the only
            // name an operator could have was their hostname.
            VStack(alignment: .leading, spacing: HubSpace.tight) {
                Text("Name").font(HubType.secondary)
                HStack {
                    TextField("This Mac's name, e.g. Studio Mini 01", text: $machineNameDraft)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                    Button("Rename") {
                        Task { await workspace.renameThisMachine(machineNameDraft) }
                    }
                    .disabled(!connected || machineNameDraft.trimmingCharacters(in: .whitespacesAndNewlines)
                              == (workspace.controllerIdentity?.settings.machineName ?? ""))
                    // The other two identifiers have one, and a name is the value an operator most
                    // often has to type somewhere else (a Channel name, a Gateway label), so it
                    // gets the same affordance rather than making him select the text by hand.
                    Button("Copy name") {
                        let value = workspace.controllerIdentity?.settings.machineName ?? ""
                        guard !value.isEmpty else { return }
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(value, forType: .string)
                        identityCopyNote = "Machine name copied: \(value)"
                    }
                    .controlSize(.small)
                    .disabled((workspace.controllerIdentity?.settings.machineName ?? "").isEmpty)
                }
                Text("What this Mac is called in the fleet, in Jobs and to the gateway. It is a label, not an identity: renaming it never changes routing or a node's registration.")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .onAppear { machineNameDraft = workspace.controllerIdentity?.settings.machineName ?? "" }
            .onChange(of: workspace.controllerIdentity?.settings.machineName) { _, value in
                machineNameDraft = value ?? ""
            }

            // **The identifiers, in plain view** (owner, 2026-10-25: *"what are the identifiers on
            // Hub Studio KH for each machine? Make sure they're prominent and easily edited, so I
            // don't repeat my current mistake, because they're hard to change. With this move I
            // want a clean slate."*).
            //
            // They used to be a **tooltip** on a row that showed the *name*: the one value a
            // controller routes by was the one value not on the screen, and the only way to change
            // it was to edit the settings file by hand. So: the Machine ID and the site are printed
            // with the sentence that says what each is for, each with a Copy button, and they are
            // **editable while this Mac is not in a group** — which is exactly when they can be
            // changed without breaking somebody else's roster.
            machineIdentifiers

            ValueRow("This Mac is", value: roleTitle(workspace.controllerIdentity?.settings.role))
            ValueRow("Group", value: groupStatus)
            Text(roleExplanation(workspace.controllerIdentity?.settings.role))
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HubSegmentedControl(
                selection: Binding(
                    get: { workspace.controllerIdentity?.settings.role ?? "standalone" },
                    set: { value in
                        // A role change is an identity change, not a preference: becoming a controller
                        // mints this Mac's own group credential and stops it being anybody's node, and
                        // becoming a node ends the group this Mac runs. It is confirmed before it is
                        // sent, and the confirmation says which of those it is.
                        guard value != (workspace.controllerIdentity?.settings.role ?? "standalone") else { return }
                        pendingRoleChange = value
                    }
                ),
                items: [
                    HubSegmentItem(tag: "standalone", label: "Standalone"),
                    HubSegmentItem(tag: "controller", label: "Controller"),
                    HubSegmentItem(tag: "agent", label: "Node")
                ],
                accessibilityLabel: "Role"
            )
            .disabled(!connected || workspace.controllerIdentity == nil)
            // Promoting a node by name, where the node is: the picker's "Controller" does the same
            // thing, but the consequence (this Mac stops following its controller and mints its own
            // group credential) is the part an operator needs before pressing it.
            if workspace.controllerIdentity?.settings.fleetCredentialOrigin == "adopted" {
                HStack(alignment: .top) {
                    Button("Promote to controller…") { pendingRoleChange = "controller" }
                        .disabled(!connected)
                    Text("Stops this Mac following its controller and makes it the head of its own group, with a new Fleet token of its own. Macs that were nodes of the old controller are unaffected; any Mac that is to be sent work by this one joins it with the new token.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let note = workspace.roleNote {
                Text(note).font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("A **Controller** runs its own group and sends work to nodes; a **Node** is sent work by a controller; **Standalone** is a Mac on its own. The role applies immediately.")
                .font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }

            HubSection("Network") {
            restartNotice

            ValueRow("Listening on") {
                if studio.configuration.listenHosts.contains("0.0.0.0") {
                    Text(listenTitle(studio.configuration.listenHost))
                } else {
                    VStack(alignment: .leading, spacing: HubSpace.xs) {
                        ForEach(studio.configuration.listenHosts, id: \.self) { address in
                            HStack {
                                Text(address == "127.0.0.1" ? "This Mac only (\(address))" : address)
                                    .textSelection(.enabled)
                                CopyValueButton(value: address)
                                    .accessibilityLabel("Copy listening address \(address)")
                            }
                        }
                    }
                }
            }
            // A setting that changes by itself has to say so, and has to be switchable off. On, the
            // list below is recomputed at every launch from this Mac's own networks — the owner's
            // ask was "i keep forgetting to turn on tailscale. i want it already the default on."
            Toggle("Follow this Mac's networks automatically", isOn: Binding(
                get: { studio.configuration.listenHostAutomatic },
                set: { setListenAutomatic($0) }))
                .toggleStyle(.checkbox)
            if studio.configuration.listenHostAutomatic {
                Text("Loopback and every private or Tailscale address this Mac has, recomputed each time it starts. Never a public address.")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Pick every network this Mac should be reachable on. It always answers this Mac itself, so choosing nothing here keeps it private.")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            // Several addresses at once, because a Mac serving its office LAN and its tailnet is
            // the normal case; loopback is always included by the Hub, so it is never a choice.
            ForEach(LocalAddresses.current()) { entry in
                HStack {
                    Toggle("Reachable at \(entry.title)", isOn: Binding(
                        get: { studio.configuration.listenHosts.contains(entry.address) },
                        set: { included in
                            var hosts = studio.configuration.listenHosts.filter { $0 != "0.0.0.0" }
                            if included { hosts.append(entry.address) }
                            else { hosts.removeAll { $0 == entry.address } }
                            pinListenHosts(hosts)
                        }))
                    CopyValueButton(value: entry.address)
                        .accessibilityLabel("Copy reachable address \(entry.address)")
                }
                // These are editable while the Hub runs: the change is saved and the notice above
                // offers the restart that applies it, instead of the control refusing to move.
            }
            Toggle("Any address on any network", isOn: Binding(
                get: { studio.configuration.listenHosts.contains("0.0.0.0") },
                set: { pinListenHosts($0 ? ["0.0.0.0"] : []) }))
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
                    .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if let address = studio.nodeFacingAddress {
                    CopyValueButton(value: address)
                        .accessibilityLabel("Copy node join address")
                }
            }
            listenWarning

            if let note = studio.fleetNote {
                Text(note).font(HubType.secondary).foregroundStyle(.secondary)
            }
            // Only a Mac that joined somebody belongs to a group. This is the action that takes
            // the group's credential off it, which is what a Mac being retired or sold needs.
            if workspace.controllerIdentity?.settings.fleetCredentialOrigin == "adopted" {
                HStack(alignment: .top) {
                    Button("Leave this group…") { confirmingLeave = true }
                        .disabled(!connected)
                    Text("Stops this Mac using the group's token and returns it to running on its own. Do this before retiring or selling it.")
                        .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }

            // The banner at the top of this page already carries the last actionable result, so
            // the plain note only appears when there is no banner (a quiet startup re-join).
            if workspace.connectionNotice == nil, let note = workspace.fleetNote {
                Text(note).font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            }

            joinController
        }
        .task(id: "\(localServicesReady)/\(workspace.connectionGeneration)") {
            if localServicesReady {
                await workspace.refreshFleetNodes()
                await workspace.refreshControllerHealth()
            }
        }
        // **The three group actions that cannot be undone by pressing the same button again.**
        // A role change re-mints this Mac's group credential, leaving a group drops the token a
        // fleet is built on, removing a node is permanent until it is re-admitted, and a rotation
        // invalidates the token every member and GatewayKH hold. Each one names its consequence
        // before it is sent; the app never reports a partial result as success.
        .confirmationDialog(
            roleConfirmation(pendingRoleChange ?? "standalone").title,
            isPresented: Binding(get: { pendingRoleChange != nil },
                                 set: { if !$0 { pendingRoleChange = nil } }),
            titleVisibility: .visible,
            presenting: pendingRoleChange) { role in
                Button(roleConfirmation(role).action) {
                    let chosen = role
                    pendingRoleChange = nil
                    Task { await workspace.setRole(chosen) }
                }
                Button("Cancel", role: .cancel) { pendingRoleChange = nil }
            } message: { role in
                Text(roleConfirmation(role).message)
            }
        .confirmationDialog(
            "Leave this group?",
            isPresented: $confirmingLeave,
            titleVisibility: .visible) {
                Button("Leave the Group", role: .destructive) {
                    Task { await workspace.leaveGroup() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This Mac drops the group's token and runs on its own again, so its controller can no longer send it work. It also asks the controller to drop this Mac from its roster; if the controller is off, it still lists this Mac and has to be cleaned up there. Joining again needs the controller's current Fleet token.")
            }
        .confirmationDialog(
            "Reset this controller's fleet?",
            isPresented: $confirmingFleetReset,
            titleVisibility: .visible) {
                Button("Reset the Fleet", role: .destructive) {
                    Task { await workspace.resetFleet() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("\(workspace.fleetNodes.map { "\($0.nodes.count) Mac\($0.nodes.count == 1 ? "" : "s")" } ?? "Every Mac on this roster") will be forgotten and every per-node credential on this controller revoked, and a new Fleet token will be minted. The old roster is kept in this Hub's Trash Folder, so nothing is lost. The Macs are not told: each one leaves from its own Connections page, and then joins this Mac again with the new token. GatewayKH needs the new token too.")
            }
        .confirmationDialog(
            "Rotate the Fleet token?",
            isPresented: $confirmingRotation,
            titleVisibility: .visible) {
                Button("Rotate the Token", role: .destructive) {
                    Task { await workspace.rotateFleetToken() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("A new Fleet token is minted and pushed to every joined Mac. The old one stops working immediately. Every member and GatewayKH must be given the new value; any Mac that refuses the old credential cannot be told automatically and has to be moved by hand — SSH over Tailscale.")
            }
        .confirmationDialog(
            "Hand this group over to \(handoverAddress)?",
            isPresented: $confirmingHandover,
            titleVisibility: .visible) {
                Button("Hand Over and Retire This Controller", role: .destructive) {
                    Task { await workspace.handOverFleet(to: handoverAddress, token: handoverToken) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Every node this controller can reach is moved to \(handoverAddress) with a new Fleet token, and this controller stops taking work. It keeps its history. A node that is switched off is not moved and needs the manual SSH path. This cannot be undone from here.")
            }
        .confirmationDialog(
            "Remove \(pendingNodeRemoval?.node.name ?? "this node") from this controller?",
            isPresented: Binding(get: { pendingNodeRemoval != nil },
                                 set: { if !$0 { pendingNodeRemoval = nil } }),
            titleVisibility: .visible,
            presenting: pendingNodeRemoval) { intent in
                Button("Remove \(intent.node.name)", role: .destructive) {
                    pendingNodeRemoval = nil
                    Task {
                        guard localServicesReady else {
                            nodeRemovalRefusal = "Removal was not sent because local services stopped. Start them, refresh Nodes, and confirm again."
                            return
                        }
                        await workspace.leaveNode(
                            intent.node,
                            expectedConnectionGeneration: intent.connectionGeneration)
                    }
                }
                Button("Cancel", role: .cancel) { pendingNodeRemoval = nil }
            } message: { intent in
                Text("\(intent.node.name) is removed from this controller's roster and its credential is revoked, so it cannot re-join on its own — not even after a restart. It stays removed until it is re-admitted from this page. Jobs it already finished stay in Jobs.")
            }
    }

    // MARK: - The node table

    /// The table's column widths. The two flexible columns (the Mac, and what it is doing) share what
    /// is left; the rest are fixed so every row's cells line up under the header.
    private enum NodeColumn {
        static let state: CGFloat = 140
        static let meter: CGFloat = 84
        static let actions: CGFloat = 104
        static let gap: CGFloat = 10
        /// The Mac column takes what it needs up to this; "Doing now" takes the rest, so header and rows
        /// share one set of column edges at any width.
        static let macMaximum: CGFloat = 360
        /// The narrowest width the one-line-per-Mac table is drawn at; below it each Mac is a small card.
        static let wideMinimum: CGFloat = 796
    }

    /// **This Mac's Machine ID and site**, printed plainly and editable exactly when editing is
    /// honest.
    ///
    /// The rule is the Hub's (`control_plane.save_settings` refuses it too, and its sentence is the
    /// one that finally decides): `controller_id` becomes this Mac's roster `node_id` when it joins,
    /// so a controller routes to this Mac by it. Changing it under a live membership would leave
    /// the controller holding a row for a Mac that no longer answers to it, while the Mac's own
    /// credential stayed keyed to the old id — so while this Mac is in a group the value is shown,
    /// the reason is stated, and the way out (Leave) is one press away rather than a paragraph.
    @ViewBuilder private var machineIdentifiers: some View {
        let settings = workspace.controllerIdentity?.settings
        let inGroup = settings?.fleetCredentialOrigin == "adopted"
        VStack(alignment: .leading, spacing: HubSpace.s) {
            Text("Identifiers").font(HubType.rowTitle)
            DiagnosticDetails(title: "Machine ID and site",
                              summary: "\(settings?.controllerId ?? "not reporting") · \(settings?.siteName ?? "")") {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            identifierLine("Machine ID", value: settings?.controllerId, copyLabel: "Copy ID",
                           explanation: "The key a controller routes to this Mac by. A join registers "
                               + "it as this Mac's node id.")
            identifierLine("Site", value: settings.map { "\($0.siteName) (\($0.siteId))" },
                           copyLabel: "Copy site",
                           explanation: "Which site this Mac belongs to: the gateway groups its "
                               + "locations by this when it lists what the Hub can do.")
            if let identityCopyNote {
                Text(identityCopyNote).font(HubType.caption).foregroundStyle(.secondary)
            }
            if inGroup {
                Text("Read-only while this Mac is in a group: its controller routes to it by this "
                     + "Machine ID, so changing it now would leave that controller holding a row "
                     + "that no longer answers, and this Mac's own credential is keyed to the old "
                     + "id. Leave the fleet first, change them, then join again.")
                    .font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(alignment: .top, spacing: HubSpace.s) {
                    Button("Leave this group…") { confirmingLeave = true }
                        .disabled(!connected)
                    Text("Returns this Mac to running on its own. Nothing else changes: its model "
                         + "library, its jobs and its output stay where they are.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                machineIdentityEditor
            }
        }
        .onAppear { loadIdentityDrafts() }
        .onChange(of: workspace.controllerIdentity?.settings.controllerId) { _, _ in loadIdentityDrafts() }
        .onChange(of: workspace.controllerIdentity?.settings.siteId) { _, _ in loadIdentityDrafts() }
        .onChange(of: workspace.controllerIdentity?.settings.siteName) { _, _ in loadIdentityDrafts() }
            }
        }
        .frame(maxWidth: 620, alignment: .leading)
    }

    /// One identifier, printed where it can be read and copied: the value in a monospace face so a
    /// person can compare two Macs character by character, and the sentence saying what it is for.
    @ViewBuilder private func identifierLine(_ label: String, value: String?,
                                             copyLabel: String, explanation: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: HubSpace.s) {
                Text(label).font(HubType.body).frame(width: 96, alignment: .leading)
                Text(value ?? "Not reporting")
                    .font(HubType.mono).textSelection(.enabled)
                    .foregroundStyle(value == nil ? Color.secondary : Color.primary)
                Button(copyLabel) {
                    guard let value, !value.isEmpty else { return }
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(value, forType: .string)
                    identityCopyNote = "\(label) copied: \(value)"
                }
                .controlSize(.small)
                .disabled(value?.isEmpty ?? true)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(label): \(value ?? "not reporting")")
            Text(explanation).font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true).padding(.leading, 96 + HubSpace.s)
        }
    }

    /// The three fields, while this Mac is its own: the identity is set up here, before a join
    /// freezes it.
    @ViewBuilder private var machineIdentityEditor: some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            HStack(spacing: HubSpace.s) {
                Text("Machine ID").font(HubType.body).frame(width: 96, alignment: .leading)
                TextField("studio-mini-01", text: $machineIdDraft)
                    .textFieldStyle(.roundedBorder).frame(width: 220).font(HubType.mono)
            }
            HStack(spacing: HubSpace.s) {
                Text("Site ID").font(HubType.body).frame(width: 96, alignment: .leading)
                TextField("local-site", text: $siteIdDraft)
                    .textFieldStyle(.roundedBorder).frame(width: 220).font(HubType.mono)
                Text("Site name").font(HubType.body).frame(width: 76, alignment: .leading)
                TextField("Local site", text: $siteNameDraft)
                    .textFieldStyle(.roundedBorder).frame(width: 200)
            }
            HStack(spacing: HubSpace.s) {
                Button("Save Machine ID and site") {
                    Task {
                        await workspace.setMachineIdentity(
                            controllerId: machineIdDraft.trimmingCharacters(in: .whitespacesAndNewlines),
                            siteId: siteIdDraft.trimmingCharacters(in: .whitespacesAndNewlines),
                            siteName: siteNameDraft.trimmingCharacters(in: .whitespacesAndNewlines))
                    }
                }
                .disabled(!connected || !identityDraftChanged)
                if let note = workspace.identityNote {
                    Text(note).font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Text("Set the Machine ID before this Mac joins anything: it is what the controller "
                 + "registers and routes by, and it is read-only from then on. Lowercase letters, "
                 + "numbers, dots, dashes and underscores — e.g. `studio-mini-01`, `shops-till-02`. "
                 + "The suggestion is only a suggestion: nothing is derived from this Mac's hostname "
                 + "beyond the single default it started with.")
                .font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The Hub's own answers into the three drafts. Called on appear and whenever any of them
    /// changes under the page, so a save elsewhere (a leave, a reset) does not leave a stale value
    /// in the field to be saved back over the new one.
    private func loadIdentityDrafts() {
        guard let settings = workspace.controllerIdentity?.settings else { return }
        machineIdDraft = settings.controllerId
        siteIdDraft = settings.siteId
        siteNameDraft = settings.siteName
    }

    private var identityDraftChanged: Bool {
        guard let settings = workspace.controllerIdentity?.settings else { return false }
        return machineIdDraft.trimmingCharacters(in: .whitespacesAndNewlines) != settings.controllerId
            || siteIdDraft.trimmingCharacters(in: .whitespacesAndNewlines) != settings.siteId
            || siteNameDraft.trimmingCharacters(in: .whitespacesAndNewlines) != settings.siteName
    }

    /// Every Mac in the group, in one table: is it there, what is it doing, how full is its disk and
    /// its memory, and what starts when it starts.
    ///
    /// The fleet overview and the roster used to be two lists of the same Macs in two row styles
    /// (different type sizes, different badges). They are one list now, joined on the Mac's id: the
    /// overview supplies what a Mac reports about itself, the roster supplies whether it answers
    /// this controller and the Remove action.
    @ViewBuilder private var nodesSection: some View {
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(alignment: .firstTextBaseline, spacing: HubSpace.m) {
                Text("Nodes").font(HubType.section)
                Spacer(minLength: 0)
                if windowWidth - HubSpace.sidebar - 1 - 2 * HubSpace.page >= NodeColumn.wideMinimum {
                    HubSegmentedControl(
                        selection: $fleetView,
                        items: [
                            HubSegmentItem(tag: "table", label: "Table", symbol: "tablecells"),
                            HubSegmentItem(tag: "cards", label: "Cards", symbol: "square.grid.2x2"),
                        ],
                        controlSize: .small,
                        accessibilityLabel: "Macs view"
                    )
                    .help("Table or cards; remembered for this Mac")
                }
                TextField("Search Macs", text: $fleetSearch)
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 240)
                    .onExitCommand { fleetSearch = "" }
                if !fleetSearch.isEmpty {
                    Button("Clear") { fleetSearch = "" }.hubRowControl()
                }
                fleetRefreshStatus
            }
            let allRows = NativeFleetRow.rows(overview: workspace.fleetOverview, nodes: workspace.fleetNodes)
            let query = fleetSearch.trimmingCharacters(in: .whitespacesAndNewlines)
            let rows = query.isEmpty ? allRows : allRows.filter {
                $0.name.localizedCaseInsensitiveContains(query)
                    || ($0.address ?? "").localizedCaseInsensitiveContains(query)
                    || ($0.node?.id ?? "").localizedCaseInsensitiveContains(query)
            }
            let hasFleetSnapshot = workspace.fleetOverview != nil || workspace.fleetNodes != nil
            // One grouped warning with its count, not a stack of repeated lines.
            let warnings = [workspace.catalogReadError,
                            workspace.fleetReadError.map { $0 + (hasFleetSnapshot ? " Showing what was read last." : "") },
                            nodeRemovalRefusal].compactMap { $0 }
            if !warnings.isEmpty {
                HubBanner(status: .warn, symbol: "exclamationmark.triangle.fill",
                          title: "\(warnings.count) fleet warning\(warnings.count == 1 ? "" : "s")",
                          message: warnings.joined(separator: "\n\n")) {
                    Button(localServicesReady ? "Retry" : "Start Services") {
                        if localServicesReady {
                            Task { await workspace.refreshFleetNodes() }
                        } else {
                            studio.start()
                        }
                    }.hubRowControl()
                }
            }
            if workspace.fleetOverview != nil || workspace.fleetNodes != nil {
                nodesSummary
                if !rows.isEmpty {
                    nodeTable(rows)
                } else if !query.isEmpty {
                    Text("No Mac matches “\(query)”.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                }
                if let removed = workspace.fleetNodes?.removed, !removed.isEmpty {
                    removedNodes(removed)
                }
            } else {
                Text(!localServicesReady ? "Start local services to see the fleet."
                     : workspace.fleetRefreshing ? "Reading the fleet…"
                     : workspace.fleetReadError ?? "The fleet has not been read yet.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
            }
            fleetGroupOperations
        }
    }

    /// The counts and the two sentences that explain the table: whose group this is, and where new
    /// work goes.
    @ViewBuilder private var nodesSummary: some View {
        VStack(alignment: .leading, spacing: HubSpace.xs) {
            if let overview = workspace.fleetOverview {
                let rows = NativeFleetRow.rows(overview: workspace.fleetOverview, nodes: workspace.fleetNodes)
                Text(poolSummary(overview) + " " + countsLine(rows))
                    .font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(pinSummary)
                    .font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if let fleet = workspace.fleetNodes {
                Text(fleetSummaryLine(fleet)).font(HubType.secondary).foregroundStyle(.secondary)
            }
            if let fleet = workspace.fleetNodes, fleet.nodes.isEmpty, let overview = workspace.fleetOverview,
               overview.belongsToGroup {
                Text(nodeMembershipSummary(overview))
                    .font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if let fleet = workspace.fleetNodes, fleet.nodes.isEmpty, workspace.fleetOverview?.belongsToGroup != true {
                Text("No Mac has joined this one yet. Macs that join this one appear here, with Remove to drop them.")
                    .font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Counts the rows the table draws, this Mac included, so the sentence and the table agree.
    private func countsLine(_ rows: [NativeFleetRow]) -> String {
        // The status line says how many are reachable and poolSummary says the list is below; this
        // sentence carries only the exceptions.
        var parts: [String] = []
        if let fleet = workspace.fleetNodes {
            if let refused = fleet.summary.unauthorized, refused > 0 {
                parts.append("\(refused) refused this controller's credential")
            }
            if fleet.summary.error > 0 { parts.append("\(fleet.summary.error) answered with an error") }
        }
        return parts.isEmpty ? "Every Mac answered." : parts.joined(separator: " · ") + "."
    }

    private func nodeTable(_ rows: [NativeFleetRow]) -> some View {
        // The remembered Table/Cards choice, with cards forced below the table's minimum width.
        let canTable = windowWidth - HubSpace.sidebar - 1 - 2 * HubSpace.page >= NodeColumn.wideMinimum
        let cards = fleetView == "cards" || !canTable
        return Group {
            if cards {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: HubSpace.m, alignment: .top)],
                          alignment: .leading, spacing: HubSpace.m) {
                    ForEach(rows) { row in fleetCard(row) }
                }
            } else {
                HubCard(padding: 0) {
                    VStack(spacing: 0) {
                        nodeTableHeader
                        ForEach(rows) { row in
                            Divider()
                            wideNodeRow(row)
                        }
                    }
                }
            }
        }
    }

    /// One Mac as a card: the same facts as a table row, equal height, actions at the foot.
    private func fleetCard(_ row: NativeFleetRow) -> some View {
        HubCard {
            VStack(alignment: .leading, spacing: HubSpace.s) {
                HStack(alignment: .top, spacing: HubSpace.m) {
                    nodeIdentity(row)
                    Spacer(minLength: 0)
                    nodeStateCell(row)
                }
                nodeDoingNow(row)
                HStack(alignment: .bottom, spacing: HubSpace.l) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Disk free").font(HubType.caption).foregroundStyle(.secondary)
                        resourceCell(text: row.machine?.diskText, tier: row.machine?.diskTier ?? .unknown,
                                     fraction: row.machine?.diskFreeFraction, reachable: row.isReachable,
                                     total: row.machine?.totalDiskText)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Memory free").font(HubType.caption).foregroundStyle(.secondary)
                        resourceCell(text: row.machine?.memoryText, tier: row.machine?.memoryTier ?? .unknown,
                                     fraction: row.machine?.memoryFreeFraction, reachable: row.isReachable)
                    }
                    Spacer(minLength: 0)
                    nodeActions(row)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 168, alignment: .topLeading)
        }
    }

    private var nodeTableHeader: some View {
        HStack(alignment: .firstTextBaseline, spacing: NodeColumn.gap) {
            Text("Mac").frame(minWidth: 170, maxWidth: NodeColumn.macMaximum, alignment: .leading).layoutPriority(1)
            Text("State").frame(width: NodeColumn.state, alignment: .leading)
            Text("Doing now").frame(minWidth: 130, maxWidth: .infinity, alignment: .leading)
            Text("Disk free").frame(width: NodeColumn.meter, alignment: .leading)
            Text("Memory free").frame(width: NodeColumn.meter, alignment: .leading)
            Color.clear.frame(width: NodeColumn.actions, height: 1)
        }
        .font(HubType.captionStrong).foregroundStyle(.secondary)
        .padding(.horizontal, HubSpace.l).padding(.vertical, HubSpace.s)
    }

    private func wideNodeRow(_ row: NativeFleetRow) -> some View {
        HStack(alignment: .top, spacing: NodeColumn.gap) {
            nodeIdentity(row).frame(minWidth: 170, maxWidth: NodeColumn.macMaximum, alignment: .leading).layoutPriority(1)
            nodeStateCell(row).frame(width: NodeColumn.state, alignment: .leading)
            nodeDoingNow(row).frame(minWidth: 130, maxWidth: .infinity, alignment: .leading)
            resourceCell(text: row.machine?.diskText, tier: row.machine?.diskTier ?? .unknown,
                         fraction: row.machine?.diskFreeFraction, reachable: row.isReachable,
                         total: row.machine?.totalDiskText)
                .frame(width: NodeColumn.meter, alignment: .leading)
            resourceCell(text: row.machine?.memoryText, tier: row.machine?.memoryTier ?? .unknown,
                         fraction: row.machine?.memoryFreeFraction, reachable: row.isReachable)
                .frame(width: NodeColumn.meter, alignment: .leading)
            nodeActions(row).frame(width: NodeColumn.actions, alignment: .trailing)
        }
        .padding(.horizontal, HubSpace.l).padding(.vertical, HubSpace.m)
    }

    /// The narrow layout, for a window too small for one line per Mac: the same cells, stacked.
    private func compactNodeRow(_ row: NativeFleetRow) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            HStack(alignment: .top, spacing: HubSpace.m) {
                nodeIdentity(row).frame(maxWidth: .infinity, alignment: .leading)
                nodeStateCell(row)
            }
            nodeDoingNow(row)
            HStack(alignment: .top, spacing: HubSpace.l) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Disk free").font(HubType.caption).foregroundStyle(.secondary)
                    resourceCell(text: row.machine?.diskText, tier: row.machine?.diskTier ?? .unknown,
                                 fraction: row.machine?.diskFreeFraction, reachable: row.isReachable,
                                 total: row.machine?.totalDiskText)
                }.frame(width: NodeColumn.meter, alignment: .leading)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Memory free").font(HubType.caption).foregroundStyle(.secondary)
                    resourceCell(text: row.machine?.memoryText, tier: row.machine?.memoryTier ?? .unknown,
                                 fraction: row.machine?.memoryFreeFraction, reachable: row.isReachable)
                }.frame(width: NodeColumn.meter, alignment: .leading)
                Spacer(minLength: 0)
                nodeActions(row)
            }
        }
        .padding(.horizontal, HubSpace.l).padding(.vertical, HubSpace.m)
    }

    /// The Mac: its name, its part in the group, what it is, where it answers, what starts with it, and
    /// when it was last heard from.
    private func nodeIdentity(_ row: NativeFleetRow) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: HubSpace.tight) {
                Text(row.name).font(HubType.rowTitle).lineLimit(1).truncationMode(.middle)
                if let role = nodeRoleTitle(row) {
                    StatusBadge(text: role, status: .off)
                }
            }
            if let facts = nodeFactsLine(row) {
                Text(facts).font(HubType.caption).foregroundStyle(.secondary)
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
            HStack(spacing: HubSpace.xs) {
                if let startup = row.startupText {
                    Text(startup).font(HubType.caption)
                        .foregroundStyle(row.startupNeedsAttention ? HubStatus.warn.textColor : Color.secondary)
                    Text("·").font(HubType.caption).foregroundStyle(.secondary)
                }
                Text(nodeSeenText(row)).font(HubType.caption).foregroundStyle(.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
            if let detail = row.node?.detail ?? row.machine?.detail, !row.isReachable {
                Text(detail).font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let readiness = workspace.fleetReadiness[row.id], readiness.cannotRunAnything {
                Text(readiness.summary).font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
            }
            // Each unhealthy state names what a person can do about it, on the row itself.
            switch row.state(readiness: workspace.fleetReadiness[row.id]) {
            case .refused:
                HStack(spacing: HubSpace.s) {
                    Text("It has to rejoin this controller with the current Fleet token.")
                        .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Refresh") { Task { await workspace.refreshFleetNodes() } }.hubRowControl()
                }
            case .offline:
                HStack(spacing: HubSpace.s) {
                    Text("Start that Mac or check its connection, then refresh.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Refresh") { Task { await workspace.refreshFleetNodes() } }.hubRowControl()
                }
            case .error:
                HStack(spacing: HubSpace.s) {
                    Text("It answered with an error; its detail above is the reason.")
                        .font(HubType.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Refresh") { Task { await workspace.refreshFleetNodes() } }.hubRowControl()
                }
            default:
                EmptyView()
            }
            if row.node?.hasCredential == false {
                Text("No credential is held for this Mac, so it has to prove itself on its next join.")
                    .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func nodeStateCell(_ row: NativeFleetRow) -> some View {
        let state = row.state(readiness: workspace.fleetReadiness[row.id])
        // A Mac that is answering but has no room, no credential or a startup that will not come
        // back is not "Ready": the chip takes the worst fact on the row.
        let resourceTrouble = row.machine?.memoryTier == .bad || row.machine?.diskTier == .bad
            || row.startupNeedsAttention || row.node?.hasCredential == false
        let shown: NativeFleetRowState = (state == .ready && resourceTrouble) ? .attention : state
        return StatusBadge(text: shown.title, status: nodeStatus(shown), symbol: nodeSymbol(shown))
    }

    private func nodeStatus(_ state: NativeFleetRowState) -> HubStatus {
        switch state {
        case .working, .ready: return .ok
        case .attention: return .warn
        case .offline, .refused, .error: return .bad
        case .unknown: return .off
        }
    }

    private func nodeSymbol(_ state: NativeFleetRowState) -> String {
        switch state {
        case .working: return "bolt.fill"
        case .ready: return "checkmark.circle.fill"
        case .attention: return "exclamationmark.triangle.fill"
        case .offline, .refused, .error: return "xmark.octagon.fill"
        case .unknown: return "questionmark.circle.fill"
        }
    }

    /// "Image · FLUX.2 Klein 4B MLX 4-bit", with who asked and for how long under it; or what the Mac
    /// last did when it has no job now. A Mac that has not said shows a dash, never a guess.
    @ViewBuilder private func nodeDoingNow(_ row: NativeFleetRow) -> some View {
        if let job = row.currentJob {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(job.modality.map(modalityTitle) ?? "Job") · \(modelName(job.model ?? ""))")
                    .font(HubType.secondary).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                Text(jobAgeLine(job)).font(HubType.caption).foregroundStyle(.secondary)
            }
        } else if row.isReachable, row.machine != nil {
            Text(done(row)).font(HubType.secondary).foregroundStyle(.secondary)
        } else {
            Text(row.isReachable ? "—" : "Not answering")
                .font(HubType.secondary).foregroundStyle(.secondary)
        }
    }

    /// "storystudio · 1 min 35 s", the submitter's own label and how long the job has run.
    private func jobAgeLine(_ job: NativeCurrentJob) -> String {
        var parts: [String] = []
        if let label = job.label, !label.isEmpty { parts.append(label) }
        if let started = job.startedAt, started > 0 {
            parts.append(NativeJob.clock(max(0, Date().timeIntervalSince1970 - started)))
        }
        return parts.isEmpty ? "Running" : parts.joined(separator: " · ")
    }

    /// What an idle Mac has done so far, from the same measured counts the overview always carried.
    private func done(_ row: NativeFleetRow) -> String {
        guard let machine = row.machine else { return "—" }
        let done = doneModalities(machine)
        if machine.jobs == 0 { return "Idle · no completed work" }
        return "Idle · \(machine.jobs) done" + (done.isEmpty ? "" : " (\(done.joined(separator: ", ")))")
    }

    /// A free-space figure as a badge in its tier's colour, with a thin meter under it.
    @ViewBuilder private func resourceCell(text: String?, tier: NativeResourceTier, fraction: Double?,
                                           reachable: Bool, total: String? = nil) -> some View {
        if let text, reachable {
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                StatusBadge(text: text.replacingOccurrences(of: " free", with: ""),
                            status: HubStatus(tier), symbol: tier == .bad ? "xmark.octagon.fill"
                            : tier == .warn ? "exclamationmark.triangle.fill" : nil)
                if let total { Text("of \(total)").font(HubType.caption).foregroundStyle(.secondary) }
                if let fraction { MeterBar(fraction: fraction, status: HubStatus(tier)).frame(width: 60) }
            }
        } else {
            Text("—").font(HubType.secondary).foregroundStyle(.secondary)
        }
    }

    /// One action per Mac (Send Here / Automatic) and a Remove behind the row's own overflow, so a
    /// row is not a pair of competing buttons. A Mac this controller does not own has no action
    /// but its details; that is honest, not an empty control.
    @ViewBuilder private func nodeActions(_ row: NativeFleetRow) -> some View {
        HStack(spacing: HubSpace.xs) {
            if canPinWork, let machine = row.machine {
                let pinned = workspace.routingPin == "machine:\(machine.machine)"
                // No dead button: an offline Mac has no Send Here at all, and the row's advice
                // says to start it. Clearing an existing pin to it is still offered.
                if machine.reachable || pinned {
                    Button(pinned ? "Automatic" : "Send Here") {
                        workspace.routingPin = pinned ? nil : "machine:\(machine.machine)"
                    }
                    .disabled(!connected)
                    .help("Send new Image, Voice, Subtitle and Music work to this Mac until you clear the pin.")
                    .rowAction()
                } else {
                    Color.clear.frame(width: 78, height: 1).accessibilityHidden(true)
                }
            } else if canPinWork {
                // Keep the Send Here column's left edge on every row, including a Mac the roster
                // has no live machine row for.
                Color.clear.frame(width: 78, height: 1).accessibilityHidden(true)
            }
            Menu {
                Button("Details") { fleetDetailRow = row }
                if let node = row.node {
                    Button("Remove \(node.name)…", role: .destructive) {
                        nodeRemovalRefusal = nil
                        pendingNodeRemoval = PendingNodeRemoval(
                            node: node, connectionGeneration: workspace.connectionGeneration)
                    }
                    .disabled(!localServicesReady || !workspace.fleetRosterReachable || workspace.fleetRefreshing)
                }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Details and more actions for \(row.name)")
            .accessibilityLabel("Details and more actions for \(row.name)")
        }
    }

    /// "Controller" or "Node" when the Hub says; this Mac's own row names nothing it is not told.
    private func nodeRoleTitle(_ row: NativeFleetRow) -> String? {
        if let machine = row.machine, let title = fleetRoleTitle(machine) { return title }
        guard let role = row.node?.role, !role.isEmpty else { return nil }
        return role == "agent" ? "Node" : role.capitalized
    }

    /// "Apple M4 · 24 GB · 0.6.126" (the address is its own line under it).
    private func nodeFactsLine(_ row: NativeFleetRow) -> String? {
        var parts: [String] = []
        if let chip = row.machine?.chip, !chip.isEmpty { parts.append(chip) }
        if let memory = row.machine?.totalMemoryText { parts.append(memory) }
        if let version = row.version, !version.isEmpty { parts.append(version) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func nodeSeenText(_ row: NativeFleetRow) -> String {
        if row.isLocal { return "this Mac" }
        if let node = row.node { return nodeSeenLine(node) }
        return row.isReachable ? "answering now" : "not answering"
    }

    /// "as of 14:02:07" with a spinner while a read is in flight. Both fleet sections show it: the page
    /// used to give no sign a Refresh was running, or how old the rows were.
    @ViewBuilder private var fleetRefreshStatus: some View {
        if workspace.fleetRefreshing { ProgressView().controlSize(.small) }
        if let at = workspace.fleetRefreshedAt {
            Text("as of \(at.formatted(date: .omitted, time: .standard))")
                .font(HubType.caption).foregroundStyle(.secondary)
        }
    }

    /// Whether this Mac chooses where work runs. A controller and a Mac on its own do; a Node does
    /// not. The decision itself lives in the store, where the contract suite can hold it to account.
    private var canPinWork: Bool {
        NativeWorkspaceStore.canPinWork(
            role: workspace.fleetOverview?.role,
            credentialOrigin: workspace.controllerIdentity?.settings.fleetCredentialOrigin,
            handedOverTo: workspace.controllerIdentity?.settings.handedOverTo)
    }

    /// "Sending to X · Clear" wherever work is submitted, while a Send here pin is set. The pin is
    /// saved and attached to every submission, and used to be visible only on the Fleet page.
    @ViewBuilder private var pinChip: some View {
        if let pin = workspace.routingPin, !pin.isEmpty {
            let id = pin.hasPrefix("machine:") ? String(pin.dropFirst("machine:".count)) : pin
            let offline = workspace.fleetOverview?.machines.first { $0.machine == id }?.reachable == false
            HStack(spacing: HubSpace.xs) {
                Image(systemName: "pin.fill")
                Text("Sending to \(id == "local" ? "this Mac" : workspace.fleetMachineName(id))\(offline ? " (offline)" : "")")
                Button("Clear") { workspace.routingPin = nil }.buttonStyle(.link)
            }
            .font(HubType.caption).foregroundStyle(offline ? HubStatus.warn.textColor : Color.blue)
            .help("Send here is set on the Fleet page. New work waits for this Mac until you clear the pin.")
        }
    }

    /// Which pool this list is. The rows answer "how is each Mac doing", which is only half the
    /// question on a Mac that joined somebody: it also has to know whose group it is looking at,
    /// and be told plainly when the group is bigger than what it could read.
    private func poolSummary(_ overview: NativeFleetOverview) -> String {
        guard overview.belongsToGroup else {
            if overview.role == "controller", overview.machines.count > 1 {
                return "This Mac runs its own group. Every Mac in the group is listed below."
            }
            return "This Mac is on its own. Nodes that join it appear here."
        }
        let head = overview.controller.map { "the group run by \($0.label)" } ?? "its group"
        guard overview.rosterReadable != false else {
            return "This Mac is part of \(head), but the controller is not answering, so the Macs below are only the ones this Mac knows about."
        }
        return "This Mac is part of \(head). Every Mac in the group is listed below."
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
        return "New work is pinned to \(workspace.fleetMachineName(pin.replacingOccurrences(of: "machine:", with: ""))). Press Automatic to let the Hub choose again."
    }

    private func doneModalities(_ machine: NativeFleetMachine) -> [String] {
        var seen = Set<String>()
        return machine.modalities.filter { $0.value > 0 }.keys.sorted().map(modalityTitle)
            .filter { seen.insert($0).inserted }
    }

    private func modalityTitle(_ modality: String) -> String {
        switch modality {
        case "image": return "Image"
        case "transcription": return "Subtitle"
        case "voice", "speech": return "Voice"
        default: return modality.capitalized
        }
    }

    /// The counts the page has to publish, including the one that used to be invisible: a node that
    /// answered and refused this controller's credential is neither reachable nor off.
    private func fleetSummaryLine(_ fleet: NativeFleetInventory) -> String {
        var parts = ["\(fleet.summary.reachable) of \(fleet.summary.total) reachable now"]
        if let unauthorized = fleet.summary.unauthorized, unauthorized > 0 {
            parts.append("\(unauthorized) refused this controller's credential")
        }
        if fleet.summary.error > 0 {
            parts.append("\(fleet.summary.error) answered with an error")
        }
        return parts.joined(separator: " · ")
    }

    /// The machines this controller has removed. Published rather than hidden, because the only way
    /// to re-admit one used to be remembering its id.
    @ViewBuilder private func removedNodes(_ removed: [NativeFleetRemovedNode]) -> some View {
        VStack(alignment: .leading, spacing: HubSpace.s) {
            Text("Removed from this group").font(HubType.rowTitle)
            Text("These Macs were removed here. They cannot re-join on their own — not even after a restart — until they are re-admitted.")
                .font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HubCard {
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    ForEach(removed) { node in
                        ActionRow {
                            Image(systemName: "minus.circle").foregroundStyle(HubStatus.warn.color)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(node.name).font(HubType.rowTitle)
                                Text(removedFacts(node)).font(HubType.caption).foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                if let address = node.address, !address.isEmpty {
                                    Text(address).font(HubType.caption).foregroundStyle(.secondary)
                                        .textSelection(.enabled)
                                }
                            }
                            RowActions {
                                Button("Re-admit") { Task { await workspace.readmitNode(node) } }
                                    .disabled(!connected)
                                    .rowAction()
                            }
                        }
                    }
                }
            }
        }
    }

    private func removedFacts(_ node: NativeFleetRemovedNode) -> String {
        var parts: [String] = []
        parts.append(node.removedAt.map { "removed \(NativeWorkspaceStore.moment($0))" }
                     ?? "removed on a date the Hub did not record")
        if let by = node.removedBy, !by.isEmpty {
            parts.append(by == "operator" ? "by this controller's operator" : "by the Mac itself")
        }
        if let role = node.role, !role.isEmpty { parts.append(role == "agent" ? "was a node" : role) }
        return parts.joined(separator: " · ")
    }

    /// "last seen 4 min ago", or the honest alternative when the Hub has no stamp for it.
    private func nodeSeenLine(_ node: NativeFleetNode) -> String {
        if node.neverConnected == true { return "never connected since it joined" }
        guard let seen = NativeWorkspaceStore.ago(node.lastSeenAt) else {
            return node.isReachable ? "seen just now" : "never seen"
        }
        return "last seen \(seen)"
    }

    /// What a node should read where a controller reads its roster: whose group it is in, and how
    /// to leave. A node has no nodes of its own, so the honest answer is the group above it.
    private func nodeMembershipSummary(_ overview: NativeFleetOverview) -> String {
        guard let controller = overview.controller else {
            return "This Mac is set as a Node, but it has not been joined to a controller yet. Use Join a controller below."
        }
        let peers = overview.machines.filter { !$0.isLocal && $0.machine != controller.machine }.count
        let reach = controller.isReachable ? "answering" : "not answering right now"
        let beside = peers == 0 ? "" : " \(peers) other Mac\(peers == 1 ? " shares" : "s share") the group."
        return "No Mac joins this one: this Mac is a Node in the group run by \(controller.label) at "
            + "\(controller.address ?? "an address this Mac did not record"), which is \(reach)."
            + "\(beside) The whole group is listed in the table below."
    }

    /// Join another Mac that is running as a controller.
    @ViewBuilder private var joinController: some View {
        HubSection("Join a controller") {
            // Where this Mac stands, right here rather than only in a banner at the top of the
            // page. The owner joined successfully and could not tell: the result was a line of
            // grey text that looked the same whether it had worked or not.
            HStack(alignment: .top, spacing: HubSpace.s) {
                Circle()
                    .fill(connectionStateTint)
                    .frame(width: 10, height: 10)
                    .padding(.top, 5)
                VStack(alignment: .leading, spacing: 2) {
                    Text(connectionStateTitle).font(HubType.rowTitle)
                    Text(workspace.controllerHealthNote ?? "Checking the controller…")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(HubSpace.m)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(connectionStateTint.opacity(0.10), in: RoundedRectangle(cornerRadius: HubRadius.card))
            Text("Enter the controller's address and its Fleet token — the one that Mac marks “Give this out”. On the controller, open Connections and copy it from there.")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("There is only one token to hand out, so there is nothing to choose between. (The Hub token on that same page is the controller's own key: a Mac keeps a copy of whatever it is given, so keep that one to yourself.)")
                .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("The address is the controller's Tailscale or LAN address; the port is optional and assumed to be 47873. `http://` is optional too.")
                .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("This Mac registers with the controller and adopts that token, so the controller can send it work. The controller then reports whether this Mac is reachable.")
                .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                TextField("100.x.y.z  ·  or a name like mac.tailnet.ts.net", text: $controllerDraft)
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                CopyValueButton(value: controllerDraft)
                    .accessibilityLabel("Copy controller address")
                // Masked like every secret, with the eye to check what was pasted so a wrong copy is
                // obvious (the owner's reason this field used to be plain text).
                SecretInput(placeholder: "Controller's Fleet token", text: $controllerToken)
                    .frame(maxWidth: 300)
                CopyValueButton(value: controllerToken)
                    .accessibilityLabel("Copy controller Fleet token")
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
            Text("Neither the address nor the token can contain a space, so pasted spaces and newlines are removed for you. Press the eye to check the token you pasted.")
                .font(HubType.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            // Kept inline as well as in the banner at the top: this is the spot the operator is
            // looking at the moment they press Join, so the answer belongs here too - and it is
            // coloured, because a success and a failure used to read identically in grey.
            if let note = workspace.fleetNote {
                Text(note)
                    .font(HubType.secondary)
                    .foregroundStyle(workspace.connectionNotice?.kind == .failure ? HubStatus.warn.textColor : HubStatus.ok.textColor)
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

    /// Green when this Mac is joined and the controller answers with a credential it accepts, amber
    /// when it is joined but not answering or its credential was refused, grey when it is simply on
    /// its own. One dot, so the state is readable at a glance without hunting for a sentence.
    ///
    /// **A refused credential is amber, never green.** The read behind this is authenticated, so a
    /// controller that will not dispatch to this Mac cannot be shown as connected — which is what
    /// `GET /api/health` did, because it answers 200 without a credential.
    private var connectionStateTint: Color {
        if workspace.controllerRemoval != nil { return HubStatus.bad.color }
        if workspace.controllerCredentialRefused { return HubStatus.warn.color }
        guard let reachable = workspace.controllerReachable else { return HubStatus.off.color }
        return reachable ? HubStatus.ok.color : HubStatus.warn.color
    }

    private var connectionStateTitle: String {
        if workspace.controllerRemoval != nil { return "Removed from the group" }
        guard let reachable = workspace.controllerReachable else { return "Not joined to a controller" }
        if workspace.controllerCredentialRefused { return "Credential refused" }
        return reachable ? "Connected" : "Joined, but not answering"
    }

    /// The status of the mode badge: a controller is a fact (blue), a node takes the connection's state,
    /// and a Mac on its own stays quiet.
    private var modeStatus: HubStatus {
        switch workspace.controllerIdentity?.settings.role {
        case "controller": return .off
        case "agent": return workspace.controllerReachable == true ? .ok : .warn
        default: return .off
        }
    }

    /// This Mac's own name. The label the owner set is authoritative (the Hub reports it as
    /// `machine_name`, and falls back to the hostname itself when none is set); the computer's name
    /// is only a last resort for a Hub that has not answered.
    private var thisMachineName: String {
        let label = workspace.controllerIdentity?.settings.machineName
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return label.isEmpty ? (Host.current().localizedName ?? "This Mac") : label
    }

    /// Where this Mac stands: "Node · joined <controller>" (with "(offline)" when that controller is
    /// not answering — the name is the last one the roster gave), "Controller · 3 nodes", or
    /// "Standalone". The controller's name is its own label from the overview this Mac already reads,
    /// and the address it was joined at only if that has never been read.
    private var groupLine: String {
        switch workspace.controllerIdentity?.settings.role {
        case "controller":
            // Once per connection the roster is read (the task on the main view), so "fleet not
            // read" is a moment, not a state: while it is being read that is what it says, and a
            // failure says the nodes were not read rather than claiming a count of zero.
            guard let nodes = workspace.fleetNodes?.nodes else {
                if workspace.fleetRefreshing { return "Controller · reading nodes…" }
                if workspace.fleetReadError != nil { return "Controller · nodes not read" }
                return "Controller"
            }
            // This Mac is in the group too, and Connections counts it; the footer cannot say one
            // fewer than the table (critic, 2026-11-13).
            let count = nodes.count + 1
            // "Macs", not "nodes": the Overview's job filter also counts "Nodes", meaning Macs that
            // are running work, and two different node counts on one screen read as a contradiction
            // (page critic, 2026-11-12). This line counts the Macs on the controller's roster.
            // The role pill already says Controller; repeating it here was the owner's
            // "nothing repeats itself" failure (footer critic, 2026-11-13).
            return "\(count) Mac\(count == 1 ? "" : "s")"
        case "agent":
            let address = workspace.controllerAddress.trimmingCharacters(in: .whitespacesAndNewlines)
            let name = workspace.fleetOverview?.controller?.label
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let shown = (name?.isEmpty == false ? name : nil) ?? (address.isEmpty ? nil : address)
            guard let shown else { return "Node · not joined" }
            return "Node · joined \(shown)" + (workspace.controllerReachable == false ? " (offline)" : "")
        case "standalone": return "Standalone"
        default: return "Not reporting"
        }
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

    /// A manual address choice — ticking one, unticking one, or the wildcard — ends automatic mode.
    ///
    /// Without this the operator unticks Tailscale, the preference is saved, and the next launch
    /// recomputes the list and puts it back: an edit that silently undoes itself is worse than
    /// either default. So a manual choice pins exactly what is ticked at that moment.
    private func pinListenHosts(_ hosts: [String]) {
        studio.configuration.listenHostAutomatic = false
        studio.setListenHosts(hosts)
    }

    /// Turn "follow this Mac's networks" on or off, saving the choice.
    ///
    /// Turning it on recomputes the list now — this Mac's current private and Tailscale addresses,
    /// added to whatever was already saved, so it can never remove a way in that worked. Turning
    /// it off changes nothing about the list: it pins exactly the addresses on screen.
    private func setListenAutomatic(_ automatic: Bool) {
        studio.configuration.listenHostAutomatic = automatic
        if automatic {
            studio.configuration.listenHost = ShellConfiguration.automaticListenHost(
                keeping: studio.configuration.listenHost)
        }
        studio.saveConfiguration()
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
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        case "0.0.0.0":
            Text("Any device on any network this Mac joins — including café or hotel Wi-Fi — can reach the Hub and try to send it work. They still need the token, but the token travels in the clear unless you are on Tailscale. Prefer a named address.")
                .font(HubType.secondary).foregroundStyle(HubStatus.warn.textColor).fixedSize(horizontal: false, vertical: true)
        default:
            Text("Only devices that can reach this one address can use the Hub. A Tailscale address is the intended choice: it is encrypted and only your own machines can reach it.")
                .font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        if !studio.canConfigure {
            Text("Stop the services to change the addresses, then start them again. Changing them restarts the Hub.")
                .font(HubType.secondary).foregroundStyle(.secondary)
        }
    }

    /// Which warning to show: loopback, the wildcard, or a named list.
    private var listenWarningKey: String {
        let hosts = studio.configuration.listenHosts.filter { $0 != "127.0.0.1" }
        if hosts.isEmpty { return "127.0.0.1" }
        return hosts.contains("0.0.0.0") ? "0.0.0.0" : hosts.joined(separator: ",")
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
                .font(HubType.secondary).foregroundStyle(.secondary)
        }
        if let note = workspace.memoryNote {
            Text(note).font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
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
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            HStack(spacing: HubSpace.s) {
                Text(row.title).font(HubType.rowTitle)
                if !row.isReady {
                    Text(memoryStateLabel(row.state)).font(HubType.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Text(memoryResidency(row)).font(HubType.caption).foregroundStyle(.secondary)
            }
            if row.isReady, let policy = row.policy {
                if policy.isFixed {
                    Text(policy.releasePolicy ?? "The model is released when each job finishes.")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                } else {
                    HubSegmentedControl(
                        selection: Binding(
                            get: { policy.mode },
                            set: { value in Task { await workspace.setMemoryMode(value, studioIDs: [row.id]) } }
                        ),
                        items: options.map { option in
                            HubSegmentItem(
                                tag: option.mode,
                                label: option.mode == policy.defaultMode ? "\(option.label) (default)" : option.label
                            )
                        },
                        accessibilityLabel: "Release policy"
                    )
                    .disabled(!connected || workspace.updatingMemory)
                    // The Studio's own sentence for the selected mode, which is the only one that
                    // knows what `auto` resolved to on this Mac. The list above is the Studio's
                    // published options too, so a fifth mode appears here without an edit.
                    Text(options.first { $0.mode == policy.mode }?.summary ?? "")
                        .font(HubType.secondary).foregroundStyle(.secondary)
                    // ...and when the selection resolved to a different mode than its own name,
                    // both are named: "Auto resolves to Balanced on this Mac". Both labels come
                    // from the Studio's payload, so the machine rule is never re-derived here.
                    if let resolution = policy.resolution {
                        Text(resolution).font(HubType.caption).foregroundStyle(.secondary)
                    }
                }
            } else if let detail = row.detail, !detail.isEmpty {
                Text(detail).font(HubType.secondary).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 2)
    }

    /// macOS's own Trash setting, beside the folder that feeds it (owner request 2026-10-19).
    ///
    /// Finder > Settings > Advanced > "Remove items from the Trash after 30 days". Everything the Hub
    /// retires ends in macOS Trash, so without this the disk fills until the owner empties it by hand -
    /// and across the fleet nobody could tell which Mac had it. **One way only**: the Hub can turn it on
    /// and has no route that turns it off, so this is a button, never a switch to flick back. `nil` is a
    /// Hub too old to report it, said as such rather than drawn as off.
    @ViewBuilder private var macosTrashSetting: some View {
        if let folder = workspace.trashFolder {
            let state = folder.macosTrashAutoEmpty
            ValueRow("Remove Trash items after 30 days") {
                HStack(spacing: HubSpace.s) {
                    Text(state == true ? "On" : state == false ? "Off" : "Not reported")
                        .foregroundStyle(state == false ? HubStatus.warn.textColor : .secondary)
                    if state == false {
                        Button("Turn on") { Task { await workspace.enableMacosTrashAutoEmpty() } }
                            .disabled(!connected || workspace.turningOnTrashAutoEmpty)
                        if workspace.turningOnTrashAutoEmpty { ProgressView().controlSize(.small) }
                    }
                }
            }
            Text("A macOS setting, not Hub Studio KH's. Files moved to macOS Trash are removed by Finder "
                 + "after 30 days, which is what stops that Trash from filling the disk.")
                .font(HubType.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
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
            ValueRow("Size limit", value: NativeWorkspaceStore.gigabytes(settings.effective.maxBytes))
            ValueRow("Move to Trash after",
                     value: "\(settings.effective.retentionDays) days or \(NativeWorkspaceStore.gigabytes(settings.effective.maxBytes))")
            ValueRow("Send Trash to macOS Trash after",
                     value: "\(settings.effective.trashHandoffDays ?? 7) days or \(settings.effective.trashHandoffGb ?? 20) GB")
            ValueRow("Automatic cleanup", value: settings.effective.cleanupEnabled ? "On" : "Off")
            if let folder = workspace.trashFolder {
                ValueRow("Trash Folder", value: "\(folder.count) item\(folder.count == 1 ? "" : "s") · \(NativeWorkspaceStore.gigabytes(folder.bytes))")
                ValueRow("Location") {
                    Text(folder.trashFolder).font(HubType.mono).textSelection(.enabled).multilineTextAlignment(.trailing)
                }
                HStack {
                    Button("Open Trash Folder") {
                        NSWorkspace.shared.open(URL(fileURLWithPath: folder.trashFolder))
                    }
                    Button("Move Contents to macOS Trash…") { confirmingTrashMove = true }
                        .disabled(!connected || workspace.movingTrash || folder.count == 0)
                    if workspace.movingTrash { ProgressView().controlSize(.small) }
                }
                .confirmationDialog(
                    "Move the Trash Folder contents to macOS Trash?",
                    isPresented: $confirmingTrashMove, titleVisibility: .visible) {
                        Button("Move \(folder.count) item\(folder.count == 1 ? "" : "s") (\(NativeWorkspaceStore.gigabytes(folder.bytes))) to macOS Trash") {
                            Task { await workspace.moveTrashToMacOS() }
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("This uses macOS Trash, so anything moved can still be put back. The Trash Folder itself stays; empty macOS Trash when you want the space back.")
                    }
                ForEach(folder.roots.filter { !($0.primary ?? false) && $0.count > 0 }) { root in
                    Text("\(root.path): \(root.count) item\(root.count == 1 ? "" : "s"), \(NativeWorkspaceStore.gigabytes(root.bytes))"
                         + (root.crossVolume == true ? " (on another volume; move these from Finder)" : ""))
                        .font(HubType.caption).foregroundStyle(.secondary)
                }
                if let note = workspace.trashNote {
                    Text(note).font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            } else if let trash = workspace.storageTrash {
                ValueRow("Trash Folder", value: "\(NativeWorkspaceStore.gigabytes(trash.bytes)), items older than \(settings.effective.trashHandoffDays ?? 7) days or beyond \(settings.effective.trashHandoffGb ?? 20) GB go to macOS Trash")
                Button("Show Trash Folder in Finder") { NSWorkspace.shared.activateFileViewerSelecting([HubTrash.live]) }
            }
            macosTrashSetting
            ForEach(workspace.storageUsage) { usage in
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    HStack {
                        Text(usage.machine == "local" ? "This Mac" : workspace.fleetMachineName(usage.machine)).font(HubType.rowTitle)
                        Spacer()
                        Text(NativeWorkspaceStore.gigabytes(usage.usedBytes)).font(HubType.mono)
                    }
                    Text("Generated output: \(NativeCapacityDisplay.gigabytes(bytes: Double(usage.usedBytes))), of which \(NativeCapacityDisplay.gigabytes(bytes: Double(usage.protectedBytes))) is protected and never deleted"
                         + (usage.freeBytes.map { " · \(NativeCapacityDisplay.gigabytes(bytes: Double($0))) free on disk" } ?? ""))
                        .font(HubType.caption).foregroundStyle(.secondary)
                    if usage.overLimit {
                        Text("Over the size limit. Cleaning up is possible, but only work that is finished and acknowledged.")
                            .font(HubType.caption).foregroundStyle(HubStatus.warn.textColor)
                    }
                }.padding(.vertical, 2)
            }
            HStack {
                Button("Change Limits…") {
                    draftGigabytes = Double(settings.effective.maxBytes) / 1_000_000_000
                    draftRetentionDays = settings.effective.retentionDays
                    draftCleanup = settings.effective.cleanupEnabled
                    draftTrashHandoffDays = settings.effective.trashHandoffDays ?? 7
                    draftTrashHandoffGb = settings.effective.trashHandoffGb ?? 20
                    showingStorageEditor = true
                }
                .disabled(!connected || workspace.savingStorage)
                Button("Restore Defaults…") { confirmingRestoreStorage = true }
                    .disabled(!connected || workspace.savingStorage)
                    .confirmationDialog(
                        "Restore the shipped storage limits?",
                        isPresented: $confirmingRestoreStorage, titleVisibility: .visible) {
                            Button("Restore Defaults", role: .destructive) {
                                Task { await workspace.restoreStorageDefaults() }
                            }
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("This replaces your limits (\(NativeWorkspaceStore.gigabytes(settings.effective.maxBytes)), \(settings.effective.retentionDays) days, cleanup \(settings.effective.cleanupEnabled ? "on" : "off")) with the shipped ones. If they are lower, the next cleanup moves finished, acknowledged work to the Trash Folder down to them.")
                        }
                if workspace.savingStorage { ProgressView().controlSize(.small) }
            }
            if let note = workspace.storageNote {
                Text(note).font(HubType.secondary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        } else {
            Text(connected ? "Storage settings are unavailable in this build." : "Start local services to read and change storage limits.")
                .font(HubType.secondary).foregroundStyle(.secondary)
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
        let requestConnection = workspace.connectionGeneration
        Task {
            do {
                guard connected, workspace.connectionGeneration == requestConnection else {
                    fileActionError = "The Hub connection changed before image export. Save the image again from the current connection."
                    return
                }
                let mediaType = job.mediaType?.split(separator: ";").first?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                let jpeg: Bool
                if let mediaType, !mediaType.isEmpty {
                    jpeg = mediaType == "image/jpeg"
                } else {
                    let data = try await workspace.imageData(for: job)
                    jpeg = data.starts(with: [0xff, 0xd8, 0xff])
                }
                guard connected, workspace.connectionGeneration == requestConnection else {
                    fileActionError = "The Hub connection changed before image export. Save the image again from the current connection."
                    return
                }
                let panel = NSSavePanel()
                panel.nameFieldStringValue = "image-\(job.id).\(jpeg ? "jpg" : "png")"
                panel.allowedContentTypes = [jpeg ? .jpeg : .png]
                guard panel.runModal() == .OK, let url = panel.url else { return }
                guard connected, workspace.connectionGeneration == requestConnection else {
                    fileActionError = "The Hub connection changed before image export. Save the image again from the current connection."
                    return
                }
                try await workspace.saveImage(job, to: url)
            } catch { fileActionError = error.localizedDescription }
        }
    }
    /// The extension the Hub's own record names (an M4A saved as ".wav" would not open).
    static func audioFileExtension(_ job: NativeJob) -> String {
        switch (job.mediaType ?? "").lowercased() {
        case "audio/mp4", "audio/x-m4a", "audio/aac": return "m4a"
        case "audio/mpeg", "audio/mp3": return "mp3"
        case "audio/flac", "audio/x-flac": return "flac"
        case "audio/ogg", "audio/opus": return "ogg"
        default: return "wav"
        }
    }
    private func saveAudio(_ job: NativeJob) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "audio-\(job.id).\(Self.audioFileExtension(job))"; panel.allowedContentTypes = [.audio]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        // The bytes are verified against the Hub's receipt before the write, then the write is
        // re-read, and only then is delivery acknowledged - the same order the Image and
        // Subtitle saves use. Acknowledgement is what releases the protected copy.
        Task {
            do { try await workspace.saveAudio(job, to: url) }
            catch { fileActionError = error.localizedDescription }
        }
    }
    /// Copying is the whole point of an answer: it is on its way somewhere else.
    private func copyAnswer(_ job: NativeJob) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(job.answer, forType: .string)
    }
    /// Save the answer the Hub recorded.
    ///
    /// There is no artifact to fetch and nothing to acknowledge: a Chat worker streams its
    /// completion and keeps no file, so the Hub records the answer itself as the item's text, and
    /// the bytes written here are the bytes on screen.
    private func saveAnswer(_ job: NativeJob) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "chat-\(job.id).txt"
        panel.allowedContentTypes = [.plainText]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do { try job.answer.write(to: url, atomically: true, encoding: .utf8) }
        catch { fileActionError = error.localizedDescription }
    }
    private func saveMusic(_ job: NativeJob) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "music-\(job.id).\(Self.audioFileExtension(job))"; panel.allowedContentTypes = [.audio]
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
    /// An in-app dialog, never a native alert (owner gate, 2026-11-13): the question and both
    /// answers are part of the page, so the window cannot lose them.
    private func confirmRemove(_ model: NativeModel) { pendingModelRemoval = model }
}

/// One Mac's diagnostic details, opened from its row's overflow. Plain values, selectable, no raw
/// JSON and no id as a title.
private struct FleetRowDetailSheet: View {
    let row: NativeFleetRow
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: HubSpace.l) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: HubSpace.xs) {
                    Text(row.name).font(HubType.section)
                    Text(row.node?.role == "agent" ? "Node" : (row.node?.role?.capitalized ?? "This Mac"))
                        .font(HubType.secondary).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                if let nodeId = row.node?.id, nodeId != "local" {
                    DiagnosticValue(label: "ID", value: nodeId)
                }
                DiagnosticValue(label: "Address", value: row.address)
                DiagnosticValue(label: "Version", value: row.version)
                if let node = row.node {
                    DiagnosticValue(label: "Joined", value: node.joinedAt.map { NativeWorkspaceStore.moment($0) })
                    DiagnosticValue(label: "Last seen", value: node.lastSeenAt.map { NativeWorkspaceStore.moment($0) })
                }
                if let detail = row.node?.detail ?? row.machine?.detail {
                    DiagnosticValue(label: "Reported", value: detail)
                }
            }
            .padding(HubSpace.l)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(HubSurface.card, in: RoundedRectangle(cornerRadius: HubRadius.card))
            .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(HubSurface.border, lineWidth: 1))
        }
        .padding(HubSpace.xl)
        .frame(width: 460)
    }
}

/// The sidebar's one line about startup: shown only when this Mac is in a fleet and will not bring its
/// services up after a restart. Such a Mac is silent to its controller until somebody opens it, which
/// is the thing nobody notices, so it says so beside the status and offers the one click that fixes it.
struct StartupSidebarNote: View {
    @ObservedObject var studio: StudioController
    var body: some View {
        if studio.servicesWillNotAutoStart {
            Button { studio.setStartServicesAtLogin(true) } label: {
                Label("Services won't start after restart. Turn on", systemImage: "exclamationmark.triangle.fill")
                    .font(HubType.caption).multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .buttonStyle(.plain).foregroundStyle(HubStatus.warn.textColor)
            .help("This Mac belongs to a fleet but \"Start services on startup\" is off. Click to turn it on.")
            .accessibilityLabel("Services will not start after a restart. Turn on Start services on startup.")
        }
    }
}

/// One small line in the sidebar footer per Studio that has stopped answering.
///
/// **This is where the old top-of-page banner went.** The banner said *"A studio did not answer"*
/// without naming it, appeared for Studios the owner had switched off, and never cleared. Owner,
/// 2026-10-28: *"can we have it like in a notification icon somewhere instead?"* A warning icon and
/// a short line sit where "Local services ready" lives; the hover and the click carry the detail,
/// and the line leaves by itself as soon as the Studio answers.
struct StudioTroubleNote: View {
    let trouble: [StudioTrouble]
    @State private var showing: String?

    var body: some View {
        ForEach(trouble) { line in
            Button {
                showing = showing == line.id ? nil : line.id
            } label: {
                HStack(alignment: .top, spacing: HubSpace.tight) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(HubStatus.warn.textColor)
                    Text(line.headline)
                        .font(HubType.caption)
                        .foregroundStyle(HubStatus.warn.textColor)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
            .buttonStyle(.plain)
            .help(line.detail)
            .accessibilityLabel("\(line.headline). \(line.detail)")
            .popover(isPresented: Binding(get: { showing == line.id },
                                          set: { if !$0 { showing = nil } })) {
                Text(line.detail)
                    .font(HubType.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: 320, alignment: .leading)
                    .padding(HubSpace.m)
            }
        }
    }
}

/// The local services' failure as one notification icon, with the actual message kept whole.
///
/// Owner, 2026-10-28: *"can we have it like in a notification icon somewhere instead?"* The
/// full-width top banner said the same sentence on every page, so the icon sits beside the
/// status line it explains. The hover and the click carry the operator's own failure text,
/// verbatim, and Dismiss - the one control the banner offered - stays in the popover.
struct StartupFailureIcon: View {
    let detail: String?
    let onDismiss: () -> Void
    @State private var showing = false

    /// The accessible name carries the whole failure: the icon is all VoiceOver has, and opening
    /// the popover must not be the only way to hear what went wrong.
    static func label(for detail: String) -> String {
        "Local services need attention. \(detail)"
    }

    var body: some View {
        if let detail {
            Button { showing.toggle() } label: {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(HubStatus.warn.textColor)
            }
            .buttonStyle(.plain)
            .help(detail)
            .accessibilityLabel(Self.label(for: detail))
            .popover(isPresented: $showing) {
                VStack(alignment: .leading, spacing: HubSpace.m) {
                    Text(detail)
                        .font(HubType.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(width: 320, alignment: .leading)
                    Button("Dismiss") {
                        showing = false
                        onDismiss()
                    }
                    .hubRowControl()
                }
                .padding(HubSpace.m)
            }
        }
    }
}
