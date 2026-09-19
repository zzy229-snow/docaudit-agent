from decimal import Decimal, InvalidOperation
import re
from app.models.audit import CheckResult


def amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def compare_amounts(invoice: str | None, payment: str | None) -> CheckResult:
    a, b = amount(invoice), amount(payment)
    if a is None or b is None:
        return CheckResult(name="amount_match", passed=None, detail="金额字段缺失或无效")
    diff = a - b
    return CheckResult(name="amount_match", passed=diff == 0, detail=f"发票{a}元，付款{b}元，差额{diff}元",
                       evidence_refs=["invoice_amount", "payment_amount"])


def compare_hotel_limit(invoice: str | None, evidence: list) -> CheckResult:
    if not evidence:
        # PRD §16/AC-05:没有匹配制度时返回"规则缺失,待人工确认",不得判定合规
        return CheckResult(name="rule_missing", passed=None,
                           detail="未检索到适用的住宿标准条款，缺少规则依据，需人工确认")
    a = amount(invoice)
    if a is None:
        return CheckResult(name="hotel_limit", passed=None, detail="缺少住宿金额或适用制度")
    match = re.search(r"(\d+(?:\.\d+)?)元/晚", evidence[0].content)
    if not match:
        return CheckResult(name="hotel_limit", passed=None, detail="制度条款中未找到可计算上限")
    limit = amount(match.group(1))
    diff = a - limit
    return CheckResult(name="hotel_limit", passed=diff <= 0,
        detail=f"示例按一晚计算：实际{a}元，上限{limit}元，超出{max(diff, Decimal('0.00'))}元",
        evidence_refs=["invoice_amount", evidence[0].chunk_id])
