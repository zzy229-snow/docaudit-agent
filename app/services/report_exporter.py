"""Audit report export helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from app.api.store import AuditEvent, AuditTask
from app.services.audit_summary import AuditExplanationSummary, build_audit_summary


def build_html_report(task: AuditTask, events: list[AuditEvent] | None = None) -> str:
    if task.report is None:
        raise ValueError("Audit task has not completed")
    summary = build_audit_summary(task.report)
    events = events or []
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"zh-CN\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            f"<title>DocAudit 审核报告 - {escape(task.task_id)}</title>",
            _style(),
            "</head>",
            "<body>",
            "<main>",
            _header(task, summary),
            _section("一、审核结论", _conclusion(summary)),
            _section("二、风险明细", _risks(summary)),
            _section("三、关键字段与证据", _fields(summary)),
            _section("四、检查项", _checks(summary)),
            _section("五、制度依据", _policies(summary)),
            _section("六、审计事件", _events(events)),
            _section("七、Agent 执行轨迹", _trace(summary)),
            "</main>",
            "</body>",
            "</html>",
        ]
    )


def _header(task: AuditTask, summary: AuditExplanationSummary) -> str:
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    status_class = "pass" if summary.status == "PASS" else "review"
    status_text = "通过" if summary.status == "PASS" else "需要复核"
    return f"""
<header>
  <div>
    <p class="eyebrow">DocAudit Agent</p>
    <h1>企业报销审核报告</h1>
    <p class="muted">任务编号：{escape(task.task_id)} ｜ 生成时间：{escape(generated_at)}</p>
  </div>
  <div class="status {status_class}">{status_text}</div>
</header>
<div class="cards">
  <div class="card"><span>材料数</span><strong>{len(task.files)}</strong></div>
  <div class="card"><span>风险数</span><strong>{len(summary.risks)}</strong></div>
  <div class="card"><span>检查项</span><strong>{len(summary.checks)}</strong></div>
  <div class="card"><span>制度依据</span><strong>{len(summary.policy_evidence)}</strong></div>
</div>
"""


def _conclusion(summary: AuditExplanationSummary) -> str:
    return f"""
<p>{escape(summary.conclusion)}</p>
<p class="next-action">{escape(summary.next_action)}</p>
"""


def _risks(summary: AuditExplanationSummary) -> str:
    if not summary.risks:
        return "<p class=\"ok\">当前已实现规则下未发现风险。</p>"
    rows = []
    for risk in summary.risks:
        rows.append(
            "<tr>"
            f"<td>{escape(risk.risk_type)}</td>"
            f"<td>{escape(risk.level)}</td>"
            f"<td>{escape(risk.reason)}</td>"
            f"<td>{escape(risk.next_action)}</td>"
            f"<td>{escape(', '.join(risk.evidence_refs) or '-')}</td>"
            f"<td>{escape(', '.join(risk.policy_refs) or '-')}</td>"
            "</tr>"
        )
    return _table(["风险类型", "级别", "原因", "建议处理", "字段证据", "制度依据"], rows)


def _fields(summary: AuditExplanationSummary) -> str:
    rows = [
        "<tr>"
        f"<td>{escape(field.name)}</td>"
        f"<td>{escape(field.value)}</td>"
        f"<td>{field.confidence:.2f}</td>"
        f"<td>{escape(field.document_id)}</td>"
        f"<td>{field.page_no}</td>"
        f"<td>{escape(field.source_text)}</td>"
        "</tr>"
        for field in summary.key_fields
    ]
    return _table(["字段", "值", "置信度", "来源文档", "页码", "原文证据"], rows)


def _checks(summary: AuditExplanationSummary) -> str:
    rows = [
        "<tr>"
        f"<td>{escape(check.name)}</td>"
        f"<td>{escape(check.status)}</td>"
        f"<td>{escape(check.detail)}</td>"
        f"<td>{escape(', '.join(check.evidence_refs) or '-')}</td>"
        "</tr>"
        for check in summary.checks
    ]
    return _table(["检查项", "状态", "说明", "证据"], rows)


def _policies(summary: AuditExplanationSummary) -> str:
    if not summary.policy_evidence:
        return "<p>未检索到制度依据。</p>"
    rows = [
        "<tr>"
        f"<td>{escape(item.chunk_id)}</td>"
        f"<td>{escape(item.section)}</td>"
        f"<td>{item.score:.4f}</td>"
        f"<td>{escape(item.content)}</td>"
        "</tr>"
        for item in summary.policy_evidence
    ]
    return _table(["条款ID", "章节", "分数", "内容"], rows)


def _events(events: list[AuditEvent]) -> str:
    if not events:
        return "<p>暂无审计事件。</p>"
    rows = [
        "<tr>"
        f"<td>{escape(event.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S'))}</td>"
        f"<td>{escape(event.event_type)}</td>"
        f"<td>{escape(event.message)}</td>"
        "</tr>"
        for event in events
    ]
    return _table(["时间", "事件", "说明"], rows)


def _trace(summary: AuditExplanationSummary) -> str:
    if not summary.trace:
        return "<p>暂无执行轨迹。</p>"
    items = "\n".join(f"<li><code>{escape(step)}</code></li>" for step in summary.trace)
    return f"<ol class=\"trace\">{items}</ol>"


def _section(title: str, body: str) -> str:
    return f"<section><h2>{escape(title)}</h2>{body}</section>"


def _table(headers: list[str], rows: list[str]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = "\n".join(rows) or f"<tr><td colspan=\"{len(headers)}\">暂无数据</td></tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _style() -> str:
    return """
<style>
  body { margin: 0; background: #f5f7fb; color: #1f2937; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }
  main { max-width: 1180px; margin: 0 auto; padding: 40px 28px; }
  header { display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 24px; }
  h1 { margin: 4px 0 8px; font-size: 34px; }
  h2 { margin: 0 0 16px; font-size: 21px; }
  section, .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 16px; box-shadow: 0 10px 28px rgba(15, 23, 42, .05); }
  section { padding: 24px; margin: 18px 0; }
  .eyebrow { margin: 0; color: #2563eb; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
  .muted { color: #6b7280; margin: 0; }
  .status { padding: 14px 18px; border-radius: 999px; font-size: 20px; font-weight: 800; white-space: nowrap; }
  .status.pass { color: #166534; background: #dcfce7; }
  .status.review { color: #9a3412; background: #ffedd5; }
  .cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 20px; }
  .card { padding: 18px; }
  .card span { display: block; color: #6b7280; margin-bottom: 8px; }
  .card strong { font-size: 28px; }
  .next-action { padding: 14px 16px; background: #eff6ff; border-radius: 12px; color: #1d4ed8; }
  .ok { padding: 14px 16px; background: #f0fdf4; border-radius: 12px; color: #166534; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { border-bottom: 1px solid #e5e7eb; padding: 10px 9px; text-align: left; vertical-align: top; }
  th { background: #f9fafb; font-weight: 700; }
  code { color: #334155; }
  .trace li { margin-bottom: 8px; }
  @media print {
    body { background: #fff; }
    section, .card { box-shadow: none; }
    main { padding: 0; }
  }
</style>
"""
