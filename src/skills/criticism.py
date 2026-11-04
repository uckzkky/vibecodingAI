"""专业乐评分析。只打开垂直乐评和厂牌页面，按听感维度整理判断。"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from skills.common import host_kind, is_reference_host, song_mentioned, topic_match
from skills.fetch_page import fetch_page
from skills.schemas import CriticismArgs
from skills.web_search import search_web

CRITIC_HOSTS = (
    "rateyourmusic.com",
    "pitchfork.com",
    "thequietus.com",
    "residentadvisor.net",
    "ra.co",
    "allmusic.com",
    "albumoftheyear.org",
    "sputnikmusic.com",
    "boomkat.com",
    "thewire.co.uk",
    "tinymixtapes.com",
    "daily.bandcamp.com",
)
ARTIST_HOSTS = ("bandcamp.com",)
CATALOG_HOSTS = ("discogs.com",)
OUTLET_NAMES = {
    "rateyourmusic.com": "Rate Your Music",
    "pitchfork.com": "Pitchfork",
    "thequietus.com": "The Quietus",
    "residentadvisor.net": "Resident Advisor",
    "ra.co": "Resident Advisor",
    "allmusic.com": "AllMusic",
    "albumoftheyear.org": "Album of the Year",
    "sputnikmusic.com": "Sputnikmusic",
    "boomkat.com": "Boomkat",
    "thewire.co.uk": "The Wire",
    "tinymixtapes.com": "Tiny Mix Tapes",
    "daily.bandcamp.com": "Bandcamp Daily",
    "bandcamp.com": "Bandcamp",
    "discogs.com": "Discogs",
}
DIMENSIONS = (
    {
        "name": "声音与制作",
        "cues": (
            "drum machine",
            "synthesizer",
            "synthesiser",
            "production",
            "reverb",
            "texture",
            "lo-fi",
            "lofi",
            "distortion",
            "ambient",
            "synth",
            "mix",
            "tape",
            "bass",
            "guitar",
            "piano",
            "vocal",
            "合成器",
            "混响",
            "鼓机",
            "音色",
            "制作",
            "混音",
            "采样",
            "氛围",
            "吉他",
            "钢琴",
            "人声",
            "失真",
            "贝斯",
        ),
    },
    {
        "name": "结构与情绪",
        "cues": (
            "melancholy",
            "nostalgia",
            "repetition",
            "melody",
            "chorus",
            "mood",
            "sparse",
            "旋律",
            "副歌",
            "情绪",
            "怀旧",
            "疏离",
            "重复",
            "结构",
        ),
    },
    {
        "name": "场景与参照",
        "cues": (
            "reminiscent",
            "underground",
            "independent",
            "influenced",
            "recalls",
            "label",
            "scene",
            "厂牌",
            "场景",
            "独立",
            "地下",
            "参照",
        ),
    },
    {
        "name": "评价落点",
        "cues": (
            "masterpiece",
            "disappointing",
            "essential",
            "highlight",
            "standout",
            "overrated",
            "rating",
            "score",
            "推荐",
            "失望",
            "亮点",
            "评分",
            "出色",
            "平庸",
        ),
    },
)
RATING = re.compile(
    r"(?:rating|score|rated|评分|平均)[^\d]{0,16}(\d+(?:\.\d+)?)\s*/\s*(5|10|100)",
    re.IGNORECASE,
)


PLACES = (
    "海南",
    "北京",
    "上海",
    "广州",
    "深圳",
    "成都",
    "杭州",
    "武汉",
    "西安",
    "南京",
    "重庆",
    "台湾",
    "香港",
    "日本",
    "韩国",
    "英国",
    "美国",
)
CHINESE_PLACES = set(PLACES) - {"日本", "韩国", "英国", "美国"}
LABEL_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9 .&'-]{1,24}Records|[\u4e00-\u9fff]{2,8}唱片)")
REVIEW_MARKERS = ("乐评", "测评", "专评", "专辑评论", "review", "critic")
INTERVIEW_MARKERS = ("专访", "采访", "访谈", "interview")


def analyze_criticism(title: str = "", artist: str = "") -> dict:
    args = CriticismArgs(title=title, artist=artist)
    if not args.title and not args.artist:
        return {"ok": False, "found": False, "note": "没有歌手，也没有歌名。", "dimensions": [], "video_reviews": []}
    scope = _footprint(args.artist, args.title)
    queries = _queries(scope, args.artist, args.title)
    rows = []
    seen: set[str] = set()
    videos = []
    video_ids: set[str] = set()
    for query in queries:
        found = search_web(query, max_results=5)
        for row in found.get("results") or []:
            url = row.get("url") or ""
            blob = f"{row.get('title') or ''} {row.get('snippet') or ''}"
            if not url or url in seen or is_reference_host(url) or not _keep_review(blob, url, args.title, args.artist):
                continue
            seen.add(url)
            if host_kind(url) == "video":
                if _messy_title(row.get("title") or ""):
                    continue
                key = _video_key(url)
                if key in video_ids or len(videos) >= 3:
                    continue
                video_ids.add(key)
                videos.append(
                    {
                        "title": (row.get("title") or url)[:80],
                        "url": url,
                        "outlet": _outlet(_host(url)),
                    }
                )
                continue
            rows.append(row)
    rows.sort(
        key=lambda row: (
            0 if _is_review(f"{row.get('title') or ''} {row.get('snippet') or ''}") else 1,
            _host_rank(row.get("url") or ""),
        )
    )
    pages = []
    for row in rows:
        if len(pages) >= 3:
            break
        page = fetch_page(row["url"], max_chars=7000)
        text = (page.get("text") or "").strip()
        if not page.get("ok") or len(text) < 80:
            continue
        blob = f"{page.get('title') or ''}\n{text[:4000]}"
        if not _on_subject(blob, args.title, args.artist):
            continue
        if _is_interview(blob) and not _is_review(blob):
            continue
        host = _host(page.get("url") or row["url"])
        pages.append(
            {
                "url": page.get("url") or row["url"],
                "title": page.get("title") or row.get("title") or "",
                "text": text,
                "role": _role(host, blob),
                "outlet": _outlet(host, page.get("title") or row.get("title") or ""),
            }
        )

    critics = [page for page in pages if page["role"] == "critic"]
    dimensions = _dimensions(critics, args.title, args.artist)
    agreement = _agreement(critics, dimensions)
    scope_line = _scope_line(scope)
    if agreement:
        agreement = f"{scope_line}{agreement}"
    elif videos and not dimensions:
        agreement = f"{scope_line}找到影像测评，页面没有正文，只保留链接，不把标题写成听感判断。"
    statements = _statements(pages)
    found = bool(dimensions)
    return {
        "ok": True,
        "found": found,
        "agreement": agreement,
        "dimensions": dimensions,
        "video_reviews": videos,
        "artist_statements": statements,
        "scope": scope,
        "outlets": [{"name": page["outlet"], "url": page["url"], "title": page["title"]} for page in pages],
        "note": "" if found else _empty_note(pages, scope, videos),
    }


def _on_subject(blob: str, title: str, artist: str) -> bool:
    song_hit = song_mentioned(blob, title) if title else False
    artist_hit = bool(artist) and artist.casefold() in blob.casefold()
    if title and artist:
        return song_hit and artist_hit
    if artist:
        return artist_hit
    return song_hit


def _footprint(artist: str, title: str) -> dict:
    snippets = []
    if artist and title:
        queries = (f'"{artist}" 厂牌 说唱 独立 电子', f'"{artist}" "{title}"')
    elif artist:
        queries = (f'"{artist}" 厂牌 说唱 独立 电子', f'"{artist}" 乐评')
    else:
        queries = (f'"{title}" 乐评 专辑',)
    for query in queries:
        found = search_web(query, max_results=5)
        for row in found.get("results") or []:
            blob = f"{row.get('title') or ''} {row.get('snippet') or ''}"
            if artist.casefold() not in blob.casefold() or is_reference_host(row.get("url") or ""):
                continue
            snippets.append(blob)
    text = "\n".join(snippets)
    folded = text.casefold()
    places = [name for name in PLACES if name in text]
    chinese = any(name in CHINESE_PLACES for name in places) or any(
        word in text for word in ("中文", "说唱", "嘻哈", "独立音乐")
    )
    scenes = []
    if any(word in text or word in folded for word in ("说唱", "嘻哈", "饶舌", "hip hop", "hip-hop")) and (
        chinese or "中文" in text
    ):
        scenes.append("中文说唱")
    elif any(word in folded for word in ("hip hop", "hip-hop", "rap")):
        scenes.append("说唱")
    if any(word in text for word in ("电子",)) or any(word in folded for word in ("techno", "house")):
        scenes.append("电子")
    if "独立" in text and "中文说唱" not in scenes:
        scenes.append("独立")
    labels = []
    for match in LABEL_RE.findall(text):
        label = " ".join(match.split())
        if label and label not in labels and artist.casefold() not in label.casefold():
            labels.append(label)
    platforms = []
    for name in ("bandcamp.com", "bilibili.com", "music.163.com", "youtube.com"):
        if name.split(".")[0] in folded:
            platforms.append(name)
    return {
        "places": places[:4],
        "scenes": scenes[:3],
        "labels": labels[:2],
        "platforms": platforms,
        "chinese": chinese,
    }


def _queries(scope: dict, artist: str, title: str) -> list[str]:
    subject = f'"{artist}" "{title}"'.strip() if artist and title else f'"{artist or title}"'
    queries = []
    if scope.get("chinese") or "中文说唱" in scope.get("scenes", []):
        queries.append(f"{subject} (乐评 OR 测评 OR 专评)")
        if artist and "中文说唱" in scope.get("scenes", []):
            queries.append(f'"{artist}" (乐评 OR 测评) (说唱 OR 嘻哈)')
    elif "电子" in scope.get("scenes", []):
        queries.append(f"{subject} site:residentadvisor.net OR site:boomkat.com OR site:thequietus.com")
    elif not scope.get("chinese"):
        queries.append(f"{subject} (review OR critic OR 乐评)")
    who = artist or title
    for label in (scope.get("labels") or [])[:1]:
        queries.append(f'"{label}" "{who}" (乐评 OR 测评 OR review)')
    if "bandcamp.com" in (scope.get("platforms") or []):
        queries.append(f"{subject} site:bandcamp.com")
    if not queries:
        queries.append(f"{subject} (乐评 OR 测评 OR review)")
    return queries[:4]


def _keep_review(blob: str, url: str, title: str, artist: str) -> bool:
    if not topic_match(blob, title, artist, "review"):
        return False
    if "/charts/" in url:
        return False
    if _is_interview(blob) and not _is_review(blob):
        return False
    host = _host(url)
    if host_kind(url) == "video":
        return _is_review(blob)
    return _is_review(blob) or _role(host, blob) in {"critic", "artist", "catalog"}


def _is_review(blob: str) -> bool:
    folded = blob.casefold()
    return any(marker in folded or marker in blob for marker in REVIEW_MARKERS)


def _is_interview(blob: str) -> bool:
    folded = blob.casefold()
    return any(marker in folded or marker in blob for marker in INTERVIEW_MARKERS)


def _scope_line(scope: dict) -> str:
    bits = (scope.get("places") or []) + (scope.get("scenes") or []) + (scope.get("labels") or [])
    if not bits:
        return "公开页面没有写明地区或厂牌，这次按艺人名检索乐评和测评。"
    return f"按检索到的活动范围（{'、'.join(bits)}）查询乐评。"


def _messy_title(title: str) -> bool:
    text = title or ""
    if text.casefold().count("youtube") >= 2:
        return True
    return text.count("…") + text.count("...") >= 1


def _video_key(url: str) -> str:
    matched = re.search(r"BV[0-9A-Za-z]+", url)
    return matched.group(0) if matched else url


def _empty_note(pages: list[dict], scope: dict, videos: list[dict]) -> str:
    note = f"没有找到写成文的专业乐评。{_scope_line(scope)}"
    if videos:
        note += " 影像测评没有正文，不能据此做听感分析。"
    if any(page["role"] == "artist" for page in pages):
        note += " 打开的 Bandcamp 页面是艺人自述，不计入乐评。"
    return note


def _dimensions(pages: list[dict], song: str, artist: str) -> list[dict]:
    built = []
    for spec in DIMENSIONS:
        hits = []
        for page in pages:
            cues = _cues_in(page["text"], spec["cues"])
            sentence = _sentence(page["text"], cues, song, artist)
            if not cues or not sentence:
                continue
            hits.append({"page": page, "cues": cues, "sentence": sentence})
        if spec["name"] == "评价落点":
            for page in pages:
                rating = _rating(page["text"])
                if rating and not any(hit["page"]["url"] == page["url"] and hit.get("rating") for hit in hits):
                    hits.append(
                        {
                            "page": page,
                            "cues": [rating],
                            "sentence": "",
                            "rating": rating,
                        }
                    )
        if not hits:
            continue
        built.append(_reading(spec["name"], hits))
    return built


def _reading(name: str, hits: list[dict]) -> dict:
    outlets = []
    for hit in hits:
        outlet = hit["page"]["outlet"]
        if outlet not in outlets:
            outlets.append(outlet)
    cue_sets = [set(cue.casefold() for cue in hit["cues"]) for hit in hits if not hit.get("rating")]
    shared = set.intersection(*cue_sets) if len(cue_sets) >= 2 else set()
    shared_words = []
    for hit in hits:
        for cue in hit["cues"]:
            if cue.casefold() in shared and cue not in shared_words:
                shared_words.append(cue)
    primary = hits[0]
    if len(outlets) >= 2 and shared_words:
        reading = (
            f"{'、'.join(outlets)}都把{'、'.join(shared_words[:3])}写进对这首录音的判断。"
            "重合的是听感，不是曲目介绍。"
        )
    elif primary.get("rating"):
        reading = (
            f"{primary['page']['outlet']}页面上能看到的分数是 {primary['rating']}。"
            "分数只说明该站的评价落点，不能代替对声音的描述。"
        )
    else:
        words = "、".join(primary["cues"][:4])
        reading = f"{primary['page']['outlet']}讨论的是{name}，页面里实际出现的判断词是{words}。"
        if primary.get("sentence"):
            reading += f"支撑这句判断的原句是「{primary['sentence'][:80]}」。"
    return {
        "name": name,
        "reading": reading,
        "url": primary["page"]["url"],
        "outlet": primary["page"]["outlet"],
    }


def _agreement(pages: list[dict], dimensions: list[dict]) -> str:
    if not dimensions:
        return ""
    outlets = []
    for page in pages:
        if page["outlet"] not in outlets:
            outlets.append(page["outlet"])
    if len(outlets) >= 2:
        return f"专业页面来自{'、'.join(outlets)}。下面只保留它们重合或明确写出的听感，不把目录风格写成乐评。"
    return f"目前只有{outlets[0]}一处专业页面能对上，下面的判断不能写成共识。"


def _statements(pages: list[dict]) -> list[dict]:
    items = []
    for page in pages:
        if page["role"] != "artist":
            continue
        parts = _pieces(page["text"])
        chosen = ""
        for part in parts:
            if 40 <= len(part) <= 180:
                chosen = part
                break
        if not chosen:
            continue
        items.append(
            {
                "title": f"{page['outlet']} 艺人自述",
                "url": page["url"],
                "snippet": f"Bandcamp 的创作说明是艺人自己写的：「{chosen[:120]}」。这不是乐评。",
                "catalog": True,
            }
        )
        break
    return items[:1]


def _cues_in(text: str, cues: tuple[str, ...]) -> list[str]:
    folded = text.casefold()
    found: list[str] = []
    for cue in sorted(cues, key=len, reverse=True):
        if cue.casefold() not in folded:
            continue
        if any(cue.casefold() != kept.casefold() and cue.casefold() in kept.casefold() for kept in found):
            continue
        found.append(cue)
    return found[:6]


def _sentence(text: str, cues: list[str] | tuple[str, ...], song: str, artist: str) -> str:
    best = ""
    best_score = 0
    for part in _pieces(text):
        if not 24 <= len(part) <= 180 or _noise(part):
            continue
        folded = part.casefold()
        score = sum(2 for cue in cues if cue and cue.casefold() in folded)
        if song and song.casefold() in folded:
            score += 2
        if artist and artist.casefold() in folded:
            score += 1
        if score > best_score:
            best_score = score
            best = part
    if cues == ("",):
        return best
    return best if best_score else ""


def _pieces(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s+|\n+", text)
    return [" ".join(part.split()) for part in parts if part.strip()]


def _noise(text: str) -> bool:
    folded = text.casefold()
    markers = ("cookie", "subscribe", "sign in", "javascript", "lyrics", "歌词", "copyright")
    return any(marker in folded for marker in markers)


def _rating(text: str) -> str:
    matched = RATING.search(text[:4000])
    if not matched:
        return ""
    return f"{matched.group(1)}/{matched.group(2)}"


def _role(host: str, blob: str = "") -> str:
    if host == "daily.bandcamp.com" or _is(host, CRITIC_HOSTS):
        return "critic"
    if _is(host, ARTIST_HOSTS):
        return "artist"
    if _is(host, CATALOG_HOSTS):
        return "catalog"
    if _is_review(blob):
        return "critic"
    return ""


def _host_rank(url: str) -> int:
    role = _role(_host(url))
    return {"critic": 0, "artist": 1, "catalog": 2}.get(role, 3)


def _outlet(host: str, title: str = "") -> str:
    if "ourvoice" in (title or "").casefold():
        return "OurVoice"
    for suffix, name in OUTLET_NAMES.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return host


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _is(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)
