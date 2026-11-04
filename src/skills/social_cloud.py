"""收集听众在社媒上的评论，做词云、平台 × 话题热点图和代表评论。不是专业乐评。

网易云歌曲评论、豆瓣短评长评和哔哩哔哩视频评论直接读公开页面。小红书要登录，只用检索摘要补充。不采集微博和知乎。
"""

from __future__ import annotations

import html
import logging
import math
import re
import time
from collections import Counter
from urllib.parse import unquote, urlparse

import httpx

from skills.common import is_reference_host
from skills.schemas import SocialCloudArgs
from skills.web_search import search_web

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
SNIPPET_SITES = (
    ("xiaohongshu.com", "小红书"),
)
SNIPPET_HOSTS = (
    ("xiaohongshu.com", "小红书"),
    ("xhslink.com", "小红书"),
)
MUSIC_TYPES = ("音乐", "说唱", "演奏", "翻唱", "MV", "VOCALOID", "乐评", "专辑", "电音", "嘻哈")
MUSIC_TAGS = ("说唱", "音乐", "rap", "hiphop", "hip-hop", "嘻哈", "歌", "专辑", "乐评", "beat", "reaction")

ASPECTS = (
    ("人声与唱腔", ("人声", "声音", "嗓音", "唱腔", "咬字", "音色", "声线", "唱功", "嗓子", "转音", "高音", "唱得")),
    ("flow 与技术", ("flow", "押韵", "韵脚", "节奏", "技术", "快嘴", "卡点", "律动", "groove", "多押", "双押", "节拍")),
    ("歌词与叙事", ("歌词", "词写", "写词", "故事", "叙事", "文本", "意象", "画面", "金句", "表达", "这句", "那句", "词好")),
    ("编曲与制作", ("beat", "编曲", "制作", "采样", "混音", "合成器", "鼓点", "贝斯", "音效", "伴奏", "prod", "制作人", "前奏", "间奏")),
    ("情绪与氛围", ("情绪", "氛围", "怀旧", "回忆", "小时候", "童年", "青春", "治愈", "emo", "伤感", "温柔", "感动", "想哭", "泪目", "破防", "眼泪")),
    ("风格与参照", ("风格", "曲风", "流派", "二次元", "梦核", "电波", "致敬", "让我想起", "像极了", "jersey", "抽象", "实验", "lofi", "lo-fi")),
    ("赞誉", ("好听", "太强", "封神", "年专", "最佳", "宝藏", "单曲循环", "惊艳", "神作", "天才", "绝了", "爱了爱了", "上头", "顶级", "牛逼")),
    ("批评", ("难听", "拉完", "一般般", "无聊", "失望", "过誉", "吹得", "平庸", "听不下去", "抄袭", "模仿", "油腻", "拉胯", "下头", "稚嫩", "割裂")),
)
POSITIVE = set(ASPECTS[6][1])
NEGATIVE = set(ASPECTS[7][1])
_NEGATED = re.compile(r"不(好听|喜欢|行|太行|怎么样|咋样)")

STOP = {
    "我们", "他们", "你们", "这个", "那个", "一个", "没有", "不是", "什么", "怎么", "可以",
    "自己", "因为", "所以", "如果", "但是", "然后", "还是", "已经", "以及", "就是", "觉得",
    "真的", "一下", "来自", "关于", "进行", "通过", "目前", "其中", "现在", "时候", "东西",
    "感觉", "比较", "知道", "出来", "这么", "那么", "这样", "一样", "还有", "有点", "一点",
    "视频", "播放", "评论", "歌手", "音乐", "歌曲", "作者", "简介", "播放量", "弹幕", "点赞",
    "转发", "收藏", "今天", "网友", "大家", "有人", "个人空间", "动态", "人数", "官方", "频道",
    "主页", "合集", "高清", "原版", "投币", "硬币", "关注", "粉丝", "更新", "回复", "楼主",
    "微博", "小红书", "哔哩哔哩", "网易云", "抖音", "贴吧", "知乎", "bilibili", "up", "UP",
    "首歌", "这首", "这首歌", "一首", "专辑", "歌", "听", "听到", "听了", "真是",
    "生日快乐", "打卡", "前排", "沙发", "来了", "谢谢", "哈哈", "哈哈哈", "问题", "名字", "是不是", "部分", "评价",
}
_TIMESTAMP = re.compile(r"\d{1,2}:\d{2}")
_EMOTE = re.compile(r"\[[^\[\]]{1,12}\]")
_REPLY = re.compile(r"^回复\s*@[^:：]{1,30}[:：]\s*")
_MENTION = re.compile(r"@[^\s@:：,，]{1,30}")
_URL = re.compile(r"https?://\S+")
_BOILER = re.compile(r"(视频播放量|视频作者|作者简介|相关视频|弹幕量).*$")
_HAN = re.compile(r"[\u4e00-\u9fff]")
_KEEP_LATIN = {"flow", "beat", "diss", "rap", "trap", "emo", "groove", "jersey", "lofi", "hook", "prod"}
_POS_FLAGS = ("n", "nz", "a", "an", "ad", "vn", "i", "l", "eng")

MAX_SONGS = 5
MAX_VIDEOS = 8
MAX_DOUBAN = 3
DRIFT_MIN = 8
LEAN_MIN = 5


