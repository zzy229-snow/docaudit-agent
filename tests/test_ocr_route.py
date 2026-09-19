"""PRD §6.2 文件处理策略路由测试:电子文本 / 扫描PDF / 图片三类分流。"""

import unittest
from io import BytesIO
from unittest.mock import patch

import fitz
from PIL import Image

from app.parsers.loader import parse_document
from app.parsers.ocr import HttpOcrEngine, MockOcrEngine, TesseractOcrEngine, get_ocr_engine
from app.parsers.pdf_parser import is_low_density
from app.agent.graph import run_audit


def make_scanned_pdf() -> bytes:
    """无文本层 PDF(模拟扫描件):空白页 + 一张图片,文本密度为 0。"""
    img = Image.new("RGB", (400, 300), "white")
    pdf_bytes = BytesIO()
    img.save(pdf_bytes, format="PDF")
    return pdf_bytes.getvalue()


def make_electronic_pdf() -> bytes:
    """带文本层的电子 PDF。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "发票金额：1280.00元\n开票日期：2026-05-12\n发票号码：INV-E-001")
    data = doc.tobytes()
    doc.close()
    return data


def make_image() -> bytes:
    img = Image.new("RGB", (400, 300), "white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class OcrRouteTests(unittest.TestCase):
    def test_default_engine_is_mock(self):
        self.assertIsInstance(get_ocr_engine(), MockOcrEngine)

    @patch.dict("os.environ", {"OCR_ENGINE": "tesseract", "TESSERACT_EXE": "tesseract", "TESSERACT_LANG": "chi_sim+eng"})
    def test_tesseract_engine_can_be_selected(self):
        engine = get_ocr_engine()
        self.assertIsInstance(engine, TesseractOcrEngine)
        self.assertEqual(engine.name, "tesseract")

    @patch.dict("os.environ", {"OCR_ENGINE": "http", "OCR_HTTP_URL": "https://ocr.example.test", "OCR_HTTP_TEXT_PATH": "result.text"})
    def test_http_engine_can_be_selected(self):
        engine = get_ocr_engine()
        self.assertIsInstance(engine, HttpOcrEngine)
        self.assertEqual(engine.name, "http")
        self.assertEqual(engine.text_path, "result.text")

    @patch.dict("os.environ", {"OCR_ENGINE": "http", "OCR_HTTP_URL": ""})
    def test_http_engine_requires_url(self):
        engine = get_ocr_engine()
        with self.assertRaisesRegex(RuntimeError, "OCR_HTTP_URL"):
            engine.recognize(b"fake-image")

    def test_http_engine_extracts_text_from_configured_path(self):
        engine = HttpOcrEngine(url="https://ocr.example.test", text_path="data.text")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"data": {"text": "\\u53d1\\u7968\\u91d1\\u989d\\uff1a520.00\\u5143"}}'

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            text = engine.recognize(b"fake-image")

        self.assertIn("发票金额：520.00元", text)

    def test_scanned_pdf_routes_to_ocr(self):
        doc = parse_document("scan.pdf", make_scanned_pdf())
        self.assertEqual(doc.document_type, "pdf_ocr")
        self.assertIn("OCR-MOCK", doc.text)

    def test_electronic_pdf_keeps_text_path(self):
        doc = parse_document("invoice.pdf", make_electronic_pdf())
        self.assertEqual(doc.document_type, "pdf_text")
        self.assertIn("1280.00", doc.text)

    def test_image_routes_to_ocr(self):
        doc = parse_document("photo.jpg", make_image())
        # 任务④:图片走 OCR 路径,并按困难区域评估追加难度后缀(_hard/_handwriting)
        self.assertTrue(doc.document_type.startswith("image_ocr"))
        self.assertIn("OCR-MOCK", doc.text)

    def test_low_density_detection(self):
        self.assertTrue(is_low_density([], min_chars=50))
        from app.models.document import Page
        self.assertFalse(is_low_density([Page(number=1, text="发票金额：1280.00元" * 10)], min_chars=50))

    def test_image_chain_reaches_report(self):
        """端到端:图片扫描件经 OCR → 抽取 → 检查 → 报告(缺少材料应触发风险)。"""
        result = run_audit([("invoice.jpg", make_image())])
        self.assertEqual(result.status, "REVIEW_REQUIRED")
        self.assertTrue(any(r.risk_type == "REQUIRED_DOCUMENTS" for r in result.risks))
        self.assertEqual(result.fields["invoice_amount"].value, "520.00")


if __name__ == "__main__":
    unittest.main()
