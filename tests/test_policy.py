from __future__ import annotations

import unittest

from taskbus.policy import decide_file_write, decide_shell


class PolicyTests(unittest.TestCase):
    def test_allows_safe_test_command(self) -> None:
        decision = decide_shell("pytest tests/data -q")
        self.assertEqual(decision.action, "allow")

    def test_denies_dangerous_git_history_command(self) -> None:
        decision = decide_shell("git reset --hard HEAD")
        self.assertEqual(decision.action, "deny")

    def test_dependency_install_requires_liaison(self) -> None:
        decision = decide_shell("pip install requests")
        self.assertEqual(decision.action, "ask_liaison")

    def test_file_write_scope(self) -> None:
        task = {
            "scope": {
                "allowed_paths": ["src/data/**"],
                "forbidden_paths": ["src/data/private/**"],
            }
        }
        self.assertEqual(decide_file_write("src/data/loader.py", task).action, "allow")
        self.assertEqual(decide_file_write("src/data/private/key.txt", task).action, "deny")
        self.assertEqual(decide_file_write("README.md", task).action, "deny")


if __name__ == "__main__":
    unittest.main()
