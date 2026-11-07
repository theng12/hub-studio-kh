"""Focused regressions for producer reference policy on the peer surface.

The raw worker catalog used to lose the r5 inventory on its way to a controller. These tests pin
the annotation the proxy adds (one producer snapshot per local row, the same identity and host
total the aggregate catalog uses, nothing for a remote worker) and the peer Image edit door,
which derives the actual reference count from the multipart body and refuses against this node's
own current producer snapshot before its worker accepts the work. The RAM write validation matrix
pins that a present invalid number is named as a bad value before the pair/reset shape is read.
No Hub app, real network, filesystem model library or inference is used.
"""
from __future__ import annotations

import asyncio
import atexit
import copy
import gc
import json
import math
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

import httpx
from fastapi import FastAPI, File, Form, UploadFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_ROOT = tempfile.TemporaryDirectory(prefix="studiohub-peer-policy-")
atexit.register(_ROOT.cleanup)
os.environ.setdefault("STUDIOHUB_DATA_DIR", _ROOT.name)
_WORKERS = Path(_ROOT.name) / "workers.json"
_WORKERS.write_text(json.dumps([]))
os.environ.setdefault("STUDIOHUB_WORKERS_FILE", str(_WORKERS))

from components.hub import standalone  # noqa: E402
from components.hub.backend import broker, fleet_nodes, memory_admission, monitor as monitor_module  # noqa: E402

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"
ROW = {"repo": MODEL, "min_total_memory_gb": 8.0}
TIER_16 = 16 * 1024 ** 3
WORKER = {"id": "image-1", "modality": "image", "machine": "local"}
REMOTE = {"id": "image-remote", "modality": "image", "machine": "node-b"}


class FakeMonitor:
    def __init__(self, models: list[dict], status: dict | None = None):
        self._models = models
        self.status = status or {}

    def cached_aggregate_catalog(self) -> dict:
        return {"models": self._models}


def catalog_bytes(*rows: dict) -> bytes:
    return json.dumps({"models": list(rows)}).encode()


def multipart_field(content: bytes, content_type: str, name: str):
    """The JSON value of one forwarded multipart text field, or ``None`` when it is absent."""
    boundary = content_type.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    marker = b'name="' + name.encode() + b'"'
    for chunk in content.split(b"--" + boundary.encode())[1:]:
        headers, _, rest = chunk.partition(b"\r\n\r\n")
        if marker in headers:
            return json.loads(rest)
    return None


def multipart_body(parts: list[tuple[str, bytes, str | None, str | None]],
                   boundary: str) -> bytes:
    """A raw multipart body in the exact part order given: (name, value, filename, type)."""
    out = b""
    for name, value, filename, content_type in parts:
        out += b"--" + boundary.encode() + b"\r\n"
        out += b'Content-Disposition: form-data; name="' + name.encode() + b'"'
        if filename is not None:
            out += b'; filename="' + filename.encode() + b'"\r\nContent-Type: '
            out += (content_type or "application/octet-stream").encode()
        out += b"\r\n\r\n" + value + b"\r\n"
    return out + b"--" + boundary.encode() + b"--\r\n"


def request_for(raw: bytes, content_type: str):
    """A Starlette Request over one fixed body, as the proxy receives it."""
    from starlette.requests import Request
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/api/generate/edit",
                    "headers": [(b"content-type", content_type.encode())],
                    "query_string": b""}, receive)


async def parsed_form(raw: bytes, content_type: str):
    """Parse a forwarded body with Starlette's real multipart parser.

    A naive ``split(b"--boundary")`` hides a missing CRLF between a delimiter and the next
    part's headers; this parser is the one the worker uses, so it is the one that must agree.
    """
    request = request_for(raw, content_type)
    form = await request.form()
    try:
        fields: dict[str, str] = {}
        files: list[tuple[str, str, bytes]] = []
        for key, value in form.multi_items():
            if isinstance(value, str):
                fields[key] = value  # last wins, exactly as the worker reads it
            else:
                files.append((key, value.filename, await value.read()))
        return fields, files
    finally:
        await form.close()


def worker_signature_app():
    """The exact Form/File signature of the Image worker's edit route."""
    app = FastAPI()

    @app.post("/api/generate/edit")
    def submit_edit(prompt: str = Form(...), repo: str = Form(...),
                    images: list[UploadFile] | None = File(default=None),
                    reference_policy: str | None = Form(default=None)) -> dict:
        return {"repo": repo, "prompt": prompt,
                "images": [upload.filename for upload in images or []],
                "policy": reference_policy}

    return app


class PeerCatalogAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Path(tempfile.mkdtemp(prefix="studiohub-peer-store-")) / \
            "memory_admission_overrides.json"
        self.addCleanup(lambda: self.settings.parent.rmdir() if self.settings.parent.exists()
                        else None)
        patches = [
            mock.patch.object(memory_admission, "SETTINGS_FILE", self.settings),
            mock.patch.object(monitor_module, "_local_node_id", lambda: "machine-a"),
            mock.patch.object(monitor_module, "_local_total_memory_bytes", lambda: TIER_16),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)

    def snapshot(self) -> dict:
        return monitor_module._memory_admission_inventory(
            MODEL, ROW, node_id="machine-a", total_memory_bytes=TIER_16)

    def annotate(self, monitor, worker, content, content_type="application/json"):
        return standalone._annotate_worker_catalog(monitor, worker, content, content_type)

    def test_a_local_row_carries_the_same_snapshot_as_the_aggregate_read(self) -> None:
        aggregate = {"repo": MODEL, "hub_studio": WORKER["id"],
                     "hub_memory_admission": self.snapshot()}
        answer = self.annotate(FakeMonitor([aggregate]), WORKER,
                               catalog_bytes({"repo": MODEL, "min_total_memory_gb": 8}))
        row = json.loads(answer)["models"][0]
        self.assertEqual(row["hub_memory_admission"], aggregate["hub_memory_admission"])
        self.assertEqual(row["hub_memory_admission"]["node_id"], "machine-a")
        self.assertEqual(row["hub_memory_admission"]["revision"],
                         self.snapshot()["revision"])

    def test_an_aggregate_miss_uses_the_same_identity_and_host_total(self) -> None:
        expected = monitor_module._memory_admission_inventory(
            MODEL, memory_admission.publish_floor(dict(ROW), 16.0),
            node_id="machine-a", total_memory_bytes=TIER_16)
        monitor = FakeMonitor([], {"image-1": {"health": {"memory": {"total_gb": 16.0}}}})
        row = json.loads(self.annotate(
            monitor, WORKER, catalog_bytes({"repo": MODEL, "min_total_memory_gb": 8}))
        )["models"][0]
        self.assertEqual(row["hub_memory_admission"], expected)

    def test_a_remote_worker_row_is_never_annotated_with_this_macs_policy(self) -> None:
        content = catalog_bytes({"repo": MODEL, "min_total_memory_gb": 8})
        self.assertEqual(self.annotate(FakeMonitor([]), REMOTE, content), content)
        row = json.loads(content)["models"][0]
        self.assertNotIn("hub_memory_admission", row)

    def test_non_catalog_and_non_json_reads_are_untouched(self) -> None:
        content = catalog_bytes({"repo": MODEL})
        self.assertEqual(self.annotate(FakeMonitor([]), WORKER, content, "text/plain"), content)
        self.assertEqual(self.annotate(FakeMonitor([]), WORKER, b"not json"), b"not json")
        self.assertEqual(self.annotate(FakeMonitor([]), WORKER, b'{"models": "x"}'),
                         b'{"models": "x"}')


