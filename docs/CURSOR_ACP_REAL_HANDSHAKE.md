# Cursor ACP Real Handshake

Captured on 2026-06-09 from WSL Ubuntu using the independent Cursor Agent CLI.

## Environment

- Command: `/home/fakebob/.local/bin/agent acp`
- Agent version: `2026.06.04-5fd875e`
- Workspace: `/mnt/d/PhD/agent_tool`
- Framing: JSON Lines
- Authentication status before probe: not logged in

Raw probe logs are intentionally ignored under `taskbus/state/*.jsonl`.

## Initialize Request

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientCapabilities": {
      "fs": {
        "readTextFile": true,
        "writeTextFile": true
      },
      "terminal": true
    },
    "clientInfo": {
      "name": "taskbus",
      "title": "TaskBus",
      "version": "0.1.0"
    }
  }
}
```

## Initialize Response

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": 1,
    "agentCapabilities": {
      "loadSession": true,
      "mcpCapabilities": {
        "http": true,
        "sse": true
      },
      "promptCapabilities": {
        "audio": false,
        "embeddedContext": false,
        "image": true
      },
      "sessionCapabilities": {
        "list": {}
      }
    },
    "authMethods": [
      {
        "id": "cursor_login",
        "name": "Cursor Login",
        "description": "Authenticate using existing Cursor login credentials. Run 'agent login' first if not logged in."
      }
    ]
  }
}
```

## Findings

- `agent acp` starts successfully in WSL Ubuntu.
- JSON Lines framing works for the initial handshake.
- The existing `build_initialize_request()` shape is accepted.
- ACP can report auth methods even when the CLI is not logged in.
- The first required auth method is `cursor_login`; interactive `agent login` is still needed before prompt/session testing.
- The probe terminates the process after the first response, so `returncode=143` is expected from termination, not an ACP failure.

## Next Boundary

Do not map business events yet. Next work should be limited to authentication and one read-only session/prompt round trip after `agent login` succeeds.
