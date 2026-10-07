"""Tokens per second on Chat come from the worker's measured attempt telemetry.

The owner asked for this to measure generation speed: *"now for the LLM, i also want it to display
the tokens per seconds too. that allows me to measure the speed of generation."*

The old `elapsed_seconds` includes prompt processing as well as decode, while `runtime_s` is the
Hub's whole round trip. Neither is a decode denominator. The app displays `prompt_tps` and
`generation_tps` exactly as the worker reports them for the final attempt, and keeps old timings as
labelled compatibility facts. Missing telemetry means no speed; literal zero remains visible.

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
    def test_rates_are_worker_fields_and_never_derived_from_a_time(self) -> None:
        speed = body(API, "static func chatMetrics(")
        self.assertIn("final.promptTps", speed)
        self.assertIn("final.generationTps", speed)
        for wrong in ("/ roundTripSeconds", "/ statedCompletionSeconds",
                      "completionTokens) /", "generationTokens) /"):
            with self.subTest(wrong=wrong):
                self.assertNotIn(wrong, speed,
                                 "a token rate is being derived in the native app")
        self.assertIn("worker completion", speed)
        self.assertIn("round trip", speed)
        self.assertIn("value >= 0", body(API, "static func rateText"),
                      "a provider-stated zero is a valid measured rate")
        self.assertEqual(without_comments(VIEWS).count("tok/s"), 0,
                         "a view formats a rate itself instead of using NativeJob.chatMetrics")

    def test_both_timings_are_decoded_as_separate_facts(self) -> None:
        item = body(API, "private struct HubJobItem: Decodable")
        self.assertIn("let usage: HubUsage?", item, "the item's usage block is not decoded")
        self.assertIn("let generationS: Double?", item,
                      "the worker's own generation time is not decoded")
        self.assertIn("let chatTelemetry: NativeChatTelemetry?", item)
        self.assertIn("let runtimeS: Double?", item, "the Hub's round trip must stay decoded")
        mapping = body(API, "private static func mapHubJob(_ wire: HubJobDetail) -> NativeJob")
        self.assertIn("generationSeconds: item?.runtimeS", mapping,
                      "the round trip must stay the round trip")
        self.assertIn("completionTokens: item?.usage?.completionTokens", mapping,
                      "the stated token count must reach the row")
        self.assertIn("statedGenerationSeconds: item?.generationS", mapping,
                      "the stated generation time must reach the row beside the round trip")
        self.assertIn("chatTelemetry: item?.chatTelemetry", mapping)
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
        self.assertIn("let telemetry: NativeChatTelemetry?", studio)
        send = body(API, "func sendChat() async {")
        self.assertIn("completionTokens: reply.completionTokens", send,
                      "the reply's stated token count must land on the transcript turn")
        self.assertIn("statedGenerationSeconds: reply.statedGenerationSeconds", send,
                      "the reply's stated generation time must land on the transcript turn")
        self.assertIn("chatTelemetry: reply.telemetry", send)

    def test_the_row_and_the_live_turn_show_it(self) -> None:
        row = body(VIEWS, "private func chatJobRow(_ job: NativeJob) -> some View")
        self.assertIn("job.chatMetrics", row, "the Chat job row must show the measured facts")
        # The round trip is shown through the shared line now, not by a copy written into this row:
        # the row keeps only the fact that is its own.
        self.assertIn("jobMetaLine(job)", row,
                      "the Chat row must draw the shared line, which carries the generation time")
        turn = VIEWS[VIEWS.index("if !turn.chatMetrics.isEmpty"):]
        turn = turn[:turn.index("\n", turn.index("Text(turn.chatMetrics)"))]
        self.assertIn("Text(turn.chatMetrics)", turn,
                      "a live reply must show its own rate under the message")

    def test_the_hub_writes_the_keys_the_app_reads(self) -> None:
        self.assertIn('"generation_s": generation_s', CHAT_JOBS,
                      "the Hub no longer records the worker's stated generation time")
        self.assertIn('"usage": usage', CHAT_JOBS,
                      "the Hub no longer records the worker's usage block")
        self.assertIn('"chat_telemetry"', CHAT_JOBS,
                      "the Hub no longer records measured attempt telemetry")
        self.assertIn("def _generation_seconds", CHAT_JOBS)

    def test_the_fixture_covers_both_timings_and_both_replies(self) -> None:
        self.assertIn('"generation_s"', NATIVE, "the fixture's Chat item must state a generation time")
        self.assertIn("chat-job-nostated", NATIVE,
                      "the fixture must carry the negative control: usage with no generation time")
        self.assertIn("no rate may be derived from the round trip", NATIVE)
        self.assertIn('"studio_hub"', NATIVE,
                      "the fixture's live reply must state studio_hub.elapsed_seconds")
        self.assertIn("a rate must not be invented for a reply that stated no generation time", NATIVE)
        self.assertIn('chatJob.chatMetrics == "12 prompt tokens · 120.0 prompt tok/s', NATIVE,
                      "the exact rendered row string must be asserted")
        self.assertIn('store.chatTurns.last?.chatMetrics == "12 prompt tokens · 120.0 prompt tok/s', NATIVE,
                      "the exact rendered live-reply string must be asserted")


if __name__ == "__main__":
    unittest.main()