class PeerReferenceDoorTests(unittest.TestCase):
    """The peer edit door resolves this node's own policy and never trusts the caller's."""

    def setUp(self) -> None:
        self.settings = Path(tempfile.mkdtemp(prefix="studiohub-peer-door-")) / \
            "memory_admission_overrides.json"
        self.addCleanup(lambda: self.settings.parent.rmdir() if self.settings.parent.exists()
                        else None)
        patches = [
            mock.patch.object(memory_admission, "SETTINGS_FILE", self.settings),
            mock.patch.object(monitor_module, "_local_node_id", lambda: "machine-a"),
            mock.patch.object(broker, "_local_node_id", lambda: "machine-a"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)
        self.worker = {"id": "image", "modality": "image", "machine": "local"}

    def snapshot(self, total_bytes: int = TIER_16, node_id: str = "machine-a") -> dict:
        return monitor_module._memory_admission_inventory(
            MODEL, ROW, node_id=node_id, total_memory_bytes=total_bytes)

    def monitor(self, snapshot: dict | None) -> "PolicyMonitor":
        rows = ([{"repo": MODEL, "hub_studio": "image", "hub_memory_admission": snapshot}]
                if snapshot is not None else [])
        monitor = PolicyMonitor(rows)
        patch = mock.patch.object(broker, "_packaged_monitor", monitor)
        patch.start()
        self.addCleanup(patch.stop)
        return monitor

    def policy(self, count: int, snapshot: dict | None = None) -> dict:
        return broker._validated_reference_policy(
            snapshot or self.snapshot(), self.worker, MODEL, None, "edit", count)

    def refusal(self, count: int, supplied=broker._REFERENCE_POLICY_ABSENT):
        return standalone._peer_reference_refusal(self.worker, MODEL, count, supplied)

    def test_a_generic_and_an_exact_count_are_allowed(self) -> None:
        self.monitor(self.snapshot())
        self.assertIsNone(self.refusal(2))
        self.assertIsNone(self.refusal(2, self.policy(2)))
        self.assertEqual(self.policy(2)["reference_count"], 2)
        self.assertIsNone(self.refusal(0))
        generic = self.policy(0)
        self.assertIsNone(generic["reference_count"])
        self.assertIsNone(self.refusal(0, generic))

    def test_a_node_that_publishes_no_policy_keeps_the_older_absent_path(self) -> None:
        self.monitor(None)
        self.assertIsNone(self.refusal(2))
        refused = self.refusal(2, self.policy(2))
        self.assertEqual(refused["code"], "REFERENCE_POLICY_UNKNOWN")

    def test_an_off_policy_is_refused_even_with_a_permissive_caller_field(self) -> None:
        self.monitor(self.snapshot(total_bytes=8 * 1024 ** 3))
        permissive = {"model": MODEL, "mode": "edit", "node_id": "machine-a",
                      "physical_tier": "8", "reference_count": 2,
                      "revision": "a" * 64,
                      "effective": {"allowed": True, "max_reference_images": 99,
                                    "swap_allowance_gb": 0}}
        refused = self.refusal(2, permissive)
        self.assertEqual(refused["code"], "REFERENCE_POLICY_REFUSED")
        self.assertIn("refuses reference images", refused["detail"])

    def test_an_unknown_policy_is_refused(self) -> None:
        snapshot = copy.deepcopy(self.snapshot())
        for row in snapshot["reference"]["edit"]["rows"]:
            row["effective"]["allowed"] = None
        self.monitor(snapshot)
        refused = self.refusal(2)
        self.assertEqual(refused["code"], "REFERENCE_POLICY_REFUSED")
        self.assertIn("unknown", refused["detail"])

    def test_a_caller_policy_that_differs_from_this_nodes_snapshot_is_a_mismatch(self) -> None:
        self.monitor(self.snapshot())
        supplied = copy.deepcopy(self.policy(2))
        supplied["effective"]["max_reference_images"] = 99
        refused = self.refusal(2, supplied)
        self.assertEqual(refused["code"], "REFERENCE_POLICY_MISMATCH")

    def test_a_policy_for_another_node_is_refused(self) -> None:
        self.monitor(self.snapshot(node_id="machine-b"))
        refused = self.refusal(2)
        self.assertEqual(refused["code"], "REFERENCE_POLICY_REFUSED")
        self.assertIn("selected destination", refused["detail"])

    def test_an_unreadable_catalog_is_unknown_not_an_absent_annotation(self) -> None:
        monitor = PolicyMonitor([])
        monitor.cached_aggregate_catalog = mock.Mock(side_effect=OSError("unreadable"))
        patch = mock.patch.object(broker, "_packaged_monitor", monitor)
        patch.start()
        self.addCleanup(patch.stop)
        refused = self.refusal(2)
        self.assertEqual(refused["code"], "REFERENCE_POLICY_UNKNOWN")

    def test_an_upload_over_the_spool_limit_is_closed_by_the_door(self) -> None:
        from starlette.datastructures import FormData
        self.monitor(self.snapshot())
        closed = []
        original_close = FormData.close

        async def close(form) -> None:
            closed.append(True)
            await original_close(form)

        patch = mock.patch.object(FormData, "close", close)
        patch.start()
        self.addCleanup(patch.stop)
        boundary = "PeerLargeUpload"
        content_type = f"multipart/form-data; boundary={boundary}"
        raw = multipart_body([
            ("repo", MODEL.encode(), None, None),
            ("images", b"z" * (1024 * 1024 + 1), "big.bin", "application/octet-stream"),
        ], boundary)
        request = request_for(raw, content_type)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            refusal, forwarded = asyncio.run(standalone._peer_reference_door(
                self.worker, request, "api/generate/edit", content_type))
            gc.collect()
        self.assertIsNone(refusal)
        self.assertIsNotNone(forwarded)
        self.assertTrue(closed, "the door must close the parsed FormData")
        self.assertEqual([w for w in caught if issubclass(w.category, ResourceWarning)], [])


class PolicyMonitor:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.status: dict = {}

    def cached_aggregate_catalog(self) -> dict:
        return {"models": self.rows}


class PeerReferenceProxyTests(unittest.TestCase):
    """Drive the real ASGI proxy: the edit door refuses before the worker, and forwards intact."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        self.app = standalone.create_app(start_background=False)
        self.monitor = self.app.state.monitor
        self.monitor.registry = [{
            "id": "image", "modality": "image", "machine": "local", "local": True,
            "base_url": "http://127.0.0.1:1", "hub_token": "worker-token",
            "managed_output_roots": [str(Path(_ROOT.name) / "out")],
        }]
        self.monitor.status = {"image": {"health": {"memory": {"total_gb": 16.0}}}}
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.snapshot = monitor_module._memory_admission_inventory(
            MODEL, ROW, node_id="machine-a", total_memory_bytes=TIER_16)
        self.monitor.cached_aggregate_catalog = lambda: {"models": [{
            "repo": MODEL, "hub_studio": "image",
            "hub_memory_admission": self.snapshot}]}
        self.fake = FakeWorkerClient()
        self.monitor._client = self.fake
        patches = [
            mock.patch.object(standalone, "_peer_admission_refusal", lambda *a, **k: None),
            mock.patch.object(standalone, "_reserve_peer_machine", lambda *a, **k: "peer:test"),
            mock.patch.object(standalone, "_settle_peer_lease", lambda *a, **k: None),
            mock.patch.object(broker, "in_maintenance", lambda *a, **k: False),
            mock.patch.object(broker, "higher_priority_work_waiting", _never_waiting),
            mock.patch.object(monitor_module, "_local_node_id", lambda: "machine-a"),
            mock.patch.object(broker, "_local_node_id", lambda: "machine-a"),
            mock.patch.object(memory_admission, "SETTINGS_FILE",
                              Path(_ROOT.name) / "memory_admission_overrides.json"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)
        from components.hub.backend import auth
        self.token = auth.load_token()

    def edit(self, count: int, policy: object = broker._REFERENCE_POLICY_ABSENT):
        files = [("images", (f"ref{index}.png", b"\x89PNG-ref-%d" % index, "image/png"))
                 for index in range(count)]
        data = {"repo": MODEL, "prompt": "a quiet harbour"}
        if policy is not broker._REFERENCE_POLICY_ABSENT:
            data["reference_policy"] = json.dumps(policy)
        return self.client.post("/studio/image/api/generate/edit", files=files, data=data,
                                headers={"X-Hub-Token": self.token})

    def edit_raw(self, raw: bytes, boundary: str):
        return self.client.post("/studio/image/api/generate/edit", content=raw,
                                headers={"X-Hub-Token": self.token,
                                         "Content-Type": f"multipart/form-data; boundary={boundary}"})

    def forwarded(self):
        self.assertEqual(len(self.fake.calls), 1)
        sent = self.fake.calls[0]
        fields, files = asyncio.run(parsed_form(sent["content"],
                                                sent["headers"]["Content-Type"]))
        return sent, fields, files

    def assert_worker_signature_accepts(self, sent: dict, expected: dict) -> None:
        from fastapi.testclient import TestClient
        answer = TestClient(worker_signature_app()).post(
            "/api/generate/edit", content=sent["content"],
            headers={"content-type": sent["headers"]["Content-Type"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["repo"], MODEL)
        self.assertEqual(body["prompt"], "a quiet harbour")
        self.assertEqual(body["images"], ["ref0.png", "ref1.png"])
        self.assertEqual(json.loads(body["policy"]), expected)

    def test_a_valid_edit_reaches_the_worker_with_its_file_bytes_intact(self) -> None:
        response = self.edit(2)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake.calls), 1)
        sent = self.fake.calls[0]["content"]
        self.assertIn(b"\x89PNG-ref-0", sent)
        self.assertIn(b"\x89PNG-ref-1", sent)
        self.assertEqual(self.fake.calls[0]["headers"]["X-Hub-Token"], "worker-token")

    def test_an_over_count_is_refused_before_the_worker_sees_it(self) -> None:
        response = self.edit(4)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "REFERENCE_POLICY_REFUSED")
        self.assertEqual(self.fake.calls, [])

    def test_a_spoofed_permissive_policy_cannot_grant_the_submit(self) -> None:
        permissive = {"model": MODEL, "mode": "edit", "node_id": "machine-a",
                      "physical_tier": "16", "reference_count": 4,
                      "revision": "a" * 64,
                      "effective": {"allowed": True, "max_reference_images": 99,
                                    "swap_allowance_gb": 0}}
        response = self.edit(4, permissive)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.fake.calls, [])

    def test_a_present_field_that_is_not_an_object_is_refused(self) -> None:
        response = self.edit(2, None)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "REFERENCE_POLICY_REFUSED")
        self.assertEqual(self.fake.calls, [])

    def test_a_policy_for_another_node_is_refused_at_the_door(self) -> None:
        self.monitor.cached_aggregate_catalog = lambda: {"models": [{
            "repo": MODEL, "hub_studio": "image",
            "hub_memory_admission": monitor_module._memory_admission_inventory(
                MODEL, ROW, node_id="machine-b", total_memory_bytes=TIER_16)}]}
        response = self.edit(2)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("selected destination", response.json()["detail"])
        self.assertEqual(self.fake.calls, [])

    def test_an_unreadable_catalog_refuses_as_unknown(self) -> None:
        self.monitor.cached_aggregate_catalog = mock.Mock(side_effect=OSError("unreadable"))
        response = self.edit(2)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "REFERENCE_POLICY_UNKNOWN")
        self.assertEqual(self.fake.calls, [])

    def test_an_absent_caller_policy_is_forwarded_as_the_exact_producer_policy(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", None,
            {"allowed": True, "max_reference_images": 5, "swap_allowance_gb": 0},
            total_memory_bytes=TIER_16)
        snapshot = monitor_module._memory_admission_inventory(
            MODEL, ROW, node_id="machine-a", total_memory_bytes=TIER_16)
        self.monitor.cached_aggregate_catalog = lambda: {"models": [{
            "repo": MODEL, "hub_studio": "image", "hub_memory_admission": snapshot}]}
        response = self.edit(5)
        self.assertEqual(response.status_code, 200, response.text)
        sent = self.fake.calls[0]
        expected = broker._validated_reference_policy(
            snapshot, self.monitor.registry[0], MODEL, None, "edit", 5)
        self.assertEqual(expected["effective"]["max_reference_images"], 5,
                         "the raised Hub override must reach the forwarded policy")
        self.assertEqual(multipart_field(sent["content"], sent["headers"]["Content-Type"],
                                         "reference_policy"), expected)
        self.assertIn(json.dumps(expected).encode(), sent["content"])
        self.assertEqual(sent["content"].count(b'name="reference_policy"'), 1)
        for index in range(5):
            self.assertEqual(sent["content"].count(b"\x89PNG-ref-%d" % index), 1)
        fields, files = asyncio.run(parsed_form(sent["content"],
                                                sent["headers"]["Content-Type"]))
        self.assertEqual(fields["repo"], MODEL)
        self.assertEqual(fields["prompt"], "a quiet harbour")
        self.assertEqual(json.loads(fields["reference_policy"]), expected)
        self.assertEqual(files, [("images", f"ref{index}.png", b"\x89PNG-ref-%d" % index)
                                 for index in range(5)])

    def test_a_supplied_valid_policy_is_replaced_by_the_exact_producer_policy(self) -> None:
        expected = broker._validated_reference_policy(
            self.snapshot, self.monitor.registry[0], MODEL, None, "edit", 2)
        response = self.edit(2, json.loads(json.dumps(expected, sort_keys=True)))
        self.assertEqual(response.status_code, 200, response.text)
        sent = self.fake.calls[0]
        self.assertEqual(sent["content"].count(b'name="reference_policy"'), 1,
                         "a supplied field is replaced, never appended beside the caller's")
        self.assertEqual(multipart_field(sent["content"], sent["headers"]["Content-Type"],
                                         "reference_policy"), expected)
        self.assertIn(json.dumps(expected).encode(), sent["content"])
        self.assertIn(b"\x89PNG-ref-0", sent["content"])
        self.assertIn(b"\x89PNG-ref-1", sent["content"])

    def test_an_older_absent_annotation_keeps_the_existing_forward(self) -> None:
        self.monitor.cached_aggregate_catalog = lambda: {"models": []}
        response = self.edit(2)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake.calls), 1)
        sent = self.fake.calls[0]
        self.assertNotIn(b'name="reference_policy"', sent["content"])
        self.assertIn(b"\x89PNG-ref-0", sent["content"])
        self.assertIn(b"\x89PNG-ref-1", sent["content"])

    def test_the_forwarded_body_parses_with_the_real_parser_in_normal_order(self) -> None:
        expected = broker._validated_reference_policy(
            self.snapshot, self.monitor.registry[0], MODEL, None, "edit", 2)
        response = self.edit(2)
        self.assertEqual(response.status_code, 200, response.text)
        sent, fields, files = self.forwarded()
        self.assertEqual(fields["repo"], MODEL)
        self.assertEqual(fields["prompt"], "a quiet harbour")
        self.assertEqual(json.loads(fields["reference_policy"]), expected)
        self.assertEqual(files, [("images", "ref0.png", b"\x89PNG-ref-0"),
                                 ("images", "ref1.png", b"\x89PNG-ref-1")])
        self.assert_worker_signature_accepts(sent, expected)

    def test_the_forwarded_body_parses_with_the_real_parser_when_files_come_first(self) -> None:
        expected = broker._validated_reference_policy(
            self.snapshot, self.monitor.registry[0], MODEL, None, "edit", 2)
        boundary = "PeerFileFirst"
        raw = multipart_body([
            ("images", b"\x89PNG-ref-0", "ref0.png", "image/png"),
            ("repo", MODEL.encode(), None, None),
            ("prompt", b"a quiet harbour", None, None),
            ("images", b"\x89PNG-ref-1", "ref1.png", "image/png"),
        ], boundary)
        response = self.edit_raw(raw, boundary)
        self.assertEqual(response.status_code, 200, response.text)
        sent, fields, files = self.forwarded()
        self.assertEqual(fields["repo"], MODEL)
        self.assertEqual(fields["prompt"], "a quiet harbour")
        self.assertEqual(json.loads(fields["reference_policy"]), expected)
        self.assertEqual(files, [("images", "ref0.png", b"\x89PNG-ref-0"),
                                 ("images", "ref1.png", b"\x89PNG-ref-1")])
        self.assert_worker_signature_accepts(sent, expected)

    def test_a_repeated_caller_field_is_replaced_at_every_part(self) -> None:
        expected = broker._validated_reference_policy(
            self.snapshot, self.monitor.registry[0], MODEL, None, "edit", 2)
        boundary = "PeerRepeated"
        raw = multipart_body([
            ("repo", MODEL.encode(), None, None),
            ("prompt", b"a quiet harbour", None, None),
            ("reference_policy", b'{"forged": true}', None, None),
            ("images", b"\x89PNG-ref-0", "ref0.png", "image/png"),
            ("reference_policy", json.dumps(expected).encode(), None, None),
            ("images", b"\x89PNG-ref-1", "ref1.png", "image/png"),
        ], boundary)
        response = self.edit_raw(raw, boundary)
        self.assertEqual(response.status_code, 200, response.text)
        sent, fields, files = self.forwarded()
        self.assertEqual(fields["repo"], MODEL)
        self.assertEqual(json.loads(fields["reference_policy"]), expected)
        self.assertNotIn(b"forged", sent["content"])
        self.assertEqual(sent["content"].count(b'name="reference_policy"'), 2)
        self.assertEqual(files, [("images", "ref0.png", b"\x89PNG-ref-0"),
                                 ("images", "ref1.png", b"\x89PNG-ref-1")])
        self.assert_worker_signature_accepts(sent, expected)

    def test_the_worker_signature_reads_a_repeated_field_as_its_last_part(self) -> None:
        from fastapi.testclient import TestClient
        boundary = "PeerRepeatedRead"
        raw = multipart_body([
            ("repo", MODEL.encode(), None, None),
            ("prompt", b"a quiet harbour", None, None),
            ("reference_policy", b'{"which": "first"}', None, None),
            ("reference_policy", b'{"which": "last"}', None, None),
        ], boundary)
        answer = TestClient(worker_signature_app()).post(
            "/api/generate/edit", content=raw,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(json.loads(answer.json()["policy"]), {"which": "last"})


class FakeWorkerResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"job": {"id": "job-1", "state": "done"}}'


class FakeWorkerClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def request(self, method, url, headers=None, content=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "content": content})
        return FakeWorkerResponse()


async def _never_waiting(*_args, **_kwargs) -> bool:
    return False


class FleetMemoryWriteStatusTests(unittest.TestCase):
    """The outer fleet PUT route keeps the relay's pending outcome at 202."""

    def setUp(self) -> None:
        from components.hub.backend import peers
        self.token = peers.fleet_token()
        self.app = standalone.create_app(start_background=False)

    def request(self, answer: dict):
        async def run():
            transport = httpx.ASGITransport(app=self.app, client=("100.64.0.9", 51234))
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://fixture-hub") as client:
                return await client.request(
                    "PUT", "/api/hub/fleet/nodes/node-a/memory/admission",
                    headers={"X-Hub-Token": self.token},
                    json={"model": MODEL, "expected_revision": "a" * 64})
        import asyncio
        with mock.patch.object(standalone.control_plane, "load_settings",
                               lambda: {"role": "controller"}), \
                mock.patch.object(fleet_nodes, "this_machine",
                                  lambda: {"id": "machine-a"}), \
                mock.patch.object(fleet_nodes, "relay_memory_admission",
                                  mock.AsyncMock(return_value=answer)):
            return asyncio.run(run())

    def test_a_pending_relay_answer_is_202(self) -> None:
        response = self.request({"ok": True, "outcome": "pending", "queued": True})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["outcome"], "pending")

    def test_a_settled_relay_answer_stays_200(self) -> None:
        response = self.request({"ok": True, "outcome": "applied"})
        self.assertEqual(response.status_code, 200, response.text)


