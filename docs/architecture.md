# Codex Usage Notify Architecture

## 目的

Codex の各ターンで発生したトークン使用量をできるだけ早く外部サービスへ渡し、外部サービス側で集計して定刻に Discord へ通知する。

この設計では、ノート PC 側は通知時刻や集計ロジックを持たない。PC 側は usage event の収集、ローカル永続化、非同期送信、再送だけを担当する。

## 設計原則

- 取りこぼしを減らすため、usage event は外部送信より先にローカルへ永続化する。
- セッション終了処理だけに依存せず、各ターン終了時を主な記録タイミングにする。
- PC 側の処理は短時間で終わるようにし、Codex 利用体験を阻害しない。
- 外部 API は冪等にし、同じ event が複数回届いても二重計上しない。
- 集計と Discord 通知は外部サービス側で行い、PC の起動状態に依存しない。
- usage 取得元の変更に備え、収集処理は他の処理から分離する。

## 全体構成

```text
Codex turn completed
  -> Local usage collector
  -> Local persistent queue
  -> Async uploader
  -> Usage ingestion API
  -> Usage event store
  -> Aggregator
  -> Scheduled notifier
  -> Discord webhook
```

## コンポーネント

### 1. Local Usage Collector

Codex の各ターン終了時に usage 情報を取得し、usage event を生成する。

責務:

- `input_tokens`、`output_tokens`、`total_tokens` を取得する。
- `client_id` を付与し、どのクライアントから発生した usage か識別できるようにする。
- `session_id`、`turn_id`、`event_id` を付与する。
- モデル名、発生時刻、スキーマバージョンを付与する。
- usage 取得に失敗した場合は、送信処理へ進めずローカルログにエラーを残す。

注意点:

- 会話ログ全体を再読込して再トークナイズする方式は避ける。
- Codex や周辺ログが出す usage 情報を差分として拾う。
- usage 取得元は将来変わりやすいため、collector の中に閉じ込める。

### 2. Local Persistent Queue

usage event をローカルに永続化し、未送信分を再送できるようにする。

推奨実装:

- 初期実装は SQLite を推奨する。
- 最小構成なら JSONL でも可能だが、ACK 管理、再送回数、ロック、重複管理を考えると SQLite の方が堅い。

SQLite テーブル例:

```sql
CREATE TABLE usage_events (
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
```

`status` は以下を想定する。

- `pending`: 未送信
- `sending`: 送信中
- `sent`: 送信済み
- `failed`: 再送待ちだが直近送信に失敗
- `dead`: 最大再送回数を超過、または恒久的に送信不能

### 3. Async Uploader

ローカルキューの未送信 event を外部 API へ送信する。

起動タイミング:

- 各ターン終了時
- Codex 起動時
- セッション終了時
- PC 起動時、スリープ復帰時
- 必要に応じて Codex 利用中の定期チェック

送信ルール:

- 送信前に event は必ずローカルへ保存済みであること。
- API から成功 ACK を受けたら `sent` に更新する。
- ネットワークエラーや 5xx は指数バックオフで再送する。
- 4xx のうち認証エラーや schema 不正は `dead` または要確認状態にする。
- 複数 event をまとめて送れる batch API を基本にする。

バックオフ例:

```text
1m -> 5m -> 15m -> 1h -> 6h -> 24h
```

### 4. Usage Ingestion API

PC から usage event を受け取り、イベントストアへ保存する。

エンドポイント例:

```http
POST /v1/usage-events
Authorization: Bearer <client_token>
Content-Type: application/json
Idempotency-Key: <batch_id>
```

リクエスト例:

```json
{
  "events": [
    {
      "event_id": "01HV2M2J8W77QPBJ6QK4H8DR0A",
      "client_id": "client-a",
      "session_id": "abc",
      "turn_id": "0012",
      "timestamp": "2026-05-06T10:15:00+09:00",
      "input_tokens": 1234,
      "cached_input_tokens": 400,
      "non_cached_input_tokens": 834,
      "output_tokens": 567,
      "reasoning_output_tokens": 120,
      "total_tokens": 1801,
      "model": "gpt-5.3-codex",
      "source": "codex",
      "schema_version": 1
    }
  ]
}
```

レスポンス例:

```json
{
  "accepted": [
    "01HV2M2J8W77QPBJ6QK4H8DR0A"
  ],
  "duplicates": [],
  "rejected": []
}
```

API 側の要件:

- `event_id` に unique 制約を持つ。
- 可能なら `client_id + session_id + turn_id` にも unique 制約を持つ。
- duplicate は成功扱いで返し、PC 側が `sent` にできるようにする。
- schema_version を見て将来の形式変更に備える。
- 認証された client ごとに保存領域を分ける。
- payload の `client_id` と認証トークンから解決した `client_id` が一致することを検証する。運用上は、サーバー側で認証情報から `client_id` を決定し、payload 側の値は監査用または不一致検出用として扱う。

### 5. Usage Event Store

