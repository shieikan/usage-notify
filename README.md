# usage-notify

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-4c1)](https://docs.astral.sh/uv/)
[![Cloudflare Workers](https://img.shields.io/badge/runtime-Cloudflare%20Workers-f38020)](https://workers.cloudflare.com/)

Codex の利用トークンをローカルで取りこぼしにくく収集し、Cloudflare Workers + D1 に送信して、日次レポートを Discord に通知するための小さな運用ツールです。

```text
Codex Stop hook
  -> local SQLite queue
  -> Cloudflare Worker
  -> D1
  -> Cron Trigger
  -> Discord Webhook
```

| Area | Choice |
| --- | --- |
| Local runtime | Python CLI, uv, python-dotenv |
| Local durability | SQLite queue |
| Cloud backend | Cloudflare Workers, D1, Cron Triggers |
| Notification | Discord Webhook |
| Client grouping | `client_id` |

## Contents

- [Features](#features)
- [Report Example](#report-example)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Codex Hook](#codex-hook)
- [Multi-Device Setup](#multi-device-setup)
- [Pricing](#pricing)
- [Development](#development)
- [Documentation](#documentation)
- [Security Notes](#security-notes)

## Features

- Codex の各ターン終了時に usage を収集
- ローカル SQLite に永続化し、送信失敗時は後で再送
- 複数デバイスを `client_id` で集計
- Cloudflare Workers + D1 + Cron Triggers で無料枠に収まりやすい構成
- Discord へ日次レポートを送信
- ユーザー別、モデル別、ユーザー x モデル別の集計
- API Usage の概算料金と週次シェアをレポート
- `uv` と `.env` ベースのローカル運用

## Report Example

```text
Codex usage report

Period: 2026-05-07 Asia/Tokyo
Total: 11,913,455 tokens
Input: 11,874,513
Cached input: 7,552
Output: 38,942
Reasoning output: 87
Estimated API usage: $60.5408
Sessions: 6
Week: 2026-05-04 - 2026-05-10

By client (daily tokens / estimated cost / weekly share):
- client-a: 11,913,455 / $60.5408 / 100.0%

By model:
- gpt-5.5: 11,913,455
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- Cloudflare account
- Discord Webhook URL
- Codex with hook support enabled

## Quick Start

Clone and install dependencies:

```sh
git clone <repo-url>
cd usage-notify
uv sync
npm install
```

Create local settings:

```sh
cp .env.example .env
```

Minimum client settings:

```dotenv
USAGE_NOTIFY_CLIENT_ID=client-a
USAGE_NOTIFY_TOKEN=replace-with-shared-random-token
USAGE_NOTIFY_API_BASE_URL=https://usage-notify.<your-subdomain>.workers.dev
USAGE_NOTIFY_LOCAL_DB=/home/<user>/.local/share/usage-notify/events.db
USAGE_NOTIFY_CODEX_LOG=/home/<user>/.codex/log/codex-tui.log
ADMIN_TOKEN=replace-with-long-random-admin-token
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Set up Cloudflare:

```sh
npx wrangler login
npx wrangler d1 create usage_notify
```

The checked-in `wrangler.toml` uses a placeholder D1 database ID:

```toml
database_id = "replace-with-d1-database-id"
```

Copy the `database_id` printed by `npx wrangler d1 create usage_notify` and replace the placeholder before running remote migrations or deploying. The generated output looks like this:

```toml
[[d1_databases]]
binding = "DB"
database_name = "usage_notify"
database_id = "<generated-d1-database-id>"
```

After updating `wrangler.toml`, run:

```sh
npm run cf:migrate:remote
npx wrangler secret put CLIENT_TOKENS
npx wrangler secret put ADMIN_TOKEN
npx wrangler secret put DISCORD_WEBHOOK_URL
npm run cf:deploy
```

For personal or small-team use, `CLIENT_TOKENS` can be a single shared token:

```text
*=replace-with-shared-random-token
```

Install the Codex user hook:

```sh
scripts/install-codex-hook
```

Restart Codex sessions after installing or changing hooks.

## Usage

Collect and upload now:

```sh
uv run usage-notify flush --retry-now
```

Check local queue status:

```sh
uv run usage-notify status
```

Generate the remote D1 report:

```sh
uv run usage-notify cloudflare-report --date 2026-05-07
```

Send the remote D1 report to Discord:

```sh
uv run usage-notify cloudflare-report --date 2026-05-07 --send-discord
```

Successful Discord sends end with:

```text
discord report sent
```

Test only the local Discord webhook path:

```sh
uv run usage-notify test-discord
```

## Codex Hook

`scripts/install-codex-hook` configures a user-scope Codex `Stop` hook:

- creates `~/.local/bin/usage-notify-codex-hook-stop`
- enables `codex_hooks = true` in `~/.codex/config.toml`
- writes `~/.codex/hooks.json`

The hook runs at the end of each Codex turn, waits briefly for the matching usage line in `~/.codex/log/codex-tui.log`, stores it in the local SQLite queue, and uploads it to the configured Worker.

Uninstall the hook from the current device:

```sh
scripts/uninstall-codex-hook
```

The uninstall script removes only this project’s hook command from `~/.codex/hooks.json` and writes a backup before editing. It leaves `codex_hooks = true` unchanged because other hooks may use it.

## Multi-Device Setup

Use Git to share the project files. Do not commit or share `.env`, `.venv/`, `node_modules/`, or `*.db`.

On another device:

```sh
git clone <repo-url>
cd usage-notify
scripts/setup-device
```

Then edit `.env` for that device:

```dotenv
USAGE_NOTIFY_CLIENT_ID=client-b
USAGE_NOTIFY_TOKEN=replace-with-shared-random-token
USAGE_NOTIFY_API_BASE_URL=https://usage-notify.<your-subdomain>.workers.dev
```

With `CLIENT_TOKENS=*=replace-with-shared-random-token`, every device uses the same `USAGE_NOTIFY_TOKEN`; the report separates devices by `USAGE_NOTIFY_CLIENT_ID`.

If you want per-device tokens instead:

```text
client-a=token1,client-b=token2
```

## Pricing

Estimated cost is calculated from `input_tokens` and `output_tokens` with model prices in `wrangler.toml`:

```toml
[vars]
MODEL_PRICES_JSON = '{"gpt-5.5":{"input":5,"output":30},"gpt-5.4-mini":{"input":0.75,"output":4.5}}'
```

The Worker also includes default prices for common OpenAI models such as `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.2`, `gpt-5.1`, `gpt-5`, `gpt-4.1`, `gpt-4o-mini`, and `o4-mini`.

`cached_input_tokens`, `non_cached_input_tokens`, and `reasoning_output_tokens` are collected when they are present in the Codex usage log. Cached input discounts are only applied to the estimate when a model price includes `cached_input`; otherwise input is charged at the normal input price. The estimate does not currently account for Batch API discounts, long-context surcharges, or regional processing surcharges. Treat it as an operational estimate, not an invoice replacement.

## Development

Run Python tests:

```sh
uv run python -m unittest discover -s tests -v
```

Run Cloudflare Worker tests:

```sh
npm run cf:test
```

Run a local Worker:

```sh
npm run cf:migrate:local
npm run cf:dev
```

Deploy:

```sh
npm run cf:deploy
```

## Documentation

- [Cloudflare deployment](docs/cloudflare.md)
- [Architecture](docs/architecture.md)

## Security Notes

- Keep `.env`, Discord Webhook URLs, `ADMIN_TOKEN`, and `CLIENT_TOKENS` out of Git.
- For personal use, a shared token is usually enough.
- For stricter separation, use one token per `client_id`.
- Local events are queued on disk, so protect the local database path if the device is shared.
