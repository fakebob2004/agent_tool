# Cursor ACP Edit Smoke

Captured on 2026-06-09 from WSL Ubuntu using an isolated temporary repository at `/tmp/taskbus-acp-smoke`.

Raw JSONL and summary files are intentionally ignored under `taskbus/state/`.

## Scope

The smoke repository contained only:

```text
calc.py
test_calc.py
```

Baseline `calc.py`:

```python
def add(a, b):
    pass
```

Prompt sent to Cursor ACP:

```text
Implement add() in calc.py.
Modify no other files.
Run pytest.
Stop after the test passes.
```

## Result

Status: `PARTIAL`

Cursor successfully edited the requested file and did not modify the test file:

```diff
diff --git a/calc.py b/calc.py
index c0941d6..4693ad3 100644
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    pass
+    return a + b
```

Observed post-smoke git state:

```text
 M calc.py
```

The prompt did not complete because Cursor requested permission to run `pytest`, and this checkpoint intentionally does not yet implement a permission broker. Independent verification also found that the WSL Python environment does not currently provide pytest:

```text
/usr/bin/python3: No module named pytest
```

## Update Types

Observed `session/update` kinds:

```json
{
  "session_info_update": 1,
  "available_commands_update": 1,
  "agent_message_chunk": 25,
  "tool_call": 5,
  "tool_call_update": 9
}
```

Unknown updates:

```json
[]
```

Observed tool call kinds:

```json
[
  "read",
  "search",
  "read",
  "edit",
  "execute"
]
```

The edit was reported as a `tool_call_update` with diff content:

```json
{
  "sessionUpdate": "tool_call_update",
  "status": "completed",
  "content": [
    {
      "type": "diff",
      "path": "/tmp/taskbus-acp-smoke/calc.py",
      "oldText": "def add(a, b):\n    pass\n",
      "newText": "def add(a, b):\n    return a + b\n"
    }
  ]
}
```

## Permission Request

Cursor exposed tool approval as a JSON-RPC request from agent to client:

```json
{
  "jsonrpc": "2.0",
  "id": 0,
  "method": "session/request_permission",
  "params": {
    "sessionId": "<redacted-session-id>",
    "toolCall": {
      "toolCallId": "<redacted-tool-call-id>",
      "title": "`cd /tmp/taskbus-acp-smoke && pytest`",
      "kind": "execute",
      "status": "pending",
      "content": [
        {
          "type": "content",
          "content": {
            "type": "text",
            "text": "Not in allowlist: cd /tmp/taskbus-acp-smoke, pytest"
          }
        }
      ]
    },
    "options": [
      {
        "optionId": "allow-once",
        "name": "Allow once",
        "kind": "allow_once"
      },
      {
        "optionId": "allow-always",
        "name": "Allow always",
        "kind": "allow_always"
      },
      {
        "optionId": "reject-once",
        "name": "Reject",
        "kind": "reject_once"
      }
    ]
  }
}
```

## Static Policy Response

The thin static permission broker was then tested against the same smoke. It replied to the first `pytest` permission request with:

```json
{
  "jsonrpc": "2.0",
  "id": 0,
  "result": {
    "outcome": {
      "outcome": "selected",
      "optionId": "allow-once"
    }
  }
}
```

This allowed the same Cursor ACP session to continue and finish:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "stopReason": "end_turn"
  }
}
```

Observed policy decisions from the auto-permission run:

```json
[
  {
    "action": "allow",
    "tool_title": "`cd /tmp/taskbus-acp-smoke && pytest`",
    "option_id": "allow-once"
  },
  {
    "action": "deny",
    "tool_title": "`cd /tmp/taskbus-acp-smoke && python3 -m pytest`",
    "option_id": "reject-once"
  },
  {
    "action": "deny",
    "tool_title": "`pip install pytest -q && cd /tmp/taskbus-acp-smoke && pytest`",
    "reason": "Command matches a denied shell pattern.",
    "option_id": "reject-once"
  }
]
```

The auto-permission run still did not fully pass because pytest is not installed in the WSL Python environment:

```text
/usr/bin/python3: No module named pytest
```

The final repository diff remained constrained to `calc.py`.

## Answers

1. Can Cursor ACP reliably modify files in the requested `cwd`?
   `YES` for this smoke. It modified only `/tmp/taskbus-acp-smoke/calc.py`.

2. Is tool confirmation exposed as a structured ACP permission event?
   `YES`. It used `session/request_permission` with `toolCall` and `options`.

3. Can TaskBus static policy answer and resume the same session?
   `YES`. The broker selected `allow-once` for the first pytest request, rejected later non-allowlisted commands, and Cursor returned `stopReason=end_turn`.

## Next Boundary

Promote the probe path into a guarded `CursorAcpWorker` adapter. Keep it read/write scoped, run Evaluator after Cursor stops, and do not connect Codex liaison or GitHub yet.
