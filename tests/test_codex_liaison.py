from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.codex_liaison import (
    CodexCliLiaisonAdapter,
    CodexLiaisonError,
    DefaultLiaisonAdapter,
    LiaisonDecisionError,
    build_compact_context,
    build_liaison_prompt,
    parse_liaison_decision,
)
from taskbus.events import BridgeEvent


class CodexLiaisonTests(unittest.TestCase):
    def test_build_context_omits_full_task_noise(self) -> None:
        task = {
            "id": "task-1",
            "objective": "Do the thing",
            "acceptance": ["tests pass"],
            "scope": {"allowed_paths": ["src/**"]},
            "context": {"notes": ["large notes should not be included by default"]},
        }
        event = BridgeEvent(type="semantic_question", message="Question?", payload={})
        context = build_compact_context(task, event, {"status": "running"})
        self.assertEqual(context.task["id"], "task-1")
        self.assertNotIn("context", context.task)

    def test_parse_liaison_decision(self) -> None:
        decision = parse_liaison_decision(
            json.dumps(
                {
                    "decision": "keep_api",
                    "instruction_to_cursor": "Preserve public API.",
                    "escalate": False,
                    "reason_summary": "Compatibility is required.",
                }
            )
        )
        self.assertEqual(decision.decision, "keep_api")

    def test_parse_rejects_missing_required_key(self) -> None:
        with self.assertRaises(LiaisonDecisionError):
            parse_liaison_decision(json.dumps({"decision": "keep_api"}))

    def test_parse_accepts_action_instruction_aliases(self) -> None:
        decision = parse_liaison_decision(
            json.dumps(
                {
                    "action": "reply",
                    "instruction": "Use the module-local cache.",
                    "escalate": False,
                }
            )
        )
        self.assertEqual(decision.decision, "reply")
        self.assertEqual(decision.instruction_to_cursor, "Use the module-local cache.")

    def test_default_adapter_uses_event_default(self) -> None:
        event = BridgeEvent(
            type="semantic_question",
            message="Question?",
            payload={"default": "Use the smallest diff."},
        )
        context = build_compact_context({}, event, {})
        decision = DefaultLiaisonAdapter().answer(context)
        self.assertEqual(decision.instruction_to_cursor, "Use the smallest diff.")

    def test_build_liaison_prompt_contains_contract_and_context(self) -> None:
        event = BridgeEvent(type="semantic_question", message="Question?", payload={})
        context = build_compact_context({"id": "task-1", "objective": "Do it"}, event, {})
        prompt = build_liaison_prompt(context)
        self.assertIn("Return exactly one JSON object", prompt)
        self.assertIn('"instruction_to_cursor"', prompt)
        self.assertIn('"task-1"', prompt)

    def test_codex_cli_adapter_invokes_process_and_parses_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_liaison.py"
            script.write_text(
                "import json, sys\n"
                "prompt = sys.stdin.read()\n"
                "assert 'Compact context JSON' in prompt\n"
                "print(json.dumps({\n"
                "    'action': 'reply',\n"
                "    'instruction': 'Preserve ValueError for empty input.',\n"
                "    'escalate': False,\n"
                "    'reason_summary': 'Tests define compatibility.'\n"
                "}))\n",
                encoding="utf-8",
            )
            event = BridgeEvent(type="semantic_question", message="Question?", payload={})
            context = build_compact_context({"id": "task-1"}, event, {})
            adapter = CodexCliLiaisonAdapter([sys.executable, str(script)], timeout=10)
            decision = adapter.answer(context)

        self.assertEqual(decision.decision, "reply")
        self.assertEqual(decision.instruction_to_cursor, "Preserve ValueError for empty input.")

    def test_codex_cli_adapter_rejects_non_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "bad_liaison.py"
            script.write_text("print('I think you should preserve compatibility')\n", encoding="utf-8")
            event = BridgeEvent(type="semantic_question", message="Question?", payload={})
            context = build_compact_context({"id": "task-1"}, event, {})
            adapter = CodexCliLiaisonAdapter([sys.executable, str(script)], timeout=10)

            with self.assertRaises(LiaisonDecisionError):
                adapter.answer(context)

    def test_codex_cli_adapter_reports_process_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "failing_liaison.py"
            script.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(7)\n", encoding="utf-8")
            event = BridgeEvent(type="semantic_question", message="Question?", payload={})
            context = build_compact_context({"id": "task-1"}, event, {})
            adapter = CodexCliLiaisonAdapter([sys.executable, str(script)], timeout=10)

            with self.assertRaises(CodexLiaisonError):
                adapter.answer(context)


if __name__ == "__main__":
    unittest.main()
