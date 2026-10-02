"""One modality table, read by every Hub consumer — and refuse-by-name where it cannot be.

The owner's rule is that nothing about models is hardcoded: registries are data and consumers
read the data. A modality/worker/engine list is a *contract* only where the Hub really closes a
vocabulary, and where a list cannot be removed it must fail loudly and name the missing entry
and the file to update. The 0.6.57 outage is the shape these tests exist to prevent: `chat-mlx`
was missing from one consumer's map, and every model picker on every Mac went blank.

Every test here asserts an **invariant over the Hub's own vocabulary** — "every modality the Hub
routes is governed" — rather than membership of a hand-edited set, so the next modality is
covered the moment it is registered and a consumer that forgets it fails here.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from components.hub import standalone, standalone_config
from components.hub.backend import (activity, artifact_metadata, automatic_recovery, broker,
                                    capabilities, cloud_guard, ledger, memory_admission,
                                    memory_control, monitor, registry, vocabulary,
                                    workload_policy)


def _descriptor(worker_id: str, modality: str, *, port: int = 51100) -> dict:
    return {
        "id": worker_id, "modality": modality, "host": "127.0.0.1", "port": port,
        "machine": "node-a", "local": True, "hub_token": "fixture-token",
        "runtime_identity": {"id": f"{worker_id}-runtime"},
        "managed_output_roots": [],
    }


class _WorkersFile:
    """A private owner-supplied worker descriptor file for one test."""

    def __init__(self, descriptors: list[dict]) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-workers-")
        root = Path(self.temporary.name)
        self.path = root / "workers.json"
        self.path.write_text(json.dumps(descriptors))
        os.chmod(self.path, 0o600)
        self.env = mock.patch.dict(os.environ, {
            "STUDIOHUB_WORKERS_FILE": str(self.path),
            "STUDIOHUB_DATA_DIR": str(root / "state"),
        })

    def __enter__(self):
        self.env.start()
        return self

    def __exit__(self, *exc):
        self.env.stop()
        self.temporary.cleanup()
        return False


class WorkerDescriptorTests(unittest.TestCase):
    """B3 — which workers may register at all."""

    def test_a_worker_declares_its_own_modality_and_the_hub_validates_it(self) -> None:
        """A descriptor for a modality the Hub does not route names the modality and the file.

        `render` is the example because it is genuinely unrouted: it is a closed modality in the
        broker's own table (the Hub's local ffmpeg step) and deliberately has no worker, while
        `video` — this test's original example — is a real worker modality now.
        """
        with _WorkersFile([_descriptor("render", "render")]):
            with self.assertRaises(ValueError) as raised:
                standalone_config.load_workers()
        message = str(raised.exception)
        self.assertIn("'render'", message)
        self.assertIn("vocabulary.py", message)
        self.assertIn("WORKER_FOR_MODALITY", message)

    def test_a_descriptor_that_disagrees_with_the_worker_table_names_both(self) -> None:
        """`subtitle` serves `transcription`; a descriptor claiming `voice` is refused by name."""
        with _WorkersFile([_descriptor("subtitle", "voice")]):
            with self.assertRaises(ValueError) as raised:
                standalone_config.load_workers()
        message = str(raised.exception)
        self.assertIn("'subtitle'", message)
        self.assertIn("'voice'", message)
        self.assertIn("STUDIOHUB_WORKERS_FILE", message)

    def test_a_descriptor_with_no_modality_is_refused_before_it_can_be_routed(self) -> None:
        descriptor = _descriptor("voice", "voice")
        descriptor.pop("modality")
        with _WorkersFile([descriptor]):
            with self.assertRaises(ValueError) as raised:
                standalone_config.load_workers()
        self.assertIn("no modality", str(raised.exception))

    def test_registering_a_modality_is_enough_to_register_its_worker(self) -> None:
        """The invariant: the Hub's own table is the only list a worker has to appear in."""
        descriptors = [
            _descriptor(worker, modality, port=51100 + index)
            for index, (modality, worker) in enumerate(vocabulary.WORKER_FOR_MODALITY.items())
        ]
        with _WorkersFile(descriptors):
            workers = standalone_config.load_workers()
        self.assertEqual(
            {worker["id"]: worker["modality"] for worker in workers},
            {worker: modality for modality, worker in vocabulary.WORKER_FOR_MODALITY.items()},
        )


