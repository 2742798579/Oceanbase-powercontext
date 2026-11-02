"""PowerContext 记忆层。

只用标准库 urllib 调 HTTP API，评审不需要额外装 Python SDK：

  POST /v1/scopes           创建 Scope（数据分区）
  POST /v1/memory/remember  显式写入一条长期记忆
  POST /v1/context/prepare  按查询召回有字节预算的上下文
  POST /v1/sources/content  把一次交互作为证据留痕

记忆里存的是「学习者的长期约束、偏好和薄弱主题」。它会在下一次检索和下一次
复习排队时被读出来，直接改变排序和卡片数量 —— 这就是本题要求的「复用」。
为了让记忆既能被人读懂、又能被程序用上，写入时用一句自然语言带上键值：

  学习偏好：优先复习科目=数据库；每日卡片上限=12
  薄弱主题：页分裂、回表
  复习进度：完成 5 张卡片，掌握 2 个主题
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import (
    DATA_DIR,
    POWERCONTEXT_SCOPE,
    POWERCONTEXT_TIMEOUT,
    POWERCONTEXT_TOKEN,
    POWERCONTEXT_URL,
)

log = logging.getLogger("recall.memory")

SCOPE_CACHE = Path(DATA_DIR) / "powercontext_scope.txt"

_PREF_COURSE_RE = re.compile(r"优先复习科目=([^\s；;，,。\"\\]+)")
_PREF_LIMIT_RE = re.compile(r"每日卡片上限=(\d+)")
_WEAK_RE = re.compile(r"薄弱主题[：:]\s*([^\n\"\\]+)")

# 用固定的「档案查询」召回长期偏好，避免记忆效果随用户查询词漂移
PROFILE_QUERY = "我的学习偏好、薄弱主题和复习进度"
_BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"


def _memory_items(content: str) -> list[str]:
    """PreparedContext 的正文是一个带标记的 JSON，先把每条记忆的 content 抽出来。"""
    if not content:
        return []
    if _BEGIN in content and _END in content:
        blob = content.split(_BEGIN, 1)[1].split(_END, 1)[0].strip()
        try:
            data = json.loads(blob)
            return [
                str(item.get("content", ""))
                for item in data.get("items", [])
                if item.get("content")
            ]
        except (json.JSONDecodeError, AttributeError):
            pass
    return [content]


class MemoryUnavailable(RuntimeError):
    """PowerContext 不可用时抛出，调用方据此降级并明确告知用户。"""


class PowerContextMemory:
    def __init__(
        self,
        base_url: str = POWERCONTEXT_URL,
        scope_id: str = POWERCONTEXT_SCOPE,
        token: str = POWERCONTEXT_TOKEN,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.scope_id = scope_id
        self.token = token
        self._lock = threading.Lock()
        self.last_error: str | None = None

    # ---------- HTTP ----------

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=POWERCONTEXT_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise MemoryUnavailable(f"PowerContext {path} HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise MemoryUnavailable(
                f"连不上 PowerContext（{self.base_url}）：{exc.reason}。"
                "请先启动 powercontext server run"
            ) from exc
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def health(self) -> dict[str, Any]:
        try:
            self._request("GET", "/health/live")
            ready = self._request("GET", "/health/ready")
            return {"ok": True, "ready": ready}
        except MemoryUnavailable as exc:
            return {"ok": False, "error": str(exc)}

    # ---------- Scope ----------

    def ensure_scope(self, title: str = "知返 · 学习资料记忆") -> str:
        if self.scope_id:
            return self.scope_id
        with self._lock:
            if self.scope_id:
                return self.scope_id
            if SCOPE_CACHE.exists():
                cached = SCOPE_CACHE.read_text(encoding="utf-8").strip()
                if cached:
                    self.scope_id = cached
                    return cached
            scope = self._request(
                "POST",
                "/v1/scopes",
                {
                    "title": title,
                    "summary": "知返：学习资料的混合检索与复习教练",
                    "idempotency_key": "recall-knowreturn-v1",
                },
            )
            scope_id = scope.get("scope_id")
            if not scope_id:
                raise MemoryUnavailable(f"创建 Scope 失败: {scope}")
            SCOPE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SCOPE_CACHE.write_text(scope_id, encoding="utf-8")
            self.scope_id = scope_id
            log.info("PowerContext scope: %s", scope_id)
            return scope_id

    # ---------- 写入 ----------

    def remember(self, text: str, kind: str = "fact", reason: str = "") -> dict[str, Any]:
        scope_id = self.ensure_scope()
        payload = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        try:
            return self._request("POST", "/v1/memory/remember", payload)
        except MemoryUnavailable as exc:
            # kind 的取值集合随版本变化，退回到最通用的 fact 再试一次
            if kind != "fact":
                payload["kind"] = "fact"
                try:
                    return self._request("POST", "/v1/memory/remember", payload)
                except MemoryUnavailable:
                    pass
            raise exc

    def remember_many(self, items: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
        out = []
        for text, kind, reason in items:
            try:
                out.append(self.remember(text, kind, reason))
            except MemoryUnavailable as exc:
                log.warning("记忆写入失败：%s", exc)
                self.last_error = str(exc)
        return out

    def write_source(self, source_id: str, content: str, metadata: dict | None = None) -> None:
        scope_id = self.ensure_scope()
        self._request(
            "POST",
            "/v1/sources/content",
            {
                "scope_id": scope_id,
                "source_id": source_id,
                "content": content,
                "metadata": metadata or {},
            },
        )

    # ---------- 条目管理 ----------

    def list_entries(self) -> list[dict[str, Any]]:
        """列出当前 Scope 下所有 active 的记忆条目（含 citation，可用于 retire）。"""
        scope_id = self.ensure_scope()
        resp = self._request("POST", "/v1/memory/entries/list", {"scope_id": scope_id})
        return resp.get("entries", []) or []

    def retire(self, citation: dict[str, Any], reason: str = "被新记忆取代") -> Any:
        """把一条记忆置为 retired：移出主动召回，内容与历史仍可查。"""
        scope_id = self.ensure_scope()
        return self._request(
            "POST",
            "/v1/memory/entries/retire",
            {"scope_id": scope_id, "citation": citation, "reason": reason},
        )

    def profile_directives(self) -> dict[str, Any]:
        """从全部 active 记忆里解析出行为参数。

        比按 query 召回更确定：偏好和薄弱点不依赖用户这次问了什么词。
        """
        try:
            entries = self.list_entries()
        except MemoryUnavailable as exc:
            self.last_error = str(exc)
            return {"status": "unavailable", "error": str(exc), "directives": PowerContextMemory.parse_directives(""), "entries": []}
        text = "\n".join(str(e.get("text", "")) for e in entries)
        return {
            "status": "ready" if entries else "empty",
            "directives": PowerContextMemory.parse_directives(text),
            "entries": entries,
            "text": text,
        }

    # ---------- 召回 ----------

    def prepare(self, query: str, max_bytes: int = 4000) -> dict[str, Any]:
        scope_id = self.ensure_scope()
        try:
            resp = self._request(
                "POST",
                "/v1/context/prepare",
                {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
            )
            return {
                "status": resp.get("status", "unknown"),
                "content": resp.get("content") or "",
                "scope_id": scope_id,
            }
        except MemoryUnavailable as exc:
            self.last_error = str(exc)
            return {"status": "unavailable", "content": "", "error": str(exc), "scope_id": scope_id}

    # ---------- 解析 ----------

    @staticmethod
    def parse_directives(content: str) -> dict[str, Any]:
        """从召回内容里抽出可以直接影响行为的指令。

        记忆文本长这样（人读得懂，程序也解析得了）：
            学习偏好：优先复习科目=数据库系统；每日卡片上限=6
            薄弱主题：页分裂
        """
        out: dict[str, Any] = {"preferred_course": None, "card_limit": None, "weak_topics": []}
        items = _memory_items(content)
        if not items:
            return out
        text = "\n".join(items)

        # 同一条偏好可能被写过多次，取最后一次（最新）的
        hits = _PREF_COURSE_RE.findall(text)
        if hits:
            out["preferred_course"] = hits[-1].strip()
        hits = _PREF_LIMIT_RE.findall(text)
        if hits:
            out["card_limit"] = int(hits[-1])

        seen: list[str] = []
        for m in _WEAK_RE.finditer(text):
            # 主题词里可能带空格（如「B+ 树索引的结构」），只按列举符号切分
            for part in re.split(r"[、，,；;]+", m.group(1)):
                part = part.strip(" ：:。\"'\\")
                if part and part not in seen:
                    seen.append(part)
        out["weak_topics"] = seen
        return out
