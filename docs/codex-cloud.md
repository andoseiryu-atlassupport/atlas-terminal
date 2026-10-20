# Codex Cloud のセットアップ

## 対象

- リポジトリ: `andoseiryu-atlassupport/atlas-terminal`
- 基準ブランチ: `main`
- 実行環境: Node.js 24、npm、Git
- 初期構成ではアプリケーション用の API キー、データベース、常駐サーバーは不要

## 環境を作る

1. Codex の Web またはデスクトップ画面で、`Work in` → `Cloud` → `Select environment` → `Create environment` を選ぶ。
2. GitHub の対象アカウントを確認し、このリポジトリを選択する。
3. `Get started` で準備を開始し、次のセットアップ依頼を渡す。

```text
Prepare and verify a Codex Cloud development environment for this repository.
Read AGENTS.md and use Node.js 24 with npm and Git.
Run npm ci, then npm run check.
The repository contains development documentation and tooling only.
Do not scaffold product features or choose an application framework.
No application secrets, database, or long-running service are required.
Report the checked-out commit, runtime versions, and command results.
```

4. 必要な依存関係の取得先にアクセスできることと、チェックの結果を確認する。
5. 設定を保存し、`Publish` を実行する。`Environment published` が表示されたことを確認する。

環境のインストールコマンドを明示する場合は、次を使います。

```sh
npm ci
npm run check
```

現在は文書チェック用の npm 依存関係だけを取得します。実際の認証情報をリポジトリに追加する必要はありません。

## 新しいタスクで確認する

公開済み環境から新しいタスクを開始し、次の確認を依頼します。

```text
Read README.md, AGENTS.md, and docs/project-brief.md.
Run git rev-parse HEAD, node --version, npm --version, and npm run check.
Report the actual outputs and whether the checks succeeded.
Do not change files or implement product features for this verification task.
```

この新規タスクの成功をもって、Cloud 側でもチェックを実行できることを確認します。
ローカル実行や GitHub Actions の成功だけでは、Cloud のセットアップ完了とは扱いません。

## 初期設定後の開発

最初に [プロジェクト資料](project-brief.md) の未決事項を検討します。
機能や技術構成が決まったら、小さな単位で実装と検証を進め、Pull Request でレビューします。
依存関係やセットアップが変わったら、Cloud 環境も更新・検証し、再公開します。

## 公式資料

- [Codex Cloud](https://learn.chatgpt.com/docs/cloud)
- [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)
