from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, task_id: str) -> Path:
        safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in task_id)
        return self.root / f"{safe_id}.json"

    def save(self, task_id: str, state: dict[str, Any]) -> Path:
        path = self.path_for(task_id)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def load(self, task_id: str) -> dict[str, Any]:
        path = self.path_for(task_id)
        return json.loads(path.read_text(encoding="utf-8"))
