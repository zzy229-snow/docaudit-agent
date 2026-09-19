import unittest
from pathlib import Path
from decimal import Decimal
from app.agent.graph import run_audit
from app.services.invoice_registry import isolate_registry
from app.tools.amount_tool import compare_amounts
from app.tools.date_tool import compare_date_range


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class AuditTests(unittest.TestCase):
    def setUp(self):
        # 发票查重登记表隔离,避免用例之间互相污染(FR-204)
        isolate_registry()

    def load(self, case):
        files = [(p.name, p.read_bytes()) for p in sorted((BASE / case).iterdir()) if p.is_file()]
        return run_audit(files)

    def test_normal_case_passes(self):
        result = self.load("normal")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(result.risks), 0)
        self.assertEqual(result.policy_evidence[0].chunk_id, "TRAVEL-V1-4.2-A")

    def test_over_limit_has_evidence(self):
        result = self.load("over_limit")
        self.assertEqual(result.status, "REVIEW_REQUIRED")
        risk = next(r for r in result.risks if r.risk_type == "HOTEL_LIMIT")
        self.assertIn("TRAVEL-V1-4.2-A", risk.policy_refs)
        self.assertIn("80.00", risk.reason)

    def test_amount_difference_uses_decimal(self):
        self.assertIn("0.10", compare_amounts("0.30", "0.20").detail)

    def test_invalid_dates_need_review(self):
        self.assertIsNone(compare_date_range("2026-02-31", "2026-02-01", "2026-02-28").passed)

    def test_missing_payment_cannot_pass(self):
        files = [(p.name, p.read_bytes()) for p in (BASE / "normal").iterdir() if p.name != "payment.txt"]
        result = run_audit(files)
        self.assertEqual(result.status, "REVIEW_REQUIRED")
        self.assertTrue(any(r.risk_type == "REQUIRED_DOCUMENTS" for r in result.risks))
        self.assertTrue(any(r.risk_type == "AMOUNT_MATCH" for r in result.risks))


if __name__ == "__main__":
    unittest.main()
