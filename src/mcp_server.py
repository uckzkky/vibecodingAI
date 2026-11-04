"""MCP 服务。把单曲资料技能暴露给 Cursor。"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mcp.server.mcpserver import MCPServer

from skills.registry import call_tool

mcp = MCPServer(
    "music-research",
    instructions="为小众华语音乐检索公开网页、听众评论与发行目录。结论须来自工具返回的链接与正文，不查百科，不把豆瓣短评当作权威乐评。",
)


@mcp.tool()
def search_web(query: str, max_results: int = 5) -> dict:
    """检索公开网页，返回标题、链接和摘要，不打开正文。"""
    return call_tool("search_web", {"query": query, "max_results": max_results})


@mcp.tool()
def fetch_page(url: str, max_chars: int = 6000) -> dict:
    """打开一个公开网页并抽出正文。视频链接只原样返回。"""
    return call_tool("fetch_page", {"url": url, "max_chars": max_chars})


@mcp.tool()
def lookup_musicbrainz(title: str, artist: str) -> dict:
    """在 MusicBrainz 查询录音、艺人、专辑、时长、标签和外部链接。"""
    return call_tool("lookup_musicbrainz", {"title": title, "artist": artist})


@mcp.tool()
def lookup_discogs(title: str, artist: str) -> dict:
    """在 Discogs 查询厂牌、风格、曲目和乐手署名。"""
    return call_tool("lookup_discogs", {"title": title, "artist": artist})


@mcp.tool()
def social_cloud(title: str = "", artist: str = "") -> dict:
    """读网易云、豆瓣、B站的听众评论，做词云和平台 × 话题热点图。"""
    return call_tool("social_cloud", {"title": title, "artist": artist})


@mcp.tool()
def find_video_reviews(title: str = "", artist: str = "") -> dict:
    """挖 B 站、豆瓣、抖音上的影音测评线索，和听众评论、专业乐评分开。"""
    return call_tool("find_video_reviews", {"title": title, "artist": artist})


@mcp.tool()
def analyze_criticism(title: str, artist: str) -> dict:
    """从垂直乐评站点整理听感判断。没有专业页面时不要用百科或豆瓣填上。"""
    return call_tool("analyze_criticism", {"title": title, "artist": artist})


@mcp.tool()
def listen_recording(audio_path: str = "") -> dict:
    """听本地音频，判断乐器和采样手法。没有音频就说明听不了，不要改去检索。"""
    return call_tool("listen_recording", {"audio_path": audio_path})


if __name__ == "__main__":
    mcp.run()
