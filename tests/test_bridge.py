from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.bridge import TaskSpecError, load_task, run_cursor_acp, run_dry, run_worker, validate_task


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TASK = ROOT / "taskbus" / "examples" / "csv_loader_task.json"
ACP_WORKER_SERVER = ROOT / "tests" / "acp_worker_server.py"


def run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


class BridgeTests(unittest.TestCase):
    def test_sample_task_validates(self) -> None:
        task = load_task(SAMPLE_TASK)
        self.assertEqual(task["id"], "local-csv-loader-empty-input")

    def test_missing_acceptance_fails(self) -> None:
        task = load_task(SAMPLE_TASK)
        task.pop("acceptance")
        with self.assertRaises(TaskSpecError):
            validate_task(task)

    def test_dry_run_writes_state(self) -> None:
        task = load_task(SAMPLE_TASK)
        with tempfile.TemporaryDirectory() as tmp:
            result = run_dry(task, tmp)
            state_path = Path(result["state_path"])
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(state_path.exists())
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["task_id"], task["id"])
            self.assertGreaterEqual(len(saved["events"]), 3)

    def test_worker_command_can_complete(self) -> None:
        task = load_task(SAMPLE_TASK)
        command = [sys.executable, "taskbus/examples/smoke_worker.py"]
        with tempfile.TemporaryDirectory() as tmp:
            result = run_worker(task, tmp, command, ROOT)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["mode"], "worker")
        self.assertTrue(result["evaluation"]["passed"])

    def test_cursor_acp_worker_can_complete_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as state_tmp:
            repo = Path(repo_tmp)
            (repo / "calc.py").write_text("def add(a, b):\n    pass\n", encoding="utf-8")
            run(["git", "init"], repo)
            run(["git", "config", "user.email", "taskbus@example.invalid"], repo)
            run(["git", "config", "user.name", "TaskBus Test"], repo)
            run(["git", "add", "."], repo)
            run(["git", "commit", "-m", "baseline"], repo)
            task = {
                "id": "bridge-acp-worker-smoke",
                "objective": "Implement add() in calc.py.",
                "scope": {"allowed_paths": ["calc.py"], "forbidden_paths": []},
                "acceptance": ["add(2, 3) returns 5"],
                "test_commands": ['python -c "from calc import add; assert add(2, 3) == 5"'],
                "limits": {"max_changed_files": 1, "max_diff_lines": 5},
            }

            result = run_cursor_acp(
                task,
                state_tmp,
                repo,
                [sys.executable, str(ACP_WORKER_SERVER)],
            )
            state_path_exists = Path(result["state_path"]).exists()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["mode"], "cursor-acp")
        self.assertTrue(result["worker"]["completed"])
        self.assertEqual(result["worker"]["stop_reason"], "end_turn")
        self.assertTrue(result["evaluation"]["passed"])
        self.assertTrue(state_path_exists)


if __name__ == "__main__":
    unittest.main()
