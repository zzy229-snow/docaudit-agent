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


st.set_page_config(page_title="DocAudit Agent 审核工作台", layout="wide")


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
    return "需复核"


def select_task(task_id: str) -> None:
    st.session_state["selected_task_id"] = task_id


def selected_task() -> AuditTask | None:
    task_id = st.session_state.get("selected_task_id")
    if not task_id:
        return None
    return task_store.get_task(task_id)


def create_task_with_files(files: list[tuple[str, bytes]]) -> AuditTask:
    task = task_store.create_task()
    for file_name, content in files:
        task = task_store.add_file(task.task_id, file_name, content)
    select_task(task.task_id)
    return task


def run_selected_task(task: AuditTask) -> AuditTask:
    report = run_audit(
        [(item.file_name, item.content) for item in task.files],
        field_overrides=task_store.field_overrides(task.task_id),
    )
    return task_store.save_report(task.task_id, report)


def show_status_badge(task: AuditTask) -> None:
    if task.status == "FAILED":
        st.error(f"任务失败：{task.error}")
    elif task.report and task.report.status == "PASS":
        st.success("审核通过")
    elif task.report and task.report.status == "REVIEW_REQUIRED":
        st.warning("需要人工复核")
    elif task.status == "READY":
        st.info("材料已就绪，等待运行审核")
    else:
        st.info("任务已创建，等待上传材料")


def render_task_table(tasks: list[AuditTask]) -> None:
    st.dataframe(
        [
            {
                "任务ID": task.task_id,
                "任务状态": status_label(task.status),
                "审核结论": result_label(task),
                "文件数": len(task.files),
                "风险数": len(task.report.risks) if task.report else 0,
                "复核项": len(task.review_items),
                "更新时间": task.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for task in tasks
        ],
        use_container_width=True,
        hide_index=True,
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
    if not task.review_items:
        st.success("暂无待复核项。")
        return

    for item in task.review_items:
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
        use_container_width=True,
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
            use_container_width=True,
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
        payload = {
            "task_id": task.task_id,
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


with st.sidebar:
    st.header("系统运行状态")
    st.metric("模型模式", provider_label())
    st.metric("OCR模式", os.getenv("OCR_ENGINE", "mock"))
    st.metric("RAG模式", os.getenv("RAG_MODE", "mock"))
    st.metric("任务存储", os.getenv("AUDIT_TASK_STORE", "sqlite"))

    st.divider()
    st.header("快速演示")
    demo_label = st.selectbox("选择虚构样例", list(DEMO_CASES))
    if st.button("创建样例任务", type="primary"):
        task = create_task_with_files(load_demo_files(DEMO_CASES[demo_label]))
        st.success(f"已创建任务：{task.task_id}")
        st.rerun()

    st.divider()
    if st.button("运行评测基线"):
        eval_report = run_evaluation()
        st.success(
            f"评测通过 {eval_report.metrics.passed_cases}/{eval_report.metrics.total_cases}，"
            f"通过率 {eval_report.metrics.pass_rate:.0%}"
        )


st.title("DocAudit Agent 审核工作台")
st.caption("面向企业报销材料的可解释审核系统：任务持久化、字段证据、制度依据、人工复核和审计事件。")

tasks = task_store.list_tasks()
overview_left, overview_mid, overview_right, overview_fourth = st.columns(4)
overview_left.metric("任务总数", len(tasks))
overview_mid.metric("待审核", sum(1 for item in tasks if item.status == "READY"))
overview_right.metric("需复核", sum(1 for item in tasks if item.report and item.report.status == "REVIEW_REQUIRED"))
overview_fourth.metric("已通过", sum(1 for item in tasks if item.report and item.report.status == "PASS"))

tab_tasks, tab_create, tab_detail = st.tabs(["任务中心", "新建审核", "审核详情"])

with tab_tasks:
    st.subheader("任务中心")
    if tasks:
        render_task_table(tasks)
        task_options = {f"{task.task_id} · {status_label(task.status)} · {result_label(task)}": task.task_id for task in tasks}
        current = st.selectbox("选择要查看的任务", list(task_options))
        if st.button("打开任务"):
            select_task(task_options[current])
            st.rerun()
    else:
        st.info("暂无任务。可在左侧创建样例任务，或在“新建审核”上传材料。")

with tab_create:
    st.subheader("新建审核任务")
    st.write("手动上传真实或脱敏材料时，文件名建议包含 `invoice`、`payment`、`approval`，便于当前规则识别材料类型。")
    uploads = st.file_uploader(
        "上传材料",
        type=["pdf", "txt", "docx", "xlsx", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )
    if st.button("创建上传任务", type="primary"):
        if not uploads:
            st.warning("请先上传材料。")
        else:
            task = create_task_with_files([(file.name, file.getvalue()) for file in uploads])
            st.success(f"已创建任务：{task.task_id}")
            st.rerun()

with tab_detail:
    task = selected_task()
    if task is None:
        st.info("请先在“任务中心”选择任务，或创建一个新任务。")
    else:
        top_left, top_right = st.columns([3, 1])
        top_left.subheader(f"审核详情 · {task.task_id}")
        if top_right.button("刷新"):
            st.rerun()
        show_status_badge(task)

        meta_left, meta_mid, meta_right, meta_fourth = st.columns(4)
        meta_left.metric("任务状态", status_label(task.status))
        meta_mid.metric("文件数", len(task.files))
        meta_right.metric("复核项", len(task.review_items))
        meta_fourth.metric("更新时间", task.updated_at.strftime("%H:%M:%S"))

        with st.expander("材料清单", expanded=True):
            st.dataframe(
                [{"文件名": item.file_name, "大小KB": round(len(item.content) / 1024, 2)} for item in task.files],
                use_container_width=True,
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
