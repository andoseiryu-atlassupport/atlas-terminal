# 財務データの選択方法

`latest_primary_filing_per_fact_v1` は Atlas の限定的な選択規則です。SEC が提供する完成済み会計ロジックではありません。

1. 現在の ticker 一覧で企業を照合する。同じ CIK の株式クラスを保持し、複数の会社候補を勝手に確定しない。
2. submissions の recent と最大 20 個の archive を取得し、accession で提出索引を作る。archive 名と primaryDocument を検証する。
3. `10-K` / `10-K/A` に結合でき、fact.end が filing.reportDate に一致する候補だけを使う。比較列・10-Q 累計は対象外。
4. duration は実際の start/end の日数差が 330〜400 日。52/53 週を許容し、短い移行年度は除外・警告する。instant に duration を混ぜない。
5. いずれかの年次 duration 指標が存在する期末から新しい順に count 個を選ぶ。assets だけの要求でもこの期間集合を使う。
6. 指標ごとの明示タグ順、提出日、取得できる場合の acceptance 時刻で選択する。同日の順序が不明で値が異なる場合は ambiguous。accession の文字列順で異なる値を選ばない。
7. 10-K/A の修正指標だけを更新する。他の指標をゼロや別計算で補完せず、出典混在を警告する。同じ値・期間の重複は除去する。

## 指標

| Metric              | us-gaap tag                                                                      | 種類     |
| ------------------- | -------------------------------------------------------------------------------- | -------- |
| revenue             | RevenueFromContractWithCustomerExcludingAssessedTax → Revenues → SalesRevenueNet | duration |
| net_income          | NetIncomeLoss                                                                    | duration |
| assets              | Assets                                                                           | instant  |
| liabilities         | Liabilities                                                                      | instant  |
| operating_cash_flow | NetCashProvidedByUsedInOperatingActivities                                       | duration |

候補は USD だけです。revenue の概念が年度間で変わる場合は警告します。タグの説明は raw source に保存しますが、定義の同一性は保証しません。利益率、成長率、四半期差分、為替換算、Assets−Equity のような補完を行いません。

各 fact は decimal 文字列、実期間、単位、提出日、実際の acceptance 時刻または null、accession、タグ、原資料 URL、JSON pointer、原 bytes の SHA-256、取得時刻を持ちます。primaryDocument がない場合は filing index を参照し、存在未確認の文書名は作りません。

## Coverage と限界

選択した期末×指標の行列を保持し、欠損と矛盾には null と reason を返します。要求年数が不足する場合も requested/available を別表示します。少なくとも一つの reported 値が必要です。既定で不足はエラー、`allow_partial=true` の場合も quote と結果の両方で不足を示します。

後年の 10-K の比較列だけに現れる過去年の restatement は追跡しません。現在の companyfacts を使って過去の情報集合を復元できるとは扱わず、`as_of` / PIT / published_before を拒否します。数値の会計的同等性や全発行体での網羅性は保証していません。

原 bytes の hash と JSON pointer で値を追跡できます。サーバーは元データの更新と独立した canonical FinancialResult bytes を quote 時に固定します。canonical JSON は UTF-8、キー昇順、`,` / `:` 区切り、ASCII への変換なし、非有限数なしです。
