# Cursor ACP Real Handshake

Captured on 2026-06-09 from WSL Ubuntu using the independent Cursor Agent CLI.

## Environment

- Command: `/home/fakebob/.local/bin/agent acp`
- Agent version: `2026.06.04-5fd875e`
- Workspace: `/mnt/d/PhD/agent_tool`
- Framing: JSON Lines
- Authentication status:
  - initialize-only probe: not logged in
  - session/new probe: logged in via Cursor CLI

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

## New Session Request

Captured after Cursor CLI login. This request does not include prompt text or file contents.

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/mnt/d/PhD/agent_tool",
    "mcpServers": []
  }
}
```

## New Session Response

Sensitive values are redacted. The full model list is intentionally omitted because it is account- and time-dependent.

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "sessionId": "<redacted-session-id>",
    "modes": {
      "currentModeId": "agent",
      "availableModes": [
        {
          "id": "agent",
          "name": "Agent"
        },
        {
          "id": "plan",
          "name": "Plan"
        },
        {
          "id": "ask",
          "name": "Ask"
        }
      ]
    },
    "models": {
      "currentModelId": "<redacted-current-model-id>",
      "availableModels": "<redacted-model-list>"
    },
    "configOptions": [
      {
        "id": "mode",
        "type": "select",
        "currentValue": "agent"
      },
      {
        "id": "model",
        "type": "select",
        "currentValue": "<redacted-current-model-id>"
      }
    ]
  }
}
```

## Set Mode Request

Captured after `session/new`. This switches the created session from the default `agent` mode to read-only `ask` mode before any future prompt probe.

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/set_mode",
  "params": {
    "sessionId": "<redacted-session-id>",
    "modeId": "ask"
  }
}
```

## Set Mode Notification And Response

Cursor sends a `session/update` notification before the JSON-RPC response for `session/set_mode`, so clients must wait for the matching response id rather than treating the next protocol message as the response.

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<redacted-session-id>",
    "update": {
      "sessionUpdate": "current_mode_update",
      "currentModeId": "ask"
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {}
}
```

## Prompt Request

Captured after switching to `ask` mode. The prompt was intentionally minimal and did not ask Cursor to inspect files.

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "session/prompt",
  "params": {
    "sessionId": "<redacted-session-id>",
    "prompt": [
      {
        "type": "text",
        "text": "Reply exactly TASKBUS_ACP_OK. Do not read files or inspect the repository."
      }
    ]
  }
}
```

## Prompt Updates And Response

Cursor may emit multiple `session/update` notifications before the final response. The observed update sequence was:

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<redacted-session-id>",
    "update": {
      "sessionUpdate": "available_commands_update",
      "availableCommands": "<redacted-command-list>"
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<redacted-session-id>",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {
        "type": "text",
        "text": "TASK"
      }
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<redacted-session-id>",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {
        "type": "text",
        "text": "BUS_ACP_OK"
      }
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<redacted-session-id>",
    "update": {
      "sessionUpdate": "session_info_update",
      "title": "Taskbus Acp Ok"
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "stopReason": "end_turn"
  }
}
```

## Findings

- `agent acp` starts successfully in WSL Ubuntu.
- JSON Lines framing works for the initial handshake.
- The existing `build_initialize_request()` shape is accepted.
- ACP can report auth methods even when the CLI is not logged in.
- After CLI login, `session/new` succeeds with `cwd` and an empty `mcpServers` list.
- New sessions currently start in `agent` mode, so TaskBus should explicitly call `session/set_mode` before read-only prompt probes.
- `session/set_mode` returns a mode update notification before the matching id response.
- Cursor's current prompt capabilities report `embeddedContext=false`, so TaskBus should prefer text blocks and resource links for the next prompt probe.
- A minimal `ask` mode `session/prompt` succeeds and returns streamed `agent_message_chunk` notifications before the final `stopReason=end_turn` response.
- Agent text chunks must be concatenated in arrival order to reconstruct the assistant message.
- The first required auth method is `cursor_login`.
- The probe terminates the process after the first response, so `returncode=143` is expected from termination, not an ACP failure.

## Next Boundary

Do not map business events yet. Next work should be a small parser for `session/update` notifications and prompt stop reasons, followed by a guarded adapter that can run only read-only `ask` mode prompts until permission mapping is implemented.
