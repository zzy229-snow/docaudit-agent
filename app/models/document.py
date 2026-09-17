from pydantic import BaseModel, Field


class Page(BaseModel):
    number: int = Field(ge=1)
    text: str


class Document(BaseModel):
    document_id: str
    file_name: str
    document_type: str
    pages: list[Page]

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)
