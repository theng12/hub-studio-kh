#!/bin/bash
# Prepare one SSD volume's kit to the current standard.
#
#   bash .build/prepare-kit.sh KH-STUDIO-XXXX
#
# Nothing is deleted: anything displaced goes to a `_trash` folder on that volume,
# which the owner empties by hand.
set -u
# The checkout this script lives in, so a release worktree prepares drives from ITS sources and
# never from a hard-coded primary checkout. The script is often run through a per-release link at
# `<worktree>/.build/prepare-kit.sh`; `BASH_SOURCE` is that link's path, so the two levels up are
# the worktree the operator is working in.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOLUME="${1:?usage: prepare-kit.sh <volume name>}"
# The kit moved into the "Video Generation Kit" folder; the old path is kept as a
# fallback so this still works on a drive that has not been restructured yet.
KIT="/Volumes/$VOLUME/Video Generation Kit/Studio Hub KH"
[ -d "$KIT" ] || KIT="/Volumes/$VOLUME/Studio Hub App"
# The staged app this drive is prepared from. It was hard-coded to a single stage directory, which
# is how a drive gets prepared from an old build while the release moves on; the stage is now named
# by the caller (`STAGE_DIR=... bash prepare-kit.sh <volume>`) and defaults to the newest one that
# exists, so the common case is right and the exceptional case is explicit.
if [ -z "${STAGE_DIR:-}" ]; then
  STAGE_DIR=$(ls -d "$ROOT"/.build/StudioHub-[0-9]* 2>/dev/null | sort | tail -1)
fi
NEWAPP="${STAGE_DIR%/}/Studio Hub.app"
NEWTREE="${STAGE_DIR%/}/Studio Hub.app.tree-sha256"
echo "### stage: $STAGE_DIR"
PY="$ROOT/.build/voice-runtime/bin/python3"

[ -d "$KIT" ] || { echo "no kit at $KIT"; exit 1; }
[ -d "$NEWAPP" ] || { echo "no staged app at $NEWAPP"; exit 1; }

echo "### $VOLUME: launchers, helper and verifier"
cp -f "$ROOT/install_app.command" "$KIT/1 - Install the app.command"
cp -f "$ROOT/copy-models.command" "$KIT/2 - Copy the models.command"
# The one to double-click for an update: it runs the two above in order, and skips the ~5 GB app
# copy when this Mac already has that exact build. Both of those files remain, because they are
# what to reach for when a step needs looking at on its own.
cp -f "$ROOT/Update this Mac.command" "$KIT/Update this Mac.command"
# Retired 2026-10-30 (owner): the Lite Mode copy launcher. A drive prepared before then may still
# carry it at the kit root and nothing writes it again, so it is moved to the volume's own `_trash`
# here - never deleted - the same rule as the app swap and the Technical prune. Lite Mode itself is
# unchanged and is ticked in Settings; there is no Lite-only model set.
if [ -e "$KIT/2 - Copy the Lite Mode models.command" ]; then
  mkdir -p "/Volumes/$VOLUME/_trash"
  mv "$KIT/2 - Copy the Lite Mode models.command" \
     "/Volumes/$VOLUME/_trash/2 - Copy the Lite Mode models.command-$(date +%Y%m%d%H%M%S)" \
    && echo "   _trash: 2 - Copy the Lite Mode models.command (retired)"
