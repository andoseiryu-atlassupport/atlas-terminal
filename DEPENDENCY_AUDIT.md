# 依存関係・公式仕様の確認

確認日: 2026-10-20。Python 3.12.14、uv 0.12.23、Node.js 24.19.0。バージョンは実際の PyPI metadata、resolver、installed distribution から確認し、`pyproject.toml` と `uv.lock` に固定した。配布名の空き状況や公開可否は確認していない。

## 直接依存

| Distribution    | 採用版  | License      | 確認                                                                   |
| --------------- | ------- | ------------ | ---------------------------------------------------------------------- |
| fastapi         | 0.151.0 | MIT          | create_app / TestClient / OpenAPI                                      |
| httpx           | 0.28.1  | BSD-3-Clause | bounded stream / MockTransport / no redirects                          |
| mcp             | 2.3.0   | MIT          | MCPServer、ClientSession、stdio_client、structured content             |
| pydantic        | 2.14.1  | MIT          | strict query / JSON / exact financial strings                          |
| typer           | 0.27.3  | MIT          | CLI、TyperException による入力エラー分類                               |
| uvicorn         | 0.54.0  | BSD-3-Clause | single worker / loopback local HTTP smoke                              |
| x402[evm,httpx] | 2.25.0  | MIT          | official V2 models / payment identifier / exact EVM / facilitator HTTP |

開発依存は build 1.6.1、hatchling 1.32.4、jsonschema 4.26.0、mypy 2.4.0、pytest 9.1.1、pytest-asyncio 1.4.0、pytest-socket 0.8.1、ruff 0.17.1。文書チェックは既存の Prettier 3.9.10。推移依存も lock し、開発環境の 89 distribution の license metadata を [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) に記録した。これは脆弱性データベースとの照合を実施したという意味ではない。

## SEC REST と EdgarTools の比較

必要な ticker、submissions/archive、companyfacts と独自の期間選択を一元管理するため、httpx による直接 REST を採用した。[EdgarTools](https://github.com/dgunning/edgartools) は再利用候補として PyPI metadata を確認した（5.62.0、MIT、requires_dist 41 件、extras を含む）。幅広い filing/table 機能と追加依存を導入せず、Atlas 側で rate/cache/期間規則を一元管理する。EdgarTools 自体はインストールしていない。

SEC live 取得・公式ページの最新状態の確認は NOT RUN。既定 2 req/s・同時 2・archive 20 個・bytes/時間上限・条件付き cache を HTTP mock で試験する。

## 公式 MCP SDK

[公式リポジトリ README](https://github.com/modelcontextprotocol/python-sdk) と解決した 2.3.0 の installed source を確認した。旧 `mcp.server.fastmcp.FastMCP` はこの版で削除されているため、`from mcp.server import MCPServer` を使う。

実 import は `mcp.server.MCPServer`、`mcp.client.session.ClientSession`、`mcp.client.stdio.StdioServerParameters/stdio_client`、`mcp.types.CallToolResult/ToolAnnotations`。Python の field 名は snake_case、wire は公式 SDK の aliases へ委ねる。公開 middleware で財務要求を型変換前に検証し、未知 field・PIT 要求・bool count を黙って受け入れない。別プロセスで initialize/list/call を公式 client によって確認する。

## 公式 x402 SDK

[公式 Python source](https://github.com/x402-foundation/x402/tree/main/python/x402)、公式 README、PyPI 2.25.0 と installed source を照合した。import を実行して確認した主要 API:

```python
from x402 import x402ClientSync, x402ResourceServerSync
from x402.http import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.http.utils import (
    encode_payment_required_header,
    decode_payment_required_header,
    encode_payment_signature_header,
    decode_payment_signature_header,
    encode_payment_response_header,
)
from x402.mechanisms.evm.exact import ExactEvmClientScheme, ExactEvmServerScheme
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.extensions.payment_identifier import (
    declare_payment_identifier_extension,
    append_payment_identifier_to_extensions,
    extract_payment_identifier,
    generate_payment_id,
)
```

`ExactEvmServerScheme` の payment_flows annotation が SDK 自身の protocol と一致しない箇所は、境界で明示 cast し、実 binding を offline test で検証した。API を推測したスタブではない。

`HTTPFacilitatorClientSync` の既定 transport は redirect 追従を有効にするため、Atlas は no-redirect の httpx.Client を dataclass `FacilitatorConfig` に渡す。`ResourceServer.settle_payment` の pending retry を採用せず、永続 claim の後で公式 low-level settle を一回だけ呼ぶ。Facilitator の実 `/supported` が V2 exact Base Sepolia を返すまで challenge を出さない。

USDC の network `eip155:84532`、contract `0x036CbD53842c5426634e7929541eC2318f3dCF7e`、decimals 6、EIP-712 name `USDC` / version `2` を、採用版の `mechanisms/evm/default_assets.py` で確認した。[Circle の一次資料](https://developers.circle.com/stablecoins/usdc-contract-addresses) の最新状態は再確認していない。実送金前の Circle との最終照合は残す。

公式モデルによる 402 challenge、EIP-3009 ローカル署名、eth-account による署名回復、公式 HTTP facilitator client の `/supported` / `/verify` / `/settle` 通信形式、汎用公式 x402 client の購入を offline transport で試験する。これは Facilitator の実稼働・認証・chain inclusion・資金残高の証明ではない。

Facilitator 固有の AuthProvider、実接続の可否、real Base Sepolia 送金、公開 transaction の証跡は未確認。標準設定で mock への fallback はしない。秘密値を要求・表示せず、明示 opt-in smoke と不足設定の分類を用意した。

## ライセンスと CI

コードのライセンスは Apache-2.0。正式な原文は [Apache 公式 GitHub](https://github.com/apache/.github/blob/main/LICENSE) から取得し、ルートの [LICENSE](LICENSE) に同梱した。README、Python・npm のパッケージ設定に同じ SPDX 識別子を記載する。原データと依存パッケージの条件は別扱い。

新規追加の `actions/setup-python` v6 は `git ls-remote` で `ece7cb06caefa5fff74198d8649806c4678c61a1` を確認して固定した。既存 checkout/setup-node の SHA を維持する。[GitHub Actions の CI run #2](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions/runs/39422982457) で依存導入・品質チェック・offline 試験・デモ・配布 build の成功を確認した。

## 無料調査コネクター

調査機能は追加パッケージを導入せず、既存の httpx / Pydantic / FastAPI / MCP と Python 標準の xml.etree.ElementTree を使う。SEC と政府公開ソースの HTTP cache/rate/retry を共有実装にまとめ、DTD / entity 宣言・UTF-16・過大な XML を拒否してから解析する。BLS・Treasury・Federal Reserve の実接続と最新の公式条件の最終確認は未実施で、採用版の検証は fixture と HTTP mock に限る。[出典](DATA_SOURCES.md)と[調査仕様](docs/research.md)を参照。
