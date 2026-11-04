"""单曲资料的编排循环。

这一层决定查哪些方面、打开哪些链接、要不要把材料交给模型。
网页检索、正文抽取、MusicBrainz、Discogs 和乐评分析的请求细节在 src/skills。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

SRC_DIR = Path(__file__).resolve().parent
REPO_DIR = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from skills.common import host_kind, is_public_http, is_reference_host, topic_match
from skills.registry import TOOLS, call_tool, openai_tools
from report import present_bundle, render_appendix, render_digest, save_report, slim_context

PROMPT_PATH = SRC_DIR / "prompt.md"
REVIEW_PROMPT_PATH = SRC_DIR / "review_prompt.md"

LOW_VALUE_HOSTS = (
    "kotobank.jp",
    "weblio.jp",
    "meaning-dictionary.com",
    "musescore.com",
)
PREFERRED_HOSTS = {
    "background": ("bandcamp.com", "genius.com", "discogs.com", "bilibili.com", "douban.com"),
    "interview": ("bilibili.com", "douban.com", "thequietus.com", "bandcamp.com"),
    "mv": ("youtube.com", "bilibili.com", "bandcamp.com"),
}

SECTION_SPECS = [
    {
        "id": "background",
        "title": "创作理念",
        "fetch": 1,
        "queries": [
            '"{artist}" "{song}" 创作理念 OR 灵感 OR 作词',
            '"{artist}" 创作 理念 OR 灵感 OR 幕后',
            '"{artist}" "{song}" site:bandcamp.com',
        ],
        "keywords": ["创作", "理念", "灵感", "作词", "幕后", "inspired", "background", "about"],
    },
    {
        "id": "interview",
        "title": "采访",
        "fetch": 1,
        "queries": [
            '"{artist}" 专访',
            '"{artist}" "{song}" 采访 OR 访谈',
            '"{artist}" 专访 OR 访谈 site:bilibili.com',
            '"{artist}" interview site:thequietus.com OR site:bandcamp.com',
        ],
        "keywords": ["采访", "专访", "访谈", "受访", "对话", "interview"],
    },
    {
        "id": "mv",
        "title": "MV 与官方影像",
        "fetch": 0,
        "queries": [
            '"{artist}" "{song}" 官方 MV OR official music video',
            '"{artist}" "{song}" site:bilibili.com MV',
        ],
        "keywords": [],
    },
]


def main() -> None:
    _utf8_stdio()
    _load_dotenv(REPO_DIR / ".env")
    args = _parse_args()
    song = (args.song or input("歌名：")).strip()
    artist = (args.artist or input("歌手：")).strip()
    if not song or not artist:
        raise SystemExit("歌名和歌手都要填。")

    print(f"正在检索：{song} / {artist}", flush=True)
    trace: list[dict] = []
    bundle = research(song, artist, trace, audio_path=args.audio or "")
    cfg = None if args.no_llm else llm_config()
    if cfg is None:
        if args.no_llm:
            print("按参数跳过综述模型。", flush=True)
        else:
            print("未配置综述模型，改为输出工具摘录。", flush=True)
        body = render_digest(bundle, trace)
    else:
        print(f"综述模型：{cfg['model']}（{cfg['label']}）", flush=True)
        try:
            written = summarize(bundle, cfg, trace)
            body = written.rstrip() + "\n\n" + render_appendix(bundle, trace)
        except Exception as exc:
            print(f"模型调用失败，改为工具摘录：{exc}", flush=True)
            body = render_digest(bundle, trace)

    path = save_report(song, artist, body, bundle)
    print(f"报告已写入 {path}", flush=True)


def answer_query(
    song: str,
    artist: str,
    on_status=None,
    audio_path: str = "",
    listen_note: str = "",
    audio_source: str = "",
    label: str = "",
) -> dict:
    """给网页用：检索、能调用模型就写成综述，并返回可渲染的报告。"""
    _load_dotenv(REPO_DIR / ".env")
    trace: list[dict] = []
    bundle = research(
        song,
        artist,
        trace,
        on_status=on_status,
        audio_path=audio_path,
        listen_note=listen_note,
        audio_source=audio_source,
        label=label,
    )
    ai_review = {"text": "", "note": ""}
    cfg = llm_config()
    if cfg:
        if on_status:
            on_status("正在用 AI 写乐评")
        try:
            ai_review["text"] = write_ai_review(bundle, cfg).strip()
        except Exception as exc:
            ai_review["note"] = f"乐评没有生成：{_review_error(exc)}"
            if on_status:
                on_status(f"AI 乐评失败：{_review_error(exc, short=True)}")
    else:
        ai_review["note"] = (
            "未检测到综述模型。在项目根目录 .env 配置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY 后，"
            "会自动根据检索材料写一段 AI 乐评。"
        )
    bundle["ai_review"] = ai_review
    view = present_bundle(bundle)
    body = render_digest(bundle, trace)
    view["saved"] = save_report(song, artist, body, bundle).name
    return view


def research(
    song: str,
    artist: str,
    trace: list[dict],
    on_status=None,
    audio_path: str = "",
    listen_note: str = "",
    audio_source: str = "",
    label: str = "",
) -> dict:
    def say(text: str) -> None:
        if on_status:
            on_status(text)
        else:
            print(text, flush=True)

    bundle = {
        "song": song,
        "artist": artist,
        "label": label,
        "musicbrainz": {},
        "discogs": {},
        "criticism": {},
        "videos": {},
        "sections": [],
        "pages": [],
    }
    say("正在读网易云、豆瓣和哔哩哔哩的听众评论")
    bundle["cloud"] = _call("social_cloud", {"title": song, "artist": artist, "label": label}, trace)
    cloud = bundle["cloud"] or {}
    where = "，".join(f"{item['name']} {item['count']} 条" for item in cloud.get("sources") or [])
    longform = int((cloud or {}).get("longform") or 0)
    say(
        f"听众评论 {cloud.get('posts') or 0} 条"
        + (f"（{where}）" if where else "")
        + (f"，其中长评 {longform} 条" if longform else "")
    )
    say("正在找哔哩哔哩乐评博主")
    videos = _call("find_video_reviews", {"title": song, "artist": artist, "label": label}, trace)
    bundle["videos"] = videos
    say(f"哔哩哔哩等影音测评 {len((videos or {}).get('items') or [])} 条")
    if song and artist:
        say("正在查厂牌和录音目录")
        bundle["musicbrainz"] = _call("lookup_musicbrainz", {"title": song, "artist": artist}, trace)
        bundle["discogs"] = _call("lookup_discogs", {"title": song, "artist": artist}, trace)
        say(f"录音资料 {len((bundle['musicbrainz'] or {}).get('recordings') or [])} 条")
        say(f"Discogs 发行 {len((bundle['discogs'] or {}).get('releases') or [])} 条")
    else:
        if label and artist:
            say(f"这次按「{label}」厂牌的 {artist} 检索。没有歌名，不查录音目录")
        else:
            say("这次只给了歌手或歌曲其中一项，不查厂牌和录音目录")
    bundle["sections"].append(_video_section(videos))
    seen: set[str] = set()
    for spec in SECTION_SPECS:
        found: list[dict] = []
        for template in spec["queries"]:
            if "{song}" in template and not song:
                continue
            if "{artist}" in template and not artist:
                continue
            query = template.replace("{song}", song).replace("{artist}", artist)
            if label:
                query = f"{query} {label}"
            result = _call("search_web", {"query": query, "max_results": 5}, trace)
            found.extend(result.get("results") or [])
        found = [row for row in _unique(found) if _keep_row(row, spec, song, artist)]
        if spec["id"] == "interview" and not found:
            extra = f"{song} {artist} 专访"
            extra_result = _call("search_web", {"query": extra, "max_results": 5}, trace)
            found.extend(extra_result.get("results") or [])
            found = [row for row in _unique(found) if _keep_row(row, spec, song, artist)]
        found = _ranked(found, spec, song, artist)[:6]
        bundle["sections"].append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "keywords": spec["keywords"],
                "results": found,
            }
        )
        say(f"检索{spec['title']}，{len(found)} 条")
        opened = 0
        for row in found:
            if opened >= spec["fetch"] or len(bundle["pages"]) >= 8:
                break
            url = row.get("url") or ""
            if url in seen or not _can_fetch(url) or not _on_topic(row, spec, song, artist):
                continue
            say("正在阅读一篇文章")
            page = _call("fetch_page", {"url": url, "max_chars": 6000}, trace)
            text = (page.get("text") or "").strip()
            blob = f"{page.get('title') or ''}\n{text[:2500]}"
            if len(text) < 80 or not topic_match(blob, song, artist, spec["id"]):
                say("这页对不上，换下一篇")
                continue
            page["section"] = spec["id"]
            bundle["pages"].append(page)
            seen.add(url)
            opened += 1
    _attach_catalog_notes(bundle)
    _promote_video_interviews(bundle)
    order = ["video", "background", "interview", "mv"]
    bundle["sections"].sort(key=lambda section: order.index(section["id"]) if section["id"] in order else 99)
    return bundle


def _promote_video_interviews(bundle: dict) -> None:
    """影音测评里标成访谈的条目，同步进「采访」，方便和文字专访对照。"""
    video = next((section for section in bundle.get("sections") or [] if section.get("id") == "video"), None)
    interview = next((section for section in bundle.get("sections") or [] if section.get("id") == "interview"), None)
    if not video or not interview:
        return
    seen = {(row.get("url") or "") for row in interview.get("results") or []}
    for row in video.get("results") or []:
        if row.get("role") != "interview":
            continue
        url = row.get("url") or ""
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        interview.setdefault("results", []).append(
            {
                "title": row.get("title") or url,
                "url": url,
                "snippet": row.get("snippet") or "来自影音测评中的访谈视频。",
            }
        )


def _video_section(videos: dict) -> dict:
    results = []
    seen: set[str] = set()
    for item in (videos or {}).get("items") or []:
        url = item.get("url") or ""
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        role = item.get("role") or "review"
        label = {"review": "测评", "interview": "访谈", "other": "相关"}.get(role, "相关")
        author = item.get("author") or item.get("platform") or ""
        summary = (item.get("summary") or "").strip()
        snippet = f"[{item.get('platform') or '视频'} · {label}] {author}"
        if summary:
            snippet += f"：{summary}"
        else:
            snippet += "。只有标题可核对，没有读到口播全文。"
        results.append(
            {
                "title": item.get("title") or url or "影音测评",
                "url": url,
                "snippet": snippet,
                "platform": item.get("platform") or "",
                "role": role,
                "host": item.get("platform") or "",
                "kind": "video",
            }
        )
    note = (videos or {}).get("note") or "没有找到哔哩哔哩乐评或豆瓣、网易云长评。"
    findings = (videos or {}).get("findings") or []
    return {
        "id": "video",
        "title": "影音测评",
        "keywords": ["乐评", "测评", "解析"],
        "results": results,
        "analysis": False,
        "video": True,
        "findings": findings,
        "note": note if results else (findings[0] if findings else note),
    }


def _review_section(criticism: dict) -> dict:
    results = []
    for item in criticism.get("dimensions") or []:
        reading = (item.get("reading") or "").strip()
        if not reading:
            continue
        results.append(
            {
                "title": item.get("name") or "乐评",
                "url": item.get("url") or "",
                "snippet": reading,
            }
        )
    return {
        "id": "review",
        "title": "专业乐评",
        "keywords": ["乐评"],
        "results": results,
        "analysis": True,
        "note": criticism.get("note") or "没有找到专业乐评。",
    }


def _listen_sections(heard: dict) -> list[dict]:
    note = heard.get("note") or "音频模型没有返回结果。"
    sections = []
    for key, title, reading_key in (
        ("samples", "采样来源", "sample_reading"),
        ("instruments", "乐器", "instrument_reading"),
    ):
        reading = (heard.get(reading_key) or "").strip()
        results = []
        if reading:
            results.append({"title": heard.get("model") or "音频模型", "url": "", "snippet": reading})
        sections.append(
            {
                "id": key,
                "title": title,
                "keywords": [],
                "results": results,
                "analysis": True,
                "note": "" if reading else note,
            }
        )
    return sections


def _audio_for(song: str, artist: str, audio_path: str) -> str:
    if audio_path and Path(audio_path).is_file():
        return audio_path
    folder = SRC_DIR / "data"
    if not folder.exists():
        return ""
    folded = f"{artist} {song}".casefold()
    tokens = [part for part in (artist.casefold(), song.casefold()) if part]
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".wav", ".flac", ".mp3", ".ogg"}:
            continue
        name = path.stem.casefold().replace("_", " ").replace("-", " ")
        if all(token in name for token in tokens) or folded in name:
            return str(path)
    return ""


def _attach_catalog_notes(bundle: dict) -> None:
    criticism = bundle.get("criticism") or {}
    background = next((section for section in bundle["sections"] if section["id"] == "background"), None)
    if background is None:
        return
    existing = {row.get("url") for row in background["results"]}
    for item in criticism.get("artist_statements") or []:
        if item.get("url") and item["url"] not in existing:
            background["results"].insert(0, item)


def _ranked(rows: list[dict], spec: dict, song: str, artist: str) -> list[dict]:
    return sorted(rows, key=lambda row: _score(row, spec, song, artist), reverse=True)


def _score(row: dict, spec: dict, song: str, artist: str) -> int:
    blob = f"{row.get('title') or ''} {row.get('snippet') or ''}"
    host = urlparse(row.get("url") or "").hostname or ""
    score = 0
    if song and song in blob:
        score += 4
    if artist and artist in blob:
        score += 4
    if any(host == name or host.endswith("." + name) for name in PREFERRED_HOSTS.get(spec["id"], ())):
        score += 6
    if any(host == name or host.endswith("." + name) for name in LOW_VALUE_HOSTS):
        score -= 12
    if spec["id"] == "mv" and any(word in blob.lower() for word in ("官方", "official")):
        score += 4
    return score


def _keep_row(row: dict, spec: dict, song: str, artist: str) -> bool:
    if is_reference_host(row.get("url") or "") or _messy_title(row.get("title") or ""):
        return False
    return _on_topic(row, spec, song, artist)


def _messy_title(title: str) -> bool:
    text = title or ""
    folded = text.casefold()
    if folded.count("youtube") >= 2:
        return True
    if text.count("…") + text.count("...") >= 3:
        return True
    return len(text) > 220 and (text.count("|") >= 3 or text.count("_") >= 4)


def _on_topic(row: dict, spec: dict, song: str, artist: str) -> bool:
    blob = f"{row.get('title') or ''} {row.get('snippet') or ''}"
    folded = blob.lower()
    host = urlparse(row.get("url") or "").hostname or ""
    if is_reference_host(row.get("url") or "") or _messy_title(row.get("title") or ""):
        return False
    if any(host == name or host.endswith("." + name) for name in LOW_VALUE_HOSTS):
        return False
    if not topic_match(blob, song, artist, spec["id"]):
        return False
    if spec["id"] == "samples":
        return "whosampled.com" in host or any(word in folded for word in ("sampled", "采样", "interpolation"))
    if spec["id"] == "interview":
        return any(word in folded for word in ("采访", "专访", "访谈", "受访", "interview"))
    return True


def _can_fetch(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if is_reference_host(url) or any(host == name or host.endswith("." + name) for name in LOW_VALUE_HOSTS):
        return False
    return host_kind(url) == "web" and is_public_http(url)


def _review_error(exc: Exception, short: bool = False) -> str:
    message = str(exc)
    if "402" in message or "Insufficient Balance" in message or "insufficient_balance" in message:
        return "DeepSeek 账户余额不足，请到 platform.deepseek.com 充值后再试。" if short else (
            "DeepSeek 账户余额不足（HTTP 402）。请到 https://platform.deepseek.com 充值或换有余额的 API Key，然后重启 web_app.py。"
        )
    if "401" in message or "Authentication" in message or "invalid_api_key" in message.lower():
        return "DeepSeek API Key 无效或已失效，请检查 .env 里的 DEEPSEEK_API_KEY。" if short else message
    if short and len(message) > 80:
        return message[:80] + "…"
    return message


def write_ai_review(bundle: dict, cfg: dict) -> str:
    """根据已检索材料写一段 AI 乐评，不再追加工具调用。"""
    context = _review_context(bundle)
    messages = [
        {"role": "system", "content": REVIEW_PROMPT_PATH.read_text(encoding="utf-8")},
        {
            "role": "user",
            "content": (
                f"歌手：{bundle.get('artist') or '（未指定）'}\n"
                f"歌名：{bundle.get('song') or '（未指定，可按歌手整体写）'}\n\n"
                "检索材料（JSON）：\n"
                + json.dumps(context, ensure_ascii=False)
            ),
        },
    ]
    final = _complete(cfg, messages, None, temperature=0.45, max_tokens=4096)
    content = (final.get("content") or "").strip()
    if not content:
        raise RuntimeError("模型没有返回乐评正文")
    return content


def _review_context(bundle: dict) -> dict:
    cloud = bundle.get("cloud") or {}
    videos = bundle.get("videos") or {}
    criticism = bundle.get("criticism") or {}
    pages = []
    for page in bundle.get("pages") or []:
        if not page.get("ok"):
            continue
        pages.append(
            {
                "section": page.get("section"),
                "title": page.get("title"),
                "url": page.get("url"),
                "text": (page.get("text") or "")[:1800],
            }
        )
    analysis = cloud.get("analysis") or {}
    return {
        "listeners": {
            "posts": cloud.get("posts"),
            "reading": cloud.get("reading") or "",
            "listener_analysis": {
                "themes": analysis.get("themes"),
                "controversies": analysis.get("controversies"),
                "platform_views": analysis.get("platform_views"),
                "paragraphs": analysis.get("paragraphs"),
            },
            "sample_voices": (analysis.get("sample_voices") or [])[:14],
            "drift_summary": (cloud.get("drift") or {}).get("lines"),
            "aspects": [
                {
                    "name": item.get("name"),
                    "quote": item.get("quote"),
                    "platform": item.get("platform"),
                    "quotes": item.get("quotes"),
                }
                for item in (cloud.get("aspects") or [])
                if item.get("quote") or item.get("quotes")
            ][:8],
            "ratings": cloud.get("ratings"),
        },
        "video_reviews": [
            {
                "platform": item.get("platform"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "role": item.get("role"),
            }
            for item in (videos.get("items") or [])[:10]
        ],
        "professional": {
            "found": criticism.get("found"),
            "agreement": criticism.get("agreement"),
            "dimensions": [
                {"name": item.get("name"), "reading": item.get("reading"), "url": item.get("url")}
                for item in (criticism.get("dimensions") or [])
            ],
            "note": criticism.get("note"),
        },
        "opened_pages": pages[:6],
        "musicbrainz": ((bundle.get("musicbrainz") or {}).get("recordings") or [])[:1],
        "discogs": ((bundle.get("discogs") or {}).get("releases") or [])[:1],
    }


def summarize(bundle: dict, cfg: dict, trace: list[dict]) -> str:
    page_chars = 1500 if cfg["label"] == "ollama" else 3500
    messages = [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {
            "role": "user",
            "content": (
                f"歌名：{bundle['song']}\n歌手：{bundle['artist']}\n\n"
                "下面是编排循环已经取回的材料。某一节为空、且还有未打开的文章链接时，再调用工具。"
                "否则直接写报告。\n\n"
                + json.dumps(slim_context(bundle, page_chars), ensure_ascii=False)
            ),
        },
    ]
    tools = openai_tools()
    for step in range(3):
        message = _complete(cfg, messages, tools if step < 2 else None)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            content = (message.get("content") or "").strip()
            if content:
                return content
            break
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            }
        )
        for call in tool_calls[:4]:
            name = call.get("function", {}).get("name") or ""
            raw_args = call.get("function", {}).get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                arguments = {}
            if name not in TOOLS:
                result = {"ok": False, "error": f"未知工具：{name}"}
            else:
                print(f"模型追加调用 {name}", flush=True)
                result = _call(name, arguments, trace)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "content": json.dumps(result, ensure_ascii=False)[:8000],
                }
            )
    final = _complete(cfg, messages, None)
    content = (final.get("content") or "").strip()
    if not content:
        raise RuntimeError("模型没有返回报告正文")
    return content


def llm_config() -> dict | None:
    if os.environ.get("OPENAI_API_KEY"):
        return {
            "base": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "key": os.environ["OPENAI_API_KEY"],
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "label": "openai",
        }
    if os.environ.get("DEEPSEEK_API_KEY"):
        return {
            "base": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            "key": os.environ["DEEPSEEK_API_KEY"],
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "label": "deepseek",
        }
    try:
        from skills.common import get_client

        response = get_client().get("http://127.0.0.1:11434/v1/models", timeout=1.5)
        if response.status_code == 200:
            models = response.json().get("data") or []
            name = os.environ.get("OLLAMA_MODEL") or (models[0]["id"] if models else "")
            if name:
                return {
                    "base": "http://127.0.0.1:11434/v1",
                    "key": "ollama",
                    "model": name,
                    "label": "ollama",
                }
    except Exception:
        return None
    return None


def _complete(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> dict:
    from skills.common import get_client

    payload: dict = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
    response = get_client().post(
        f"{cfg['base']}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:400]}")
    return response.json()["choices"][0]["message"]


def _call(name: str, arguments: dict, trace: list[dict]) -> dict:
    result = call_tool(name, arguments)
    trace.append({"tool": name, "ok": bool(result.get("ok"))})
    return result


def _unique(rows: list[dict]) -> list[dict]:
    chosen = []
    seen: set[str] = set()
    for row in rows:
        url = row.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        chosen.append(row)
    return chosen


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询一首歌的乐评、背景、采访、采样、乐器和影像")
    parser.add_argument("--song", "-s", help="歌名")
    parser.add_argument("--artist", "-a", help="歌手")
    parser.add_argument("--audio", help="本地音频。采样和乐器只听这个文件")
    parser.add_argument("--no-llm", action="store_true", help="只输出工具摘录，不调用综述模型")
    return parser.parse_args()


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    main()