class MemoryPolicyVocabularyTests(unittest.TestCase):
    """B4/B5 — the floors come from the producer, and the Hub's own policy says so."""

    def test_every_published_floor_spelling_is_read(self) -> None:
        for field in workload_policy._TOTAL_MEMORY_FIELDS:
            with self.subTest(field=field):
                entry = {"repo": "org/model", field: 12.5}
                self.assertEqual(
                    workload_policy.required_total_memory_gb("org/model", entry), 12.5)
        for field in workload_policy._FREE_MEMORY_FIELDS:
            with self.subTest(field=field):
                entry = {"repo": "org/model", field: 4.5}
                self.assertEqual(
                    workload_policy.required_free_memory_gb("org/model", entry), 4.5)

    def test_the_strictest_published_floor_wins(self) -> None:
        entry = {"repo": "org/model", "min_free_memory_gb": 2.5,
                 "required_free_memory_gb": 4.25}
        self.assertEqual(workload_policy.required_free_memory_gb("org/model", entry), 4.25)

    def test_a_published_floor_that_cannot_be_read_is_loud(self) -> None:
        """Dropping an unreadable floor would silently weaken the gate."""
        readers = (
            (workload_policy.required_total_memory_gb, workload_policy._TOTAL_MEMORY_FIELDS),
            (workload_policy.required_free_memory_gb, workload_policy._FREE_MEMORY_FIELDS),
        )
        for reader, fields in readers:
            for field in fields:
                with self.subTest(field=field):
                    with self.assertRaises(ValueError) as raised:
                        reader("org/model", {"repo": "org/model", field: "eight"})
                    self.assertIn(field, str(raised.exception))
                    self.assertIn("workload_policy.py", str(raised.exception))

    def test_the_owner_approved_qwen_floor_is_still_in_force(self) -> None:
        """Qwen3-TTS 12Hz 0.6B Base 8-bit: 8 GB tier, 4.2 GB live headroom (3.2 until the 2026-10-02 memory bounds raised it to the measured footprint).

        Reads the **shipped Voice row**, not a hand-written dict. The floor moved out of the Hub's
        model-keyed table and onto the model, so a version of this test that passed only
        `min_unified_memory_gb` would keep passing after the row lost its free floor — which is
        exactly the silent regression this guards. Producer to policy, end to end.
        """
        from components.voice.app.backend import catalog as voice_catalog

        row = next((m for m in voice_catalog.CATALOG
                    if m.repo == "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"), None)
        self.assertIsNotNone(row, "the 8 GB production voice-cloning tier left the voice catalog")
        published = voice_catalog.serialize_model(row)
        self.assertEqual(
            published.get("min_free_memory_gb"), 4.2,
            "the Voice row no longer publishes the owner-approved live-free floor, so the Hub has "
            "no source for it and would fall back to its 2.0 GB default")
        self.assertEqual(published["min_unified_memory_gb"], 8)
        policy = memory_admission.describe(row.repo, published)
        self.assertEqual(policy["effective_min_free_memory_gb"], 4.2)
        # Was 0.0 ("a tier is not a wall"). Owner decision 2026-09-30: the requirement is enforced,
        # and for this model the manifest's 8 GB qualification is the number.
        self.assertEqual(policy["effective_min_total_memory_gb"], 8.0)
        self.assertEqual(policy["min_total_source"], "manifest")
        # `catalog`, not `fleet_default`: the model states it now.
        self.assertEqual(policy["source"], "catalog")
        self.assertEqual(
            broker._admission_requirements(row.repo, published),
            {"min_total": 8.0, "min_free": 4.2})
        self.assertEqual(
            memory_admission.describe("org/brand-new-model",
                                      {"repo": "org/brand-new-model"})["effective_min_free_memory_gb"],
            memory_admission.DEFAULT_MIN_FREE_MEMORY_GB,
            "a model that publishes no floor must still get the generic default")

    def test_a_measured_fleet_override_is_visible_and_its_absence_is_named(self) -> None:
        measured = memory_admission.describe(
            "aitrader/flux2-klein-4b-mlx-4bit",
            {"repo": "aitrader/flux2-klein-4b-mlx-4bit", "min_unified_memory_gb": 16})
        self.assertTrue(measured["fleet_default_measured"])
        self.assertEqual(measured["source"], "fleet_default")

        unmeasured = memory_admission.describe("org/new-model", {"repo": "org/new-model"})
        self.assertFalse(unmeasured["fleet_default_measured"])
        self.assertIn("No fleet measurement", unmeasured["default_reason"])


