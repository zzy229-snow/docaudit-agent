"""材料可读性判定测试:读不出来就不给结论。

覆盖:
- 演示引擎文本的识别(带标记即视为未识别);
- 三种"不可读"情形(MOCK_OCR / NO_TEXT / NO_FIELDS);
- stub 合成语料引擎被视为"已识别";
- 端到端:演示引擎下上传图片 → 结论 UNDETERMINED,且不带任何抽取字段与误导性风险;
- 回归:纯文本演示材料(不经过 OCR)不受影响,结论照旧。
"""
import os
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

# 第三方库(pymilvus)在 import 时会 load_dotenv() 读走仓库 .env 里的真实引擎配置;
# 这里在任何 app 模块之前钉住离线默认值,保证"演示模式"相关断言可复现。
os.environ["OCR_ENGINE"] = "mock"
os.environ["MODEL_PROVIDER"] = "mock"

from app.agent.graph import run_audit
from app.api.main import app
from app.models.document import Document, Page
from app.parsers.loader import parse_document
from app.parsers.ocr import MockOcrEngine, StubOcrEngine, get_ocr_engine
from app.services.invoice_registry import isolate_registry
from app.services.readability import (
    assess_readability,
    demo_mode_notice,
    is_synthetic_engine,
    is_synthetic_text,
    needs_ocr,
)

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"


def make_image() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    return buf.getvalue()


def make_doc(text: str, document_type: str = "image_ocr", file_name: str = "invoice.png") -> Document:
    return Document(document_id="d1", file_name=file_name, document_type=document_type,
                    pages=[Page(number=1, text=text)])


class SyntheticTextTests(unittest.TestCase):
    def test_mock_engine_text_is_synthetic(self):
        text = parse_document("invoice.png", make_image()).text
        self.assertTrue(is_synthetic_text(text))
        self.assertIn("【OCR-MOCK】", text)

    def test_stub_engine_text_is_not_synthetic(self):
        with patch.dict(os.environ, {"OCR_ENGINE": "stub"}):
            doc = parse_document("invoice.png", make_image())
        self.assertFalse(is_synthetic_text(doc.text))
        self.assertNotIn("【OCR-MOCK】", doc.text)
        self.assertIn("520.00", doc.text)

    def test_engine_factory_maps_stub_and_mock(self):
        with patch.dict(os.environ, {"OCR_ENGINE": "stub"}):
            engine = get_ocr_engine()
        self.assertIsInstance(engine, StubOcrEngine)
        self.assertEqual(engine.name, "stub")
        with patch.dict(os.environ, {"OCR_ENGINE": "mock"}):
            self.assertIsInstance(get_ocr_engine(), MockOcrEngine)

    def test_engine_name_helpers(self):
        self.assertTrue(is_synthetic_engine("mock"))
        self.assertTrue(is_synthetic_engine("stub"))
        self.assertFalse(is_synthetic_engine("tesseract"))
        self.assertTrue(needs_ocr("image_ocr_hard"))
        self.assertTrue(needs_ocr("pdf_ocr"))
        self.assertFalse(needs_ocr("pdf_text"))

    def test_demo_mode_notice_mentions_engine_and_fix(self):
        with patch.dict(os.environ, {"OCR_ENGINE": "mock", "MODEL_PROVIDER": "mock"}):
            notice = demo_mode_notice()
        self.assertIsNotNone(notice)
        self.assertIn("OCR 引擎=mock", notice)
        self.assertIn("无法判定", notice)
        with patch.dict(os.environ, {"OCR_ENGINE": "tesseract", "MODEL_PROVIDER": "openai"}):
            self.assertIsNone(demo_mode_notice())


