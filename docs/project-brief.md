# プロジェクト概要

Atlas Terminal は無料の公開一次ソースから米株調査用データを取得し、CLI・Python SDK・stdio MCP・参照 HTTP API に共通の取得・検証サービスを提供します。

## 対応範囲

- 政府5ソース、調査16データセット。SEC の企業・業種・財務・開示・保有、BLS の物価・雇用、米財務省の利回り・債務、FRB 公式リリース。
- 財務37指標の年次・明示四半期・YTD、元の XBRL 文脈、入力を追跡できる6比率、指定10社までの比較。
- Form 3/4/5 と13F の1提出ごとの XML 明細。大量保有、投信、企業イベントは提出資料の発見メタデータ。
- 数値の単位・期間・出典 hash・取得時刻を一緒に返す。欠損を埋めず、履歴上限や部分結果を明示する。
- 調査取得にデータ購読・API キー・wallet・LLM は不要。SEC の実連絡先を含む UA は private 設定で与える。
- 既存の年次5指標と任意の x402 snapshot API は別契約を維持。決済既定 off、mainnet 拒否、testnet は Base Sepolia USDC に限定。

## 技術構成

Python 3.12 以上、uv、Pydantic、httpx、Typer、FastAPI、公式 MCP / x402 SDK、SQLite。XML は標準ライブラリで扱い、DTD / entity 宣言、不正な filename、任意 URL を拒否します。Node.js 24 / Prettier は文書チェックに使います。

## 制約と検証

統合株価・板・オプション・市場予想・通信社記事・決算電話全文・完全な corporate actions / security master・PIT・売買・内蔵 LLM 分析は未提供です。無料アクセスと第三者コンテンツの再配布許諾を混同しません。

各調査データセットは架空 fixture で検証し、HTTP connector は mock で試験します。実ソース・最新の公式条件・実 testnet 送金は未検証です。[調査仕様](research.md)、[実装状況](../IMPLEMENTATION_STATUS.md)、[出典と条件](../DATA_SOURCES.md)を参照してください。
