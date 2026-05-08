# Cloudflare Deployment

Cloudflare Workers + D1 + Cron Triggers で、完全無料枠に収まりやすい構成にする。

現在の Cron Trigger は毎日 19:00 UTC、つまり Asia/Tokyo の翌日 04:00 に日次レポートを送信する。

## 構成

```text
PC side Python CLI
  -> POST /v1/usage-events
  -> Cloudflare Worker
  -> Cloudflare D1
  -> Cron Trigger
  -> Discord Webhook
```

PC側の `usage-notify` CLI はそのまま使う。`USAGE_NOTIFY_API_BASE_URL` を Workers のURLへ向ける。

## 初回セットアップ

依存関係を入れる。

```sh
npm install
```

Cloudflareへログインする。

```sh
npx wrangler login
```

D1 database を作成する。

```sh
npx wrangler d1 create usage_notify
```

出力された `database_id` を `wrangler.toml` の `database_id` に設定する。

マイグレーションを適用する。

```sh
npm run cf:migrate:remote
```

secret を登録する。

```sh
npx wrangler secret put CLIENT_TOKENS
npx wrangler secret put DISCORD_WEBHOOK_URL
npx wrangler secret put ADMIN_TOKEN
```

料金計算は `MODEL_PRICES_JSON` で設定する。値は 1M tokens あたりの USD。Worker には OpenAI 公式モデルページを元にした主要モデルのデフォルト単価も入っている。

```toml
[vars]
MODEL_PRICES_JSON = '{"gpt-5.5":{"input":5,"output":30},"gpt-5.4-mini":{"input":0.75,"output":4.5}}'
```

デフォルト価格表:

| Model | Input / 1M | Output / 1M |
| --- | ---: | ---: |
| `gpt-5.5` | $5.00 | $30.00 |
| `gpt-5.4-pro` | $30.00 | $180.00 |
| `gpt-5.4` | $2.50 | $15.00 |
| `gpt-5.4-mini` | $0.75 | $4.50 |
| `gpt-5.4-nano` | $0.20 | $1.25 |
| `gpt-5.3-codex`, `gpt-5.3-chat-latest` | $1.75 | $14.00 |
| `gpt-5.2`, `gpt-5.2-codex`, `gpt-5.2-chat-latest` | $1.75 | $14.00 |
| `gpt-5.1`, `gpt-5.1-codex`, `gpt-5.1-codex-max` | $1.25 | $10.00 |
| `gpt-5`, `gpt-5-codex` | $1.25 | $10.00 |
| `gpt-5-mini` | $0.25 | $2.00 |
| `gpt-5-nano` | $0.05 | $0.40 |
| `gpt-4.1` | $2.00 | $8.00 |
| `gpt-4.1-mini` | $0.40 | $1.60 |
| `gpt-4.1-nano` | $0.10 | $0.40 |
| `gpt-4o-mini` | $0.15 | $0.60 |
| `o4-mini` | $1.10 | $4.40 |

参照元: [OpenAI Models](https://developers.openai.com/api/docs/models), [OpenAI model catalog](https://developers.openai.com/api/docs/models/all), 各モデル詳細ページ。`cached_input_tokens` はCodexログに含まれる場合に保存する。料金計算ではモデル単価に `cached_input` がある場合だけcached input単価を使い、未設定なら通常input単価で概算する。Batch API、長文コンテキスト割増、regional processing の上乗せは現行の集計では分けて計算しない。モデル単価が変わった場合や別モデルを使う場合はここを更新して再デプロイする。

少人数利用では共有トークン方式を推奨する。`CLIENT_TOKENS` は以下の形式にする。

```text
*=shared-random-token
```

各デバイスでは同じ `USAGE_NOTIFY_TOKEN` を使い、`USAGE_NOTIFY_CLIENT_ID` だけ変える。

```dotenv
USAGE_NOTIFY_CLIENT_ID=client-a
USAGE_NOTIFY_TOKEN=shared-random-token
```

別デバイス:

```text
USAGE_NOTIFY_CLIENT_ID=client-b
USAGE_NOTIFY_TOKEN=shared-random-token
```

デバイスごとにトークンを分けたい場合は、以下のどちらかの形式も使える。

```text
client-a=token1,client-b=token2
```

または:

```json
{"client-a":"token1","client-b":"token2"}
```

デプロイする。

```sh
npm run cf:deploy
```

PC側 `.env` の `USAGE_NOTIFY_API_BASE_URL` を Workers URL に変更する。

```dotenv
USAGE_NOTIFY_CLIENT_ID=client-a
USAGE_NOTIFY_TOKEN=shared-random-token
USAGE_NOTIFY_API_BASE_URL=https://usage-notify.<your-subdomain>.workers.dev
```

送信する。

```sh
uv run usage-notify flush --retry-now
```

## 手動レポート送信

Cronを待たずに日次レポートを確認する場合:

```sh
curl -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-05-06","send_discord":true}' \
  https://usage-notify.<your-subdomain>.workers.dev/v1/reports/daily
```

## ローカル検証

```sh
npm run cf:test
npx wrangler d1 migrations apply usage_notify --local
npx wrangler dev
```
