"""确定性本地嵌入（feature hashing）。

不依赖任何外部模型或网络，保证：
- 同一份语料每次入库得到完全相同的向量（可复现，评审环境无需 API Key）
- 中文按字 / 双字切分，英文按标识符与下划线切分，兼顾规格文本与代码符号

如需换成真实 embedding 模型（如 MiniMax / OpenAI 兼容接口），只需替换本文件的
`embed_documents` / `embed_query` 实现，其余模块无感知。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

_WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*|\d+(?:\.\d+)?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

DEFAULT_DIM = 384


def _hash(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if (value >> 63) & 1 else -1.0


def tokenize(text: str) -> list[str]:
    """英文/标识符 + 中文单字与双字 + 数字。"""
    tokens: list[str] = []

    for word in _WORD_RE.findall(text):
        lowered = word.lower()
        tokens.append(lowered)
        # min_amount -> min / amount，提升代码符号召回
        if "_" in lowered:
            tokens.extend(part for part in lowered.split("_") if part)

    for run in _CJK_RE.findall(text):
        tokens.extend(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))

    return tokens


class HashingEmbedding:
    """把文本映射为 dim 维 L2 归一化向量。"""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        counts: dict[str, int] = {}
        for token in tokenize(text):
            counts[token] = counts.get(token, 0) + 1

        for token, count in counts.items():
            index, sign = _hash(token, self.dim)
            # 次线性词频，避免长文档被高频词主导
            vector[index] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)
