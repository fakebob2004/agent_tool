from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .policy import decide_file_write


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass(frozen=True)
class EvaluationReport:
    passed: bool
    changed_files: list[str]
    diff_lines: int
    gates: list[GateResult] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "changed_files": self.changed_files,
            "diff_lines": self.diff_lines,
            "gates": [gate.to_dict() for gate in self.gates],
            "tests": self.tests,
        }


def evaluate_worker_result(task: dict[str, Any], worker_payload: dict[str, Any]) -> EvaluationReport:
    changed_files = [str(path) for path in worker_payload.get("changed_files", [])]
    diff_lines = int(worker_payload.get("diff_lines", 0) or 0)
    tests = _coerce_tests(worker_payload.get("tests"))

    gates = [
        _gate_changed_files_scope(task, changed_files),
        _gate_changed_file_count(task, changed_files),
        _gate_diff_lines(task, diff_lines),
        _gate_tests(tests),
    ]
    return EvaluationReport(
        passed=all(gate.passed for gate in gates),
        changed_files=changed_files,
        diff_lines=diff_lines,
        gates=gates,
        tests=tests,
    )


def collect_git_changes(repo_root: Path | str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    git = ["git", "-c", f"safe.directory={root.as_posix()}"]
    names = _run(git + ["diff", "--name-only"], root)
    numstat = _run(git + ["diff", "--numstat"], root)

    changed_files = [line.strip() for line in names.stdout.splitlines() if line.strip()]
    diff_lines = 0
    for line in numstat.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        diff_lines += _numstat_value(added) + _numstat_value(deleted)

    return {
        "changed_files": changed_files,
        "diff_lines": diff_lines,
    }


def run_test_commands(commands: list[str], repo_root: Path | str) -> list[dict[str, Any]]:
    root = Path(repo_root)
    results: list[dict[str, Any]] = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            shell=True,
            text=True,
            capture_output=True,
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        results.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "passed": completed.returncode == 0,
                "output_tail": output[-4000:],
            }
        )
    return results


def _gate_changed_files_scope(task: dict[str, Any], changed_files: list[str]) -> GateResult:
    violations = []
    for path in changed_files:
        decision = decide_file_write(path, task)
        if decision.action != "allow":
            violations.append(f"{path}: {decision.reason}")

    if violations:
        return GateResult(
            "path_scope",
            False,
            "Changed files outside allowed scope: " + "; ".join(violations),
        )
    return GateResult("path_scope", True, "All changed files are inside TaskSpec scope.")


def _gate_changed_file_count(task: dict[str, Any], changed_files: list[str]) -> GateResult:
    max_changed = int(task.get("limits", {}).get("max_changed_files", 0) or 0)
    if max_changed and len(changed_files) > max_changed:
        return GateResult(
            "changed_file_count",
            False,
            f"Changed {len(changed_files)} files, over limit {max_changed}.",
        )
    return GateResult("changed_file_count", True, "Changed file count is within limit.")


def _gate_diff_lines(task: dict[str, Any], diff_lines: int) -> GateResult:
    max_lines = int(task.get("limits", {}).get("max_diff_lines", 0) or 0)
    if max_lines and diff_lines > max_lines:
        return GateResult(
            "diff_lines",
            False,
            f"Diff has {diff_lines} changed lines, over limit {max_lines}.",
        )
    return GateResult("diff_lines", True, "Diff size is within limit.")


def _gate_tests(tests: list[dict[str, Any]]) -> GateResult:
    if not tests:
        return GateResult("tests", True, "No test result was provided.")
    failed = [test for test in tests if not test.get("passed", False)]
    if failed:
        commands = ", ".join(str(test.get("command", "<unknown>")) for test in failed)
        return GateResult("tests", False, f"Test command(s) failed: {commands}.")
    return GateResult("tests", True, "All provided test commands passed.")


def _coerce_tests(value: Any) -> list[dict[str, Any]]:
    if value in (None, "", "not_run_dry_run"):
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        normalized = value.strip().lower()
        return [
            {
                "command": "<worker-reported>",
                "passed": normalized in ("passed", "pass", "ok", "success", "succeeded"),
                "output_tail": value,
            }
        ]
    return [
        {
            "command": "<worker-reported>",
            "passed": False,
            "output_tail": f"Unsupported test result format: {type(value).__name__}",
        }
    ]


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"Command failed: {' '.join(command)}")
    return completed


def _numstat_value(value: str) -> int:
    if value == "-":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0
