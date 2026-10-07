"""Milvus Lite 向量库封装(PRD §9.1/§9.3)。

- 默认只用稠密向量(bge-m3 dense,1024 维)检索;可选稠密+稀疏加权融合(见 SPARSE_WEIGHT_ENV);
- 元数据:policy_id/version/department/expense_type/effective_from/to 用于过滤(§9.3 元数据过滤);
- 检索结果映射回 PolicyEvidence 契约(chunk_id/section/content/score),不新增契约字段;
- 编码依赖 FlagEmbedding+torch,不在主 requirements.txt,装 requirements-rag.txt 后启用。

实现说明:
- Milvus Lite 文件库,每次连接后需 load_collection 才能检索;
- JSON 字段不能建标量索引;2.6 无 num_entities,用 get_collection_stats;
- 稀疏检索 metric_type=IP,稠密用 L2。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from app.models.audit import PolicyEvidence

COLLECTION = "policy_chunks"
DENSE_DIM = 1024  # bge-m3 稠密维度
DENSE_FIELD = "vector"
SPARSE_FIELD = "sparse_vector"

#: 稀疏通道权重(环境变量 ``RAG_SPARSE_WEIGHT``)。0 = 只用稠密通道(默认)。
#:
#: 为什么默认稠密优先(2026-10-07 实测,三条黄金查询):本项目语料(16 chunk)下稀疏通道的
#: top1 常与语义无关 —— 问"杭州出差酒店费用上限"返回 PHARM-V1-3.3(药品制度);而等权 RRF
#: 把两路秩次相加,会让"两路都排中等"的无关 chunk 压过"稠密通道第 1 名"的正确条款
#: (实测把 TRAVEL-V1-4.2-B 挤出第 1 名,同一查询两次运行还会因平票翻转)。
#: 稠密通道三条黄金查询全中,因此默认稠密优先;需要精确词命中(编号/金额类)时可设
#: ``RAG_SPARSE_WEIGHT=0.3`` 打开稠密+稀疏加权融合。
SPARSE_WEIGHT_ENV = "RAG_SPARSE_WEIGHT"


class VectorStore:
    """面向制度检索的最小 Milvus 封装。"""

    def __init__(self, uri: str | None = None, collection: str = COLLECTION) -> None:
        from pymilvus import MilvusClient

        self.uri = uri or os.environ.get("MILVUS_URI", str(Path(__file__).resolve().parents[2] / "data" / "rag" / "policy.db"))
        Path(self.uri).parent.mkdir(parents=True, exist_ok=True)
        self.collection = collection
        self.client = MilvusClient(uri=self.uri)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        if self.client.has_collection(self.collection):
            self.client.load_collection(self.collection)
            return
        schema = self.client.create_schema(auto_id=True)
        schema.add_field(field_name="pk", datatype=DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="section_path", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="policy_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="version", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="department", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="expense_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="effective_from", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="effective_to", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=DENSE_DIM)
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
        index = self.client.prepare_index_params()
        index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="L2")
        index.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
        self.client.create_collection(self.collection, schema=schema, index_params=index)
        self.client.load_collection(self.collection)

    def insert(self, rows: list[dict]) -> int:
        self.client.insert(self.collection, rows)
        return self.client.get_collection_stats(self.collection).get("row_count", 0)

    def clear(self) -> None:
        self.client.drop_collection(self.collection)
        self._ensure_collection()

    def hybrid_search(
        self,
        dense_vec: list[float],
        sparse_vec: dict[int, float],
        limit: int = 5,
        filters: str | None = None,
        sparse_weight: float | None = None,
    ) -> list[PolicyEvidence]:
        """检索制度条款。默认**只用稠密通道**(依据见 :data:`SPARSE_WEIGHT_ENV` 注释)。

        ``sparse_weight > 0`` 时启用稠密+稀疏加权融合,用于需要精确词命中的场景
        (编号/金额类查询);等权 RRF 在本项目的语料上会把无关 chunk 抬到第 1 名。
        """
        from pymilvus import AnnSearchRequest, WeightedRanker

        if sparse_weight is None:
            try:
                sparse_weight = float(os.environ.get(SPARSE_WEIGHT_ENV, "0") or 0)
            except ValueError:
                sparse_weight = 0.0
        filter_expr = filters or ""
        fields = ["chunk_id", "section_path", "content", "policy_id", "version"]
        # 候选池比最终返回大:原来两路各只取 limit(默认 2)个,RRF 平票时名次会翻转
        candidates = max(limit * 3, 10)

        if sparse_weight <= 0 or not sparse_vec:
            hits = self.client.search(
                collection_name=self.collection,
                data=[dense_vec],
                anns_field=DENSE_FIELD,
                search_params={"metric_type": "L2"},
                limit=limit,
                filter=filter_expr,
                output_fields=fields,
            )
        else:
            dense_req = AnnSearchRequest(data=[dense_vec], anns_field=DENSE_FIELD,
                                         param={"metric_type": "L2"}, limit=candidates)
            sparse_req = AnnSearchRequest(data=[sparse_vec], anns_field=SPARSE_FIELD,
                                          param={"metric_type": "IP"}, limit=candidates)
            hits = self.client.hybrid_search(
                collection_name=self.collection,
                reqs=[dense_req, sparse_req],
                ranker=WeightedRanker(1.0, float(sparse_weight)),
                limit=limit,
                filter=filter_expr,
                output_fields=fields,
            )

        evidence = []
        for hit in hits[0]:
            entity = hit.get("entity", {})
            evidence.append(PolicyEvidence(
                chunk_id=entity.get("chunk_id", ""),
                section=entity.get("section_path", ""),
                content=entity.get("content", ""),
                score=round(hit.get("distance", 0.0), 4),
            ))
        return evidence


_LOCK = threading.Lock()
_STORES: dict[tuple[str, str], VectorStore] = {}


def get_store(uri: str | None = None, collection: str = COLLECTION) -> VectorStore:
    """进程内复用同一个 :class:`VectorStore`(按 ``uri``+collection 缓存)。

    两处收益:
    1. 省掉每次检索重新连库 + ``load_collection`` 的开销;
    2. **Milvus Lite 是文件级单进程独占锁** —— 进程内只持有一个连接,语义更清楚:
       "谁先连上谁持有,直到进程退出"(多进程并发的限制见 docs/RAG_INTEGRATION.md)。
    """
    key = (uri or os.environ.get("MILVUS_URI", str(Path(__file__).resolve().parents[2] / "data" / "rag" / "policy.db")), collection)
    with _LOCK:
        store = _STORES.get(key)
        if store is None:
            store = VectorStore(uri=uri, collection=collection)
            _STORES[key] = store
        return store


def reset_stores() -> None:
    """丢弃缓存的连接(测试或切换 ``MILVUS_URI`` 后使用)。"""
    with _LOCK:
        _STORES.clear()
