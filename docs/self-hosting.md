# 自己運用

参照 API は単一プロセス、単一の private SQLite volume 用です。CLI の bind は loopback 限定です。`WEB_CONCURRENCY` / `UVICORN_WORKERS` が 1 以外なら起動しません。複数ホストで独立した state volume を作って同じ signer を使うと、予算を共有できません。

## 無料モード

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
```

実 SEC では `--provider sec` と private な `SEC_USER_AGENT` を使います。`ATLAS_STATE_DIR` をサーバー専用の永続ディレクトリに設定します。既定はユーザーの `.local/state/atlas-terminal`。新規ディレクトリは mode 0700、DB は 0600 を使い、共有権限の DB を拒否します。SQLite WAL を含めて保護・バックアップしてください。

## HTTP

| Method | Path                                         | 動作                                     |
| ------ | -------------------------------------------- | ---------------------------------------- |
| GET    | /healthz                                     | 秘密設定を含まない稼働状態               |
| GET    | /v1/catalog                                  | 対応指標・期間・価格モード               |
| GET    | /v1/research/catalog                         | 調査データセット・引数・単位・上限       |
| POST   | /v1/research                                 | 見積もり・支払いなしの調査取得           |
| GET    | /v1/companies/resolve?query=ATLASDEMO        | 無料照合                                 |
| GET    | /v1/filings?cik=9999999999&form=10-K&limit=3 | 無料メタデータ                           |
| POST   | /v1/quotes                                   | データ準備・不変成果物保存・無料見積もり |
| GET    | /v1/quotes/{quote_id}/result                 | off は回復認証、正価格は決済後に納品     |
| GET    | /v1/quotes/{quote_id}/status                 | 回復認証付き状態照合                     |

`POST /v1/quotes` の本文例:

```json
{
  "identifier": "ATLASDEMO",
  "metrics": ["revenue", "net_income"],
  "period": "annual",
  "count": 3,
  "allow_partial": false
}
```

レスポンスの `Atlas-Recovery-Token` は一度だけヘッダーに返します。JSON、MCP の結果、ログへ含めません。状態と購入済み結果の取得には `Authorization: Bearer` で使います。quote ID を知るだけでは状態や購入済み結果を取得できません。token は未払い結果へのアクセスや新規購入の権限を与えません。

`/openapi.json` は FastAPI の生成仕様です。ドメイン契約は [JSON Schema](../schemas/atlas.schema.json)。x402 wire payload は公式 SDK モデルを使います。

## 上限と保持

quote は 5 分、準備はサービス全体で 30 件/分、保存中 quote は 128 件、snapshot 合計は 64 MiB、成果物一つは 2 MiB、POST 本文は 16 KiB に制限します。決済済み snapshot は決済確認から最低 24 時間保持します。未着手 quote の期限切れと購入後の保持期限は別です。不明状態は自動削除しません。

SEC は一つの SQLite rate/cache を共用し、既定 2 req/s、burst 1、同時 2、HTTP timeout と bytes/時間上限、有限 retry、etag/last-modified を使います。ticker は 24 時間、submissions は 15 分、companyfacts は 1 時間の cache 目安です。期限切れの更新に失敗しても、古い値を最新として返しません。

外部公開用の認証、TLS termination、アクセス制御、運用監視、商用価格、複数 replica、SLA は提供しません。API は単一プロセス・loopback 接続向けです。
