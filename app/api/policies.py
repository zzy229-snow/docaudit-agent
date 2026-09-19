"""制度管理接口(PRD §5.4 FR-301~FR-304 / §12.2)。

- ``POST /api/v1/policies``:上传制度 TXT/DOCX/PDF,录入版本、生效时间、部门与费用类型,
  立即完成切片入库,状态为 ``draft``(FR-301/FR-302 切片部分);
- ``POST /api/v1/policies/{id}/publish``:发布索引,发布后参与检索(FR-302);
  可选 ``switch_version=true`` 同时停用同制度名的其他版本(FR-303 版本切换);
- ``POST /api/v1/policies/{id}/disable``:停用,历史版本保留(FR-303)。

检索侧按部门、费用类型与生效区间过滤(FR-304),见 ``app.rag.retriever``。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.parsers.loader import parse_document
from app.rag.text_chunker import chunk_text
from app.services import policy_store

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])

MAX_POLICY_BYTES = 8 * 1024 * 1024
ALLOWED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


class PolicySummary(BaseModel):
    policy_id: str
    name: str
    version: str
    department: str
    expense_type: str
    effective_from: str
    effective_to: str
    status: str
    source_file: str
    chunk_count: int
    published_at: str | None = None


class PolicyListResponse(BaseModel):
    policies: list[PolicySummary]


class PolicyActionResponse(BaseModel):
    policy: PolicySummary
    message: str
    disabled_policy_ids: list[str] = []
    index_mode: str


def _summary(document: policy_store.PolicyDocument) -> PolicySummary:
    return PolicySummary(
        policy_id=document.policy_id,
        name=document.name,
        version=document.version,
        department=document.department,
        expense_type=document.expense_type,
        effective_from=document.effective_from,
        effective_to=document.effective_to,
        status=document.status,
        source_file=document.source_file,
        chunk_count=document.chunk_count,
        published_at=document.published_at,
    )


def _require_policy(policy_id: str) -> policy_store.PolicyDocument:
    document = policy_store.get_policy(policy_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy document not found")
    return document


def _extract_text(file_name: str, content: bytes) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"仅支持 {', '.join(sorted(ALLOWED_SUFFIXES))} 格式的制度文件")
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(content) > MAX_POLICY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件不得超过8MB")
    try:
        document = parse_document(file_name, content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return "\n".join(page.text for page in document.pages)


@router.post("", response_model=PolicySummary, status_code=status.HTTP_201_CREATED)
async def upload_policy(
    file: UploadFile = File(...),
    name: str = Form(...),
    version: str = Form(...),
    effective_from: str = Form(...),
    department: str = Form("ALL"),
    expense_type: str = Form("TRAVEL"),
    effective_to: str = Form(""),
    policy_id: str = Form(""),
) -> PolicySummary:
    """上传制度并切片入库(FR-301/FR-302,状态 draft)。"""
    content = await file.read()
    file_name = file.filename or "policy.txt"
    text = _extract_text(file_name, content)

    resolved_id = policy_id.strip() or f"UP-{uuid4().hex[:8].upper()}"
    document = policy_store.create_policy(
        policy_id=resolved_id,
        name=name,
        version=version,
        department=department,
        expense_type=expense_type,
        effective_from=effective_from,
        effective_to=effective_to,
        source_file=file_name,
        mime=file.content_type or "application/octet-stream",
    )
    if not document.metadata_complete():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="制度元数据不完整（名称/版本/生效时间/部门/费用类型）")

    chunks = chunk_text(
        text,
        policy_id=resolved_id,
        version=version,
        department=department,
        expense_type=expense_type,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    if not chunks:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="制度正文为空或无法切片")
    policy_store.save_chunks(resolved_id, chunks)
    return _summary(_require_policy(resolved_id))


@router.get("", response_model=PolicyListResponse)
def list_policies(status_filter: str | None = None) -> PolicyListResponse:
    return PolicyListResponse(policies=[_summary(item) for item in policy_store.list_policies(status_filter)])


@router.get("/{policy_id}", response_model=PolicySummary)
def get_policy(policy_id: str) -> PolicySummary:
    return _summary(_require_policy(policy_id))


@router.post("/{policy_id}/publish", response_model=PolicyActionResponse)
def publish_policy(policy_id: str, switch_version: bool = False) -> PolicyActionResponse:
    """发布制度索引(FR-302);``switch_version=true`` 同时停用同制度名旧版本(FR-303)。"""
    _require_policy(policy_id)
    try:
        published = policy_store.publish_policy(policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    disabled = policy_store.disable_other_versions(policy_id) if switch_version else []
    message = f"制度 {published.name} {published.version} 已发布，切片 {published.chunk_count} 条"
    if disabled:
        message += f"；已停用旧版本 {', '.join(disabled)}"
    return PolicyActionResponse(
        policy=_summary(published),
        message=message,
        disabled_policy_ids=disabled,
        index_mode="local（关键词索引即时生效）",
    )


@router.post("/{policy_id}/disable", response_model=PolicyActionResponse)
def disable_policy(policy_id: str) -> PolicyActionResponse:
    """停用制度(FR-303):停用后不再参与检索,历史版本保留可追溯。"""
    try:
        document = policy_store.disable_policy(policy_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy document not found") from exc
    return PolicyActionResponse(
        policy=_summary(document),
        message=f"制度 {document.policy_id} 已停用，不再参与检索",
        index_mode="local",
    )
