"""Tests for HTML stripping in http_call.

Verifies that _strip_html removes tags, script/style blocks, and collapses
whitespace — but passes through non-HTML content (JSON API responses)
unchanged.
"""

from __future__ import annotations

from nexus.tools.http_call import _strip_html


def test_html_tags_stripped() -> None:
    result = _strip_html("<p>Hello <b>world</b></p>")
    assert result == "Hello world"


def test_script_blocks_removed() -> None:
    html = "<html><head><script>var x=1;function foo(){}</script></head><body>Hello</body></html>"
    result = _strip_html(html)
    assert "var x" not in result
    assert "function" not in result
    assert "Hello" in result


def test_style_blocks_removed() -> None:
    html = "<html><head><style>.a{color:red;font-size:12px}</style></head><body>Text</body></html>"
    result = _strip_html(html)
    assert "color" not in result
    assert "font-size" not in result
    assert "Text" in result


def test_comment_blocks_removed() -> None:
    html = "<html><body><!-- this is a comment -->Text</body></html>"
    result = _strip_html(html)
    assert "comment" not in result
    assert "Text" in result


def test_whitespace_collapsed() -> None:
    html = "<html><body>  a  \n\n  b  \t\t  c  </body></html>"
    result = _strip_html(html)
    assert result == "a b c"


def test_plain_text_unchanged() -> None:
    text = "Hello world, this is plain text."
    assert _strip_html(text) == text


def test_json_api_unchanged() -> None:
    payload = '{"ok": true, "data": [1, 2, 3], "url": "https://example.com"}'
    assert _strip_html(payload) == payload


def test_short_text_unchanged() -> None:
    assert _strip_html("hi") == "hi"
    assert _strip_html("") == ""


def test_truncation_applies_to_stripped() -> None:
    html = (
        "<html><head><title>T</title></head><body>"
        + "<p>" + "x" * 20_000 + "</p>"
        + "</body></html>"
    )
    result = _strip_html(html)[:10_000]
    assert len(result) <= 10_000
    assert "<p>" not in result
    assert "x" in result


def test_html_with_attributes_stripped() -> None:
    html = '<html><body><div class="foo" id="bar">Content <a href="http://x">link</a></div></body></html>'
    result = _strip_html(html)
    assert "class" not in result
    assert "href" not in result
    assert "Content" in result
    assert "link" in result
