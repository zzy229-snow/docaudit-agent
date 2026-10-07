"""重排测试(app/rag/rerank.py)。

核心回归:**"上海住宿标准"必须把含"上海"的条款排到"其他城市"兜底条款前面**。
这是纯向量检索的真实缺陷(实测 distance 0.6034 vs 0.5849,错序),调融合权重修不掉,
所以用实体重排兜住,bge-reranker 作为可选增强。
"""
import os
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.models.audit import PolicyEvidence  # noqa: E402
from app.rag import rerank  # noqa: E402


def ev(chunk_id: str, content: str, score: float = 0.5) -> PolicyEvidence:
    return PolicyEvidence(chunk_id=chunk_id, section="4.2", content=content, score=score)


TIER_A = ev("TRAVEL-V1-4.2-A", "住宿费标准:北京、上海、广州、深圳 600 元/晚", 0.6034)
OTHER_B = ev("TRAVEL-V1-4.2-B", "住宿费标准:其他城市 450 元/晚", 0.5849)
TABLE = ev("EXP-V1-4.3-TABLE-R1", "住宿费 超标部分自理", 0.772)


class EntityRerankTests(unittest.TestCase):
    def test_city_mention_beats_fallback_clause(self):
        """核心回归:上海查询 → 含"上海"的 A 要压过"其他城市"的 B。"""
        result = rerank.entity_rank([OTHER_B, TIER_A], "上海住宿标准", "上海")
        self.assertEqual(["TRAVEL-V1-4.2-A", "TRAVEL-V1-4.2-B"], [i.chunk_id for i in result])

    def test_non_first_tier_city_prefers_other_city_clause(self):
        """杭州不是枚举城市 → "其他城市 450"才是答案。"""
        result = rerank.entity_rank([TIER_A, OTHER_B], "杭州出差酒店费用上限", "杭州")
        self.assertEqual(["TRAVEL-V1-4.2-B", "TRAVEL-V1-4.2-A"], [i.chunk_id for i in result])

    def test_city_taken_from_task_when_query_has_none(self):
        """只给城市(自由文本为空)时也要能重排。"""
        result = rerank.entity_rank([OTHER_B, TIER_A], "", "北京")
        self.assertEqual("TRAVEL-V1-4.2-A", result[0].chunk_id)

    def test_no_entity_in_query_keeps_original_order(self):
        """查询里没有实体时不得改动顺序(排序必须可复现,不能引入随机性)。"""
        original = [OTHER_B, TIER_A, TABLE]
        result = rerank.entity_rank(original, "住宿费能报多少钱", None)
        self.assertEqual([i.chunk_id for i in original], [i.chunk_id for i in result])

    def test_order_is_stable_within_same_rank(self):
        a = ev("X-1", "其他城市 450 元")
        b = ev("X-2", "其他城市 450 元")
        result = rerank.entity_rank([a, b], "杭州住宿标准", "杭州")
        self.assertEqual(["X-1", "X-2"], [i.chunk_id for i in result])


class _FixedReranker:
    """按给定分数打分,用来构造"与实体重排相反"的排序(证明交叉编码器真的生效)。"""

    def __init__(self, scores):
        self._scores = list(scores)

    def compute_score(self, pairs, normalize=True):
        return self._scores[: len(pairs)]


class _BoomReranker:
    def __init__(self, *args, **kwargs):
        pass

    def compute_score(self, pairs, normalize=True):
        raise RuntimeError("模型炸了")


class RerankPipelineTests(unittest.TestCase):
    def setUp(self):
        rerank.reset_reranker()
        self._saved = {k: os.environ.get(k) for k in
                       (rerank.RERANKER_DIR_ENV, rerank.RERANK_ENV, rerank.FP16_ENV)}

    def tearDown(self):
        rerank.reset_reranker()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_without_weights_only_entity_layer_runs_and_says_so(self):
        os.environ.pop(rerank.RERANKER_DIR_ENV, None)
        result, label = rerank.rerank("上海住宿标准", [OTHER_B, TIER_A], city="上海", top_k=2)
        self.assertEqual("TRAVEL-V1-4.2-A", result[0].chunk_id)
        self.assertTrue(label.startswith("实体"), label)
        self.assertIn("不可用", label)  # 说明为什么没用交叉编码器

    def test_cross_encoder_reorders_when_available(self):
        """注入假交叉编码器(必须 patch get_reranker:直接塞 _RERANKER 会被它会话覆盖)。"""
        # 实体重排后顺序是 [TIER_A, OTHER_B, TABLE];给最后一名最高分,
        # 若交叉编码器真的生效,最终顺序应与之相反 —— 否则说明这一层没生效。
        saved = rerank.get_reranker
        rerank.get_reranker = lambda: _FixedReranker([0.1, 0.2, 0.95])
        rerank._RERANKER_PATH = str(BASE / "bge-reranker-v2-m3")
        try:
            result, label = rerank.rerank("上海住宿标准", [OTHER_B, TIER_A, TABLE], city="上海", top_k=2)
        finally:
            rerank.get_reranker = saved
            rerank.reset_reranker()

        self.assertEqual(["EXP-V1-4.3-TABLE-R1", "TRAVEL-V1-4.2-B"], [i.chunk_id for i in result])
        self.assertIn("bge-reranker-v2-m3", label)

    def test_cross_encoder_failure_falls_back_to_entity_order(self):
        saved = rerank.get_reranker
        rerank.get_reranker = lambda: _BoomReranker()
        try:
            result, label = rerank.rerank("上海住宿标准", [OTHER_B, TIER_A], city="上海", top_k=2)
        finally:
            rerank.get_reranker = saved
            rerank.reset_reranker()

        self.assertEqual("TRAVEL-V1-4.2-A", result[0].chunk_id, "打分失败必须退回实体重排,不能乱序")
        self.assertTrue(label.startswith("实体"), label)

    def test_explicit_off_switch(self):
        os.environ[rerank.RERANK_ENV] = "0"
        self.assertFalse(rerank.rerank_enabled())
        self.assertIsNone(rerank.get_reranker())
        self.assertIn(rerank.RERANK_ENV, rerank.reranker_status())

    def test_missing_dir_is_reported_not_crashed(self):
        os.environ[rerank.RERANKER_DIR_ENV] = str(BASE / "no-such-reranker")
        self.assertIsNone(rerank.get_reranker())
        self.assertIn("不存在", rerank.reranker_status())

    def test_empty_candidates(self):
        result, label = rerank.rerank("北京住宿标准", [], city="北京")
        self.assertEqual([], result)
        self.assertEqual("无候选", label)

    def test_top_k_truncates_after_rerank(self):
        result, _ = rerank.rerank("上海住宿标准", [OTHER_B, TIER_A, TABLE], city="上海", top_k=2)
        self.assertEqual(2, len(result))
        self.assertEqual("TRAVEL-V1-4.2-A", result[0].chunk_id)


if __name__ == "__main__":
    unittest.main()
