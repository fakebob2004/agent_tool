from __future__ import annotations

import unittest
from pathlib import Path

from taskbus.acp_permission import AcpPermissionBroker
from taskbus.cursor_acp import parse_permission_request


def permission_message(tool_call: dict[str, object]) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "session/request_permission",
        "params": {
            "sessionId": "sess",
            "toolCall": tool_call,
            "options": [
                {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "allow-always", "name": "Allow always", "kind": "allow_always"},
                {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
            ],
        },
    }


class AcpPermissionBrokerTests(unittest.TestCase):
    def test_allows_explicit_pytest_command(self) -> None:
        request = parse_permission_request(
            permission_message(
                {
                    "title": "`cd /tmp/taskbus-acp-smoke && pytest`",
                    "kind": "execute",
                    "status": "pending",
                }
            )
        )
        decision = AcpPermissionBroker("/tmp/taskbus-acp-smoke").decide(request)

        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.option_id, "allow-once")
        self.assertEqual(
            decision.json_rpc_response(request.request_id),
            {
                "jsonrpc": "2.0",
                "id": 0,
                "result": {"outcome": {"outcome": "selected", "optionId": "allow-once"}},
            },
        )

    def test_denies_dependency_install(self) -> None:
        request = parse_permission_request(
            permission_message(
                {
                    "rawInput": {"command": "cd /tmp/taskbus-acp-smoke && pip install pytest"},
                    "kind": "execute",
                    "status": "pending",
                }
            )
        )
        decision = AcpPermissionBroker("/tmp/taskbus-acp-smoke").decide(request)

        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.option_id, "reject-once")
        self.assertIn("denied shell pattern", decision.reason)

    def test_allows_edit_inside_scope(self) -> None:
        request = parse_permission_request(
            permission_message(
                {
                    "kind": "edit",
                    "status": "pending",
                    "content": [{"type": "diff", "path": "/tmp/taskbus-acp-smoke/calc.py"}],
                }
            )
        )
        decision = AcpPermissionBroker("/tmp/taskbus-acp-smoke", allowed_paths=["calc.py"]).decide(request)

        self.assertEqual(decision.action, "allow")

    def test_denies_edit_outside_scope(self) -> None:
        request = parse_permission_request(
            permission_message(
                {
                    "kind": "edit",
                    "status": "pending",
                    "content": [{"type": "diff", "path": "/tmp/taskbus-acp-smoke/secret.txt"}],
                }
            )
        )
        decision = AcpPermissionBroker(Path("/tmp/taskbus-acp-smoke"), allowed_paths=["calc.py"]).decide(request)

        self.assertEqual(decision.action, "deny")


if __name__ == "__main__":
    unittest.main()
