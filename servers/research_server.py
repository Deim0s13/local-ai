"""Research MCP server: web search via local SearXNG, and page fetching.

Tools:
  web_search(query, max_results, time_range)  -> results from SearXNG on 127.0.0.1
  fetch_page(url, max_chars)                   -> readable text of a web page or PDF

No API keys. Search goes to your own SearXNG; pages are fetched directly.
"""
from __future__ import annotations

import io
import os
from urllib.parse import urlparse

import httpx
import trafilatura
from mcp.server import MCPServer

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
USER_AGENT = "Mozilla/5.0 (Macintosh; Apple silicon) local-ai-research/0.1"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

server = MCPServer(
    name="research",
        log_level="WARNING",
    instructions=(
        "Use web_search when the question needs current or missing information, "
        "then fetch_page on the most relevant results. Cite the URLs you used."
    ),
)


@server.tool()
def web_search(query: str, max_results: int = 8, time_range: str = "") -> list[dict]:
    """Search the web through the local SearXNG instance.

    Args:
        query: what to search for.
        max_results: how many results to return (1-20).
        time_range: optional recency filter: "day", "week", "month" or "year".
    """
    params = {"q": query, "format": "json", "safesearch": 0}
    if time_range in {"day", "week", "month", "year"}:
        params["time_range"] = time_range
    r = httpx.get(f"{SEARXNG_URL}/search", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for item in r.json().get("results", [])[: max(1, min(max_results, 20))]:
        out.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "published": item.get("publishedDate") or "",
                "engine": item.get("engine", ""),
            }
        )
    return out


@server.tool()
def fetch_page(url: str, max_chars: int = 12000) -> dict:
    """Fetch a web page (or PDF) and return its main readable text.

    Args:
        url: an http or https URL.
        max_chars: cut the text to this many characters.
    """
    if urlparse(url).scheme not in {"http", "https"}:
        return {"url": url, "error": "only http and https URLs are allowed"}
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        from markitdown import MarkItDown

        text = MarkItDown().convert_stream(io.BytesIO(r.content), file_extension=".pdf").text_content
        title = ""
    else:
        text = trafilatura.extract(r.text, url=str(r.url), include_links=False, favor_recall=True) or ""
        meta = trafilatura.extract_metadata(r.text)
        title = (meta.title if meta else "") or ""
    text = text.strip()
    return {
        "url": str(r.url),
        "title": title,
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


if __name__ == "__main__":
    server.run("stdio")