class VerdictTests(unittest.TestCase):
    def test_synthetic_text_is_unreadable_with_fix_hint(self):
        doc = make_doc("【OCR-MOCK】发票号码：INV-MOCK-2026-001 发票金额：520.00元 开票日期：2026-05-12")
        verdict = assess_readability([doc], {"invoice_amount": object()})

        self.assertFalse(verdict.readable)
        self.assertEqual(verdict.code, "MOCK_OCR")
        self.assertIn("OCR_ENGINE=tesseract|http|mineru", verdict.reason)
        self.assertEqual(verdict.unreadable_files, ["invoice.png"])
        self.assertIn("无法判定", verdict.as_trace())

    def test_short_text_is_unreadable(self):
        verdict = assess_readability([make_doc("WETS: 张三")], {})

        self.assertFalse(verdict.readable)
        self.assertEqual(verdict.code, "NO_TEXT")

    def test_no_fields_is_unreadable(self):
        verdict = assess_readability([make_doc("这张材料是一段足够长的普通说明文字，但没有任何报销字段")], {})

        self.assertFalse(verdict.readable)
        self.assertEqual(verdict.code, "NO_FIELDS")

    def test_no_documents_is_unreadable(self):
        verdict = assess_readability([], {})
        self.assertFalse(verdict.readable)
        self.assertEqual(verdict.code, "NO_TEXT")

    def test_readable_when_text_and_fields_present(self):
        doc = make_doc("发票号码：12345678 发票金额：900.00元 开票日期：2019-02-19", file_name="a.png")
        verdict = assess_readability([doc], {"invoice_amount": object()})

        self.assertTrue(verdict.readable)
        self.assertIsNone(verdict.code)
        self.assertIn("材料可读", verdict.as_trace())


class PipelineTests(unittest.TestCase):
    def test_demo_engine_image_yields_undetermined_report(self):
        report = run_audit([("invoice.png", make_image())], task_id="task-readability-demo")

        self.assertEqual(report.status, "UNDETERMINED")
        self.assertEqual(report.fields, {})
        self.assertEqual([r.risk_type for r in report.risks][0], "MATERIAL_UNREADABLE")
        self.assertEqual(report.risk_summary.get("HIGH"), 1)
        self.assertIsNotNone(report.failure_reason)
        self.assertNotIn("【OCR-MOCK】", report.failure_reason or "")
        self.assertTrue(any("material_unreadable" in line for line in report.trace))

    def test_manual_fields_survive_unreadable_material(self):
        """人工修正/补充的字段是人的判断,不受"材料未识别"影响,仍出现在报告里。"""
        report = run_audit(
            [("invoice.png", make_image())],
            task_id="task-readability-manual",
            field_overrides={"invoice_amount": {"value": "900.00", "reason": "按发票原件核对"}},
        )

        self.assertEqual(report.status, "UNDETERMINED")
        self.assertEqual(list(report.fields), ["invoice_amount"])
        self.assertEqual(report.fields["invoice_amount"].value, "900.00")

    def test_text_material_unaffected_by_readability_rule(self):
        """回归:纯文本演示材料不经过 OCR,结论照旧(演示链路不能被误伤)。"""
        files = [(p.name, p.read_bytes()) for p in sorted((DEMO / "normal").iterdir()) if p.is_file()]
        report = run_audit(files, task_id="task-readability-txt")

        self.assertEqual(report.status, "PASS")
        self.assertTrue(report.fields)


class ApiIntegrationTests(unittest.TestCase):
    """"上传图片却没识别"必须能从前端/接口层面看出来(客户就是这么踩的坑)。"""

    def setUp(self):
        isolate_registry()
        self.client = TestClient(app)

    def test_uploading_image_in_demo_mode_returns_undetermined(self):
        task_id = self.client.post("/api/v1/audit-tasks",
                                   json={"applicant": "张三", "expense_type": "HOTEL"}).json()["task_id"]
        upload = self.client.post(f"/api/v1/audit-tasks/{task_id}/documents",
                                  files={"file": ("invoice.png", make_image(), "image/png")})
        self.assertEqual(upload.status_code, 200)

        run = self.client.post(f"/api/v1/audit-tasks/{task_id}/run")
        self.assertEqual(run.status_code, 200)
        body = run.json()

        self.assertEqual(body["result_status"], "UNDETERMINED")
        summary = self.client.get(f"/api/v1/audit-tasks/{task_id}/summary").json()["summary"]
        self.assertEqual(summary["status"], "UNDETERMINED")
        self.assertIn("OCR_ENGINE", summary["conclusion"] + summary["next_action"])
        self.assertEqual(self.client.get(f"/api/v1/audit-tasks/{task_id}/fields").json()["fields"], {})
        risks = self.client.get(f"/api/v1/audit-tasks/{task_id}/risks").json()["risks"]
        self.assertEqual(risks[0]["risk_type"], "MATERIAL_UNREADABLE")


if __name__ == "__main__":
    unittest.main()
