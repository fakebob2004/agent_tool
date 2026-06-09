from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Sequence

from .acp_framing import FramingError, JsonLinesFramer, MessageFramer
from .acp_permission import AcpPermissionBroker
from .cursor_acp import (
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
from .evaluator import collect_git_changes, evaluate_worker_result, run_test_commands
from .prompt_builder import build_cursor_worker_prompt
from .worker import WorkerCapabilities, WorkerMode


CURSOR_ACP_WORKER_CAPABILITIES = WorkerCapabilities(
    mode=WorkerMode.INTERACTIVE,
    structured_events=True,
    supports_tool_review=True,
    supports_session_resume=True,
    supports_model_selection=False,
    trusted_event_channel=False,
)


class CursorAcpWorker:
    capabilities = CURSOR_ACP_WORKER_CAPABILITIES

    def __init__(
        self,
        command: str | Sequence[str] = ("agent", "acp"),
        *,
        session_mode: str | None = "agent",
        setup_timeout: float = 30.0,
        prompt_timeout: float = 300.0,
        framer: MessageFramer | None = None,
    ) -> None:
        self.command = command
        self.session_mode = session_mode
        self.setup_timeout = setup_timeout
        self.prompt_timeout = prompt_timeout
        self.framer = framer or JsonLinesFramer()

    def run(self, task: dict[str, Any], repo_root: Path | str, policy: AcpPermissionBroker | None = None) -> dict[str, Any]:
        repo = Path(repo_root)
        prompt = build_cursor_worker_prompt(task)
        broker = policy or AcpPermissionBroker(
            repo,
            allowed_paths=[str(path) for path in task.get("scope", {}).get("allowed_paths", [])],
            test_commands=_test_command_allowlist(repo, [str(command) for command in task.get("test_commands", [])]),
        )

        process = subprocess.Popen(
            self.command,
            cwd=repo,
            shell=isinstance(self.command, str),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        messages: Queue[dict[str, Any]] = Queue()
        stderr_lines: Queue[str] = Queue()
        threading.Thread(target=_read_stdout, args=(process.stdout, self.framer, messages), daemon=True).start()
        threading.Thread(target=_read_stderr, args=(process.stderr, stderr_lines), daemon=True).start()

        transcript = AcpPromptTranscript()
        policy_decisions: list[dict[str, Any]] = []
        prompt_response: dict[str, Any] | None = None
        worker_status = "failed"
        error: str | None = None
        try:
            _send(process, self.framer, build_initialize_request(request_id=1).to_dict())
            initialize = _wait_for_response(
                process,
                self.framer,
                messages,
                self.setup_timeout,
                1,
                transcript,
                broker,
                policy_decisions,
            )
            if initialize is None:
                raise TimeoutError("Timed out waiting for initialize response.")

            _send(process, self.framer, build_new_session_request(cwd=str(repo), request_id=2).to_dict())
            new_session = _wait_for_response(
                process,
                self.framer,
                messages,
                self.setup_timeout,
                2,
                transcript,
                broker,
                policy_decisions,
            )
            if new_session is None:
                raise TimeoutError("Timed out waiting for session/new response.")
            session_id = new_session.get("result", {}).get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("session/new response did not include sessionId.")

            next_request_id = 3
            if self.session_mode:
                _send(
                    process,
                    self.framer,
                    build_set_session_mode_request(session_id, self.session_mode, request_id=next_request_id).to_dict(),
                )
                if _wait_for_response(
                    process,
                    self.framer,
                    messages,
                    self.setup_timeout,
                    next_request_id,
                    transcript,
                    broker,
                    policy_decisions,
                ) is None:
                    raise TimeoutError("Timed out waiting for session/set_mode response.")
                next_request_id += 1

            _send(process, self.framer, build_prompt_request(session_id, prompt, request_id=next_request_id).to_dict())
            prompt_response = _wait_for_response(
                process,
                self.framer,
                messages,
                self.prompt_timeout,
                next_request_id,
                transcript,
                broker,
                policy_decisions,
            )
            if prompt_response is None:
                raise TimeoutError("Timed out waiting for session/prompt response.")
            transcript.apply_response(JsonRpcResponse.from_dict(prompt_response))
            worker_status = "completed"
        except Exception as exc:
            error = str(exc)
        finally:
            _close_process(process)

        worker_payload = collect_git_changes(repo)
        worker_payload["tests"] = run_test_commands([str(command) for command in task.get("test_commands", [])], repo)
        evaluation = evaluate_worker_result(task, worker_payload)

        return {
            "worker_status": worker_status,
            "stop_reason": transcript.stop_reason,
            "agent_report": transcript.text,
            "prompt_response": prompt_response,
            "changed_files": worker_payload["changed_files"],
            "diff_lines": worker_payload["diff_lines"],
            "policy_decisions": policy_decisions,
            "unknown_acp_updates": transcript.unknown_updates,
            "unknown_agent_requests": transcript.unknown_agent_requests,
            "permission_requests": [asdict(request) for request in transcript.permission_requests],
            "tests": worker_payload["tests"],
            "evaluation": evaluation.to_dict(),
            "stderr_tail": list(stderr_lines.queue)[-20:],
            "error": error,
        }


def _test_command_allowlist(repo: Path, commands: list[str]) -> list[str]:
    allowed: list[str] = []
    for command in commands:
        allowed.append(command)
        allowed.append(f"cd {repo.as_posix()} && {command}")
    return allowed


def _send(process: subprocess.Popen[bytes], framer: MessageFramer, message: dict[str, Any]) -> None:
    if process.stdin is None or process.stdin.closed:
        raise RuntimeError("ACP stdin is closed.")
    process.stdin.write(framer.encode(message))
    process.stdin.flush()


def _wait_for_response(
    process: subprocess.Popen[bytes],
    framer: MessageFramer,
    messages: Queue[dict[str, Any]],
    timeout: float,
    request_id: int | str,
    transcript: AcpPromptTranscript,
    broker: AcpPermissionBroker,
    policy_decisions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            message = messages.get(timeout=remaining)
        except Empty:
            continue
        if message.get("method") == "session/update":
            transcript.apply_update(parse_session_update(message))
        elif "id" in message and "method" in message:
            transcript.apply_agent_request(message)
            if message.get("method") == "session/request_permission":
                permission_request = parse_permission_request(message)
                decision = broker.decide(permission_request)
                policy_decisions.append(
                    {
                        "request_id": permission_request.request_id,
                        "tool_kind": permission_request.tool_call.get("kind"),
                        "tool_title": permission_request.tool_call.get("title"),
                        "action": decision.action,
                        "reason": decision.reason,
                        "option_id": decision.option_id,
                    }
                )
                _send(process, framer, decision.json_rpc_response(permission_request.request_id))
        if message.get("id") == request_id and ("result" in message or "error" in message):
            return message


def _read_stdout(stream, framer: MessageFramer, messages: Queue[dict[str, Any]]) -> None:
    while True:
        try:
            messages.put(framer.read(stream))
        except EOFError:
            break
        except FramingError:
            continue


def _read_stderr(stream, stderr_lines: Queue[str]) -> None:
    for line in stream:
        stderr_lines.put(line.decode("utf-8", errors="replace").rstrip("\r\n"))


def _close_process(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()
