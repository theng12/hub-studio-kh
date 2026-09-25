"""A published RAM tier must reach the screen, and must never become a wall.

The owner, 2026-09-25, on Chat: *"8 GB behaviour. Today nothing refuses and nothing warns on Chat;
Image warns and lets you try. I recommend making Chat warn the same way, and only adding a refusal
if you actually want one."* — and then: *"2. yes show warning"*.

Measured before anything was built: **Chat already did.** The worker derives the sentence from the
model's own manifest tier and publishes it as ``memory_warning`` on its catalog row, one key from
the top level where the app reads it, and the Chat page draws it with the same orange triangle the
Image page uses. The independent ST-14 review had recorded the opposite — "the app ignores the
warning" — which is exactly why this is a test now: an unproven claim about a warning is one
refactor away from being true, and nobody would notice, because the machine that would show it is
the one machine nobody develops on.

Two things are pinned here:

1. The Chat worker publishes the warning where the app reads it, it says both numbers, and it stays
   **advice**: the model is still eligible and still offered on the small host. A tier is not a wall
   (``memory_admission``'s own doctrine, and the owner's stated preference).
2. The invariant behind it, so the next studio cannot publish a warning into the void: every worker
   that puts a ``memory_warning`` on a catalog row must have a page that renders it.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from components.chat.app.backend import memory_policy as memory_policy_module
from components.chat.app.backend.main import create_app
from test_chat_worker import TOKEN, _FakeEngine


ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
# The tier this project ships the chat model at, from its own manifest rather than a literal: a
# test that hardcoded 16 would keep passing the day the manifest changed to something else.
CHAT_MANIFEST = (ROOT / "model-manifests" / "qwen3-5-9b-4bit.json").read_text()


def _manifest_tier_gb() -> float:
    return float(re.search(r'"minimum_unified_memory_gb"\s*:\s*(\d+(?:\.\d+)?)',
                           CHAT_MANIFEST).group(1))


class ChatBelowTierWarningTests(unittest.TestCase):
    """The published warning, driven through the real catalog route on two host sizes."""

    def _row(self, memory_gb: int) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _FakeEngine(verified=True)
            engine.minimum_unified_memory_gb = _manifest_tier_gb()
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            client = TestClient(create_app(engine, policy, TOKEN, "a" * 64))
            # The worker reads the host's memory from sysctl; patching the call keeps this honest
            # about which machine it is claiming to be, without pretending to be one.
            with mock.patch("subprocess.check_output",
                            return_value=str(memory_gb * 1024 ** 3)):
                response = client.get("/api/catalog", headers={"x-studio-token": TOKEN})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["models"][0]

    def test_a_host_below_the_tier_publishes_the_warning_the_app_reads(self) -> None:
        tier = _manifest_tier_gb()
        row = self._row(8)
        warning = row.get("memory_warning")
        self.assertIsInstance(warning, str, "an 8 GB host must be told it is below the tier")
        self.assertIn("8 GB", warning, f"the warning must state this Mac's memory: {warning}")
        self.assertIn(f"{tier:g} GB", warning, f"the warning must state the model's tier: {warning}")
        # One key from the top level, which is where `NativeModel.memoryWarning` decodes from.
        self.assertIsInstance(row.get("min_total_memory_gb"), (int, float),
                              "the tier the warning is derived from must be published beside it")

    def test_a_host_at_the_tier_publishes_no_warning_at_all(self) -> None:
        # Not "a reassuring message": nothing. A warning rendered on a machine that is fine is
        # noise the operator learns to ignore, and then the real one is ignored too.
        self.assertIsNone(self._row(32).get("memory_warning"))

    def test_the_warning_is_advice_and_the_model_is_still_offered(self) -> None:
        row = self._row(8)
        self.assertIsNotNone(row.get("memory_warning"))
        self.assertTrue(row.get("memory_eligible"),
                        "a RAM tier is advice, not an admission gate")
        self.assertTrue(row.get("available"), "an 8 GB Mac must still be able to try")
        self.assertTrue(row.get("execution_ready"))


class EveryPublishedWarningHasAPageTests(unittest.TestCase):
    """The invariant: no worker publishes a warning into the void.

    Read from the source of both halves rather than from a list, so the next studio to publish one
    fails here instead of shipping a sentence that can never appear.
    """

    #: The app's own name for each worker's page, because two of the five differ from the
    #: component directory — the component is ``subtitle`` and the Studio the operator sees is
    #: **Transcription** (``selectedTranscriptionModel``), and the component is ``voice`` while the
    #: page is Audio (``selectedAudioModel``). This was first written as ``worker.capitalize()``,
    #: which reported the Subtitle page as missing a warning it has rendered all along; a mapping
    #: that is guessed reports working code as broken. A worker absent from this table fails the
    #: test, so a sixth Studio has to say what its page is called rather than defaulting to a name
    #: nothing uses.
    STUDIO_ACCESSOR = {
        "chat": "Chat",
        "image": "Image",
        "music": "Music",
        "subtitle": "Transcription",
        "voice": "Audio",
    }

    def _publishing_workers(self) -> set[str]:
        found = set()
        # Every module of every worker's backend, not just `main.py`: Subtitle builds its catalog
        # in `engine.py`, and the first version of this check scanned only `main.py`, so it missed
        # the one worker the invariant was written for and passed while the defect was live. A
        # discovery rule that looks in one file is a rule about that file.
        for module in sorted((ROOT / "components").glob("*/app/backend/*.py")):
            text = module.read_text()
            # A catalog field the app can decode as `memoryWarning`. The nested
            # `readiness.host.memory_warning` shape is deliberately NOT this: it is a level down
            # under a key no screen reads, and publishing only that is the defect.
            if re.search(r'^\s*[\'"]memory_warning[\'"]:\s*\S', text, re.M):
                found.add(module.relative_to(ROOT).parts[1])
        return found

    def test_every_worker_that_publishes_a_warning_has_a_page_that_renders_one(self) -> None:
        rendered = set(re.findall(r"selected(\w+)Model\?\.memoryWarning", VIEWS))
        for worker in sorted(self._publishing_workers()):
            with self.subTest(worker=worker):
                accessor = self.STUDIO_ACCESSOR.get(worker)
                self.assertIsNotNone(
                    accessor,
                    f"components/{worker} publishes `memory_warning`, and this test does not know "
                    f"what its page is called — add it to STUDIO_ACCESSOR with the accessor name "
                    f"the SwiftUI page actually uses")
                self.assertIn(accessor, rendered,
                              f"components/{worker} publishes `memory_warning` on its catalog row, "
                              f"but no page renders `selected{accessor}Model?.memoryWarning`, so "
                              f"the operator can never see it (rendered: {sorted(rendered)})")

    def test_the_four_shipped_warnings_are_the_ones_renderable(self) -> None:
        # A guard on the test itself: if a refactor renames the Swift accessors, the check above
        # would silently pass by finding nothing to compare.
        self.assertEqual(sorted(self._publishing_workers()),
                         ["chat", "image", "music", "subtitle"],
                         "the set of workers publishing a below-tier warning has changed; if that "
                         "is deliberate, update STUDIO_ACCESSOR and this expectation together")
        self.assertTrue({"Chat", "Image", "Music", "Transcription"} <= set(
            re.findall(r"selected(\w+)Model\?\.memoryWarning", VIEWS)),
            "the render sites this invariant is measured against have been renamed or removed")


if __name__ == "__main__":
    unittest.main()
