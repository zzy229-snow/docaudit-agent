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
    """必须有可用的 bge-m3 权重目录,否则 milvus 检索必然降级,测试没有意义。

    这里**显式读一次仓库 .env**:单测默认不加载 .env(见 tests/__init__.py 的隔离约定),
    但本用例是"真机集成验证",机器级配置(权重放在哪)本来就该从 .env 读。
    不读的话,开发者在 .env 里配了 BGE_MODEL_DIR、测试却一直 skip —— 等于没验证。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE / ".env", override=False)
    except Exception:
        pass
    configured = os.environ.get("BGE_MODEL_DIR", "").strip()
    if configured and Path(configured).exists():
        return True
    return (BASE / "data" / "rag" / "bge-m3").exists()


def _index_state() -> tuple:
    """返回 (是否可用, 不可用原因)。原因写进 skip 提示,免得把"锁被占用"误读成"没建索引"。"""
    try:
        from app.rag.vector_store import COLLECTION, VectorStore  # noqa: F401
        store = VectorStore()
        total = store.client.get_collection_stats(store.collection).get("row_count", 0)
        return (total > 0), ("" if total > 0 else "集合已连接但为空,请先运行 python -m app.rag.build_index")
    except Exception as exc:
        cause = exc.__cause__
        text = f"{exc!r}"
        if cause is not None and repr(cause) != repr(exc):
            text += f"(根因: {cause!r})"
        return False, text


_INDEX_OK, _INDEX_WHY = _index_state()


@unittest.skipUnless(
    _HAS_BGE and _model_path_configured() and _INDEX_OK,
    "需要 bge-m3 权重(设 BGE_MODEL_DIR 或放到 data/rag/bge-m3)与可用的 Milvus 索引"
    "(先运行 python -m app.rag.build_index);当前索引不可用原因: " + (_INDEX_WHY or "bge-m3 权重缺失"),
)
class RagMilvusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.rag import retriever
        cls._old = retriever.RAG_MODE
        retriever.RAG_MODE = "milvus"
        # 兜底:用例执行顺序不保证,别的用例可能清理过环境变量;这里再读一次 .env,
        # 保证权重路径在本进程可见(否则会静默降级到 mock,断言失败但看不出原因)
        if not os.environ.get("BGE_MODEL_DIR", "").strip():
            try:
                from dotenv import load_dotenv
                load_dotenv(BASE / ".env", override=False)
            except Exception:
                pass

    @classmethod
    def tearDownClass(cls):
        from app.rag import retriever
        retriever.RAG_MODE = cls._old

    def test_engine_is_milvus_not_silently_degraded(self):
        """防假信心:检索必须真的走 milvus,不能降级到 mock 还显示通过;重排层也要留痕。"""
        from app.rag import retriever
        retriever.retrieve_policy(None, query="北京出差住宿标准是多少元一晚")
        self.assertIn("milvus", retriever.RETRIEVAL_NOTE)
        self.assertNotIn("降级", retriever.RETRIEVAL_NOTE)
        # 排序问题的修法是重排,不是悄悄少给几条 —— 用了哪一层必须写进 trace
        self.assertIn("重排=", retriever.RETRIEVAL_NOTE)

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
