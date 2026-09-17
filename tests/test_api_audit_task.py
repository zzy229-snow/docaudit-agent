import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class AuditTaskApiTests(unittest.TestCase):
    def setUp(self):
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
        self.assertTrue(any(item.startswith("task_id=audit-") for item in trace.json()["trace"]))

    def test_audit_task_over_limit_exposes_risks(self):
        task_id = self.create_task()
        self.upload_case(task_id, "over_limit")

        run_response = self.client.post(f"/api/v1/audit-tasks/{task_id}/run")
        self.assertEqual(run_response.status_code, 200)
        self.assertEqual(run_response.json()["result_status"], "REVIEW_REQUIRED")

        risks = self.client.get(f"/api/v1/audit-tasks/{task_id}/risks")
        self.assertEqual(risks.status_code, 200)
        self.assertTrue(any(item["risk_type"] == "HOTEL_LIMIT" for item in risks.json()["risks"]))

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
