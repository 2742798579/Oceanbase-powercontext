"""PowerContext 客户端：保存 / 复用任务状态。

使用 PowerContext 的 HTTP API（默认 http://127.0.0.1:8000）：
- POST /v1/scopes            创建 scope（幂等）
- POST /v1/sources/content   保存完整任务状态快照（可审计证据）
- POST /v1/memory/remember   保存"已完成 / 待办 / 验证结果"三条 Memory
- POST /v1/memory/search     按任务检索状态 Memory
- POST /v1/context/prepare   读取有界上下文（重启后复用状态的主入口）
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TAG_RE = re.compile(r"^\[(?P<task>[^\]]+)\]\[(?P<bucket>completed|todo|verification)\]\s*(?P<body>.*)$", re.S)


class PowerContextError(RuntimeError):
    pass


class PowerContextClient:
    def __init__(self, base_url: str, token: str = "", timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # 本机 Server 不走系统代理（否则会被 http_proxy 劫持成 502）
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        handlers = [urllib.request.ProxyHandler({})] if host in {"127.0.0.1", "localhost", "::1"} else []
        self._opener = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------ 基础请求
    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise PowerContextError(f"PowerContext {method} {path} → HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise PowerContextError(
                f"无法连接 PowerContext（{self.base_url}）：{error}。请先执行 `powercontext server run`。"
            ) from error

    def health(self) -> dict:
        return self._request("GET", "/health/ready")

    # ------------------------------------------------------------ scope
    def list_scopes(self, query: str = "", query_field: str = "title") -> list[dict]:
        path = "/v1/scopes"
        if query:
            path = f"{path}?query={urllib.parse.quote(query)}&query_field={query_field}"
        data = self._request("GET", path)
        if isinstance(data, dict):
            return data.get("scopes") or data.get("items") or []
        return data or []

    def ensure_scope(self, title: str, summary: str, idempotency_key: str) -> str:
        payload = {"title": title, "summary": summary, "idempotency_key": idempotency_key}
        try:
            data = self._request("POST", "/v1/scopes", payload)
        except PowerContextError as error:
            # 幂等键已存在但参数不同（409）：按标题找回已有 scope
            if "409" not in str(error):
                raise
            for scope in self.list_scopes():
                if scope.get("title") == title and scope.get("scope_id"):
                    return str(scope["scope_id"])
            raise

        scope_id = data.get("scope_id") or data.get("id")
        if not scope_id:
            raise PowerContextError(f"创建 scope 失败，响应: {data}")
        return str(scope_id)

    # ------------------------------------------------------------ 写入
    def remember(self, scope_id: str, kind: str, text: str, reason: str | None = None) -> dict:
        payload = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        return self._request("POST", "/v1/memory/remember", payload)

    def capture_source(self, scope_id: str, source_id: str, content: str, metadata: dict) -> dict:
        return self._request(
            "POST",
            "/v1/sources/content",
            {"scope_id": scope_id, "source_id": source_id, "content": content, "metadata": metadata},
        )

    def save_task_state(self, scope_id: str, state: dict, run_id: str) -> dict:
        task_id = state["task_id"]
        snapshot = json.dumps(state, ensure_ascii=False, indent=2)

        source = self.capture_source(
            scope_id,
            source_id=f"task-state-{task_id}-{run_id}",
            content=snapshot,
            metadata={"kind": "task-state", "task_id": task_id, "run_id": run_id},
        )

        memories: list[dict] = []
        for bucket, label in (
            ("completed", "已完成"),
            ("todo", "待办"),
            ("verification", "验证结果"),
        ):
            values = state.get(bucket) or []
            if not values:
                continue
            text = f"[{task_id}][{bucket}] " + "；".join(str(item) for item in values)
            memories.append(
                self.remember(
                    scope_id,
                    kind=f"sdd-task-{bucket}",
                    text=text,
                    reason=f"{label}：{task_id}",
                )
            )

        return {"source": source, "memories": memories}

    # ------------------------------------------------------------ 读取
    def search(self, scope_id: str, query: str, limit: int = 10) -> list[dict]:
        data = self._request(
            "POST", "/v1/memory/search", {"scope_id": scope_id, "query": query, "limit": limit}
        )
        hits = data.get("hits") or []
        normalized: list[dict] = []
        for hit in hits:
            entry = hit.get("entry") or hit
            normalized.append(
                {
                    "text": entry.get("text", ""),
                    "kind": entry.get("kind", ""),
                    "score": hit.get("score"),
                }
            )
        return normalized

    def prepare(self, scope_id: str, query: str, max_bytes: int = 4000) -> dict:
        return self._request(
            "POST",
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
        )

    def list_entries(self, scope_id: str) -> list[dict]:
        data = self._request("POST", "/v1/memory/entries/list", {"scope_id": scope_id})
        return data.get("entries") or []

    def load_task_state(self, scope_id: str, task_id: str, query: str) -> dict:
        entries = self.list_entries(scope_id)
        prepared = self.prepare(scope_id, query, max_bytes=4000)
        hits = self.search(scope_id, f"{task_id} 任务状态 已完成 待办 验证结果", limit=10)

        # 同一 bucket 可能因多次运行存在多条，取最后写入的一条
        buckets: dict[str, list[str]] = {"completed": [], "todo": [], "verification": []}
        for entry in entries:
            match = TAG_RE.match(str(entry.get("text", "")).strip())
            if not match or match.group("task") != task_id:
                continue
            body = match.group("body").strip()
            buckets[match.group("bucket")] = [item.strip() for item in body.split("；") if item.strip()]

        state = {
            "source": "PowerContext（/v1/memory/entries/list + /v1/memory/search + /v1/context/prepare）",
            "completed": buckets["completed"],
            "todo": buckets["todo"],
            "verification": buckets["verification"],
            "prepared_status": prepared.get("status"),
            "prepared_content": prepared.get("content"),
            "entries_total": len(entries),
            "search_hits": hits,
        }
        return state
