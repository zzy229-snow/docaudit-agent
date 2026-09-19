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
    # PRD 附录A 四级:HIGH/MEDIUM/LOW/INFO(LOW、INFO 只提示,不阻断任务)
    level: Literal["HIGH", "MEDIUM", "LOW", "INFO"]
    reason: str
    evidence_refs: list[str] = []
    policy_refs: list[str] = []


class AuditReport(BaseModel):
    status: Literal["PASS", "REVIEW_REQUIRED"]
    fields: dict[str, ExtractedField]
    policy_evidence: list[PolicyEvidence] = []
    checks: list[CheckResult] = []
    risks: list[RiskItem] = []
    #: 各等级风险数量(如 {"HIGH": 1, "MEDIUM": 2}),由 risk_rules.risk_summary 生成
    risk_summary: dict[str, int] = {}
    trace: list[str] = []
