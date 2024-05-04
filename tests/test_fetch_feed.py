from FeedSummarizer.lambda_function import fetch_feed


def test_fetch_feed():
    rss_xml = """
    <?xml version="1.0" encoding="UTF-8"?>
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
        xmlns:admin="http://webns.net/mvcb/" xmlns:content="http://purl.org/rss/1.0/modules/content/"
        xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:hatena="http://www.hatena.ne.jp/info/xmlns#"
        xmlns:syn="http://purl.org/rss/1.0/modules/syndication/" xmlns:taxo="http://purl.org/rss/1.0/modules/taxonomy/">
        <channel rdf:about="https://b.hatena.ne.jp/sinnra0/bookmark">
            <title>HatenaBookmark</title>
            <link>https://b.hatena.ne.jp/sinnra0/bookmark</link>
            <description>HatenaBookmark</description>
            <items>
                <rdf:Seq>
                    <rdf:li rdf:resource="https://b.hatena.ne.jp/sinnra0/20240428#bookmark-18625960"/>
                </rdf:Seq>
            </items>
        </channel>
        <item rdf:about="https://b.hatena.ne.jp/sinnra0/20240428#bookmark-18625960">
            <title>Google</title>
            <link>https://www.google.com/</link>
            <description/>
            <dc:creator>sinnra0</dc:creator>
            <dc:date>2024-04-28T09:56:06Z</dc:date>
            <hatena:bookmarkcount>1509</hatena:bookmarkcount>
            <content:encoded><blockquote cite="https://www.google.com/" title="Google"><cite><img src="https://cdn-ak2.favicon.st-hatena.com/64?url=https%3A%2F%2Fwww.google.com%2F" alt="" /> <a href="https://www.google.com/">Google</a></cite><p><a href="https://www.google.com/"><img src="https://cdn-ak-scissors.b.st-hatena.com/image/square/3483603de9195add0e730d417656b4274cfc09dc/height=90;version=1;width=120/https%3A%2F%2Fwww.google.com%2Flogos%2Fdoodles%2F2024%2Fvalentines-day-2024-6753651837110186-2xa.gif" alt="Google" title="Google" class="entry-image" /></a></p><p><a href="https://b.hatena.ne.jp/entry/s/www.google.com/"><a href="https://b.hatena.ne.jp/entry/s/www.google.com/"></blockquote></content:encoded>
        </item>
    </rdf:RDF>
    """
    # urlでテストするのが難しいので、textでテストする
    feed = fetch_feed(rss_xml)
    assert feed["feed"]["title"] == "HatenaBookmark"
    assert len(feed["entries"]) == 1
    assert feed["entries"][0]["title"] == "Google"
