"""A result that cannot be played says *why*, in a sentence, naming the Mac.

**The owner, on a clip that would not play:** *"just give a proper clear reason why it can't be
played. 502 is no proper reason. it must be understandable from a glance. so i know why it didn't
play."*

He was reading `Local engine HTTP 502: worker artifact could not be read`. Two things were wrong
with it and the status code was the smaller one:

* **"502" is a number, not a reason.** It names the layer that failed, not the cause.
* **"worker artifact could not be read" names a component he has never seen.** There is no "worker
  artifact" anywhere in what he uses; there is a clip, and there is the Mac that made it.

**The fact he actually needs is which Mac**, because a finished result is not stored in the Hub. It
sits on the Mac that generated it — a written decision, not a defect (`backend/job_storage.py`:
*"Image and voice generation artifacts live on the worker Macs"*) — and the Hub serves it by
proxying to that Mac. When that Mac is off, the honest sentence says so and says what fixes it.
This is deliberately **not** solved by copying every artifact to the controller: the owner was asked
and declined (*"No not at all. no need to copy."*), so the failure stays and only the explanation
changes.

The two causes are kept apart because their fixes differ — a worker missing from the roster is a Mac
that is off, while a registered worker that does not answer is one that is running and unhealthy —
and a third refusal (a malformed stored reference) is deliberately left as an internal error, since
it is a Hub-side integrity failure the operator neither caused nor can act on.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import standalone  # noqa: E402

STANDALONE = (ROOT / "components" / "hub" / "standalone.py").read_text(encoding="utf-8")
STUDIO_API = (ROOT / "native" / "StudioAPI.swift").read_text(encoding="utf-8")


class TheReasonNamesTheMacTest(unittest.TestCase):
    def test_a_remote_result_names_the_mac_that_holds_it(self) -> None:
        reason = standalone.artifact_unavailable_reason(
            {"studio": "voice@studio-2", "machine": "studio-2"},
            "not answering this Hub")
        self.assertIn("studio-2", reason,
                      "the sentence does not say which Mac has the file, so there is nothing to do "
                      "about it")
        self.assertIn("Start that Mac", reason,
                      "the sentence states the cause but not the action")

    def test_the_mac_is_read_from_the_studio_id_when_the_item_does_not_state_one(self) -> None:
        # `item["machine"]` is not on every row — the studio id carries it as `role@machine`, which
        # is the convention `_item_notified` already reads. A sentence that fell back to "this Mac"
        # for a remote result would be actively misleading: it would send the operator to restart
        # the wrong machine.
        reason = standalone.artifact_unavailable_reason(
            {"studio": "image@kh-mac-04"}, "not running or not connected to this Hub")
        self.assertIn("kh-mac-04", reason)

    def test_a_local_result_says_this_mac_and_the_local_engine(self) -> None:
        # A single-Mac install has no machine name at all, and pointing at "this Mac" is correct
        # there — the fix is this app's own services, not another computer.
        reason = standalone.artifact_unavailable_reason(
            {"studio": "voice"}, "not running or not connected to this Hub")
        self.assertIn("local voice engine", reason)
        self.assertIn("Studio Hub's services", reason)

    def test_the_two_causes_read_differently(self) -> None:
        # Same Mac, different fault, different fix: off/disconnected versus running-and-unhealthy.
        item = {"studio": "voice@studio-2", "machine": "studio-2"}
        gone = standalone.artifact_unavailable_reason(item, "not running or not connected to this Hub")
        silent = standalone.artifact_unavailable_reason(item, "not answering this Hub")
        self.assertNotEqual(gone, silent)
        self.assertIn("not answering this Hub", silent)

    def test_the_reason_is_a_sentence_and_not_a_status_code(self) -> None:
        for item in ({"studio": "voice@studio-2", "machine": "studio-2"},
                     {"studio": "voice"},
                     {"studio": "image@kh-mac-04"}):
            with self.subTest(item=item):
                reason = standalone.artifact_unavailable_reason(item, "not answering this Hub")
                self.assertNotIn("worker artifact", reason,
                                 "the sentence names a component the operator has never seen")
                self.assertIsNone(re.search(r"\b(4\d\d|5\d\d)\b", reason),
                                  f"a status code is still in the sentence: {reason!r}")
                self.assertTrue(reason.rstrip().endswith("."), "the reason is not a sentence")
                self.assertGreater(len(reason), 80, "the reason is too short to explain anything")


class TheRouteUsesTheReasonTest(unittest.TestCase):
    """Both refusal points on the artifact route, pinned where they are written."""

    def test_the_missing_worker_refusal_is_explained(self) -> None:
        route = STANDALONE[STANDALONE.index("async def job_artifact("):]
        route = route[:route.index("\n    @app.")]
        self.assertIn("artifact_unavailable_reason(", route,
                      "the artifact route refuses without explaining again")
        self.assertNotIn('HTTPException(503, "artifact worker is unavailable")', route,
                         "the old terse refusal came back")

    def test_the_unreachable_worker_refusal_is_explained(self) -> None:
        route = STANDALONE[STANDALONE.index("async def job_artifact("):]
        route = route[:route.index("\n    @app.")]
        self.assertNotIn('HTTPException(502, "worker artifact could not be read")', route,
                         "the 502 the owner rejected came back")

    def test_a_malformed_reference_stays_an_internal_refusal(self) -> None:
        # Not every failure is the operator's to fix. A stored reference that does not match the
        # worker's origin is a Hub-side integrity refusal; dressing it in "start that Mac" would
        # send them to restart a Mac that is running perfectly well. Asserted on the raise itself —
        # the *next* refusal down the route legitimately does use the sentence, so a test that
        # looked past this one would be asserting about the wrong statement.
        route = STANDALONE[STANDALONE.index("async def job_artifact("):]
        route = route[:route.index("\n    @app.")]
        refusal = re.search(
            r"raise HTTPException\([^)]*worker artifact reference is invalid[^)]*\)", route)
        self.assertIsNotNone(refusal, "the malformed-reference refusal is gone")
        assert refusal is not None
        self.assertNotIn("artifact_unavailable_reason", refusal.group(0),
                         "a Hub-side integrity refusal is being explained as the operator's fault")


class TheAppShowsTheHubsSentenceTest(unittest.TestCase):
    """The other half: a good sentence prefixed with a status code is still a status code."""

    def setUp(self) -> None:
        self.validate = STUDIO_API[STUDIO_API.index("private func validateResponse("):]
        self.validate = self.validate[:self.validate.index("\n    /// `{\"detail\"")]

    def test_the_hubs_detail_is_the_message(self) -> None:
        self.assertIn("throw NativeAPIError.message(", self.validate,
                      "the Hub's own explanation is no longer shown to the operator")
        self.assertIn("errorDetail(data)", self.validate,
                      "the Hub's `detail` is no longer what the operator reads")

    def test_the_status_code_comes_after_the_sentence_never_before_it(self) -> None:
        # The code is kept, because it is what makes a report diagnosable — but it is appended, so
        # the sentence is what the eye lands on. Asserted as an order, not as an absence: both
        # halves matter and only the order was wrong.
        self.assertNotIn('"Local engine HTTP \\(http.statusCode): \\(detail)"', self.validate,
                         "the message is a status code with a sentence after it again")
        self.assertIn('NativeAPIError.message("\\(detail) (HTTP \\(http.statusCode))")', self.validate,
                      "the status code is no longer carried after the sentence")

    def test_a_refusal_with_no_body_still_says_something(self) -> None:
        # The fallback has to exist: a proxy or a crash can answer with an empty body, and "no
        # message" is worse than a status code.
        self.assertIn("localizedString(forStatusCode:", self.validate,
                      "a bodiless refusal now produces no message at all")


if __name__ == "__main__":
    unittest.main()
