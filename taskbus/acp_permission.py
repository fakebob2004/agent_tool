from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Literal

from .cursor_acp import AcpPermissionRequest


PermissionAction = Literal["allow", "deny"]


@dataclass(frozen=True)
class AcpPermissionDecision:
    action: PermissionAction
    reason: str
    option_id: str | None = None

    def response_result(self) -> dict[str, object]:
        if self.option_id is not None:
            return {"outcome": {"outcome": "selected", "optionId": self.option_id}}
        return {"outcome": {"outcome": "cancelled"}}

    def json_rpc_response(self, request_id: int | str) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": self.response_result(),
        }


class AcpPermissionBroker:
    def __init__(
        self,
        repo_root: Path | str,
        *,
        allowed_paths: list[str] | None = None,
        test_commands: list[str] | None = None,
        trusted_command_roots: list[str] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        repo_posix = self.repo_root.as_posix()
        self.allowed_paths = tuple(allowed_paths or ["**"])
        self.trusted_command_roots = tuple(root.rstrip("/") for root in (trusted_command_roots or []))
        self.test_commands = tuple(
            test_commands
            or [
                "pytest",
                "pytest -q",
                "python -m pytest",
                "python -m pytest -q",
                f"cd {repo_posix} && pytest",
                f"cd {repo_posix} && pytest -q",
                f"cd {repo_posix} && python -m pytest",
                f"cd {repo_posix} && python -m pytest -q",
            ]
        )

    def decide(self, request: AcpPermissionRequest) -> AcpPermissionDecision:
        tool_call = request.tool_call
        kind = str(tool_call.get("kind", "")).strip()
        if kind in ("read", "search"):
            return self._allow(request, "Read/search tool call is low risk.")
        if kind == "edit":
            return self._decide_edit(request)
        if kind == "execute":
            return self._decide_execute(request)
        return self._deny(request, f"Unsupported ACP tool call kind: {kind or '<empty>'}.")

    def _decide_edit(self, request: AcpPermissionRequest) -> AcpPermissionDecision:
        paths = _extract_paths(request.tool_call)
        if not paths:
            return self._deny(request, "Edit request did not expose target paths.")
        for path in paths:
            if not self._path_allowed(path):
                return self._deny(request, f"Edit path is outside allowed scope: {path}")
        return self._allow(request, "Edit paths are inside allowed scope.")

    def _decide_execute(self, request: AcpPermissionRequest) -> AcpPermissionDecision:
        command = _extract_command(request.tool_call)
        normalized = " ".join(command.split())
        if not normalized:
            return self._deny(request, "Execute request did not expose a command.")
        if _is_dangerous_command(normalized):
            return self._deny(request, "Command matches a denied shell pattern.")
        if (
            normalized in self.test_commands
            or normalized in ("git status", "git diff")
            or self._is_trusted_python_pytest(normalized)
        ):
            return self._allow(request, "Command matches the ACP safe command allow-list.")
        return self._deny(request, f"Command is not in the ACP safe command allow-list: {normalized}")

    def _is_trusted_python_pytest(self, command: str) -> bool:
        direct = _trusted_python_pytest_command(command, self.trusted_command_roots)
        if direct:
            return True
        repo_prefix = f"cd {self.repo_root.as_posix()} && "
        if command.startswith(repo_prefix):
            return _trusted_python_pytest_command(command[len(repo_prefix) :], self.trusted_command_roots)
        return False

    def _path_allowed(self, path: str) -> bool:
        normalized = _normalize_path(path)
        repo_prefix = self.repo_root.as_posix().rstrip("/") + "/"
        if normalized.startswith(repo_prefix.lstrip("/")):
            normalized = normalized[len(repo_prefix.lstrip("/")) :]
        elif normalized.startswith(repo_prefix):
            normalized = normalized[len(repo_prefix) :]
        elif path.startswith("/"):
            return False
        return any(fnmatch(normalized, pattern) for pattern in self.allowed_paths)

    def _allow(self, request: AcpPermissionRequest, reason: str) -> AcpPermissionDecision:
        option_id = _option_id(request, "allow_once") or _option_id(request, "allow_always")
        if option_id is None:
            return AcpPermissionDecision("deny", "No allow option was provided by the agent.")
        return AcpPermissionDecision("allow", reason, option_id=option_id)

    def _deny(self, request: AcpPermissionRequest, reason: str) -> AcpPermissionDecision:
        option_id = _option_id(request, "reject_once")
        return AcpPermissionDecision("deny", reason, option_id=option_id)


def _option_id(request: AcpPermissionRequest, kind: str) -> str | None:
    for option in request.options:
        if option.get("kind") == kind and isinstance(option.get("optionId"), str):
            return str(option["optionId"])
    return None


def _extract_command(tool_call: dict[str, object]) -> str:
    raw_input = tool_call.get("rawInput")
    if isinstance(raw_input, dict) and isinstance(raw_input.get("command"), str):
        return raw_input["command"]
    title = tool_call.get("title")
    if isinstance(title, str):
        return title.strip().strip("`")
    return ""


def _extract_paths(tool_call: dict[str, object]) -> list[str]:
    paths: list[str] = []
    raw_input = tool_call.get("rawInput")
    if isinstance(raw_input, dict) and isinstance(raw_input.get("path"), str):
        paths.append(raw_input["path"])
    locations = tool_call.get("locations")
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, dict) and isinstance(location.get("path"), str):
                paths.append(location["path"])
    content = tool_call.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "diff" and isinstance(item.get("path"), str):
                paths.append(item["path"])
    return paths


def _normalize_path(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().lstrip("./")


def _is_dangerous_command(command: str) -> bool:
    denied_patterns = (
        "sudo *",
        "rm -rf *",
        "git reset --hard*",
        "git push*",
        "pip install *",
        "python -m pip install *",
        "npm install*",
        "pnpm add *",
        "yarn add *",
    )
    parts = [part.strip() for part in command.split("&&")]
    return any(fnmatch(part, pattern) for part in parts for pattern in denied_patterns)


def _trusted_python_pytest_command(command: str, trusted_roots: tuple[str, ...]) -> bool:
    parts = command.split()
    if len(parts) not in (3, 4, 5):
        return False
    python = parts[0]
    remainder = parts[1:]
    if remainder and remainder[0] == "-B":
        remainder = remainder[1:]
    if len(remainder) not in (2, 3):
        return False
    flag, module = remainder[:2]
    if flag != "-m" or module != "pytest":
        return False
    if len(remainder) == 3 and remainder[2] != "-q":
        return False
    python_path = PurePosixPath(python.replace("\\", "/"))
    if not python_path.is_absolute():
        return False
    parent = python_path.parent.as_posix().rstrip("/")
    return any(parent == root or parent.startswith(root + "/") for root in trusted_roots)
