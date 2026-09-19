"""任务④ 手写/困难区域识别测试:区域评估 + 图片预处理 + 难度路由 + 人工复核联动。

不依赖真实 OCR 引擎(用 fake 引擎),保证离线确定性;真实引擎留集成路径。
"""

import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.models.document import Page
from app.parsers.loader import parse_document
from app.parsers.ocr import (MockOcrEngine, OcrEngine, RoutingOcrEngine,
                             TesseractOcrEngine, preprocess_image_for_ocr)
from app.parsers.region import (LEVEL_HANDWRITING, LEVEL_HARD, LEVEL_NORMAL,
                                assess, assess_image, assess_text, doc_type_suffix,
                                human_review_reason)
from app.agent.graph import run_audit


def make_image(width: int, height: int, color: str = "white") -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_text_image(text: str, width: int = 1200, height: int = 400) -> bytes:
    """生成含文字的图片(用于困难区域评估路径)。"""
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 36)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 40), text, font=font, fill="black")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_lines_image(lines, width: int = 1000) -> bytes:
    """多行文本图片(接近真实票据版式:每字段一行)。"""
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (width, 120 + 70 * len(lines)), (250, 249, 246))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 34)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((40, 30 + i * 70), line, font=font, fill=(40, 40, 40))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeEngine(OcrEngine):
    name = "fake"

    def __init__(self, text: str = "发票金额：520.00元"):
        self.text = text
        self.calls = 0

    def recognize(self, content: bytes) -> str:
        self.calls += 1
        return self.text


class FailingEngine(OcrEngine):
    name = "failing"

    def recognize(self, content: bytes) -> str:
        raise RuntimeError("engine down")


class RegionAssessmentTests(unittest.TestCase):
    def test_low_resolution_image_is_hard(self):
        result = assess_image(make_image(400, 300))
        self.assertEqual(result.level, LEVEL_HARD)
        self.assertTrue(result.needs_human_review)
        self.assertEqual(result.suggested_engine, "mineru")
        self.assertTrue(any("分辨率" in r for r in result.reasons))

    def test_normal_image_is_normal(self):
        result = assess_image(make_text_image("发票金额：520.00元 申请人：张三"))
        self.assertEqual(result.level, LEVEL_NORMAL)
        self.assertFalse(result.needs_human_review)

    def test_handwriting_keyword_detected(self):
        result = assess_text("发票金额：1280.00元 申请人：张三 签字：张三")
        self.assertEqual(result.level, LEVEL_HANDWRITING)
        self.assertTrue(result.needs_human_review)
        self.assertTrue(any("手写" in r or "签字" in r for r in result.reasons))

    def test_garbled_text_is_hard(self):
        result = assess_text("囧囧囧□□□碁碁碁■■■" * 3)
        self.assertEqual(result.level, LEVEL_HARD)
        self.assertTrue(result.needs_human_review)

    def test_empty_text_is_hard(self):
        result = assess_text("")
        self.assertEqual(result.level, LEVEL_HARD)
        self.assertIn("过少", result.reasons[0])

    def test_normal_text_passes(self):
        result = assess_text("发票金额：1280.00元 开票日期：2026-05-12 申请人：张三")
        self.assertEqual(result.level, LEVEL_NORMAL)
        self.assertFalse(result.needs_human_review)

    def test_combined_assessment_takes_worst(self):
        """图片低质 + 文本含手写 -> 取更严重等级 handwriting。"""
        result = assess(content=make_image(400, 300), text="签字：李四 金额520.00")
        self.assertEqual(result.level, LEVEL_HANDWRITING)

    def test_doc_type_suffix_mapping(self):
        self.assertEqual(doc_type_suffix(assess_text("签字：张三 金额500元")), "_handwriting")
        self.assertEqual(doc_type_suffix(assess_image(make_image(300, 200))), "_hard")
        self.assertEqual(doc_type_suffix(assess_text("发票金额：520.00元 申请人：张三")), "")

    def test_human_review_reason_mentions_file(self):
        reason = human_review_reason(assess_text("签字：张三 金额500元"), "invoice.jpg")
        self.assertIn("invoice.jpg", reason)
        self.assertIn("handwriting", reason)


