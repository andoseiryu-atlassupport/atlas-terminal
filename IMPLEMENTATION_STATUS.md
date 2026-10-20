# Atlas Terminal の機能

Atlas Terminal は無料の公開一次ソースから米株調査用の情報を取得するデータツールです。CLI・Python SDK・stdio MCP・HTTP API で同じデータセットと取得規則を使います。

## データセット

| 分野       | 機能                                                                             |
| ---------- | -------------------------------------------------------------------------------- |
| 企業       | 現在の ticker / CIK 検索、企業概要、SIC 業種、取引所、決算期                     |
| 財務       | US-GAAP の37指標。損益・貸借・キャッシュフロー・EPS・株数・配当                  |
| 期間       | 年次、明示された独立四半期、YTD。実際の開始日・終了日と提出情報を照合            |
| 分析       | 元の値と計算式を保持する6比率、指定した最大10社の比較                            |
| XBRL       | 会計概念と単位の一覧、元の会計文脈を保持した観測値                               |
| 開示       | 全フォームの提出一覧と、大量保有・投信・委任状・募集・企業イベント関連資料の検索 |
| 保有・取引 | Form 3/4/5 の明細、運用者の13F情報表。1提出ごとに取得                            |
| 経済       | CPI・雇用・失業・賃金・求人など BLS の8系列                                      |
| 米財務省   | 米国債の日次パー利回り、Debt to the Penny の連邦債務                             |
| 政策       | FRB 公式リリースの見出し・公開日時・原文リンク                                   |

全16データセットの識別子・引数・単位・上限は `atlas research catalog --json` で確認できます。[調査ガイド](docs/research.md)に個別の取得例を掲載しています。

## 利用方法

| 窓口          | 操作                                                             |
| ------------- | ---------------------------------------------------------------- |
| CLI           | `atlas research catalog` / `atlas research get`                  |
| Python SDK    | `Atlas.research_catalog()` / `Atlas.get_research(query)`         |
| MCP           | `atlas_research_catalog` / `atlas_get_research` を含む8ツール    |
| HTTP          | `GET /v1/research/catalog` / `POST /v1/research`                 |
| 年次 snapshot | `financials get` / `Atlas.get_financials`。年次5指標の不変成果物 |

調査取得はデータ購読・API キー・ウォレット・LLM なしで使えます。SEC の取得にはアプリ名と実連絡先を含む private な `SEC_USER_AGENT` が必要です。`fixture` provider は架空データを通信なしで返します。

## 出典と品質

- 数値は decimal 文字列で返し、単位・対象期間・accession・原資料ハッシュ・JSON pointer / XML path・取得時刻を保持します。
- 欠損・矛盾を明示し、要求期間数と実際の収録範囲を分けます。部分結果には `allow_partial` の指定が必要です。
- 未報告のQ4、為替換算、13Fのドル/千ドル単位、CUSIPからtickerへの対応は推定しません。
- 提出資料の一覧、報告された明細、計算した比率を区別します。現在の値を過去の情報集合として扱う PIT 要求は拒否します。
- 取得先を固定し、cache・rate limit・応答上限・有限 retry を適用します。取得失敗時に有料ソースへ切り替えません。

[データソースと利用条件](DATA_SOURCES.md) · [財務の選択方法](docs/methodology.md) · [検証範囲](docs/verification.md)

## 任意の snapshot API

年次5指標の不変 snapshot は、明示的な見積もり・購入・再取得に対応します。支払いは既定で無効です。`mock` は架空の決済、`testnet` は x402 V2 exact/EIP-3009・Base Sepolia USDC を使います。予算・方針の検査、署名前の予約、認証付き回復を行い、結果不明の決済は保留します。調査 endpoint で支払いは発生しません。

## 対応範囲

統合株価、日足と調整係数、板、オプション、市場予想、通信社記事、決算電話会議全文、完全な証券マスター・企業イベント履歴、PIT、売買は提供しません。

HTTP API は単一プロセス・単一 private SQLite volume・loopback 接続向けです。mainnet、分散予算管理、自動の決済照合、外部公開用の認証・TLS は対象外です。

[使い始める](docs/quickstart.md) · [API の設定](docs/self-hosting.md) · [決済設定](docs/payments.md) · [Apache-2.0](LICENSE)
