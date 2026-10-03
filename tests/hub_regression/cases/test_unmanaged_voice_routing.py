"""A voice the Hub does not manage stays on the Mac whose library holds it.

`_shared_voice_allows_studio` answered "allowed on any studio" for a `voice_library_id` that is not
a Hub shared voice. Such an id is an entry in this Mac's own Voice Studio library - the app lists
voices from it - and no other Mac holds it, so a pool dispatch could send the job to a joined node
that then failed it for a missing reference.
"""

from __future__ import annotations

from components.hub.backend import broker


LOCAL_VOICE = {"id": "voice", "modality": "voice", "machine": "local", "local": True}
PEER_VOICE = {"id": "voice@node-a", "modality": "voice", "machine": "node-a", "local": False}


def _batch(voice_id: str) -> dict:
    return {"modality": "voice", "routing": "pool",
            "shared_params": {"voice_library_id": voice_id}}


def test_unmanaged_voice_is_refused_on_a_node_that_does_not_hold_it(fixture_monitor, monkeypatch):
    monkeypatch.setattr(broker.shared_voices, "synced_studio_ids", lambda voice_id: None)
    batch = _batch("0123456789ab")

    assert not broker._shared_voice_allows_studio(batch, {}, PEER_VOICE)
    assert broker._shared_voice_allows_studio(batch, {}, LOCAL_VOICE)


def test_hub_voice_still_goes_only_where_it_is_synchronized(fixture_monitor, monkeypatch):
    monkeypatch.setattr(broker.shared_voices, "synced_studio_ids",
                        lambda voice_id: {"voice@node-a"})
    batch = _batch("0123456789ab")

    assert broker._shared_voice_allows_studio(batch, {}, PEER_VOICE)
    assert not broker._shared_voice_allows_studio(batch, {}, LOCAL_VOICE)


def test_a_voice_job_without_a_library_voice_is_unrestricted(fixture_monitor):
    batch = {"modality": "voice", "routing": "pool", "shared_params": {}}

    assert broker._shared_voice_allows_studio(batch, {}, PEER_VOICE)
