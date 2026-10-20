# 無料の公開ソースによる米株調査

`atlas research`、`Atlas.get_research`、MCP の `atlas_get_research`、HTTP の `POST /v1/research` は同じサービスを呼びます。データ取得元は無料の政府一次ソースだけです。API キー、データ契約、モデル、ウォレットは必要ありません。SEC のみ、利用者自身の実連絡先を含む private な `SEC_USER_AGENT` が必要です。

各結果に欠損、対象期間、取得範囲、利用条件を付けます。API の一覧は `atlas research catalog --json`、HTTP では `GET /v1/research/catalog` です。

## データセット

| ID                         | 内容                               | 主な引数                                                    | 出力の種類                 |
| -------------------------- | ---------------------------------- | ----------------------------------------------------------- | -------------------------- |
| `companies`                | 現在の SEC 企業・ticker 一覧の検索 | `identifier`（任意）、`limit`                               | 現在の参照情報             |
| `profile`                  | 社名・SIC・上場先・決算期など      | `identifier`                                                | 提出メタデータ             |
| `statements`               | 財務37指標                         | `identifier`, `metrics`, `period`, `count`, `allow_partial` | 正規化された報告値         |
| `ratios`                   | 財務比率6種類、式と元データ        | `identifier`, `period`, `count`, `allow_partial`            | 計算値                     |
| `company_facts`            | 概念一覧、または元の XBRL 観測値   | `identifier`, `taxonomy`, `concept`, `unit`, `limit`        | 元の会計文脈を保持した値   |
| `filings`                  | 全フォームの提出一覧               | `identifier`, `form`, `limit`                               | 資料への参照               |
| `insider_transactions`     | Form 3/4/5 の取引・保有            | `identifier`, `accession`, `limit`                          | 選択した1提出の XML 明細   |
| `institutional_holdings`   | 運用者の13F保有表                  | `identifier`, `accession`, `limit`                          | 選択した1提出の XML 明細   |
| `ownership_filings`        | 13D/13G 大量保有報告               | `identifier`, `limit`                                       | 資料への参照               |
| `fund_filings`             | N-PORT/N-PX/N-CSR 等の報告         | `identifier`, `limit`                                       | 資料への参照               |
| `corporate_action_filings` | 8-K・委任状・募集・買付関連        | `identifier`, `limit`                                       | 資料への参照               |
| `economics`                | BLS 物価・雇用8系列                | `series`, `limit`                                           | 経済観測値                 |
| `treasury_yields`          | 米国債の日次パー利回り             | `year`, `limit`                                             | 日付・満期別の観測値       |
| `federal_debt`             | Debt to the Penny                  | `limit`                                                     | 日次の債務残高             |
| `policy_news`              | FRB 公式リリース                   | `limit`                                                     | 見出し・日時・原文リンク   |
| `compare`                  | 指定した最大10社の最新1期比較      | `identifiers`, `metrics`, `period`, `allow_partial`         | 企業ごとの財務とカバレッジ |

`identifier` は ticker、CIK、会社名です。13F は**運用者の CIK**を使います。発行企業の CIK を指定しても、その企業を保有する全運用者を逆引きする機能にはなりません。インサイダー報告も、照合できた提出履歴に限ります。`accession` を省略すると対象フォームの最新提出を選びます。元提出と修正提出は自動結合しません。

`limit` は1〜500、財務 `count` は1〜20、比較対象は最大10件。データセットに関係のない引数、未知の引数、bool を件数に使う入力を拒否します。フォーム別一覧の `form` は完全一致です。`SCHEDULE 13D` と旧 `SC 13D` などは元の名称を保持します。

## 実行例

次の例はすべて架空データを使い、通信しません。実データでは `--provider public` を使い、`ATLASDEMO` を実在の ticker / CIK に置き換えます。

```sh
uv run --offline atlas research get profile --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period ytd --metrics operating_cash_flow,capital_expenditure --count 2 --provider fixture --json
uv run --offline atlas research get ratios --identifier ATLASDEMO --count 1 --provider fixture --json
uv run --offline atlas research get company_facts --identifier ATLASDEMO --concept EarningsPerShareBasic --unit USD/shares --limit 5 --provider fixture --json
uv run --offline atlas research get filings --identifier ATLASDEMO --form 8-K --provider fixture --json
uv run --offline atlas research get insider_transactions --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get institutional_holdings --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get economics --series unemployment --provider fixture --json
uv run --offline atlas research get treasury_yields --year 2026 --provider fixture --json
uv run --offline atlas research get federal_debt --provider fixture --json
uv run --offline atlas research get policy_news --provider fixture --json
```

Python:

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    data = atlas.get_research(
        {
            "dataset": "statements",
            "identifier": "ATLASDEMO",
            "period": "quarterly",
            "metrics": ["revenue", "eps_basic"],
            "count": 3,
        }
    )
    print(data["records"])
