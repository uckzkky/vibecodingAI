"""技能共用的 HTTP 客户端和链接检查。"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

USER_AGENT = "VibeCodingMusicAgent/0.1 (coursework; single-song research)"

VIDEO_HOSTS = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "bilibili.com",
    "b23.tv",
    "music.163.com",
    "163cn.tv",
    "y.qq.com",
    "v.qq.com",
    "youku.com",
    "iqiyi.com",
)
SEARCH_HOSTS = (
    "duckduckgo.com",
    "google.com",
    "bing.com",
    "sogou.com",
)
EXACT_SEARCH_HOSTS = ("baidu.com", "www.baidu.com", "m.baidu.com")

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
        )
    return _client


def tool_error(message: str, **extra: object) -> dict:
    body: dict = {"ok": False, "error": message}
    body.update(extra)
    return body


def is_public_http(url: str) -> bool:
    try:
        assert_public_url(url)
        return True
    except ValueError:
        return False


def assert_public_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只接受 http(s) 链接")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise ValueError("拒绝本地地址")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    ):
        raise ValueError("拒绝内网地址")
    return parsed.geturl()


def unwrap_result_url(url: str) -> str:
    candidate = (url or "").strip()
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    parsed = urlparse(candidate)
    target = parse_qs(parsed.query).get("uddg", [])
    if target:
        candidate = unquote(target[0])
    return candidate


REFERENCE_HOSTS = (
    "wikipedia.org",
    "wikimedia.org",
    "baike.baidu.com",
    "baike.sogou.com",
    "baike.com",
    "chinesemusicwiki.com",
    "douban.com",
)
REFERENCE_TITLE_MARKERS = ("百度百科", "维基百科", "搜狗百科", "维基文库")


def is_reference_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return _host_is(host, REFERENCE_HOSTS)


def is_reference_result(title: str, url: str) -> bool:
    if is_reference_host(url):
        return True
    return any(marker in (title or "") for marker in REFERENCE_TITLE_MARKERS)


def generic_title(title: str) -> bool:
    text = (title or "").strip()
    return bool(text) and (text.isdigit() or len(text) <= 2)


_YEAR_CONTEXT = re.compile(
    r"(born|january|february|march|april|may|june|july|august|september|october|november|december|"
    r"\bjan\b|\bfeb\b|\bmar\b|\bapr\b|\bjun\b|\bjul\b|\baug\b|\bsep\b|\boct\b|\bnov\b|\bdec\b|"
    r"年|月|copyright|©|released|出生)",
    re.IGNORECASE,
)


def song_mentioned(blob: str, song: str) -> bool:
    """年份歌名不把出生日期、发行日期里的数字当成曲名。"""
    if not song or not blob:
        return False
    if not generic_title(song):
        return song.casefold() in blob.casefold()
    for match in re.finditer(re.escape(song), blob, flags=re.IGNORECASE):
        window = blob[max(0, match.start() - 18) : match.end() + 6]
        if _YEAR_CONTEXT.search(window):
            continue
        return True
    return False


def topic_match(blob: str, song: str, artist: str, section_id: str = "") -> bool:
    """歌名太短或只是年份时，必须同时出现艺人名，避免对上任意一年的页面。"""
    folded = (blob or "").lower()
    song_hit = song_mentioned(blob or "", song)
    artist_hit = bool(artist) and artist.lower() in folded
    if section_id == "interview":
        return artist_hit
    if section_id == "background" and generic_title(song):
        return artist_hit and (song_hit or ("专辑" in (blob or "") and song.casefold() in folded))
    if generic_title(song) or section_id == "samples":
        return song_hit and artist_hit
    return song_hit or artist_hit


def host_kind(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if _host_is(host, VIDEO_HOSTS):
        return "video"
    if host in EXACT_SEARCH_HOSTS or _host_is(host, SEARCH_HOSTS):
        return "search"
    return "web"


def _host_is(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)
