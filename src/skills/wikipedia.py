"""维基百科检索。只返回条目开头的纯文本。"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from skills.common import get_client, tool_error
from skills.schemas import WikipediaArgs


def _http_error(exc: httpx.HTTPStatusError, lang: str, query: str) -> dict:
    if exc.response.status_code == 403:
        return tool_error("维基百科拒绝了当前网络的访问", lang=lang, query=query)
    return tool_error(f"HTTP {exc.response.status_code}", lang=lang, query=query)


def search_wikipedia(query: str, lang: str = "zh") -> dict:
    args = WikipediaArgs(query=query, lang=lang)
    endpoint = f"https://{args.lang}.wikipedia.org/w/api.php"
    try:
        found = get_client().get(
            endpoint,
            params={
                "action": "query",
                "list": "search",
                "srsearch": args.query,
                "srlimit": 3,
                "format": "json",
                "utf8": 1,
            },
        )
        found.raise_for_status()
        hits = found.json().get("query", {}).get("search", [])
    except httpx.HTTPStatusError as exc:
        return _http_error(exc, args.lang, args.query)
    except Exception as exc:
        return tool_error(f"{type(exc).__name__}: {exc}", lang=args.lang, query=args.query)

    if not hits:
        return {
            "ok": True,
            "lang": args.lang,
            "query": args.query,
            "title": "",
            "url": "",
            "extract": "",
            "search_hits": [],
            "note": "没有条目",
        }

    title = hits[0]["title"]
    try:
        page = get_client().get(
            endpoint,
            params={
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "exchars": 3200,
                "redirects": 1,
                "titles": title,
                "format": "json",
            },
        )
        page.raise_for_status()
        pages = page.json().get("query", {}).get("pages", {})
        entry = next(iter(pages.values()))
    except httpx.HTTPStatusError as exc:
        return _http_error(exc, args.lang, args.query)
    except Exception as exc:
        return tool_error(f"{type(exc).__name__}: {exc}", lang=args.lang, query=args.query)

    wiki_title = entry.get("title") or title
    slug = quote(wiki_title.replace(" ", "_"), safe="()/:!")
    return {
        "ok": True,
        "lang": args.lang,
        "query": args.query,
        "title": wiki_title,
        "url": f"https://{args.lang}.wikipedia.org/wiki/{slug}",
        "extract": entry.get("extract") or "",
        "search_hits": [hit.get("title") or "" for hit in hits],
    }