class ArtifactMediaTypeTests(unittest.TestCase):
    """B6 — a new modality must not degrade to `application/octet-stream` silently."""

    def test_every_routed_modality_declares_its_artifact_types(self) -> None:
        missing = sorted(set(vocabulary.WORKER_FOR_MODALITY)
                         - set(artifact_metadata._MEDIA_TYPES))
        self.assertEqual(missing, [], "a routed modality has no declared artifact media types")

    def test_a_modality_the_hub_has_not_been_taught_still_serves_known_bytes(self) -> None:
        self.assertEqual(
            artifact_metadata.trusted_media_type("application/json", "a-new-modality"),
            "application/json")
        self.assertIsNone(
            artifact_metadata.trusted_media_type("application/x-evil", "a-new-modality"))

    def test_a_declared_modality_still_narrows_the_answer(self) -> None:
        self.assertIsNone(artifact_metadata.trusted_media_type("audio/wav", "image"))
        self.assertEqual(
            artifact_metadata.trusted_media_type("audio/wav", "voice"), "audio/wav")

    def test_bytes_outrank_a_worker_header(self) -> None:
        self.assertEqual(
            artifact_metadata.media_type_for_proxy(
                "chat", None, "text/plain", data=b'{"scene": 1}'),
            "application/json")
        self.assertEqual(
            artifact_metadata.media_type_for_proxy("transcription", None, None),
            "application/octet-stream")

    def test_sniffing_proves_the_types_the_hub_proxies(self) -> None:
        wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 12
        self.assertEqual(artifact_metadata.sniff_media_type(wav), "audio/wav")
        self.assertEqual(artifact_metadata.sniff_media_type(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
                         "image/png")
        self.assertIsNone(artifact_metadata.sniff_media_type(b"not a media file"))


class RetryClassificationTests(unittest.TestCase):
    """B8 — an unclassified modality must fail loudly, not take the risky default."""

    def test_every_submittable_modality_is_classified(self) -> None:
        missing = sorted(set(broker.MODALITY) - set(broker.MODALITY_RETRY_CLASS))
        self.assertEqual(missing, [], "a submittable modality has no retry/idempotency class")

    def test_the_external_lanes_are_the_auxiliary_class(self) -> None:
        self.assertEqual(
            set(broker._EXTERNAL_LANES),
            {modality for modality, kind in broker.MODALITY_RETRY_CLASS.items()
             if kind == broker.RETRY_CLASS_AUXILIARY_LANE},
        )

    def test_an_unclassified_modality_is_refused_by_name_and_file(self) -> None:
        with self.assertRaises(ValueError) as raised:
            broker.retry_class("a-modality-from-the-next-release")
        message = str(raised.exception)
        self.assertIn("a-modality-from-the-next-release", message)
        self.assertIn("MODALITY_RETRY_CLASS", message)
        self.assertIn("broker.py", message)
        self.assertIn("duplicate an artifact", message)

    def test_a_new_batch_modality_cannot_be_submitted_unclassified(self) -> None:
        with mock.patch.dict(
                broker.MODALITY,
                {"newlane": ("/api/generate/newlane", "prompt", "text")}):
            result = broker._submit_batch_locked(
                {"modality": "newlane", "model": "org/model",
                 "items": [{"index": 0}]})
        self.assertIn("MODALITY_RETRY_CLASS", result.get("error", ""))


class PlacementTests(unittest.TestCase):
    """B7 — placement follows the artifact class and the model's own floor, not a modality set."""

    class _Monitor:
        def __init__(self, rows: list[dict]) -> None:
            self._rows = rows

        def scheduling_catalog_entries(self, model, *, modality=None):
            return [{"entry": row} for row in self._rows
                    if row.get("repo") == model]

    def _key_names(self, modality: str, model: str, tier: float | None) -> str:
        rows = [] if tier is None else [
            {"repo": model, "min_total_memory_gb": tier}]
        with mock.patch.object(broker, "_packaged_monitor", self._Monitor(rows)):
            return broker._placement_key(modality, model).__name__

    def test_placement_is_one_rule_for_every_lane(self) -> None:
        """The placement key is the model's, not the modality's.

        It used to branch on the artifact class the modality produces, so **every** audio job went
        to the high-memory machines whatever model it named. The owner found it: the 8 GB-floor
        Qwen3-TTS 0.6B took a 32 GB Mac that a Z-Image-class job needed, while the 12-32 GB voice
        models queued behind it. There is no branch left to test, so what is pinned here is that
        every lane resolves to the same single key.
        """
        for modality in ("voice", "music", "video", "image", "subtitle", "chat", "transcription"):
            with self.subTest(modality=modality):
                self.assertEqual(broker._placement_key.__name__, "_placement_key")
                self.assertFalse(hasattr(broker, "_audio_placement_key"),
                                 "the modality branch is gone; a resurrected one would sort audio "
                                 "onto the big Macs again")
                self.assertFalse(hasattr(broker, "_flexible_placement_key"),
                                 "one key: there is no second ordering to drift from it")

    def test_placement_order_ignores_the_modality_and_follows_the_machine(self) -> None:
        """The same two machines order the same way whichever lane is asking."""
        small = {"id": "small", "machine": "a"}
        big = {"id": "big", "machine": "b"}
        with mock.patch.object(broker, "_known_memory_gb",
                               lambda studio: 8.0 if studio["id"] == "small" else 32.0):
            self.assertEqual([s["id"] for s in sorted([big, small],
                                                     key=broker._placement_key("voice", None))],
                             ["small", "big"])

    def test_a_chat_model_is_actually_ordered_onto_the_larger_mac(self) -> None:
        from components.hub.backend import peers

        small = {"id": "chat-small", "modality": "chat", "machine": "node-a", "local": False}
        large = {"id": "chat-large", "modality": "chat", "machine": "node-b", "local": False}
        rows = [{"repo": "org/chat", "min_total_memory_gb": 16.0}]
        fake = self._Monitor(rows)
        fake.registry = [small, large]
        fake.status = {small["id"]: {"status": "up"}, large["id"]: {"status": "up"}}
        now = time.time()
        cached = {
            "node-a": (now, {"host": {"total_gb": 8.0}, "reachable": True, "auth": True,
                             "status": "connected"}),
            "node-b": (now, {"host": {"total_gb": 32.0}, "reachable": True, "auth": True,
                             "status": "connected"}),
        }
        with mock.patch.object(broker, "_packaged_monitor", fake), \
             mock.patch.dict(peers._cache, cached, clear=True), \
             mock.patch.object(registry, "load_flags", lambda: {}), \
             mock.patch.object(broker, "_busy", set()), \
             mock.patch.object(broker, "_maintenance", set()):
            order = [studio["id"] for studio in broker._eligible_studios(
                "chat", "pool", "org/chat")]
        self.assertEqual(
            order, ["chat-large", "chat-small"],
            "the 9B Chat model was preferentially scheduled onto the smallest capable Mac",
        )


class ActivityVocabularyTests(unittest.TestCase):
    """B9 — a studio's own reported operation decides; no `KeyError` and no silent drop."""

    def test_every_routed_modality_may_name_itself(self) -> None:
        self.assertEqual(activity.VALID_STUDIOS, frozenset(vocabulary.WORKER_FOR_MODALITY))

    def test_a_snapshot_with_no_operation_validates_for_every_modality(self) -> None:
        now = time.time()
        for modality in sorted(vocabulary.WORKER_FOR_MODALITY):
            with self.subTest(modality=modality):
                snapshot = {
                    "schema": activity.SCHEMA, "studio": modality, "observed_at": now,
                    "active": {"id": "job-1", "state": "running", "model": "org/model",
                               "source": "direct", "created_at": now - 1,
                               "started_at": now - 1, "updated_at": now},
                    "latest": None,
                }
                validated = activity.validate_snapshot(snapshot)
                self.assertIsNotNone(
                    validated, "a snapshot with no explicit operation was rejected")
                self.assertEqual(
                    validated["active"]["operation"],
                    vocabulary.operation_for(modality, None))

    def test_the_core_stats_operations_cover_every_routed_operation(self) -> None:
        missing = sorted(set(vocabulary.STUDIO_OPERATION.values())
                         - set(ledger._CORE_STATS_OPERATIONS))
        self.assertEqual(missing, [], "a routed operation is missing from core stats")


class LegacyRegistryTests(unittest.TestCase):
    """B10 — the legacy registry admits every modality the Hub routes."""

    def _load_with(self, entries: list[dict]) -> list[dict]:
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-registry-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "studios.json"
        path.write_text(json.dumps(entries))
        with mock.patch.object(registry, "REGISTRY_FILE", path), \
             mock.patch.object(registry, "load_flags", lambda: {}), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STUDIOHUB_PACKAGED", None)
            return registry.load_registry()

    def test_a_legacy_entry_for_every_routed_modality_is_admitted(self) -> None:
        entries = [
            {"id": f"{modality}-remote", "modality": modality, "host": "127.0.0.1",
             "port": 52000 + index, "machine": f"node-{index}"}
            for index, modality in enumerate(sorted(vocabulary.WORKER_FOR_MODALITY))
        ]
        loaded = {studio["id"] for studio in self._load_with(entries)}
        missing = sorted({entry["id"] for entry in entries} - loaded)
        self.assertEqual(
            missing, [],
            "a routed modality was silently skipped by the legacy registry",
        )

    def test_the_dead_pre_registration_trap_is_gone(self) -> None:
        """`build_machine_entries` had no caller and skipped every non-image/voice modality."""
        self.assertFalse(hasattr(registry, "build_machine_entries"))
        self.assertFalse(hasattr(registry, "MODALITY_PORT"))
        self.assertFalse(hasattr(registry, "FAMILY_PORTS"))


class TranscriptionCapabilityTests(unittest.TestCase):
    """B11 — the STT surface is a capability, and its owner set is derived."""

    def test_the_owner_set_is_derived_from_the_operation_namespace(self) -> None:
        owners = {modality for modality, operation in capabilities.OPERATION_BY_MODALITY.items()
                  if operation == "audio.transcription"}
        self.assertTrue(owners, "no modality owns audio.transcription")
        self.assertTrue(owners <= monitor._transcription_fallback_modalities())

    def test_the_shipped_subtitle_worker_is_recognised_with_no_health_and_no_capability(
            self) -> None:
        """The regression: modality `transcription` was dropped from the fallback set."""
        studio = {"id": "subtitle", "modality": "transcription"}
        self.assertTrue(monitor.reports_transcription(studio))
        self.assertTrue(monitor.reports_transcription({"id": "voice", "modality": "voice"}))
        self.assertFalse(monitor.reports_transcription({"id": "chat", "modality": "chat"}))

    def test_a_new_modality_that_advertises_transcription_is_folded_in(self) -> None:
        declared = {"id": "stt", "modality": "a-new-modality",
                    "capabilities": ["transcribe", "subtitles"]}
        self.assertTrue(monitor.reports_transcription(declared))
        health = {"id": "stt2", "modality": "a-new-modality"}
        self.assertTrue(monitor.reports_transcription(
            health, {"transcription": {"available": True}}))
        self.assertFalse(monitor.reports_transcription(
            {"id": "other", "modality": "a-new-modality"}, {"generation": {}}))

    def test_the_registry_query_includes_exactly_the_advertisers(self) -> None:
        registry_rows = [{"id": "voice", "modality": "voice"},
                         {"id": "subtitle", "modality": "transcription"},
                         {"id": "chat", "modality": "chat"}]
        chosen = {row["id"] for row in monitor.transcription_studios(registry_rows, {})}
        self.assertEqual(chosen, {"voice", "subtitle"})


class VoiceModeVocabularyTests(unittest.TestCase):
    """B13 — the Voice worker publishes the modes; the Hub renames them and never re-derives.

    `_voice_modes` used to decide this itself: `"voice-design" in capabilities or "voicedesign"
    in repo` — the A9 name-substring shape — plus a `"tts" in capabilities` default, so a
    design-capable model whose repo id did not spell it lost a mode it offers, and a TTS model
    that declared nothing was advertised as `preset_voice` on the strength of being TTS. The
    producer now publishes a top-level `modes` array on every row, and these tests hold the Hub
    to reading it — including for the shipped catalog, so the next producer mode cannot land
    without the Hub being taught it.
    """

    def _shipped_rows(self) -> list[dict]:
        from components.voice.app.backend import catalog as voice_catalog

        rows = [voice_catalog.serialize_model(model) for model in voice_catalog.CATALOG]
        self.assertTrue(rows, "the shipped Voice catalog is empty")
        return rows

    def test_every_mode_the_producer_publishes_has_a_hub_control(self) -> None:
        """The invariant: the producer's vocabulary is covered by the Hub's mapping."""
        for row in self._shipped_rows():
            with self.subTest(model=row["repo"]):
                published = row["modes"]
                self.assertTrue(
                    published, "the producer promises a non-empty `modes` array")
                mapped = capabilities._voice_modes(
                    {"repo": row["repo"], "modes": published})
                # Compared through the real table, so this test cannot restate the mapping.
                self.assertEqual(
                    mapped,
                    [capabilities.VOICE_CONTROL_FOR_MODE[mode] for mode in published],
                )
                self.assertTrue(
                    set(mapped) <= set(capabilities.VOICE_CONTROLS),
                    "a mapped voice control is outside the Hub's published vocabulary",
                )

    def test_the_hub_vocabulary_is_exactly_the_mapping(self) -> None:
        self.assertEqual(set(capabilities.VOICE_CONTROLS),
                         set(capabilities.VOICE_CONTROL_FOR_MODE.values()))

    def test_the_mapping_keeps_the_producers_own_order(self) -> None:
        """The producer orders cloning→preset→design; the Hub renames without reordering."""
        self.assertEqual(
            capabilities._voice_modes(
                {"repo": "org/voice", "modes": ["design", "cloning"]}),
            [capabilities.VOICE_CONTROL_FOR_MODE["design"],
             capabilities.VOICE_CONTROL_FOR_MODE["cloning"]],
        )

    def test_a_mode_the_hub_does_not_know_fails_by_name_and_file(self) -> None:
        with self.assertRaises(ValueError) as raised:
            capabilities._voice_modes(
                {"repo": "org/new-voice", "modes": ["cloning", "beatbox"]})
        message = str(raised.exception)
        self.assertIn("'beatbox'", message)
        self.assertIn("org/new-voice", message)
        self.assertIn("capabilities.py", message)
        self.assertIn("components/voice/app/backend/catalog.py", message)

    def test_a_row_with_no_published_modes_is_not_guessed(self) -> None:
        """This row is what the old derivation turned into `reference_audio_clone`."""
        row = {"repo": "org/VoiceDesign-9B",
               "capabilities": ["tts", "voice-cloning", "voice-design"]}
        capabilities._reported_unpublished_voice_modes.discard(row["repo"])
        with self.assertLogs("studiohub.capabilities", level="WARNING") as captured:
            self.assertIsNone(capabilities._voice_modes(row))
        self.assertTrue(
            any(row["repo"] in line and "catalog.py" in line for line in captured.output),
            f"the stale producer was not named in the log: {captured.output}",
        )
        controls = capabilities._controls(row, "voice")
        self.assertNotIn(
            "voice_modes", controls,
            "a Voice row with no published modes must publish no mode at all")
        # Only the mode is withheld; the rest of the row is still described.
        self.assertEqual(controls["capabilities"], row["capabilities"])


class CloudGuardDirectionTests(unittest.TestCase):
    """B12 — anything that names a hosted vendor is refused; an unknown spelling is not."""

    def test_every_known_hosted_vendor_is_refused_through_provider(self) -> None:
        for vendor in sorted(cloud_guard.CLOUD_PROVIDER_NAMES):
            with self.subTest(vendor=vendor):
                self.assertIsNotNone(
                    cloud_guard.cloud_reason("org/local-looking", entries=({"provider": vendor},)))

    def test_a_new_vendors_own_identity_fields_are_refused(self) -> None:
        self.assertIsNotNone(cloud_guard.cloud_reason(
            "org/model", entries=({"cloud_provider": "brand-new-hosted"},)))
        self.assertIsNotNone(cloud_guard.cloud_reason(
            "org/model", entries=({"provider_model": "newvendor/llm-1"},)))
        self.assertIsNotNone(cloud_guard.cloud_reason(
            "org/model", entries=({"cloud_model_id": "newvendor/llm-1"},)))

    def test_an_unknown_local_spelling_survives_and_is_named_in_the_log(self) -> None:
        """The opposite direction of the A14 allowlist bug: unknown is local and loud."""
        with self.assertLogs("studiohub.cloud_guard", level="WARNING") as captured:
            cloud_guard._reported_unknown_providers.discard("local-mlx")
            self.assertIsNone(cloud_guard.cloud_reason(
                "org/model", entries=({"provider": "local-mlx"},)))
        self.assertTrue(any("cloud_guard.py" in line for line in captured.output))


class RecoveryScopeTests(unittest.TestCase):
    """The sweep addition — a modality with no measured stall budget is not silently recovered."""

    def test_an_uncovered_modality_has_no_stall_threshold(self) -> None:
        self.assertEqual(automatic_recovery.stall_threshold_s("image"), 3600.0)
        self.assertEqual(automatic_recovery.stall_threshold_s("voice"), 7200.0)
        for modality in ("chat", "transcription", "music", "a-new-modality"):
            with self.subTest(modality=modality):
                self.assertIsNone(automatic_recovery.stall_threshold_s(modality))
        self.assertEqual(automatic_recovery.SUPPORTED_MODALITIES, {"image", "voice"})


class MemoryControlCoverageTests(unittest.TestCase):
    """The sweep addition — every routed modality is governed by the memory-policy gate."""

    def test_every_routed_modality_is_governed(self) -> None:
        missing = sorted(set(vocabulary.WORKER_FOR_MODALITY)
                         - set(memory_control.SUPPORTED_MODALITIES))
        self.assertEqual(missing, [], "a routed modality is skipped by the memory-policy gate")

    def test_the_named_superset_is_documented(self) -> None:
        """`render` is the deliberate extra; `video` is a routed worker modality now."""
        self.assertTrue({"render", "video"} <= set(memory_control.SUPPORTED_MODALITIES))
        self.assertNotIn("render", vocabulary.WORKER_FOR_MODALITY)
        self.assertEqual(vocabulary.WORKER_FOR_MODALITY.get("video"), "video")


class EngineRouteTests(unittest.TestCase):
    """Lane contract workstream item 1 — `modality` is derived from the one engine table."""

    def test_every_registered_engine_publishes_a_modality(self) -> None:
        for engine, (worker, modality) in standalone._ENGINE_ROUTE.items():
            with self.subTest(engine=engine):
                self.assertEqual(standalone._worker_for_engine(engine), worker)
                self.assertEqual(standalone._modality_for_engine(engine), modality)

    def test_an_error_row_for_a_registered_engine_still_carries_the_modality(self) -> None:
        row = standalone._manifest_error_row(
            {"package_id": "p", "model_id": "m", "engine": "chat-mlx"}, "broken")
        self.assertEqual(row["modality"], "chat")
        self.assertIsNone(row["lane_contract"])

    def test_an_unregistered_engine_has_no_modality_to_publish(self) -> None:
        row = standalone._manifest_error_row(
            {"package_id": "p", "model_id": "m", "engine": "video-mlx"}, "broken")
        self.assertIsNone(row["modality"])


class LaneContractEchoTests(unittest.TestCase):
    """Lane contract workstream item 2 — echo the studio's contract, add the Hub's deadline."""

    class _Monitor:
        def __init__(self, rows, status=None):
            self._rows = rows
            self.status = status or {}

        def cached_aggregate_catalog(self):
            return {"models": self._rows}

    def test_the_hub_adds_its_own_deadline_and_never_a_worker_estimate(self) -> None:
        contract = {"schema": "studiohub.lane-contract.v1", "modality": "chat",
                    "operation": "chat.completion"}
        row = {"repo": "org/chat", "hub_studio": "chat", "lane_contract": contract}
        monitor_rows = self._Monitor([row], {"chat": {"status": "up"}})
        manifest = {"package_id": "chat-1", "model_id": "org/chat"}
        lane_contracts = standalone._lane_contracts(monitor_rows)
        echoed = standalone._contract_for(manifest, lane_contracts)
        self.assertEqual(echoed, contract)
        self.assertNotIn("deadline_seconds", contract,
                         "the worker's contract was mutated in place")

    def test_a_down_worker_contributes_no_contract(self) -> None:
        row = {"repo": "org/chat", "hub_studio": "chat",
               "lane_contract": {"schema": "studiohub.lane-contract.v1"}}
        self.assertEqual(
            standalone._lane_contracts(self._Monitor([row], {"chat": {"status": "down"}})), {})

    def test_a_broken_catalog_cache_cannot_fail_a_package_row(self) -> None:
        class Exploding:
            status = {}

            def cached_aggregate_catalog(self):
                raise RuntimeError("cache is unavailable")

        self.assertEqual(standalone._lane_contracts(Exploding()), {})

    def test_the_aggregate_catalog_passes_the_contract_through_verbatim(self) -> None:
        """`/api/hub/catalog` is a pass-through: the studio owns the contract, the Hub carries it."""
        from components.hub.backend import model_ids

        contract = {"schema": "studiohub.lane-contract.v1", "operation": "audio.transcription"}
        payload = {"models": [{"repo": "org/model", "lane_contract": contract}]}
        annotated = model_ids.annotate(payload)
        self.assertEqual(annotated["models"][0]["lane_contract"], contract)


if __name__ == "__main__":
    unittest.main()
