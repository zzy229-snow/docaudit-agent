"""检索降级链测试(milvus → local → mock)。

背景:原来的降级链是 milvus → mock,而 mock **只看城市名字符串匹配**,连查询问的是什么都不看
(实测问"发票代码和发票号码怎么填",mock 照样返回住宿标准条款)。把这种结果当作"制度依据"
写进审核报告,等于把"依据不可用"伪装成"有依据"——对审核系统是隐患。

现在的约定:
- milvus 失败 → 退到 local(真关键词检索,结果可引用);
- local 也失败 → 才退到 mock(并注明两层失败原因);
- 任何一层降级都写进 RETRIEVAL_NOTE(会进审核 trace)。
"""
import os
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.rag import retriever  # noqa: E402


def _boom(*_args, **_kwargs):
    raise RuntimeError("模拟检索失败")


class DegradeChainTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = retriever.RAG_MODE
        self._old_milvus = retriever._retrieve_milvus

    def tearDown(self):
        retriever.RAG_MODE = self._old_mode
        retriever._retrieve_milvus = self._old_milvus
        retriever.set_retrieval_note("")

    def test_milvus_failure_falls_back_to_local_not_mock(self):
        """核心断言:milvus 挂了要退到 local(真检索),而不是直接退到 mock。"""
        retriever.RAG_MODE = "milvus"
        retriever._retrieve_milvus = _boom

        result = retriever.retrieve_policy("北京", query="北京出差住宿标准是多少元一晚")

        self.assertIn("降级 local", retriever.RETRIEVAL_NOTE)
        self.assertNotIn("mock", retriever.RETRIEVAL_NOTE)
        self.assertTrue(result, "退到 local 后应有命中")
        # 降级结果必须"可引用":条款号和正文都不能空
        for item in result:
            self.assertTrue(item.chunk_id)
            self.assertTrue(item.content)
        self.assertTrue(result[0].chunk_id.startswith("TRAVEL"),
                        f"住宿标准查询应命中差旅制度:{[i.chunk_id for i in result]}")

    def test_local_also_failing_falls_back_to_mock_with_both_reasons(self):
        """两层都挂才用 mock,并且两个原因都要留痕(否则无从定位)。"""
        retriever.RAG_MODE = "milvus"
        retriever._retrieve_milvus = _boom
        from app.rag import local_store
        saved = local_store.retrieve_local
        local_store.retrieve_local = _boom
        try:
            result = retriever.retrieve_policy("北京", query="北京出差住宿标准")
        finally:
            local_store.retrieve_local = saved

        self.assertIn("降级 mock", retriever.RETRIEVAL_NOTE)
        self.assertIn("local 亦失败", retriever.RETRIEVAL_NOTE)
        self.assertIn("模拟检索失败", retriever.RETRIEVAL_NOTE)
        self.assertTrue(result, "最后一层兜底仍应返回规则匹配结果,不能空手而归")

    def test_local_mode_failure_degrades_to_mock(self):
        retriever.RAG_MODE = "local"
        from app.rag import local_store
        saved = local_store.retrieve_local
        local_store.retrieve_local = _boom
        try:
            result = retriever.retrieve_policy("上海", query="上海住宿标准")
        finally:
            local_store.retrieve_local = saved

        self.assertIn("local 检索失败，已降级 mock", retriever.RETRIEVAL_NOTE)
        self.assertTrue(result)

    def test_successful_milvus_is_not_reported_as_degraded(self):
        """没降级就不能出现"降级"字样(否则 trace 会误导运维去查根本没发生的故障)。"""
        from app.models.audit import PolicyEvidence
        retriever.RAG_MODE = "milvus"
        retriever._retrieve_milvus = lambda *a, **k: [
            PolicyEvidence(chunk_id="TRAVEL-V1-4.2-A", section="4.2", content="一线城市 600 元", score=0.4)
        ]
        result = retriever.retrieve_policy("北京", query="北京住宿标准")

        self.assertNotIn("降级", retriever.RETRIEVAL_NOTE)
        self.assertIn("milvus", retriever.RETRIEVAL_NOTE)
        self.assertEqual("TRAVEL-V1-4.2-A", result[0].chunk_id)

    def test_degrade_to_local_handles_query_without_city(self):
        """只给文件名(没有 city/query)时也不能因为降级就崩掉。"""
        retriever.RAG_MODE = "milvus"
        retriever._retrieve_milvus = _boom
        result = retriever.retrieve_policy(None, files=[("invoice.txt", b"")])
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
