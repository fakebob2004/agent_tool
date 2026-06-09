from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from taskbus.cursor_acp import (
    AcpProtocolError,
    AcpPromptTranscript,
    CursorAcpSession,
    JsonRpcResponse,
    build_initialize_request,
    build_new_session_request,
    build_prompt_request,
    build_set_session_mode_request,
    embedded_text_resource_content,
    parse_permission_request,
    parse_session_update,
    resource_link_content,
    text_content,
)


ROOT = Path(__file__).resolve().parents[1]
ECHO_SERVER = ROOT / "tests" / "acp_echo_server.py"
GOLDEN = ROOT / "_refs" / "open_source_agents" / "python-sdk" / "tests" / "golden"


def load_golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


class CursorAcpTransportTests(unittest.TestCase):
    def test_json_rpc_response_validation(self) -> None:
        response = JsonRpcResponse.from_dict({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        self.assertTrue(response.ok)
        with self.assertRaises(AcpProtocolError):
            JsonRpcResponse.from_dict({"jsonrpc": "2.0", "id": 1})

    def test_initialize_request_shape(self) -> None:
        request = build_initialize_request()
        data = request.to_dict()
        self.assertEqual(data["method"], "initialize")
        self.assertEqual(data["params"]["protocolVersion"], 1)
        self.assertTrue(data["params"]["clientCapabilities"]["fs"]["readTextFile"])

    def test_new_session_request_matches_acp_shape(self) -> None:
        golden = load_golden("new_session_request.json")
        request = build_new_session_request(
            cwd=golden["cwd"],
            mcp_servers=golden["mcpServers"],
        )
        data = request.to_dict()
        self.assertEqual(data["method"], "session/new")
        self.assertEqual(data["params"], golden)

    def test_new_session_request_can_be_minimal(self) -> None:
        request = build_new_session_request(cwd="/repo")
        self.assertEqual(request.to_dict()["params"], {"cwd": "/repo", "mcpServers": []})

    def test_prompt_request_matches_acp_shape(self) -> None:
        golden = load_golden("prompt_request.json")
        request = build_prompt_request(
            session_id=golden["sessionId"],
            prompt=golden["prompt"],
        )
        data = request.to_dict()
        self.assertEqual(data["method"], "session/prompt")
        self.assertEqual(data["params"], golden)

    def test_prompt_helpers_match_content_block_shapes(self) -> None:
        self.assertEqual(text_content("hello"), {"type": "text", "text": "hello"})
        self.assertEqual(
            resource_link_content(
                "file:///home/user/document.pdf",
                "document.pdf",
                mime_type="application/pdf",
                size=1024000,
            ),
            load_golden("content_resource_link.json"),
        )
        self.assertEqual(
            embedded_text_resource_content(
                "file:///home/user/script.py",
                "def hello():\n    print('Hello, world!')",
                mime_type="text/x-python",
            ),
            load_golden("content_resource_text.json"),
        )

    def test_prompt_request_accepts_text_shortcut_and_message_id(self) -> None:
        request = build_prompt_request(
            session_id="sess_abc123def456",
            prompt="Inspect only.",
            message_id="00000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(
            request.to_dict()["params"],
            {
                "sessionId": "sess_abc123def456",
                "prompt": [{"type": "text", "text": "Inspect only."}],
                "messageId": "00000000-0000-4000-8000-000000000001",
            },
        )

    def test_set_session_mode_request_shape(self) -> None:
        request = build_set_session_mode_request(
            session_id="sess_abc123def456",
            mode_id="ask",
            request_id=9,
        )
        self.assertEqual(
            request.to_dict(),
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "session/set_mode",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "modeId": "ask",
                },
            },
        )

    def test_parse_session_update_text_chunks(self) -> None:
        first = parse_session_update(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "TASK"},
                    },
                },
            }
        )
        second = parse_session_update(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "BUS_ACP_OK"},
                    },
                },
            }
        )
        transcript = AcpPromptTranscript()
        transcript.apply_update(first)
        transcript.apply_update(second)
        transcript.apply_response(JsonRpcResponse(id=4, result={"stopReason": "end_turn"}))

        self.assertEqual(transcript.text, "TASKBUS_ACP_OK")
        self.assertEqual(transcript.stop_reason, "end_turn")

    def test_parse_session_update_title_and_validation(self) -> None:
        update = parse_session_update(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "update": {
                        "sessionUpdate": "session_info_update",
                        "title": "Taskbus Acp Ok",
                    },
                },
            }
        )

        self.assertEqual(update.title, "Taskbus Acp Ok")
        self.assertIsNone(update.text_delta)
        with self.assertRaises(AcpProtocolError):
            parse_session_update({"jsonrpc": "2.0", "method": "other", "params": {}})

    def test_prompt_transcript_keeps_structured_and_unknown_updates(self) -> None:
        tool_call = parse_session_update(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "call_001",
                        "kind": "edit",
                        "status": "pending",
                    },
                },
            }
        )
        plan = parse_session_update(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "update": {
                        "sessionUpdate": "plan",
                        "entries": [{"content": "Run tests", "status": "pending"}],
                    },
                },
            }
        )
        unknown = parse_session_update(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "update": {
                        "sessionUpdate": "future_update",
                        "payload": {"keep": True},
                    },
                },
            }
        )

        transcript = AcpPromptTranscript()
        for update in (tool_call, plan, unknown):
            transcript.apply_update(update)

        self.assertEqual(tool_call.category, "tool_call")
        self.assertEqual(plan.category, "plan")
        self.assertTrue(unknown.is_unknown)
        self.assertEqual(transcript.update_counts["tool_call"], 1)
        self.assertEqual(transcript.tool_calls[0]["toolCallId"], "call_001")
        self.assertEqual(transcript.plans[0]["entries"][0]["content"], "Run tests")
        self.assertEqual(transcript.unknown_updates, [{"sessionUpdate": "future_update", "payload": {"keep": True}}])

    def test_prompt_transcript_tracks_agent_thought_chunks(self) -> None:
        thought = parse_session_update(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_abc123def456",
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "The shell was rejected."},
                    },
                },
            }
        )
        transcript = AcpPromptTranscript()
        transcript.apply_update(thought)

        self.assertEqual(thought.category, "agent_thought_chunk")
        self.assertEqual(transcript.thoughts, "The shell was rejected.")
        self.assertEqual(transcript.unknown_updates, [])

    def test_parse_permission_request(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "session/request_permission",
            "params": load_golden("request_permission_request.json"),
        }
        request = parse_permission_request(message)
        transcript = AcpPromptTranscript()
        transcript.apply_agent_request(message)

        self.assertEqual(request.request_id, 0)
        self.assertEqual(request.session_id, "sess_abc123def456")
        self.assertEqual(request.tool_call["toolCallId"], "call_001")
        self.assertEqual(request.options[0]["optionId"], "allow-once")
        self.assertEqual(transcript.permission_requests[0], request)

    def test_prompt_transcript_keeps_unknown_agent_requests(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": "server-1",
            "method": "session/future_request",
            "params": {"ok": True},
        }
        transcript = AcpPromptTranscript()
        transcript.apply_agent_request(message)

        self.assertEqual(transcript.unknown_agent_requests, [message])

    def test_request_response_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("initialize", {"protocolVersion": 1})
            finally:
                session.close()
        self.assertTrue(response.ok)
        self.assertEqual(response.result["method"], "initialize")

    def test_notifications_are_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("notify", {})
                notification = session.next_notification()
            finally:
                session.close()
        self.assertTrue(response.ok)
        self.assertEqual(notification.method, "session/update")

    def test_stderr_tail_is_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("stderr", {})
            finally:
                session.close()
        self.assertTrue(response.ok)
        self.assertIn("stderr message", session.stderr_tail())

    def test_error_response_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("error", {})
            finally:
                session.close()
        self.assertFalse(response.ok)
        self.assertEqual(response.error["message"], "forced error")

    def test_session_methods_round_trip_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                new_session = build_new_session_request(cwd=tmp, request_id=7)
                session.send(new_session)
                new_session_response = session.wait_for_response(7)

                prompt = build_prompt_request(
                    session_id="sess_abc123def456",
                    prompt="Report status only.",
                    request_id=8,
                )
                session.send(prompt)
                prompt_response = session.wait_for_response(8)
            finally:
                session.close()

        self.assertTrue(new_session_response.ok)
        self.assertEqual(new_session_response.result["method"], "session/new")
        self.assertTrue(prompt_response.ok)
        self.assertEqual(prompt_response.result["method"], "session/prompt")


if __name__ == "__main__":
    unittest.main()
