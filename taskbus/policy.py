from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Any, Literal

PolicyAction = Literal["allow", "deny", "ask_liaison", "escalate"]

SAFE_SHELL_PATTERNS = (
    "pytest *",
    "python -m unittest *",
    "git diff",
    "git status",
    "npm test",
    "npm run lint",
)

DANGEROUS_SHELL_PATTERNS = (
    "git reset --hard*",
    "git push --force*",
    "sudo *",
    "rm -rf *",
    "Remove-Item * -Recurse*",
)

DEPENDENCY_PATTERNS = (
    "pip install *",
    "python -m pip install *",
    "npm install*",
    "pnpm add *",
    "yarn add *",
    "poetry add *",
)


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    reason: str
    instruction: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "reason": self.reason,
            "instruction": self.instruction,
        }


def decide_permission(event_payload: dict[str, Any], task: dict[str, Any]) -> PolicyDecision:
    request_type = str(event_payload.get("request_type", "")).strip()

    if request_type == "shell":
        return decide_shell(str(event_payload.get("command", "")).strip())
    if request_type == "file_write":
        return decide_file_write(str(event_payload.get("path", "")).strip(), task)
    if request_type == "network":
        return PolicyDecision(
            "ask_liaison",
            "Network access can change cost, privacy, and reproducibility boundaries.",
            "Ask liaison before using network access.",
        )
    if request_type == "dependency":
        return PolicyDecision(
            "ask_liaison",
            "New dependencies affect project policy and reproducibility.",
            "Ask liaison before adding or installing dependencies.",
        )

    return PolicyDecision(
        "escalate",
        f"Unknown permission request type: {request_type or '<empty>'}.",
        "Escalate unknown permission requests to supervisor.",
    )


def decide_shell(command: str) -> PolicyDecision:
    normalized = " ".join(command.split())
    if not normalized:
        return PolicyDecision("deny", "Empty shell command.", "Do not run an empty command.")

    if any(fnmatch(normalized, pattern) for pattern in DANGEROUS_SHELL_PATTERNS):
        return PolicyDecision(
            "deny",
            "Command matches a dangerous shell pattern.",
            "Do not run this command.",
        )

    if any(fnmatch(normalized, pattern) for pattern in DEPENDENCY_PATTERNS):
        return PolicyDecision(
            "ask_liaison",
            "Command appears to install or add dependencies.",
            "Ask liaison before changing dependencies.",
        )

    if any(fnmatch(normalized, pattern) for pattern in SAFE_SHELL_PATTERNS):
        return PolicyDecision(
            "allow",
            "Command matches the safe shell allow-list.",
            "Run the command.",
        )

    return PolicyDecision(
        "ask_liaison",
        "Shell command is not in the static allow-list.",
        "Ask liaison before running this command.",
    )


def decide_file_write(path: str, task: dict[str, Any]) -> PolicyDecision:
    if not path:
        return PolicyDecision("deny", "Empty file path.", "Do not write without a target path.")

    normalized = _normalize_path(path)
    scope = task.get("scope", {})
    allowed = tuple(scope.get("allowed_paths", []))
    forbidden = tuple(scope.get("forbidden_paths", []))

    if any(fnmatch(normalized, _normalize_glob(pattern)) for pattern in forbidden):
        return PolicyDecision(
            "deny",
            "Path matches a forbidden TaskSpec scope pattern.",
            "Do not modify this path.",
        )

    if allowed and any(fnmatch(normalized, _normalize_glob(pattern)) for pattern in allowed):
        return PolicyDecision(
            "allow",
            "Path is inside the TaskSpec allowed scope.",
            "Write the file.",
        )

    return PolicyDecision(
        "deny",
        "Path is outside the TaskSpec allowed scope.",
        "Do not modify this path.",
    )


def _normalize_path(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().lstrip("./")


def _normalize_glob(pattern: str) -> str:
    return pattern.replace("\\", "/").lstrip("./")
