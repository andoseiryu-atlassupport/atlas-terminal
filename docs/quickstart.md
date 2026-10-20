# 導入と MCP

Python 3.12 以上と uv を用意し、このチェックアウトで `uv sync --locked --all-extras --dev` を実行します。初回の依存ダウンロード後は `uv run --offline atlas demo --json` で動きます。PyPI への配布は行っていません。

## 最短のデータ取得

```sh
uv run --offline atlas demo --json
uv run --offline atlas company resolve ATLASDEMO --provider fixture --json
uv run --offline atlas financials get ATLASDEMO --metrics revenue,net_income --count 3 --provider fixture --json
uv run --offline atlas filings list ATLASDEMO --form 10-K --limit 3 --provider fixture --json
```

実 SEC を使う場合は `SEC_USER_AGENT` に利用者の識別情報と連絡先を private な環境設定から渡し、identifier を実企業、provider を `sec` に変更します。API キー・ウォレット・モデルキーは不要です。`.env.example` は変数名一覧で、自動ロードはしません。contact をチャットやログに記録しないでください。

## MCP ホストへの接続

[設定例](../examples/mcp.fixture.json)の `/absolute/path/to/atlas-terminal` を実パスに置き換えます。ホスト側に uv の PATH を設定するか、command を uv の絶対パスにします。

```json
{
  "mcpServers": {
    "atlas-terminal": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/atlas-terminal",
        "--offline",
        "atlas",
        "mcp",
        "serve",
        "--provider",
        "fixture"
      ]
    }
  }
}
```

`fixture` 起動後、公式クライアントの initialize / tools/list / tools/call に対応します。`atlas_get_financials` へ `identifier=ATLASDEMO`、`metrics=[revenue, net_income]`、`count=3` を渡すと CLI/SDK と同じ structured content を返します。

`sec` ではホストの private environment 設定から `SEC_USER_AGENT` を渡します。`hosted` は起動時に `ATLAS_HOSTED_ORIGIN`、private な `ATLAS_STATE_DIR`、有料購入用 `ATLAS_POLICY_FILE` を渡します。鍵の実値を設定例・モデル入力へ含めません。MCP ツールから provider、URL、policy、wallet を変更する機能はありません。

| Tool                  | 動作                                                   |
| --------------------- | ------------------------------------------------------ |
| atlas_resolve_company | 企業候補を照合                                         |
| atlas_get_financials  | fixture / SEC のみ取得。hosted は QUOTE_REQUIRED       |
| atlas_list_filings    | 提出メタデータと出典参照                               |
| atlas_quote           | 成果物と価格を固定。支払わない                         |
| atlas_purchase        | 委任済みの方針・予算内で支出する                       |
| atlas_purchase_status | private な回復 capability を使って状態照合。支払わない |

プロセス起動時の失敗は stderr、それ以降の stdout は公式 SDK のプロトコル専用です。Windows でも uv コマンドを使えますが、Windows 上での実行試験は未実施です。
