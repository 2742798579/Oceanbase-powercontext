"""存储层：seekdb（pyseekdb）为主，纯内存实现仅用于单元测试。

seekdb 同时承担两件事：
1. 存工程资料（规格 / 代码 / 测试的 chunk 与 metadata）
2. 提供检索：向量相似度（HNSW）+ 全文包含匹配（fulltext index）

注意：pyseekdb 的 embedded 模式目前只支持 Linux / macOS；Windows 请使用 server 模式
或直接在 Linux 环境运行（见 README 的"已知限制"）。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .corpus import Chunk
from .embedding import HashingEmbedding


@dataclass
class Hit:
    chunk_id: str
    score: float
    metadata: dict[str, object]
    document: str


def _flatten(value: object) -> list:
    """pyseekdb 的 query/get 返回可能是 [ [...]] 或 [...]，统一成扁平 list。"""
    if isinstance(value, list):
        if value and isinstance(value[0], list):
            return value[0]
        return value
    return []


class SeekdbStore:
    backend = "seekdb"

    def __init__(
        self,
        path: Path,
        database: str,
        collection_name: str,
        dim: int = 384,
        remote: dict | None = None,
    ) -> None:
        import pyseekdb

        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._dim = dim
        self._name = collection_name
        self._embedding = HashingEmbedding(dim)
        self.query_log: list[str] = []

        if remote:
            # server 模式：连 seekdb server 或 OceanBase（Windows 下唯一可用方式）
            self._client = pyseekdb.Client(
                host=remote.get("host", "127.0.0.1"),
                port=int(remote.get("port", 2881)),
                tenant=remote.get("tenant", "sys"),
                database=database,
                user=remote.get("user", "root"),
                password=remote.get("password", ""),
            )
        else:
            self._client = pyseekdb.Client(path=str(self.path), database=database)
        self._collection = self._open_collection()

    # ------------------------------------------------------------ 建表
    def _open_collection(self):
        from pyseekdb import HNSWConfiguration

        if self._client.has_collection(self._name):
            return self._client.get_collection(self._name, embedding_function=None)

        return self._client.create_collection(
            name=self._name,
            configuration=HNSWConfiguration(dimension=self._dim, distance="cosine"),
            embedding_function=None,
        )

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._name)
        except Exception:  # noqa: BLE001 - 首次运行没有该 collection
            pass
        self._collection = self._open_collection()

    # ------------------------------------------------------------ 写入
    def add(self, chunks: list[Chunk], batch_size: int = 64) -> int:
        if not chunks:
            return 0
        vectors = self._embedding.embed_documents([chunk.text for chunk in chunks])
        for start in range(0, len(chunks), batch_size):
            window = chunks[start : start + batch_size]
            self._collection.add(
                ids=[chunk.chunk_id for chunk in window],
                documents=[chunk.text for chunk in window],
                metadatas=[chunk.metadata() for chunk in window],
                embeddings=vectors[start : start + batch_size],
            )
        # HNSW 索引异步维护，写入后刷新保证立即可检索
        try:
            self._collection.refresh_index()
        except Exception:  # noqa: BLE001 - 旧版本无该方法时忽略
            pass
        return len(chunks)

    def count(self) -> int:
        try:
            return int(self._collection.count())
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------ 检索
    def search_vector(self, query: str, limit: int, doc_type: str | None = None) -> list[Hit]:
        where = {"doc_type": {"$eq": doc_type}} if doc_type else None
        self.query_log.append(f"vector  | doc_type={doc_type} | n_results={limit} | query={query[:60]!r}")
        result = self._collection.query(
            query_embeddings=[self._embedding.embed_query(query)],
            n_results=limit,
            where=where,
            include=["documents", "metadatas"],
        )
        return self._to_hits(result)

    def search_contains(self, needle: str, limit: int, doc_type: str | None = None) -> list[Hit]:
        where = {"doc_type": {"$eq": doc_type}} if doc_type else None
        self.query_log.append(f"fulltext| doc_type={doc_type} | limit={limit} | $contains={needle!r}")
        result = self._collection.get(
            where=where,
            where_document={"$contains": needle},
            limit=limit,
            include=["documents", "metadatas"],
        )
        return self._to_hits(result, default_score=1.0)

    def _to_hits(self, result: dict, default_score: float | None = None) -> list[Hit]:
        ids = _flatten(result.get("ids"))
        documents = _flatten(result.get("documents"))
        metadatas = _flatten(result.get("metadatas"))
        distances = _flatten(result.get("distances"))

        hits: list[Hit] = []
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            document = documents[index] if index < len(documents) else ""
            if default_score is not None:
                score = default_score
            else:
                distance = distances[index] if index < len(distances) else 1.0
                score = 1.0 - float(distance)
            hits.append(
                Hit(
                    chunk_id=str(chunk_id),
                    score=score,
                    metadata=dict(metadata or {}),
                    document=str(document or ""),
                )
            )
        return hits

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


class InMemoryStore:
    """仅供单元测试 / Windows 本地冒烟使用的实现，接口与 SeekdbStore 对齐。

    正式验证流程（sddctx verify、scripts/run_verification.py）只使用 SeekdbStore。
    persist_path 存在时把数据落盘成 JSON，便于跨进程调试（这不是数据库，也不参与验收）。
    """

    backend = "memory"

    def __init__(self, dim: int = 384, persist_path: Path | None = None) -> None:
        self._dim = dim
        self._embedding = HashingEmbedding(dim)
        self._rows: list[tuple[str, str, dict, list[float]]] = []
        self.query_log: list[str] = []
        self._persist_path = Path(persist_path) if persist_path else None
        self._load()

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.is_file():
            return
        payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        self._rows = [
            (row["chunk_id"], row["document"], row["metadata"], self._embedding.embed(row["document"]))
            for row in payload.get("rows", [])
        ]

    def _save(self) -> None:
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rows": [
                {"chunk_id": chunk_id, "document": document, "metadata": metadata}
                for chunk_id, document, metadata, _ in self._rows
            ]
        }
        self._persist_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def reset(self) -> None:
        self._rows.clear()
        self._save()

    def add(self, chunks: list[Chunk], batch_size: int = 64) -> int:
        for chunk in chunks:
            self._rows.append(
                (chunk.chunk_id, chunk.text, chunk.metadata(), self._embedding.embed(chunk.text))
            )
        self._save()
        return len(chunks)

    def count(self) -> int:
        return len(self._rows)

    def search_vector(self, query: str, limit: int, doc_type: str | None = None) -> list[Hit]:
        self.query_log.append(f"vector  | doc_type={doc_type} | n_results={limit} | query={query[:60]!r}")
        query_vector = self._embedding.embed(query)
        scored: list[Hit] = []
        for chunk_id, document, metadata, vector in self._rows:
            if doc_type and metadata.get("doc_type") != doc_type:
                continue
            scored.append(
                Hit(chunk_id, _cosine(query_vector, vector), dict(metadata), document)
            )
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:limit]

    def search_contains(self, needle: str, limit: int, doc_type: str | None = None) -> list[Hit]:
        self.query_log.append(f"fulltext| doc_type={doc_type} | limit={limit} | $contains={needle!r}")
        hits = [
            Hit(chunk_id, 1.0, dict(metadata), document)
            for chunk_id, document, metadata, _ in self._rows
            if (not doc_type or metadata.get("doc_type") == doc_type) and needle in document
        ]
        return hits[:limit]

    def close(self) -> None:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def open_store(settings, backend: str = "seekdb"):
    if backend == "memory":
        return InMemoryStore(
            settings.embed_dim, persist_path=settings.seekdb_path / "memory_store.json"
        )
    remote = None
    if getattr(settings, "seekdb_host", ""):
        remote = {
            "host": settings.seekdb_host,
            "port": settings.seekdb_port,
            "tenant": settings.seekdb_tenant,
            "user": settings.seekdb_user,
            "password": settings.seekdb_password,
        }
    return SeekdbStore(
        path=settings.seekdb_path,
        database=settings.seekdb_database,
        collection_name=settings.collection,
        dim=settings.embed_dim,
        remote=remote,
    )
