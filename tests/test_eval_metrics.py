"""评测指标与新增用例类型测试(PRD §13.2/§16/AC-05)。"""
import json
import tempfile
import unittest
from pathlib import Path

from app.evaluation.runner import EvaluationCase, load_cases, run_case, run_evaluation
from app.parsers.loader import parse_document
from app.services.invoice_registry import isolate_registry
from app.tools.amount_tool import compare_hotel_limit
from app.tools.registry import get_tool


REPO = Path(__file__).resolve().parents[1]
CASES_JSON = REPO / "data" / "evaluation" / "cases.json"


class RuleMissingTests(unittest.TestCase):
    def setUp(self):
        isolate_registry()

    def test_hotel_limit_without_evidence_reports_rule_missing(self):
        result = compare_hotel_limit("680.00", [])

        self.assertEqual(result.name, "rule_missing")
        self.assertIsNone(result.passed)
        self.assertIn("缺少规则依据", result.detail)

    def test_missing_amount_with_evidence_stays_hotel_limit(self):
        class Evidence:
            chunk_id = "TRAVEL-V1-4.2-A"
            section = "4.2"
            content = "北京、上海、广州、深圳的住宿标准为600元/晚。"
            score = 1.0

        result = compare_hotel_limit(None, [Evidence()])

        self.assertEqual(result.name, "hotel_limit")
        self.assertIsNone(result.passed)

    def test_rule_missing_case_does_not_claim_compliance(self):
        """AC-05:没有匹配制度时输出"缺少规则依据/待人工确认",不得判定合规。"""
        case = next(c for c in load_cases(CASES_JSON) if c.case_id == "CASE_RULE_MISSING")
        result = run_case(case, repo_root=REPO)

        self.assertTrue(result.passed, result.missing_risks or result.unexpected_risks)
        self.assertEqual(result.actual_status, "REVIEW_REQUIRED")
        self.assertEqual(result.actual_risks, ["RULE_MISSING"])

    def test_rule_missing_tool_call_is_recorded_as_skipped(self):
        case = next(c for c in load_cases(CASES_JSON) if c.case_id == "CASE_RULE_MISSING")
        result = run_case(case, repo_root=REPO)

        self.assertGreater(result.tool_skipped, 0)


class TextDocumentQualityRoutingTests(unittest.TestCase):
    def test_handwriting_keyword_marks_text_document(self):
        content = "申请人：张三\n签字：张三（手写）\n".encode("utf-8")

        document = parse_document("approval.txt", content)

        self.assertEqual(document.document_type, "text_handwriting")

    def test_plain_text_document_keeps_plain_type(self):
        content = "申请人：张三\n出差城市：上海\n".encode("utf-8")

        document = parse_document("approval.txt", content)

        self.assertEqual(document.document_type, "text")

    def test_handwriting_case_routes_to_review(self):
        case = next(c for c in load_cases(CASES_JSON) if c.case_id == "CASE_HANDWRITING")
        result = run_case(case, repo_root=REPO)

        self.assertTrue(result.passed, result.missing_risks or result.unexpected_risks)
        self.assertEqual(result.actual_risks, ["HANDWRITING_REVIEW"])
        self.assertEqual(result.actual_status, "REVIEW_REQUIRED")


class CaseMetadataTests(unittest.TestCase):
    def test_generated_cases_carry_category_and_expected_tools(self):
        cases = load_cases(CASES_JSON)
        categories = {case.category for case in cases}

        self.assertNotIn("uncategorized", categories)
        self.assertLessEqual(
            {"正常合规", "住宿超标", "材料缺失", "金额不一致", "日期冲突", "重复发票",
             "制度无依据", "指令注入", "OCR困难", "手写低置信度"},
            categories,
        )
        duplicate_case = next(case for case in cases if case.case_id == "CASE_DUP_02")
        self.assertEqual(duplicate_case.expected_tools, ["duplicate_invoice"])

    def test_case_defaults_keep_backward_compatibility(self):
        case = EvaluationCase(case_id="X", documents_dir="data/demo/normal", expected_status="PASS")

        self.assertEqual(case.category, "uncategorized")
        self.assertEqual(case.expected_tools, [])