class FleetMemoryWriteValidationTests(unittest.TestCase):
    """A present invalid RAM number is named before the pair/reset shape is classified."""

    def write(self, **ram):
        body = {"expected_revision": "a" * 64, "model": MODEL, **ram}
        response = standalone._fleet_memory_admission_write(
            body=body, entry={}, node="node-a", node_id="machine-a",
            total_memory_bytes=TIER_16)
        return response.status_code, json.loads(response.body)

    def test_present_invalid_numbers_are_bad_values_even_in_a_mixed_pair(self) -> None:
        cases = [
            ({"min_free_memory_gb": "bad", "min_total_memory_gb": 9},
             "min_free_memory_gb"),
            ({"min_free_memory_gb": True, "min_total_memory_gb": 9},
             "min_free_memory_gb"),
            ({"min_free_memory_gb": -1, "min_total_memory_gb": 9},
             "min_free_memory_gb"),
            ({"min_free_memory_gb": math.nan, "min_total_memory_gb": 9},
             "min_free_memory_gb"),
            ({"min_free_memory_gb": None, "min_total_memory_gb": "bad"},
             "min_total_memory_gb"),
            ({"min_free_memory_gb": 4, "min_total_memory_gb": "bad"},
             "min_total_memory_gb"),
            ({"min_free_memory_gb": "bad", "min_total_memory_gb": None},
             "min_free_memory_gb"),
        ]
        for ram, field in cases:
            with self.subTest(ram=ram):
                status, payload = self.write(**ram)
                self.assertEqual(status, 400, payload)
                self.assertEqual(payload["code"], "BAD_VALUE")
                self.assertEqual(payload["field"], field)

    def test_a_valid_shape_is_still_classified_as_before(self) -> None:
        status, payload = self.write(min_free_memory_gb=None, min_total_memory_gb=9)
        self.assertEqual((status, payload["code"]), (400, "RAM_RESET_MIXED"))
        status, payload = self.write(min_free_memory_gb=4, min_total_memory_gb=None)
        self.assertEqual((status, payload["code"]), (400, "RAM_PAIR_REQUIRED"))
        status, payload = self.write(min_total_memory_gb=9)
        self.assertEqual((status, payload["code"]), (400, "RAM_PAIR_REQUIRED"))


if __name__ == "__main__":
    unittest.main()
