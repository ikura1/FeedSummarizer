from datetime import datetime, timezone
import json
import os

import boto3

s3 = boto3.client("s3")
BUCKET_NAME = "FeedSummarizer"
OBJECT_KEY = "last_run_time.json"


def lambda_handler(_event, _context):
    # 前回の実行時間をS3から取得
    last_run_time = get_last_run_time()
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


def get_last_run_time():
    try:
        response = s3.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY)
        last_run_time = json.loads(response["Body"].read().decode("utf-8"))[
            "last_run_time"
        ]

    except Exception as e:
        last_run_time = None
        print(f"Error retrieving the last run time: {str(e)}")
    return last_run_time
