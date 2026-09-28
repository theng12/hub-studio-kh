#!/usr/bin/env python3
"""Stamp a kit drive's index documents **from the files actually on the drive**.

    python3 packaging/kit_stamp_index.py /Volumes/KH-STUDIO-NOVA
    python3 packaging/kit_stamp_index.py KH-STUDIO-NOVA     # a bare volume name is understood

**Why this exists.** The drive's index — the table that answers "which build is on this drive?" —
has now been wrong three times, and every time for the same reason: two of its three version rows
were **copied by hand** from a repository file, so they described whatever that file said on the day
it was written rather than what the drive carries. Recorded in the ledger twice (`0.6.63` printed
against a `0.6.70` bundle, `0.6.70` against `0.6.89`) and again in 0.6.91. The Studio Hub row was
fixed the same way this file fixes the other two: **derive it from the artefact beside it.**

What it reads, and what it refuses to guess:

  * **Studio Hub** — the row already points at the kit's generated
    `Studio Hub KH/focused-release-manifest.json`, and is left alone for that reason. This script
    does not restate a number the manifest already derives.
  * **Story Studio** — the newest `*.dmg` in `Story Studio/`, version read from its filename.
  * **GatewayKH** — the newest `*.dmg` in `GatewayKH/installers/`, version read from its filename.

A folder with no installer, or a filename with no version in it, is left **exactly as it is** and
reported: a stamped `unknown` would be a new wrong answer rather than a fix for an old one.

It also copies the kit's own `README.md` over the drive-root copy when one exists. Those two files
were the same 130-line document at different ages — the root one dated 2026-09-26 and naming Story
**1.4.219** while the kit beside it carried 1.4.234 — which is what a second copy with no check
looks like after a few releases.

**And it rewrites the installer filenames in the instructions, which is the defect that followed.**
Stamping the version *line* was not enough: the same documents say *"Open
`Story Studio KH 1.4.219-arm64.dmg`"* further down, because that filename was copied in by hand and
nothing derived it. On the three drives prepared on 2026-09-28 those steps pointed at installers
that had just been moved to `_trash` — an operator following the checklist would fail at step one.
So every backticked `*.dmg` in the kit's own text documents is rewritten from the installer that is
actually in that app's folder. A document that names a file beside it should be told that file's
name, not remember one.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

VERSION = re.compile(r"(\d+\.\d+\.\d+)")


def newest_installer(folder: Path, pattern: str = "*.dmg") -> tuple[Path | None, str | None]:
    """The newest matching installer, and the version its own filename states."""
    try:
        files = [p for p in folder.glob(pattern) if p.is_file()]
    except OSError:
        return None, None
    if not files:
        return None, None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    match = VERSION.search(newest.name)
    return newest, (match.group(1) if match else None)


def _stamp_row(text: str, label: str, version: str) -> tuple[str, bool]:
    """Set the version cell on the row whose first cell names `label`.

    Narrow on purpose: the row is matched by its own backticked folder name, and only the third
    cell of that one line is rewritten. A wider edit would be free to touch prose.
    """
    pattern = re.compile(
        r"^(\|\s*`" + re.escape(label) + r"/`\s*\|\s*[^|]+\|\s*)([^|]+)(\|)",
        re.MULTILINE,
    )
    new, count = pattern.subn(lambda m: f"{m.group(1)}{version} {m.group(3)}", text)
    return new, count == 1


def resolve_drive(argument: str) -> Path:
    """The drive a caller meant, whether they named a path or only the volume.

    **This is the defect that hid the last one.** Every caller passes the bare volume name it loops
    over, so `Path("KH-STUDIO-NOVA")` was a *relative* path: the kit index was looked for under the
    current directory, never found, and the write refused — on every drive ever prepared this way.
    The refusal was even printed in full, reading `no kit index at KH-STUDIO-NOVA/Video Generation
    Kit/...`, which is exactly what a path missing its `/Volumes` looks like and exactly what nobody
    reads at the end of a 30-line log.

    It cost nothing only by luck: the repository template the drives were copied from happened to
    name the current versions, so the *content* was right while the mechanism that derives it from
    the drive — the point of this script, and the fix for three recorded stale-index bugs — was dead.
    A name with no separator is therefore read as a volume, and every refusal below prints an
    absolute path so the next one of these is visible in the log rather than plausible in it.
    """
    candidate = Path(argument)
    if candidate.exists() or "/" in argument:
        return candidate
    return Path("/Volumes") / argument


def stamp(drive: Path) -> int:
    kit = drive / "Video Generation Kit"
    index = kit / "Studio Hub KH" / "README.md"
    if not index.exists():
        print(f"no kit index at {index.resolve()} — is {drive.resolve()} the drive?")
        return 1

    text = index.read_text(encoding="utf-8")
    problems: list[str] = []

    story, story_version = newest_installer(kit / "Story Studio")
    if story is None or story_version is None:
        problems.append(f"Story Studio: no installer with a version in its name under {kit / 'Story Studio'}")
    else:
        text, ok = _stamp_row(text, "Story Studio", story_version)
        if not ok:
            problems.append("Story Studio: the index has no row for that folder")
        else:
            print(f"Story Studio  -> {story_version}   ({story.name})")

    gateway, gateway_version = newest_installer(kit / "GatewayKH" / "installers")
    if gateway is None or gateway_version is None:
        problems.append(
            f"GatewayKH: no installer with a version in its name under {kit / 'GatewayKH' / 'installers'}")
    else:
        text, ok = _stamp_row(text, "GatewayKH", gateway_version)
        if not ok:
            problems.append("GatewayKH: the index has no row for that folder")
        else:
            print(f"GatewayKH     -> {gateway_version}   ({gateway.name})")

    if not problems:
        # A separate count: `problems` is the list of *reasons it refused*, and a folder it could
        # not stamp belongs in it as a reason, not as a number added to a list.
        unstampable = stamp_documents(drive)
        if unstampable:
            problems.append(f"{unstampable} app folder(s) had no installer to read")

    if problems:
        # **Nothing is written when anything is unclear.** A half-stamped index is worse than the
        # stale one it replaced, because it looks current.
        for line in problems:
            print(f"REFUSED: {line}")
        return 2

    index.write_text(text, encoding="utf-8")
    print(f"stamped {index}")

    # The drive-root copy is the same document; leaving an older one there is how the two came to
    # name different versions in the first place.
    root_copy = kit / "README.md"
    if root_copy.exists():
        shutil.copyfile(index, root_copy)
        print(f"refreshed the drive-root copy: {root_copy}")
    return 0


#: The text documents inside an app's kit folder that name its installer.
DOC_SUFFIXES = (".md", ".txt", ".command", ".sh")
#: A backticked installer path. Deliberately narrow: a whole-token match inside backticks, ending
#: in `.dmg`, so prose about "the DMG" is never touched.
BACKTICKED_DMG = re.compile(r"`([^`\n]*?)([A-Za-z0-9][A-Za-z0-9 ._+-]*?\.dmg)`")
#: The document's own version sentence — `**Version 1.4.219.**`. Rewritten here too, so one script
#: is responsible for the whole claim rather than leaving this line to whichever release step
#: happened to run: measured on the 2026-09-28 drives, the top line had been stamped while a step
#: three lines below still said `Story Studio KH 1.4.219-arm64.dmg`.
VERSION_SENTENCE = re.compile(r"\*\*Version \d+\.\d+\.\d+\.\*\*")


def rewrite_installer_names(folder: Path, installer: Path, *, prefix: str = "") -> int:
    """Point every backticked `*.dmg` in this folder's documents at `installer`.

    `prefix` is the path the documents use for the installer when it is not beside them — the
    Gateway kit keeps its DMG in `installers/`, so its instructions say `installers/<name>`.

    Only the **basename** is replaced, and only for a backticked token that already looks like this
    app's installer (its first component before the version matches). That keeps a document free to
    mention another app's DMG, or a checksum file, without this rewriting it.
    """
    target = f"{prefix}{installer.name}"

    def family(name: str) -> str:
        """Everything in a filename **before its version** — the part that names the app.

        Not `split("-")[0]`: the Gateway's installer is `gateway-kh-0.2.67.dmg`, where the first
        hyphen is inside the name, while Story's is `Story Studio KH 1.4.235-arm64.dmg`, where the
        version sits mid-name and the hyphen comes after it. Cutting at the version handles both,
        and it is the only rule that does not have to know which app it is looking at.
        """
        match = VERSION.search(name)
        return (name[:match.start()] if match else name).strip().lower()

    wanted = family(installer.name)
    changed = 0
    for path in sorted(folder.rglob("*")):
        if path.is_dir() or path.suffix not in DOC_SUFFIXES:
            continue
        # Never rewrite inside the model library or a nested app bundle.
        if ".app/" in str(path) or path.name.startswith("."):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        def _one(match: re.Match[str]) -> str:
            nonlocal changed
            directory, name = match.group(1), match.group(2)
            if family(name) != wanted or name == target:
                return match.group(0)
            # The old text's own directory is kept when it had one (`installers/`), so the
            # document keeps pointing at the same place relative to itself.
            keep = directory if directory else prefix
            changed += 1
            return f"`{keep}{installer.name}`"

        new_text = BACKTICKED_DMG.sub(_one, text)
        # The version sentence, from the same installer — only in a document that is *about* this
        # app, which is the folder this function was handed.
        stated = VERSION.search(installer.name)
        if stated is not None:
            stamped = VERSION_SENTENCE.sub(f"**Version {stated.group(1)}.**", new_text)
            if stamped != new_text:
                changed += 1
                new_text = stamped
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"   rewrote installer name(s) in {path.relative_to(folder)}")
    return changed


def stamp_documents(drive: Path) -> int:
    """Rewrite the installer names in each app's instructions, from what is on the drive."""
    kit = drive / "Video Generation Kit"
    problems = 0
    story, _ = newest_installer(kit / "Story Studio")
    if story is not None:
        rewrite_installer_names(kit / "Story Studio", story)
    else:
        problems += 1
    gateway, _ = newest_installer(kit / "GatewayKH" / "installers")
    if gateway is not None:
        # The Gateway kit is the one that keeps its DMG in a subfolder, so its documents say
        # `installers/<name>` — and its own folder is the root of those documents.
        rewrite_installer_names(kit / "GatewayKH", gateway, prefix="installers/")
    else:
        problems += 1
    return problems


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(stamp(resolve_drive(sys.argv[1])))
