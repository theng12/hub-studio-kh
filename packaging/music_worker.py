"""Private bundle-relative Music entry point; run with immutable Python -I -B.

The Music worker reuses the immutable Voice Python for the shared ML stack; nothing is
installed or downloaded at runtime.  It serves Stable Audio 3 (in this runtime) and YuE2 (in
its own pinned runtime, ``runtimes/music-yue2``).

Standalone use (no app wiring required yet)::

    /path/to/pinned/voice-runtime/bin/python -I -B packaging/music_worker.py \\
        --data-dir ~/MusicWorker/data \\
        --models-dir ~/MusicWorker/models \\
        --ready-file ~/MusicWorker/data/ready.json \\
        --hub-token-file ~/MusicWorker/data/hub.token \\
        --port 47899

The CLI shape (``--data-dir`` / ``--models-dir`` / ``--ready-file`` /
``--hub-token-file`` / ``--parent-pid`` / ``--port`` / ``--offline`` /
``--check``) matches ``image_worker.py`` and ``subtitle_worker.py``. Checkpoints always
come from the verified package receipt in ``--models-dir``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

RESOURCES = Path(__file__).resolve().parent
sys.path.insert(0, str(RESOURCES))
sys.path.insert(0, str(RESOURCES.parent))
from voice_worker import read_private_token, run_server  # noqa: E402

# The worker's default model; `backend.catalog.DEFAULT_REPO` is the same package.
PACKAGE_ID = "stable-audio-3-medium"
MANIFEST_NAME = f"{PACKAGE_ID}.json"


def _resolve_source(resources: Path) -> Path:
    """The directory holding ``backend/`` for this worker.

    Packaged: ``<Resources>/components/music/app``.  Source checkout:
    ``<repo>/components/music/app`` (``resources`` is ``<repo>/packaging``).
    """
    packaged = resources / "components" / "music" / "app"
    if packaged.is_dir():
        return packaged
    return resources.parent / "components" / "music" / "app"


def _resolve_manifest(resources: Path) -> Path | None:
    for candidate in (resources / "model-manifests" / MANIFEST_NAME,
                      resources.parent / "model-manifests" / MANIFEST_NAME):
        if candidate.is_file():
            return candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--hub-token-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--source", type=Path, default=None,
                        help="override the directory containing backend/")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")

    resources = RESOURCES
    bundle = resources.parent.parent
    data = args.data_dir.expanduser().resolve()
    models = args.models_dir.expanduser().resolve()
    ready = args.ready_file.expanduser().absolute()
    source = (args.source or _resolve_source(resources)).expanduser().resolve()
    release_path = resources / "release-manifest.json"
    release_version = "standalone"
    runtime_sha256 = None
    if release_path.is_file():
        try:
            release = json.loads(release_path.read_text())
            music_release = release["components"]["music"]
            runtime_sha256 = music_release["runtime_sha256"]
            if (release["schema"] != "studiohub.standalone-release"
                    or release["schema_version"] != 1
                    or not isinstance(runtime_sha256, str)
                    or len(runtime_sha256) != 64
                    or any(ch not in "0123456789abcdef" for ch in runtime_sha256)):
                raise ValueError("release does not identify an exact Music runtime")
            release_version = str(release["version"])
        except (OSError, KeyError, TypeError, ValueError) as exc:
            parser.error(f"Invalid Music release identity: {exc}")

    manifest = _resolve_manifest(resources)

    if not models.is_dir():
        parser.error("Selected model library is unavailable; reconnect its volume")
    if any(path == bundle or path.is_relative_to(bundle) for path in (data, models)):
        parser.error("Music state and selected models must be outside the app bundle")
    if ready.is_symlink() or not ready.resolve().is_relative_to(data):
        parser.error("Ready file must stay inside external app data")
    if not (source / "backend" / "main.py").is_file():
        parser.error(f"Incomplete Music source: {source / 'backend' / 'main.py'} is missing")
    if manifest is None:
        parser.error(f"Music model manifest {MANIFEST_NAME} was not found")

    if args.parent_pid is not None:
        if args.parent_pid <= 1 or args.parent_pid != os.getppid():
            parser.error("Parent PID must identify the worker actual owner")
        if os.getpgrp() != os.getpid():
            os.setsid()
        pgid = os.getpgrp()
        if pgid != os.getpid():
            parser.error("Private Music process group must be isolated")
    else:
        pgid = None

    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    music_data = data / "music"
    if music_data.is_symlink():
        parser.error("Music data cannot be a symlink")
    music_data.mkdir(exist_ok=True, mode=0o700)
    if music_data.stat().st_uid != os.geteuid() or music_data.stat().st_mode & 0o077:
        parser.error("Music state must be owner-only")

    try:
        token = read_private_token(args.hub_token_file, data)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    # The app owns these settings; inherited credentials/caches from another
    # Studio install or the developer's shell must not leak into the engine.
    for key in list(os.environ):
        if key.startswith(("PYTHON", "HF_", "HUGGINGFACE_", "STUDIOHUB_",
                           "KH_STUDIO_", "PINOKIO_", "DYLD_")):
            os.environ.pop(key, None)
    outputs = args.output_dir.expanduser().resolve() if args.output_dir else None
    os.environ.update({
        "STUDIOHUB_MUSIC_DATA_DIR": str(music_data),
        "STUDIOHUB_MUSIC_OUTPUT_DIR": str(outputs) if outputs else str(music_data / "outputs"),
        "STUDIOHUB_MUSIC_MANIFEST": str(manifest),
        "STUDIOHUB_MUSIC_LIBRARY_DIR": str(models),
        "STUDIOHUB_FLEET_TOKEN": token,
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HOME": str(music_data / "cache"),
        "XDG_CACHE_HOME": str(music_data / "cache"),
        "MPLCONFIGDIR": str(music_data / "cache" / "matplotlib"),
    })
    if runtime_sha256 is not None:
        os.environ["STUDIOHUB_MUSIC_RUNTIME_SHA256"] = runtime_sha256
    os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))

    if args.check:
        from backend.cache import MusicCache
        try:
            package_status = MusicCache(library=models, manifest_path=manifest).status()
        except (OSError, ValueError, KeyError) as exc:
            package_status = {"state": "invalid", "reason": str(exc)}
        print(json.dumps({
            "component": "music", "version": release_version,
            "source": str(source), "data": str(music_data),
            "models": str(models),
            "manifest": str(manifest),
            "manifest_package_id": PACKAGE_ID,
            "python": sys.executable, "runtime_sha256": runtime_sha256,
            "package": package_status,
            "offline": bool(args.offline), "managed_updates": False,
        }, indent=2))
        return

    os.chdir(music_data)
    run_server("backend.main:from_environment", args.port, ready, release_version,
               args.parent_pid, pgid, factory=True)


if __name__ == "__main__":
    main()
