from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import uuid


TOKEN_KEYS = {
    "input_tokens",
    "cached_input_tokens",
    "non_cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
}


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    client_id: str
    session_id: str
    turn_id: str
    occurred_at: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model: str | None
    source: str
    schema_version: int
    cached_input_tokens: int | None = None
    non_cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None


def parse_codex_usage_line(line: str, client_id: str) -> UsageEvent | None:
    if "codex.turn.token_usage." not in line:
        return None

    timestamp = _parse_timestamp(line)
    if timestamp is None:
        return None
    session_id = _match_required(r"thread\.id=([^\s}]+)", line)
    turn_id = _match_required(r"turn\.id=([^\s}]+)", line)
    model = _match_optional(r"\bmodel=([^\s}]+)", line)
    token_values = _parse_token_values(line)

    if not session_id or not turn_id:
        return None
    if "input_tokens" not in token_values or "output_tokens" not in token_values:
        return None

    input_tokens = token_values["input_tokens"]
    output_tokens = token_values["output_tokens"]
    total_tokens = token_values.get("total_tokens", input_tokens + output_tokens)
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"usage-notify:{client_id}:{session_id}:{turn_id}"))

    return UsageEvent(
        event_id=event_id,
        client_id=client_id,
        session_id=session_id,
        turn_id=turn_id,
        occurred_at=timestamp,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        model=model,
        source="codex-tui-log",
        schema_version=1,
        cached_input_tokens=token_values.get("cached_input_tokens"),
        non_cached_input_tokens=token_values.get("non_cached_input_tokens"),
        reasoning_output_tokens=token_values.get("reasoning_output_tokens"),
    )


def collect_from_log(path: str, client_id: str) -> list[UsageEvent]:
    events: list[UsageEvent] = []
    with open(path, "r", encoding="utf-8", errors="replace") as file:
        for line in file:
            event = parse_codex_usage_line(line, client_id)
            if event is not None:
                events.append(event)
    return events


def collect_turn_from_log(path: str, client_id: str, turn_id: str) -> UsageEvent | None:
    with open(path, "r", encoding="utf-8", errors="replace") as file:
        for line in reversed(file.readlines()):
            if f"turn.id={turn_id}" not in line:
                continue
            event = parse_codex_usage_line(line, client_id)
            if event is not None:
                return event
    return None


def _parse_timestamp(line: str) -> str | None:
    raw = line.split(" ", 1)[0]
    try:
        if raw.endswith("Z"):
            parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
        else:
            parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_token_values(line: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for key, raw_value in re.findall(r"codex\.turn\.token_usage\.([a-z_]+)=(\d+)", line):
        if key in TOKEN_KEYS:
            values[key] = int(raw_value)
    return values


def _match_required(pattern: str, text: str) -> str | None:
    return _match_optional(pattern, text)


def _match_optional(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    if match is None:
        return None
    return match.group(1)
