"""跨文档主体对齐:申请人 ↔ 发票购买方 ↔ 付款收款方。

主体不一致(如发票购买方不是申请人本人)是报销审核的常见风险信号。
未提供主体信息时返回 passed=None(跳过),不产生风险——材料本就可能不含抬头。
"""

from app.models.audit import CheckResult


def _norm(value) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    return v or None


def compare_subject(applicant_name, invoice_buyer=None, payment_party=None) -> CheckResult:
    applicant = _norm(applicant_name)
    buyer = _norm(invoice_buyer)
    payee = _norm(payment_party)

    if applicant is None:
        return CheckResult(name="applicant_match", passed=None, detail="缺少申请人字段")

    parties = [("发票购买方", "invoice_buyer", buyer), ("付款收款方", "payment_party", payee)]
    present = [(label, ref) for label, ref, value in parties if value is not None]

    if not present:
        return CheckResult(name="applicant_match", passed=None,
                           detail="材料未包含购买方或收款方主体信息")

    mismatches = [f"{label}{value}" for label, ref, value in parties
                  if value is not None and value != applicant]
    if mismatches:
        return CheckResult(name="applicant_match", passed=False,
                           detail=f"申请人{applicant} 与 {'、'.join(mismatches)} 不一致",
                           evidence_refs=["applicant_name"] + [ref for _, ref in present])

    detail = "申请人{applicant} 与 {labels} 一致".format(
        applicant=applicant, labels="、".join(label for label, _ in present))
    return CheckResult(name="applicant_match", passed=True, detail=detail,
                       evidence_refs=["applicant_name"] + [ref for _, ref in present])
