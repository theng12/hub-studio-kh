"""A published RAM tier must reach the screen, and a host below it must not advertise the model.

Premise changed twice. Owner decision 2026-09-30 made the Hub treat a model's RAM requirement as a
hard eligibility floor (`memory_admission.requirement_gb`; the scheduler counts a Mac below it out of
the model's pool and never dispatches to it) while the *worker* kept publishing the warning and
still answering a request made straight to it. Owner decision 2026-10-19 (8 GB Mac 1605) closed that
last gap: an 8 GB Mac was still advertising Chat as runnable for a 16 GB model, so the Gateway went
on offering it for install there. The Chat worker now reports the host **not eligible** below its
model's own tier, which makes readiness not ready, `memory_eligible` false, and a request refused
with the same plain sentence the warning carries.

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

Three things are pinned here:

1. The Chat worker publishes the warning where the app reads it, and it says both numbers.
2. A host below the tier is **not eligible**: readiness and `available` are false, `memory_eligible`
   is false, and a request straight to the worker is refused with the warning's own sentence.
3. The invariant behind it, so the next studio cannot publish a warning into the void: every worker
   that puts a ``memory_warning`` on a catalog row must have a page that renders it.

The Hub's own aggregate is pinned here too: a worker that states no memory total (the Chat worker
publishes none) must still have its floor enforced from the machine's own profile, or the published
row is offered as runnable on a Mac the scheduler has already counted out.
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

    def test_a_host_below_the_tier_is_not_eligible_and_is_refused_in_that_sentence(self) -> None:
        """8 GB Mac 1605 advertised Chat as runnable for a 16 GB model, so the Gateway offered it
        for install there. The worker's own tier is an exclusion, and the worker now says so."""
        row = self._row(8)
        warning = row.get("memory_warning")
        self.assertIsNotNone(warning)
        self.assertFalse(row.get("memory_eligible"),
                         "a host below the model's own tier must not publish itself as eligible")
        self.assertFalse(row.get("available"), "an 8 GB Mac must not be offered a 16 GB model")
        self.assertFalse(row.get("execution_ready"))

        # The refusal is the warning's own sentence, not a second, differently-worded gate.
        with tempfile.TemporaryDirectory() as tmp:
            engine = _FakeEngine(verified=True)
            engine.minimum_unified_memory_gb = _manifest_tier_gb()
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            client = TestClient(create_app(engine, policy, TOKEN, "a" * 64))
            with mock.patch("subprocess.check_output",
                            return_value=str(8 * 1024 ** 3)):
                refused = client.post("/v1/chat/completions", headers={"x-studio-token": TOKEN},
                                      json={"model": engine.model_id,
                                            "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("Run it on a 16 GB or larger Mac", refused.json()["detail"])


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
        # Video publishes a warning too, and its page reads `selectedVideoModel` — and unlike the
        # other five, its tier is a routing gate as well as advice: below it the worker publishes
        # `memory_eligible: false`, which is what stops the gateway offering 22B weights to a
        # machine that cannot load them. The sentence and the verdict are one fact, so the page
        # renders the sentence and the Models screen shows the verdict.
        "video": "Video",
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
        # **Voice joined this list on 2026-09-26 (SH-3).** It was the one Studio that published a
        # structured `fit` verdict and no sentence, so the Audio page had nothing to draw — and its
        # warning could never have fired anyway, because `system_info._sysctl` called a bare
        # `sysctl` that is not on PATH, leaving every model's fit verdict "unknown" fleet-wide.
        self.assertEqual(sorted(self._publishing_workers()),
                         ["chat", "image", "music", "subtitle", "video", "voice"],
                         "the set of workers publishing a below-tier warning has changed; if that "
                         "is deliberate, update STUDIO_ACCESSOR and this expectation together")
        self.assertTrue({"Chat", "Image", "Music", "Transcription", "Video", "Audio"} <= set(
            re.findall(r"selected(\w+)Model\?\.memoryWarning", VIEWS)),
            "the render sites this invariant is measured against have been renamed or removed")


class _StandInMonitor:
    """Only what `_assemble_aggregate_catalog` reads, so the Hub's own floor can be driven directly.

    The assembler's docstring says as much: a minimal stand-in is not required to implement the
    registry query. Nothing here is a Hub, a worker or a request.
    """

    def __init__(self, machine: str, health: dict) -> None:
        self.registry = [{"id": "chat", "modality": "chat", "machine": machine, "title": "Chat"}]
        self.status = {"chat": {"status": "up", "health": health}}

    def catalog_observation(self, sid: str) -> dict:
        return {"observed_at": 0.0, "age_seconds": 0.0, "stale": False, "last_error": None}


class HubAggregateEnforcesTheFloorTests(unittest.TestCase):
    """A worker that states no RAM total must still have its floor enforced in the Hub's aggregate.

    8 GB Mac 1605 was offered the 16 GB chat model for install because the Chat worker publishes no
    ``memory.total_gb``, and ``publish_floor`` can enforce nothing without a total: the row went out
    as runnable while the scheduler had already counted that Mac out of the model's pool.
    """

    REPO = "mlx-community/Qwen3.5-9B-4bit"

    def _row(self) -> dict:
        return {"repo": self.REPO, "model_id": self.REPO, "name": "Qwen3.5-9B 4-bit",
                "memory_eligible": True, "min_unified_memory_gb": _manifest_tier_gb()}

    def _published(self, health: dict, profile_gb: float | None) -> dict:
        from components.hub.backend import hardware_profiles, monitor as monitor_module

        with mock.patch.object(hardware_profiles, "machine_hardware_profile",
                               return_value=None if profile_gb is None
                               else {"memory_gb": profile_gb}):
            catalog = monitor_module.StudioMonitor._assemble_aggregate_catalog(
                _StandInMonitor("1101", health), [{"models": [self._row()]}], [])
        rows = [row for row in catalog["models"] if row.get("repo") == self.REPO]
        self.assertEqual(len(rows), 1, catalog["models"])
        return rows[0]

    def test_a_worker_that_states_no_memory_total_falls_back_to_the_machines_own_profile(self) -> None:
        row = self._published({}, 8)
        self.assertIs(False, row.get("memory_eligible"),
                      "an 8 GB Mac must not publish a 16 GB model as eligible just because its "
                      "worker omitted the RAM total")
        self.assertEqual(_manifest_tier_gb(), row.get("min_unified_memory_gb"))

    def test_the_machines_profile_at_the_tier_keeps_the_model_eligible(self) -> None:
        self.assertIs(True, self._published({}, 32).get("memory_eligible"))

    def test_a_reported_total_still_wins_over_the_profile(self) -> None:
        self.assertIs(True, self._published({"memory": {"total_gb": 32}}, 8).get("memory_eligible"))
        self.assertIs(False, self._published({"memory": {"total_gb": 8}}, 32).get("memory_eligible"))

    def test_with_no_evidence_at_all_the_row_is_left_as_the_worker_published_it(self) -> None:
        # Neither the worker nor the profile says how much memory the Mac has: nothing is invented,
        # so nothing is enforced. An assumed 0 would exclude every model on an unassigned machine.
        self.assertIs(True, self._published({}, None).get("memory_eligible"))


if __name__ == "__main__":
    unittest.main()
