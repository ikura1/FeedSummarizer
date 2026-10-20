import json

import boto3
import feedparser
import pytest
import requests
import responses
from moto import mock_aws

from FeedSummarizer import lambda_function
from FeedSummarizer.lambda_function import BUCKET_NAME, OBJECT_KEY, REGION

WEBHOOK_URL = "https://hooks.slack.com/services/T00000000/B00000000/XXXX"


@pytest.fixture
def s3_client(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET_NAME)
        yield s3


@pytest.fixture
def mock_feed(monkeypatch):
    entry = feedparser.FeedParserDict(
        title="Article",
        link="https://example.com/article",
        date="2021-09-15T12:00:00+00:00",
        description="あとでよむ",
    )
    feed = feedparser.FeedParserDict(entries=[entry])
    monkeypatch.setattr(lambda_function, "fetch_feed", lambda _url: feed)
    monkeypatch.setattr(lambda_function, "SLACK_WEBHOOK_URL", WEBHOOK_URL)


@responses.activate
def test_lambda_handler_fallback_when_summarize_fails(
    s3_client, mock_feed, monkeypatch
):
    def raise_timeout(_title, _url):
        raise TimeoutError("timeout")

    monkeypatch.setattr(lambda_function, "summarize_from_url", raise_timeout)
    responses.add(responses.POST, WEBHOOK_URL, status=200)

    result = lambda_function.lambda_handler(None, None)

    assert result["statusCode"] == 200
    # URLのみの簡易投稿にフォールバックする
    assert len(responses.calls) == 1
    assert (
        b"Reference: <https://example.com/article>" in responses.calls[0].request.body
    )
    # 要約に失敗したことが分かるよう一言添える
    assert (
        lambda_function.SUMMARY_FAILED_NOTE.encode("unicode_escape")
        in responses.calls[0].request.body
    )
    # 失敗した記事でも実行時間が進む
    body = s3_client.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)["Body"].read()
    assert json.loads(body) == {
        "last_run_time": "2021-09-15T12:00:00+00:00",
        "recent_urls": ["https://example.com/article"],
    }


@responses.activate
def test_lambda_handler_does_not_advance_when_slack_fails(
    s3_client, mock_feed, monkeypatch
):
    monkeypatch.setattr(
        lambda_function,
        "summarize_from_url",
        lambda _title, _url: {"text": "summary", "img_url": None},
    )
    responses.add(responses.POST, WEBHOOK_URL, status=500)

    with pytest.raises(requests.HTTPError):
        lambda_function.lambda_handler(None, None)

    # Slackへの投稿に失敗した場合は次回再送するため実行時間を進めない
    with pytest.raises(s3_client.exceptions.NoSuchKey):
        s3_client.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)


@responses.activate
def test_lambda_handler_skips_already_posted_url(s3_client, monkeypatch):
    # 同じURLを2回ブックマークした場合（RSS は新しい順）
    entries = [
        feedparser.FeedParserDict(
            title="New", link="https://example.com/new", date="2021-09-17T00:00:00Z"
        ),
        feedparser.FeedParserDict(
            title="Again",
            link="https://example.com/article",
            date="2021-09-16T00:00:00Z",
        ),
    ]
    feed = feedparser.FeedParserDict(entries=entries)
    monkeypatch.setattr(lambda_function, "fetch_feed", lambda _url: feed)
    monkeypatch.setattr(lambda_function, "SLACK_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setattr(
        lambda_function,
        "summarize_from_url",
        lambda _title, _url: {"text": "summary", "img_url": None},
    )
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=OBJECT_KEY,
        Body=json.dumps(
            {
                "last_run_time": "2021-09-15T00:00:00+00:00",
                "recent_urls": ["https://example.com/article"],
            }
        ),
    )
    responses.add(responses.POST, WEBHOOK_URL, status=200)

    lambda_function.lambda_handler(None, None)

    # 投稿済みのURLは飛ばし、次の記事を1件だけ投稿する
    assert len(responses.calls) == 1
    assert b"https://example.com/new" in responses.calls[0].request.body
    body = s3_client.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)["Body"].read()
    assert json.loads(body) == {
        "last_run_time": "2021-09-17T00:00:00+00:00",
        "recent_urls": ["https://example.com/article", "https://example.com/new"],
    }
