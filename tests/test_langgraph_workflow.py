"""LangGraph 工作流、受控计划与防循环测试(PRD §7.2/§7.4)。"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent import pipeline
from app.agent.graph import resolve_engine, run_audit
from app.agent.langgraph_workflow import build_audit_graph, langgraph_available
from app.agent.planner import allowed_tools, build_plan, validate_plan
from app.agent import nodes as agent_nodes
from app.models.audit import AuditPlan, CritiqueResult, PlanStep, RiskItem
from app.models.state import AgentControl, ToolCallRecord
from app.services.invoice_registry import isolate_registry


BASE = Path(__file__).resolve().parents[1] / "data" / "demo"


def load_files(case: str):
    return [(p.name, p.read_bytes()) for p in sorted((BASE / case).iterdir()) if p.is_file()]


class EngineSelectionTests(unittest.TestCase):
    def setUp(self):
        isolate_registry()

    def tearDown(self):
        os.environ.pop("AGENT_ENGINE", None)

    def test_default_engine_is_langgraph(self):
        os.environ.pop("AGENT_ENGINE", None)

        self.assertEqual(resolve_engine(), "langgraph")

    def test_unknown_engine_falls_back_to_default(self):
        os.environ["AGENT_ENGINE"] = "no_such_engine"

        self.assertEqual(resolve_engine(), "langgraph")

    def test_sequential_engine_still_available(self):
        os.environ["AGENT_ENGINE"] = "sequential"

        report = run_audit(load_files("over_limit"), task_id="task-seq")

        self.assertEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual({risk.risk_type for risk in report.risks}, {"APPLICANT_MATCH", "HOTEL_LIMIT"})

    def test_langgraph_and_sequential_produce_same_conclusions(self):
        cases = ["normal", "over_limit", "amount_mismatch", "date_out_of_range"]
        results = {}
        for engine in ("langgraph", "sequential"):
            os.environ["AGENT_ENGINE"] = engine
            isolate_registry()
            results[engine] = {case: run_audit(load_files(case), task_id=f"task-{engine}") for case in cases}

        for case in cases:
            graph_report = results["langgraph"][case]
            seq_report = results["sequential"][case]
            self.assertEqual(graph_report.status, seq_report.status, case)
            self.assertEqual(
                sorted(risk.risk_type for risk in graph_report.risks),
                sorted(risk.risk_type for risk in seq_report.risks),
                case,
            )
            self.assertEqual(
                {name: f.value for name, f in graph_report.fields.items()},
                {name: f.value for name, f in seq_report.fields.items()},
                case,
            )
            self.assertEqual(
                [step.tool for step in graph_report.audit_plan.steps],
                [step.tool for step in seq_report.audit_plan.steps],
                case,
            )

    def test_falls_back_to_sequential_when_langgraph_missing(self):
        os.environ["AGENT_ENGINE"] = "langgraph"
        with patch("app.agent.graph.langgraph_available", return_value=False):
            report = run_audit(load_files("normal"), task_id="task-fallback")

        self.assertEqual(report.status, "PASS")
        self.assertTrue(any("回退顺序执行" in item for item in report.trace))

    def test_max_steps_guard_applies_on_langgraph_path(self):
        os.environ["AGENT_ENGINE"] = "langgraph"

        with self.assertRaisesRegex(RuntimeError, "max_steps=3"):
            run_audit(load_files("normal"), max_steps=3)


class GraphStructureTests(unittest.TestCase):
    @unittest.skipUnless(langgraph_available(), "本机未安装 langgraph")
    def test_graph_contains_all_prd_nodes_and_compiles(self):
        graph = build_audit_graph()

        self.assertIsNotNone(graph)
        self.assertEqual(
            pipeline.NODE_NAMES,
            ("parse_documents", "extract", "retrieve", "plan", "check", "critic", "route_review", "report"),
        )

    def test_node_sequence_matches_prd_section_7_2(self):
        """§7.2 节点表:ingest/parse、extract、plan、retrieve、execute、critic、route_review、report。"""
        names = set(pipeline.NODE_NAMES)

        self.assertEqual(
            names,
            {"parse_documents", "extract", "retrieve", "plan", "check", "critic", "route_review", "report"},
        )
        self.assertLess(pipeline.NODE_NAMES.index("plan"), pipeline.NODE_NAMES.index("check"))
        self.assertLess(pipeline.NODE_NAMES.index("critic"), pipeline.NODE_NAMES.index("report"))


class PlannerTests(unittest.TestCase):
    def test_plan_only_uses_whitelisted_tools(self):
        plan = build_plan("task-plan", fields={"invoice_amount": "520.00"}, policy_evidence=[])

        self.assertTrue(all(step.tool in allowed_tools() for step in plan.steps))
        self.assertEqual(validate_plan(plan), [])
        self.assertEqual(len(plan.steps), 6)

    def test_plan_marks_steps_with_missing_fields_as_may_skip(self):
        plan = build_plan("task-plan", fields={}, policy_evidence=[])

        by_tool = {step.tool: step for step in plan.steps}
        self.assertTrue(by_tool["hotel_limit"].may_skip)
        self.assertTrue(by_tool["amount_match"].may_skip)
        self.assertFalse(by_tool["required_documents"].may_skip)
        self.assertTrue(any("缺少字段" in note for note in plan.notes))

    def test_plan_with_full_fields_has_no_skip_candidates(self):
        fields = {
            "invoice_amount": "520.00", "payment_amount": "520.00", "invoice_date": "2026-08-01",
            "travel_start_date": "2026-08-01", "travel_end_date": "2026-08-02",
            "applicant_name": "张三", "invoice_number": "INV-1",
        }
        evidence = [object()]

        plan = build_plan("task-plan", fields=fields, policy_evidence=evidence)

        self.assertFalse(any(step.may_skip for step in plan.steps))

    def test_validate_plan_rejects_unknown_tool(self):
        plan = AuditPlan(
            task_id="t",
            steps=[PlanStep(order=1, name="check_x", tool="not_a_tool", reason="x")],
        )

        issues = validate_plan(plan)

        self.assertTrue(any("未注册工具" in issue for issue in issues))

    def test_plan_is_attached_to_report(self):
        isolate_registry()
        report = run_audit(load_files("normal"), task_id="task-plan-report")

        self.assertIsNotNone(report.audit_plan)
        self.assertEqual(report.audit_plan.created_by, "rules")
        self.assertEqual(report.audit_plan.status, "ready")


class CriticTests(unittest.TestCase):
    def _state(self, checks=None, risks=None):
        return {"checks": checks or [], "risks": risks or [], "trace": []}

    def test_critic_passes_when_all_checks_conclude(self):
        class Check:
            name = "amount_match"
            passed = True

        state = self._state(checks=[Check()], risks=[])

        result = agent_nodes.critic(state)["critique"]

        self.assertTrue(result.complete)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.evidence_coverage, 1.0)

    def test_critic_reports_skipped_checks_and_evidence_gaps(self):
        class Skipped:
            name = "hotel_limit"
            passed = None

        risk = RiskItem(risk_type="AMOUNT_MATCH", level="HIGH", reason="差额", evidence_refs=[])
        state = self._state(checks=[Skipped()], risks=[risk])

        result = agent_nodes.critic(state)["critique"]

        self.assertFalse(result.complete)
        self.assertTrue(any("无结论" in issue for issue in result.issues))
        self.assertTrue(any("缺少材料/规则证据" in issue for issue in result.issues))
        self.assertEqual(result.evidence_coverage, 0.0)

    def test_critic_flags_hotel_limit_without_policy_ref(self):
        risk = RiskItem(risk_type="HOTEL_LIMIT", level="MEDIUM", reason="超标",
                        evidence_refs=["invoice_amount"], policy_refs=[])
        state = self._state(checks=[], risks=[risk])

        result = agent_nodes.critic(state)["critique"]

        self.assertEqual(result.missing_policy_risks, ["HOTEL_LIMIT"])


class RouteReviewTests(unittest.TestCase):
    def _state(self, risks=None, control=None, tool_calls=None, started_at=None):
        return {
            "risks": risks or [],
            "checks": [],
            "tool_calls": tool_calls or [],
            "control": control or AgentControl(),
            "trace": [],
            "started_at": started_at,
        }

    def test_continue_when_no_risk(self):
        state = self._state()

        control = agent_nodes.route_review(state)["control"]

        self.assertEqual(control.route, "continue")
        self.assertIn("自动完成", control.route_reason)

    def test_review_when_blocking_risk_present(self):
        risk = RiskItem(risk_type="DUPLICATE_INVOICE", level="HIGH", reason="重复发票")
        state = self._state(risks=[risk])

        control = agent_nodes.route_review(state)["control"]

        self.assertEqual(control.route, "review")
        self.assertIn("HIGH", control.route_reason)

    def test_fail_on_no_progress(self):
        calls = [
            ToolCallRecord(tool_name="amount_match", status="success", summary="x",
                           input_summary={"invoice_amount": "1.00"}),
            ToolCallRecord(tool_name="amount_match", status="success", summary="x",
                           input_summary={"invoice_amount": "1.00"}),
        ]
        state = self._state(tool_calls=calls)

        control = agent_nodes.route_review(state)["control"]

        self.assertEqual(control.route, "fail")
        self.assertIn("无进展", control.route_reason)

    def test_progress_is_tracked_when_inputs_differ(self):
        calls = [
            ToolCallRecord(tool_name="amount_match", status="success", summary="x",
                           input_summary={"invoice_amount": "1.00"}),
            ToolCallRecord(tool_name="amount_match", status="success", summary="y",
                           input_summary={"invoice_amount": "2.00"}),
        ]
        state = self._state(tool_calls=calls)

        self.assertEqual(agent_nodes.route_review(state)["control"].route, "continue")

    def test_fail_on_timeout_budget(self):
        from time import perf_counter

        control = AgentControl(timeout_seconds=0.01)
        state = self._state(control=control, started_at=perf_counter() - 5)

        result = agent_nodes.route_review(state)["control"]

        self.assertEqual(result.route, "fail")
        self.assertIn("超时", result.route_reason)

    def test_fail_on_token_budget(self):
        control = AgentControl(token_budget=100, tokens_used=100)
        state = self._state(control=control)

        result = agent_nodes.route_review(state)["control"]

        self.assertEqual(result.route, "fail")
        self.assertIn("Token 预算", result.route_reason)

    def test_fail_route_marks_report_failed(self):
        state = self._state(control=AgentControl(route="fail", route_reason="测试终止"),
                            risks=[])
        state.update({"task_id": "task-fail", "fields": {}, "policy_evidence": [], "checks": [],
                      "human_review_items": [], "tool_calls": [], "audit_plan": None, "critique": None})

        report = agent_nodes.report(state)["report"]

        self.assertEqual(report.status, "FAILED")
        self.assertEqual(report.failure_reason, "测试终止")


class ToolRetryTests(unittest.TestCase):
    def test_idempotent_tool_is_retried_then_succeeds(self):
        os.environ["TOOL_RETRY_BACKOFF_SECONDS"] = "0"
        attempts = {"count": 0}
        original = agent_nodes.get_tool("amount_match")

        class Flaky:
            spec = original.spec

            def execute(self, **kwargs):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise RuntimeError("transient")
                return original.execute(**kwargs)

        state = {"control": AgentControl(), "trace": [], "tool_calls": []}
        with patch.object(agent_nodes, "get_tool", return_value=Flaky()):
            execution = agent_nodes._execute_with_retry(state, "amount_match",
                                                       {"invoice_amount": "1.00", "payment_amount": "1.00"})

        self.assertEqual(attempts["count"], 2)
        self.assertEqual(state["control"].tool_retry_count, 1)
        self.assertTrue(execution.result.passed)
        self.assertTrue(any("tool_retry" in item for item in state["trace"]))

    def test_retry_budget_exhausted_raises(self):
        os.environ["TOOL_RETRY_BACKOFF_SECONDS"] = "0"
        original = agent_nodes.get_tool("date_range")

        class Broken:
            spec = original.spec

            def execute(self, **kwargs):
                raise RuntimeError("always broken")

        state = {"control": AgentControl(max_tool_retries=2), "trace": [], "tool_calls": []}
        with patch.object(agent_nodes, "get_tool", return_value=Broken()):
            with self.assertRaisesRegex(RuntimeError, "always broken"):
                agent_nodes._execute_with_retry(state, "date_range", {})

        self.assertEqual(state["control"].tool_retry_count, 2)


if __name__ == "__main__":
    unittest.main()
