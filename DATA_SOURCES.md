# データの出典と利用条件

初期 provider は SEC の公開 JSON だけです。API キーは不要ですが、利用者による識別用 User-Agent と実連絡先、SEC のアクセス条件への適合が必要です。

| データ              | 参照元                                                   |
| ------------------- | -------------------------------------------------------- |
| 現在の ticker / CIK | https://www.sec.gov/files/company_tickers.json           |
| 企業と提出履歴      | https://data.sec.gov/submissions/CIK{cik}.json           |
| 追加履歴            | submissions が列挙する検証済みの archive filename        |
| XBRL facts          | https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json |

一次資料: [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[Accessing EDGAR](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)、[Developer Resources](https://www.sec.gov/about/developer-resources)。SEC の実レスポンス取得と公式ページの最新状態の確認は未実施です。endpoint 処理の規則は HTTP MockTransport で試験しています。実運用前に最新条件を確認してください。

年次・US-GAAP・USD の明示タグに限定した Atlas の正規化です。[選択方法と限界](docs/methodology.md)を参照してください。SEC の原データがすべての会社・会計概念・原資料を網羅するとは扱いません。現在の ticker は過去の symbol history ではありません。

原文 JSON bytes は private cache に保存し、hash と JSON pointer で追跡します。提供対象は構造化事実・提出メタデータ・元資料参照で、第三者の文書全文、画像、音声を丸ごと再配信しません。コードライセンスの選択によって原資料全体へ同じ権利が自動付与されるものではありません。

`resources/sec_like` と `tests/fixtures` の会社、CIK、金額、URL はすべて架空です。`.invalid` リンクへ通信しません。fixture の取得時刻は 2026-10-20T00:00:00Z 固定、原 hash は同梱 bytes から算出します。
