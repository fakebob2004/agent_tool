from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


SEMANTIC_MARKER = "TASKBUS_SEMANTIC_QUESTION:"


def send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


def main() -> int:
    session_id = "sess_semantic"
    prompt_count = 0
    for line in sys.stdin:
        data = json.loads(line)
        method = data.get("method")
        request_id = data.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": session_id}})
        elif method == "session/set_mode":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "session/prompt":
            prompt_count += 1
            if prompt_count == 1:
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {
                                    "type": "text",
                                    "text": (
                                        f"{SEMANTIC_MARKER} Existing tests require empty input to raise "
                                        "ValueError, but the new requirement says empty input should be "
                                        "safely handled. Which behavior should I preserve?"
                                    ),
                                },
                            },
                        },
                    }
                )
            else:
                prompt_text = data.get("params", {}).get("prompt", "")
                if "ValueError" in str(prompt_text):
                    Path("parser.py").write_text(
                        "def parse_items(text):\n"
                        "    if text == \"\":\n"
                        "        raise ValueError(\"empty input\")\n"
                        "    return text.split(\",\")\n",
                        encoding="utf-8",
                    )
                    message = "implemented after liaison"
                else:
                    message = "missing liaison instruction"
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": message},
                            },
                        },
                    }
                )
            send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
