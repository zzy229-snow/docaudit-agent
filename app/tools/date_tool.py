from datetime import date
from app.models.audit import CheckResult


def parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        parts = raw.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-").split("-")
        return date(*(int(p) for p in parts))
    except (TypeError, ValueError):
        return None


def compare_date_range(invoice: str | None, start: str | None, end: str | None) -> CheckResult:
    d, a, b = (parse_date(x) for x in (invoice, start, end))
    if not all((d, a, b)):
        return CheckResult(name="date_range", passed=None, detail="日期字段缺失或无效")
    if a > b:
        return CheckResult(name="date_range", passed=None, detail="出差开始日期晚于结束日期")
    return CheckResult(name="date_range", passed=a <= d <= b,
        detail=f"发票日期{d}，出差时间{a}至{b}",
        evidence_refs=["invoice_date", "travel_start_date", "travel_end_date"])
