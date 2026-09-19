"""End-to-end evaluation runner for the audit agent.

The runner intentionally evaluates the public ``run_audit`` workflow instead of
testing individual nodes. This makes it useful as a regression baseline before
switching mock components to real OCR / LLM / vector retrieval providers.
"""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from pydantic import BaseModel, Field, model_validator

from app.agent.graph import run_audit
from app.services.invoice_registry import isolated_registry


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = REPO_ROOT / "data" / "evaluation" / "cases.json"


@contextmanager
def _maybe_isolated_registry(isolate: bool) -> Iterator[None]:
    """按需隔离发票查重登记表,保证重复发票用例每次运行都从干净历史开始。"""
    if not isolate:
        yield
        return
    with isolated_registry():
        yield


class EvaluationCase(BaseModel):
    """A single expected outcome for an end-to-end demo audit case."""

    case_id: str | None = None
    case: str | None = None
    name: str = ""
    documents_dir: str | None = None
    expected_status: str
    expected_risks: list[str] = Field(default_factory=list)
    expected_fields: dict[str, str] = Field(default_factory=dict)
    expected_policy_refs: list[str] = Field(default_factory=list)
    #: 用例类别(PRD §13.1/§25.4 的覆盖维度,如 正常合规/材料缺失/金额不一致/指令注入)
    category: str = "uncategorized"
    #: 期望被调用的工具名(PRD §13.2 工具选择准确率);为空则不参与该指标
    expected_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fill_backward_compatible_fields(self) -> "EvaluationCase":
        """Support the original compact case format while preferring explicit IDs."""
        if self.case_id is None and self.case:
            self.case_id = self.case
        if self.documents_dir is None and self.case:
            self.documents_dir = f"data/demo/{self.case}"
        if not self.case_id:
            raise ValueError("case_id is required")
        if not self.documents_dir:
            raise ValueError("documents_dir is required")
        return self


class FieldMismatch(BaseModel):
    field: str
    expected: str
    actual: str | None


class CaseResult(BaseModel):
    case_id: str
    name: str
    passed: bool
    expected_status: str
    actual_status: str
    expected_risks: list[str]
    actual_risks: list[str]
    missing_risks: list[str]
    unexpected_risks: list[str]
    expected_policy_refs: list[str]
    actual_policy_refs: list[str]
    missing_policy_refs: list[str]
    field_mismatches: list[FieldMismatch]
    trace_tail: list[str]
    #: 用例类别(来自 EvaluationCase.category)
    category: str = "uncategorized"
    #: 端到端耗时(PRD §13.2 效率指标)
    duration_ms: float = 0.0
    #: 工具执行统计(PRD §13.2 工具执行成功率)
    tool_total: int = 0
    tool_passed: int = 0
    #: 参数缺失/无结论(CheckResult.passed is None)的检查项数量
    tool_skipped: int = 0
    #: 期望工具是否都被调用(None = 用例未声明 expected_tools)
    tool_selection_passed: bool | None = None
    #: 运行异常(如达到 max_steps 被安全终止)
    error: str | None = None

    @property
    def status_passed(self) -> bool:
        return self.expected_status == self.actual_status

    @property
    def risks_passed(self) -> bool:
        return not self.missing_risks and not self.unexpected_risks

    @property
    def fields_passed(self) -> bool:
        return not self.field_mismatches

    @property
    def policy_refs_passed(self) -> bool:
        return not self.missing_policy_refs


