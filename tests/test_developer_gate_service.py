"""Focused service tests for the frozen developer-gate pilot.

No Hub, network, tool installation or real Library source is used. Every attempt, archive and
spool lives in a temporary directory; the runner, admission, metrics and inventory are exact
test fixtures supplied through the normal constructor, never through the wire.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import time
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_gate_contract as contract  # noqa: E402
from components.hub import developer_gate_runner as gate_runner  # noqa: E402
from components.hub import developer_gate_service as service_mod  # noqa: E402
from components.hub import developer_tests  # noqa: E402

NODE = "node-1"
CONTROLLER = "ctrl-1"
OBSERVED = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
PIN = {"id": "python", "version": "3.11.0", "manifest_sha256": "d" * 64}
TOOL_MANIFEST = {
    "format": "studio.tool-manifest.v1", "id": "tool-manifest-pilot",
    "revision": "tm-rev-1", "sha256": "e" * 64, "tools": [PIN],
    "provenance": "hub-packaged-official-hash-pinned",
}
RUNNER = {"id": "studio.app-gate-runner.v1", "revision": "runner-rev-1",
          "sha256": "a" * 64}
LIMITS = {
    "runtime_seconds": 60, "workspace_bytes": 1_000_000, "result_spool_bytes": 500_000,
    "shared_cache_bytes": 100_000, "minimum_free_bytes": 100_000,
    "toolchain_install_bytes": 50_000, "max_process_tree_rss_bytes": 2_000_000,
    "max_system_swap_growth_bytes": 0, "start_min_free_memory_bytes": 500_000,
}
BOUNDS = {
    "json_bytes": 1_000_000, "source_bytes": 5_000_000, "source_expanded_bytes": 10_000_000,
    "source_files": 1000, "result_bytes": 5_000_000, "log_bytes": 1_000_000,
    "workspace_bytes": 2_000_000, "result_spool_bytes": 1_000_000,
    "shared_cache_bytes": 500_000, "runtime_seconds": 120, "tool_prepare_seconds": 60,
    "toolchain_install_bytes": 100_000, "max_process_tree_rss_bytes": 4_000_000,
    "max_system_swap_growth_bytes": 0, "inventory_max_age_seconds": 300,
    "result_files": 100, "workspace_enforcement": "monitored_stop",
    "sample_interval_seconds": 0.5,
}


def _source_tar() -> tuple[bytes, dict]:
    buffer = io.BytesIO()
    payload = b"print('portable gate')\n"
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("main.py")
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    raw = buffer.getvalue()
    return raw, {
        "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        "expanded_bytes": len(payload), "file_count": 1, "commit": "c" * 40,
        "media_type": "application/x-tar",
    }


def _inventory(node: str = NODE, controller: str = CONTROLLER) -> dict:
    return {
        "format": "studio.developer-gate.inventory.v1", "node_id": node,
        "controller_id": controller, "observed_at": OBSERVED,
        "internal_physical_drive": {
            "device_id": "disk0", "internal": True, "physical": True,
            "size_bytes": 500_277_792_768, "source": "diskutil-info-plist",
            "workspace_backing_device_id": "disk0",
            "backing_source": "diskutil-info-plist", "observed_at": OBSERVED,
            "reason": None,
        },
        "free_bytes": 300_000_000_000,
        "toolchains": [{
            "id": "python", "pin": PIN, "available": True, "observed_at": OBSERVED,
            "installed_bytes": None, "provenance": "owned-toolchain-readback",
            "reason": None,
        }],
        "developer_gate_space": {
            "observed_at": OBSERVED, "workspace_bytes": 0, "result_spool_bytes": 0,
            "shared_cache_bytes": 0, "workspace_limit_bytes": 40_000_000_000,
            "result_spool_limit_bytes": 2_000_000_000,
            "shared_cache_limit_bytes": None, "cleanup_state": "none", "reason": None,
        },
    }


def _fixture() -> tuple[service_mod.GateAuthority, dict, bytes]:
    raw, source = _source_tar()
    gate = {
        "id": "gate-pilot-1", "revision": "gate-rev-1", "sha256": "f" * 64,
        "required_toolchains": [PIN], "source": source,
        "command_fixture_sha256": "1" * 64, "dependency_manifest_sha256": "2" * 64,
        "tool_manifest": TOOL_MANIFEST, "limits": dict(LIMITS),
        "budget_provenance": "requested", "check_ids": ["check-1"],
    }
    policy = {
        "format": "studio.developer-gate.policy.v1", "revision": "pol-rev-1",
        "minimum_internal_physical_bytes": 500_000_000_000,
        "allowed_targets": [{"controller_id": CONTROLLER, "node_id": NODE}],
        "excluded_targets": [],
    }
    request = {
        "format": "studio.developer-gate.request.v1", "run_id": "run-1",
        "test_id": "test-1", "runner": dict(RUNNER),
        "node_setting_revision": "set-rev-1",
        "admission_policy_revision": policy["revision"],
        "minimum_internal_physical_bytes": policy["minimum_internal_physical_bytes"],
        "requested_limits": dict(LIMITS), "gate": gate, "source": source,
        "tool_manifest": TOOL_MANIFEST,
    }
    authority = service_mod.GateAuthority(
        supported=True, runner=dict(RUNNER), gates=(gate,),
        tool_manifests=(TOOL_MANIFEST,),
        stop_proof={"revision": "sp-rev-1", "proof_sha256": "7" * 64,
                    "stop_latency_seconds": 0.2},
        policy=policy, bounds=dict(BOUNDS),
        setting={"enabled": True, "verified": True, "revision": "set-rev-1"},
        trusted_toolchains={"python": {"available": True,
                                       "executable": sys.executable,
                                       "version": "3.11.0"}},
        actual_toolchains=({"id": "python", "pin": PIN, "available": True,
                            "observed_at": OBSERVED, "installed_bytes": None,
                            "provenance": "owned-toolchain-readback",
                            "reason": None},),
    )
    return authority, request, raw


class FakeAdmission:
    def __init__(self) -> None:
        self.reserved: list[str] = []
        self.released: list[tuple[str, bool]] = []
        self.events: dict[str, object] = {}

    def reserve_app_gate(self, attempt_id, *, machine="local", resource_safe=None):
        self.reserved.append(attempt_id)
        self.events[attempt_id] = __import__("threading").Event()
        return f"developer-test:{attempt_id}"

    def release(self, attempt_id, *, confirmed):
        self.released.append((attempt_id, confirmed))
        return confirmed

    def stage_allowed(self, attempt_id):
        return True

    def cancel_requested(self, attempt_id):
        return False

    def cancel_event(self, attempt_id):
        return self.events.get(attempt_id)

    @staticmethod
    def owner_for(attempt_id):
        return f"developer-test:{attempt_id}"


class FakeMetrics:
    def available_bytes(self):
        return 16 * 1024 ** 3


class FakeBroker:
    def __init__(self) -> None:
        self.restored: list[tuple[str, str]] = []
        self.released: list[tuple[str, str]] = []

    def restore_external_machine(self, machine, owner):
        self.restored.append((machine, owner))

    def release_external_machine(self, machine, owner):
        self.released.append((machine, owner))


def fake_runner(**kwargs):
    kwargs["identity_recorder"]({"pid": os.getpid(), "pgid": os.getpid(),
                                 "create_time": 123.0})
    log = b"portable check passed\n"
    checks = [{
        "id": "check-1", "outcome": "passed", "exit_code": 0,
        "duration_seconds": 0.01,
        "log_sha256": hashlib.sha256(log).hexdigest(), "log_bytes": len(log),
        "reason": None,
    }]
    return gate_runner.GateRunResult(
        outcome="passed", exit_code=0, termination_signal=None, reason=None,
        checks=checks, check_logs={"check-1": log},
        process_release={"confirmed": True, "observed_at": OBSERVED,
                         "owned_processes_remaining": 0,
                         "proof": "owned-process-identities-exited"},
        started_at=OBSERVED, finished_at=OBSERVED, scope=gate_runner.SCOPE,
        peak_workspace_bytes=0, before=None)


class DeveloperGateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="studiohub-gate-service-")
        self.addCleanup(self.temp.cleanup)
        self.old_identities = developer_tests._identities
        developer_tests._identities = lambda: (NODE, CONTROLLER)
        self.addCleanup(setattr, developer_tests, "_identities", self.old_identities)
        self.disk = mock.patch.object(
            service_mod.shutil, "disk_usage",
            return_value=types.SimpleNamespace(free=400_000_000_000,
                                               total=500_277_792_768,
                                               used=100_277_792_768))
        self.disk.start()
        self.addCleanup(self.disk.stop)

    def _service(self, *, authority=None, runner=fake_runner, root_dir=None):
        authority = authority or _fixture()[0]
        return service_mod.DeveloperGateService(
            root_dir=root_dir or self.temp.name, authority=authority, runner=runner,
            metrics=FakeMetrics(), admission=FakeAdmission(), broker=FakeBroker(),
            inventory_provider=lambda node, controller: _inventory(node, controller))

    def _wait(self, service, key, states, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = service.status(key, CONTROLLER, NODE)
            if status["state"] in states:
                return status
            time.sleep(0.01)
        self.fail(f"attempt {key} did not reach {states}: {service.status(key, CONTROLLER, NODE)}")

    def test_unsupported_production_capabilities_and_submit_refuse(self) -> None:
        authority, request, _raw = _fixture()
        unsupported = service_mod.GateAuthority(
            supported=False, runner=authority.runner, policy=authority.policy,
            bounds=authority.bounds, reason=dict(service_mod.UNSUPPORTED_REASON))
        service = self._service(authority=unsupported)
        caps = service.capabilities()
        self.assertFalse(caps["supported"])
        self.assertEqual(caps["reason"]["code"], "unsupported")
        self.assertEqual(caps["gates"], [])
        self.assertEqual(caps["tool_manifests"], [])
        self.assertEqual(caps["concurrency"], 1)
        with self.assertRaises(service_mod.GateServiceError) as refused:
            service.accept(contract.canonical_json(request).encode(), "key-1",
                           CONTROLLER, NODE, {"kind": "fleet"}, {"credential": "fleet-header"})
        self.assertEqual(refused.exception.status, 503)
        self.assertEqual(refused.exception.code, "unsupported")

    def test_source_run_result_and_ack_cleanup(self) -> None:
        authority, request, raw = _fixture()
        service = self._service(authority=authority)
        accepted = service.accept(contract.canonical_json(request).encode(), "key-1",
                                  CONTROLLER, NODE, {"kind": "fleet"},
                                  {"credential": "fleet-header"})
        self.assertEqual(accepted["state"], "waiting_source")
        uploaded = service.source("key-1", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
        self.assertNotEqual(uploaded["state"], "waiting_source")
        status = self._wait(service, "key-1", {"passed", "failed", "not_run"})
        self.assertEqual(status["state"], "passed")
        self.assertIsNotNone(status["result"])
        path, descriptor = service.result("key-1", CONTROLLER, NODE)
        self.assertTrue(path.is_file())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         descriptor["sha256"])
        # No ACK yet: the workspace and spool are preserved.
        rec = service._store.get_attempt_by_key("key-1")
        self.assertFalse(rec["workspace_removed"])
        ack = {
            "format": contract.ACK_FORMAT, "request_id": "ack-1",
            "acceptance": rec["acceptance"], "result": descriptor,
        }
        acked = service.ack("key-1", contract.canonical_json(ack).encode(),
                            CONTROLLER, NODE)
        self.assertTrue(acked["result_acknowledged"])
        self.assertTrue(acked["workspace_removed"])
        self.assertIsNone(service._store.get_result_path(rec["acceptance"]["attempt_id"]))
        with self.assertRaises(service_mod.GateServiceError):
            service.result("key-1", CONTROLLER, NODE)

    def test_same_key_same_request_reconciles_and_different_request_refuses(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        body = contract.canonical_json(request).encode()
        first = service.accept(body, "key-1", CONTROLLER, NODE,
                               {"kind": "fleet"}, {"credential": "fleet-header"})
        again = service.accept(body, "key-1", CONTROLLER, NODE,
                               {"kind": "fleet"}, {"credential": "fleet-header"})
        self.assertEqual(first["acceptance"]["attempt_id"],
                         again["acceptance"]["attempt_id"])
        changed = dict(request)
        changed["run_id"] = "run-2"
        with self.assertRaises(service_mod.GateServiceError) as refused:
            service.accept(contract.canonical_json(changed).encode(), "key-1",
                           CONTROLLER, NODE, {"kind": "fleet"},
                           {"credential": "fleet-header"})
        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(refused.exception.code, "conflict")

    def test_source_rejects_a_body_that_does_not_match_the_pin(self) -> None:
        authority, request, raw = _fixture()
        service = self._service(authority=authority)
        service.accept(contract.canonical_json(request).encode(), "key-1",
                       CONTROLLER, NODE, {"kind": "fleet"},
                       {"credential": "fleet-header"})
        with self.assertRaises(service_mod.GateServiceError) as refused:
            service.source("key-1", io.BytesIO(raw + b"x"), len(raw) + 1,
                           CONTROLLER, NODE)
        self.assertEqual(refused.exception.status, 400)
        rec = service._store.get_attempt_by_key("key-1")
        self.assertEqual(rec["state"], "waiting_source")
        self.assertNotIn("source_completed", rec.get("metadata") or {})

    def test_missing_trusted_tool_preparation_refuses_before_launch(self) -> None:
        authority, request, raw = _fixture()
        authority = service_mod.GateAuthority(
            supported=True, runner=authority.runner, gates=authority.gates,
            tool_manifests=authority.tool_manifests, stop_proof=authority.stop_proof,
            policy=authority.policy, bounds=authority.bounds, setting=authority.setting,
            trusted_toolchains={}, actual_toolchains=())
        service = self._service(authority=authority)
        service.accept(contract.canonical_json(request).encode(), "key-1",
                       CONTROLLER, NODE, {"kind": "fleet"},
                       {"credential": "fleet-header"})
        service.source("key-1", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
        status = self._wait(service, "key-1", {"failed", "not_run", "recovery_hold"})
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["reason"]["code"], "toolchain_unavailable")
        self.assertEqual(service._admission.reserved, [])

    def test_recovery_without_identity_retains_the_physical_hold(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        accepted = service.accept(contract.canonical_json(request).encode(), "key-1",
                                  CONTROLLER, NODE, {"kind": "fleet"},
                                  {"credential": "fleet-header"})
        attempt_id = accepted["acceptance"]["attempt_id"]
        service._store.update_state(attempt_id, "waiting_idle")
        service._store.update_state(attempt_id, "preparing_toolchains")
        service._store.update_state(attempt_id, "running")
        restored: list[tuple[str, str]] = []
        result = service.recover()
        self.assertIn(attempt_id, result["recovery_hold"])
        self.assertEqual(service._broker.restored,
                         [("local", f"developer-test:{attempt_id}")])
        self.assertEqual(service._broker.released, [])
        rec = service._store.get_attempt_by_id(attempt_id)
        self.assertEqual(rec["state"], "recovery_hold")
        self.assertTrue(service._store.get_workspace_path(attempt_id).is_dir())

    def test_recovery_with_confirmed_identity_but_no_result_retains_workspace(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        accepted = service.accept(contract.canonical_json(request).encode(), "key-1",
                                  CONTROLLER, NODE, {"kind": "fleet"},
                                  {"credential": "fleet-header"})
        attempt_id = accepted["acceptance"]["attempt_id"]
        service._store.update_state(attempt_id, "waiting_idle")
        service._store.update_state(attempt_id, "preparing_toolchains")
        service._store.update_state(attempt_id, "running")
        service._store.update_metadata(attempt_id, {
            "child_identity": {"pid": 1234, "pgid": 1234, "create_time": 1.0}})
        proof = types.SimpleNamespace(confirmed=True)
        with mock.patch.object(service_mod.gate_runner, "stop_exact_identity",
                               return_value=proof):
            result = service.recover()
        self.assertIn(attempt_id, result["recovery_hold"])
        self.assertEqual(len(service._broker.released), 1)
        rec = service._store.get_attempt_by_id(attempt_id)
        self.assertEqual(rec["state"], "recovery_hold")
        self.assertTrue(service._store.get_workspace_path(attempt_id).is_dir())

    def test_runtime_recovery_skips_live_and_waiting_attempts(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        body = contract.canonical_json(request).encode()
        actor = {"kind": "fleet"}
        source = service.accept(body, "key-source", CONTROLLER, NODE, actor,
                                {"credential": "fleet-header"})
        idle = service.accept(body, "key-idle", CONTROLLER, NODE, actor,
                              {"credential": "fleet-header"})
        running = service.accept(body, "key-run", CONTROLLER, NODE, actor,
                                 {"credential": "fleet-header"})
        idle_id = idle["acceptance"]["attempt_id"]
        run_id = running["acceptance"]["attempt_id"]
        service._store.update_state(idle_id, "waiting_idle")
        service._store.update_state(run_id, "waiting_idle")
        service._store.update_state(run_id, "preparing_toolchains")
        service._store.update_state(run_id, "running")
        service._active_attempt = run_id
        result = service.recover_runtime()
        self.assertEqual(result, {"interrupted": [], "recovery_hold": [], "not_run": []})
        self.assertEqual(service._store.get_attempt_by_id(
            source["acceptance"]["attempt_id"])["state"], "waiting_source")
        self.assertEqual(service._store.get_attempt_by_id(idle_id)["state"], "waiting_idle")
        self.assertEqual(service._store.get_attempt_by_id(run_id)["state"], "running")

    def test_restart_settles_no_child_attempts_not_run_with_ack_cleanup(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        body = contract.canonical_json(request).encode()
        actor = {"kind": "fleet"}
        source = service.accept(body, "key-source", CONTROLLER, NODE, actor,
                                {"credential": "fleet-header"})
        idle = service.accept(body, "key-idle", CONTROLLER, NODE, actor,
                              {"credential": "fleet-header"})
        idle_id = idle["acceptance"]["attempt_id"]
        service._store.update_state(idle_id, "waiting_idle")
        result = service.recover()
        self.assertEqual(sorted(result["not_run"]),
                         sorted([source["acceptance"]["attempt_id"], idle_id]))
        for attempt_id in (source["acceptance"]["attempt_id"], idle_id):
            rec = service._store.get_attempt_by_id(attempt_id)
            self.assertEqual(rec["state"], "not_run")
            self.assertEqual(rec["metadata"]["process_release"]["proof"], "never_started")
        path, descriptor = service.result("key-idle", CONTROLLER, NODE)
        with tarfile.open(path, "r") as archive:
            manifest = json.loads(archive.extractfile("manifest.json").read())
        self.assertIsNone(manifest["exit_code"])
        self.assertIsNone(manifest["started_at"])
        rec = service._store.get_attempt_by_id(idle_id)
        ack = {"format": contract.ACK_FORMAT, "request_id": "ack-1",
               "acceptance": rec["acceptance"], "result": descriptor}
        acked = service.ack("key-idle", contract.canonical_json(ack).encode(),
                            CONTROLLER, NODE)
        self.assertTrue(acked["workspace_removed"])

    def test_runner_failure_reloads_durable_identity_before_exact_stop(self) -> None:
        authority, request, raw = _fixture()
        identity = {"pid": 4321, "pgid": 4321, "create_time": 9.0}

        def failing_runner(**kwargs):
            kwargs["identity_recorder"](identity)
            raise RuntimeError("runner exploded")

        service = self._service(authority=authority, runner=failing_runner)
        service.accept(contract.canonical_json(request).encode(), "key-1",
                       CONTROLLER, NODE, {"kind": "fleet"},
                       {"credential": "fleet-header"})
        with mock.patch.object(service_mod.gate_runner, "stop_exact_identity",
                               return_value=types.SimpleNamespace(confirmed=True)) as stop:
            service.source("key-1", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
            status = self._wait(service, "key-1", {"failed", "recovery_hold"})
        self.assertEqual(status["state"], "failed")
        stop.assert_called_once()
        self.assertEqual(stop.call_args.args[0], identity)

    def test_insufficient_resources_at_launch_terminalizes_without_process_or_rerun(self) -> None:
        authority, request, raw = _fixture()
        service = self._service(authority=authority)
        service.accept(contract.canonical_json(request).encode(), "key-1",
                       CONTROLLER, NODE, {"kind": "fleet"},
                       {"credential": "fleet-header"})
        service._metrics = types.SimpleNamespace(available_bytes=lambda: 0)
        service.source("key-1", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
        status = self._wait(service, "key-1", {"failed", "not_run", "recovery_hold"})
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["reason"]["code"], "resource_unknown")
        self.assertIsNotNone(status["result"])
        self.assertEqual(service._admission.reserved, [])
        deadline = time.monotonic() + 2.0
        while service._active_attempt is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(service._active_attempt)
        self.assertFalse(service._worker.is_alive())
        self.assertEqual(service._store.get_attempt_by_key("key-1")["state"], "failed")

    def test_runner_failure_retains_hold_when_release_is_not_confirmed(self) -> None:
        authority, request, raw = _fixture()
        identity = {"pid": 4321, "pgid": 4321, "create_time": 9.0}

        def failing_runner(**kwargs):
            kwargs["identity_recorder"](identity)
            raise RuntimeError("runner exploded")

        service = self._service(authority=authority, runner=failing_runner)
        service._admission.release = lambda attempt_id, *, confirmed: False
        service.accept(contract.canonical_json(request).encode(), "key-1",
                       CONTROLLER, NODE, {"kind": "fleet"},
                       {"credential": "fleet-header"})
        with mock.patch.object(service_mod.gate_runner, "stop_exact_identity",
                               return_value=types.SimpleNamespace(confirmed=True)):
            service.source("key-1", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
            status = self._wait(service, "key-1", {"failed", "recovery_hold"})
        self.assertEqual(status["state"], "recovery_hold")
        self.assertIsNone(service._store.get_attempt_by_key("key-1")["result"])

    def test_exact_replay_returns_original_acceptance_after_setting_off(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        body = contract.canonical_json(request).encode()
        actor = {"kind": "fleet"}
        transport = {"credential": "fleet-header"}
        first = service.accept(body, "key-1", CONTROLLER, NODE, actor, transport)
        service._authority = replace(
            authority, setting={"enabled": False, "verified": True,
                                "revision": "set-rev-1"})
        service._inventory_provider = lambda node, controller: _inventory(node="node-2")
        replay = service.accept(body, "key-1", CONTROLLER, NODE, actor, transport)
        self.assertEqual(replay["acceptance"]["attempt_id"],
                         first["acceptance"]["attempt_id"])
        changed = dict(request)
        changed["run_id"] = "run-2"
        with self.assertRaises(service_mod.GateServiceError) as conflict:
            service.accept(contract.canonical_json(changed).encode(), "key-1",
                           CONTROLLER, NODE, actor, transport)
        self.assertEqual(conflict.exception.status, 409)
        with self.assertRaises(service_mod.GateServiceError) as other_actor:
            service.accept(body, "key-1", CONTROLLER, NODE,
                           {"kind": "node", "node_id": NODE}, transport)
        self.assertEqual(other_actor.exception.status, 409)
        with self.assertRaises(service_mod.GateServiceError) as fresh:
            service.accept(body, "key-2", CONTROLLER, NODE, actor, transport)
        self.assertEqual(fresh.exception.code, "disabled")

    def test_execute_revalidates_policy_before_launch(self) -> None:
        authority, request, raw = _fixture()
        service = self._service(authority=authority)
        service.accept(contract.canonical_json(request).encode(), "key-1",
                       CONTROLLER, NODE, {"kind": "fleet"},
                       {"credential": "fleet-header"})
        policy = dict(authority.policy)
        policy["revision"] = "pol-rev-2"
        service._authority = replace(authority, policy=policy)
        service.source("key-1", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
        status = self._wait(service, "key-1", {"failed", "recovery_hold", "not_run"})
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["reason"]["code"], "conflict")
        self.assertEqual(service._admission.reserved, [])

    def test_execute_refuses_stale_or_ineligible_inventory_before_launch(self) -> None:
        authority, request, raw = _fixture()
        service = self._service(authority=authority)
        service.accept(contract.canonical_json(request).encode(), "key-1",
                       CONTROLLER, NODE, {"kind": "fleet"},
                       {"credential": "fleet-header"})
        stale = _inventory()
        stale["observed_at"] = "2020-01-01T00:00:00Z"
        service._inventory_provider = lambda node, controller: stale
        service.source("key-1", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
        status = self._wait(service, "key-1", {"failed", "recovery_hold", "not_run"})
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["reason"]["code"], "inventory_stale")
        self.assertEqual(service._admission.reserved, [])

        other_root = tempfile.TemporaryDirectory(prefix="studiohub-gate-service-other-")
        self.addCleanup(other_root.cleanup)
        other = self._service(authority=authority, root_dir=other_root.name)
        other.accept(contract.canonical_json(request).encode(), "key-2",
                     CONTROLLER, NODE, {"kind": "fleet"},
                     {"credential": "fleet-header"})
        ineligible = _inventory()
        ineligible["internal_physical_drive"]["internal"] = False
        other._inventory_provider = lambda node, controller: ineligible
        other.source("key-2", io.BytesIO(raw), len(raw), CONTROLLER, NODE)
        status = self._wait(other, "key-2", {"failed", "recovery_hold", "not_run"})
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["reason"]["code"], "physical_ineligible")
        self.assertEqual(other._admission.reserved, [])


    def test_accept_binds_inventory_identity_and_freshness(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        body = contract.canonical_json(request).encode()
        actor = {"kind": "fleet"}
        transport = {"credential": "fleet-header"}
        wrong_node = _inventory(node="node-2")
        stale_drive = _inventory()
        stale_drive["internal_physical_drive"]["observed_at"] = "2020-01-01T00:00:00Z"
        future_drive = _inventory()
        future_drive["internal_physical_drive"]["observed_at"] = "2030-01-01T00:00:00Z"
        future_inventory = _inventory()
        future_inventory["observed_at"] = "2030-01-01T00:00:00Z"
        cases = ((wrong_node, "conflict"), (stale_drive, "inventory_stale"),
                 (future_drive, "inventory_stale"), (future_inventory, "inventory_stale"))
        for index, (inventory, code) in enumerate(cases):
            service._inventory_provider = (
                lambda node, controller, value=inventory: value)
            with self.assertRaises(service_mod.GateServiceError) as refused:
                service.accept(body, f"key-bad-{index}", CONTROLLER, NODE, actor, transport)
            self.assertEqual(refused.exception.code, code)
        self.assertEqual(service._store.list_attempts(), [])

    def test_accept_refuses_free_or_external_drive_before_workspace(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        body = contract.canonical_json(request).encode()
        actor = {"kind": "fleet"}
        transport = {"credential": "fleet-header"}
        low = _inventory()
        low["free_bytes"] = 1000
        service._inventory_provider = lambda node, controller: low
        with self.assertRaises(service_mod.GateServiceError) as refused:
            service.accept(body, "key-low", CONTROLLER, NODE, actor, transport)
        self.assertEqual(refused.exception.code, "resource_unknown")
        external = _inventory()
        external["internal_physical_drive"]["internal"] = False
        service._inventory_provider = lambda node, controller: external
        with self.assertRaises(service_mod.GateServiceError) as refused:
            service.accept(body, "key-ext", CONTROLLER, NODE, actor, transport)
        self.assertEqual(refused.exception.code, "physical_ineligible")
        self.assertEqual(service._store.list_attempts(), [])
        self.assertEqual(list(service._store.workspaces_dir.iterdir()), [])

    def test_result_persist_requires_the_live_free_floor(self) -> None:
        authority, request, _raw = _fixture()
        service = self._service(authority=authority)
        accepted = service.accept(contract.canonical_json(request).encode(), "key-1",
                                  CONTROLLER, NODE, {"kind": "fleet"},
                                  {"credential": "fleet-header"})
        rec = service._store.get_attempt_by_id(accepted["acceptance"]["attempt_id"])
        result = gate_runner.GateRunResult(
            outcome="not_run", exit_code=None, termination_signal=None,
            reason={"code": "floor", "detail": "free-floor regression"}, checks=[],
            check_logs={},
            process_release={"confirmed": True, "observed_at": OBSERVED,
                             "owned_processes_remaining": 0, "proof": "never_started"},
            started_at=None, finished_at=OBSERVED, scope=gate_runner.SCOPE,
            peak_workspace_bytes=0, before=None)
        needed = 1000
        floor = (max(int(request["requested_limits"]["minimum_free_bytes"]),
                     service_mod.gate_runner.MINIMUM_FREE_BYTES) + 2 * needed)
        # Room for the archive itself, but below the live floor: must refuse.
        with mock.patch.object(service_mod.DeveloperGateService, "_tar_size",
                               return_value=needed), \
                mock.patch.object(service_mod.shutil, "disk_usage",
                                  return_value=types.SimpleNamespace(
                                      free=floor - 1, total=1, used=1)):
            with self.assertRaises(service_mod.GateServiceError) as refused:
                service._persist_result(rec, request, result)
        self.assertEqual(refused.exception.code, "resource_unknown")
        self.assertIn("live free floor", refused.exception.detail)


if __name__ == "__main__":
    unittest.main()
