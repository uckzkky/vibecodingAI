"""打开公开网页并抽出正文。视频站只保留链接。"""

from __future__ import annotations

from bs4 import BeautifulSoup

from skills.common import assert_public_url, get_client, host_kind, is_reference_host, tool_error
from skills.schemas import FetchPageArgs

_cache: dict[str, dict] = {}


def fetch_page(url: str, max_chars: int = 6000) -> dict:
    args = FetchPageArgs(url=url, max_chars=max_chars)
    try:
        public = assert_public_url(args.url)
    except ValueError as exc:
        return tool_error(str(exc), url=args.url)

    cached = _cache.get(public)
    if cached is not None:
        return cached

    if is_reference_host(public):
        return tool_error("不抓百科和豆瓣", url=public)

    kind = host_kind(public)
    if kind == "video":
        result = {
            "ok": True,
            "url": public,
            "title": "",
            "text": "",
            "truncated": False,
            "note": "视频页面只保留链接，不抓正文",
        }
        _cache[public] = result
        return result
    if kind == "search":
        return tool_error("不抓搜索引擎结果页", url=public)

    try:
        response = get_client().get(public)
        response.raise_for_status()
    except Exception as exc:
        return tool_error(f"{type(exc).__name__}: {exc}", url=public)

    content_type = response.headers.get("content-type", "")
    if any(token in content_type for token in ("pdf", "image/", "audio/", "video/")):
        result = {
            "ok": True,
            "url": str(response.url),
            "title": "",
            "text": "",
            "truncated": False,
            "note": f"不是 HTML 正文（{content_type.split(';')[0]}）",
        }
        _cache[public] = result
        return result

    raw = response.content[:1_000_000]
    title, text = _extract(raw, response.encoding or "utf-8")
    truncated = len(text) > args.max_chars
    result = {
        "ok": True,
        "url": str(response.url),
        "title": title,
        "text": text[: args.max_chars],
        "truncated": truncated,
    }
    _cache[public] = result
    return result


def _extract(raw: bytes, encoding: str) -> tuple[str, str]:
    head = raw[:1000].lower()
    if b"charset=gb" in head:
        encoding = "gb18030"
    html = raw.decode(encoding, errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description = ""
    for key in ("og:description", "description"):
        node = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if node and node.get("content"):
            description = " ".join(node["content"].split())
            break
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "header", "footer", "aside"]):
        tag.decompose()
    root = None
    for selector in (
        ".tralbum-about",
        "#trackInfo",
        ".review_text",
        "article",
        "main",
        "[role=main]",
        "#content",
    ):
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(strip=True)) >= 80:
            root = candidate
            break
    root = root or soup.body or soup
    lines = [line.strip() for line in root.get_text("\n").splitlines()]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(line)
        blank = False
    text = "\n".join(cleaned).strip()
    head = description.replace("...", "").replace("…", "").strip()
    if head and len(head) >= 40 and head[:40] not in text:
        text = description + "\n\n" + text
    if _looks_blocked(text):
        return " ".join(title.split()), ""
    return " ".join(title.split()), text


def _looks_blocked(text: str) -> bool:
    head = text[:600].lower()
    markers = ("captcha", "验证码", "请完成验证", "安全验证", "访问验证")
    return any(marker in head for marker in markers)
