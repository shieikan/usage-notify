from __future__ import annotations

import json
import os
import sys
import time

from .collector import collect_turn_from_log
from .store import connect, insert_events, mark_failed, mark_sent, pending_events
from .uploader import UploadError, upload_events


def run_stop_hook(
    *,
    db_path: str,
    client_id: str,
    codex_log: str,
    api_base_url: str,
    token: str,
    wait_seconds: float = 12.0,
    poll_interval_seconds: float = 0.5,
    batch_size: int = 50,
    timeout_seconds: float = 5.0,
) -> tuple[bool, str | None]:
    payload = _read_stdin_json()
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return False, "Codex Stop hook payload did not include turn_id"

    deadline = time.monotonic() + wait_seconds
    event_found = False
    while time.monotonic() <= deadline:
        if os.path.exists(codex_log):
            event = collect_turn_from_log(codex_log, client_id, turn_id)
            if event is not None:
                with connect(db_path) as connection:
                    insert_events(connection, [event])
                event_found = True
                break
        time.sleep(poll_interval_seconds)

    if not event_found:
        return False, f"Usage line for turn_id={turn_id} was not found before timeout"

    with connect(db_path) as connection:
        events = pending_events(connection, batch_size, include_waiting=True)
        if not events:
            return True, None
        event_ids = [str(event["event_id"]) for event in events]
        try:
            accepted, duplicates = upload_events(api_base_url, token, events, timeout_seconds)
        except UploadError as error:
            mark_failed(connection, event_ids, str(error))
            return False, f"Usage upload failed: {error}"
        sent_ids = accepted + duplicates
        mark_sent(connection, sent_ids)
        failed_ids = [event_id for event_id in event_ids if event_id not in set(sent_ids)]
        mark_failed(connection, failed_ids, "API did not accept event")
        if failed_ids:
            return False, f"Usage upload left {len(failed_ids)} event(s) unaccepted"
    return True, None


def _read_stdin_json() -> dict[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}

