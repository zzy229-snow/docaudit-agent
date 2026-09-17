"""LLM 字段抽取测试:LLM 补缺 / 正则优先 / 失败降级。"""

import unittest

from app.extraction.field_extractor import extract_fields
from app.models.document import Document, Page


class FakeGateway:
    def __init__(self, payload=None, raise_on_call=False, available=True):
        self._payload = payload or {}
        self._raise = raise_on_call
        self._available = available
        self.instruction = None
        self.content = None

    def available(self) -> bool:
        return self._available

    def complete_json(self, instruction, content, schema):
        self.instruction = instruction
        self.content = content
        if self._raise:
            raise RuntimeError("simulated LLM outage")
        return self._payload


def make_doc(text: str, file_name: str = "invoice.txt", document_id: str = "d1",
             page_no: int = 1) -> Document:
    return Document(document_id=document_id, file_name=file_name,
                    document_type="invoice", pages=[Page(number=page_no, text=text)])


UNLABELED_TEXT = (
    "出差申请:8月1日 至 8月2日 赴 上海 参加行业会议,"
    "住宿费人民币陆佰捌拾元整,发票号 TEST-2026-00099,申请人 李四,"
    "购买方 李四,收款方 李四。"
)
LLM_PAYLOAD = {
    "invoice_amount": {"value": "680.00", "page": 1, "evidence": "陆佰捌拾元整"},
    "payment_amount": {"value": "680.00", "page": 1, "evidence": "付款 680.00"},
    "invoice_date": {"value": "2026-08-01", "page": 1, "evidence": "开票 2026-08-01"},
    "travel_start_date": {"value": "2026-08-01", "page": 1, "evidence": "8月1日"},
    "travel_end_date": {"value": "2026-08-02", "page": 1, "evidence": "8月2日"},
    "travel_city": {"value": "上海", "page": 1, "evidence": "赴 上海"},
    "invoice_number": {"value": "TEST-2026-00099", "page": 1, "evidence": "发票号 TEST-2026-00099"},
    "applicant_name": {"value": "李四", "page": 1, "evidence": "申请人 李四"},
    "invoice_buyer": {"value": "李四", "page": 1, "evidence": "购买方 李四"},
    "payment_party": {"value": "李四", "page": 1, "evidence": "收款方 李四"},
}


class LlmExtractionTests(unittest.TestCase):

    def test_llm_fills_fields_regex_cannot_find(self):
        """无标签文本:正则抽不到,LLM 补全全部 8 个字段,置信度 0.85。"""
        gw = FakeGateway(payload=LLM_PAYLOAD)
        docs = [make_doc(UNLABELED_TEXT)]
        fields = extract_fields(docs, gateway=gw)
        self.assertEqual(len(fields), 10)
        self.assertEqual(fields["invoice_amount"].value, "680.00")
        self.assertEqual(fields["invoice_amount"].confidence, 0.85)
        self.assertEqual(fields["travel_city"].value, "上海")
        self.assertEqual(fields["applicant_name"].value, "李四")
        self.assertEqual(fields["invoice_buyer"].value, "李四")
        self.assertEqual(fields["payment_party"].value, "李四")
        self.assertEqual(fields["invoice_date"].source_text, "开票 2026-08-01")

    def test_regex_wins_over_llm_on_conflict(self):
        """同名字段:正则结果(置信度1.0)优先,LLM 不覆盖。"""
        gw = FakeGateway(payload=LLM_PAYLOAD)
        labeled = "发票金额：1000.00\n发票日期：2026-08-01"
        docs = [make_doc(labeled)]
        fields = extract_fields(docs, gateway=gw)
        self.assertEqual(fields["invoice_amount"].value, "1000.00")
        self.assertEqual(fields["invoice_amount"].confidence, 1.0)
        self.assertEqual(fields["invoice_amount"].source_text, "发票金额：1000.00")

    def test_llm_missing_value_not_adopted(self):
        """LLM 对某字段返回 null:不采纳,null 字段保持缺失。"""
        payload = dict(LLM_PAYLOAD)
        payload["travel_city"] = {"value": None, "page": 1, "evidence": None}
        gw = FakeGateway(payload=payload)
        docs = [make_doc(UNLABELED_TEXT)]
        fields = extract_fields(docs, gateway=gw)
        self.assertNotIn("travel_city", fields)
        self.assertEqual(len(fields), 9)

    def test_gateway_failure_degrades_to_regex(self):
        """LLM 调用抛错:降级纯正则,不中断,结果与正则基线一致。"""
        gw = FakeGateway(payload=LLM_PAYLOAD, raise_on_call=True)
        docs = [make_doc("发票金额：680.00\n发票日期：2026-08-01")]
        fields = extract_fields(docs, gateway=gw)
        self.assertEqual(fields["invoice_amount"].value, "680.00")
        self.assertNotIn("travel_city", fields)

    def test_unavailable_gateway_skips_llm(self):
        """网关未配置(available=False):直接走正则,不发起调用。"""
        gw = FakeGateway(available=False)
        docs = [make_doc("发票金额：680.00")]
        fields = extract_fields(docs, gateway=gw)
        self.assertEqual(fields["invoice_amount"].value, "680.00")
        self.assertIsNone(gw.instruction)

    def test_prompt_anchors_document_and_page(self):
        """传参内容包含文档标识与页码,便于 LLM 定位证据。"""
        gw = FakeGateway(payload=LLM_PAYLOAD)
        docs = [make_doc(UNLABELED_TEXT, file_name="hotel.txt", document_id="d9")]
        extract_fields(docs, gateway=gw)
        self.assertIn("hotel.txt", gw.content)
        self.assertIn("第1页", gw.content)
        self.assertIn("不可信", gw.instruction)


if __name__ == "__main__":
    unittest.main()
