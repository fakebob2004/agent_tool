from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, Sequence

from .events import BridgeEvent

TASKBUS_EVENT_PREFIX = "TASKBUS_EVENT:"


class CursorSession(Protocol):
    def events(self) -> Iterator[BridgeEvent]:
        raise NotImplementedError

    def send(self, text: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


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

    def send(self, text: str) -> None:
        return None

    def close(self) -> None:
        return None


class SubprocessCursorSession:
    """Line-oriented adapter for a real Cursor CLI or compatible worker command."""

    def __init__(
        self,
        command: str | Sequence[str],
        cwd: Path | str,
        initial_prompt: str = "",
        timeout_seconds: int | None = None,
    ) -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.initial_prompt = initial_prompt
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[str] | None = None

    def events(self) -> Iterator[BridgeEvent]:
        use_shell = isinstance(self.command, str)
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            shell=use_shell,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if self.initial_prompt:
            self.send(self.initial_prompt)

        emitted_finished = False
        assert self.process.stdout is not None
        for line in self.process.stdout:
            event = parse_worker_event_line(line)
            if event is None:
                continue
            emitted_finished = emitted_finished or event.is_finished
            yield event

        try:
            self.process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            self._close_streams()
            yield BridgeEvent(
                type="finished",
                message="Worker process timed out.",
                payload={"returncode": self.process.returncode, "stderr": stderr, "timed_out": True},
            )
            return

        stderr = self.process.stderr.read() if self.process.stderr is not None else ""
        self._close_streams()
        if not emitted_finished:
            yield BridgeEvent(
                type="finished",
                message="Worker process exited.",
                payload={"returncode": self.process.returncode, "stderr": stderr},
            )

    def send(self, text: str) -> None:
        if self.process is None or self.process.stdin is None or self.process.stdin.closed:
            return
        self.process.stdin.write(text)
        self.process.stdin.write("\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        self._close_streams()

    def _close_streams(self) -> None:
        if self.process is None:
            return
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def parse_worker_event_line(line: str) -> BridgeEvent | None:
    stripped = line.strip()
    if not stripped.startswith(TASKBUS_EVENT_PREFIX):
        return None

    raw = stripped[len(TASKBUS_EVENT_PREFIX) :].strip()
    data: dict[str, Any] = json.loads(raw)
    return BridgeEvent(
        type=data["type"],
        message=str(data.get("message", "")),
        payload=dict(data.get("payload", {})),
    )
