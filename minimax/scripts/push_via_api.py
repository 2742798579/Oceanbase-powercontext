#!/usr/bin/env python
"""把本地 minimax/ 目录推送到仓库的指定分支（Git Data API，一次提交）。

用法：
    GITHUB_TOKEN=xxx python scripts/push_via_api.py --dry-run     # 只打印将要提交的文件
    GITHUB_TOKEN=xxx python scripts/push_via_api.py --yes         # 真正创建 commit 并更新分支

要求 token 具备该仓库 **Contents: Read and write** 权限。
上传时会把 CRLF 归一化为 LF（与网页上传行为一致），避免 Linux 判题环境出现行尾差异。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", "data", ".pytest_cache", ".venv"}
SKIP_FILES = {".env", "upload-manifest.md"}
DEFAULT_REPO = "2742798579/Oceanbase-powercontext"
DEFAULT_BRANCH = "2026-competition-ecosystem"
DEFAULT_PATH = "minimax"
COMMIT_MESSAGE = "feat(minimax): SDD context module (seekdb + PowerContext + MiniMax)"


class GitHubError(RuntimeError):
    pass


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", "sddctx-push")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        raise GitHubError(f"{method} {path} → HTTP {error.code}: {detail}") from error


def collect(base: Path, prefix: str) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if any(part in SKIP_DIRS for part in rel.parts) or path.name in SKIP_FILES:
            continue
        if path.suffix == ".pyc":
            continue
        content = path.read_bytes().replace(b"\r\n", b"\n")
        files.append((f"{prefix}/{rel.as_posix()}", content))
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--message", default=COMMIT_MESSAGE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="确认创建提交并更新分支")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    files = collect(ROOT, args.path.strip("/"))
    print(f"待提交 {len(files)} 个文件 → {args.repo}@{args.branch}/{args.path}/")
    if args.dry_run or not args.yes:
        for name, content in files[:10]:
            print(f"  {name}  ({len(content)} bytes)")
        if len(files) > 10:
            print(f"  … 其余 {len(files) - 10} 个")
        print("（预演模式：加 --yes 且提供 GITHUB_TOKEN 才会真正提交）")
        return 0

    if not token:
        print("缺少 GITHUB_TOKEN")
        return 2

    try:
        ref = api("GET", f"/repos/{args.repo}/git/ref/heads/{args.branch}", token)
        base_commit = ref["object"]["sha"]
        base = api("GET", f"/repos/{args.repo}/git/commits/{base_commit}", token)
        print(f"基线提交 {base_commit[:12]}（{base['message'].splitlines()[0][:60]}）")

        tree_items = []
        for name, content in files:
            blob = api("POST", f"/repos/{args.repo}/git/blobs", token, {
                "content": content.decode("utf-8"),
                "encoding": "utf-8",
            })
            tree_items.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"已上传 {len(tree_items)} 个 blob")

        tree = api("POST", f"/repos/{args.repo}/git/trees", token, {
            "base_tree": base["tree"]["sha"],
            "tree": tree_items,
        })
        commit = api("POST", f"/repos/{args.repo}/git/commits", token, {
            "message": args.message,
            "tree": tree["sha"],
            "parents": [base_commit],
        })
        api("PATCH", f"/repos/{args.repo}/git/refs/heads/{args.branch}", token, {
            "sha": commit["sha"],
            "force": False,
        })
        print(f"提交完成：{commit['sha']}")
        print(f"提测填 → Commit Id: {commit['sha']}  Branch: {args.branch}")
        return 0
    except GitHubError as error:
        print(f"失败：{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
