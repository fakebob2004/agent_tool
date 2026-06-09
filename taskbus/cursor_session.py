from __future__ import annotations

import json
import os
import secrets
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, Sequence
from uuid import uuid4

from .events import BridgeEvent

TASKBUS_EVENT_PREFIX = "TASKBUS_EVENT:"
TASKBUS_PROTOCOL = "taskbus-worker-v1"


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
        secure_event_channel: bool = True,
    ) -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.initial_prompt = initial_prompt
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[str] | None = None
        self.secure_event_channel = secure_event_channel
        self.session_id = str(uuid4())
        self.nonce = secrets.token_urlsafe(24)
        self._last_sequence = 0

    def events(self) -> Iterator[BridgeEvent]:
        use_shell = isinstance(self.command, str)
        env = dict(os.environ)
        if self.secure_event_channel:
            env.update(
                {
                    "TASKBUS_PROTOCOL": TASKBUS_PROTOCOL,
                    "TASKBUS_SESSION_ID": self.session_id,
                    "TASKBUS_NONCE": self.nonce,
                }
            )
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            shell=use_shell,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        if self.initial_prompt:
            self.send(self.initial_prompt)

        emitted_finished = False
        assert self.process.stdout is not None
        for line in self.process.stdout:
            event = parse_worker_event_line(
                line,
                session_id=self.session_id if self.secure_event_channel else None,
                nonce=self.nonce if self.secure_event_channel else None,
                min_sequence=self._last_sequence + 1 if self.secure_event_channel else None,
            )
            if event is None:
                continue
            self._last_sequence += 1
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


def parse_worker_event_line(
    line: str,
    session_id: str | None = None,
    nonce: str | None = None,
    min_sequence: int | None = None,
) -> BridgeEvent | None:
    stripped = line.strip()
    if not stripped.startswith(TASKBUS_EVENT_PREFIX):
        return None

    raw = stripped[len(TASKBUS_EVENT_PREFIX) :].strip()
    data: dict[str, Any] = json.loads(raw)
    if session_id is not None or nonce is not None:
        if data.get("protocol") != TASKBUS_PROTOCOL:
            return None
        if data.get("session_id") != session_id:
            return None
        if data.get("nonce") != nonce:
            return None
        sequence = data.get("sequence")
        if not isinstance(sequence, int):
            return None
        if min_sequence is not None and sequence < min_sequence:
            return None
    return BridgeEvent(
        type=data["type"],
        message=str(data.get("message", "")),
        payload=dict(data.get("payload", {})),
    )
