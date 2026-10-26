"""Reference-to-image in the regression sweep: N references in, N `images` parts out.

The reference door was written for one image (`refs[0]`) and gated on a worker advertising
`img2img`. Reference-to-image (owner-approved 2026-10-26) is the same door with N parts and the
`edit` capability, plus the reference lane's own measured memory floors. These are the properties
the sweep keeps: order is preserved, an over-count is refused by the published number, and an
edit item never lands on a Mac that can only do text-to-image.
"""
from __future__ import annotations

import asyncio
import base64

from unittest import mock

from components.hub.backend import broker

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"


def _row(**changes) -> dict:
    row = {
        "repo": MODEL,
        "modality": "image",
        "capabilities": ["txt2img", "edit"],
        "lane_contract": {"references": {"field": "images", "min": 1, "max": 2,
                                         "max_dimension": 1024,
                                         "min_total_memory_gb": 24.0,
                                         "min_free_memory_gb": 14.5}},
    }
    row.update(changes)
    return row


def _reference(payload: bytes) -> dict:
    return {"b64": base64.b64encode(payload).decode(), "mime": "image/png"}


class _Client:
    async def post(self, *args, **kwargs):   # pragma: no cover - the dispatch never posts here
        raise AssertionError("dispatch must not post in these cases")


def _dispatch(row, refs):
    with mock.patch.object(broker, "studio_request",
                           lambda _studio, path: ("http://worker" + path, {})):
        return asyncio.run(broker._reference_dispatch(
            _Client(), {"id": "image", "machine": "local", "modality": "image"},
            {"id": "b", "modality": "image", "model": MODEL}, row, refs, None,
            ("img2img", "edit")))


def test_two_references_become_two_images_parts_to_the_edit_route():
    mode, url, _headers, files = _dispatch(_row(), [_reference(b"a"), _reference(b"b")])
    assert mode == "edit"
    assert url.endswith("/api/generate/edit")
    assert [name for name, _part in files] == ["images", "images"]
    assert [part[1] for _name, part in files] == [b"a", b"b"]


def test_an_over_count_is_refused_by_the_published_number():
    try:
        _dispatch(_row(), [_reference(b"a"), _reference(b"b"), _reference(b"c")])
    except ValueError as error:
        assert "1 to 2" in str(error)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError("three references were accepted by a two-reference lane")


def test_an_edit_item_skips_a_mac_that_only_clears_the_text_to_image_floor():
    row = _row()
    edit = broker._admission_requirements(MODEL, row, mode="edit")
    text = broker._admission_requirements(MODEL, row)
    assert edit["min_total"] == 24.0 and edit["min_free"] == 14.5
    assert broker._memory_gate(edit, {"total_gb": 16.0, "available_gb": 12.0})[0] == "skip"
    assert broker._memory_gate(text, {"total_gb": 16.0, "available_gb": 12.0})[0] == "run"


def test_the_reference_count_a_worker_reported_is_published_in_the_result():
    item = {"state": "done", "index": 0, "bytes": 1, "sha256": "a" * 64,
            "media_type": "image/png"}
    broker._record_worker_identity(item, {"id": "image"}, {"reference_count": 2})
    assert broker.terminal_result({"id": "b"}, item)["reference_count"] == 2
    text_only = {"state": "done", "index": 0, "bytes": 1, "sha256": "a" * 64,
                 "media_type": "image/png"}
    assert "reference_count" not in broker.terminal_result({"id": "b"}, text_only)
