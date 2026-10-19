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

    with pytest.raises(requests.HTTPError):
        lambda_function.lambda_handler(None, None)

    # Slackへの投稿に失敗した場合は次回再送するため実行時間を進めない
    with pytest.raises(s3_client.exceptions.NoSuchKey):
        s3_client.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)


@pytest.fixture
def mock_three_entries(monkeypatch):
    # RSS は新しい順に並ぶ
    entries = [
        feedparser.FeedParserDict(
            title=f"Article{i}",
            link=f"https://example.com/{i}",
            date=f"2021-09-1{i}T12:00:00+00:00",
        )
        for i in (3, 2, 1)
    ]
    feed = feedparser.FeedParserDict(entries=entries)
    monkeypatch.setattr(lambda_function, "fetch_feed", lambda _url: feed)
    monkeypatch.setattr(lambda_function, "SLACK_WEBHOOK_URL", WEBHOOK_URL)
    posted = []
    monkeypatch.setattr(lambda_function, "post_entry", lambda e: posted.append(e.link))
    return posted


def _saved_time(s3_client):
    body = s3_client.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)["Body"].read()
    return json.loads(body)["last_run_time"]


def test_lambda_handler_processes_entries_oldest_first(
    s3_client, mock_three_entries, monkeypatch
):
    monkeypatch.setattr(lambda_function, "MAX_ENTRIES_PER_RUN", 2)

    result = lambda_function.lambda_handler(None, None)

    assert result["body"] == "Processed 2 entries."
    assert mock_three_entries == ["https://example.com/1", "https://example.com/2"]
    assert _saved_time(s3_client) == "2021-09-12T12:00:00+00:00"

    # 次回は続きから処理する
    lambda_function.lambda_handler(None, None)
    assert mock_three_entries[-1] == "https://example.com/3"
    assert _saved_time(s3_client) == "2021-09-13T12:00:00+00:00"


class _FakeContext:
    def __init__(self, remaining):
        self.remaining = list(remaining)

    def get_remaining_time_in_millis(self):
        return self.remaining.pop(0)


def test_lambda_handler_stops_when_time_is_short(s3_client, mock_three_entries):
    # 1件目は必ず処理し、2件目の前に残り時間が足りなくなる
    context = _FakeContext([10_000])

    result = lambda_function.lambda_handler(None, context)

    assert result["body"] == "Processed 1 entries."
    assert mock_three_entries == ["https://example.com/1"]
    assert _saved_time(s3_client) == "2021-09-11T12:00:00+00:00"