def social_cloud(title: str = "", artist: str = "", label: str = "") -> dict:
    args = SocialCloudArgs(title=title, artist=artist, label=label)
    if not args.artist and not args.title:
        return _empty("没有歌手，也没有歌名。")
    docs: list[dict] = []
    links: list[dict] = []
    errors: list[str] = []
    ratings: list[dict] = []
    for name, collect in (("网易云", _netease), ("哔哩哔哩", _bilibili), ("豆瓣", _douban), ("摘要", _snippets)):
        try:
            found, places = collect(args.artist, args.title, args.label)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            continue
        docs.extend(found)
        links.extend(item for item in places if item.get("url"))
        ratings.extend(item for item in places if item.get("score"))
    docs = [doc for doc in _dedupe(docs) if doc.get("platform") not in {"微博", "知乎"}]
    if not docs:
        note = "没有拿到听众评论。网易云、豆瓣和哔哩哔哩没有对上的条目，小红书的检索摘要也没有提到。"
        return {**_empty(note), "errors": errors}

    words = _words(docs, args.artist, args.title)
    sources = _sources(docs)
    heat = _heat(docs, sources)
    aspects = _aspects(docs)
    tone = _tone_counts(docs)
    drift = _drift(docs, sources)
    sentiment = _sentiment(docs, sources)
    analysis = _listener_analysis(docs, sources, aspects, tone, drift, ratings)
    platform_clouds = _platform_clouds(docs, sources)
    return {
        "ok": True,
        "posts": len(docs),
        "longform": sum(1 for doc in docs if "长评" in (doc.get("where") or "")),
        "words": words,
        "platform_clouds": platform_clouds,
        "sources": sources,
        "heat": heat,
        "aspects": aspects,
        "tone": tone,
        "drift": drift,
        "sentiment": sentiment,
        "ratings": ratings,
        "links": links[:14],
        "findings": _findings(docs, sources, heat, aspects, tone, ratings),
        "analysis": analysis,
        "reading": analysis.get("reading") or "",
        "note": "这些是听众评论和社媒摘要，不是专业乐评。没有保留用户名。",
        "errors": errors,
    }


def _netease(artist: str, title: str, label: str = "") -> tuple[list[dict], list[dict]]:
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://music.163.com/"}
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        query = " ".join(part for part in (label, artist, title) if part)
        response = client.get(
            "https://music.163.com/api/search/get/web",
            params={"s": query, "type": 1, "limit": 30, "offset": 0},
        )
        songs = ((response.json().get("result") or {}).get("songs") or [])
        picked = []
        for song in songs:
            names = [item.get("name") or "" for item in song.get("artists") or []]
            name = song.get("name") or ""
            if artist and not any(artist.casefold() in item.casefold() for item in names):
                continue
            if not artist and name.casefold() != title.casefold():
                continue
            picked.append(song)
        if title:
            picked.sort(key=lambda song: 0 if title.casefold() in (song.get("name") or "").casefold() else 1)
        docs: list[dict] = []
        places: list[dict] = []
        for song in picked[:MAX_SONGS]:
            song_id = song.get("id")
            url = f"https://music.163.com/song?id={song_id}"
            payload = client.get(
                f"https://music.163.com/api/v1/resource/comments/R_SO_4_{song_id}",
                params={"limit": 80, "offset": 0},
            ).json()
            rows = (payload.get("hotComments") or []) + (payload.get("comments") or [])
            count = 0
            for row in rows:
                raw = row.get("content") or ""
                text = _clean(raw, 480 if len(raw) >= 80 else 240)
                if not text:
                    continue
                docs.append(
                    {
                        "platform": "网易云",
                        "text": text,
                        "likes": int(row.get("likedCount") or 0),
                        "url": url,
                        "where": f"《{song.get('name') or ''}》" + ("长评" if len(text) >= 80 else ""),
                        "kind": "comment",
                    }
                )
                count += 1
            if count:
                places.append({"platform": "网易云", "title": f"《{song.get('name') or ''}》评论 {count} 条", "url": url})
            time.sleep(0.2)
    return docs, places


_ON_TOPIC = ("说唱", "嘻哈", "rap", "hiphop", "hip-hop", "freestyle", "专辑", "乐评", "测评", "解析", "韵脚", "reaction", "听感")
_OFF_TOPIC = ("动画", "动漫", "儿歌", "幼儿", "淘气", "龙骑团", "喵星人", "宠物", "养猫", "广告", "主题曲", "片头", "片尾", "插曲")


def _bilibili_queries(artist: str, title: str, label: str = "") -> list[str]:
    subject = artist or title
    queries = []
    if label and subject:
        queries.append(f"{label} {subject}")
    if artist and title:
        queries.append(f"{artist} {title}")
    queries.extend(
        [
            f"{subject} 说唱",
            f"{subject} 乐评",
            f"{subject} freestyle",
            f"{subject} 专辑",
            f"{subject} 嘻哈",
        ]
    )
    return queries


def _bilibili_keep(name: str, tags: str, kind: str, author: str, subject: str) -> int | None:
    """同名动画、宠物会盖住歌手。只留标题或标签里能对上音乐讨论的视频。"""
    blob = f"{name} {tags} {kind} {author}".casefold()
    if subject.casefold() not in blob:
        return None
    on_topic = any(cue in blob for cue in _ON_TOPIC)
    off_topic = any(cue in blob for cue in _OFF_TOPIC)
    if off_topic and not on_topic:
        return None
    tags_folded = tags.casefold()
    if not on_topic and not any(word in kind for word in MUSIC_TYPES) and not any(word in tags_folded for word in MUSIC_TAGS):
        return None
    score = 2 if subject.casefold() in name.casefold() else 0
    if on_topic:
        score += 5
    return score


