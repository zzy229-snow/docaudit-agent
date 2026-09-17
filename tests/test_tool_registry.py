import unittest

from app.models.audit import PolicyEvidence
from app.tools.registry import TOOL_REGISTRY, get_tool, list_tool_specs


class ToolRegistryTests(unittest.TestCase):
    def test_registry_contains_core_audit_tools(self):
        self.assertEqual(
            set(TOOL_REGISTRY),
            {"required_documents", "amount_match", "date_range", "hotel_limit", "applicant_match"},
        )
        self.assertTrue(all(spec.idempotent for spec in list_tool_specs()))

    def test_amount_tool_executes_through_registry(self):
        execution = get_tool("amount_match").execute(invoice_amount="1280.00", payment_amount="1180.00")

        self.assertEqual(execution.spec.name, "amount_match")
        self.assertFalse(execution.result.passed)
        self.assertEqual(execution.input_summary["invoice_amount"], "1280.00")
        self.assertIn("100.00", execution.result.detail)

    def test_hotel_limit_tool_keeps_policy_evidence_summary(self):
        evidence = [
            PolicyEvidence(
                chunk_id="TRAVEL-V1-4.2-A",
                section="住宿标准",
                content="北京住宿标准为1200元/晚。",
                score=1.0,
            )
        ]

        execution = get_tool("hotel_limit").execute(invoice_amount="1280.00", policy_evidence=evidence)

        self.assertEqual(execution.input_summary["policy_evidence"], "list[1]")
        self.assertFalse(execution.result.passed)
        self.assertIn("TRAVEL-V1-4.2-A", execution.result.evidence_refs)

    def test_unknown_tool_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            get_tool("not_a_tool")


if __name__ == "__main__":
    unittest.main()
