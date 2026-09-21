import re
from pydantic import BaseModel

from app.models.document import Document
from app.models.field import ExtractedField
from app.extraction.llm_field_extractor import LlmExtractionDiagnostics, extract_fields_with_llm_diagnostics


LABELS = {
    # 价税合计的真实版面写法是"价税合计(大写)玖佰元整 (小写)¥900.00":标签与金额之间允许少量
    # 非数字字符,但**不跨行**取数(跨行容易捞到下一行的合计金额),跨行只在有小写标记时认。
    "invoice_amount": (
        r"(?:发票金额|价税合计|住宿金额)[^0-9\n]{0,16}?[¥￥]?\s*(\d+(?:\.\d{1,2})?)",
        r"(?:发票金额|价税合计|住宿金额)[^0-9]{0,16}?[（(]?小写[）)]?\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)",
    ),
    "payment_amount": (
        r"(?:付款金额|支付金额|实付金额)[^0-9\n]{0,16}?[¥￥]?\s*(\d+(?:\.\d{1,2})?)",
        r"(?:付款金额|支付金额|实付金额)[^0-9]{0,16}?[（(]?小写[）)]?\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)",
    ),
    "invoice_date": r"(?:发票日期|开票日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
    "travel_start_date": r"(?:出差开始|出差起始日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
    "travel_end_date": r"(?:出差结束|出差结束日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
    "travel_city": r"(?:出差城市|目的地)\s*[:：]?\s*([\u4e00-\u9fff]{2,8})",
    "invoice_number": r"(?:发票号码|发票号)\s*[:：]?\s*([A-Za-z0-9-]{5,32})",
    "applicant_name": r"(?:申请人|报销人)\s*[:：]?\s*([\u4e00-\u9fff]{2,5})",
    "invoice_buyer": r"(?:购买方|购方名称|发票抬头)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,32})",
    "payment_party": r"(?:收款方|收款人|收款单位|收款账户名)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,32})",
}


class FieldExtractionOutcome(BaseModel):
    fields: dict[str, ExtractedField]
    llm_diagnostics: LlmExtractionDiagnostics | None = None


def extract_fields(documents: list[Document]) -> dict[str, ExtractedField]:
    return extract_fields_with_diagnostics(documents).fields


def extract_fields_with_diagnostics(documents: list[Document]) -> FieldExtractionOutcome:
    """Deterministic baseline; values are only taken from explicit labels.

    ``LABELS`` 的值可以是单个正则,也可以是正则元组 —— 元组按顺序尝试,先命中的那个为准
    (用于"同一标签在真实版面上有多种写法"的情况,例如价税合计的同行/跨行写法)。
    """
    found: dict[str, ExtractedField] = {}
    for doc in documents:
        for page in doc.pages:
            for name, patterns in LABELS.items():
                if name in found:
                    continue
                for pattern in ((patterns,) if isinstance(patterns, str) else patterns):
                    match = re.search(pattern, page.text)
                    if match:
                        value = match.group(1).strip()
                        found[name] = ExtractedField(name=name, value=value, confidence=1.0,
                            document_id=doc.document_id, page_no=page.number, source_text=match.group(0))
                        break
    llm_outcome = extract_fields_with_llm_diagnostics(documents)
    for name, field in llm_outcome.fields.items():
        found.setdefault(name, field)
    return FieldExtractionOutcome(fields=found, llm_diagnostics=llm_outcome.diagnostics)
