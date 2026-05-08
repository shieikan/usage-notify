from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3


SERVER_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
  client_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL,
  model TEXT,
  source TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  raw_payload TEXT NOT NULL,
  UNIQUE (client_id, event_id),
  UNIQUE (client_id, session_id, turn_id)
);

CREATE INDEX IF NOT EXISTS idx_usage_events_client_occurred_at
  ON usage_events (client_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_usage_events_client_model_occurred_at
  ON usage_events (client_id, model, occurred_at);
"""


def connect_server(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SERVER_SCHEMA)
    return connection


def upsert_clients(connection: sqlite3.Connection, token_map: dict[str, str]) -> None:
    now = utc_now()
    with connection:
        for client_id, token in token_map.items():
            connection.execute(
                """
                INSERT INTO clients (client_id, display_name, token_hash, status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                  token_hash = excluded.token_hash,
                  status = 'active',
                  updated_at = excluded.updated_at
                """,
                (client_id, client_id, hash_token(token), now, now),
            )


def client_id_for_token(connection: sqlite3.Connection, token: str) -> str | None:
    row = connection.execute(
        "SELECT client_id FROM clients WHERE token_hash = ? AND status = 'active'",
        (hash_token(token),),
    ).fetchone()
    if row is None:
        return None
    return str(row["client_id"])


def store_usage_events(
    connection: sqlite3.Connection,
    authenticated_client_id: str,
    events: list[dict[str, object]],
) -> dict[str, list[object]]:
    accepted: list[str] = []
    duplicates: list[str] = []
    rejected: list[dict[str, object]] = []
    received_at = utc_now()

    with connection:
        for event in events:
            error = validate_event(authenticated_client_id, event)
            event_id = str(event.get("event_id", ""))
            if error is not None:
                rejected.append({"event_id": event_id, "error": error})
                continue

            try:
                connection.execute(
                    """
                    INSERT INTO usage_events (
                      client_id, event_id, session_id, turn_id, occurred_at, received_at,
                      input_tokens, output_tokens, total_tokens, model, source, schema_version, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["client_id"],
                        event["event_id"],
                        event["session_id"],
                        event["turn_id"],
                        event["timestamp"],
                        received_at,
                        event["input_tokens"],
                        event["output_tokens"],
                        event["total_tokens"],
                        event.get("model"),
                        event["source"],
                        event["schema_version"],
                        json.dumps(event, separators=(",", ":")),
                    ),
                )
                accepted.append(event_id)
            except sqlite3.IntegrityError:
                duplicates.append(event_id)

    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}


def validate_event(authenticated_client_id: str, event: dict[str, object]) -> str | None:
    required = [
        "event_id",
        "client_id",
        "session_id",
        "turn_id",
        "timestamp",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "source",
        "schema_version",
    ]
    for key in required:
        if key not in event:
            return f"missing {key}"
    if event["client_id"] != authenticated_client_id:
        return "client_id does not match token"
    for key in ["input_tokens", "output_tokens", "total_tokens", "schema_version"]:
        if not isinstance(event[key], int):
            return f"{key} must be integer"
    return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

