from io import BytesIO
from PIL import Image


def validate_image(content: bytes) -> None:
    with Image.open(BytesIO(content)) as image:
        image.verify()


def parse_image(content: bytes) -> str:
    validate_image(content)
    raise ValueError("图片OCR尚未接入；当前版本请使用电子PDF或TXT样例")
