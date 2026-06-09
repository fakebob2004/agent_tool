from __future__ import annotations

import unittest

from taskbus.prompt_builder import PromptBuildError, build_cursor_worker_prompt


class PromptBuilderTests(unittest.TestCase):
    def test_task_spec_fields_enter_prompt(self) -> None:
        prompt = build_cursor_worker_prompt(
            {
                "objective": "Fix loader",
                "scope": {
                    "allowed_paths": ["src/data/**"],
                    "forbidden_paths": [".env"],
                },
                "acceptance": ["tests pass"],
                "test_commands": ["pytest tests/data -q"],
            }
        )
        self.assertIn("Fix loader", prompt)
        self.assertIn("src/data/**", prompt)
        self.assertIn(".env", prompt)
        self.assertIn("pytest tests/data -q", prompt)

    def test_secret_fields_are_not_rendered(self) -> None:
        prompt = build_cursor_worker_prompt(
            {
                "objective": "Fix loader",
                "token": "SECRET_VALUE",
                "scope": {"allowed_paths": ["src/**"]},
                "acceptance": ["tests pass"],
                "test_commands": ["pytest"],
            }
        )
        self.assertNotIn("SECRET_VALUE", prompt)

    def test_prompt_length_limit(self) -> None:
        with self.assertRaises(PromptBuildError):
            build_cursor_worker_prompt(
                {
                    "objective": "x" * 200,
                    "scope": {"allowed_paths": ["src/**"]},
                    "acceptance": ["tests pass"],
                    "test_commands": ["pytest"],
                },
                max_chars=50,
            )


if __name__ == "__main__":
    unittest.main()
