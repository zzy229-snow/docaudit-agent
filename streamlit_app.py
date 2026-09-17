import streamlit as st
from app.agent.graph import run_audit


st.set_page_config(page_title="DocAudit Agent", layout="wide")
st.title("企业报销材料审核 · 开发演示版")
st.caption("当前支持电子PDF、TXT、DOCX、XLSX；图片及扫描PDF的OCR尚未接入。示例按一晚住宿计算。")
uploads = st.file_uploader("上传酒店发票、付款凭证和审批单", type=["pdf", "txt", "docx", "xlsx", "png", "jpg"], accept_multiple_files=True)
if st.button("开始审核", type="primary"):
    if not uploads:
        st.warning("请先上传材料。也可以使用 data/demo 下的三个TXT样例。")
    else:
        try:
            result = run_audit([(f.name, f.getvalue()) for f in uploads])
            st.subheader("审核结果")
            st.metric("状态", "需要人工复核" if result.status == "REVIEW_REQUIRED" else "通过")
            st.subheader("字段及来源")
            st.dataframe([{"字段": k, "值": v.value, "文件ID": v.document_id, "页码": v.page_no, "原文": v.source_text} for k, v in result.fields.items()], use_container_width=True)
            st.subheader("检查项")
            st.dataframe([{"名称": c.name, "通过": c.passed, "说明": c.detail} for c in result.checks], use_container_width=True)
            st.subheader("风险")
            if result.risks:
                for risk in result.risks: st.warning(f"{risk.risk_type}: {risk.reason}")
            else: st.success("当前规则下未发现风险")
            with st.expander("制度依据与执行轨迹"):
                for item in result.policy_evidence: st.write(f"{item.chunk_id}：{item.content}")
                st.write(result.trace)
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
