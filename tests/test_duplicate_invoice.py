"""重复发票查重测试(PRD §5.3 FR-204 / §8.3 check_duplicate_invoice)。"""
import unittest
from pathlib import Path

from app.agent.graph import run_audit
from app.models.field import ExtractedField
from app.services.invoice_registry import (
    ENV_REGISTRY_PATH,
    find_matches,
    isolate_registry,
    record_count,
    register_invoice,
    register_task_invoice,
    registry_path,
    reset_registry,
)
from app.tools.duplicate_tool import check_duplicate_invoice
from app.tools.registry import get_tool


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


class RegistryTests(unittest.TestCase):
    def setUp(self):
        isolate_registry()
        reset_registry()

    def test_isolated_registry_uses_env_path(self):
        self.assertTrue(str(registry_path()).endswith("invoice_registry.sqlite3"))
        self.assertEqual(record_count(), 0)

    def test_register_then_find_and_dedup_history(self):
        self.assertTrue(register_invoice("task-1", "INV-001", "2026-08-01", "520.00"))
        self.assertEqual(record_count(), 1)

        records = find_matches("INV-001")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].task_id, "task-1")
        self.assertEqual(records[0].invoice_amount, "520.00")

        # 同一任务重复登记同一号码:幂等(主键去重)
        register_invoice("task-1", "INV-001", "2026-08-01", "520.00")
        self.assertEqual(record_count(), 1)

    def test_register_skips_missing_number_and_can_exclude_own_task(self):
        self.assertFalse(register_invoice("task-1", None))
        self.assertFalse(register_invoice("task-1", "   "))
        self.assertEqual(record_count(), 0)

        register_invoice("task-1", "INV-002")
        register_invoice("task-2", "INV-002")

        self.assertEqual(len(find_matches("INV-002")), 2)
        self.assertEqual([r.task_id for r in find_matches("INV-002", exclude_task_id="task-2")], ["task-1"])

    def test_register_task_invoice_reads_extracted_fields(self):
        fields = {
            "invoice_number": ExtractedField(name="invoice_number", value="INV-003", confidence=1.0,
                                             document_id="doc-1", page_no=1, source_text="发票号码：INV-003"),
            "invoice_amount": ExtractedField(name="invoice_amount", value="680.00", confidence=1.0,
                                            document_id="doc-1", page_no=1, source_text="发票金额：680.00"),
        }

        self.assertTrue(register_task_invoice("task-3", fields))

        record = find_matches("INV-003")[0]
        self.assertEqual(record.invoice_amount, "680.00")
        self.assertIsNone(record.invoice_date)

    def test_registry_path_follows_env(self):
        target = Path(registry_path())
        self.assertEqual(target.name, "invoice_registry.sqlite3")
        self.assertTrue(str(target).startswith(str(Path(registry_path()).parent)))
        import os

        self.assertIn(ENV_REGISTRY_PATH, os.environ)


class DuplicateToolTests(unittest.TestCase):
    def setUp(self):
        isolate_registry()
        reset_registry()

    def test_missing_invoice_number_is_skipped_not_risky(self):
        result = check_duplicate_invoice(None, "2026-08-01", "520.00", task_id="task-a")

        self.assertTrue(result.passed)
        self.assertIn("跳过", result.detail)

    def test_first_submission_is_not_duplicate(self):
        result = check_duplicate_invoice("INV-100", "2026-08-01", "520.00", task_id="task-a")

        self.assertTrue(result.passed)
        self.assertIn("未命中历史记录", result.detail)

    def test_exact_duplicate_reports_related_task_and_matched_fields(self):
        register_invoice("task-old", "INV-200", "2026-08-01", "520.00")

        result = check_duplicate_invoice("INV-200", "2026-08-01", "520.00", task_id="task-new")

        self.assertFalse(result.passed)
        self.assertEqual(result.name, "duplicate_invoice")
        self.assertIn("task-old", result.detail)
        self.assertIn("发票号码", result.detail)
        self.assertIn("开票日期", result.detail)
        self.assertIn("金额", result.detail)
        self.assertEqual(result.evidence_refs, ["invoice_number", "invoice_date", "invoice_amount"])

    def test_same_number_different_amount_asks_for_manual_verification(self):
        register_invoice("task-old", "INV-300", "2026-08-01", "520.00")

        result = check_duplicate_invoice("INV-300", "2026-08-01", "480.00", task_id="task-new")

        self.assertFalse(result.passed)
        self.assertEqual(result.name, "invoice_number_conflict")
        self.assertIn("不一致", result.detail)
        self.assertIn("task-old", result.detail)

    def test_own_task_record_is_ignored(self):
        register_invoice("task-self", "INV-400", "2026-08-01", "520.00")

        result = check_duplicate_invoice("INV-400", "2026-08-01", "520.00", task_id="task-self")

        self.assertTrue(result.passed)

    def test_tool_is_registered_with_expected_spec(self):
        tool = get_tool("check_duplicate_invoice")
        execution = tool.execute(invoice_number="INV-500", invoice_date=None, invoice_amount=None, task_id="t")

        self.assertEqual(tool.spec.category, "database")
        self.assertTrue(tool.spec.idempotent)
        self.assertEqual(execution.spec.name, "check_duplicate_invoice")
        self.assertTrue(execution.result.passed)


class DuplicateEndToEndTests(unittest.TestCase):
    def setUp(self):
        isolate_registry()
        reset_registry()

    def load_files(self, case: str):
        return [(p.name, p.read_bytes()) for p in sorted((BASE / case).iterdir()) if p.is_file()]

    def test_second_submission_of_same_invoice_is_high_risk(self):
        """FR-204:同一发票第二次提交 → DUPLICATE_INVOICE(HIGH)并进入人工复核。"""
        first = run_audit(self.load_files("normal"), task_id="task-first")
        self.assertEqual(first.status, "PASS")
        self.assertEqual(first.risks, [])

        second = run_audit(self.load_files("normal"), task_id="task-second")

        self.assertEqual(second.status, "REVIEW_REQUIRED")
        risk = next(r for r in second.risks if r.risk_type == "DUPLICATE_INVOICE")
        self.assertEqual(risk.level, "HIGH")
        self.assertIn("task-first", risk.reason)
        self.assertEqual(second.risk_summary, {"HIGH": 1})

    def test_rerunning_same_task_id_is_not_flagged(self):
        """修正后重跑必须复用同一 task_id,否则会被自己的历史记录误判。"""
        files = self.load_files("normal")
        run_audit(files, task_id="task-rerun")
        again = run_audit(files, task_id="task-rerun")

        self.assertEqual(again.status, "PASS")

    def test_materials_without_invoice_number_skip_dedup(self):
        files = [(p.name, p.read_bytes()) for p in sorted((BASE / "normal").iterdir())
                 if p.name != "invoice.txt"]
        report = run_audit(files, task_id="task-no-invoice")

        self.assertNotIn("DUPLICATE_INVOICE", {r.risk_type for r in report.risks})


if __name__ == "__main__":
    unittest.main()
