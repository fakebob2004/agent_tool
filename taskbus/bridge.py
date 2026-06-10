from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .codex_liaison import CodexCliLiaisonAdapter, DefaultLiaisonAdapter, build_compact_context
from .cursor_acp_worker import CursorAcpWorker
from .cursor_session import CursorSession, DryRunCursorSession, SubprocessCursorSession
from .events import BridgeEvent, utc_now
from .evaluator import collect_git_changes, evaluate_worker_result, run_test_commands
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
    return _run_session(
        task=task,
        state_dir=state_dir,
        session=DryRunCursorSession(task),
        mode="dry_run",
        remaining_risks=[
            "Real Cursor PTY integration is available only through explicit worker commands.",
            "Dry-run mode does not run git/test collection.",
        ],
    )


def run_worker(
    task: dict[str, Any],
    state_dir: Path | str,
    worker_command: str | Sequence[str],
    repo_root: Path | str,
) -> dict[str, Any]:
    return _run_session(
        task=task,
        state_dir=state_dir,
        session=SubprocessCursorSession(worker_command, cwd=repo_root),
        mode="worker",
        repo_root=repo_root,
        remaining_risks=[
            "Worker output must emit authenticated TASKBUS_EVENT JSON lines.",
            "Cursor SDK, Hooks, or agent-specific CLI adapter still needs confirmation.",
        ],
    )


def run_cursor_acp(
    task: dict[str, Any],
    state_dir: Path | str,
    repo_root: Path | str,
    acp_command: str | Sequence[str] = ("agent", "acp"),
    session_mode: str | None = "agent",
    trusted_command_roots: list[str] | None = None,
    liaison_command: str | list[str] | None = None,
) -> dict[str, Any]:
    liaison = (
        CodexCliLiaisonAdapter(liaison_command, cwd=repo_root, capture_last_message=True)
        if liaison_command
        else None
    )
    worker = CursorAcpWorker(
        acp_command,
        session_mode=session_mode,
        trusted_command_roots=trusted_command_roots,
        liaison=liaison,
    )
    worker_result = worker.run(task, repo_root)
    worker_completed = worker_result.get("worker_status") == "completed"
    evaluation = worker_result.get("evaluation", {})
    evaluation_passed = bool(evaluation.get("passed"))
    if not worker_completed:
        status = "worker_failed"
    elif not evaluation_passed:
        status = "evaluation_failed"
    else:
        status = "succeeded"

    result = {
        "task_id": task["id"],
        "status": status,
        "mode": "cursor-acp",
        "created_at": utc_now(),
        "worker": {
            "completed": worker_completed,
            "status": worker_result.get("worker_status"),
            "stop_reason": worker_result.get("stop_reason"),
            "changed_files": worker_result.get("changed_files", []),
            "diff_lines": worker_result.get("diff_lines", 0),
            "error": worker_result.get("error"),
        },
        "policy_decisions": worker_result.get("policy_decisions", []),
        "unknown_acp_updates": worker_result.get("unknown_acp_updates", []),
        "unknown_agent_requests": worker_result.get("unknown_agent_requests", []),
        "permission_requests": worker_result.get("permission_requests", []),
        "tests": worker_result.get("tests", []),
        "evaluation": evaluation,
        "summary": "cursor-acp worker ran through ACP transport, policy broker, and evaluator.",
        "remaining_risks": [
            "Bridge cursor-acp mode is explicit and not the default.",
            "Real Codex liaison is used only when --liaison-command is provided.",
            "GitHub integration is intentionally not connected.",
        ],
    }
    store = StateStore(state_dir)
    state_path = store.save(str(task["id"]), result)
    result["state_path"] = str(state_path)
    store.save(str(task["id"]), result)
    return result


