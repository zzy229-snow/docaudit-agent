"""发票专用 OCR(OCR_ENGINE=http)结构化字段通道测试。

用一个真实的本地 HTTP 服务(127.0.0.1 随机端口)当假 OCR 接口,所以这里测的是
真实的请求编码/响应取值/字段规范化/优先级,而不是 mock 出来的调用。

覆盖:
- 值规范化:日期(2019年02月19日 → 2019-02-19)、金额(¥1,280.00元 → 1280.00)、号码去分隔符;
- 配置解析:OCR_HTTP_FIELD_MAP / OCR_HTTP_HEADERS 的合法与非法 JSON;
- 文本模式(接口返回整段文本)与结构化模式(接口只返回字段)都能跑通;
- 结构化字段优先于正则抽取,并在 trace 里可追溯;值冲突时采用接口值且写明冲突;
- 接口结构化字段存在时,不会因"文本不足"被判成"无法判定";
- 引擎字段不跨文件泄漏。
"""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from app.agent.graph import run_audit
from app.parsers.loader import parse_document
from app.parsers.ocr import (
    HttpOcrEngine,
    get_ocr_engine,
    normalize_ocr_value,
    parse_field_map,
    parse_headers,
    text_from_fields,
)
from app.services.invoice_registry import isolate_registry

#: 这张"发票"接口返回的字段(用 2019 年那张测试票的值,便于对照)
INVOICE_FIELDS = {
    "InvoiceNum": "1234 5678",
    "InvoiceDate": "2019年02月19日",
    "TotalAmount": "¥900.00",
    "BuyerName": "示例科技有限公司",
}
INVOICE_FIELD_MAP = {
    "invoice_number": "InvoiceNum",
    "invoice_date": "InvoiceDate",
    "invoice_amount": "TotalAmount",
    "invoice_buyer": "BuyerName",
}


def make_image() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    return buf.getvalue()


class _FakeOcrHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        self.server.requests.append({"body": json.loads(raw), "headers": dict(self.headers)})
        payload = json.dumps(self.server.response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静音
        return


class FakeOcrServer:
    """本地假 OCR 服务:按需返回固定 JSON,并记录收到的请求。"""

    def __init__(self, response: dict):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOcrHandler)
        self.server.response = response
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/ocr"

    @property
    def requests(self) -> list[dict]:
        return self.server.requests

    def __enter__(self) -> "FakeOcrServer":
        self.thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.server.shutdown()
        self.server.server_close()


def http_env(url: str, **overrides) -> dict:
    env = {
        "OCR_ENGINE": "http",
        "OCR_HTTP_URL": url,
        "OCR_HTTP_TIMEOUT": "10",
        "OCR_HTTP_TEXT_PATH": "",
    }
    env.update(overrides)
    return env


class NormalizationTests(unittest.TestCase):
    def test_dates(self):
        for raw, expected in (("2019年02月19日", "2019-02-19"), ("2019/2/19", "2019-02-19"),
                              ("2019-02-19 00:00:00", "2019-02-19"), ("2019.2.19", "2019-02-19")):
            self.assertEqual(normalize_ocr_value("invoice_date", raw), expected, raw)

    def test_amounts(self):
        for raw, expected in (("¥900.00", "900.00"), ("￥1,280.00元", "1280.00"),
                              ("849.06", "849.06"), (900, "900.00"), ("-94.34", "-94.34")):
            self.assertEqual(normalize_ocr_value("invoice_amount", raw), expected, raw)

    def test_numbers_keep_alnum_only(self):
        self.assertEqual(normalize_ocr_value("invoice_number", "1234 5678"), "12345678")
        self.assertEqual(normalize_ocr_value("invoice_code", "1100-2200-3300"), "110022003300")

    def test_names_and_city_strip_whitespace(self):
        self.assertEqual(normalize_ocr_value("invoice_buyer", " 示例科技有限公司 "), "示例科技有限公司")
        self.assertEqual(normalize_ocr_value("travel_city", "杭 州"), "杭 州")

    def test_nested_and_empty_values(self):
        self.assertEqual(normalize_ocr_value("invoice_amount", {"value": "¥900.00"}), "900.00")
        self.assertEqual(normalize_ocr_value("invoice_number", {"word": "12345678"}), "12345678")
        self.assertEqual(normalize_ocr_value("invoice_number", {}), "")
        self.assertEqual(normalize_ocr_value("invoice_number", None), "")
        self.assertEqual(normalize_ocr_value("invoice_amount", "无"), "")

    def test_text_from_fields_uses_chinese_labels(self):
        text = text_from_fields({"invoice_number": "12345678", "invoice_amount": "900.00"})
        self.assertIn("发票号码：12345678", text)
        self.assertIn("发票金额：900.00", text)

    def test_map_and_header_parsing(self):
        self.assertEqual(parse_field_map('{"invoice_number": "InvoiceNum"}'), {"invoice_number": "InvoiceNum"})
        self.assertEqual(parse_field_map(""), {})
        self.assertEqual(parse_headers('{"X-Api-Key": "abc"}'), {"X-Api-Key": "abc"})
        self.assertEqual(parse_headers(None), {})
        with self.assertRaisesRegex(RuntimeError, "不是合法 JSON"):
            parse_field_map("{not json}")
        with self.assertRaisesRegex(RuntimeError, "必须是 JSON 对象"):
            parse_field_map('["a"]')


