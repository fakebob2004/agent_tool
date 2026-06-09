from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taskbus.acp_framing import FramingError, JsonLinesFramer, MessageFramer
from taskbus.acp_permission import AcpPermissionBroker
from taskbus.cursor_acp import (
    AcpPromptTranscript,
    JsonRpcRequest,
    JsonRpcResponse,
    build_initialize_request,
    build_new_session_request,
    build_prompt_request,
    build_set_session_mode_request,
    parse_permission_request,
    parse_session_update,
)


PROMPT_TEMPLATE = """Implement add() in calc.py.
Modify no other files.
Run this exact test command:
{test_command}
Stop after the test passes."""


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


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def prepare_repo(repo: Path) -> None:
    resolved = repo.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    in_posix_tmp = str(resolved).startswith("/tmp/")
    in_system_tmp = resolved == temp_root or temp_root in resolved.parents
    if not (in_posix_tmp or in_system_tmp) or not resolved.name.startswith("taskbus-acp-smoke"):
        raise ValueError(f"Refusing to recreate non-smoke repo: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)
    (resolved / "calc.py").write_text("def add(a, b):\n    pass\n", encoding="utf-8")
    (resolved / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "taskbus@example.invalid"],
        ["git", "config", "user.name", "TaskBus Smoke"],
        ["git", "add", "."],
        ["git", "commit", "-m", "baseline"],
    ]
    for command in commands:
        result = run_command(command, resolved)
        if result.returncode != 0:
            raise RuntimeError(f"{' '.join(command)} failed: {result.stderr}")


def reader_thread(stream, framer: MessageFramer, output: Path, queue: Queue[dict[str, Any]]) -> None:
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


def send_request(process: subprocess.Popen[bytes], framer: MessageFramer, output: Path, request: JsonRpcRequest) -> None:
    assert process.stdin is not None
    message = request.to_dict()
    record(output, "client_to_agent", message)
    process.stdin.write(framer.encode(message))
    process.stdin.flush()


def send_message(process: subprocess.Popen[bytes], framer: MessageFramer, output: Path, message: dict[str, Any]) -> None:
    assert process.stdin is not None
    record(output, "client_to_agent", message)
    process.stdin.write(framer.encode(message))
    process.stdin.flush()


