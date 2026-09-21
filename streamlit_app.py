import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from app.agent.graph import run_audit
from app.api.store import AuditTask, task_store
from app.config import load_environment
from app.evaluation.runner import run_evaluation
from app.services.audit_summary import AuditExplanationSummary, build_audit_summary
from app.services.report_exporter import build_html_report
from app.services.task_naming import build_report_title, safe_file_name
from app.services.readability import demo_mode_notice, is_synthetic_engine

load_dotenv()

load_environment()

DEMO_ROOT = Path(__file__).resolve().parent / "data" / "demo"
DEMO_CASES = {
    "正常报销：三类材料齐全": "normal",
    "住宿超标 + 主体不一致": "over_limit",
    "缺少付款凭证": "missing_payment",
    "主体不一致但金额正常": "subject_mismatch",
    "发票日期超出行程": "date_out_of_range",
}

#: 新建审核可选报销类型 -> 名称里的"事件"
EXPENSE_TYPES = {
    "住宿报销": "HOTEL",
    "差旅报销": "TRAVEL",
    "交通报销": "TRANSPORT",
    "采购报销": "PURCHASE",
    "招待报销": "MEAL",
}

# 视图导航(状态驱动,便于"打开任务"直接跳转到审核详情)
NAV_TASKS, NAV_CREATE, NAV_DETAIL = "任务中心", "新建审核", "审核详情"
NAV_OPTIONS = [NAV_TASKS, NAV_CREATE, NAV_DETAIL]


st.set_page_config(page_title="DocAudit Agent 审核工作台", layout="wide")


def _inject_nav_style() -> None:
    """把视图导航的 radio 化妆成标签页样式(选中项下方横线),兼顾可编程跳转。"""
    primary = st.get_option("theme.primaryColor") or "#FF4B4B"
    st.markdown(
        f"""
<style>
  /* 视图导航:还原标签页外观(选中项下方横线) */
  div[role="radiogroup"] {{
      border-bottom: 1px solid rgba(128, 128, 128, 0.25);
      gap: 0;
      margin-bottom: 0.75rem;
  }}
  div[role="radiogroup"] label {{
      padding: 0.5rem 0.15rem 0.55rem 0.15rem;
      margin: 0 1.1rem -1px 0;
      border-bottom: 2px solid transparent;
      border-radius: 0;
      background: transparent;
      transition: border-color 0.18s ease, color 0.18s ease;
  }}
  div[role="radiogroup"] label:hover {{
      color: {primary};
  }}
  div[role="radiogroup"] label:has(input:checked),
  div[role="radiogroup"] label[aria-checked="true"] {{
      border-bottom-color: {primary};
      color: {primary};
  }}
  div[role="radiogroup"] label:has(input:checked) p,
  div[role="radiogroup"] label[aria-checked="true"] p {{
      font-weight: 600;
  }}
  /* 隐藏圆点指示器(Streamlit 1.64:label 内首个 div 为圆点),只留下划线 */
  div[role="radiogroup"] label div:first-child,
  div[role="radiogroup"] label > span:first-child,
  div[role="radiogroup"] label svg:first-child {{
      display: none !important;
  }}
</style>
""",
        unsafe_allow_html=True,
    )


_inject_nav_style()


def load_demo_files(case: str) -> list[tuple[str, bytes]]:
    case_dir = DEMO_ROOT / case
    return [(path.name, path.read_bytes()) for path in sorted(case_dir.iterdir()) if path.is_file()]


def provider_label() -> str:
    provider = os.getenv("MODEL_PROVIDER", "mock").strip().lower()
    if provider == "mock":
        return "Mock / 离线安全模式"
    return f"{provider} · {os.getenv('MODEL_NAME', '未设置模型名')}"


def status_label(status: str) -> str:
    return {
        "CREATED": "已创建",
        "READY": "待审核",
        "COMPLETED": "已完成",
        "FAILED": "失败",
    }.get(status, status)


def result_label(task: AuditTask) -> str:
    if task.report is None:
        return "-"
    if task.report.status == "PASS":
        return "通过"
    if task.report.status == "UNDETERMINED":
        return "无法判定（材料未识别）"
    if task.report.status == "FAILED":
        return "失败"
    return "需复核"


