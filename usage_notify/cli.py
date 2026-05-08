from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path

from .cloudflare_api import CloudflareApiError, request_daily_report
from .collector import collect_from_log
from .codex_hook import run_stop_hook
from .env import DEFAULT_ENV_FILE, env_default, load_app_env
from .report import DiscordSendError, daily_report, maybe_send_discord
from .selftest import run_self_test
from .server import run_server
from .store import connect, insert_events, mark_failed, mark_sent, pending_events, status_counts
from .uploader import UploadError, upload_events


DEFAULT_CODEX_LOG = "~/.codex/log/codex-tui.log"
DEFAULT_DB = "~/.local/share/usage-notify/events.db"


def main(argv: list[str] | None = None) -> int:
    env_file = _preparse_env_file(argv)
    load_app_env(env_file)

    parser = argparse.ArgumentParser(prog="usage-notify")
    parser.add_argument("--env-file", default=env_file)
    parser.add_argument("--db", default=env_default("USAGE_NOTIFY_LOCAL_DB", DEFAULT_DB))
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--client-id", default=os.environ.get("USAGE_NOTIFY_CLIENT_ID"))
    collect_parser.add_argument("--codex-log", default=env_default("USAGE_NOTIFY_CODEX_LOG", DEFAULT_CODEX_LOG))

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("--api-base-url", default=os.environ.get("USAGE_NOTIFY_API_BASE_URL"))
    upload_parser.add_argument("--token-env", default="USAGE_NOTIFY_TOKEN")
    upload_parser.add_argument("--batch-size", type=int, default=50)
    upload_parser.add_argument("--timeout-seconds", type=float, default=5)
    upload_parser.add_argument("--retry-now", action="store_true")

    flush_parser = subparsers.add_parser("flush")
    flush_parser.add_argument("--client-id", default=os.environ.get("USAGE_NOTIFY_CLIENT_ID"))
    flush_parser.add_argument("--codex-log", default=env_default("USAGE_NOTIFY_CODEX_LOG", DEFAULT_CODEX_LOG))
    flush_parser.add_argument("--api-base-url", default=os.environ.get("USAGE_NOTIFY_API_BASE_URL"))
    flush_parser.add_argument("--token-env", default="USAGE_NOTIFY_TOKEN")
    flush_parser.add_argument("--batch-size", type=int, default=50)
    flush_parser.add_argument("--timeout-seconds", type=float, default=5)
    flush_parser.add_argument("--retry-now", action="store_true")

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default=env_default("USAGE_NOTIFY_HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(env_default("USAGE_NOTIFY_PORT", "8080")))
    serve_parser.add_argument("--server-db", default=env_default("USAGE_NOTIFY_SERVER_DB", "./server-events.db"))
    serve_parser.add_argument("--tokens-env", default="USAGE_NOTIFY_CLIENT_TOKENS")

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--server-db", default=env_default("USAGE_NOTIFY_SERVER_DB", "./server-events.db"))
    report_parser.add_argument("--date", dest="report_date")
    report_parser.add_argument("--timezone", default=env_default("USAGE_NOTIFY_TIMEZONE", "Asia/Tokyo"))
    report_parser.add_argument("--discord-webhook-env", default="DISCORD_WEBHOOK_URL")
    report_parser.add_argument("--send-discord", action="store_true")

    cloudflare_report_parser = subparsers.add_parser("cloudflare-report")
    cloudflare_report_parser.add_argument("--api-base-url", default=os.environ.get("USAGE_NOTIFY_API_BASE_URL"))
    cloudflare_report_parser.add_argument("--admin-token-env", default="ADMIN_TOKEN")
    cloudflare_report_parser.add_argument("--date", dest="report_date")
    cloudflare_report_parser.add_argument("--send-discord", action="store_true")
    cloudflare_report_parser.add_argument("--timeout-seconds", type=float, default=10)

    subparsers.add_parser("test-discord")
    subparsers.add_parser("self-test")
    subparsers.add_parser("status")
    hook_stop_parser = subparsers.add_parser("hook-stop")
    hook_stop_parser.add_argument("--client-id", default=os.environ.get("USAGE_NOTIFY_CLIENT_ID"))
    hook_stop_parser.add_argument("--codex-log", default=env_default("USAGE_NOTIFY_CODEX_LOG", DEFAULT_CODEX_LOG))
    hook_stop_parser.add_argument("--api-base-url", default=os.environ.get("USAGE_NOTIFY_API_BASE_URL"))
    hook_stop_parser.add_argument("--token-env", default="USAGE_NOTIFY_TOKEN")
    hook_stop_parser.add_argument("--wait-seconds", type=float, default=float(env_default("USAGE_NOTIFY_HOOK_WAIT_SECONDS", "12")))
    hook_stop_parser.add_argument("--batch-size", type=int, default=50)
    hook_stop_parser.add_argument("--timeout-seconds", type=float, default=5)

    args = parser.parse_args(argv)

    if args.command == "collect":
        if not args.client_id:
            print("missing client id: set USAGE_NOTIFY_CLIENT_ID or pass --client-id")
            return 1
        db_path = _expand(args.db)
        _ensure_parent(db_path)
        return _collect(db_path, args.client_id, _expand(args.codex_log))
    if args.command == "upload":
        if not args.api_base_url:
            print("missing API base URL: set USAGE_NOTIFY_API_BASE_URL or pass --api-base-url")
            return 1
        db_path = _expand(args.db)
        _ensure_parent(db_path)
        return _upload(
            db_path,
            args.api_base_url,
            args.token_env,
            args.batch_size,
            args.timeout_seconds,
            args.retry_now,
        )
    if args.command == "flush":
        if not args.client_id:
            print("missing client id: set USAGE_NOTIFY_CLIENT_ID or pass --client-id")
            return 1
        if not args.api_base_url:
            print("missing API base URL: set USAGE_NOTIFY_API_BASE_URL or pass --api-base-url")
            return 1
        db_path = _expand(args.db)
        _ensure_parent(db_path)
        collect_code = _collect(db_path, args.client_id, _expand(args.codex_log))
        if collect_code != 0:
            return collect_code
        return _upload(
            db_path,
            args.api_base_url,
            args.token_env,
            args.batch_size,
            args.timeout_seconds,
            args.retry_now,
        )
    if args.command == "serve":
        token_map = _parse_token_map(args.tokens_env)
        if not token_map:
            print(f"missing or empty token map env: {args.tokens_env}")
            print("expected format: client_id=token,other-client=other-token")
            return 1
        _ensure_parent(_expand(args.server_db))
        run_server(args.host, args.port, _expand(args.server_db), token_map)
        return 0
    if args.command == "report":
        report_date = date.fromisoformat(args.report_date) if args.report_date else date.today()
        content = daily_report(_expand(args.server_db), report_date, args.timezone)
        print(content)
        if args.send_discord:
            try:
                sent = maybe_send_discord(args.discord_webhook_env, content)
                if not sent:
                    print(f"missing discord webhook env: {args.discord_webhook_env}")
                    return 1
            except DiscordSendError as error:
                print(str(error))
                return 1
        return 0
    if args.command == "cloudflare-report":
        return _cloudflare_report(args)
    if args.command == "test-discord":
        return _test_discord()
    if args.command == "self-test":
        try:
            print(run_self_test())
        except RuntimeError as error:
            print(f"self-test failed: {error}")
            return 1
        print("self-test: ok")
        return 0
    if args.command == "status":
        db_path = _expand(args.db)
        _ensure_parent(db_path)
        return _status(db_path)
    if args.command == "hook-stop":
        return _hook_stop(args)
    raise AssertionError(args.command)


def _collect(db_path: str, client_id: str, codex_log: str) -> int:
    if not os.path.exists(codex_log):
        print(f"codex log not found: {codex_log}")
        return 1
    events = collect_from_log(codex_log, client_id)
    with connect(db_path) as connection:
        inserted = insert_events(connection, events)
    print(f"collected={len(events)} inserted={inserted}")
    return 0


def _upload(
    db_path: str,
    api_base_url: str,
    token_env: str,
    batch_size: int,
    timeout_seconds: float,
    retry_now: bool,
) -> int:
    token = os.environ.get(token_env)
    if not token:
        print(f"missing token env: {token_env}")
        return 1

    with connect(db_path) as connection:
        events = pending_events(connection, batch_size, include_waiting=retry_now)
        if not events:
            print("no pending events")
            return 0
        event_ids = [str(event["event_id"]) for event in events]
        try:
            accepted, duplicates = upload_events(api_base_url, token, events, timeout_seconds)
        except UploadError as error:
            mark_failed(connection, event_ids, str(error))
            print(f"upload failed events={len(event_ids)} error={error}")
            return 1
        sent_ids = accepted + duplicates
        mark_sent(connection, sent_ids)
        failed_ids = [event_id for event_id in event_ids if event_id not in set(sent_ids)]
        mark_failed(connection, failed_ids, "API did not accept event")
        print(f"uploaded accepted={len(accepted)} duplicates={len(duplicates)} failed={len(failed_ids)}")
    return 0


def _status(db_path: str) -> int:
    with connect(db_path) as connection:
        counts = status_counts(connection)
    if not counts:
        print("no events")
        return 0
    for status, count in counts.items():
        print(f"{status}: {count}")
    return 0


def _test_discord() -> int:
    try:
        sent = maybe_send_discord("DISCORD_WEBHOOK_URL", "usage-notify Discord webhook test")
    except DiscordSendError as error:
        print(str(error))
        return 1
    if not sent:
        print("missing discord webhook env: DISCORD_WEBHOOK_URL")
        return 1
    print("discord test sent")
    return 0


def _cloudflare_report(args: argparse.Namespace) -> int:
    if not args.api_base_url:
        print("missing API base URL: set USAGE_NOTIFY_API_BASE_URL or pass --api-base-url")
        return 1
    admin_token = os.environ.get(args.admin_token_env)
    if not admin_token:
        print(f"missing admin token env: {args.admin_token_env}")
        return 1

    try:
        content = request_daily_report(
            args.api_base_url,
            admin_token,
            args.report_date,
            args.send_discord,
            args.timeout_seconds,
        )
    except CloudflareApiError as error:
        print(f"Cloudflare report failed: {error}")
        return 1

    print(content)
    if args.send_discord:
        print("discord report sent")
    return 0


def _hook_stop(args: argparse.Namespace) -> int:
    warning = None
    if not args.client_id:
        warning = "missing client id: set USAGE_NOTIFY_CLIENT_ID or pass --client-id"
    elif not args.api_base_url:
        warning = "missing API base URL: set USAGE_NOTIFY_API_BASE_URL or pass --api-base-url"
    token = os.environ.get(args.token_env)
    if not warning and not token:
        warning = f"missing token env: {args.token_env}"

    if warning:
        _codex_hook_response(warning)
        return 0

    db_path = _expand(args.db)
    _ensure_parent(db_path)
    ok, message = run_stop_hook(
        db_path=db_path,
        client_id=args.client_id,
        codex_log=_expand(args.codex_log),
        api_base_url=args.api_base_url,
        token=token or "",
        wait_seconds=args.wait_seconds,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout_seconds,
    )
    _codex_hook_response(None if ok else message)
    return 0


def _codex_hook_response(system_message: str | None) -> None:
    payload: dict[str, object] = {
        "continue": True,
        "suppressOutput": True,
    }
    if system_message:
        payload["systemMessage"] = system_message
    print(json.dumps(payload, separators=(",", ":")))


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _parse_token_map(env_name: str) -> dict[str, str]:
    raw = os.environ.get(env_name, "")
    token_map: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        client_id, separator, token = pair.partition("=")
        if not separator or not client_id or not token:
            continue
        token_map[client_id] = token
    local_client_id = os.environ.get("USAGE_NOTIFY_CLIENT_ID")
    local_token = os.environ.get("USAGE_NOTIFY_TOKEN")
    if local_client_id and local_token:
        token_map[local_client_id] = local_token
    return token_map


def _preparse_env_file(argv: list[str] | None) -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", default=os.environ.get("USAGE_NOTIFY_ENV_FILE", DEFAULT_ENV_FILE))
    parsed, _ = parser.parse_known_args(argv)
    return parsed.env_file