def _bilibili_replies(client: httpx.Client, aid: int) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    cursor = 0
    for _ in range(3):
        try:
            reply = client.get(
                "https://api.bilibili.com/x/v2/reply/main",
                params={"type": 1, "oid": aid, "mode": 3, "next": cursor, "ps": 20},
            ).json()
        except (httpx.HTTPError, ValueError):
            break
        if reply.get("code") != 0:
            break
        data = reply.get("data") or {}
        batch = data.get("replies") or []
        if not batch:
            break
        for item in batch:
            text = _clean((item.get("content") or {}).get("message") or "")
            if not text or text in seen:
                continue
            seen.add(text)
            found.append((text, int(item.get("like") or 0)))
        info = data.get("cursor") or {}
        nxt = info.get("next")
        if info.get("is_end") or nxt in (None, cursor):
            break
        cursor = nxt
        time.sleep(0.2)
    return found


def _bilibili(artist: str, title: str, label: str = "") -> tuple[list[dict], list[dict]]:
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.bilibili.com/"}
    subject = artist or title
    videos: list[dict] = []
    seen: set[str] = set()
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        client.get("https://www.bilibili.com/")
        for query in _bilibili_queries(artist, title, label):
            payload = client.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={"search_type": "video", "keyword": query, "page": 1},
            ).json()
            for row in (payload.get("data") or {}).get("result") or []:
                bvid = row.get("bvid") or ""
                if not bvid or bvid in seen:
                    continue
                name = html.unescape(re.sub(r"<[^>]+>", "", row.get("title") or ""))
                score = _bilibili_keep(name, row.get("tag") or "", row.get("typename") or "", row.get("author") or "", subject)
                if score is None:
                    continue
                seen.add(bvid)
                videos.append({**row, "clean_title": name, "score": score})
            time.sleep(0.25)
        videos.sort(key=lambda row: (row["score"], int(row.get("review") or 0)), reverse=True)
        docs: list[dict] = []
        places: list[dict] = []
        for video in videos[:MAX_VIDEOS]:
            aid = video.get("aid")
            url = f"https://www.bilibili.com/video/{video.get('bvid')}"
            replies = _bilibili_replies(client, aid)
            for text, likes in replies:
                docs.append(
                    {
                        "platform": "哔哩哔哩",
                        "text": text,
                        "likes": likes,
                        "url": url,
                        "where": video["clean_title"][:40],
                        "kind": "comment",
                    }
                )
            if replies:
                places.append({"platform": "哔哩哔哩", "title": f"{video['clean_title'][:36]}（评论 {len(replies)} 条）", "url": url})
    return docs, places


def _douban(artist: str, title: str, label: str = "") -> tuple[list[dict], list[dict]]:
    from bs4 import BeautifulSoup

    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.douban.com/"}
    docs: list[dict] = []
    places: list[dict] = []
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        page = client.get(
            "https://www.douban.com/search",
            params={"cat": "1003", "q": " ".join(part for part in (label, artist, title) if part)},
        )
        soup = BeautifulSoup(page.text, "html.parser")
        subjects = []
        for result in soup.select("div.result"):
            link = result.select_one("div.title a") or result.select_one("a")
            if link is None:
                continue
            matched = re.search(r"music\.douban\.com/subject/(\d+)", unquote(link.get("href") or ""))
            if not matched:
                continue
            name = " ".join(link.get_text(" ").split())
            cast = " ".join((result.select_one(".subject-cast") or result).get_text(" ").split())
            if artist and artist.casefold() not in f"{name} {cast}".casefold():
                continue
            if not artist and title.casefold() not in name.casefold():
                continue
            if title and artist and title.casefold() in name.casefold():
                subjects.insert(0, (matched.group(1), name))
            else:
                subjects.append((matched.group(1), name))
        seen: set[str] = set()
        for subject_id, name in subjects:
            if subject_id in seen or len(seen) >= MAX_DOUBAN:
                continue
            seen.add(subject_id)
            url = f"https://music.douban.com/subject/{subject_id}/"
            time.sleep(0.6)
            detail = BeautifulSoup(client.get(url).text, "html.parser")
            score = _text(detail.select_one("strong.rating_num"))
            votes = _text(detail.select_one("span[property='v:votes']"))
            count = 0
            for start in (0, 20):
                time.sleep(0.6)
                listing = client.get(
                    f"{url}comments/",
                    params={"start": start, "limit": 20, "status": "P", "sort": "new_score"},
                )
                items = BeautifulSoup(listing.text, "html.parser").select("li.comment-item")
                for item in items:
                    text = _clean((item.select_one("span.short") or item).get_text(" "))
                    if not text:
                        continue
                    star = item.select_one("span[class*='allstar']")
                    stars = 0
                    if star is not None:
                        found = re.search(r"allstar(\d)0", " ".join(star.get("class") or []))
                        stars = int(found.group(1)) if found else 0
                    likes = _text(item.select_one("span.vote-count"))
                    docs.append(
                        {
                            "platform": "豆瓣",
                            "text": text,
                            "likes": int(likes) if likes.isdigit() else 0,
                            "stars": stars,
                            "url": f"{url}comments/",
                            "where": f"《{name}》短评",
                            "kind": "comment",
                        }
                    )
                    count += 1
                if len(items) < 20:
                    break
            time.sleep(0.6)
            reviews = BeautifulSoup(client.get(f"{url}reviews", params={"sort": "hotest"}).text, "html.parser")
            for item in reviews.select("div.review-item")[:8]:
                body = item.select_one("div.short-content")
                text = _clean(re.sub(r"\(\s*展开\s*\)", "", body.get_text(" ")) if body else "", 600)
                if not text:
                    continue
                star = item.select_one("span[class*='allstar']")
                found = re.search(r"allstar(\d)0", " ".join(star.get("class") or [])) if star is not None else None
                link = item.select_one("h2 a")
                docs.append(
                    {
                        "platform": "豆瓣",
                        "text": text,
                        "likes": 0,
                        "stars": int(found.group(1)) if found else 0,
                        "url": (link.get("href") if link is not None else "") or f"{url}reviews",
                        "where": f"《{name}》长评",
                        "kind": "comment",
                    }
                )
                count += 1
            places.append(
                {
                    "platform": "豆瓣",
                    "title": f"《{name}》" + (f"评分 {score}（{votes} 人）" if score else "评价人数不足") + f"，评论 {count} 条",
                    "url": url,
                    "score": score,
                    "votes": votes,
                    "name": name,
                }
            )
    return docs, places