受信した usage event を保存する。

クライアント管理テーブル:

```sql
CREATE TABLE clients (
  client_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
```

推奨テーブル:

```sql
CREATE TABLE usage_events (
  id BIGSERIAL PRIMARY KEY,
  client_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  input_tokens INTEGER NOT NULL,
  cached_input_tokens INTEGER,
  non_cached_input_tokens INTEGER,
  output_tokens INTEGER NOT NULL,
  reasoning_output_tokens INTEGER,
  total_tokens INTEGER NOT NULL,
  model TEXT,
  source TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  raw_payload JSONB NOT NULL,
  UNIQUE (client_id, event_id),
  UNIQUE (client_id, session_id, turn_id)
);
```

インデックス:

```sql
CREATE INDEX idx_usage_events_client_occurred_at
  ON usage_events (client_id, occurred_at);

CREATE INDEX idx_usage_events_client_model_occurred_at
  ON usage_events (client_id, model, occurred_at);
```

`clients.display_name` は Discord 通知や管理画面で表示する名前として使う。usage event には表示名ではなく安定した `client_id` を保存する。

### 6. Aggregator

保存済み usage event を日次、週次、月次などで集計する。

基本集計軸:

- 日別
- 週別
- 月別
- クライアント別
- モデル別
- セッション別
- クライアント x モデル別

集計方針:

- 初期実装では通知時に都度集計でよい。
- イベント数が増えたら materialized view または summary table を導入する。
- 遅延到着した event を扱えるよう、集計は `received_at` ではなく `occurred_at` 基準にする。
- 全体合計に加えて、`client_id` ごとの小計を必ず出せるようにする。

遅延到着の扱い:

- Discord 通知は指定期間の現時点集計を送る。
- 通知後に過去期間の event が届いた場合、次回通知で「前回以降に到着した過去分」として補足するか、ダッシュボード上でのみ補正する。
- どちらにするかは運用ポリシーとして固定する。

### 7. Scheduled Notifier

外部サービス側で定刻に集計結果を Discord Webhook へ送る。

責務:

- 指定タイムゾーンで通知時刻を管理する。
- usage event を集計する。
- Discord Webhook へ送信する。
- 送信失敗時に再試行する。
- 通知履歴を保存し、二重通知を避ける。

通知履歴テーブル例:

```sql
CREATE TABLE notification_runs (
  id BIGSERIAL PRIMARY KEY,
  report_scope TEXT NOT NULL,
  scope_client_id TEXT,
  period_type TEXT NOT NULL,
  period_start TIMESTAMPTZ NOT NULL,
  period_end TIMESTAMPTZ NOT NULL,
  scheduled_for TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL,
  discord_message_id TEXT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (report_scope, scope_client_id, period_type, period_start, period_end, scheduled_for)
);
```

`report_scope` は以下を想定する。

- `all_clients`: 全クライアント合算レポート。`scope_client_id` は `NULL`。
- `single_client`: 特定クライアント単体のレポート。`scope_client_id` に対象 `client_id` を入れる。

Discord 通知例:

```text
Codex usage report

Period: 2026-05-06 00:00-23:59 Asia/Tokyo
Total: 18,420 tokens
Input: 12,300
Output: 6,120
Sessions: 4

By client:
- client-a: 12,000
- client-b: 6,420

By model:
- gpt-5.3-codex: 18,420

By client and model:
- client-a / gpt-5.3-codex: 12,000
- client-b / gpt-5.3-codex: 6,420
```

## データモデル

### UsageEvent

```json
{
  "event_id": "string",
  "client_id": "string",
  "session_id": "string",
  "turn_id": "string",
  "timestamp": "ISO-8601 string",
  "input_tokens": 0,
  "output_tokens": 0,
  "total_tokens": 0,
  "model": "string",
  "source": "codex",
  "schema_version": 1
}
```

制約:

- `event_id` は同じ turn に対して再送しても変えない。
- `client_id + session_id + turn_id` は同じ turn を一意に表す。
- `client_id` は外部サービスで登録済みのクライアントIDと一致させる。
- `total_tokens` は原則 `input_tokens + output_tokens` と一致させる。
- `timestamp` は usage が発生した時刻であり、送信時刻ではない。

## PC 側の処理フロー

### ターン終了時

```text
1. Codex の usage 情報を取得する
2. client_id / event_id / session_id / turn_id を決定する
3. usage event をローカル DB に pending として保存する
4. uploader を非同期で起動する
5. uploader が pending event を batch 送信する
6. API で accepted または duplicate なら sent に更新する
7. 失敗なら retry_count / next_retry_at / last_error を更新する
```

### 起動時・復帰時

```text
1. ローカル DB の pending / failed event を読む
2. next_retry_at が現在時刻以前のものを送信対象にする
3. batch 送信する
4. 結果に応じて sent / failed / dead を更新する
```

### セッション終了時

```text
1. 短いタイムアウト付きで flush を試す
2. 終了に失敗しても、ローカル DB に保存済みなので次回再送に任せる
```

