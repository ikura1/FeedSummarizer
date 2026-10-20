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
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOKEN_LENGTH = 15000
MAX_RETRIES = 3
DECREMENT_TOKEN = 1000

SKIP_DOMAINS = ["https://twitter.com", "https://x.com/"]
# 同じURLの二重投稿を防ぐため、直近に投稿したURLを覚えておく件数
RECENT_URLS_LIMIT = 100
SUMMARY_FAILED_NOTE = "（要約できませんでした）"


def lambda_handler(_event, _context):
    """RSSフィードを取得し、Slackに通知を送信するLambda関数"""
    # 環境変数から設定を読み込む
    feed_url = os.environ.get("RSS_FEED_URL")

    # S3 クライアントの初期化
    s3_client = boto3.client("s3")

    # 最終実行時間と直近に投稿したURLを S3 から取得
    state = load_state(s3_client, BUCKET_NAME, OBJECT_KEY)
    last_run_time = parse_last_run_time(state)
    recent_urls = state.get("recent_urls", [])

    # RSSフィードを読み込む
    feed = fetch_feed(feed_url)

    # 最終実行時間以降の記事をフィルタリング（投稿済みのURLは飛ばす）
    skipped = False
    entry = filter_feed_entry(feed, last_run_time)
    while entry is not None and entry.link in recent_urls:
        print(f"Skip already posted {entry.link}")
        skipped = True
        last_run_time = datetime.fromisoformat(entry.date)
        entry = filter_feed_entry(feed, last_run_time)

    if entry is None:
        if skipped:
            set_last_run_time(
                s3_client, BUCKET_NAME, OBJECT_KEY, last_run_time, recent_urls
            )
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
            simple_post_to_slack(
                SLACK_WEBHOOK_URL, entry.link, SUMMARY_FAILED_NOTE
            ).raise_for_status()

    # 最後に共有した日時を実行時間として、投稿したURLとともにS3に保存
    entry_time = datetime.fromisoformat(entry.date)
    recent_urls = (recent_urls + [entry.link])[-RECENT_URLS_LIMIT:]
    set_last_run_time(s3_client, BUCKET_NAME, OBJECT_KEY, entry_time, recent_urls)

    # 処理結果を返す
    return {"statusCode": 200, "body": "Processed 1 entries."}


def summarize_from_url(title: str, url: str):
    """urlを要約

    Raises:
        ValueError: 本文を取得できなかった場合
    """
    res = None
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
    except requests.exceptions.RequestException as e:
        # AWS からの直接アクセスを弾くサイトでも Reader API なら取得できるため続行する
        print(f"Failed to fetch {url}: {e!r}")
        res = None

    img_url = None
    html = ""
    # "application/pdf; charset=..." のようにパラメータが付く場合がある
    if res is not None and res.headers.get("Content-Type", "").startswith(
        "application/pdf"
    ):
        entry_text = extract_text_from_pdf(res.content)
    else:
        if res is not None:
            # charset 指定がないと requests は ISO-8859-1 とみなし日本語が文字化けする
            if res.encoding is None or res.encoding.lower() == "iso-8859-1":
                res.encoding = res.apparent_encoding
            html = res.text
            img_url = extract_ogp_image(BeautifulSoup(html, "html.parser"), res.url)
        entry_text = get_content_text(url, html)
    return {"text": summarize_text(title, entry_text), "img_url": img_url}


def set_last_run_time(
    s3_client: Any,
    bucket_name: str,
    object_key: str,
    current_time: datetime,
    recent_urls: list[str] | None = None,
):
    """最終実行日時をS3バケットに保存する

    Args:
        s3_client (boto3.Session): S3クライアント
        bucket_name (str): S3のバケット名
        object_key (str): S3のオブジェクトキー
        current_time (datetime): 実行日時
        recent_urls (list[str] | None): 直近に投稿したURL
    """
    state = {"last_run_time": current_time.isoformat()}
    if recent_urls is not None:
        state["recent_urls"] = recent_urls
    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=json.dumps(state),
    )


def load_state(s3_client: Any, bucket_name: str, object_key: str) -> dict:
    """S3バケットから前回の実行状態を取得する。存在しない場合は空の辞書を返す"""
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    except s3_client.exceptions.NoSuchKey:
        return {}
    return json.loads(response["Body"].read().decode("utf-8"))


def parse_last_run_time(state: dict) -> datetime:
    """実行状態から前回の実行時間を取り出す。未実行の場合は最小の日時を返す"""
    if "last_run_time" not in state:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.fromisoformat(state["last_run_time"])


def get_last_run_time(s3_client: Any, bucket_name: str, object_key: str) -> datetime:
    """S3バケットから前回の実行時間を取得する

    Args:
        s3_client (boto3.Session): S3クライアント
        bucket_name (str): S3バケット名
        object_key (str): S3オブジェクトキー

    Returns:
        datetime: 前回の実行時間
    """
    return parse_last_run_time(load_state(s3_client, bucket_name, object_key))


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
    if not text:
        return ""
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
                            "Slack にそのまま表示するため、各行は「• 」で始め、"
                            "見出しや太字などの Markdown 記法は使わないでください。"
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


def simple_post_to_slack(
    webhook_url: str, text: str, note: str | None = None
) -> requests.Response:
    """Slackに簡易なテキストを投稿する関数

    Args:
        webhook_url (str): SlackのWebhook URL
        text (str): Url
        note (str | None): URLの下に添える一言

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
                    "text": f"Reference: <{text}>" + (f"\n{note}" if note else ""),
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
                "mrkdwn_in": ["text", "fields"],
                "color": "#36a64f",
                "pretext": comment,
                "title": title,
                "title_link": entry_url,
                "image_url": img_url,
                # XXX: はてぶのトレンドコメントを拾う
                "fields": [
                    {
                        "title": "要約",
                        "value": summary,
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