def _text(node) -> str:
    return node.get_text(strip=True) if node is not None else ""


def _snippets(artist: str, title: str, label: str = "") -> tuple[list[dict], list[dict]]:
    subject = artist or title
    who = " ".join(part for part in (label, subject) if part)
    docs: list[dict] = []
    seen: set[str] = set()
    for site, _label in SNIPPET_SITES:
        queries = [
            f'"{who}" {title} site:{site}'.strip() if artist and title else f'"{who}" site:{site}',
            f'"{who}" 乐评 OR 听感 OR 评价 site:{site}',
        ]
        if artist and title:
            queries.append(f'"{who}" "{title}" 怎么样 site:{site}')
        for query in queries:
            result = search_web(query, max_results=5)
            for row in result.get("results") or []:
                url = row.get("url") or ""
                blob = f"{row.get('title') or ''} {row.get('snippet') or ''}"
                platform = _snippet_platform(url)
                if not url or url in seen or not platform or is_reference_host(url):
                    continue
                if subject.casefold() not in blob.casefold():
                    continue
                text = _clean(_BOILER.sub("", blob))
                if not text:
                    continue
                seen.add(url)
                docs.append({"platform": platform, "text": text, "likes": 0, "url": url, "where": "检索摘要", "kind": "snippet"})
            time.sleep(0.25)
    return docs, []


def _snippet_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for suffix, label in SNIPPET_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return label
    return ""


def _clean(text: str, limit: int = 240) -> str:
    text = _URL.sub("", text or "")
    text = _REPLY.sub("", text)
    text = _MENTION.sub("", text)
    text = _EMOTE.sub("", text)
    text = " ".join(text.split())
    if len(_TIMESTAMP.findall(text)) >= 3:
        return ""
    if len(text) < 4 or not (_HAN.search(text) or re.search(r"[A-Za-z]{3,}", text)):
        return ""
    return text[:limit]


def _dedupe(docs: list[dict]) -> list[dict]:
    kept = []
    seen: set[str] = set()
    for doc in docs:
        key = re.sub(r"\W+", "", doc["text"])[:40]
        if key in seen:
            continue
        seen.add(key)
        kept.append(doc)
    return kept


def _weight(doc: dict) -> float:
    base = 0.5 if doc["kind"] == "snippet" else 1.0
    return base + math.log1p(max(doc.get("likes") or 0, 0)) / 4


def _words(docs: list[dict], artist: str, title: str) -> list[dict]:
    blocked = {item.casefold() for item in STOP}
    for name in (artist, title):
        if name:
            blocked.add(name.casefold())
            blocked.update(part.casefold() for part in _plain_cut(name))
    scores: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    for doc in docs:
        terms = set()
        for token in _content_terms(_tagged(doc["text"]), blocked):
            terms.add(token)
        for aspect_words in (words for _name, words in ASPECTS):
            for cue in aspect_words:
                if len(cue) >= 2 and cue in doc["text"].casefold() and cue.casefold() not in blocked:
                    terms.add(cue)
        doc["terms"] = terms
        for term in terms:
            counts[term] += 1
            scores[term] += _weight(doc) * (1.5 if _word_tone(term) != "plain" else 1.0)
    ranked = [term for term, _score in scores.most_common(60) if counts[term] >= 2]
    if len(ranked) < 8:
        ranked = [term for term, _score in scores.most_common(24)]
    return [
        {"text": term, "count": counts[term], "score": round(scores[term], 2), "tone": _word_tone(term)}
        for term in ranked[:36]
    ]


def _platform_clouds(docs: list[dict], sources: list[dict]) -> list[dict]:
    """每个平台一份词云。同一个词只计一次出现次数，供前端按频率改变大小和颜色。"""
    clouds = []
    for item in sources:
        if item["count"] < 3:
            continue
        counts: Counter[str] = Counter()
        for doc in docs:
            if doc["platform"] != item["name"]:
                continue
            for term in doc.get("terms") or ():
                counts[term] += 1
        ranked = [term for term, count in counts.most_common(28) if count >= 2]
        if len(ranked) < 6:
            ranked = [term for term, _count in counts.most_common(16)]
        seen: set[str] = set()
        words = []
        for term in ranked:
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            words.append({"text": term, "count": counts[term]})
        if words:
            clouds.append({"platform": item["name"], "words": words})
    return clouds


_jieba_ready = False


def _tagged(text: str) -> list[tuple[str, str]]:
    global _jieba_ready
    try:
        import jieba
        import jieba.posseg as pseg
    except ImportError:
        return [(word, "n") for word in _plain_cut(text)]
    if not _jieba_ready:
        jieba.setLogLevel(logging.ERROR)
        for cue in (word for _name, words in ASPECTS for word in words):
            if len(cue) >= 2:
                jieba.add_word(cue)
        _jieba_ready = True
    return [(item.word, item.flag) for item in pseg.lcut(text)]


