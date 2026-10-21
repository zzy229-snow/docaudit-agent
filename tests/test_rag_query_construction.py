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


class ReferenceQueryTests(unittest.TestCase):
    """编号引用查询:先按元数据精确命中,不赌向量相似度。

    实测依据:查询"4.2 条怎么规定的"时,稠密向量的候选池里**根本没有** TRAVEL-V1-4.2-*,
    重排也无从挽救;而稀疏通道能精确命中 —— 说明这是确定性匹配问题,不是相似度问题。
    """

    def test_extracts_full_clause_id(self):
        self.assertEqual(["TRAVEL-V1-4.2"], retriever._reference_tokens("TRAVEL-V1-4.2 这条制度说了什么"))
        self.assertEqual(["EXP-V1-4.5"], retriever._reference_tokens("EXP-V1-4.5 是什么条款"))

    def test_extracts_section_number(self):
        self.assertEqual(["4.2"], retriever._reference_tokens("4.2 条怎么规定的"))
        self.assertEqual(["4.2.1"], retriever._reference_tokens("4.2.1 小节怎么说"))

    def test_dates_are_not_mistaken_for_clause_numbers(self):
        """日期不能被当成编号(否则每次问"2026.8.1 的报销"都会触发精确匹配)。"""
        for q in ("2026.8.1 出差住宿", "2019.02.19 的发票", "住宿费能报多少钱", "发票号码怎么填"):
            self.assertEqual([], retriever._reference_tokens(q), q)

    def test_exact_hits_match_by_prefix(self):
        class _Store:
            def all_chunk_ids(self):
                return ["EXP-V1-4.3-TABLE-R1", "TRAVEL-V1-4.2-A", "TRAVEL-V1-4.2-B"]

            def fetch_by_ids(self, ids):
                from app.models.audit import PolicyEvidence
                return [PolicyEvidence(chunk_id=i, section="4.2", content=f"正文{i}", score=0.0) for i in ids]

        hits = retriever._exact_reference_hits(_Store(), ["4.2"], limit=2)
        self.assertEqual(["TRAVEL-V1-4.2-A", "TRAVEL-V1-4.2-B"], [h.chunk_id for h in hits])

    def test_no_tokens_means_no_exact_path(self):
        class _Store:
            def all_chunk_ids(self):       # 不该被调用
                raise AssertionError("没有编号引用时不应查询编号集合")

        self.assertEqual([], retriever._exact_reference_hits(_Store(), []))

    def test_reference_query_short_circuits_without_vector_search(self):
        """两条精确命中就够用了:不该再去编码/检索/重排(省 ~1.7 秒且避免被相似度带偏)。"""
        from app.models.audit import PolicyEvidence
        from app.rag import vector_store

        class _Store:
            def all_chunk_ids(self):
                return ["TRAVEL-V1-4.2-A", "TRAVEL-V1-4.2-B", "EXP-V1-4.4"]

            def fetch_by_ids(self, ids):
                return [PolicyEvidence(chunk_id=i, section="4.2", content="住宿费标准", score=0.0) for i in ids]

            def hybrid_search(self, **_kwargs):
                raise AssertionError("已有两条精确命中,不该再做向量检索")

        saved = vector_store.get_store
        vector_store.get_store = lambda *a, **k: _Store()
        try:
            result = retriever._retrieve_milvus("4.2 条怎么规定的")
        finally:
            vector_store.get_store = saved

        self.assertEqual(["TRAVEL-V1-4.2-A", "TRAVEL-V1-4.2-B"], [h.chunk_id for h in result])
        self.assertIn("编号精确命中", retriever.LAST_RERANK_LABEL)


if __name__ == "__main__":
    unittest.main()
