"""评测接口与接口契约测试(PRD §12.2 / §15)。"""
import unittest

from fastapi.testclient import TestClient

from app.api.main import app


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_openapi_exposes_prd_endpoints(self):
        paths = set(app.openapi()["paths"])

        for expected in (
            "/api/v1/audit-tasks",
            "/api/v1/audit-tasks/{task_id}/documents",
            "/api/v1/audit-tasks/{task_id}/run",
            "/api/v1/audit-tasks/{task_id}",
            "/api/v1/audit-tasks/{task_id}/fields",
            "/api/v1/audit-tasks/{task_id}/risks",
            "/api/v1/audit-tasks/{task_id}/trace",
            "/api/v1/review-items/{review_item_id}/decision",
            "/api/v1/policies",
            "/api/v1/policies/{policy_id}/publish",
            "/api/v1/evaluations/run",
        ):
            self.assertIn(expected, paths, expected)


class EvaluationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_run_default_evaluation_returns_metrics(self):
        response = self.client.post("/api/v1/evaluations/run")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        metrics = payload["metrics"]
        self.assertGreaterEqual(metrics["total_cases"], 57)
        self.assertEqual(metrics["failed_cases"], 0)
        self.assertEqual(metrics["pass_rate"], 1.0)
        self.assertTrue(payload["results"])

    def test_run_with_markdown_format(self):
        response = self.client.post("/api/v1/evaluations/run", json={"format": "markdown"})

        payload = response.json()

        self.assertIsNone(payload["results"])
        self.assertIn("Audit Agent Evaluation Report", payload["markdown"])
        self.assertIn("风险 Precision", payload["markdown"])

    def test_custom_cases_path_outside_repo_is_rejected(self):
        response = self.client.post(
            "/api/v1/evaluations/run",
            json={"cases_path": "../../../../Windows/win.ini"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("仓库目录内", response.json()["detail"])

    def test_missing_cases_path_returns_404(self):
        response = self.client.post(
            "/api/v1/evaluations/run",
            json={"cases_path": "data/evaluation/does_not_exist.json"},
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