def _plain_cut(text: str) -> list[str]:
    try:
        import jieba

        jieba.setLogLevel(logging.ERROR)
        return [part for part in jieba.lcut(text or "") if part.strip()]
    except ImportError:
        return re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z]{3,}", text or "")


def _keep(token: str, flag: str) -> bool:
    if len(token) < 2 or len(token) > 8 or token.isdigit():
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z\-]*", token):
        return token.casefold() in _KEEP_LATIN or len(token) >= 3
    if not _HAN.search(token):
        return False
    if flag.startswith(_POS_FLAGS) or flag.startswith(("v", "nr", "ns", "nt", "j")):
        return True
    # 结巴有时把一个完整的较长词标成未知，不能因此丢掉
    return len(token) >= 3 and flag in {"x", "g", "b", "z"}


def _content_terms(pairs: list[tuple[str, str]], blocked: set[str]) -> set[str]:
    """按结巴切出的词来取，不按字数截断。紧挨着的两个实词再拼成一个更长的词。"""
    kept: list[tuple[int, str]] = []
    for index, (word, flag) in enumerate(pairs):
        token = word.strip()
        if token.casefold() in blocked or not _keep(token, flag):
            continue
        kept.append((index, token.casefold() if token.isascii() else token))
    terms = {token for _index, token in kept}
    for (left, first), (right, second) in zip(kept, kept[1:]):
        if right != left + 1:
            continue
        phrase = first + second
        if _HAN.search(first) and _HAN.search(second) and 3 <= len(phrase) <= 6:
            terms.add(phrase)
    return terms


def _word_tone(text: str) -> str:
    if text in NEGATIVE:
        return "neg"
    if text in POSITIVE:
        return "pos"
    if any(text in words for _name, words in ASPECTS[:6]):
        return "theme"
    return "plain"


def _hits(doc: dict, words: tuple[str, ...]) -> bool:
    folded = doc["text"].casefold()
    return any(word.casefold() in folded for word in words)


def _doc_tone(doc: dict) -> str:
    stars = doc.get("stars") or 0
    if stars >= 4:
        return "pos"
    if 0 < stars <= 2:
        return "neg"
    text = doc["text"]
    if _NEGATED.search(text) or _hits(doc, tuple(NEGATIVE)):
        return "neg"
    if _hits(doc, tuple(POSITIVE)):
        return "pos"
    return ""


def _sources(docs: list[dict]) -> list[dict]:
    counts: Counter[str] = Counter(doc["platform"] for doc in docs)
    kinds = {doc["platform"]: doc["kind"] for doc in docs}
    return [
        {"name": name, "count": count, "kind": "评论" if kinds[name] == "comment" else "检索摘要"}
        for name, count in counts.most_common()
    ]


def _heat(docs: list[dict], sources: list[dict]) -> dict:
    rows = [item["name"] for item in sources if item["count"] >= 3]
    cols = [name for name, _words in ASPECTS]
    cells = []
    for platform in rows:
        group = [doc for doc in docs if doc["platform"] == platform]
        line = []
        for _name, words in ASPECTS:
            hit = sum(1 for doc in group if _hits(doc, words))
            line.append({"hits": hit, "share": round(hit * 100 / len(group))})
        cells.append(line)
    return {"rows": rows, "cols": cols, "cells": cells, "sizes": [sum(1 for doc in docs if doc["platform"] == row) for row in rows]}


def _aspects(docs: list[dict]) -> list[dict]:
    built = []
    for name, words in ASPECTS:
        group = [doc for doc in docs if _hits(doc, words)]
        if not group:
            continue
        quote = _quote(group, words)
        voices = _quote_samples(group, words, limit=3)
        built.append(
            {
                "name": name,
                "count": len(group),
                "share": round(len(group) * 100 / len(docs)),
                "cues": [word for word in words if any(word.casefold() in doc["text"].casefold() for doc in group)][:5],
                "quote": quote["text"] if quote else "",
                "url": quote["url"] if quote else "",
                "platform": quote.get("platform") if quote else "",
                "where": quote.get("where") if quote else "",
                "quotes": voices,
            }
        )
    built.sort(key=lambda item: item["count"], reverse=True)
    return built


def _quote(group: list[dict], words: tuple[str, ...]) -> dict | None:
    picked = _quote_samples(group, words, limit=1)
    return picked[0] if picked else None


