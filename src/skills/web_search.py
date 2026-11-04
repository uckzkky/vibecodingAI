"""公开网页检索：DuckDuckGo，失败时改走 Bing RSS。"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from skills.common import get_client, is_reference_result, tool_error, unwrap_result_url
from skills.schemas import SearchWebArgs

_cache: dict[tuple[str, int], dict] = {}
_last_call = 0.0


def search_web(query: str, max_results: int = 5) -> dict:
    args = SearchWebArgs(query=query, max_results=max_results)
    key = (args.query, args.max_results)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    errors: list[str] = []
    result: dict | None = None
    for name, fn in (("ddgs", _ddgs), ("duckduckgo", _ddg_lite), ("bing", _bing_rss)):
        _pace()
        try:
            rows = _clean(fn(args.query, args.max_results), args.max_results)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if rows:
            result = {
                "ok": True,
                "query": args.query,
                "backend": name,
                "results": rows,
            }
            break
        errors.append(f"{name}: empty")

    if result is None:
        result = tool_error(
            "检索没有结果",
            query=args.query,
            attempts=errors,
            results=[],
        )
    _cache[key] = result
    return result


def _pace() -> None:
    global _last_call
    wait = 0.6 - (time.monotonic() - _last_call)
    if _last_call and wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _clean(rows: list[dict], limit: int) -> list[dict]:
    cleaned = []
    seen: set[str] = set()
    for row in rows:
        url = unwrap_result_url(str(row.get("url") or "")).strip()
        title = " ".join(str(row.get("title") or "").split())
        if not url.startswith(("http://", "https://")) or url in seen or is_reference_result(title, url):
            continue
        seen.add(url)
        cleaned.append(
            {
                "title": title,
                "url": url,
                "snippet": " ".join(str(row.get("snippet") or "").split())[:400],
            }
        )
        if len(cleaned) >= limit:
            break
    return cleaned


def _ddgs(query: str, max_results: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    raw = DDGS().text(query, max_results=max_results)
    rows = []
    for item in raw or []:
        rows.append(
            {
                "title": item.get("title") or "",
                "url": item.get("href") or item.get("url") or "",
                "snippet": item.get("body") or item.get("snippet") or "",
            }
        )
    return rows


def _ddg_lite(query: str, max_results: int) -> list[dict]:
    response = get_client().post(
        "https://lite.duckduckgo.com/lite/",
        data={"q": query},
        headers={"Referer": "https://lite.duckduckgo.com/"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")
    rows = []
    for link, snippet in zip(links, snippets):
        rows.append(
            {
                "title": link.get_text(" ", strip=True),
                "url": link.get("href") or "",
                "snippet": snippet.get_text(" ", strip=True),
            }
        )
        if len(rows) >= max_results:
            break
    return rows


def _bing_rss(query: str, max_results: int) -> list[dict]:
    response = get_client().get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss"},
        headers={"Accept": "application/rss+xml, application/xml, text/xml"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    rows = []
    for item in root.iter("item"):
        snippet = item.findtext("description") or ""
        snippet = BeautifulSoup(snippet, "html.parser").get_text(" ", strip=True)
        rows.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "url": (item.findtext("link") or "").strip(),
                "snippet": snippet,
            }
        )
        if len(rows) >= max_results:
            break
    return rows
