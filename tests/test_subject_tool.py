"""主体对齐检查(任务③):申请人 ↔ 发票购买方 ↔ 付款收款方。"""

import unittest
from pathlib import Path

from app.agent.graph import run_audit
from app.tools.subject_tool import compare_subject

BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class SubjectMatchTests(unittest.TestCase):

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

    def test_payee_mismatch_fails(self):
        result = compare_subject("张三", "张三", "XX科技有限公司")
        self.assertFalse(result.passed)
        self.assertIn("收款方", result.detail)

    def test_no_subject_info_skips(self):
        result = compare_subject("张三", None, None)
        self.assertIsNone(result.passed)
        self.assertIn("未包含", result.detail)

    def test_partial_subject_info_still_checks(self):
        """付款单未提供收款方时,发票购买方与申请人一致仍可通过。"""
        result = compare_subject("张三", "张三", None)
        self.assertTrue(result.passed)
        self.assertEqual(result.evidence_refs, ["applicant_name", "invoice_buyer"])

    def test_missing_applicant_skips(self):
        result = compare_subject(None, "张三", "张三")
        self.assertIsNone(result.passed)

    def test_demo_normal_passes_with_subject(self):
        """normal 组:申请人/购买方/收款方一致,无风险。"""
        files = [(p.name, p.read_bytes()) for p in sorted((BASE / "normal").iterdir()) if p.is_file()]
        result = run_audit(files)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(result.risks), 0)
        match_check = next(c for c in result.checks if c.name == "applicant_match")
        self.assertTrue(match_check.passed)

    def test_demo_over_limit_raises_subject_risk(self):
        """over_limit 组:购买方李四 ≠ 申请人张三,触发主体不一致风险。"""
        files = [(p.name, p.read_bytes()) for p in sorted((BASE / "over_limit").iterdir()) if p.is_file()]
        result = run_audit(files)
        self.assertEqual(result.status, "REVIEW_REQUIRED")
        self.assertTrue(any(r.risk_type == "HOTEL_LIMIT" for r in result.risks))
        self.assertTrue(any(r.risk_type == "APPLICANT_MATCH" for r in result.risks))


if __name__ == "__main__":
    unittest.main()
