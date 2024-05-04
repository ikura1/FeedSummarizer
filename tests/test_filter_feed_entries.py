from datetime import datetime
from types import SimpleNamespace

import pytest

from FeedSummarizer.lambda_function import filter_feed_entries


# モックフィードデータの準備
@pytest.fixture
def mock_feed():
    return SimpleNamespace(
        **{
            "entries": [
                SimpleNamespace(
                    **{
                        "title": "Old Article",
                        "link": "https://example.com/old",
                        "date": "2021-09-01T10:00:00Z",
                    }
                ),
                SimpleNamespace(
                    **{
                        "title": "New Article",
                        "link": "https://example.com/new",
                        "date": "2021-09-15T12:00:00Z",
                    }
                ),
            ]
        }
    )


# テスト関数
def test_filter_feed_entries(mock_feed):
    # テスト用の最終実行時間設定
    last_run_time = datetime.fromisoformat("2021-09-10T12:00:00Z")

    # 関数実行
    result = filter_feed_entries(mock_feed, last_run_time)

    # 結果の検証
    assert len(result) == 1  # 1つのエントリが期待される
    assert result[0].title == "New Article"  # 新しい記事のみがフィルタされる
