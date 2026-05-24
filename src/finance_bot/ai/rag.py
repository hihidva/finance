"""ChromaDB manager — 2 collections phục vụ RAG:

1. `signals_history`
     Mỗi document = 1 signal đã có ÍT NHẤT 1 outcome (tức bot đã xem được kết quả thật).
     Document text mô tả: asset, side, indicators chính, kết quả P&L sau 1d/3d/7d/30d.
     Khi gặp tình huống mới giống lịch sử → retrieve để LLM "rút kinh nghiệm".

2. `knowledge`
     Kiến thức user-fed (hoặc auto-fed sau này): patterns thị trường, macro rules,
     bài học. Là cách để bot "được cập nhật kiến thức mới" theo yêu cầu của user.

Cả 2 collection dùng external embedding (sentence-transformers) để tránh phụ thuộc
vào ChromaDB default model (nặng + tải mạng lần đầu).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from finance_bot.ai.embedding import embed, embed_one
from finance_bot.logger import logger
from finance_bot.settings import PROJECT_ROOT, get_settings


SIGNALS_COLLECTION = "signals_history"
KNOWLEDGE_COLLECTION = "knowledge"


class RagUnavailable(RuntimeError):
    pass


@dataclass
class RetrievedDoc:
    id: str
    text: str
    metadata: dict
    distance: float


# ----------------------------------------------------------------------
# Chroma client
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def _client():
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
    except ImportError as exc:
        raise RagUnavailable("chromadb chưa cài. Chạy `uv sync`.") from exc

    s = get_settings()
    chroma_path = Path(s.chroma_dir)
    if not chroma_path.is_absolute():
        chroma_path = PROJECT_ROOT / chroma_path
    chroma_path.mkdir(parents=True, exist_ok=True)

    return chromadb.PersistentClient(
        path=str(chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
    )


def _collection(name: str):
    return _client().get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


# ----------------------------------------------------------------------
# Generic upsert / query
# ----------------------------------------------------------------------
def upsert(collection: str, ids: list[str], texts: list[str],
           metadatas: list[dict]) -> None:
    if not ids:
        return
    vectors = embed(texts)
    _collection(collection).upsert(
        ids=ids,
        embeddings=vectors,
        documents=texts,
        metadatas=metadatas,
    )


def delete(collection: str, ids: Iterable[str]) -> None:
    ids = list(ids)
    if not ids:
        return
    _collection(collection).delete(ids=ids)


def query(collection: str, text: str, n: int = 5,
          where: dict | None = None) -> list[RetrievedDoc]:
    if n <= 0:
        return []
    try:
        vec = embed_one(text)
    except Exception as exc:
        logger.warning("embedding failed for RAG query: {}", exc)
        return []
    try:
        res = _collection(collection).query(
            query_embeddings=[vec], n_results=n, where=where,
        )
    except Exception as exc:
        logger.warning("Chroma query failed for {}: {}", collection, exc)
        return []

    out: list[RetrievedDoc] = []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for i, doc, meta, dist in zip(ids, docs, metas, dists):
        out.append(RetrievedDoc(id=i, text=doc, metadata=meta or {}, distance=float(dist)))
    return out


def count(collection: str) -> int:
    try:
        return _collection(collection).count()
    except Exception as exc:
        logger.warning("Chroma count failed for {}: {}", collection, exc)
        return 0


def status_summary() -> dict[str, int]:
    return {
        SIGNALS_COLLECTION: count(SIGNALS_COLLECTION),
        KNOWLEDGE_COLLECTION: count(KNOWLEDGE_COLLECTION),
    }
