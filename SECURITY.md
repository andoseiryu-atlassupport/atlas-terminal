# セキュリティと境界

データ取得は read-only ですが、`atlas_purchase` は委任された資産の支出を伴います。MCP annotations は説明用で、制御は署名境界の policy と永続台帳で行います。

- 主年度の財務値と出典を返すサービスです。外部の会社名・文書名は文字列データとして扱い、命令実行や支払先変更へ使いません。
- SEC endpoint は固定 allowlist。archive は CIK 付き basename、primaryDocument は安全な filename のみ。URL・SQL・shell コマンドを通常の識別子として受け付けません。
- ホスト・Facilitator は operator の固定設定だけを使い、redirect を追従しません。支払先・network・token・domain・金額・期限・resource を署名前に照合します。
- mainnet は未対応。Base Sepolia USDC、exact / EIP-3009 に固定します。mock は架空 network/asset と専用ヘッダーを使い、本物の x402 と混同しません。
- 予算予約と SIGNING claim は SQLite の write transaction で一度だけ確定します。全 task/session の同じ signer を日次集計し、未確定予約は日をまたいでも保持します。
- サーバーは settlement 前に注文と nonce を確定します。timeout/crash は UNKNOWN に残し、自動で再送金・再署名・解放を行いません。証跡未確認を未払い扱いしません。
- 注文 ID は認証ではありません。回復 bearer は status と購入済み artifact に限定し、新規支出を許可しません。サーバーには token hash、クライアントには private capability を保存します。
- 秘密鍵、seed、raw 支払署名、bearer、購入 DB、SEC 連絡先は公開ファイル・ツール出力に含めません。HTTP/payment ライブラリの診断は抑制し、既存 root handler にも filter を設定します。CLI/API の例外は入力値や外部例外の全文を出しません。

private state はディレクトリ 0700、DB 0600 で作成します。WAL/SHM も含めて保護し、共有ディレクトリに実購入 DB を置かないでください。MCP ツールから policy ファイルや wallet を変更する操作は公開していません。

**同じ OS ユーザー権限で shell を実行できる主体は、環境・policy・DB を読んだり書き換えたりできます。ローカル設定は hardware signer や OS sandbox による隔離ではありません。** 複数端末・複数 volume・分散予算・預かり wallet・mainnet 金融安全性・SLA は今回対象外です。

## 報告

連絡先は所有者が選定していません。脆弱性の報告先を捏造しません。公開 Issue に秘密鍵・支払証拠・回復 token・実購入 DB を添付しないでください。公開前に所有者が private な報告経路を設定する必要があります。
