"""
embedding.py — 本地向量嵌入（使用 BGE 模型，纯本地，无 API 依赖）

使用 sentence-transformers 加载 BAAI/bge-small-zh-v1.5，
对中文记忆文本生成 512 维嵌入向量。

设计：
- 懒加载：第一次调用时加载模型，后续复用
- CPU 推理：模型仅 33MB，CPU 推理足够快（<100ms/条）
- 批量处理：支持一次编码多条文本
- 降级策略：模型加载失败时返回 None，不影响主流程
"""

from __future__ import annotations

import logging
import os
import numpy as np
from typing import Any

if os.environ.get("MEMORY_AGENT_HF_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

if os.environ.get("MEMORY_AGENT_HF_DISABLE_SSL_VERIFICATION", "").strip().lower() in {"1", "true", "yes", "on"}:
    os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFICATION", "1")

from .config import EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, EMBEDDING_DEVICE, EMBEDDING_BATCH_SIZE

logger = logging.getLogger("memory_agent.embedding")

# ── 全局模型实例（懒加载）──
_model: Any = None  # SentenceTransformer 实例


def _load_model() -> Any:
    """
    加载 sentence-transformers 模型。

    首次加载会下载模型（~33MB），之后从缓存读取。
    如需强制离线，设置 MEMORY_AGENT_HF_OFFLINE=1。
    缓存目录：~/.cache/huggingface/hub/
    """
    global _model
    if _model is not None:
        return _model

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.error(
            "sentence-transformers not installed. "
            "Run: pip install sentence-transformers"
        )
        return None

    try:
        logger.info(
            "Loading embedding model: %s (device=%s)",
            EMBEDDING_MODEL, EMBEDDING_DEVICE,
        )
        _model = SentenceTransformer(
            EMBEDDING_MODEL,
            device=EMBEDDING_DEVICE,
        )
        logger.info("Embedding model loaded successfully")
        return _model
    except Exception as e:
        logger.error("Failed to load embedding model: %s", e)
        return None


def embed_text(text: str) -> list[float] | None:
    """
    将单条文本编码为向量。

    Returns:
        512 维 float 列表，或 None（模型加载失败时）
    """
    model = _load_model()
    if model is None:
        return None

    try:
        # BGE 模型推荐在 query 前加指令（但此处编码的是记忆内容而非查询）
        emb = model.encode(text, normalize_embeddings=True)
        return emb.tolist() if isinstance(emb, np.ndarray) else list(emb)
    except Exception as e:
        logger.error("Embedding failed: %s", e)
        return None


def embed_batch(texts: list[str]) -> list[list[float]] | None:
    """
    批量编码多条文本（比逐条调用更高效）。

    Returns:
        (N, 512) 的向量列表，或 None（模型加载失败时）
    """
    if not texts:
        return []

    model = _load_model()
    if model is None:
        return None

    try:
        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=EMBEDDING_BATCH_SIZE,
            show_progress_bar=False,
        )
        if isinstance(embeddings, np.ndarray):
            return [e.tolist() for e in embeddings]
        return [list(e) for e in embeddings]
    except Exception as e:
        logger.error("Batch embedding failed: %s", e)
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    计算两个向量的余弦相似度。

    向量已归一化时等价于点积。
    """
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    dot = float(np.dot(a_arr, b_arr))
    norm_a = float(np.linalg.norm(a_arr))
    norm_b = float(np.linalg.norm(b_arr))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def is_model_loaded() -> bool:
    """检查模型是否已加载。"""
    global _model
    return _model is not None


def unload_model() -> None:
    """释放模型内存（用于重置/测试）。"""
    global _model
    if _model is not None:
        del _model
        _model = None
        logger.info("Embedding model unloaded")