def review_progress(task: AuditTask) -> str:
    """复核进度文本:驳回/通过后任务中心立即可见变化。"""
    items = task.review_items
    if not items:
        return "-"
    approved = sum(1 for item in items if item.status == "APPROVED")
    rejected = sum(1 for item in items if item.status == "REJECTED")
    pending = len(items) - approved - rejected
    parts = []
    if pending:
        parts.append(f"待复核 {pending}")
    if approved:
        parts.append(f"通过 {approved}")
    if rejected:
        parts.append(f"驳回 {rejected}")
    return " / ".join(parts)


def review_counts(task: AuditTask) -> tuple[int, int]:
    items = task.review_items
    approved = sum(1 for item in items if item.status == "APPROVED")
    rejected = sum(1 for item in items if item.status == "REJECTED")
    return approved, rejected


def select_task(task_id: str) -> None:
    st.session_state["selected_task_id"] = task_id


def selected_task() -> AuditTask | None:
    task_id = st.session_state.get("selected_task_id")
    if not task_id:
        return None
    return task_store.get_task(task_id)


def create_task_with_files(
    files: list[tuple[str, bytes]],
    applicant: str = "",
    department: str = "",
    expense_type: str = "",
    note: str = "",
    title: str = "",
) -> AuditTask:
    task = task_store.create_task(
        applicant=applicant,
        department=department,
        expense_type=expense_type,
        note=note,
        title=title,
    )
    for file_name, content in files:
        task = task_store.add_file(task.task_id, file_name, content)
    st.session_state["selected_task_id"] = task.task_id
    return task


def task_label(task: AuditTask) -> str:
    """客户视角的名称:优先报销单名称,没有时回退技术编号。"""
    return task.title or task.task_id


def request_open(task_id: str) -> None:
    """打开任务:设为当前任务并切换到审核详情。"""
    st.session_state["selected_task_id"] = task_id
    switch_view(NAV_DETAIL)


def switch_view(view: str) -> None:
    """切换视图。

    Streamlit 的 radio 一旦被用户交互过,其前端值优先于后端 session_state 修改;
    因此递增"导航代次"让 radio 以新 key 重建,从而接受新的选中值。
    """
    st.session_state["view"] = view
    st.session_state["nav_gen"] = st.session_state.get("nav_gen", 0) + 1
    st.rerun()


def run_selected_task(task: AuditTask) -> AuditTask:
    report = run_audit(
        [(item.file_name, item.content) for item in task.files],
        field_overrides=task_store.field_overrides(task.task_id),
        task_id=task.task_id,
    )
    return task_store.save_report(task.task_id, report)


def show_status_badge(task: AuditTask) -> None:
    approved, rejected = review_counts(task)
    if task.status == "FAILED":
        st.error(f"任务失败：{task.error}")
    elif task.report and task.report.status == "PASS":
        st.success("审核通过")
    elif task.report and task.report.status == "REVIEW_REQUIRED":
        suffix = f"（复核：通过 {approved} / 驳回 {rejected}）" if (approved or rejected) else ""
        st.warning(f"需要人工复核{suffix}")
    elif task.status == "READY":
        st.info("材料已就绪，等待运行审核")
    else:
        st.info("任务已创建，等待上传材料")


def render_task_table(tasks: list[AuditTask]):
    """任务表格:单击任意行即可打开该任务详情。

    第一列是客户视角的"报销单名称"(日期区间+人物+事件),技术编号放到最后一列。
    """
    return st.dataframe(
        [
            {
                "报销单名称": task_label(task),
                "审核结论": result_label(task),
                "任务状态": status_label(task.status),
                "文件数": len(task.files),
                "风险数": len(task.report.risks) if task.report else 0,
                "复核进度": review_progress(task),
                "更新时间": task.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
                "技术编号": task.task_id,
            }
            for task in tasks
        ],
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )


def render_summary(summary: AuditExplanationSummary) -> None:
    label = "通过" if summary.status == "PASS" else "需要复核"
    left, middle, right, fourth = st.columns(4)
    left.metric("审核结论", label)
    middle.metric("风险数", len(summary.risks))
    right.metric("制度依据", len(summary.policy_evidence))
    fourth.metric("检查项", len(summary.checks))

    if summary.status == "PASS":
        st.success(summary.conclusion)
    else:
        st.warning(summary.conclusion)
    st.write(summary.next_action)


def render_risks(summary: AuditExplanationSummary) -> None:
    if not summary.risks:
        st.success("当前已实现规则下未发现风险。")
        return

    for risk in summary.risks:
        with st.container(border=True):
            cols = st.columns([2, 1, 4])
            cols[0].markdown(f"### {risk.risk_type}")
            cols[1].markdown(f"**{risk.level}**")
            cols[2].write(risk.reason)
            st.caption("建议处理：" + risk.next_action)
            if risk.evidence_refs:
                st.caption("字段证据：" + "、".join(risk.evidence_refs))
            if risk.policy_refs:
                st.caption("制度依据：" + "、".join(risk.policy_refs))


