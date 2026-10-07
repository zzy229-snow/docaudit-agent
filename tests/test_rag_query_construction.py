"""检索查询文本构造的回归测试。

背景(2026-10-07 实测踩坑):审核流程调用 `retrieve_policy(city="上海", query=None)`,
而 milvus 分支原来把 `query or city` 当检索文本 —— 于是送进向量库和交叉编码器的查询就是
光秃秃的"上海"两个字:

- 向量侧:把 TRAVEL-V1-4.2-B("其他城市 450 元/晚",distance 1.0809)排在
  TRAVEL-V1-4.2-A(一线城市 600,distance 1.0494)前面;
- 交叉编码器侧:0.0081 vs 0.0076,差距 0.0005 —— 纯噪声,却同样把 B 排前。

结果:上海出差的住宿上限被算成 450 元(正确是 600),风险金额也从 80 元变成 230 元。
local 分支一直用 `_query_from_city` 展开("上海 住宿标准 差旅 报销 酒店"),
所以这个坑只在 milvus 分支上 —— 本用例把它钉住。
"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.rag import retriever  # noqa: E402


class QueryConstructionTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = retriever.RAG_MODE
        self._old_milvus = retriever._retrieve_milvus
        self.captured = {}

        def _capture(query, expense_type=None, department=None, city=None):
            self.captured.update(query=query, expense_type=expense_type, city=city)
            return []

        retriever.RAG_MODE = "milvus"
        retriever._retrieve_milvus = _capture

    def tearDown(self):
        retriever.RAG_MODE = self._old_mode
        retriever._retrieve_milvus = self._old_milvus

    def test_city_only_request_expands_to_retrievable_query(self):
        """只给城市(审核流程的真实调用方式)时,查询文本必须展开,不能只传城市名。"""
        retriever.retrieve_policy("上海")
        query = self.captured["query"]
        self.assertIn("上海", query)
        self.assertIn("住宿标准", query)
        self.assertGreater(len(query), len("上海"), "查询文本不能就是城市名本身")

    def test_city_is_passed_to_rerank_for_entity_matching(self):
        retriever.retrieve_policy("杭州")
        self.assertEqual("杭州", self.captured["city"])

    def test_explicit_query_wins_over_expansion(self):
        retriever.retrieve_policy("上海", query="上海住宿标准")
        self.assertEqual("上海住宿标准", self.captured["query"])

    def test_no_city_no_query_stays_empty(self):
        """没有城市也没有 query 时不要凭空造查询(会引入无关检索)。"""
        retriever.retrieve_policy(None, files=[("invoice.txt", b"")])
        self.assertEqual("", self.captured["query"])


if __name__ == "__main__":
    unittest.main()