## 外部サービス側の処理フロー

### 受信

```text
1. Authorization を検証する
2. payload schema を検証する
3. 認証情報から server-side client_id を解決する
4. payload の client_id と server-side client_id の一致を検証する
5. client_id + event_id で既存 event を確認する
6. 未登録なら保存する
7. 既存なら duplicate として成功扱いで返す
```

### 定刻通知

```text
1. scheduler が通知設定を読む
2. 対象期間を timezone に基づいて決定する
3. occurred_at 基準で usage event を全体、client 別、model 別、client x model 別に集計する
4. notification_runs に実行予定を記録する
5. Discord Webhook へ送信する
6. 成功なら sent、失敗なら retryable 状態にする
```

## 認証・セキュリティ

PC から外部 API への認証:

- client ごとに発行した bearer token を使う。
- token はローカル設定ファイルまたは OS の secret store に保存する。
- token はログへ出さない。
- token から解決される `client_id` を正とし、payload 内の `client_id` はなりすまし防止のため検証する。

Discord Webhook:

- 外部サービス側だけが保持する。
- PC 側には Discord Webhook URL を置かない。
- Webhook URL はログ、エラー通知、管理画面に露出させない。

API 防御:

- payload サイズ制限を設ける。
- client ごとに rate limit を設ける。
- schema validation を行う。
- 失敗ログには secret を含めない。

## 設定

PC 側設定例:

```toml
api_base_url = "https://usage.example.com"
client_id = "client-a"
client_token_env = "USAGE_NOTIFY_TOKEN"
db_path = "~/.local/share/usage-notify/events.db"
batch_size = 50
request_timeout_seconds = 5
max_retry_count = 20
```

外部サービス側設定例:

```toml
timezone = "Asia/Tokyo"
daily_report_time = "04:00"
weekly_report_day = "Monday"
weekly_report_time = "09:00"
discord_webhook_url_env = "DISCORD_WEBHOOK_URL"
```

## 障害時の挙動

PC がオフライン:

- ローカル DB に pending として残す。
- 次回起動時、復帰時、次ターン終了時に再送する。

外部 API が停止:

- uploader は 5xx または接続失敗を retryable として扱う。
- 指数バックオフで再送する。

同じ event を複数回送信:

- 外部 API が unique 制約で duplicate として扱う。
- PC 側は duplicate を成功扱いにして `sent` にする。

Discord 送信失敗:

- notification_runs に失敗を記録する。
- 外部サービス側で再送する。
- usage event の保存とは独立して扱う。

usage 取得失敗:

- event を捏造しない。
- collector error としてローカルログへ記録する。
- 後から復元できるログが存在する場合のみ、別プロセスで補完する。

## 実装フェーズ

### Phase 1: 最小実用版

- PC 側で usage event を SQLite に保存する。
- 未送信 event を API に batch 送信する。
- API 側で event を冪等保存する。
- 日次集計を全体、client 別、model 別で Discord へ送る。

### Phase 2: 信頼性強化

- 起動時、復帰時、終了時 flush を追加する。
- retry_count、next_retry_at、dead 状態を実装する。
- notification_runs による通知履歴と再送を実装する。
- 遅延到着 event の補足ポリシーを実装する。

### Phase 3: 可視化・運用

- 管理画面または CLI でローカルキュー状態を確認できるようにする。
- 日次、週次、月次、client 別、モデル別の表示を追加する。
- token usage の異常増加を検知する。

## 最初に決めるべき未確定事項

- Codex の usage 情報をどこから安定して取得するか。
- PC 側をどの形で組み込むか。例: hook、wrapper、ログ監視、CLI 補助プロセス。
- 外部サービスの実行基盤。例: Cloudflare Workers、Vercel Cron、Fly.io、常駐 VPS。
- 遅延到着した過去分を Discord 通知で補足するか。
- `client_id` の命名規則。例: `client-a`、`client-b`、`team-device-1`。
- 1 台の PC 上で複数用途を分けたい場合、`client_id` を端末単位にするか、用途単位にするか。

## 推奨する追加フィールド

複数ユーザーやプロジェクト別集計の可能性があるなら、以下を追加する。

```json
{
  "user_id": "optional-user-id",
  "project_path_hash": "optional-hash"
}
```

`client_id` は必須フィールドとして扱うため、追加フィールドではなく UsageEvent 本体に含める。

`project_path_hash` はプロジェクト別集計をしたい場合だけ使う。生のパスはプライバシー上の理由で送らない方がよい。

## 採用判断

この設計では、PC 側の責務を「早く、確実に、同じ event を再送できる形で外部へ渡す」ことに限定する。定刻通知、集計、Discord Webhook 管理は外部サービス側へ寄せる。

これにより、ノート PC のスリープ、電源断、tmux や screen の detach、ウィンドウクローズに影響されにくく、使用量の取りこぼしを抑えやすい構成になる。
