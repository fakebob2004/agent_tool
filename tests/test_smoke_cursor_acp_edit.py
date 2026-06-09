from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.smoke_cursor_acp_edit import PROMPT_TEMPLATE, prepare_repo, run_command


class CursorAcpEditSmokeScriptTests(unittest.TestCase):
    def test_prepare_repo_creates_clean_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "taskbus-acp-smoke-test"
            prepare_repo(repo)

            self.assertEqual((repo / "calc.py").read_text(encoding="utf-8"), "def add(a, b):\n    pass\n")
            self.assertIn("assert add(2, 3) == 5", (repo / "test_calc.py").read_text(encoding="utf-8"))
            status = run_command(["git", "status", "--short"], repo)

        self.assertEqual(status.returncode, 0)
        self.assertEqual(status.stdout, "")

    def test_prepare_repo_refuses_non_temp_path(self) -> None:
        with self.assertRaises(ValueError):
            prepare_repo(Path.cwd() / "taskbus-acp-smoke-danger")

    def test_prompt_template_uses_explicit_test_command(self) -> None:
        prompt = PROMPT_TEMPLATE.format(test_command="/tmp/venv/bin/python -B -m pytest -q")

        self.assertIn("/tmp/venv/bin/python -B -m pytest -q", prompt)
        self.assertIn("Run this exact test command:", prompt)


if __name__ == "__main__":
    unittest.main()
