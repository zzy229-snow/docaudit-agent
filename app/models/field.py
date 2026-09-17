from typing import Any
from pydantic import BaseModel, Field


class ExtractedField(BaseModel):
    name: str
    value: Any
    confidence: float = Field(ge=0, le=1)
    document_id: str
    page_no: int = Field(ge=1)
    source_text: str
