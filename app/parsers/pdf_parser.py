import fitz
from app.models.document import Page
from app.parsers.ocr import OcrEngine, get_ocr_engine, DEFAULT_TEXT_DENSITY


def extract_pdf_text(content: bytes) -> list[Page]:
    """只提取电子文本层(PRD §6.2 首选路径),不做 OCR。"""
    with fitz.open(stream=content, filetype="pdf") as pdf:
        if len(pdf) > 20:
            raise ValueError("演示版最多支持20页PDF")
        return [Page(number=i + 1, text=page.get_text("text")) for i, page in enumerate(pdf)]


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
