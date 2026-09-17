from pydantic import BaseModel

from app.models.audit import AuditReport


class FieldSummary(BaseModel):
    name: str
    value: str
    confidence: float
    document_id: str
    page_no: int
    source_text: str


class RiskSummary(BaseModel):
    risk_type: str
    level: str
    reason: str
    evidence_refs: list[str]
    policy_refs: list[str]
    next_action: str


class CheckSummary(BaseModel):
    name: str
    status: str
    detail: str
    evidence_refs: list[str]


class PolicySummary(BaseModel):
    chunk_id: str
    section: str
    content: str
    score: float


class AuditExplanationSummary(BaseModel):
    status: str
    conclusion: str
    next_action: str
    key_fields: list[FieldSummary]
    risks: list[RiskSummary]
    checks: list[CheckSummary]
    policy_evidence: list[PolicySummary]
    trace: list[str]


def build_audit_summary(report: AuditReport) -> AuditExplanationSummary:
    risks = [
        RiskSummary(
            risk_type=risk.risk_type,
            level=risk.level,
            reason=risk.reason,
            evidence_refs=risk.evidence_refs,
            policy_refs=risk.policy_refs,
            next_action=_risk_next_action(risk.risk_type),
        )
        for risk in report.risks
    ]
    return AuditExplanationSummary(
        status=report.status,
        conclusion=_conclusion(report),
        next_action=_overall_next_action(report),
        key_fields=[
            FieldSummary(
                name=name,
                value=str(field.value),
                confidence=field.confidence,
                document_id=field.document_id,
                page_no=field.page_no,
                source_text=field.source_text,
            )
            for name, field in sorted(report.fields.items())
        ],
        risks=risks,
        checks=[
            CheckSummary(
                name=check.name,
                status=_check_status(check.passed),
                detail=check.detail,
                evidence_refs=check.evidence_refs,
            )
            for check in report.checks
        ],
        policy_evidence=[
            PolicySummary(
                chunk_id=item.chunk_id,
                section=item.section,
                content=item.content,
                score=item.score,
            )
            for item in report.policy_evidence
        ],
        trace=report.trace,
    )


def _conclusion(report: AuditReport) -> str:
    if not report.risks:
        return "当前材料在已实现规则下未发现风险。"
    return f"发现{len(report.risks)}项需要复核的风险，请优先查看风险说明和制度依据。"


def _overall_next_action(report: AuditReport) -> str:
    if not report.risks:
        return "可进入人工抽检或报告导出。"
    return "请财务审核员核对风险项对应的原始字段和制度条款。"


def _risk_next_action(risk_type: str) -> str:
    mapping = {
        "REQUIRED_DOCUMENTS": "补充缺失材料后重新审核。",
        "AMOUNT_MATCH": "核对发票金额与付款凭证金额。",
        "DATE_RANGE": "核对发票日期和出差日期范围。",
        "HOTEL_LIMIT": "核对住宿标准、入住晚数和是否存在例外审批。",
    }
    return mapping.get(risk_type, "由财务审核员确认处理方式。")


def _check_status(passed: bool | None) -> str:
    if passed is True:
        return "PASS"
    if passed is False:
        return "RISK"
    return "REVIEW"
