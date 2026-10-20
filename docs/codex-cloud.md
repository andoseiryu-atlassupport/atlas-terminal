# Codex Cloud のセットアップ

このリポジトリは Python 3.12 以上、uv、Node.js 24、npm、Git を使います。通常の fixture 試験に API キー・wallet・外部 DB は不要です。

## セットアップ

依存導入と検証のコマンドは次のとおりです。

```sh
uv sync --locked --all-extras --dev
npm ci
npm run check
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy
uv run --offline pytest
uv run --offline atlas demo --json
uv run --offline python examples/free_research.py
uv run --offline python -m build --no-isolation
```

初回の依存取得には PyPI / package download の許可が必要です。実行後の fixture デモ・テストはオフラインです。uv 0.12.23、Python 3.12.14、Node.js 24.19.0 で検証しています。

実 SEC では SEC の 2 ドメインと private な SEC_USER_AGENT、real testnet では選んだ Facilitator / HTTPS service の許可と専用 wallet/policy が別途必要です。既存 network 制限を回避せず、管理 UI の設定フローで扱います。秘密値を setup log やチャットへ出しません。

無料調査の実取得では、必要なソースの `api.bls.gov`、`home.treasury.gov`、`api.fiscaldata.treasury.gov`、`www.federalreserve.gov` への HTTPS 許可が別途必要です。fixture 検証はこれらへ通信しません。`scripts/research_smoke.py` は `--allow-network` を明示した場合だけ live 検査を行います。通信制限による未検証を fixture PASS で置き換えません。

## 別タスクでの再現

新しい Cloud タスクで README / AGENTS / 実装状況を読み、上記 locked install、demo、MCP の subprocess 試験、模擬購入を確認してください。この作業で実施したコマンド成功は、その新規タスクや GitHub Actions の成功とは区別します。

公式資料: [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)。
