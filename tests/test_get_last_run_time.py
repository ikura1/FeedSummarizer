from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from feed_summarizer.consts import OBJECT_KEY, REGION
from feed_summarizer.lambda_function import get_last_run_time


@mock_aws
@pytest.fixture
def s3_client():
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
    expected = datetime(2021, 9, 1, tzinfo=timezone.utc)
    assert result == expected


def test_get_last_run_time_not_exists(s3_client):
    s3_client.create_bucket(Bucket="not-exists-object")
    # ファイルが存在しない場合のテスト
    result = get_last_run_time(s3_client, "not-exists-object", OBJECT_KEY)
    assert result == datetime.min.replace(tzinfo=timezone.utc)
