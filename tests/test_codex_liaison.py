from __future__ import annotations

import json
import unittest

from taskbus.codex_liaison import (
    DefaultLiaisonAdapter,
    LiaisonDecisionError,
    build_compact_context,
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

    def test_default_adapter_uses_event_default(self) -> None:
        event = BridgeEvent(
            type="semantic_question",
            message="Question?",
            payload={"default": "Use the smallest diff."},
        )
        context = build_compact_context({}, event, {})
        decision = DefaultLiaisonAdapter().answer(context)
        self.assertEqual(decision.instruction_to_cursor, "Use the smallest diff.")


if __name__ == "__main__":
    unittest.main()
