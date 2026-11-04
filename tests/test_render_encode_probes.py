#!/usr/bin/env python3
"""The durable encode-probe control: adoption, evidence, holds and recovery.

No real FFmpeg, no real process, no live node. The runner is a fake that writes the
encoded clip and answers the probe, and the executor is a fake that answers the
admission, memory and process-state questions.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import render_encode_probes  # noqa: E402
from components.hub.backend.render_encode_probe import ProbeRun  # noqa: E402
from components.hub.backend.render_encode_probes import (  # noqa: E402
    EncodeProbeControl,
)

ENCODE = {
    "container": "mp4",
    "requested_codec": "h264",
    "requested_effort": "medium",
    "crf": 23,
    "quality_cpu_preset": "medium",
    "bitrate_kbps": None,
}
OTHER_ENCODE = {**ENCODE, "requested_effort": "hardware"}

NODE = "shop-m4-controller-01"


def report(*, width=1920, height=1080, fps="24/1", duration="2.000000",
           pix_fmt="yuv420p", video_codec="h264", audio_codec="aac",
           format_name="mov,mp4,m4a,3gp,3g2,mj2"):
    return {
        "streams": [
            {"codec_type": "video", "codec_name": video_codec, "pix_fmt": pix_fmt,
             "width": width, "height": height, "avg_frame_rate": fps,
             "duration": duration},
            {"codec_type": "audio", "codec_name": audio_codec, "duration": duration},
        ],
        "format": {"format_name": format_name, "duration": duration, "bit_rate": "1500000"},
    }


class FakeAuthority:
    """The loader's snapshot: pinned tools, digests and bounds, and nothing else."""

    class _Qualification:
        profile_digest = "a" * 64
        tools_digest = "b" * 64
        compiler_digest = "c" * 64
        hardware_class = "m4-16gb"

    class _Bounds:
        max_rss_bytes = 2 * 1024 ** 3
        max_wall_seconds = 600
        min_available_memory_bytes = 1
        output_limit_bytes = 64 * 1024 * 1024

    def __init__(self):
        self.qualification = type(self)._Qualification()
        self.bounds = FakeAuthority._Bounds()
        self.ffmpeg = Path("/tools/ffmpeg")
        self.ffprobe = Path("/tools/ffprobe")
        self.selected_tier = "H.264 · medium"
        self.exact_encoder = "libx264"


class FakeLoader:
    def __init__(self, authority=FakeAuthority()):
        self._authority = authority

    def snapshot(self):
        return self._authority


class FakeBroker:
    """The broker's physical hold, and only that."""

    def __init__(self):
        self.holds: dict[str, str] = {}
        self.acquired: list[tuple[str, str]] = []
        self.released: list[tuple[str, str]] = []
        self.restored: list[tuple[str, str]] = []
        self.refuse = False

    def acquire_external_machine(self, machine, owner):
        if self.refuse:
            return False
        self.acquired.append((machine, owner))
        self.holds[machine] = owner
        return True

    def release_external_machine(self, machine, owner):
        self.released.append((machine, owner))
        if self.holds.get(machine) == owner:
            del self.holds[machine]

    def restore_external_machine(self, machine, owner):
        self.restored.append((machine, owner))
        self.holds.setdefault(machine, owner)


class FakeExecutor:
    """The executor's own gates, answered without a Hub."""

    def __init__(self):
        self.broker = FakeBroker()
        self.admission = True
        self.memory = True
        self.process_state = "gone"
        self.stopped: list[object] = []
        self.stop_works = True

    def _admission_allowed(self, owner=None):
        return self.admission

    def _has_memory(self, authority):
        return self.memory

    def _recorded_process_state(self, identity):
        return self.process_state

    async def _stop_recovered_exact_group(self, identity):
        self.stopped.append(identity)
        return self.stop_works


class FakeRunner:
    """Stands in for the process owner: writes the encoded file, answers the probe."""

    def __init__(self, *, probe_report=None, encoder_code=0, probe_code=0,
                 encoder_stderr=b"", output=b"probe-bytes"):
        self.probe_report = probe_report if probe_report is not None else report()
        self.encoder_code = encoder_code
        self.probe_code = probe_code
        self.encoder_stderr = encoder_stderr
        self.output = output
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv, timeout_seconds, workspace, on_spawn=None):
        argv = list(argv)
        self.calls.append((argv, timeout_seconds))
        if on_spawn is not None:
            on_spawn(FakeIdentity())
        if len(self.calls) == 1:
            Path(argv[-1]).write_bytes(self.output)
            return ProbeRun(self.encoder_code, b"", self.encoder_stderr, 0.5)
        payload = self.probe_report
        if not isinstance(payload, (bytes, bytearray)):
            payload = json.dumps(payload).encode("utf-8")
        return ProbeRun(self.probe_code, payload, b"", 0.2)


