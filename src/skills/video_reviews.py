"""影音测评：B 站 UP、豆瓣相关页、抖音检索摘要里的评歌视频。

这是博主解读，不是听众评论，也不是专业乐评。抖音通常读不到口播全文，只保留标题和摘要。
"""

from __future__ import annotations

import html
import re
import time
from urllib.parse import unquote, urlparse

import httpx

from skills.common import is_reference_host
from skills.schemas import VideoReviewsArgs
from skills.web_search import search_web

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
MUSIC_TYPES = ("音乐", "说唱", "演奏", "翻唱", "MV", "VOCALOID", "乐评", "专辑", "电音", "嘻哈")
MUSIC_TAGS = ("说唱", "音乐", "rap", "hiphop", "hip-hop", "嘻哈", "歌", "专辑", "乐评", "beat", "reaction")
REVIEW_CUES = (
    "乐评", "测评", "锐评", "解析", "解读", "听感", "reaction", "react", "盘点",
    "安利", "吐槽", "打分", "评分", "review", "essay", "深度", "拆解", "分析",
)
SKIP_CUES = ("完整版", "纯享", "无损", "全专循环", "一小时", "十小时", "bgm", "翻唱挑战")
INTERVIEW_CUES = ("专访", "访谈", "采访", "对话", "创作理念", "幕后")
MAX_BILI = 8
MAX_SNIPPET = 6


def find_video_reviews(title: str = "", artist: str = "", label: str = "") -> dict:
    args = VideoReviewsArgs(title=title, artist=artist, label=label)
    if not args.title and not args.artist:
        return _empty("没有歌手，也没有歌名。")
    items: list[dict] = []
    errors: list[str] = []
    try:
        items.extend(_bilibili(args.artist, args.title, args.label))
    except Exception as exc:
        errors.append(f"哔哩哔哩：{type(exc).__name__}")
    try:
        items.extend(_douban(args.artist, args.title))
    except Exception as exc:
        errors.append(f"豆瓣：{type(exc).__name__}")
    try:
        items.extend(_snippet_platform(args.artist, args.title, "抖音", "douyin.com"))
    except Exception as exc:
        errors.append(f"抖音：{type(exc).__name__}")
    items = _dedupe(items)[:14]
    findings = _findings(items)
    note = "影音测评来自视频标题、简介或检索摘要，不是口播全文，也不算专业乐评。"
    if any(item["platform"] == "抖音" for item in items):
        note += "抖音条目通常读不到口播，只作线索。"
    if errors and not items:
        note = "；".join(errors)
    return {
        "ok": True,
        "found": bool(items),
        "items": items,
        "findings": findings,
        "note": note if items else (note if not errors else "；".join(errors)),
        "errors": errors,
    }


def _bilibili(artist: str, title: str, label: str = "") -> list[dict]:
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.bilibili.com/"}
    subject = (artist or title).casefold()
    keyword = f"{artist} {title}".strip()
    queries = [keyword]
    if label and (artist or title):
        queries.insert(0, f"{label} {artist or title}")
    if artist:
        queries.append(f"{artist} 乐评")
        queries.append(f"{artist} 测评")
    if title and artist:
        queries.append(f"{artist} {title} 解析")
    seen: set[str] = set()
    picked: list[dict] = []
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        client.get("https://www.bilibili.com/")
        for query in queries:
            if len(picked) >= MAX_BILI:
                break
            payload = client.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={"search_type": "video", "keyword": query, "page": 1},
            ).json()
            rows = (payload.get("data") or {}).get("result") or []
            for row in rows:
                bvid = row.get("bvid") or ""
                if not bvid or bvid in seen:
                    continue
                name = _plain(row.get("title") or "")
                author = (row.get("author") or "").strip()
                tags = (row.get("tag") or "").casefold()
                desc = _plain(row.get("description") or "")
                blob = f"{name} {tags} {author} {desc}".casefold()
                if subject not in blob:
                    continue
                kind = row.get("typename") or ""
                if not any(word in kind for word in MUSIC_TYPES) and not any(word in tags for word in MUSIC_TAGS):
                    if not _looks_review(f"{name} {desc}"):
                        continue
                role = _role(f"{name} {desc} {tags}")
                if role == "skip":
                    continue
                if role == "other" and not _looks_review(f"{name} {desc} {tags}"):
                    continue
                url = f"https://www.bilibili.com/video/{bvid}"
                summary = desc
                if len(summary) < 24:
                    summary = _view_desc(client, bvid) or summary
                summary = _clip(summary or (row.get("tag") or ""), 160)
                if not summary and role != "interview":
                    continue
                seen.add(bvid)
                picked.append(
                    {
                        "platform": "哔哩哔哩",
                        "author": author or "UP主",
                        "title": name[:80],
                        "url": url,
                        "summary": summary,
                        "role": role,
                        "plays": int(row.get("play") or 0),
                        "kind": "video",
                    }
                )
                if len(picked) >= MAX_BILI:
                    break
            time.sleep(0.35)
    picked.sort(key=lambda item: (0 if item["role"] == "review" else 1, -item.get("plays") or 0))
    return picked


def _view_desc(client: httpx.Client, bvid: str) -> str:
    try:
        payload = client.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
        ).json()
    except (httpx.HTTPError, ValueError):
        return ""
    if payload.get("code") != 0:
        return ""
    return _plain((payload.get("data") or {}).get("desc") or "")


