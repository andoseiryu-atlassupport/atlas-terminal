# プロジェクト概要

Atlas Terminal は SEC の公開 JSON を取得・正規化し、CLI、Python SDK、stdio MCP、参照 HTTP API から利用できるツールです。

## 対応範囲

- 現在の企業照合、提出書類メタデータ、年次財務 5 指標。
- US-GAAP、USD、主年度 10-K / 10-K/A。値と期間、原資料参照を一緒に返す。
- ローカル SEC 取得と架空 fixture デモ。通常のデータ取得に wallet や LLM API は不要。
- 明示 quote/purchase による任意の x402 参照 API。off / mock / testnet を区別する。
- 公式 x402 V2 exact EIP-3009、Base Sepolia USDC のみ。mainnet は拒否する。

## 技術構成

Python 3.12 以上、uv、Pydantic、httpx、Typer、FastAPI、公式 MCP / x402 SDK、SQLite。Node.js 24 / Prettier はリポジトリの文書チェックに使います。

## 制約と検証

四半期、為替換算、PIT、後年の比較列だけに現れる修正値の全面追跡には対応しません。株価、ニュース、監視、売買、LLM 分析の機能はありません。

オフライン試験・公式 SDK のローカル統合は確認済みです。SEC live と実 testnet 送金は未検証です。詳細は [実装状況](../IMPLEMENTATION_STATUS.md)、[財務選択](methodology.md)、[セキュリティ](../SECURITY.md)を参照してください。
