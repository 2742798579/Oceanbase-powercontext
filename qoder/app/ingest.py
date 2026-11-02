"""资料导入：Markdown / 纯文本 -> 带主题标签的 chunk -> seekdb。

切块策略刻意保持简单可解释：
  - 以二级标题作为主题边界；
  - 段内按空行切段，过短的段落与下一段合并；
  - 每块控制在 60~400 字，保证既能被向量表达，又能作为一张复习卡片的正面。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from .store import SeekDB

log = logging.getLogger("recall.ingest")

MAX_CHUNK = 400
MIN_CHUNK = 60


def doc_id_for(title: str, source_path: str) -> str:
    return hashlib.sha1(f"{title}|{source_path}".encode("utf-8")).hexdigest()[:16]


def _split_sections(text: str, fallback_title: str) -> list[tuple[str, str]]:
    """返回 [(topic, body)]，topic 取最近的 ## 标题。"""
    sections: list[tuple[str, str]] = []
    current_topic = fallback_title
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append((current_topic, body))
        buffer.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            flush()
            current_topic = line[3:].strip() or fallback_title
        elif line.startswith("# "):
            flush()
        else:
            buffer.append(line)
    flush()
    return sections


def _chunk_body(body: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        p = " ".join(p.split())
        if not p:
            continue
        if len(p) > MAX_CHUNK:
            # 超长段落按句号再切
            sentences = re.split(r"(?<=[。！？；])", p)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if len(buf) + len(s) > MAX_CHUNK and buf:
                    chunks.append(buf)
                    buf = s
                else:
                    buf = (buf + s) if buf else s
            continue
        if len(buf) + len(p) > MAX_CHUNK and buf:
            chunks.append(buf)
            buf = p
        else:
            buf = (buf + " " + p).strip() if buf else p
    if buf:
        chunks.append(buf)
    # 合并过短的尾块
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < MIN_CHUNK and len(merged[-1]) + len(c) <= MAX_CHUNK:
            merged[-1] = merged[-1] + " " + c
        else:
            merged.append(c)
    return merged


def build_chunks(title: str, text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seq = 0
    for topic, body in _split_sections(text, title):
        for piece in _chunk_body(body):
            out.append({"topic": topic[:128], "seq": seq, "content": piece})
            seq += 1
    return out


def ingest_text(
    title: str,
    course: str,
    text: str,
    tags: str = "",
    source_path: str = "",
    replace: bool = True,
) -> dict[str, Any]:
    store = SeekDB.instance()
    doc_id = doc_id_for(title, source_path or title)
    if replace:
        store.delete_doc(doc_id)
    chunks = build_chunks(title, text)
    if not chunks:
        return {"doc_id": doc_id, "inserted": 0, "title": title}
    doc = {
        "doc_id": doc_id,
        "doc_title": title,
        "source_path": source_path or f"inline/{title}",
        "course": course or "未分类",
        "tags": tags,
    }
    inserted = store.insert_chunks(doc, chunks)
    log.info("导入 %s -> %d 个 chunk", title, inserted)
    return {
        "doc_id": doc_id,
        "title": title,
        "course": doc["course"],
        "inserted": inserted,
        "topics": sorted({c["topic"] for c in chunks}),
    }


def ingest_file(path: Path, course: str | None = None, tags: str = "") -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    stem = path.stem
    # 约定文件名格式：课程-标题.md
    if course is None:
        course = stem.split("-")[0] if "-" in stem else "未分类"
    title = stem.split("-", 1)[1] if "-" in stem else stem
    return ingest_text(title, course, text, tags=tags, source_path=str(path))


def ingest_dir(directory: Path, tags: str = "") -> list[dict[str, Any]]:
    results = []
    for path in sorted(directory.glob("*.md")):
        results.append(ingest_file(path, tags=tags))
    return results
