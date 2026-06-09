from __future__ import annotations

import unittest

from taskbus.evaluator import evaluate_worker_result


TASK = {
    "scope": {
        "allowed_paths": ["src/data/**", "tests/data/**"],
        "forbidden_paths": ["src/data/private/**"],
    },
    "limits": {
        "max_changed_files": 2,
        "max_diff_lines": 20,
    },
}


class EvaluatorTests(unittest.TestCase):
    def test_passes_scoped_small_change(self) -> None:
        report = evaluate_worker_result(
            TASK,
            {
                "changed_files": ["src/data/loader.py", "tests/data/test_loader.py"],
                "diff_lines": 12,
                "tests": [{"command": "pytest tests/data -q", "passed": True}],
            },
        )
        self.assertTrue(report.passed)

    def test_fails_for_forbidden_path(self) -> None:
        report = evaluate_worker_result(
            TASK,
            {
                "changed_files": ["src/data/private/key.txt"],
                "diff_lines": 1,
                "tests": [{"command": "pytest tests/data -q", "passed": True}],
            },
        )
        self.assertFalse(report.passed)
        self.assertIn("path_scope", [gate.name for gate in report.gates if not gate.passed])

    def test_fails_for_diff_limit(self) -> None:
        report = evaluate_worker_result(TASK, {"changed_files": ["src/data/loader.py"], "diff_lines": 21})
        self.assertFalse(report.passed)

    def test_fails_for_test_failure(self) -> None:
        report = evaluate_worker_result(
            TASK,
            {
                "changed_files": ["src/data/loader.py"],
                "diff_lines": 2,
                "tests": [{"command": "pytest tests/data -q", "passed": False}],
            },
        )
        self.assertFalse(report.passed)


if __name__ == "__main__":
    unittest.main()
