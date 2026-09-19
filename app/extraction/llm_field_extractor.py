from datetime import date
from decimal import Decimal, InvalidOperation
import os
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.models.document import Document
from app.models.field import ExtractedField
from app.services.model_gateway import ModelGateway, ModelGatewayError


FIELD_NAMES = {
    "invoice_amount",
    "payment_amount",
    "invoice_date",
    "travel_start_date",
    "travel_end_date",
    "travel_city",
    "invoice_number",
    "applicant_name",
    "invoice_buyer",
    "payment_party",
}


class LlmFieldCandidate(BaseModel):
    value: Any | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    source_text: str = ""
    page_no: int = Field(default=1, ge=1)


class LlmFieldExtractionResult(BaseModel):
    fields: dict[str, LlmFieldCandidate]


class LlmExtractionDiagnostics(BaseModel):
    attempts: int = 0
    accepted_fields: list[str] = Field(default_factory=list)
    rejected_fields: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    error_types: list[str] = Field(default_factory=list)
    fallback_used: bool = False


class LlmExtractionOutcome(BaseModel):
    fields: dict[str, ExtractedField]
    diagnostics: LlmExtractionDiagnostics


def extract_fields_with_llm(documents: list[Document], gateway: ModelGateway | None = None) -> dict[str, ExtractedField]:
    return extract_fields_with_llm_diagnostics(documents, gateway=gateway).fields


def extract_fields_with_llm_diagnostics(
    documents: list[Document],
    gateway: ModelGateway | None = None,
) -> LlmExtractionOutcome:
    gateway = gateway or ModelGateway()
    diagnostics = LlmExtractionDiagnostics()
    if gateway.provider == "mock":
        diagnostics.fallback_used = True
        diagnostics.errors.append("MODEL_PROVIDER=mock, skip LLM extraction")
        return LlmExtractionOutcome(fields={}, diagnostics=diagnostics)
    prompt = _load_prompt()
    content = _document_context(documents)
    parsed: LlmFieldExtractionResult | None = None
    for attempt in range(1, _max_attempts() + 1):
        diagnostics.attempts = attempt
        try:
            payload = gateway.complete_json(_prompt_for_attempt(prompt, attempt), content)
            parsed = LlmFieldExtractionResult.model_validate(payload)
            break
        except (ModelGatewayError, ValidationError, ValueError) as exc:
            diagnostics.errors.append(f"attempt_{attempt}: {exc}")
            diagnostics.error_types.append(_classify_error(exc))
    if parsed is None:
        diagnostics.fallback_used = True
        return LlmExtractionOutcome(fields={}, diagnostics=diagnostics)

    extracted: dict[str, ExtractedField] = {}
    doc_id = documents[0].document_id if documents else "llm"
    for name, candidate in parsed.fields.items():
        normalized, reject_reason = _normalize_candidate(name, candidate, content)
        if reject_reason:
            diagnostics.rejected_fields[name] = reject_reason
            continue
        assert normalized is not None
        extracted[name] = ExtractedField(
            name=name,
            value=normalized,
            confidence=candidate.confidence,
            document_id=doc_id,
            page_no=candidate.page_no,
            source_text=candidate.source_text,
        )
        diagnostics.accepted_fields.append(name)
    if not extracted:
        diagnostics.fallback_used = True
    return LlmExtractionOutcome(fields=extracted, diagnostics=diagnostics)


def classify_rejection(reason: str) -> str:
    if reason.startswith("low confidence"):
        return "low_confidence"
    if reason == "source_text not found in documents":
        return "source_text_not_found"
    if reason == "missing source_text":
        return "missing_source_text"
    if reason == "unknown field":
        return "unknown_field"
    if reason == "empty value":
        return "empty_value"
    if reason.startswith("invalid"):
        return "invalid_format"
    if reason == "negative amount":
        return "invalid_format"
    return "other_rejection"


def _classify_error(exc: Exception) -> str:
    message = str(exc)
    if isinstance(exc, ValidationError):
        return "schema_validation_error"
    if "未返回JSON对象" in message or "JSON解析失败" in message or "JSON顶层必须是对象" in message:
        return "json_format_error"
    if "模型调用失败" in message:
        return "model_call_error"
    return "other_error"


def _normalize_candidate(name: str, candidate: LlmFieldCandidate, document_context: str) -> tuple[str | None, str | None]:
    if name not in FIELD_NAMES:
        return None, "unknown field"
    if candidate.value in (None, ""):
        return None, "empty value"
    if candidate.confidence < _min_confidence():
        return None, f"low confidence {candidate.confidence}"
    if not candidate.source_text:
        return None, "missing source_text"
    if candidate.source_text not in document_context:
        return None, "source_text not found in documents"

    raw = str(candidate.value).strip()
    if name.endswith("_amount"):
        return _normalize_amount(raw)
    if name.endswith("_date"):
        return _normalize_date(raw)
    if name == "travel_city":
        city = raw.strip()
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,8}市?", city):
            return None, "invalid city format"
        return city, None
    if name == "invoice_number":
        if not re.fullmatch(r"[A-Za-z0-9-]{5,32}", raw):
            return None, "invalid invoice number format"
        return raw, None
    if name == "applicant_name":
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,5}", raw):
            return None, "invalid applicant name format"
        return raw, None
    if name in {"invoice_buyer", "payment_party"}:
        if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,32}", raw):
            return None, f"invalid {name} format"
        return raw, None
    return raw, None


def _normalize_amount(value: str) -> tuple[str | None, str | None]:
    text = value.replace("￥", "").replace("¥", "").replace("元", "").replace(",", "").strip()
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, "invalid amount format"
    if amount < 0:
        return None, "negative amount"
    return f"{amount:.2f}", None


def _normalize_date(value: str) -> tuple[str | None, str | None]:
    text = value.strip().replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("/", "-").replace(".", "-")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None, "invalid date format"
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat(), None
    except ValueError:
        return None, "invalid date value"


def _document_context(documents: list[Document]) -> str:
    parts = []
    for doc in documents:
        for page in doc.pages:
            parts.append(f"file={doc.file_name}\ndocument_id={doc.document_id}\npage={page.number}\n{page.text}")
    return "\n\n---\n\n".join(parts)


def _load_prompt() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "prompts" / "field_extraction_v1.txt").read_text(encoding="utf-8")


def _max_attempts() -> int:
    raw = os.getenv("MODEL_EXTRACTION_MAX_ATTEMPTS", "2")
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return 2


def _min_confidence() -> float:
    raw = os.getenv("MODEL_EXTRACTION_MIN_CONFIDENCE", "0.7")
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.7


def _prompt_for_attempt(prompt: str, attempt: int) -> str:
    if attempt == 1:
        return prompt
    return (
        f"{prompt}\n\n"
        "上一次输出未通过系统校验。请重新输出严格 JSON 对象：顶层只能包含 fields；"
        "字段名只能来自 Schema；不要 Markdown；不要解释；没有原文证据必须返回 null。"
    )
