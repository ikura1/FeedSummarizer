from datetime import datetime
from types import SimpleNamespace

import pytest

from FeedSummarizer.lambda_function import filter_feed_entry


# モックフィードデータの準備
@pytest.fixture
def mock_feed():
    return SimpleNamespace(
        entries=[
            SimpleNamespace(
                title="Old Article",
                link="https://example.com/old",
                date="2021-09-01T10:00:00Z",
            ),
            SimpleNamespace(
                title="New Article",
                link="https://example.com/new",
                date="2021-09-15T12:00:00Z",
            ),
        ]
    )


# テスト関数
def test_filter_feed_entries(mock_feed):
    # テスト用の最終実行時間設定
    last_run_time = datetime.fromisoformat("2021-09-10T12:00:00Z")

    # 関数実行
    result = filter_feed_entry(mock_feed, last_run_time)

    # 結果の検証
    assert result.title == "New Article"  # 新しい記事のみがフィルタされる


def test_filter_feed_entries_skip_invalid_date():
    feed = SimpleNamespace(
        entries=[
            SimpleNamespace(
                title="Valid Article",
                link="https://example.com/valid",
                date="2021-09-15T12:00:00Z",
            ),
            SimpleNamespace(
                title="Invalid Article",
                link="https://example.com/invalid",
                date="Wed, 15 Sep 2021 12:00:00 GMT",
                get=lambda key: "https://example.com/invalid",
            ),
        ]
    )
    last_run_time = datetime.fromisoformat("2021-09-10T12:00:00Z")

    result = filter_feed_entry(feed, last_run_time)

    assert result.title == "Valid Article"
