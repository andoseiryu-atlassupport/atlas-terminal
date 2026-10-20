# 技術上の選択

| 選択                             | 実装上の理由                                                                                   |
| -------------------------------- | ---------------------------------------------------------------------------------------------- |
| 直接 SEC REST                    | 必要な endpoint と期間選択、rate/cache を共通層で制御する                                      |
| 政府の無料一次ソース             | SEC / BLS / Treasury / Federal Reserve に限定。再配布条件の不明な相場サイトで補完しない        |
| 共通 research query              | 16データセットを dataset 別の引数・件数・出典・カバレッジで扱い、全 interface で同じ検証を使う |
| 報告値・計算値・資料一覧を区別   | 四半期と YTD を実期間から選択し、Q4 や13F金額単位を推測しない。比率は元入力と式を残す          |
| 保守的な公開 transport           | SEC 連絡先と他ソースの UA を分離。共有 cache/rate と BLS の日次上限を永続化する                |
| 共通 application service         | CLI / SDK / MCP / HTTP で別々の財務選択ロジックを持たない                                      |
| MCP 2.3.0 の MCPServer           | 採用した公式 SDK の公開 API を使用する                                                         |
| x402 の低水準 facilitator client | 一つの永続 claim ごとに settle を一回呼び、内部 retry に依存しない                             |
| EIP-3009 に限定                  | domain・chain・token・recipient・amount・期限を署名境界で照合する                              |
| 不明な決済は保留                 | 証拠なしの再送金・予算解放を行わない。自動 onchain reconciliation は未実装                     |
| SQLite の BLOB で snapshot 保存  | private volume 一つで不変 bytes と台帳を管理する。複数 replica は対象外                        |

採用 SDK の version、実 import、wire 形式、依存 license は [依存監査](../DEPENDENCY_AUDIT.md)を参照してください。コードのライセンス状態は [LICENSE](../LICENSE)、原データの条件は [DATA_SOURCES](../DATA_SOURCES.md)に記載しています。
