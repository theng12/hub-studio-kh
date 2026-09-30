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
        meta = body(VIEWS, "private func jobMetaLine", 1800)
        self.assertIn("job.machine", meta)
        self.assertIn("fleetMachineName", meta)


class FleetTests(unittest.TestCase):
    def test_health_is_runnability_not_just_an_answering_hub(self):
        self.assertIn('get(["api", "hub", "model-health"])', API)
        machine = body(VIEWS, "private func machineTint", 500)
        self.assertIn("machineCannotRun", machine)
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
        self.assertNotIn("unavailable in this build", body(VIEWS, "@ViewBuilder private var fleetOverview", 3000))
        self.assertNotIn("unavailable in this build", body(VIEWS, "@ViewBuilder private var joinedNodes", 3000))
        self.assertIn("as of", body(VIEWS, "private var fleetRefreshStatus", 500))
        self.assertIn("unreachableFirst", VIEWS)
        self.assertIn("workspace.fleetRefreshing", body(VIEWS, "@ViewBuilder private var joinedNodes", 800))


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
        self.assertIn("storageWouldDelete(", body(VIEWS, "private var storageEditor", 3000))

    def test_installing_asks_when_work_is_active(self):
        install = body(VIEWS, 'Button(workspace.installingRelease ? "Installing…" : "Install")', 700)
        self.assertIn("workspace.activeWorkCount > 0", install)
        self.assertIn("confirmingInstall = true", install)


class UpdateTests(unittest.TestCase):
    def test_install_work_runs_off_the_main_actor(self):
        install = body(API, "func installOfferedRelease(", 5200)
        self.assertIn("Task.detached", install)
        self.assertNotIn("try installVerifiedArtifact(", install)
        self.assertNotIn("try Self.verifyArtifactFile(", install, "hashing a 2 GB image on the main actor froze the window")
        self.assertNotIn("try Self.verifyArtifactFile(", body(API, "private func downloadReleaseArtifact", 4200))

    def test_only_a_joined_node_updates_itself_and_only_when_idle(self):
        tick = body(API, "func releaseTick(", 2600)
        self.assertIn('controllerIdentity?.settings.role == "agent"', tick)
        self.assertIn("activeWorkCount == 0", tick)
        self.assertIn("releasePublisherKey != nil", tick, "this must never be the read that pins a key")

    def test_an_update_relaunches_the_app_without_the_quit_prompt(self):
        self.assertIn("onRelaunchRequested", HUB)
        relaunch = body(HUB, "func relaunchAfterUpdate()", 1200)
        self.assertIn("relaunching = true", relaunch)
        self.assertIn("if !relaunching {", HUB)

    def test_the_sidebar_badges_an_offered_update(self):
        self.assertIn('.badge(page == .connections && workspace.showingUpdateBanner ? Text("Update") : nil)', VIEWS)


class PollCostTests(unittest.TestCase):
    def test_polling_slows_when_idle_and_skips_a_hidden_window(self):
        self.assertIn("isWindowVisible() && activeWorkCount > 0 ? 2 : 10", API)
        self.assertIn("if isWindowVisible() { await refresh(reportErrors: false) }", API)
        self.assertIn("controller.workspace.isWindowVisible", HUB)
        self.assertNotIn("try? await Task.sleep(for: .seconds(2))", body(API, "pollTask = Task", 400))


class OverviewTests(unittest.TestCase):
    def test_in_progress_includes_video_and_subtitle_work(self):
        overview = body(VIEWS, "private var overview: some View", 2600)
        self.assertIn("activeVideo.isEmpty && activeSubtitles.isEmpty", overview)
        self.assertIn("ForEach(activeVideo)", overview)
        self.assertIn("ForEach(activeSubtitles)", overview)


class DownloadTests(unittest.TestCase):
    def test_recovery_pending_is_drawn_and_failures_stay_visible(self):
        self.assertIn('"recovery_pending"]', body(VIEWS, "private func active(_ state: String)", 200))
        self.assertIn("$0.error != nil", body(VIEWS, "private var activeDownloads", 300))


if __name__ == "__main__":
    unittest.main()
