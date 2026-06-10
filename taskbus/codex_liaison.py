from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import BridgeEvent


class LiaisonDecisionError(ValueError):
    pass


class CodexLiaisonError(RuntimeError):
    pass


ALLOWED_LIAISON_DECISIONS = frozenset(
    {
        "continue",
        "reply",
        "keep_api",
        "preserve_existing_contract",
        "preserve_empty_input_value_error",
        "use_module_local_cache",
        "escalate",
    }
)


@dataclass(frozen=True)
class LiaisonDecision:
    decision: str
    instruction_to_cursor: str
    escalate: bool
    reason_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "instruction_to_cursor": self.instruction_to_cursor,
            "escalate": self.escalate,
            "reason_summary": self.reason_summary,
        }


@dataclass(frozen=True)
class CompactContext:
    task: dict[str, Any]
    current_state: dict[str, Any]
    cursor_event: dict[str, Any]
    relevant_diff: str = ""
    relevant_test_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "current_state": self.current_state,
            "cursor_event": self.cursor_event,
            "relevant_diff": self.relevant_diff,
            "relevant_test_output": self.relevant_test_output,
        }


class DefaultLiaisonAdapter:
    """Deterministic liaison adapter used until an external Codex call is wired in."""

    def answer(self, context: CompactContext) -> LiaisonDecision:
        event_payload = context.cursor_event.get("payload", {})
        instruction = event_payload.get(
            "default",
            "Preserve current behavior unless the TaskSpec explicitly says otherwise.",
        )
        return LiaisonDecision(
            decision="preserve_existing_contract",
            instruction_to_cursor=str(instruction),
            escalate=False,
            reason_summary="Default liaison keeps the worker inside compact TaskSpec context.",
        )


class CodexCliLiaisonAdapter:
    """Runs a short-context Codex process and parses its JSON decision."""

    def __init__(
        self,
        command: str | list[str],
        *,
        cwd: Path | str | None = None,
        timeout: float = 120.0,
        capture_last_message: bool = False,
    ) -> None:
        self.command = command
        self.cwd = Path(cwd) if cwd is not None else None
        self.timeout = timeout
        self.capture_last_message = capture_last_message

    def answer(self, context: CompactContext) -> LiaisonDecision:
        prompt = build_liaison_prompt(context)
        output_path: Path | None = None
        command = self.command
        if self.capture_last_message:
            if isinstance(command, str):
                raise CodexLiaisonError("capture_last_message requires command to be a list.")
            handle = tempfile.NamedTemporaryFile(prefix="taskbus-codex-liaison-", suffix=".txt", delete=False)
            handle.close()
            output_path = Path(handle.name)
            command = [*command, "--output-last-message", str(output_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.cwd,
                input=prompt,
                text=True,
                capture_output=True,
                shell=isinstance(self.command, str),
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            raise CodexLiaisonError(f"Codex liaison command timed out after {self.timeout} seconds.") from exc

        try:
            if completed.returncode != 0:
                stderr = completed.stderr.strip()
                raise CodexLiaisonError(
                    stderr or f"Codex liaison command failed with exit code {completed.returncode}."
                )

            output = completed.stdout
            if output_path is not None:
                output = output_path.read_text(encoding="utf-8")
            return parse_liaison_decision(_extract_json_object(output))
        finally:
            if output_path is not None:
                output_path.unlink(missing_ok=True)


def build_compact_context(
    task: dict[str, Any],
    event: BridgeEvent,
    current_state: dict[str, Any],
    relevant_diff: str = "",
    relevant_test_output: str = "",
) -> CompactContext:
    compact_task = {
        "id": task.get("id"),
        "objective": task.get("objective"),
        "acceptance": task.get("acceptance", []),
        "scope": task.get("scope", {}),
        "limits": task.get("limits", {}),
        "routing": task.get("routing", {}),
    }
    return CompactContext(
        task=compact_task,
        current_state={
            "status": current_state.get("status", "running"),
            "changed_files": current_state.get("changed_files", []),
            "attempt": current_state.get("attempt", 1),
            "liaison_rounds": current_state.get("liaison_rounds", 0),
        },
        cursor_event=event.to_dict(),
        relevant_diff=_trim(relevant_diff),
        relevant_test_output=_trim(relevant_test_output),
    )


def build_liaison_prompt(context: CompactContext) -> str:
    return (
        "You are a short-context Codex liaison for TaskBus.\n"
        "Answer only the semantic question. Do not edit files, run commands, or ask follow-up questions.\n"
        "Return exactly one JSON object with these keys:\n"
        '- "decision": one of continue, reply, keep_api, preserve_existing_contract, '
        'preserve_empty_input_value_error, use_module_local_cache, escalate\n'
        '- "instruction_to_cursor": concrete instruction for the same Cursor ACP session\n'
        '- "escalate": boolean, true only when a human must decide. If omitted, false is assumed\n'
        '- "reason_summary": one short sentence\n\n'
        "Compact context JSON:\n"
        f"{json.dumps(context.to_dict(), ensure_ascii=False, indent=2)}\n"
    )


def parse_liaison_decision(text: str) -> LiaisonDecision:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LiaisonDecisionError(f"Liaison output is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LiaisonDecisionError("Liaison output must be a JSON object.")

    data = _normalize_decision_keys(data)
    if "escalate" not in data:
        data["escalate"] = False
    missing = [key for key in ("decision", "instruction_to_cursor") if key not in data]
    if missing:
        raise LiaisonDecisionError("Liaison output missing required key(s): " + ", ".join(missing))

    if not isinstance(data["escalate"], bool):
        raise LiaisonDecisionError("Liaison key 'escalate' must be a boolean.")

    decision = str(data["decision"]).strip()
    instruction = str(data["instruction_to_cursor"]).strip()
    if not decision:
        raise LiaisonDecisionError("Liaison key 'decision' must not be empty.")
    if decision not in ALLOWED_LIAISON_DECISIONS:
        raise LiaisonDecisionError(
            "Liaison key 'decision' must be one of: " + ", ".join(sorted(ALLOWED_LIAISON_DECISIONS))
        )
    if not instruction:
        raise LiaisonDecisionError("Liaison key 'instruction_to_cursor' must not be empty.")

    return LiaisonDecision(
        decision=decision,
        instruction_to_cursor=instruction,
        escalate=data["escalate"],
        reason_summary=str(data.get("reason_summary", "")).strip(),
    )


def _normalize_decision_keys(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    if "decision" not in normalized and "action" in normalized:
        normalized["decision"] = normalized["action"]
    if "instruction_to_cursor" not in normalized and "instruction" in normalized:
        normalized["instruction_to_cursor"] = normalized["instruction"]
    return normalized


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise LiaisonDecisionError("Liaison output is empty.")
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        candidate = stripped[index : index + end]
        trailing = stripped[index + end :].strip()
        if not trailing or trailing.startswith(("```", "\n")):
            return candidate
    raise LiaisonDecisionError("Liaison output does not contain a JSON object.")


def _trim(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]
