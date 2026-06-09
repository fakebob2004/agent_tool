from __future__ import annotations

import json
from typing import Any, BinaryIO, Protocol


class FramingError(ValueError):
    pass


class MessageFramer(Protocol):
    def encode(self, message: dict[str, Any]) -> bytes:
        raise NotImplementedError

    def read(self, stream: BinaryIO) -> dict[str, Any]:
        raise NotImplementedError


class JsonLinesFramer:
    def encode(self, message: dict[str, Any]) -> bytes:
        return json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"

    def read(self, stream: BinaryIO) -> dict[str, Any]:
        line = stream.readline()
        if line == b"":
            raise EOFError
        stripped = line.strip()
        if not stripped:
            raise FramingError("Empty JSON Lines message.")
        return _loads_object(stripped)


class ContentLengthFramer:
    def encode(self, message: dict[str, Any]) -> bytes:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        return header + body

    def read(self, stream: BinaryIO) -> dict[str, Any]:
        headers: dict[str, str] = {}
        while True:
            line = stream.readline()
            if line == b"":
                raise EOFError
            if line in (b"\r\n", b"\n"):
                break
            try:
                name, value = line.decode("ascii").split(":", 1)
            except ValueError as exc:
                raise FramingError(f"Invalid header line: {line!r}") from exc
            headers[name.strip().lower()] = value.strip()

        raw_length = headers.get("content-length")
        if raw_length is None:
            raise FramingError("Content-Length header is required.")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise FramingError(f"Invalid Content-Length value: {raw_length!r}") from exc
        if length < 0:
            raise FramingError("Content-Length must not be negative.")

        body = _read_exact(stream, length)
        return _loads_object(body)


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if chunk == b"":
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _loads_object(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FramingError(f"Invalid JSON message: {exc}") from exc
    if not isinstance(data, dict):
        raise FramingError("Framed JSON message must be an object.")
    return data
