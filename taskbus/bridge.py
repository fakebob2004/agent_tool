from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .cursor_session import DryRunCursorSession
from .events import BridgeEvent, utc_now
from .policy import PolicyDecision, decide_permission
from .state import StateStore

REQUIRED_TASK_KEYS = ("id", "objective", "scope", "acceptance", "test_commands")


class TaskSpecError(ValueError):
    pass


def load_task(path: Path | str) -> dict[str, Any]:
    task_path = Path(path)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    validate_task(task)
    return task


def validate_task(task: dict[str, Any]) -> None:
    for key in REQUIRED_TASK_KEYS:
        if key not in task:
            raise TaskSpecError(f"TaskSpec missing required key: {key}")

    if not str(task["id"]).strip():
        raise TaskSpecError("TaskSpec id must not be empty.")
    if not str(task["objective"]).strip():
        raise TaskSpecError("TaskSpec objective must not be empty.")
    if not isinstance(task["acceptance"], list) or not task["acceptance"]:
        raise TaskSpecError("TaskSpec acceptance must be a non-empty list.")
    if not isinstance(task["test_commands"], list) or not task["test_commands"]:
        raise TaskSpecError("TaskSpec test_commands must be a non-empty list.")

    scope = task["scope"]
    if not isinstance(scope, dict):
        raise TaskSpecError("TaskSpec scope must be an object.")
    allowed_paths = scope.get("allowed_paths")
    if not isinstance(allowed_paths, list) or not allowed_paths:
        raise TaskSpecError("TaskSpec scope.allowed_paths must be a non-empty list.")


def run_dry(task: dict[str, Any], state_dir: Path | str) -> dict[str, Any]:
    store = StateStore(state_dir)
    events: list[dict[str, Any]] = []
    policy_decisions: list[dict[str, Any]] = []
    liaison_decisions: list[dict[str, Any]] = []
    status = "running"

    for event in DryRunCursorSession(task).events():
        events.append(event.to_dict())

        if event.type == "permission_request":
            decision = decide_permission(event.payload, task)
            policy_decisions.append(decision.to_dict())
            events.append(_decision_event(decision).to_dict())
            if decision.action in ("deny", "escalate"):
                status = "blocked"
                break

        elif event.type == "semantic_question":
            decision = _default_liaison_decision(event)
            liaison_decisions.append(decision)
            events.append(
                BridgeEvent(
                    type="liaison_decision",
                    message=decision["instruction_to_cursor"],
                    payload=decision,
                ).to_dict()
            )

        elif event.is_finished:
            status = "succeeded"
            break

    result = {
        "task_id": task["id"],
        "status": status,
        "mode": "dry_run",
        "created_at": utc_now(),
        "events": events,
        "policy_decisions": policy_decisions,
        "liaison_decisions": liaison_decisions,
        "summary": "Dry run exercised permission, liaison, and finish routing.",
        "remaining_risks": [
            "Real Cursor PTY integration is not implemented yet.",
            "Deterministic git/test evaluator is not implemented yet.",
        ],
    }
    state_path = store.save(str(task["id"]), result)
    result["state_path"] = str(state_path)
    store.save(str(task["id"]), result)
    return result


def _decision_event(decision: PolicyDecision) -> BridgeEvent:
    return BridgeEvent(
        type="policy_decision",
        message=decision.instruction,
        payload=decision.to_dict(),
    )


def _default_liaison_decision(event: BridgeEvent) -> dict[str, Any]:
    return {
        "decision": "preserve_existing_contract",
        "instruction_to_cursor": event.payload.get(
            "default",
            "Preserve current behavior unless TaskSpec explicitly says otherwise.",
        ),
        "escalate": False,
        "reason_summary": "Dry-run liaison default keeps the worker inside the TaskSpec boundary.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local task bus bridge.")
    parser.add_argument("task", help="Path to a TaskSpec JSON file.")
    parser.add_argument("--state-dir", default="taskbus/state", help="Directory for state JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic dry-run worker.")
    args = parser.parse_args(argv)

    if not args.dry_run:
        parser.error("Only --dry-run is implemented in the current MVP slice.")

    task = load_task(args.task)
    result = run_dry(task, args.state_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
