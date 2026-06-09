from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from taskbus.bridge import TaskSpecError, load_task, run_dry, run_worker, validate_task


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TASK = ROOT / "taskbus" / "examples" / "csv_loader_task.json"


class BridgeTests(unittest.TestCase):
    def test_sample_task_validates(self) -> None:
        task = load_task(SAMPLE_TASK)
        self.assertEqual(task["id"], "local-csv-loader-empty-input")

    def test_missing_acceptance_fails(self) -> None:
        task = load_task(SAMPLE_TASK)
        task.pop("acceptance")
        with self.assertRaises(TaskSpecError):
            validate_task(task)

    def test_dry_run_writes_state(self) -> None:
        task = load_task(SAMPLE_TASK)
        with tempfile.TemporaryDirectory() as tmp:
            result = run_dry(task, tmp)
            state_path = Path(result["state_path"])
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(state_path.exists())
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["task_id"], task["id"])
            self.assertGreaterEqual(len(saved["events"]), 3)

    def test_worker_command_can_complete(self) -> None:
        task = load_task(SAMPLE_TASK)
        command = [sys.executable, "taskbus/examples/smoke_worker.py"]
        with tempfile.TemporaryDirectory() as tmp:
            result = run_worker(task, tmp, command, ROOT)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["mode"], "worker")
        self.assertTrue(result["evaluation"]["passed"])


if __name__ == "__main__":
    unittest.main()