class FakeIdentity:
    """A process identity that no live process matches."""

    def __init__(self):
        self.pid = 999999
        self.process_group_id = 999999
        self.start_token = "0.0:0.0:/tools/ffmpeg"


class ProbeControlFixture(unittest.TestCase):
    """A temp data root with a fake loader, executor and runner."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.data_root = self.root / "data"
        self.data_root.mkdir()
        self.loader = FakeLoader()
        self.executor = FakeExecutor()
        self.runner = FakeRunner()
        self.node = NODE
        self.paused = False
        self.control = EncodeProbeControl(
            self.data_root, loader=self.loader, executor=self.executor,
            node_id=lambda: self.node,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
            admission_allowed=lambda: not self.paused,
            runner=self.runner, expected_uid=os.getuid(), clock=lambda: 1_000,
            scratch_root=self.root / "scratch")

    def probe(self, encode=None):
        return asyncio.run(self.control.probe(encode or ENCODE))

    def inspect(self, encode=None):
        return self.control.inspect(encode or ENCODE)


class IdentityTests(ProbeControlFixture):
    def test_a_tuple_is_unmeasured_before_it_is_probed(self):
        state = self.inspect()
        self.assertEqual(state["state"], "unmeasured")
        self.assertIsNone(state["evidence"])
        self.assertIsNone(state["authority_digest"])
        self.assertIsNotNone(state["probe_key"])

    def test_a_tuple_that_is_not_the_closed_six_field_form_is_refused(self):
        for encode in ({**ENCODE, "extra": 1}, {**ENCODE, "crf": "23"},
                       {key: value for key, value in ENCODE.items() if key != "crf"}):
            with self.subTest(encode=encode):
                state = self.control.inspect(encode)
                self.assertEqual(state["state"], "unmeasured")
                self.assertIsNone(state["probe_key"])
                self.assertIn("six-field", state["reason"])

    def test_a_different_tuple_is_a_different_probe(self):
        first = self.inspect(ENCODE)
        second = self.inspect(OTHER_ENCODE)
        self.assertNotEqual(first["probe_key"], second["probe_key"])

    def test_a_changed_node_is_a_different_probe(self):
        first = self.inspect()
        self.node = "shop-m4-controller-02"
        self.assertNotEqual(first["probe_key"], self.inspect()["probe_key"])

    def test_a_changed_tool_digest_is_a_different_probe(self):
        first = self.inspect()

        class OtherQualification(FakeAuthority._Qualification):
            tools_digest = "d" * 64

        class OtherAuthority(FakeAuthority):
            _Qualification = OtherQualification

        self.loader._authority = OtherAuthority()
        self.assertNotEqual(first["probe_key"], self.inspect()["probe_key"])

    def test_without_an_installed_authority_nothing_is_probed(self):
        self.loader._authority = None
        state = self.inspect()
        self.assertEqual(state["state"], "unmeasured")
        self.assertIsNone(state["probe_key"])
        self.assertIn("authority", state["reason"])

    def test_reading_writes_nothing(self):
        self.inspect()
        self.inspect(OTHER_ENCODE)
        self.assertEqual(self._files(), set())

    def _files(self) -> set[str]:
        return {str(path.relative_to(self.data_root))
                for path in self.data_root.rglob("*") if path.is_file()}


class ProbeTests(ProbeControlFixture):
    def test_a_successful_probe_publishes_support_with_exact_evidence(self):
        state = self.probe()
        self.assertEqual(state["state"], "supported")
        evidence = state["evidence"]
        self.assertEqual(evidence["format"], render_encode_probes.EVIDENCE_FORMAT)
        self.assertEqual(evidence["requested_encode"], ENCODE)
        self.assertEqual(evidence["node_identity"], {"physical_node_id": NODE})
        self.assertEqual(evidence["hardware_identity"]["backend"], "libx264")
        self.assertEqual(evidence["hardware_identity"]["chip"], "Apple M4")
        self.assertEqual(evidence["profile_digest"], "a" * 64)
        self.assertEqual(evidence["tools_digest"], "b" * 64)
        self.assertEqual(evidence["compiler_digest"], "c" * 64)
        self.assertEqual(evidence["purpose"], "encode_support_only")
        self.assertEqual(len(evidence["probe_result_digest"]), 64)
        self.assertEqual(len(evidence["probe_vector_digest"]), 64)
        self.assertEqual(state["authority_digest"],
                         render_encode_probes._digest(evidence))

    def test_the_evidence_is_closed_and_carries_no_scratch_path(self):
        evidence = self.probe()["evidence"]
        self.assertEqual(set(evidence), render_encode_probes._EVIDENCE_KEYS)
        text = json.dumps(evidence)
        self.assertNotIn("scratch", text)
        self.assertNotIn("/tmp", text)

    def test_the_authority_digest_is_the_documented_canonical_hash(self):
        state = self.probe()
        canonical = json.dumps(
            state["evidence"], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.assertEqual(state["authority_digest"],
                         __import__("hashlib").sha256(canonical).hexdigest())

    def test_a_failed_encode_is_never_support(self):
        self.runner = FakeRunner(encoder_code=1, encoder_stderr=b"Unknown encoder")
        self.control._runner_callable = self.runner
        state = self.probe()
        self.assertEqual(state["state"], "unsupported")
        self.assertIsNone(state["evidence"])
        self.assertIsNone(state["authority_digest"])

    def test_a_wrong_probe_report_is_never_support(self):
        self.runner = FakeRunner(probe_report=report(pix_fmt="yuv444p"))
        self.control._runner_callable = self.runner
        state = self.probe()
        self.assertEqual(state["state"], "unsupported")
        self.assertIsNone(state["evidence"])

    def test_the_probe_holds_the_machine_and_releases_it_afterwards(self):
        self.probe()
        self.assertEqual(len(self.executor.broker.acquired), 1)
        self.assertEqual(len(self.executor.broker.released), 1)
        self.assertEqual(self.executor.broker.holds, {})

    def test_a_paused_mac_never_probes(self):
        self.paused = True
        state = self.probe()
        self.assertEqual(state["state"], "unmeasured")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.executor.broker.acquired, [])

    def test_a_closed_render_admission_never_probes(self):
        self.executor.admission = False
        state = self.probe()
        self.assertEqual(state["state"], "unmeasured")
        self.assertEqual(self.runner.calls, [])

    def test_a_mac_without_the_memory_never_probes(self):
        self.executor.memory = False
        state = self.probe()
        self.assertEqual(state["state"], "unmeasured")
        self.assertEqual(self.runner.calls, [])

    def test_a_busy_mac_never_probes(self):
        self.executor.broker.refuse = True
        state = self.probe()
        self.assertEqual(state["state"], "unmeasured")
        self.assertEqual(self.runner.calls, [])

    def test_a_tuple_the_compiler_refuses_is_unsupported_not_unmeasured(self):
        state = self.probe({**ENCODE, "crf": 99, "quality_cpu_preset": "medium"})
        self.assertEqual(state["state"], "unsupported")
        self.assertEqual(self.runner.calls, [])

    def test_a_second_post_never_runs_the_probe_again(self):
        first = self.probe()
        second = self.probe()
        self.assertEqual(first["state"], "supported")
        self.assertEqual(second["state"], "supported")
        self.assertEqual(len(self.runner.calls), 2,
                         "one encode and one ffprobe, and no more")

    def test_a_failed_spawn_without_identity_retains_the_hold(self):
        """A launch that never happened proves nothing about this Mac's encoder."""

        def exploding_runner(argv, timeout_seconds, workspace, on_spawn=None):
            raise OSError("the tool could not be run")

        self.control._runner_callable = exploding_runner
        state = self.probe()
        self.assertEqual(state["state"], "pending")
        self.assertIsNone(state["evidence"])
        self.assertEqual(self.executor.broker.released, [])
        self.assertTrue(self.executor.broker.holds)

    def test_a_post_after_a_failure_never_runs_the_probe_again(self):
        self.runner = FakeRunner(encoder_code=1)
        self.control._runner_callable = self.runner
        first = self.probe()
        second = self.probe()
        self.assertEqual(first["state"], "unsupported")
        self.assertEqual(second["state"], "unsupported")
        self.assertEqual(len(self.runner.calls), 1)

    def test_a_capability_only_probe_publishes_no_tier_and_no_grant(self):
        """Hardware HEVC is a probe-only vector: it names no render tier."""
        hevc = {**ENCODE, "requested_codec": "h265", "requested_effort": "hardware"}
        self.runner = FakeRunner(probe_report=report(video_codec="hevc"))
        self.control._runner_callable = self.runner
        state = self.probe(hevc)
        self.assertEqual(state["state"], "supported")
        evidence = state["evidence"]
        self.assertEqual(evidence["exact_encoder"], "hevc_videotoolbox")
        self.assertIsNone(evidence["selected_tier"])
        # A capability-only entry never becomes a render grant.
        grants = [entry for entry in self.control.supported_entries()
                  if entry["requested_encode"]["requested_codec"] == "h265"]
        self.assertEqual(len(grants), 1)
        self.assertIsNone(grants[0]["evidence"]["selected_tier"])


