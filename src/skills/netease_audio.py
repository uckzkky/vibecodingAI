"""从网易云歌曲链接取播放器返回的录音。只听它给出的地址，不绕过会员。"""

from __future__ import annotations

import base64
import json
import random
import re
import string
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

PRESET_KEY = b"0CoJUm6Qyw8W8jud"
IV = b"0102030405060708"
RSA_PUBKEY = 0x010001
RSA_MODULUS = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7"
    "b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280"
    "104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932"
    "575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b"
    "3ece0462db0a22b8e7",
    16,
)
PLAYER_API = "https://music.163.com/weapi/song/enhance/player/url/v1?csrf_token="
DETAIL_API = "https://music.163.com/api/song/detail/"
MAX_BYTES = 4_000_000
SONG_ID = re.compile(r"(?:[?&#]id=|/song/)(\d{4,})")
HOSTS = {"music.163.com", "y.music.163.com", "163cn.tv", "www.163cn.tv"}


def fetch_netease_audio(page_url: str, folder: Path) -> dict:
    """把歌曲页解析成临时音频文件。失败时 path 为空，note 写给报告。"""
    raw = (page_url or "").strip()
    if not raw:
        return _fail("没有音频。采样和乐器只由模型听录音判断，这次没有检索网页。", "没有网易云链接")
    if not _is_netease(raw):
        return _fail("这不是网易云歌曲链接。采样和乐器只听网易云播放器返回的录音，这次没有检索网页。", "链接不是网易云")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
    }
    try:
        with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client:
            song_id = _song_id(client, raw)
            if not song_id:
                return _fail("这个链接里没有网易云歌曲编号。", "链接里没有歌曲编号")
            title, artist = _detail(client, song_id)
            play_url = _player_url(client, song_id)
            if not play_url:
                return _fail(
                    "网易云没有返回可播放的录音，常见原因是会员专享或已下架。采样和乐器不改去检索网页。",
                    "网易云没有返回录音",
                    song_id=song_id,
                    title=title,
                    artist=artist,
                )
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{uuid.uuid4().hex}.mp3"
            size = _download(client, play_url, path)
    except Exception as exc:
        return _fail(f"读取网易云录音失败：{type(exc).__name__}。这次没有改去检索网页。", "读取网易云录音失败")
    if size < 10_000:
        path.unlink(missing_ok=True)
        return _fail("网易云返回的录音太短，听不了。这次没有改去检索网页。", "网易云录音太短", song_id=song_id)
    source = _source_line(song_id, title, artist)
    return {
        "ok": True,
        "path": str(path),
        "song_id": song_id,
        "title": title,
        "artist": artist,
        "source": source,
        "note": "",
        "status": "已从网易云拿到录音",
    }


def _fail(note: str, status: str, song_id: str = "", title: str = "", artist: str = "") -> dict:
    return {
        "ok": False,
        "path": "",
        "song_id": song_id,
        "title": title,
        "artist": artist,
        "source": "",
        "note": note,
        "status": status,
    }


def _is_netease(url: str) -> bool:
    if "://" not in url:
        url = "https://" + url
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host in HOSTS or host.endswith(".music.163.com")


def _song_id(client: httpx.Client, url: str) -> str:
    matched = SONG_ID.search(url)
    if matched:
        return matched.group(1)
    if "://" not in url:
        url = "https://" + url
    response = client.get(url)
    matched = SONG_ID.search(str(response.url)) or SONG_ID.search(response.text[:4000])
    return matched.group(1) if matched else ""


def _detail(client: httpx.Client, song_id: str) -> tuple[str, str]:
    try:
        response = client.get(DETAIL_API, params={"id": song_id, "ids": f"[{song_id}]"})
        payload = response.json()
    except Exception:
        return "", ""
    songs = payload.get("songs") or []
    if not songs:
        return "", ""
    song = songs[0]
    artists = song.get("artists") or []
    artist = "、".join(item.get("name") or "" for item in artists if item.get("name"))
    return (song.get("name") or "").strip(), artist


def _player_url(client: httpx.Client, song_id: str) -> str:
    body = {
        "ids": json.dumps([int(song_id)]),
        "level": "standard",
        "encodeType": "mp3",
        "csrf_token": "",
    }
    response = client.post(PLAYER_API, data=_weapi(body))
    payload = response.json()
    rows = payload.get("data") or []
    if not rows:
        return ""
    row = rows[0]
    url = (row.get("url") or "").strip()
    if row.get("code") not in (200, None) or not url.startswith("http"):
        return ""
    if row.get("freeTrialInfo") and not row.get("url"):
        return ""
    return url


def _download(client: httpx.Client, url: str, path: Path) -> int:
    size = 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        kind = (response.headers.get("content-type") or "").lower()
        if "text/html" in kind or "json" in kind:
            raise ValueError("播放地址不是音频")
        with path.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
                size += len(chunk)
                if size >= MAX_BYTES:
                    break
    return size


def _source_line(song_id: str, title: str, artist: str) -> str:
    label = f"《{title}》" if title else f"歌曲 {song_id}"
    who = f"（{artist}）" if artist else ""
    return f"这段录音来自网易云{label}{who}，编号 {song_id}。"


def _weapi(data: dict) -> dict:
    text = json.dumps(data, separators=(",", ":"))
    secret = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
    params = _aes_b64(_aes_b64(text, PRESET_KEY), secret.encode("utf-8"))
    return {"params": params, "encSecKey": _rsa(secret)}


def _aes_b64(text: str, key: bytes) -> str:
    cipher = AES.new(key, AES.MODE_CBC, IV)
    encrypted = cipher.encrypt(pad(text.encode("utf-8"), 16))
    return base64.b64encode(encrypted).decode("utf-8")


def _rsa(secret: str) -> str:
    number = int(secret[::-1].encode("utf-8").hex(), 16)
    return format(pow(number, RSA_PUBKEY, RSA_MODULUS), "x").zfill(256)