class HttpEngineTests(unittest.TestCase):
    def test_structured_mode_builds_text_from_fields_and_records_them(self):
        with FakeOcrServer({"words_result": INVOICE_FIELDS}) as server:
            with patch.dict(os.environ, http_env(server.url, OCR_HTTP_FIELDS_PATH="words_result",
                                                 OCR_HTTP_FIELD_MAP=json.dumps(INVOICE_FIELD_MAP),
                                                 OCR_HTTP_API_KEY="test-key")):
                engine = get_ocr_engine()
                text = engine.recognize(make_image())

        self.assertIsInstance(engine, HttpOcrEngine)
        self.assertIn("发票号码：12345678", text)
        self.assertNotIn("【OCR-MOCK】", text)
        self.assertEqual(engine.last_fields["invoice_date"], "2019-02-19")
        self.assertEqual(engine.last_fields["invoice_amount"], "900.00")
        # 请求体带 base64 与 key
        request = server.requests[-1]
        self.assertIn("file_base64", request["body"])
        self.assertEqual(request["headers"].get("Authorization"), "Bearer test-key")

    def test_text_mode_still_supported(self):
        response = {"data": {"text": "发票金额：520.00元 付款金额：520.00元 开票日期：2026-05-12 申请人：张三"}}
        with FakeOcrServer(response) as server:
            with patch.dict(os.environ, http_env(server.url, OCR_HTTP_TEXT_PATH="data.text")):
                doc = parse_document("invoice.png", make_image())

        self.assertIn("520.00", doc.text)

    def test_missing_text_and_fields_raises_clear_error(self):
        with FakeOcrServer({"result": {"message": "ok"}}) as server:
            with patch.dict(os.environ, http_env(server.url)):
                engine = get_ocr_engine()
                with self.assertRaisesRegex(RuntimeError, "OCR_HTTP_FIELDS_PATH"):
                    engine.recognize(make_image())

    def test_extra_headers_are_sent(self):
        with FakeOcrServer({"data": {"text": "发票金额：520.00元"}}) as server:
            with patch.dict(os.environ, http_env(server.url, OCR_HTTP_TEXT_PATH="data.text",
                                                 OCR_HTTP_HEADERS='{"X-Api-Key": "abc"}')):
                get_ocr_engine().recognize(make_image())

        self.assertEqual(server.requests[-1]["headers"].get("X-Api-Key"), "abc")

    def test_last_fields_do_not_leak_across_documents(self):
        with FakeOcrServer({"words_result": INVOICE_FIELDS}) as server:
            with patch.dict(os.environ, http_env(server.url, OCR_HTTP_FIELDS_PATH="words_result",
                                                 OCR_HTTP_FIELD_MAP=json.dumps(INVOICE_FIELD_MAP))):
                engine = get_ocr_engine()
                sink: dict[str, str] = {}
                parse_document("invoice.png", make_image(), ocr_engine=engine, field_sink=sink)
                self.assertIn("invoice_number", sink)
                self.assertEqual(engine.last_fields, {})   # 取走即清空,不跨文件复用


class StructuredPipelineTests(unittest.TestCase):
    """端到端:发票接口给的是结构化字段,审核链路必须用上它们。"""

    def setUp(self):
        isolate_registry()

    def test_structured_fields_drive_the_audit(self):
        with FakeOcrServer({"words_result": INVOICE_FIELDS}) as server:
            with patch.dict(os.environ, http_env(server.url, OCR_HTTP_FIELDS_PATH="words_result",
                                                 OCR_HTTP_FIELD_MAP=json.dumps(INVOICE_FIELD_MAP))):
                report = run_audit([("invoice.png", make_image())], task_id="task-http-ocr")

        self.assertNotEqual(report.status, "UNDETERMINED")     # 材料被真识别了
        self.assertEqual(report.fields["invoice_number"].value, "12345678")
        self.assertEqual(report.fields["invoice_date"].value, "2019-02-19")
        self.assertEqual(report.fields["invoice_amount"].value, "900.00")
        self.assertEqual(report.fields["invoice_amount"].document_id, "ocr-api")
        self.assertTrue(any("ocr_structured_fields" in line for line in report.trace))

    def test_api_value_wins_and_conflict_is_recorded(self):
        """接口字段与版面正则冲突时采用接口值,并把冲突写进 trace(可追溯)。"""
        response = {"words_result": INVOICE_FIELDS, "text": "发票号码：99999999 发票金额：111.00"}
        with FakeOcrServer(response) as server:
            with patch.dict(os.environ, http_env(server.url, OCR_HTTP_TEXT_PATH="text",
                                                 OCR_HTTP_FIELDS_PATH="words_result",
                                                 OCR_HTTP_FIELD_MAP=json.dumps(INVOICE_FIELD_MAP))):
                report = run_audit([("invoice.png", make_image())], task_id="task-http-ocr-conflict")

        self.assertEqual(report.fields["invoice_number"].value, "12345678")
        self.assertEqual(report.fields["invoice_amount"].value, "900.00")
        conflict_lines = [line for line in report.trace if "ocr_structured_fields_conflict" in line]
        self.assertTrue(conflict_lines)
        self.assertIn("invoice_number", conflict_lines[0])

    def test_manual_correction_still_beats_api_fields(self):
        with FakeOcrServer({"words_result": INVOICE_FIELDS}) as server:
            with patch.dict(os.environ, http_env(server.url, OCR_HTTP_FIELDS_PATH="words_result",
                                                 OCR_HTTP_FIELD_MAP=json.dumps(INVOICE_FIELD_MAP))):
                report = run_audit(
                    [("invoice.png", make_image())],
                    task_id="task-http-ocr-manual",
                    field_overrides={"invoice_amount": {"value": "888.00", "reason": "按发票原件核对"}},
                )

        self.assertEqual(report.fields["invoice_amount"].value, "888.00")