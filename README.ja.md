# Atlas Terminal

AI エージェントが SEC 公開データの企業情報、年次財務実績、提出書類メタデータを取得する CLI・Python SDK・stdio MCP です。ローカル取得は無料で、ウォレットも LLM API キーも不要です。任意の参照 HTTP サービスでは、不変の財務 snapshot を明示的な見積もり・購入で取得できます。

[English](README.md) · [実装・検証状況](IMPLEMENTATION_STATUS.md) · [選択方法](docs/methodology.md)

## オフラインデモ

Python 3.12 以上と [uv](https://docs.astral.sh/uv/) を用意し、このチェックアウトで実行します。

```sh
uv sync --locked --all-extras --dev
uv run --offline atlas demo --json
```

架空の SEC 形式入力から正規化するデモです。通信、モデルキー、ウォレットを使いません。期間、USD 単位、タグ、accession、原 JSON の位置、原文ハッシュ、取得時刻を各値に付けます。`is_synthetic=true` と `.invalid` リンクで架空データだと明示します。

## SEC のローカル取得

`SEC_USER_AGENT` に利用者自身のアプリ名・実際の連絡先メールを、非公開のプロセス設定として指定します。Atlas は連絡先を自動入力しません。

```sh
uv run atlas company resolve AAPL --provider sec --json
uv run atlas financials get AAPL --metrics revenue,net_income --period annual --count 3 --provider sec --json
uv run atlas filings list AAPL --form 10-K --limit 3 --provider sec --json
```

既定の provider は SEC です。取得失敗時にホスト版・支払いへ切り替わりません。架空企業 `ATLASDEMO` では `--provider fixture` を指定します。標準出力は単一 JSON、エラーは非ゼロ終了です。終了コードは 2=入力、3=上流、4=方針・設定、5=決済、1=予期しない失敗です。

対応指標は `revenue`、`net_income`、`assets`、`liabilities`、`operating_cash_flow`。主年度の 10-K / 10-K/A に基づく年次・US-GAAP・USD に限定します。欠損・矛盾・年数不足は既定で `INCOMPLETE_COVERAGE`。`--allow-partial` を明示すると欠損を含む行列を返します。株価・ニュース・監視・売買・有料データ・内蔵 LLM 分析は対象外です。後年の比較列だけに現れる修正と `as_of` / PIT 要求にも対応しません。

## Python と MCP

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    result = atlas.get_financials(
        {
            "identifier": "ATLASDEMO",
            "metrics": ["revenue", "net_income"],
            "period": "annual",
            "count": 3,
            "allow_partial": False,
        }
    )
    print(result.model_dump_json())
```

```sh
uv run --offline atlas mcp serve --provider fixture
```

[MCP 設定例](examples/mcp.fixture.json)の絶対パスを実際のチェックアウトに変更します。標準出力は MCP プロトコル専用です。企業照合、財務、開示、見積もり、購入、購入状態の 6 ツールが使えます。支払いを行うのは購入だけで、接続先・鍵・方針ファイルをツール引数に公開しません。[詳しい導入手順](docs/quickstart.md)を参照してください。

## 任意の参照 API と決済

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
uv run --offline python examples/mock_purchase.py
```

2 行目は通信・実資金を使わない模擬購入・再取得です。モードは `off`（無料）、`mock`（架空資産、`simulated=true`）、`testnet`（公式 x402 V2 / exact / EIP-3009、Base Sepolia USDC のみ）。既定は支払い無効、mainnet はコードで拒否します。単一プロセスと非公開 SQLite volume を想定し、loopback bind・quote 上限・認証付き回復を実装しています。

別プロセスのサービスを使う場合は `ATLAS_HOSTED_ORIGIN` と非公開の `ATLAS_STATE_DIR` を設定します。有料購入は `ATLAS_POLICY_FILE` も必要です。mock 用方針は private な実行時ディレクトリへコピーして使います。

```sh
uv run atlas quote financials ATLASDEMO --metrics revenue,net_income --count 3 --json
uv run atlas purchase QUOTE_ID --json
uv run atlas purchase-status QUOTE_ID --json
```

ホスト版の通常取得は自動購入しません。見積もりには財務値も回復 token も含めず、token はクライアントの private store に保存します。決済結果が不明なら予算を保留し、新しい署名を作りません。[自己運用](docs/self-hosting.md)・[決済と testnet の明示実行](docs/payments.md)を参照してください。

## 検証と制約

```sh
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy
uv run --offline pytest
uv run --offline python -m build --no-isolation
npm ci
npm run check
```

既定の試験は IP socket を禁止し、SEC live と real testnet を除外します。公式 SDK の wire 処理と一時鍵によるローカル署名は、オフラインの Facilitator transport で確認します。**SEC ライブ取得・Base Sepolia の実送金は未実施**です。稼働中サービス・本番対応・Facilitator の実稼働を確認済みとは表示しません。[実行記録](IMPLEMENTATION_STATUS.md)、[依存関係](DEPENDENCY_AUDIT.md)、[データ利用条件](DATA_SOURCES.md)、[セキュリティ境界](SECURITY.md)に詳細を記載しています。

## ライセンス

本プロジェクトのコードは Apache License, Version 2.0 で提供します。全文は [LICENSE](LICENSE) を参照してください。

原データの利用条件は [DATA_SOURCES.md](DATA_SOURCES.md) に別途記載しています。
