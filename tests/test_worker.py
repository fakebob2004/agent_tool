from __future__ import annotations

import unittest

from taskbus.worker import (
    CURSOR_CLI_PROBED_CAPABILITIES,
    SCRIPTED_WORKER_CAPABILITIES,
    WorkerMode,
)


class WorkerCapabilitiesTests(unittest.TestCase):
    def test_scripted_worker_is_interactive_structured_and_trusted(self) -> None:
        self.assertTrue(SCRIPTED_WORKER_CAPABILITIES.interactive)
        self.assertTrue(SCRIPTED_WORKER_CAPABILITIES.structured_events)
        self.assertTrue(SCRIPTED_WORKER_CAPABILITIES.trusted_event_channel)

    def test_cursor_cli_probe_stays_conservative(self) -> None:
        self.assertEqual(CURSOR_CLI_PROBED_CAPABILITIES.mode, WorkerMode.BATCH)
        self.assertFalse(CURSOR_CLI_PROBED_CAPABILITIES.structured_events)
        self.assertFalse(CURSOR_CLI_PROBED_CAPABILITIES.trusted_event_channel)


if __name__ == "__main__":
    unittest.main()