class EvaluationMetrics(BaseModel):
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    status_accuracy: float
    risk_accuracy: float
    field_accuracy: float
    policy_ref_accuracy: float
    # --- PRD §13.2 业务/Agent/效率/安全指标 ---
    #: 风险标签级 Precision/Recall/F1(全部用例的 TP/FP/FN 汇总,按风险类型计数)
    risk_precision: float = 0.0
    risk_recall: float = 0.0
    risk_f1: float = 0.0
    #: 工具执行成功率 = 参数齐全并给出结论的检查项 ÷ 已执行检查项(PRD §1.4)
    tool_success_rate: float = 0.0
    #: 参数缺失(字段不全)导致跳过的检查项数量与比例
    tool_skipped_total: int = 0
    tool_param_missing_rate: float = 0.0
    #: 工具选择准确率 = 声明了 expected_tools 的用例中,期望工具全部被调用的比例
    tool_selection_accuracy: float | None = None
    tool_calls_total: int = 0
    #: 需要人工复核的用例比例(PRD §13.2 人工介入率)
    review_rate: float = 0.0
    #: 端到端耗时(毫秒,最近排名法)
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_max_ms: float = 0.0
    #: 达到 max_steps 被安全终止的比例(PRD §1.4 非预期死循环率)
    dead_loop_rate: float = 0.0
    error_cases: int = 0
    #: 各类型的用例数与通过数,用于展示覆盖度(PRD §25.4)
    categories: dict[str, int] = Field(default_factory=dict)
    category_passed: dict[str, int] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    cases_path: str
    metrics: EvaluationMetrics
    results: list[CaseResult]


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvaluationCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    return [EvaluationCase.model_validate(item) for item in raw_cases]


def load_case_files(documents_dir: Path) -> list[tuple[str, bytes]]:
    if not documents_dir.exists():
        raise FileNotFoundError(f"evaluation documents_dir does not exist: {documents_dir}")
    files = [(path.name, path.read_bytes()) for path in sorted(documents_dir.iterdir()) if path.is_file()]
    if not files:
        raise ValueError(f"evaluation documents_dir is empty: {documents_dir}")
    return files


def run_case(case: EvaluationCase, repo_root: Path = REPO_ROOT) -> CaseResult:
    documents_dir = repo_root / str(case.documents_dir)
    started_at = perf_counter()
    error: str | None = None
    try:
        report = run_audit(load_case_files(documents_dir), task_id=f"eval-{case.case_id}")
    except Exception as exc:  # noqa: BLE001 — 异常用例必须计入评测,而不是让评测崩溃
        error = f"{type(exc).__name__}: {exc}"
        return CaseResult(
            case_id=str(case.case_id),
            name=case.name,
            passed=False,
            expected_status=case.expected_status,
            actual_status="ERROR",
            expected_risks=sorted(case.expected_risks),
            actual_risks=[],
            missing_risks=sorted(case.expected_risks),
            unexpected_risks=[],
            expected_policy_refs=sorted(case.expected_policy_refs),
            actual_policy_refs=[],
            missing_policy_refs=sorted(case.expected_policy_refs),
            field_mismatches=[],
            trace_tail=[],
            category=case.category,
            duration_ms=round((perf_counter() - started_at) * 1000, 1),
            error=error,
        )
    duration_ms = round((perf_counter() - started_at) * 1000, 1)

    actual_fields = {name: _normalize_value(field.value) for name, field in report.fields.items()}
    field_mismatches = [
        FieldMismatch(field=name, expected=str(expected), actual=actual_fields.get(name))
        for name, expected in case.expected_fields.items()
        if actual_fields.get(name) != str(expected)
    ]

    actual_risks = sorted({risk.risk_type for risk in report.risks})
    expected_risks = sorted(case.expected_risks)
    missing_risks = sorted(set(expected_risks) - set(actual_risks))
    unexpected_risks = sorted(set(actual_risks) - set(expected_risks))

    actual_policy_refs = sorted(
        {
            *[item.chunk_id for item in report.policy_evidence],
            *[ref for risk in report.risks for ref in risk.policy_refs],
        }
    )
    expected_policy_refs = sorted(case.expected_policy_refs)
    missing_policy_refs = sorted(set(expected_policy_refs) - set(actual_policy_refs))

    passed = (
        report.status == case.expected_status
        and not missing_risks
        and not unexpected_risks
        and not field_mismatches
        and not missing_policy_refs
    )

    tool_names = {check.name for check in report.checks}
    tool_passed = sum(1 for check in report.checks if check.passed is True)
    tool_skipped = sum(1 for check in report.checks if check.passed is None)
    tool_selection_passed = (
        None
        if not case.expected_tools
        else {name.lower() for name in case.expected_tools}.issubset(tool_names)
    )

    return CaseResult(
        case_id=str(case.case_id),
        name=case.name,
        passed=passed,
        expected_status=case.expected_status,
        actual_status=report.status,
        expected_risks=expected_risks,
        actual_risks=actual_risks,
        missing_risks=missing_risks,
        unexpected_risks=unexpected_risks,
        expected_policy_refs=expected_policy_refs,
        actual_policy_refs=actual_policy_refs,
        missing_policy_refs=missing_policy_refs,
        field_mismatches=field_mismatches,
        trace_tail=report.trace[-5:],
        category=case.category,
        duration_ms=duration_ms,
        tool_total=len(report.checks),
        tool_passed=tool_passed,
        tool_skipped=tool_skipped,
        tool_selection_passed=tool_selection_passed,
    )


