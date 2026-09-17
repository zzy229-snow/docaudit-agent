import unittest

from app.extraction.field_extractor import extract_fields
from app.extraction.llm_field_extractor import extract_fields_with_llm
from app.models.document import Document, Page


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
            }
        }


class LlmFieldExtractorTests(unittest.TestCase):
    def test_llm_extractor_validates_candidates(self):
        doc = Document(document_id="doc1", file_name="sample.txt", document_type="text", pages=[
            Page(number=1, text="报销人：李四")
        ])

        fields = extract_fields_with_llm([doc], gateway=FakeGateway())

        self.assertEqual(fields["applicant_name"].value, "李四")
        self.assertEqual(fields["applicant_name"].confidence, 0.91)

    def test_rule_extractor_keeps_deterministic_values_before_llm_fill(self):
        doc = Document(document_id="doc1", file_name="sample.txt", document_type="text", pages=[
            Page(number=1, text="发票金额：580.00\n报销人：李四")
        ])

        fields = extract_fields([doc])

        self.assertEqual(fields["invoice_amount"].value, "580.00")


if __name__ == "__main__":
    unittest.main()
