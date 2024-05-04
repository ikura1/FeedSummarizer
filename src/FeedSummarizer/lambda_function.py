import json
import os
from datetime import datetime, timezone

import boto3
import feedparser
import requests

BUCKET_NAME = "feedsummarizer"
OBJECT_KEY = "last_run_time.json"
REGION = "us-east-1"
REQUEST_TIMEOUT = 5


s3 = boto3.client("s3")


def lambda_handler(_event, _context):
    # 環境変数から設定を読み込む
    feed_url = os.environ.get("RSS_FEED_URL")
    slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

    # S3 クライアントの初期化
    s3_client = boto3.client("s3")

    # 最終実行時間を S3 から取得
    last_run_time = get_last_run_time(s3_client, BUCKET_NAME, OBJECT_KEY)

    # RSSフィードを読み込む
    feed = fetch_feed(feed_url)

    # 最終実行時間以降の記事をフィルタリング
    filtered_entries = filter_feed_entries(feed, last_run_time)

    # Slackに通知を送信
    for entry in filtered_entries:
        post_to_slack(slack_webhook_url, entry.link)

    # 現在の実行時刻を最新の実行時間としてS3に保存
    current_time = datetime.now(timezone.utc)
    set_last_run_time(s3_client, BUCKET_NAME, OBJECT_KEY, current_time)

    # 処理結果を返す
    return {"statusCode": 200, "body": f"Processed {len(filtered_entries)} entries."}


def set_last_run_time(
    s3_client: boto3.Session, bucket_name: str, object_key: str, current_time: datetime
):
    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=json.dumps({"last_run_time": current_time.isoformat()}),
    )


def get_last_run_time(
    s3_client: boto3.Session, bucket_name: str, object_key: str
) -> datetime:
    """S3バケットから前回の実行時間を取得する

    Args:
        s3_client (boto3.Session): S3クライアント
        bucket_name (str): S3バケット名
        object_key (str): S3オブジェクトキー

    Returns:
        datetime: 前回の実行時間
    """
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
        last_run_time = json.loads(response["Body"].read().decode("utf-8"))[
            "last_run_time"
        ]
        return datetime.fromisoformat(last_run_time)
    except s3_client.exceptions.NoSuchKey:
        return datetime.min.replace(tzinfo=timezone.utc)


def fetch_feed(feed_url: str):
    return feedparser.parse(feed_url)


def filter_feed_entries(feed, last_run_time: datetime):
    # 各記事のタイトルとリンクを出力
    filtered_entries = []
    for entry in feed.entries:
        # RSSフィードの登録日時をdatetimeオブジェクトに変換
        entry_date = datetime.fromisoformat(entry.date)

        # 最終実行時間と比較
        if entry_date > last_run_time:
            filtered_entries.append(entry)

    return filtered_entries


# Slackにメッセージを投稿する関数
def post_to_slack(webhook_url, message):
    payload = {"text": message}
    response = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    return response