def run_evaluation(
    cases_path: Path = DEFAULT_CASES_PATH,
    repo_root: Path = REPO_ROOT,
    isolate_invoice_registry: bool = True,
) -> EvaluationReport:
    """运行全部用例。

    ``isolate_invoice_registry`` 默认开启:把发票查重登记表指向临时文件,
    这样重复发票用例在每次运行中都从干净历史开始,结论可复现。
    """
    with _maybe_isolated_registry(isolate_invoice_registry):
        cases = load_cases(cases_path)
        results = [run_case(case, repo_root=repo_root) for case in cases]
    total = len(results)
    passed_cases = sum(1 for item in results if item.passed)
    durations = [item.duration_ms for item in results]
    tool_total = sum(item.tool_total for item in results)
    tool_passed_total = sum(item.tool_passed for item in results)
    tool_skipped_total = sum(item.tool_skipped for item in results)
    tool_executed = tool_total - tool_skipped_total
    selection_cases = [item for item in results if item.tool_selection_passed is not None]

    # 风险标签级 P/R/F1:把所有用例的期望风险与实际风险汇总后按类型计数
    true_positive = missed = extra = 0
    for item in results:
        expected, actual = set(item.expected_risks), set(item.actual_risks)
        true_positive += len(expected & actual)
        missed += len(expected - actual)
        extra += len(actual - expected)
    precision = _ratio(true_positive, true_positive + extra)
    recall = _ratio(true_positive, true_positive + missed)
    f1 = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0

    categories: dict[str, int] = {}
    category_passed: dict[str, int] = {}
    for item in results:
        categories[item.category] = categories.get(item.category, 0) + 1
        if item.passed:
            category_passed[item.category] = category_passed.get(item.category, 0) + 1

    error_cases = sum(1 for item in results if item.error)
    dead_loops = sum(1 for item in results if item.error and "max_steps" in item.error)

    return EvaluationReport(
        cases_path=str(cases_path),
        metrics=EvaluationMetrics(
            total_cases=total,
            passed_cases=passed_cases,
            failed_cases=total - passed_cases,
            pass_rate=_ratio(passed_cases, total),
            status_accuracy=_ratio(sum(1 for item in results if item.status_passed), total),
            risk_accuracy=_ratio(sum(1 for item in results if item.risks_passed), total),
            field_accuracy=_ratio(sum(1 for item in results if item.fields_passed), total),
            policy_ref_accuracy=_ratio(sum(1 for item in results if item.policy_refs_passed), total),
            risk_precision=precision,
            risk_recall=recall,
            risk_f1=f1,
            tool_success_rate=_ratio(tool_passed_total, tool_executed),
            tool_skipped_total=tool_skipped_total,
            tool_param_missing_rate=_ratio(tool_skipped_total, tool_total),
            tool_selection_accuracy=(
                _ratio(sum(1 for item in selection_cases if item.tool_selection_passed), len(selection_cases))
                if selection_cases
                else None
            ),
            tool_calls_total=tool_total,
            review_rate=_ratio(sum(1 for item in results if item.actual_status == "REVIEW_REQUIRED"), total),
            latency_p50_ms=_percentile(durations, 0.50),
            latency_p95_ms=_percentile(durations, 0.95),
            latency_max_ms=round(max(durations), 1) if durations else 0.0,
            dead_loop_rate=_ratio(dead_loops, total),
            error_cases=error_cases,
            categories=dict(sorted(categories.items())),
            category_passed=dict(sorted(category_passed.items())),
        ),
        results=results,
    )


