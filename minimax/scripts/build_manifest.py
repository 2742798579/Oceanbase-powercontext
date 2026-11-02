#!/usr/bin/env python
"""生成 docs/upload-manifest.md：网页上传用的文件清单（行数 / 字节 / blob SHA 前缀）。

用法：python scripts/build_manifest.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "upload-manifest.md"
SKIP_DIRS = {".git", "__pycache__", "data", ".pytest_cache", ".venv"}
SKIP_FILES = {".env", "upload-manifest.md"}


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()[:12]


def main() -> None:
    rows: list[tuple[str, int, int, str]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts) or path.name in SKIP_FILES:
            continue
        data = path.read_bytes()
        lines = 0 if not data else data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
        rows.append((rel.as_posix(), lines, len(data), blob_sha(data)))

    total_lines = sum(row[1] for row in rows)
    total_bytes = sum(row[2] for row in rows)
    lines = [
        "# 上传清单（网页上传用）",
        "",
        f"> 由 `scripts/build_manifest.py` 生成。共 **{len(rows)} 个文件**，"
        f"{total_lines} 行，{total_bytes} 字节。",
        "> 上传完成后运行 `python scripts/check_remote.py` 逐文件校验，"
        "它用 git blob SHA 比对，不依赖行数是否看得准。",
        "",
        "| # | 文件（相对 `minimax/`） | 行数 | 字节 | blob SHA |",
        "| ---: | --- | ---: | ---: | --- |",
    ]
    for index, (rel, line_count, size, sha) in enumerate(rows, 1):
        lines.append(f"| {index} | `{rel}` | {line_count} | {size} | `{sha}` |")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {OUT}（{len(rows)} 个文件 / {total_lines} 行）")


if __name__ == "__main__":
    main()
