# GitHub Issues + Actions + Local Bridge Task Bus MVP Plan

## Goal

Build a local-first task bus for coding agents.

- GitHub Issues and PRs are the durable control plane.
- The local bridge is the real-time execution loop.
- Cursor handles repo edits and tests.
- A short-lived Codex liaison handles semantic questions.
- A static policy engine handles routine permission decisions.

The first version does not use MCP and does not put the Cursor conversation loop inside GitHub Actions.

## MVP Scope

### M0: Local Task Contract

Deliver:

- `taskbus/schemas/task.schema.json`
- `taskbus/schemas/bridge_event.schema.json`
- `taskbus/schemas/liaison_decision.schema.json`
- `taskbus/examples/csv_loader_task.json`
- lightweight local validation in Python

Acceptance:

- A sample TaskSpec validates.
- Missing `acceptance`, `scope.allowed_paths`, or `test_commands` fails.

### M1: Bridge Dry Run

Deliver:

- `taskbus/bridge.py`
- `taskbus/events.py`
- `taskbus/state.py`
- `taskbus/cursor_session.py`

Acceptance:

- A local command reads a TaskSpec.
- The bridge simulates permission, semantic-question, and finished events.
- The bridge writes `taskbus/state/<task_id>.json`.
- The bridge writes a result JSON and exits successfully for the sample task.

### M2: Policy Engine

Deliver:

- `taskbus/policy.py`
- static allow / deny / ask_liaison / escalate decisions

Acceptance:

- Routine test and inspect commands are allowed.
- Dangerous commands such as hard reset, force push, recursive delete, and sudo are denied or escalated.
- Dependency installs and network access require liaison/supervisor decision.

### M3: Liaison Adapter

Deliver:

- `taskbus/codex_liaison.py`
- compact context builder
- structured JSON decision parser

Acceptance:

- Semantic questions are routed to liaison.
- Only compact task state, recent event text, relevant diff summary, and test summary are passed.
- The bridge can send `instruction_to_cursor` back to the worker.

### M4: Real Cursor Session

Deliver:

- Cursor CLI/session adapter
- stdout/stderr event recognition
- stdin answer writing
- transcript summary persistence

Acceptance:

- One low-risk local task can run continuously.
- At least three Cursor question/answer rounds can be handled.
- Permission confirmations do not call liaison.

### M5: Git/Test Evaluator

Deliver:

- changed-file collection
- allowed/forbidden path gate
- diff-size gate
- test command runner
- result summary

Acceptance:

- Out-of-scope edits fail.
- Failed tests fail the task.
- Result JSON includes changed files, tests, summary, and remaining risks.

### M6: GitHub Control Plane

Deliver:

- Issue form
- `intake.yml`
- `ci.yml`
- `github_ops.py`
- PR body and Issue comment generation

Acceptance:

- A GitHub Issue can produce a normalized TaskSpec.
- A successful local run can create a PR.
- CI and final status are linked back to the Issue.

## Current Development Order

1. Implement M0, M1, and M2 as a runnable local vertical slice.
2. Add tests for validation, policy decisions, and dry-run state output.
3. Add liaison adapter behind a small interface.
4. Replace dry-run worker with real Cursor session adapter.
5. Add evaluator gates before any GitHub integration.

## Current Implementation Checkpoint

Implemented:

- Baseline git repository and dry-run MVP.
- Evaluator gates for path scope, changed-file count, diff size, and test results.
- Compact liaison adapter with structured decision parsing.
- Subprocess worker adapter for Cursor-compatible commands that emit `TASKBUS_EVENT:{json}` lines.
- Stdin reply path from bridge decisions back to the worker.
- Smoke worker example for local protocol testing.
- Cursor CLI probe document for the local Windows install.
- Worker capability model for batch vs interactive routing.
- Cursor worker prompt contract and prompt builder.
- Session/nonce/sequence checks for scripted worker events.
- ACP-first interface decision document.
- PowerShell probe script for desktop Cursor, `agent`, `cursor-agent`, and ACP availability.
- JSON-RPC-over-stdio ACP transport skeleton with request/response, notification, stderr, timeout, and cleanup tests.
- ACP message framing abstraction for JSON Lines and Content-Length transports.
- Raw `agent acp` probe script that records client, agent, stderr, timeout, and process-exit messages to ignored JSONL.
- Real `agent acp` initialize handshake captured from WSL Ubuntu; JSON Lines framing and current initialize params are confirmed.

Still pending:

- Cursor Agent CLI login in WSL, then read-only session/prompt ACP round trip.
- Confirmed Cursor SDK or Hook adapter.
- Real-world Cursor command presets after interface confirmation.
- GitHub Issue/PR control plane.
- Reviewer and summarizer artifacts.

## Non-Goals For The First Slice

- No MCP adapter.
- No remote worker.
- No GitHub Actions execution loop.
- No automatic dependency installation.
- No production secrets access.
- No deployment.
- No automatic public API change.
- No destructive git history operations.

## Definition Of Done For MVP

- A local TaskSpec or GitHub Issue can start a task.
- The bridge can run or dry-run a worker session.
- Policy handles routine permission requests without model calls.
- Liaison handles only semantic questions through compact context.
- Worker changes are constrained by TaskSpec scope.
- Test failures, path violations, and diff-limit violations fail the task.
- Successful tasks create traceable PR artifacts.
- Failed tasks leave structured logs and a compact summary.
