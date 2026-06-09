from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WorkerMode(str, Enum):
    BATCH = "batch"
    INTERACTIVE = "interactive"


@dataclass(frozen=True)
class WorkerCapabilities:
    mode: WorkerMode
    structured_events: bool
    supports_tool_review: bool
    supports_session_resume: bool
    supports_model_selection: bool
    trusted_event_channel: bool = False

    @property
    def interactive(self) -> bool:
        return self.mode == WorkerMode.INTERACTIVE

    @property
    def batch(self) -> bool:
        return self.mode == WorkerMode.BATCH

    def to_dict(self) -> dict[str, bool | str]:
        return {
            "mode": self.mode.value,
            "structured_events": self.structured_events,
            "supports_tool_review": self.supports_tool_review,
            "supports_session_resume": self.supports_session_resume,
            "supports_model_selection": self.supports_model_selection,
            "trusted_event_channel": self.trusted_event_channel,
        }


SCRIPTED_WORKER_CAPABILITIES = WorkerCapabilities(
    mode=WorkerMode.INTERACTIVE,
    structured_events=True,
    supports_tool_review=False,
    supports_session_resume=False,
    supports_model_selection=False,
    trusted_event_channel=True,
)

CURSOR_CLI_PROBED_CAPABILITIES = WorkerCapabilities(
    mode=WorkerMode.BATCH,
    structured_events=False,
    supports_tool_review=False,
    supports_session_resume=False,
    supports_model_selection=False,
    trusted_event_channel=False,
)
