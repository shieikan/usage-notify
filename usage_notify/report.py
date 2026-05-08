from __future__ import annotations

from datetime import date, datetime, time, timezone
import os
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from .server_store import connect_server


class DiscordSendError(RuntimeError):
    pass


def daily_report(db_path: str, report_date: date, timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    start_local = datetime.combine(report_date, time.min, tzinfo=tz)
    end_local = datetime.combine(report_date, time.max, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = end_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    with connect_server(db_path) as connection:
        total = connection.execute(
            """
            SELECT
              COALESCE(SUM(input_tokens), 0) AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(total_tokens), 0) AS total_tokens,
              COUNT(DISTINCT session_id) AS sessions
            FROM usage_events
            WHERE occurred_at >= ? AND occurred_at <= ?
            """,
            (start_utc, end_utc),
        ).fetchone()
        by_client = connection.execute(
            """
            SELECT client_id, SUM(total_tokens) AS total_tokens
            FROM usage_events
            WHERE occurred_at >= ? AND occurred_at <= ?
            GROUP BY client_id
            ORDER BY total_tokens DESC
            """,
            (start_utc, end_utc),
        ).fetchall()
        by_model = connection.execute(
            """
            SELECT COALESCE(model, 'unknown') AS model, SUM(total_tokens) AS total_tokens
            FROM usage_events
            WHERE occurred_at >= ? AND occurred_at <= ?
            GROUP BY model
            ORDER BY total_tokens DESC
            """,
            (start_utc, end_utc),
        ).fetchall()
        by_client_model = connection.execute(
            """
            SELECT client_id, COALESCE(model, 'unknown') AS model, SUM(total_tokens) AS total_tokens
            FROM usage_events
            WHERE occurred_at >= ? AND occurred_at <= ?
            GROUP BY client_id, model
            ORDER BY client_id ASC, total_tokens DESC
            """,
            (start_utc, end_utc),
        ).fetchall()

    lines = [
        "Codex usage report",
        "",
        f"Period: {report_date.isoformat()} {timezone_name}",
        f"Total: {int(total['total_tokens']):,} tokens",
        f"Input: {int(total['input_tokens']):,}",
        f"Output: {int(total['output_tokens']):,}",
        f"Sessions: {int(total['sessions']):,}",
        "",
        "By client:",
    ]
    lines.extend(_format_rows(by_client, "client_id"))
    lines.append("")
    lines.append("By model:")
    lines.extend(_format_rows(by_model, "model"))
    lines.append("")
    lines.append("By client and model:")
    if by_client_model:
        for row in by_client_model:
            lines.append(f"- {row['client_id']} / {row['model']}: {int(row['total_tokens']):,}")
    else:
        lines.append("- none: 0")
    return "\n".join(lines)


def send_discord(webhook_url: str, content: str) -> None:
    payload = (f'{{"content":{_json_string(content)}}}').encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "usage-notify/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        detail = body.strip() or error.reason
        raise DiscordSendError(f"Discord webhook returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise DiscordSendError(f"Discord webhook request failed: {error.reason}") from error


def maybe_send_discord(webhook_env: str, content: str) -> bool:
    webhook_url = os.environ.get(webhook_env)
    if not webhook_url:
        return False
    send_discord(webhook_url, content)
    return True


def _format_rows(rows: list[object], label_key: str) -> list[str]:
    if not rows:
        return ["- none: 0"]
    return [f"- {row[label_key]}: {int(row['total_tokens']):,}" for row in rows]


def _json_string(value: str) -> str:
    import json

    return json.dumps(value)
