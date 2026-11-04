#!/usr/bin/env python3
"""The reversible v2 owner grant: storage, reading, and what withdraws it.

The owner's v1 opt-in record is never rewritten. The v2 grant lives in its own record and
binds the v1 it came from, the migration authorization, the physical node, and one exact
tuple with its evidence digest per entry. A v1 that is off, malformed or changed, or a
grant made for another node, yields nothing at all.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import render_encode, render_encode_grants  # noqa: E402
from components.hub.backend.render_executor import (  # noqa: E402
    OPT_IN_PROVENANCE,
    InstalledRenderAuthorityLoader,
)

NODE = "shop-m4-controller-01"
OTHER_NODE = "shop-m4-controller-02"
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
ENCODE = dict(render_encode.DEFAULT_OPT_IN_ENCODE)
OTHER_ENCODE = {**ENCODE, "requested_effort": "hardware"}


class GrantFixture(unittest.TestCase):
    """A temp data root with a real v1 opt-in record written by the loader itself."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # The strict reader refuses a symlinked ancestor, so the temp root is resolved.
        self.data_root = Path(os.path.realpath(self.temporary.name)) / "data"
        self.data_root.mkdir()
        self.resources = Path(os.path.realpath(self.temporary.name)) / "resources"
        self.resources.mkdir()
        self.loader = InstalledRenderAuthorityLoader(
            self.resources, data_root=self.data_root, clock=lambda: 1_000,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16})
        self.v1_path = self.data_root / "render-authority/opt-in/unqualified.json"
        self.v2_path = self.data_root / "render-authority/opt-in/probed-v2.json"

    def enable_v1(self, who: str = "owner@gateway", encode: dict | None = None) -> None:
        self.loader.set_opt_in(True, who, encode=encode)

    def entry(self, encode: dict | None = None,
              digest: str = DIGEST) -> dict:
        return {"requested_encode": dict(encode or ENCODE), "authority_digest": digest}

    def many_tuples(self, count: int) -> list[dict]:
        """``count`` distinct tuples the pinned compiler builds, so the list is not a copy."""
        tuples: list[dict] = []
        for effort in ("ultrafast", "medium", "slow", "hardware"):
            for codec in ("h264", "h265"):
                for container in ("mp4", "mov", "webm"):
                    for crf, preset in ((23, "medium"), (18, "slow"), (23, "ultrafast"),
                                        (28, "ultrafast"), (32, "ultrafast")):
                        tuples.append({**ENCODE, "requested_effort": effort,
                                       "requested_codec": codec, "container": container,
                                       "crf": crf, "quality_cpu_preset": preset})
                        if len(tuples) >= count:
                            return tuples
        return tuples

    def migrate(self, entries: list[dict] | None = None, **changes) -> dict:
        options = {
            "expected_uid": os.getuid(),
            "physical_node_id": NODE,
            "authorization_id": "developer@relay",
            "authorized_at": 1_700_000_000,
            "entries": entries if entries is not None else [self.entry()],
        }
        options.update(changes)
        return render_encode_grants.write_v2_grants(self.data_root, **options)

    def read(self, node: str = NODE) -> dict | None:
        return render_encode_grants.read_v2_grants(
            self.data_root, expected_uid=os.getuid(), physical_node_id=node)

    def files(self) -> set[str]:
        return {str(path.relative_to(self.data_root))
                for path in self.data_root.rglob("*") if path.is_file()}


class MigrationTests(GrantFixture):
    def test_the_grant_binds_the_v1_it_came_from_and_the_node_it_was_probed_on(self) -> None:
        self.enable_v1()
        document = self.migrate()
        self.assertEqual(document["format"], render_encode_grants.FORMAT)
        source = document["v1"]
        self.assertEqual(source["provenance"], "owner-opt-in-unqualified")
        self.assertEqual(source["accepted_by"], "owner@gateway")
        self.assertEqual(source["accepted_at"], 1_000)
        self.assertEqual(
            source["sha256"],
            render_encode_grants._digest(json.loads(self.v1_path.read_bytes())),
            "the v1 canonical SHA is what a changed v1 is caught by")
        self.assertEqual(document["migration"], {
            "authorization_id": "developer@relay", "authorized_at": 1_700_000_000})
        self.assertEqual(document["node"], {"physical_node_id": NODE})
        self.assertEqual(document["entries"],
                         [{"requested_encode": ENCODE, "authority_digest": DIGEST}])

    def test_the_v1_record_is_left_byte_for_byte_and_the_v2_lives_apart_from_it(self) -> None:
        self.enable_v1()
        before = self.v1_path.read_bytes()
        self.migrate()
        self.assertEqual(self.v1_path.read_bytes(), before,
                         "the migration must not rewrite the original grant")
        self.assertTrue(self.v2_path.is_file())
        self.assertEqual(self.v2_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.v2_path.parent.stat().st_mode & 0o777, 0o700)

    def test_off_stays_off_and_nothing_is_written_for_it(self) -> None:
        self.loader.set_opt_in(False, "owner@gateway")
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertFalse(self.v2_path.exists())
        self.assertIsNone(self.read())

    def test_a_grant_written_while_v1_was_on_is_withdrawn_when_v1_goes_off(self) -> None:
        self.enable_v1()
        self.migrate()
        self.assertIsNotNone(self.read())
        self.loader.set_opt_in(False, "owner@gateway")
        self.assertIsNone(self.read())

    def test_an_enabled_v1_that_is_replaced_withdraws_the_grant(self) -> None:
        self.enable_v1()
        self.migrate()
        self.loader.set_opt_in(True, "someone-else")
        self.assertIsNone(self.read(),
                          "a v1 that changed is not the v1 the grant was made from")

    def test_a_malformed_v1_withdraws_the_grant(self) -> None:
        self.enable_v1()
        self.migrate()
        self.v1_path.write_bytes(b'{"enabled":true}')
        self.assertIsNone(self.read())

    def test_a_grant_made_for_another_node_is_not_read_here(self) -> None:
        self.enable_v1()
        self.migrate()
        self.assertIsNotNone(self.read(NODE))
        self.assertIsNone(self.read(OTHER_NODE))

    def test_reading_writes_nothing(self) -> None:
        self.enable_v1()
        self.migrate()
        before = self.files()
        self.read()
        self.read(OTHER_NODE)
        self.assertEqual(self.files(), before)

    def test_the_record_stays_a_private_bounded_record(self) -> None:
        self.enable_v1()
        self.migrate()
        raw = self.v2_path.read_bytes()
        self.assertLessEqual(len(raw), 16 * 1024)
        self.assertEqual(json.loads(raw)["format"], render_encode_grants.FORMAT)


