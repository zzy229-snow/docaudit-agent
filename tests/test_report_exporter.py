import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.agent.graph import run_audit
from app.api.store import AuditTask, StoredFile
from app.services.report_exporter import build_html_report


BASE = Path(__file__).resolve().parents[1] / "data" / "demo" / "over_limit"


class ReportExporterTests(unittest.TestCase):
    def test_html_report_contains_conclusion_risks_and_policy(self):
        files = [(path.name, path.read_bytes()) for path in sorted(BASE.iterdir()) if path.is_file()]
        report = run_audit(files)
        task = AuditTask(
            task_id="task-report-test",
            status="COMPLETED",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            files=[StoredFile(file_name=name, content=content) for name, content in files],
            report=report,
        )

        html = build_html_report(task)

        self.assertIn("<!doctype html>", html)
        self.assertIn("企业报销审核报告", html)
        self.assertIn("HOTEL_LIMIT", html)
        self.assertIn("TRAVEL-V1-4.2-A", html)
        self.assertIn("字段证据", html)


if __name__ == "__main__":
    unittest.main()