def _quote_samples(group: list[dict], words: tuple[str, ...], limit: int = 3) -> list[dict]:
    candidates = [doc for doc in group if doc["kind"] == "comment" and 10 <= len(doc["text"]) <= 120] or [
        doc for doc in group if 10 <= len(doc["text"]) <= 160
    ]
    if not candidates:
        return []

    def rank(doc: dict) -> tuple:
        folded = doc["text"].casefold()
        hits = sum(1 for word in words if word.casefold() in folded)
        tone = _doc_tone(doc)
        tone_bonus = 1 if tone in {"pos", "neg"} else 0
        return (tone_bonus, hits, doc.get("likes") or 0, -abs(len(doc["text"]) - 50))

    out: list[dict] = []
    used: set[str] = set()
    for doc in sorted(candidates, key=rank, reverse=True):
        key = re.sub(r"\W+", "", doc["text"])[:36]
        if key in used:
            continue
        used.add(key)
        out.append(
            {
                "text": doc["text"][:118],
                "platform": doc["platform"],
                "where": doc.get("where") or "",
                "tone": _doc_tone(doc) or "neutral",
                "likes": doc.get("likes") or 0,
                "url": doc.get("url") or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def _drift(docs: list[dict], sources: list[dict]) -> dict:
    """每个平台和各平台等权平均比：表态多少、褒贬比例、各话题多谈或少谈几个百分点、这个平台特有的说法。"""
    platforms = [item["name"] for item in sources if item["count"] >= DRIFT_MIN]
    if len(platforms) < 2:
        return {"overall": {}, "rows": [], "lines": []}
    pool = [doc for doc in docs if doc["platform"] in platforms]
    groups = {platform: [doc for doc in pool if doc["platform"] == platform] for platform in platforms}
    profiles = {platform: _profile(group) for platform, group in groups.items()}
    overall = _average(list(profiles.values()))
    rows = []
    lines = []
    for platform in platforms:
        group = groups[platform]
        others = [doc for doc in pool if doc["platform"] != platform]
        profile = profiles[platform]
        offsets = [
            {
                "aspect": name,
                "share": profile["aspects"][name],
                "overall": overall["aspects"][name],
                "diff": profile["aspects"][name] - overall["aspects"][name],
            }
            for name, _words in ASPECTS
        ]
        row = {
            "platform": platform,
            "size": len(group),
            "pos": profile["pos"],
            "neg": profile["neg"],
            "net": profile["net"],
            "net_diff": profile["net"] - overall["net"],
            "opinion": profile["opinion"],
            "opinion_diff": profile["opinion"] - overall["opinion"],
            "lean": profile["lean"],
            "lean_n": profile["lean_n"],
            "lean_diff": None if profile["lean"] is None or overall["lean"] is None else profile["lean"] - overall["lean"],
            "stars": profile["stars"],
            "offsets": offsets,
            "distinctive": _distinctive(group, others),
        }
        rows.append(row)
        lines.append(_drift_line(row))
    return {"overall": overall, "rows": rows, "lines": lines}


def _profile(group: list[dict]) -> dict:
    size = max(len(group), 1)
    tones = Counter(_doc_tone(doc) for doc in group)
    pos = round(tones.get("pos", 0) * 100 / size)
    neg = round(tones.get("neg", 0) * 100 / size)
    judged = tones.get("pos", 0) + tones.get("neg", 0)
    starred = [doc["stars"] for doc in group if doc.get("stars")]
    return {
        "size": len(group),
        "pos": pos,
        "neg": neg,
        "net": pos - neg,
        "opinion": round(judged * 100 / size),
        "lean": round(tones.get("pos", 0) * 100 / judged) if judged >= LEAN_MIN else None,
        "lean_n": judged,
        "stars": round(sum(starred) / len(starred), 1) if starred else None,
        "aspects": {
            name: round(sum(1 for doc in group if _hits(doc, words)) * 100 / size)
            for name, words in ASPECTS
        },
    }


def _sentiment(docs: list[dict], sources: list[dict]) -> list[dict]:
    """情感条按平台自己的褒贬画。评论满 3 条就出现，不套平台偏移的 8 条门槛。"""
    rows = []
    for item in sources:
        if item["count"] < 3:
            continue
        group = [doc for doc in docs if doc["platform"] == item["name"]]
        profile = _profile(group)
        score = profile["lean"] if profile["lean"] is not None else max(0, min(100, 50 + profile["net"]))
        rows.append({"platform": item["name"], "score": score, "size": len(group)})
    return rows


def _average(profiles: list[dict]) -> dict:
    """各平台等权平均，免得评论最多的平台把基准拉向自己。"""

    def mean(values):
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values)) if values else None

    stars = [item["stars"] for item in profiles if item["stars"]]
    return {
        "size": sum(item["size"] for item in profiles),
        "platforms": len(profiles),
        "pos": mean(item["pos"] for item in profiles),
        "neg": mean(item["neg"] for item in profiles),
        "net": mean(item["net"] for item in profiles),
        "opinion": mean(item["opinion"] for item in profiles),
        "lean": mean(item["lean"] for item in profiles),
        "stars": round(sum(stars) / len(stars), 1) if stars else None,
        "aspects": {name: mean(item["aspects"][name] for item in profiles) for name, _words in ASPECTS},
    }


def _distinctive(group: list[dict], others: list[dict]) -> list[str]:
    inside: Counter[str] = Counter(term for doc in group for term in doc.get("terms") or ())
    outside: Counter[str] = Counter(term for doc in others for term in doc.get("terms") or ())
    size_in = max(len(group), 1)
    size_out = max(len(others), 1)
    scored = []
    for term, count in inside.items():
        if count < 2:
            continue
        ratio = ((count + 0.5) / size_in) / ((outside.get(term, 0) + 0.5) / size_out)
        if ratio >= 2:
            scored.append((ratio * math.log1p(count), term))
    return [term for _score, term in sorted(scored, reverse=True)[:6]]


def _drift_line(row: dict) -> str:
    line = f"{row['platform']}（{row['size']} 条）有明确褒贬的评论占 {row['opinion']}%，{_versus(row['opinion'], row['opinion_diff'])}"
    if row["lean"] is None:
        line += f"；表态的只有 {row['lean_n']} 条，不判断偏正面还是偏负面。"
    elif row["lean_diff"] is None or abs(row["lean_diff"]) < 5:
        line += f"；表态的 {row['lean_n']} 条里褒义占 {row['lean']}%，褒贬比例和各平台平均差不多。"
    else:
        lean = "更偏正面" if row["lean_diff"] > 0 else "更偏负面"
        line += f"；表态的 {row['lean_n']} 条里褒义占 {row['lean']}%，{_versus(row['lean'], row['lean_diff'])}，{lean}。"
    if row["lean"] is not None and row["lean_n"] < 15:
        line += "表态条数少，褒贬比例只作参考。"
    if row["stars"]:
        line += f"带星级的评论平均 {row['stars']} 星。"
    more = [item for item in sorted(row["offsets"], key=lambda item: item["diff"], reverse=True) if item["diff"] >= 4][:2]
    less = [item for item in sorted(row["offsets"], key=lambda item: item["diff"]) if item["diff"] <= -4][:2]
    if more:
        line += "多谈" + "、".join(f"{item['aspect']}（+{item['diff']}）" for item in more)
        line += "，" if less else "。"
    if less:
        line += "少谈" + "、".join(f"{item['aspect']}（{item['diff']}）" for item in less) + "。"
    if row["distinctive"]:
        line += f"这里特有的说法：{'、'.join(row['distinctive'][:4])}。"
    return line


