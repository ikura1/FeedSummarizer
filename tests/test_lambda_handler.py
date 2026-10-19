import json

import boto3
import feedparser
import pytest
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
    # 失敗した記事でも実行時間が進む
    body = s3_client.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)["Body"].read()
    assert json.loads(body) == {"last_run_time": "2021-09-15T12:00:00+00:00"}


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

    with pytest.raises(Exception):
        lambda_function.lambda_handler(None, None)

    # Slackへの投稿に失敗した場合は次回再送するため実行時間を進めない
    with pytest.raises(s3_client.exceptions.NoSuchKey):
        s3_client.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)
