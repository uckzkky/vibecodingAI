"""MusicBrainz 录音检索。不含采样和乐器的主观判断。"""

from __future__ import annotations

import time

import httpx

from skills.common import USER_AGENT, generic_title, get_client, tool_error
from skills.schemas import MusicBrainzArgs

API = "https://musicbrainz.org/ws/2/recording"
_last_call = 0.0


def lookup_musicbrainz(title: str, artist: str) -> dict:
    args = MusicBrainzArgs(title=title, artist=artist)
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    query = f'recording:"{_escape(args.title)}" AND artist:"{_escape(args.artist)}"'
    try:
        found = _matching(_search(query, headers), args.title, args.artist)
        if not found:
            found = _matching(_search(f"{args.title} {args.artist}", headers), args.title, args.artist)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {429, 503}:
            return tool_error(f"MusicBrainz HTTP {exc.response.status_code}", service="musicbrainz")
        time.sleep(2)
        try:
            found = _matching(_search(query, headers), args.title, args.artist)
        except httpx.HTTPStatusError as retry_exc:
            return tool_error(
                f"MusicBrainz 暂时不可用（{retry_exc.response.status_code}）",
                service="musicbrainz",
            )
    except Exception as exc:
        return tool_error(f"{type(exc).__name__}: {exc}", service="musicbrainz")

    if not found:
        return {
            "ok": True,
            "service": "musicbrainz",
            "query": query,
            "recordings": [],
            "note": "MusicBrainz 没有匹配录音",
        }

    recordings = []
    found.sort(key=_hit_penalty)
    for hit in found[:4]:
        summary = _summary(hit)
        score = summary.get("score")
        try:
            detail = _lookup(hit["id"], headers)
        except Exception as exc:
            summary["detail_error"] = f"{type(exc).__name__}: {exc}"
            recordings.append(summary)
            continue
        summary.update(_summary(detail))
        summary["score"] = summary.get("score") or score
        recordings.append(summary)

    recordings.sort(key=_rank_recording)
    return {
        "ok": True,
        "service": "musicbrainz",
        "query": query,
        "recordings": recordings,
    }


def _matching(hits: list[dict], title: str, artist: str) -> list[dict]:
    wanted = artist.casefold().strip()
    kept = []
    for hit in hits:
        credit = _credit(hit).casefold()
        if not wanted or wanted not in credit:
            continue
        name = hit.get("title") or ""
        if generic_title(title) and title.casefold() not in name.casefold():
            continue
        kept.append(hit)
    return kept


def _rank_recording(recording: dict) -> tuple:
    return _release_rank(recording.get("disambiguation") or "", recording.get("first_release_date") or "")


def _hit_penalty(hit: dict) -> tuple:
    return _release_rank(hit.get("disambiguation") or "", hit.get("first-release-date") or "")


def _release_rank(note: str, date: str) -> tuple:
    folded = note.lower()
    penalty = 5 if any(word in folded for word in ("video", "live", "karaoke")) else 0
    return (penalty, date or "9999")


def _search(query: str, headers: dict[str, str]) -> list[dict]:
    _pace()
    response = get_client().get(
        API,
        params={"query": query, "fmt": "json", "limit": 10},
        headers=headers,
    )
    response.raise_for_status()
    return response.json().get("recordings") or []


def _lookup(mbid: str, headers: dict[str, str]) -> dict:
    _pace()
    response = get_client().get(
        f"{API}/{mbid}",
        params={
            "fmt": "json",
            "inc": "artist-credits+releases+tags+genres+url-rels+work-rels+artist-rels",
        },
        headers=headers,
    )
    response.raise_for_status()
    return response.json()


def _pace() -> None:
    global _last_call
    wait = 1.1 - (time.monotonic() - _last_call)
    if _last_call and wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _summary(recording: dict) -> dict:
    releases = []
    for release in (recording.get("releases") or [])[:6]:
        releases.append(
            {
                "title": release.get("title") or "",
                "date": release.get("date") or "",
                "country": release.get("country") or "",
            }
        )
    urls = []
    relations = []
    for rel in (recording.get("relations") or [])[:20]:
        resource = (rel.get("url") or {}).get("resource")
        if resource:
            urls.append({"type": rel.get("type") or "", "url": resource})
            continue
        if rel.get("work"):
            relations.append(
                {"type": rel.get("type") or "", "work": rel["work"].get("title") or ""}
            )
        elif rel.get("artist"):
            relations.append(
                {"type": rel.get("type") or "", "artist": rel["artist"].get("name") or ""}
            )
    mbid = recording.get("id") or ""
    return {
        "id": mbid,
        "url": f"https://musicbrainz.org/recording/{mbid}" if mbid else "",
        "title": recording.get("title") or "",
        "artist": _credit(recording),
        "disambiguation": recording.get("disambiguation") or "",
        "length": _length(recording.get("length")),
        "first_release_date": recording.get("first-release-date") or "",
        "score": recording.get("score"),
        "tags": _names(recording.get("tags")),
        "genres": _names(recording.get("genres")),
        "releases": releases,
        "urls": urls[:10],
        "relations": relations[:10],
    }


def _credit(recording: dict) -> str:
    parts = []
    for item in recording.get("artist-credit") or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("name"):
            parts.append(item["name"])
    return "".join(parts)


def _names(values: list | None) -> list[str]:
    return [item.get("name") for item in values or [] if item.get("name")][:12]


def _length(milliseconds: int | None) -> str:
    if not milliseconds:
        return ""
    seconds = int(milliseconds) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"
