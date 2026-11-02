"""seekdb 存储层。

seekdb 在本作品里承担三件事，全部通过 pyseekdb 完成：
1. 结构化存储：当票账本（lies）、玩家档案（players）；
2. 向量检索：新谎言与历史旧账做语义比对，用于「撞当」判定；
3. 混合检索：全文条件 + 向量近邻 + RRF 融合，用于「查账」按关键词翻旧账。

每次操作都会写一条 trace，随接口响应返回，便于评审核对 seekdb 的实际读写。
"""

from __future__ import annotations

import contextvars
import os
import tempfile
import time
import uuid
from typing import Any

import pyseekdb

LIES = "lies"
PLAYERS = "players"

_trace_var: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("trace", default=None)


def resolve_seekdb_path(raw: str, explicit: bool) -> str:
    """解析嵌入式数据目录。

    seekdb 嵌入式内核在 Windows 上无法打开含非 ASCII 字符的路径
    （报 seekdb_open failed: code=-1）。默认路径命中这个坑时自动
    退到系统临时目录；用户显式指定的路径则直接报错，避免数据写到
    他不知道的地方。
    """
    path = os.path.abspath(raw)
    if path.isascii():
        return path
    if explicit:
        raise RuntimeError(
            "seekdb 嵌入式模式无法打开含非 ASCII 字符的路径：\n"
            f"  {path}\n"
            "请把 SEEKDB_PATH 改成纯英文路径，例如 D:\\seekdb-data\\pawnshop"
        )
    fallback = os.path.join(tempfile.gettempdir(), "lie-pawnshop-seekdb")
    if not fallback.isascii():
        raise RuntimeError(f"默认数据目录与临时目录都含非 ASCII 字符，请手动设置 SEEKDB_PATH。当前：{path}")
    print(f"[warn] 数据目录 {path} 含非 ASCII 字符，seekdb 无法打开；已自动改用 {fallback}")
    return fallback


def trace_reset() -> None:
    _trace_var.set([])


def trace_add(op: str, target: str, **kwargs: Any) -> None:
    buf = _trace_var.get()
    if buf is None:
        return
    item: dict[str, Any] = {"op": op, "target": target, "ts": round(time.time(), 3)}
    item.update(kwargs)
    buf.append(item)


def trace_dump() -> list[dict[str, Any]]:
    return list(_trace_var.get() or [])


