import fitz
from app.models.document import Page


def parse_pdf(content: bytes) -> list[Page]:
    with fitz.open(stream=content, filetype="pdf") as pdf:
        if len(pdf) > 20:
            raise ValueError("演示版最多支持20页PDF")
        pages = [Page(number=i + 1, text=page.get_text("text")) for i, page in enumerate(pdf)]
    if not any(page.text.strip() for page in pages):
        raise ValueError("扫描PDF暂无OCR支持；请上传可复制文字的PDF")
    return pages
