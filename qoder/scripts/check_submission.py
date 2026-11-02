"""提测前自检：逐条打印 PASS / FAIL，退出码 0 才建议提交。

只依赖标准库。私有仓库需要 GITHUB_TOKEN（fine-grained，勾选目标仓库的
Contents + Metadata 只读即可）。

    # 最常用
    python scripts/check_submission.py ^
        --repo 2742798579/Oceanbase-powercontext ^
        --branch 2026-competition-ecosystem ^
        --commit <40 位 SHA> ^
        --site https://<你的 Qoder Sites 地址>

    # 私有仓库
    set GITHUB_TOKEN=github_pat_xxx
    python scripts/check_submission.py --repo ... --branch ...

    # 只想校验 URL 格式 / 本地文件，不联网
    python scripts/check_submission.py --repo-url "https://github.com/a/b" --offline

检查项：
  1  提交 URL 格式（不能带 token / 端口 / query / 分支路径）
  2  仓库存在且可读
  3  不是官方上游本身
  4  目标分支存在
  5  commit 存在，且确实是该分支上的提交
  6  qoder/ 目录下关键文件齐全
  7  项目网站未登录可访问
  8  本地待提交文件齐全、且没有把凭据/大文件带进去
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"

REQUIRED_REPO_FILES = [
    "qoder/README.md",
    "qoder/VERIFY.md",
    "qoder/QODER_LOG.md",
    "qoder/SUBMIT.md",
    "qoder/requirements.txt",
    "qoder/run.py",
    "qoder/app/main.py",
    "qoder/app/store.py",
    "qoder/app/memory.py",
    "qoder/web/index.html",
    "qoder/site/index.html",
    "qoder/scripts/verify.py",
]

# 这些不应该出现在仓库里
FORBIDDEN_PATTERNS = [
    (r"\.venv/", "虚拟环境"),
    (r"data/seekdb/", "本地数据库"),
    (r"data/models/", "向量模型文件"),
    (r"\.env$", "环境变量文件（可能含凭据）"),
    (r"powercontext_scope\.txt$", "本地 scope id"),
]

results: list[tuple[str, str, str]] = []  # (状态, 检查项, 详情)


def add(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "INFO": "[----]"}[status]
    print(f"{icon} {name}" + (f"    {detail}" if detail else ""))


def http_json(url: str, token: str | None, timeout: int = 15):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "knowreturn-check")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_head(url: str, timeout: int = 15) -> int:
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; knowreturn-check)")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


# ---------- 1. URL 格式 ----------

def check_url_format(raw: str) -> str | None:
    """返回清洗后的 owner/repo；格式非法返回 None。"""
    name = "1. 提交 URL 格式"
    if not raw:
        add("SKIP", name, "未提供 --repo-url")
        return None

    problems = []
    if "@" in raw:
        problems.append("包含 @（疑似把 token 拼进了 URL）")
    if re.search(r"github_pat_|ghp_|gho_|ghs_", raw):
        problems.append("包含 token 字样")
    if "?" in raw or "#" in raw:
        problems.append("包含 query / fragment")
    if "/tree/" in raw or "/blob/" in raw or "/commit/" in raw:
        problems.append("包含分支或文件路径")

    cleaned = raw.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        problems.append("协议必须是 https")
    if parsed.hostname != "github.com":
        problems.append(f"host 应为 github.com，实际 {parsed.hostname}")
    if parsed.port:
        problems.append("包含端口号")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2:
        problems.append(f"路径应为 /owner/repo，实际 /{parsed.path.strip('/')}")

    if problems:
        add("FAIL", name, "；".join(problems))
        return None
    add("PASS", name, f"{parts[0]}/{parts[1]}")
    return f"{parts[0]}/{parts[1]}"


# ---------- 2~6. 仓库侧 ----------

def check_remote(repo: str, branch: str, commit: str, token: str | None) -> None:
    try:
        info = http_json(f"{API}/repos/{repo}", token)
    except urllib.error.HTTPError as exc:
        add("FAIL", "2. 仓库存在且可读", f"HTTP {exc.code}（私有仓库请设 GITHUB_TOKEN）")
        return
    except Exception as exc:  # noqa: BLE001
        add("SKIP", "2. 仓库存在且可读", f"网络不可达：{exc}")
        return

    add(
        "PASS",
        "2. 仓库存在且可读",
        f"private={info.get('private')} default_branch={info.get('default_branch')}",
    )

    full = (info.get("full_name") or "").lower()
    if full in ("oceanbase/powercontext", "oceanbase/seekdb"):
        add("FAIL", "3. 不是官方上游本身", full)
    else:
        add("PASS", "3. 不是官方上游本身", full)

    # 分支
    if not branch:
        add("SKIP", "4. 目标分支存在", "未提供 --branch")
    else:
        try:
            b = http_json(f"{API}/repos/{repo}/branches/{branch}", token)
            tip = b["commit"]["sha"]
            add("PASS", "4. 目标分支存在", f"{branch} → {tip[:12]}")
            if not commit:
                add("SKIP", "5. commit 在分支上", f"未提供 --commit，分支 tip 是 {tip}")
            elif commit == tip:
                add("PASS", "5. commit 在分支上", "与分支 tip 一致")
            else:
                try:
                    c = http_json(f"{API}/repos/{repo}/commits/{commit}", token)
                    add(
                        "PASS",
                        "5. commit 在分支上",
                        f"{commit[:12]} 存在（不是 tip，tip 是 {tip[:12]}；确认平台按 commit 取代码）",
                    )
                    _ = c
                except urllib.error.HTTPError:
                    add("FAIL", "5. commit 在分支上", f"{commit[:12]} 在该仓库不存在")
        except urllib.error.HTTPError as exc:
            add("FAIL", "4. 目标分支存在", f"{branch} HTTP {exc.code}")
            try:
                branches = http_json(f"{API}/repos/{repo}/branches?per_page=100", token)
                names = [x["name"] for x in branches]
                add("INFO", "   现有分支", ", ".join(names[:12]) + (" …" if len(names) > 12 else ""))
            except Exception:  # noqa: BLE001
                pass

    # qoder/ 目录
    ref = branch or info.get("default_branch")
    missing = []
    try:
        for path in REQUIRED_REPO_FILES:
            try:
                http_json(f"{API}/repos/{repo}/contents/{path}?ref={ref}", token)
            except urllib.error.HTTPError:
                missing.append(path)
        if missing:
            add("FAIL", "6. qoder/ 关键文件齐全", f"缺 {len(missing)} 个：" + ", ".join(missing[:4]))
        else:
            add("PASS", "6. qoder/ 关键文件齐全", f"{len(REQUIRED_REPO_FILES)} 个文件都在 {ref}")
    except Exception as exc:  # noqa: BLE001
        add("SKIP", "6. qoder/ 关键文件齐全", str(exc))


# ---------- 7. 站点 ----------

def check_site(url: str) -> None:
    if not url:
        add("SKIP", "7. 项目网站可访问", "未提供 --site")
        return
    try:
        code = http_head(url)
    except Exception as exc:  # noqa: BLE001
        add("SKIP", "7. 项目网站可访问", f"网络不可达：{exc}")
        return
    if 200 <= code < 400:
        add("PASS", "7. 项目网站可访问", f"HTTP {code}")
        add("INFO", "   记得用无痕窗口再开一次", "确认未登录状态下也能打开")
    else:
        add("FAIL", "7. 项目网站可访问", f"HTTP {code}")


# ---------- 8. 本地文件 ----------

def check_local() -> None:
    if not ROOT.exists():
        add("SKIP", "8. 本地文件齐全", "找不到 qoder/ 目录")
        return
    missing = [p for p in REQUIRED_REPO_FILES if not (ROOT.parent / p).exists()]
    if missing:
        add("FAIL", "8. 本地文件齐全", f"缺 {len(missing)} 个：" + ", ".join(missing[:4]))
    else:
        add("PASS", "8. 本地文件齐全", f"{len(REQUIRED_REPO_FILES)} 个都在")

    gitignore = ROOT / ".gitignore"
    ignored_text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if gitignore.exists():
        add("PASS", "   .gitignore", "存在")
    else:
        add("FAIL", "   .gitignore", "缺失，容易把 .venv 推上去")

    leaked, ignored = [], []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(ROOT.parent)).replace("\\", "/")
        for pattern, label in FORBIDDEN_PATTERNS:
            if not re.search(pattern, rel):
                continue
            key = rel.replace("qoder/", "", 1)
            if key.split("/")[0] + "/" in ignored_text or rel in ignored_text:
                ignored.append(label)
            else:
                leaked.append(f"{rel}（{label}）")
    if leaked:
        add("FAIL", "   不该提交的东西", "；".join(sorted(set(leaked))[:4]))
    elif ignored:
        add("PASS", "   不该提交的东西", "已由 .gitignore 排除：" + "、".join(sorted(set(ignored))))
    else:
        add("PASS", "   不该提交的东西", "没有 .venv / 数据库 / 模型 / 凭据")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="", help="owner/repo")
    parser.add_argument("--repo-url", default="", help="平台上要填的仓库 URL，用于格式校验")
    parser.add_argument("--branch", default="2026-competition-ecosystem")
    parser.add_argument("--commit", default="")
    parser.add_argument("--site", default="", help="Qoder Sites 公网地址")
    parser.add_argument("--offline", action="store_true", help="只跑本地与 URL 格式检查")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip() or None
    print("=" * 68)
    print("知返 KnowReturn · 提测前自检")
    print("=" * 68)

    repo = args.repo.strip()
    if args.repo_url:
        parsed_repo = check_url_format(args.repo_url)
        repo = repo or (parsed_repo or "")
    elif repo:
        add("SKIP", "1. 提交 URL 格式", "未提供 --repo-url")

    if not args.offline:
        if repo:
            check_remote(repo, args.branch, args.commit, token)
        else:
            add("FAIL", "2. 仓库存在且可读", "既没给 --repo 也没给 --repo-url")
        check_site(args.site)
    else:
        add("SKIP", "2~7. 仓库与站点", "--offline")

    check_local()

    print("-" * 68)
    fails = [r for r in results if r[0] == "FAIL"]
    skips = [r for r in results if r[0] == "SKIP"]
    if fails:
        print(f"结果：{len(fails)} 项未通过，{len(skips)} 项跳过 —— 先修 FAIL 再提测")
        for _, name, detail in fails:
            print(f"  · {name} {detail}")
        return 1
    print(f"结果：全部通过（{len(skips)} 项跳过）—— 可以提测")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
