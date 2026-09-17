from typing import Literal
from pydantic import BaseModel
from .field import ExtractedField


class PolicyEvidence(BaseModel):
    chunk_id: str
    section: str
    content: str
    score: float


class CheckResult(BaseModel):
    name: str
    passed: bool | None
    detail: str
    evidence_refs: list[str] = []


class RiskItem(BaseModel):
    risk_type: str
    level: Literal["HIGH", "MEDIUM", "LOW"]
    reason: str
    evidence_refs: list[str] = []
    policy_refs: list[str] = []


class AuditReport(BaseModel):
    status: Literal["PASS", "REVIEW_REQUIRED"]
    fields: dict[str, ExtractedField]
    policy_evidence: list[PolicyEvidence]
    checks: list[CheckResult]
    risks: list[RiskItem]
    trace: list[str]
