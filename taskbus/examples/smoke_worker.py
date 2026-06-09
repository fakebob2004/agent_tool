from __future__ import annotations

import json
import sys

PREFIX = "TASKBUS_EVENT:"


def emit(event_type: str, message: str, payload: dict) -> None:
    print(
        PREFIX
        + json.dumps(
            {
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
