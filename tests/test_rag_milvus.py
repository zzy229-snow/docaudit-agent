"""Milvus 向量检索集成测试(PRD §9.3 混合召回/§13.2 Recall@K)。

仅在具备 bge-m3 编码环境且已建立索引时运行(否则 skip):先执行
    <RAG 环境>/python.exe -m app.rag.build_index
再以同一环境运行本测试。
"""

import json
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

try:
    from FlagEmbedding import BGEM3FlagModel
    _HAS_BGE = True
except ImportError:
    _HAS_BGE = False


def _index_ready() -> bool:
    from pymilvus import MilvusClient
    try:
        from app.rag.vector_store import COLLECTION, VectorStore
        store = VectorStore()
        total = store.client.get_collection_stats(store.collection).get("row_count", 0)
        return total > 0
    except Exception:
        return False


@unittest.skipUnless(_HAS_BGE and _index_ready(), "需要 bge-m3 环境与已建立的 Milvus 索引(先运行 build_index.py)")
class RagMilvusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.rag import retriever
        cls._old = retriever.RAG_MODE
        retriever.RAG_MODE = "milvus"

    @classmethod
    def tearDownClass(cls):
        from app.rag import retriever
        retriever.RAG_MODE = cls._old

    def test_retrieve_hotel_limit_first_city(self):
        from app.rag.retriever import retrieve_policy
        result = retrieve_policy(None, query="北京出差住宿标准是多少元一晚")
        self.assertTrue(result)
        self.assertEqual(result[0].chunk_id, "TRAVEL-V1-4.2-A")  # Recall@1 命中黄金条款

    def test_retrieve_hotel_limit_other_city(self):
        from app.rag.retriever import retrieve_policy
        result = retrieve_policy(None, query="杭州出差酒店费用上限")
        self.assertTrue(result)
        self.assertEqual(result[0].chunk_id, "TRAVEL-V1-4.2-B")

    def test_contract_output_fields(self):
        from app.rag.retriever import retrieve_policy
        result = retrieve_policy(None, query="上海住宿标准")
        for item in result:
            self.assertTrue(item.chunk_id.startswith("TRAVEL-"))
            self.assertTrue(item.section)
            self.assertTrue(item.content)


if __name__ == "__main__":
    unittest.main()
