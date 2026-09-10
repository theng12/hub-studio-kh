from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend import generation, media_tools, reference_audio, transcription


ROOT = Path(__file__).parents[2]


def test_generation_resolves_ffmpeg_from_the_packaged_tools_root(
    tmp_path: Path, monkeypatch,
) -> None:
    """A clean packaged environment must resolve tools from its explicit root."""
    root = tmp_path / "runtime"
    executable = root / "bin" / "ffmpeg"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("STUDIOHUB_TOOLS_DIR", str(root / "bin"))

    assert generation._find_ffmpeg_executable() == executable


def test_all_media_workers_share_the_managed_resolver() -> None:
    """Changing resolver policy once must cover TTS, reference decode, and STT."""
    assert all(
        getattr(worker, "media_tools", None) is media_tools
        for worker in (generation, reference_audio, transcription)
    )


def test_media_capability_exposes_only_availability_and_version(
    tmp_path: Path, monkeypatch,
) -> None:
    """Capability output must be safe to return from a health endpoint."""
    root = tmp_path / "runtime" / "bin"
    for name in ("ffmpeg", "ffprobe"):
        executable = root / name
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("STUDIOHUB_TOOLS_DIR", str(root))
    monkeypatch.setattr(
        media_tools,
        "subprocess",
        SimpleNamespace(
            run=lambda command, **_kwargs: SimpleNamespace(
                stdout=f"{Path(command[0]).name} version 7.1.1 Copyright\n"
            )
        ),
        raising=False,
    )

    status = getattr(media_tools, "availability", lambda: {})()

    assert status == {
        "ffmpeg": {"available": True, "version": "7.1.1"},
        "ffprobe": {"available": True, "version": "7.1.1"},
    }
    assert str(root) not in str(status)


def test_generation_and_transcription_capabilities_publish_safe_media_facts(
    monkeypatch,
) -> None:
    facts = {
        "ffmpeg": {"available": True, "version": "7.1.1"},
        "ffprobe": {"available": True, "version": "7.1.1"},
    }
    monkeypatch.setattr(media_tools, "availability", lambda: facts)

    generation_status = generation.availability()
    transcription_status = transcription.availability()

    assert generation_status.get("media") == facts
    assert transcription_status.get("media") == facts
    assert str(ROOT) not in str(generation_status.get("media"))
    assert str(ROOT) not in str(transcription_status.get("media"))


# Pinokio launcher delegation checks were removed with the launcher scripts;
# the standalone package ships its runtime and uses the external tools root.
