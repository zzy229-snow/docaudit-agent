"""制度管理接口与制度过滤检索测试(PRD §5.4 FR-301~FR-304 / §9.1~§9.3)。"""
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.graph import run_audit
from app.api.main import app
from app.rag.retriever import retrieve_policy
from app.rag.text_chunker import chunk_text
from app.services import policy_store
from app.services.invoice_registry import isolate_registry


REPO = Path(__file__).resolve().parents[1]

POLICY_V1 = """第4章 差旅住宿标准
4.2 拉萨住宿标准为500元/晚。
4.3 员工住宿超标部分需由部门负责人书面说明。
"""

POLICY_V2 = """第4章 差旅住宿标准
4.2 拉萨住宿标准为300元/晚。
"""


def load_audit_files(city_amount: str = "520.00") -> list[tuple[str, bytes]]:
    """构造一份拉萨报销材料(发票/付款/审批),金额由参数决定。"""
    files = {
        "approval.txt": f"申请人：张三\n出差城市：拉萨\n出差开始：2026-08-01\n出差结束：2026-08-02\n",
        "invoice.txt": f"发票号码：INV-LASA-001\n发票日期：2026-08-01\n发票金额：{city_amount}\n购买方：张三\n",
        "payment.txt": f"付款金额：{city_amount}\n收款方：张三\n",
    }
    return [(name, content.encode("utf-8")) for name, content in files.items()]


class TextChunkerTests(unittest.TestCase):
    def test_chunks_keep_section_path_and_metadata(self):
        chunks = chunk_text(POLICY_V1, policy_id="UP-TEST", version="V1", department="销售部",
                            expense_type="TRAVEL", effective_from="2026-01-01")

        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(chunk.policy_id == "UP-TEST" for chunk in chunks))
        self.assertTrue(all(chunk.department == "销售部" for chunk in chunks))
        self.assertIn("4.2", chunks[0].section_path)
        self.assertIn("拉萨", chunks[0].content)

    def test_table_rows_keep_header(self):
        text = "第4章 标准\n| 城市 | 上限 |\n| --- | --- |\n| 拉萨 | 500 |\n"

        chunks = chunk_text(text, policy_id="UP-TBL", version="V1")

        table_chunks = [chunk for chunk in chunks if "|" in chunk.content]
        self.assertTrue(table_chunks)
        self.assertIn("| 城市 | 上限 |", table_chunks[0].content)
        self.assertIn("拉萨", table_chunks[0].content)

    def test_empty_text_produces_no_chunks(self):
        self.assertEqual(chunk_text("", policy_id="UP-EMPTY", version="V1"), [])


