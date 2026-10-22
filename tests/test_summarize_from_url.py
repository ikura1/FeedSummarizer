import pymupdf
import pytest
import responses
from bs4 import BeautifulSoup

from FeedSummarizer import lambda_function
from FeedSummarizer.lambda_function import (
    extract_ogp_image,
    extract_text_from_pdf,
    summarize_from_url,
    summarize_text,
)

URL = "https://example.com/article"


@pytest.fixture
def fake_summarize(monkeypatch):
    received = {}

    def _summarize(title, text):
        received["text"] = text
        return "summary"

    monkeypatch.setattr(lambda_function, "summarize_text", _summarize)
    # Reader API は使わずHTMLから本文を抽出させる
    monkeypatch.setattr(lambda_function, "get_llm_friendly_text", lambda _url: None)
    return received


@responses.activate
def test_summarize_from_url_does_not_summarize_error_page(fake_summarize):
    responses.add(responses.GET, URL, status=404, body="Not Found")

    # Reader API でも取れなければ、エラーページではなく空の本文で要約に回す
    summarize_from_url("title", URL)
    assert fake_summarize["text"] == ""


@responses.activate
def test_summarize_from_url_uses_reader_when_direct_fetch_blocked(
    fake_summarize, monkeypatch
):
    # AWS からの直接アクセスが 403 でも Reader API の本文で要約する
    responses.add(responses.GET, URL, status=403, body="Forbidden")
    monkeypatch.setattr(
        lambda_function, "get_llm_friendly_text", lambda _url: "reader text"
    )

    result = summarize_from_url("title", URL)

    assert fake_summarize["text"] == "reader text"
    assert result["img_url"] is None


@responses.activate
def test_summarize_from_url_decodes_japanese_without_charset(fake_summarize):
    html = (
        "<html><head><title>t</title></head>"
        "<body><p>日本語の本文です。" + "あ" * 200 + "</p></body></html>"
    )
    responses.add(
        responses.GET,
        URL,
        body=html.encode("utf-8"),
        content_type="text/html",
    )

    summarize_from_url("title", URL)

    assert "日本語の本文です" in fake_summarize["text"]


@responses.activate
def test_summarize_from_url_pdf_with_params(fake_summarize):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Hello PDF")
    responses.add(
        responses.GET,
        URL,
        body=doc.tobytes(),
        content_type="application/pdf; qs=0.001",
    )

    result = summarize_from_url("title", URL)

    assert "Hello PDF" in fake_summarize["text"]
    assert result["img_url"] is None


def test_extract_text_from_pdf():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Page1")
    doc.new_page().insert_text((72, 72), "Page2")

    text = extract_text_from_pdf(doc.tobytes())

    assert "Page1" in text
    assert "Page2" in text


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("https://cdn.example.com/a.png", "https://cdn.example.com/a.png"),
        ("/images/a.png", "https://example.com/images/a.png"),
    ],
)
def test_extract_ogp_image(content, expected):
    soup = BeautifulSoup(
        f'<meta property="og:image" content="{content}">', "html.parser"
    )
    assert extract_ogp_image(soup, URL) == expected


def test_extract_ogp_image_missing():
    soup = BeautifulSoup("<html></html>", "html.parser")
    assert extract_ogp_image(soup, URL) is None


def test_summarize_text_raises_on_empty_text():
    # 空の要約を投稿しないよう例外にしてURLのみの投稿に回す
    with pytest.raises(ValueError):
        summarize_text("title", "   ")


@responses.activate
def test_summarize_from_url_skips_too_large_response(fake_summarize, monkeypatch):
    # 巨大なファイルは直接取得せず、Reader API の本文で要約する
    monkeypatch.setattr(lambda_function, "MAX_DOWNLOAD_BYTES", 10)
    monkeypatch.setattr(
        lambda_function, "get_llm_friendly_text", lambda _url: "reader text"
    )
    responses.add(responses.GET, URL, body=b"x" * 100, content_type="text/html")

    summarize_from_url("title", URL)

    assert fake_summarize["text"] == "reader text"