def render_review_workbench(task: AuditTask) -> None:
    items = task.review_items
    if not items:
        st.success("暂无待复核项。")
        return

    approved, rejected = review_counts(task)
    pending = len(items) - approved - rejected
    if pending:
        st.info(f"复核进度：待处理 {pending} / 已通过 {approved} / 已驳回 {rejected}")
    else:
        st.success(f"全部复核项已处理：已通过 {approved} / 已驳回 {rejected}（任务中心表格「复核进度」列已同步）")

    for item in items:
        with st.container(border=True):
            cols = st.columns([2, 4, 1, 1])
            cols[0].markdown(f"**{item.risk_type}**")
            cols[1].write(item.reason)
            cols[2].metric("状态", item.status)
            reviewer = cols[3].text_input(
                "处理人",
                value=item.decided_by or "finance-reviewer",
                key=f"reviewer-{item.review_item_id}",
            )
            approve, reject = st.columns(2)
            if approve.button("通过复核", key=f"approve-{item.review_item_id}"):
                task_store.decide_review_item(item.review_item_id, "APPROVED", reviewer)
                st.rerun()
            if reject.button("驳回复核", key=f"reject-{item.review_item_id}"):
                task_store.decide_review_item(item.review_item_id, "REJECTED", reviewer)
                st.rerun()


def render_fields(summary: AuditExplanationSummary, task: AuditTask) -> None:
    st.dataframe(
        [
            {
                "字段": field.name,
                "值": field.value,
                "置信度": field.confidence,
                "来源文档": field.document_id,
                "页码": field.page_no,
                "原文证据": field.source_text,
            }
            for field in summary.key_fields
        ],
        width="stretch",
        hide_index=True,
    )

    with st.expander("人工字段修正"):
        field_names = [field.name for field in summary.key_fields]
        if not field_names:
            st.info("暂无可修正字段。")
            return
        field_name = st.selectbox("字段", field_names)
        value = st.text_input("修正值")
        reason = st.text_area("修正原因", placeholder="例如：人工核对原始发票图片，金额应为 580.00")
        if st.button("保存修正并标记为待重审"):
            if not value or not reason:
                st.warning("请填写修正值和修正原因。")
            else:
                task_store.save_field_correction(task.task_id, field_name, value, reason)
                st.success("已保存修正。请重新运行审核。")
                st.rerun()


def render_events(task: AuditTask) -> None:
    events = task_store.list_events(task.task_id)
    if not events:
        st.info("暂无事件。")
        return
    for event in events:
        st.markdown(f"**{event.created_at.strftime('%Y-%m-%d %H:%M:%S')} · {event.event_type}**")
        st.caption(event.message)


def render_report_tabs(task: AuditTask) -> None:
    if task.report is None:
        st.info("运行审核后，这里会展示字段、风险、制度依据、执行轨迹和报告下载。")
        return

    summary = build_audit_summary(task.report)
    render_summary(summary)

    tab_risks, tab_fields, tab_checks, tab_policy, tab_review, tab_events, tab_json = st.tabs(
        ["风险看板", "字段证据", "检查项", "制度依据", "人工复核", "事件时间线", "报告导出"]
    )

    with tab_risks:
        render_risks(summary)

    with tab_fields:
        render_fields(summary, task)

    with tab_checks:
        st.dataframe(
            [
                {
                    "检查": check.name,
                    "状态": check.status,
                    "说明": check.detail,
                    "证据": "、".join(check.evidence_refs),
                }
                for check in summary.checks
            ],
            width="stretch",
            hide_index=True,
        )

    with tab_policy:
        if summary.policy_evidence:
            for item in summary.policy_evidence:
                with st.container(border=True):
                    st.markdown(f"**{item.chunk_id} · {item.section} · score={item.score:.2f}**")
                    st.write(item.content)
        else:
            st.write("未检索到适用制度。")

    with tab_review:
        render_review_workbench(task)

    with tab_events:
        render_events(task)

    with tab_json:
        html_report = build_html_report(task, events=task_store.list_events(task.task_id))
        report_stem = safe_file_name(task_label(task), fallback=task.task_id)
        st.download_button(
            "下载 HTML 正式报告",
            data=html_report,
            file_name=f"{report_stem}-docaudit-report.html",
            mime="text/html",
            type="primary",
        )
        payload = {
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status,
            "summary": summary.model_dump(),
            "events": [
                {
                    "event_id": event.event_id,
                    "task_id": event.task_id,
                    "event_type": event.event_type,
                    "message": event.message,
                    "created_at": event.created_at.isoformat(),
                }
                for event in task_store.list_events(task.task_id)
            ],
        }
        st.json(payload)
        st.download_button(
            "下载 JSON 审核包",
            data=json.dumps(payload, ensure_ascii=False, indent=2),
            file_name=f"{task.task_id}-docaudit-report.json",
            mime="application/json",
        )


