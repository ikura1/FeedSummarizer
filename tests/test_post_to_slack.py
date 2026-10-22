import json

import responses
from inline_snapshot import snapshot

from FeedSummarizer.lambda_function import post_to_slack, simple_post_to_slack


# テストケース
@responses.activate
def test_post_to_slack():
    webhook_url = (
        "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
    )
    comment = "あとでよむ"
    title = "タイトル"
    entry_url = "https://example.com"
    image_url = "https://example.com/image.jpg"
    summary = "Hello, world!"
    responses.add(responses.POST, webhook_url, json={"status": "ok"}, status=200)

    # 関数を実行
    response = post_to_slack(webhook_url, entry_url, title, summary, comment, image_url)

    # 結果の検証
    assert response.status_code == 200
    assert responses.calls[0].request.body == snapshot(
        b'{"attachments": [{"mrkdwn_in": ["text", "fields"], "color": "#36a64f", "pretext": "\\u3042\\u3068\\u3067\\u3088\\u3080", "title": "\\u30bf\\u30a4\\u30c8\\u30eb", "title_link": "https://example.com", "image_url": "https://example.com/image.jpg", "fields": [{"title": "\\u8981\\u7d04", "value": "Hello, world!", "short": false}], "thumb_url": "https://example.com/image.jpg"}]}'
    )
    assert len(responses.calls) == 1, "Should have made one HTTP call to Slack"


@responses.activate
def test_simple_post_to_slack():
    webhook_url = (
        "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
    )
    text = "Hello, world!"
    responses.add(responses.POST, webhook_url, json={"status": "ok"}, status=200)

    # 関数を実行
    response = simple_post_to_slack(webhook_url, text)

    # 結果の検証
    assert response.status_code == 200
    assert responses.calls[0].request.body == snapshot(
        b'{"unfurl_links": true, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Reference: <Hello, world!>"}}]}'
    )
    assert len(responses.calls) == 1, "Should have made one HTTP call to Slack"


@responses.activate
def test_post_to_slack_escapes_control_sequences():
    webhook_url = "https://hooks.slack.com/services/T00000000/B00000000/XXXX"
    responses.add(responses.POST, webhook_url, status=200)

    # 記事のタイトルや要約に含まれる <!channel> でメンションが飛ばないようにする
    post_to_slack(
        webhook_url,
        "https://example.com",
        "<!channel> title",
        "• A & B <script>",
        "<@U123>",
        None,
    )

    body = json.loads(responses.calls[0].request.body)
    attachment = body["attachments"][0]
    assert attachment["title"] == "&lt;!channel&gt; title"
    assert attachment["pretext"] == "&lt;@U123&gt;"
    assert attachment["fields"][0]["value"] == "• A &amp; B &lt;script&gt;"
    # URL はそのままリンクとして使う
    assert attachment["title_link"] == "https://example.com"
