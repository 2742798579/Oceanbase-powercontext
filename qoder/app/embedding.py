"""文本向量化：优先用真实的语义模型，缺依赖时退回确定性哈希。

两种后端：
  onnx  ——  BAAI/bge-small-zh-v1.5 的 ONNX 版本（512 维），中文语义检索效果好，
             只需要 onnxruntime + tokenizers，不需要装 torch。模型用
             `python scripts/fetch_model.py` 下载（约 95MB，默认走 hf-mirror 镜像）。
  hash  ——  带符号的特征哈希（384 维），零依赖、离线可跑，用来保证评审在没有
             模型文件时也能把整条链路跑通；词面重合度尚可，语义能力弱。

选哪个由环境变量 EMBEDDING_PROVIDER 控制，默认 auto：
  auto  有模型文件就用 onnx，否则用 hash
  onnx  强制 onnx（缺文件直接报错，避免悄悄降级）
  hash  强制 hash
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Protocol

from .config import EMBEDDING_DIM, MODEL_DIR

log = logging.getLogger("recall.embedding")

QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, text: str, is_query: bool = False) -> list[float]: ...


# ---------- 后端一：确定性哈希 ----------

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]*|\d+(?:\.\d+)?")


class HashEmbedder:
    name = "hash"
    dim = EMBEDDING_DIM

    def embed(self, text: str, is_query: bool = False) -> list[float]:
        dim = self.dim
        vec = [0.0] * dim
        norm_text = unicodedata.normalize("NFKC", text or "").lower()

        features: list[tuple[str, float]] = []
        for word in _WORD_RE.findall(norm_text):
            if len(word) == 1 and not word.isdigit():
                continue
            features.append((word, 1.0))
        for run in re.findall(r"[\u4e00-\u9fff]+", norm_text):
            for ch in run:
                features.append((ch, 0.35))
            for i in range(len(run) - 1):
                features.append((run[i : i + 2], 1.0))

        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm < 1e-9:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]


# ---------- 后端二：bge-small-zh ONNX ----------


class OnnxEmbedder:
    name = "onnx:bge-small-zh-v1.5"
    dim = 512

    def __init__(self, model_path: Path, tokenizer_path: Path, threads: int = 2) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=512)
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.output_name = self.session.get_outputs()[0].name
        log.info("已加载语义模型 %s (dim=%d)", self.name, self.dim)

    def embed(self, text: str, is_query: bool = False) -> list[float]:
        import numpy as np

        payload = (QUERY_PREFIX + text) if is_query else (text or "")
        enc = self.tokenizer.encode(payload or " ")
        feed = {
            "input_ids": np.array([enc.ids], dtype=np.int64),
            "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
        }
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)

        hidden = self.session.run([self.output_name], feed)[0]
        pooled = hidden[:, 0, :] if hidden.ndim == 3 else hidden
        vec = pooled[0].astype("float32")
        norm = float(np.linalg.norm(vec))
        if norm < 1e-9:
            vec = np.zeros(self.dim, dtype="float32")
            vec[0] = 1.0
            return vec.tolist()
        return (vec / norm).tolist()


# ---------- 选择与缓存 ----------

_embedder: Embedder | None = None


def _model_files() -> tuple[Path, Path]:
    return MODEL_DIR / "model.onnx", MODEL_DIR / "tokenizer.json"


def build_embedder() -> Embedder:
    mode = os.environ.get("EMBEDDING_PROVIDER", "auto").strip().lower()
    model_path, tokenizer_path = _model_files()
    have_files = model_path.exists() and tokenizer_path.exists()

    if mode == "hash":
        return HashEmbedder()
    if mode == "onnx" and not have_files:
        raise RuntimeError(
            f"EMBEDDING_PROVIDER=onnx 但缺少模型文件：{MODEL_DIR}。"
            "先运行 python scripts/fetch_model.py"
        )
    if have_files:
        try:
            return OnnxEmbedder(model_path, tokenizer_path)
        except Exception as exc:  # noqa: BLE001
            if mode == "onnx":
                raise
            log.warning("语义模型加载失败（%s），退回 hash 向量", exc)
    else:
        log.warning(
            "未找到语义模型文件，使用 hash 向量（仅词面匹配）。"
            "要获得语义检索效果，运行 python scripts/fetch_model.py"
        )
    return HashEmbedder()


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = build_embedder()
    return _embedder


def embed(text: str, is_query: bool = False) -> list[float]:
    return get_embedder().embed(text, is_query=is_query)


def embedding_dim() -> int:
    return get_embedder().dim


def to_vector_literal(vec: list[float]) -> str:
    """seekdb 的 VECTOR 字面量格式：'[0.1,0.2,...]'"""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
