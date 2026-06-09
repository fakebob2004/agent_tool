from __future__ import annotations

import json
import sys


def main() -> int:
    for line in sys.stdin:
        data = json.loads(line)
        method = data.get("method")
        if method == "notify":
            print(
                json.dumps({"jsonrpc": "2.0", "id": data.get("id"), "result": {"ok": True}}),
                flush=True,
            )
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {"ok": True},
                    }
                ),
                flush=True,
            )
        elif method == "error":
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": data.get("id"),
                        "error": {"code": -32000, "message": "forced error"},
                    }
                ),
                flush=True,
            )
        elif method == "stderr":
            print("stderr message", file=sys.stderr, flush=True)
            print(
                json.dumps({"jsonrpc": "2.0", "id": data.get("id"), "result": {"ok": True}}),
                flush=True,
            )
        elif method == "session/new":
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": data.get("id"),
                        "result": {
                            "method": method,
                            "params": data.get("params", {}),
                            "sessionId": "sess_echo",
                        },
                    }
                ),
                flush=True,
            )
        else:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": data.get("id"),
                        "result": {"method": method, "params": data.get("params", {})},
                    }
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
