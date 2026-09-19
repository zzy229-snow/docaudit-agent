"""报销单命名测试(日期区间 + 人物 + 事件)。"""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.graph import run_audit
from app.api.main import app
from app.api.store import SQLiteAuditTaskStore
from app.models.field import ExtractedField
from app.services.invoice_registry import isolate_registry
from app.services.task_naming import (
    build_report_title,
    event_label,
    format_date_range,
    format_single_date,
    parse_date_parts,
    safe_file_name,
    sanitize_title,
    title_from_fields,
)


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


def load_files(case: str):
    return [(p.name, p.read_bytes()) for p in sorted((BASE / case).iterdir()) if p.is_file()]


class DateFormatTests(unittest.TestCase):
    def test_same_month_range_matches_product_spec(self):
        """产品示例:2026.9.18-9.19(右端同年省略年份)。"""
        self.assertEqual(format_date_range("2026-09-18", "2026-09-19"), "2026.9.18-9.19")

    def test_cross_month_and_cross_year(self):
        self.assertEqual(format_date_range("2026-09-28", "2026-10-02"), "2026.9.28-10.2")
        self.assertEqual(format_date_range("2026-12-30", "2027-01-02"), "2026.12.30-2027.1.2")

    def test_single_day_and_one_sided_ranges(self):
        self.assertEqual(format_date_range("2026-08-01", "2026-08-01"), "2026.8.1")
        self.assertEqual(format_date_range("2026-08-01", None), "2026.8.1")
        self.assertEqual(format_date_range(None, "2026-08-02"), "2026.8.2")
        self.assertEqual(format_date_range(None, None), "")

    def test_parses_common_chinese_and_dotted_formats(self):
        self.assertEqual(parse_date_parts("2026年9月18日"), (2026, 9, 18))
        self.assertEqual(parse_date_parts("2026.9.18"), (2026, 9, 18))
        self.assertEqual(parse_date_parts("2026-09-18"), (2026, 9, 18))
        self.assertEqual(format_single_date("2026/9/8"), "2026.9.8")

    def test_invalid_dates_are_ignored(self):
        for value in (None, "", "待确认", "2026-13-40", "abc"):
            self.assertEqual(format_single_date(value), "", value)
            self.assertEqual(format_date_range(value, None), "", value)


class EventLabelTests(unittest.TestCase):
    def test_expense_type_codes_map_to_chinese_events(self):
        self.assertEqual(event_label("HOTEL"), "住宿报销")
        self.assertEqual(event_label("travel"), "差旅报销")
        self.assertEqual(event_label("PURCHASE"), "采购报销")

    def test_chinese_expense_type_is_recognized(self):
        self.assertEqual(event_label("住宿"), "住宿报销")
        self.assertEqual(event_label("城市交通费"), "交通报销")

    def test_user_event_wins(self):
        self.assertEqual(event_label("HOTEL", "9月上海出差住宿"), "9月上海出差住宿")
        self.assertEqual(event_label(None, None), "")


