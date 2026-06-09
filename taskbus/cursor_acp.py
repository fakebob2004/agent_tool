from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any, BinaryIO, Iterable, Sequence

from .acp_framing import FramingError, JsonLinesFramer, MessageFramer


UPDATE_CATEGORY_BY_KIND = {
    "agent_message_chunk": "agent_message_chunk",
    "available_commands_update": "session_info",
    "current_mode_update": "session_info",
    "plan": "plan",
    "session_info_update": "session_info",
    "tool_call": "tool_call",
    "tool_call_update": "tool_call_update",
    "user_message_chunk": "user_message_chunk",
}


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


@dataclass(frozen=True)
class AcpSessionUpdate:
    session_id: str
    kind: str
    update: dict[str, Any]

    @property
    def category(self) -> str:
        return UPDATE_CATEGORY_BY_KIND.get(self.kind, "unknown")

    @property
    def is_unknown(self) -> bool:
        return self.category == "unknown"

    @property
    def text_delta(self) -> str | None:
        if self.kind != "agent_message_chunk":
            return None
        content = self.update.get("content")
        if not isinstance(content, dict):
            return None
        if content.get("type") != "text":
            return None
        text = content.get("text")
        return text if isinstance(text, str) else None

    @property
    def title(self) -> str | None:
        if self.kind != "session_info_update":
            return None
        title = self.update.get("title")
        return title if isinstance(title, str) else None


@dataclass(frozen=True)
class AcpPermissionRequest:
    request_id: int | str
    session_id: str
    tool_call: dict[str, Any]
    options: list[dict[str, Any]]
    params: dict[str, Any]


@dataclass
class AcpPromptTranscript:
    text: str = ""
    stop_reason: str | None = None
    updates: list[AcpSessionUpdate] = field(default_factory=list)
    update_counts: dict[str, int] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_updates: list[dict[str, Any]] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    session_info: list[dict[str, Any]] = field(default_factory=list)
    unknown_updates: list[dict[str, Any]] = field(default_factory=list)
    permission_requests: list[AcpPermissionRequest] = field(default_factory=list)
    unknown_agent_requests: list[dict[str, Any]] = field(default_factory=list)

    def apply_update(self, update: AcpSessionUpdate) -> None:
        self.updates.append(update)
        self.update_counts[update.kind] = self.update_counts.get(update.kind, 0) + 1
        text_delta = update.text_delta
        if text_delta is not None:
            self.text += text_delta
        if update.category == "tool_call":
            self.tool_calls.append(update.update)
        elif update.category == "tool_call_update":
            self.tool_call_updates.append(update.update)
        elif update.category == "plan":
            self.plans.append(update.update)
        elif update.category == "session_info":
            self.session_info.append(update.update)
        elif update.is_unknown:
            self.unknown_updates.append(update.update)

    def apply_agent_request(self, message: dict[str, Any]) -> None:
        if message.get("method") == "session/request_permission":
            self.permission_requests.append(parse_permission_request(message))
        elif "method" in message and "id" in message:
            self.unknown_agent_requests.append(message)

    def apply_response(self, response: JsonRpcResponse) -> None:
        if isinstance(response.result, dict):
            stop_reason = response.result.get("stopReason")
            if isinstance(stop_reason, str):
                self.stop_reason = stop_reason


def parse_session_update(message: JsonRpcNotification | dict[str, Any]) -> AcpSessionUpdate:
    if isinstance(message, JsonRpcNotification):
        method = message.method
        params = message.params
    else:
        method = message.get("method")
        params = message.get("params", {})
    if method != "session/update":
        raise AcpProtocolError("Expected session/update notification.")
    if not isinstance(params, dict):
        raise AcpProtocolError("session/update params must be an object.")
    session_id = params.get("sessionId")
    update = params.get("update")
    if not isinstance(session_id, str) or not session_id:
        raise AcpProtocolError("session/update missing sessionId.")
    if not isinstance(update, dict):
        raise AcpProtocolError("session/update missing update object.")
    kind = update.get("sessionUpdate")
    if not isinstance(kind, str) or not kind:
        raise AcpProtocolError("session/update missing sessionUpdate kind.")
    return AcpSessionUpdate(session_id=session_id, kind=kind, update=update)


