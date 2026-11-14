#!/usr/bin/env python3
"""The Render tab's facts: what a render is, and the totals the page puts above the list.

Two questions, pinned separately, because a wrong one is invisible in the other:

* **what one render states** — its length, canvas, encode, hash, wall time and where the file is;
* **what the totals do with a fact the Hub does not have.** A render the Hub never timed, or a
  packet that states no length, must read as *unknown* on the row and out of the average — never
  as 0, which the owner would read as "a zero-second render" (AGENTS.md §6).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import render_history  # noqa: E402
from components.hub.backend.render_jobs import RenderJobRuntime  # noqa: E402
from _render_authority_root import anchor_root  # noqa: E402

NOW = 1_800_000_000.0

PACKET = {
    "recipe": {
        "namespace": "studio.render.final.v1",
        "canvas": {"width": 1920, "height": 1080, "working_fps": 30},
        "target_duration_sec": 300,
        "encode": {"container": "mp4", "requested_codec": "h264",
                   "requested_effort": "hardware", "crf": 23,
                   "quality_cpu_preset": "medium", "bitrate_kbps": None},
    },
}

_RECEIPT = {
    "owner_scope": "site:controller:hub", "hub_job_id": "job",
    "attempt_id": "a" * 32, "accepted_request_sha256": "b" * 64,
    "receipt_digest": "c" * 64, "output_sha256": "d" * 64,
    "output_size_bytes": 25_000_000, "profile_digest": "1" * 64,
    "tools_digest": "2" * 64, "compiler_digest": "3" * 64,
    "qualification_digest": "4" * 64, "generated_command_digest": "5" * 64,
    "selected_tier": "hardware-h264", "feature_losses": [],
}


def database(path: Path) -> Path:
    """An empty `hub.db` carrying the render lane's own schema."""
    RenderJobRuntime(path.parent / "renders", database_path=path)
    return path


