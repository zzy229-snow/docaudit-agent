from fastapi import FastAPI, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.agent.graph import run_audit
from app.api.store import AuditTask, task_store
from app.models.audit import CheckResult, RiskItem
from app.models.field import ExtractedField
from app.services.audit_summary import AuditExplanationSummary, build_audit_summary


app = FastAPI(title="DocAudit Agent API", version="0.1.0")


class TaskSummary(BaseModel):
    task_id: str
    status: str
    file_count: int
    created_at: str
    updated_at: str
    error: str | None = None
    result_status: str | None = None


class UploadResponse(TaskSummary):
    uploaded_file: str


class RunResponse(TaskSummary):
    checks: list[CheckResult]
    risks: list[RiskItem]


class FieldsResponse(BaseModel):
    task_id: str
    fields: dict[str, ExtractedField]


class RisksResponse(BaseModel):
    task_id: str
    risks: list[RiskItem]


class TraceResponse(BaseModel):
    task_id: str
    trace: list[str]


class SummaryResponse(BaseModel):
    task_id: str
    summary: AuditExplanationSummary


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/audit-tasks", response_model=TaskSummary, status_code=status.HTTP_201_CREATED)
def create_audit_task() -> TaskSummary:
    return _task_summary(task_store.create_task())


@app.post("/api/v1/audit-tasks/{task_id}/documents", response_model=UploadResponse)
async def upload_document(task_id: str, file: UploadFile = File(...)) -> UploadResponse:
    task = _require_task(task_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    updated = task_store.add_file(task.task_id, file.filename or "upload.bin", content)
    return UploadResponse(**_task_summary(updated).model_dump(), uploaded_file=file.filename or "upload.bin")


@app.post("/api/v1/audit-tasks/{task_id}/run", response_model=RunResponse)
def run_audit_task(task_id: str) -> RunResponse:
    task = _require_task(task_id)
    if not task.files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task has no uploaded documents")
    try:
        report = run_audit([(item.file_name, item.content) for item in task.files])
    except (ValueError, RuntimeError) as exc:
        failed = task_store.save_error(task.task_id, str(exc))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=failed.error) from exc
    updated = task_store.save_report(task.task_id, report)
    return RunResponse(**_task_summary(updated).model_dump(), checks=report.checks, risks=report.risks)


@app.get("/api/v1/audit-tasks/{task_id}", response_model=TaskSummary)
def get_audit_task(task_id: str) -> TaskSummary:
    return _task_summary(_require_task(task_id))


@app.get("/api/v1/audit-tasks/{task_id}/fields", response_model=FieldsResponse)
def get_fields(task_id: str) -> FieldsResponse:
    task = _require_completed_task(task_id)
    return FieldsResponse(task_id=task.task_id, fields=task.report.fields)


@app.get("/api/v1/audit-tasks/{task_id}/risks", response_model=RisksResponse)
def get_risks(task_id: str) -> RisksResponse:
    task = _require_completed_task(task_id)
    return RisksResponse(task_id=task.task_id, risks=task.report.risks)


@app.get("/api/v1/audit-tasks/{task_id}/trace", response_model=TraceResponse)
def get_trace(task_id: str) -> TraceResponse:
    task = _require_completed_task(task_id)
    return TraceResponse(task_id=task.task_id, trace=task.report.trace)


@app.get("/api/v1/audit-tasks/{task_id}/summary", response_model=SummaryResponse)
def get_summary(task_id: str) -> SummaryResponse:
    task = _require_completed_task(task_id)
    return SummaryResponse(task_id=task.task_id, summary=build_audit_summary(task.report))


def _require_task(task_id: str) -> AuditTask:
    try:
        return task_store.require_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit task not found") from exc


def _require_completed_task(task_id: str) -> AuditTask:
    task = _require_task(task_id)
    if task.report is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Audit task has not completed")
    return task


def _task_summary(task: AuditTask) -> TaskSummary:
    result_status: str | None = task.report.status if task.report else None
    return TaskSummary(
        task_id=task.task_id,
        status=task.status,
        file_count=len(task.files),
        created_at=task.created_at.isoformat(),
        updated_at=task.updated_at.isoformat(),
        error=task.error,
        result_status=result_status,
    )
