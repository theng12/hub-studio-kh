import AppKit
import SwiftUI

// The Jobs page's Downloads tab (owner, 2026-10-08: model downloads in their own tab, not a section at
// the bottom of All). Draws the real window offscreen, picks the last segment, and checks it draws.
// Run from the lifecycle gate (tests/native/lifecycle_main.swift), which native/build.sh runs.
enum ViewChecks {
    @MainActor static func run(data: URL, models: URL) throws {
        var failures = 0
        func check(_ ok: Bool, _ what: String) { if !ok { failures += 1; print("FAIL: \(what)") } }
        check(HistoryTab.allCases.last == .downloads && HistoryTab.downloads.title == "Downloads",
              "Downloads is the last Jobs tab")
        check(Set(HistoryTab.allCases.map(\.id)).count == HistoryTab.allCases.count, "Jobs tab ids are unique")
        check(HistoryTab.downloads.studio == nil, "the Downloads tab shows no Studio's history")

        let configuration = try ShellConfiguration(
            arguments: ["lifecycle-tests", "--data-dir", data.path, "--models-dir", models.path],
            bundle: URL(fileURLWithPath: "/Applications/StudioHubFixture.app"))
        let studio = StudioController(configuration: configuration, loginAgentStatus: { _ in .disabled })
        studio.selection = .jobs
        NSApplication.shared.setActivationPolicy(.prohibited)
        let hosting = NSHostingView(rootView: StudioWindow(studio: studio))
        hosting.frame = NSRect(x: 0, y: 0, width: 1280, height: 800)
        let window = NSWindow(contentRect: hosting.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = hosting
        window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
        window.orderFront(nil)
        func settle() {
            for _ in 0..<8 {
                RunLoop.current.run(until: Date().addingTimeInterval(0.1))
                hosting.layoutSubtreeIfNeeded()
                window.displayIfNeeded()
            }
        }
        func draw() -> Data? {
            settle()
            guard let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { return nil }
            hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
            return bitmap.representation(using: .png, properties: [:])
        }
        func segmented(_ view: NSView) -> NSSegmentedControl? {
            if let control = view as? NSSegmentedControl { return control }
            for child in view.subviews { if let found = segmented(child) { return found } }
            return nil
        }
        let all = draw()
        let control = segmented(hosting)
        check(control?.segmentCount == HistoryTab.allCases.count, "the Jobs page draws one segment per tab")
        if let control, control.segmentCount > 0 {
            let last = control.segmentCount - 1
            check(control.label(forSegment: last) == "Downloads", "the last segment reads Downloads")
            control.selectedSegment = last
            _ = control.sendAction(control.action, to: control.target)
        }
        let downloads = draw()
        if let path = ProcessInfo.processInfo.environment["STUDIOHUB_VIEW_CHECKS_PNG"], let downloads {
            try? downloads.write(to: URL(fileURLWithPath: path))
        }
        check(all != nil && downloads != nil, "the Jobs page draws offscreen")
        check(all != downloads, "choosing the Downloads tab changes the page")

        // ---- Jobs Tab Geometry Invariance, Multi-bar Independence & Reachability (2026-11-02) ----
        if let control, control.segmentCount == HistoryTab.allCases.count {
            let baselineWidth = control.fittingSize.width
            let baselineSegments = (0..<control.segmentCount).map { control.width(forSegment: $0) }
            check(baselineSegments.allSatisfy { $0 == baselineSegments[0] },
                  "Jobs tabs have equal widths: \(baselineSegments)")

            var maxVariance: CGFloat = 0
            for tabIndex in 0..<control.segmentCount {
                control.selectedSegment = tabIndex
                _ = control.sendAction(control.action, to: control.target)
                settle()

                let curWidth = control.fittingSize.width
                let diff = abs(curWidth - baselineWidth)
                if diff > maxVariance { maxVariance = diff }
                check(curWidth == baselineWidth,
                      "tab \(tabIndex) maintains invariant width: expected \(baselineWidth), got \(curWidth)")

                for segIndex in 0..<control.segmentCount {
                    let segW = control.width(forSegment: segIndex)
                    check(segW == baselineSegments[segIndex],
                          "tab \(tabIndex) preserves segment \(segIndex) width: expected \(baselineSegments[segIndex]), got \(segW)")
                }
            }
            check(maxVariance == 0.0, "all 8 tab selections produce 0.0 pt width variance (100% invariant geometry)")
        }

        // Multi-bar independence: coexisting segmented controls do not mutate each other
        func allSegmented(_ view: NSView) -> [NSSegmentedControl] {
            var list: [NSSegmentedControl] = []
            if let sc = view as? NSSegmentedControl { list.append(sc) }
            for child in view.subviews { list.append(contentsOf: allSegmented(child)) }
            return list
        }
        let multiHarness = NSHostingView(rootView: VStack(spacing: HubSpace.m) {
            HubSegmentedControl(
                selection: .constant(HistoryTab.all),
                items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                sizing: .stablePerLabel
            )
            HubSegmentedControl(
                selection: .constant(JobMachineFilter.all),
                items: JobMachineFilter.allCases.map { HubSegmentItem(tag: $0, label: $0.rawValue) }
            )
        })
        multiHarness.frame = NSRect(x: 0, y: 0, width: 800, height: 200)
        multiHarness.layoutSubtreeIfNeeded()
        let coexisting = allSegmented(multiHarness)
        check(coexisting.count == 2, "multi-bar harness hosts exactly 2 segmented controls")
        if coexisting.count == 2 {
            let bar1 = coexisting[0]
            let bar2 = coexisting[1]
            check(bar1.segmentCount == 8 && bar2.segmentCount == 3,
                  "coexisting segmented controls maintain distinct segment counts (8 and 3)")
            let bar1OrigW = bar1.fittingSize.width
            let bar2OrigW = bar2.fittingSize.width
            bar2.selectedSegment = 1
            _ = bar2.sendAction(bar2.action, to: bar2.target)
            bar1.selectedSegment = 7
            _ = bar1.sendAction(bar1.action, to: bar1.target)
            multiHarness.layoutSubtreeIfNeeded()
            check(bar1.segmentCount == 8 && bar1.fittingSize.width == bar1OrigW,
                  "bar 1 is unaffected by bar 2 manipulation")
            check(bar2.segmentCount == 3 && bar2.fittingSize.width == bar2OrigW,
                  "bar 2 is unaffected by bar 1 manipulation")
        }

        // Narrow layout reachability: 520pt window minus sidebar (or 400pt container)
        let narrowHost = NSHostingView(rootView: ScrollView(.horizontal, showsIndicators: false) {
            HubSegmentedControl(
                selection: .constant(HistoryTab.all),
                items: HistoryTab.allCases.map { HubSegmentItem(tag: $0, label: $0.title) },
                sizing: .stablePerLabel
            )
        })
        narrowHost.frame = NSRect(x: 0, y: 0, width: 400, height: 48)
        let narrowWindow = NSWindow(contentRect: narrowHost.frame, styleMask: [.borderless],
                                    backing: .buffered, defer: false)
        narrowWindow.contentView = narrowHost
        narrowWindow.setFrameOrigin(NSPoint(x: -20000, y: -20000))
        narrowWindow.orderFront(nil)
        for _ in 0..<8 {
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
            narrowHost.layoutSubtreeIfNeeded()
        }
        let narrowControl = segmented(narrowHost)
        check(narrowControl != nil && narrowControl?.segmentCount == 8,
              "narrow layout retains all 8 segments without clipping")
        if let nc = narrowControl {
            let lastSegW = nc.width(forSegment: 7)
            check(lastSegW >= 90.0, "narrow layout preserves full Downloads segment width: \(lastSegW)pt")
            check(nc.frame.width >= nc.fittingSize.width, "narrow layout keeps intrinsic tab width")
            let lastSegment = NSRect(x: nc.bounds.maxX - lastSegW, y: 0,
                                     width: lastSegW, height: nc.bounds.height)
            _ = nc.scrollToVisible(lastSegment)
            for _ in 0..<8 {
                RunLoop.current.run(until: Date().addingTimeInterval(0.1))
                narrowHost.layoutSubtreeIfNeeded()
            }
            check(nc.visibleRect.maxX >= lastSegment.maxX - 1,
                  "Downloads becomes fully visible after scrolling")
        }

        // The Models page's function tabs (owner, 2026-10-19: "it has no tab or proper spacing. it's
        // squished"). One segmented control, one segment per function the library holds a model for
        // and none for a function it does not - the empty "Video — 0 models" group the owner asked
        // to be rid of was the same row of nothing. This gate has no Hub, so the library is empty:
        // that is exactly the case pinned here. `native/render-page.sh` shows the populated page.
        studio.selection = .models
        let models = draw()
        let tabs = segmented(hosting)
        check(tabs != nil, "the Models page draws its function tabs as a segmented control")
        check(tabs?.segmentCount == 1,
              "a function the library holds no model for gets no tab, only All")
        check(tabs?.label(forSegment: 0)?.hasPrefix("All ") == true,
              "the Models page's first tab is All, carrying the count it would show")
        check(models != nil, "the Models page draws offscreen")
        if let path = ProcessInfo.processInfo.environment["STUDIOHUB_VIEW_CHECKS_MODELS_PNG"],
           let models {
            try? models.write(to: URL(fileURLWithPath: path))
        }
        // The Render page (owner, 2026-10-26: "there's no render tab"). Two things are pinned.
        //
        // **Its rows never turn a fact the Hub did not record into a number.** A render accepted
        // before this build recorded a start has no wall time and no stated length; the row must
        // show only what it has, and a 0 would read as a measured nothing (AGENTS.md §6).
        let legacy = NativeRender(
            id: "c41d0e88-1f52-4c6b-8e27-3a9b5c6d7e1f", state: "output_ready",
            outcome: "succeeded", purpose: "production", display: nil, machine: "local",
            machineName: "This Mac", createdAt: 1_800_000_000, startedAt: nil, finishedAt: nil,
            wallSeconds: nil, runningSeconds: nil, videoSeconds: nil, width: nil, height: nil,
            frameRate: nil, container: nil, codec: nil, effort: nil, crf: nil, tier: nil,
            outputBytes: nil, outputSha256: nil, profileDigest: nil,
            storyUuid: "6d0b8f2e-8a51-4a63-9f0e-1c2b3a4d5e6f", intentId: "intent-1",
            attemptNo: 1, gatewayAttemptKey: "gateway-1", dispatchKey: "gateway:1",
            failure: nil, localFile: nil)
        let legacyFacts = RenderCopy.facts(legacy)
        check(legacyFacts.contains("length not recorded") && !legacyFacts.contains("0.0 s"),
              "a render with no recorded length says so, and never shows a zero: \(legacyFacts)")
        check(RenderCopy.video(legacy) == "length not recorded",
              "the detail says the length was not recorded, not that it was none")
        // A failed attempt produced nothing. The packet's own target must not be drawn as though it
        // had been rendered, or a failure would read as five minutes of video.
        let failed = NativeRender(
            id: "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d", state: "failed", outcome: "failed",
            purpose: "production", display: nil, machine: "blue-2", machineName: "Blue Room 2",
            createdAt: 1_800_000_000, startedAt: 1_799_999_900, finishedAt: 1_799_999_940,
            wallSeconds: 40, runningSeconds: nil, videoSeconds: 300, width: 1920, height: 1080,
            frameRate: 30, container: "mp4", codec: "h264", effort: "hardware", crf: 23,
            tier: "hardware-h264", outputBytes: nil, outputSha256: nil, profileDigest: nil,
            storyUuid: nil, intentId: nil, attemptNo: 1, gatewayAttemptKey: nil, dispatchKey: nil,
            failure: "ffmpeg exited with code 1.", localFile: nil)
        check(!RenderCopy.facts(failed).contains("5 min 0 s"),
              "a failed attempt does not show the packet's target as produced video")
        check(RenderCopy.video(failed).hasPrefix("none produced"),
              "a failed attempt's detail says nothing was produced")
        check(legacyFacts.contains("6d0b8f2e") == false,
              "the Story instance belongs in the detail, not in the one-line facts")
        check(RenderCopy.title(legacy).hasSuffix("c41d0e88"),
              "a render with no channel or episode name is titled by its own job id")
        check(RenderCopy.facts(legacy).contains("This Mac"),
              "the row names the Mac that rendered it")
        // A render Story Studio named shows which channel and episode it belongs to, and the job
        // id it was titled by before is still reachable in Details — nothing is invented for a
        // render that sent neither.
        let named = NativeRender(
            id: "8e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b", state: "output_ready",
            outcome: "succeeded", purpose: "production",
            display: NativeRenderDisplay(channel: "Khmer History", episode: "EP012 The Fall"),
            machine: "local", machineName: "This Mac", createdAt: 1_800_000_000,
            startedAt: 1_799_999_900, finishedAt: 1_799_999_940, wallSeconds: 40,
            runningSeconds: nil, videoSeconds: 300, width: 1920, height: 1080,
            frameRate: 30, container: "mp4", codec: "h264", effort: "hardware", crf: 23,
            tier: "hardware-h264", outputBytes: 25_000_000, outputSha256: nil,
            profileDigest: nil, storyUuid: nil, intentId: nil, attemptNo: 1,
            gatewayAttemptKey: nil, dispatchKey: nil, failure: nil, localFile: nil)
        check(RenderCopy.title(named).contains("Khmer History")
                && RenderCopy.title(named).contains("EP012 The Fall"),
              "a named render is titled by its channel and episode: \(RenderCopy.title(named))")
        check(RenderCopy.title(legacy).contains("c41d0e88"),
              "a render with no name keeps its job id in the title")
        check(NativeRenderDisplay(channel: nil, episode: "EP013").label == "EP013",
              "one label alone is still shown")
        check(RenderCopy.status(legacy) == .ok && RenderCopy.outcomeLabel(legacy) == "Done",
              "a published output reads as done")

        // And the page itself draws offscreen with nothing read yet: it says it is reading, rather
        // than drawing an empty list that would claim this Hub has rendered nothing.
        studio.selection = .renders
        let renders = draw()
        check(renders != nil, "the Render page draws offscreen")
        if let path = ProcessInfo.processInfo.environment["STUDIOHUB_VIEW_CHECKS_RENDERS_PNG"],
           let renders {
            try? renders.write(to: URL(fileURLWithPath: path))
        }
        // The install screen (owner decision 2026-10-28). This gate has no Hub, so the app has read
        // no plan: the page must draw its "reading" state rather than an empty preset list, which
        // would claim this Hub offers no presets. The populated page is shown by
        // `native/render-page.sh` against a fixture.
        //
        // The three promises that are pure app logic and can be checked without a Hub are pinned
        // here too, because they are the ones a refactor breaks silently: a preset's count comes
        // from the Hub and is never recomputed, a model too big here says so and stays listed, and
        // a job heading this app has no word for is still a heading.
        let emptyPlan = NativeInstallPlan(
            memoryGB: 16, library: "/Volumes/KH-STUDIO-ATLAS/model-target",
            diskPath: "/Volumes/KH-STUDIO-ATLAS/model-target", freeBytes: 380_000_000_000,
            diskMeasured: true, stopBytes: 5_000_000_000,
            presets: [NativePreset(id: "creator-16", name: "Creator · 16 GB", tierGB: 16,
                                   summary: "Adds the larger image model.", installedCount: 9,
                                   packageCount: 10, complete: false, fitsThisMac: true,
                                   toCopyBytes: 1_500_000_000, freeAfterBytes: 378_500_000_000,
                                   refused: nil,
                                   packageIDs: Array(repeating: "p", count: 10))],
            startingPreset: "creator-16",
            groups: [NativeInstallGroup(job: "a-job-this-app-does-not-know", models: [
                NativeInstallModel(id: "big", label: "A model", job: "a-job-this-app-does-not-know",
                                   purpose: "Does a thing.", quantization: "MLX 4-bit",
                                   bytes: 9_000_000_000, floorGB: 32, installed: false,
                                   reason: "Needs a Mac with at least 32 GB of memory; this one has 16 GB.",
                                   sources: ["internet"])])],
            problems: [])
        check(emptyPlan.presets.first?.complete == false && emptyPlan.presets.first?.installedCount == 9,
              "9 of 10 is not complete")
        let bigModel = emptyPlan.allModels.first
        check(bigModel?.refusedHere == true, "a model too big for this Mac is marked refused here")
        check(bigModel?.reason?.contains("32 GB") == true && bigModel?.reason?.contains("16 GB") == true,
              "the refusal names the floor and this Mac's memory")
        check(emptyPlan.allModels.count == 1, "a refused model is still listed, never hidden")
        check(emptyPlan.groups.first?.title == "a-job-this-app-does-not-know",
              "a job this app has no word for is shown verbatim rather than dropped")
        check(StudioPage.library.first == .setup, "Setup leads the Library section")
        check(StudioPage.setup.title == "Set up this Mac",
              "the page reads as the thing the operator is doing")

        studio.selection = .setup
        let setupPage = draw()
        check(setupPage != nil, "the install screen draws offscreen with no Hub")
        if let path = ProcessInfo.processInfo.environment["STUDIOHUB_VIEW_CHECKS_SETUP_PNG"],
           let setupPage {
            try? setupPage.write(to: URL(fileURLWithPath: path))
        }
        window.orderOut(nil)

        // **The Studio line is a sidebar-footer item, not the top-of-page banner** (owner,
        // 2026-10-28: "can we have it like in a notification icon somewhere instead?"). What the
        // view has to be able to do: draw nothing when no Studio is missing, draw a line naming the
        // Studio when one is, and carry the detail in words the operator can read.
        //
        // The name is checked in pixels rather than in the type's own string: two notes that differ
        // only in the Studio's name must not render identically, which is what fails if the line
        // stops drawing its name and leaves a bare warning icon. A string check cannot see that.
        func noteHost(_ trouble: [StudioTrouble]) -> NSHostingView<StudioTroubleNote> {
            let host = NSHostingView(rootView: StudioTroubleNote(trouble: trouble))
            host.frame = NSRect(x: 0, y: 0, width: 240, height: 80)
            host.layoutSubtreeIfNeeded()
            return host
        }
        func pixels(_ view: NSView) -> Data? {
            view.layoutSubtreeIfNeeded()
            guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return nil }
            view.cacheDisplay(in: view.bounds, to: rep)
            return rep.representation(using: .png, properties: [:])
        }
        let stopping = StudioTrouble(studio: "Voice Studio", misses: 5, starting: false)
        let otherStudio = StudioTrouble(studio: "Subtitle Studio", misses: 5, starting: false)
        let startingLine = StudioTrouble(studio: "Subtitle Studio", misses: 3, starting: true)
        check(stopping.headline == "Voice Studio not answering",
              "the short line names the Studio, got: \(stopping.headline)")
        check(startingLine.headline == "Subtitle Studio still starting",
              "a Studio that has never answered reads as starting, got: \(startingLine.headline)")
        check(stopping.detail.contains("5 reads") && stopping.detail.contains("clears itself"),
              "the detail says how long and that it clears itself, got: \(stopping.detail)")
        check(!stopping.detail.contains("A studio did not answer"),
              "the vague banner sentence is gone, not reworded into the footer")
        let noLine = noteHost([])
        let voiceLine = noteHost([stopping])
        let subtitleLine = noteHost([otherStudio])
        check(noLine.fittingSize.height <= 1, "no Studio missing draws no footer line")
        check(pixels(noLine) != nil, "the footer's Studio note draws offscreen")
        check(pixels(noLine) != pixels(voiceLine),
              "a Studio that stopped answering adds a line to the footer")
        check(pixels(voiceLine) != pixels(subtitleLine),
              "the line draws the Studio's own name, not a bare warning icon")
        check(noteHost([stopping, startingLine]).fittingSize.height > noLine.fittingSize.height,
              "two Studios down grow the footer rather than burying the second line")

        // **The services' own failure is a notification icon, not the top banner** (owner,
        // 2026-10-28). It must draw nothing with no failure, draw an icon with one, and carry the
        // actual operator text in words - the detail must survive, not become "something failed".
        func failureHost(_ detail: String?) -> NSHostingView<StartupFailureIcon> {
            let host = NSHostingView(rootView: StartupFailureIcon(detail: detail, onDismiss: {}))
            host.frame = NSRect(x: 0, y: 0, width: 240, height: 40)
            host.layoutSubtreeIfNeeded()
            return host
        }
        let noFailure = failureHost(nil)
        let withFailure = failureHost("the services did not start: port 47873 is in use")
        check(noFailure.fittingSize.height <= 1, "no services failure draws no icon")
        check(pixels(noFailure) != nil, "the services failure icon draws offscreen")
        check(pixels(noFailure) != pixels(withFailure),
              "a services failure adds its notification icon to the footer")
        check(StartupFailureIcon.label(for: "port 47873 is in use").contains("port 47873 is in use"),
              "the icon's accessible name carries the actual failure, not just a triangle")

        // The Hub writes `machine` only when a worker reports; a running item still names its Mac
        // in the dispatch target. Actual routing identity, or nothing.
        check(NativeWorkspaceStore.routingMachine(machine: nil, studio: "image@mini-01") == "mini-01",
              "a running fleet item names its Mac from the dispatch target")
        check(NativeWorkspaceStore.routingMachine(machine: nil, studio: "image") == "local",
              "a dispatch target with no machine suffix is this Hub's own Mac")
        check(NativeWorkspaceStore.routingMachine(machine: nil, studio: nil) == nil,
              "an unplaced item has no routing identity, and none is invented")
        check(NativeWorkspaceStore.routingMachine(machine: "local", studio: "image@mini-01") == "local",
              "the attempt's own reported machine wins over the dispatch target")

        // ---- Whose work is whose, and running work first (owner on controller 1400) ----
        check(JobFacts.stage("running") == .running && JobFacts.stage("cancel_requested") == .running
              && JobFacts.stage("cancelling") == .running,
              "running work, and work being stopped, is in Running now")
        check(JobFacts.stage("queued") == .waiting && JobFacts.stage("paused") == .waiting,
              "queued work is in the second group, never in history")
        check(JobFacts.stage("done") == .finished && JobFacts.stage("uncertain") == .finished
              && JobFacts.stage("error") == .finished,
              "finished, failed and uncertain rows stay in history")
        check(JobFacts.whereTag(machine: "local", isActive: true, name: { $0 }) == "This Mac",
              "a This Mac row carries its own tag, not a blank")
        check(JobFacts.whereTag(machine: "mini-01", isActive: true, name: { _ in "mini-01" }) == "mini-01",
              "a node row names the node")
        check(JobFacts.whereTag(machine: nil, isActive: true, name: { $0 }) == "Mac not reported",
              "a live row whose Mac the Hub did not name is not claimed for this Mac")
        check(JobFacts.whereTag(machine: nil, isActive: false, name: { $0 }) == nil,
              "a finished row whose Mac was never recorded says nothing")
        check(JobFacts.askedTag(origin: "this-mac") == "asked here"
              && JobFacts.askedTag(origin: "gateway") == "sent by the Gateway"
              && JobFacts.askedTag(origin: nil) == nil,
              "who asked, and nothing for a batch filed before it was recorded")
        check(NativeWorkspaceStore.cancelText("Cancelled by user") == "Cancelled"
              && NativeWorkspaceStore.cancelText("Cancelled from the Gateway") == "Cancelled from the Gateway"
              && NativeWorkspaceStore.cancelText(nil) == nil,
              "an old 'by user' claim is not repeated; a named source is kept")
        check(JobMachineFilter.allCases.map(\.rawValue) == ["All", "This Mac", "Nodes"],
              "Overview's In progress and Jobs offer the same three filters")
        let unplaced = NativeJob(id: "u", state: "queued", progress: 0, text: "t", model: "m",
                                 createdAt: 1, outputURL: nil, error: nil, machine: nil)
        let placed = NativeJob(id: "p", state: "queued", progress: 0, text: "t", model: "m",
                               createdAt: 1, outputURL: nil, error: nil, machine: "local")
        let node = NativeJob(id: "n", state: "running", progress: 0.2, text: "t", model: "m",
                             createdAt: 1, outputURL: nil, error: nil, machine: "mini-01")
        check(JobMachineFilter.thisMac.includes(placed) && !JobMachineFilter.nodes.includes(placed)
              && JobMachineFilter.nodes.includes(node),
              "the filters split this Mac's work from the nodes' work")
        check(!JobMachineFilter.thisMac.includes(unplaced) && !JobMachineFilter.nodes.includes(unplaced)
              && JobMachineFilter.all.includes(unplaced),
              "an unplaced row is claimed by neither Mac filter: unknown is not guessed into This Mac")

        if failures > 0 { exit(1) }
        print("Native view checks passed")
    }
}
