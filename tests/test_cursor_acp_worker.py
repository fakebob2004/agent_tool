from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.codex_liaison import LiaisonDecision
from taskbus.cursor_acp_worker import CursorAcpWorker


ROOT = Path(__file__).resolve().parents[1]
FAKE_SERVER = ROOT / "tests" / "acp_worker_server.py"
SEMANTIC_SERVER = ROOT / "tests" / "acp_semantic_server.py"


def run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


class MockLiaison:
    def __init__(self) -> None:
        self.contexts: list[object] = []

    def answer(self, context: object) -> LiaisonDecision:
        self.contexts.append(context)
        return LiaisonDecision(
            decision="preserve_empty_input_value_error",
            instruction_to_cursor=(
                "Preserve the existing compatibility contract: empty input must raise ValueError. "
                "Implement only the non-empty parsing behavior needed by the task."
            ),
            escalate=False,
            reason_summary="Existing tests define the compatibility boundary.",
        )


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

    def test_worker_uses_mock_liaison_for_semantic_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "parser.py").write_text("def parse_items(text):\n    pass\n", encoding="utf-8")
            (repo / "test_parser.py").write_text(
                "from parser import parse_items\n"
                "try:\n"
                "    parse_items(\"\")\n"
                "except ValueError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError(\"empty input must raise ValueError\")\n"
                "assert parse_items(\"a,b\") == [\"a\", \"b\"]\n",
                encoding="utf-8",
            )
            run(["git", "init"], repo)
            run(["git", "config", "user.email", "taskbus@example.invalid"], repo)
            run(["git", "config", "user.name", "TaskBus Test"], repo)
            run(["git", "add", "."], repo)
            run(["git", "commit", "-m", "baseline"], repo)

            task = {
                "id": "acp-semantic-smoke",
                "objective": "Safely handle parser input without changing existing tests.",
                "scope": {"allowed_paths": ["parser.py"], "forbidden_paths": ["test_parser.py"]},
                "acceptance": [
                    "Existing tests keep requiring empty input to raise ValueError.",
                    "Non-empty comma-separated input parses into a list.",
                ],
                "test_commands": ["python test_parser.py"],
                "limits": {"max_changed_files": 1, "max_diff_lines": 8},
            }
            liaison = MockLiaison()
            worker = CursorAcpWorker([sys.executable, str(SEMANTIC_SERVER)], liaison=liaison, prompt_timeout=10)
            result = worker.run(task, repo)

        self.assertEqual(result["worker_status"], "completed")
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["changed_files"], ["parser.py"])
        self.assertTrue(result["evaluation"]["passed"])
        self.assertEqual(len(result["liaison_decisions"]), 1)
        self.assertEqual(result["liaison_decisions"][0]["decision"], "preserve_empty_input_value_error")
        self.assertEqual(len(liaison.contexts), 1)
        context = liaison.contexts[0]
        self.assertEqual(context.cursor_event["type"], "semantic_question")
        self.assertIn("empty input", context.cursor_event["message"])
        self.assertIn("ValueError", context.relevant_test_output)
        self.assertEqual(result["unknown_acp_updates"], [])


if __name__ == "__main__":
    unittest.main()
