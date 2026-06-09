from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taskbus.acp_framing import ContentLengthFramer, FramingError, JsonLinesFramer, MessageFramer
from taskbus.cursor_acp import (
    JsonRpcRequest,
    build_initialize_request,
    build_new_session_request,
    build_set_session_mode_request,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(output: Path, direction: str, message: dict[str, Any] | str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "direction": direction,
                    "timestamp": utc_now(),
                    "message": message,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def reader_thread(
    stream,
    framer: MessageFramer,
    output: Path,
    queue: Queue[dict[str, Any]],
) -> None:
    while True:
        try:
            message = framer.read(stream)
        except EOFError:
            break
        except FramingError as exc:
            record(output, "agent_to_client_error", str(exc))
            continue
        record(output, "agent_to_client", message)
        queue.put(message)


def stderr_thread(stream, output: Path) -> None:
    for line in stream:
        record(output, "agent_stderr", line.decode("utf-8", errors="replace").rstrip("\n"))


def build_framer(name: str) -> MessageFramer:
    if name == "jsonlines":
        return JsonLinesFramer()
    if name == "content-length":
        return ContentLengthFramer()
    raise ValueError(f"Unknown framing: {name}")


def send_request(process: subprocess.Popen[bytes], framer: MessageFramer, output: Path, request: JsonRpcRequest) -> None:
    assert process.stdin is not None
    message = request.to_dict()
    record(output, "client_to_agent", message)
    process.stdin.write(framer.encode(message))
    process.stdin.flush()


def wait_for_response(
    output: Path,
    messages: Queue[dict[str, Any]],
    timeout: float,
    label: str,
    request_id: int | str,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            record(
                output,
                "probe_result",
                {
                    "label": label,
                    "received_response": False,
                    "request_id": request_id,
                    "timeout_seconds": timeout,
                },
            )
            return None
        try:
            message = messages.get(timeout=remaining)
        except Empty:
            continue
        if message.get("id") == request_id:
            record(
                output,
                "probe_result",
                {
                    "label": label,
                    "received_response": True,
                    "request_id": request_id,
                    "message": message,
                },
            )
            return message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Cursor Agent ACP without TaskBus mapping.")
    parser.add_argument(
        "--command",
        nargs="+",
        default=["agent", "acp"],
        help="ACP command to launch, default: agent acp.",
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Working directory for the ACP process.",
    )
    parser.add_argument(
        "--framing",
        choices=["jsonlines", "content-length"],
        default="jsonlines",
        help="Message framing to try.",
    )
    parser.add_argument(
        "--output",
        default="taskbus/state/cursor_acp_probe.jsonl",
        help="JSONL file for raw probe transcript.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the first response after initialize.",
    )
    parser.add_argument(
        "--new-session-cwd",
        default=None,
        help="If set, send session/new with this absolute ACP cwd after initialize responds.",
    )
    parser.add_argument(
        "--session-mode",
        default=None,
        help="If set with --new-session-cwd, send session/set_mode for the created session.",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    if output.exists():
        output.unlink()

    framer = build_framer(args.framing)
    try:
        process = subprocess.Popen(
            args.command,
            cwd=Path(args.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        record(output, "process_start_error", {"command": args.command, "error": str(exc)})
        print(str(output))
        return 127
    record(output, "process_started", {"command": args.command, "pid": process.pid})
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    messages: Queue[dict[str, Any]] = Queue()
    stdout_thread = threading.Thread(
        target=reader_thread,
        args=(process.stdout, framer, output, messages),
        daemon=True,
    )
    err_thread = threading.Thread(target=stderr_thread, args=(process.stderr, output), daemon=True)
    stdout_thread.start()
    err_thread.start()

    exit_code = 1
    try:
        send_request(process, framer, output, build_initialize_request(request_id=1))
        initialize_response = wait_for_response(output, messages, args.timeout, "initialize", 1)
        if initialize_response is None:
            exit_code = 2
        elif args.new_session_cwd is None:
            exit_code = 0
        else:
            send_request(
                process,
                framer,
                output,
                build_new_session_request(cwd=args.new_session_cwd, request_id=2),
            )
            new_session_response = wait_for_response(output, messages, args.timeout, "session/new", 2)
            if new_session_response is None:
                exit_code = 2
            elif args.session_mode is None:
                exit_code = 0
            else:
                session_id = new_session_response.get("result", {}).get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    record(output, "probe_result", {"label": "session/set_mode", "missing_session_id": True})
                    exit_code = 3
                else:
                    send_request(
                        process,
                        framer,
                        output,
                        build_set_session_mode_request(session_id=session_id, mode_id=args.session_mode, request_id=3),
                    )
                    set_mode_response = wait_for_response(output, messages, args.timeout, "session/set_mode", 3)
                    exit_code = 0 if set_mode_response is not None else 2
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        record(output, "process_exit", {"returncode": process.returncode})

    print(str(output))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
