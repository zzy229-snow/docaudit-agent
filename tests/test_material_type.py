"""材料类型判定测试:按票面内容判类型,文件名只做兜底。

起因:客户上传的文件名常是「微信图片_2026.png」,只按文件名找关键词会把识别出
发票号码的发票算成"缺少发票"。
"""
import unittest
from unittest.mock import patch

from app.agent.graph import run_audit
from app.services.invoice_registry import isolate_registry
from app.services.material_type import (
    classify_material,
    content_scores,
    filename_type,
    material_check_inputs,
)
from app.tools.document_tool import check_required_documents

#: 一段票面文本(发票)
INVOICE_TEXT = (
    "北京增值税电子普通发票\n"
    "发票代码：110022003300\n"
    "发票号码：12345678\n"
    "开票日期：2019年02月19日\n"
    "价税合计(小写)¥900.00\n"
    "校验码：67355772754612372792\n"
)
PAYMENT_TEXT = "付款凭证\n付款金额：900.00元\n付款时间：2019-02-20\n流水号：PAY-2026-0001\n转账"
APPROVAL_TEXT = "出差审批单\n审批人：李经理\n审批意见：同意\n报销申请：住宿费900元"


class ClassifyTests(unittest.TestCase):
    def test_filename_fallback_when_content_is_silent(self):
        self.assertEqual(classify_material("invoice.txt", "随意的一句话"), "invoice")
        self.assertEqual(classify_material("payment_2026.txt", ""), "payment")
        self.assertEqual(classify_material("approval.pdf", "内容"), "approval")
        self.assertEqual(classify_material("微信图片_2026.png", "内容"), "other")

    def test_invoice_content_beats_generic_filename(self):
        """客户场景:文件名是"微信图片_xxx",但票面明显是发票。"""
        self.assertEqual(classify_material("微信图片_2026.png", INVOICE_TEXT), "invoice")

    def test_content_beats_misleading_filename(self):
        """文件名写 payment,内容却是发票 —— 以内容为准。"""
        self.assertEqual(classify_material("payment.txt", INVOICE_TEXT), "invoice")

    def test_payment_and_approval_content(self):
        self.assertEqual(classify_material("扫描件001.pdf", PAYMENT_TEXT), "payment")
        self.assertEqual(classify_material("扫描件002.pdf", APPROVAL_TEXT), "approval")

    def test_ambiguous_content_falls_back_to_filename(self):
        """同一份材料同时像发票又像付款凭证(演示语料就是这样)→ 不硬判,回退文件名。"""
        mixed = INVOICE_TEXT + "\n" + PAYMENT_TEXT
        self.assertEqual(classify_material("invoice.jpg", mixed), "invoice")
        self.assertEqual(classify_material("扫描件.png", mixed), "other")

    def test_structured_ocr_fields_count_as_content(self):
        fields = {"invoice_number": "12345678", "invoice_amount": "900.00"}
        self.assertEqual(classify_material("微信图片.png", "", fields), "invoice")
        self.assertEqual(content_scores("", fields)["invoice"], 2)

    def test_threshold_is_configurable(self):
        with patch.dict("os.environ", {"MATERIAL_MIN_CONTENT_SIGNALS": "1"}):
            # 只命中一个信号时,阈值降到 1 也算决定性
            self.assertEqual(classify_material("x.png", "发票号码：12345678"), "invoice")

    def test_material_check_inputs_split_content_and_fallback(self):
        docs = [
            _doc("微信图片_2026.png", INVOICE_TEXT),      # 内容判得出 → types
            _doc("扫描件A.pdf", PAYMENT_TEXT),            # 内容判得出 → types
            _doc("unclear.txt", "看不懂的内容"),           # 判不出 → 文件名兜底
        ]
        names, types = material_check_inputs(docs)

        self.assertEqual(types, ["invoice", "payment"])
        self.assertEqual(names, ["unclear.txt"])

    def test_filename_type_direct(self):
        self.assertEqual(filename_type("INVOICE_2026.PDF"), "invoice")
        self.assertIsNone(filename_type("微信图片_2026.png"))


