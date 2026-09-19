"""制度存储与版本管理(PRD §5.4 FR-301~FR-304)。

SQLite 保存两件事:
- ``policy_documents``:制度元数据与状态(元数据不完整不允许发布,FR-301);
- ``policy_chunks``:切片结果(发布后参与检索,FR-302)。

版本规则(FR-303/FR-304,§9.4):
- 只检索 ``published`` 状态、部门匹配且生效区间覆盖任务日期的制度;
- 停用/回滚通过状态切换完成,历史版本保留可追溯;
- 存储位置由 ``POLICY_STORE_PATH`` 控制(评测/单测指向临时文件,保证结论可复现)。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from app.rag.chunker import PolicyChunk

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE_PATH = REPO_ROOT / "data" / "runtime" / "policies.sqlite3"
ENV_STORE_PATH = "POLICY_STORE_PATH"

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_DISABLED = "disabled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_documents (
    policy_id      TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    version        TEXT NOT NULL,
    department     TEXT NOT NULL,
    expense_type   TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    status         TEXT NOT NULL,
    source_file    TEXT NOT NULL,
    mime           TEXT NOT NULL,
    chunk_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    published_at   TEXT
);
CREATE TABLE IF NOT EXISTS policy_chunks (
    chunk_id       TEXT PRIMARY KEY,
    policy_id      TEXT NOT NULL,
    section_path   TEXT NOT NULL,
    content        TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    department     TEXT NOT NULL,
    expense_type   TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    content_type   TEXT NOT NULL DEFAULT 'text'
);
"""


@dataclass
class PolicyDocument:
    policy_id: str
    name: str
    version: str
    department: str
    expense_type: str
    effective_from: str
    effective_to: str = ""
    status: str = STATUS_DRAFT
    source_file: str = ""
    mime: str = ""
    chunk_count: int = 0
    created_at: str = ""
    published_at: str | None = None
    errors: list[str] = field(default_factory=list)

    def metadata_complete(self) -> bool:
        """FR-301:元数据完整(名称/版本/生效时间/部门/费用类型)才允许发布。"""
        return all(
            str(value).strip()
            for value in (self.policy_id, self.name, self.version, self.effective_from,
                          self.department, self.expense_type)
        )


def store_path() -> Path:
    raw = os.environ.get(ENV_STORE_PATH)
    return Path(raw) if raw else DEFAULT_STORE_PATH


def isolate_store(path: Path | str | None = None) -> Path:
    """把制度存储切到独立文件(单测/评测使用),返回实际路径。"""
    target = Path(path) if path else Path(tempfile.mkdtemp(prefix="docaudit_policy_store_")) / "policies.sqlite3"
    os.environ[ENV_STORE_PATH] = str(target)
    return target


@contextmanager
def isolated_store(path: Path | str | None = None) -> Iterator[None]:
    original = os.environ.get(ENV_STORE_PATH)
    isolate_store(path)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(ENV_STORE_PATH, None)
        else:
            os.environ[ENV_STORE_PATH] = original


def create_policy(
    *,
    policy_id: str,
    name: str,
    version: str,
    department: str = "ALL",
    expense_type: str = "TRAVEL",
    effective_from: str = "",
    effective_to: str = "",
    source_file: str = "",
    mime: str = "",
) -> PolicyDocument:
    document = PolicyDocument(
        policy_id=policy_id.strip(),
        name=name.strip(),
        version=version.strip(),
        department=(department or "ALL").strip(),
        expense_type=(expense_type or "TRAVEL").strip(),
        effective_from=effective_from.strip(),
        effective_to=(effective_to or "").strip(),
        status=STATUS_DRAFT,
        source_file=source_file,
        mime=mime,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO policy_documents (policy_id, name, version, department, expense_type,"
            " effective_from, effective_to, status, source_file, mime, chunk_count, created_at, published_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (document.policy_id, document.name, document.version, document.department,
             document.expense_type, document.effective_from, document.effective_to, document.status,
             document.source_file, document.mime, 0, document.created_at, None),
        )
    return document


def save_chunks(policy_id: str, chunks: list[PolicyChunk]) -> int:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn, conn:
        conn.execute("DELETE FROM policy_chunks WHERE policy_id = ?", (policy_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO policy_chunks (chunk_id, policy_id, section_path, content, policy_version,"
            " department, expense_type, effective_from, effective_to, content_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (chunk.chunk_id, policy_id, chunk.section_path, chunk.content, chunk.version,
                 chunk.department, chunk.expense_type, chunk.effective_from, chunk.effective_to,
                 str(chunk.metadata.get("content_type", "text")))
                for chunk in chunks
            ],
        )
        conn.execute("UPDATE policy_documents SET chunk_count = ? WHERE policy_id = ?", (len(chunks), policy_id))
    return len(chunks)


