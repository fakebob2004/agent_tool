from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.bridge import load_task
from taskbus.codex_liaison import CodexCliLiaisonAdapter
from taskbus.cursor_acp_worker import CursorAcpWorker


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_SERVER = ROOT / "tests" / "acp_semantic_server.py"
SEMANTIC_TASK = ROOT / "taskbus" / "examples" / "stress_suite" / "semantic_conflict_empty_input.json"
VENDORED_CODEX = (
    Path.home()
    / "AppData"
    / "Roaming"
    / "npm"
    / "node_modules"
    / "@openai"
    / "codex"
    / "node_modules"
    / "@openai"
    / "codex-win32-x64"
    / "vendor"
    / "x86_64-pc-windows-msvc"
    / "bin"
    / "codex.exe"
)


def run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


@unittest.skipUnless(
    os.environ.get("TASKBUS_RUN_REAL_CODEX_TESTS") == "1",
    "set TASKBUS_RUN_REAL_CODEX_TESTS=1 to call the real Codex CLI",
)
class RealCodexLiaisonTests(unittest.TestCase):
    def test_real_codex_liaison_resumes_semantic_acp_session(self) -> None:
        codex = Path(os.environ.get("TASKBUS_CODEX_EXE", VENDORED_CODEX))
        self.assertTrue(codex.exists(), f"Codex executable not found: {codex}")

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

            task = load_task(SEMANTIC_TASK)
            liaison = CodexCliLiaisonAdapter(
                [
                    str(codex),
                    "exec",
                    "--sandbox",
                    "read-only",
                    "--ephemeral",
                    "--cd",
                    str(repo),
                ],
                cwd=repo,
                timeout=180,
                capture_last_message=True,
            )
            worker = CursorAcpWorker(
                [sys.executable, str(SEMANTIC_SERVER)],
                liaison=liaison,
                prompt_timeout=240,
            )
            result = worker.run(task, repo)

        self.assertEqual(result["worker_status"], "completed", result.get("error"))
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["changed_files"], ["parser.py"])
        self.assertEqual(len(result["liaison_decisions"]), 1)
        self.assertIn(
            result["liaison_decisions"][0]["decision"],
            {"continue", "reply", "keep_api", "preserve_empty_input_value_error"},
        )
        self.assertIn("ValueError", result["liaison_decisions"][0]["instruction_to_cursor"])
        self.assertTrue(result["evaluation"]["passed"], result["evaluation"])


if __name__ == "__main__":
    unittest.main()
