import unittest
from unittest.mock import patch

from app.evaluation.rag_runner import run_rag_evaluation
from app.rag.local_store import retrieve_local
from app.rag.retriever import retrieve_policy


class RagLocalTests(unittest.TestCase):
    def test_local_store_retrieves_policy_chunks(self):
        evidence = retrieve_local("杭州 住宿标准 差旅报销 酒店", limit=2)

        self.assertTrue(evidence)
        self.assertTrue(any(item.chunk_id == "TRAVEL-V1-4.2-B" for item in evidence))

    def test_retriever_local_mode_uses_real_policy_chunks(self):
        with patch("app.rag.retriever.RAG_MODE", "local"):
            evidence = retrieve_policy("杭州")

        self.assertTrue(evidence)
        self.assertTrue(any("其他城市" in item.content or "杭州" in item.content for item in evidence))

    def test_rag_evaluation_reports_policy_hits(self):
        with patch("app.rag.retriever.RAG_MODE", "local"):
            report = run_rag_evaluation()

        self.assertGreater(report.metrics.evaluated_cases, 0)
        self.assertGreaterEqual(report.metrics.hit_rate, 0.8)


if __name__ == "__main__":
    unittest.main()