def get_policy(policy_id: str) -> PolicyDocument | None:
    path = store_path()
    if not path.exists():
        return None
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT policy_id, name, version, department, expense_type, effective_from, effective_to,"
            " status, source_file, mime, chunk_count, created_at, published_at"
            " FROM policy_documents WHERE policy_id = ?",
            (policy_id,),
        ).fetchone()
    return _document(row) if row else None


def list_policies(status: str | None = None) -> list[PolicyDocument]:
    path = store_path()
    if not path.exists():
        return []
    sql = ("SELECT policy_id, name, version, department, expense_type, effective_from, effective_to,"
           " status, source_file, mime, chunk_count, created_at, published_at FROM policy_documents")
    params: list[str] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at"
    with closing(_connect(path)) as conn:
        return [_document(row) for row in conn.execute(sql, params).fetchall()]


def publish_policy(policy_id: str) -> PolicyDocument:
    """发布制度索引(FR-302/FR-303):元数据不完整或没有切片时拒绝。"""
    document = get_policy(policy_id)
    if document is None:
        raise KeyError(policy_id)
    if not document.metadata_complete():
        raise ValueError("制度元数据不完整（名称/版本/生效时间/部门/费用类型），不允许发布")
    if document.chunk_count <= 0:
        raise ValueError("制度尚未切片，不允许发布")
    _set_status(policy_id, STATUS_PUBLISHED, published_at=datetime.now().isoformat(timespec="seconds"))
    return get_policy(policy_id)  # type: ignore[return-value]


def disable_policy(policy_id: str) -> PolicyDocument:
    """停用制度(FR-303):停用后不再参与检索,历史版本保留。"""
    document = get_policy(policy_id)
    if document is None:
        raise KeyError(policy_id)
    _set_status(policy_id, STATUS_DISABLED)
    return get_policy(policy_id)  # type: ignore[return-value]


def disable_other_versions(policy_id: str) -> list[str]:
    """同一制度名的其他已发布版本全部停用(版本切换/回滚用,FR-303)。"""
    document = get_policy(policy_id)
    if document is None:
        raise KeyError(policy_id)
    disabled: list[str] = []
    for other in list_policies(STATUS_PUBLISHED):
        if other.policy_id != policy_id and other.name == document.name:
            _set_status(other.policy_id, STATUS_DISABLED)
            disabled.append(other.policy_id)
    return disabled


def load_published_chunks(
    department: str | None = None,
    expense_type: str | None = None,
    as_of: str | None = None,
) -> list[PolicyChunk]:
    """加载已发布且适用的制度切片(FR-304:部门、费用类型、生效时间过滤)。"""
    path = store_path()
    if not path.exists():
        return []
    as_of = as_of or date.today().isoformat()
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT c.chunk_id, c.section_path, c.content, c.policy_version, c.department, c.expense_type,"
            " c.effective_from, c.effective_to FROM policy_chunks c"
            " JOIN policy_documents d ON d.policy_id = c.policy_id"
            " WHERE d.status = ? ORDER BY c.chunk_id",
            (STATUS_PUBLISHED,),
        ).fetchall()

    chunks: list[PolicyChunk] = []
    for row in rows:
        chunk_id, section_path, content, version, chunk_department, chunk_expense, eff_from, eff_to = row
        if department and department != "ALL" and chunk_department not in ("ALL", department):
            continue
        if expense_type and chunk_expense and chunk_expense != expense_type:
            continue
        if eff_from and as_of < eff_from:
            continue
        if eff_to and as_of > eff_to:
            continue
        chunks.append(
            PolicyChunk(
                chunk_id=chunk_id,
                section_path=section_path,
                content=content,
                policy_id=chunk_id.split("-")[0],
                version=version,
                department=chunk_department,
                expense_type=chunk_expense,
                effective_from=eff_from,
                effective_to=eff_to or "",
            )
        )
    return chunks


def reset_store() -> None:
    path = store_path()
    if not path.exists():
        return
    with closing(_connect(path)) as conn, conn:
        conn.execute("DELETE FROM policy_chunks")
        conn.execute("DELETE FROM policy_documents")


def _set_status(policy_id: str, status: str, published_at: str | None = None) -> None:
    path = store_path()
    with closing(_connect(path)) as conn, conn:
        if published_at:
            conn.execute("UPDATE policy_documents SET status = ?, published_at = ? WHERE policy_id = ?",
                         (status, published_at, policy_id))
        else:
            conn.execute("UPDATE policy_documents SET status = ? WHERE policy_id = ?", (status, policy_id))


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def _document(row: tuple) -> PolicyDocument:
    return PolicyDocument(
        policy_id=row[0], name=row[1], version=row[2], department=row[3], expense_type=row[4],
        effective_from=row[5], effective_to=row[6] or "", status=row[7], source_file=row[8], mime=row[9],
        chunk_count=row[10], created_at=row[11], published_at=row[12],
    )