def _versus(value: int, diff: int) -> str:
    base = value - diff
    if abs(diff) < 3:
        return f"和各平台平均（{base}%）差不多"
    return f"比各平台平均（{base}%）{'高' if diff > 0 else '低'} {abs(diff)} 个百分点"


def _tone_counts(docs: list[dict]) -> dict:
    counts = Counter(_doc_tone(doc) for doc in docs)
    return {"pos": counts.get("pos", 0), "neg": counts.get("neg", 0), "total": len(docs)}


def _aspect_words(name: str) -> tuple[str, ...]:
    for aspect, words in ASPECTS:
        if aspect == name:
            return words
    return ()


def _listener_analysis(
    docs: list[dict],
    sources: list[dict],
    aspects: list[dict],
    tone: dict,
    drift: dict,
    ratings: list[dict],
) -> dict:
    """把评论整理成主题、争议和分平台解读，供 AI 乐评消化，而不是堆百分比。"""
    themes = []
    for item in aspects:
        if item["name"] in {"赞誉", "批评"}:
            continue
        words = _aspect_words(item["name"])
        group = [doc for doc in docs if _hits(doc, words)] if words else []
        voices = item.get("quotes") or []
        if not voices and group:
            voices = _quote_samples(group, words, limit=3)
        cue_text = "、".join(item.get("cues") or [])[:40]
        narrative = f"关于{item['name']}，听众反复提到{cue_text or '相关细节'}。"
        if voices:
            sample = "；".join(f"「{v['text'][:56]}…」({v['platform']})" if len(v["text"]) > 56 else f"「{v['text']}」({v['platform']})" for v in voices[:2])
            narrative += f" 例如 {sample}。"
        themes.append({"topic": item["name"], "narrative": narrative, "voices": voices[:4]})

    controversies = []
    neg_docs = sorted(
        [doc for doc in docs if _doc_tone(doc) == "neg" and doc["kind"] == "comment"],
        key=lambda doc: doc.get("likes") or 0,
        reverse=True,
    )
    for neg in neg_docs[:4]:
        hit = next((name for name, words in ASPECTS if _hits(neg, words)), "批评")
        counter = None
        for pos in sorted(
            [doc for doc in docs if _doc_tone(doc) == "pos" and _hits(doc, _aspect_words(hit))],
            key=lambda doc: doc.get("likes") or 0,
            reverse=True,
        ):
            if re.sub(r"\W+", "", pos["text"])[:30] != re.sub(r"\W+", "", neg["text"])[:30]:
                counter = pos
                break
        controversies.append(
            {
                "issue": hit,
                "critical": neg["text"][:120],
                "critical_platform": neg["platform"],
                "supportive": (counter["text"][:120] if counter else ""),
                "supportive_platform": (counter["platform"] if counter else ""),
            }
        )

    platform_views = []
    for row in (drift or {}).get("rows") or []:
        plat = row["platform"]
        group = [doc for doc in docs if doc["platform"] == plat]
        voices = _quote_samples(group, (), limit=4) if group else []
        if not voices and group:
            voices = [
                {"text": doc["text"][:100], "platform": plat, "tone": _doc_tone(doc) or "neutral", "likes": doc.get("likes") or 0}
                for doc in sorted(group, key=lambda d: d.get("likes") or 0, reverse=True)[:3]
            ]
        more = sorted(
            (item for item in row.get("offsets") or [] if item.get("diff", 0) >= 4),
            key=lambda item: item["diff"],
            reverse=True,
        )
        less = sorted((item for item in row.get("offsets") or [] if item.get("diff", 0) <= -4), key=lambda item: item["diff"])
        focus = ""
        if more:
            focus = "更常谈" + "、".join(item["aspect"] for item in more[:2])
        if less:
            focus += ("；" if focus else "") + "较少谈" + "、".join(item["aspect"] for item in less[:2])
        stance = _platform_stance(row)
        distinctive = "、".join(row.get("distinctive") or [])[:60]
        platform_views.append(
            {
                "platform": plat,
                "stance": stance,
                "focus": focus or "话题分布和全体接近",
                "distinctive": distinctive,
                "voices": voices[:4],
            }
        )

    sample_voices = []
    for name, _ in [(s["name"], s["count"]) for s in sources if s["count"] >= 5][:4]:
        group = [doc for doc in docs if doc["platform"] == name]
        sample_voices.extend(_quote_samples(group, (), limit=2))

    paragraphs = _analysis_paragraphs(themes, controversies, platform_views, tone, ratings, drift)
    return {
        "themes": themes[:6],
        "controversies": controversies[:4],
        "platform_views": platform_views,
        "sample_voices": sample_voices[:12],
        "paragraphs": paragraphs,
        "reading": "\n\n".join(paragraphs),
    }


