"""把工具结果写成可读报告。模型综述和摘录共用来源附录。"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from skills.common import is_reference_host, topic_match

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def save_report(song: str, artist: str, body: str, bundle: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{stamp}_{_slug(artist)}_{_slug(song)}"
    path = OUTPUT_DIR / f"{stem}.md"
    context = OUTPUT_DIR / f"{stem}.context.json"
    path.write_text(body, encoding="utf-8")
    context.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def render_digest(bundle: dict, trace: list[dict]) -> str:
    song = bundle["song"]
    artist = bundle["artist"]
    lines = [
        f"# {_heading(song, artist)}",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "这次没有调用综述模型。顺序是：基本信息 → 听众怎么说 → 影音测评 → 创作理念 / 采访 → MV。乐评来自豆瓣长评、网易云长评和哔哩哔哩乐评博主，不查外国乐评机构。不使用百科，也不做采样。",
        "",
        "## 基本信息",
        "",
    ]
    if song and artist:
        lines.extend(
            [
                _musicbrainz_block(bundle.get("musicbrainz") or {}),
                "",
                _discogs_block(bundle.get("discogs") or {}),
                "",
            ]
        )
    else:
        if bundle.get("label") and artist:
            lines.extend([f"这次按厂牌「{bundle['label']}」的 {artist} 检索，不查录音目录。", ""])
        else:
            lines.extend(["这次按歌手或歌曲做乐评，不查录音目录。", ""])
    lines.extend([_cloud_block(bundle.get("cloud") or {}), ""])
    ai = bundle.get("ai_review") or {}
    if ai.get("text"):
        lines.extend(["", "## AI 乐评", "", ai["text"], ""])
    elif ai.get("note"):
        lines.extend(["", "## AI 乐评", "", ai["note"], ""])
    pages = bundle.get("pages") or []
    for section in bundle.get("sections") or []:
        section = {
            **section,
            "_song": bundle.get("song") or "",
            "_artist": bundle.get("artist") or "",
        }
        lines.append(_section_block(section, pages))
        lines.append("")
    lines.append(_tool_block(trace))
    return "\n".join(lines).rstrip() + "\n"


def render_appendix(bundle: dict, trace: list[dict]) -> str:
    lines = ["---", "", "## 检索附录", "", "这一节由编排循环附上，不经过模型改写。", ""]
    lines.append(_tool_block(trace))
    lines.append("")
    lines.append(_musicbrainz_block(bundle.get("musicbrainz") or {}))
    lines.append("")
    lines.append(_discogs_block(bundle.get("discogs") or {}))
    lines.append("")
    for section in bundle.get("sections") or []:
        lines.append(f"### {section['title']}")
        rows = section.get("results") or []
        if not rows:
            lines.append(f"- {section.get('note') or '未在检索结果中找到。'}")
        for row in rows:
            lines.append(f"- {_link(row.get('title') or row['url'], row['url'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def slim_context(bundle: dict, page_chars: int) -> dict:
    pages = []
    for page in bundle.get("pages") or []:
        pages.append(
            {
                "section": page.get("section"),
                "url": page.get("url"),
                "title": page.get("title"),
                "text": (page.get("text") or "")[:page_chars],
                "note": page.get("note"),
                "ok": page.get("ok"),
                "error": page.get("error"),
            }
        )
    sections = []
    for section in bundle.get("sections") or []:
        sections.append(
            {
                "id": section["id"],
                "title": section["title"],
                "results": section.get("results") or [],
            }
        )
    return {
        "song": bundle["song"],
        "artist": bundle["artist"],
        "musicbrainz": bundle.get("musicbrainz"),
        "discogs": bundle.get("discogs"),
        "criticism": bundle.get("criticism"),
        "sections": sections,
        "pages": pages,
    }


def _section_block(section: dict, pages: list[dict]) -> str:
    lines = [f"## {section['title']}", ""]
    rows = section.get("results") or []
    song = section.get("_song") or ""
    artist = section.get("_artist") or ""
    if section.get("analysis"):
        if not rows:
            return "\n".join(lines + [section.get("note") or "没有找到专业乐评。"])
        lines.append("下面按听感维度整理，只保留专业页面里实际出现的判断。")
        lines.append("")
        for row in rows:
            lines.append(f"- {row.get('title') or '乐评'}：{row.get('snippet') or ''}")
            if row.get("url"):
                lines.append(f"  （来源：{row['url']}）")
        return "\n".join(lines)
    if section.get("video") or section["id"] == "video":
        for line in section.get("findings") or []:
            lines.append(f"- {line}")
        if not rows:
            lines.append(section.get("note") or "没有找到影音测评。")
            return "\n".join(lines)
        lines.append("")
        for row in rows[:10]:
            title = row.get("title") or row.get("url") or "影音测评"
            lines.append(f"- {_link(title, row.get('url') or '')}")
            if row.get("snippet"):
                lines.append(f"  {row['snippet'][:200]}")
        if section.get("note"):
            lines.extend(["", section["note"]])
        return "\n".join(lines)
    related = []
    for page in pages:
        if not page.get("ok"):
            continue
        blob = (page.get("title") or "") + (page.get("text") or "")[:2500]
        if not topic_match(blob, song, artist, section["id"]):
            continue
        if _skipped_host(page.get("url") or ""):
            continue
        related.append(page)
    if section["id"] == "mv":
        rows = sorted(rows, key=_mv_rank)
        if not rows:
            return "\n".join(lines + ["未在检索结果中找到。"])
        lines.append("视频站只保留检索到的地址。")
        lines.append("")
        shown = 0
        for row in rows:
            title = row.get("title") or ""
            blob = f"{title} {row.get('snippet') or ''}"
            if not _is_video(row.get("url") or "") or _skipped_host(row.get("url") or ""):
                continue
            if not topic_match(blob, song, artist, "mv"):
                continue
            if len(title) > 80:
                title = title[:80].rstrip() + "…"
            shown += 1
            lines.append(f"- {_link(title or row['url'], row['url'])}")
            if row.get("snippet"):
                lines.append(f"  {row['snippet'][:180]}")
            if shown >= 6:
                break
        if shown == 0:
            return "\n".join(lines + ["未在检索结果中找到。"])
        return "\n".join(lines)

    catalog = [row for row in rows if row.get("catalog") and row.get("snippet")]
    for row in catalog[:3]:
        lines.append(f"- {row['snippet']}")
        if row.get("url"):
            lines.append(f"  （来源：{row['url']}）")
    quotes = []
    for page in related:
        for paragraph in _pick(page.get("text") or "", section.get("keywords") or []):
            quotes.append((paragraph, page.get("url") or "", page.get("title") or page.get("url") or ""))
    quotes = _dedupe_quotes(quotes)[:3]
    if not rows and not quotes and not catalog:
        return "\n".join(lines + ["未在检索结果中找到。"])
    for paragraph, url, title in quotes:
        lines.append(f"- {paragraph}")
        if url:
            lines.append(f"  （来源：{_link(title, url)}）")
    if not quotes:
        matched = [
            row
            for row in rows
            if not row.get("catalog")
            and _blob_has(row, section.get("keywords") or [])
            and topic_match(
                f"{row.get('title') or ''} {row.get('snippet') or ''}",
                song,
                artist,
                section["id"],
            )
            and not _skipped_host(row.get("url") or "")
            and not _messy(row)
        ]
        if not matched and not catalog:
            return "\n".join(lines + ["未在检索结果中找到。"])
        for row in matched[:4]:
            lines.append(f"- {_link(row.get('title') or row['url'], row['url'])}")
            if row.get("snippet"):
                lines.append(f"  {row['snippet'][:180]}")
    return "\n".join(lines)


def _discogs_block(payload: dict) -> str:
    if not payload:
        return "Discogs 没有调用。"
    if not payload.get("ok"):
        return f"Discogs 调用失败：{str(payload.get('error') or '').splitlines()[0]}"
    releases = payload.get("releases") or []
    if not releases:
        return payload.get("note") or "Discogs 没有对上的发行。"
    best = releases[0]
    bits = [f"Discogs 发行「{best.get('title') or '未命名'}」"]
    if best.get("artists"):
        bits.append(f"艺人 {'、'.join(best['artists'][:3])}")
    if best.get("year"):
        bits.append(f"年份 {best['year']}")
    lines = ["，".join(bits) + "。这是目录信息，不是乐评。"]
    if best.get("labels"):
        lines.append(f"- 厂牌：{'、'.join(best['labels'][:3])}")
    styles = (best.get("styles") or []) + (best.get("genres") or [])
    if styles:
        lines.append(f"- 风格：{'、'.join(styles[:6])}")
    if best.get("formats"):
        lines.append(f"- 格式：{'、'.join(best['formats'][:3])}")
    named = [
        track.get("title")
        for track in (best.get("tracks") or [])
        if track.get("title")
    ]
    if named:
        lines.append(f"- 曲目：{'、'.join(named[:8])}")
    if best.get("url"):
        lines.append(f"（来源：{best['url']}）")
    return "\n".join(lines)


def _musicbrainz_block(payload: dict) -> str:
    if not payload:
        return "MusicBrainz 没有调用。"
    if not payload.get("ok"):
        message = str(payload.get("error") or "").splitlines()[0]
        if "503" in message or "429" in message:
            message = "MusicBrainz 暂时不可用"
        return f"MusicBrainz 调用失败：{message}"
    recordings = payload.get("recordings") or []
    if not recordings:
        return payload.get("note") or "MusicBrainz 没有匹配录音。"
    best = recordings[0]
    bits = [f"MusicBrainz 录音「{best.get('title') or '未命名'}」"]
    if best.get("artist"):
        bits.append(f"艺人 {best['artist']}")
    if best.get("length"):
        bits.append(f"时长 {best['length']}")
    if best.get("first_release_date"):
        bits.append(f"首发 {best['first_release_date']}")
    lines = ["，".join(bits) + "。"]
    if best.get("disambiguation"):
        lines.append(f"区分说明：{best['disambiguation']}。")
    for release in _same_year_releases(best)[:3]:
        when = "，".join(part for part in (release.get("date"), release.get("country")) if part)
        suffix = f"（{when}）" if when else ""
        lines.append(f"- 专辑：{release.get('title') or '未知'}{suffix}")
    tags = best.get("genres") or best.get("tags") or []
    if tags:
        lines.append(f"- 标签：{'、'.join(tags[:8])}")
    if best.get("url"):
        lines.append(f"（来源：{best['url']}）")
    for item in best.get("urls") or []:
        lines.append(f"- {item.get('type') or '链接'}：{item.get('url')}")
    return "\n".join(lines)


def _same_year_releases(recording: dict) -> list[dict]:
    releases = list(recording.get("releases") or [])
    year = (recording.get("first_release_date") or "")[:4]
    if year:
        same_year = [item for item in releases if (item.get("date") or "").startswith(year)]
        if same_year:
            releases = same_year
    releases.sort(key=lambda item: 0 if re.search(r"[\u4e00-\u9fff]", item.get("title") or "") else 1)
    chosen = []
    seen: set[str] = set()
    for item in releases:
        key = f"{item.get('date') or ''}|{item.get('country') or ''}"
        if key in seen:
            continue
        seen.add(key)
        chosen.append(item)
    return chosen


def _mv_rank(row: dict) -> int:
    blob = f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()
    if any(word in blob for word in ("官方", "official music video", "official mv")):
        return 0
    return 1


def _messy(row: dict) -> bool:
    title = row.get("title") or ""
    return len(title) > 90 or title.count("周杰伦") >= 3 or title.count("YouTube") >= 2


def _blob_has(row: dict, keywords: list[str]) -> bool:
    blob = f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()
    return any(keyword.lower() in blob for keyword in keywords)


def _tool_block(trace: list[dict]) -> str:
    counts = Counter(item["tool"] for item in trace)
    lines = ["## 工具调用", "", "| 工具 | 次数 |", "|------|------|"]
    for name in (
        "search_web",
        "fetch_page",
        "lookup_musicbrainz",
        "lookup_discogs",
        "analyze_criticism",
        "listen_recording",
    ):
        lines.append(f"| {name} | {counts.get(name, 0)} |")
    failed = [item for item in trace if not item.get("ok")]
    if failed:
        lines.append("")
        lines.append(f"其中 {len(failed)} 次没有拿到结果。")
    return "\n".join(lines)


def _pick(text: str, keywords: list[str], limit: int = 2) -> list[str]:
    scored = []
    for paragraph in _paragraphs(text):
        if _looks_like_nav(paragraph):
            continue
        haystack = paragraph.lower()
        score = sum(haystack.count(keyword.lower()) for keyword in keywords)
        if score:
            scored.append((score, paragraph[:450]))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [paragraph for _, paragraph in scored[:limit]]


def _looks_like_nav(paragraph: str) -> bool:
    haystack = paragraph.lower()
    hits = sum(token in haystack for token in ("lyrics", "chords", "pinyin", "streaming", "covers"))
    return hits >= 3


def _dedupe_quotes(quotes: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    kept = []
    for paragraph, url, title in quotes:
        key = "".join(paragraph.split())[:48]
        longer = any(
            "".join(other.split())[:48] == key and len(other) > len(paragraph) for other, _, _ in quotes
        )
        if longer or any("".join(item[0].split())[:48] == key for item in kept):
            continue
        kept.append((paragraph, url, title))
    return kept


def _skipped_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    blocked = ("musescore.com", "kotobank.jp", "weblio.jp", "meaning-dictionary.com")
    if is_reference_host(url):
        return True
    return any(host == name or host.endswith("." + name) for name in blocked)


def _paragraphs(text: str) -> list[str]:
    chunks = re.split(r"\n\s*\n", text or "")
    if len(chunks) <= 1:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        chunks = []
        buffer: list[str] = []
        for line in lines:
            buffer.append(line)
            if len(buffer) == 4:
                chunks.append(" ".join(buffer))
                buffer = []
        if buffer:
            chunks.append(" ".join(buffer))
    paragraphs = []
    for chunk in chunks:
        piece = re.sub(r"\s+", " ", chunk).strip()
        if len(piece) >= 40:
            paragraphs.append(piece)
    return paragraphs


def _link(title: str, url: str) -> str:
    label = (title or url).replace("[", "（").replace("]", "）")
    if len(label) > 48:
        label = label[:48].rstrip() + "…"
    return f"[{label}]({url})"


def _heading(song: str, artist: str) -> str:
    if song and artist:
        return f"{song} — {artist}"
    return artist or song or "乐评"


def _slug(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", value).strip()
    return cleaned[:40] or "untitled"


def present_bundle(bundle: dict) -> dict:
    """把一次检索收成网页报告用的结构。"""
    song = bundle.get("song") or ""
    artist = bundle.get("artist") or ""
    facts, links, note = _present_recording(bundle.get("musicbrainz") or {})
    extra_facts, extra_links = _present_discogs(bundle.get("discogs") or {}, song)
    seen = {item["label"] for item in facts}
    for item in extra_facts:
        if item["label"] not in seen:
            facts.append(item)
    links.extend(extra_links)
    criticism = bundle.get("criticism") or {}
    lead = criticism.get("agreement") or ""
    lead_url = ""
    for item in criticism.get("dimensions") or []:
        if item.get("url"):
            lead_url = item["url"]
            break
    pages = bundle.get("pages") or []
    sections = []
    for section in bundle.get("sections") or []:
        enriched = {**section, "_song": song, "_artist": artist}
        sections.append(_present_section(enriched, pages))
    return {
        "song": song,
        "artist": artist,
        "facts": facts,
        "links": links,
        "lead": lead,
        "leadUrl": lead_url,
        "leadLabel": "专业乐评",
        "note": note,
        "cloud": _present_cloud(bundle.get("cloud") or {}),
        "aiReview": bundle.get("ai_review") or {"text": "", "note": ""},
        "sections": sections,
    }


def _present_cloud(cloud: dict) -> dict:
    return {
        "words": cloud.get("words") or [],
        "platformClouds": cloud.get("platform_clouds") or [],
        "sources": cloud.get("sources") or [],
        "posts": cloud.get("posts") or 0,
        "heat": cloud.get("heat") or {},
        "aspects": cloud.get("aspects") or [],
        "tone": cloud.get("tone") or {},
        "drift": cloud.get("drift") or {},
        "sentiment": cloud.get("sentiment") or [],
        "ratings": cloud.get("ratings") or [],
        "links": cloud.get("links") or [],
        "findings": cloud.get("findings") or [],
        "note": cloud.get("note") or "",
        "reading": cloud.get("reading") or "",
    }


def _cloud_block(cloud: dict) -> str:
    lines = ["## 听众怎么说", ""]
    if not cloud or not cloud.get("words"):
        lines.append(cloud.get("note") or "没有拿到听众评论。")
        return "\n".join(lines)
    for line in cloud.get("findings") or []:
        if line.startswith("微博") or line.startswith("知乎"):
            continue
        lines.append(f"- {line}")
    if cloud.get("reading"):
        lines.extend(["", "### 听众分析（归纳）", "", cloud["reading"], ""])
    if cloud.get("note"):
        lines.extend(["", cloud["note"]])
    heat = cloud.get("heat") or {}
    if heat.get("rows"):
        lines.extend(["", "### 平台 × 话题（提到该话题的评论占比）", ""])
        lines.append("| 平台 | " + " | ".join(heat["cols"]) + " |")
        lines.append("|" + "---|" * (len(heat["cols"]) + 1))
        for name, size, row in zip(heat["rows"], heat.get("sizes") or [], heat["cells"]):
            if name in {"微博", "知乎"}:
                continue
            cells = " | ".join(f"{cell['share']}%" for cell in row)
            lines.append(f"| {name}（{size}） | {cells} |")
    drift = cloud.get("drift") or {}
    if drift.get("rows"):
        overall = drift.get("overall") or {}
        lines.extend(["", "### 平台偏移（和各平台平均相比）", ""])
        base = f"各平台平均（{overall.get('platforms')} 个平台等权）：有明确褒贬的评论占 {overall.get('opinion')}%"
        if overall.get("lean") is not None:
            base += f"，表态里褒义占 {overall.get('lean')}%"
        lines.append(base + "。")
        lines.append("")
        lines.extend(
            f"- {line}" for line in drift.get("lines") or []
            if not line.startswith("微博") and not line.startswith("知乎")
        )
        cols = [item["aspect"] for item in drift["rows"][0]["offsets"]]
        lines.extend(["", "| 平台 | 表态比例 | 褒义占表态 | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 3)])
        for row in drift["rows"]:
            if row.get("platform") in {"微博", "知乎"}:
                continue
            cells = " | ".join(f"{item['diff']:+d}" for item in row["offsets"])
            lean = "样本少" if row.get("lean_diff") is None else f"{row['lean_diff']:+d}"
            lines.append(f"| {row['platform']} | {row['opinion_diff']:+d} | {lean} | {cells} |")
        lines.extend(["", "表中数字都是这个平台减去各平台平均，单位是百分点。话题列是提到该话题的评论占比。"])
    aspects = [item for item in cloud.get("aspects") or [] if item.get("quote")]
    if aspects:
        lines.extend(["", "### 代表评论", ""])
        for item in aspects:
            where = f"{item.get('platform')} {item.get('where') or ''}".strip()
            lines.append(f"- {item['name']}（{item['count']} 条）：「{item['quote']}」（{where}，来源：{item.get('url')}）")
    links = cloud.get("links") or []
    if links:
        lines.extend(["", "### 评论出处", ""])
        lines.extend(f"- {item['platform']}：[{item['title']}]({item['url']})" for item in links)
    return "\n".join(lines)


def _present_discogs(payload: dict, song: str) -> tuple[list[dict], list[dict]]:
    if not payload or not payload.get("ok"):
        return [], []
    releases = payload.get("releases") or []
    if not releases:
        return [], []
    best = releases[0]
    facts = []
    if best.get("year"):
        facts.append({"label": "年份", "value": str(best["year"])})
    if best.get("labels"):
        facts.append({"label": "厂牌", "value": "、".join(best["labels"][:2])})
    styles = list(best.get("styles") or [])
    for genre in best.get("genres") or []:
        if genre not in styles:
            styles.append(genre)
    if styles:
        facts.append({"label": "风格", "value": "、".join(styles[:4])})
    if best.get("title") and song.casefold() not in best["title"].casefold():
        facts.append({"label": "专辑", "value": best["title"]})
    links = []
    if best.get("url"):
        links.append({"label": "Discogs", "url": best["url"]})
    return facts, links


def _present_recording(payload: dict) -> tuple[list[dict], list[dict], str]:
    if not payload:
        return [], [], ""
    if not payload.get("ok"):
        message = str(payload.get("error") or "").splitlines()[0]
        if "503" in message or "429" in message:
            message = "MusicBrainz 暂时不可用"
        return [], [], message
    recordings = payload.get("recordings") or []
    if not recordings:
        return [], [], payload.get("note") or "MusicBrainz 没有匹配录音。"
    best = recordings[0]
    facts = []
    if best.get("length"):
        facts.append({"label": "时长", "value": best["length"]})
    if best.get("first_release_date"):
        facts.append({"label": "首发", "value": best["first_release_date"]})
    albums = _same_year_releases(best)
    if albums and albums[0].get("title"):
        facts.append({"label": "专辑", "value": albums[0]["title"]})
    tags = best.get("genres") or best.get("tags") or []
    if tags:
        facts.append({"label": "标签", "value": "、".join(tags[:4])})
    links = []
    if best.get("url"):
        links.append({"label": "MusicBrainz", "url": best["url"]})
    for item in (best.get("urls") or [])[:3]:
        if item.get("url"):
            links.append({"label": item.get("type") or "链接", "url": item["url"]})
    return facts, links, ""


def _present_section(section: dict, pages: list[dict]) -> dict:
    rows = section.get("results") or []
    song = section.get("_song") or ""
    artist = section.get("_artist") or ""
    if section.get("analysis"):
        items = [
            {
                "kind": "quote",
                "title": row.get("title") or "乐评",
                "url": row.get("url") or "",
                "text": row.get("snippet") or "",
            }
            for row in rows
            if row.get("snippet")
        ]
        return {
            "id": "review",
            "title": section["title"],
            "found": bool(items),
            "missing": "" if items else (section.get("note") or "没有找到专业乐评。"),
            "items": items,
        }
    if section.get("video") or section["id"] == "video":
        items = []
        for row in rows[:10]:
            items.append(
                {
                    "kind": "video",
                    "title": row.get("title") or row.get("url") or "影音测评",
                    "url": row.get("url") or "",
                    "host": row.get("platform") or row.get("host") or _host_label(row.get("url") or ""),
                    "text": (row.get("snippet") or "")[:180],
                }
            )
        return {
            "id": "video",
            "title": section["title"],
            "found": bool(items),
            "missing": "" if items else (section.get("note") or "没有找到影音测评。"),
            "items": items,
            "findings": section.get("findings") or [],
            "note": section.get("note") or "",
        }
    if section["id"] == "mv":
        items = []
        for row in sorted(rows, key=_mv_rank):
            title = row.get("title") or ""
            url = row.get("url") or ""
            blob = f"{title} {row.get('snippet') or ''}"
            if not url or not _is_video(url) or _skipped_host(url) or not topic_match(blob, song, artist, "mv"):
                continue
            if len(title) > 80:
                title = title[:80].rstrip() + "…"
            items.append(
                {
                    "kind": "video",
                    "title": title or url,
                    "url": url,
                    "host": _host_label(url),
                    "text": (row.get("snippet") or "")[:160],
                }
            )
            if len(items) >= 6:
                break
        return {"id": "mv", "title": section["title"], "found": bool(items), "items": items}

    related = []
    for page in pages:
        blob = (page.get("title") or "") + (page.get("text") or "")[:2500]
        if not page.get("ok") or not topic_match(blob, song, artist, section["id"]) or _skipped_host(page.get("url") or ""):
            continue
        related.append(page)
    quotes = []
    for page in related:
        for paragraph in _pick(page.get("text") or "", section.get("keywords") or []):
            quotes.append((paragraph, page.get("url") or "", page.get("title") or ""))
    items = [
        {
            "kind": "quote",
            "text": row.get("snippet") or "",
            "url": row.get("url") or "",
            "title": row.get("title") or row.get("url") or "",
        }
        for row in rows
        if row.get("catalog") and row.get("snippet")
    ]
    items.extend(
        {"kind": "quote", "text": text, "url": url, "title": title or url}
        for text, url, title in _dedupe_quotes(quotes)[:3]
    )
    if not items:
        matched = [
            row
            for row in rows
            if not row.get("catalog")
            and _blob_has(row, section.get("keywords") or [])
            and topic_match(f"{row.get('title') or ''} {row.get('snippet') or ''}", song, artist, section["id"])
            and not _skipped_host(row.get("url") or "")
            and not _messy(row)
        ]
        items = [
            {
                "kind": "link",
                "title": row.get("title") or row.get("url") or "",
                "url": row.get("url") or "",
                "text": (row.get("snippet") or "")[:180],
            }
            for row in matched[:4]
        ]
    return {
        "id": section["id"],
        "title": section["title"],
        "found": bool(items),
        "missing": "" if items else "这次检索没有对上的页面。",
        "items": items[:4],
    }


def _is_video(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        token in host
        for token in ("youtube", "youtu.be", "bilibili", "b23.tv", "iqiyi", "youku", "v.qq.com")
    )


def _host_label(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "youtube" in host or host == "youtu.be":
        return "YouTube"
    if "bilibili" in host or host.endswith("b23.tv"):
        return "哔哩哔哩"
    if "iqiyi" in host:
        return "爱奇艺"
    if "youku" in host:
        return "优酷"
    return host.removeprefix("www.") or "视频"
