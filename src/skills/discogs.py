"""Discogs 发行目录：厂牌、风格、曲目和乐手署名。"""

from __future__ import annotations

import os
import time

import httpx

from skills.common import USER_AGENT, generic_title, get_client, tool_error
from skills.schemas import DiscogsArgs

API = "https://api.discogs.com"
_last_call = 0.0
INSTRUMENT_HINTS = (
    "guitar",
    "bass",
    "drum",
    "piano",
    "synth",
    "violin",
    "cello",
    "vocal",
    "sax",
    "trumpet",
    "flute",
    "organ",
    "keyboard",
    "percussion",
    "吉他",
    "贝斯",
    "鼓",
    "钢琴",
    "合成",
    "弦乐",
    "人声",
)


def lookup_discogs(title: str, artist: str) -> dict:
    args = DiscogsArgs(title=title, artist=artist)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.discogs.v2.discogs+json",
    }
    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    try:
        found = _search(args, headers, token)
    except httpx.HTTPStatusError as exc:
        return tool_error(f"Discogs HTTP {exc.response.status_code}", service="discogs")
    except Exception as exc:
        return tool_error(f"{type(exc).__name__}: {exc}", service="discogs")

    if not found:
        return {
            "ok": True,
            "query": f"{args.title} {args.artist}",
            "releases": [],
            "note": "Discogs 没有对上的发行。",
        }

    releases = []
    for item in found[:2]:
        try:
            detail = _release(item, headers, token)
        except httpx.HTTPStatusError:
            continue
        except Exception:
            continue
        if detail and _release_matches(detail, args.title, args.artist):
            releases.append(detail)
    if not releases:
        return {
            "ok": True,
            "query": f"{args.title} {args.artist}",
            "releases": [],
            "note": "Discogs 有检索命中，但发行页没有打开。",
        }
    return {"ok": True, "query": f"{args.title} {args.artist}", "releases": releases, "note": ""}


def _search(args: DiscogsArgs, headers: dict, token: str) -> list[dict]:
    precise = _query(
        {"artist": args.artist, "track": args.title, "type": "release", "per_page": 5},
        headers,
        token,
    )
    matched = [item for item in precise if _search_score(item, args.title, args.artist) > 0]
    if matched:
        return matched
    broad = _query(
        {"q": f"{args.artist} {args.title}", "type": "release", "per_page": 8},
        headers,
        token,
    )
    return [item for item in broad if _search_score(item, args.title, args.artist) > 0]


def _query(params: dict, headers: dict, token: str) -> list[dict]:
    if token:
        params = {**params, "token": token}
    _pace()
    response = get_client().get(f"{API}/database/search", params=params, headers=headers)
    if response.status_code in {429, 503}:
        time.sleep(2)
        _pace()
        response = get_client().get(f"{API}/database/search", params=params, headers=headers)
    response.raise_for_status()
    return list(response.json().get("results") or [])


def _release(item: dict, headers: dict, token: str) -> dict | None:
    resource = item.get("resource_url") or ""
    if not resource and item.get("id"):
        resource = f"{API}/releases/{item['id']}"
    if not resource:
        return None
    params = {"token": token} if token else None
    _pace()
    response = get_client().get(resource, params=params, headers=headers)
    response.raise_for_status()
    return _summarize(response.json())


def _summarize(release: dict) -> dict:
    artists = []
    for person in release.get("artists") or []:
        name = _clean_name(person.get("name") or "")
        if name and name not in artists:
            artists.append(name)
    labels = []
    for label in release.get("labels") or []:
        name = label.get("name") or ""
        if name and name not in labels and name.lower() != "not on label":
            labels.append(name)
        elif name.lower() == "not on label" and "自发行" not in labels:
            labels.append("自发行")
    tracks = []
    for track in release.get("tracklist") or []:
        if track.get("type_") == "heading":
            continue
        title = track.get("title") or ""
        if not title:
            continue
        tracks.append(
            {
                "position": track.get("position") or "",
                "title": title,
                "duration": track.get("duration") or "",
            }
        )
    credits = []
    for person in (release.get("extraartists") or [])[:16]:
        role = person.get("role") or ""
        name = _clean_name(person.get("name") or "")
        if name and role:
            credits.append({"name": name, "role": role, "instrument": _is_instrument(role)})
    formats = []
    for fmt in release.get("formats") or []:
        bits = [fmt.get("name") or "", *(fmt.get("descriptions") or [])]
        text = " ".join(bit for bit in bits if bit)
        if text:
            formats.append(text)
    styles = list(release.get("styles") or [])
    genres = [item for item in (release.get("genres") or []) if item not in styles]
    return {
        "id": release.get("id"),
        "title": release.get("title") or "",
        "artists": artists,
        "year": str(release.get("year") or ""),
        "country": release.get("country") or "",
        "labels": labels,
        "genres": genres,
        "styles": styles,
        "formats": formats,
        "tracks": tracks[:24],
        "credits": credits,
        "notes": " ".join((release.get("notes") or "").split())[:500],
        "url": release.get("uri") or "",
    }


def _release_matches(detail: dict, title: str, artist: str) -> bool:
    artist_hit = any(
        artist.casefold() in name.casefold() or name.casefold() in artist.casefold()
        for name in detail.get("artists") or []
    )
    title_hit = title.casefold() in (detail.get("title") or "").casefold()
    track_hit = any(title.casefold() in (track.get("title") or "").casefold() for track in detail.get("tracks") or [])
    if not artist_hit:
        return False
    if generic_title(title):
        return title_hit or track_hit
    return title_hit or track_hit or not detail.get("tracks")


def _search_score(item: dict, title: str, artist: str) -> int:
    blob = (item.get("title") or "").casefold()
    score = 0
    if artist.casefold() in blob:
        score += 5
    if title.casefold() in blob:
        score += 5
    return score


def _clean_name(name: str) -> str:
    text = " ".join(name.split())
    if text.endswith(")") and " (" in text:
        text = text.rsplit(" (", 1)[0]
    return text.strip()


def _is_instrument(role: str) -> bool:
    folded = role.casefold()
    return any(hint.casefold() in folded for hint in INSTRUMENT_HINTS)


def _pace() -> None:
    global _last_call
    wait = 1.1 - (time.monotonic() - _last_call)
    if _last_call and wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
