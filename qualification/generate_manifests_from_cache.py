#!/usr/bin/env python3
"""Generate Hub model manifests for models already present in the Hugging Face cache.

The Hub schedules a model only if it is in the packaged catalogue, and the packaged catalogue is a
list of manifests: each one names the repo, the exact revision, every file with its size and sha256,
the memory floor, and any companion packages.  This writes those manifests from what is already on
disk, so a model can be adopted without downloading it again.

Companions matter: `model_packages.preflight` reports a model as verified only when every package in
its `dependencies` is itself verified, which is why a MOSS-TTS import sat at `partial` until its audio
tokenizer arrived.  Families that need one therefore get a companion manifest as well.

The cache lives inside the library root (`<library>/hub/models--...`), and `import_folder` registers
rather than copies when the source is inside the library - so adopting these models costs no extra
disk.

  python3 qualification/generate_manifests_from_cache.py            # dry run: print the plan
  python3 qualification/generate_manifests_from_cache.py --write    # write manifests + import them
"""
from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.voice.app.backend import catalog  # noqa: E402

HUB = Path.home() / "StudioHubModels" / "hub"
LIBRARY = Path.home() / "StudioHubModels"
OUT = ROOT / "model-manifests"
ENGINE = "voice-mlx"
SCHEMA = 1
SKIP = (".gitattributes", ".gitignore", "README.md", ".git")

# The owner's pick: Group A (staged, fits this Mac) plus the three giants, so a bigger Mac can use
# them later while the drive kit skips them here.
CHOSEN = [
    "SWivid/F5-TTS",
    "mlx-community/LongCat-AudioDiT-1B-4bit",
    "mlx-community/Kokoro-82M-bf16",
    "mlx-community/chatterbox-4bit",
    "mlx-community/chatterbox-8bit",
    "mlx-community/chatterbox-turbo-4bit",
    "mlx-community/ornithopterus-3b-0.1-ft-4bit",
    "mlx-community/VibeVoice-Realtime-0.5B-4bit",
    "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit",
    "mlx-community/Audio8-TTS-Preview-0.6b-bf16",
    "mlx-community/MOSS-TTS-Nano-100M",
    "mlx-community/fish-audio-s2-pro-8bit",
    "mlx-community/fish-audio-s2-pro-bf16",
    "mlx-community/echo-tts-base",
]
CHOSEN = [repo.replace("ornithopterus", "orpheus") for repo in CHOSEN]


def field(entry, name, default=None):
    return entry.get(name, default) if isinstance(entry, dict) else getattr(entry, name, default)


def by_repo() -> dict:
    return {field(entry, "repo"): entry for entry in getattr(catalog, "CATALOG", ())}


def companions_for(repo: str, entries: dict) -> list[dict]:
    entry = entries.get(repo)
    family = field(entry, "family") if entry else None
    if not isinstance(family, str):
        return []
    return list(getattr(catalog, "FAMILY_COMPANIONS", {}).get(family, ()))


def slug(repo: str) -> str:
    return repo.split("/", 1)[1].lower().replace("_", "-").replace(".", "-")


def repo_dir(repo: str) -> Path:
    return HUB / ("models--" + repo.replace("/", "--"))


def revision_and_snapshot(repo: str) -> tuple[str, Path]:
    ref = repo_dir(repo) / "refs" / "main"
    revision = ref.read_text().strip()
    snapshot = repo_dir(repo) / "snapshots" / revision
    if not snapshot.is_dir():
        raise SystemExit(f"{repo}: no snapshot for revision {revision}")
    return revision, snapshot


def wanted(relative: str, *, allow, ignore) -> bool:
    name = Path(relative).name
    if name in SKIP:
        return False
    if allow and not any(fnmatch.fnmatch(relative, pattern) for pattern in allow):
        return False
    return not any(fnmatch.fnmatch(relative, pattern) for pattern in (ignore or ()))


