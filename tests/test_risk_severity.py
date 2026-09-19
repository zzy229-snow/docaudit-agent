"""风险四级分级与复核路由测试(PRD §5.3 FR-205 / 附录A)。"""
import unittest
from pathlib import Path

from app.agent.graph import run_audit
from app.agent.risk_rules import (
    DEFAULT_LEVEL,
    LEVEL_ACTIONS,
    RISK_LEVELS,
    SEVERITY_RULES,
    build_risk,
    has_blocking_risk,
    level_action,
    overall_status,
    requires_review,
    risk_summary,
    severity_of,
)
from app.services.invoice_registry import isolate_registry


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class SeverityRuleTests(unittest.TestCase):
    def test_all_four_levels_are_defined(self):
        self.assertEqual(RISK_LEVELS, ("HIGH", "MEDIUM", "LOW", "INFO"))
        self.assertEqual(set(LEVEL_ACTIONS), set(RISK_LEVELS))

    def test_amount_mismatch_and_duplicate_invoice_are_high(self):
        """附录A:HIGH 包含重复发票与重大金额不一致。"""
        self.assertEqual(severity_of("AMOUNT_MATCH"), "HIGH")
        self.assertEqual(severity_of("DUPLICATE_INVOICE"), "HIGH")

    def test_policy_and_date_issues_are_medium(self):
        self.assertEqual(severity_of("HOTEL_LIMIT"), "MEDIUM")
        self.assertEqual(severity_of("DATE_RANGE"), "MEDIUM")
        self.assertEqual(severity_of("RULE_MISSING"), "MEDIUM")

    def test_unknown_risk_type_falls_back_to_medium(self):
        self.assertEqual(severity_of("SOMETHING_NEW"), DEFAULT_LEVEL)
        self.assertEqual(severity_of("something_new"), "MEDIUM")

    def test_review_required_only_for_high_and_medium(self):
        self.assertTrue(requires_review("HIGH"))
        self.assertTrue(requires_review("MEDIUM"))
        self.assertFalse(requires_review("LOW"))
        self.assertFalse(requires_review("INFO"))
        self.assertEqual(level_action("HIGH"), "必须人工复核")
        self.assertEqual(level_action("INFO"), "仅展示")

    def test_rules_use_declared_levels_only(self):
        for risk_type, level in SEVERITY_RULES.items():
            self.assertIn(level, RISK_LEVELS, risk_type)

    def test_overall_status_ignores_low_and_info(self):
        low = [build_risk("MERCHANT_NAME_FORMAT", "商户名称格式差异")]
        self.assertEqual(low[0].level, "LOW")
        self.assertFalse(has_blocking_risk(low))
        self.assertEqual(overall_status(low), "PASS")

        info = [build_risk("MATERIAL_QUALITY_INFO", "材料清晰度较低", level="INFO")]
        self.assertEqual(overall_status(info), "PASS")

        mixed = low + [build_risk("DUPLICATE_INVOICE", "重复发票")]
        self.assertTrue(has_blocking_risk(mixed))
        self.assertEqual(overall_status(mixed), "REVIEW_REQUIRED")
        self.assertEqual(risk_summary(mixed), {"HIGH": 1, "LOW": 1})

    def test_build_risk_normalizes_type_and_accepts_override(self):
        risk = build_risk("amount_match", "差额100元", evidence_refs=["invoice_amount"])
        self.assertEqual(risk.risk_type, "AMOUNT_MATCH")
        self.assertEqual(risk.level, "HIGH")
        overridden = build_risk("OCR_QUALITY_REVIEW", "可读", level="INFO")
        self.assertEqual(overridden.level, "INFO")

    def test_risk_summary_omits_empty_levels(self):
        risks = [build_risk("HOTEL_LIMIT", "超标"), build_risk("HOTEL_LIMIT", "超标")]
        self.assertEqual(risk_summary(risks), {"MEDIUM": 2})


class SeverityEndToEndTests(unittest.TestCase):
    def setUp(self):
        # 发票查重登记表隔离,避免用例之间互相污染(FR-204)
        isolate_registry()

    def load_files(self, case: str):
        return [(p.name, p.read_bytes()) for p in sorted((BASE / case).iterdir()) if p.is_file()]

    def test_over_limit_case_has_medium_risks_with_actions(self):
        report = run_audit(self.load_files("over_limit"))
        levels = {risk.risk_type: risk.level for risk in report.risks}
        self.assertEqual(levels["HOTEL_LIMIT"], "MEDIUM")
        self.assertEqual(report.risk_summary.get("MEDIUM"), len(report.risks))
        self.assertEqual(report.status, "REVIEW_REQUIRED")

    def test_amount_mismatch_case_routes_to_high_risk(self):
        """PRD AC-02:发票1280元、付款1180元 → 差额100元风险,等级 HIGH。"""
        report = run_audit(self.load_files("amount_mismatch"))
        levels = {risk.risk_type: risk.level for risk in report.risks}
        self.assertEqual(levels["AMOUNT_MATCH"], "HIGH")
        self.assertIn("100", next(r.reason for r in report.risks if r.risk_type == "AMOUNT_MATCH"))
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(report.risk_summary.get("HIGH"), 1)

    def test_date_conflict_case_is_medium_only(self):
        report = run_audit(self.load_files("date_out_of_range"))
        self.assertEqual({r.risk_type for r in report.risks}, {"DATE_RANGE"})
        self.assertEqual(report.risk_summary, {"MEDIUM": 1})

    def test_report_summary_counts_match_risk_list(self):
        report = run_audit(self.load_files("normal"))
        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.risk_summary, {})
        self.assertTrue(any(item.startswith("risk_levels=none") for item in report.trace))


if __name__ == "__main__":
    unittest.main()