class TitleBuildTests(unittest.TestCase):
    def test_full_title_matches_product_spec(self):
        title = build_report_title(
            applicant="张三", expense_type="HOTEL",
            start_date="2026-09-18", end_date="2026-09-19",
        )

        self.assertEqual(title, "2026.9.18-9.19 张三 住宿报销")

    def test_missing_segments_are_omitted(self):
        self.assertEqual(build_report_title(applicant="张三", expense_type="HOTEL"), "张三 住宿报销")
        self.assertEqual(build_report_title(expense_type="HOTEL"), "住宿报销")
        self.assertEqual(
            build_report_title(start_date="2026-09-18", end_date="2026-09-19", event="住宿"),
            "2026.9.18-9.19 住宿",
        )

    def test_falls_back_to_task_id_when_nothing_is_known(self):
        self.assertEqual(build_report_title(task_id="task-abc123"), "task-abc123")
        self.assertEqual(build_report_title(), "")

    def test_title_from_extracted_fields_uses_trip_range(self):
        fields = {
            "applicant_name": ExtractedField(name="applicant_name", value="张三", confidence=1.0,
                                             document_id="d", page_no=1, source_text="申请人：张三"),
            "travel_start_date": ExtractedField(name="travel_start_date", value="2026-08-01", confidence=1.0,
                                                document_id="d", page_no=1, source_text="出差开始：2026-08-01"),
            "travel_end_date": ExtractedField(name="travel_end_date", value="2026-08-02", confidence=1.0,
                                              document_id="d", page_no=1, source_text="出差结束：2026-08-02"),
        }

        title = title_from_fields(fields, expense_type="HOTEL", task_id="task-x")

        self.assertEqual(title, "2026.8.1-8.2 张三 住宿报销")

    def test_title_from_fields_falls_back_to_invoice_date(self):
        fields = {
            "invoice_date": ExtractedField(name="invoice_date", value="2026-05-12", confidence=1.0,
                                           document_id="d", page_no=1, source_text="开票日期：2026-05-12"),
        }

        self.assertEqual(title_from_fields(fields, expense_type="TRAVEL"), "2026.5.12 差旅报销")

    def test_explicit_applicant_wins_over_extracted_name(self):
        """用户在新建审核里填的申请人优先于材料抽取到的姓名。"""
        fields = {
            "applicant_name": ExtractedField(name="applicant_name", value="张三", confidence=1.0,
                                             document_id="d", page_no=1, source_text="申请人：张三"),
            "invoice_date": ExtractedField(name="invoice_date", value="2026-08-01", confidence=1.0,
                                           document_id="d", page_no=1, source_text="发票日期：2026-08-01"),
        }

        title = title_from_fields(fields, expense_type="HOTEL", applicant="李四")

        self.assertEqual(title, "2026.8.1 李四 住宿报销")

    def test_safe_file_name(self):
        self.assertEqual(safe_file_name("2026.9.18-9.19 张三 住宿报销"), "2026.9.18-9.19_张三_住宿报销")
        self.assertEqual(safe_file_name(""), "docaudit-report")
        self.assertNotIn("/", safe_file_name("a/b:c*d?"))


class TitleSanitizingTests(unittest.TestCase):
    def test_control_characters_and_extra_spaces_are_cleaned(self):
        cleaned = sanitize_title("2026.9.18-9.19\n\t张三   住宿报销\r\n")

        self.assertEqual(cleaned, "2026.9.18-9.19 张三 住宿报销")

    def test_title_length_is_capped(self):
        self.assertEqual(len(sanitize_title("甲" * 200)), 80)

    def test_blank_input_falls_back_to_auto_name(self):
        self.assertEqual(sanitize_title("   \n  "), "")
        self.assertEqual(build_report_title(applicant="  \n", expense_type="HOTEL"), "住宿报销")


class StoreTitleTests(unittest.TestCase):
    def setUp(self):
        from app.services.invoice_registry import isolate_registry as _isolate

        _isolate()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "tasks.sqlite3"
        self.store = SQLiteAuditTaskStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_task_gets_metadata_title_at_creation(self):
        task = self.store.create_task(applicant="张三", department="市场部", expense_type="HOTEL")

        self.assertEqual(task.title, "张三 住宿报销")
        self.assertFalse(task.title_locked)

    def test_auto_title_is_refined_after_audit(self):
        task = self.store.create_task(applicant="张三", department="市场部", expense_type="HOTEL")
        for name, content in load_files("normal"):
            self.store.add_file(task.task_id, name, content)
        report = run_audit([(item.file_name, item.content) for item in self.store.require_task(task.task_id).files],
                           task_id=task.task_id)

        updated = self.store.save_report(task.task_id, report)

        self.assertEqual(updated.title, "2026.8.1-8.2 张三 住宿报销")

    def test_task_applicant_wins_over_extracted_name(self):
        """建单填的申请人优先:材料里是张三,建单填李四 → 名称用李四。"""
        task = self.store.create_task(applicant="李四", expense_type="HOTEL")
        for name, content in load_files("normal"):
            self.store.add_file(task.task_id, name, content)
        report = run_audit([(item.file_name, item.content) for item in self.store.require_task(task.task_id).files],
                           task_id=task.task_id)

        updated = self.store.save_report(task.task_id, report)

        self.assertEqual(updated.title, "2026.8.1-8.2 李四 住宿报销")

    def test_explicit_title_is_locked_and_not_overwritten(self):
        task = self.store.create_task(applicant="张三", expense_type="HOTEL", title="9月上海出差-住宿")
        for name, content in load_files("normal"):
            self.store.add_file(task.task_id, name, content)
        report = run_audit([(item.file_name, item.content) for item in self.store.require_task(task.task_id).files],
                           task_id=task.task_id)

        updated = self.store.save_report(task.task_id, report)

        self.assertEqual(updated.title, "9月上海出差-住宿")
        self.assertTrue(updated.title_locked)

    def test_rename_persists_and_locks(self):
        task = self.store.create_task(applicant="张三", expense_type="HOTEL")

        renamed = self.store.set_title(task.task_id, "2026.9.18-9.19 张三 住宿报销")

        self.assertEqual(renamed.title, "2026.9.18-9.19 张三 住宿报销")
        self.assertTrue(renamed.title_locked)
        recreated = SQLiteAuditTaskStore(self.db).require_task(task.task_id)
        self.assertEqual(recreated.title, "2026.9.18-9.19 张三 住宿报销")
        self.assertTrue(recreated.title_locked)

    def test_rename_is_recorded_as_audit_event(self):
        task = self.store.create_task(applicant="张三", expense_type="HOTEL")

        self.store.set_title(task.task_id, "新名称")

        events = self.store.list_events(task.task_id)
        self.assertTrue(any(event.event_type == "TASK_RENAMED" for event in events))


