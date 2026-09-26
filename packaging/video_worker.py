"""Private bundle-relative Video entry point; run with immutable Python -I -B.

The Video worker is the one studio whose interpreter is not the shared base runtime: the LTX
pack runs on `ltx_pipelines_mlx`, which needs Python 3.11 while the base runtime is 3.12. The
staged tree is therefore its own `runtimes/video`, pinned by content digest in
`packaging/stage_voice.py` exactly the way the Image runtime is.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

RESOURCES = Path(__file__).resolve().parent
sys.path.insert(0, str(RESOURCES))
from voice_worker import read_private_token, run_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--hub-token-file', type=Path, required=True)
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    resources = RESOURCES
    bundle = resources.parent.parent
    data = args.data_dir.expanduser().resolve()
    models = args.models_dir.expanduser().resolve()
    ready = args.ready_file.expanduser().absolute()
    release_path = resources / 'release-manifest.json'
    source = resources / 'components' / 'video' / 'app'
    runtime = resources / 'runtimes' / 'video'
    manifest_path = resources / 'model-manifests' / 'ltx-2-3-22b-ram-12gb-mlx.json'
    if not models.is_dir():
        parser.error('Selected model library is unavailable; reconnect its volume')
    if any(path == bundle or path.is_relative_to(bundle) for path in (data, models)):
        parser.error('Video state and selected models must be outside the app bundle')
    if ready.is_symlink() or not ready.resolve().is_relative_to(data):
        parser.error('Ready file must stay inside external app data')
    if not (release_path.is_file() and (source / 'backend/main.py').is_file()
            and manifest_path.is_file() and (runtime / 'bin/python3').is_file()):
        parser.error('Incomplete Video bundle: release, source, model catalog or runtime is missing')
    try:
        release = json.loads(release_path.read_text())
        video = release['components']['video']
        identity = video['runtime_sha256']
        if (release['schema'] != 'studiohub.standalone-release'
                or release['schema_version'] != 1 or not isinstance(identity, str)
                or len(identity) != 64 or any(ch not in '0123456789abcdef' for ch in identity)):
            raise ValueError('Release manifest does not identify an exact Video runtime')
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(f'Invalid Video release identity: {exc}')
    if args.parent_pid is not None:
        if args.parent_pid <= 1 or args.parent_pid != os.getppid():
            parser.error('Parent PID must identify the worker actual owner')
        if os.getpgrp() != os.getpid():
            os.setsid()
        pgid = os.getpgrp()
        if pgid != os.getpid():
            parser.error('Private Video process group must be isolated')
    else:
        pgid = None
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    video_data = data / 'video'
    if video_data.is_symlink():
        parser.error('Video data cannot be a symlink')
    video_data.mkdir(exist_ok=True, mode=0o700)
    if video_data.stat().st_uid != os.geteuid() or video_data.stat().st_mode & 0o077:
        parser.error('Video state must be owner-only')
    try:
        token = read_private_token(args.hub_token_file, data)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for key in list(os.environ):
        if key.startswith(('PYTHON', 'HF_', 'HUGGINGFACE_', 'STUDIOHUB_',
                           'KH_STUDIO_', 'PINOKIO_', 'DYLD_')):
            os.environ.pop(key, None)
    os.environ.update({
        'STUDIOHUB_VIDEO_DATA_DIR': str(video_data),
        'STUDIOHUB_MODELS_DIR': str(models),
        'STUDIOHUB_VIDEO_MANIFEST': str(manifest_path),
        'STUDIOHUB_RELEASE_MANIFEST': str(release_path),
        'STUDIOHUB_FLEET_TOKEN': token,
        'HF_HUB_DISABLE_TELEMETRY': '1', 'PYTHONNOUSERSITE': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        # The engine resolves the text encoder by path, but a pack that falls back to a hub id
        # must find the companion in this cache rather than the operator's home.
        'HF_HOME': str(video_data / 'cache'),
        'XDG_CACHE_HOME': str(video_data / 'cache'),
        # **The voice runtime's `bin` is on PATH on purpose: that is where this app ships ffmpeg.**
        # The LTX pack muxes the decoded video and audio into the MP4 with an `ffmpeg` subprocess
        # (`ltx_pipelines_mlx.utils.media_io`, `find_ffmpeg()` → `shutil.which`), so a worker whose
        # PATH held only its own runtime and `/usr/bin` generated the whole clip and then failed at
        # the last step with *"ffmpeg not found on PATH. Install it with: brew install ffmpeg"* —
        # measured 2026-09-26, on the first through-the-chain run. The bundle already carries
        # `ffmpeg` and `ffprobe` (21.7 MB each, in the Voice runtime, which every Hub build has);
        # duplicating them into the video runtime would add 43 MB and a second licence copy for no
        # reason. A Hub bundle without a Voice runtime is not a bundle (`hub_worker.main` refuses
        # one), so this path always exists in a staged app.
        'PATH': ':'.join([
            str(runtime / 'bin'),
            str(RESOURCES / 'runtimes' / 'voice' / 'bin'),
            '/usr/bin', '/bin', '/usr/sbin', '/sbin',
        ]),
    })
    # Only when the owner asked for it — see the Image worker's note; forcing this on made
    # switching Offline mode off change nothing.
    if args.offline:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.dont_write_bytecode = True
    if args.check:
        print(json.dumps({'component': 'video', 'version': release['version'],
                          'source': str(source), 'runtime_sha256': identity,
                          'data': str(video_data), 'models': str(models),
                          'offline': bool(args.offline), 'managed_updates': False}))
        return
    os.chdir(video_data)
    run_server('components.video.app.backend.main:from_environment', args.port,
               ready, str(release['version']), args.parent_pid, pgid, factory=True)


if __name__ == '__main__':
    main()
