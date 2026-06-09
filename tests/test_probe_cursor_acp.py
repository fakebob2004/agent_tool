from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probe_cursor_acp.py"
ECHO_SERVER = ROOT / "tests" / "acp_echo_server.py"


class CursorAcpProbeScriptTests(unittest.TestCase):
    def test_probe_can_send_initialize_then_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "probe.jsonl"
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROBE),
                    "--command",
                    sys.executable,
                    str(ECHO_SERVER),
                    "--cwd",
                    tmp,
                    "--output",
                    str(output),
                    "--timeout",
                    "5",
                    "--new-session-cwd",
                    tmp,
                    "--session-mode",
                    "ask",
                    "--prompt-text",
                    "Report status only.",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        client_methods = [
            row["message"]["method"]
            for row in rows
            if row["direction"] == "client_to_agent"
        ]
        probe_labels = [
            row["message"]["label"]
            for row in rows
            if row["direction"] == "probe_result" and row["message"].get("received_response")
        ]
        self.assertEqual(client_methods, ["initialize", "session/new", "session/set_mode", "session/prompt"])
        self.assertEqual(probe_labels, ["initialize", "session/new", "session/set_mode", "session/prompt"])


if __name__ == "__main__":
    unittest.main()
