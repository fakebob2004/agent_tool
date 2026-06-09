from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

EventType = Literal[
    "permission_request",
    "semantic_question",
    "finished",
    "policy_decision",
    "liaison_decision",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BridgeEvent:
    type: EventType
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)

    @property
    def is_finished(self) -> bool:
        return self.type == "finished"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeEvent":
        return cls(
            id=str(data["id"]),
            type=data["type"],
            message=str(data["message"]),
            payload=dict(data.get("payload", {})),
            created_at=str(data["created_at"]),
        )