def parse_permission_request(message: dict[str, Any]) -> AcpPermissionRequest:
    if message.get("jsonrpc") != "2.0":
        raise AcpProtocolError("Permission request missing jsonrpc='2.0'.")
    if message.get("method") != "session/request_permission":
        raise AcpProtocolError("Expected session/request_permission.")
    request_id = message.get("id")
    if request_id is None:
        raise AcpProtocolError("Permission request missing id.")
    params = message.get("params")
    if not isinstance(params, dict):
        raise AcpProtocolError("Permission request params must be an object.")
    session_id = params.get("sessionId")
    tool_call = params.get("toolCall")
    options = params.get("options")
    if not isinstance(session_id, str) or not session_id:
        raise AcpProtocolError("Permission request missing sessionId.")
    if not isinstance(tool_call, dict):
        raise AcpProtocolError("Permission request missing toolCall.")
    if not isinstance(options, list) or not all(isinstance(item, dict) for item in options):
        raise AcpProtocolError("Permission request options must be a list of objects.")
    return AcpPermissionRequest(
        request_id=request_id,
        session_id=session_id,
        tool_call=tool_call,
        options=options,
        params=params,
    )


class CursorAcpSession:
    """Minimal JSON-RPC-over-stdio transport for a future Cursor ACP adapter."""

    def __init__(
        self,
        command: str | Sequence[str],
        cwd: Path | str,
        timeout_seconds: float = 10.0,
        framer: MessageFramer | None = None,
    ) -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.timeout_seconds = timeout_seconds
        self.framer = framer or JsonLinesFramer()
        self.process: subprocess.Popen[bytes] | None = None
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
        process.stdin.write(self.framer.encode(request.to_dict()))
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
        while True:
            try:
                data = self.framer.read(process.stdout)
            except EOFError:
                break
            except FramingError:
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
            self._stderr_lines.put(line.decode("utf-8", errors="replace").rstrip("\r\n"))

    def _require_process(self) -> subprocess.Popen[bytes]:
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


def build_new_session_request(
    cwd: Path | str,
    request_id: int | str = 1,
    *,
    mcp_servers: Sequence[dict[str, Any]] | None = None,
    additional_directories: Sequence[Path | str] | None = None,
) -> JsonRpcRequest:
    params: dict[str, Any] = {
        "cwd": str(cwd),
        "mcpServers": list(mcp_servers or []),
    }
    if additional_directories:
        params["additionalDirectories"] = [str(path) for path in additional_directories]
    return JsonRpcRequest(id=request_id, method="session/new", params=params)


def text_content(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def resource_link_content(
    uri: str,
    name: str,
    *,
    mime_type: str | None = None,
    size: int | None = None,
    description: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "resource_link",
        "uri": uri,
        "name": name,
    }
    if mime_type is not None:
        block["mimeType"] = mime_type
    if size is not None:
        block["size"] = size
    if description is not None:
        block["description"] = description
    if title is not None:
        block["title"] = title
    return block


def embedded_text_resource_content(uri: str, text: str, *, mime_type: str | None = None) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "uri": uri,
        "text": text,
    }
    if mime_type is not None:
        resource["mimeType"] = mime_type
    return {"type": "resource", "resource": resource}


def build_prompt_request(
    session_id: str,
    prompt: str | Iterable[dict[str, Any]],
    request_id: int | str = 1,
    *,
    message_id: str | None = None,
) -> JsonRpcRequest:
    if isinstance(prompt, str):
        prompt_blocks = [text_content(prompt)]
    else:
        prompt_blocks = list(prompt)
    params: dict[str, Any] = {
        "sessionId": session_id,
        "prompt": prompt_blocks,
    }
    if message_id is not None:
        params["messageId"] = message_id
    return JsonRpcRequest(id=request_id, method="session/prompt", params=params)


def build_set_session_mode_request(
    session_id: str,
    mode_id: str,
    request_id: int | str = 1,
) -> JsonRpcRequest:
    return JsonRpcRequest(
        id=request_id,
        method="session/set_mode",
        params={
            "sessionId": session_id,
            "modeId": mode_id,
        },
    )