def manifest_for(repo: str, *, entries: dict, dependencies: list[str]) -> dict:
    entry = entries.get(repo)
    revision, snapshot = revision_and_snapshot(repo)
    allow = field(entry, "allow_patterns")
    ignore = field(entry, "ignore_patterns") or ()
    files = []
    for path in sorted(snapshot.rglob("*")):
        # Snapshot files are symlinks into blobs/ - that is HuggingFace's own layout, and opening
        # them follows the link, so hashing the target is exactly what the manifest should record.
        if not path.is_file():
            continue
        relative = path.relative_to(snapshot).as_posix()
        if not wanted(relative, allow=allow, ignore=ignore):
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        files.append({
            "path": f"model/{relative}",
            "source_path": relative,
            "size": path.stat().st_size,
            "sha256": digest.hexdigest(),
            "urls": [f"https://huggingface.co/{repo}/resolve/{revision}/{relative}"],
        })
    if not files:
        raise SystemExit(f"{repo}: no files selected (check allow/ignore patterns)")
    floor = field(entry, "min_unified_memory_gb")
    if not isinstance(floor, int):
        floor = 8
    return {
        "schema_version": SCHEMA,
        "package_id": slug(repo),
        "model_id": repo,
        "revision": revision,
        "engine": ENGINE,
        "minimum_macos": field(entry, "minimum_macos") or "14.0",
        "source": {
            "provider": "huggingface",
            "captured_at": __import__("datetime").date.today().isoformat(),
            "sources": [{"repo": repo, "revision": revision}],
            "layout": {"model": {"repo": repo, "revision": revision, "destination": "model"}},
        },
        "defaults": {"parameters_source": f"model/{files[0]['source_path']}"},
        "qualification": {
            "minimum_unified_memory_gb": floor,
            "recommended_unified_memory_gb": max(floor, 16),
            "status": "unqualified",
            "reason": "Generated from the model files already on disk; local inference is unmeasured.",
            "memory_evidence": "Memory floor taken from the Voice catalogue's declared minimum.",
        },
        "files": files,
        "dependencies": dependencies or None,
    }


def main() -> int:
    write = "--write" in sys.argv
    entries = by_repo()
    manifests, missing = {}, []
    companion_repos = []
    for repo in CHOSEN:
        if repo not in entries:
            missing.append(repo)
            continue
        comps = companions_for(repo, entries)
        companion_repos.extend(field(c, "repo") for c in comps)
        manifests[repo] = manifest_for(
            repo, entries=entries,
            dependencies=[slug(field(c, "repo")) for c in comps])
    for repo in dict.fromkeys(companion_repos):
        manifests[repo] = manifest_for(repo, entries={}, dependencies=[])
    print(f"models chosen: {len(CHOSEN)} | manifests to write: {len(manifests)}"
          f" | companions: {len(set(companion_repos))}")
    if missing:
        print("  NOT in the catalogue:", ", ".join(missing))
    total = 0
    for repo, manifest in sorted(manifests.items()):
        size = sum(entry["size"] for entry in manifest["files"])
        total += size
        deps = ", ".join(manifest["dependencies"] or []) or "-"
        print(f"  {manifest['package_id']:34s} {size/1e9:6.2f} GB  {len(manifest['files']):3d} files"
              f"  floor {manifest['qualification']['minimum_unified_memory_gb']:>2d} GB  deps: {deps}")
    print(f"  total {total/1e9:.1f} GB (already on disk)")
    if not write:
        print("\ndry run - pass --write to write model-manifests/ and import")
        return 0

    OUT.mkdir(exist_ok=True)
    from components import model_packages
    for repo, manifest in sorted(manifests.items()):
        path = OUT / f"{manifest['package_id']}.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    # Companions first: a model's preflight needs them already registered.
    ordered = sorted(manifests.items(), key=lambda row: bool(row[1]["dependencies"]))
    for repo, manifest in ordered:
        _, snapshot = revision_and_snapshot(repo)
        try:
            result = model_packages.import_folder(manifest, snapshot, LIBRARY)
            print(f"  imported {manifest['package_id']}: {result.get('state') if isinstance(result, dict) else result}")
        except Exception as exc:
            print(f"  IMPORT FAILED {manifest['package_id']}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
