from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Iterable

from .collector import UsageEvent


SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
  event_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  cached_input_tokens INTEGER,
  non_cached_input_tokens INTEGER,
  output_tokens INTEGER NOT NULL,
  reasoning_output_tokens INTEGER,
  total_tokens INTEGER NOT NULL,
  model TEXT,
  source TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  sent_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (client_id, session_id, turn_id)
);

CREATE INDEX IF NOT EXISTS idx_usage_events_status_retry
  ON usage_events (status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_usage_events_client_occurred_at
  ON usage_events (client_id, occurred_at);
"""


def connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    ensure_optional_columns(connection)
    return connection


def ensure_optional_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(usage_events)").fetchall()
    }
    optional_columns = {
        "cached_input_tokens": "INTEGER",
        "non_cached_input_tokens": "INTEGER",
        "reasoning_output_tokens": "INTEGER",
    }
    with connection:
        for column, column_type in optional_columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE usage_events ADD COLUMN {column} {column_type}")


def insert_events(connection: sqlite3.Connection, events: Iterable[UsageEvent]) -> int:
    now = utc_now()
    inserted = 0
    with connection:
        for event in events:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO usage_events (
                  event_id, client_id, session_id, turn_id, occurred_at,
                  input_tokens, cached_input_tokens, non_cached_input_tokens,
                  output_tokens, reasoning_output_tokens, total_tokens, model, source,
                  schema_version, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    event.event_id,
                    event.client_id,
                    event.session_id,
                    event.turn_id,
                    event.occurred_at,
                    event.input_tokens,
                    event.cached_input_tokens,
                    event.non_cached_input_tokens,
                    event.output_tokens,
                    event.reasoning_output_tokens,
                    event.total_tokens,
                    event.model,
                    event.source,
                    event.schema_version,
                    now,
                    now,
                ),
            )
            inserted += cursor.rowcount
    return inserted


def pending_events(
    connection: sqlite3.Connection,
    limit: int,
    include_waiting: bool = False,
) -> list[dict[str, object]]:
    now = utc_now()
    retry_filter = "1 = 1" if include_waiting else "(next_retry_at IS NULL OR next_retry_at <= ?)"
    params: tuple[object, ...]
    if include_waiting:
        params = (limit,)
    else:
        params = (now, limit)
    rows = connection.execute(
        f"""
        SELECT
          event_id, client_id, session_id, turn_id, occurred_at,
          input_tokens, cached_input_tokens, non_cached_input_tokens,
          output_tokens, reasoning_output_tokens, total_tokens, model, source, schema_version
        FROM usage_events
        WHERE
          status IN ('pending', 'failed')
          AND {retry_filter}
        ORDER BY occurred_at ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_to_event_payload(row) for row in rows]


def mark_sent(connection: sqlite3.Connection, event_ids: Iterable[str]) -> int:
    ids = list(event_ids)
    if not ids:
        return 0
    now = utc_now()
    with connection:
        return connection.executemany(
            """
            UPDATE usage_events
            SET status = 'sent', sent_at = ?, last_error = NULL, updated_at = ?
            WHERE event_id = ?
            """,
            [(now, now, event_id) for event_id in ids],
        ).rowcount


def mark_failed(connection: sqlite3.Connection, event_ids: Iterable[str], error: str) -> int:
    ids = list(event_ids)
    if not ids:
        return 0
    now = utc_now()
    with connection:
        for event_id in ids:
            retry_count = connection.execute(
                "SELECT retry_count FROM usage_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()["retry_count"]
            next_retry_at = retry_at(retry_count + 1)
            connection.execute(
                """
                UPDATE usage_events
                SET
                  status = 'failed',
                  retry_count = retry_count + 1,
                  next_retry_at = ?,
                  last_error = ?,
                  updated_at = ?
                WHERE event_id = ?
                """,
                (next_retry_at, error[:1000], now, event_id),
            )
    return len(ids)


def status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT status, COUNT(*) AS count FROM usage_events GROUP BY status ORDER BY status"
    ).fetchall()
    return {row["status"]: row["count"] for row in rows}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retry_at(retry_count: int) -> str:
    minutes = [1, 5, 15, 60, 360, 1440]
    delay = minutes[min(retry_count - 1, len(minutes) - 1)]
    return (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat().replace("+00:00", "Z")


def payload_json(events: list[dict[str, object]]) -> bytes:
    return json.dumps({"events": events}, separators=(",", ":")).encode("utf-8")


def _row_to_event_payload(row: sqlite3.Row) -> dict[str, object]:
    event = {
        "event_id": row["event_id"],
        "client_id": row["client_id"],
        "session_id": row["session_id"],
        "turn_id": row["turn_id"],
        "timestamp": row["occurred_at"],
        "input_tokens": row["input_tokens"],
        "cached_input_tokens": row["cached_input_tokens"],
        "non_cached_input_tokens": row["non_cached_input_tokens"],
        "output_tokens": row["output_tokens"],
        "reasoning_output_tokens": row["reasoning_output_tokens"],
        "total_tokens": row["total_tokens"],
        "model": row["model"],
        "source": row["source"],
        "schema_version": row["schema_version"],
    }
    return {key: value for key, value in event.items() if value is not None}
