"""The native Chat page must be wired to the Hub route, not just present.

The private wire structs cannot be reached from the Swift fixture, so — as the peer-path and
wire-encoding suites already do — these pin the source: Chat is in `Create`, the page routes to
the Hub's local chat route, and the snake_case encoder the Hub reads is used rather than a
hand-written key map.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
SHELL = (ROOT / "native" / "ShellCore.swift").read_text()


class NativeChatWiringTests(unittest.TestCase):
    def test_chat_is_a_derived_create_page_not_a_hand_edited_list(self) -> None:
        """The sidebar is derived from the function enum, so a new Studio lands without an edit.

        This used to require `.chat` inside a literal ``static let create: [Self] = [...]`` — one
        of four hand-edited per-Studio lists, and the shape that left Chat in neither the sidebar
        summary nor the status line. The page set is now derived from `StudioFunction.allCases`
        and `hasEngine`, so the invariant is that an engine-backed Studio brings its page along.
        """
        create = re.search(r"static let create: \[Self\] = \{(.*?)\}\(\)", SHELL, re.S)
        self.assertIsNotNone(create, "StudioPage.create is no longer derived")
        self.assertIn("StudioFunction.allCases", create.group(1))
        self.assertIn("hasEngine", create.group(1))
        self.assertNotIn(".chat", create.group(1),
                         "a hand-edited per-Studio page list came back")
        engine = re.search(r"var hasEngine: Bool \{(.*?)\n    \}", SHELL, re.S)
        self.assertIsNotNone(engine, "StudioFunction.hasEngine not found")
        self.assertIn(".chat", engine.group(1),
                      "Chat is not answered as an engine-backed Studio")
        later = re.search(r"static let comingLater: \[Self\] = (.*?)\n    static", SHELL, re.S)
        self.assertIsNotNone(later)
        self.assertIn("!$0.hasEngine", later.group(1),
                      "the coming-later section is a hand-edited list again")

    def test_the_page_switch_draws_the_chat_workspace(self) -> None:
        self.assertIn("case .chat: chatWorkspace", VIEWS)
        self.assertIn("private var chatWorkspace: some View", VIEWS)
        self.assertIn("workspace.sendChat()", VIEWS)

    def test_the_ready_status_is_derived_from_the_pages_own_studio(self) -> None:
        """A page whose Studio has an engine is a workspace; a page-name list is the old trap."""
        status = re.search(r"private func workspaceStatus.*?\n    \}", VIEWS, re.S)
        self.assertIsNotNone(status)
        body = status.group(0)
        self.assertIn("page.function", body,
                      "workspaceStatus reads a list of page names again, so a new Studio's "
                      "page would read 'Engine not installed'")
        self.assertIn("hasEngine", body)

    def test_send_chat_posts_to_the_hub_local_chat_route(self) -> None:
        method = re.search(r"func sendChat\(\) async \{.*?\n    \}", API, re.S)
        self.assertIsNotNone(method, "sendChat not found")
        body = method.group(0)
        self.assertIn('["api", "hub", "chat", "completions"]', body)
        self.assertIn("HubChatCompletion", body)

    def test_the_chat_request_struct_carries_what_the_hub_reads(self) -> None:
        request = re.search(r"private struct HubChatRequest: Encodable \{(.*?)\}", API, re.S)
        self.assertIsNotNone(request, "HubChatRequest not found")
        fields = set(re.findall(r"let ([a-zA-Z]+):", request.group(1)))
        self.assertEqual(fields, {"model", "messages", "stream"})

    def test_the_chat_response_reads_the_openai_message_content(self) -> None:
        completion = re.search(r"private struct HubChatCompletion: Decodable \{(.*?)\n\}", API, re.S)
        self.assertIsNotNone(completion, "HubChatCompletion not found")
        self.assertIn("choices", API)
        self.assertIn("struct Message: Decodable { let content: String }", API)

    def test_send_is_gated_on_a_ready_chat_model(self) -> None:
        self.assertIn("var chatModel", API)
        self.assertIn("selectedChatModel", VIEWS)
        start = VIEWS.index('Button("Send")')
        send = VIEWS[start:start + 700]
        for condition in ("supported != true", "cached != true", "runtimeReady != true"):
            self.assertIn(condition, send,
                          f"the Send button is not gated on {condition}")

    def test_a_retired_chat_model_is_not_kept_selected(self) -> None:
        reconcile = re.search(r"private func reconcileSelections.*?\n    \}", API, re.S)
        self.assertIsNotNone(reconcile, "reconcileSelections not found")
        self.assertIn("chatModel", reconcile.group(0))

    def test_chat_turns_are_published_state_not_a_local_only_buffer(self) -> None:
        for field in ("chatPrompt", "chatTurns", "submittingChat"):
            self.assertIn(field, API)


if __name__ == "__main__":
    unittest.main()
