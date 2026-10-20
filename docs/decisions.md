# 技術上の選択

| 選択                             | 実装上の理由                                                               |
| -------------------------------- | -------------------------------------------------------------------------- |
| 直接 SEC REST                    | 必要な endpoint と期間選択、rate/cache を共通層で制御する                  |
| 共通 application service         | CLI / SDK / MCP / HTTP で別々の財務選択ロジックを持たない                  |
| MCP 2.3.0 の MCPServer           | 採用した公式 SDK の公開 API を使用する                                     |
| x402 の低水準 facilitator client | 一つの永続 claim ごとに settle を一回呼び、内部 retry に依存しない         |
| EIP-3009 に限定                  | domain・chain・token・recipient・amount・期限を署名境界で照合する          |
| 不明な決済は保留                 | 証拠なしの再送金・予算解放を行わない。自動 onchain reconciliation は未実装 |
| SQLite の BLOB で snapshot 保存  | private volume 一つで不変 bytes と台帳を管理する。複数 replica は対象外    |

採用 SDK の version、実 import、wire 形式、依存 license は [依存監査](../DEPENDENCY_AUDIT.md)を参照してください。コードのライセンス状態は [LICENSE](../LICENSE)、原データの条件は [DATA_SOURCES](../DATA_SOURCES.md)に記載しています。
