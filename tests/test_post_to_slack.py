import responses

from feed_summarizer.lambda_function import post_to_slack


# テストケース
@responses.activate
def test_post_to_slack():
    webhook_url = (
        "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
    )
    message = "Hello, world!"
    responses.add(responses.POST, webhook_url, json={"status": "ok"}, status=200)

    # 関数を実行
    response = post_to_slack(webhook_url, message)

    # 結果の検証
    assert response.status_code == 200
    assert responses.calls[0].request.body == b'{"text": "Hello, world!"}'
    assert len(responses.calls) == 1, "Should have made one HTTP call to Slack"
