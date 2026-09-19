import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app
from app.services.invoice_registry import isolate_registry


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class AuditTaskApiTests(unittest.TestCase):
    def setUp(self):
        # 发票查重登记表隔离:同一用例内多次提交同一发票不应互相污染(FR-204)
        isolate_registry()
        self.client = TestClient(app)

    def create_task(self) -> str:
        response = self.client.post("/api/v1/audit-tasks")
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "CREATED")
        return data["task_id"]

    def upload_case(self, task_id: str, case: str) -> None:
        for path in sorted((BASE / case).iterdir()):
            if path.is_file():
                with path.open("rb") as handle:
                    response = self.client.post(
                        f"/api/v1/audit-tasks/{task_id}/documents",
                        files={"file": (path.name, handle, "text/plain")},
                    )
                self.assertEqual(response.status_code, 200)
        summary = self.client.get(f"/api/v1/audit-tasks/{task_id}").json()
        self.assertEqual(summary["status"], "READY")
        self.assertEqual(summary["file_count"], 3)

    def test_audit_task_normal_case_passes_through_api(self):
        task_id = self.create_task()
        self.upload_case(task_id, "normal")

        run_response = self.client.post(f"/api/v1/audit-tasks/{task_id}/run")
        self.assertEqual(run_response.status_code, 200)
        self.assertEqual(run_response.json()["result_status"], "PASS")

        fields = self.client.get(f"/api/v1/audit-tasks/{task_id}/fields")
        self.assertEqual(fields.status_code, 200)
        self.assertIn("invoice_amount", fields.json()["fields"])

        trace = self.client.get(f"/api/v1/audit-tasks/{task_id}/trace")
        self.assertEqual(trace.status_code, 200)
        # 审核流程复用 API 任务ID(修正后重跑也是同一任务,FR-104/FR-204)
        self.assertTrue(any(item == f"task_id={task_id}" for item in trace.json()["trace"]))

    def test_audit_task_over_limit_exposes_risks(self):
        task_id = self.create_task()
        self.upload_case(task_id, "over_limit")

        run_response = self.client.post(f"/api/v1/audit-tasks/{task_id}/run")
        self.assertEqual(run_response.status_code, 200)
        self.assertEqual(run_response.json()["result_status"], "REVIEW_REQUIRED")

        risks = self.client.get(f"/api/v1/audit-tasks/{task_id}/risks")
        self.assertEqual(risks.status_code, 200)
        self.assertTrue(any(item["risk_type"] == "HOTEL_LIMIT" for item in risks.json()["risks"]))
        self.assertTrue(any(item["risk_type"] == "APPLICANT_MATCH" for item in risks.json()["risks"]))

        summary = self.client.get(f"/api/v1/audit-tasks/{task_id}/summary")
        self.assertEqual(summary.status_code, 200)
        payload = summary.json()["summary"]
        self.assertEqual(payload["status"], "REVIEW_REQUIRED")
        self.assertTrue(any(item["risk_type"] == "HOTEL_LIMIT" for item in payload["risks"]))
        self.assertTrue(any(item["chunk_id"] == "TRAVEL-V1-4.2-A" for item in payload["policy_evidence"]))

        html_report = self.client.get(f"/api/v1/audit-tasks/{task_id}/report.html")
        self.assertEqual(html_report.status_code, 200)
        self.assertIn("text/html", html_report.headers["content-type"])
        self.assertIn("企业报销审核报告", html_report.text)
        self.assertIn("HOTEL_LIMIT", html_report.text)

        review_items = self.client.get(f"/api/v1/audit-tasks/{task_id}/review-items")
        self.assertEqual(review_items.status_code, 200)
        self.assertTrue(any(item["risk_type"] == "HOTEL_LIMIT" for item in review_items.json()["review_items"]))
        self.assertTrue(any(item["risk_type"] == "APPLICANT_MATCH" for item in review_items.json()["review_items"]))

    def test_field_correction_reruns_audit_and_clears_risk(self):
        task_id = self.create_task()
        self.upload_case(task_id, "over_limit")
        self.client.post(f"/api/v1/audit-tasks/{task_id}/run")

        correction = self.client.patch(
            f"/api/v1/audit-tasks/{task_id}/fields/invoice_amount",
            json={"value": "580.00", "reason": "人工核对酒店发票原图，金额应为580.00"},
        )
        self.assertEqual(correction.status_code, 200)
        self.assertEqual(correction.json()["correction"]["original_value"], "680.00")
        payment_correction = self.client.patch(
            f"/api/v1/audit-tasks/{task_id}/fields/payment_amount",
            json={"value": "580.00", "reason": "人工核对付款截图，金额应为580.00"},
        )
        self.assertEqual(payment_correction.status_code, 200)
        buyer_correction = self.client.patch(
            f"/api/v1/audit-tasks/{task_id}/fields/invoice_buyer",
            json={"value": "张三", "reason": "人工核对发票抬头，购买方应为张三"},
        )
        self.assertEqual(buyer_correction.status_code, 200)

        rerun = self.client.post(f"/api/v1/audit-tasks/{task_id}/run")
        self.assertEqual(rerun.status_code, 200)
        self.assertEqual(rerun.json()["result_status"], "PASS")

        risks = self.client.get(f"/api/v1/audit-tasks/{task_id}/risks")
        self.assertEqual(risks.status_code, 200)
        self.assertEqual(risks.json()["risks"], [])

        fields = self.client.get(f"/api/v1/audit-tasks/{task_id}/fields")
        self.assertEqual(fields.json()["fields"]["invoice_amount"]["value"], "580.00")

        trace = self.client.get(f"/api/v1/audit-tasks/{task_id}/trace").json()["trace"]
        self.assertTrue(any("字段invoice_amount已人工修正为580.00" in item for item in trace))

    def test_review_item_decision_is_recorded(self):
        task_id = self.create_task()
        self.upload_case(task_id, "over_limit")
        self.client.post(f"/api/v1/audit-tasks/{task_id}/run")
        item = self.client.get(f"/api/v1/audit-tasks/{task_id}/review-items").json()["review_items"][0]

        decision = self.client.post(
            f"/api/v1/review-items/{item['review_item_id']}/decision",
            json={"decision": "APPROVED", "decided_by": "finance-reviewer"},
        )

        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["review_item"]["status"], "APPROVED")
        self.assertEqual(decision.json()["review_item"]["decided_by"], "finance-reviewer")

        events = self.client.get(f"/api/v1/audit-tasks/{task_id}/events")
        self.assertEqual(events.status_code, 200)
        self.assertTrue(any(item["event_type"] == "REVIEW_DECIDED" for item in events.json()["events"]))

    def test_task_list_and_events_show_audit_lifecycle(self):
        task_id = self.create_task()
        self.upload_case(task_id, "normal")
        self.client.post(f"/api/v1/audit-tasks/{task_id}/run")

        tasks = self.client.get("/api/v1/audit-tasks")
        self.assertEqual(tasks.status_code, 200)
        self.assertTrue(any(item["task_id"] == task_id for item in tasks.json()["tasks"]))

        events = self.client.get(f"/api/v1/audit-tasks/{task_id}/events")
        self.assertEqual(events.status_code, 200)
        event_types = [item["event_type"] for item in events.json()["events"]]
        self.assertIn("TASK_CREATED", event_types)
        self.assertIn("DOCUMENT_UPLOADED", event_types)
        self.assertIn("AUDIT_COMPLETED", event_types)

    def test_running_empty_task_is_rejected(self):
        task_id = self.create_task()

        response = self.client.post(f"/api/v1/audit-tasks/{task_id}/run")

        self.assertEqual(response.status_code, 400)
        self.assertIn("no uploaded documents", response.json()["detail"])

    def test_missing_task_returns_404(self):
        response = self.client.get("/api/v1/audit-tasks/task-missing")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
