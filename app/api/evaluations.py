"""离线评测接口(PRD §12.2 ``POST /api/v1/evaluations/run``)。

把 ``app.evaluation.runner`` 的回归评测暴露成 HTTP 接口,便于 CI 与演示调用。
安全约束(PRD §15):
- ``cases_path`` 必须落在仓库目录内,防止路径穿越读取任意文件;
- 用例集不存在时返回 404,而不是静默跑默认集。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.evaluation.runner import (
    DEFAULT_CASES_PATH,
    REPO_ROOT,
    run_evaluation,
)
from app.evaluation.runner import report_to_markdown

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


class EvaluationRunRequest(BaseModel):
    cases_path: str | None = None
    format: Literal["json", "markdown", "both"] = "json"


class EvaluationRunResponse(BaseModel):
    cases_path: str
    format: str
    metrics: dict
    results: list | None = None
    markdown: str | None = None


def _resolve_cases_path(raw: str | None) -> Path:
    if not raw:
        return DEFAULT_CASES_PATH
    candidate = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if REPO_ROOT.resolve() not in candidate.parents and candidate != REPO_ROOT.resolve():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="cases_path 必须位于仓库目录内")
    if not candidate.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例集文件不存在")
    return candidate


@router.post("/run", response_model=EvaluationRunResponse)
def run_evaluation_endpoint(request: EvaluationRunRequest | None = None) -> EvaluationRunResponse:
    """运行离线回归评测并返回指标(默认用例集,可按仓库内相对路径指定)。"""
    payload = request or EvaluationRunRequest()
    cases_path = _resolve_cases_path(payload.cases_path)
    report = run_evaluation(cases_path=cases_path, repo_root=REPO_ROOT)
    return EvaluationRunResponse(
        cases_path=str(cases_path),
        format=payload.format,
        metrics=report.metrics.model_dump(),
        results=[item.model_dump() for item in report.results] if payload.format in ("json", "both") else None,
        markdown=report_to_markdown(report) if payload.format in ("markdown", "both") else None,
    )
