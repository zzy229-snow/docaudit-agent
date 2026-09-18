import json
import os
from pathlib import Path

import streamlit as st

from app.agent.graph import run_audit
from app.config import load_environment
from app.evaluation.runner import run_evaluation
from app.services.audit_summary import build_audit_summary


load_environment()

DEMO_ROOT = Path(__file__).resolve().parent / "data" / "demo"
DEMO_CASES = {
    "不使用样例，手动上传": None,
    "正常报销：三类材料齐全": "normal",
    "住宿超标 + 主体不一致": "over_limit",
    "缺少付款凭证": "missing_payment",
    "主体不一致但金额正常": "subject_mismatch",
    "发票日期超出行程": "date_out_of_range",
}


st.set_page_config(page_title="DocAudit Agent", layout="wide")
st.title("企业多模态报销审核 Agent")
st.caption("上传发票、付款凭证和审批单后，系统会抽取字段、检索制度、运行确定性工具并生成可解释结论。")


def load_demo_files(case: str) -> list[tuple[str, bytes]]:
    case_dir = DEMO_ROOT / case
    return [(path.name, path.read_bytes()) for path in sorted(case_dir.iterdir()) if path.is_file()]


def provider_label() -> str:
    provider = os.getenv("MODEL_PROVIDER", "mock").strip().lower()
    if provider == "mock":
        return "Mock / 离线安全模式"
    return f"{provider} · {os.getenv('MODEL_NAME', '未设置模型名')}"


with st.sidebar:
    st.header("运行状态")
    st.caption("这些信息用于讲解时解释当前是否调用真实模型。")
    st.metric("模型模式", provider_label())
    st.metric("OCR模式", os.getenv("OCR_ENGINE", "mock"))
    st.metric("RAG模式", os.getenv("RAG_MODE", "mock"))

    st.divider()
    st.header("演示样例")
    demo_label = st.selectbox("选择样例", list(DEMO_CASES))
    if DEMO_CASES[demo_label]:
        st.info("点击“开始审核”会直接使用仓库内虚构样例，不需要上传文件。")
    else:
        st.info("手动上传时，文件名建议包含 invoice、payment、approval。")

    st.divider()
    if st.button("运行评测基线"):
        eval_report = run_evaluation()
        st.success(
            f"评测通过 {eval_report.metrics.passed_cases}/{eval_report.metrics.total_cases}，"
            f"通过率 {eval_report.metrics.pass_rate:.0%}"
        )


uploads = st.file_uploader(
    "上传材料",
    type=["pdf", "txt", "docx", "xlsx", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

run_clicked = st.button("开始审核", type="primary")

if run_clicked:
    selected_case = DEMO_CASES[demo_label]
    if selected_case:
        files = load_demo_files(selected_case)
        st.caption(f"当前使用样例：data/demo/{selected_case}")
    elif uploads:
        files = [(file.name, file.getvalue()) for file in uploads]
    else:
        files = []

    if not files:
        st.warning("请先上传材料，或在左侧选择一个演示样例。")
    else:
        try:
            report = run_audit(files)
            summary = build_audit_summary(report)

            label = "通过" if summary.status == "PASS" else "需要复核"
            status_color = "normal" if summary.status == "PASS" else "inverse"

            st.subheader("审核结论")
            left, middle, right, fourth = st.columns(4)
            left.metric("状态", label)
            middle.metric("风险数", len(summary.risks), delta_color=status_color)
            right.metric("制度依据", len(summary.policy_evidence))
            fourth.metric("检查项", len(summary.checks))

            if summary.status == "PASS":
                st.success(summary.conclusion)
            else:
                st.warning(summary.conclusion)
            st.write(summary.next_action)

            if summary.risks:
                st.subheader("风险卡片")
                for risk in summary.risks:
                    with st.container(border=True):
                        cols = st.columns([2, 1, 3])
                        cols[0].markdown(f"### {risk.risk_type}")
                        cols[1].markdown(f"**级别：{risk.level}**")
                        cols[2].write(risk.reason)
                        st.caption("建议处理：" + risk.next_action)
                        if risk.evidence_refs:
                            st.caption("字段证据：" + "、".join(risk.evidence_refs))
                        if risk.policy_refs:
                            st.caption("制度依据：" + "、".join(risk.policy_refs))
            else:
                st.success("当前已实现规则下未发现风险。")

            tab_fields, tab_checks, tab_policy, tab_trace, tab_json = st.tabs(
                ["关键字段", "检查项", "制度依据", "执行轨迹", "JSON报告"]
            )

            with tab_fields:
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

            with tab_trace:
                for step in summary.trace:
                    st.code(step)

            with tab_json:
                payload = summary.model_dump()
                st.json(payload)
                st.download_button(
                    "下载 JSON 报告",
                    data=json.dumps(payload, ensure_ascii=False, indent=2),
                    file_name="docaudit-report.json",
                    mime="application/json",
                )

        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