class LegacySchemaMigrationTests(unittest.TestCase):
    """旧库(没有 title 列)打开后自动补列,不丢历史任务。"""

    def test_missing_columns_are_added_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "legacy.sqlite3"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE audit_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    report_json TEXT,
                    error TEXT
                );
                """
            )
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO audit_tasks(task_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         ("task-legacy", "CREATED", now, now))
            conn.commit()
            conn.close()

            store = SQLiteAuditTaskStore(db)
            restored = store.require_task("task-legacy")

            self.assertEqual(restored.title, "")
            created = store.create_task(applicant="张三", expense_type="HOTEL")
            self.assertEqual(created.title, "张三 住宿报销")


class TaskTitleApiTests(unittest.TestCase):
    def setUp(self):
        isolate_registry()
        self.tmp = tempfile.TemporaryDirectory()
        import app.api.store as store_module

        self._original_store = store_module.task_store
        store_module.task_store = SQLiteAuditTaskStore(Path(self.tmp.name) / "api.sqlite3")
        self.client = TestClient(app)

    def tearDown(self):
        import app.api.store as store_module

        store_module.task_store = self._original_store
        self.tmp.cleanup()

    def test_create_returns_generated_title_and_note_becomes_event(self):
        response = self.client.post(
            "/api/v1/audit-tasks",
            json={"applicant": "张三", "department": "市场部", "expense_type": "HOTEL"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["title"], "张三 住宿报销")

        with_note = self.client.post(
            "/api/v1/audit-tasks",
            json={"applicant": "张三", "expense_type": "HOTEL", "note": "9月上海出差"},
        )
        # 事由作为"事件"参与命名,日期在运行审核后补全
        self.assertEqual(with_note.json()["title"], "张三 9月上海出差")

    def test_title_is_completed_after_running_audit(self):
        created = self.client.post(
            "/api/v1/audit-tasks",
            json={"applicant": "张三", "department": "市场部", "expense_type": "HOTEL"},
        ).json()
        task_id = created["task_id"]
        for name, content in load_files("normal"):
            self.client.post(f"/api/v1/audit-tasks/{task_id}/documents",
                             files={"file": (name, content, "text/plain")})

        self.client.post(f"/api/v1/audit-tasks/{task_id}/run")

        summary = self.client.get(f"/api/v1/audit-tasks/{task_id}").json()
        self.assertEqual(summary["title"], "2026.8.1-8.2 张三 住宿报销")
        self.assertEqual(summary["applicant"], "张三")

    def test_rename_endpoint_and_locked_title(self):
        task_id = self.client.post("/api/v1/audit-tasks", json={"applicant": "张三"}).json()["task_id"]

        renamed = self.client.patch(f"/api/v1/audit-tasks/{task_id}", json={"title": "2026.9.18-9.19 张三 住宿报销"})

        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "2026.9.18-9.19 张三 住宿报销")

        empty = self.client.patch(f"/api/v1/audit-tasks/{task_id}", json={"title": "   "})
        self.assertEqual(empty.status_code, 400)

    def test_task_list_exposes_title_first_class(self):
        self.client.post("/api/v1/audit-tasks", json={"applicant": "张三", "expense_type": "HOTEL"})

        tasks = self.client.get("/api/v1/audit-tasks").json()["tasks"]

        self.assertTrue(tasks)
        self.assertTrue(all("title" in task for task in tasks))


if __name__ == "__main__":
    unittest.main()
