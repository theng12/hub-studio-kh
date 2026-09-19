"""A shared voice is pushed once to a controller and installed on every node it owns.

The owner asked for this in his own words: adding a voice in the gateway should share it across a
fleet of fifty to a hundred Macs, default on, instead of hand-copying clips from machine to
machine. The gateway talks only to controllers, so the fan-out is the Hub's job - and the module
that does it was sitting complete with nothing calling it.

Two things these tests pin, both of which decide whether a push is useful or a trap:

* **The id.** A node stores the id it is given, so the id has to be one a node accepts (12 lowercase
  hex) and re-pushing the same clip has to refresh that voice rather than add a second copy. The
  caller may name a clip by its own content hash, which gives one id across all six controllers.
* **The timings.** A transcript without its aligned segments installs on every node and then fails
  to clone on the models that need the alignment - a push that looks successful and is not. So the
  segments are required with a transcript, they travel in the fan-out, and the node's own count is
  checked before the target is called synchronized.
"""
import asyncio
import hashlib
import importlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


AUDIO = b"RIFF____WAVEfmt " + b"x" * 400
TRANSCRIPT = "A short line that was transcribed."
SEGMENTS = [
    {"start_seconds": 0.0, "end_seconds": 1.4, "text": "A short line"},
    {"start_seconds": 1.4, "end_seconds": 3.1, "text": "that was transcribed."},
]


class SharedVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.module = importlib.import_module("components.hub.backend.shared_voices")
        self._old_root = self.module.ROOT
        self.module.ROOT = self.root / "shared_voices"

    def tearDown(self):
        self.module.ROOT = self._old_root
        self.temporary.cleanup()

    def push(self, *, audio=AUDIO, transcript=TRANSCRIPT, segments=SEGMENTS, voice_id=None,
             name="Todd", **overrides):
        # `transcript`/`transcript_segments` may arrive either as the named parameters or in
        # **overrides, and the tests need to say "not mentioned" (None) as distinct from "empty".
        for key in ("transcript", "transcript_segments"):
            if key in overrides:
                if key == "transcript":
                    transcript = overrides.pop(key)
                else:
                    segments = overrides.pop(key)
        values = {"name": name, "language": "en", "gender": "m", "license": "self-owned",
                  "permission_acknowledged": True, "filename": "reference.wav"}
        values.update(overrides)
        return self.module.upsert(audio_bytes=audio, transcript=transcript,
                                  transcript_segments=segments, voice_id=voice_id, **values)

    # ── the timings ────────────────────────────────────────────────────────

    def test_a_transcript_without_its_timings_is_refused(self):
        """The shape that must never be stored: it installs everywhere and clones nowhere."""
        with self.assertRaises(ValueError) as error:
            self.push(segments=[])
        self.assertIn("transcript_segments", str(error.exception))
        self.assertEqual(self.module.list_voices(), [], "nothing may be stored")

    def test_a_voice_with_no_transcript_needs_no_timings(self):
        # A clip nobody transcribed is cloned by models that pick their own window; the engine
        # clears the transcript for those, so there is nothing to align.
        voice, created = self.push(transcript="", segments=[])
        self.assertTrue(created)
        self.assertFalse(voice["has_transcript"])
        self.assertEqual(voice["segment_count"], 0)

    def test_the_timings_are_stored_and_read_back(self):
        voice, _ = self.push()
        self.assertEqual(voice["segment_count"], len(SEGMENTS))
        stored = self.module.get(voice["id"])
        self.assertEqual(stored["transcript_segments"], SEGMENTS)
        self.assertEqual(stored["transcript"], TRANSCRIPT)

    def test_a_listing_reports_the_timings_without_carrying_them(self):
        """A hundred voices' worth of segments must not be in every status read."""
        self.push()
        listed = self.module.list_voices()[0]
        self.assertTrue(listed["has_segments"])
        self.assertEqual(listed["segment_count"], len(SEGMENTS))
        self.assertNotIn("transcript_segments", listed)

    def test_a_refresh_can_correct_a_transcript_and_its_timings(self):
        voice, _ = self.push()
        corrected = [{"start_seconds": 0.0, "end_seconds": 2.0, "text": "A corrected line."}]
        again, _ = self.push(transcript="A corrected line.", segments=corrected,
                             name=voice["name"])
        self.assertEqual(again["transcript"], "A corrected line.")
        self.assertEqual(self.module.get(voice["id"])["transcript_segments"], corrected)

    def test_a_voice_pushed_again_after_removal_comes_back(self):
        """A removal must not be permanent. The gateway names a clip by its content hash, so the
        voice it re-pushes has exactly the id the tombstone remembers - and `start_sync` refuses any
        voice with a tombstone, so the push answered 201 and installed nowhere, with no way back
        except the manual sync route. A push is the operator asking for it back."""
        # The gateway names a clip by its own content hash, so the id survives the removal.
        digest = hashlib.sha256(AUDIO).hexdigest()[:12]
        voice, _ = self.push(voice_id=digest)
        self.module.prepare_delete(voice["id"])
        self.assertIsNotNone(self.module.get_deletion(voice["id"]))

        again, created = self.push(voice_id=digest)
        self.assertEqual(again["id"], voice["id"], "the same bytes are the same voice")
        self.assertIsNone(self.module.get_deletion(voice["id"]),
                          "the tombstone must be gone, or every node is told to delete it again")
        self.assertIsNotNone(self.module.get(voice["id"]))

    def test_a_metadata_only_push_keeps_the_transcript_and_timings(self):
        """Omitted is 'keep', not 'clear'. The gateway re-pushes on every edit, so a rename used to
        wipe the words and the timings on every controller and then on every node."""
        voice, _ = self.push()
        again, _ = self.push(name="Todd Renamed", transcript=None, transcript_segments=None)
        stored = self.module.get(voice["id"])
        self.assertEqual(again["name"], "Todd Renamed")
        self.assertEqual(stored["transcript"], TRANSCRIPT, "the words were kept")
        self.assertEqual(stored["transcript_segments"], SEGMENTS, "the timings were kept")

    def test_an_explicit_empty_transcript_still_clears_it(self):
        voice, _ = self.push()
        self.push(transcript="", transcript_segments=None)
        stored = self.module.get(voice["id"])
        self.assertEqual(stored["transcript"], "")
        self.assertEqual(stored["transcript_segments"], [])

    def test_malformed_timings_are_refused(self):
        for bad in ([{"start_seconds": 2, "end_seconds": 1, "text": "backwards"}],
                    [{"start_seconds": 0, "end_seconds": 1, "text": "  "}],
                    [{"start_seconds": "x", "end_seconds": 1, "text": "y"}],
                    [{"start_seconds": -1, "end_seconds": 1, "text": "y"}],
                    "not-a-list"):
            with self.assertRaises(ValueError):
                self.push(segments=bad)

    # ── the id ─────────────────────────────────────────────────────────────

    def test_the_hub_mints_an_id_a_node_accepts(self):
        voice, created = self.push()
        self.assertTrue(created)
        self.assertRegex(voice["id"], r"^[0-9a-f]{12}$")

    def test_a_caller_may_name_the_clip_by_its_own_hash(self):
        """One id across six controllers, which is what the gateway derives today."""
        digest = hashlib.sha256(AUDIO).hexdigest()[:12]
        voice, created = self.push(voice_id=digest)
        self.assertTrue(created)
        self.assertEqual(voice["id"], digest)

    def test_pushing_the_same_clip_again_refreshes_it_instead_of_duplicating(self):
        first, created = self.push(name="Todd")
        again, created_again = self.push(name="Todd Renamed")
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(again["id"], first["id"], "the same bytes are the same voice")
        self.assertEqual(again["name"], "Todd Renamed")
        self.assertEqual(len(self.module.list_voices()), 1)

    def test_a_refresh_may_arrive_under_a_different_id(self):
        """Identity is the bytes: a caller that changes its naming must not fork the voice."""
        first, _ = self.push()
        second, created = self.push(voice_id="0fb9c76a924f")
        self.assertFalse(created)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.module.list_voices()), 1)

    def test_an_id_that_already_holds_other_audio_is_a_conflict(self):
        digest = hashlib.sha256(AUDIO).hexdigest()[:12]
        self.push(voice_id=digest)
        with self.assertRaises(self.module.SharedVoiceConflict):
            self.push(audio=b"RIFF____WAVEfmt " + b"y" * 400, voice_id=digest)

    def test_an_id_that_is_not_twelve_hex_is_refused(self):
        for bad in ("gateway-push-probe", "0FB9C76A924F", "0fb9c76a924", "0fb9c76a924ff", "zzzzzzzzzzzz"):
            with self.assertRaises(ValueError):
                self.push(voice_id=bad)

    # ── the fan-out ────────────────────────────────────────────────────────

    class _Response:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, response):
            self.response = response
            self.calls = []

        async def put(self, url, headers=None, data=None, files=None, timeout=None):
            self.calls.append({"url": url, "headers": headers, "data": data, "files": files})
            return self.response

    class _Monitor:
        def __init__(self, client, registry):
            self._client = client
            self.registry = registry
            self.status = {row["id"]: {"status": "up"} for row in registry}

    def _fan_out(self, *, reported_segments="match"):
        """Run one node push against a node that answers with the given stored count."""
        self.push()
        voice = self.module.list_voices()[0]
        stored = self.module.get(voice["id"])
        studio = {"id": "voice@node-a", "modality": "voice", "machine": "node-a",
                  "host": "127.0.0.1", "port": 9, "local": False}
        sync = {"status": "created", "sha256": hashlib.sha256(AUDIO).hexdigest()}
        if reported_segments == "match":
            sync["segments"] = len(SEGMENTS)
        elif reported_segments is not None:
            sync["segments"] = reported_segments
        client = self._Client(self._Response({"voice": {"id": voice["id"]}, "sync": sync}))
        monitor = self._Monitor(client, [studio])
        result = asyncio.run(self.module._sync_target(monitor, stored, studio))
        return voice, client.calls[0], result

    def test_the_fan_out_carries_the_timings_to_the_node(self):
        voice, call, result = self._fan_out()
        sent = json.loads(call["data"]["transcript_segments_json"])
        self.assertEqual(sent, SEGMENTS)
        self.assertEqual(call["data"]["transcript"], TRANSCRIPT)
        self.assertEqual(call["data"]["audio_sha256"], hashlib.sha256(AUDIO).hexdigest())
        self.assertIn(f"/api/voices/{voice['id']}/fleet-sync", call["url"])
        self.assertEqual(result["status"], "synced")

    def test_a_node_that_drops_the_timings_is_a_failed_sync_not_a_silent_one(self):
        """200 with the clip and no timings is the exact shape that only fails later, per node."""
        _, _, result = self._fan_out(reported_segments=0)
        self.assertEqual(result["status"], "failed")
        self.assertIn("timings", result["message"])

    def test_a_node_that_does_not_confirm_the_count_is_not_called_synchronized(self):
        # An older worker that ignores the field cannot be trusted with the timings it never
        # mentions, so the target stays a failure rather than reading as done.
        _, _, result = self._fan_out(reported_segments=None)
        self.assertEqual(result["status"], "failed")

    def test_a_target_that_is_off_is_pending_rather_than_failed(self):
        self.push()
        voice = self.module.list_voices()[0]
        stored = self.module.get(voice["id"])
        studio = {"id": "voice@node-b", "modality": "voice", "machine": "node-b",
                  "host": "127.0.0.1", "port": 9, "local": False}
        client = self._Client(self._Response({}))
        monitor = self._Monitor(client, [studio])
        monitor.status[studio["id"]] = {"status": "down"}
        result = asyncio.run(self.module._sync_target(monitor, stored, studio))
        self.assertEqual(result["status"], "pending")
        self.assertEqual(client.calls, [], "an off node must not be pushed to")

    def test_one_target_per_machine(self):
        """A node with several voice studios must not be pushed to several times."""
        monitor = self._Monitor(self._Client(self._Response({})), [
            {"id": "voice@node-a", "modality": "voice", "machine": "node-a"},
            {"id": "voice@node-a-2", "modality": "voice", "machine": "node-a"},
            {"id": "voice", "modality": "voice", "machine": "local"},
            {"id": "image@node-a", "modality": "image", "machine": "node-a"},
        ])
        self.assertEqual([row["machine"] for row in self.module.targets(monitor)],
                         ["node-a", "local"])


if __name__ == "__main__":
    unittest.main()
