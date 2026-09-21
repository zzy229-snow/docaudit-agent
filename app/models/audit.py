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


class PlanStep(BaseModel):
    """受控审核计划的一步(PRD §7.2 plan 节点 / §7.1 计划必须符合允许的 Schema)。"""

    order: int
    name: str
    tool: str
    reason: str
    required: bool = True
    #: 材料/字段不足时该步可能被跳过
    may_skip: bool = False


class AuditPlan(BaseModel):
    """审核计划(§4.1 阶段4产物)。生成方式只有规则或模型两种,步骤必须落在工具白名单内。"""

    task_id: str
    version: int = 1
    created_by: Literal["rules", "llm"] = "rules"
    status: Literal["ready", "needs_replan", "terminated"] = "ready"
    steps: list[PlanStep] = []
    notes: list[str] = []


class CritiqueResult(BaseModel):
    """自检结果(PRD §7.2 critic 节点:完整性、引用、冲突复核)。"""

    complete: bool
    issues: list[str] = []
    checks_total: int = 0
    checks_with_conclusion: int = 0
    risks_total: int = 0
    risks_with_evidence: int = 0
    evidence_coverage: float = 0.0
    missing_policy_risks: list[str] = []


class AuditReport(BaseModel):
    #: PRD §4.2 任务状态:PASS / REVIEW_REQUIRED / FAILED(不可自动恢复的失败)
    #: UNDETERMINED:材料未被真正识别或无可用于检查的证据 —— 不给结论,避免误导
    status: Literal["PASS", "REVIEW_REQUIRED", "FAILED", "UNDETERMINED"]
    fields: dict[str, ExtractedField]
    policy_evidence: list[PolicyEvidence] = []
    checks: list[CheckResult] = []
    risks: list[RiskItem] = []
    #: 各等级风险数量(如 {"HIGH": 1, "MEDIUM": 2}),由 risk_rules.risk_summary 生成
    risk_summary: dict[str, int] = {}
    #: 受控审核计划与自检结果(§7.2 plan / critic 节点产物)
    audit_plan: AuditPlan | None = None
    critique: CritiqueResult | None = None
    #: 失败原因(仅 status=FAILED 时存在)
    failure_reason: str | None = None
    trace: list[str] = []
