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
from .codex_liaison import CompactContext, DefaultLiaisonAdapter, LiaisonDecision
from .events import BridgeEvent
from .evaluator import collect_git_changes, evaluate_worker_result, run_test_commands
from .prompt_builder import build_cursor_worker_prompt
from .worker import WorkerCapabilities, WorkerMode


SEMANTIC_QUESTION_MARKER = "TASKBUS_SEMANTIC_QUESTION:"


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
        trusted_command_roots: list[str] | None = None,
        liaison: Any | None = None,
        max_liaison_rounds: int = 1,
        framer: MessageFramer | None = None,
    ) -> None:
        self.command = command
        self.session_mode = session_mode
        self.setup_timeout = setup_timeout
        self.prompt_timeout = prompt_timeout
        self.trusted_command_roots = trusted_command_roots or []
        self.liaison = liaison or DefaultLiaisonAdapter()
        self.max_liaison_rounds = max_liaison_rounds
        self.framer = framer or JsonLinesFramer()

    def run(self, task: dict[str, Any], repo_root: Path | str, policy: AcpPermissionBroker | None = None) -> dict[str, Any]:
        repo = Path(repo_root)
        prompt = build_cursor_worker_prompt(task)
        broker = policy or AcpPermissionBroker(
            repo,
            allowed_paths=[str(path) for path in task.get("scope", {}).get("allowed_paths", [])],
            test_commands=_test_command_allowlist(repo, [str(command) for command in task.get("test_commands", [])]),
            trusted_command_roots=self.trusted_command_roots,
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
        liaison_decisions: list[dict[str, Any]] = []
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

            prompt_response, next_request_id = self._run_prompt_rounds(
                process=process,
                messages=messages,
                session_id=session_id,
                initial_prompt=prompt,
                first_request_id=next_request_id,
                transcript=transcript,
                broker=broker,
                policy_decisions=policy_decisions,
                liaison_decisions=liaison_decisions,
                task=task,
            )
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
            "liaison_decisions": liaison_decisions,
            "unknown_acp_updates": transcript.unknown_updates,
            "unknown_agent_requests": transcript.unknown_agent_requests,
            "permission_requests": [asdict(request) for request in transcript.permission_requests],
            "tests": worker_payload["tests"],
            "evaluation": evaluation.to_dict(),
            "stderr_tail": list(stderr_lines.queue)[-20:],
            "error": error,
        }

    def _run_prompt_rounds(
        self,
        *,
        process: subprocess.Popen[bytes],
        messages: Queue[dict[str, Any]],
        session_id: str,
        initial_prompt: str,
        first_request_id: int,
        transcript: AcpPromptTranscript,
        broker: AcpPermissionBroker,
        policy_decisions: list[dict[str, Any]],
        liaison_decisions: list[dict[str, Any]],
        task: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        prompt_text = initial_prompt
        request_id = first_request_id
        handled_questions: set[str] = set()
        for _round in range(self.max_liaison_rounds + 1):
            text_start = len(transcript.text)
            _send(process, self.framer, build_prompt_request(session_id, prompt_text, request_id=request_id).to_dict())
            response = _wait_for_response(
                process,
                self.framer,
                messages,
                self.prompt_timeout,
                request_id,
                transcript,
                broker,
                policy_decisions,
            )
            if response is None:
                raise TimeoutError("Timed out waiting for session/prompt response.")
            transcript.apply_response(JsonRpcResponse.from_dict(response))
            new_text = transcript.text[text_start:]
            question = _extract_semantic_question(new_text)
            if not question or question in handled_questions:
                return response, request_id + 1
            handled_questions.add(question)
            if len(liaison_decisions) >= self.max_liaison_rounds:
                raise RuntimeError("Semantic question exceeded max liaison rounds.")
            decision = self._ask_liaison(task, question, transcript, liaison_decisions)
            if decision.escalate:
                raise RuntimeError(f"Liaison escalated semantic question: {decision.reason_summary}")
            prompt_text = decision.instruction_to_cursor
            request_id += 1
        raise RuntimeError("Prompt loop exited without a final response.")

    def _ask_liaison(
        self,
        task: dict[str, Any],
        question: str,
        transcript: AcpPromptTranscript,
        liaison_decisions: list[dict[str, Any]],
    ) -> LiaisonDecision:
        event = BridgeEvent(
            type="semantic_question",
            message=question,
            payload={
                "question": question,
                "default": "Preserve existing behavior unless the TaskSpec explicitly overrides it.",
            },
        )
        context = CompactContext(
            task={
                "id": task.get("id"),
                "objective": task.get("objective"),
                "acceptance": task.get("acceptance", []),
                "scope": task.get("scope", {}),
                "limits": task.get("limits", {}),
            },
            current_state={
                "status": "needs_liaison",
                "changed_files": [],
                "attempt": 1,
                "liaison_rounds": len(liaison_decisions),
            },
            cursor_event=event.to_dict(),
            relevant_diff="",
            relevant_test_output=transcript.text[-4000:],
        )
        decision = self.liaison.answer(context)
        liaison_decisions.append(decision.to_dict())
        return decision


def _test_command_allowlist(repo: Path, commands: list[str]) -> list[str]:
    allowed: list[str] = []
    for command in commands:
        allowed.append(command)
        allowed.append(f"cd {repo.as_posix()} && {command}")
    return allowed


def _extract_semantic_question(text: str) -> str | None:
    marker_index = text.find(SEMANTIC_QUESTION_MARKER)
    if marker_index < 0:
        return None
    question = text[marker_index + len(SEMANTIC_QUESTION_MARKER) :].strip()
    if not question:
        return None
    return question.splitlines()[0].strip()


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
