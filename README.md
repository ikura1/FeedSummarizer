# FeedSummarizer

RSS フィード（はてなブックマークなど）の新着記事を Claude（Anthropic）で要約し、Slack に投稿する AWS Lambda 関数です。

## 動作

1. S3 (`feedsummarizer/last_run_time.json`) から前回投稿した記事の日時を読む
2. RSS フィードから、それより新しい記事を古い順に 1 件取り出す
3. 記事本文を取得して要約し、Slack に投稿する
   - 本文は [Jina Reader](https://jina.ai/reader/) で取得し、失敗したら HTML から抽出する
   - PDF にも対応
   - X (Twitter) の URL や、要約に失敗した記事は URL のみを投稿する（失敗時は「要約できませんでした」と添える）
   - 直近 100 件で投稿済みの URL は投稿しない
4. 投稿した記事の日時と URL を S3 に保存する

1 回の実行で 1 記事を処理します。

## 環境変数

| 名前 | 説明 |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API の API キー（[Claude Console](https://platform.claude.com/) で発行） |
| `SLACK_WEBHOOK_URL` | 投稿先の Slack Incoming Webhook URL |
| `RSS_FEED_URL` | 読み込む RSS フィードの URL |
| `CLAUDE_MODEL` | 要約に使うモデル（省略時は `claude-haiku-5-5`） |

## 開発

```sh
uv sync
uv run pytest
uv run pre-commit install  # コミット時に ruff / bandit を実行
```

## デプロイ

`master` に push すると、Lint とテストが通ったあとに GitHub Actions が Lambda（`FeedSummarizer`、us-east-1、python3.13）を更新します。依存パッケージは Lambda レイヤー `lambda-feedsumarizer` として公開されます。

依存パッケージと GitHub Actions は、Dependabot が毎月まとめて更新 PR を作ります。
