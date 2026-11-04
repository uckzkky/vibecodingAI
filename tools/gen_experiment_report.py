"""生成《现代软件开发方法实践》实验一 Word 报告。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from docx.oxml.ns import qn

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "现代软件开发方法实践-实验一-实验报告.docx"
IMG_ARCH = REPO / "docs" / "architecture.png"
IMG_UI = REPO / "docs" / "readme-screenshots" / "03-results-top.png"

REPO_URL = "https://github.com/uckzkky/vibecodingAI"

SECTION4 = (
    "Agent 名称「vibe乐评」：输入歌手或歌曲，从网易云、豆瓣、哔哩哔哩等抓取听众评论与影音讨论，"
    "网页展示词云、情感对比与检索轨迹，并由大模型在材料范围内写中文乐评，结论绑定工具结果、不凭记忆编造。"
    "功能含听众聚合可视化、影音测评检索、发行目录查询（MusicBrainz/Discogs）、采访与 MV 检索、AI 乐评。"
    "Skills：search_web、fetch_page、social_cloud、find_video_reviews、lookup_musicbrainz、lookup_discogs 等，"
    "经 Pydantic 注册于 registry.py。"
    "上下文集成：MCP 服务 music-research（mcp_server.py）；命令行 Function Calling（summarize + openai_tools）；"
    "Web 路径 loop.research 编排技能链，write_ai_review 将 JSON 与 review_prompt.md 提交 DeepSeek。"
    "架构：浏览器 → web_app → loop → Skills → 看板与乐评。"
)


def set_run_font(run, name: str = "宋体", size_pt: float = 12):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size_pt)


def add_para(doc: Document, text: str, bold: bool = False, size: float = 12):
    p = doc.add_paragraph()
    run = p.add_run(text)
    set_run_font(run, size_pt=size)
    run.bold = bold
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.space_after = Pt(6)
    return p


def main() -> None:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = title.add_run("现代软件开发方法实践-（实验一）—实验报告")
    set_run_font(tr, "黑体", 16)
    tr.bold = True
    doc.add_paragraph()

    add_para(doc, "1. 姓名：________________________")
    add_para(doc, "2. 学号：________________________")
    doc.add_paragraph()

    add_para(doc, "3. 请提供你的 Agent 代码存放的 repo（请包含核心的 prompt）：", bold=True)
    add_para(doc, REPO_URL)
    add_para(doc, "核心 prompt 文件（仓库内路径）：")
    add_para(doc, "• src/review_prompt.md — 网页「AI 乐评」的 system prompt（约束材料来源、篇幅与写法）")
    add_para(doc, "• src/prompt.md — 命令行综述报告的 system prompt（四层外界声音、工具补洞规则）")
    add_para(
        doc,
        "review_prompt 首句示例：「你是一位中文乐评人。根据附带的检索材料，写一段真正的乐评……」"
        "prompt 首句示例：「你是做乐评的评论员。材料只来自这次对话里的工具结果……」"
        "完整正文见上述文件。"
    )
    doc.add_paragraph()

    add_para(doc, "4. 请简要介绍你设计的 Agent（不超过 500 字，可附图）：", bold=True)
    add_para(doc, "注意：需说明 agent 的主要功能，使用了哪些工具或 Skills，使用了哪些上下文 (context) 集成技术。")
    add_para(doc, "附图 1：系统架构", bold=True)
    if IMG_ARCH.exists():
        doc.add_picture(str(IMG_ARCH), width=Cm(15.5))
    doc.add_paragraph()
    for block in SECTION4.split("\n\n"):
        add_para(doc, block.strip())
    char_count = len(SECTION4.replace("\n", ""))
    add_para(doc, f"（以上说明约 {char_count} 字。）", size=10.5)

    add_para(doc, "附图 2：Web 工作台界面", bold=True)
    if IMG_UI.exists():
        doc.add_picture(str(IMG_UI), width=Cm(14))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(f"Saved: {OUT}")


if __name__ == "__main__":
    main()
