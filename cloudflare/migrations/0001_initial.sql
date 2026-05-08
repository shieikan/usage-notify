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

CREATE TABLE IF NOT EXISTS notification_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_scope TEXT NOT NULL,
  scope_client_id TEXT,
  period_type TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  scheduled_for TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (report_scope, scope_client_id, period_type, period_start, period_end, scheduled_for)
);

