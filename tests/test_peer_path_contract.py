"""The app's peer paths must be ones the real Hub routes.

The Hub's fleet surface is `/studio/<modality>/<path>`, where the first segment selects the local
worker. The app called `/studio/subtitle/...` — the worker's *name*, not its *modality* — and the
Hub answered `404 {"detail":"unknown studio"}`. That single wrong segment broke the Transcribe
button, and because the app's refresh awaits all of its reads together it also took out the
transcription availability read, so the Whisper models never reached the Audio page and there was
no transcribe model to choose.

Nothing caught it. The Swift contract fixture had been written against the app's own spelling, so
the app and its fixture agreed with each other and disagreed with the Hub; every gate passed while
the feature was dead. Worse, a source-level table check would *still* have missed it, because the
Hub really does expose explicit **GET** reads under `/studio/subtitle/...` for the Subtitle
studio's own catalogue and history. Only the synchronous transcribe **POST** falls through to the
modality router. So these tests both compare the app against the routing table and ask a running
Hub, which is the only thing that can tell a routed path from an unrouted one with the same prefix.
"""
import json
import re
import sys
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "components" / "hub" / "standalone.py"
NATIVE = ROOT / "native"
FIXTURE = ROOT / "tests" / "native" / "api_main.swift"


def hub_modalities() -> set[str]:
    """The modalities the Hub's peer surface routes, read from the Hub's own vocabulary.

    The table used to be a literal inside `standalone.py` and this parsed it out of the source
    text. It now lives in `backend/vocabulary.py`, so the peer surface, the fleet join and the
    packaged worker descriptors read one table instead of three copies that drifted in turn.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from components.hub.backend.vocabulary import WORKER_FOR_MODALITY
    assert WORKER_FOR_MODALITY, "WORKER_FOR_MODALITY is empty"
    return set(WORKER_FOR_MODALITY)


def studio_paths(text: str) -> set[str]:
    """Every first segment after /studio/ in a native source file, however it is written."""
    found = set()
    # get(["studio", "<modality>", ...]) and makeRequest(["studio", "<modality>", ...])
    for match in re.finditer(r'"studio"\s*,\s*"([A-Za-z_]+)"', text):
        found.add(match.group(1))
    return found


def concrete_studio_paths(text: str) -> set[str]:
    """Every full studio path the app builds, as a request path."""
    found = set()
    for line in text.splitlines():
        match = re.search(
            r'(?:get|makeRequest|sendHubJSON)\(\s*\["studio",\s*"([a-z_]+)"(.*?)\]', line)
        if not match:
            continue
        tail = re.findall(r'"([^"]+)"', match.group(2))
        if tail:
            found.add(("/" + "/".join([match.group(1)] + tail)).rstrip("/"))
    return found


class PeerPathContractTests(unittest.TestCase):
    def test_the_routing_table_is_what_the_app_expects(self):
        self.assertEqual(hub_modalities(), {"image", "voice", "transcription", "music", "chat"})

    def test_every_studio_path_in_the_app_names_a_routed_modality(self):
        routed = hub_modalities()
        checked = 0
        for source in sorted(NATIVE.glob("*.swift")):
            used = studio_paths(source.read_text())
            checked += len(used)
            for modality in used:
                self.assertIn(
                    modality, routed,
                    f"{source.name} asks the Hub for /studio/{modality}/... but the Hub routes "
                    f"{sorted(routed)}; it will answer 404 'unknown studio'")
        # If the app's paths are ever spelled in a way this test cannot see, it must say so
        # rather than pass quietly - a gate that matches nothing is not a gate.
        self.assertGreater(checked, 0, "no studio paths were found to check at all")

    def test_the_swift_fixture_refuses_a_studio_the_hub_does_not_route(self):
        """The fixture must be able to fail, or it is not a gate.

        It is the only place the app's requests are checked against a Hub-shaped server, so it has
        to refuse an unrouted modality the way the Hub does.
        """
        text = FIXTURE.read_text()
        self.assertIn("unknown studio", text)
        guard = re.search(r'hubRoutedModalities[^=]*=\s*\[(.*?)\]', text, re.S)
        self.assertIsNotNone(guard, "the fixture no longer refuses unknown studios")
        self.assertEqual(set(re.findall(r'"([a-z_]+)"', guard.group(1))), hub_modalities())

    def test_subtitle_is_never_used_as_a_peer_modality_again(self):
        """The exact regression: the worker's name is not the modality."""
        for source in NATIVE.glob("*.swift"):
            self.assertNotIn(
                '"studio", "subtitle"', source.read_text(),
                f"{source.name} uses the worker name where the Hub expects the modality")
        self.assertNotIn('"/studio/subtitle/', FIXTURE.read_text())


