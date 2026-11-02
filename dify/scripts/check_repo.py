#!/usr/bin/env python3
"""提测前自查：确认仓库、分支、dify 目录、报名 URL 四项都合格。

用法：
    set GITHUB_TOKEN=<你的 token>          # Windows
    export GITHUB_TOKEN=<你的 token>       # Linux/macOS
    python scripts/check_repo.py --repo 2742798579/Oceanbase-powercontext \
        --url https://github.com/2742798579/Oceanbase-powercontext

只读，不会改动仓库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

API = "https://api.github.com"
DEFAULT_BRANCH = "2026-competition-ecosystem"
DEFAULT_DIR = "dify"


def _curl_json(url: str, token: str) -> tuple[int, dict | list | None]:
    """优先用 curl（能走 Windows 证书库，绕开本地 TLS 代理的证书问题）。"""
    cmd = ["curl", "-s", "--ssl-no-revoke", "--max-time", "30",
           "-H", f"Authorization: Bearer {token}",
           "-H", "Accept: application/vnd.github+json",
           "-w", "\n%{http_code}", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=45).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0, None
    body, _, code = out.rpartition("\n")
    try:
        return int(code or 0), json.loads(body) if body.strip() else None
    except ValueError:
        return int(code or 0), None


def _urllib_json(url: str, token: str, insecure: bool) -> tuple[int, dict | list | None]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "lie-pawnshop-check",
    })
    ctx = None
    if insecure:
        import ssl
        ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:  # noqa: BLE001 - 网络/证书类失败统一当作未知
        return 0, None


class Checker:
    def __init__(self, token: str, insecure: bool) -> None:
        self.token = token
        self.insecure = insecure
        self.results: list[tuple[str, str, str]] = []

    def get(self, path: str) -> tuple[int, dict | list | None]:
        url = path if path.startswith("http") else API + path
        code, data = _curl_json(url, self.token)
        if code == 0:
            code, data = _urllib_json(url, self.token, self.insecure)
        return code, data

    def record(self, name: str, ok: bool | None, detail: str = "") -> None:
        status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        self.results.append((status, name, detail))
        print(f"[{status}] {name}" + (f"    {detail}" if detail else ""))

    def warn(self, name: str, detail: str = "") -> None:
        self.results.append(("WARN", name, detail))
        print(f"[WARN] {name}" + (f"    {detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.results)


def check_url(raw: str) -> tuple[bool, str]:
    """赛题要求：提交 URL 不含认证信息、query、端口、分支路径。"""
    problems = []
    if "@" in raw.split("//", 1)[-1]:
        problems.append("含认证信息（user:token@）")
    if "?" in raw:
        problems.append("含 query")
    if re.search(r"//[^/]+:\d+", raw):
        problems.append("含端口")
    if re.search(r"/(tree|blob|commits?)/", raw):
        problems.append("含分支或文件路径")
    if raw.rstrip("/").endswith(".git"):
        problems.append("以 .git 结尾（允许单个，但建议去掉）")
    return (not problems), "；".join(problems) if problems else "格式正确"


def main() -> int:
    ap = argparse.ArgumentParser(description="提测前自查")
    ap.add_argument("--repo", required=True, help="owner/name，例如 2742798579/Oceanbase-powercontext")
    ap.add_argument("--branch", default=DEFAULT_BRANCH, help=f"目标分支，默认 {DEFAULT_BRANCH}")
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"作品目录，默认 {DEFAULT_DIR}")
    ap.add_argument("--url", default="", help="报名登记里的仓库地址，用来校验格式")
    ap.add_argument("--insecure", action="store_true", help="跳过 TLS 校验（本机有代理时用）")
    args = ap.parse_args()

    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        print("缺少环境变量 GITHUB_TOKEN", file=sys.stderr)
        return 2

    c = Checker(token, args.insecure)
    owner_repo = args.repo.strip().strip("/")

    # 1. 仓库可读
    code, repo = c.get(f"/repos/{owner_repo}")
    if code != 200 or not isinstance(repo, dict):
        c.record("① 仓库存在且可读", False, f"HTTP {code}")
        print("\n仓库读不到，后面的检查没法做。先确认 token 有没有这个仓库的权限。")
        return 1
    c.record("① 仓库存在且可读", True, f"HTTP 200  {owner_repo}")

    # 2. 可见性
    private = repo.get("private")
    c.record(
        "② 可见性",
        True,
        ("private（组织方需要 token 才能读，务必确认参赛信息里已填带凭据地址）"
         if private else "public（组织方直接可读，报名地址务必用不带 token 的干净 URL）"),
    )

    # 3. 默认分支（不是提交条件，但极易导致推错分支）
    default = repo.get("default_branch")
    if default == args.branch:
        c.record("③ 默认分支", True, f"{default}，与目标分支一致")
    else:
        c.warn("③ 默认分支", f"当前 {default}，目标分支是 {args.branch}——clone 下来记得切分支，提测时 Branch 字段也必须手填")

    # 4. 目标分支存在
    code, branch = c.get(f"/repos/{owner_repo}/branches/{args.branch}")
    if code == 200 and isinstance(branch, dict):
        sha = branch.get("commit", {}).get("sha", "")
        c.record("④ 目标分支存在", True, f"{args.branch} @ {sha[:10]}")
    else:
        c.record("④ 目标分支存在", False, f"HTTP {code}，分支 {args.branch} 不存在")
        sha = ""

    # 5. 作品目录内容
    if sha:
        code, items = c.get(f"/repos/{owner_repo}/contents/{args.dir}?ref={args.branch}")
        if code == 200 and isinstance(items, list):
            names = [i["name"] for i in items]
            real = [n for n in names if n != ".gitkeep"]
            c.record(
                "⑤ 作品目录有内容",
                bool(real),
                f"{args.dir}/ 下 {len(real)} 项：" + ", ".join(sorted(real)[:8]) + ("…" if len(real) > 8 else "")
                if real else f"{args.dir}/ 只有 .gitkeep，代码还没推上去",
            )
        else:
            c.record("⑤ 作品目录有内容", False, f"{args.dir}/ 不存在（HTTP {code}）")

    # 6. 报名地址格式
    if args.url:
        ok, detail = check_url(args.url)
        c.record("⑥ 报名地址格式", ok, detail)
    else:
        c.record("⑥ 报名地址格式", None, "未传 --url，跳过")

    print("\n" + "=" * 60)
    if c.failed:
        print("有检查项未通过，先修完再提测。")
        return 1
    print("全部通过 ✅ 可以提测")
    if sha:
        print(f"提测填写：Branch = {args.branch}    Commit Id = {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