def insert(path: Path, job_id: str, **fields) -> None:
    """One row as the lane writes it. Only `render_history` is under test here."""
    row = {
        "id": job_id, "owner_scope": "site:controller:hub",
        "dispatch_key": f"gateway:{job_id}", "body_sha256": "0" * 64,
        "request_bytes": b"{}", "packet_json": json.dumps(PACKET),
        "story_uuid": "ff9b2a6c-1111-4222-8333-9444aaaa5555", "intent_id": "intent-" + job_id,
        "attempt_no": 1, "gateway_attempt_key": "gateway-" + job_id,
        "destination_site": "site", "destination_controller": "controller",
        "destination_node": "node", "dispatch_fence": "fence",
        "profile_digest": "1" * 64, "tools_digest": "2" * 64,
        "compiler_digest": "3" * 64, "qualification_digest": "4" * 64,
        "qualification_json": "{}", "purpose": "production",
        "state": "output_ready", "machine": "local",
        "created_at": NOW - 600, "updated_at": NOW - 480, "started_at": NOW - 600 + 60,
        "output_receipt_json": json.dumps({**_RECEIPT, "hub_job_id": job_id}),
        "output_path": None, "recovery_note": None, "stopped_evidence_json": None,
    }
    row.update(fields)
    with sqlite3.connect(path) as connection:
        names = ",".join(row)
        marks = ",".join("?" * len(row))
        connection.execute(f"INSERT INTO render_jobs ({names}) VALUES ({marks})",
                           tuple(row.values()))


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="render-history-")
        self.root = anchor_root(self, Path(self.temporary.name))
        self.path = database(self.root / "hub.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def read(self, **kwargs) -> dict:
        return render_history.read(self.path, now=NOW, **kwargs)

    def test_one_render_states_its_own_length_canvas_encode_and_hash(self) -> None:
        insert(self.path, "job-1")
        row = self.read()["renders"][0]
        self.assertEqual(row["outcome"], "succeeded")
        self.assertEqual(row["video_seconds"], 300)
        self.assertEqual((row["width"], row["height"], row["frame_rate"]), (1920, 1080, 30))
        self.assertEqual((row["container"], row["codec"], row["effort"]), ("mp4", "h264", "hardware"))
        self.assertEqual(row["output_bytes"], 25_000_000)
        self.assertEqual(row["output_sha256"], "d" * 64)
        self.assertEqual(row["tier"], "hardware-h264")
        # The 60 s between the process being bound and the row settling.
        self.assertEqual(row["wall_seconds"], 60)
        self.assertEqual(row["story_uuid"], "ff9b2a6c-1111-4222-8333-9444aaaa5555")
        self.assertEqual(row["gateway_attempt_key"], "gateway-job-1")
        self.assertIsNone(row["failure"])

    def test_peak_rss_comes_from_the_completed_jobs_retained_receipt(self) -> None:
        from uuid import uuid4
        from components.hub.backend import render_authority_files
        from components.hub.backend.render_executor import InstalledRenderAuthorityLoader

        # macOS spells TemporaryDirectory under /var, a symlink the strict reader refuses.
        self.path = self.path.resolve()
        job_id = str(uuid4())
        document = {
            "format": "studio.render.qualification-receipt.v1", "production_eligible": False,
            "profile_digest": "1" * 64, "tools_digest": "2" * 64,
            "compiler_digest": "3" * 64, "hardware_class": "m4-16gb",
            "selected_tier": "hardware-h264", "exact_encoder": "h264_videotoolbox",
            "font_support": False, "files": {},
            "bounds": {"max_rss_bytes": 100_000_000, "max_wall_seconds": 120,
                       "min_available_memory_bytes": 100_000_000,
                       "output_limit_bytes": 50_000_000},
        }
        document["qualification_digest"] = InstalledRenderAuthorityLoader._record_digest(document)
        measured = {"peak_rss_bytes": 12_345_678, "wall_seconds": 60,
                    "min_free_bytes": 100_000_000, "output_bytes": 25_000_000,
                    "hub_job_id": job_id, "receipt_digest": _RECEIPT["receipt_digest"]}
        insert(self.path, job_id, qualification_json=json.dumps({"document": document}))
        self.assertIsNone(self.read()["renders"][0]["peak_rss_bytes"])
        (self.path.parent / "render-authority").mkdir(mode=0o700)
        render_authority_files.write_record_once(
            self.path.parent, "receipts", InstalledRenderAuthorityLoader._receipt_name(
                document["hardware_class"], job_id, document["qualification_digest"]),
            json.dumps({**document, "intent_id": job_id, "measured": measured}).encode())
        row = self.read()["renders"][0]
        self.assertEqual(row["peak_rss_bytes"], 12_345_678)
        self.assertIn("sampled every 250 ms", row["peak_rss_source"])
        self.assertEqual(render_history.detail(self.path, job_id, now=NOW)["peak_rss_bytes"],
                         12_345_678)
        # The same file must not be credited to a different output or failed attempt.
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE render_jobs SET output_receipt_json = ? WHERE id = ?",
                               (json.dumps({**_RECEIPT, "receipt_digest": "0" * 64}), job_id))
        self.assertIsNone(self.read()["renders"][0]["peak_rss_bytes"])
        self.assertIsNone(self.read()["renders"][0]["peak_rss_source"])

    def test_display_labels_story_sent_are_published_and_absent_ones_stay_absent(self) -> None:
        """Channel and episode for the row, or nothing: never a name the Hub made up."""
        insert(self.path, "job-1",
               display_json=json.dumps({"channel": "Khmer History",
                                        "episode": "EP012 ការធ្លាក់ចុះ"}))
        insert(self.path, "job-2", created_at=NOW - 900, display_json=None)
        insert(self.path, "job-3", created_at=NOW - 1200,
               display_json=json.dumps({"channel": "", "episode": 7}))
        rows = {row["id"]: row for row in self.read()["renders"]}
        self.assertEqual(rows["job-1"]["display"],
                         {"channel": "Khmer History", "episode": "EP012 ការធ្លាក់ចុះ"})
        # A render Story Studio sent no name for is not named by the Hub.
        self.assertIsNone(rows["job-2"]["display"])
        # Stored labels that are not labels are dropped rather than shown.
        self.assertIsNone(rows["job-3"]["display"])

    def test_counts_minutes_average_and_speed_come_from_the_renders_themselves(self) -> None:
        insert(self.path, "job-1")                     # 300 s output, 60 s wall
        insert(self.path, "job-2", created_at=NOW - 900, started_at=NOW - 840,
               updated_at=NOW - 720)                   # 300 s output, 120 s wall
        insert(self.path, "job-3", state="failed", created_at=NOW - 1200,
               started_at=NOW - 1140, updated_at=NOW - 1100, output_receipt_json=None)
        totals = self.read()["totals"]
        self.assertEqual(totals["processed"], 3)
        self.assertEqual((totals["succeeded"], totals["failed"], totals["running"]), (2, 1, 0))
        self.assertEqual(totals["output_seconds"], 600)
        self.assertEqual(totals["output_minutes"], 10)
        self.assertEqual(totals["unknown_duration"], 0)
        self.assertEqual(totals["measured_renders"], 2)
        self.assertEqual(totals["average_wall_seconds"], 90)
        # Ten minutes of video produced in three minutes of rendering.
        self.assertAlmostEqual(totals["speed"], 600 / 180)

    def test_a_packet_that_states_no_length_is_unknown_not_zero(self) -> None:
        insert(self.path, "job-1", packet_json="{}")
        row = self.read()["renders"][0]
        self.assertIsNone(row["video_seconds"])
        self.assertIsNone(row["width"])
        totals = self.read()["totals"]
        self.assertEqual(totals["unknown_duration"], 1)
        self.assertIsNone(totals["output_seconds"])
        self.assertIsNone(totals["output_minutes"])
        # Nothing timed and nothing measured: no rate is claimed at all.
        self.assertIsNone(totals["average_wall_seconds"])
        self.assertIsNone(totals["speed"])

    def test_a_known_length_beside_an_unknown_one_still_counts_the_unknown(self) -> None:
        insert(self.path, "job-1")
        insert(self.path, "job-2", created_at=NOW - 900, packet_json="{}")
        totals = self.read()["totals"]
        self.assertEqual(totals["output_seconds"], 300)
        self.assertEqual(totals["unknown_duration"], 1)
        self.assertEqual(totals["measured_renders"], 1)

    def test_a_render_accepted_before_started_at_was_recorded_reports_no_wall_time(self) -> None:
        insert(self.path, "job-1", started_at=None)
        row = self.read()["renders"][0]
        self.assertIsNone(row["wall_seconds"])
        totals = self.read()["totals"]
        self.assertIsNone(totals["average_wall_seconds"])
        self.assertIsNone(totals["speed"])
        self.assertEqual(totals["measured_renders"], 0)
        # The output it produced is still its own fact.
        self.assertEqual(totals["output_seconds"], 300)

    def test_a_running_render_says_how_long_it_has_been_going(self) -> None:
        insert(self.path, "job-1", state="rendering", started_at=NOW - 90,
               updated_at=NOW - 90, output_receipt_json=None)
        row = self.read()["renders"][0]
        self.assertEqual(row["outcome"], "running")
        self.assertEqual(row["running_seconds"], 90)
        self.assertIsNone(row["wall_seconds"])
        self.assertEqual(self.read()["totals"]["running"], 1)

    def test_newest_first_and_the_limit_is_a_page_not_the_totals(self) -> None:
        for index in range(5):
            insert(self.path, f"job-{index}", created_at=NOW - 100 * (5 - index))
        answer = self.read(limit=2)
        self.assertEqual([row["id"] for row in answer["renders"]], ["job-4", "job-3"])
        self.assertEqual(answer["totals"]["processed"], 5)

    def test_the_window_matches_the_history_the_Hub_keeps(self) -> None:
        insert(self.path, "job-old",
               created_at=NOW - 91 * 86400, started_at=NOW - 91 * 86400,
               updated_at=NOW - 91 * 86400 + 60)
        self.assertEqual(self.read()["renders"], [])
        self.assertEqual(self.read()["retention_days"], 90)

    def test_a_file_the_Hub_still_holds_is_named_and_one_it_does_not_is_not(self) -> None:
        held = Path(self.temporary.name) / "final.mp4"
        held.write_bytes(b"video")
        insert(self.path, "job-1", output_path=str(held))
        insert(self.path, "job-2", created_at=NOW - 900, output_path=str(held) + ".gone")
        rows = {row["id"]: row for row in self.read()["renders"]}
        self.assertEqual(rows["job-1"]["local_file"], str(held))
        self.assertIsNone(rows["job-2"]["local_file"])

    def test_a_failure_says_why_in_the_Hubs_own_words(self) -> None:
        insert(self.path, "job-1", state="failed", recovery_note="output_validation_failed")
        insert(self.path, "job-2", created_at=NOW - 900, state="failed",
               stopped_evidence_json=json.dumps({"exit_code": 1, "reason": "failed"}))
        insert(self.path, "job-3", created_at=NOW - 1200, state="cancelled")
        rows = {row["id"]: row for row in self.read()["renders"]}
        self.assertIn("did not match", rows["job-1"]["failure"])
        self.assertEqual(rows["job-2"]["failure"], "ffmpeg exited with code 1.")
        self.assertEqual(rows["job-3"]["failure"], "Cancelled before the output was accepted.")

    def test_detail_answers_one_render_and_nothing_for_an_unknown_id(self) -> None:
        insert(self.path, "job-1", state="failed", recovery_note="prelaunch_failed")
        row = render_history.detail(self.path, "job-1", now=NOW)
        self.assertEqual(row["id"], "job-1")
        self.assertEqual(row["failure"], "The render could not start on that Mac.")
        self.assertIsNone(render_history.detail(self.path, "job-missing", now=NOW))


