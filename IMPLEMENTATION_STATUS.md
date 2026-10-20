# 実装・検証状況

確認日: **2026-10-20**。Python 3.12.14 / Linux で検証。

## 検証結果

**v0.2.0: オフライン試験158件合格（追加の調査試験71件を含む）。ライブ試験2件は対象外。** SEC・BLS・Treasury・FRB の実接続と公式条件ページの最新状態、実 Base Sepolia 送金、Windows の実行は未検証です。

| 項目                       | 状態              | 確認範囲                                                                                                                                                                         |
| -------------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Offline data / CLI / SDK   | PASS              | 原 fixture の正規化、完全/部分期待値との一致、出典 hash/pointer                                                                                                                  |
| SEC connector              | PASS（mock HTTP） | rate/cache/etag、archive、403/429、有限 retry、bytes/time 上限                                                                                                                   |
| MCP subprocess             | PASS              | 公式 client で initialize/list/call、8 tools、調査 catalog/get と structured content                                                                                             |
| HTTP / off / mock          | PASS              | quote、固定 snapshot、policy、原子的予算予約、認証付き再取得                                                                                                                     |
| 決済の競合・障害           | PASS（offline）   | 同じ proof の settle 一回、nonce/ID 重複拒否、UNKNOWN 保留、再署名禁止                                                                                                           |
| Official x402 SDK          | PASS（offline）   | V2 headers、exact EIP-3009 ローカル署名・署名回復、facilitator HTTP、汎用公式 client                                                                                             |
| 新規 wheel install         | PASS              | 新規 venv に0.2.0 wheel と wheelを含む73パッケージを導入。checkout 外 cwd で調査 CLI/MCP/HTTP、旧デモ、mock購入・再起動回復                                                      |
| Package / quality          | PASS              | sdist/wheel、fixture bytes、py.typed、ruff、mypy、Prettier                                                                                                                       |
| Credential pattern scan    | PASS（限定検査）  | 公開ファイルと到達可能な Git blobs。完全な秘密検出保証ではない                                                                                                                   |
| SEC live                   | NOT RUN           | 利用者による接続設定と live 検証が必要                                                                                                                                           |
| Real testnet E2E           | NOT RUN           | 専用 wallet、token、Facilitator の設定と明示実行が必要                                                                                                                           |
| GitHub Actions（既存基盤） | PASS              | [CI run #2](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions/runs/39422982457) で依存導入・整形・型検査・87件のoffline試験・秘密情報検査・デモ・配布buildが成功 |

## 実行したコマンド

```text
uv sync --locked --all-extras --dev --offline      PASS
npm ci                                            PASS
uv run --offline ruff check .                      PASS
uv run --offline ruff format --check .             PASS
uv run --offline mypy                              PASS (23 source files)
uv run --offline pytest                            PASS (158 passed, 2 deselected)
uv run --offline atlas demo --json                  PASS
uv run --offline python examples/free_research.py  PASS
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

UNKNOWN からの自動 onchain 照合・予算解放、Facilitator 固有の認証統合、複数 replica、外部公開用の認証/TLS は未実装です。UNKNOWN は運用者の確認まで保留します。mainnet、為替換算、PIT、後年比較列の全面追跡には対応しません。旧 financials 契約は年次5指標を維持し、拡張調査の四半期・YTD・比率は別契約です。コードのライセンスは [Apache-2.0](LICENSE) です。SEC 原データと依存パッケージの条件は別途適用されます。

## 無料調査 v0.2.0

政府5ソース・16データセット、財務37指標、6比率、経済8系列を追加しました。企業参照・報告値・計算値・XML保有/取引・提出資料一覧・経済観測値・公式見出しを区別し、CLI / SDK / MCP / HTTP で共通サービスを使います。[調査仕様](docs/research.md)と[出典](DATA_SOURCES.md)に制限を記載しています。

追加71試験では全データセットの架空原 bytes、公開 JSON schema、quarter/YTD と Q4 非生成、EPS単位とJSON pointer、欠損/矛盾/修正、比率の期間不一致/分母ゼロ、13F namespace/単位/option、Form 4 の小数/価格欠損/脚注、安全な取得先、BLS系列/年平均/永続日次上限、金利のnull、債務の精度、CLI・実MCP subprocess・HTTPの一致を検証しました。決済が mock のサーバーでも調査取得に支払いが発生しないことを確認しています。

新規仮想環境にビルド済み0.2.0 wheelを導入し、`python -I scripts/verify_installed.py` 相当の checkout 外検査が成功しました。17個の新しい原 fixture、Apache-2.0のLICENSE、配布metadataを確認し、wheel / sdist に private material や実DBを含めていません。

今回のローカル検証とCIは別の証拠です。上表のCI run #2は既存基盤の87試験に対する記録です。拡張版の最新CIは[GitHub Actions](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions?query=branch%3Acodex%2Ffree-us-equity-research)で確認してください。

統合株価・日足/調整係数・板・オプション・市場予想・通信社記事・決算電話全文・完全な企業イベント履歴とPITは未提供です。無料アクセスを第三者資料の無条件の再配布権とは扱いません。ライブ検査用の `scripts/research_smoke.py` は `--allow-network` がない限り通信しません。この環境では実ソースへの接続を検証していません。
