"""MinerU 适配层测试:输出归一化 + 常驻服务参数 + 真实版式下的抽取修复。

背景(实测 2026-10-01,CPU 单张发票):
- MinerU 的 markdown+HTML 表格会切断"标签—值"(`价税合计(大写)</td><td ...>玖佰元整 (小写)¥900.00`),
  直接喂规则抽取器会漏抽金额;
- 票面底部签章栏"收款人：/复核:/开票人：前台"会被当成收款方,抽成 `payment_party="复核"`;
- 日期输出是 `2019年02月19日`,下游比较需要 ISO 形态。

测试用的 markdown 是**仿造的真实版式**(数据全是虚构的),避免把真实发票内容放进仓库。
"""
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app.extraction.field_extractor import extract_fields
from app.models.document import Document, Page
from app.parsers.ocr import MineruOcrEngine, normalize_mineru_text

#: 仿真实 MinerU 输出:图片占位 + 标题 + 表格(含 rowspan/colspan 与 HTML 实体)+ 签章栏 + 印章 details
MINERU_MARKDOWN = """![](images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg)

## 北京增值税电子普通发票

![](images/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg)

<details>
<summary>seal</summary>

某某税务局
发票专用章
</details>

发票代码：110000000000

发票号码: 12345678

开票日期：2026年03月05日

校验码：12345678901234567890

<table><tr><td rowspan=1 colspan=1>购买方</td><td rowspan=1 colspan=5>名    称：示例科技有限公司纳税人识别号：110000000000000000地址、电话：</td><td rowspan=1 colspan=1>密码区</td><td rowspan=1 colspan=4>&lt;0/3&lt;&gt;5-0--71531**2-67*75&gt;3&gt;3&gt;37-50348&gt;2&gt;8+*0**+-4/39+7&amp;1*-8906</td></tr><tr><td rowspan=1 colspan=2>货物或应税劳务、服务名称*住宿服务*住宿费合计</td><td rowspan=1 colspan=1>规格型号</td><td rowspan=1 colspan=1>单位</td><td rowspan=1 colspan=1>数量2</td><td rowspan=1 colspan=3>单价471.698113</td><td rowspan=1 colspan=1>金额943.40¥943.40</td><td rowspan=1 colspan=1>税率6%</td><td rowspan=1 colspan=1>税额56.60¥56.60</td></tr><tr><td rowspan=1 colspan=2>价税合计(大写)</td><td rowspan=1 colspan=9>壹仟元整                          (小写)¥1000.00</td></tr><tr><td rowspan=1 colspan=1>销售方</td><td rowspan=1 colspan=5>名    称：示例开票方有限公司纳税人识别号：220000000000000000</td><td rowspan=1 colspan=1>备注</td><td rowspan=1 colspan=4></td></tr></table>

收款人：
复核:
开票人：前台
"""


def mineru_document(text: str | None = None) -> Document:
    return Document(document_id="d", file_name="发票.png", document_type="image_ocr",
                    pages=[Page(number=1, text=text if text is not None else normalize_mineru_text(MINERU_MARKDOWN))])


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        self.text = normalize_mineru_text(MINERU_MARKDOWN)

    def test_no_html_left(self):
        self.assertNotIn("<td", self.text)
        self.assertNotIn("</", self.text)
        self.assertNotIn("rowspan", self.text)

    def test_entities_decoded(self):
        self.assertNotIn("&lt;", self.text)
        self.assertNotIn("&amp;", self.text)
        self.assertIn("<0/3<>5-0--71531", self.text)     # 密码区还原成可读符号

    def test_image_placeholders_dropped(self):
        self.assertNotIn("images/", self.text)

    def test_markdown_heading_marker_removed(self):
        self.assertIn("北京增值税电子普通发票", self.text)
        self.assertNotIn("##", self.text)

    def test_table_cells_are_on_their_own_lines(self):
        lines = self.text.splitlines()
        self.assertIn("价税合计(大写)", lines)            # 标签独立成行
        self.assertTrue(any("(小写)¥1000.00" in line for line in lines))
        # 签章栏三行都在
        self.assertTrue(any(line.startswith("收款人") for line in lines))
        self.assertTrue(any(line.startswith("开票人") for line in lines))

    def test_tax_id_line_not_broken_by_label(self):
        """销售方/购买方名称与税号同一格时,内容必须保留完整。"""
        self.assertIn("示例科技有限公司", self.text)
        self.assertIn("110000000000000000", self.text)

    def test_empty_input(self):
        self.assertEqual(normalize_mineru_text(""), "")


