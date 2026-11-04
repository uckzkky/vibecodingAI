"""工具参数。MCP 和 function calling 都用这一套模型。"""

from pydantic import BaseModel, Field


class SearchWebArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=400, description="检索语句")
    max_results: int = Field(5, ge=1, le=8, description="最多返回几条")


class FetchPageArgs(BaseModel):
    url: str = Field(..., description="要读取正文的 http(s) 链接")
    max_chars: int = Field(6000, ge=500, le=12000, description="正文截断长度")


class MusicBrainzArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="歌名")
    artist: str = Field(..., min_length=1, max_length=200, description="歌手")


class DiscogsArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="歌名")
    artist: str = Field(..., min_length=1, max_length=200, description="歌手")


class CriticismArgs(BaseModel):
    title: str = Field("", max_length=200, description="歌名。只查歌手时留空")
    artist: str = Field("", max_length=200, description="歌手。只查歌曲时留空")


class SocialCloudArgs(BaseModel):
    title: str = Field("", max_length=200, description="歌名。只查歌手时留空")
    artist: str = Field("", max_length=200, description="歌手。只查歌曲时留空")
    label: str = Field("", max_length=200, description="厂牌。用来限定歌手，可留空")


class VideoReviewsArgs(BaseModel):
    title: str = Field("", max_length=200, description="歌名。只查歌手时留空")
    artist: str = Field("", max_length=200, description="歌手。只查歌曲时留空")
    label: str = Field("", max_length=200, description="厂牌。用来限定歌手，可留空")


class ListenArgs(BaseModel):
    audio_path: str = Field("", max_length=500, description="本地音频路径。空字符串表示这次没有录音")
