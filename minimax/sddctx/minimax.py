"""MiniMax 客户端与补丁落地。

MiniMax 走 OpenAI 兼容的 /chat/completions 接口；每次调用都会落盘一条开发记录
（请求消息、响应正文、用量、耗时、模型名），用于评审核对"模型实际参与"。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class MiniMaxError(RuntimeError):
    pass


@dataclass
class MiniMaxReply:
    content: str
    model: str
    usage: dict
    latency_ms: int
    raw: dict


class MiniMaxClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.minimaxi.com/v1",
        model: str = "MiniMax-M2",
        temperature: float = 0.2,
        timeout: int = 240,
    ) -> None:
        if not api_key:
            raise MiniMaxError("缺少 MINIMAX_API_KEY，请先配置 .env")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._timeout = timeout

    def chat(self, messages: list[dict], max_tokens: int = 8192) -> MiniMaxReply:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:  # pragma: no cover - 依赖外部服务
            detail = error.read().decode("utf-8", errors="replace")
            raise MiniMaxError(f"MiniMax HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:  # pragma: no cover
            raise MiniMaxError(f"MiniMax 网络错误: {error}") from error

        latency_ms = int((time.time() - started) * 1000)
        choices = raw.get("choices") or []
        if not choices:
            raise MiniMaxError(f"MiniMax 响应缺少 choices: {json.dumps(raw, ensure_ascii=False)[:500]}")

        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if not content and message.get("reasoning_content"):
            content = message["reasoning_content"]

        return MiniMaxReply(
            content=content,
            model=raw.get("model", self._model),
            usage=raw.get("usage") or {},
            latency_ms=latency_ms,
            raw=raw,
        )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """从模型输出里取出 JSON 对象（容忍 markdown 代码块与前后缀说明）。"""
    candidates: list[str] = []
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)

    for candidate in candidates:
        candidate = candidate.strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            continue
    raise MiniMaxError("无法从模型输出中解析出 JSON，请检查记录文件")


def save_record(
    records_dir: Path,
    name: str,
    *,
    messages: list[dict],
    reply: MiniMaxReply,
    extra: dict | None = None,
) -> Path:
    records_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = records_dir / f"{stamp}-{name}.json"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "minimax",
        "model": reply.model,
        "latency_ms": reply.latency_ms,
        "usage": reply.usage,
        "request": {"messages": messages},
        "response": {"content": reply.content},
    }
    if extra:
        record.update(extra)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def snapshot_workspace(workspace: Path, target: Path, files: list[str] | None = None) -> list[str]:
    """把业务工程里的源码 / 规格 / 测试快照到 target 目录。"""
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    candidates = files or [
        str(path.relative_to(workspace).as_posix())
        for pattern in ("src/**/*.py", "specs/*.md", "tests/*.py")
        for path in sorted(workspace.glob(pattern))
    ]
    for rel in candidates:
        source = workspace / rel
        if not source.is_file():
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        copied.append(rel)
    return copied


def write_diff(before_dir: Path, after_dir: Path, output: Path) -> str:
    chunks: list[str] = []
    for path in sorted(before_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(before_dir).as_posix()
        after_path = after_dir / rel
        before_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        after_lines = (
            after_path.read_text(encoding="utf-8").splitlines(keepends=True)
            if after_path.is_file()
            else []
        )
        if before_lines == after_lines:
            continue
        diff = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=3,
        )
        chunks.append("".join(diff))
    text = "\n".join(chunks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return text


def apply_edits(workspace: Path, edits: list[dict]) -> list[str]:
    """把模型给出的完整文件内容写入业务工程，返回被修改的相对路径。

    拒绝改动 tests/ 与 specs/：测试集必须在修改前后保持一致，规格由人维护。
    """
    changed: list[str] = []
    for edit in edits:
        rel = str(edit.get("path", "")).strip()
        content = edit.get("content")
        if not rel or content is None:
            raise MiniMaxError(f"非法 edit 项: {edit}")
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise MiniMaxError(f"拒绝越界路径: {rel}")
        if rel.startswith("tests/") or rel.startswith("specs/"):
            raise MiniMaxError(f"模型试图修改测试或规格文件，已拒绝: {rel}")

        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        changed.append(rel)
    return changed


def tree_hash(root: Path, pattern: str) -> str:
    """目录内容哈希，用于证明测试集在修改前后没有变化。"""
    digest = hashlib.sha256()
    for path in sorted(root.glob(pattern)):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def code_version(workspace: Path) -> str:
    digest = hashlib.sha256()
    for pattern in ("src/**/*.py", "tests/*.py", "specs/*.md"):
        for path in sorted(workspace.glob(pattern)):
            digest.update(path.relative_to(workspace).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
