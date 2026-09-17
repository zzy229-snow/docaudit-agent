"""PRD §6.2 文件处理策略路由测试:电子文本 / 扫描PDF / 图片三类分流。"""

import unittest
from io import BytesIO

import fitz
from PIL import Image

from app.parsers.loader import parse_document
from app.parsers.ocr import MockOcrEngine, get_ocr_engine
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
        self.assertEqual(doc.document_type, "image_ocr")
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
