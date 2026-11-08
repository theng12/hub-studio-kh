"""Bounded official tool preparation tests.

Every archive is synthetic and every stream/probe callback is fake. No external
download, real tool installation, Hub store, or production process is touched.
"""

from __future__ import annotations

import collections
import hashlib
import io
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_gate_tools as tools

_Usage = collections.namedtuple("_Usage", "total used free")
_PLATFORM = f"{sys.platform}-{platform.machine()}"
_GO_STDOUT = "go version go1.27.2 darwin/arm64\n"


class DeveloperGateToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gate_tools_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tools_root = self.tmp / "owned"
        os.mkdir(self.tools_root, 0o700)
        self.attempt_root = self.tmp / "attempts"
        os.mkdir(self.attempt_root, 0o700)
        usage_patch = mock.patch.object(
            tools, "_disk_usage", return_value=_Usage(0, 0, 50 * 1024 ** 3)
        )
        usage_patch.start()
        self.addCleanup(usage_patch.stop)

        self.archive = b""
        self.stream_calls: list[tuple[str, float]] = []
        self.probed: list[tuple[Path, float]] = []
        self.stages: list[str] = []
        self.probe_result: dict | None = None
        self.probe_error: Exception | None = None
        self.stream_error: Exception | None = None
        self.cancel_stage: str | None = None

    # -- fixtures ---------------------------------------------------------
    def _checkpoint(self, stage: str) -> None:
        self.stages.append(stage)
        if stage == self.cancel_stage:
            raise RuntimeError("cancelled by test lease")

    def _open_stream(self, url: str, timeout: float) -> io.BytesIO:
        self.stream_calls.append((url, timeout))
        if self.stream_error is not None:
            raise self.stream_error
        return io.BytesIO(self.archive)

    def _probe(self, executable: Path, timeout: float) -> dict:
        self.probed.append((Path(executable), timeout))
        if self.probe_error is not None:
            raise self.probe_error
        if self.probe_result is not None:
            return self.probe_result
        return {
            "exit_code": 0,
            "process_release": True,
            "version": "1.27.2",
            "stdout": _GO_STDOUT,
        }

    def _tar(self, entries: list[dict]) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for entry in entries:
                info = tarfile.TarInfo(entry["name"])
                info.type = entry["type"]
                info.mode = entry.get("mode", 0o644)
                if entry["type"] == tarfile.REGTYPE:
                    data = entry.get("data", b"")
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                elif entry["type"] in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    info.linkname = entry["linkname"]
                    tar.addfile(info)
                else:
                    tar.addfile(info)
        return buffer.getvalue()

    def _manifest(
        self,
        archive: bytes,
        *,
        expanded_bytes: int | None = None,
        executable: str = "root/bin/tool",
        url: str = "https://go.dev/dl/test.tar.gz",
        filename: str = "test.tar.gz",
        limits: dict | None = None,
    ) -> Path:
        pin = {
            "id": "go",
            "version": "1.27.2",
            "platform": _PLATFORM,
            "filename": filename,
            "url": url,
            "size_bytes": len(archive),
            "sha256": hashlib.sha256(archive).hexdigest(),
            "executable": executable,
        }
        if expanded_bytes is not None:
            pin["expanded_bytes"] = expanded_bytes
        data = {
            "format": tools.MANIFEST_FORMAT,
            "scope": "archive-preparation-only",
            "production_installer_qualified": False,
            "platform": _PLATFORM,
            "limits": limits
            or {
                "max_archive_bytes": 1024 * 1024,
                "max_expanded_bytes": 1024 * 1024,
                "max_member_bytes": 1024 * 1024,
                "max_members": 1000,
                "max_path_length": 1024,
                "max_total_seconds": 60,
                "min_free_bytes": tools.MIN_FREE_BYTES,
                "download_cache_bytes": 1024 * 1024,
            },
            "artifacts": [pin],
        }
        path = self.tmp / "official-tools.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _prepare(self, manifest: Path, **kwargs):
        return tools.prepare_official_tool(
            "go",
            tools_root=self.tools_root,
            attempt_root=self.attempt_root,
            checkpoint=self._checkpoint,
            open_stream=self._open_stream,
            probe=self._probe,
            manifest_path=manifest,
            **kwargs,
        )

    def _happy_entries(self, tool_mode: int = 0o755) -> tuple[list[dict], int]:
        entries = [
            {"name": "root/", "type": tarfile.DIRTYPE, "mode": 0o755},
            {"name": "root/bin/", "type": tarfile.DIRTYPE, "mode": 0o755},
            {"name": "root/lib/", "type": tarfile.DIRTYPE, "mode": 0o755},
            {
                "name": "root/bin/tool",
                "type": tarfile.REGTYPE,
                "data": b"#!/bin/sh\nexit 0\n",
                "mode": tool_mode,
            },
            {
                "name": "root/lib/data.txt",
                "type": tarfile.REGTYPE,
                "data": b"hello",
                "mode": 0o644,
            },
            {
                "name": "root/bin/tool-link",
                "type": tarfile.SYMTYPE,
                "linkname": "tool",
                "mode": 0o777,
            },
        ]
        return entries, len(b"#!/bin/sh\nexit 0\n") + len(b"hello")

    # -- happy path and reuse ---------------------------------------------
    def test_prepares_pinned_tree_and_reuses_it(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        manifest = self._manifest(self.archive, expanded_bytes=expanded)
        result = self._prepare(manifest)

        self.assertEqual(result["format"], tools.RESULT_FORMAT)
        self.assertFalse(result["production_installer_qualified"])
        self.assertFalse(result["reused_existing_tree"])
        self.assertEqual(result["tool"]["sha256"], hashlib.sha256(self.archive).hexdigest())
        tool_root = Path(result["paths"]["tool_root"])
        executable = Path(result["paths"]["executable"])
        self.assertTrue(tool_root.is_dir())
        self.assertTrue(executable.is_file())
        self.assertTrue(os.access(executable, os.X_OK))
        self.assertTrue((tool_root / "root" / "bin" / "tool-link").is_symlink())
        self.assertTrue((tool_root / ".prepared.json").is_file())
        self.assertEqual(result["version_line"], "go version go1.27.2 darwin/arm64")
        self.assertEqual(result["footprint"]["regular_files"], 3)
        self.assertEqual(result["footprint"]["symlinks"], 1)
        self.assertEqual(len(self.probed), 1)
        for stage in ("start", "download", "extract", "probe", "promote"):
            self.assertIn(stage, self.stages)
        self.assertTrue(Path(result["archive"]["path"]).is_file())

        self.stream_calls.clear()
        self.probed.clear()
        reused = self._prepare(manifest)
        self.assertTrue(reused["reused_existing_tree"])
        self.assertEqual(reused["paths"]["tool_root"], result["paths"]["tool_root"])
        self.assertEqual(self.stream_calls, [])
        self.assertEqual(len(self.probed), 1)

    def test_required_callbacks_are_mandatory(self) -> None:
        with self.assertRaises(tools.OfficialToolError):
            tools.prepare_official_tool(
                "go",
                tools_root=self.tools_root,
                attempt_root=self.attempt_root,
                checkpoint=None,
                open_stream=self._open_stream,
                probe=self._probe,
                manifest_path=self._manifest(b"x"),
            )

    def test_expired_deadline_refuses(self) -> None:
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(b"x"), deadline_monotonic=time.monotonic() - 1)

    def test_checkpoint_cancel_cleans_attempt_directory(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        self.cancel_stage = "extract"
        with self.assertRaises(RuntimeError):
            self._prepare(self._manifest(self.archive, expanded_bytes=expanded))
        self.assertEqual(list(self.attempt_root.iterdir()), [])

    # -- hostile archive members -----------------------------------------
    def test_rejects_path_traversal_and_absolute_members(self) -> None:
        for name in ("../evil", "/tmp/evil"):
            with self.subTest(name=name):
                archive = self._tar(
                    [{"name": name, "type": tarfile.REGTYPE, "data": b"x"}]
                )
                with self.assertRaises(tools.OfficialToolError):
                    self._prepare(self._manifest(archive))
                self.assertFalse((self.tools_root / "tools").joinpath("evil").exists())

    def test_rejects_hardlinks_special_files_and_sparse_members(self) -> None:
        hardlink = self._tar(
            [
                {"name": "root/bin/tool", "type": tarfile.REGTYPE, "data": b"x", "mode": 0o755},
                {"name": "root/hard", "type": tarfile.LNKTYPE, "linkname": "root/bin/tool"},
            ]
        )
        fifo = self._tar([{"name": "root/pipe", "type": tarfile.FIFOTYPE}])
        for archive in (hardlink, fifo):
            with self.subTest(archive=archive[:8]):
                with self.assertRaises(tools.OfficialToolError):
                    self._prepare(self._manifest(archive))

    def test_rejects_escaping_missing_and_chained_symlinks(self) -> None:
        escaping = self._tar(
            [{"name": "root/link", "type": tarfile.SYMTYPE, "linkname": "../../etc/passwd"}]
        )
        missing = self._tar(
            [{"name": "root/link", "type": tarfile.SYMTYPE, "linkname": "missing"}]
        )
        chain = self._tar(
            [
                {"name": "root/a", "type": tarfile.SYMTYPE, "linkname": "b"},
                {"name": "root/b", "type": tarfile.SYMTYPE, "linkname": "a"},
            ]
        )
        for archive in (escaping, missing, chain):
            with self.subTest(archive=archive[:8]):
                with self.assertRaises(tools.OfficialToolError):
                    self._prepare(self._manifest(archive))

    def test_rejects_writes_through_earlier_links(self) -> None:
        archive = self._tar(
            [
                {"name": "root/lib/", "type": tarfile.DIRTYPE, "mode": 0o755},
                {"name": "root/link", "type": tarfile.SYMTYPE, "linkname": "lib"},
                {"name": "root/link/evil", "type": tarfile.REGTYPE, "data": b"x"},
            ]
        )
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(archive))
        self.assertFalse((self.tools_root / "tools").joinpath("evil").exists())

    def test_rejects_casefold_collisions(self) -> None:
        archive = self._tar(
            [
                {"name": "root/A/x", "type": tarfile.REGTYPE, "data": b"x"},
                {"name": "root/a/y", "type": tarfile.REGTYPE, "data": b"y"},
            ]
        )
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(archive))

    # -- pin, caps and version proof -------------------------------------
    def test_rejects_download_hash_and_size_mismatch(self) -> None:
        good = self._tar(self._happy_entries()[0])
        manifest = self._manifest(good)
        self.archive = good + b"extra"
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(manifest)

    def test_rejects_expanded_byte_mismatch(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(self.archive, expanded_bytes=expanded + 1))

    def test_rejects_member_and_expanded_caps(self) -> None:
        archive = self._tar(
            [{"name": "root/bin/tool", "type": tarfile.REGTYPE, "data": b"hello", "mode": 0o755}]
        )
        limits = {
            "max_archive_bytes": 1024 * 1024,
            "max_expanded_bytes": 2,
            "max_member_bytes": 2,
            "max_members": 10,
            "max_path_length": 1024,
            "max_total_seconds": 60,
            "min_free_bytes": tools.MIN_FREE_BYTES,
            "download_cache_bytes": 1024 * 1024,
        }
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(archive, limits=limits))

    def test_rejects_version_and_executable_proof_failures(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        manifest = self._manifest(self.archive, expanded_bytes=expanded)
        self.probe_result = {
            "exit_code": 0,
            "process_release": True,
            "version": "9.9.9",
            "stdout": "v9.9.9\n",
        }
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(manifest)

        self.probe_result = None
        not_executable = self._tar(self._happy_entries(tool_mode=0o644)[0])
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(not_executable))

    def test_rejects_substring_unparsed_and_bool_exit_versions(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        manifest = self._manifest(self.archive, expanded_bytes=expanded)
        for result in (
            {
                "exit_code": 0,
                "process_release": True,
                "version": "13.15.0",
                "stdout": "Python 3.15.0",
            },
            {
                "exit_code": 0,
                "process_release": True,
                "version": "3.15.0evil",
                "stdout": "Python 3.15.0",
            },
            {
                "exit_code": False,
                "process_release": True,
                "version": "1.27.2",
                "stdout": _GO_STDOUT,
            },
            {"exit_code": 0, "version": "1.27.2", "stdout": _GO_STDOUT},
        ):
            with self.subTest(result=result):
                self.probe_result = result
                with self.assertRaises(tools.OfficialToolError):
                    self._prepare(manifest)

    def test_unconfirmed_release_and_probe_exception_preserve_staging(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        manifest = self._manifest(self.archive, expanded_bytes=expanded)
        self.probe_result = {
            "exit_code": 0,
            "process_release": False,
            "version": "1.27.2",
            "stdout": _GO_STDOUT,
        }
        with self.assertRaises(tools.OfficialToolError) as caught:
            self._prepare(manifest)
        self.assertTrue(Path(caught.exception.retained_staging).is_dir())

        self.probe_result = None
        self.probe_error = RuntimeError("probe crashed")
        with self.assertRaises(tools.OfficialToolError) as caught:
            self._prepare(manifest)
        self.assertTrue(Path(caught.exception.retained_staging).is_dir())

    def test_second_preparation_is_refused_while_lock_held(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        manifest = self._manifest(self.archive, expanded_bytes=expanded)
        fd, key = tools._acquire_preparation_lock(self.tools_root)
        try:
            with self.assertRaises(tools.OfficialToolError):
                self._prepare(manifest)
        finally:
            tools._release_preparation_lock(fd, key)

    def test_tampered_retained_tree_is_rejected_before_probe(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        manifest = self._manifest(self.archive, expanded_bytes=expanded)
        result = self._prepare(manifest)
        tool_root = Path(result["paths"]["tool_root"])
        (tool_root / "root" / "bin" / "tool").write_text("tampered\n", encoding="utf-8")
        self.probed.clear()
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(manifest)
        self.assertEqual(self.probed, [])

    def test_free_space_reserve_refuses_before_writes(self) -> None:
        entries, expanded = self._happy_entries()
        self.archive = self._tar(entries)
        manifest = self._manifest(self.archive, expanded_bytes=expanded)
        with mock.patch.object(
            tools, "_disk_usage", return_value=_Usage(0, 0, tools.MIN_FREE_BYTES + 1)
        ):
            with self.assertRaises(tools.OfficialToolError):
                self._prepare(manifest)
        self.assertEqual(list(self.attempt_root.iterdir()), [])

    def test_rejects_untrusted_url_and_unknown_tool(self) -> None:
        archive = self._tar(self._happy_entries()[0])
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(archive, url="http://go.dev/dl/test.tar.gz"))
        with self.assertRaises(tools.OfficialToolError):
            tools.prepare_official_tool(
                "node",
                tools_root=self.tools_root,
                attempt_root=self.attempt_root,
                checkpoint=self._checkpoint,
                open_stream=self._open_stream,
                probe=self._probe,
                manifest_path=self._manifest(archive),
            )

    def test_rejects_non_private_tools_root(self) -> None:
        archive = self._tar(self._happy_entries()[0])
        os.chmod(self.tools_root, 0o777)
        with self.assertRaises(tools.OfficialToolError):
            self._prepare(self._manifest(archive))


if __name__ == "__main__":
    unittest.main()
