#!/usr/bin/env python3
"""Import existing voices straight into Studio Hub's voice library.

For people with a folder of reference clips from the old Voice Studio who do not want to add
each one by hand. It writes the library format the app reads, so the voices appear in
Voices the next time the app refreshes.

    python3 import_my_voices.py "/Volumes/KH-STUDIO-ATLAS/studio-models/voices"
    python3 import_my_voices.py "<folder>" --dry-run      # show what would happen
    python3 import_my_voices.py "<folder>" --language en  # default language for all

It reads only. Nothing in the source folder is changed or deleted.

Each voice is one folder containing a clip and, usually, a transcript; or loose clips with a
transcript beside them. A voice without words is still imported - it will show as needing a
transcript, and you can add or fix it in the app - because losing a clip you have is worse
than a row you have to finish later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
import wave
from pathlib import Path

AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")
TRANSCRIPT_EXTENSIONS = (".txt", ".text", ".srt", ".vtt", ".md")


def library_root() -> Path:
    """Where the app expects voices: <app data>/voice/voices.

    The worker resolves STUDIOHUB_DATA_DIR then appends 'voice'; in the packaged app the data
    root is ~/Library/Application Support/StudioHub Standalone.
    """
    override = os.environ.get("STUDIOHUB_VOICES_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    data = Path.home() / "Library/Application Support/StudioHub Standalone"
    return data / "voice" / "voices"


def plain_transcript(body: str) -> str:
    """Subtitle files are transcripts with timing in them; the clone wants the words only."""
    kept = []
    for line in body.splitlines():
        text = line.strip()
        if not text or text.upper() == "WEBVTT" or "-->" in text or text.isdigit():
            continue
        kept.append(text)
    return " ".join(kept)


def read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def wav_details(path: Path) -> tuple[float | None, int | None, int | None]:
    """Duration, sample rate and channels for a PCM WAV; nothing is guessed for other formats."""
    try:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate() or 0
            frames = stream.getnframes()
            channels = stream.getnchannels()
            duration = frames / rate if rate else None
            return (round(duration, 3) if duration else None, rate or None, channels or None)
    except (wave.Error, OSError, EOFError):
        return (None, None, None)


def find_voice(folder: Path) -> tuple[Path | None, str]:
    """Return (clip, transcript text) for one voice folder or one loose clip."""
    if folder.is_dir():
        items = sorted(p for p in folder.iterdir() if not p.name.startswith("."))
        clip = next((p for p in items if p.suffix.lower() in AUDIO_EXTENSIONS), None)
        for item in items:
            if item.suffix.lower() in TRANSCRIPT_EXTENSIONS:
                return clip, plain_transcript(read_text(item))
        # A transcript living beside the clip under a different name.
        if clip is not None:
            for suffix in TRANSCRIPT_EXTENSIONS:
                sibling = clip.with_suffix(suffix)
                if sibling.is_file():
                    return clip, plain_transcript(read_text(sibling))
        return clip, ""
    if folder.suffix.lower() in AUDIO_EXTENSIONS:
        for suffix in TRANSCRIPT_EXTENSIONS:
            sibling = folder.with_suffix(suffix)
            if sibling.is_file():
                return folder, plain_transcript(read_text(sibling))
        return folder, ""
    return None, ""


def name_for(folder: Path) -> str:
    if folder.is_dir():
        return folder.name
    return folder.stem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="the folder holding your voices")
    parser.add_argument("--dry-run", action="store_true", help="show what would be imported")
    parser.add_argument("--language", default="en", help="language code to record (default en)")
    parser.add_argument("--gender", default="n", choices=("n", "m", "f"),
                        help="n neutral, m male, f female (default n)")
    parser.add_argument("--license", default="self-owned",
                        choices=("self-owned", "public-domain", "permission", "unknown"),
                        help="rights to record (default self-owned: owned by me)")
    parser.add_argument("--root", type=Path, default=None,
                        help="the voice library folder, if it is not the default")
    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"Not a folder: {args.folder}")
        return 2
    destination = (args.root or library_root()).expanduser()
    existing: dict[str, str] = {}
    if destination.is_dir():
        for child in destination.iterdir():
            meta = child / "metadata.json"
            if meta.is_file():
                try:
                    existing[json.loads(meta.read_text()).get("name", "").lower()] = child.name
                except (OSError, ValueError):
                    continue

    entries = sorted(p for p in args.folder.iterdir() if not p.name.startswith("."))
    print(f"reading : {args.folder}")
    print(f"library : {destination}")
    print(f"already saved: {len(existing)} voice(s)")
    print()

    imported = skipped = 0
    for entry in entries:
        clip, transcript = find_voice(entry)
        if clip is None:
            continue
        name = name_for(entry)
        if name.lower() in existing:
            print(f"  skip   {name}  (already saved)")
            skipped += 1
            continue
        duration, rate, channels = wav_details(clip)
        voice_id = uuid.uuid4().hex[:12]
        # The app's own vocabulary: has_transcript is what decides whether a clone may use it.
        metadata = {
            "id": voice_id, "name": name, "language": args.language, "gender": args.gender,
            "notes": f"Imported from {args.folder.name}", "license": args.license,
            "source_url": None, "permission_acknowledged": True,
            "has_transcript": bool(transcript.strip()),
            "audio_extension": clip.suffix.lower(),
            "duration_seconds": duration, "sample_rate": rate, "channels": channels,
            "fleet_managed": False,
            "audio_sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
            "created_at": time.time(),
        }
        state = "ready" if transcript.strip() else "NO TRANSCRIPT (add it in the app)"
        print(f"  add    {name:<34} {clip.name:<20} {state}")
        imported += 1
        if args.dry_run:
            continue
        target = destination / voice_id
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
        shutil.copyfile(clip, target / f"reference{clip.suffix.lower()}")
        (target / "metadata.json").write_text(json.dumps(metadata, indent=2))
        if transcript.strip():
            (target / "transcript.txt").write_text(transcript.strip())

    print()
    if args.dry_run:
        print(f"DRY RUN - nothing written. {imported} would be imported, {skipped} skipped.")
        print("Run again without --dry-run to do it.")
    else:
        print(f"Imported {imported} voice(s), skipped {skipped}.")
        print("Open the app (or press Refresh on the Voices page) to see them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
