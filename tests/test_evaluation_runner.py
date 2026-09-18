import unittest
import tempfile
from pathlib import Path

from app.evaluation.runner import EvaluationCase, run_case, run_evaluation, write_report


class EvaluationRunnerTests(unittest.TestCase):
    def test_default_cases_pass(self):
        report = run_evaluation()

        self.assertEqual(report.metrics.total_cases, 5)
        self.assertEqual(report.metrics.passed_cases, 5)
        self.assertEqual(report.metrics.failed_cases, 0)
        self.assertEqual(report.metrics.pass_rate, 1.0)
        self.assertEqual(report.metrics.status_accuracy, 1.0)
        self.assertEqual(report.metrics.risk_accuracy, 1.0)
        self.assertEqual(report.metrics.field_accuracy, 1.0)
        self.assertEqual(report.metrics.policy_ref_accuracy, 1.0)

    def test_case_result_reports_field_mismatch(self):
        case = EvaluationCase(
            case_id="BAD_FIELD_EXPECTATION",
            documents_dir="data/demo/normal",
            expected_status="PASS",
            expected_fields={"invoice_amount": "999.00"},
            expected_policy_refs=["TRAVEL-V1-4.2-A"],
        )

        result = run_case(case)

        self.assertFalse(result.passed)
        self.assertEqual(result.field_mismatches[0].field, "invoice_amount")
        self.assertEqual(result.field_mismatches[0].actual, "580.00")

    def test_original_compact_case_format_still_loads(self):
        case = EvaluationCase(
            case="normal",
            expected_status="PASS",
            expected_risks=[],
        )

        result = run_case(case, repo_root=Path(__file__).resolve().parents[1])

        self.assertTrue(result.passed)
        self.assertEqual(result.case_id, "normal")

    def test_report_can_be_written_to_file(self):
        report = run_evaluation()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "reports" / "evaluation.md"

            write_report(report, output, "markdown")

            self.assertTrue(output.exists())
            self.assertIn("Audit Agent Evaluation Report", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