class AmountLabelTests(unittest.TestCase):
    """真实发票版面上的"价税合计"写法要能抽出金额(实机踩过)。"""

    def test_price_tax_total_with_words_parsed(self):
        from app.extraction.field_extractor import extract_fields
        from app.models.document import Document, Page

        text = ("北京增值税电子普通发票\n发票代码：110022003300\n发票号码：12345678\n"
                "开票日期：2019年02月19日\n"
                "价税合计(大写) 玖佰元整 (小写)¥900.00\n")
        doc = Document(document_id="d", file_name="发票.png", document_type="text",
                       pages=[Page(number=1, text=text)])

        fields = extract_fields([doc])

        self.assertEqual(fields["invoice_amount"].value, "900.00")
        self.assertEqual(fields["invoice_number"].value, "12345678")

    def test_label_does_not_cross_into_next_amount(self):
        """大写金额后面直接跟下一行数字时,不跨行乱取(宁可不给值)。"""
        from app.extraction.field_extractor import extract_fields
        from app.models.document import Document, Page

        text = "价税合计(大写)壹拾万圆整\n合计 94339.62 税额 5660.38"
        doc = Document(document_id="d", file_name="发票.png", document_type="text",
                       pages=[Page(number=1, text=text)])

        self.assertNotIn("invoice_amount", extract_fields([doc]))

    def test_cross_line_amount_recognized_when_marked_xiaoxie(self):
        """OCR 把小写金额排到下一行时,只要有"小写"标记就认。"""
        from app.extraction.field_extractor import extract_fields
        from app.models.document import Document, Page

        text = "价税合计(大写)壹拾万圆整\n(小写) ￥100000.00"
        doc = Document(document_id="d", file_name="发票.png", document_type="text",
                       pages=[Page(number=1, text=text)])

        self.assertEqual(extract_fields([doc])["invoice_amount"].value, "100000.00")


class RequiredDocumentsTests(unittest.TestCase):
    def test_content_types_satisfy_required_documents(self):
        result = check_required_documents(names=[], material_types=["invoice", "payment", "approval"])
        self.assertTrue(result.passed)
        self.assertEqual(result.detail, "材料齐全")

    def test_missing_type_reported(self):
        result = check_required_documents(names=[], material_types=["invoice", "approval"])
        self.assertFalse(result.passed)
        self.assertIn("付款凭证", result.detail)

    def test_filename_keywords_still_work_as_fallback(self):
        result = check_required_documents(names=["invoice.txt", "payment.txt", "approval.txt"])
        self.assertTrue(result.passed)

    def test_chinese_filenames_match_labels(self):
        result = check_required_documents(names=["发票.png", "付款凭证.png", "审批单.png"])
        self.assertTrue(result.passed)


class EndToEndMaterialTypeTests(unittest.TestCase):
    """整链路:文件全是"微信图片"这类名字,靠内容判类型。"""

    def setUp(self):
        isolate_registry()

    def test_content_based_typing_keeps_materials_complete(self):
        files = [("微信图片_001.txt", INVOICE_TEXT.encode("utf-8")),
                 ("微信图片_002.txt", PAYMENT_TEXT.encode("utf-8")),
                 ("微信图片_003.txt", APPROVAL_TEXT.encode("utf-8"))]

        report = run_audit(files, task_id="task-content-type")

        required = next(check for check in report.checks if check.name == "required_documents")
        self.assertTrue(required.passed, required.detail)
        self.assertEqual(required.detail, "材料齐全")
        self.assertTrue(any(line.startswith("material_type:") for line in report.trace))

    def test_missing_payment_detected_by_content_not_filename(self):
        files = [("微信图片_001.txt", INVOICE_TEXT.encode("utf-8")),
                 ("微信图片_003.txt", APPROVAL_TEXT.encode("utf-8"))]

        report = run_audit(files, task_id="task-content-type-missing")

        required = next(check for check in report.checks if check.name == "required_documents")
        self.assertFalse(required.passed)
        self.assertIn("付款凭证", required.detail)
        self.assertTrue(any(risk.risk_type == "REQUIRED_DOCUMENTS" for risk in report.risks))


def _doc(file_name: str, text: str):
    from app.models.document import Document, Page
    return Document(document_id="d", file_name=file_name, document_type="text",
                    pages=[Page(number=1, text=text)])


if __name__ == "__main__":
    unittest.main()
