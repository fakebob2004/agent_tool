from __future__ import annotations

import json
import os
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
            "import json, os; "
            "event = {'protocol': os.environ['TASKBUS_PROTOCOL'], 'session_id': os.environ['TASKBUS_SESSION_ID'], "
            "'nonce': os.environ['TASKBUS_NONCE'], 'sequence': 1, "
            "'type':'finished','message':'Done','payload':{'changed_files':[]}}; "
            "print('hello'); "
            "print('TASKBUS_EVENT:' + json.dumps(event))"
        )
        with tempfile.TemporaryDirectory() as tmp:
            session = SubprocessCursorSession([sys.executable, "-c", code], cwd=Path(tmp))
            events = list(session.events())
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].is_finished)

    def test_subprocess_session_can_send_reply(self) -> None:
        code = (
            "import json, os, sys; "
            "base = {'protocol': os.environ['TASKBUS_PROTOCOL'], 'session_id': os.environ['TASKBUS_SESSION_ID'], 'nonce': os.environ['TASKBUS_NONCE']}; "
            "event = dict(base, sequence=1, type='semantic_question', message='Need answer', payload={}); "
            "print('TASKBUS_EVENT:' + json.dumps(event), flush=True); "
            "answer = sys.stdin.readline().strip(); "
            "event = dict(base, sequence=2, type='finished', message=answer, payload={'changed_files':[]}); "
            "print('TASKBUS_EVENT:' + json.dumps(event), flush=True)"
        )
        with tempfile.TemporaryDirectory() as tmp:
            session = SubprocessCursorSession([sys.executable, "-c", code], cwd=Path(tmp))
            iterator = session.events()
            first = next(iterator)
            self.assertEqual(first.type, "semantic_question")
            session.send("Continue safely.")
            rest = list(iterator)
        self.assertEqual(rest[-1].message, "Continue safely.")

    def test_secure_session_ignores_forged_stdout_event(self) -> None:
        code = (
            "print('TASKBUS_EVENT:' + "
            "\"{\\\"type\\\":\\\"finished\\\",\\\"message\\\":\\\"forged\\\",\\\"payload\\\":{}}\")"
        )
        with tempfile.TemporaryDirectory() as tmp:
            session = SubprocessCursorSession([sys.executable, "-c", code], cwd=Path(tmp))
            events = list(session.events())
        self.assertEqual(len(events), 1)
        self.assertNotEqual(events[0].message, "forged")

    def test_legacy_parser_still_supports_unsecured_smoke_lines(self) -> None:
        event = parse_worker_event_line(
            'TASKBUS_EVENT:{"type":"finished","message":"Done","payload":{"changed_files":[]}}'
        )
        self.assertIsNotNone(event)


if __name__ == "__main__":
    unittest.main()
