import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import boto3
import feedparser
import openai
import pymupdf
import requests
from bs4 import BeautifulSoup
from extractcontent3 import ExtractContent
from openai import OpenAI

BUCKET_NAME = "feedsummarizer"
OBJECT_KEY = "last_run_time.json"
REGION = "us-east-1"
REQUEST_TIMEOUT = 5
# Reader API はページの描画を待つため応答が遅い
READER_TIMEOUT = 10
# ブラウザ以外のアクセスを弾くサイトがあるためUAを指定する
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOKEN_LENGTH = 15000
MAX_RETRIES = 3
DECREMENT_TOKEN = 1000

SKIP_DOMAINS = ["https://twitter.com", "https://x.com/"]


def lambda_handler(_event, _context):
    """RSSフィードを取得し、Slackに通知を送信するLambda関数"""
    # 環境変数から設定を読み込む
    feed_url = os.environ.get("RSS_FEED_URL")

    # S3 クライアントの初期化
    s3_client = boto3.client("s3")

    # 最終実行時間を S3 から取得
    last_run_time = get_last_run_time(s3_client, BUCKET_NAME, OBJECT_KEY)

    # RSSフィードを読み込む
    feed = fetch_feed(feed_url)

    # 最終実行時間以降の記事をフィルタリング
    entry = filter_feed_entry(feed, last_run_time)

    if entry is None:
        return {"statusCode": 200, "body": "No new entries."}

    # AWSからでは、HTMLを取得できないドメインはUrlのみを投稿
    if any(entry.link.startswith(domain) for domain in SKIP_DOMAINS):
        simple_post_to_slack(SLACK_WEBHOOK_URL, entry.link).raise_for_status()
    else:
        try:
            summary = summarize_from_url(entry.title, entry.link)

            # Slackに通知を送信
            post_to_slack(
                SLACK_WEBHOOK_URL,
                entry.link,
                entry.title,
                summary.get("text"),
                entry.get("description", ""),
                summary.get("img_url"),
            ).raise_for_status()
        except Exception as e:  # noqa: BLE001
            # 要約に失敗した記事で処理が止まり続けないよう、URLのみを投稿して先へ進める
            print(f"Failed to summarize {entry.link}: {e!r}")
            simple_post_to_slack(SLACK_WEBHOOK_URL, entry.link).raise_for_status()

    # 最後に共有した日時を実行時間としてS3に保存
    entry_time = datetime.fromisoformat(entry.date)
    set_last_run_time(s3_client, BUCKET_NAME, OBJECT_KEY, entry_time)

    # 処理結果を返す
    return {"statusCode": 200, "body": "Processed 1 entries."}


def summarize_from_url(title: str, url: str):
    """urlを要約"""
    res = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    # エラーページを要約しないよう、失敗時は例外にしてURLのみの投稿に回す
    res.raise_for_status()
    img_url = None

    # "application/pdf; charset=..." のようにパラメータが付く場合がある
    if res.headers.get("Content-Type", "").startswith("application/pdf"):
        entry_text = extract_text_from_pdf(res.content)
    else:
        # charset 指定がないと requests は ISO-8859-1 とみなし日本語が文字化けする
        if res.encoding is None or res.encoding.lower() == "iso-8859-1":
            res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        img_url = extract_ogp_image(soup, res.url)
        entry_text = get_content_text(url, res.text)
    return {"text": summarize_text(title, entry_text), "img_url": img_url}


def set_last_run_time(
    s3_client: Any,
    bucket_name: str,
    object_key: str,
    current_time: datetime,
):
    """最終実行日時をS3バケットに保存する

    Args:
        s3_client (boto3.Session): S3クライアント
        bucket_name (str): S3のバケット名
        object_key (str): S3のオブジェクトキー
        current_time (datetime): 実行日時
    """
    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=json.dumps({"last_run_time": current_time.isoformat()}),
    )


def get_last_run_time(s3_client: Any, bucket_name: str, object_key: str) -> datetime:
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
        return datetime.min.replace(tzinfo=UTC)


def fetch_feed(feed_url: str):
    """RSSフィードを取得するヘルパー関数"""
    return feedparser.parse(feed_url)


def filter_feed_entry(feed, last_run_time: datetime):
    """最終実行時間以降の記事をフィルタリングするヘルパー関数"""
    # 各記事のタイトルとリンクを出力
    for entry in reversed(feed.entries):
        # RSSフィードの登録日時をdatetimeオブジェクトに変換
        try:
            entry_date = datetime.fromisoformat(entry.date)
        except (AttributeError, ValueError) as e:
            print(f"Skip entry with invalid date {entry.get('link')}: {e!r}")
            continue

        # 最終実行時間と比較
        if entry_date > last_run_time:
            return entry


