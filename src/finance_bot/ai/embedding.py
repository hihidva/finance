"""Sentence-Transformers wrapper.

Sử dụng model multilingual nhẹ (`paraphrase-multilingual-MiniLM-L12-v2`, ~120MB,
384-dim) — vừa hỗ trợ tiếng Việt vừa hỗ trợ tiếng Anh, đủ cho RAG semantic search.

Lazy-loaded singleton để tránh download model khi chỉ chạy CLI nhẹ.
"""
from __future__ import annotations

from functools import lru_cache

from finance_bot.logger import logger
from finance_bot.settings import get_settings


class EmbeddingUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_encoder():
    """Lazy-load sentence-transformer; return the model singleton."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingUnavailable(
            "sentence-transformers chưa cài. Chạy `uv sync`."
        ) from exc

    s = get_settings()
    logger.info("Loading embedding model: {}", s.embedding_model)
    return SentenceTransformer(s.embedding_model)


def embed(texts: list[str]) -> list[list[float]]:
    """Encode a batch of strings → list of float vectors."""
    if not texts:
        return []
    model = get_encoder()
    arr = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return arr.tolist()


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def embedding_dim() -> int:
    return int(get_encoder().get_sentence_embedding_dimension())
