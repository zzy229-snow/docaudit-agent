from dataclasses import dataclass, field
from datetime import datetime, timezone
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Literal
from uuid import uuid4

from app.models.audit import AuditReport, RiskItem
from app.config import repo_root


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
class AuditEvent:
    event_id: str
    task_id: str
    event_type: str
    message: str
    created_at: datetime


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
    #: FR-001:任务元数据(申请人/部门/报销类型/备注),同时用于 RBAC 对象级鉴权(§15)
    applicant: str = ""
    department: str = ""
    expense_type: str = ""
    note: str = ""


class InMemoryAuditTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, AuditTask] = {}

    def create_task(
        self,
        applicant: str = "",
        department: str = "",
        expense_type: str = "",
        note: str = "",
    ) -> AuditTask:
        now = _now()
        task = AuditTask(
            task_id=f"task-{uuid4().hex[:12]}",
            status="CREATED",
            created_at=now,
            updated_at=now,
            applicant=applicant,
            department=department,
            expense_type=expense_type,
            note=note,
        )
        self._tasks[task.task_id] = task
        return task

    def list_tasks(self, applicant: str | None = None) -> list[AuditTask]:
        tasks = sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
        if applicant:
            tasks = [task for task in tasks if task.applicant == applicant]
        return tasks

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

    def list_events(self, task_id: str) -> list[AuditEvent]:
        self.require_task(task_id)
        return []

    def log_event(self, task_id: str, event_type: str, message: str) -> None:
        """内存存储不保留事件(实现留空,SQlite 版持久化)。"""
        return None


