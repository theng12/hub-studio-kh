"""Focused lifecycle tests for Developer Mode's node service.

No Hub, network, model download or real generation is used. The runner child is a tiny local
script that writes a synthetic PNG; metrics are real psutil identity with fixed memory inputs;
the ledger, setting and library all live in a temporary directory. The receipt is checked
against the frozen candidate-2 schema when it is present.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import types
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_image_runner as runner  # noqa: E402
from components.hub import developer_test_service as service  # noqa: E402
from components.hub import developer_tests as dt  # noqa: E402
from components.hub.backend import (  # noqa: E402
    broker, chat_jobs, developer_test_admission as admission, transcription_jobs, update_drain)

try:
    import jsonschema
except Exception:  # pragma: no cover - schema checks are skipped only when unavailable
    jsonschema = None

AUTHORITY = Path("~/Developer/_orchestra/developer-mode-20261105/"
                 "developer-test-v1-candidate2.schema.json").expanduser()

FAKE_CHILD = r'''
import argparse, json, os, struct, sys, time, zlib
p = argparse.ArgumentParser()
p.add_argument("--request-file"); p.add_argument("--result-file"); p.add_argument("--output")
a, _ = p.parse_known_args()
counter = os.environ.get("FAKE_COUNTER")
if counter:
    with open(counter, "a", encoding="utf-8") as stream:
        stream.write("run\n")
request = json.loads(open(a.request_file, "r", encoding="utf-8").read())
time.sleep(float(os.environ.get("FAKE_SLEEP", "0.05")))
settings = request["request"]["settings"]
w, h = settings["width_px"], settings["height_px"]
def chunk(tag, body):
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
raw = b"".join(b"\x00" + b"\x10\x80\x40" * w for _ in range(h))
open(a.output, "wb").write(b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
print("fake child ran; prompt=" + request["prompt"])
json.dump({"ok": True, "generation_seconds": 0.01, "decoded": True,
           "decoded_width": w, "decoded_height": h}, open(a.result_file, "w"))
'''


class FixedMetrics:
    """Real process identity/tree from psutil, fixed available/total/swap inputs."""

    def __init__(self, *, available=8 * 1024 ** 3, total=16 * 1024 ** 3, swap=(0,)):
        self._real = runner.Metrics()
        self._available = available
        self._total = total
        self._swap = list(swap)
        self._swap_index = 0

    def available_bytes(self):
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
        return self._real.tree(pid)

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


class DeveloperTestServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="developer-test-service-"))
        self.manifests = self.tmp / "manifests"
        self.manifests.mkdir()
        (self.manifests / "fake-image-pkg.json").write_text(json.dumps(_manifest()))
        self.models = self.tmp / "models"
        self.models.mkdir()
        self.runtime = self.tmp / "runtimes" / "image" / "bin" / "python3"
        self.runtime.parent.mkdir(parents=True)
        self.runtime.write_text("#!/bin/sh\nexit 1\n")
        self.runtime.chmod(0o755)
        self.release = self.tmp / "release-manifest.json"
        self.release.write_text(json.dumps({
            "schema": "studiohub.standalone-release", "schema_version": 1,
            "version": "0.6.223",
            "components": {"image": {"runtime_sha256": "b" * 64}}}))
        self.child = self.tmp / "fake_child.py"
        self.child.write_text(FAKE_CHILD)
        self.assets = self.tmp / "assets"
        shutil.copytree(runner.ASSETS_DIR, self.assets)
        # Packaged fixtures currently sit beside fixtures/manifest.json, while FixtureLibrary
        # resolves entries under fixtures/; mirror them so the copy matches its own manifest.
        for png in runner.ASSETS_DIR.glob("ref-*.png"):
            shutil.copy2(png, self.assets / "fixtures" / png.name)
        # Test-only full-runner proof: the packaged assets deliberately carry only the bounded
        # process-tree primitive, so real capabilities stay supported=false.
        evidence = self.assets / "evidence.bin"
        evidence.write_bytes(b"full-runner-proof-fixture")
        (self.assets / "stop-proof.json").write_text(json.dumps({
            "stop_latency_seconds": 0.5, "source": "test fixture", "revision": "r1",
            "proof_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}))
        (self.assets / "stop-proof-meta.json").write_text(json.dumps(
            {"scope": "full_runner", "evidence_file": "evidence.bin"}))
        self.counter = self.tmp / "runs.txt"
        self.env = mock.patch.dict(os.environ, {
            "STUDIOHUB_DATA_DIR": str(self.tmp / "data"),
            "STUDIOHUB_MODELS_DIR": str(self.models),
            "STUDIOHUB_MODEL_MANIFESTS": str(self.manifests),
            "STUDIOHUB_RELEASE_MANIFEST": str(self.release),
            "FAKE_COUNTER": str(self.counter),
            "FAKE_SLEEP": "0.05",
        })
        self.env.start()
        self.patches = [
            mock.patch.object(update_drain, "draining", return_value=False),
            mock.patch.object(admission, "_machine_paused", return_value=False),
            mock.patch.object(dt, "_identities", return_value=("node-a", "controller-a")),
        ]
        for patch in self.patches:
            patch.start()
        dt.ensure_initial_setting()
        admission._reset()
        broker.batches.clear()
        broker._external_machine_leases.clear()
        broker._preparing_machines.clear()
        broker.configure_monitor(types.SimpleNamespace(registry=[], status={}))
        broker.configure_render_executor(None)
        chat_jobs.batches.clear()
        transcription_jobs.batches.clear()
        self.revision = self._enable()
        self.lib = runner.FixtureLibrary(self.assets)
        self.caps = runner.build_model_capabilities(
            self.manifests, rows=[_row()],
            preflight=lambda manifest: {"state": "files_verified", "folder": str(self.models)})
        self.assertEqual(len(self.caps), 1)
        self.assertTrue(self.caps[0].wire["installed"])
        self.runner = self._configure()

    def tearDown(self) -> None:
        try:
            service.shutdown()
        finally:
            service._reset()
            admission._reset()
            broker.batches.clear()
            broker._external_machine_leases.clear()
            broker._preparing_machines.clear()
            broker.configure_render_executor(None)
            chat_jobs.batches.clear()
            transcription_jobs.batches.clear()
            for patch in self.patches:
                patch.stop()
            self.env.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def _configure(self, **overrides):
        options = {"child_command": [sys.executable, str(self.child)],
                   "models": self.caps, "rows": [_row()], "metrics": FixedMetrics(),
                   "assets_dir": self.assets, "hub_version": "0.6.223-test"}
        options.update(overrides)
        return service.configure(**options)

    def _enable(self) -> str:
        revision = json.loads(dt.setting_state()[1])["revision"]
        dt.change_setting(json.dumps({
            "format": dt.SETTING_FORMAT, "enabled": True,
            "expected_revision": revision, "request_id": "enable-1"}).encode())
        return json.loads(dt.setting_state()[1])["revision"]

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
            "format": dt.REQUEST_FORMAT, "run_id": "run-1", "test_id": "test-1",
            "runner_id": runner.RUNNER_ID,
            "model": {"id": "fake/image-model", "revision": "a" * 40},
            "kind": "reference_counts", "settings": settings,
            "input_refs": runner.construct_input_refs(
                self.lib, fixture_set, settings["reference_count"]),
            "requested_limits": {"max_runtime_seconds": 10,
                                 "max_process_tree_rss_bytes": 1024 ** 3,
                                 "max_system_swap_growth_bytes": 0,
                                 "test_start_min_free_bytes": 0},
            "runner_revision": identity["runner_revision"],
            "runner_sha256": identity["runner_sha256"],
            "fixture_set_sha256": self.lib.sha256,
            "node_setting_revision": self.revision,
        }
        request.update(overrides)
        return request

    def _raw(self, request=None) -> bytes:
        return json.dumps(request or self._request(), sort_keys=True,
                          separators=(",", ":")).encode()

    def _submit(self, key: str, request=None):
        return dt.submit(self._raw(request), key, actor={"kind": "fleet", "node_id": None},
                         destination="node-a")

    def _status(self, key: str) -> dict:
        return json.loads(dt.attempt_status(key)[1])

    def _wait(self, key: str, states, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self._status(key)
            if value["state"] in states:
                return value
            time.sleep(0.02)
        self.fail(f"{key} stayed {self._status(key)['state']}, expected one of {states}")

    def _runs(self) -> int:
        try:
            return len(self.counter.read_text().splitlines())
        except OSError:
            return 0

    def _validate(self, definition: str, value: dict) -> None:
        if jsonschema is None or not AUTHORITY.exists():
            return
        schema = json.loads(AUTHORITY.read_text())
        jsonschema.Draft202012Validator(
            {"$schema": "https://json-schema.org/draft/2020-12/schema",
             "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}).validate(value)

    # -- tests -------------------------------------------------------------

    def test_configure_uses_the_packaged_runtime_and_never_the_hub_interpreter(self):
        service._reset()
        summary = self._configure(child_command=None, assets_dir=None)
        self.assertTrue(summary["runtime"])
        self.assertNotEqual(service._runner._child_command[0], sys.executable)
        self.assertEqual(Path(service._runner._child_command[0]).resolve(),
                         self.runtime.resolve())
        status, body = service.capabilities()
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertFalse(value["supported"])
        self.assertEqual(value["reason"]["code"], "unsupported")
        self.assertIn("stop proof", value["reason"]["detail"])
        self.assertEqual(value["setting"]["revision"], self.revision)
        self._validate("capabilities", value)
        self.runner = self._configure()

    def test_capabilities_describe_source_mode_as_unsupported(self):
        service._reset()
        os.environ.pop("STUDIOHUB_RELEASE_MANIFEST", None)
        service.configure(models=self.caps, rows=[_row()], metrics=FixedMetrics())
        status, body = service.capabilities()
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertFalse(value["supported"])
        self.assertEqual(value["reason"]["code"], "unsupported")
        self._validate("capabilities", value)
        self.runner = self._configure()

    def test_invalid_optional_assets_refuse_tests_and_preserve_recovery_holds(self):
        raw = self._raw()
        row = dt.accept_attempt("broken-assets-key", raw, dt.validate_request(json.loads(raw)),
                                self.revision, actor={"kind": "fleet", "node_id": None},
                                destination="node-a")
        dt.update_attempt("broken-assets-key", state="running")
        summary = self._configure(assets_dir=self.tmp / "missing-assets")
        self.assertFalse(summary["configured"])
        with self.assertRaises(dt.Refused) as refusal:
            service.capabilities()
        self.assertEqual(refusal.exception.status, 503)
        result = service.recover()
        self.assertIn(row["attempt_id"], result["recovery_hold"])
        self.assertFalse(self._status("broken-assets-key")["resource_release_confirmed"])
        self.assertEqual(self._runs(), 0)

    def test_setting_is_rechecked_after_installation_before_spawn(self):
        self.caps = runner.build_model_capabilities(
            self.manifests, rows=[_row()],
            preflight=lambda manifest: {"state": "not_installed"})

        def install_and_change_setting(package_id, revision):
            dt.change_setting(json.dumps({
                "format": dt.SETTING_FORMAT, "enabled": True,
                "expected_revision": self.revision, "request_id": "change-during-install"
            }).encode())
            return {"started": True, "completed": True, "release_confirmed": True,
                    "download_id": "dl-changed", "verified_manifest_sha256": "c" * 64,
                    "reason": None}

        self._configure(install_model=install_and_change_setting)
        self._submit("setting-during-install")
        value = self._wait("setting-during-install", {"not_run"})
        self.assertTrue(value["resource_release_confirmed"])
        receipt = json.loads(dt.attempt_receipt("setting-during-install")[1])
        self._validate("receipt", receipt)
        self.assertEqual(receipt["reason"]["code"], "stale_setting")
        self.assertEqual(self._runs(), 0)

    def test_escaped_runner_refusal_does_not_claim_unknown_resources_released(self):
        def refuse_after_reservation(*args, **kwargs):
            admission.reserve(kwargs["attempt_id"])
            raise runner.RunnerRefusal("internal_error", "runner refused after reservation")

        with mock.patch.object(service._runner, "run_attempt", side_effect=refuse_after_reservation):
            self._submit("escaped-refusal")
            value = self._wait("escaped-refusal", {"recovery_hold"})
        self.assertFalse(value["resource_release_confirmed"])
        self.assertEqual(admission.active_attempt(), value["attempt_id"])
        self.assertEqual(self._runs(), 0)

    def test_pins_are_validated_before_durable_acceptance(self):
        broken = self._request(runner_sha256="0" * 64)
        with self.assertRaises(dt.Refused) as refusal:
            self._submit("pin-key", broken)
        self.assertEqual(refusal.exception.status, 409)
        self.assertEqual(refusal.exception.code, "stale_setting")
        self.assertIsNone(dt.attempt("pin-key"))

    def test_passing_attempt_settles_receipt_log_and_never_reruns_a_replay(self):
        status, body = self._submit("pass-key")
        self.assertEqual(status, 202)
        attempt_id = json.loads(body)["attempt_id"]
        self.assertEqual(self._wait("pass-key", {"passed"})["attempt_id"], attempt_id)

        status, body = dt.attempt_status("pass-key")
        value = json.loads(body)
        self.assertTrue(value["resource_release_confirmed"])
        receipt_bytes = dt.attempt_receipt("pass-key")[1]
        self.assertEqual(hashlib.sha256(receipt_bytes).hexdigest(), value["receipt_sha256"])
        receipt = json.loads(receipt_bytes)
        self._validate("receipt", receipt)
        self.assertEqual(receipt["outcome"], "passed")
        self.assertTrue(receipt["resource_release"]["confirmed"])
        chunk = json.loads(dt.attempt_log("pass-key", 0, 4096)[1])
        self.assertTrue(chunk["eof"])
        self.assertIn(b"fake child ran", base64.b64decode(chunk["data_base64"]))
        self.assertEqual(self._runs(), 1)

        # The same exact bytes reconcile the original attempt, never a second launch.
        status, body = self._submit("pass-key")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["attempt_id"], attempt_id)
        time.sleep(0.2)
        self.assertEqual(self._runs(), 1)

    def test_a_different_actor_or_destination_conflicts_on_replay(self):
        self._submit("bind-key")
        self._wait("bind-key", {"passed"})
        with self.assertRaises(dt.Refused) as refusal:
            dt.submit(self._raw(), "bind-key", actor={"kind": "hub", "node_id": None},
                      destination="node-a")
        self.assertEqual(refusal.exception.code, "conflict")
        with self.assertRaises(dt.Refused) as refusal:
            dt.submit(self._raw(), "bind-key", actor={"kind": "fleet", "node_id": None},
                      destination="node-b")
        self.assertEqual(refusal.exception.code, "conflict")

    def test_cancel_stops_the_owned_child_and_settles_cancelled(self):
        os.environ["FAKE_SLEEP"] = "3"
        self._submit("cancel-key")
        self._wait("cancel-key", {"running"})
        deadline = time.time() + 5
        while self._runs() == 0 and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self._runs(), 1)
        status, body = dt.cancel_attempt(
            "cancel-key",
            json.dumps({"format": dt.CANCEL_FORMAT, "request_id": "cancel-1"}).encode())
        self.assertEqual(status, 202)
        value = self._wait("cancel-key", {"cancelled"})
        self.assertTrue(value["resource_release_confirmed"])
        receipt = json.loads(dt.attempt_receipt("cancel-key")[1])
        self.assertEqual(receipt["outcome"], "cancelled")
        self.assertTrue(receipt["resource_release"]["confirmed"])
        self.assertIsNone(admission.active_attempt())
        self.assertNotIn("local", broker._external_machine_leases)

    def test_cancelling_a_waiting_attempt_settles_it_without_starting(self):
        os.environ["FAKE_SLEEP"] = "2"
        self._submit("first-key")
        self._wait("first-key", {"running"})
        self._submit("second-key")
        self.assertEqual(self._status("second-key")["state"], "waiting_idle")
        dt.cancel_attempt(
            "second-key",
            json.dumps({"format": dt.CANCEL_FORMAT, "request_id": "cancel-2"}).encode())
        self.assertEqual(self._status("second-key")["state"], "cancelled")
        receipt = json.loads(dt.attempt_receipt("second-key")[1])
        self._validate("receipt", receipt)
        self.assertEqual(receipt["outcome"], "cancelled")
        self.assertEqual(receipt["reason"]["code"], "cancel_requested")
        self.assertTrue(receipt["resource_release"]["confirmed"])
        self._wait("first-key", {"passed"})
        time.sleep(0.2)
        self.assertEqual(self._runs(), 1)

    def test_unconfirmed_install_release_holds_without_a_receipt(self):
        self.caps = runner.build_model_capabilities(
            self.manifests, rows=[_row()],
            preflight=lambda manifest: {"state": "not_installed"})
        self.runner = self._configure(install_model=lambda package_id, revision: {
            "started": True, "completed": False, "release_confirmed": False,
            "download_id": "dl-test-1", "verified_manifest_sha256": None,
            "reason": "the transfer is still running"})
        self._submit("hold-key")
        value = self._wait("hold-key", {"recovery_hold"})
        self.assertFalse(value["resource_release_confirmed"])
        self.assertIsNone(value["receipt_sha256"])
        with self.assertRaises(dt.Refused) as refusal:
            dt.attempt_receipt("hold-key")
        self.assertEqual(refusal.exception.code, "incomplete_evidence")
        self.assertEqual(self._runs(), 0)

    def test_a_completed_test_owned_install_continues_into_the_child(self):
        self.caps = runner.build_model_capabilities(
            self.manifests, rows=[_row()],
            preflight=lambda manifest: {"state": "not_installed"})
        self.runner = self._configure(install_model=lambda package_id, revision: {
            "started": True, "completed": True, "release_confirmed": True,
            "download_id": "dl-test-2", "verified_manifest_sha256": "c" * 64,
            "reason": None})
        self._submit("install-key")
        value = self._wait("install-key", {"passed"})
        receipt = json.loads(dt.attempt_receipt("install-key")[1])
        self.assertTrue(receipt["installation"]["required"])
        self.assertTrue(receipt["installation"]["completed"])
        self.assertEqual(receipt["installation"]["download_id"], "dl-test-2")
        self.assertEqual(self._runs(), 1)
        self.assertTrue(value["resource_release_confirmed"])

    def test_accepted_receipt_identity_survives_unavailable_updated_assets(self):
        request = self._request()
        with mock.patch.object(service, "_start_next"):
            self._submit("accepted-before-update", request)
        self.assertIsNotNone(dt.attempt("accepted-before-update")["receipt_context"])
        self.assertFalse(self._configure(assets_dir=self.tmp / "missing-assets")["configured"])
        service.recover()
        receipt = json.loads(dt.attempt_receipt("accepted-before-update")[1])
        self._validate("receipt", receipt)
        self.assertEqual(receipt["outcome"], "not_run")
        self.assertEqual(receipt["runner_sha256"], request["runner_sha256"])
        self.assertEqual(receipt["fixture_set_sha256"], request["fixture_set_sha256"])
        self.assertEqual(receipt["request_sha256"], hashlib.sha256(self._raw(request)).hexdigest())
        self.assertEqual(self._runs(), 0)

    def test_recover_settles_waiting_rows_not_run_and_never_reruns(self):
        raw = self._raw()
        request = dt.validate_request(json.loads(raw))
        dt.accept_attempt("waiting-key", raw, request, self.revision,
                          actor={"kind": "fleet", "node_id": None}, destination="node-a")
        result = service.recover()
        self.assertIn(dt.attempt("waiting-key")["attempt_id"], result["not_run"])
        self.assertEqual(self._status("waiting-key")["state"], "not_run")
        receipt = json.loads(dt.attempt_receipt("waiting-key")[1])
        self._validate("receipt", receipt)
        self.assertEqual(receipt["outcome"], "not_run")
        self.assertEqual(receipt["reason"]["code"], "restarted")
        self.assertTrue(receipt["resource_release"]["confirmed"])
        time.sleep(0.2)
        self.assertEqual(self._runs(), 0)

    def test_recover_stops_the_exact_recorded_child_and_retains_uncertain_release(self):
        raw = self._raw()
        request = dt.validate_request(json.loads(raw))
        row = dt.accept_attempt("running-key", raw, request, self.revision,
                                actor={"kind": "fleet", "node_id": None},
                                destination="node-a")
        dt.update_attempt("running-key", state="running", installation={
            "started": True, "completed": True, "download_id": "dl-finished",
            "verified_manifest_sha256": "c" * 64, "reason": None})
        attempt_dir = self.tmp / "data" / "developer-tests" / "attempts" / row["attempt_id"]
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "log.txt").write_bytes(b"retained redacted log")
        (attempt_dir / "child-identity.json").write_text(json.dumps(
            {"pid": 999_999, "pgid": 999_999, "create_time": 1.0}))
        service.recover()
        value = self._status("running-key")
        self.assertEqual(value["state"], "interrupted")
        self.assertTrue(value["resource_release_confirmed"])
        receipt = json.loads(dt.attempt_receipt("running-key")[1])
        self._validate("receipt", receipt)
        self.assertEqual(receipt["outcome"], "interrupted")
        self.assertEqual(receipt["reason"]["code"], "worker_lost")
        self.assertTrue(receipt["installation"]["started"])
        self.assertTrue(receipt["installation"]["completed"])
        self.assertEqual(receipt["installation"]["download_id"], "dl-finished")
        self.assertTrue(receipt["log"]["truncated"])
        self.assertEqual(base64.b64decode(dt.attempt("running-key")["log_base64"]),
                         b"retained redacted log")
        self.assertEqual(receipt["log"]["sha256"], hashlib.sha256(b"retained redacted log").hexdigest())
        self.assertEqual(self._runs(), 0)

        row = dt.accept_attempt("held-key", raw, dt.validate_request(json.loads(raw)),
                                self.revision, actor={"kind": "fleet", "node_id": None},
                                destination="node-a")
        dt.update_attempt("held-key", state="running")
        # No persisted identity is not proof of release, even if the stop primitive would say so.
        with mock.patch.object(service._runner, "process_identity", return_value=None), \
                mock.patch.object(service._runner, "stop_recorded",
                                  return_value=types.SimpleNamespace(confirmed=True)):
            service.recover()
        held = self._status("held-key")
        self.assertEqual(held["state"], "recovery_hold")
        self.assertFalse(held["resource_release_confirmed"])

    def test_recover_retains_hold_when_download_start_identity_was_not_saved(self):
        raw = self._raw()
        row = dt.accept_attempt("pending-download", raw, dt.validate_request(json.loads(raw)),
                                self.revision, actor={"kind": "fleet", "node_id": None},
                                destination="node-a")
        dt.update_attempt("pending-download", state="installing", install_start_pending=True)
        attempt_dir = self.tmp / "data" / "developer-tests" / "attempts" / row["attempt_id"]
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stage.json").write_text(json.dumps({"stage": "installing"}))
        admission.restore_before_production()
        result = service.recover()
        self.assertIn(row["attempt_id"], result["recovery_hold"])
        self.assertFalse(self._status("pending-download")["resource_release_confirmed"])
        self.assertEqual(admission.active_attempt(), row["attempt_id"])
        self.assertFalse(service._release_owned_download({
            "install_download_id": "missing-transfer", "install_download_owned": True}))
        self.assertEqual(self._runs(), 0)

    def test_recover_cancels_only_a_test_owned_download(self):
        from components.hub import model_downloads
        raw = self._raw()
        for key, owned in (("owned-key", True), ("shared-key", False)):
            row = dt.accept_attempt(key, raw, dt.validate_request(json.loads(raw)), self.revision,
                                    actor={"kind": "fleet", "node_id": None},
                                    destination="node-a")
            dt.update_attempt(key, state="running", install_download_id="dl-1",
                              install_download_owned=owned)
            attempt_dir = (self.tmp / "data" / "developer-tests" / "attempts"
                           / row["attempt_id"])
            attempt_dir.mkdir(parents=True, exist_ok=True)
            (attempt_dir / "child-identity.json").write_text(json.dumps(
                {"pid": 999_999, "pgid": 999_999, "create_time": 1.0}))
        with mock.patch.object(service._runner, "stop_recorded",
                               return_value=types.SimpleNamespace(confirmed=True)), \
                mock.patch.object(model_downloads, "status",
                                  return_value={"state": "running"}), \
                mock.patch.object(model_downloads, "cancel",
                                  return_value={"state": "cancelled"}) as cancel:
            service.recover()
        self.assertEqual(cancel.call_count, 1)
        self.assertEqual(cancel.call_args.args[0], "dl-1")
        self.assertEqual(self._status("owned-key")["state"], "interrupted")
        self.assertEqual(self._status("shared-key")["state"], "interrupted")

    def test_a_node_credential_cannot_relay_to_a_remote_node(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.middleware("http")
        async def actor(request: Request, call_next):
            request.state.fleet_actor = {"kind": "node", "node_id": "node-a"}
            return await call_next(request)

        old_ids = dt._local_ids
        dt._local_ids = lambda: ("local",)
        self.addCleanup(setattr, dt, "_local_ids", old_ids)
        dt.register_routes(app)
        client = TestClient(app)
        with mock.patch.object(dt, "_relay",
                               side_effect=AssertionError("must not relay")):
            response = client.get(
                "/api/hub/fleet/nodes/remote-node/developer-tests/capabilities")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["reason"]["code"], "unauthorized")

    def test_post_routes_reject_a_query_string(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.middleware("http")
        async def actor(request: Request, call_next):
            request.state.fleet_actor = {"kind": "fleet", "node_id": None}
            return await call_next(request)

        old_ids = dt._local_ids
        dt._local_ids = lambda: ("local",)
        self.addCleanup(setattr, dt, "_local_ids", old_ids)
        dt.register_routes(app)
        client = TestClient(app)
        response = client.post(
            "/api/hub/fleet/nodes/local/developer-tests?unexpected=1",
            content=b"{}", headers={"Idempotency-Key": "query-key"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason"]["code"], "invalid_request")

    def test_shutdown_stops_the_live_attempt_and_releases_its_lease(self):
        os.environ["FAKE_SLEEP"] = "5"
        self._submit("stop-key")
        self._wait("stop-key", {"running"})
        result = service.shutdown()
        self.assertEqual(result["stopped"], self._status("stop-key")["attempt_id"])
        self.assertTrue(result["release_confirmed"])
        self.assertEqual(self._status("stop-key")["state"], "interrupted")
        self.assertNotIn("local", broker._external_machine_leases)


if __name__ == "__main__":
    unittest.main()
