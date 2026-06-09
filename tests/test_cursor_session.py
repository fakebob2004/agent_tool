from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.cursor_session import SubprocessCursorSession, parse_worker_event_line


class CursorSessionTests(unittest.TestCase):
    def test_parse_worker_event_line(self) -> None:
        event = parse_worker_event_line(
            'TASKBUS_EVENT:{"type":"finished","message":"Done","payload":{"changed_files":[]}}'
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertTrue(event.is_finished)

    def test_ignores_non_event_line(self) -> None:
        self.assertIsNone(parse_worker_event_line("ordinary output"))

    def test_subprocess_session_reads_structured_events(self) -> None:
        code = (
            "import json; "
            "print('hello'); "
            "print('TASKBUS_EVENT:' + json.dumps({'type':'finished','message':'Done','payload':{'changed_files':[]}}))"
        )
        with tempfile.TemporaryDirectory() as tmp:
            session = SubprocessCursorSession([sys.executable, "-c", code], cwd=Path(tmp))
            events = list(session.events())
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].is_finished)

    def test_subprocess_session_can_send_reply(self) -> None:
        code = (
            "import json, sys; "
            "print('TASKBUS_EVENT:' + json.dumps({'type':'semantic_question','message':'Need answer','payload':{}}), flush=True); "
            "answer = sys.stdin.readline().strip(); "
            "print('TASKBUS_EVENT:' + json.dumps({'type':'finished','message':answer,'payload':{'changed_files':[]}}), flush=True)"
        )
        with tempfile.TemporaryDirectory() as tmp:
            session = SubprocessCursorSession([sys.executable, "-c", code], cwd=Path(tmp))
            iterator = session.events()
            first = next(iterator)
            self.assertEqual(first.type, "semantic_question")
            session.send("Continue safely.")
            rest = list(iterator)
        self.assertEqual(rest[-1].message, "Continue safely.")


if __name__ == "__main__":
    unittest.main()
