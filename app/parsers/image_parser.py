from io import BytesIO
from PIL import Image
from app.parsers.ocr import OcrEngine, get_ocr_engine


def validate_image(content: bytes) -> None:
    with Image.open(BytesIO(content)) as image:
        image.verify()


def parse_image(content: bytes, ocr_engine: OcrEngine | None = None) -> str:
    """PRD §6.2 扫描/图片路径:先校验,再 OCR。引擎默认 Mock,可配 MinerU。"""
    validate_image(content)
    engine = ocr_engine or get_ocr_engine()
    return engine.recognize(content)
