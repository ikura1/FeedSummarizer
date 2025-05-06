import json
import os
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile, TemporaryFile

import boto3
import feedparser
import openai
import pymupdf
import requests
from bs4 import BeautifulSoup
from extractcontent3 import ExtractContent
from openai import OpenAI
from pypdf import PdfReader

BUCKET_NAME = "feedsummarizer"
OBJECT_KEY = "last_run_time.json"
REGION = "us-east-1"
REQUEST_TIMEOUT = 5
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
MAX_TOKEN_LENGTH = 15000
MAX_RETRIES = 3
DECREMENT_TOKEN = 1000

s3 = boto3.client("s3")

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
    if any([entry.link.startswith(domain) for domain in SKIP_DOMAINS]):
        simple_post_to_slack(SLACK_WEBHOOK_URL, entry.link)
    else:
        summary = summarize_from_url(entry.title, entry.link)

        # Slackに通知を送信
        post_to_slack(
            SLACK_WEBHOOK_URL,
            entry.link,
            entry.title,
            summary.get("text"),
            entry.description,
            summary.get("img_url"),
        )

    # 最後に共有した日時を実行時間としてS3に保存
    entry_time = datetime.fromisoformat(entry.date)
    set_last_run_time(s3_client, BUCKET_NAME, OBJECT_KEY, entry_time)

    # 処理結果を返す
    return {"statusCode": 200, "body": "Processed 1 entries."}


def summarize_from_url(title: str, url: str):
    """urlを要約"""
    res = requests.get(url, timeout=REQUEST_TIMEOUT)
    img_url = None

    if res.headers.get("Content-Type") == "application/pdf":
        with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            file_path = tmp_file.name
            with open(file_path, "wb") as f:
                f.write(res.content)
            entry_text = extract_text_from_pdf(file_path)

    else:
        soup = BeautifulSoup(res.text, "html.parser")
        img_url = extract_ogp_image(soup)
        entry_text = get_content_text(url, res.text)
    return {"text": summarize_text(title, entry_text), "image": img_url}


def set_last_run_time(
    s3_client: boto3.Session,
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
    """RSSフィードを取得するヘルパー関数"""
    return feedparser.parse(feed_url)


def filter_feed_entry(feed, last_run_time: datetime):
    """最終実行時間以降の記事をフィルタリングするヘルパー関数"""
    # 各記事のタイトルとリンクを出力
    for entry in reversed(feed.entries):
        # RSSフィードの登録日時をdatetimeオブジェクトに変換
        entry_date = datetime.fromisoformat(entry.date)

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


def extract_ogp_image(soup):
    """OGP画像を抽出するヘルパー関数"""
    og_image_elems = soup.select('[property="og:image"]')
    for og_image_elem in og_image_elems:
        return og_image_elem.get("content")
    return None


def summarize_text(title: str, text: str) -> str:
    """テキストを要約する関数"""
    client = OpenAI()
    for _ in range(MAX_RETRIES):
        try:
            # OpenAI APIを使用してテキストの要約を実行
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "assistant", "content": f"【Title】{title}\n###\n{text}"},
                    {
                        "role": "user",
                        "content": """
                        この記事の内容について、技術的な視点で要約をしてください。
                        3点の箇条書きでお願いします。
                        lang:ja
                        """,
                    },
                ],
            )
        except openai.BadRequestError as e:
            print(e)
            text_length = min(len(text), MAX_TOKEN_LENGTH)
            text = text[: text_length - DECREMENT_TOKEN]
    if len(response.choices) < 1:
        return ""
    return response.choices[0].message.content


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


def extract_text_from_pdf(file_path: str):
    """PDFファイルからテキストを抽出する"""

    doc = pymupdf.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    return text


def extract_text_from_pypdf(binary):
    """PDFファイルからテキストを抽出する"""
    with TemporaryFile(buffering=0) as f:
        f.write(binary)
        f.seek(0)
        pdf = PdfReader(f)
        text = ""
        page_length = len(pdf.pages)
        for page_num in range(page_length):
            page = pdf.pages[page_num]
            text += page.extract_text()
        return text


def get_llm_friendly_text(url: str) -> str:
    """https://jina.ai/reader/ APIを使用して、URLからテキストを取得する

    Args:
        url (str): 要約するURL

    Returns:
        str: URLから取得したテキスト
    """
    try:
        res = requests.get(f"https://r.jina.ai/{url}", timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        return None
    if res.status_code != 200:
        return None
    return res.text
