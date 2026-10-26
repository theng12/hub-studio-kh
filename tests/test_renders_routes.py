#!/usr/bin/env python3
"""The Render tab's routes: one Mac's list, one render, and a controller's site-wide view.

The aggregation is the interesting half. A controller has to add every joined Mac's own list to
its own without inventing anything: a Mac that does not answer is **named as not answering** and
its renders are simply not there — never a zero that reads as "that Mac rendered nothing", never
a page that fails because one Mac is asleep.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub import renders  # noqa: E402
from components.hub.backend.render_history import read  # noqa: E402
from components.hub.backend.render_jobs import RenderJobRuntime  # noqa: E402
from tests.test_render_history import PACKET, insert  # noqa: E402

NOW = 1_800_000_000.0


def _app(path: Path, *, role: str = "standalone", nodes=None, answers=None) -> FastAPI:
    """The three routes, with the fleet read replaced by whatever the test says each Mac had."""
    nodes = nodes or []

    async def pool() -> dict:
        return {"role": role, "controller": None,
                "nodes": [{"machine": node, "label": name, "state": "reachable"}
                          for node, name in nodes]}

    async def read_node(node_id: str, path: str) -> dict:
        return (answers or {}).get(node_id, {"ok": False, "reason": "unreachable",
                                             "detail": "that Mac did not answer"})

    app = FastAPI()
    renders.register_routes(
        app, database_path=path, pool=pool, read_node=read_node,
        local_machine=lambda: {"id": "local", "name": "This Mac"},
        settings=lambda: {"role": role})
    return app


def node_answer(rows: list[dict], totals: dict) -> dict:
    return {"ok": True, "body": {"renders": rows, "totals": totals, "retention_days": 90}}


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="render-tab-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "hub.db"
        RenderJobRuntime(self.root / "renders", database_path=self.path)
        insert(self.path, "job-1")
        insert(self.path, "job-2", created_at=NOW - 900, started_at=NOW - 840,
               updated_at=NOW - 720)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def get(self, app: FastAPI, url: str):
        return TestClient(app).get(url)

    def test_a_standalone_Mac_lists_its_own_renders_newest_first(self) -> None:
        answer = self.get(_app(self.path), "/api/hub/renders")
        self.assertEqual(answer.status_code, 200)
        body = answer.json()
        self.assertEqual([row["id"] for row in body["renders"]], ["job-1", "job-2"])
        self.assertEqual(body["role"], "standalone")
        self.assertEqual(body["machines"], [{"machine": "local", "machine_name": "This Mac",
                                             "renders": 2, "ok": True, "error": None}])
        self.assertEqual(body["renders"][0]["machine_name"], "This Mac")

    def test_one_render_carries_everything_the_detail_view_shows(self) -> None:
        answer = self.get(_app(self.path), "/api/hub/renders/job-1")
        self.assertEqual(answer.status_code, 200)
        row = answer.json()
        for field in ("profile_digest", "output_sha256", "video_seconds", "width", "height",
                      "frame_rate", "wall_seconds", "failure", "local_file", "story_uuid",
                      "intent_id", "gateway_attempt_key", "codec"):
            self.assertIn(field, row)
        self.assertIsNone(row["local_file"])
        self.assertEqual(self.get(_app(self.path), "/api/hub/renders/job-missing").status_code, 404)

    def test_a_controller_adds_each_mac_and_names_one_that_does_not_answer(self) -> None:
        node_row = {**read(self.path, now=NOW)["renders"][0], "id": "job-node"}
        node_row["created_at"] = NOW - 100
        app = _app(self.path, role="controller",
                   nodes=[("blue-1", "Blue Room 1"), ("blue-2", "Blue Room 2")],
                   answers={"blue-1": node_answer([node_row], {
                       "processed": 1, "succeeded": 1, "failed": 0, "cancelled": 0,
                       "running": 0, "output_seconds": 300, "unknown_duration": 0,
                       "measured_renders": 1, "measured_output_seconds": 300,
                       "measured_wall_seconds": 60})})
        body = self.get(app, "/api/hub/renders").json()
        self.assertEqual([row["id"] for row in body["renders"]],
                         ["job-node", "job-1", "job-2"])
        self.assertEqual(body["renders"][0]["machine"], "blue-1")
        self.assertEqual(body["renders"][0]["machine_name"], "Blue Room 1")
        sources = {row["machine"]: row for row in body["machines"]}
        self.assertEqual(sources["blue-1"]["renders"], 1)
        self.assertEqual(sources["blue-1"]["machine_name"], "Blue Room 1")
        self.assertFalse(sources["blue-2"]["ok"])
        self.assertEqual(sources["blue-2"]["error"], "that Mac did not answer")
        # This Mac's two renders plus the node's one, and the rates re-derived from the sums.
        self.assertEqual(body["totals"]["processed"], 3)
        self.assertEqual(body["totals"]["output_seconds"], 900)
        self.assertEqual(body["totals"]["measured_renders"], 3)
        self.assertAlmostEqual(body["totals"]["speed"], 900 / 240)

    def test_a_node_that_answers_with_prose_instead_of_a_list_is_an_error_not_a_crash(self) -> None:
        app = _app(self.path, role="controller", nodes=[("blue-1", "Blue Room 1")],
                   answers={"blue-1": {"ok": True, "body": {"renders": "nope"}}})
        body = self.get(app, "/api/hub/renders").json()
        self.assertEqual([row["id"] for row in body["renders"]], ["job-1", "job-2"])
        self.assertEqual(body["machines"][-1]["renders"], 0)

    def test_a_mac_the_pool_could_not_reach_is_named_and_not_dialled_again(self) -> None:
        dialled: list[str] = []

        async def pool() -> dict:
            return {"role": "controller", "controller": None,
                    "nodes": [{"machine": "blue-9", "label": "Blue Room 9",
                               "state": "unreachable"}]}

        async def read_node(node_id: str, path: str) -> dict:
            dialled.append(node_id)
            return {"ok": True, "body": {"renders": []}}

        app = FastAPI()
        renders.register_routes(
            app, database_path=self.path, pool=pool, read_node=read_node,
            local_machine=lambda: {"id": "local", "name": "This Mac"},
            settings=lambda: {"role": "controller"})
        body = self.get(app, "/api/hub/renders").json()
        self.assertEqual(dialled, [])
        self.assertEqual(body["machines"][-1]["machine"], "blue-9")
        self.assertEqual(body["machines"][-1]["machine_name"], "Blue Room 9")
        self.assertFalse(body["machines"][-1]["ok"])
        self.assertEqual(body["machines"][-1]["error"], "that Mac did not answer")

    def test_a_node_does_not_ask_the_fleet_for_a_roster_it_will_not_use(self) -> None:
        """A node's page is its own renders. Dialling its controller for a roster it cannot use
        would be a wait on every page open, so the role is read locally first."""

        async def pool() -> dict:
            raise AssertionError("a node must not read the fleet roster for its own renders")

        app = FastAPI()
        renders.register_routes(
            app, database_path=self.path, pool=pool,
            read_node=lambda node_id, path: None,
            local_machine=lambda: {"id": "local", "name": "This Mac"},
            settings=lambda: {"role": "agent"})
        body = self.get(app, "/api/hub/renders").json()
        self.assertEqual(body["role"], "agent")
        self.assertEqual([row["id"] for row in body["renders"]], ["job-1", "job-2"])
        self.assertEqual(len(body["machines"]), 1)

    def test_the_per_mac_route_answers_local_and_refuses_a_node_on_a_non_controller(self) -> None:
        app = _app(self.path)
        body = self.get(app, "/api/hub/fleet/nodes/local/renders").json()
        self.assertEqual(body["machine"], "local")
        self.assertEqual(len(body["renders"]), 2)
        self.assertEqual(
            self.get(app, "/api/hub/fleet/nodes/blue-1/renders").status_code, 404)

    def test_the_per_mac_route_relays_a_node_on_a_controller(self) -> None:
        from components.hub.backend import render_history
        app = _app(self.path, role="controller", nodes=[("blue-1", "Blue Room 1")],
                   answers={"blue-1": node_answer([], render_history.totals([]))})
        body = self.get(app, "/api/hub/fleet/nodes/blue-1/renders").json()
        self.assertEqual(body["renders"], [])
        self.assertEqual(body["retention_days"], 90)
        self.assertIsNone(body["totals"]["output_minutes"])


class WiringTests(unittest.TestCase):
    """The routes are reachable on a real Hub, with the real token, not only in a bare app.

    A page whose route was never registered looks exactly like a page with nothing to show: the
    app draws its "cannot list renders" banner and nobody reads it as a wiring fault. This builds
    `create_app` and asks it.
    """

    def setUp(self) -> None:
        from contextlib import ExitStack
        from unittest.mock import patch

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temporary = tempfile.TemporaryDirectory(prefix="render-tab-wired-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        data = root / "hub"
        data.mkdir()
        output = root / "image-output"
        output.mkdir()
        workers = root / "private-workers.json"
        workers.write_text(json.dumps([{
            "id": "image", "title": "Image Studio", "modality": "image", "host": "127.0.0.1",
            "port": 51101, "machine": "node-a", "local": True,
            "runtime_identity": {"id": "image-runtime"},
            "hub_token": "wired-hub-token", "managed_output_roots": [str(output)]}]))
        workers.chmod(0o600)
        token = data / ".hub_token"
        token.write_text("wired-hub-token\n")
        token.chmod(0o600)
        for key, value in (("STUDIOHUB_DATA_DIR", str(data)),
                           ("STUDIOHUB_WORKERS_FILE", str(workers)),
                           ("STUDIOHUB_PACKAGED", "1")):
            self.stack.enter_context(patch.dict(os.environ, {key: value}))
        from components.hub.backend import auth, ledger, monitor as monitor_module, registry
        for module, name, value in (
                (registry, "DATA_DIR", data),
                (registry, "REGISTRY_FILE", data / "studios.json"),
                (registry, "LABELS_FILE", data / "machine_labels.json"),
                (registry, "FLAGS_FILE", data / "machine_flags.json"),
                (monitor_module, "DATA_DIR", data),
                (auth, "TOKEN_FILE", data / ".hub_token"),
                (auth, "PASSWORD_FILE", data / ".hub_password.json"),
                (auth, "SESSIONS_FILE", data / ".hub_sessions.json"),
                (ledger, "DB_FILE", data / "hub.db"),
                (ledger, "PINOKIO_HOME", data)):
            self.stack.enter_context(patch.object(module, name, value))
        for name in ("_labels_cache", "_flags_cache"):
            setattr(registry, name, None)
        self.database = data / "hub.db"
        RenderJobRuntime(data / "final-renders", database_path=self.database)
        insert(self.database, "job-wired", created_at=time.time() - 600,
               started_at=time.time() - 540, updated_at=time.time() - 480)

    def test_the_app_serves_the_render_list_and_one_render_with_the_hub_token(self) -> None:
        from fastapi.testclient import TestClient
        from components.hub.standalone import create_app
        with TestClient(create_app(start_background=False)) as client:
            headers = {"Authorization": "Bearer wired-hub-token"}
            listed = client.get("/api/hub/renders", headers=headers)
            self.assertEqual(listed.status_code, 200)
            body = listed.json()
            self.assertEqual([row["id"] for row in body["renders"]], ["job-wired"])
            self.assertEqual(body["totals"]["processed"], 1)
            one = client.get("/api/hub/renders/job-wired", headers=headers)
            self.assertEqual(one.status_code, 200)
            self.assertEqual(one.json()["video_seconds"], 300)
            self.assertEqual(
                client.get("/api/hub/fleet/nodes/local/renders", headers=headers).status_code, 200)


class MergeTests(unittest.TestCase):
    def test_totals_are_added_and_never_averaged_across_macs(self) -> None:
        a = {"processed": 1, "succeeded": 1, "failed": 0, "cancelled": 0, "running": 0,
             "output_seconds": 600, "unknown_duration": 0, "measured_renders": 1,
             "measured_output_seconds": 600, "measured_wall_seconds": 60}
        b = {"processed": 1, "succeeded": 1, "failed": 0, "cancelled": 0, "running": 0,
             "output_seconds": 60, "unknown_duration": 0, "measured_renders": 1,
             "measured_output_seconds": 60, "measured_wall_seconds": 120}
        from components.hub.backend import render_history
        merged = render_history.merge_totals([a, b])
        self.assertEqual(merged["processed"], 2)
        self.assertEqual(merged["average_wall_seconds"], 90)
        self.assertAlmostEqual(merged["speed"], 660 / 180)

    def test_merging_nothing_keeps_every_rate_unknown(self) -> None:
        from components.hub.backend import render_history
        merged = render_history.merge_totals([render_history.totals([])])
        self.assertEqual(merged["processed"], 0)
        self.assertIsNone(merged["output_minutes"])
        self.assertIsNone(merged["average_wall_seconds"])
        self.assertIsNone(merged["speed"])


if __name__ == "__main__":
    unittest.main()
