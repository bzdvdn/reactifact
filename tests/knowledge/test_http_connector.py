from __future__ import annotations

import asyncio

import httpx
import pytest
from reactifact.knowledge import (
    ConfigError,
    HashEmbedder,
    HttpConnector,
    KnowledgePipeline,
    MemoryIndex,
    SourceError,
)

A = "https://example.test/a.md"
B = "https://example.test/b.md"
SITEMAP = "https://example.test/sitemap.xml"
CHILD = "https://example.test/child.xml"

SITEMAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.test/a.md</loc></url>
  <url><loc>https://example.test/b.md</loc></url>
</urlset>
"""

INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.test/child.xml</loc></sitemap>
</sitemapindex>
"""

CHILD_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.test/a.md</loc></url>
</urlset>
"""


def _transport(
    *,
    heads: dict[str, httpx.Response] | None = None,
    gets: dict[str, httpx.Response] | None = None,
    calls: list[tuple[str, str]] | None = None,
) -> httpx.MockTransport:
    head_map = heads or {}
    get_map = gets or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, str(request.url)))
        if request.method == "HEAD":
            return head_map.get(
                str(request.url),
                httpx.Response(
                    200,
                    headers={
                        "etag": '"v1"',
                        "content-type": "text/markdown",
                    },
                ),
            )
        return get_map.get(str(request.url), httpx.Response(404))

    return httpx.MockTransport(handler)


def test_http_connector_lists_and_fetches_selected_urls() -> None:
    calls: list[tuple[str, str]] = []
    connector = HttpConnector(
        [A, B],
        source_id="web",
        transport=_transport(
            heads={
                A: httpx.Response(
                    200,
                    headers={"etag": '"a1"', "content-type": "text/markdown"},
                ),
                B: httpx.Response(
                    200,
                    headers={
                        "last-modified": "Wed, 21 Oct 2026 07:28:00 GMT",
                        "content-type": "text/html",
                    },
                ),
            },
            gets={
                A: httpx.Response(
                    200,
                    headers={"content-type": "text/markdown; charset=utf-8"},
                    content=b"# A\n\nrefund policy",
                ),
            },
            calls=calls,
        ),
    )

    metas = asyncio.run(connector.list_documents())
    assert [meta.uri for meta in metas] == [A, B]
    assert metas[0].fingerprint == '"a1"'
    assert metas[0].content_type == "text/markdown"
    assert metas[1].fingerprint.startswith("Wed, 21 Oct")

    documents = asyncio.run(connector.fetch(uris={A}))
    assert [document.uri for document in documents] == [A]
    assert documents[0].content_type == "text/markdown"
    assert documents[0].metadata["sourceUrl"] == A
    assert not any(method == "GET" and url == B for method, url in calls)

    asyncio.run(connector.aclose())


def test_http_connector_reads_a_sitemap_and_index() -> None:
    gets = {
        SITEMAP: httpx.Response(200, content=SITEMAP_XML),
        A: httpx.Response(200, content=b"# A"),
        B: httpx.Response(200, content=b"# B"),
    }
    connector = HttpConnector(
        sitemap=SITEMAP,
        transport=_transport(gets=gets),
    )
    metas = asyncio.run(connector.list_documents())
    assert [meta.uri for meta in metas] == [A, B]

    index_connector = HttpConnector(
        sitemap="https://example.test/index.xml",
        transport=_transport(
            gets={
                "https://example.test/index.xml": httpx.Response(
                    200, content=INDEX_XML
                ),
                CHILD: httpx.Response(200, content=CHILD_XML),
                A: httpx.Response(200, content=b"# A"),
            }
        ),
    )
    assert [meta.uri for meta in asyncio.run(index_connector.list_documents())] == [A]


def test_http_connector_incremental_run_skips_unchanged_urls(tmp_path) -> None:
    calls: list[tuple[str, str]] = []
    connector = HttpConnector(
        [A],
        transport=_transport(
            gets={A: httpx.Response(200, content=b"# A\n\nrefund policy")},
            calls=calls,
        ),
    )
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )
    state = f"{tmp_path}/state.json"

    first = asyncio.run(pipeline.run(incremental=True, state_path=state))
    second = asyncio.run(pipeline.run(incremental=True, state_path=state))

    assert first.documents_changed == 1
    assert second.documents_skipped == 1
    assert [call for call in calls if call[0] == "GET" and call[1] == A] == [("GET", A)]
    asyncio.run(connector.aclose())


def test_batched_ingest_fetches_one_batch_at_a_time(tmp_path) -> None:
    third = "https://example.test/c.md"
    calls: list[tuple[str, str]] = []
    connector = HttpConnector(
        [A, B, third],
        transport=_transport(
            gets={
                A: httpx.Response(200, content=b"# A\n\nrefund"),
                B: httpx.Response(200, content=b"# B\n\nrefund"),
                third: httpx.Response(200, content=b"# C\n\nrefund"),
            },
            calls=calls,
        ),
    )
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )
    state = f"{tmp_path}/state.json"

    first = asyncio.run(pipeline.run(incremental=True, state_path=state, batch_size=1))
    assert first.documents_ingested == 3
    assert len([call for call in calls if call[0] == "GET"]) == 3

    second = asyncio.run(pipeline.run(incremental=True, state_path=state, batch_size=1))
    assert second.documents_skipped == 3
    assert len([call for call in calls if call[0] == "GET"]) == 3
    asyncio.run(connector.aclose())


def test_batch_size_is_validated() -> None:
    pipeline = KnowledgePipeline(
        HttpConnector([A], transport=_transport()),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    with pytest.raises(ConfigError, match="batch_size"):
        asyncio.run(pipeline.run(batch_size=0))


def test_http_connector_surfaces_http_errors() -> None:
    connector = HttpConnector(
        [A],
        transport=_transport(
            heads={A: httpx.Response(503, text="down")},
        ),
    )
    with pytest.raises(SourceError, match="503"):
        asyncio.run(connector.list_documents())


def test_http_connector_requires_urls_or_sitemap() -> None:
    with pytest.raises(ValueError, match="urls=|sitemap="):
        HttpConnector()
