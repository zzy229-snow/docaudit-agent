"""LLM field extraction robustness evaluation.

This runner focuses on the model-assisted field extraction layer instead of the
full audit workflow. It is designed for real API adoption: after configuring an
OpenAI-compatible model, run this module to measure JSON failures, schema
failures, hallucinated evidence, low-confidence rejections and fallback usage.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.evaluation.runner import DEFAULT_CASES_PATH, EvaluationCase, load_case_files, load_cases
from app.extraction.llm_field_extractor import classify_rejection, extract_fields_with_llm_diagnostics
from app.parsers.loader import parse_document
from app.services.model_gateway import ModelGateway


REPO_ROOT = Path(__file__).resolve().parents[2]


class LlmCaseResult(BaseModel):
    case_id: str
    name: str
    provider: str
    model: str
    attempts: int
    accepted_fields: list[str]
    rejected_fields: dict[str, str]
    errors: list[str]
    error_types: list[str]
    fallback_used: bool
    expected_fields: dict[str, str]
    matched_fields: list[str]
    missing_expected_fields: list[str]
    unexpected_fields: list[str]
    wrong_fields: dict[str, dict[str, str | None]]

    @property
    def passed(self) -> bool:
        return not self.fallback_used and not self.missing_expected_fields and not self.wrong_fields


class LlmEvalMetrics(BaseModel):
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    fallback_cases: int
    json_format_errors: int = 0
    schema_validation_errors: int = 0
    model_call_errors: int = 0
    low_confidence_rejections: int = 0
    hallucinated_evidence_rejections: int = 0
    missing_source_text_rejections: int = 0
    unknown_field_rejections: int = 0
    invalid_format_rejections: int = 0
    accepted_field_count: int = 0
    rejected_field_count: int = 0


class LlmEvalReport(BaseModel):
    cases_path: str
    provider: str
    model: str
    metrics: LlmEvalMetrics
    results: list[LlmCaseResult]


def run_llm_case(case: EvaluationCase, gateway: ModelGateway | None = None, repo_root: Path = REPO_ROOT) -> LlmCaseResult:
    gateway = gateway or ModelGateway()
    documents = [parse_document(file_name, content) for file_name, content in load_case_files(repo_root / str(case.documents_dir))]
    outcome = extract_fields_with_llm_diagnostics(documents, gateway=gateway)
    actual_fields = {name: str(field.value) for name, field in outcome.fields.items()}
    expected_fields = {name: str(value) for name, value in case.expected_fields.items()}

    matched_fields = sorted(
        name for name, expected in expected_fields.items()
        if actual_fields.get(name) == expected
    )
    missing_expected_fields = sorted(name for name in expected_fields if name not in actual_fields)
    unexpected_fields = sorted(name for name in actual_fields if name not in expected_fields)
    wrong_fields = {
        name: {"expected": expected, "actual": actual_fields.get(name)}
        for name, expected in expected_fields.items()
        if name in actual_fields and actual_fields.get(name) != expected
    }

    return LlmCaseResult(
        case_id=str(case.case_id),
        name=case.name,
        provider=gateway.provider,
        model=gateway.model,
        attempts=outcome.diagnostics.attempts,
        accepted_fields=sorted(outcome.diagnostics.accepted_fields),
        rejected_fields=outcome.diagnostics.rejected_fields,
        errors=outcome.diagnostics.errors,
        error_types=outcome.diagnostics.error_types,
        fallback_used=outcome.diagnostics.fallback_used,
        expected_fields=expected_fields,
        matched_fields=matched_fields,
        missing_expected_fields=missing_expected_fields,
        unexpected_fields=unexpected_fields,
        wrong_fields=wrong_fields,
    )


def run_llm_evaluation(
    cases_path: Path = DEFAULT_CASES_PATH,
    gateway: ModelGateway | None = None,
    repo_root: Path = REPO_ROOT,
) -> LlmEvalReport:
    gateway = gateway or ModelGateway()
    cases = load_cases(cases_path)
    results = [run_llm_case(case, gateway=gateway, repo_root=repo_root) for case in cases]
    return LlmEvalReport(
        cases_path=str(cases_path),
        provider=gateway.provider,
        model=gateway.model,
        metrics=_build_metrics(results),
        results=results,
    )


def report_to_markdown(report: LlmEvalReport) -> str:
    lines = [
        "# LLM Field Extraction Robustness Report",
        "",
        f"- Provider：{report.provider}",
        f"- Model：{report.model or '-'}",
        f"- 用例数：{report.metrics.total_cases}",
        f"- 通过数：{report.metrics.passed_cases}",
        f"- 通过率：{report.metrics.pass_rate:.2%}",
        f"- fallback 用例数：{report.metrics.fallback_cases}",
        f"- JSON 格式错误：{report.metrics.json_format_errors}",
        f"- Schema 校验错误：{report.metrics.schema_validation_errors}",
        f"- 模型调用错误：{report.metrics.model_call_errors}",
        f"- 低置信拒收：{report.metrics.low_confidence_rejections}",
        f"- 幻觉证据拒收：{report.metrics.hallucinated_evidence_rejections}",
        f"- 缺少 source_text 拒收：{report.metrics.missing_source_text_rejections}",
        f"- 未知字段拒收：{report.metrics.unknown_field_rejections}",
        f"- 格式非法拒收：{report.metrics.invalid_format_rejections}",
        f"- 接收字段数：{report.metrics.accepted_field_count}",
        f"- 拒收字段数：{report.metrics.rejected_field_count}",
        "",
        "| Case | Result | Attempts | Accepted | Rejected | Missing expected | Wrong fields | Errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.results:
        lines.append(
            "| {case} | {result} | {attempts} | {accepted} | {rejected} | {missing} | {wrong} | {errors} |".format(
                case=item.case_id,
                result="PASS" if item.passed else "FAIL",
                attempts=item.attempts,
                accepted=len(item.accepted_fields),
                rejected=len(item.rejected_fields),
                missing=", ".join(item.missing_expected_fields) or "-",
                wrong=", ".join(item.wrong_fields) or "-",
                errors=", ".join(item.error_types) or "-",
            )
        )
    return "\n".join(lines)


def render_report(report: LlmEvalReport, output_format: str) -> str:
    if output_format == "markdown":
        return report_to_markdown(report)
    return json.dumps(report.model_dump(), ensure_ascii=False, indent=2)


def write_report(report: LlmEvalReport, output_path: Path, output_format: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(report, output_format), encoding="utf-8")


def _build_metrics(results: list[LlmCaseResult]) -> LlmEvalMetrics:
    total = len(results)
    passed = sum(1 for item in results if item.passed)
    error_counts = Counter(error_type for item in results for error_type in item.error_types)
    rejection_counts = Counter(
        classify_rejection(reason)
        for item in results
        for reason in item.rejected_fields.values()
    )
    accepted_field_count = sum(len(item.accepted_fields) for item in results)
    rejected_field_count = sum(len(item.rejected_fields) for item in results)

    return LlmEvalMetrics(
        total_cases=total,
        passed_cases=passed,
        failed_cases=total - passed,
        pass_rate=round(passed / total, 4) if total else 0.0,
        fallback_cases=sum(1 for item in results if item.fallback_used),
        json_format_errors=error_counts["json_format_error"],
        schema_validation_errors=error_counts["schema_validation_error"],
        model_call_errors=error_counts["model_call_error"],
        low_confidence_rejections=rejection_counts["low_confidence"],
        hallucinated_evidence_rejections=rejection_counts["source_text_not_found"],
        missing_source_text_rejections=rejection_counts["missing_source_text"],
        unknown_field_rejections=rejection_counts["unknown_field"],
        invalid_format_rejections=rejection_counts["invalid_format"],
        accepted_field_count=accepted_field_count,
        rejected_field_count=rejected_field_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LLM field extraction robustness evaluation.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to evaluation cases JSON.")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format.")
    parser.add_argument("--output", type=Path, help="Optional path to save the LLM evaluation report.")
    parser.add_argument("--fail-on-regression", action="store_true", help="Exit 1 when any LLM eval case fails.")
    args = parser.parse_args()

    report = run_llm_evaluation(cases_path=args.cases)
    rendered = render_report(report, args.format)
    print(rendered)
    if args.output:
        write_report(report, args.output, args.format)
    if args.fail_on_regression and report.metrics.failed_cases:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
