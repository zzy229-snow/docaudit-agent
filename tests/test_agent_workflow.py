import unittest
from pathlib import Path

from app.agent.graph import run_audit
from app.services.invoice_registry import isolate_registry


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        # 发票查重登记表隔离,避免用例之间互相污染(FR-204)
        isolate_registry()

    def load_files(self, case: str):
        return [(p.name, p.read_bytes()) for p in sorted((BASE / case).iterdir()) if p.is_file()]

    def test_report_trace_contains_agent_runtime_summary(self):
        result = run_audit(self.load_files("normal"))

        self.assertTrue(any(item.startswith("task_id=audit-") for item in result.trace))
        self.assertIn("steps=5/10", result.trace)
        self.assertIn("tool_calls=7", result.trace)
        self.assertIn("human_review_items=0", result.trace)
        self.assertTrue(any(item.startswith("parse_documents: 解析3份材料") for item in result.trace))
        self.assertTrue(any(item.startswith("llm_extraction:") for item in result.trace))

    def test_review_case_counts_human_review_items(self):
        result = run_audit(self.load_files("over_limit"))

        self.assertEqual(result.status, "REVIEW_REQUIRED")
        self.assertIn("human_review_items=2", result.trace)

    def test_max_steps_stops_before_unbounded_execution(self):
        with self.assertRaisesRegex(RuntimeError, "max_steps=2"):
            run_audit(self.load_files("normal"), max_steps=2)


if __name__ == "__main__":
    unittest.main()