class SeekdbStore:
    def __init__(self, embedding_function: Any, mode: str = "embedded", **conn: Any) -> None:
        self.ef = embedding_function
        self.mode = mode
        self._conn = conn
        self.path: str | None = None
        if mode == "server":
            self.client = pyseekdb.Client(
                host=conn.get("host", "127.0.0.1"),
                port=int(conn.get("port", 2881)),
                user=conn.get("user", "root"),
                password=conn.get("password", ""),
                database=conn.get("database", "pawnshop"),
                tenant=conn.get("tenant", "sys"),
            )
        else:
            self.path = resolve_seekdb_path(conn.get("path", "./data/seekdb"), bool(conn.get("explicit_path")))
            self.client = pyseekdb.Client(path=self.path)
        self.lies = self._open(LIES)
        self.players = self._open(PLAYERS)

    # ------------------------------------------------------------------ 基础
    def _open(self, name: str) -> Any:
        if self.client.has_collection(name):
            col = self.client.get_collection(name, embedding_function=self.ef)
            trace_add("collection.open", name, dimension=col.dimension)
            return col
        col = self.client.create_collection(name, embedding_function=self.ef)
        trace_add("collection.create", name, dimension=col.dimension)
        return col

    def _refresh(self, col: Any, name: str) -> None:
        col.refresh_index()
        trace_add("index.refresh", name)

    def stats(self) -> dict[str, Any]:
        info = {
            "mode": self.mode,
            "path": self.path,
            "embedding": getattr(self.ef, "name", lambda: "unknown")(),
            "dimension": getattr(self.ef, "dimension", None),
            "lies": self.lies.count(),
            "players": self.players.count(),
        }
        trace_add("stats", "all", **info)
        return info

    # ------------------------------------------------------------------ 玩家
    def get_player(self, player_id: str) -> dict[str, Any] | None:
        res = self.players.get(ids=[player_id], include=["documents", "metadatas"])
        ids = res.get("ids") or []
        trace_add("get", PLAYERS, ids=[player_id], hit=bool(ids))
        if not ids:
            return None
        meta = (res.get("metadatas") or [{}])[0] or {}
        doc = (res.get("documents") or [""])[0]
        return {"player_id": player_id, "profile": doc, **meta}

    def save_player(self, player_id: str, profile: str, meta: dict[str, Any]) -> None:
        payload = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}
        self.players.upsert(ids=[player_id], documents=[profile], metadatas=[payload])
        self._refresh(self.players, PLAYERS)
        trace_add("upsert", PLAYERS, ids=[player_id], **payload)

    # ------------------------------------------------------------------ 账本
    def add_lie(
        self,
        pawn_id: str,
        lie: str,
        player_id: str,
        player_name: str,
        quote: int,
        verdict: str,
        tag: str,
        ts: int,
    ) -> None:
        meta = {
            "player_id": player_id,
            "player_name": player_name,
            "quote": int(quote),
            "verdict": verdict,
            "tag": tag,
            "ts": int(ts),
            "pawn_id": pawn_id,
        }
        self.lies.add(ids=[pawn_id], documents=[lie], metadatas=[meta])
        self._refresh(self.lies, LIES)
        trace_add("add", LIES, ids=[pawn_id], quote=quote, verdict=verdict)

    def get_lie(self, pawn_id: str) -> dict[str, Any] | None:
        res = self.lies.get(ids=[pawn_id], include=["documents", "metadatas"])
        ids = res.get("ids") or []
        trace_add("get", LIES, ids=[pawn_id], hit=bool(ids))
        if not ids:
            return None
        return {
            "pawn_id": pawn_id,
            "lie": (res.get("documents") or [""])[0],
            **((res.get("metadatas") or [{}])[0] or {}),
        }

    def similar_lies(self, text: str, n: int = 5, exclude_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """向量近邻检索，返回带语义相似度的旧账。"""
        exclude = set(exclude_ids or [])
        res = self.lies.query(
            query_texts=[text],
            n_results=n + len(exclude) + 1,
            include=["documents", "metadatas", "distances"],
        )
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        out: list[dict[str, Any]] = []
        for i, pid in enumerate(ids):
            if pid in exclude:
                continue
            dist = float(dists[i]) if i < len(dists) else 1.0
            sim = max(0.0, min(1.0, 1.0 - dist))
            meta = metas[i] or {}
            out.append(
                {
                    "pawn_id": pid,
                    "lie": docs[i],
                    "player_name": meta.get("player_name", "无名氏"),
                    "quote": int(meta.get("quote", 0)),
                    "verdict": meta.get("verdict", ""),
                    "similarity": round(sim, 4),
                }
            )
            if len(out) >= n:
                break
        trace_add("query.vector", LIES, n_results=n, returned=len(out), top=out[0]["lie"] if out else None)
        return out

    def player_lies(self, player_id: str, limit: int = 10) -> list[dict[str, Any]]:
        res = self.lies.get(where={"player_id": player_id}, include=["documents", "metadatas"])
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        rows = []
        for i, pid in enumerate(ids):
            meta = metas[i] or {}
            rows.append({"pawn_id": pid, "lie": docs[i], **meta})
        rows.sort(key=lambda r: int(r.get("ts", 0)), reverse=True)
        trace_add("get.where", LIES, filter={"player_id": player_id}, returned=len(rows))
        return rows[:limit]

    def search_lies(self, keyword: str, n: int = 6) -> list[dict[str, Any]]:
        """全文 + 向量 + RRF 融合的混合检索。"""
        try:
            res = self.lies.hybrid_search(
                query={"where_document": {"$contains": keyword}, "n_results": max(n * 3, 12)},
                knn={"query_texts": [keyword], "n_results": max(n * 3, 12)},
                rank={"rrf": {"rank_window_size": 60, "rank_constant": 60}},
                n_results=n,
                include=["documents", "metadatas"],
            )
        except Exception as exc:  # 某些 provider 的全文索引不可用时退化为向量检索
            trace_add("hybrid_search.fallback", LIES, error=str(exc)[:200])
            return self.similar_lies(keyword, n=n)
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        out = []
        for i, pid in enumerate(ids):
            meta = metas[i] or {}
            out.append({"pawn_id": pid, "lie": docs[i], **meta})
        trace_add("hybrid_search", LIES, keyword=keyword, returned=len(out))
        return out


def new_pawn_id() -> str:
    return f"D-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def store_from_env(embedding_function: Any) -> SeekdbStore:
    mode = (os.getenv("SEEKDB_MODE") or "embedded").strip().lower()
    if mode == "server":
        return SeekdbStore(
            embedding_function,
            mode="server",
            host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
            port=os.getenv("SEEKDB_PORT", "2881"),
            user=os.getenv("SEEKDB_USER", "root"),
            password=os.getenv("SEEKDB_PASSWORD", ""),
            database=os.getenv("SEEKDB_DATABASE", "pawnshop"),
            tenant=os.getenv("SEEKDB_TENANT", "sys"),
        )
    default_path = "./data/seekdb"
    raw = os.getenv("SEEKDB_PATH") or default_path
    # 和默认值相同就当成"没指定"，这样 .env 里原样保留默认值时仍能走自动兜底
    explicit = os.path.abspath(raw) != os.path.abspath(default_path)
    return SeekdbStore(
        embedding_function,
        mode="embedded",
        path=raw,
        explicit_path=explicit,
    )