def _douban(artist: str, title: str) -> list[dict]:
    """豆瓣检索页直接抓标题和摘要。search_web 会丢掉豆瓣，所以不能走通用检索。"""
    from bs4 import BeautifulSoup

    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.douban.com/"}
    queries = [f"{artist} {title} 乐评".strip(), f"{artist} 测评".strip()]
    if title and artist:
        queries.append(f"{artist} {title} 解析")
    items: list[dict] = []
    seen: set[str] = set()
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        for query in queries:
            if len(items) >= MAX_SNIPPET:
                break
            time.sleep(0.6)
            page = client.get("https://www.douban.com/search", params={"q": query})
            soup = BeautifulSoup(page.text, "html.parser")
            for result in soup.select("div.result"):
                link = result.select_one("div.title a") or result.select_one("a")
                if link is None:
                    continue
                href = unquote(link.get("href") or "")
                matched = re.search(r"https?://[^&\s\"']+", href)
                url = matched.group(0) if matched else href
                if "douban.com" not in url or url in seen:
                    continue
                name = " ".join(link.get_text(" ").split())
                cast = " ".join(result.get_text(" ").split())
                blob = f"{name} {cast}"
                if artist and artist.casefold() not in blob.casefold():
                    continue
                role = _role(blob)
                if role == "skip":
                    continue
                if not _looks_review(blob) and "review" not in url and "annotation" not in url:
                    continue
                summary = ""
                content = result.select_one(".content") or result
                for node in content.select("p, span"):
                    text = " ".join(node.get_text(" ").split())
                    if len(text) > 20 and text != name:
                        summary = text
                        break
                seen.add(url)
                items.append(
                    {
                        "platform": "豆瓣",
                        "author": "豆瓣",
                        "title": name[:80] or url,
                        "url": url.split("&")[0],
                        "summary": _clip(summary, 160),
                        "role": "review" if role in {"other", "review"} else role,
                        "plays": 0,
                        "kind": "snippet",
                    }
                )
                if len(items) >= MAX_SNIPPET:
                    break
    return items


def _snippet_platform(artist: str, title: str, platform: str, site: str) -> list[dict]:
    subject = f"{artist} {title}".strip()
    queries = [
        f'{subject} 乐评 OR 测评 OR 解析 site:{site}',
        f'{subject} 听感 OR reaction site:{site}',
    ]
    if artist and not title:
        queries.append(f'"{artist}" 专辑 测评 OR 乐评 site:{site}')
    items: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        if len(items) >= MAX_SNIPPET:
            break
        found = search_web(query, max_results=5)
        for row in found.get("results") or []:
            url = row.get("url") or ""
            if not url or url in seen or is_reference_host(url):
                continue
            host = urlparse(url).netloc.lower()
            if site not in host:
                continue
            name = _plain(row.get("title") or "")
            snippet = _plain(row.get("snippet") or "")
            blob = f"{name} {snippet}"
            if artist and artist.casefold() not in blob.casefold():
                continue
            if title and title.casefold() not in blob.casefold() and artist:
                # 歌手检索允许只对上艺人
                pass
            elif title and not artist and title.casefold() not in blob.casefold():
                continue
            role = _role(blob)
            if role == "skip":
                continue
            if role == "other" and platform == "抖音" and not _looks_review(blob):
                continue
            if role == "other" and not _looks_review(blob):
                continue
            seen.add(url)
            items.append(
                {
                    "platform": platform,
                    "author": platform,
                    "title": name[:80] or url,
                    "url": url,
                    "summary": _clip(snippet, 160),
                    "role": "review" if role == "other" else role,
                    "plays": 0,
                    "kind": "snippet",
                }
            )
            if len(items) >= MAX_SNIPPET:
                break
    return items


def _role(blob: str) -> str:
    text = blob.casefold()
    if any(cue.casefold() in text for cue in INTERVIEW_CUES):
        return "interview"
    if any(cue.casefold() in text for cue in SKIP_CUES) and not _looks_review(blob):
        return "skip"
    if _looks_review(blob):
        return "review"
    return "other"


def _looks_review(blob: str) -> bool:
    text = blob.casefold()
    return any(cue.casefold() in text for cue in REVIEW_CUES)


def _findings(items: list[dict]) -> list[str]:
    if not items:
        return ["没有找到可核对的影音测评。"]
    by = {}
    for item in items:
        by.setdefault(item["platform"], 0)
        by[item["platform"]] += 1
    line = "、".join(f"{name} {count} 条" for name, count in by.items())
    lines = [f"影音测评共 {len(items)} 条（{line}）。"]
    reviews = [item for item in items if item.get("role") == "review"]
    interviews = [item for item in items if item.get("role") == "interview"]
    if reviews:
        sample = reviews[0]
        tip = sample["summary"] or sample["title"]
        lines.append(f"测评取向最多，例如 {sample['platform']}《{sample['title'][:24]}》：{tip[:60]}")
    if interviews:
        lines.append(f"其中 {len(interviews)} 条更像访谈或创作谈，可和「创作理念 / 采访」对照。")
    douyin = sum(1 for item in items if item["platform"] == "抖音")
    if douyin:
        lines.append(f"抖音 {douyin} 条只有标题或摘要，未读到口播全文。")
    return lines


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.get("url") or item.get("title") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _plain(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _empty(note: str) -> dict:
    return {"ok": True, "found": False, "items": [], "findings": [note], "note": note, "errors": []}