class SQLiteAuditTaskStore(InMemoryAuditTaskStore):
    """SQLite-backed task store for local production-like runs.

    It keeps uploaded files, reports, manual corrections, review decisions and
    event history durable across API process restarts without requiring an
    external database service.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or _default_db_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    report_json TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    content BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES audit_tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS field_corrections (
                    task_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    original_value TEXT,
                    corrected_value TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, field_name),
                    FOREIGN KEY(task_id) REFERENCES audit_tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS review_items (
                    review_item_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    risk_type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT,
                    decided_by TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES audit_tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES audit_tasks(task_id) ON DELETE CASCADE
                );
                """
            )
            self._migrate(conn)

    #: 旧库升级用:新增列(列名, 类型)
    _TASK_EXTRA_COLUMNS = (
        ("applicant", "TEXT DEFAULT ''"),
        ("department", "TEXT DEFAULT ''"),
        ("expense_type", "TEXT DEFAULT ''"),
        ("note", "TEXT DEFAULT ''"),
    )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """轻量迁移:已有 SQLite 库缺少任务元数据列时自动补齐(不需要 Alembic)。"""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(audit_tasks)").fetchall()}
        for column, ddl in self._TASK_EXTRA_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE audit_tasks ADD COLUMN {column} {ddl}")

    def create_task(
        self,
        applicant: str = "",
        department: str = "",
        expense_type: str = "",
        note: str = "",
    ) -> AuditTask:
        now = _now()
        task = AuditTask(
            task_id=f"task-{uuid4().hex[:12]}",
            status="CREATED",
            created_at=now,
            updated_at=now,
            applicant=applicant,
            department=department,
            expense_type=expense_type,
            note=note,
        )
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO audit_tasks(task_id, status, created_at, updated_at, applicant, department,"
                " expense_type, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task.task_id, task.status, _dump_dt(now), _dump_dt(now),
                 applicant, department, expense_type, note),
            )
            self._insert_event(conn, task.task_id, "TASK_CREATED", "创建审核任务")
        return self.require_task(task.task_id)

    def list_tasks(self, applicant: str | None = None) -> list[AuditTask]:
        sql = "SELECT task_id FROM audit_tasks"
        params: list[str] = []
        if applicant:
            sql += " WHERE applicant = ?"
            params.append(applicant)
        sql += " ORDER BY created_at DESC"
        with self._lock, self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self.require_task(row["task_id"]) for row in rows]

    def get_task(self, task_id: str) -> AuditTask | None:
        with self._lock, self._connection() as conn:
            row = conn.execute("SELECT * FROM audit_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            file_rows = conn.execute(
                "SELECT file_name, content FROM audit_files WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
            correction_rows = conn.execute(
                "SELECT * FROM field_corrections WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
            review_rows = conn.execute(
                "SELECT * FROM review_items WHERE task_id = ? ORDER BY updated_at",
                (task_id,),
            ).fetchall()
        return AuditTask(
            task_id=row["task_id"],
            status=row["status"],
            created_at=_load_dt(row["created_at"]),
            updated_at=_load_dt(row["updated_at"]),
            applicant=row["applicant"] or "",
            department=row["department"] or "",
            expense_type=row["expense_type"] or "",
            note=row["note"] or "",
            files=[StoredFile(file_name=item["file_name"], content=item["content"]) for item in file_rows],
            report=AuditReport.model_validate_json(row["report_json"]) if row["report_json"] else None,
            error=row["error"],
            corrections={
                item["field_name"]: FieldCorrection(
                    field_name=item["field_name"],
                    original_value=item["original_value"],
                    corrected_value=item["corrected_value"],
                    reason=item["reason"],
                    created_at=_load_dt(item["created_at"]),
                )
                for item in correction_rows
            },
            review_items=[
                ReviewItem(
                    review_item_id=item["review_item_id"],
                    task_id=item["task_id"],
                    risk_type=item["risk_type"],
                    reason=item["reason"],
                    status=item["status"],
                    decision=item["decision"],
                    decided_by=item["decided_by"],
                    updated_at=_load_dt(item["updated_at"]),
                )
                for item in review_rows
            ],
        )

    def add_file(self, task_id: str, file_name: str, content: bytes) -> AuditTask:
        self.require_task(task_id)
        now = _now()
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO audit_files(task_id, file_name, content, created_at) VALUES (?, ?, ?, ?)",
                (task_id, file_name, content, _dump_dt(now)),
            )
            conn.execute(
                "UPDATE audit_tasks SET status = ?, updated_at = ?, error = NULL WHERE task_id = ?",
                ("READY", _dump_dt(now), task_id),
            )
            self._insert_event(conn, task_id, "DOCUMENT_UPLOADED", f"上传材料：{file_name}")
        return self.require_task(task_id)

    def save_report(self, task_id: str, report: AuditReport) -> AuditTask:
        self.require_task(task_id)
        now = _now()
        review_items = [_review_item_from_risk(task_id, risk) for risk in report.risks]
        with self._lock, self._connection() as conn:
            conn.execute("DELETE FROM review_items WHERE task_id = ?", (task_id,))
            conn.executemany(
                """
                INSERT INTO review_items(review_item_id, task_id, risk_type, reason, status, decision, decided_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.review_item_id,
                        item.task_id,
                        item.risk_type,
                        item.reason,
                        item.status,
                        item.decision,
                        item.decided_by,
                        _dump_dt(item.updated_at),
                    )
                    for item in review_items
                ],
            )
            conn.execute(
                "UPDATE audit_tasks SET status = ?, updated_at = ?, report_json = ?, error = NULL WHERE task_id = ?",
                ("COMPLETED", _dump_dt(now), report.model_dump_json(), task_id),
            )
            self._insert_event(conn, task_id, "AUDIT_COMPLETED", f"审核完成：{report.status}，风险数：{len(report.risks)}")
        return self.require_task(task_id)

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
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO field_corrections(task_id, field_name, original_value, corrected_value, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, field_name) DO UPDATE SET
                    original_value = excluded.original_value,
                    corrected_value = excluded.corrected_value,
                    reason = excluded.reason,
                    created_at = excluded.created_at
                """,
                (
                    task_id,
                    correction.field_name,
                    correction.original_value,
                    correction.corrected_value,
                    correction.reason,
                    _dump_dt(correction.created_at),
                ),
            )
            conn.execute(
                "UPDATE audit_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                ("READY", _dump_dt(correction.created_at), task_id),
            )
            self._insert_event(conn, task_id, "FIELD_CORRECTED", f"人工修正字段：{field_name}")
        return correction

    def decide_review_item(self, review_item_id: str, decision: ReviewStatus, decided_by: str | None = None) -> ReviewItem:
        now = _now()
        with self._lock, self._connection() as conn:
            row = conn.execute("SELECT task_id FROM review_items WHERE review_item_id = ?", (review_item_id,)).fetchone()
            if row is None:
                raise KeyError(review_item_id)
            conn.execute(
                """
                UPDATE review_items
                SET status = ?, decision = ?, decided_by = ?, updated_at = ?
                WHERE review_item_id = ?
                """,
                (decision, decision, decided_by, _dump_dt(now), review_item_id),
            )
            conn.execute("UPDATE audit_tasks SET updated_at = ? WHERE task_id = ?", (_dump_dt(now), row["task_id"]))
            self._insert_event(conn, row["task_id"], "REVIEW_DECIDED", f"复核项处理：{decision}")
        task = self.require_task(row["task_id"])
        for item in task.review_items:
            if item.review_item_id == review_item_id:
                return item
        raise KeyError(review_item_id)

    def save_error(self, task_id: str, error: str) -> AuditTask:
        self.require_task(task_id)
        now = _now()
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE audit_tasks SET status = ?, updated_at = ?, error = ? WHERE task_id = ?",
                ("FAILED", _dump_dt(now), error, task_id),
            )
            self._insert_event(conn, task_id, "AUDIT_FAILED", error)
        return self.require_task(task_id)

    def list_events(self, task_id: str) -> list[AuditEvent]:
        self.require_task(task_id)
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE task_id = ? ORDER BY created_at, event_id",
                (task_id,),
            ).fetchall()
        return [
            AuditEvent(
                event_id=row["event_id"],
                task_id=row["task_id"],
                event_type=row["event_type"],
                message=row["message"],
                created_at=_load_dt(row["created_at"]),
            )
            for row in rows
        ]

    def _insert_event(self, conn: sqlite3.Connection, task_id: str, event_type: str, message: str) -> None:
        conn.execute(
            "INSERT INTO audit_events(event_id, task_id, event_type, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"event-{uuid4().hex[:12]}", task_id, event_type, message, _dump_dt(_now())),
        )

    def log_event(self, task_id: str, event_type: str, message: str) -> None:
        """写入审计事件(§15:上传/查看/修改/导出/越权拒绝都要留痕)。"""
        with self._lock, self._connection() as conn:
            self._insert_event(conn, task_id, event_type, message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dump_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _load_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _default_db_path() -> Path:
    configured = os.getenv("AUDIT_TASK_DB")
    if configured:
        return Path(configured)
    return repo_root() / "data" / "runtime" / "audit_tasks.sqlite3"


def _review_item_from_risk(task_id: str, risk: RiskItem) -> ReviewItem:
    return ReviewItem(
        review_item_id=f"review-{uuid4().hex[:12]}",
        task_id=task_id,
        risk_type=risk.risk_type,
        reason=risk.reason,
        status="OPEN",
        updated_at=_now(),
    )


def create_task_store() -> InMemoryAuditTaskStore:
    backend = os.getenv("AUDIT_TASK_STORE", "sqlite").lower()
    if backend == "memory":
        return InMemoryAuditTaskStore()
    return SQLiteAuditTaskStore()


task_store = create_task_store()
