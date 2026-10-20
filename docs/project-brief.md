# プロジェクト概要

Atlas Terminal は無料の公開一次ソースから米株調査用データを取得し、CLI・Python SDK・stdio MCP・参照 HTTP API に共通の取得・検証サービスを提供します。

## 対応範囲

- 政府5ソース、調査16データセット。SEC の企業・業種・財務・開示・保有、BLS の物価・雇用、米財務省の利回り・債務、FRB 公式リリース。
- 財務37指標の年次・明示四半期・YTD、元の XBRL 文脈、入力を追跡できる6比率、指定10社までの比較。
- Form 3/4/5 と13F の1提出ごとの XML 明細。大量保有、投信、企業イベントは提出資料の発見メタデータ。
- 数値の単位・期間・出典 hash・取得時刻を一緒に返す。欠損を埋めず、履歴上限や部分結果を明示する。
- 調査取得にデータ購読・API キー・wallet・LLM は不要。SEC の実連絡先を含む UA は private 設定で与える。
- 年次5指標の不変 snapshot と任意の x402 API。決済既定 off、mainnet 拒否、testnet は Base Sepolia USDC に限定。

## 技術構成

Python 3.12 以上、uv、Pydantic、httpx、Typer、FastAPI、公式 MCP / x402 SDK、SQLite。XML は標準ライブラリで扱い、DTD / entity 宣言、不正な filename、任意 URL を拒否します。Node.js 24 / Prettier は文書チェックに使います。

## 制約と検証

統合株価・板・オプション・市場予想・通信社記事・決算電話全文・完全な corporate actions / security master・PIT・売買・内蔵 LLM 分析は未提供です。無料アクセスと第三者コンテンツの再配布許諾を混同しません。

提供データの種類・取得方法・上限は[調査仕様](research.md)、全体の機能は[機能一覧](../IMPLEMENTATION_STATUS.md)、原データの条件は[出典と条件](../DATA_SOURCES.md)に記載しています。試験と未検証の範囲は[検証範囲](verification.md)を参照してください。
