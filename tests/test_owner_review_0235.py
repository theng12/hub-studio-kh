"""The owner's live review of 0.6.234, pinned as 0.6.235 source checks.

Each test is the acceptance for one numbered item in the batch. They read the shipped source; the
render/native gates prove the pixels and behaviour where a source assertion cannot.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
DESIGN = (ROOT / "native" / "Design.swift").read_text()


class SidebarTests(unittest.TestCase):
    def test_selected_row_is_a_quiet_tint_and_accent_bar(self) -> None:
        row = VIEWS[VIEWS.index("private func sidebarRow"):]
        row = row[:row.index("private func pageTitle")]
        self.assertIn("Color.accentColor.opacity(0.12)", row)
        self.assertIn("fill(selected ? Color.accentColor : Color.clear)", row)
        self.assertIn("width: 3, height: 18", row)
        self.assertNotIn("Color.white", row, "the selected row is no longer a white-on-accent block")


class FooterTests(unittest.TestCase):
    def test_pill_names_the_product_version_and_whats_new(self) -> None:
        pill = VIEWS[VIEWS.index("private var footerVersionPill"):]
        pill = pill[:pill.index("private var sidebarList")]
        self.assertIn('Text("Hub Studio KH")', pill)
        self.assertIn("Text(appVersion)", pill)
        self.assertIn('Text("· What\'s New")', pill)


class ServiceStateTests(unittest.TestCase):
    def test_one_service_phase_drives_footer_and_overview(self) -> None:
        self.assertIn("private enum ServicePhase", VIEWS)
        self.assertIn("private var servicePhase: ServicePhase", VIEWS)
        self.assertIn("private var servicePhaseLine: String", VIEWS)
        status = VIEWS[VIEWS.index("private var overviewStatus"):]
        status = status[:status.index("private var overview:")]
        self.assertIn("servicePhase == .recovering", status)
        self.assertIn("servicePhase == .stopped", status)
        footer = VIEWS[VIEWS.index("private var footerStatusLine"):]
        footer = footer[:footer.index("private var footerVersionPill")]
        self.assertIn("return servicePhaseLine", footer)

    def test_overview_has_one_start_primary_and_no_model_blocker_while_stopped(self) -> None:
        overview = VIEWS[VIEWS.index("private var overview:") :]
        overview = overview[:overview.index("private var measuredSection")]
        self.assertNotIn('Button("Start Services")', overview,
                         "Start Services lives in the setup card, not the empty in-progress card")
        self.assertIn("servicePhase == .running, !workspace.models.contains", overview)
        self.assertIn("servicePhase == .running {", overview)

    def test_model_library_copy_names_the_local_folder(self) -> None:
        self.assertIn("Your model folder is on this Mac.", VIEWS)
        self.assertIn("No model folder is selected.", VIEWS)
        self.assertNotIn("Connect your library to inspect installed models", VIEWS)


class AudioServiceStateTests(unittest.TestCase):
    def test_audio_has_one_service_banner_and_one_primary(self) -> None:
        audio = VIEWS[VIEWS.index("private var audioWorkspace"):]
        audio = audio[:audio.index("private var voiceSourcePicker")]
        self.assertIn('primary: servicePhase == .stopped', audio)
        self.assertIn('title: servicePhase == .recovering ? "Starting local services…" : "Local services are stopped"', audio)
        self.assertIn(".disabled(servicePhase != .running || audioBlockedReason != nil)", audio)
        blocked = VIEWS[VIEWS.index("private var audioBlockedReason"):]
        blocked = blocked[:blocked.index("private var voiceLibraryLoadKey")]
        self.assertNotIn('"Start the local services first."', blocked)

    def test_voice_select_says_services_stopped(self) -> None:
        picker = VIEWS[VIEWS.index("private var voiceSourcePicker"):]
        picker = picker[:picker.index("private var audioBlockedReason")]
        self.assertIn("Services stopped — start them to list voices.", picker)
        self.assertIn("Services are starting — saved voices will list when they are ready.", picker)
        self.assertIn(".disabled(workspace.voices.isEmpty || servicePhase != .running)", picker)

    def test_voice_and_model_actions_disable_while_stopped(self) -> None:
        picker = VIEWS[VIEWS.index("private var voiceSourcePicker"):]
        picker = picker[:picker.index("private var audioBlockedReason")]
        self.assertEqual(picker.count(".disabled(servicePhase != .running)"), 2)
        audio = VIEWS[VIEWS.index("private var audioWorkspace"):]
        audio = audio[:audio.index("private var voiceSourcePicker")]
        self.assertIn('Button("Manage Audio Models")', audio)
        self.assertIn(".disabled(servicePhase != .running)", audio)


class AudioLayoutTests(unittest.TestCase):
    def test_script_two_thirds_recent_one_third_generation_full_width(self) -> None:
        audio = VIEWS[VIEWS.index("private var audioWorkspace"):]
        audio = audio[:audio.index("private var audioScriptSection")]
        self.assertIn("contentWidth * 2 / 3", audio)
        self.assertIn("contentWidth / 3", audio)
        self.assertIn("audioGenerationSection", audio)
        # Generation is after the HStack, not inside the narrow column.
        self.assertLess(audio.index("HStack(alignment: .top"), audio.index("audioGenerationSection"))

    def test_recent_audio_has_one_empty_state_and_one_next_step(self) -> None:
        pane = VIEWS[VIEWS.index("private var audioRecentPane"):]
        pane = pane[:pane.index("private var voiceSourcePicker")]
        self.assertIn('empty("Your audio will appear here", "Write a script and press Generate Audio."', pane)
        self.assertIn("showHint: !workspace.audioJobs.isEmpty", pane)
        self.assertEqual(pane.count('empty("Your audio will appear here"'), 1)


if __name__ == "__main__":
    unittest.main()
