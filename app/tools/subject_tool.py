"""Cross-document subject consistency checks.

In reimbursement review, the applicant, invoice buyer, and payment payee should
usually point to the same person or entity. Missing subject fields are treated as
unknown instead of risky because many demo or legacy materials may not contain
invoice title / payee information.
"""

from app.models.audit import CheckResult


def compare_subject(
    applicant_name: str | None,
    invoice_buyer: str | None = None,
    payment_party: str | None = None,
) -> CheckResult:
    applicant = _normalize_subject(applicant_name)
    buyer = _normalize_subject(invoice_buyer)
    payee = _normalize_subject(payment_party)

    if applicant is None:
        return CheckResult(name="applicant_match", passed=None, detail="缺少申请人字段")

    present_parties = [
        ("发票购买方", "invoice_buyer", buyer),
        ("付款收款方", "payment_party", payee),
    ]
    present_refs = [(label, ref, value) for label, ref, value in present_parties if value is not None]
    if not present_refs:
        return CheckResult(
            name="applicant_match",
            passed=True,
            detail="材料未包含购买方或收款方主体信息，主体一致性检查跳过",
            evidence_refs=["applicant_name"],
        )

    mismatches = [
        f"{label}{value}"
        for label, _ref, value in present_refs
        if value != applicant
    ]
    evidence_refs = ["applicant_name"] + [ref for _label, ref, _value in present_refs]
    if mismatches:
        return CheckResult(
            name="applicant_match",
            passed=False,
            detail=f"申请人{applicant} 与 {'、'.join(mismatches)} 不一致",
            evidence_refs=evidence_refs,
        )

    labels = "、".join(label for label, _ref, _value in present_refs)
    return CheckResult(
        name="applicant_match",
        passed=True,
        detail=f"申请人{applicant} 与 {labels} 一致",
        evidence_refs=evidence_refs,
    )


def _normalize_subject(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
