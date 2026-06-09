# Cursor ACP Probe

This checkpoint prepares TaskBus for a real Cursor Agent ACP run without mapping any business methods yet.

## Prerequisite

Install the independent Cursor terminal CLI. On Windows, first validate it from WSL:

```bash
wsl
curl https://cursor.com/install -fsS | bash
which agent
agent --version
agent --help
agent acp --help
```

The desktop `cursor.cmd` entrypoint is not enough for this checkpoint.

## Safe CLI Smoke Tests

Read-only probe in this repository:

```bash
cd /mnt/d/PhD/agent_tool
git status --short
agent --print --output-format json \
  "Read this repository. Do not modify anything. Report the number of Python files and the available test command."
git status --short
```

Minimal write smoke test in a temporary repository:

```bash
mkdir -p /tmp/taskbus-cursor-smoke
cd /tmp/taskbus-cursor-smoke
git init
printf 'def add(a, b):\n    pass\n' > calc.py
printf 'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n' > test_calc.py
git add .
git commit -m "baseline"

agent --print --output-format json \
  "Implement add in calc.py so test_calc.py passes. Modify no other files. Run pytest."

git diff
pytest -q
```

## Raw ACP Probe

After `agent acp --help` works:

```bash
cd /mnt/d/PhD/agent_tool
python scripts/probe_cursor_acp.py \
  --command agent acp \
  --framing jsonlines \
  --output taskbus/state/cursor_acp_probe.jsonl
```

If JSON Lines does not receive a response, retry:

```bash
python scripts/probe_cursor_acp.py \
  --command agent acp \
  --framing content-length \
  --output taskbus/state/cursor_acp_probe_content_length.jsonl
```

The output JSONL is intentionally ignored by git because it may include local paths, errors, or account state. Preserve sanitized examples later in documentation only after review.

## Success Criteria

- `agent` is available.
- `agent acp` starts.
- `initialize` gets a valid JSON-RPC response or a useful JSON-RPC error.
- Raw client-to-agent, agent-to-client, stderr, timeout, and process-exit records are captured.
- The process exits or is terminated cleanly.
