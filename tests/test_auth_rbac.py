"""RBAC 与对象级鉴权测试(PRD §2 角色权限 / §15 访问控制 / AC-09)。"""
import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app
from app.services.invoice_registry import isolate_registry


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"

ALICE = {"X-User-Id": "alice", "X-Role": "APPLICANT"}
BOB = {"X-User-Id": "bob", "X-Role": "APPLICANT"}
REVIEWER = {"X-User-Id": "reviewer-1", "X-Role": "REVIEWER"}
POLICY_ADMIN = {"X-User-Id": "policy-admin", "X-Role": "POLICY_ADMIN"}
DEVELOPER = {"X-User-Id": "dev-1", "X-Role": "DEVELOPER"}


def upload_case(client: TestClient, task_id: str, case: str, headers: dict) -> None:
    for path in sorted((BASE / case).iterdir()):
        if path.is_file():
            with path.open("rb") as handle:
                response = client.post(
                    f"/api/v1/audit-tasks/{task_id}/documents",
                    files={"file": (path.name, handle, "text/plain")},
                    headers=headers,
                )
            assert response.status_code == 200, response.text


class AuthDisabledTests(unittest.TestCase):
    """AUTH_MODE 默认 off:保持既有调用契约(本地演示/单测)。"""

    def setUp(self):
        os.environ.pop("AUTH_MODE", None)
        isolate_registry()
        self.client = TestClient(app)

    def test_calls_without_identity_headers_still_work(self):
        created = self.client.post("/api/v1/audit-tasks")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get("/api/v1/audit-tasks").status_code, 200)


class RbacEnforcedTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTH_MODE"] = "enforce"
        isolate_registry()
        self.client = TestClient(app)
        self.alice_task = self._create_task(ALICE, applicant="alice", department="研发部")

    def tearDown(self):
        os.environ.pop("AUTH_MODE", None)

    def _create_task(self, headers: dict, applicant: str, department: str = "") -> str:
        response = self.client.post(
            "/api/v1/audit-tasks",
            json={"applicant": applicant, "department": department, "expense_type": "TRAVEL"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["task_id"]

    def test_missing_identity_is_rejected(self):
        response = self.client.get("/api/v1/audit-tasks")

        self.assertEqual(response.status_code, 401)
        self.assertIn("身份头", response.json()["detail"])

    def test_unknown_role_is_rejected(self):
        response = self.client.get("/api/v1/audit-tasks", headers={"X-User-Id": "x", "X-Role": "ROOT"})

        self.assertEqual(response.status_code, 401)

    def test_applicant_cannot_read_other_task_and_existence_is_not_leaked(self):
        """AC-09:越权访问返回拒绝,且不泄露任务是否存在。"""
        self._create_task(BOB, applicant="bob")

        denied = self.client.get(f"/api/v1/audit-tasks/{self.alice_task}", headers=BOB)
        missing = self.client.get("/api/v1/audit-tasks/task-does-not-exist", headers=BOB)
        allowed = self.client.get(f"/api/v1/audit-tasks/{self.alice_task}", headers=ALICE)

        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json(), missing.json())
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["applicant"], "alice")

    def test_denied_access_is_written_to_audit_log(self):
        self.client.get(f"/api/v1/audit-tasks/{self.alice_task}", headers=BOB)

        events = self.client.get(f"/api/v1/audit-tasks/{self.alice_task}/events", headers=REVIEWER).json()

        self.assertTrue(any(item["event_type"] == "ACCESS_DENIED" for item in events["events"]))

    def test_applicant_list_only_returns_own_tasks(self):
        self._create_task(BOB, applicant="bob")

        alice_tasks = self.client.get("/api/v1/audit-tasks", headers=ALICE).json()["tasks"]
        reviewer_tasks = self.client.get("/api/v1/audit-tasks", headers=REVIEWER).json()["tasks"]

        self.assertTrue(alice_tasks)
        self.assertTrue(all(item["applicant"] == "alice" for item in alice_tasks))
        self.assertGreaterEqual(len(reviewer_tasks), len(alice_tasks))

    def test_applicant_cannot_create_task_for_someone_else(self):
        response = self.client.post(
            "/api/v1/audit-tasks",
            json={"applicant": "someone-else"},
            headers=ALICE,
        )

        self.assertEqual(response.status_code, 403)

    def test_applicant_requires_applicant_field(self):
        response = self.client.post("/api/v1/audit-tasks", json={}, headers=ALICE)

        self.assertEqual(response.status_code, 400)

    def test_reviewer_can_access_any_task_and_decide_review_items(self):
        upload_case(self.client, self.alice_task, "over_limit", ALICE)
        self.client.post(f"/api/v1/audit-tasks/{self.alice_task}/run", headers=ALICE)

        risks = self.client.get(f"/api/v1/audit-tasks/{self.alice_task}/risks", headers=REVIEWER)
        self.assertEqual(risks.status_code, 200)

        item = self.client.get(f"/api/v1/audit-tasks/{self.alice_task}/review-items", headers=REVIEWER).json()["review_items"][0]
        decision = self.client.post(
            f"/api/v1/review-items/{item['review_item_id']}/decision",
            json={"decision": "APPROVED", "decided_by": "reviewer-1"},
            headers=REVIEWER,
        )

        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["review_item"]["status"], "APPROVED")

    def test_applicant_cannot_decide_review_items(self):
        upload_case(self.client, self.alice_task, "over_limit", ALICE)
        self.client.post(f"/api/v1/audit-tasks/{self.alice_task}/run", headers=ALICE)
        item = self.client.get(f"/api/v1/audit-tasks/{self.alice_task}/review-items", headers=REVIEWER).json()["review_items"][0]

        decision = self.client.post(
            f"/api/v1/review-items/{item['review_item_id']}/decision",
            json={"decision": "APPROVED"},
            headers=ALICE,
        )

        self.assertEqual(decision.status_code, 403)

    def test_policy_endpoints_require_policy_admin(self):
        form = {"name": "差旅制度", "version": "V1", "effective_from": "2026-01-01"}
        policy_text = "第4章 标准\n4.2 拉萨住宿标准为500元/晚。".encode("utf-8")

        as_applicant = self.client.post(
            "/api/v1/policies",
            data=form,
            files={"file": ("p.txt", policy_text, "text/plain")},
            headers=ALICE,
        )
        as_admin = self.client.post(
            "/api/v1/policies",
            data=form,
            files={"file": ("p.txt", policy_text, "text/plain")},
            headers=POLICY_ADMIN,
        )

        self.assertEqual(as_applicant.status_code, 403)
        self.assertEqual(as_admin.status_code, 201)

    def test_evaluation_endpoint_requires_developer_or_admin(self):
        denied = self.client.post("/api/v1/evaluations/run", headers=REVIEWER)
        allowed = self.client.post("/api/v1/evaluations/run", headers=DEVELOPER)

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_health_and_docs_stay_public(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)

    def test_task_department_is_passed_into_retrieval_context(self):
        """§2/FR-304:任务部门进入检索上下文(此处只验证元数据贯通)。"""
        task = self.client.get(f"/api/v1/audit-tasks/{self.alice_task}", headers=ALICE).json()

        self.assertEqual(task["department"], "研发部")
        self.assertEqual(task["applicant"], "alice")
        self.assertEqual(task["expense_type"], "TRAVEL")


if __name__ == "__main__":
    unittest.main()
