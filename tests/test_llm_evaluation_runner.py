import unittest

from app.evaluation.llm_runner import run_llm_case, run_llm_evaluation
from app.evaluation.runner import EvaluationCase


class StubGateway:
    provider = "stub"
    model = "stub-model"

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def complete_json(self, instruction: str, content: str):
        self.calls += 1
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        if isinstance(payload, Exception):
            raise payload
        return payload


class LlmEvaluationRunnerTests(unittest.TestCase):
    def test_llm_case_reports_matched_and_rejected_fields(self):
        case = EvaluationCase(
            case_id="CASE_STUB",
            name="stub",
            documents_dir="data/demo/normal",
            expected_status="PASS",
            expected_fields={"invoice_amount": "580.00", "invoice_date": "2026-08-01"},
        )
        gateway = StubGateway([
            {
                "fields": {
                    "invoice_amount": {
                        "value": "580元",
                        "confidence": 0.95,
                        "source_text": "发票金额：580.00",
                        "page_no": 1,
                    },
                    "invoice_date": {
                        "value": "2026/08/01",
                        "confidence": 0.95,
                        "source_text": "发票日期：2026-08-01",
                        "page_no": 1,
                    },
                    "applicant_name": {
                        "value": "李四",
                        "confidence": 0.95,
                        "source_text": "不存在的原文",
                        "page_no": 1,
                    },
                }
            }
        ])

        result = run_llm_case(case, gateway=gateway)

        self.assertEqual(result.matched_fields, ["invoice_amount", "invoice_date"])
        self.assertEqual(result.rejected_fields["applicant_name"], "source_text not found in documents")
        self.assertFalse(result.fallback_used)

    def test_llm_evaluation_counts_json_errors_and_fallback(self):
        class BrokenGateway(StubGateway):
            def complete_json(self, instruction: str, content: str):
                from app.services.model_gateway import ModelGatewayError

                raise ModelGatewayError("模型未返回JSON对象")

        report = run_llm_evaluation(gateway=BrokenGateway([]))

        self.assertEqual(report.metrics.total_cases, 5)
        self.assertEqual(report.metrics.fallback_cases, 5)
        self.assertGreaterEqual(report.metrics.json_format_errors, 5)
        self.assertEqual(report.metrics.passed_cases, 0)


if __name__ == "__main__":
    unittest.main()
