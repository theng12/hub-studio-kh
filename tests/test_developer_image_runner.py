"""Focused fake tests for the packaged Developer Mode image runner.

No real model is loaded, installed or generated. The child is a tiny local script that writes a
synthetic PNG; process metrics are real psutil values with fixed memory/swap inputs. The receipt,
request and capabilities are checked against the frozen candidate-2 schema when it is present.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_image_runner as runner  # noqa: E402

try:
    import jsonschema
except Exception:  # pragma: no cover - the focused test skips schema checks only
    jsonschema = None

AUTHORITY = Path("~/Developer/_orchestra/developer-mode-20261105/"
                 "developer-test-v1-candidate2.schema.json").expanduser()


def _png(width: int, height: int) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
    raw = b"".join(b"\x00" + b"\x10\x80\x40" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


FAKE_CHILD = r'''
import argparse, json, os, struct, sys, time, zlib
p = argparse.ArgumentParser()
p.add_argument("--request-file"); p.add_argument("--result-file"); p.add_argument("--output")
a, _ = p.parse_known_args()
request = json.loads(open(a.request_file, "r", encoding="utf-8").read())
time.sleep(float(os.environ.get("FAKE_SLEEP", "0.3")))
if os.environ.get("FAKE_SPAWN"):
    child = __import__("subprocess").Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    open(a.result_file + ".grandchild", "w").write(str(child.pid))
if os.environ.get("FAKE_CHATTER"):
    print(("noise prompt=" + request["prompt"] + " ") * 6000)
settings = request["request"]["settings"]
w, h = settings["width_px"], settings["height_px"]
def chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
raw = b"".join(b"\x00" + b"\x10\x80\x40" * w for _ in range(h))
open(a.output, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
print("fake child ran; prompt=" + request["prompt"])
if os.environ.get("FAKE_FAIL"):
    json.dump({"ok": False, "error": "engine said no", "error_code": "IMAGE_GENERATION_FAILED"},
              open(a.result_file, "w"))
    sys.exit(2)
result = {"ok": True, "decoded": True, "decoded_width": w, "decoded_height": h}
if not os.environ.get("FAKE_NO_GENERATION"):
    result["generation_seconds"] = 0.01
else:
    result["generation_seconds"] = None
    result["generation_seconds_reason"] = "fake engine reports no inference-only duration"
json.dump(result, open(a.result_file, "w"))
'''


class FixedMetrics:
    """Real process identity/tree from psutil, fixed available/total/swap inputs."""

    def __init__(self, *, available=8 * 1024 ** 3, total=16 * 1024 ** 3,
                 swap=(0,), rss_inflate=0, fail_available=False):
        from components.hub.developer_image_runner import Metrics
        self._real = Metrics()
        self._available = available
        self._total = total
        self._swap = list(swap)
        self._swap_index = 0
        self._rss_inflate = rss_inflate
        self._fail_available = fail_available

    def available_bytes(self):
        if self._fail_available:
            raise runner.MetricUnavailable("fake unavailable memory")
        return self._available

    def total_bytes(self):
        return self._total

    def swap_used_bytes(self):
        value = self._swap[min(self._swap_index, len(self._swap) - 1)]
        self._swap_index += 1
        return value

    def identity(self, pid):
        return self._real.identity(pid)

    def tree(self, pid):
        rss, identities = self._real.tree(pid)
        return rss + self._rss_inflate, identities

    def alive(self, identity):
        return self._real.alive(identity)


def _row():
    return types.SimpleNamespace(
        repo="fake/image-model", revision="a" * 40, package_id="fake-image-pkg",
        label="Fake Image 4-bit", engine="image-mflux",
        limits={"min_steps": 4, "max_steps": 4, "min_guidance": 1.0, "max_guidance": 1.0,
                "min_seed": 0, "max_seed": 100, "max_prompt": 100},
        size_presets={"1:1": (32, 32)}, dimension_multiple=32,
        memory=types.SimpleNamespace(low_ram_below_bytes=None),
        reference_limits={"min": 0, "max": 2})


def _manifest():
    return {"package_id": "fake-image-pkg", "model_id": "fake/image-model",
            "revision": "a" * 40, "engine": "image-mflux", "files": []}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="developer-image-runner-"))
        self.assets = self.tmp / "assets"
        shutil.copytree(runner.ASSETS_DIR, self.assets)
        self.manifests = self.tmp / "manifests"
        self.manifests.mkdir()
        (self.manifests / "fake-image-pkg.json").write_text(json.dumps(_manifest()))
        self.models = self.tmp / "models"
        self.models.mkdir()
        self.child = self.tmp / "fake_child.py"
        self.child.write_text(FAKE_CHILD)
        self.lib = runner.FixtureLibrary(self.assets)
        self.policy = runner.SafetyPolicy(self.assets)
        self.caps = runner.build_model_capabilities(
            self.manifests, rows=[_row()],
            preflight=lambda manifest: {"state": "files_verified", "folder": str(self.models)})
        self.assertEqual(len(self.caps), 1)
        self.assertTrue(self.caps[0].wire["installed"])
        self.runner = runner.ImageTestRunner(
            data_dir=self.tmp / "data", models_dir=self.models,
            manifests_dir=self.manifests, assets_dir=self.assets,
            models=self.caps, metrics=FixedMetrics(), child_command=[sys.executable, str(self.child)],
            hub_version="0.6.223", sample_interval_ms=20, stop_grace_seconds=1.0)
        os.environ["FAKE_SLEEP"] = "0.4"
        os.environ.pop("FAKE_SPAWN", None)

    def tearDown(self):
        for name in ("FAKE_SLEEP", "FAKE_SPAWN", "FAKE_FAIL", "FAKE_NO_GENERATION",
                     "FAKE_CHATTER"):
            os.environ.pop(name, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setting(self):
        return {"format": "studio.developer-test.node-setting-result.v1",
                "node_id": "node-1", "controller_id": "controller-1", "enabled": True,
                "revision": "rev-test-1", "verified": True, "reason": None,
                "updated_at": "2026-11-06T00:00:00Z", "request_id": "req-1"}

    def _request(self, **overrides):
        fixture_set = self.lib.set("developer-image-basic-v1")
        settings = {
            "width_px": 32, "height_px": 32, "steps": 4, "guidance": 1.0, "seed": 42,
            "memory_mode": "default", "reference_count": 1, "prompt_characters": 20,
            "fixture_set_id": fixture_set["id"],
            "prompt_sha256": runner.prompt_sha256(fixture_set["prompt_pattern"], 20),
        }
        settings.update(overrides.pop("settings", {}))
        identity = runner.runner_identity()
        request = {
            "format": "studio.developer-test.request.v1", "run_id": "run-1",
            "test_id": "test-1", "runner_id": runner.RUNNER_ID,
            "model": {"id": "fake/image-model", "revision": "a" * 40},
            "kind": "reference_counts", "settings": settings,
            "input_refs": runner.construct_input_refs(
                self.lib, fixture_set, settings["reference_count"]),
            "requested_limits": {"max_runtime_seconds": 5,
                                 "max_process_tree_rss_bytes": 1024 ** 3,
                                 "max_system_swap_growth_bytes": 0,
                                 "test_start_min_free_bytes": 0},
            "runner_revision": identity["runner_revision"],
            "runner_sha256": identity["runner_sha256"],
            "fixture_set_sha256": self.lib.sha256,
            "node_setting_revision": "rev-test-1",
        }
        request.update(overrides)
        return request

    def _run(self, request=None, **kwargs):
        request = request or self._request()
        raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        return self.runner.run_attempt(
            request, raw, attempt_id=kwargs.pop("attempt_id", "attempt-1"),
            idempotency_key="key-1", node_id="node-1", controller_id="controller-1",
            **kwargs)

    def _validate(self, definition, value):
        if jsonschema is None or not AUTHORITY.exists():
            return
        schema = json.loads(AUTHORITY.read_text())
        jsonschema.Draft202012Validator(
            {"$schema": "https://json-schema.org/draft/2020-12/schema",
             "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}).validate(value)

    def test_prompt_construction_is_exact_code_points(self):
        self.assertEqual(runner.construct_prompt("ab😀", 5), "ab😀ab")
        self.assertEqual(runner.construct_prompt("é", 2), "é")
        self.assertEqual(runner.construct_prompt("abc", 0), "")
        self.assertEqual(len(runner.construct_prompt("😀", 3)), 3)
        with self.assertRaises(runner.RunnerRefusal):
            runner.construct_prompt("", 1)

    def test_packaged_identities_match_raw_bytes(self):
        manifest = (self.assets / "fixtures" / "manifest.json").read_bytes()
        policy = (self.assets / "safety-policy.json").read_bytes()
        self.assertEqual(self.lib.sha256, hashlib.sha256(manifest).hexdigest())
        self.assertEqual(self.policy.sha256, hashlib.sha256(policy).hexdigest())
        self.assertEqual(runner.runner_identity()["runner_sha256"],
                         hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest())
        self.assertIsNone(runner.load_stop_proof(self.assets))

    def test_pin_mismatches_are_refused(self):
        bad = self._request(settings={"prompt_sha256": "0" * 64})
        with self.assertRaises(runner.RunnerRefusal):
            runner.validate_pinned_request(
                bad, fixtures=self.lib, policy=self.policy, models=self.caps,
                node_setting_revision="rev-test-1")
        bad = self._request(fixture_set_sha256="0" * 64)
        with self.assertRaises(runner.RunnerRefusal):
            runner.validate_pinned_request(
                bad, fixtures=self.lib, policy=self.policy, models=self.caps)
        bad = self._request()
        bad["settings"]["reference_count"] = 3
        with self.assertRaises(runner.RunnerRefusal):
            runner.validate_pinned_request(
                bad, fixtures=self.lib, policy=self.policy, models=self.caps)

    def test_capabilities_never_invent_support(self):
        caps = self.runner.capabilities(node_id="node-1", controller_id="controller-1",
                                        setting=self._setting())
        self._validate("capabilities", caps)
        self.assertFalse(caps["supported"])
        self.assertEqual(caps["reason"]["code"], "unsupported")
        self.assertIsNone(caps["stop_proof"])
        self.assertTrue(caps["fixture_sets"])
        self.assertEqual(caps["models"][0]["manifest_sha256"],
                         hashlib.sha256((self.manifests / "fake-image-pkg.json").read_bytes()).hexdigest())

    def test_process_tree_stop_proof_is_bounded_but_not_full_support(self):
        proof = runner.run_stop_proof(self.tmp / "proof", grace=2.0)
        self.assertTrue(proof["stop_latency_seconds"] > 0)
        loaded = runner.load_stop_proof(self.tmp / "proof")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["scope"], "process_tree_stop_primitive")
        # Package this proof beside the fixtures: support must remain false.
        for name in ("stop-proof.json", "stop-proof-meta.json",
                     "stop-proof-evidence.json"):
            shutil.copy(self.tmp / "proof" / name, self.assets / name)
        caps = self.runner.capabilities(node_id="node-1", controller_id="controller-1",
                                        setting=self._setting())
        self.assertFalse(caps["supported"])
        self.assertIn("process-tree", caps["reason"]["detail"])

    def test_full_runner_proof_flips_support_only_in_fixture(self):
        evidence = (self.assets / "evidence.bin")
        evidence.write_bytes(b"full-runner-proof-fixture")
        proof = {"stop_latency_seconds": 0.5, "source": "test fixture", "revision": "r1",
                 "proof_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
        (self.assets / "stop-proof.json").write_text(json.dumps(proof))
        (self.assets / "stop-proof-meta.json").write_text(json.dumps(
            {"scope": "full_runner", "evidence_file": "evidence.bin"}))
        caps = self.runner.capabilities(node_id="node-1", controller_id="controller-1",
                                        setting=self._setting())
        self._validate("capabilities", caps)
        self.assertTrue(caps["supported"])
        self.assertIsNone(caps["reason"])

    def test_fake_child_pass_receipt_matches_frozen_schema(self):
        outcome = self._run()
        self.assertTrue(outcome.terminal)
        self.assertIsNotNone(outcome.receipt)
        self.assertEqual(outcome.receipt["outcome"], "passed", outcome.receipt["reason"])
        self._validate("receipt", outcome.receipt)
        self.assertTrue(all(check["passed"] for check in outcome.receipt["checks"]))
        self.assertTrue(outcome.release.confirmed)
        self.assertGreater(outcome.receipt["measurements"]["peak_process_memory_bytes"], 0)
        self.assertEqual(outcome.receipt["output"]["width_px"], 32)
        self.assertEqual(outcome.receipt["output"]["height_px"], 32)
        self.assertEqual(outcome.receipt["log"]["sha256"],
                         hashlib.sha256(outcome.log_bytes).hexdigest())
        self.assertNotIn(b"tiny red square", outcome.log_bytes)

    def test_runtime_budget_stops_the_exact_tree(self):
        os.environ["FAKE_SLEEP"] = "5"
        request = self._request()
        request["requested_limits"]["max_runtime_seconds"] = 1
        outcome = self._run(request)
        self.assertEqual(outcome.receipt["outcome"], "failed")
        self.assertEqual(outcome.receipt["reason"]["code"], "runtime_budget")
        self.assertTrue(outcome.release.confirmed)
        self._validate("receipt", outcome.receipt)
        checks = {item["name"]: item for item in outcome.receipt["checks"]}
        self.assertFalse(checks["runtime_budget"]["passed"])
        self.assertEqual(checks["runtime_budget"]["reason"]["code"], "runtime_budget")

    def test_rss_budget_is_enforced_and_release_confirmed(self):
        os.environ["FAKE_SLEEP"] = "5"
        self.runner.metrics = FixedMetrics(rss_inflate=2 * 1024 ** 3)
        outcome = self._run()
        self.assertEqual(outcome.receipt["reason"]["code"], "rss_budget")
        self.assertTrue(outcome.release.confirmed)

    def test_cancel_event_settles_without_a_pass(self):
        os.environ["FAKE_SLEEP"] = "5"
        event = threading.Event()
        threading.Timer(0.2, event.set).start()
        outcome = self._run(cancel_event=event)
        self.assertEqual(outcome.receipt["outcome"], "cancelled")
        self.assertEqual(outcome.receipt["reason"]["code"], "cancel_requested")
        self.assertTrue(outcome.release.confirmed)
        self._validate("receipt", outcome.receipt)

    def test_unreadable_start_memory_is_not_a_zero(self):
        self.runner.metrics = FixedMetrics(fail_available=True)
        outcome = self._run()
        self.assertEqual(outcome.receipt["outcome"], "not_run")
        self.assertEqual(outcome.receipt["reason"]["code"], "metric_unavailable")
        reasons = {item["field"] for item in outcome.receipt["metric_unavailable_reasons"]}
        self.assertIn("available_memory_before_start_bytes", reasons)
        self.assertIsNone(
            outcome.receipt["measurement_metadata"]["available_memory_before_start_bytes"])
        self._validate("receipt", outcome.receipt)

    def test_installer_callback_is_the_only_install_path(self):
        self.caps[0].wire["installed"] = False
        calls = []

        def install(package_id, revision):
            calls.append((package_id, revision))
            return {"started": True, "completed": True, "download_id": "download-1",
                    "verified_manifest_sha256": "b" * 64}

        self.runner.install_model = install
        outcome = self._run()
        self.assertEqual(calls, [("fake-image-pkg", "a" * 40)])
        self.assertEqual(outcome.receipt["installation"]["download_id"], "download-1")
        self.assertTrue(outcome.receipt["installation"]["completed"])
        self.assertEqual(outcome.receipt["outcome"], "passed")

    def test_installer_failure_is_model_install_failed(self):
        self.caps[0].wire["installed"] = False
        self.runner.install_model = lambda package_id, revision: {
            "started": True, "completed": False, "release_confirmed": True,
            "reason": "disk full"}
        outcome = self._run()
        self.assertEqual(outcome.receipt["outcome"], "failed")
        self.assertEqual(outcome.receipt["reason"]["code"], "model_install_failed")
        self._validate("receipt", outcome.receipt)

    def test_stop_recorded_kills_only_the_recorded_identity(self):
        process = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                                   start_new_session=True)
        try:
            from components.hub.developer_image_runner import Metrics
            identity = Metrics().identity(process.pid)
            self.assertIsNotNone(identity)
            attempt = self.runner._attempt_dir("recovery-1")
            (attempt / "child-identity.json").write_text(json.dumps(
                {"pid": process.pid, "pgid": os.getpgid(process.pid),
                 "create_time": identity["create_time"]}))
            proof = self.runner.stop_recorded("recovery-1")
            self.assertTrue(proof.confirmed)
            process.wait(timeout=2)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_stop_recorded_without_identity_uses_the_durable_stage(self):
        attempt = self.runner._attempt_dir("no-identity-1")
        self.assertFalse(self.runner.stop_recorded("no-identity-1").confirmed)
        (attempt / "stage.json").write_text(json.dumps({"stage": "installing"}))
        self.assertTrue(self.runner.stop_recorded("no-identity-1").confirmed)
        (attempt / "stage.json").write_text(json.dumps({"stage": "running"}))
        proof = self.runner.stop_recorded("no-identity-1")
        self.assertFalse(proof.confirmed)
        self.assertEqual(proof.reason["code"], "release_unknown")

    def test_stop_recorded_never_signals_an_unverified_identity(self):
        process = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                                   start_new_session=True)
        try:
            attempt = self.runner._attempt_dir("unverified-1")
            (attempt / "child-identity.json").write_text(json.dumps(
                {"pid": process.pid, "pgid": os.getpgid(process.pid), "create_time": 0.0}))
            proof = self.runner.stop_recorded("unverified-1")
            self.assertFalse(proof.confirmed)
            time.sleep(0.2)
            self.assertIsNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_stop_recorded_never_signals_a_corrupt_pgid(self):
        process = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                                   start_new_session=True)
        try:
            identity = runner.Metrics().identity(process.pid)
            self.assertIsNotNone(identity)
            attempt = self.runner._attempt_dir("corrupt-pgid-1")
            (attempt / "child-identity.json").write_text(json.dumps(
                {"pid": process.pid, "pgid": process.pid + 1,
                 "create_time": identity["create_time"]}))
            proof = self.runner.stop_recorded("corrupt-pgid-1")
            self.assertFalse(proof.confirmed)
            time.sleep(0.2)
            self.assertIsNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_engine_refusal_is_preserved_over_the_exit_status(self):
        os.environ["FAKE_FAIL"] = "1"
        outcome = self._run()
        self.assertEqual(outcome.receipt["outcome"], "failed")
        self.assertEqual(outcome.receipt["reason"]["code"], "engine_refused")
        self.assertIn("engine said no", outcome.receipt["reason"]["detail"])
        self._validate("receipt", outcome.receipt)

    def test_missing_generation_duration_is_not_labelled_inference(self):
        os.environ["FAKE_NO_GENERATION"] = "1"
        outcome = self._run()
        self.assertEqual(outcome.receipt["outcome"], "failed")
        self.assertEqual(outcome.receipt["reason"]["code"], "incomplete_evidence")
        self.assertIsNone(outcome.receipt["measurements"]["generation_seconds"])
        reasons = {item["field"]: item["reason"]
                   for item in outcome.receipt["metric_unavailable_reasons"]}
        self.assertIn("generation_seconds", reasons)
        self.assertIn("no inference-only duration", reasons["generation_seconds"])
        self._validate("receipt", outcome.receipt)

    def test_bounded_log_is_truncated_and_never_retained_raw(self):
        os.environ["FAKE_CHATTER"] = "1"
        os.environ["FAKE_SLEEP"] = "0.1"
        outcome = self._run()
        self.assertLessEqual(len(outcome.log_bytes), runner.LOG_TOTAL_BYTES)
        self.assertTrue(outcome.receipt["log"]["truncated"])
        self.assertNotIn(b"tiny red square", outcome.log_bytes)
        self.assertFalse((outcome.attempt_dir / "child.log.raw").exists())

    def test_log_sink_redacts_every_split_and_overlapping_secret(self):
        secrets = [b"abcdef", b"ghijkl", b"cdef"]
        stream = b"abcdefghijkl"
        for split in range(len(stream) + 1):
            sink = runner._BoundedLogSink(secrets)
            sink.feed(stream[:split])
            sink.feed(stream[split:])
            sink.finish()
            body = sink.body()
            for secret in secrets:
                self.assertNotIn(secret, body, f"split={split}")

    def test_default_child_command_help_smoke(self):
        completed = subprocess.run(
            [sys.executable, "-I", "-B", runner.__file__, "--help"],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--request-file", completed.stdout)
        default = runner.ImageTestRunner(
            data_dir=self.tmp / "default-data", models_dir=self.models,
            manifests_dir=self.manifests, assets_dir=self.assets,
            runtime_python=sys.executable, metrics=FixedMetrics())
        self.assertNotIn("--child", default._child_command)

    def test_packaged_fixture_set_covers_the_campaign_order(self):
        fixture_set = self.lib.set("developer-image-basic-v1")
        self.assertGreaterEqual(len(fixture_set["reference_order"]), 10)
        self.assertEqual(len(set(fixture_set["reference_order"])), 10)
        for fixture in fixture_set["fixtures"]:
            self.assertEqual((fixture["width_px"], fixture["height_px"]), (1024, 1024))
        self.assertEqual(runner.construct_input_refs(self.lib, fixture_set, 0), [])
        self.assertEqual(len(runner.construct_input_refs(self.lib, fixture_set, 10)), 10)
        with self.assertRaises(runner.RunnerRefusal):
            runner.construct_input_refs(self.lib, fixture_set, 11)

    def test_test_reference_range_allows_txt2img_and_caps_at_fixtures(self):
        row = _row()
        row.reference_limits = {"min": 1, "max": 10}
        caps = runner.build_model_capabilities(
            self.manifests, rows=[row], preflight=lambda manifest: {"state": "files_verified"},
            fixture_reference_max=10)
        bounds = caps[0].wire["settings_bounds"]["reference_count"]
        self.assertEqual(bounds, {"minimum": 0, "maximum": 10})
        self.assertEqual(caps[0].wire["intrinsic_reference_max"], 10)
        caps = runner.build_model_capabilities(
            self.manifests, rows=[row], preflight=lambda manifest: {"state": "files_verified"},
            fixture_reference_max=4)
        self.assertEqual(caps[0].wire["settings_bounds"]["reference_count"]["maximum"], 4)
        self.assertEqual(caps[0].wire["intrinsic_reference_max"], 10)

    def test_memory_modes_projects_bounded_staged_from_reference_memory(self):
        # Single-mode text lane + bounded edit policy (e.g. Qwen Image 2.1 Turbo).
        qwen_style = types.SimpleNamespace(
            memory=types.SimpleNamespace(low_ram_below_bytes=None, single_mode_reason="always bounded"),
            reference_memory=types.SimpleNamespace(low_ram_below_bytes=18_525_000_000))
        self.assertEqual(runner._memory_modes(qwen_style), ["default", "bounded-staged"])

        # Bounded text lane policy (e.g. FLUX.2 Klein).
        klein_style = types.SimpleNamespace(
            memory=types.SimpleNamespace(low_ram_below_bytes=12_000_000_000),
            reference_memory=None)
        self.assertEqual(runner._memory_modes(klein_style), ["default", "bounded-staged"])

        # Single-mode only, neither lane bounded.
        single_mode = types.SimpleNamespace(
            memory=types.SimpleNamespace(low_ram_below_bytes=None),
            reference_memory=None)
        self.assertEqual(runner._memory_modes(single_mode), ["default"])

        # Dict row shape with dict policies.
        dict_row = {
            "memory": {"low_ram_below_bytes": None},
            "reference_memory": {"low_ram_below_bytes": 51_539_607_552},
        }
        self.assertEqual(runner._memory_modes(dict_row), ["default", "bounded-staged"])

    def test_settings_validation_accepts_bounded_staged_when_offered(self):
        settings = {
            "width_px": 32, "height_px": 32, "steps": 4, "guidance": 1.0, "seed": 42,
            "memory_mode": "bounded-staged", "reference_count": 1, "prompt_characters": 20,
            "fixture_set_id": "developer-image-basic-v1",
            "prompt_sha256": runner.prompt_sha256("pattern", 20),
        }
        bounds = {
            "width_px": {"minimum": 32, "maximum": 32},
            "height_px": {"minimum": 32, "maximum": 32},
            "dimension_multiple": 32,
            "steps": {"minimum": 1, "maximum": 10},
            "guidance": {"minimum": 0.0, "maximum": 2.0},
            "seed": {"minimum": 0, "maximum": 100},
            "reference_count": {"minimum": 0, "maximum": 2},
            "prompt_characters": {"minimum": 0, "maximum": 100},
        }
        # Accepted when bounded-staged is offered.
        runner._validate_settings(settings, bounds, ["default", "bounded-staged"], {"1:1": (32, 32)})
        # Refused when bounded-staged is not offered.
        with self.assertRaises(runner.RunnerRefusal) as ctx:
            runner._validate_settings(settings, bounds, ["default"], {"1:1": (32, 32)})
        self.assertEqual(ctx.exception.code, "invalid_request")

    def test_effective_sample_interval_is_recorded(self):
        self.assertEqual(runner._effective_sample_interval_ms(20, 5), 250)
        self.assertEqual(runner._effective_sample_interval_ms(250, 3600), 900)
        self.assertEqual(runner._effective_sample_interval_ms(1000, 5), 1000)
        outcome = self._run()
        self.assertEqual(outcome.receipt["measurement_metadata"]["method"]["sample_interval_ms"],
                         250)

    def test_pre_spawn_revalidation_refusal_settles_not_run(self):
        def revalidate():
            raise runner.RunnerRefusal("stale_setting", "the setting changed during installation")

        outcome = self._run(revalidate=revalidate)
        self.assertEqual(outcome.receipt["outcome"], "not_run")
        self.assertEqual(outcome.receipt["reason"]["code"], "stale_setting")
        self.assertFalse((outcome.attempt_dir / "child-identity.json").exists())
        self.assertFalse((outcome.attempt_dir / "output.png").exists())
        self._validate("receipt", outcome.receipt)

    def test_model_manifest_change_after_acceptance_is_refused(self):
        (self.manifests / "fake-image-pkg.json").write_text(
            json.dumps(_manifest()) + " ")
        outcome = self._run()
        self.assertEqual(outcome.receipt["outcome"], "not_run")
        self.assertEqual(outcome.receipt["reason"]["code"], "stale_setting")
        self.assertFalse((outcome.attempt_dir / "output.png").exists())

    def test_log_chunk_reconstructs_retained_bytes(self):
        outcome = self._run()
        chunk = self.runner.read_log_chunk("attempt-1", 0, 4)
        self.assertEqual(chunk["length_bytes"], min(4, len(outcome.log_bytes)))
        self.assertEqual(chunk["chunk_sha256"], hashlib.sha256(outcome.log_bytes[:4]).hexdigest())


if __name__ == "__main__":
    unittest.main()
