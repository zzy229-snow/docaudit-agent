"""Milvus Lite 向量库封装(PRD §9.1/§9.3)。

- 稠密向量(bge-m3 dense,1024 维)+ 稀疏向量(bge-m3 lexical)混合检索,RRF 融合;
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
from pathlib import Path

from app.models.audit import PolicyEvidence

COLLECTION = "policy_chunks"
DENSE_DIM = 1024  # bge-m3 稠密维度


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
    ) -> list[PolicyEvidence]:
        """稠密+稀疏混合检索,RRF 融合(PRD §9.3 混合召回+重排的起点)。"""
        from pymilvus import AnnSearchRequest, RRFRanker

        dense_req = AnnSearchRequest(data=[dense_vec], anns_field="vector",
                                     param={"metric_type": "L2"}, limit=limit)
        sparse_req = AnnSearchRequest(data=[sparse_vec], anns_field="sparse_vector",
                                      param={"metric_type": "IP"}, limit=limit)
        hits = self.client.hybrid_search(
            collection_name=self.collection,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=60),
            limit=limit,
            output_fields=["chunk_id", "section_path", "content", "policy_id", "version"],
            filter=filters or "",
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