def report_to_markdown(report: EvaluationReport) -> str:
    metrics = report.metrics
    lines = [
        "# Audit Agent Evaluation Report",
        "",
        "## 总览",
        "",
        f"- 用例数：{metrics.total_cases}",
        f"- 通过数：{metrics.passed_cases}",
        f"- 通过率：{metrics.pass_rate:.2%}",
        f"- 状态准确率：{metrics.status_accuracy:.2%}",
        f"- 风险准确率（风险集合完全一致比例）：{metrics.risk_accuracy:.2%}",
        f"- 字段准确率：{metrics.field_accuracy:.2%}",
        f"- 制度引用准确率：{metrics.policy_ref_accuracy:.2%}",
        "",
        "## 业务与Agent指标（PRD §13.2）",
        "",
        f"- 风险 Precision / Recall / F1：{metrics.risk_precision:.2%} / {metrics.risk_recall:.2%} / {metrics.risk_f1:.2%}",
        f"- 工具执行成功率：{metrics.tool_success_rate:.2%}（已执行 {metrics.tool_calls_total - metrics.tool_skipped_total}/{metrics.tool_calls_total}，参数缺失跳过 {metrics.tool_skipped_total} 次 / {metrics.tool_param_missing_rate:.2%}）",
        f"- 工具选择准确率：{'—' if metrics.tool_selection_accuracy is None else f'{metrics.tool_selection_accuracy:.2%}'}",
        f"- 人工复核率：{metrics.review_rate:.2%}",
        f"- 端到端耗时 P50 / P95 / Max：{metrics.latency_p50_ms:.0f}ms / {metrics.latency_p95_ms:.0f}ms / {metrics.latency_max_ms:.0f}ms",
        f"- 非预期死循环率：{metrics.dead_loop_rate:.2%}（异常用例 {metrics.error_cases}）",
        "",
        "## 用例类别覆盖（PRD §25.4）",
        "",
        "| 类别 | 用例数 | 通过数 |",
        "| --- | --- | --- |",
    ]
    for category, count in metrics.categories.items():
        lines.append(f"| {category} | {count} | {metrics.category_passed.get(category, 0)} |")
    lines += [
        "",
        "## 逐例结果",
        "",
        "| Case | 类别 | Status | Risks | Fields | Policy refs | 耗时(ms) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.results:
        lines.append(
            "| {case} | {category} | {status} | {risks} | {fields} | {policy} | {duration} |".format(
                case=item.case_id,
                category=item.category,
                status="PASS" if item.status_passed else f"FAIL actual={item.actual_status}",
                risks="PASS" if item.risks_passed else f"FAIL miss={item.missing_risks}, extra={item.unexpected_risks}",
                fields="PASS" if item.fields_passed else f"FAIL {len(item.field_mismatches)} mismatch",
                policy="PASS" if item.policy_refs_passed else f"FAIL miss={item.missing_policy_refs}",
                duration=f"{item.duration_ms:.0f}",
            )
        )
    return "\n".join(lines)


def render_report(report: EvaluationReport, output_format: str) -> str:
    if output_format == "markdown":
        return report_to_markdown(report)
    return json.dumps(report.model_dump(), ensure_ascii=False, indent=2)


def write_report(report: EvaluationReport, output_path: Path, output_format: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(report, output_format), encoding="utf-8")


def _normalize_value(value: Any) -> str:
    return str(value)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    """最近排名法分位数(样本量小,不引入 numpy 依赖)。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(quantile * (len(ordered) - 1))))
    return round(ordered[index], 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run audit-agent regression evaluation cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to evaluation cases JSON.")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format.")
    parser.add_argument("--output", type=Path, help="Optional path to save the evaluation report.")
    args = parser.parse_args()

    report = run_evaluation(cases_path=args.cases)
    rendered = render_report(report, args.format)
    print(rendered)
    if args.output:
        write_report(report, args.output, args.format)

    if report.metrics.failed_cases:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
