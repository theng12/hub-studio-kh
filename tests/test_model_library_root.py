"""One folder for this Mac's models, and both halves of the app agreeing about which one.

**The owner's report, 2026-10-18:** *the SSD installer saves models at a different location than Hub
uses on a new Mac, so the folder has to be repointed by hand.* Two folders were in play and nothing
reconciled them:

* the kit's copy step (`qualification/copy_models_for_this_mac.py`) wrote `~/StudioHubModels`;
* the app's own default button (`useDefaultLibrary`) selected `<app data>/models`, a folder inside
  the app's data directory - so a new Mac that took the default and a Mac the drive was copied onto
  ended up with two model folders, the one the Hub read being empty.

The fix is one resolver (`components/model_library.py`, shipped inside the app) that answers with the
folder the Hub is actually using - the operator's own `modelsPath` from `app-preferences.json`, else
an install that already holds models in the app's own data folder, else the canonical home-folder
library - and an installer that fills *that* folder instead of one of its own choosing. Two
properties keep it from doing harm: a saved choice that cannot be read (or names a folder that is not
there) is a **problem the operator sees**, never a silent switch to a different folder; and
`STUDIOHUB_MODELS_DIR`, which the app never reads, is not allowed to decide anything here. These
tests pin the resolver's rules, the three places that must keep naming the same folder (Swift, the
resolver, the kit's fallback), and the app's own default button. They are source-level checks: they
run anywhere, with no app, no Hub and no models.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components import model_library  # noqa: E402  (after the path insert, like the app's own tests)

SWIFT_CORE = ROOT / "native" / "ShellCore.swift"
SWIFT_APP = ROOT / "native" / "StudioHub.swift"
SWIFT_VIEWS = ROOT / "native" / "StudioViews.swift"
HELPER = ROOT / "qualification" / "copy_models_for_this_mac.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_prefs(home: Path, text: str) -> Path:
    """The app's preferences file, written the way the app writes it: owner-only (`0600`).

    The resolver applies the app's own reader guards before it reads anything (`O_NOFOLLOW`, a
    regular file, owned by the effective user, no group/other bits), so a fixture that wrote a
    group-readable file would be testing that guard instead of the JSON rules below.
    """
    prefs = model_library.preferences_file(home)
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text(text)
    prefs.chmod(0o600)
    return prefs


class ResolutionTests(unittest.TestCase):
    """Which folder the resolver answers with, and why."""

    def test_the_workers_variable_is_not_a_choice_for_the_hub(self):
        """The app never reads `STUDIOHUB_MODELS_DIR`; a shell value must not redirect the copy step."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            answer = model_library.resolve_library(home, environ={"STUDIOHUB_MODELS_DIR": "/somewhere/else"})
            self.assertEqual((answer.library, answer.source),
                             (home / model_library.LIBRARY_FOLDER, "default"),
                             "a shell variable was allowed to decide where the Hub's models go")

    def test_the_hubs_own_folder_is_the_one_used(self):
        """The operator's saved choice is the folder the Hub reads, so it is where models must arrive."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            chosen = home / "Models on the drive"
            chosen.mkdir()
            _write_prefs(home, json.dumps({"modelsPath": str(chosen)}))
            answer = model_library.resolve_library(home, environ={})
            self.assertEqual((answer.library, answer.source, answer.problem), (chosen, "configured", ""))

    def test_a_configured_folder_that_is_missing_is_reported_not_replaced(self):
        """An unplugged drive is not a reason to fill a different folder: that hides the real fault."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            missing = home / "Volume-that-is-not-there" / "models"
            _write_prefs(home, json.dumps({"modelsPath": str(missing)}))
            answer = model_library.resolve_library(home, environ={})
            self.assertFalse(answer.usable, "a folder that is not there is a problem, not a fallback")
            self.assertIsNone(answer.library, "no other folder may be offered in its place")
            self.assertEqual(answer.source, "configured")
            self.assertIn(str(missing), answer.problem)
            self.assertNotIn("Traceback", answer.problem)

    def test_an_install_that_keeps_models_in_the_apps_own_folder_keeps_them(self):
        """`<data>/models` was the default for a long time: a Mac really using it must not be moved."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            app_owned = model_library.data_library(home) / "flux2-klein-4b-mlx-4bit"
            app_owned.mkdir(parents=True)
            answer = model_library.resolve_library(home, environ={})
            self.assertEqual((answer.library, answer.source),
                             (model_library.data_library(home), "app-data"))

    def test_the_empty_folder_the_app_created_is_not_a_library(self):
        """The button creates that folder, so an empty one is not evidence of where the models are."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            model_library.data_library(home).mkdir(parents=True)
            answer = model_library.resolve_library(home, environ={})
            self.assertEqual((answer.library, answer.source),
                             (home / model_library.LIBRARY_FOLDER, "default"))

    def test_a_mac_that_has_never_chosen_uses_the_canonical_home_folder(self):
        """This is the new Mac: no settings, no library - the app and the kit both say this folder."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            answer = model_library.resolve_library(home, environ={})
            self.assertEqual((answer.library, answer.source, answer.problem),
                             (home / model_library.LIBRARY_FOLDER, "default", ""))

    def test_a_library_the_kit_already_filled_is_kept(self):
        """A Mac the drive already filled keeps that folder; the earlier Downloads one is still found."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            canonical = home / model_library.LIBRARY_FOLDER / ".studiohub"
            canonical.mkdir(parents=True)
            (canonical / "installed.json").write_text("{}")
            answer = model_library.resolve_library(home, environ={})
            self.assertEqual((answer.library, answer.source),
                             (home / model_library.LIBRARY_FOLDER, "library"))

            (home / model_library.LIBRARY_FOLDER).rename(home / "moved-aside")
            legacy = home / model_library.LEGACY_LIBRARY_FOLDER / ".studiohub"
            legacy.mkdir(parents=True)
            (legacy / "installed.json").write_text("{}")
            answer = model_library.resolve_library(home, environ={})
            self.assertEqual((answer.library, answer.source),
                             (home / model_library.LEGACY_LIBRARY_FOLDER, "legacy"))

    def test_no_choice_yet_is_not_a_fault(self):
        """The app's own spellings for "nothing chosen": no file, an empty file, no key, `null`, ""."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            prefs = model_library.preferences_file(home)
            for text in (None, "", "   ", "{}", '{"modelsPath": ""}', '{"modelsPath": "  "}',
                         '{"modelsPath": null}', '{"offline": false}'):
                if text is None:
                    if prefs.exists():
                        prefs.unlink()
                else:
                    _write_prefs(home, text)
                answer = model_library.resolve_library(home, environ={})
                self.assertEqual((answer.library, answer.source, answer.problem),
                                 (home / model_library.LIBRARY_FOLDER, "default", ""), repr(text))

    def test_settings_that_cannot_be_read_stop_the_step(self):
        """Damage is a fault, not "no choice": the Hub may be pointing at a folder we cannot name."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            prefs = model_library.preferences_file(home)
            for text in ("not json", "[]", "7", '{"modelsPath": 7}', '{"modelsPath": ["/a"]}'):
                _write_prefs(home, text)
                answer = model_library.resolve_library(home, environ={})
                self.assertFalse(answer.usable, text)
                self.assertIsNone(answer.library, text)
                self.assertEqual(answer.source, "unusable", text)
                self.assertIn(str(prefs), answer.problem, text)
                self.assertNotIn("Traceback", answer.problem, text)

    def test_a_settings_file_that_is_not_text_is_a_problem_not_a_crash(self):
        """A corrupt settings file is damage, not "no choice" - and never a traceback.

        `read_text(encoding="utf-8")` used to let `UnicodeDecodeError` (a `ValueError`, so the
        `json.loads` guard below never saw it) escape the read guard: a binary `app-preferences.json`
        killed the copy step with a traceback instead of stopping it with a sentence.
        """
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            prefs = model_library.preferences_file(home)
            prefs.parent.mkdir(parents=True)
            prefs.write_bytes(b"\xff\xfe{")          # not valid UTF-8
            prefs.chmod(0o600)
            answer = model_library.resolve_library(home, environ={})
            self.assertFalse(answer.usable)
            self.assertIsNone(answer.library)
            self.assertEqual(answer.source, "unusable")
            self.assertIn(str(prefs), answer.problem)
            self.assertNotIn("Traceback", answer.problem)

    def test_a_settings_file_the_app_would_refuse_is_refused_here_too(self):
        """The app reads its settings only through `readPrivateRegularFile`; this mirrors it.

        A symlinked, group-readable or non-regular `app-preferences.json` is a file the app treats as
        absent - so an answer taken from it would name a folder the Hub is **not** using, which is the
        mismatch this whole module exists to remove. Such a file is a problem, never a choice.
        """
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            prefs = model_library.preferences_file(home)
            chosen = home / "Models on the drive"
            chosen.mkdir()
            payload = json.dumps({"modelsPath": str(chosen)})

            prefs.parent.mkdir(parents=True)
            prefs.write_text(payload)
            prefs.chmod(0o644)                        # readable by other users
            answer = model_library.resolve_library(home, environ={})
            self.assertFalse(answer.usable, "a group-readable settings file decided the folder")
            self.assertEqual(answer.source, "unusable")
            self.assertIn(str(prefs), answer.problem)

            target = home / "elsewhere.json"          # a symlink is not followed
            target.write_text(payload)
            target.chmod(0o600)
            prefs.unlink()
            prefs.symlink_to(target)
            answer = model_library.resolve_library(home, environ={})
            self.assertFalse(answer.usable, "a symlinked settings file decided the folder")
            self.assertEqual(answer.source, "unusable")
            self.assertIn(str(prefs), answer.problem)

            prefs.unlink()                            # a directory is not a settings file
            prefs.mkdir()
            answer = model_library.resolve_library(home, environ={})
            self.assertFalse(answer.usable, "a non-regular settings file decided the folder")
            self.assertEqual(answer.source, "unusable")
            self.assertIn(str(prefs), answer.problem)

    def test_the_preferences_file_is_the_apps_own(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(model_library.preferences_file(home),
                             home / "Library/Application Support/StudioHub Standalone/app-preferences.json")


def _swift_string(source: str, pattern: str) -> str:
    found = re.search(pattern, source)
    assert found is not None, f"no match for {pattern}"
    return found.group(1)


def _helper_module():
    """The kit's copy script, loaded for its constants only (`main()` is behind its own guard).

    Importing it runs no copy: the module body is imports and definitions. Loading it is what makes
    the two names it falls back to comparable *as paths* rather than by a regex over their spelling.
    """
    spec = importlib.util.spec_from_file_location("copy_models_for_this_mac", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OneNameForTheFolderTests(unittest.TestCase):
    """The Swift app, the resolver and the kit's fallback must name the same two folders."""

    def test_the_library_names_are_equal_everywhere(self):
        swift = _text(SWIFT_CORE)
        self.assertEqual(model_library.LIBRARY_FOLDER,
                         _swift_string(swift, r'static let libraryFolderName = "([^"]+)"'),
                         "ShellCore.libraryFolderName and components/model_library.LIBRARY_FOLDER differ")
        self.assertEqual(str(model_library.LEGACY_LIBRARY_FOLDER),
                         _swift_string(swift, r'static let legacyLibraryFolder = "([^"]+)"'),
                         "the earlier kit's folder differs between the app and the resolver")
        helper = _helper_module()
        self.assertEqual(model_library.LIBRARY_FOLDER, helper.LIBRARY_FOLDER,
                         "the kit's fallback name differs from the resolver's")
        self.assertEqual(model_library.LEGACY_LIBRARY_FOLDER, helper.LEGACY_LIBRARY_FOLDER)


class TheInstallerFillsTheHubsFolderTests(unittest.TestCase):
    """The copy step asks the app where its models belong instead of deciding for itself."""

    def test_the_helper_uses_the_shared_resolver(self):
        helper = _text(HELPER)
        self.assertIn("from model_library import resolve_library", helper)
        self.assertIn("shared_resolver()", helper)

    def test_the_helper_no_longer_picks_its_own_destination(self):
        helper = _text(HELPER)
        self.assertNotIn("destination = Path.home() / LIBRARY_FOLDER", helper,
                         "the helper is deciding the folder again instead of asking the app")

    def test_a_configured_folder_that_is_missing_stops_the_copy(self):
        """Nothing is copied into a different folder: that is the repointing the owner reported."""
        helper = _text(HELPER)
        self.assertIn("resolution.problem", helper)
        self.assertIn("Nothing was copied: filling a different folder would leave the Hub reading an empty", helper)
        block = helper.split("resolution.problem", 1)[1]
        self.assertIn("return 2", block[:600], "a missing configured folder must stop the step")


class TheAppOffersOneDefaultTests(unittest.TestCase):
    """`useDefaultLibrary` no longer selects a second, app-owned folder - and the reveal is truthful."""

    def test_the_default_folder_is_the_one_the_kit_fills(self):
        app = _text(SWIFT_APP)
        parts = app.split("func useDefaultLibrary()", 1)
        self.assertEqual(len(parts), 2, "useDefaultLibrary is gone")
        body = parts[1].split("var libraryFolderExists", 1)[0]
        self.assertIn("ShellConfiguration.defaultLibraryFolder(dataDirectory: configuration.dataDirectory)", body)
        self.assertIn("static func defaultLibrary(", _text(SWIFT_CORE))

    def test_an_install_that_already_uses_the_apps_own_folder_keeps_it(self):
        """`<data>/models` was this button's answer for a long time; a Mac really using it stays there."""
        core = _text(SWIFT_CORE)
        self.assertIn("static func defaultLibraryFolder(", core)
        body = core.split("static func defaultLibraryFolder(", 1)[1].split("\n    }", 1)[0]
        self.assertIn('appendingPathComponent("models")', body)
        self.assertIn("contentsOfDirectory", body)
        self.assertIn("!entries.isEmpty", body)
        self.assertIn("defaultLibrary(home: home)", body)

    def test_the_prepared_library_prefers_the_apps_own_data_folder_too(self):
        """`preparedLibrary` must offer the same folder the resolver's step 2 and the button name.

        It used to scan only the canonical/legacy libraries, so a Mac with a non-empty
        `<data>/models` was offered `~/StudioHubModels` while the copy step filled the data folder -
        the same two-folder divergence, one level up.
        """
        app = _text(SWIFT_APP)
        body = app.split("var preparedLibrary: URL?", 1)[1].split("\n    }", 1)[0]
        self.assertIn(
            "ShellConfiguration.preparedLibrary(dataDirectory: configuration.dataDirectory)", body,
            "the prepared library must look in the app's real data directory, --data-dir included")
        prepared = _text(SWIFT_CORE).split("static func preparedLibrary(", 1)[1].split("\n    }", 1)[0]
        self.assertIn('appendingPathComponent("models")', prepared)
        self.assertIn("contentsOfDirectory", prepared)
        self.assertIn("!entries.isEmpty", prepared)
        self.assertIn("defaultDataDirectory(home: home)", prepared,
                      "with no data directory to hand it must look under the same home folder")

    def test_the_reveal_creates_a_missing_folder_then_reveals_it(self):
        """A missing folder is created rather than refused, so a fresh Mac's library is reachable."""
        app = _text(SWIFT_APP)
        self.assertIn("func revealLibrary() -> Bool", app)
        body = app.split("func revealLibrary() -> Bool", 1)[1].split("\n    }", 1)[0]
        self.assertIn("createDirectory", body)
        self.assertIn("withIntermediateDirectories: true", body)
        self.assertIn("activateFileViewerSelecting", body)
        self.assertIn("ShellConfiguration.defaultLibraryFolder", body,
                      "with no folder selected the reveal creates the resolver's own answer")
        self.assertIn("failure = error.localizedDescription", body,
                      "a reveal that cannot create the folder reports why")

    def test_both_views_show_the_location_and_the_reveal(self):
        views = _text(SWIFT_VIEWS)
        self.assertGreaterEqual(views.count("studio.revealLibrary()"), 2,
                                "the Settings row and the Models page must both reveal the library")
        self.assertNotIn(".disabled(!studio.libraryFolderExists)", views,
                         "a missing folder is created by the reveal, never a dead button")
        self.assertIn("HubSection(\"Model library\")", views)


if __name__ == "__main__":
    unittest.main()
