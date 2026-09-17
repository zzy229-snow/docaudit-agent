"""RAG mock 路由测试(PRD §9.3/§23.5):默认零依赖,协作者基线行为不变,失败可降级。"""

import unittest
from unittest.mock import patch

from app.rag import retriever
from app.rag.retriever import retrieve_policy


class RagMockRouteTests(unittest.TestCase):
    def setUp(self):
        self._mode = retriever.RAG_MODE
        retriever.RAG_MODE = "mock"

    def tearDown(self):
        retriever.RAG_MODE = self._mode

    def test_downgrade_when_milvus_fails(self):
        """RAG_MODE=milvus 但检索抛错时,自动降级回 mock,不中断主流程(PRD §23.5)。"""
        retriever.RAG_MODE = "milvus"
        with patch("app.rag.retriever._retrieve_milvus", side_effect=RuntimeError("milvus down")):
            result = retrieve_policy("杭州")
        self.assertTrue(result, "降级后应仍返回制度证据")
        self.assertEqual(result[0].chunk_id, "TRAVEL-V1-4.2-B")

    def test_mock_city_hit(self):
        """一线城市命中 600 元条款。"""
        result = retrieve_policy("北京")
        self.assertEqual(result[0].chunk_id, "TRAVEL-V1-4.2-A")

    def test_mock_other_city_fallback(self):
        """非一线城市命中兜底 450 元条款。"""
        result = retrieve_policy("杭州")
        self.assertEqual(result[0].chunk_id, "TRAVEL-V1-4.2-B")

    def test_mock_no_city_returns_empty(self):
        self.assertEqual(retrieve_policy(None), [])

    def test_contract_unchanged(self):
        """输出契约:PolicyEvidence(chunk_id/section/content/score),与协作者基线一致。"""
        result = retrieve_policy("北京")
        for item in result:
            self.assertTrue(hasattr(item, "chunk_id"))
            self.assertTrue(hasattr(item, "section"))
            self.assertTrue(hasattr(item, "content"))
            self.assertTrue(hasattr(item, "score"))


if __name__ == "__main__":
    unittest.main()
