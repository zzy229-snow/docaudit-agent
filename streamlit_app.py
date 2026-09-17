import streamlit as st

from app.agent.graph import run_audit
from app.services.audit_summary import build_audit_summary


st.set_page_config(page_title="DocAudit Agent", layout="wide")
st.title("企业报销材料审核")
st.caption("上传发票、付款凭证和审批单后，系统会提取字段、检索制度、运行确定性工具并生成可解释结论。")

uploads = st.file_uploader(
    "上传材料",
    type=["pdf", "txt", "docx", "xlsx", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

if st.button("开始审核", type="primary"):
    if not uploads:
        st.warning("请先上传材料。也可以使用 data/demo 下的 normal 或 over_limit 样例。")
    else:
        try:
            report = run_audit([(file.name, file.getvalue()) for file in uploads])
            summary = build_audit_summary(report)

            label = "通过" if summary.status == "PASS" else "需要复核"
            st.subheader("审核结论")
            left, middle, right = st.columns(3)
            left.metric("状态", label)
            middle.metric("风险数", len(summary.risks))
            right.metric("制度依据", len(summary.policy_evidence))
            st.info(summary.conclusion)
            st.write(summary.next_action)

            if summary.risks:
                st.subheader("风险解释")
                for risk in summary.risks:
                    with st.container(border=True):
                        st.markdown(f"**{risk.risk_type} · {risk.level}**")
                        st.write(risk.reason)
                        st.caption("下一步：" + risk.next_action)
                        if risk.evidence_refs:
                            st.caption("字段证据：" + "、".join(risk.evidence_refs))
                        if risk.policy_refs:
                            st.caption("制度依据：" + "、".join(risk.policy_refs))
            else:
                st.success("当前已实现规则下未发现风险。")

            st.subheader("关键字段")
            st.dataframe(
                [
                    {
                        "字段": field.name,
                        "值": field.value,
                        "置信度": field.confidence,
                        "页码": field.page_no,
                        "原文": field.source_text,
                    }
                    for field in summary.key_fields
                ],
                use_container_width=True,
            )

            st.subheader("检查项")
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
            )

            with st.expander("制度依据"):
                if summary.policy_evidence:
                    for item in summary.policy_evidence:
                        st.write(f"{item.chunk_id} / 第{item.section}节：{item.content}")
                else:
                    st.write("未检索到适用制度。")

            with st.expander("Agent执行轨迹"):
                for step in summary.trace:
                    st.write(step)
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
