import feedparser

# RSSフィードのURLを設定
url = "https://b.hatena.ne.jp/sinnra0/bookmark.rss"

# フィードを読み込む
feed = feedparser.parse(url)

# 各記事のタイトルとリンクを出力
for entry in feed.entries:
    print(f"Title: {entry.title}")
    print(f"Link: {entry.link}")
    print("---")
