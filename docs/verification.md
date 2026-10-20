# 検証範囲

確認日: 2026-10-20。Atlas Terminal 0.2.0、Python 3.12.14 / Linux。

## 確認済みの動作

オフライン試験158件が成功しています。既定の試験は IP socket を禁止し、実ソース取得と実送金を行いません。

| 項目                   | 検証対象                                                                                                                                       |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| 調査16データセット     | 架空の原 JSON / XML から取得し、公開 schema と照合                                                                                             |
| 財務の期間・単位       | 年次・四半期・YTDの区別、Q4非生成、EPS単位、欠損・矛盾・修正提出                                                                               |
| 財務比率               | 入力の出典、対象期間の一致、分母ゼロ、Decimal の計算                                                                                           |
| 保有・取引             | Form 3/4/5 の小数・価格欠損・脚注、13Fのnamespace・単位・オプション区分                                                                        |
| 経済・金利・債務       | BLS系列と年平均、金利のnull、債務の数値精度                                                                                                    |
| HTTP取得               | mock HTTP で固定取得先、cache、etag、rate、日次上限、応答上限、有限retryを確認                                                                 |
| CLI / SDK / MCP / HTTP | 結果の一致、公式 MCP client による別プロセス接続                                                                                               |
| 任意の決済             | オフラインの x402 wire・ローカル署名・mock Facilitator、予算・認証・障害回復                                                                   |
| 配布パッケージ         | 新規仮想環境へ wheel を導入し、リポジトリ外からCLI・MCP・HTTPを実行                                                                            |
| CI                     | [GitHub Actions](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions/runs/39426994713)で型・整形・158試験・デモ・配布buildを確認 |

通常の調査 endpoint は決済モードにかかわらず支払いなしで取得します。模擬購入では API 再起動後の認証付き回復も確認しています。wheel / sdist は原 fixture と Apache-2.0 の LICENSE を含み、private な実行時DBを含みません。

## 未確認の範囲

SEC・BLS・米財務省・FRBへの実接続、公式利用条件ページの最新状態、実 Base Sepolia 送金、Windowsでの実行は未検証です。オフライン試験は、実ソースの稼働・網羅性・配信遅延・実決済の成功を示すものではありません。

## 検証コマンド

```sh
uv sync --locked --all-extras --dev
npm ci
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy
uv run --offline pytest
uv run --offline atlas demo --json
uv run --offline python examples/free_research.py
uv run --offline python examples/mock_purchase.py
uv run --offline python -m build --no-isolation
npm run check
python scripts/scan_public_files.py
```

`scripts/verify_installed.py` はインストール済みのパッケージを使い、CLI・MCP・loopback HTTP・mock購入・再起動後の回復を確認します。外部通信と実送金は行いません。

`scripts/research_smoke.py` と `scripts/smoke.py` の実ソース検査には `--allow-network` が必要です。実testnet送金には専用の設定と送金用flagも必要です。既定のCIでは実行しません。

[機能一覧](../IMPLEMENTATION_STATUS.md) · [出典と条件](../DATA_SOURCES.md) · [開発への参加](../CONTRIBUTING.md)
