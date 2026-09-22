"""Placement policy: audio wants the high-memory Macs, image and subtitle the small ones.

The owner's fleet is many 8 GB Macs and a few 16 GB+ ones. The small Macs render images but
cannot hold the audio models, so the largest capable machine takes audio and video while
image and subtitle stay on the small ones.
"""
from __future__ import annotations

import pytest

from components.hub.backend import broker


class _Monitor:
    def __init__(self, studios):
        self.registry = studios
        self.status = {studio["id"]: {"status": "up"} for studio in studios}


def _studio(studio_id: str, modality: str, machine: str) -> dict:
    return {"id": studio_id, "modality": modality, "machine": machine,
            "host": "127.0.0.1", "port": 1, "local": False,
            "runtime_identity": {"id": "runtime", "compatible": True}}


@pytest.fixture
def placement(monkeypatch):
    studios = [
        _studio("image-small", "image", "small"),
        _studio("image-big", "image", "big"),
        _studio("voice-small", "voice", "small"),
        _studio("voice-big", "voice", "big"),
        _studio("subtitle-small", "transcription", "small"),
        _studio("subtitle-big", "transcription", "big"),
    ]
    memory = {"small": 8.0, "big": 32.0}
    monkeypatch.setattr(broker, "_monitor", lambda: _Monitor(studios))
    monkeypatch.setattr(broker, "busy_machines", lambda: set())
    monkeypatch.setattr(broker.peers, "dispatch_ready", lambda studio: True)
    monkeypatch.setattr(broker, "machine_enabled", lambda machine: True)
    monkeypatch.setattr(broker, "studio_enabled", lambda machine, studio_id: True)
    monkeypatch.setattr(broker, "_machine_blocked", lambda machine: False)
    monkeypatch.setattr(broker.routing_rule, "allows", lambda studio, routing: True)
    monkeypatch.setattr(broker, "_known_memory_gb",
                        lambda studio: memory.get(studio["machine"]))
    return studios


def test_audio_prefers_the_high_memory_machine(placement):
    order = [studio["id"] for studio in broker._eligible_studios("voice", "pool")]
    assert order[0] == "voice-big"
    # Preference, never a filter: the small Mac stays eligible behind it.
    assert "voice-small" in order


def test_image_prefers_the_small_machine(placement):
    order = [studio["id"] for studio in broker._eligible_studios("image", "pool")]
    assert order[0] == "image-small"
    assert "image-big" in order


def test_subtitle_prefers_the_small_machine(placement):
    order = [studio["id"] for studio in broker._eligible_studios("transcription", "pool")]
    assert order[0] == "subtitle-small"
    assert "subtitle-big" in order


def test_unknown_memory_sorts_last_either_way(monkeypatch):
    studios = [{"id": "unknown"}, {"id": "small"}]
    monkeypatch.setattr(broker, "_known_memory_gb",
                        lambda studio: None if studio["id"] == "unknown" else 8.0)
    assert sorted(studios, key=broker._audio_placement_key)[-1]["id"] == "unknown"
    assert sorted(studios, key=broker._flexible_placement_key)[-1]["id"] == "unknown"
