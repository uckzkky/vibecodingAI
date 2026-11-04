"""打包可部署源码（不含 .venv、.env、.git、运行产物）。输出到 dist/vibecodingAI-deploy.zip"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "dist"
ARCHIVE = OUT_DIR / "vibecodingAI-deploy.zip"
ROOT_NAME = "vibecodingAI"

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".taste-skill",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
}
SKIP_FILE_NAMES = {".env", ".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}


def should_skip(path: Path, rel: Path) -> bool:
    if rel.name in SKIP_FILE_NAMES:
        return True
    if rel.suffix.lower() in SKIP_SUFFIXES:
        return True
    if rel.name == "mcp.json" and rel.parent.name == ".cursor":
        return True
    parts = rel.parts
    if "src" in parts and "output" in parts and rel.name != ".gitkeep":
        return True
    if ".cursor" in parts and "skills" in parts:
        return True
    for part in parts:
        if part in SKIP_DIR_NAMES:
            return True
    return False


def collect_files() -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO)
        if should_skip(path, rel):
            continue
        arc = f"{ROOT_NAME}/{rel.as_posix()}"
        rows.append((path, arc))
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = collect_files()
    if ARCHIVE.exists():
        ARCHIVE.unlink()
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        deploy_txt = (
            f"vibe乐评 — 部署包\n"
            f"打包时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            "1. 解压本文件夹\n"
            "2. python -m venv .venv\n"
            "3. .venv\\Scripts\\pip install -r requirements.txt\n"
            "4. 复制 .env.example 为 .env，填入 DEEPSEEK_API_KEY\n"
            "5. .venv\\Scripts\\python src\\web_app.py\n"
            "6. 浏览器打开 http://127.0.0.1:8765\n\n"
            "完整说明见 README.md\n"
        )
        zf.writestr(f"{ROOT_NAME}/DEPLOY.txt", deploy_txt.encode("utf-8"))
        for path, arc in files:
            zf.write(path, arc)
    size_mb = ARCHIVE.stat().st_size / (1024 * 1024)
    print(f"Wrote {ARCHIVE} ({len(files) + 1} entries, {size_mb:.2f} MiB)")


if __name__ == "__main__":
    main()