class EmptyTests(unittest.TestCase):
    def test_a_Hub_that_has_not_rendered_yet_reads_as_empty_rather_than_failing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="render-history-empty-") as directory:
            path = Path(directory) / "hub.db"
            answer = render_history.read(path, now=NOW)
            self.assertEqual(answer["renders"], [])
            self.assertEqual(answer["totals"]["processed"], 0)
            self.assertIsNone(answer["totals"]["output_minutes"])
            # Reading a history never creates the store it reads.
            self.assertFalse(path.exists())

    def test_a_database_without_the_render_table_is_an_empty_history(self) -> None:
        with tempfile.TemporaryDirectory(prefix="render-history-plain-") as directory:
            path = Path(directory) / "hub.db"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE assets (id TEXT)")
            self.assertEqual(render_history.read(path, now=NOW)["renders"], [])


class StartedAtTests(unittest.TestCase):
    """The one fact the tab needs that the lane did not already record."""

    def test_an_existing_database_gains_the_column_and_keeps_its_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="render-history-migrate-") as directory:
            path = Path(directory) / "hub.db"
            database(path)
            insert(path, "job-1")
            with sqlite3.connect(path) as connection:
                connection.execute("ALTER TABLE render_jobs DROP COLUMN started_at")
                connection.execute("UPDATE render_jobs SET updated_at = ?", (NOW - 60,))
            RenderJobRuntime(Path(directory) / "renders", database_path=path)
            with sqlite3.connect(path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(render_jobs)")}
            self.assertIn("started_at", columns)
            self.assertIsNone(render_history.read(path, now=NOW)["renders"][0]["started_at"])

    def test_a_row_accepted_before_the_labels_existed_reports_no_name(self) -> None:
        """The column is added to an existing database, and its older rows keep no name."""
        with tempfile.TemporaryDirectory(prefix="render-history-migrate-display-") as directory:
            path = Path(directory) / "hub.db"
            database(path)
            insert(path, "job-1")
            with sqlite3.connect(path) as connection:
                connection.execute("ALTER TABLE render_jobs DROP COLUMN display_json")
            RenderJobRuntime(Path(directory) / "renders", database_path=path)
            with sqlite3.connect(path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(render_jobs)")}
            self.assertIn("display_json", columns)
            self.assertIsNone(render_history.read(path, now=NOW)["renders"][0]["display"])

    def test_binding_the_render_process_is_when_the_wall_clock_starts(self) -> None:
        from components.hub.backend.render_jobs import ProcessIdentity

        with tempfile.TemporaryDirectory(prefix="render-history-start-") as directory:
            path = Path(directory) / "hub.db"
            runtime = RenderJobRuntime(Path(directory) / "renders", database_path=path)
            insert(path, "job-1", state="launch_pending", attempt_id="a" * 32,
                   launch_token="token", started_at=None, updated_at=NOW - 30,
                   output_receipt_json=None)
            runtime.record_process_started(
                "site:controller:hub", "job-1", "a" * 32, "token",
                ProcessIdentity(pid=4321, process_group_id=4321, start_token="start"))
            with sqlite3.connect(path) as connection:
                started = connection.execute(
                    "SELECT started_at, state FROM render_jobs WHERE id='job-1'").fetchone()
            self.assertEqual(started[1], "rendering")
            self.assertIsNotNone(started[0])
            self.assertAlmostEqual(started[0], time.time(), delta=30)


if __name__ == "__main__":
    unittest.main()