fi
mkdir -p "$KIT/Technical"
cp -f "$ROOT/qualification/copy_models_for_this_mac.py" "$KIT/Technical/copy_models_for_this_mac.py"
cp -f "$ROOT/qualification/verify-kit.sh" "$KIT/Technical/verify-kit.sh"
# `verify-kit.sh` runs `$HERE/verify-launch.sh` (line 125) and this script never copied it. It
# worked only because older kits had left a copy there, so a freshly prepared drive had a verifier
# that could not run - which the Technical prune exposed by retiring the stale copy. Both files
# travel together now.
cp -f "$ROOT/qualification/verify-launch.sh" "$KIT/Technical/verify-launch.sh"
chmod +x "$KIT/Technical/verify-launch.sh"
# Retired from the kit 2026-10-01 (owner: keep only what is still used): the standalone 8 GB Z-Image
# runner (superseded by RUN-8GB-IMAGE, which runs Z-Image through the Hub; Z-Image is qualified on 8 GB),
# the chat-tier runner (16 GB measured and QUALIFIED, 8 GB a documented exclusion, LLM phase shelved) and
# the LTX video runners (qualified at 24 GB on the 32 GB Mac; 16 GB not offered). All of them stay in
# `qualification/` in git; to bring one back, add its cp/chmod lines here and its names to MANAGED below.
# The model record belongs on the drive too: it is the page that answers "can this Mac run that",
# and the Mac in question is usually the one the drive is plugged into.
cp -f "$ROOT/docs/model-benchmarks.md" "$KIT/Technical/model-benchmarks.md"
chmod 644 "$KIT/Technical/model-benchmarks.md"
# The 8 GB text-to-speech test (Qwen3-TTS 0.6B 8-bit, VoxCPM2 4-bit, OmniVoice bf16): a double-click wrapper, the measurement, its README, and the two texts the 32 GB Mac
# read (S2 and S4) so both machines are timed on identical input. Without the texts beside it the
# script refuses, so all four travel together.
cp -f "$ROOT/qualification/RUN-8GB-TTS.command" "$KIT/Technical/RUN-8GB-TTS.command"
cp -f "$ROOT/qualification/RUN-8GB-BREEZE.command" "$KIT/Technical/RUN-8GB-BREEZE.command"
cp -f "$ROOT/qualification/eight-gb-tts-run.sh" "$KIT/Technical/eight-gb-tts-run.sh"
cp -f "$ROOT/qualification/README-8gb-tts.md" "$KIT/Technical/README-8gb-tts.md"
cp -f "$ROOT/qualification/tts-script-S2.txt" "$KIT/Technical/tts-script-S2.txt"
cp -f "$ROOT/qualification/tts-script-S4.txt" "$KIT/Technical/tts-script-S4.txt"
chmod +x "$KIT/Technical/RUN-8GB-TTS.command" "$KIT/Technical/RUN-8GB-BREEZE.command" "$KIT/Technical/eight-gb-tts-run.sh"
# The 16 GB Fish test (Fish Audio S2 Pro 4-bit, 8-bit, bf16): a double-click wrapper that first imports those three packages from the kit's
# model-library onto the 16 GB Mac (bf16 / 8-bit are shelved / 24 GB-tier, so the normal copy step skips them) through import_kit_packages.py,
# then runs eight-gb-tts-run.sh --models fish4,fish8,fishbf16 --target-gb 16. Needs the 8 GB test's files beside it (ssd_wear.py, the two texts).
cp -f "$ROOT/qualification/RUN-16GB-FISH.command" "$KIT/Technical/RUN-16GB-FISH.command"
cp -f "$ROOT/qualification/import_kit_packages.py" "$KIT/Technical/import_kit_packages.py"
chmod 755 "$KIT/Technical/RUN-16GB-FISH.command"
chmod 644 "$KIT/Technical/import_kit_packages.py"
chmod 644 "$KIT/Technical/README-8gb-tts.md" "$KIT/Technical/tts-script-S2.txt" "$KIT/Technical/tts-script-S4.txt"
# ssd_wear.py (the internal-SSD write counter) is shared by the 8 GB TTS and image runners; each refuses to start without it beside it.
cp -f "$ROOT/qualification/ssd_wear.py" "$KIT/Technical/ssd_wear.py"
chmod 644 "$KIT/Technical/ssd_wear.py"
# The 8 GB image test (Z-Image-Turbo 4-bit thumbnails, FLUX.2 Klein 4B 4-bit scenes) through the Hub: a double-click wrapper, the shell
# shim that finds the app's Python, the Python program, and its README (ssd_wear.py above travels with it).
cp -f "$ROOT/qualification/RUN-8GB-IMAGE.command" "$KIT/Technical/RUN-8GB-IMAGE.command"
cp -f "$ROOT/qualification/eight-gb-image-run.sh" "$KIT/Technical/eight-gb-image-run.sh"
cp -f "$ROOT/qualification/eight_gb_image_run.py" "$KIT/Technical/eight_gb_image_run.py"
cp -f "$ROOT/qualification/README-8gb-image.md" "$KIT/Technical/README-8gb-image.md"
chmod +x "$KIT/Technical/RUN-8GB-IMAGE.command" "$KIT/Technical/eight-gb-image-run.sh"
chmod 644 "$KIT/Technical/eight_gb_image_run.py" "$KIT/Technical/README-8gb-image.md"
cp -f "$ROOT/qualification/NODE-SETUP-CHECKLIST.md" "$KIT/NODE-SETUP-CHECKLIST.md"
# The two index documents, which name every app's version. They are the first thing an operator
# reads and they were the *last* thing anyone updated: the drives were found carrying "0.6.70" in
# one and "0.6.58" in the other while the bundle beside them was newer. Nothing copied them, so
# every drive kept whatever it was born with. A prepared drive now gets them with its app.
cp -f "$ROOT/packaging/kit-readmes/README.md" "$KIT/README.md"
cp -f "$ROOT/packaging/kit-readmes/Studio Hub KH.md" "$KIT/Studio Hub KH.md"
chmod 644 "$KIT/README.md" "$KIT/Studio Hub KH.md"
# **The two app versions are stamped from the installers on the drive**, not copied from the
# repository, because a copy of a fact is what made the index name 1.4.219 against a 1.4.234 drive.
# The script refuses to write a half-stamped index and says which folder confused it.
# The full path, not the bare name: `$VOLUME` is relative, which made the stamper look for
# the kit under the current directory and refuse on every drive it was ever run against.
"$PY" "$ROOT/packaging/kit_stamp_index.py" "/Volumes/$VOLUME" || echo "   kit index NOT stamped — see above"
# 755, not +x: `+x` only adds the execute bit to whatever mode the file arrived with, so a source
# written 600 became 711 on the drive while its neighbours were 755 — the same command behaving
# differently depending on the account reading it.
chmod 755 "$KIT/1 - Install the app.command" "$KIT/2 - Copy the models.command" \
          "$KIT/Update this Mac.command" \
          "$KIT/Technical/verify-kit.sh"

