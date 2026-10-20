# Atlas Terminal

Atlas Terminal は、AI エージェントが無料の公開一次ソースから米株を調査するためのデータツールです。企業情報、財務、開示、報告された保有・取引情報、経済指標を CLI・Python SDK・stdio MCP・HTTP API から利用できます。

[English](README.md) · [機能一覧](IMPLEMENTATION_STATUS.md) · [調査ガイド](docs/research.md) · [データソース](DATA_SOURCES.md)

## 提供する情報

SEC EDGAR、BLS、米財務省の利回り・Fiscal Data、FRB の5ソースから16種類のデータセットを扱います。

| 分野       | 内容                                                                                   |
| ---------- | -------------------------------------------------------------------------------------- |
| 企業       | 現在の ticker / CIK 一覧、企業概要、SIC 業種、取引所、決算期                           |
| 財務       | 損益・貸借・キャッシュフロー・EPS・株数・配当の37指標。年次・報告された独立四半期・YTD |
| 分析       | 計算式と元の報告値を残す6つの比率、指定した最大10社の比較                              |
| XBRL       | 会計概念一覧、taxonomy・単位・会計文脈を保持した観測値                                 |
| 開示       | 全フォームの提出一覧。大量保有、投信、委任状、募集、企業イベント関連資料               |
| 保有・取引 | Form 3/4/5 の取引・保有明細、選択した運用者の13F保有表                                 |
| 経済       | BLS の物価・雇用8系列、米国債の日次パー利回り、連邦債務                                |
| 政策       | FRB 公式リリースの見出し、公開日時、原文リンク                                         |

数値には単位、実際の対象期間、原資料ハッシュ、取得時刻、JSON pointer / XML path を付けます。欠損・矛盾は明示し、財務比率には計算式と入力値を残します。

## 使い始める

Python 3.12 以上と [uv](https://docs.astral.sh/uv/) を用意し、リポジトリのディレクトリで実行します。

```sh
uv sync --locked
uv run --offline atlas research catalog --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
```

`fixture` は通信せずに試せる架空データです。会社・識別子・数値は実在の情報ではなく、`is_synthetic=true` と `.invalid` の資料リンクで区別します。

実データの調査では `--provider public` が既定です。SEC には利用者のアプリ名・実際の連絡先メールを含む `SEC_USER_AGENT` を非公開の設定で渡します。BLS・米財務省・FRB の取得には、この設定は不要です。

```sh
uv run atlas research get statements --identifier AAPL --period quarterly --metrics revenue,eps_basic --count 3 --allow-partial --json
uv run atlas research get economics --series cpi --json
uv run atlas research get treasury_yields --year 2026 --json
```

調査機能にデータ購読、API キー、ウォレット、LLM は不要です。コマンドは JSON を返し、エラー時は非ゼロで終了します。データセット名・指標・経済系列・取得上限は `research catalog` と[調査ガイド](docs/research.md)を参照してください。

## Python・MCP・HTTP

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    result = atlas.get_research(
        {
            "dataset": "statements",
            "identifier": "ATLASDEMO",
            "metrics": ["revenue", "eps_basic"],
            "period": "quarterly",
            "count": 3,
        }
    )
    print(result["records"])
```

MCP サーバー:

```sh
uv run --offline atlas mcp serve --provider fixture
```

調査カタログ、調査取得、企業照合、年次 snapshot、開示、見積もり、購入、購入状態の8ツールを提供します。`atlas_research_catalog` で対象を調べ、`atlas_get_research` に `query` object を渡して取得します。[MCP の接続方法](docs/quickstart.md)。

ローカル HTTP API:

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
```

`GET /v1/research/catalog` はデータセットとクエリ仕様、`POST /v1/research` は調査結果を返します。調査取得に見積もり・支払いは不要です。[API の設定](docs/self-hosting.md)。

## 年次 snapshot と任意の決済

`financials get` と `Atlas.get_financials` は、`revenue`、`net_income`、`assets`、`liabilities`、`operating_cash_flow` の年次・US-GAAP・USD snapshot を返します。37指標や四半期・YTD は `research get statements` で取得できます。

HTTP API は、明示的な見積もり・購入・再取得による不変の年次 snapshot にも対応します。決済モードは `off`、`mock`、`testnet` で、既定は off です。testnet は x402 V2 exact/EIP-3009 と Base Sepolia USDC に対応し、mainnet は対象外です。調査取得では snapshot を購入しません。[決済設定](docs/payments.md)。

## データの範囲

提出一覧は資料のメタデータとリンクです。構造化した保有・取引明細は選択した1提出の情報で、修正提出・報告の遅れ・収録範囲に制限があります。現在の snapshot や改定後の経済観測値から、過去の時点で利用可能だった情報を復元することはできません。

統合株価、板、オプションチェーン、市場予想、通信社記事、決算電話会議全文は提供しません。ソースのアクセス制限と第三者資料の利用条件は、コードのライセンスとは別に適用されます。

[出典と条件](DATA_SOURCES.md) · [財務の選択方法](docs/methodology.md) · [検証範囲](docs/verification.md) · [セキュリティ](SECURITY.md) · [開発への参加](CONTRIBUTING.md)

## ライセンス

Atlas Terminal のコードは [Apache-2.0](LICENSE) です。原データと依存パッケージには、それぞれの利用条件が適用されます。