# ---------------- 侧栏 ----------------
if "view" not in st.session_state:
    st.session_state["view"] = NAV_TASKS
if "nav_gen" not in st.session_state:
    st.session_state["nav_gen"] = 0

with st.sidebar:
    st.header("系统运行状态")
    st.metric("模型模式", provider_label())
    st.metric("OCR模式", os.getenv("OCR_ENGINE", "mock"))
    st.metric("RAG模式", os.getenv("RAG_MODE", "mock"))
    st.metric("任务存储", os.getenv("AUDIT_TASK_STORE", "sqlite"))
    if is_synthetic_engine():
        st.caption("⚠️ OCR模式=mock 表示不识别图片/扫描件，上传这类材料会得到“无法判定”。")

    st.divider()
    st.header("快速演示")
    demo_label = st.selectbox("选择虚构样例", list(DEMO_CASES))
    if st.button("创建样例任务", type="primary"):
        task = create_task_with_files(
            load_demo_files(DEMO_CASES[demo_label]),
            applicant="张三",
            department="市场部",
            expense_type="HOTEL",
        )
        st.success(f"已创建报销单：{task_label(task)}")
        request_open(task.task_id)

    st.divider()
    if st.button("运行评测基线"):
        eval_report = run_evaluation()
        st.success(
            f"评测通过 {eval_report.metrics.passed_cases}/{eval_report.metrics.total_cases}，"
            f"通过率 {eval_report.metrics.pass_rate:.0%}"
        )


# ---------------- 主区域 ----------------
st.title("DocAudit Agent 审核工作台")
st.caption("面向企业报销材料的可解释审核系统：任务持久化、字段证据、制度依据、人工复核和审计事件。")

tasks = task_store.list_tasks()
# 演示模式横幅:不识别真实材料的配置必须在最显眼处说清楚(否则客户会以为系统"读错了")
demo_notice = demo_mode_notice()
if demo_notice:
    st.warning(f"⚠️ {demo_notice}", icon="⚠️")

overview_left, overview_mid, overview_right, overview_fourth = st.columns(4)
overview_left.metric("任务总数", len(tasks))
overview_mid.metric("待审核", sum(1 for item in tasks if item.status == "READY"))
overview_right.metric("需复核", sum(1 for item in tasks if item.report and item.report.status == "REVIEW_REQUIRED"))
overview_fourth.metric("已通过", sum(1 for item in tasks if item.report and item.report.status == "PASS"))
if any(item.report and item.report.status == "UNDETERMINED" for item in tasks):
    st.caption(
        "无法判定 "
        f"{sum(1 for item in tasks if item.report and item.report.status == 'UNDETERMINED')} "
        "单：材料没被真正识别出来，系统不会给结论（点开可见原因与改法）。"
    )

# 状态驱动的视图切换(替代 st.tabs:"打开任务"可以直接跳到审核详情)
view = st.radio(
    "视图导航",
    NAV_OPTIONS,
    index=NAV_OPTIONS.index(st.session_state["view"]),
    horizontal=True,
    label_visibility="collapsed",
    key=f"nav-{st.session_state['nav_gen']}",
)
if view != st.session_state["view"]:
    # 用户手动切换导航:同步状态(不 rerun,本次 run 直接用新视图渲染)
    st.session_state["view"] = view

if view == NAV_TASKS:
    st.subheader("任务中心")
    if tasks:
        st.caption("提示：单击表格任意一行即可直接打开该任务的审核详情。")
        table = render_task_table(tasks)
        selected_rows = getattr(getattr(table, "selection", None), "rows", []) or []
        if selected_rows:
            picked = tasks[selected_rows[0]]
            if picked.task_id != st.session_state.get("selected_task_id"):
                request_open(picked.task_id)

        task_options = {
            f"{task_label(task)} · {status_label(task.status)} · {result_label(task)} · {review_progress(task)}": task.task_id
            for task in tasks
        }
        current = st.selectbox("或从列表选择任务", list(task_options))
        if st.button("打开任务", type="primary"):
            request_open(task_options[current])
    else:
        st.info("暂无任务。可在左侧创建样例任务，或在“新建审核”上传材料。")