class PreprocessTests(unittest.TestCase):
    def test_preprocess_enlarges_and_returns_png(self):
        original = make_image(300, 200)
        processed = preprocess_image_for_ocr(original, scale=2)
        self.assertTrue(processed.startswith(b"\x89PNG"))
        with Image.open(BytesIO(processed)) as img:
            self.assertEqual(img.size, (600, 400))
            self.assertEqual(img.mode, "L")  # 灰度 + 二值化

    def test_preprocess_keeps_original_on_bad_input(self):
        bad = b"not-an-image"
        self.assertEqual(preprocess_image_for_ocr(bad), bad)


class QualityScoreTests(unittest.TestCase):
    """OCR 文本质量评分(多策略择优依据)。"""

    def test_good_text_scores_high(self):
        from app.parsers.region import text_quality_score
        score = text_quality_score("发票号码: INV-REAL-001\n发票日期: 2026-06-11\n发票金额: 560.00\n申请人: 张三")
        self.assertGreater(score, 0.85)

    def test_garbled_text_scores_low(self):
        from app.parsers.region import text_quality_score
        score = text_quality_score("WETS: 张三")
        self.assertLess(score, 0.85)

    def test_empty_text_scores_zero(self):
        from app.parsers.region import text_quality_score
        self.assertEqual(text_quality_score(""), 0.0)


class AdaptiveStrategyTests(unittest.TestCase):
    """质量自适应策略顺序:清晰图用原图,困难图用预处理。"""

    def _engine(self):
        return TesseractOcrEngine(exe="tesseract", preprocess=True, adaptive=True)

    def test_clear_image_prefers_raw(self):
        engine = self._engine()
        strategies = engine._strategies(make_text_image("发票金额：520.00元 申请人：张三"))
        self.assertEqual(strategies[0][0][:8], make_text_image("发票金额：520.00元 申请人：张三")[:8],
                         "清晰图首个策略应为原图")

    def test_hard_image_prefers_preprocessed(self):
        engine = self._engine()
        hard = make_image(300, 200)
        strategies = engine._strategies(hard)
        first_payload = strategies[0][0]
        self.assertNotEqual(first_payload, hard, "困难图首个策略应为预处理后的图")
        self.assertTrue(first_payload.startswith(b"\x89PNG"))

    def test_non_adaptive_engine_uses_single_strategy(self):
        engine = TesseractOcrEngine(exe="tesseract", adaptive=False)
        self.assertEqual(len(engine._strategies(make_image(300, 200))), 1)


class RoutingEngineTests(unittest.TestCase):
    def test_hard_material_prefers_high_accuracy_engine(self):
        light, high = FakeEngine("light"), FakeEngine("high")
        router = RoutingOcrEngine(light=light, high=high)
        result = router.recognize(make_image(300, 200))  # 低分辨率 -> 困难
        self.assertEqual(result, "high")
        self.assertEqual(light.calls, 0)
        self.assertEqual(high.calls, 1)

    def test_normal_material_uses_light_engine(self):
        light, high = FakeEngine("light"), FakeEngine("high")
        router = RoutingOcrEngine(light=light, high=high)
        result = router.recognize(make_text_image("发票金额：520.00元 申请人：张三"))
        self.assertEqual(result, "light")
        self.assertEqual(high.calls, 0)

    def test_degrades_to_fallback_when_engines_fail(self):
        router = RoutingOcrEngine(light=FailingEngine(), high=FailingEngine(),
                                  fallback=MockOcrEngine())
        result = router.recognize(make_image(1400, 900))
        self.assertIn("OCR-MOCK", result)

    def test_assessment_recorded_for_upper_layer(self):
        router = RoutingOcrEngine(light=FakeEngine(), high=FakeEngine())
        router.recognize(make_image(300, 200))
        self.assertIsNotNone(router.last_assessment)
        self.assertEqual(router.last_assessment.level, LEVEL_HARD)


class LoaderIntegrationTests(unittest.TestCase):
    def test_hardwriting_text_image_marks_document_type(self):
        """OCR 文本含手写关键词 -> document_type 带 _handwriting。"""
        engine = FakeEngine("发票金额：520.00元 申请人：张三 签字：张三")
        doc = parse_document("scan.jpg", make_text_image("签字"), ocr_engine=engine)
        self.assertEqual(doc.document_type, "image_ocr_handwriting")

    def test_low_quality_image_marks_hard(self):
        engine = FakeEngine("发票金额：520.00元 申请人：张三")
        doc = parse_document("photo.jpg", make_image(300, 200), ocr_engine=engine)
        self.assertEqual(doc.document_type, "image_ocr_hard")

    def test_normal_image_keeps_plain_document_type(self):
        engine = FakeEngine("发票金额：520.00元 开票日期：2026-05-12 申请人：张三")
        doc = parse_document("clear.jpg", make_text_image("发票金额：520.00元 申请人：张三"), ocr_engine=engine)
        self.assertEqual(doc.document_type, "image_ocr")


