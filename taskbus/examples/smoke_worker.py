from __future__ import annotations

import json
import os
import sys

PREFIX = "TASKBUS_EVENT:"
PROTOCOL = os.environ.get("TASKBUS_PROTOCOL", "taskbus-worker-v1")
SESSION_ID = os.environ.get("TASKBUS_SESSION_ID", "")
NONCE = os.environ.get("TASKBUS_NONCE", "")
SEQUENCE = 0


def emit(event_type: str, message: str, payload: dict) -> None:
    global SEQUENCE
    SEQUENCE += 1
    print(
        PREFIX
        + json.dumps(
            {
                "protocol": PROTOCOL,
                "source": "scripted-worker",
                "session_id": SESSION_ID,
                "nonce": NONCE,
                "sequence": SEQUENCE,
                "type": event_type,
                "message": message,
                "payload": payload,
            }
        ),
        flush=True,
    )


def main() -> int:
    emit(
        "semantic_question",
        "Should the worker keep the public API stable?",
        {
            "question": "preserve_public_api",
            "default": "Keep the public API stable and use the smallest scoped change.",
        },
    )
    instruction = sys.stdin.readline().strip()
    emit(
        "finished",
        f"Worker received instruction: {instruction}",
        {
            "changed_files": [],
            "diff_lines": 0,
            "tests": [
                {
                    "command": "smoke-worker self-check",
                    "passed": True,
                    "output_tail": "smoke worker completed",
                }
            ],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
