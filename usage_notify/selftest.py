from __future__ import annotations

from datetime import date
import tempfile

from .collector import collect_from_log
from .report import daily_report
from .server_store import connect_server, store_usage_events, upsert_clients
from .store import connect, insert_events, mark_sent, pending_events, status_counts


SAMPLE_LOG = (
    "2026-05-06T10:56:53.008725Z  INFO "
    "session_loop{thread_id=sample-session}:submission_dispatch{}:"
    "turn{otel.name=\"session_task.turn\" thread.id=sample-session "
    "turn.id=sample-turn model=gpt-5.5 "
    "codex.turn.token_usage.input_tokens=100 "
    "codex.turn.token_usage.output_tokens=25 "
    "codex.turn.token_usage.total_tokens=125}: close\n"
)


def run_self_test() -> str:
    with tempfile.TemporaryDirectory() as directory:
        local_db = f"{directory}/local.db"
        server_db = f"{directory}/server.db"
        log_path = f"{directory}/codex-tui.log"
        client_id = "self-test-client"

        with open(log_path, "w", encoding="utf-8") as file:
            file.write(SAMPLE_LOG)

        events = collect_from_log(log_path, client_id)
        with connect(local_db) as local_connection:
            inserted = insert_events(local_connection, events)
            payloads = pending_events(local_connection, 50)

            with connect_server(server_db) as server_connection:
                upsert_clients(server_connection, {client_id: "self-test-token"})
                result = store_usage_events(server_connection, client_id, payloads)

            sent_ids = result["accepted"] + result["duplicates"]
            mark_sent(local_connection, sent_ids)
            local_counts = status_counts(local_connection)

        report = daily_report(server_db, date(2026, 5, 6), "Asia/Tokyo")
        if inserted != 1:
            raise RuntimeError(f"expected inserted=1, got {inserted}")
        if result["accepted"] != [payloads[0]["event_id"]]:
            raise RuntimeError(f"expected one accepted event, got {result}")
        if local_counts != {"sent": 1}:
            raise RuntimeError(f"expected local sent count, got {local_counts}")
        if "self-test-client: 125" not in report:
            raise RuntimeError("expected client total in report")
        return report

