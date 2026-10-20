# データの出典と利用条件

調査機能は無料でアクセスできる政府一次ソースに限定します。データ購読や API キーは不要です。SEC は利用者による識別用 User-Agent と実連絡先、アクセス条件への適合が必要です。他のソースへ SEC の連絡先を送信しません。

## 採用ソース

| ソース                      | 取得するデータ                                    | 費用・認証                            | 取得範囲・留意点                                                             |
| --------------------------- | ------------------------------------------------- | ------------------------------------- | ---------------------------------------------------------------------------- |
| SEC EDGAR                   | ticker、企業、submissions、companyfacts、提出 XML | 無料、API キー不要、実連絡先を含む UA | 構造化事実と提出参照。第三者提出資料の権利を政府作成物と同一視しない         |
| BLS API V1                  | CPI・雇用・失業・賃金・求人                       | 無料、登録不要                        | 最新3年、8系列を固定登録。ローカル DB で25試行/UTC日、共有 IP の上流制限は別 |
| Treasury interest rates XML | 日次 nominal par yield curve                      | 無料、API キー不要                    | 1990年以降の指定年、日付・満期別。取引可能な債券価格ではない                 |
| Treasury Fiscal Data        | Debt to the Penny                                 | 無料、API キー不要                    | 最新500件までの1ページ。日付・米ドル単位を保持                               |
| Federal Reserve RSS         | 公式 press release の見出し・日時・リンク         | 無料、API キー不要                    | 配信中のリリースだけ。通信社記事・全文を提供しない                           |

公式資料・条件の参照先:

- SEC: [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[Developer Resources](https://www.sec.gov/about/developer-resources)、[Form 13F](https://www.sec.gov/files/form13f.pdf)。
- BLS: [API V1](https://www.bls.gov/developers/api_signature_v1.htm)、[API features and limits](https://www.bls.gov/developers/api_features.htm)、[linking and copyright](https://www.bls.gov/bls/linksite.htm)。
- Treasury yields: [CSV / XML archive](https://home.treasury.gov/interest-rates-data-csv-archive)、[site policy](https://home.treasury.gov/footer/privacy-act)。
- Fiscal Data: [API documentation](https://fiscaldata.treasury.gov/api-documentation/)、[about](https://fiscaldata.treasury.gov/about-us/)。
- Federal Reserve: [feeds](https://www.federalreserve.gov/feeds/feeds.htm)、[disclaimer](https://www.federalreserve.gov/disclaimer.htm)。

**確認範囲:** 固定 endpoint、データ形状、条件付き cache、上限、欠損・単位・出典をオフラインの fixture / HTTP mock で試験しています。この開発環境からの実接続、公式ページの最新状態、個別の再配布条件の最終確認は未実施です。参照リンクは利用条件の保証や個別のライセンス許諾ではありません。実運用では原サイトの最新条件・制限に従ってください。

## 取得と加工の範囲

任意 URL や mirror を受け付けず、redirect を追いません。期限切れ cache の更新に失敗しても、古い値を最新値として返したり、有料 provider に切り替えたりしません。HTTP は共有 rate limiter と最大3試行、原 bytes の時間・サイズ上限を使います。BLS の日次試行数も永続化します。

`statements` は年次・四半期・YTD、37の US-GAAP 指標をタグと単位を明示して正規化します。旧 `financials` は年次5指標の契約を維持します。元の taxonomy / unit / context を読む `company_facts`、計算式と入力を残す `ratios`、提出一覧だけの discovery datasets を区別します。[選択方法](docs/methodology.md)と[調査仕様](docs/research.md)を参照してください。

無料閲覧と再配布許諾は異なります。Yahoo Finance 等の非公開 API、再配布条件が確認できない無料日足、取引所の有料相場、コンセンサス予想、通信社記事、決算電話全文は統合していません。株価・板・全銘柄の調整係数・PIT を取得できるとは表示しません。

## SEC endpoint

| データ                | 参照元                                                                    |
| --------------------- | ------------------------------------------------------------------------- |
| 現在の ticker / CIK   | https://www.sec.gov/files/company_tickers.json                            |
| 企業と提出履歴        | https://data.sec.gov/submissions/CIK{cik}.json                            |
| 追加履歴              | submissions が列挙する検証済みの archive filename                         |
| XBRL facts            | https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json                  |
| 提出 XML と directory | 検証済み CIK / accession の https://www.sec.gov/Archives/edgar/data/ 配下 |

一次資料: [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[Accessing EDGAR](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)、[Developer Resources](https://www.sec.gov/about/developer-resources)。SEC の実レスポンス取得と公式ページの最新状態の確認は未実施です。endpoint 処理の規則は HTTP MockTransport で試験しています。実運用前に最新条件を確認してください。

SEC の原データがすべての会社・会計概念・原資料を網羅するとは扱いません。現在の ticker は過去の symbol history ではありません。13F は運用者別の個々の提出から取り、全投資家のリアルタイム保有一覧へ拡張解釈しません。

原文 JSON / XML bytes は private cache に保存し、hash と JSON pointer / XML local path で追跡します。提供対象は構造化事実・提出メタデータ・限定した脚注・元資料参照で、第三者の文書全文、画像、音声を丸ごと再配信しません。コードの Apache-2.0 が原資料全体へ自動適用されるものではありません。

`resources/sec_like`、`resources/research`、`tests/fixtures` の会社・CIK・金額・観測値はすべて架空です。`.invalid` の資料リンクへ通信しません。fixture の取得時刻は 2026-10-20T00:00:00Z 固定、原 hash は同梱 bytes から算出します。政府の原資料に似せた fixture を実データと表示しません。
