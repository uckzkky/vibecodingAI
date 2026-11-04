"""生成 vibe乐评 系统架构图 docs/architecture.png"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "architecture.png"

# 中文字体（Windows 常见）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def box(ax, x, y, w, h, text, fc="#f5f5f5", ec="#333333"):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.2,
        edgecolor=ec,
        facecolor=fc,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=9, wrap=True)


def arrow(ax, x1, y1, x2, y2):
    ax.add_patch(
        FancyArrowPatch(
            (x1, y1),
            (x2, y2),
            arrowstyle="-|>",
            mutation_scale=12,
            linewidth=1.1,
            color="#555555",
        )
    )


def main() -> None:
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=150)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.2)
    ax.axis("off")
    ax.set_title("vibe乐评 — Web 版系统架构", fontsize=13, fontweight="bold", pad=12)

    box(ax, 3.6, 5.35, 2.8, 0.55, "浏览器\n(React 工作台)", fc="#e8f4ff")
    box(ax, 3.2, 4.35, 3.6, 0.65, "web_app.py\nStarlette · SSE /api/ask", fc="#fff4e6")
    box(ax, 3.0, 3.25, 4.0, 0.75, "loop.py\nanswer_query · research · write_ai_review", fc="#ffecec")

    box(ax, 0.35, 1.55, 2.5, 1.35, "Skills（registry.py）\n· social_cloud\n· find_video_reviews\n· search_web / fetch_page\n· MusicBrainz / Discogs", fc="#eef9ee")
    box(ax, 3.75, 1.55, 2.5, 1.35, "外部数据源\n网易云 / 豆瓣 / B站\nMusicBrainz / Discogs\n公开网页", fc="#f0f0f0")
    box(ax, 7.15, 1.55, 2.5, 1.35, "DeepSeek\n+ review_prompt.md\n(JSON 检索材料)", fc="#f3eeff")

    box(ax, 0.35, 0.25, 4.2, 0.85, "MCP music-research\n(mcp_server.py → 同一套 Skills)", fc="#fafafa", ec="#888888")
    box(ax, 5.45, 0.25, 4.3, 0.85, "CLI 可选路径\nsummarize() + Function Calling\n(prompt.md)", fc="#fafafa", ec="#888888")

    arrow(ax, 5.0, 5.35, 5.0, 5.0)
    arrow(ax, 5.0, 4.35, 5.0, 4.0)
    arrow(ax, 4.2, 3.25, 1.6, 2.9)
    arrow(ax, 5.0, 3.25, 5.0, 2.9)
    arrow(ax, 5.8, 3.25, 8.4, 2.9)
    arrow(ax, 2.85, 1.55, 4.0, 2.9)
    arrow(ax, 1.6, 1.55, 1.6, 1.1)
    arrow(ax, 7.5, 1.55, 7.5, 1.1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