def _run_session(
    task: dict[str, Any],
    state_dir: Path | str,
    session: CursorSession,
    mode: str,
    remaining_risks: list[str],
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    store = StateStore(state_dir)
    liaison = DefaultLiaisonAdapter()
    events: list[dict[str, Any]] = []
    policy_decisions: list[dict[str, Any]] = []
    liaison_decisions: list[dict[str, Any]] = []
    worker_payload: dict[str, Any] = {}
    status = "running"

    try:
        for event in session.events():
            events.append(event.to_dict())

            if event.type == "permission_request":
                decision = decide_permission(event.payload, task)
                policy_decisions.append(decision.to_dict())
                events.append(_decision_event(decision).to_dict())
                if decision.action in ("deny", "escalate"):
                    status = "blocked"
                    break
                session.send(decision.instruction)

            elif event.type == "semantic_question":
                context = build_compact_context(
                    task=task,
                    event=event,
                    current_state={
                        "status": status,
                        "changed_files": worker_payload.get("changed_files", []),
                        "liaison_rounds": len(liaison_decisions),
                    },
                )
                decision = liaison.answer(context).to_dict()
                liaison_decisions.append(decision)
                events.append(
                    BridgeEvent(
                        type="liaison_decision",
                        message=decision["instruction_to_cursor"],
                        payload=decision,
                    ).to_dict()
                )
                session.send(decision["instruction_to_cursor"])

            elif event.is_finished:
                worker_payload = event.payload
                status = "succeeded"
                break
    finally:
        session.close()

    if mode == "worker" and repo_root is not None:
        worker_payload.update(_collect_worker_payload(task, worker_payload, repo_root))

    evaluation = evaluate_worker_result(task, worker_payload)
    if status == "succeeded" and not evaluation.passed:
        status = "failed"

    result = {
        "task_id": task["id"],
        "status": status,
        "mode": mode,
        "created_at": utc_now(),
        "events": events,
        "policy_decisions": policy_decisions,
        "liaison_decisions": liaison_decisions,
        "evaluation": evaluation.to_dict(),
        "summary": f"{mode} exercised permission, liaison, finish, and evaluator routing.",
        "remaining_risks": remaining_risks,
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


def _collect_worker_payload(
    task: dict[str, Any],
    worker_payload: dict[str, Any],
    repo_root: Path | str,
) -> dict[str, Any]:
    collected: dict[str, Any] = {}
    if "changed_files" not in worker_payload or "diff_lines" not in worker_payload:
        collected.update(collect_git_changes(repo_root))
    if "tests" not in worker_payload:
        collected["tests"] = run_test_commands([str(cmd) for cmd in task["test_commands"]], repo_root)
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local task bus bridge.")
    parser.add_argument("task", help="Path to a TaskSpec JSON file.")
    parser.add_argument("--state-dir", default="taskbus/state", help="Directory for state JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic dry-run worker.")
    parser.add_argument(
        "--worker",
        choices=["cursor-acp"],
        help="Run a built-in worker adapter. Currently only cursor-acp.",
    )
    parser.add_argument("--worker-command", help="Run a real worker command that emits TASKBUS_EVENT lines.")
    parser.add_argument(
        "--acp-command",
        nargs="+",
        default=["agent", "acp"],
        help="ACP command for --worker cursor-acp, default: agent acp.",
    )
    parser.add_argument(
        "--session-mode",
        default="agent",
        help="ACP session mode for --worker cursor-acp. Use empty string to skip setting a mode.",
    )
    parser.add_argument(
        "--trusted-command-root",
        action="append",
        default=[],
        help="Trusted directory for absolute test interpreter commands in cursor-acp policy.",
    )
    parser.add_argument(
        "--liaison-command",
        nargs="+",
        help="Command for a short-context Codex liaison process in --worker cursor-acp mode.",
    )
    parser.add_argument("--repo-root", default=".", help="Repository root for worker/evaluator commands.")
    args = parser.parse_args(argv)

    selected = sum(bool(value) for value in (args.dry_run, args.worker_command, args.worker))
    if selected != 1:
        parser.error("Specify exactly one of --dry-run, --worker-command, or --worker cursor-acp.")

    task = load_task(args.task)
    if args.dry_run:
        result = run_dry(task, args.state_dir)
    elif args.worker == "cursor-acp":
        result = run_cursor_acp(
            task,
            args.state_dir,
            args.repo_root,
            args.acp_command,
            session_mode=args.session_mode or None,
            trusted_command_roots=args.trusted_command_root,
            liaison_command=args.liaison_command,
        )
    else:
        result = run_worker(task, args.state_dir, args.worker_command, args.repo_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
