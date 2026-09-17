from hashlib import sha256
from io import BytesIO
from pathlib import Path
from docx import Document as WordDocument
from openpyxl import load_workbook
from app.models.document import Document, Page
from .pdf_parser import parse_pdf
from .image_parser import parse_image


MAX_BYTES = 8 * 1024 * 1024


def parse_document(file_name: str, content: bytes) -> Document:
    if not content or len(content) > MAX_BYTES:
        raise ValueError("文件不能为空且不得超过8MB")
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        pages = parse_pdf(content)
    elif suffix in {".png", ".jpg", ".jpeg"}:
        pages = [Page(number=1, text=parse_image(content))]
    elif suffix == ".txt":
        pages = [Page(number=1, text=content.decode("utf-8-sig"))]
    elif suffix == ".docx":
        word = WordDocument(BytesIO(content))
        text = [p.text for p in word.paragraphs]
        text += [" | ".join(c.text for c in row.cells) for t in word.tables for row in t.rows]
        pages = [Page(number=1, text="\n".join(text))]
    elif suffix == ".xlsx":
        book = load_workbook(BytesIO(content), read_only=True, data_only=True)
        text = [f"[{sheet.title}] " + " | ".join(" ".join(str(v) for v in row if v is not None) for row in sheet.iter_rows(values_only=True)) for sheet in book.worksheets]
        book.close()
        pages = [Page(number=1, text="\n".join(text))]
    else:
        raise ValueError("仅支持PDF、TXT、DOCX、XLSX及图片文件")
    return Document(document_id=sha256(content).hexdigest()[:16], file_name=Path(file_name).name,
                    document_type="unknown", pages=pages)