class ProcessSafetyTests(ProbeControlFixture):
    def test_each_phase_has_a_distinct_durable_identity(self):
        state = self.probe()
        self.assertEqual(state["state"], "supported")
        for phase in (1, 2):
            self.assertIsNotNone(self.control._read_launch(state["probe_key"], phase))

    def test_failed_second_launch_record_keeps_hold_and_refuses_evidence(self):
        original = self.control._write_launch
        self.control._write_launch = lambda key, identity, owner, phase: (
            original(key, identity, owner, phase) if phase == 1 else False)
        state = self.probe()
        self.assertEqual(state["state"], "pending")
        self.assertIsNone(state["evidence"])
        self.assertEqual(self.executor.broker.released, [])
        self.assertIsNone(self.control._read_result(state["probe_key"]))

    def test_unknown_group_after_leader_exit_keeps_hold(self):
        self.executor.process_state = "unknown"
        state = self.probe()
        self.assertEqual(state["state"], "pending")
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.executor.broker.released, [])

    def test_default_runner_refuses_a_surviving_group_after_leader_exit(self):
        import io
        from unittest.mock import Mock, patch
        process = Mock(pid=999999, stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=0)
        process.poll.return_value = 0
        self.executor.process_state = "unknown"
        self.control._identity_of = lambda process, executable: FakeIdentity()
        recorded = []
        with patch.object(render_encode_probes.subprocess, "Popen", return_value=process), \
                patch.object(render_encode_probes.os, "getpgid", side_effect=ProcessLookupError), \
                patch.object(render_encode_probes.os, "killpg") as kill:
            with self.assertRaisesRegex(RuntimeError, "did not settle"):
                self.control._default_runner(["/tools/ffmpeg"], 1, self.root, recorded.append)
        self.assertEqual(len(recorded), 1)
        kill.assert_not_called()

    def test_cancelled_request_keeps_thread_owned_until_shutdown_drains_it(self):
        import threading
        started = threading.Event()
        finish = threading.Event()
        original = self.runner

        def waiting_runner(argv, budget, workspace, on_spawn):
            started.set()
            if not finish.wait(2):
                raise RuntimeError("test runner was not released")
            return original(argv, budget, workspace, on_spawn)

        self.control._runner_callable = waiting_runner

        async def scenario():
            request = asyncio.create_task(self.control.probe(ENCODE))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            stop = asyncio.create_task(self.control.stop())
            await asyncio.sleep(0)
            self.assertFalse(stop.done())
            finish.set()
            await stop
            self.assertFalse(self.control._runs)

        asyncio.run(scenario())
        self.assertEqual(self.inspect()["state"], "pending")
        self.assertEqual(self.executor.broker.released, [])

    def test_shutdown_fences_new_probes(self):
        asyncio.run(self.control.stop())
        state = self.probe()
        self.assertEqual(state["state"], "unmeasured")
        self.assertEqual(self.runner.calls, [])
        self.assertIn("shutting down", state["reason"])

    def test_kill_never_signals_an_unproved_identity(self):
        from unittest.mock import patch
        self.executor.process_state = "unknown"
        with patch.object(render_encode_probes.os, "killpg") as kill:
            self.assertFalse(self.control._kill_group(object(), FakeIdentity()))
            self.assertFalse(self.control._kill_group(object(), None))
        kill.assert_not_called()


