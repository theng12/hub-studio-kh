import AppKit
import SwiftUI
import AVFoundation
import UniformTypeIdentifiers
import ServiceManagement

@MainActor
final class PreviewPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published var playing = false
    private var player: AVAudioPlayer?
    func play(_ data: Data) throws {
        player?.stop()
        player = try AVAudioPlayer(data: data)
        player?.delegate = self
        player?.play()
        playing = true
    }
    func stop() { player?.stop(); playing = false }
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) { Task { @MainActor [weak self] in self?.playing = false } }
}

struct StudioWindow: View {
    @ObservedObject var studio: StudioController
    var body: some View { StudioContent(studio: studio, workspace: studio.workspace) }
}

/// Tabs for the unified history. Owner requirement (2026-09-15): each generation page keeps its
/// own history, and the shared history is presented with tabs rather than one flat scroll.
enum HistoryTab: String, CaseIterable, Identifiable {
    case all = "All"
    case image = "Image"
    case audio = "Audio"
    case subtitle = "Subtitle"
    var id: String { rawValue }
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
    @State private var historyTab = HistoryTab.all
    @State private var loadingImage = false
    @State private var revealedToken = false
    @State private var connectionNote: String?
    /// 0 means all time; otherwise the window in hours.
    @State private var statisticsWindow = 0
    @State private var showingStorageEditor = false
    @State private var draftGigabytes = 50.0
    @State private var draftRetentionDays = 30
    @State private var draftCleanup = true


    private var connected: Bool { studio.readyURL != nil && !studio.stopping && workspace.isReachable }
    private var selectedAudioModel: NativeModel? { workspace.models.first { $0.repo == workspace.audioModel && $0.function == .audio } }
    private var selectedTranscriptionModel: NativeModel? { workspace.models.first { $0.repo == workspace.transcriptionModel && $0.function == .transcription } }
    private var selectedImageModel: NativeModel? { workspace.models.first { $0.repo == workspace.imageModel && $0.function == .image } }
    private var previewImageJob: NativeJob? { workspace.imageJobs.first { $0.state == "done" && (selectedImageID == nil || $0.id == selectedImageID) } }
    private var activeImages: [NativeJob] { workspace.imageJobs.filter { active($0.state) } }
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

