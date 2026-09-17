from app.models.audit import AuditReport
from .nodes import parse_documents, extract, retrieve, check, report


def run_audit(files: list[tuple[str, bytes]]) -> AuditReport:
    """Small fixed workflow with a state interface ready for LangGraph integration."""
    if not files:
        raise ValueError("请上传至少一份材料")
    state: dict = {"files": files, "trace": []}
    for node in (parse_documents, extract, retrieve, check, report):
        state.update(node(state))
    return state["report"]