def get_content_text(url: str, text: str) -> str:
    """URLからコンテンツのテキストを取得する関数"""
    # reader apiを使用してテキストを取得
    friendly_text = get_llm_friendly_text(url)
    if friendly_text:
        return friendly_text
    return extract_content_from_html(text)


def extract_content_from_html(html: str) -> str:
    """URLからコンテンツとタイトルを抽出するヘルパー関数"""
    extractor = ExtractContent()
    opt = {"threshold": 0}
    extractor.set_option(opt)
    extractor.analyse(html)
    text, _ = extractor.as_text()
    return text


def extract_ogp_image(soup, base_url: str = ""):
    """OGP画像を抽出するヘルパー関数"""
    elem = soup.select_one('[property="og:image"]')
    if not elem or not elem.get("content"):
        return None
    # 相対パスで書かれている場合はSlackで表示できないため絶対URLにする
    return urljoin(base_url, elem["content"])


def summarize_text(title: str, text: str) -> str:
    """テキストを要約する関数

    Raises:
        ValueError: 本文が空、または要約を取得できなかった場合
    """
    if not text or not text.strip():
        raise ValueError("No content to summarize")
    # 長すぎる本文はトークンと料金を無駄にするため先に切り詰める
    text = text[:MAX_TOKEN_LENGTH]
    client = OpenAI()
    response = None
    for _ in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "あなたは技術記事の要約者です。"
                            "与えられた記事の内容を技術的な視点で、"
                            "日本語の3点の箇条書きで要約してください。"
                        ),
                    },
                    {"role": "user", "content": f"【Title】{title}\n###\n{text}"},
                ],
            )
            break
        except openai.BadRequestError as e:
            print(e)
            text = text[: len(text) - DECREMENT_TOKEN]
    if response is None or len(response.choices) < 1:
        raise ValueError("Failed to get summary from OpenAI")
    content = response.choices[0].message.content
    if not content:
        raise ValueError("Empty summary from OpenAI")
    return content


def simple_post_to_slack(webhook_url: str, text: str) -> requests.Response:
    """Slackに簡易なテキストを投稿する関数

    Args:
        webhook_url (str): SlackのWebhook URL
        text (str): Url

    Returns:
        requests.Response: Slackへのリクエストのレスポンス
    """
    payload = {
        "unfurl_links": True,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Reference: <{text}>",
                },
            }
        ],
    }
    response = requests.post(
        webhook_url,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    return response


def post_to_slack(
    webhook_url: str,
    entry_url: str,
    title: str,
    summary: str,
    comment: str,
    img_url: str,
) -> requests.Response:
    """Slackにメッセージを投稿する関数

    Args:
        webhook_url (str): SlackのWebhook URL
        entry_url (str): 記事のURL
        title (str): 記事のタイトル
        summary (str): 記事の要約
        comment (str): 記事へのコメント
        img_url (str): 記事の画像

    Returns:
        requests.Response: Slackへのリクエストのレスポンス
    """
    payload = {
        "attachments": [
            {
                "mrkdwn_in": ["text"],
                "color": "#36a64f",
                "pretext": comment,
                "title": title,
                "title_link": entry_url,
                "image_url": img_url,
                # XXX: はてぶのトレンドコメントを拾う
                "fields": [
                    {
                        "title": "要約",
                        # summaryをコードブロックで囲む
                        "value": f"```\n{summary}\n```",
                        "short": False,
                    },
                ],
                "thumb_url": img_url,
            }
        ],
    }
    response = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    return response


def extract_text_from_pdf(content: bytes) -> str:
    """PDFのバイト列からテキストを抽出する"""
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        return "".join(page.get_text() for page in doc)


def get_llm_friendly_text(url: str) -> str:
    """https://jina.ai/reader/ APIを使用して、URLからテキストを取得する

    Args:
        url (str): 要約するURL

    Returns:
        str: URLから取得したテキスト
    """
    try:
        res = requests.get(f"https://r.jina.ai/{url}", timeout=READER_TIMEOUT)
    except requests.exceptions.RequestException as e:
        print(f"Reader API failed for {url}: {e!r}")
        return None
    if res.status_code != 200:
        return None
    return res.text