def _platform_stance(row: dict) -> str:
    parts = []
    if row.get("opinion") is not None:
        if row["opinion"] >= 40:
            parts.append("评论里愿意明确表态的人较多")
        elif row["opinion"] <= 15:
            parts.append("多数评论在聊细节，很少直接下判断")
    if row.get("lean") is not None and row.get("lean_n", 0) >= 8:
        if row["lean"] >= 70:
            parts.append("表态的以褒义为主")
        elif row["lean"] <= 45:
            parts.append("表态里分歧更大，批评声不弱")
    if row.get("stars"):
        parts.append(f"带星级评论平均约 {row['stars']} 星")
    return "；".join(parts) if parts else "语气和全体接近"


def _analysis_paragraphs(
    themes: list[dict],
    controversies: list[dict],
    platform_views: list[dict],
    tone: dict,
    ratings: list[dict],
    drift: dict,
) -> list[str]:
    lines: list[str] = []
    if themes:
        lead = themes[0]
        second = themes[1]["topic"] if len(themes) > 1 else ""
        lines.append(
            f"听众讨论最集中的是「{lead['topic']}」"
            + (f"，其次常提到「{second}」。" if second else "。")
            + (f" {lead['narrative']}" if lead.get("narrative") else "")
        )
    if platform_views:
        bits = []
        for view in platform_views[:3]:
            bit = f"{view['platform']}：{view['stance']}，{view['focus']}"
            if view.get("distinctive"):
                bit += f"，常出现「{view['distinctive']}」等说法"
            bits.append(bit)
        lines.append("各平台关注点并不相同：" + "。".join(bits) + "。")
    if controversies:
        item = controversies[0]
        line = f"争议集中在{item['issue']}附近：有人批评「{item['critical'][:70]}」"
        if item.get("supportive"):
            line += f"，也有人从另一角度回应「{item['supportive'][:70]}」"
        lines.append(line + "。")
    elif tone.get("neg", 0) == 0 and tone.get("pos", 0):
        lines.append("这批评论里直接批评较少，褒义和具体细节描述占上风，但也要注意网易云等平台可能少表态、只聊梗的情况。")
    rated = [item for item in ratings if item.get("score")]
    if rated:
        bits = "、".join(f"《{item['name']}》{item['score']} 分" for item in rated[:2])
        lines.append(f"豆瓣条目侧：{bits}，长评短评里对制作、叙事和「值不值得听」的判断往往比单条热评更完整。")
    drift_lines = (drift or {}).get("lines") or []
    if drift_lines and len(lines) < 4:
        lines.append("平台之间还可对照：" + drift_lines[0].split("。", 1)[0] + "。")
    return [line for line in lines if line.strip()][:5]


def _findings(
    docs: list[dict],
    sources: list[dict],
    heat: dict,
    aspects: list[dict],
    tone: dict,
    ratings: list[dict],
) -> list[str]:
    lines = []
    parts = [f"{item['name']} {item['count']} 条{item['kind']}" for item in sources]
    lines.append(f"共收集 {len(docs)} 条：{'，'.join(parts)}。")
    rated = [item for item in ratings if item.get("score")]
    if rated:
        bits = "；".join(f"《{item['name']}》{item['score']} 分（{item['votes']} 人）" for item in rated)
        starred = [doc["stars"] for doc in docs if doc.get("stars")]
        line = f"豆瓣评分：{bits}。"
        if starred:
            high = sum(1 for star in starred if star >= 4)
            low = sum(1 for star in starred if star <= 2)
            line += f"带星级的短评和长评 {len(starred)} 条，四星以上 {round(high * 100 / len(starred))}%，两星以下 {round(low * 100 / len(starred))}%。"
        lines.append(line)
    topical = [item for item in aspects if item["name"] not in {"赞誉", "批评"}]
    if topical:
        top = topical[0]
        line = f"听众谈得最多的是{top['name']}，{top['share']}% 的评论提到，常见说法有{'、'.join(top['cues'][:4])}。"
        if len(topical) > 1:
            second = topical[1]
            line += f"其次是{second['name']}（{second['share']}%）。"
        lines.append(line)
    for platform, row in zip(heat.get("rows") or [], heat.get("cells") or []):
        ranked = sorted(
            ((heat["cols"][index], cell["share"]) for index, cell in enumerate(row) if heat["cols"][index] not in {"赞誉", "批评"}),
            key=lambda item: item[1],
            reverse=True,
        )
        if ranked and ranked[0][1] > 0:
            lines.append(f"{platform}上最集中的话题是{ranked[0][0]}（{ranked[0][1]}%）。")
    if tone["pos"] or tone["neg"]:
        line = f"带明显褒义词的评论 {tone['pos']} 条，带贬义词的 {tone['neg']} 条。"
        if tone["neg"] == 0:
            line += "这批评论里没有看到批评。"
        elif tone["neg"] * 3 >= tone["pos"]:
            line += "批评的比重不低，值得点开看争议在哪。"
        lines.append(line)
    return lines


def _empty(note: str) -> dict:
    return {
        "ok": True,
        "posts": 0,
        "words": [],
        "platform_clouds": [],
        "sources": [],
        "heat": {"rows": [], "cols": [], "cells": [], "sizes": []},
        "aspects": [],
        "tone": {"pos": 0, "neg": 0, "total": 0},
        "drift": {"overall": {}, "rows": [], "lines": []},
        "sentiment": [],
        "ratings": [],
        "links": [],
        "findings": [],
        "analysis": {"themes": [], "controversies": [], "platform_views": [], "sample_voices": [], "paragraphs": [], "reading": ""},
        "reading": "",
        "note": note,
    }
