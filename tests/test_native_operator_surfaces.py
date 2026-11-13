"""What the native app shows an operator about jobs, the fleet, updates and storage (0.6.114).

These pin wiring that the SwiftUI views own and the API contract suite cannot reach: a control that is
present, guarded, and reading the fact the Hub publishes. Each one fails if the behaviour is removed.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()
HUB = (ROOT / "native" / "StudioHub.swift").read_text()


def body(source: str, marker: str, length: int = 2500) -> str:
    return source[source.index(marker):][:length]


class JobRowTests(unittest.TestCase):
    def test_a_queued_row_shows_the_hubs_reason_not_a_fixed_sentence(self):
        queued = body(VIEWS, "@ViewBuilder private func jobProgress", 900)
        self.assertIn("NativeJob.waitingLabel(note: job.governorNote", queued)
        self.assertNotIn('Text("Waiting for a free worker")', queued)

    def test_every_job_row_that_can_fail_offers_retry(self):
        self.assertEqual(VIEWS.count('else if job.state == "error"'), 4,
                         "image, music, video and audio rows each need the Retry branch")
        self.assertIn('items", String(job.itemIndex), "retry"', API)

    def test_the_meta_line_names_the_machine(self):
        meta = body(VIEWS, "private func jobMetaParts", 1800)   # builds the line jobMetaLine draws
        self.assertIn("job.machine", meta)
        self.assertIn("fleetMachineName", meta)


class FleetTests(unittest.TestCase):
    def test_health_is_runnability_not_just_an_answering_hub(self):
        self.assertIn('get(["api", "hub", "model-health"])', API)
        # The Connections table's state word: a Mac that answers but cannot run anything reads as
        # Attention, never as Ready. The decision lives with the row (`NativeFleetRow.state`).
        state = body(API, "func state(readiness: NativeMachineReadiness?)", 700)
        self.assertIn("cannotRunAnything", state)
        self.assertIn(".attention", state)
        self.assertIn("workspace.fleetReadiness[row.id]", VIEWS)
        self.assertIn("cannotRunAnything", API)

    def test_the_sidebar_counts_models_that_can_run(self):
        summary = body(VIEWS, "private var generatorSummary", 500)
        self.assertIn("NativeWorkspaceStore.runsHere", summary)

    def test_voice_is_listed_once(self):
        self.assertIn("seen.insert($0).inserted", body(VIEWS, "private func doneModalities", 400))

    def test_send_here_is_disabled_for_an_offline_mac_but_can_always_be_cleared(self):
        self.assertIn(".disabled(!connected || (!machine.reachable && !pinned))", VIEWS)

    def test_the_pin_is_visible_on_every_page_that_sends_it(self):
        self.assertEqual(VIEWS.count("pinChip\n"), 5, "image, music, video, audio and subtitle pages")
        chip = body(VIEWS, "private var pinChip", 900)
        self.assertIn("Clear", chip)
        self.assertIn("(offline)", chip)

    def test_the_fleet_page_reads_as_a_dated_report_that_says_what_failed(self):
        # One table of Macs (0.6.127 design pass): the overview and the roster are one list now.
        nodes = body(VIEWS, "@ViewBuilder private var nodesSection", 3000)
        self.assertNotIn("unavailable in this build", nodes)
        self.assertNotIn("fleetOverview: some View", VIEWS, "the second list of the same Macs is back")
        self.assertNotIn("private var joinedNodes", VIEWS, "the second list of the same Macs is back")
        self.assertIn("as of", body(VIEWS, "private var fleetRefreshStatus", 500))
        self.assertIn("unreachableFirst", API)
        self.assertIn("workspace.fleetRefreshing", nodes)
        self.assertIn("workspace.fleetReadError", nodes)

    def test_nodes_reads_follow_local_services_and_each_workspace_connection(self):
        self.assertIn(
            "private var localServicesReady: Bool { studio.readyURL != nil && !studio.stopping }",
            VIEWS,
        )
        self.assertIn('.task(id: "\\(localServicesReady)/\\(workspace.connectionGeneration)")', VIEWS)
        self.assertIn("if localServicesReady {\n                await workspace.refreshFleetNodes()", VIEWS)
        nodes = body(VIEWS, "@ViewBuilder private var nodesSection", 3400)
        self.assertIn(".disabled(!localServicesReady || workspace.fleetRefreshing)", nodes)
        self.assertIn('!localServicesReady ? "Start local services to see the fleet."', nodes)
        self.assertIn("workspace.catalogReadError", nodes)

    def test_roster_failure_copy_only_claims_stale_data_when_a_snapshot_exists(self):
        nodes = body(VIEWS, "@ViewBuilder private var nodesSection", 3400)
        self.assertIn("let hasFleetSnapshot = workspace.fleetOverview != nil || workspace.fleetNodes != nil", nodes)
        self.assertIn('hasFleetSnapshot ? " Showing what was read last." : ""', nodes)

    def test_remove_uses_current_roster_and_confirmation_generation(self):
        row = body(VIEWS, "@ViewBuilder private func nodeActions", 1800)
        self.assertIn("pendingNodeRemoval = PendingNodeRemoval(", row)
        self.assertIn("node: node, connectionGeneration: workspace.connectionGeneration", row)
        self.assertIn(
            ".disabled(!localServicesReady || !workspace.fleetRosterReachable || workspace.fleetRefreshing)",
            row,
        )
        dialog = body(VIEWS, '"Remove \\(pendingNodeRemoval?.node.name', 1300)
        self.assertIn("Task {\n                        guard localServicesReady", dialog)
        self.assertIn("expectedConnectionGeneration: intent.connectionGeneration", dialog)

    def test_unread_controller_roster_is_not_presented_as_zero_nodes(self):
        group = body(VIEWS, "private var groupLine", 800)
        self.assertIn("guard let nodes = workspace.fleetNodes?.nodes else {", group)
        self.assertIn('if workspace.fleetReadError != nil { return "Controller · nodes not read" }', group)
        self.assertIn("let count = nodes.count", group)


class ConfirmationTests(unittest.TestCase):
    def test_hand_over_is_confirmed_like_the_other_irreversible_fleet_actions(self):
        button = body(VIEWS, 'Button(workspace.fleetOperationRunning ? "Handing over…"', 250)
        self.assertIn("confirmingHandover = true", button)
        self.assertNotIn("handOverFleet", button)
        dialog = body(VIEWS, '"Hand this group over to', 700)
        self.assertIn("handOverFleet(to: handoverAddress", dialog)

    def test_restore_defaults_asks_first_and_the_editor_shows_the_dry_run(self):
        self.assertNotIn('Button("Restore Defaults") { Task', VIEWS)
        self.assertIn("confirmingRestoreStorage = true", VIEWS)
        # The window is only a slice bound, not the assertion: it has to reach the `.task` that runs
        # the dry run, and the editor's own copy has grown twice (both stages now name days and GB).
        self.assertIn("storageWouldDelete(", body(VIEWS, "private var storageEditor", 4200))

class RetiredSelfUpdateTests(unittest.TestCase):
    def test_the_app_carries_no_self_update_machinery(self):
        for gone in ("releaseTick", "refreshRelease", "checkForUpdateNow", "installOfferedRelease",
                     "installVerifiedArtifact", "releasePublisherKey", "offeredRelease",
                     "showingUpdateBanner", "manualUpdateNote", "updateStatusLine", "relaunchAfterUpdate",
                     "RelaunchHelper", "changePublisherKey", "ReleaseInstaller"):
            self.assertNotIn(gone, VIEWS + API + HUB, gone)
        self.assertFalse((ROOT / "native" / "UpdateClient.swift").exists(),
                         "the in-app update client is retired")

    def test_the_footer_control_opens_the_launcher_with_the_one_sentence(self):
        self.assertIn("NativeWorkspaceStore.openStudioLauncher()", VIEWS)
        self.assertIn('"Updates are installed by Studio Launcher KH."', VIEWS)
        self.assertNotIn("Check for Updates", VIEWS)


class PollCostTests(unittest.TestCase):
    def test_polling_slows_when_idle_and_skips_a_hidden_window(self):
        self.assertIn("isWindowVisible() && activeWorkCount > 0 ? 2 : 10", API)
        self.assertIn("if isWindowVisible() { await refresh(reportErrors: false) }", API)
        self.assertIn("controller.workspace.isWindowVisible", HUB)
        self.assertNotIn("try? await Task.sleep(for: .seconds(2))", body(API, "pollTask = Task", 400))


class OverviewTests(unittest.TestCase):
    def test_in_progress_includes_video_and_subtitle_work(self):
        overview = body(VIEWS, "private var overview: some View", 6000)
        self.assertIn("let video = activeVideo.filter(overviewMachineFilter.includes)", overview)
        self.assertIn("let subtitles = activeSubtitles.filter(overviewMachineFilter.includes)", overview)
        self.assertIn("video.isEmpty\n                        && subtitles.isEmpty", overview)
        self.assertIn("video.map { AnyView(videoJobRow($0)) }", overview)
        self.assertIn("subtitles.map { AnyView(subtitleJobRow($0)) }", overview)


class DownloadTests(unittest.TestCase):
    def test_recovery_pending_is_drawn_and_failures_stay_visible(self):
        self.assertIn('"recovery_pending"]', body(VIEWS, "private func active(_ state: String)", 200))
        self.assertIn("$0.error != nil", body(VIEWS, "private var activeDownloads", 300))


if __name__ == "__main__":
    unittest.main()