```

MCP は `atlas_research_catalog` を呼び、次に `atlas_get_research` へ `{"query":{"dataset":"economics","series":"cpi"}}` を渡します。`--provider public` で起動すればマクロ情報だけの利用に SEC の連絡先は必要ありません。SEC の設定は最初の SEC 取得時に検査します。`hosted` provider は調査機能を中継しません。

HTTP:

```sh
uv run --offline atlas serve --provider fixture --payments off --port 8000
curl -sS http://127.0.0.1:8000/v1/research/catalog
curl -sS http://127.0.0.1:8000/v1/research -H 'Content-Type: application/json' -d '{"dataset":"economics","series":"cpi"}'
```

API は自己運用向けの単一プロセス・loopback 参照実装です。調査 endpoint は常に見積もり・決済なしです。任意の年次 snapshot 購入機能は別の契約を維持します。

## 財務と期間

指標名・タグ・単位はカタログの `statement_metrics` を参照します。米ドル、株数、1株当たり米ドルを区別します。IFRS や独自拡張概念は正規化対象外ですが、`company_facts` はソースに存在する taxonomy を明示指定して閲覧できます。

- `annual`: 10-K / 10-K/A の主年度、330〜400日の期間。
- `quarterly`: 70〜110日の明示された期間。10-K で報告された独立四半期も候補ですが、年次から9か月を引いた Q4 は作りません。
- `ytd`: 10-Q / 10-Q/A で70〜310日の報告値のうち、選択した提出で最も長い期間を使います。3か月値と累計値を混同しません。

accession・form・filed・submissions の reportDate と、実際の start/end を照合します。`fy` / `frame` だけで期間を決めません。貸借対照表の instant 値は、同じ報告日で実在する duration 値を確認できた期間に載せます。移行期・短い年度・duration のない期は欠落することがあります。指標ごとに元提出と修正提出が異なる場合があり、それぞれ出典を保持します。

欠損や同順位の矛盾は `null` と理由を返します。不足がある場合、既定では `INCOMPLETE_COVERAGE` です。`allow_partial=true` / `--allow-partial` の指定で部分結果を返します。報告期間が全くなければ部分指定でもエラーです。

比率は純利益率・粗利益率・営業利益率・流動比率・負債資本比率（総負債 / 株主資本）・営業 CF / 純利益です。出力単位は `ratio`、計算精度は Decimal 28桁。同じ期間の報告値だけで計算し、分母ゼロ・期間不一致・欠損は計算しません。PER・PBR・利回り予想など、株価や市場予想が必要な値はありません。

## 保有明細の注意点

Form 3/4/5 は取引コード、取得/処分コード、直接/間接、デリバティブ、価格の欠損、脚注 ID を保持します。報告された取引を一律に「買い」「売り」と解釈しません。1脚注は最大2,000文字、最大200脚注で、切り詰めは別 field で表示します。価格の通貨は推定せず、`as_reported_in_filing` とします。

13F は期末保有の遅れて提出された一部資産の報告です。現在の全保有・空売り・売買フローではありません。過年度と新様式で金額の表示規約が異なるため、`reported_value_unit=see_filing_instructions` として元数値を保持し、ドル/千ドルを推定して乗算しません。CUSIP から ticker を推測したり、有料識別子データベースへ照会したりしません。

XML 文書は提出履歴と安全な directory filename から選び、最大6候補を調べます。未知の XML 様式は成功扱いしません。資料全文・画像・添付資料の丸ごとの再配布は提供しません。

## 経済・政策データ

BLS の `series` は `cpi`、`cpi_sa`、`core_cpi`、`unemployment`、`participation`、`payrolls`、`hourly_earnings`、`job_openings`。系列 ID でも指定できます。登録不要 V1 の最新3年の範囲を使い、季節調整・単位・脚注・M13 の年平均を保持します。改定前の vintage や前年比を自動生成しません。共有キャッシュと、同一 private DB で1日25 HTTP 試行の上限を設けています。他のプログラム・同一 IP の使用分は把握できないので、上流側の制限が先に来ることがあります。

米国債は指定年の nominal par curve、`limit` の1行は1日・1満期です。日付の途中で上限に達すれば一部満期だけ返ることがあり、coverage に切り詰めを示します。Fiscal Data は最新500件までの1ページ。FRB は配信中の公式見出しとリンクだけで、会社ニュース全般や記事全文ではありません。

## 出典・検証・未提供情報

元 bytes は private cache、値には `raw_sha256`・`retrieved_at` と JSON pointer / XML local path を付けます。XML path は namespace を除いた要素名と1始まりの位置を使います。JSON pointer は RFC 6901 の `/` → `~1` を適用します。検索結果、会社名、脚注、見出しなどのソース文字列は命令として実行しません。

年・観測日を指定して過去の値を得ることと、その当時に利用できた値であることは異なります。`as_of` / `pit` 等は拒否します。比較する企業の最新決算日は一致するとは限りません。

統合株価、日足と調整係数、板、オプション、コンセンサス予想、網羅的ニュース・文字起こし、完全な証券マスター、PIT は未提供です。無料サイトの非公開 API や利用条件の不明なスクレイピングで埋めません。利用条件・出典は [DATA_SOURCES](../DATA_SOURCES.md)、試験結果は [検証範囲](verification.md) を参照してください。カタログの `live_verified=false` はオフライン検証と実接続を区別しています。
