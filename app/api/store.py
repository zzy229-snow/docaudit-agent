from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from app.models.audit import AuditReport, RiskItem


TaskStatus = Literal["CREATED", "READY", "COMPLETED", "FAILED"]
ReviewStatus = Literal["OPEN", "APPROVED", "REJECTED"]


@dataclass
class StoredFile:
    file_name: str
    content: bytes


@dataclass
class FieldCorrection:
    field_name: str
    original_value: str | None
    corrected_value: str
    reason: str
    created_at: datetime


@dataclass
class ReviewItem:
    review_item_id: str
    task_id: str
    risk_type: str
    reason: str
    status: ReviewStatus
    decision: str | None = None
    decided_by: str | None = None
    updated_at: datetime = field(default_factory=lambda: _now())


@dataclass
class AuditTask:
    task_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    files: list[StoredFile] = field(default_factory=list)
    report: AuditReport | None = None
    error: str | None = None
    corrections: dict[str, FieldCorrection] = field(default_factory=dict)
    review_items: list[ReviewItem] = field(default_factory=list)


class InMemoryAuditTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, AuditTask] = {}

    def create_task(self) -> AuditTask:
        now = _now()
        task = AuditTask(task_id=f"task-{uuid4().hex[:12]}", status="CREATED", created_at=now, updated_at=now)
        self._tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> AuditTask | None:
        return self._tasks.get(task_id)

    def add_file(self, task_id: str, file_name: str, content: bytes) -> AuditTask:
        task = self.require_task(task_id)
        task.files.append(StoredFile(file_name=file_name, content=content))
        task.status = "READY"
        task.updated_at = _now()
        return task

    def save_report(self, task_id: str, report: AuditReport) -> AuditTask:
        task = self.require_task(task_id)
        task.report = report
        task.error = None
        task.status = "COMPLETED"
        task.review_items = [_review_item_from_risk(task.task_id, risk) for risk in report.risks]
        task.updated_at = _now()
        return task

    def save_field_correction(self, task_id: str, field_name: str, corrected_value: str, reason: str) -> FieldCorrection:
        task = self.require_task(task_id)
        original_value = None
        if task.report and field_name in task.report.fields:
            original_value = str(task.report.fields[field_name].value)
        correction = FieldCorrection(
            field_name=field_name,
            original_value=original_value,
            corrected_value=corrected_value,
            reason=reason,
            created_at=_now(),
        )
        task.corrections[field_name] = correction
        task.status = "READY"
        task.updated_at = _now()
        return correction

    def field_overrides(self, task_id: str) -> dict[str, dict[str, str]]:
        task = self.require_task(task_id)
        return {
            correction.field_name: {"value": correction.corrected_value, "reason": correction.reason}
            for correction in task.corrections.values()
        }

    def decide_review_item(self, review_item_id: str, decision: ReviewStatus, decided_by: str | None = None) -> ReviewItem:
        for task in self._tasks.values():
            for item in task.review_items:
                if item.review_item_id == review_item_id:
                    item.status = decision
                    item.decision = decision
                    item.decided_by = decided_by
                    item.updated_at = _now()
                    task.updated_at = _now()
                    return item
        raise KeyError(review_item_id)

    def save_error(self, task_id: str, error: str) -> AuditTask:
        task = self.require_task(task_id)
        task.error = error
        task.status = "FAILED"
        task.updated_at = _now()
        return task

    def require_task(self, task_id: str) -> AuditTask:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _review_item_from_risk(task_id: str, risk: RiskItem) -> ReviewItem:
    return ReviewItem(
        review_item_id=f"review-{uuid4().hex[:12]}",
        task_id=task_id,
        risk_type=risk.risk_type,
        reason=risk.reason,
        status="OPEN",
        updated_at=_now(),
    )


task_store = InMemoryAuditTaskStore()