class EvidenceTests(GrantFixture):
    def test_a_caller_requested_tuple_without_evidence_is_refused(self) -> None:
        self.enable_v1()
        for label, entry in {
            "no digest": {"requested_encode": ENCODE},
            "no tuple": {"authority_digest": DIGEST},
            "extra key": {**self.entry(), "note": "x"},
            "digest is not hex": self.entry(digest="z" * 64),
            "digest is short": self.entry(digest="a" * 63),
            "tuple is not closed": {"requested_encode": {"container": "mp4"},
                                    "authority_digest": DIGEST},
        }.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self.migrate([entry])
        self.assertFalse(self.v2_path.exists())

    def test_the_same_tuple_twice_is_refused(self) -> None:
        self.enable_v1()
        with self.assertRaises(ValueError):
            self.migrate([self.entry(), self.entry(digest=OTHER_DIGEST)])
        self.assertFalse(self.v2_path.exists())

    def test_two_different_tuples_are_kept_as_two_entries(self) -> None:
        self.enable_v1()
        self.migrate([self.entry(), self.entry(OTHER_ENCODE, OTHER_DIGEST)])
        document = self.read()
        self.assertEqual([item["requested_encode"] for item in document["entries"]],
                         [ENCODE, OTHER_ENCODE])

    def test_an_empty_or_over_long_entry_list_is_refused(self) -> None:
        self.enable_v1()
        with self.assertRaises(ValueError):
            self.migrate([])
        too_many = [self.entry(encode) for encode in self.many_tuples(
            render_encode_grants.MAX_ENTRIES + 1)]
        with self.assertRaises(ValueError):
            self.migrate(too_many)
        # The bound itself still fits the private record it has to live in.
        at_the_bound = too_many[:render_encode_grants.MAX_ENTRIES]
        self.migrate(at_the_bound)
        self.assertEqual(len(self.read()["entries"]),
                         render_encode_grants.MAX_ENTRIES)

    def test_a_tampered_record_reads_as_nothing(self) -> None:
        self.enable_v1()
        self.migrate()
        original = json.loads(self.v2_path.read_bytes())
        for label, broken in {
            "extra top key": {**original, "note": "x"},
            "wrong format": {**original, "format": "studio.render.other.v1"},
            "no node": {key: value for key, value in original.items() if key != "node"},
            "other node": {**original, "node": {"physical_node_id": OTHER_NODE}},
            "loose file": {**original, "v1": {**original["v1"], "sha256": "a" * 63}},
            "duplicate tuple": {**original, "entries": [
                original["entries"][0], original["entries"][0]]},
        }.items():
            with self.subTest(label):
                self.v2_path.write_bytes(
                    json.dumps(broken, sort_keys=True, separators=(",", ":")).encode())
                self.assertIsNone(self.read())
        self.v2_path.write_bytes(b"{not json")
        self.assertIsNone(self.read())


class SchemaAgreementTests(unittest.TestCase):
    """The v1 record this grant reads is the loader's own; the two must not drift."""

    def test_the_v1_shape_matches_the_loader(self) -> None:
        self.assertEqual(render_encode_grants.V1_FORMAT,
                         InstalledRenderAuthorityLoader._OPT_IN_FORMAT)
        self.assertEqual(render_encode_grants.V1_KEYS,
                         InstalledRenderAuthorityLoader._OPT_IN_KEYS)
        self.assertEqual(render_encode_grants.V1_PROVENANCE, OPT_IN_PROVENANCE)


if __name__ == "__main__":
    unittest.main()
