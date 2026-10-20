# 決済と回復

## モード

- `off`: 価格 0、wallet 不要、x402 を呼ばない。回復 capability で同じ quote → result を取得する。
- `mock`: 完全オフラインの独自シミュレーション。`Atlas-Mock-Required` / `Atlas-Mock-Payment` を使い、本物の x402 と呼ばない。receipt は simulated=true、transaction=null。
- `testnet`: 公式 x402 Python 2.25.0、V2 / exact / EIP-3009、Base Sepolia `eip155:84532` の USDC のみ。`PAYMENT-REQUIRED` / `PAYMENT-SIGNATURE` / `PAYMENT-RESPONSE` を公式 SDK で処理する。mainnet は設定を書き換えても拒否する。

価格例の 10000 atomic = 0.01 USDC は試験用で、販売価格ではありません。購入単位は固定した年次財務 snapshot です。企業照合・開示・カタログ・見積もりは無料です。

## オフラインで確認

```sh
uv run --offline python examples/mock_purchase.py
uv run --offline pytest tests/test_payments.py tests/test_official_x402.py
```

後者は公式 SDK と一時鍵でローカル署名も確認しますが、Facilitator HTTP transport は模擬で、ネットワーク送信・チェーン検証・送金はありません。testnet モードのコード試験が成功しても実 testnet PASS とは記録しません。

別々のプロセスで試す場合はサーバーを `atlas serve --provider fixture --payments mock` で起動し、クライアントに origin と private な state を設定します。`examples/budget.mock.json` を private な方針ファイルへコピーして `ATLAS_POLICY_FILE` に指定します。標準の CLI で quote → purchase → purchase-status を実行できます。

## 署名と予算

operator が設定した origin、scheme、network、asset、recipient、decimals、一回/セッション/UTC 日次の上限を検証します。`--max-cost` は atomic units の文字列で上限を下げる場合だけ使えます。表示 symbol で通貨を同定しません。task/session を変えても同じ signer の日次予算を共有し、不明の予約は日付が変わっても保留します。

SQLite transaction 内で総予約を確認し、署名する直前に `RESERVED → SIGNING` を一度だけ確定します。公式 signer に渡る EIP-712 の domain、chain、contract、recipient、amount、型、期限も照合します。台帳の不調・読み込み失敗は支払いを止めます。署名・回復 token をモデルへ返しません。

## 再試行と不明状態

サーバーは verified payer、nonce、payment identifier、quote/query/resource/価格 fingerprint を保存し、nonce と注文の一意制約を設けます。決済開始前に `SETTLEMENT_SUBMITTED` を commit し、競合時は一つだけが settlement へ進みます。成功確認だけが receipt と data の公開を許可します。

Facilitator の timeout、非確定 failure、送金後 commit 前の crash は `SETTLEMENT_UNKNOWN` です。202 を返し、クライアントは再署名せず予算を保留します。再取得は bearer または保存済みと同じ支払証拠を必要とします。元 SEC データが更新されても、購入済み FinancialResult の canonical hash は変わりません。

SDK の `ResourceServer.settle_payment` は採用版で pending の内部 retry を持つため、Atlas は公式の低水準 `HTTPFacilitatorClientSync.settle` を一回だけ呼びます。存在しない facilitator status API は実装しません。不明状態の自動 onchain 照合・自動解放は未実装です。運用者が private 台帳と facilitator/chain の証跡を照合するまで、新しい支払いや予約解除を行いません。単一プロセスでも外部送金と DB commit の原子性はなく、無条件の exactly-once は保証しません。

## 実 testnet の明示実行手順（未検証）

この手順は許可済みの testnet 検証環境で明示実行するためのものです。

1. 専用の少額 testnet wallet、Base Sepolia USDC test token、許可済みの Facilitator URL と必要な認証方式を用意する。既存資産の wallet や mainnet を使わない。
2. サーバーの private environment に `ATLAS_PAY_TO`、`ATLAS_FACILITATOR_URL`、必要なら `ATLAS_PUBLIC_ORIGIN` を設定し、`uv run atlas serve --provider fixture --payments testnet` で起動する。Facilitator は固定設定の URL のみ使用し、redirect を拒否する。公開 URL の場合は HTTPS と別途の運用制御を用意する。
3. クライアントの private environment に `ATLAS_HOSTED_ORIGIN`、`ATLAS_STATE_DIR`、`ATLAS_POLICY_FILE`、`ATLAS_TESTNET_PRIVATE_KEY` を設定する。鍵の値をコマンド引数・文書・チャットへ貼らない。
4. `examples/budget.testnet.template.json` を private file へコピーする。origin と実際の testnet recipient を照合し、意図した上限を設定して enabled を明示的に true にする。テンプレートのままでは署名しない。
5. 以下を明示実行し、日時・SDK 版・network・asset・transaction・result hash・再取得の一致を証跡として記録する。

```sh
uv run python scripts/smoke.py testnet --allow-network --allow-testnet-transfer
```

Facilitator の実稼働・対応 scheme・token/domain の最終照合・認証・残高は未検証です。標準 adapter は許可済みの無認証 facilitator または注入した公式 facilitator client を使います。サービス固有の認証が必要なら、公式 AuthProvider を private な運用構成で設定する実装が別途必要です。

SEC live も別コマンドです。

```sh
uv run python scripts/smoke.py sec --allow-network
```

pytest から実行する場合も別 opt-in が必要です。外部 PR の CI では実行しません。

```sh
ATLAS_RUN_LIVE_SEC=1 uv run pytest -m live_sec
ATLAS_RUN_LIVE_X402=1 uv run pytest -m live_x402
```

これらの環境変数構文は POSIX shell 用です。Windows では process environment の設定方法を使い、同じ Python entrypoint を実行してください。資金・接続・設定がなければ NOT RUN のままにします。
