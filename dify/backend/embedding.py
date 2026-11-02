"""嵌入函数实现。

两种实现：
1. OpenAICompatibleEmbedding —— 走任意 OpenAI 兼容的 /embeddings 接口（推荐）。
   例如阿里云百炼 compatible-mode、SiliconFlow、Ollama(/v1)、vLLM 等。
2. HashEmbedding —— 纯本地确定性向量，不需要任何外部服务与密钥。
   它只保证"链路能跑通"，不具备真实语义能力，用于离线自测与评审快速体验。

注意：pyseekdb 会把嵌入函数的 name() 持久化到 collection 元数据里，
重新打开集合时会按 name 从 registry 反查类，因此自定义实现必须注册。
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from typing import Any

import httpx
from pyseekdb.client.embedding_function import EmbeddingFunctionRegistry


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0:
        return vec
    return [x / norm for x in vec]


class HashEmbedding:
    """离线降级嵌入：字符 n-gram 哈希投影 + L2 归一。

    确定性、无依赖、维度可配；对中文按字切分，能捕捉一部分字面重合，
    但没有真正的语义理解。适合在没有模型服务时跑通全链路。
    """

    def __init__(self, dimension: int = 512) -> None:
        self._dimension = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    @staticmethod
    def name() -> str:
        return "lie_pawnshop_hash"

    def get_config(self) -> dict[str, Any]:
        return {"dimension": self._dimension}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "HashEmbedding":
        return HashEmbedding(dimension=int(config.get("dimension", 512)))

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self._dimension
        text = (text or "").strip()
        if not text:
            return vec
        grams: list[str] = []
        grams.extend(text)  # unigram
        grams.extend(text[i : i + 2] for i in range(len(text) - 1))  # bigram
        for gram in grams:
            digest = hashlib.md5(gram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec)

    def __call__(self, documents: Any) -> list[list[float]]:
        docs = [documents] if isinstance(documents, str) else list(documents)
        return [self._one(d) for d in docs]


class OpenAICompatibleEmbedding:
    """OpenAI 兼容 /embeddings 客户端（带本地缓存与分批）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "text-embedding-v3",
        dimension: int = 1024,
        timeout: float = 30.0,
        batch_size: int = 8,
        send_dimensions: bool = False,
        cache_size: int = 4096,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._dimension = int(dimension)
        self.timeout = timeout
        self.batch_size = max(1, int(batch_size))
        self.send_dimensions = send_dimensions
        self._cache: dict[str, list[float]] = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    @staticmethod
    def name() -> str:
        return "lie_pawnshop_openai_compatible"

    def get_config(self) -> dict[str, Any]:
        # 只持久化非敏感字段；api_key 从环境变量读取，不落库
        return {
            "base_url": self.base_url,
            "model": self.model,
            "dimension": self._dimension,
            "batch_size": self.batch_size,
            "send_dimensions": self.send_dimensions,
        }

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "OpenAICompatibleEmbedding":
        import os

        return OpenAICompatibleEmbedding(
            base_url=config.get("base_url", ""),
            api_key=os.getenv("EMBEDDING_API_KEY", ""),
            model=config.get("model", "text-embedding-v3"),
            dimension=int(config.get("dimension", 1024)),
            batch_size=int(config.get("batch_size", 8)),
            send_dimensions=bool(config.get("send_dimensions", False)),
        )

    def _cache_get(self, text: str) -> list[float] | None:
        with self._lock:
            return self._cache.get(text)

    def _cache_put(self, text: str, vec: list[float]) -> None:
        with self._lock:
            if len(self._cache) >= self._cache_size:
                self._cache.clear()
            self._cache[text] = vec

    def _request(self, batch: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.model, "input": batch}
        if self.send_dimensions:
            payload["dimensions"] = self._dimension
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{self.base_url}/embeddings", json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                items = sorted(data["data"], key=lambda x: x.get("index", 0))
                vecs = [_l2_normalize([float(v) for v in it["embedding"]]) for it in items]
                if len(vecs) != len(batch):
                    raise ValueError(f"embedding count mismatch: {len(vecs)} != {len(batch)}")
                got = len(vecs[0])
                if got != self._dimension:
                    raise ValueError(
                        f"embedding dimension mismatch: model returned {got}, expected {self._dimension}. "
                        f"请把 EMBEDDING_DIMENSION 改成 {got}，并重建数据库。"
                    )
                return vecs
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"embedding request failed: {last_err}") from last_err

    def __call__(self, documents: Any) -> list[list[float]]:
        docs = [documents] if isinstance(documents, str) else list(documents)
        docs = [d or "" for d in docs]
        out: list[list[float] | None] = [self._cache_get(d) for d in docs]
        missing = [d for d, v in zip(docs, out) if v is None]
        # 去重后再请求
        uniq = list(dict.fromkeys(missing))
        for i in range(0, len(uniq), self.batch_size):
            batch = uniq[i : i + self.batch_size]
            for text, vec in zip(batch, self._request(batch)):
                self._cache_put(text, vec)
        return [self._cache_get(d) or [0.0] * self._dimension for d in docs]


EmbeddingFunctionRegistry.register(HashEmbedding)
EmbeddingFunctionRegistry.register(OpenAICompatibleEmbedding)


def build_embedding_from_env() -> tuple[Any, str]:
    """按环境变量构造嵌入函数，返回 (ef, 模式说明)。"""
    import os

    provider = (os.getenv("EMBEDDING_PROVIDER") or "hash").strip().lower()
    if provider in {"openai", "openai_compatible", "compatible"}:
        base_url = os.getenv("EMBEDDING_BASE_URL", "").strip()
        if not base_url:
            raise RuntimeError("EMBEDDING_PROVIDER=openai 时必须设置 EMBEDDING_BASE_URL")
        ef = OpenAICompatibleEmbedding(
            base_url=base_url,
            api_key=os.getenv("EMBEDDING_API_KEY", "").strip(),
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-v3").strip(),
            dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
            batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "8")),
            send_dimensions=os.getenv("EMBEDDING_SEND_DIMENSIONS", "false").lower() == "true",
        )
        return ef, f"openai-compatible({ef.model}, dim={ef.dimension})"
    ef = HashEmbedding(dimension=int(os.getenv("EMBEDDING_DIMENSION", "512")))
    return ef, "hash-offline(无语义能力，仅用于跑通链路)"
