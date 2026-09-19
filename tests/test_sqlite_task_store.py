import tempfile
import unittest
from pathlib import Path

from app.api.store import SQLiteAuditTaskStore
from app.agent.graph import run_audit
from app.services.invoice_registry import isolate_registry


BASE = Path(__file__).resolve().parents[1] / "data" / "demo" / "normal"


class SQLiteAuditTaskStoreTests(unittest.TestCase):
    def setUp(self):
        # 发票查重登记表隔离,避免用例之间互相污染(FR-204)
        isolate_registry()

    def test_task_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit_tasks.sqlite3"
            store = SQLiteAuditTaskStore(db_path)

            task = store.create_task()
            for path in sorted(BASE.iterdir()):
                store.add_file(task.task_id, path.name, path.read_bytes())
            task = store.require_task(task.task_id)
            report = run_audit([(item.file_name, item.content) for item in task.files])
            store.save_report(task.task_id, report)

            recreated = SQLiteAuditTaskStore(db_path)
            restored = recreated.require_task(task.task_id)

            self.assertEqual(restored.status, "COMPLETED")
            self.assertEqual(restored.report.status, "PASS")
            self.assertEqual(len(restored.files), 3)
            event_types = [item.event_type for item in recreated.list_events(task.task_id)]
            self.assertIn("TASK_CREATED", event_types)
            self.assertIn("DOCUMENT_UPLOADED", event_types)
            self.assertIn("AUDIT_COMPLETED", event_types)


if __name__ == "__main__":
    unittest.main()
