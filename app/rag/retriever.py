"""制度检索器(PRD §9.3)。RAG_MODE 控制后端:

- mock   (默认):规则匹配基线,零依赖,保持协作者原有行为;
- local  :本地制度切片关键词检索,零外部服务,用于生产化演示前的真实检索基线;
- milvus:向量混合检索(需已运行 build_index.py 建立索引;编码依赖见 requirements-rag.txt)。

任何后端失败都降级回 mock(PRD §23.5「检索失败降级」),输出契约 PolicyEvidence 不变。
"""
from __future__ import annotations

import os

from app.config import load_environment
from app.models.audit import PolicyEvidence
from .indexer import load_policy

load_environment()

RAG_MODE = os.environ.get("RAG_MODE", "mock").strip().lower()


def retrieve_policy(city: str | None, query: str | None = None) -> list[PolicyEvidence]:
    """按城市(或自由文本 query)检索制度。返回 PolicyEvidence 列表,契约不变。"""
    if RAG_MODE == "local":
        try:
            return _retrieve_local(query or _query_from_city(city))
        except Exception as exc:  # noqa: BLE001
            print(f"[rag] local 检索失败,降级 mock: {exc!r}")
            return _retrieve_mock(city)
    if RAG_MODE == "milvus":
        try:
            return _retrieve_milvus(query or city or "")
        except Exception as exc:  # noqa: BLE001 — 检索失败必须降级不可中断主流程
            print(f"[rag] milvus 检索失败,降级 mock: {exc!r}")
            return _retrieve_mock(city)
    return _retrieve_mock(city)


def _retrieve_mock(city: str | None) -> list[PolicyEvidence]:
    """规则匹配基线(协作者原逻辑,未改动):按城市命中条款,其余城市取兜底条款。"""
    if not city:
        return []
    city = city.removesuffix("市")
    matches = []
    for item in load_policy():
        if city in item.content:
            matches.append(item.model_copy(update={"score": 1.0}))
        elif "其他城市" in item.content and city not in {"北京", "上海", "广州", "深圳"}:
            matches.append(item.model_copy(update={"score": 0.8}))
    return matches[:2]


def _retrieve_local(query: str) -> list[PolicyEvidence]:
    from .local_store import retrieve_local

    return retrieve_local(query, limit=2)


def _retrieve_milvus(query: str) -> list[PolicyEvidence]:
    """Milvus 混合检索:编码查询 → 稠密+稀疏 → RRF 融合。"""
    from FlagEmbedding import BGEM3FlagModel
    from .vector_store import VectorStore

    model_path = os.environ.get(
        "BGE_MODEL_DIR",
        r"<bge-m3 目录>",
    )
    model = BGEM3FlagModel(model_name_or_path=model_path, use_fp16=False)
    out = model.encode([query], return_dense=True, return_sparse=True)
    dense = out["dense_vecs"][0].tolist()
    sparse = dict(out["lexical_weights"][0])

    store = VectorStore()
    return store.hybrid_search(dense_vec=dense, sparse_vec=sparse, limit=2)


def _query_from_city(city: str | None) -> str:
    if not city:
        return ""
    return f"{city} 住宿标准 差旅 报销 酒店"
