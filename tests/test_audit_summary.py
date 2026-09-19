import unittest
from pathlib import Path

from app.agent.graph import run_audit
from app.services.audit_summary import build_audit_summary
from app.services.invoice_registry import isolate_registry


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class AuditSummaryTests(unittest.TestCase):
    def setUp(self):
        # 发票查重登记表隔离,避免用例之间互相污染(FR-204)
        isolate_registry()

    def load_report(self, case: str):
        files = [(path.name, path.read_bytes()) for path in sorted((BASE / case).iterdir()) if path.is_file()]
        return run_audit(files)

    def test_pass_summary_has_clear_next_action(self):
        summary = build_audit_summary(self.load_report("normal"))

        self.assertEqual(summary.status, "PASS")
        self.assertEqual(len(summary.risks), 0)
        self.assertIn("未发现风险", summary.conclusion)
        self.assertTrue(summary.key_fields)
        self.assertTrue(any(check.status == "PASS" for check in summary.checks))

    def test_risk_summary_explains_policy_and_next_action(self):
        summary = build_audit_summary(self.load_report("over_limit"))

        self.assertEqual(summary.status, "REVIEW_REQUIRED")
        risk = next(item for item in summary.risks if item.risk_type == "HOTEL_LIMIT")
        self.assertIn("TRAVEL-V1-4.2-A", risk.policy_refs)
        self.assertIn("入住晚数", risk.next_action)
        subject_risk = next(item for item in summary.risks if item.risk_type == "APPLICANT_MATCH")
        self.assertIn("同一主体", subject_risk.next_action)
        self.assertTrue(any(item.chunk_id == "TRAVEL-V1-4.2-A" for item in summary.policy_evidence))


if __name__ == "__main__":
    unittest.main()
