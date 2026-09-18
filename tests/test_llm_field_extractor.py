import unittest

from app.extraction.field_extractor import extract_fields
from app.extraction.llm_field_extractor import extract_fields_with_llm, extract_fields_with_llm_diagnostics
from app.models.document import Document, Page
from app.services.model_gateway import ModelGatewayError


class FakeGateway:
    provider = "fake"

    def complete_json(self, instruction: str, content: str):
        return {
            "fields": {
                "applicant_name": {
                    "value": "李四",
                    "confidence": 0.91,
                    "source_text": "报销人：李四",
                    "page_no": 1,
                },
                "invoice_amount": {
                    "value": "999.00",
                    "confidence": 0.9,
                    "source_text": "发票金额：999.00",
                    "page_no": 1,
                },
                "invoice_buyer": {
                    "value": "测试公司",
                    "confidence": 0.9,
                    "source_text": "购买方：测试公司",
                    "page_no": 1,
                },
            }
        }


class RetryGateway:
    provider = "fake"

    def __init__(self):
        self.calls = 0

    def complete_json(self, instruction: str, content: str):
        self.calls += 1
        if self.calls == 1:
            raise ModelGatewayError("bad json")
        return {
            "fields": {
                "invoice_amount": {
                    "value": "¥580",
                    "confidence": 0.9,
                    "source_text": "发票金额：580.00",
                    "page_no": 1,
                },
                "invoice_date": {
                    "value": "2026年08月01日",
                    "confidence": 0.9,
                    "source_text": "发票日期：2026-08-01",
                    "page_no": 1,
                },
            }
        }


class UnsafeGateway:
    provider = "fake"

    def complete_json(self, instruction: str, content: str):
        return {
            "fields": {
                "travel_city": {
                    "value": "北京",
                    "confidence": 0.99,
                    "source_text": "模型猜测：北京",
                    "page_no": 1,
                },
                "applicant_name": {
                    "value": "王五",
                    "confidence": 0.4,
                    "source_text": "报销人：王五",
                    "page_no": 1,
                },
                "unknown_field": {
                    "value": "x",
                    "confidence": 1,
                    "source_text": "x",
                    "page_no": 1,
                },
            }
        }


class LlmFieldExtractorTests(unittest.TestCase):
    def test_llm_extractor_validates_candidates(self):
        doc = Document(document_id="doc1", file_name="sample.txt", document_type="text", pages=[
            Page(number=1, text="报销人：李四\n购买方：测试公司")
        ])

        fields = extract_fields_with_llm([doc], gateway=FakeGateway())

        self.assertEqual(fields["applicant_name"].value, "李四")
        self.assertEqual(fields["applicant_name"].confidence, 0.91)
        self.assertEqual(fields["invoice_buyer"].value, "测试公司")

    def test_llm_extractor_retries_and_normalizes_values(self):
        doc = Document(document_id="doc1", file_name="sample.txt", document_type="text", pages=[
            Page(number=1, text="发票金额：580.00\n发票日期：2026-08-01")
        ])
        gateway = RetryGateway()

        outcome = extract_fields_with_llm_diagnostics([doc], gateway=gateway)

        self.assertEqual(gateway.calls, 2)
        self.assertEqual(outcome.fields["invoice_amount"].value, "580.00")
        self.assertEqual(outcome.fields["invoice_date"].value, "2026-08-01")
        self.assertEqual(outcome.diagnostics.attempts, 2)
        self.assertIn("attempt_1", outcome.diagnostics.errors[0])

    def test_llm_extractor_rejects_unsupported_or_unevidenced_candidates(self):
        doc = Document(document_id="doc1", file_name="sample.txt", document_type="text", pages=[
            Page(number=1, text="报销人：王五")
        ])

        outcome = extract_fields_with_llm_diagnostics([doc], gateway=UnsafeGateway())

        self.assertEqual(outcome.fields, {})
        self.assertEqual(outcome.diagnostics.rejected_fields["travel_city"], "source_text not found in documents")
        self.assertEqual(outcome.diagnostics.rejected_fields["applicant_name"], "low confidence 0.4")
        self.assertEqual(outcome.diagnostics.rejected_fields["unknown_field"], "unknown field")
        self.assertTrue(outcome.diagnostics.fallback_used)

    def test_rule_extractor_keeps_deterministic_values_before_llm_fill(self):
        doc = Document(document_id="doc1", file_name="sample.txt", document_type="text", pages=[
            Page(number=1, text="发票金额：580.00\n报销人：李四")
        ])

        fields = extract_fields([doc])

        self.assertEqual(fields["invoice_amount"].value, "580.00")


if __name__ == "__main__":
    unittest.main()