class MetricsTests(unittest.TestCase):
    def _cases_file(self, payload: list[dict]) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="docaudit_metrics_cases_")) / "cases.json"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return tmp

    def test_risk_precision_recall_use_expected_vs_actual(self):
        """故意把期望风险写错:Recall < 1 且 Precision 捕获多报。"""
        cases_file = self._cases_file([
            {
                "case_id": "OK_CASE",
                "documents_dir": "data/eval_generated/CASE_T1_NORM_01_北京",
                "expected_status": "PASS",
                "expected_risks": [],
                "category": "正常合规",
                "expected_tools": ["hotel_limit"],
            },
            {
                "case_id": "WRONG_CASE",
                "documents_dir": "data/eval_generated/CASE_T1_LIMIT_01_北京",
                "expected_status": "REVIEW_REQUIRED",
                "expected_risks": ["DATE_RANGE"],
                "category": "住宿超标",
            },
        ])

        report = run_evaluation(cases_path=cases_file, repo_root=REPO)

        self.assertEqual(report.metrics.total_cases, 2)
        self.assertEqual(report.metrics.error_cases, 0)
        # 期望 DATE_RANGE(未出现)=FN;实际 HOTEL_LIMIT(未期望)=FP
        self.assertEqual(report.metrics.risk_recall, 0.0)
        self.assertEqual(report.metrics.risk_precision, 0.0)
        self.assertEqual(report.metrics.failed_cases, 1)
        self.assertEqual(report.metrics.categories, {"住宿超标": 1, "正常合规": 1})
        self.assertEqual(report.metrics.tool_selection_accuracy, 1.0)
        self.assertGreater(report.metrics.latency_p50_ms, 0.0)
        self.assertEqual(report.metrics.dead_loop_rate, 0.0)
        self.assertGreater(report.metrics.tool_calls_total, 0)
        self.assertLessEqual(report.metrics.tool_success_rate, 1.0)

    def test_missing_case_directory_is_counted_as_error_not_crash(self):
        cases_file = self._cases_file([
            {
                "case_id": "MISSING_DIR",
                "documents_dir": "data/eval_generated/DOES_NOT_EXIST",
                "expected_status": "PASS",
            },
        ])

        report = run_evaluation(cases_path=cases_file, repo_root=REPO)

        self.assertEqual(report.metrics.error_cases, 1)
        self.assertEqual(report.metrics.failed_cases, 1)
        self.assertEqual(report.results[0].actual_status, "ERROR")
        self.assertIn("FileNotFoundError", report.results[0].error or "")

    def test_expected_tool_mismatch_lowers_selection_accuracy(self):
        cases_file = self._cases_file([
            {
                "case_id": "TOOL_MISMATCH",
                "documents_dir": "data/eval_generated/CASE_T1_NORM_01_北京",
                "expected_status": "PASS",
                "expected_risks": [],
                "expected_tools": ["no_such_tool"],
            },
        ])

        report = run_evaluation(cases_path=cases_file, repo_root=REPO)

        self.assertFalse(report.results[0].tool_selection_passed)
        self.assertEqual(report.metrics.tool_selection_accuracy, 0.0)

    def test_default_evaluation_reports_extended_metrics(self):
        report = run_evaluation()
        metrics = report.metrics

        self.assertGreaterEqual(metrics.total_cases, 57)
        self.assertEqual(metrics.failed_cases, 0)
        self.assertEqual(metrics.risk_f1, 1.0)
        self.assertEqual(metrics.dead_loop_rate, 0.0)
        self.assertGreater(metrics.latency_p95_ms, 0.0)
        self.assertIn("指令注入", metrics.categories)
        self.assertIn("制度无依据", metrics.categories)


if __name__ == "__main__":
    unittest.main()
