"""百度增值税发票识别接入测试(OCR_ENGINE=baidu)。

用本地假服务分别扮演 token 接口与发票识别接口,断言的是真实请求编码、token 缓存/刷新、
以及按**用户提供的真实返回示例**做字段映射的结果(而不是编出来的响应)。

真实返回示例来自百度 aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice 的
words_result(号码 14640000 / 开票日期 2016年06月02日 / 价税合计 100000.00 / 购买方 百度时代…)。
"""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs

from PIL import Image

# 第三方库(pymilvus)在 import 时会 load_dotenv() 读走仓库 .env 里的真实引擎配置
# (venv 在仓库内);这里先钉住离线默认值,本模块内的 patch.dict 会按需覆盖。
os.environ["OCR_ENGINE"] = "mock"
os.environ["MODEL_PROVIDER"] = "mock"

from app.agent.graph import run_audit
from app.parsers.ocr import BaiduVatInvoiceOcrEngine, get_ocr_engine
from app.services.invoice_registry import isolate_registry

#: 用户提供的百度真实返回(节选关键字段)
BAIDU_SAMPLE = {
    "log_id": "5425496231209218858",
    "words_result_num": 36,
    "words_result": {
        "InvoiceNumDigit": "123456",
        "ServiceType": "其他",
        "InvoiceNum": "14640000",
        "InvoiceNumConfirm": "14640000",
        "SellerName": "示例销售方有限公司",
        "TotalAmount": "94339.62",
        "InvoiceDate": "2016年06月02日",
        "PurchaserName": "示例采购方有限公司",
        "Province": "上海",
        "City": "",
        "InvoiceCode": "3100000000",
        "InvoiceCodeConfirm": "3100000000",
        "AmountInWords": "壹拾万圆整",
        "AmountInFiguers": "100000.00",
        "TotalTax": "5660.38",
        "InvoiceType": "专用发票",
        "SellerRegisterNum": "91310114000000000X",
    },
}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        server = self.server
        self.server.requests.append({"path": self.path, "body": raw.decode("utf-8", "replace")})
        if "oauth" in self.path:
            server.token_calls += 1
            payload = {"access_token": f"token-{server.token_calls}", "expires_in": 2592000}
        else:
            server.invoice_calls += 1
            payload = server.invoice_responses[min(server.invoice_calls - 1,
                                                   len(server.invoice_responses) - 1)]
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 — 百度的 token 接口支持 GET
        self.do_POST()

    def log_message(self, *args):
        return


class FakeBaidu:
    """本地假百度:token 接口 + 发票识别接口。"""

    def __init__(self, invoice_responses: list[dict]):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.invoice_responses = invoice_responses
        self.server.requests = []
        self.server.token_calls = 0
        self.server.invoice_calls = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc_info):
        self.server.shutdown()
        self.server.server_close()


def make_image() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PNG")
    return buf.getvalue()


def baidu_env(base: str, **overrides) -> dict:
    env = {
        "OCR_ENGINE": "baidu",
        "BAIDU_OCR_API_KEY": "api-key-123",
        "BAIDU_OCR_SECRET_KEY": "secret-key-456",
        "BAIDU_OCR_URL": f"{base}/rest/2.0/ocr/v1/vat_invoice",
        "BAIDU_OCR_TOKEN_URL": f"{base}/oauth/2.0/token",
        "OCR_HTTP_TIMEOUT": "10",
    }
    env.update(overrides)
    return env


