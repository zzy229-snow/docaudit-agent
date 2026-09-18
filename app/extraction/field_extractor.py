import re
from pydantic import BaseModel

from app.models.document import Document
from app.models.field import ExtractedField
from app.extraction.llm_field_extractor import LlmExtractionDiagnostics, extract_fields_with_llm_diagnostics


LABELS = {
    "invoice_amount": r"(?:发票金额|价税合计|住宿金额)\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)",
    "payment_amount": r"(?:付款金额|支付金额|实付金额)\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)",
    "invoice_date": r"(?:发票日期|开票日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
    "travel_start_date": r"(?:出差开始|出差起始日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
    "travel_end_date": r"(?:出差结束|出差结束日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
    "travel_city": r"(?:出差城市|目的地)\s*[:：]?\s*([\u4e00-\u9fff]{2,8})",
    "invoice_number": r"(?:发票号码|发票号)\s*[:：]?\s*([A-Za-z0-9-]{5,32})",
    "applicant_name": r"(?:申请人|报销人)\s*[:：]?\s*([\u4e00-\u9fff]{2,5})",
}


class FieldExtractionOutcome(BaseModel):
    fields: dict[str, ExtractedField]
    llm_diagnostics: LlmExtractionDiagnostics | None = None


def extract_fields(documents: list[Document]) -> dict[str, ExtractedField]:
    return extract_fields_with_diagnostics(documents).fields


def extract_fields_with_diagnostics(documents: list[Document]) -> FieldExtractionOutcome:
    """Deterministic baseline; values are only taken from explicit labels."""
    found: dict[str, ExtractedField] = {}
    for doc in documents:
        for page in doc.pages:
            for name, pattern in LABELS.items():
                match = re.search(pattern, page.text)
                if match and name not in found:
                    value = match.group(1).strip()
                    if name.endswith("_amount"):
                        value = value  # Decimal conversion happens inside Tools.
                    found[name] = ExtractedField(name=name, value=value, confidence=1.0,
                        document_id=doc.document_id, page_no=page.number, source_text=match.group(0))
    llm_outcome = extract_fields_with_llm_diagnostics(documents)
    for name, field in llm_outcome.fields.items():
        found.setdefault(name, field)
    return FieldExtractionOutcome(fields=found, llm_diagnostics=llm_outcome.diagnostics)
