# vibe乐评

**vibe乐评** 是一个面向「小众华语音乐」的课程作业型智能体：你输入**歌手**或**歌曲**，系统会从网易云、豆瓣、哔哩哔哩等公开渠道**自动抓取听众评论与影音讨论**，在网页上汇总成**词云、情感对比**和一篇**可核对来源的 AI 乐评**。  
所有结论都尽量绑定检索到的材料，**不让大模型凭记忆编造**乐评或百科式背景。

若你是**批改老师**或**第一次接触编程的同学**，建议按顺序阅读：[项目是做什么](#项目是做什么) → [5 分钟跑起来](#5-分钟跑起来) → [页面上每一块是什么意思](#页面上每一块是什么意思) → [和课程要求如何对应](#和课程要求如何对应)。

---

## 项目是做什么

| 问题 | 本项目的做法 |
|------|----------------|
| 想快速了解「大家怎么评价这首歌」 | 抓取多平台评论，做词云与情感对比 |
| 想要一篇可读的乐评，而不是链接堆砌 | 用 DeepSeek 等模型，在**检索材料范围内**写中文乐评 |
| 担心 AI 胡说 | 报告与乐评 prompt 都要求：材料里没有的，写「未找到」 |
| 小众歌手资料少 | 支持厂牌 + 歌手等写法，缩小检索范围；不依赖百度百科式科普 |

**不做什么：** 不查微博/知乎听众流；不假装有「外国专业机构乐评」；不把豆瓣短评直接当权威定论；不上传或分析你的私人音频（除非另行配置本地听音模块）。

---

## 5 分钟跑起来

### 第一步：下载并安装依赖

```bash
git clone https://github.com/uckzkky/vibecodingAI.git
cd vibecodingAI
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

（Mac/Linux 把 `.venv\Scripts\` 换成 `.venv/bin/`。）

### 第二步：配置 AI 写乐评用的密钥

1. 复制 `.env.example` 为 `.env`（**不要**把 `.env` 提交到 GitHub）。  
2. 在 [DeepSeek 开放平台](https://platform.deepseek.com/) 申请 API Key，填入：

```bash
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_MODEL=deepseek-chat
```

未配置密钥时，网页仍可**检索并展示词云与情感**；**「乐评」一栏**会提示无法生成正文。

### 第三步：启动网页服务

```bash
.venv\Scripts\python src\web_app.py
```

浏览器打开：**http://127.0.0.1:8765**

### 第四步：输入查询并点击「开始检索」

| 你可以这样写 | 系统会理解成 |
|--------------|--------------|
| `lufive的《2008》` | 歌手 lufive，歌名 2008 |
| `某厂牌旗下的某歌手` | 在该厂牌语境下检索该歌手 |
| 只写歌手名 | 围绕这位歌手检索（不查全球发行目录） |

检索大约需要 **1～3 分钟**（取决于网络与各平台响应）。右侧会逐步显示「检索轨迹」。

### 可选：在 Cursor 里用 MCP 调试技能

```bash
copy .cursor\mcp.json.example .cursor\mcp.json
```

把文件里的 Python 路径改成你电脑上的 `.venv` 与 `src/mcp_server.py` 的实际位置。详见 [MCP 是什么、怎么用](#mcp-是什么怎么用)。

### 可选：命令行生成 Markdown 报告

```bash
.venv\Scripts\python src\loop.py --song 2008 --artist lufive
```

报告保存在 `src/output/`（该目录已在 `.gitignore` 中忽略，不会推送到 GitHub）。

---

## 页面上每一块是什么意思

示例截图在 [`docs/readme-screenshots/`](docs/readme-screenshots/)（查询示例：`lufive的《2008》`）。

### 整体布局

- **左侧（主内容）**：查询框 → 三个数字概览 → 词云 → 情感对比 → AI 乐评  
- **右侧（检索轨迹）**：像「工作日志」，记录系统何时去抓数据、何时写乐评  
- **顶栏**：产品名与当前状态（就绪 / 检索中 / 实时更新）

![未开始检索时的界面](docs/readme-screenshots/01-idle.png)

### ① 查询区

在文本框用**一句话**说明要查谁/哪首歌，点击 **开始检索**。系统会自动解析歌手、歌名、厂牌（若你写了「厂牌旗下的歌手」这类句式）。

![检索完成后的标题、概览与词云](docs/readme-screenshots/03-results-top.png)

| 区域 | 含义（给非技术读者） |
|------|----------------------|
| 大标题 | 当前查到的歌名与歌手 |
| 标题下方一句摘要 | 哪个平台的听众**整体更偏正面**（基于评论用词统计） |
| 三个数字 | **平台数**：有几个平台够做情感对比；**词数**：词云里有多少个不同的高频词；**最高表态**：目前最「偏褒义」的平台及分数 |

### ② 各平台词云

把听众评论里**反复出现的词**画出来：**字越大、颜色越亮（黄绿色）**，出现越多。每个词只显示一次，避免刷屏。  
数据来源主要是网易云、豆瓣、哔哩哔哩等（**不含**微博、知乎）。

![词云与情感对比](docs/readme-screenshots/05-sentiment-review.png)

### ③ 情感对比

每个平台一条横条，分数 **0～100**，表示「带明确褒贬态度的评论里，褒义大致占多少」。  
若某平台评论太少或几乎没有情感词，可能**不显示**该平台——这是正常现象，不是故障。

### ④ AI 乐评

检索结束后，大模型会阅读系统整理好的评论摘要、争议点与平台差异，写一篇**中文乐评**（流式打字效果）。  
乐评强调**观点与归纳**；若材料不足，文中会说明依据有限。  
**请知悉：** 乐评是辅助阅读工具，不能替代你本人对音乐的判断。

![乐评正文与检索轨迹](docs/readme-screenshots/04-trace-and-review.png)

### 右侧「检索轨迹」怎么读

| 标记 | 含义 |
|------|------|
| **CALL** | 系统正在调用某个「技能」（例如去网易云读评论） |
| **OBS** | 技能返回了结果（例如「共 331 条评论」） |
| **LLM** | 大模型在规划步骤或撰写乐评 |

这让批改者能清楚看到：**结论来自工具与材料，而不是模型空口编造**。

---

## 系统内部如何协作（给懂一点技术的读者）

一次网页查询的大致顺序：

1. **听众评论与词云**（技能 `social_cloud`）  
2. **影音测评线索**（技能 `find_video_reviews`：B 站、豆瓣、抖音等）  
3. **发行信息**（有歌名+歌手时：`lookup_musicbrainz`、`lookup_discogs`）  
4. **采访 / 创作理念 / MV**（网页搜索 + 打开正文页）  
5. **AI 乐评**（把整理好的 JSON 交给 DeepSeek，见 `src/review_prompt.md`）  
6. **本地存档**（可选，写入 `src/output/`）

网页主路径由 **`src/loop.py` 里的编排循环**按固定顺序调用技能；命令行模式还可让模型通过 **Function Calling** 补调工具（见 `src/prompt.md`）。

---

## 代码与文档在哪里

| 路径 | 说明 |
|------|------|
| `src/web/index.html` | 网页界面与交互 |
| `src/web_app.py` | 本地 Web 服务、解析用户输入 |
| `src/loop.py` | 编排：先抓什么、再写什么乐评 |
| `src/review_prompt.md` | 约束 AI 乐评写法（给模型看的规则） |
| `src/skills/` | 各项「技能」：搜网页、读评论、查目录等 |
| `src/mcp_server.py` | 把同一套技能暴露给 Cursor（MCP 协议） |
| `docs/ai乐评-有效prompt.md` | 开发过程中有效的产品指令整理（供复盘作业过程） |

---

## MCP 是什么、怎么用

**MCP（Model Context Protocol）** 可以理解为：让 Cursor 里的 AI **像本项目的网页一样**，调用你写好的 Python 技能（搜网页、读评论等），而不是只聊天。

1. 配置 `.cursor/mcp.json`（从 `mcp.json.example` 复制并改路径）。  
2. 在 Cursor 设置中启用 MCP 服务器 **music-research**。  
3. 在对话中可要求 AI 调用 `social_cloud` 等工具做实验。

---

## 和课程要求如何对应

| 课程要求 | 本项目中的体现 |
|----------|----------------|
| **至少 2 个不同的 Tool / Skill** | 例如：网页搜索、读评论、查 MusicBrainz/Discogs、影音测评等，共多项独立技能 |
| **MCP 或 Function Calling** | MCP：`mcp_server.py`；Function Calling：命令行 `summarize()` + `openai_tools()` |
| **Vibe Coding：样板交给 AI，人写 prompt 与编排** | HTTP/参数/MCP 样板在 `src/skills/`；产品与边界在 `prompt.md`、`loop.py`、网页 |

---

## 更多参考

- 协议与 API 链接：[`docs/literature/bibliography.md`](docs/literature/bibliography.md)  
- 开发指令复盘：[`docs/ai乐评-有效prompt.md`](docs/ai乐评-有效prompt.md)  
- 答辩用宽屏截图（可选）：[`docs/readme-screenshots/补拍说明.md`](docs/readme-screenshots/补拍说明.md)

---

## 截图文件一览

| 文件 | 内容 |
|------|------|
| `01-idle.png` | 检索前的空界面 |
| `03-results-top.png` | 概览数字与词云（上） |
| `05-sentiment-review.png` | 词云（下）、情感条、乐评开头 |
| `04-trace-and-review.png` | 乐评与轨迹日志 |
| `02-results-full.png` | 整页长图（备用） |
