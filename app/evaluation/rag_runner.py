"""RAG retrieval evaluation runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from app.evaluation.runner import DEFAULT_CASES_PATH, EvaluationCase, load_cases
from app.rag.local_store import retrieve_local
from app.rag.retriever import retrieve_policy


class RagCaseResult(BaseModel):
    case_id: str
    name: str
    query: str
    expected_policy_refs: list[str]
    actual_policy_refs: list[str]
    missing_policy_refs: list[str]
    passed: bool


class RagEvalMetrics(BaseModel):
    total_cases: int
    evaluated_cases: int
    passed_cases: int
    failed_cases: int
    hit_rate: float


class RagEvalReport(BaseModel):
    cases_path: str
    mode: str
    metrics: RagEvalMetrics
    results: list[RagCaseResult]


def run_rag_case(case: EvaluationCase, mode: str = "local") -> RagCaseResult | None:
    if not case.expected_policy_refs:
        return None
    query = _query_for_case(case)
    evidence = _retrieve(query, mode)
    actual_refs = sorted({item.chunk_id for item in evidence})
    expected_refs = sorted(case.expected_policy_refs)
    missing = sorted(set(expected_refs) - set(actual_refs))
    return RagCaseResult(
        case_id=str(case.case_id),
        name=case.name,
        query=query,
        expected_policy_refs=expected_refs,
        actual_policy_refs=actual_refs,
        missing_policy_refs=missing,
        passed=not missing,
    )


def run_rag_evaluation(cases_path: Path = DEFAULT_CASES_PATH, mode: str = "local") -> RagEvalReport:
    cases = load_cases(cases_path)
    results = [result for case in cases if (result := run_rag_case(case, mode=mode)) is not None]
    total = len(load_cases(cases_path))
    evaluated = len(results)
    passed = sum(1 for item in results if item.passed)
    return RagEvalReport(
        cases_path=str(cases_path),
        mode=mode,
        metrics=RagEvalMetrics(
            total_cases=total,
            evaluated_cases=evaluated,
            passed_cases=passed,
            failed_cases=evaluated - passed,
            hit_rate=round(passed / evaluated, 4) if evaluated else 0.0,
        ),
        results=results,
    )


def report_to_markdown(report: RagEvalReport) -> str:
    lines = [
        "# RAG Retrieval Evaluation Report",
        "",
        f"- 总用例数：{report.metrics.total_cases}",
        f"- 检索模式：{report.mode}",
        f"- 检索评测用例数：{report.metrics.evaluated_cases}",
        f"- 命中用例数：{report.metrics.passed_cases}",
        f"- 命中率：{report.metrics.hit_rate:.2%}",
        "",
        "| Case | Result | Query | Expected refs | Actual refs | Missing refs |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.results:
        lines.append(
            "| {case} | {result} | {query} | {expected} | {actual} | {missing} |".format(
                case=item.case_id,
                result="PASS" if item.passed else "FAIL",
                query=item.query,
                expected=", ".join(item.expected_policy_refs),
                actual=", ".join(item.actual_policy_refs) or "-",
                missing=", ".join(item.missing_policy_refs) or "-",
            )
        )
    return "\n".join(lines)


def render_report(report: RagEvalReport, output_format: str) -> str:
    if output_format == "markdown":
        return report_to_markdown(report)
    return json.dumps(report.model_dump(), ensure_ascii=False, indent=2)


def write_report(report: RagEvalReport, output_path: Path, output_format: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(report, output_format), encoding="utf-8")


def _query_for_case(case: EvaluationCase) -> str:
    field_text = " ".join(f"{key}:{value}" for key, value in case.expected_fields.items())
    risk_text = " ".join(case.expected_risks)
    return f"{case.name} {risk_text} {field_text} 住宿标准 差旅报销 制度条款"


def _retrieve(query: str, mode: str):
    if mode == "local":
        return retrieve_local(query, limit=2)
    return retrieve_policy(city=None, query=query)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAG retrieval evaluation cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to evaluation cases JSON.")
    parser.add_argument("--mode", choices=["local", "configured"], default="local", help="RAG backend to evaluate.")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format.")
    parser.add_argument("--output", type=Path, help="Optional path to save the RAG evaluation report.")
    parser.add_argument("--fail-on-regression", action="store_true", help="Exit 1 when any RAG eval case fails.")
    args = parser.parse_args()

    report = run_rag_evaluation(cases_path=args.cases, mode=args.mode)
    rendered = render_report(report, args.format)
    print(rendered)
    if args.output:
        write_report(report, args.output, args.format)
    if args.fail_on_regression and report.metrics.failed_cases:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