class DurableRecordTests(ProbeControlFixture):
    def test_the_records_are_private_and_bounded(self):
        self.probe()
        for path in self.data_root.rglob("*"):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertLessEqual(path.stat().st_size, 16 * 1024)

    def test_the_evidence_survives_a_restart(self):
        state = self.probe()
        restarted = EncodeProbeControl(
            self.data_root, loader=self.loader, executor=self.executor,
            node_id=lambda: self.node,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
            admission_allowed=lambda: True, runner=FakeRunner(),
            expected_uid=os.getuid(), clock=lambda: 2_000,
            scratch_root=self.root / "scratch")
        again = restarted.inspect(ENCODE)
        self.assertEqual(again["state"], "supported")
        self.assertEqual(again["authority_digest"], state["authority_digest"])

    def test_supported_entries_lists_only_the_proved_tuples(self):
        self.assertEqual(self.control.supported_entries(), [])
        self.probe()
        entries = self.control.supported_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["state"], "supported")
        self.assertEqual(entries[0]["requested_encode"], ENCODE)

    def test_a_tampered_evidence_record_is_not_support(self):
        self.probe()
        path = (self.data_root / "render-authority/probe-evidence")
        for name in os.listdir(path):
            if name.endswith(".json") and not name.endswith(".proof.json"):
                target = path / name
                document = json.loads(target.read_bytes())
                document["exact_encoder"] = "h264_videotoolbox"
                target.write_bytes(json.dumps(document).encode("utf-8"))
        state = self.inspect()
        self.assertNotEqual(state["state"], "supported")
        self.assertIsNone(state["evidence"])
        self.assertIsNone(state["authority_digest"])


