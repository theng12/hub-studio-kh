"""Native Chat consumes request-scoped measured telemetry without inventing rates."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
FIXTURE = (ROOT / "tests" / "native" / "api_main.swift").read_text()


def body(source: str, marker: str, stop: str = "\n    }") -> str:
    start = source.index(marker)
    end = source.find(stop, start + len(marker))
    return source[start:end if end >= 0 else len(source)]


class NativeChatMetricsWiringTests(unittest.TestCase):
    def test_completion_and_history_decode_the_same_attempt_telemetry(self) -> None:
        self.assertIn("let telemetry: NativeChatTelemetry?", body(API, "private struct HubChatStudio"))
        self.assertIn("let chatTelemetry: NativeChatTelemetry?", body(API, "private struct HubJobItem"))
        mapping = body(API, "private static func mapHubJob(_ wire: HubJobDetail)")
        self.assertIn("chatTelemetry: item?.chatTelemetry", mapping)
        send = body(API, "func sendChat() async {")
        self.assertIn("chatTelemetry: reply.telemetry", send)
        self.assertIn('"chat_telemetry"', FIXTURE)

    def test_display_uses_worker_rates_and_keeps_legacy_times_labelled(self) -> None:
        metrics = body(API, "static func chatMetrics(")
        self.assertIn("promptTps", metrics)
        self.assertIn("generationTps", metrics)
        self.assertIn("worker completion", metrics)
        self.assertIn("round trip", metrics)
        self.assertIn('String(format: "%.1f", value)', body(API, "static func rateText"))
        self.assertIn('String(format: "%.3g", value)', body(API, "static func rateText"))
        self.assertIn("MLX process peak", metrics)
        self.assertNotRegex(metrics, r"generationTokens\)?\s*/")
        self.assertNotRegex(metrics, r"completionTokens\)?\s*/")
        self.assertIn("if telemetry.finalAttempt > 1", metrics)

    def test_live_activity_is_scoped_to_the_turn_and_never_uses_generation_percent(self) -> None:
        request = body(API, "private struct HubChatRequest: Encodable")
        self.assertIn("let jobID: String", request)
        send = body(API, "func sendChat() async {")
        self.assertIn("jobID: turn.id", send)
        poll = body(API, "private func pollChatActivity(")
        self.assertIn("activity.id == jobID", poll)
        request = body(API, "private func getChatActivity(")
        self.assertIn('["api", "hub", "chat", "activity"]', request)
        self.assertIn('URLQueryItem(name: "job_id", value: jobID)', request)
        activity = body(API, "struct NativeChatActivity:")
        self.assertIn("totalPromptTokens > 0", activity)
        self.assertIn('case "generating": return nil', activity)

    def test_the_chat_page_draws_an_accessible_measured_phase(self) -> None:
        page = body(VIEWS, "private var chatWorkspace: some View", stop="\n    private var imageWorkspace")
        self.assertIn("workspace.chatActivity", page)
        self.assertIn("activity.statusText", page)
        self.assertIn("activity.progress", page)
        self.assertIn("accessibilityLabel", page)

    def test_every_chat_poll_lifecycle_boundary_cancels_it(self) -> None:
        disconnect = body(API, "func disconnect() {")
        clear = body(API, "func clearChat() {")
        send = body(API, "func sendChat() async {")
        self.assertIn("cancelChatActivity()", disconnect)
        self.assertIn("cancelChatActivity()", clear)
        self.assertIn("cancelChatActivity(requestToken: requestToken)", send)


if __name__ == "__main__":
    unittest.main()
