"""Parse OpenCode's JSON event output into assistant text."""

import json
from collections.abc import Iterable
from typing import Any


class OpenCodeOutputError(ValueError):
    """Raised when OpenCode does not return a usable assistant response."""


def collect_assistant_text(output: str) -> tuple[str, list[dict[str, Any]]]:
    """Collect text parts from newline-delimited OpenCode events."""
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OpenCodeOutputError("OpenCode returned malformed JSON output") from exc
        if not isinstance(event, dict):
            continue
        events.append(event)
        _collect_text(event, text_parts)
    content = "".join(text_parts).strip()
    if not content:
        raise OpenCodeOutputError("OpenCode returned no assistant response")
    return content, events


def collect_assistant_result(lines: Iterable[str]) -> tuple[str, int, int, int]:
    """Parse a line stream without retaining all OpenCode events in memory."""
    text_parts: list[str] = []
    event_count = 0
    input_tokens = output_tokens = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OpenCodeOutputError("OpenCode returned malformed JSON output") from exc
        if not isinstance(event, dict):
            continue
        event_count += 1
        _collect_text(event, text_parts)
        properties = event.get("properties", event)
        if not isinstance(properties, dict):
            continue
        usage = properties.get("usage", properties)
        if isinstance(usage, dict):
            input_tokens = int(
                usage.get("input_tokens", usage.get("prompt_tokens", input_tokens)) or input_tokens
            )
            output_tokens = int(
                usage.get("output_tokens", usage.get("completion_tokens", output_tokens)) or output_tokens
            )
    content = "".join(text_parts).strip()
    if not content:
        raise OpenCodeOutputError("OpenCode returned no assistant response")
    return content, event_count, input_tokens, output_tokens


def _collect_text(value: Any, text_parts: list[str]) -> None:
    """Collect only text-bearing event parts, excluding metadata strings."""
    if isinstance(value, dict):
        part = value.get("part")
        if (
            isinstance(part, dict)
            and part.get("type") in {"text", "reasoning"}
            and isinstance(part.get("text"), str)
        ):
            text_parts.append(part["text"])
        if value.get("type") in {"text", "assistant.text"} and isinstance(value.get("text"), str):
            text_parts.append(value["text"])
        if value.get("type") == "text" and isinstance(value.get("content"), str):
            text_parts.append(value["content"])
        for key in ("parts", "properties"):
            nested = value.get(key)
            if isinstance(nested, dict | list):
                _collect_text(nested, text_parts)
    elif isinstance(value, list):
        for item in value:
            _collect_text(item, text_parts)
