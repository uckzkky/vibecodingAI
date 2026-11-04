"""技能登记。编排循环和 MCP 都从这里调用。"""

from __future__ import annotations

from pydantic import ValidationError

from skills.common import tool_error
from skills.criticism import analyze_criticism
from skills.discogs import lookup_discogs
from skills.fetch_page import fetch_page
from skills.listening import listen_recording
from skills.musicbrainz import lookup_musicbrainz
from skills.schemas import (
    CriticismArgs,
    DiscogsArgs,
    FetchPageArgs,
    ListenArgs,
    MusicBrainzArgs,
    SearchWebArgs,
    SocialCloudArgs,
    VideoReviewsArgs,
)
from skills.social_cloud import social_cloud
from skills.video_reviews import find_video_reviews
from skills.web_search import search_web

TOOLS = {
    "search_web": {
        "fn": search_web,
        "schema": SearchWebArgs,
        "description": "检索公开网页，返回标题、链接和摘要，不打开正文。百科和豆瓣链接会被丢掉。",
    },
    "fetch_page": {
        "fn": fetch_page,
        "schema": FetchPageArgs,
        "description": "打开一个公开网页并抽出正文。视频链接只原样返回。百科和豆瓣页面会拒绝。",
    },
    "lookup_musicbrainz": {
        "fn": lookup_musicbrainz,
        "schema": MusicBrainzArgs,
        "description": "在 MusicBrainz 查询录音、艺人、专辑、时长、标签和外部链接。",
    },
    "lookup_discogs": {
        "fn": lookup_discogs,
        "schema": DiscogsArgs,
        "description": "在 Discogs 查询厂牌、风格、曲目和乐手署名。这是发行目录，不是乐评。",
    },
    "social_cloud": {
        "fn": social_cloud,
        "schema": SocialCloudArgs,
        "description": "读网易云评论、豆瓣短评长评和评分、哔哩哔哩视频评论，再加小红书的检索摘要，做各平台词云、平台 × 话题热点图和代表评论。不采集微博和知乎。这是听众意见，不是专业乐评。",
    },
    "find_video_reviews": {
        "fn": find_video_reviews,
        "schema": VideoReviewsArgs,
        "description": "挖哔哩哔哩评歌视频、豆瓣和抖音上的测评线索。用标题、简介或检索摘要，不把口播当成专业乐评，也不和听众评论混在一起。",
    },
    "analyze_criticism": {
        "fn": analyze_criticism,
        "schema": CriticismArgs,
        "description": "先根据艺人检索到的地区、厂牌和圈子查找乐评机构，再按声音、结构、场景和评价落点分析正文。没有正文就说明没找到，不用艺人接触不到的乐评站填空。",
    },
    "listen_recording": {
        "fn": listen_recording,
        "schema": ListenArgs,
        "description": "用音频分类模型听一段本地录音，判断乐器和采样手法。不检索网页，也不猜测被采样的原曲。",
    },
}


def call_tool(name: str, arguments: dict | None = None) -> dict:
    meta = TOOLS.get(name)
    if meta is None:
        return tool_error(f"未知工具：{name}")
    try:
        args = meta["schema"].model_validate(arguments or {})
        return meta["fn"](**args.model_dump())
    except ValidationError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"{type(exc).__name__}: {exc}")


def openai_tools() -> list[dict]:
    listed = []
    for name, meta in TOOLS.items():
        schema = meta["schema"].model_json_schema()
        schema.pop("title", None)
        listed.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta["description"],
                    "parameters": schema,
                },
            }
        )
    return listed