class BaiduEngineTests(unittest.TestCase):
    def test_sample_response_maps_to_project_fields(self):
        with FakeBaidu([BAIDU_SAMPLE]) as fake:
            with patch.dict(os.environ, baidu_env(fake.base)):
                engine = get_ocr_engine()
                text = engine.recognize(make_image())

        self.assertIsInstance(engine, BaiduVatInvoiceOcrEngine)
        self.assertEqual(engine.last_fields["invoice_code"], "3100000000")
        self.assertEqual(engine.last_fields["invoice_number"], "14640000")
        self.assertEqual(engine.last_fields["invoice_date"], "2016-06-02")
        # 价税合计(小写)作为发票金额,与付款凭证同口径
        self.assertEqual(engine.last_fields["invoice_amount"], "100000.00")
        self.assertEqual(engine.last_fields["invoice_buyer"], "示例采购方有限公司")
        # 票面 Province/City 不映射出差城市(票面地址 ≠ 出差城市)
        self.assertNotIn("travel_city", engine.last_fields)
        # 没配整段文本路径时,用字段拼一段可读文本
        self.assertIn("发票号码：14640000", text)

    def test_token_is_cached_across_calls(self):
        with FakeBaidu([BAIDU_SAMPLE, BAIDU_SAMPLE]) as fake:
            with patch.dict(os.environ, baidu_env(fake.base)):
                engine = get_ocr_engine()
                engine.recognize(make_image())
                engine.recognize(make_image())

        self.assertEqual(fake.server.token_calls, 1)     # 只换一次 token
        self.assertEqual(fake.server.invoice_calls, 2)

    def test_token_refreshed_when_baidu_reports_invalid_token(self):
        invalid = {"error_code": 110, "error_msg": "Access token invalid or no longer valid"}
        with FakeBaidu([invalid, BAIDU_SAMPLE]) as fake:
            with patch.dict(os.environ, baidu_env(fake.base)):
                engine = get_ocr_engine()
                fields = engine.extract_fields(engine.call(make_image()))

        self.assertEqual(fake.server.token_calls, 2)     # 第一次被拒后刷新
        self.assertEqual(fields["invoice_number"], "14640000")

    def test_other_error_codes_raise_with_message(self):
        with FakeBaidu([{"error_code": 216201, "error_msg": "image format error"}]) as fake:
            with patch.dict(os.environ, baidu_env(fake.base)):
                engine = get_ocr_engine()
                with self.assertRaisesRegex(RuntimeError, "216201.*image format error"):
                    engine.recognize(make_image())

    def test_missing_secret_key_gives_actionable_error(self):
        with FakeBaidu([BAIDU_SAMPLE]) as fake:
            env = baidu_env(fake.base)
            # 三个可能的 Secret 来源都清空(机器上 .env 可能已经配了真 key)
            env["BAIDU_OCR_SECRET_KEY"] = ""
            env["OCR_HTTP_API_KEY_SECRET"] = ""
            env["OCR_HTTP_SECRET_KEY"] = ""
            with patch.dict(os.environ, env):
                engine = get_ocr_engine()
                with self.assertRaisesRegex(RuntimeError, "BAIDU_OCR_SECRET_KEY"):
                    engine.recognize(make_image())

    def test_request_is_form_encoded_with_image_base64(self):
        with FakeBaidu([BAIDU_SAMPLE]) as fake:
            with patch.dict(os.environ, baidu_env(fake.base)):
                get_ocr_engine().recognize(make_image())

        invoice_requests = [r for r in fake.server.requests if "oauth" not in r["path"]]
        self.assertEqual(len(invoice_requests), 1)
        body = parse_qs(invoice_requests[0]["body"])
        self.assertIn("image", body)
        self.assertGreater(len(body["image"][0]), 100)          # base64 图片内容
        self.assertIn("access_token=token-1", invoice_requests[0]["path"])


class BaiduPipelineTests(unittest.TestCase):
    def setUp(self):
        isolate_registry()

    def test_audit_uses_baidu_fields_end_to_end(self):
        with FakeBaidu([BAIDU_SAMPLE]) as fake:
            with patch.dict(os.environ, baidu_env(fake.base)):
                report = run_audit([("发票扫描件.png", make_image())], task_id="task-baidu-e2e")

        self.assertNotEqual(report.status, "UNDETERMINED")
        self.assertEqual(report.fields["invoice_number"].value, "14640000")
        self.assertEqual(report.fields["invoice_amount"].value, "100000.00")
        self.assertEqual(report.fields["invoice_amount"].source_text,
                         "OCR结构化字段：invoice_amount=100000.00")
        self.assertTrue(any("ocr_structured_fields" in line for line in report.trace))

    def test_material_type_inferred_from_baidu_fields(self):
        """文件名是"发票扫描件.png":靠接口字段判出这是发票,而不是靠文件名。"""
        with FakeBaidu([BAIDU_SAMPLE]) as fake:
            with patch.dict(os.environ, baidu_env(fake.base)):
                report = run_audit([("发票扫描件.png", make_image())], task_id="task-baidu-type")

        material_line = [line for line in report.trace if line.startswith("material_type:")]
        self.assertTrue(material_line)
        self.assertIn("→发票", material_line[0])
        required = next(check for check in report.checks if check.name == "required_documents")
        self.assertNotIn("发票", required.detail)      # 不再把这张发票算成缺少发票
        self.assertIn("付款凭证", required.detail)


if __name__ == "__main__":
    unittest.main()
