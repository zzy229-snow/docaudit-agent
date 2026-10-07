"""Milvus 向量检索集成测试(PRD §9.3 混合召回/§13.2 Recall@K)。

运行前提(缺任一项则自动 skip):
1. 装了 ``requirements-rag.txt``(其中 milvus-lite 是"本地库文件"模式必需项);
2. 已建索引:``<RAG环境>/python -m app.rag.build_index``;
3. 能拿到 bge-m3 权重:设 ``BGE_MODEL_DIR``,或把权重放到 ``data/rag/bge-m3``。

为什么这里要额外断言"确实用了 milvus":``retrieve_policy`` 在检索异常时会**静默降级到 mock**
(见 ``app/rag/retriever.py`` 的降级分支)。没有这条断言时,环境没配好测试照样全绿 ——
2026-10-07 实测踩过这个坑:三个用例在**没配模型路径**的情况下"通过",其实一次向量检索都没发生。

黄金断言用 top-2 而不是第 1 名:排名会被融合策略影响(见 vector_store.SPARSE_WEIGHT_ENV 的实测记录),
把融合参数钉死在测试里会让测试变脆;产品侧需要的是"命中正确的住宿标准条款"。
"""

import os
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

try:
    from FlagEmbedding import BGEM3FlagModel
    _HAS_BGE = True
except ImportError:
    _HAS_BGE = False


def _model_path_configured() -> bool:
    """必须有可用的 bge-m3 权重目录,否则 milvus 检索必然降级,测试没有意义。"""
    configured = os.environ.get("BGE_MODEL_DIR", "").strip()
    if configured and Path(configured).exists():
        return True
    return (BASE / "data" / "rag" / "bge-m3").exists()


def _index_ready() -> bool:
    try:
        from app.rag.vector_store import COLLECTION, VectorStore  # noqa: F401
        store = VectorStore()
        total = store.client.get_collection_stats(store.collection).get("row_count", 0)
        return total > 0
    except Exception:
        return False


@unittest.skipUnless(
    _HAS_BGE and _model_path_configured() and _index_ready(),
    "需要 bge-m3 权重(设 BGE_MODEL_DIR 或放到 data/rag/bge-m3)与已建立的 Milvus 索引"
    "(先运行 python -m app.rag.build_index)",
)
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

    def test_engine_is_milvus_not_silently_degraded(self):
        """防假信心:检索必须真的走 milvus,不能降级到 mock 还显示通过。"""
        from app.rag import retriever
        retriever.retrieve_policy(None, query="北京出差住宿标准是多少元一晚")
        self.assertIn("milvus", retriever.RETRIEVAL_NOTE)
        self.assertNotIn("降级", retriever.RETRIEVAL_NOTE)

    def test_retrieve_hotel_limit_first_city(self):
        from app.rag.retriever import retrieve_policy
        result = retrieve_policy(None, query="北京出差住宿标准是多少元一晚")
        self.assertTrue(result)
        # 一线城市条款须进 top-2(被稀疏通道噪声挤出行列视为失败)
        self.assertIn("TRAVEL-V1-4.2-A", [item.chunk_id for item in result[:2]])

    def test_retrieve_hotel_limit_other_city(self):
        from app.rag.retriever import retrieve_policy
        result = retrieve_policy(None, query="杭州出差酒店费用上限")
        self.assertTrue(result)
        self.assertIn("TRAVEL-V1-4.2-B", [item.chunk_id for item in result[:2]])

    def test_contract_output_fields(self):
        from app.rag.retriever import retrieve_policy
        result = retrieve_policy(None, query="上海住宿标准")
        self.assertTrue(result)
        self.assertTrue(result[0].chunk_id.startswith("TRAVEL-"),
                        f"住宿标准类查询不应召回无关制度:{[i.chunk_id for i in result]}")
        for item in result:
            self.assertTrue(item.chunk_id)
            self.assertTrue(item.section)
            self.assertTrue(item.content)


if __name__ == "__main__":
    unittest.main()
