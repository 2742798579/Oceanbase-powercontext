#!/usr/bin/env python
"""上传后自检：把本地 minimax/ 与远端分支逐文件比对（git blob SHA）。

用法：
    python scripts/check_remote.py                       # 默认比对 minimax/ 目录
    python scripts/check_remote.py --path minimax/sddctx # 只比对某个子目录
    GITHUB_TOKEN=xxx python scripts/check_remote.py      # 私有仓库才需要 token

原理：GitHub 的 tree 接口会返回每个文件的 git blob SHA（`sha1("blob <len>\\0" + content)`），
本地算同一个值即可逐文件比对，**不需要下载文件**，一次 API 调用就能查完整棵树。
网页上传会把 CRLF 转成 LF，所以本地同时用「原始内容」和「LF 归一化内容」两种算法比对。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", "data", ".pytest_cache", ".venv", "node_modules"}
SKIP_FILES = {".env", "upload-manifest.md"}


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def local_files(base: Path, sub: str) -> dict[str, set[str]]:
    """base 就是 minimax/ 目录本身，比对时补上 sub 前缀以对齐远端路径。"""
    prefix = sub.strip("/") + "/"
    result: dict[str, set[str]] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES or path.suffix == ".pyc":
            continue
        data = path.read_bytes()
        result[prefix + rel.as_posix()] = {
            blob_sha(data),
            blob_sha(data.replace(b"\r\n", b"\n")),
        }
    return result


def remote_tree(repo: str, branch: str, sub: str, token: str | None) -> dict[str, str]:
    url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "sddctx-check"}
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    prefix = sub.strip("/") + "/"
    return {
        item["path"]: item["sha"]
        for item in payload.get("tree", [])
        if item["type"] == "blob" and item["path"].startswith(prefix)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="2742798579/Oceanbase-powercontext")
    parser.add_argument("--branch", default="2026-competition-ecosystem")
    parser.add_argument("--path", default="minimax")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    local = local_files(ROOT, args.path)
    try:
        remote = remote_tree(args.repo, args.branch, args.path, token)
    except urllib.error.HTTPError as error:
        print(f"读取远端失败：HTTP {error.code}（私有仓库需要 GITHUB_TOKEN）")
        return 2

    missing = [path for path in local if path not in remote]
    extra = [path for path in remote if path not in local]
    mismatch = [
        path for path in local if path in remote and remote[path] not in local[path]
    ]
    same = len(local) - len(missing) - len(mismatch)

    print(f"仓库 {args.repo} @ {args.branch} / {args.path}/")
    print(f"本地 {len(local)} 个文件，远端 {len(remote)} 个文件")
    print(f"一致 {same}，缺失 {len(missing)}，内容不一致 {len(mismatch)}，远端多余 {len(extra)}")
    for label, items in (("缺失", missing), ("不一致", mismatch), ("多余", extra)):
        for item in items[:20]:
            print(f"  [{label}] {item}")
        if len(items) > 20:
            print(f"  [{label}] … 其余 {len(items) - 20} 个")
    if not missing and not mismatch:
        print("结果：本地与远端一致 ✅ 可以提测")
        return 0
    print("结果：尚未一致 ❌ 先补齐再提测")
    return 1


if __name__ == "__main__":
    sys.exit(main())
