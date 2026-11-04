"""对话式网页。输入歌手或歌曲，返回乐评。"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import sys
import threading
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from loop import answer_query

PAGE = SRC_DIR / "web" / "index.html"
_YEAR = re.compile(r"\d{2,4}")


def parse_request(text: str) -> tuple[str, str, str, str]:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    while True:
        nxt = re.sub(r"^(请|帮我|帮忙|查一下|查询|介绍一下|介绍|看看|讲讲|分析一下|分析)\s*", "", raw)
        if nxt == raw:
            break
        raw = nxt.strip()
    raw = raw.strip(" ？?。！!")
    labeled = (
        (r"(.+?)厂牌旗下的(.+?)的[《「](.+?)[》」]$", True),
        (r"(.+?)旗下的(.+?)的[《「](.+?)[》」]$", True),
        (r"(.+?)厂牌的(.+?)的[《「](.+?)[》」]$", True),
        (r"(.+?)厂牌旗下的(.+)$", False),
        (r"(.+?)旗下的(.+)$", False),
        (r"(.+?)厂牌的(.+)$", False),
    )
    for pattern, has_song in labeled:
        matched = re.fullmatch(pattern, raw)
        if not matched:
            continue
        label = matched.group(1).strip(" 《》「」")
        artist = matched.group(2).strip(" 《》「」")
        song = matched.group(3).strip(" 《》「」") if has_song else ""
        if label and artist and artist != label:
            return song, artist, label, ""
    patterns = (
        (r"(.+?)的[《「](.+?)[》」]$", 2, 1),
        (r"[《「](.+?)[》」]\s*(?:by|[-—/|])\s*(.+)$", 1, 2),
        (r"[《「](.+?)[》」]\s+(.+)$", 1, 2),
        (r"(.+?)\s*(?:by|[-—/|])\s*(.+)$", 1, 2),
        (r"(.+?)的(.+)$", 2, 1),
    )
    for pattern, song_at, artist_at in patterns:
        matched = re.fullmatch(pattern, raw, flags=re.IGNORECASE)
        if not matched:
            continue
        song = matched.group(song_at).strip(" 《》「」")
        artist = matched.group(artist_at).strip(" 《》「」")
        if song and artist and song != artist:
            return song, artist, "", ""
    titled = re.fullmatch(r"[《「](.+?)[》」]", raw)
    if titled:
        song = titled.group(1).strip()
        if _YEAR.fullmatch(song):
            return "", "", "", "年份歌名请写成「歌手的《歌名》」。例如：lufive的《2008》。"
        return song, "", "", ""
    if _YEAR.fullmatch(raw):
        return "", "", "", "年份歌名请写成「歌手的《歌名》」。例如：lufive的《2008》。"
    if len(raw) > 80:
        return "", "", "", "写一个歌手，或写成「歌手的《歌名》」，厂牌用「厂牌旗下的歌手」。"
    parts = raw.split(" ")
    if len(parts) == 2 and all(parts):
        if _YEAR.fullmatch(parts[0]):
            return parts[0], parts[1], "", ""
        return parts[1], parts[0], "", ""
    return "", raw, "", ""


async def index(_: Request) -> FileResponse:
    return FileResponse(PAGE)


async def ask(request: Request) -> StreamingResponse:
    form = await request.form()
    song, artist, label, problem = parse_request(str(form.get("text") or ""))

    async def stream():
        if problem or not (song or artist):
            yield _event(
                {
                    "type": "need",
                    "text": problem or "写一个歌手，或写成「歌手的《歌名》」，厂牌用「厂牌旗下的歌手」。",
                }
            )
            return
        events: queue.Queue = queue.Queue()

        def worker() -> None:
            try:
                events.put({"type": "status", "text": _start_line(song, artist, label)})
                report = answer_query(
                    song,
                    artist,
                    label=label,
                    on_status=lambda text: events.put({"type": "status", "text": text}),
                )
                events.put({"type": "report", "report": report})
            except Exception as exc:
                events.put({"type": "error", "text": f"检索中断：{exc}"})
            finally:
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = await asyncio.to_thread(events.get)
            if item is None:
                break
            yield _event(item)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _start_line(song: str, artist: str, label: str = "") -> str:
    if label and song and artist:
        return f"开始查 {label} 厂牌的 {artist}《{song}》"
    if label and artist:
        return f"开始查 {label} 厂牌的 {artist}"
    if song and artist:
        return f"开始查 {artist} 的《{song}》"
    if artist:
        return f"开始查歌手 {artist} 的乐评"
    return f"开始查《{song}》的乐评"


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/ask", ask, methods=["POST"]),
    ]
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
