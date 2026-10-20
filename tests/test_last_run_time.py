import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from FeedSummarizer.lambda_function import (
    OBJECT_KEY,
    REGION,
    get_last_run_time,
    set_last_run_time,
)


@pytest.fixture
def s3_client():
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        yield s3


def test_get_last_run_time_exists(s3_client):
    """ファイルが存在する場合のテスト
    最終実行時間が正しく取得できることを確認する
    """
    # バケットとオブジェクトをセットアップ
    s3_client.create_bucket(Bucket="exists-object")
    s3_client.put_object(
        Bucket="exists-object",
        Key=OBJECT_KEY,
        Body='{"last_run_time": "2021-09-01T00:00:00+00:00"}',
    )

    # 関数をテスト
    result = get_last_run_time(s3_client, "exists-object", OBJECT_KEY)
    expected = datetime(2021, 9, 1, tzinfo=UTC)
    assert result == expected


def test_get_last_run_time_not_exists(s3_client):
    s3_client.create_bucket(Bucket="not-exists-object")
    # ファイルが存在しない場合のテスト
    result = get_last_run_time(s3_client, "not-exists-object", OBJECT_KEY)
    assert result == datetime.min.replace(tzinfo=UTC)


def test_set_last_run_time_exists(s3_client):
    """ファイルが存在する場合のテスト
    最終実行時間が正しく取得できることを確認する
    """
    s3_client.create_bucket(
        Bucket="bucket-set",
    )
    # バケットとオブジェクトをセットアップ
    current_time_ = datetime.now(UTC)

    set_last_run_time(s3_client, "bucket-set", OBJECT_KEY, current_time_)

    response = s3_client.get_object(Bucket="bucket-set", Key=OBJECT_KEY)
    saved_time = json.loads(response["Body"].read().decode("utf-8"))["last_run_time"]

    # 関数をテスト
    assert saved_time == current_time_.isoformat()


def test_set_last_run_time_with_recent_urls(s3_client):
    s3_client.create_bucket(Bucket="bucket-urls")
    current_time_ = datetime(2021, 9, 1, tzinfo=UTC)

    set_last_run_time(
        s3_client, "bucket-urls", OBJECT_KEY, current_time_, ["https://example.com"]
    )

    response = s3_client.get_object(Bucket="bucket-urls", Key=OBJECT_KEY)
    assert json.loads(response["Body"].read()) == {
        "last_run_time": "2021-09-01T00:00:00+00:00",
        "recent_urls": ["https://example.com"],
    }
    # 以前の形式と同じく日時を読める
    assert get_last_run_time(s3_client, "bucket-urls", OBJECT_KEY) == current_time_
