from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Sequence


class AcpError(RuntimeError):
    pass


class AcpTimeoutError(AcpError):
    pass


class AcpProtocolError(AcpError):
    pass


@dataclass(frozen=True)
class JsonRpcRequest:
    id: int | str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }


@dataclass(frozen=True)
class JsonRpcResponse:
    id: int | str | None
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = "2.0"

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonRpcResponse":
        if data.get("jsonrpc") != "2.0":
            raise AcpProtocolError("JSON-RPC response missing jsonrpc='2.0'.")
        if "result" not in data and "error" not in data:
            raise AcpProtocolError("JSON-RPC response must contain result or error.")
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass(frozen=True)
class JsonRpcNotification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonRpcNotification":
        if data.get("jsonrpc") != "2.0":
            raise AcpProtocolError("JSON-RPC notification missing jsonrpc='2.0'.")
        if "id" in data:
            raise AcpProtocolError("JSON-RPC notification must not contain id.")
        method = data.get("method")
        if not isinstance(method, str) or not method:
            raise AcpProtocolError("JSON-RPC notification missing method.")
        params = data.get("params", {})
        if not isinstance(params, dict):
            raise AcpProtocolError("JSON-RPC notification params must be an object.")
        return cls(method=method, params=params)


class CursorAcpSession:
    """Minimal JSON-RPC-over-stdio transport for a future Cursor ACP adapter."""

    def __init__(
        self,
        command: str | Sequence[str],
        cwd: Path | str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._responses: Queue[JsonRpcResponse] = Queue()
        self._notifications: Queue[JsonRpcNotification] = Queue()
        self._stderr_lines: Queue[str] = Queue()
        self._reader_threads: list[threading.Thread] = []

    def start(self) -> None:
        if self.process is not None:
            raise AcpError("ACP session already started.")
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
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._reader_threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self._reader_threads:
            thread.start()

    def request(self, method: str, params: dict[str, Any] | None = None) -> JsonRpcResponse:
        request_id = self._next_id
        self._next_id += 1
        self.send(JsonRpcRequest(id=request_id, method=method, params=params or {}))
        return self.wait_for_response(request_id)

    def send(self, request: JsonRpcRequest) -> None:
        process = self._require_process()
        if process.stdin is None or process.stdin.closed:
            raise AcpError("ACP stdin is closed.")
        process.stdin.write(json.dumps(request.to_dict(), separators=(",", ":")) + "\n")
        process.stdin.flush()

    def wait_for_response(self, request_id: int | str) -> JsonRpcResponse:
        pending: list[JsonRpcResponse] = []
        while True:
            try:
                response = self._responses.get(timeout=self.timeout_seconds)
            except Empty as exc:
                for item in pending:
                    self._responses.put(item)
                raise AcpTimeoutError(f"Timed out waiting for JSON-RPC response id {request_id}.") from exc

            if response.id == request_id:
                for item in pending:
                    self._responses.put(item)
                return response
            pending.append(response)

    def next_notification(self, timeout_seconds: float | None = None) -> JsonRpcNotification:
        try:
            return self._notifications.get(timeout=timeout_seconds or self.timeout_seconds)
        except Empty as exc:
            raise AcpTimeoutError("Timed out waiting for JSON-RPC notification.") from exc

    def stderr_tail(self, limit: int = 20) -> list[str]:
        lines = list(self._stderr_lines.queue)
        return lines[-limit:]

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
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _read_stdout(self) -> None:
        process = self._require_process()
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            try:
                if "id" in data:
                    self._responses.put(JsonRpcResponse.from_dict(data))
                elif "method" in data:
                    self._notifications.put(JsonRpcNotification.from_dict(data))
            except AcpProtocolError:
                continue

    def _read_stderr(self) -> None:
        process = self._require_process()
        assert process.stderr is not None
        for line in process.stderr:
            self._stderr_lines.put(line.rstrip("\n"))

    def _require_process(self) -> subprocess.Popen[str]:
        if self.process is None:
            raise AcpError("ACP session has not been started.")
        return self.process


def build_initialize_request(request_id: int | str = 1) -> JsonRpcRequest:
    return JsonRpcRequest(
        id=request_id,
        method="initialize",
        params={
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {
                    "readTextFile": True,
                    "writeTextFile": True,
                },
                "terminal": True,
            },
            "clientInfo": {
                "name": "taskbus",
                "title": "TaskBus",
                "version": "0.1.0",
            },
        },
    )
