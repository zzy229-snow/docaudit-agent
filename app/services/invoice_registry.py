"""发票查重登记表(PRD §5.3 FR-204,非目标 §1.3 的"Mock Tool"替代真实税务查验)。

每个审核任务完成时把关键发票字段(号码/开票日期/金额)登记到本地 SQLite,
``check_duplicate_invoice`` 工具据此查询历史记录并返回关联任务编号。

- 真实环境可把本模块换成数据库表或税务查验平台客户端,接口保持不变;
- 评测/单测通过 ``INVOICE_REGISTRY_PATH`` 指向临时文件,保证同一评测集可复现。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = REPO_ROOT / "data" / "runtime" / "invoice_registry.sqlite3"
ENV_REGISTRY_PATH = "INVOICE_REGISTRY_PATH"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS invoice_records (
    task_id        TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_date   TEXT,
    invoice_amount TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (task_id, invoice_number)
)
"""


@dataclass(frozen=True)
class InvoiceRecord:
    task_id: str
    invoice_number: str
    invoice_date: str | None
    invoice_amount: str | None


def registry_path() -> Path:
    """登记表位置:优先环境变量,便于评测隔离与多环境切换。"""
    raw = os.environ.get(ENV_REGISTRY_PATH)
    return Path(raw) if raw else DEFAULT_REGISTRY_PATH


def isolate_registry(path: Path | str | None = None) -> Path:
    """把登记表切到独立文件,返回实际路径。

    单测/评测使用:每次切换到一个全新的临时文件,保证重复发票用例从干净历史开始。
    """
    target = Path(path) if path else Path(tempfile.mkdtemp(prefix="docaudit_invoice_registry_")) / "invoice_registry.sqlite3"
    os.environ[ENV_REGISTRY_PATH] = str(target)
    return target


@contextmanager
def isolated_registry(path: Path | str | None = None) -> Iterator[None]:
    """上下文管理器形式:退出时恢复原有登记表位置。"""
    original = os.environ.get(ENV_REGISTRY_PATH)
    isolate_registry(path)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(ENV_REGISTRY_PATH, None)
        else:
            os.environ[ENV_REGISTRY_PATH] = original


def register_invoice(
    task_id: str,
    invoice_number: str | None,
    invoice_date: str | None = None,
    invoice_amount: str | None = None,
) -> bool:
    """登记一条发票记录;号码为空时跳过。同一任务重复提交同一号码只保留一条。"""
    number = _clean(invoice_number)
    if not number:
        return False
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO invoice_records "
            "(task_id, invoice_number, invoice_date, invoice_amount, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, number, _clean(invoice_date), _clean(invoice_amount), datetime.now().isoformat(timespec="seconds")),
        )
    return True


def register_task_invoice(task_id: str, fields: Mapping[str, object]) -> bool:
    """从抽取字段登记发票信息(字段值取自 ``ExtractedField.value``)。"""
    return register_invoice(
        task_id=task_id,
        invoice_number=_field_value(fields, "invoice_number"),
        invoice_date=_field_value(fields, "invoice_date"),
        invoice_amount=_field_value(fields, "invoice_amount"),
    )


def find_matches(
    invoice_number: str | None,
    exclude_task_id: str | None = None,
) -> list[InvoiceRecord]:
    """按发票号码查询历史记录;``exclude_task_id`` 用于排除任务自身的记录。"""
    number = _clean(invoice_number)
    if not number:
        return []
    path = registry_path()
    if not path.exists():
        return []
    sql = "SELECT task_id, invoice_number, invoice_date, invoice_amount FROM invoice_records WHERE invoice_number = ?"
    params: list[str] = [number]
    if exclude_task_id:
        sql += " AND task_id <> ?"
        params.append(exclude_task_id)
    sql += " ORDER BY created_at"
    with closing(_connect(path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [InvoiceRecord(*row) for row in rows]


def list_records() -> list[InvoiceRecord]:
    path = registry_path()
    if not path.exists():
        return []
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT task_id, invoice_number, invoice_date, invoice_amount FROM invoice_records ORDER BY created_at"
        ).fetchall()
    return [InvoiceRecord(*row) for row in rows]


def record_count() -> int:
    path = registry_path()
    if not path.exists():
        return 0
    with closing(_connect(path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM invoice_records").fetchone()[0])


def reset_registry() -> None:
    """清空登记表(评测/单测使用;不删除文件,避免 Windows 句柄占用)。"""
    path = registry_path()
    if not path.exists():
        return
    with closing(_connect(path)) as conn, conn:
        conn.execute("DELETE FROM invoice_records")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    return conn


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _field_value(fields: Mapping[str, object], name: str) -> str | None:
    field = fields.get(name)
    value = getattr(field, "value", None) if field is not None else None
    return _clean(value)
