"""报销单展示命名(客户视角的"发票命名")。

命名规则(产品约定): **日期区间 + 人物 + 事件**,例如::

    2026.9.18-9.19 张三 住宿报销

- 日期: 左端写 ``年.月.日``;右端同年时省略年份(``9.19``),跨年时写全;
  只有一天时只写一天;没有日期时省略该段。
- 人物: 申请人姓名,缺失时省略。
- 事件: 报销类型映射(住宿报销/差旅报销/交通报销/采购报销/招待报销…)或用户填写的事由。
- 全部缺失时回退到技术编号,保证列表里永远有可读标识。

本模块是纯函数,不依赖存储与模型,便于单测与在 API/Streamlit 两侧复用。
"""
from __future__ import annotations

import re
from collections.abc import Mapping

#: 报销类型 -> 事件名
EVENT_LABELS: dict[str, str] = {
    "HOTEL": "住宿报销",
    "ACCOMMODATION": "住宿报销",
    "TRAVEL": "差旅报销",
    "TRANSPORT": "交通报销",
    "TRAFFIC": "交通报销",
    "PURCHASE": "采购报销",
    "MEAL": "招待报销",
    "ENTERTAIN": "招待报销",
    "MEDICAL": "医药报销",
    "EXPENSE": "费用报销",
    "OFFICE": "办公采购报销",
}

#: 类型名里的中文关键词(用户直接写中文时也能识别)
_EVENT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("住宿", "住宿报销"),
    ("酒店", "住宿报销"),
    ("差旅", "差旅报销"),
    ("交通", "交通报销"),
    ("车票", "交通报销"),
    ("机票", "交通报销"),
    ("采购", "采购报销"),
    ("办公", "采购报销"),
    ("招待", "招待报销"),
    ("餐", "招待报销"),
    ("医药", "医药报销"),
    ("医疗", "医药报销"),
)

_DATE_RE = re.compile(r"(\d{4})\D{0,2}(\d{1,2})\D{0,2}(\d{1,2})")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_MAX_TITLE_CHARS = 80


def sanitize_title(value: object) -> str:
    """名称清洗:去控制字符(换行/制表符)、折叠空白、限长。

    名称可能来自用户输入或抽取字段,必须能安全地放进界面、文件名与 JSON。
    """
    cleaned = _CONTROL_RE.sub(" ", str(value or ""))
    return " ".join(cleaned.split())[:_MAX_TITLE_CHARS].strip()


def parse_date_parts(value: object) -> tuple[int, int, int] | None:
    """把 ``2026-09-18`` / ``2026.9.18`` / ``2026年9月18日`` 解析为 (年, 月, 日)。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _DATE_RE.search(text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def format_single_date(value: object) -> str:
    """``2026.9.18``;无法解析时返回空串。"""
    parts = parse_date_parts(value)
    return f"{parts[0]}.{parts[1]}.{parts[2]}" if parts else ""


def format_date_range(start: object = None, end: object = None) -> str:
    """日期区间:``2026.9.18-9.19``(同年省略年份),单日期则只写一个日期。"""
    first = parse_date_parts(start)
    last = parse_date_parts(end)
    if first is None and last is None:
        return ""
    if first is None:
        return format_single_date(end)
    if last is None or last == first:
        return f"{first[0]}.{first[1]}.{first[2]}"
    head = f"{first[0]}.{first[1]}.{first[2]}"
    tail = f"{last[1]}.{last[2]}" if last[0] == first[0] else f"{last[0]}.{last[1]}.{last[2]}"
    return f"{head}-{tail}"


def event_label(expense_type: str | None = None, event: str | None = None) -> str:
    """事件名:优先用户填写的事由/备注,否则按报销类型映射。"""
    if event and sanitize_title(event):
        return sanitize_title(event)
    code = (expense_type or "").strip().upper()
    if code in EVENT_LABELS:
        return EVENT_LABELS[code]
    text = sanitize_title(expense_type)
    if text:
        for keyword, label in _EVENT_KEYWORDS:
            if keyword in text:
                return label
        return text
    return ""


def build_report_title(
    applicant: str | None = None,
    expense_type: str | None = None,
    start_date: object = None,
    end_date: object = None,
    event: str | None = None,
    task_id: str | None = None,
) -> str:
    """按"日期区间 人物 事件"拼装报销单名称(缺失段自动省略)。"""
    parts = [
        format_date_range(start_date, end_date),
        str(applicant).strip() if applicant else "",
        event_label(expense_type, event),
    ]
    title = sanitize_title(" ".join(part for part in parts if part))
    return title or sanitize_title(task_id)


def _field_value(fields: Mapping[str, object], name: str) -> str | None:
    field = fields.get(name)
    value = getattr(field, "value", None) if field is not None else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def title_from_fields(
    fields: Mapping[str, object],
    expense_type: str | None = None,
    event: str | None = None,
    task_id: str | None = None,
    applicant: str | None = None,
) -> str:
    """从抽取字段生成名称:优先行程日期(出差开始/结束),回退发票日期。

    ``applicant`` 显式传入时优先(用户在新建审核里填的申请人),
    材料里抽取到的 ``applicant_name`` 只作为兜底。
    """
    start = _field_value(fields, "travel_start_date") or _field_value(fields, "invoice_date")
    end = _field_value(fields, "travel_end_date") or _field_value(fields, "invoice_date")
    return build_report_title(
        applicant=applicant or _field_value(fields, "applicant_name"),
        expense_type=expense_type,
        start_date=start,
        end_date=end,
        event=event,
        task_id=task_id,
    )


def safe_file_name(title: str, fallback: str = "docaudit-report") -> str:
    """把名称转成安全文件名(报告下载用)。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", sanitize_title(title))
    return cleaned or fallback