class EndToEndReviewTests(unittest.TestCase):
    def test_handwriting_material_raises_review_risk(self):
        """端到端:手写材料经审核后产生 HANDWRITING_REVIEW 风险与人工复核项。"""
        engine = FakeEngine("发票金额：520.00元 开票日期：2026-05-12 申请人：张三 签字：张三")
        with patch("app.parsers.loader.get_ocr_engine", return_value=engine):
            report = run_audit([("invoice.jpg", make_text_image("签字"))])
        risk_types = {r.risk_type for r in report.risks}
        self.assertIn("HANDWRITING_REVIEW", risk_types)
        self.assertTrue(any("签字" in "".join(t) or "困难" in "".join(t) for t in [report.trace]))

    def test_hard_material_raises_quality_risk(self):
        engine = FakeEngine("发票金额：520.00元 申请人：张三")
        with patch("app.parsers.loader.get_ocr_engine", return_value=engine):
            report = run_audit([("photo.jpg", make_image(300, 200))])
        risk_types = {r.risk_type for r in report.risks}
        self.assertIn("OCR_QUALITY_REVIEW", risk_types)

    def test_garbled_ocr_text_raises_quality_risk(self):
        """OCR 识别失败(乱码文本)-> OCR_QUALITY_REVIEW 人工复核。"""
        engine = FakeEngine("WETS: 张三")
        with patch("app.parsers.loader.get_ocr_engine", return_value=engine):
            report = run_audit([("scan.jpg", make_text_image("付款金额：560.00"))])
        self.assertIn("OCR_QUALITY_REVIEW", {r.risk_type for r in report.risks})

    def test_normal_image_no_extra_risk(self):
        engine = FakeEngine("发票金额：520.00元 开票日期：2026-05-12 申请人：张三 出差城市：杭州")
        with patch("app.parsers.loader.get_ocr_engine", return_value=engine):
            report = run_audit([("clear.jpg", make_text_image("发票金额：520.00元 申请人：张三"))])
        risk_types = {r.risk_type for r in report.risks}
        self.assertNotIn("HANDWRITING_REVIEW", risk_types)
        self.assertNotIn("OCR_QUALITY_REVIEW", risk_types)


TESSERACT_EXE = "C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSDATA_DIR = Path(__file__).resolve().parents[1] / "data" / "ocr_models" / "tessdata"


@unittest.skipUnless(
    Path(TESSERACT_EXE).exists() and (TESSDATA_DIR / "chi_sim.traineddata").exists(),
    "本机未安装 Tesseract 或缺少 chi_sim 中文语言包",
)
class RealTesseractIntegrationTests(unittest.TestCase):
    """真实 Tesseract 引擎集成(本机有中文环境时运行)。"""

    def _engine(self):
        return TesseractOcrEngine(exe=TESSERACT_EXE, lang="chi_sim+eng",
                                  tessdata_dir=str(TESSDATA_DIR), preprocess=True)

    def test_recognizes_chinese_fields(self):
        text = self._engine().recognize(
            make_text_image("发票金额：520.00元 申请人：张三 出差城市：杭州"))
        self.assertIn("发票", text)
        self.assertIn("520", text)
        self.assertIn("张三", text)

    def test_end_to_end_real_ocr_image_passes(self):
        """真实 OCR 图片材料端到端(票据版式):字段可抽取且不误报识别风险。"""
        engine = self._engine()
        files = [
            ("invoice.jpg", make_lines_image([
                "发票号码：INV-REAL-001", "发票日期：2026-06-11",
                "发票金额：560.00", "购买方：张三"])),
            ("payment.jpg", make_lines_image(["付款金额：560.00", "收款方：张三"])),
            ("approval.jpg", make_lines_image([
                "申请人：张三", "出差城市：北京",
                "出差开始：2026-06-10", "出差结束：2026-06-12"])),
        ]
        with patch("app.parsers.loader.get_ocr_engine", return_value=engine):
            report = run_audit(files)
        self.assertIn("invoice_amount", report.fields)
        self.assertIn("payment_amount", report.fields)
        self.assertIn("applicant_name", report.fields)
        self.assertNotIn("OCR_QUALITY_REVIEW", {r.risk_type for r in report.risks})


if __name__ == "__main__":
    unittest.main()
