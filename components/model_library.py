"""The one place that answers *which folder holds this Mac's models*.

**Why this exists (owner report, 2026-10-18):** the SSD kit's copy step wrote every model into
`~/StudioHubModels`, while the Hub could be using a different folder - the app's own data folder
(`<data>/models`, what the old "Use Default Folder" button selected) or a folder the operator chose
and the app saved as `modelsPath`. The models then arrived somewhere the Hub was not looking, and
the Mac had to be repointed by hand.

**What the app itself does** (`native/ShellCore.swift`, `ShellConfiguration.init`): the effective
model folder is `--models-dir` (an explicit command-line override) if given, else `modelsPath` from
`app-preferences.json` in its data folder, else nothing - the app asks. It never reads
`STUDIOHUB_MODELS_DIR`; that variable is what the *workers* are handed (`packaging/*_worker.py`), and
a value in a shell is no evidence of what the Hub is using, so this module does not consult it
either. The kit's standard invocation is the launcher in the kit root, which passes only
`STUDIOHUB_APP_ROOT` and runs the helper with the app's own interpreter under the operator's home.

So the questions answered here are: *is there a saved choice, is it usable, and is there already a
library on this Mac?* - and, when the saved choice cannot be read at all, the answer is a plain
problem rather than a guess, because the wrong folder here means 30 GB copied where the Hub will
never look. The settings file is read under the same guards the app itself applies before it will
read it at all (`_read_private_text`): a symlinked, non-regular, not-owner-only or non-text
`app-preferences.json` is refused with a problem, never read into an answer the app would reject.

Read-only: this module looks at folders and at the app's saved settings. It never creates, moves or
deletes anything (the caller decides what to do with the answer).
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

#: The canonical home-folder library: a sibling of the home folder, not `~/Downloads`, because macOS
#: privacy protection covers Downloads and a service the app starts can be blocked from reading a
#: library inside it (the worker then waits inside `open()` forever). Must equal
#: `ShellConfiguration.libraryFolderName` in `native/ShellCore.swift`.
LIBRARY_FOLDER = "StudioHubModels"
#: The folder the first kit filled. Still recognised, so a Mac set up with it keeps working.
LEGACY_LIBRARY_FOLDER = Path("Downloads") / "model-library"
#: The app's own data folder (its `dataDirectory`), relative to the home folder.
DATA_RELATIVE = Path("Library/Application Support/StudioHub Standalone")
#: Where the app saves the library the operator chose (`modelsPath`), beside the data folder's name.
PREFERENCES_RELATIVE = DATA_RELATIVE / "app-preferences.json"
#: The folder the app's earlier "Use Default Folder" button created inside its data folder.
DATA_LIBRARY_NAME = "models"
#: The app's own record of what it has installed in a library; a folder holding it is a real library.
INDEX_RELATIVE = Path(".studiohub") / "installed.json"


@dataclass(frozen=True)
class Resolution:
    """Which folder to use, why, and - when nothing can be decided - the plain reason.

    `library` is None only when there is a problem: a caller that gets a problem must not fall back
    to a folder of its own, because filling a folder the Hub is not reading is the defect this
    module exists to prevent.
    """

    library: Path | None
    source: str                 # "configured" | "app-data" | "library" | "legacy" | "default"
                                # | "unusable" (a problem, no folder to use)
    problem: str = ""           # a sentence for the operator; never a traceback or an errno

    @property
    def usable(self) -> bool:
        return not self.problem


def preferences_file(home: Path) -> Path:
    """The app's saved settings for this home folder."""
    return Path(home) / PREFERENCES_RELATIVE


def data_directory(home: Path) -> Path:
    """The app's own data folder for this home folder (its `dataDirectory`)."""
    return Path(home) / DATA_RELATIVE


def default_library(home: Path) -> Path:
    """The canonical home-folder library for this home folder."""
    return Path(home) / LIBRARY_FOLDER


def data_library(home: Path) -> Path:
    """The library the app's earlier "Use Default Folder" button created inside its data folder."""
    return data_directory(home) / DATA_LIBRARY_NAME


def holds_anything(folder: Path) -> bool:
    """True when this folder exists and has at least one entry.

    The distinction that matters: the app **creates** that folder when the button is pressed, so an
    *empty* one proves nothing about where a Mac's models are, while a folder with something in it
    is a Mac that is really using it - and moving such a Mac to another folder would be the silent
    switch this module exists to prevent.
    """
    try:
        return any(True for _ in Path(folder).iterdir())
    except OSError:
        return False


