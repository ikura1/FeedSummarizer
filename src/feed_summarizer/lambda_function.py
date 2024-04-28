import json
from datetime import datetime, timezone

import boto3

from .consts import BUCKET_NAME, OBJECT_KEY

s3 = boto3.client("s3")


def lambda_handler(_event, _context):
    # 前回の実行時間をS3から取得
    last_run_time = get_last_run_time(s3, BUCKET_NAME, OBJECT_KEY)
    # post_feed(datetime.fromisoformat(last_run_time))

    # 現在の実行時間を保存
    current_time = datetime.now(timezone.utc).isoformat()
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=OBJECT_KEY,
        Body=json.dumps({"last_run_time": current_time}),
    )
    return {
        "statusCode": 200,
        "body": json.dumps(
            f"Last run time was: {last_run_time}, current time is: {current_time}"
        ),
    }


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
