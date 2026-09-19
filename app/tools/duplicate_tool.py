"""重复发票查重工具(PRD §5.3 FR-204 / §8.3 ``check_duplicate_invoice``)。

输入:发票号码 + 开票日期 + 金额(来自抽取字段)。
处理:查询历史登记记录(``app.services.invoice_registry``)。
输出:命中时返回关联任务编号与命中字段;号码相同但日期/金额不一致时提示人工核实。

工具是确定性的(同输入同结论),不调用模型。
"""
from __future__ import annotations

from app.models.audit import CheckResult
from app.services.invoice_registry import InvoiceRecord, find_matches

TOOL_NAME = "duplicate_invoice"
CONFLICT_NAME = "invoice_number_conflict"


def check_duplicate_invoice(
    invoice_number: str | None,
    invoice_date: str | None = None,
    invoice_amount: str | None = None,
    task_id: str | None = None,
) -> CheckResult:
    number = _clean(invoice_number)
    if number is None:
        # 没有发票号码无法查重:这是"未执行",不是风险(材料缺失由 required_documents 负责)
        return CheckResult(
            name=TOOL_NAME,
            passed=True,
            detail="材料未提供发票号码，重复发票检查跳过",
        )

    records = find_matches(number, exclude_task_id=task_id)
    if not records:
        return CheckResult(
            name=TOOL_NAME,
            passed=True,
            detail=f"发票号码{number}未命中历史记录",
            evidence_refs=["invoice_number"],
        )

    date, amount = _clean(invoice_date), _clean(invoice_amount)
    exact = [record for record in records if _matches(record, date, amount)]
    if exact:
        record = exact[0]
        matched = "、".join(_matched_fields(record, date, amount))
        return CheckResult(
            name=TOOL_NAME,
            passed=False,
            detail=(
                f"重复发票：号码{number}已由任务{record.task_id}提交"
                f"（命中字段：{matched}）"
            ),
            evidence_refs=["invoice_number", "invoice_date", "invoice_amount"],
        )

    record = records[0]
    return CheckResult(
        name=CONFLICT_NAME,
        passed=False,
        detail=(
            f"发票号码{number}与任务{record.task_id}的历史记录相同，"
            f"但开票日期或金额不一致（历史：{record.invoice_date or '未知'}/"
            f"{record.invoice_amount or '未知'}，本次：{date or '未知'}/{amount or '未知'}），需人工核实"
        ),
        evidence_refs=["invoice_number"],
    )


def _matches(record: InvoiceRecord, date: str | None, amount: str | None) -> bool:
    if date is not None and record.invoice_date != date:
        return False
    if amount is not None and record.invoice_amount != amount:
        return False
    return True


def _matched_fields(record: InvoiceRecord, date: str | None, amount: str | None) -> list[str]:
    fields = ["发票号码"]
    if date is not None and record.invoice_date == date:
        fields.append("开票日期")
    if amount is not None and record.invoice_amount == amount:
        fields.append("金额")
    return fields


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
