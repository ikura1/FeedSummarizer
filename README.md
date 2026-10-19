# FeedSummarizer

RSS フィード（はてなブックマークなど）の新着記事を OpenAI で要約し、Slack に投稿する AWS Lambda 関数です。

## 動作

1. S3 (`feedsummarizer/last_run_time.json`) から前回投稿した記事の日時を読む
2. RSS フィードから、それより新しい記事を古い順に取り出す
3. 記事本文を取得して要約し、Slack に投稿する
   - 本文は [Jina Reader](https://jina.ai/reader/) で取得し、失敗したら HTML から抽出する
   - PDF にも対応
   - X (Twitter) の URL や、要約に失敗した記事は URL のみを投稿する
4. 投稿した記事の日時を S3 に保存する

1 回の実行で最大 `MAX_ENTRIES_PER_RUN` 件（既定 3 件）を処理します。Lambda の残り時間が 40 秒を切ったら、残りは次回に回します。

## 環境変数

| 名前 | 説明 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI の API キー |
| `SLACK_WEBHOOK_URL` | 投稿先の Slack Incoming Webhook URL |
| `RSS_FEED_URL` | 読み込む RSS フィードの URL |
| `OPENAI_MODEL` | 要約に使うモデル（省略時は `gpt-4o-mini`） |
| `MAX_ENTRIES_PER_RUN` | 1 回の実行で投稿する記事の上限（省略時は 3） |

## 開発

```sh
uv sync
uv run pytest
uv run pre-commit install  # コミット時に ruff / bandit を実行
```

## デプロイ

`master` に push すると、Lint とテストが通ったあとに GitHub Actions が Lambda（`FeedSummarizer`、us-east-1、python3.13）を更新します。依存パッケージは Lambda レイヤー `lambda-feedsumarizer` として公開されます。