elif view == NAV_CREATE:
    st.subheader("新建审核任务")
    st.write(
        "先填报销信息，名称会按 **日期区间 + 人物 + 事件** 自动生成"
        "（例如 `2026.9.18-9.19 张三 住宿报销`）；日期在运行审核后按材料自动补全。"
    )
    info_left, info_mid, info_right = st.columns(3)
    expense_label = info_left.selectbox("报销类型", list(EXPENSE_TYPES))
    applicant = info_mid.text_input("申请人", placeholder="张三")
    department = info_right.text_input("部门", placeholder="市场部")
    event_note = st.text_input(
        "备注 / 事由（作为名称里的“事件”，可留空按报销类型生成）",
        placeholder="例如：住宿报销 / 9月上海出差",
    )
    preview = build_report_title(
        applicant=applicant,
        expense_type=EXPENSE_TYPES[expense_label],
        event=event_note,
    )
    st.caption(f"报销单名称预览：{preview or '（运行审核后按材料自动生成）'}")

    st.write("手动上传真实或脱敏材料时，文件名建议包含 `invoice`、`payment`、`approval`，便于当前规则识别材料类型。")
    uploads = st.file_uploader(
        "上传材料",
        type=["pdf", "txt", "docx", "xlsx", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )
    if uploads and is_synthetic_engine():
        scan_names = [f.name for f in uploads if not f.name.lower().endswith((".txt", ".docx", ".xlsx"))]
        if scan_names:
            st.error(
                "当前 OCR 引擎是演示模式（OCR_ENGINE=" + os.getenv("OCR_ENGINE", "mock") + "），"
                f"不会真正识别 {'、'.join(scan_names)} 这类图片/扫描件；提交后本次会被标记为“无法判定”。"
                "要真读材料，请在 .env 里配置 OCR_ENGINE=tesseract|http|mineru 后重启服务。"
            )
    if st.button("创建上传任务", type="primary"):
        if not uploads:
            st.warning("请先上传材料。")
        else:
            task = create_task_with_files(
                [(file.name, file.getvalue()) for file in uploads],
                applicant=applicant,
                department=department,
                expense_type=EXPENSE_TYPES[expense_label],
                note=event_note,
            )
            st.success(f"已创建报销单：{task_label(task)}（已切换到审核详情）")
            request_open(task.task_id)

else:
    task = selected_task()
    if task is None:
        st.info("请先在“任务中心”选择任务，或创建一个新任务。")
    else:
        top_left, top_right = st.columns([3, 1])
        top_left.subheader(task_label(task))
        top_left.caption(f"技术编号：{task.task_id}")
        if top_right.button("刷新"):
            st.rerun()
        if top_left.button("返回任务中心"):
            switch_view(NAV_TASKS)
        show_status_badge(task)

        with st.expander("报销单名称"):
            st.caption("按“日期区间 + 人物 + 事件”命名；手动改过的名称不会被审核结果覆盖。")
            renamed = st.text_input("名称", value=task.title, key=f"title-{task.task_id}")
            if st.button("保存名称", key=f"rename-{task.task_id}"):
                if not renamed.strip():
                    st.warning("名称不能为空。")
                else:
                    task_store.set_title(task.task_id, renamed)
                    st.success("名称已更新。")
                    st.rerun()

        meta_left, meta_mid, meta_right, meta_fourth = st.columns(4)
        meta_left.metric("任务状态", status_label(task.status))
        meta_mid.metric("文件数", len(task.files))
        meta_right.metric("复核进度", review_progress(task))
        meta_fourth.metric("更新时间", task.updated_at.strftime("%H:%M:%S"))

        with st.expander("材料清单", expanded=True):
            st.dataframe(
                [{"文件名": item.file_name, "大小KB": round(len(item.content) / 1024, 2)} for item in task.files],
                width="stretch",
                hide_index=True,
            )

        run_disabled = not task.files
        if st.button("运行 / 重新运行审核", type="primary", disabled=run_disabled):
            try:
                task = run_selected_task(task)
                st.success("审核完成。")
                st.rerun()
            except (ValueError, RuntimeError) as exc:
                task_store.save_error(task.task_id, str(exc))
                st.error(str(exc))

        render_report_tabs(task)
