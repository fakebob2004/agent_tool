from __future__ import annotations

import io
import unittest

from taskbus.acp_framing import ContentLengthFramer, FramingError, JsonLinesFramer


class AcpFramingTests(unittest.TestCase):
    def test_json_lines_round_trip(self) -> None:
        framer = JsonLinesFramer()
        message = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        stream = io.BytesIO(framer.encode(message))
        self.assertEqual(framer.read(stream), message)

    def test_content_length_round_trip(self) -> None:
        framer = ContentLengthFramer()
        message = {"jsonrpc": "2.0", "method": "session/update", "params": {"ok": True}}
        stream = io.BytesIO(framer.encode(message))
        self.assertEqual(framer.read(stream), message)

    def test_content_length_rejects_missing_header(self) -> None:
        framer = ContentLengthFramer()
        with self.assertRaises(FramingError):
            framer.read(io.BytesIO(b"X-Test: 1\r\n\r\n{}"))

    def test_json_lines_rejects_non_object(self) -> None:
        framer = JsonLinesFramer()
        with self.assertRaises(FramingError):
            framer.read(io.BytesIO(b"[]\n"))


if __name__ == "__main__":
    unittest.main()
