from hashlib import sha256
from io import BytesIO
from pathlib import Path
from docx import Document as WordDocument
from openpyxl import load_workbook
from app.models.document import Document, Page
from .pdf_parser import extract_pdf_text, is_low_density
from .image_parser import parse_image
from app.parsers.ocr import OcrEngine, get_ocr_engine

MAX_BYTES = 8 * 1024 * 1024


def parse_document(file_name: str, content: bytes, ocr_engine: OcrEngine | None = None) -> Document:
    """PRD §6.2 文件处理策略分发:类型识别 → 首选路径 → 回退路径(OCR)。"""
    if not content or len(content) > MAX_BYTES:
        raise ValueError("文件不能为空且不得超过8MB")
    engine = ocr_engine or get_ocr_engine()
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        # 首选:电子文本;回退:文本密度过低转 OCR(§6.2)
        pages = extract_pdf_text(content)
        if is_low_density(pages):
            pages = [Page(number=1, text=engine.recognize(content))]
            doc_type = "pdf_ocr"
        else:
            doc_type = "pdf_text"
    elif suffix in {".png", ".jpg", ".jpeg"}:
        pages = [Page(number=1, text=parse_image(content, engine))]
        doc_type = "image_ocr"
    elif suffix == ".txt":
        pages = [Page(number=1, text=content.decode("utf-8-sig"))]
        doc_type = "text"
    elif suffix == ".docx":
        word = WordDocument(BytesIO(content))
        text = [p.text for p in word.paragraphs]
        text += [" | ".join(c.text for c in row.cells) for t in word.tables for row in t.rows]
        pages = [Page(number=1, text="\n".join(text))]
        doc_type = "docx"
    elif suffix == ".xlsx":
        book = load_workbook(BytesIO(content), read_only=True, data_only=True)
        text = [f"[{sheet.title}] " + " | ".join(
            " ".join(str(v) for v in row if v is not None)
            for row in sheet.iter_rows(values_only=True)) for sheet in book.worksheets]
        book.close()
        pages = [Page(number=1, text="\n".join(text))]
        doc_type = "xlsx"
    else:
        raise ValueError("仅支持PDF、TXT、DOCX、XLSX及图片文件")
    return Document(document_id=sha256(content).hexdigest()[:16], file_name=Path(file_name).name,
                    document_type=doc_type, pages=pages)
