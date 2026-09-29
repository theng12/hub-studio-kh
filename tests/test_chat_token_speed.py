"""Tokens per second on Chat, derived only from the number the worker stated.

The owner asked for this to measure generation speed: *"now for the LLM, i also want it to display
the tokens per seconds too. that allows me to measure the speed of generation."*

The trap is which seconds. A Chat item carries two timings that look alike and are not: `runtime_s`,
the Hub's own round trip around the proxied call, and `generation_s`, the time the Chat worker
states *it* spent generating. On a cold turn the round trip includes the model load - measured 11.98
s round trip where generation was ~7.2 s - so a rate computed from it would understate the model and
quietly change meaning on the next reply. The rate here is `completion_tokens / generation_s` and
nothing else; when the worker stated no generation time the row shows the token count and the round
trip labelled as the round trip, or nothing.

The wire half is pinned here (the JSON keys the Hub writes and the app decodes); the behaviour half
- a fixture job with `generation_s`, one without, and a live reply with and without
`studio_hub.elapsed_seconds` - is in `tests/native/api_main.swift`.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
NATIVE = (ROOT / "tests" / "native" / "api_main.swift").read_text()
CHAT_JOBS = (ROOT / "components" / "hub" / "backend" / "chat_jobs.py").read_text()


def without_comments(swift: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in swift.splitlines())


def body(of: str, marker: str, stop: str = "\n    }") -> str:
    text = without_comments(of)
    start = text.index(marker)
    end = text.find(stop, start + len(marker))
    return text[start:end if end >= 0 else len(text)]


class ChatTokenSpeedSourceTests(unittest.TestCase):
    def test_the_rate_comes_from_the_stated_generation_time_and_never_the_round_trip(self) -> None:
        speed = body(API, "static func tokenSpeed(completionTokens: Int?, statedGenerationSeconds: Double?,\n                           roundTripSeconds: Double?) -> String {")
        self.assertIn("Double(completionTokens) / stated", speed,
                      "the rate must be completion_tokens over the worker's stated time")
        for wrong in ("/ roundTripSeconds", "/ roundTrip", "completionTokens) / generationSeconds"):
            with self.subTest(wrong=wrong):
                self.assertNotIn(wrong, speed,
                                 "a rate is being derived from a number the worker did not state")
        # No token count, no line at all - not a zero the worker never published.
        self.assertIn("guard let completionTokens, completionTokens > 0 else { return \"\" }", speed,
                      "an absent token count must publish nothing, not a rate")
        # No stated time: the round trip is named as the round trip, and there is no `tok/s`.
        fallback = speed[speed.index("guard let roundTripSeconds"):]
        self.assertIn("round trip", fallback,
                      "the round trip must be labelled as the round trip, not shown as a rate")
        self.assertNotIn("tok/s", fallback,
                         "the fallback branch must not print a rate")
        # The only formatter of a rate in the app. Comments are stripped first: the docstrings are
        # allowed to name the shape they produce.
        self.assertEqual(without_comments(API).count("tok/s"), 2,
                         "a second place formats tokens per second; there must be one")
        self.assertEqual(without_comments(VIEWS).count("tok/s"), 0,
                         "a view formats a rate itself instead of using NativeJob.tokenSpeed")

    def test_both_timings_are_decoded_as_separate_facts(self) -> None:
        item = body(API, "private struct HubJobItem: Decodable")
        self.assertIn("let usage: HubUsage?", item, "the item's usage block is not decoded")
        self.assertIn("let generationS: Double?", item,
                      "the worker's own generation time is not decoded")
        self.assertIn("let runtimeS: Double?", item, "the Hub's round trip must stay decoded")
        mapping = body(API, "private static func mapHubJob(_ wire: HubJobDetail) -> NativeJob")
        self.assertIn("generationSeconds: item?.runtimeS", mapping,
                      "the round trip must stay the round trip")
        self.assertIn("completionTokens: item?.usage?.completionTokens", mapping,
                      "the stated token count must reach the row")
        self.assertIn("statedGenerationSeconds: item?.generationS", mapping,
                      "the stated generation time must reach the row beside the round trip")
        usage = body(API, "private struct HubUsage: Decodable")
        for field in ("promptTokens", "completionTokens", "totalTokens"):
            with self.subTest(field=field):
                self.assertIn(f"let {field}: Int?", usage,
                              f"`{field}` must be optional: an absent count is not zero")

    def test_a_live_reply_carries_the_workers_own_numbers_into_the_transcript(self) -> None:
        completion = body(API, "private struct HubChatCompletion: Decodable", stop="\n}")
        self.assertIn("let usage: HubUsage?", completion,
                      "the live reply's usage block is not decoded")
        self.assertIn("let studioHub: HubChatStudio?", completion,
                      "the live reply's worker-stated timing is not decoded")
        self.assertIn("var statedGenerationSeconds: Double? { studioHub?.elapsedSeconds }", completion,
                      "the live reply's stated time must come from studio_hub.elapsed_seconds")
        studio = body(API, "private struct HubChatStudio: Decodable")
        self.assertIn("let elapsedSeconds: Double?", studio)
        send = body(API, "func sendChat() async {")
        self.assertIn("completionTokens: reply.completionTokens", send,
                      "the reply's stated token count must land on the transcript turn")
        self.assertIn("statedGenerationSeconds: reply.statedGenerationSeconds", send,
                      "the reply's stated generation time must land on the transcript turn")

    def test_the_row_and_the_live_turn_show_it(self) -> None:
        row = body(VIEWS, "private func chatJobRow(_ job: NativeJob) -> some View")
        self.assertIn("job.tokenSpeed", row, "the Chat job row must show the rate")
        # The round trip is shown through the shared line now, not by a copy written into this row:
        # the row keeps only the fact that is its own.
        self.assertIn("jobMetaLine(job)", row,
                      "the Chat row must draw the shared line, which carries the generation time")
        turn = VIEWS[VIEWS.index("if !turn.speed.isEmpty"):]
        turn = turn[:turn.index("\n", turn.index("Text(turn.speed)"))]
        self.assertIn("Text(turn.speed)", turn,
                      "a live reply must show its own rate under the message")

    def test_the_hub_writes_the_keys_the_app_reads(self) -> None:
        self.assertIn('"generation_s": generation_s', CHAT_JOBS,
                      "the Hub no longer records the worker's stated generation time")
        self.assertIn('"usage": usage', CHAT_JOBS,
                      "the Hub no longer records the worker's usage block")
        self.assertIn("def _generation_seconds", CHAT_JOBS)

    def test_the_fixture_covers_both_timings_and_both_replies(self) -> None:
        self.assertIn('"generation_s"', NATIVE, "the fixture's Chat item must state a generation time")
        self.assertIn("chat-job-nostated", NATIVE,
                      "the fixture must carry the negative control: usage with no generation time")
        self.assertIn("no rate may be derived from the round trip", NATIVE)
        self.assertIn('"studio_hub"', NATIVE,
                      "the fixture's live reply must state studio_hub.elapsed_seconds")
        self.assertIn("a rate must not be invented for a reply that stated no generation time", NATIVE)
        self.assertIn('chatJob.tokenSpeed == "10 tokens · 1.4 tok/s · 7.2 s"', NATIVE,
                      "the exact rendered row string must be asserted")
        self.assertIn('store.chatTurns.last?.speed == "10 tokens · 1.4 tok/s · 7.2 s"', NATIVE,
                      "the exact rendered live-reply string must be asserted")


if __name__ == "__main__":
    unittest.main()
