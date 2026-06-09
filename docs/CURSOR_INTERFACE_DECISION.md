# Cursor Interface Decision

## Current Decision

Preferred integration path: **ACP**.

Fallback path: **Cursor Agent headless JSON / stream-JSON batch**, once an independent `cursor-agent` or `agent` CLI is installed and confirmed.

Policy integration path: **Hooks**, after the session transport is proven.

Legacy/testing path: authenticated `TASKBUS_EVENT` scripted worker protocol.

The desktop `cursor.cmd` entrypoint is not treated as a stable automation endpoint. In the local probe, it exposes desktop Cursor options and mentions an `agent` subcommand, but it does not expose the Cursor Agent CLI-specific parameter surface needed for automation.

## Why ACP First

ACP is JSON-RPC over stdio. That aligns with TaskBus better than asking a model to print control events in normal stdout:

- Structured request/response boundaries.
- Natural session and notification model.
- No natural-language output parsing.
- Better fit for a bridge that owns policy, evaluator, liaison, and audit state.

## Adapter Priority

1. `CursorAcpSession`
   - Owns JSON-RPC framing, request IDs, stdio transport, notifications, stderr, timeout, and process cleanup.
   - Does not hard-code Cursor method mappings until real `agent acp` samples are available.

2. Cursor Agent headless batch
   - Uses `--print --output-format json` or `stream-json`.
   - Runs one prompt to completion, then TaskBus evaluates and optionally starts a repair run.

3. Hooks
   - Best suited for tool approval and audit events.
   - Routes tool permission checks into `policy.py`.

4. SDK
   - Deferred until a public, stable local SDK entrypoint is identified for this environment.

## Not Allowed For Now

- Do not call Cursor's internal extension assets directly.
- Do not import internal `agent-sdk` files from the desktop install.
- Do not infer Cursor Agent CLI options from desktop `cursor.cmd`.
- Do not treat unauthenticated stdout text as a trusted worker event channel.

## Next Checkpoint

Use `scripts/probe_cursor_interfaces.ps1` to find a public `agent` or `cursor-agent` command. If `agent acp` or `cursor-agent acp` becomes available, run the ACP transport against the real process and record sanitized sample messages before mapping TaskBus events.
