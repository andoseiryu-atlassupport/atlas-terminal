# Atlas Terminal

Atlas Terminal の公開開発リポジトリです。Codex Cloud を使って開発を進めます。

## 現在の状態

プロジェクトの立ち上げ段階です。製品の用途、具体的な機能、アプリケーションの技術構成は、これから決定します。
現在は開発手順、Codex 向けの指示、文書の品質チェックを用意しています。

## 開発を始める

必要なツールは Git と Node.js 24 です。Node.js は現在の文書チェック用で、製品の技術構成を決めるものではありません。

```sh
git clone https://github.com/andoseiryu-atlassupport/atlas-terminal.git
cd atlas-terminal
npm ci
npm run check
```

Windows PowerShell で npm の実行が制限されている場合は、`npm.cmd` を使えます。

```powershell
npm.cmd ci
npm.cmd run check
```

`npm run check` は文書・設定ファイルの整形と、変更内容の空白エラーを確認します。
アプリケーションの起動・ビルド・テストは、技術構成を選んだ段階で追加します。

## 開発資料

- [プロジェクトの確定事項と未決事項](docs/project-brief.md)
- [Codex Cloud のセットアップ](docs/codex-cloud.md)
- [開発への参加手順](CONTRIBUTING.md)
- [Codex 向けの作業指示](AGENTS.md)

## ライセンス

ライセンスは未決定です。権利者が決定するまで、オープンソースライセンスは付与していません。
