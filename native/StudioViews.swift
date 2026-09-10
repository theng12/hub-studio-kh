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

    private var connected: Bool { studio.readyURL != nil && !studio.stopping && workspace.isReachable }
    private var selectedAudioModel: NativeModel? { workspace.models.first { $0.repo == workspace.audioModel && $0.function == .audio } }
    private var selectedTranscriptionModel: NativeModel? { workspace.models.first { $0.repo == workspace.transcriptionModel && $0.function == .transcription } }
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
                    ForEach(StudioPage.workspaces) { page in Label(page.rawValue, systemImage: page.symbol).tag(page) }
                }
                Section("Library & app") {
                    ForEach(StudioPage.management) { page in Label(page.rawValue, systemImage: page.symbol).tag(page) }
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
                if activeAudio.isEmpty && activeDownloads.isEmpty && !workspace.transcribing {
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
                                Text(page.rawValue).font(.headline)
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
            }.padding(28)
        }
    }
    private func workspaceStatus(_ page: StudioPage) -> String {
        guard page == .audio || page == .transcription else { return "Engine not installed" }
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
                            .disabled(!connected || workspace.submittingAudio || workspace.audioText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selectedAudioModel?.cached != true || selectedAudioModel?.supported != true || selectedAudioModel?.runtimeReady != true)
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
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Speaker").font(.callout)
                            Picker("Speaker", selection: $workspace.audioSpeaker) { ForEach(workspace.audioSpeakers, id: \.self) { Text($0).tag($0) } }.labelsHidden()
                            Text("Speakers are supplied by the selected runtime.").font(.caption).foregroundStyle(.secondary)
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
        let selection = function == .audio ? $workspace.audioModel : $workspace.transcriptionModel
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
            heading("Transcription", "Import a recording, review its transcript and export text or subtitles.")
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
                        Text("This workspace keeps its recording, model and transcript separate from Audio.").font(.callout).foregroundStyle(.secondary)
                        Button("Manage Transcription Models") { modelFilter = "Transcription"; studio.select(.models) }
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
                                Button("Remove Download…") { confirmRemove(model) }.disabled(!connected)
                            } else {
                                Button("Download Model") { Task { await workspace.startDownload(model) } }.buttonStyle(.borderedProminent).disabled(!connected || studio.configuration.offline || !model.supported)
                                if studio.configuration.offline { Text("Offline mode is on. Change it in Settings before downloading.").font(.caption).foregroundStyle(.secondary) }
                            }
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
                heading("Jobs", "Follow local work and retrieve completed assets.")
                if workspace.transcribing { transcriptionActivity }
                sectionTitle("Audio")
                if workspace.audioJobs.isEmpty { Text("No audio jobs yet.").foregroundStyle(.secondary) }
                ForEach(workspace.audioJobs) { job in jobRow(job, compact: false); Divider() }
                sectionTitle("Transcription")
                if let result = workspace.transcriptionResult {
                    HStack { VStack(alignment: .leading, spacing: 5) { Text("Latest transcript").font(.headline); Text(result.text).lineLimit(2).foregroundStyle(.secondary) }; Spacer(); Button("Open Transcript") { studio.select(.transcription) } }
                } else if !workspace.transcribing { Text("No completed transcript yet.").foregroundStyle(.secondary) }
                Divider()
                sectionTitle("Model downloads")
                if workspace.downloads.isEmpty { Text("No model downloads.").foregroundStyle(.secondary) }
                ForEach(workspace.downloads) { downloadRow($0) }
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
                        else if studio.running { Button(studio.stopping ? "Stopping…" : "Stop Services…") { studio.confirmStop() }.disabled(studio.stopping) }
                        else { Button("Start Services") { studio.start() }.disabled(!studio.canConfigure || !studio.hasLibrary) }
                        Button("Release Model Memory") { Task { await workspace.releaseMemory() } }.disabled(!connected)
                        Button("Open Log") { studio.openLog() }
                    }
                }
                Section("App data") {
                    LabeledContent("Location") { Text(studio.configuration.dataDirectory.path).textSelection(.enabled).multilineTextAlignment(.trailing) }
                    Button("Show App Data") { NSWorkspace.shared.open(studio.configuration.dataDirectory) }
                    LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development")
                }
            }.formStyle(.grouped)
        }
    }
    private var connections: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                heading("Connections", "Optional API access and external Studio Hub or GenStudio control.")
                Text("Local creation works independently. Connecting a controller is optional.").font(.headline)
                Divider()
                VStack(alignment: .leading, spacing: 12) {
                    sectionTitle("Local API")
                    if let url = studio.readyURL { LabeledContent("Address") { Text(url.absoluteString).textSelection(.enabled) }; Text("Available only on this Mac in the current preview.").foregroundStyle(.secondary) }
                    else { Text("Start local services to make the API available on this Mac.").foregroundStyle(.secondary) }
                }
                Divider()
                VStack(alignment: .leading, spacing: 12) {
                    sectionTitle("Studio Hub & GenStudio")
                    Text("Controller connection is not available in this build.").font(.headline)
                    Text("Authenticated remote control and fleet integration are planned as optional features. They are not required to generate local assets.").foregroundStyle(.secondary)
                }
            }.padding(28)
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

    private func chooseTranscriptionFile() {
        let panel = NSOpenPanel(); panel.title = "Choose a recording"; panel.allowedContentTypes = [.audio, .movie]; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK { workspace.transcriptionFile = panel.url }
    }
    private func saveAudio(_ job: NativeJob) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "audio-\(job.id).wav"; panel.allowedContentTypes = [.wav]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { do { try await workspace.audioData(for: job).write(to: url, options: .atomic) } catch { fileActionError = error.localizedDescription } }
    }
    private func exportTranscript() {
        guard let result = workspace.transcriptionResult else { return }
        let ext = transcriptFormat == "Text" ? "txt" : transcriptFormat.lowercased()
        let panel = NSSavePanel(); panel.nameFieldStringValue = "transcript.\(ext)"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let text = transcriptFormat == "SRT" ? result.srt : transcriptFormat == "VTT" ? result.vtt : result.text
        do { try text.write(to: url, atomically: true, encoding: .utf8) } catch { fileActionError = error.localizedDescription }
    }
    private func confirmRemove(_ model: NativeModel) {
        let alert = NSAlert(); alert.messageText = "Remove downloaded model?"; alert.informativeText = "Remove \(model.label) from this library. Existing generated files are kept. Models in use or required by another installed model cannot be removed."
        alert.addButton(withTitle: "Keep Model"); alert.addButton(withTitle: "Remove Download")
        if alert.runModal() == .alertSecondButtonReturn { Task { await workspace.removeModel(model) } }
    }
}
