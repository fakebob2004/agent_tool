from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .events import BridgeEvent


class LiaisonDecisionError(ValueError):
    pass


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


def parse_liaison_decision(text: str) -> LiaisonDecision:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LiaisonDecisionError(f"Liaison output is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LiaisonDecisionError("Liaison output must be a JSON object.")

    missing = [
        key
        for key in ("decision", "instruction_to_cursor", "escalate", "reason_summary")
        if key not in data
    ]
    if missing:
        raise LiaisonDecisionError("Liaison output missing required key(s): " + ", ".join(missing))

    if not isinstance(data["escalate"], bool):
        raise LiaisonDecisionError("Liaison key 'escalate' must be a boolean.")

    decision = str(data["decision"]).strip()
    instruction = str(data["instruction_to_cursor"]).strip()
    if not decision:
        raise LiaisonDecisionError("Liaison key 'decision' must not be empty.")
    if not instruction:
        raise LiaisonDecisionError("Liaison key 'instruction_to_cursor' must not be empty.")

    return LiaisonDecision(
        decision=decision,
        instruction_to_cursor=instruction,
        escalate=data["escalate"],
        reason_summary=str(data["reason_summary"]).strip(),
    )


def _trim(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]