class ExtractionOnMineruTextTests(unittest.TestCase):
    """归一化之后的文本,规则抽取器必须能拿到关键字段。"""

    def setUp(self):
        self.fields = extract_fields([mineru_document()])

    def test_amount_extracted_from_table_cells(self):
        self.assertEqual(self.fields["invoice_amount"].value, "1000.00")

    def test_number_and_code(self):
        self.assertEqual(self.fields["invoice_number"].value, "12345678")
        self.assertEqual(self.fields["invoice_code"].value, "110000000000")
        self.assertEqual(self.fields["invoice_date"].value, "2026-03-05")

    def test_date_normalized_to_iso(self):
        self.assertEqual(self.fields["invoice_date"].value, "2026-03-05")
        self.assertNotIn("年", self.fields["invoice_date"].value)

    def test_signature_row_is_not_taken_as_payee(self):
        self.assertNotIn("payment_party", self.fields)

    def test_buyer_kept(self):
        self.assertIn("示例科技有限公司", self.fields["invoice_buyer"].value)

    def test_real_payee_still_extracted(self):
        """真正的收款方不能被误伤(payment_party 的排除只针对签章栏)。"""
        text = "付款凭证\n付款金额：1000.00元\n收款方：示例收款有限公司\n"
        fields = extract_fields([mineru_document(text)])
        self.assertEqual(fields["payment_party"].value, "示例收款有限公司")

    def test_raw_markdown_without_normalization_would_fail(self):
        """反例:不做归一化就抽不到金额 —— 说明这层适配是必需的。"""
        fields = extract_fields([mineru_document(MINERU_MARKDOWN)])
        self.assertNotIn("invoice_amount", fields)


class EngineCommandTests(unittest.TestCase):
    """引擎要把常驻服务地址传给 mineru CLI(否则每次自起临时服务,约 100 秒/张)。"""

    def _run_engine(self, **kwargs):
        captured = {}

        def fake_run(command, **run_kwargs):
            captured["command"] = command
            out_dir = Path(command[command.index("-o") + 1])
            target = out_dir / "发票" / "auto" / "发票.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(MINERU_MARKDOWN, encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        engine = MineruOcrEngine(exe=__file__, cache_dir=".", **kwargs)   # exe 只需存在
        with patch("app.parsers.ocr.subprocess.run", side_effect=fake_run):
            text = engine.recognize(b"\x89PNG fake image")
        return captured["command"], text

    def test_api_url_passed_when_configured(self):
        with patch("app.parsers.ocr.socket.create_connection"):
            command, text = self._run_engine(api_url="http://127.0.0.1:8321")

        self.assertIn("--api-url", command)
        self.assertIn("http://127.0.0.1:8321", command)
        self.assertNotIn("<td", text)            # 返回值已归一化

    def test_api_url_dropped_when_service_down(self):
        """常驻服务没起来时不能传 --api-url:否则 CLI 直接失败,审核变 FAILED。"""
        with patch("app.parsers.ocr.socket.create_connection", side_effect=OSError("refused")):
            command, text = self._run_engine(api_url="http://127.0.0.1:8321")

        self.assertNotIn("--api-url", command)
        self.assertIn("发票号码", text)          # 仍然正常产出文本(只是慢) 

    def test_api_url_omitted_by_default(self):
        command, _ = self._run_engine()

        self.assertNotIn("--api-url", command)

    def test_backend_overridable(self):
        command, _ = self._run_engine(backend="hybrid-engine")

        self.assertIn("hybrid-engine", command)

    def test_factory_reads_env(self):
        from app.parsers.ocr import _build_mineru_engine

        with patch.dict(os.environ, {"MINERU_API_URL": "http://127.0.0.1:9999",
                                     "MINERU_BACKEND": "pipeline"}):
            engine = _build_mineru_engine()

        self.assertEqual(engine.api_url, "http://127.0.0.1:9999")
        self.assertEqual(engine.backend, "pipeline")


if __name__ == "__main__":
    unittest.main()
