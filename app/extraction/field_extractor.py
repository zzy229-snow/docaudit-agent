import re
from pydantic import BaseModel

from app.models.document import Document
from app.models.field import ExtractedField
from app.extraction.llm_field_extractor import LlmExtractionDiagnostics, extract_fields_with_llm_diagnostics
from app.parsers.ocr import normalize_ocr_value


#: 发票/凭证底部签章栏的标签。实测(MinerU 输出)这一行形如
#: "收款人: 复核: 开票人: 前台",按标签正则会把"复核"当成收款方 —— 必须排除。
SIGNATURE_LABELS: tuple[str, ...] = ("收款人", "复核", "开票人", "审核人", "签章")
#: 主体字段的值落在这些说明书用词上时丢弃
SUBJECT_VALUE_REJECTS: dict[str, set[str]] = {
    "payment_party": {"复核", "开票人", "收款人", "收款方", "审核", "审核人", "签章"},
}


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
    # 发票代码:本地路线(MinerU/Tesseract)也应当抽到,过去只有云端结构化字段才给
    "invoice_code": r"(?:发票代码)\s*[:：]?\s*([A-Za-z0-9-]{5,32})",
    "applicant_name": r"(?:申请人|报销人)\s*[:：]?\s*([\u4e00-\u9fff]{2,5})",
    "invoice_buyer": (
        r"(?:购买方|购方名称|发票抬头)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,32})",
        # 真实票面(MinerU 输出同理):"购买方"与"名 称：<公司名>"在表格里分格换行,
        # 名称后面紧跟"纳税人识别号",用负向先行断言截断,避免把下一个标签一起吃进去
        r"名\s*称\s*[:：]\s*((?:(?!纳税人识别号|地址|电话|开户行|税号|密码区)[\u4e00-\u9fffA-Za-z0-9（）()·\-]){2,40})",
    ),
    "payment_party": r"(?:收款方|收款人|收款单位|收款账户名)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,32})",
}


class FieldExtractionOutcome(BaseModel):
    fields: dict[str, ExtractedField]
    llm_diagnostics: LlmExtractionDiagnostics | None = None


def extract_fields(documents: list[Document]) -> dict[str, ExtractedField]:
    return extract_fields_with_diagnostics(documents).fields


def _line_around(text: str, start: int, end: int) -> str:
    """取出匹配所在的整行(用于判断它是不是签章栏)。"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return text[line_start: line_end if line_end != -1 else len(text)]


def _is_signature_row(line: str) -> bool:
    """整行同时出现两个以上签章标签 → 这是签名栏,不是真实的收款方/主体。"""
    return sum(label in line for label in SIGNATURE_LABELS) >= 2


def _accepts(name: str, value: str, text: str, start: int, end: int) -> bool:
    rejects = SUBJECT_VALUE_REJECTS.get(name)
    if not rejects:
        return True
    if value in rejects:
        return False
    return not _is_signature_row(_line_around(text, start, end))


def _normalize_value(name: str, value: str) -> str:
    """字段值规范化。日期统一成 ISO 形态(实测 MinerU 会给出"2019年02月19日")。"""
    if name.endswith("_date"):
        return normalize_ocr_value(name, value) or value
    return value


def extract_fields_with_diagnostics(documents: list[Document]) -> FieldExtractionOutcome:
    """Deterministic baseline; values are only taken from explicit labels.

    ``LABELS`` 的值可以是单个正则,也可以是正则元组 —— 元组按顺序尝试,先命中的那个为准
    (用于"同一标签在真实版面上有多种写法"的情况,例如价税合计的同行/跨行写法)。
    命中的值还要过 :func:`_accepts`(排除签章栏)与 :func:`_normalize_value`(日期规范化)。
    """
    found: dict[str, ExtractedField] = {}
    for doc in documents:
        for page in doc.pages:
            for name, patterns in LABELS.items():
                if name in found:
                    continue
                for pattern in ((patterns,) if isinstance(patterns, str) else patterns):
                    match = re.search(pattern, page.text)
                    if not match:
                        continue
                    value = match.group(1).strip()
                    if not _accepts(name, value, page.text, match.start(), match.end()):
                        continue
                    found[name] = ExtractedField(
                        name=name,
                        value=_normalize_value(name, value),
                        confidence=1.0,
                        document_id=doc.document_id,
                        page_no=page.number,
                        source_text=match.group(0),
                    )
                    break
    llm_outcome = extract_fields_with_llm_diagnostics(documents)
    for name, field in llm_outcome.fields.items():
        found.setdefault(name, field)
    return FieldExtractionOutcome(fields=found, llm_diagnostics=llm_outcome.diagnostics)