class LiveRouterTests(unittest.TestCase):
    """Ask a real Hub process whether it routes the paths the app builds.

    The table check cannot tell `/studio/subtitle/api/transcribe/availability` (a real GET read the
    Hub defines) from `/studio/subtitle/api/transcribe` (a POST that falls through to the modality
    router and is refused). Only the running router answers that.
    """

    # A Hub only starts from a complete bundle: it insists on a real `runtimes/voice` beside it.
    # The bundle name carries no version, so the versioned names only matter for an older install;
    # omitting the current one made this whole live probe skip while looking green.
    INSTALLED = tuple(Path(folder) / f"Studio Hub{version}.app"
                      for folder in (Path.home() / "Applications", Path("/Applications"))
                      for version in ("", " 0.4.0"))
    # The library lives outside the home folder because macOS protects ~/Downloads; the old
    # location is still accepted so this probe keeps working on a Mac the earlier kit filled in.
    LIBRARIES = (Path.home() / "StudioHubModels",
                 Path.home() / "Downloads" / "model-library")

    @classmethod
    def _library(cls):
        for candidate in cls.LIBRARIES:
            if candidate.is_dir():
                return candidate
        return None

    def _bundle(self):
        for candidate in self.INSTALLED:
            runtime = candidate / "Contents/Resources/runtimes/voice/bin/python3"
            worker = candidate / "Contents/Resources/hub_worker.py"
            if runtime.is_file() and worker.is_file():
                return runtime, worker, candidate / "Contents/Resources/components/hub/standalone.py"
        return None

    def test_every_studio_path_the_app_builds_is_routed(self):
        bundle = self._bundle()
        library = self._library()
        if bundle is None or library is None:
            self.skipTest("no installed app bundle or local model library on this machine")
        runtime, worker, hub_source = bundle
        # An installed bundle can predate the very fix this probes for. Reported as a skip rather
        # than a failure: the point of this test is the tree under test, and the tree under test is
        # checked by the source-level tests above.
        #
        # Detect the *capability* — the installed Hub has a modality routing table it serves from —
        # not a literal. This check used to look for the inline `"transcription": "subtitle"` dict
        # in `standalone.py`, so the moment the table was consolidated into
        # `backend/vocabulary.py` it began skipping **every** run: a skip where there had been real
        # coverage, and it read as "this Mac has no bundle to probe" rather than "this check is
        # stale". The table may live in the Hub source or in the shared vocabulary module.
        routed = ((hub_source.parent / "backend" / "vocabulary.py").is_file()
                  or "WORKER_FOR_MODALITY" in hub_source.read_text())
        if not routed:
            self.skipTest("the installed bundle predates the modality router; rebuild it to probe")
        paths = set()
        for source in sorted(NATIVE.glob("*.swift")):
            paths |= concrete_studio_paths(source.read_text())
        self.assertTrue(paths, "no studio paths found to probe")

        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            ready = data / "ready.json"
            process = subprocess.Popen(
                [str(runtime), str(worker), "--data-dir", str(data),
                 "--models-dir", str(library), "--ready-file", str(ready),
                 "--host", "127.0.0.1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                for _ in range(120):
                    if ready.is_file() and ready.stat().st_size:
                        break
                    if process.poll() is not None:
                        self.skipTest("the Hub did not start on this machine")
                    time.sleep(0.5)
                else:
                    self.skipTest("the Hub did not report ready in time")
                info = json.loads(ready.read_text())
                base, token = info["url"], info["token"]
                unrouted = []
                for path in sorted(paths):
                    request = urllib.request.Request(f"{base}{path}",
                                                     headers={"X-Hub-Token": token})
                    try:
                        with urllib.request.urlopen(request, timeout=60) as response:
                            status, body = response.status, response.read()
                    except urllib.error.HTTPError as error:
                        status, body = error.code, error.read()
                    except OSError as error:
                        self.skipTest(f"could not reach the local Hub: {error}")
                    if status == 404 and b"unknown studio" in body:
                        unrouted.append(path)
                self.assertFalse(
                    unrouted,
                    "the Hub answers 404 'unknown studio' for: " + ", ".join(unrouted))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()


class WireEncodingTests(unittest.TestCase):
    """Hub request bodies are snake_case, and the app must not override that by hand.

    `sendHubJSON` used a bare `JSONEncoder()` while `sendJSON` used `.convertToSnakeCase`, so the
    job envelope carried `voiceReferenceAssetId` while the Hub reads `voice_reference_asset_id`.
    The Hub ignores unknown keys, so nothing errored: the reference was simply dropped, the Voice
    job ran with no reference, and every Qwen3-TTS Base clone failed with a message about a missing
    reference - three layers away from the actual cause.
    """

    def test_hub_json_is_encoded_as_snake_case(self):
        text = (NATIVE / "StudioAPI.swift").read_text()
        method = re.search(r"private func sendHubJSON.*?\n    \}", text, re.S)
        self.assertIsNotNone(method, "sendHubJSON not found")
        self.assertIn("keyEncodingStrategy = .convertToSnakeCase", method.group(0),
                      "sendHubJSON must encode keys the way the Hub reads them")

    def test_no_request_struct_hand_maps_a_key_the_encoder_already_handles(self):
        """A hand-written `voice_reference_asset_id` mapping is what disguised the bug."""
        text = (NATIVE / "StudioAPI.swift").read_text()
        self.assertNotIn('voiceReferenceAssetId = "voice_reference_asset_id"', text)

    def test_the_audio_envelope_puts_the_reference_key_the_hub_reads_on_the_wire(self):
        """Encode the app's own struct and read the key back out, case for case."""
        text = (NATIVE / "StudioAPI.swift").read_text()
        declaration = re.search(r"private struct HubAudioRequest: Codable \{(.*?)\n\}", text, re.S)
        self.assertIsNotNone(declaration, "HubAudioRequest not found")
        fields = re.findall(r"let ([a-zA-Z]+):", declaration.group(1))
        # `convertToSnakeCase` on `voiceReferenceAssetId` produces exactly this key, which is the
        # one `_shared_params_with_local_reference` looks for in the Hub.
        self.assertIn("voiceReferenceAssetId", fields)
        # This assertion used to read `text + quoted`, where the app's own source contains the key
        # only inside comments - and where the Hub's key is written in backticks, so the regex found
        # nothing. It therefore passed no matter what the app did: a hand-written CodingKeys mapping
        # to the wrong key - the exact mistake the file's docstring exists to prevent - left all
        # three wire tests green. Now the app must not spell the wire key at all, and the Hub's real
        # reader must.
        self.assertNotIn("voice_reference_asset_id", without_comments(text),
                         "the app must rely on convertToSnakeCase, not on a literal key")
        self.assertNotIn("CodingKeys", without_comments(declaration.group(1)),
                         "a hand-written mapping overrides convertToSnakeCase silently")
        self.assertIn("voice_reference_asset_id", HUB.read_text(),
                      "the Hub is the side that reads this key")


def without_comments(swift: str) -> str:
    """Swift source with `//` comments removed, so a key mentioned only in prose cannot pass a test.

    The previous version of the wire-key test searched the raw file plus a set of quoted strings,
    and an app comment mentioning the Hub's key satisfied it.
    """
    lines = []
    for line in swift.splitlines():
        if line.strip().startswith("//"):
            continue
        lines.append(line.split("//", 1)[0])
    return "\n".join(lines)


class JobRowContractTests(unittest.TestCase):
    """A transcript is text; it must never be drawn with the audio row.

    The Subtitle page's own panel drew every subtitle with the audio row, so it offered Play and
    Save for a transcript - while the Jobs page, looking at the same job, correctly offered "Open
    Transcript". Reading the rendered window is the only way to see a mistake like that, and this
    pins it from the source instead.

    This is the second time this exact defect has appeared: it was fixed once for the subtitle rows
    and the fix was applied in one place rather than everywhere the list is drawn.
    """

    VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()

    def _lines(self):
        return self.VIEWS.splitlines()

    def test_every_subtitle_list_is_drawn_with_the_subtitle_row(self):
        offenders = [line.strip() for line in self._lines()
                     if "subtitleJobs" in line and "ForEach" in line
                     and "subtitleJobRow" not in line]
        self.assertFalse(offenders, "a subtitle list is drawn with the audio row: " + str(offenders))

    def test_the_audio_row_is_never_handed_a_subtitle_job(self):
        offenders = [line.strip() for line in self._lines()
                     if "jobRow(" in line and "subtitle" in line.lower()]
        self.assertFalse(offenders, "the audio row was given subtitle work: " + str(offenders))

    def test_the_subtitle_row_offers_no_playback(self):
        body = self.VIEWS[self.VIEWS.index("private func subtitleJobRow"):]
        body = body[:body.index("private func jobRow(")]
        for forbidden in ('Button("Play")', 'Button("Save…")', "saveAudio", "workspace.play("):
            self.assertNotIn(forbidden, body,
                             f"the subtitle row must not offer {forbidden}")

    def test_the_subtitle_row_opens_the_transcript(self):
        body = self.VIEWS[self.VIEWS.index("private func subtitleJobRow"):]
        body = body[:body.index("private func jobRow(")]
        self.assertIn('Button("Open Transcript")', body)
        self.assertIn("studio.select(.transcription)", body)

    def test_the_audio_row_still_plays_audio(self):
        body = self.VIEWS[self.VIEWS.index("private func jobRow("):]
        self.assertIn('Button("Play")', body[:2000])


class AudioSpeedWireTests(unittest.TestCase):
    """The pace control must reach the worker, and within the range the audit allows.

    The engine applies speed as pitch-preserving tempo to the finished audio, so the value is a
    plain number on the job parameters. It has to be in the body: a control that changes the UI and
    not the audio is worse than no control.
    """

    def test_the_audio_params_carry_a_speed(self):
        text = (NATIVE / "StudioAPI.swift").read_text()
        params = re.search(r"private struct HubAudioParams: Codable \{(.*?)\n\}", text, re.S)
        self.assertIsNotNone(params, "HubAudioParams not found")
        self.assertIn("var speed: Double", params.group(1))

    def test_every_audio_submission_sends_the_chosen_speed(self):
        """All four voice paths: clone-from-library, clone-from-clip, preset and design."""
        text = (NATIVE / "StudioAPI.swift").read_text()
        constructions = re.findall(r"presetSpeaker: [^\n]*\n?[^\n]*language: language[^\n]*", text)
        audio = [line for line in constructions if "HubAudioParams" not in line]
        self.assertTrue(audio, "no audio parameter constructions found")
        for line in audio:
            self.assertIn("speed: audioSpeed", line,
                          f"an audio path does not send the pace: {line.strip()}")

    def test_the_slider_uses_the_audited_range(self):
        """0.5-2.0 comes from the contract, and is declared once."""
        api = (NATIVE / "StudioAPI.swift").read_text()
        self.assertIn("static let audioSpeedRange: ClosedRange<Double> = 0.5...2.0", api)
        views = (NATIVE / "StudioViews.swift").read_text()
        self.assertIn("in: NativeWorkspaceStore.audioSpeedRange", views,
                      "the slider must not repeat the range by hand")


class AudioBlockageTests(unittest.TestCase):
    """A disabled Generate button must say why.

    The owner hit a Generate Audio button that could not be pressed and gave no reason, on a Mac
    Studio, and had to ask what was wrong. The page also only ever loaded the voice library on the
    Voices page, so going straight to Audio showed "no saved voices" while ten were on the machine.

    Both are pinned here from the view source, because neither is reachable from the fixtures: one is
    a disabled state with no output, the other is a missing side effect.
    """

    VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()

    def test_the_generate_button_is_disabled_by_one_named_reason(self):
        audio = self.VIEWS[self.VIEWS.index("private var audioWorkspace"):]
        audio = audio[:audio.index("private var modelsWorkspace")]
        # No inline condition: the button and the message under it must read the same rule.
        self.assertIn(".disabled(audioBlockedReason != nil)", audio)
        self.assertNotIn(".disabled(!connected || workspace.submittingAudio", audio)

    def test_the_reason_is_shown_next_to_the_button(self):
        audio = self.VIEWS[self.VIEWS.index("private var audioWorkspace"):]
        audio = audio[:audio.index("private var modelsWorkspace")]
        self.assertIn("if let reason = audioBlockedReason", audio)
        self.assertIn("Label(reason, systemImage:", audio)

    def test_every_way_to_be_blocked_is_named(self):
        body = self.VIEWS[self.VIEWS.index("private var audioBlockedReason"):]
        body = body[:body.index("private var voiceLibraryLoadKey")]
        for covered in ("Start the local services", "Write the script", "Choose a model",
                        "Download", "runtime is unavailable", "saved voice", "Prepare Voice",
                        "Describe the voice"):
            self.assertIn(covered, body, f"the reason list does not cover: {covered}")
        # The preset branch reports the one thing a roster model can be waiting for: it has not
        # published its voices yet. The page says that in two places - the picker and the button's
        # reason, both in this one file - so pin that the two agree rather than pinning today's
        # wording. A rename in either copy alone leaves the button explaining something the tab
        # beside it does not say.
        preset = body[body.index("case .preset:"):]
        self.assertIn("publishesPresetRoster", preset,
                      "the preset branch must ask the model whether it publishes a roster, not "
                      "assume every preset model has one")
        reason = re.search(r'return "([^"]*)"', preset)
        self.assertIsNotNone(reason, "the preset branch no longer names what it waits for")
        roster = re.search(r"([A-Za-z-]+ voices)", reason.group(1))
        self.assertIsNotNone(roster,
                             f"the preset reason must name the roster: {reason.group(1)!r}")
        tab = self.VIEWS[self.VIEWS.index("} else if workspace.audioMode == .preset {"):]
        tab = tab[:tab.index("} else {")]
        self.assertIn(roster.group(1), tab,
                      "the preset tab and the blocked reason must call the model's roster the "
                      "same thing")

    def test_the_audio_page_loads_the_voice_library_itself(self):
        """Loading it only on the Voices page left the picker empty on a fresh launch."""
        audio = self.VIEWS[self.VIEWS.index("private var audioWorkspace"):]
        audio = audio[:audio.index("private var modelsWorkspace")]
        self.assertIn("await workspace.refreshVoices()", audio)
        self.assertIn(".task(id: voiceLibraryLoadKey)", audio)


if __name__ == "__main__":
    unittest.main()

class FleetCredentialWiringTests(unittest.TestCase):
    """Which token the app hands the operator. The two are not interchangeable.

    A Mac that joins adopts the value it is given, and the controller then presents that same value
    back to it. Handing over the Hub token would give every node's owner full authority over the
    controller, so the screen has to offer the fleet token and the join form has to name it. Proved
    against two real Hubs in `tests/test_standalone_connections.py`; pinned here so the screen cannot
    quietly start offering the wrong one.
    """

    def test_the_connections_screen_offers_the_fleet_token_for_joining(self) -> None:
        source = (NATIVE / "StudioViews.swift").read_text()
        credential = source[source.index("private var hubCredential"):]
        credential = credential[:credential.index("private var controllerSection")]
        self.assertIn("studio.fleetToken", credential,
                      "the credential offered for joining must be the fleet token")
        self.assertIn("Copy Fleet Token", credential)
        self.assertIn("studio.connectionToken", credential,
                      "the Hub token stays visible: the gateway uses it")
        join = source[source.index("private var joinController"):]
        self.assertIn("Fleet token", join, "the join form must ask for the token it needs")

    def test_the_fleet_credential_is_read_from_its_own_file(self) -> None:
        """Two tokens means two files, and reading the wrong one is invisible until a node joins."""
        hub = (NATIVE / "StudioHub.swift").read_text()
        reader = hub[hub.index("func readFleetToken()"):]
        reader = reader[:reader.index("func setListenHosts")]
        self.assertIn('appendingPathComponent("hub")', reader,
                      "the Hub keeps its state under hub/, so that is where its credential is")
        self.assertIn('.appendingPathComponent(".fleet_token")', reader)