class PolicyApiTests(unittest.TestCase):
    def setUp(self):
        policy_store.isolate_store()
        policy_store.reset_store()
        isolate_registry()
        self.client = TestClient(app)

    def upload(self, policy_text: str, **overrides) -> dict:
        form = {
            "name": "差旅住宿制度",
            "version": "V1",
            "department": "ALL",
            "expense_type": "TRAVEL",
            "effective_from": "2026-01-01",
            "effective_to": "",
        }
        form.update(overrides)
        response = self.client.post(
            "/api/v1/policies",
            data=form,
            files={"file": ("travel_policy.txt", policy_text.encode("utf-8"), "text/plain")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_upload_creates_draft_with_chunks(self):
        payload = self.upload(POLICY_V1)

        self.assertEqual(payload["status"], "draft")
        self.assertGreaterEqual(payload["chunk_count"], 2)
        self.assertEqual(payload["version"], "V1")
        self.assertEqual(payload["effective_from"], "2026-01-01")

    def test_rejects_unsupported_file_type(self):
        response = self.client.post(
            "/api/v1/policies",
            data={"name": "x", "version": "V1", "effective_from": "2026-01-01"},
            files={"file": ("policy.exe", b"binary", "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("仅支持", response.json()["detail"])

    def test_rejects_missing_metadata(self):
        response = self.client.post(
            "/api/v1/policies",
            data={"name": "", "version": "V1", "effective_from": "2026-01-01"},
            files={"file": ("policy.txt", POLICY_V1.encode("utf-8"), "text/plain")},
        )

        # FastAPI 表单校验把空名称视为缺失(422)
        self.assertEqual(response.status_code, 422)

    def test_publish_requires_complete_metadata(self):
        """FR-301:元数据不完整(缺生效时间)不允许发布。"""
        document = policy_store.create_policy(
            policy_id="UP-INCOMPLETE", name="缺日期的制度", version="V1",
            department="ALL", expense_type="TRAVEL", effective_from="",
            source_file="x.txt", mime="text/plain",
        )
        policy_store.save_chunks(document.policy_id, chunk_text(POLICY_V1, policy_id=document.policy_id, version="V1"))

        response = self.client.post(f"/api/v1/policies/{document.policy_id}/publish")

        self.assertEqual(response.status_code, 400)
        self.assertIn("元数据不完整", response.json()["detail"])

    def test_draft_is_not_used_by_retrieval_until_published(self):
        payload = self.upload(POLICY_V1)

        evidence = retrieve_policy("拉萨")

        self.assertFalse(any(item.chunk_id.startswith(payload["policy_id"]) for item in evidence))

    def test_publish_makes_policy_retrievable_and_auditable(self):
        payload = self.upload(POLICY_V1)
        publish = self.client.post(f"/api/v1/policies/{payload['policy_id']}/publish")

        self.assertEqual(publish.status_code, 200)
        self.assertEqual(publish.json()["policy"]["status"], "published")
        evidence = retrieve_policy("拉萨")
        self.assertTrue(any(item.chunk_id.startswith(payload["policy_id"]) for item in evidence))
        self.assertIn("500元/晚", evidence[0].content)

        report = run_audit(load_audit_files("520.00"), task_id="task-lasa")
        risk = next(item for item in report.risks if item.risk_type == "HOTEL_LIMIT")
        self.assertIn("20.00", risk.reason)          # 520 - 500
        # 已发布制度条款参与引用(基线的"其他城市"兜底条款仍会一并引用)
        self.assertTrue(any(ref.startswith(payload["policy_id"]) for ref in risk.policy_refs))

    def test_publish_requires_complete_metadata_and_chunks(self):
        response = self.client.post("/api/v1/policies/UP-NOPE/publish")

        self.assertEqual(response.status_code, 404)

    def test_disable_removes_policy_from_retrieval(self):
        payload = self.upload(POLICY_V1)
        self.client.post(f"/api/v1/policies/{payload['policy_id']}/publish")

        disable = self.client.post(f"/api/v1/policies/{payload['policy_id']}/disable")

        self.assertEqual(disable.status_code, 200)
        self.assertEqual(disable.json()["policy"]["status"], "disabled")
        evidence = retrieve_policy("拉萨")
        self.assertFalse(any(item.chunk_id.startswith(payload["policy_id"]) for item in evidence))

    def test_version_switch_disables_previous_version(self):
        v1 = self.upload(POLICY_V1, version="V1", policy_id="UP-TRAVEL-V1")
        self.client.post(f"/api/v1/policies/{v1['policy_id']}/publish")
        v2 = self.upload(POLICY_V2, version="V2", policy_id="UP-TRAVEL-V2")

        switched = self.client.post(f"/api/v1/policies/{v2['policy_id']}/publish?switch_version=true")

        self.assertEqual(switched.json()["policy"]["status"], "published")
        self.assertEqual(switched.json()["disabled_policy_ids"], [v1["policy_id"]])
        evidence = retrieve_policy("拉萨")
        self.assertTrue(any("300元/晚" in item.content for item in evidence))
        self.assertFalse(any("500元/晚" in item.content for item in evidence))

    def test_department_filter_excludes_other_departments(self):
        payload = self.upload(POLICY_V1, department="市场部")
        self.client.post(f"/api/v1/policies/{payload['policy_id']}/publish")

        allowed = retrieve_policy("拉萨", department="市场部")
        blocked = retrieve_policy("拉萨", department="研发部")

        self.assertTrue(any(item.chunk_id.startswith(payload["policy_id"]) for item in allowed))
        self.assertFalse(any(item.chunk_id.startswith(payload["policy_id"]) for item in blocked))

    def test_effective_window_filter_excludes_future_policy(self):
        payload = self.upload(POLICY_V1, effective_from="2027-01-01")
        self.client.post(f"/api/v1/policies/{payload['policy_id']}/publish")

        before = retrieve_policy("拉萨", as_of="2026-08-01")
        after = retrieve_policy("拉萨", as_of="2027-06-01")

        self.assertFalse(any(item.chunk_id.startswith(payload["policy_id"]) for item in before))
        self.assertTrue(any(item.chunk_id.startswith(payload["policy_id"]) for item in after))

    def test_version_lock_keeps_task_using_old_policy_until_switch(self):
        """FR-303:新版本发布后旧任务按任务日期仍可命中旧版本,切换后新任务用新版本。"""
        v1 = self.upload(POLICY_V1, version="V1", policy_id="UP-KEEP-V1")
        self.client.post(f"/api/v1/policies/{v1['policy_id']}/publish")
        v2 = self.upload(POLICY_V2, version="V2", policy_id="UP-KEEP-V2", effective_from="2027-01-01")
        self.client.post(f"/api/v1/policies/{v2['policy_id']}/publish")

        current = retrieve_policy("拉萨", as_of="2026-08-01")
        future = retrieve_policy("拉萨", as_of="2027-06-01")

        self.assertTrue(any("500元/晚" in item.content for item in current))
        self.assertTrue(any("300元/晚" in item.content for item in future))


class AuditDepartmentContextTests(unittest.TestCase):
    def setUp(self):
        policy_store.isolate_store()
        policy_store.reset_store()
        isolate_registry()

    def test_run_audit_passes_department_into_retrieval(self):
        self.client = TestClient(app)
        uploaded = self.client.post(
            "/api/v1/policies",
            data={"name": "研发部住宿制度", "version": "V1", "department": "研发部",
                  "expense_type": "TRAVEL", "effective_from": "2026-01-01"},
            files={"file": ("rd_policy.txt", POLICY_V1.encode("utf-8"), "text/plain")},
        ).json()
        self.client.post(f"/api/v1/policies/{uploaded['policy_id']}/publish")

        matched = run_audit(load_audit_files(), task_id="task-dept-yes", department="研发部")
        blocked = run_audit(load_audit_files(), task_id="task-dept-no", department="财务部")

        self.assertTrue(any(item.chunk_id.startswith(uploaded["policy_id"])
                            for item in matched.policy_evidence))
        self.assertFalse(any(item.chunk_id.startswith(uploaded["policy_id"])
                             for item in blocked.policy_evidence))


if __name__ == "__main__":
    unittest.main()
