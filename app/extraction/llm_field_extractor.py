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
}


class LlmFieldCandidate(BaseModel):
    value: Any | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    source_text: str = ""
    page_no: int = Field(default=1, ge=1)


class LlmFieldExtractionResult(BaseModel):
    fields: dict[str, LlmFieldCandidate]


def extract_fields_with_llm(documents: list[Document], gateway: ModelGateway | None = None) -> dict[str, ExtractedField]:
    gateway = gateway or ModelGateway()
    if gateway.provider == "mock":
        return {}
    prompt = _load_prompt()
    content = _document_context(documents)
    try:
        payload = gateway.complete_json(prompt, content)
        parsed = LlmFieldExtractionResult.model_validate(payload)
    except (ModelGatewayError, ValidationError, ValueError):
        return {}
    extracted: dict[str, ExtractedField] = {}
    doc_id = documents[0].document_id if documents else "llm"
    for name, candidate in parsed.fields.items():
        if name not in FIELD_NAMES or candidate.value in (None, ""):
            continue
        extracted[name] = ExtractedField(
            name=name,
            value=candidate.value,
            confidence=candidate.confidence,
            document_id=doc_id,
            page_no=candidate.page_no,
            source_text=candidate.source_text,
        )
    return extracted


def _document_context(documents: list[Document]) -> str:
    parts = []
    for doc in documents:
        for page in doc.pages:
            parts.append(f"file={doc.file_name}\ndocument_id={doc.document_id}\npage={page.number}\n{page.text}")
    return "\n\n---\n\n".join(parts)


def _load_prompt() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "prompts" / "field_extraction_v1.txt").read_text(encoding="utf-8")
