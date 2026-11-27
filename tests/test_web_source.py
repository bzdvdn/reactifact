"""WebSource: lazy fetching, keyword asearch, resolve→text (hermetic)."""

import asyncio

import httpx
from reactifact.sources import WebSource

PAGES = {
    "https://example.org/infra": (
        "<html><title>Infra costs</title><body><script>bad()</script>"
        "<h1>GPU inference</h1><p>gpu inference cost grew in Q2 &amp; Q3.</p></body></html>"
    ),
    "https://example.org/auth": (
        "<html><body><h1>Authentication</h1><p>we use tokens and sso</p></body></html>"
    ),
}


def build(urls=None):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=PAGES[str(req.url)])
    )
    return WebSource(urls=urls or list(PAGES), transport=transport)


def test_asearch_ranks_by_query_and_excerpt():
    src = build()
    refs = asyncio.run(src.asearch("how much does gpu inference cost?"))
    assert any(r.locator == "https://example.org/infra" for r in refs)
    assert refs[0].locator == "https://example.org/infra"  # top match first
    assert refs[0].score > 0


def test_resolve_returns_cleaned_text():
    src = build()
    refs = asyncio.run(src.asearch("gpu inference"))
    assert refs, "expected at least one match"
    text = asyncio.run(src.resolve(refs[0]))
    # scripts stripped, tags stripped, entities unescaped
    assert "bad()" not in text
    assert "GPU inference" in text
    assert "&" in text


def test_no_fetch_until_needed():
    called = {"n": 0, "urls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        called["urls"].append(str(request.url))
        return httpx.Response(200, text=PAGES[str(request.url)])

    src = WebSource(urls=list(PAGES), transport=httpx.MockTransport(handler))
    assert called["n"] == 0  # registration alone never fetches
    refs = asyncio.run(src.asearch("gpu"))
    assert called["n"] == 2  # lazy: both pages fetched on first live search
    # search() over cache needs no network
    again = src.search("gpu")
    assert [r.locator for r in again] == [r.locator for r in refs]


def test_fetch_sends_the_user_agent_header():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user-agent"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text=PAGES[str(request.url)])

    src = WebSource(urls=list(PAGES), transport=httpx.MockTransport(handler))
    asyncio.run(src.asearch("gpu"))
    assert seen["user-agent"] == WebSource.DEFAULT_UA


def test_html_to_text_strips_markup():
    from reactifact.sources import _html_to_text

    assert "hello" in _html_to_text(
        "<html><body><p>hello</p><script>x()</script></body></html>"
    )
    assert "x()" not in _html_to_text(
        "<html><body><script>x()</script>fine</body></html>"
    )


def test_html_to_text_strips_boilerplate_landmarks():
    """A page's nav/header/footer chrome must not outrank real content in the
    naive `[:200]`-style offline excerpt (real bug: a Wikipedia-shaped page's
    <nav> menu was landing ahead of the article text)."""
    from reactifact.sources import _html_to_text

    html = (
        "<html><body>"
        "<header>Site Header <nav>Main menu Contents Current events</nav></header>"
        "<!-- a comment full of noise -->"
        "<article>The article's real content starts here.</article>"
        "<aside>Related links sidebar</aside>"
        "<footer>Copyright footer text</footer>"
        "</body></html>"
    )
    text = _html_to_text(html)
    assert "The article's real content starts here." in text
    assert "Main menu" not in text
    assert "Related links sidebar" not in text
    assert "Copyright footer text" not in text
    assert "noise" not in text