def _read_private_text(path: Path) -> tuple[str | None, str]:
    """(the file's text, a plain problem). A `None` text means the file is not there at all.

    The file-level half of the app's own reader (`ShellConfiguration.readPrivateRegularFile`,
    `native/ShellCore.swift`): the file is opened without following a symlink (`O_NOFOLLOW`) and
    accepted only if it is a **regular file**, **owned by the effective user** and **not readable or
    writable by anyone else** (`st_mode & 0o077 == 0`) - the app writes it `0600` inside a `0700`
    folder.

    The app treats every failure of those guards as "there is no settings file", so a preferences
    file it refuses to read names **no** folder the Hub is using. Reading it here anyway would answer
    with a folder the Hub is not looking at - the mismatch this module exists to remove - so a file
    that is **there but fails a guard** is reported as a problem for the operator instead. The bytes
    are decoded as UTF-8: a file that is not text (`UnicodeDecodeError`, a `ValueError` and so not
    caught by the `json.loads` guard) is a problem too, never a traceback.
    """
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None, ""
    except OSError:
        return None, f"the app's settings file ({path}) could not be read"
    try:
        try:
            info = os.fstat(descriptor)
        except OSError:
            return None, f"the app's settings file ({path}) could not be read"
        if not stat.S_ISREG(info.st_mode):
            return None, (f"the app's settings file ({path}) is not a regular file, so the Hub's "
                          "model folder cannot be read from it")
        if info.st_uid != os.geteuid():
            return None, (f"the app's settings file ({path}) is not owned by this user, so the Hub's "
                          "model folder cannot be read from it")
        if info.st_mode & 0o077:
            return None, (f"the app's settings file ({path}) is readable by other users, so the "
                          "Hub's model folder cannot be read from it")
        try:
            handle = os.fdopen(descriptor, "r", encoding="utf-8")
        except OSError:
            return None, f"the app's settings file ({path}) could not be read"
        descriptor = -1                       # the handle owns the descriptor from here
        try:
            with handle:
                return handle.read(), ""
        except UnicodeDecodeError:
            return None, (f"the app's settings file ({path}) is not text, so the Hub's model folder "
                          "cannot be read from it")
        except OSError:
            return None, f"the app's settings file ({path}) could not be read"
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _saved_choice(home: Path) -> tuple[Path | None, str]:
    """(the saved library, a plain problem). The two are never both set.

    Mirrors the app's own reader: a **missing file**, a missing `modelsPath` key, a JSON `null` and
    an empty string all mean "no library chosen yet" - that is the app's own spelling for it. A file
    that is **there but cannot be understood** (unreadable, not text, not JSON, not an object, or a
    `modelsPath` that is not a path) is not "no choice": it is a fault the operator has to see,
    because the Hub may well be pointing at a folder this script can no longer name. The file-level
    guards the app applies before it reads anything live in `_read_private_text`.
    """
    path = preferences_file(home)
    text, problem = _read_private_text(path)
    if problem:
        return None, problem
    if text is None or not text.strip():
        return None, ""
    try:
        value = json.loads(text)
    except ValueError:
        return None, f"the app's settings file ({path}) is not JSON, so the Hub's model folder cannot be read from it"
    if not isinstance(value, dict):
        return None, f"the app's settings file ({path}) is not a settings object, so the Hub's model folder cannot be read from it"
    saved = value.get("modelsPath")
    if saved is None or (isinstance(saved, str) and not saved.strip()):
        return None, ""                       # no library chosen yet, the app's own reading
    if not isinstance(saved, str):
        return None, f"the app's settings file ({path}) names its model folder with something that is not a path"
    try:
        return Path(saved.strip()).expanduser(), ""
    except (OSError, RuntimeError):
        return None, f"the app's settings file ({path}) names a model folder that cannot be used as a path"


def configured_library(home: Path) -> Path | None:
    """The library the app saved for this home folder, or None when it saved none / it is unusable."""
    return _saved_choice(home)[0]


def resolve_library(home: Path | str | None = None,
                    environ: dict | None = None) -> Resolution:
    """The folder this Mac's models belong in, in the order the app itself resolves it.

    1. `modelsPath` in the app's saved settings - the folder the Hub is using. A file that cannot be
       understood is a problem, and when the saved folder **is not there** (an unplugged drive, a
       deleted folder) the answer carries that as a problem too rather than a different folder:
       filling another one would hide the reason the Hub reads as empty.
    2. The app's own data folder (`<data>/models`) when it already **holds something** - a Mac set up
       while that was the default keeps it. An empty one is ignored.
    3. `~/StudioHubModels`, else the earlier `~/Downloads/model-library`, when either holds the app's
       install record - a Mac the kit already filled keeps that folder.
    4. `~/StudioHubModels` - the canonical default a Mac that has never chosen one uses, so a new Mac
       and the kit's copy step agree by construction.

    `environ` is accepted so callers can pass a fixed environment; it is deliberately **not** read
    for `STUDIOHUB_MODELS_DIR`, which the app never consults (see this module's docstring).
    """
    home_path = Path(home) if home is not None else Path.home()

    saved, problem = _saved_choice(home_path)
    if problem:
        return Resolution(None, "unusable", problem)
    if saved is not None:
        if saved.is_dir():
            return Resolution(saved, "configured")
        return Resolution(None, "configured",
                          problem=f"the Hub is set to use {saved}, which is not there")

    app_owned = data_library(home_path)
    if holds_anything(app_owned):
        return Resolution(app_owned, "app-data")

    for relative, source in ((LIBRARY_FOLDER, "library"), (LEGACY_LIBRARY_FOLDER, "legacy")):
        candidate = home_path / relative
        if (candidate / INDEX_RELATIVE).is_file():
            return Resolution(candidate, source)

    return Resolution(default_library(home_path), "default")
