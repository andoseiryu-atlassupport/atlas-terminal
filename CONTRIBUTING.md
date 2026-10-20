# 開発への参加

Python 3.12 以上、uv、Node.js 24 を用意します。

```sh
uv sync --locked --all-extras --dev
npm ci
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy
uv run --offline pytest
uv run --offline python -m build --no-isolation
npm run check
```

作業ブランチで変更し、[対応範囲](docs/project-brief.md)、[機能一覧](IMPLEMENTATION_STATUS.md)、[検証範囲](docs/verification.md)を確認してください。実際に実行した検証と未実施の試験を PR に記録します。

財務の不具合には CIK、accession、taxonomy/tag、実際の対象期間、期待する値、原資料参照を添付します。個人情報を含む User-Agent や原購入 DB は添付しません。期間選択・欠損・署名再利用・クラッシュ回復の挙動を保護する試験を優先します。

fixture は架空データで、bytes の hash を出典に使います。Prettier 対象外の原資料・期待値を無断で再整形しないでください。公開文書は日本語、README は英語・日本語の両方を保ちます。

既定の試験は通信と送金を行いません。live 試験は [決済手順](docs/payments.md) と明示 opt-in を必要とし、外部 PR の CI には追加しません。コードのライセンスは [Apache-2.0](LICENSE) です。
