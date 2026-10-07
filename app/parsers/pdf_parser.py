from io import BytesIO

from pypdf import PdfReader

from app.models.document import Page
from app.parsers.ocr import OcrEngine, get_ocr_engine, DEFAULT_TEXT_DENSITY

#: 用 pypdf(BSD-3)而不是 PyMuPDF(AGPL-3.0):本项目按 Apache-2.0 发布,不能引入 copyleft 依赖
MAX_PAGES = 20


def extract_pdf_text(content: bytes) -> list[Page]:
    """只提取电子文本层(PRD §6.2 首选路径),不做 OCR。"""
    reader = PdfReader(BytesIO(content))
    if len(reader.pages) > MAX_PAGES:
        raise ValueError(f"演示版最多支持{MAX_PAGES}页PDF")
    return [
        Page(number=index + 1, text=page.extract_text() or "")
        for index, page in enumerate(reader.pages)
    ]


def is_low_density(pages: list[Page], min_chars: int = DEFAULT_TEXT_DENSITY) -> bool:
    """文本密度检测(PRD §6.2):全页文本合计低于阈值判为扫描件。"""
    if not pages:
        return True
    text = "\n".join(p.text for p in pages)
    return len(text.strip()) < min_chars


def parse_pdf(content: bytes, ocr_engine: OcrEngine | None = None) -> list[Page]:
    """PRD §6.2 首选/回退路径:先电子文本,密度过低则转 OCR。兼容旧调用。"""
    pages = extract_pdf_text(content)
    if is_low_density(pages):
        engine = ocr_engine or get_ocr_engine()
        return [Page(number=1, text=engine.recognize(content))]
    return pages
