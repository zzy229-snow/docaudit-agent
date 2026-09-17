from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from app.models.audit import AuditReport


TaskStatus = Literal["CREATED", "READY", "COMPLETED", "FAILED"]


@dataclass
class StoredFile:
    file_name: str
    content: bytes


@dataclass
class AuditTask:
    task_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    files: list[StoredFile] = field(default_factory=list)
    report: AuditReport | None = None
    error: str | None = None


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
        task.updated_at = _now()
        return task

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


task_store = InMemoryAuditTaskStore()
