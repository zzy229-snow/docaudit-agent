import re
from pathlib import Path

from app.models.document import Document
from app.models.field import ExtractedField
from app.services.model_gateway import default_gateway

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

# LLM 抽取的目标字段清单:字段名 -> (中文含义, 抽取提示)
LLM_FIELDS: dict[str, str] = {
    "invoice_amount": "发票金额/价税合计/住宿金额,如 680.00。金额统一输出为阿拉伯数字字符串,中文大写也要转为数字(如 陆佰捌拾元整 -> 680.00)。",
    "payment_amount": "付款金额/实付金额/支付金额,如 680.00。金额统一输出为阿拉伯数字字符串。",
    "invoice_date": "发票日期/开票日期,格式 YYYY-MM-DD。",
    "travel_start_date": "出差开始日期,格式 YYYY-MM-DD。",
    "travel_end_date": "出差结束日期,格式 YYYY-MM-DD。",
    "travel_city": "出差城市/目的地,中文城市名。",
    "invoice_number": "发票号码,字母数字连字符组合。",
    "applicant_name": "申请人/报销人姓名,中文姓名。",
}

LLM_CONFIDENCE = 0.85


def _field_schema() -> dict:
    properties = {}
    for name, hint in LLM_FIELDS.items():
        properties[name] = {
            "type": "object",
            "description": hint,
            "properties": {
                "value": {"type": ["string", "number"], "description": "抽取到的值;原文未出现时必须为 null"},
                "page": {"type": "integer", "description": "所在页码,从 1 开始"},
                "evidence": {"type": "string", "description": "原文证据(短摘录,20字以内),未出现时 null"},
            },
            "required": ["value", "page", "evidence"],
        }
    schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    return schema


def _build_llm_content(documents: list[Document]) -> str:
    blocks = []
    for doc in documents:
        for page in doc.pages:
            blocks.append(f"=== 文档 {doc.file_name} 第{page.number}页 ===\n{page.text}")
    return "\n\n".join(blocks)


def _load_instruction() -> str:
    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "field_extraction.txt"
    return prompt_path.read_text(encoding="utf-8")


def _extract_regex(documents: list[Document]) -> dict[str, ExtractedField]:
    """确定性基线:只取明确标签的值,置信度 1.0。"""
    found: dict[str, ExtractedField] = {}
    for doc in documents:
        for page in doc.pages:
            for name, pattern in LABELS.items():
                match = re.search(pattern, page.text)
                if match and name not in found:
                    found[name] = ExtractedField(
                        name=name, value=match.group(1).strip(), confidence=1.0,
                        document_id=doc.document_id, page_no=page.number,
                        source_text=match.group(0))
    return found


def _extract_llm(documents: list[Document], gateway) -> dict[str, ExtractedField]:
    """LLM 抽取:正则漏掉的字段由 LLM 补缺。返回新字段,置信度 0.85。"""
    result: dict[str, ExtractedField] = {}
    payload = gateway.complete_json(
        instruction=_load_instruction(),
        content=_build_llm_content(documents),
        schema=_field_schema(),
    )
    for name in LLM_FIELDS:
        item = payload.get(name)
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        page = item.get("page")
        evidence = item.get("evidence")
        if value is None or value == "":
            continue
        # 找到对应文档(页码定位时取内容包含证据的文档,简化:取第一个匹配页码)
        for doc in documents:
            if any(p.number == page for p in doc.pages):
                document_id = doc.document_id
                break
        else:
            document_id = documents[0].document_id
        result[name] = ExtractedField(
            name=name,
            value=str(value),
            confidence=LLM_CONFIDENCE,
            document_id=document_id,
            page_no=int(page) if isinstance(page, int) else 1,
            source_text=str(evidence or ""),
        )
    return result


def extract_fields(documents: list[Document], gateway=None,
                   use_llm: bool = True) -> dict[str, ExtractedField]:
    """字段抽取:正则优先(确定性基线),LLM 补缺,失败自动降级正则。

    - 正则结果置信度 1.0,LLM 结果 0.85。
    - LLM 未配置或调用失败时不中断主流程,仅输出日志(与 RAG 降级策略一致)。
    - gateway 为 None 时按环境变量构造(MODEL_BASE_URL/MODEL_API_KEY/MODEL_NAME)。
    """
    found = _extract_regex(documents)
    if not use_llm or not documents:
        return found
    gw = gateway if gateway is not None else default_gateway()
    if not gw.available():
        return found
    try:
        llm_fields = _extract_llm(documents, gw)
    except Exception as exc:  # noqa: BLE001 - 降级不应中断审核主流程
        print(f"[extraction] LLM 抽取失败,降级为纯正则: {exc}")
        return found
    for name, field in llm_fields.items():
        if name not in found:
            found[name] = field
    return found