class RecoveryTests(ProbeControlFixture):
    def _write_pending_record(self):
        """A durable probe record with no outcome: what a crash leaves behind."""
        identity = self.control._identity(ENCODE)
        probe_key = self.control._probe_key(ENCODE, identity)
        self.control._write_record({
            "format": render_encode_probes.RECORD_FORMAT,
            "probe_key": probe_key,
            "requested_encode": dict(ENCODE),
            "identity": dict(identity),
            "owner": self.control._owner(probe_key),
            "created_at": 1_000,
        })
        return probe_key

    def test_a_probe_without_a_recorded_process_retains_its_hold_after_restart(self):
        probe_key = self._write_pending_record()
        recovered = asyncio.run(self.control.recover_and_restore_physical_holds())
        self.assertEqual(recovered, [{"probe_key": probe_key, "state": "pending"}])
        self.assertEqual(self.inspect()["state"], "pending")
        self.assertTrue(self.executor.broker.holds)
        self.assertEqual(self.executor.broker.released, [])

    def test_a_probe_whose_process_is_gone_is_unmeasured_after_a_restart(self):
        probe_key = self._write_pending_record()
        for phase in (1, 2):
            self.control._write_launch(
                probe_key, FakeIdentity(), self.control._owner(probe_key), phase)
        recovered = asyncio.run(self.control.recover_and_restore_physical_holds())
        self.assertEqual(recovered, [{"probe_key": probe_key, "state": "unmeasured"}])
        self.assertEqual(self.inspect()["state"], "unmeasured")

    def test_an_unproved_process_keeps_its_hold_and_stays_pending(self):
        probe_key = self._write_pending_record()
        self.control._write_launch(
            probe_key, FakeIdentity(), self.control._owner(probe_key))
        self.executor.process_state = "unknown"
        recovered = asyncio.run(self.control.recover_and_restore_physical_holds())
        self.assertEqual(recovered, [{"probe_key": probe_key, "state": "pending"}])
        self.assertEqual(self.inspect()["state"], "pending")
        self.assertEqual(self.executor.broker.restored, [("local", self.control._owner(probe_key))])

    def test_a_live_process_is_stopped_before_the_hold_is_released(self):
        probe_key = self._write_pending_record()
        for phase in (1, 2):
            self.control._write_launch(
                probe_key, FakeIdentity(), self.control._owner(probe_key), phase)
        self.executor.process_state = "live"
        recovered = asyncio.run(self.control.recover_and_restore_physical_holds())
        self.assertEqual(recovered, [{"probe_key": probe_key, "state": "unmeasured"}])
        self.assertEqual(len(self.executor.stopped), 2)
        self.assertEqual(self.inspect()["state"], "unmeasured")

    def test_a_live_process_that_cannot_be_stopped_keeps_its_hold(self):
        probe_key = self._write_pending_record()
        self.control._write_launch(
            probe_key, FakeIdentity(), self.control._owner(probe_key))
        self.executor.process_state = "live"
        self.executor.stop_works = False
        recovered = asyncio.run(self.control.recover_and_restore_physical_holds())
        self.assertEqual(recovered, [{"probe_key": probe_key, "state": "pending"}])
        self.assertEqual(self.inspect()["state"], "pending")
        self.assertEqual(self.executor.broker.released, [])


if __name__ == "__main__":
    unittest.main()
