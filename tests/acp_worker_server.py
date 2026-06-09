from __future__ import annotations

import json
import sys
from pathlib import Path


def send(message: dict[str, object]) -> None:
    print(json.dumps(message), flush=True)


def main() -> int:
    session_id = "sess_worker"
    for line in sys.stdin:
        data = json.loads(line)
        method = data.get("method")
        request_id = data.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": session_id}})
        elif method == "session/set_mode":
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {"sessionUpdate": "current_mode_update", "currentModeId": "agent"},
                    },
                }
            )
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "session/prompt":
            permission_id = 99
            send(
                {
                    "jsonrpc": "2.0",
                    "id": permission_id,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": session_id,
                        "toolCall": {
                            "toolCallId": "tool_test",
                            "title": "`python -c \"from calc import add; assert add(2, 3) == 5\"`",
                            "kind": "execute",
                            "status": "pending",
                        },
                        "options": [
                            {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
                        ],
                    },
                }
            )
            response = json.loads(sys.stdin.readline())
            if response.get("id") == permission_id:
                Path("calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "done"},
                        },
                    },
                }
            )
            send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
