"""Placement policy: the smallest capable machine first, for every lane.

The rule is the **model's**, not the modality's, and the owner restated it on 2026-09-25 after
finding the old one spending the machines only big jobs can use:

    "the qwen3 tts 0.6 can run on 8gb machine so it must take up the small ones first. but qwen3
    tts 1.7b needs 16+ so it will wait for freed up 16+ machines... this should be generator
    specific at all. instead this priority case should be model - ram requirement specific."

The old rule sorted every audio job onto the high-memory machines because its *artifact* was audio,
so the 8 GB-floor Qwen3-TTS 0.6B took a 32 GB Mac while the 12-32 GB voice models queued behind it.
Capability is admission's job - a machine below a model's published floor is refused before
placement runs - so ordering is only ever the tie-break among machines that can all do the work,
and the smallest of those is the right one on a fleet where the 16 GB+ Macs are the scarce ones.
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
        # Every lane, so "one rule" is checked against all of them rather than the three the old
        # policy happened to be written for.
        _studio("music-small", "music", "small"),
        _studio("music-big", "music", "big"),
        _studio("chat-small", "chat", "small"),
        _studio("chat-big", "chat", "big"),
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


def test_audio_prefers_the_smallest_machine_not_the_biggest(placement):
    """The owner's case: an 8 GB-capable voice model must not take a 32 GB Mac."""
    order = [studio["id"] for studio in broker._eligible_studios("voice", "pool")]
    assert order[0] == "voice-small"
    # Preference, never a filter: the big Mac stays eligible behind it.
    assert "voice-big" in order


def test_every_lane_is_ordered_by_the_same_rule(placement):
    """One rule, and it is the model's - a lane that sorted differently is the defect the modality
    branch was. None of these lanes names a model, so every machine is capable and the smallest
    comes first whichever lane is asking."""
    for modality in ("voice", "music", "image", "transcription", "chat"):
        order = [studio["id"] for studio in broker._eligible_studios(modality, "pool")]
        assert order and order[0].endswith("-small"), (
            f"{modality} did not prefer the smallest machine: {order}")


def test_a_machine_below_the_models_floor_is_not_preferred(placement, monkeypatch):
    """A 16 GB-floor model must not be put ahead of a Mac that can actually run it.

    This is the half that makes the rule "model - ram requirement specific" rather than merely
    "smallest": the 0.6B voice model goes to the 8 GB Mac, and a 16 GB model does not follow it
    there - the floor sorts the incapable machine last.
    """
    monkeypatch.setattr(broker, "_published_memory_tier_gb",
                        lambda model, modality: 16.0 if model == "big/floor" else 8.0)
    small_first = [s["id"] for s in broker._eligible_studios("voice", "pool", "small/floor")]
    assert small_first[0] == "voice-small"
    big_first = [s["id"] for s in broker._eligible_studios("voice", "pool", "big/floor")]
    assert big_first[0] == "voice-big", (
        f"a 16 GB-floor model was offered the 8 GB Mac first: {big_first}")


def test_image_prefers_the_small_machine(placement):
    order = [studio["id"] for studio in broker._eligible_studios("image", "pool")]
    assert order[0] == "image-small"
    assert "image-big" in order


def test_subtitle_prefers_the_small_machine(placement):
    order = [studio["id"] for studio in broker._eligible_studios("transcription", "pool")]
    assert order[0] == "subtitle-small"
    assert "subtitle-big" in order


def test_unknown_memory_sorts_last(monkeypatch):
    """An unknown is not evidence of a small Mac, so it must not be preferred as one."""
    studios = [{"id": "unknown"}, {"id": "small"}]
    monkeypatch.setattr(broker, "_known_memory_gb",
                        lambda studio: None if studio["id"] == "unknown" else 8.0)
    assert sorted(studios, key=broker._placement_key("voice", None))[-1]["id"] == "unknown"
