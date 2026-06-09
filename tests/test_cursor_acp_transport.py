from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.cursor_acp import (
    AcpProtocolError,
    CursorAcpSession,
    JsonRpcResponse,
    build_initialize_request,
)


ROOT = Path(__file__).resolve().parents[1]
ECHO_SERVER = ROOT / "tests" / "acp_echo_server.py"


class CursorAcpTransportTests(unittest.TestCase):
    def test_json_rpc_response_validation(self) -> None:
        response = JsonRpcResponse.from_dict({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        self.assertTrue(response.ok)
        with self.assertRaises(AcpProtocolError):
            JsonRpcResponse.from_dict({"jsonrpc": "2.0", "id": 1})

    def test_initialize_request_shape(self) -> None:
        request = build_initialize_request()
        data = request.to_dict()
        self.assertEqual(data["method"], "initialize")
        self.assertEqual(data["params"]["protocolVersion"], 1)
        self.assertTrue(data["params"]["clientCapabilities"]["fs"]["readTextFile"])

    def test_request_response_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("initialize", {"protocolVersion": 1})
            finally:
                session.close()
        self.assertTrue(response.ok)
        self.assertEqual(response.result["method"], "initialize")

    def test_notifications_are_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("notify", {})
                notification = session.next_notification()
            finally:
                session.close()
        self.assertTrue(response.ok)
        self.assertEqual(notification.method, "session/update")

    def test_stderr_tail_is_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("stderr", {})
            finally:
                session.close()
        self.assertTrue(response.ok)
        self.assertIn("stderr message", session.stderr_tail())

    def test_error_response_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = CursorAcpSession([sys.executable, str(ECHO_SERVER)], cwd=tmp)
            try:
                session.start()
                response = session.request("error", {})
            finally:
                session.close()
        self.assertFalse(response.ok)
        self.assertEqual(response.error["message"], "forced error")


if __name__ == "__main__":
    unittest.main()