def wait_for_response(
    process: subprocess.Popen[bytes],
    framer: MessageFramer,
    output: Path,
    messages: Queue[dict[str, Any]],
    timeout: float,
    label: str,
    request_id: int | str,
    transcript: AcpPromptTranscript | None = None,
    permission_broker: AcpPermissionBroker | None = None,
    policy_decisions: list[dict[str, Any]] | None = None,
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

        if message.get("method") == "session/update" and transcript is not None:
            try:
                transcript.apply_update(parse_session_update(message))
            except Exception as exc:  # pragma: no cover - defensive transcript capture
                record(output, "parse_error", {"label": label, "error": str(exc), "message": message})
        elif "id" in message and "method" in message and transcript is not None:
            try:
                transcript.apply_agent_request(message)
                if message.get("method") == "session/request_permission" and permission_broker is not None:
                    permission_request = parse_permission_request(message)
                    decision = permission_broker.decide(permission_request)
                    policy_record = {
                        "request_id": permission_request.request_id,
                        "tool_kind": permission_request.tool_call.get("kind"),
                        "tool_title": permission_request.tool_call.get("title"),
                        "action": decision.action,
                        "reason": decision.reason,
                        "option_id": decision.option_id,
                    }
                    if policy_decisions is not None:
                        policy_decisions.append(policy_record)
                    record(output, "policy_decision", policy_record)
                    send_message(
                        process,
                        framer,
                        output,
                        decision.json_rpc_response(permission_request.request_id),
                    )
            except Exception as exc:  # pragma: no cover - defensive transcript capture
                record(output, "parse_error", {"label": label, "error": str(exc), "message": message})

        if message.get("id") == request_id and ("result" in message or "error" in message):
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


def verify_repo(repo: Path, python: str) -> dict[str, Any]:
    status = run_command(["git", "status", "--short"], repo)
    diff_files = run_command(["git", "diff", "--name-only"], repo)
    calc_diff = run_command(["git", "diff", "--", "calc.py"], repo)
    test_diff = run_command(["git", "diff", "--", "test_calc.py"], repo)
    pytest = run_command([python, "-B", "-m", "pytest", "-q"], repo)
    changed_files = [line for line in diff_files.stdout.splitlines() if line.strip()]
    return {
        "git_status": status.stdout,
        "changed_files": changed_files,
        "calc_diff": calc_diff.stdout,
        "test_calc_diff": test_diff.stdout,
        "pytest": {
            "returncode": pytest.returncode,
            "stdout": pytest.stdout,
            "stderr": pytest.stderr,
        },
        "passed": (
            status.returncode == 0
            and status.stdout == " M calc.py\n"
            and diff_files.returncode == 0
            and changed_files == ["calc.py"]
            and bool(calc_diff.stdout.strip())
            and not test_diff.stdout.strip()
            and pytest.returncode == 0
        ),
    }


def close_process(process: subprocess.Popen[bytes], output: Path) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    record(output, "process_exit", {"returncode": process.returncode})


def run_smoke(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    python = str(Path(args.python)) if args.python else sys.executable
    test_command = f"{python} -B -m pytest -q"
    output = Path(args.output)
    summary_output = Path(args.summary_output)
    if output.exists():
        output.unlink()
    if summary_output.exists():
        summary_output.unlink()

    prepare_repo(repo)
    framer = JsonLinesFramer()
    process = subprocess.Popen(
        args.command,
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    record(output, "process_started", {"command": args.command, "pid": process.pid})
    assert process.stdout is not None
    assert process.stderr is not None
    messages: Queue[dict[str, Any]] = Queue()
    threading.Thread(target=reader_thread, args=(process.stdout, framer, output, messages), daemon=True).start()
    threading.Thread(target=stderr_thread, args=(process.stderr, output), daemon=True).start()

    transcript = AcpPromptTranscript()
    policy_decisions: list[dict[str, Any]] = []
    permission_broker = (
        AcpPermissionBroker(
            repo,
            allowed_paths=["calc.py"],
            test_commands=[
                test_command,
                f"{python} -m pytest",
                f"{python} -B -m pytest",
                f"cd {repo.as_posix()} && {test_command}",
                f"cd {repo.as_posix()} && {python} -m pytest",
                f"cd {repo.as_posix()} && {python} -B -m pytest",
            ],
            trusted_command_roots=[Path(python).parent.as_posix()],
        )
        if args.auto_permissions
        else None
    )
    prompt_response: dict[str, Any] | None = None
    exit_code = 1
    try:
        send_request(process, framer, output, build_initialize_request(request_id=1))
        if wait_for_response(process, framer, output, messages, args.timeout, "initialize", 1, transcript) is None:
            return 2

        send_request(process, framer, output, build_new_session_request(cwd=str(repo), request_id=2))
        new_session_response = wait_for_response(process, framer, output, messages, args.timeout, "session/new", 2, transcript)
        if new_session_response is None:
            return 2
        session_id = new_session_response.get("result", {}).get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            record(output, "probe_result", {"label": "session", "missing_session_id": True})
            return 3

        if args.session_mode:
            send_request(
                process,
                framer,
                output,
                build_set_session_mode_request(session_id=session_id, mode_id=args.session_mode, request_id=3),
            )
            if wait_for_response(process, framer, output, messages, args.timeout, "session/set_mode", 3, transcript) is None:
                return 2
            prompt_request_id = 4
        else:
            prompt_request_id = 3

        send_request(
            process,
            framer,
            output,
            build_prompt_request(
                session_id=session_id,
                prompt=PROMPT_TEMPLATE.format(test_command=test_command),
                request_id=prompt_request_id,
            ),
        )
        prompt_response = wait_for_response(
            process,
            framer,
            output,
            messages,
            args.prompt_timeout,
            "session/prompt",
            prompt_request_id,
            transcript,
            permission_broker,
            policy_decisions,
        )
        exit_code = 0 if prompt_response is not None else 2
        return exit_code
    finally:
        close_process(process, output)
        verification = verify_repo(repo, python)
        if prompt_response is not None:
            transcript.apply_response(JsonRpcResponse.from_dict(prompt_response))
        summary = {
            "repo": str(repo),
            "python": python,
            "test_command": test_command,
            "prompt_response": prompt_response,
            "transcript": {
                "text": transcript.text,
                "thoughts": transcript.thoughts,
                "stop_reason": transcript.stop_reason,
                "update_counts": transcript.update_counts,
                "tool_calls": transcript.tool_calls,
                "tool_call_updates": transcript.tool_call_updates,
                "plans": transcript.plans,
                "session_info_count": len(transcript.session_info),
                "unknown_updates": transcript.unknown_updates,
                "permission_requests": [asdict(request) for request in transcript.permission_requests],
                "unknown_agent_requests": transcript.unknown_agent_requests,
            },
            "policy_decisions": policy_decisions,
            "verification": verification,
            "passed": (
                exit_code == 0
                and transcript.stop_reason == "end_turn"
                and verification["passed"]
                and not transcript.unknown_updates
            ),
        }
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary_output.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        record(output, "smoke_summary", summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a real Cursor ACP edit smoke in an isolated temp repo.")
    parser.add_argument("--command", nargs="+", default=["agent", "acp"], help="ACP command to launch.")
    parser.add_argument("--repo", default="/tmp/taskbus-acp-smoke", help="Temporary smoke repo path.")
    parser.add_argument("--session-mode", default=None, help="Optional ACP session mode to set before prompting.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Seconds to wait for setup responses.")
    parser.add_argument("--prompt-timeout", type=float, default=180.0, help="Seconds to wait for prompt completion.")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used for pytest verification and allowed test command construction.",
    )
    parser.add_argument(
        "--auto-permissions",
        action="store_true",
        help="Reply to session/request_permission using the static ACP permission broker.",
    )
    parser.add_argument("--output", default="taskbus/state/cursor_acp_edit_smoke.jsonl", help="Raw ignored JSONL log.")
    parser.add_argument(
        "--summary-output",
        default="taskbus/state/cursor_acp_edit_smoke_summary.json",
        help="Ignored smoke summary JSON.",
    )
    args = parser.parse_args(argv)
    try:
        return run_smoke(args)
    except Exception as exc:
        output = Path(args.output)
        record(output, "smoke_error", {"error": str(exc)})
        print(str(output))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
