"""End-to-end evaluation runner for the audit agent.

The runner intentionally evaluates the public ``run_audit`` workflow instead of
testing individual nodes. This makes it useful as a regression baseline before
switching mock components to real OCR / LLM / vector retrieval providers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.agent.graph import run_audit


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = REPO_ROOT / "data" / "evaluation" / "cases.json"


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
    report = run_audit(load_case_files(documents_dir))

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
    )


def run_evaluation(
    cases_path: Path = DEFAULT_CASES_PATH,
    repo_root: Path = REPO_ROOT,
) -> EvaluationReport:
    cases = load_cases(cases_path)
    results = [run_case(case, repo_root=repo_root) for case in cases]
    total = len(results)
    passed_cases = sum(1 for item in results if item.passed)

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
        ),
        results=results,
    )


def report_to_markdown(report: EvaluationReport) -> str:
    lines = [
        "# Audit Agent Evaluation Report",
        "",
        f"- 用例数：{report.metrics.total_cases}",
        f"- 通过数：{report.metrics.passed_cases}",
        f"- 通过率：{report.metrics.pass_rate:.2%}",
        f"- 状态准确率：{report.metrics.status_accuracy:.2%}",
        f"- 风险准确率：{report.metrics.risk_accuracy:.2%}",
        f"- 字段准确率：{report.metrics.field_accuracy:.2%}",
        f"- 制度引用准确率：{report.metrics.policy_ref_accuracy:.2%}",
        "",
        "| Case | Status | Risks | Fields | Policy refs |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.results:
        lines.append(
            "| {case} | {status} | {risks} | {fields} | {policy} |".format(
                case=item.case_id,
                status="PASS" if item.status_passed else f"FAIL actual={item.actual_status}",
                risks="PASS" if item.risks_passed else f"FAIL miss={item.missing_risks}, extra={item.unexpected_risks}",
                fields="PASS" if item.fields_passed else f"FAIL {len(item.field_mismatches)} mismatch",
                policy="PASS" if item.policy_refs_passed else f"FAIL miss={item.missing_policy_refs}",
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