echo "### $VOLUME: Technical folder is a managed set"
# Everything above is what this kit carries. Anything else in Technical came from an earlier kit
# layout, and nothing has ever removed it: the owner found 29 files where 9 are written, including
# `8gb-z-image-run.sh` alongside `eight-gb-z-image-run.sh` (identical) and `check-everything.sh`
# alongside `verify-launch.sh` (identical). A folder that only grows is a folder nobody can read,
# and one stale copy of a script is how an operator runs the wrong one.
#
# Moved, never deleted, to the volume's own _trash — the same rule as the app swap and the model
# prune. The list below is written out rather than inferred so a file that disappears from this
# script is visibly retired rather than silently kept.
MANAGED="copy_models_for_this_mac.py verify-kit.sh verify-launch.sh model-benchmarks.md \
RUN-8GB-TTS.command RUN-8GB-BREEZE.command eight-gb-tts-run.sh README-8gb-tts.md tts-script-S2.txt tts-script-S4.txt ssd_wear.py \
RUN-16GB-FISH.command import_kit_packages.py \
RUN-8GB-IMAGE.command eight-gb-image-run.sh eight_gb_image_run.py README-8gb-image.md"
PRUNED=0
for entry in "$KIT/Technical"/* "$KIT/Technical"/.[!.]*; do
  [ -e "$entry" ] || continue
  name=$(basename "$entry")
  # Result folders the 8 GB / quick-check runners write beside themselves are evidence, not leftovers: leave them
  # in place for the owner/coordinator to archive.
  case "$name" in "TTS 8GB Run "*|"TTS "[0-9]*"GB Run "*|"Image 8GB Run "*|*" Quick Check "*) continue ;; esac
  keep=no
  for managed in $MANAGED; do [ "$name" = "$managed" ] && keep=yes; done
  [ "$keep" = yes ] && continue
  mkdir -p "/Volumes/$VOLUME/_trash/Technical-retired"
  mv "$entry" "/Volumes/$VOLUME/_trash/Technical-retired/$name" && PRUNED=$((PRUNED + 1))
done
if [ "$PRUNED" -gt 0 ]; then
  echo "   retired $PRUNED leftover(s) this kit does not carry -> /Volumes/$VOLUME/_trash/Technical-retired"
fi

echo "### $VOLUME: app"
# The app it replaces is kept, not deleted: a drive that has to serve a Mac the new build
# disagrees with is worth being able to walk back to. It goes to the volume's own _trash,
# stamped with the version it was, alongside everything else displaced from this drive.
if [ -d "$KIT/Studio Hub.app" ]; then
  OLDVER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
             "$KIT/Studio Hub.app/Contents/Info.plist" 2>/dev/null || echo unknown)
  mkdir -p "/Volumes/$VOLUME/_trash"
  chmod -R u+w "$KIT/Studio Hub.app" 2>/dev/null || true
  DEST="/Volumes/$VOLUME/_trash/Studio Hub.app-$OLDVER"
  [ -e "$DEST" ] && DEST="$DEST-$(date +%Y%m%d%H%M%S)"
  mv "$KIT/Studio Hub.app" "$DEST" && echo "   _trash: Studio Hub.app-$OLDVER"
fi
if [ -e "$KIT/Studio Hub.app" ]; then
  echo "   ERROR: the old app could not be displaced; leaving this volume untouched"
  exit 1
fi
rsync -a "$NEWAPP/" "$KIT/Studio Hub.app/"
cp -f "$NEWTREE" "$KIT/Studio Hub.app.tree-sha256"
APPVER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
           "$KIT/Studio Hub.app/Contents/Info.plist" 2>/dev/null || echo unknown)
# The key is `implementation_commit` — the release manifest's own name for the commit the app was
# staged from. Reading `commit` produced "unknown" on four drives, which is honest but useless when
# the value is sitting right there under a different name.
APPCOMMIT=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['implementation_commit'][:7])" \
              "$KIT/Studio Hub.app/Contents/Resources/release-manifest.json" 2>/dev/null || echo unknown)
echo "   version $APPVER files $(find "$KIT/Studio Hub.app" -type f | wc -l | tr -d ' ') commit $APPCOMMIT"
# The index document is stamped from the app that is now on this drive, not from whatever the
# repository said when the file was written. The page exists to answer "which build is this?" and a
# hand-maintained answer to that question has been wrong three times: 0.6.63 against a 0.6.70
# bundle, 0.6.70 against 0.6.89, and 0.6.58 for a whole release cycle. Two lines, rewritten from
# the artefact itself, end the class.
"$PY" - "$KIT/Studio Hub KH.md" "$APPVER" "$APPCOMMIT" <<'STAMP'
import pathlib, re, sys
path, version, commit = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text()
text = re.sub(r"\*\*Version [0-9.]+\*\* \(build `[0-9a-f]+`\)\.",
              f"**Version {version}** (build `{commit}`).", text, count=1)
text = re.sub(r"`[0-9]+\.[0-9]+\.[0-9]+ · [0-9a-f]+`",
              f"`{version} · {commit}`", text, count=1)
path.write_text(text)
print(f"   index document stamped {version} · {commit}")
STAMP

echo "### $VOLUME: retire the Pinokio kit into _trash (nothing deleted)"
mkdir -p "/Volumes/$VOLUME/_trash"
for item in studio-mac-bootstrap studio-models \
            "START HERE - TerraNash Mac Setup.md" "Setup Fleet Tokens.md" \
            "Setup for Fleet.rtf" AGENTS.md "READ-ME-FIRST.md" \
            .studio-mac-fleet-ssd.json; do
  if [ -e "/Volumes/$VOLUME/$item" ] && [ ! -e "/Volumes/$VOLUME/_trash/$item" ]; then
    mv "/Volumes/$VOLUME/$item" "/Volumes/$VOLUME/_trash/" && echo "   _trash: $item"
  fi
done

echo "### $VOLUME: retired models leave the model library (moved to _trash, nothing deleted)"
# The one retired-models list ships inside the staged app (components/retired_models.py). A package id on it
# that still has a folder in this drive's model-library is moved to the volume's own _trash, the same rule as
# the app swap and the Technical prune. Added 2026-10-01 with the LTX-2.3 retirement (Gemma is NOT retired).
RETIRED_IDS=$("$PY" - "$NEWAPP/Contents/Resources/components" <<'RETIRED'
import sys
sys.path.insert(0, sys.argv[1])
import retired_models
print("\n".join(i for i in retired_models.RETIRED_MODELS if "/" not in i and "\\" not in i))
RETIRED
)
for retired_id in $RETIRED_IDS; do
  src="$KIT/model-library/$retired_id"
  [ -d "$src" ] && [ ! -L "$src" ] || continue
  mkdir -p "/Volumes/$VOLUME/_trash/model-library-retired"
  dest="/Volumes/$VOLUME/_trash/model-library-retired/$retired_id"
  [ -e "$dest" ] && dest="$dest-$(date +%Y%m%d%H%M%S)"
  mv "$src" "$dest" && echo "   _trash: model-library/$retired_id"
done

echo "### $VOLUME: retired models leave the model library index too"
# Moving a retired model's folder is not enough: `model-library/.studiohub/installed.json` still listed
# it, and `verify-kit.sh` checks every file the index records, so it failed on every drive ("missing:
# ltx-2-3-22b-ram-12gb-mlx/...") the day LTX-2.3 was retired (found 2026-10-02, pruned by hand). An entry is
# dropped only when its package is on the retired list AND its folder is gone from the library - one that
# is still on disk (a move that failed) stays, so the verifier keeps saying so. Any other missing entry is
# left alone on purpose: that is a damaged copy, and the verifier exists to say so. The old index is copied to
# the volume's own _trash first (nothing deleted), and the new one is written atomically with mode 0600 in
# the Hub's own format (sorted keys). This block is also what tests/test_kit_index_prune.py runs.
"$PY" - "$KIT/model-library" "/Volumes/$VOLUME/_trash" "$NEWAPP/Contents/Resources/components" <<'PRUNEINDEX'
import json, os, shutil, sys, time
from pathlib import Path

library, trash, components = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
index_path = library / ".studiohub" / "installed.json"
if not index_path.is_file() or index_path.is_symlink():
    print("   index: none to prune"); raise SystemExit(0)
sys.path.insert(0, components)
import retired_models
try:
    index = json.loads(index_path.read_text())
except ValueError as exc:
    print(f"   index: NOT pruned, it is not readable JSON ({exc})"); raise SystemExit(0)
if not isinstance(index, dict):
    print("   index: NOT pruned, it is not an object"); raise SystemExit(0)

def on_disk(package_id, record):
    folders = {package_id}
    folder = record.get("folder") if isinstance(record, dict) else None
    if isinstance(folder, str) and folder and ".." not in Path(folder).parts and not Path(folder).is_absolute():
        folders.add(folder)
    return any((library / name).exists() for name in folders)

gone = sorted(
    package_id for package_id in retired_models.RETIRED_MODELS
    if "/" not in package_id and "\\" not in package_id and package_id in index
    and not on_disk(package_id, index[package_id])
)
if not gone:
    print("   index: nothing to prune"); raise SystemExit(0)
trash.mkdir(parents=True, exist_ok=True)
backup = trash / f"installed.json.before-prune-{time.strftime('%Y%m%d%H%M%S')}"
shutil.copy2(index_path, backup)
for package_id in gone:
    del index[package_id]
tmp = index_path.with_name("installed.json.tmp")
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w") as stream:
    json.dump(index, stream, sort_keys=True)
    stream.flush()
    os.fsync(stream.fileno())
os.replace(tmp, index_path)
print(f"   index: pruned {', '.join(gone)} (old index kept: _trash/{backup.name})")
PRUNEINDEX

echo "### $VOLUME: kit manifest"
"$PY" "$ROOT/packaging/release_kit.py" --app "$KIT/Studio Hub.app" \
      --library "$KIT/model-library" \
      --output "$KIT/focused-release-manifest.json" | tail -14
