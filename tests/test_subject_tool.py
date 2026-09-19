import unittest
from pathlib import Path

from app.agent.graph import run_audit
from app.services.invoice_registry import isolate_registry
from app.tools.subject_tool import compare_subject


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class SubjectMatchTests(unittest.TestCase):
    def setUp(self):
        # 发票查重登记表隔离,避免用例之间互相污染(FR-204)
        isolate_registry()

    def load_files(self, case: str):
        return [(p.name, p.read_bytes()) for p in sorted((BASE / case).iterdir()) if p.is_file()]

    def test_all_parties_consistent(self):
        result = compare_subject("张三", "张三", "张三")

        self.assertTrue(result.passed)
        self.assertIn("一致", result.detail)
        self.assertEqual(result.evidence_refs, ["applicant_name", "invoice_buyer", "payment_party"])

    def test_invoice_buyer_mismatch_fails(self):
        result = compare_subject("张三", "李四", "张三")

        self.assertFalse(result.passed)
        self.assertIn("李四", result.detail)
        self.assertIn("invoice_buyer", result.evidence_refs)

    def test_missing_subject_info_skips_without_risk(self):
        result = compare_subject("张三", None, None)

        self.assertTrue(result.passed)
        self.assertIn("检查跳过", result.detail)

    def test_missing_applicant_needs_review(self):
        result = compare_subject(None, "张三", "张三")

        self.assertIsNone(result.passed)

    def test_demo_normal_passes_with_subjects(self):
        result = run_audit(self.load_files("normal"))

        self.assertEqual(result.status, "PASS")
        self.assertFalse(result.risks)
        match_check = next(check for check in result.checks if check.name == "applicant_match")
        self.assertTrue(match_check.passed)

    def test_demo_over_limit_has_subject_risk(self):
        result = run_audit(self.load_files("over_limit"))

        self.assertEqual(result.status, "REVIEW_REQUIRED")
        self.assertTrue(any(risk.risk_type == "HOTEL_LIMIT" for risk in result.risks))
        self.assertTrue(any(risk.risk_type == "APPLICANT_MATCH" for risk in result.risks))


if __name__ == "__main__":
    unittest.main()