    var body: some View {
        HStack(spacing: 0) {
            List(selection: Binding<StudioPage?>(get: { studio.selection }, set: { if let value = $0 { studio.select(value) } })) {
                Label("Overview", systemImage: StudioPage.overview.symbol).tag(StudioPage.overview)
                Section("Create") {
                    ForEach(StudioPage.workspaces) { page in Label(page.title, systemImage: page.symbol).tag(page) }
                }
                Section("Library & app") {
                    ForEach(StudioPage.management) { page in Label(page.title, systemImage: page.symbol).tag(page) }
                }
            }
            .listStyle(.sidebar)
            .safeAreaInset(edge: .bottom) {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: connected ? "checkmark.circle.fill" : "circle").foregroundStyle(connected ? Color.green : Color.secondary)
                    Text(studio.status).font(.caption).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }.padding(16)
            }
            .frame(width: 210)
            Divider()
            VStack(spacing: 0) {
                if let error = studio.failure ?? workspace.error ?? fileActionError {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text(error).textSelection(.enabled)
                        Spacer()
                        Button("Dismiss") { studio.failure = nil; workspace.error = nil; fileActionError = nil }
                    }.font(.callout).padding(14).background(Color(nsColor: .controlBackgroundColor))
                    Divider()
                }
                switch studio.selection {
                case .overview: overview
                case .audio: audioWorkspace
                case .image: imageWorkspace
                case .transcription: transcriptionWorkspace
                case .models: modelsWorkspace
                case .jobs: jobsWorkspace
                case .connections: connections
                case .settings: settings
                default: unavailableWorkspace
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity).background(Color(nsColor: .windowBackgroundColor))
        }
        .frame(minWidth: 1040, minHeight: 680)
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
    private var setup: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(studio.hasLibrary ? "Start your local services" : "Choose where your models live").font(.title3.weight(.semibold))
            Text(studio.hasLibrary ? "Your model library is selected. Start the services to create assets and manage models." : "Keep models on this Mac or an external drive. Your settings and generated files stay separate from the app.").foregroundStyle(.secondary)
            HStack {
                if studio.starting || studio.stopping { ProgressView().controlSize(.small); Text(studio.status) }
                else {
                    if studio.hasLibrary { Button("Start Services") { studio.start() }.buttonStyle(.borderedProminent).disabled(!studio.canConfigure) }
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
                if activeAudio.isEmpty && activeImages.isEmpty && activeDownloads.isEmpty && !workspace.transcribing {
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
                    if workspace.transcribing { transcriptionActivity }
                    ForEach(activeDownloads) { download in downloadRow(download) }
                }
                Divider()
                sectionTitle("Workspaces")
                VStack(spacing: 0) {
                    ForEach(StudioPage.workspaces) { page in
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
                        if page != .chat { Divider() }
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
        .task { if connected { await workspace.refreshStatistics(hours: statisticsWindow) } }
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

    private func functionSymbol(_ modality: String?) -> String {
        switch modality {
        case "image": return "photo"
        case "transcription": return "waveform"
        case "speech", "voice": return "mic"
        default: return "clock"
        }
    }

    private func seconds(_ value: Double?) -> String {
        guard let value else { return "not measured" }
        return value >= 60 ? String(format: "%.1f min", value / 60) : String(format: "%.1f s", value)
    }
    private func workspaceStatus(_ page: StudioPage) -> String {
        guard page == .audio || page == .transcription || page == .image else { return "Engine not installed" }
        guard connected else { return "Services stopped" }
        if workspace.refreshing && workspace.models.isEmpty { return "Checking models…" }
        return workspace.models.contains { $0.function == page.function && $0.supported && $0.cached && $0.runtimeReady } ? "Ready to create" : "Needs a model"
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

    private var imageWorkspace: some View {
        VStack(alignment: .leading, spacing: 20) {
            heading("Image Studio", "Describe an image, choose your settings and generate it locally.")
            if !connected { setup }
            HSplitView {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Prompt").font(.headline)
                        TextEditor(text: $workspace.imagePrompt)
                            .font(.body).padding(8).frame(height: 155)
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                            .accessibilityLabel("Image prompt")
                        HStack {
                            if workspace.submittingImage { ProgressView().controlSize(.small) }
                            Spacer()
                            Button("Generate Image") { selectedImageID = nil; Task { await workspace.generateImage() } }
                                .buttonStyle(.borderedProminent)
                                .disabled(!connected || workspace.submittingImage || workspace.imagePrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selectedImageModel?.supported != true || selectedImageModel?.runtimeReady != true || selectedImageModel?.cached != true)
                        }
                        Divider()
                        modelPicker(.image)
                        if let configuration = workspace.imageConfiguration, let values = workspace.imageSettings {
                            imageControls(configuration, values)
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
                    Group {
                        if loadingImage { ProgressView("Loading image…").frame(maxWidth: .infinity, maxHeight: .infinity) }
                        else if let imagePreview {
                            Image(nsImage: imagePreview).resizable().scaledToFit()
                                .accessibilityLabel(previewImageJob?.text ?? "Generated image")
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        } else { empty("Your image will appear here", "Enter a prompt and generate an image. Completed images stay in Jobs.", symbol: "photo") }
                    }.frame(minHeight: 200).background(Color(nsColor: .controlBackgroundColor))
                    if let job = previewImageJob { Text(job.text).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                    Divider()
                    HStack { Text("Recent images").font(.headline); Spacer(); Button("All Jobs") { studio.select(.jobs) } }
                    ScrollView { LazyVStack(alignment: .leading, spacing: 12) { ForEach(workspace.imageJobs.prefix(5)) { imageJobRow($0) } } }
                        .frame(minHeight: 80, maxHeight: 150)
                }.padding(.leading, 20).frame(minWidth: 320)
            }
        }.padding(28)
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

    private func imageControls(_ configuration: NativeImageConfiguration, _ values: NativeImageSettings) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Picker("Image size", selection: Binding(
                get: { configuration.sizes.first { $0.width == workspace.imageSettings?.width && $0.height == workspace.imageSettings?.height }?.id ?? "" },
                set: { id in
                    guard let size = configuration.sizes.first(where: { $0.id == id }) else { return }
                    workspace.imageSettings?.width = size.width; workspace.imageSettings?.height = size.height
                })) {
                    ForEach(configuration.sizes) { size in Text("\(size.label) · \(size.width) × \(size.height)").tag(size.id) }
                }
            Stepper(value: Binding(get: { workspace.imageSettings?.steps ?? values.steps }, set: { workspace.imageSettings?.steps = $0 }),
                    in: configuration.limits.minSteps...configuration.limits.maxSteps) { Text("Steps: \(workspace.imageSettings?.steps ?? values.steps)") }
            HStack {
                Text("Guidance")
                Spacer()
                TextField("Guidance", value: Binding(get: { workspace.imageSettings?.guidance ?? values.guidance }, set: { workspace.imageSettings?.guidance = $0 }), format: .number)
                    .textFieldStyle(.roundedBorder).frame(width: 95)
            }
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text("Seed")
                    Spacer()
                    TextField("Seed", value: Binding(get: { workspace.imageSettings?.seed ?? values.seed }, set: { workspace.imageSettings?.seed = $0 }), format: .number.grouping(.never))
                        .textFieldStyle(.roundedBorder).frame(width: 145)
                }
                Text("Use −1 for a new random seed.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func imageJobRow(_ job: NativeJob) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(2)
                    Text("\(modelName(job.model)) · \(stateLabel(job.state))").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if job.state == "done" {
                    Button("Preview") { selectedImageID = job.id; studio.select(.image) }
                    Button("Save…") { saveImage(job) }.disabled(!connected)
                } else if job.state == "uncertain" {
                    Button("Check Original Result") { Task { await workspace.reconcileImage(job) } }.disabled(!connected)
                } else if active(job.state) {
                    Button("Cancel") { Task { await workspace.cancelImage(job) } }.disabled(!connected)
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
                VStack(alignment: .leading, spacing: 12) {
                    Text("Script").font(.headline).fixedSize()
                    TextEditor(text: $workspace.audioText)
                        .font(.body).padding(8).background(Color(nsColor: .textBackgroundColor))
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                        .accessibilityLabel("Audio script").frame(minHeight: 180)
                    HStack {
                        Text("\(workspace.audioText.count) characters").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        if workspace.submittingAudio { ProgressView().controlSize(.small) }
                        Button("Generate Audio") { Task { await workspace.generateAudio() } }
                            .buttonStyle(.borderedProminent)
                            .disabled(!connected || workspace.submittingAudio || workspace.audioText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selectedAudioModel?.cached != true || selectedAudioModel?.supported != true || selectedAudioModel?.runtimeReady != true || (selectedAudioModel?.requiresReference == true && workspace.audioReferenceAssetId == nil))
                    }
                    Divider().padding(.top, 8)
                    HStack { Text("Recent audio").font(.headline); Spacer(); if player.playing { Button("Stop Playback") { player.stop() } }; Button("All Jobs") { studio.select(.jobs) } }
                    ScrollView { LazyVStack(alignment: .leading, spacing: 12) { ForEach(workspace.audioJobs.prefix(5)) { jobRow($0, compact: true) } } }
                        .frame(minHeight: 110, maxHeight: 220)
                    if workspace.audioJobs.isEmpty { Text("Your generated audio will appear here.").foregroundStyle(.secondary).font(.callout) }
                }.padding(.trailing, 20).frame(minWidth: 410)
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Generation").font(.headline)
                        modelPicker(.audio)
                        if selectedAudioModel?.requiresReference == true {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Reference clip").font(.callout)
                                HStack(spacing: 8) {
                                    Button("Choose Clip…") { chooseReferenceClip() }
                                    Text(workspace.audioReferencePath.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "No clip chosen")
                                        .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                                }
                                Text("A short, clean recording of the voice you want to reuse.").font(.caption).foregroundStyle(.secondary)
                            }
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Reference transcript").font(.callout)
                                TextField("The exact words spoken in the clip", text: $workspace.audioReferenceTranscript)
                                    .textFieldStyle(.roundedBorder)
                                Text(selectedAudioModel?.requiresTranscript == false
                                     ? "Optional for this model." : "Required: this model matches the clip to these words.").font(.caption).foregroundStyle(.secondary)
                            }
                            HStack(spacing: 8) {
                                Button("Prepare Voice") { Task { await workspace.stageVoiceReference() } }
                                    .disabled(!connected || workspace.stagingReference
                                              || workspace.audioReferencePath == nil
                                              || (selectedAudioModel?.requiresTranscript != false
                                                  && workspace.audioReferenceTranscript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
                                if workspace.stagingReference { ProgressView().controlSize(.small) }
                                if let status = workspace.audioReferenceStatus {
                                    Text(status).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        } else {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Speaker").font(.callout)
                                Picker("Speaker", selection: $workspace.audioSpeaker) { ForEach(workspace.audioSpeakers, id: \.self) { Text($0).tag($0) } }.labelsHidden()
                                Text("Speakers are supplied by the selected runtime.").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Language").font(.callout)
                            TextField("Language code, e.g. en", text: $workspace.audioLanguage).textFieldStyle(.roundedBorder)
                        }
                        Spacer(minLength: 12)
                        Text("Model installation and storage are managed centrally.").font(.callout).foregroundStyle(.secondary)
                        Button("Manage Audio Models") { modelFilter = "Audio"; studio.select(.models) }
                    }.padding(.leading, 20).padding(.bottom, 20)
                }.frame(minWidth: 245, idealWidth: 280, maxWidth: 330)
            }
        }.padding(28)
    }

    private func modelPicker(_ function: StudioFunction) -> some View {
        let selection = function == .audio ? $workspace.audioModel : function == .image ? $workspace.imageModel : $workspace.transcriptionModel
        let models = workspace.models.filter { $0.function == function && $0.supported }
        return VStack(alignment: .leading, spacing: 7) {
            Text("Model").font(.callout)
            Picker("Model", selection: selection) {
                Text("Choose a model").tag("")
                ForEach(models) { model in Text(model.label + (model.cached ? "" : " · Not installed")).tag(model.repo) }
            }.labelsHidden().frame(maxWidth: .infinity)
            if let model = models.first(where: { $0.repo == selection.wrappedValue }) {
                Label(model.cached ? "Installed" : "Download in Models", systemImage: model.cached ? "checkmark.circle" : "arrow.down.circle")
                    .font(.caption).foregroundStyle(model.cached ? Color.secondary : Color.orange)
            }
            if models.isEmpty { Text(connected ? "No compatible model is available for this workspace yet." : "Start the services to load available models.").font(.caption).foregroundStyle(.secondary) }
        }
    }

    private var transcriptionWorkspace: some View {
        VStack(alignment: .leading, spacing: 20) {
            heading("Subtitle Studio", "Import a recording, review its transcript and export text or subtitles.")
            if !connected { setup }
            HSplitView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 12) {
                        Image(systemName: "waveform").font(.title2).foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(workspace.transcriptionFile?.lastPathComponent ?? "No recording selected").font(.headline).lineLimit(2).textSelection(.enabled)
                            Text("Audio or video file").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Choose File…", action: chooseTranscriptionFile).disabled(workspace.transcribing)
                    }.padding(16).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                    HStack {
                        Picker("Result format", selection: $transcriptFormat) { Text("Text").tag("Text"); Text("SRT").tag("SRT"); Text("VTT").tag("VTT") }.pickerStyle(.segmented).frame(maxWidth: 240)
                        Spacer()
                        Button("Export…", action: exportTranscript).disabled(workspace.transcriptionResult == nil)
                    }
                    ScrollView {
                        if let result = workspace.transcriptionResult {
                            Text(transcriptFormat == "SRT" ? result.srt : transcriptFormat == "VTT" ? result.vtt : result.text)
                                .font(transcriptFormat == "Text" ? .body : .system(.body, design: .monospaced))
                                .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(18)
                        } else { empty("Your transcript", "Choose a recording and transcribe it. Text and subtitle exports will appear here.", symbol: "text.alignleft") }
                    }.frame(maxWidth: .infinity, maxHeight: .infinity).background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                    if workspace.transcribing { transcriptionActivity }
                    HStack {
                        if let result = workspace.transcriptionResult { Text("\(result.language) · \(result.duration, specifier: "%.1f") seconds").font(.caption).foregroundStyle(.secondary) }
                        Spacer()
                        Button("Transcribe") { Task { await workspace.transcribe() } }.buttonStyle(.borderedProminent)
                            .disabled(!connected || workspace.transcribing || workspace.transcriptionFile == nil || selectedTranscriptionModel?.cached != true || selectedTranscriptionModel?.supported != true || selectedTranscriptionModel?.runtimeReady != true)
                    }
                }.padding(.trailing, 20).frame(minWidth: 410)
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Recognition").font(.headline)
                        modelPicker(.transcription)
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Language").font(.callout)
                            TextField("Auto-detect", text: $workspace.transcriptionLanguage).textFieldStyle(.roundedBorder)
                            Text("Leave blank to detect the language, or enter a code such as en.").font(.caption).foregroundStyle(.secondary)
                        }
                        Divider().padding(.top, 8)
                        HStack { Text("Recent subtitles").font(.headline); Spacer(); Button("All Jobs") { studio.select(.jobs) } }
                        if workspace.subtitleJobs.isEmpty { Text("Completed subtitles will appear here.").foregroundStyle(.secondary).font(.callout) }
                        ForEach(workspace.subtitleJobs.prefix(5)) { job in jobRow(job, compact: true) }
                        Text("This workspace keeps its recording, model and transcript separate from Audio.").font(.callout).foregroundStyle(.secondary)
                        Button("Manage Subtitle Models") { modelFilter = "Transcription"; studio.select(.models) }
                    }.padding(.leading, 20).padding(.bottom, 20)
                }.frame(minWidth: 245, idealWidth: 280, maxWidth: 330)
            }
        }.padding(28)
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
            HSplitView {
                List(filteredModels, selection: $selectedModel) { model in
                    HStack(spacing: 12) {
                        Image(systemName: model.function.symbol).foregroundStyle(.secondary).frame(width: 22)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(model.label).fontWeight(.medium)
                            Text(model.function.rawValue).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
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
                            Divider()
                            Text(model.supportNote).font(.callout).foregroundStyle(.secondary)
                            if model.cached {
                                if model.supported { Button("Use in \(model.function.rawValue)") { if model.function == .audio { workspace.audioModel = model.repo; studio.select(.audio) } else { workspace.transcriptionModel = model.repo; studio.select(.transcription) } }.buttonStyle(.borderedProminent) }
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
                                    Button("Import Folder…") { chooseModelFolder(for: model) }.disabled(!connected || workspace.updatingModels)
                                }
                                if studio.configuration.offline { Text("Offline mode is on. Change it in Settings before downloading. Importing a folder works offline.").font(.caption).foregroundStyle(.secondary) }
                            }
                            if let note = workspace.modelNote { Text(note).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                        }.padding(20)
                    } else { empty("Model details", "Select a model to inspect its status and available actions.", symbol: "externaldrive") }
                }.frame(minWidth: 260, idealWidth: 320, maxWidth: 380)
            }
            if workspace.refreshing && workspace.models.isEmpty { ProgressView("Reading model library…").controlSize(.small) }
            else if filteredModels.isEmpty { Text(connected ? "No models match this view. Catalogs for the remaining engines will appear when those engines are integrated." : "Start the local services to inspect the model library.").font(.callout).foregroundStyle(.secondary) }
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
                    ForEach(HistoryTab.allCases) { tab in Text(tab.rawValue).tag(tab) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 420, alignment: .leading)
                .accessibilityLabel("History filter")
                if workspace.transcribing { transcriptionActivity }
                if historyTab == .all || historyTab == .image {
                    sectionTitle("Image")
                    if workspace.imageJobs.isEmpty { Text("No image jobs yet.").foregroundStyle(.secondary) }
                    ForEach(workspace.imageJobs) { job in imageJobRow(job); Divider() }
                }
                if historyTab == .all || historyTab == .audio {
                    sectionTitle("Audio")
                    if workspace.audioJobs.isEmpty { Text("No audio jobs yet.").foregroundStyle(.secondary) }
                    ForEach(workspace.audioJobs) { job in jobRow(job, compact: false); Divider() }
                }
                if historyTab == .all || historyTab == .subtitle {
                    sectionTitle("Subtitle")
                    if workspace.subtitleJobs.isEmpty { Text("No subtitle jobs yet.").foregroundStyle(.secondary) }
                    ForEach(workspace.subtitleJobs) { job in jobRow(job, compact: false); Divider() }
                    if let result = workspace.transcriptionResult {
                        HStack { VStack(alignment: .leading, spacing: 5) { Text("Latest transcript").font(.headline); Text(result.text).lineLimit(2).foregroundStyle(.secondary) }; Spacer(); Button("Open Subtitle") { studio.select(.transcription) } }
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
    private func jobRow(_ job: NativeJob, compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: active(job.state) ? "waveform" : job.state == "done" ? "checkmark.circle" : "exclamationmark.circle").foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 5) {
                    Text(job.text.isEmpty ? modelName(job.model) : job.text).lineLimit(compact ? 1 : 3)
                    Text("\(modelName(job.model)) · \(stateLabel(job.state))").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if job.state == "done" {
                    Button("Play") { Task { do { try player.play(await workspace.audioData(for: job)) } catch { fileActionError = error.localizedDescription } } }.disabled(!connected)
                    Button("Save…") { saveAudio(job) }.disabled(!connected)
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
            if active(download.state) { ProgressView(value: min(100, max(0, download.percent)), total: 100) }
            if let error = download.error, !error.isEmpty { Text(error).font(.caption).foregroundStyle(.red) }
        }
    }
    private var transcriptionActivity: some View {
        HStack(spacing: 12) { ProgressView().controlSize(.small); VStack(alignment: .leading, spacing: 4) { Text("Transcribing recording").font(.headline); Text("You can use other pages while this finishes.").foregroundStyle(.secondary).font(.callout) }; Spacer() }
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 0) {
            heading("Settings", "App behavior, local storage and service controls.").padding(28)
            Form {
                Section("General") {
                    Toggle("Start at login", isOn: Binding(get: { studio.loginStatus == .enabled || studio.loginStatus == .requiresApproval }, set: { studio.setLogin($0) }))
                    if studio.loginStatus == .requiresApproval { Button("Approve in System Settings") { studio.setLogin(true) } }
                    Text("Closing the window keeps local jobs running. Quit stops the app and its services.").foregroundStyle(.secondary)
                }
                Section("Model library") {
                    LabeledContent("Location") { Text(studio.configuration.modelsDirectory?.path ?? "Not selected").textSelection(.enabled).multilineTextAlignment(.trailing) }
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
                    if studio.canCancelRecovery { Button("Cancel Recovery") { studio.stop() } }
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
                    Text("Finished work is cleaned up on this Mac only. Anything still being written, not yet acknowledged, or in use is protected and is never deleted.")
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
                Stepper(value: $draftGigabytes, in: 1...2000, step: 5) {
                    Text("Keep at most \(String(format: "%.0f", draftGigabytes)) GB")
                }
                Stepper(value: $draftRetentionDays, in: 1...365, step: 1) {
                    Text("Keep finished work for \(draftRetentionDays) days")
                }
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
    private var connections: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                heading("Connections", "Optional API access and external Studio Hub or GenStudio control.")
                Text("Local creation works independently. Connecting a controller is optional.").font(.headline)
                Divider()
                localAPI
                Divider()
                controllerSection
            }.padding(28)
        }
        .task { if connected { await workspace.refreshConnection() } }
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
                    Text("Node-to-node routing is unchanged: GenStudio reaches a location controller, and joined nodes keep their existing enrolment. This app does not open a second gateway.")
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
                Button("Copy Token") {
                    if let token = studio.connectionToken {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(token, forType: .string)
                        connectionNote = "Hub token copied"
                    }
                }.disabled(studio.connectionToken == nil)
                if let connectionNote { Text(connectionNote).font(.callout).foregroundStyle(.secondary) }
            }
            Text("This is the credential this app already uses for its own Hub; a controller presents the same token to reach it. It is never written to the log or to the status line.")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    private var controllerSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Studio Hub & GenStudio")
            if let identity = workspace.controllerIdentity, let version = workspace.hubVersion {
                LabeledContent("Hub version", value: version.version)
                LabeledContent("Data store", value: identity.readiness.sqliteAuthoritative == true ? "Local, authoritative" : identity.settings.databaseMode)
                LabeledContent("Global authority", value: (identity.settings.globalAuthority ?? "genstudio").capitalized)
                LabeledContent("Migration stage", value: identity.settings.migrationStage ?? identity.readiness.migrationStage ?? "local")
                if let warning = identity.readiness.telemetryWarning {
                    Text(warning).font(.callout).foregroundStyle(.orange)
                }
                Text("Jobs are recorded and claimed on this Mac. GenStudio remains the authority for accounts, billing and assets; this app reports the capabilities it actually has and offers no update channel it cannot honour.")
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                Text("Remote control is optional and not required to create assets locally.")
                    .foregroundStyle(.secondary)
            }
            LabeledContent("Enrolment", value: "Existing fleet tokens; no new credential scope")
        }
    }
    private var unavailableWorkspace: some View {
        VStack(alignment: .leading, spacing: 24) {
            heading(studio.selection.rawValue, workspaceDescription(studio.selection))
            Spacer()
            empty("Engine not installed in this build", "This workspace is part of the standalone app. Its local engine and generation workflow still need to be integrated.", symbol: studio.selection.symbol)
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
                    Text("Protected and never deleted: \(NativeWorkspaceStore.gigabytes(usage.protectedBytes))"
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
    /// Reference clip for Qwen Base voice cloning. The Hub accepts the same audio formats the
    /// Voice worker does; validation stays on the Hub so the app and GenStudio behave identically.
    private func chooseReferenceClip() {
        let panel = NSOpenPanel(); panel.title = "Choose a reference clip"
        panel.allowedContentTypes = [.audio]; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { workspace.audioReferencePath = url.path }
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
        Task { do { try await workspace.audioData(for: job).write(to: url, options: .atomic) } catch { fileActionError = error.localizedDescription } }
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
