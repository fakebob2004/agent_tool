from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.cursor_acp_worker import CursorAcpWorker


ROOT = Path(__file__).resolve().parents[1]
FAKE_SERVER = ROOT / "tests" / "acp_worker_server.py"


def run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


class CursorAcpWorkerTests(unittest.TestCase):
    def test_worker_handles_permission_and_evaluates_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "calc.py").write_text("def add(a, b):\n    pass\n", encoding="utf-8")
            run(["git", "init"], repo)
            run(["git", "config", "user.email", "taskbus@example.invalid"], repo)
            run(["git", "config", "user.name", "TaskBus Test"], repo)
            run(["git", "add", "."], repo)
            run(["git", "commit", "-m", "baseline"], repo)

            task = {
                "id": "acp-worker-smoke",
                "objective": "Implement add() in calc.py.",
                "scope": {"allowed_paths": ["calc.py"], "forbidden_paths": []},
                "acceptance": ["add(2, 3) returns 5"],
                "test_commands": ['python -c "from calc import add; assert add(2, 3) == 5"'],
                "limits": {"max_changed_files": 1, "max_diff_lines": 5},
            }
            worker = CursorAcpWorker([sys.executable, str(FAKE_SERVER)], prompt_timeout=10)
            result = worker.run(task, repo)

        self.assertEqual(result["worker_status"], "completed")
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["changed_files"], ["calc.py"])
        self.assertEqual(result["policy_decisions"][0]["action"], "allow")
        self.assertTrue(result["evaluation"]["passed"])
        self.assertEqual(result["unknown_acp_updates"], [])


if __name__ == "__main__":
    unittest.main()
