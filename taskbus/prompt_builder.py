from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "prompts" / "cursor_worker_contract.md"
MAX_PROMPT_CHARS = 12000
SECRET_KEYS = {
    "secret",
    "secrets",
    "token",
    "tokens",
    "api_key",
    "apikey",
    "password",
    "credential",
    "credentials",
}


class PromptBuildError(ValueError):
    pass


def build_cursor_worker_prompt(
    task: dict[str, Any],
    template_path: Path | str = DEFAULT_TEMPLATE,
    max_chars: int = MAX_PROMPT_CHARS,
) -> str:
    sanitized = _sanitize_task(task)
    scope = sanitized.get("scope", {})
    prompt = Path(template_path).read_text(encoding="utf-8").format(
        objective=_block_text(sanitized.get("objective", "")),
        allowed_paths=_bullet_list(scope.get("allowed_paths", [])),
        forbidden_paths=_bullet_list(scope.get("forbidden_paths", [])),
        acceptance=_bullet_list(sanitized.get("acceptance", [])),
        test_commands=_bullet_list(sanitized.get("test_commands", [])),
    )
    if len(prompt) > max_chars:
        raise PromptBuildError(f"Prompt length {len(prompt)} exceeds limit {max_chars}.")
    return prompt


def _sanitize_task(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS:
                continue
            clean[key] = _sanitize_task(item)
        return clean
    if isinstance(value, list):
        return [_sanitize_task(item) for item in value]
    return value


def _bullet_list(values: Any) -> str:
    if not values:
        return "- <none>"
    if not isinstance(values, list):
        values = [values]
    return "\n".join(f"- {_inline_text(item)}" for item in values)


def _block_text(value: Any) -> str:
    return str(value).strip()


def _inline_text(value: Any) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
