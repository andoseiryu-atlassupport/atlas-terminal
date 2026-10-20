# 実装・検証状況

確認日: **2026-10-20**。Python 3.12.14 / Linux で検証。

## 検証結果

**オフライン試験 87 件合格。ライブ試験 2 件は対象外。** SEC live、実 Base Sepolia 送金、Windows の実行は未検証です。

| 項目                     | 状態              | 確認範囲                                                                             |
| ------------------------ | ----------------- | ------------------------------------------------------------------------------------ |
| Offline data / CLI / SDK | PASS              | 原 fixture の正規化、完全/部分期待値との一致、出典 hash/pointer                      |
| SEC connector            | PASS（mock HTTP） | rate/cache/etag、archive、403/429、有限 retry、bytes/time 上限                       |
| MCP subprocess           | PASS              | 公式 client で initialize/list/call、6 tools、structured content                     |
| HTTP / off / mock        | PASS              | quote、固定 snapshot、policy、原子的予算予約、認証付き再取得                         |
| 決済の競合・障害         | PASS（offline）   | 同じ proof の settle 一回、nonce/ID 重複拒否、UNKNOWN 保留、再署名禁止               |
| Official x402 SDK        | PASS（offline）   | V2 headers、exact EIP-3009 ローカル署名・署名回復、facilitator HTTP、汎用公式 client |
| 新規 wheel install       | PASS              | 別 venv・checkout 外 cwd で demo、MCP、loopback HTTP mock 購入・再起動回復           |
| Package / quality        | PASS              | sdist/wheel、fixture bytes、py.typed、ruff、mypy、Prettier                           |
| Credential pattern scan  | PASS（限定検査）  | 公開ファイルと到達可能な Git blobs。完全な秘密検出保証ではない                       |
| SEC live                 | NOT RUN           | 利用者による接続設定と live 検証が必要                                               |
| Real testnet E2E         | NOT RUN           | 専用 wallet、token、Facilitator の設定と明示実行が必要                               |
| GitHub Actions           | NOT RUN           | workflow をローカル点検。remote run の成功は未確認                                   |

## 実行したコマンド

```text
uv sync --all-extras --dev                         PASS
npm ci                                            PASS
uv run --offline ruff check .                      PASS
uv run --offline ruff format --check .             PASS
uv run --offline mypy                              PASS (19 source files)
uv run --offline pytest                            PASS (87 passed, 2 deselected)
uv run --offline atlas demo --json                  PASS
uv run --offline python examples/mock_purchase.py  PASS
uv run --offline python -m build --no-isolation     PASS
npm run check                                     PASS
python scripts/scan_public_files.py                PASS
```

初回の依存ダウンロードは必要です。hash 固定 requirements から別環境へ runtime dependencies とビルド済み wheel を導入し、デモ・MCP・模擬購入・API 再起動後の回復を確認しました。`scripts/verify_installed.py` を検証したい環境の Python で実行すると、同じローカル検査を再現できます。外部ネットワークと実資金は使いません。

デモの canonical FinancialResult SHA-256:

```text
d7bd371748db3900398007a625554b8f7e637ba93d6802940b7e8914d41f5126
```

## 起動

```sh
uv run --offline atlas demo --json
uv run atlas company resolve AAPL --provider sec --json
uv run atlas financials get AAPL --metrics revenue,net_income --period annual --count 3 --provider sec --json
uv run --offline atlas mcp serve --provider fixture
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
uv run --offline python examples/mock_purchase.py
```

SEC は private な `SEC_USER_AGENT` が必要です。hosted の購入には接続先、private state、明示 policy を設定します。[README 日本語](README.ja.md)、[MCP 導入](docs/quickstart.md)、[自己運用](docs/self-hosting.md)、[決済](docs/payments.md)を参照してください。

## バージョンと制約

Python 3.12 / Pydantic 2.14.1 / httpx 0.28.1 / Typer 0.27.3 / FastAPI 0.151.0 / Uvicorn 0.54.0 / MCP 2.3.0 / x402[evm,httpx] 2.25.0。直接・推移依存を uv.lock で固定し、実 import と公式 source を確認しました。[依存監査](DEPENDENCY_AUDIT.md)に詳細があります。

UNKNOWN からの自動 onchain 照合・予算解放、Facilitator 固有の認証統合、複数 replica、外部公開用の認証/TLS は未実装です。UNKNOWN は運用者の確認まで保留します。mainnet、四半期・為替・派生値、PIT、後年比較列の全面追跡には対応しません。コードのライセンスは [Apache-2.0](LICENSE) です。SEC 原データと依存パッケージの条件は別途適用されます。
