from __future__ import annotations

from collections.abc import Iterator

from .events import BridgeEvent


class DryRunCursorSession:
    """Deterministic worker session used before a real Cursor adapter exists."""

    def __init__(self, task: dict) -> None:
        self.task = task

    def events(self) -> Iterator[BridgeEvent]:
        test_command = self.task.get("test_commands", ["python -m unittest discover"])[0]
        yield BridgeEvent(
            type="permission_request",
            message=f"Worker asks to run: {test_command}",
            payload={"request_type": "shell", "command": test_command},
        )
        yield BridgeEvent(
            type="semantic_question",
            message="Should the implementation preserve existing public API behavior?",
            payload={
                "question": "preserve_public_api",
                "default": "Preserve the existing public API unless the TaskSpec explicitly allows a change.",
            },
        )
        yield BridgeEvent(
            type="finished",
            message="Dry run completed without modifying repository files.",
            payload={"changed_files": [], "tests": "not_run_dry_run"},
        )
